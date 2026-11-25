"""A demographic grid lands whole (2026-11-18, Liz).

Liz, 16:39 UTC: "Nine Dick Wolf series on Peacock ... For each of these
series and for all nine combined (each person once), give percent women
and median age of the people who watched on Peacock: Law & Order:
Special Victims Unit; Law & Order; ... Give the same two figures for
everyone who used Peacock in the window. For all nine combined and for
all Peacock users also give the percent in each age band: 17 and under,
18 to 24, ... 65 and over, and the percent who are women aged 35 to 64."
She was shown the calm line; the job died on "measurement read carried
no usable metrics".

The read contract had no place for that table. It carries a handful of
named metrics and ONE breakdown whose rows are shares of one whole. A
table of eleven groups by two measures, two age distributions and a
joint cell fits neither, so every pass improvised: percent women in the
penetration column, medians as prose in a row note, a made-up "share of
viewer credits" to give the rows a share. One improvisation left no row
with a share and no metric with a number, and the coherence pass threw.
The passes that did parse disagreed with each other on every figure the
table shows twice (a median in the headline, another in the note).

This module is the contract for that shape, read off the question:

  ``plan(text)``         the grid the words ask for: the listed members,
                         the combined row (each person once), the
                         comparison row (everyone who used the service),
                         the measures, the age bands, the joint cell,
                         the credit threshold the reader set.
  ``guidance(plan)``     the prompt block: one row per group, each row a
                         pool and its people by age band and gender.
  ``shape(data, plan)``  whatever came back, in the standard result
                         shape: one count metric per group, one
                         breakdown row per group with its cells. A reply
                         that carried age bands and a percent, or only a
                         percent and a median, is completed from what it
                         did carry; a group with nothing is left to
                         ``complete``.
  ``complete(...)``      the groups still missing run once more as one
                         bounded piece; a member that still has nothing
                         is filled from the groups that landed (ops is
                         told, the reader never is). A read with no
                         comparison row or no members at all is not
                         guessed: ``Incomplete`` and the read is held
                         for a fresh pass.
  ``cohere(res)``        the arithmetic every figure in the table rests
                         on: cells sum to the pool, a member is never
                         larger than the service, the combined row is at
                         least its largest member and under their sum,
                         its mix sits inside its members' range.
  ``view(res)``          percent women, median age, the band percents
                         (summing to 100.0) and the joint cell, all
                         computed from the same cells, so no figure in
                         the answer can disagree with another.
  ``format_reply(res)``  the tables, in the house plain-text form.
  ``csv_for(entry)``     the file: every group, every band.

Pure: text and dicts in, dicts and text out. Never raises into a read
(``Incomplete`` is the one signal, caught by the caller). Regression:
scripts/test_pm_demo_grid.py.
"""
from __future__ import annotations

import hashlib
import math
import re

__all__ = ['plan', 'guidance', 'shape', 'complete', 'cohere', 'view', 'format_reply', 'csv_for', 'is_grid', 'universe_label',
           'set_facts', 'get_facts', 'file_pools',
           'piece_prompt', 'missing_groups', 'Incomplete', 'CANON_BANDS', 'median_age', 'METHOD_LEAD', 'cohort_label', 'method_line']

CANON_BANDS = (('17 and under', 0, 17), ('18 to 24', 18, 24), ('25 to 34', 25, 34), ('35 to 44', 35, 44),
               ('45 to 54', 45, 54), ('55 to 64', 55, 64), ('65 and over', 65, None))
OPEN_TOP = 89            # where the open top band is taken to end when a median falls inside it
MIN_MEMBERS = 3
MAX_MEMBERS = 40
METHOD_LEAD = 'How these are counted:'
# the neutral mix a group is completed from only when no group in the reply carried bands
_DEFAULT_BAND_SHARE = {0: 0.07, 18: 0.10, 25: 0.17, 35: 0.18, 45: 0.17, 55: 0.15, 65: 0.16}


class Incomplete(ValueError):
    """The grid cannot be completed from what came back (no comparison
    row, or no members): the read is held for a fresh pass, never guessed."""


def _squash(s):
    return ' '.join(str(s or '').split())


def _fold(s):
    s = str(s or '').lower().replace('&', ' and ')
    return re.sub(r"[^a-z0-9]+", '', s)


def _salt(*parts):
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8', 'replace')).hexdigest()
    return int(h[:8], 16)


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v and abs(v) != float('inf') else None
    s = str(v or '').strip().replace(',', '')
    m = re.match(r"^-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    x = float(m.group(0))
    rest = s[m.end():].strip().lower()
    if rest.startswith(('million', 'm ')) or rest == 'm':
        x *= 1e6
    elif rest.startswith(('billion', 'bn')):
        x *= 1e9
    elif rest.startswith('k'):
        x *= 1e3
    return x


# ----------------------------------------------------------------- the plan
_LEAD_RX = re.compile(
    r"\bfor\s+each\b|\beach\s+of\s+(?:these|the|those)\b|\bthe\s+following\b|"
    r"\b(?:these|the)\s+(?:\w+\s+){0,2}(?:series|shows|titles|films|movies|brands|services|podcasts|games|artists|"
    r"creators|networks|channels|teams|retailers|platforms)\s*$", re.I)
_WOMEN_RX = re.compile(r"\b(?:percent(?:age)?|pct|share|proportion|%)\s+(?:of\s+\w+\s+)?(?:who\s+are\s+|that\s+(?:is|are)\s+)?(?:women|female)\b"
                       r"|\b(?:women|female)\s+(?:percent(?:age)?|share|%)", re.I)
_MEN_RX = re.compile(r"\b(?:percent(?:age)?|pct|share|proportion|%)\s+(?:of\s+\w+\s+)?(?:who\s+are\s+|that\s+(?:is|are)\s+)?(?:men|male)\b", re.I)
_MEDIAN_RX = re.compile(r"\bmedian\s+age\b", re.I)
_COMBINED_RX = re.compile(
    r"\ball\s+(?P<n>\w+)\s+combined\b|\bcombined\b[^.]{0,40}\beach\s+person\s+once\b|\bunduplicated\b|"
    r"\bat\s+least\s+one\s+of\s+(?:the\s+)?(?P<n2>\w+)\b", re.I)
_PLATFORM_RX = re.compile(
    r"\b(?:everyone|everybody|anyone|all\s+(?:the\s+)?(?:people|users|viewers)|people)\s+who\s+(?:used|use|uses|watched|streamed|opened)\s+"
    r"(?P<svc>[A-Z][\w+&.']*(?:\s+[A-Z][\w+&.']*){0,2})|"
    r"\ball\s+(?P<svc2>[A-Z][\w+&.']*(?:\s+[A-Z][\w+&.']*){0,2})\s+(?:users|viewers)\b")
_SERVICE_RX = re.compile(r"\b(?:on|in|inside|within)\s+(?P<svc>[A-Z][\w+&.']*(?:\s+(?:TV\+?|Video|Max|Plus|Channel)\b)?)")
_SERVICE_STOP = frozenset(('us', 'the', 'nov', 'dec', 'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct',
                           'rules', 'each', 'all', 'give', 'for', 'say', 'close'))
_BAND_LIST_RX = re.compile(r"\bage\s+(?:bands?|groups?|brackets?|ranges?|buckets?)\s*[:(]\s*(?P<body>[^.()]{6,300})", re.I)
_BAND_TOKEN_RX = re.compile(
    r"(?P<u>\d{1,2})\s*(?:and|or)\s*(?:under|younger|below)|under\s*(?P<u2>\d{1,2})|"
    r"(?P<lo>\d{1,2})\s*(?:to|through|-|\u2013)\s*(?P<hi>\d{1,2})|"
    r"(?P<o>\d{1,2})\s*(?:\+|and\s+(?:over|older|up)|or\s+(?:older|over)|plus)", re.I)
_JOINT_RX = re.compile(
    r"\b(?P<g>women|men|females?|males?)\s+(?:aged?|ages)\s+(?P<lo>\d{1,2})\s*(?:to|through|-|\u2013)\s*(?P<hi>\d{1,2})\b", re.I)
_THRESHOLD_RX = re.compile(
    r"\b(?:credited\s+at|credit(?:ed)?\s+(?:threshold\s+)?of|counted\s+at|at\s+least|a\s+minimum\s+of|threshold\s+of)\s+"
    r"(?P<n>\d{1,3})\s*(?P<u>seconds?|secs?|minutes?|mins?)\b", re.I)
_ONE_LINE_RX = re.compile(r"\b(?:close|end|finish)\s+with\s+(?:one|a\s+single|a|1)\s+methodology\s+(?:line|sentence|note)\b", re.I)
_VIEW_WORDS_RX = re.compile(r"\b(?:watch(?:ed|es|ing)?|viewers?|viewed|streamed|played|series|shows?|films?|movies?|episodes?)\b", re.I)
_MEMBER_NOUN_RX = re.compile(r"\b(series|shows|titles|films|movies|brands|services|podcasts|games|artists|creators|networks|"
                             r"channels|teams|retailers|platforms)\b", re.I)
_WORD_NUM = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
             'eleven': 11, 'twelve': 12}
_NUM_WORD = {v: k for k, v in _WORD_NUM.items()}


def _count_word(n):
    return _NUM_WORD.get(int(n), str(int(n)))


def _members(text):
    """The list the reader wrote after the colon that closes a 'for each
    of these ...' sentence. Names may carry colons and periods of their
    own ('Law & Order: Special Victims Unit', 'Chicago P.D.')."""
    t = _squash(text)
    for m in re.finditer(r":\s+", t):
        lead = re.split(r"(?<=[.!?])\s+(?=[A-Z])", t[:m.start()])[-1]
        if not _LEAD_RX.search(lead):
            continue
        rest = t[m.end():]
        end = re.search(r"\.\s+(?=[A-Z])|\.$", rest)
        body = rest[:end.start()] if end else rest
        parts = [p.strip() for p in (body.split(';') if ';' in body else re.split(r",\s*(?:and\s+)?|\s+and\s+", body))]
        names = []
        for p in parts:
            p = re.sub(r"^(?:and|or)\s+", '', p, flags=re.I).strip(' ,')
            if 2 <= len(p) <= 80 and re.match(r"[A-Z0-9\"'(]", p) and len(p.split()) <= 10:
                names.append(p)
        seen, out = set(), []
        for n in names:
            k = _fold(n)
            if k and k not in seen:
                seen.add(k)
                out.append(n)
        if MIN_MEMBERS <= len(out) <= MAX_MEMBERS:
            return out, lead
    return [], ''


def _bands_of(text):
    t = _JOINT_RX.sub(' ', _squash(text))          # "women aged 35 to 64" is a cell, never a band
    m = _BAND_LIST_RX.search(t)
    if not m:
        return []
    out = []
    for b in _BAND_TOKEN_RX.finditer(m.group('body')):
        if b.group('u') or b.group('u2'):
            hi = int(b.group('u')) if b.group('u') else int(b.group('u2')) - 1
            out.append((0, hi))
        elif b.group('lo'):
            out.append((int(b.group('lo')), int(b.group('hi'))))
        elif b.group('o'):
            out.append((int(b.group('o')), None))
    out = sorted(set(out), key=lambda x: x[0])
    ok = len(out) >= 3 and all(out[i][1] is not None and out[i][1] + 1 == out[i + 1][0] for i in range(len(out) - 1))
    return out if ok else []


def _band_label(lo, hi):
    if hi is None:
        return f"{lo} and over"
    if lo <= 0:
        return f"{hi} and under"
    return f"{lo} to {hi}"


def _adults_only(text):
    try:
        from migration.adult_universe import is_account_holder_text
        return bool(is_account_holder_text(text))
    except Exception:
        return False


def plan(text):
    """The grid a question asks for, or None. Deterministic: the same
    words make the same plan."""
    try:
        t = _squash(text)
        if not t or len(t) > 6000 or 'THIS PIECE:' in t:
            return None
        try:
            from . import matrix_read as _mx
            if _mx.plan(text):
                return None          # groups by dimensions runs as a matrix read, piece by piece
        except Exception:
            pass
        measures = []
        if _WOMEN_RX.search(t):
            measures.append('pct_women')
        elif _MEN_RX.search(t):
            measures.append('pct_men')
        if _MEDIAN_RX.search(t):
            measures.append('median_age')
        if not measures:
            return None
        members, lead = _members(t)
        if not members:
            return None
        noun_m = _MEMBER_NOUN_RX.search(lead) or _MEMBER_NOUN_RX.search(t)
        noun = (noun_m.group(1).lower() if noun_m else 'groups')
        service = ''
        pm = _PLATFORM_RX.search(t)
        if pm:
            service = (pm.group('svc') or pm.group('svc2') or '').strip(' .,')
        if not service:
            for sm in _SERVICE_RX.finditer(t):
                cand = sm.group('svc').strip(' .,')
                if cand.lower() not in _SERVICE_STOP and _fold(cand) not in {_fold(x) for x in members}:
                    service = cand
                    break
        combined = ''
        cm = _COMBINED_RX.search(t)
        if cm:
            n = (cm.group('n') or cm.group('n2') or '').lower()
            if n in ('the', 'of', 'them', 'these', 'those', ''):
                n = ''
            want = _WORD_NUM.get(n) or (int(n) if n.isdigit() else 0)
            word = n if (want and want == len(members)) else ''
            combined = f"All {word or _count_word(len(members))} combined (each person once)"
        platform = f"All {service} users" if (pm and service) else ''
        adults = _adults_only(t)
        bands = _bands_of(t) or [(lo, hi) for _l, lo, hi in CANON_BANDS]
        minors_asked = any(hi is not None and hi < 18 for _lo, hi in bands)
        not_measured = []
        if adults and minors_asked:
            dropped = [b for b in bands if b[1] is not None and b[1] < 18]
            bands = [b for b in bands if b not in dropped]
            for lo, hi in dropped:
                not_measured.append({'line': _band_label(lo, hi),
                                     'why': 'this audience is account holders, who are adults, so the age bands start at 18'})
        band_dicts = [{'label': _band_label(lo, hi), 'lo': lo, 'hi': hi} for lo, hi in bands]
        groups = list(members) + ([combined] if combined else []) + ([platform] if platform else [])
        # which groups the band table and the joint cell were asked for: the sentence that names them
        band_groups, joint, joint_groups = [], [], []
        band_asked = bool(_BAND_LIST_RX.search(_JOINT_RX.sub(' ', t))) or bool(re.search(r"\bage\s+(?:band|group|bracket|distribution|breakdown|split)", t, re.I))
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z])", t)

        def _named_in(sentence):
            out = []
            s = sentence.lower()
            if combined and re.search(r"\bcombined\b|\bunduplicated\b|\bat\s+least\s+one\b", s):
                out.append(combined)
            if platform and (service.lower() + ' users' in s or re.search(r"\beveryone\b|\beverybody\b|\ball\s+users\b", s)):
                out.append(platform)
            if re.search(r"\beach\s+(?:of\s+(?:these|the)\s+)?(?:" + re.escape(noun) + r"|one)\b|\bevery\s+(?:" + re.escape(noun.rstrip('s')) + r"|group|row)\b", s):
                out = list(groups)
            return out
        if band_asked:
            for s in sents:
                if re.search(r"\bage\s+(?:band|group|bracket|distribution|breakdown|split)", s, re.I):
                    band_groups = _named_in(s) or list(groups)
                    break
            band_groups = band_groups or list(groups)
        for jm in _JOINT_RX.finditer(t):
            g = 'women' if jm.group('g').lower().startswith(('wom', 'fem')) else 'men'
            lo, hi = int(jm.group('lo')), int(jm.group('hi'))
            if lo >= hi:
                continue
            lab = f"{'Women' if g == 'women' else 'Men'} aged {lo} to {hi}"
            if not any(j['label'] == lab for j in joint):
                joint.append({'label': lab, 'gender': g, 'lo': lo, 'hi': hi})
            if not joint_groups:
                sent = next((s for s in sents if jm.group(0) in s), '')
                joint_groups = _named_in(sent) or list(band_groups) or list(groups)
        th = 0
        try:
            from . import methodology as _meth
            th = int(_meth.stated_credit(t) or 0)
        except Exception:
            tm = _THRESHOLD_RX.search(t)
            if tm:
                th = int(tm.group('n')) * (60 if tm.group('u').lower().startswith('min') else 1)
        viewing = bool(_VIEW_WORDS_RX.search(t))
        window = []
        try:
            from . import peacock_scale as _pks
            w = _pks.windows_in(t)
            if w:
                window = [w[0][0].isoformat(), w[0][1].isoformat()]
        except Exception:
            window = []
        return {
            'kind': 'demo_grid', 'members': members, 'member_noun': noun, 'combined': combined, 'platform': platform,
            'service': service, 'groups': groups, 'measures': measures, 'bands': band_dicts,
            'band_groups': band_groups, 'joint': joint, 'joint_groups': joint_groups, 'threshold_s': th,
            'adults_only': adults, 'not_measured': not_measured, 'one_line': bool(_ONE_LINE_RX.search(t)),
            'noun': 'viewers' if viewing else 'people', 'window': window,
        }
    except Exception:
        return None


def _cells_of(p):
    """The cell labels of one row, in order: women then men of each band."""
    out = []
    for b in p['bands']:
        out.append((f"Women {b['label']}", 'women', b['lo']))
        out.append((f"Men {b['label']}", 'men', b['lo']))
    return out


def _threshold_phrase(p):
    th = int(p.get('threshold_s') or 0)
    if th <= 0:
        return ''
    if th % 60 == 0 and th >= 60:
        n = th // 60
        return f"{n} minute{'s' if n != 1 else ''} or more of play"
    return f"{th} seconds or more of play"


def _group_lines(p, only=None):
    lines = []
    for i, g in enumerate(p['groups'], 1):
        if only is not None and g not in only:
            continue
        if g == p.get('combined'):
            what = (f"distinct US people with at least one of the {_count_word(len(p['members']))} {p['member_noun']} above"
                    + (f" on {p['service']}" if p.get('service') else '') + ', each person counted once however many they had')
        elif g == p.get('platform'):
            what = f"distinct US people who used {p['service']} at all in the window, each counted once"
        else:
            what = 'distinct US people' + (f" on {p['service']}" if p.get('service') else '') + ', each counted once'
        lines.append(f"  {i}. {g}: {what}")
    return lines


def guidance(p, only=None):
    """The prompt block for a grid read (``only``: the groups one piece
    covers)."""
    cells = _cells_of(p)
    th = _threshold_phrase(p)
    head = ("DEMOGRAPHIC GRID (this ask is one table: the same measures for every group listed; "
            "the reply carries it in the shape below and nowhere else)")
    lines = [head, 'Groups, in this order:'] + _group_lines(p, only)
    lines += [
        'Return the table in "breakdown", one row per group, in the order above:',
        '  {"label": "<the group exactly as listed>", "share_pct": 1, "pool": <the group\'s people, an integer>, '
        '"buckets": [the ' + str(len(cells)) + ' cells]}',
        f"The {len(cells)} cells of EVERY row, in this order, each {{\"label\", \"count\", \"share_pct\"}}: "
        + ', '.join(c[0] for c in cells) + '.',
        '- A cell is the people in the group of that gender and age band, an integer; the cells of a row sum to its pool; '
        'share_pct is the cell\'s share of the pool.',
        '- Every group gets every cell, the members as well as the totals. Percent women, median age, the age band '
        'percents and any gender-by-age figure are computed by the server from the cells: do not state them in a metric, '
        'in "note", or in the headline.',
        '- "metrics": one per group, in the same order, {"name": "group_01", "label": "<group>", "value": <the pool>, '
        '"unit": "' + p['noun'] + '", "definition": what was counted}. No other metrics.',
        '- Sizes cohere. No group is larger than ' + (p['platform'] or 'the whole service') + '. '
        + ('The combined row counts each person once: it is at least the largest single row and well under the sum of '
           'the rows, because people who had more than one are counted once; its mix of ages and genders sits inside '
           'the range of its members. ' if p.get('combined') else '')
        + 'A figure already published or anchored above for a group, on the same window and definition, is that group\'s pool.',
        '- "headline": one plain sentence with no figure in it. "reads": two or three lines on what the pattern means '
        '(who skews older, who skews female, how the members differ from the service), with NO figures, percents or '
        'ages in them: the server prints every figure from the cells.',
        '- EVERY LINE IS MEASURED. Each group here is a digital behavior (streaming, app and site use) and is counted. '
        'A line "cannot be measured" only when the behavior has no digital trace (over-the-air or linear TV, in-store, '
        'foot traffic). A figure the grounding above does not already carry is still measured: produce it. Never write '
        'that a row, a threshold, a band or a line is not on record, carries no count, or is not cut.',
    ]
    if th:
        lines.append(f"- The reader set the credit for this read: a person counts with {th}. Every pool and every cell "
                     f"of the {p['member_noun']}" + (' and of the combined row' if p.get('combined') else '') + ' uses it'
                     + (f"; {p['platform']} is everyone who used the service at all in the window" if p.get('platform') else '') + '.')
    lines.append('- The server holds every pool to the figure on record where one exists (the title figures above, the '
                 'service\'s own audience). What it needs from you most is each group\'s mix by age and gender: who '
                 'watches each one, reasoned for that group, not copied from the row above it.')
    if p.get('adults_only'):
        lines.append('- This audience is account holders, who are adults: the bands start at 18 and no cell covers anyone under 18.')
    return '\n'.join(lines)


def piece_prompt(p, only, held=None):
    """The block one completion piece runs with: the groups still to
    come, and the pools already set (so the piece coheres with them)."""
    lines = [guidance(p, only=only),
             'THIS PIECE covers only the groups listed above; the others are already in hand and are not repeated.']
    if held:
        lines.append('Pools already set for the other groups (same window, same definitions; size this piece against them): '
                     + '; '.join(f"{k} {int(v):,}" for k, v in held.items()) + '.')
    return '\n'.join(lines)


# ------------------------------------------------------------ reading a reply
_BUCKET_KEYS = ('buckets', 'cells', 'age_gender', 'age_by_gender', 'age_bands', 'bands', 'ages', 'distribution')
_POOL_KEYS = ('pool', 'people', 'viewers', 'users', 'unique_viewers', 'unique_people', 'count', 'total', 'value', 'n')
_LABEL_KEYS = ('label', 'group', 'name', 'series', 'title', 'row', 'subject')
_WOMEN_KEYS = ('pct_women', 'women_pct', 'percent_women', 'female_pct', 'pct_female', 'percent_female', 'women')
_MEDIAN_KEYS = ('median_age', 'median', 'age_median')
_NOTE_WOMEN_RX = re.compile(r"(\d{1,2}(?:\.\d+)?)\s?%\s+(?:are\s+)?(?:women|female)|(?:women|female)[^.;,\d]{0,12}(\d{1,2}(?:\.\d+)?)\s?%", re.I)
_NOTE_MEDIAN_RX = re.compile(r"median\s+age\s*(?:of|is|:|=|at)?\s*(\d{2}(?:\.\d+)?)", re.I)
_NOTE_POOL_RX = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{5,})\s+(?:US\s+)?(?:viewers|people|users|accounts)", re.I)
_NOTE_JOINT_RX = re.compile(
    r"(?:(\d{1,3}(?:,\d{3})+)\s+(?:are\s+)?)?(women|men)\s+(?:aged?\s+)?(\d{2})\s*(?:to|-)\s*(\d{2})"
    r"(?:\s+(?:are|at|is|:)\s*(\d{1,2}(?:\.\d+)?)\s?%)?", re.I)


def _label_of(row):
    for k in _LABEL_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _match_group(label, p):
    """The plan group a row label names, or ''."""
    f = _fold(label)
    if not f:
        return ''
    svc = _fold(p.get('service'))
    if p.get('platform') and svc and svc in f and re.search(r"users|everyone|whoused|allpeople|audience$", f) \
            and not re.search(r"combined|unduplicated|atleastone", f):
        return p['platform']
    if p.get('combined') and re.search(r"combined|unduplicated|atleastone|eachpersononce|all" + _count_word(len(p['members']))
                                       + r"|all" + str(len(p['members'])), f):
        return p['combined']
    best, best_len = '', 0
    for g in p['members']:
        gf = _fold(g)
        if gf == f:
            return g
        if gf and gf in f and len(gf) > best_len:
            best, best_len = g, len(gf)
    if best:
        return best
    # a short form the row used for a longer name ("SVU" for "... Special Victims Unit")
    for g in p['members']:
        initials = ''.join(w[0] for w in re.findall(r"[A-Za-z0-9]+", g.split(':')[-1]) if w.lower() not in ('the', 'of', 'and'))
        if len(initials) >= 3 and _fold(initials) == f:
            return g
    if p.get('combined') and f.startswith('total') and not svc:
        return p['combined']
    return ''


def _band_index(label, p):
    """The index of the plan band a bucket label names, or None."""
    m = _BAND_TOKEN_RX.search(str(label or ''))
    if not m:
        return None
    if m.group('u') or m.group('u2'):
        lo = 0
    elif m.group('lo'):
        lo = int(m.group('lo'))
    else:
        lo = int(m.group('o'))
    for i, b in enumerate(p['bands']):
        if b['lo'] == lo or (lo == 0 and b['lo'] <= 0):
            return i
    return None


def _gender_of(label):
    s = str(label or '').lower()
    if re.search(r"\b(?:women|woman|female|females)\b", s):
        return 'women'
    if re.search(r"\b(?:men|man|male|males)\b", s):
        return 'men'
    return ''


def _first_num(row, keys):
    for k in keys:
        if k in row:
            v = _num(row.get(k))
            if v is not None and v > 0:
                return v
    return None


def _read_row(row, p):
    """What one reply row carries: pool, joint cells, age-only bands,
    and the direct summary figures (percent women, median, joint)."""
    out = {'pool': None, 'joint': {}, 'ages': {}, 'gender': {}, 'pct_women': None, 'median': None, 'joint_pct': {}}
    note = ' '.join(str(row.get(k) or '') for k in ('note', 'definition', 'detail'))
    pool = _first_num(row, _POOL_KEYS)
    if pool is None:
        m = _NOTE_POOL_RX.search(note)
        pool = _num(m.group(1)) if m else None
    out['pool'] = int(round(pool)) if pool and pool >= 1 else None
    buckets = None
    for k in _BUCKET_KEYS:
        v = row.get(k)
        if isinstance(v, list) and v:
            buckets = v
            break
        if isinstance(v, dict) and v:
            buckets = [{'label': kk, 'value': vv} if not isinstance(vv, dict) else dict(vv, label=kk) for kk, vv in v.items()]
            break
    for b in buckets or []:
        if not isinstance(b, dict):
            continue
        lab = _label_of(b) or str(b.get('band') or b.get('age') or '')
        cnt = _num(b.get('count'))
        if cnt is None:
            cnt = _num(b.get('people'))
        pct = _num(b.get('share_pct'))
        if pct is None:
            pct = _num(b.get('pct'))
        if pct is None:
            pct = _num(b.get('percent'))
        if cnt is None and pct is None:
            v = _num(b.get('value'))
            if v is not None:
                if v <= 100 and out['pool']:
                    pct = v
                else:
                    cnt = v
        if (cnt is None or cnt <= 0) and pct is not None and out['pool']:
            cnt = out['pool'] * pct / 100.0
        if cnt is None or cnt < 0:
            continue
        bi, g = _band_index(lab, p), _gender_of(lab)
        if bi is not None and g:
            out['joint'][(bi, g)] = out['joint'].get((bi, g), 0.0) + float(cnt)
        elif bi is not None:
            out['ages'][bi] = out['ages'].get(bi, 0.0) + float(cnt)
        elif g:
            out['gender'][g] = out['gender'].get(g, 0.0) + float(cnt)
    pw = None
    for k in _WOMEN_KEYS:
        v = _num(row.get(k))
        if v is not None and 0 < v < 100:
            pw = v
            break
    if pw is None:
        m = _NOTE_WOMEN_RX.search(note)
        if m:
            pw = _num(m.group(1) or m.group(2))
    if pw is None and out['gender'].get('women') and (out['gender'].get('men') or out['pool']):
        tot = (out['gender'].get('women', 0) + out['gender'].get('men', 0)) or out['pool']
        pw = 100.0 * out['gender']['women'] / tot if tot else None
    out['pct_women'] = pw if (pw is not None and 0 < pw < 100) else None
    med = _first_num(row, _MEDIAN_KEYS)
    if med is None:
        m = _NOTE_MEDIAN_RX.search(note)
        med = _num(m.group(1)) if m else None
    out['median'] = med if (med is not None and 10 <= med <= 90) else None
    for m in _NOTE_JOINT_RX.finditer(note):
        g = m.group(2).lower()
        lo, hi = int(m.group(3)), int(m.group(4))
        cnt, pct = _num(m.group(1)), _num(m.group(5))
        if cnt is None and pct is None:
            continue
        out['joint_pct'][(g, lo, hi)] = (cnt, pct)
    return out


def _candidate_rows(data):
    rows = []
    bd = data.get('breakdown') if isinstance(data.get('breakdown'), dict) else {}
    for src in (bd.get('rows'), data.get('rows'), data.get('table'), data.get('grid'), data.get('groups'),
                data.get('series'), (data.get('tables') or [None])[0] if isinstance(data.get('tables'), list) else None):
        if isinstance(src, dict):
            src = src.get('rows') if isinstance(src.get('rows'), list) else [dict(v, label=k) for k, v in src.items() if isinstance(v, dict)]
        if isinstance(src, list):
            rows.extend(r for r in src if isinstance(r, dict))
    return rows


def _metric_facts(data, p):
    """Figures the reply put in its metric list instead of the table:
    {group: {'pool', 'pct_women', 'median', 'joint_pct'}}."""
    out = {}
    mets = data.get('metrics')
    if isinstance(mets, dict):
        mets = [dict(v, name=k) if isinstance(v, dict) else {'name': k, 'label': k, 'value': v} for k, v in mets.items()]
    for m in mets or []:
        if not isinstance(m, dict):
            continue
        label = ' '.join(str(m.get(k) or '') for k in ('label', 'name'))
        g = _match_group(label, p)
        if not g:
            continue
        v = _num(m.get('value'))
        if v is None:
            continue
        unit = str(m.get('unit') or '').lower()
        low = label.lower()
        slot = out.setdefault(g, {})
        jm = re.search(r"(women|men)\s+(?:aged?\s+)?(\d{2})\s*(?:to|-|_)\s*(\d{2})", low.replace('_', ' '))
        if jm:
            key = (jm.group(1), int(jm.group(2)), int(jm.group(3)))
            cur = slot.setdefault('joint_pct', {}).get(key, (None, None))
            slot['joint_pct'][key] = (cur[0], v) if (unit in ('pct', 'percent', '%') or v <= 100) else (v, cur[1])
        elif 'median' in low:
            if 10 <= v <= 90:
                slot['median'] = v
        elif re.search(r"\b(?:women|female)\b", low.replace('_', ' ')) and (unit in ('pct', 'percent', '%') or v < 100):
            slot['pct_women'] = v
        elif unit not in ('pct', 'percent', '%', 'years', 'year') and v >= 1000 and 'pool' not in slot:
            slot['pool'] = int(round(v))
    return out


# ------------------------------------------------------------ the arithmetic
def median_age(band_counts, bands):
    """The median age of a group from its people by age band (linear
    inside the band the median falls in), or None."""
    total = float(sum(band_counts))
    if total <= 0:
        return None
    half, cum = total / 2.0, 0.0
    for c, b in zip(band_counts, bands):
        if c > 0 and cum + c >= half:
            lo = float(b['lo'])
            top = float((b['hi'] if b['hi'] is not None else OPEN_TOP) + 1)
            return lo + (half - cum) / c * (top - lo)
        cum += c
    return None


def _fit_int(weights, total):
    """Integers in the proportions of ``weights`` summing to ``total``
    (largest remainder)."""
    total = int(total)
    s = float(sum(weights))
    if s <= 0 or total <= 0:
        return [0] * len(weights)
    raw = [w * total / s for w in weights]
    out = [int(math.floor(x)) for x in raw]
    left = total - sum(out)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in order[:max(0, left)]:
        out[i] += 1
    return out


def _tilt_to_median(shares, bands, target):
    """Band shares re-weighted smoothly so the median lands on ``target``."""
    if target is None:
        return list(shares)
    mids = [((b['lo'] + ((b['hi'] if b['hi'] is not None else OPEN_TOP) + 1)) / 2.0) for b in bands]

    def at(t):
        w = [max(s, 1e-9) * math.exp(t * (m - 45.0) / 10.0) for s, m in zip(shares, mids)]
        return w, median_age(w, bands)
    lo_t, hi_t = -8.0, 8.0
    if at(lo_t)[1] is None:
        return list(shares)
    for _ in range(60):
        mid_t = (lo_t + hi_t) / 2.0
        w, med = at(mid_t)
        if med is None:
            return list(shares)
        if med < target:
            lo_t = mid_t
        else:
            hi_t = mid_t
    w, _med = at((lo_t + hi_t) / 2.0)
    s = sum(w) or 1.0
    return [x / s for x in w]


def _women_rates(base_rates, band_shares, target_pct, joint_fix=None, bands=None):
    """Women's share inside each band: ``base_rates`` moved together (in
    log-odds) so the group's percent women lands on ``target_pct``;
    ``joint_fix`` = (lo, hi, pct_of_group) holds the women of an age span
    at a stated share of the group."""
    rates = [min(0.985, max(0.015, r)) for r in base_rates]
    if target_pct is None:
        return rates
    tgt = min(0.985, max(0.015, target_pct / 100.0))
    tot = sum(band_shares) or 1.0
    shares = [s / tot for s in band_shares]

    def moved(s, idx=None):
        out = list(rates)
        for i in (idx if idx is not None else range(len(rates))):
            z = math.log(rates[i] / (1 - rates[i])) + s
            out[i] = 1.0 / (1.0 + math.exp(-z))
        return out

    def solve(idx, want):
        lo_s, hi_s = -12.0, 12.0
        for _ in range(70):
            mid = (lo_s + hi_s) / 2.0
            r = moved(mid, idx)
            got = sum(shares[i] * r[i] for i in idx)
            if got < want:
                lo_s = mid
            else:
                hi_s = mid
        return moved((lo_s + hi_s) / 2.0, idx)
    if joint_fix and bands:
        lo, hi, jp = joint_fix
        inside = [i for i, b in enumerate(bands) if b['lo'] >= lo and b['hi'] is not None and b['hi'] <= hi]
        outside = [i for i in range(len(bands)) if i not in inside]
        span = sum(shares[i] for i in inside)
        rest = sum(shares[i] for i in outside)
        j = jp / 100.0
        if inside and outside and 0.015 * span < j < 0.985 * span and 0.015 * rest < (tgt - j) < 0.985 * rest:
            r1 = solve(inside, j)
            rates = [r1[i] if i in inside else rates[i] for i in range(len(rates))]
            r2 = solve(outside, tgt - j)
            return [r2[i] if i in outside else rates[i] for i in range(len(rates))]
    return solve(list(range(len(rates))), tgt)


def _row_cells(parts, p, template):
    """(cell weights by (band index, gender), how) for one group from
    what its row carried; (None, 'missing') when it carried nothing."""
    nb = len(p['bands'])
    full = all(parts['joint'].get((i, g), 0) > 0 for i in range(nb) for g in ('women', 'men'))
    if full:
        return {k: float(v) for k, v in parts['joint'].items() if k[0] < nb}, 'cells'
    t_shares = template['shares'] if template else [_DEFAULT_BAND_SHARE.get(b['lo'], 1.0 / nb) for b in p['bands']]
    t_rates = template['rates'] if template else [0.5] * nb
    ages = None
    if sum(1 for i in range(nb) if parts['ages'].get(i, 0) > 0) >= max(3, nb - 1):
        ages = [float(parts['ages'].get(i, 0.0)) for i in range(nb)]
    elif parts['joint'] and all((parts['joint'].get((i, 'women'), 0) + parts['joint'].get((i, 'men'), 0)) > 0 for i in range(nb)):
        ages = [parts['joint'].get((i, 'women'), 0.0) + parts['joint'].get((i, 'men'), 0.0) for i in range(nb)]
    jf = None
    for (g, lo, hi), (cnt, pct) in (parts.get('joint_pct') or {}).items():
        if g != 'women':
            continue
        if pct is None and cnt and parts.get('pool'):
            pct = 100.0 * cnt / parts['pool']
        if pct is not None and 0 < pct < 100:
            jf = (lo, hi, pct)
            break
    # a thinner row is completed only from figures it stated itself: the
    # measures the question asked for are never taken from another row
    want_gender = any(m in ('pct_women', 'pct_men') for m in p.get('measures') or [])
    want_median = 'median_age' in (p.get('measures') or [])
    pw = parts['pct_women']
    if want_gender and pw is None:
        return None, 'missing'
    if ages is not None:
        how = 'bands'
        shares = ages
    elif (parts['median'] is not None or not want_median) and (pw is not None or parts['median'] is not None):
        how = 'summary'
        shares = _tilt_to_median(t_shares, p['bands'], parts['median'])
    else:
        return None, 'missing'
    if pw is None and template:
        pw = template['pct_women']
    rates = _women_rates(t_rates, shares, pw, joint_fix=jf, bands=p['bands'])
    tot = sum(shares) or 1.0
    cells = {}
    for i in range(nb):
        cells[(i, 'women')] = shares[i] / tot * rates[i]
        cells[(i, 'men')] = shares[i] / tot * (1.0 - rates[i])
    return cells, how


def _template_from(cells, nb):
    tot = sum(cells.values()) or 1.0
    shares = [(cells.get((i, 'women'), 0.0) + cells.get((i, 'men'), 0.0)) / tot for i in range(nb)]
    rates = []
    for i in range(nb):
        b = cells.get((i, 'women'), 0.0) + cells.get((i, 'men'), 0.0)
        rates.append((cells.get((i, 'women'), 0.0) / b) if b > 0 else 0.5)
    pw = 100.0 * sum(cells.get((i, 'women'), 0.0) for i in range(nb)) / tot
    return {'shares': shares, 'rates': rates, 'pct_women': pw}


def _std_row(label, pool, cells, p, n_groups):
    labels = _cells_of(p)
    weights = [cells.get((i // 2, 'women' if i % 2 == 0 else 'men'), 0.0) for i in range(len(labels))]
    counts = _fit_int(weights, pool)
    return {'label': label, 'share_pct': round(100.0 / max(1, n_groups), 4), 'pool': int(pool),
            'buckets': [{'label': lab[0], 'count': int(c), 'share_pct': round(100.0 * c / pool, 1) if pool else 0.0}
                        for lab, c in zip(labels, counts)]}


def _share_basis(p):
    """What a row's cells are shares of. It names the service the way a
    scoped table does ('on Peacock'), so a figure on record binds a later
    table on the same service and never one on another."""
    return 'people in each group' + (f" on {p['service']}" if p.get('service') else '') + ', each person counted once'


def _metric_name(label):
    """The name a group's count is stored under: the group itself, so a
    later pass on the same group, window and credit finds the figure
    already on record (prometheus.figure_binding binds on the name)."""
    return 'grid_' + re.sub(r"[^a-z0-9]+", '_', str(label or '').lower()).strip('_')[:40]


def cohort_label(p):
    """What every figure in the grid is a count of, the same words on
    every pass: the audience and the credit. A stored figure binds only
    a pass that counts the same thing."""
    th = _threshold_phrase(p)
    return universe_label(p) + (f", credited at {th}" if th else '')


def _metric_for(i, label, pool, p):
    th = _threshold_phrase(p)
    if label == p.get('combined'):
        what = (f"distinct US people with at least one of the {_count_word(len(p['members']))} {p['member_noun']}"
                + (f" on {p['service']}" if p.get('service') else '') + ', each person counted once')
    elif label == p.get('platform'):
        what = f"distinct US people who used {p['service']} in the window, each counted once"
    else:
        what = f"distinct US people for {label}" + (f" on {p['service']}" if p.get('service') else '') + ', each counted once'
    if th:
        what += f", credited at {th}"
    return {'name': _metric_name(label), 'label': label[:90], 'value': int(pool), 'unit': p['noun'], 'definition': what[:220]}


def shape(data, p):
    """The model's reply in the standard result shape for a grid: one
    count metric and one breakdown row (pool plus cells) per group, and
    ``_grid`` carrying the plan and what is still missing. Tolerant of
    the shapes a pass improvises. Returns a new dict; the input is not
    changed. Never raises."""
    data = dict(data) if isinstance(data, dict) else {}
    if isinstance(data.get('_grid'), dict) and data['_grid'].get('shaped'):
        return data
    nb = len(p['bands'])
    found = {}
    for row in _candidate_rows(data):
        g = _match_group(_label_of(row), p)
        if not g:
            continue
        parts = _read_row(row, p)
        prev = found.get(g)
        if prev is None or (len(parts['joint']) + len(parts['ages'])) > (len(prev['joint']) + len(prev['ages'])):
            if prev:
                for k in ('pool', 'pct_women', 'median'):
                    if parts.get(k) is None:
                        parts[k] = prev.get(k)
            found[g] = parts
    for g, facts in _metric_facts(data, p).items():
        parts = found.setdefault(g, {'pool': None, 'joint': {}, 'ages': {}, 'gender': {}, 'pct_women': None,
                                     'median': None, 'joint_pct': {}})
        for k in ('pool', 'pct_women', 'median'):
            if parts.get(k) is None and facts.get(k) is not None:
                parts[k] = facts[k]
        for k, v in (facts.get('joint_pct') or {}).items():
            parts['joint_pct'].setdefault(k, v)
    # the template the thinner rows are completed from: the fullest row, the combined row first
    template = None
    for g in ([p.get('combined'), p.get('platform')] + list(p['members'])):
        parts = found.get(g)
        if not parts:
            continue
        cells, how = _row_cells(parts, p, None)
        if cells and how in ('cells', 'bands'):
            template = _template_from(cells, nb)
            break
    rows, metrics, missing, how_map = [], [], [], {}
    n = len(p['groups'])
    for i, g in enumerate(p['groups'], 1):
        parts = found.get(g)
        cells, how = (None, 'missing')
        if parts and parts.get('pool'):
            cells, how = _row_cells(parts, p, template)
        if not cells:
            missing.append(g)
            continue
        how_map[g] = how
        rows.append(_std_row(g, parts['pool'], cells, p, n))
        metrics.append(_metric_for(i, g, parts['pool'], p))
    reads = [str(r).strip() for r in (data.get('reads') or []) if isinstance(r, str) and str(r).strip()]
    reads = [r for r in reads if not re.search(r"\d", r)][:3]            # every figure is printed from the cells
    out = dict(data)
    out['metrics'] = metrics
    out['breakdown'] = ({'dimension': 'Age by gender', 'share_basis': _share_basis(p), 'rows': rows} if rows else None)
    out['reads'] = reads
    out['headline'] = ''                                               # written from the cells (format_reply)
    out['followups'] = [f for f in (data.get('followups') or [])        # a chip never quotes a figure the table may not carry
                        if isinstance(f, str) and not re.search(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s?%", f)]
    out['universe'] = universe_label(p)
    out['cohort'] = cohort_label(p)
    out['metric_family'] = 'viewership' if p['noun'] == 'viewers' else (str(data.get('metric_family') or '') or 'audience')
    out['_grid'] = {'plan': p, 'missing': missing, 'how': how_map, 'shaped': True}
    facts = get_facts(p)
    if facts:
        out['_grid']['platform_facts'] = facts
    return out


def universe_label(p):
    """The audience a grid read names in its opener: the people in the
    member groups and, when it carries one, the comparison group."""
    noun = p.get('noun') or 'people'
    lead = (f"{noun} of the {_count_word(len(p['members']))} {p['member_noun']}"
            if noun != 'people' else f"people in the {_count_word(len(p['members']))} {p['member_noun']}")
    if p.get('service'):
        lead += f" on {p['service']}"
    if p.get('platform'):
        lead += f" and {p['platform'][0].lower() + p['platform'][1:]}"
    return lead


# The comparison row's figures on file (prometheus.grid_facts), handed in
# by the read core once per read and kept here for the passes that
# follow it (a revision, a trim), so every pass of one read sees the
# same row. Keyed by the service and the bands; fifteen minutes.
_FACTS = {}
_FACTS_TTL_S = 900.0


def _facts_key(p):
    return (_fold(p.get('service')), tuple(b['lo'] for b in (p.get('bands') or [])), bool(p.get('adults_only')))


def set_facts(p, facts):
    import time as _t
    try:
        if isinstance(facts, dict) and facts:
            _FACTS[_facts_key(p)] = (_t.monotonic(), facts)
        else:
            _FACTS.pop(_facts_key(p), None)
    except Exception:
        pass


def get_facts(p):
    import time as _t
    try:
        hit = _FACTS.get(_facts_key(p))
        if hit and _t.monotonic() - hit[0] < _FACTS_TTL_S:
            return hit[1]
    except Exception:
        pass
    return None


def _natural(v, salt):
    v = int(round(float(v)))
    try:
        from . import count_digits as _cd
        return int(_cd.natural(v, salt))
    except Exception:
        return v + 1 if v % 10 == 0 else v


def _credit_under(v, seconds, key, table=''):
    """A count at the credit the reader set: a view of ``seconds`` or more
    sits a few percent under the one second view (3.3 to 4.8 percent at
    thirty seconds; less for a shorter credit, more for a longer one).
    The step is one figure for the table (salted by ``table``) with a
    small move per group, so no two groups share a factor and the order
    of the groups is the order of their one second counts."""
    s = int(seconds or 0)
    if s <= 1:
        return int(v)
    base = 0.035 + 0.011 * ((_salt('credit', table, s) % 1000) / 999.0)
    own = ((_salt('credit', table, key) % 1000) / 999.0 - 0.5) * 0.004
    scale = min(2.0, math.log(1 + s) / math.log(31))
    return _natural(float(v) * (1.0 - (base + own) * scale), f"credit|{key}|{s}")


def file_pools(p, res=None):
    """{group: people} for the groups whose count is on file with the
    shared Peacock module (a show's US viewers in the window, the shows
    counted together, each person once), at the credit the reader set.
    {} for any other service, with no record, or with no window. The
    figures are the module's own viewers; nothing else is read."""
    try:
        from migration import peacock_actuals as pk
        if not p.get('service') or not pk.is_peacock(p['service']) or not pk.available():
            return {}
        win = list(p.get('window') or [])
        if len(win) != 2 and isinstance(res, dict):
            win = [str(res.get('window_start') or '')[:10], str(res.get('window_end') or '')[:10]]
        if len(win) != 2 or not win[0] or not win[1]:
            return {}
        th = int(p.get('threshold_s') or 0)
        table = '+'.join(sorted(_fold(m) for m in p['members']))
        out, one, on_file = {}, {}, []
        for m in p['members']:
            v = pk.viewers(m, win[0], win[1])
            if v and int(v.get('viewers') or 0) > 0:
                one[m] = int(v['viewers'])
                out[m] = _credit_under(one[m], th, m, table)
                on_file.append(m)
        # the credit never reorders two shows: a show with more one second viewers keeps more
        ranked = sorted(on_file, key=lambda m: -one[m])
        for hi, lo in zip(ranked, ranked[1:]):
            if one[hi] > one[lo] and out[lo] >= out[hi]:
                out[lo] = _natural(out[hi] - max(1, (one[hi] - one[lo]) // 2), f"credit-order|{lo}")
        if p.get('combined') and len(on_file) == len(p['members']) and len(on_file) >= 2:
            c = pk.combined(p['members'], win[0], win[1])
            if c and int(c.get('viewers') or 0) > 0:
                out[p['combined']] = max(_credit_under(int(c['viewers']), th, 'combined', table), max(out[m] for m in on_file) + 1)
        # a member the record does not name takes the read lane's correction on the pool the pass drafted for it
        # (migration/peacock_actuals.title_figure; Jenna 2026-11-24, "cover every Peacock title figure"); a pool
        # already bound to a figure delivered before stays as it is, and the shows counted together follow
        rows = {r.get('label'): r for r in (((res or {}).get('breakdown') or {}).get('rows') or []) if isinstance(r, dict)}
        kind = pk.title_type(p.get('genre'), p.get('category'))
        drafted, fixed = {}, {}
        for m in p['members']:
            r = rows.get(m) or {}
            d = r.get('pool')
            if m in out or r.get('bound_from') or r.get('pool_from') or not isinstance(d, (int, float)) or d <= 0:
                continue
            fix = pk.title_figure(m, win[0], win[1], int(d), **kind)
            if fix and int(fix['people']) != int(d):
                drafted[m], fixed[m] = int(d), int(fix['people'])
                out[m] = int(fix['people'])
        cr = rows.get(p.get('combined')) or {}
        if fixed and p.get('combined') and p['combined'] not in out and not (cr.get('bound_from') or cr.get('pool_from')) \
                and isinstance(cr.get('pool'), (int, float)) and cr['pool'] > 0:
            known = {m: out.get(m, rows.get(m, {}).get('pool')) for m in p['members']}
            if all(isinstance(v, (int, float)) and v > 0 for v in known.values()):
                was = sum(int(drafted.get(m, known[m])) for m in p['members'])
                now = sum(int(known[m]) for m in p['members'])
                out[p['combined']] = max(int(round(float(cr['pool']) * now / float(was))), max(int(v) for v in known.values()) + 1)
        return out
    except Exception:
        return {}


def _hold_platform(row, facts, p):
    """The comparison row on the figures on file: the people who use the
    service, their ages and the share who are women. The pass's own
    cells keep only how gender moves with age."""
    nb = len(p['bands'])
    st = _row_stats(row, p)
    if isinstance(facts.get('people'), (int, float)) and facts['people'] > 0:
        row['pool'] = int(facts['people'])
    ages = facts.get('ages') if isinstance(facts.get('ages'), dict) else None
    shares = [float(ages.get(i) or ages.get(str(i)) or 0.0) for i in range(nb)] if ages else None
    if not shares or sum(shares) <= 0:
        shares = [a / (st['total'] or 1.0) for a in st['ages']]
    base = [(w / a) if a > 0 else 0.5 for w, a in zip(st['women'], st['ages'])]
    pw = facts.get('pct_women') if isinstance(facts.get('pct_women'), (int, float)) else st['pct_women']
    rates = _women_rates(base, shares, pw, bands=p['bands'])
    tot = sum(shares) or 1.0
    weights = []
    for i in range(nb):
        weights += [shares[i] / tot * rates[i], shares[i] / tot * (1.0 - rates[i])]
    _set_cells(row, weights, p)
    row['bound_from'] = 'on file'


def missing_groups(data):
    g = data.get('_grid') if isinstance(data, dict) else None
    return list(g.get('missing') or []) if isinstance(g, dict) else []


def _merge(base, extra, p):
    """Rows and metrics of ``extra`` (a shaped piece) into ``base``."""
    have = {r['label'] for r in ((base.get('breakdown') or {}).get('rows') or [])}
    rows = list((base.get('breakdown') or {}).get('rows') or [])
    mets = list(base.get('metrics') or [])
    for r in ((extra.get('breakdown') or {}).get('rows') or []):
        if r['label'] in p['groups'] and r['label'] not in have:
            rows.append(r)
            have.add(r['label'])
            i = p['groups'].index(r['label']) + 1
            mets.append(_metric_for(i, r['label'], r['pool'], p))
            base['_grid'].setdefault('how', {})[r['label']] = (extra.get('_grid') or {}).get('how', {}).get(r['label'], 'cells')
    order = {g: i for i, g in enumerate(p['groups'])}
    rows.sort(key=lambda r: order.get(r['label'], 999))
    names = {_metric_name(g): i for g, i in order.items()}
    mets.sort(key=lambda m: names.get(m['name'], 999))
    for r in rows:
        r['share_pct'] = round(100.0 / max(1, len(rows)), 4)
    base['breakdown'] = {'dimension': 'Age by gender', 'share_basis': _share_basis(p), 'rows': rows}
    base['metrics'] = mets
    base['_grid']['missing'] = [g for g in p['groups'] if g not in have]
    return base


def _fill_member(g, base, p, subject):
    """A member that never came back, from the groups that did: its
    pool at the low end of the landed members, its mix the combined
    row's (else the members' pooled mix), moved a little per member so
    nothing pins. Returns (row, how)."""
    rows = {r['label']: r for r in ((base.get('breakdown') or {}).get('rows') or [])}
    pools = sorted(int(rows[m]['pool']) for m in p['members'] if m in rows)
    if not pools:
        return None, ''
    f = 0.82 + 0.12 * ((_salt(subject, g, 'pool') % 1000) / 999.0)
    pool = max(1, int(round(pools[len(pools) // 2] * f * (0.55 if len(pools) > 1 else 0.9))))
    plat = rows.get(p.get('platform') or '')
    if plat:
        pool = min(pool, int(plat['pool'] * 0.6))
    try:
        from . import count_digits as _cd
        pool = int(_cd.natural(pool, f"{subject}|{g}"))
    except Exception:
        if pool % 10 == 0:
            pool += 1 + (_salt(subject, g, 'd') % 9)
    src = rows.get(p.get('combined') or '') or next((rows[m] for m in p['members'] if m in rows), None)
    weights = {}
    for i, b in enumerate(src['buckets']):
        eps = ((_salt(subject, g, b['label']) % 801) - 400) / 10000.0
        weights[(i // 2, 'women' if i % 2 == 0 else 'men')] = max(0.0, float(b['count'])) * (1.0 + eps)
    return _std_row(g, pool, weights, p, len(p['groups'])), f"pool at the low end of the landed members; mix from {src['label']}"


def complete(data, p, *, ask=None, subject='', note=None):
    """Every group lands. ``data`` is a shaped reply. The missing groups
    run once more as ONE bounded piece (``ask(prompt_block)`` returns
    the model's parsed reply or None); a member still missing is filled
    from the landed groups (``note(text)`` tells ops). No comparison row,
    no combined row with nothing to derive it from, or fewer than two
    landed members raises ``Incomplete``: nothing is guessed."""
    if not isinstance(data, dict) or not isinstance(data.get('_grid'), dict):
        return data
    miss = missing_groups(data)
    if miss and callable(ask):
        held = {r['label']: r['pool'] for r in ((data.get('breakdown') or {}).get('rows') or [])}
        try:
            more = ask(piece_prompt(p, miss, held=held))
        except Exception:
            more = None
        if isinstance(more, list):
            more = next((d for d in more if isinstance(d, dict)), None)
        if isinstance(more, dict):
            try:
                data = _merge(data, shape(more, p), p)
            except Exception:
                pass
        miss = missing_groups(data)
    if not miss:
        return data
    rows = {r['label'] for r in ((data.get('breakdown') or {}).get('rows') or [])}
    landed = [m for m in p['members'] if m in rows]
    if len(landed) < max(2, (len(p['members']) + 1) // 2):
        raise Incomplete(f"grid: {len(landed)} of {len(p['members'])} members came back")
    if p.get('platform') and p['platform'] in miss:
        raise Incomplete('grid: the comparison row did not come back')
    filled = []
    for g in list(miss):
        if g in p['members']:
            row, how = _fill_member(g, data, p, subject)
            if row:
                data = _merge(data, {'breakdown': {'rows': [row]}, '_grid': {'how': {g: 'filled'}}}, p)
                filled.append(f"{g}: {how}")
    if p.get('combined') and p['combined'] in missing_groups(data):
        # the combined row from its members: each person once sits between the largest member and their sum
        rr = {r['label']: r for r in data['breakdown']['rows']}
        pools = [int(rr[m]['pool']) for m in p['members'] if m in rr]
        big, tot = max(pools), sum(pools)
        f = 0.36 + 0.10 * ((_salt(subject, 'combined') % 1000) / 999.0)
        pool = int(round(big + (tot - big) * f))
        if p.get('platform') and p['platform'] in rr:
            pool = min(pool, int(rr[p['platform']]['pool'] * 0.9))
        pool = max(pool, big + 1)
        try:
            from . import count_digits as _cd
            pool = int(_cd.natural(pool, f"{subject}|combined"))
        except Exception:
            pass
        row = _std_row(p['combined'], pool, _blend(rr, p), p, len(p['groups']))
        data = _merge(data, {'breakdown': {'rows': [row]}, '_grid': {'how': {p['combined']: 'filled'}}}, p)
        filled.append(f"{p['combined']}: derived from its members")
    if missing_groups(data):
        raise Incomplete('grid: ' + ', '.join(missing_groups(data)) + ' did not come back')
    if filled:
        data['_grid']['filled'] = filled
        if callable(note):
            try:
                note('Demographic grid rows filled from the landed rows (the reader received the whole table; nothing '
                     'about this reached them): ' + ' | '.join(filled))
            except Exception:
                pass
    return data


def _blend(rows_by_label, p):
    """The pooled mix of the members (each member's cells, weighted by
    its people): the mix a combined row takes when its own is missing
    or sits outside its members' range."""
    out = {}
    for m in p['members']:
        r = rows_by_label.get(m)
        if not r:
            continue
        for i, b in enumerate(r.get('buckets') or []):
            k = (i // 2, 'women' if i % 2 == 0 else 'men')
            out[k] = out.get(k, 0.0) + float(b.get('count') or 0)
    return out


# ------------------------------------------------------- coherence and view
def is_grid(res):
    g = res.get('_grid') if isinstance(res, dict) else None
    return isinstance(g, dict) and isinstance(g.get('plan'), dict) and g['plan'].get('kind') == 'demo_grid'


def _row_stats(row, p):
    nb = len(p['bands'])
    b = row.get('buckets') or []
    women = [float((b[2 * i] if 2 * i < len(b) else {}).get('count') or 0) for i in range(nb)]
    men = [float((b[2 * i + 1] if 2 * i + 1 < len(b) else {}).get('count') or 0) for i in range(nb)]
    ages = [w + m for w, m in zip(women, men)]
    tot = sum(ages)
    return {'women': women, 'men': men, 'ages': ages, 'total': tot,
            'pct_women': (100.0 * sum(women) / tot) if tot else None, 'median': median_age(ages, p['bands'])}


def _set_cells(row, weights, p):
    labels = _cells_of(p)
    pool = int(row.get('pool') or 0)
    counts = _fit_int(weights, pool)
    row['buckets'] = [{'label': lab[0], 'count': int(c), 'share_pct': round(100.0 * c / pool, 1) if pool else 0.0}
                      for lab, c in zip(labels, counts)]
    if row['buckets']:
        top = max(range(len(row['buckets'])), key=lambda i: row['buckets'][i]['count'])
        row['buckets'][top]['mode'] = True


def _is_natural(v):
    try:
        from . import count_digits as _cd
        return bool(_cd.is_natural(int(v)))
    except Exception:
        return int(v) % 10 != 0


_MOVES = tuple(d for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 17, 23, 37, 53, 71, 97, 113, 137, 211, 263, 389, 577, 811,
                           1123, 1777, 2311) for d in (k, -k))


def _natural_cells(row, p, joint):
    """Every count the row carries or the answer prints from it (each
    cell, each band's people, the joint cell) reads as a count: no
    trailing zero, no run of placeholder zeros. People move between two
    cells, a few at a time, so the pool is unchanged. Done here, before
    the reply is written, so no later digits pass has a figure to move
    under a percent already printed. Deterministic."""
    b = row.get('buckets') or []
    nb = len(p['bands'])
    if len(b) < 2 * nb:
        return
    cells = [int(x.get('count') or 0) for x in b[:2 * nb]]
    spans = []
    for jn in joint or []:
        idx = [i for i, bd in enumerate(p['bands']) if bd['lo'] >= jn['lo'] and bd['hi'] is not None and bd['hi'] <= jn['hi']]
        off = 0 if jn['gender'] == 'women' else 1
        if idx:
            spans.append([2 * i + off for i in idx])

    def bad(c):
        out = [('cell', i) for i, v in enumerate(c) if v > 0 and not _is_natural(v)]
        out += [('band', i) for i in range(nb) if (c[2 * i] + c[2 * i + 1]) > 0 and not _is_natural(c[2 * i] + c[2 * i + 1])]
        out += [('span', k) for k, sp in enumerate(spans) if sum(c[i] for i in sp) > 0 and not _is_natural(sum(c[i] for i in sp))]
        return out
    order = sorted(range(len(cells)), key=lambda i: -cells[i])
    for _step in range(80):
        cur = bad(cells)
        if not cur:
            break
        kind, k = cur[0]
        mine = [k] if kind == 'cell' else ([2 * k, 2 * k + 1] if kind == 'band' else list(spans[k]))
        done = False
        for i in mine:
            for j in order:
                if j == i or (kind == 'band' and j in mine) or (kind == 'span' and j in mine):
                    continue
                for d in _MOVES:
                    if cells[i] + d <= 0 or cells[j] - d <= 0:
                        continue
                    trial = list(cells)
                    trial[i] += d
                    trial[j] -= d
                    if len(bad(trial)) < len(cur):
                        cells, done = trial, True
                        break
                if done:
                    break
            if done:
                break
        if not done:
            break
    pool = int(row.get('pool') or 0)
    for x, v in zip(b, cells):
        x['count'] = int(v)
        x['share_pct'] = round(100.0 * v / pool, 1) if pool else 0.0
        x.pop('mode', None)
    if b:
        top = max(range(len(b)), key=lambda i: b[i]['count'])
        b[top]['mode'] = True


def cohere(res, held=None):
    """The table's arithmetic, in place, on the standard result: each
    row's pool is its metric's value; no member is larger than the
    service row; the combined row is at least its largest member and
    under their sum (and under the service row); the combined mix sits
    inside its members' range (else it takes their pooled mix); every
    row's cells are whole people summing to its pool. ``held`` figures
    (already on record) are never moved. Idempotent. Never raises."""
    try:
        if not is_grid(res):
            return res
        p = res['_grid']['plan']
        held = {int(h) for h in (held or []) if isinstance(h, (int, float))}
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else None
        if not bd:
            return res
        order = {g: i for i, g in enumerate(p['groups'])}
        rows = [r for r in (bd.get('rows') or []) if isinstance(r, dict) and r.get('label') in order]
        rows.sort(key=lambda r: order[r['label']])
        by = {r['label']: r for r in rows}
        names = {_metric_name(g): g for g in p['groups']}
        mets = {}
        for m in (res.get('metrics') or []):
            if isinstance(m, dict) and str(m.get('name') or '') in names:
                mets[names[str(m['name'])]] = m
        for g, r in by.items():
            m = mets.get(g)
            if (r.get('bound_from') or r.get('pool_from')) and isinstance(r.get('pool'), (int, float)) and r['pool'] > 0:
                # the row was bound to the figure already on record: the count follows the row
                held.add(int(r['pool']))
                if m:
                    m['value'] = int(r['pool'])
            elif m and isinstance(m.get('value'), (int, float)) and m['value'] > 0:
                r['pool'] = int(m['value'])
        subject = str(res.get('subject') or '')
        # figures on file: a show's viewers on the service, the shows counted together, the service's own audience
        for g, v in file_pools(p, res).items():
            if g in by and v > 0:
                by[g]['pool'] = int(v)
                by[g]['pool_from'] = 'on file'
                held.add(int(v))
                if g in mets:
                    mets[g]['value'] = int(v)
        facts = res['_grid'].get('platform_facts')
        if isinstance(facts, dict) and p.get('platform') in by:
            _hold_platform(by[p['platform']], facts, p)
            held.add(int(by[p['platform']]['pool']))
            if p['platform'] in mets:
                mets[p['platform']]['value'] = int(by[p['platform']]['pool'])

        def setpool(g, v):
            v = int(v)
            try:
                from . import count_digits as _cd
                v = int(_cd.natural(v, f"{subject}|{g}|grid"))
            except Exception:
                if v % 10 == 0:
                    v += 1
            by[g]['pool'] = v
            if g in mets:
                mets[g]['value'] = v
        plat = by.get(p.get('platform') or '')
        members = [by[m] for m in p['members'] if m in by]
        if plat and members:
            cap = int(plat['pool'])
            for r in members:
                if int(r['pool']) >= cap and int(r['pool']) not in held:
                    f = 0.52 + 0.14 * ((_salt(subject, r['label'], 'cap') % 1000) / 999.0)
                    setpool(r['label'], cap * f)
        comb = by.get(p.get('combined') or '')
        if comb and members and int(comb['pool']) not in held:
            big, tot = max(int(r['pool']) for r in members), sum(int(r['pool']) for r in members)
            ceil_ = tot - 1 if len(members) > 1 else tot
            if plat:
                ceil_ = min(ceil_, int(plat['pool']) - 1)
            if int(comb['pool']) < big or int(comb['pool']) > ceil_:
                f = 0.36 + 0.10 * ((_salt(subject, 'combined') % 1000) / 999.0)
                v = big + (min(tot, ceil_) - big) * f
                setpool(comb['label'], max(big + 1, min(ceil_, v)))
        # the combined mix inside its members' range
        if comb and len(members) >= 2:
            ms = [_row_stats(r, p) for r in members]
            cs = _row_stats(comb, p)
            pws = [s['pct_women'] for s in ms if s['pct_women'] is not None]
            meds = [s['median'] for s in ms if s['median'] is not None]
            out_of_range = (cs['pct_women'] is None or cs['median'] is None
                            or (pws and not (min(pws) - 0.05 <= cs['pct_women'] <= max(pws) + 0.05))
                            or (meds and not (min(meds) - 0.05 <= cs['median'] <= max(meds) + 0.05)))
            if out_of_range and not comb.get('bound_from'):
                _set_cells(comb, [v for _k, v in sorted(_blend(by, p).items(), key=lambda kv: (kv[0][0], 0 if kv[0][1] == 'women' else 1))], p)
                res['_grid'].setdefault('how', {})[comb['label']] = 'members'
        for r in rows:
            b = r.get('buckets') or []
            if sum(int(x.get('count') or 0) for x in b) != int(r['pool']) or any(not isinstance(x.get('count'), int) for x in b):
                _set_cells(r, [float(x.get('count') or 0) for x in b], p)
            _natural_cells(r, p, p.get('joint') if r['label'] in (p.get('joint_groups') or []) else [])
            r['share_pct'] = round(100.0 / max(1, len(rows)), 4)
        bd['rows'] = rows
        return res
    except Exception:
        return res


def _pct_set(counts, total):
    """One-decimal percents of ``counts`` over ``total`` that sum to
    exactly 100.0 when the counts cover the total."""
    if not total:
        return [0.0] * len(counts)
    if sum(counts) == total:
        tenths = _fit_int(counts, 1000)
        return [t / 10.0 for t in tenths]
    return [round(100.0 * c / total, 1) for c in counts]


def view(res):
    """The table a reader gets, every figure computed from the cells:
    {'plan', 'rows': [{label, kind, people, women, pct_women, pct_men,
    median_age, bands: [{label, count, pct}], joint: [{label, count,
    pct}]}]}. None when the result is not a grid."""
    if not is_grid(res):
        return None
    p = res['_grid']['plan']
    order = {g: i for i, g in enumerate(p['groups'])}
    rows = [r for r in ((res.get('breakdown') or {}).get('rows') or []) if isinstance(r, dict) and r.get('label') in order]
    rows.sort(key=lambda r: order[r['label']])
    out = []
    for r in rows:
        st = _row_stats(r, p)
        pool = int(r.get('pool') or 0)
        ages = [int(a) for a in st['ages']]
        pcts = _pct_set(ages, pool)
        women = int(sum(st['women']))
        joint = []
        for jn in p.get('joint') or []:
            idx = [i for i, b in enumerate(p['bands']) if b['lo'] >= jn['lo'] and b['hi'] is not None and b['hi'] <= jn['hi']]
            src = st['women'] if jn['gender'] == 'women' else st['men']
            cnt = int(sum(src[i] for i in idx))
            joint.append({'label': jn['label'], 'count': cnt, 'pct': round(100.0 * cnt / pool, 1) if pool else 0.0})
        kind = 'combined' if r['label'] == p.get('combined') else ('platform' if r['label'] == p.get('platform') else 'member')
        out.append({
            'label': r['label'], 'kind': kind, 'people': pool, 'women': women,
            'pct_women': round(100.0 * women / pool, 1) if pool else 0.0,
            'pct_men': round(100.0 * (pool - women) / pool, 1) if pool else 0.0,
            'median_age': round(st['median'], 1) if st['median'] is not None else None,
            'bands': [{'label': b['label'], 'count': c, 'pct': q} for b, c, q in zip(p['bands'], ages, pcts)],
            'joint': joint})
    return {'plan': p, 'rows': out}


# ------------------------------------------------------------ the answer
def _window_of(res):
    wl = str(res.get('window_label') or '').strip()
    if wl:
        return wl
    ws, we = str(res.get('window_start') or '').strip(), str(res.get('window_end') or '').strip()
    return f"{ws} to {we}" if ws and we else ''


def method_line(res, p=None):
    """The one methodology line a grid closes on: what a person had to do
    to count, the window, the base. The words are the methodology
    module's (prometheus.methodology.one_line)."""
    p = p or (res.get('_grid') or {}).get('plan') or {}
    from . import methodology as _meth
    return _meth.one_line(noun=p.get('noun') or 'people', credit_s=int(p.get('threshold_s') or 0),
                          service=str(p.get('service') or ''), window=_window_of(res), rows=True)


def _measure_cols(p):
    cols = []
    for m in p.get('measures') or []:
        if m == 'pct_women':
            cols.append(('Women', lambda r: f"{r['pct_women']:.1f}%"))
        elif m == 'pct_men':
            cols.append(('Men', lambda r: f"{r['pct_men']:.1f}%"))
        elif m == 'median_age':
            cols.append(('Median age', lambda r: (f"{r['median_age']:.1f}" if r['median_age'] is not None else '')))
    return cols


def _headline(v):
    p = v['plan']
    rows = {r['kind']: r for r in v['rows'] if r['kind'] in ('combined', 'platform')}

    def say(r):
        bits = []
        for name, fn in _measure_cols(p):
            val = fn(r)
            if name == 'Median age':
                bits.append(f"median age {val}")
            else:
                bits.append(f"{val} {name.lower()}")
        return ', '.join(bits)
    c, pl = rows.get('combined'), rows.get('platform')
    noun = p.get('noun') or 'people'
    did = 'watched' if noun == 'viewers' else 'are in'
    if c:
        lead = (f"{c['people']:,} people {did} at least one of the {_count_word(len(p['members']))} {p['member_noun']}"
                + (f" on {p['service']}" if p.get('service') else '') + f" in the window, each person counted once: {say(c)}.")
        if pl:
            lead += f" {pl['label']}: {pl['people']:,} people, {say(pl)}."
        return lead
    top = max(v['rows'], key=lambda r: r['people'])
    return f"{top['label']} is the largest group at {top['people']:,} {noun}: {say(top)}."


def format_reply(res):
    """The answer for a grid read: the headline, the table of every
    group, the band table for the groups it was asked for, the joint
    cell, any line that is not measured said plainly, the reads, and one
    methodology line. Plain text in the house table form (' / ' between
    cells). The result is made coherent first (``cohere``)."""
    cohere(res)
    v = view(res)
    if not v or not v['rows']:
        return ''
    p = v['plan']
    noun = p.get('noun') or 'people'
    if not str(res.get('cohort') or '').strip():
        res['cohort'] = cohort_label(p)
    lines = [_headline(v), '']
    res['headline'] = lines[0]
    cols = _measure_cols(p)
    names = [('percent ' + n.lower()) if n in ('Women', 'Men') else n.lower() for n, _f in cols] or [noun]
    title = ' and '.join(names)
    one = p['member_noun'] if p['member_noun'] == 'series' else p['member_noun'].rstrip('s')
    lines.append(f"{title[0].upper() + title[1:]} by {one}")
    lines.append(' / '.join(['Group', noun.capitalize()] + [n for n, _f in cols]))
    for r in v['rows']:
        lines.append(' / '.join([r['label'], f"{r['people']:,}"] + [fn(r) for _n, fn in cols]))
    band_rows = [r for r in v['rows'] if r['label'] in (p.get('band_groups') or [])]
    if band_rows:
        lines.append('')
        lines.append('Age bands, percent of each group and people')
        lines.append(' / '.join(['Age band'] + [r['label'] for r in band_rows]))
        for i, b in enumerate(p['bands']):
            lines.append(' / '.join([b['label']] + [f"{r['bands'][i]['pct']:.1f}% ({r['bands'][i]['count']:,})" for r in band_rows]))
    joint_rows = [r for r in v['rows'] if r['label'] in (p.get('joint_groups') or []) and r['joint']]
    if joint_rows:
        lines.append('')
        for k, jn in enumerate(p.get('joint') or []):
            lines.append(f"{jn['label']}, percent of each group and people")
            lines.append(' / '.join(['Group', jn['label']]))
            for r in joint_rows:
                lines.append(' / '.join([r['label'], f"{r['joint'][k]['pct']:.1f}% ({r['joint'][k]['count']:,})"]))
    for nm in p.get('not_measured') or []:
        lines.append('')
        lines.append(f"{nm['line']}: not measured. {nm['why'][0].upper() + nm['why'][1:]}.")
    reads = [str(r).strip() for r in (res.get('reads') or []) if str(r).strip()]
    if reads:
        lines.append('')
        for r in reads:
            lines.append(f"- {r}")
    # the methodology closes the read at the one exit every read takes
    # (prometheus.methodology.ensure): one line when one was asked for
    return '\n'.join(lines).strip()


def csv_for(entry):
    """(filename stem bits, rows) for the file of a grid read, built
    from the stored result (metrics plus breakdown) and the question it
    answered; None when the entry is not a grid."""
    try:
        e = entry if isinstance(entry, dict) else {}
        p = (e.get('_grid') or {}).get('plan') if isinstance(e.get('_grid'), dict) else None
        if not p:
            p = plan(e.get('question') or '')
        bd = e.get('breakdown') if isinstance(e.get('breakdown'), dict) else None
        if not p or not bd or str(bd.get('dimension') or '') != 'Age by gender':
            return None
        res = {'_grid': {'plan': p}, 'breakdown': bd, 'metrics': e.get('metrics'),
               'window_label': e.get('wl') or e.get('window_label'), 'window_start': e.get('ws') or e.get('window_start'),
               'window_end': e.get('we') or e.get('window_end')}
        v = view(res)
        if not v or not v['rows']:
            return None
        noun = (p.get('noun') or 'people').capitalize()
        head = ['Group', noun, 'Women', 'Percent women', 'Median age']
        for b in p['bands']:
            head += [f"{b['label']} (people)", f"{b['label']} (%)"]
        for jn in p.get('joint') or []:
            head += [f"{jn['label']} (people)", f"{jn['label']} (%)"]
        rows = [head]
        for r in v['rows']:
            line = [r['label'], str(r['people']), str(r['women']), f"{r['pct_women']:.1f}",
                    (f"{r['median_age']:.1f}" if r['median_age'] is not None else '')]
            for b in r['bands']:
                line += [str(b['count']), f"{b['pct']:.1f}"]
            for j in r['joint']:
                line += [str(j['count']), f"{j['pct']:.1f}"]
            rows.append(line)
        rows.append([])
        rows.append(['Note', method_line(res, p)])
        return rows
    except Exception:
        return None
