#!/usr/bin/env python3
"""Digital Twins IQ: a twin stands for the actual people in its subject's
base (Jenna 2026-10-30, the Ohio Gubernatorial Voters twin built on the
Florida profile, sized at 1.4M, seated in Salt Lake City and Orlando).

- a profile stands for a subject only when its name carries every
  identity word of the subject
- an electorate is sized by its registered voters, from the research
- the room seats from the base's own markets, by party and setting,
  and each seat's bio carries its lean and the issues it weighs

Hermetic: the research is a stub, no network."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_synthesis as DS  # noqa: E402
from migration import digital_twin_panel as PANEL  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402

OHIO_RESEARCH = {
    'base': {'kind': 'electorate', 'definition': 'People registered to vote in Ohio.', 'count_us': 8037114, 'ceiling_us': 9021766,
             'turnout_us': 4201368, 'source_type': 'Secretary of State'},
    'geography': {'Columbus OH': 21.4, 'Cleveland-Akron OH': 28.9, 'Cincinnati OH': 17.6, 'Dayton OH': 8.7, 'Toledo OH': 6.9,
                  'Youngstown OH': 4.8, 'Zanesville OH': 2.1, 'Lima OH': 1.6, 'Wheeling-Steubenville': 1.9},
    'composition': {'party': {'Republican': 41.0, 'Democrat': 33.0, 'Independent or undecided': 26.0},
                    'urbanicity': {'Urban': 31.0, 'Suburban': 46.0, 'Rural': 23.0}},
    'issues': ['the cost of living', 'jobs and the Intel plant', 'abortion', 'public schools', 'crime', 'property taxes'],
    'values': 'They want a governor who keeps the lights on and the bills down. They are wary of anyone who talks down to small towns.',
    'format': 'none', 'franchise_stage': 'none', 'ip_related': False, 'us_fans_estimate': 8037114, 'avid_share_pct': 22.0,
    'platform_home': 'Facebook', 'platform_reach': {'Facebook': 68, 'YouTube': 74, 'Netflix': 62},
    'demographics': {'gender': {'Women': 52, 'Men': 48}, 'age': {'18-24': 8, '25-34': 14, '35-44': 16, '45-54': 17, '55-64': 20, '65+': 25},
                     'income': {'Less than $25,000': 14, '$25,000-$49,999': 22, '$50,000-$74,999': 20, '$75,000-$99,999': 16, '$100,000-$149,999': 17, '$150,000+': 11},
                     'ethnicity': {'White': 80, 'Hispanic': 4, 'Black': 12, 'Asian': 2, 'Other': 2}},
    'notes': 'Ohio voters.',
}


class Matcher(unittest.TestCase):
    def test_every_identity_word_must_match(self):
        self.assertFalse(DS.profile_fits_subject('2026_Florida_Gubernatorial_Voters_08_25_2026_02_16.csv', 'Ohio Gubernatorial Voters'))
        self.assertTrue(DS.profile_fits_subject('2026_Florida_Gubernatorial_Voters_08_25_2026_02_16.csv', 'Florida Gubernatorial Voters'))
        self.assertTrue(DS.profile_fits_subject('Gilmore Girls Viewers', 'Gilmore Girls fans'))
        self.assertFalse(DS.profile_fits_subject('Apple TV EST Buyers', 'Apple buyers'))
        self.assertTrue(DS.profile_fits_subject('Apple_06_01_2026_10_00.csv', 'Apple buyers'))
        self.assertEqual(DS.subject_identity_tokens('2026 Ohio Gubernatorial Voters'), ['ohio', 'gubernatorial'])
        self.assertFalse(DS.profile_fits_subject('Gilmore Girls Viewers ON HULU', 'Gilmore Girls'))
        self.assertTrue(DS.profile_fits_subject('Jennifer_Beals_06_17_2026_17_09.csv', 'Jennifer Beals'))
        self.assertTrue(DS.profile_fits_subject('Nike Shoppers', 'Nike'))

    def test_catalog_pick_skips_a_near_miss(self):
        class S3:
            def get_object(self, Bucket, Key):
                import io, json
                doc = {'jobs': [{'display_name': '2026 Florida Gubernatorial Voters', 's3_key': '2026_Florida_Gubernatorial_Voters_08_25_2026_02_16.csv'},
                                {'display_name': 'Ohio State Buckeyes', 's3_key': 'Ohio_State_Buckeyes_07_01_2026_09_00.csv'}]}
                return {'Body': io.BytesIO(json.dumps(doc).encode('utf-8'))}
        self.assertEqual(DS.find_profile_key(S3(), 'Ohio Gubernatorial Voters', {}), '')
        self.assertEqual(DS.find_profile_key(S3(), 'Florida Gubernatorial Voters', {}), '2026_Florida_Gubernatorial_Voters_08_25_2026_02_16.csv')


class Research(unittest.TestCase):
    def test_base_geography_and_composition(self):
        base = DS.research_base(OHIO_RESEARCH)
        self.assertEqual(base['kind'], 'electorate')
        self.assertEqual(base['count_us'], 8037114)
        geo = DS.research_geography(OHIO_RESEARCH)
        self.assertEqual(geo[0]['label'], 'Cleveland-Akron OH')
        comp = DS.research_composition(OHIO_RESEARCH, 'seed')
        self.assertIn('party', comp)
        self.assertAlmostEqual(sum(r['pct'] for r in comp['party']), 100.0, places=1)
        self.assertEqual(DS.research_base({'base': {'kind': 'fans'}}), {})


class Build(unittest.TestCase):
    def _twin(self):
        import unittest.mock as mock
        with mock.patch.object(DS, 'corpus_anchors', return_value={'catalog': {}, 'profile_key': '', 'profile': None, 'journeys': [], 'bpiq': [], 'attribution': [], 'trends': [], 'ledger': [], 'gen_pop': {}, 'related': []}), \
             mock.patch.object(DS, 'research_anchors', return_value=dict(OHIO_RESEARCH)):
            return DS.build_twin({'subject': 'Ohio Gubernatorial Voters', 'subject_kind': 'audience', 'purpose': 'Governor race'}, None, s3=None)

    def test_electorate_is_sized_by_its_registered_voters(self):
        tw = self._twin()
        size = int(tw['meta']['size_us'])
        self.assertTrue(7_900_000 <= size <= 8_200_000, size)
        self.assertNotEqual(size % 10, 0)
        self.assertEqual(tw['diagnostics']['size_source'], 'base')
        self.assertIn(tw['meta']['source'], ('research', 'the record'))   # the payload scrub rewrites the word
        ident = tw['identity']
        self.assertEqual(ident['base']['kind'], 'electorate')
        self.assertTrue(ident['place_scoped'])
        self.assertIn('party', ident['demographics'])
        self.assertIn('urbanicity', ident['demographics'])
        self.assertEqual(ident['geography'][0]['label'], 'Cleveland-Akron OH')
        self.assertEqual(ident['issues'][0], 'the cost of living')
        self.assertIn('keeps the lights on', ident['values'])

    def test_room_seats_ohio_by_party_with_voter_bios(self):
        tw = self._twin()
        tw['meta']['key'] = 'ohio'
        g = PANEL.build_group([tw], in_room=12, seed='t')
        roster = g['roster']
        self.assertEqual(len(roster), 12)
        ohio = {'Columbus', 'Cleveland-Akron', 'Cincinnati', 'Dayton', 'Toledo', 'Youngstown', 'Zanesville', 'Lima', 'Wheeling-Steubenville'}
        for r in roster:
            self.assertIn(r['city'], ohio, r['city'])
        parties = [r['party'] for r in roster]
        self.assertGreaterEqual(parties.count('Republican'), 4)
        self.assertGreaterEqual(parties.count('Democrat'), 3)
        self.assertGreaterEqual(parties.count('Independent or undecided'), 2)
        self.assertTrue(all(r['urbanicity'] in ('Urban', 'Suburban', 'Rural') for r in roster))
        sess = {'id': 'room1', 'group': g}
        ROOM.ensure_personas(sess, {'ohio': tw}, session_seed='room1')
        bios = [r['persona'] for r in roster]
        self.assertTrue(all('Politically' in b for b in bios), bios[0])
        self.assertTrue(any('Republican' in b for b in bios))
        self.assertTrue(any('Democrat' in b for b in bios))
        self.assertTrue(all(('vote' in b.lower() or 'registered' in b.lower() or 'rolls' in b.lower()) for b in bios), bios[0])
        self.assertFalse(any('mug' in b or 'box set' in b or 'binges' in b or 'original run' in b for b in bios), bios[0])
        self.assertTrue(any('cost of living' in b for b in bios))
        card = ROOM.persona_card(tw, roster[0], 'room1')
        text = ROOM._card_text(card)
        self.assertIn('keeps the lights on', text)

    def test_a_title_twin_is_untouched(self):
        import unittest.mock as mock
        research = {'format': 'series', 'franchise_stage': 'legacy', 'ip_related': True, 'us_fans_estimate': 9_400_000, 'avid_share_pct': 18, 'platform_home': 'Netflix',
                    'platform_reach': {'Netflix': 70}, 'demographics': {'gender': {'Women': 70, 'Men': 30}}, 'notes': 'x'}
        with mock.patch.object(DS, 'corpus_anchors', return_value={'catalog': {}, 'profile_key': '', 'profile': None, 'journeys': [], 'bpiq': [], 'attribution': [], 'trends': [], 'ledger': [], 'gen_pop': {}, 'related': []}), \
             mock.patch.object(DS, 'research_anchors', return_value=research):
            tw = DS.build_twin({'subject': 'Gilmore Girls', 'subject_kind': 'ip'}, None, s3=None)
        self.assertNotIn('base', tw['identity'])
        self.assertFalse(tw['identity']['place_scoped'])
        self.assertNotIn('party', tw['identity']['demographics'])
        g = PANEL.build_group([dict(tw, meta=dict(tw['meta'], key='gg'))], in_room=6, seed='t')
        sess = {'id': 'r', 'group': g}
        ROOM.ensure_personas(sess, {'gg': tw}, session_seed='r')
        self.assertFalse(any('Politically' in r['persona'] for r in g['roster']))


if __name__ == '__main__':
    unittest.main(verbosity=1)
