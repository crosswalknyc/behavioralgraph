#!/usr/bin/env python3
"""
Trends IQ scraper orchestrator.

Runs every scraper in the package, writes per-source snapshots to S3,
and writes a summary `s3://dashboard-inputs/trends_iq_snapshots/latest/_index.json`
with counts + errors so we can monitor freshness at a glance.

Hetzner crontab (12:00 UTC = 8:00 AM EDT / 7:00 AM ET, right before
Jenna starts her workday so the "Updated ..." stamp reads as a fresh
morning refresh instead of the middle of the night):

    0 12 * * *  cd /root/finished_codes/bg-webapp && [ -f /root/finished_codes/.env.trends_scrapers ] && set -a && . /root/finished_codes/.env.trends_scrapers && set +a; /usr/bin/python3 -m scripts.trends_scrapers.run_all >> /var/log/trends_scrapers.log 2>&1

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
    ('google_wide',        'scripts.trends_scrapers.google_trends_wide', 'Google Trends (wide)', 'search'),
    ('wikipedia_trending', 'scripts.trends_scrapers.wikipedia_trending', 'Wikipedia',            'search'),
    ('music_charts',       'scripts.trends_scrapers.music_charts',       'Music',                'music'),
    ('podcast_charts',     'scripts.trends_scrapers.podcast_charts',     'Podcasts',             'podcast'),
    ('book_charts',        'scripts.trends_scrapers.book_charts',        'Books',                'book'),
    ('libby_trends',       'scripts.trends_scrapers.libby_trends',       'Libby popular',        'libby'),
    ('philanthropy_news',  'scripts.trends_scrapers.philanthropy_news',  'Philanthropy news',    'news'),
    ('youtube',   'scripts.trends_scrapers.youtube',    'YouTube',   'social'),
    # X, TikTok, and Instagram are NOT in this list. As of 2026-07 they
    # were switched from hashtag/topic lists to real trending posts /
    # videos / tweets, which require donated cookies + a residential IP
    # (all three fingerprint Hetzner's datacenter egress). They now run
    # daily from Jenna's laptop via `local_residential_run.py`.
    # Reddit was previously fetched live at request time from Render,
    # but Reddit blocks Render's datacenter egress. Hetzner's residential
    # egress gets 200s so we run it here daily like every other social.
    ('reddit',    'scripts.trends_scrapers.reddit',     'Reddit',    'social'),
    ('bestbuy',   'scripts.trends_scrapers.bestbuy',    'Best Buy',  'retailer'),
    ('nike',      'scripts.trends_scrapers.nike',       'Nike',      'retailer'),
    ('lululemon', 'scripts.trends_scrapers.lululemon',  'Lululemon', 'retailer'),
    ('etsy',      'scripts.trends_scrapers.etsy',       'Etsy',      'retailer'),
    ('sephora',   'scripts.trends_scrapers.sephora',    'Sephora',   'retailer'),
    ('target',    'scripts.trends_scrapers.target',     'Target',    'retailer'),
    ('walmart',   'scripts.trends_scrapers.walmart',    'Walmart',   'retailer'),
    # Streaming platforms. Prime Video uses donated cookies via
    # cookie_domain=<host>. Netflix, Disney+, ESPN+, Max, and Hulu are
    # NOT in this list because they run from Jenna's laptop via
    # `local_residential_run.py`:
    #   - Netflix (2026-07): switched from public weekly TSV to authenticated
    #     daily scrape of netflix.com/browse, which needs the operator's
    #     donated netflix.com cookies (only available on her machine).
    #   - Disney+ / ESPN+: Bamgrid CDN IP-gates datacenter ranges.
    #   - Max: play.max.com IP-gates similarly.
    #   - Hulu (2026-07): kept returning 0 items from Hetzner despite
    #     valid donated cookies; Hulu WAFs the datacenter IP pre-auth.
    ('primevideo', 'scripts.trends_scrapers.primevideo',    'Prime Video', 'streaming'),
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

    # Run why_trending AFTER the parallel batch finishes so it can read
    # everyone else's fresh snapshots (Wikipedia, GDELT-people, Google
    # Trends). Also runs on an --only whitelist, and can be skipped.
    if (not only or 'why_trending' in only) and 'why_trending' not in skip:
        try:
            results.append(_run_one(
                'why_trending',
                'scripts.trends_scrapers.why_trending',
                'Why is this trending?',
                'meta',
            ))
        except Exception as e:
            logging.exception("run_all: why_trending post-step crashed")
            results.append({'source': 'why_trending', 'error': str(e), 'national': []})

    # stream_estimates: US audience-size estimates (Claude Sonnet +
    # web_search per item) for every top podcast / song / streaming
    # title. Runs AFTER music_charts, podcast_charts, and the streaming
    # snapshots have landed - it reads all of them and stamps each
    # unique item with a `us_estimate` + day-over-day trend. Cost is
    # ~55 web_search calls per day (~$1.10) so we gate on the same
    # only/skip whitelist as why_trending. Streaming snapshots for
    # Netflix / Disney+ / ESPN+ / Max / Hulu are written by
    # local_residential_run.py on Jenna's laptop, so on the day the
    # local batch hasn't run yet those platforms use yesterday's
    # rankings; the next Hetzner run picks up the fresh ones.
    if (not only or 'stream_estimates' in only) and 'stream_estimates' not in skip:
        try:
            results.append(_run_one(
                'stream_estimates',
                'scripts.trends_scrapers.stream_estimates',
                'US Streams',
                'meta',
            ))
        except Exception as e:
            logging.exception("run_all: stream_estimates post-step crashed")
            results.append({'source': 'stream_estimates', 'error': str(e), 'national': []})

    _write_index(results)

    # ------------------------------------------------------------------
    # Warm the dashboard cache with the default (National, 7d) tuple.
    # Every user's first Trends IQ visit hits this tuple; if it's cold
    # the aggregator does 30+ S3 reads + cross-platform annotation +
    # geo filtering and takes 3-5 seconds. Doing it once here, right
    # after the fresh snapshots land, means every user hit tomorrow is
    # an instant cache read. Best-effort - if it fails we log and move
    # on; the first user request will just rebuild.
    try:
        # Late import so this file stays runnable standalone in envs
        # where the Flask app module isn't installed (test boxes).
        sys.path.insert(0, os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..')))
        import trends_iq  # type: ignore
        for lookback in (7, 30):
            filters = {
                'geo_type':      'National',
                'geo_value':     '',
                'lookback_days': lookback,
            }
            t0 = time.time()
            trends_iq.compute_view(filters, force_refresh=True)
            print(f"cache warm: National last-{lookback}d "
                   f"rebuilt in {time.time() - t0:.1f}s")
    except Exception as e:
        logging.warning("run_all: dashboard cache warm failed: %s", e)

    total_elapsed = time.time() - started
    print(f"\ntrends scrapers complete in {total_elapsed:.1f}s")
    print(f"{'source':<12} {'kind':<9} {'count':>6}  {'elapsed':>8}  error")
    print('-' * 70)
    fail_count = 0
    empty_sources: list[tuple[str, str]] = []  # (source, kind) that need cookies
    for r in sorted(results, key=lambda x: x.get('source', '')):
        err = r.get('error') or ''
        if err:
            fail_count += 1
        count = len(r.get('national') or [])
        kind = r.get('kind') or ''
        # Retailers/streaming with 0 items are always cookie-donation
        # candidates. TikTok specifically is a soft-fail case: the CC
        # anonymously exposes 3 preview cards, so anything <=5 (rather
        # than exactly 0) is a signal the operator should donate
        # ads.tiktok.com cookies to unlock the full list.
        if kind in {'retailer', 'streaming'} and count == 0:
            empty_sources.append((r.get('source', ''), kind))
        elif r.get('source') == 'tiktok' and count <= 5:
            empty_sources.append(('tiktok', 'social'))
        print(f"{r.get('source', ''):<12} {kind:<9} "
               f"{count:>6}  "
               f"{(r.get('orchestrator_elapsed_s') or r.get('scrape_elapsed_s') or 0):>7.1f}s  "
               f"{err[:60]}")

    # Empty retailer / streaming feeds are almost always a bot-block or
    # a missing session. Print the exact `donate_cookies.py` command the
    # operator needs to run. Netflix uses public TSVs so it's never in
    # this list even when it fails (that would be a network issue, not
    # a cookie issue).
    if empty_sources:
        domain_map = {
            # Retailers
            'target':     'target.com',     'walmart':    'walmart.com',
            'etsy':       'etsy.com',       'sephora':    'sephora.com',
            'lululemon':  'lululemon.com',  'bestbuy':    'bestbuy.com',
            'nike':       'nike.com',       'ulta':       'ulta.com',
            # Streaming (Disney+ / ESPN+ intentionally omitted - they
            # run from Jenna's laptop via local_residential_run.py
            # because Bamgrid IP-gates Hetzner)
            'hulu':       'hulu.com',
            'max':        'max.com',        'primevideo': 'amazon.com',
            # Social - TikTok CC hashtag list is login-gated as of 2026-07
            'tiktok':     'ads.tiktok.com',
        }
        need = [domain_map[s] for s, _k in empty_sources if s in domain_map]
        if need:
            print()
            print(f"COOKIE_DONATION_NEEDED: {', '.join(need)}")
            print(f"From your laptop:  python3 scripts/trends_scrapers/donate_cookies.py {' '.join(need)}")

    return 0 if fail_count < len(results) else 2


if __name__ == '__main__':
    sys.exit(main())
