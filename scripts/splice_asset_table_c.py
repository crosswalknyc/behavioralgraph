#!/usr/bin/env python3
"""Attribution IQ Phase 2 (Sony GOAT redesign): Weekly Summary polish.

Flip the empty-bullet state on the Phase 1 Weekly Summary card:
instead of showing a "move the picker forward" fallback inside the
card body, hide the entire card (header + body) when bulletsArr is
empty. At T-90 with zero measured activity, an empty summary card
diluted the at-a-glance value of the new landing surface; the Asset-
Ranked Table's own empty-state handles the "no activity yet"
messaging now.

Byte-level splice per index-html-safety.mdc.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_asset_table_c.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


OLD = """                // ===== Compose the card =====
                var bulletsArr = [bulletMover, bulletSignal, bulletAud, bulletSoft].filter(function(x) { return !!x; });
                var bulletsHtml;
                if (bulletsArr.length) {
                    bulletsHtml = '<ul style="list-style: disc; padding-left: 1.2rem; margin: 0; display: flex; flex-direction: column; gap: 0.35rem;">'
                        + bulletsArr.map(function(b) { return '<li style="font-size: 0.82rem; line-height: 1.45; color: #9AA09B;">' + b + '</li>'; }).join('')
                        + '</ul>';
                } else {
                    bulletsHtml = '<div style="font-size: 0.78rem; color: #797F81;">No asset activity in view yet at this cursor. Move the picker forward, or click T-30 / T-7 / T-0 to jump to a campaign moment with measured activity.</div>';
                }"""

NEW = """                // ===== Compose the card =====
                // Phase 2 polish: hide the entire card (header + body)
                // when no bullets compute. The Asset-Ranked Table's own
                // empty-state ("No assets in view at this as-of date")
                // now carries the "move the picker forward" prompt, so
                // an empty Weekly Summary here just dilutes the surface.
                var bulletsArr = [bulletMover, bulletSignal, bulletAud, bulletSoft].filter(function(x) { return !!x; });
                if (!bulletsArr.length) {
                    card.style.display = 'none';
                    card.innerHTML = '';
                    return;
                }
                var bulletsHtml = '<ul style="list-style: disc; padding-left: 1.2rem; margin: 0; display: flex; flex-direction: column; gap: 0.35rem;">'
                    + bulletsArr.map(function(b) { return '<li style="font-size: 0.82rem; line-height: 1.45; color: #9AA09B;">' + b + '</li>'; }).join('')
                    + '</ul>';"""


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[splice] backup written: {BACKUP} ({len(src):,} bytes)")

    src = splice(src, OLD, NEW, "weekly summary empty-state flip")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote: {INDEX} ({len(src):,} bytes)")


if __name__ == "__main__":
    main()
