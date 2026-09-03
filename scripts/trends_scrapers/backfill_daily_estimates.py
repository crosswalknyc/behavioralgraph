"""
Backfill daily `stream_estimates.json` snapshots from a start date
through today, one calendar day at a time.

Each dated snapshot lives at
`s3://dashboard-inputs/trends_iq_snapshots/{YYYY-MM-DD}/stream_estimates.json`
and represents the unique US audience for THAT calendar day (the
research prompt names the specific date, and every returned number
is a daily count - see the header of `stream_estimates.py`). The
window accumulator in `trends_iq._accumulate_stream_estimates_over_window`
sums N of these daily snapshots to produce a plain-sum window count
with no multiplier or decay factor.

This script fills gaps for days that don't already have a snapshot
(or that carry an obviously-stale one from the pre-daily era) so the
plain-sum accumulator has real coverage back to the start date.
Skips days whose dated snapshot already exists and looks daily by
construction (has a `target_date` key matching the day's ISO date
AND non-empty `items`); overwrite with `--force-refresh`.

Uses the canonical `stream_estimates.fetch_for_date(target_date, only)`
research path - there is NO second research implementation. The
backfill script is a thin loop over calendar days, no forked prompts.

PARALLELISM (added 2026-09-03)
------------------------------
The workspace has a dedicated Anthropic key pool at
`.env.avid_skins` (~51 keys) plus a loader at
`migration/avid_key_pool.py`, following the pattern documented in
`avid-and-cut-skin-rules.mdc` section 6. Each worker process pins a
distinct key to ``ANTHROPIC_API_KEY`` in its own environment BEFORE
importing `stream_estimates`, so per-key rate-limit budgets scale
linearly with worker count.

Sharding is by CALENDAR DAY (not by item within a day). Every task
is a small contiguous list of ISO dates the worker processes in
sequence via `fetch_for_date`. Each task's output is atomic (one
dated snapshot per date, written by the same worker that fetched
it), so there is never a partial per-day file that would need to be
merged. Resume works day-by-day inside the worker: the worker checks
S3 for a daily-era snapshot at the start of each date and skips if
already present.

Usage:

    # Backfill last 7 days as a smoke test (single worker, no pool).
    python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-08-27 --workers 1

    # Full parallel backfill to June 1, 50 workers from the key pool.
    nohup python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-06-01 --workers 50 \\
        > /var/log/backfill_daily_estimates_parallel.log 2>&1 &
    disown

    # Dry-run to report gaps + estimated cost without touching Claude
    # or S3.
    python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-06-01 --dry-run

    # Force re-research even where a snapshot exists.
    python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-08-27 --force-refresh

    # Restrict to specific kinds (comma-separated).
    python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-08-27 --only fast_channel,song

Guardrails:

  - Never overwrites a `latest/` key. Only writes to
    `trends_iq_snapshots/{YYYY-MM-DD}/stream_estimates.json`.
  - Idempotent: re-running on the same window with no --force-refresh
    is a no-op for any day already covered.
  - Never raises on a per-day failure (research call flakes, S3 hiccup,
    missing input snapshot). Logs the failure, moves on to the next
    day. Full log ends with a summary of how many days succeeded /
    skipped / failed.
  - Local ops flags only (--since / --dry-run / --only /
    --force-refresh / --workers / --dates-per-worker / --sleep-seconds
    / --pool-path). Never exposed on any dashboard or API surface
    per `no-external-overrides.mdc`.
  - Kinds that didn't exist on earlier dates degrade gracefully:
    `fast_channel` was added mid-August, so before that date the
    `fast_channels.json` / `fast_channel_lineups.json` snapshots
    used by the collector return empty and no fast_channel items
    get researched. The dated snapshot for that day still writes,
    just without a `fast_channel` block. The window accumulator on
    the app side simply uses a shorter effective window for those
    items and the frontend renders as many days as have coverage.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_S3_BUCKET = 'dashboard-inputs'
_S3_DATED  = 'trends_iq_snapshots/{date}/stream_estimates.json'


# CLI aliases mirroring stream_estimates.py so operators can pass the
# same short names in both places (e.g. --only goodreads,wattpad).
_RAIL_ALIASES = {
    'goodreads_most_read':  'goodreads_book',
    'goodreads':            'goodreads_book',
    'wattpad':              'wattpad_story',
    'wattpad_hot':          'wattpad_story',
    'wattpad_originals':    'wattpad_story',
    'wattpad_romance':      'wattpad_story',
    'wattpad_teen_fiction': 'wattpad_story',
    'wattpad_fanfiction':   'wattpad_story',
    'wattpad_fantasy':      'wattpad_story',
}


def _s3():
    return boto3.client(
        's3',
        region_name=(os.environ.get('AWS_REGION') or 'us-east-2'),
    )


def _existing_snapshot_is_daily(target_date_iso: str) -> bool:
    """Return True when the dated snapshot for `target_date_iso`
    already exists in S3 AND carries a `target_date` field matching
    the day (i.e. was written by a daily-era run, not the pre-2026-09-03
    weekly era). False when missing, empty, or from the weekly era."""
    key = _S3_DATED.format(date=target_date_iso)
    try:
        resp = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code')
        if code in ('NoSuchKey', '404'):
            return False
        logger.info("s3 head %s: %s", key, e)
        return False
    except Exception as e:
        logger.info("s3 head %s: %s", key, e)
        return False
    try:
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return False
    if not payload.get('items'):
        return False
    # A daily-era snapshot has `target_date` set to the day's ISO.
    # A pre-daily-era snapshot (weekly research) will be missing this
    # key entirely. Treat missing / mismatched as a gap that should
    # backfill so the window accumulator gets real daily coverage.
    return payload.get('target_date') == target_date_iso


def _iter_dates(start_iso: str, end_iso: str):
    """Yield ISO dates from start through end inclusive, newest-first
    (newest days matter most for the live dashboard - if the backfill
    is interrupted, we still have the freshest N days)."""
    start = date.fromisoformat(start_iso)
    end   = date.fromisoformat(end_iso)
    if end < start:
        start, end = end, start
    d = end
    while d >= start:
        yield d.isoformat()
        d = d - timedelta(days=1)


def _estimate_cost_note(num_days: int) -> str:
    """Very rough guidance for the log so an operator can budget the
    run. ~1200 items x $0.02/item ~ $24/day at the current caps; a
    92-day backfill (Jun 1 -> Sep 1) ~ $2,200. Actual will vary with
    intra-day cache hits and per-kind cap tuning."""
    per_day_low  = 15
    per_day_high = 35
    return (f"~${num_days * per_day_low}-${num_days * per_day_high} "
            f"in AI usage across {num_days} day(s).")


def _chunk_dates(dates: list[str], size: int) -> list[list[str]]:
    """Split `dates` into contiguous chunks of `size` each. The last
    chunk may be shorter."""
    size = max(1, int(size))
    return [dates[i:i + size] for i in range(0, len(dates), size)]


# --------------------------------------------------------------------
# Multi-key worker pool
# --------------------------------------------------------------------
# Pattern documented in `avid-and-cut-skin-rules.mdc` section 6 and
# proven in `scripts/backfill_avid_fans_all.py`. Each worker process
# pops one key from a shared queue in its initializer, pins it to
# ANTHROPIC_API_KEY in its own environment BEFORE importing
# stream_estimates (which imports claude_client on first fetch), and
# holds that key for the process lifetime. Per-key Anthropic rate
# limits therefore scale linearly with worker count.

def _load_pool_keys(pool_path: Optional[str]) -> list[str]:
    """Load the multi-key Anthropic pool via `migration/avid_key_pool`.
    Falls back to a single-element list containing ANTHROPIC_API_KEY
    when the pool is missing or empty. Returns [] only if no key is
    available anywhere."""
    # `migration/` sits at the repo root. Try the on-Hetzner canonical
    # path first, then walk up from this file.
    candidates: list[str] = []
    here = os.path.dirname(os.path.abspath(__file__))
    # bg-webapp/scripts/trends_scrapers -> bg-webapp/scripts -> bg-webapp
    #  -> <repo root> -> <repo root>/migration
    for depth in range(1, 6):
        cand = os.path.normpath(os.path.join(here, *(['..'] * depth), 'migration'))
        if os.path.isdir(cand):
            candidates.append(cand)
    # Hetzner canonical.
    candidates.append('/root/finished_codes/migration')
    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if c not in sys.path:
            sys.path.insert(0, c)
    try:
        from avid_key_pool import load_keys  # type: ignore
    except Exception as e:
        logger.warning("avid_key_pool import failed: %s; "
                        "falling back to single ANTHROPIC_API_KEY", e)
        primary = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
        return [primary] if primary else []
    try:
        return list(load_keys(pool_path) or [])
    except Exception as e:
        logger.warning("avid_key_pool.load_keys failed: %s; "
                        "falling back to single ANTHROPIC_API_KEY", e)
        primary = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
        return [primary] if primary else []


def _worker_init(key_queue) -> None:
    """ProcessPoolExecutor initializer. Runs ONCE per worker process.
    Pops one key from the shared queue and pins it to ANTHROPIC_API_KEY
    for the lifetime of this worker.
    """
    try:
        key = key_queue.get(timeout=5)
    except Exception:
        key = ''
    if key and key.startswith('sk-ant'):
        os.environ['ANTHROPIC_API_KEY'] = key
        # Not through logger: initializer may run before basicConfig
        # attaches to the child's stderr in some contexts.
        print(f"[worker pid={os.getpid()}] pinned API key "
              f"{key[:18]}...", flush=True)
    else:
        print(f"[worker pid={os.getpid()}] WARN no key pinned - "
              f"will fall back to inherited ANTHROPIC_API_KEY",
              flush=True)


def _worker_run_dates(
    dates: list[str],
    only_list: Optional[list[str]],
    force_refresh: bool,
    sleep_between_days_s: float,
) -> list[dict]:
    """Worker task: process a contiguous range of ISO dates one at a
    time in this worker's process. Each date writes its own atomic
    dated snapshot via `stream_estimates.fetch_for_date`. Returns a
    list of per-date result dicts."""
    only = set(only_list) if only_list else None
    # Lazy import so the parent doesn't drag anthropic into memory,
    # and so this worker's pinned ANTHROPIC_API_KEY is what
    # claude_client sees on first construction.
    from scripts.trends_scrapers import stream_estimates as se
    results: list[dict] = []
    pid = os.getpid()
    for i, d_iso in enumerate(dates):
        already = (not force_refresh) and _existing_snapshot_is_daily(d_iso)
        if already:
            results.append({
                'date':      d_iso,
                'status':    'skipped',
                'count':     0,
                'elapsed_s': 0.0,
                'pid':       pid,
            })
            continue
        t0 = time.time()
        try:
            result = se.fetch_for_date(d_iso, only=only or None)
            n = int(result.get('count') or 0)
            results.append({
                'date':      d_iso,
                'status':    'ok',
                'count':     n,
                'elapsed_s': round(time.time() - t0, 1),
                'pid':       pid,
            })
        except Exception as e:
            results.append({
                'date':      d_iso,
                'status':    'failed',
                'error':     f'{type(e).__name__}: {e}',
                'elapsed_s': round(time.time() - t0, 1),
                'pid':       pid,
            })
        # Small pause between days in the same worker so a single
        # worker doesn't hammer its own key back-to-back.
        if i < len(dates) - 1 and sleep_between_days_s > 0:
            time.sleep(sleep_between_days_s)
    return results


def backfill_range(
    since_iso: str,
    until_iso: Optional[str] = None,
    only: Optional[set[str]] = None,
    dry_run: bool = False,
    force_refresh: bool = False,
    sleep_between_days_s: float = 1.5,
    workers: int = 0,
    dates_per_worker: int = 1,
    pool_path: Optional[str] = None,
) -> dict:
    """Backfill dated snapshots from `since_iso` through `until_iso`
    (default today - 1).

    Parallelism: `workers` process-level workers, each pinned to a
    distinct Anthropic key from `.env.avid_skins` (or the file at
    `pool_path`). `dates_per_worker` sets the task granularity: how
    many contiguous ISO dates a single worker owns per submitted
    task. Workers pick the next task off the pool when they finish.

    Returns a summary dict:

        {
          'since': '2026-06-01',
          'until': '2026-09-02',
          'total_days': 94,
          'wrote':      [<iso>, ...],   # days researched + written
          'skipped':    [<iso>, ...],   # days already covered
          'failed':     [(<iso>, <reason>), ...],
        }
    """
    if not until_iso:
        # Default to yesterday - today's snapshot is what the live
        # daily cron writes; the backfill doesn't stomp on it.
        until_iso = (
            datetime.now(timezone.utc).date() - timedelta(days=1)
        ).isoformat()

    days = list(_iter_dates(since_iso, until_iso))
    only_list = sorted(only) if only else None

    logger.info("backfill_daily_estimates: since=%s until=%s -> %d days "
                 "(dry_run=%s, force_refresh=%s, only=%s)",
                 since_iso, until_iso, len(days), dry_run, force_refresh,
                 only_list or 'all')
    logger.info("Cost estimate: %s", _estimate_cost_note(len(days)))

    wrote: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    if dry_run:
        for i, d_iso in enumerate(days, 1):
            if _existing_snapshot_is_daily(d_iso):
                logger.info("[%d/%d] %s: SKIP (dated snapshot already daily-era)",
                             i, len(days), d_iso)
                skipped.append(d_iso)
            else:
                logger.info("[%d/%d] %s: WOULD BACKFILL (dry-run)",
                             i, len(days), d_iso)
                wrote.append(d_iso)
        return {
            'since':      since_iso,
            'until':      until_iso,
            'total_days': len(days),
            'wrote':      wrote,
            'skipped':    skipped,
            'failed':     failed,
            'dry_run':    True,
        }

    # -----------------------------------------------------------------
    # Resolve worker count and pin one Anthropic key per worker.
    # -----------------------------------------------------------------
    keys = _load_pool_keys(pool_path)
    if not keys:
        logger.error("no Anthropic keys available: check "
                      ".env.avid_skins or ANTHROPIC_API_KEY. Aborting.")
        return {
            'since': since_iso, 'until': until_iso,
            'total_days': len(days),
            'wrote': [], 'skipped': [], 'failed':
                [(d, 'no ANTHROPIC_API_KEY available') for d in days],
            'dry_run': False,
        }
    # Default: one worker per key MINUS ONE, floored at 1. Leaves one
    # key headroom for the live daily cron / dashboard traffic while
    # the backfill runs. Operator can override with --workers.
    if workers <= 0:
        workers = max(1, len(keys) - 1)
    if workers > len(keys):
        logger.warning("--workers=%d exceeds pool size %d; workers "
                        "beyond key count will share keys (rate-limit "
                        "contention).", workers, len(keys))
    logger.info("pool: %d keys, workers: %d, dates_per_worker: %d",
                 len(keys), workers, dates_per_worker)

    # -----------------------------------------------------------------
    # Task shape: one task = a contiguous list of ISO dates. Each
    # worker processes its assigned dates sequentially (so its output
    # stays atomic per-day) and picks up the next task when done.
    # -----------------------------------------------------------------
    tasks = _chunk_dates(days, dates_per_worker)
    logger.info("shard: %d tasks of up to %d dates each",
                 len(tasks), dates_per_worker)

    # Build a spawn-compatible queue and pre-fill with one key per
    # worker (repeating round-robin only when workers > keys).
    mp_ctx = mp.get_context('spawn')
    key_queue = mp_ctx.Queue()
    for i in range(workers):
        key_queue.put(keys[i % len(keys)])

    t_start = time.time()
    completed_tasks = 0
    total_items_written = 0
    per_worker_items: dict[int, int] = {}
    per_worker_days: dict[int, int] = {}

    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=mp_ctx,
        initializer=_worker_init,
        initargs=(key_queue,),
    ) as pool:
        fut_to_task = {
            pool.submit(
                _worker_run_dates,
                task,
                only_list,
                force_refresh,
                sleep_between_days_s,
            ): task
            for task in tasks
        }
        for fut in as_completed(fut_to_task):
            task = fut_to_task[fut]
            try:
                per_date = fut.result()
            except Exception as e:
                # Task-level catastrophic failure (rare - the worker
                # catches per-day errors internally). Mark every day
                # in the task failed and keep going.
                logger.exception("task %s failed catastrophically: %s",
                                  task, e)
                for d_iso in task:
                    failed.append((d_iso, f'task-crash: {type(e).__name__}: {e}'))
                completed_tasks += 1
                continue

            for r in per_date:
                d_iso = r['date']
                pid = r.get('pid') or 0
                if r['status'] == 'ok':
                    wrote.append(d_iso)
                    n = int(r.get('count') or 0)
                    total_items_written += n
                    per_worker_items[pid] = per_worker_items.get(pid, 0) + n
                    per_worker_days[pid] = per_worker_days.get(pid, 0) + 1
                    logger.info("[pid=%d] %s: WROTE %d items (%.1fs)",
                                 pid, d_iso, n, r.get('elapsed_s') or 0)
                elif r['status'] == 'skipped':
                    skipped.append(d_iso)
                    logger.info("[pid=%d] %s: SKIP (already daily-era)",
                                 pid, d_iso)
                else:
                    failed.append((d_iso, r.get('error') or '?'))
                    logger.warning("[pid=%d] %s: FAILED (%s)",
                                    pid, d_iso, r.get('error'))

            completed_tasks += 1
            # Aggregate progress line every task.
            elapsed_min = (time.time() - t_start) / 60.0
            done_days = len(wrote) + len(skipped) + len(failed)
            remaining = len(days) - done_days
            aggregate_items_per_min = (total_items_written / elapsed_min
                                        if elapsed_min > 0 else 0.0)
            # Simple ETA: assume remaining days take (elapsed / done)
            # per day. Handles a run where most of the front-loaded
            # days were already daily-era (skipped) gracefully because
            # skipped days consume ~0 wall time.
            eta_min = (elapsed_min * remaining / max(1, done_days)
                        if done_days > 0 else 0.0)
            logger.info("[progress] tasks=%d/%d days_done=%d "
                         "(wrote=%d skip=%d fail=%d) items=%d "
                         "elapsed=%.1fm rate=%.0f items/min eta=%.1fm",
                         completed_tasks, len(tasks), done_days,
                         len(wrote), len(skipped), len(failed),
                         total_items_written, elapsed_min,
                         aggregate_items_per_min, eta_min)

    elapsed_min = (time.time() - t_start) / 60.0
    logger.info("backfill_daily_estimates: complete in %.1fm. "
                 "wrote=%d skipped=%d failed=%d (of %d total)",
                 elapsed_min, len(wrote), len(skipped),
                 len(failed), len(days))
    # Per-worker throughput summary (helps operators tune --workers).
    if per_worker_items:
        logger.info("per-worker throughput (top 10 by items):")
        for pid, n in sorted(per_worker_items.items(),
                              key=lambda kv: -kv[1])[:10]:
            wd = per_worker_days.get(pid) or 0
            rate = (n / elapsed_min) if elapsed_min > 0 else 0.0
            logger.info("  pid=%d  days=%d items=%d rate=%.1f/m",
                         pid, wd, n, rate)

    return {
        'since':                since_iso,
        'until':                until_iso,
        'total_days':           len(days),
        'wrote':                wrote,
        'skipped':              skipped,
        'failed':               failed,
        'dry_run':              False,
        'elapsed_min':          round(elapsed_min, 1),
        'total_items_written':  total_items_written,
        'per_worker_items':     per_worker_items,
        'per_worker_days':      per_worker_days,
        'pool_size':            len(keys),
        'workers':              workers,
    }


def _parse_only(raw: str) -> set[str]:
    """Parse --only comma-list applying the same aliases as
    stream_estimates.py CLI."""
    out: set[str] = set()
    for tok in (raw or '').split(','):
        t = tok.strip().lower()
        if not t:
            continue
        out.add(_RAIL_ALIASES.get(t, t))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=('Backfill dated stream_estimates.json snapshots '
                     'so the trends_iq window accumulator has real '
                     'daily coverage back to `--since`.'))
    parser.add_argument('--since', required=True,
                        help='Start date (YYYY-MM-DD). Backfill '
                              'includes this day.')
    parser.add_argument('--until', default='',
                        help='End date (YYYY-MM-DD). Defaults to '
                              'yesterday UTC.')
    parser.add_argument('--only', default='',
                        help='Comma-separated kinds to research on '
                              'each day (e.g. song,fast_channel). '
                              'Default = all kinds. Same aliases as '
                              'stream_estimates.py CLI.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report gaps + cost estimate without '
                              'touching AI or S3.')
    parser.add_argument('--force-refresh', action='store_true',
                        help='Re-research even where a daily-era '
                              'snapshot already exists.')
    parser.add_argument('--sleep-seconds', type=float, default=1.5,
                        help='Per-worker pause between two calendar '
                              'days handled by the same worker. Default 1.5s.')
    parser.add_argument('--workers', type=int, default=0,
                        help='Parallel worker PROCESSES. 0 (default) = '
                              'one worker per Anthropic key in the pool '
                              'MINUS ONE (floor 1), so a single key stays '
                              'free for the live daily cron. Each worker '
                              'pins a distinct ANTHROPIC_API_KEY.')
    parser.add_argument('--dates-per-worker', type=int, default=1,
                        help='Task granularity: how many contiguous ISO '
                              'dates a worker owns per submitted task. '
                              'Larger values reduce task-queue overhead; '
                              'smaller values give finer-grained load '
                              'balancing near the end of the run. Default 1.')
    parser.add_argument('--pool-path', default='',
                        help='Optional override for the Anthropic key '
                              'pool file. Defaults to '
                              '/root/finished_codes/.env.avid_skins or '
                              '<repo>/.env.avid_skins.')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    only = _parse_only(args.only)
    summary = backfill_range(
        since_iso=args.since,
        until_iso=args.until or None,
        only=only or None,
        dry_run=args.dry_run,
        force_refresh=args.force_refresh,
        sleep_between_days_s=args.sleep_seconds,
        workers=args.workers,
        dates_per_worker=args.dates_per_worker,
        pool_path=args.pool_path or None,
    )
    # Exit non-zero if the run hit a failure but not everything
    # failed (partial success). Zero on clean success or clean skip.
    if summary['failed']:
        return 2 if summary['wrote'] or summary['skipped'] else 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
