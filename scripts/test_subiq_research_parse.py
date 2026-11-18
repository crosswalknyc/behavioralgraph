#!/usr/bin/env python3
"""Subscriber IQ research survives a cut-off reply and a low-confidence
bracket (2026-11-18, Outlander: Blood of My Blood on Starz).

Three trackers in one day read 131,927 US viewers for Starz's flagship:
the research reply ran past max_tokens inside its citation lists, the
brace scan never closed, json.loads('') raised, and the generator fell
to the 'niche' tier prior (80,000 base). The same prior also took over
whenever a parsed bracket came back with reach_confidence 'low'.

Pinned here:
  * a complete reply parses; braces inside strings do not fool the scan;
  * a cut-off reply still yields the headline bracket, stamped _truncated;
  * a numeric bracket at low confidence sets reach from its conservative
    bound, never the tier prior; the ceiling and override still win.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")

import SVOD_Churn_Attribution as S  # noqa: E402

FULL = ('{"searches_performed":12,"reach_method":"primetime_comparable",'
        '"reach_us_estimate":3000000,"reach_us_lower":2100000,"reach_us_upper":4200000,'
        '"reach_confidence":"low","reach_sources":["Starz press release: S8 premiere drew {nearly} 3M","Variety"],'
        '"conversion_pct":2.61,"pre_existing_pct":0.29,"avg_days_to_signup":9.5,'
        '"all_sources":["https://example.com/a","https://example.com/b"]}')


class ParseResearchJson(unittest.TestCase):
    def test_complete_reply_parses_with_braces_inside_strings(self):
        r = S._parse_research_json(FULL)
        self.assertEqual(r["reach_us_lower"], 2100000)
        self.assertNotIn("_truncated", r)
        self.assertEqual(r["reach_sources"][0], "Starz press release: S8 premiere drew {nearly} 3M")

    def test_fenced_reply_parses(self):
        r = S._parse_research_json("```json\n" + FULL + "\n```")
        self.assertEqual(r["reach_us_estimate"], 3000000)

    def test_cut_off_reply_keeps_the_bracket(self):
        cut = FULL[:FULL.index('"all_sources"') + 30]
        r = S._parse_research_json(cut)
        self.assertTrue(r["_truncated"])
        self.assertEqual((r["reach_us_estimate"], r["reach_us_lower"], r["reach_us_upper"]),
                         (3000000, 2100000, 4200000))
        self.assertEqual(r["reach_confidence"], "low")
        self.assertEqual(r["reach_method"], "primetime_comparable")
        self.assertEqual(len(r["reach_sources"]), 2)
        self.assertEqual(r["conversion_pct"], 2.61)

    def test_cut_inside_sources_list(self):
        cut = ('{"reach_method":"primetime_comparable","reach_us_estimate":880000,'
               '"reach_us_lower":640000,"reach_us_upper":1150000,"reach_confidence":"low",'
               '"reach_sources":["Analyst-supplied title-level universe: 1,599,223 (2025-08-08 to 2025-10-10)","https://www.ex')
        r = S._parse_research_json(cut)
        self.assertTrue(r["_truncated"])
        self.assertEqual(r["reach_us_lower"], 640000)
        self.assertEqual(len(r["reach_sources"]), 1)

    def test_no_bracket_is_none(self):
        self.assertIsNone(S._parse_research_json("no json here"))
        self.assertIsNone(S._parse_research_json(""))
        self.assertIsNone(S._parse_research_json('{"foo": 1'))


def _starz_config(**over):
    cfg = {
        "project_name": "Outlander_Blood_of_My_Blood_Season_2_to_date",
        "show_search_terms": ["Outlander: Blood of My Blood"],
        "platform_name": "Starz",
        "genre": "Drama",
        "content_cadence": "Weekly",
        "is_new_show": False,
        "episode_dates": ["2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09",
                          "2026-10-16", "2026-10-23", "2026-10-30", "2026-11-06"],
    }
    cfg.update(over)
    return cfg


def _low_conf_bracket(**over):
    r = {"reach_us_estimate": 3000000, "reach_us_lower": 2100000, "reach_us_upper": 4200000,
         "reach_confidence": "low", "reach_sources": ["a", "b"], "conversion_pct": 2.61,
         "pre_existing_pct": 0.29, "avg_days_to_signup": 9.5, "_claude_searched": True}
    r.update(over)
    return r


class LowConfidenceBracketSetsReach(unittest.TestCase):
    def setUp(self):
        self._research = S._research_show_externally_with_claude
        self._bracket = _low_conf_bracket()
        S._research_show_externally_with_claude = lambda **kw: dict(self._bracket)

    def tearDown(self):
        S._research_show_externally_with_claude = self._research

    def test_low_confidence_uses_the_conservative_bound(self):
        panel = S._build_synthetic_panel(_starz_config())
        self.assertEqual(panel["reach_source"], "claude_external_research")
        self.assertGreaterEqual(panel["reach_us"], 2100000)
        self.assertLess(panel["reach_us"], 2100000 * 1.08)

    def test_truncated_bracket_counts_too(self):
        self._bracket = _low_conf_bracket(_truncated=True)
        panel = S._build_synthetic_panel(_starz_config())
        self.assertEqual(panel["reach_source"], "claude_external_research")
        self.assertGreaterEqual(panel["reach_us"], 2100000)

    def test_ceiling_still_caps_a_slice(self):
        panel = S._build_synthetic_panel(_starz_config(reach_us_ceiling=1599190))
        self.assertLess(panel["reach_us"], 1599190)
        self.assertGreater(panel["reach_us"], 1599190 * 0.85)

    def test_override_still_wins(self):
        panel = S._build_synthetic_panel(_starz_config(reach_us_override=1599223))
        self.assertEqual(panel["reach_us"], 1599223)
        self.assertEqual(panel["reach_source"], "override")

    def test_null_reach_still_goes_to_priors_path(self):
        self._bracket = _low_conf_bracket(reach_us_estimate=None, reach_us_lower=None, reach_us_upper=None)
        validate = S._claude_validate_priors_reach
        S._claude_validate_priors_reach = lambda **kw: None
        try:
            panel = S._build_synthetic_panel(_starz_config())
        finally:
            S._claude_validate_priors_reach = validate
        self.assertEqual(panel["reach_source"], "priors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
