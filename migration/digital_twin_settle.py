"""The daily check that settles the Digital Twins IQ track record
(2026-11-20).

Jenna, 2026-11-20, in a choice form: "All of it, starting with the two
flaws and the daily scoring". Once a day on the build box
(``twins-track-record-settle.timer``, about an hour after the day's data
lands) every open call is checked by its own resolver:

  counted    our own count (``digital_twin_counted``) once the window has
             closed plus a day
  published  a published figure (a premiere's first week, an opening
             weekend), looked up once a day after the due day with the
             approved-source rules the Gen Pop calibration follows
  certified  a certified result (an election), the same way
  client     the client's own results: on the due day the person who
             made the call gets one email from Prometheus asking for them;
             the result comes in through the Add your result button

A Pilot Testing forecast made before its title aired carries no due day
yet: the check finds the air date (the forecast calibration record
first, then one lookup a week) and fills the due day and the window in.

Rules held here:
  * one runner: an S3 lock (conditional put), stale after three hours;
    a second run while one holds the lock does nothing
  * idempotent: a settled call is never checked again, an outcome is
    written once (``record_outcome`` refuses a different one), a client
    is asked once per call
  * never guessed: a lookup that did not search, found nothing, or found
    no source is retried the next day; after five days running the
    call goes on the failure alert (jenna@, jessie@)
  * post hoc: a call made on or after the day its result started to
    exist is marked post hoc and never public; its outcome is kept for
    audit and counts toward nothing
  * a miss stays: nothing here deletes or revises a scored call
  * catches up: everything due is checked on every run, so a missed day
    (a restart) is picked up by the next run

The weekly summary of newly scored calls, hits and misses goes to
jenna@, jessie@ and liz@ from the internal sender (a business report);
the failure alert to jenna@ and jessie@ only. No test sends mail
(``prometheus.mail_guard``).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import traceback
import uuid
from typing import Callable, Optional

from migration import digital_twin_calls as CALLS
from migration import digital_twin_ledger as LG

S3_BUCKET = LG.S3_BUCKET
LOCK_KEY = LG.LEDGER_PREFIX + 'settle_lock.json'
STATE_KEY = LG.LEDGER_PREFIX + 'settle_state.json'
LOCK_STALE_S = 3 * 3600
ALERT_AFTER = 5                      # failed daily checks in a row after the due day
REALERT_S = 7 * 86400
AIRED_RECHECK_DAYS = 7
DASHBOARD = 'https://dashboard.crosswalknyc.com'
PUBLIC_PAGE = 'https://www.crosswalknyc.com/twins-track-record.html'
INTERNAL_SENDER = 'BehavioralGraph <jenna@crosswalknyc.com>'
SUMMARY_TO = ('jenna@crosswalknyc.com', 'jessie@crosswalknyc.com', 'liz@crosswalknyc.com')
ALERT_TO = ('jenna@crosswalknyc.com', 'jessie@crosswalknyc.com')
USERS_KEY = 'system/users.json'

LOOKUP_SYSTEM = """You check one real-world result for a call that was written down before it happened. Search the web. Count only these sources:
- an election: the official results of the election authority (the secretary of state, the state or county board of elections), certified or the final official canvass; a media projection or an unofficial count is not enough;
- a premiere's first week: the network's or platform's own published counts, or the established measurement services' published figures as reported by the trade press;
- an opening weekend: the domestic opening weekend gross as reported by the standard box office trackers;
- anything else: SEC filings and earnings reports, Pew Research, Nielsen, Statista, eMarketer, YouGov, app analytics (Sensor Tower, data.ai).
Never use social posts, store visits, cable or satellite reach, or a figure with no named source. If the result is not out yet, or the sources disagree, answer known false. Never guess.
JSON only:
{"known": true|false, "value": <number or null>, "unit": "<people|pct|usd|points|null>", "winner": "<the exact option that won, or null>",
 "distribution": [{"label": "<option>", "pct": <share>}] or null, "happened": true|false|null, "as_of": "YYYY-MM-DD",
 "source_class": "certified result"|"published", "source": "<the source, named>", "note": "<one short line>"}"""


# ---------------------------------------------------------------- time
def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))


def _epoch(s) -> Optional[float]:
    try:
        return _dt.datetime.strptime(str(s)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=_dt.timezone.utc).timestamp()
    except Exception:
        try:
            return _dt.datetime.strptime(str(s)[:10], '%Y-%m-%d').replace(tzinfo=_dt.timezone.utc).timestamp()
        except Exception:
            return None


# ---------------------------------------------------------------- S3 documents
def _get_json(s3, key: str):
    if s3 is None:
        return LG._MEM.setdefault('settle', {}).get(key)
    try:
        r = s3.get_object(Bucket=S3_BUCKET, Key=key)
        raw = r['Body'].read()
        return json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw), str(r.get('ETag') or '').strip('"') or None
    except Exception:
        return None


def _doc(s3, key: str):
    got = _get_json(s3, key)
    if s3 is None:
        return got
    return got[0] if isinstance(got, tuple) else None


def _put_json(s3, key: str, doc: dict, **kw) -> None:
    if s3 is None:
        LG._MEM.setdefault('settle', {})[key] = doc
        return
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, ensure_ascii=False, indent=1).encode('utf-8'), ContentType='application/json', **kw)


def load_state(s3) -> dict:
    d = _doc(s3, STATE_KEY)
    return d if isinstance(d, dict) else {}


def save_state(s3, state: dict) -> None:
    try:
        _put_json(s3, STATE_KEY, state)
    except Exception as e:
        print(f"[track-record] state write failed: {str(e)[:120]}")


# ---------------------------------------------------------------- the lock
def acquire_lock(s3, owner: str, now: Optional[float] = None) -> bool:
    """Take the run's lock: written when missing (IfNoneMatch), taken
    over when stale (IfMatch on the etag read). A fresh lock held by
    another runner is a no. Without S3 (tests) the in-memory document
    plays the same rules."""
    now = float(now if now is not None else _now())
    body = {'owner': owner, 'at': now, 'host': os.environ.get('HOSTNAME') or '', 'pid': os.getpid()}
    if s3 is None:
        mem = LG._MEM.setdefault('settle', {})
        cur = mem.get(LOCK_KEY)
        if isinstance(cur, dict) and cur.get('owner') != owner and now - float(cur.get('at') or 0) < LOCK_STALE_S:
            return False
        mem[LOCK_KEY] = body
        return True
    got = _get_json(s3, LOCK_KEY)
    try:
        if not isinstance(got, tuple):
            _put_json(s3, LOCK_KEY, body, IfNoneMatch='*')
            return True
        doc, etag = got
        if isinstance(doc, dict) and doc.get('owner') != owner and now - float(doc.get('at') or 0) < LOCK_STALE_S:
            return False
        if etag:
            _put_json(s3, LOCK_KEY, body, IfMatch=etag)
        else:
            _put_json(s3, LOCK_KEY, body)
        return True
    except Exception as e:
        print(f"[track-record] lock not taken: {str(e)[:120]}")
        return False


def release_lock(s3, owner: str) -> None:
    if s3 is None:
        mem = LG._MEM.setdefault('settle', {})
        if (mem.get(LOCK_KEY) or {}).get('owner') == owner:
            mem.pop(LOCK_KEY, None)
        return
    got = _get_json(s3, LOCK_KEY)
    if isinstance(got, tuple) and isinstance(got[0], dict) and got[0].get('owner') != owner:
        return
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=LOCK_KEY)
    except Exception:
        pass


# ---------------------------------------------------------------- lookups
def default_lookup(rec: dict, spec: dict) -> Optional[dict]:
    """One lookup on the research desk (web search on) for a published or
    certified result. None when no model answers, no search ran, or the
    answer names no source: the call is retried the next day."""
    try:
        from migration.digital_twin_room import Models, model_available
    except Exception:
        return None
    if not model_available():
        return None
    meas = spec.get('measure') or {}
    user = json.dumps({'call': {'subject': rec.get('subject'), 'question': rec.get('question'), 'measure': meas, 'scored_as': spec.get('comparator'),
                                'event': spec.get('event'), 'due': spec.get('due_at'), 'options': meas.get('options')},
                       'today': CALLS.today_iso()}, ensure_ascii=False, default=str)
    try:
        data, crec = Models().call('research', LOOKUP_SYSTEM, user, max_tokens=700, temperature=0.1)
    except Exception as e:
        print(f"[track-record] lookup failed: {str(e)[:160]}")
        return None
    if not isinstance(data, dict):
        return None
    data = dict(data)
    data['_searches'] = int((crec or {}).get('searches') or 0)
    return data


def default_aired(twin: dict, sess: dict) -> Optional[dict]:
    try:
        from migration.digital_twin_forecast import aired_performance
        return aired_performance(twin, sess)
    except Exception as e:
        print(f"[track-record] aired check failed: {str(e)[:160]}")
        return None


def _trusted(found: Optional[dict]) -> bool:
    if not isinstance(found, dict) or found.get('known') is not True:
        return False
    if not str(found.get('source') or '').strip():
        return False
    return int(found.get('_searches', 1) or 0) > 0


def _outcome_from_lookup(found: dict, spec: dict) -> Optional[dict]:
    comp = spec.get('comparator')
    out = {'source_class': 'certified result' if spec.get('resolver') == 'certified' else 'published',
           'note': (str(found.get('note') or '') + (' Source: ' + str(found.get('source')) if found.get('source') else ''))[:380],
           'observed_at': CALLS.iso_day(found.get('as_of')) + 'T00:00:00Z' if CALLS.iso_day(found.get('as_of')) else ''}
    dist = [d for d in (found.get('distribution') or []) if isinstance(d, dict) and d.get('label') is not None and d.get('pct') is not None]
    if comp == 'winner':
        if not found.get('winner'):
            return None
        out['winner'] = str(found['winner'])
        if dist:
            out['distribution'] = dist
    elif comp == 'split':
        if not dist:
            return None
        out['distribution'] = dist
    elif comp == 'yes_no':
        if found.get('happened') is None:
            return None
        out['happened'] = bool(found['happened'])
    else:
        try:
            out['value'] = float(found.get('value'))
        except (TypeError, ValueError):
            return None
    return out


# ---------------------------------------------------------------- one call
def _post_hoc(rec: dict, spec: dict) -> bool:
    known = CALLS.iso_day(spec.get('knowable_at'))
    made = CALLS.iso_day(rec.get('created_at'))
    return bool(known and made and made >= known)


def _fail(s3, rec: dict, why: str, now: float, *, days: int = 1) -> dict:
    ch = dict(rec.get('checks') or {})
    ch['attempts'] = int(ch.get('attempts') or 0) + 1
    ch['last_error'] = str(why)[:200]
    ch['last_checked_at'] = _iso(now)
    ch['next_check_at'] = _iso(now + days * 86400)
    LG.update_call(s3, rec['id'], checks=ch)
    return {'id': rec['id'], 'result': 'retry', 'why': why, 'attempts': ch['attempts']}


def _checked(s3, rec: dict, now: float, **extra) -> None:
    ch = dict(rec.get('checks') or {})
    ch.update({'last_checked_at': _iso(now), 'last_error': '', 'attempts': 0})
    ch.update(extra)
    LG.update_call(s3, rec['id'], checks=ch)


def _session_of(payload: dict, sid: str) -> Optional[dict]:
    for k in ('pilot_sessions', 'panel_sessions'):
        for s in (payload or {}).get(k) or []:
            if isinstance(s, dict) and str(s.get('id') or '') == str(sid or ''):
                return s
    return None


def _air_date_on_file(s3, sid: str) -> dict:
    """What the forecast calibration record already knows about the
    session's title: {'aired_on', 'people'} (empty when nothing)."""
    try:
        from migration.digital_twin_forecast import load_ledger
        cal = load_ledger(s3) or {}
    except Exception:
        return {}
    for r in cal.get('records') or []:
        if isinstance(r, dict) and str(r.get('session_id') or '') == str(sid) and r.get('aired'):
            return {'aired_on': CALLS.iso_day(r.get('aired')), 'people': r.get('actual')}
    ck = (cal.get('checks') or {}).get(str(sid)) if isinstance(cal.get('checks'), dict) else None
    if isinstance(ck, dict) and ck.get('aired') and CALLS.iso_day(ck.get('aired_on')):
        return {'aired_on': CALLS.iso_day(ck.get('aired_on')), 'people': ck.get('people')}
    return {}


def _find_air_date(s3, rec: dict, spec: dict, now: float, *, load_twin: Callable, aired_fn: Callable) -> tuple:
    """(spec with the due day filled in, None) when the air date is
    known; (None, retry dict) when it is not yet."""
    sid = str(rec.get('session_id') or '')
    on_file = _air_date_on_file(s3, sid)
    found = None
    if not on_file:
        nxt = _epoch((rec.get('checks') or {}).get('next_air_check_at'))
        if nxt and nxt > now:
            return None, {'id': rec['id'], 'result': 'waiting', 'why': 'the title has not aired yet'}
        payload = load_twin(str(rec.get('twin_key') or '')) if rec.get('twin_key') else None
        sess = _session_of(payload, sid) if payload else None
        if not (payload and sess):
            return None, _fail(s3, rec, 'the test behind the call could not be read', now)
        found = aired_fn(payload, sess)
        if not isinstance(found, dict):
            return None, _fail(s3, rec, 'the air date could not be checked', now)
        if not found.get('aired') or not CALLS.iso_day(found.get('aired_on')):
            ch = dict(rec.get('checks') or {})
            ch.update({'last_checked_at': _iso(now), 'next_air_check_at': _iso(now + AIRED_RECHECK_DAYS * 86400), 'last_error': ''})
            LG.update_call(s3, rec['id'], checks=ch)
            return None, {'id': rec['id'], 'result': 'waiting', 'why': 'the title has not aired yet'}
        on_file = {'aired_on': CALLS.iso_day(found.get('aired_on')), 'people': found.get('people')}
    aired = on_file['aired_on']
    metric = str((spec.get('measure') or {}).get('metric') or '')
    upd = {'knowable_at': aired}
    meas = dict(spec.get('measure') or {})
    if metric == 'week_one_audience':
        upd['due_at'] = CALLS.add_days(aired, 8)
        meas.update({'window_start': aired, 'window_end': CALLS.add_days(aired, 7)})
    elif metric == 'return_pct':
        upd['due_at'] = CALLS.add_days(aired, 15)
        meas.update({'window_start': aired, 'window_end': CALLS.add_days(aired, 14)})
    upd['measure'] = meas
    LG.update_call(s3, rec['id'], call=upd)
    spec2 = dict(spec)
    spec2.update(upd)
    if on_file.get('people'):
        spec2['_people_on_file'] = on_file.get('people')
    return spec2, None


def check_call(s3, rec: dict, now: float, *, connect=None, lookup: Callable = default_lookup, aired_fn: Callable = default_aired,
               load_twin: Optional[Callable] = None, ask_client: Optional[Callable] = None, dry_run: bool = False) -> dict:
    """One open call: settled, asked for, waiting or retried. Never raises."""
    rid = rec.get('id')
    try:
        spec = dict(rec.get('call') or {})
        if not spec:
            return {'id': rid, 'result': 'skipped', 'why': 'no check on the call'}
        today = CALLS.today_iso(now)
        nxt = _epoch((rec.get('checks') or {}).get('next_check_at'))
        if nxt and nxt > now:
            return {'id': rid, 'result': 'waiting', 'why': 'checked today already'}
        metric = str((spec.get('measure') or {}).get('metric') or '')
        if not spec.get('due_at') and metric in ('week_one_audience', 'return_pct') and rec.get('kind') in ('forecast', 'scorecard'):
            if load_twin is None:
                return {'id': rid, 'result': 'waiting', 'why': 'no air date yet'}
            spec2, wait = _find_air_date(s3, rec, spec, now, load_twin=load_twin, aired_fn=aired_fn)
            if wait:
                return wait
            spec = spec2
            rec = LG.load_record(s3, rid) or rec
        due = CALLS.iso_day(spec.get('due_at'))
        if not due:
            return {'id': rid, 'result': 'waiting', 'why': 'no due day yet'}
        if due > today:
            return {'id': rid, 'result': 'waiting', 'why': f'due on {due}'}
        if _post_hoc(rec, spec) and not rec.get('post_hoc'):
            if not dry_run:
                LG.update_call(s3, rid, post_hoc=True)
            rec = dict(rec, post_hoc=True)
        resolver = spec.get('resolver')
        if resolver == 'client':
            if (rec.get('checks') or {}).get('asked_at'):
                return {'id': rid, 'result': 'waiting', 'why': 'waiting on the client'}
            if dry_run:
                return {'id': rid, 'result': 'would ask', 'why': 'due; the client is asked for their results'}
            sent = ask_client(rec, spec) if callable(ask_client) else {'sent': False, 'reason': 'no mailer'}
            _checked(s3, rec, now, asked_at=_iso(now), asked=bool((sent or {}).get('sent')), ask_note=str((sent or {}).get('reason') or '')[:120])
            return {'id': rid, 'result': 'asked', 'why': 'the client was asked for their results' if (sent or {}).get('sent') else f"not sent: {(sent or {}).get('reason')}"}
        if resolver == 'counted':
            from migration import digital_twin_counted as COUNT
            try:
                got = COUNT.settle(spec, connect=connect)
            except ValueError as e:
                return _fail(s3, rec, str(e), now) if not dry_run else {'id': rid, 'result': 'retry', 'why': str(e)}
            except COUNT.Unavailable as e:
                return _fail(s3, rec, f'the count could not be taken: {e}', now) if not dry_run else {'id': rid, 'result': 'retry', 'why': str(e)}
            if dry_run:
                return {'id': rid, 'result': 'would settle', 'value': got['value']}
            out = LG.record_outcome(s3, rid, got['value'], source_class='our own count', note=got.get('note') or '', observed_at=_iso(now))
            _checked(s3, rec, now)
            return {'id': rid, 'result': 'settled' if out else 'refused', 'value': got['value'], 'post_hoc': bool(rec.get('post_hoc'))}
        if resolver in ('published', 'certified'):
            if spec.get('_people_on_file') and str((spec.get('measure') or {}).get('metric') or '') == 'week_one_audience':
                found = {'known': True, 'value': spec['_people_on_file'], 'source': 'the title\'s week-one figure on file', 'as_of': spec.get('due_at'), '_searches': 1}
            else:
                found = lookup(rec, spec)
            if not _trusted(found):
                return _fail(s3, rec, 'the result is not out yet, or no source was found', now) if not dry_run else {'id': rid, 'result': 'retry', 'why': 'not out yet'}
            out = _outcome_from_lookup(found, spec)
            if out is None:
                return _fail(s3, rec, 'the result did not match how the call is scored', now) if not dry_run else {'id': rid, 'result': 'retry', 'why': 'no match'}
            if dry_run:
                return {'id': rid, 'result': 'would settle', 'outcome': out}
            done = LG.record_outcome(s3, rid, out.get('value'), distribution=out.get('distribution'), happened=out.get('happened'), winner=out.get('winner'),
                                     source_class=out['source_class'], note=out.get('note') or '', observed_at=out.get('observed_at') or _iso(now))
            _checked(s3, rec, now)
            return {'id': rid, 'result': 'settled' if done else 'refused', 'post_hoc': bool(rec.get('post_hoc'))}
        return {'id': rid, 'result': 'skipped', 'why': f'no check named {resolver}'}
    except Exception as e:
        traceback.print_exc()
        return {'id': rid, 'result': 'error', 'why': str(e)[:200]}


# ---------------------------------------------------------------- the client ask
def _users(s3) -> dict:
    d = _doc(s3, USERS_KEY) if s3 is not None else None
    return d if isinstance(d, dict) else {}


def _seat(users: dict, username: str) -> dict:
    u = (users or {}).get('users') or {}
    rec = u.get(str(username or ''))
    if isinstance(rec, dict):
        return rec
    low = str(username or '').lower()
    for k, v in u.items():
        if isinstance(v, dict) and (str(k).lower() == low or str(v.get('email') or '').lower() == low):
            return v
    return {}


def _said(rec: dict) -> str:
    pred = rec.get('prediction') or {}
    unit = str(pred.get('unit') or '')
    if pred.get('point') is not None:
        v = pred['point']
        if unit == 'pct':
            return f"{float(v):.1f}%"
        if unit == 'people':
            return f"{int(round(float(v))):,} people"
        return f"{v}"
    if pred.get('call'):
        return str(pred['call'])
    return 'the split the room gave'


def client_ask(rec: dict, spec: dict, *, users: dict, send: Optional[Callable] = None) -> dict:
    """The email that asks the person who made a call for their results:
    from Prometheus through the house door (Reply-To jenna@, Jenna BCC,
    Liz BCC by the door's default). Never raises."""
    try:
        from migration.digital_twin_session_mail import _first_name
    except Exception:
        def _first_name(r, u):   # noqa: E306
            return 'there'
    who = str(rec.get('created_by') or '')
    seat = _seat(users, who)
    to = str(seat.get('email') or (who if '@' in who else '')).strip()
    if not to:
        return {'sent': False, 'reason': 'no address on the seat'}
    first = _first_name(seat, who)
    subject = f"A call is due: {rec.get('subject')}"
    what = ' '.join(str(rec.get('question') or (spec.get('measure') or {}).get('what') or '').split())
    called = CALLS.iso_day(rec.get('created_at'))
    lines = [f"Hi {first},", '',
             f"On {called}, a call on {rec.get('subject')} went on the record: {what.rstrip('.')}. We said {_said(rec)}.",
             '', 'Its result is due now. When your own numbers are in, add them to the call and it is scored.', '',
             f"Add your result: {DASHBOARD}/?view=digitalTwinsIQ&record=1", '', 'Prometheus', 'Crosswalk']
    body = '\n'.join(lines)
    if send is None:
        from prometheus.outbound_mail import send_user_email as send
    return send(to=to, subject=subject[:150], body=body, instructed=True, caller='track-record-due', family='sizing')


# ---------------------------------------------------------------- internal mail
def _send_internal(to, subject: str, body: str, *, send: Optional[Callable] = None, dry_run: bool = False) -> dict:
    if dry_run:
        return {'sent': False, 'reason': 'dry run'}
    if send is not None:
        return send(list(to), subject, body)
    try:
        from prometheus.mail_guard import sending_allowed
        if not sending_allowed():
            return {'sent': False, 'reason': 'test mode'}
    except Exception:
        if (os.environ.get('REGRESSION_TEST_MODE') or '').strip() == '1':
            return {'sent': False, 'reason': 'test mode'}
    try:
        import boto3
        boto3.client('ses', region_name='us-east-2').send_email(Source=INTERNAL_SENDER, Destination={'ToAddresses': list(to)},
                                                                Message={'Subject': {'Data': subject}, 'Body': {'Text': {'Data': body}}})
        return {'sent': True}
    except Exception as e:
        traceback.print_exc()
        return {'sent': False, 'reason': str(e)[:160]}


def _miss_words(rec: dict) -> str:
    sc = rec.get('score') or {}
    if sc.get('hit') is True:
        return 'called right'
    if sc.get('hit') is False:
        return 'called wrong'
    if sc.get('tvd') is not None:
        return f"{float(sc['tvd']):.1f} points off on the split"
    if sc.get('rel_error') is not None:
        return f"{float(sc['rel_error']) * 100:.1f}% off"
    if sc.get('abs_error') is not None:
        return f"{float(sc['abs_error']):.1f} off"
    return 'scored'


def _happened(rec: dict) -> str:
    out = rec.get('outcome') or {}
    unit = str((rec.get('prediction') or {}).get('unit') or '')
    if out.get('value') is not None:
        v = out['value']
        if unit == 'pct':
            return f"{float(v):.1f}%"
        if unit == 'people':
            return f"{int(round(float(v))):,} people"
        return f"{v}"
    if out.get('winner'):
        return str(out['winner'])
    if out.get('happened') is not None:
        return 'it happened' if out['happened'] else 'it did not happen'
    return 'scored'


def weekly_summary(s3, *, now: Optional[float] = None, send: Optional[Callable] = None, dry_run: bool = False, force: bool = False) -> dict:
    """The week's newly scored calls, hits and misses, to Jenna, Jessie
    and Liz. Nothing is sent when nothing was scored and nothing is due
    or waiting (unless ``force``)."""
    now = float(now if now is not None else _now())
    state = load_state(s3)
    since = _epoch(state.get('last_summary_at')) or (now - 7 * 86400)
    rows = LG.load_index(s3).get('records') or []
    scored, open_calls, waiting, stuck = [], [], [], []
    for r in rows:
        if not LG.is_call(r):
            continue
        rec = LG.load_record(s3, r['id']) or {}
        if r.get('has_outcome'):
            at = _epoch((rec.get('outcome') or {}).get('observed_at')) or _epoch(((rec.get('checks') or {}).get('last_checked_at')))
            if at and at >= since and not rec.get('post_hoc'):
                scored.append(rec)
            continue
        if r.get('post_hoc'):
            continue
        open_calls.append(rec)
        ch = rec.get('checks') or {}
        if (rec.get('call') or {}).get('resolver') == 'client' and ch.get('asked_at'):
            waiting.append(rec)
        if int(ch.get('attempts') or 0) >= ALERT_AFTER:
            stuck.append(rec)
    if not (scored or waiting or stuck or force):
        return {'sent': False, 'reason': 'nothing new this week', 'scored': 0}
    hits = sum(1 for r in scored if (r.get('score') or {}).get('hit') is True)
    misses = sum(1 for r in scored if (r.get('score') or {}).get('hit') is False)
    subject = f"Track record this week: {len(scored)} call{'s' if len(scored) != 1 else ''} scored"
    lines = [f"{len(scored)} call{'s' if len(scored) != 1 else ''} scored since {CALLS.iso_day(_iso(since))}: {hits} called right, {misses} called wrong."
             if scored else f"No calls were scored since {CALLS.iso_day(_iso(since))}.", '']
    for r in scored:
        lines.append(f"- {r.get('subject')}: {' '.join(str(r.get('question') or r.get('measure') or '').split())[:160]} We said {_said(r)}; it landed at {_happened(r)} "
                     f"({_miss_words(r)}, {LG.outcome_label((r.get('outcome') or {}).get('source_class')) or 'scored'}).")
    nxt = sorted((CALLS.iso_day((r.get('call') or {}).get('due_at')), r) for r in open_calls if CALLS.iso_day((r.get('call') or {}).get('due_at')))
    lines += ['', f"Open calls: {len(open_calls)}." + (f" Next due: {nxt[0][1].get('subject')} on {nxt[0][0]}." if nxt else '')]
    if waiting:
        lines.append(f"Waiting on client results: {len(waiting)}.")
    if stuck:
        lines.append(f"Not settled after {ALERT_AFTER} daily checks: {len(stuck)}.")
    lines += ['', f"The public record: {PUBLIC_PAGE}"]
    body = '\n'.join(lines)
    res = _send_internal(SUMMARY_TO, subject, body, send=send, dry_run=dry_run)
    if res.get('sent') and not dry_run:
        state['last_summary_at'] = _iso(now)
        save_state(s3, state)
    return dict(res, scored=len(scored), hits=hits, misses=misses, subject=subject, body=body)


def _alert(s3, stuck: list, job_error: str, *, now: float, send: Optional[Callable], dry_run: bool) -> dict:
    lines = ['The track record check needs a look.', '']
    if job_error:
        lines += [f"The daily check failed twice in a row: {job_error}", '']
    for r in stuck:
        ch = r.get('checks') or {}
        lines.append(f"- {r.get('subject')}: {' '.join(str(r.get('question') or '').split())[:140]} Due {CALLS.iso_day((r.get('call') or {}).get('due_at'))}; "
                     f"{int(ch.get('attempts') or 0)} checks without a result ({ch.get('last_error') or 'no reason given'}).")
    return _send_internal(ALERT_TO, 'Track record check needs a look', '\n'.join(lines), send=send, dry_run=dry_run)


# ---------------------------------------------------------------- the run
def run(s3, *, now: Optional[float] = None, connect=None, lookup: Callable = default_lookup, aired_fn: Callable = default_aired,
        load_twin: Optional[Callable] = None, users: Optional[dict] = None, client_send: Optional[Callable] = None,
        internal_send: Optional[Callable] = None, dry_run: bool = False) -> dict:
    """One daily pass. Returns {'ran', 'results', 'settled', ...}."""
    now = float(now if now is not None else _now())
    owner = uuid.uuid4().hex[:12]
    if not acquire_lock(s3, owner, now):
        return {'ran': False, 'reason': 'another run holds the lock', 'results': []}
    state = load_state(s3)
    results, job_error = [], ''
    try:
        if load_twin is None and s3 is not None:
            def load_twin(key):   # noqa: E306
                try:
                    from migration import digital_twins as DT
                    return DT.load_twin(s3, key)
                except Exception:
                    return None
        if users is None:
            users = _users(s3)
        rows = [r for r in (LG.load_index(s3).get('records') or []) if LG.is_call(r) and not r.get('has_outcome')]
        for r in rows:
            rec = LG.load_record(s3, r['id'])
            if not rec or rec.get('outcome'):
                continue

            def ask(rec_, spec_):   # noqa: E306
                return client_ask(rec_, spec_, users=users, send=client_send)
            results.append(check_call(s3, rec, now, connect=connect, lookup=lookup, aired_fn=aired_fn, load_twin=load_twin, ask_client=ask, dry_run=dry_run))
        state['consecutive_failures'] = 0
    except Exception as e:
        traceback.print_exc()
        job_error = str(e)[:200]
        state['consecutive_failures'] = int(state.get('consecutive_failures') or 0) + 1
    finally:
        release_lock(s3, owner)
    stuck = []
    alerted = dict(state.get('alerted') or {})
    for r in (LG.load_index(s3).get('records') or []):
        if not LG.is_call(r) or r.get('has_outcome'):
            continue
        rec = LG.load_record(s3, r['id']) or {}
        if int((rec.get('checks') or {}).get('attempts') or 0) >= ALERT_AFTER:
            last = _epoch(alerted.get(rec['id']))
            if not last or now - last >= REALERT_S:
                stuck.append(rec)
    alert = None
    if stuck or (job_error and int(state.get('consecutive_failures') or 0) >= 2):
        alert = _alert(s3, stuck, job_error, now=now, send=internal_send, dry_run=dry_run)
        if alert.get('sent'):
            for r in stuck:
                alerted[r['id']] = _iso(now)
    if not dry_run:
        state.update({'last_run_at': _iso(now), 'alerted': alerted, 'last_results': results[-60:]})
        if not job_error:
            state['last_ok_at'] = _iso(now)
        save_state(s3, state)
    counts = {}
    for x in results:
        counts[x.get('result')] = counts.get(x.get('result'), 0) + 1
    return {'ran': True, 'results': results, 'counts': counts, 'alert': alert, 'error': job_error, 'checked': len(results)}


__all__ = ['run', 'check_call', 'weekly_summary', 'client_ask', 'acquire_lock', 'release_lock', 'load_state', 'LOOKUP_SYSTEM', 'SUMMARY_TO', 'ALERT_TO',
           'INTERNAL_SENDER', 'ALERT_AFTER']
