#!/usr/bin/env python3
"""Add ``theme`` to the weekly-PDF payload the frontend POSTs.

Small byte-level splice on ``templates/index.html`` per
``index-html-safety.mdc`` (StrReplace corrupts files > ~8 MB). We
insert a single new field into the object returned by
``_iiqBuildWeeklyPdfPayload``, keyed off ``document.body.dataset.theme``
so the dashboard's current theme (dark by default, or light if the
user has flipped it) selects which PDF variant renders.

Idempotent: re-running is a no-op after the first apply. The script
validates ``templates/index.html`` after splicing.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

BG_ROOT = Path(__file__).resolve().parents[1]
INDEX = BG_ROOT / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_weekly_pdf_theme.html")
VALIDATOR = BG_ROOT / "scripts" / "validate_index_html.py"


OLD = """                return {
                    title: {
                        display_name: displayName,
                        distributor: distributor,
                        opening_date: opening,
                    },
                    as_of: asOf,
                    week_start: asOfMinus7,
                    week_end: asOf,
                    days_to_open: daysToOpen,
                    phase_label: phaseLabel,
                    snapshot: {
                        exposed_viewers: Math.max(0, Math.round(totalNow)),
                        exposed_delta_pct: (exposedDelta != null) ? Number(exposedDelta.toFixed(2)) : null,
                        response_rate_pct: (responseRateNow != null) ? Number(responseRateNow.toFixed(2)) : null,
                        response_delta_pct: (responseDelta != null) ? Number(responseDelta.toFixed(2)) : null,
                        response_metric_label: responseLabel,
                        sample_size: sampleSize,
                    },
                    bullets: bullets,
                    top_assets: topAssets,
                    top_audiences: topAudiences,
                };
            }"""


NEW = """                // Theme mirrors the on-screen dashboard so the downloaded
                // PDF reads as a continuation of the surface the user is
                // looking at. Dashboard defaults to dark; if the user has
                // flipped light mode via the theme toggle,
                // document.body.dataset.theme reads 'light' and the
                // backend routes to the Off-White portrait variant.
                var _iiqPdfTheme = 'dark';
                try {
                    var _iiqBodyTheme = (document && document.body
                        && document.body.dataset && document.body.dataset.theme) || '';
                    if (String(_iiqBodyTheme).toLowerCase() === 'light') {
                        _iiqPdfTheme = 'light';
                    }
                } catch (e) { /* keep dark */ }

                return {
                    theme: _iiqPdfTheme,
                    title: {
                        display_name: displayName,
                        distributor: distributor,
                        opening_date: opening,
                    },
                    as_of: asOf,
                    week_start: asOfMinus7,
                    week_end: asOf,
                    days_to_open: daysToOpen,
                    phase_label: phaseLabel,
                    snapshot: {
                        exposed_viewers: Math.max(0, Math.round(totalNow)),
                        exposed_delta_pct: (exposedDelta != null) ? Number(exposedDelta.toFixed(2)) : null,
                        response_rate_pct: (responseRateNow != null) ? Number(responseRateNow.toFixed(2)) : null,
                        response_delta_pct: (responseDelta != null) ? Number(responseDelta.toFixed(2)) : null,
                        response_metric_label: responseLabel,
                        sample_size: sampleSize,
                    },
                    bullets: bullets,
                    top_assets: topAssets,
                    top_audiences: topAudiences,
                };
            }"""


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    orig_len = len(src)
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP} ({orig_len:,} chars)")

    if "theme: _iiqPdfTheme," in src:
        print("[splice] theme already wired into weekly-pdf payload; no-op.")
        return 0

    count = src.count(OLD)
    if count == 0:
        print("[splice] ERROR: anchor NOT FOUND. index.html may already be "
              "patched or the return-block was refactored. Aborting.",
              file=sys.stderr)
        return 2
    if count > 1:
        print(f"[splice] ERROR: anchor found {count}x; not unique. Aborting.",
              file=sys.stderr)
        return 2

    src = src.replace(OLD, NEW)
    INDEX.write_text(src, encoding="utf-8")
    final_len = len(src)
    print(f"[write] {INDEX} ({final_len:,} chars, net delta {final_len - orig_len:+,})")

    if VALIDATOR.is_file():
        rc = subprocess.call([sys.executable, str(VALIDATOR)])
        if rc != 0:
            print("[validate] FAILED. Reverting from backup.", file=sys.stderr)
            INDEX.write_text(BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
