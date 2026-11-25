"""A cohort of the base's own subject is sized by the base, never by
research (Armine at Lionsgate, 2026-11-23; Jenna 2026-11-24: "run the
Nov 23 10:47 skateboarding-series question again ... find the cause of
the original contradiction").

The live shape, sanitized: a skateboarder's profile open on the page;
the thread had read the name-searcher cohort (1,742,869 people) and the
skateboard shows they watch; "wat about series about skateboarding"
ran on that cohort. The named subject, "people that Searched <skater> in
the US", was read as another subject's label (borrowed base), so the
read was sized by web research: the low research anchor, 102,000 US
people, became the audience cap, every count metric was cut to it
(1,037,241 -> 60,696; 543,775 -> 60,695; 418,663 -> 60,698, each paired
with the 59.5% rate on the shared word "series"), and the table under
them still carried 543,775 and 80,173 off the 1,742,869 the thread had
stated. The reply read "60,696 ... watched a skateboarding series" over
rows of 343,345 and 80,173.

The rules under test:

* knowledge.cohort_of_base / base_is_borrowed: a behavioral cohort of
  the base's own subject (its name, spelled as the reader spelled it,
  plus behavior and audience words) is the base's, never borrowed; the
  ceiling on its counts is the base profile's whole audience, with the
  cohort wording in the prompt block, never a researched size.
* consistency._pair_shares: a count's own share is the pct metric right
  after it, else the one count sharing a word beyond the measure and
  audience words; "The Nine Club viewers" never takes "Series watch
  rate".
* consistency.size_gate: a cap holds the table too; a row above the cap
  becomes the cap times its penetration.

Pure tests; no network, no model.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, 'pm_corpus'))

from prometheus import consistency as C  # noqa: E402
from prometheus import knowledge as K  # noqa: E402

ASK = "wat about series about skateboarding"
METRICS = [
    {'name': 'skate_series_watchers', 'label': 'Watched a skateboarding series', 'unit': 'viewers', 'value': 1037241,
     'definition': 'name searchers with at least one play of an episodic skateboarding series in the window'},
    {'name': 'series_watch_rate', 'label': 'Series watch rate', 'unit': 'pct', 'value': 59.5,
     'definition': 'share of the 1,742,869 name searchers who played at least one skateboarding series'},
    {'name': 'top_series_viewers', 'label': 'The Nine Club viewers', 'unit': 'viewers', 'value': 543775},
    {'name': 'avid_series_viewers', 'label': 'Avid series viewers (4 or more episodes)', 'unit': 'viewers', 'value': 418663},
    {'name': 'series_per_watcher', 'label': 'Distinct series per watcher', 'unit': 'series', 'value': 3},
]
ROWS = [{'label': 'The Nine Club', 'share_pct': 19.6, 'penetration_pct': 31.2, 'pool': 543775},
        {'label': 'Street League Skateboarding broadcasts', 'share_pct': 11.6, 'penetration_pct': 19.7, 'pool': 343345},
        {'label': 'Betty', 'share_pct': 2.9, 'penetration_pct': 4.6, 'pool': 80173}]


def _data():
    return {'subject': 'people that Searched Casey Vale in the US', 'metrics': copy.deepcopy(METRICS),
            'headline': '1,037,241 of the 1,742,869 Casey Vale name searchers watched a skateboarding series, and The Nine Club leads at 543,775.',
            'breakdown': {'dimension': 'Skateboarding series', 'share_basis': "share of the cohort's skateboarding series viewing signals",
                          'rows': copy.deepcopy(ROWS)}}


class TheCohort(unittest.TestCase):
    def test_a_cohort_of_the_base_is_the_bases_own(self):
        for named in ('people that Searched Casey Vale in the US', 'Casey Vale name searchers', 'people who watched Casey Vale on YouTube',
                      'the avid tier of Casey Vale fans', 'Casey Vale viewers who also searched the name', 'people that searched Casey Vale'):
            self.assertTrue(K.cohort_of_base(named, 'Casey Vale'), named)
            self.assertTrue(K.cohort_of_base(named, 'Casey Vale - Avid Fan'), named)
        # one slip of spelling on a word of five letters or more is the same name; a short word must match exactly
        self.assertTrue(K.cohort_of_base('people that Searched Cassidy Rodriquez in the US', 'Cassidy Rodriguez'))
        self.assertFalse(K.cohort_of_base('Mike fans', 'Nike'))

    def test_another_subject_is_not(self):
        for named in ('Nike fans', 'people who watched Reacher', 'VICE Sports', 'the US people', 'Street League viewers', 'Nike'):
            self.assertFalse(K.cohort_of_base(named, 'Casey Vale'), named)
        self.assertFalse(K.cohort_of_base('people who watched Reacher', 'Prime Video'))
        self.assertFalse(K.cohort_of_base('Casey Vale', 'Casey Vale'))       # the subject itself is not a cohort of it

    def test_the_persons_profile_stays_the_base_for_its_own_cohort(self):
        from prometheus import page_default as pd
        # the Statham rule holds: a title-viewing ask on a person's page drops the profile as the anchor
        self.assertTrue(pd.person_page_for_title_ask('wat about series about skateboarding', 'ATHLETE', 'Casey Vale'))
        self.assertTrue(pd.person_page_for_title_ask('how many people watched his films on Prime Video', 'ACTOR', 'Jason Statham', subject_hint='Jason Statham'))
        # a cohort of the person's own audience is a question about that audience: the profile is the base
        self.assertFalse(pd.person_page_for_title_ask('wat about series about skateboarding', 'ATHLETE', 'Casey Vale', subject_hint='Casey Vale searchers'))
        self.assertFalse(pd.person_page_for_title_ask('wat about series about skateboarding', 'ATHLETE', 'Casey Vale', subject_hint='people that Searched Casey Vale in the US'))
        src = open(os.path.join(ROOT, 'prometheus', 'generated_read.py'), encoding='utf-8').read()
        self.assertIn("person_page_for_title_ask(text, _pcat_b, base.get('subject'), subject_hint=(bind_subject or subj_hint))", src)
        src2 = open(os.path.join(ROOT, 'prometheus', 'generation_base.py'), encoding='utf-8').read()
        self.assertIn("person_page_for_title_ask(text, _pcat, page_base.get('subject'), subject_hint=subject_hint)", src2)

    def test_the_base_is_not_borrowed_for_its_own_cohort(self):
        base = {'subject': 'Casey Vale', 'source': 'page'}
        self.assertFalse(K.base_is_borrowed(ASK, 'people that Searched Casey Vale in the US', base))
        self.assertTrue(K.base_is_borrowed('how big is VICE Sports', 'VICE Sports', {'subject': 'Pinterest', 'source': 'page'}))

    def test_the_prompt_block_names_the_ceiling_as_the_bases_audience(self):
        blk = C.size_block('people that Searched Casey Vale in the US', 2851821, researched=False, cohort_of='Casey Vale')
        self.assertIn("AUDIENCE CEILING (binding). people that Searched Casey Vale in the US is a part of Casey Vale's audience of 2,851,821 US people", blk)
        self.assertIn('every row of a table sits inside the cohort', blk)
        self.assertNotIn('AUDIENCE SIZE (binding, anchor low)', blk)
        plain = C.size_block('Casey Vale', 2851821, researched=False)
        self.assertIn('AUDIENCE SIZE (binding, anchor low). Casey Vale: 2,851,821', plain)

    def test_read_blocks_carries_the_cohort_branch(self):
        src = open(os.path.join(ROOT, 'prometheus', 'knowledge.py'), encoding='utf-8').read()
        self.assertIn('cohort_of_base(named, base_subject)', src)
        self.assertIn("cap_source = 'profile' if cap else ''\n            cohort_base = base_subject if cap else ''", src)
        self.assertIn('cohort_of=cohort_base', src)


class TheShares(unittest.TestCase):
    def test_a_count_takes_only_its_own_share(self):
        pairs = C._pair_shares(copy.deepcopy(METRICS))
        self.assertEqual(pairs, {0: 59.5})
        self.assertEqual(C._companion_share(METRICS, 0), 59.5)
        self.assertIsNone(C._companion_share(METRICS, 2))
        self.assertIsNone(C._companion_share(METRICS, 3))

    def test_a_share_named_for_its_count_still_pairs_at_a_distance(self):
        mets = [{'name': 'buyers', 'label': 'Nike buyers', 'unit': 'count', 'value': 500000},
                {'name': 'other', 'label': 'Other count', 'unit': 'count', 'value': 100},
                {'name': 'nike_buyer_rate', 'label': 'Nike buyer rate', 'unit': 'pct', 'value': 12.5}]
        self.assertEqual(C._pair_shares(mets), {0: 12.5})


class TheGate(unittest.TestCase):
    def test_the_right_cap_moves_nothing(self):
        d, rec = C.size_gate(_data(), 2851821, salt='t')
        self.assertEqual(rec['moves'], [])
        self.assertEqual(d['metrics'][0]['value'], 1037241)

    def test_a_cap_holds_the_table_with_the_metrics(self):
        d, rec = C.size_gate(_data(), 102000, salt='t')
        moved = {m['metric']: m for m in rec['moves']}
        self.assertEqual(moved['skate_series_watchers']['share'], 59.5)
        self.assertIsNone(moved['top_series_viewers']['share'])          # never the rate on the word 'series'
        self.assertIn('row The Nine Club', moved)
        self.assertIn('row Street League Skateboarding broadcasts', moved)
        for row in d['breakdown']['rows']:
            self.assertLessEqual(row['pool'], 102000, row)
            self.assertNotEqual(row['pool'] % 10, 0, row)
        self.assertEqual(d['breakdown']['rows'][0]['pool'], 31824)      # 31.2% of the cap
        self.assertEqual(d['breakdown']['rows'][2]['pool'], 80173)      # already under the cap: untouched
        self.assertNotIn('543,775', d['headline'])


class FixturesCarryTheCase(unittest.TestCase):
    def test_the_live_shape_is_a_fixture(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        self.assertIn('fx077', ids)
        self.assertEqual(ids['fx077']['question'], ASK)
        self.assertEqual(ids['fx077']['recorded']['route'], 'page_analysis')


if __name__ == '__main__':
    unittest.main(verbosity=2)
