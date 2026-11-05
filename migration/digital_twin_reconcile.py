"""The scorecard reconciles with what the room said out loud (Jenna, 2026-11-04).

Jenna, verbatim: "How is this aligned with when I asked the group if
jennifer beals would make them watch more and they said yes and they all
loved her. so why is her rating so low and follow her into next episode
so low? she should have a rating higher than scott caan and inline with
LL Cool J based on just that. update these metrics. she was a big draw"

The NCIS: New York pilot room, asked at 43:13 what would bring it back,
named Jennifer Beals ten seats of sixteen. The characters card still read
her at 25.2% excellent with 9.8% following her into a second episode: the
card was inferred from each seat's dial while she was on screen (her
scenes were the quiet ops-room stretch) plus a lift for time on screen
that the leads earned and she did not, and it never read the room's own
answers. Screen time is a fact the card shows; it is never a weight on
appeal. Appeal comes from the seats' reactions in a character's scenes
(the dial, the laughs and leans, the remarks) and from what the seats say
when asked.

The rule. When a question the room finished bears on a scorecard measure,
the seats' explicit answers outrank the inferred score and the measure is
recomputed from them:

  a question about watching more / coming back / a second episode whose
  options name characters       -> who they would follow into a second
                                   episode, and the named characters'
                                   appeal (a seat who names a character as
                                   the reason to come back rates that
                                   character at least as high as anyone
                                   else in the cast, never below good)
  a question naming a character  -> that character's appeal (yes: at least
  or a pair                         excellent; maybe: at least good; no: at
                                   most fair); a pair question also rates
                                   the key relationship
  a keep-watching, would-you-     -> tune_in, finish, definitely_recommend,
  recommend or excellent-or-not     total_positive on the battery
  question
  a question naming a scene (a   -> the scenes liked best and least
  clock, or the moment it was
  asked at)

When more than half the room names one character as the reason it would
come back, that character rates in line with the room's best-rated
character (within salted tenths): the seats who named her rate her with
the lead, and the aggregate follows. Deterministic, salted, messy digits;
the rating rows sum to 100.0, the follow shares do not (a seat can follow
more than one person, see ``digital_twin_diagnostics.follow_shares``); no
two characters land on the same tenth of excellent, total positive or
likability (the recompute here is ``character_table`` itself, which
settles the rows through ``distinct_cast_tenths``); norms and the
forecast are re-attached; the card carries one plain line ("Updated after
the room's answer at 43:13.") so the change is visible and explainable.

Who they would follow into a second episode (Jenna, 2026-11-04: "this
should be as high as jennifer if not higher for ll cool j ... make him
66.1") is a per-person share of the room, never a split: each seat's
come-back answer (what it named, the lean, its confidence) is recorded
here as ``come_back`` and the shares are derived in the diagnostics from
that record and the seats' own ratings.

Runs when a room question lands (digital_twin_room.run_question and its
in-place landing on the twin), on every read of a test, and when a test
stops. Idempotent per question: a question already applied is skipped.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from migration.digital_twin_engine import VERSION, clamp, jit

RECONCILE_VERSION = 1
GRADES = ('a', 'b', 'c', 'd', 'e')          # excellent, very good, good, fair, poor
_GRADE_RANK = {g: i for i, g in enumerate(GRADES)}
DRAW_WEIGHT = 0.86                         # kept for older readers; the follow is a per-person share since 2026-11-04 and no longer splits a seat's leaning
REMARK_STEP = 1.5                          # one plainly positive or negative remark about a character, in dial points
REMARK_CAP = 3.0

WATCH_MORE_RX = re.compile(r"\b(watch (?:another|a second|the next|one more|more)|another episode|second episode|next episode|episode (?:two|2)|come back|coming back|"
                           r"bring(?:s)? (?:me|you|them|us) back|press play|tune in|keep watching|watch more|watch again)\b", re.I)
HYPOTHETICAL_RX = re.compile(r"\b(would that change|what if|suppose|imagine|instead of)\b", re.I)
RECOMMEND_RX = re.compile(r"\brecommend", re.I)
FINISH_RX = re.compile(r"\b(finish(?:ed)?|watch (?:it )?to the end|see it through|stick with it)\b", re.I)
OVERALL_RX = re.compile(r"\b(how would you rate (?:it|this|that|the (?:show|episode|pilot|cut|film|movie|series))|overall rating|rate (?:it|this|the (?:show|episode|pilot|cut)) overall|"
                        r"was (?:it|this|that) excellent|excellent or not|good or bad)\b", re.I)
KEEP_WATCHING_RX = re.compile(r"\b(would you (?:keep watching|watch (?:another|a second|the next)|come back|tune in|watch episode)|keep watching|watch the next|"
                              r"watch a second|watch another|come back for|tune in again)\b", re.I)
SCENE_WORDS_RX = re.compile(r"\b(this scene|that scene|the scene|this moment|that moment|the moment|what just played|the last minute|the ending|the opening|cold open|"
                            r"the close|the end of the episode|the final scene|the last scene|the first scene)\b", re.I)
CLOCK_RX = re.compile(r"\b(\d{1,2}):(\d{2})\b")
PAIR_RX = re.compile(r"\b(partners?|partnership|the two(?: of them)?|both(?: of them)?|the pair|the duo|duo|two (?:men|guys|leads|of them)|the (?:guys|leads|boys|two)|them together|together|the cast)\b", re.I)
PERSON_RX = re.compile(r"\b(her|him|she|he|woman|man|lady|boss|the lead|more of)\b", re.I)
EXCELLENT_WORD_RX = re.compile(r"\b(excellent|loved?(?: it)?|the best|strongest|outstanding|great)\b", re.I)
DEFINITE_RX = re.compile(r"\b(definitely|absolutely|yes|for sure|already)\b", re.I)
DEFINITELY_NOT_RX = re.compile(r"\b(definitely not|never|no way)\b", re.I)
BLACK_RX = re.compile(r"\b(black screen|cuts? to black|logo|credits|the cut ends|title card|slate)\b", re.I)
POS_RX = re.compile(r"\b(loved?|like[ds]?|held the room|presence|authority|command(?:ed|ing)?|strongest|solid|believed|bought (?:her|him|them)|want(?:ed)? more|more of (?:her|him|them)|"
                    r"carried|owned the room|set the tone|earned|magnetic|compelling|the reason|brings? me back|tips me|moves me)\b", re.I)
NEG_RX = re.compile(r"\b(stiff|flat|barely registered|did not land|didn't land|lost me|boring|bored|wooden|annoying|weak|forgettable|no warmth|too little|miscast|grating)\b", re.I)
_COMMON_NAME_TOKENS = {'cool', 'may', 'will', 'grace', 'hope', 'joy', 'rose', 'summer', 'love', 'king', 'young', 'small', 'long', 'white', 'black', 'brown', 'green',
                       'the', 'and', 'man', 'woman', 'lady', 'guy', 'boss', 'lead', 'jay', 'jr', 'sr', 'iii', 'dr', 'mr', 'mrs', 'ms'}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _norm(s) -> str:
    return ' ' + ' '.join(re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).split()) + ' '


def _tokens(name: str) -> list:
    """The spellings an option or a line uses for a cast member: the full
    name, then its longer parts (a part that is an ordinary word never
    stands for the person on its own)."""
    full = _norm(name).strip()
    out = [full] if full else []
    parts = full.split()
    if len(parts) > 1:
        for p in (parts[-1], parts[0]):
            if len(p) >= 3 and p not in _COMMON_NAME_TOKENS and p not in out:
                out.append(p)
    return out


def _mentions(text_norm: str, name: str) -> bool:
    return any(f" {tok} " in text_norm for tok in _tokens(name))


def _named_in(text_norm: str, cast: list) -> list:
    out = []
    for c in cast:
        n = c.get('name') if isinstance(c, dict) else c
        if n and _mentions(text_norm, str(n)):
            out.append(str(n))
    return out


def _finished(rec: dict) -> bool:
    if not isinstance(rec, dict) or not rec.get('finished_at'):
        return False
    if str(rec.get('stage') or '') in ('failed', 'stopped'):
        return False
    return bool(rec.get('answers')) and bool(rec.get('options'))


def _leans(rec: dict) -> dict:
    return {str(o.get('id')): str(o.get('lean') or 'other') for o in (rec.get('options') or []) if isinstance(o, dict)}


def _labels(rec: dict) -> dict:
    return {str(o.get('id')): str(o.get('label') or '') for o in (rec.get('options') or []) if isinstance(o, dict)}


def _clock_of(rec: dict) -> str:
    try:
        from migration.digital_twin_pilot import question_clock
        return question_clock(rec) or ''
    except Exception:
        return str(rec.get('playhead_clock') or '')


def cast_and_pair(session: dict) -> tuple:
    """The cast on the test and the pair the room is asked about (the two
    most often in a scene together, else the two leads)."""
    try:
        from migration.digital_twin_diagnostics import cast_from_session
        cast = cast_from_session(session) or []
    except Exception:
        cast = []
    rel = ((session.get('diagnostics') or {}).get('characters') or {}).get('relationship') or {}
    pair = [str(x) for x in (rel.get('pair') or []) if x] if isinstance(rel, dict) else []
    names = [c['name'] for c in cast]
    if len(pair) != 2 or any(p not in names for p in pair):
        pair = names[:2] if len(names) >= 2 else []
    return cast, pair


def _option_character(opt: dict, cast: list, named: list, pair: list):
    """What a watch-more option points at: a cast member, ('pair', [a, b])
    or None. A 'no' option never names a reason to come back."""
    lean = str(opt.get('lean') or 'other')
    if lean == 'no':
        return None
    label = _norm(opt.get('label'))
    hits = _named_in(label, cast)
    if len(hits) == 1:
        return hits[0]
    if len(hits) >= 2:
        return ('pair', hits[:2])
    if pair and PAIR_RX.search(label):
        return ('pair', list(pair))
    if len(named) == 1 and PERSON_RX.search(label):
        return named[0]
    return None


def _yes_no_ish(rec: dict) -> bool:
    leans = set(_leans(rec).values())
    return 'yes' in leans and ('no' in leans or 'maybe' in leans)


def _scene_target(rec: dict, session: dict) -> Optional[dict]:
    """The scene a question is about: the clock it names, else the moment
    it was asked at (the stimulus window), when the words point at one."""
    scenes = session.get('scenes') or []
    if not scenes:
        return None
    text = f"{rec.get('q') or ''} {rec.get('framing') or ''}"
    for m in CLOCK_RX.finditer(text):
        t = int(m.group(1)) * 60 + int(m.group(2))
        sc = next((s for s in scenes if float(s.get('t_start') or 0) <= t < float(s.get('t_end') or 0)), None)
        if sc is not None:
            return {'mode': 'clock', 'scene_id': int(sc['id']), 'clock': m.group(0)}
    stim = rec.get('stimulus') if isinstance(rec.get('stimulus'), dict) else {}
    win = stim.get('window') if isinstance(stim.get('window'), dict) else {}
    if win and SCENE_WORDS_RX.search(text):
        try:
            to_s = float(win.get('playhead_s') if win.get('playhead_s') is not None else rec.get('playhead_s') or 0)
            from_s = float(win.get('from_s') if win.get('from_s') is not None else max(0.0, to_s - 75.0))
        except (TypeError, ValueError):
            return None
        if to_s > from_s:
            return {'mode': 'window', 'from_s': from_s, 'to_s': to_s, 'clock': str(win.get('clock') or '')}
    return None


def question_bearing(rec: dict, session: dict, cast: Optional[list] = None, pair: Optional[list] = None) -> Optional[dict]:
    """What scorecard measure a finished question bears on, or None.
    {'kind': 'watch_more' | 'character' | 'pair' | 'battery' | 'scene', ...}."""
    if not _finished(rec):
        return None
    if cast is None or pair is None:
        cast, pair = cast_and_pair(session)
    q_norm = _norm(rec.get('q'))
    text = f"{rec.get('q') or ''} {rec.get('framing') or ''}"
    text_norm = _norm(text)
    named = _named_in(q_norm, cast) or _named_in(text_norm, cast)
    hypothetical = bool(HYPOTHETICAL_RX.search(str(rec.get('q') or '')))
    opts = [o for o in (rec.get('options') or []) if isinstance(o, dict) and o.get('id')]
    if WATCH_MORE_RX.search(text):
        mapping = {str(o['id']): _option_character(o, cast, named, pair) for o in opts}
        if any(v for v in mapping.values()):
            return {'kind': 'watch_more', 'option_character': mapping, 'named': named, 'pair': pair, 'clock': _clock_of(rec)}
        if not hypothetical and _yes_no_ish(rec) and not named:
            return {'kind': 'battery', 'measure': 'tune_in', 'clock': _clock_of(rec)}
        return None
    if named and not hypothetical:
        if len(named) >= 2:
            return {'kind': 'pair', 'targets': named[:2], 'clock': _clock_of(rec)}
        return {'kind': 'character', 'target': named[0], 'clock': _clock_of(rec)}
    if not hypothetical and _yes_no_ish(rec):
        if RECOMMEND_RX.search(text):
            return {'kind': 'battery', 'measure': 'recommend', 'clock': _clock_of(rec)}
        if FINISH_RX.search(text):
            return {'kind': 'battery', 'measure': 'finish', 'clock': _clock_of(rec)}
        if KEEP_WATCHING_RX.search(text):
            return {'kind': 'battery', 'measure': 'tune_in', 'clock': _clock_of(rec)}
        if OVERALL_RX.search(text):
            return {'kind': 'battery', 'measure': 'overall', 'clock': _clock_of(rec)}
    if not hypothetical and _yes_no_ish(rec):
        tgt = _scene_target(rec, session)
        if tgt:
            return {'kind': 'scene', 'target': tgt, 'clock': _clock_of(rec)}
    return None


# ------------------------------------------------------------ characters
def _max_grade(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The higher of two grades ('a' beats 'c')."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _GRADE_RANK[a] <= _GRADE_RANK[b] else b


def _min_grade(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if a is None:
        return b
    if b is None:
        return a
    return a if _GRADE_RANK[a] >= _GRADE_RANK[b] else b


def _remark(line: str, name: str, single: bool) -> float:
    """One remark's weight for a character: plainly positive words +1,
    plainly negative -1, only when the line is about the character (by
    name, or by pronoun when the question named one person)."""
    low = _norm(line)
    if not low.strip():
        return 0.0
    if not (_mentions(low, name) or (single and PERSON_RX.search(line or ''))):
        return 0.0
    pos = len(POS_RX.findall(line or ''))
    neg = len(NEG_RX.findall(line or ''))
    return float(clamp(pos - neg, -1, 1))


def character_evidence(session: dict, cast: Optional[list] = None, pair: Optional[list] = None) -> dict:
    """Every finished question's explicit word on the characters:
    {draw: {seat: name}, follow: {seat: name | ('pair', [a, b])},
    come_back: {seat: {lean, conf, named, asked_about}},
    floor: {seat: {name: grade}}, ceiling: {seat: {name: grade}},
    rel_floor: {seat: grade}, rel_ceiling: {seat: grade},
    remarks: {seat: {name: points}}, draw_counts: {name: n},
    questions: [{id, kind, clock}], clocks: [..]}.

    ``come_back`` is the seat's own answer on the latest come-back
    question it finished: the lean of what it picked (yes, maybe, no,
    other), its confidence (1 to 5), the character or pair it named as
    its reason (None when it named nobody), and the cast members the
    question itself named in its text (a seat asked about a person by
    name and picking someone else has spoken on that person)."""
    ev = {'draw': {}, 'follow': {}, 'come_back': {}, 'floor': {}, 'ceiling': {}, 'rel_floor': {}, 'rel_ceiling': {}, 'remarks': {}, 'draw_counts': {}, 'questions': [], 'clocks': []}
    if cast is None or pair is None:
        cast, pair = cast_and_pair(session)
    if not cast:
        return ev
    names = [c['name'] for c in cast]

    def floor(seat, name, g):
        ev['floor'].setdefault(seat, {})[name] = _max_grade(ev['floor'].get(seat, {}).get(name), g)

    def ceiling(seat, name, g):
        ev['ceiling'].setdefault(seat, {})[name] = _min_grade(ev['ceiling'].get(seat, {}).get(name), g)

    for rec in (session.get('questions') or []):
        b = question_bearing(rec, session, cast, pair)
        if not b or b['kind'] not in ('watch_more', 'character', 'pair'):
            continue
        leans, labels = _leans(rec), _labels(rec)
        single = b.get('named')[0] if (b['kind'] == 'watch_more' and len(b.get('named') or []) == 1) else (b.get('target') if b['kind'] == 'character' else None)
        for a in rec.get('answers') or []:
            if not isinstance(a, dict) or a.get('seat') is None:
                continue
            try:
                seat = int(a['seat'])
            except (TypeError, ValueError):
                continue
            oid = str(a.get('option') or '')
            lean = leans.get(oid, 'other')
            if b['kind'] == 'watch_more':
                tgt = (b.get('option_character') or {}).get(oid)
                try:
                    conf = int(a.get('confidence') if a.get('confidence') is not None else 3)
                except (TypeError, ValueError):
                    conf = 3
                named_here = None
                if isinstance(tgt, str) and tgt in names:
                    ev['draw'][seat] = tgt
                    ev['follow'][seat] = tgt
                    ev['draw_counts'][tgt] = ev['draw_counts'].get(tgt, 0) + 1
                    floor(seat, tgt, 'c')
                    named_here = tgt
                elif isinstance(tgt, tuple) and tgt[0] == 'pair' and len(tgt[1]) == 2 and all(n in names for n in tgt[1]):
                    ev['follow'][seat] = ('pair', list(tgt[1]))
                    named_here = ('pair', list(tgt[1]))
                ev['come_back'][seat] = {'lean': lean if lean in ('yes', 'maybe', 'no') else 'other', 'conf': max(1, min(5, conf)), 'named': named_here,
                                         'asked_about': [n for n in (b.get('named') or []) if n in names]}
                for n in names:
                    w = _remark(str(a.get('line') or ''), n, single == n)
                    if w:
                        cur = ev['remarks'].setdefault(seat, {}).get(n, 0.0)
                        ev['remarks'][seat][n] = float(clamp(cur + REMARK_STEP * w, -REMARK_CAP, REMARK_CAP))
            elif b['kind'] == 'character':
                n = b['target']
                if lean == 'yes':
                    floor(seat, n, 'a')
                elif lean == 'maybe':
                    floor(seat, n, 'c')
                elif lean == 'no':
                    ceiling(seat, n, 'd')
            elif b['kind'] == 'pair':
                a_n, b_n = b['targets']
                if lean == 'yes':
                    ev['rel_floor'][seat] = _max_grade(ev['rel_floor'].get(seat), 'a')
                    floor(seat, a_n, 'b')
                    floor(seat, b_n, 'b')
                elif lean == 'maybe':
                    floor(seat, a_n, 'c')
                    floor(seat, b_n, 'c')
                elif lean == 'no':
                    ev['rel_ceiling'][seat] = _min_grade(ev['rel_ceiling'].get(seat), 'd')
        ev['questions'].append({'id': rec.get('id'), 'kind': b['kind'], 'clock': b.get('clock') or ''})
        ck = b.get('clock') or ''
        if ck and ck not in ev['clocks']:
            ev['clocks'].append(ck)
    return ev


def room_draw(ev: dict, roster: list) -> Optional[str]:
    """The character more than half the room named as the reason it would
    come back, or None."""
    counts = (ev or {}).get('draw_counts') or {}
    if not counts or not roster:
        return None
    name, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return name if n * 2 > len(roster) else None


def update_note(clocks: list, kind: str = 'answer') -> str:
    clocks = [c for c in (clocks or []) if c]
    if not clocks:
        return "Updated after the room's answers."
    return f"Updated after the room's {kind}{'s' if len(clocks) > 1 else ''} at {', '.join(clocks)}."


# ---------------------------------------------------------------- battery
def battery_answer(measure: str, lean: str, label: str, current: Optional[str]) -> Optional[str]:
    """The battery option a seat's explicit answer sets (None leaves the
    inferred one). tune_in / finish are yes or no; recommend and overall
    carry a floor or a ceiling from the lean and the words."""
    lean = str(lean or 'other')
    if measure in ('tune_in', 'finish'):
        return 'a' if lean == 'yes' else 'b' if lean == 'no' else None
    if measure == 'recommend':
        if lean == 'yes':
            return 'a' if (DEFINITE_RX.search(label or '') or EXCELLENT_WORD_RX.search(label or '')) else _max_grade(current, 'b')
        if lean == 'no':
            return 'd' if DEFINITELY_NOT_RX.search(label or '') else 'c'
        return None
    if measure == 'overall':
        if lean == 'yes':
            return 'a' if EXCELLENT_WORD_RX.search(label or '') else _max_grade(current, 'b')
        if lean == 'maybe':
            return _max_grade(current, 'c')
        if lean == 'no':
            return _min_grade(current, 'd')
    return None


def _messy_battery(bat: dict, seed: str) -> None:
    """No whole-number share on the rebuilt battery (a 80.0 reads as a
    round number): a tenth moves within each question's rows, the sums
    hold, and the headline and the whole-room quadrant re-read the rows."""
    from migration.digital_twin_diagnostics import _messy_rows
    by_id = {}
    for q in bat.get('questions') or []:
        rows = q.get('rows') or []
        if not rows:
            continue
        _messy_rows(rows, f"{seed}|{q.get('id')}")
        q['top_pct'] = round(float(rows[0]['pct']), 1)
        if q.get('id') == 'overall' and len(rows) >= 2:
            q['total_positive_pct'] = round(float(rows[0]['pct']) + float(rows[1]['pct']), 1)
        if q.get('kind') == 'yes_no':
            q['yes_pct'] = round(float(rows[0]['pct']), 1)
        by_id[str(q.get('id'))] = q
    head = bat.get('headline') if isinstance(bat.get('headline'), dict) else None
    total = next((qd for qd in (bat.get('quadrants') or []) if qd.get('key') == 'total'), None)
    pairs = (('excellent_pct', 'overall', 'top_pct'), ('total_positive_pct', 'overall', 'total_positive_pct'), ('definitely_recommend_pct', 'recommend', 'top_pct'),
             ('finish_pct', 'finish', 'yes_pct'), ('tune_in_pct', 'tune_in', 'yes_pct'), ('better_than_expected_pct', 'expectations', 'top_pct'), ('full_price_pct', 'full_price', 'yes_pct'))
    for key, qid, field in pairs:
        if qid in by_id and by_id[qid].get(field) is not None:
            if head is not None and key in head:
                head[key] = by_id[qid][field]
            if total is not None and key in total:
                total[key] = by_id[qid][field]
    urg = by_id.get('urgency') or by_id.get('urgency_pilot')
    if head is not None and urg is not None and urg.get('top_pct') is not None and 'urgency_opening_pct' in head:
        head['urgency_opening_pct'] = urg['top_pct']


def _apply_battery(session: dict, twin: dict, pending: list) -> bool:
    """The seats' explicit keep-watching / recommend / finish / overall
    answers onto their battery cards; the battery, the headline and the
    quadrants rebuilt from them."""
    from migration import digital_twin_pilot as PI
    sc = session.get('scorecard') or {}
    bat = sc.get('battery') or {}
    if not bat.get('by_seat'):
        return False
    answers = {int(r['seat']): dict(r.get('answers') or {}) for r in bat['by_seat'] if r.get('seat') is not None}
    why = {int(r['seat']): str(r.get('why') or '') for r in bat['by_seat'] if r.get('seat') is not None}
    changed = False
    for rec, b in pending:
        measure = b['measure']
        qid = {'overall': 'overall', 'recommend': 'recommend', 'finish': 'finish', 'tune_in': 'tune_in'}[measure]
        leans, labels = _leans(rec), _labels(rec)
        for a in rec.get('answers') or []:
            try:
                seat = int(a.get('seat'))
            except (TypeError, ValueError, AttributeError):
                continue
            if seat not in answers or qid not in answers[seat]:
                continue
            oid = str(a.get('option') or '')
            new = battery_answer(measure, leans.get(oid, 'other'), labels.get(oid, ''), answers[seat].get(qid))
            if new and new != answers[seat].get(qid):
                answers[seat][qid] = new
                changed = True
    if not changed:
        return False
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    watch = session.get('watch') if isinstance(session.get('watch'), dict) else {}
    media = session.get('media') or {}
    seed = f"pilot|{session.get('id')}|{media.get('hash')}|{VERSION}"
    media_key = str(watch.get('media_key') or media.get('hash') or '')
    fmt = str(sc.get('format') or PI.format_for(session))
    new_bat = PI.battery_scorecard(twin or {}, roster, segments, session.get('seat_dials') or {}, session.get('beats') or [], session.get('scenes') or [],
                                   session.get('events') or [], fmt=fmt, subject=str(((twin or {}).get('meta') or {}).get('subject') or ((twin or {}).get('meta') or {}).get('name') or 'the twin'),
                                   media_key=media_key, session_seed=seed, answers_in=answers, why_in=why)
    _messy_battery(new_bat, f"{seed}|{media_key}|room")
    sc['battery'] = new_bat
    head = new_bat.get('headline') or {}
    sc['recommend_pct'] = head.get('definitely_recommend_pct')
    sc['finish_intent_pct'] = head.get('finish_pct')
    # the seats' cards keep the room's answers, so a re-read of the same cut starts from them
    if not isinstance(session.get('watch'), dict):
        session['watch'] = dict(watch)
    card = session['watch'].get('card') if isinstance(session['watch'].get('card'), dict) else None
    if card is None:
        card = {'format': fmt, 'answers': {}, 'why': {}, 'best': {}, 'worst': {}}
        session['watch']['card'] = card
    card['format'] = fmt
    card['answers'] = {str(k): v for k, v in answers.items()}
    card['from_room'] = True
    return True


# ----------------------------------------------------------------- scenes
def _apply_scenes(session: dict, pending: list) -> bool:
    """A seat who said a scene landed names it the scene liked best; one
    who said it lost them names it the scene liked least."""
    scenes = session.get('scenes') or []
    beats = session.get('beats') or []
    seat_dials = session.get('seat_dials') or {}
    if not scenes:
        return False
    watch = session.setdefault('watch', {}) if isinstance(session.get('watch'), dict) else session.setdefault('watch', {})
    card = watch.setdefault('card', {})
    best = {str(k): v for k, v in (card.get('best') or {}).items()}
    worst = {str(k): v for k, v in (card.get('worst') or {}).items()}
    by_id = {int(s['id']): s for s in scenes}

    def in_window(from_s, to_s):
        ids = []
        for s in scenes:
            if float(s.get('t_end') or 0) > from_s and float(s.get('t_start') or 0) < to_s and not BLACK_RX.search(str(s.get('caption') or '')):
                ids.append(int(s['id']))
        return ids

    def seat_pick(seat, ids, want_best):
        series = seat_dials.get(str(seat)) or []
        per = {}
        for i, b in enumerate(beats):
            if i < len(series) and b.get('scene_id') in ids:
                per.setdefault(int(b['scene_id']), []).append(series[i])
        if not per:
            return ids[-1] if ids else None
        means = {k: sum(v) / len(v) for k, v in per.items()}
        return (max if want_best else min)(means.items(), key=lambda kv: kv[1])[0]
    changed = False
    for rec, b in pending:
        tgt = b['target']
        leans = _leans(rec)
        ids = [tgt['scene_id']] if tgt['mode'] == 'clock' else in_window(tgt['from_s'], tgt['to_s'])
        if not ids or any(i not in by_id for i in ids):
            continue
        for a in rec.get('answers') or []:
            try:
                seat = int(a.get('seat'))
            except (TypeError, ValueError, AttributeError):
                continue
            lean = leans.get(str(a.get('option') or ''), 'other')
            if lean == 'yes':
                pick = seat_pick(seat, ids, True)
                if pick is not None and best.get(str(seat)) != pick:
                    best[str(seat)] = pick
                    changed = True
            elif lean == 'no':
                pick = seat_pick(seat, ids, False)
                if pick is not None and worst.get(str(seat)) != pick:
                    worst[str(seat)] = pick
                    changed = True
    if changed:
        card['best'], card['worst'] = best, worst
        card['from_room'] = True
    return changed


# ------------------------------------------------------------------ glue
def _load_twin(s3, twin_key: str) -> dict:
    if s3 is None or not twin_key:
        return {}
    try:
        from migration import digital_twin_synthesis as SYN
        t = SYN.load_twin(s3, twin_key)
        return t if isinstance(t, dict) else {}
    except Exception:
        return {}


def pending_questions(session: dict, force: bool = False) -> list:
    """[(rec, bearing)] for the finished questions that bear on a measure
    and have not been applied to this scorecard yet."""
    if not isinstance(session, dict) or not session.get('scorecard'):
        return []
    applied = ((session.get('reconcile') or {}).get('applied') or {}) if isinstance(session.get('reconcile'), dict) else {}
    cast, pair = cast_and_pair(session)
    out = []
    for rec in (session.get('questions') or []):
        b = question_bearing(rec, session, cast, pair)
        if not b:
            continue
        stamp = str(rec.get('finished_at') or '')
        if not force and applied.get(str(rec.get('id'))) == stamp:
            continue
        out.append((rec, b))
    return out


def reconcile_session(session: dict, twin: Optional[dict] = None, *, s3=None, twin_key: str = '', force: bool = False) -> dict:
    """The scorecard brought into line with every finished question that
    bears on it. Returns {changed, applied: [ids], measures: [..], note}.
    Never raises into a route."""
    out = {'changed': False, 'applied': [], 'measures': [], 'note': ''}
    try:
        pending = pending_questions(session, force=force)
    except Exception as e:
        print(f"[twin-reconcile] skipped: {e}")
        return out
    if not pending:
        return out
    try:
        if not twin:
            twin = _load_twin(s3, twin_key or str((session.get('twin_keys') or [''])[0] or ''))
        twin = twin or {}
        from migration import digital_twin_diagnostics as DIAG
        kinds = {b['kind'] for _, b in pending}
        clocks = []
        for _, b in pending:
            if b.get('clock') and b['clock'] not in clocks:
                clocks.append(b['clock'])
        note = update_note(clocks)
        sc = session.get('scorecard') or {}
        d = session.setdefault('diagnostics', {}) if isinstance(session.get('diagnostics'), dict) else session.setdefault('diagnostics', {})
        if kinds & {'watch_more', 'character', 'pair'}:
            d['characters'] = DIAG.character_table(session, twin) or {}
            out['measures'].append('characters')
            try:   # what the room said about each named person rides on the rebuilt rows when that read exists
                from migration.digital_twin_talent_read import stamp_rows
                stamp_rows(session)
            except Exception:
                pass
        if 'battery' in kinds:
            if _apply_battery(session, twin, [(r, b) for r, b in pending if b['kind'] == 'battery']):
                notes = [n for n in (sc.get('reconcile_notes') or []) if n != note]
                sc['reconcile_notes'] = (notes + [note])[-3:]
                out['measures'].append('battery')
        if 'scene' in kinds:
            if _apply_scenes(session, [(r, b) for r, b in pending if b['kind'] == 'scene']):
                d['scenes'] = DIAG.scenes_liked(session, twin) or {}
                if d['scenes']:
                    d['scenes']['note'] = note
                    d['scenes']['read'] = (str(d['scenes'].get('read') or '') + ' ' + note).strip()
                out['measures'].append('scenes')
        # downstream: the norm lines and columns, the forecast, the ledger cell this test filled
        if sc:
            try:
                from migration import digital_twin_pilot as PI
                PI.attach_norms(sc, str(sc.get('format') or PI.format_for(session)), str(session.get('genre') or 'all') or 'all', s3=s3)
            except Exception as e:
                print(f"[twin-reconcile] norms refresh skipped: {e}")
            try:
                from migration.digital_twin_forecast import attach_forecast
                attach_forecast(twin, session, s3=s3)
            except Exception as e:
                print(f"[twin-reconcile] forecast refresh skipped: {e}")
            if session.get('norms_recorded_at') and s3 is not None:
                try:
                    from migration import digital_twin_pilot as PI
                    PI.forget_norms(session, s3=s3)
                    PI.record_norms(session, s3=s3)
                    PI.attach_norms(sc, str(sc.get('format') or PI.format_for(session)), str(session.get('genre') or 'all') or 'all', s3=s3)
                except Exception as e:
                    print(f"[twin-reconcile] ledger re-file skipped: {e}")
            # the character and component rows carry the norm column as it stands after the re-file
            try:
                DIAG.refresh_norm_refs(session)
            except Exception as e:
                print(f"[twin-reconcile] norm refs refresh skipped: {e}")
        state = session.get('reconcile') if isinstance(session.get('reconcile'), dict) else {}
        applied = dict(state.get('applied') or {})
        for rec, b in pending:
            applied[str(rec.get('id'))] = str(rec.get('finished_at') or '')
            out['applied'].append(str(rec.get('id')))
        notes = [n for n in (state.get('notes') or []) if n != note] + [note]
        session['reconcile'] = {'applied': applied, 'at': _now(), 'notes': notes[-6:], 'measures': sorted(set(list(state.get('measures') or []) + out['measures'])),
                                'version': RECONCILE_VERSION}
        session['updated_at'] = _now()
        out['changed'] = True
        out['note'] = note
        print(f"[twin-reconcile] {session.get('id')}: {', '.join(out['applied'])} -> {', '.join(out['measures']) or 'no measure moved'}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[twin-reconcile] failed: {e}")
    return out


def after_question(session: dict, rec: Optional[dict] = None, twin: Optional[dict] = None, *, s3=None, twin_key: str = '') -> dict:
    """The hook a landed room question calls: a Pilot Testing room with a
    scorecard reconciles; a focus-group room (no scorecard) is left
    alone. Never raises."""
    try:
        if not isinstance(session, dict) or not session.get('scorecard'):
            return {'changed': False, 'applied': [], 'measures': [], 'note': ''}
        return reconcile_session(session, twin, s3=s3, twin_key=twin_key)
    except Exception as e:
        print(f"[twin-reconcile] after_question skipped: {e}")
        return {'changed': False, 'applied': [], 'measures': [], 'note': ''}


__all__ = ['question_bearing', 'character_evidence', 'room_draw', 'cast_and_pair', 'battery_answer', 'update_note', 'pending_questions', 'reconcile_session',
           'after_question', 'RECONCILE_VERSION', 'DRAW_WEIGHT', 'GRADES']
