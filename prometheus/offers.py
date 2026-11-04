"""Offers: a build Prometheus proposes that the user can accept with
one tap, in the chat or from an email.

Jenna, 2026-10-07 (Eliot's "is there a way to build an audience of
people who have attended a Gunna concert?" was never answered with a
profile): "email him and put in chat: Following up on the Gunna
Concert Goer profile? Do you still want me to pull it? Say yes or no
or maybe a chip and button he can click that says yes in the email to
launch it and if launched charge him for the profile obviously."

An offer is one S3 document under ``system/pm_offers/<id>.json``:
the user, the thread, the label, the approved-shape ``spec_draft``
(the same object the dashboard's approve button posts), and a status
(pending, launched, declined, failed). The email carries two signed
links (yes / no); the chat turn carries the same two as chips with
the offer id on its meta. Either path runs the SAME launch: the
approve route as the user, which prices, charges and queues exactly
like the dashboard button. One launch per offer: the first decision
wins, a second click sees "already handled".

A start that does not go through (the queue post failed, the route
raised, no run id came back) is retried once after a short backoff
(2026-11-04, Jenna: "it should never say anything like that ever. it
should only send it to us."). When it still does not start, the user
reads the calm line, the offer goes back to pending (so a later yes,
in the chat or from the email, or an ops retry through
``retry(app, oid)`` starts it), and ops get the offer id, the user,
the subject, the queue response and the cause. The approve route
charges only after the queue accepted the run, so a start that failed
never charged; if a charge ever showed up without a run id, decide
refunds it (host refund_credit) and says so in the ops note. A reply
the route meant for the reader (out of funds, a duplicate build
already running, a question back) is shown as it is and the offer
stays open too.

Signed with the shared approval secret (migration.hostmap_ingest),
so a link cannot be forged or replayed onto another offer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request

OFFERS_PREFIX = 'system/pm_offers/'
BASE_URL = (os.environ.get('PM_OFFER_BASE_URL')
            or os.environ.get('PM_CORRECT_BASE_URL')
            or 'https://dashboard.crosswalknyc.com').rstrip('/')
ACTIONS = ('yes', 'no')
# The one line a reader gets when the pull did not start (app.py
# _CHATBOT_CALM_MESSAGE, verbatim): it stays pending on the server and
# ops are told. The old "I could not start the <label> pull just now ...
# Ask me in the dashboard" is retired (Jenna 2026-11-04).
CALM_LINE = ("Working on it. Confirmed your task is in progress and I will email you when it "
             "completes.")
RETRY_BACKOFF_S = 2.5        # one retry of the start after this pause
OPS_ROUTE = 'prometheus/offers'

bp = Blueprint('prometheus_offers', __name__, url_prefix='/prometheus/offer')

_YES_RX = re.compile(
    r"^\W*(?:yes|yep|yeah|yup|sure|ok(?:ay)?|please|go ahead|do it|pull it|"
    r"run it|build it|launch it|yes,? pull it|yes please|go for it|"
    r"let'?s do it|sounds good|approved?)\b[\s,.!-]*(?:please|pull it|run it|"
    r"do it|go|now|thanks?)?\W*$", re.I)
_NO_RX = re.compile(
    r"^\W*(?:no|nope|nah|not now|no thanks|no thank you|not yet|later|"
    r"don'?t|do not|cancel|skip(?: it)?|hold off|never ?mind)\b.{0,40}$", re.I)


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _secret():
    try:
        from migration.hostmap_ingest import approval_secret
        return approval_secret() or ''
    except Exception:
        return ''


def sign(oid, secret=None):
    sec = secret if secret is not None else _secret()
    if not sec:
        return ''
    return hmac.new(sec.encode('utf-8'), f"offer:{oid}".encode('utf-8'),
                    hashlib.sha256).hexdigest()[:40]


def verify(oid, token, secret=None):
    exp = sign(oid, secret)
    return bool(exp) and hmac.compare_digest(exp, str(token or ''))


def links(oid, token):
    return {a: f"{BASE_URL}/prometheus/offer/{a}?id={oid}&t={token}"
            for a in ACTIONS}


def key_for(oid):
    safe = ''.join(c for c in str(oid) if c.isalnum() or c in '-_')
    return f"{OFFERS_PREFIX}{safe}.json"


def _s3():
    from .host import host
    return host.s3_client, host.bucket


def load(oid):
    try:
        s3, bucket = _s3()
        raw = s3.get_object(Bucket=bucket, Key=key_for(oid))['Body'].read()
        doc = json.loads(raw.decode('utf-8'))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def save(doc):
    s3, bucket = _s3()
    s3.put_object(Bucket=bucket, Key=key_for(doc['id']),
                  Body=json.dumps(doc, indent=2).encode('utf-8'),
                  ContentType='application/json')


def create(username, *, label, prompt, spec_draft, thread_id='',
           run_avid=True, email='', note=''):
    """Write a pending offer and return (doc, links)."""
    oid = f"of_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    doc = {'id': oid, 'username': str(username or '').strip(),
           'email': str(email or ''), 'label': str(label or '').strip(),
           'prompt': str(prompt or ''), 'spec_draft': spec_draft or {},
           'run_avid': bool(run_avid), 'thread_id': str(thread_id or ''),
           'note': str(note or ''), 'status': 'pending',
           'created': _now(), 'decided': None, 'decision': None,
           'run_id': None, 'result': None}
    save(doc)
    return doc, links(oid, sign(oid))


def chat_turn(doc):
    """The agent turn for the user's thread: the follow-up question
    with the two chips, the offer id on meta so the next message is
    read as the answer."""
    label = str(doc.get('label') or 'that profile')
    return {'role': 'agent', 'ts': _now(),
            'text': (f"Following up on the {label} profile. Do you still "
                     f"want me to pull it? Say yes or no."),
            'meta': {'kind': 'question', 'surface': 'analyze',
                     'offer_id': doc['id'],
                     'options': [{'label': 'Yes, pull it', 'send': 'Yes, pull it'},
                                 {'label': 'No', 'send': 'No'}]}}


def pending_in_history(history):
    """The offer id on the latest agent turn when no user turn follows
    it, else ''. The user's next message answers that offer."""
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        role = str(h.get('role') or '').lower()
        if role == 'user':
            return ''
        if role in ('agent', 'assistant'):
            meta = h.get('meta') if isinstance(h.get('meta'), dict) else {}
            return str(meta.get('offer_id') or '')
    return ''


def answer_kind(text):
    """'yes' | 'no' | '' for a message answering an offer."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 60:
        return ''
    if _NO_RX.match(t):
        return 'no'
    if _YES_RX.match(t):
        return 'yes'
    return ''


def _claim(doc, decision):
    """First decision wins. Writes the claim before any launch so a
    second click (or a chip after the email) cannot double-charge."""
    fresh = load(doc['id']) or doc
    if str(fresh.get('status') or 'pending') != 'pending':
        return None
    fresh['status'] = 'accepting' if decision == 'yes' else 'declined'
    fresh['decision'] = decision
    fresh['decided'] = _now()
    save(fresh)
    return fresh


def _user_record(username):
    try:
        from .host import host
        doc = host.s3_json('system/users.json', {}) or {}
        users = doc.get('users') if isinstance(doc.get('users'), dict) else doc
        rec = users.get(username) if isinstance(users, dict) else None
        return rec if isinstance(rec, dict) else {}
    except Exception:
        return {}


def launch(app, doc):
    """Run the approve route as the user: it prices, charges and
    queues exactly like the dashboard button. Returns the approve
    payload (success, run_id, error...)."""
    uname = doc['username']
    rec = _user_record(uname)
    body = {'spec_draft': doc.get('spec_draft') or {},
            'run_avid': bool(doc.get('run_avid', True))}
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['username'] = uname
            if rec.get('role'):
                s['role'] = rec.get('role')
        r = c.post('/api/brief-chat/approve', json=body,
                   headers={'X-Prometheus-Trace': f"offer:{doc['id']}"})
        try:
            data = r.get_json() or {}
        except Exception:
            data = {}
        data['_http'] = r.status_code
        return data


def append_turns(doc, turns):
    """Append turns to the offer's thread (best effort)."""
    tid = str(doc.get('thread_id') or '')
    if not tid:
        return
    try:
        from . import service as _svc
        hist = _svc.load_thread(doc['username'], tid)
        hist = list(hist or []) + list(turns)
        _svc.save_thread(doc['username'], tid, hist)
    except Exception:
        traceback.print_exc()


def launched(result):
    """The approve route queued the run: success with a run id."""
    r = result if isinstance(result, dict) else {}
    return bool(r.get('success')) and bool(r.get('run_id'))


def guidance(result):
    """The approve route answered the reader instead of queuing: out of
    funds, a duplicate build already running, a question back. Shown as
    it is; never a start failure."""
    r = result if isinstance(result, dict) else {}
    return bool(r.get('guidance') or r.get('no_funds'))


def reply_for(decision, doc, result=None):
    """The plain sentence the user reads after deciding. A start that
    did not go through reads as the calm line (the pull stays pending
    and ops are told), never as a failure notice."""
    label = str(doc.get('label') or 'that profile')
    if decision == 'no':
        return (f"Understood. I will not pull the {label} profile. "
                f"Ask me in the dashboard whenever you want it.")
    result = result or {}
    if launched(result):
        charged = result.get('credits_charged')
        money = ''
        try:
            if charged is not None and float(charged) > 0:
                money = f" Your account has been charged for it."
        except Exception:
            money = ''
        return (f"On it. Pulling {label} now. It lands in Select Profile "
                f"in your dashboard when it finishes, and I will confirm "
                f"here and by email.{money}")
    if guidance(result):
        text = str(result.get('error') or result.get('reply') or '').strip()
        if text:
            return text
    return CALM_LINE


def _sleep(seconds):
    time.sleep(max(0.0, float(seconds or 0)))


def _try_launch(app, doc):
    """One start attempt; a raise is a result, never an exception."""
    try:
        out = launch(app, doc)
        return out if isinstance(out, dict) else {'success': False, 'error': '', '_http': None}
    except Exception as e:
        traceback.print_exc()
        print(f"[pm-offer] launch raised for {doc.get('id')}: {e}")
        return {'success': False, 'error': '', '_http': None, '_exc': f"{type(e).__name__}: {e}"[:300]}


def _result_summary(result):
    r = result if isinstance(result, dict) else {}
    return {k: r.get(k) for k in ('success', 'run_id', 'credits_charged', 'error', 'reply', 'guidance',
                                  'no_funds', '_http', '_exc') if r.get(k) not in (None, '', False)}


def _ops_email(err, *, username='', payload=None):
    """The ops alert for an offer that did not start (jenna@ + jessie@
    through the chatbot error path). Never raises."""
    try:
        from prometheus.legacy import H as _h
        _h._chatbot_error_email(OPS_ROUTE, err, user_email=username or None, payload=payload)
        return True
    except Exception:
        try:
            from .host import host
            host.error_email(OPS_ROUTE, err, tb='(offer start)')
            return True
        except Exception:
            return False


def _refund(username, credits, reason):
    """Reverse a charge that has no run behind it (host refund_credit,
    the same reversal the build flow uses when a downstream step fails
    after the charge). Returns True when it posted."""
    try:
        from prometheus.legacy import H as _h
        fn = _h.get('refund_credit')
        return bool(fn(username, credits=int(credits), reason=reason)) if callable(fn) else False
    except Exception:
        traceback.print_exc()
        return False


def _charged_without_run(result):
    """A charge on a result with no run id: a half state the approve
    route never produces (it charges after the queue accepted), guarded
    here anyway."""
    r = result if isinstance(result, dict) else {}
    if launched(r):
        return 0
    try:
        return int(float(r.get('credits_charged') or 0))
    except (TypeError, ValueError):
        return 0


def decide(app, oid, action, *, via='email', user_text='', append=True):
    """Apply a decision. Returns (state, reply_text, doc):
    state in already | invalid | launched | declined | guidance | held.

    yes: the approve route runs as the user (prices, charges after the
    queue accepts, queues). A start that did not go through is retried
    once after RETRY_BACKOFF_S. Still not started: the offer goes back
    to pending (a later yes or `retry` starts it), the user reads the
    calm line, ops get the cause ('held'). A reply the route meant for
    the reader (out of funds, a duplicate build, a question back) is
    shown as it is and the offer stays open ('guidance'). Never a
    failure notice to the reader, never a charge without a run.
    ``append`` writes the turns onto the thread (the email path; the
    dashboard widget persists its own turns)."""
    doc = load(oid)
    if not doc:
        return 'invalid', 'This link is not valid.', None
    if action not in ACTIONS:
        return 'invalid', 'This link is not valid.', doc
    claimed = _claim(doc, action)
    if claimed is None:
        doc = load(oid) or doc
        return 'already', _already_text(doc), doc
    doc = claimed
    turns = []
    if user_text:
        turns.append({'role': 'user', 'text': str(user_text), 'ts': _now()})
    if action == 'no':
        reply = reply_for('no', doc)
        turns.append({'role': 'agent', 'text': reply, 'ts': _now(),
                      'meta': {'kind': 'answer', 'offer_id': oid, 'via': via}})
        if append:
            append_turns(doc, turns)
        return 'declined', reply, doc
    attempts = [_try_launch(app, doc)]
    if not launched(attempts[-1]) and not guidance(attempts[-1]):
        # a transient queue hiccup self-heals: one more start, same offer
        _sleep(RETRY_BACKOFF_S)
        attempts.append(_try_launch(app, doc))
    result = attempts[-1]
    ok = launched(result)
    refunded = 0
    for a in attempts:
        n = _charged_without_run(a)
        if n and _refund(doc.get('username'), n, f"offer {oid}: charged with no run id"):
            refunded += n
    if ok:
        state = 'launched'
        doc['status'] = 'launched'
        doc['run_id'] = result.get('run_id')
    else:
        # the pull stays pending on the server: a later yes (chat or
        # email) or an ops retry starts it; nothing is closed as failed
        state = 'guidance' if guidance(result) else 'held'
        doc['status'] = 'pending'
        doc['decision'] = None
        doc['decided'] = None
        doc['run_id'] = None
        doc['held'] = (state == 'held')
    doc['result'] = _result_summary(result)
    doc['last_attempt'] = {'ts': _now(), 'via': via, 'state': state,
                           'attempts': [_result_summary(a) for a in attempts],
                           'refunded_credits': refunded}
    doc['via'] = via
    save(doc)
    reply = reply_for('yes', doc, result)
    if not user_text:
        turns.append({'role': 'user', 'text': 'Yes, pull it', 'ts': _now(),
                      'meta': {'via': via, 'offer_id': oid}})
    turns.append({'role': 'agent', 'text': reply, 'ts': _now(),
                  'meta': {'kind': 'answer', 'offer_id': oid, 'via': via,
                           'run_id': doc['run_id'], 'held': state == 'held'}})
    if append:
        append_turns(doc, turns)
    if state == 'held':
        cause = (result.get('_exc') or
                 ('the route answered without a run id' if result.get('success') else
                  (str(result.get('error') or '').strip() or f"http {result.get('_http')}")))
        _ops_email(
            f"offer {oid} for {doc.get('username')} ({doc.get('label')}) did not start after a retry; "
            f"held pending: the offer stays open, a later yes or retry(app, '{oid}') starts it, the user read "
            f"the calm line | cause: {str(cause)[:200]} | queue: {json.dumps(_result_summary(result))[:400]}"
            + (f" | refunded {refunded} credit(s) charged with no run" if refunded else " | nothing charged"),
            username=str(doc.get('username') or ''),
            payload={'offer_id': oid, 'user': doc.get('username'), 'subject': doc.get('label'), 'via': via,
                     'attempts': [_result_summary(a) for a in attempts], 'refunded_credits': refunded})
    elif refunded:
        _ops_email(f"offer {oid} for {doc.get('username')} ({doc.get('label')}): {refunded} credit(s) were charged on "
                   f"an attempt that returned no run id and were refunded; final state {state}",
                   username=str(doc.get('username') or ''),
                   payload={'offer_id': oid, 'user': doc.get('username'), 'subject': doc.get('label'), 'state': state})
    return state, reply, doc


def retry(app, oid):
    """Ops: start a held offer again (the same decide path, via 'ops')."""
    return decide(app, oid, 'yes', via='ops', append=True)


def _already_text(doc):
    st = str((doc or {}).get('status') or '')
    label = str((doc or {}).get('label') or 'that profile')
    if st == 'launched':
        return (f"Already on it. {label} is being pulled and lands in "
                f"Select Profile when it finishes.")
    if st == 'declined':
        return f"Already noted. I am not pulling the {label} profile."
    if st == 'accepting':
        return f"Already on it. {label} is starting now."
    return "This has already been handled."


def page(title, body_text, ok=True):
    ink, body, muted, olive, off = (
        '#0C1618', '#5C6560', '#888C89', '#5E7E12', '#E9E8E1')
    safe = (str(body_text or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title></head>"
        f"<body style='margin:0;padding:0;background:{off};font-family:"
        f"Helvetica,Arial,sans-serif;color:{body}'>"
        f"<div style='max-width:620px;margin:0 auto;padding:48px 24px'>"
        f"<div style='font-size:11px;letter-spacing:.12em;text-transform:"
        f"uppercase;color:{olive};font-weight:600'>Prometheus</div>"
        f"<h1 style='font-size:24px;color:{ink};font-weight:700;"
        f"margin:10px 0 14px'>{title}</h1>"
        f"<div style='background:#fff;border-radius:12px;padding:16px 18px;"
        f"color:{ink};font-size:16px;line-height:1.55'>{safe}</div>"
        f"<div style='margin-top:26px;color:{muted};font-size:13px'>"
        f"You can close this tab. The same answer is in your Prometheus "
        f"chat.</div>"
        f"<div style='margin-top:28px;color:{ink}'>Prometheus<br>"
        f"<span style='color:{muted}'>Crosswalk</span></div>"
        f"</div></body></html>")


@bp.route('/<action>', methods=['GET'])
def offer_action(action):
    from flask import current_app
    oid = str(request.args.get('id') or '').strip()
    tok = str(request.args.get('t') or '').strip()
    if not oid or not verify(oid, tok):
        return page('This link is not valid.',
                    'Open your Prometheus chat in the dashboard and answer '
                    'there.', ok=False), 403
    try:
        state, reply, _doc = decide(current_app._get_current_object(), oid,
                                    action, via='email')
    except Exception as e:
        # the decision itself raised: the reader gets the calm line (the
        # offer is still on the server), ops get the cause
        traceback.print_exc()
        _ops_email(f"offer {oid}: the {action} decision from the email link raised {type(e).__name__}: {str(e)[:200]}; "
                   f"the user read the calm line, the offer is still on the server",
                   payload={'offer_id': oid, 'action': action, 'via': 'email'})
        return page('Working on it.', CALM_LINE, ok=True), 200
    titles = {'launched': 'Pulling it now.', 'declined': 'Understood.',
              'already': 'Already handled.', 'held': 'Working on it.',
              'guidance': 'Noted.', 'invalid': 'This link is not valid.'}
    code = 200 if state in ('launched', 'declined', 'already', 'held', 'guidance') else 409
    return page(titles.get(state, 'Done.'), reply, ok=state != 'invalid'), code


def _default_note(doc):
    label = str(doc.get('label') or 'that profile')
    return (f"I can pull the {label} profile with the Total Universe and "
            f"the Avid tier, landing in Select Profile like any other "
            f"profile.")


def _note_html(doc):
    note = str(doc.get('note') or _default_note(doc))
    return (note.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('\n', '<br>'))


def email_html(doc, lnk, who_first=''):
    """The follow-up email body (HTML), From Prometheus."""
    ink, body, muted, olive, off = (
        '#0C1618', '#5C6560', '#888C89', '#5E7E12', '#E9E8E1')
    label = str(doc.get('label') or 'that profile')
    greet = f"Hi {who_first}," if who_first else "Hi,"
    btn = (lambda href, text, bg, fg:
           f"<a href='{href}' style='display:inline-block;background:{bg};"
           f"color:{fg};text-decoration:none;border-radius:10px;padding:12px 22px;"
           f"font-size:15px;font-weight:600;margin-right:12px'>{text}</a>")
    return (
        f"<html><body style='margin:0;padding:0;background:{off};"
        f"font-family:Helvetica,Arial,sans-serif;color:{body}'>"
        f"<div style='max-width:640px;margin:0 auto;padding:32px 24px'>"
        f"<div style='font-size:11px;letter-spacing:.12em;text-transform:"
        f"uppercase;color:{olive};font-weight:600'>Prometheus</div>"
        f"<h1 style='font-size:22px;color:{ink};font-weight:700;"
        f"margin:10px 0 14px'>Following up on the {label} profile.</h1>"
        f"<div style='background:#fff;border-radius:12px;padding:16px 18px;"
        f"color:{ink};font-size:16px;line-height:1.55'>"
        f"{greet}<br><br>{_note_html(doc)}<br><br>"
        f"Do you still want me to pull it? Say yes or no.</div>"
        f"<div style='margin:22px 0 0'>"
        f"{btn(lnk['yes'], 'Yes, pull it', ink, off)}"
        f"{btn(lnk['no'], 'No', '#FFFFFF', ink)}</div>"
        f"<div style='color:{muted};font-size:13px;margin-top:12px'>"
        f"Yes starts the pull right away and your account is charged for "
        f"the profile, the same as approving it in the dashboard. You can "
        f"also answer in your Prometheus chat.</div>"
        f"<div style='margin-top:28px;color:{ink}'>Prometheus<br>"
        f"<span style='color:{muted}'>Crosswalk</span></div>"
        f"</div></body></html>")


def email_text(doc, lnk, who_first=''):
    label = str(doc.get('label') or 'that profile')
    greet = f"Hi {who_first}," if who_first else "Hi,"
    return (f"{greet}\n\nFollowing up on the {label} profile.\n\n"
            f"{str(doc.get('note') or _default_note(doc))}\n\n"
            f"Do you still want me to pull it? Say yes or no.\n\n"
            f"Yes, pull it: {lnk['yes']}\nNo: {lnk['no']}\n\n"
            f"Yes starts the pull right away and your account is charged "
            f"for the profile, the same as approving it in the dashboard. "
            f"You can also answer in your Prometheus chat.\n\n"
            f"Prometheus\nCrosswalk\n")
