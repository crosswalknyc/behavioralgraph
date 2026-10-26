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
GET  /usage               per-day calls and metered charges for the key
GET  /schemas/ask         the v1 response JSON Schema (public)
GET  /schemas/ask-request the v1 request JSON Schema (public)
GET  /understand?text=..  the routing decision only (session callers;
                          used by the eval set and the widget's dev view)
GET  /health              liveness + version

Every handler is wrapped by the host's route guard (calm reply on an
unhandled error, ops email) and the ask logger, the same two wrappers
the legacy chat routes use, so observability is unchanged.
"""
import os
import time
from functools import wraps

from flask import Blueprint, g, jsonify, make_response, redirect, request, send_from_directory

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
        if via == 'api_key':
            # The legacy /api/brief-chat gate stamps the key owner on g;
            # this door never did, so the in-service code could not tell
            # a key ask from a dashboard ask (its notify fired unmarked
            # and twice, 2026-10-21). The owner flag is what the notify,
            # the ask log and the 'via' marker read.
            g._pm_api_key_owner = service.username_of(user)
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
    """Liveness for everyone; with ``?deep=1`` the dependency checks a
    partner can poll (storage, reasoning, records) as ok / degraded /
    down, plain names only."""
    out = {'success': True, 'service': 'prometheus', 'version': __version__, 'bound': host.bound}
    if (request.args.get('deep') or '').strip() in ('1', 'true', 'yes'):
        from . import partner_api as _papi
        deep, status = _papi.handle_health()
        out.update(deep)
        return jsonify(out), status
    return jsonify(out)


@bp.route('/desktop/manifest', methods=['GET'])
def desktop_manifest():
    """What the installed desktop app reads at launch (public: a version
    number and relative paths). See prometheus/desktop.py."""
    from . import desktop as _dk
    resp = make_response(jsonify(_dk.manifest_doc()), 200)
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp


@bp.route('/desktop/ui/', methods=['GET'])
@bp.route('/desktop/ui/<path:asset>', methods=['GET'])
def desktop_ui(asset='index.html'):
    """The desktop app's page and its assets, served from the service so
    the window updates with every deploy. Static files only."""
    from . import desktop as _dk
    name = str(asset or 'index.html').strip('/') or 'index.html'
    if '..' in name:
        return jsonify({'error': 'not found'}), 404
    resp = make_response(send_from_directory(_dk.UI_DIR, name))
    resp.headers['Cache-Control'] = 'no-cache' if name.endswith('.html') else 'public, max-age=86400'
    return resp


@bp.route('/desktop/download/<which>', methods=['GET'])
def desktop_download(which):
    """Hands out a platform build through a short presigned link; 404
    when that build is not published."""
    from . import desktop as _dk
    url = _dk.presigned_download(which)
    if not url:
        return jsonify({'error': 'that build is not available'}), 404
    return redirect(url, code=302)


@bp.route('/schemas/ask', methods=['GET'])
@bp.route('/schemas/ask-request', methods=['GET'])
def schema_ask():
    """The JSON Schema a partner codes against (public: a schema has no
    data in it). /schemas/ask is the response, /schemas/ask-request the
    request body."""
    import json as _json
    name = 'ask-request.v1.json' if request.path.endswith('ask-request') else 'ask.v1.json'
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schemas', name)
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            doc = _json.load(fh)
    except Exception:
        return jsonify({'error': 'schema unavailable'}), 503
    resp = make_response(jsonify(doc), 200)
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@bp.route('/usage', methods=['GET'])
@_guarded('prometheus/usage')
def usage():
    """Per-day calls and metered charges for the calling key
    (``?from=YYYY-MM-DD&to=YYYY-MM-DD``, default the last 30 days), the
    current balance and the key's policy. Session callers get their own
    seat's view the same way."""
    user, via, err = _auth()
    if err:
        return err
    from . import partner_api as _papi
    out, status = _papi.handle_usage(user, dict(request.args))
    return jsonify(out), status


@bp.route('/ask', methods=['POST'])
@_guarded('prometheus/ask', log_ask=True)
def ask():
    user, via, err = _auth()
    if err:
        return err
    body = _body()
    if via == 'api_key':
        # the v1 contract (2026-10-14): scopes, limits, idempotency, the
        # daily cap, the partner belt over every string. One engine
        # underneath: partner_api calls the same service.ask.
        from . import partner_api as _papi
        v1, status, extra = _papi.handle_ask(user, body, dict(request.headers), raw_len=request.content_length)
        resp = make_response(jsonify(v1), status)
        for k, v in (extra or {}).items():
            resp.headers[k] = v
        return resp
    _t0 = time.monotonic()
    env, status = service.ask(user, body, via=via)
    # Over 45 seconds the widget has promised an email as well as the
    # chat (Jenna 2026-10-14); keep the promise, then close the live line.
    try:
        from . import progress as _pg
        _emailed = False
        if time.monotonic() - _t0 >= _pg.LONG_WAIT_S and via == 'session':
            _emailed = _email_long_answer(user, body, env)
        _pg.done(emailed=_emailed)
        if isinstance(env, dict):
            env['waited_s'] = int(time.monotonic() - _t0)
            env['emailed'] = bool(_emailed)
    except Exception:
        pass
    return jsonify(env), status


def _email_long_answer(user, body, env):
    """The finished answer to the reader's inbox when the ask ran past
    the long-wait mark. Only a real answer with text; never a brief card
    or a clarifier. Returns True when sent."""
    try:
        raw = (env or {}).get('raw') if isinstance(env, dict) else None
        if not isinstance(raw, dict):
            return False
        reply = str(raw.get('reply') or raw.get('answer') or '').strip()
        if not reply or raw.get('action') not in (None, 'answer') or raw.get('guidance') or raw.get('clarify') or raw.get('spec_draft') or raw.get('spec_drafts'):
            return False
        to = str((user or {}).get('email') or '').strip()
        if '@' not in to:
            return False
        q = ' '.join(str((body or {}).get('text') or '').split())
        from .outbound_mail import send_user_email
        from .guards import scrub_method_language as _scrub
        subject = f"Prometheus: {q[:70]}" if q else 'Prometheus: your answer'
        text = f"You asked: {q}\n\n{reply}\n\nThis answer is also in your chat thread on the dashboard.\n\nPrometheus\nCrosswalk"
        html = None
        try:
            from welcome_email import render_card_html
            paras = [p_ for p_ in reply.split('\n') if p_.strip()]
            html = render_card_html('Your answer is ready.', sub=q[:140], lines=paras[:40] + ['This answer is also in your chat thread on the dashboard.'], title=subject)
        except Exception:
            html = None
        rec = send_user_email(to=to, subject=subject, body=_scrub(text), html=html, instructed=True, caller='long_ask')
        return bool(rec.get('sent'))
    except Exception:
        return False


@bp.route('/progress/<trace>', methods=['GET'])
def progress_get(trace):
    """The live line for an ask in flight (the widget polls while it
    waits; 2026-10-14). Session or key, no logging, no model."""
    user, via, err = _auth()
    if err:
        return err
    from . import progress as _pg
    doc = _pg.get(trace) or {}
    return jsonify({'success': True, 'trace': str(trace)[:32], 'label': doc.get('label') or '',
                    'elapsed_s': int(time.time() - float(doc.get('started') or time.time())) if doc else 0,
                    'long_wait': _pg.long_wait(doc), 'done': bool(doc.get('done')), 'emailed': bool(doc.get('emailed')),
                    'lines': [l.get('label') for l in (doc.get('lines') or [])][-6:]})


GOLDEN_USER = {'username': 'canary:golden', 'email': '', 'role': 'super_admin',
               'has_chatbot_profile_iq_access': True, 'unlimited': True, 'company': 'Crosswalk'}


RENDER_PLAYWRIGHT_BROWSERS = '/opt/render/project/.cache/ms-playwright'


def _playwright_browsers_default():
    """Point Playwright at the browser build.sh installed when the service
    env did not (2026-10-21, SiriusXM): the path has to be set before the
    playwright module is first imported, and only when the directory is
    really there, so a machine without it keeps Playwright's own default."""
    if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH') and os.path.isdir(RENDER_PLAYWRIGHT_BROWSERS):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = RENDER_PLAYWRIGHT_BROWSERS


@bp.route('/internal/streamscout', methods=['POST'])
def streamscout_relay():
    """Run one StreamScout platform from THIS deployment and return its
    rows (2026-10-15, Jess + Spike's New Heights note). Pandora answers
    only from a US address: the build server sits in Germany and its
    anonymous login comes back INVALID_REQUEST, while the dashboard runs
    in Oregon. The build server relays US-only platforms here. Shared
    secret auth (SYNTH_QUEUE_SECRET); one platform per call so a request
    stays short."""
    import hmac as _hmac
    secret = str(getattr(host, 'queue_secret', '') if host.has('queue_secret') else '')
    given = str(request.headers.get('X-Synth-Auth') or '')
    if not secret or not _hmac.compare_digest(secret, given):
        return jsonify({'error': 'not found'}), 404
    body = _body()
    title = str(body.get('title') or '').strip()
    platform = str(body.get('platform') or '').strip().lower()
    if not title or not platform or not platform.isalnum():
        return jsonify({'error': 'title and platform required'}), 400
    # the placement the content map already holds there (a watch uuid / url):
    # the resolver opens its page directly instead of discovering the title
    hint = str(body.get('url') or '').strip()[:400] or None
    import sys as _sys
    ss_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'streamscout')
    if os.path.isdir(ss_dir) and ss_dir not in _sys.path:
        _sys.path.insert(0, ss_dir)
    _playwright_browsers_default()
    try:
        import streamscout_query as _ssq_mod
        _ssq = _ssq_mod.streamscout_query
    except Exception as e:
        return jsonify({'error': f'streamscout unavailable: {type(e).__name__}'}), 503
    t0 = time.monotonic()
    kind = str(body.get('kind') or 'series')
    show = str(body.get('show') or '') or None
    flagged, ambiguous = [], False
    try:
        report = getattr(_ssq_mod, 'query_report', None)
        if callable(report):
            # the full account: a resolver that raised is 'unverified', never a zero;
            # a near title HELD for a human (2026-10-26, HowTo 5e) and a bare title
            # that names more than one work (5g) ride back too, so the build server
            # never reads a hold as an absence
            rep = report(title, platforms=[platform], kind=kind, show=show, url=hint) or {}
            rows = rep.get('rows') or []
            failed = dict(rep.get('failed') or {})
            flagged = [str(x) for x in ((rep.get('flagged') or {}).get(platform) or [])]
            ambiguous = bool(rep.get('ambiguous'))
        else:
            rows = _ssq(title, platforms=[platform], kind=kind, show=show, url=hint) or []
            failed = {}
    except Exception as e:
        # the resolver raised: the platform could not be checked from here
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {str(e)[:200]}', 'rows': [],
                        'unverified': True}), 200
    out = {'success': True, 'platform': platform, 'rows': [r for r in rows if isinstance(r, dict)],
           'unverified': bool(failed.get(platform)) and not rows,
           'flagged': flagged, 'ambiguous': ambiguous,
           'elapsed_s': round(time.monotonic() - t0, 2)}
    if failed.get(platform):
        out['error'] = str(failed[platform])[:200]
    return jsonify(out)


@bp.route('/internal/golden-ask', methods=['POST'])
def golden_ask():
    """One golden-set ask through the real door on THIS deployment, as a
    probe user: no thread write, no charge, no mail, no ask-log row
    (audit item 2, 2026-10-14). Shared-secret auth, the build server's
    SYNTH_QUEUE_SECRET, so only the nightly grader can call it. Returns
    the envelope plus the reading and the route hints the ask produced."""
    import hmac as _hmac
    secret = str(getattr(host, 'queue_secret', '') if host.has('queue_secret') else '')
    given = str(request.headers.get('X-Synth-Auth') or '')
    if not secret or not _hmac.compare_digest(secret, given):
        return jsonify({'error': 'not found'}), 404
    body = _body()
    text = str(body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    ask_body = {'text': text, 'history': body.get('history') or [], 'client': 'golden', 'persist': False}
    if isinstance(body.get('context'), dict):
        ask_body['context'] = body['context']
    if body.get('surface'):
        ask_body['surface'] = body['surface']
    try:
        g._pm_ask_user = GOLDEN_USER['username']
    except Exception:
        pass
    t0 = time.monotonic()
    env, status = service.ask(dict(GOLDEN_USER), ask_body, via='canary')
    out = {'success': True, 'status': status, 'env': env, 'elapsed_s': round(time.monotonic() - t0, 2)}
    try:
        out['reading'] = getattr(g, '_pm_reading', None)
        out['route'] = getattr(g, '_pm_ask_route', None)
        out['outcome'] = getattr(g, '_pm_ask_outcome', None)
    except Exception:
        pass
    return jsonify(out)


@bp.route('/upload', methods=['POST'])
@_guarded('prometheus/upload')
def upload():
    """A document joins the thread (2026-10-06, Jenna: "upload a document
    to it. like a powerpoint to be edited, or pdf or other data").
    Multipart: file (one or many), optional thread_id. Session only.
    The file and its text land under the asker's thread; the reply
    names what arrived and what to do next; both turns persist."""
    user, via, err = _auth()
    if err:
        return err
    if via == 'api_key':
        from . import contract_v1 as _C
        return jsonify(_C.declined('unsupported', 'Documents are not available on the key surface yet. Ask the question in text.')), 200
    from . import documents as _docs
    uname = service.username_of(user)
    tid = str(request.form.get('thread_id') or '').strip() or None
    if tid and not service.thread_exists(uname, tid):
        tid = None
    if not tid:
        tid = service.active_thread_id(uname)
    files = [f for f in request.files.getlist('file') if f and f.filename]
    if not files:
        return jsonify({'success': False, 'error': 'Attach a file: a PowerPoint, PDF, Word document, sheet or image.'}), 400
    recs, errors = [], []
    for f in files[:5]:
        try:
            data = f.read()
            if not data:
                errors.append(f"{f.filename}: empty file"); continue
            if not _docs.kind_of(f.filename):
                errors.append(f"{f.filename}: I take .pptx, .pdf, .docx, .xlsx, .csv, .txt, .md and images"); continue
            recs.append(_docs.store_upload(uname, tid, f.filename, data))
        except Exception as e:
            errors.append(f"{f.filename}: {str(e)[:120]}")
    if not recs:
        return jsonify({'success': False, 'error': '; '.join(errors) or 'That file did not land. Try again.'}), 400
    reply, chips = _docs.ack(recs[-1])
    if len(recs) > 1:
        reply = f"Got {len(recs)} files: " + ', '.join(r['name'] for r in recs) + '. ' + reply.split('. ', 1)[-1]
    if errors:
        reply += ' Not taken: ' + '; '.join(errors) + '.'
    # Persist both turns so the thread knows what it holds.
    try:
        history = service.load_thread(uname, tid) if tid else []
        now = service._now()
        history.append({'role': 'user', 'text': 'Uploaded ' + ', '.join(r['name'] for r in recs), 'ts': now,
                        'meta': {'kind': 'upload', 'attachments': [
                            {k: r.get(k) for k in ('upload_id', 'name', 'kind', 'summary')} for r in recs]}})
        history.append({'role': 'agent', 'text': reply, 'ts': now,
                        'meta': {'kind': 'upload_ack', 'options': [{'label': c, 'send': c} for c in chips]}})
        if tid:
            service.save_thread(uname, tid, history)
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        host.ask_hint(route='document_upload', outcome='answered')
    except Exception:
        pass
    return jsonify({'success': True, 'thread_id': tid, 'reply': reply,
                    'options': [{'label': c, 'send': c} for c in chips],
                    'attachments': [{k: r.get(k) for k in ('upload_id', 'name', 'kind', 'summary', 'pages', 'rows')} for r in recs],
                    'errors': errors})


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


@bp.route('/schemas', methods=['GET'])
@_guarded('prometheus/schemas')
def schemas():
    """The schema versions this build speaks on every seam (reply
    envelope, model transport, job documents, queue payloads). A
    client checks the envelope tag against this before trusting a
    shape (2026-10-02 RCA W3)."""
    user, via, err = _auth()
    if err:
        return err
    from . import seams as _seams
    return jsonify({'success': True, 'version': __version__,
                    'schemas': _seams.versions()})


@bp.route('/threads', methods=['GET'])
@_guarded('prometheus/threads')
def threads_list():
    user, via, err = _auth()
    if err:
        return err
    out = service.list_threads(service.username_of(user),
                               subject_key=str(request.args.get('subject_key') or '').strip() or None)
    if via == 'api_key':
        # a key sees its own conversations, never the owner's dashboard threads
        from . import partner_keys as _K
        pref = f"api-{_K.key_id(user)[:8]}-"
        convs = [{'conversation_id': t['id'][len(pref):], 'turns': t.get('turns', 0), 'updated': t.get('updated')}
                 for t in (out.get('threads') or []) if str(t.get('id') or '').startswith(pref)]
        return jsonify({'schema': 'prometheus.conversations.v1', 'conversations': convs})
    return jsonify({'success': True, **out})


@bp.route('/threads', methods=['POST'])
@_guarded('prometheus/threads')
def threads_new():
    user, via, err = _auth()
    if err:
        return err
    if via == 'api_key':
        from . import contract_v1 as _C
        return jsonify(_C.declined('unsupported', 'Conversations start with the first ask: send a conversation_id on POST /ask.')), 200
    body = _body()
    bind = body.get('bind')
    if isinstance(bind, dict) or body.get('page_context'):
        # The conversation follows the project (2026-10-21): the thread
        # bound to what the dashboard has open becomes active, created
        # when none exists; an empty key is the general chat.
        from . import thread_binding as _tb
        bind = bind if isinstance(bind, dict) else {}
        sk = str(bind.get('subject_key') or '').strip()
        label = str(bind.get('subject_label') or '').strip()
        product = str(bind.get('product') or '').strip()
        if not sk and body.get('page_context'):
            sk, label, product = _tb.subject_key_for(body.get('page_context'))
        out = service.thread_for_subject(service.username_of(user), sk or '', label or '', product or '', create=True)
        return jsonify({'success': True, 'thread_id': out.get('tid'), 'active': out.get('tid'),
                        'switched': bool(out.get('switched')), 'created': bool(out.get('created')),
                        'binding': out.get('binding'), 'turns': out.get('history') or []})
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
    safe_t = ''.join(c for c in str(tid) if c.isalnum() or c in '-_.')
    if via == 'api_key':
        # the id is the partner's conversation id; only this key's own
        from . import partner_api as _papi, guards as _g
        safe_t = _papi.conversation_thread_id(user, safe_t)
    if not safe_t or not service.thread_exists(uname, safe_t):
        return jsonify({'success': False, 'error': 'unknown thread'}), 404
    turns = service.load_thread(uname, safe_t)
    if via == 'api_key':
        turns = [{'role': t.get('role'), 'text': _g.scrub_partner_text(str(t.get('text') or ''), fallback=''),
                  'ts': t.get('ts')} for t in (turns or []) if isinstance(t, dict)]
        return jsonify({'schema': 'prometheus.conversation.v1', 'conversation_id': str(tid), 'turns': turns})
    return jsonify({'success': True, 'thread_id': safe_t, 'turns': turns})


@bp.route('/jobs/<job_id>/files', methods=['GET'])
@_guarded('prometheus/job_files')
def job_files(job_id):
    """The files a finished job ships with (a journey's workbook, CSV and
    PDF analysis; a deck), listed as API paths the same key downloads."""
    user, via, err = _auth()
    if err:
        return err
    from . import partner_api as _papi
    out, status, extra = _papi.handle_job_files(user, job_id)
    return jsonify(out), status


@bp.route('/jobs/<job_id>/files/<path:name>', methods=['GET'])
@_guarded('prometheus/job_file')
def job_file(job_id, name):
    user, via, err = _auth()
    if err:
        return err
    from . import partner_api as _papi
    data, ctype, fname = _papi.job_file(user, job_id, name)
    if data is None:
        return jsonify({'error': {'scope': 'That job belongs to another key.', 'unknown': 'No such job.', 'not_done': 'Not ready yet.',
                                  'unknown_file': 'No such file on this job.', 'build': 'The file could not be built.'}.get(fname, 'unavailable')}), ctype
    resp = make_response(data, 200)
    resp.headers['Content-Type'] = ctype
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    resp.headers['Cache-Control'] = 'private, max-age=0'
    return resp


@bp.route('/jobs/<job_id>', methods=['GET'])
@_guarded('prometheus/jobs')
def job_get(job_id):
    user, via, err = _auth()
    if err:
        return err
    if via == 'api_key':
        from . import partner_api as _papi
        v1, status, extra = _papi.handle_job(user, job_id)
        resp = make_response(jsonify(v1), status)
        for k, v in (extra or {}).items():
            resp.headers[k] = v
        return resp
    out, status = service.job_status(user, job_id, via=via)
    return jsonify(out), status
