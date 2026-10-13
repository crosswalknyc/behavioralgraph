"""Replay real asks against the current build (2026-10-02, S2).

The ask log records what every user asked, on which screen, and how
Prometheus answered (route + outcome). This module re-runs those asks
through the real request handlers with the model, S3, ClickHouse, and
email all faked, then compares the live route and outcome with what
shipped at the time. The nightly runner
(`migration/pm_ask_replay.py`) walks yesterday's log and writes a
status doc; the answer grader (`migration/pm_answer_grading.py`) uses
the probe to re-check replies users pushed back on.

Isolation
---------
The chat module and its side modules (memory, insights ledger,
corrections, usage log, panel fact store) open their own S3 and
ClickHouse clients. A replay must never write a real user's memory,
ledger, or ask log, so the handlers run in a CHILD PROCESS that
installs a fake `boto3` (every service client delegates to one fake
S3 store; SES and the rest are no-ops) and a missing
`clickhouse_connect` before any application module is imported. The
queue URL is the harness's unreachable test host. The parent (`probe_many`) only passes
JSON in and out. `probe_route` is the one-ask convenience wrapper.

Inside the child, `harness()` binds the recording host from
`scripts/_pm_behavior.py`, captures `render_usage_log.record_ask`, and
snapshots the fake S3 so each ask starts from the same empty state.

What the replay can and cannot see
----------------------------------
* Every deterministic lane runs for real: routing, pricing and status
  intercepts, the no-build gate, the answer gate, open-screen binding,
  ledger replay, the clarify ladder, copy assembly.
* The model is canned. On the analyze surface it returns one short
  reply; on the interpret surface it returns a draft built from the
  logged decision and subject. A difference only the model could have
  produced is not a regression and is not reported as one.
* The library is empty, no build is active, and no read is cached, so
  outcomes that need that state are reported as unchecked.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types

WEBAPP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(WEBAPP)

REPLAY_USER = {'username': 'replay', 'email': 'replay@crosswalknyc.com'}

# Views where the ask log's `subject` is the profile open on screen.
PROFILE_VIEWS = frozenset({'', 'profileIQ', 'profile', 'compareIQ'})

BUILD_DECISIONS = frozenset({
    'new_build', 'existing_match', 'derive_cut', 'time_shifted_refresh',
    'cut_needs_parent', 'subscriber_iq', 'batch_draft'})

# Outcomes that are a failed reply on their own, whatever shipped before.
BAD_OUTCOMES = frozenset({'error', 'empty', 'faulted', 'clarified_repeat',
                          'unknown', ''})

# Outcomes that answered or moved the ask forward.
GOOD_OUTCOMES = frozenset({
    'answered', 'opened', 'downloaded', 'status', 'panel_fact',
    'memory_confirm', 'rerouted', 'forwarded',
    # deterministic lanes (2026-10-02 S3)
    'in_library', 'not_found', 'approve_without_draft',
    'workorder_status', 'workorder_eta', 'workorder_cancel'}) | BUILD_DECISIONS

# Outcomes that depend on state the replay does not have: an empty
# library, no active build, no recent read to export.
HARNESS_LIMITED_OUTCOMES = frozenset({
    'declined_no_base_profile', 'build_first_offer',
    'declined_no_csv_source', 'nothing_active'})

# A clarifying question is the intake contract doing its job on an
# under-specified ask (2026-10-02 W2). It is reported as drift, never
# as a regression, unless the reply itself is broken.
CLARIFY_OUTCOMES = frozenset({'clarified', 'asked_open_screen', 'asked_base',
                              'asked_what', 'asked_subject'})

# Routes that read Subscriber IQ files the replay does not carry. An
# honest miss there is unchecked, as is any miss on a non-profile
# screen whose numbers the log did not keep; a broken reply is still
# a fail.
DATA_DEPENDENT_ROUTES = frozenset({'subiq_reroute', 'subiq_lookup'})

# Reply text that only a broken copy path produces.
_COPY_DEFECT_RE = re.compile(
    r'(\bthe that subject\b|\bthat subject profile\b|\bthe the\b|'
    r'\bof of\b|<Stub |\bNone\b audience|'
    r'needs an initial data cut of (what|how|why|which|who|when|is|are|do|does|can)\b)',
    re.IGNORECASE)

CHILD_TIMEOUT_S = int(os.environ.get('PM_REPLAY_CHILD_TIMEOUT_S', '900'))

# A priced offer is the product declining to give a read away, not a
# dead end: a shipped free answer that now draws an offer is drift to
# look at, never a regression (Jordan's breakdown asks, 2026-10-12).
OFFER_OUTCOMES = frozenset({'panel_offer'})

# Credit prices the replay child answers with (the live table lives in
# wallet.DEFAULT_PRICING; only the shape matters here).
_CREDIT_PRICES = {'derived_read': 1, 'viewership_read': 5, 'panel_report': 5}

# Library snapshot (2026-10-13). The nightly runners write the Select
# Profile library (names + keys, no file bodies) to this path before a
# child starts, so asks that turn on what the library holds (a named
# ask answered from the shelf, "do you see X", a cut off a held
# profile) replay against the real shelf instead of an empty one.
CATALOG_ENV = 'PM_REPLAY_CATALOG'
CATALOG_FIELDS = ('display_name', 's3_key', 'category', 'created_at',
                  'last_modified', 'profile_subject', 'job_id')

# The widget's door. PM_REPLAY_DOOR=legacy posts to the brief-chat
# cores directly (the pre-2026-10-13 behaviour) for comparison.
DOOR_PATH = '/api/prometheus/v1/ask'

_LOCK = threading.Lock()
_STATE: dict = {'harness': None, 'captured': [], 'baseline': None,
                'door': ''}


def write_catalog_snapshot(s3, path, bucket='dashboard-inputs'):
    """Slim copy of the library index for the replay child; sets
    PM_REPLAY_CATALOG so every child started after this call reads it.
    Returns the number of profiles written, 0 on any failure."""
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key='system/s3_cache.json')
                         ['Body'].read())
        hidden = set()
        try:
            qs = json.loads(s3.get_object(
                Bucket=bucket, Key='metadata/admin_quick_selects.json')['Body'].read())
            hidden = {k for k, v in (qs.get('profiles') or {}).items() if v is False}
        except Exception:
            pass
        jobs = []
        for j in (doc.get('jobs') or []):
            if not isinstance(j, dict):
                continue
            key = j.get('s3_key') or j.get('key')
            if not key or key in hidden:
                continue
            slim = {k: j.get(k) for k in CATALOG_FIELDS if j.get(k) is not None}
            slim.update({'s3_key': key, 'status': 'cached', 'source': 's3'})
            jobs.append(slim)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'jobs': jobs}, fh)
        os.environ[CATALOG_ENV] = path
        return len(jobs)
    except Exception:
        traceback.print_exc()
        return 0


class _DoorModule:
    """The app module the service host binds against, assembled from
    the chat module (cores, gates, thread store, ask logging) with the
    recording host behind it for everything else."""

    def __init__(self, chat, rh):
        self._chat, self._rh = chat, rh

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        chat = object.__getattribute__(self, '_chat')
        if hasattr(chat, name):
            return getattr(chat, name)
        return getattr(object.__getattribute__(self, '_rh'), name)


def _bind_service_door(h):
    """Bind prometheus.service to the harness and mount the v1 ask
    route on the harness app (2026-10-13). The widget's asks enter
    through that door, where the command lanes live (email me this
    file, open / download, build status, offers, drill-downs); a
    replay posted straight to the legacy cores never saw them and
    judged their asks by what the build flow did instead. Returns the
    path to post to, or '' when the door could not be mounted."""
    try:
        from prometheus import host as _ph
        _ph.host.bind_from_app_module(_DoorModule(h.chat, h.host))
        if 'prometheus' not in h.app.blueprints:
            from prometheus.blueprint import bp
            h.app.register_blueprint(bp)
        return DOOR_PATH
    except Exception:
        traceback.print_exc()
        return ''


def _bind_catalog_snapshot(h):
    """Load PM_REPLAY_CATALOG (if set) into the harness library."""
    path = os.environ.get(CATALOG_ENV) or ''
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, encoding='utf-8') as fh:
            doc = json.load(fh)
        jobs = [j for j in (doc.get('jobs') or [])
                if isinstance(j, dict) and j.get('s3_key')]
    except Exception:
        return 0
    h.host.set('s3_cache', {'jobs': jobs})
    h.host.set('_admin_hidden_profile_keys', lambda *a, **k: set())
    return len(jobs)


# ---------------------------------------------------------------------------
# Child-process isolation
# ---------------------------------------------------------------------------
class _FakeBotoModule(types.ModuleType):
    """`import boto3` inside the child yields this. `client()` hands
    back the harness's fake S3 so every side module shares one store."""

    def __init__(self):
        super().__init__('boto3')
        self.store = None

    def client(self, *_a, **_k):
        if self.store is None:
            self.store = _fresh_fake_s3()
        return _FakeClient(self.store)

    def resource(self, *_a, **_k):
        return self.client()

    def Session(self, *_a, **_k):  # noqa: N802
        return self


class _FakeClient:
    """Delegates S3 calls to the shared fake store; any other service
    call (SES send_email, Lambda invoke, ...) is a no-op returning {}."""

    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        store = object.__getattribute__(self, '_store')
        if hasattr(store, name):
            return getattr(store, name)
        return lambda *a, **k: {}


def _fresh_fake_s3():
    _ensure_paths()
    from _pm_behavior import FakeS3
    return FakeS3()


def install_isolation():
    """Make the current process safe to run handlers in: fake boto3,
    no ClickHouse, no panel fact store, offline flags. Idempotent.
    Call BEFORE importing any application module."""
    os.environ['REGRESSION_TEST_MODE'] = '1'
    os.environ['HOSTMAP_GAP_DOMAIN_RESEARCH'] = '0'
    os.environ['PM_PANEL_FACT_STORE'] = '0'
    os.environ['PM_REPLAY_CHILD'] = '1'
    if not isinstance(sys.modules.get('boto3'), _FakeBotoModule):
        sys.modules['boto3'] = _FakeBotoModule()
    # `import clickhouse_connect` raises ImportError; every caller is
    # fail-safe around it.
    sys.modules['clickhouse_connect'] = None  # type: ignore[assignment]


def in_child():
    return os.environ.get('PM_REPLAY_CHILD') == '1'


def _ensure_paths():
    for p in (WEBAPP, os.path.join(REPO_ROOT, 'scripts'), REPO_ROOT):
        if p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Harness (child only)
# ---------------------------------------------------------------------------
def _normalize_for_match(s):
    if not s:
        return ''
    s = re.sub(r"[^a-z0-9\s]+", " ", str(s).lower())
    return re.sub(r"\s+", " ", s).strip()


def _cas_update_in_memory(s3):
    def _cas(bucket, key, mutate_fn, max_retries=5, default=None, **_kw):
        try:
            cur = s3.json(key, default)
        except Exception:
            cur = default
        try:
            new = mutate_fn(cur if cur is not None else default)
        except Exception:
            return None
        if new is not None:
            try:
                s3.put_json(key, new)
            except Exception:
                pass
        return new
    return _cas


def harness():
    """Load the chat module once, offline, with the ask log captured.
    Refuses to run outside an isolated child unless
    PM_REPLAY_ALLOW_INPROCESS=1 (tests that install the fakes first)."""
    with _LOCK:
        if _STATE['harness'] is not None:
            return _STATE['harness']
        if not in_child() and os.environ.get('PM_REPLAY_ALLOW_INPROCESS') != '1':
            raise RuntimeError('replay harness only runs in an isolated child; '
                               'use probe_many / probe_route')
        _ensure_paths()
        from _pm_behavior import load_chat  # scripts/_pm_behavior.py
        h = load_chat()
        h.app.config['PROPAGATE_EXCEPTIONS'] = False
        boto = sys.modules.get('boto3')
        if isinstance(boto, _FakeBotoModule):
            # one store for the host and for every side module
            boto.store = h.s3
        import render_usage_log as _rul
        captured = _STATE['captured']

        def _capture(**kw):
            captured.append(kw)
        _rul.record_ask = _capture
        try:
            _rul._s3 = h.s3
        except Exception:
            pass
        for modname, attr in (('insights_ledger', '_s3'),):
            mod = sys.modules.get(modname)
            if mod is not None:
                try:
                    setattr(mod, attr, h.s3)
                except Exception:
                    pass
        h.as_user(dict(REPLAY_USER))
        h.host.set('_require_profile_run_access', lambda key: (True, None))
        h.host.set('_normalize_for_match', _normalize_for_match)
        # Seat money + credit prices (2026-10-13): unbound, these render
        # as "<Stub ...>" inside a priced offer and the copy-defect check
        # then fails a correct reply (Jordan's breakdown offers).
        h.host.set('_session_money_symbol', lambda *a, **k: '$')
        h.host.set('get_credit_cost', lambda key, *a, **k: _CREDIT_PRICES.get(str(key), 1))
        h.host.set('CREDITS_PANEL_REPORT', 5)
        _bind_catalog_snapshot(h)
        _STATE['door'] = ('' if os.environ.get('PM_REPLAY_DOOR') == 'legacy'
                          else _bind_service_door(h))
        h.host.set('_CHATBOT_CALM_MESSAGE',
                   'Nothing was lost. Give me a moment and ask again.')
        h.host.set('get_current_user', lambda *a, **k: dict(REPLAY_USER))
        h.host.set('_fmt_study_date', lambda d, *a, **k: str(d or ''))
        h.host.set('_stamp_csv_text', lambda text, *a, **k: str(text or ''))
        h.host.set('_s3_json_cas_update', _cas_update_in_memory(h.s3))
        for name in ('CREDITS_SVOD', 'CREDITS_SUBSCRIBER_IQ',
                     'CREDITS_PROFILE_ANALYSIS'):
            h.host.set(name, 10)
        for name in ('_pm_watch_notify',):
            if hasattr(h.chat, name):
                h.patch(name, lambda *a, **k: None)
        # Baseline state: every probe starts from this snapshot so one
        # ask's thread, ledger, or memory never bleeds into the next.
        _STATE['baseline'] = dict(getattr(h.s3, 'objects', {}) or {})
        _STATE['harness'] = h
        return h


def _restore_baseline(h):
    objects = getattr(h.s3, 'objects', None)
    base = _STATE.get('baseline')
    if isinstance(objects, dict) and isinstance(base, dict):
        objects.clear()
        objects.update(base)


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', str(s or '')).strip('_')[:60] or 'Subject'


def _profile_csv(subject):
    s = str(subject).replace(',', ' ')
    rows = [
        'Column,Value,Brand Penetration (Row),Raw,Projection,Category Share',
        f'BRAND INPUT,{s},100.0000%,123457,4073013,100',
        'SAMPLE SIZE,2025-10-01 to 2026-10-01,100.0000%,123457,4073013,100',
        'BRAND CATEGORY,GENERAL,100.0000%,123457,4073013,100',
        'GENDER,FEMALE,53.2341%,65723,2168091,53.2',
        'GENDER,MALE,46.7659%,57734,1904922,46.8',
        'AGE,18-24,14.2317%,17571,579671,14.2',
        'AGE,25-34,22.8143%,28166,929193,22.8',
        'AGE,35-44,21.3327%,26337,868853,21.3',
        'AGE,45-54,17.6411%,21779,718497,17.6',
        'AGE,55-64,13.1137%,16190,534121,13.1',
        'AGE,65+,10.8665%,13414,442678,10.9',
        'BEVERAGE,Dr Pepper,41.2311%,50903,1679302,30.1',
        'BEVERAGE,Coca-Cola,38.1133%,47053,1552287,27.9',
        'QSR,McDonalds,55.1234%,68055,2245000,40.3',
        'STREAMING/PLATFORM,Netflix,67.4213%,83238,2745912,35.2',
    ]
    return ('\n'.join(rows) + '\n').encode('utf-8')


def _page_context(h, view_id, subject, view_data):
    pc = {}
    view_id = str(view_id or '').strip()
    subject = str(subject or '').strip()
    if view_id:
        pc['view_context'] = {
            'view_id': view_id,
            'view_title': subject or view_id,
            'data': view_data if isinstance(view_data, dict) else {}}
    if subject and (view_id in PROFILE_VIEWS):
        key = f'{_slug(subject)}_09_01_2026_10_00.csv'
        try:
            h.s3.put_object(Bucket='dashboard-inputs', Key=key,
                            Body=_profile_csv(subject))
        except Exception:
            pass
        pc['primary'] = {'s3_key': key, 'name': subject}
        pc.setdefault('view_context', {
            'view_id': 'profileIQ', 'view_title': subject, 'data': {}})
    return pc


def _canned_for(surface, subject, logged_outcome):
    if surface == 'analyze':
        who = subject or 'This audience'
        return {'reply': (f'{who} reads 1.6x the US rate on the lead brand; '
                          'the second brand sits at 0.8x.')}
    decision = (logged_outcome if logged_outcome in BUILD_DECISIONS
                and logged_outcome not in ('batch_draft', 'subscriber_iq')
                else 'new_build')
    tu, avid = _canned_sizes(subject)
    return {'decision': decision, 'subject': subject or '',
            'brand_category': 'GENERAL',
            'date_window': '2025-10-02 to 2026-10-02',
            'estimated_credits': 10,
            'universe_label': subject or '',
            # sizing fields the sample-check lane reads; without them a
            # sample ask faults in the child and reads as a regression
            # (Emmet's Hilton sizing, 2026-10-13)
            'subject_raw_tu': tu, 'subject_raw_avid': avid,
            'us_audience_estimate': int(round(tu * 32.99)),
            'window_label': '10-02-2025 to 10-02-2026'}


def _canned_sizes(subject):
    """Deterministic, messy sample sizes for the canned draft."""
    h = int(hashlib.md5(str(subject or 'audience').encode('utf-8')).hexdigest()[:8], 16)
    tu = 20_000 + h % 180_000
    if tu % 10 == 0:
        tu += 1 + (h >> 8) % 8
    avid = int(tu * 0.31) + 1 + (h >> 16) % 7
    if avid % 10 == 0:
        avid += 3
    return tu, avid


def _reply_text(payload):
    try:
        from prometheus import ask_outcome
        t = ask_outcome.reply_text(payload) or ''
        if t:
            return t
    except Exception:
        pass
    if isinstance(payload, dict):
        # guidance payloads carry the text the widget shows in `error`
        for k in ('reply', 'error', 'message', 'text'):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return ''


def surface_for(question, has_ctx, mode=None):
    try:
        from prometheus import understand
        d = understand.decide(question, has_ctx=bool(has_ctx), mode=mode)
        s = str((d or {}).get('surface') or '')
        return ('analyze' if s == 'analyze' else 'interpret'), (d or {}).get('row')
    except Exception:
        return ('analyze' if has_ctx else 'interpret'), None


def _probe_inprocess(question, view_id='', subject='', history=None,
                     view_data=None, logged_outcome=None, mode=None):
    question = str(question or '').strip()
    out = {'route': '', 'outcome': '', 'surface': '', 'row': None,
           'reply': '', 'status': 0, 'ms': 0, 'result': None}
    if not question:
        out.update(route='replay_fault', outcome='empty_question')
        return out
    try:
        h = harness()
    except Exception as exc:  # harness missing (no scripts/ next to bg-webapp)
        out.update(route='replay_fault', outcome='harness_unavailable',
                   reply=str(exc)[:200])
        return out
    with _LOCK:
        _restore_baseline(h)
        view_id = str(view_id or '').strip()
        pc = _page_context(h, view_id, subject, view_data)
        has_ctx = bool(pc)
        surface, row = surface_for(question, has_ctx, mode)
        out['surface'], out['row'] = surface, row
        _canned_dual(h, subject, logged_outcome)
        door = _STATE.get('door') or ''
        path = door or ('/api/brief-chat/analyze' if surface == 'analyze'
                        else '/api/brief-chat/interpret')
        body = {'text': question, 'history': list(history or []),
                'page_context': pc}
        if door:
            # the widget's shape: no persisted thread, page context as
            # the dashboard sends it
            body.update({'client': 'dashboard', 'persist': False})
        if mode:
            body['mode'] = mode
        n0 = len(_STATE['captured'])
        t0 = time.time()
        try:
            status, payload = h.post(path, body,
                                     session={'username': REPLAY_USER['username']})
        except Exception as exc:
            status, payload = 500, {'error': str(exc)[:300]}
        out['ms'] = int((time.time() - t0) * 1000)
        out['status'] = int(status or 0)
        recs = _STATE['captured'][n0:]
        rec = recs[-1] if recs else {}
        out['route'] = str(rec.get('route') or '')
        out['outcome'] = str(rec.get('outcome') or '')
        extra = rec.get('extra') or {}
        out['result'] = extra.get('result') if isinstance(extra, dict) else None
        if door and isinstance(payload, dict):
            # `surface` stays the routing table's call (what the tests
            # pin); the door's own choice rides alongside
            out['door_surface'] = str(payload.get('surface') or '')
            raw = payload.get('raw')
            if isinstance(raw, dict):
                merged = dict(raw)
                merged.setdefault('reply', payload.get('text'))
                payload = merged
        out['reply'] = _reply_text(payload)[:600]
        if not rec:
            out['route'] = out['route'] or 'unlogged'
            out['outcome'] = out['outcome'] or (
                'error' if out['status'] >= 400 else 'unlogged')
    return out


def _canned_dual(h, subject, logged_outcome):
    """One canned model that answers each call in the shape its caller
    parses: interpret-tagged agent calls get the brief draft, analysis
    calls get the reply. The door picks the surface itself, so the
    model can no longer be shaped for one surface up front."""
    draft = _canned_for('interpret', subject, logged_outcome)
    reply = _canned_for('analyze', subject, logged_outcome)

    def _copy(d):
        return json.loads(json.dumps(d))

    def _agent(*a, **k):
        tag = k.get('usage_tag') or ()
        first = str(tag[0]) if isinstance(tag, (list, tuple)) and tag else ''
        sp = str(k.get('system_prompt') or '')
        wants_draft = (first == 'interpret' or 'subject_raw_tu' in sp
                       or '"decision"' in sp or 'existing_match' in sp)
        data = draft if wants_draft else reply
        return {'success': True, 'data': _copy(data), 'model': 'replay'}

    h.host.set('_run_nflx_claude_agent', _agent)
    h.patch('_pm_claude_json', lambda *a, **k: {
        'success': True, 'data': _copy(reply), 'model': 'replay'})


# ---------------------------------------------------------------------------
# Parent-side API
# ---------------------------------------------------------------------------
_PROBE_KEYS = ('question', 'view_id', 'subject', 'history', 'view_data',
               'logged_outcome', 'mode')


def _fault(reason, detail=''):
    return {'route': 'replay_fault', 'outcome': reason, 'surface': '',
            'row': None, 'reply': str(detail)[:300], 'status': 0, 'ms': 0,
            'result': None}


def probe_many(items, timeout_s=None, python=None):
    """Run every item ({question, view_id, subject, history, view_data,
    logged_outcome, mode}) in one isolated child. Returns a list of
    probe dicts in the same order. Never raises."""
    items = [dict(i or {}) for i in (items or [])]
    if not items:
        return []
    if in_child() or os.environ.get('PM_REPLAY_ALLOW_INPROCESS') == '1':
        return [_probe_inprocess(**{k: i.get(k) for k in _PROBE_KEYS})
                for i in items]
    fd_in, path_in = tempfile.mkstemp(prefix='pm_replay_in_', suffix='.json')
    fd_out, path_out = tempfile.mkstemp(prefix='pm_replay_out_', suffix='.json')
    os.close(fd_out)
    try:
        with os.fdopen(fd_in, 'w') as fh:
            json.dump(items, fh)
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(
            [WEBAPP, os.path.join(REPO_ROOT, 'scripts'), REPO_ROOT,
             env.get('PYTHONPATH', '')]).strip(os.pathsep)
        cmd = [python or sys.executable, '-m', 'prometheus.replay',
               '--batch', path_in, path_out]
        try:
            proc = subprocess.run(cmd, cwd=WEBAPP, env=env,
                                  capture_output=True, text=True,
                                  timeout=timeout_s or CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return [_fault('child_timeout') for _ in items]
        except Exception as exc:
            return [_fault('child_failed', exc) for _ in items]
        try:
            with open(path_out) as fh:
                results = json.load(fh)
        except Exception:
            tail = (proc.stderr or '')[-600:]
            return [_fault('child_no_output', f'rc={proc.returncode} {tail}')
                    for _ in items]
        if not isinstance(results, list) or len(results) != len(items):
            return [_fault('child_bad_output') for _ in items]
        return results
    finally:
        for p in (path_in, path_out):
            try:
                os.unlink(p)
            except OSError:
                pass


def probe_route(question, view_id='', subject='', history=None,
                view_data=None, logged_outcome=None, mode=None):
    """Re-run one ask in an isolated child. Returns a dict with the
    live route, outcome, surface, reply text, HTTP status, wall time,
    and the routing row. Never raises."""
    return probe_many([{'question': question, 'view_id': view_id,
                        'subject': subject, 'history': history,
                        'view_data': view_data,
                        'logged_outcome': logged_outcome, 'mode': mode}])[0]


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def _is_bad_reply(text):
    text = str(text or '')
    if _COPY_DEFECT_RE.search(text):
        return True
    try:
        from prometheus import ask_outcome
        return bool(ask_outcome.is_faulted_text(text))
    except Exception:
        return False


def _needs_subject_decline(live):
    """Interpret declined only because the canned draft had no subject."""
    return (live.get('surface') == 'interpret'
            and live.get('outcome') == 'declined'
            and 'which audience' in str(live.get('reply') or '').lower())


def compare(logged_route, logged_outcome, live, had_subject=True,
            has_view_data=False, view_id='', rejected=False):
    """Classify a replay. Returns (verdict, reason).

    fail       the live reply is broken (error, empty, faulted copy,
               a leaked internal token).
    regressed  a good shipped outcome now declines or dead-ends.
    drift      route or outcome moved, neither side bad; includes a
               good outcome that now asks a clarifying question, one
               that now draws a priced offer, and any move away from a
               shipped reply the reader pushed back on (`rejected`).
    same       same route and outcome.
    unchecked  the difference is one only the model or state the
               replay does not carry could explain.
    """
    lr, lo = str(logged_route or ''), str(logged_outcome or '')
    vr, vo = str(live.get('route') or ''), str(live.get('outcome') or '')
    if vr == 'replay_fault':
        return 'unchecked', vo
    if _is_bad_reply(live.get('reply')):
        return 'fail', f'broken reply on {vr}/{vo or "no outcome"}'
    if vo in BAD_OUTCOMES or int(live.get('status') or 0) >= 500:
        off_profile = str(view_id or '') not in PROFILE_VIEWS
        if not has_view_data and (vr in DATA_DEPENDENT_ROUTES or off_profile) \
                and int(live.get('status') or 0) < 500:
            return 'unchecked', f'{vr}/{vo}: needs on-screen data the replay does not carry'
        return 'fail', f'live {vr}/{vo or "no outcome"}'
    if (lr, lo) == (vr, vo):
        return 'same', ''
    if _needs_subject_decline(live) and not had_subject:
        return 'unchecked', 'interpret needs a subject the log did not keep'
    if vo in HARNESS_LIMITED_OUTCOMES:
        return 'unchecked', f'{vo}: state the replay does not carry'
    if rejected:
        # The shipped reply is not a baseline: the reader re-asked or
        # pushed back on it, so moving off it is the point of a fix.
        return 'drift', f'{lr}/{lo} -> {vr}/{vo} (the reader pushed back on the shipped reply)'
    if lo in GOOD_OUTCOMES and vo in CLARIFY_OUTCOMES:
        return 'drift', f'{lr}/{lo} -> {vr}/{vo} (asks first)'
    if lo in GOOD_OUTCOMES and vo in OFFER_OUTCOMES:
        return 'drift', f'{lr}/{lo} -> {vr}/{vo} (now a priced offer)'
    if lo in GOOD_OUTCOMES and vo not in GOOD_OUTCOMES:
        return 'regressed', f'{lr}/{lo} -> {vr}/{vo}'
    return 'drift', f'{lr}/{lo} -> {vr}/{vo}'


def item_from_record(rec):
    extra = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
    return {'question': str(rec.get('question') or ''),
            'view_id': str(rec.get('view') or ''),
            'subject': str(rec.get('subject') or ''),
            'view_data': rec.get('view_data') if isinstance(rec.get('view_data'), dict) else None,
            'logged_outcome': str(rec.get('outcome') or ''),
            'mode': rec.get('mode') or None,
            '_extra_result': extra.get('result')}


def rejected_key(rec):
    """(user, question) key for a reply the reader pushed back on."""
    return (str(rec.get('user') or '').strip().casefold(),
            str(rec.get('question') or '').strip().casefold())


def entry_for(rec, live, rejected=False):
    """Build the replay entry for one ask-log record and its probe."""
    question = str(rec.get('question') or '')
    subject = str(rec.get('subject') or '')
    extra = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
    verdict, reason = compare(rec.get('route'), rec.get('outcome'), live,
                              had_subject=bool(subject),
                              has_view_data=bool(rec.get('view_data')),
                              view_id=str(rec.get('view') or ''),
                              rejected=bool(rejected))
    return {
        'question': question[:200], 'user': str(rec.get('user') or '')[:60],
        'view': str(rec.get('view') or ''), 'subject': subject[:80],
        'ts': rec.get('ts'),
        'logged': {'route': rec.get('route'), 'outcome': rec.get('outcome'),
                   'result': extra.get('result'), 'rejected': bool(rejected)},
        'live': {k: live.get(k) for k in ('route', 'outcome', 'surface',
                                          'row', 'status', 'ms', 'result')},
        'reply': str(live.get('reply') or '')[:240],
        'verdict': verdict, 'reason': reason,
    }


def replay_records(recs, rejected=None):
    """Replay a list of ask-log records in one child. Returns entries.
    `rejected` is the set of rejected_key() pairs whose shipped reply
    the reader pushed back on (the grader finds them)."""
    recs = list(recs or [])
    rejected = set(rejected or ())
    items = [{k: v for k, v in item_from_record(r).items()
              if not k.startswith('_')} for r in recs]
    lives = probe_many(items)
    return [entry_for(r, live, rejected=rejected_key(r) in rejected)
            for r, live in zip(recs, lives)]


def replay_record(rec):
    return replay_records([rec])[0]


def reset():
    """Drop the loaded harness (tests)."""
    with _LOCK:
        _STATE['harness'] = None
        _STATE['captured'].clear()
        _STATE['baseline'] = None


def _main(argv):
    if len(argv) >= 3 and argv[0] == '--batch':
        install_isolation()
        with open(argv[1]) as fh:
            items = json.load(fh)
        results = [_probe_inprocess(**{k: i.get(k) for k in _PROBE_KEYS})
                   for i in items]
        with open(argv[2], 'w') as fh:
            json.dump(results, fh)
        return 0
    print('usage: python -m prometheus.replay --batch in.json out.json',
          file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))


__all__ = ['probe_route', 'probe_many', 'compare', 'replay_record',
           'replay_records', 'entry_for', 'item_from_record', 'surface_for',
           'harness', 'install_isolation', 'reset', 'BAD_OUTCOMES',
           'GOOD_OUTCOMES', 'BUILD_DECISIONS', 'HARNESS_LIMITED_OUTCOMES',
           'CLARIFY_OUTCOMES']
