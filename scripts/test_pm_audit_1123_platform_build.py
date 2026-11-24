"""A platform named in a build order is a cut of the subject, never
dropped from an existing match (Nov 23 2026 ask audit, fix 4).

"can we build a profile of Paul Rodriquez on YouTube" and "Build a
profile of Paul Rodriguez only on YouTube" both came back as the whole
Paul Rodriguez profile on file with the YouTube scope gone; the reader
pushed back and left. The house rule for an embedded qualifier: the
Total Universe stays whole and the qualifier rides as a derived cut
named '{Subject} - {Cut}'. A platform the order scopes the subject to
is the behavioral cut '{Platform} users'.

`prometheus.build_platform_scope.apply` re-routes an existing_match
draft into a derive_cut of that file with the platform cut; the app's
two interpret paths call it right after the embedded-cut decomposer.
A platform's own audience, a consumption universe on a named service,
a content or service subject, a draft already carrying the platform,
and an order naming two platforms are left alone.

Pure tests, no network, no app boot.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from prometheus import build_platform_scope as bps  # noqa: E402


def _draft(**over):
    d = {'decision': 'existing_match', 'subject': 'Example Skater', 'existing_match_display_name': 'Example Skater',
         'existing_match_s3_key': 'Example_Skater_11_17_2026_01_55.csv', 'brand_category': 'ATHLETE', 'assumptions': ['the skateboarder']}
    d.update(over)
    return d


class ScopeWords(unittest.TestCase):
    def test_one_platform_as_where(self):
        for t in ("can we build a profile of Example Skater on YouTube", "Build a profile of Example Skater only on YouTube",
                  "build a profile of Example Brand on TikTok"):
            self.assertTrue(bps.platform_scope_of(t), t)

    def test_the_platforms_own_audience_two_platforms_and_none(self):
        for t in ("build a profile of YouTube viewers", "who uses TikTok", "build a profile of Example Skater",
                  "Example Skater on YouTube, TikTok and Instagram"):
            self.assertEqual(bps.platform_scope_of(t), '', t)

    def test_a_consumption_universe_is_read_as_one(self):
        self.assertTrue(bps.is_consumption_universe("people who watched Example Show on Netflix"))
        self.assertTrue(bps.is_consumption_universe("build a profile of Example Show viewers on Hulu", 'Example Show Viewers'))
        self.assertFalse(bps.is_consumption_universe("Build a profile of Example Skater only on YouTube", 'Example Skater'))


class ReRoute(unittest.TestCase):
    def test_existing_match_becomes_the_platform_cut(self):
        d = _draft()
        self.assertTrue(bps.apply(d, "Build a profile of Example Skater only on YouTube", compose=lambda p, c: f"{p} - {c}"))
        self.assertEqual(d['decision'], 'derive_cut')
        self.assertEqual(d['cut_label'], 'YouTube users')
        self.assertEqual(d['derive_type'], 'behavioral')
        self.assertEqual(d['existing_match_s3_key'], 'Example_Skater_11_17_2026_01_55.csv')
        self.assertEqual(d['subject'], 'Example Skater - YouTube users')
        self.assertEqual(d['name'], 'Example Skater - YouTube users')
        self.assertEqual(d['parent_display_name'], 'Example Skater')
        self.assertFalse(d['run_avid'])
        self.assertTrue(any('YouTube users' in a for a in d['assumptions']))
        self.assertNotIn('\u2014', ' '.join(d['assumptions']))

    def test_the_cut_name_without_a_composer(self):
        d = _draft()
        self.assertTrue(bps.apply(d, "can we build a profile of Example Skater on YouTube"))
        self.assertEqual(d['name'], 'Example Skater - YouTube users')

    def test_left_alone(self):
        cases = [
            (_draft(), "build a profile of Example Skater"),                                           # no platform
            (_draft(decision='new_build'), "build a profile of Example Skater on YouTube"),              # only an existing match re-routes here
            (_draft(subject='YouTube', existing_match_display_name='YouTube'), "build a profile of YouTube users on YouTube"),
            (_draft(subject='Example Show Viewers', existing_match_display_name='Example Show Viewers', brand_category='SERIES - DRAMA'),
             "build a profile of people who watched Example Show on Netflix"),                           # the service defines that universe
            (_draft(brand_category='SERIES - COMEDY'), "build a profile of Example Skater on YouTube"),  # a title: carriage, not a cut
            (_draft(existing_match_display_name='Example Skater - YouTube users', subject='Example Skater - YouTube users'),
             "build a profile of Example Skater on YouTube"),                                            # already carries it
            (_draft(), "Example Skater on YouTube and TikTok"),                                           # two platforms
        ]
        for d, t in cases:
            before = dict(d)
            self.assertFalse(bps.apply(d, t), t)
            self.assertEqual(d['decision'], before['decision'], t)
            self.assertNotIn('cut_label', d, t)

    def test_never_raises(self):
        self.assertFalse(bps.apply(None, "x"))
        self.assertFalse(bps.apply({'decision': 'existing_match'}, None))


class WiredIntoTheApp(unittest.TestCase):
    def test_both_interpret_paths_call_it_after_the_decomposer(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertEqual(src.count('_bps.apply('), 2)
        for a, b in (('_decompose_embedded_subject_cuts(spec_draft, prompt_text)', '_bps.apply(spec_draft, prompt_text, compose=_compose_cut_name)'),
                     ('_decompose_embedded_subject_cuts(draft, prompt)', '_bps.apply(draft, prompt, compose=_compose_cut_name)')):
            i, j = src.index(a), src.index(b)
            self.assertLess(i, j)
            self.assertLess(j - i, 600)
        self.assertTrue(re.search(r"\bprometheus import build_platform_scope\b", src))


if __name__ == '__main__':
    unittest.main(verbosity=2)
