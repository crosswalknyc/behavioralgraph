#!/usr/bin/env python3
"""
blue_iq_daily_warm.py — Daily refresh + cache pre-warm for Blue IQ.

Two jobs:
  1. Re-run the behavioral party imputer for every panelist with recent
     activity. Output: `s3://dashboard-inputs/blue_iq/party_imputed/all.json`
  2. Pre-warm the per-filter cache for every (party x state) and
     (party x DMA) combo whose panel size clears MIN_CELL_SIZE.
     Output: `s3://dashboard-inputs/blue_iq/cache/<sha256>.json`

Wire into the Hetzner crontab on `168.119.215.48`:

    # Blue IQ — daily party imputer + cache pre-warm at 5am UTC
    0 5 * * *  cd /root/finished_codes/bg-webapp && /usr/bin/python3 scripts/blue_iq_daily_warm.py >> /var/log/blue_iq_daily_warm.log 2>&1

Run manually:
    python3 bg-webapp/scripts/blue_iq_daily_warm.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Make `import blue_iq` work when invoked from the repo root or scripts/.
HERE = os.path.dirname(os.path.abspath(__file__))
BG_WEBAPP = os.path.dirname(HERE)
sys.path.insert(0, BG_WEBAPP)

import blue_iq  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger('blue_iq_daily_warm')


def warm_party_imputer(lookback_days: int = 90) -> dict:
    log.info("Step 1/2: party imputer over last %dd ...", lookback_days)
    t0 = time.time()
    counts = blue_iq.bulk_impute_party_to_s3(lookback_days=lookback_days)
    dt = time.time() - t0
    log.info("Party imputer done in %.1fs: %s", dt, counts)
    return counts


def warm_filter_cache(parties: list[str] | None = None,
                       states: list[str] | None = None,
                       dmas: list[str] | None = None,
                       limit: int = 0) -> dict:
    log.info("Step 2/2: pre-warm filter cache ...")
    opts = blue_iq.get_filter_options()
    parties = parties or ['All', 'Democrat', 'Republican', 'Independent']
    states  = states  or opts.get('states', [])
    dmas    = dmas    or opts.get('dmas', [])

    combos: list[dict] = []
    combos.append({'party': 'All', 'geo_type': 'National', 'geo_value': ''})
    for p in parties:
        combos.append({'party': p, 'geo_type': 'National', 'geo_value': ''})
        for s in states:
            combos.append({'party': p, 'geo_type': 'State', 'geo_value': s})
    for p in parties:
        for d in dmas[:50]:  # cap DMA fan-out — top 50 by panel size
            combos.append({'party': p, 'geo_type': 'DMA', 'geo_value': d})

    if limit and limit > 0:
        combos = combos[:limit]

    stats = {'attempted': 0, 'cached': 0, 'suppressed': 0, 'errors': 0}
    for f in combos:
        stats['attempted'] += 1
        try:
            payload = blue_iq.compute_panel_view(f, force_refresh=True)
            if payload.get('suppressed'):
                stats['suppressed'] += 1
            else:
                stats['cached'] += 1
        except Exception as e:
            log.warning("warm failed for %s: %s", f, e)
            stats['errors'] += 1
    log.info("Cache warm done: %s", stats)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lookback', type=int, default=90,
                    help='days of clickstream history used by the party imputer (default 90)')
    ap.add_argument('--skip-imputer', action='store_true',
                    help='skip step 1 (run only cache pre-warm)')
    ap.add_argument('--skip-cache', action='store_true',
                    help='skip step 2 (run only party imputer)')
    ap.add_argument('--limit', type=int, default=0,
                    help='cap on number of filter combos to warm (0 = all)')
    args = ap.parse_args()

    log.info("Blue IQ daily warm starting at %s", datetime.now(timezone.utc).isoformat())
    if not args.skip_imputer:
        warm_party_imputer(lookback_days=args.lookback)
    if not args.skip_cache:
        warm_filter_cache(limit=args.limit)
    log.info("Blue IQ daily warm complete.")


if __name__ == '__main__':
    main()
