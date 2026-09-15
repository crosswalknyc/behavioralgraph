#!/usr/bin/env python3
"""Restore the stream-estimate keys lost on 2026-09-15.

On 2026-09-15 a truncated line in the batch results stream raised out
of the estimator, and the generic scraper error handler wrote a
255-byte stub over `latest/stream_estimates.json`, taking an
18,009-key store to zero. The coverage gate re-priced the 6,769 keys
the rendered board asked for, but every key the board did not ask for
that day is simply gone, and rows that reach for one fall back to a
value derived from their rank slot.

This restores them the same way a normal run would: `fetch()` composes
its output as `dict(prior_items)` updated with what it freshly
researched, so a key nobody re-priced keeps its last good value and is
walked to a day-specific number. Prior items come from the 2026-09-14
dated snapshot, the last clean one.

Today's fresh values always win. Nothing already priced is overwritten.

  python3 scripts/restore_stream_estimates_2026_09_15.py --dry-run
  python3 scripts/restore_stream_estimates_2026_09_15.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

logger = logging.getLogger('restore_stream_estimates')

BUCKET = 'dashboard-inputs'
LATEST_KEY = 'trends_iq_snapshots/latest/stream_estimates.json'
PRIOR_KEY = 'trends_iq_snapshots/2026-09-14/stream_estimates.json'


def _s3():
    import boto3
    return boto3.client('s3')


def _get(key: str) -> dict:
    return json.loads(_s3().get_object(Bucket=BUCKET, Key=key)['Body'].read())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    if not args.apply and not args.dry_run:
        ap.error('pass --dry-run or --apply')
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base

    today = _get(LATEST_KEY)
    prior = _get(PRIOR_KEY)

    today_items = today.get('items') or {}
    prior_items = prior.get('items') or {}
    target_iso = today.get('target_date') or '2026-09-14'
    prev_iso = prior.get('target_date') or '2026-09-13'

    carried = {k: dict(v) for k, v in prior_items.items()
               if k not in today_items}
    logger.info("today=%d keys, prior=%d keys, carrying forward %d",
                len(today_items), len(prior_items), len(carried))
    if not carried:
        logger.info("nothing to restore")
        return 0

    merged: dict[str, dict] = dict(carried)
    merged.update(today_items)          # today's fresh values win

    # Same three passes fetch() runs over carried-forward keys, so a
    # restored value is day-specific rather than yesterday's integer
    # repeated with a dead trend chip.
    fresh_keys = set(today_items.keys())
    pre_fast = se._capture_fast_containment_state(merged)
    n_walk = se._apply_inherited_daily_variation(
        merged, fresh_keys, prior_items, target_iso, prev_date_iso=prev_iso)
    n_nudge = se._enforce_min_daily_movement(merged, prior_items, target_iso)
    n_lift = se._enforce_fast_channel_containment(
        merged, pre_fast, prior_items, target_iso)
    logger.info("walked %d, nudged %d, channel floor lifts %d",
                n_walk, n_nudge, n_lift)
    merged = se._attach_dod_trend(merged, prior, prev_date_iso=prev_iso,
                                   today_iso=target_iso)

    unchanged = sum(1 for k in carried
                    if merged.get(k, {}).get('us_estimate')
                    == prior_items[k].get('us_estimate'))
    logger.info("restored %d keys (%d still equal to their prior integer)",
                len(carried), unchanged)

    if args.dry_run:
        logger.info("dry run; nothing written. would publish %d keys",
                    len(merged))
        return 0

    out = dict(today)
    out['items'] = merged
    out['count'] = len(merged)
    # The stub's error string is an artifact of the write that caused
    # the loss; the board it describes no longer exists.
    out['error'] = None
    out.pop('error_preserved_prior_items', None)
    out['generated_at'] = datetime.now(timezone.utc).isoformat()
    out['restored_from'] = PRIOR_KEY
    out['restored_keys'] = len(carried)

    _base.write_snapshot('stream_estimates', out)
    logger.info("published %d keys to latest/ + today's dated copy",
                len(merged))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
