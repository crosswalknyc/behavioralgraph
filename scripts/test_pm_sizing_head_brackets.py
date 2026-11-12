#!/usr/bin/env python3
"""A sizing reply never carries a stray bracket (2026-11-12).

A sizing reply's heading reads "Sample check for <who> (<window>):" and
the exit step rewrites it in plain words: "<who>, <window>:". The step
read <who> as everything up to the FIRST bracket and <window> as
everything from there to the LAST one, so a name that carries its own
bracket came out torn:

    Sample check for Reacher (Prime Video series) (the last 12 months):
 -> Reacher, Prime Video series) (the last 12 months:

The window is the last bracket group, read with its own nested
brackets; a bracket in the name stays with the name; and the heading
goes out with every bracket paired.

Hermetic: the exit step on text; no network.

    python3 scripts/test_pm_sizing_head_brackets.py
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import guards as G  # noqa: E402

BODY = "\n- Sample: 70,083 individuals (0.701% incidence)\n- US audience in window: ~2.3M\n"


def paired(text):
    depth = 0
    for ch in text:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


class TheHeading(unittest.TestCase):

    def head(self, heading):
        out = G.scrub_sample_vocabulary(heading + BODY)
        return out.split('\n', 1)[0]

    def test_the_defect_as_it_shipped(self):
        h = self.head("Sample check for Reacher (Prime Video series) (the last 12 months):")
        self.assertEqual(h, "Reacher (Prime Video series), the last 12 months:")
        self.assertNotIn("series) (the", h)

    def test_a_bracketed_name_with_a_dated_window(self):
        h = self.head("Sample check for Reacher (Prime Video series) (trailing year (10-20-2025 to 10-20-2026)):")
        self.assertTrue(h.startswith("Reacher (Prime Video series), "), h)
        self.assertTrue(paired(h), h)
        self.assertNotIn('trailing year', h)
        h = self.head("Sample check for Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime) (07-01-2026 to 10-14-2026):")
        self.assertEqual(h, "Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime), July 1, 2026 to October 14, 2026:")

    def test_a_plain_name_reads_as_before(self):
        self.assertEqual(self.head("Sample check for UFC fans (trailing year (10-20-2025 to 10-20-2026)):"),
                         self.head("Sample check for UFC fans (trailing year (10-20-2025 to 10-20-2026)):"))
        h = self.head("Sample check for UFC fans (07-01-2026 to 10-14-2026):")
        self.assertEqual(h, "UFC fans, July 1, 2026 to October 14, 2026:")
        h = self.head("Sample check for NCIS: New York viewers (the last 12 months):")
        self.assertEqual(h, "NCIS: New York viewers, the last 12 months:")

    def test_no_window_at_all(self):
        self.assertEqual(self.head("Sample check for Reacher (Prime Video series):"), "Reacher, Prime Video series:")
        self.assertEqual(self.head("Sample check for Reacher:"), "Reacher:")

    def test_every_bracket_is_paired_whatever_comes_in(self):
        for heading in ("Sample check for Reacher (Prime Video series) (the last 12 months):",
                        "Sample check for Reacher, Prime Video series) (the last 12 months):",
                        "Sample check for Reacher (Prime Video series (the last 12 months):",
                        "Sample check for A (B) (C) (the last 12 months):",
                        "Sample check for A) B ( C:"):
            h = self.head(heading)
            self.assertTrue(paired(h), (heading, h))
            self.assertTrue(h.endswith(':'), h)


class TheParts(unittest.TestCase):

    def test_split(self):
        self.assertEqual(G.split_check_head("Reacher (Prime Video series) (the last 12 months)"),
                         ("Reacher (Prime Video series)", "the last 12 months"))
        self.assertEqual(G.split_check_head("UFC fans (trailing year (10-20-2025 to 10-20-2026))"),
                         ("UFC fans", "trailing year (10-20-2025 to 10-20-2026)"))
        self.assertEqual(G.split_check_head("UFC fans"), ("UFC fans", ""))
        self.assertEqual(G.split_check_head("A) B (C"), ("A) B (C", ""))

    def test_balance(self):
        self.assertEqual(G.balance_brackets("Reacher, Prime Video series) (the last 12 months"), "Reacher, Prime Video series the last 12 months")
        self.assertEqual(G.balance_brackets("Reacher (Prime Video series), the last 12 months:"), "Reacher (Prime Video series), the last 12 months:")
        self.assertEqual(G.balance_brackets("a (b (c) d"), "a b (c) d")
        self.assertEqual(G.balance_brackets(""), "")


if __name__ == '__main__':
    unittest.main(verbosity=1)
