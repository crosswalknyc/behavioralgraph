#!/usr/bin/env python3
"""Rewire the cohort-mover panel so it surfaces new viewers reached this
week (a signal that moves when creators drop new assets), not just
response-rate movement (which stays roughly flat when new drops perform
at the same rate as older ones).

The current code ranks the top 3 audiences by |response_pct delta week
over week|. `response_pct` = campaign-weighted mean response x per-audience
tilt. The tilt is deterministic per audience, so response_pct only shifts
when the CAMPAIGN mean shifts. When new creator drops perform at rates
similar to the existing asset mix, the mean is stable and every audience
shows near-zero pp delta -- even though 25 creators may have dropped in
the prior week and cumulative exposure jumped +49%. That was the
disconnect Alexia saw: the header pill moved big, the cohort panel read
'no movement.'

Fix: rank by |delta in projected reached viewers| for each audience
week over week. That signal moves whenever:

  - new assets enter the in-view set (raising inViewN and the overlap
    fraction against the total universe)
  - existing assets have accrued more PIT views by this week's cursor

so a wave of creator drops shows as a positive delta on every audience
they touched, sized by the audience's overlap share. Direction still
flips negative for audiences whose exposed share shrinks (e.g. a phase
transition that retires assets from view).

Retains the empty-state fold from the earlier fix: if every mover is
within noise (< 3,000 new-reached viewers), the panel still folds to
'No audience movement in this window.' That threshold picks up real
drop waves while filtering pure jitter.

Also renames the panel eyebrow from 'Audiences leaning in (or out)
this week' -> 'Audiences reached this week (net new)' so the metric
matches the label.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # bg-webapp/
INDEX_HTML = HERE / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_cohort_mover_metric_2026_09_23.html")


def _splice(src, old, new, desc):
    if new in src and old not in src:
        print(f"  [skip] {desc}: already applied")
        return src
    n = src.count(old)
    if n == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND\n  old={old!r}")
    if n > 1:
        raise RuntimeError(f"[{desc}] anchor found {n}x\n  old={old!r}")
    print(f"  [ok]   {desc}")
    return src.replace(old, new)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = INDEX_HTML.read_text(encoding="utf-8")
    original_size = len(src)
    print(f"=== Cohort mover metric: response_pct -> new reached ===")
    print(f"  --dry-run: {args.dry_run}")
    print(f"  size: {original_size:,} bytes")
    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")
        print(f"  backup: {BACKUP}")
    print()

    # ------------------------------------------------------------------
    # 1. Change the mover struct to carry new-reached delta and rank by
    # that instead of response_pct delta.
    # ------------------------------------------------------------------
    old_compute = (
        "                for (var j = 0; j < currRows.length; j++) {\n"
        "                    var c = currRows[j];\n"
        "                    var p = prevBySubj[c.subject_key];\n"
        "                    var currPct = Number(c.response_pct) || 0;\n"
        "                    var prevPct = p ? (Number(p.response_pct) || 0) : 0;\n"
        "                    var deltaPp = currPct - prevPct;\n"
        "                    var salt = String(c.subject_key || 'aud') + '|pp|' + asOfIso;\n"
        "                    var deltaPpJit = _iiqPctJitter(salt, deltaPp * 10) / 10; // preserve 0.01pp precision\n"
        "                    var launchWeek = (prevPct <= 0 && currPct > 0);\n"
        "                    out.push({\n"
        "                        subject_key: c.subject_key,\n"
        "                        display: c.display,\n"
        "                        category: c.category,\n"
        "                        fit: c.fit,\n"
        "                        curr_pct: currPct,\n"
        "                        prev_pct: prevPct,\n"
        "                        delta_pp: deltaPpJit,\n"
        "                        use_info: c.use_info,\n"
        "                        launch_week: launchWeek,\n"
        "                        rank_score: Math.abs(deltaPpJit)\n"
        "                    });\n"
        "                }\n"
        "                out.sort(function(a, b) { return b.rank_score - a.rank_score; });\n"
        "                return out.slice(0, 3);\n"
        "            }\n"
    )
    new_compute = (
        "                for (var j = 0; j < currRows.length; j++) {\n"
        "                    var c = currRows[j];\n"
        "                    var p = prevBySubj[c.subject_key];\n"
        "                    // Response rate delta -- kept as a secondary field on the\n"
        "                    // struct so drill-in surfaces can still read it, but no\n"
        "                    // longer the primary ranking signal (see script header).\n"
        "                    var currPct = Number(c.response_pct) || 0;\n"
        "                    var prevPct = p ? (Number(p.response_pct) || 0) : 0;\n"
        "                    var deltaPp = currPct - prevPct;\n"
        "                    var salt = String(c.subject_key || 'aud') + '|pp|' + asOfIso;\n"
        "                    var deltaPpJit = _iiqPctJitter(salt, deltaPp * 10) / 10;\n"
        "                    // Projected reached viewers per cohort at each cursor.\n"
        "                    // Moves when new assets enter the in-view set or when\n"
        "                    // existing assets accrue more PIT views by this week's\n"
        "                    // cursor -- so a creator drop wave shows as positive\n"
        "                    // delta on every audience it touched.\n"
        "                    var currReached = Number(c.reached) || 0;\n"
        "                    var prevReached = p ? (Number(p.reached) || 0) : 0;\n"
        "                    var deltaReached = currReached - prevReached;\n"
        "                    var launchWeek = (prevReached <= 0 && currReached > 0);\n"
        "                    out.push({\n"
        "                        subject_key: c.subject_key,\n"
        "                        display: c.display,\n"
        "                        category: c.category,\n"
        "                        fit: c.fit,\n"
        "                        curr_pct: currPct,\n"
        "                        prev_pct: prevPct,\n"
        "                        delta_pp: deltaPpJit,\n"
        "                        curr_reached: currReached,\n"
        "                        prev_reached: prevReached,\n"
        "                        delta_reached: Math.round(deltaReached),\n"
        "                        use_info: c.use_info,\n"
        "                        launch_week: launchWeek,\n"
        "                        rank_score: Math.abs(deltaReached)\n"
        "                    });\n"
        "                }\n"
        "                out.sort(function(a, b) { return b.rank_score - a.rank_score; });\n"
        "                return out.slice(0, 3);\n"
        "            }\n"
    )
    src = _splice(src, old_compute, new_compute,
                  "_iiqComputeAudienceMoversRows: rank by delta_reached")

    # ------------------------------------------------------------------
    # 2. Update the panel render to display the new-reached count with
    # the appropriate arrow, and swap the empty-state threshold to
    # 3,000 new-reached viewers.
    # ------------------------------------------------------------------
    old_render_delta = (
        "                        var arrow, deltaColor;\n"
        "                        if (m.delta_pp > 0)      { arrow = '\u25B2'; deltaColor = '#C7F23E'; }\n"
        "                        else if (m.delta_pp < 0) { arrow = '\u25BC'; deltaColor = '#f87171'; }\n"
        "                        else                     { arrow = '\u2192'; deltaColor = '#B7B3D8'; }\n"
        "                        var deltaSign = m.delta_pp > 0 ? '+' : '';\n"
        "                        var deltaBlock = '<div style=\"flex:0 0 auto; text-align:right; font-size:0.78rem; font-weight:700; color:' + deltaColor + '; white-space:nowrap;\">'\n"
        "                            + arrow + ' ' + deltaSign + m.delta_pp.toFixed(2) + 'pp'\n"
        "                            + '</div>';\n"
    )
    new_render_delta = (
        "                        // Ranked and colored by delta in projected reached\n"
        "                        // viewers week over week (matches the ranking above).\n"
        "                        var _dr = Number(m.delta_reached) || 0;\n"
        "                        var arrow, deltaColor;\n"
        "                        if (_dr > 0)      { arrow = '\u25B2'; deltaColor = '#C7F23E'; }\n"
        "                        else if (_dr < 0) { arrow = '\u25BC'; deltaColor = '#f87171'; }\n"
        "                        else              { arrow = '\u2192'; deltaColor = '#B7B3D8'; }\n"
        "                        var deltaSign = _dr > 0 ? '+' : (_dr < 0 ? '-' : '');\n"
        "                        var deltaBlock = '<div style=\"flex:0 0 auto; text-align:right; font-size:0.78rem; font-weight:700; color:' + deltaColor + '; white-space:nowrap;\">'\n"
        "                            + arrow + ' ' + deltaSign + fmtCompact(Math.abs(_dr)) + ' reached'\n"
        "                            + '</div>';\n"
    )
    src = _splice(src, old_render_delta, new_render_delta,
                  "audience-mover row: render new-reached delta instead of pp")

    # Swap the metric label under the delta block.
    old_metric = (
        "                        var metricBlock = '<div style=\"flex:0 0 auto; text-align:right; font-size:0.62rem; color:#797F81; text-transform:uppercase; letter-spacing:0.06em; margin-left:0.4rem;\">'\n"
        "                            + escapeHtml(primaryMetricLabel)\n"
        "                            + '</div>';\n"
    )
    new_metric = (
        "                        // Metric label. Shows the response rate (info-seek\n"
        "                        // or ticketing) that the CURRENT week reads for this\n"
        "                        // cohort as secondary context, so the primary count\n"
        "                        // delta above and the rate metric below reinforce.\n"
        "                        var _rateNow = Number(m.curr_pct) || 0;\n"
        "                        var metricBlock = '<div style=\"flex:0 0 auto; text-align:right; font-size:0.62rem; color:#797F81; text-transform:uppercase; letter-spacing:0.06em; margin-left:0.4rem;\">'\n"
        "                            + _rateNow.toFixed(2) + '% ' + escapeHtml(primaryMetricLabel)\n"
        "                            + '</div>';\n"
    )
    src = _splice(src, old_metric, new_metric,
                  "audience-mover row: metric label carries rate + label")

    # Swap the empty-state threshold from |delta_pp| < 0.05 to
    # |delta_reached| < 3000 (people).
    old_empty = (
        "                var audMoversHaveSignal = audMovers.some(function(m) { return Math.abs(Number(m.delta_pp) || 0) >= 0.05; });\n"
    )
    new_empty = (
        "                // Empty-state threshold in projected NEW-reached viewers\n"
        "                // per cohort (3,000). Picks up a drop wave that adds ~10K+\n"
        "                // reached across its exposed audiences; filters pure jitter.\n"
        "                var audMoversHaveSignal = audMovers.some(function(m) { return Math.abs(Number(m.delta_reached) || 0) >= 3000; });\n"
    )
    src = _splice(src, old_empty, new_empty,
                  "audience-mover empty state: |delta_reached| threshold")

    # Rename the panel eyebrow.
    old_eyebrow = (
        "                var audPanel = '<div style=\"flex:1 1 50%; min-width: 280px;\">'\n"
        "                    + '<div style=\"font-size:0.6rem; font-weight:600; color:#9AA09B; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:0.45rem;\">Audiences leaning in (or out) this week</div>'\n"
        "                    + audPanelHtml\n"
        "                    + '</div>';\n"
    )
    new_eyebrow = (
        "                var audPanel = '<div style=\"flex:1 1 50%; min-width: 280px;\">'\n"
        "                    + '<div style=\"font-size:0.6rem; font-weight:600; color:#9AA09B; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:0.45rem;\">Audiences reached this week (net new)</div>'\n"
        "                    + audPanelHtml\n"
        "                    + '</div>';\n"
    )
    src = _splice(src, old_eyebrow, new_eyebrow,
                  "audience-mover panel eyebrow: reached this week (net new)")

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
