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

Usage:

    # Backfill last 7 days as a smoke test.
    python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-08-27

    # Kick off the full backfill to June 1 in the background.
    nohup python3 -m scripts.trends_scrapers.backfill_daily_estimates \\
        --since 2026-06-01 \\
        > /var/log/backfill_daily_estimates.log 2>&1 &

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
    --force-refresh). Never exposed on any dashboard or API surface
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
import sys
import time
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
    import os
    return boto3.client('s3',
                         region_name=(os.environ.get('AWS_REGION')
                                       or 'us-east-2'))


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
            f"in AI usage across {num_days} day(s), 45-90 min per day "
            f"at concurrency=6 (~1-3 hours total per every 3 days).")


def backfill_range(since_iso: str,
                    until_iso: Optional[str] = None,
                    only: Optional[set[str]] = None,
                    dry_run: bool = False,
                    force_refresh: bool = False,
                    sleep_between_days_s: float = 1.5) -> dict:
    """Backfill dated snapshots from `since_iso` through `until_iso`
    (default today - 1). Returns a summary dict:

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
    logger.info("backfill_daily_estimates: since=%s until=%s -> %d days "
                 "(dry_run=%s, force_refresh=%s, only=%s)",
                 since_iso, until_iso, len(days), dry_run, force_refresh,
                 sorted(only) if only else 'all')
    logger.info("Cost estimate: %s", _estimate_cost_note(len(days)))

    wrote: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    for i, d_iso in enumerate(days, 1):
        already = (not force_refresh) and _existing_snapshot_is_daily(d_iso)
        if already:
            logger.info("[%d/%d] %s: SKIP (dated snapshot already daily-era)",
                         i, len(days), d_iso)
            skipped.append(d_iso)
            continue
        if dry_run:
            logger.info("[%d/%d] %s: WOULD BACKFILL (dry-run)",
                         i, len(days), d_iso)
            wrote.append(d_iso)
            continue
        try:
            # Import lazily so --dry-run doesn't require the anthropic
            # package to be installed on whatever host runs the audit.
            from scripts.trends_scrapers import stream_estimates as se
            t0 = time.time()
            result = se.fetch_for_date(d_iso, only=only or None)
            n = result.get('count') or 0
            elapsed = time.time() - t0
            logger.info("[%d/%d] %s: WROTE %d items (%.1fs)",
                         i, len(days), d_iso, n, elapsed)
            wrote.append(d_iso)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            logger.exception("[%d/%d] %s: FAILED (%s)",
                              i, len(days), d_iso, reason)
            failed.append((d_iso, reason))
        # Small gap between days so Claude / web_search aren't
        # slammed with back-to-back thousand-item batches. Skip on
        # the last day.
        if i < len(days) and not dry_run:
            time.sleep(sleep_between_days_s)

    summary = {
        'since':      since_iso,
        'until':      until_iso,
        'total_days': len(days),
        'wrote':      wrote,
        'skipped':    skipped,
        'failed':     failed,
        'dry_run':    dry_run,
    }
    logger.info("backfill_daily_estimates: complete. wrote=%d skipped=%d "
                 "failed=%d (of %d total)",
                 len(wrote), len(skipped), len(failed), len(days))
    return summary


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
                        help='Pause between calendar days. Default 1.5s.')
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
    )
    # Exit non-zero if the run hit a failure but not everything
    # failed (partial success). Zero on clean success or clean skip.
    if summary['failed']:
        return 2 if summary['wrote'] or summary['skipped'] else 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
