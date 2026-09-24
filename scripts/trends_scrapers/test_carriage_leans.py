#!/usr/bin/env python3
"""Regression: a title's place in its Amazon band is reasoned, not drawn.

Covers the 2026-09-24 rails (HBO Max, Peacock, BritBox, MGM+ on
Amazon) and the lean mechanism they ride on:

  * a higher lean sits higher in the band, so the reasoning is what
    orders titles on the breakout
  * two titles the reasoning put at the SAME lean still never share a
    position (the per-title spread), and never sit on a band edge
  * every derived value with a lean and a day in play is strictly
    under its parent and inside the researched band
  * a title with no lean yet takes the title-hash draw, so the board
    never waits on a call
  * the Starz and Paramount+ rails ignore leans entirely, so nothing
    that shipped moved
  * the child is not a constant multiple of the parent across titles
    or across days
  * rank order on the breakout can differ from the parent's, which is
    the point of the breakout

No network, no board, no clickstream. Leans come from a temp dir.

    python3 -m scripts.trends_scrapers.test_carriage_leans
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

_TMP = tempfile.mkdtemp(prefix='carriage_leans_')
os.environ['DERIVED_RAIL_LEANS'] = _TMP

from scripts.trends_scrapers import derived_rails as dr  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


_NEW = ('max_amazon', 'peacock_amazon', 'britbox_amazon', 'mgmplus_amazon')
_OLD = ('starz_amazon', 'paramountplus_amazon')
_DAYS = ['2026-09-%02d' % d for d in range(1, 29)]

# A small catalog with a spread of leans, two of them equal on
# purpose, plus one title deliberately left out of the file.
_LEANS = {
    'Practical Magic':    0.71,
    'The Sopranos':       0.58,
    'Casablanca':         0.58,
    'Midsomer Murders':   0.44,
    'Wicked':             0.12,
    'Poker Face':        -0.23,
    'The Pitt':          -0.61,
    'Lanterns':          -0.84,
}
_UNREASONED = 'Brand New Title Nobody Has Seen'


def _write_leans() -> None:
    for child in _NEW:
        rail = dr.rail_for(child)
        doc = {'rail': child, 'parent': rail.parent, 'titles': {
            dr.lean_key(t): {'title': t, 'lean': v, 'why': 'test'}
            for t, v in _LEANS.items()}}
        with open(os.path.join(_TMP, f'{child}.json'), 'w') as fh:
            json.dump(doc, fh)
    dr.reset_leans_cache()


def main() -> int:
    _write_leans()

    for child in _NEW:
        rail = dr.rail_for(child)
        check(rail is not None and rail.reasoned,
              f'{child} is registered and reasons its titles')
        check(len(dr.title_leans(child)) == len(_LEANS),
              f'{child} loaded {len(_LEANS)} lean(s) from the store')

        # Order follows the lean, within one category band.
        for cat in ('Film', 'TV'):
            lo, hi = dr.band_for(child, cat)
            ordered = sorted(_LEANS.items(), key=lambda kv: -kv[1])
            shares = [dr.base_share_for(child, t, cat) for t, _ in ordered]
            monotone = all(shares[i] > shares[i + 1]
                           for i in range(len(shares) - 1)
                           if ordered[i][1] != ordered[i + 1][1])
            check(monotone,
                  f'{child}/{cat}: a higher lean sits higher in the band')
            check(all(lo < s < hi for s in shares),
                  f'{child}/{cat}: every reasoned position sits strictly '
                  f'inside {lo:.3f} to {hi:.3f}')
            a = dr.base_share_for(child, 'The Sopranos', cat)
            b = dr.base_share_for(child, 'Casablanca', cat)
            check(abs(a - b) > 1e-6,
                  f'{child}/{cat}: two titles at the same lean take '
                  f'different positions ({a:.5f} vs {b:.5f})')

        # No lean yet: the title-hash draw, inside the band.
        lo, hi = dr.band_for(child, 'Film')
        u = dr.base_share_for(child, _UNREASONED, 'Film')
        check(dr.title_lean(child, _UNREASONED) is None and lo <= u <= hi,
              f'{child}: a title with no lean takes the draw inside the band')

        # With the day in play: inside the band, strictly under the
        # parent, and never a constant multiple.
        bad = 0
        ratios: set = set()
        for pv in (4_113, 61_207, 285_714, 1_204_663):
            for t in list(_LEANS) + [_UNREASONED]:
                for cat in ('Film', 'TV'):
                    lo, hi = dr.band_for(child, cat)
                    for d in _DAYS[:10]:
                        s = dr.share_for(child, t, cat, d)
                        if not (lo <= s <= hi):
                            bad += 1
                        v, ceil, _ = dr.derive_value(pv, child, t, cat,
                                                     day_iso=d)
                        if not (0 < v < pv and v <= ceil):
                            bad += 1
                        # At a four-figure parent the child is two or
                        # three figures and ratios collide on rounding
                        # alone; the constancy test reads where the
                        # ratio is expressible.
                        if pv >= 60_000:
                            ratios.add(round(v / float(pv), 6))
        check(bad == 0,
              f'{child}: every reasoned share stayed in band and every '
              f'value strictly under its parent')
        check(len(ratios) > 300,
              f'{child}: {len(ratios)} distinct child/parent ratios across '
              f'titles and days, not a constant')

        # Rank order on the breakout can differ from the parent's. A
        # 7% gap on the parent is inside what the band can turn over;
        # the over-indexing title lands above the one it trails.
        parent = {'Practical Magic': 100_000, 'Lanterns': 107_000}
        child_v = {t: dr.derive_value(pv, child, t, 'Film',
                                      day_iso=_DAYS[0])[0]
                   for t, pv in parent.items()}
        check(child_v['Practical Magic'] > child_v['Lanterns'],
              f'{child}: a title that over-indexes on Amazon sits above '
              f'one it sits below on the parent '
              f'({child_v["Practical Magic"]:,} vs {child_v["Lanterns"]:,})')

    # Same-day, cross-title: two titles that round onto one child
    # integer are stepped apart inside the ceiling and under the
    # parent. This is the collision the HBO Max depth tail produced on
    # the first render (three titles at 1,199).
    taken = {1199, 1209, 1189, 1179}
    for lead in (1, -1, 0):
        new = dr.step_clear(1199, taken, 1600, lead, 'Evil Dead',
                            'max_amazon|same-day')
        check(new not in taken and 0 < new <= 1600 and abs(new - 1199) < 200,
              f'step_clear moved a taken value off the rail (lead {lead}: '
              f'1199 -> {new})')
    check(dr.step_clear(1500, {1500}, 1500, 1, 'x', 's') < 1500,
          'step_clear on the ceiling steps down, never over it')
    check(dr.step_clear(1234, {1199}, 1600, 1, 'x', 's') == 1234,
          'step_clear leaves an untaken value alone')

    # The two rails that shipped on the draw never read a lean.
    for child in _OLD:
        rail = dr.rail_for(child)
        check(rail is not None and not rail.reasoned
              and dr.title_leans(child) == {},
              f'{child} does not reason titles and loads no leans')

    # Switching the pass off returns every rail to the draw.
    os.environ['DERIVED_RAIL_LEANS'] = 'off'
    dr.reset_leans_cache()
    check(all(dr.title_leans(c) == {} for c in _NEW),
          'DERIVED_RAIL_LEANS=off disables the pass for every rail')
    os.environ['DERIVED_RAIL_LEANS'] = _TMP
    dr.reset_leans_cache()

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILURE(S)')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
