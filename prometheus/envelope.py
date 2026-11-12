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

from . import seams as _seams

_JOB_KEYS = (
    ('read_job_id', 'read'),
    ('deck_job_id', 'deck'),
    ('bpiq_job_id', 'bpiq'),
    ('jiq_job_id', 'jiq'),
    ('fw_job_id', 'fw'),
    ('aiq_job_id', 'aiq'),
    ('tlist_job_id', 'tlist'),
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
    # Nested legacy shapes the widget renders from their own block: the
    # sample check and guided discovery (2026-10-13; an API caller saw
    # "Nothing came back" for a sample check that had answered).
    for k in ('incidence_check', 'discovery'):
        block = raw.get(k)
        if isinstance(block, dict):
            v = block.get('message')
            if isinstance(v, str) and v.strip():
                return v.strip()
    err = raw.get('error')
    if isinstance(err, str) and err.strip():
        return err.strip()
    return ''


def wrap(raw, *, surface, decision, thread_id=None, via='session'):
    raw = raw if isinstance(raw, dict) else {}
    # The one exit every reply passes (2026-10-05, Jenna): no method
    # language reaches a reader. Fail-safe.
    try:
        from . import guards as _g
        _g.scrub_method_language_payload(raw)
    except Exception:
        pass
    # The door rule is about decks (2026-11-11): a chat reply is never run
    # through it, so a title, a brand or a person's name is never rewritten.
    # A reply that IS deck copy (an outline typed into the chat) still is.
    try:
        from . import door_words as _dw
        _dw.scrub_payload(raw)
    except Exception:
        pass
    # A promise has a record (2026-11-11, prometheus.promise_truth): a calm
    # reply with no job and no run behind it gets the held record the ops
    # watch and the daily digest read. Fail-safe; the reply always ships.
    try:
        from . import promise_truth as _ptr
        _ptr.back_calm(raw, thread_id=thread_id or '')
    except Exception:
        pass
    # A people count never passes the US population or the audience it is
    # part of (2026-11-11: "about 392,312,555 insurance-active people" on an
    # audience of 8,977,404). Corrected in place to the figure the answer
    # carries, or the sentence goes; the reply always ships. Fail-safe.
    try:
        from . import people_bounds as _pb
        _pb.guard_payload(raw)
    except Exception:
        pass
    # A partnership's dollar value is the Brand Partnership valuation's to
    # state (Jenna 2026-11-11): an answer written in the request that
    # still carries one loses those lines and points at the valuation.
    if surface == 'analyze':
        try:
            from . import partnership_value as _pvg
            _q = ''
            try:
                from flask import g as _g_pv
                _q = str(getattr(_g_pv, '_pm_ask_text', '') or '')
            except Exception:
                _q = ''
            _pvg.guard_payload(raw, decision=decision, question=_q)
        except Exception:
            pass
    # Reply shape (2026-10-06): chat answers read as answers, not
    # reports (no capital-letter headers, definitions out of the
    # sentence). Analyze surface only; briefs and guidance keep shape.
    if surface == 'analyze':
        try:
            from . import reply_shape as _rs
            _rs.shape_payload(raw)
        except Exception:
            pass
    job = _job_of(raw, surface)
    # the legacy brief rides as spec_draft / spec_drafts (2026-10-13
    # audit: a successful brief wrapped as kind 'error' with "Nothing
    # came back", which is what an API caller and the ask log saw)
    draft = (raw.get('draft') or raw.get('drafts') or raw.get('batch')
             or raw.get('spec_draft') or raw.get('spec_drafts'))
    text = _text_of(raw)
    if draft and not text:
        try:
            from . import ask_outcome as _ao
            text = _ao.reply_text(raw)
        except Exception:
            text = ''
    trace_id = ''
    try:
        from flask import g as _g
        trace_id = str(getattr(_g, '_pm_trace_id', '') or '')
    except Exception:
        trace_id = ''

    if job:
        kind = 'job'
        if not text:
            what = 'deck' if surface == 'deck' else 'read'
            text = f'Working on it. The {what} will land in this thread when it is ready.'
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
        # I4c (2026-10-20): "Nothing came back for that one" never ships as
        # the reply. With nothing to show, the one question that moves the
        # conversation (name the subject) goes out as a clarify.
        kind = 'clarify'
        if not text:
            try:
                from . import subject_rules as _sr
                text = _sr.clarify_for('')
            except Exception:
                text = 'Which subject should this be on? Name the show, brand or person and I will take it from there.'
            raw['reply'] = text
            raw['action'] = raw.get('action') or 'answer'
            raw.setdefault('followups', [])

    env = {
        'schema': _seams.ENVELOPE,
        'success': kind not in ('error',),
        'kind': kind,
        'text': text,
        'trace_id': trace_id or None,
        'options': _options_of(raw),
        'job': job,
        'draft': draft if isinstance(draft, (dict, list)) else None,
        'surface': surface,
        'decision': decision or {},
        'thread_id': thread_id,
    }
    if via == 'api_key':
        env['text'] = _INFRA_RX.sub('', env['text']).strip()
        env['_raw'] = raw          # the v1 contract reads facts off it, then drops it
    else:
        env['raw'] = raw
    # A guided flow that let this message through as a new question
    # (prometheus.intake_guard, Jenna 2026-11-05) gets its one chip
    # back on the reply that answers it: 'Back to the valuation', 'Back
    # to the flywheel'. The flow stays pending on the server at its
    # step. Fail-safe; a reply that is the flow's own never carries it.
    try:
        from . import intake_guard as _ig
        hit = _ig.pending_resume()
        if hit and not (raw.get('bpiq_wizard') or raw.get('bpiq_collect') or raw.get('fw_collect')
                        or raw.get('jiq_collect') or raw.get('aiq_collect') or raw.get('twin_collect')):
            _ig.attach_resume_chip(env, hit)
    except Exception:
        pass
    # A read that landed since the reader's last message, and that the
    # reader's copy of the thread does not show yet, gets one trailing
    # line ("Your <name> read landed above.") and a mark the widget pulls
    # the thread on (Jenna 2026-11-10). Never the read itself: the reply
    # to a question is the answer to that question. prometheus.read_landing.
    try:
        from . import read_landing as _rl
        _rl.attach_landed_note(env, raw, via=via)
    except Exception:
        pass
    return env
