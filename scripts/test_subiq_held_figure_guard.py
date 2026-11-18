#!/usr/bin/env python3
"""A tracker never ships as a tiny number under a title that already holds
a figure, and a figure supplied by hand is still read against what the
title holds (2026-11-18, Outlander: Blood of My Blood on Starz).

What went wrong: a 30-day tracker's research reply was cut off, the run
fell to an 80,000-base tier prior and shipped at 131,927 US viewers under
a title held at 1,599,223. The by-hand regeneration that followed passed
its viewer count in directly; the plausibility step logged an error and
its record read PASS.

Pinned here (hermetic: the model client is a stub, no network, no mail):
  a. a cut-off or unreadable reply is asked for again; a complete second
     reply is used; when it still fails the run sizes off the title's held
     figure scaled to its window, never the tier prior; a low, unconfident
     number under a quarter of the held figure is lifted; a complete,
     confident read is left alone;
  c. every figure, supplied ones included, goes through the held figure
     check, which reports and never blocks; a plausibility step that did
     not run to an answer reads UNCHECKED, never PASS.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["REGRESSION_TEST_MODE"] = "1"
os.environ.pop("OPENAI_API_KEY", None)

import SVOD_Churn_Attribution as S  # noqa: E402
import claude_client as CC  # noqa: E402  (after S: the generator pins its own copy of the client)

FULL = ('{"searches_performed":12,"reach_method":"primetime_comparable",'
        '"reach_us_estimate":3000000,"reach_us_lower":2100000,"reach_us_upper":4200000,'
        '"reach_confidence":"low","reach_sources":["a","b"],"conversion_pct":2.61,'
        '"pre_existing_pct":0.29,"avg_days_to_signup":9.5,"all_sources":["https://example.com/a"]}')
CUT = FULL[:FULL.index('"all_sources"') + 30]

S1 = {"season": 1, "us_viewers": 1599223, "sample_size": 48476, "window": {"start": "2025-08-08", "end": "2025-10-10"}}
S2 = {"season": 2, "us_viewers": 1476764, "sample_size": 44764, "window": {"start": "2026-09-18", "end": "2026-11-18"}}


def _config(**over):
    cfg = {
        "project_name": "Show_X_30_days",
        "show_search_terms": ["Show X"],
        "platform_name": "Starz",
        "genre": "Drama",
        "content_cadence": "Weekly",
        "is_new_show": False,
        "campaign_start": datetime(2026, 9, 18),
        "campaign_end": datetime(2026, 10, 17),
        "episode_dates": ["2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16"],
    }
    cfg.update(over)
    return cfg


class _Model:
    """claude_messages stand-in: replies in order, records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


class RetryACutOffReply(unittest.TestCase):
    def setUp(self):
        self._cm, self._on = CC.claude_messages, CC.is_claude_reasoning_enabled
        CC.is_claude_reasoning_enabled = lambda: True
        S._EXTERNAL_RESEARCH_CACHE.clear()

    def tearDown(self):
        CC.claude_messages, CC.is_claude_reasoning_enabled = self._cm, self._on
        S._EXTERNAL_RESEARCH_CACHE.clear()

    def _research(self, model):
        CC.claude_messages = model
        return S._research_show_externally_with_claude(
            show_name="Show X", platform_name="Starz", genre="Drama", content_cadence="Weekly",
            episode_count=5, campaign_start=datetime(2026, 9, 18), campaign_end=datetime(2026, 10, 17),
            is_new_show=False, platform_info={"tier": "niche"}, context_note=None)

    def test_complete_reply_is_asked_once(self):
        m = _Model(FULL)
        r = self._research(m)
        self.assertEqual(len(m.calls), 1)
        self.assertNotIn("_incomplete", r)

    def test_cut_off_reply_is_asked_again_and_the_complete_one_wins(self):
        m = _Model(CUT, FULL)
        r = self._research(m)
        self.assertEqual(len(m.calls), 2)
        self.assertIn("could not be read to the end", m.calls[1]["user"])
        self.assertNotIn("_truncated", r)
        self.assertNotIn("_incomplete", r)
        self.assertEqual(r["reach_us_lower"], 2100000)

    def test_unreadable_reply_is_asked_again(self):
        m = _Model("I could not find anything useful.", FULL)
        r = self._research(m)
        self.assertEqual(len(m.calls), 2)
        self.assertEqual(r["reach_us_estimate"], 3000000)

    def test_cut_off_twice_is_marked_incomplete(self):
        m = _Model(CUT, CUT)
        r = self._research(m)
        self.assertEqual(len(m.calls), 2)
        self.assertTrue(r["_truncated"] and r["_incomplete"])

    def test_nothing_readable_twice_is_none(self):
        m = _Model("no object here", "still nothing")
        self.assertIsNone(self._research(m))
        self.assertEqual(len(m.calls), 2)


class SizeOffTheHeldFigure(unittest.TestCase):
    def setUp(self):
        self._research = S._research_show_externally_with_claude
        self._validate = S._claude_validate_priors_reach
        self.reply = None
        S._research_show_externally_with_claude = lambda **kw: (dict(self.reply) if self.reply else None)
        S._claude_validate_priors_reach = lambda **kw: self.fail("the tier prior was consulted")

    def tearDown(self):
        S._research_show_externally_with_claude = self._research
        S._claude_validate_priors_reach = self._validate

    def _expected(self, held, days):
        return held["us_viewers"] * S._window_scale(days, S._window_span_days(held["window"]["start"], held["window"]["end"]))

    def test_window_scale_is_a_flattening_curve(self):
        self.assertAlmostEqual(S._window_scale(4, 64), 0.276, delta=0.02)     # a premiere window of a season
        self.assertAlmostEqual(S._window_scale(30, 64), 0.77, delta=0.03)
        self.assertEqual(S._window_scale(64, 64), 1.0)
        self.assertEqual(S._window_scale(0, 64), 1.0)
        self.assertLessEqual(S._window_scale(400, 30), 1.5)

    def test_cut_off_twice_sizes_off_the_held_figure_not_the_prior(self):
        self.reply = json.loads(FULL) | {"_truncated": True, "_incomplete": True}
        panel = S._build_synthetic_panel(_config(held_reads=[S1, S2], run_season=2))
        exp = self._expected(S2, 30)
        self.assertEqual(panel["reach_source"], "held_title_figure")
        self.assertGreaterEqual(panel["reach_us"], exp * 0.90 - 1)
        self.assertLess(panel["reach_us"], exp * 0.97 + 1)
        self.assertGreater(panel["reach_us"], 131927 * 5)

    def test_no_reply_at_all_sizes_off_the_held_figure(self):
        self.reply = None
        panel = S._build_synthetic_panel(_config(held_reads=[S1], run_season=1))
        self.assertEqual(panel["reach_source"], "held_title_figure")
        self.assertLess(panel["reach_us"], S1["us_viewers"])

    def test_null_reach_sizes_off_the_held_figure(self):
        self.reply = json.loads(FULL) | {"reach_us_estimate": None, "reach_us_lower": None, "reach_us_upper": None}
        panel = S._build_synthetic_panel(_config(held_reads=[S1], run_season=1))
        self.assertEqual(panel["reach_source"], "held_title_figure")

    def test_run_without_a_season_reads_the_season_it_falls_in(self):
        self.reply = None
        panel = S._build_synthetic_panel(_config(held_reads=[S1, S2], run_season=None))
        exp = self._expected(S2, 30)
        self.assertGreaterEqual(panel["reach_us"], exp * 0.90 - 1)
        self.assertLess(panel["reach_us"], exp * 0.97 + 1)

    def test_two_titles_never_land_on_one_number(self):
        self.reply = None
        a = S._build_synthetic_panel(_config(held_reads=[S1], run_season=1))["reach_us"]
        b = S._build_synthetic_panel(_config(held_reads=[S1], run_season=1,
                                             show_search_terms=["Show Y"]))["reach_us"]
        self.assertNotEqual(a, b)

    def test_without_a_held_figure_a_cut_off_bracket_still_sets_reach(self):
        S._claude_validate_priors_reach = self._validate
        self.reply = json.loads(FULL) | {"_truncated": True, "_incomplete": True}
        panel = S._build_synthetic_panel(_config())
        self.assertEqual(panel["reach_source"], "claude_external_research")
        self.assertGreaterEqual(panel["reach_us"], 2100000 * 0.95)

    def test_unconfident_tiny_number_is_lifted(self):
        self.reply = json.loads(FULL) | {"reach_us_estimate": 150000, "reach_us_lower": 100000,
                                         "reach_us_upper": 220000, "reach_confidence": "low"}
        cfg = _config(campaign_end=datetime(2026, 11, 18), held_reads=[S1, S2], run_season=2)
        panel = S._build_synthetic_panel(cfg)
        self.assertTrue(panel["reach_source"].endswith("+held_floor"))
        self.assertGreater(panel["reach_us"], S2["us_viewers"] * 0.85)
        self.assertEqual(cfg["_held_locked_reach"], panel["reach_us"])

    def test_complete_confident_read_is_left_alone(self):
        self.reply = json.loads(FULL) | {"reach_us_estimate": 150000, "reach_us_lower": 100000,
                                         "reach_us_upper": 220000, "reach_confidence": "high"}
        cfg = _config(campaign_end=datetime(2026, 11, 18), held_reads=[S1, S2], run_season=2)
        panel = S._build_synthetic_panel(cfg)
        self.assertEqual(panel["reach_source"], "claude_external_research")
        self.assertLess(panel["reach_us"], 120000)
        rep = S.held_figure_check(cfg, panel["reach_us"], panel["reach_source"])
        self.assertEqual(rep["status"], "FLAGGED")          # reported, not changed

    def test_supplied_figure_still_wins(self):
        self.reply = None
        panel = S._build_synthetic_panel(_config(held_reads=[S1], run_season=1, reach_us_override=1599223))
        self.assertEqual((panel["reach_us"], panel["reach_source"]), (1599223, "override"))


class EveryFigureIsCheckedAgainstTheHeldOnes(unittest.TestCase):
    def test_supplied_figure_on_another_seasons_number_is_reported(self):
        cfg = _config(campaign_end=datetime(2026, 11, 18), held_reads=[S1], run_season=2, reach_us_override=1599182)
        rep = S.held_figure_check(cfg, 1599182, "override")
        self.assertEqual(rep["status"], "FLAGGED")
        self.assertTrue(any("Season 1" in f for f in rep["flags"]))

    def test_supplied_figure_wildly_below_is_reported(self):
        cfg = _config(campaign_end=datetime(2026, 11, 18), held_reads=[S1, S2], run_season=2, reach_us_override=131927)
        rep = S.held_figure_check(cfg, 131927, "override")
        self.assertEqual(rep["status"], "FLAGGED")
        self.assertTrue(any("under a quarter" in f for f in rep["flags"]))

    def test_slice_above_its_containing_window_is_reported(self):
        cfg = _config(held_reads=[S2], run_season=2)
        self.assertEqual(S.held_figure_check(cfg, 1600000, "override")["status"], "FLAGGED")

    def test_sound_figure_reads_ok(self):
        cfg = _config(held_reads=[S1, S2], run_season=2)
        rep = S.held_figure_check(cfg, 1273942, "override")
        self.assertEqual((rep["status"], rep["flags"]), ("OK", []))
        self.assertEqual(rep["basis"]["held_us"], 1476764)

    def test_no_held_figure_and_bad_input_never_raise(self):
        self.assertEqual(S.held_figure_check(_config(), 500000)["status"], "NO_HELD_FIGURE")
        self.assertIn(S.held_figure_check({"held_reads": [{"us_viewers": "x"}, None, 7]}, None)["status"],
                      ("NO_HELD_FIGURE", "UNCHECKED"))

    def test_check_is_report_only(self):
        src = open(S.__file__, encoding="utf-8").read()
        body = src[src.index("def held_figure_check("):src.index("def _held_reads_for(")]
        for needle in ("raise ", "send_raw_email", "send_email", "client('ses'", "smtplib", "put_object"):
            self.assertNotIn(needle, body)

    def test_by_hand_run_reads_held_figures_only_outside_test_mode(self):
        cfg = _config()
        self.assertEqual(S._held_reads_for(cfg), [])                       # test mode: no registry read
        self.assertEqual(S._held_reads_for(_config(held_reads=[S1])), [S1])

    def test_plausibility_step_that_did_not_run_is_unchecked(self):
        out = S.ai_validate_metrics("Show X", "Starz", 48476, 1253, 2.58)
        self.assertTrue(out.get("unchecked") and out.get("passed"))
        src = open(S.__file__, encoding="utf-8").read()
        self.assertIn("'UNCHECKED' if validation.get('unchecked')", src)
        self.assertIn("'unchecked': True, 'flags': [], 'note': f'AI validation error", src)

    def test_held_sized_reach_is_locked_against_later_adjustment(self):
        import pandas as pd
        df = pd.DataFrame({"Category": ["Total Show Watchers"], "Count": [100]})
        p = {"_held_locked_reach": 1200000}
        out, changes = S.apply_ai_adjustments(df, {"passed": False, "flags": ["x"]}, 100, 5, "Starz", p)
        self.assertEqual(changes, [])
        self.assertTrue(any("held figure" in f for f in p["_ai_flags"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
