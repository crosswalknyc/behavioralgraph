"""Carried chart hits are 4-tuples; no consumer may strict-unpack 3.

`published_chart_index` returns `(pos, label, group)` for a chart captured
today and `(pos, label, group, day)` when the chart is carried from the
archive (`published_chart_source`). On 2026-09-30 the collector still did
`pos, chart_name, group = hit`, the first carried Prime Video chart hit
it, and the whole nightly `stream_estimates` pass crashed: the board
served 2026-09-28 for a day. This test keeps every consumer tolerant.

Run: python3 -m scripts.trends_scrapers.test_chart_carry_unpack
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# Files that read hits out of the published chart index.
CONSUMERS = [
    HERE / 'stream_estimates.py',
    HERE / 'residential_chart_pricing.py',
    HERE / 'coverage_gate.py',
    HERE / 'terminal_bracket.py',
    HERE / 'board_invariants.py',
    HERE / 'chart_entry_sync.py',
    ROOT / 'trends_iq.py',
]

# A strict tuple unpack of a chart hit: three bare names, `= hit`, end of
# statement. Index access (`hit[0]`) and `hit[0], hit[1], hit[2]` are fine.
STRICT = re.compile(r'^\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*=\s*hit\s*$',
                    re.MULTILINE)


def test_no_strict_three_unpack() -> list[str]:
    bad = []
    for f in CONSUMERS:
        if not f.exists():
            continue
        for m in STRICT.finditer(f.read_text(encoding='utf-8')):
            line = f.read_text(encoding='utf-8')[:m.start()].count('\n') + 1
            bad.append(f'{f.name}:{line}: {m.group(0).strip()}')
    return bad


def test_carried_hits_are_four_tuples() -> list[str]:
    from scripts.trends_scrapers import stream_estimates as se
    idx = {'k': (3, 'Some Chart', 'tv')}
    carried = {k: tuple(v) + ('2026-09-28',) for k, v in idx.items()}
    hit = carried['k']
    errs = []
    if len(hit) != 4:
        errs.append(f'carried hit has {len(hit)} elements')
    # The tolerant read every consumer must use.
    pos, chart_name, group = hit[0], hit[1], hit[2]
    if (pos, chart_name, group) != (3, 'Some Chart', 'tv'):
        errs.append('tolerant unpack lost a field')
    if not hasattr(se, 'published_chart_source'):
        errs.append('published_chart_source missing')
    return errs


def main() -> int:
    errs = test_no_strict_three_unpack() + test_carried_hits_are_four_tuples()
    for e in errs:
        print('FAIL', e)
    print('ok' if not errs else f'{len(errs)} failure(s)')
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
