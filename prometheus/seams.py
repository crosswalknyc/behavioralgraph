"""Typed envelopes and the one ``unwrap()`` for every Prometheus seam.

2026-10-02 RCA, program W3. Four producers hand dicts across process
or thread boundaries and, until now, every consumer guessed the
shape. The guesses failed in the same way each time: a consumer read
the transport wrapper as if it were the content (Babylon 5 intake
read ``{'success', 'data'}`` as the brief; the v1 jobs endpoint
wrapped a job status document as if it were the read).

The seams, each with a schema tag the producer stamps:

    pm.model.v1     model transport      {'success', 'data', 'model', ...}
    pm.envelope.v1  reply to a client    envelope.wrap() output
    pm.job.v1       background job doc   {'job_id', 'user', 'status', ...}
    pm.queue.v1     Render -> worker     the build spec / tool payload

``unwrap(obj)`` is the single function a consumer calls to get the
content out of any of them. It recognizes the tag first, then falls
back to shape for untagged legacy objects, and it unwraps Flask view
returns (a Response, or a ``(Response, status)`` tuple) so the answer
gate and tests read one way. A consumer that calls ``unwrap`` cannot
make the Babylon 5 mistake: a transport wrapper never comes back as
content.

``versions()`` lists every schema this build speaks; the contract
test builds every shape and runs every consumer against it.
"""
from __future__ import annotations

MODEL = 'pm.model.v1'
ENVELOPE = 'pm.envelope.v1'
JOB = 'pm.job.v1'
QUEUE = 'pm.queue.v1'

_JOB_TRANSPORT_KEYS = frozenset((
    'schema', 'job_id', 'job_type', 'user', 'status', 'started_at',
    'finished_at', 'stage', 'stage_at', 'stages_ms', 'verify',
    'question', 'email', 'notify', 'thread_id'))

_JOB_LABELS = {
    'read': 'read', 'deck': 'deck',
    'bpiq': 'Brand Partnership IQ valuation',
    'jiq': 'Digital Journey IQ read',
    'fw': 'Flywheel read',
    'aiq': 'Attribution IQ setup',
}


def versions():
    return {'model': MODEL, 'envelope': ENVELOPE, 'job': JOB,
            'queue': QUEUE}


# --------------------------------------------------------------- stamps

def tag_model(result):
    """Stamp a model transport result. Idempotent, never raises."""
    if isinstance(result, dict):
        result.setdefault('schema', MODEL)
    return result


def tag_job(doc, job_type):
    """Stamp a background job status document."""
    if isinstance(doc, dict):
        doc.setdefault('schema', JOB)
        if job_type and not doc.get('job_type'):
            doc['job_type'] = str(job_type)
    return doc


def tag_queue(payload):
    if isinstance(payload, dict):
        payload.setdefault('schema', QUEUE)
    return payload


# ---------------------------------------------------------------- shape

def schema_of(obj):
    """The schema family of a dict ('model', 'envelope', 'job',
    'queue') from its tag, else from its shape, else ''."""
    if not isinstance(obj, dict):
        return ''
    tag = str(obj.get('schema') or '')
    if tag.startswith('pm.model.'):
        return 'model'
    if tag.startswith('pm.envelope.'):
        return 'envelope'
    if tag.startswith('pm.job.'):
        return 'job'
    if tag.startswith('pm.queue.'):
        return 'queue'
    keys = set(obj.keys())
    # Untagged legacy shapes, most specific first.
    if 'kind' in keys and 'surface' in keys and \
            ('text' in keys or 'raw' in keys):
        return 'envelope'
    if 'job_id' in keys and 'status' in keys and \
            ('payload' in keys or 'finished_at' in keys
             or 'started_at' in keys or 'stage' in keys):
        return 'job'
    if 'success' in keys and 'data' in keys and \
            not (keys & {'reply', 'kind', 'draft', 'options', 'question',
                         'guidance'}):
        return 'model'
    return ''


def is_transport(obj):
    """True when the dict is a wrapper, not content."""
    return schema_of(obj) in ('model', 'envelope', 'job')


# ------------------------------------------------------------- job docs

def job_result(doc):
    """The user-facing content of a job status document: the read
    itself for a read job, a reply + link for a deck, a reply + where
    it landed for a tool job. Transport keys never come back."""
    if not isinstance(doc, dict):
        return {}
    jtype = str(doc.get('job_type') or '').lower()
    status = str(doc.get('status') or '').lower()
    payload = doc.get('payload')
    if isinstance(payload, dict) and payload:
        # A read job (or any job that carries its content as payload).
        return payload
    body = {k: v for k, v in doc.items() if k not in _JOB_TRANSPORT_KEYS}
    if status in ('error', 'failed'):
        body.setdefault('success', False)
        body.setdefault('reply', str(
            doc.get('reply') or doc.get('error')
            or 'That one did not finish. Ask again and I will rerun it.'))
        return body
    if not body.get('reply'):
        if jtype == 'deck' or doc.get('url'):
            title = str(doc.get('title') or '').strip()
            n = doc.get('slides')
            body['reply'] = (f'Your deck{" " + repr(title) if title else ""}'
                             f' is ready'
                             + (f' ({n} slides)' if n else '') + '.')
        else:
            label = _JOB_LABELS.get(jtype, 'read')
            subj = str(doc.get('subject') or '').strip()
            body['reply'] = (f'Your {label}'
                             + (f' for {subj}' if subj else '')
                             + ' is ready in the dashboard.')
    body.setdefault('success', True)
    return body


# --------------------------------------------------------------- unwrap

def unwrap(obj):
    """Content out of any seam object.

    - ``(response, status)`` tuple        -> unwrap(response)
    - Flask Response (has get_json)       -> unwrap(json body)
    - model transport                     -> data dict ({} on failure)
    - reply envelope                      -> raw legacy payload when the
                                             envelope carries one, else
                                             the envelope itself
    - job status document                 -> job_result(doc)
    - queue payload / anything else       -> the object as given
    """
    if isinstance(obj, tuple) and obj:
        return unwrap(obj[0])
    getter = getattr(obj, 'get_json', None)
    if callable(getter):
        try:
            return unwrap(getter(silent=True))
        except TypeError:
            return unwrap(getter())
    if not isinstance(obj, dict):
        return obj
    fam = schema_of(obj)
    if fam == 'model':
        if obj.get('success'):
            data = obj.get('data')
            return data if isinstance(data, dict) else {}
        return {}
    if fam == 'envelope':
        raw = obj.get('raw')
        return raw if isinstance(raw, dict) else obj
    if fam == 'job':
        return job_result(obj)
    return obj


def status_of(obj, default=200):
    """HTTP status of a Flask view return (tuple or Response)."""
    if isinstance(obj, tuple):
        for part in obj[1:]:
            if isinstance(part, int):
                return part
        return default
    code = getattr(obj, 'status_code', None)
    return int(code) if isinstance(code, int) else default
