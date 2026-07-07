#!/usr/bin/env python3
"""
Trends IQ scraper orchestrator.

Runs every scraper in the package, writes per-source snapshots to S3,
and writes a summary `s3://dashboard-inputs/trends_iq_snapshots/latest/_index.json`
with counts + errors so we can monitor freshness at a glance.

Hetzner crontab (5am UTC = 1am ET, before dashboard traffic peaks):

    0 5 * * *  cd /root/finished_codes/bg-webapp && [ -f /root/finished_codes/.env.trends_scrapers ] && set -a && . /root/finished_codes/.env.trends_scrapers && set +a; /usr/bin/python3 -m scripts.trends_scrapers.run_all >> /var/log/trends_scrapers.log 2>&1

Manual one-shot (during dev):

    cd /root/finished_codes/bg-webapp
    python3 -m scripts.trends_scrapers.run_all
    python3 -m scripts.trends_scrapers.run_all --only bestbuy,nike        # subset
    python3 -m scripts.trends_scrapers.run_all --skip walmart,target      # skip Playwright

Each scraper is executed in a thread pool so a slow retailer (Walmart
warm-up) doesn't block the fast ones. Playwright-based scrapers still
launch their own browsers in parallel; if you're on a small VM cap the
workers with `--workers 3`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone


SCRAPERS = [
    # (source_key, module_path, label, kind)
    ('google_wide', 'scripts.trends_scrapers.google_trends_wide', 'Google Trends (wide)', 'search'),
    ('x',         'scripts.trends_scrapers.x_twitter',  'X',         'social'),
    ('tiktok',    'scripts.trends_scrapers.tiktok',     'TikTok',    'social'),
    ('youtube',   'scripts.trends_scrapers.youtube',    'YouTube',   'social'),
    ('instagram', 'scripts.trends_scrapers.instagram',  'Instagram', 'social'),
    ('bestbuy',   'scripts.trends_scrapers.bestbuy',    'Best Buy',  'retailer'),
    ('nike',      'scripts.trends_scrapers.nike',       'Nike',      'retailer'),
    ('lululemon', 'scripts.trends_scrapers.lululemon',  'Lululemon', 'retailer'),
    ('etsy',      'scripts.trends_scrapers.etsy',       'Etsy',      'retailer'),
    ('sephora',   'scripts.trends_scrapers.sephora',    'Sephora',   'retailer'),
    ('target',    'scripts.trends_scrapers.target',     'Target',    'retailer'),
    ('walmart',   'scripts.trends_scrapers.walmart',    'Walmart',   'retailer'),
]


def _run_one(source: str, module_path: str, label: str, kind: str) -> dict:
    started = time.time()
    try:
        module = __import__(module_path, fromlist=['fetch'])
        from scripts.trends_scrapers._base import run_scraper  # local import
        payload = run_scraper(source, label, kind, module.fetch)
    except Exception as e:
        logging.exception("run_all: scraper %s failed to import/run", source)
        payload = {
            'source':   source,
            'label':    label,
            'kind':     kind,
            'national': [],
            'error':    f'orchestrator: {type(e).__name__}: {e}',
        }
    elapsed = time.time() - started
    payload['orchestrator_elapsed_s'] = round(elapsed, 2)
    return payload


def _write_index(results: list[dict]) -> None:
    """Write a summary index the dashboard can peek at without hitting
    every per-source object."""
    try:
        from scripts.trends_scrapers._base import _s3_client, S3_BUCKET
        s3 = _s3_client()
        summary = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sources': [
                {
                    'source':          r.get('source'),
                    'label':           r.get('label'),
                    'kind':            r.get('kind'),
                    'national_count':  len(r.get('national') or []),
                    'error':           r.get('error'),
                    'elapsed_s':       r.get('orchestrator_elapsed_s')
                                       or r.get('scrape_elapsed_s'),
                    'fetched_at':      r.get('fetched_at'),
                }
                for r in results
            ],
        }
        body = json.dumps(summary, ensure_ascii=False).encode('utf-8')
        s3.put_object(Bucket=S3_BUCKET,
                       Key='trends_iq_snapshots/latest/_index.json',
                       Body=body,
                       ContentType='application/json',
                       CacheControl='public, max-age=60')
    except Exception as e:
        logging.warning("run_all: failed to write _index.json: %s", e)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Trends IQ daily scraper orchestrator')
    p.add_argument('--only',   default='', help='comma-separated source keys to run')
    p.add_argument('--skip',   default='', help='comma-separated source keys to skip')
    p.add_argument('--workers', type=int, default=int(os.environ.get('TRENDS_SCRAPERS_WORKERS', '6')),
                    help='max concurrent scrapers (default 6)')
    p.add_argument('--verbose', '-v', action='store_true')
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    only = {s.strip() for s in args.only.split(',') if s.strip()}
    skip = {s.strip() for s in args.skip.split(',') if s.strip()}
    plan = [
        (src, mod, lbl, kind)
        for (src, mod, lbl, kind) in SCRAPERS
        if (not only or src in only) and src not in skip
    ]
    if not plan:
        print("run_all: no scrapers selected", file=sys.stderr)
        return 1

    logging.info("run_all: running %d scrapers with %d workers: %s",
                  len(plan), args.workers, ', '.join(p[0] for p in plan))
    started = time.time()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers,
                                                 thread_name_prefix='trends-scr') as ex:
        futures = {ex.submit(_run_one, *p): p[0] for p in plan}
        for fut in concurrent.futures.as_completed(futures):
            src = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                logging.exception("run_all: %s crashed", src)
                results.append({'source': src, 'error': str(e), 'national': []})

    _write_index(results)

    total_elapsed = time.time() - started
    print(f"\ntrends scrapers complete in {total_elapsed:.1f}s")
    print(f"{'source':<12} {'kind':<9} {'count':>6}  {'elapsed':>8}  error")
    print('-' * 70)
    fail_count = 0
    empty_retailer_sources: list[str] = []
    for r in sorted(results, key=lambda x: x.get('source', '')):
        err = r.get('error') or ''
        if err:
            fail_count += 1
        count = len(r.get('national') or [])
        if r.get('kind') == 'retailer' and count == 0:
            empty_retailer_sources.append(r.get('source', ''))
        print(f"{r.get('source', ''):<12} {r.get('kind', ''):<9} "
               f"{count:>6}  "
               f"{(r.get('orchestrator_elapsed_s') or r.get('scrape_elapsed_s') or 0):>7.1f}s  "
               f"{err[:60]}")

    # Empty retailer feeds are almost always a bot-block. Print the
    # exact `donate_cookies.py` command the operator needs to run.
    if empty_retailer_sources:
        domain_map = {
            'target':    'target.com',    'walmart':   'walmart.com',
            'etsy':      'etsy.com',      'sephora':   'sephora.com',
            'lululemon': 'lululemon.com', 'bestbuy':   'bestbuy.com',
            'nike':      'nike.com',      'ulta':      'ulta.com',
        }
        need = [domain_map[s] for s in empty_retailer_sources if s in domain_map]
        if need:
            print()
            print(f"COOKIE_DONATION_NEEDED: {', '.join(need)}")
            print(f"From your laptop:  python3 scripts/trends_scrapers/donate_cookies.py {' '.join(need)}")

    return 0 if fail_count < len(results) else 2


if __name__ == '__main__':
    sys.exit(main())
