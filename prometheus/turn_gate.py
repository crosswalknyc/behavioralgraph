"""The turn gate: continuation before routing, repeats handed to ops.

Runs inside service.ask before the Reader and every pattern lane
(I2 and I7, 2026-10-20). It reads the turn against the previous agent
turn (prometheus.continuation) and either answers it in the
conversation, rewrites it so the lanes run on the bound subject and
deliverable, or lets it through as a new ask.

Returns:
  None                      a new ask; nothing changes
  (envelope, status)        the gate answered (a confirm card, a clarify,
                            a definition, the calm promise on a repeat)
  {'text': ..., 'body': ...} a rewrite: the lanes run on it

A repeat never gets a scold or a debugging question (Jenna 2026-10-21,
Jay Patel: "It should never answer like this ... they should just say
we are working on it and email you when done, and then email me and
jessie that there is a problem"). The second ask may take the other
path once; the third ask, or a second ask the other path could not
answer, gets the standing calm line and ops gets the problem through
the chatbot error email, keyed on user + question (`calm_hold`).

The open-tab confirm, the priced-offer fallback and the calm line are
never reached from a continuation: the classification rides on
flask.g._pm_continuation and the lanes check it.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time

from . import continuation as _cont
from . import subject_rules as _sr
from . import faults as _faults

__all__ = ['gate', 'repeat_count', 'mark_failed_by_user', 'calm_hold', 'calm_payload', 'CALM_LINE',
           'RepeatUnanswered', 'ALERT_ROUTE']

# The standing calm line (app.py _CHATBOT_CALM_MESSAGE, Jenna 2026-09-30),
# verbatim. The one thing a reader is told when the answer will come by
# email instead of in the chat.
CALM_LINE = ("Working on it. Confirmed your task is in progress and I will email you when it "
             "completes.")
# The route label on the ops email: the chatbot error path's subject reads
# "Chatbot error: prometheus/asked-again (<user>)".
ALERT_ROUTE = 'prometheus/asked-again'
REPEAT_WINDOW_S = 30 * 60
HOLD_HOURS = 2


class RepeatUnanswered(Exception):
    """A question asked again after replies that did not land. Its text is
    the user and the question, so the error email's dedupe key (route +
    exception text) is keyed on user + question and a different user or
    question is never swallowed by the 15 minute cooldown. The attributes
    ride into the email body (app.py _chatbot_error_email)."""

    def __init__(self, user, question, prior_answers=(), bound_subject='', window='', suggested_path=''):
        q = ' '.join(str(question or '').split())
        super().__init__(f"{user or 'unknown'} asked again, unanswered: {q[:140]}")
        self.pm_user = str(user or '')
        self.pm_question = q
        self.pm_prior_answers = [' '.join(str(a or '').split())[:400] for a in (prior_answers or []) if str(a or '').strip()][:4]
        self.pm_bound_subject = str(bound_subject or '')
        self.pm_window = str(window or '')
        self.pm_suggested_path = str(suggested_path or '')
_SIGNAL_PREFIX = 'system/usage/ask_log/_signals/'


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _norm_q(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()


def _parse_ts(v):
    try:
        return _dt.datetime.strptime(str(v or ''), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=_dt.timezone.utc)
    except Exception:
        try:
            return _dt.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        except Exception:
            return None


def repeat_count(text, history, window_s=REPEAT_WINDOW_S, now=None):
    """How many times this same question was asked in the thread within
    the window and drew a reply the reader did not accept. Counts the
    prior user turns that match, each followed by an agent turn."""
    q = _norm_q(text)
    if not q or len(q.split()) < 3:
        return 0
    turns = [t for t in (history or []) if isinstance(t, dict)]
    if turns and str(turns[-1].get('role') or '') == 'user' and _norm_q(turns[-1].get('text') or turns[-1].get('content')) == q:
        turns = turns[:-1]
    now = now or _dt.datetime.now(_dt.timezone.utc)
    n = 0
    for i, t in enumerate(turns):
        if str(t.get('role') or '') != 'user':
            continue
        if _norm_q(t.get('text') or t.get('content')) != q:
            continue
        ts = _parse_ts(t.get('ts'))
        if ts is not None and (now - ts).total_seconds() > window_s:
            continue
        answered = any(str(x.get('role') or '') != 'user' for x in turns[i + 1:i + 3])
        if answered:
            n += 1
    return n


def mark_failed_by_user(uname, question, prior_reply='', reason='repeat', s3=None, bucket='dashboard-inputs'):
    """The prior reply did not land: record it now, in real time, under
    the ask log's signals prefix (the watch card and the day's digest
    read it). Never raises."""
    try:
        import boto3
        c = s3 or boto3.client('s3', region_name='us-east-2')
        ts = _now()
        day = ts[:10]
        rec = {'ts': ts, 'user': str(uname or '')[:120], 'signal': 'failed_by_user', 'reason': reason,
               'question': ' '.join(str(question or '').split())[:600], 'prior_reply': ' '.join(str(prior_reply or '').split())[:400]}
        key = f"{_SIGNAL_PREFIX}{day}/{ts[11:19].replace(':', '')}_{abs(hash(rec['question'])) % 10**8:08d}.json"
        c.put_object(Bucket=bucket, Key=key, Body=json.dumps(rec).encode('utf-8'), ContentType='application/json')
        return True
    except Exception as e:
        _faults.note('turn_gate.mark_failed_by_user', e)
        return False


def _price_label_for(kind, viewership, host):
    """The standing price for a re-run of this deliverable, in the seat's
    currency when the host knows it."""
    from . import rate_card as _rc
    key = {'build': 'profile_iq_build', 'journey': 'journey_iq', 'deck': 'panel_report', 'cut': 'profile_iq_derived_cut'}.get(kind)
    if not key:
        key = 'viewership_read' if viewership else 'panel_report'
    usd = _rc.default(key)
    symbol = '$'
    try:
        if host is not None and host.has('pricing_copy_for'):
            copy = str(host.pricing_copy_for(None) or '')
            if '\u00a3' in copy and '$' not in copy:
                symbol = '\u00a3'
    except Exception:
        pass
    return _rc.money(usd, symbol), key


def _answer(raw, reason, *, tid, via, client_surface, envelope, interpret_shape, persist, persist_turn, uname, history, text, host, route, outcome):
    decision = {'surface': 'analyze', 'mode': None, 'reason': reason, 'client_hint': None}
    out = interpret_shape(raw) if client_surface == 'interpret' else raw
    if client_surface == 'interpret':
        out['followups'] = list(raw.get('followups') or [])
        for k in ('rerun_confirm', 'memory_confirm'):
            if raw.get(k):
                out[k] = raw[k]
    try:
        host.ask_hint(route=route, outcome=outcome, subject=raw.get('subject') or None)
    except Exception as _fx:
        _faults.note('turn_gate', _fx)
    env = envelope.wrap(out, surface='analyze', decision=decision, thread_id=tid, via=via)
    persist_turn(persist, uname, tid, history, text, env, out, 'analyze')
    return env, 200


def _asked_before_question(history):
    """When the last agent turn is a question, the user ask that drew it;
    '' otherwise."""
    turns = [x for x in (history or []) if isinstance(x, dict)]
    if not turns or str(turns[-1].get('role') or '') == 'user':
        return ''
    last = ' '.join(str(turns[-1].get('text') or turns[-1].get('content') or '').split())
    if not _cont._QUESTION_RX.search(last[:200]) or len(last) > 300:
        return ''
    for t in reversed(turns[:-1]):
        if str(t.get('role') or '') == 'user':
            return ' '.join(str(t.get('text') or t.get('content') or '').split())
    return ''


def _last_agent(history):
    for t in reversed([x for x in (history or []) if isinstance(x, dict)]):
        if str(t.get('role') or '') != 'user':
            return t
    return None


def _prior_replies(text, history, limit=3):
    """The agent replies that followed this same question in the thread,
    oldest first: what the reader was shown and did not accept."""
    q = _norm_q(text)
    turns = [t for t in (history or []) if isinstance(t, dict)]
    out = []
    for i, t in enumerate(turns):
        if str(t.get('role') or '') != 'user' or _norm_q(t.get('text') or t.get('content')) != q:
            continue
        for nxt in turns[i + 1:i + 3]:
            if str(nxt.get('role') or '') == 'user':
                break
            txt = ' '.join(str(nxt.get('text') or nxt.get('content') or '').split())
            if txt:
                out.append(txt)
                break
    return out[-limit:]


def calm_payload():
    """The reply a reader sees on a repeat: the calm promise, in the shape
    the widget renders for the calm line (guidance, no card, no chips)."""
    return {'success': False, 'guidance': True, 'analysis_read': True, 'held': True,
            'error': CALM_LINE, 'reply': CALM_LINE, 'followups': [],
            'offer_deck': False, 'deck_angle': None}


def calm_hold(text, history, page, uname, host, *, reason, n_rep=0, cont=None, prior=None, bound=None):
    """A question that keeps coming back without landing is finished by a
    human: the reader is told the calm line, the ask is logged as held with
    a promise and a due time, and ops (jenna@ + jessie@) get one email
    through the chatbot error path carrying the user, the question, the
    earlier replies, the bound subject and window, and the suggested
    answer path. Keyed on user + question. Never raises; returns the raw
    calm payload."""
    text = ' '.join(str(text or '').split())
    prior = list(prior) if prior is not None else _prior_replies(text, history)
    try:
        s = bound if bound is not None else _sr.bind(text, history, page)
    except Exception:
        s = None
    subj = (s.display if s else '') or ''
    window = ''
    try:
        window = str((s.qualifiers or {}).get('window') or '') if s else ''
        if not window and isinstance(cont, dict) and cont.get('window'):
            window = _cont.window_phrase(cont['window'])
    except Exception:
        window = ''
    deliv = (cont or {}).get('deliverable') if isinstance(cont, dict) else None
    if not subj and isinstance(deliv, dict) and deliv.get('subject'):
        subj = str(deliv.get('subject') or '')
    path = _suggested_path(text, subj, page, cont)
    # the record for the watch card and the day's digest
    try:
        from flask import g as _g
        _g._pm_ask_outcome = 'held'
        _g._pm_held_promise = {'question': text, 'subject': subj, 'window': window, 'hours': HOLD_HOURS}
    except Exception:
        pass
    try:
        if host is not None and host.has('record_held'):
            host.record_held(uname, text, CALM_LINE, f"{reason}; bound subject: {subj or '(none)'}; path: {path}", 'repeat')
    except Exception as e:
        _faults.note('turn_gate.record_held', e)
    # ops: one email per user + question
    try:
        exc = RepeatUnanswered(uname, text, prior, subj, window, path)
        payload = {'text': text, 'user': uname, 'asked_times': int(n_rep or 0) + 1, 'page': page or '',
                   'bound_subject': subj, 'window': window, 'suggested_path': path,
                   'prior_replies': exc.pm_prior_answers, 'reason': reason}
        if host is not None and host.has('error_email'):
            host.error_email(ALERT_ROUTE, exc, user_email=uname or None, payload=payload, tb='(no traceback: the question came back without an answer)')
    except Exception as e:
        _faults.note('turn_gate.alert', e)
    return calm_payload()


def _suggested_path(text, subj, page, cont):
    """One line for ops: the lane that should answer this question."""
    kind = str((cont or {}).get('kind') or '') if isinstance(cont, dict) else ''
    deliv = (cont or {}).get('deliverable') if isinstance(cont, dict) else None
    if kind == 'rerun_window' and isinstance(deliv, dict) and deliv.get('subject'):
        return f"re-run the {deliv.get('subject')} read on the window the user named, at the standing price"
    if kind == 'parameter_change' and subj:
        return f"the derived read on {subj} with the new metric or breakdown"
    if kind == 'correction':
        return f"re-ask the previous question on {subj or 'the subject the user names'}; never the page they pushed away"
    low = text.lower()
    if re.search(r"where.*(?:watch|watched|stream|see)", low):
        return f"where-watched on {subj or 'the title in the words'}"
    if re.search(r"how (?:many|big)|size|audience", low) and not re.search(r"what did|watch(?:ed)? on", low):
        return f"sizing on {subj or 'the audience in the words'}"
    if subj:
        return f"the reasoned read on {subj} (bind_subject={subj}), answered in the thread and by email"
    if page:
        return f"the one-question clarify (no subject in the words; {page} is open and may be the context)"
    return "the one-question clarify, then the reasoned read on the subject the user names"


def gate(text, body, history, ctx, user, uname, tid, via, persist, *, client_surface, host, envelope,
         interpret_shape, persist_turn):
    page = _sr._page_name(ctx)
    cont = _cont.classify(text, history, None, page)
    try:
        from flask import g as _g
        _g._pm_continuation = cont
    except Exception:
        pass
    kind = str(cont.get('kind') or 'new_ask')
    deliv = cont.get('deliverable') or {}
    common = dict(tid=tid, via=via, client_surface=client_surface, envelope=envelope, interpret_shape=interpret_shape,
                  persist=persist, persist_turn=persist_turn, uname=uname, history=history, text=text, host=host)

    # ---- I7: the same question again after a reply that did not land
    n_rep = repeat_count(text, history)
    if n_rep:
        prev = _last_agent(history)
        prev_text = str((prev or {}).get('text') or (prev or {}).get('content') or '')
        mark_failed_by_user(uname, text, prev_text, reason='repeat')
        _watch(host, uname, text, 'repeat_guard', 'failed_by_user')
        try:
            from flask import g as _g2
            _g2._pm_repeat_n = n_rep
        except Exception:
            pass
        if n_rep >= 2 or kind != 'new_ask':
            # The third ask, or a repeated continuation whose own path
            # already ran once: the calm promise, and ops gets the problem.
            # Never a scold, never a debugging question (Jenna 2026-10-21).
            raw = calm_hold(text, history, page, uname, host, reason='asked again, unanswered',
                            n_rep=n_rep, cont=cont)
            return _answer(raw, 'repeat_calm', route='repeat_guard', outcome='held', **common)
        # the second time: the other path, once. The binder's subject rides
        # as a bound subject so the page never answers for it again; the
        # answer gate sees _pm_repeat_n and hands a reply that is not an
        # answer (empty, faulted, the same clarify again) to calm_hold.
        s = _sr.bind(text, history, page)
        body = dict(body)
        if s and s.source == 'words' and not body.get('bind_subject'):
            body['bind_subject'] = s.display
        body['_pm_alternative'] = True
        try:
            host.ask_hint(route='repeat_guard', outcome='alternative_path', subject=(s.display if s else None))
        except Exception as _fx:
            _faults.note('turn_gate', _fx)
        return {'text': text, 'body': body}

    if kind == 'new_ask':
        return None

    # ---- a re-run in a window: the confirm card with the deliverable, the window, the price
    if kind == 'rerun_window':
        if not deliv or deliv.get('kind') in ('none', 'question'):
            raw = {'success': True, 'action': 'answer',
                   'reply': ('Which read should I run again? Name it (or the question it answered) and the window, '
                             'two dates or a month, and I will quote it.'),
                   'followups': [], 'offer_deck': False, 'deck_angle': None}
            return _answer(raw, 'rerun_which', route='rerun_window', outcome='asked_which_deliverable', **common)
        label, key = _price_label_for(str(deliv.get('kind') or 'read'), bool(deliv.get('viewership')), host)
        raw = _cont.rerun_confirm(deliv, cont.get('window'), label)
        raw['subject'] = deliv.get('subject') or ''
        raw['rerun_confirm']['price_key'] = key
        return _answer(raw, 'rerun_confirm', route='rerun_window', outcome='asked_confirm' if cont.get('window') else 'asked_window', **common)

    # ---- the yes to a re-run card: the original question runs on the window, charged at the quoted price
    if kind == 'acceptance':
        prev = _last_agent(history)
        meta = (prev or {}).get('meta') if isinstance((prev or {}).get('meta'), dict) else {}
        rc = meta.get('rerun_confirm') if isinstance(meta.get('rerun_confirm'), dict) else None
        if rc and rc.get('question') and rc.get('window'):
            body = dict(body)
            body['bind_subject'] = body.get('bind_subject') or rc.get('subject') or ''
            body['surface'] = 'analyze'
            body['rerun_charge'] = {'subject': rc.get('subject'), 'window': rc.get('window'),
                                    'price_key': rc.get('price_key') or 'panel_report', 'price_label': rc.get('price_label')}
            try:
                from flask import g as _g3
                _g3._pm_rerun_charge = body['rerun_charge']
            except Exception:
                pass
            try:
                host.ask_hint(route='rerun_window', outcome='accepted', subject=rc.get('subject'))
            except Exception as _fx:
                _faults.note('turn_gate', _fx)
            return {'text': str(rc['question']), 'body': body}
        return None

    # ---- a reply to the which-audience / which-subject question: the original ask runs bound to it
    if kind == 'chip_answer':
        prev = _last_agent(history)
        ptxt = str((prev or {}).get('text') or (prev or {}).get('content') or '')
        orig = str((cont.get('params') or {}).get('question') or '')
        if re.search(r"which (?:audience|subject) should (?:this|it|the \w+) be on|i read that as a task|name the (?:show|brand|person)", ptxt, re.I) and orig:
            subj = str(cont.get('subject') or '').strip()
            if subj and not _sr.is_metric_phrase(subj):
                body = dict(body)
                body['bind_subject'] = subj
                q = dict((cont.get('params') or {}).get('qualifiers') or {})
                if q.get('platform'):
                    body['bind_platform'] = q['platform']
                if q.get('window'):
                    body['bind_window'] = q['window']
                body['surface'] = 'analyze'
                try:
                    host.ask_hint(route='chip_answer', outcome='bound_original_ask', subject=subj)
                except Exception as _fx:
                    _faults.note('turn_gate', _fx)
                return {'text': orig, 'body': body}
        return None

    # ---- a different metric, breakdown, window or cut on the last read: same subject, the read again
    if kind == 'parameter_change':
        subj = str(cont.get('subject') or '').strip()
        if subj:
            body = dict(body)
            body['bind_subject'] = body.get('bind_subject') or subj
            body['surface'] = 'analyze'
            body['metric_hint'] = dict(cont.get('params') or {})
            try:
                host.ask_hint(route='parameter_change', outcome='rebound_previous_subject', subject=subj)
            except Exception as _fx:
                _faults.note('turn_gate', _fx)
            return {'text': text, 'body': body}
        return None

    # ---- a question about the previous answer: a definition when one fits, else the lanes answer in context
    if kind == 'question_about_answer':
        try:
            from . import guards as _gd
            if _gd.is_sample_definition_ask(text):
                return None   # the product fact about the ten million answers this one (Tania Nasr 2026-10-20)
        except Exception as e:
            _faults.note('turn_gate.sample_fact', e)
        try:
            from . import kpi_definitions as _kd
            vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else {}
            view_id = str((vc or {}).get('view_id') or '')
            d = _kd.find_definition(text, view_id)
            if d:
                raw = {'success': True, 'action': 'answer', 'reply': _kd.definition_reply(d, view_id),
                       'followups': [], 'offer_deck': False, 'deck_angle': None}
                return _answer(raw, 'kpi_definition', route='kpi_definition', outcome='answered', **common)
        except Exception as e:
            _faults.note('turn_gate.definition', e)
        return None

    # a correction continues the conversation: the prior reply is graded
    # failed_by_user now (I10), the thread's subject rides as the bound
    # subject so the page never answers for it again, and the lanes read
    # the flag and never price it
    if kind == 'correction':
        prev = _last_agent(history)
        prev_text = str((prev or {}).get('text') or (prev or {}).get('content') or '')
        mark_failed_by_user(uname, text, prev_text, reason='correction')
        _watch(host, uname, text, 'correction', 'failed_by_user')
        body = dict(body)
        body['_pm_correction'] = True
        subj = str(cont.get('subject') or (deliv or {}).get('subject') or '').strip()
        if subj and not body.get('bind_subject') and not _sr.is_metric_phrase(subj):
            body['bind_subject'] = subj
        # the question the person is correcting was drawn by an ask of its
        # own (Mark Valdez 2026-10-20: the deck ask drew "Do you want this
        # on Salish Matter?"); that ask runs again on the corrected subject
        prev_q = _asked_before_question(history)
        if prev_q:
            try:
                from . import understand as _und
                surf = str((_und.decide(prev_q, has_ctx=bool(page)) or {}).get('surface') or '')
            except Exception:
                surf = ''
            if surf == 'deck':
                body['surface'] = 'deck'
                new_text = f"{prev_q.rstrip(' .?!')} on {subj}" if subj else prev_q
                try:
                    host.ask_hint(route='correction', outcome='deck_reasked', subject=subj or None)
                except Exception as _fx:
                    _faults.note('turn_gate', _fx)
                return {'text': new_text, 'body': body}
        # no subject to continue on: the deterministic lanes still get the
        # turn (a deliverable lookup, a product fact, a status ask answer
        # corrections too); an empty reply becomes the one-question
        # clarify at the exit (envelope)
        try:
            host.ask_hint(route='correction', outcome='rebound_thread_subject' if subj else 'continued', subject=subj or None)
        except Exception as _fx:
            _faults.note('turn_gate', _fx)
        return {'text': text, 'body': body}
    return None


def _watch(host, uname, question, route, outcome):
    """The System Status watch card sees a failed_by_user grade the
    moment it happens (I10). Never raises."""
    try:
        if host is not None and host.has('watch_flag'):
            host.watch_flag(uname, question, route, outcome, {'user_signal': 'rejected'})
    except Exception as e:
        _faults.note('turn_gate.watch', e)
