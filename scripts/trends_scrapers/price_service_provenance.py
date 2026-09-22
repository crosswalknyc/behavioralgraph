#!/usr/bin/env python3
"""Give every streaming / FAST row a reading researched for its own service.

A row on service X must render a reading researched for service X. Where
no such reading exists, this researches one for X and writes it into the
stored entry NARROWLY: only `by_platform[X]` moves, so the title's rows on
services that were already reading correctly stay exactly where they are.

The population is the rows `trends_iq._enforce_service_provenance` marks,
collected through the coverage gate's own per-service path, which is the
same path the platform-cap correction uses. Derived rails never appear in
it: their value comes from their parent by design, so there is no reading
of their own to be missing.

Pricing uses the estimator's own tiering (top-ranked rows on the stronger
model, the long tail on the lighter one) through the discounted batch
lane. Progress is checkpointed as results stream back, under a key of its
own so a resume here can never be mistaken for the nightly run's. Spend is
capped and reported.

Never queries clickstream (`.cursor/rules/trends-rankers-never-clickstream.mdc`).

    python3 -m scripts.trends_scrapers.price_service_provenance --dry-run
    python3 -m scripts.trends_scrapers.price_service_provenance --cap-usd 60
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_FILTERS = {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1}
_CHECKPOINT = '/tmp/service_provenance_checkpoint.json'


def _load_checkpoint(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save_checkpoint(path: str, results: dict) -> None:
    try:
        with open(path, 'w') as fh:
            json.dump(results, fh)
    except Exception:
        logger.exception('checkpoint write failed (non-fatal)')


def collect(payload: dict) -> list:
    """Targets whose rows were showing another service's reading."""
    from scripts.trends_scrapers.coverage_gate import collect_missing
    (_stream_items, _headline_items, _total, _res, _base_n,
     cap_targets) = collect_missing(payload)
    return [t for t in cap_targets
            if 'cross_service' in (t.get('states') or ())]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--cap-usd', type=float, default=60.0)
    ap.add_argument('--checkpoint', default=_CHECKPOINT)
    ap.add_argument('--trail', default='/tmp/service_provenance_trail.json')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    import trends_iq
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import coverage_gate as cg
    from scripts.trends_scrapers._spend_monitor import SpendMonitor

    target_date_iso = (datetime.now(timezone.utc).date()
                       - timedelta(days=1)).isoformat()

    payload = trends_iq.compute_view(dict(_FILTERS), force_refresh=True)
    targets = collect(payload)
    rows = sum(len(t['rows']) for t in targets)
    blocks = sum(len(t['platforms']) for t in targets)
    print(f'titles needing a reading of their own : {len(targets)}')
    print(f'service readings to research          : {blocks}')
    print(f'rendered rows behind them             : {rows}')

    n_hi = sum(1 for t in targets if int(t['best_rank'] or 0) <= 20)
    est = (n_hi * ((3000 / 1e6) * 3.0 + (500 / 1e6) * 15.0)
           + (len(targets) - n_hi) * ((3000 / 1e6) * 1.0
                                      + (500 / 1e6) * 5.0)) * 0.5
    print(f'projected spend (discounted lane)     : ${est:.2f}')
    if args.dry_run:
        print('dry-run: nothing researched')
        return 0
    if est > args.cap_usd:
        print(f'projected ${est:.2f} is over the ${args.cap_usd:.2f} '
              f'guard; stopping without spending')
        return 2

    done = _load_checkpoint(args.checkpoint)
    if done:
        print(f'resuming: {len(done)} title(s) already priced')
    todo = [t for t in targets if t['entry_key'] not in done]

    meter = SpendMonitor(cap_usd=args.cap_usd, prefix='service_provenance')
    results = dict(done)
    if todo:
        items = cg._cap_research_items(se, todo)
        cp = {'target_date_iso': f'{target_date_iso}-service-provenance',
              'kept_prior': {}, 'in_progress': {}, 'flushed_at': 0}
        fresh = se._research_all_batch(items,
                                        target_date_iso=target_date_iso,
                                        spend_monitor=meter,
                                        checkpoint_state=cp)
        if len(fresh) < se._BATCH_FALLBACK_MIN_SHARE * len(items):
            print(f'batch returned {len(fresh)}/{len(items)}; pricing the '
                  f'remainder one at a time')
            rest = [it for it in items
                    if se._lookup_key(it['kind'], it['display_title'],
                                       it.get('artist') or '') not in fresh]
            fresh.update(se._research_all(rest,
                                           target_date_iso=target_date_iso,
                                           spend_monitor=meter))
        results.update(fresh)
        _save_checkpoint(args.checkpoint, results)
        print(f'priced {len(fresh)}/{len(items)} title(s) this pass')

    stats = cg._merge_cap_platform_blocks(results, targets, target_date_iso)
    print(f'wrote {stats["blocks"]} service reading(s) into '
          f'{stats["entries"]} stored entry(ies)')
    if stats['no_result'] or stats['no_block']:
        held = stats['no_result'] + stats['no_block']
        print(f'held (no usable reading, tonight retries): {len(held)}')
        for h in held[:25]:
            print(f'   {h}')

    with open(args.trail, 'w') as fh:
        json.dump(stats['trail'], fh, indent=1)
    print(f'trail -> {args.trail}')
    print(f'spend: ${meter.total():.2f}')

    try:
        n = trends_iq.invalidate_live_compute_view_caches()
        print(f'purged {n} live cache entries')
    except Exception:
        logger.exception('cache purge failed (non-fatal)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
