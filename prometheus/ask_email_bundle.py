"""Hold Prometheus ask-copy emails until a multi-step walk ends.

Jenna 2026-10-28: a wizard or other guided intake must not mail one
note per tap. Turns append to a bundle on the same pending-intake
document the draft already uses. One email goes out when the walk
ends (Accept / run, Decline, Cancel, a new chip on the thread, the
next unrelated message, or two hours idle).

A single-turn ask still mails once, as before. Probes never mail.
"""
from __future__ import annotations

import copy
import re
import time
import traceback

IDLE_S = 2 * 3600

_COLLECT = (
    ('bpiq_wizard', 'brand_partnership'),
    ('bpiq_collect', 'brand_partnership'),
    ('bpiq_confirm_payload', 'brand_partnership'),
    ('jiq_collect', 'digital_journey'),
    ('jiq_confirm_payload', 'digital_journey'),
    ('fw_collect', 'flywheel'),
    ('fw_confirm_payload', 'flywheel'),
    ('aiq_collect', 'attribution'),
    ('aiq_confirm_payload', 'attribution'),
    ('twin_collect', 'digital_twin'),
    ('twin_confirm_payload', 'digital_twin'),
    ('spec_draft', 'profile_iq'),
)

_START_RX = re.compile(
    r"^\s*(?:value a brand partnership|pull brand partnership valuation|"
    r"brand partnership valuation|bpiq|"
    r"(?:create|build|run)(?: a| the)? digital journey|"
    r"(?:build|run)(?: a| the)? twin|"
    r"(?:run|start)(?: a| the)? (?:flywheel|attribution|tracking))\s*[.!]?\s*$",
    re.I,
)

_RUNNING_PREFIX = "It's running"
_FUNDS_PREFIX = 'That valuation needs funds'
_DECLINE = "Okay, I won't run it."
_CANCEL = 'No problem. Ask me anything else when ready.'


class MemoryDocs:
    """In-process pending-intake docs for tests."""

    def __init__(self):
        self.docs = {}

    def load_doc(self, user):
        return copy.deepcopy(self.docs.get(str(user or ''), {}) or {})

    def save_doc(self, user, doc):
        self.docs[str(user or '')] = copy.deepcopy(doc or {})
        return True

    def list_users(self):
        return list(self.docs.keys())


class S3Docs:
    def __init__(self, s3=None, bucket=None):
        self.s3 = s3
        self.bucket = bucket or 'dashboard-inputs'

    def load_doc(self, user):
        from prometheus import pending_intake as pi
        return pi.load_doc(self.s3, self.bucket, user)

    def save_doc(self, user, doc):
        from prometheus import pending_intake as pi
        return pi.save_doc(self.s3, self.bucket, user, doc)

    def list_users(self):
        if self.s3 is None:
            return []
        out = []
        prefix = 'system/pm_pending_intake/'
        try:
            paginator = self.s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get('Contents') or []:
                    key = str(obj.get('Key') or '')
                    if not key.endswith('.json') or key.count('/') != 2:
                        continue
                    name = key[len(prefix):-5]
                    if name:
                        out.append(name)
        except Exception:
            traceback.print_exc()
        return out


def live_store():
    """The pending-intake document on the host, or None when unbound."""
    try:
        from prometheus.host import host as _host
    except Exception:
        return None
    s3 = None
    bucket = 'dashboard-inputs'
    try:
        has = getattr(_host, 'has', None)
        if callable(has) and has('s3_client'):
            s3 = getattr(_host, 's3_client', None)
    except Exception:
        s3 = None
    if s3 is None:
        try:
            s3 = getattr(_host, 's3_client', None)
        except Exception:
            s3 = None
    try:
        bucket = getattr(_host, 'bucket', None) or bucket
    except Exception:
        try:
            bucket = getattr(_host, 'S3_BUCKET', None) or bucket
        except Exception:
            pass
    if s3 is None:
        return None
    return S3Docs(s3, bucket)


def flow_from_payload(payload):
    if not isinstance(payload, dict):
        return ''
    for key, flow in _COLLECT:
        val = payload.get(key)
        if key == 'bpiq_wizard':
            if isinstance(val, dict) and val.get('step'):
                return flow
            continue
        if key == 'spec_draft':
            if isinstance(val, dict) and val:
                return flow
            continue
        if val:
            return flow
    return ''


def walk_open(payload):
    """True when this reply is still collecting a walk."""
    if not isinstance(payload, dict):
        return False
    try:
        from prometheus_watch_notify import answer_text
        reply = answer_text(payload)
    except Exception:
        reply = str(payload.get('reply') or '')
    if isinstance(reply, str) and reply.startswith(_FUNDS_PREFIX):
        return True
    if is_terminal(payload, ''):
        return False
    return bool(flow_from_payload(payload))


def is_terminal(payload, question=''):
    try:
        from prometheus_watch_notify import answer_text
        reply = answer_text(payload)
    except Exception:
        reply = str((payload or {}).get('reply') or '')
    if not isinstance(reply, str):
        return False
    if reply.startswith(_RUNNING_PREFIX):
        return True
    if reply.strip() in (_DECLINE, _CANCEL):
        return True
    if (payload or {}).get('bpiq_job_id') and not (
            isinstance((payload or {}).get('bpiq_wizard'), dict)
            and (payload or {}).get('bpiq_wizard', {}).get('step')):
        return True
    return False


def is_walk_start(question, payload=None):
    q = str(question or '').strip()
    if not q:
        return False
    try:
        from prometheus import bpiq_wizard as W
        if W.is_start(q):
            return True
    except Exception:
        pass
    if _START_RX.match(q):
        return True
    if payload and flow_from_payload(payload) and not (
            isinstance(payload, dict) and isinstance(payload.get('bpiq_wizard'), dict)
            and payload['bpiq_wizard'].get('step') not in (None, '', 'talent')):
        # first wizard step is talent after the chip; the chip itself is the start
        pass
    return False


def outcome_line(question, payload):
    try:
        from prometheus_watch_notify import answer_text
        reply = answer_text(payload)
    except Exception:
        reply = str((payload or {}).get('reply') or '')
    q = str(question or '').strip()
    if isinstance(reply, str) and reply.startswith(_RUNNING_PREFIX):
        m = re.search(r'\$[\d,]+', q)
        if m:
            return f"Accepted {m.group(0)}, running"
        return 'Accepted, running'
    if (isinstance(reply, str) and reply.strip() == _DECLINE) or q.lower() == 'decline':
        return 'Declined'
    if (isinstance(reply, str) and reply.strip() == _CANCEL) or q.lower() == 'cancel':
        return 'Cancelled'
    try:
        from prometheus.pending_intake import is_run_order
        if is_run_order(q):
            return 'Accepted, running'
    except Exception:
        pass
    low = q.lower().rstrip('.!')
    if low in ('run the journey', 'run the valuation', 'run the flywheel',
               'start tracking', 'build the twin', 'go ahead', 'run it'):
        return 'Accepted, running'
    return 'Walk ended'


def _key(thread_id, draft_id):
    tid = ''.join(c for c in str(thread_id or 'none') if c.isalnum() or c in '-_') or 'none'
    did = ''.join(c for c in str(draft_id or 'walk') if c.isalnum() or c in '-_') or 'walk'
    return f"{tid}|{did}"


def _on_thread(bundles, thread_id):
    tid = str(thread_id or 'none')
    prefix = _key(tid, 'x').rsplit('|', 1)[0] + '|'
    for k, b in (bundles or {}).items():
        if str(k).startswith(prefix) and isinstance(b, dict):
            return k, b
        if isinstance(b, dict) and str(b.get('thread_id') or '') == tid:
            return k, b
    return '', None


def _empty(username, record, thread_id, flow, subject, via, surface, now):
    return {
        'username': str(username or ''),
        'record': {
            'email': str((record or {}).get('email') or ''),
            'company': str((record or {}).get('company') or ''),
            'first_name': str((record or {}).get('first_name') or ''),
            'last_name': str((record or {}).get('last_name') or ''),
        },
        'thread_id': str(thread_id or 'none'),
        'flow': str(flow or 'walk'),
        'subject': str(subject or ''),
        'via': via,
        'surface': surface,
        'turns': [],
        'ts': now,
        'started_ts': now,
    }


def _append(bundle, question, payload, now):
    try:
        from prometheus_watch_notify import answer_text
        ans = answer_text(payload)
    except Exception:
        ans = str((payload or {}).get('reply') or '')
    b = dict(bundle or {})
    turns = list(b.get('turns') or [])
    turns.append({'q': str(question or ''), 'a': str(ans or '')})
    b['turns'] = turns
    b['ts'] = now
    if isinstance(ans, str) and ans.startswith("Here's what I'll run"):
        b['recap_q'] = str(question or '')
        b['recap_a'] = ans
    return b


def record_turn(store, username, record, question, payload, subject=None,
                thread_id='', via=None, surface=None, now=None):
    """Append this turn to the thread's bundle, or say it is a single
    ask. Returns (action, bundle, outcome) where action is hold, flush,
    abandoned, or single. flush / abandoned carry the bundle to send."""
    now = time.time() if now is None else now
    if store is None:
        return 'single', None, ''
    tid = str(thread_id or 'none')
    flow = flow_from_payload(payload)
    open_ = walk_open(payload)
    terminal = is_terminal(payload, question)
    start = is_walk_start(question, payload)
    doc = store.load_doc(username) or {}
    bundles = dict(doc.get('ask_bundles') or {}) if isinstance(doc.get('ask_bundles'), dict) else {}
    existing_key, existing = _on_thread(bundles, tid)

    if existing and start and (existing.get('turns') or []) and open_ and not terminal:
        old = existing
        bundles.pop(existing_key, None)
        draft_id = flow or 'walk'
        key = _key(tid, draft_id)
        bundles[key] = _append(
            _empty(username, record, tid, draft_id, subject, via, surface, now),
            question, payload, now)
        doc['ask_bundles'] = bundles
        store.save_doc(username, doc)
        return 'supersede', old, 'Cancelled'

    draft_id = flow or (existing or {}).get('flow') or 'walk'
    key = _key(tid, draft_id)

    if open_ and not terminal:
        base = bundles.get(key) or existing or _empty(
            username, record, tid, draft_id, subject, via, surface, now)
        if existing_key and existing_key != key:
            bundles.pop(existing_key, None)
        bundles[key] = _append(base, question, payload, now)
        doc['ask_bundles'] = bundles
        store.save_doc(username, doc)
        return 'hold', None, ''

    try:
        from prometheus.pending_intake import is_run_order
        run_yes = is_run_order(question)
    except Exception:
        run_yes = False
    if terminal or (existing and not open_ and (flow or run_yes)):
        base = bundles.get(key) or existing or _empty(
            username, record, tid, draft_id, subject, via, surface, now)
        bundled = _append(base, question, payload, now)
        bundles.pop(key, None)
        if existing_key:
            bundles.pop(existing_key, None)
        doc['ask_bundles'] = bundles
        store.save_doc(username, doc)
        return 'flush', bundled, outcome_line(question, payload)

    if existing and not open_ and not terminal:
        bundles.pop(existing_key, None)
        doc['ask_bundles'] = bundles
        store.save_doc(username, doc)
        return 'abandoned', existing, 'Walk ended'

    return 'single', None, ''


def flush_idle(store=None, now=None, idle_s=IDLE_S, send_fn=None):
    """Send one email per bundle that has sat idle. Returns how many."""
    now = time.time() if now is None else now
    store = store if store is not None else live_store()
    if store is None:
        return 0
    n = 0
    try:
        users = store.list_users()
    except Exception:
        users = []
    for user in users:
        try:
            doc = store.load_doc(user) or {}
            bundles = dict(doc.get('ask_bundles') or {}) if isinstance(doc.get('ask_bundles'), dict) else {}
            if not bundles:
                continue
            keep = {}
            for key, bundle in bundles.items():
                if not isinstance(bundle, dict):
                    continue
                ts = float(bundle.get('ts') or 0)
                if now - ts < idle_s:
                    keep[key] = bundle
                    continue
                rec = bundle.get('record') or {}
                if send_fn is not None:
                    send_fn('walk', username=bundle.get('username') or user,
                            record=rec, bundle=bundle, outcome='Walk ended')
                else:
                    _deliver_walk(bundle.get('username') or user, rec, bundle,
                                  'Walk ended',
                                  via=bundle.get('via'),
                                  surface=bundle.get('surface'))
                n += 1
            if keep != bundles:
                doc['ask_bundles'] = keep
                store.save_doc(user, doc)
        except Exception:
            traceback.print_exc()
    return n


def _deliver_walk(username, record, bundle, outcome, via=None, surface=None,
                  send_fn=None):
    import prometheus_watch_notify as pwn
    turns = list((bundle or {}).get('turns') or [])
    recap_q = str((bundle or {}).get('recap_q') or '')
    recap_a = str((bundle or {}).get('recap_a') or '')
    if not recap_q or not recap_a:
        for t in reversed(turns):
            a = str((t or {}).get('a') or '')
            if a and not a.startswith(_RUNNING_PREFIX) and a not in (_DECLINE, _CANCEL):
                recap_q = str((t or {}).get('q') or recap_q)
                recap_a = a
                break
    if send_fn is not None:
        send_fn('walk', username=username, record=record, turns=turns,
                outcome=outcome, question=recap_q, answer=recap_a,
                subject=(bundle or {}).get('subject'), via=via, surface=surface)
        return
    pwn.send_walk(username, record, turns, outcome,
                  subject=(bundle or {}).get('subject'),
                  recap_q=recap_q, recap_a=recap_a,
                  via=via or (bundle or {}).get('via'),
                  surface=surface or (bundle or {}).get('surface'))


def handle_notify(username, record, question, payload, subject=None,
                  via=None, surface=None, thread_id='', store=None,
                  send_fn=None, now=None):
    """Decide hold / flush / single for one ask-copy. Returns the
    action. send_fn, when set, receives every email a real send would."""
    store = store if store is not None else live_store() or MemoryDocs()
    action, bundle, outcome = record_turn(
        store, username, record, question, payload, subject=subject,
        thread_id=thread_id, via=via, surface=surface, now=now)
    if action == 'hold':
        return action
    if action in ('flush', 'abandoned', 'supersede'):
        if bundle and (bundle.get('turns') or []):
            _deliver_walk(username, record, bundle, outcome,
                          via=via, surface=surface, send_fn=send_fn)
        if action == 'abandoned':
            if send_fn is not None:
                send_fn('single', username=username, record=record,
                        question=question, payload=payload, subject=subject,
                        via=via, surface=surface)
            else:
                import prometheus_watch_notify as pwn
                pwn.send_single(username, record, question, payload, subject,
                                via=via, surface=surface)
        return action
    if send_fn is not None:
        send_fn('single', username=username, record=record,
                question=question, payload=payload, subject=subject,
                via=via, surface=surface)
    else:
        import prometheus_watch_notify as pwn
        pwn.send_single(username, record, question, payload, subject,
                        via=via, surface=surface)
    return 'single'
