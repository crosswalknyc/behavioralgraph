"""HTTP surface for Prometheus: /api/prometheus/v1/*

One set of routes for every client. A caller authenticates with the
dashboard session cookie (widget, standalone web app) or with
``X-Crosswalk-API-Key`` (customer API, scripts, a native app). Both
resolve to the same user record, so the same wallet, the same product
access, and the same threads apply no matter which door was used.

Routes
------
POST /ask                 run one ask (see service.ask for the body)
GET  /threads             list the caller's threads
POST /threads             start a thread  {title?, activate?}
GET  /threads/<tid>       one thread's turns
GET  /jobs/<job_id>       poll a background read / deck / tool job
GET  /understand?text=..  the routing decision only (session callers;
                          used by the eval set and the widget's dev view)
GET  /health              liveness + version

Every handler is wrapped by the host's route guard (calm reply on an
unhandled error, ops email) and the ask logger, the same two wrappers
the legacy chat routes use, so observability is unchanged.
"""
import os
from functools import wraps

from flask import Blueprint, g, jsonify, make_response, request

from . import __version__
from .host import host
from . import service
from . import understand

bp = Blueprint('prometheus_api', __name__, url_prefix='/api/prometheus/v1')


# ------------------------------------------------------------------ CORS
# A standalone app on its own host needs to call this surface from the
# browser. Origins are allow-listed by env (comma-separated); nothing
# configured means same-origin only, exactly as before. Preflight
# (OPTIONS) is answered here without touching auth; real requests
# carry a session cookie or X-Crosswalk-API-Key.
def _allowed_origins():
    raw = os.environ.get('PROMETHEUS_ALLOWED_ORIGINS', '') or ''
    return {o.strip().rstrip('/') for o in raw.split(',') if o.strip()}


def _cors_origin():
    origin = (request.headers.get('Origin') or '').strip().rstrip('/')
    if not origin:
        return None
    allowed = _allowed_origins()
    if origin in allowed:
        return origin
    # A wildcard entry means any origin, but still with credentials
    # echoed per origin (never a literal '*' with credentials).
    if '*' in allowed:
        return origin
    return None


@bp.before_request
def _preflight():
    if request.method == 'OPTIONS':
        resp = make_response('', 204)
        return resp
    return None


@bp.after_request
def _cors_headers(resp):
    origin = _cors_origin()
    if origin:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Access-Control-Allow-Headers'] = (
            'Content-Type, X-Crosswalk-API-Key, X-Requested-With')
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Max-Age'] = '600'
        resp.headers['Vary'] = 'Origin'
    return resp


def _guarded(label, log_ask=False):
    """Apply the host's route guard (and ask logger) at call time, so
    the module imports cleanly before the host is bound."""
    def deco(fn):
        @wraps(fn)
        def inner(*a, **k):
            f = fn
            if log_ask and host.has('ask_logged'):
                f = host.ask_logged(label)(f)
            if host.has('route_guard'):
                f = host.route_guard(label)(f)
            return f(*a, **k)
        return inner
    return deco


def _auth():
    """(user, via, error_response). API keys allowed: one wallet."""
    user, err = host.gate(allow_api_key=True)
    if err:
        return None, None, err
    via = 'api_key' if (user or {}).get('_auth_via') == 'api_key' else 'session'
    try:
        # The ask log and the per-ask notify read the session user;
        # an API-key caller has none, so hand them the gated identity.
        g._pm_ask_user = service.username_of(user)
    except Exception:
        pass
    return user, via, None


def _body():
    try:
        return request.get_json(force=True, silent=True) or {}
    except Exception:
        return {}


@bp.route('/health', methods=['GET'])
def health():
    return jsonify({'success': True, 'service': 'prometheus',
                    'version': __version__, 'bound': host.bound})


@bp.route('/ask', methods=['POST'])
@_guarded('prometheus/ask', log_ask=True)
def ask():
    user, via, err = _auth()
    if err:
        return err
    body = _body()
    if via == 'api_key':
        body.setdefault('client', 'api')
    env, status = service.ask(user, body, via=via)
    return jsonify(env), status


@bp.route('/understand', methods=['GET', 'POST'])
@_guarded('prometheus/understand')
def understand_only():
    user, via, err = _auth()
    if err:
        return err
    if via == 'api_key':
        return jsonify({'success': False,
                        'error': 'not available on this key'}), 403
    body = _body() if request.method == 'POST' else {}
    text = (body.get('text') or request.args.get('text') or '').strip()
    has_ctx = bool(body.get('context') or body.get('has_ctx')
                   or request.args.get('has_ctx'))
    try:
        open_tabs = int(body.get('open_tabs') or request.args.get('open_tabs') or 0)
    except (TypeError, ValueError):
        open_tabs = 0
    d = understand.decide(text, has_ctx=has_ctx, mode=body.get('mode'),
                          extra=body.get('extra'), open_tabs=open_tabs,
                          deck_in_flight=bool(body.get('deck_in_flight')))
    return jsonify({'success': True, 'decision': d})


@bp.route('/routing-table', methods=['GET'])
@_guarded('prometheus/routing-table')
def routing_table():
    """The declarative routing decision table the server routes by and
    the widget acts on (2026-10-02 RCA). Session callers only."""
    user, via, err = _auth()
    if err:
        return err
    if via == 'api_key':
        return jsonify({'success': False,
                        'error': 'not available on this key'}), 403
    from . import routing_table as _rt
    return jsonify({'success': True, 'rows': _rt.as_public(),
                    'client_actions': sorted(_rt.CLIENT_ACTIONS)})


@bp.route('/threads', methods=['GET'])
@_guarded('prometheus/threads')
def threads_list():
    user, via, err = _auth()
    if err:
        return err
    out = service.list_threads(service.username_of(user))
    return jsonify({'success': True, **out})


@bp.route('/threads', methods=['POST'])
@_guarded('prometheus/threads')
def threads_new():
    user, via, err = _auth()
    if err:
        return err
    body = _body()
    tid, idx = service.new_thread(
        service.username_of(user),
        activate=bool(body.get('activate')),
        title=str(body.get('title') or '').strip() or None)
    return jsonify({'success': True, 'thread_id': tid,
                    'active': idx.get('active')})


@bp.route('/threads/<tid>', methods=['GET'])
@_guarded('prometheus/threads')
def thread_get(tid):
    user, via, err = _auth()
    if err:
        return err
    uname = service.username_of(user)
    safe_t = ''.join(c for c in str(tid) if c.isalnum() or c in '-_')
    if not safe_t or not service.thread_exists(uname, safe_t):
        return jsonify({'success': False, 'error': 'unknown thread'}), 404
    turns = service.load_thread(uname, safe_t)
    if via == 'api_key':
        turns = [{'role': t.get('role'), 'text': t.get('text'),
                  'ts': t.get('ts')} for t in (turns or [])
                 if isinstance(t, dict)]
    return jsonify({'success': True, 'thread_id': safe_t, 'turns': turns})


@bp.route('/jobs/<job_id>', methods=['GET'])
@_guarded('prometheus/jobs')
def job_get(job_id):
    user, via, err = _auth()
    if err:
        return err
    out, status = service.job_status(user, job_id, via=via)
    return jsonify(out), status
