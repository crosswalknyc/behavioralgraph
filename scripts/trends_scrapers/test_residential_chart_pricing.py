#!/usr/bin/env python3
"""The residential pricing pass re-levels what moved, and only that.

Runs offline: the estimator, the reasoning calls and S3 are all
stubbed, so this covers the scoping, the no-op, the write path and the
restore without spending anything or touching the board.

    python3 -m scripts.trends_scrapers.test_residential_chart_pricing
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from . import residential_chart_pricing as rcp

_FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f'  ok   {name}')
        return
    _FAILURES.append(name)
    print(f'  FAIL {name}\n         got  {got!r}\n         want {want!r}')


_PRICED = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


class FakeEstimator:
    """Every attribute `reprice` and the scoping read, and nothing else."""

    def __init__(self, snapshots: dict, *, chart_result=None,
                 coherence_result=None, reclamped: int = 0):
        self._snapshots = snapshots
        self._chart_result = chart_result or {'charts': 0, 'titles': 0,
                                              'skipped': 0}
        self._coherence_result = coherence_result or {'rails': 0,
                                                      'moved': 0,
                                                      'held': 0}
        self._reclamped = reclamped
        self.saw_chart_sets: list[str] = []
        self.saw_coherence: list[str] = []

    # The declared charts, and where each one's rows live.
    def _charted_slugs(self):
        return [('netflix', 'Netflix'), ('max', 'HBO Max'),
                ('pluto', 'Pluto TV')]

    @staticmethod
    def published_chart_snapshot(slug: str) -> str:
        return {'pluto': 'pluto_popular'}.get(slug, slug)

    def _read_snapshot(self, source: str):
        return self._snapshots.get(source)

    # The three passes, recording the scope they were handed.
    def _reason_published_charts_as_sets(self, items, target_date_iso):
        self.saw_chart_sets = [s for s, _l in self._charted_slugs()]
        return dict(self._chart_result)

    def _reclamp_carried_to_platform_ceiling(self, items):
        return self._reclamped

    def _enforce_published_chart_coherence(self, items, target_date_iso):
        self.saw_coherence = [s for s, _l in self._charted_slugs()]
        return dict(self._coherence_result)


def board(**kw) -> dict:
    # `generated_at` is the estimator run that reasoned; `fetched_at`
    # is deliberately LATER here, because that is the shape the live
    # board had on 2026-09-25 and keying the scope to it is the bug
    # this test pins.
    out = {'generated_at': iso(_PRICED),
           'fetched_at': iso(_PRICED + timedelta(hours=11, minutes=33)),
           'target_date': '2026-09-24',
           'items': {'streaming:tv:lanterns': {'us_estimate': 412_883}}}
    out.update(kw)
    return out


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------

def test_scope() -> None:
    print('scope is the charts that moved since the board was priced')

    # Netflix and HBO Max re-scraped at 15:00 UTC, Pluto is on the
    # build box and has not moved since the 06:00 pass.
    se = FakeEstimator({
        'netflix': {'fetched_at': iso(_PRICED + timedelta(hours=9))},
        'max': {'chart_captured_at': iso(_PRICED + timedelta(hours=9))},
        'pluto_popular': {'fetched_at': iso(_PRICED - timedelta(hours=2))},
    })
    check('only the two that re-scraped',
          [s for s, _l in rcp.charts_moved_since_pricing(se, board())],
          ['netflix', 'max'])

    # The gate and this pass land seconds apart in the same lane.
    se2 = FakeEstimator({
        'netflix': {'fetched_at': iso(_PRICED + timedelta(seconds=30))},
        'max': {'fetched_at': iso(_PRICED)},
        'pluto_popular': {'fetched_at': iso(_PRICED)},
    })
    check('a same-run write is not a chart that moved',
          rcp.charts_moved_since_pricing(se2, board()), [])

    # A chart we cannot date is a chart we cannot prove is current.
    se3 = FakeEstimator({
        'netflix': {'fetched_at': 'not a date'},
        'max': {},
        'pluto_popular': {'fetched_at': iso(_PRICED)},
    })
    check('an undateable snapshot is included, a missing one is not',
          [s for s, _l in rcp.charts_moved_since_pricing(se3, board())],
          ['netflix'])

    # A board that has never been reasoned prices everything it sees.
    se4 = FakeEstimator({
        'netflix': {'fetched_at': iso(_PRICED)},
        'max': {'fetched_at': iso(_PRICED)},
        'pluto_popular': {'fetched_at': iso(_PRICED)},
    })
    check('an unstamped board prices every declared chart',
          [s for s, _l in rcp.charts_moved_since_pricing(
              se4, board(generated_at=None))],
          ['netflix', 'max', 'pluto'])


def test_scope_ignores_unrelated_writes() -> None:
    print('a write that touched no chart does not mark charts current')

    # The live shape on 2026-09-25: the estimator finished at 09:20,
    # the scrapes landed at 15:00, and a Wattpad re-price rewrote the
    # board at 17:33 without levelling a single chart. Keyed to
    # `fetched_at` this reports nothing to do, which is the bug.
    scraped = iso(_PRICED + timedelta(hours=9))
    se = FakeEstimator({'netflix': {'chart_captured_at': scraped},
                        'max': {'chart_captured_at': scraped},
                        'pluto_popular': {'fetched_at': iso(_PRICED)}})
    check('the re-scraped charts are still in scope',
          [s for s, _l in rcp.charts_moved_since_pricing(se, board())],
          ['netflix', 'max'])

    # Peacock's shape: the catalog pull writes the file at 06:00 and
    # the Mac merges the chart in at 17:00, stamping `chart_merged_at`
    # and leaving `fetched_at` alone. Reading `fetched_at` alone puts
    # a chart that moved nine hours ago out of scope.
    merged = FakeEstimator({
        'netflix': {'fetched_at': iso(_PRICED),
                    'chart_merged_at': scraped},
        'max': {'fetched_at': iso(_PRICED)},
        'pluto_popular': {'fetched_at': iso(_PRICED)}})
    check('a merged-in chart counts as moved',
          [s for s, _l in rcp.charts_moved_since_pricing(
              merged, board())], ['netflix'])
    check('the latest stamp wins, not the first one read',
          rcp.chart_moved_at({'fetched_at': iso(_PRICED),
                              'chart_captured_at': iso(
                                  _PRICED - timedelta(days=1)),
                              'chart_merged_at': scraped}),
          rcp._parse_iso(scraped))
    check('no stamp at all is undateable',
          rcp.chart_moved_at({'national': []}), None)

    # Once this pass has levelled them, its own per-slug stamp wins.
    levelled = {'netflix': iso(_PRICED + timedelta(hours=10)),
                'max': iso(_PRICED + timedelta(hours=10))}
    check('a second run in the same hour re-prices nothing',
          rcp.charts_moved_since_pricing(
              se, board(**{rcp.LEVELLED_FIELD: levelled})), [])

    # A narrowed run must not mark the charts it skipped as done.
    check('a chart this pass skipped is still in scope',
          [s for s, _l in rcp.charts_moved_since_pricing(
              se, board(**{rcp.LEVELLED_FIELD: {'netflix': iso(
                  _PRICED + timedelta(hours=10))}}))],
          ['max'])


# ---------------------------------------------------------------------------
# reprice
# ---------------------------------------------------------------------------

def _patched_write(monkey: dict):
    from scripts.trends_scrapers import _base
    real = _base.write_snapshot

    def fake(source, payload, **kw):
        monkey['source'] = source
        monkey['payload'] = payload
    _base.write_snapshot = fake
    return real


def _restore_write(real) -> None:
    from scripts.trends_scrapers import _base
    _base.write_snapshot = real


def test_reprice() -> None:
    print('reprice narrows the shared chart list and puts it back')
    snaps = {'stream_estimates': board(),
             'netflix': {'fetched_at': iso(_PRICED)},
             'max': {'fetched_at': iso(_PRICED)},
             'pluto_popular': {'fetched_at': iso(_PRICED)}}

    se = FakeEstimator(snaps, chart_result={'charts': 2, 'titles': 17,
                                            'skipped': 0},
                       coherence_result={'rails': 2, 'moved': 5,
                                         'held': 0})
    before = se._charted_slugs()
    wrote: dict = {}
    real = _patched_write(wrote)
    try:
        stats = rcp.reprice(se, slugs=['netflix', 'max'])
    finally:
        _restore_write(real)

    check('the chart pass saw only the scope',
          se.saw_chart_sets, ['netflix', 'max'])
    check('so did the coherence pass',
          se.saw_coherence, ['netflix', 'max'])
    check('the shared chart list is restored',
          se._charted_slugs(), before)
    check('it reasoned about the board\'s own day',
          stats['target_date'], '2026-09-24')
    check('and it wrote', stats['written'], True)
    check('through the shared path', wrote.get('source'),
          'stream_estimates')
    check('stamping when it ran',
          bool((wrote.get('payload') or {})
               .get('residential_charts_repriced_at')), True)
    check('carrying the readings forward',
          set((wrote.get('payload') or {}).get('items') or {}),
          {'streaming:tv:lanterns'})
    check('and stamping only the charts it levelled',
          sorted((wrote.get('payload') or {})
                 .get(rcp.LEVELLED_FIELD) or {}),
          ['max', 'netflix'])


def test_reprice_noop() -> None:
    print('a board that already descends is not rewritten')
    snaps = {'stream_estimates': board(),
             'netflix': {'fetched_at': iso(_PRICED)}}
    se = FakeEstimator(snaps)
    wrote: dict = {}
    real = _patched_write(wrote)
    try:
        stats = rcp.reprice(se, slugs=['netflix'])
    finally:
        _restore_write(real)
    check('nothing moved, nothing written', stats['written'], False)
    check('and the board was not touched', wrote, {})

    # A ceiling reclamp on its own is still a change worth writing.
    se2 = FakeEstimator(snaps, reclamped=3)
    wrote2: dict = {}
    real2 = _patched_write(wrote2)
    try:
        stats2 = rcp.reprice(se2, slugs=['netflix'])
    finally:
        _restore_write(real2)
    check('a reclamp alone writes', stats2['written'], True)


def test_reprice_restores_on_failure() -> None:
    print('a failing pass still puts the shared chart list back')

    class Boom(FakeEstimator):
        def _enforce_published_chart_coherence(self, items, day):
            raise RuntimeError('coherence blew up')

    se = Boom({'stream_estimates': board()},
              chart_result={'charts': 1, 'titles': 4, 'skipped': 0})
    before = se._charted_slugs()
    raised = ''
    try:
        rcp.reprice(se, slugs=['netflix'])
    except RuntimeError as e:
        raised = str(e)
    check('the failure surfaces', raised, 'coherence blew up')
    check('and the chart list is not left narrowed',
          se._charted_slugs(), before)


def test_dry_run() -> None:
    print('dry run reasons about nothing')
    se = FakeEstimator({'stream_estimates': board()})
    wrote: dict = {}
    real = _patched_write(wrote)
    try:
        stats = rcp.reprice(se, slugs=['max'], dry_run=True)
    finally:
        _restore_write(real)
    check('names the scope', stats.get('would_price'), ['max'])
    check('called nothing', se.saw_chart_sets, [])
    check('wrote nothing', wrote, {})


def test_empty_board() -> None:
    print('no stored readings is a no-op, not a crash')
    se = FakeEstimator({'stream_estimates': {'items': {}}})
    stats = rcp.reprice(se, slugs=['max'])
    check('nothing written', stats['written'], False)
    check('nothing reasoned', se.saw_chart_sets, [])


def main() -> int:
    for fn in (test_scope, test_scope_ignores_unrelated_writes,
               test_reprice, test_reprice_noop,
               test_reprice_restores_on_failure, test_dry_run,
               test_empty_board):
        fn()
    print()
    if _FAILURES:
        print(f'{len(_FAILURES)} failing: ' + ', '.join(_FAILURES))
        return 1
    print('all residential chart pricing checks pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
