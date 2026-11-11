"""A people count has a ceiling (2026-11-11).

A category answer on the open Chime Banking Customers profile read: "On a
projected base of 8,977,404 people that is about 392,312,555
insurance-active people." The figure is larger than the United States.
The answer had written the count as arithmetic on two figures it was
handed (the audience and the 43.7% share) and the percent was applied as
a whole number; that sum is fixed where it is evaluated
(prometheus.figures). This module is the net under every answer, whatever
wrote the words:

1. A count of US people never passes the US population (329,900,000).
2. A count stated as part of an audience never passes that audience: the
   base its own sentence names ('a base of M people that is N', 'N of the
   M', 'Of the M viewers, N ...'), or the open audience when the sentence
   says the count sits inside it.
3. Where the answer carries the figure for the same quantity (a category's
   reach, a profile row, a cross), the words print that figure, never a
   second sum that lands near it.

An answer is never held or failed for this. A count that breaks a rule is
replaced in place with the figure the answer carries for it; with no such
figure, and when the count is plainly the share times the base without
the division by 100, the same sum is done right; otherwise the sentence
that states it is dropped and the rest ships. A reply that is only that
sentence loses the bracket that holds the count, or is left as written
and noted on the trail.

    check(text, audience=None, facts=(), resolver=None)   the pass itself
    guard_composed(text, blocks)    a composed answer against its evidence
    guard_payload(raw)              a reply payload on its way out
    clean(text)                     words alone (rules 1 and 2)
    facts_from_blocks(blocks)       the figures an evidence block carries

Pure text and dict arithmetic; no model. The one lookup (the open
profile's own figures, ``_turn_evidence``) runs only after a count has
already broken a rule. Never raises.
Regression: scripts/test_pm_people_bounds.py.
"""
from __future__ import annotations

import hashlib
import re
import traceback

__all__ = ['US_POPULATION', 'check', 'guard_composed', 'guard_payload', 'clean', 'facts_from_blocks', 'trail']

US_POPULATION = 329_900_000

_COUNT = r'(?:\d{1,3}(?:,\d{3})+|\d{5,})'
_NOUNS = (r'people|persons|viewers|users|accounts|individuals|subscribers|subs|buyers|shoppers|readers|listeners|'
          r'players|fans|searchers|visitors|customers|members|adults|women|men|consumers|streamers|gamers|'
          r'travelers|drivers|owners|engagers|followers|watchers|purchasers|americans|households|respondents')
# up to three describing words between the figure and its noun ('insurance-active', 'US', 'unique monthly'),
# never a word that starts another clause and never a unit that is not a person
_MOD = (r"(?:\s+(?!(?:of|in|on|by|for|to|from|per|and|or|than|vs|versus|across|among|with|at|is|are|was|were|that|who|"
        r"which|views?|streams?|plays?|sessions?|hours?|minutes?|visits?|searches|impressions?|orders?|transactions?|"
        r"dollars?|episodes?|downloads?|installs?|clicks?|posts?|videos?|titles?|times|rows?)\b)[A-Za-z][\w'’/-]*){0,3}?")
_PEOPLE_RX = re.compile(r"(?<![\w,.$/-])(?P<n>" + _COUNT + r")(?![\d,]*\s*%)(?P<mod>" + _MOD + r")\s+(?P<noun>" + _NOUNS + r")\b", re.I)
_SCALED_RX = re.compile(r"(?<![\w,.$/-])(?P<n>\d{1,3}(?:\.\d+)?)\s?(?P<scale>billion|million|bn|[MB])\b(?P<mod>" + _MOD + r")\s+(?P<noun>"
                        + _NOUNS + r")\b")
_LABEL_FIRST_RX = re.compile(r"\b(?:US people|projected (?:US )?(?:people|audience|viewers|users))\s*(?::|=|of|at|is|was)?\s*"
                             r"(?:about\s+|roughly\s+|around\s+)?(?P<n>" + _COUNT + r")(?![\d,]*\s*%)", re.I)
_OF_THE_RX = re.compile(r"(?<![\w,.$/-])(?P<n>" + _COUNT + r")\s+(?:out\s+)?of\s+(?:the|its|their|those|these|all|that|this|his|her)\s+"
                        r"(?P<m>" + _COUNT + r")(?![\d,]*\s*%)", re.I)
_ANY_COUNT_RX = re.compile(r"(?<![\w,.$/-])" + _COUNT + r"(?![\d,]*\s*%)")
# the two ways a sentence names the base of its own counts: by its noun ('a base of M'), or by opening on it ('Of the M viewers, ...')
_BASE_NOUN_RX = re.compile(r"\b(?:base|universe|audience|pool|file|profile|cohort|total|size)\s+(?:of|is|at|was|:)\s+"
                           r"(?:about\s+|roughly\s+|around\s+|some\s+|nearly\s+)?$", re.I)
_BASE_OPEN_RX = re.compile(r"^\W*(?:of|among|out\s+of|from|across)\s+(?:the|its|their|those|these|all|that|this|his|her)\s+$", re.I)
# what must sit between the base and a count for the count to be a part of it
_EQUIV_RX = re.compile(r"(?:\bthat(?:'s|\s+is|\s+comes\s+to|\s+works\s+out\s+to|\s+means|\s+puts\s+it\s+at)|\bwhich\s+is|\bor|\bis|\bare|\bequals|"
                       r"\bcomes\s+to|\bworks\s+out\s+to|\btranslates\s+to|[,:(])\s*(?:about\s+|roughly\s+|around\s+|some\s+|nearly\s+|approximately\s+|just\s+|only\s+)?$", re.I)
_AFTER_OPEN_RX = re.compile(r"^[^,;.]{0,60},\s*(?:about\s+|roughly\s+|around\s+|some\s+|nearly\s+|only\s+|just\s+)?$")
_BASE_AFTER_RX = re.compile(r"\b(?:on|against|from|out\s+of|off)\s+(?:a|an|the|its|this|that)\s+(?:[a-z-]+\s+){0,2}$", re.I)
# a platform's own size is a world figure unless the sentence says otherwise
_PLATFORM_NOUN_RX = re.compile(r"\b(?:followers|subscribers|subs|users|accounts|members|players|listeners|fans)$", re.I)
# a read whose measure is a volume (hours, views, orders) states counts that are not people; the row lines its
# table prints ('Pool 5,885,924,493 viewers') carry that measure under a people noun (Cara Dorfman's hours by
# month, 2026-11-10), so on such a read a row's pool is never read as a headcount
_VOLUME_RX = re.compile(r"(?<![\w,.$/-])" + _COUNT + r"(?:\s+[A-Za-z][\w'’/-]*){0,2}?\s+(?:hours|minutes|views|streams|plays|sessions|visits|"
                        r"searches|orders|purchases|transactions|impressions|episodes)\b", re.I)
_POOL_ROW_RX = re.compile(r"\bPool\s+$")
MANY = 3        # this many counts over the US population in one answer is a unit, never a slip: nothing is touched
MAX_DROPS = 2   # an answer never loses more than this many sentences to the net
_US_CUE_RX = re.compile(r"\b(?:US|U\.S\.|projected|audience|profile|file|base|cohort|panel|of them|of those)\b|%")
_PCT_RX = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d+)?)\s*%")
_GLOBAL_RX = re.compile(r"\b(?:worldwide|global(?:ly)?|international(?:ly)?|around the world|across the world|in the world|world'?s|"
                        r"on earth|the planet|countries|markets outside)\b", re.I)
_OUTSIDE_RX = re.compile(r"\b(?:US adults|U\.S\. adults|Americans|nationally|nationwide|across the (?:US|U\.S\.|country)|"
                         r"in the (?:US|U\.S\.) overall|US[- ]wide|gen(?:eral)? pop(?:ulation)?|US population|of the US\b|US total|"
                         r"all US|whole US|entire US)", re.I)
_INSIDE_RX = re.compile(r"\b(?:of|in|inside|within)\s+(?:the|this|that|its)\s+(?:whole\s+|entire\s+|full\s+)?"
                        r"(?:file|profile|audience|cohort|universe|base)\b|\bof (?:them|those|these)\b", re.I)
_BACKREF_RX = re.compile(r"^\s*(?:that(?:'s|\s+is|\s+comes|\s+works|\s+puts|\s+means)|which\s+is|on\s+(?:a|an|the|this|that|its)\b[^.]{0,60}?\bbase\b|"
                         r"in\s+people|as\s+people|in\s+headcount|put\s+another\s+way)", re.I)
_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_QUICK_RX = re.compile(r"\d,\d{3}|\d{5,}|\d\s?(?:million|billion|bn)\b|\d[MB]\b")
_SCALE = {'million': 1e6, 'm': 1e6, 'billion': 1e9, 'bn': 1e9, 'b': 1e9}
_GENERIC = {'brand', 'brands', 'category', 'audience', 'people', 'this', 'that', 'with', 'least', 'engage', 'total',
            'universe', 'overall', 'group', 'share', 'reach', 'profile', 'projected'}

# an evidence line that carries a share and the people behind it: a profile row
# ('- CHIME | DIGITAL BANKING: 11.7224% of this audience | projected US people 1,052,381')
# or a category's reach ('  insurance: 43.7% of this audience engage ...; projected US people 3,925,084')
_FACT_LINE_RX = re.compile(r"^\s*(?:-\s*)?(?P<label>[^:\n]{1,90}?):\s*(?P<share>\d{1,3}(?:\.\d+)?)%[^\n]{0,200}?"
                           r"[|;]\s*projected US people (?P<n>" + _COUNT + r")", re.M)
_AUDIENCE_RX = re.compile(r"projected US audience (" + _COUNT + r")")


def _num(s):
    try:
        return int(round(float(str(s).replace(',', ''))))
    except (TypeError, ValueError):
        return None


def _dec(pct_text):
    s = str(pct_text)
    return len(s.split('.', 1)[1]) if '.' in s else 0


def _messy(v, salt=''):
    """A recomputed count never ends in a zero; the move is downward."""
    v = int(v)
    if v > 0 and v % 10 == 0:
        v -= 1 + (int(hashlib.sha1(f"{salt}|{v}".encode('utf-8', 'replace')).hexdigest()[:6], 16) % 9)
    return v


def _mentions(s):
    """Every people count a sentence states: {'v', 'a', 'b' (the span of
    the figure), 'raw', 'mod', 'scale', 'part_of'}."""
    out, taken = [], set()
    for m in _OF_THE_RX.finditer(s):
        v, whole = _num(m.group('n')), _num(m.group('m'))
        if v is not None and whole is not None:
            out.append({'v': v, 'a': m.start('n'), 'b': m.end('n'), 'e': m.end('n'), 'raw': m.group('n'), 'mod': '', 'scale': None, 'part_of': whole})
            taken.add(m.start('n'))
    for m in _PEOPLE_RX.finditer(s):
        if m.start('n') in taken:
            continue
        v = _num(m.group('n'))
        if v is not None:
            out.append({'v': v, 'a': m.start('n'), 'b': m.end('n'), 'e': m.end(), 'raw': m.group('n'),
                        'mod': (m.group('mod') or '') + ' ' + m.group('noun'), 'scale': None, 'part_of': None})
            taken.add(m.start('n'))
    for m in _LABEL_FIRST_RX.finditer(s):
        if m.start('n') in taken:
            continue
        v = _num(m.group('n'))
        if v is not None:
            out.append({'v': v, 'a': m.start('n'), 'b': m.end('n'), 'e': m.end('n'), 'raw': m.group('n'), 'mod': '', 'scale': None, 'part_of': None})
            taken.add(m.start('n'))
    for m in _SCALED_RX.finditer(s):
        try:
            mult = _SCALE[m.group('scale').lower()]
            v = int(round(float(m.group('n')) * mult))
        except (KeyError, ValueError):
            continue
        out.append({'v': v, 'a': m.start('n'), 'b': m.end('n'), 'e': m.end(), 'raw': m.group('n'),
                    'mod': (m.group('mod') or '') + ' ' + m.group('noun'), 'scale': mult, 'part_of': None})
    out.sort(key=lambda d: d['a'])
    return out


def _named_base(s):
    """The base a sentence names for its own counts: (value, start, end,
    kind) with kind 'noun' ('a base of M') or 'open' ('Of the M viewers,
    ...'), or None."""
    for m in _ANY_COUNT_RX.finditer(s):
        lead = s[max(0, m.start() - 48):m.start()]
        kind = 'noun' if _BASE_NOUN_RX.search(lead) else ('open' if _BASE_OPEN_RX.search(s[:m.start()]) else '')
        v = _num(m.group(0)) if kind else None
        if v:
            return v, m.start(), m.end(), kind
    return None


def _part_of_base(s, m, base):
    """True when the sentence states the count as a part of the base it
    names: 'a base of M people that is about N', 'Of the M viewers, N
    ...', 'N people, on a base of M'."""
    v, a, b, kind = base
    if m['a'] == a:
        return False
    if m['a'] > b:
        between = s[b:m['a']]
        return bool(_EQUIV_RX.search(between[-48:]) and len(between) <= 90) if kind == 'noun' else bool(_AFTER_OPEN_RX.match(between))
    return kind == 'noun' and bool(_BASE_AFTER_RX.search(s[m['b']:a][-60:] + '')) and len(s[m['b']:a]) <= 90


def _pcts(s):
    return [(m.group(1), float(m.group(1))) for m in _PCT_RX.finditer(s) if float(m.group(1)) <= 100.0]


def _label_tokens(label):
    return {t for t in re.findall(r"[a-z][a-z0-9']{3,}", str(label or '').lower()) if t not in _GENERIC}


def _share_forms(share):
    try:
        f = float(share)
    except (TypeError, ValueError):
        return set()
    return {f"{f:.1f}", f"{f:.2f}", f"{f:.4f}", f"{f:g}"}


def _names(fact, text):
    """True when the text names the figure's own subject."""
    low = str(text or '').lower()
    toks = _label_tokens(fact.get('label'))
    if toks:
        return any(t in low for t in toks)
    label = str(fact.get('label') or '').strip().lower()
    return bool(label) and label in low


def _tied(fact, s, prev, m):
    """True when a count is the same quantity as a figure the answer
    carries: the sentence states the figure's share and names its
    subject, or points back to the sentence that does ('43.7% engage with
    insurance brands. On a base of M people that is N ...')."""
    forms = _share_forms(fact.get('share'))
    if not forms:
        return False
    here = {p for p, _ in _pcts(s)}
    if here & forms:
        return _names(fact, s)
    if prev and (not here or _BACKREF_RX.search(s)) and ({p for p, _ in _pcts(prev)} & forms):
        return _names(fact, s) or _names(fact, prev)
    return False


def _pick_fact(facts, s, prev, m, ceiling=None):
    """The one figure the answer carries for this count, or None (none
    fits, or two fit and differ: neither is asserted)."""
    hits = []
    for f in facts or ():
        p = _num(f.get('people'))
        if not p or p > US_POPULATION or (ceiling and p > ceiling):
            continue
        if _tied(f, s, prev, m):
            hits.append(f)
    if not hits or len({_num(f.get('people')) for f in hits}) > 1:
        return None
    return hits[0]


def _slip(s, prev, m, base, salt=''):
    """The count is the share times the base without the division by 100:
    the same sum, done right; None when it is anything else."""
    if not base:
        return None
    for src in (s, prev or ''):
        for txt, x in _pcts(src):
            if x <= 0:
                continue
            if abs(m['v'] - x * base) <= 0.5 * (10 ** (-_dec(txt))) * base + 2:
                return _messy(round(x * base / 100.0), salt)
        if _pcts(src):
            break
    return None


def _write(m, new):
    if m.get('scale'):
        return f"{new / m['scale']:.{_dec(m['raw'])}f}"
    return f"{int(new):,}" if (',' in m['raw'] or int(new) >= 10000) else str(int(new))


_CLAUSE_LEAD_RX = re.compile(r"\s*(?:,|;|\s-)\s*(?:or\s+|that\s+is\s+|that's\s+|which\s+is\s+)?(?:about\s+|roughly\s+|around\s+|some\s+|nearly\s+)?$", re.I)


def _drop_bracket(s, m):
    """The bracket that holds the count, or the short clause that states
    it (', 392,312,555 people'), removed; None when the count sits in
    neither."""
    a = s.rfind('(', 0, m['a'])
    b = s.find(')', m['b'])
    if a >= 0 and b >= 0 and ')' not in s[a:m['a']]:
        out = s[:a].rstrip() + s[b + 1:]
        return re.sub(r"\s+([,.;:])", r"\1", out)
    lead = _CLAUSE_LEAD_RX.search(s[:m['a']])
    # only a clause that ends where the count's noun ends: nothing is cut out of the middle of a statement
    if lead and lead.start() > 0 and re.match(r"\s*(?:[.,;:!?)]|$)", s[m.get('e', m['b']):]):
        out = s[:lead.start()] + s[m.get('e', m['b']):]
        return re.sub(r"\s+([,.;:])", r"\1", out)
    return None


def check(text, *, audience=None, facts=(), resolver=None, subject=''):
    """The pass. ``audience``: the open audience's projected people.
    ``facts``: [{'label', 'share' (percent), 'people', 'base'}] the answer
    carries. ``resolver``: () -> (facts, audience), called once and only
    when a count has broken a rule and nothing handed in settles it.
    Returns {'text', 'changed', 'fixes', 'dropped', 'unresolved'}."""
    info = {'text': text, 'changed': False, 'fixes': [], 'dropped': [], 'unresolved': []}
    try:
        if not isinstance(text, str) or not text or not _QUICK_RX.search(text):
            return info
        facts = [f for f in (facts or ()) if isinstance(f, dict)]
        audience = _num(audience) if audience else None
        state = {'resolved': resolver is None}
        # a table of figures over the US population is a measure that is not people; a single one is a slip
        over = sum(1 for ln in text.split('\n') for m in _mentions(ln) if m['v'] > US_POPULATION)
        rules = {'us': over < MANY, 'volume': bool(_VOLUME_RX.search(text))}
        if over >= MANY:
            info['unresolved'].append({'was': over, 'why': 'many counts over the US population: a measure that is not people'})
        lines = text.split('\n')
        total = sum(1 for ln in lines for part in _SPLIT_RX.split(ln) if part.strip())
        out_lines = []
        for line in lines:
            if not line.strip() or not _QUICK_RX.search(line):
                out_lines.append(line)
                continue
            parts, pos = [], 0
            for sm in _SPLIT_RX.finditer(line):
                parts.append([line[pos:sm.start()], sm.group(0)])
                pos = sm.end()
            parts.append([line[pos:], ''])
            prev = ''
            kept = []
            for s, sep in parts:
                s_new, dropped = s, False
                for _ in range(6):                     # one count per turn, spans re-read after each edit
                    hit = _first_violation(s_new, audience, rules)
                    if hit is None:
                        break
                    m, why, base = hit
                    if not state['resolved']:
                        state['resolved'] = True
                        try:
                            more, aud2 = resolver() or ((), None)
                            facts += [f for f in (more or ()) if isinstance(f, dict)]
                            audience = audience or (_num(aud2) if aud2 else None)
                        except Exception:
                            traceback.print_exc()
                    ceiling = min(x for x in (US_POPULATION, base or audience) if x)
                    fact = _pick_fact(facts, s_new, prev, m, ceiling)
                    new, how = (_num(fact['people']), 'figure') if fact else (None, '')
                    if new is None:
                        new = _slip(s_new, prev, m, base or audience, subject)
                        how = 'sum'
                        if new is not None and new > ceiling:
                            new = None
                    if new is not None:
                        s_new = s_new[:m['a']] + _write(m, new) + s_new[m['b']:]
                        info['fixes'].append({'was': m['v'], 'now': int(new), 'why': why, 'how': how})
                        continue
                    if total > 1 and len(info['dropped']) < MAX_DROPS:
                        dropped = True
                        info['dropped'].append({'was': m['v'], 'why': why, 'sentence': s.strip()[:240]})
                        break
                    cut = _drop_bracket(s_new, m)
                    if cut is not None and cut.strip():
                        s_new = cut
                        info['dropped'].append({'was': m['v'], 'why': why, 'sentence': '(bracket)'})
                        continue
                    info['unresolved'].append({'was': m['v'], 'why': why + '; nothing to correct it to and no sentence to spare'})
                    break
                if dropped:
                    total -= 1
                    continue
                # the words print the figure the answer carries, never a second sum beside it
                if facts:
                    s_new = _agree(s_new, prev, facts, audience, info)
                kept.append([s_new, sep])
                prev = s_new
            if kept:
                kept[-1][1] = ''
                out_lines.append(''.join(a + b for a, b in kept))
            elif not parts or all(not a.strip() for a, _ in parts):
                out_lines.append(line)
            # a line that held only dropped sentences goes with them
        out = '\n'.join(out_lines)
        if out != text:
            out = re.sub(r'\n{3,}', '\n\n', out).strip('\n') if (info['dropped']) else out
            if out.strip():
                info['text'] = out
                info['changed'] = True
        return info
    except Exception:
        traceback.print_exc()
        return {'text': text, 'changed': False, 'fixes': [], 'dropped': [], 'unresolved': []}


def _first_violation(s, audience, rules=None):
    """(mention, why, base) for the first people count in the sentence
    that breaks rule 1 or 2, or None."""
    ms = _mentions(s)
    if not ms:
        return None
    rules = rules or {'us': True, 'volume': False}
    base = _named_base(s)
    is_global = bool(_GLOBAL_RX.search(s))
    for m in ms:
        part = bool(base) and _part_of_base(s, m, base)
        row_pool = rules.get('volume') and bool(_POOL_ROW_RX.search(s[:m['a']]))
        if m['v'] > US_POPULATION and not is_global and rules.get('us', True) and not row_pool:
            platform = bool(_PLATFORM_NOUN_RX.search(str(m.get('mod') or '').strip()))
            if not platform or part or m.get('part_of') or _US_CUE_RX.search(s):
                return m, 'over the US population', (base[0] if part and base[0] <= US_POPULATION else None)
        if m.get('part_of') and m['v'] > m['part_of']:
            return m, 'over its own base', m['part_of']
        if _OUTSIDE_RX.search(s[max(0, m['a'] - 40):m['b'] + 70]):
            continue
        if part and m['v'] > base[0]:
            return m, 'over its own base', base[0]
        if audience and not base and m['v'] > audience and _INSIDE_RX.search(s):
            return m, 'over the audience', audience
    return None


def _agree(s, prev, facts, audience, info):
    """Rule 3 on one sentence: a count tied to a figure the answer carries,
    and near it, is that figure."""
    for _ in range(4):
        moved = False
        for m in _mentions(s):
            for f in facts:
                p = _num(f.get('people'))
                base = _num(f.get('base')) or audience
                if not p or p == m['v'] or (base and m['v'] == base):
                    continue
                if _pick_fact(facts, s, prev, m) is not f:
                    continue
                try:
                    share = float(f.get('share'))
                except (TypeError, ValueError):
                    share = None
                if base and share is not None:
                    near = abs(m['v'] - share / 100.0 * base) <= 0.001 * base + 9 or abs(m['v'] - p) <= 0.002 * p
                else:
                    near = abs(m['v'] - p) <= 0.02 * p
                if not near:
                    continue
                if m.get('scale') and _write(m, p) == m['raw']:
                    continue          # '3.9 million' is the figure at its own precision
                s = s[:m['a']] + _write(m, p) + s[m['b']:]
                info['fixes'].append({'was': m['v'], 'now': p, 'why': 'not the figure the answer carries', 'how': 'figure'})
                moved = True
                break
            if moved:
                break
        if not moved:
            break
    return s


def facts_from_blocks(blocks):
    """(facts, audience) read off the evidence an answer was written
    from: every line that carries a share with the people behind it, and
    the audience's own projected size."""
    facts, audience = [], None
    try:
        for b in ([blocks] if isinstance(blocks, str) else (blocks or ())):
            t = str(b or '')
            if not t:
                continue
            if audience is None:
                am = _AUDIENCE_RX.search(t)
                if am:
                    audience = _num(am.group(1))
            if 'projected US people' not in t:
                continue
            for m in _FACT_LINE_RX.finditer(t):
                label = m.group('label').split('|')[0].strip(' -')
                p = _num(m.group('n'))
                if label and p:
                    facts.append({'label': label, 'share': float(m.group('share')), 'people': p})
        for f in facts:
            f['base'] = audience
    except Exception:
        traceback.print_exc()
    return facts, audience


def trail(info):
    bits = []
    for f in (info or {}).get('fixes') or []:
        bits.append(f"{f['was']:,} -> {f['now']:,} ({f['why']}; {f['how']})")
    for d in (info or {}).get('dropped') or []:
        bits.append(f"dropped a sentence stating {d['was']:,} ({d['why']})")
    for u in (info or {}).get('unresolved') or []:
        bits.append(f"left {u['was']:,} as written ({u['why']})")
    return '; '.join(bits)


def _log(where, info):
    t = trail(info)
    if t:
        try:
            print(f"[pm-verify] people bounds ({where}): {t[:600]}")
        except Exception:
            pass


def guard_composed(text, blocks):
    """A composed answer against the evidence it was written from: rules
    1 to 3, in place. Returns the text."""
    try:
        if not isinstance(text, str) or not _QUICK_RX.search(text):
            return text
        facts, audience = facts_from_blocks(blocks)
        info = check(text, audience=audience, facts=facts)
        _log('composed', info)
        return info['text'] if info['changed'] else text
    except Exception:
        traceback.print_exc()
        return text


def clean(text):
    """Words alone: rules 1 and 2. Returns the text."""
    try:
        if not isinstance(text, str) or not _QUICK_RX.search(text):
            return text
        info = check(text)
        _log('text', info)
        return info['text'] if info['changed'] else text
    except Exception:
        return text


def _turn_evidence():
    """(facts, audience) for the profile open on this ask: its own rows
    and category reach for what the question names. Only ever called
    after a count has broken a rule. ([], None) off a request or with no
    profile open."""
    try:
        from flask import g, has_request_context, request
        if not has_request_context():
            return [], None
        body = request.get_json(silent=True) or {}
        body = body if isinstance(body, dict) else {}
        ask = str(getattr(g, '_pm_ask_text', '') or body.get('text') or '')
        pc = getattr(g, '_pm_page_ctx', None)
        if not isinstance(pc, dict) or not pc.get('primary'):
            pc = body.get('context') if isinstance(body.get('context'), dict) else body.get('page_context')
        key = str((((pc or {}) if isinstance(pc, dict) else {}).get('primary') or {}).get('s3_key') or '')
        if not key.lower().endswith('.csv'):
            return [], None
        from . import host
        if not (host.has('s3_client') and host.has('bucket')):
            return [], None
        import prometheus_analysis as pma
        df, _ = pma.load_profile_df(host.s3_client, host.bucket, key)
        gp = pma.load_genpop_map(host.s3_client, host.bucket)
        block = pma.build_named_entity_rows(df, gp, ask) or ''
        facts, _aud = facts_from_blocks([block])
        audience = None
        try:
            audience = _num((pma._profile_meta(df, '') or {}).get('proj'))
        except Exception:
            audience = None
        for f in facts:
            f['base'] = audience
        return facts, audience
    except Exception:
        traceback.print_exc()
        return [], None


def guard_payload(raw):
    """A reply payload on its way out (the envelope every chat reply
    passes; a finished read as it lands): the words are checked in place
    and a metric that carried a corrected count follows it. Returns the
    payload."""
    try:
        if not isinstance(raw, dict):
            return raw
        for k in ('reply', 'message', 'summary', 'text'):
            v = raw.get(k)
            if not isinstance(v, str) or not _QUICK_RX.search(v):
                continue
            info = check(v, resolver=_turn_evidence, subject=str(raw.get('profile') or ''))
            if not info['changed']:
                if info['unresolved']:
                    _log('reply', info)
                continue
            raw[k] = info['text']
            raw.setdefault('_scrubbed', []).append(k + ':people')
            _log('reply', info)
            moved = {f['was']: f['now'] for f in info['fixes']}
            for mt in (raw.get('metrics') if isinstance(raw.get('metrics'), list) else []):
                if isinstance(mt, dict) and _num(mt.get('value')) in moved:
                    mt['value'] = moved[_num(mt.get('value'))]
    except Exception:
        traceback.print_exc()
    return raw
