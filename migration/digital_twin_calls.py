"""What a call is, and what settles it (Digital Twins IQ, 2026-11-20).

Jenna, 2026-11-20, in a choice form: "Only questions with a real result
to check: forecasts, election and premiere calls, the final question of
a Predictive Analytics run, and behavior we can count ourselves".

A call goes on the record with what settles it, from the moment it is
made (``call`` on the ledger row):

  due_at       the day its result is knowable (YYYY-MM-DD); empty while
               the day is not known yet (a pilot before its air date)
  knowable_at  the day the result starts to exist (the air date, the
               election day); a call made on or after it is post hoc
  resolver     the check that settles it
                 counted    our own count, once the window closes plus a day
                 published  a published figure (a premiere's first week,
                            an opening weekend)
                 certified  a certified result (an election)
                 client     the client's own results, added by the client
  comparator   how it is scored
                 number     a point against a value
                 split      a distribution against a distribution
                 yes_no     a probability against whether it happened
                 winner     a pick against the winner
  measure      exactly what is measured: subject, what (plain words),
               metric, unit, people, platform, event, window_start,
               window_end, options

What is a call:
  * a Pilot Testing forecast: the week-one audience (published), the
    season completion (the client's own results) and the return in week
    two (our own count when the title is in the content map, else the
    client's own results);
  * the final question of a Predictive Analytics run, only when the
    business question names a real-world outcome (an election, a
    premiere, an opening weekend, a behavior) and a date still ahead;
  * any room question or forecast an admin logs on purpose before its
    result, with its due date, check, measure and scoring.
Every other room answer stays in the room and is not a call.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

RESOLVERS = ('counted', 'published', 'certified', 'client')
COMPARATORS = ('number', 'split', 'yes_no', 'winner')
EVENTS = ('election', 'premiere', 'opening_weekend', 'week_one', 'completion', 'return', 'behavior', 'other')

_MONTHS = {m: i + 1 for i, m in enumerate(('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'))}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS['sept'] = 9
_MONTH_RX = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO_RX = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")
_MD_RX = re.compile(r"\b" + _MONTH_RX + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(20\d\d))?", re.I)
_DM_RX = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH_RX + r"\.?,?\s+(20\d\d)\b", re.I)
_US_RX = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d|\d\d)\b")

_ELECTION_RX = re.compile(r"\b(?:elect(?:ion|ions|ed)?|vote|votes|voting|ballots?|primary|primaries|runoff|caucus(?:es)?|referendum|proposition|"
                          r"senate (?:race|seat)|governor(?:'s)? race|mayor(?:al)? race|wins? (?:the|this) (?:race|seat|primary|election|runoff))\b", re.I)
_OPENING_RX = re.compile(r"\b(?:opening weekend|box office|opens? in theaters|in theaters|theatrical (?:release|opening)|first weekend in theaters)\b", re.I)
_PREMIERE_RX = re.compile(r"\b(?:premieres?|premiered|series premiere|season premiere|debuts?|first week|week one|launch(?:es)? on|drops? on|"
                          r"releases? on|released on|comes? out on|arrives? on|streams? on)\b", re.I)
_BEHAVIOR_RX = re.compile(r"\b(?:sign(?:s|ed)? up|subscribe[sd]?|cancel(?:s|led|ed)?|renew(?:s|ed)?|buy|buys|bought|purchase[sd]?|watch(?:es|ed)?|"
                          r"stream(?:s|ed)?|download(?:s|ed)?|install(?:s|ed)?|visit(?:s|ed)?)\b", re.I)
# a room answer that is an opinion, a what-if or the past is never a call by itself
_HYPOTHETICAL_RX = re.compile(r"\b(?:if|say|suppose|imagine|picture|were to|could buy|can buy|buy the rights|instead of|restocked|brought back)\b", re.I)
_PAST_RX = re.compile(r"\b(?:in the past (?:year|month|week)|last (?:year|month|week)|did you|have you ever|when you sit down|which app do you open|"
                      r"do you (?:usually|normally)|how often do you)\b", re.I)
_OPINION_RX = re.compile(r"\b(?:what brands?|which brands?|associate|ambassador|what stops you|what holds you|how do you feel|what do you think|"
                         r"most excited|favorite|favourite|would get you|would you think)\b", re.I)


# ---------------------------------------------------------------- dates
def iso_day(v) -> str:
    """YYYY-MM-DD from a date, a datetime or a string that starts with
    one; '' when it is not a date."""
    if isinstance(v, _dt.datetime):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, _dt.date):
        return v.isoformat()
    m = re.match(r"\s*(20\d\d)-(\d{1,2})-(\d{1,2})", str(v or ''))
    if not m:
        return ''
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ''


def add_days(day: str, n: int) -> str:
    d = iso_day(day)
    if not d:
        return ''
    return (_dt.date.fromisoformat(d) + _dt.timedelta(days=int(n))).isoformat()


def today_iso(now: Optional[float] = None) -> str:
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    return _dt.datetime.fromtimestamp(float(now), _dt.timezone.utc).date().isoformat()


def _mk(y, mo, d) -> Optional[_dt.date]:
    try:
        return _dt.date(int(y), int(mo), int(d))
    except (TypeError, ValueError):
        return None


def find_date(text: str, today: str) -> str:
    """The first calendar date named in the words (an ISO date, 'Nov 3',
    'November 3, 2026', '3 November 2026', '11/3/2026'). A month and day
    with no year is the nearest such day to today. '' when no day is
    named (a bare month or 'next week' is not a date)."""
    t = str(text or '')
    base = _dt.date.fromisoformat(iso_day(today) or today_iso())
    found = []
    for m in _ISO_RX.finditer(t):
        d = _mk(m.group(1), m.group(2), m.group(3))
        if d:
            found.append((m.start(), d))
    for m in _MD_RX.finditer(t):
        mo = _MONTHS.get(m.group(1).lower().rstrip('.'))
        day = int(m.group(2))
        if not mo or not 1 <= day <= 31:
            continue
        if m.group(3):
            d = _mk(m.group(3), mo, day)
        else:
            # no year: the nearest such day to today, ahead or behind ('Nov 3' asked on Nov 18 is the one just gone)
            cands = [c for c in (_mk(base.year - 1, mo, day), _mk(base.year, mo, day), _mk(base.year + 1, mo, day)) if c]
            d = min(cands, key=lambda c: abs((c - base).days)) if cands else None
        if d:
            found.append((m.start(), d))
    for m in _DM_RX.finditer(t):
        mo = _MONTHS.get(m.group(2).lower().rstrip('.'))
        d = _mk(m.group(3), mo, m.group(1)) if mo else None
        if d:
            found.append((m.start(), d))
    for m in _US_RX.finditer(t):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        d = _mk(y, m.group(1), m.group(2))
        if d:
            found.append((m.start(), d))
    if not found:
        return ''
    found.sort(key=lambda x: x[0])
    return found[0][1].isoformat()


def event_of(text: str) -> str:
    """The real-world outcome the words name: election, opening_weekend,
    premiere or behavior; '' when they name none."""
    t = str(text or '')
    if _ELECTION_RX.search(t):
        return 'election'
    if _OPENING_RX.search(t):
        return 'opening_weekend'
    if _PREMIERE_RX.search(t):
        return 'premiere'
    if _BEHAVIOR_RX.search(t):
        return 'behavior'
    return ''


# ---------------------------------------------------------------- the spec
def make_call(*, resolver: str, comparator: str, measure: dict, due_at: str = '', knowable_at: str = '', due_rule: str = '', event: str = '') -> dict:
    """A normalized call spec. Raises ValueError on a resolver or
    comparator the record does not know."""
    resolver = str(resolver or '').strip().lower()
    comparator = str(comparator or '').strip().lower()
    if resolver not in RESOLVERS:
        raise ValueError(f"the check must be one of {', '.join(RESOLVERS)}")
    if comparator not in COMPARATORS:
        raise ValueError(f"the scoring must be one of {', '.join(COMPARATORS)}")
    meas = {k: v for k, v in (measure or {}).items() if v not in (None, '', [])}
    for k in ('window_start', 'window_end'):
        if k in meas:
            meas[k] = iso_day(meas[k])
    ev = str(event or meas.get('event') or '').strip().lower()
    if ev and ev not in EVENTS:
        ev = 'other'
    if ev:
        meas['event'] = ev
    return {'resolver': resolver, 'comparator': comparator, 'measure': meas, 'due_at': iso_day(due_at), 'knowable_at': iso_day(knowable_at),
            'due_rule': str(due_rule or '')[:120], 'event': ev}


def validate_call(spec: dict) -> tuple:
    """(ok, reason). A call names what is measured and how it is scored;
    a counted call names its title and both window days; a call with no
    due date says how the day will be found."""
    if not isinstance(spec, dict):
        return False, 'no call'
    if spec.get('resolver') not in RESOLVERS:
        return False, 'the check is missing'
    if spec.get('comparator') not in COMPARATORS:
        return False, 'how it is scored is missing'
    meas = spec.get('measure') or {}
    if not (meas.get('subject') and meas.get('what')):
        return False, 'what is measured is missing'
    if spec['resolver'] == 'counted':
        if not meas.get('metric'):
            return False, 'a count names its measure'
        if not (meas.get('window_start') and meas.get('window_end')) and not spec.get('due_rule'):
            return False, 'a count names its window'
    if not spec.get('due_at') and not spec.get('due_rule'):
        return False, 'the day its result is knowable is missing'
    return True, ''


# ---------------------------------------------------------------- room answers
def options_of(rec: dict) -> list:
    rows = rec.get('options') if (rec.get('options') and isinstance(rec['options'][0], dict)) else (rec.get('distribution') or [])
    return [o for o in rows or [] if isinstance(o, dict) and o.get('label')]


def room_prediction(rec: dict) -> Optional[dict]:
    """The published split of a finished room question as a prediction:
    the distribution, the lead as the point, the lead as the pick, the
    yes share as the probability on a yes or no, and the people behind
    each answer."""
    opts = options_of(rec)
    if not opts:
        return None
    lead = max(opts, key=lambda o: float(o.get('pct') or 0))
    pred = {'distribution': [{'label': o['label'], 'pct': float(o.get('pct') or 0)} for o in opts], 'point': float(lead.get('pct') or 0), 'unit': 'pct',
            'call': str(lead['label'])}
    people = {str(o['label']): int(float(o.get('people') or 0)) for o in opts if o.get('people') not in (None, '')}
    if people:
        pred['people'] = people
    if rec.get('yes_pct') is not None:
        try:
            pred['probability'] = round(float(rec['yes_pct']) / 100.0, 4)
        except (TypeError, ValueError):
            pass
    fb = rec.get('firmness') if isinstance(rec.get('firmness'), dict) else {}
    lead_r = (fb.get('lead') or {}) if isinstance(fb, dict) else {}
    if lead_r.get('low_pct') is not None and lead_r.get('high_pct') is not None:
        pred['low'], pred['high'] = lead_r['low_pct'], lead_r['high_pct']
    return pred


def _yes_option(opts: list) -> Optional[dict]:
    for o in opts:
        if re.match(r"\s*(yes|definitely|probably yes|likely)\b", str(o.get('label') or ''), re.I):
            return o
    return None


def classify_answer(question: str, *, today: str = '') -> tuple:
    """('call', event, date) when the words name a real-world outcome and
    a date still ahead of ``today``; else ('not_a_call', reason, '').
    The reason is one of: opinion, what if, the past, no date, a date
    already past, no real result."""
    q = ' '.join(str(question or '').split())
    today = iso_day(today) or today_iso()
    if _PAST_RX.search(q):
        return 'not_a_call', 'the past', ''
    day = find_date(q, today)
    event = event_of(q)
    if day and event:
        if day < today:
            return 'not_a_call', 'a date already past', ''
        return 'call', event, day
    if _HYPOTHETICAL_RX.search(q):
        return 'not_a_call', 'what if', ''
    if _OPINION_RX.search(q):
        return 'not_a_call', 'opinion', ''
    if event and not day:
        return 'not_a_call', 'no date', ''
    return 'not_a_call', 'no real result', ''


_DUE_AFTER = {'election': 1, 'premiere': 8, 'opening_weekend': 4, 'behavior': 1}


def guided_call(plan_doc: dict, sess: dict, twin: dict, *, today: str = '', content_map_has=None) -> Optional[dict]:
    """The call a finished Predictive Analytics run puts on the record:
    only its final question, only when the business question names a
    real-world outcome and a date still ahead. Returns {kind, measure,
    prediction, question, question_kind, question_id, call} or None."""
    plan_doc = plan_doc or {}
    qs = plan_doc.get('questions') or []
    if not qs:
        return None
    try:
        final = int(plan_doc.get('final') if plan_doc.get('final') is not None else len(qs) - 1)
    except (TypeError, ValueError):
        final = len(qs) - 1
    if not 0 <= final < len(qs):
        return None
    pq = qs[final] or {}
    rec = next((r for r in (sess or {}).get('questions') or [] if isinstance(r, dict) and r.get('guided_i') == final and r.get('finished_at')), None)
    if not rec:
        return None
    today = iso_day(today) or today_iso()
    words = ' '.join(str(x or '') for x in (plan_doc.get('asked'), plan_doc.get('goal'), pq.get('q')))
    verdict, event, day = classify_answer(words, today=today)
    if verdict != 'call':
        return None
    qk = str(pq.get('kind') or rec.get('kind') or '')
    pred = room_prediction(rec)
    if not pred:
        return None
    opts = options_of(rec)
    subject = _subject_of(twin)
    meas = {'subject': subject, 'what': ' '.join(str(pq.get('q') or rec.get('q') or '').split())[:300], 'event': event, 'on': day,
            'options': [str(o['label']) for o in opts][:8], 'people': f"the {subject} audience"}
    if qk == 'multiple_choice' and event in ('election', 'opening_weekend', 'premiere'):
        comparator = 'winner'
    elif qk == 'yes_no' and event in ('election', 'premiere', 'behavior'):
        comparator = 'number'
        yes = _yes_option(opts)
        if not yes:
            return None
        if event == 'election':
            pred = {'point': float(yes.get('pct') or 0), 'unit': 'pct', 'call': 'Yes' if float(yes.get('pct') or 0) >= 50 else 'No',
                    'distribution': pred['distribution']}
            meas.update({'metric': 'vote_share', 'unit': 'pct'})
        else:
            try:
                n = int(float(yes.get('people') or 0))
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                return None
            pred = {'point': n, 'unit': 'people', 'distribution': pred['distribution']}
            meas.update({'metric': 'people_who_do_it', 'unit': 'people'})
    else:
        return None
    if event == 'election':
        resolver = 'certified'
    elif event in ('premiere', 'opening_weekend'):
        resolver = 'published'
    else:
        show, platform = _title_of(twin, words)
        has = bool(show) and callable(content_map_has) and bool(content_map_has(show, platform))
        resolver = 'counted' if has else 'client'
        if has:
            meas.update({'metric': 'title_viewers', 'title': show, 'platform': platform, 'window_start': day, 'window_end': add_days(day, 7)})
    due = add_days(day, _DUE_AFTER.get(event, 1)) if resolver != 'counted' else add_days(meas.get('window_end') or day, 1)
    spec = make_call(resolver=resolver, comparator=comparator, measure=meas, due_at=due, knowable_at=day, event=event)
    return {'kind': 'call', 'measure': f"{event}:{day}", 'prediction': pred, 'question': meas['what'], 'question_kind': qk,
            'question_id': str(rec.get('id') or ''), 'call': spec}


def _subject_of(twin: dict) -> str:
    m = (twin or {}).get('meta') or {}
    return str(m.get('subject') or m.get('name') or 'the twin')[:120]


def _title_of(twin: dict, words: str) -> tuple:
    """(show, platform) a behavior call is counted against: the twin's
    own title and home (a viewers twin of a show), else nothing."""
    m = (twin or {}).get('meta') or {}
    platform = str(m.get('platform_home') or '')
    if str(m.get('subject_kind') or '').lower() in ('content', 'title', 'series', 'film') or str(m.get('format') or '').lower() in ('series', 'film'):
        return str(m.get('subject') or m.get('name') or ''), platform
    return '', platform


# ---------------------------------------------------------------- pilot forecasts
def forecast_calls(twin: dict, sess: dict, fc: dict, *, aired_on: str = '', content_map_has=None) -> list:
    """The calls a Pilot Testing forecast makes: the week-one audience,
    the season completion and the return in week two. Each carries what
    settles it; a title whose air date is not known yet carries the rule
    the day is found by (the daily check looks for the air date)."""
    out = []
    if not isinstance(fc, dict) or not isinstance(fc.get('episode'), dict):
        return out
    show = str(fc.get('show') or _subject_of(twin))
    m = (twin or {}).get('meta') or {}
    platform = str(m.get('platform_home') or m.get('name') or '')
    aired_on = iso_day(aired_on or ((fc.get('actual') or {}).get('aired') if isinstance(fc.get('actual'), dict) else ''))
    ep = fc['episode']
    base = int(fc.get('base_people') or 0)
    if base > 0 and ep.get('week_one_audience'):
        meas = {'subject': show, 'what': str(ep.get('definition') or 'People who watch the episode within seven days of release, on every screen.'),
                'metric': 'week_one_audience', 'unit': 'people', 'people': 'everyone in the US', 'platform': platform, 'event': 'week_one'}
        if aired_on:
            meas.update({'window_start': aired_on, 'window_end': add_days(aired_on, 7)})
        out.append({'kind': 'forecast', 'measure': 'week_one_audience', 'question': 'Week-one audience if released', 'question_kind': 'forecast',
                    'prediction': {'point': ep['week_one_audience'], 'low': ep.get('low'), 'high': ep.get('high'), 'unit': 'people'},
                    'call': make_call(resolver='published', comparator='number', measure=meas, due_at=add_days(aired_on, 8) if aired_on else '',
                                      knowable_at=aired_on, due_rule='seven days after the title is released, plus a day', event='week_one')})
    ser = fc.get('series') or {}
    if ser.get('season_completion_pct') is not None:
        meas = {'subject': show, 'what': str(ser.get('completion_definition') or 'The share of episode viewers who watch through the season finale.'),
                'metric': 'season_completion_pct', 'unit': 'pct', 'people': 'the people who watch the episode', 'platform': platform, 'event': 'completion'}
        out.append({'kind': 'forecast', 'measure': 'season_completion_pct', 'question': 'Share of episode viewers who watch through the finale', 'question_kind': 'forecast',
                    'prediction': {'point': ser['season_completion_pct'], 'unit': 'pct'},
                    'call': make_call(resolver='client', comparator='number', measure=meas, knowable_at=aired_on,
                                      due_rule='a week after the season finale, from your own results', event='completion')})
    hd = (((sess or {}).get('scorecard') or {}).get('battery') or {}).get('headline') or {}
    if hd.get('tune_in_pct') is not None:
        counted = callable(content_map_has) and bool(content_map_has(show, platform))
        meas = {'subject': show, 'what': 'Of the people who watch in the first week, the share who watch again in the second week.',
                'metric': 'return_pct', 'unit': 'pct', 'people': 'the people who watch in the first week', 'platform': platform, 'event': 'return', 'title': show}
        if aired_on:
            meas.update({'window_start': aired_on, 'window_end': add_days(aired_on, 14)})
        out.append({'kind': 'scorecard', 'measure': 'return_pct', 'question': 'Share who come back the second week', 'question_kind': 'forecast',
                    'prediction': {'point': float(hd['tune_in_pct']), 'unit': 'pct'},
                    'call': make_call(resolver='counted' if counted else 'client', comparator='number', measure=meas,
                                      due_at=add_days(aired_on, 15) if aired_on else '', knowable_at=aired_on,
                                      due_rule='fourteen days after the title is released, plus a day', event='return')})
    return out


# ---------------------------------------------------------------- writing calls
def record_guided(s3, payload: dict, key: str, sess: dict, plan_doc: dict, *, username: str = '', today: str = '', content_map_has=None) -> Optional[dict]:
    """The finished Predictive Analytics run's final question on the
    record, when it is a call (``guided_call``). Private to the twin's
    company; a Crosswalk run reaches the public record only when an admin
    logs it on purpose. Never raises."""
    try:
        from migration import digital_twin_ledger as LG
        if content_map_has is None:
            try:
                from migration import digital_twin_counted as COUNT
                content_map_has = COUNT.has_title
            except Exception:
                content_map_has = None
        c = guided_call(plan_doc, sess, payload, today=today, content_map_has=content_map_has)
        if not c:
            return None
        twin_key = str(key or ((payload or {}).get('meta') or {}).get('key') or '')
        try:
            from migration.digital_twin_room import subject_class
            aclass = subject_class(payload)
        except Exception:
            aclass = ''
        return LG.record_prediction(s3, c['kind'], _subject_of(payload), c['prediction'], measure=c['measure'], twin_key=twin_key, session_id=str(sess.get('id') or ''),
                                    question_id=c['question_id'], question=c['question'], question_kind=c['question_kind'], audience_class=aclass,
                                    method_version='guided-1', company=LG.twin_company_of(payload, twin_key), call=c['call'],
                                    created_by=str(username or sess.get('created_by') or ''))
    except Exception as e:
        print(f"[twin-calls] the run's call was not written: {str(e)[:160]}")
        return None


def knowable_passed(spec: dict, today: str = '') -> bool:
    """True when the day the result starts to exist is today or behind
    us: a call logged then would be post hoc."""
    known = iso_day((spec or {}).get('knowable_at'))
    return bool(known) and known <= (iso_day(today) or today_iso())


def _find_question(payload: dict, sid: str, qid: str) -> tuple:
    for k in ('panel_sessions', 'pilot_sessions'):
        for s in (payload or {}).get(k) or []:
            if isinstance(s, dict) and str(s.get('id') or '') == str(sid or ''):
                q = next((x for x in s.get('questions') or [] if isinstance(x, dict) and str(x.get('id') or '') == str(qid or '')), None)
                return s, q
    return None, None


def log_on_purpose(s3, body: dict, *, by: str, load_twin, today: str = '') -> tuple:
    """Put a room question or an open call on the record on purpose,
    before its result (Jenna 2026-11-20: "Only calls we deliberately log
    before the result"). ``body``: ledger_id (an open call already on the
    ledger) or twin_key + session_id + question_id (a finished room
    question), and the call: due_at, knowable_at, check (counted,
    published, certified, client), scored_as (number, split, yes_no,
    winner), what, subject, metric, unit, people, platform, title,
    window_start, window_end, event. Returns (row, error, http code)."""
    from migration import digital_twin_ledger as LG
    body = body or {}
    lid = str(body.get('ledger_id') or '').strip()
    have = LG.load_record(s3, lid) if lid else None
    if lid and not have:
        return None, 'That call is not on the record.', 404
    key = str(body.get('twin_key') or (have or {}).get('twin_key') or '')
    payload = load_twin(key) if (key and callable(load_twin)) else None
    meta = (payload or {}).get('meta') or {}
    meas = {k: body.get(k) for k in ('what', 'metric', 'unit', 'people', 'platform', 'title', 'window_start', 'window_end', 'event') if body.get(k) not in (None, '')}
    meas['subject'] = str(body.get('subject') or (have or {}).get('subject') or meta.get('subject') or meta.get('name') or '').strip()
    if not meas.get('what'):
        meas['what'] = str((have or {}).get('question') or '')
    try:
        spec = make_call(resolver=body.get('check') or body.get('resolver'), comparator=body.get('scored_as') or body.get('comparator'), measure=meas,
                         due_at=body.get('due_at'), knowable_at=body.get('knowable_at') or body.get('due_at'), event=body.get('event') or '')
    except ValueError as e:
        return None, str(e), 400
    if not have and not meas.get('what'):
        sess, q = _find_question(payload, str(body.get('session_id') or ''), str(body.get('question_id') or ''))
        if q:
            spec['measure']['what'] = ' '.join(str(q.get('q') or '').split())[:300]
    ok, why = validate_call(spec)
    if not ok:
        return None, f"Not logged: {why}.", 400
    if knowable_passed(spec, today):
        return None, 'Not logged: its result is already knowable, so it would be a call made after the fact.', 409
    if have:
        if have.get('outcome') or have.get('post_hoc') or not LG.is_call(have):
            return None, 'Not logged: that row is already scored, made after the fact, or not a call.', 409
        rec = LG.update_call(s3, lid, call=spec, logged=True, logged_by=by, public=bool(LG.is_house(have)))
        return (rec, '', 200) if rec else (None, 'Not logged: the record could not be written.', 500)
    if not isinstance(payload, dict):
        return None, 'twin not found', 404
    sess, q = _find_question(payload, str(body.get('session_id') or ''), str(body.get('question_id') or ''))
    if not (sess and q and q.get('finished_at')):
        return None, 'Not logged: pick a finished question from a room.', 400
    from migration import digital_twin_room as ROOM
    rec = ROOM.ledger_row_for_question(s3, payload, sess, q, twin_key=key, call=spec, logged=True, logged_by=by)
    if not rec:
        return None, 'Not logged: that question has no split to put on the record.', 400
    if rec.get('post_hoc'):
        return None, 'Not logged: the room answered it on a result already counted.', 409
    if not rec.get('logged'):
        rec = LG.update_call(s3, rec['id'], call=spec, logged=True, logged_by=by, public=bool(LG.is_house(rec))) or rec
    return rec, '', 200


# ---------------------------------------------------------------- the 24 open rows (2026-11-20)
def _norm_q(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", ' ', str(q or '').lower()).strip()


def classify_open_rows(records: list, *, today: str = '') -> list:
    """For each open record: ('call' | 'not_a_call' | 'folded', reason,
    folded_into). An answer asked again in the same room folds into the
    first time it was asked; a question that names a real-world outcome
    and a date that was still ahead when it was asked stays a call."""
    out = []
    first_by_q = {}
    for rec in sorted(records, key=lambda r: str(r.get('created_at') or '')):
        key = (str(rec.get('twin_key') or rec.get('subject') or ''), str(rec.get('session_id') or ''), _norm_q(rec.get('question')))
        if key in first_by_q:
            out.append((rec, 'folded', 'asked again in the same room', first_by_q[key]))
            continue
        first_by_q[key] = rec.get('id')
        day = iso_day(rec.get('created_at')) or iso_day(today) or today_iso()
        verdict, why, when = classify_answer(rec.get('question') or '', today=day)
        if verdict == 'call':
            out.append((rec, 'call', f"{why} on {when}", ''))
        else:
            out.append((rec, 'not_a_call', why, ''))
    return out


__all__ = ['RESOLVERS', 'COMPARATORS', 'EVENTS', 'iso_day', 'add_days', 'today_iso', 'find_date', 'event_of', 'make_call', 'validate_call', 'options_of',
           'room_prediction', 'classify_answer', 'guided_call', 'forecast_calls', 'record_guided', 'knowable_passed', 'log_on_purpose', 'classify_open_rows']
