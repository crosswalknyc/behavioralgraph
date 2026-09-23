#!/usr/bin/env python3
"""Two follow-on fixes for the same Alexia pass on 2026-09-23.

## 1. "Launch signal" body tile misfires post-launch

`_iiqTrendTotalExposureThisWeek` returns `launch: true` when the
prior-week accrual DELTA (`totalPrev = sum(views * (pitPrev -
pitPrevPrev))`) is < 50 panelists. That accrual delta collapses on
mature campaigns where the biggest viral posts have already peaked
and are decaying: this-week and prior-week accrual are both small
relative to lifetime views, but the campaign is 7 weeks in, not on
launch week.

Fix: pass the campaign start date into the function and only fire
`launch: true` when the prior 7-day window sits BEFORE the campaign
started. Post-launch weeks with thin accrual return `steady: true`
and the tile renders "Steady state" with a note that the week's
absolute delta is below the WoW noise threshold, instead of the
misleading "Launch signal / prior 7d has too little accrual" copy.

## 2. Cohort mover panel shows "+0.00pp" x3 when nothing moved

`_iiqComputeAudienceMoversRows` returns the top 3 audiences by
`|delta_pp|`. When every audience is within noise of 0.00pp week-
over-week (small panel, static persona composition), the top 3 all
render as "-> +0.00pp / +0.00pp / +0.00pp", which reads as "0%
growth across cohorts." The empty-state ("No audience movement in
this window.") never fires because there ARE 3 rows to render, they
just happen to all be numeric zero.

Fix: at the top of the audience-movers render, if every returned
mover has |delta_pp| < 0.05, treat the panel as empty and render the
already-defined empty-state copy. Applies pre-map so the render
path stays symmetric with the true-empty case.

Both fixes anchor on unique substrings and are idempotent.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # bg-webapp/
INDEX_HTML = HERE / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_launch_signal_and_cohort_movers_2026_09_23.html")


def _splice(src: str, old: str, new: str, desc: str) -> tuple[str, bool]:
    if new in src and old not in src:
        print(f"  [skip] {desc}: already applied")
        return src, False
    n = src.count(old)
    if n == 0:
        raise RuntimeError(
            f"[{desc}] anchor NOT FOUND\n  old={old!r}"
        )
    if n > 1:
        raise RuntimeError(
            f"[{desc}] anchor found {n}x, not unique\n  old={old!r}"
        )
    print(f"  [ok]   {desc}")
    return src.replace(old, new), True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = INDEX_HTML.read_text(encoding="utf-8")
    original_size = len(src)
    print("=== Launch-signal + cohort-mover fixes ===")
    print(f"  --dry-run: {args.dry_run}")
    print(f"  size: {original_size:,} bytes")
    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")
        print(f"  backup: {BACKUP}")
    print()

    # ------------------------------------------------------------------
    # 1. _iiqTrendTotalExposureThisWeek: add startDate, distinguish
    # true launch from post-launch thin accrual.
    # ------------------------------------------------------------------
    old_fn_sig = (
        "            function _iiqTrendTotalExposureThisWeek(asOfIso, slug) {\n"
        "                var stash = window.__intentIQAssetsRaw;\n"
        "                if (!stash || !Array.isArray(stash.cards)) return { delta: 0, prev: 0, launch: true };\n"
        "                if (!asOfIso) return { delta: 0, prev: 0, launch: true };\n"
    )
    new_fn_sig = (
        "            function _iiqTrendTotalExposureThisWeek(asOfIso, slug, startDate) {\n"
        "                var stash = window.__intentIQAssetsRaw;\n"
        "                if (!stash || !Array.isArray(stash.cards)) return { delta: 0, prev: 0, launch: true, steady: false };\n"
        "                if (!asOfIso) return { delta: 0, prev: 0, launch: true, steady: false };\n"
    )
    src, _ = _splice(src, old_fn_sig, new_fn_sig,
                     "_iiqTrendTotalExposureThisWeek: take startDate")

    old_fn_tail = (
        "                // Launch-signal floor: when the prior 7d had < 50 viewers\n"
        "                // of accrual (typical for launch-week or earliest posted\n"
        "                // assets), the absolute delta is arithmetically tiny even\n"
        "                // when the RATIO looks huge. Signal that state to the\n"
        "                // caller so the tile can show 'Launch signal' instead of\n"
        "                // a `+1 viewer` display that reads as a placeholder.\n"
        "                var launch = (totalPrev < 50);\n"
        "                return {\n"
        "                    delta: _iiqCountJitter(slug || 'default', 'trend_expo_' + asOfIso, Math.round(totalDelta)),\n"
        "                    prev:  Math.round(totalPrev),\n"
        "                    launch: launch,\n"
        "                };\n"
        "            }\n"
    )
    new_fn_tail = (
        "                // Distinguish TRUE launch (the prior 7d window sits BEFORE\n"
        "                // the campaign started) from THIN ACCRUAL (post-launch, the\n"
        "                // week's absolute accrual delta is small). Only the true\n"
        "                // case earns the 'Launch signal' label; the thin-accrual\n"
        "                // case renders as 'Steady state' + the actual delta value.\n"
        "                var prevIsoCheck = _iiqAddDaysIso(asOfIso, -7);\n"
        "                var priorWeekBeforeCampaign = !!(startDate && prevIsoCheck && prevIsoCheck < startDate);\n"
        "                var launch = priorWeekBeforeCampaign || (totalPrev < 50 && !startDate);\n"
        "                var steady = !launch && (totalPrev < 50 || Math.abs(totalDelta) < 50);\n"
        "                return {\n"
        "                    delta: _iiqCountJitter(slug || 'default', 'trend_expo_' + asOfIso, Math.round(totalDelta)),\n"
        "                    prev:  Math.round(totalPrev),\n"
        "                    launch: launch,\n"
        "                    steady: steady,\n"
        "                };\n"
        "            }\n"
    )
    src, _ = _splice(src, old_fn_tail, new_fn_tail,
                     "_iiqTrendTotalExposureThisWeek: split launch vs steady")

    # Caller: pass startDate through, and add a 'Steady' render branch.
    old_call = (
        "                var weeksLabel = _iiqTrendWeeksLabel(asOf, opening, ov);\n"
        "                var totalExpo = _iiqTrendTotalExposureThisWeek(asOf, slug);\n"
    )
    new_call = (
        "                var weeksLabel = _iiqTrendWeeksLabel(asOf, opening, ov);\n"
        "                // Campaign start = earliest phase.start_date (already the\n"
        "                // convention across the module) so 'launch signal' only\n"
        "                // fires when the prior 7d actually sits before that.\n"
        "                var _cmpStart = '';\n"
        "                try {\n"
        "                    var _phs = (ov && Array.isArray(ov.phases)) ? ov.phases : [];\n"
        "                    for (var _pi = 0; _pi < _phs.length; _pi++) {\n"
        "                        var _s = _phs[_pi] && _phs[_pi].start_date;\n"
        "                        if (_s && (!_cmpStart || _s < _cmpStart)) _cmpStart = _s;\n"
        "                    }\n"
        "                    if (!_cmpStart && ov && ov.opening_date) _cmpStart = ov.opening_date;\n"
        "                } catch(_e) {}\n"
        "                var totalExpo = _iiqTrendTotalExposureThisWeek(asOf, slug, _cmpStart);\n"
    )
    src, _ = _splice(src, old_call, new_call,
                     "trend caller: pass campaign start into totalExpo")

    old_render = (
        "                var expoTileBody, expoTileNote;\n"
        "                if (totalExpo && totalExpo.launch) {\n"
        "                    expoTileBody = '<span style=\"color:#B7B3D8; font-size:0.95rem; font-weight:600;\">Launch signal</span>';\n"
        "                    expoTileNote = 'prior 7d has too little accrual for a weekly delta yet';\n"
        "                } else {\n"
        "                    var deltaVal = (totalExpo && typeof totalExpo === 'object') ? totalExpo.delta : totalExpo;\n"
        "                    expoTileBody = '<span style=\"color:#E9E8E1; font-size:0.95rem; font-weight:600;\">+' + fmtCompact(deltaVal) + ' viewers</span>';\n"
        "                    expoTileNote = 'over ' + escapeHtml(_iiqFmtAsOfDate(_iiqAddDaysIso(asOf, -6))) + ' to ' + escapeHtml(_iiqFmtAsOfDate(asOf));\n"
        "                }\n"
    )
    new_render = (
        "                var expoTileBody, expoTileNote;\n"
        "                if (totalExpo && totalExpo.launch) {\n"
        "                    expoTileBody = '<span style=\"color:#B7B3D8; font-size:0.95rem; font-weight:600;\">Launch signal</span>';\n"
        "                    expoTileNote = 'prior 7d sits before the campaign start';\n"
        "                } else if (totalExpo && totalExpo.steady) {\n"
        "                    var deltaSteady = (totalExpo && typeof totalExpo === 'object') ? totalExpo.delta : totalExpo;\n"
        "                    var stSign = deltaSteady > 0 ? '+' : '';\n"
        "                    expoTileBody = '<span style=\"color:#B7B3D8; font-size:0.95rem; font-weight:600;\">Steady state</span>';\n"
        "                    expoTileNote = stSign + fmtCompact(deltaSteady) + ' this week, below WoW threshold';\n"
        "                } else {\n"
        "                    var deltaVal = (totalExpo && typeof totalExpo === 'object') ? totalExpo.delta : totalExpo;\n"
        "                    expoTileBody = '<span style=\"color:#E9E8E1; font-size:0.95rem; font-weight:600;\">+' + fmtCompact(deltaVal) + ' viewers</span>';\n"
        "                    expoTileNote = 'over ' + escapeHtml(_iiqFmtAsOfDate(_iiqAddDaysIso(asOf, -6))) + ' to ' + escapeHtml(_iiqFmtAsOfDate(asOf));\n"
        "                }\n"
    )
    src, _ = _splice(src, old_render, new_render,
                     "expo tile: add 'Steady state' render branch")

    # ------------------------------------------------------------------
    # 2. Cohort movers panel: fold all-zero into the empty state.
    # ------------------------------------------------------------------
    old_aud_render = (
        "                // ---- Sub-panel B: audience movers -------------------\n"
        "                var audPanelHtml;\n"
        "                if (!audMovers.length) {\n"
        "                    audPanelHtml = '<div style=\"padding: 1rem; text-align:center; color:#797F81; font-size:0.78rem;\">No audience movement in this window.</div>';\n"
        "                } else {\n"
    )
    new_aud_render = (
        "                // ---- Sub-panel B: audience movers -------------------\n"
        "                var audPanelHtml;\n"
        "                // A cohort mover set where every |delta_pp| rounds to\n"
        "                // 0.00 renders as three '\u2192 +0.00pp' rows and reads as\n"
        "                // '0% growth across cohorts', which is the same signal\n"
        "                // as the empty state. Fold both into the empty-state\n"
        "                // copy so the panel never surfaces three visibly-flat\n"
        "                // rows as if they were movers.\n"
        "                var audMoversHaveSignal = audMovers.some(function(m) { return Math.abs(Number(m.delta_pp) || 0) >= 0.05; });\n"
        "                if (!audMovers.length || !audMoversHaveSignal) {\n"
        "                    audPanelHtml = '<div style=\"padding: 1rem; text-align:center; color:#797F81; font-size:0.78rem;\">No audience movement in this window.</div>';\n"
        "                } else {\n"
    )
    src, _ = _splice(src, old_aud_render, new_aud_render,
                     "audience movers: empty-state when every |delta| < 0.05pp")

    # ------------------------------------------------------------------
    # Write + validate.
    # ------------------------------------------------------------------
    if not args.dry_run:
        INDEX_HTML.write_text(src, encoding="utf-8")
    print()
    print(f"size {original_size:,} -> {len(src):,} bytes  "
          f"(delta {len(src) - original_size:+,})")

    if not args.dry_run:
        r = subprocess.run(
            ["python3", str(HERE / "scripts" / "validate_index_html.py")],
            capture_output=True, text=True,
        )
        print()
        print("== validate_index_html.py ==")
        print(r.stdout.strip() or "(no stdout)")
        if r.returncode != 0:
            print(r.stderr.strip())
            print("[ABORT] validator failed. Restore with:")
            print(f"    cp {BACKUP} {INDEX_HTML}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
