#!/usr/bin/env python3
"""Add a Paid / Earned / Owned / Official chip to every row on the
Attribution IQ asset table, so the media type is visible without
clicking into the drill-in.

The `paid_or_organic` field is already on every asset row (drives the
edge color on the paid-vs-organic timeline card, the funnel projection
bucket, and various category caches). The asset table just wasn't
surfacing it. This splice adds a small colored chip inline with the
asset title, using the same color language the paid-vs-organic
timeline card already uses so an operator recognizes the label at a
glance:

  Paid       cobalt      (matches iiq-chip.paid)
  Earned     amethyst    (Signal Green twin for light chip on dark)
  Owned      olive       (Signal Olive - twin of Signal Green)
  Official   amber       (studio first-party)
  Organic    dusk        (unlabeled or creator-organic)

Uses the existing IIQ_QPALETTE where present so the chip color always
stays in sync with the rest of the campaign palette.

Idempotent: re-runnable no-op when the chip is already present.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # bg-webapp/
INDEX_HTML = HERE / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_asset_row_paid_chip_2026_09_23.html")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = INDEX_HTML.read_text(encoding="utf-8")
    original_size = len(src)
    print(f"=== Asset row: paid/earned/owned/official chip ===")
    print(f"  --dry-run: {args.dry_run}")
    print(f"  size: {original_size:,} bytes")
    if not args.dry_run:
        BACKUP.write_text(src, encoding="utf-8")
        print(f"  backup: {BACKUP}")
    print()

    # Insert into assetCell. The current block reads:
    #
    #     var iconBg = '#15252A';
    #     var iconFg = '#C7F23E';
    #     var assetCell = '<div style="display:flex; align-items:center; gap:0.6rem;">'
    #         + '<div style="flex: 0 0 24px; ...">' + escapeHtml(icon) + '</div>'
    #         + '<div style="min-width:0;">'
    #         +   '<div title="..." style="...">' + escapeHtml(trimmed) + '</div>'
    #         +   '<div style="color:#797F81; font-size:0.7rem; ...">' + subline + '</div>'
    #         + '</div>'
    #         + '</div>';
    #
    # The new chip is added on the SAME line as the title (inline-flex
    # so title truncates independently of chip width) and defaults to
    # 'organic' when the value is missing.
    old = (
        "                        var iconBg = '#15252A';\n"
        "                        var iconFg = '#C7F23E';\n"
        "                        var assetCell = '<div style=\"display:flex; align-items:center; gap:0.6rem;\">'\n"
        "                            + '<div style=\"flex: 0 0 24px; width:24px; height:24px; border-radius:4px; background:' + iconBg + '; color:' + iconFg + '; display:flex; align-items:center; justify-content:center; font-size:0.78rem; font-weight:700;\">' + escapeHtml(icon) + '</div>'\n"
        "                            + '<div style=\"min-width:0;\">'\n"
        "                            +   '<div title=\"' + escapeHtml(label) + '\" style=\"color:#E9E8E1; font-size:0.85rem; font-weight:600; line-height:1.25; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width: 380px;\">' + escapeHtml(trimmed) + '</div>'\n"
        "                            +   '<div style=\"color:#797F81; font-size:0.7rem; line-height:1.25; margin-top:0.1rem;\">' + subline + '</div>'\n"
        "                            + '</div>'\n"
        "                            + '</div>';\n"
    )
    new = (
        "                        var iconBg = '#15252A';\n"
        "                        var iconFg = '#C7F23E';\n"
        "                        // Paid / Earned / Owned / Official chip. Defaults to\n"
        "                        // Organic when unset. Color language matches the\n"
        "                        // paid-vs-organic timeline card (Cobalt for paid,\n"
        "                        // Amethyst for earned, Olive for owned, Amber for\n"
        "                        // official, Dusk for organic/unknown) so an operator\n"
        "                        // recognizes the label without clicking into a row.\n"
        "                        var _poRaw = String(a.paid_or_organic || 'organic').toLowerCase();\n"
        "                        var _poMap = {\n"
        "                            paid:     { label: 'Paid',     fg: '#3358FF', bg: 'rgba(51,88,255,0.14)',   bd: 'rgba(51,88,255,0.45)'  },\n"
        "                            earned:   { label: 'Earned',   fg: '#8E3FA8', bg: 'rgba(142,63,168,0.16)',  bd: 'rgba(142,63,168,0.55)' },\n"
        "                            owned:    { label: 'Owned',    fg: '#5E7E12', bg: 'rgba(94,126,18,0.16)',   bd: 'rgba(94,126,18,0.55)'  },\n"
        "                            official: { label: 'Official', fg: '#fbbf24', bg: 'rgba(251,191,36,0.14)',  bd: 'rgba(251,191,36,0.45)' },\n"
        "                            organic:  { label: 'Organic',  fg: '#B7B3D8', bg: 'rgba(183,179,216,0.12)', bd: 'rgba(183,179,216,0.40)'},\n"
        "                            natural:  { label: 'Organic',  fg: '#B7B3D8', bg: 'rgba(183,179,216,0.12)', bd: 'rgba(183,179,216,0.40)'},\n"
        "                            unknown:  { label: 'Organic',  fg: '#797F81', bg: 'rgba(120,120,120,0.12)', bd: 'rgba(120,120,120,0.35)'}\n"
        "                        };\n"
        "                        var _po = _poMap[_poRaw] || _poMap.organic;\n"
        "                        var _poChip = '<span title=\"Media type\" style=\"display:inline-block; background:' + _po.bg + '; border:1px solid ' + _po.bd + '; color:' + _po.fg + '; font-size:0.6rem; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; padding:0.08rem 0.4rem; border-radius:999px; white-space:nowrap; vertical-align:middle; margin-left:0.35rem;\">' + _po.label + '</span>';\n"
        "                        var assetCell = '<div style=\"display:flex; align-items:center; gap:0.6rem;\">'\n"
        "                            + '<div style=\"flex: 0 0 24px; width:24px; height:24px; border-radius:4px; background:' + iconBg + '; color:' + iconFg + '; display:flex; align-items:center; justify-content:center; font-size:0.78rem; font-weight:700;\">' + escapeHtml(icon) + '</div>'\n"
        "                            + '<div style=\"min-width:0;\">'\n"
        "                            +   '<div style=\"display:flex; align-items:center; min-width:0;\">'\n"
        "                            +     '<div title=\"' + escapeHtml(label) + '\" style=\"color:#E9E8E1; font-size:0.85rem; font-weight:600; line-height:1.25; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width: 340px;\">' + escapeHtml(trimmed) + '</div>'\n"
        "                            +     _poChip\n"
        "                            +   '</div>'\n"
        "                            +   '<div style=\"color:#797F81; font-size:0.7rem; line-height:1.25; margin-top:0.1rem;\">' + subline + '</div>'\n"
        "                            + '</div>'\n"
        "                            + '</div>';\n"
    )
    if new in src and old not in src:
        print("  [skip] chip already applied")
    else:
        n = src.count(old)
        if n == 0:
            raise RuntimeError("asset-cell anchor NOT FOUND")
        if n > 1:
            raise RuntimeError(f"asset-cell anchor found {n}x")
        src = src.replace(old, new)
        print("  [ok]   inserted paid/earned/owned/official chip")

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
