#!/usr/bin/env python3
"""
Microdramas IQ scraper orchestrator.

Runs each microdrama scraper (currently just Peacock) and merges its
snapshot into the persistent catalog at
`s3://dashboard-inputs/microdramas_iq/catalog.json`.

Hetzner crontab (5am UTC, after the trends scrapers so donated cookies
are freshest):

    30 5 * * *  cd /root/finished_codes/bg-webapp && [ -f /root/finished_codes/.env.trends_scrapers ] && set -a && . /root/finished_codes/.env.trends_scrapers && set +a; /usr/bin/python3 -m scripts.microdramas_scrapers.run_all >> /var/log/microdramas_scrapers.log 2>&1

Manual one-shot:

    cd /root/finished_codes/bg-webapp
    python3 -m scripts.microdramas_scrapers.run_all
    python3 -m scripts.microdramas_scrapers.run_all --seed   # write curated
                                                              # baseline only
"""

from __future__ import annotations

import argparse
import logging
import sys

SCRAPERS = [
    # (source_key, module_path, label)
    ('peacock', 'scripts.microdramas_scrapers.peacock', 'Peacock'),
]


def _run_one(source: str, module_path: str, label: str, seed_only: bool) -> int:
    try:
        module = __import__(module_path, fromlist=['fetch', 'fetch_baseline', '_write_snapshot'])
    except Exception as e:
        print(f'  ! could not import {module_path}: {e}')
        return 0

    try:
        if seed_only:
            titles = module.fetch_baseline()
            payload = {
                'source': source, 'label': label, 'kind': 'microdramas',
                'titles': titles, 'seed': True,
            }
        else:
            payload = module.fetch()
        module._write_snapshot(payload)
        return len(payload.get('titles') or [])
    except Exception as e:
        print(f'  ! {source} scraper failed: {e}')
        import traceback; traceback.print_exc()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='Microdramas IQ scraper runner.')
    ap.add_argument('--seed', action='store_true',
                     help='Skip live pulls; write curated baselines only.')
    ap.add_argument('--only', default='',
                     help='Comma-separated source list to run.')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    only = {s.strip() for s in args.only.split(',') if s.strip()}
    total = 0
    for source, mod_path, label in SCRAPERS:
        if only and source not in only:
            continue
        print(f'--- {label} ---')
        n = _run_one(source, mod_path, label, seed_only=args.seed)
        total += n
        print(f'  {label}: {n} titles')

    print(f'Done. Total titles across sources: {total}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
