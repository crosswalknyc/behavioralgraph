#!/usr/bin/env python3
"""Re-level the declared charts against the order they are showing now.

The phase problem
-----------------
The pricing pass runs at 06:00 UTC on the build box. The session-gated
platforms are scraped from the operator's Mac at 15:00 UTC, because
Disney+, HBO Max, Peacock, Netflix, Hulu and Starz all need a donated
residential session. So the charts change nine hours AFTER they were
last priced, and nothing re-prices against them until 06:00 UTC the
next morning.

For the fifteen hours in between, those rails render yesterday's
readings against today's order. On 2026-09-25 every declared chart
passed the sort test at 07:40 PT and five had degraded by 09:36 PT,
an hour after the re-scrape: HBO Max from nine descending pairs of
nine to six, Starz and Disney+ to seven, Netflix and Peacock to eight.
Nothing was wrong with the readings and nothing was wrong with the
scrape. They were describing two different days.

What this does
--------------
It closes the phase rather than the symptom: the set-level pricing
runs as part of the residential lane, right after the scrapes that
move the charts, on the same machine. Three passes, the same three
the nightly runs in the same order, over the same code:

  `_reason_published_charts_as_sets`   one call per chart, the whole
      chart sized together. This is the pass that can make a chart
      descend; the per-item pass cannot, because each of its calls
      sees one title and has nothing to be consistent with.
  `_reclamp_carried_to_platform_ceiling`
  `_enforce_published_chart_coherence` the readings descend across the
      positions the service gave, and nothing it left off the chart
      out-draws the title it ranks last.

The result is written through `_base.write_snapshot`, so the board
file, today's dated copy, the window index and the reasoning companion
all land together and the companion is merged rather than replaced.

Why this and not the other two
------------------------------
Moving the residential scrape ahead of 06:00 UTC would put it at
roughly 22:00 PT, and it would not actually fix anything: the charts
would still move at one hour and be priced at another, a wake-triggered
run would re-open the gap the moment the laptop opened, and Netflix's
daily rail is a morning publication. Keying a second Hetzner pass to
fire when the residential lane reports needs a signalling channel
between two machines, a trigger, and a second orchestrator invocation,
and it would price the charts from a box that cannot see them. Running
the pass where the scrape already runs adds one step to a lane that
already ends with one.

Scope comes from the data
-------------------------
A chart is in when the snapshot carrying it is NEWER than the board
was last priced, which is exactly the set that moved out from under
the last pass. That stays correct when the residential scraper list
changes, and it means a second run in the same hour re-prices nothing.
`--all` overrides it, `--slug` narrows it.

Ordering inside the lane
------------------------
After the coverage gate, not before. The gate is this lane's per-item
pass: a title new to today's chart has no reading at all, and the
chart-set pass drops a title it has no entry for, so running first
would size the chart over a partial set and leave the new title blank.
The nightly has its own per-item pass ahead of the chart sets and gets
the same ordering for the same reason.

Never queries clickstream.

    python3 -m scripts.trends_scrapers.residential_chart_pricing --dry-run
    python3 -m scripts.trends_scrapers.residential_chart_pricing --all
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# A chart whose snapshot is newer than its last levelling by less than
# this is the same run's write landing a moment apart, not a scrape
# that moved the chart. Keeps a lane that finishes the gate and this
# pass seconds apart from re-pricing itself.
_FRESHER_BY_SECONDS = 120

# When each chart was last sized as a set, per slug. Written by this
# pass; a slug with no entry falls back to the board's `generated_at`.
LEVELLED_FIELD = 'chart_levelled_at'


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_levelled(board: dict, slug: str) -> Optional[datetime]:
    """When this chart was last sized as a set.

    NOT `fetched_at`. That is the last time anyone wrote the board at
    all, and the board is written by several passes that never touch a
    chart: on 2026-09-25 a Wattpad re-price at 17:33 UTC left it
    stamped two hours after the 15:00 UTC scrapes, so a scope keyed to
    it would have reported every chart current while none of them had
    been re-levelled since 09:20.

    `generated_at` is the estimator run that actually reasoned, which
    is the right floor for a chart nothing has levelled since. This
    pass then stamps its own slugs, so a second run in the same hour
    re-prices nothing and a `--slug` run does not mark the charts it
    skipped as done.
    """
    per_slug = board.get(LEVELLED_FIELD)
    if isinstance(per_slug, dict):
        got = _parse_iso(per_slug.get(slug))
        if got is not None:
            return got
    return _parse_iso(board.get('generated_at'))


# Every way a snapshot records that its chart moved. All three are
# read and the LATEST wins, because they are not alternatives.
# Peacock is the case that forces it: `peacock.json` is written by the
# JustWatch catalog pull at 06:00 UTC and its chart is merged in from
# the operator's Mac at 17:00, and the merge stamps `chart_merged_at`
# without touching `fetched_at`. Reading `fetched_at` alone reports
# Peacock's chart nine hours older than it is, which is the same phase
# error this pass exists to close, one level down.
_MOVED_STAMPS = ('chart_merged_at', 'chart_captured_at', 'fetched_at')


def chart_moved_at(snap: dict) -> Optional[datetime]:
    """The latest stamp on a snapshot that means its chart changed."""
    seen = [_parse_iso(snap.get(f)) for f in _MOVED_STAMPS]
    seen = [d for d in seen if d is not None]
    return max(seen) if seen else None


def charts_moved_since_pricing(se, board: dict) -> list[tuple[str, str]]:
    """Declared charts whose snapshot is newer than their last levelling.

    A snapshot with no usable stamp is included rather than skipped: a
    chart we cannot date is a chart we cannot prove is current, and
    re-pricing one that did not move costs a call and changes nothing.
    """
    out: list[tuple[str, str]] = []
    for slug, label in se._charted_slugs():
        snap = se._read_snapshot(se.published_chart_snapshot(slug)) or {}
        if not snap:
            continue
        levelled = last_levelled(board, slug)
        if levelled is not None:
            got = chart_moved_at(snap)
            if got is not None and (got - levelled).total_seconds() \
                    <= _FRESHER_BY_SECONDS:
                continue
        out.append((slug, label))
    return out


def reprice(se, *, slugs: Optional[list[str]] = None,
            dry_run: bool = False) -> dict:
    """Run the three chart passes and write the result.

    `slugs` restricts `_charted_slugs` for the duration, which is how
    this pass is scoped without changing the shared functions it
    borrows. Restored in a finally, so a failure cannot leave the
    module reporting a narrowed chart list to the next caller.
    """
    from scripts.trends_scrapers import _base

    board = se._read_snapshot('stream_estimates') or {}
    items = board.get('items') or {}
    if not items:
        logger.warning('no stored readings to re-level; nothing to do')
        return {'charts': 0, 'titles': 0, 'moved': 0, 'written': False}

    # Reason about the day the stored board is about, so this corrects
    # that day's readings rather than opening a new one.
    target_date_iso = board.get('target_date') or (
        datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    original = se._charted_slugs
    if slugs is not None:
        want = {s for s in slugs}
        se._charted_slugs = lambda: [(s, l) for s, l in original()
                                     if s in want]
    # Captured while the narrowing is in place, because the stamp
    # below has to name the charts this run actually levelled and the
    # finally puts the shared list back before it is written.
    in_scope = [s for s, _l in se._charted_slugs()]
    stats = {'charts': 0, 'titles': 0, 'skipped': 0, 'rails': 0,
             'moved': 0, 'held': 0, 'reclamped': 0, 'written': False,
             'target_date': target_date_iso}
    try:
        if dry_run:
            stats['would_price'] = in_scope
            return stats

        # One call per chart, the whole chart sized together. Anything
        # that fails here is non-fatal by construction inside the
        # function: the rail keeps the readings it had.
        cs = se._reason_published_charts_as_sets(items, target_date_iso)
        stats['charts'] = cs.get('charts', 0)
        stats['titles'] = cs.get('titles', 0)
        stats['skipped'] = cs.get('skipped', 0)

        stats['reclamped'] = se._reclamp_carried_to_platform_ceiling(
            items) or 0

        pc = se._enforce_published_chart_coherence(items, target_date_iso)
        stats['rails'] = pc.get('rails', 0)
        stats['moved'] = pc.get('moved', 0)
        stats['held'] = pc.get('held', 0)
    finally:
        se._charted_slugs = original

    if not (stats['titles'] or stats['moved'] or stats['reclamped']):
        logger.info('every declared chart already descends against the '
                    'order it is showing; nothing written')
        return stats

    board['items'] = items
    board['count'] = len(items)
    board.setdefault('target_date', target_date_iso)
    now_iso = datetime.now(timezone.utc).isoformat()
    board['residential_charts_repriced_at'] = now_iso
    # Per slug, so tomorrow's scope asks the right question of each
    # chart and a narrowed run does not mark the rest as levelled.
    levelled = dict(board.get(LEVELLED_FIELD) or {})
    for slug in in_scope:
        levelled[slug] = now_iso
    board[LEVELLED_FIELD] = levelled
    # The shared write path: board file, today's dated copy, the window
    # index, and the reasoning companion merged rather than replaced.
    _base.write_snapshot('stream_estimates', board)
    stats['written'] = True
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description='Re-level the declared charts after the residential '
                    'scrapes move them')
    ap.add_argument('--dry-run', action='store_true',
                    help='report which charts would be re-levelled')
    ap.add_argument('--all', action='store_true',
                    help='every declared chart, not only the ones whose '
                         'snapshot is newer than the priced board')
    ap.add_argument('--slug', action='append', default=[],
                    help='restrict to these services (repeatable)')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    from scripts.trends_scrapers import stream_estimates as se

    board = se._read_snapshot('stream_estimates') or {}
    if args.slug:
        declared = {s for s, _l in se._charted_slugs()}
        unknown = [s for s in args.slug if s not in declared]
        if unknown:
            print(f'not a service that publishes a chart: '
                  f'{", ".join(unknown)}')
            return 2
        scope = list(args.slug)
        why = 'named on the command line'
    elif args.all:
        scope = [s for s, _l in se._charted_slugs()]
        why = 'every declared chart'
    else:
        moved = charts_moved_since_pricing(se, board)
        scope = [s for s, _l in moved]
        why = 'snapshot newer than the last pricing pass'

    print(f'board last reasoned : '
          f'{board.get("generated_at") or "unknown"}')
    print(f'board target day    : {board.get("target_date") or "unknown"}')
    print(f'charts in scope ({why}): {len(scope)}')
    for s in scope:
        print(f'   {s}')
    if not scope:
        print('no chart has moved since the board was priced; nothing '
              'to do')
        return 0

    stats = reprice(se, slugs=scope, dry_run=args.dry_run)
    if args.dry_run:
        print('dry-run: nothing reasoned, nothing written')
        return 0

    print(f'sized as a set      : {stats["charts"]} chart(s), '
          f'{stats["titles"]} title(s) re-levelled, '
          f'{stats["skipped"]} left to their per-item values')
    print(f'put back under cap  : {stats["reclamped"]} reading(s)')
    print(f'coherence           : {stats["moved"]} reading(s) moved '
          f'across {stats["rails"]} rail(s), {stats["held"]} held')
    print(f'written             : {stats["written"]}')

    if stats['written']:
        try:
            import trends_iq
            n = trends_iq.invalidate_live_compute_view_caches()
            print(f'purged {n} live cache entries')
        except Exception:
            logger.exception('cache purge failed (non-fatal)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
