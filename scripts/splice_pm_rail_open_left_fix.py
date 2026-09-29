#!/usr/bin/env python3
"""Drawer opens leftward regardless of anchoring (2026-09-28 Jenna:
"it should just open to the left so it doesnt force the window off
screen"). A dragged widget is left-anchored, so width growth extended
RIGHT and pushed it off-screen. Now the open shifts the window's left
edge by exactly the growth, clamped to the viewport, and the close
reverses the same amount. Python byte-level splice."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_pm_rail_leftfix.html")

OLD = """            // The widget is right-anchored, so width growth extends
            // LEFT: the chat keeps its full width and the drawer can
            // stay open beside it.
            if (_pmRailOpen && !was) {
                var w = widget.getBoundingClientRect().width;
                widget.style.width = Math.min(
                    w + _PM_RAIL_W,
                    Math.floor(window.innerWidth * 0.94)) + 'px';
            } else if (!_pmRailOpen && was) {
                var w2 = widget.getBoundingClientRect().width;
                widget.style.width = Math.max(w2 - _PM_RAIL_W, 360) + 'px';
            }"""
NEW = """            // Open LEFT no matter how the window is anchored
            // (2026-09-28): a dragged window is left-positioned, so a
            // plain width increase grows RIGHT and shoves it off
            // screen. Grow the width AND pull the left edge back by
            // the same amount, clamped to the viewport; the chat
            // never moves and nothing leaves the screen. Close
            // reverses the exact applied growth.
            if (_pmRailOpen && !was) {
                var r = widget.getBoundingClientRect();
                var grow = Math.min(
                    _PM_RAIL_W,
                    Math.max(0, r.left - 8),
                    Math.max(0, Math.floor(window.innerWidth * 0.96)
                             - r.width));
                if (grow < 40) grow = Math.min(_PM_RAIL_W,
                    Math.max(40, Math.floor(window.innerWidth * 0.96)
                             - r.width));
                window._pmRailGrow = grow;
                var leftAnchored = !!widget.style.left
                    || getComputedStyle(widget).right === 'auto';
                widget.style.width = (r.width + grow) + 'px';
                if (leftAnchored) {
                    widget.style.left = Math.max(8, r.left - grow) + 'px';
                    widget.style.right = 'auto';
                }
            } else if (!_pmRailOpen && was) {
                var g = window._pmRailGrow || _PM_RAIL_W;
                var r2 = widget.getBoundingClientRect();
                widget.style.width = Math.max(r2.width - g, 360) + 'px';
                if (widget.style.left) {
                    widget.style.left = (r2.left + g) + 'px';
                }
                window._pmRailGrow = 0;
            }"""

src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
count = src.count(OLD)
if count != 1:
    raise RuntimeError(f"anchor found {count}x")
INDEX.write_text(src.replace(OLD, NEW), encoding="utf-8")
print("spliced open-left fix")
