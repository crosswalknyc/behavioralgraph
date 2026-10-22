"""``ask()``: the single entry point every Prometheus client calls.

    understand -> gate -> run the surface core -> persist -> envelope

The dashboard widget, the standalone app, and the API all land here
with the same request shape::

    {'text': str,                    # required
     'thread_id': str | None,        # defaults to the caller's active thread
     'history': [...] | None,        # a client may send its own; else loaded
     'context': {...} | None,        # page facts: open profile, cuts, tabs
     'mode': str | None,             # explicit analyze mode (chip)
     'surface': str | None,          # a client mid armed-step names it
     'extra': {...} | None,          # explicit confirm / input step
     'persist': bool,                # save turns server-side (default: yes,
                                     #   except client == 'dashboard')
     'client': 'dashboard' | 'app' | 'api' | None}

The two request cores (``host.analyze_core`` / ``host.interpret_core``)
are the legacy route bodies, split out of their Flask views in Phase 1
so they run for an already-gated user from any surface. They still
return Flask responses; ``_unpack`` reads them back to a dict.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from .host import host
from . import understand
from . import envelope
from . import seams
from . import faults as _faults

_SURFACES = ('interpret', 'analyze', 'deck')
_ANALYZE_PASSTHROUGH = ('bind_subject', 'bind_cohort', 'overall_ranks',
                        'panel_confirm', 'bpiq_inputs', 'bpiq_confirm',
                        'jiq_inputs', 'jiq_confirm', 'fw_inputs',
                        'fw_confirm', 'aiq_inputs', 'aiq_confirm')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def username_of(user):
    return str((user or {}).get('username') or (user or {}).get('email')
               or 'anon').strip()


def _unpack(resp):
    """Flask Response | (Response, status) | dict -> (dict, status)."""
    status = 200
    if isinstance(resp, tuple):
        resp, status = resp[0], (resp[1] if len(resp) > 1 else 200)
    if isinstance(resp, dict):
        return resp, status
    try:
        data = resp.get_json(silent=True)
        if data is None:
            data = json.loads(resp.get_data(as_text=True) or '{}')
        return (data if isinstance(data, dict) else {'value': data}), \
            getattr(resp, 'status_code', status) or status
    except Exception:
        return {'success': False}, getattr(resp, 'status_code', 500) or 500


# ---------------------------------------------------------------- threads

def threads_index(uname):
    return host.load_threads_index(uname)


def active_thread_id(uname):
    idx = threads_index(uname)
    tid = idx.get('active')
    if not tid and idx.get('threads'):
        tid = idx['threads'][0]['id']
    return tid


def thread_exists(uname, tid):
    idx = threads_index(uname)
    return any(t.get('id') == tid for t in idx.get('threads', []))


def load_thread(uname, tid):
    return host.s3_json(host.thread_key(uname, tid), [])


def save_thread(uname, tid, history):
    """Write one thread by id and refresh its index entry. Never moves
    the caller's active thread: a request from the app or the API must
    not switch what the dashboard shows."""
    trimmed = list(history or [])[-200:]
    host.s3_put_json(host.thread_key(uname, tid), trimmed)
    idx = threads_index(uname)
    now = _now()
    for th in idx.get('threads', []):
        if th.get('id') == tid:
            th['updated'] = now
            th['turns'] = len(trimmed)
            if th.get('title') in (None, '', 'New chat'):
                try:
                    th['title'] = host.thread_title_from(trimmed)
                except Exception as _fx:
                    _faults.note('service', _fx)
            break
    host.s3_put_json(host.threads_index_key(uname), idx)
    return True


def new_thread(uname, *, activate=False, title=None):
    idx = threads_index(uname)
    now = _now()
    tid = uuid.uuid4().hex[:10]
    idx.setdefault('threads', []).append(
        {'id': tid, 'title': (title or 'New chat')[:48], 'created': now,
         'updated': now, 'turns': 0})
    cap = host.max_threads if host.has('max_threads') else 40
    if len(idx['threads']) > cap:
        idx['threads'] = sorted(
            idx['threads'], key=lambda t: str(t.get('updated') or ''),
            reverse=True)[:cap]
    if activate or not idx.get('active'):
        idx['active'] = tid
    host.s3_put_json(host.threads_index_key(uname), idx)
    host.s3_put_json(host.thread_key(uname, tid), [])
    return tid, idx


def ensure_thread(uname, tid, *, title=None):
    """A thread with this exact id exists for the user (created when it
    does not). Never changes the active thread: an API conversation must
    not move what the dashboard shows."""
    idx = threads_index(uname)
    if any(t.get('id') == tid for t in idx.get('threads', [])):
        return tid
    now = _now()
    idx.setdefault('threads', []).append(
        {'id': tid, 'title': (title or 'API conversation')[:48], 'created': now, 'updated': now, 'turns': 0, 'api': True})
    cap = host.max_threads if host.has('max_threads') else 40
    if len(idx['threads']) > cap:
        # the dashboard's own threads keep their cap; API conversations are pruned oldest first
        api_threads = sorted([t for t in idx['threads'] if t.get('api')], key=lambda t: str(t.get('updated') or ''))
        while len(idx['threads']) > cap and api_threads:
            gone = api_threads.pop(0)
            if gone.get('id') != tid:
                idx['threads'] = [t for t in idx['threads'] if t.get('id') != gone.get('id')]
    host.s3_put_json(host.threads_index_key(uname), idx)
    host.s3_put_json(host.thread_key(uname, tid), [])
    return tid


def find_job_doc(job_id):
    """(doc, kind) for a job id across every job store, or (None, None).
    No owner check: callers that have no user (the webhook sweeper) read
    the document and judge the owner themselves."""
    jid = str(job_id or '').strip()
    if not jid or len(jid) > 64 or not all(c.isalnum() or c in '-_' for c in jid):
        return None, None
    s3 = host.s3_client
    for prefix, jtype in _job_prefixes():
        try:
            obj = s3.get_object(Bucket=host.bucket, Key=f'{prefix}{jid}.json')
            return json.loads(obj['Body'].read().decode('utf-8')), jtype
        except Exception:
            continue
    return None, None


def job_doc_field(job_id, field):
    """One field stamped on a job document at launch, or None."""
    try:
        doc, _kind = find_job_doc(job_id)
        return str((doc or {}).get(field) or '').strip() or None
    except Exception:
        return None


def job_thread_id(job_id):
    """The thread id stamped on a job document at launch, or None."""
    return job_doc_field(job_id, 'thread_id')


def list_threads(uname, subject_key=None):
    idx = threads_index(uname)
    threads = sorted(idx.get('threads', []),
                     key=lambda t: str(t.get('updated') or ''),
                     reverse=True)
    if subject_key:
        sk = str(subject_key).strip()
        threads = [t for t in threads
                   if isinstance(t.get('binding'), dict) and t['binding'].get('subject_key') == sk]
    return {'active': idx.get('active'), 'threads': threads}


def thread_binding(uname, tid):
    """The project a thread is bound to ({'subject_key', 'subject_label',
    'product'}), or None for the general chat."""
    from . import thread_binding as _tb
    try:
        return _tb.binding_of_thread(threads_index(uname), tid)
    except Exception as _fx:
        _faults.note('service', _fx)
        return None


def stash_binding(uname, tid=None):
    """Put the thread's project on the request (``g._pm_thread_id`` /
    ``g._pm_thread_binding``) so the binder reads it as the thread
    subject and the ask log records it. The explicit id wins when the
    caller sent one that is theirs; else the active thread. Never
    raises."""
    try:
        from flask import g as _g
        t = str(tid or '').strip() or None
        if t and not thread_exists(uname, t):
            t = None
        if not t:
            t = active_thread_id(uname)
        _g._pm_thread_id = t
        _g._pm_thread_binding = thread_binding(uname, t) if t else None
        return _g._pm_thread_binding
    except Exception as _fx:
        _faults.note('service', _fx)
        return None


def thread_for_subject(uname, subject_key, subject_label=None, product=None, *, create=True):
    """The conversation follows the project (Debi Pomerantz 2026-10-21):
    activate the thread bound to this project, creating it when none
    exists; an empty subject_key activates the general chat. Persists
    the index and the bodies of any thread the cap retired.

    Returns {'tid', 'created', 'switched', 'binding', 'history'}."""
    from . import thread_binding as _tb
    idx = threads_index(uname)
    cap = host.max_threads if host.has('max_threads') else 40
    out = _tb.bind(idx, subject_key, subject_label, product, cap=cap, create=create)
    if not out.get('tid'):
        out['history'] = []
        return out
    host.s3_put_json(host.threads_index_key(uname), idx)
    if out.get('created'):
        host.s3_put_json(host.thread_key(uname, out['tid']), [])
    for gone in out.get('retired') or []:
        try:
            host.s3_client.delete_object(Bucket=host.bucket, Key=host.thread_key(uname, gone))
        except Exception as _fx:
            _faults.note('service', _fx)
    out['history'] = [] if out.get('created') else load_thread(uname, out['tid'])
    return out


# -------------------------------------------------------------------- ask

def _gate_for(surface, user):
    """Mode + funds gates for the chosen surface. Returns a Flask
    response to short-circuit with, or None to proceed."""
    if surface in ('analyze', 'deck'):
        if not host.gate_analyze(user):
            return host.gate_refusal('analyze')
    else:
        if not host.gate_pull(user):
            return host.gate_refusal('pull')
        # The interpret view ran its funds gate before parsing; the
        # analyze core runs its own inside. Mirror the view here.
        fr = host.funds_gate(user)
        if fr is not None:
            return fr
    return None


def _analyze_body(body, text, history, decision, tid=None):
    ctx = body.get('context')
    if ctx is None:
        ctx = body.get('page_context')
    out = {'text': text, 'history': history, 'page_context': ctx or None,
           'thread_id': tid}
    if isinstance(body.get('reading'), dict):
        out['reading'] = body['reading']   # the Reader's understanding (2026-10-14)
    mode = decision.get('mode') or body.get('mode')
    if mode:
        out['mode'] = mode
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    for k in _ANALYZE_PASSTHROUGH:
        v = extra.get(k, body.get(k))
        if v is not None:
            out[k] = v
    return out


def _deck_body(body, text, history, ctx, tid=None):
    out = {'text': text, 'history': history, 'page_context': ctx or None,
           'thread_id': tid}
    for k in ('angle', 'confirm_open_screen'):
        if body.get(k) is not None:
            out[k] = body[k]
    return out


def _interpret_body(body, text, history, tid=None):
    out = {'text': text, 'history': history, 'thread_id': tid}
    for k in ('locked_sample_tu', 'locked_sample_avid'):
        if body.get(k) is not None:
            out[k] = body[k]
    if isinstance(body.get('reading'), dict):
        out['reading'] = body['reading']
    return out


_ACCEPT_RX = re.compile(
    r"^\s*(ok(ay)?|yes|yeah|yep|yup|sure|please|go|go ahead|do it|run it|"
    r"build it|approve[d]?|run|build|start|let's do it|lets do it|sounds good|"
    r"fine)\b[\s,.!-]*(ok(ay)?|yes|please|go|run|build|start|do it|it|that|"
    r"the run|the profile|the build)?", re.I)
_OFFER_RX = re.compile(r"^run a profile on (.+)$", re.I)


def _last_agent_options(history):
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        role = str(h.get('role') or '').lower()
        if role == 'user':
            return []
        if role in ('agent', 'assistant'):
            meta = h.get('meta') if isinstance(h.get('meta'), dict) else {}
            opts = meta.get('options') or h.get('options') or []
            out = []
            for o in opts:
                if isinstance(o, dict):
                    out.append(str(o.get('send') or o.get('label') or ''))
                elif isinstance(o, str):
                    out.append(o)
            return [o for o in out if o]
    return []


_APPROVED_NAMED_RUN_RX = re.compile(
    r"^\s*(?:yes[,.!]?\s*)?(?:ok(?:ay)?[,.!]?\s*)?approve[d]?\s+"
    r"(?:(?:the|this|that|a)\s+)?(?:run|build|profile|brief|pull)"
    r"(?:\s+(?:on|for|of))\s+(?P<subject>[^?]{2,80}?)\s*[.!]*\s*$", re.I)


def approved_named_run(text):
    """'Approve the run on Shannen Doherty' -> 'Shannen Doherty'; '' when
    the approval names no subject (that is a short reply) or the text
    is a question."""
    t = str(text or '').strip()
    if not t or '?' in t or len(t) > 120:
        return ''
    m = _APPROVED_NAMED_RUN_RX.match(t)
    if not m:
        return ''
    subj = re.sub(r"\s+", " ", m.group('subject')).strip(" '\".,;:")
    if not subj or subj.lower() in ('it', 'this', 'that', 'them', 'the brief', 'the run'):
        return ''
    return subj


def accept_offered_run(history, text):
    """When the previous agent turn offered 'Run a profile on X' and this
    turn accepts it in words, return that chip's send value; else ''.
    'ok run Gunna', 'yes', 'go ahead and build it', 'run gunna' all
    accept; 'why do I need that', 'not now', a new question do not."""
    t = str(text or '').strip()
    if not t or len(t) > 80:
        return ''
    offers = [o for o in _last_agent_options(history) if _OFFER_RX.match(o.strip())]
    if not offers:
        return ''
    low = t.lower()
    if re.search(r"\b(not now|no thanks|no|later|why|what|how|which|who|\?)", low) \
            and not re.match(r"^\s*(ok|yes|yeah|sure)\b", low):
        return ''
    for o in offers:
        subj = _OFFER_RX.match(o.strip()).group(1).strip()
        subj_l = subj.lower()
        if re.search(r"\b(run|build|pull|start|do)\b.*" + re.escape(subj_l), low) \
                or low == subj_l:
            return o.strip()
    # A build verb followed by some OTHER noun phrase is a different ask
    # ("run a profile on Taylor Swift" under a Gunna offer).
    m = re.search(r"\b(run|build|pull|start)\b\s+(?:a profile on\s+|a profile for\s+)?(.*)$", low)
    if m:
        rest = re.sub(r"\b(it|that|this|the run|the profile|the build|please|now|again|for me|thanks?)\b",
                      " ", m.group(2))
        rest = re.sub(r"[^a-z0-9]+", " ", rest).strip()
        if rest and not any(re.search(re.escape(_OFFER_RX.match(o.strip()).group(1).strip().lower()), rest)
                            for o in offers):
            return ''
    if len(offers) == 1 and _ACCEPT_RX.match(t) and len(t.split()) <= 6:
        return offers[0].strip()
    return ''


# The dispatcher's lane order (2026-10-21). ask() reads top to bottom:
# commands and guards, then the continuation read against the previous
# agent turn (I2), then every deterministic lane (a lookup is free and
# metered), and only when all of those have declined the priced custom
# ask offer (the work line) and the model surfaces. Where-watched is a
# deterministic lane and is never priced. Each block below carries a
# "# lane: <name>" marker; scripts/test_pm_custom_ask_pricing.py holds
# the source order to this tuple.
LANE_ORDER = (
    'offer_acceptance', 'priced_offer_acceptance', 'offer_answer', 'referents', 'document', 'confirm_answer', 'short_reply',
    'spend_cap_precheck',
    'continuation',
    'talent_list_acceptance', 'meta_check', 'standing', 'where_watched', 'sizing_quote',
    'reader', 'pricing', 'design_request', 'spend_cap', 'thread_followups', 'catalog',
    'sizing_fallback', 'status', 'deliverable_lookup', 'email_file', 'brand_coverage',
    'definitions', 'capability',
    'surface_decision',
    'work_gate',
)
DETERMINISTIC_LANES = frozenset((
    'meta_check', 'standing', 'where_watched', 'sizing_quote', 'reader', 'pricing', 'design_request',
    'thread_followups', 'catalog', 'sizing_fallback', 'status', 'deliverable_lookup', 'email_file',
    'brand_coverage', 'definitions', 'capability'))
NEVER_PRICED_LANES = frozenset(('where_watched', 'status', 'pricing', 'catalog', 'definitions',
                                'deliverable_lookup', 'email_file', 'brand_coverage', 'capability'))


def lane_markers(source=None):
    """The '# lane: <name>' markers of ask() in source order."""
    import inspect as _inspect
    src = source if source is not None else _inspect.getsource(ask)
    return [m.group(1) for m in re.finditer(r"^\s*# lane: ([a-z_]+)\s*$", src, re.M)]


def ask(user, body, *, via='session'):
    """Run one ask end to end. Returns (envelope_dict, http_status)."""
    body = body if isinstance(body, dict) else {}
    text = str(body.get('text') or '').strip()
    uname = username_of(user)
    # Standing preferences in the ask ("always", "from now on") are kept
    # as rules for every later answer (2026-10-13 audit).
    try:
        from . import shared_memory as _shm0
        _txt0 = str((body or {}).get('text') or '')
        if _txt0 and uname and _shm0.capture_preferences(uname, _txt0):
            # the rule is kept; the question beside it routes on its own
            text = _shm0.strip_preferences(_txt0).strip() or text
            body = dict(body, text=text)
    except Exception as _fx:
        _faults.note('service', _fx)
    client = str(body.get('client') or '').strip().lower()
    persist = bool(body.get('persist', client != 'dashboard'))

    try:
        from flask import g as _g_ask
        _g_ask._pm_ask_text = text
    except Exception:
        pass
    if not text:
        env = envelope.wrap({'success': False,
                             'error': 'Ask me something and I will take it from there.'},
                            surface='interpret', decision={'reason': 'empty'},
                            via=via)
        return env, 200

    # Thread: explicit id must belong to the caller; else the active one.
    tid = str(body.get('thread_id') or '').strip() or None
    if tid and not thread_exists(uname, tid):
        return envelope.wrap({'success': False, 'error': 'unknown thread'},
                             surface='interpret',
                             decision={'reason': 'bad_thread'}, via=via), 404
    if not tid:
        tid = active_thread_id(uname)
    # The project this thread is bound to rides the request (2026-10-21,
    # Debi: the conversation follows the project). The binder reads it as
    # the thread's subject ahead of the history and the page; the ask log
    # records it; nothing else in the ask changes.
    stash_binding(uname, tid)

    history = body.get('history')
    if not isinstance(history, list):
        history = load_thread(uname, tid) if tid else []

    ctx = body.get('context')
    if ctx is None:
        ctx = body.get('page_context')
    has_ctx = bool(ctx)
    open_tabs = 0
    try:
        open_tabs = len((ctx or {}).get('other_tabs') or [])
    except Exception as _fx:
        _faults.note('service', _fx)

    # lane: offer_acceptance
    # Accepting an offered run (2026-10-06, Eliot / Gunna): the build-
    # first offer ends with the chips 'Run a profile on X' / 'Not now'.
    # A typed acceptance ("ok run Gunna", "yes", "go ahead", "build
    # it") is that chip, not a new question; before this it re-ran the
    # analyze path and re-issued the identical offer. The text becomes
    # the chip's own send value so the build flow takes it.
    try:
        _accepted = accept_offered_run(history, text)
    except Exception:
        _accepted = ''
    if _accepted:
        print(f"[prometheus] offer accepted: {text[:60]!r} -> {_accepted!r}")
        text = _accepted
        body = dict(body)
        body['text'] = text
        body['surface'] = 'interpret'
    else:
        # An approval that names the run IS the run (2026-10-07 Scott:
        # "Approve the run on Shannen Doherty" re-drew the offer, then
        # the chip read "Run a profile on Approve"). The subject the
        # reader approved becomes the build order; a bare "approve"
        # stays a short reply answered below.
        _named_run = approved_named_run(text)
        if _named_run:
            print(f"[prometheus] approval names the run: {text[:60]!r} -> {_named_run!r}")
            text = 'Run a profile on ' + _named_run
            body = dict(body)
            body['text'] = text
            body['surface'] = 'interpret'

    # lane: priced_offer_acceptance
    # A typed approval is the chip (Jenna 2026-10-22, Casey Pearson at
    # Paramount+: the $100 offer on her Jackass: Best & Last read, then
    # "Approve", drew "Nothing is waiting on an approval right now",
    # twice). The offer lived only in the widget's armed state, which a
    # thread switch or a history refresh drops, so the server had nothing
    # to bind the word to. Every priced offer is now a pending item on the
    # server (prometheus.pending_offers); a bare approval binds to the
    # most recent open one across the user's recent threads and runs the
    # offered question with the chip's own panel_confirm, charging once at
    # the quoted price; a bare cancel closes it with nothing charged. The
    # no-pending line below is right only when nothing is open anywhere.
    if not _armed(body) and not body.get('_meta_replay') \
            and not str(body.get('text') or '').lower().startswith('run a profile on '):
        try:
            from . import pending_offers as _pend
            _pend_hit = _pend.resolve_reply(uname, tid, text, history, ctx=body.get('context') or body.get('page_context'))
        except Exception as e:
            _faults.note('service.pending_offer', e)
            _pend_hit = None
        if _pend_hit and _pend_hit['action'] == 'approve':
            _po = _pend_hit['offer']
            print(f"[prometheus] typed approval binds the pending offer: {text[:40]!r} -> "
                  f"{str(_po.get('question') or '')[:80]!r} ({_po.get('kind') or 'report'}, {_po.get('price_label') or 'quoted'})")
            try:
                if _po.get('id'):
                    _pend.close(uname, _po['id'], 'approved')
            except Exception as _fx:
                _faults.note('service.pending_offer_close', _fx)
            body = dict(body)
            body['_client_surface_sent'] = str(body.get('surface') or '').strip().lower()
            body['_approved_offer'] = {'id': _po.get('id') or '', 'thread_id': _po.get('thread_id') or '',
                                       'typed': str(text)[:40]}
            text = str(_po.get('question') or '').strip()
            body['text'] = text
            body['surface'] = 'analyze'
            body['panel_confirm'] = _pend.confirm_body(_po)
            try:
                # the read's own lane names the route; the approval rides
                # along as a field the digest and the replay can see
                from flask import g as _g
                _g._pm_approved_offer = {'typed': str(body['_approved_offer']['typed']), 'offer_id': _po.get('id') or '',
                                         'price': str(_po.get('price_label') or ''), 'kind': str(_po.get('kind') or ''),
                                         'offer_thread': str(_po.get('thread_id') or '')}
            except Exception as _fx:
                _faults.note('service', _fx)
        elif _pend_hit and _pend_hit['action'] == 'cancel':
            _po = _pend_hit['offer']
            try:
                if _po.get('id'):
                    _pend.close(uname, _po['id'], 'cancelled')
            except Exception as _fx:
                _faults.note('service.pending_offer_close', _fx)
            raw = {'success': True, 'action': 'answer', 'reply': _pend.DECLINE_REPLY,
                   'followups': [], 'offer_deck': False, 'deck_angle': None,
                   'offer_cancelled': {'subject': str(_po.get('subject') or ''),
                                       'question': str(_po.get('question') or '')[:200]}}
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'priced_offer_cancelled', 'client_hint': None}
            if str(body.get('surface') or '').strip().lower() == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = []
            try:
                host.ask_hint(route='priced_offer_acceptance', outcome='cancelled',
                              subject=str(_po.get('subject') or ''))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: offer_answer
    # Answering an offer (2026-10-07 Jenna, Eliot / Gunna Concert Goers):
    # the previous agent turn asked "Do you still want me to pull it?"
    # with the offer id on its meta. A yes launches that build through
    # the approve route (priced, charged, queued like the dashboard
    # button); a no closes it. Either way the message is the answer to
    # the offer, never a new ask.
    try:
        from . import offers as _offers
        _oid = _offers.pending_in_history(history)
        _okind = _offers.answer_kind(text) if _oid else ''
    except Exception:
        _oid, _okind = '', ''
    if _oid and _okind:
        from flask import current_app as _cur
        try:
            _state, _reply, _odoc = _offers.decide(
                _cur._get_current_object(), _oid, _okind, via='chat',
                user_text=text, append=persist)
        except Exception as e:
            print(f"[prometheus] offer decision failed: {e}")
            _state, _reply, _odoc = 'failed', '', None
        if _reply:
            raw = {'success': True, 'action': 'answer', 'reply': _reply,
                   'followups': [], 'offer_id': _oid, 'offer_state': _state}
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'offer_answer', 'client_hint': None}
            if str(body.get('surface') or '').strip().lower() == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = []
            try:
                host.ask_hint(route='offer_answer', outcome=_state)
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            # decide() wrote both turns when this caller persists.
            return env, 200

    # lane: referents
    # Unresolved referents (2026-10-02 Jenna: "it should have asked him
    # which 3 influencers he was talking about then actually given him
    # the answer"). Two halves, both before any surface runs:
    #  1. This turn is the names answering our which-ones question:
    #     fold them into the question that asked, and run that on the
    #     analyze surface bound to those names.
    #  2. This turn points at "these three creators" / "both shows" /
    #     "all 4 titles" and names none of them anywhere we can see:
    #     ask which ones. No model call, no charge, no offer.
    # lane: document
    # Documents on the thread (2026-10-06, Jenna: "upload a document to
    # it. like a powerpoint to be edited, or pdf or other data"). Runs
    # BEFORE the referent clarify, the standing answers and the Reader
    # (2026-10-16: 'can you see the full url now?' drew a which-one
    # question from memory and a Profile IQ pitch while the file sat on
    # the thread). An ask about an attached file is answered from the
    # file, or applied to it and handed back; never a build, never a
    # design request.
    _client_surface = str(body.get('_client_surface_sent') or body.get('surface') or '').strip().lower()
    if not _armed(body):
        try:
            from . import documents as _docs
            _doc_raw = _docs.answer(text, uname, tid, history)
        except Exception as e:
            _faults.note('service.document_lane', e)
            _doc_raw = None
        if _doc_raw:
            raw = _doc_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'document', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_doc_raw.get('followups') or [])
                if _doc_raw.get('file_link'):
                    raw['file_link'] = _doc_raw['file_link']
            try:
                host.ask_hint(route='document', outcome='answered')
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    referent_decision = None
    _client_surface = str(body.get('_client_surface_sent') or body.get('surface') or '').strip().lower()
    try:
        from . import referents as _refs
        _merged, _label = _refs.answer_merge(history, text)
        if _merged:
            text = _merged
            body = dict(body)
            body['text'] = text
            if not body.get('bind_subject') and not (
                    isinstance(body.get('extra'), dict)
                    and body['extra'].get('bind_subject')):
                body['bind_subject'] = _label
            referent_decision = {'surface': 'analyze', 'mode': None,
                                 'reason': 'referent_answer',
                                 'client_hint': None}
        else:
            _extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
            _armed_now = any(_extra.get(k) or body.get(k)
                             for k in _ANALYZE_PASSTHROUGH)
            if not _armed_now:
                _ref = _refs.detect_unresolved(text, history, ctx)
                if _ref:
                    raw = _refs.clarify_payload(_ref, text)
                    if _client_surface == 'interpret':
                        raw = _interpret_shape(raw)
                    decision = {'surface': 'analyze', 'mode': None,
                                'reason': 'referent_clarify',
                                'client_hint': None}
                    try:
                        host.ask_hint(route='referent_clarify',
                                      outcome='asked_which')
                    except Exception as _fx:
                        _faults.note('service', _fx)
                    env = envelope.wrap(raw, surface='analyze',
                                        decision=decision,
                                        thread_id=tid, via=via)
                    _persist_turn(persist, uname, tid, history, text,
                                  env, raw, 'analyze')
                    return env, 200
    except Exception as e:
        _faults.note('service.referent_gate', e)

    # lane: confirm_answer
    # Answers to a which-one confirm (2026-10-05, Scott). The turn
    # before asked "Do you mean for Will And Grace, or Will & Grace on
    # Hulu?" and the user typed "both". The widget only resolves exact
    # chip text, so the raw word reached the router, which asked the
    # same question again and then gave up. The server resolves it:
    # both / either / whichever / a label / "the Hulu one" picks the
    # option (the same entity under two spellings is one option), and
    # the ORIGINAL ask re-runs bound to it. Never ask twice.
    if referent_decision is None and not _armed(body):
        try:
            _ca = _confirm_answer(text, history)
        except Exception as e:
            _faults.note('service.confirm_answer_gate', e)
            _ca = None
        if _ca:
            text = _ca['question']
            body = dict(body)
            body['text'] = text
            if _ca.get('subject') and not body.get('bind_subject'):
                body['bind_subject'] = _ca['subject']
                if _ca.get('cohort'):
                    body['bind_cohort'] = _ca['cohort']
            try:
                host.ask_hint(route='confirm_answer',
                              outcome='resolved',
                              subject=_ca.get('subject'))
            except Exception as _fx:
                _faults.note('service', _fx)

    # lane: short_reply
    # Bare replies (2026-10-02 audit). "approved", "ok", "no", "1",
    # "none" with nothing armed on the server side are not asks. They
    # used to reach a reasoning pass that guessed a subject out of them
    # ("approved" became a fresh build of the last profile mentioned;
    # "no" re-asked the open-screen question it was declining). With no
    # armed step they get a short, deterministic reply; a number or a
    # yes that answers the options on the previous agent turn is
    # rewritten to that option and runs as that option.
    if referent_decision is None:
        try:
            _rewrite, _bare = _bare_reply(body, text, history, has_ctx, uname=uname)
            if _rewrite:
                text = _rewrite
                body = dict(body)
                body['text'] = text
            elif _bare is not None:
                decision = {'surface': 'analyze' if has_ctx else 'interpret',
                            'mode': None, 'reason': 'short_reply',
                            'client_hint': None}
                raw = _bare
                if _client_surface == 'interpret':
                    raw = _interpret_shape(raw)
                try:
                    host.ask_hint(route='short_reply', outcome='answered')
                except Exception as _fx:
                    _faults.note('service', _fx)
                env = envelope.wrap(raw, surface=decision['surface'],
                                    decision=decision, thread_id=tid,
                                    via=via)
                _persist_turn(persist, uname, tid, history, text, env, raw,
                              decision['surface'])
                return env, 200
        except Exception as e:
            _faults.note('service.bare_reply_gate', e)

    # lane: spend_cap_precheck
    # Per-user daily spend cap (audit item 8, 2026-10-14): over the cap the
    # model surfaces stop for the day with one honest line; the lookups
    # below (pricing, catalog, sizing from the catalog, status) still run.
    _cap_hit = False
    if referent_decision is None and not _armed(body):
        try:
            from . import spend_guard as _sg
            _ok, _spent, _cap = _sg.check(user)
            _cap_hit = not _ok
        except Exception as _fx:
            _faults.note('service.spend_guard', _fx)
            _cap_hit = False

    # lane: continuation
    # Continuation before routing (I2, 2026-10-20). Every turn is read
    # against the previous agent turn first: a re-run in a window, a
    # reply to the question just asked, a parameter change on the last
    # read, a question about the answer, a correction. Each binds its
    # subject and deliverable from the thread and never falls onto the
    # open tab, a fresh priced offer or a calm line. The same question
    # coming back after a reply the reader did not accept takes the
    # other path (I7); the prior ask is graded failed in real time.
    if referent_decision is None and not _armed(body) and not body.get('_meta_replay'):
        try:
            from . import turn_gate as _tg
            _tg_out = _tg.gate(text, body, history, ctx, user, uname, tid, via, persist,
                               client_surface=_client_surface, host=host, envelope=envelope,
                               interpret_shape=_interpret_shape, persist_turn=_persist_turn)
        except Exception as e:
            _faults.note('service.turn_gate', e)
            _tg_out = None
        if _tg_out is not None:
            if isinstance(_tg_out, tuple):
                return _tg_out
            # a rewrite: the gate re-bound the text / body and lets the lanes run
            text = str(_tg_out.get('text') or text)
            body = dict(_tg_out.get('body') or body)

    # The Reader (2026-10-14, Jenna: "make prometheus closer to your level
    # of intelligence in understanding what is being asked"). One
    # understanding step with the whole thread, the page, the library
    # candidates and the labeled bank in view, before any pattern lane.
    # Deterministic intents run here; the rest steer the surface below.
    # The lanes stay as the fallback when the Reader is unsure or away.
    # Two standing answers before the Reader (2026-10-14): behavior with
    # no digital trace is declined with the nearest measurable read, and
    # a question about where the data comes from gets the plain
    # first-party answer. No model call, no charge.
    # lane: talent_list_acceptance
    # The talent list (Custom Ask, 2026-10-15): the chip on a roster offer,
    # or a yes right after it, charges the seat and queues the list. No
    # model call; never a profile.
    if referent_decision is None and not body.get('_meta_replay'):
        try:
            from . import talent_list_job as _tl
            _acc = _tl.accepted(text, history)
        except Exception as _fx:
            _faults.note('service.talent_list', _fx)
            _acc = None
        if _acc:
            try:
                raw = _tl.start(user, uname, text, _acc, tid)
            except Exception as _fx:
                _faults.note('service.talent_list', _fx)
                raw = {'success': False, 'guidance': True, 'followups': [],
                       'error': 'The list did not start. Nothing was charged. Ask me again in a moment.'}
            decision = {'surface': 'analyze', 'mode': None, 'reason': 'talent_list', 'client_hint': None}
            _out = _interpret_shape(raw) if _client_surface == 'interpret' else raw
            env = envelope.wrap(_out, surface='analyze', decision=decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, _out, 'analyze')
            return env, 200
    # lane: meta_check
    # A meta check ("are you not working?", "hello?", "you there?") never
    # repeats a pending confirm (Emmet at Kartel got the same "Do you mean
    # for ..." back to it, 2026-10-15). It answers the question the reader
    # is waiting on, through the door again, with a one-line lead.
    if referent_decision is None and not body.get('_meta_replay') and meta_check(text):
        prev = last_user_question(history)
        if prev:
            _rb = {k: v for k, v in body.items() if k not in _ARMED_BODY_KEYS and k not in _ANALYZE_PASSTHROUGH and k != 'extra'}
            env, st = ask(user, dict(_rb, text=prev, _meta_replay=True, persist=False), via=via)
            lead = 'I am here. Picking your question back up. '
            env['text'] = lead + str(env.get('text') or '')
            _r = env.get('raw') if isinstance(env.get('raw'), dict) else env.get('_raw')
            if isinstance(_r, dict):
                for k in ('reply', 'error'):
                    if _r.get(k):
                        _r[k] = lead + str(_r[k])
                        break
            _persist_turn(persist, uname, tid, history, text, env, _r or {}, str(env.get('surface') or 'analyze'))
            return env, st
        raw = {'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
               'reply': 'I am here. Ask me anything about an audience, a title, a brand or a person and I will take it from there.'}
        decision = {'surface': 'analyze', 'mode': None, 'reason': 'meta_check', 'client_hint': None}
        _out = _interpret_shape(raw) if _client_surface == 'interpret' else raw
        env = envelope.wrap(_out, surface='analyze', decision=decision, thread_id=tid, via=via)
        _persist_turn(persist, uname, tid, history, text, env, _out, 'analyze')
        return env, 200
    # lane: standing
    if referent_decision is None and not _armed(body):
        _standing = _standing_answer(text)
        if _standing is None:
            # A journey-figure question reads from the delivered journey
            # first (Jenna 2026-10-14: two different numbers for one
            # question is its own trust problem). Exact, no model call.
            try:
                from . import journey_lane as _jl
                _standing = _jl.answer(text, ctx, host=host, user=user, history=history)
                if _standing is not None:
                    try:
                        host.ask_hint(route='journey_file', outcome='answered', subject=_standing.get('profile'))
                    except Exception:
                        pass
            except Exception as _fx:
                _faults.note('service.journey_lane', _fx)
                _standing = None
        if _standing is not None:
            _decision = {'surface': 'analyze', 'mode': None, 'reason': 'standing', 'client_hint': None}
            _out = _interpret_shape(_standing) if _client_surface == 'interpret' else _standing
            env = envelope.wrap(_out, surface='analyze', decision=_decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, _out, 'analyze')
            return env, 200
    # lane: where_watched
    # Where a named title is watched (Jay Patel 2026-10-20: "where people
    # watched the film mutiny" with Prime Video open). A lookup of
    # carriage from the library, the content map and one research call,
    # never a put-together read: free at the metered rate, and it runs
    # before any priced quote so no offer can stand in for it (regressed
    # on 2026-10-21 to "Pulling this data will cost $500."; the rule is the
    # work line: a lookup is free, a put-together read is the custom ask).
    if referent_decision is None and not _armed(body):
        try:
            from . import where_watched as _ww
            _ww_raw = _ww.answer(text, ctx, host=host, user=user, history=history)
        except Exception as _fx:
            _faults.note('service.where_watched', _fx)
            _ww_raw = None
        if _ww_raw is not None:
            _decision = {'surface': 'analyze', 'mode': None, 'reason': 'where_watched', 'client_hint': None}
            _out = _interpret_shape(_ww_raw) if _client_surface == 'interpret' else _ww_raw
            env = envelope.wrap(_out, surface='analyze', decision=_decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, _out, 'analyze')
            return env, 200
    # lane: sizing_quote
    # Sizing ($100, rule 26) and the cross of a held audience across
    # services (a custom ask in the sizing lane) quote first and wait for
    # approve (2026-10-21, Jenna). Runs armed or not so a confirm cannot
    # fall through to a compiled read. Where-watched is never here: it is
    # the free lane above.
    if referent_decision is None:
        try:
            from . import priced_read as _pr
            _ex = body.get('extra') if isinstance(body.get('extra'), dict) else {}
            _pc = _ex.get('panel_confirm') or body.get('panel_confirm')
            _pr_raw = _pr.service_turn(
                text, user=user, ctx=ctx, history=history,
                host=host, panel_confirm=_pc)
        except Exception as e:
            _faults.note('service.priced_read', e)
            _pr_raw = None
        if _pr_raw:
            raw = _pr_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'priced_read', 'client_hint': None}
            if (_client_surface == 'interpret' and _pr_raw.get('success')
                    and _pr_raw.get('reply')):
                raw = _interpret_shape(_pr_raw)
                raw['followups'] = list(_pr_raw.get('followups') or [])
                if _pr_raw.get('panel_offer'):
                    raw['panel_offer'] = _pr_raw['panel_offer']
            try:
                host.ask_hint(
                    route=(str(_pr_raw.get('priced_lane') or '') if _pr_raw.get('priced_lane') and not _pr_raw.get('panel_offer')
                           else 'priced_read'),
                    outcome=('panel_offer' if _pr_raw.get('panel_offer')
                             else 'answered'),
                    subject=str(_pr_raw.get('subject') or ''))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: reader
    reading = None
    if referent_decision is None and not _armed(body) and len(text.split()) >= 2 and not _cap_hit:
        try:
            from . import reader as _rd
            from . import progress as _pg
            reading = _rd.read(text, ctx, history, user, uname, progress=_pg.say)
        except Exception as e:
            _faults.note('service.reader', e)
            reading = None
        if reading:
            body = dict(body, reading=reading)
            try:
                from flask import g as _g_rd
                _g_rd._pm_reading = reading
            except Exception as _fx:
                _faults.note('service', _fx)
            try:
                _rx = _execute_reading(reading, text, ctx, history, user, uname, body, tid, via, persist, _client_surface)
            except Exception as e:
                _faults.note('service.reader_executor', e)
                _rx = None
            if _rx is not None:
                return _rx

    # lane: pricing
    # Pricing (2026-10-06, audit item 5): one deterministic answer from
    # the rate card on every surface. It used to be an error on one
    # surface, a decline on another and an answer on the third.
    if referent_decision is None and not _armed(body):
        try:
            _is_price = bool(host.pricing_question(text)) if host.has('pricing_question') else False
        except Exception:
            _is_price = False
        if _is_price:
            try:
                # Rate card in the seat's currency (East Tree Media and
                # Omaze read pounds, Jenna 2026-10-08).
                if host.has('pricing_copy_for'):
                    copy = str(host.pricing_copy_for(uname) or '')
                else:
                    copy = str(host.pricing_copy) if host.has('pricing_copy') else ''
            except Exception:
                copy = ''
            if copy:
                raw = {'success': True, 'action': 'answer', 'reply': copy,
                       'followups': ['What is my balance?'],
                       'offer_deck': False, 'deck_angle': None}
                decision = {'surface': 'analyze', 'mode': None,
                            'reason': 'product_fact', 'client_hint': None}
                if _client_surface == 'interpret':
                    raw = _interpret_shape(raw)
                try:
                    host.ask_hint(route='pricing', outcome='answered')
                except Exception as _fx:
                    _faults.note('service', _fx)
                env = envelope.wrap(raw, surface='analyze', decision=decision,
                                    thread_id=tid, via=via)
                _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
                return env, 200

    # lane: design_request
    # Design requests (2026-10-06, Jenna, Alexia's "Section 4 ... please
    # remove"). A change-the-page ask is not a question and never a
    # build: one fixed reply, one email to the user experience team.
    if referent_decision is None and not _armed(body):
        try:
            from . import design_request as _dr
            _dr_raw = _dr.answer(text, user, ctx) if _dr.is_design_request(text) else None
        except Exception as e:
            _faults.note('service.design_request_lane', e)
            _dr_raw = None
        if _dr_raw:
            raw = _dr_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'design_request', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = []
            try:
                host.ask_hint(route='design_request', outcome='answered')
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: spend_cap
    if _cap_hit and referent_decision is None and not _armed(body):
        from . import spend_guard as _sg2
        _ok2, _spent2, _cap2 = _sg2.check(user)
        raw = {'success': True, 'action': 'answer', 'reply': _sg2.over_cap_reply(_spent2, _cap2), 'followups': [], 'offer_deck': False, 'deck_angle': None}
        decision = {'surface': 'analyze', 'mode': None, 'reason': 'spend_cap', 'client_hint': None}
        if _client_surface == 'interpret':
            raw = _interpret_shape(raw)
        try:
            host.ask_hint(route='spend_cap', outcome='declined_spend_cap')
        except Exception as _fx:
            _faults.note('service', _fx)
        env = envelope.wrap(raw, surface='analyze', decision=decision, thread_id=tid, via=via)
        _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
        return env, 200

    # lane: thread_followups
    # 2026-10-13 audit lanes, all deterministic, all before any model:
    #   same question again in this thread -> the same answer, with when;
    #   "is there a way to / can we" -> what we can do, never the opener;
    #   "not now / no thanks" after an offer -> closed, nothing queued;
    #   a short follow-up -> the previous turn's lane and subject.
    if referent_decision is None and not _armed(body):
        _sm_raw, _sm_reason = None, ''
        try:
            from . import smarter as _sm
            _tr = _sm.thread_replay(text, history)
            if _tr:
                _sm_raw, _sm_reason = {'success': True, 'action': 'answer', 'reply': _tr['reply'],
                                       'followups': _tr.get('followups') or [], 'offer_deck': False, 'deck_angle': None}, 'thread_replay'
            elif _sm.capability_ask(text) and not _reading_overrules(reading, 'capability'):
                _cap = _sm.capability_ask(text)
                _sm_raw, _sm_reason = {'success': True, 'action': 'answer', 'reply': _cap['reply'], 'followups': _cap['followups'],
                                       'offer_deck': False, 'deck_angle': None}, 'capability'
            elif _sm.offer_declined(text) and _last_turn_offered(history):
                _sm_raw, _sm_reason = {'success': True, 'action': 'answer', 'reply': _sm.DECLINE_REPLY, 'followups': [],
                                       'offer_deck': False, 'deck_angle': None}, 'offer_declined'
            else:
                _lane = _sm.followup_lane(text, history)
                if _lane == 'catalog_lookup':
                    from . import catalog_lane as _cl0
                    _subj = re.sub(r"^(?:and|also|what about|how about|any|same for|now|then|ok|okay)\s+", '', text.strip(' ?.!'), flags=re.I).strip(' ?.!')
                    _subj = re.sub(r"^(?:any|some)\s+", '', _subj, flags=re.I)
                    _q = text if re.search(r"\b(do we|do you|is there)\b", text, re.I) else f"do we have {_subj} profiles?"
                    _c0 = _cl0.answer(_q, ctx)
                    if _c0:
                        _sm_raw, _sm_reason = _c0, 'catalog_lookup'
                elif _lane == 'status_lookup':
                    from . import status_lane as _stl0
                    _c0 = _stl0.answer(text, uname, host=host, user=user, history=history)
                    if _c0:
                        _sm_raw, _sm_reason = _c0, 'status_lookup'
        except Exception as e:
            _faults.note('service.audit_lanes', e)
        if _sm_raw:
            raw = _sm_raw
            decision = {'surface': 'analyze', 'mode': None, 'reason': _sm_reason, 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_sm_raw.get('followups') or [])
            try:
                host.ask_hint(route=_sm_reason, outcome='answered')
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: catalog
    # Catalog lane (2026-10-06, Jenna: speed). "do we have a profile for
    # X", "do you see X", "how big is the X audience" are lookups: the
    # corpus catalog answers them with no model call. Runs before any
    # client-forced surface, so a yes/no question sitting in the build
    # flow can never draft a build.
    if referent_decision is None and not _armed(body):
        try:
            from . import catalog_lane as _cl
            _cat = _cl.answer(text, ctx)
        except Exception as e:
            _faults.note('service.catalog_lane', e)
            _cat = None
        if _cat:
            raw = _cat
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'catalog_lookup', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_cat.get('followups') or [])
                if _cat.get('memory_confirm'):
                    raw['memory_confirm'] = _cat['memory_confirm']
            try:
                host.ask_hint(route='catalog_lookup', outcome='answered',
                              subject=_cat.get('subject'))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: sizing_fallback
    # Sizing lane (2026-10-14, Ben at Kartel; priced $100 on 2026-10-21).
    # How big is X, the size of these two data sets, the sample size of
    # X customers: quoted in priced_read.service_turn above. This
    # standing call is the fail-safe when classify missed (panel-self
    # still answers free). Never the panel line when an audience is named.
    if referent_decision is None and not _armed(body):
        try:
            from . import sizing_lane as _szl, guards as _gs
            _sz = _szl.answer(text, ctx, host=host, history=history, staff=_gs.is_staff(user))
        except Exception as e:
            _faults.note('service.sizing_lane', e)
            _sz = None
        if _sz:
            raw = _sz
            decision = {'surface': 'analyze', 'mode': None, 'reason': 'sizing', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_sz.get('followups') or [])
            try:
                host.ask_hint(route='sizing', outcome='answered', subject=str(_sz.get('subject') or ''))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: status
    # Status lane (2026-10-13, Carolyn: "I don't see No Reservations - only
    # Babylon 5" drew "Nothing came back"; "have not received an email yet"
    # drew a page analysis). An ask that says the reader cannot see, find
    # or has not received their work is answered from their jobs across
    # every product, their engine runs and the delivery records. No model.
    if referent_decision is None and not _armed(body):
        try:
            from . import status_lane as _stl
            from . import deliverables as _dlv0
            # a named artifact ("where is the howdy deck") belongs to the deliverable lane below
            _st_raw = (_stl.answer(text, uname, host=host, user=user, history=history)
                       if _stl.wants_status(text) and not _dlv0.parse(text) else None)
        except Exception as e:
            _faults.note('service.status_lane', e)
            _st_raw = None
        if _st_raw:
            raw = _st_raw
            decision = {'surface': 'analyze', 'mode': None, 'reason': 'status_lookup', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_st_raw.get('followups') or [])
            try:
                host.ask_hint(route='status_lookup', outcome='answered')
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: deliverable_lookup
    # Deliverable lookup lane (2026-10-09, Scott: "where do i find the
    # howdy deck created last night" ran as a read and failed; "no a pdf
    # was created. where is it" drew the open-page confirm). Where-is-my
    # deck / pdf / file is a lookup into what Prometheus produced for
    # the caller: decks, linked files, sent mail, the catalog. No model
    # call, never a read, never the page confirm.
    if referent_decision is None and not _armed(body):
        try:
            from . import deliverables as _dlv
            _dl_raw = (_dlv.answer(text, uname, user=user, ctx=ctx, history=history, host=host)
                       if _dlv.parse(text) else None)
        except Exception as e:
            _faults.note('service.deliverable_lane', e)
            _dl_raw = None
        if _dl_raw:
            raw = _dl_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'deliverable_lookup', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_dl_raw.get('followups') or [])
            try:
                host.ask_hint(route='deliverable_lookup', outcome='answered',
                              subject=_dl_raw.get('subject'))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: email_file
    # Email-me-this-file lane (2026-10-12, Debi: with the Austin Sprinz
    # profile open, "Email me this file" went to the build flow and came
    # back with an existing-match offer). A reader asking for their own
    # file is a command: the open profile's CSV (and its cut), or the
    # latest read in the thread, goes out through the house mail door.
    if referent_decision is None and not _armed(body):
        try:
            from . import send_file as _sf
            _sf_raw = (_sf.answer(text, uname, user=user, ctx=ctx, history=history, host=host)
                       if _sf.parse(text) else None)
        except Exception as e:
            _faults.note('service.send_file_lane', e)
            _sf_raw = None
        if _sf_raw:
            raw = _sf_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'email_file', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_sf_raw.get('followups') or [])
            try:
                host.ask_hint(route='email_file', outcome='answered' if _sf_raw.get('sent') else 'clarified',
                              subject=_sf_raw.get('subject'))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: brand_coverage
    # Brand coverage lane (2026-10-08). "Is Alexa measured in Crosswalk?",
    # "what would alexa and echo be listed under in the behavioral tab":
    # whether we carry a brand, its category, and what the open profile
    # shows for it, answered from Gen Pop and the profile with no model
    # call and never the open-page confirm.
    if referent_decision is None and not _armed(body):
        try:
            from . import brand_coverage as _bcov
            _bc_raw = _bcov.answer(text, ctx, host=host) if _bcov.parse(text) else None
        except Exception as e:
            _faults.note('service.brand_coverage_lane', e)
            _bc_raw = None
        if _bc_raw:
            raw = _bc_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'brand_coverage', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_bc_raw.get('followups') or [])
            try:
                host.ask_hint(route='brand_coverage', outcome='answered',
                              subject=_bc_raw.get('subject'))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: definitions
    # Drill-down lane (2026-10-06, Alexia's 27,559). A question that
    # names a number from a Digital Journey is a lookup into that
    # journey: answer from the breakdown the file holds, or build it once
    # and write it onto the page under the row. Never a fresh read that
    # cannot be checked, never the calm line.
    if referent_decision is None and not _armed(body):
        try:
            from . import drilldown as _dd
            _dd_raw = _dd.answer(text, uname, ctx=ctx, tid=tid) if (_dd.looks_like_drilldown(text) or _dd.looks_like_definition(text)) else None
        except Exception as e:
            _faults.note('service.drill_down_lane', e)
            _dd_raw = None
        if _dd_raw:
            raw = _dd_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'journey_drilldown', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_dd_raw.get('followups') or [])
                if _dd_raw.get('read_job_id'):
                    raw['read_job_id'] = _dd_raw['read_job_id']
            try:
                host.ask_hint(route='journey_drilldown', outcome='answered',
                              subject=_dd_raw.get('subject'))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # lane: capability
    # Capability questions (2026-10-02 audit). "Can I cut the existing
    # Apple TV+ profile by quarter (i.e., 2Q 2026)?" was split into two
    # builds named "I.e Can I Cut ..." and "2Q 2026 Can I Cut ...". A
    # question about what the product does gets the answer, not a
    # draft and never a batch.
    if referent_decision is None and not _armed(body):
        try:
            from . import guards as _g
            _cap = _g.capability_answer(text)
        except Exception as e:
            _faults.note('service.capability_gate', e)
            _cap = None
        if _cap:
            raw = {'success': True, 'action': 'answer',
                   'reply': _cap['reply'],
                   'followups': list(_cap.get('followups') or []),
                   'offer_deck': False, 'deck_angle': None}
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'capability_question', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_cap.get('followups') or [])
            try:
                host.ask_hint(route='capability_question', outcome='answered')
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw,
                          'analyze')
            return env, 200

    # lane: surface_decision
    # A client in the middle of its own armed step (a confirm chip, a
    # deck angle picker, a clarify answer) already knows the surface.
    # It names it; the server still gates and runs it.
    forced = str(body.get('surface') or '').strip().lower()
    # A client sitting in its build flow forces 'interpret' for every
    # message. A question is still a question (2026-10-06: "do we have
    # a profile for Ms. Rachel?" drafted a 77 second build that way).
    # With nothing armed, a question-shaped ask keeps the server's own
    # read of it.
    if forced == 'interpret' and referent_decision is None and not _armed(body):
        try:
            _own = understand.decide(text, has_ctx=has_ctx, open_tabs=open_tabs)
            if _own.get('surface') == 'analyze' and str(_own.get('reason') or '') in (
                    'question', 'subiq_lookup', 'capability_question', 'compare_open',
                    'data_open'):
                forced = ''
        except Exception as _fx:
            _faults.note('service', _fx)
    if referent_decision is not None:
        decision = referent_decision
    elif forced in _SURFACES:
        decision = {'surface': forced,
                    'mode': (str(body.get('mode') or '').strip().lower()
                             or None),
                    'reason': 'client_surface', 'client_hint': None}
    else:
        try:
            _outline = understand.prior_deck_outline(history)
        except Exception:
            _outline = False
        decision = understand.decide(
            text, has_ctx=has_ctx, mode=body.get('mode'),
            extra=body.get('extra'), open_tabs=open_tabs,
            deck_in_flight=bool(body.get('deck_in_flight')),
            prior_outline=_outline)
        # the Reader's confident intent picks the surface (2026-10-14)
        try:
            from . import reader as _rd2
            _steer = _rd2.steer(reading, decision, has_ctx=has_ctx)
            if _steer:
                decision, _bind = _steer
                if _bind and not body.get('bind_subject'):
                    body = dict(body, bind_subject=_bind)
        except Exception as e:
            _faults.note('service.reader_steer', e)
    # "Don't build a new profile. Answer this from ..." is answer mode,
    # whatever surface the widget sent (2026-10-13 audit, Emma).
    try:
        from . import smarter as _sm2
        if _sm2.negated_build(text) and referent_decision is None:
            if decision.get('surface') == 'interpret':
                decision = dict(decision, surface='analyze', reason='negated_build')
            _stripped = _sm2.strip_build_negation(text)
            if _stripped != text:
                text = _stripped   # the question itself reaches the answer path
                body = dict(body, text=text)
    except Exception as _fx:
        _faults.note('service', _fx)
    surface = decision['surface']
    try:
        host.ask_hint(route='prometheus/ask:' + surface)
    except Exception as _fx:
        _faults.note('service', _fx)

    gate_resp = _gate_for(surface, user)
    if gate_resp is not None:
        raw, status = _unpack(gate_resp)
        return envelope.wrap(raw, surface=surface, decision=decision,
                             thread_id=tid, via=via), status

    # lane: work_gate
    # The work line (Jenna 2026-10-21, Liz's QSR ask on the open
    # Paramount+ profile answered free): a put-together read on an open
    # file is a custom ask on every analyze lane. The offer first; on
    # Accept the charge lands here once and rides the confirm so no
    # lane charges again.
    if surface == 'analyze' and referent_decision is None:
        try:
            from . import priced_read as _prw
            _wg = _prw.work_gate(text, history, ctx, user, body, username=uname,
                                 consume=(host.consume_credit if host.has('consume_credit') else None))
        except Exception as _fx:
            _faults.note('service.work_gate', _fx)
            _wg = None
        if _wg:
            raw = _wg
            decision = dict(decision, reason=('custom_ask_offer' if _wg.get('panel_offer') else 'custom_ask_funds'))
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_wg.get('followups') or [])
                if _wg.get('panel_offer'):
                    raw['panel_offer'] = _wg['panel_offer']
            try:
                host.ask_hint(route='reasoned_metrics',
                              outcome=('panel_offer' if _wg.get('panel_offer') else 'panel_out_of_credits'),
                              subject=str(_wg.get('subject') or ''))
            except Exception as _fx:
                _faults.note('service', _fx)
            env = envelope.wrap(raw, surface='analyze', decision=decision, thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    try:
        if surface == 'analyze':
            resp = host.analyze_core(
                user, _analyze_body(body, text, history, decision, tid),
                text, history)
        elif surface == 'deck':
            resp = host.deck_core(user, _deck_body(body, text, history, ctx, tid))
        else:
            resp = host.interpret_core(
                user, _interpret_body(body, text, history, tid), text, history)
    except Exception as e:
        try:
            host.error_email('prometheus/ask:' + surface, e)
        except Exception as _fx:
            _faults.note('service', _fx)
        raw, status = host.calm_payload(), 200
    else:
        raw, status = _unpack(resp)
        if surface == 'analyze' and isinstance(raw, dict) and raw.get('action') == 'answer' and raw.get('reply'):
            raw = _after_answer(raw, text, uname, ctx)
        if (surface == 'analyze' and _client_surface == 'interpret'
                and isinstance(raw, dict)):
            # The widget is sitting in its build flow (an armed step, or a
            # stale one). That flow renders analyze results only in the
            # guidance shape, the same way the legacy interpret deflection
            # hands them back. Until 2026-10-21 this applied only to a
            # referent decision: Jay Patel's "What did the BookTok exposed
            # viewers watch on Prime Video in the 30 days after the film?"
            # started its read ("On it ...") but the widget, in its build
            # flow, rendered the success payload as an empty draft card
            # ("New profile, ready to run" / "Nothing is queued yet"), so
            # the promise was never seen and the question came back three
            # times.
            raw = _interpret_shape(raw)

    env = envelope.wrap(raw, surface=surface, decision=decision,
                        thread_id=tid, via=via)
    _persist_turn(persist, uname, tid, history, text, env, raw, surface)
    return env, status


_META_RX = re.compile(
    r"^\s*(?:hey|hi|hello|yo|um|so)?[,.!\s]*(?:are you (?:not |still |even )?(?:working|there|alive|on|awake|broken|down|ok|okay)|"
    r"you (?:there|alive|ok|okay|working)|is (?:this|anyone|anybody|it) (?:thing )?(?:working|there|on|broken)|"
    r"(?:anyone|anybody) (?:there|home)|hello|is this (?:still )?working|does this (?:still )?work|"
    r"why (?:are you|aren't you|arent you) (?:not )?(?:answering|responding|working)|what is going on|what's going on|wtf|"
    r"did you (?:get|see) (?:that|my (?:question|message)))[\s?!.]*$", re.I)


def meta_check(text):
    """True for a message about whether Prometheus is working at all."""
    t = ' '.join(str(text or '').split())
    return bool(t) and len(t) <= 80 and bool(_META_RX.match(t))


def last_user_question(history):
    """The most recent user turn that is a question or request, not a
    meta check or a one-word acknowledgement."""
    for t in reversed(history or []):
        if not isinstance(t, dict) or t.get('role') != 'user':
            continue
        txt = ' '.join(str(t.get('text') or '').split())
        if txt and not meta_check(txt) and len(txt.split()) >= 3:
            return txt
    return ''


def _standing_answer(text):
    """Two answers that come before any executor or lane (2026-10-14):
    behavior with no digital trace (linear TV, in-store, foot traffic,
    radio) is declined with the nearest measurable read instead of being
    answered with the digital numbers as if they were the same thing;
    and a question about where the data comes from gets the one plain
    first-party answer. Returns a raw reply or None."""
    try:
        import prometheus_analysis as _pma
        gate = _pma.classify_quantifiability(text)
        if gate:
            reply, chips = _pma.build_not_quantifiable_reply(text, gate)
            try:
                host.ask_hint(route='quantifiability_gate', outcome='declined_not_quantifiable')
            except Exception:
                pass
            return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips or [], 'offer_deck': False,
                    'deck_angle': None, 'not_quantifiable': gate.get('domain')}
    except Exception as _fx:
        _faults.note('service', _fx)
    try:
        from . import guards as _g
        if _g.is_provenance_question(text):
            try:
                host.ask_hint(route='provenance', outcome='answered')
            except Exception:
                pass
            return {'success': True, 'action': 'answer', 'reply': _g.PROVENANCE_REPLY, 'followups': [], 'offer_deck': False, 'deck_angle': None}
    except Exception as _fx:
        _faults.note('service', _fx)
    if re.match(r"^\s*(?:i will|i'll|ill|let me) name them[.! ]*$", text, re.I):
        return {'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                'reply': 'Go ahead. Name them here, one per line is fine, and I will check each one.'}
    try:
        # A roster ask ("all the influencers in your network", "which
        # brands do you cover") is the offer and the question back, before
        # the Reader spends a call on it (2026-10-15, Emmet at Kartel).
        from . import catalog_lane as _cl
        _ro = _cl.roster_ask(text)
        if _ro:
            try:
                host.ask_hint(route='roster_offer', outcome='answered')
            except Exception:
                pass
            _reply, _chips = _cl.roster_reply(*_ro)
            _out = {'success': True, 'action': 'answer', 'reply': _reply, 'followups': _chips,
                    'offer_deck': False, 'deck_angle': None, 'roster_ask': True}
            if _ro[2]:
                from . import talent_list_job as _tl
                _out['offer'] = _tl.offer(_cl._plural(_ro[0]), _ro[2], _ro[1])[2]
            return _out
    except Exception as _fx:
        _faults.note('service', _fx)
    return None


def _execute_reading(reading, text, ctx, history, user, uname, body, tid, via, persist, _client_surface):
    """Run the deterministic executor the Reader chose, or return None
    to let the lanes and surfaces proceed (they also see the reading)."""
    from . import reader as _rd
    intent = str(reading.get('intent') or '')
    conf = float(reading.get('confidence') or 0)
    if intent not in _rd.EXECUTABLE or conf < 0.6:
        return None
    from . import progress as _pg
    raw, reason = None, ''
    subs = _rd.subjects_of(reading)
    page = ''
    try:
        page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        page = ''
    if _rd.refers_to_page(reading) and page:
        subs = [page] + [x for x in subs if x.lower() not in ('<page>', 'this', 'that', 'it')]
    if intent == 'size':
        from . import sizing_lane as _szl, guards as _g
        if _g.is_sample_size_ask(text) or not subs or all(_szl.is_panel_noun(s) for s in subs):
            return None     # the panel itself ("how many panelists do you have"): the sample-size answer says 10 million
        _pg.say('Sizing ' + (' and '.join(subs[:3]) if subs else 'the audience'))
        try:
            from . import priced_read as _pr
            _ex = body.get('extra') if isinstance(body.get('extra'), dict) else {}
            _pc = _ex.get('panel_confirm') or body.get('panel_confirm')
            raw = _pr.service_turn(text, user=user, ctx=ctx, history=history,
                                   host=host, panel_confirm=_pc)
        except Exception:
            raw = None
        if raw is None:
            raw = _szl.answer(text, ctx, host=host, history=history, subjects=subs or None,
                              staff=_g.is_staff(user), reader_subjects=list((reading or {}).get('subjects') or []))
        reason = 'sizing'
    elif intent == 'exists':
        from . import catalog_lane as _cl
        subj = subs[0] if subs else ''
        if subj:
            _pg.say(f"Looking for {subj} in the library")
            raw = _cl.answer(f"do we have {subj}?", ctx)
            reason = 'catalog_lookup'
    elif intent == 'status':
        from . import status_lane as _stl
        _pg.say('Checking your runs and deliveries')
        raw = _stl.answer(text, uname, host=host, user=user, history=history)
        reason = 'status_lookup'
    elif intent == 'capability':
        from . import smarter as _sm
        cap = _sm.capability_ask(text)
        if not cap:
            cap = _rd.capability_reply(reading)
        if cap:
            raw = {'success': True, 'action': 'answer', 'reply': cap['reply'], 'followups': cap.get('followups') or [],
                   'offer_deck': False, 'deck_angle': None}
            reason = 'capability'
    elif intent == 'command':
        # a send order ('email me the Babylon 5 breakdown') is a file or a
        # summary we hold, never a build (2026-10-14, golden g079)
        from . import send_file as _sf
        if re.search(r"\b(e-?mail|send|mail|forward)\b", text, re.I) and (_sf.parse(text) or subs):
            _pg.say('Sending the file')
            raw = _sf.answer(text, uname, user=user, ctx=ctx, history=history, host=host)
            reason = 'email_file'
    elif intent == 'decline':
        from . import smarter as _sm
        if _last_turn_offered(history):
            raw = {'success': True, 'action': 'answer', 'reply': _sm.DECLINE_REPLY, 'followups': [], 'offer_deck': False, 'deck_angle': None}
            reason = 'offer_declined'
    if not raw:
        return None
    # restate when the reading was not sure (the reader corrects a
    # restatement instantly, and the correction feeds the bank)
    if conf < 0.75 and reading.get('restate') and raw.get('action') == 'answer' and raw.get('reply'):
        raw = dict(raw, reply=f"{reading['restate'].rstrip('.')}.\n\n{raw['reply']}")
    raw = dict(raw, reading={'intent': intent, 'confidence': conf, 'subjects': subs})
    decision = {'surface': 'analyze', 'mode': None, 'reason': 'reader:' + reason, 'client_hint': None}
    out = raw
    if _client_surface == 'interpret':
        out = _interpret_shape(raw)
        out['followups'] = list(raw.get('followups') or [])
        if raw.get('memory_confirm'):
            out['memory_confirm'] = raw['memory_confirm']
    try:
        host.ask_hint(route=reason, outcome='answered', subject=(subs[0] if subs else None))
    except Exception as _fx:
        _faults.note('service', _fx)
    env = envelope.wrap(out, surface='analyze', decision=decision, thread_id=tid, via=via)
    _persist_turn(persist, uname, tid, history, text, env, out, 'analyze')
    return env, 200


def _reading_overrules(reading, lane_intent):
    """A confident reading that names a different intent beats a phrasing
    lane (2026-10-14: 'can you build me a Nike profile' read build_profile
    at 0.9 and the capability lane still answered)."""
    try:
        return isinstance(reading, dict) and float(reading.get('confidence') or 0) >= 0.7 \
            and bool(reading.get('intent')) and str(reading.get('intent')) != lane_intent
    except Exception:
        return False


def _last_turn_offered(history):
    """The previous agent turn carried chips or a priced offer."""
    for turn in reversed(list(history or [])):
        if str(turn.get('role') or '') == 'user':
            continue
        meta = turn.get('meta') or {}
        txt = str(turn.get('text') or turn.get('content') or '')
        return bool(meta.get('options') or meta.get('offer') or re.search(r"\b(credits?|on approval|want me to|shall i|would you like)\b", txt, re.I))
    return False


def _after_answer(raw, text, uname, ctx):
    """Self-grade before send (2026-10-13 audit): a stated count that sits
    inside a published metric's family is replaced by the published count;
    the one most useful other read we hold on the subject rides as a chip.
    Never raises, never blocks."""
    try:
        from . import smarter as _sm
        subject = str((ctx or {}).get('subject') or raw.get('subject') or '') if isinstance(ctx, dict) else str(raw.get('subject') or '')
        if subject:
            new_reply, swaps = _sm.bind_to_published(raw.get('reply') or '', subject)
            if swaps:
                raw = dict(raw, reply=new_reply, bound_to_published=swaps)
                print(f"[prometheus] bound {len(swaps)} figure(s) to published measurements for {subject!r}")
            chip = _sm.adjacent_read(subject, uname, host=host, exclude_kind=str((ctx or {}).get('kind') or ''))
            if chip:
                fu = list(raw.get('followups') or [])
                if chip['label'] not in fu and len(fu) < 4:
                    raw = dict(raw, followups=fu + [chip['label']])
    except Exception as e:
        _faults.note('service.after_answer_step', e)
    return raw


_ARMED_BODY_KEYS = ('locked_sample_tu', 'locked_sample_avid', 'angle',
                    'confirm_open_screen', 'draft', 'step')
_THANKS_RX = re.compile(r'^(?:thanks?|thank\s+you|ty|cool|got\s+it|'
                        r'understood|noted|perfect|great)[.! ]*$', re.I)
_ORD_WORDS = {'one': 1, 'first': 1, 'the first': 1, 'the first one': 1,
              'two': 2, 'second': 2, 'the second': 2, 'the second one': 2,
              'three': 3, 'third': 3, 'the third': 3, 'the third one': 3,
              'four': 4, 'five': 5}


def _armed(body):
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    if any(extra.get(k) or body.get(k) for k in _ANALYZE_PASSTHROUGH):
        return True
    return any(body.get(k) for k in _ARMED_BODY_KEYS)


_READ_RUNNING_RX = re.compile(
    r"^\s*On it\b|\bthe read will land right here\b|\blands right here when it is ready\b|"
    r"\bwill confirm here when it is (?:ready|live)\b", re.I)


def _read_in_flight(prev):
    """True when the previous agent turn started a read, deck or build
    that is still running (its text is the 'On it' promise or it
    carries a job id)."""
    if not isinstance(prev, dict):
        return False
    meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
    if any(meta.get(k) or prev.get(k) for k in ('read_job_id', 'deck_job_id', 'bpiq_job_id',
                                                  'jiq_job_id', 'fw_job_id', 'aiq_job_id')):
        return True
    return bool(_READ_RUNNING_RX.search(str(prev.get('text') or '')))


def _last_agent_turn(history):
    for t in reversed(history or []):
        if isinstance(t, dict) and str(t.get('role') or '') == 'agent':
            return t
    return None


_UTILITY_CHIP_RX = re.compile(
    r'^(?:email\s+me|send\s+me|save|download|something\s+else|'
    r'cancel|no\b|none\b|skip|new\s+thread|start\s+over)', re.I)


def _option_labels(turn):
    """Choices the previous agent turn offered, only when that turn
    asked a question: a numbered list in its text first, else its
    envelope options minus utility chips (email me, something else)."""
    out = []
    if not isinstance(turn, dict):
        return out
    txt = str(turn.get('text') or '')
    if '?' not in txt:
        return out
    for m in re.finditer(r'(?m)^\s*(\d{1,2})[.)]\s+(.+?)\s*$', txt):
        out.append(m.group(2).strip())
    if out:
        return out
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    for o in (meta.get('options') or []):
        if isinstance(o, dict):
            lbl = str(o.get('send') or o.get('label') or '').strip()
        else:
            lbl = str(o or '').strip()
        if lbl and not _UTILITY_CHIP_RX.match(lbl):
            out.append(lbl)
    return out


_CONFIRM_Q_RX = re.compile(
    r'^\s*Do you mean (?:for )?(.+?)(?:,? or (.+?))?\s*\?\s*$', re.I | re.S)
_CONFIRM_ANY_RX = re.compile(
    r'^(?:both|either|either one|either is fine|whichever|any|any of them|'
    r'all|all of them|does ?n[o\']t matter|doesnt matter|same thing|'
    r'they are the same|same|yes|yep|yeah|that one|the first( one)?|'
    r'first|the second( one)?|second)[.! ]*$', re.I)
_CONFIRM_THE_ONE_RX = re.compile(r'^the\s+(.+?)\s+one[.! ]*$', re.I)


def _confirm_options(turn):
    """[{label, subject, cohort}] for a which-one confirm turn, from
    its envelope options first (they carry the bound subject), else
    parsed out of the question text."""
    out = []
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else {}
    for o in (mc.get('options') or []):
        if isinstance(o, dict) and o.get('label'):
            out.append({'label': str(o['label']), 'subject': str(o.get('subject') or o['label']),
                        'cohort': str(o.get('cohort') or '') or None})
    if not out:
        for o in (meta.get('options') or []):
            lbl = str((o.get('label') if isinstance(o, dict) else o) or '').strip()
            if lbl and not _UTILITY_CHIP_RX.match(lbl):
                out.append({'label': lbl, 'subject': lbl, 'cohort': None})
    if not out:
        m = _CONFIRM_Q_RX.match(str(turn.get('text') or ''))
        if m:
            for g in (m.group(1), m.group(2)):
                if g and g.strip():
                    out.append({'label': g.strip(), 'subject': g.strip(), 'cohort': None})
    return out


def _confirm_answer(text, history):
    """When the previous agent turn was a which-one confirm and this
    turn answers it, return {'question', 'subject', 'cohort', 'label'}:
    the ask that triggered the confirm plus the option picked. None
    when this turn is not such an answer."""
    hist = [t for t in (history or []) if isinstance(t, dict)]
    prev = _last_agent_turn(hist)
    if not prev or not _CONFIRM_Q_RX.match(str(prev.get('text') or '')):
        return None
    options = _confirm_options(prev)
    if not options:
        return None
    low = ' '.join(str(text or '').lower().split()).strip()
    if not low or len(low) > 60:
        return None
    try:
        from prometheus_memory import entity_fold
    except Exception:
        def entity_fold(x):
            return re.sub(r'[^a-z0-9]+', ' ', str(x or '').lower()).strip()
    pick = None
    for o in options:
        if low.strip(' .!') == o['label'].lower() \
                or entity_fold(low) == entity_fold(o['label']):
            pick = o
            break
    if pick is None:
        m = _CONFIRM_THE_ONE_RX.match(low)
        if m:
            key = entity_fold(m.group(1))
            for o in options:
                if key and (key in entity_fold(o['label'])
                            or key in o['label'].lower()):
                    pick = o
                    break
    if pick is None and _CONFIRM_ANY_RX.match(low):
        if re.match(r'^(?:the )?second', low) and len(options) > 1:
            pick = options[1]
        else:
            # both / either / yes / first: one entity under two
            # spellings is one option; otherwise the first offered.
            pick = options[0]
    if pick is None:
        return None
    # The ask that triggered the confirm: the memory_confirm question
    # on the turn, else the user turn right before it.
    meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
    mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else {}
    question = str(mc.get('question') or '').strip()
    if not question:
        idx = hist.index(prev)
        for t in reversed(hist[:idx]):
            if str(t.get('role') or '') == 'user' and str(t.get('text') or '').strip():
                question = str(t['text']).strip()
                break
    if not question:
        return None
    return {'question': question, 'subject': pick.get('subject') or pick['label'],
            'cohort': pick.get('cohort'), 'label': pick['label']}


def _pending_brief_waiting(uname, text):
    try:
        from . import pending_intake as _pi
        return bool(uname) and _pi.waiting(host.s3_client, host.bucket, uname, text)
    except Exception:
        return False


def _bare_reply(body, text, history, has_ctx, uname=''):
    """(rewrite_text, payload). rewrite_text is set when the bare reply
    resolves to an option the previous turn offered (the ask continues
    as that option). payload is the deterministic answer for a bare
    reply nothing was waiting on. (None, None) means not a bare reply
    or an armed step owns it."""
    from . import guards
    kind = guards.bare_reply_kind(text)
    if not kind or _armed(body):
        return None, None
    if kind == 'affirm' and _pending_brief_waiting(uname, text):
        return None, None   # a proposed brief is stashed for this reader; the flow's confirm step owns the yes (2026-10-13)
    if kind == 'affirm':
        try:
            from . import talent_list_job as _tl
            if _tl.last_offer(history):
                return None, None   # the talent-list offer owns a yes right after it (2026-10-15)
        except Exception as _fx:
            _faults.note('service.affirm_offer', _fx)
    prev = _last_agent_turn(history)
    options = _option_labels(prev)
    low = ' '.join(str(text or '').lower().split()).strip(' .!')
    if kind == 'number' and options:
        picks = []
        for tok in re.split(r'\s*(?:,|and|&)\s*', low):
            tok = tok.replace('option', '').strip()
            n = None
            if tok.isdigit():
                n = int(tok)
            elif tok in _ORD_WORDS:
                n = _ORD_WORDS[tok]
            elif tok in ('the last', 'the last one', 'last'):
                n = len(options)
            if n and 1 <= n <= len(options):
                picks.append(options[n - 1])
        if picks:
            return ', '.join(dict.fromkeys(picks)), None

    def _p(reply, chips=None):
        out = {'success': True, 'action': 'answer', 'reply': reply,
               'followups': list(chips or []), 'offer_deck': False,
               'deck_angle': None}
        return out

    if kind == 'affirm':
        if _THANKS_RX.match(low):
            return None, _p('Anytime. Ask me the next one when you are ready.')
        # A yes right after "On it" is the reader confirming a read that is
        # already running (2026-10-12, Debi: "yes" after the read started drew
        # "Nothing is waiting on a yes right now").
        if _read_in_flight(prev):
            return None, _p('The read is already running. It lands right here in a few '
                            'minutes, and you can keep asking in the meantime.')
        if options:
            return None, _p('Which one? ' + ' / '.join(options[:4]) + '. '
                            'Send the one you mean and I will run it.',
                            options[:4])
        if re.search(r'approv|go|run|ship|do it|proceed', low):
            return None, _p(
                'Nothing is waiting on an approval right now. If you were '
                'approving a brief, use the Approve & run button on its '
                'card, or name the run ("approve the run on Shannen '
                'Doherty") and I will start it. Otherwise tell me the '
                'audience or the question and I will take it from there.')
        return None, _p(
            'Nothing is waiting on a yes right now. Ask me the question '
            'or name the audience and I will run it.')
    if kind == 'negative':
        if re.search(r'cancel|stop|never ?mind|forget|scratch|skip', low):
            return None, _p('Closed. Ask me anything else when ready.')
        if options:
            return None, _p(
                'No problem. Which audience should I use instead? Name '
                'it here and I will run the same question on it.')
        return None, _p(
            'No problem. Tell me the audience or the question you want '
            'instead and I will run it.')
    if kind == 'none':
        return None, _p(
            'Noted. If that answers a brief, finish it on the card above. '
            'Otherwise, what would you like to run next?')
    # number with no options to map to
    return None, _p(
        'Which list is that answering? Tell me the choice in words and '
        'I will run it.')


def _interpret_shape(raw):
    """Analyze result -> the one payload the widget's build flow renders
    as a plain chat turn (no approval card): success False + guidance,
    reply on `error`, with the armed extras the flow knows how to arm
    (read job, memory confirm, priced read offer)."""
    if not isinstance(raw, dict) or not raw.get('success') \
            or not raw.get('reply'):
        return raw
    out = {'success': False, 'guidance': True, 'analysis_read': True,
           'error': str(raw['reply'])}
    for k in ('read_job_id', 'memory_confirm', 'panel_offer',
              'referent_clarify', 'file_link'):
        if raw.get(k):
            out[k] = raw[k]
    if raw.get('memory_confirm') or raw.get('panel_offer'):
        out['followups'] = [str(f) for f in (raw.get('followups') or []) if f]
    return out


def _persist_turn(persist, uname, tid, history, text, env, raw, surface):
    """Append the user turn and the agent turn to the thread. The
    which-ones question keeps its referent on the agent turn's meta so
    the names that come back merge into the question that asked."""
    # Every priced offer a lane produced is a pending item on the server,
    # whoever persists the thread (the dashboard client writes its own
    # turns, so this is the only record of the offer the server holds).
    # A typed approval binds to it (2026-10-22, Casey Pearson).
    try:
        if isinstance(raw, dict) and isinstance(raw.get('panel_offer'), dict) and uname:
            from . import pending_offers as _pend
            _pend.record(uname, tid, raw, text)
    except Exception as e:
        _faults.note('service.pending_offer_record', e)
    if not (persist and tid and env.get('text')):
        return
    try:
        turns = list(history)
        turns.append({'role': 'user', 'text': text, 'ts': _now()})
        meta = {'kind': env['kind'], 'surface': surface}
        if env.get('options'):
            meta['options'] = env['options']
        if env.get('job'):
            meta[env['job']['type'] + '_job_id'] = env['job']['id']
        if isinstance(raw, dict) and isinstance(
                raw.get('referent_clarify'), dict):
            meta['referent_clarify'] = raw['referent_clarify']
        if isinstance(raw, dict) and isinstance(raw.get('offer'), dict):
            meta['offer'] = raw['offer']     # the talent-list offer (2026-10-15)
        if isinstance(raw, dict) and isinstance(raw.get('rerun_confirm'), dict):
            meta['rerun_confirm'] = raw['rerun_confirm']   # the re-run card (I2, 2026-10-20)
        if isinstance(raw, dict) and raw.get('subject'):
            meta.setdefault('subject', str(raw.get('subject'))[:120])
        if isinstance(raw, dict) and isinstance(raw.get('panel_offer'), dict):
            meta['panel_offer'] = raw['panel_offer']
        if isinstance(raw, dict) and isinstance(raw.get('priced_paid'), dict):
            meta['priced_paid'] = raw['priced_paid']
        turns.append({'role': 'agent', 'text': env['text'],
                      'ts': _now(), 'meta': meta})
        save_thread(uname, tid, turns)
    except Exception as e:
        print(f"[prometheus] persist failed for {uname}: {e}")
    # Corpus catalog (2026-10-05): every figure this reply states is
    # banked under its subject, so the next answer or build on the
    # subject sees it. Off the request path; fail-safe.
    try:
        subj = _answer_subject(env, raw)
        if subj and env.get('text') and env.get('kind') not in ('clarify', 'question'):
            from migration import corpus_catalog as _cc
            _cc.record_answer_async(subj, env['text'], user=uname, thread_id=tid or '')
    except Exception as e:
        _faults.note('service.corpus_catalog_bank', e)


def _answer_subject(env, raw):
    """The subject a reply is about, from the decision, the ask hint,
    or the bound page."""
    for src in (env.get('decision') or {}, raw if isinstance(raw, dict) else {}):
        for k in ('subject', 'bind_subject', 'subject_name'):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    try:
        from flask import g as _g
        v = getattr(_g, '_pm_ask_subject', None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception as _fx:
        _faults.note('service', _fx)
    return ''


# ------------------------------------------------------------------- jobs

def _job_prefixes():
    out = []
    for cap, jtype in (('read_prefix', 'read'), ('deck_prefix', 'deck'),
                       ('bpiq_prefix', 'bpiq'), ('jiq_prefix', 'jiq'),
                       ('fw_prefix', 'fw'), ('aiq_prefix', 'aiq')):
        if host.has(cap):
            out.append((getattr(host, cap), jtype))
    try:
        from . import talent_list_job as _tl
        out.append((_tl.JOB_PREFIX, 'tlist'))     # the talent list (2026-10-15)
    except Exception:
        pass
    return out


def job_status(user, job_id, *, via='session'):
    """Look a job up across every job store. Returns (dict, status)."""
    jid = str(job_id or '').strip()
    if not jid or len(jid) > 64 or not all(c.isalnum() or c in '-_' for c in jid):
        return {'success': False, 'error': 'bad job id'}, 400
    s3 = host.s3_client
    for prefix, jtype in _job_prefixes():
        try:
            obj = s3.get_object(Bucket=host.bucket, Key=f'{prefix}{jid}.json')
            payload = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            continue
        if not host.job_owner_ok(payload.get('user'), user):
            return {'success': False, 'error': 'not your job'}, 403
        status = str(payload.get('status') or 'running').lower()
        out = {'success': True, 'job': {'id': jid, 'type': jtype,
                                        'status': status}}
        if status in ('done', 'complete', 'completed', 'ready', 'held'):
            # The status document is transport; the read (or the deck
            # link, or where the tool output landed) is the content.
            # Wrapping the document itself handed API callers the
            # user's own question as the result text (2026-10-02 RCA).
            content = seams.unwrap(seams.tag_job(dict(payload), jtype))
            out['result'] = envelope.wrap(
                content, surface=('deck' if jtype == 'deck' else 'analyze'),
                decision={'reason': 'job_result'}, via=via)
        elif status in ('error', 'failed'):
            out['result'] = {'kind': 'error', 'text': (
                payload.get('reply') or payload.get('error')
                or 'That one did not land. Send the question again and I will take it from there.')}
            if via == 'api_key':
                out['result']['text'] = envelope._INFRA_RX.sub(
                    '', out['result']['text']).strip()
        return out, 200
    return {'success': False, 'error': 'unknown job'}, 404
