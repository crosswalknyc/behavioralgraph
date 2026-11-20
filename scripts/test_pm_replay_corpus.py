#!/usr/bin/env python3
"""The replay corpus and its gate (Prometheus rebuild stage 1, 2026-11-20).

Pins, hermetically (no S3, no model, no mail, no chat module, no app):

1. the intent-class table: every ask-log route the 30-day log carried
   lands in a class, the Reader's intents land in a class, and
   agreement is read through the same table;
2. the corpus builder on synthetic records: the truncated reading and
   thread reprs parse, the thread's last two turns before the ask are
   picked by text and stamp, seats are aliased, the manifest carries
   counts and no text;
3. the sanitized fixtures replay in service mode on a stub host and
   come back GATE OK with every case `same`; nothing is charged for
   real, nothing is mailed, the only stores touched are the in-memory
   ones;
4. the baseline comparison: a run whose regressions are new fails the
   gate, a run identical to its baseline passes;
5. the fixtures carry no client text markers (no seat names, no thread
   ids, no banned words).
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()               # before any prometheus module opens a store client
from pm_corpus import build_corpus as BC  # noqa: E402
from prometheus import intent_classes as IC  # noqa: E402


class IntentClasses(unittest.TestCase):
    ROUTES_30D = ('reasoned_metrics', 'bpiq_wizard', 'profile_build', 'page_analysis', 'multi_ask', 'deck', 'status_lookup',
                  'capability', 'sizing', 'priced_read', 'build_order', 'email_file', 'unprojected', 'subiq_reroute', 'short_reply',
                  'open_screen_confirm', 'where_watched', 'journey_file', 'subject_unresolved', 'read_answer', 'provenance',
                  'read_inflight_dedupe', 'derived_read', 'document', 'catalog_lookup', 'capability_question', 'pricing',
                  'question_first', 'audience_referent', 'reconcile', 'roster_offer', 'repeat_guard', 'search_demand', 'read_replay',
                  'priced_offer_acceptance', 'ledger_replay', 'journey_drilldown', 'build_reroute', 'rerun_window', 'wayfinding',
                  'digital_twin', 'referent_clarify', 'kpi_definition', 'us_scope', 'cut_clarify', 'number_provenance', 'sample_size',
                  'panel_fact', 'cut_reroute', 'quantifiability_gate', 'help_writing', 'csv_download', 'deliverable_lookup',
                  'offer_declined', 'brand_coverage', 'data_floor', 'intake_cancel', 'dashboard_behavior', 'insight_ask')

    def test_every_logged_route_has_a_class(self):
        for r in self.ROUTES_30D:
            self.assertIn(IC.class_of(r, 'answered', 'analyze'), IC.CLASSES, r)
            self.assertNotEqual(IC.class_of(r, 'answered', 'analyze'), 'other', r)

    def test_incident_classes(self):
        self.assertEqual(IC.class_of('wayfinding', 'answered'), 'wayfinding')
        self.assertEqual(IC.class_of('status_lookup', 'answered'), 'status')
        self.assertEqual(IC.class_of('email_file', 'answered'), 'send_file')
        self.assertEqual(IC.class_of('insight_ask', 'answered'), 'insight')
        self.assertEqual(IC.class_of('dashboard_behavior', 'raised'), 'dashboard_issue')
        self.assertEqual(IC.class_of('page_analysis', 'panel_offer'), 'read')     # the quote rides the default route
        self.assertEqual(IC.class_of('page_analysis', 'turn'), 'wizard')
        self.assertEqual(IC.class_of('', 'new_build', 'interpret'), 'build')
        self.assertEqual(IC.class_of('unknown', 'answered', 'deck'), 'deck')

    def test_reader_intents_land(self):
        for intent in ('exists', 'size', 'status', 'run_pending', 'build_profile', 'cut', 'journey', 'valuation', 'campaign',
                       'tracker', 'twin', 'analyze_page', 'analyze', 'compare', 'capability', 'dashboard_issue', 'command', 'deck',
                       'definition', 'pricing', 'decline', 'chat', 'wayfinding', 'send_file_order', 'insight_ask', 'continuation_rerun'):
            self.assertNotEqual(IC.class_of_intent(intent), 'other', intent)

    def test_agreement_reads_through_one_table(self):
        self.assertTrue(IC.agrees('status', 'status_lookup', 'answered'))
        self.assertFalse(IC.agrees('status', 'wayfinding', 'answered'))
        self.assertTrue(IC.agrees('analyze', 'reasoned_metrics', 'landed'))
        self.assertTrue(IC.agrees('analyze', 'page_analysis', 'answered'))        # neighbours
        self.assertFalse(IC.agrees('analyze', 'page_analysis', 'answered', strict=True))
        self.assertTrue(IC.agrees('command', 'email_file', 'answered'))
        self.assertFalse(IC.agrees('command', 'insight_ask', 'answered'))
        self.assertTrue(IC.agrees('build_profile', 'build_order', 'lane_order'))


class CorpusBuilder(unittest.TestCase):
    REC = {'ts': '2026-11-18T00:04:20Z', 'user': 'reader_one', 'view': 'profileIQ', 'question': 'how big is the audience here',
           'surface': 'analyze', 'route': 'sizing', 'outcome': 'answered', 'ms': 1200, 'subject': 'Example Brand',
           'reply': 'On Example Brand: 1,234,567 US people. The avid tier is 345,671.', 'job_id': '',
           'extra': {'result': 'answered', 'trace_id': 'abc',
                     'reading': "{'intent': 'size', 'confidence': 0.75, 'subjects': [{'as_written': 'the audience', 'catalog': '', 'kind': 'unknown'}, {'as_written': 'Example Brand sho",
                     'thread': "{'subject_key': 'profile:Example_Brand_06_28_2026_13_25.csv', 'subject_label': 'Example Brand', 'product': 'profileIQ', 'id': 'a2673cecd7'}"}}
    THREAD = [{'role': 'user', 'text': 'open the Example Brand profile', 'ts': '2026-11-17T23:50:00.000Z'},
              {'role': 'agent', 'text': 'It is open.', 'ts': '2026-11-17T23:50:02.000Z', 'meta': {'options': ['Size it', 'Cut it'], 'huge': 'x' * 5000}},
              {'role': 'user', 'text': 'how big is the audience here', 'ts': '2026-11-18T00:04:19.000Z'},
              {'role': 'agent', 'text': 'On Example Brand: 1,234,567 US people.', 'ts': '2026-11-18T00:04:21.000Z'}]

    def test_truncated_reprs_parse(self):
        rd = BC.parse_reading(self.REC['extra'])
        self.assertEqual((rd['intent'], rd['confidence']), ('size', 0.75))
        self.assertEqual(rd['subjects'][0]['as_written'], 'the audience')
        self.assertTrue(rd['truncated'])
        th = BC.parse_thread(self.REC['extra'])
        self.assertEqual(th['id'], 'a2673cecd7')
        self.assertEqual(th['subject_label'], 'Example Brand')
        self.assertEqual(BC.parse_reading({'reading': ''}), None)
        self.assertEqual(BC.parse_reading({'reading': "{'intent': 'analyze', 'confidence': 0.9, 'subj"}), {'intent': 'analyze', 'confidence': 0.9, 'subjects': [], 'flags': [], 'restate': None, 'truncated': True})

    def test_history_is_the_two_turns_before_the_ask(self):
        hist = BC.pick_history(self.THREAD, self.REC['question'], self.REC['ts'])
        self.assertEqual([t['role'] for t in hist], ['user', 'agent'])
        self.assertEqual(hist[1]['text'], 'It is open.')
        self.assertEqual(hist[1]['meta'], {'options': ['Size it', 'Cut it']})   # the oversized key is dropped
        # the ask is not in the thread yet: the last two turns before the stamp
        hist2 = BC.pick_history(self.THREAD[:2], 'something else', '2026-11-18T01:00:00Z')
        self.assertEqual(len(hist2), 2)

    def test_cases_alias_seats_and_skip_probes(self):
        recs = [dict(self.REC), dict(self.REC, user='canary:golden'), dict(self.REC, user='replay'),
                dict(self.REC, user='reader_two', ts='2026-11-18T01:00:00Z', question='thanks')]
        seats = {}
        cases = BC.build_cases(recs, lambda u, t: self.THREAD, None, seats=seats)
        self.assertEqual(len(cases), 2)
        self.assertEqual([c['seat'] for c in cases], ['s1', 's2'])
        self.assertEqual(set(seats), {'reader_one', 'reader_two'})
        c = cases[0]
        self.assertEqual(c['recorded']['class'], 'sizing')
        self.assertEqual(c['recorded']['reply_first'], 'On Example Brand: 1,234,567 US people.')
        self.assertFalse(c['recorded']['quoted'])
        self.assertIsNone(c['recorded']['mailed'])
        self.assertEqual(c['thread']['id'], 'a2673cecd7')
        self.assertEqual(len(c['history']), 2)
        self.assertEqual(c['reading']['intent'], 'size')
        self.assertEqual(c['id'], BC.case_id(self.REC))
        for k in ('user', 'email'):
            self.assertNotIn(k, c)

    def test_manifest_carries_counts_only(self):
        seats = {}
        cases = BC.build_cases([dict(self.REC)], lambda u, t: [], None, seats=seats)
        m = BC.manifest_of(cases, days=30, stamp='test', seats=seats)
        flat = json.dumps(m)
        self.assertNotIn('how big', flat)
        self.assertNotIn('reader_one', flat)
        self.assertEqual((m['n'], m['seats'], m['by_class']), (1, 1, {'sizing': 1}))

    def test_quoted_and_job_marks(self):
        rec = dict(self.REC, outcome='panel_offer', reply='Pulling this data will cost $100.', job_id='j1')
        r = BC.recorded_of(rec)
        self.assertTrue(r['quoted'])
        self.assertTrue(r['job'])
        self.assertEqual(r['class'], 'sizing')
        self.assertEqual(BC.recorded_of(dict(rec, route='page_analysis'))['class'], 'read')   # a quote on the default route


class ServiceModeReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = RC.load_fixtures()
        cls.rep = RC.run(cls.cases, mode='service')

    def test_fixtures_replay_same_and_gate_ok(self):
        v = self.rep['verdicts']
        self.assertEqual(v['fail'], 0, [e for e in self.rep['entries'] if e['verdict'] == 'fail'])
        self.assertEqual(v['regressed'], 0, [(e['id'], e['reason']) for e in self.rep['entries'] if e['verdict'] == 'regressed'])
        self.assertEqual(v['same'], len(self.cases), [(e['id'], e['recorded'], e['live']['route'], e['live']['outcome']) for e in self.rep['entries'] if e['verdict'] != 'same'])
        self.assertTrue(self.rep['gate']['ok'], self.rep['gate'])
        self.assertEqual(self.rep['agreement'], 100.0)

    def test_the_incident_classes_are_in_the_set(self):
        classes = {e['class'] for e in self.rep['entries']}
        for cls in ('wayfinding', 'status', 'send_file', 'build', 'read', 'page_analysis', 'data_floor', 'cancel', 'wizard', 'deck'):
            self.assertIn(cls, classes)

    def test_known_misses_are_marked_not_hidden(self):
        # a case whose current answer is the wrong lane carries both the mark and the expected route (none open today:
        # the own-order status case was fixed by stage 2 and moved to its right route)
        for c in [c for c in self.cases if c.get('known_miss')]:
            self.assertIn('expected', c)
            self.assertNotEqual(c['expected'], c['recorded'])

    def test_nothing_real_was_touched(self):
        fake = RC._FAKE['s3']
        self.assertTrue(getattr(sys.modules['boto3'], '_pm_replay_fake', False))
        self.assertIsNone(sys.modules.get('clickhouse_connect'))
        # every store write a case made landed in the in-memory fake, under a probe's own prefix or a pending-offer stash
        for k in fake.puts:
            self.assertTrue(k.startswith('system/') or k.startswith('probe') or 'canary' in k, k)
        self.assertEqual(os.environ.get('PM_MAIL_OFF'), '1')
        from prometheus import mail_guard
        self.assertFalse(mail_guard.sending_allowed())
        # the one would-be charge is the typed approval of a quote (fx027); the stub recorded it, no wallet moved
        self.assertEqual(self.rep['would_charge'], 1)

    def test_render_prints_no_question_text(self):
        text = RC.render(self.rep)
        for c in self.cases:
            if len(c['question']) >= 12:
                self.assertNotIn(c['question'][:24], text)
        self.assertIn('GATE OK', text)


class BaselineGate(unittest.TestCase):
    def _entries(self, verdicts):
        return [{'id': f'c{i}', 'class': 'read', 'live_class': 'read', 'class_agree': True, 'verdict': v,
                 'live': {'route': 'reasoned_metrics', 'outcome': 'landed' if v == 'same' else 'error'}}
                for i, v in enumerate(verdicts)]

    def test_new_regression_fails_the_gate(self):
        base = {'entries': self._entries(['same'] * 6), 'by_class': {'read': {'n': 6, 'agreement': 100.0}}}
        rep = RC.summarize(self._entries(['same'] * 5 + ['regressed']), base)
        self.assertFalse(rep['gate']['ok'])
        self.assertTrue(any('newly regressed' in r for r in rep['gate']['reasons']))

    def test_identical_run_passes(self):
        ents = self._entries(['same'] * 5 + ['regressed'])
        base = {'entries': ents, 'by_class': {'read': {'n': 6, 'agreement': 100.0}}}
        rep = RC.summarize(ents, base)
        self.assertTrue(rep['gate']['ok'], rep['gate'])

    def test_fail_always_blocks(self):
        rep = RC.summarize(self._entries(['same', 'fail']), None)
        self.assertFalse(rep['gate']['ok'])

    def test_class_agreement_drop_fails(self):
        ents = self._entries(['same'] * 10)
        for e in ents[:3]:
            e['class_agree'] = False
        base = {'entries': ents, 'by_class': {'read': {'n': 10, 'agreement': 100.0}}}
        rep = RC.summarize(ents, base)
        self.assertFalse(rep['gate']['ok'])
        self.assertTrue(any(r.startswith('read: agreement') for r in rep['gate']['reasons']))


class FixturesAreSanitized(unittest.TestCase):
    def test_no_client_markers(self):
        with open(RC.FIXTURES, encoding='utf-8') as fh:
            text = fh.read().lower()
        for bad in ('canary:', '"user":', '"email":', 'thread_id', '@', 'amazon', 'lionsgate', 'paramount'):
            self.assertNotIn(bad, text, bad)
        for c in RC.load_fixtures():
            self.assertEqual(c['seat'], 'fx')
            self.assertFalse(c.get('thread'))
            self.assertTrue(c['id'].startswith('fx'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
