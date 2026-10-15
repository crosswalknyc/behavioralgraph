"""The v1 response contract for key callers of ``POST /api/prometheus/v1/ask``.

A partner codes against five kinds and a fixed shape::

    {"schema": "prometheus.ask.v1",
     "kind": "answer" | "clarify" | "job" | "pending" | "declined",
     "trace_id": "...", "conversation_id": "...",
     "answer":   {"text": "...", "facts": [...], "followups": [...]},
     "clarify":  {"question": "...", "options": [{"label", "send"}]},
     "job":      {"id", "status", "poll", "eta_seconds", "stage"},
     "pending":  {"retry_after_seconds", "text"},
     "declined": {"reason", "text", "next": {"action", "endpoint"}},
     "billing":  {"charged_usd", "balance_after_usd", "basis"},
     "replayed": false}

The widget's seven envelope kinds collapse to these five: a build
draft is ``declined`` with ``next.action = "build"`` (builds run on
``/api/v1/profiles/run``), guidance and nudges become ``declined`` or
``clarify`` depending on whether they carry options. Facts are numbers
read off the structured figure ledger the answer already carries, so a
partner never parses prose. Every string passes the partner belt
(``guards.scrub_partner_payload``) on the way out, and the JSON Schema in
``prometheus/schemas/ask.v1.json`` pins the shape.
"""
from __future__ import annotations

import re

SCHEMA = 'prometheus.ask.v1'
KINDS = ('answer', 'clarify', 'job', 'pending', 'declined')
DECLINE_REASONS = ('not_digital', 'no_base', 'scope', 'rate_limit', 'insufficient_funds', 'daily_cap', 'unsupported',
                   'unsafe_input', 'too_large', 'build_required', 'unknown_subject', 'error')
POLL_PREFIX = '/api/prometheus/v1/jobs/'
BUILD_ENDPOINT = '/api/v1/profiles/run'
TOP_UP_ENDPOINT = '/wallet'
HTTP_FOR_KIND = {'answer': 200, 'clarify': 200, 'job': 202, 'pending': 202, 'declined': 200}
HTTP_FOR_REASON = {'scope': 403, 'rate_limit': 429, 'insufficient_funds': 402, 'daily_cap': 402, 'too_large': 413,
                   'unsafe_input': 400, 'error': 500}

_UNIT_FOR_KIND = {'count': 'people', 'pct': 'percent', 'percent': 'percent', 'usd': 'usd', 'dollars': 'usd',
                  'index': 'index', 'x': 'ratio', 'ratio': 'ratio', 'minutes': 'minutes', 'hours': 'hours', 'days': 'days'}
_NUM_RX = re.compile(r'^-?\$?\s*[\d,]+(?:\.\d+)?\s*(%|x|m|k|b)?$', re.I)

__all__ = ['SCHEMA', 'KINDS', 'DECLINE_REASONS', 'to_v1', 'declined', 'pending', 'job', 'facts_of', 'http_status', 'legacy_view', 'is_v1']


def _num(v):
    """A number from a value that may be a string with commas, a % or a $."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace(',', '')
        m = _NUM_RX.match(v.strip())
        if not m:
            return None
        s = re.sub(r'[%$xXmMkKbB\s]', '', s)
        try:
            n = float(s)
        except Exception:
            return None
        suf = (m.group(1) or '').lower()
        n *= {'k': 1e3, 'm': 1e6, 'b': 1e9}.get(suf, 1)
        return int(n) if n == int(n) and suf not in ('%',) else n
    return None


def _unit_guess(label: str, value) -> str:
    low = str(label or '').lower()
    if isinstance(value, str) and value.strip().endswith('%'):
        return 'percent'
    if isinstance(value, str) and value.strip().startswith('$'):
        return 'usd'
    if any(w in low for w in ('share', 'pct', 'percent', 'rate', 'conversion', 'penetration', '%')):
        return 'percent'
    if 'index' in low:
        return 'index'
    if any(w in low for w in ('usd', 'dollar', 'revenue', 'value', 'spend', 'price', 'cost', '$')):
        return 'usd'
    return 'people'


def facts_of(raw: dict, *, subject: str = '', window: dict = None, product: str = '') -> list:
    """Structured facts from a reply payload: the figure ledger the answer
    carries, plus metrics / breakdown / kpi blocks when present. Numbers
    are numbers; provenance fields never leave the house."""
    raw = raw if isinstance(raw, dict) else {}
    subject = str(subject or raw.get('profile') or raw.get('subject') or '').strip()
    win = window if isinstance(window, dict) else (raw.get('window') if isinstance(raw.get('window'), dict) else None)
    product = product or str(raw.get('product') or '')
    out, seen = [], set()

    def _add(label, value, unit=None, extra=None):
        n = _num(value)
        if n is None:
            return
        label = str(label or 'figure').strip()[:120]
        key = (label.lower(), round(float(n), 6))
        if key in seen:
            return
        seen.add(key)
        f = {'label': label, 'value': n, 'unit': unit or _unit_guess(label, value)}
        if subject:
            f['subject'] = subject
        if win and (win.get('start') or win.get('end')):
            f['window'] = {'start': win.get('start'), 'end': win.get('end')}
        if product:
            f['product'] = product
        if isinstance(extra, dict):
            for k in ('as_of', 'base'):
                if extra.get(k) is not None:
                    f[k] = extra[k]
        out.append(f)

    for fig in raw.get('figures') or []:
        if isinstance(fig, dict) and fig.get('source') != 'model_typed':
            _add(fig.get('label'), fig.get('value'), _UNIT_FOR_KIND.get(str(fig.get('kind') or '').lower()))
    for m in raw.get('metrics') or []:
        if isinstance(m, dict):
            _add(m.get('name') or m.get('label'), m.get('value'), _UNIT_FOR_KIND.get(str(m.get('unit') or '').lower()) or m.get('unit'))
    for f in raw.get('facts') or []:
        if isinstance(f, dict):
            _add(f.get('label'), f.get('value'))
    bd = raw.get('breakdown')
    if isinstance(bd, dict):
        for k, v in bd.items():
            if isinstance(v, (int, float, str)):
                _add(k, v)
            elif isinstance(v, dict):
                for kk in ('count', 'accounts', 'value', 'pct'):
                    if kk in v:
                        _add(f"{k} ({kk})" if kk != 'value' else k, v[kk], 'percent' if kk == 'pct' else None)
    kp = raw.get('kpis')
    if isinstance(kp, dict):
        for k, v in kp.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                _add(str(k).replace('_', ' '), v, 'percent' if 'pct' in str(k) else None)
    ic = raw.get('incidence_check')
    if isinstance(ic, dict):
        for k in ('size', 'sample', 'projection', 'us_population', 'audience'):
            if ic.get(k) is not None:
                _add(k.replace('_', ' '), ic.get(k))
    return out[:60]


def _options(env: dict) -> list:
    out = []
    for o in (env or {}).get('options') or []:
        if isinstance(o, dict) and o.get('label'):
            out.append({'label': str(o['label'])[:120], 'send': str(o.get('send') or o['label'])[:200]})
    return out[:12]


def declined(reason: str, text: str, *, next_action: dict = None, trace_id: str = None, conversation_id: str = None,
             billing: dict = None, retry_after: int = None) -> dict:
    reason = reason if reason in DECLINE_REASONS else 'unsupported'
    body = {'reason': reason, 'text': str(text or '').strip()}
    if next_action:
        body['next'] = next_action
    if retry_after is not None:
        body['retry_after_seconds'] = int(retry_after)
    return _frame('declined', trace_id, conversation_id, billing, declined=body)


def pending(text: str, *, retry_after: int = 30, trace_id: str = None, conversation_id: str = None, job_id: str = None,
            billing: dict = None) -> dict:
    body = {'retry_after_seconds': int(retry_after), 'text': str(text or '').strip()}
    if job_id:
        body['job_id'] = str(job_id)
        body['poll'] = POLL_PREFIX + str(job_id)
    return _frame('pending', trace_id, conversation_id, billing, pending=body)


def job(job_id: str, *, status: str = 'working', stage: str = '', eta_seconds: int = 120, trace_id: str = None,
        conversation_id: str = None, billing: dict = None) -> dict:
    body = {'id': str(job_id), 'status': status, 'poll': POLL_PREFIX + str(job_id), 'eta_seconds': int(eta_seconds)}
    if stage:
        body['stage'] = str(stage)[:120]
    return _frame('job', trace_id, conversation_id, billing, job=body)


def _frame(kind: str, trace_id, conversation_id, billing, **blocks) -> dict:
    out = {'schema': SCHEMA, 'kind': kind, 'trace_id': trace_id or None, 'conversation_id': conversation_id or None}
    out.update(blocks)
    out['billing'] = billing or {'charged_usd': 0.0, 'balance_after_usd': None, 'basis': 'metered'}
    out['replayed'] = False
    return out


def http_status(v1: dict) -> int:
    kind = (v1 or {}).get('kind')
    if kind == 'declined':
        return HTTP_FOR_REASON.get(((v1.get('declined') or {}).get('reason')), 200)
    return HTTP_FOR_KIND.get(kind, 200)


_JOB_STAGES = {'queued': 'lined up', 'claimed': 'starting', 'running': 'working', 'working': 'working', 'done': 'done',
               'complete': 'done', 'completed': 'done', 'ready': 'done', 'held': 'held', 'error': 'error', 'failed': 'error'}


def to_v1(env: dict, raw: dict = None, *, billing: dict = None, conversation_id: str = None, subject: str = '',
          window: dict = None) -> dict:
    """Map the internal envelope (``envelope.wrap``) to the v1 contract."""
    env = env if isinstance(env, dict) else {}
    raw = raw if isinstance(raw, dict) else (env.get('_raw') if isinstance(env.get('_raw'), dict) else {})
    kind = str(env.get('kind') or '')
    text = str(env.get('text') or '').strip()
    trace = env.get('trace_id')
    opts = _options(env)
    jobd = env.get('job') if isinstance(env.get('job'), dict) else None
    if kind == 'job' and jobd:
        status = _JOB_STAGES.get(str(jobd.get('status') or 'working').lower(), 'working')
        return job(jobd.get('id'), status=status, stage=text[:120] if text else '', trace_id=trace, conversation_id=conversation_id, billing=billing)
    if kind == 'draft':
        # a build is its own product: the partner runs it on the build endpoint
        return declined('build_required', text or 'This needs a profile build first.',
                        next_action={'action': 'build', 'endpoint': BUILD_ENDPOINT}, trace_id=trace, conversation_id=conversation_id, billing=billing)
    if kind == 'clarify' or (kind in ('guidance', 'nudge') and opts):
        return _frame('clarify', trace, conversation_id, billing, clarify={'question': text, 'options': opts})
    if kind in ('guidance', 'nudge') and raw.get('success') is False and not raw.get('reply'):
        return declined('error', 'That one did not finish. Ask again and it will rerun.', trace_id=trace, conversation_id=conversation_id, billing=billing)
    if kind in ('guidance', 'nudge'):
        if raw.get('no_funds'):
            return declined('insufficient_funds', text, next_action={'action': 'top_up', 'endpoint': TOP_UP_ENDPOINT},
                            trace_id=trace, conversation_id=conversation_id, billing=billing)
        return declined('unsupported', text, trace_id=trace, conversation_id=conversation_id, billing=billing)
    if kind == 'error' or (raw.get('success') is False and not raw.get('reply')) or (env.get('success') is False and not text):
        # an engine error never echoes its own text to a partner
        return declined('error', 'That one did not finish. Ask again and it will rerun.', trace_id=trace, conversation_id=conversation_id, billing=billing)
    if raw.get('held') or raw.get('pending'):
        return pending(text or 'Still working on it.', retry_after=int(raw.get('retry_after') or 30), trace_id=trace,
                       conversation_id=conversation_id, job_id=(jobd or {}).get('id'), billing=billing)
    followups = [o['label'] for o in opts][:6]
    answer = {'text': text, 'facts': facts_of(raw, subject=subject, window=window), 'followups': followups}
    return _frame('answer', trace, conversation_id, billing, answer=answer)


def is_v1(doc) -> bool:
    return isinstance(doc, dict) and doc.get('schema') == SCHEMA


def legacy_view(v1: dict) -> dict:
    """The legacy reply shape the ask log and the weekly reviews read,
    for a v1 response (the log classifies what the caller received; the
    contract itself is never rewritten)."""
    v1 = v1 if isinstance(v1, dict) else {}
    kind = str(v1.get('kind') or '')
    if kind == 'answer':
        a = v1.get('answer') or {}
        return {'success': True, 'action': 'answer', 'reply': str(a.get('text') or ''), 'followups': list(a.get('followups') or []),
                'figures': [{'label': f.get('label'), 'value': f.get('value'), 'kind': f.get('unit'), 'source': 'api_fact'} for f in (a.get('facts') or [])]}
    if kind == 'clarify':
        c = v1.get('clarify') or {}
        return {'success': True, 'action': 'clarify', 'question': str(c.get('question') or ''), 'options': list(c.get('options') or [])}
    if kind == 'job':
        j = v1.get('job') or {}
        return {'success': True, 'action': 'answer', 'read_job_id': j.get('id'), 'reply': f"Working on it ({j.get('status') or 'working'})."}
    if kind == 'pending':
        p = v1.get('pending') or {}
        return {'success': True, 'action': 'answer', 'reply': str(p.get('text') or ''), 'held': True}
    d = v1.get('declined') or {}
    reason = str(d.get('reason') or '')
    if reason == 'error':
        return {'success': False, 'error': str(d.get('text') or 'declined: error'), 'declined': reason}
    out = {'success': True, 'action': 'answer', 'reply': str(d.get('text') or ''), 'declined': reason, 'followups': []}
    if reason == 'insufficient_funds':
        out['no_funds'] = True
    return out


def log_view(payload, log_surface, env_text):
    """(is_v1, log_surface, env_text, payload_for_the_log) for the ask
    logger: a v1 response is classified through its legacy view."""
    if not is_v1(payload):
        return False, log_surface, env_text, payload
    text = str(((payload.get('answer') or {}).get('text')) or ((payload.get('declined') or {}).get('text')) or '')
    return True, 'analyze', text, legacy_view(payload)
