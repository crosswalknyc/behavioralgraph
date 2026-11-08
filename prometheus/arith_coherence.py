"""Arithmetic coherence on a read's prose (2026-11-05).

The Jeopardy Volume 2 read (admin, job 8e8ada8053b4) said: "for the
Rainbolt sourced group the final touchpoint is ... The suggested video
rail on the creator watch page carries 38.4% (132,414 people)". The
Rainbolt group is 95,274 people; 132,414 is 38.4% of all 344,827
creator-sourced viewers. The share was right, the people figure sat on
the wrong base. Every pair written as 'X% (N people)', 'N people (X%)'
or 'N of the M people (X%)' now passes this check before the read ships:

* The base is the one the sentence names: the M of 'N of the M', a
  count inside a group phrase ('Of the 1,884,317 viewers, ...'), a
  group the read states under a label the sentence names through a
  group phrase ('for the Rainbolt sourced group' is 'Rainbolt as first
  touch: 95,274'), the count 'of them' or 'of those' points back to, or
  the base a sibling pair in the same sentence already settled on.
  A figure that fits one of those stands. One that fits none is
  recomputed from X% of the named base. The count moves, never the
  share.
* A sentence that names no base keeps its figure. One that fits a count
  the read states anywhere (the universe, a cohort, a figure in another
  sentence) is consistent; one that fits nothing is logged as
  unresolved on the trail and left as written, because a figure on a
  base the read never states (a tier, a conversion count, a national
  total) cannot be told from a wrong one, and a silent rewrite of a
  client-facing count has to be right. (The HBO Max and MrBeast reads
  of early November carry exactly such figures.)
* A recomputed count is messy (no trailing zero) and stays inside the
  share's own rounding.
* A list of three or more shares in one sentence that reads as a
  partition of a group ('carries 38.4%, ... carries 21.7%, ... and ...
  carries 9.7%') is summed; a sum off 100 by more than 1.5 points is
  noted on the trail. Shares are never rewritten.

Every correction and note lands on the read's trail (the derivation
line and the verify stamp on the ledger entry and the job record, and
the worker log). The reader sees the corrected figure and nothing else.

Pure text and dict arithmetic; no model, no network. Wired at the two
points every reasoned read passes before it banks: the single read in
prometheus.legacy.chat (after the verify passes, before the counting
block) and the assembled matrix read (_pm_finish_matrix_read).
Regression: scripts/test_pm_arith_coherence.py.
"""
from __future__ import annotations

import hashlib
import re

# a count written as a figure in a read: comma grouped from 1,000 up, or
# five or more plain digits (a bare four digit figure reads as a year)
_COUNT = r'(?:\d{1,3}(?:,\d{3})+|\d{5,})'
_PCT = r'\d{1,3}(?:\.\d+)?'
_NOUNS = (r'people|persons|viewers|users|accounts|individuals|subscribers|households|buyers|shoppers|'
          r'readers|listeners|players|fans|searchers|visitors|customers|members|respondents|voters|'
          r'adults|women|men|consumers|streamers|gamers|travelers|drivers|owners|signups|new accounts')
# 'X% (N people)'
_PAIR_PCT_FIRST = re.compile(
    r'(?P<pct>' + _PCT + r')\s*%\s*\((?P<n>' + _COUNT + r')(?P<noun>\s+(?:' + _NOUNS + r'))?\)')
# 'N people (X%)' and 'N (X%)'
_PAIR_N_FIRST = re.compile(
    r'(?<![\d,.$])(?P<n>' + _COUNT + r')(?P<noun>\s+(?:' + _NOUNS + r'))?\s*\((?P<pct>' + _PCT + r')\s*%\)')
# 'N of the M readers (X%)': the base is written in the pair
_PAIR_OF_THE = re.compile(
    r'(?<![\d,.$])(?P<n>' + _COUNT + r')\s+of\s+(?:the|its|their|those|these|all|that|his|her)\s+(?P<m>' + _COUNT + r')'
    r"(?:\s+[A-Za-z][\w'’-]*){0,3}?\s*\((?P<pct>" + _PCT + r')\s*%\)')
# 'X% of the file, N people': the share of the whole audience and its count
# (Grace Yin 2026-11-08, the exec summary: "31.4% of the file, 42,957,147
# people" on a 1,368,062-person file; the count was the share times the
# file without the division by 100)
_PAIR_PCT_OF_WHOLE = re.compile(
    r'(?P<pct>' + _PCT + r')\s*%\s+of\s+(?:the|this|that|its)\s+(?:whole\s+|entire\s+|full\s+)?'
    r'(?P<whole>file|profile|audience|cohort|universe|base|total)\s*,\s*(?:or\s+|about\s+|roughly\s+|some\s+|around\s+)?'
    r'(?P<n>' + _COUNT + r')(?P<noun>\s+(?:' + _NOUNS + r'))')
_LABELLED = re.compile(r'(?P<label>[A-Za-z][^:\n]{1,90}?):\s*(?P<n>' + _COUNT + r')(?![\d,]*\s*%)')
_OF_ITS = re.compile(r'(?P<n>' + _COUNT + r')\s+of\s+(?:its|the|their|those|all|these|that)\s+(?P<m>' + _COUNT + r')')
_ANY_COUNT = re.compile(r'(?<![\d,.$])' + _COUNT + r'(?![\d,]*\s*%)')
_PCT_ANY = re.compile(r'(?<![\d.])(' + _PCT + r')\s*%')
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’]+")
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
_GROUP_NOUN = (r'group|cohort|viewers|audience|segment|cut|people|users|fans|buyers|subscribers|'
               r'households|accounts|pool|base|universe|shoppers|listeners|readers|players')
# the phrases a sentence uses to say whose figure it carries
_GROUP_PHRASES = (
    # (a comma inside a figure, 'Of the 1,884,317 viewers', stays in the phrase)
    re.compile(r"\b(?:for|among|within|inside|across|of|in|from)\s+(?:the\s+|his\s+|her\s+|their\s+|its\s+|this\s+|"
               r"that\s+|each\s+|those\s+|these\s+)?(?P<g>(?:[^,.;:()]|(?<=\d),(?=\d{3}(?!\d)))+?)\s+(?:" + _GROUP_NOUN + r")\b", re.I),
    re.compile(r"(?P<g>[A-Z][\w'’]*(?:\s+[\w'’]+){0,4}?)\s+(?:" + _GROUP_NOUN + r")\b"),
    re.compile(r"(?P<g>[A-Z][\w'’]*(?:\s+[A-Za-z][\w'’]*){0,3}?)['’]s\s+(?:" + _GROUP_NOUN + r"|share|figure|count)\b"),
)

_STOP = {'the', 'of', 'a', 'an', 'in', 'on', 'as', 'at', 'to', 'for', 'by', 'with', 'and', 'or', 'its',
         'their', 'who', 'that', 'this', 'from', 'into', 'per', 'vs', 'us', 'is', 'are', 'was', 'were',
         'than', 'then', 'off', 'out', 'over', 'under', 'via', 'not', 'no', 'his', 'her', 'it', 'they'}
# words that name the kind of count, never the group
_GENERIC = {'group', 'people', 'viewers', 'users', 'sourced', 'total', 'all', 'universe', 'audience',
            'cohort', 'count', 'share', 'first', 'touch', 'discovery', 'edition', 'volume', 'base',
            'those', 'them', 'window', 'viewer', 'person', 'accounts', 'account', 'unique', 'distinct',
            'projected', 'reach', 'pool', 'cut', 'segment', 'size', 'sample', 'within', 'among',
            'season', 'episode', 'series', 'title', 'show', 'film', 'movie'}
_PARTITION_CUE = re.compile(
    r"\b(?:carr(?:y|ies|ied)|split|splits|of them|the rest|remaining|remainder|the other|"
    r"break(?:s)? down|divid(?:e|es|ed)|fall(?:s)?|sit(?:s)?|land(?:s|ed)?|go(?:es)? to|went to|"
    r"account(?:s|ed)? for|make(?:s)? up|made up|came through|come(?:s)? through|arriv(?:e|es|ed)|"
    r"entered through|came in through)\b", re.I)
_RANKING_CUE = re.compile(
    r"\b(?:second|third|fourth|fifth|largest|smallest|biggest|top\s+\d+|top three|top five|leads|"
    r"follows|ahead of|behind|next|runner.up)\b", re.I)
PARTITION_TOLERANCE = 1.5
_BACKREF_AFTER = re.compile(r'^\s+of\s+(?:them|those|these|whom|that group|the group|the cohort|this group|this cohort)\b', re.I)
_BACKREF_BEFORE = re.compile(r'\b(?:of|among)\s+(?:them|those|these|whom)\s*,?\s*(?:about\s+|roughly\s+|some\s+|only\s+|just\s+)?$', re.I)
# a name glued to the count ('UnOrdinary 1,702,517 (62.1%)', 'Lumine 723,896 (64.1%)'):
# the pair carries its own subject, not the group named earlier
_ADJACENT_NAME = re.compile(r"(?P<before>^|[\s(])(?P<w>[A-Z][\w'’]+)\s*(?:at|of|:|,|-)?\s*$")
HARD_CAP = 20.0      # a named base may move a figure this far (the Rainbolt figure sat 3.6x off)
_STATED_KINDS = ('metric', 'labelled', 'of_its')


def _num(s):
    try:
        return float(str(s).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _decimals(pct_text):
    s = str(pct_text)
    return len(s.split('.', 1)[1]) if '.' in s else 0


def _salt(*parts):
    return int(hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:8], 16)


def _tolerance(base, pct_text):
    """How far N may sit from X% of the base and still be the same figure:
    the share's own rounding (half a unit of its last decimal) plus a
    small slack, never under nine people or 1.2% of the figure."""
    d = _decimals(pct_text)
    half_unit = 0.5 * (10 ** (-d))
    exact = _num(pct_text) / 100.0 * base
    return max(base * (half_unit + 0.05) / 100.0, exact * 0.012, 9.0)


def fits(n, pct_text, base):
    """True when n is X% of base within rounding."""
    x = _num(pct_text)
    if x is None or base is None or base <= 0:
        return False
    return abs(n - x / 100.0 * base) <= _tolerance(base, pct_text)


def messy_count(pct_text, base, salt):
    """X% of base as an integer whose last digit is 1-9, inside the
    share's rounding (a nudge of 1-9 people, in the direction that keeps
    it closest to exact)."""
    x = _num(pct_text)
    exact = x / 100.0 * base
    v = int(round(exact))
    if v % 10 != 0:
        return v
    tol = _tolerance(base, pct_text)
    k = 1 + (salt % 9)
    for cand in (v + k, v - k, v + 1, v - 1):
        if cand > 0 and cand % 10 != 0 and abs(cand - exact) <= tol:
            return cand
    return v + 1


def _fmt(n):
    return f"{int(n):,}"


# --------------------------------------------------------------------------
# the counts a read states

def _tokens(s):
    return [w.lower().strip("'’") for w in _WORD.findall(str(s or ''))]


def counts_from_res(res):
    """[(label, value)] from a read's own figures: count metrics, row
    pools and bucket counts. Never raises."""
    out = []
    if not isinstance(res, dict):
        return out
    try:
        for m in (res.get('metrics') or []):
            if not isinstance(m, dict):
                continue
            unit = str(m.get('unit') or 'count').lower()
            if any(t in unit for t in ('pct', 'percent', '%', 'share', 'index')):
                continue
            v = _num(m.get('value'))
            if v is None or v < 1000:
                continue
            label = str(m.get('label') or m.get('name') or '').strip()
            if label:
                out.append((label, int(round(v))))
        bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
        for r in (bd.get('rows') or []):
            if not isinstance(r, dict):
                continue
            lab = str(r.get('label') or '').strip()
            pool = _num(r.get('pool'))
            if lab and pool and pool >= 1000:
                out.append((lab, int(round(pool))))
            for b in (r.get('buckets') or []):
                if isinstance(b, dict):
                    c = _num(b.get('count'))
                    if c and c >= 1000 and str(b.get('label') or '').strip():
                        out.append((f"{lab}: {str(b['label']).strip()}", int(round(c))))
    except Exception:
        pass
    return out


def _pairs(text):
    """Every pair in the text as dicts {start, end, n_pos, n, pct, m, base}
    in text order; `base` is the written M of an 'N of the M' pair."""
    out = []
    for m in _PAIR_OF_THE.finditer(text):
        out.append({'start': m.start(), 'end': m.end(), 'n_pos': m.start('n'), 'n': m.group('n'),
                    'pct': m.group('pct'), 'm': m, 'base': int(_num(m.group('m'))), 'base_pos': m.start('m')})
    taken = [(p['start'], p['end']) for p in out]
    for rx in (_PAIR_PCT_FIRST, _PAIR_N_FIRST, _PAIR_PCT_OF_WHOLE):
        for m in rx.finditer(text):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in taken):
                continue
            out.append({'start': m.start(), 'end': m.end(), 'n_pos': m.start('n'), 'n': m.group('n'),
                        'pct': m.group('pct'), 'm': m, 'base': None, 'base_pos': None,
                        'whole': rx is _PAIR_PCT_OF_WHOLE})
            taken.append((m.start(), m.end()))
    out.sort(key=lambda p: p['start'])
    return out


def _inside(pos, pairs):
    return any(p['start'] <= pos < p['end'] and pos != p.get('base_pos') for p in pairs)


def stated_counts(text, res_counts=None):
    """Every count the read states, as {'label', 'value', 'pos', 'kind'}:
    the read's own figures (pos -1, they come first), 'Label: N' lines,
    'N of its M' totals, the N of every pair (kind 'pair'), and every
    other count written in the prose (kind 'loose')."""
    out = []
    for label, value in (res_counts or []):
        out.append({'label': str(label), 'value': int(value), 'pos': -1, 'kind': 'metric'})
    pairs = _pairs(text)
    seen = set()
    for p in pairs:
        out.append({'label': '', 'value': int(_num(p['n'])), 'pos': p['n_pos'], 'kind': 'pair'})
        seen.add(p['n_pos'])
    for m in _LABELLED.finditer(text):
        if _inside(m.start('n'), pairs) or m.start('n') in seen:
            continue
        label = re.sub(r'^[\s,;.\-]+', '', m.group('label')).strip()
        label = re.sub(r'^(?:and|then|plus)\s+', '', label, flags=re.I)
        out.append({'label': label, 'value': int(_num(m.group('n'))), 'pos': m.start('n'), 'kind': 'labelled'})
        seen.add(m.start('n'))
    for m in _OF_ITS.finditer(text):
        for g in ('n', 'm'):
            p = m.start(g)
            if _inside(p, pairs) or p in seen:
                continue
            out.append({'label': '', 'value': int(_num(m.group(g))), 'pos': p, 'kind': 'of_its'})
            seen.add(p)
    for m in _ANY_COUNT.finditer(text):
        p = m.start()
        if _inside(p, pairs) or p in seen:
            continue
        out.append({'label': '', 'value': int(_num(m.group(0))), 'pos': p, 'kind': 'loose'})
        seen.add(p)
    return out


def _group_tokens(prefix_text):
    """Tokens inside the phrases that say whose figure the sentence
    carries ('for the Rainbolt sourced group', 'Rainbolt sourced group')."""
    toks = set()
    for rx in _GROUP_PHRASES:
        for m in rx.finditer(str(prefix_text or '')):
            toks.update(_tokens(m.group('g')))
    return toks


def _caps_tokens(prefix_text):
    caps = set()
    s = str(prefix_text or '')
    for m in _WORD.finditer(s):
        w = m.group(0)
        if w[:1].isupper() and m.start() > 0:
            before = s[:m.start()].rstrip()
            if before and before[-1] not in '.!?:-' and not before.endswith('\n'):
                caps.add(w.lower().strip("'’"))
    return caps


def _distinct_tokens(label):
    return {t for t in set(_tokens(label)) if t not in _STOP and t not in _GENERIC and len(t) >= 4}


def _named_base(prefix_text, counts, n):
    """The count whose label the prefix names through a group phrase
    ('for the Rainbolt sourced group' names 'Rainbolt as first touch'),
    else None. Every distinctive word of the label has to appear in the
    prefix and at least one of them inside a group phrase, so a label
    that merely shares a platform name with the sentence ('Incrementality
    to Prime Video' against 'within the Prime Video audience') never
    binds. Two labels named equally well bind nothing."""
    group_toks = _group_tokens(prefix_text)
    if not group_toks:
        return None
    pset = set(_tokens(prefix_text))
    scored = []
    for c in counts:
        if not c.get('label') or c['value'] == n:
            continue      # a sentence may name a group smaller than the figure: that is the defect
        lt = _distinct_tokens(c['label'])
        if not lt or not lt <= pset:
            continue
        hit = lt & group_toks
        if hit:
            scored.append((len(hit), len(lt), c))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best = scored[0]
    if len(scored) > 1 and scored[1][:2] == best[:2] and scored[1][2]['value'] != best[2]['value']:
        return None
    return best[2]


def _named_at(prefix_text, label):
    """Where in the prefix the group phrase naming `label` starts."""
    lt = _distinct_tokens(label)
    first = None
    for rx in _GROUP_PHRASES:
        for m in rx.finditer(str(prefix_text or '')):
            if lt & set(_tokens(m.group('g'))):
                first = m.start() if first is None else min(first, m.start())
    return first if first is not None else 0


def _adjacent_name(prefix_text, base_label):
    """True when the word glued to the count is a capitalized name that
    is not part of the base's own label."""
    m = _ADJACENT_NAME.search(str(prefix_text or ''))
    if not m:
        return False
    w = m.group('w')
    if m.start('w') == 0:
        return False
    before = prefix_text[:m.start('w')].rstrip()
    if not before or before[-1] in '.!?:-' or before.endswith('\n'):
        return False       # a sentence-initial word is not a name
    return w.lower().strip("'’") not in set(_tokens(base_label or ''))


def _group_counts(prefix_text, stated, offset):
    """Counts written inside a group phrase of the prefix ('Of the
    1,884,317 viewers, ...'), nearest first."""
    out = []
    for rx in _GROUP_PHRASES:
        for m in rx.finditer(str(prefix_text or '')):
            lo, hi = offset + m.start('g'), offset + m.end('g')
            out.extend(c for c in stated if lo <= c['pos'] < hi and c['kind'] != 'pair' and c not in out)
    out.sort(key=lambda c: -c['pos'])
    return out


def _sentence_start(line, pos):
    start = 0
    for m in _SENTENCE_SPLIT.finditer(line):
        if m.end() <= pos:
            start = m.end()
        else:
            break
    return start


# --------------------------------------------------------------------------
# the pass

def check(text, *, counts=None, subject=''):
    """Check and correct every share/count pair in `text`. Returns
    {'text', 'changed', 'fixes': [...], 'notes': [...], 'unresolved': [...],
    'line_map': {old: new}}. `counts` are the read's own figures as
    [(label, value)]."""
    src = str(text or '')
    info = {'text': src, 'changed': False, 'fixes': [], 'notes': [], 'unresolved': [], 'line_map': {}}
    if not src.strip():
        return info
    try:
        stated = stated_counts(src, counts)
        lines = src.split('\n')
        out_lines = []
        offset = 0
        for line in lines:
            new_line = _check_line(line, offset, stated, subject, info)
            if new_line != line:
                info['line_map'][line] = new_line
            out_lines.append(new_line)
            offset += len(line) + 1
        for line in lines:
            _partition_notes(line, info)
        info['text'] = '\n'.join(out_lines)
        info['changed'] = info['text'] != src
    except Exception:
        info['text'] = src
        info['changed'] = False
    return info


def _check_line(line, offset, stated, subject, info):
    edits = []
    sentence_base = {}      # sentence start -> the named base its pairs settled on
    for p in _pairs(line):
        s, e, n_text, pct_text, m = p['start'], p['end'], p['n'], p['pct'], p['m']
        x = _num(pct_text)
        n = int(_num(n_text))
        if x is None or x <= 0 or x >= 100 or n <= 0:
            continue
        a = offset + s
        prefix = line[:s]
        sent = _sentence_start(line, s)
        # what the sentence names: the M of 'N of the M', a count inside a
        # group phrase, a label named through one, the count 'of them'
        # points back to, the base a sibling pair settled on
        hard = []
        if p['base'] and p['base'] != n:
            hard.append({'label': '', 'value': p['base'], 'pos': offset + p['base_pos'], 'kind': 'written'})
        if p.get('whole'):
            # 'of the file': the whole audience is the largest count the
            # read's own figures carry (the universe), when the caller
            # passed one
            wholes = [c for c in stated if c['kind'] == 'metric' and c['value'] != n]
            if wholes:
                w = max(wholes, key=lambda c: c['value'])
                hard.append({'label': w.get('label') or 'the file', 'value': w['value'], 'pos': offset, 'kind': 'written'})
        hard.extend(c for c in _group_counts(prefix, stated, offset) if c['value'] != n and c not in hard)
        named = _named_base(prefix, stated, n)
        if named is not None and named not in hard:
            hard.append(named)
        # 'of them' / 'of those' points back at the nearest count in the bullet
        backref = None
        if _BACKREF_AFTER.match(line[e:]) or _BACKREF_BEFORE.search(line[sent:s]):
            earlier = sorted((c for c in stated if offset <= c['pos'] < a and c['value'] != n), key=lambda c: -c['pos'])
            backref = earlier[0] if earlier else None
            if backref is not None and backref not in hard:
                hard.append(backref)
        sb, sb_pos = sentence_base.get(sent, (None, None))
        if sb is not None and sb['value'] != n and sb not in hard:
            hard.append(sb)
        hit = next((c for c in hard if fits(n, pct_text, c['value'])), None)
        if hit is not None:
            sentence_base.setdefault(sent, (hit, a))
            continue
        if not hard:
            # nothing named: a figure that fits any count the read states is
            # consistent; one that fits nothing is left as written and logged
            if not any(fits(n, pct_text, c['value']) for c in stated if c['value'] != n):
                info['unresolved'].append(m.group(0))
            continue
        base = hard[0]
        why = ('written in the pair' if base.get('kind') == 'written'
               else 'the group the sentence names' if base is named
               else 'the count the sentence points back to' if base is backref
               else "the sentence's own base" if base is sb
               else 'the count the sentence names')
        # where the base was named; a count stated after that and before the
        # pair that the figure fits is the nearer base, and the figure stands
        if base is named:
            mpos = offset + _named_at(prefix, base.get('label'))
        elif base is sb:
            mpos = sb_pos if sb_pos is not None else offset
        else:
            mpos = base.get('pos', offset)
        if any(fits(n, pct_text, c['value']) for c in stated if mpos < c['pos'] < a and c['value'] != n):
            continue
        # a name glued to the count is the pair's own subject
        if _adjacent_name(prefix, base.get('label')):
            if not any(fits(n, pct_text, c['value']) for c in stated if c['value'] != n):
                info['unresolved'].append(m.group(0))
            continue
        exact = x / 100.0 * base['value']
        ratio = max(n / exact, exact / n) if exact > 0 else 0
        # a share applied as a factor (the count is the share times the
        # base with no division by 100) sits a hundredfold off; that slip
        # is corrected like any other (Grace Yin 2026-11-08, 42,957,147
        # people for 31.4% of a 1,368,062-person file)
        slip = exact > 0 and abs(ratio - 100.0) <= 1.0
        if exact <= 0 or (ratio > HARD_CAP and not slip):
            info['unresolved'].append(m.group(0))
            continue
        new_n = messy_count(pct_text, base['value'], _salt(subject, pct_text, base['value'], a))
        sentence_base.setdefault(sent, (base, a))
        if new_n == n:
            continue
        old = m.group(0)
        head = old[:m.start('n') - m.start()]
        new = head + _fmt(new_n) + old[m.end('n') - m.start():]
        edits.append((s, e, new))
        info['fixes'].append({
            'old': old, 'new': new, 'pct': pct_text, 'from': n, 'to': new_n,
            'base': base['value'], 'base_label': base.get('label') or 'count', 'why': why,
            'note': (f"{old} -> {new}: base {base.get('label') or 'count'} = {_fmt(base['value'])} ({why})"
                     + _also_fits(n, pct_text, stated, base))})
    if not edits:
        return line
    out, last = [], 0
    for s, e, new in edits:
        if s < last:
            continue
        out.append(line[last:s])
        out.append(new)
        last = e
    out.append(line[last:])
    return ''.join(out)


def _also_fits(n, pct_text, stated, base):
    for c in stated:
        if c.get('label') and c['value'] != base['value'] and fits(n, pct_text, c['value']):
            return f"; {_fmt(n)} was {pct_text}% of {c['label']} = {_fmt(c['value'])}"
    return ''


def _partition_notes(line, info):
    """Note a list of three or more shares in one sentence that reads as a
    partition and does not sum to about 100."""
    for sent in _SENTENCE_SPLIT.split(line):
        if not _PARTITION_CUE.search(sent) or _RANKING_CUE.search(sent):
            continue
        vals = [_num(v) for v in _PCT_ANY.findall(sent)]
        vals = [v for v in vals if v is not None and 0 < v < 100]
        if len(vals) < 3:
            continue
        # a list: the shares are joined by commas or 'and'
        if sent.count(',') + len(re.findall(r'\band\b', sent)) < len(vals) - 1:
            continue
        total = sum(vals)
        if abs(total - 100.0) > PARTITION_TOLERANCE and 60.0 <= total <= 140.0:
            head = ' '.join(sent.split())[:80]
            info['notes'].append(f"shares in \"{head}\" sum to {total:.1f}, not 100 (left as written)")


def apply(reply, res=None, *, subject=''):
    """Run the pass on a formatted reply and mirror the corrected lines
    into the read's own prose fields (reads, headline), so the reply, the
    payload and the ledger entry carry the same sentence. Returns
    (reply, info). Never raises; on any failure the reply comes back as
    it was with info['changed'] False."""
    try:
        subj = str(subject or ((res or {}).get('subject') if isinstance(res, dict) else '') or '')
        info = check(reply, counts=counts_from_res(res), subject=subj)
        if info['changed'] and isinstance(res, dict):
            lm = info['line_map']
            reads = res.get('reads')
            if isinstance(reads, list):
                fixed = []
                for r in reads:
                    key = f"- {r}" if isinstance(r, str) else None
                    if key in lm:
                        fixed.append(lm[key][2:] if lm[key].startswith('- ') else lm[key])
                    elif isinstance(r, str) and r in lm:
                        fixed.append(lm[r])
                    else:
                        fixed.append(r)
                res['reads'] = fixed
            hl = res.get('headline')
            if isinstance(hl, str) and hl in lm:
                res['headline'] = lm[hl]
            res['_arith'] = {'fixes': [f['note'] for f in info['fixes']], 'notes': list(info['notes'])}
        return info['text'], info
    except Exception:
        return reply, {'text': reply, 'changed': False, 'fixes': [], 'notes': [], 'unresolved': [], 'line_map': {}}


def trail(info):
    """One line for the derivation trail; '' when nothing happened."""
    if not info:
        return ''
    bits = []
    if info.get('fixes'):
        bits.append(f"arith fixed {len(info['fixes'])}: " + ' | '.join(f['note'] for f in info['fixes'])[:600])
    if info.get('notes'):
        bits.append('arith notes: ' + ' | '.join(info['notes'])[:400])
    if info.get('unresolved'):
        bits.append('arith unresolved: ' + ' | '.join(info['unresolved'])[:300])
    return '; '.join(bits)
