#!/usr/bin/env python3
"""A digital twin of a named subject with no Profile IQ profile yet
(2026-11-04). Hermetic: a fake S3, a fake queue, no model, no network.

The defect: the twin job's audience pull posted a body the queue refused
(no subject_rows, no tu_demos, a guessed category, no run id) from a
worker with no queue address, and came back empty without a word.

Covers: the reproduction (the old body against the listener's own
required-field check, the new body passing it), the spec body (the
converted draft, a canonical category, the standing window, the messy
sample, the requester), the ``then`` block, phase one (queued and the job
stops; an audience already on file; a subject already building for
another twin), the refund path with the partner-safe line, phase two on
the handed-back key, the charging at confirm, and the copy on both doors
(the audience is pulled first, the twin follows, the price).
"""
from __future__ import annotations

import ast
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.pop('SYNTH_QUEUE_URL', None)
os.environ.pop('SYNTH_QUEUE_SECRET', None)

from prometheus import twin_named_pull as NP  # noqa: E402
from prometheus import twin_url_list as TUL  # noqa: E402

SUBJECT = 'Testy McSubject'
WINDOW = {'start': '2025-11-04', 'end': '2026-11-04', 'label': 'Trailing 12 months'}


# --------------------------------------------------- the listener's check
# The queue listener (finished_codes/migration/synth_queue_listener.py)
# validates every new_build body before it is written to pending. The
# live function is lifted by AST when the parent repo is reachable (it
# imports Flask and creates /root/synth_queue at import); otherwise this
# verbatim copy of its new_build branch (2026-11-04) stands in.
def _embedded_validate_spec(spec, decision="new_build", body=None):
    body = body or {}
    if decision == "derive_cut":
        for k in ("name", "brand_category"):
            if k not in spec:
                return f"derive_cut requires spec.{k}"
        return None
    required = ["name", "brand_category", "subject_raw_tu", "subject_rows", "tu_demos"]
    missing = [k for k in required if k not in spec]
    if missing:
        return f"missing fields: {missing}"
    if not isinstance(spec["subject_raw_tu"], int):
        return "subject_raw_tu must be int"
    if spec["subject_raw_tu"] < 500:
        return "subject_raw_tu too small (<500 panelists)"
    if spec["subject_raw_tu"] > 9_500_000:
        return "subject_raw_tu too large"
    if spec.get("run_avid") and spec.get("subject_raw_avid") is not None:
        av = spec["subject_raw_avid"]
        if not isinstance(av, int):
            return "subject_raw_avid must be int"
        if av < 500:
            return "subject_raw_avid too small (<500 panelists)"
        if av > 8_000_000:
            return "subject_raw_avid too large"
        if av > spec["subject_raw_tu"]:
            return "subject_raw_avid must be <= subject_raw_tu"
    if not isinstance(spec["subject_rows"], list) or not spec["subject_rows"]:
        return "subject_rows must be a non-empty list"
    if not isinstance(spec["tu_demos"], dict) or not spec["tu_demos"]:
        return "tu_demos must be a non-empty dict"
    return None


def _live_validate_spec():
    for cand in (os.environ.get('FINISHED_CODES_ROOT') or '', os.path.join(ROOT, '..'), '/tmp/fc-main', '/root/finished_codes'):
        path = os.path.join(cand, 'migration', 'synth_queue_listener.py') if cand else ''
        if path and os.path.exists(path):
            try:
                tree = ast.parse(open(path, encoding='utf-8').read())
                fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_validate_spec')
                ns = {}
                exec(compile(ast.Module(body=[fn], type_ignores=[]), path, 'exec'), ns)
                return ns['_validate_spec'], path
            except Exception:
                continue
    return _embedded_validate_spec, 'embedded'


validate_spec, VALIDATOR_SOURCE = _live_validate_spec()


def listener_verdict(body):
    return validate_spec(body.get('spec') or {}, decision=str(body.get('decision') or 'new_build'), body=body)


# ------------------------------------------------------------- fixtures
class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in sorted(self.objs) if k.startswith(Prefix)]}


def _fake_spec_from_draft(d):
    """The chat converter's shape, hermetically: name, stem, category, the
    messy sample, the rows, the demos, the notes."""
    return {'name': d.get('subject'), 'file_stem': str(d.get('subject') or '').replace(' ', '_'), 'brand_category': d.get('brand_category'),
            'subject_raw_tu': 48_317, 'subject_raw_avid': 10_631, 'subject_rows': [tuple(r) for r in d.get('subject_rows') or []],
            'extra_rows': list(d.get('extra_rows') or []), 'tu_demos': d.get('tu_demos') or {}, 'avid_demos': d.get('avid_demos') or d.get('tu_demos') or {},
            'persona_notes': d.get('persona_notes') or '', 'date_range': d.get('date_range'), 'addon_cuts': d.get('addon_cuts') or []}


def _draft():
    return {'subject': SUBJECT, 'brand_category': 'HOST/PERSONALITY', 'decision': 'new_build', 'audience_type': 'general',
            'subject_rows': [['HOST/PERSONALITY', SUBJECT], ['TALENT', SUBJECT]], 'extra_rows': [],
            'tu_demos': {'GENDER': {'FEMALE': 52.1317, 'MALE': 46.7211, 'NON-BINARY': 1.1472}}, 'avid_demos': {'GENDER': {'FEMALE': 55.0, 'MALE': 44.0, 'NON-BINARY': 1.0}},
            'subject_raw_tu': 23_118, 'subject_raw_avid': 5_273, 'universe_anchor': 762_000, 'engaged_share': 1.0,
            'persona_notes': 'A regional television host.', 'date_range': {'start': '2020-01-01', 'end': '2020-12-31'}}


def _inputs(**over):
    base = {'subject': SUBJECT, 'subject_kind': 'talent', 'purpose': 'a podcast launch', 'window': dict(WINDOW), 'op': 'build',
            'ask_text': f"Build a twin of {SUBJECT}", 'charged': {'profile': 5, 'twin': 15}, 'pull_audience': True,
            'profile_run_id': 'run_named', 'requester_email': 'carolyn@example.com', 'thread_id': 't1'}
    base.update(over)
    return base


def _legacy_body(subject):
    """The body migration.digital_twin_synthesis.queue_profile_for_twin
    posted before 2026-11-04 (the shipped shape, verbatim)."""
    raw = 48_317
    return {'decision': 'new_build', 'run_avid': True, 'user_email': 'carolyn', 'requested_at': '2026-11-04T00:00:00Z',
            'spec': {'name': subject, 'file_stem': 'Testy_McSubject', 'brand_category': 'ACTOR', 'subject_raw_tu': raw,
                     'subject_raw_avid': int(raw * 0.22), 'universe_anchor': int(raw * 32.99), 'engaged_share': 1.0,
                     'anchor_source': f"US people who engaged with {subject} in the window", 'date_range': '2025-11-04 TO 2026-11-04'}}


class Reproduction(unittest.TestCase):
    def test_the_old_body_is_refused_and_the_new_body_is_accepted(self):
        old = _legacy_body(SUBJECT)
        verdict = listener_verdict(old)
        self.assertEqual(verdict, "missing fields: ['subject_rows', 'tu_demos']", f"validator from {VALIDATOR_SOURCE}")
        self.assertNotIn('then', old)
        self.assertNotIn('run_id', old)
        self.assertNotIn('username', old)
        new = NP.spec_body(_draft(), subject=SUBJECT, kind='talent', window=WINDOW, run_id='run_named',
                           requester={'username': 'carolyn', 'email': 'carolyn@example.com'}, spec_from_draft=_fake_spec_from_draft)
        self.assertIsNone(listener_verdict(new), f"validator from {VALIDATOR_SOURCE}")

    def test_the_read_workers_environment_reaches_the_local_listener_with_the_secret_alone(self):
        """The build server's read worker had the secret and no address
        (the old pull needed both and never posted). The shared queue
        helper falls back to the local listener."""
        saved = {k: os.environ.get(k) for k in ('SYNTH_QUEUE_URL', 'SYNTH_QUEUE_SECRET', 'SYNTH_LISTENER_PORT')}
        try:
            os.environ.pop('SYNTH_QUEUE_URL', None)
            os.environ['SYNTH_QUEUE_SECRET'] = 'x'
            os.environ.pop('SYNTH_LISTENER_PORT', None)
            self.assertEqual(TUL._queue_env(), ('http://127.0.0.1:8765', 'x'))
            os.environ.pop('SYNTH_QUEUE_SECRET', None)
            self.assertEqual(TUL._queue_env(), ('', ''))
            self.assertEqual(NP.queue_profile({'run_id': 'r'}), ('', 'the queue is not configured here'))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class SpecBody(unittest.TestCase):
    def _body(self, draft=None, **kw):
        args = dict(subject=SUBJECT, kind='talent', window=WINDOW, run_id='run_named', requester={'username': 'carolyn', 'email': 'carolyn@example.com'},
                    spec_from_draft=_fake_spec_from_draft)
        args.update(kw)
        return NP.spec_body(draft or _draft(), **args)

    def test_the_body_the_engine_receives(self):
        body = self._body(prompt='Build a twin of Testy McSubject for a podcast launch')
        spec = body['spec']
        self.assertIsNone(listener_verdict(body))
        self.assertEqual(spec['name'], SUBJECT)
        self.assertEqual(spec['file_stem'], 'Testy_McSubject')
        self.assertEqual(spec['brand_category'], 'HOST/PERSONALITY')
        self.assertEqual(spec['date_range'], '2025-11-04 TO 2026-11-04')      # the twin's window, not the draft's
        self.assertEqual(spec['addon_cuts'], [])
        self.assertEqual(spec['subject_rows'][0], ('HOST/PERSONALITY', SUBJECT))
        self.assertEqual(spec['tu_demos']['GENDER']['FEMALE'], 52.1317)
        self.assertIsInstance(spec['subject_raw_tu'], int)
        self.assertNotEqual(spec['subject_raw_tu'] % 10, 0)
        self.assertGreaterEqual(spec['subject_raw_tu'], 500)
        self.assertLessEqual(spec['subject_raw_avid'], spec['subject_raw_tu'])
        self.assertEqual(body['decision'], 'new_build')
        self.assertTrue(body['run_avid'])
        self.assertEqual(body['run_id'], 'run_named')
        self.assertEqual(body['user_email'], 'carolyn@example.com')
        self.assertEqual(body['username'], 'carolyn')
        self.assertEqual(body['email_to'], '')
        self.assertEqual(body['source'], NP.SOURCE)
        self.assertEqual(body['prompt'], 'Build a twin of Testy McSubject for a podcast launch')
        self.assertNotIn('then', body)              # the job adds it
        self.assertNotIn('universe_seed_csv', spec)  # a named subject is not a file-fed universe
        self.assertNotIn('\u2014', json.dumps(body))

    def test_a_category_the_catalog_does_not_know_falls_back_to_a_canonical_one(self):
        d = _draft()
        d['brand_category'] = 'REGIONAL TV HOSTS'
        body = self._body(d, kind='talent', fmt='')
        self.assertEqual(body['spec']['brand_category'], 'INFLUENCER/CREATOR')
        body2 = self._body(dict(d, brand_category=''), kind='ip', fmt='film')
        self.assertEqual(body2['spec']['brand_category'], 'MOVIE')
        self.assertTrue(NP.is_canonical_category('SERIES - COMEDY'))
        self.assertTrue(NP.is_canonical_category('STREAMING/PLATFORM'))
        self.assertFalse(NP.is_canonical_category('BOOK FANS'))

    def test_a_draft_that_renamed_or_cut_is_brought_back_to_a_fresh_build(self):
        d = _draft()
        d.update({'decision': 'existing_match', 'existing_match_s3_key': 'Testy_McSubject_01_01_2026_10_00.csv', 'addon_cuts': [{'type': 'gender', 'id': 'female'}],
                  'subject_rows': []})
        body = self._body(d)
        self.assertEqual(body['decision'], 'new_build')
        self.assertEqual(body['spec']['addon_cuts'], [])
        self.assertNotIn('existing_match_s3_key', body['spec'])
        self.assertEqual(body['spec']['subject_rows'], [('HOST/PERSONALITY', SUBJECT)])   # the anchor row is never empty

    def test_the_prompt_asks_for_a_fresh_total_universe_on_the_twins_window(self):
        p = NP.audience_prompt(SUBJECT, 'talent', 'a podcast launch', WINDOW, platform_home='YouTube')
        self.assertTrue(p.startswith(f"run a profile on {SUBJECT}"))
        self.assertIn('fresh new_build', p)
        self.assertIn('emit no addon_cuts', p)
        self.assertIn('2025-11-04 to 2026-11-04', p)
        self.assertIn(f"{SUBJECT} is a person", p)
        self.assertIn('home platform is YouTube', p)
        self.assertIn('used to a podcast launch', p)
        self.assertIn('never narrows the universe', p)

    def test_fallback_draft_is_us_shaped_messy_and_canonical(self):
        d = NP.fallback_draft(SUBJECT, 'talent', '', WINDOW, gen_pop_rows=[{'Column': 'GENDER', 'Value': 'FEMALE', 'Brand Penetration (Row)': '50.7391%'},
                                                                           {'Column': 'GENDER', 'Value': 'MALE', 'Brand Penetration (Row)': '48.1227%'},
                                                                           {'Column': 'AGE', 'Value': '18-24', 'Brand Penetration (Row)': '11.4731%'}])
        self.assertEqual(d['tu_demos']['GENDER']['FEMALE'], 50.7391)
        self.assertNotEqual(d['subject_raw_tu'] % 10, 0)
        self.assertGreaterEqual(d['subject_raw_tu'], 500)
        self.assertEqual(d['brand_category'], 'INFLUENCER/CREATOR')
        self.assertEqual(d['subject_rows'], [['INFLUENCER/CREATOR', SUBJECT]])
        self.assertIsNone(listener_verdict(NP.spec_body(d, subject=SUBJECT, kind='talent', window=WINDOW, run_id='r', requester={}, spec_from_draft=_fake_spec_from_draft)))

    def test_then_block_marks_a_named_pull_not_a_list(self):
        then = NP.then_block(_inputs(pull_profile=lambda s, w: ''), job_id='job1', username='carolyn', extras={'user': 'carolyn'})
        self.assertEqual((then['kind'], then['job_id'], then['username']), ('twin_build', 'job1', 'carolyn'))
        self.assertIs(then['inputs']['from_list'], False)
        self.assertIs(then['inputs']['pull_audience'], True)
        self.assertEqual(then['inputs']['profile_key'], '')
        self.assertEqual((then['inputs']['source'], then['inputs']['op']), ('profile', 'build'))
        self.assertNotIn('pull_profile', then['inputs'])
        self.assertEqual(then['charged'], {'profile': 5, 'twin': 15})
        self.assertEqual(then['extras'], {'user': 'carolyn'})
        json.dumps(then)


class Chaining(unittest.TestCase):
    def _run(self, inputs, post, status_fn=None, on_file=None, refunds=None, job_id='job1'):
        status = {}
        s3 = FakeS3()
        fake_app = types.SimpleNamespace(refund_credit=lambda uname, credits=0, reason='', **kw: (refunds if refunds is not None else []).append((uname, credits, reason)) or True)
        saved = sys.modules.get('app')
        sys.modules['app'] = fake_app
        try:
            out = NP.run_pull_in_job(inputs, 'carolyn', job_id, {'job_id': job_id}, lambda j, r: status.update(r), s3=s3, bucket='b', usage_extras={'user': 'carolyn'},
                                     draft_fn=lambda p: None, spec_from_draft=_fake_spec_from_draft, post=post,
                                     status_fn=status_fn or (lambda rid: {'status': 'unknown'}), on_file=on_file or (lambda s3_, subj, w=None: ''))
        finally:
            if saved is not None:
                sys.modules['app'] = saved
            else:
                sys.modules.pop('app', None)
        return out, status, s3

    def test_phase_one_queues_the_audience_with_the_twin_on_the_body_and_stops(self):
        posted = []

        def post(body):
            posted.append(body)
            return 200, {'run_id': body['run_id'], 'status': 'queued'}
        inputs = _inputs()
        out, status, _s3 = self._run(inputs, post)
        self.assertIsNone(out)                                   # the job stops; the engine hands the twin back when the audience lands
        self.assertEqual(status['status'], 'waiting')
        self.assertEqual(status['stage'], NP.waiting_stage(SUBJECT))
        self.assertIn('usually under an hour', status['stage'])
        self.assertTrue(status['waiting_on_profile'])
        self.assertEqual(status['profile_run_id'], 'run_named')
        body = posted[0]
        self.assertEqual(body['run_id'], 'run_named')
        self.assertIsNone(listener_verdict(body), f"validator from {VALIDATOR_SOURCE}")
        self.assertEqual(body['spec']['brand_category'], 'INFLUENCER/CREATOR')     # the US-shaped draft stood in (no model here)
        then = body['then']
        self.assertEqual((then['kind'], then['job_id'], then['username']), ('twin_build', 'job1', 'carolyn'))
        self.assertIs(then['inputs']['pull_audience'], True)
        self.assertIs(then['inputs']['from_list'], False)
        self.assertEqual(then['inputs']['profile_key'], '')
        self.assertEqual(then['charged'], {'profile': 5, 'twin': 15})
        self.assertEqual(then['extras'], {'user': 'carolyn'})
        self.assertEqual(body['username'], 'carolyn')
        self.assertEqual(body['user_email'], 'carolyn@example.com')

    def test_an_audience_already_on_file_is_used_and_its_charge_comes_back(self):
        refunds = []
        out, status, _s3 = self._run(_inputs(), post=lambda b: (200, {'run_id': 'never'}), on_file=lambda s3_, subj, w=None: 'Testy_McSubject_11_01_2026_10_00.csv', refunds=refunds)
        self.assertEqual(out, 'Testy_McSubject_11_01_2026_10_00.csv')
        self.assertEqual(status['profile_key'], out)
        self.assertEqual(status['profile_state'], 'on_file')
        self.assertEqual([(c, 'already on file' in r) for _u, c, r in refunds], [(5, True)])

    def test_a_restarted_job_never_posts_the_same_audience_twice(self):
        posted = []
        out, status, _s3 = self._run(_inputs(), post=lambda b: posted.append(b) or (200, {'run_id': 'x'}), status_fn=lambda rid: {'status': 'running', 'run_id': rid})
        self.assertIsNone(out)
        self.assertEqual(posted, [])
        self.assertEqual(status['status'], 'waiting')
        out2, status2, _s3 = self._run(_inputs(), post=lambda b: (200, {'run_id': 'x'}),
                                       status_fn=lambda rid: {'status': 'tu_done', 'tu_key': 'Testy_McSubject_11_04_2026_18_00.csv', 'tu_registered_in_dashboard': True})
        self.assertEqual(out2, 'Testy_McSubject_11_04_2026_18_00.csv')

    def test_a_subject_already_building_for_another_twin_waits_on_that_run(self):
        """The listener's per-subject lock answers 409 with the active run:
        the twin waits on it and leaves its then block where the engine
        fires every waiting twin when that run lands."""
        out, status, s3 = self._run(_inputs(), post=lambda b: (409, {'error': 'a build for this subject is already in progress', 'existing_run_id': 'run_other'}), job_id='job2')
        self.assertIsNone(out)
        self.assertEqual(status['status'], 'waiting')
        self.assertEqual(status['profile_run_id'], 'run_other')
        self.assertTrue(status['joined_run'])
        key = NP.waiting_key('run_other', 'job2')
        self.assertEqual(key, 'system/digital_twins/waiting/run_other/job2.json')
        then = json.loads(s3.objs[key])
        self.assertEqual((then['kind'], then['job_id'], then['username']), ('twin_build', 'job2', 'carolyn'))
        self.assertIs(then['inputs']['pull_audience'], True)

    def test_a_pull_that_never_queues_refunds_both_with_the_partner_safe_line(self):
        refunds = []
        out, status, _s3 = self._run(_inputs(), post=lambda b: (400, {'error': 'spec validation: tu_demos must be a non-empty dict'}), refunds=refunds)
        self.assertEqual(out, '')
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['error'], NP.FAIL_LINE)
        self.assertEqual(sorted(c for _u, c, _r in refunds), [5, 15])
        for banned in ('rebuild', 'rerun', 'held', 'queue', 'spec', 'synth', 'pipeline'):
            self.assertNotIn(banned, status['error'].lower())

    def test_an_engine_failure_is_the_engines_refund(self):
        refunds = []
        out, status, _s3 = self._run(_inputs(), post=lambda b: (200, {'run_id': b['run_id']}), status_fn=lambda rid: {'status': 'failed'}, refunds=refunds)
        self.assertEqual(out, '')
        self.assertEqual(refunds, [])
        self.assertEqual(status['error'], NP.FAIL_LINE)


class JobPath(unittest.TestCase):
    """The twin job itself: phase one routes a named subject with no
    audience through the pull; phase two builds on the handed-back key."""

    def setUp(self):
        from prometheus import host as _hostmod
        self.s3 = FakeS3()
        _hostmod.host.bind(s3_client=self.s3, bucket='b', ask_hint=lambda **k: None)
        self.saved = (NP.queue_profile, NP.SPEC_FROM_DRAFT, NP.audience_on_file)
        self.posted = []

        def fake_queue(body, post=None):
            self.posted.append(body)
            return body['run_id'], ''
        NP.queue_profile = fake_queue
        NP.SPEC_FROM_DRAFT = _fake_spec_from_draft
        NP.audience_on_file = lambda s3_, subj, w=None: ''

    def tearDown(self):
        NP.queue_profile, NP.SPEC_FROM_DRAFT, NP.audience_on_file = self.saved

    def test_phase_one_routes_a_named_subject_with_no_audience_through_the_pull(self):
        from prometheus import digital_twin_job as J
        inputs = _inputs()
        self.s3.put_object(Bucket='b', Key=f"{J.JOB_PREFIX}job3.json", Body=json.dumps({'job_id': 'job3', 'user': 'carolyn', 'status': 'queued', 'inputs': inputs}))
        rec = J.run_job('job3', 'carolyn', inputs, {'user': 'carolyn'})
        self.assertEqual(rec['status'], 'waiting')
        self.assertEqual(rec['stage'], NP.waiting_stage(SUBJECT))
        self.assertEqual(len(self.posted), 1)
        body = self.posted[0]
        self.assertIsNone(listener_verdict(body))
        self.assertEqual(body['then']['job_id'], 'job3')
        self.assertEqual(body['run_id'], 'run_named')
        saved = json.loads(self.s3.objs[f"{J.JOB_PREFIX}job3.json"])
        self.assertEqual(saved['status'], 'waiting')
        self.assertTrue(saved['waiting_on_profile'])

    def test_a_job_that_arrives_undecided_decides_the_pull_itself(self):
        """An older job document (or a confirm whose catalog read failed):
        no pull_audience on the inputs, no profile on file. The job
        queues the audience rather than building on research alone."""
        from prometheus import digital_twin_job as J
        inputs = {'subject': SUBJECT, 'subject_kind': 'talent', 'purpose': 'a podcast launch', 'op': 'build', 'source': '', 'profile_key': ''}
        rec = J.run_job('job4', 'carolyn', inputs, {})
        self.assertEqual(rec['status'], 'waiting')
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0]['then']['inputs']['charged'], {'profile': 0, 'twin': 0})

    def test_a_group_shaped_by_demographics_never_pulls(self):
        from prometheus import digital_twin_job as J
        from migration import digital_twin_synthesis as SYN
        saved = (SYN.build_twin, SYN.persist, J._grant, J._thread_note)
        try:
            SYN.build_twin = lambda inputs, cj, **kw: {'meta': {'name': 'Women 25-34', 'size_us': 1_234_567}}
            SYN.persist = lambda s3_, payload, username, job_id: 'digital_twins/runs/w.json.gz'
            J._grant = lambda username, key: None
            J._thread_note = lambda username, tid, text, meta: None
            rec = J.run_job('job5', 'carolyn', {'subject': 'Women 25-34', 'source': 'demographics', 'demographics': {'gender': ['Women'], 'age': ['25-34']}, 'op': 'build'}, {})
        finally:
            SYN.build_twin, SYN.persist, J._grant, J._thread_note = saved
        self.assertEqual(rec['status'], 'done')
        self.assertEqual(self.posted, [])

    def test_phase_two_builds_on_the_handed_back_key_and_says_the_audience_was_pulled_first(self):
        from prometheus import digital_twin_job as J
        from migration import digital_twin_synthesis as SYN
        import prometheus.delivered as _delivered
        seen = {}
        saved = (SYN.build_twin, SYN.persist, J._grant, J._thread_note, _delivered.on_done)
        try:
            SYN.build_twin = lambda inputs, cj, **kw: seen.setdefault('inputs', dict(inputs)) and {'meta': {'name': SUBJECT, 'subject': SUBJECT, 'size_us': 2_345_671, 'profile_key': inputs['profile_key']}}
            SYN.persist = lambda s3_, payload, username, job_id: 'digital_twins/runs/tm.json.gz'
            J._grant = lambda username, key: seen.setdefault('grant', (username, key))
            J._thread_note = lambda username, tid, text, meta: seen.setdefault('note', text)
            _delivered.on_done = lambda kind, **kw: seen.setdefault('email', kw.get('email'))
            # the engine's handoff: the then block's inputs plus the landed key (an older engine also forced from_list True;
            # the named pull's own flag wins)
            inputs = _inputs(profile_key='Testy_McSubject_11_04_2026_18_00.csv', source='profile', from_list=True, profile_pulled=True)
            self.s3.put_object(Bucket='b', Key=f"{J.JOB_PREFIX}job6.json", Body=json.dumps({'job_id': 'job6', 'user': 'carolyn', 'status': 'queued', 'queued': True, 'inputs': inputs}))
            rec = J.run_job('job6', 'carolyn', inputs, {})
        finally:
            SYN.build_twin, SYN.persist, J._grant, J._thread_note, _delivered.on_done = saved
        self.assertEqual(rec['status'], 'done')
        self.assertEqual(self.posted, [])
        self.assertEqual(seen['inputs']['profile_key'], 'Testy_McSubject_11_04_2026_18_00.csv')
        self.assertEqual(seen['inputs']['source'], 'profile')
        self.assertIsNone(seen['inputs'].get('pull_profile'))
        self.assertIs(seen['email'], True)
        self.assertIn(f"built on the {SUBJECT} audience pulled first", rec['ready_message'])
        self.assertIn('Select Profile', rec['ready_message'])
        self.assertNotIn('your list', rec['ready_message'])
        self.assertEqual(seen['grant'], ('carolyn', 'digital_twins/runs/tm.json.gz'))


class Charging(unittest.TestCase):
    def test_a_pull_charges_the_audience_and_the_twin_once_at_confirm(self):
        charges = []

        def consume(uname, description=None, pull_type=None, credits_used=0, job_id=None, **kw):
            charges.append((pull_type, credits_used, job_id))
            return True
        charged, err = NP.charge({'username': 'carolyn'}, 'carolyn', SUBJECT, pull=True, profile_run_id='run_named', twin_job_id='job1',
                                 consume_credit=consume, is_free=lambda u, n: False)
        self.assertIsNone(err)
        self.assertEqual(charged, {'profile': NP.PROFILE_CREDITS, 'twin': 0})           # the twin is included (2026-11-05): the audience build alone
        self.assertEqual(charges, [('Chatbot Profile IQ (new_build)', 5, 'run_named')])

    def test_a_subject_on_file_charges_the_twin_alone(self):
        charges = []

        def consume(uname, description=None, pull_type=None, credits_used=0, job_id=None, **kw):
            charges.append((pull_type, credits_used, job_id))
            return True
        charged, err = NP.charge({'username': 'carolyn'}, 'carolyn', SUBJECT, pull=False, twin_job_id='job1', consume_credit=consume, is_free=lambda u, n: False)
        self.assertIsNone(err)
        self.assertEqual(charged, {'profile': 0, 'twin': 0})                             # nothing: the twin is included (2026-11-05)
        self.assertEqual(charges, [])

    def test_funds_short_charges_nothing_and_says_so(self):
        charged, err = NP.charge({'username': 'carolyn'}, 'carolyn', SUBJECT, pull=True, profile_run_id='r', twin_job_id='j',
                                 consume_credit=lambda *a, **k: False, is_free=lambda u, n: False)
        self.assertEqual(charged, {'profile': 0, 'twin': 0})
        self.assertIn('cannot cover it right now', err)
        self.assertIn('$300', err)                                          # the audience build; the twin is included (2026-11-05)
        self.assertIn('the twin is included', err)
        charged2, err2 = NP.charge({'username': 'carolyn'}, 'carolyn', SUBJECT, pull=False, twin_job_id='j', consume_credit=lambda *a, **k: False, is_free=lambda u, n: False)
        self.assertEqual(charged2, {'profile': 0, 'twin': 0})
        self.assertIsNone(err2)                                             # nothing to charge for a twin on file

    def test_a_free_seat_is_never_charged(self):
        charged, err = NP.charge({'username': 'jenna', 'role': 'super_admin'}, 'jenna', SUBJECT, pull=True, profile_run_id='r', twin_job_id='j',
                                 consume_credit=lambda *a, **k: (_ for _ in ()).throw(AssertionError('charged a free seat')), is_free=lambda u, n: True)
        self.assertIsNone(err)
        self.assertEqual(charged, {'profile': 0, 'twin': 0})


class Copy(unittest.TestCase):
    BANNED = ('synth', 'pipeline', 'hostmap', 'claude', 'queue', 'rebuild', 'rerun', 'held', '\u2014', '\u2013')

    def test_the_prometheus_card_says_the_audience_is_pulled_first_with_the_price(self):
        from prometheus import digital_twin_lane as lane
        parsed = {'subject': SUBJECT, 'subject_kind': 'talent', 'purpose': 'a podcast launch', 'window': dict(WINDOW)}
        card = lane.confirm_reply(parsed, on_file='')
        self.assertIn(f"- Audience: no {SUBJECT} audience is on file yet, so it is pulled first (usually under an hour), then the twin is built from it", card)
        self.assertIn('- Price: $300 charged now for the audience build; the twin and its first two runs are included (each group you open on it is priced per group); each further run $100.', card)
        self.assertIn("Say 'Build the twin'", card)
        for b in self.BANNED:
            self.assertNotIn(b, card.lower())
        card2 = lane.confirm_reply(parsed, on_file='Testy_McSubject_11_01_2026_10_00.csv')
        self.assertIn(f"- Audience: built on the {SUBJECT} audience already on file", card2)
        self.assertIn('- Price: the twin and its first two runs are included (each group you open on it is priced per group); each further run $100', card2)
        self.assertNotIn('$1,050', card2)

    def test_an_idea_and_a_demographic_group_are_never_pulled(self):
        """An idea has no audience on file by definition (it reads from
        research); a group shaped by demographics is Gen Pop cut to the
        picks. Neither is charged for an audience build."""
        from prometheus import digital_twin_lane as lane
        self.assertFalse(NP.pullable({'subject': 'A dating show set in Antarctica', 'subject_kind': 'idea'}))
        self.assertFalse(NP.pullable({'subject': 'Women 25-34', 'source': 'demographics'}))
        self.assertFalse(NP.pullable({'subject': SUBJECT, 'profile_key': 'Testy_McSubject_11_01_2026_10_00.csv'}))
        self.assertTrue(NP.pullable({'subject': SUBJECT, 'subject_kind': 'talent'}))
        card = lane.confirm_reply({'subject': 'A dating show set in Antarctica', 'subject_kind': 'idea', 'purpose': 'a pitch', 'window': dict(WINDOW)})
        self.assertNotIn('- Audience:', card)
        self.assertIn('- Price: the twin and its first two runs are included (each group you open on it is priced per group); each further run $100', card)
        started, charges = [], []
        job_id, line, err = lane.start_build({'username': 'carolyn'}, 'carolyn', {'subject': 'A dating show set in Antarctica', 'subject_kind': 'idea', 'purpose': 'a pitch'},
                                             start_fn=lambda kind, job_id, username, inputs, extras, sw, runner: started.append(inputs) or {'job_id': job_id},
                                             consume_credit=lambda uname, **kw: charges.append(kw.get('pull_type')) or True, is_free=lambda u, n: False)
        self.assertIsNone(err)
        self.assertIs(started[0]['pull_audience'], False)
        self.assertEqual(charges, [])                                                     # the twin is included (2026-11-05)

    def test_the_prometheus_confirm_charges_both_and_queues_the_pull(self):
        from prometheus import digital_twin_lane as lane
        started, charges = [], []

        def start_fn(kind, job_id, username, inputs, extras, status_write, runner):
            started.append((kind, job_id, username, inputs))
            return {'job_id': job_id, 'status': 'queued'}

        def consume(uname, description=None, pull_type=None, credits_used=0, job_id=None, **kw):
            charges.append((pull_type, credits_used, job_id))
            return True
        user = {'username': 'carolyn', 'email': 'carolyn@example.com', 'role': 'user'}
        confirm = {'subject': SUBJECT, 'subject_kind': 'talent', 'purpose': 'a podcast launch', 'window': dict(WINDOW)}
        job_id, line, err = lane.start_build(user, 'carolyn', confirm, thread_id='t1', ask_text='Build the twin', start_fn=start_fn, consume_credit=consume,
                                             is_free=lambda u, n: False, on_file='')
        self.assertIsNone(err)
        self.assertIn(f"No {SUBJECT} audience is on file yet, so I am pulling it first", line)
        self.assertIn('usually under an hour', line)
        self.assertIn('by email', line)
        kind, jid, username, inputs = started[0]
        self.assertEqual((kind, jid, username), ('twin_build', job_id, 'carolyn'))
        self.assertIs(inputs['pull_audience'], True)
        self.assertTrue(inputs['profile_run_id'])
        self.assertEqual(inputs['charged'], {'profile': 5, 'twin': 0})                   # the audience build; the twin is included (2026-11-05)
        self.assertEqual(inputs['requester_email'], 'carolyn@example.com')
        self.assertEqual([c[0] for c in charges], ['Chatbot Profile IQ (new_build)'])
        self.assertEqual(charges[0][2], inputs['profile_run_id'])   # the audience charge carries the run id the engine refunds by
        self.assertEqual(len(charges), 1)                            # no twin line: the twin is included (2026-11-05)
        for b in self.BANNED:
            self.assertNotIn(b, line.lower())
        # a subject on file: the twin alone, built on that audience
        started.clear()
        charges.clear()
        job_id2, line2, err2 = lane.start_build(user, 'carolyn', confirm, start_fn=start_fn, consume_credit=consume, is_free=lambda u, n: False,
                                                on_file='Testy_McSubject_11_01_2026_10_00.csv')
        self.assertIsNone(err2)
        self.assertTrue(line2.startswith('On it. Building the Testy McSubject twin now'))
        self.assertIs(started[0][3]['pull_audience'], False)
        self.assertEqual(started[0][3]['profile_key'], 'Testy_McSubject_11_01_2026_10_00.csv')
        self.assertEqual([c[0] for c in charges], [])                 # a twin on an audience on file charges nothing (included, 2026-11-05)

    def test_funds_short_on_the_prometheus_door_queues_nothing(self):
        from prometheus import digital_twin_lane as lane
        started = []
        job_id, line, err = lane.start_build({'username': 'carolyn'}, 'carolyn', {'subject': SUBJECT, 'purpose': 'x'}, start_fn=lambda *a: started.append(a),
                                             consume_credit=lambda *a, **k: False, is_free=lambda u, n: False, on_file='')
        self.assertEqual(job_id, '')
        self.assertIn('cannot cover it right now', err['reply'])
        self.assertEqual(started, [])

    def test_the_twins_tab_door_says_it_too(self):
        import inspect
        import digital_twins_api as API
        src = inspect.getsource(API.dt_build)
        self.assertIn('TNP.audience_on_file', src)
        self.assertIn('TNP.charge(', src)
        self.assertIn("'pull_audience': pull", src)
        self.assertIn('TNP.promise(subject) if pull else J.build_promise(subject)', src)
        self.assertIn("'pulls_audience': pull", src)

    def test_the_walkthrough_review_prices_the_audience_pull(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn("profile: /^audience build\\b/i", page)
        self.assertIn("<i>The audience</i>, pulled first (usually under an hour)</span><b>' + usd(pa) + '</b>", page)
        self.assertIn("Charged now</span><b>' + usd(pa) + '</b>", page)                 # the audience build alone; the twin is included (2026-11-05)
        self.assertIn("<i>The twin</i>, with its first two runs</span><b>Included</b>", page)
        self.assertIn('The audience is pulled first (usually under an hour), then the twin is built from it; both land on the Twins tab and Prometheus confirms in the chat and by email.', page)
        self.assertIn('If this audience is already on file, nothing is charged now.', page)
        self.assertIn('waited < 7200000', page)

    def test_the_pricing_lines_carry_the_audience_build(self):
        from migration import digital_twins as DT
        lines = DT.pricing_lines()
        self.assertTrue(any(l.startswith("Audience build - $300 when the twin's subject has no audience on file yet") for l in lines))
        self.assertTrue(lines[0].startswith('Digital Twin - included'))     # the twin line stays the first line the tab reads; the group is the priced unit (2026-11-05)
        pr = NP.price()
        self.assertEqual((pr['profile_usd'], pr['twin_usd'], pr['total_usd']), (300.0, 0.0, 300.0))

    def test_waiting_and_ready_copy(self):
        self.assertEqual(NP.waiting_stage(SUBJECT), f"pulling the {SUBJECT} audience first (usually under an hour); the twin follows")
        r = NP.ready_message('Testy McSubject', '2.35M', SUBJECT)
        self.assertIn('built on the Testy McSubject audience pulled first', r)
        self.assertIn('Select Profile', r)
        for b in self.BANNED:
            self.assertNotIn(b, (r + NP.promise(SUBJECT) + NP.FAIL_LINE).lower())

    def test_the_old_pull_is_no_longer_called_from_the_job(self):
        import inspect
        from prometheus import digital_twin_job as J
        src = inspect.getsource(J.run_job)
        self.assertNotIn('queue_profile_for_twin(', src)
        self.assertIn("_TNP.run_pull_in_job", src)
        self.assertIn("inputs['pull_profile'] = None", src)


if __name__ == '__main__':
    print(f"[named-pull test] listener validator: {VALIDATOR_SOURCE}")
    unittest.main(verbosity=2)
