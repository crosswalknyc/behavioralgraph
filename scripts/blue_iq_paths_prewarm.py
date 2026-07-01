#!/usr/bin/env python3
"""blue_iq_paths_prewarm.py — Refresh the "Top observed paths" agent cache.

Calls compute_panel_view(force_refresh=True) per geography. Side effect:
the new path_discovery.discover_issue_paths agent runs for each geo with
the EXACT issue-bucket list that the dashboard will surface at request
time, so the per-issue cache key in s3://dashboard-inputs/blue_iq/issue_paths/v1/
matches what a real user request hashes to.

Pre-warming through compute_panel_view (vs. calling discover_issue_paths
directly) also re-warms the full per-filter payload cache so the next
user landing on that geo sees a sub-second response.

Cost: agent call only fires when the in-S3 path cache is older than 24h,
so re-runs are cheap. First nightly fill across National + 51 states +
top-20 DMAs ≈ 72 agent calls × ~$0.10 = ~$7.

Recommended Hetzner crontab — after blue_iq_daily_warm.py (cube) AND
blue_iq_candidates_prewarm.py (candidates) so the cube + agent layers
are in sync:

    50 8 * * *  cd /root/finished_codes/bg-webapp && /usr/bin/python3 \\
        scripts/blue_iq_paths_prewarm.py --states --national \\
        >> /var/log/blue_iq_paths_prewarm.log 2>&1

Manual:
    python3 bg-webapp/scripts/blue_iq_paths_prewarm.py --national
    python3 bg-webapp/scripts/blue_iq_paths_prewarm.py --states
    python3 bg-webapp/scripts/blue_iq_paths_prewarm.py \\
        --geo State:California --geo DMA:"Los Angeles"
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
BG_WEBAPP = os.path.dirname(HERE)
sys.path.insert(0, BG_WEBAPP)

logger = logging.getLogger('blue_iq_paths_prewarm')

US_STATES = [
    'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
    'Connecticut', 'Delaware', 'District of Columbia', 'Florida', 'Georgia',
    'Hawaii', 'Idaho', 'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky',
    'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan',
    'Minnesota', 'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
    'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina',
    'North Dakota', 'Ohio', 'Oklahoma', 'Oregon', 'Pennsylvania',
    'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas',
    'Utah', 'Vermont', 'Virginia', 'Washington', 'West Virginia', 'Wisconsin',
    'Wyoming',
]

TOP_DMAS = [
    'New York', 'Los Angeles', 'Chicago', 'Philadelphia', 'Dallas-Ft. Worth',
    'San Francisco-Oakland-San Jose', 'Boston (Manchester)', 'Atlanta',
    'Houston', 'Washington DC (Hagerstown)', 'Phoenix (Prescott)',
    'Tampa-St. Petersburg (Sarasota)', 'Seattle-Tacoma', 'Detroit',
    'Minneapolis-St. Paul', 'Miami-Ft. Lauderdale', 'Denver',
    'Orlando-Daytona Bch-Melbrn', 'Cleveland-Akron (Canton)',
    'Sacramnto-Stkton-Modesto',
]


def _prewarm_one(geo_type: str, geo_value: str, idx: int, total: int,
                  lookback: int) -> tuple[str, int, int, float]:
    """Hit compute_panel_view for one geo. Returns (cache_id, n_paths,
    n_buckets, elapsed_seconds). n_paths = -1 on failure."""
    from blue_iq import compute_panel_view                          # type: ignore
    t0 = time.time()
    n_paths = 0
    n_buckets = 0
    try:
        view = compute_panel_view({
            'party':         'All',
            'geo_type':      geo_type,
            'geo_value':     geo_value,
            'lookback_days': lookback,
        }, force_refresh=True)
        cards = (view or {}).get('cards') or {}
        n_paths = len(cards.get('issue_paths_agent') or [])
        n_buckets = len(cards.get('issue_buckets') or [])
    except Exception as e:
        logger.error("[%d/%d] %s|%s FAILED: %s",
                      idx, total, geo_type, geo_value or '<empty>', e)
        n_paths = -1
    dur = time.time() - t0
    logger.info("[%d/%d] %s|%s -> %d agent paths / %d buckets (%.1fs)",
                 idx, total, geo_type, geo_value or '<empty>',
                 n_paths, n_buckets, dur)
    return (f"{geo_type}|{geo_value}", n_paths, n_buckets, dur)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Prewarm the Top observed paths agent cache.')
    parser.add_argument('--national', action='store_true',
                          help='Prewarm the National geo.')
    parser.add_argument('--states', action='store_true',
                          help='Prewarm all 50 states + DC.')
    parser.add_argument('--dmas', action='store_true',
                          help='Prewarm the top-20 DMAs.')
    parser.add_argument('--geo', action='append', default=[],
                          help='Specific geo to prewarm, TYPE:VALUE form.')
    parser.add_argument('--lookback', type=int, default=30,
                          help='Lookback window in days for compute_panel_view '
                                '(default 30, matches the dashboard default).')
    parser.add_argument('--workers', type=int, default=2,
                          help='Parallel agent calls (default 2). Path agent '
                                'requests are heavier than the candidate agent '
                                '(longer prompts, more issues), so keep this '
                                'conservative to avoid 429s.')
    parser.add_argument('--limit', type=int, default=0,
                          help='Cap total geos (debug). 0 = no cap.')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    geos: list[tuple[str, str]] = []
    if args.national:
        geos.append(('National', ''))
    if args.states:
        geos.extend(('State', s) for s in US_STATES)
    if args.dmas:
        geos.extend(('DMA', d) for d in TOP_DMAS)
    for spec in args.geo:
        if ':' not in spec:
            logger.warning("ignoring --geo %r (expected TYPE:VALUE)", spec)
            continue
        gt, gv = spec.split(':', 1)
        geos.append((gt.strip(), gv.strip()))

    if not geos:
        parser.error('Nothing to do. Pass --national, --states, --dmas, or --geo.')

    if args.limit > 0:
        geos = geos[:args.limit]

    total = len(geos)
    logger.info("Prewarming %d geos (lookback=%dd) with %d workers",
                 total, args.lookback, args.workers)
    t_run = time.time()

    results: list[tuple[str, int, int, float]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(_prewarm_one, gt, gv, i + 1, total, args.lookback)
                 for i, (gt, gv) in enumerate(geos)]
        for fut in as_completed(futs):
            results.append(fut.result())

    total_secs = time.time() - t_run
    n_ok = sum(1 for _, n_p, _, _ in results if n_p >= 0)
    n_fail = sum(1 for _, n_p, _, _ in results if n_p < 0)
    total_paths = sum(max(0, n_p) for _, n_p, _, _ in results)
    avg_dur = (sum(d for _, _, _, d in results) / len(results)) if results else 0
    logger.info("=" * 60)
    logger.info("DONE: %d ok / %d failed / %d total agent paths / "
                 "%.1fs total / %.1fs avg per geo",
                 n_ok, n_fail, total_paths, total_secs, avg_dur)
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
