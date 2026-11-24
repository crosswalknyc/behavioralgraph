""""Full breakdown" gets a real breakdown (Nov 23 2026 ask audit, fix 8;
Jenna 2026-11-23: when the reader asks for the full breakdown of a figure
or a read, the answer lists its parts, by the dimension the read carries,
with figures that add up, instead of the one-line provenance answer).

The live case: a Profile IQ page, a read that said "YouTube carries
2,667,703 of the 2,851,821 US people in the <subject> audience", then
"what is the full breakdown of 2,851,821 audience. Where is the audience
from also". The reply said only where the figure came from ("2,851,821 is
the US projection of the <subject> profile in Profile IQ, an audience
read for ..."), and the breakdown and the geography went unanswered.

What a breakdown is, in order of what we hold:

1. The record of the read that stated the figure, when it carries a
   breakdown table (a matrix read's groups, a title-list read's titles):
   its rows, each as people and share, adding up to the figure.
2. The profile the figure is the US projection of (the page, or the
   read's base): the audience split by gender and by age (each adds up
   to the figure), where they live (the top markets and everything
   else, adding up), and the services it reaches through (overlapping,
   said so).

Nothing here calls a model or charges; the lane that answers a quoted
figure stays free. Anything that cannot be built returns None and the
provenance line stands as it did. Nothing a reader sees names how the
split was produced.

Regression: scripts/test_pm_audit_1123_full_breakdown.py.
"""
from __future__ import annotations

import re

__all__ = ['wants', 'build', 'render', 'implied_figure', 'BREAKDOWN_RX', 'GEO_RX']

_ANAPHOR = r"(?:it|that|this|them|those|these|the\s+(?:number|figure|total|count|audience|read|result|numbers|figures)|that\s+(?:number|figure|total|audience|read)|this\s+(?:number|figure|total|audience|read))"
BREAKDOWN_RX = re.compile(
    r"\b(?:(?:full|whole|complete|detailed|entire)\s+breakdown|break-?down\s+(?:of|on|for|behind)\s+(?:the\s+)?(?:\$?\d|" + _ANAPHOR + r")|"
    r"break\s+(?:" + _ANAPHOR + r"|\$?\d[\d,.]*\s*\w*)\s+(?:down|out|up)|break\s+(?:down|out)\s+(?:the\s+)?(?:\$?\d|" + _ANAPHOR + r")|"
    r"what\s+(?:makes|goes)\s+(?:up|into)\s+(?:the\s+)?(?:\$?\d|" + _ANAPHOR + r")|(?:composition|make-?up|split|parts)\s+of\s+(?:the\s+)?(?:\$?\d|" + _ANAPHOR + r")|"
    r"how\s+(?:is|does)\s+(?:the\s+)?(?:\$?\d[\d,.]*|" + _ANAPHOR + r")\s+(?:split|broken\s+(?:down|out)|made\s+up|composed)|"
    r"(?:split|breakdown)\s+(?:by|across)\s+(?:gender|age|platform|market|region|state|city|title|month|segment))\b", re.I)
GEO_RX = re.compile(
    r"\b(?:where\s+(?:is|are|does|do)\s+(?:the\s+|this\s+|that\s+)?(?:audience|they|people|viewers|fans|readers|users|it)\s+(?:from|based|located|live|living|coming\s+from)|"
    r"where\s+(?:are\s+)?they\s+(?:from|based|located)|geograph|by\s+(?:market|region|state|city|dma|location)|which\s+(?:markets|states|cities|regions|dmas)|"
    r"(?:top|biggest)\s+(?:markets|states|cities|dmas))\b", re.I)
_FIGURE_RX = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
_SUBJECT_NAMED_RX = re.compile(r"\b(?:breakdown|split|composition)\s+(?:of|for|on)\s+(?:the\s+)?[A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,4}\s+(?:viewers|fans|audience|readers|subscribers|buyers|shoppers|users|customers)\b")

_DEMO_PARTITIONS = (('GENDER', 'By gender'), ('AGE', 'By age'))
_GEO_COLS = ('LOCATION', 'DMA', 'MARKET')
_PLATFORM_COLS = ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'SOCIAL MEDIA', 'APP/PLATFORM', 'APP/PLATFORM USAGE',
                  'STREAMING MUSIC', 'STREAMING/MUSIC', 'VIRTUAL MVPD FAST', 'VIRTUAL MVPD/FAST', 'FAST PLATFORM')
_SKIP_VALUES = frozenset(('', 'nan', 'none', 'total', 'other', 'unknown', 'not specified', 'prefer not to say'))
_MAX_ROWS = 8


def _squash(s):
    return ' '.join(str(s or '').split())


def wants(text):
    """{'breakdown': bool, 'geo': bool} when the words ask for the parts of
    a figure or a read already in hand (or where that audience is from),
    else None. A breakdown of a NAMED audience ("demographic breakdown of
    Yellowstone viewers") is a read of its own, never this."""
    t = _squash(text)
    if not t or len(t) > 400:
        return None
    if _SUBJECT_NAMED_RX.search(t) and not _FIGURE_RX.search(t):
        return None
    bd = bool(BREAKDOWN_RX.search(t))
    geo = bool(GEO_RX.search(t))
    if not bd and not geo:
        return None
    return {'breakdown': bd, 'geo': geo}


def implied_figure(text, history, max_turns=6):
    """For a breakdown ask that quotes no figure ("break that down", "what
    is the full breakdown?"), the headline figure of the latest landed
    read in the thread: {'figure', 'digits', 'turn', 'sentence'} or None."""
    t = _squash(text)
    if not t or _FIGURE_RX.search(t) or not wants(t):
        return None
    turns = [h for h in (history or []) if isinstance(h, dict)]
    agent = [(i, h) for i, h in enumerate(turns) if str(h.get('role') or '').lower() in ('agent', 'assistant')][-max_turns:]
    for i, h in reversed(agent):
        body = str(h.get('text') or h.get('content') or '')
        meta = h.get('meta') if isinstance(h.get('meta'), dict) else {}
        if not (meta.get('read_job_id') or str(meta.get('kind') or '') == 'read' or len(body) >= 160):
            continue
        figs = [(m.group(0), int(re.sub(r"[^\d]", "", m.group(0)))) for m in _FIGURE_RX.finditer(body)]
        figs = [f for f in figs if f[1] >= 1000]
        if not figs:
            continue
        raw, val = max(figs, key=lambda f: f[1])
        return {'figure': raw, 'digits': str(val), 'turn': i, 'sentence': ''}
    return None


# ------------------------------------------------------------------ data
def _num(x):
    try:
        s = str(x).replace('%', '').replace(',', '').strip()
        if s in ('', 'nan', 'None'):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _cols(df):
    bp = next((c for c in df.columns if 'penetration' in c.lower() and 'row' in c.lower()), None) \
        or next((c for c in df.columns if 'penetration' in c.lower()), None)
    pj = next((c for c in df.columns if 'projection' in c.lower()), None)
    return bp, pj


def _frame(host, key):
    if not host or not key:
        return None
    try:
        from .brand_coverage import _frame as _bf
        df = _bf(host, key)
        return df if df is not None and len(df) and 'Column' in df.columns and 'Value' in df.columns else None
    except Exception:
        return None


def _rows(df, col_names):
    bp, pj = _cols(df)
    if not bp:
        return []
    want = {c.upper() for c in col_names}
    out = []
    for col, val, b, p in zip(df['Column'].astype(str), df['Value'].astype(str), df[bp], df[pj] if pj else [None] * len(df)):
        if col.strip().upper() not in want:
            continue
        v = _squash(val)
        if v.lower() in _SKIP_VALUES:
            continue
        share = _num(b)
        if share is None or share <= 0:
            continue
        out.append({'label': v, 'share': share, 'proj': _num(p)})
    out.sort(key=lambda r: -r['share'])
    return out


_AGE_LEAD_RX = re.compile(r"^(?:under\s*)?(\d{1,3})")


def _age_order(rows):
    def k(r):
        m = _AGE_LEAD_RX.match(r['label'].lower())
        n = int(m.group(1)) if m else 999
        return (n - 1 if r['label'].lower().startswith('under') else n, r['label'])
    return sorted(rows, key=k)


def _nice(label, col=''):
    """A bucket label as a reader writes it: 'Male', 'Non-binary', 'Trans female'; a market or a service as it is."""
    t = _squash(label)
    if col in ('GENDER',) or (t.isupper() and len(t) > 3 and not re.search(r"\d", t)):
        t = t.lower()
        t = t[:1].upper() + t[1:]
        t = re.sub(r"\blgbtq\+?", 'LGBTQ+', t, flags=re.I)
    return t


def profile_total(df):
    """The profile's US projection: its SAMPLE SIZE (else BRAND INPUT) row's projection."""
    bp, pj = _cols(df)
    if not pj:
        return None
    for want in ('SAMPLE SIZE', 'BRAND INPUT'):
        for col, p in zip(df['Column'].astype(str), df[pj]):
            if col.strip().upper() == want:
                v = _num(p)
                if v and v > 0:
                    return int(round(v))
    return None


def _add_up(rows, total, keep=_MAX_ROWS, rest_label=''):
    """People per row from its share of the total, the rounding remainder
    put on the biggest row so the parts add to the total exactly. Rows
    past `keep` fold into one rest row (named) when a rest label is
    given, else the shown rows are scaled to the total they cover."""
    if not rows or not total:
        return []
    shown = rows[:keep]
    rest = rows[keep:]
    parts = [{'label': r['label'], 'share': r['share'], 'people': int(round(total * r['share'] / 100.0))} for r in shown]
    covered = sum(r['share'] for r in shown)
    if rest_label:
        rest_share = max(0.0, 100.0 - covered)
        if rest or rest_share > 0.05:
            parts.append({'label': rest_label, 'share': rest_share, 'people': int(round(total * rest_share / 100.0))})
    diff = total - sum(p['people'] for p in parts)
    if parts and diff:
        big = max(parts, key=lambda p: p['people'])
        big['people'] += diff
    return parts


def _partition_ok(rows):
    """A gender or age block is a partition only when its shares add to about 100."""
    s = sum(r['share'] for r in rows)
    return 90.0 <= s <= 110.0


# ---------------------------------------------------------------- sources
def _from_record(entry, total):
    """The breakdown table a read's record carries, as parts of the figure."""
    if not isinstance(entry, dict):
        return None
    bd = entry.get('breakdown') if isinstance(entry.get('breakdown'), dict) else {}
    rows = [r for r in (bd.get('rows') or []) if isinstance(r, dict) and r.get('label')]
    if not rows:
        return None
    pools = [r.get('pool') if isinstance(r.get('pool'), (int, float)) else None for r in rows]
    if all(p is not None for p in pools) and pools:
        s = sum(pools)
        if s and abs(s - total) / float(total) <= 0.02:
            parts = [{'label': str(r['label']), 'share': (r.get('share_pct') if isinstance(r.get('share_pct'), (int, float)) else 100.0 * p / s),
                      'people': int(p)} for r, p in zip(rows, pools)]
            diff = total - sum(p['people'] for p in parts)
            if diff:
                max(parts, key=lambda p: p['people'])['people'] += diff
            return {'dimension': str(bd.get('dimension') or 'Part'), 'parts': parts, 'adds': True}
    shares = [r.get('share_pct') for r in rows]
    if all(isinstance(x, (int, float)) for x in shares) and shares:
        srows = [{'label': str(r['label']), 'share': float(r['share_pct'])} for r in rows]
        if 90.0 <= sum(x['share'] for x in srows) <= 110.0:
            return {'dimension': str(bd.get('dimension') or 'Part'), 'parts': _add_up(srows, total, keep=12), 'adds': True}
    return None


def _from_profile(df, total, *, geo, platforms, stated=None):
    sections = []
    for col, label in _DEMO_PARTITIONS:
        rows = _rows(df, (col,))
        if rows and _partition_ok(rows):
            rows = [dict(r, label=_nice(r['label'], col)) for r in rows]
            if col == 'AGE':
                rows = _age_order(rows)
            sections.append({'dimension': label, 'parts': _add_up(rows, total, keep=_MAX_ROWS, rest_label='All other'), 'adds': True})
    geo_rows = _rows(df, _GEO_COLS)
    if geo_rows:
        sections.append({'dimension': 'Where they live (top markets)', 'parts': _add_up(geo_rows, total, keep=_MAX_ROWS, rest_label='All other markets'), 'adds': True})
    elif geo:
        sections.append({'dimension': 'Where they live', 'parts': [], 'adds': False, 'note': 'This profile does not carry a market split.'})
    if platforms:
        prow = _rows(df, _PLATFORM_COLS)
        seen, uniq = set(), []
        for r in prow:
            k = re.sub(r"[^a-z0-9]", "", r['label'].lower())
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        if uniq:
            parts = []
            for r in uniq[:_MAX_ROWS]:
                people = int(round(total * r['share'] / 100.0))
                said = (stated or {}).get(re.sub(r"[^a-z0-9]", "", r['label'].lower()))
                if said and abs(said - people) <= max(50, 0.01 * people):
                    people = said          # the figure the thread already stated for this service stands
                parts.append({'label': r['label'], 'share': r['share'], 'people': people})
            sections.append({'dimension': 'Where they are online', 'parts': parts, 'adds': False,
                             'note': 'One person uses more than one, so these do not add to the total.'})
    return sections


_STATED_RX = re.compile(r"\b([A-Z][A-Za-z0-9+&'.-]*(?:\s+[A-Z][A-Za-z0-9+&'.-]*){0,3})\s+(?:carries|reaches|has|holds|at|:)\s*(\d{1,3}(?:,\d{3})+)")


def stated_figures(history, max_turns=8):
    """{service key: people} for figures the thread's reads already stated next to a service name
    ("YouTube carries 2,667,703 of the ...", "YouTube reach inside the audience: 2,667,703")."""
    out = {}
    turns = [h for h in (history or []) if isinstance(h, dict) and str(h.get('role') or '').lower() in ('agent', 'assistant')][-max_turns:]
    for h in turns:
        body = str(h.get('text') or h.get('content') or '')
        for m in _STATED_RX.finditer(body):
            name = re.sub(r"\s+(?:reach|people|viewers|users|audience)\b.*$", '', m.group(1), flags=re.I).strip()
            k = re.sub(r"[^a-z0-9]", "", name.lower())
            if k and k not in out:
                try:
                    out[k] = int(m.group(2).replace(',', ''))
                except ValueError:
                    pass
    return out


def build(figure_digits, *, record=None, ctx=None, host=None, text='', history=None):
    """{'total', 'sections': [{dimension, parts: [{label, share, people}], adds, note?}], 'source'} or None."""
    try:
        total = int(float(str(figure_digits or '').replace(',', '')))
    except (TypeError, ValueError):
        return None
    if total < 1000:
        return None
    w = wants(text) or {'breakdown': True, 'geo': False}
    entry = (record or {}).get('entry') if isinstance(record, dict) else None
    sec = _from_record(entry, total)
    if sec:
        return {'total': total, 'sections': [sec], 'source': 'record'}
    keys = []
    if isinstance(record, dict) and record.get('base'):
        keys.append(str(record['base']))
    try:
        pk = str(((ctx or {}).get('primary') or {}).get('s3_key') or '').strip()
        if pk and pk not in keys:
            keys.append(pk)
    except Exception:
        pass
    for key in keys:
        if not key.lower().endswith('.csv'):
            continue
        df = _frame(host, key)
        if df is None:
            continue
        ptot = profile_total(df)
        if ptot and abs(ptot - total) / float(total) > 0.03:
            continue        # the figure is not this profile's whole audience; a split of it would be a guess
        platforms = w['breakdown'] or w['geo']
        sections = _from_profile(df, total, geo=w['geo'], platforms=platforms, stated=stated_figures(history))
        if any(s.get('parts') for s in sections):
            return {'total': total, 'sections': sections, 'source': 'profile', 'key': key}
    return None


# ----------------------------------------------------------------- words
def _fmt(n):
    return f"{int(n):,}"


def _pct(x):
    return f"{float(x):.1f}%"


def render(bd, figure_text):
    """The breakdown as plain lines under one lead sentence."""
    if not bd or not bd.get('sections'):
        return ''
    lines = [f"Here is how the {figure_text} breaks down."]
    for s in bd['sections']:
        parts = s.get('parts') or []
        if not parts:
            if s.get('note'):
                lines.append(f"{s['dimension']}: {s['note']}")
            continue
        items = '; '.join(f"{p['label']} {_fmt(p['people'])} ({_pct(p['share'])})" for p in parts)
        tail = ''
        if s.get('adds'):
            tail = f" These add up to {_fmt(sum(p['people'] for p in parts))}."
        elif s.get('note'):
            tail = ' ' + s['note']
        lines.append(f"{s['dimension']}: {items}.{tail}")
    return '\n'.join(lines)
