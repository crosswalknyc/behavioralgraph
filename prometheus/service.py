"""``ask()``: the single entry point every Prometheus client calls.

    understand -> gate -> run the surface core -> persist -> envelope

The dashboard widget, the standalone app, and the API all land here
with the same request shape::

    {'text': str,                    # required
     'thread_id': str | None,        # defaults to the caller's active thread
     'history': [...] | None,        # a client may send its own; else loaded
     'context': {...} | None,        # page facts: open profile, cuts, tabs
     'mode': str | None,             # explicit analyze mode (chip)
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
import uuid
from datetime import datetime, timezone

from .host import host
from . import understand
from . import envelope

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
                except Exception:
                    pass
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


def list_threads(uname):
    idx = threads_index(uname)
    return {'active': idx.get('active'),
            'threads': sorted(idx.get('threads', []),
                              key=lambda t: str(t.get('updated') or ''),
                              reverse=True)}


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


def _analyze_body(body, text, history, decision):
    ctx = body.get('context')
    if ctx is None:
        ctx = body.get('page_context')
    out = {'text': text, 'history': history, 'page_context': ctx or None}
    mode = decision.get('mode') or body.get('mode')
    if mode:
        out['mode'] = mode
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    for k in _ANALYZE_PASSTHROUGH:
        v = extra.get(k, body.get(k))
        if v is not None:
            out[k] = v
    return out


def _interpret_body(body, text, history):
    out = {'text': text, 'history': history}
    for k in ('locked_sample_tu', 'locked_sample_avid'):
        if body.get(k) is not None:
            out[k] = body[k]
    return out


def ask(user, body, *, via='session'):
    """Run one ask end to end. Returns (envelope_dict, http_status)."""
    body = body if isinstance(body, dict) else {}
    text = str(body.get('text') or '').strip()
    uname = username_of(user)
    client = str(body.get('client') or '').strip().lower()
    persist = bool(body.get('persist', client != 'dashboard'))

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
    except Exception:
        pass

    decision = understand.decide(
        text, has_ctx=has_ctx, mode=body.get('mode'),
        extra=body.get('extra'), open_tabs=open_tabs,
        deck_in_flight=bool(body.get('deck_in_flight')))
    surface = decision['surface']
    try:
        host.ask_hint(route='prometheus/ask:' + surface)
    except Exception:
        pass

    gate_resp = _gate_for(surface, user)
    if gate_resp is not None:
        raw, status = _unpack(gate_resp)
        return envelope.wrap(raw, surface=surface, decision=decision,
                             thread_id=tid, via=via), status

    try:
        if surface == 'analyze':
            resp = host.analyze_core(
                user, _analyze_body(body, text, history, decision),
                text, history)
        elif surface == 'deck':
            resp = host.deck_core(user, {'text': text, 'history': history,
                                         'page_context': ctx or None})
        else:
            resp = host.interpret_core(
                user, _interpret_body(body, text, history), text, history)
    except Exception as e:
        try:
            host.error_email('prometheus/ask:' + surface, e)
        except Exception:
            pass
        raw, status = host.calm_payload(), 200
    else:
        raw, status = _unpack(resp)

    env = envelope.wrap(raw, surface=surface, decision=decision,
                        thread_id=tid, via=via)

    if persist and tid and env.get('text'):
        try:
            turns = list(history)
            turns.append({'role': 'user', 'text': text, 'ts': _now()})
            meta = {'kind': env['kind'], 'surface': surface}
            if env.get('options'):
                meta['options'] = env['options']
            if env.get('job'):
                meta[env['job']['type'] + '_job_id'] = env['job']['id']
            turns.append({'role': 'agent', 'text': env['text'],
                          'ts': _now(), 'meta': meta})
            save_thread(uname, tid, turns)
        except Exception as e:
            print(f"[prometheus] persist failed for {uname}: {e}")

    return env, status


# ------------------------------------------------------------------- jobs

def _job_prefixes():
    out = []
    for cap, jtype in (('read_prefix', 'read'), ('deck_prefix', 'deck'),
                       ('bpiq_prefix', 'bpiq'), ('jiq_prefix', 'jiq'),
                       ('fw_prefix', 'fw'), ('aiq_prefix', 'aiq')):
        if host.has(cap):
            out.append((getattr(host, cap), jtype))
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
        if status in ('done', 'complete', 'completed', 'ready'):
            out['result'] = envelope.wrap(
                payload, surface=('deck' if jtype == 'deck' else 'analyze'),
                decision={'reason': 'job_result'}, via=via)
        elif status in ('error', 'failed'):
            out['result'] = {'kind': 'error', 'text': (
                payload.get('reply') or payload.get('error')
                or 'That one did not finish. Ask again and I will rerun it.')}
            if via == 'api_key':
                out['result']['text'] = envelope._INFRA_RX.sub(
                    '', out['result']['text']).strip()
        return out, 200
    return {'success': False, 'error': 'unknown job'}, 404
