"""Cancel means gone; a new thread starts clean (Jenna 2026-11-05).

Jenna, verbatim: "if you cancel a wizard or open a new thread it should
drop the wizard not just keep it open."

The day before, the Brand Partnership Valuation wizard swallowed every
later message on the thread it was started in and on a second thread
she opened, and the Cancel chip left it armed. Two things kept it alive:

  * the wizard's draft lives in more than one place (the per-thread S3
    key, the user-level pending-intake stash, the widget's page state,
    the widget's sessionStorage) and Cancel cleared one of them;
  * nothing dropped the user's armed intakes when a new thread opened.

This module is the one place that knows every armed-intake store and
clears all of them:

  * the bpiq wizard draft (``system/pm_pending_intake/<user>/bpiq_wizard_<thread>.json``
    and the ``brand_partnership_wizard`` / ``brand_partnership`` flows on
    the user's pending-intake document; ``bpiq_wizard.shared_store().clear``
    when the store exposes it, the keys directly otherwise);
  * the flywheel wizard (``flywheel_wizard`` / ``flywheel`` flows);
  * every other guided intake stashed on the pending-intake document:
    the journey brief (``digital_journey``), the Attribution IQ campaign
    setup and add-assets brief (``attribution``), the Digital Twins
    intakes (``digital_twin`` from the twin lane, ``digital_twin_list``
    from twin_url_list; twin_named_pull rides the twin lane's flow), the
    bulk pull (``bulk_profile_pull``), a stashed profile brief;
  * on an explicit cancel, every open priced offer recorded on that
    thread (``pending_offers``), so a stale Approve chip cannot re-arm
    what the reader just dropped. A new thread leaves offers alone: an
    offer stays open on the thread it was made on and is approved only
    there (2026-11-11; until then a typed approval in a new thread bound
    to the offer made in the old one).

The referent clarify and the memory confirm hold no server state: both
read the last agent turn of the thread, so the cancel reply (or the
empty history of a new thread) ends them on its own. The bpiq run
MARKER (``bpiq_run_<thread>.json``, the job id an Accept already
started) is a record of a charged run, never an intake; it stays.

Hooks (small, at the seams):

  * ``prometheus.legacy.threads.api_synth_chat_threads_new`` and
    ``service.thread_for_subject`` (a created project thread) call
    ``on_new_thread``; ``service.ask`` calls ``on_fresh_thread`` on the
    first message of a thread that holds no turns yet (the client minted
    the id, or the idle rotation opened it);
  * ``service.ask`` runs the ``intake_cancel`` lane first: a cancel word
    while anything is armed drops everything for that user and thread
    and answers with one plain line and no chips.

A probe identity (the golden set, the canary, a replay) holds no shared
state and is never written for: ``drop_all`` is a no-op there. Nothing
here raises into a request; every S3 touch is fail-safe.
"""
from __future__ import annotations

import json
import re
import traceback

from . import faults as _faults

PENDING_PREFIX = 'system/pm_pending_intake/'

# flow name on the pending-intake document -> the words the reader hears
LABELS = {
    'brand_partnership_wizard': 'the valuation',
    'brand_partnership': 'the valuation',
    'flywheel_wizard': 'the flywheel',
    'flywheel': 'the flywheel',
    'digital_journey': 'the journey',
    'attribution': 'the campaign setup',
    'digital_twin': 'the twin',
    'digital_twin_list': 'the twin',
    'bulk_profile_pull': 'the bulk pull',
    'profile_iq': 'the profile brief',
}

# body keys the widget sends while one of its intake steps is armed
# (intake_cancel: the widget's own Cancel branch names the intake it had
# armed, a flow name, or True for the valuation wizard; read in _body_flows)
_BODY_ARMED = (
    ('bpiq_wizard', 'brand_partnership_wizard'),
    ('bpiq_inputs', 'brand_partnership'),
    ('bpiq_confirm', 'brand_partnership'),
    ('jiq_inputs', 'digital_journey'),
    ('jiq_confirm', 'digital_journey'),
    ('fw_inputs', 'flywheel'),
    ('fw_confirm', 'flywheel'),
    ('aiq_inputs', 'attribution'),
    ('aiq_confirm', 'attribution'),
    ('twin_inputs', 'digital_twin'),
    ('twin_confirm', 'digital_twin'),
    ('twin_list_confirm', 'digital_twin_list'),
    ('bulk_confirm', 'bulk_profile_pull'),
)

_CANCEL_RX = re.compile(
    r"^\s*(?:"
    r"cancel(?:\s+(?:that|it|this|everything|the\s+(?:wizard|valuation|journey|flywheel|twin|setup|intake|brief|flow)))?"
    r"|stop(?:\s+(?:that|it|this|the\s+(?:wizard|valuation|journey|flywheel|twin|setup|intake|flow)))?"
    r"|drop\s+(?:that|it|this|everything|the\s+(?:wizard|valuation|journey|flywheel|twin|setup|intake|brief|flow))"
    r"|never\s?mind|forget\s+(?:it|that|this)|nvm|not\s+now|abort|quit(?:\s+the\s+wizard)?|exit\s+the\s+wizard"
    r"|start\s+over"
    r")\s*[.!?]*\s*$",
    re.I)


def is_cancel_text(text):
    """A bare cancel: the chip's label typed, or the words that mean it.
    A sentence that carries a question or a new ask is never one."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 48 or '?' in t:
        return False
    return bool(_CANCEL_RX.match(t))


# ------------------------------------------------------------------ host
def _host():
    try:
        from .host import host as _h
        return _h
    except Exception:
        return None


def _s3():
    """(s3 client, bucket) from the host, or (None, None)."""
    h = _host()
    if h is None:
        return None, None
    try:
        s3 = h.s3_client if h.has('s3_client') else None
        bucket = (h.bucket if h.has('bucket') else None) or 'dashboard-inputs'
        return (s3, bucket) if s3 is not None else (None, None)
    except Exception:
        return None, None


def _uname(user):
    if isinstance(user, dict):
        return str(user.get('username') or user.get('email') or '').strip()
    return str(user or '').strip()


def _user_key(uname):
    try:
        import prometheus_memory as pmm
        return pmm._user_key(uname)
    except Exception:
        return ''.join(c for c in str(uname or 'anon') if c.isalnum() or c in '-_.@').lower() or 'anon'


def _safe_tid(thread_id):
    return ''.join(c for c in str(thread_id or 'none') if c.isalnum() or c in '-_') or 'none'


def _is_probe(uname):
    try:
        if _faults.is_probe():
            return True
    except Exception:
        pass
    try:
        from . import probe_identity as _pi
        return bool(_pi.is_probe_user(uname))
    except Exception:
        return False


# --------------------------------------------------------------- lookups
def _bpiq_thread_keys(s3, bucket, uk, thread_id):
    """The per-thread bpiq draft keys to clear: the named thread's (and
    the thread-less one) when a thread is given, every one under the
    user otherwise. Run markers (``bpiq_run_*``) are never listed."""
    base = f"{PENDING_PREFIX}{uk}/bpiq_wizard_"
    if thread_id:
        keys = [base + _safe_tid(thread_id) + '.json']
        if _safe_tid(thread_id) != 'none':
            keys.append(base + 'none.json')
        return keys
    keys = []
    try:
        token = None
        while True:
            kw = {'Bucket': bucket, 'Prefix': base, 'MaxKeys': 200}
            if token:
                kw['ContinuationToken'] = token
            page = s3.list_objects_v2(**kw)
            for o in (page.get('Contents') or []):
                k = str(o.get('Key') or '')
                if k.startswith(base) and k.endswith('.json'):
                    keys.append(k)
            token = page.get('NextContinuationToken') if page.get('IsTruncated') else None
            if not token:
                break
    except Exception:
        pass
    return keys


def _key_exists(s3, bucket, key):
    try:
        s3.get_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def _pending_flows(s3, bucket, uname, thread_id=None):
    """The stashed briefs: this thread's when a thread is named (a brief
    waits on the thread it was proposed on), every thread's otherwise
    (the reset that drops a reader's intakes clears them all)."""
    try:
        from . import pending_intake as _pi
        return [str(f) for f in (_pi.active_flows(s3, bucket, uname, any_thread=not thread_id) or [])]
    except Exception:
        return []


def _body_flows(body):
    out = []
    if not isinstance(body, dict):
        return out
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    ic = body.get('intake_cancel')
    if ic is None:
        ic = extra.get('intake_cancel')
    if ic:
        out.append(str(ic) if str(ic) in LABELS else 'brand_partnership_wizard')
    for key, flow in _BODY_ARMED:
        v = body.get(key)
        if v is None:
            v = extra.get(key)
        if v:
            out.append(flow)
    return out


def _history_flows(history):
    """The intake the last agent turn is still asking for, read from the
    thread itself (the dashboard writes its own turns; a reload may have
    emptied the stores while the step prompt still shows)."""
    out = []
    try:
        from . import bpiq_wizard as _bw
        if _bw.last_agent_is_wizard(history):
            out.append('brand_partnership_wizard')
    except Exception:
        pass
    try:
        for turn in reversed(list(history or [])):
            if not isinstance(turn, dict) or str(turn.get('role') or '') not in ('agent', 'assistant'):
                continue
            meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
            if isinstance(meta.get('bpiq_wizard'), dict) and meta['bpiq_wizard'].get('step'):
                out.append('brand_partnership_wizard')
            for k, flow in (('jiq_confirm_payload', 'digital_journey'), ('fw_confirm_payload', 'flywheel'),
                            ('aiq_confirm_payload', 'attribution'), ('twin_confirm_payload', 'digital_twin')):
                if meta.get(k):
                    out.append(flow)
            break
    except Exception:
        pass
    return out


def _stored_flows(uname, thread_id=None):
    """Flow names armed in the shared stores (the pending-intake document
    and the bpiq per-thread keys). Read-only."""
    found = []
    s3, bucket = _s3()
    if s3 is None or not uname:
        return found
    found.extend(_pending_flows(s3, bucket, uname, thread_id))
    uk = _user_key(uname)
    keys = _bpiq_thread_keys(s3, bucket, uk, thread_id)
    # a listing (no thread named) only returns keys that exist; a named
    # thread's keys are looked up
    if any((not thread_id) or _key_exists(s3, bucket, k) for k in keys):
        found.append('brand_partnership_wizard')
    return found


def _dedupe(flows):
    seen, out = set(), []
    for f in flows:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def armed(user, thread_id=None, *, body=None, history=None):
    """Flow names armed for this reader right now (the stores, the body
    the widget sent, the thread's last agent turn). Read-only."""
    uname = _uname(user)
    found = list(_stored_flows(uname, thread_id))
    found.extend(_body_flows(body))
    found.extend(_history_flows(history))
    return _dedupe(found)


def labels_for(flows):
    seen, out = set(), []
    for f in flows or []:
        lbl = LABELS.get(str(f), '')
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out


# ------------------------------------------------------------------ drop
def drop_all(user, thread_id=None, *, close_offers=False, body=None, history=None):
    """Clear every armed intake for this reader (every thread's when no
    thread is given; the named thread's for the thread-keyed stores).
    Returns {'dropped': [flow names], 'labels': [words], 'offers_closed': n}.
    Never raises."""
    uname = _uname(user)
    res = {'dropped': [], 'labels': [], 'offers_closed': 0}
    if not uname:
        return res
    if _is_probe(uname):
        res['probe'] = True
        return res
    stored, stores_read = [], False
    try:
        stored = _stored_flows(uname, thread_id)
        stores_read = True
        res['dropped'] = _dedupe(list(stored) + _body_flows(body) + _history_flows(history))
        res['labels'] = labels_for(res['dropped'])
    except Exception as e:
        _faults.note('intake_reset.armed', e)
    s3, bucket = _s3()
    if s3 is None:
        return res
    # nothing in the stores and the stores answered: no write (this runs on
    # the first message of every fresh thread)
    if stores_read and stored:
        # the bpiq wizard's own clear (the pending-intake flow plus the per-thread key)
        try:
            from . import bpiq_wizard as _bw
            store = _bw.shared_store()
            if store is not None and callable(getattr(store, 'clear', None)):
                store.clear(uname, thread_id or '')
        except Exception as e:
            _faults.note('intake_reset.bpiq_store', e)
        # every per-thread bpiq key this call covers (the store cleared one)
        try:
            uk = _user_key(uname)
            for k in _bpiq_thread_keys(s3, bucket, uk, thread_id):
                try:
                    s3.delete_object(Bucket=bucket, Key=k)
                except Exception:
                    pass
        except Exception as e:
            _faults.note('intake_reset.bpiq_keys', e)
        # every guided intake on the user's pending-intake document (the
        # flywheel wizard, the journey, the campaign setup, the twins, the
        # bulk pull, a stashed brief); the document's other fields stay
        try:
            from . import pending_intake as _pi
            if _pi.active_flows(s3, bucket, uname, any_thread=True):
                _pi.clear(s3, bucket, uname)
        except Exception as e:
            _faults.note('intake_reset.pending_intake', e)
    elif not stores_read:
        # the stores could not be read: clear them anyway, blind
        try:
            from . import bpiq_wizard as _bw
            _bw.shared_store().clear(uname, thread_id or '')
        except Exception as e:
            _faults.note('intake_reset.bpiq_store', e)
        try:
            from . import pending_intake as _pi
            _pi.clear(s3, bucket, uname)
        except Exception as e:
            _faults.note('intake_reset.pending_intake', e)
    # open priced offers on this thread, on an explicit cancel only
    if close_offers and thread_id:
        try:
            from . import pending_offers as _po
            doc = _po._load(uname)
            n = 0
            for o in doc.get('offers') or []:
                if o.get('status') == 'open' and str(o.get('thread_id') or '') == str(thread_id):
                    o['status'] = 'cancelled'
                    o['closed'] = _po._now()
                    n += 1
            if n:
                _po._save(uname, doc)
                res['offers_closed'] = n
                if 'the offer' not in res['labels']:
                    res['labels'].append('the offer')
        except Exception as e:
            _faults.note('intake_reset.offers', e)
    return res


def on_new_thread(user, thread_id=None):
    """A new thread opened: every armed intake for this reader goes,
    whatever thread it was armed on. Offers stay (see the module note)."""
    try:
        return drop_all(user, None)
    except Exception as e:
        _faults.note('intake_reset.on_new_thread', e)
        return {'dropped': [], 'labels': [], 'offers_closed': 0}


def thread_is_fresh(uname, tid, history):
    """True for the first message on a thread nothing has answered in
    yet (the client minted the id, the idle rotation or a bind opened
    it). The dashboard saves the reader's own turn before it asks, so
    the stored thread may already hold that one user turn; a thread is
    fresh until an agent turn is stored in it."""
    if history or not tid:
        return False
    h = _host()
    if h is None:
        return False
    try:
        if not (h.has('s3_json') and h.has('thread_key')):
            return False
        stored = h.s3_json(h.thread_key(uname, tid), []) or []
        return not any(isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant')
                       for t in stored)
    except Exception as e:
        _faults.note('intake_reset.fresh', e)
    return False


def on_fresh_thread(user, tid, history):
    """Drop on the first message of a brand-new thread (no create call
    reached the server for it). Returns the drop result, or None."""
    try:
        uname = _uname(user)
        if not uname or not thread_is_fresh(uname, tid, history):
            return None
        return drop_all(uname, None)
    except Exception as e:
        _faults.note('intake_reset.on_fresh_thread', e)
        return None


# ----------------------------------------------------------------- words
def cancel_reply(res, confirm=False):
    """One plain line naming what went. ``confirm`` is a decline at a
    priced confirm step (the widget says so): the line also says nothing
    was charged, the promise that copy used to make."""
    labels = list((res or {}).get('labels') or [])
    if not labels:
        what = 'it'
    elif len(labels) == 1:
        what = labels[0]
    elif len(labels) == 2:
        what = f'{labels[0]} and {labels[1]}'
    else:
        what = ', '.join(labels[:-1]) + f', and {labels[-1]}'
    if confirm or (res or {}).get('offers_closed'):
        return f'Dropped {what}. Nothing is queued and nothing was charged. Ask me anything.'
    return f'Dropped {what}. Ask me anything.'


def cancel_payload(res, confirm=False):
    """The chat payload for the cancel lane: one plain line, no chips,
    every widget intake flag off."""
    return {
        'success': True,
        'action': 'answer',
        'reply': cancel_reply(res, confirm=confirm),
        'followups': [],
        'offer_deck': False,
        'deck_angle': None,
        'intake_dropped': list((res or {}).get('dropped') or []) or ['intake'],
        'bpiq_collect': False,
        'jiq_collect': False,
        'fw_collect': False,
        'aiq_collect': False,
        'twin_collect': False,
    }


__all__ = ['LABELS', 'is_cancel_text', 'armed', 'labels_for', 'drop_all', 'on_new_thread',
           'thread_is_fresh', 'on_fresh_thread', 'cancel_reply', 'cancel_payload']
