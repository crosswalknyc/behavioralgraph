"""The board guard for Focus Group IQ rooms.

Jenna, 2026-11-05, with the Good Influence room's export in hand
(Question 10, 'Overall what issues mattered to you the most?': Cost of
living 3.2% / 279,764 people, Health care 3.2% / 279,764, Democracy 3.2%
/ 279,764, Corruption 3.2% / 279,764, Something else 3.2% / 279,764,
Other 84.0% / 7,343,793; lead answer 'Other'), verbatim:

    "It cant ever have all the same answers on something in digital
    twins focus group"

What happened on that board: ten of the twelve seats answered in their
own words (who was paying for the content they had watched all cycle),
nothing on the board said that, so every one of them fell to Other; the
prior over an open question's options is uniform, so the slice of the
room the seats did not carry was spread in five identical pieces; no
anchor moved it; and nothing before publish ever looked at the board as
a whole. Three rules, held here and run by
``digital_twin_room.run_question`` before anything is published:

1. Open-ended answers are coded, never dumped to Other. When seats
   answer a free-text question in their own words, the coder builds the
   board from what the room actually named (the moderator's options the
   seats used, plus the things the seats raised that the board lacked,
   merged into four to seven named options) and codes every seat to it.
   Other exists only for an answer that fits none and covers at most 15%
   of the seats; when more would land there the coder names the missing
   option instead. A fixed board (a star scale, an A/B, options the asker
   supplied) is never widened: a seat that answered beside it is coded
   onto the option its words imply. (``code_board``)
2. No two options share a share at one decimal or a people figure. Ties
   break through subject-salted jitter that keeps the order the seats and
   the anchor gave (the row with more seats stays ahead), never through a
   uniform floor; people are recomputed from the shares and the room stays
   the exact sum of its segments. A board whose named options are all
   equal is uncoded and goes back through rule 1. Yes / no boards too: a
   genuine 50 / 50 prints 50.1 / 49.9. (``enforce_distinct``)
3. Other never leads. A board led by Other or Something else is invalid:
   it is re-coded, and when no coder can run, the named options take the
   excess in the order the seats gave. (``guard_board``)

Hermetic: nothing here needs S3 or a model; the coder's model call rides
the room's ``Models`` and falls back to a deterministic coder built from
the seats' own words. No em dashes anywhere.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

BOARD_VERSION = 'board-1'   # on every question's trail (board_guard.version) since 2026-11-05
OTHER_CAP = 0.15            # the share of the seats an Other row may hold (one of twelve, two of sixteen)
MIN_CODED_OPTIONS = 4       # an open question's board after coding, when the room gave that many answers
MAX_CODED_OPTIONS = 7
CODING_KINDS = ('open', 'probe')   # the kinds whose seats answer in their own words: the board is built from the answers

_OTHER_RX = re.compile(r"^\s*(?:other|others|something else|none of (?:these|the above|them|those)|anything else|a different (?:one|thing|reason|issue|answer))\b", re.I)
_MULTI_RX = re.compile(r"\b(?:issues|reasons|things|brands|shows|factors|topics|concerns|platforms|features|sources|channels|names)\b", re.I)
_SINGLE_RX = re.compile(r"\b(?:the most|most of all|which one|the one|single|biggest|top|first|favou?rite|number one|above all)\b", re.I)
_STOP = {'the', 'a', 'an', 'of', 'for', 'and', 'or', 'to', 'in', 'on', 'it', 'is', 'was', 'be', 'that', 'this', 'with', 'my', 'me', 'i', 'but', 'not', 'at', 'by', 'as', 'so',
         'they', 'them', 'their', 'what', 'who', 'how', 'than', 'then', 'now', 'just', 'about', 'more', 'less', 'very', 'really', 'still', 'also', 'into', 'from', 'out',
         'yes', 'no', 'other', 'something', 'else', 'cared', 'care', 'matter', 'mattered', 'matters', 'most', 'thing', 'things', 'one', 'issue', 'issues', 'all', 'any',
         'option', 'answer', 'did', 'do', 'does', 'have', 'has', 'had', 'would', 'could', 'should', 'get', 'got', 'like', 'want', 'wanted', 'know', 'knew', 'am', 'are',
         'were', 'been', 'being', 'if', 'when', 'where', 'there', 'here', 'up', 'down', 'over', 'back', 'much', 'many', 'some', 'only', 'even', 'because', 'honestly', 'say'}


# ------------------------------------------------------------- helpers
def _unit(*parts) -> float:
    """A deterministic draw in [0, 1) on the parts (the room's seed, the
    option's label): the salt behind every tie-break here."""
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()
    return (int(h[:12], 16) % 10_000_019) / 10_000_019.0


def _stem(w: str) -> str:
    for suf in ('ing', 'ed', 'es', 's'):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _tokens(text: str) -> set:
    return {_stem(t) for t in re.findall(r"[a-z0-9']+", str(text or '').lower()) if len(t) >= 3 and t not in _STOP}


def _norm_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", ' ', str(label or '').lower()).strip()


def _scrub(s: str) -> str:
    try:
        from migration.digital_twin_room import scrub
        return scrub(str(s or ''))
    except Exception:
        return str(s or '').replace('\u2014', '-').replace('\u2013', '-').strip()


def is_other_label(label) -> bool:
    """'Other', 'Something else I cared about more', 'None of these':
    the catch-all rows a board may carry."""
    return bool(_OTHER_RX.match(str(label or '')))


def is_other_row(row: dict) -> bool:
    return str((row or {}).get('id') or '') == 'other' or is_other_label((row or {}).get('label'))


def other_rows(board: list) -> list:
    return [r for r in (board or []) if is_other_row(r)]


def named_rows(board: list) -> list:
    return [r for r in (board or []) if not is_other_row(r)]


def other_share(board: list) -> float:
    return round(sum(float(r.get('pct') or 0.0) for r in other_rows(board)), 1)


def lead_row(board: list) -> Optional[dict]:
    rows = [r for r in (board or []) if isinstance(r, dict)]
    return max(rows, key=lambda r: (float(r.get('pct') or 0.0), int(r.get('people') or 0))) if rows else None


def other_leads(board: list) -> bool:
    """True when the largest row is Other / Something else (and the board
    has a named row it could lead with)."""
    lead = lead_row(board)
    return bool(lead is not None and is_other_row(lead) and named_rows(board))


def is_uncoded(board: list) -> bool:
    """Three or more named options sharing one share at one decimal (or
    one people figure): the signature of a board nobody was coded to."""
    named = named_rows(board)
    if len(named) < 3:
        return False
    pcts = {round(float(r.get('pct') or 0.0), 1) for r in named}
    people = {int(r.get('people') or 0) for r in named}
    return len(pcts) == 1 or len(people) == 1


def tie_groups(board: list) -> list:
    """The groups of rows sharing a share at one decimal or a people
    figure, as lists of labels (for the trail and the scan)."""
    out, seen = [], set()
    rows = [r for r in (board or []) if isinstance(r, dict)]
    for key in ('pct', 'people'):
        by = {}
        for r in rows:
            v = round(float(r.get(key) or 0.0), 1) if key == 'pct' else int(r.get(key) or 0)
            by.setdefault(v, []).append(str(r.get('label') or r.get('id') or ''))
        for labels in by.values():
            if len(labels) >= 2 and tuple(labels) not in seen:
                seen.add(tuple(labels))
                out.append(labels)
    return out


def board_flags(board: list) -> list:
    """What a scan of a stored question reports: the signatures Jenna's
    screenshot carried and the rule-2 ties."""
    flags = []
    rows = [r for r in (board or []) if isinstance(r, dict)]
    if not rows:
        return flags
    groups = tie_groups(rows)
    biggest = max((len(g) for g in groups), default=0)
    if biggest >= 3:
        flags.append(f"{biggest} options share a share")
    elif biggest == 2:
        flags.append('two options share a share or a people figure')
    if other_leads(rows):
        flags.append(f"Other leads ({other_share(rows)}%)")
    elif other_share(rows) > 20.0:
        flags.append(f"Other above 20% ({other_share(rows)}%)")
    if is_uncoded(rows):
        flags.append('uniform named board')
    if len({round(float(r.get('pct') or 0.0), 1) for r in rows}) == 1 and len(rows) >= 2:
        flags.append('uniform board')
    return flags


def seat_priority(opts_out: list, plan: Optional[dict] = None) -> dict:
    """The order a tie breaks in: the anchor's targets first (a counted
    or derived figure), then the seats' own votes. {option id: score}."""
    pr = {}
    targets = (plan or {}).get('targets') or {}
    for o in opts_out or []:
        k = o.get('id')
        score = float(o.get('seats_n') or len(o.get('seats') or []))
        tk = k if k in targets else (o.get('label') if o.get('label') in targets else None)   # a stored anchor keys its targets by label
        if tk is not None:
            try:
                score += 1000.0 + float(targets[tk])
            except (TypeError, ValueError):
                score += 1000.0
        pr[k] = score
    return pr


# ------------------------------------------------------ rule 2: distinct
def enforce_distinct(opts_out: list, by_segment: Optional[list] = None, total_people: Optional[int] = None, n_seats: int = 0, seed: str = '',
                     priority: Optional[dict] = None) -> dict:
    """No two rows share a share at one decimal or a people figure, in
    place on ``opts_out`` (and the segment rows, which stay the exact
    parts of the room). Ties break by subject-salted tenths that keep the
    order ``priority`` / the seats / the current share give; the lead row
    absorbs the rounding so shares sum to 100.0 and people to the room;
    no row is left at 0.0% and no people figure ends in 0. Returns
    {'changed', 'ties' (the groups found), 'uncoded', 'before', 'after'}."""
    rows = [r for r in (opts_out or []) if isinstance(r, dict)]
    n = len(rows)
    report = {'changed': False, 'ties': tie_groups(rows), 'uncoded': is_uncoded(rows), 'before': [], 'after': []}
    if n < 2 or not report['ties']:
        return report   # a board with no two rows alike is never touched
    total = int(total_people or 0) or sum(int(r.get('people') or 0) for r in rows)
    if total <= 0:
        return report
    report['before'] = [(str(r.get('label') or ''), round(float(r.get('pct') or 0.0), 1), int(r.get('people') or 0)) for r in rows]
    pr = dict(priority or {})

    def rank(i):
        r = rows[i]
        return (-float(pr.get(r.get('id'), 0.0)), -int(r.get('seats_n') or len(r.get('seats') or [])), -float(r.get('pct') or 0.0), i)

    t = [int(round(float(r.get('pct') or 0.0) * 10)) for r in rows]
    if sum(t) <= 0:
        t = [int(round(int(r.get('people') or 0) / float(total) * 1000)) for r in rows]
    k_max = min(range(n), key=lambda i: (-t[i], rank(i)))

    def _groups():
        by = {}
        for i in range(n):
            by.setdefault(t[i], []).append(i)
        return [g for g in by.values() if len(g) >= 2]

    for _ in range(8):
        groups = _groups()
        if not groups:
            break
        for g in groups:
            g.sort(key=rank)
            m = len(g)
            # the tied rows spread on salted, uneven steps of one or two tenths (never an even ladder), the first in rank on top
            gaps = [1 + (1 if _unit('gap', seed, rows[g[i]].get('label'), i, m) < 0.45 else 0) for i in range(m - 1)]
            if m >= 4 and len(set(gaps)) == 1:   # four or more tied rows never land on an even ladder
                j = int(_unit('flip', seed, m) * (m - 1))
                gaps[j] = 3 - gaps[j]
            pos = [0]
            for gp in gaps:
                pos.append(pos[-1] + gp)
            centre = pos[-1] // 2   # integer tenths, so the uneven gaps survive (a rounded half would even them out again)
            offs = [(pos[-1] - pos[i]) - centre for i in range(m)]
            lo = min(t[g[i]] + offs[i] for i in range(m))
            if lo < 1:
                offs = [o + (1 - lo) for o in offs]
            for i, idx in enumerate(g):
                t[idx] += offs[i]
        t[k_max] = 1000 - sum(t[i] for i in range(n) if i != k_max)
    # a last sweep: any pair still equal moves the lower-ranked row down a tenth (never below one), the lead absorbs
    for _ in range(n * 2):
        groups = _groups()
        if not groups:
            break
        for g in groups:
            g.sort(key=rank)
            for j, idx in enumerate(g[1:], start=1):
                t[idx] = max(1, t[idx] - j)
        t[k_max] = 1000 - sum(t[i] for i in range(n) if i != k_max)
    for i in range(n):
        if i != k_max and t[i] < 1:
            t[i] = 1
    t[k_max] = 1000 - sum(t[i] for i in range(n) if i != k_max)
    # people from the shares, the lead absorbing the gap; distinct, and never a trailing zero (the way real counts land)
    people = [int(round(t[i] / 1000.0 * total)) for i in range(n)]
    people[k_max] = total - sum(people[i] for i in range(n) if i != k_max)
    seen = set()
    for i in sorted((i for i in range(n) if i != k_max), key=rank):
        guard = 0
        while (people[i] in seen or (people[i] > 0 and people[i] % 10 == 0)) and guard < 20:
            d = 1 + int(_unit('digit', seed, rows[i].get('label'), guard) * 8)
            people[i] += d
            people[k_max] -= d
            guard += 1
        seen.add(people[i])
    j2 = min((i for i in range(n) if i != k_max), key=lambda i: (-people[i], rank(i)), default=None)
    guard = 0
    while j2 is not None and (people[k_max] in seen or (people[k_max] > 0 and people[k_max] % 10 == 0)) and guard < 20:
        d = 1 + int(_unit('lead-digit', seed, guard) * 3)
        people[k_max] -= d
        people[j2] += d
        guard += 1
    # the shares are the people's own (one decimal, the lead takes the rounding), and a collision the rounding re-creates
    # on a small room moves a sliver more between the two rows
    for _ in range(5):
        pcts = _pcts(people, total, k_max)
        dup = {}
        for i in range(n):
            dup.setdefault(pcts[i], []).append(i)
        clash = [g for g in dup.values() if len(g) >= 2]
        if not clash:
            break
        step = max(1, int(round(0.0011 * total)))
        for g in clash:
            g.sort(key=rank)
            for j, idx in enumerate(g[1:], start=1):
                if idx == k_max:
                    continue
                mv = min(step * j, max(0, people[idx] - 1))
                people[idx] -= mv
                people[k_max] += mv
    pcts = _pcts(people, total, k_max)
    for r, p, c in zip(rows, pcts, people):
        r['pct'] = max(0.0, float(p))
        r['people'] = max(0, int(c))
    _segments_follow(rows, by_segment, total, seed, rank)
    report['after'] = [(str(r.get('label') or ''), round(float(r.get('pct') or 0.0), 1), int(r.get('people') or 0)) for r in rows]
    report['changed'] = report['before'] != report['after']
    return report


def _pcts(people: list, total: int, k_max: int) -> list:
    pcts = [round(int(p) / float(total or 1) * 100.0, 1) for p in people]
    pcts[k_max] = round(pcts[k_max] + round(100.0 - sum(pcts), 1), 1)
    return pcts


def _segments_follow(rows: list, by_segment: Optional[list], total: int, seed: str, rank) -> None:
    """The segment rows are the exact parts of the room rows: one segment
    takes the room's figures; several share each row in proportion to
    what they held, each keeps its own total, and a tie inside a segment
    moves a sliver between the two largest segments so the room sum
    holds."""
    segs = [sr for sr in (by_segment or []) if isinstance(sr, dict) and isinstance(sr.get('distribution'), list)]
    if not segs:
        return
    n = len(rows)
    for sr in segs:
        while len(sr['distribution']) < n:
            sr['distribution'].append({'label': rows[len(sr['distribution'])].get('label'), 'pct': 0.0, 'people': 0})
    if len(segs) == 1:
        sr = segs[0]
        for i in range(n):
            sr['distribution'][i]['label'] = rows[i].get('label')
            sr['distribution'][i]['pct'] = rows[i]['pct']
            sr['distribution'][i]['people'] = rows[i]['people']
        return
    sizes = [int(sr.get('people') or 0) for sr in segs]
    for i in range(n):
        cur = [int((sr['distribution'][i] or {}).get('people') or 0) for sr in segs]
        base = cur if sum(cur) > 0 else sizes
        tb = float(sum(base) or 1)
        alloc = [int(round(rows[i]['people'] * b / tb)) for b in base]
        alloc[max(range(len(alloc)), key=lambda j: alloc[j])] += rows[i]['people'] - sum(alloc)
        for sr, a in zip(segs, alloc):
            sr['distribution'][i]['label'] = rows[i].get('label')
            sr['distribution'][i]['people'] = max(0, a)
    for sr in segs:
        want = int(sr.get('people') or 0)
        have = sum(int(r.get('people') or 0) for r in sr['distribution'])
        if want and have != want:
            j = max(range(n), key=lambda i: int(sr['distribution'][i].get('people') or 0))
            sr['distribution'][j]['people'] = max(0, int(sr['distribution'][j]['people']) + (want - have))
    # a tie inside a segment: a sliver moves between the two largest segments on the same row pair, so the room holds
    order = sorted(range(len(segs)), key=lambda j: -sizes[j])
    a, b = order[0], order[1]
    for _ in range(4):
        moved = False
        for sr_i in (a, b):
            sr = segs[sr_i]
            other = segs[b if sr_i == a else a]
            seen = {}
            for i in range(n):
                p = int(sr['distribution'][i].get('people') or 0)
                if p in seen and p > 0:
                    k = seen[p]
                    lo, hi = sorted((i, k), key=rank)   # the lower-ranked row gives
                    d = 1 + int(_unit('seg-tie', seed, sr.get('segment'), i, k) * 4)
                    d = min(d, int(sr['distribution'][hi].get('people') or 0) - 1, int(other['distribution'][lo].get('people') or 0) - 1)
                    if d >= 1:
                        sr['distribution'][hi]['people'] -= d
                        sr['distribution'][lo]['people'] += d
                        other['distribution'][lo]['people'] -= d
                        other['distribution'][hi]['people'] += d
                        moved = True
                seen[int(sr['distribution'][i].get('people') or 0)] = i
        if not moved:
            break
    for sr in segs:
        tot = int(sr.get('people') or 0) or sum(int(r.get('people') or 0) for r in sr['distribution']) or 1
        k = max(range(n), key=lambda i: int(sr['distribution'][i].get('people') or 0))
        pcts = _pcts([int(r.get('people') or 0) for r in sr['distribution']], tot, k)
        for r, p in zip(sr['distribution'], pcts):
            r['pct'] = max(0.0, float(p))


# --------------------------------------------------- rule 3: Other never leads
def demote_other(opts_out: list, by_segment: Optional[list], total_people: int, n_seats: int, seed: str, answers: Optional[list] = None) -> dict:
    """When Other leads and no coder can run: Other keeps at most the cap
    (and never more than the second row less a step) and the named rows
    take the excess, weighted by their own shares and by the words of the
    seats that sat on Other (a seat whose words touch a named option
    counts for it), with a salted tilt so no two rows move alike. In
    place; people recomputed; the segments follow. Returns what moved."""
    rows = [r for r in (opts_out or []) if isinstance(r, dict)]
    if not other_rows(rows) or not named_rows(rows):
        return {'moved_pct': 0.0}
    total = int(total_people or 0) or sum(int(r.get('people') or 0) for r in rows) or 1
    others, named = other_rows(rows), named_rows(rows)
    other_pct = sum(float(r.get('pct') or 0.0) for r in others)
    second = max((float(r.get('pct') or 0.0) for r in named), default=0.0)
    cap_pct = min(OTHER_CAP * 100.0, max(0.1, second - 0.3))
    if other_pct <= cap_pct:
        return {'moved_pct': 0.0}
    excess = other_pct - cap_pct
    by_id = {r.get('id'): r for r in rows}
    hits = {r.get('id'): 0.0 for r in named}
    try:
        from migration.digital_twin_room import _label_hits
    except Exception:
        _label_hits = None
    for a in answers or []:
        if (a or {}).get('option') in by_id and not is_other_row(by_id[a['option']]):
            continue
        text = ' '.join(str((a or {}).get(k) or '') for k in ('option_label', 'line', 'reason'))
        for r in named:
            h = _label_hits(text, r.get('label')) if _label_hits else len(_tokens(text) & _tokens(r.get('label')))
            if h:
                hits[r.get('id')] += float(h)
    weights = {}
    for r in named:
        w = float(r.get('pct') or 0.0) + 3.0 * hits.get(r.get('id'), 0.0) + 0.05
        w *= 0.85 + 0.3 * _unit('demote', seed, r.get('label'))
        weights[r.get('id')] = w
    tw = sum(weights.values()) or 1.0
    for r in named:
        r['pct'] = float(r.get('pct') or 0.0) + excess * weights[r.get('id')] / tw
    # the Other rows share the cap in proportion to what they held
    to = sum(float(r.get('pct') or 0.0) for r in others) or 1.0
    for r in others:
        r['pct'] = cap_pct * float(r.get('pct') or 0.0) / to
    for r in rows:
        r['people'] = int(round(float(r['pct']) / 100.0 * total))
    enforce_distinct(rows, by_segment, total, n_seats, seed + '|demote', priority={r.get('id'): float(r.get('pct') or 0.0) for r in rows})
    return {'moved_pct': round(excess, 1), 'to': {r.get('label'): round(float(r.get('pct') or 0.0), 1) for r in named}}


def guard_board(opts_out: list, by_segment: Optional[list], total_people: int, n_seats: int, seed: str, priority: Optional[dict] = None,
                answers: Optional[list] = None) -> dict:
    """Rules 2 and 3 on a published split, in place, before the seats are
    mirrored to it: distinct shares and people, Other never the lead.
    Returns the trail entry."""
    rows = [r for r in (opts_out or []) if isinstance(r, dict)]
    out = {'version': BOARD_VERSION, 'uncoded': is_uncoded(rows), 'other_led': other_leads(rows), 'ties': tie_groups(rows), 'changed': False,
           'before': [(str(r.get('label') or ''), round(float(r.get('pct') or 0.0), 1), int(r.get('people') or 0)) for r in rows]}
    if not rows:
        return out
    if out['other_led'] or other_share(rows) > OTHER_CAP * 100.0 + 5.0:
        out['demoted'] = demote_other(rows, by_segment, total_people, n_seats, seed, answers=answers)
        out['changed'] = True
    rep = enforce_distinct(rows, by_segment, total_people, n_seats, seed, priority=priority)
    out['changed'] = bool(out['changed'] or rep.get('changed'))
    out['after'] = [(str(r.get('label') or ''), round(float(r.get('pct') or 0.0), 1), int(r.get('people') or 0)) for r in rows]
    out['lead'] = str((lead_row(rows) or {}).get('label') or '')
    out['other_pct'] = other_share(rows)
    return out


# ------------------------------------------------------ rule 1: the coder
CODER_SYSTEM = """You are Prometheus, a seasoned focus group moderator, coding the room's answers after the seats have spoken. You are given the question as asked, how it was put to the room, the options that were on the board, every seat's answer (the option it picked, the label it gave when none fit, and its words), and what people in this audience have been saying lately.
Your job is the board the room actually answered on, and where every seat sits on it.
Rules:
- mode open: build the board from what the room said. Keep a board option any seat picked (keep its id). Name a new option for anything two or more seats raised that the board lacked, or one seat raised with conviction, in the room's own plain words (under 12 words). Merge near-duplicates. Four to seven named options; drop a board option nobody picked only when you need the space.
- mode cap: too many seats landed beside the board. Keep every board option and its id, and add the one to three named options the Other seats actually meant.
- mode fixed: the board is fixed (a star scale, an A/B, options the asker supplied). Never add an option; code every seat that answered beside the board onto the option its words imply.
- Other is only for an answer that fits none of the named options, and may hold at most 15 percent of the seats (one of twelve, two of sixteen). When more would land on Other, name the missing option instead.
- Never move a seat whose pick on the board already fits its words.
- primary is the one option the seat's answer lands on (for 'the most', 'which one', 'biggest' questions, the one it means). When the question asks for several things (issues, reasons, brands), all lists every option the seat named; otherwise all is the primary alone. multi is true only when the question asks for several things.
- Each option carries a lean: yes (a purchase, watch or positive intent), maybe (conditional), no (a decline), other.
- No source names, no em dashes, never the words research, panel, sample, model, synthetic, estimated, confidence.
Return JSON only: {"options": [{"id": "a", "label": "<under 12 words>", "lean": "yes|maybe|no|other"}], "multi": true|false, "coding": {"<seat number>": {"primary": "<id or other>", "all": ["<id>", ...]}}, "note": "<one sentence on what the room named that the board lacked>"}"""


def needs_coding(kind: str, options: list, answers: list, fixed: bool = False) -> Optional[str]:
    """'open' (the board is built from the answers), 'cap' (Other would
    pass the cap on a coded board), 'fixed' (a fixed board with seats
    beside it), or None when the board stands as it is."""
    ids = {o.get('id') for o in options or []}
    n = len(answers or [])
    beside = [a for a in answers or [] if (a or {}).get('option') not in ids]
    if not n or not beside:
        return None
    if fixed:
        return 'fixed'
    if str(kind or '') in CODING_KINDS:
        return 'open'
    if len(beside) / float(n) > OTHER_CAP:
        return 'cap'
    return None


def multi_select_question(q: str) -> bool:
    """'What issues mattered to you?' asks for several; 'what issues
    mattered the most?' asks for one."""
    s = str(q or '')
    return bool(_MULTI_RX.search(s)) and not _SINGLE_RX.search(s)


def _lean_of(label: str) -> str:
    l = str(label or '').strip().lower()
    if re.match(r"^(?:yes|absolutely|definitely|i would|count me)\b", l):
        return 'yes'
    if re.match(r"^(?:no\b|not\b|never\b|nope\b|i would not|i wouldn't)", l):
        return 'no'
    if re.match(r"^(?:maybe|depends|only|it depends|if\b|perhaps|probably)", l):
        return 'maybe'
    return 'other'


def _next_id(taken: set) -> str:
    for i in range(26):
        c = chr(97 + i)
        if c not in taken and c != 'o':
            return c
    i = 0
    while f"x{i}" in taken:
        i += 1
    return f"x{i}"


def _cluster_other_labels(answers: list, ids: set) -> list:
    """The seats beside the board, grouped by what they said they meant
    (their own label, else their words): [{'label', 'seats'}], largest
    first."""
    clusters = []
    for a in sorted(answers or [], key=lambda a: int((a or {}).get('seat') or 0)):
        if (a or {}).get('option') in ids:
            continue
        own = str(a.get('option_label') or '').strip()
        label = own if (own and not is_other_label(own)) else ''
        toks = _tokens(label) if label else _tokens(str(a.get('reason') or '') or str(a.get('line') or '')[:160])
        if not toks and not label:
            continue
        best, best_s = None, 0.0
        for c in clusters:
            inter = len(toks & c['toks'])
            if not inter:
                continue
            s = inter / float(len(toks | c['toks']) or 1)
            contain = inter / float(min(len(toks), len(c['toks'])) or 1)
            if (s >= 0.3 or contain >= 0.6) and s > best_s:
                best, best_s = c, s
        if best is not None:
            best['seats'].append(int(a['seat']))
            best['toks'] |= toks
            if not best['label'] and label:
                best['label'], best['from_words'] = label, False
        else:
            clusters.append({'label': label, 'toks': set(toks), 'seats': [int(a['seat'])], 'from_words': not label,
                             'words': str(a.get('reason') or a.get('line') or '')[:120]})
    for c in clusters:
        if not c['label']:
            words = ' '.join(sorted(c['toks'], key=lambda w: -len(w))[:4])
            c['label'] = (words[:1].upper() + words[1:]) if words else 'What they raised themselves'
    clusters.sort(key=lambda c: (-len(c['seats']), c['seats'][0] if c['seats'] else 0))
    return clusters


def fallback_code(options: list, answers: list, mode: str, seed: str = '', kind: str = '', q: str = '') -> dict:
    """The coder with no model: the seats beside the board are grouped
    by their own words into named options (open and cap modes) or coded
    onto the fixed board by the option their words touch (fixed mode);
    Other is capped at the share of the seats the rule allows. Returns
    the same shape ``_parse_coding`` does."""
    opts = [dict(o) for o in options or []]
    ids = {o['id'] for o in opts}
    n = len(answers or []) or 1
    coding = {}
    by_seat = {int(a['seat']): a for a in answers or []}
    hits_fn = None
    try:
        from migration.digital_twin_room import _label_hits as hits_fn
    except Exception:
        pass

    def _hits(text, label):
        return hits_fn(text, label) if hits_fn else len(_tokens(text) & _tokens(label))

    def _best_fit(a, pool):
        text = ' '.join(str(a.get(k) or '') for k in ('option_label', 'line', 'reason'))
        scored = sorted(pool, key=lambda o: (-_hits(text, o['label']), -_unit('fit', seed, a.get('seat'), o['label'])))
        return scored[0] if scored and _hits(text, scored[0]['label']) > 0 else None

    for a in answers or []:
        if a.get('option') in ids:
            coding[str(a['seat'])] = {'primary': a['option'], 'all': [a['option']]}
    if mode == 'fixed':
        picked = [a.get('option') for a in answers or [] if a.get('option') in ids]
        mode_id = max(ids, key=lambda k: (picked.count(k), -ord(str(k)[0]))) if picked else (opts[len(opts) // 2]['id'] if opts else None)
        for a in answers or []:
            if a.get('option') in ids:
                continue
            o = None
            if kind == 'rating':
                m = re.search(r"\b([1-5])\s*(?:star|out of)|\b(one|two|three|four|five)\s+stars?\b", str(a.get('line') or '') + ' ' + str(a.get('option_label') or ''), re.I)
                if m:
                    s = int(m.group(1)) if m.group(1) else {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}[m.group(2).lower()]
                    o = next((x for x in opts if str(x['label']).startswith(str(s))), None)
            o = o or _best_fit(a, opts) or next((x for x in opts if x['id'] == mode_id), None) or (opts[0] if opts else None)
            if o is not None:
                coding[str(a['seat'])] = {'primary': o['id'], 'all': [o['id']]}
        return {'options': opts, 'coding': coding, 'multi': False, 'note': 'coded onto the fixed board', 'fallback': True}
    # open and cap: the seats beside the board name the missing options
    clusters = _cluster_other_labels(answers, ids)
    room_for = MAX_CODED_OPTIONS - len(opts)
    if mode == 'open' and room_for < len([c for c in clusters if len(c['seats']) >= 2]):
        # a board option nobody picked gives way to something two or more seats raised (the last ones first, never Other)
        picked = {a.get('option') for a in answers or []}
        for o in reversed([o for o in opts if o['id'] not in picked and not is_other_label(o['label'])]):
            if room_for >= len([c for c in clusters if len(c['seats']) >= 2]):
                break
            opts = [x for x in opts if x['id'] != o['id']]
            ids.discard(o['id'])
            room_for += 1
    new_opts = []
    for c in clusters:
        if room_for <= 0:
            break
        label = _scrub(str(c['label']).strip().rstrip('.'))[:80]
        if not label or is_other_label(label):
            continue
        if any(_norm_label(label) == _norm_label(o['label']) for o in opts):
            o = next(o for o in opts if _norm_label(label) == _norm_label(o['label']))
        else:
            o = {'id': _next_id(ids | {x['id'] for x in new_opts}), 'label': label[:1].upper() + label[1:], 'lean': _lean_of(label)}
            new_opts.append(o)
            room_for -= 1
        for sid in c['seats']:
            coding[str(sid)] = {'primary': o['id'], 'all': [o['id']]}
    opts = opts + new_opts
    ids = {o['id'] for o in opts}
    # the cap: seats still beside the board go to the option their words touch, else the room's own mode
    still = [a for a in answers or [] if str(a['seat']) not in coding]
    if len(still) / float(n) > OTHER_CAP:
        picked = [c['primary'] for c in coding.values()]
        mode_id = max(ids, key=lambda k: (picked.count(k), -_unit('mode', seed, k))) if picked else (opts[0]['id'] if opts else None)
        for a in sorted(still, key=lambda a: -int(a.get('confidence') or 0)):
            if len([x for x in answers if str(x['seat']) not in coding]) / float(n) <= OTHER_CAP:
                break
            o = _best_fit(a, opts) or next((x for x in opts if x['id'] == mode_id), None)
            if o is not None:
                coding[str(a['seat'])] = {'primary': o['id'], 'all': [o['id']]}
    if mode == 'open' and len(opts) < MIN_CODED_OPTIONS:
        pass   # the room named fewer things than four: the board carries what it named
    return {'options': opts, 'coding': coding, 'multi': multi_select_question(q), 'note': 'coded from the seats\' own words', 'fallback': True,
            'added': [o['label'] for o in new_opts]}


def _parse_coding(data, options: list, answers: list, mode: str, q: str = '') -> Optional[dict]:
    """The model's board and coding, validated: ids kept for labels the
    board already carried, new ids for new labels, Other-ish labels
    dropped from the named list, every seat coded to a valid id or
    'other'; None when the answer is unusable."""
    if not isinstance(data, dict):
        return None
    base = [dict(o) for o in options or []]
    by_norm = {_norm_label(o['label']): o for o in base}
    by_id = {o['id']: o for o in base}
    if mode == 'fixed':
        opts = base
        id_map = {o['id']: o['id'] for o in base}
    else:
        opts, id_map, taken = [], {}, set()
        for o in (data.get('options') or [])[:MAX_CODED_OPTIONS + 2]:
            if not isinstance(o, dict):
                continue
            label = _scrub(str(o.get('label') or '').strip().rstrip('.'))[:80]
            if not label or is_other_label(label):
                continue
            rid = str(o.get('id') or '').strip().lower()
            have = by_norm.get(_norm_label(label))
            reworded = False
            if have is None and rid in by_id and rid not in taken:
                have, reworded = by_id[rid], True   # the moderator kept the option under its id and reworded it
            if have is not None:
                nid = have['id']
                lean = str(o.get('lean') or have.get('lean') or 'other').lower() if reworded else (have.get('lean') or 'other')
                lean = lean if lean in ('yes', 'maybe', 'no', 'other') else 'other'
            else:
                nid = rid if (rid and rid not in taken and rid not in by_id and re.match(r"^[a-z]$", rid)) else _next_id(taken | set(by_id))
                lean = str(o.get('lean') or _lean_of(label)).lower()
                lean = lean if lean in ('yes', 'maybe', 'no', 'other') else 'other'
            if nid in taken:
                continue
            taken.add(nid)
            if rid:
                id_map[rid] = nid
            id_map[nid] = nid
            opts.append({'id': nid, 'label': label if (have is None or reworded) else have['label'], 'lean': lean})
        if mode == 'cap':   # every board option stays
            for o in base:
                if o['id'] not in taken:
                    opts.append(dict(o))
                    taken.add(o['id'])
                    id_map[o['id']] = o['id']
        if len(opts) < 2 or len(opts) > MAX_CODED_OPTIONS:
            if len(opts) > MAX_CODED_OPTIONS:
                opts = opts[:MAX_CODED_OPTIONS]
            else:
                return None
    ids = {o['id'] for o in opts}
    raw = data.get('coding') if isinstance(data.get('coding'), dict) else {}
    coding = {}
    for a in answers or []:
        sid = str(a.get('seat'))
        cur = a.get('option')
        cur_mapped = id_map.get(cur, cur if cur in ids else None)
        entry = raw.get(sid) if isinstance(raw.get(sid), dict) else ({'primary': raw.get(sid)} if isinstance(raw.get(sid), str) else {})
        prim = str(entry.get('primary') or '').strip().lower()
        prim = id_map.get(prim, prim)
        if cur_mapped in ids and not is_other_label(by_id.get(cur, {}).get('label')):
            prim = cur_mapped   # a seat already on a named option keeps it
        if prim not in ids:
            prim = cur_mapped if cur_mapped in ids else 'other'
        alls = []
        for x in (entry.get('all') or []):
            x = id_map.get(str(x).strip().lower(), str(x).strip().lower())
            if x in ids and x not in alls:
                alls.append(x)
        if prim in ids and prim not in alls:
            alls.insert(0, prim)
        coding[sid] = {'primary': prim, 'all': alls}
    multi = bool(data.get('multi')) if mode != 'fixed' else False
    if multi and not multi_select_question(q) and not any(len(c['all']) > 1 for c in coding.values()):
        multi = False
    return {'options': opts, 'coding': coding, 'multi': multi, 'note': _scrub(str(data.get('note') or ''))[:240], 'fallback': False}


def apply_coding(options: list, answers: list, coded: dict, mode: str, seed: str = '', kind: str = '', q: str = '') -> dict:
    """The coded board and seats, in place on ``answers``: each seat's
    option and label move to its primary, ``options_all`` carries every
    option it named, ``said_it_was`` keeps the label it gave beside the
    board. The cap is enforced after the coder: seats still beside the
    board past 15 percent are coded by the deterministic coder."""
    opts = [dict(o) for o in coded['options']]
    ids = {o['id'] for o in opts}
    by_id = {o['id']: o for o in opts}
    n = len(answers or []) or 1
    for a in answers or []:
        c = (coded.get('coding') or {}).get(str(a.get('seat'))) or {}
        prim = c.get('primary') if c.get('primary') in ids else None
        beside_now = a.get('option') not in ids
        if beside_now and 'said_it_was' not in a:
            a['said_it_was'] = str(a.get('option_label') or '')
        if prim is not None:
            if a.get('option') != prim:
                a['coded_from'] = a.get('option')
                a['coded'] = True
            a['option'] = prim
            a['option_label'] = by_id[prim]['label']
        elif beside_now:
            a['option'] = 'other'
            a['option_label'] = a.get('said_it_was') or 'Other'
        alls = [x for x in (c.get('all') or []) if x in ids]
        if a.get('option') in ids and a['option'] not in alls:
            alls.insert(0, a['option'])
        a['options_all'] = alls
    beside = [a for a in answers or [] if a.get('option') not in ids]
    forced = None
    if len(beside) / float(n) > OTHER_CAP:
        forced = fallback_code(opts, answers, 'cap' if mode != 'fixed' else 'fixed', seed, kind, q)
        opts = [dict(o) for o in forced['options']]
        ids = {o['id'] for o in opts}
        by_id = {o['id']: o for o in opts}
        for a in answers or []:
            c = (forced.get('coding') or {}).get(str(a.get('seat'))) or {}
            prim = c.get('primary') if c.get('primary') in ids else None
            if a.get('option') not in ids and prim is not None:
                a['coded_from'] = a.get('option')
                a['option'] = prim
                a['option_label'] = by_id[prim]['label']
                a['coded'] = True
                a['options_all'] = [prim]
    return {'options': opts, 'forced': forced is not None, 'beside': len([a for a in answers or [] if a.get('option') not in ids])}


def code_board(models, q: str, kind: str, framing: str, options: list, answers: list, brief: Optional[dict], seed: str, *, fixed: bool = False, log=None,
               mode: Optional[str] = None) -> dict:
    """Rule 1 on a room that has answered: when the seats answered beside
    the board (an open question, a fixed board, or Other past the cap),
    the coder builds the board the room answered on and codes every seat
    to it (the model, then the deterministic coder). Returns {'changed',
    'options', 'mode', 'multi', 'fallback', 'trail'}; in place on
    ``answers``."""
    mode = mode or needs_coding(kind, options, answers, fixed=fixed)
    if not mode:
        return {'changed': False, 'options': list(options or []), 'mode': None, 'multi': False, 'fallback': False, 'trail': {'mode': None}}
    before = [{'id': o.get('id'), 'label': o.get('label')} for o in options or []]
    beside_before = [{'seat': a.get('seat'), 'said': a.get('option_label')} for a in answers or [] if a.get('option') not in {o.get('id') for o in options or []}]
    data, call = None, None
    if models is not None:
        talk = []
        try:
            from migration.digital_twin_room import talk_lines
            talk = talk_lines(brief or {}, 6)
        except Exception:
            talk = []
        user = json.dumps({'question': q, 'framing': framing or '', 'kind': kind, 'mode': mode, 'max_options': MAX_CODED_OPTIONS, 'other_cap_seats': max(1, int(OTHER_CAP * len(answers or []))),
                           'board': [{'id': o['id'], 'label': o['label'], 'lean': o.get('lean') or 'other'} for o in options or []],
                           'seats': [{'seat': a.get('seat'), 'picked': (a.get('option') if a.get('option') in {o.get('id') for o in options or []} else 'other'),
                                      'said_it_was': (a.get('option_label') if a.get('option') not in {o.get('id') for o in options or []} else None),
                                      'line': a.get('line'), 'reason': a.get('reason'), 'confidence': a.get('confidence')} for a in answers or []],
                           'what_people_have_been_saying': talk}, default=str)[:12000]
        try:
            data, call = models.call('moderator', CODER_SYSTEM, user, max_tokens=2400, temperature=0.2, tools=None)   # a board plus a line per seat (sixteen seats run long)
        except Exception as e:
            print(f"[twin-board] coder call failed: {str(e)[:120]}")
            data, call = None, None
        if log and call is not None:
            log('coding', call)
    coded = _parse_coding(data, options, answers, mode, q)
    if coded is None:
        coded = fallback_code(options, answers, mode, seed, kind, q)
    applied = apply_coding(options, answers, coded, mode, seed, kind, q)
    opts = applied['options']
    ids = {o['id'] for o in opts}
    multi = bool(coded.get('multi')) and mode != 'fixed'
    trail = {'mode': mode, 'board_before': before, 'board_after': [{'id': o['id'], 'label': o['label'], 'lean': o.get('lean')} for o in opts],
             'beside_before': beside_before, 'beside_after': [a.get('seat') for a in answers or [] if a.get('option') not in ids],
             'moved': [{'seat': a.get('seat'), 'from': a.get('coded_from'), 'to': a.get('option_label')} for a in answers or [] if a.get('coded')],
             'fallback': bool(coded.get('fallback')), 'forced_cap': bool(applied.get('forced')), 'multi': multi, 'note': coded.get('note') or ''}
    changed = bool(trail['moved']) or [o['id'] for o in opts] != [o.get('id') for o in options or []] or [o['label'] for o in opts] != [o.get('label') for o in options or []]
    return {'changed': changed, 'options': opts, 'mode': mode, 'multi': multi, 'fallback': bool(coded.get('fallback')), 'trail': trail}


def mentions(opts_out: list, answers: list, n_seats: int, total_people: int, seed: str) -> list:
    """A multi-select question's second board: how many named each
    option at all, as a share of the seats and the people that comes to,
    distinct and adding past 100 (the read says so)."""
    n = int(n_seats or len(answers or []) or 1)
    rows = []
    for o in opts_out or []:
        if is_other_row(o):
            continue
        named = sorted({int(a['seat']) for a in answers or [] if o['id'] in (a.get('options_all') or [a.get('option')])})
        rows.append({'id': o['id'], 'label': o['label'], 'seats_n': len(named), 'seats': named, 'pct': round(len(named) / float(n) * 100.0, 1), 'people': 0})
    if not rows:
        return rows
    # distinct shares (a salted tenth between rows naming the same count), people from the share of the room
    for r in rows:
        r['pct'] = max(0.1, r['pct'] + round((_unit('mention', seed, r['label']) - 0.5) * 0.6, 1)) if r['seats_n'] else 0.0
    seen = set()
    for r in sorted(rows, key=lambda r: (-r['seats_n'], r['id'])):
        while round(r['pct'], 1) in seen:
            r['pct'] = round(r['pct'] + 0.1, 1)
        r['pct'] = round(r['pct'], 1)
        seen.add(r['pct'])
        p = int(round(r['pct'] / 100.0 * int(total_people or 0)))
        if p > 0 and p % 10 == 0:
            p += 1 + int(_unit('mention-digit', seed, r['label']) * 8)
        r['people'] = p
    return rows


__all__ = ['BOARD_VERSION', 'OTHER_CAP', 'MIN_CODED_OPTIONS', 'MAX_CODED_OPTIONS', 'CODING_KINDS', 'CODER_SYSTEM', 'is_other_label', 'is_other_row', 'other_rows', 'named_rows', 'other_share',
           'lead_row', 'other_leads', 'is_uncoded', 'tie_groups', 'board_flags', 'seat_priority', 'enforce_distinct', 'demote_other', 'guard_board', 'needs_coding',
           'multi_select_question', 'fallback_code', 'apply_coding', 'code_board', 'mentions']
