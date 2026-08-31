#!/usr/bin/env python3
"""Collapse Trends IQ filter bar to a single row.

Expands the grid from 3 to 5 explicit columns so Window / As-of /
Today / Lens / Apply all sit inline, and adds a <900px wrap
breakpoint (already the file convention) so mobile stacks 2-per-row.
"""
from pathlib import Path

INDEX = Path("bg-webapp/templates/index.html")
BACKUP = Path("/tmp/index.pre_trends_filter_one_row.html")

OLD = """            #trendsIQView .tiq-filter-bar {
                /* Geography + Region controls were removed 2026-07-27
                   (Trends IQ is now always National); Lens dropdown was
                   added 2026-08-10.  All three controls (Window, Lens,
                   Apply) live on a single row so the filter box stays
                   compact.  align-items: end lines the Apply button's
                   baseline up with the bottom of the two select inputs
                   (each has a stacked <label> above it). */
                display: grid;
                grid-template-columns: minmax(160px, 220px) minmax(180px, 240px) auto;
                justify-content: start;
                gap: 0.6rem;
                align-items: end;
                padding: 0.75rem 1rem;
                background: var(--bg-secondary, #1e1e2e);
                border: 1px solid var(--border-color, #2c2c3e);
                border-radius: 10px;
                margin-bottom: 1rem;
            }
"""

NEW = """            #trendsIQView .tiq-filter-bar {
                /* All five controls (Window, As-of date, Today button,
                   Lens, Apply) sit on a single row so the filter box
                   stays compact.  align-items: end lines the button
                   baselines up with the bottom of the select/date
                   inputs (each has a stacked <label> above it).  On
                   viewports narrower than 900px the grid collapses to
                   two columns so mobile users are not cramped. */
                display: grid;
                grid-template-columns: minmax(140px, 200px) minmax(140px, 180px) auto minmax(160px, 220px) auto;
                justify-content: start;
                gap: 0.6rem;
                align-items: end;
                padding: 0.75rem 1rem;
                background: var(--bg-secondary, #1e1e2e);
                border: 1px solid var(--border-color, #2c2c3e);
                border-radius: 10px;
                margin-bottom: 1rem;
            }
            @media (max-width: 900px) {
                #trendsIQView .tiq-filter-bar {
                    grid-template-columns: 1fr 1fr;
                }
            }
"""


def splice(src: str, old: str, new: str, desc: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x (not unique)")
    return src.replace(old, new)


def main() -> None:
    raw = INDEX.read_bytes()
    before = len(raw)
    BACKUP.write_bytes(raw)
    src = raw.decode("utf-8")
    src = splice(src, OLD, NEW, "trends filter bar one row")
    out = src.encode("utf-8")
    INDEX.write_bytes(out)
    after = INDEX.stat().st_size
    print(f"[splice] before_bytes={before} after_bytes={after} delta_bytes={after - before}")


if __name__ == "__main__":
    main()
