#!/usr/bin/env python3
"""Weekly Summary WoW chip: when total exposure moved less than 0.05%
week over week, render 'Steady state' (the same verdict the exposure
tile below already gives) instead of a salted +/-0.1..0.99% that
contradicts the '0 this week' tile. GOAT audit 2026-09-23.

Byte-level splice per index-html-safety.mdc.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "templates" / "index.html"
BACKUP = Path("/tmp/index.html.pre_wow_chip_steady_2026_09_23.html")

OLD = """                    var deltaPct = (totalNow - totalPrev) / totalPrev * 100;
                    // Never ship a round zero; nudge by 0.1 in the direction
                    // of the raw delta so the chip reads as measurement,
                    // not a placeholder.
                    if (Math.abs(deltaPct) < 0.05) {
                        var salt = String(slug) + '|wow|' + asOf;
                        var h = 0;
                        for (var _i = 0; _i < salt.length; _i++) h = ((h << 5) - h + salt.charCodeAt(_i)) | 0;
                        var jitter = ((Math.abs(h) % 90) + 10) / 100; // 0.10..0.99
                        deltaPct = (h % 2 === 0) ? jitter : -jitter;
                    }
                    var pos = deltaPct > 0;"""
NEW = """                    var deltaPct = (totalNow - totalPrev) / totalPrev * 100;
                    if (Math.abs(deltaPct) < 0.05) {
                        // Below the WoW threshold: say so, in the same words
                        // as the total-exposure tile, rather than inventing
                        // a fractional move.
                        wowChipHtml = '<span class="iiq-hero-wow-chip" style="color:#B7B3D8; background: rgba(183,179,216,0.10); border: 1px solid rgba(183,179,216,0.35);">Steady state WoW</span>';
                    }
                    var pos = deltaPct > 0;"""
OLD_TAIL = """                    var sign = pos ? '+' : '';
                    wowChipHtml = '<span class="iiq-hero-wow-chip" style="color:' + chipColor + '; background: ' + chipBg + '; border: 1px solid ' + chipBorder + ';">' + sign + deltaPct.toFixed(1) + '% WoW exposure</span>';"""
NEW_TAIL = """                    var sign = pos ? '+' : '';
                    if (!wowChipHtml) wowChipHtml = '<span class="iiq-hero-wow-chip" style="color:' + chipColor + '; background: ' + chipBg + '; border: 1px solid ' + chipBorder + ';">' + sign + deltaPct.toFixed(1) + '% WoW exposure</span>';"""


def splice(src, old, new, desc):
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f"[{desc}] expected 1 anchor, found {n}")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
before = len(src)
src = splice(src, OLD, NEW, "wow chip steady")
src = splice(src, OLD_TAIL, NEW_TAIL, "wow chip tail")
INDEX.write_text(src, encoding="utf-8")
print(f"OK: {before:,} -> {len(src):,} bytes. Backup at {BACKUP}")
