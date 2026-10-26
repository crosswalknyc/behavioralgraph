"""A figure held by an Attribution IQ campaign is broken down from the
campaign's own assets, under the campaign's own window (Jenna
2026-10-26, Alexia, The Influencer Project).

2026-10-23 (Alexia, on the Attribution IQ campaign The Influencer
Project - Hades): "Isolate the data to just the assets that drove the
67,729 uniwue visitor to the Influencer Project ticketing page". The
campaign holds 67,729 as its ticketing stage and 90 tracked assets and
creator groups with the converters each one carried. Nothing bound the
figure to the campaign: the drill-down lane only knew Digital Journey
payloads, so the ask went to the page analysis, which handed off to a
generated read, which took the same-title Profile IQ file "Potential
The Influencer Project Ticket Buyer" as its base and printed seven
invented "asset groups" under that file's window (Jan 1 2025 to Dec 31
2025). Jenna: "think Prom is getting confused. it's using a Jan-Dec
2025 time period."

The rule, the same one the corpus-anchors step applies to journeys (a
read never starts cold on a subject we already hold):

  1. A question that states a figure an Attribution IQ campaign holds
     (a journey step, the exposed base, a ticketing surface, a first or
     last touch row, a timing bucket, a fork) binds to that campaign:
     the campaign on screen first, then the thread's campaign, then a
     campaign the words name, then any campaign in the library that
     holds the figure.
  2. A breakdown or isolation of such a figure is answered from the
     campaign's own data, deterministically and with no model call:
     the ticketing stage splits across the tracked assets by the
     converters each one carried, one person on one asset (the asset
     they last carried before the visit), rows adding to the stage
     exactly; the exposed base lists the people each asset reached
     (people on several assets count on each); any other held figure
     answers from the table it sits in.
  3. The window shown is always the campaign's: its first phase start
     (else its first asset) through the date of its latest read.
  4. When the campaign holds no asset-level data under the figure, the
     answer says so in plain words and names the splits the campaign
     does hold. A generated read never prints asset groups.

Everything here is fail-safe: any error returns None and the ask
continues down its normal path.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import traceback
import uuid
from datetime import datetime

from .host import host
from . import stated_numbers as _sn

INTENT_PREFIX = 'intent/'
DATA_FILE_PREFIX = 'generated_data/'
US_GEN_POP = 329_900_000
_SLUGS_TTL_S = 600
_MAX_LIBRARY_SCAN = 24
_TOP_ROWS_IN_CHAT = 15
_cache = {'slugs': (0.0, []), 'camps': {}}

# The words that make a figure question an asset / channel / surface
# breakdown of a campaign figure. A count plus one of these is the lane.
_CUE_RX = re.compile(
    r"\b(?:isolat(?:e|ed|ing)|assets?|creatives?|touch ?points?|placements?|clips?|posts?|creators?|"
    r"drove|drive|drives|driving|driven|behind|last[\s-]?touch(?:ed)?|first[\s-]?touch(?:ed)?|"
    r"surfaces?|channels?|platforms?|split|break ?down|breakdown|came from|come from|coming from|"
    r"by (?:asset|creator|channel|platform|surface|phase|format|tag))\b", re.I)
_GROUP_RX = {
    'channel': re.compile(r"\bby (?:channel|platform|surface)s?\b|\bper (?:channel|platform)\b|\b(?:channel|platform) (?:split|mix|breakdown)\b", re.I),
    'tag': re.compile(r"\bby (?:paid|organic|earned|official|tag)\b|\bpaid (?:vs\.?|versus|or|and) (?:organic|earned)\b|\borganic (?:vs\.?|versus|or|and) paid\b", re.I),
    'phase': re.compile(r"\bby phase\b|\bper phase\b|\bphase (?:split|mix|breakdown)\b", re.I),
}
_GENERIC_WORDS = frozenset((
    'the', 'and', 'for', 'with', 'from', 'this', 'that', 'project', 'film', 'movie', 'series', 'show',
    'campaign', 'hades', 'films', 'studio', 'pictures', 'entertainment', 'media', 'official', 'trailer',
    'season', 'episode', 'opening', 'week', 'weekend', 'launch', 'review', 'reviews', 'brand', 'ticketing',
))


# --------------------------------------------------------------- figures

def numbers_in(text):
    """The count figures the ask states (100 and up, written as counts)."""
    return _sn.counts(text, min_value=100, lane='drilldown')


def looks_like(text):
    """The ask states a count and asks what assets, channels, surfaces or
    touches sit behind it, or how it splits. A question that asks how
    many of an audience did something is a read, never this lane."""
    t = str(text or '')
    if not t or len(t) > 600 or not numbers_in(t):
        return False
    if _sn.is_measurement_ask(t):
        return False
    return bool(_CUE_RX.search(t)) or _sn.is_figure_question(t)


def group_by(text):
    """'channel' | 'tag' | 'phase' | '' : the roll-up the words ask for."""
    t = str(text or '')
    for k, rx in _GROUP_RX.items():
        if rx.search(t):
            return k
    return ''


# ------------------------------------------------------------- the data

def _s3():
    return host.s3_client, host.bucket


def _json(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    except Exception:
        return None


def _list_keys(s3, bucket, prefix):
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        keys = [str(o.get('Key') or '') for o in (resp.get('Contents') or [])]
        token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
        n = 0
        while token and n < 20:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, ContinuationToken=token)
            keys.extend(str(o.get('Key') or '') for o in (resp.get('Contents') or []))
            token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
            n += 1
        return keys
    except Exception:
        return []


def campaign_slugs(s3, bucket):
    """Every campaign slug under intent/ (the latest read's key names it).
    Cached for ten minutes."""
    ts, slugs = _cache['slugs']
    if slugs and time.time() - ts < _SLUGS_TTL_S:
        return list(slugs)
    found = []
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=INTENT_PREFIX, Delimiter='/')
        for p in (resp.get('CommonPrefixes') or []):
            slug = str(p.get('Prefix') or '')[len(INTENT_PREFIX):].strip('/')
            if slug:
                found.append(slug)
    except Exception:
        found = []
    if not found:
        for k in _list_keys(s3, bucket, INTENT_PREFIX):
            parts = k[len(INTENT_PREFIX):].split('/')
            if len(parts) >= 3 and parts[1] == 'mta' and parts[0] not in found:
                found.append(parts[0])
    _cache['slugs'] = (time.time(), list(found))
    return found


_COEF_RX = re.compile(r"/mta/coefficients_(\d{4}-\d{2}-\d{2})\.json$")


def latest_read_key(s3, bucket, slug):
    best, best_day = '', ''
    for k in _list_keys(s3, bucket, f"{INTENT_PREFIX}{slug}/mta/coefficients_"):
        if '/_backups/' in k:
            continue
        m = _COEF_RX.search(k)
        if m and m.group(1) > best_day:
            best, best_day = k, m.group(1)
    return best


def load_campaign(s3, bucket, slug):
    """The campaign's latest read plus its asset list, with the display
    name and the window. None when the slug holds no read."""
    slug = re.sub(r"[^a-z0-9_\-]", '', str(slug or '').lower())
    if not slug:
        return None
    key = latest_read_key(s3, bucket, slug)
    if not key:
        return None
    hit = _cache['camps'].get(slug)
    if hit and hit.get('key') == key:
        return hit
    doc = _json(s3, bucket, key)
    if not isinstance(doc, dict) or not isinstance(doc.get('overall'), dict):
        return None
    adoc = _json(s3, bucket, f"{INTENT_PREFIX}{slug}/source/normalized_assets.json") or {}
    title = (adoc.get('title') or {}) if isinstance(adoc, dict) else {}
    display = str(doc.get('display_name') or title.get('display_name') or '').strip()
    if not display or display == slug:
        display = str(title.get('display_name') or '').strip() or slug.replace('_', ' ').title()
    camp = {'slug': slug, 'key': key, 'doc': doc, 'assets_doc': adoc if isinstance(adoc, dict) else {},
            'display_name': display, 'family': display.split(' - ')[0].strip() or display,
            'as_of': str(doc.get('as_of') or '')[:10]}
    camp['window'] = window_of(camp)
    if len(_cache['camps']) > 40:
        _cache['camps'].clear()
    _cache['camps'][slug] = camp
    return camp


# -------------------------------------------------------------- window

def _fmt_day(iso):
    try:
        d = datetime.strptime(str(iso)[:10], '%Y-%m-%d')
    except Exception:
        return str(iso or '')
    return f"{d.strftime('%b')} {d.day}"


def window_label(start, end):
    s, e = str(start or '')[:10], str(end or '')[:10]
    if not s or not e:
        return ''
    if s[:4] == e[:4]:
        return f"{_fmt_day(s)} to {_fmt_day(e)}, {e[:4]}"
    return f"{_fmt_day(s)}, {s[:4]} to {_fmt_day(e)}, {e[:4]}"


def window_of(camp):
    """{'start','end','label'}: the campaign's first phase start (else
    its first posted asset, else the day tickets went on sale) through
    the date of its latest read."""
    adoc = camp.get('assets_doc') or {}
    starts = [str(p.get('start_date') or '')[:10] for p in (adoc.get('phases') or []) if isinstance(p, dict)]
    starts = [s for s in starts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)]
    start = min(starts) if starts else ''
    if not start:
        posted = [str(a.get('posted_date') or '')[:10] for a in (adoc.get('assets') or []) if isinstance(a, dict)]
        posted = [p for p in posted if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)]
        start = min(posted) if posted else ''
    if not start:
        start = str((adoc.get('title') or {}).get('ticketing_open_date') or '')[:10]
    end = camp.get('as_of') or ''
    if not end:
        m = _COEF_RX.search(str(camp.get('key') or ''))
        end = m.group(1) if m else ''
    return {'start': start, 'end': end, 'label': window_label(start, end)}


# ------------------------------------------------------- held figures

def _int(v):
    try:
        return int(round(float(v)))
    except Exception:
        return None


def _conversion_phrase(label):
    """'Went to a ticketing site or app for a ticket within 7d' ->
    'went to a ticketing site or app for a ticket within 7 days'."""
    t = re.sub(r"^\s*(?:top|mid|lower) funnel:\s*", '', str(label or '').strip(), flags=re.I)
    t = re.sub(r"\b(\d+)\s*d\b", r"\1 days", t)
    return (t[0].lower() + t[1:]) if t else ''


def held_figures(camp):
    """Every count the campaign states, with the table it sits in and
    its parent: journey steps, the exposed base, ticketing surfaces,
    research surfaces, first / last touch, assists, timing, forks, path
    shapes, leaks and per-platform reach."""
    doc = camp.get('doc') or {}
    ov = doc.get('overall') or {}
    pt = ov.get('paths') or {}
    out = []
    prev = None
    for n in (pt.get('nest') or []):
        if not isinstance(n, dict) or n.get('stage') == '0_tam':
            continue
        v = _int(n.get('us_accounts'))
        if v is None:
            continue
        out.append({'kind': 'stage', 'stage': str(n.get('stage') or ''), 'label': str(n.get('label') or '').strip(),
                    'value': v, 'parent': prev, 'table': 'journey steps'})
        prev = v
    stage = {f['stage']: f for f in out if f['kind'] == 'stage'}
    ex = doc.get('exposure') or ov.get('exposure') or {}
    v = _int(ex.get('us_people'))
    if v is not None:
        out.append({'kind': 'exposed', 'label': 'US people who saw at least one of the campaign\'s assets',
                    'value': v, 'parent': None, 'table': 'exposed base'})
        for p in (ex.get('by_platform') or []):
            if isinstance(p, dict) and _int(p.get('us_people')) is not None:
                out.append({'kind': 'platform_reach', 'label': str(p.get('platform') or ''), 'value': _int(p.get('us_people')),
                            'parent': v, 'table': 'people reached by platform (people on more than one platform count on each)'})
    tick = (stage.get('3_ticketer') or {}).get('value')
    paid = (stage.get('4_paid') or {}).get('value')
    info = (stage.get('2_infoseek') or {}).get('value')
    wh = pt.get('where') or {}
    for r in (wh.get('ticketer_partition') or []):
        if isinstance(r, dict) and _int(r.get('us_accounts')) is not None:
            out.append({'kind': 'partition', 'label': str(r.get('surface') or ''), 'value': _int(r.get('us_accounts')),
                        'parent': tick, 'table': 'ticketing surfaces, of the people who reached a showtimes page', 'rows': wh.get('ticketer_partition')})
    for r in (wh.get('infoseek_overlap') or []):
        if isinstance(r, dict) and _int(r.get('us_accounts')) is not None:
            out.append({'kind': 'overlap', 'label': str(r.get('surface') or ''), 'value': _int(r.get('us_accounts')),
                        'parent': info, 'table': 'research surfaces, of the people who looked the title up (people on several surfaces count on each)', 'rows': wh.get('infoseek_overlap')})
    at = pt.get('attribution') or {}
    for fld, name in (('first_touch', 'first touch'), ('last_touch', 'last touch'), ('assists', 'assists (one person can carry several)')):
        for r in (at.get(fld) or []):
            if isinstance(r, dict) and _int(r.get('us_accounts')) is not None:
                out.append({'kind': 'touch', 'label': str(r.get('touchpoint') or ''), 'value': _int(r.get('us_accounts')),
                            'parent': paid, 'table': name, 'rows': at.get(fld)})
    for r in (pt.get('time_to_conversion') or []):
        if isinstance(r, dict) and _int(r.get('us_accounts')) is not None:
            out.append({'kind': 'timing', 'label': str(r.get('bucket') or ''), 'value': _int(r.get('us_accounts')),
                        'parent': paid, 'table': 'time from the last asset to the ticketing visit', 'rows': pt.get('time_to_conversion')})
    for r in (pt.get('path_archetypes') or []):
        if isinstance(r, dict) and _int(r.get('us_accounts')) is not None:
            out.append({'kind': 'path', 'label': str(r.get('archetype') or ''), 'value': _int(r.get('us_accounts')),
                        'parent': paid, 'table': 'path shapes', 'rows': pt.get('path_archetypes')})
    for f in (pt.get('forks') or []):
        if not isinstance(f, dict):
            continue
        par = (stage.get(str(f.get('of_stage') or '')) or {}).get('value')
        for side in ('yes', 'no'):
            if _int(f.get(side)) is not None:
                out.append({'kind': 'fork', 'label': f"{f.get('question')}: {side}", 'value': _int(f.get(side)), 'parent': par,
                            'table': 'a split of a journey step'})
    for r in (pt.get('leaks') or []):
        if isinstance(r, dict) and _int(r.get('us_accounts')) is not None:
            par = (stage.get(str(r.get('of_base') or '')) or {}).get('value')
            out.append({'kind': 'leak', 'label': str(r.get('leak') or ''), 'value': _int(r.get('us_accounts')), 'parent': par,
                        'table': 'where people left', 'note': str(r.get('note') or '')})
    return out


def figure_match(camp, numbers):
    """The held figure one of the stated numbers names, journey steps and
    the exposed base first. None when the campaign holds none of them."""
    nums = [int(n) for n in (numbers or []) if n]
    if not nums:
        return None
    figs = held_figures(camp)
    for n in nums:
        for f in figs:
            if f['value'] == n:
                return dict(f)
    return None


# ------------------------------------------------------------ the assets

def _factor(camp):
    doc = camp.get('doc') or {}
    pt = (doc.get('overall') or {}).get('paths') or {}
    try:
        f = float(pt.get('us_projection_factor') or 0)
        if f > 0:
            return f
    except Exception:
        pass
    try:
        n = float(((doc.get('overall') or {}).get('sample_size')) or (doc.get('exposure') or {}).get('sample') or 0)
        if n > 0:
            return US_GEN_POP / n
    except Exception:
        pass
    return 0.0


def _asset_index(camp):
    out = {}
    for a in ((camp.get('assets_doc') or {}).get('assets') or []):
        if isinstance(a, dict) and a.get('asset_id'):
            out[str(a['asset_id'])] = a
    return out


def _clean_label(title, channel):
    """'TikTok · @roomforscream · 2 clips' -> '@roomforscream, 2 clips';
    the channel leads the campaign's own label and the row names it
    separately."""
    parts = [p.strip() for p in re.split(r"\s+\u00b7\s+|\s+\|\s+", str(title or '')) if p.strip()]
    ch = str(channel or '').strip().lower()
    if parts and ch and parts[0].lower() in (ch, f"{ch} reel", f"{ch} reels", f"{ch} video"):
        parts = parts[1:]
    label = ', '.join(parts) if parts else str(title or '').strip()
    label = re.sub(r"\s+", ' ', label).strip()
    return label[:140]


def _tag_word(tag):
    t = str(tag or '').strip().lower()
    return {'paid': 'paid', 'earned': 'earned', 'official': 'official', 'organic': 'organic'}.get(t, t or 'organic')


def touchpoint_rows(camp):
    """The tracked assets and creator groups the campaign's read carries,
    with the converters and the people reached each one holds."""
    doc = camp.get('doc') or {}
    tps = (doc.get('overall') or {}).get('touchpoints') or []
    idx = _asset_index(camp)
    rows = []
    for t in tps:
        if not isinstance(t, dict):
            continue
        tid = str(t.get('touchpoint_id') or '')
        a = idx.get(tid) or {}
        url = str(a.get('url') or '').strip()
        posted = str(a.get('posted_date') or '')[:10]
        if not url and tid.startswith('grp:'):
            # a creator group: the first clip's address and date
            handle = tid.split(':', 2)[-1]
            members = [asset for asset in idx.values()
                       if handle and handle not in ('other', '') and handle in str(asset.get('action_label') or '')
                       and str(asset.get('channel') or '').lower() == str(t.get('channel') or '').lower()]
            members.sort(key=lambda m: str(m.get('posted_date') or ''))
            if members:
                url = str(members[0].get('url') or '').strip()
                posted = str(members[0].get('posted_date') or '')[:10]
        rows.append({
            'id': tid, 'asset': _clean_label(t.get('asset_title'), t.get('channel')),
            'channel': str(t.get('channel') or '').strip(), 'tag': _tag_word(t.get('paid_or_organic')),
            'phase': str(t.get('phase') or '').strip(), 'type': str(t.get('asset_type') or '').strip(),
            'posted': posted, 'url': url,
            'converters': float(t.get('converted_n') or 0), 'exposed': float(t.get('exposed_n') or 0),
        })
    return rows


def _largest_remainder(total, weights):
    s = sum(max(0.0, float(w)) for w in weights.values())
    if total <= 0 or s <= 0:
        return {k: 0 for k in weights}
    raw = {k: total * max(0.0, float(w)) / s for k, w in weights.items()}
    base = {k: int(v) for k, v in raw.items()}
    rem = int(total - sum(base.values()))
    order = sorted(weights, key=lambda k: (-(raw[k] - base[k]), str(k)))
    for k in order[:max(0, rem)]:
        base[k] += 1
    return base


def _fix_endings(counts):
    """Every count ends in a non-zero digit; the total never moves. A row
    that lands on a zero takes one person from (or gives one to) the
    nearest row that stays clean after the move, smallest rows first."""
    keys = [k for k, v in counts.items() if v > 0]
    for _ in range(4):
        bad = [k for k in keys if counts[k] % 10 == 0]
        if not bad:
            break
        for k in sorted(bad, key=lambda x: (counts[x], str(x))):
            if counts[k] % 10 != 0:
                continue
            fixed = False
            for other in sorted(keys, key=lambda x: (counts[x], str(x))):
                if other == k or counts[other] <= 1:
                    continue
                if (counts[other] - 1) % 10 != 0 and (counts[k] + 1) % 10 != 0:
                    counts[other] -= 1
                    counts[k] += 1
                    fixed = True
                    break
            if not fixed:
                for other in sorted(keys, key=lambda x: (counts[x], str(x))):
                    if other == k or counts[k] <= 1:
                        continue
                    if (counts[other] + 1) % 10 != 0 and (counts[k] - 1) % 10 != 0:
                        counts[other] += 1
                        counts[k] -= 1
                        break
    return counts


def allocate(total, weights):
    """Single assignment of `total` people across the weighted rows:
    largest remainder, then clean last digits; rows add to `total`."""
    counts = _largest_remainder(int(total), dict(weights))
    return _fix_endings(counts)


def asset_rows(camp, fig, by=''):
    """The per-asset table under a held figure, or None when the campaign
    holds no asset-level data under it.

    The ticketing stage: one person on one asset, weighted by the
    converters each asset carried, rows adding to the stage exactly. The
    exposed base: the people each asset reached, overlapping (a person on
    several assets counts on each). `by` rolls the rows up ('channel',
    'tag', 'phase')."""
    kind = fig.get('kind')
    stage = str(fig.get('stage') or '')
    tps = touchpoint_rows(camp)
    if not tps:
        return None
    total = int(fig['value'])
    if kind == 'stage' and stage == '4_paid':
        weights = {r['id']: r['converters'] for r in tps}
        if sum(weights.values()) <= 0:
            return None
        counts = allocate(total, weights)
        rows = [dict(r, people=counts.get(r['id'], 0)) for r in tps]
        mode = 'single'
    elif kind == 'exposed' or (kind == 'stage' and stage == '1_exposed'):
        f = _factor(camp)
        if f <= 0:
            return None
        rows = []
        for r in tps:
            n = int(round(r['exposed'] * f))
            if n > 0 and n % 10 == 0:
                n += 1
            rows.append(dict(r, people=n))
        mode = 'overlap'
    else:
        return None
    rows = [r for r in rows if r['people'] > 0]
    if by in ('channel', 'tag', 'phase'):
        agg = {}
        for r in rows:
            k = r[by] or 'other'
            g = agg.setdefault(k, {'id': f"{by}:{k}", 'asset': k, 'channel': r['channel'] if by == 'channel' else '',
                                   'tag': r['tag'] if by == 'tag' else '', 'phase': r['phase'] if by == 'phase' else '',
                                   'type': '', 'posted': '', 'url': '', 'people': 0, 'assets': 0})
            g['people'] += r['people']
            g['assets'] += 1
        rows = list(agg.values())
        if mode == 'single':
            fixed = _fix_endings({r['id']: r['people'] for r in rows})
            for r in rows:
                r['people'] = fixed.get(r['id'], r['people'])
    rows.sort(key=lambda r: (-r['people'], r['asset']))
    cum = 0
    for i, r in enumerate(rows, 1):
        cum += r['people']
        r['rank'] = i
        r['share'] = round(100.0 * r['people'] / total, 1) if total else 0.0
        r['cumulative'] = round(100.0 * cum / total, 1) if total else 0.0
    return {'mode': mode, 'rows': rows, 'total': total, 'tracked': len(tps),
            'carried_none': len(tps) - len([r for r in tps if (r['converters'] if mode == 'single' else r['exposed']) > 0]),
            'by': by}


# ---------------------------------------------------------------- words

def _title_line(camp):
    return f"On {camp['display_name']} (Attribution IQ), {camp['window']['label']}:" if camp['window'].get('label') \
        else f"On {camp['display_name']} (Attribution IQ):"


def _the(family):
    """'the The Influencer Project campaign' never (view_base precedent)."""
    f = str(family or '').strip()
    return f if f.lower().startswith('the ') else f"the {f}"


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _short(label, limit=72):
    t = str(label or '').strip()
    return t if len(t) <= limit else t[:limit - 3].rstrip(' ,') + '...'


def _row_line(r, by='', mode='single', total=0):
    head = _short(r['asset'])
    if by == 'channel' or by == 'phase':
        head = f"{r['asset']} ({_plural(r['assets'], 'asset')})"
    elif by == 'tag':
        head = f"{r['asset'].capitalize()} assets ({r['assets']})"
    else:
        bits = [b for b in (r.get('channel'), r.get('tag')) if b]
        if bits:
            head = f"{head} ({', '.join(bits)})"
    if mode != 'single':
        return f"{r['rank']}. {head}: {r['people']:,} people, {r['share']:.1f}% of the {total:,}"
    return f"{r['rank']}. {head}: {r['people']:,} people, {r['share']:.1f}%, cumulative {r['cumulative']:.1f}%"


def _tag_sentence(rows, total):
    agg = {}
    for r in rows:
        agg[r['tag']] = agg.get(r['tag'], 0) + r['people']
    parts = []
    names = {'paid': 'paid creator posts', 'earned': 'earned creator clips and press', 'official': "the studio's own assets", 'organic': 'organic posts'}
    for tag, n in sorted(agg.items(), key=lambda kv: -kv[1]):
        parts.append(f"{names.get(tag, tag + ' assets')} {n:,} ({100.0 * n / total:.1f}%)")
    return ('By tag: ' + ', '.join(parts) + '.') if parts else ''


def reply_for_assets(camp, fig, table, text=''):
    rows, total, by = table['rows'], table['total'], table.get('by') or ''
    k = min(_TOP_ROWS_IN_CHAT, len(rows))
    top = rows[:k]
    rest = rows[k:]
    rest_n = sum(r['people'] for r in rest)
    lines = [_title_line(camp), '']
    what = _conversion_phrase(fig.get('label')) if fig.get('kind') == 'stage' else 'saw at least one of the campaign\'s assets'
    unit = {'channel': 'channels', 'tag': 'tags', 'phase': 'phases'}.get(by, 'tracked assets and creator groups')
    if table['mode'] == 'single':
        lead = (f"The {total:,} people who {what} sit on {len(rows)} {unit}, each person counted once, on the asset they "
                f"last carried before the visit, so the rows add to {total:,} exactly.")
        if table.get('carried_none') and not by:
            lead += f" {table['carried_none']} of the {table['tracked']} tracked assets carried none of them."
    else:
        lead = (f"Each of the {len(rows)} {unit} reached a share of the {total:,} people who {what}; a person who saw "
                f"several assets counts on each, so the rows overlap and do not add to {total:,}.")
    if len(rows) >= 2:
        a, b = rows[0], rows[1]
        if table['mode'] == 'single':
            lead += (f" {_short(a['asset'])} carries the most, {a['people']:,} ({a['share']:.1f}%), and {_short(b['asset'])} is next with "
                     f"{b['people']:,} ({b['share']:.1f}%); together the top two hold {a['people'] + b['people']:,} "
                     f"({100.0 * (a['people'] + b['people']) / total:.1f}%).")
        else:
            lead += (f" {_short(a['asset'])} reached the most, {a['people']:,} ({a['share']:.1f}% of the {total:,}), then "
                     f"{_short(b['asset'])} with {b['people']:,} ({b['share']:.1f}%).")
    if rest and table['mode'] == 'single':
        lead += (f" The top {k} rows cover {top[-1]['cumulative']:.1f}%; the other {len(rest)} carry {rest_n:,} "
                 f"({100.0 * rest_n / total:.1f}%) between them.")
    if table['mode'] == 'single' and not by:
        ts = _tag_sentence(rows, total)
        if ts:
            lead += ' ' + ts
    lines.append(lead)
    lines.append('')
    head = {'channel': 'Channels', 'tag': 'Paid, earned and official', 'phase': 'Phases'}.get(by, 'Assets')
    if table['mode'] == 'single':
        lines.append(f"{head} behind the {total:,}, ranked (people, share, cumulative)")
    else:
        lines.append(f"{head} that reached the {total:,}, ranked (people reached, share of the {total:,})")
    for r in top:
        lines.append(_row_line(r, by, table['mode'], total))
    if rest and table['mode'] == 'single':
        lines.append(f"The other {len(rest)} {unit}: {rest_n:,} people, {100.0 * rest_n / total:.1f}%")
    elif rest:
        lines.append(f"The other {len(rest)} {unit} are in the CSV.")
    if table['mode'] == 'single':
        lines.append(f"Total: {total:,} people.")
    lines.append('')
    lines.append('How these are counted:')
    if camp['window'].get('label'):
        lines.append(f"- Window: {camp['window']['label']}, the campaign's first phase through its latest read.")
    ex = ((camp.get('doc') or {}).get('exposure') or {}).get('us_people')
    base = f"{int(ex):,} US people who saw at least one of its assets, projected to the US" if isinstance(ex, (int, float)) and ex else 'the US people who saw at least one of its assets, projected to the US'
    lines.append(f"- Base: {_the(camp['family'])} campaign, {base}.")
    if fig.get('kind') == 'stage' and fig.get('label'):
        lines.append(f"- Conversion: {what}.")
    if table['mode'] == 'single':
        lines.append(f"- Each person is counted once, on the asset they last carried before the visit; the rows add to {total:,}.")
    else:
        lines.append("- Reach per asset counts a person on every asset they saw, so the rows overlap.")
    lines.append(f"- Assets: {table['tracked']} tracked assets and creator groups across "
                 f"{', '.join(sorted({r['channel'] for r in touchpoint_rows(camp) if r['channel']}))}. "
                 "The full table, with channel, paid or organic, phase, posted date and address, is in the CSV.")
    return '\n'.join(lines)


def reply_for_held(camp, fig, table_rows=None):
    """A held figure with no asset-level data under it: what it is, where
    it sits, and the splits the campaign does hold."""
    lines = [_title_line(camp), '']
    v = fig['value']
    kind = fig.get('kind')
    if kind == 'stage':
        what = _conversion_phrase(fig.get('label'))
        par = fig.get('parent')
        s = f"{v:,} is the campaign's count of US people who {what}."
        if par:
            s += f" It sits inside the {par:,} on the step before it ({100.0 * v / par:.1f}% carried through)."
        lines.append(s)
        splits = []
        if fig.get('stage') == '3_ticketer':
            rows = ((camp['doc'].get('overall') or {}).get('paths') or {}).get('where', {}).get('ticketer_partition') or []
            if rows:
                splits.append('by ticketing surface: ' + ', '.join(f"{r.get('surface')} {int(r.get('us_accounts') or 0):,} ({r.get('pct')}%)" for r in rows if isinstance(r, dict)))
        if fig.get('stage') == '2_infoseek':
            rows = ((camp['doc'].get('overall') or {}).get('paths') or {}).get('where', {}).get('infoseek_overlap') or []
            if rows:
                splits.append('by research surface (people on several count on each): ' + ', '.join(f"{r.get('surface')} {int(r.get('us_accounts') or 0):,} ({r.get('pct')}%)" for r in rows if isinstance(r, dict)))
        lines.append('')
        lines.append(f"The campaign does not carry an asset-by-asset split of the {v:,}; the asset split it holds is on the "
                     f"people who went to a ticketing site and on the people each asset reached."
                     + (' The splits it holds under this step: ' + '; '.join(splits) + '.' if splits else ''))
    else:
        par = fig.get('parent')
        s = f"{fig.get('label')}: {v:,} people"
        if par:
            s += f", {100.0 * v / par:.1f}% of the {par:,} it sits inside"
        s += f" ({fig.get('table')})."
        lines.append(s)
        rows = fig.get('rows') or []
        if rows:
            sib = ', '.join(f"{(r.get('surface') or r.get('touchpoint') or r.get('bucket') or r.get('archetype'))} {int(r.get('us_accounts') or 0):,}"
                            for r in rows if isinstance(r, dict))
            lines.append(f"The whole table: {sib}.")
        if fig.get('note'):
            lines.append(str(fig['note']))
        lines.append('')
        lines.append(f"The campaign does not carry an asset-by-asset split under this row; the asset split it holds is on the "
                     f"people who went to a ticketing site and on the people each asset reached.")
    lines.append('')
    lines.append('How these are counted:')
    if camp['window'].get('label'):
        lines.append(f"- Window: {camp['window']['label']}, the campaign's first phase through its latest read.")
    ex = ((camp.get('doc') or {}).get('exposure') or {}).get('us_people')
    if isinstance(ex, (int, float)) and ex:
        lines.append(f"- Base: {_the(camp['family'])} campaign, {int(ex):,} US people who saw at least one of its assets, projected to the US.")
    return '\n'.join(lines)


def followups_for(camp, fig, table):
    doc = camp.get('doc') or {}
    nest = {n.get('stage'): n for n in (((doc.get('overall') or {}).get('paths') or {}).get('nest') or []) if isinstance(n, dict)}
    out = []
    v = fig['value']
    by = (table or {}).get('by') or ''
    if table and table.get('mode') == 'single':
        if by != 'channel':
            out.append(f"Split the {v:,} by channel")
        if by != 'tag':
            out.append(f"Split the {v:,} by paid, earned and official assets")
        ex = (doc.get('exposure') or {}).get('us_people')
        if isinstance(ex, (int, float)) and ex:
            out.append(f"Which assets reached the {int(ex):,} exposed people?")
    elif nest.get('4_paid'):
        out.append(f"Isolate the assets that drove the {int(nest['4_paid'].get('us_accounts') or 0):,} ticketing visitors")
    if table:
        out.append('Email me this file')
    return out[:4]


# ------------------------------------------------------------------ csv

def csv_for(camp, fig, table):
    """(filename, csv text): the full table, every row."""
    rows = table['rows']
    by = table.get('by') or ''
    what = 'ticketing visitors' if table['mode'] == 'single' else 'people reached'
    unit = {'channel': 'channel', 'tag': 'tag', 'phase': 'phase'}.get(by, 'asset')
    fname = f"{camp['family']} - {what} by {unit} behind the {table['total']:,}.csv".replace('/', '-')
    buf = io.StringIO()
    w = csv.writer(buf)
    count_col = 'Unique ticketing-site visitors' if table['mode'] == 'single' else 'People reached'
    if by:
        w.writerow(['Rank', unit.capitalize(), 'Assets', count_col, 'Share %', 'Cumulative %'])
        for r in rows:
            w.writerow([r['rank'], r['asset'], r['assets'], r['people'], f"{r['share']:.1f}", f"{r['cumulative']:.1f}"])
    else:
        w.writerow(['Rank', 'Asset', 'Channel', 'Paid or organic', 'Asset type', 'Phase', 'Posted', 'URL', count_col, 'Share %', 'Cumulative %'])
        for r in rows:
            w.writerow([r['rank'], r['asset'], r['channel'], r['tag'], r['type'], r['phase'], r['posted'], r['url'],
                        r['people'], f"{r['share']:.1f}", f"{r['cumulative']:.1f}"])
    if table['mode'] == 'single':
        w.writerow(['', 'Total', '', '', '', '', '', '', table['total'], '100.0', ''] if not by else ['', 'Total', '', table['total'], '100.0', ''])
    w.writerow([])
    w.writerow([f"{camp['display_name']} (Attribution IQ), {camp['window'].get('label') or ''}"])
    if table['mode'] == 'single':
        w.writerow(['Each person is counted once, on the asset they last carried before the ticketing visit; the rows add to the total.'])
    else:
        w.writerow(['A person who saw several assets counts on each, so the rows overlap.'])
    return fname, buf.getvalue()


def publish_csv(s3, bucket, fname, text):
    """Upload under generated_data/ with a 7-day link; None on any miss."""
    try:
        key = f"{DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
        s3.put_object(Bucket=bucket, Key=key, Body=text.encode('utf-8'), ContentType='text/csv')
        url = s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key,
                                                               'ResponseContentDisposition': f'attachment; filename="{fname}"'},
                                        ExpiresIn=7 * 24 * 3600)
        return {'url': url, 'label': f"Download {fname}", 'key': key}
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------------------------------------- binding

def _norm(s):
    return re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).strip()


def slug_from_ctx(ctx):
    vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
    vc = vc if isinstance(vc, dict) else {}
    try:
        from . import view_base as _vb
        if _vb.domain_for(vc.get('view_id')) != 'attribution':
            return ''
    except Exception:
        return ''
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    return re.sub(r"[^a-z0-9_\-]", '', str(data.get('title') or data.get('slug') or data.get('campaign') or '').lower())


def slug_from_binding(binding):
    b = binding if isinstance(binding, dict) else {}
    sk = str(b.get('subject_key') or '')
    if sk.lower().startswith('campaign:'):
        return re.sub(r"[^a-z0-9_\-]", '', sk.split(':', 1)[1].strip().lower())
    return ''


def _thread_binding():
    try:
        from flask import g as _g
        return getattr(_g, '_pm_thread_binding', None)
    except Exception:
        return None


def names_campaign(text, slug, family=''):
    """The words name the campaign: its family title whole, or two of its
    distinctive words (one when it only has one)."""
    t = f" {_norm(text)} "
    for cand in (_norm(family), _norm(slug.replace('_', ' ').replace('-', ' '))):
        if not cand:
            continue
        if f" {cand} " in t:
            return True
        toks = [w for w in cand.split() if len(w) > 3 and w not in _GENERIC_WORDS]
        hits = [w for w in toks if f" {w} " in t]
        if toks and (len(hits) >= 2 or len(hits) == len(toks)):
            return True
    return False


def resolve(text, ctx=None, binding=None, *, s3, bucket, numbers=None, hint=''):
    """(campaign, figure) for the campaign the ask binds to and the held
    figure it states; None when no campaign holds a stated figure. `hint`
    is extra words that may name the campaign (a bound subject on a
    resumed read); the figure always comes from the ask itself."""
    nums = list(numbers or numbers_in(text))
    if not nums:
        return None
    tried = []
    for slug in (slug_from_ctx(ctx), slug_from_binding(binding if binding is not None else _thread_binding())):
        if slug and slug not in tried:
            tried.append(slug)
    slugs = campaign_slugs(s3, bucket)
    words = f"{text} {hint or ''}"
    named = [s for s in slugs if s not in tried and names_campaign(words, s)]
    for slug in tried + named:
        camp = load_campaign(s3, bucket, slug)
        if not camp:
            continue
        if slug in named and not names_campaign(words, slug, camp.get('family')):
            continue
        fig = figure_match(camp, nums)
        if fig:
            return camp, fig
    # the library: any campaign that holds the figure (bounded)
    n = 0
    for slug in slugs:
        if slug in tried or slug in named:
            continue
        if n >= _MAX_LIBRARY_SCAN:
            break
        n += 1
        camp = load_campaign(s3, bucket, slug)
        if not camp:
            continue
        fig = figure_match(camp, nums)
        if fig:
            return camp, fig
    return None


# ----------------------------------------------------------------- lane

def _scrub(text):
    try:
        from . import guards as _g
        return _g.scrub_method_language(text)
    except Exception:
        return text


def window_for_base(base, *, s3=None, bucket=None):
    """The campaign window for a view base of the attribution domain
    ({'start','end','label'}), from the screen's campaign or the one the
    base names; None when no campaign resolves."""
    try:
        b = base if isinstance(base, dict) else {}
        if s3 is None:
            s3, bucket = _s3()
        slug = slug_from_ctx({'view_context': b.get('view_context')})
        camp = load_campaign(s3, bucket, slug) if slug else None
        if not camp:
            for cand in campaign_slugs(s3, bucket):
                c = load_campaign(s3, bucket, cand)
                if c and names_campaign(str(b.get('subject') or ''), cand, c.get('family')):
                    camp = c
                    break
        return dict(camp['window']) if camp and camp['window'].get('label') else None
    except Exception:
        return None


def answer(text, uname='', ctx=None, tid=None, *, s3=None, bucket=None, binding=None, publish=True, hint=''):
    """The lane. None when the ask is not a campaign figure breakdown or
    no campaign holds the figure. Otherwise the raw analyze payload,
    answered from the campaign's own table with no model call."""
    try:
        if not looks_like(text):
            return None
        if s3 is None:
            s3, bucket = _s3()
        hit = resolve(text, ctx, binding, s3=s3, bucket=bucket, hint=hint)
        if not hit:
            return None
        camp, fig = hit
        by = group_by(text)
        table = asset_rows(camp, fig, by=by)
        if table and table['rows']:
            reply = reply_for_assets(camp, fig, table, text)
            kind = 'assets' if not by else f'assets_by_{by}'
        else:
            table = None
            reply = reply_for_held(camp, fig)
            kind = 'held'
        reply = _scrub(reply)
        out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups_for(camp, fig, table),
               'offer_deck': False, 'deck_angle': None, 'subject': camp['display_name'], 'profile': camp['display_name'],
               'campaign': camp['slug'], 'drilldown': kind, 'figure': fig['value'],
               'window_label': camp['window'].get('label') or '', 'window_start': camp['window'].get('start') or '',
               'window_end': camp['window'].get('end') or ''}
        if table:
            fname, csv_text = csv_for(camp, fig, table)
            out['csv_name'], out['csv_text'] = fname, csv_text
            out['rows'] = [{'rank': r['rank'], 'asset': r['asset'], 'channel': r.get('channel', ''), 'tag': r.get('tag', ''),
                            'people': r['people'], 'share': r['share'], 'cumulative': r['cumulative']} for r in table['rows']]
            out['total'] = table['total']
            if publish:
                link = publish_csv(s3, bucket, fname, csv_text)
                if link:
                    out['file_link'] = {'url': link['url'], 'label': link['label']}
                    out['csv_key'] = link['key']
                    out.pop('csv_text', None)   # the file carries the full table; the payload carries the link
        return out
    except Exception as e:
        traceback.print_exc()
        try:
            from . import faults as _faults
            _faults.note('campaign_drilldown', e)
        except Exception:
            pass
        return None
