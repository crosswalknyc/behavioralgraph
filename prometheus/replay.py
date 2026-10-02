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

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
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

_LOCK = threading.Lock()
_STATE: dict = {'harness': None, 'captured': [], 'baseline': None}


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
    return {'decision': decision, 'subject': subject or '',
            'brand_category': 'GENERAL',
            'date_window': '2025-10-02 to 2026-10-02',
            'estimated_credits': 10,
            'universe_label': subject or ''}


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
        h.canned_model(_canned_for(surface, subject, logged_outcome))
        path = ('/api/brief-chat/analyze' if surface == 'analyze'
                else '/api/brief-chat/interpret')
        body = {'text': question, 'history': list(history or []),
                'page_context': pc}
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
        out['reply'] = _reply_text(payload)[:600]
        if not rec:
            out['route'] = out['route'] or 'unlogged'
            out['outcome'] = out['outcome'] or (
                'error' if out['status'] >= 400 else 'unlogged')
    return out


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
            has_view_data=False, view_id=''):
    """Classify a replay. Returns (verdict, reason).

    fail       the live reply is broken (error, empty, faulted copy,
               a leaked internal token).
    regressed  a good shipped outcome now declines or dead-ends.
    drift      route or outcome moved, neither side bad; includes a
               good outcome that now asks a clarifying question.
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
    if lo in GOOD_OUTCOMES and vo in CLARIFY_OUTCOMES:
        return 'drift', f'{lr}/{lo} -> {vr}/{vo} (asks first)'
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


def entry_for(rec, live):
    """Build the replay entry for one ask-log record and its probe."""
    question = str(rec.get('question') or '')
    subject = str(rec.get('subject') or '')
    extra = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
    verdict, reason = compare(rec.get('route'), rec.get('outcome'), live,
                              had_subject=bool(subject),
                              has_view_data=bool(rec.get('view_data')),
                              view_id=str(rec.get('view') or ''))
    return {
        'question': question[:200], 'user': str(rec.get('user') or '')[:60],
        'view': str(rec.get('view') or ''), 'subject': subject[:80],
        'ts': rec.get('ts'),
        'logged': {'route': rec.get('route'), 'outcome': rec.get('outcome'),
                   'result': extra.get('result')},
        'live': {k: live.get(k) for k in ('route', 'outcome', 'surface',
                                          'row', 'status', 'ms', 'result')},
        'reply': str(live.get('reply') or '')[:240],
        'verdict': verdict, 'reason': reason,
    }


def replay_records(recs):
    """Replay a list of ask-log records in one child. Returns entries."""
    recs = list(recs or [])
    items = [{k: v for k, v in item_from_record(r).items()
              if not k.startswith('_')} for r in recs]
    lives = probe_many(items)
    return [entry_for(r, live) for r, live in zip(recs, lives)]


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
