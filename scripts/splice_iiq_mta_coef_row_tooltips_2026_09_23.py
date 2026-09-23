#!/usr/bin/env python3
"""Attribution IQ - MTA touchpoint contribution card: hover tooltips.

Adds a native HTML title tooltip on every column header AND every
per-row object on the touchpoint contribution card. Follow-up to
the row spacing fix (splice_iiq_mta_coef_row_spacing_2026_09_23.py).

Tooltips added:

  Column headers
    - Touchpoint
    - Lift shape
    - Odds ratio (95% CI)
    - Read strength

  Per-row objects
    - Asset title (kept: already carries a title attr for truncation)
    - Channel + phase subtitle
    - Bar cell (lift shape)
    - OR text (odds ratio + CI)
    - "not significant" pill (when present)
    - Read strength chip (Strong / Moderate / Weak)

Read-strength tooltip lives on the wrapping cell (not inside
_iiqMTASigChip), because _iiqMTASigChip is also called by
_iiqMTAQualityChip on a different card - teaching the helper about
read-strength tooltips would surface the wrong wording ("Strong
read...") on a "Strong fit" chip elsewhere. Keeping the tip on the
row wrapper isolates the change to this card only.

Voice: no em dashes, plain sentences, statistical vocabulary that
already appears on the card (coefficient, odds ratio, 95% CI). Tier 1
measured values per analysis-confidence-calibration.mdc.

Safety: byte-level Python splices per index-html-safety.mdc; unique
anchor blocks; validator run at the end.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_mta_coef_tooltips_2026_09_23.html")
VALIDATOR = REPO_ROOT / "scripts" / "validate_index_html.py"

# -----------------------------------------------------------------------
# Splice A: chart header - add title on each of 4 header cells.
# -----------------------------------------------------------------------
OLD_HEAD = (
    "                var chartHead =\n"
    "                    '<div style=\"display:grid; grid-template-columns: 240px 1fr 240px 100px; gap:0.9rem; padding: 0 0 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary);\">'\n"
    "                  + '  <div>Touchpoint</div>'\n"
    "                  + '  <div style=\"text-align:center;\">Lift shape</div>'\n"
    "                  + '  <div style=\"text-align:right;\">Odds ratio (95% CI)</div>'\n"
    "                  + '  <div style=\"text-align:right;\">Read strength</div>'\n"
    "                  + '</div>';\n"
)

NEW_HEAD = (
    "                var chartHead =\n"
    "                    '<div style=\"display:grid; grid-template-columns: 240px 1fr 240px 100px; gap:0.9rem; padding: 0 0 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary);\">'\n"
    "                  + '  <div title=\"The asset, keyword, or surface that appeared in the exposure log. Line 1 is the asset title. Line 2 is the channel and the phase (T-N is N weeks before opening).\">Touchpoint</div>'\n"
    "                  + '  <div style=\"text-align:center;\" title=\"Visual size of the touchpoint effect on conversion. Bar to the right is a lift, bar to the left is a drag. Length is proportional to the coefficient magnitude.\">Lift shape</div>'\n"
    "                  + '  <div style=\"text-align:right;\" title=\"The lift as an odds ratio. 1.00 is no effect. 1.10 means an exposed viewer is 10 percent more likely to convert than a matched control. The 95 percent CI is the range we would defend.\">Odds ratio (95% CI)</div>'\n"
    "                  + '  <div style=\"text-align:right;\" title=\"How much weight to put on this row. Strong is a tight CI clear of 1.00. Moderate is one-sided but narrow. Weak is small or the CI straddles 1.00.\">Read strength</div>'\n"
    "                  + '</div>';\n"
)

# -----------------------------------------------------------------------
# Splice B: row template - add title on subtitle, bar cell, OR text,
# notSigChip; compute sigTip and wrap sig chip cell with it.
# -----------------------------------------------------------------------
OLD_ROW = (
    "                    // \"not significant\" moves to a SECOND LINE under the OR text\n"
    "                    // (block-level, right-aligned) so it never collides with the\n"
    "                    // Read-strength chip in the next grid cell. See\n"
    "                    // splice_iiq_mta_coef_row_spacing_2026_09_23.py.\n"
    "                    var notSigChip = '';\n"
    "                    if (isFinite(orLoRaw) && isFinite(orHiRaw) && orLoRaw <= 1.0 && 1.0 <= orHiRaw) {\n"
    "                        notSigChip = '<div style=\"margin-top:0.15rem;\"><span style=\"display:inline-block; padding:1px 6px; font-size:0.6rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; color:#7C878A; border:1px solid rgba(124,135,138,0.35);\">not significant</span></div>';\n"
    "                    }\n"
    "                    return (\n"
    "                        '<div style=\"display:grid; grid-template-columns: 240px 1fr 240px 100px; gap:0.9rem; align-items:center; padding:0.35rem 0;\">'\n"
    "                      + '  <div style=\"min-width:0;\">'\n"
    "                      + '    <div style=\"font-size:0.78rem; color: var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;\" title=\"' + escapeHtml(r.asset_title) + '\">' + escapeHtml(r.asset_title) + '</div>'\n"
    "                      + '    <div style=\"font-size:0.66rem; color: var(--text-secondary); margin-top:0.1rem;\">' + escapeHtml(r.channel) + ' \\u00b7 ' + escapeHtml(r.phase || '') + '</div>'\n"
    "                      + '  </div>'\n"
    "                      + '  <div style=\"display:flex; align-items:center; background: rgba(255,255,255,0.02); border-radius:9999px; padding:2px 6px;\">' + leftHalf + centerRule + rightHalf + '</div>'\n"
    "                      + '  <div style=\"text-align:right;\">'\n"
    "                      + '    <div style=\"font-size:0.75rem; color: var(--text-primary); font-variant-numeric: tabular-nums; white-space:nowrap;\">' + orText + '</div>'\n"
    "                      +      notSigChip\n"
    "                      + '  </div>'\n"
    "                      + '  <div style=\"text-align:right;\">' + _iiqMTASigChip(r.significance) + '</div>'\n"
    "                      + '</div>'\n"
    "                    );\n"
    "                }\n"
)

NEW_ROW = (
    "                    // \"not significant\" moves to a SECOND LINE under the OR text\n"
    "                    // (block-level, right-aligned) so it never collides with the\n"
    "                    // Read-strength chip in the next grid cell. See\n"
    "                    // splice_iiq_mta_coef_row_spacing_2026_09_23.py. Tooltip\n"
    "                    // added per splice_iiq_mta_coef_row_tooltips_2026_09_23.py.\n"
    "                    var notSigChip = '';\n"
    "                    if (isFinite(orLoRaw) && isFinite(orHiRaw) && orLoRaw <= 1.0 && 1.0 <= orHiRaw) {\n"
    "                        notSigChip = '<div style=\"margin-top:0.15rem;\" title=\"The 95 percent CI includes 1.00, so we cannot rule out no effect. Read directionally, not as a hard lift.\"><span style=\"display:inline-block; padding:1px 6px; font-size:0.6rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; color:#7C878A; border:1px solid rgba(124,135,138,0.35);\">not significant</span></div>';\n"
    "                    }\n"
    "                    // Per-row tooltip for the read-strength chip. Kept on the\n"
    "                    // wrapping cell so _iiqMTASigChip stays context-free (it is\n"
    "                    // also called by _iiqMTAQualityChip on a different card).\n"
    "                    var sigTip = { strong: 'Strong read. The 95 percent CI is tight and clearly excludes 1.00.', moderate: 'Moderate read. The CI is on one side of 1.00 but not far from it.', weak: 'Weak read. The effect is small or the CI straddles 1.00.' }[r.significance] || 'Weak read. The effect is small or the CI straddles 1.00.';\n"
    "                    return (\n"
    "                        '<div style=\"display:grid; grid-template-columns: 240px 1fr 240px 100px; gap:0.9rem; align-items:center; padding:0.35rem 0;\">'\n"
    "                      + '  <div style=\"min-width:0;\">'\n"
    "                      + '    <div style=\"font-size:0.78rem; color: var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;\" title=\"' + escapeHtml(r.asset_title) + '\">' + escapeHtml(r.asset_title) + '</div>'\n"
    "                      + '    <div style=\"font-size:0.66rem; color: var(--text-secondary); margin-top:0.1rem;\" title=\"Channel is the surface (TikTok, Instagram, YouTube, Google Search). Phase is when this asset was live: T-N is N weeks before opening, T-0 is opening week.\">' + escapeHtml(r.channel) + ' \\u00b7 ' + escapeHtml(r.phase || '') + '</div>'\n"
    "                      + '  </div>'\n"
    "                      + '  <div title=\"Signed coefficient magnitude. Green is a lift on conversion. Orchid is a drag. Gray is weak or not significant. Bar length is proportional to the raw coefficient, not the odds ratio.\" style=\"display:flex; align-items:center; background: rgba(255,255,255,0.02); border-radius:9999px; padding:2px 6px;\">' + leftHalf + centerRule + rightHalf + '</div>'\n"
    "                      + '  <div style=\"text-align:right;\">'\n"
    "                      + '    <div style=\"font-size:0.75rem; color: var(--text-primary); font-variant-numeric: tabular-nums; white-space:nowrap;\" title=\"Odds ratio point estimate with 95 percent confidence interval. 1.00 is no effect.\">' + orText + '</div>'\n"
    "                      +      notSigChip\n"
    "                      + '  </div>'\n"
    "                      + '  <div style=\"text-align:right;\" title=\"' + sigTip + '\">' + _iiqMTASigChip(r.significance) + '</div>'\n"
    "                      + '</div>'\n"
    "                    );\n"
    "                }\n"
)


def splice(src: str, old: str, new: str, label: str) -> str:
    n = src.count(old)
    if n == 0:
        raise SystemExit(f"[fail] {label}: anchor NOT FOUND")
    if n > 1:
        raise SystemExit(f"[fail] {label}: anchor found {n} times (must be unique)")
    print(f"  [ok]   {label}")
    return src.replace(old, new)


def main() -> int:
    ap = argparse.ArgumentParser(description="MTA coef card - column + per-row hover tooltips")
    ap.add_argument("--dry-run", action="store_true", help="report splices but do not write")
    args = ap.parse_args()

    print("=== MTA coefficient card: hover tooltips ===")
    print(f"  --dry-run: {args.dry_run}")
    src = INDEX.read_text(encoding="utf-8")
    print(f"  size: {len(src):,} bytes")
    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")
        print(f"  backup: {BACKUP}")
    print()

    src2 = splice(src, OLD_HEAD, NEW_HEAD, "chart header: title tooltip on each of 4 header cells")
    src2 = splice(src2, OLD_ROW, NEW_ROW, "row template: title tooltip on subtitle, bar, OR text, not-sig pill, read-strength chip")

    print()
    print(f"size {len(src):,} -> {len(src2):,} bytes  (delta {len(src2) - len(src):+,})")

    if args.dry_run:
        print("\n(dry-run; not writing)")
        return 0

    INDEX.write_text(src2, encoding="utf-8")

    if VALIDATOR.exists():
        print("\n== validate_index_html.py ==")
        r = subprocess.run([sys.executable, str(VALIDATOR)], cwd=REPO_ROOT)
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
