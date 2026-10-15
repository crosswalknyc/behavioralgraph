"""The key-caller path for ``/api/prometheus/v1``: what wraps ``service.ask``
when the caller authenticated with ``X-Crosswalk-API-Key``.

Order on every ask: scope -> size -> idempotency replay -> rate window ->
funds and the daily cap -> input safety -> conversation -> parameters ->
the one shared ``service.ask`` -> billing -> the v1 contract -> the
partner belt over every string -> the call row. The dashboard widget and
the app never come through here; they get the widget envelope from the
same ``service.ask``. One engine, two shapes.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from . import api_meter
from . import contract_v1 as C
from . import partner_keys as K
from . import service
from .host import host

__all__ = ['handle_ask', 'handle_job', 'handle_job_files', 'job_file', 'handle_usage', 'handle_health', 'conversation_thread_id', 'unsafe_input', 'wallet_room']

_CONV_RX = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$')
_UNSAFE_RX = re.compile(
    r'(?:ignore|disregard|forget)\s+(?:all\s+|the\s+|your\s+|any\s+)?(?:previous|prior|above|earlier|system)\s+(?:instructions?|prompts?|rules?)'
    r'|\bsystem prompt\b|\bdeveloper message\b|\byou are now\b.{0,40}\b(?:unrestricted|jailbroken|dan)\b'
    r'|<\s*/?\s*(?:system|assistant|instructions?)\s*>|\[\s*(?:INST|SYSTEM)\s*\]|\bBEGIN (?:UNTRUSTED|DATA)\b|\bEND (?:UNTRUSTED|DATA)\b'
    r'|\bprint (?:your|the) (?:system|hidden) (?:prompt|instructions)\b|\breveal (?:your|the) (?:prompt|instructions|rules)\b', re.I)
_WINDOW_RX = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_SCREEN_ACTIONS = frozenset(('open_view', 'open_screen', 'navigate', 'design_request', 'open_on_screen', 'switch_view'))
_FALLBACK_ANSWER = 'The figures for this one are in the facts list.'


def unsafe_input(text: str) -> bool:
    """Prompt-injection shapes a partner's text must not carry into a
    model call. Conservative: ordinary questions never match."""
    return bool(_UNSAFE_RX.search(str(text or '')))


def _username(user) -> str:
    return service.username_of(user)


def _conv_slug(cid: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '-', str(cid or '')).strip('-')[:64]


def conversation_thread_id(user: dict, conversation_id: str) -> str:
    """A thread id that is stable per (key, conversation id) and never
    collides with the owner's dashboard threads."""
    return f"api-{K.key_id(user)[:8]}-{_conv_slug(conversation_id)}"


def _window(body: dict):
    w = body.get('window')
    if not isinstance(w, dict):
        return None
    s, e = str(w.get('start') or '').strip(), str(w.get('end') or '').strip()
    if (s and not _WINDOW_RX.match(s)) or (e and not _WINDOW_RX.match(e)):
        return 'bad'
    return {'start': s or None, 'end': e or None} if (s or e) else None


def _unlimited(user: dict) -> bool:
    """An unlimited seat is never charged, so its wallet is never read,
    never gated and never shown (Jenna 2026-10-14: it can sit below zero
    and that means nothing)."""
    u = user or {}
    if str(u.get('role') or '').lower() == 'super_admin' or u.get('unlimited'):
        return True
    try:
        import wallet as _w
        if _w.is_unlimited(u):
            return True
    except Exception:
        pass
    try:
        if host.has('wallet_snapshot'):
            snap = host.wallet_snapshot(_username(u))
            if isinstance(snap, dict) and snap.get('unlimited'):
                return True
    except Exception:
        pass
    return False


def _balance(user: dict):
    """The wallet behind the seat, or None for an unlimited seat (no
    wallet applies) and when nothing can be read."""
    if _unlimited(user):
        return None
    try:
        if host.has('wallet_snapshot'):
            snap = host.wallet_snapshot(_username(user))
            if isinstance(snap, dict):
                for k in ('balance_usd', 'wallet_balance_usd', 'balance'):
                    if snap.get(k) is not None:
                        return float(snap[k])
            elif isinstance(snap, (int, float)):
                return None if float(snap) < 0 else float(snap)
    except Exception:
        pass
    try:
        v = (user or {}).get('wallet_balance_usd')
        return float(v) if v is not None else None
    except Exception:
        return None


def _funds_ok(user: dict) -> bool:
    """The same funds decision the dashboard makes, before any model call."""
    try:
        if host.has('funds_gate'):
            return host.funds_gate(user) is None
    except Exception:
        return True
    return True


UNSETTLED_WINDOW_S = 1800      # metered charges settle to the wallet when a session closes (30 min idle)


def _wallet_metered(user: dict) -> bool:
    """True when this owner's asks are metered against a wallet (not an
    unlimited seat, not a company pool without a wallet)."""
    if _unlimited(user):
        return False
    try:
        import pay_per_use as _ppu
        if not _ppu.billing_active(user):
            return False
    except Exception:
        pass
    return (user or {}).get('wallet_balance_usd') is not None


def wallet_room(user: dict, key_id: str) -> dict:
    """{'balance', 'unsettled', 'available', 'ok'}: the wallet never goes
    negative (Jenna 2026-10-14). One more ask needs the balance, net of
    charges not yet settled, to hold at least the reserve."""
    bal = _balance(user)
    if bal is None:
        return {'balance': None, 'unsettled': 0.0, 'available': None, 'ok': True}
    unsettled = api_meter.spent_since(key_id, UNSETTLED_WINDOW_S)
    avail = round(float(bal) - float(unsettled or 0.0), 4)
    return {'balance': float(bal), 'unsettled': round(float(unsettled or 0.0), 4), 'available': avail, 'ok': avail >= K.MIN_RESERVE_USD}


def _billing(user: dict, request_id: str, sandbox: bool, key_id: str = '') -> dict:
    charged = 0.0
    if not sandbox and request_id:
        try:
            import render_usage_log as _rul
            charged = float((_rul.request_charge(request_id) or {}).get('billed_usd') or 0.0)
        except Exception:
            charged = 0.0
    after = None
    if not sandbox:
        room = wallet_room(user, key_id) if key_id else {'available': _balance(user)}
        if room.get('available') is not None:
            after = max(0.0, round(float(room['available']) - charged, 2))
    return {'charged_usd': round(charged, 4), 'balance_after_usd': after, 'basis': 'metered'}


def _scrub(v1: dict) -> dict:
    from . import guards as _g
    _g.scrub_partner_payload(v1)
    ans = v1.get('answer')
    if isinstance(ans, dict) and not str(ans.get('text') or '').strip():
        ans['text'] = _FALLBACK_ANSWER if ans.get('facts') else ''
        if not ans['text']:
            v1.clear()
            v1.update(C.declined('error', 'That one did not come through cleanly. Ask again and it will rerun.'))
    for blk in ('clarify', 'declined', 'pending', 'job'):
        b = v1.get(blk)
        if isinstance(b, dict):
            for k in ('question', 'text', 'stage'):
                if k in b and not str(b.get(k) or '').strip():
                    b[k] = {'question': 'Which one do you mean?', 'text': 'Nothing more to add here.', 'stage': 'working'}[k]
    cl = v1.get('clarify')
    if isinstance(cl, dict) and isinstance(cl.get('options'), list):
        cl['options'] = [o for o in cl['options'] if isinstance(o, dict) and o.get('label') and not _g.partner_violations(str(o.get('send') or ''))]
    ans = v1.get('answer')
    if isinstance(ans, dict) and isinstance(ans.get('followups'), list):
        ans['followups'] = [f for f in ans['followups'] if f and not _g.partner_violations(str(f))]
    return v1


def _finish(v1: dict, *, key_id: str, owner: str, endpoint: str, t0: float, request_id: str = '', idem: str = '',
            conversation_id: str = '', trace_id: str = '', qhash: str = '') -> tuple:
    """Scrub, pick the HTTP status, record the call. Returns (v1, status, headers)."""
    v1 = _scrub(v1)
    if trace_id and not v1.get('trace_id'):
        v1['trace_id'] = trace_id
    status = C.http_status(v1)
    headers = {}
    dec = v1.get('declined') if isinstance(v1.get('declined'), dict) else {}
    if dec.get('retry_after_seconds'):
        headers['Retry-After'] = str(int(dec['retry_after_seconds']))
    ms = int((time.monotonic() - t0) * 1000)
    try:
        api_meter.record(key_id=key_id, owner=owner, endpoint=endpoint, kind=str(v1.get('kind') or ''), status=status, ms=ms,
                         trace_id=str(v1.get('trace_id') or ''), request_id=request_id, idempotency_key=idem,
                         conversation_id=conversation_id, charged_usd=float((v1.get('billing') or {}).get('charged_usd') or 0.0),
                         response=v1, qhash=qhash)
    except Exception:
        pass
    return v1, status, headers


def handle_ask(user: dict, body: dict, headers: dict = None, *, ask=None, raw_len: int = None) -> tuple:
    """(v1_response, http_status, extra_headers) for a key caller's ask."""
    ask = ask or service.ask
    headers = headers or {}
    body = dict(body) if isinstance(body, dict) else {}
    t0 = time.monotonic()
    kid, owner = K.key_id(user), _username(user)
    sandbox = K.is_sandbox(user)
    idem = str(headers.get('Idempotency-Key') or headers.get('idempotency-key') or '').strip()[:128]
    cid = str(body.get('conversation_id') or '').strip()
    trace_id = ''
    try:
        from flask import g as _g
        trace_id = str(getattr(_g, '_pm_trace_id', '') or '')
    except Exception:
        pass

    text = str(body.get('text') or '').strip()
    qh = api_meter.question_hash(f"{cid}|{text}")      # the same words in another conversation are another question

    def _out(v1, request_id=''):
        return _finish(v1, key_id=kid, owner=owner, endpoint='ask', t0=t0, request_id=request_id, idem=idem,
                       conversation_id=cid, trace_id=trace_id, qhash=qh)

    if not K.has_scope(user, 'ask'):
        return _out(C.declined('scope', f"This key cannot ask. Its scopes are {', '.join(K.scopes(user))}.", conversation_id=cid))
    size = raw_len if raw_len is not None else len(json.dumps(body, default=str))
    if size > K.REQUEST_BODY_MAX or len(text) > K.REQUEST_TEXT_MAX:
        return _out(C.declined('too_large', f"Keep the text under {K.REQUEST_TEXT_MAX:,} characters and the body under 64 KB.", conversation_id=cid))
    if cid and not _CONV_RX.match(cid):
        return _out(C.declined('unsupported', 'conversation_id is up to 64 characters: letters, digits, dot, colon, dash, underscore.', conversation_id=None))
    if not text:
        return _out(C.declined('unsupported', 'Send the question in "text".', conversation_id=cid))
    if idem:
        hit = api_meter.idempotent_replay(kid, idem, K.IDEMPOTENCY_TTL_S)
        if hit:
            replay = dict(hit[0])
            replay['replayed'] = True
            return replay, int(hit[1] or 200), {}
    # Loop safeties (Jenna 2026-10-14: "build in safeties to keep those dead
    # loops from draining a wallet"). The same question again inside ten
    # minutes replays the first answer free; the same question hammering
    # past the count is held, nothing charged.
    repeats = api_meter.repeat_count(kid, qh, K.REPEAT_WINDOW_S)
    if repeats >= K.REPEAT_HOLD_AFTER:
        v1 = C.declined('rate_limit', f"The same question has come in {repeats} times in the last {K.REPEAT_WINDOW_S // 60} minutes, "
                        f"which reads as a stuck loop. Nothing was charged. Try again in a few minutes, or change the question.",
                        retry_after=300, conversation_id=cid)
        v1['declined']['loop'] = True
        return _out(v1)
    if repeats:
        hit = api_meter.repeat_replay(kid, qh, K.REPEAT_WINDOW_S)
        if hit and isinstance(hit[0], dict) and hit[0].get('kind') in ('answer', 'clarify'):
            replay = dict(hit[0])
            replay['replayed'] = True
            replay['billing'] = {'charged_usd': 0.0, 'balance_after_usd': (replay.get('billing') or {}).get('balance_after_usd'), 'basis': 'metered'}
            # the repeat still counts toward the loop hold
            try:
                api_meter.record(key_id=kid, owner=owner, endpoint='ask', kind=str(replay.get('kind')), status=int(hit[1] or 200),
                                 ms=int((time.monotonic() - t0) * 1000), trace_id=trace_id, conversation_id=cid, charged_usd=0.0, qhash=qh)
            except Exception:
                pass
            return replay, int(hit[1] or 200), {}
    lim = K.limits(user)
    rate = api_meter.check_rate(kid, 'ask', lim['ask_per_minute'], lim['ask_per_day'])
    if not rate['allowed']:
        what = (f"{lim['ask_per_minute']} a minute" if lim['ask_per_minute'] else '') + \
               (f"{', ' if lim['ask_per_minute'] else ''}{lim['ask_per_day']} a day" if lim['ask_per_day'] else '')
        return _out(C.declined('rate_limit', f"Over the limit for this key ({what}). Try again shortly.",
                               retry_after=rate['retry_after'], conversation_id=cid))
    if not sandbox and not _unlimited(user):
        cap = K.daily_cap_usd(user)                 # 0 = none; the wallet's funds are the budget
        if cap > 0 and api_meter.spent_today(kid) >= cap:
            return _out(C.declined('daily_cap', f"This key reached its daily spend cap of ${cap:,.2f}. It resets at 00:00 UTC.",
                                   next_action={'action': 'raise_cap', 'endpoint': 'contact your Crosswalk admin'}, conversation_id=cid))
        if not _funds_ok(user):
            return _out(C.declined('insufficient_funds', 'The wallet behind this key has no funds for a read. Add funds and ask again.',
                                   next_action={'action': 'top_up', 'endpoint': C.TOP_UP_ENDPOINT}, conversation_id=cid))
        if _wallet_metered(user):
            room = wallet_room(user, kid)
            if not room['ok']:
                avail = room.get('available')
                return _out(C.declined('insufficient_funds',
                                       f"The wallet behind this key is at ${max(0.0, avail or 0.0):,.2f} after charges still settling; "
                                       f"it cannot go below zero. Add funds and ask again.",
                                       next_action={'action': 'top_up', 'endpoint': C.TOP_UP_ENDPOINT}, conversation_id=cid))
    if unsafe_input(text):
        return _out(C.declined('unsafe_input', 'That text cannot be run as written. Ask the question plainly.', conversation_id=cid))
    win = _window(body)
    if win == 'bad':
        return _out(C.declined('unsupported', 'window.start and window.end are YYYY-MM-DD.', conversation_id=cid))
    subject = str(body.get('subject') or '').strip()[:160]
    view = str(body.get('view') or '').strip().lower()[:40]
    base_key = str(body.get('base_key') or '').strip()[:300]

    # the shared door: a stable thread per conversation, parameters as page facts
    req = {'text': text, 'client': 'api', 'persist': bool(cid)}
    if cid:
        tid = conversation_thread_id(user, cid)
        try:
            service.ensure_thread(owner, tid, title=f"API: {cid}"[:48])
        except Exception:
            pass
        req['thread_id'] = tid
    else:
        req['history'] = []
    ctx = {'api': True}
    if subject:
        ctx['subject'] = subject
        req['bind_subject'] = subject
    if win:
        ctx['window'] = win
    if view:
        ctx['view_id'] = view
    if base_key:
        ctx['base_key'] = base_key
    if len(ctx) > 1:
        req['context'] = ctx
    for k in ('mode', 'surface', 'extra'):
        if body.get(k) is not None:
            req[k] = body[k]
    request_id = uuid.uuid4().hex[:12]
    try:
        from flask import g as _g
        _g._pm_ppu_request_id = request_id
    except Exception:
        pass
    env, _status = ask(user, req, via='api_key')
    raw = env.get('_raw') if isinstance(env, dict) else None
    if isinstance(raw, dict) and str(raw.get('action') or '') in _SCREEN_ACTIONS:
        v1 = C.declined('unsupported', 'That action needs the dashboard screen. Ask for the figure itself and it comes back here.',
                        conversation_id=cid, billing=_billing(user, request_id, sandbox, kid))
        return _out(v1, request_id)
    billing = _billing(user, request_id, sandbox, kid)
    v1 = C.to_v1(env, raw, billing=billing, conversation_id=cid, subject=subject, window=win)
    if v1.get('kind') == 'job':
        try:
            from . import webhooks as _wh
            _wh.register_for_key(user, v1['job']['id'], conversation_id=cid, request_url=str(body.get('webhook_url') or ''))
        except Exception:
            pass
    if isinstance(env, dict):
        env.pop('_raw', None)
    return _out(v1, request_id)


def handle_job(user: dict, job_id: str) -> tuple:
    """(v1, status, headers) for a key caller polling a job."""
    t0 = time.monotonic()
    kid, owner = K.key_id(user), _username(user)
    lim = K.limits(user)
    rate = api_meter.check_rate(kid, 'jobs', lim['jobs_per_minute'], lim['jobs_per_day'])
    if not rate['allowed']:
        return _finish(C.declined('rate_limit', 'Polling too often. Try again shortly.', retry_after=rate['retry_after']),
                       key_id=kid, owner=owner, endpoint='jobs', t0=t0)
    out, status = service.job_status(user, job_id, via='api_key')
    if status == 404 or not out.get('success'):
        v1 = C.declined('unsupported' if status != 403 else 'scope',
                        'No job with that id for this key.' if status == 404 else 'That job belongs to another key.')
        if status == 403:
            v1['declined']['reason'] = 'scope'
        return _finish(v1, key_id=kid, owner=owner, endpoint='jobs', t0=t0)
    jd = out.get('job') or {}
    st = str(jd.get('status') or 'running').lower()
    sandbox = K.is_sandbox(user)
    doc, _kind = service.find_job_doc(job_id)
    doc = doc if isinstance(doc, dict) else {}
    rid = str(doc.get('request_id') or out.get('request_id') or jd.get('request_id') or '')
    jd = dict(jd, stage=str(doc.get('stage') or doc.get('progress') or jd.get('stage') or ''))
    billing = _billing(user, rid, sandbox, kid) if rid else {'charged_usd': 0.0, 'balance_after_usd': (None if sandbox else _balance(user)), 'basis': 'metered'}
    if st in ('done', 'complete', 'completed', 'ready'):
        res = out.get('result') or {}
        v1 = C.to_v1(res, res.get('_raw') if isinstance(res, dict) else None, billing=billing)
        if v1.get('kind') == 'job':          # a finished job never reads as a job
            v1 = C.to_v1(dict(res, kind='answer', job=None), res.get('_raw'), billing=billing)
    elif st == 'held':
        v1 = C.pending('Held for a moment on our side. Poll again shortly.', retry_after=120, job_id=job_id, billing=billing)
    elif st in ('error', 'failed'):
        v1 = C.declined('error', 'That one did not finish. Ask again and it will rerun.', billing=billing)
    elif st == 'timed_out':
        v1 = C.declined('error', 'That one ran out of time. Ask again and it will rerun.', billing=billing)
    else:
        v1 = C.job(job_id, status='working' if st != 'queued' else 'queued', stage=str(jd.get('stage') or ''), eta_seconds=120, billing=billing)
    return _finish(v1, key_id=kid, owner=owner, endpoint='jobs', t0=t0)


def handle_usage(user: dict, args: dict) -> tuple:
    import datetime as _dt
    today = _dt.datetime.utcnow().date()
    try:
        d_to = _dt.date.fromisoformat(str(args.get('to') or today.isoformat()))
        d_from = _dt.date.fromisoformat(str(args.get('from') or (d_to - _dt.timedelta(days=29)).isoformat()))
    except Exception:
        return {'error': 'from and to are YYYY-MM-DD'}, 400
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    if (d_to - d_from).days > 366:
        return {'error': 'a usage window is at most a year'}, 400
    rows = api_meter.usage(K.key_id(user), d_from.isoformat(), d_to.isoformat())
    days = {}
    for r in rows:
        d = days.setdefault(r['day'], {'day': r['day'], 'calls': 0, 'charged_usd': 0.0, 'errors': 0, 'by_kind': {}})
        d['calls'] += r['calls']
        d['charged_usd'] = round(d['charged_usd'] + r['charged_usd'], 6)
        d['errors'] += r.get('errors', 0)
        d['by_kind'][r['kind'] or 'answer'] = r['calls']
    total = round(sum(d['charged_usd'] for d in days.values()), 6)
    sandbox = K.is_sandbox(user)
    return {'schema': 'prometheus.usage.v1', 'key_id': K.key_id(user), 'from': d_from.isoformat(), 'to': d_to.isoformat(),
            'days': [days[k] for k in sorted(days)], 'total_calls': sum(d['calls'] for d in days.values()),
            'total_charged_usd': total, 'balance_usd': (None if sandbox else _balance(user)), 'basis': 'metered',
            'policy': {k: v for k, v in K.policy_view(user).items() if k in ('scopes', 'limits', 'daily_cap_usd', 'webhook_url')}}, 200


def handle_health() -> tuple:
    """Dependency checks a partner can poll, with plain names only."""
    checks = {}
    try:
        from .host import host as _h
        s3 = _h.s3_client if _h.has('s3_client') else None
        if s3 is not None:
            t = time.monotonic()
            s3.head_bucket(Bucket=_h.bucket)
            checks['storage'] = {'status': 'ok', 'ms': int((time.monotonic() - t) * 1000)}
        else:
            checks['storage'] = {'status': 'degraded'}
    except Exception:
        checks['storage'] = {'status': 'down'}
    try:
        import os as _os
        checks['analysis'] = {'status': 'ok' if (_os.environ.get('ANTHROPIC_API_KEY') or _os.environ.get('CLAUDE_API_KEY')) else 'degraded'}
    except Exception:
        checks['analysis'] = {'status': 'degraded'}
    try:
        from . import store as _st
        if _st.enabled():
            _st._exec('SELECT 1')
            checks['records'] = {'status': 'ok'}
        else:
            checks['records'] = {'status': 'degraded'}
    except Exception:
        checks['records'] = {'status': 'down'}
    worst = 'ok'
    for c in checks.values():
        if c['status'] == 'down':
            worst = 'down'
        elif c['status'] == 'degraded' and worst == 'ok':
            worst = 'degraded'
    return {'schema': 'prometheus.health.v1', 'status': worst, 'checks': checks}, (200 if worst != 'down' else 503)


# ------------------------------------------------------------------ files
FILES_PATH = '/api/prometheus/v1/jobs/{job}/files/{name}'
_FILE_TYPES = {'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'csv': 'text/csv',
               'pdf': 'application/pdf', 'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


def _job_for_files(user, job_id):
    """(doc, kind) when the job exists, is terminal and belongs to this key; else (None, reason)."""
    doc, kind = service.find_job_doc(job_id)
    if not isinstance(doc, dict):
        return None, 'unknown'
    try:
        if not host.job_owner_ok(doc.get('user'), user):
            return None, 'scope'
    except Exception:
        return None, 'scope'
    if str(doc.get('status') or '').lower() not in ('done', 'complete', 'completed', 'ready'):
        return None, 'not_done'
    return doc, kind


def _journey_files(doc):
    """The three files a finished journey ships with, built from the stored payload."""
    from prometheus.journey_delivery import load_payload
    from migration import journey_iq as _jiq
    from migration import journey_pdf as _jp
    key = str(doc.get('s3_key') or doc.get('result_key') or '')
    if not key:
        return []
    payload = load_payload(host.s3_client, key, host.bucket)
    out = [(_jiq.breakdown_filename(payload, 'xlsx'), lambda: _jiq.breakdown_xlsx(payload)),
           (_jiq.breakdown_filename(payload, 'csv'), lambda: _jiq.breakdown_csv(payload).encode('utf-8')),
           (_jp.pdf_filename(payload), lambda: _jp.build_pdf(payload))]
    return out


def _deck_files(doc):
    fname = str(doc.get('filename') or '')
    key = str(doc.get('s3_key') or '')
    if not fname and key:
        fname = key.rsplit('/', 1)[-1]
    if not fname:
        return []
    if not key:
        return []

    def _read():
        return host.s3_client.get_object(Bucket=host.bucket, Key=key)['Body'].read()
    return [(fname, _read)]


def _files_for(doc, kind):
    try:
        if kind in ('jiq', 'journey'):
            return _journey_files(doc)
        if kind == 'deck':
            return _deck_files(doc)
    except Exception:
        return []
    return []


def handle_job_files(user, job_id):
    """(doc, status, headers): the files a finished job ships with, as API
    paths the same key downloads (no public links)."""
    t0 = time.monotonic()
    kid, owner = K.key_id(user), _username(user)
    doc, kind = _job_for_files(user, job_id)
    if doc is None:
        reason = kind
        v1 = C.declined('scope' if reason == 'scope' else 'unsupported',
                        {'unknown': 'No job with that id for this key.', 'scope': 'That job belongs to another key.',
                         'not_done': 'The files are ready when the job is done. Poll the job first.'}[reason])
        return _finish(v1, key_id=kid, owner=owner, endpoint='files', t0=t0)
    files = []
    for name, _make in _files_for(doc, kind):
        ext = name.rsplit('.', 1)[-1].lower()
        files.append({'name': name, 'type': _FILE_TYPES.get(ext, 'application/octet-stream'),
                      'url': FILES_PATH.format(job=job_id, name=name)})
    out = {'schema': 'prometheus.files.v1', 'job_id': str(job_id), 'files': files}
    if not files:
        out['note'] = 'This read ships no separate files; its numbers are in the answer.'
    try:
        api_meter.record(key_id=kid, owner=owner, endpoint='files', kind='files', status=200, ms=int((time.monotonic() - t0) * 1000))
    except Exception:
        pass
    return out, 200, {}


def job_file(user, job_id, name):
    """(bytes, content_type, filename) or (None, status, reason)."""
    doc, kind = _job_for_files(user, job_id)
    if doc is None:
        return None, (403 if kind == 'scope' else 404), kind
    for fname, make in _files_for(doc, kind):
        if fname == name:
            try:
                data = make()
            except Exception:
                return None, 500, 'build'
            ext = fname.rsplit('.', 1)[-1].lower()
            return data, _FILE_TYPES.get(ext, 'application/octet-stream'), fname
    return None, 404, 'unknown_file'
