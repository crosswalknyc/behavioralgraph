#!/usr/bin/env python3
"""Regression: the carriage model stays honest.

The Vampire Diaries ranked implausibly high on the Max rail because a
Prime Video reading landed on the Max row. The fix was to state each
service's distribution mix explicitly rather than let it fall out of
a per-title call: a main rail is the whole service across every path
it is sold through, Prime Video's rail is Prime Video's own catalog
and not the channels sold inside it, and an "X on Amazon" rail exists
only where the split is published.

These checks are the ones that would go quiet first if any of that
drifted:

  * every service on the Streaming tab has a mix entry, so a service
    added later cannot slip through with no stated scope
  * the registry and the rails agree in both directions: a service
    marked for breakout has a rail, and a rail's parent is marked for
    breakout
  * a service whose Amazon split is NOT published has no rail, and
    the reason is written down
  * an Amazon-only service has no rail, because a breakout of it
    would equal its parent
  * Prime Video's scope excludes the channels sold through it, which
    is the sentence the whole fix turns on
  * the share moves day to day and never leaves the band, and with no
    day given it is byte-identical to what it was before the day
    existed, so nothing that shipped moved underneath us

No network, no board, no clickstream.

    python3 -m scripts.trends_scrapers.test_carriage_model
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import carriage_mix as cm    # noqa: E402
from scripts.trends_scrapers import derived_rails as dr   # noqa: E402
from scripts.trends_scrapers import stream_estimates as se  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


_DAYS = ['2026-09-%02d' % d for d in range(1, 29)]
_TITLES = ['Southpaw', 'Landman', 'Power Book II: Ghost', 'Outlander',
           'Mission: Impossible - The Final Reckoning', 'Star Trek',
           'A Quiet Place', 'Tulsa King', 'John Wick', 'Yellowstone']


def _service_keys() -> list:
    """Every non-derived service on the Streaming tab."""
    return [p['key'] for p in se._STREAMING_PLATFORMS_META
            if not p.get('derived_from')]


def main() -> int:
    # -----------------------------------------------------------
    # Coverage: no service on the tab without a stated scope.
    # -----------------------------------------------------------
    missing = [k for k in _service_keys() if cm.mix_for(k) is None]
    check(not missing,
          f'every service on the Streaming tab has a distribution mix '
          f'entry{"" if not missing else " (missing: %s)" % missing}')

    blank = [k for k in _service_keys() if not cm.scope_line(k)]
    check(not blank,
          f'every service renders a scope line for the research '
          f'prompt{"" if not blank else " (blank: %s)" % blank}')

    thin = [k for k in _service_keys()
            if len((cm.mix_for(k).basis or '').strip()) < 120]
    check(not thin,
          f'every service states the evidence behind its mix'
          f'{"" if not thin else " (thin: %s)" % thin}')

    # -----------------------------------------------------------
    # The registry and the rails agree in both directions.
    # -----------------------------------------------------------
    parents = {dr.parent_slug(c) for c in dr.child_slugs()}
    marked = set(cm.breakout_services())
    check(parents == marked,
          f'the services marked for a breakout are exactly the ones '
          f'with a rail (marked {sorted(marked)}, railed {sorted(parents)})')

    for slug in marked:
        m = cm.mix_for(slug)
        check(m.on_amazon and m.amazon_share is not None,
              f'{slug} is marked for a breakout and carries both '
              f'carriage and a researched share')

    # A rail's band must bracket the service-level share it was
    # reasoned from. A band that does not contain its own anchor is a
    # sign the two drifted apart.
    for child in dr.child_slugs():
        rail = dr.rail_for(child)
        parent_share = cm.amazon_share(rail.parent)
        lo = min(b[0] for b in rail.bands.values())
        hi = max(b[1] for b in rail.bands.values())
        check(parent_share is not None and lo <= parent_share <= hi,
              f'{child}: the per-title band {lo:.3f} to {hi:.3f} '
              f'brackets the service-level share of {parent_share}')
        check(abs(rail.anchor_share - (parent_share or 0)) < 1e-9,
              f'{child}: the rail anchor and the registry share are the '
              f'same number ({rail.anchor_share})')

    # -----------------------------------------------------------
    # Held breakouts: carried, no rail, and the reason written down.
    # -----------------------------------------------------------
    held = cm.held_breakouts()
    check(bool(held), 'the services held back from a breakout are named')
    for slug, reason in sorted(held.items()):
        m = cm.mix_for(slug)
        check(m is not None and m.on_amazon and not m.breakout
              and m.amazon_share is None,
              f'{slug} is carried on Amazon, has no published share and '
              f'is not marked for a breakout')
        check(slug not in parents,
              f'{slug} has no derived rail')
        check(len(reason.strip()) > 20,
              f'{slug} says why it is held: {reason}')

    # -----------------------------------------------------------
    # Amazon-only services: a breakout would equal the parent.
    # -----------------------------------------------------------
    for slug in ('lionsgateplus', 'moviesphereplus'):
        m = cm.mix_for(slug)
        check(m is not None and not m.breakout and slug not in parents,
              f'{slug} is Amazon-carried with no second storefront of '
              f'its own and correctly has no breakout rail')

    # -----------------------------------------------------------
    # Services with no Amazon path at all.
    # -----------------------------------------------------------
    for slug in ('netflix', 'disneyplus', 'hulu', 'espnplus'):
        m = cm.mix_for(slug)
        check(m is not None and not m.on_amazon and not m.breakout,
              f'{slug} has no Amazon path, so no carriage component and '
              f'no breakout')
        line = cm.scope_line(slug)
        check('Prime Video' not in line,
              f'{slug} scope line does not mention Prime Video')

    # -----------------------------------------------------------
    # The sentence the whole fix turns on.
    # -----------------------------------------------------------
    pv = cm.scope_line('primevideo')
    check('OWN licensed catalog' in pv and 'NOT Prime Video viewing' in pv,
          "Prime Video's scope is its own catalog and explicitly not the "
          'channels sold through it')
    check(cm.mix_for('primevideo').breakout is False,
          'Prime Video has no breakout rail of its own')

    preface = cm.prime_video_exclusion_note()
    for phrase in ('whole US audience', 'Prime Video is the exception',
                   'never carry one'):
        check(phrase in preface,
              f'the prompt preface states: {phrase}')

    # -----------------------------------------------------------
    # The share moves day to day, stays in band, and the no-day
    # answer has not moved.
    # -----------------------------------------------------------
    for child in dr.child_slugs():
        for cat in ('Film', 'TV', ''):
            lo, hi = dr.band_for(child, cat)
            out_of_band = []
            flat = []
            for t in _TITLES:
                vals = [dr.share_for(child, t, cat, d) for d in _DAYS]
                out_of_band += [v for v in vals if not (lo <= v <= hi)]
                if len(set(round(v, 6) for v in vals)) < len(_DAYS) // 2:
                    flat.append(t)
            check(not out_of_band,
                  f'{child}/{cat or "unclassified"}: every daily share '
                  f'across {len(_TITLES)} titles x {len(_DAYS)} days sat '
                  f'inside {lo:.3f} to {hi:.3f}')
            check(not flat,
                  f'{child}/{cat or "unclassified"}: no title sits on a '
                  f'constant share across the month'
                  f'{"" if not flat else " (%s)" % flat}')

    # Two titles never land on one share on the same day.
    for child in dr.child_slugs():
        collisions = 0
        for d in _DAYS:
            seen = [round(dr.share_for(child, t, 'Film', d), 6)
                    for t in _TITLES]
            collisions += len(seen) - len(set(seen))
        check(collisions == 0,
              f'{child}: no two titles shared a share on any of the '
              f'{len(_DAYS)} days tested')

    # The no-day answer is still the original title hash draw. This is
    # the guard against the day wobble silently re-levelling a rail
    # that already shipped.
    for child in dr.child_slugs():
        rail = dr.rail_for(child)
        same = True
        for t in _TITLES:
            for cat in ('Film', 'TV', ''):
                lo, hi = dr.band_for(child, cat)
                h = hashlib.sha256(
                    f'{rail.child}|{t.strip().lower()}'.encode()).hexdigest()
                want = lo + (hi - lo) * (int(h[:12], 16) / float(16 ** 12))
                if abs(dr.share_for(child, t, cat) - want) > 1e-12:
                    same = False
        check(same,
              f'{child}: with no day given the share is byte-identical to '
              f'the original per-title draw, so nothing that shipped moved')

    # -----------------------------------------------------------
    # The subset invariant holds with the day in play.
    # -----------------------------------------------------------
    for child in dr.child_slugs():
        bad = 0
        for pv_value in (137, 5_113, 285_714, 1_204_663, 4_998_201):
            for t in _TITLES:
                for cat in ('Film', 'TV'):
                    for d in _DAYS[:7]:
                        v, ceil, _ = dr.derive_value(pv_value, child, t, cat,
                                                      day_iso=d)
                        if not (0 < v < pv_value and v <= ceil):
                            bad += 1
        check(bad == 0,
              f'{child}: every derivation with a day in play stayed '
              f'strictly under its parent and its ceiling')

    check(not dr.registered_ceiling_check(),
          'every rail\'s registered service ceiling still equals its '
          "parent's times the top of its band")

    # -----------------------------------------------------------
    # House style.
    # -----------------------------------------------------------
    here = os.path.dirname(os.path.abspath(__file__))
    dashed = []
    for name in ('carriage_mix.py', 'derived_rails.py',
                 'derived_rail_mirror.py', 'paramountplus_amazon.py',
                 'disambiguate_held_titles.py'):
        with open(os.path.join(here, name), encoding='utf-8') as fh:
            if '\u2014' in fh.read():
                dashed.append(name)
    check(not dashed,
          f'no em dashes in the carriage modules'
          f'{"" if not dashed else " (%s)" % dashed}')

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
