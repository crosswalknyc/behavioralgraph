#!/usr/bin/env python3
"""blue_iq_candidates_prewarm.py — Refresh the Top Candidates cache nightly.

Calls candidate_discovery.discover_candidates(force_refresh=True) for the
geos we want hot at first page-load:
  * National
  * All 50 U.S. states + DC
  * The top ~20 DMAs by panel reach (configurable)

Each call hits the OpenAI web-search agent (~10-15s) and writes the result
to s3://dashboard-inputs/blue_iq/candidates/v2/{geo_type}__{geo_slug}.json
with a 24h TTL. Lazy fill still works for any geo not pre-warmed, but
pre-warming means the first user to land on a state-filtered dashboard
gets an instant card instead of waiting 15s for the agent.

Cost: ~51 calls × ~$0.10 = ~$5/day.

Recommended Hetzner crontab, AFTER blue_iq_daily_warm.py so the panel
cube is fresh first:

    30 8 * * *  cd /root/finished_codes/bg-webapp && /usr/bin/python3 \\
        scripts/blue_iq_candidates_prewarm.py --states --national \\
        >> /var/log/blue_iq_candidates_prewarm.log 2>&1

Manual:
    python3 bg-webapp/scripts/blue_iq_candidates_prewarm.py --national
    python3 bg-webapp/scripts/blue_iq_candidates_prewarm.py --states
    python3 bg-webapp/scripts/blue_iq_candidates_prewarm.py \\
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

logger = logging.getLogger('blue_iq_candidates_prewarm')

# Full state list (50 + DC). Match the format produced by
# external_signals._USPS_TO_NAME so the cache keys line up with what
# blue_iq.py looks up at request time.
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

# Top media markets we want hot. Match the cube's DMA universe so the
# user clicking a DMA in the dropdown hits a primed cell. Aligned with
# blue_iq.DMA_TO_STATE keys.
TOP_DMAS = [
    'New York', 'Los Angeles', 'Chicago', 'Philadelphia', 'Dallas-Ft. Worth',
    'San Francisco-Oakland-San Jose', 'Boston (Manchester)', 'Atlanta',
    'Houston', 'Washington DC (Hagerstown)', 'Phoenix (Prescott)',
    'Tampa-St. Petersburg (Sarasota)', 'Seattle-Tacoma', 'Detroit',
    'Minneapolis-St. Paul', 'Miami-Ft. Lauderdale', 'Denver', 'Orlando-Daytona Bch-Melbrn',
    'Cleveland-Akron (Canton)', 'Sacramnto-Stkton-Modesto',
]


def _prewarm_one(geo_type: str, geo_value: str, idx: int, total: int) -> tuple[str, int, float]:
    from candidate_discovery import discover_candidates    # type: ignore
    t0 = time.time()
    try:
        cands = discover_candidates(geo_type, geo_value, force_refresh=True)
        dur = time.time() - t0
        logger.info("[%d/%d] %s|%s -> %d candidates (%.1fs)",
                     idx, total, geo_type, geo_value or '<empty>', len(cands), dur)
        return (f"{geo_type}|{geo_value}", len(cands), dur)
    except Exception as e:
        dur = time.time() - t0
        logger.error("[%d/%d] %s|%s FAILED after %.1fs: %s",
                      idx, total, geo_type, geo_value or '<empty>', dur, e)
        return (f"{geo_type}|{geo_value}", -1, dur)


def main() -> int:
    parser = argparse.ArgumentParser(description='Prewarm the Top Candidates cache.')
    parser.add_argument('--national', action='store_true',
                          help='Prewarm the National geo.')
    parser.add_argument('--states', action='store_true',
                          help='Prewarm all 50 states + DC.')
    parser.add_argument('--dmas', action='store_true',
                          help='Prewarm the top-20 DMAs.')
    parser.add_argument('--geo', action='append', default=[],
                          help='Specific geo to prewarm, in TYPE:VALUE form. '
                                'Can be repeated.')
    parser.add_argument('--workers', type=int, default=3,
                          help='Parallel agent calls (default 3). The OpenAI '
                                'API handles a few in parallel fine; >5 risks '
                                'rate-limit 429s.')
    parser.add_argument('--limit', type=int, default=0,
                          help='Cap total geos (debug). 0 = no cap.')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    # Build the work list.
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
    logger.info("Prewarming %d geos with %d workers", total, args.workers)
    t_run = time.time()

    results: list[tuple[str, int, float]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(_prewarm_one, gt, gv, i + 1, total)
                 for i, (gt, gv) in enumerate(geos)]
        for fut in as_completed(futs):
            results.append(fut.result())

    total_secs = time.time() - t_run
    n_ok = sum(1 for _, n, _ in results if n >= 0)
    n_fail = sum(1 for _, n, _ in results if n < 0)
    total_cands = sum(n for _, n, _ in results if n >= 0)
    avg_dur = (sum(d for _, _, d in results) / len(results)) if results else 0
    logger.info("=" * 60)
    logger.info("DONE: %d ok / %d failed / %d total candidates / %.1fs total "
                 "/ %.1fs avg per geo", n_ok, n_fail, total_cands, total_secs, avg_dur)
    return 0 if n_fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
