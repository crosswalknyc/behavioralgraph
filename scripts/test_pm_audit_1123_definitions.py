"""A glossary term asked plainly is answered from the glossary, at once,
before the Reader and the lanes (Nov 23 2026 ask audit, fix 3).

One afternoon on a profile page: "what is share", "how do I read share
share vs penetration", "what metric is penetration vs share" and "what
does cohort mean in this" each took 26 to 40 seconds through the model
(a Reader call, a digest, anchors, the model), and "what does share
mean", asked after a journey answer in the same thread, was answered as
a journey row ("News or editorial article share" ... 8.9% of that
table's base).

The rules:

* `kpi_definitions.standing_answer(text, ctx)` answers a plain
  definition question from the house glossary in the standing lane,
  before the Reader: no model call.
* The bare word "share" is the profile's Share of category column on a
  profile view only (`short_aliases`, `PROFILE_VIEWS`); on a journey or
  any other view the word is that view's own and the glossary stands
  aside.
* A question that names two terms ("share vs penetration") gets both
  entries, in the order the words name them (`find_definitions`,
  `definitions_reply`).
* New cues: "how do I read X", "what metric is X", "explain X vs Y",
  "the difference between X and Y".
* A quoted label the glossary does not know, a deictic "how is this
  calculated", and a term the glossary does not carry still fall
  through to the lanes as before.

Pure tests, no network, no app.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from prometheus import kpi_definitions as kd  # noqa: E402


def _ctx(view_id):
    return {'view_context': {'view_id': view_id}}


class ShareOnAProfile(unittest.TestCase):
    def test_bare_share_is_the_profile_column(self):
        for t in ("what is share", "what does share mean", "what is the share column", "what does share mean here"):
            r = kd.standing_answer(t, _ctx('profileIQ'))
            self.assertIsNotNone(r, t)
            self.assertEqual(r['kpi_definition'], ['category_share'], t)
            self.assertIn('Share of category', r['reply'])

    def test_bare_share_is_not_claimed_on_another_view(self):
        for v in ('journeyIQ', 'subscriberIQ', 'attributionIQ'):
            self.assertIsNone(kd.standing_answer("what does share mean", _ctx(v)), v)
            self.assertIsNone(kd.standing_answer("what is share", _ctx(v)), v)

    def test_full_aliases_still_answer_everywhere(self):
        r = kd.standing_answer("how is penetration calculated", _ctx('subscriberIQ'))
        self.assertEqual((r or {}).get('kpi_definition'), ['penetration'])


class TwoTermsBothAnswered(unittest.TestCase):
    def test_share_vs_penetration_names_two_in_order(self):
        r = kd.standing_answer("how do I read share share vs penetration", _ctx('profileIQ'))
        self.assertEqual(r['kpi_definition'], ['category_share', 'penetration'])
        self.assertLess(r['reply'].index('Share of category'), r['reply'].index('Penetration:'))
        r2 = kd.standing_answer("what metric is penetration vs share", _ctx('profileIQ'))
        self.assertEqual(r2['kpi_definition'], ['penetration', 'category_share'])

    def test_explain_x_vs_y_and_difference_between(self):
        self.assertEqual(kd.standing_answer("can you explain share vs penetration on the scripted series viewers for Example Skater", _ctx('profileIQ'))['kpi_definition'],
                         ['category_share', 'penetration'])
        self.assertEqual(kd.standing_answer("what is the difference between index and penetration", _ctx('profileIQ'))['kpi_definition'],
                         ['index', 'penetration'])

    def test_an_alias_inside_a_longer_alias_is_the_longer_entry(self):
        # "share of the audience" is penetration's own alias; the bare share must not ride along as a second entry
        r = kd.standing_answer("what does share of the audience mean", _ctx('profileIQ'))
        self.assertEqual(r['kpi_definition'], ['penetration'])


class StillWithTheLanes(unittest.TestCase):
    def test_unknown_terms_and_pointers_fall_through(self):
        for t, v in (("what does cohort mean in this", 'profileIQ'), ("can you explain how to read this", 'profileIQ'),
                     ('what does "Saw a retarget" mean', 'journeyIQ'), ("explain the difference between the two reads", 'profileIQ'),
                     ("what is the index for nike", 'profileIQ'), ("what is the share for Nike", 'profileIQ')):
            self.assertIsNone(kd.standing_answer(t, _ctx(v)), t)

    def test_the_crosswalk_sample_keeps_its_own_answer(self):
        # the product fact about the ten million has its own lane; the audience-size entry never answers for it
        for t in ("how can i interpret the results from the profile, what's th meaning of the crosswalk sample and US audience",
                  "what is the 10 million sample you keep mentioning", "how big is the crosswalk sample"):
            self.assertIsNone(kd.standing_answer(t, _ctx('profileIQ')), t)

    def test_a_long_or_many_question_ask_is_not_a_definition(self):
        t = ("identify brand partnerships that index the highest for this audience and tell me the share of each. "
             "Can you define who my key audience is? Is my audience non-tech?")
        self.assertFalse(kd.is_definition_ask(t, 'profileIQ'))
        self.assertIsNone(kd.standing_answer(t, _ctx('profileIQ')))


class FixturesCarryTheCases(unittest.TestCase):
    def test_the_live_shapes_are_fixtures(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        for fid in ('fx065', 'fx066', 'fx067'):
            self.assertIn(fid, ids)
            self.assertEqual(ids[fid]['recorded']['route'], 'kpi_definition', fid)
        self.assertIn('fx068', ids)
        self.assertNotEqual(ids['fx068']['recorded']['route'], 'kpi_definition', 'the journey view keeps its own share')


if __name__ == '__main__':
    unittest.main(verbosity=2)
