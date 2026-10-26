"""A question across the Subscriber IQ library is answered from the
library (2026-10-26).

Bria asked "Of the existing Subscriber IQs available on the dashboard,
which TV series have the highest percent acquired or reactivated
accounts?" and was asked to name a show, then quoted $500 on "Name of
show". The question names the whole library on purpose. Every Subscriber
IQ read carries its KEY METRICS block at the top of the file (show,
platform, show watchers, new platform signups, clean conversion rate),
so the ranking is a read of those blocks, no model call, no charge.

is_library_wide_ask(text)   the words name the library, not one title
library(s3, bucket, can_open=None)   [{show, platform, key, watchers, signups, rate, window}]
answer(text, s3, bucket, user=None, can_open=None) -> analyze payload
"""
from __future__ import annotations

import csv
import io
import re
import time
import traceback

_LIBRARY_RX = re.compile(
    r"\b(?:existing|available|all|every|across|among|of)\s+(?:the\s+|our\s+|your\s+)?(?:subscriber\s*iqs?|subscriber\s+iq\s+(?:reads?|files?|trackers?|reports?))\b"
    r"|\bsubscriber\s*iqs?\s+(?:available|on\s+the\s+dashboard|in\s+the\s+library|you\s+have|we\s+have)\b"
    r"|\b(?:which|what)\s+(?:tv\s+|streaming\s+)?(?:series|shows?|titles?)\b[^?]{0,80}\bsubscriber\s*iq\b"
    r"|\bsubscriber\s*iq\b[^?]{0,80}\b(?:which|what)\s+(?:tv\s+|streaming\s+)?(?:series|shows?|titles?)\s+(?:have|has|had|show|rank)", re.I)
_SKIP_PREFIXES = ('historic/', 'purgatory/', '_backups/', 'system/')
_CACHE = {'at': 0.0, 'bucket': '', 'rows': None}
_TTL_S = 3600


def is_library_wide_ask(text):
    t = ' '.join(str(text or '').split())
    return bool(t) and bool(_LIBRARY_RX.search(t))


def _num(s):
    s = str(s or '').replace(',', '').strip()
    try:
        return float(s)
    except Exception:
        return None


def _parse_head(body):
    out = {'show': '', 'platform': '', 'window': '', 'watchers': None, 'signups': None, 'rate': None}
    try:
        for row in csv.reader(io.StringIO(body)):
            cells = [str(c).strip() for c in row]
            if not cells:
                continue
            label = cells[0].lower()
            vals = [c for c in cells[1:] if c]
            if label == 'show/content tracked' and vals:
                out['show'] = vals[0]
            elif label == 'platform tracked' and vals:
                out['platform'] = vals[0]
            elif label == 'analysis date range' and vals:
                out['window'] = vals[0]
            elif label == 'total show watchers' and vals:
                out['watchers'] = _num(vals[0])
            elif label == 'new platform signups' and vals:
                out['signups'] = _num(vals[0])
            elif label == 'clean conversion rate' and vals:
                pct = next((v for v in vals if v.endswith('%')), '')
                out['rate'] = _num(pct.rstrip('%'))
            if out['rate'] is not None and out['show']:
                break
    except Exception:
        pass
    return out


_PLATFORMS = {'netflix': 'Netflix', 'hulu': 'Hulu', 'peacock': 'Peacock', 'max': 'Max', 'hbo max': 'Max', 'paramount+': 'Paramount+',
              'paramount plus': 'Paramount+', 'apple tv+': 'Apple TV+', 'apple tv': 'Apple TV+', 'amazon prime video': 'Prime Video',
              'prime video': 'Prime Video', 'amazon': 'Prime Video', 'disney+': 'Disney+', 'disney plus': 'Disney+', 'starz': 'Starz',
              'mgm+': 'MGM+', 'tubi': 'Tubi', 'youtube': 'YouTube', 'britbox': 'BritBox', 'amc+': 'AMC+'}


def _platform_name(p):
    p = ' '.join(str(p or '').split())
    return _PLATFORMS.get(p.lower(), p)


def _clean_show(name):
    """One title for the reader: a cell that repeats the title after a comma
    keeps the first copy; a cell that is a slug list ("Task show, task tv,
    task hbo") or all lowercase is not a name, and the file name stands in."""
    s = ' '.join(str(name or '').split())
    if ', ' in s:
        parts = s.split(', ')
        a, b = parts[0], parts[1]
        if len(parts) == 2 and re.sub(r"[^a-z0-9]+", '', a.lower()) == re.sub(r"[^a-z0-9]+", '', b.lower()):
            return a
        return ''
    if s and s == s.lower():
        return ''
    return s


def _show_from_key(key):
    name = key.rsplit('/', 1)[-1].replace('.csv', '')
    m = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name)
    out = (m.group(1) if m else name).replace('_', ' ').strip()
    return out.title() if out == out.lower() else out


def library(s3, bucket, can_open=None, now=None):
    """Every Subscriber IQ read the caller can open, with its headline
    figures. Cached an hour per process."""
    now = now or time.time()
    if _CACHE['rows'] is not None and _CACHE['bucket'] == bucket and now - _CACHE['at'] < _TTL_S:
        rows = _CACHE['rows']
    else:
        rows = []
        try:
            pag = s3.get_paginator('list_objects_v2')
            keys = []
            for page in pag.paginate(Bucket=bucket):
                for o in page.get('Contents') or []:
                    k = str(o.get('Key') or '')
                    if not k.endswith('.csv') or k.startswith(_SKIP_PREFIXES):
                        continue
                    keys.append((k, o.get('LastModified')))
            from concurrent.futures import ThreadPoolExecutor

            def _one(item):
                k, lm = item
                try:
                    body = s3.get_object(Bucket=bucket, Key=k, Range='bytes=0-6000')['Body'].read().decode('utf-8', 'ignore')
                except Exception:
                    return None
                h = _parse_head(body)
                h['key'] = k
                h['show'] = _clean_show(h['show']) or _show_from_key(k)
                h['platform'] = _platform_name(h.get('platform'))
                h['modified'] = str(lm or '')
                # the rate is signups over watchers; the file's own cell is the
                # fallback (some older layouts carry another base in that cell)
                if h.get('signups') is not None and h.get('watchers'):
                    h['rate'] = round(100.0 * h['signups'] / h['watchers'], 1)
                return h
            with ThreadPoolExecutor(max_workers=8) as ex:
                rows = [h for h in ex.map(_one, keys) if h]
        except Exception:
            traceback.print_exc()
        _CACHE.update({'at': now, 'bucket': bucket, 'rows': rows})
    if can_open is not None:
        try:
            rows = [r for r in rows if can_open(r['key'], r['show'])]
        except Exception:
            pass
    # one line per show: the newest file wins
    seen = {}
    for r in sorted(rows, key=lambda r: str(r.get('modified') or ''), reverse=True):
        k = re.sub(r"[^a-z0-9]+", '', str(r.get('show') or '').lower())
        if k and k not in seen:
            seen[k] = r
    return list(seen.values())


def _pct(v):
    return f"{v:.1f}%" if v is not None else ''


def _n(v):
    return f"{int(round(v)):,}" if v is not None else ''


def answer(text, s3, bucket, user=None, can_open=None, limit=10):
    rows = library(s3, bucket, can_open=can_open)
    if not rows:
        return {'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                'reply': 'There are no Subscriber IQ reads on the dashboard for your seat yet. Name a show and a service and I will set one up.'}
    ranked = sorted([r for r in rows if r.get('rate') is not None], key=lambda r: r['rate'], reverse=True)
    lines = [f"Across the {len(rows)} Subscriber IQ reads on the dashboard, these titles turned the largest share of their "
             f"show watchers into new accounts on the service:"]
    for i, r in enumerate(ranked[:limit], 1):
        plat = f" on {r['platform']}" if r.get('platform') else ''
        tail = f" ({_n(r['signups'])} new accounts from {_n(r['watchers'])} watchers)" if r.get('signups') is not None and r.get('watchers') else ''
        lines.append(f"{i}. {r['show']}{plat}: {_pct(r['rate'])}{tail}")
    if len(ranked) < len(rows):
        lines.append(f"{len(rows) - len(ranked)} read(s) carry no headline rate and are not ranked here.")
    lines.append("Reactivated accounts sit inside each read month by month rather than as one headline figure; open a read and ask "
                 "me for its reactivated share, or name two titles and I will set them side by side.")
    chips = [f"Open {r['show']}" for r in ranked[:3]]
    return {'success': True, 'action': 'answer', 'reply': '\n'.join(lines), 'followups': chips, 'offer_deck': False, 'deck_angle': None,
            'subiq_library': [{'show': r['show'], 'platform': r.get('platform'), 'rate': r.get('rate')} for r in ranked[:limit]]}


__all__ = ['is_library_wide_ask', 'library', 'answer']
