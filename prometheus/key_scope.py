"""A key a chat message carries is checked before anything reads it (2026-11-18).

The page context and the thread history can both arrive in the request,
so every storage key in them is the sender's word, not ours. Before
this module the "email me this file" order attached whatever key the
page context named as the open profile, and the "download that" order
signed a link to whatever a turn's link pointed at, in any bucket.

One place decides:

  ``allowed_for(user, uname, host)``  the check ``(kind, key) -> bool``:
      a profile key is a profile file this reader can open (the profile
      page's own gate); a journey key is a run this reader's Digital
      Journey list shows; a tracker key is a live tracker this reader
      can open. Anything else is a no.
  ``page_context(ctx, user, uname, host)``  the context with every key
      that fails the check removed, as if nothing were open there.

WHAT a key may be is ``file_access``; WHO may open it is the host's own
access helper. A refusal is written to the server log and the reader is
told nothing about it. Never raises.
"""
from __future__ import annotations

import file_access as _fa

__all__ = ['allowed_for', 'allowed_here', 'page_context', 'install', 'scope_request_body']


def _is_probe():
    try:
        from . import faults as _faults
        return bool(_faults.is_probe())
    except Exception:
        return False


def _journey_ok(user, uname, host, key):
    full = key
    if isinstance(key, str) and key.startswith('__demo_'):
        full = f"journey-iq/demos/{key}.json.gz"
    if _fa.servable('journey', full) is None:
        return False
    try:
        if host is not None and host.has('can_open_journey'):
            return bool(host.can_open_journey(user, full))
    except Exception:
        return False
    # no host helper bound: the reader's own journey list decides
    try:
        from . import send_file as _sf
        return any(str(j.get('key')) == full for j in _sf.user_journeys(uname, host, user=user))
    except Exception:
        return False


def allowed_for(user, uname, host):
    """The access check for keys this reader's request carries."""
    probe = _is_probe()

    def allowed(kind, key):
        try:
            if not isinstance(key, str) or not key:
                return False
            if kind == 'profile':
                if _fa.servable('profile', key) is None:
                    return False
                if probe:
                    return True   # the golden set and the canary read the library as the staff seat they stand in for
                if host is not None and host.has('can_open_profile') and host.can_open_profile(user, key):
                    return True
                # a key caller has no session for the page's gate; the
                # partner surface's own catalog rule decides for it
                if (isinstance(user, dict) and user.get('_auth_via') == 'api_key'
                        and host is not None and host.has('partner_key_ok')):
                    return bool(host.partner_key_ok(key))
                return False
            if kind == 'journey':
                if probe and _fa.servable('journey', key) is not None:
                    return True
                return _journey_ok(user, uname, host, key)
            if kind == 'subscriber':
                if _fa.servable('subscriber', key) is None:
                    return False
                if probe:
                    return True
                if host is not None and host.has('can_open_subiq'):
                    return bool(host.can_open_subiq(user, key, ''))
                return False
            if kind == 'twin':
                # shape only here; the twin lane asks company and access itself
                return _fa.servable('twin', key) is not None
            return False
        except Exception:
            return False
    return allowed


def allowed_here(user, uname=''):
    """``allowed_for`` on the bound host, for callers inside the app."""
    try:
        from .host import host as _host
    except Exception:
        _host = None
    return allowed_for(user, uname, _host)


def page_context(ctx, user, uname, host):
    """``ctx`` with every key this reader may not open removed."""
    if not isinstance(ctx, dict):
        return ctx
    clean, refused = _fa.scrub_page_context(ctx, allowed_for(user, uname, host))
    for k in refused:
        _fa.refuse('chat_context', k, uname)
    return clean


# The chat surfaces whose JSON body can carry a page context.
_SCOPED_PATHS = ('/api/brief-chat/', '/api/synth-chat/', '/api/prometheus/')


def scope_request_body(body, user, uname, host):
    """Check the page context of a parsed request body in place. Returns
    the number of context fields checked."""
    n = 0
    if not isinstance(body, dict):
        return n
    for f in ('page_context', 'context'):
        if isinstance(body.get(f), dict):
            body[f] = page_context(body[f], user, uname, host)
            n += 1
    return n


def install(app, host_module):
    """One hook ahead of every chat route: the page context a signed-in
    reader's request carries is checked once, before any handler reads
    it. The parsed body is the one every later ``request.get_json()``
    returns, so the handlers see the checked copy. A request with no
    session is left to the gates at the point of reading (the file
    lanes ask ``allowed_for`` themselves). Never raises, never blocks a
    request."""
    try:
        from flask import request, session
        from .host import host as _host
    except Exception:
        return False

    @app.before_request
    def _pm_scope_request_keys():
        try:
            if request.method not in ('POST', 'PUT', 'PATCH'):
                return None
            if not str(request.path or '').startswith(_SCOPED_PATHS):
                return None
            uname = str(session.get('username') or '')
            if not uname:
                return None
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return None
            if not any(isinstance(body.get(f), dict) for f in ('page_context', 'context')):
                return None
            user = host_module.get_current_user() or {}
            scope_request_body(body, user, uname, _host)
        except Exception:
            pass
        return None
    return True
