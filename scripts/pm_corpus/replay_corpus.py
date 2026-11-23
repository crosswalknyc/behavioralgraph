#!/usr/bin/env python3
"""Replay the behaviour corpus through service.ask and report drift by
intent class (rebuild stage 1). This is the gate every later stage runs
before it ships.

    python3 scripts/pm_corpus/replay_corpus.py --corpus latest
    python3 scripts/pm_corpus/replay_corpus.py --corpus latest --baseline ~/.cache/crosswalk_replay/results/<stamp>.json
    python3 scripts/pm_corpus/replay_corpus.py --fixtures            # the committed sanitized set
    python3 scripts/pm_corpus/replay_corpus.py --corpus latest --mode service --limit 200

Two modes, one comparison:

  door     (default) each case posts to /api/prometheus/v1/ask on the
           chat module loaded in an isolated child (prometheus.replay):
           fake S3, no ClickHouse, no mail, the model canned per case
           (the Reader hands back the logged reading; the cores get one
           short reply or a draft). The lanes and the cores' own regex
           layers run for real. Needs the parent repo's scripts/ on the
           path (scripts/_pm_behavior.py); the runner finds it beside
           bg-webapp or through PM_PARENT_SCRIPTS.
  service  each case runs service.ask in this process on a stub host
           with the three cores stubbed to markers. Lane level only; no
           child, no chat module. The unit test uses it.

Hermetic in both: REGRESSION_TEST_MODE=1, a probe identity, no charge,
no mail, no thread post, no S3 write. The report never prints a
question or a reply; entries carry them for the operator's own reading
in the results file, which stays in ~/.cache/crosswalk_replay/results/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
WEBAPP = os.path.dirname(SCRIPTS)
for _p in (SCRIPTS, WEBAPP):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('HOSTMAP_GAP_DOMAIN_RESEARCH', '0')

from pm_corpus import build_corpus as _bc  # noqa: E402

FIXTURES = os.path.join(HERE, 'fixtures', 'sanitized_cases.jsonl')
RESULTS_ROOT = os.path.join(_bc.CACHE_ROOT, 'results')
VERDICTS = ('same', 'drift', 'regressed', 'fail', 'unchecked')
CLASS_DROP_POINTS = 5.0

REPLAY_USER = {'username': 'canary:replay', 'email': '', 'role': 'user', 'company': 'Crosswalk',
               'credits': -1, 'unlimited': True, 'paying_customer': True,
               'has_chatbot_profile_iq_access': True}


# ------------------------------------------------------------- service mode
class _Sink:
    """A falsy callable attribute sink for host names service mode does
    not provide (the shape scripts/_pm_behavior.Stub has)."""

    def __init__(self, name, log):
        self._name, self._log = name, log

    def __call__(self, *a, **k):
        self._log.append(self._name)
        if len(a) == 1 and callable(a[0]) and not k:
            return a[0]
        return _Sink(self._name + '()', self._log)

    def __getattr__(self, n):
        if n.startswith('__'):
            raise AttributeError(n)
        return _Sink(f'{self._name}.{n}', self._log)

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())


class _LegacyNS:
    """The namespace prometheus.legacy.H reads in service mode: a few
    real values, a recording sink for the rest."""

    def __init__(self, names, log):
        object.__setattr__(self, '_names', dict(names))
        object.__setattr__(self, '_log', log)

    def __getattr__(self, name):
        names = object.__getattribute__(self, '_names')
        if name in names:
            return names[name]
        if name.startswith('__'):
            raise AttributeError(name)
        return _Sink(name, object.__getattribute__(self, '_log'))


class _FakeS3Light:
    """An in-memory S3 for service mode: get / put / list / delete /
    head on one dict. Anything else is a no-op returning {}."""

    def __init__(self):
        self.objects = {}
        self.puts = []

    def _miss(self, op):
        try:
            from botocore.exceptions import ClientError
            return ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'NoSuchKey'}}, op)
        except Exception:
            return KeyError(op)

    def get_object(self, Bucket=None, Key=None, **_k):
        if Key not in self.objects:
            raise self._miss('GetObject')
        import io as _io
        body = self.objects[Key]
        return {'Body': _io.BytesIO(body), 'ContentLength': len(body), 'ETag': '"%d"' % len(body)}

    def head_object(self, Bucket=None, Key=None, **_k):
        if Key not in self.objects:
            raise self._miss('HeadObject')
        return {'ContentLength': len(self.objects[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket=None, Key=None, Body=b'', **_k):
        self.puts.append(str(Key))
        self.objects[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')
        return {'ETag': '"x"'}

    def delete_object(self, Bucket=None, Key=None, **_k):
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, Bucket=None, Prefix='', **_k):
        keys = sorted(k for k in self.objects if str(k).startswith(Prefix or ''))
        return {'Contents': [{'Key': k, 'Size': len(self.objects[k])} for k in keys], 'KeyCount': len(keys), 'IsTruncated': False}

    def generate_presigned_url(self, *a, **k):
        return 'https://fake-s3/' + str((k.get('Params') or {}).get('Key') or '')

    def get_paginator(self, op):
        me = self

        class _Pag:
            def paginate(self, **k):
                yield me.list_objects_v2(**k)
        return _Pag()

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *a, **k: {}


_FAKE = {'s3': None}


def install_isolation_light():
    """Service mode runs in this process: every boto3 client any module
    opens is the one in-memory store above, ClickHouse is absent, mail
    is off. Call before importing prometheus modules. Idempotent."""
    import types
    os.environ['REGRESSION_TEST_MODE'] = '1'
    os.environ['PM_MAIL_OFF'] = '1'
    os.environ['PM_PANEL_FACT_STORE'] = '0'
    os.environ['HOSTMAP_GAP_DOMAIN_RESEARCH'] = '0'
    if _FAKE['s3'] is None:
        _FAKE['s3'] = _FakeS3Light()
    if getattr(sys.modules.get('boto3'), '_pm_replay_fake', False):
        return _FAKE['s3']
    fake = types.ModuleType('boto3')
    fake._pm_replay_fake = True
    fake.client = lambda *a, **k: _FAKE['s3']
    fake.resource = lambda *a, **k: _FAKE['s3']
    fake.Session = lambda *a, **k: fake
    sys.modules['boto3'] = fake
    sys.modules['clickhouse_connect'] = None  # type: ignore[assignment]
    return _FAKE['s3']


class _Stub:
    """The stub host for service mode: records every side effect so the
    report can say what a case would have done."""

    def __init__(self):
        self.hints = []
        self.cores = []
        self.writes = []
        self.charges = []
        self.mails = []
        self.legacy_calls = []
        self.reading = None
        self.app = None

    def bind(self):
        install_isolation_light()
        from flask import Flask
        from prometheus.host import host
        from prometheus import rate_card as RC
        from prometheus import legacy as _legacy
        me = self
        self.app = Flask('pm_replay_service')
        self.app.config['TESTING'] = True
        self.app.secret_key = 'replay'

        def _core(name):
            def _run(user, body, *a, **k):
                me.cores.append((name, dict(body)))
                if name == 'interpret':
                    return {'success': True, 'reply': 'Here is the brief.', 'spec_draft': {'subject': str(body.get('bind_subject') or 'x'), 'decision': 'new_build'}}
                if name == 'deck':
                    return {'success': True, 'job_id': 'replay-deck', 'reply': 'Working on the deck.'}
                return {'success': True, 'action': 'answer', 'reply': f'<core:{name}>', 'followups': [], 'core_marker': name}
            return _run

        def _claude(system, user_prompt, **kw):
            if str(kw.get('surface') or '') == 'reader' and isinstance(me.reading, dict) and me.reading.get('intent'):
                return dict(me.reading)
            return {}

        host.bind(analyze_core=_core('analyze'), interpret_core=_core('interpret'), deck_core=_core('deck'),
                  ask_hint=lambda **k: me.hints.append(dict(k)), gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: me.mails.append(('error', a)),
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda key, *a, **k: me.writes.append(str(key)),
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: str(t)[:30], bucket='x', s3_client=_FAKE['s3'],
                  job_owner_ok=lambda a, b: True, claude_data=_claude,
                  load_users=lambda: {'users': {REPLAY_USER['username']: dict(REPLAY_USER)}},
                  check_user_credits=lambda u: (True, -1),
                  consume_credit=lambda username, **kw: me.charges.append((username, dict(kw))) or True,
                  pricing_question=lambda t: bool(re.search(r"\b(?:cost|costs|price|pricing|rate card|charge)\b", str(t or ''), re.I)),
                  pricing_copy_for=lambda u: RC.render('$'), pricing_copy=RC.render('$'),
                  incidence_check=lambda *a, **k: None, audience_range=lambda s: None,
                  user_runs=lambda *a, **k: [], recent_read=lambda *a, **k: None,
                  product_access=lambda u, r: {}, live_features=lambda: {}, twins_locked=lambda u: False,
                  can_open_profile=lambda u, k: True, can_open_journey=lambda u, k: True, partner_key_ok=lambda k: True)
        # the legacy host the side modules read through prometheus.legacy.H
        _legacy.bind(_LegacyNS({
            'app': self.app, 's3_client': _FAKE['s3'], 'S3_BUCKET': 'dashboard-inputs', 'S3_REGION': 'us-east-2',
            'load_users': lambda: {'users': {REPLAY_USER['username']: dict(REPLAY_USER)}},
            'check_user_credits': lambda *a, **k: (True, -1), 'consume_credit': lambda *a, **k: me.charges.append(('legacy', a, k)) or True,
            '_caller_wallet_snapshot': lambda *a, **k: {'credits_remaining': -1},
            '_chatbot_error_email': lambda *a, **k: me.mails.append(('error', a)),
            '_chatbot_calm_payload': lambda: {'success': False, 'guidance': True, 'error': 'Nothing was lost. Give me a moment and ask again.', 'followups': []},
            '_pm_claude_json': lambda *a, **k: {'success': False, 'error': 'no model in service mode'},
            '_run_nflx_claude_agent': lambda *a, **k: {'success': False, 'error': 'no model in service mode'},
            '_session_money_symbol': lambda *a, **k: '$', 'get_credit_cost': lambda key, *a, **k: 1,
            '_v1_price_table_for': lambda username=None: {'new_build': 300.0, 'derive_cut': 100.0, 'time_shifted_refresh': 300.0,
                                                           'existing_match': 0.0, 'panel_report': 500.0, 'viewership_read': 500.0},
            's3_cache': {'jobs': []}, '_admin_hidden_profile_keys': lambda *a, **k: set(),
            '_pm_watch_notify': lambda *a, **k: None, 'SYNTH_QUEUE_URL': 'http://queue.invalid', 'SYNTH_QUEUE_SECRET': 'x',
        }, self.legacy_calls))
        return host

    def side_effects(self):
        """What the stubs recorded. Nothing here reached a real store: the
        counts say what the cases would have done on a live host."""
        return {'would_write': len(self.writes) + len(_FAKE['s3'].puts if _FAKE['s3'] else []),
                'would_charge': len(self.charges), 'would_mail': len(self.mails)}


def _page_context_for(case):
    pc = {}
    view = str(case.get('view') or '')
    subj = str(case.get('subject') or '') or str(((case.get('thread') or {}).get('subject_label')) or '')
    key = str(((case.get('thread') or {}).get('subject_key')) or '')
    if view:
        pc['view_context'] = {'view_id': view, 'view_title': subj or view, 'data': {}}
    if subj and view in ('', 'profileIQ', 'profile', 'compareIQ'):
        prim = {'name': subj}
        if key.startswith('profile:'):
            prim['s3_key'] = key.split(':', 1)[1]
        pc['primary'] = prim
        pc.setdefault('view_context', {'view_id': 'profileIQ', 'view_title': subj, 'data': {}})
    return pc


def _service_once(stub, case):
    """One case through service.ask on the stub host -> live dict. The
    outcome follows the ask log's own rule: a lane's hint, else the
    inferred one, with the taxonomy's override states winning."""
    from prometheus import service
    from prometheus import ask_outcome as _ao
    stub.hints.clear()
    stub.cores.clear()
    stub.reading = case.get('reading') or None
    n_ch, n_wr = len(stub.charges), len(stub.writes) + (len(_FAKE['s3'].puts) if _FAKE['s3'] else 0)
    pc = _page_context_for(case)
    body = {'text': case['question'], 'client': 'dashboard', 'persist': False,
            'surface': case.get('surface') or 'analyze', 'history': list(case.get('history') or []),
            'context': pc or None}
    if case.get('mode'):
        body['mode'] = case['mode']
    for _k in ('bind_subject', 'bind_cohort'):      # a chip's bound subject, when the case carries one (2026-11-20)
        if case.get(_k):
            body[_k] = case[_k]
    t0 = time.time()
    fault = ''
    u_compact = ''
    with stub.app.test_request_context('/api/prometheus/v1/ask', method='POST', headers={'X-Prometheus-Caller': 'replay'}):
        try:
            env, status = service.ask(dict(REPLAY_USER), body)
        except Exception as exc:
            env, status, fault = {}, 500, f'{type(exc).__name__}: {str(exc)[:200]}'
        try:
            from flask import g as _g
            from prometheus import understanding as _und
            _ud = getattr(_g, '_pm_understanding', None)
            u_compact = _und.Understanding.from_dict(_ud).compact() if isinstance(_ud, dict) else ''
        except Exception:
            u_compact = ''
    if fault:
        return {'route': 'replay_fault', 'outcome': 'service_raised', 'surface': '', 'reply': fault, 'status': 500,
                'ms': int((time.time() - t0) * 1000), 'kind': '', 'decision': '', 'core': '', 'bind': '', 'u': u_compact}
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else {}
    surface = str(env.get('surface') or '')
    inferred, outcome0, _s = _ao._ask_infer_route_outcome(surface, raw, status)
    hint = next((h.get('route') for h in reversed(stub.hints) if h.get('route')), None)
    route = _ao.route_pick(hint, inferred)
    core = stub.cores[-1][0] if stub.cores else ''
    core_body = stub.cores[-1][1] if stub.cores else {}
    if core and not hint:
        route = {'analyze': 'page_analysis', 'interpret': 'profile_build', 'deck': 'deck'}.get(core, route)
    hint_out = next((h.get('outcome') for h in reversed(stub.hints) if h.get('outcome')), None)
    base = hint_out or outcome0
    if core == 'analyze' and raw.get('analysis_read') and str(raw.get('error') or '').startswith('<core:'):
        # the analyze core's marker shaped for a client sitting in its build flow (service._interpret_shape: success
        # False + guidance, the reply on `error`): the core answered; the shape is the widget's, not an error
        base = 'answered'
    cls_out, _d = _ao.classify(surface, raw, status, history=case.get('history') or [], base_outcome=base)
    outcome = cls_out if cls_out in ('empty', 'faulted', 'clarified', 'clarified_repeat', 'proposed', 'confirmed', 'rerouted') else (base or cls_out)
    return {'route': str(route or ''), 'outcome': str(outcome or ''), 'surface': surface,
            'reply': str(env.get('text') or '')[:600], 'status': int(status or 0), 'ms': int((time.time() - t0) * 1000),
            'kind': str(env.get('kind') or ''), 'decision': str((env.get('decision') or {}).get('reason') or ''), 'core': core,
            'bind': str(core_body.get('bind_subject') or (core_body.get('extra') or {}).get('bind_subject') or '') if isinstance(core_body, dict) else '',
            'u': u_compact,
            'would_charge': len(stub.charges) > n_ch,
            'would_write': (len(stub.writes) + (len(_FAKE['s3'].puts) if _FAKE['s3'] else 0)) > n_wr}


def run_service(cases):
    stub = _Stub()
    stub.bind()
    lives = [_service_once(stub, c) for c in cases]
    return lives, stub.side_effects()


# ---------------------------------------------------------------- door mode
def _parent_scripts():
    """The parent repo's scripts/ (scripts/_pm_behavior.py) for the child."""
    cands = [os.environ.get('PM_PARENT_SCRIPTS') or '', os.path.join(os.path.dirname(WEBAPP), 'scripts')]
    for c in cands:
        if c and os.path.exists(os.path.join(c, '_pm_behavior.py')):
            return c
    return ''


def run_door(cases, *, chunk=150):
    from prometheus import replay as _rp
    ps = _parent_scripts()
    if ps:
        os.environ['PYTHONPATH'] = os.pathsep.join([p for p in (ps, os.environ.get('PYTHONPATH', '')) if p])
    items = []
    for c in cases:
        th = c.get('thread') or {}
        items.append({'question': c['question'], 'view_id': c.get('view') or '',
                      'subject': c.get('subject') or th.get('subject_label') or '',
                      'history': list(c.get('history') or []), 'view_data': None,
                      'logged_outcome': (c.get('recorded') or {}).get('outcome') or '',
                      'mode': c.get('mode') or None, 'reading': c.get('reading') or None,
                      'client_surface': c.get('surface') or None})
    lives = []
    for i in range(0, len(items), chunk):
        lives.extend(_rp.probe_many(items[i:i + chunk]))
    return lives, {'isolation': 'child process: fake boto3, no ClickHouse, mail off, REGRESSION_TEST_MODE=1'}


# -------------------------------------------------------------- comparison
def _entry(case, live, baseline_entry=None):
    from prometheus import replay as _rp
    from prometheus import intent_classes as _ic
    rec = case.get('recorded') or {}
    verdict, reason = _rp.compare(rec.get('route'), rec.get('outcome'), live, had_subject=bool(case.get('subject')),
                                  has_view_data=False, view_id=str(case.get('view') or ''),
                                  rejected=str((case.get('signals') or {}).get('user_signal') or '') in ('repeat', 'pushback', 'rejected'))
    lr, lo = str(live.get('route') or ''), str(live.get('outcome') or '')
    if verdict == 'regressed':
        if lr == 'data_floor':
            # the data floor (2026-11-18) now answers a window before 2021 ahead of every lane: a gate added since the
            # ask shipped, not a dead end
            verdict, reason = 'drift', f"{rec.get('route')}/{rec.get('outcome')} -> {lr}/{lo} (the data floor gate)"
        elif lr == 'deck' and lo == 'declined' and re.search(r"no figures loaded|do not have a profile or a finished read", str(live.get('reply') or '')):
            # the replay carries no page figures and an empty library, so a deck has nothing to draw from
            verdict, reason = 'unchecked', 'deck: needs the page figures or a library the replay does not carry'
    live_cls = _ic.class_of(live.get('route'), live.get('outcome'), live.get('surface') or live.get('door_surface'))
    rec_cls = rec.get('class') or _ic.class_of(rec.get('route'), rec.get('outcome'), case.get('surface'))
    e = {'id': case.get('id'), 'seat': case.get('seat'), 'day': case.get('day'), 'class': rec_cls,
         'recorded': {'route': rec.get('route'), 'outcome': rec.get('outcome'), 'job': rec.get('job'), 'quoted': rec.get('quoted')},
         'live': {k: live.get(k) for k in ('route', 'outcome', 'surface', 'door_surface', 'kind', 'decision', 'core', 'status', 'ms')},
         'live_class': live_cls, 'class_agree': _ic.near(rec_cls, live_cls),
         'verdict': verdict, 'reason': reason,
         'question': str(case.get('question') or '')[:200], 'reply': str(live.get('reply') or '')[:240]}
    # the Understanding (stage 2): its compact line, and whether its intent lands in the live and the recorded class
    uc = str(live.get('u') or '')
    if uc:
        try:
            from prometheus import understanding as _und
            p = _und.parse_compact(uc)
            e['u'] = {'intent': p.get('intent'), 'conf': p.get('conf'), 'src': p.get('src'), 'act': p.get('act'),
                      'scope': p.get('scope'), 'flip': p.get('flip'),
                      'agree_live': _und.agrees(uc, live.get('route'), live.get('outcome'), live.get('surface') or live.get('door_surface')),
                      'agree_recorded': _und.agrees(uc, rec.get('route'), rec.get('outcome'), case.get('surface'))}
        except Exception:
            e['u'] = None
    if baseline_entry is not None:
        b = baseline_entry.get('live') or {}
        e['baseline'] = {'route': b.get('route'), 'outcome': b.get('outcome'), 'verdict': baseline_entry.get('verdict'),
                         'class': baseline_entry.get('live_class')}
        same_route = (b.get('route'), b.get('outcome')) == (live.get('route'), live.get('outcome'))
        same_reply = bool(e['reply']) and e['reply'] == str(baseline_entry.get('reply') or '')[:240]
        if same_route:
            e['vs_baseline'] = 'same'
        elif same_reply:
            # the answer is the baseline's word for word and only its label moved (an intake turn now logged as its
            # flow instead of the page analysis it ran beside): a relabel, never a change in what the reader got
            # (2026-11-23). It keeps the baseline's verdict and class agreement.
            e['vs_baseline'] = 'relabel'
            e['verdict'], e['reason'] = str(baseline_entry.get('verdict') or e['verdict']), 'relabel: ' + str(e.get('reason') or '')
            e['class_agree'] = bool(baseline_entry.get('class_agree'))
        else:
            e['vs_baseline'] = 'changed'
    return e


def summarize(entries, baseline=None):
    by_class = defaultdict(lambda: {k: 0 for k in ('n',) + VERDICTS + ('class_agree',)})
    verdicts = {k: 0 for k in VERDICTS}
    for e in entries:
        v = e['verdict'] if e['verdict'] in VERDICTS else 'unchecked'
        verdicts[v] += 1
        row = by_class[e['class']]
        row['n'] += 1
        row[v] += 1
        row['class_agree'] += 1 if e.get('class_agree') else 0
    out_classes = {}
    for cls, row in sorted(by_class.items(), key=lambda kv: -kv[1]['n']):
        row = dict(row)
        row['agreement'] = round(100.0 * row['class_agree'] / max(row['n'], 1), 1)
        out_classes[cls] = row
    n = len(entries)
    rep = {'n': n, 'verdicts': verdicts, 'by_class': out_classes,
           'agreement': round(100.0 * sum(1 for e in entries if e.get('class_agree')) / max(n, 1), 1),
           'same_pct': round(100.0 * verdicts['same'] / max(n, 1), 1)}
    # the Understanding's shadow grade over the replay (stage 2)
    with_u = [e for e in entries if isinstance(e.get('u'), dict)]
    if with_u:
        u_cls = defaultdict(lambda: {'n': 0, 'agree_live': 0, 'agree_recorded': 0, 'flipped': 0})
        for e in with_u:
            row = u_cls[e['class']]
            row['n'] += 1
            row['agree_live'] += 1 if e['u'].get('agree_live') else 0
            row['agree_recorded'] += 1 if e['u'].get('agree_recorded') else 0
            row['flipped'] += 1 if e['u'].get('flip') else 0
        rep['understanding'] = {
            'n': len(with_u),
            'agree_live_pct': round(100.0 * sum(1 for e in with_u if e['u'].get('agree_live')) / len(with_u), 1),
            'agree_recorded_pct': round(100.0 * sum(1 for e in with_u if e['u'].get('agree_recorded')) / len(with_u), 1),
            'flipped': sum(1 for e in with_u if e['u'].get('flip')),
            'by_source': dict(sorted(__import__('collections').Counter(str(e['u'].get('src')) for e in with_u).items())),
            'by_class': {k: dict(v, agree_live_pct=round(100.0 * v['agree_live'] / max(v['n'], 1), 1),
                                 agree_recorded_pct=round(100.0 * v['agree_recorded'] / max(v['n'], 1), 1))
                         for k, v in sorted(u_cls.items(), key=lambda kv: -kv[1]['n'])}}
    gate = {'ok': True, 'reasons': []}
    if baseline:
        b_entries = {e.get('id'): e for e in (baseline.get('entries') or [])}
        # a fail the baseline already carried belongs to whoever owns that template; it is reported, it does not
        # block the stage being gated. A new fail or a new regression does.
        carried = [e['id'] for e in entries if e['verdict'] == 'fail' and (b_entries.get(e['id']) or {}).get('verdict') == 'fail']
        new_fail = [e['id'] for e in entries if e['verdict'] == 'fail' and e['id'] not in carried]
        new_regressed = [e['id'] for e in entries if e['verdict'] == 'regressed'
                         and (b_entries.get(e['id']) or {}).get('verdict') not in ('regressed', 'fail')]
        if carried:
            gate['reasons'].append(f"{len(carried)} failed replies carried from the baseline (not this stage's): {', '.join(carried[:6])}")
        if new_fail:
            gate['ok'] = False
            gate['reasons'].append(f"{len(new_fail)} newly failed replies: {', '.join(new_fail[:12])}")
        if new_regressed:
            gate['ok'] = False
            gate['reasons'].append(f"{len(new_regressed)} newly regressed against the baseline: {', '.join(new_regressed[:12])}")
        b_classes = (baseline.get('by_class') or {})
        for cls, row in out_classes.items():
            b = b_classes.get(cls) or {}
            if b.get('n', 0) >= 5 and row['n'] >= 5 and (b.get('agreement', 0) - row['agreement']) > CLASS_DROP_POINTS:
                gate['ok'] = False
                gate['reasons'].append(f"{cls}: agreement {b.get('agreement')} -> {row['agreement']}")
        rep['changed_vs_baseline'] = sum(1 for e in entries if e.get('vs_baseline') == 'changed')
        rep['relabeled_vs_baseline'] = sum(1 for e in entries if e.get('vs_baseline') == 'relabel')
    else:
        if verdicts['fail']:
            gate['ok'] = False
            gate['reasons'].append(f"{verdicts['fail']} failed replies")
        regressed = [e for e in entries if e['verdict'] == 'regressed']
        if regressed:
            gate['reasons'].append(f"{len(regressed)} regressed against the recorded route (no baseline given; read them)")
    rep['gate'] = gate
    return rep


def run(cases, *, baseline=None, mode='door', limit=0):
    cases = list(cases or [])
    if limit:
        cases = cases[:int(limit)]
    t0 = time.time()
    if mode == 'service':
        lives, effects = run_service(cases)
    else:
        lives, effects = run_door(cases)
    b_entries = {e.get('id'): e for e in ((baseline or {}).get('entries') or [])}
    entries = [_entry(c, l, b_entries.get(c.get('id'))) for c, l in zip(cases, lives)]
    rep = summarize(entries, baseline)
    rep.update({'mode': mode, 'seconds': int(time.time() - t0), 'side_effects': effects, 'entries': entries,
                'would_charge': sum(1 for l in lives if l.get('would_charge')),
                'would_write': sum(1 for l in lives if l.get('would_write'))})
    return rep


def render(rep):
    """Plain summary, no client text."""
    v = rep['verdicts']
    lines = [f"replayed {rep['n']} asks in {rep.get('mode')} mode ({rep.get('seconds', 0)}s): "
             f"same {v['same']}, drift {v['drift']}, regressed {v['regressed']}, fail {v['fail']}, unchecked {v['unchecked']}; "
             f"class agreement {rep['agreement']}%"]
    if 'changed_vs_baseline' in rep:
        lines.append(f"changed against the baseline: {rep['changed_vs_baseline']}"
                     + (f"; relabeled (same answer, the route named differently): {rep['relabeled_vs_baseline']}" if rep.get('relabeled_vs_baseline') else ''))
    if rep.get('mode') == 'service':
        lines.append(f"stubs recorded: {rep.get('would_charge', 0)} would charge, {rep.get('would_write', 0)} would write (nothing reached a store)")
    lines.append('by class:')
    for cls, row in rep['by_class'].items():
        lines.append(f"  {cls:16s} n={row['n']:4d} agree={row['agreement']:5.1f}%  same={row['same']} drift={row['drift']} "
                     f"regressed={row['regressed']} fail={row['fail']} unchecked={row['unchecked']}")
    u = rep.get('understanding')
    if u:
        lines.append(f"understanding (shadow): on {u['n']} asks, intent agrees with the live route {u['agree_live_pct']}% and with the "
                     f"recorded route {u['agree_recorded_pct']}%; {u['flipped']} answered by a flipped executor; sources {u['by_source']}")
        for cls, row in u['by_class'].items():
            lines.append(f"  {cls:16s} n={row['n']:4d} live={row['agree_live_pct']:5.1f}%  recorded={row['agree_recorded_pct']:5.1f}%  flipped={row['flipped']}")
    g = rep['gate']
    lines.append(('GATE OK' if g['ok'] else 'GATE FAILED') + (': ' + '; '.join(g['reasons']) if g['reasons'] else ''))
    return '\n'.join(lines)


def save(rep, stamp=None):
    os.makedirs(RESULTS_ROOT, mode=0o700, exist_ok=True)
    stamp = stamp or time.strftime('%Y%m%d_%H%M%S', time.gmtime())
    path = os.path.join(RESULTS_ROOT, f'{stamp}.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)
    return path


def load_fixtures(path=FIXTURES):
    out = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith('#'):
                out.append(json.loads(line))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--corpus', default='', help="a stamp, 'latest', or a path to cases.jsonl(.gz)")
    ap.add_argument('--fixtures', action='store_true', help='run the committed sanitized set')
    ap.add_argument('--fetch', action='store_true', help='pull the latest corpus from S3 first')
    ap.add_argument('--baseline', default='', help='a saved results file to compare against')
    ap.add_argument('--mode', choices=('door', 'service'), default='door')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--classes', default='', help='only cases whose recorded class is in this comma list')
    ap.add_argument('--save', action='store_true', help='write the results file (default when not --fixtures)')
    ap.add_argument('--no-save', action='store_true')
    args = ap.parse_args(argv)
    if args.fetch:
        print('fetched corpus ' + _bc.fetch_from_s3('latest'))
    if args.fixtures:
        cases = load_fixtures()
    else:
        cases = _bc.load_cases(args.corpus or 'latest')
    if args.classes:
        want = {c.strip() for c in args.classes.split(',') if c.strip()}
        cases = [c for c in cases if (c.get('recorded') or {}).get('class') in want]
    baseline = None
    if args.baseline:
        with open(os.path.expanduser(args.baseline), encoding='utf-8') as fh:
            baseline = json.load(fh)
    rep = run(cases, baseline=baseline, mode=args.mode, limit=args.limit)
    print(render(rep))
    if (args.save or not args.fixtures) and not args.no_save:
        print('results: ' + save(rep))
    return 0 if rep['gate']['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
