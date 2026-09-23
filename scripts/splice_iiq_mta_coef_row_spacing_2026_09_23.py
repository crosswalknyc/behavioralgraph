#!/usr/bin/env python3
"""Attribution IQ - MTA touchpoint contribution row spacing fix.

Defect surfaced on GOAT: the "NOT SIGNIFICANT" pill and the
Read-strength "WEAK" pill collide on the right edge of every
non-significant row. Root cause: the "not significant" pill lives
inside the odds-ratio cell (right-aligned, white-space:nowrap), so
when the CI text is at full width the pill pushes to the right
edge and sits within the tiny 0.6rem gap that separates it from the
Read-strength cell. On the OR-only cases the two pills read as one
glued blob.

Fix:
  1. Widen the OR cell 220px -> 240px, and lift the inter-column gap
     0.6rem -> 0.9rem so the Read-strength chip has visible breathing
     room.
  2. Move the "not significant" pill onto a SECOND LINE under the OR
     text (block-level, right-aligned, small top margin). Reads
     naturally: number on line 1, qualifier on line 2, same shape
     as the subtitle under the touchpoint name.
  3. Header row uses the same widened grid + gap so the column
     headers still align over their columns.

Zero API/data change. Pure CSS/HTML shape swap.

Safety: templates/index.html is ~11.4 MB; StrReplace silently
truncates past ~8 MB. This uses Python byte-level splices per
`.cursor/rules/index-html-safety.mdc` with unique anchor blocks,
plus a validator run at the end.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_mta_coef_spacing_2026_09_23.html")
VALIDATOR = REPO_ROOT / "scripts" / "validate_index_html.py"

# -----------------------------------------------------------------------
# Splice A: row template (grid widths, gap, and split of OR + notSigChip).
# -----------------------------------------------------------------------
OLD_ROW = (
    "                    var notSigChip = '';\n"
    "                    if (isFinite(orLoRaw) && isFinite(orHiRaw) && orLoRaw <= 1.0 && 1.0 <= orHiRaw) {\n"
    "                        notSigChip = ' <span style=\"display:inline-block; margin-left:0.35rem; padding:1px 6px; font-size:0.6rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; color:#7C878A; border:1px solid rgba(124,135,138,0.35);\">not significant</span>';\n"
    "                    }\n"
    "                    return (\n"
    "                        '<div style=\"display:grid; grid-template-columns: 240px 1fr 220px 90px; gap:0.6rem; align-items:center; padding:0.35rem 0;\">'\n"
    "                      + '  <div style=\"min-width:0;\">'\n"
    "                      + '    <div style=\"font-size:0.78rem; color: var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;\" title=\"' + escapeHtml(r.asset_title) + '\">' + escapeHtml(r.asset_title) + '</div>'\n"
    "                      + '    <div style=\"font-size:0.66rem; color: var(--text-secondary); margin-top:0.1rem;\">' + escapeHtml(r.channel) + ' \\u00b7 ' + escapeHtml(r.phase || '') + '</div>'\n"
    "                      + '  </div>'\n"
    "                      + '  <div style=\"display:flex; align-items:center; background: rgba(255,255,255,0.02); border-radius:9999px; padding:2px 6px;\">' + leftHalf + centerRule + rightHalf + '</div>'\n"
    "                      + '  <div style=\"font-size:0.75rem; color: var(--text-primary); text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap;\">' + orText + notSigChip + '</div>'\n"
    "                      + '  <div style=\"text-align:right;\">' + _iiqMTASigChip(r.significance) + '</div>'\n"
    "                      + '</div>'\n"
    "                    );\n"
    "                }\n"
)

NEW_ROW = (
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

# -----------------------------------------------------------------------
# Splice B: chart header row must match the widened grid + gap so the
# "Odds ratio (95% CI)" and "Read strength" column labels stay aligned
# with their columns.
# -----------------------------------------------------------------------
OLD_HEAD = (
    "                var chartHead =\n"
    "                    '<div style=\"display:grid; grid-template-columns: 240px 1fr 220px 90px; gap:0.6rem; padding: 0 0 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary);\">'\n"
)
NEW_HEAD = (
    "                var chartHead =\n"
    "                    '<div style=\"display:grid; grid-template-columns: 240px 1fr 240px 100px; gap:0.9rem; padding: 0 0 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary);\">'\n"
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
    ap = argparse.ArgumentParser(description="Attribution IQ - MTA touchpoint contribution row spacing fix")
    ap.add_argument("--dry-run", action="store_true", help="report splices but do not write")
    args = ap.parse_args()

    print("=== MTA coefficient row spacing fix ===")
    print(f"  --dry-run: {args.dry_run}")
    src = INDEX.read_text(encoding="utf-8")
    print(f"  size: {len(src):,} bytes")
    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")
        print(f"  backup: {BACKUP}")
    print()

    src2 = splice(src, OLD_ROW, NEW_ROW, "row template: OR + not-significant on separate lines, widened grid")
    src2 = splice(src2, OLD_HEAD, NEW_HEAD, "chart header: match widened grid so labels align with columns")

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
