"""The answer gate on every chat surface: the watch notice, the ask taxonomy, the held and repeat messages, and the response unwrap.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import threading
import traceback
from prometheus import faults as _faults
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import seams as _seams, cohort_scope as _cohort_scope, shared_memory as _shared

# <<moved>>
# <<block: 1>>
def _pm_watch_notify(username, question, payload, subject=None,
                     probe=False, via=None, surface=None, thread_id=None):
    """Email Jenna the question and the answer for every account, API asks included (marked).
    Off the request thread; the 'On it' placeholder is skipped, the finished read calls again.
    Probes (probe=True, a probe caller, a canary label) never mail. Never raises.
    A multi-step walk holds each turn and sends one email when it ends."""
    try:
        if probe or _C._pm_is_probe_user(username) or _C._pm_probe_caller():
            return
        if via is None and getattr(__import__('flask').g, '_pm_api_key_owner', None):
            via = 'api'
    except Exception as _fx:
        _faults.note('chat', _fx)
    tid = thread_id
    try:
        if not tid:
            from flask import g as _g, has_request_context
            if has_request_context():
                tid = getattr(_g, '_pm_thread_id', None)
    except Exception as _fx:
        _faults.note('chat', _fx)

    def _run():
        try:
            import prometheus_watch_notify as _pwn
            rec = _pwn.user_record(_H.load_users() or {}, username)
            _pwn.notify(username, rec, question, payload, subject, via=via,
                        surface=surface, thread_id=tid or '')
        except Exception:
            traceback.print_exc()
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 2>>
_PM_TAXONOMY_OVERRIDES = frozenset({
    'empty', 'faulted', 'clarified', 'clarified_repeat', 'proposed',
    'confirmed', 'rerouted'})


# <<block: 3>>
def _pm_swallow(where, exc=None, note=None):
    """The one way to swallow an exception on a request path
    (2026-10-02 RCA: `except Exception: pass` hid every model, shape,
    and transport fault behind a polite sentence). Prints the
    traceback and records a fault note on flask.g so the ask log
    carries it in `extra.faults`. Never raises."""
    try:
        traceback.print_exc()
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:
        from flask import g as _g
        faults = getattr(_g, '_pm_faults', None)
        if faults is None:
            faults = []
            _g._pm_faults = faults
        if len(faults) < 6:
            msg = str(where)[:40]
            if exc is not None:
                msg += f': {type(exc).__name__}'
            if note:
                msg += f' {str(note)[:60]}'
            faults.append(msg)
    except Exception as _fx:
        _faults.note('chat', _fx)


# <<block: 4>>
def _pm_ask_apply_taxonomy(surface, payload, status_code, history,
                           base_outcome, question, username):
    """Refine the legacy outcome with prometheus.ask_outcome and fire
    the ops alert for the states a user should never sit in. Returns
    (outcome, extra). Never raises."""
    extra = {}
    try:
        from flask import g as _g
        faults = getattr(_g, '_pm_faults', None)
        if faults:
            extra['faults'] = '; '.join(faults)[:200]
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:
        from prometheus import ask_outcome as _ao
        outcome, detail = _ao.classify(surface, payload, status_code,
                                       history, base_outcome)
        if outcome in _C._PM_TAXONOMY_OVERRIDES:
            final = outcome
        else:
            final = base_outcome or outcome
        extra['result'] = outcome
        for k, v in (detail or {}).items():
            extra[k] = v
        if outcome in _ao.ALERT_OUTCOMES:
            already = False
            try:
                from flask import g as _g2
                already = bool(getattr(_g2, '_pm_fault_alerted', False))
                _g2._pm_fault_alerted = True
            except Exception as _fx:
                _faults.note('chat', _fx)
            if not already:
                try:
                    _H._chatbot_error_email(
                        f'prometheus/{surface}',
                        RuntimeError(f'ask outcome {outcome}'),
                        username or 'unknown',
                        {'question': str(question or '')[:600],
                         'outcome': outcome,
                         'reply_sample': (detail or {}).get('sample') or
                         _ao.reply_text(payload)[:300],
                         'surface': surface,
                         'faults': extra.get('faults')},
                        'Reply classified ' + outcome
                        + ' by prometheus.ask_outcome (no traceback; '
                          'the route returned normally).')
                except Exception:
                    traceback.print_exc()
        return final, (extra or None)
    except Exception:
        traceback.print_exc()
        return base_outcome, (extra or None)


# <<block: 5>>
_PM_GATE_HELD_MESSAGE = (
    "I did not land a clean answer on that one. I am looking at it now "
    "and will email you the read.")


# <<block: 6>>
_PM_GATE_TASK_MESSAGE = __import__('prometheus.build_guards', fromlist=['TASK_MESSAGE']).TASK_MESSAGE


# <<block: 7>>
# A repeat never reads as a scold (Jenna 2026-10-21): the pick line names
# the choices and runs the one tapped; with nothing to pick from the calm
# promise ships and ops finish by email (prometheus.turn_gate.calm_hold).
_PM_GATE_REPEAT_MESSAGE = (
    "Name the one you mean in a few words and I will run it right away.")


# <<block: 8>>
_PM_GATE_REPEAT_PICK_MESSAGE = (
    "Pick one and I will run it right away: {options}.")


# <<block: 9>>
_PM_GATE_HELD_NEXT_MESSAGE = (
    "I did not land a clean answer on that one. Ask it again in a few "
    "words, or open the profile and ask from there. I am also looking "
    "at it and will email you the read.")


# <<block: 10>>
_PM_GATE_RETRY_BUDGET_S = 45


# <<block: 11>>
_PM_GATE_HELD = frozenset({'empty', 'faulted'})


# <<block: 12>>
def _pm_unpack_resp(resp):
    """(actual_response, status_code, payload, envelope) for a Flask
    view return. `envelope` is the v1 {kind, surface, raw} wrapper when
    the body carried one (payload is then the legacy raw body), else
    None."""
    status_code, payload, envelope = 200, None, None
    actual = resp
    if isinstance(resp, tuple) and resp:
        actual = resp[0]
        if len(resp) > 1 and isinstance(resp[1], int):
            status_code = resp[1]
    try:
        payload = actual.get_json(silent=True)
    except Exception:
        payload = None
    if _seams.schema_of(payload) == 'envelope' \
            and isinstance(payload.get('raw'), dict):
        envelope = payload
        payload = _seams.unwrap(payload)
    return actual, status_code, payload, envelope


# <<block: 13>>
def _pm_draft_decision(payload):
    """The build decision a reply carries, if any."""
    if not isinstance(payload, dict):
        return ''
    for src in (payload, payload.get('draft'), payload.get('spec'),
                payload.get('data')):
        if isinstance(src, dict) and src.get('decision'):
            return str(src.get('decision') or '')
    return ''


# <<block: 14>>
def _pm_answer_gate(fn, args, kwargs, resp, payload, status_code,
                    surface, outcome, extra, question, history,
                    username, t0):
    """The only exit for a reply (2026-10-02 RCA). A reply the user
    should never receive (empty, transport text, scaffold labels, a
    build card for a task) does not leave this function as-is:

      1. empty / faulted on the answer surface: one retry inside the
         request budget, keeping the retried answer when it is clean;
      2. still held: the honest reply ("I will email you the read") plus
         the manual-look email so a human closes the loop;
      3. a new-build draft for an ask that reads as a task: the
         which-audience question instead of the card.

    Returns (resp, payload, status_code, outcome, extra). Never raises;
    on any internal failure the original reply passes through."""
    extra = dict(extra or {})
    try:
        from flask import g as _g
        from prometheus import ask_outcome as _ao
        if not isinstance(payload, dict):
            return resp, payload, status_code, outcome, (extra or None)
        _, _, _, envelope = _C._pm_unpack_resp(resp)
        result = str(extra.get('result') or outcome or '')
        # Async acknowledgements (a queued job) are answers.
        if (payload.get('job_id') or payload.get('read_job_id')
                or payload.get('pending')):
            return resp, payload, status_code, outcome, (extra or None)
        # Handoffs (action build_profile / route_hint) carry a blank
        # reply by contract: the widget runs the build interpret next.
        # Holding one replaces the handoff with the email promise and
        # the user never sees the build card (2026-10-05, Soulidified).
        if _ao.is_handoff(payload) and status_code < 400:
            return resp, payload, status_code, outcome, (extra or None)

        held = result in _C._PM_GATE_HELD
        # The same clarify twice in a row is the no-repeat contract
        # for every flow (the guided intakes catch it earlier in
        # _pm_intake_resolve; this covers profile builds, Subscriber
        # IQ and cuts).
        repeat = result == 'clarified_repeat'
        # A question asked again (turn_gate counted it) whose other path
        # came back with no answer: the calm promise and ops get the
        # problem, never a retry loop and never a scold (Jenna 2026-10-21).
        try:
            _n_rep = int(getattr(_g, '_pm_repeat_n', 0) or 0)
        except Exception:
            _n_rep = 0
        if _n_rep >= 1 and status_code < 400 and not payload.get('held'):
            _prev_txt = _ao.last_agent_text(history)
            _same = bool(_prev_txt) and _ao._norm(_prev_txt)[:160] == _ao._norm(_ao.reply_text(payload))[:160]
            if held or repeat or result in ('error',) or _same:
                _calm = _C._pm_calm_hold_response(question, history, username, envelope, extra,
                                               reason='asked again; the other path returned ' + (result or 'no answer'), n_rep=_n_rep)
                if _calm is not None:
                    return _calm
        mismatched = False
        try:
            from prometheus import guards as _pg
            if surface == 'interpret' and _C._pm_draft_decision(payload) \
                    in ('new_build',) and status_code < 400 \
                    and _pg.reads_as_task(question):
                mismatched = True
        except Exception as _fx:
            _faults.note('chat', _fx)
        if not held and not mismatched and not repeat:
            return resp, payload, status_code, outcome, (extra or None)

        # 1. one retry on the answer surface
        if held and surface == 'analyze' \
                and (time.time() - t0) < _C._PM_GATE_RETRY_BUDGET_S \
                and not getattr(_g, '_pm_gate_retried', False):
            _g._pm_gate_retried = True
            try:
                resp2 = fn(*args, **kwargs)
                _, sc2, pl2, _ = _C._pm_unpack_resp(resp2)
                oc2, det2 = _ao.classify(surface, pl2, sc2, history,
                                         outcome)
                if isinstance(pl2, dict) and oc2 not in _C._PM_GATE_HELD:
                    extra['gate'] = 'retry_ok'
                    extra['result'] = oc2
                    for k, v in (det2 or {}).items():
                        extra[k] = v
                    final = oc2 if oc2 in _C._PM_TAXONOMY_OVERRIDES \
                        else outcome
                    if final in _C._PM_GATE_HELD:
                        final = 'answered'
                    return resp2, pl2, sc2, final, extra
                extra['gate'] = 'retry_held'
            except Exception:
                _C._pm_swallow('answer-gate retry')

        # 2 / 3. honest reply, never the broken one
        if mismatched:
            new_payload = {'success': False, 'guidance': True,
                           'error': _C._PM_GATE_TASK_MESSAGE}
            new_status = 400
            outcome = 'mismatched'
            extra['gate'] = 'task_not_build'
            extra['result'] = 'mismatched'
            told = _C._PM_GATE_TASK_MESSAGE
            reason = ('The ask reads as an analysis task but the '
                      'interpret step drafted a new profile build; the '
                      'user was asked which audience instead of being '
                      'shown the build card.')
        elif repeat:
            new_payload = dict(payload)
            for k in ('answer', 'message', 'text', 'markdown', 'html',
                      'followups'):
                new_payload.pop(k, None)
            # Drop the collect flags so the widget does not re-arm the
            # same question.
            for k in [k for k in new_payload if str(k).endswith('_collect')]:
                new_payload.pop(k, None)
            _opts = _C._pm_gate_options(history)
            if _opts:
                told = _C._PM_GATE_REPEAT_PICK_MESSAGE.format(
                    options=' or '.join(_opts))
                new_payload['followups'] = list(_opts)
            else:
                # nothing to pick from: the same question is not asked a
                # second time; the calm promise ships and ops finish it
                _calm = _C._pm_calm_hold_response(question, history, username, envelope, extra,
                                               reason='the same clarifying question was about to go out twice; nothing to pick from')
                if _calm is not None:
                    return _calm
                told = _C._PM_GATE_REPEAT_MESSAGE
            new_payload['reply'] = told
            new_payload['success'] = True
            new_payload['gate'] = 'repeat'
            new_status = 200
            extra['gate'] = 'repeat'
            reason = ('The same clarifying question was about to go out '
                      'twice in a row; the user was offered the choices '
                      'as chips to pick from. Please read the thread in '
                      'case they are still stuck.')
        else:
            new_payload = dict(payload)
            for k in ('answer', 'message', 'text', 'markdown', 'html'):
                new_payload.pop(k, None)
            new_payload['reply'] = _C._PM_GATE_HELD_NEXT_MESSAGE
            new_payload['success'] = True
            new_payload['gate'] = 'held'
            _q_short = ' '.join(str(question or '').split())[:70]
            if _q_short:
                new_payload['followups'] = [f"Try again: {_q_short}"]
            new_status = 200
            extra['gate'] = extra.get('gate') or 'held'
            told = _C._PM_GATE_HELD_NEXT_MESSAGE
            reason = ('The reply came back ' + result + ' (blank, '
                      'transport text, or scaffold labels), so the user '
                      'was given a retry and told the read will come by '
                      'email. Please send it within two hours.')
        try:
            _H._prometheus_manual_look_email(question, told, reason,
                                             user_email=username or None)
        except Exception:
            traceback.print_exc()
        try:
            _C._pm_record_held_reply(username, question, told, reason,
                                  'repeat' if repeat else ('mismatched' if mismatched else 'held'))
        except Exception:
            traceback.print_exc()
        body = new_payload
        if envelope is not None:
            body = dict(envelope)
            body['raw'] = new_payload
        new_resp = jsonify(body)
        return (new_resp, new_status), new_payload, new_status, \
            outcome, extra
    except Exception:
        traceback.print_exc()
        return resp, payload, status_code, outcome, (extra or None)


__all__ = ['_pm_watch_notify', '_PM_TAXONOMY_OVERRIDES', '_pm_swallow', '_pm_ask_apply_taxonomy', '_PM_GATE_HELD_MESSAGE', '_PM_GATE_TASK_MESSAGE', '_PM_GATE_REPEAT_MESSAGE', '_PM_GATE_REPEAT_PICK_MESSAGE', '_PM_GATE_HELD_NEXT_MESSAGE', '_PM_GATE_RETRY_BUDGET_S', '_PM_GATE_HELD', '_pm_unpack_resp', '_pm_draft_decision', '_pm_answer_gate']
