#!/usr/bin/env python3
"""The guided focus group (Digital Twins IQ, 2026-11-18).

Jenna, verbatim: "a digital twins wizard that is like focus group for
dummies ... it would ask you what your audience is. then what your end
goal is in terms of like what business question do you want to
understand? like is it if I do x will y happen, is it will x win then
prometheus builds the room and runs through a list of questions aimed at
starting macro and getting micro to give you the responses and
prediction of ending with a full focus group outcome of your total
business question goal."

Held here:
  * the kind of question is read from the words when no chip is given;
  * the planning desk's answer is taken within bounds (five to eight
    questions, wide to narrow, the settling question last, options kept,
    banned words refused) and the deterministic plan stands when the
    desk gives nothing or something unusable;
  * the run asks the planned questions in order through the room's own
    ask, tags each record with its plan index, saves as it goes, writes
    the outcome, and a stopped run resumes at the first unanswered
    question without asking anything twice;
  * the outcome desk's answer is scrubbed and bounded; the deterministic
    outcome reads the settling question's leading answer; the lines and
    the public view carry no method word;
  * the API: plan (nothing charged), run (a room opens as one group and
    the run starts), the session carries the guided block.

Hermetic: REGRESSION_TEST_MODE never calls a model; fakes stand in.
    python3 scripts/test_digital_twin_guided.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_guided as GD  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_room_seats import _beals  # noqa: E402

BANNED = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|research|panel|sample|archetype|persona|segment|cohort|confidence|survey|prediction)\b", re.I)


def _twin():
    t = _beals()
    t['meta']['size_us'] = 2_776_141
    t['meta']['key'] = 'digital-twins/t/beals.json.gz'
    return t


class Kind(unittest.TestCase):
    def test_the_kind_is_read_from_the_words(self):
        self.assertEqual(GD.kind_of('If we add a holiday boxed set at Target, will more people buy season two?'), 'effect')
        self.assertEqual(GD.kind_of('Will the Flashdance cut or the NCIS cut win them over?'), 'contest')
        self.assertEqual(GD.kind_of('Should we sign her for a two-year activewear deal?'), 'go')
        self.assertEqual(GD.kind_of('Would this audience pay $45 for a hoodie?'), 'price')
        self.assertEqual(GD.kind_of('What do they make of her as a fashion name?'), 'open')
        self.assertEqual(GD.kind_of('anything', 'contest'), 'contest', 'the chip wins')


class Plan(unittest.TestCase):
    def test_the_deterministic_plan_runs_wide_to_narrow_and_ends_on_the_question(self):
        t = _twin()
        p = GD.plan(t, 'If we add a holiday boxed set at Target, will more people buy season two?')
        self.assertEqual(p['method'], 'deterministic')
        self.assertEqual(p['kind'], 'effect')
        self.assertTrue(GD.MIN_QUESTIONS <= len(p['questions']) <= GD.MAX_QUESTIONS)
        self.assertEqual([q['level'] for q in p['questions']][:2], ['macro', 'macro'])
        self.assertEqual(p['questions'][-1]['level'], 'micro')
        self.assertEqual(p['final'], len(p['questions']) - 1)
        self.assertEqual(p['questions'][-1]['kind'], 'yes_no')
        self.assertIn('boxed set at Target', p['questions'][-1]['q'])
        for q in p['questions']:
            self.assertIsNone(BANNED.search(q['q']), q['q'])
        c = GD.plan(t, 'Will the Flashdance cut or the NCIS cut win them over?')
        self.assertEqual(c['kind'], 'contest')
        self.assertEqual(c['questions'][-1]['kind'], 'multiple_choice')

    def test_the_desk_plan_is_taken_within_bounds(self):
        t = _twin()
        data = {'goal': 'Whether a holiday boxed set at Target lifts season two.', 'success': 'A clear majority say they would buy season two with the set on the shelf.',
                'questions': [{'q': 'How do you watch her shows today', 'kind': 'multiple_choice', 'options': ['Live', 'Streaming', 'Clips', 'I do not'], 'level': 'macro', 'tests': 'Where they stand.'},
                              {'q': 'What draws you back to a show after a season ends?', 'kind': 'open', 'level': 'macro', 'tests': 'The pull.'},
                              {'q': 'Do you buy boxed sets at all anymore?', 'kind': 'yes_no', 'level': 'mid', 'tests': 'The habit.'},
                              {'q': 'Would a holiday boxed set at Target make season two feel like an event?', 'kind': 'yes_no', 'level': 'mid', 'tests': 'The pull of the set.'},
                              {'q': 'Our synthetic panel suggests', 'kind': 'open', 'level': 'micro', 'tests': 'x'},
                              {'q': 'If the boxed set were at Target this holiday, would you buy season two?', 'kind': 'yes_no', 'level': 'micro', 'tests': 'The question itself.'}],
                'final': 5}
        p = GD.plan(t, 'If we add a holiday boxed set at Target, will more people buy season two?', call=lambda *a: data)
        self.assertEqual(p['method'], 'reasoned')
        self.assertEqual(len(p['questions']), 5, 'the banned line is dropped')
        self.assertEqual(p['final'], 4, 'the settling question is still last')
        self.assertTrue(p['questions'][0]['q'].endswith('?'))
        self.assertEqual(p['questions'][0]['options'], ['Live', 'Streaming', 'Clips', 'I do not'])
        self.assertEqual(p['asked'], 'If we add a holiday boxed set at Target, will more people buy season two?')
        # too few usable questions: the deterministic plan stands
        thin = GD.plan(t, 'Should we sign her?', call=lambda *a: {'goal': 'x', 'questions': data['questions'][:2]})
        self.assertEqual(thin['method'], 'deterministic')
        self.assertEqual(GD.plan(t, 'Should we sign her?', call=lambda *a: None)['method'], 'deterministic')


class Run(unittest.TestCase):
    def _sess(self, t):
        return P.open_session([t], title='Guided', session_id='guided-1')

    def test_the_run_asks_in_order_tags_saves_and_writes_the_outcome(self):
        t = _twin()
        sess = self._sess(t)
        plan = GD.plan(t, 'If we add a holiday boxed set at Target, will more people buy season two?')
        asked, saves = [], []

        def fake_ask(sess_, q, **kw):
            asked.append((q, kw.get('kind'), tuple(kw.get('options') or [])))
            rec = {'id': f"q{len(asked)}", 'q': q, 'kind': kw.get('kind') or 'open', 'stage': 'done', 'finished_at': 'now', 'read': f"The room on: {q}",
                   'options': [{'id': 'yes', 'label': 'Yes', 'pct': 61.2, 'people': 1_699_000, 'seats_n': 10}, {'id': 'no', 'label': 'No', 'pct': 38.8, 'people': 1_077_141, 'seats_n': 6}] if (kw.get('kind') or '') == 'yes_no' else [],
                   'voices': [{'name': 'Mateo', 'line': 'I would.'}]}
            sess_.setdefault('questions', []).append(rec)
            return rec
        payload = {'meta': t['meta'], 'panel_sessions': [sess]}
        job = GD.start_run(None, payload, 'k', sess, plan, username='jenna', ask=fake_ask, save=lambda p: saves.append(len(sess.get('questions') or [])), run_async=False)
        self.assertEqual(job['op'], 'guided_room')
        self.assertEqual([a[0] for a in asked], [q['q'] for q in plan['questions']], 'every planned question, in order')
        self.assertEqual(asked[-1][1], 'yes_no')
        self.assertEqual([r['guided_i'] for r in sess['questions']], list(range(len(plan['questions']))))
        self.assertGreaterEqual(len(saves), len(plan['questions']) + 1, 'saved as each question lands and at the end')
        g = sess['guided']
        self.assertEqual(g['status'], 'done')
        self.assertEqual(g['done'], len(plan['questions']))
        out = g['outcome']
        self.assertNotIn('method', out, 'the stored outcome carries no method word')
        self.assertEqual(out['call'], 'yes')
        self.assertIn('61.2%', out['answer'])
        self.assertIn('1,699,000 people', out['answer'])
        lines = GD.outcome_lines(sess)
        self.assertTrue(lines[0].startswith('The question:') and lines[1].startswith('The outcome:'))
        for l in lines:
            self.assertIsNone(BANNED.search(l), l)
        pub = GD.public_guided(sess)
        self.assertEqual(pub['status'], 'done')
        self.assertNotIn('method', pub['outcome'])
        self.assertIsNone(BANNED.search(json.dumps(pub)), 'the public view carries no method word')

    def test_a_stopped_run_resumes_without_asking_twice(self):
        t = _twin()
        sess = self._sess(t)
        plan = GD.plan(t, 'Should we sign her for a two-year activewear deal?')
        n = len(plan['questions'])
        asked = []

        def flaky(sess_, q, **kw):
            asked.append(q)
            if len(asked) == 3:
                raise RuntimeError('the model went away')
            rec = {'id': f"q{len(asked)}", 'q': q, 'kind': kw.get('kind') or 'open', 'stage': 'done', 'finished_at': 'now', 'read': 'r',
                   'options': [{'id': 'no', 'label': 'No', 'pct': 58.0, 'people': 1_610_162, 'seats_n': 9}, {'id': 'yes', 'label': 'Yes', 'pct': 42.0, 'people': 1_165_979, 'seats_n': 7}] if (kw.get('kind') or '') == 'yes_no' else []}
            sess_.setdefault('questions', []).append(rec)
            return rec
        payload = {'meta': t['meta'], 'panel_sessions': [sess]}
        GD.start_run(None, payload, 'k', sess, plan, username='jenna', ask=flaky, save=lambda p: None, run_async=False)
        self.assertEqual(sess['guided']['status'], 'stopped')
        self.assertEqual(sess['guided']['done'], 2)
        self.assertIn('resume', sess['guided']['error'])
        job2 = GD.start_run(None, payload, 'k', sess, plan, username='jenna', ask=flaky, save=lambda p: None, run_async=False)
        self.assertEqual(job2['job_id'], sess['guided']['job_id'], 'the same job picks back up')
        self.assertEqual(sess['guided']['status'], 'done')
        self.assertEqual(len(asked), n + 1, 'the two finished questions were not asked again; only the failed one and the rest')
        self.assertEqual(sorted(r['guided_i'] for r in sess['questions']), list(range(n)))
        self.assertEqual(sess['guided']['outcome']['call'], 'no')

    def test_the_outcome_desk_is_scrubbed_and_bounded(self):
        t = _twin()
        sess = self._sess(t)
        plan = GD.plan(t, 'Will this audience pay $45 for a hoodie from her collection?')
        sess['questions'] = [{'id': 'f', 'guided_i': plan['final'], 'q': plan['questions'][-1]['q'], 'kind': 'yes_no', 'stage': 'done', 'finished_at': 'now', 'read': 'Most would not.',
                              'options': [{'id': 'no', 'label': 'No', 'pct': 64.4, 'people': 1_787_835, 'seats_n': 10}, {'id': 'yes', 'label': 'Yes', 'pct': 35.6, 'people': 988_306, 'seats_n': 6}]}]
        data = {'call': 'no', 'answer': 'No: 64.4% of the audience, 1,787,835 people, would not pay $45 for the hoodie.', 'why': 'The research panel says the price is the wall. Our model of the audience is confident.',
                'numbers': ['No 64.4%, 1,787,835 people, 10 of 16 seats', 'Yes 35.6%, 988,306 people'], 'who': 'The dance and fitness followers said yes; everyone else said no.',
                'would_change': 'A price under $35, or the hoodie in the show.', 'next': 'Would you pay $30 for the same hoodie?'}
        out = GD.outcome(sess, plan, call=lambda *a: data)
        self.assertEqual(out['method'], 'reasoned')
        self.assertEqual(out['call'], 'no')
        self.assertNotIn('panel', out['why'].lower())
        self.assertNotIn('model', out['why'].lower())
        self.assertNotIn('confiden', out['why'].lower())
        self.assertEqual(len(out['numbers']), 2)
        self.assertEqual(GD.outcome(sess, plan, call=lambda *a: None)['method'], 'deterministic')


class Report(unittest.TestCase):
    """The report of the whole process: the data the page draws, the
    PDF, the CSV block, and no method word on any of it."""

    def _done_session(self):
        t = _twin()
        sess = P.open_session([t], title='Guided', session_id='guided-r')
        plan = GD.plan(t, 'If we add a holiday boxed set at Target, will more people buy season two?')

        def fake_ask(sess_, q, **kw):
            n = len(sess_.get('questions') or []) + 1
            rec = {'id': f"q{n}", 'q': q, 'kind': kw.get('kind') or 'open', 'stage': 'done', 'finished_at': 'now', 'read': f"The room on {n}.",
                   'distribution': [{'id': 'yes', 'label': 'Yes', 'pct': 61.2, 'people': 1_699_000}, {'id': 'no', 'label': 'No', 'pct': 38.8, 'people': 1_077_141}] if (kw.get('kind') or '') == 'yes_no' else [],
                   'options': [{'id': 'yes', 'label': 'Yes', 'pct': 61.2, 'people': 1_699_000, 'seats_n': 10}, {'id': 'no', 'label': 'No', 'pct': 38.8, 'people': 1_077_141, 'seats_n': 6}] if (kw.get('kind') or '') == 'yes_no' else [],
                   'voices': [{'name': 'Mateo', 'age': 34, 'city': 'Austin', 'line': 'I would buy it for my sister.', 'seat': 1}]}
            sess_.setdefault('questions', []).append(rec)
            return rec
        payload = {'meta': t['meta'], 'panel_sessions': [sess]}
        GD.start_run(None, payload, 'k', sess, plan, username='jenna', ask=fake_ask, save=lambda p: None, run_async=False)
        return payload, sess, plan

    def test_the_report_data_carries_the_whole_process_and_no_method_word(self):
        payload, sess, plan = self._done_session()
        rp = GD.report(payload, sess)
        self.assertEqual(rp['product'], 'Predictive Analytics')
        self.assertEqual(rp['goal'], plan['goal'])
        self.assertEqual(len(rp['questions']), len(plan['questions']))
        self.assertEqual([q['n'] for q in rp['questions']], list(range(1, len(plan['questions']) + 1)))
        self.assertEqual(rp['questions'][0]['level_word'], 'The big picture')
        self.assertEqual(rp['questions'][-1]['level_word'], 'The question itself')
        self.assertTrue(rp['questions'][-1]['settles'])
        self.assertEqual(rp['questions'][-1]['lead']['label'], 'Yes')
        self.assertEqual(rp['outcome']['call_word'], 'Yes')
        self.assertTrue(rp['audience']['seats'] >= 12)
        self.assertEqual(len(rp['seats']), rp['audience']['seats'])
        self.assertIsNone(BANNED.search(json.dumps(rp)), 'the report carries no method word')
        self.assertNotIn('method', json.dumps(sess['guided']), 'the stored plan and outcome carry no method word either')

    def test_the_pdf_builds_and_the_csv_carries_the_outcome_block(self):
        payload, sess, plan = self._done_session()
        try:
            import reportlab  # noqa: F401
        except Exception:
            self.skipTest('reportlab not installed')
        pdf = GD.build_report_pdf(payload, sess)
        self.assertTrue(pdf[:5] == b'%PDF-' and len(pdf) > 4000)
        self.assertTrue(GD.report_filename(payload, sess).endswith('.pdf'))
        self.assertIn('Predictive_Analytics', GD.report_filename(payload, sess))
        csv_text = P.session_csv(sess)
        self.assertIn('Predictive Analytics', csv_text)
        self.assertIn('The outcome:', csv_text)
        self.assertIsNone(BANNED.search(csv_text.split('Predictive Analytics', 1)[1]), 'the outcome block carries no method word')


class Api(unittest.TestCase):
    """The routes, on the pricing suite's harness (carol at Acme, a funded
    company wallet): the plan is free, the run opens a room and charges it
    the way any room is charged, and the session carries the guided block."""

    def test_plan_is_free_and_run_opens_a_charged_room_that_carries_the_guided_block(self):
        import test_digital_twin_pricing as TP
        h = TP.Harness(self)
        try:
            key = TP.KEY
            r = h.c.post(f'/api/digital-twins/{key}/guided/plan', json={'goal': 'If we add a holiday boxed set at Target, will more people buy season two?'})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            d = r.get_json()
            self.assertTrue(d['success'] and len(d['plan']['questions']) >= GD.MIN_QUESTIONS)
            self.assertEqual([k['id'] for k in d['kinds']], [k[0] for k in GD.KINDS])
            self.assertIsNone(BANNED.search(json.dumps(d['plan'])), 'the plan carries no method word')
            self.assertEqual(h.charges, [], 'planning is free')
            bad = h.c.post(f'/api/digital-twins/{key}/guided/plan', json={'goal': '   '})
            self.assertEqual(bad.status_code, 400)
            none = h.c.post(f'/api/digital-twins/{key}/guided/run', json={})
            self.assertEqual(none.status_code, 400, 'no plan, no room')
            r2 = h.c.post(f'/api/digital-twins/{key}/guided/run', json={'plan': d['plan']})
            self.assertEqual(r2.status_code, 200, r2.get_data(as_text=True)[:300])
            d2 = r2.get_json()
            self.assertTrue(d2['success'] and d2['job_id'] and d2['session_id'])
            self.assertEqual(len(h.charges), 1, 'one group charge, as any room')
            self.assertEqual(d2['guided']['total'], len(d['plan']['questions']))
            self.assertEqual(d2['guided']['goal'], d['plan']['goal'])
            self.assertEqual(len(d2['guided']['questions']), len(d['plan']['questions']))
            self.assertNotIn('method', d2['guided'])
            self.assertEqual(d2['session']['title'], d['plan']['goal'][:120])
            twin = h.twin()
            sess = next(x for x in twin['panel_sessions'] if x['id'] == d2['session_id'])
            self.assertEqual(sess['guided']['job_id'], d2['job_id'])
            self.assertTrue(12 <= len(sess['group']['roster']) <= 16, 'the room seats the kinds, never fewer than twelve')
            g = h.c.get(f'/api/digital-twins/{key}/panel/{d2["session_id"]}')
            self.assertEqual(g.status_code, 200)
            self.assertEqual(((g.get_json() or {}).get('session') or {}).get('id'), d2['session_id'])
            # resume on a run that is still running is a no-op that reports it
            rr = h.c.post(f'/api/digital-twins/{key}/guided/{d2["session_id"]}/resume', json={})
            self.assertEqual(rr.status_code, 200, rr.get_data(as_text=True)[:300])
            self.assertEqual(len(h.charges), 1, 'resume never charges')
            self.assertEqual(h.c.post(f'/api/digital-twins/{key}/guided/nope/resume', json={}).status_code, 404)
            rp = h.c.get(f'/api/digital-twins/{key}/guided/{d2["session_id"]}/report')
            self.assertEqual(rp.status_code, 200, rp.get_data(as_text=True)[:300])
            body = rp.get_json()
            self.assertEqual(body['report']['product'], 'Predictive Analytics')
            self.assertEqual(len(body['report']['questions']), len(d['plan']['questions']))
            self.assertEqual(h.c.get(f'/api/digital-twins/{key}/guided/nope/report').status_code, 404)
            self.assertEqual(sess.get('product'), 'predict')
        finally:
            h.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
