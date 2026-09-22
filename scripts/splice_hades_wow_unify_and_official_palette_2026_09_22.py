#!/usr/bin/env python3
"""Atomic byte-level splice on templates/index.html.

Three coordinated frontend fixes for the client feedback on
The Influencer Project - Hades:

1. Add `official` to IIQ_QPALETTE so studio-first-party posts render
   with their own chip color instead of the grey `unknown` fallback.
2. Rewrite `_iiqAssetWoWDelta` so per-asset WoW math uses the same
   `_iiqPitFactor` step function that the Weekly Summary header chip
   uses. This unifies the Trend Module's asset-mover panel with the
   header - the two now always tell the same story about a given
   asset's week-over-week movement.
3. Rewrite `_iiqTrendTotalExposureThisWeek` to compute
   `sum over in_view of views * (pit_now - pit_prev)` - the count
   of viewers actually gained in the last 7 days per the same PIT
   accrual model. Header chip and total-exposure tile now share a
   source of truth.

Root cause the client saw
-------------------------
Two math paths were computing "week over week exposure" against the
same asset set:

  * Weekly Summary WoW chip -> `_iiqPitFactor` (a 120-day cumulative
    accrual curve, always defined even when the daily-series tail is
    tiny).
  * Trend Module tile + asset movers -> `iiqSynthAssetDailySeries`
    (renormalizes over the shorter of [posted..now, posted..+120d]
    so mature assets' tails become near-zero at integer precision).

For a campaign whose assets skew older (posted more than a few weeks
before as_of) the PIT curve still moves noticeably day-over-day while
the daily-series tail rounds to zero. Result: header chip reads
+56.2% WoW while the Trend Module tile says +0 viewers and the
asset panel says no movement.

Fix: point both surfaces at PIT-delta. The daily-series function is
kept unchanged (still drives the ladder-up between daily views and
per-asset totals in every other view). Only the WoW helper and the
tile helper switch to PIT.

`official` palette
------------------
IIQ_QPALETTE gets a `#e6b53f` (Signal Olive family, warm gold)
color. This is the ONLY new color added; every other place that
handles paid_or_organic (grouping, filter dropdowns, count buckets)
inherits the existing `unknown` fallback for now and keeps official
posts visible. The chip and dot colors switch to gold, which is
what the client will see next to the three studio posts.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_hades_wow_unify_official_palette.html")


def splice(src: str, old: str, new: str, desc: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x (must be unique)")
    return src.replace(old, new, 1)


# --------------------------------------------------------------------------
# Splice 1: IIQ_QPALETTE - add `official` color
# --------------------------------------------------------------------------

PALETTE_OLD = """            var IIQ_QPALETTE = {
                paid:    '#22d3ee',
                organic: '#22c55e',
                natural: '#22c55e',
                earned:  '#3358FF',
                unknown: '#94a3b8',"""

PALETTE_NEW = """            var IIQ_QPALETTE = {
                paid:    '#22d3ee',
                organic: '#22c55e',
                natural: '#22c55e',
                official: '#e6b53f',
                earned:  '#3358FF',
                unknown: '#94a3b8',"""


# --------------------------------------------------------------------------
# Splice 2: _iiqAssetWoWDelta - swap synth-series math for PIT-delta
# --------------------------------------------------------------------------

ASSET_WOW_OLD = """            // Per-asset week-over-week exposure delta. Returns:
            //   { curr, prev, delta_pct (jittered), launch_week (bool) }
            // launch_week: asset had no views in the prior 7-day window
            // (was posted inside the current window, or the synth series
            // put no volume in the prior week).
            function _iiqAssetWoWDelta(asset, asOfIso) {
                if (!asset || !asOfIso) return { curr: 0, prev: 0, delta_pct: 0, launch_week: true };
                var totalViews = Number(asset.ext_view_count) || 0;
                var totalEng   = Number(asset.ext_engagement) || 0;
                if (totalViews <= 0) return { curr: 0, prev: 0, delta_pct: 0, launch_week: true };
                var series = iiqSynthAssetDailySeries(asset, totalViews, totalEng) || [];
                if (!series.length) return { curr: 0, prev: 0, delta_pct: 0, launch_week: true };
                var currStart = _iiqAddDaysIso(asOfIso, -6);
                var currEnd   = asOfIso;
                var prevStart = _iiqAddDaysIso(asOfIso, -13);
                var prevEnd   = _iiqAddDaysIso(asOfIso, -7);
                var curr = _iiqSumViewsInWindow(series, currStart, currEnd);
                var prev = _iiqSumViewsInWindow(series, prevStart, prevEnd);
                // Asset posted inside the current 7d window -> launch week
                var postedIso = String(asset.posted_date || '').slice(0, 10);
                var launchWeek = (!prev || prev <= 0) || (postedIso && postedIso > prevEnd);
                if (launchWeek) {
                    return { curr: Math.round(curr), prev: Math.round(prev), delta_pct: 0, launch_week: true };
                }
                var deltaPctRaw = (curr - prev) / prev * 100;
                var salt = String(asset.asset_id || asset.url || asset.action_label || 'a') + '|' + asOfIso;
                var deltaPct = _iiqPctJitter(salt, deltaPctRaw);
                return { curr: Math.round(curr), prev: Math.round(prev), delta_pct: deltaPct, launch_week: false };
            }"""

ASSET_WOW_NEW = """            // Per-asset week-over-week exposure delta. Returns:
            //   { curr, prev, delta_pct (jittered), launch_week (bool) }
            // launch_week: asset was posted inside the current window
            // (no prior-week accrual to compare against).
            //
            // Uses _iiqPitFactor (the 120-day cumulative accrual curve)
            // for both windows so this helper always agrees with the
            // Weekly Summary header WoW chip. `curr` = viewers who saw
            // the asset in the last 7 days = ext_view_count *
            // (pit_now - pit_last_week). `prev` = viewers who saw it
            // in the 7 days before that.
            //
            // Retired the iiqSynthAssetDailySeries source because its
            // renormalization over [posted..now] made mature-asset
            // tails round to zero at integer precision, which the
            // header WoW chip (also PIT-based) never suffered from.
            // Header and Trend Module now share one source of truth.
            function _iiqAssetWoWDelta(asset, asOfIso) {
                if (!asset || !asOfIso) return { curr: 0, prev: 0, delta_pct: 0, launch_week: true };
                var totalViews = Number(asset.ext_view_count) || 0;
                if (totalViews <= 0) return { curr: 0, prev: 0, delta_pct: 0, launch_week: true };
                var postedIso = String(asset.posted_date || '').slice(0, 10);
                var prevEnd = _iiqAddDaysIso(asOfIso, -7);
                var prevPrevEnd = _iiqAddDaysIso(asOfIso, -14);
                var pitNow = _iiqPitFactor(asset, asOfIso);
                var pitPrev = _iiqPitFactor(asset, prevEnd);
                var pitPrevPrev = _iiqPitFactor(asset, prevPrevEnd);
                var curr = Math.max(0, totalViews * (pitNow - pitPrev));
                var prev = Math.max(0, totalViews * (pitPrev - pitPrevPrev));
                // Launch week: prev == 0 (asset had no accrual in the
                // 7 days before the picked window), or posted inside the
                // current window.
                var launchWeek = (!prev || prev < 1) || (postedIso && postedIso > prevEnd);
                if (launchWeek) {
                    return { curr: Math.round(curr), prev: Math.round(prev), delta_pct: 0, launch_week: true };
                }
                var deltaPctRaw = (curr - prev) / prev * 100;
                var salt = String(asset.asset_id || asset.url || asset.action_label || 'a') + '|' + asOfIso;
                var deltaPct = _iiqPctJitter(salt, deltaPctRaw);
                return { curr: Math.round(curr), prev: Math.round(prev), delta_pct: deltaPct, launch_week: false };
            }"""


# --------------------------------------------------------------------------
# Splice 3: _iiqTrendTotalExposureThisWeek - swap to PIT-delta sum
# --------------------------------------------------------------------------

TREND_TOTAL_OLD = """            // Sum of PIT-adjusted views across all in-view assets over
            // the current 7-day window [as_of-6, as_of]. Used in the
            // summary strip's middle tile. Jittered so no trailing zero.
            function _iiqTrendTotalExposureThisWeek(asOfIso, slug) {
                var stash = window.__intentIQAssetsRaw;
                if (!stash || !Array.isArray(stash.cards)) return 0;
                if (!asOfIso) return 0;
                var inView = _iiqFilterAssetsByAsOf(stash.cards, asOfIso, true);
                var start = _iiqAddDaysIso(asOfIso, -6);
                var total = 0;
                for (var i = 0; i < inView.length; i++) {
                    var a = inView[i];
                    if (!a) continue;
                    var totalViews = Number(a.ext_view_count) || 0;
                    var totalEng   = Number(a.ext_engagement) || 0;
                    if (totalViews <= 0) continue;
                    var series = iiqSynthAssetDailySeries(a, totalViews, totalEng) || [];
                    total += _iiqSumViewsInWindow(series, start, asOfIso);
                }
                return _iiqCountJitter(slug || 'default', 'trend_expo_' + asOfIso, Math.round(total));
            }"""

TREND_TOTAL_NEW = """            // Sum of PIT-delta viewers across all in-view assets over
            // the current 7-day window. Uses the same _iiqPitFactor
            // step function that the Weekly Summary header WoW chip
            // uses, so this tile always agrees with the header.
            //
            // viewers_this_week = sum over in_view of
            //   ext_view_count * (pit_now - pit_prev)
            //
            // where pit_prev = _iiqPitFactor(asset, as_of - 7d). This
            // is literally the count of new viewers accrued in the
            // last 7 days per the shared accrual model.
            //
            // Retired the iiqSynthAssetDailySeries source because its
            // day-level tail rounded to zero for mature assets, which
            // made the tile read "+0 viewers" while the header still
            // read a real WoW %.
            function _iiqTrendTotalExposureThisWeek(asOfIso, slug) {
                var stash = window.__intentIQAssetsRaw;
                if (!stash || !Array.isArray(stash.cards)) return 0;
                if (!asOfIso) return 0;
                var inView = _iiqFilterAssetsByAsOf(stash.cards, asOfIso, true);
                var prev = _iiqAddDaysIso(asOfIso, -7);
                var total = 0;
                for (var i = 0; i < inView.length; i++) {
                    var a = inView[i];
                    if (!a) continue;
                    var totalViews = Number(a.ext_view_count) || 0;
                    if (totalViews <= 0) continue;
                    var pitNow = _iiqPitFactor(a, asOfIso);
                    var pitPrev = _iiqPitFactor(a, prev);
                    total += Math.max(0, totalViews * (pitNow - pitPrev));
                }
                return _iiqCountJitter(slug || 'default', 'trend_expo_' + asOfIso, Math.round(total));
            }"""


# --------------------------------------------------------------------------
# Splice 4: "organic-like" OR-check on line ~39261 - include 'official'
# so orgCount + row rendering treats studio-first-party posts as
# organic-family for count/aggregation purposes.
# --------------------------------------------------------------------------

ORG_COUNT_OLD = "                        var orgCount  = cards.filter(function(c){ return c.paid_or_organic === 'organic' || c.paid_or_organic === 'natural'; }).length;"
ORG_COUNT_NEW = "                        var orgCount  = cards.filter(function(c){ return c.paid_or_organic === 'organic' || c.paid_or_organic === 'natural' || c.paid_or_organic === 'official'; }).length;"

EDGE_COLOR_OLD = "                            var edgeColor = c.paid_or_organic === 'paid' ? '#22d3ee'\n                                          : (c.paid_or_organic === 'organic' || c.paid_or_organic === 'natural') ? '#22c55e'"
EDGE_COLOR_NEW = "                            var edgeColor = c.paid_or_organic === 'paid' ? '#22d3ee'\n                                          : c.paid_or_organic === 'official' ? '#e6b53f'\n                                          : (c.paid_or_organic === 'organic' || c.paid_or_organic === 'natural') ? '#22c55e'"


# --------------------------------------------------------------------------
def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP}  ({len(src):,} bytes)")

    src = splice(src, PALETTE_OLD, PALETTE_NEW, "palette add official")
    src = splice(src, ASSET_WOW_OLD, ASSET_WOW_NEW, "unify _iiqAssetWoWDelta to PIT")
    src = splice(src, TREND_TOTAL_OLD, TREND_TOTAL_NEW, "unify _iiqTrendTotalExposureThisWeek to PIT")
    src = splice(src, ORG_COUNT_OLD, ORG_COUNT_NEW, "include official in orgCount")
    src = splice(src, EDGE_COLOR_OLD, EDGE_COLOR_NEW, "add official edgeColor branch")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[write] {INDEX}  ({len(src):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
