#!/usr/bin/env python3
"""Fix the WoW `Total exposure this week` tile so it stops showing
`+1 viewer` while the header chip reads `+56.2% WoW`.

Root cause: header chip and the tile both use the same PIT-factor
accrual math (`ext_view_count * (pit_now - pit_prev)`), but the header
returns a RATIO `(totalNow - totalPrev) / totalPrev * 100` and the
tile returns the ABSOLUTE DELTA in viewers. When `totalPrev` is very
small (a few viewers from assets that were barely posted 14 days
ago), the ratio can be a huge percentage while the absolute count is
close to zero. That is a real math-vs-display divergence in the tile,
independent of the -23 regeneration and the checkout-page relabel.

Fix: gate the tile on a totalPrev floor. When the prior 7d had fewer
than a small threshold of viewers (typical for launch-week / earliest
posted assets), show `Launch signal` instead of `+N viewers`.
Otherwise render the absolute delta as before.

Threshold: 50 viewers of prior-week accrual. Set low enough that any
campaign past its first two weeks shows the absolute delta as
intended, but high enough to short-circuit the divide-by-tiny case
that produced `+1 viewer`.

Standing rules honored:
  * no-em-dashes.mdc: no em dashes.
  * no-modeled-or-source-language.mdc: `Launch signal` is neutral
    observation copy, not a hedge.
  * index-html-safety.mdc: byte-level Python splice, unique anchor.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent  # bg-webapp/
INDEX_HTML = HERE / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_wow_tile_floor.html")

# The function that computes the tile value.  Rewritten so it also
# returns the prior-week total; the caller uses that to decide whether
# to show the launch-signal string or the absolute delta.
OLD_FN = """function _iiqTrendTotalExposureThisWeek(asOfIso, slug) {
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

NEW_FN = """function _iiqTrendTotalExposureThisWeek(asOfIso, slug) {
                var stash = window.__intentIQAssetsRaw;
                if (!stash || !Array.isArray(stash.cards)) return { delta: 0, prev: 0, launch: true };
                if (!asOfIso) return { delta: 0, prev: 0, launch: true };
                var inView = _iiqFilterAssetsByAsOf(stash.cards, asOfIso, true);
                var prevIso = _iiqAddDaysIso(asOfIso, -7);
                var prevPrevIso = _iiqAddDaysIso(asOfIso, -14);
                var totalDelta = 0;
                var totalPrev = 0;
                for (var i = 0; i < inView.length; i++) {
                    var a = inView[i];
                    if (!a) continue;
                    var totalViews = Number(a.ext_view_count) || 0;
                    if (totalViews <= 0) continue;
                    var pitNow = _iiqPitFactor(a, asOfIso);
                    var pitPrev = _iiqPitFactor(a, prevIso);
                    var pitPrevPrev = _iiqPitFactor(a, prevPrevIso);
                    totalDelta += Math.max(0, totalViews * (pitNow - pitPrev));
                    totalPrev  += Math.max(0, totalViews * (pitPrev - pitPrevPrev));
                }
                // Launch-signal floor: when the prior 7d had < 50 viewers
                // of accrual (typical for launch-week or earliest posted
                // assets), the absolute delta is arithmetically tiny even
                // when the RATIO looks huge. Signal that state to the
                // caller so the tile can show 'Launch signal' instead of
                // a `+1 viewer` display that reads as a placeholder.
                var launch = (totalPrev < 50);
                return {
                    delta: _iiqCountJitter(slug || 'default', 'trend_expo_' + asOfIso, Math.round(totalDelta)),
                    prev:  Math.round(totalPrev),
                    launch: launch,
                };
            }"""

# The call site that renders the tile. Rewritten to consume the new
# return shape and gate on the launch flag.
OLD_TILE = """                var expoTile = tile('Total exposure this week',
                    '<span style="color:#E9E8E1; font-size:0.95rem; font-weight:600;">+' + fmtCompact(totalExpo) + ' viewers</span>',
                    'over ' + escapeHtml(_iiqFmtAsOfDate(_iiqAddDaysIso(asOf, -6))) + ' to ' + escapeHtml(_iiqFmtAsOfDate(asOf)));"""

NEW_TILE = """                var expoTileBody, expoTileNote;
                if (totalExpo && totalExpo.launch) {
                    expoTileBody = '<span style="color:#B7B3D8; font-size:0.95rem; font-weight:600;">Launch signal</span>';
                    expoTileNote = 'prior 7d has too little accrual for a weekly delta yet';
                } else {
                    var deltaVal = (totalExpo && typeof totalExpo === 'object') ? totalExpo.delta : totalExpo;
                    expoTileBody = '<span style="color:#E9E8E1; font-size:0.95rem; font-weight:600;">+' + fmtCompact(deltaVal) + ' viewers</span>';
                    expoTileNote = 'over ' + escapeHtml(_iiqFmtAsOfDate(_iiqAddDaysIso(asOf, -6))) + ' to ' + escapeHtml(_iiqFmtAsOfDate(asOf));
                }
                var expoTile = tile('Total exposure this week', expoTileBody, expoTileNote);"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    src = INDEX_HTML.read_text(encoding="utf-8")
    original_size = len(src)

    if src.count(OLD_FN) != 1:
        print(f"[abort] _iiqTrendTotalExposureThisWeek anchor: "
              f"{src.count(OLD_FN)} matches (want 1)")
        return 1
    if src.count(OLD_TILE) != 1:
        print(f"[abort] expoTile anchor: {src.count(OLD_TILE)} matches (want 1)")
        return 1

    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")

    src = src.replace(OLD_FN, NEW_FN)
    src = src.replace(OLD_TILE, NEW_TILE)

    if not args.dry_run:
        INDEX_HTML.write_text(src, encoding="utf-8")

    print(f"[splice] _iiqTrendTotalExposureThisWeek + expoTile call site")
    print(f"[size]   {original_size:,} -> {len(src):,} bytes  "
          f"(delta {len(src) - original_size:+,})")
    print(f"[backup] {BACKUP}")

    if args.dry_run:
        print("[dry-run] no write")
        return 0

    r = subprocess.run(
        ["python3", str(HERE / "scripts" / "validate_index_html.py")],
        capture_output=True, text=True,
    )
    print()
    print("== validate_index_html.py ==")
    print(r.stdout.strip() or "(no stdout)")
    if r.returncode != 0:
        print(r.stderr.strip())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
