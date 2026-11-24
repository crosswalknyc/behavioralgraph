"""Who may ask and who pays: the gate refusal, the access gate, the funding check and funds gate, and the usage and attribution extras merged onto a call.

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
import uuid
import traceback
from prometheus import faults as _faults
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import plan_allowance as _plan

# <<moved>>
# <<block: 1>>
def _pm_gate_refusal(kind):
    """Build a partner-safe 403 for a mode-blocked chatbot request.

    `kind` is 'analyze' when the caller tried to reach a read / deck
    surface but their mode is 'pull'; 'pull' when they tried to reach
    a new-build surface but their mode is 'analysis'. The reply carries
    `guidance=True` so the widget renders it as a plain agent bubble
    (see the `data.guidance && data.error` branch in the analyze /
    approve / interpret handlers). No internal vocabulary."""
    if kind == 'analyze':
        msg = ('This account covers new profile pulls only. To '
               'unlock analysis of existing profiles, please '
               'contact your account manager.')
    else:
        msg = ('This account covers analysis only. To unlock the '
               'ability to pull new profiles, please contact your '
               'account manager.')
    return jsonify({
        'success': False,
        'guidance': True,
        'error': msg,
    }), 403


# <<block: 2>>
def _pm_access_gate(user):
    """Prometheus tier gate for the analysis surfaces (2026-08-26).

    Admin sets prometheus_access per user: 'full' (analysis and
    everything else - the default; every existing user resolves here)
    or 'pulls_only' (Profile IQ / Subscriber IQ builds only). A
    pulls_only user who has NOT opted into pay as you go gets Jenna's
    exact offer copy with Yes/No chips instead of the analysis; the
    affordance itself never hides. Returns None when the user may
    proceed."""
    import pay_per_use as ppu
    if ppu.analysis_allowed(user):
        return None
    _C._pm_ask_hint(outcome='declined_not_subscribed')
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': ppu.OFFER_MESSAGE,
        'followups': ['Yes', 'No'],
        'pay_per_use_offer': True,
        'offer_deck': False, 'deck_angle': None})


# <<block: 3>>
def _pm_has_funding(user, username, data=None):
    """Pure funding decision for the Prometheus ask surfaces (Jenna
    2026-09-16: "make sure that no one gets free metered usage of
    prometheus if they do not have any credits/money in their account
    unless they've been set to unlimited").

    Returns (allowed, reason). Allowed when ANY funding source is open:
      * unlimited: personal credits == -1, the admin 'unlimited' flag,
        or an unlimited company pool reachable by this user
        (check_user_credits returns -1),
      * credits: personal / company-pool credits remaining > 0 within
        the user's ceiling,
      * money: the resolved billing subject (personal wallet or the
        company-shared wallet) can absorb one metered answer at the
        configured rate - covers prepaid balance, auto-reload with a
        card on file, and monthly-invoice room.
    Role is not a free pass. Liz (super_admin, $0, 0 credits) still
    ran Prometheus until this check treated her like any other
    drained account (Jenna 2026-09-16).
    Blocked only when every source above is exhausted."""
    user = user or {}
    try:
        import wallet as _w
    except Exception:
        traceback.print_exc()
        return False, 'wallet_unavailable'
    try:
        if _w.is_unlimited(user):
            return True, 'unlimited'
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:
        if data is None:
            data = _H.load_users()
        if _w.opening_funding_unmet(user, data):
            return False, 'opening_topup'
    except Exception:
        traceback.print_exc()
    try:
        has_cr, left = _H.check_user_credits(username)
        if left == -1:
            return True, 'unlimited_pool'
        if has_cr and left > 0:
            return True, 'credits'
    except Exception:
        traceback.print_exc()
    try:
        if data is None:
            data = _H.load_users()
        subject, _kind, _key = _w.resolve_billing_subject(user, data)
    except Exception:
        subject = user
    try:
        rate = float(_w.metered_answer_usd())
    except Exception:
        rate = 2.10
    try:
        ok, _why = _w.wallet_can_absorb(subject or {}, rate)
        if ok:
            return True, 'wallet'
    except Exception:
        traceback.print_exc()
    return False, 'no_funding'


# <<block: 4>>
def _pm_funds_gate(user):
    """Prometheus funds gate: None when the user may proceed, else the
    partner-safe paused reply. Session surfaces only. Fails open on
    internal lookup errors (a billing-file hiccup must never take
    Prometheus down for funded users); the pure decision lives in
    _pm_has_funding."""
    try:
        uname = (session.get('username') or (user or {}).get('username')
                 or '').strip()
        allowed, reason = _C._pm_has_funding(user, uname)
        if allowed:
            return None
        try:
            _C._pm_ask_hint(outcome='declined_no_funds')
        except Exception as _fx:
            _faults.note('chat', _fx)
        print(f"[prometheus] funds gate blocked {uname!r} ({reason})")
        if reason == 'opening_topup':
            try:
                import lock_hit_notify as _lhn
                _lhn.notify_opening_lock_hit(user, uname, where="prometheus")
            except Exception:
                pass
        reply = (_H.opening_funds_message(user) if reason == 'opening_topup'
                 else _plan.out_of_plan_reply(_H, uname, user, _H.NO_FUNDS_MESSAGE))   # a plan allowance: the plain top-up ask, one notice
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': reply,
            'followups': [],
            'no_funds': True,
            'top_up_url': '/wallet',
            'top_up_label': 'Add funds',
            'offer_deck': False, 'deck_angle': None})
    except Exception:
        traceback.print_exc()
        return None


# <<block: 5>>
def _pm_usage_extras(user):
    """Attribution extras for a pay-as-you-go user's model calls.


    Returns None when billing_active is false (unlimited, super_admin,
    or pulls_only without the opt-in). Everyone else, including
    full-tier dashboard users and Kartel, gets username, email, the
    active session id (30-minute idle window), and a per-request id
    so one ask that fans out into several model calls still counts
    as one ask on the bill."""
    import pay_per_use as ppu
    try:
        if not ppu.billing_active(user):
            return None
        email = (user.get('email') or '').strip().lower()
        uname = (session.get('username') or user.get('username')
                 or '').strip()
        rid = None
        try:
            from flask import g as _g
            rid = getattr(_g, '_pm_ppu_request_id', None)
            if not rid:
                rid = uuid.uuid4().hex[:12]
                _g._pm_ppu_request_id = rid
        except Exception:
            rid = uuid.uuid4().hex[:12]
        return {'user': uname or email,
                'user_email': email or uname,
                'session_id': ppu.touch_session(email or uname),
                'request_id': rid,
                'pay_per_use': True}
    except Exception:
        traceback.print_exc()
        return None


# <<block: 6>>
def _pm_attrib_extras():
    """Always-on per-user attribution for a dashboard (Prometheus) model
    call, independent of pay-as-you-go billing.

    Returns {'user', 'user_email'} for the logged-in user when a request
    context is present, else {} (background threads such as the deck job
    have no request context and pass attribution explicitly instead).

    This tags every Prometheus chat record with WHO caused it so the
    daily spend email can break Prometheus cost out per user. It NEVER
    sets pay_per_use, so billing and the credit gate (both keyed off
    _pm_usage_extras returning None for subscribed users) are untouched.
    Never raises."""
    try:
        from flask import has_request_context
        if not has_request_context():
            return {}
        u = _H.get_current_user() or {}
        email = (u.get('email') or '').strip().lower()
        uname = (session.get('username') or u.get('username') or '').strip()
        if not (email or uname):
            return {}
        return {'user': uname or email, 'user_email': email or uname}
    except Exception:
        return {}


# <<block: 7>>
def _pm_merge_extras(*parts):
    """Merge attribution dicts left-to-right (later parts win on key
    collisions). Returns a dict, or None when nothing merged (None keeps
    the untagged code path in _run_nflx_claude_agent). Never raises."""
    out = {}
    for p in parts:
        if isinstance(p, dict):
            for k, v in p.items():
                if v is not None:
                    out[k] = v
    return out or None


__all__ = ['_pm_gate_refusal', '_pm_access_gate', '_pm_has_funding', '_pm_funds_gate', '_pm_usage_extras', '_pm_attrib_extras', '_pm_merge_extras']
