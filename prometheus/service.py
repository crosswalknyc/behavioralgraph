"""``ask()``: the single entry point every Prometheus client calls.

    understand -> gate -> run the surface core -> persist -> envelope

The dashboard widget, the standalone app, and the API all land here
with the same request shape::

    {'text': str,                    # required
     'thread_id': str | None,        # defaults to the caller's active thread
     'history': [...] | None,        # a client may send its own; else loaded
     'context': {...} | None,        # page facts: open profile, cuts, tabs
     'mode': str | None,             # explicit analyze mode (chip)
     'surface': str | None,          # a client mid armed-step names it
     'extra': {...} | None,          # explicit confirm / input step
     'persist': bool,                # save turns server-side (default: yes,
                                     #   except client == 'dashboard')
     'client': 'dashboard' | 'app' | 'api' | None}

The two request cores (``host.analyze_core`` / ``host.interpret_core``)
are the legacy route bodies, split out of their Flask views in Phase 1
so they run for an already-gated user from any surface. They still
return Flask responses; ``_unpack`` reads them back to a dict.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from .host import host
from . import understand
from . import envelope
from . import seams

_SURFACES = ('interpret', 'analyze', 'deck')
_ANALYZE_PASSTHROUGH = ('bind_subject', 'bind_cohort', 'overall_ranks',
                        'panel_confirm', 'bpiq_inputs', 'bpiq_confirm',
                        'jiq_inputs', 'jiq_confirm', 'fw_inputs',
                        'fw_confirm', 'aiq_inputs', 'aiq_confirm')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def username_of(user):
    return str((user or {}).get('username') or (user or {}).get('email')
               or 'anon').strip()


def _unpack(resp):
    """Flask Response | (Response, status) | dict -> (dict, status)."""
    status = 200
    if isinstance(resp, tuple):
        resp, status = resp[0], (resp[1] if len(resp) > 1 else 200)
    if isinstance(resp, dict):
        return resp, status
    try:
        data = resp.get_json(silent=True)
        if data is None:
            data = json.loads(resp.get_data(as_text=True) or '{}')
        return (data if isinstance(data, dict) else {'value': data}), \
            getattr(resp, 'status_code', status) or status
    except Exception:
        return {'success': False}, getattr(resp, 'status_code', 500) or 500


# ---------------------------------------------------------------- threads

def threads_index(uname):
    return host.load_threads_index(uname)


def active_thread_id(uname):
    idx = threads_index(uname)
    tid = idx.get('active')
    if not tid and idx.get('threads'):
        tid = idx['threads'][0]['id']
    return tid


def thread_exists(uname, tid):
    idx = threads_index(uname)
    return any(t.get('id') == tid for t in idx.get('threads', []))


def load_thread(uname, tid):
    return host.s3_json(host.thread_key(uname, tid), [])


def save_thread(uname, tid, history):
    """Write one thread by id and refresh its index entry. Never moves
    the caller's active thread: a request from the app or the API must
    not switch what the dashboard shows."""
    trimmed = list(history or [])[-200:]
    host.s3_put_json(host.thread_key(uname, tid), trimmed)
    idx = threads_index(uname)
    now = _now()
    for th in idx.get('threads', []):
        if th.get('id') == tid:
            th['updated'] = now
            th['turns'] = len(trimmed)
            if th.get('title') in (None, '', 'New chat'):
                try:
                    th['title'] = host.thread_title_from(trimmed)
                except Exception:
                    pass
            break
    host.s3_put_json(host.threads_index_key(uname), idx)
    return True


def new_thread(uname, *, activate=False, title=None):
    idx = threads_index(uname)
    now = _now()
    tid = uuid.uuid4().hex[:10]
    idx.setdefault('threads', []).append(
        {'id': tid, 'title': (title or 'New chat')[:48], 'created': now,
         'updated': now, 'turns': 0})
    cap = host.max_threads if host.has('max_threads') else 40
    if len(idx['threads']) > cap:
        idx['threads'] = sorted(
            idx['threads'], key=lambda t: str(t.get('updated') or ''),
            reverse=True)[:cap]
    if activate or not idx.get('active'):
        idx['active'] = tid
    host.s3_put_json(host.threads_index_key(uname), idx)
    host.s3_put_json(host.thread_key(uname, tid), [])
    return tid, idx


def list_threads(uname):
    idx = threads_index(uname)
    return {'active': idx.get('active'),
            'threads': sorted(idx.get('threads', []),
                              key=lambda t: str(t.get('updated') or ''),
                              reverse=True)}


# -------------------------------------------------------------------- ask

def _gate_for(surface, user):
    """Mode + funds gates for the chosen surface. Returns a Flask
    response to short-circuit with, or None to proceed."""
    if surface in ('analyze', 'deck'):
        if not host.gate_analyze(user):
            return host.gate_refusal('analyze')
    else:
        if not host.gate_pull(user):
            return host.gate_refusal('pull')
        # The interpret view ran its funds gate before parsing; the
        # analyze core runs its own inside. Mirror the view here.
        fr = host.funds_gate(user)
        if fr is not None:
            return fr
    return None


def _analyze_body(body, text, history, decision, tid=None):
    ctx = body.get('context')
    if ctx is None:
        ctx = body.get('page_context')
    out = {'text': text, 'history': history, 'page_context': ctx or None,
           'thread_id': tid}
    mode = decision.get('mode') or body.get('mode')
    if mode:
        out['mode'] = mode
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    for k in _ANALYZE_PASSTHROUGH:
        v = extra.get(k, body.get(k))
        if v is not None:
            out[k] = v
    return out


def _deck_body(body, text, history, ctx, tid=None):
    out = {'text': text, 'history': history, 'page_context': ctx or None,
           'thread_id': tid}
    for k in ('angle', 'confirm_open_screen'):
        if body.get(k) is not None:
            out[k] = body[k]
    return out


def _interpret_body(body, text, history, tid=None):
    out = {'text': text, 'history': history, 'thread_id': tid}
    for k in ('locked_sample_tu', 'locked_sample_avid'):
        if body.get(k) is not None:
            out[k] = body[k]
    return out


_ACCEPT_RX = re.compile(
    r"^\s*(ok(ay)?|yes|yeah|yep|yup|sure|please|go|go ahead|do it|run it|"
    r"build it|approve[d]?|run|build|start|let's do it|lets do it|sounds good|"
    r"fine)\b[\s,.!-]*(ok(ay)?|yes|please|go|run|build|start|do it|it|that|"
    r"the run|the profile|the build)?", re.I)
_OFFER_RX = re.compile(r"^run a profile on (.+)$", re.I)


def _last_agent_options(history):
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        role = str(h.get('role') or '').lower()
        if role == 'user':
            return []
        if role in ('agent', 'assistant'):
            meta = h.get('meta') if isinstance(h.get('meta'), dict) else {}
            opts = meta.get('options') or h.get('options') or []
            out = []
            for o in opts:
                if isinstance(o, dict):
                    out.append(str(o.get('send') or o.get('label') or ''))
                elif isinstance(o, str):
                    out.append(o)
            return [o for o in out if o]
    return []


def accept_offered_run(history, text):
    """When the previous agent turn offered 'Run a profile on X' and this
    turn accepts it in words, return that chip's send value; else ''.
    'ok run Gunna', 'yes', 'go ahead and build it', 'run gunna' all
    accept; 'why do I need that', 'not now', a new question do not."""
    t = str(text or '').strip()
    if not t or len(t) > 80:
        return ''
    offers = [o for o in _last_agent_options(history) if _OFFER_RX.match(o.strip())]
    if not offers:
        return ''
    low = t.lower()
    if re.search(r"\b(not now|no thanks|no|later|why|what|how|which|who|\?)", low) \
            and not re.match(r"^\s*(ok|yes|yeah|sure)\b", low):
        return ''
    for o in offers:
        subj = _OFFER_RX.match(o.strip()).group(1).strip()
        subj_l = subj.lower()
        if re.search(r"\b(run|build|pull|start|do)\b.*" + re.escape(subj_l), low) \
                or low == subj_l:
            return o.strip()
    # A build verb followed by some OTHER noun phrase is a different ask
    # ("run a profile on Taylor Swift" under a Gunna offer).
    m = re.search(r"\b(run|build|pull|start)\b\s+(?:a profile on\s+|a profile for\s+)?(.*)$", low)
    if m:
        rest = re.sub(r"\b(it|that|this|the run|the profile|the build|please|now|again|for me|thanks?)\b",
                      " ", m.group(2))
        rest = re.sub(r"[^a-z0-9]+", " ", rest).strip()
        if rest and not any(re.search(re.escape(_OFFER_RX.match(o.strip()).group(1).strip().lower()), rest)
                            for o in offers):
            return ''
    if len(offers) == 1 and _ACCEPT_RX.match(t) and len(t.split()) <= 6:
        return offers[0].strip()
    return ''


def ask(user, body, *, via='session'):
    """Run one ask end to end. Returns (envelope_dict, http_status)."""
    body = body if isinstance(body, dict) else {}
    text = str(body.get('text') or '').strip()
    uname = username_of(user)
    client = str(body.get('client') or '').strip().lower()
    persist = bool(body.get('persist', client != 'dashboard'))

    if not text:
        env = envelope.wrap({'success': False,
                             'error': 'Ask me something and I will take it from there.'},
                            surface='interpret', decision={'reason': 'empty'},
                            via=via)
        return env, 200

    # Thread: explicit id must belong to the caller; else the active one.
    tid = str(body.get('thread_id') or '').strip() or None
    if tid and not thread_exists(uname, tid):
        return envelope.wrap({'success': False, 'error': 'unknown thread'},
                             surface='interpret',
                             decision={'reason': 'bad_thread'}, via=via), 404
    if not tid:
        tid = active_thread_id(uname)

    history = body.get('history')
    if not isinstance(history, list):
        history = load_thread(uname, tid) if tid else []

    ctx = body.get('context')
    if ctx is None:
        ctx = body.get('page_context')
    has_ctx = bool(ctx)
    open_tabs = 0
    try:
        open_tabs = len((ctx or {}).get('other_tabs') or [])
    except Exception:
        pass

    # Accepting an offered run (2026-10-06, Eliot / Gunna): the build-
    # first offer ends with the chips 'Run a profile on X' / 'Not now'.
    # A typed acceptance ("ok run Gunna", "yes", "go ahead", "build
    # it") is that chip, not a new question; before this it re-ran the
    # analyze path and re-issued the identical offer. The text becomes
    # the chip's own send value so the build flow takes it.
    try:
        _accepted = accept_offered_run(history, text)
    except Exception:
        _accepted = ''
    if _accepted:
        print(f"[prometheus] offer accepted: {text[:60]!r} -> {_accepted!r}")
        text = _accepted
        body = dict(body)
        body['text'] = text
        body['surface'] = 'interpret'

    # Unresolved referents (2026-10-02 Jenna: "it should have asked him
    # which 3 influencers he was talking about then actually given him
    # the answer"). Two halves, both before any surface runs:
    #  1. This turn is the names answering our which-ones question:
    #     fold them into the question that asked, and run that on the
    #     analyze surface bound to those names.
    #  2. This turn points at "these three creators" / "both shows" /
    #     "all 4 titles" and names none of them anywhere we can see:
    #     ask which ones. No model call, no charge, no offer.
    referent_decision = None
    _client_surface = str(body.get('surface') or '').strip().lower()
    try:
        from . import referents as _refs
        _merged, _label = _refs.answer_merge(history, text)
        if _merged:
            text = _merged
            body = dict(body)
            body['text'] = text
            if not body.get('bind_subject') and not (
                    isinstance(body.get('extra'), dict)
                    and body['extra'].get('bind_subject')):
                body['bind_subject'] = _label
            referent_decision = {'surface': 'analyze', 'mode': None,
                                 'reason': 'referent_answer',
                                 'client_hint': None}
        else:
            _extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
            _armed_now = any(_extra.get(k) or body.get(k)
                             for k in _ANALYZE_PASSTHROUGH)
            if not _armed_now:
                _ref = _refs.detect_unresolved(text, history, ctx)
                if _ref:
                    raw = _refs.clarify_payload(_ref, text)
                    if _client_surface == 'interpret':
                        raw = _interpret_shape(raw)
                    decision = {'surface': 'analyze', 'mode': None,
                                'reason': 'referent_clarify',
                                'client_hint': None}
                    try:
                        host.ask_hint(route='referent_clarify',
                                      outcome='asked_which')
                    except Exception:
                        pass
                    env = envelope.wrap(raw, surface='analyze',
                                        decision=decision,
                                        thread_id=tid, via=via)
                    _persist_turn(persist, uname, tid, history, text,
                                  env, raw, 'analyze')
                    return env, 200
    except Exception as e:
        print(f"[prometheus] referent gate skipped: {e}")

    # Answers to a which-one confirm (2026-10-05, Scott). The turn
    # before asked "Do you mean for Will And Grace, or Will & Grace on
    # Hulu?" and the user typed "both". The widget only resolves exact
    # chip text, so the raw word reached the router, which asked the
    # same question again and then gave up. The server resolves it:
    # both / either / whichever / a label / "the Hulu one" picks the
    # option (the same entity under two spellings is one option), and
    # the ORIGINAL ask re-runs bound to it. Never ask twice.
    if referent_decision is None and not _armed(body):
        try:
            _ca = _confirm_answer(text, history)
        except Exception as e:
            print(f"[prometheus] confirm-answer gate skipped: {e}")
            _ca = None
        if _ca:
            text = _ca['question']
            body = dict(body)
            body['text'] = text
            if _ca.get('subject') and not body.get('bind_subject'):
                body['bind_subject'] = _ca['subject']
                if _ca.get('cohort'):
                    body['bind_cohort'] = _ca['cohort']
            try:
                host.ask_hint(route='confirm_answer',
                              outcome='resolved',
                              subject=_ca.get('subject'))
            except Exception:
                pass

    # Bare replies (2026-10-02 audit). "approved", "ok", "no", "1",
    # "none" with nothing armed on the server side are not asks. They
    # used to reach a reasoning pass that guessed a subject out of them
    # ("approved" became a fresh build of the last profile mentioned;
    # "no" re-asked the open-screen question it was declining). With no
    # armed step they get a short, deterministic reply; a number or a
    # yes that answers the options on the previous agent turn is
    # rewritten to that option and runs as that option.
    if referent_decision is None:
        try:
            _rewrite, _bare = _bare_reply(body, text, history, has_ctx)
            if _rewrite:
                text = _rewrite
                body = dict(body)
                body['text'] = text
            elif _bare is not None:
                decision = {'surface': 'analyze' if has_ctx else 'interpret',
                            'mode': None, 'reason': 'short_reply',
                            'client_hint': None}
                raw = _bare
                if _client_surface == 'interpret':
                    raw = _interpret_shape(raw)
                try:
                    host.ask_hint(route='short_reply', outcome='answered')
                except Exception:
                    pass
                env = envelope.wrap(raw, surface=decision['surface'],
                                    decision=decision, thread_id=tid,
                                    via=via)
                _persist_turn(persist, uname, tid, history, text, env, raw,
                              decision['surface'])
                return env, 200
        except Exception as e:
            print(f"[prometheus] bare-reply gate skipped: {e}")

    # Pricing (2026-10-06, audit item 5): one deterministic answer from
    # the rate card on every surface. It used to be an error on one
    # surface, a decline on another and an answer on the third.
    if referent_decision is None and not _armed(body):
        try:
            _is_price = bool(host.pricing_question(text)) if host.has('pricing_question') else False
        except Exception:
            _is_price = False
        if _is_price:
            try:
                copy = str(host.pricing_copy) if host.has('pricing_copy') else ''
            except Exception:
                copy = ''
            if copy:
                raw = {'success': True, 'action': 'answer', 'reply': copy,
                       'followups': ['How many credits do I have left?'],
                       'offer_deck': False, 'deck_angle': None}
                decision = {'surface': 'analyze', 'mode': None,
                            'reason': 'product_fact', 'client_hint': None}
                if _client_surface == 'interpret':
                    raw = _interpret_shape(raw)
                try:
                    host.ask_hint(route='pricing', outcome='answered')
                except Exception:
                    pass
                env = envelope.wrap(raw, surface='analyze', decision=decision,
                                    thread_id=tid, via=via)
                _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
                return env, 200

    # Catalog lane (2026-10-06, Jenna: speed). "do we have a profile for
    # X", "do you see X", "how big is the X audience" are lookups: the
    # corpus catalog answers them with no model call. Runs before any
    # client-forced surface, so a yes/no question sitting in the build
    # flow can never draft a build.
    if referent_decision is None and not _armed(body):
        try:
            from . import catalog_lane as _cl
            _cat = _cl.answer(text, ctx)
        except Exception as e:
            print(f"[prometheus] catalog lane skipped: {e}")
            _cat = None
        if _cat:
            raw = _cat
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'catalog_lookup', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_cat.get('followups') or [])
                if _cat.get('memory_confirm'):
                    raw['memory_confirm'] = _cat['memory_confirm']
            try:
                host.ask_hint(route='catalog_lookup', outcome='answered',
                              subject=_cat.get('subject'))
            except Exception:
                pass
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # Drill-down lane (2026-10-06, Alexia's 27,559). A question that
    # names a number from a Digital Journey is a lookup into that
    # journey: answer from the breakdown the file holds, or build it once
    # and write it onto the page under the row. Never a fresh read that
    # cannot be checked, never the calm line.
    if referent_decision is None and not _armed(body):
        try:
            from . import drilldown as _dd
            _dd_raw = _dd.answer(text, uname, ctx=ctx, tid=tid) if _dd.looks_like_drilldown(text) else None
        except Exception as e:
            print(f"[prometheus] drill-down lane skipped: {e}")
            _dd_raw = None
        if _dd_raw:
            raw = _dd_raw
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'journey_drilldown', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_dd_raw.get('followups') or [])
                if _dd_raw.get('read_job_id'):
                    raw['read_job_id'] = _dd_raw['read_job_id']
            try:
                host.ask_hint(route='journey_drilldown', outcome='answered',
                              subject=_dd_raw.get('subject'))
            except Exception:
                pass
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw, 'analyze')
            return env, 200

    # Capability questions (2026-10-02 audit). "Can I cut the existing
    # Apple TV+ profile by quarter (i.e., 2Q 2026)?" was split into two
    # builds named "I.e Can I Cut ..." and "2Q 2026 Can I Cut ...". A
    # question about what the product does gets the answer, not a
    # draft and never a batch.
    if referent_decision is None and not _armed(body):
        try:
            from . import guards as _g
            _cap = _g.capability_answer(text)
        except Exception as e:
            print(f"[prometheus] capability gate skipped: {e}")
            _cap = None
        if _cap:
            raw = {'success': True, 'action': 'answer',
                   'reply': _cap['reply'],
                   'followups': list(_cap.get('followups') or []),
                   'offer_deck': False, 'deck_angle': None}
            decision = {'surface': 'analyze', 'mode': None,
                        'reason': 'capability_question', 'client_hint': None}
            if _client_surface == 'interpret':
                raw = _interpret_shape(raw)
                raw['followups'] = list(_cap.get('followups') or [])
            try:
                host.ask_hint(route='capability_question', outcome='answered')
            except Exception:
                pass
            env = envelope.wrap(raw, surface='analyze', decision=decision,
                                thread_id=tid, via=via)
            _persist_turn(persist, uname, tid, history, text, env, raw,
                          'analyze')
            return env, 200

    # A client in the middle of its own armed step (a confirm chip, a
    # deck angle picker, a clarify answer) already knows the surface.
    # It names it; the server still gates and runs it.
    forced = str(body.get('surface') or '').strip().lower()
    # A client sitting in its build flow forces 'interpret' for every
    # message. A question is still a question (2026-10-06: "do we have
    # a profile for Ms. Rachel?" drafted a 77 second build that way).
    # With nothing armed, a question-shaped ask keeps the server's own
    # read of it.
    if forced == 'interpret' and referent_decision is None and not _armed(body):
        try:
            _own = understand.decide(text, has_ctx=has_ctx, open_tabs=open_tabs)
            if _own.get('surface') == 'analyze' and str(_own.get('reason') or '') in (
                    'question', 'subiq_lookup', 'capability_question', 'compare_open',
                    'data_open'):
                forced = ''
        except Exception:
            pass
    if referent_decision is not None:
        decision = referent_decision
    elif forced in _SURFACES:
        decision = {'surface': forced,
                    'mode': (str(body.get('mode') or '').strip().lower()
                             or None),
                    'reason': 'client_surface', 'client_hint': None}
    else:
        try:
            _outline = understand.prior_deck_outline(history)
        except Exception:
            _outline = False
        decision = understand.decide(
            text, has_ctx=has_ctx, mode=body.get('mode'),
            extra=body.get('extra'), open_tabs=open_tabs,
            deck_in_flight=bool(body.get('deck_in_flight')),
            prior_outline=_outline)
    surface = decision['surface']
    try:
        host.ask_hint(route='prometheus/ask:' + surface)
    except Exception:
        pass

    gate_resp = _gate_for(surface, user)
    if gate_resp is not None:
        raw, status = _unpack(gate_resp)
        return envelope.wrap(raw, surface=surface, decision=decision,
                             thread_id=tid, via=via), status

    try:
        if surface == 'analyze':
            resp = host.analyze_core(
                user, _analyze_body(body, text, history, decision, tid),
                text, history)
        elif surface == 'deck':
            resp = host.deck_core(user, _deck_body(body, text, history, ctx, tid))
        else:
            resp = host.interpret_core(
                user, _interpret_body(body, text, history, tid), text, history)
    except Exception as e:
        try:
            host.error_email('prometheus/ask:' + surface, e)
        except Exception:
            pass
        raw, status = host.calm_payload(), 200
    else:
        raw, status = _unpack(resp)
        if (referent_decision is not None and _client_surface == 'interpret'
                and isinstance(raw, dict)):
            # The widget is sitting in its build flow (that is where it
            # sent the names). That flow renders analyze results only
            # in the guidance shape, the same way the legacy interpret
            # deflection hands them back.
            raw = _interpret_shape(raw)

    env = envelope.wrap(raw, surface=surface, decision=decision,
                        thread_id=tid, via=via)
    _persist_turn(persist, uname, tid, history, text, env, raw, surface)
    return env, status


_ARMED_BODY_KEYS = ('locked_sample_tu', 'locked_sample_avid', 'angle',
                    'confirm_open_screen', 'draft', 'step')
_THANKS_RX = re.compile(r'^(?:thanks?|thank\s+you|ty|cool|got\s+it|'
                        r'understood|noted|perfect|great)[.! ]*$', re.I)
_ORD_WORDS = {'one': 1, 'first': 1, 'the first': 1, 'the first one': 1,
              'two': 2, 'second': 2, 'the second': 2, 'the second one': 2,
              'three': 3, 'third': 3, 'the third': 3, 'the third one': 3,
              'four': 4, 'five': 5}


def _armed(body):
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    if any(extra.get(k) or body.get(k) for k in _ANALYZE_PASSTHROUGH):
        return True
    return any(body.get(k) for k in _ARMED_BODY_KEYS)


def _last_agent_turn(history):
    for t in reversed(history or []):
        if isinstance(t, dict) and str(t.get('role') or '') == 'agent':
            return t
    return None


_UTILITY_CHIP_RX = re.compile(
    r'^(?:email\s+me|send\s+me|save|download|something\s+else|'
    r'cancel|no\b|none\b|skip|new\s+thread|start\s+over)', re.I)


def _option_labels(turn):
    """Choices the previous agent turn offered, only when that turn
    asked a question: a numbered list in its text first, else its
    envelope options minus utility chips (email me, something else)."""
    out = []
    if not isinstance(turn, dict):
        return out
    txt = str(turn.get('text') or '')
    if '?' not in txt:
        return out
    for m in re.finditer(r'(?m)^\s*(\d{1,2})[.)]\s+(.+?)\s*$', txt):
        out.append(m.group(2).strip())
    if out:
        return out
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    for o in (meta.get('options') or []):
        if isinstance(o, dict):
            lbl = str(o.get('send') or o.get('label') or '').strip()
        else:
            lbl = str(o or '').strip()
        if lbl and not _UTILITY_CHIP_RX.match(lbl):
            out.append(lbl)
    return out


_CONFIRM_Q_RX = re.compile(
    r'^\s*Do you mean (?:for )?(.+?)(?:,? or (.+?))?\s*\?\s*$', re.I | re.S)
_CONFIRM_ANY_RX = re.compile(
    r'^(?:both|either|either one|either is fine|whichever|any|any of them|'
    r'all|all of them|does ?n[o\']t matter|doesnt matter|same thing|'
    r'they are the same|same|yes|yep|yeah|that one|the first( one)?|'
    r'first|the second( one)?|second)[.! ]*$', re.I)
_CONFIRM_THE_ONE_RX = re.compile(r'^the\s+(.+?)\s+one[.! ]*$', re.I)


def _confirm_options(turn):
    """[{label, subject, cohort}] for a which-one confirm turn, from
    its envelope options first (they carry the bound subject), else
    parsed out of the question text."""
    out = []
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else {}
    for o in (mc.get('options') or []):
        if isinstance(o, dict) and o.get('label'):
            out.append({'label': str(o['label']), 'subject': str(o.get('subject') or o['label']),
                        'cohort': str(o.get('cohort') or '') or None})
    if not out:
        for o in (meta.get('options') or []):
            lbl = str((o.get('label') if isinstance(o, dict) else o) or '').strip()
            if lbl and not _UTILITY_CHIP_RX.match(lbl):
                out.append({'label': lbl, 'subject': lbl, 'cohort': None})
    if not out:
        m = _CONFIRM_Q_RX.match(str(turn.get('text') or ''))
        if m:
            for g in (m.group(1), m.group(2)):
                if g and g.strip():
                    out.append({'label': g.strip(), 'subject': g.strip(), 'cohort': None})
    return out


def _confirm_answer(text, history):
    """When the previous agent turn was a which-one confirm and this
    turn answers it, return {'question', 'subject', 'cohort', 'label'}:
    the ask that triggered the confirm plus the option picked. None
    when this turn is not such an answer."""
    hist = [t for t in (history or []) if isinstance(t, dict)]
    prev = _last_agent_turn(hist)
    if not prev or not _CONFIRM_Q_RX.match(str(prev.get('text') or '')):
        return None
    options = _confirm_options(prev)
    if not options:
        return None
    low = ' '.join(str(text or '').lower().split()).strip()
    if not low or len(low) > 60:
        return None
    try:
        from prometheus_memory import entity_fold
    except Exception:
        def entity_fold(x):
            return re.sub(r'[^a-z0-9]+', ' ', str(x or '').lower()).strip()
    pick = None
    for o in options:
        if low.strip(' .!') == o['label'].lower() \
                or entity_fold(low) == entity_fold(o['label']):
            pick = o
            break
    if pick is None:
        m = _CONFIRM_THE_ONE_RX.match(low)
        if m:
            key = entity_fold(m.group(1))
            for o in options:
                if key and (key in entity_fold(o['label'])
                            or key in o['label'].lower()):
                    pick = o
                    break
    if pick is None and _CONFIRM_ANY_RX.match(low):
        if re.match(r'^(?:the )?second', low) and len(options) > 1:
            pick = options[1]
        else:
            # both / either / yes / first: one entity under two
            # spellings is one option; otherwise the first offered.
            pick = options[0]
    if pick is None:
        return None
    # The ask that triggered the confirm: the memory_confirm question
    # on the turn, else the user turn right before it.
    meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
    mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else {}
    question = str(mc.get('question') or '').strip()
    if not question:
        idx = hist.index(prev)
        for t in reversed(hist[:idx]):
            if str(t.get('role') or '') == 'user' and str(t.get('text') or '').strip():
                question = str(t['text']).strip()
                break
    if not question:
        return None
    return {'question': question, 'subject': pick.get('subject') or pick['label'],
            'cohort': pick.get('cohort'), 'label': pick['label']}


def _bare_reply(body, text, history, has_ctx):
    """(rewrite_text, payload). rewrite_text is set when the bare reply
    resolves to an option the previous turn offered (the ask continues
    as that option). payload is the deterministic answer for a bare
    reply nothing was waiting on. (None, None) means not a bare reply
    or an armed step owns it."""
    from . import guards
    kind = guards.bare_reply_kind(text)
    if not kind or _armed(body):
        return None, None
    prev = _last_agent_turn(history)
    options = _option_labels(prev)
    low = ' '.join(str(text or '').lower().split()).strip(' .!')
    if kind == 'number' and options:
        picks = []
        for tok in re.split(r'\s*(?:,|and|&)\s*', low):
            tok = tok.replace('option', '').strip()
            n = None
            if tok.isdigit():
                n = int(tok)
            elif tok in _ORD_WORDS:
                n = _ORD_WORDS[tok]
            elif tok in ('the last', 'the last one', 'last'):
                n = len(options)
            if n and 1 <= n <= len(options):
                picks.append(options[n - 1])
        if picks:
            return ', '.join(dict.fromkeys(picks)), None

    def _p(reply, chips=None):
        out = {'success': True, 'action': 'answer', 'reply': reply,
               'followups': list(chips or []), 'offer_deck': False,
               'deck_angle': None}
        return out

    if kind == 'affirm':
        if _THANKS_RX.match(low):
            return None, _p('Anytime. Ask me the next one when you are ready.')
        if options:
            return None, _p('Which one? ' + ' / '.join(options[:4]) + '. '
                            'Send the one you mean and I will run it.',
                            options[:4])
        if re.search(r'approv|go|run|ship|do it|proceed', low):
            return None, _p(
                'Nothing is waiting on an approval right now. If you were '
                'approving a brief, use the Approve button on its card. '
                'Otherwise tell me the audience or the question and I '
                'will take it from there.')
        return None, _p(
            'Nothing is waiting on a yes right now. Ask me the question '
            'or name the audience and I will run it.')
    if kind == 'negative':
        if re.search(r'cancel|stop|never ?mind|forget|scratch|skip', low):
            return None, _p('Closed. Ask me anything else when ready.')
        if options:
            return None, _p(
                'No problem. Which audience should I use instead? Name '
                'it here and I will run the same question on it.')
        return None, _p(
            'No problem. Tell me the audience or the question you want '
            'instead and I will run it.')
    if kind == 'none':
        return None, _p(
            'Noted. If that answers a brief, finish it on the card above. '
            'Otherwise, what would you like to run next?')
    # number with no options to map to
    return None, _p(
        'Which list is that answering? Tell me the choice in words and '
        'I will run it.')


def _interpret_shape(raw):
    """Analyze result -> the one payload the widget's build flow renders
    as a plain chat turn (no approval card): success False + guidance,
    reply on `error`, with the armed extras the flow knows how to arm
    (read job, memory confirm, priced read offer)."""
    if not isinstance(raw, dict) or not raw.get('success') \
            or not raw.get('reply'):
        return raw
    out = {'success': False, 'guidance': True, 'analysis_read': True,
           'error': str(raw['reply'])}
    for k in ('read_job_id', 'memory_confirm', 'panel_offer',
              'referent_clarify', 'file_link'):
        if raw.get(k):
            out[k] = raw[k]
    if raw.get('memory_confirm') or raw.get('panel_offer'):
        out['followups'] = [str(f) for f in (raw.get('followups') or []) if f]
    return out


def _persist_turn(persist, uname, tid, history, text, env, raw, surface):
    """Append the user turn and the agent turn to the thread. The
    which-ones question keeps its referent on the agent turn's meta so
    the names that come back merge into the question that asked."""
    if not (persist and tid and env.get('text')):
        return
    try:
        turns = list(history)
        turns.append({'role': 'user', 'text': text, 'ts': _now()})
        meta = {'kind': env['kind'], 'surface': surface}
        if env.get('options'):
            meta['options'] = env['options']
        if env.get('job'):
            meta[env['job']['type'] + '_job_id'] = env['job']['id']
        if isinstance(raw, dict) and isinstance(
                raw.get('referent_clarify'), dict):
            meta['referent_clarify'] = raw['referent_clarify']
        turns.append({'role': 'agent', 'text': env['text'],
                      'ts': _now(), 'meta': meta})
        save_thread(uname, tid, turns)
    except Exception as e:
        print(f"[prometheus] persist failed for {uname}: {e}")
    # Corpus catalog (2026-10-05): every figure this reply states is
    # banked under its subject, so the next answer or build on the
    # subject sees it. Off the request path; fail-safe.
    try:
        subj = _answer_subject(env, raw)
        if subj and env.get('text') and env.get('kind') not in ('clarify', 'question'):
            from migration import corpus_catalog as _cc
            _cc.record_answer_async(subj, env['text'], user=uname, thread_id=tid or '')
    except Exception as e:
        print(f"[prometheus] corpus catalog bank skipped: {e}")


def _answer_subject(env, raw):
    """The subject a reply is about, from the decision, the ask hint,
    or the bound page."""
    for src in (env.get('decision') or {}, raw if isinstance(raw, dict) else {}):
        for k in ('subject', 'bind_subject', 'subject_name'):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    try:
        from flask import g as _g
        v = getattr(_g, '_pm_ask_subject', None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception:
        pass
    return ''


# ------------------------------------------------------------------- jobs

def _job_prefixes():
    out = []
    for cap, jtype in (('read_prefix', 'read'), ('deck_prefix', 'deck'),
                       ('bpiq_prefix', 'bpiq'), ('jiq_prefix', 'jiq'),
                       ('fw_prefix', 'fw'), ('aiq_prefix', 'aiq')):
        if host.has(cap):
            out.append((getattr(host, cap), jtype))
    return out


def job_status(user, job_id, *, via='session'):
    """Look a job up across every job store. Returns (dict, status)."""
    jid = str(job_id or '').strip()
    if not jid or len(jid) > 64 or not all(c.isalnum() or c in '-_' for c in jid):
        return {'success': False, 'error': 'bad job id'}, 400
    s3 = host.s3_client
    for prefix, jtype in _job_prefixes():
        try:
            obj = s3.get_object(Bucket=host.bucket, Key=f'{prefix}{jid}.json')
            payload = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            continue
        if not host.job_owner_ok(payload.get('user'), user):
            return {'success': False, 'error': 'not your job'}, 403
        status = str(payload.get('status') or 'running').lower()
        out = {'success': True, 'job': {'id': jid, 'type': jtype,
                                        'status': status}}
        if status in ('done', 'complete', 'completed', 'ready', 'held'):
            # The status document is transport; the read (or the deck
            # link, or where the tool output landed) is the content.
            # Wrapping the document itself handed API callers the
            # user's own question as the result text (2026-10-02 RCA).
            content = seams.unwrap(seams.tag_job(dict(payload), jtype))
            out['result'] = envelope.wrap(
                content, surface=('deck' if jtype == 'deck' else 'analyze'),
                decision={'reason': 'job_result'}, via=via)
        elif status in ('error', 'failed'):
            out['result'] = {'kind': 'error', 'text': (
                payload.get('reply') or payload.get('error')
                or 'That one did not finish. Ask again and I will rerun it.')}
            if via == 'api_key':
                out['result']['text'] = envelope._INFRA_RX.sub(
                    '', out['result']['text']).strip()
        return out, 200
    return {'success': False, 'error': 'unknown job'}, 404
