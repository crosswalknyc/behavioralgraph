#!/usr/bin/env python3
"""Regression tests for the 2026-09-15 Trends IQ incident.

Covers the two defects that took the board down and the three guards
added so the same shape of failure reports itself next time:

  1. `_base.run_scraper` must not publish an empty accumulator over a
     good one when a fetch raises. A truncated line in the batch
     results stream wrote a 255-byte stub over an 18,009-item store,
     and every downstream row fell back to a rank-derived value.
  2. `stream_estimates._iter_batch_results` must survive an
     undecodable line instead of taking the whole estimator with it.
  3. `run_guard.RunLock` must refuse a second concurrent run.
  4. `run_guard.check_baseline_share` must alert past its thresholds,
     counting carried-forward and rank-derived rows separately
     and stay quiet at or near zero.

Run: python3 scripts/test_trends_run_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.trends_scrapers import _base                      # noqa: E402
from scripts.trends_scrapers import run_guard                   # noqa: E402
from scripts.trends_scrapers import stream_estimates as se      # noqa: E402

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        _failures.append(label)


# ---------------------------------------------------------------------------
def test_failed_fetch_preserves_accumulator() -> None:
    print("\n[1] a failed fetch must not wipe the accumulated store")
    prior = {
        'source': 'stream_estimates',
        'items': {f'song:track {i}': {'us_estimate': 1000 + i}
                  for i in range(18009)},
        'count': 18009,
        'target_date': '2026-09-13',
        'generated_at': '2026-09-14T14:58:40+00:00',
    }
    written: dict = {}

    orig_read, orig_write = _base.read_snapshot, _base.write_snapshot
    _base.read_snapshot = lambda source: prior          # type: ignore[assignment]
    _base.write_snapshot = lambda source, payload, **kw: written.update(  # type: ignore[assignment]
        {'source': source, 'payload': payload})
    try:
        def _boom() -> dict:
            raise json.JSONDecodeError('Unterminated string', '{"a"', 4)

        out = _base.run_scraper('stream_estimates', 'US Streams', 'meta', _boom)
    finally:
        _base.read_snapshot, _base.write_snapshot = orig_read, orig_write

    payload = written.get('payload') or {}
    check(bool(out.get('error')), "the error is still recorded")
    check(len(payload.get('items') or {}) == 18009,
          "all 18,009 prior items survive the failure")
    check(payload.get('count') == 18009, "count matches the preserved store")
    check(payload.get('error_preserved_prior_items') is True,
          "the payload is flagged as carrying prior content")
    check(payload.get('target_date') == '2026-09-13',
          "target_date carries forward")
    check(payload.get('national') == [],
          "national still reads empty, so a dark scraper still reads dark")


def test_no_prior_still_writes_error() -> None:
    print("\n[2] with no prior snapshot the error payload is unchanged")
    written: dict = {}
    orig_read, orig_write = _base.read_snapshot, _base.write_snapshot
    _base.read_snapshot = lambda source: None            # type: ignore[assignment]
    _base.write_snapshot = lambda source, payload, **kw: written.update(  # type: ignore[assignment]
        {'payload': payload})
    try:
        _base.run_scraper('some_scraper', 'X', 'meta',
                          lambda: (_ for _ in ()).throw(RuntimeError('nope')))
    finally:
        _base.read_snapshot, _base.write_snapshot = orig_read, orig_write
    p = written.get('payload') or {}
    check('RuntimeError' in (p.get('error') or ''), "the error is recorded")
    check(not p.get('error_preserved_prior_items'),
          "nothing is flagged as preserved when there was nothing to preserve")


def test_successful_fetch_unaffected() -> None:
    print("\n[3] a successful fetch is untouched by the guard")
    written: dict = {}
    orig_read, orig_write = _base.read_snapshot, _base.write_snapshot
    _base.read_snapshot = lambda source: {'items': {'stale': 1}}   # type: ignore[assignment]
    _base.write_snapshot = lambda source, payload, **kw: written.update(  # type: ignore[assignment]
        {'payload': payload})
    try:
        _base.run_scraper('s', 'X', 'meta',
                          lambda: {'items': {'fresh': 2}, 'count': 1})
    finally:
        _base.read_snapshot, _base.write_snapshot = orig_read, orig_write
    p = written.get('payload') or {}
    check(p.get('items') == {'fresh': 2},
          "fresh items win; no stale merge on the success path")
    check(p.get('error') is None, "no error on the success path")


# ---------------------------------------------------------------------------
def test_batch_iter_survives_bad_line() -> None:
    print("\n[4] one undecodable line must not lose the whole stream")

    def _stream():
        yield 'a'
        yield 'b'
        raise json.JSONDecodeError('Unterminated string', 'x' * 240, 240)

    got = list(se._iter_batch_results(_stream(), 'msgbatch_test'))
    check(got == ['a', 'b'],
          "results decoded before the bad line are kept, not discarded")

    class _Flaky:
        """Raises once in the middle, then keeps yielding."""
        def __init__(self):
            self.n = 0

        def __next__(self):
            self.n += 1
            if self.n == 2:
                raise json.JSONDecodeError('bad', '{', 1)
            if self.n > 4:
                raise StopIteration
            return self.n

        def __iter__(self):
            return self

    got2 = list(se._iter_batch_results(_Flaky(), 'msgbatch_test'))
    check(got2 == [1, 3, 4],
          "a single bad line is skipped and reading continues")


def test_batch_iter_gives_up_on_garbage() -> None:
    print("\n[5] a stream that turns to garbage stops cleanly")

    class _AllBad:
        def __next__(self):
            raise json.JSONDecodeError('bad', '{', 1)

        def __iter__(self):
            return self

    got = list(se._iter_batch_results(_AllBad(), 'msgbatch_test'))
    check(got == [], "no results, and no exception escapes")


# ---------------------------------------------------------------------------
def test_run_lock_blocks_second_run() -> None:
    print("\n[6] the run lock admits one run at a time")
    sent: list[str] = []
    orig_send = run_guard.send_alert
    run_guard.send_alert = lambda tag, subject, body, **kw: (  # type: ignore[assignment]
        sent.append(tag) or True)
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'run.lock')
            with run_guard.RunLock(path) as first:
                check(first.acquired, "the first run takes the lock")
                with run_guard.RunLock(path) as second:
                    check(not second.acquired, "the second run is refused")
                check('overlapping_run' in sent or 'stale_run' in sent,
                      "the refused run raises an alert")
            with run_guard.RunLock(path) as third:
                check(third.acquired, "the lock frees on exit")
    finally:
        run_guard.send_alert = orig_send


def test_provenance_share_alarm() -> None:
    print("\n[7] the quality alarm separates carried from rank-derived")
    sent: list[tuple[str, str]] = []
    orig_send = run_guard.send_alert
    run_guard.send_alert = lambda tag, subject, body, **kw: (  # type: ignore[assignment]
        sent.append((tag, subject)) or True)
    try:
        clean = {'total': 12285, 'carried_after': 41,
                 'rank_tier_after': 3, 'by_list': {}}
        got = run_guard.check_baseline_share(clean)
        check(got is not None and got['rank_tier_pct'] < 0.1,
              "a clean board measures ~0% on the rank tier")
        check(got is not None and got['carried_pct'] < 1.0,
              "a clean board carries a handful of rows")
        check(not sent, "a clean board sends nothing")

        # The 2026-09-15 shape: the store was lost, so nearly every row
        # had no reading anywhere and fell to the rank tier.
        broken = {
            'total': 12285,
            'carried_after': 0,
            'rank_tier_after': 9520,
            'by_list': {
                'fast.roku': {'total': 619, 'carried': 0, 'rank_tier': 619,
                               'carried_pct': 0.0, 'rank_tier_pct': 100.0},
                'gaming.xbox_gamepass': {'total': 94, 'carried': 0,
                                          'rank_tier': 94,
                                          'carried_pct': 0.0,
                                          'rank_tier_pct': 100.0},
            },
        }
        got2 = run_guard.check_baseline_share(broken)
        check(got2 is not None and 77.0 < got2['rank_tier_pct'] < 78.0,
              f"a lost store measures ~77.5% rank tier (got {got2})")
        check(len(sent) == 1, "exactly one alert fires")
        check(sent[0][0] == 'rank_tier_share', "it is the rank-tier alert")
        check('placeholder' in sent[0][1].lower(),
              "the subject says what is wrong in plain words")

        # A pricing pass that ran out of time: every row keeps its own
        # last reading. Alertable, but a different and milder thing.
        sent.clear()
        stale = {
            'total': 12285,
            'carried_after': 6140,
            'rank_tier_after': 4,
            'by_list': {
                'streaming.netflix': {'total': 200, 'carried': 190,
                                       'rank_tier': 0,
                                       'carried_pct': 95.0,
                                       'rank_tier_pct': 0.0},
            },
        }
        got3 = run_guard.check_baseline_share(stale)
        check(got3 is not None and 49.0 < got3['carried_pct'] < 51.0,
              f"a stale board measures ~50% carried (got {got3})")
        check(len(sent) == 1 and sent[0][0] == 'carried_share',
              "the carried alert fires and the rank-tier one does not")
        check('older reading' in sent[0][1].lower(),
              "the subject says the board is old, not wrong")
    finally:
        run_guard.send_alert = orig_send

    check(run_guard.RECIPIENTS == ['jenna@crosswalknyc.com',
                                    'jessie@crosswalknyc.com'],
          "alerts go to jenna and jessie only, never liz")


if __name__ == '__main__':
    print("Trends IQ run-guard regression suite")
    test_failed_fetch_preserves_accumulator()
    test_no_prior_still_writes_error()
    test_successful_fetch_unaffected()
    test_batch_iter_survives_bad_line()
    test_batch_iter_gives_up_on_garbage()
    test_run_lock_blocks_second_run()
    test_provenance_share_alarm()
    print(f"\n{'ALL PASS' if not _failures else str(len(_failures)) + ' FAILED'}")
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1 if _failures else 0)
