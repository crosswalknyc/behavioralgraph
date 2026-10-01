"""The response contract every Prometheus client renders.

The legacy analyze and interpret cores return several JSON shapes that
grew up with the dashboard widget. The envelope normalizes them into
one typed message so a new client (standalone app, API consumer) only
ever learns one shape::

    {
      'success': bool,
      'kind': 'answer' | 'clarify' | 'draft' | 'job' | 'guidance'
              | 'nudge' | 'error',
      'text': str,                 # what to show the person
      'options': [{'label','send'}],  # quick-reply chips, may be empty
      'job': {'id', 'type', 'status_url'} | None,
      'draft': dict | None,        # approvable build brief(s)
      'surface': 'analyze' | 'interpret' | 'deck',
      'decision': {...},           # from understand.decide
      'thread_id': str | None,
      'raw': dict                  # legacy payload; session callers only
    }

``raw`` keeps the dashboard widget's existing renderers working during
the migration. It is stripped for API-key callers, along with a light
scrub of infrastructure words from ``text``.
"""
import re

_JOB_KEYS = (
    ('read_job_id', 'read'),
    ('deck_job_id', 'deck'),
    ('bpiq_job_id', 'bpiq'),
    ('jiq_job_id', 'jiq'),
    ('fw_job_id', 'fw'),
    ('aiq_job_id', 'aiq'),
    ('job_id', 'read'),
)

# Hard infrastructure words that must never reach an API consumer even
# if an upstream string slipped. Prose is already written for users;
# this is a belt, not a rewrite.
_INFRA_RX = re.compile(
    r'\b(hetzner|clickhouse|systemd|traceback|anthropic|claude|openai)\b'
    r'|/root/|finished_codes|\.py:', re.I)


def _options_of(raw):
    out = []
    for o in (raw.get('options') or []):
        if isinstance(o, dict):
            label = str(o.get('label') or '').strip()
            send = str(o.get('send') or label).strip()
            if label:
                out.append({'label': label, 'send': send})
        elif isinstance(o, str) and o.strip():
            out.append({'label': o.strip(), 'send': o.strip()})
    for f in (raw.get('followups') or []):
        if isinstance(f, str) and f.strip():
            out.append({'label': f.strip(), 'send': f.strip()})
    return out


def _job_of(raw, surface):
    for key, jtype in _JOB_KEYS:
        jid = raw.get(key)
        if jid:
            if key == 'job_id' and surface == 'deck':
                jtype = 'deck'
            return {'id': str(jid), 'type': jtype,
                    'status_url': f'/api/prometheus/v1/jobs/{jid}'}
    return None


def _text_of(raw):
    for k in ('reply', 'message', 'question', 'summary', 'text'):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    err = raw.get('error')
    if isinstance(err, str) and err.strip():
        return err.strip()
    return ''


def wrap(raw, *, surface, decision, thread_id=None, via='session'):
    raw = raw if isinstance(raw, dict) else {}
    job = _job_of(raw, surface)
    draft = raw.get('draft') or raw.get('drafts') or raw.get('batch')
    text = _text_of(raw)

    if job:
        kind = 'job'
        if not text:
            text = 'Working on it. The read will land in this thread when it is ready.'
    elif draft:
        kind = 'draft'
        if not text:
            text = 'Here is the brief. Approve it to start the build.'
    elif raw.get('guidance') or raw.get('no_funds'):
        kind = 'guidance'
    elif (raw.get('action') == 'clarify' or raw.get('clarify')
          or (raw.get('question') and not raw.get('reply'))):
        kind = 'clarify'
    elif raw.get('nudge'):
        kind = 'nudge'
    elif raw.get('success') and text:
        kind = 'answer'
    elif text:
        # Legacy interpret answers ride as success=False + error text.
        kind = 'answer' if raw.get('success') is not False or raw.get('reply') else 'guidance'
    else:
        kind = 'error'
        text = text or 'Nothing came back for that one. Try asking it another way.'

    env = {
        'success': kind not in ('error',),
        'kind': kind,
        'text': text,
        'options': _options_of(raw),
        'job': job,
        'draft': draft if isinstance(draft, (dict, list)) else None,
        'surface': surface,
        'decision': decision or {},
        'thread_id': thread_id,
    }
    if via == 'api_key':
        env['text'] = _INFRA_RX.sub('', env['text']).strip()
    else:
        env['raw'] = raw
    return env
