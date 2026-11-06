#!/usr/bin/env python3
"""Arithmetic coherence on a read's prose (2026-11-05). Hermetic: no
model, no S3, no mail.

The Jeopardy Volume 2 read (admin, job 8e8ada8053b4, 22:56Z) said "for
the Rainbolt sourced group ... The suggested video rail on the creator
watch page carries 38.4% (132,414 people)". Rainbolt's group is 95,274
people; 132,414 is 38.4% of all 344,827 creator-sourced viewers.

Covered here:
  1. the Rainbolt sentence: 132,414 becomes about 36,585 off the group
     the sentence names; the share never moves; the trail says why
  2. a correct sentence is left alone (the fixed figure, its siblings)
  3. the 'N people (X%)' order and the 'N of the M readers (X%)' form
  4. a sentence whose base is the universe: 'Of the 1,884,317 viewers'
     binds, and an unqualified share that fits the universe stands
  5. a figure on a base the read never states is left as written and
     logged as unresolved; a name glued to the count keeps its figure
  6. a partition of shares off 100 is noted, never rewritten
  7. a recomputed count is messy (no trailing zero) and inside rounding
  8. apply() mirrors the fix into res['reads'] and stamps res['_arith']
  9. the read core and the matrix finish both run the pass and carry
     the trail
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import arith_coherence as AC  # noqa: E402

METRICS_LINE = ("US viewers of the YouTube edition, Volume 2: 1,884,317, Creator sourced discovery: 344,827, "
                "Rainbolt as first touch: 95,274.")
RAINBOLT = ("- Last touch before watching: for the Rainbolt sourced group the final touchpoint is overwhelmingly "
            "on platform and one click wide. The suggested video rail on the creator watch page carries 38.4% "
            "(132,414 people), a return trip through YouTube search carries 21.7%, a Shorts cutdown of the "
            "Volume 2 episode carries 16.3%, the Jeopardy channel page and subscription feed carry 13.9%, and an "
            "off platform link out of Reddit or a Facebook group carries 9.7%.")
DISCOVERY = ("- How they discovered the show: the single largest path is not a creator at all, it is the suggested "
             "chain off another Jeopardy or game show clip at 27.4% (516,303 people). Creator video is second at "
             "18.3%, and the owned subscription feed third at 14.9%.")
RES = {'subject': 'Jeopardy (YouTube edition, Volume 2)',
       'metrics': [{'name': 'volume2_us_viewers', 'label': 'US viewers of the YouTube edition, Volume 2',
                    'value': 1884317, 'unit': 'viewers'},
                   {'name': 'creator_sourced_viewers', 'label': 'Creator sourced discovery', 'value': 344827,
                    'unit': 'viewers'},
                   {'name': 'rainbolt_first_touch', 'label': 'Rainbolt as first touch', 'value': 95274,
                    'unit': 'viewers'}]}


def _reply(*lines):
    return 'On Jeopardy (YouTube edition, Volume 2):\n\n' + METRICS_LINE + '\n\n' + '\n'.join(lines)


class RainboltSentence(unittest.TestCase):
    def test_people_figure_recomputed_off_the_named_group(self):
        out, info = AC.apply(_reply(RAINBOLT, DISCOVERY), dict(RES))
        self.assertTrue(info['changed'])
        self.assertEqual(len(info['fixes']), 1)
        f = info['fixes'][0]
        self.assertEqual(f['from'], 132414)
        self.assertEqual(f['base'], 95274)
        self.assertEqual(f['base_label'], 'Rainbolt as first touch')
        self.assertAlmostEqual(f['to'], 36585, delta=60)
        self.assertNotEqual(f['to'] % 10, 0)
        self.assertIn('38.4% (36,585 people)', out)
        self.assertNotIn('132,414', out)
        # the share is untouched, the siblings are untouched
        self.assertIn('carries 38.4%', out)
        self.assertIn('carries 21.7%, a Shorts cutdown', out)
        self.assertIn('27.4% (516,303 people)', out)
        # the trail names the base and what the wrong figure was a share of
        self.assertIn('Rainbolt as first touch = 95,274', f['note'])
        self.assertIn('Creator sourced discovery = 344,827', f['note'])
        self.assertIn('arith fixed 1', AC.trail(info))

    def test_text_alone_resolves_the_same_base(self):
        out, info = AC.apply(_reply(RAINBOLT, DISCOVERY), None)
        self.assertEqual([f['to'] for f in info['fixes']], [info['fixes'][0]['to']])
        self.assertIn('38.4% (36,585 people)', out)

    def test_siblings_on_the_wrong_base_follow_the_sentence(self):
        bad = RAINBOLT.replace('carries 21.7%,', 'carries 21.7% (74,827 people),') \
                      .replace('carries 16.3%,', 'carries 16.3% (56,207 people),')
        out, info = AC.apply(_reply(bad, DISCOVERY), dict(RES))
        self.assertEqual(sorted(f['from'] for f in info['fixes']), [56207, 74827, 132414])
        self.assertIn('21.7% (20,674 people)', out)
        self.assertIn('16.3% (15,5', out)


class CorrectSentenceLeftAlone(unittest.TestCase):
    def test_the_fixed_read_passes_untouched(self):
        good = RAINBOLT.replace('(132,414 people)', '(36,585 people)') \
                       .replace('carries 21.7%,', 'carries 21.7% (20,674 people),')
        out, info = AC.apply(_reply(good, DISCOVERY), dict(RES))
        self.assertFalse(info['changed'])
        self.assertEqual(info['fixes'], [])
        self.assertEqual(info['unresolved'], [])
        self.assertEqual(out, _reply(good, DISCOVERY))

    def test_idempotent(self):
        out, _ = AC.apply(_reply(RAINBOLT, DISCOVERY), dict(RES))
        out2, info2 = AC.apply(out, dict(RES))
        self.assertFalse(info2['changed'])
        self.assertEqual(out, out2)


class PeopleFirstOrder(unittest.TestCase):
    def test_people_then_share_off_a_back_reference(self):
        text = "- Rainbolt brought 95,274 viewers (5.1%). Of those, 132,414 people (38.4%) came through the rail."
        out, info = AC.apply(text, {'metrics': [{'label': 'US viewers', 'value': 1884317, 'unit': 'viewers'}]})
        self.assertEqual(len(info['fixes']), 1)
        self.assertEqual(info['fixes'][0]['base'], 95274)
        self.assertIn('36,585 people (38.4%)', out)
        self.assertIn('95,274 viewers (5.1%)', out)      # fits the universe, stands

    def test_n_of_the_m_form(self):
        ok = ("Lore Olympus reaches 1,382,647 US readers, and 1,021,583 of the 1,382,647 readers (73.9%) "
              "have no Prime Video stream.")
        out, info = AC.apply(ok, None)
        self.assertFalse(info['changed'])
        wrong = ok.replace('1,021,583 of the', '721,583 of the')
        out, info = AC.apply(wrong, None)
        self.assertEqual(len(info['fixes']), 1)
        self.assertEqual(info['fixes'][0]['base'], 1382647)
        self.assertAlmostEqual(info['fixes'][0]['to'], 1021776, delta=800)
        self.assertRegex(out, r'1,02\d,\d{3} of the 1,382,647 readers \(73\.9%\)')


class UniverseBase(unittest.TestCase):
    def test_of_the_universe_binds(self):
        text = ("US viewers of Volume 2: 1,884,317.\n- Of the 1,884,317 viewers, 27.4% (416,303 people) came through "
                "the suggested rail and 9.7% (182,779 people) through Shorts.")
        out, info = AC.apply(text, None)
        self.assertEqual([f['from'] for f in info['fixes']], [416303])
        self.assertEqual(info['fixes'][0]['base'], 1884317)
        self.assertIn('27.4% (516,303 people)', out)
        self.assertIn('9.7% (182,779 people)', out)

    def test_unqualified_share_that_fits_the_universe_stands(self):
        out, info = AC.apply(_reply(DISCOVERY), dict(RES))
        self.assertFalse(info['changed'])
        self.assertEqual(info['unresolved'], [])


class UnstatedBaseStands(unittest.TestCase):
    def test_a_figure_on_a_base_the_read_never_states_is_logged_not_moved(self):
        text = ("- Depth works against Paramount+ here: the Avid tier sits at 22.1583% (12,003,181 people) versus "
                "24.2051% audience-wide.\n- Where this shared audience buys: Amazon 84.1189% (157,128,599 people) "
                "carries the reach.")
        out, info = AC.apply(text, {'metrics': [{'label': 'HBO Max and Paramount+ overlap', 'value': 45213554,
                                                 'unit': 'people'}]})
        self.assertFalse(info['changed'])
        self.assertEqual(len(info['unresolved']), 2)
        self.assertIn('arith unresolved', AC.trail(info))

    def test_a_name_glued_to_the_count_keeps_its_own_figure(self):
        text = ("- Incrementality: 2,611,739 of the 4,217,683 readers have no Prime Video stream, 61.9% of the group. "
                "Per title the incremental counts are UnOrdinary 1,702,517 (62.1%), The Gamer 918,463 (61.8%) and "
                "Lumine 723,896 (64.1%).")
        out, info = AC.apply(text, None)
        self.assertFalse(info['changed'])

    def test_a_nearer_count_the_figure_fits_is_its_base(self):
        text = ("- 21 films: 2,183,642 of the 5,214,877 film viewers open music first. Pool 1,412,836 viewers. "
                "Music first: 798,213 (56.5%) | Title first: 436,591 (30.9%) | No music touch: 178,032 (12.6%)")
        out, info = AC.apply(text, None)
        self.assertFalse(info['changed'])


class PartitionNotes(unittest.TestCase):
    def test_shares_that_miss_100_are_noted_not_rewritten(self):
        text = ("- For the Shorts sourced group the rail carries 48.4%, search carries 21.7%, the channel page "
                "carries 16.3% and off platform links carry 10.9%.")
        out, info = AC.apply(text, None)
        self.assertEqual(out, text)
        self.assertEqual(len(info['notes']), 1)
        self.assertRegex(info['notes'][0], r'sum to 97\.3, not 100')
        self.assertIn('left as written', info['notes'][0])
        # inside the 1.5 point band nothing is said
        close = text.replace('10.9%', '12.9%')
        out, info = AC.apply(close, None)
        self.assertEqual(info['notes'], [])

    def test_shares_that_reach_100_are_quiet(self):
        out, info = AC.apply(_reply(RAINBOLT), dict(RES))
        self.assertEqual(info['notes'], [])

    def test_a_ranked_top_three_is_not_a_partition(self):
        text = "- The top three paths: the rail carries 27.4%, creator video is second at 18.3% and the feed third at 14.9%."
        out, info = AC.apply(text, None)
        self.assertEqual(info['notes'], [])


class MessyCounts(unittest.TestCase):
    def test_recomputed_count_never_ends_in_zero(self):
        for base in (100000, 240000, 95274, 1884317, 36000):
            for pct in ('25.0', '38.4', '10.0', '50.0', '12.5'):
                v = AC.messy_count(pct, base, 7)
                self.assertNotEqual(v % 10, 0, (pct, base, v))
                self.assertTrue(AC.fits(v, pct, base), (pct, base, v))

    def test_tolerance_is_the_share_rounding(self):
        self.assertTrue(AC.fits(36585, '38.4', 95274))
        self.assertTrue(AC.fits(36620, '38.4', 95274))
        self.assertFalse(AC.fits(37400, '38.4', 95274))
        self.assertFalse(AC.fits(132414, '38.4', 95274))
        self.assertTrue(AC.fits(96100, '5.1', 1884317))


class MirrorIntoRes(unittest.TestCase):
    def test_reads_and_stamp(self):
        res = dict(RES, reads=[RAINBOLT[2:], DISCOVERY[2:]], headline='x')
        out, info = AC.apply(_reply(RAINBOLT, DISCOVERY), res)
        self.assertIn('38.4% (36,585 people)', res['reads'][0])
        self.assertEqual(res['reads'][1], DISCOVERY[2:])
        self.assertEqual(len(res['_arith']['fixes']), 1)

    def test_never_raises(self):
        out, info = AC.apply(None, None)
        self.assertEqual(out, '')
        self.assertFalse(info['changed'])
        out, info = AC.apply('', {'metrics': 'nonsense'})
        self.assertEqual(out, '')
        self.assertFalse(info['changed'])


class Wiring(unittest.TestCase):
    def test_read_core_and_matrix_finish_run_the_pass(self):
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertGreaterEqual(src.count('_arith.apply(reply, res'), 2)
        # the single read: after the verify passes, before the counting block
        i_pass = src.index('_arith.apply(reply, res')
        self.assertLess(src.index("stages['verify'] = int((time.monotonic() - _t_verify) * 1000)"), i_pass)
        self.assertLess(i_pass, src.index('reply = _meth.ensure(reply, res, question=text,'))
        # the trail rides the derivation line and the verify stamp
        self.assertIn("_verify_stamp['arith']", src)
        self.assertIn('f"; {_arith_trail}" if _arith_trail else', src)
        # the matrix finish
        j = src.index('def _pm_finish_matrix_read(')
        k = src.index('def _pm_run_matrix_read(')
        body = src[j:k]
        self.assertIn('_arith.apply(reply, res', body)
        self.assertIn('_arith_trail', body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
