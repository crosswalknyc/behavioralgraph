"""The read a person gets after a room session (Jenna 2026-11-20).

Jenna, verbatim: "after a person finishes a session with a digital twin
(they ask questions and are gone for a good period of time, close the
room, browser, leave the room to go elsewhere) email that person the pdf
read of what has happened in thqat room."

One email, from Prometheus, to the person who held the session, with
the room's PDF read attached. The server decides when the session
ended, so a closed browser counts:

  * a VISIT is the activity of one person in one room since the last
    read they were sent. Every open, poll, ask and presence ping stamps
    ``last_activity`` on a small record under ACTIVE_PREFIX; an ask
    also counts toward ``asked``. The page sends a light hint when the
    room goes out of view (the room closed, another tab or product
    opened, the page unloaded); that sets ``leaving_at``. Coming back
    clears it.
  * the session ENDED when the person asked at least one question in
    this visit and then either nothing happened for QUIET_S (thirty
    minutes), or LEAVE_S (two minutes) passed after a leaving hint with
    nothing new since. A question still running holds the read until it
    lands; a Predictive Analytics run still running holds it too.
  * the SWEEPER (a thread in every web worker, every SWEEP_S) reads the
    visit records and sends the ones that are due. One S3 lock per
    visit (a conditional put) means three workers never send twice; a
    SENT marker per room and person remembers how many of their
    questions the last read covered, so a later visit with new
    questions earns a new read and a visit with none earns nothing.
  * only the person who held the session receives it. Probe identities
    never mail. The mail leaves through the house door
    (prometheus.outbound_mail.send_user_email): From Prometheus,
    Reply-To Jenna, Jenna on BCC, the PDF gated through the design
    pack, the copy through the house scrub. Nothing in the subject, the
    body or the file says how anything was produced.

Off under DTIQ_SESSION_MAIL=0 and in the regression harness (the
functions here still run when a test calls them with its own store).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import quote

VERSION = 1
BUCKET = os.environ.get('CORPUS_CATALOG_BUCKET', 'dashboard-inputs')
ACTIVE_PREFIX = 'system/digital_twins/session_mail/active/'
SENT_PREFIX = 'system/digital_twins/session_mail/sent/'
LOCK_PREFIX = 'system/digital_twins/session_mail/locks/'
DASHBOARD = 'https://dashboard.crosswalknyc.com'
SWEEP_S = 60.0                 # how often each worker looks for a visit that is due
STAMP_MIN_GAP_S = 30.0         # a poll re-stamps a visit at most this often per process (the room polls every few seconds while it works)
LOCK_STALE_S = 600.0           # a sender that died mid-send loses its lock after this
RETRY_WAIT_S = 600.0           # a failed send (SES) waits this long before the next try
MAX_ATTEMPTS = 5               # then the visit is dropped
CALLER = 'room-session-read'
# the door stamps a counting family on the copy; a room read counts people and shares, never plays, so it carries no view
# definition (the room's own headline can say 'viewers' of a title without the read being a viewing count)
FAMILY = 'sizing'
_LAST_STAMP: dict = {}         # (key, sid, user) -> epoch of the last poll stamp from this process
_STAMP_LOCK = threading.Lock()


def _env_s(name: str, default: float) -> float:
    try:
        v = float(str(os.environ.get(name) or '').strip() or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def quiet_s() -> float:
    """Silence that ends a session: thirty minutes, DTIQ_SESSION_MAIL_QUIET_S to override."""
    return _env_s('DTIQ_SESSION_MAIL_QUIET_S', 1800.0)


def leave_s() -> float:
    """After a leaving hint with nothing new: two minutes, DTIQ_SESSION_MAIL_LEAVE_S to override."""
    return _env_s('DTIQ_SESSION_MAIL_LEAVE_S', 120.0)


def _regression() -> bool:
    return str(os.environ.get('REGRESSION_TEST_MODE') or '').strip().lower() in ('1', 'true', 'yes')


def enabled() -> bool:
    """The hooks and the sweeper run: on by default, off under
    DTIQ_SESSION_MAIL=0, off in the regression harness unless a test
    sets DTIQ_SESSION_MAIL=1 on purpose."""
    flag = str(os.environ.get('DTIQ_SESSION_MAIL') or '').strip().lower()
    if flag in ('0', 'false', 'off', 'no'):
        return False
    if _regression():
        return flag in ('1', 'true', 'on', 'yes')
    return True


# ------------------------------------------------------------------ store
def _pair(s3):
    return s3, BUCKET


def _safe(s: str) -> str:
    raw = str(s or '')
    return re.sub(r"[^A-Za-z0-9]+", '_', raw).strip('_')[:120] + '_' + hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]


def visit_id(key: str, sid: str, user: str) -> str:
    return _safe(f"{key}|{sid}|{user}")


def visit_key(key: str, sid: str, user: str) -> str:
    return f"{ACTIVE_PREFIX}{visit_id(key, sid, user)}.json"


def sent_key(key: str, sid: str, user: str) -> str:
    return f"{SENT_PREFIX}{visit_id(key, sid, user)}.json"


def lock_key(vid: str) -> str:
    return f"{LOCK_PREFIX}{vid}.json"


def _read(s3, k: str):
    """(document, etag) or (None, None)."""
    s3b, bucket = _pair(s3)
    if s3b is None:
        return None, None
    try:
        r = s3b.get_object(Bucket=bucket, Key=k)
        doc = json.loads(r['Body'].read().decode('utf-8')) or {}
        return (doc if isinstance(doc, dict) else {}), (str(r.get('ETag') or '').strip('"') or None)
    except Exception:
        return None, None


def _write(s3, k: str, doc: dict, **cond) -> bool:
    s3b, bucket = _pair(s3)
    if s3b is None:
        return False
    try:
        s3b.put_object(Bucket=bucket, Key=k, Body=json.dumps(doc, ensure_ascii=False, default=str).encode('utf-8'), ContentType='application/json', **cond)
        return True
    except Exception as e:
        if not cond and not _regression():
            print(f"[twin-session-mail] write skipped {k[-40:]}: {str(e)[:100]}")
        return False


def _delete(s3, k: str) -> None:
    s3b, bucket = _pair(s3)
    if s3b is None:
        return
    try:
        s3b.delete_object(Bucket=bucket, Key=k)
    except Exception:
        pass


def _list(s3, prefix: str) -> list:
    s3b, bucket = _pair(s3)
    if s3b is None:
        return []
    out, token = [], None
    try:
        while True:
            kw = {'Bucket': bucket, 'Prefix': prefix}
            if token:
                kw['ContinuationToken'] = token
            r = s3b.list_objects_v2(**kw) or {}
            out.extend(str(o.get('Key') or '') for o in (r.get('Contents') or []) if str(o.get('Key') or '').endswith('.json'))
            token = r.get('NextContinuationToken') if r.get('IsTruncated') else None
            if not token:
                break
    except Exception as e:
        if not _regression():
            print(f"[twin-session-mail] list skipped: {str(e)[:100]}")
    return out


# --------------------------------------------------------------- the probe
def is_probe(user: str, record: Optional[dict] = None, s3=None) -> bool:
    u = str(user or '').strip()
    if not u:
        return True
    try:
        from prometheus.probe_identity import is_probe_user
        return bool(is_probe_user(u, record, s3=s3))
    except Exception:
        low = u.lower()
        return low.startswith('canary') or low in ('smoke', 'probe', 'replay', 'sandbox_partner')


# ---------------------------------------------------------- the activity
KINDS = ('open', 'poll', 'ask', 'here', 'leaving')


def note_activity(s3, key: str, sid: str, user: str, kind: str, now: Optional[float] = None) -> Optional[dict]:
    """Stamp the visit: ``last_activity`` moves on everything but a
    leaving hint, an ask counts, a leaving hint sets ``leaving_at`` and
    anything after it clears it. A poll re-stamps at most every
    STAMP_MIN_GAP_S per process. Returns the record written, or None
    when nothing was. Never raises."""
    try:
        kind = str(kind or '').strip().lower()
        if kind not in KINDS or not key or not sid or not user:
            return None
        if is_probe(user, s3=s3):
            return None
        now = float(now or time.time())
        tag = (str(key), str(sid), str(user))
        if kind == 'poll':
            with _STAMP_LOCK:
                last = float(_LAST_STAMP.get(tag) or 0.0)
                if now - last < STAMP_MIN_GAP_S:
                    return None
                _LAST_STAMP[tag] = now
        else:
            with _STAMP_LOCK:
                _LAST_STAMP[tag] = now
        vk = visit_key(key, sid, user)
        rec, _etag = _read(s3, vk)
        if not rec:
            rec = {'v': VERSION, 'key': str(key), 'session_id': str(sid), 'user': str(user), 'opened_at': now, 'asked': 0,
                   'first_ask_at': None, 'last_ask_at': None, 'attempts': 0, 'last_attempt': None}
        if kind == 'leaving':
            rec['leaving_at'] = now
        else:
            rec['last_activity'] = now
            rec['leaving_at'] = None
        if kind == 'ask':
            rec['asked'] = int(rec.get('asked') or 0) + 1
            rec['first_ask_at'] = rec.get('first_ask_at') or now
            rec['last_ask_at'] = now
        rec.setdefault('last_activity', now)
        rec['last_kind'] = kind
        return rec if _write(s3, vk, rec) else None
    except Exception:
        traceback.print_exc()
        return None


def visits(s3) -> list:
    """Every visit record in the store."""
    out = []
    for k in _list(s3, ACTIVE_PREFIX):
        doc, _etag = _read(s3, k)
        if doc and doc.get('key') and doc.get('session_id') and doc.get('user'):
            doc['_store_key'] = k
            out.append(doc)
    return out


# -------------------------------------------------------------- the judge
def _in_flight(q: dict) -> bool:
    try:
        from migration.digital_twin_room_jobs import in_flight
        return bool(in_flight(q))
    except Exception:
        return str((q or {}).get('stage') or '') in ('researching', 'the room is thinking', 'moderator is reading', 'picking it back up')


def own_questions(sess: dict, user: str) -> list:
    """The questions this person asked in the room themselves, in order:
    the ones a Predictive Analytics plan asked for them are not theirs."""
    u = str(user or '')
    out = []
    for q in (sess or {}).get('questions') or []:
        if not isinstance(q, dict) or q.get('guided_i') is not None:
            continue
        if str(q.get('asked_by') or (sess or {}).get('created_by') or '') != u:
            continue
        out.append(q)
    return out


def finished_questions(sess: dict, user: str) -> list:
    return [q for q in own_questions(sess, user) if not _in_flight(q) and str(q.get('stage') or '') != 'failed']


def guided_running(sess: dict) -> bool:
    g = (sess or {}).get('guided')
    return isinstance(g, dict) and str(g.get('status') or '') == 'running'


def judge(rec: dict, sess: Optional[dict], sent: Optional[dict], now: Optional[float] = None) -> tuple:
    """('send' | 'wait' | 'drop', reason) for one visit record."""
    now = float(now or time.time())
    if not sess:
        return 'drop', 'the room is gone'
    user = str(rec.get('user') or '')
    last = float(rec.get('last_activity') or rec.get('opened_at') or 0.0)
    leaving = rec.get('leaving_at')
    quiet = (now - last) >= quiet_s()
    left = bool(leaving) and (now - float(leaving)) >= leave_s() and last <= float(leaving) + 0.001
    if int(rec.get('asked') or 0) < 1:
        return ('drop', 'no question asked this visit') if (quiet or left) else ('wait', 'nothing asked yet')
    if guided_running(sess):
        return 'wait', 'a Predictive Analytics run is still going'
    if any(_in_flight(q) for q in own_questions(sess, user)):
        return 'wait', 'a question is still running'
    done = finished_questions(sess, user)
    covered = int((sent or {}).get('questions_at_send') or 0)
    if len(done) <= covered:
        return ('drop', 'nothing new since the last read') if (quiet or left) else ('wait', 'nothing new since the last read')
    attempts = int(rec.get('attempts') or 0)
    if attempts >= MAX_ATTEMPTS:
        return 'drop', 'the read could not be sent'
    if rec.get('last_attempt') and (now - float(rec.get('last_attempt') or 0.0)) < RETRY_WAIT_S:
        return 'wait', 'waiting to try again'
    if left:
        return 'send', 'the room was left'
    if quiet:
        return 'send', 'the room went quiet'
    return 'wait', 'still in the room'


# ---------------------------------------------------------------- the copy
def _first_name(user_rec: Optional[dict], user: str) -> str:
    """The first name on the seat, else the first part of the address,
    else the username; 'there' when nothing is on file."""
    rec = user_rec or {}
    first = str(rec.get('first_name') or rec.get('firstName') or '').strip()
    if first:
        return first
    name = str(rec.get('name') or rec.get('display_name') or '').strip()
    if name:
        return name.split()[0]
    for cand in (str(rec.get('email') or ''), str(user or '')):
        u = cand.strip()
        if '@' in u:
            u = u.split('@')[0]
        parts = [p for p in re.split(r"[._\-\s]+", u) if p and p.isalpha()]
        if parts:
            return parts[0].capitalize()
    return 'there'


def _clean(s: str) -> str:
    return ' '.join(str(s or '').replace('\u2014', '-').replace('\u2013', '-').split())


def _day(ts) -> str:
    try:
        d = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return d.strftime('%A, %B ') + str(d.day)
    except Exception:
        return ''


def _iso_to_epoch(s) -> Optional[float]:
    try:
        return datetime.strptime(str(s), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def room_link(key: str, sid: str) -> str:
    """The room on the dashboard: the twin and the room. The page opens
    the Focus Group IQ tab for a link that names a room, so the link
    carries no tab id (the house door reads that id as a word it will
    not send)."""
    return f"{DASHBOARD}/?view=digitalTwinsIQ&twin={quote(str(key or ''), safe='')}&room={quote(str(sid or ''), safe='')}"


def _headline(q: dict) -> str:
    for k in ('headline', 'read_short', 'read'):
        v = _clean((q or {}).get(k) or '')
        if v:
            return v if v.endswith(('.', '!', '?')) else v + '.'
    dist = [d for d in ((q or {}).get('distribution') or []) if isinstance(d, dict) and d.get('label')]
    if dist:
        top = max(dist, key=lambda d: float(d.get('pct') or 0))
        try:
            return f"{_clean(top['label'])}: {float(top.get('pct') or 0):.0f}%."
        except Exception:
            return _clean(top['label']) + '.'
    return ''


def compose(payload: dict, sess: dict, rec: dict, user_rec: Optional[dict], *, neutral: bool = False) -> dict:
    """The subject, the body and the file name. ``neutral`` drops the
    names the user chose (a room title or a question can carry a word
    the house door refuses); the rest of the copy is ours."""
    m = (payload or {}).get('meta') or {}
    twin = _clean(m.get('name') or m.get('subject') or 'your twin') if not neutral else 'your twin'
    title = _clean((sess or {}).get('title') or '')
    if neutral or title.lower() in ('', 'room', 'focus group room'):
        title = ''
    user = str(rec.get('user') or '')
    done = finished_questions(sess, user)
    n_visit = max(1, min(int(rec.get('asked') or 1), len(done))) if done else int(rec.get('asked') or 1)
    this_visit = done[-n_visit:] if done else []
    n = len(this_visit) or int(rec.get('asked') or 1)
    first = _first_name(user_rec, user)
    # when: the day the questions were asked, and how long the session ran
    t0 = rec.get('first_ask_at') or _iso_to_epoch((this_visit[0] if this_visit else {}).get('asked_at')) or rec.get('opened_at')
    t1 = rec.get('last_ask_at') or _iso_to_epoch((this_visit[-1] if this_visit else {}).get('finished_at')) or rec.get('last_activity') or t0
    when = ''
    day = _day(t0)
    if day:
        when = f" on {day}"
    try:
        mins = int(round((float(t1) - float(t0)) / 60.0))
    except Exception:
        mins = 0
    if mins >= 2:
        when += f", over {mins} minutes"
    where = f"your room on {twin}" + (f' ("{title}")' if title else '')
    qword = 'question' if n == 1 else 'questions'
    lines = [f"Hi {first},", '', f"Here is the read from {where}. You asked {n} {qword}{when}."]
    heads = []
    if not neutral:
        for q in this_visit[-2:]:
            h = _headline(q)
            qt = _clean(q.get('q') or '')
            if h and qt:
                heads.append(f"{qt} {h}")
            elif h:
                heads.append(h)
    if heads:
        lines += ['', 'The headline reads:'] + [f"- {h}" for h in heads]
    lines += ['', 'The full read is attached: every question, how the room split, and what the people in it said.', '',
              f"Open the room: {room_link(str(rec.get('key') or ''), str(rec.get('session_id') or ''))}", '', 'Prometheus', 'Crosswalk']
    subject = f"Your read from the room on {twin}" if not title else f"Your read: {title}"
    if neutral:
        subject = 'Your read from the room'
    try:
        from migration.digital_twin_exports import panel_pdf_filename
        fname = panel_pdf_filename(payload, sess)
    except Exception:
        fname = 'Focus_Group_IQ_read.pdf'
    return {'subject': subject[:150], 'body': '\n'.join(lines), 'pdf_name': fname, 'questions': n}


# ---------------------------------------------------------------- the send
def _default_sender(**kw) -> dict:
    from prometheus.outbound_mail import send_user_email
    return send_user_email(**kw)


def _pdf(payload: dict, sess: dict, portrait_fn: Optional[Callable]) -> Optional[bytes]:
    try:
        from migration.digital_twin_exports import build_panel_pdf
        data = build_panel_pdf(payload, sess, portrait_fn=portrait_fn)
        return data if data and len(data) > 100 else None
    except Exception:
        traceback.print_exc()
        return None


def _refused(out: dict) -> bool:
    r = str((out or {}).get('reason') or '')
    return r.startswith('banned vocabulary') or r.startswith('explains what happened')


def send_read(s3, rec: dict, payload: dict, sess: dict, *, user_rec: Optional[dict], portrait_fn: Optional[Callable] = None,
              sender: Optional[Callable] = None, now: Optional[float] = None) -> dict:
    """Build the room's PDF, write the mail, send it through the house
    door. Returns the door's record plus ``handled``: True when the
    visit is finished with (sent, or suppressed for a reason that will
    not change), False when it should be tried again later."""
    now = float(now or time.time())
    user = str(rec.get('user') or '')
    out = {'sent': False, 'handled': False, 'reason': ''}
    email = str((user_rec or {}).get('email') or '').strip()
    if not email or '@' not in email:
        out.update({'handled': True, 'reason': 'no email on the seat'})
        return out
    if is_probe(user, user_rec, s3=s3):
        out.update({'handled': True, 'reason': 'probe identity'})
        return out
    pdf = _pdf(payload, sess, portrait_fn)
    if pdf is None:
        out.update({'handled': False, 'reason': 'the read could not be written'})
        return out
    send = sender or _default_sender
    msg = compose(payload, sess, rec, user_rec)
    try:
        door = send(to=email, subject=msg['subject'], body=msg['body'], instructed=True, caller=CALLER, pdf=pdf, pdf_name=msg['pdf_name'], bcc_liz=False, family=FAMILY) or {}
    except Exception as e:
        traceback.print_exc()
        door = {'sent': False, 'reason': f"send failed: {e}"[:200]}
    if not door.get('sent') and _refused(door):
        # a name the user chose carried a word the door will not send; the same read with our words only
        msg = compose(payload, sess, rec, user_rec, neutral=True)
        try:
            door = send(to=email, subject=msg['subject'], body=msg['body'], instructed=True, caller=CALLER, pdf=pdf, pdf_name=msg['pdf_name'], bcc_liz=False, family=FAMILY) or {}
        except Exception as e:
            traceback.print_exc()
            door = {'sent': False, 'reason': f"send failed: {e}"[:200]}
    out.update({k: door.get(k) for k in ('sent', 'reason', 'message_id', 'destinations') if k in door})
    out['subject'] = msg['subject']
    out['questions'] = msg['questions']
    reason = str(door.get('reason') or '')
    if door.get('sent'):
        out['handled'] = True
    elif reason.startswith('send failed') or reason.startswith('no recipient'):
        out['handled'] = False
    else:
        out['handled'] = True        # test mode, a probe ask, not instructed, refused twice: nothing more to do with this visit
    return out


# --------------------------------------------------------------- the lock
def _owner() -> str:
    return f"{os.environ.get('RENDER_INSTANCE_ID') or os.environ.get('HOSTNAME') or 'local'}:{os.getpid()}:{threading.get_ident()}"


def lock_fresh(doc, now: Optional[float] = None) -> bool:
    return isinstance(doc, dict) and (float(now or time.time()) - float(doc.get('at') or 0.0)) < LOCK_STALE_S


def acquire(s3, vid: str, owner: str, now: Optional[float] = None) -> bool:
    """Take the visit's lock: write it when missing (IfNoneMatch), take
    it over when stale (IfMatch on the etag read), keep it when ours. A
    fresh lock held by another sender is a no. No store: True."""
    s3b, bucket = _pair(s3)
    if s3b is None:
        return True
    now = float(now or time.time())
    doc, etag = _read(s3, lock_key(vid))
    body = {'visit': vid, 'owner': owner, 'at': now}
    try:
        if doc is None:
            s3b.put_object(Bucket=bucket, Key=lock_key(vid), Body=json.dumps(body).encode('utf-8'), ContentType='application/json', IfNoneMatch='*')
            return True
        if str(doc.get('owner') or '') == owner:
            s3b.put_object(Bucket=bucket, Key=lock_key(vid), Body=json.dumps(body).encode('utf-8'), ContentType='application/json')
            return True
        if lock_fresh(doc, now):
            return False
        if etag:
            s3b.put_object(Bucket=bucket, Key=lock_key(vid), Body=json.dumps(body).encode('utf-8'), ContentType='application/json', IfMatch=etag)
        else:
            s3b.put_object(Bucket=bucket, Key=lock_key(vid), Body=json.dumps(body).encode('utf-8'), ContentType='application/json')
        return True
    except Exception:
        return False


def release(s3, vid: str, owner: str) -> None:
    s3b, bucket = _pair(s3)
    if s3b is None:
        return
    doc, _etag = _read(s3, lock_key(vid))
    if doc is not None and str(doc.get('owner') or '') != owner:
        return
    _delete(s3, lock_key(vid))


# -------------------------------------------------------------- the sweep
def _session_of(payload: dict, sid: str) -> Optional[dict]:
    for s in (payload or {}).get('panel_sessions') or []:
        if isinstance(s, dict) and str(s.get('id') or '') == str(sid):
            return s
    return None


def sweep(s3, *, load_twin: Callable, user_for: Callable, save_for: Optional[Callable] = None, portrait_fn: Optional[Callable] = None,
          sender: Optional[Callable] = None, now: Optional[float] = None, owner: Optional[str] = None) -> int:
    """One pass: every visit record is judged; the due ones are sent
    under the visit's lock; the finished ones are dropped. Returns how
    many reads were sent. Never raises."""
    sent_n = 0
    now = float(now or time.time())
    owner = owner or _owner()
    twins: dict = {}
    try:
        recs = visits(s3)
    except Exception:
        traceback.print_exc()
        return 0
    for rec in recs:
        try:
            key, sid, user = str(rec['key']), str(rec['session_id']), str(rec['user'])
            vid = visit_id(key, sid, user)
            store_key = rec.get('_store_key') or visit_key(key, sid, user)
            if key not in twins:
                try:
                    twins[key] = load_twin(key)
                except Exception:
                    traceback.print_exc()
                    twins[key] = None
            payload = twins.get(key)
            sess = _session_of(payload, sid) if payload else None
            if sess is not None:
                try:   # a question whose job died reads as failed, the way a page read would make it (never 'running' forever)
                    from migration import digital_twin_room_jobs as RJ
                    if RJ.sweep_session(s3, key, sess, now=now) and save_for is not None:
                        save_for(key)(payload)
                except Exception:
                    traceback.print_exc()
            sent_doc, _e = _read(s3, sent_key(key, sid, user))
            verdict, why = judge(rec, sess, sent_doc, now=now)
            if verdict == 'drop':
                _delete(s3, store_key)
                continue
            if verdict != 'send':
                continue
            if not acquire(s3, vid, owner, now=now):
                continue
            try:
                fresh, _e2 = _read(s3, store_key)
                if not fresh:
                    continue          # another sender finished it between the list and the lock
                if judge(fresh, sess, sent_doc, now=now)[0] != 'send':
                    continue
                user_rec = None
                try:
                    user_rec = user_for(user)
                except Exception:
                    traceback.print_exc()
                out = send_read(s3, fresh, payload, sess, user_rec=user_rec, portrait_fn=portrait_fn, sender=sender, now=now)
                if out.get('sent'):
                    sent_n += 1
                    _write(s3, sent_key(key, sid, user), {'v': VERSION, 'key': key, 'session_id': sid, 'user': user, 'sent_at': now,
                                                          'sent_at_iso': datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                          'questions_at_send': len(finished_questions(sess, user)), 'subject': str(out.get('subject') or '')[:200],
                                                          'message_id': out.get('message_id'), 'why': why})
                    _delete(s3, store_key)
                    print(f"[twin-session-mail] read sent to {user} for {sid} ({why})")
                elif out.get('handled'):
                    prior = sent_doc or {}
                    _write(s3, sent_key(key, sid, user), {'v': VERSION, 'key': key, 'session_id': sid, 'user': user, 'sent_at': prior.get('sent_at'),
                                                          'questions_at_send': len(finished_questions(sess, user)), 'suppressed': str(out.get('reason') or '')[:160], 'at': now})
                    _delete(s3, store_key)
                    if not _regression():
                        print(f"[twin-session-mail] read for {user} / {sid} not sent: {out.get('reason')}")
                else:
                    fresh['attempts'] = int(fresh.get('attempts') or 0) + 1
                    fresh['last_attempt'] = now
                    fresh['last_error'] = str(out.get('reason') or '')[:160]
                    fresh.pop('_store_key', None)
                    _write(s3, store_key, fresh)
            finally:
                release(s3, vid, owner)
        except Exception:
            traceback.print_exc()
    return sent_n


__all__ = ['enabled', 'quiet_s', 'leave_s', 'note_activity', 'visits', 'judge', 'compose', 'send_read', 'sweep', 'acquire', 'release',
           'own_questions', 'finished_questions', 'room_link', 'visit_key', 'sent_key', 'lock_key', 'visit_id',
           'ACTIVE_PREFIX', 'SENT_PREFIX', 'LOCK_PREFIX', 'SWEEP_S', 'STAMP_MIN_GAP_S', 'LOCK_STALE_S', 'CALLER', 'FAMILY', 'KINDS']
