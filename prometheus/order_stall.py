"""A priced order that stalls is visible the same day (Jenna 2026-11-11).

Samantha Kolton's Brand Partnership valuation order was left for a
general read at its first step, asked one question twice in the same
words at its last, and never reached its quote. Nothing said so: the
ask log filed each turn as answered and the question copies read like
any other walk. Jenna: "these errors cannot happen."

At answer time this module looks at the reply that is about to go out
and the thread it goes into, and says where a priced or approval-gated
order stands:

    state   open      the order asked one of its steps
            quote     the order reached its quote (the recap, a confirm
                      card, a brief with its price)
            accepted  the reader accepted and it started
            declined / cancelled
            left      a general answer went out while the order was open
    flags   repeated_question                the same question, in the
                                             same words, twice in a row
            general_answer_while_order_open  the order stood aside for a
                                             question and stays open
            order_exited_without_quote       the order was open, this
                                             reply is not the order's,
                                             and nothing leads back

`stamp(extra, question, payload, history)` puts that on the ask-log
record (`extra['order']`) and on the payload the question copy is built
from; `subject_mark` and `order_block` are what the existing copy email
carries; `walk_flags` reads a finished walk. The weekly review lists the
orders that did not reach a quote from the same record.

No mail is sent from here and no recipient is named here. Pure.
"""
from __future__ import annotations

import re

FLAG_REPEAT = 'repeated_question'
FLAG_LEFT = 'general_answer_while_order_open'
FLAG_EXIT = 'order_exited_without_quote'
FLAG_NO_QUOTE = 'did_not_reach_a_quote'          # a finished walk (the email and the weekly review)

LABELS = {
    'brand_partnership': 'Brand Partnership valuation',
    'digital_journey': 'Digital Journey',
    'flywheel': 'Flywheel',
    'attribution': 'Attribution IQ campaign',
    'digital_twin': 'Digital Twin',
    'profile_iq': 'Profile build',
    'bulk_pull': 'Bulk profile list',
    'priced_read': 'Custom read',
    'deck': 'Deck',
}
STEP_LABELS = {
    'talent': 'the talent', 'campaign': 'the campaign', 'dates': 'the campaign dates', 'attr': 'the attribution window',
    'attr_custom': 'the attribution window', 'control': 'the control group', 'assets': 'the campaign assets',
    'links': 'the campaign links', 'charge': 'the quote',
}
FLAG_WORDS = {
    FLAG_REPEAT: 'repeated question',
    FLAG_LEFT: 'general answer while the order was open',
    FLAG_EXIT: 'left the order without a quote',
    FLAG_NO_QUOTE: 'did not reach a quote',
}

_COLLECT = (
    ('bpiq_wizard', 'brand_partnership'), ('bpiq_collect', 'brand_partnership'), ('bpiq_confirm_payload', 'brand_partnership'),
    ('jiq_collect', 'digital_journey'), ('jiq_confirm_payload', 'digital_journey'),
    ('fw_collect', 'flywheel'), ('fw_confirm_payload', 'flywheel'), ('fw_wizard', 'flywheel'),
    ('aiq_collect', 'attribution'), ('aiq_confirm_payload', 'attribution'),
    ('twin_collect', 'digital_twin'), ('twin_confirm_payload', 'digital_twin'),
    ('spec_draft', 'profile_iq'), ('spec_drafts', 'profile_iq'),
    ('panel_offer', 'priced_read'),
)
_QUOTE_KEYS = ('bpiq_confirm_payload', 'jiq_confirm_payload', 'fw_confirm_payload', 'aiq_confirm_payload',
               'twin_confirm_payload', 'spec_draft', 'spec_drafts', 'panel_offer')
_RUNNING_RX = re.compile(r"^\s*(?:it'?s running\b|that valuation is already running\b|on it\b|working on it\b)", re.I)
_CANCEL_TEXT = ('No problem. Ask me anything else when ready.',)
_DECLINE_TEXT = ("Okay, I won't run it.",)
_QUOTE_TEXT_RX = re.compile(r"^\s*(?:say accept to run it[^\n]*\n)?\s*here(?:'|\u2019)s what i(?:'|\u2019)ll run\b", re.I)
_FUNDS_RX = re.compile(r"needs funds on the account first|could not take payment|did not start", re.I)


def _norm(text):
    return ' '.join(str(text or '').split()).lower()


def _reply(payload):
    if not isinstance(payload, dict):
        return ''
    for k in ('reply', 'answer', 'message', 'text', 'error'):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _last_agent(history):
    for t in reversed(list(history or [])):
        if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant'):
            return t
    return None


def _turn_text(turn):
    return str((turn or {}).get('text') or (turn or {}).get('content') or '')


def flow_of(payload):
    """The order a reply belongs to, or ''."""
    if not isinstance(payload, dict):
        return ''
    for key, flow in _COLLECT:
        v = payload.get(key)
        if key in ('bpiq_wizard', 'fw_wizard'):
            if isinstance(v, dict) and v.get('step'):
                return flow
            continue
        if v:
            return flow
    return ''


def _bpiq_step(text):
    try:
        from prometheus import bpiq_wizard as _w
        return _w.step_from_prompt(text) or ''
    except Exception:
        return ''


def step_of(payload):
    """The step an order's reply is on ('' when the order has no named steps)."""
    if not isinstance(payload, dict):
        return ''
    w = payload.get('bpiq_wizard')
    if isinstance(w, dict) and w.get('step'):
        return str(w['step'])
    f = payload.get('fw_wizard')
    if isinstance(f, dict) and f.get('step'):
        return str(f['step'])
    if flow_of(payload) == 'brand_partnership':
        return _bpiq_step(_reply(payload))
    return ''


def open_order(history):
    """(flow, step) of the order the thread's last agent turn left open,
    or ('', ''): a step of the valuation wizard (its own words), a turn
    that carries an order's draft, or a reply that carried the way back
    to one."""
    turn = _last_agent(history)
    if not turn:
        return '', ''
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    txt = _turn_text(turn)
    w = meta.get('bpiq_wizard') or turn.get('bpiq_wizard')
    if isinstance(w, dict) and w.get('step'):
        return 'brand_partnership', str(w['step'])
    step = _bpiq_step(txt)
    if step:
        return 'brand_partnership', step
    for key, flow in _COLLECT:
        if key in ('bpiq_wizard', 'spec_draft', 'spec_drafts', 'panel_offer') or key.endswith('_confirm_payload'):
            continue          # a quote already given is not an order left open
        if meta.get(key) or turn.get(key):
            return flow, ''
    if meta.get('intake_resume'):
        return str(meta['intake_resume']), ''
    try:
        from prometheus import intake_guard as _ig
        for o in (meta.get('options') or turn.get('options') or turn.get('followups') or []):
            lbl = (o.get('send') or o.get('label')) if isinstance(o, dict) else o
            if _ig.is_resume_chip(str(lbl or '')):
                for flow, chip in _ig.RESUME_CHIPS.items():
                    if _norm(chip) == _norm(lbl) and not flow.endswith('_wizard'):
                        return flow, ''
    except Exception:
        pass
    return '', ''


def observe(question, payload, history=None):
    """Where a priced order stands after this reply, or None when no
    order is involved. See the module docstring. Never raises."""
    try:
        if not isinstance(payload, dict):
            return None
        reply = _reply(payload)
        flow = flow_of(payload)
        prev = _last_agent(history)
        prev_txt = _turn_text(prev)
        flags = []
        if flow:
            step = step_of(payload)
            if reply in _CANCEL_TEXT:
                state = 'cancelled'
            elif reply in _DECLINE_TEXT:
                state = 'declined'
            elif _RUNNING_RX.match(reply) or (payload.get('bpiq_job_id') and not step):
                state = 'accepted'
            elif any(payload.get(k) for k in _QUOTE_KEYS) or step == 'charge' or _QUOTE_TEXT_RX.match(reply):
                state = 'quote'
            else:
                state = 'open'
            if state == 'open' and prev_txt and _norm(prev_txt)[:240] == _norm(reply)[:240]:
                flags.append(FLAG_REPEAT)
            out = {'flow': flow, 'state': state}
            if step:
                out['step'] = step
            if flags:
                out['flags'] = flags
            return out
        # the wizard's own closing lines carry no draft
        if reply in _CANCEL_TEXT or reply in _DECLINE_TEXT:
            oflow, ostep = open_order(history)
            if oflow:
                return {'flow': oflow, 'state': 'cancelled' if reply in _CANCEL_TEXT else 'declined', **({'step': ostep} if ostep else {})}
            return None
        if _RUNNING_RX.match(reply) and (payload.get('bpiq_job_id') or 'valuation' in reply.lower()):
            return {'flow': 'brand_partnership', 'state': 'accepted'}
        if _FUNDS_RX.search(reply) and 'valuation' in reply.lower():
            return {'flow': 'brand_partnership', 'state': 'quote', 'step': 'charge'}
        # not an order's reply: was an order open when it went out?
        resume = str(payload.get('intake_resume') or '').strip()
        oflow, ostep = open_order(history)
        if resume:
            out = {'flow': resume if not resume.endswith('_wizard') else resume[:-7], 'state': 'left', 'flags': [FLAG_LEFT]}
            if ostep and oflow == out['flow']:
                out['step'] = ostep
            return out
        if oflow and reply:
            # a handoff to the build flow, a promise, a clarify of another lane: still not the order's reply
            out = {'flow': oflow, 'state': 'left', 'flags': [FLAG_EXIT]}
            if ostep:
                out['step'] = ostep
            return out
        # any other question asked again in the same words
        if reply and prev_txt and '?' in reply and _norm(prev_txt)[:240] == _norm(reply)[:240] and len(reply) <= 400:
            return {'flow': '', 'state': 'open', 'flags': [FLAG_REPEAT]}
        return None
    except Exception:
        return None


def stamp(extra, question, payload, history=None):
    """`extra` with the order's standing under 'order' (and the same on
    the payload, for the question copy). Returns `extra` unchanged when
    no order is involved. Never raises."""
    try:
        obs = observe(question, payload, history)
        if not obs:
            return extra
        # the ask log keeps eight extras, as short strings: the order's
        # standing leads the dict so it is never the one dropped
        out = {'order': encode(obs)}
        for k, v in (extra or {}).items():
            if k != 'order':
                out[k] = v
        if isinstance(payload, dict):
            payload['order_stall'] = obs
        return out
    except Exception:
        return extra


def encode(obs):
    """'brand_partnership|open|assets|repeated_question' (flow, state,
    step, flags joined by commas): the form the ask log keeps."""
    o = obs or {}
    return '|'.join([str(o.get('flow') or ''), str(o.get('state') or ''), str(o.get('step') or ''),
                     ','.join(str(f) for f in (o.get('flags') or []))])[:200]


def decode(value):
    """The dict `encode` wrote, from an ask-log record's extra; {} for
    anything else."""
    if isinstance(value, dict):
        return value
    parts = str(value or '').split('|')
    if len(parts) != 4:
        return {}
    out = {'flow': parts[0], 'state': parts[1]}
    if parts[2]:
        out['step'] = parts[2]
    if parts[3]:
        out['flags'] = [f for f in parts[3].split(',') if f]
    return out


# ----------------------------------------------------------- the copy
def label(flow):
    return LABELS.get(str(flow or ''), 'Order') if flow else 'Question'


def where(obs):
    """'the campaign assets' for a named step; '' otherwise."""
    return STEP_LABELS.get(str((obs or {}).get('step') or ''), '')


def flag_words(flags):
    return [FLAG_WORDS.get(f, str(f).replace('_', ' ')) for f in (flags or []) if f]


def subject_mark(mail_subject, obs_or_flags):
    """The copy email's subject, marked when an order stalled. Unmarked
    for a walk that is going as it should."""
    flags = obs_or_flags.get('flags') if isinstance(obs_or_flags, dict) else obs_or_flags
    words = flag_words(flags)
    if not words:
        return mail_subject
    return f"ORDER STALLED ({words[0]}): {mail_subject}"


def order_block(obs_or_flags, flow='', step=''):
    """One plain line for the copy's body, or ''."""
    if isinstance(obs_or_flags, dict):
        flags, flow, step = obs_or_flags.get('flags'), obs_or_flags.get('flow') or flow, obs_or_flags.get('step') or step
    else:
        flags = obs_or_flags
    words = flag_words(flags)
    if not words:
        return ''
    at = STEP_LABELS.get(str(step or ''), '')
    return (f"{label(flow)}: {', '.join(words)}" + (f". It stopped at {at}." if at else '.'))


def walk_flags(turns, outcome='', flow='', seen_flags=None):
    """The flags of a finished walk: what its turns were stamped with,
    a question asked twice in the same words among its answers, and
    'did not reach a quote' when it ended (idle, or on another message)
    with no recap or confirm in it. A walk the reader cancelled or
    declined is the reader's choice and is not a stall."""
    flags = [f for f in (seen_flags or []) if f]
    answers = [str((t or {}).get('a') or '') for t in (turns or [])]
    for i in range(1, len(answers)):
        a, b = _norm(answers[i - 1])[:240], _norm(answers[i])[:240]
        if a and a == b and FLAG_REPEAT not in flags:
            flags.append(FLAG_REPEAT)
    reached = any(_QUOTE_TEXT_RX.match(a) or "here's what i'll run" in a.lower() or 'cost on approval' in a.lower()
                  or re.search(r"\b(?:run the (?:journey|valuation|flywheel)|start tracking|build the twin)\b", a, re.I)
                  for a in answers)
    ended = str(outcome or '').strip().lower()
    if not reached and ended in ('walk ended',) and str(flow or '') not in ('', 'walk', 'profile_iq'):
        if FLAG_NO_QUOTE not in flags:
            flags.append(FLAG_NO_QUOTE)
    return flags


def last_step(turns):
    """The valuation step the walk's last question was on, or ''."""
    for t in reversed(list(turns or [])):
        s = _bpiq_step(str((t or {}).get('a') or ''))
        if s:
            return s
    return ''


__all__ = ['FLAG_REPEAT', 'FLAG_LEFT', 'FLAG_EXIT', 'FLAG_NO_QUOTE', 'LABELS', 'STEP_LABELS', 'observe', 'stamp', 'encode', 'decode', 'flow_of',
           'step_of', 'open_order', 'label', 'where', 'flag_words', 'subject_mark', 'order_block', 'walk_flags', 'last_step']
