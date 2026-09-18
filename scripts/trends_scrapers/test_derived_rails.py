#!/usr/bin/env python3
"""Regression: a derived rail is always a subset of its parent.

Covers the invariants the 2026-09-17 defect broke, so they cannot come
back quietly:

  * the derived value is strictly below the parent, always
  * it stays inside the researched band, so the top of the share
    distribution sits at the band top rather than at 1.0
  * it never equals the previous day's derived value on the same rail
  * deriving twice from the same parent gives the same answer, so the
    board-level pass can run after every other pass without
    compounding
  * the child's registered service ceiling is exactly its parent's
    ceiling times the top of its band, which is what lets the board's
    cap pass leave derived rails to their parent
  * Southpaw, the title that surfaced the defect, reads below Starz on
    the numbers that produced it

No network, no board, no clickstream. Pure arithmetic against the
registry.

    python3 -m scripts.trends_scrapers.test_derived_rails
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import derived_rails as dr  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


# A spread of parent values from a thin catalog row to a tentpole, plus
# the awkward small ones where rounding and the digit draw have the
# least room.
_PARENTS = [3, 7, 19, 41, 137, 904, 5_113, 47_209, 285_714, 747_997,
            1_204_663, 4_998_201]
_TITLES = ['Southpaw', 'Power Book II: Ghost', 'Outlander', 'Spartacus',
           'The Housemaid', 'BMF', 'P-Valley', 'John Wick',
           'Now You See Me', 'Saw X']
_CATS = ['Film', 'TV', '']


def main() -> int:
    rails = dr.child_slugs()
    check(bool(rails), f'the registry carries at least one rail: {rails}')

    for child in rails:
        parent = dr.parent_slug(child)
        check(bool(parent), f'{child} names a parent rail ({parent!r})')

        below = above = stepped_ok = 0
        worst_share = 0.0
        for title in _TITLES:
            for cat in _CATS:
                lo, hi = dr.band_for(child, cat)
                for pv in _PARENTS:
                    v, ceiling, _how = dr.derive_value(pv, child, title, cat)
                    if v <= 0:
                        continue
                    if v < pv:
                        below += 1
                    else:
                        above += 1
                        print(f'      {child} {title!r} {cat!r} parent={pv} '
                              f'child={v}')
                    if v > ceiling:
                        above += 1
                    share = v / float(pv)
                    worst_share = max(worst_share, share)
                    # The band is a statement about the share, so it
                    # only binds where the parent is big enough for the
                    # share to be expressible at integer resolution.
                    if pv >= 1_000 and share > hi + 1e-9:
                        above += 1
                        print(f'      {child} {title!r} share {share:.4f} '
                              f'above band top {hi:.4f}')
                    if pv >= 10_000 and share < lo * 0.9:
                        above += 1
                        print(f'      {child} {title!r} share {share:.4f} '
                              f'far below band floor {lo:.4f}')

                    # Idempotence: the same parent always gives the
                    # same child.
                    again, _c, _h = dr.derive_value(pv, child, title, cat)
                    if again != v:
                        above += 1
                        print(f'      {child} {title!r} not idempotent: '
                              f'{v} then {again}')

                    # Adjacent-day collision: hand the derivation its
                    # own answer as yesterday's and it must move off it
                    # without leaving the ceiling or reaching the
                    # parent.
                    v2, _c2, how2 = dr.derive_value(
                        pv, child, title, cat, prev_child=v, lead=1)
                    if pv >= 100:
                        if v2 != v and v2 < pv and 0 < v2 <= ceiling:
                            stepped_ok += 1
                        else:
                            above += 1
                            print(f'      {child} {title!r} parent={pv} did '
                                  f'not step off a repeat: {v} -> {v2} '
                                  f'({how2}, ceiling {ceiling})')

        check(above == 0,
              f'{child}: {below} derivations all sat strictly below the '
              f'{parent} rail and inside the researched band '
              f'({above} violation(s))')
        check(worst_share < 1.0,
              f'{child}: the highest share reached was {worst_share:.4f}, '
              f'not 1.0')
        check(stepped_ok > 0,
              f'{child}: an adjacent-day repeat is moved off inside the '
              f'ceiling ({stepped_ok} case(s))')

    problems = dr.registered_ceiling_check()
    check(not problems,
          'every rail\'s registered service ceiling equals its parent\'s '
          'times the top of its band'
          + ('' if not problems else f': {problems}'))

    # The title that surfaced the defect, on the numbers that produced
    # it. The parent reading was 747,997 before the cap pass pulled it
    # to 285,714; the child had been left on the pre-correction number
    # and read 341,592.
    for pv in (747_997, 285_714):
        v, ceiling, _how = dr.derive_value(pv, 'starz_amazon', 'Southpaw',
                                            'Film')
        check(v < pv and v <= ceiling,
              f'Southpaw against a Starz reading of {pv:,} derives '
              f'{v:,}, under both the rail it is part of and its '
              f'ceiling of {ceiling:,}')

    # A rail with no parent value has nothing to take a share of and
    # says so rather than inventing one.
    v, _c, how = dr.derive_value(0, 'starz_amazon', 'Southpaw', 'Film')
    check(v == 0 and how == 'no_parent',
          'a title with no number on the parent rail derives nothing')

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
