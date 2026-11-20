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
# this is a belt, not a rewrite. (stage 4: the list is the table's)
from .vocabulary import INFRA_RX as _INFRA_RX


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
    # a question with choices that rides as a block (the deck step's
    # did-you-mean): its question is the text (2026-11-12)
    try:
        from . import ask_outcome as _ao
        return _ao.choice_block(raw)[0]
    except Exception:
        return ''


def wrap(raw, *, surface, decision, thread_id=None, via='session'):
    raw = raw if isinstance(raw, dict) else {}
    # "Do not build anything" is honored for the whole ask (prometheus.no_build,
    # Cara Dorfman 2026-11-16): a draft, a quote, a promise of a read or a
    # question that leads to a build never leaves on such an ask. The lanes
    # that start work step aside first, so nothing is running behind the
    # line that goes out instead. In place, so the caller stores what was said.
    try:
        from . import no_build as _nb
        if _nb.active() and _nb.refuses(raw):
            _held = _nb.hold()
            raw.clear()
            raw.update(_held)
            surface = 'analyze'
            decision = dict(decision or {}, surface='analyze', reason='no_build')
    except Exception:
        pass
    # The one exit every reply passes (2026-10-05, Jenna; stage 4,
    # 2026-11-20: prometheus.exit.payload, one pipeline in one fixed
    # order): no method language, no Reader echo, no sample vocabulary, no
    # dashboard guesswork, the named-title line; the door rule on deck
    # copy only; a calm line gets its record; no people count above the US
    # or its base; on the analyze surface the partnership-value and
    # valuation-price guards and the reply shape; then no dash character
    # and no HH on any field. Each pass is fail-safe; the reply always ships.
    try:
        from . import exit as _exit
        _q = ''
        try:
            from flask import g as _g_pv
            _q = str(getattr(_g_pv, '_pm_ask_text', '') or '')
        except Exception:
            _q = ''
        _exit.payload(raw, surface=surface, decision=decision, thread_id=thread_id or '', question=_q)
    except Exception:
        pass
    job = _job_of(raw, surface)
    # the legacy brief rides as spec_draft / spec_drafts (2026-10-13
    # audit: a successful brief wrapped as kind 'error' with "Nothing
    # came back", which is what an API caller and the ask log saw)
    draft = (raw.get('draft') or raw.get('drafts') or raw.get('batch')
             or raw.get('spec_draft') or raw.get('spec_drafts'))
    if draft:
        # A seat the plan covers never reads a price on a brief (Jenna
        # 2026-11-08; Liz Huszarik read "(new build, $300)" in the
        # Prometheus app on 2026-11-17). The mark rides the payload so the
        # reply text, the key contract and the question copy agree.
        try:
            from . import ask_outcome as _ao_inc
            _ao_inc.stamp_included(raw)
        except Exception:
            pass
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
    # An ask that reached before the data floor and was moved onto the
    # corrected window says so first, with the dates used (Jenna
    # 2026-11-18, prometheus.data_floor): "No data can be pulled from
    # before January 1, 2021. The window starts January 1, 2021 and runs
    # to ...". Once per ask; a reply that already says it is left alone.
    try:
        from . import data_floor as _dfl
        _dfl.attach_note(env, raw, via=via)
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
