#!/usr/bin/env python3
"""The 2026-11-02 nightly golden failures, fixed at the lane. Hermetic.

g090 g094 g097  a sizing ask keeps the first letter of its subject and
                never sizes the word "projected"
g091 g017 g022  "do we have / tell me if we have X" is a library lookup,
                never a where-watched read or a size quote
g020            "is there a Gunna profile in the library" names Gunna
g040            a decline reads "Understood, nothing is queued"
g074            "what platforms do X fans stream on" is not where-watched
g053 g095       a journey "just like the one you did for Babylon 5" takes
                the precedent's direction, and the precedent's own stored
                file lends the shape when the corpus holds none
g054            a noun-led product order ("journey for X on Y, start
                from ...") is never a custom-ask quote
g055 g056       a one-shot valuation fills the talent from "Gunna x Nike"
                and never asks "Who is the property or talent?"
g065            "here" is never a subject; a deixis deck order on an
                empty thread means the screen (g083)
g044            a category audience ("yoga clothing buyers") is a
                persona universe: no entity to verify
g077            a question about a figure already shown is talk, never
                a quote
probes          the golden set leaves no state behind: wizard drafts,
                stashed briefs and memory are skipped on a probe ask
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import catalog_lane as CL  # noqa: E402
from prometheus import intake_reader as IR  # noqa: E402
from prometheus import priced_read as P  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import where_watched as WW  # noqa: E402


class SizingSubjects(unittest.TestCase):
    def test_first_letter_survives(self):
        subs = SZ.subjects('can i get the netflix profile size and the spotify one')
        low = [s.lower() for s in subs]
        self.assertIn('netflix', low)
        self.assertNotIn('etflix', low)

    def test_the_x_one_how_many(self):
        subs = SZ.subjects('the noah wyle one, how many people')
        self.assertEqual([s.lower() for s in subs], ['noah wyle'])

    def test_front_clause_subject(self):
        subs = SZ.subjects('Gilmore Girls viewers, how many in the sample and projected')
        self.assertEqual(len(subs), 1)
        self.assertIn('gilmore girls', subs[0].lower())
        self.assertNotIn('projected', subs[0].lower())


class CatalogExists(unittest.TestCase):
    def test_tell_me_if_we_have_is_a_lookup(self):
        p = CL.parse('pls tell me if we have a nike profile and how big it is')
        self.assertIsNotNone(p)
        self.assertEqual(p[0], 'exists')
        self.assertEqual(p[1].lower(), 'nike')

    def test_roster_ask_does_not_eat_a_name(self):
        self.assertIsNone(CL.roster_ask('is there a Gunna profile in the library'))
        p = CL.parse('is there a Gunna profile in the library')
        self.assertIsNotNone(p)
        self.assertEqual(p[1], 'Gunna')

    def test_lane_order_matches_markers(self):
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        self.assertIn('catalog_exists', S.LANE_ORDER)
        self.assertLess(S.LANE_ORDER.index('catalog_exists'), S.LANE_ORDER.index('where_watched'))


class WhereWatched(unittest.TestCase):
    def test_platforms_fans_stream_on_is_not_carriage(self):
        self.assertIsNone(WW.parse('what platforms do Glen Powell fans stream on', [], page=''))

    def test_bare_preposition_is_not_a_title(self):
        self.assertFalse(WW._clean_title('On'))


class ProductOrders(unittest.TestCase):
    def test_noun_led_journey_order(self):
        self.assertTrue(P.is_product_order(
            'journey for Young Sheldon on Netflix, start from people who watched a clip on TikTok, end at the first episode watched'))
        self.assertTrue(P.is_product_order('deck for Reba McEntire'))

    def test_questions_about_a_product_are_not_orders(self):
        self.assertFalse(P.is_product_order('how big is the journey for Landman'))
        self.assertFalse(P.is_product_order('the journey for Landman shows what?'))
        self.assertFalse(P.is_product_order('journey for Landman?'))

    def test_valuation_orders(self):
        self.assertTrue(P.is_product_order('can you value Gunna x Nike? campaign ran June through August 2026'))
        self.assertTrue(P.is_product_order('Value the Stanley x Target partnership. Campaign window is 2025-01-15 to 2025-03-31.'))

    def test_question_about_a_shown_figure_is_talk(self):
        t = 'Is that 55% of total viewers or of new and reactivated watchers?'
        self.assertTrue(P.is_talk(t))
        self.assertIsNone(P.classify(t, [], {'primary': {'name': 'X', 's3_key': 'x.csv'}}))

    def test_over_index_on_the_open_file_is_still_the_work_line(self):
        hit = P.classify('which streaming services over-index for this audience', [], {'primary': {'name': 'Costco', 's3_key': 'c.csv'}})
        self.assertIsNotNone(hit)
        self.assertEqual(hit['via'], 'generate')


class ValuationOneShot(unittest.TestCase):
    def test_gunna_x_nike_fills_talent_and_dates(self):
        d = W.apply_ahead(W.empty_draft(), 'can you value Gunna x Nike? campaign ran June through August 2026')
        self.assertEqual(d['talent'], 'Gunna')
        self.assertEqual(d['campaign'], 'Gunna x Nike')
        self.assertEqual(d['brand_partner'], 'Nike')
        self.assertEqual((d['campaign_start'], d['campaign_end']), ('2026-06-01', '2026-08-31'))
        self.assertEqual(W.next_step(d), W.STEP_ATTR)

    def test_stanley_x_target_with_iso_window(self):
        d = W.apply_ahead(W.empty_draft(), 'Value the Stanley x Target partnership. Campaign window is 2025-01-15 to 2025-03-31.')
        self.assertEqual(d['talent'], 'Stanley')
        self.assertEqual(d['brand_partner'], 'Target')
        self.assertEqual(d['campaign_start'], '2025-01-15')

    def test_separate_talent_still_wins(self):
        d = W.apply_ahead(W.empty_draft(), 'Jennifer Beals, Ganni x Lululemon')
        self.assertEqual(d['talent'], 'Jennifer Beals')
        self.assertEqual(d['campaign'], 'Ganni x Lululemon')
        self.assertEqual(d['brand_partner'], 'Lululemon')

    def test_frame_stripper(self):
        self.assertEqual(W._strip_ask_frame('can you value Gunna x Nike?  ran'), 'Gunna x Nike')
        self.assertEqual(W._strip_ask_frame('what is the Glen Powell x RAM Trucks deal worth?'), 'Glen Powell x RAM Trucks')
        self.assertEqual(W._strip_ask_frame('Jennifer Beals'), 'Jennifer Beals')

    def test_window_prompt_names_the_pair(self):
        from datetime import date
        d = W.apply_ahead(W.empty_draft(), 'can you value Gunna x Nike? campaign ran June through August 2026', today=date(2026, 10, 28))
        self.assertTrue(W.attr_reply(d, today=date(2026, 11, 2)).startswith('Gunna x Nike: I read the campaign as June 1 to August 31, 2026'))
        d2 = W.apply_ahead(W.empty_draft(), 'Jennifer Beals, Ganni x Lululemon, October 7 - 28', today=date(2026, 10, 28))
        self.assertTrue(W.attr_reply(d2, today=date(2026, 10, 28)).startswith('Jennifer Beals, Ganni x Lululemon: '))

    def test_probe_store_stays_in_process(self):
        self.assertIsInstance(W.store_for({'persist': False}), W.MemoryStore)


class JourneyPrecedent(unittest.TestCase):
    SHAPE = {'start_behavior': 'saw a social post about {subject}',
             'conversion_event': 'watched an episode on Prime Video within 30 days of the first post',
             'journey_kind': 'watch', 'platform': 'Prime Video'}

    def test_template_settles_the_direction(self):
        p = IR.apply_journey_template({'subject': 'No Reservations', 'platform': '', 'journey_direction': 'unclear'}, self.SHAPE, 'Babylon 5 on Prime Video')
        self.assertEqual(p['journey_direction'], 'to_watch')
        self.assertFalse(IR.direction_unclear(p))
        self.assertEqual(p['platform'], 'Prime Video')

    def test_template_repoints_the_platform(self):
        p = IR.apply_journey_template({'subject': 'No Reservations', 'platform': 'Amazon Prime', 'journey_direction': 'unclear'}, self.SHAPE, 'Babylon 5 on Prime Video')
        self.assertIn('on Amazon Prime', p['conversion_event'])
        self.assertNotIn('Prime Video', p['conversion_event'])

    def test_reference_silences_the_direction_question(self):
        self.assertIsNone(IR.journey_direction_from_text(
            'Build a digital journey for Anthony Bourdain No Reservations on Amazon Prime just like the one you did for Babylon 5'))

    def test_file_shape_from_a_stored_journey(self):
        from prometheus import precedent as PR
        import gzip, io, json

        class _S3:
            def get_object(self, Bucket, Key):
                doc = {'meta': {'project_name': 'Babylon 5 on Prime Video', 'journey_kind': 'watch'},
                       'j': {'spine': [{'label': 'US gen pop'}, {'label': 'Saw a social post about Babylon 5'},
                                       {'label': 'Watched an episode on Prime Video within 30 days of the first post'}]}}
                body = gzip.compress(json.dumps(doc).encode('utf-8'))
                return {'Body': io.BytesIO(body)}
        shape = PR.journey_file_shape({'key': 'journey-iq/x/Babylon_5.json.gz', 'name': 'Babylon 5 on Prime Video'}, s3=_S3())
        self.assertIsNotNone(shape)
        self.assertEqual(shape['journey_kind'], 'watch')
        self.assertIn('{subject}', shape['start_behavior'])
        self.assertEqual(shape['platform'], 'Prime Video')


class Deixis(unittest.TestCase):
    def test_here_is_never_a_subject(self):
        for w in ('here', 'there', 'this data', 'the screen'):
            self.assertIn(w, SR._DEIXIS)

    def test_reader_here_binds_nothing(self):
        reading = {'intent': 'analyze_page', 'confidence': 0.93, 'subjects': [{'as_written': 'here', 'catalog': '', 'kind': 'unknown'}]}
        s = SR.bind('what is the median age here', None, '', reading)
        self.assertNotEqual(str(s.label() or '').lower(), 'here')

    def test_deck_order_on_this_means_the_screen(self):
        self.assertTrue(SR.deixis_points_at_screen('make this a deck', []))
        self.assertTrue(SR.deixis_points_at_screen('turn this into a one-pager', []))
        self.assertFalse(SR.deixis_points_at_screen('make this a deck', [{'role': 'agent', 'text': 'a' * 100}]))
        self.assertFalse(SR.deixis_points_at_screen('make a deck on Costco', []))


class DeclineCopy(unittest.TestCase):
    def test_decline_reads_nothing_is_queued(self):
        from prometheus import pending_offers as PO
        self.assertIn('nothing is queued', PO.DECLINE_REPLY.lower())
        self.assertTrue(PO._DECLINED_RX.search(PO.DECLINE_REPLY))


class ProbeState(unittest.TestCase):
    def test_is_probe_is_false_outside_a_request(self):
        from prometheus import faults as F
        self.assertFalse(F.is_probe())

    def test_analyze_body_carries_persist_false(self):
        out = S._analyze_body({'text': 'x', 'persist': False}, 'x', [], {'surface': 'analyze'})
        self.assertIs(out.get('persist'), False)
        out2 = S._analyze_body({'text': 'x'}, 'x', [], {'surface': 'analyze'})
        self.assertNotIn('persist', out2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
