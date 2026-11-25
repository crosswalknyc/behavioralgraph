"""Peacock title figures in a Prometheus read (Jenna, 2026-11-18).

A read on Peacock answers from the figures on file. The reasoned
measurement pass hands the model one block, built here from
``migration.peacock_actuals``: for each show on file the question names,
its US viewers and plays for the windows the question carries (and the
trailing twelve months), and the scale any other Peacock title is read
against. The block is binding and takes precedence over an earlier
figure for the same title on Peacock. A read that never names Peacock
gets the same block when it is about a Peacock title (a show on file
named in the words, the request or the base, or a base seeded on
Peacock pages), so a Peacock split inside it reads the figures on file
and the scale (Jenna 2026-11-24, "cover every Peacock title figure"); a
read that names another service gets none.

What the block carries is ours: viewers (people, each once) and plays.
It never carries an account figure or a word on where a figure came
from, so the model has nothing of that kind to repeat. A question that
asks for a title's accounts on Peacock is answered in viewers, with one
plain sentence that the count is people: a title's account count on a
service is not a figure Crosswalk states, to anyone.

``held_conflicts`` names the earlier reads on the ledger that the
figures on file contradict, so they can be set aside the way a read
that contradicts a record is (insights_ledger.retire_entries).

Pure text and arithmetic over the shared module; no model. Every entry
point returns '' / [] on any failure, so a read with no record behind
it runs exactly as before. Wired in prometheus.legacy.chat (the
measurement pass's ledger block). Regression:
scripts/test_peacock_calibration_wiring.py.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

__all__ = ['block', 'windows_in', 'held_conflicts', 'guard']

_MONTHS = {m: i + 1 for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'))}
_ISO_RX = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MDY_RX = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I)
_MY_RX = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d{2})\b", re.I)
_TRAIL_RX = re.compile(r"\b(?:trailing|last|past)\s+(\d{1,3})\s*[- ]?(day|week|month)s?\b", re.I)


def _pk():
    try:
        from migration import peacock_actuals as pk
        return pk
    except Exception:
        return None


def _month_end(y, m):
    import calendar
    return date(y, m, calendar.monthrange(y, m)[1])


def windows_in(*texts, today=None) -> list:
    """The windows a question or a request names, as (start, end) dates:
    two full dates, two month-and-year marks, one month-and-year mark
    (that month), or a trailing span. [] when none can be read."""
    today = today or datetime.now(timezone.utc).date()
    out = []
    for t in texts:
        s = str(t or '')
        if not s:
            continue
        days = []
        for m in _ISO_RX.finditer(s):
            try:
                days.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
            except ValueError:
                pass
        for m in _MDY_RX.finditer(s):
            try:
                days.append((m.start(), date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))))
            except ValueError:
                pass
        days.sort()
        if len(days) >= 2:
            a, b = days[0][1], days[1][1]
            if b >= a:
                out.append((a, b))
        elif not days:
            marks = [(int(m.group(2)), _MONTHS[m.group(1).lower()[:3]]) for m in _MY_RX.finditer(s)]
            if len(marks) >= 2:
                a, b = date(marks[0][0], marks[0][1], 1), _month_end(*marks[1])
                if b >= a:
                    out.append((a, b))
            elif len(marks) == 1:
                out.append((date(marks[0][0], marks[0][1], 1), _month_end(*marks[0])))
        m = _TRAIL_RX.search(s)
        if m:
            n = int(m.group(1))
            span = n * {'day': 1, 'week': 7, 'month': 30.4375}[m.group(2).lower()]
            out.append((today - timedelta(days=int(round(span)) - 1), today))
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq[:4]


_OTHER_SERVICE_RX = re.compile(r"\b(?:hulu|netflix|prime video|amazon|hbo|max|paramount|disney|apple tv|youtube|tubi|pluto|roku|"
                               r"starz|mgm|amc\+|britbox|fubo|sling|discovery\+|bet\+|crunchyroll|vudu|fandango)\b", re.I)
_AUDIENCE_TAIL_RX = re.compile(r"\s+(?:on\s+peacock|tv series|series|viewers?|watchers?|audience|fans)\s*$", re.I)
_TITLE_BASE_RX = re.compile(r"\b(?:viewers?|watchers?|streamers?|series|show|season|episodes?|film|movie|special)\b", re.I)
_SEEDED = {}                 # s3_key -> the base's universe is seeded on Peacock pages (one read per key per process)
_HEAD_READER = None          # tests hand in a reader: (s3_key) -> the first bytes of the base's file, as text
_S3 = []


def _base_head(key):
    if _HEAD_READER is not None:
        return _HEAD_READER(key)
    if str(os.environ.get('REGRESSION_TEST_MODE', '')).strip() == '1':
        return ''
    if not _S3:
        import boto3
        _S3.append(boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2'))
    pk = _pk()
    body = _S3[0].get_object(Bucket=getattr(pk, 'BUCKET', 'dashboard-inputs'), Key=key, Range='bytes=0-16383')['Body'].read()
    return body.decode('utf-8', 'replace')


def _base_seeded_on_peacock(base) -> bool:
    """The read's base is a title viewers universe seeded on Peacock pages
    (its BRAND INPUT carries the Peacock watch addresses). Only a base
    named like a title's audience is looked at, so a talent, brand or
    persona read never waits on it."""
    key = str((base or {}).get('s3_key') or '')
    if not key.lower().endswith('.csv') or ':' in key or not _TITLE_BASE_RX.search(str((base or {}).get('subject') or '')):
        return False
    if key not in _SEEDED:
        try:
            head = _base_head(key) or ''
            line = next((ln for ln in head.splitlines()[1:60] if ln.upper().startswith('BRAND INPUT,')), '')
            _SEEDED[key] = 'peacocktv' in line.lower()
        except Exception:
            return False
    return bool(_SEEDED.get(key))


def _unnamed_peacock_title(pk, hay, base) -> str:
    """A read that never names Peacock but is about a Peacock title (Jenna
    2026-11-24, "cover every Peacock title figure"): a show on file named in
    the words, the request or the base, or a base seeded on Peacock pages.
    '' when the read names another service, or is about no Peacock title."""
    if _OTHER_SERVICE_RX.search(hay):
        return ''
    named = pk.titles_named(hay)
    if named:
        return ' and '.join(pk.display_title(k) or k for k in named)
    if _base_seeded_on_peacock(base):
        return _AUDIENCE_TAIL_RX.sub('', str((base or {}).get('subject') or '').strip()).strip() or 'this title'
    return ''


def block(text, metric_request=None, base=None, history=None) -> str:
    """The binding lines for a read on Peacock, or ''. The question, the
    measurement request and the base's own name are read for Peacock,
    for the shows on file and for windows. A read that never names
    Peacock gets the same lines when it is about a Peacock title, so a
    Peacock split inside it reads the figures on file and the scale."""
    try:
        pk = _pk()
        if pk is None or not pk.available():
            return ''
        mr = metric_request if isinstance(metric_request, dict) else {}
        b = base if isinstance(base, dict) else {}
        hay = ' '.join(str(x or '') for x in (text, mr.get('subject'), mr.get('needed'), mr.get('cohort'), mr.get('window'),
                                               b.get('subject'), b.get('universe')))
        if 'peacock' not in hay.lower():
            title = _unnamed_peacock_title(pk, hay, b)
            if not title:
                return ''
            hay = f"{hay} ({title} on Peacock)"
        return pk.binding_block(hay, windows=windows_in(text, mr.get('window')))
    except Exception:
        return ''


def guard(text):
    """The net on an outgoing text (the shared scrub)."""
    try:
        pk = _pk()
        return pk.scrub(text) if pk is not None else text
    except Exception:
        return text


_PEOPLE_UNITS = ('viewers', 'people', 'persons', 'individuals', 'players')


def held_conflicts(index_doc, tolerance: float = 0.35) -> list:
    """Earlier reads on the ledger that state a US viewers figure for a
    show on file, on Peacock, that the figure on file contradicts by more
    than ``tolerance``: [{'key', 'subject', 'question', 'asker', 'ts',
    'label', 'held', 'on_file', 'ratio', 'window'}]. A metric counts when
    its unit is people and it names the show's whole Peacock audience in
    the entry's window (a share, a subset or an overlap line is not
    compared). Never raises."""
    out = []
    try:
        pk = _pk()
        if pk is None or not pk.available():
            return out
        for _sk, sub in ((index_doc or {}).get('subjects') or {}).items():
            for e in (sub.get('entries') or []):
                if not isinstance(e, dict) or e.get('retired'):
                    continue
                ctx = ' '.join(str(e.get(k) or '') for k in ('subject', 'q', 'reply'))
                if 'peacock' not in ctx.lower():
                    continue
                ws, we = str(e.get('ws') or '')[:10], str(e.get('we') or '')[:10]
                for m in (e.get('metrics') or []):
                    if not isinstance(m, dict) or str(m.get('unit') or '').lower() not in _PEOPLE_UNITS:
                        continue
                    words = ' '.join(str(m.get(k) or '') for k in ('label', 'definition'))
                    low = words.lower()
                    if re.search(r"\b(?:also|both|only|either|inside the \d|share|subscri\w*|avid|first[- ]time|returning|already|before|"
                                 r"four or more|each series|unduplicated|peak|latest|day)\b", low):
                        continue
                    names = pk.titles_named(words) or pk.titles_named(str(e.get('subject') or ''))
                    if len(names) != 1 or ('peacock' not in low and 'peacock' not in str(e.get('subject') or '').lower()):
                        continue
                    a, b = ws, we
                    span = windows_in(str(m.get('definition') or ''))
                    if span:
                        a, b = span[0][0].isoformat(), span[0][1].isoformat()
                    v = pk.viewers(names[0], a, b)
                    try:
                        held = float(m.get('value') or 0)
                    except (TypeError, ValueError):
                        held = 0.0
                    if not v or held <= 0:
                        continue
                    ratio = held / float(v['viewers'])
                    if abs(ratio - 1.0) > tolerance:
                        out.append({'key': e.get('k'), 'subject': e.get('subject'), 'question': str(e.get('q') or '')[:160],
                                    'asker': e.get('asker'), 'ts': e.get('ts'), 'label': m.get('label'), 'held': int(held),
                                    'on_file': int(v['viewers']), 'ratio': round(ratio, 2), 'window': f'{a} to {b}'})
    except Exception:
        return out
    return out
