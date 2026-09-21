#!/usr/bin/env python3
"""What the 90s section budget actually sees.

`compute_view` fans every card out into one thread pool and gives the
pool `SECTION_BUDGET_S` to finish; whatever misses renders as a loading
placeholder and is named in the ops alert. This probe runs the same
`streaming_trending` task the pool runs, on its own and then alongside
the streaming window read the rest of the request pays for, so the
section's wall clock can be read against the budget directly.

Reports cold (first call in the process, nothing cached) and warm
(every later call), because the poster cache lives in process memory
and a freshly started app pays the cold number once.

Read-only. No writes, no clickstream.
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import trends_iq as T  # noqa: E402


def main() -> int:
    use_index = os.environ.get('TRENDS_IQ_STREAM_WINDOW_INDEX', '1') != '0'
    T._STREAM_WINDOW_INDEX_ENABLED = use_index
    windows = [int(x) for x in (sys.argv[1:] or ['1', '7', '30'])]
    print(f'lean per-day index: {"on" if use_index else "off"}   '
          f'section budget: {T.SECTION_BUDGET_S}s', flush=True)

    for i, n in enumerate(windows):
        # The section task exactly as compute_view submits it.
        t0 = time.time()
        T._fetch_streaming_trending(None, n, keywords=None)
        solo = time.time() - t0
        state = 'cold' if i == 0 else 'warm'

        # The same task with the window read running beside it, which
        # is the pressure a second in-flight request puts on the pool.
        ex = ThreadPoolExecutor(max_workers=2)
        t0 = time.time()
        f_sec = ex.submit(T._fetch_streaming_trending, None, n, None)
        f_acc = ex.submit(T._accumulate_stream_estimates_over_window, n)
        futures_wait([f_sec, f_acc])
        together = time.time() - t0
        ex.shutdown(wait=False)

        flag = 'OVER BUDGET' if max(solo, together) >= T.SECTION_BUDGET_S \
            else 'inside budget'
        print(f'window={n:>2}d  streaming_trending alone {solo:7.2f}s '
              f'({state})   with the window read beside it '
              f'{together:7.2f}s   {flag}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
