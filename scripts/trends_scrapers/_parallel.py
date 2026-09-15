"""Shared fan-out helper for the Trends IQ scrapers.

Two scrapers in this package spend nearly all of their wall clock
waiting on `api.anthropic.com`: `headline_estimates` (one web_search
call per headline) and `lens_relevance` (one batch-scoring call per
25 items per persona lens). Both are pure IO wait, so the fix is more
threads in flight, not faster code.

This module centralises the three things both of them need:

  * `worker_count()`  - read the worker count from an env var, with a
                        one-step global kill switch back to sequential.
  * `imap_unordered()`- run a callable over a work list and yield
                        results as they land. A worker count of 1 runs
                        a plain in-process loop with no pool at all,
                        so the sequential path stays genuinely
                        sequential and stays debuggable.
  * `call_with_backoff()` - retry the transient API failures (429,
                        529 overloaded, connection resets, read
                        timeouts) with salted exponential backoff, and
                        fail fast on everything else.

Rate-limit context, measured against the trends key on 2026-09-15 by
reading the `anthropic-ratelimit-*` response headers directly:

    requests       10,000 / min
    input tokens   10,000,000 / min
    output tokens   2,000,000 / min

The nightly run's peak draw sits around 1-2% of those ceilings, so the
account limit is not what bounds throughput. The defaults in the two
callers are set from measured throughput instead, and every one of
them is env-tunable so a bad night can be walked back without a
deploy.

Concurrency is NOT a spend multiplier. The same number of calls go out
either way; they just overlap. The only new spend is a retry that
would not have fired sequentially, and retries only fire on a failure
that would otherwise have lost the item outright.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import random
import time
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')
R = TypeVar('R')


# Flip this to 1 (or true/yes/on) in the environment and EVERY scraper
# in this package drops back to a sequential in-process loop. This is
# the documented one-step revert: no deploy, no code change, no
# per-scraper env var to remember at 4am.
SEQUENTIAL_ENV = 'TRENDS_SCRAPERS_SEQUENTIAL'

_TRUTHY = ('1', 'true', 'yes', 'on')


def sequential_forced() -> bool:
    return (os.environ.get(SEQUENTIAL_ENV) or '').strip().lower() in _TRUTHY


def worker_count(env_name: str, default: int, *, maximum: int = 128) -> int:
    """Resolve the worker count for one scraper.

    Precedence: the global sequential kill switch, then the scraper's
    own env var, then the measured default. Always returns >= 1, so a
    garbage env value degrades to the default rather than to zero
    workers (which would hang the phase).
    """
    if sequential_forced():
        logger.info("%s: %s set, running sequentially", env_name, SEQUENTIAL_ENV)
        return 1
    raw = (os.environ.get(env_name) or '').strip()
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return min(n, maximum)
            logger.warning("%s=%r is below 1; using default %d",
                           env_name, raw, default)
        except ValueError:
            logger.warning("%s=%r is not an integer; using default %d",
                           env_name, raw, default)
    return max(1, min(default, maximum))


def imap_unordered(fn: Callable[[T], R],
                   items: Sequence[T],
                   workers: int,
                   *,
                   label: str = 'pool',
                   result_timeout: Optional[float] = None
                   ) -> Iterator[tuple[T, Optional[R], Optional[BaseException]]]:
    """Apply `fn` to every item, yielding `(item, result, error)`.

    One bad item can never take the phase down: an exception raised by
    `fn` comes back on that item's tuple as `error` and the rest of the
    work carries on. Callers decide whether to log, skip, or substitute.

    `workers <= 1` runs a plain loop and never creates a pool, so the
    sequential fallback behaves exactly like the pre-parallel code path
    (same order, same single-threaded semantics, trivially debuggable).
    """
    if not items:
        return
    if workers <= 1:
        for it in items:
            try:
                yield it, fn(it), None
            except BaseException as e:      # noqa: BLE001 - isolation is the point
                yield it, None, e
        return

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix=label) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for fut in concurrent.futures.as_completed(futs):
            it = futs[fut]
            try:
                yield it, fut.result(timeout=result_timeout), None
            except BaseException as e:      # noqa: BLE001
                yield it, None, e


# ---------------------------------------------------------------------------
# Transient-failure retry
# ---------------------------------------------------------------------------
# Matched against `repr(exc)` lower-cased. Anything not on this list is
# treated as a real error and raised immediately, so a bad prompt or a
# revoked key fails fast instead of burning the retry budget.
_RETRYABLE_MARKERS = (
    'rate_limit', 'ratelimit', '429',
    'overloaded', '529',
    'timeout', 'timed out',
    'connection', 'connectionerror', 'remote end closed',
    'apiconnectionerror', 'internalservererror', '500', '502', '503',
)


def is_retryable(exc: BaseException) -> bool:
    blob = f'{type(exc).__name__} {exc!r}'.lower()
    return any(m in blob for m in _RETRYABLE_MARKERS)


def backoff_delay(base: float, attempt: int, cap: float, salt: str) -> float:
    """Exponential backoff with a deterministic per-caller jitter.

    The jitter is salted off `salt` rather than drawn fresh so that N
    workers that hit the same 429 in the same instant do not all wake
    up together and re-stampede the endpoint.
    """
    span = min(cap, base * (2 ** attempt))
    h = hashlib.sha256(f'{salt}|{attempt}'.encode('utf-8')).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF          # 0.0 - 1.0, deterministic
    return span * (0.5 + 0.5 * frac) + random.uniform(0, 0.25)


def call_with_backoff(fn: Callable[[], R],
                      *,
                      attempts: int = 2,
                      base_delay: float = 2.0,
                      max_delay: float = 30.0,
                      salt: str = '',
                      label: str = '') -> R:
    """Call `fn`, retrying only the transient API failures.

    `attempts` counts TOTAL tries, not retries, so `attempts=2` keeps
    the two-try behaviour the scrapers already had. Non-retryable
    errors raise on the first try. The final attempt's exception
    propagates to the caller.
    """
    last: BaseException
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except BaseException as e:          # noqa: BLE001
            last = e
            if attempt >= attempts - 1 or not is_retryable(e):
                raise
            delay = backoff_delay(base_delay, attempt, max_delay,
                                  salt or label or 'trends')
            logger.info("%s: retryable (%s); backing off %.1fs "
                        "before attempt %d/%d",
                        label or 'call', type(e).__name__, delay,
                        attempt + 2, attempts)
            time.sleep(delay)
    raise last  # pragma: no cover - loop always returns or raises


def content_key(*parts: Any) -> str:
    """Stable short hash over the parts that actually feed a model call.

    Used as a cache key so work is only redone when the inputs to that
    work changed. Deterministic across processes and across days: no
    PYTHONHASHSEED dependence, no wall-clock, no ordering surprises.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p if p is not None else '').encode('utf-8'))
        h.update(b'\x1f')
    return h.hexdigest()[:20]
