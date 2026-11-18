#!/usr/bin/env python3
"""The outcome ledger and the firmness layer (Digital Twins IQ,
2026-11-17).

Held here:
  * the ledger: a prediction is recorded with a hash that dates it; a
    repeat of the same prediction is one row; an outcome scores the row
    (abs and relative error, coverage, TVD, Brier, hit); a count on a
    result already known is post hoc and never public; the first rows
    (the midterm calls, the NCIS: New York pilot) backfill idempotently;
    summaries are unit-aware; history_for reads the typical miss;
  * firmness: a counted result reads firm; a research-anchored count
    leans; a room on its habit prior alone with nothing found declines
    (early, with the line that says what would settle it); an agreeable
    room is capped; ranges clip to 0..100 and sum sensibly; the public
    view carries no signals; the forecast word follows the calibration
    scope and the ledger's own miss once it has three rows;
  * the room: a question lands with a firmness block and a ledger row;
    the chat and the CSV carry the range; a scorecard carries a range per
    money measure;
  * the record lives in one place for everyone: a row carries its twin's
    company; a seat reads its own company's calls and the house's public
    record (settled and open), another company never sees them, the
    website shows only the public rows, an admin can release a client's
    row and take it back, a post hoc row is never public, older rows are
    restamped with their company (Jenna 2026-11-17);
  * nothing anywhere offers a survey or a check with people: the twins
    stand on their own (Jenna 2026-11-17); what settles an early read is
    our own counted read on the audience.

Hermetic: REGRESSION_TEST_MODE, an in-memory ledger, no model.
    python3 scripts/test_digital_twin_ledger_firmness.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_confidence as CONF  # noqa: E402
from migration import digital_twin_ledger as LG  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_room_seats_mirror_count import _pvalley, fake_model  # noqa: E402


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        import io
        return {'Body': io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in self.objects if k.startswith(Prefix)], 'IsTruncated': False}


class Ledger(unittest.TestCase):
    def setUp(self):
        LG.reset_memory()

    def test_a_prediction_is_dated_and_scored_once_the_outcome_lands(self):
        r = LG.record_prediction(None, 'forecast', 'Second Helping', {'point': 1_000_000, 'low': 800_000, 'high': 1_200_000, 'unit': 'people'}, measure='week_one_audience',
                                 session_id='s1', question='Week one', question_kind='forecast', audience_class='title', method_version='forecast-1')
        self.assertTrue(r['hash'] and r['id'].startswith('lg_'))
        self.assertIsNone(r['outcome'])
        again = LG.record_prediction(None, 'forecast', 'Second Helping', {'point': 1_000_000, 'low': 800_000, 'high': 1_200_000, 'unit': 'people'}, measure='week_one_audience', session_id='s1')
        self.assertEqual(again['id'], r['id'], 'the same prediction is one row')
        self.assertEqual(len(LG.load_index(None)['records']), 1)
        out = LG.record_outcome(None, r['id'], value=1_100_000, source_class='published')
        self.assertEqual(out['score']['abs_error'], 100_000)
        self.assertAlmostEqual(out['score']['rel_error'], 0.0909, places=4)
        self.assertTrue(out['score']['covered'])
        self.assertEqual(out['score']['direction'], 'low')
        sm = LG.summary(None)
        self.assertEqual((sm['n'], sm['n_scored']), (1, 1))
        self.assertAlmostEqual(sm['mean_rel_error'], 0.0909, places=4)
        self.assertEqual(sm['by_unit']['people']['n'], 1)
        # a revision after the outcome is a new, non-public row; the scored one stands
        rev = LG.record_prediction(None, 'forecast', 'Second Helping', {'point': 1_050_000, 'unit': 'people'}, measure='week_one_audience', session_id='s1')
        self.assertNotEqual(rev['id'], r['id'])
        self.assertFalse(rev['public'])
        self.assertTrue(LG.load_record(None, r['id'])['outcome'])

    def test_distributions_calls_and_post_hoc_rows(self):
        r = LG.record_prediction(None, 'room_count', 'P-Valley viewers', {'distribution': [{'label': 'Yes', 'pct': 41.3}, {'label': 'No', 'pct': 58.7}], 'point': 58.7, 'unit': 'pct', 'probability': 0.413},
                                 measure='share:No', session_id='s2', question_id='q1', question='Would you pay?', question_kind='yes_no', audience_class='title')
        out = LG.record_outcome(None, r['id'], distribution=[{'label': 'yes', 'pct': 37.0}, {'label': 'no', 'pct': 63.0}], happened=False, source_class='panel')
        self.assertEqual(out['score']['tvd'], 4.3)
        self.assertTrue(out['score']['lead_right'])
        self.assertAlmostEqual(out['score']['brier'], 0.1706, places=3)
        self.assertTrue(out['score']['hit'])
        ph = LG.record_prediction(None, 'room_count', 'Texas', {'distribution': [{'label': 'A', 'pct': 55.0}, {'label': 'B', 'pct': 45.0}], 'unit': 'pct'}, measure='share:A', session_id='s3', question_id='q9', post_hoc=True)
        self.assertFalse(ph['public'])
        LG.record_outcome(None, ph['id'], distribution=[{'label': 'A', 'pct': 55.1}, {'label': 'B', 'pct': 44.9}])
        self.assertEqual(LG.summary(None)['n_scored'], 1, 'a post hoc row never counts')
        # a row with no company behind it is nobody's: not the house's, not public, read by staff alone (2026-11-17)
        self.assertEqual(len(LG.public_rows(None, site=True)), 0)
        self.assertEqual(len(LG.public_rows(None, site=False, staff=True)), 1)
        self.assertEqual(len(LG.public_rows(None, site=False, company='Acme')), 0)
        self.assertEqual(LG.tvd([{'label': 'x', 'pct': 50}], [{'label': 'y', 'pct': 50}]), None)

    def test_the_record_is_scoped_by_company_and_the_site_shows_the_house(self):
        # Jenna 2026-11-17: the record is not a twin's; it lives in one place. A seat reads its own company's calls and the
        # house's public record; another company never sees them; the website carries the house's calls and what an admin released
        acme = LG.record_prediction(None, 'room_count', 'Acme buyers', {'distribution': [{'label': 'Yes', 'pct': 60.0}, {'label': 'No', 'pct': 40.0}], 'point': 60.0, 'unit': 'pct'},
                                    measure='share:Yes', session_id='a1', question_id='q1', question='Would you renew?', company='Acme Studios')
        beta = LG.record_prediction(None, 'room_count', 'Beta fans', {'distribution': [{'label': 'Yes', 'pct': 30.0}, {'label': 'No', 'pct': 70.0}], 'point': 70.0, 'unit': 'pct'},
                                    measure='share:No', session_id='b1', question_id='q1', question='Would you come back?', company='Beta Media')
        house = LG.record_prediction(None, 'forecast', 'Our own title', {'point': 1_234_567, 'unit': 'people'}, measure='week_one_audience', session_id='h1', company='Crosswalk')
        self.assertFalse(acme['public']); self.assertFalse(acme['house'])
        self.assertTrue(house['house']); self.assertTrue(house['public'], 'a house row is public by default')
        # open calls: each company its own, the house's for everyone, staff everything
        self.assertEqual(sorted(r['subject'] for r in LG.open_rows(None, company='Acme Studios')), ['Acme buyers', 'Our own title'])
        self.assertEqual(sorted(r['subject'] for r in LG.open_rows(None, company='Beta Media')), ['Beta fans', 'Our own title'])
        self.assertEqual(len(LG.open_rows(None, staff=True)), 3)
        self.assertEqual([r['subject'] for r in LG.open_rows(None, company='Nobody Inc')], ['Our own title'])
        # settled: the same scope; the website only the public rows
        LG.record_outcome(None, acme['id'], distribution=[{'label': 'Yes', 'pct': 58.0}, {'label': 'No', 'pct': 42.0}], source_class='panel')
        LG.record_outcome(None, house['id'], value=1_300_000, source_class='published')
        self.assertEqual(sorted(r['subject'] for r in LG.public_rows(None, site=False, company='Acme Studios')), ['Acme buyers', 'Our own title'])
        self.assertEqual([r['subject'] for r in LG.public_rows(None, site=False, company='Beta Media')], ['Our own title'])
        self.assertEqual([r['subject'] for r in LG.public_rows(None, site=True)], ['Our own title'])
        self.assertEqual(LG.summary(None, scope=True, company='Acme Studios')['n_scored'], 2)
        self.assertEqual(LG.summary(None, scope=True, company='Beta Media')['n_scored'], 1)
        self.assertEqual(LG.summary(None, site=True)['n_scored'], 1)
        self.assertEqual(LG.summary(None)['n_scored'], 2, 'the firmness layer learns from every scored row')
        # an admin releases a client's row to the public record, and can take it back
        LG.release(None, acme['id'], True)
        self.assertEqual(sorted(r['subject'] for r in LG.public_rows(None, site=True)), ['Acme buyers', 'Our own title'])
        LG.release(None, acme['id'], False)
        self.assertEqual([r['subject'] for r in LG.public_rows(None, site=True)], ['Our own title'])
        # a post hoc row can never be released
        ph = LG.record_prediction(None, 'call', 'Known', {'call': 'A', 'distribution': [{'label': 'A', 'pct': 60.0}, {'label': 'B', 'pct': 40.0}], 'unit': 'pct'}, session_id='p1', post_hoc=True, company='Crosswalk')
        LG.record_outcome(None, ph['id'], winner='A', distribution=[{'label': 'A', 'pct': 61.0}, {'label': 'B', 'pct': 39.0}])
        self.assertFalse(LG.release(None, ph['id'], True)['public'])
        # the public view carries no company or key
        row = LG.public_rows(None, site=True)[0]
        self.assertNotIn('question_text', row)
        for k in ('subject', 'what', 'said', 'happened', 'called_on', 'settled_on', 'hash'):
            self.assertIn(k, row)

    def test_restamp_puts_the_company_on_older_rows(self):
        old = LG.record_prediction(None, 'room_count', 'Old row', {'distribution': [{'label': 'Yes', 'pct': 50.0}, {'label': 'No', 'pct': 50.0}], 'point': 50.0, 'unit': 'pct'},
                                   measure='share:Yes', session_id='o1', question_id='q1', public=True)
        rec = LG.load_record(None, old['id'])
        del rec['house']; rec['public'] = True
        LG._save(None, rec)
        self.assertEqual(LG.restamp(None), 1)
        rec = LG.load_record(None, old['id'])
        self.assertFalse(rec['house']); self.assertFalse(rec['public'], 'a row with no company behind it leaves the public record')
        self.assertEqual(LG.restamp(None), 0, 'idempotent')

    def test_backfill_is_idempotent_and_public(self):
        rows = LG.backfill_known(None)
        self.assertEqual(len(rows), 6)
        rows2 = LG.backfill_known(None)
        self.assertEqual(len(LG.load_index(None)['records']), 6, 'a second backfill adds nothing')
        sm = LG.summary(None)
        self.assertEqual(sm['n_scored'], 6)
        self.assertEqual(sm['hit_rate'], 1.0)
        self.assertLess(sm['mean_rel_error'], 0.05)
        pub = LG.public_rows(None)
        self.assertEqual(len(pub), 6)
        self.assertTrue(all(r['hash'] and r['called_on'] < r['settled_on'] for r in pub))
        hist = LG.history_for(None, 'forecast', measure='week_one_audience', audience_class='title')
        self.assertEqual(hist['n_scored'], 3, 'widened to the kind (the three NCIS rows) when the narrow scope has under three')
        self.assertIsNotNone(hist['mean_rel_error'])

    def test_the_fake_s3_round_trips_and_rebuilds(self):
        s3 = FakeS3()
        r = LG.record_prediction(s3, 'call', 'Ohio', {'point': 4_200_000, 'unit': 'people', 'call': 'Acton'}, measure='turnout', session_id='x')
        self.assertIn(LG.INDEX_KEY, s3.objects)
        LG.record_outcome(s3, r['id'], value=4_600_000, winner='Acton')
        del s3.objects[LG.INDEX_KEY]
        idx = LG.rebuild_index(s3)
        self.assertEqual(len(idx['records']), 1)
        self.assertTrue(idx['records'][0]['hit'])


class Firmness(unittest.TestCase):
    def _rec(self, status, answers_opt='a', conf=5, facts=3, study_ok=True):
        opts = [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 62.0, 'people': 620_000}, {'id': 'b', 'label': 'Maybe', 'lean': 'maybe', 'pct': 23.0, 'people': 230_000},
                {'id': 'c', 'label': 'No', 'lean': 'no', 'pct': 15.0, 'people': 150_000}]
        answers = [{'seat': i, 'option': answers_opt, 'confidence': conf} for i in range(1, 13)]
        trail = {'anchor_status': status, 'study': ({'ok': True, 'figures': [1, 2], 'reach': [1]} if study_ok else {'ok': False, 'error': 'x'}),
                 'anchor': {'basis': {'rows': [{'low_pct': 55, 'high_pct': 70}, {'low_pct': 15, 'high_pct': 30}, {'low_pct': 8, 'high_pct': 22}]}}}
        return {'kind': 'yes_no', 'options': opts, 'answers': answers, 'trail': trail, 'facts_used': ['f'] * facts}

    def _roster(self, n=12, weighted=False):
        return [{'seat': i, 'people': (100_000 + 15_000 * i if weighted else 83_333), 'archetype': {'id': f"k{i}"} if weighted else None} for i in range(1, n + 1)]

    def test_a_counted_result_is_firm_and_a_prior_alone_declines(self):
        fb = CONF.firmness_for_room(self._rec('result'), self._roster())
        self.assertEqual(fb['word'], 'firm')
        self.assertLessEqual(fb['predicted_error_pp'], 3.0)
        self.assertFalse(fb['decline'])
        self.assertTrue(fb['line'].startswith('Firm:'))
        fb2 = CONF.firmness_for_room(self._rec('derived'), self._roster(), prior={'a': 0.5, 'b': 0.3, 'c': 0.2})
        self.assertIn(fb2['word'], ('firm', 'leaning'))
        fb3 = CONF.firmness_for_room(self._rec('prior', facts=0, study_ok=False), self._roster(), prior={'a': 0.5, 'b': 0.3, 'c': 0.2})
        self.assertEqual(fb3['word'], 'early')
        self.assertTrue(fb3['decline'])
        self.assertTrue(fb3['line'].startswith('Not enough to call it yet'))
        self.assertIn('What would settle it', fb3['line'])
        self.assertIn('a counted read on this audience', fb3['line'])
        for bad in ('real people', 'survey', 'study', 'People'):
            self.assertNotIn(bad, fb3['line'])
        for r in fb3['ranges']:
            self.assertTrue(0.0 <= r['low_pct'] <= r['pct'] <= r['high_pct'] <= 100.0)
        self.assertEqual(fb3['lead']['label'], 'Yes')

    def test_history_moves_the_word_and_an_agreeable_room_is_capped(self):
        rec = self._rec('derived')
        good = CONF.firmness_for_room(rec, self._roster(), prior={'a': 0.5, 'b': 0.3, 'c': 0.2}, history={'n_scored': 10, 'typical_error': 2.0})
        bad = CONF.firmness_for_room(rec, self._roster(), prior={'a': 0.5, 'b': 0.3, 'c': 0.2}, history={'n_scored': 10, 'typical_error': 18.0})
        self.assertLess(good['predicted_error_pp'], bad['predicted_error_pp'])
        ag = CONF.agreement_signal(rec['answers'], self._roster(), rec['options'], prior={'a': 0.34, 'b': 0.33, 'c': 0.33})
        self.assertTrue(ag['agreeable'], 'every seat on one side against an even prior is the agreeable room')
        self.assertLessEqual(ag['signal'], 0.55)
        split = [{'seat': i, 'option': ('a', 'b', 'c')[i % 3], 'confidence': 2} for i in range(1, 13)]
        ag2 = CONF.agreement_signal(split, self._roster(), rec['options'], prior={'a': 0.5, 'b': 0.3, 'c': 0.2})
        self.assertLess(ag2['signal'], ag['signal'] + 0.3)
        self.assertFalse(ag2['agreeable'])
        # the public view: the word, the line, the ranges, nothing about the weighing
        pub = CONF.public_firmness(good)
        self.assertEqual(set(pub) - {'what_settles'}, {'word', 'line', 'decline', 'ranges', 'lead'})
        self.assertNotIn('confidence', json.dumps(pub).lower())

    def test_weighted_seats_count_by_their_people(self):
        rec = self._rec('derived')
        roster = self._roster(weighted=True)
        rec['answers'] = [{'seat': i, 'option': ('a' if i >= 9 else 'c'), 'confidence': 4} for i in range(1, 13)]   # the four biggest seats say yes
        ag = CONF.agreement_signal(rec['answers'], roster, rec['options'])
        self.assertGreater(ag['lead_share'], 33.3, 'the four biggest seats carry more than a third of the people')

    def test_the_forecast_word_follows_the_scope_and_the_ledger(self):
        fb = CONF.firmness_for_count(1_000_000, low=750_000, high=1_250_000, calibration_scope='none')
        self.assertEqual((fb['word'], fb['low'], fb['high']), ('early', 750_000, 1_250_000))
        fb2 = CONF.firmness_for_count(1_000_000, low=900_000, high=1_100_000, calibration_scope='show')
        self.assertEqual(fb2['word'], 'firm')
        fb3 = CONF.firmness_for_count(1_000_000, low=900_000, high=1_100_000, calibration_scope='platform', history={'n_scored': 6, 'mean_rel_error': 0.03})
        self.assertEqual(fb3['word'], 'firm', 'a ledger with a small typical miss firms a platform-anchored count')
        self.assertAlmostEqual(fb3['low'], 950_000, delta=1)     # the ledger's 3% miss with headroom sits at the 5% floor
        fb4 = CONF.firmness_for_count(1_000_000, calibration_scope='platform', history={'n_scored': 6, 'mean_rel_error': 0.3})
        self.assertEqual(fb4['word'], 'leaning')
        self.assertAlmostEqual(fb4['low'], 550_000, delta=1)
        for b in (fb, fb2, fb3, fb4):
            self.assertNotIn('confidence', b['line'].lower())


class InTheRoom(unittest.TestCase):
    def setUp(self):
        ROOM.clear_room_caches()
        LG.reset_memory()

    def test_a_question_carries_firmness_and_a_ledger_row(self):
        twin = _pvalley()
        sess = P.open_session([twin], title='P-Valley', session_id='firm-room-1')
        rec = ROOM.ask_room(sess, {'pv': twin}, 'are you currently watching swat exiles?', s3=None, twin_key='pv', run_async=False, model_fn=fake_model)
        fb = rec['firmness']
        self.assertEqual(fb['word'], 'firm', 'a count set by a counted result is firm')
        self.assertEqual(len(fb['ranges']), len(rec['options']))
        self.assertTrue(any(l.startswith('Firm:') for l in ROOM.chat_lines(sess, rec)))
        self.assertIn('firmness', rec['trail'])
        self.assertIn('signals', rec['trail']['firmness'])
        rows = LG.load_index(None)['records']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'room_count')
        self.assertTrue(rows[0]['post_hoc'], 'the result was known: post hoc')
        self.assertEqual(rows[0]['firmness'], 'firm')
        csv_text = P.session_csv(sess)
        self.assertIn('How firm', csv_text)
        self.assertIn('firm', csv_text.lower())
        self.assertIn('Kind', csv_text)

    def test_a_prior_only_question_declines_and_still_publishes(self):
        twin = _pvalley()
        sess = P.open_session([twin], title='P2', session_id='firm-room-2')
        rec = ROOM.ask_room(sess, {'pv': twin}, 'would you buy a Pynk hoodie for $45?', s3=None, twin_key='pv', run_async=False)
        self.assertTrue(rec['options'] and rec['read'])
        fb = rec['firmness']
        self.assertEqual(fb['word'], 'early')
        self.assertTrue(fb['decline'])
        self.assertTrue(fb['line'].startswith('Not enough to call it yet'))
        rows = LG.load_index(None)['records']
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['post_hoc'])
        self.assertFalse(rows[0]['public'], "a client room's call is on the ledger, never on the public record by itself")
        self.assertTrue(LG.visible(rows[0], staff=True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
