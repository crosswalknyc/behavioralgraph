#!/usr/bin/env python3
"""Fixes for Alexia's four callouts on 2026-09-23 pre-Hades call.

1. **Identical numbers across titles.** `reloadIntentIQ()` refreshes
   overview / audiences / cohorts on title change but never invalidates
   `window.__iiqMTACache` or re-fires `iiqRenderMTA()`. Every slab that
   sources from the MTA payload (Paths, Surfaces, Attribution, Timing,
   Leaks + the KPI tiles the sufficiency card draws) keeps rendering the
   previously-loaded title's data until the browser is reloaded. Alexia
   loaded Goat first, switched to Hades, and every Attribution IQ number
   on the Hades page was still Goat's. The S3 caches are correct and
   different for both titles; the client just wasn't asking for the new
   one.

   Fix: at the top of `reloadIntentIQ()`, drop the prior slabs that got
   claimed into AIQ panels from the old title's MTA render (any
   `<section>` inside a `.aiq-panel` whose id isn't in the fixed
   ID-claim allowlist) and invalidate `window.__iiqMTACache`. Then, in
   the `.then()` after the fetches resolve, call `iiqRenderMTA()` so the
   new title's payload fetches and re-slabs. The AIQ MutationObserver
   picks up the new slabs and shelves them into panels the same way it
   does on first load.

2. **"Attribution on paid tickets only" heading.** Standing rule (Jenna
   2026-09-23): never say "bought a ticket" / "paid ticket" / "ticketing"
   in an Attribution IQ heading. Attribution IQ reads clickstream only;
   the deepest signal we observe is a checkout page reach.

   Fix: swap the four remaining film-branch strings in the paths card:

     - `cardTitle: 'Paths to ticketing.'`
        -> `'Paths to checkout page.'`
     - caption: `'... to a paid ticket, and ...'`
        -> `'... to the checkout page, and ...'`
     - `attrScope: 'on paid tickets only'`
        -> `'on checkout page reach only'`
     - `ttcToNoun: 'paid ticket'`
        -> `'checkout page reach'`

   Update `_AIQ_TABS.attribution.heads` so the head-prefix match keeps
   claiming the new h5 text into the Attribution tab:

     - `'attribution on paid tickets only'`
        -> `'attribution on checkout page reach only'`
     - `'touchpoint contribution to ticketing'`
        -> `'touchpoint contribution to '` (open prefix so any `bfl`
          variant matches: "cart / checkout", "signup", etc.)

3. **"Week 1, Launch Signal" on a 7-weeks-in campaign.**
   `_iiqTrendWeeksLabel` returns `'Week 1'` when `endDate` is empty. The
   Hades ingest has `phase.start_date = 2026-08-09` (real) but
   `phase.end_date = ''` (tickets aren't on sale yet, no known close).
   The label check `if (!startDate || !endDate || !asOfIso)` treats the
   missing end_date as "we can't compute", falls through to `tminus ||
   'Week 1'`, and tminus is empty because opening_date is also blank.

   Fix: when start_date exists and asOf exists but end_date is missing,
   compute `Week N` from start_date to as_of instead of returning
   `'Week 1'`. Reads as "Week 7 since Aug 9" for Hades today. Also
   reconciles the header WoW chip (+43.8%) with the body: the chip is
   computed from asset PIT views (correct at Week 7), and the label
   now agrees.

Every splice anchors on a unique substring so a re-run is a no-op.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # bg-webapp/
INDEX_HTML = HERE / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_alexia_fixes_2026_09_23.html")


def _splice(src: str, old: str, new: str, desc: str) -> tuple[str, bool]:
    """Splice, tolerating idempotent re-runs (new-already-present is OK)."""
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
    print(f"=== Alexia fixes 2026-09-23 ===")
    print(f"  --dry-run: {args.dry_run}")
    print(f"  index.html size: {original_size:,} bytes")
    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")
        print(f"  backup: {BACKUP}")
    print()

    # ------------------------------------------------------------------
    # 1. reloadIntentIQ: invalidate stale MTA slabs + trigger MTA
    # refetch on the new title.
    # ------------------------------------------------------------------
    #
    # Head of the function: right after `if (!slug) return;`, before the
    # status-text assignment. Insert the cleanup pass here so the user
    # sees the panels blank while the new fetches are in flight (no
    # stale numbers ever paint alongside "Loading <slug>...").
    old_head = (
        "            window.reloadIntentIQ = function() {\n"
        "                var slug = window.__intentIQ.currentSlug;\n"
        "                if (!slug) return;\n"
        "                document.getElementById('iiqStatus').textContent = 'Loading ' + slug + '...';\n"
    )
    new_head = (
        "            window.reloadIntentIQ = function() {\n"
        "                var slug = window.__intentIQ.currentSlug;\n"
        "                if (!slug) return;\n"
        "                document.getElementById('iiqStatus').textContent = 'Loading ' + slug + '...';\n"
        "                // Title change: drop every AIQ tab slab that came\n"
        "                // from the PRIOR title's MTA render, and invalidate\n"
        "                // the MTA payload cache. Otherwise Paths / Surfaces\n"
        "                // / Attribution / Timing / Leaks and the sufficiency\n"
        "                // tiles (Panelists in read, Baseline conversion,\n"
        "                // Assets in view, Cohorts, Read strength) keep\n"
        "                // rendering the previous title's data until the\n"
        "                // browser reloads. The ID-claim allowlist below is\n"
        "                // the three sections owned by their own renderers\n"
        "                // (Weekly Summary + Asset Table + Audience Table).\n"
        "                try {\n"
        "                    var _keepSlabIds = { 'iiqWeeklySummaryCard': 1, 'iiqAssetTableSection': 1, 'iiqAudienceTableSection': 1 };\n"
        "                    document.querySelectorAll('#intentIQView .aiq-panel > section').forEach(function(sec) {\n"
        "                        if (!_keepSlabIds[sec.id]) sec.remove();\n"
        "                    });\n"
        "                    if (window.__iiqMTACache) {\n"
        "                        window.__iiqMTACache = { slug: null, audience_slug: '', data: null, ts: 0 };\n"
        "                    }\n"
        "                } catch(_e) {}\n"
    )
    src, _ = _splice(src, old_head, new_head,
                     "reloadIntentIQ: clear stale AIQ slabs + invalidate MTA cache")

    # In the .then() success block, right after the metrics-caption try
    # block, kick off the MTA refetch. Anchoring on the exact tail so
    # we land immediately before the status-text success line.
    old_tail = (
        "                    try { _iiqApplyBrandLabels(); } catch(_e) {}\n"
        "                    try { _iiqApplyTopLevelTabVisibility(); } catch(_e) {}\n"
        "                    try { _iiqUpdateAssetsMetricsCaption(); } catch(_e) {}\n"
        "                    document.getElementById('iiqStatus').textContent = 'Loaded ' + slug + '.';\n"
    )
    new_tail = (
        "                    try { _iiqApplyBrandLabels(); } catch(_e) {}\n"
        "                    try { _iiqApplyTopLevelTabVisibility(); } catch(_e) {}\n"
        "                    try { _iiqUpdateAssetsMetricsCaption(); } catch(_e) {}\n"
        "                    // Re-fire the MTA fetch for the new title. The\n"
        "                    // slug-check inside iiqRenderMTA re-populates\n"
        "                    // #iiqMTAContent with the new payload; the AIQ\n"
        "                    // MutationObserver then re-shelves the fresh\n"
        "                    // slabs into their tab panels. Guarded so a\n"
        "                    // missing #iiqMTAContent (rare) doesn't break\n"
        "                    // the rest of reloadIntentIQ.\n"
        "                    try { if (typeof iiqRenderMTA === 'function') iiqRenderMTA(); } catch(_e) {}\n"
        "                    document.getElementById('iiqStatus').textContent = 'Loaded ' + slug + '.';\n"
    )
    src, _ = _splice(src, old_tail, new_tail,
                     "reloadIntentIQ: refetch MTA payload after title change")

    # ------------------------------------------------------------------
    # 2. Attribution / Paths card labels (never say "paid ticket" /
    # "ticketing" for a film branch).
    # ------------------------------------------------------------------
    src, _ = _splice(
        src,
        "                var cardTitle = isFilm ? 'Paths to ticketing.' : 'Paths to conversion.';",
        "                var cardTitle = isFilm ? 'Paths to checkout page.' : 'Paths to conversion.';",
        "paths card title: Paths to checkout page",
    )
    src, _ = _splice(
        src,
        "                    ? 'How exposed viewers moved from first campaign touch to a paid ticket, and where the drop-offs live.'",
        "                    ? 'How exposed viewers moved from first campaign touch to the checkout page, and where the drop-offs live.'",
        "paths card caption: to the checkout page",
    )
    src, _ = _splice(
        src,
        "                var attrScope = isFilm ? 'on paid tickets only' : 'on conversions only';",
        "                var attrScope = isFilm ? 'on checkout page reach only' : 'on conversions only';",
        "attribution scope: on checkout page reach only",
    )
    src, _ = _splice(
        src,
        "                var ttcToNoun = isFilm ? 'paid ticket' : escapeHtml(overallConvNoun);",
        "                var ttcToNoun = isFilm ? 'checkout page reach' : escapeHtml(overallConvNoun);",
        "ttc noun: checkout page reach",
    )

    # ------------------------------------------------------------------
    # 3. _AIQ_TABS heads: keep prefix-match in sync with new h5 texts.
    # ------------------------------------------------------------------
    old_heads = (
        "            { id: 'attribution', label: 'Attribution', heads: ['attribution on paid tickets only',\n"
        "                                                               'touchpoint contribution to ticketing'] },"
    )
    new_heads = (
        "            { id: 'attribution', label: 'Attribution', heads: ['attribution on checkout page reach only',\n"
        "                                                               'touchpoint contribution to '] },"
    )
    src, _ = _splice(src, old_heads, new_heads,
                     "_AIQ_TABS.attribution: sync head prefixes")

    # ------------------------------------------------------------------
    # 4. _iiqTrendWeeksLabel: handle empty end_date (Hades has phases[0]
    # .start_date but no end_date because tickets aren't on sale yet).
    # ------------------------------------------------------------------
    old_label = (
        "                if (!startDate || !endDate || !asOfIso) {\n"
        "                    return tminus || 'Week 1';\n"
        "                }\n"
        "                var sT = new Date(startDate + 'T12:00:00Z').getTime();\n"
        "                var eT = new Date(endDate   + 'T12:00:00Z').getTime();\n"
        "                var aT = new Date(asOfIso   + 'T12:00:00Z').getTime();\n"
        "                if (isNaN(sT) || isNaN(eT) || isNaN(aT)) return tminus || 'Week 1';\n"
        "                var totalWeeks = Math.max(1, Math.round((eT - sT) / (7 * 86400000)) + 1);\n"
        "                var curWeekRaw = Math.floor((aT - sT) / (7 * 86400000)) + 1;\n"
    )
    new_label = (
        "                if (!asOfIso || !startDate) {\n"
        "                    return tminus || 'Week 1';\n"
        "                }\n"
        "                var sT = new Date(startDate + 'T12:00:00Z').getTime();\n"
        "                var aT = new Date(asOfIso   + 'T12:00:00Z').getTime();\n"
        "                if (isNaN(sT) || isNaN(aT)) return tminus || 'Week 1';\n"
        "                // When end_date is unknown (pre-ticket-sale film, open-\n"
        "                // ended brand campaign) fall back to 'Week N since\n"
        "                // start_date' so a 7-week-old campaign doesn't read as\n"
        "                // Week 1. Full 'Week N of M' still fires when both bounds\n"
        "                // are present.\n"
        "                var eT = endDate ? new Date(endDate + 'T12:00:00Z').getTime() : NaN;\n"
        "                var totalWeeks = isNaN(eT) ? 0 : Math.max(1, Math.round((eT - sT) / (7 * 86400000)) + 1);\n"
        "                var curWeekRaw = Math.floor((aT - sT) / (7 * 86400000)) + 1;\n"
    )
    src, _ = _splice(src, old_label, new_label,
                     "_iiqTrendWeeksLabel: compute Week N when end_date is unknown")

    # And the label-string build: use the "since MMM D" form when
    # totalWeeks is 0 (unknown campaign end).
    old_wk_str = (
        "                if (curWeekRaw < 1) curWeekRaw = 1;\n"
        "                if (curWeekRaw > totalWeeks) curWeekRaw = totalWeeks;\n"
        "                var weekStr = 'Week ' + curWeekRaw + ' of ' + totalWeeks;\n"
        "                return tminus ? (tminus + ' \u00b7 ' + weekStr) : weekStr;"
    )
    new_wk_str = (
        "                if (curWeekRaw < 1) curWeekRaw = 1;\n"
        "                if (totalWeeks > 0 && curWeekRaw > totalWeeks) curWeekRaw = totalWeeks;\n"
        "                var weekStr;\n"
        "                if (totalWeeks > 0) {\n"
        "                    weekStr = 'Week ' + curWeekRaw + ' of ' + totalWeeks;\n"
        "                } else {\n"
        "                    // 'Week 7 since Aug 9' -- short month, no year, no\n"
        "                    // leading zero on the day.\n"
        "                    var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];\n"
        "                    var sD = new Date(startDate + 'T12:00:00Z');\n"
        "                    weekStr = 'Week ' + curWeekRaw + ' since ' + MON[sD.getUTCMonth()] + ' ' + sD.getUTCDate();\n"
        "                }\n"
        "                return tminus ? (tminus + ' \u00b7 ' + weekStr) : weekStr;"
    )
    src, _ = _splice(src, old_wk_str, new_wk_str,
                     "_iiqTrendWeeksLabel: 'since Aug 9' when totalWeeks unknown")

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
