"""The "Your answer is ready" email goes out only for a finished answer
(Jenna 2026-11-19).

Jenna asked Prometheus to "Run a profile for Aiyana Lee". The ask ran
past the long-wait mark (prometheus.progress, 45 seconds), the build
started in the same turn, and that turn's reply ("Included with your
plan. Profile building: Aiyana Lee, the Total Universe with its Avid Fan
tier. I will email you when it is complete. ...") was mailed to her under
"Your answer is ready." Jenna: "I shouldnt have gotten this email ... my
answer wasnt ready".

A reply that says what will happen next is not an answer. The reader
sees that turn in the chat, and the build's own completion email is the
one mail a build sends. The same holds for every turn that waits on the
reader or on work: a brief for approval, a quote, a confirmation, a
clarifying question, a read or deck still running, the calm promise, a
refusal that says we will get back to them, and directions around the
dashboard.

``finished_answer(env)`` -> (True, 'answer') only when every one of
these holds, read off the envelope and the payload the lanes already
write (no new mark on any reply):

  * the envelope's kind is ``answer`` (never clarify, draft, job,
    guidance, nudge or error);
  * the payload is an answer payload with words in it;
  * the payload points at nothing still to come: no run, no job, no
    draft, no offer (``order_truth.has_artifact``), and none of the
    marks an order, a hold or a waiting turn carries;
  * the words are not a promise or a progress line
    (``promise_truth.promise_kind``, ``owes_follow_up``);
  * the turn is not a wayfinding answer;
  * the house classifier reads the reply as answered
    (``ask_outcome.classify``: never clarified, proposed, confirmed,
    faulted, empty or rerouted).

Everything else -> (False, why). Never raises: a failure inside the
check reads as "not finished", so a doubtful reply is never mailed.
A finished read, a count, a sizing, a comparison, a table or a
delivered file carries none of those marks and mails as before.

Regression: scripts/test_pm_answer_ready_mail.py.
"""
from __future__ import annotations

__all__ = ['finished_answer', 'reply_text_of']

# a turn that waits on the reader: a question, a confirmation, a quote, a pick, a guided step
_WAITING_KEYS = (
    'guidance', 'clarify', 'memory_confirm', 'referent_clarify', 'audience_confirm', 'options', 'nudge', 'no_funds',
    'panel_offer', 'offer', 'next_step', 'route_hint', 'rewrite_text', 'bpiq_wizard', 'bpiq_collect', 'fw_collect',
    'jiq_collect', 'aiq_collect', 'twin_collect', 'twin_confirm_payload',
)
# a turn that reports an order: started, in line, held, or not started
_ORDER_KEYS = ('auto_started', 'pending_position', 'order_state', 'not_started', 'held', 'held_ref', 'pending', 'job_failed')
# a turn that points the reader somewhere on the dashboard instead of answering with data
_WAYFINDING_KEYS = ('wayfinding', 'action_hint', 'open_view')


def reply_text_of(raw):
    """The words the reader was shown, from any of the answer shapes."""
    if not isinstance(raw, dict):
        return ''
    for k in ('reply', 'answer', 'message', 'text'):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _payload_of(env):
    for k in ('raw', '_raw'):
        v = env.get(k)
        if isinstance(v, dict):
            return v
    return None


def finished_answer(env):
    """(True, 'answer') when the envelope carries a finished answer the
    reader may be mailed; (False, why) for anything that waits on the
    reader or on work. Never raises."""
    try:
        if not isinstance(env, dict):
            return False, 'no envelope'
        kind = str(env.get('kind') or '')
        if kind != 'answer':
            return False, kind or 'no kind'
        raw = _payload_of(env)
        if raw is None:
            return False, 'no payload'
        if raw.get('success') is False or raw.get('action') not in (None, 'answer'):
            return False, 'not an answer payload'
        text = reply_text_of(raw)
        if not text:
            return False, 'no text'
        decision = env.get('decision') if isinstance(env.get('decision'), dict) else {}
        if any(raw.get(k) for k in _WAYFINDING_KEYS) or str(decision.get('reason') or '') == 'wayfinding':
            return False, 'directions'
        if any(raw.get(k) for k in _ORDER_KEYS):
            return False, 'order'
        if any(raw.get(k) for k in _WAITING_KEYS):
            return False, 'waiting on the reader'
        from . import order_truth as _ot
        if _ot.has_artifact(raw):
            return False, 'work still to come'
        from . import promise_truth as _pt
        if _pt.promise_kind(text) or _pt.owes_follow_up(text):
            return False, 'promise'
        from . import ask_outcome as _ao
        outcome, detail = _ao.classify('analyze', raw)
        if outcome != 'answered' or (isinstance(detail, dict) and detail.get('async')):
            return False, str(outcome or 'not answered')
        return True, 'answer'
    except Exception:
        return False, 'check failed'
