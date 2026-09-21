#!/usr/bin/env python3
"""Break `streaming_trending`'s wall clock into its parts.

`bench_section_budget.py` says how long the section takes. This says
where the time goes, so a fix can be aimed rather than guessed.

Wraps every candidate cost centre inside `_fetch_streaming_trending`
and reports, per centre: how many times it was called, the wall clock
the section actually spent inside it, and the summed time across
worker threads (which exceeds the wall clock wherever a thread pool is
doing the work, and is the number that tells you how much total work
was performed).

Cold is the first call in a fresh process with every in-process cache
empty, which is what a freshly deployed worker serves. Warm is every
call after it.

Read-only. No writes, no clickstream.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import trends_iq as T  # noqa: E402

# name -> [calls, summed seconds across all threads]
_STATS: dict[str, list] = defaultdict(lambda: [0, 0.0])
_LOCK = threading.Lock()
# Wall clock the main section thread spent inside a top-level centre.
_WALL: dict[str, float] = defaultdict(float)


_MAIN = threading.current_thread()


def _wrap(name: str, fn, wall: bool = False):
    """Count and time every call to `fn` under `name`.

    Only calls made on the thread running the section count towards
    its wall clock. A cost centre that got moved into a pool is still
    counted and still timed, but its summed time is work performed
    rather than time the section stood still, and adding it to the
    wall would take the total past what the section actually took.
    """
    def inner(*a, **kw):
        t0 = time.time()
        try:
            return fn(*a, **kw)
        finally:
            dt = time.time() - t0
            with _LOCK:
                _STATS[name][0] += 1
                _STATS[name][1] += dt
                if wall and threading.current_thread() is _MAIN:
                    _WALL[name] += dt
    return inner


def install() -> None:
    """Patch the cost centres. Every one is looked up as a module
    global at call time, so rebinding on the module is enough."""
    # Top level, called straight from the section body. These sum to
    # the section's own wall clock.
    for n in ('_read_snapshot', '_read_snapshot_nearest',
              '_annotate_streaming_weeks', '_enrich_streaming_with_posters',
              '_snapshot_items_for_geo', '_split_streaming_items',
              '_merge_streaming_depth'):
        setattr(T, n, _wrap(n, getattr(T, n), wall=True))
    # Inside the above. These show what the top-level time is made of.
    for n in ('_load_streaming_history_weeks', '_wiki_poster_lookup',
              '_tvmaze_poster_lookup', '_wiki_opensearch_titles',
              '_wiki_summary_thumb', '_wiki_pageimages_thumb',
              '_itunes_poster_lookup'):
        if hasattr(T, n):
            setattr(T, n, _wrap(n, getattr(T, n)))


def _reset() -> None:
    with _LOCK:
        _STATS.clear()
        _WALL.clear()


def _report(label: str, total: float) -> None:
    print(f'\n  {label}: {total:.2f}s total')
    print(f'    {"cost centre":<34} {"calls":>7} {"section wall":>13} '
          f'{"work across threads":>20}')
    rows = sorted(_STATS.items(), key=lambda kv: -kv[1][1])
    for name, (calls, secs) in rows:
        if calls == 0:
            continue
        w = _WALL.get(name)
        wall_s = f'{w:12.2f}s' if w else f'{"":>13}'
        print(f'    {name:<34} {calls:>7} {wall_s} {secs:19.2f}s')
    accounted = sum(_WALL.values())
    print(f'    {"(section wall accounted for)":<34} {"":>7} '
          f'{accounted:12.2f}s')
    print(f'    {"(other: pool waits + shaping)":<34} {"":>7} '
          f'{total - accounted:12.2f}s')


def main() -> int:
    windows = [int(x) for x in (sys.argv[1:] or ['7'])]
    install()
    print(f'streaming_trending cost breakdown   '
          f'section budget: {T.SECTION_BUDGET_S}s', flush=True)

    for i, n in enumerate(windows):
        for state in ('cold', 'warm'):
            if state == 'warm' and i > 0:
                continue
            _reset()
            t0 = time.time()
            T._fetch_streaming_trending(None, n, keywords=None)
            total = time.time() - t0
            _report(f'window={n}d {state}', total)
            if i > 0:
                break
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
