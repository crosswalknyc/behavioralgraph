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
import socket
import sys
import time
from datetime import datetime, timezone, timedelta


SCRAPERS = [
    # (source_key, module_path, label, kind)
    ('google_wide',        'scripts.trends_scrapers.google_trends_wide', 'Google Trends (wide)', 'search'),
    ('wikipedia_trending', 'scripts.trends_scrapers.wikipedia_trending', 'Wikipedia',            'search'),
    ('music_charts',       'scripts.trends_scrapers.music_charts',       'Music',                'music'),
    ('podcast_charts',     'scripts.trends_scrapers.podcast_charts',     'Podcasts',             'podcast'),
    # Standalone snapshot for the YouTube rail. The rail is already
    # inside podcast_charts.json (dashboard-facing), but this per-source
    # file lets a partner poll `youtube_podcasts.json` directly without
    # unpacking the consolidated snapshot. Same parser, so the two never
    # drift; extra cost is one public HTTP GET per day.
    ('youtube_podcasts',   'scripts.trends_scrapers.youtube_podcasts',   'YouTube Popular Podcasts', 'podcast'),
    ('book_charts',        'scripts.trends_scrapers.book_charts',        'Books',                'book'),
    # Comics / manga / graphic novels: Amazon Comics & Graphic Novels
    # bestsellers + Apple Books Comics genre RSS + Libby Comics via
    # LA County OverDrive. Same-day cost is low (all three sources are
    # public HTML / RSS / JSON, no cookies, ~5s wall time), so it runs
    # alongside book_charts in the standard daily batch.
    ('comics_charts',      'scripts.trends_scrapers.comics_charts',      'Comics',               'comics'),
    ('libby_trends',       'scripts.trends_scrapers.libby_trends',       'Libby popular',        'libby'),
    # Wattpad serialized fiction: six rails (Hot / Originals / four
    # genre rankings) rolled up into a single snapshot. Rides on the
    # Books tab as a sixth source alongside Amazon / Apple / Audible
    # / Libby. Public browse surfaces; no cookies required.
    ('wattpad_charts',     'scripts.trends_scrapers.wattpad_charts',     'Wattpad',              'wattpad'),
    # Goodreads community-driven weekly read chart. One rail today
    # (Most Read This Week, ~50 titles). Rides on the Books tab as a
    # seventh source right after Amazon Kindle so the community
    # signal reads adjacent to the retail signal it summarizes.
    # Public browse surface; no cookies required (curl_cffi Chrome-
    # TLS impersonation used defensively).
    ('goodreads_charts',   'scripts.trends_scrapers.goodreads_charts',   'Goodreads',            'goodreads'),
    # Broadway weekly attendance: single-panel scrape of the Playbill
    # grosses page, which mirrors the Broadway League Tuesday report.
    # One row per currently-running production sorted by attendance
    # desc. Public HTML surface; curl_cffi Chrome-TLS impersonation
    # used defensively. Attendance is native (no Claude estimator).
    ('broadway_grosses',   'scripts.trends_scrapers.broadway_grosses',   'Broadway',             'broadway'),
    ('philanthropy_news',  'scripts.trends_scrapers.philanthropy_news',  'Philanthropy news',    'news'),
    ('business_news',      'scripts.trends_scrapers.business_news',      'Business news',        'news'),
    ('wall_street_news',   'scripts.trends_scrapers.wall_street_news',   'Wall Street news',     'news'),
    # FAST (Free Ad-Supported Streaming TV): one snapshot covering the
    # top 100 titles on Roku Channel, Tubi, Pluto TV, and Amazon
    # (Prime Video ad-tier, which absorbed Freevee in Nov 2024). Data
    # comes from JustWatch's public GraphQL - no cookies, no
    # datacenter-IP blocking.
    ('fast_channels',      'scripts.trends_scrapers.fast_channels',      'FAST channels',        'fast'),
    # Lens scoring depends on every OTHER latest snapshot being in
    # place first (it reads them all to build the item universe).
    # Kept AFTER all content scrapers so a same-day run picks up
    # today's fresh chart/podcast/etc. items instead of yesterday's.
    ('lens_scores',        'scripts.trends_scrapers.lens_relevance',     'Persona lens scores',  'meta'),
    # Social scrapers (Reddit, YouTube trending, TikTok, Instagram, X)
    # were removed from the daily cron 2026-08-20 (Jenna: "kill the
    # scrape too"). The social panel was dropped from the Trends IQ
    # surface because signal quality wasn't where it needed to be, and
    # the daily API/scraping cost is no longer justified. If we bring
    # any social source back, add its (source, module, label, 'social')
    # tuple back here and re-wire `_fetch_social_trending` into
    # `compute_view` in trends_iq.py.
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
    #   - HBO Max: play.hbomax.com IP-gates similarly.
    #   - Hulu (2026-07): kept returning 0 items from Hetzner despite
    #     valid donated cookies; Hulu WAFs the datacenter IP pre-auth.
    ('primevideo', 'scripts.trends_scrapers.primevideo',    'Prime Video', 'streaming'),
    # Paramount+ and Peacock (2026-09-04). Both ride JustWatch's public
    # GraphQL - the same no-cookie, no-IP-block path fast_channels uses
    # - so they run fine from Hetzner's datacenter IP. No residential
    # hop, no donated session, no cookie-donation domain.
    ('paramountplus', 'scripts.trends_scrapers.paramountplus', 'Paramount+', 'streaming'),
    ('peacock',       'scripts.trends_scrapers.peacock',       'Peacock',    'streaming'),
    # Meta Horizon Store (formerly Oculus). One snapshot with two
    # panels (Top Free + Top Paid) - matches how the store surfaces
    # its own rails on the Games landing page. Anonymous fetch works
    # via curl_cffi Chrome-TLS impersonation; no cookies needed today.
    ('meta_quest', 'scripts.trends_scrapers.meta_quest',    'Meta Quest', 'gaming'),
    # Steam (Valve). One snapshot with two panels (Most Played by
    # 24-hour peak concurrent, Top Sellers by weekly US revenue).
    # All three endpoints (ISteamChartsService/GetMostPlayedGames,
    # IStoreTopSellersService/GetWeeklyTopSellers,
    # IStoreBrowseService/GetItems) are anonymous public JSON; no
    # cookies needed. curl_cffi Chrome-TLS impersonation covers
    # Steam's basic rate-limit posture from the Hetzner box.
    ('steam_charts', 'scripts.trends_scrapers.steam_charts', 'Steam',      'gaming'),
]


# ---------------------------------------------------------------------------
# Manifest check.
#
# `_manifest.json` (git-tracked, in this same directory) lists every Python
# file that should be present under scripts/trends_scrapers/. `_check_manifest`
# runs at the top of main() BEFORE any scraper fans out, walks the manifest,
# and verifies every listed file resolves on disk. Missing files log a WARN
# and best-effort trigger a once-per-day SES email to jenna@ + liz@ via
# cookie_gap_notify.notify_scraper_manifest_drift.
#
# This is the guardrail that catches the class of gap where a scraper lands
# on origin/main but never gets rsync'd to Hetzner (Hetzner is populated by
# rsync, not `git pull`, so a commit-and-push without rsync silently 404s at
# cron time; comics_charts.py on 2026-08-31 is the case that motivated this).
#
# Non-blocking: cron continues to run every scraper it can find. The scrapers
# in the manifest but missing from disk will simply fail to import inside
# `_run_one` (which already catches and logs); the email is the ops signal.
# The manifest just lists filenames, not MD5s, so committing to a scraper
# does not require re-committing the manifest.
# ---------------------------------------------------------------------------
def _check_manifest(scrapers_dir: str) -> list[str]:
    """Return the list of filenames in _manifest.json that are missing
    on disk. Best-effort: if the manifest itself can't be read, log a
    WARN and return []."""
    manifest_path = os.path.join(scrapers_dir, '_manifest.json')
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
    except FileNotFoundError:
        logging.warning(
            "run_all: manifest %s not found; skipping drift check",
            manifest_path,
        )
        return []
    except Exception as e:
        logging.warning(
            "run_all: manifest %s could not be parsed: %s; skipping drift check",
            manifest_path, e,
        )
        return []

    expected = doc.get('files') or []
    if not isinstance(expected, list) or not expected:
        logging.warning(
            "run_all: manifest %s has no files list; skipping drift check",
            manifest_path,
        )
        return []

    missing = [
        name for name in expected
        if not os.path.isfile(os.path.join(scrapers_dir, name))
    ]
    return missing


def _fire_manifest_drift_notice(missing: list[str], scrapers_dir: str) -> None:
    """Best-effort SES email + WARN log for missing scrapers. Never raises."""
    if not missing:
        return
    logging.warning(
        "run_all: scraper directory drift detected; %d file(s) missing: %s",
        len(missing), ', '.join(sorted(missing)),
    )
    try:
        from scripts.trends_scrapers.cookie_gap_notify import (
            notify_scraper_manifest_drift,
        )
    except Exception as e:
        logging.warning(
            "run_all: could not import notify_scraper_manifest_drift: %s", e,
        )
        return
    try:
        host = socket.gethostname() or 'unknown'
    except Exception:
        host = 'unknown'
    try:
        notify_scraper_manifest_drift(
            missing,
            host=host,
            scrapers_dir=scrapers_dir,
        )
    except Exception as e:
        logging.warning("run_all: manifest drift notify failed: %s", e)


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


# ---------------------------------------------------------------------------
# Freshness verify + alert.
#
# The whole Trends IQ dashboard reads `latest/stream_estimates.json` for
# every daily audience chip. If today's cron runs but produces zero fresh
# research (Anthropic credit exhausted, transient rate-limit, etc.), the
# in-scraper safety net preserves the prior snapshot rather than clobbering
# with an empty file, and the summary column shows a plausible-looking
# elapsed time. That's the right thing to do for the data, but it means
# the dashboard silently sits on yesterday's numbers with no operator
# signal until a user complains ("today's numbers are the same as
# yesterday"). This verify runs at the end of every cron and pages
# jenna@ + jessie@ if `target_date` on `latest/` isn't yesterday UTC.
# System alert, never Liz - matches `profile-iq-pipeline-rules.mdc` #6
# ("Failure / system alerts -> jenna@, jessie@ ONLY").
# ---------------------------------------------------------------------------

def _verify_stream_estimates_freshness() -> str | None:
    """Return None if `latest/stream_estimates.json` `target_date` equals
    yesterday UTC, else a short operator string describing the drift."""
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    try:
        import boto3  # imported lazily so unit tests don't need it
        s3 = boto3.client('s3')
        obj = s3.get_object(
            Bucket=S3_BUCKET,
            Key='trends_iq_snapshots/latest/stream_estimates.json',
        )
        data = json.loads(obj['Body'].read())
        td = data.get('target_date')
        item_count = len(data.get('items') or {})
        last_mod = obj.get('LastModified')
        if td != yesterday:
            return (
                f"target_date={td!r} (expected {yesterday!r}, yesterday UTC). "
                f"items={item_count}. lastMod={last_mod!s}. "
                "Dashboard will keep serving whatever is on latest/ until "
                "the next successful cron. Investigate: /var/log/trends_scrapers.log"
            )
        return None
    except Exception as e:  # pragma: no cover - best-effort verify
        return f"freshness check crashed: {type(e).__name__}: {e}"


def _send_freshness_alert(msg: str) -> None:
    """Send a system alert to jenna@ + jessie@ on freshness drift.

    Never Liz (workspace rule: system alerts go to jenna+jessie only).
    Best-effort - SES failures log and never raise so a bad SES config
    can't cascade the cron exit.
    """
    try:
        import boto3
        ses = boto3.client('ses', region_name='us-east-2')
        subject = "Trends: stream_estimates target_date drift"
        body_text = (
            "The daily Trends IQ cron completed but the latest "
            "stream_estimates snapshot on S3 does not reflect yesterday UTC.\n"
            "\n"
            f"Detail: {msg}\n"
            "\n"
            "What this means for users: every FAST / streaming / podcast / "
            "book audience chip on the dashboard is still reading whatever "
            "target_date lives on latest/. If that target_date is the same "
            "one served yesterday, users see today's numbers as identical "
            "to yesterday's numbers for the same window.\n"
            "\n"
            "Log: /var/log/trends_scrapers.log on Hetzner (168.119.215.48).\n"
            "Manual re-run:\n"
            "  cd /root/finished_codes/bg-webapp && \\\n"
            "  set -a && . /root/finished_codes/.env.trends_scrapers && \\\n"
            "  set +a && python3 -m scripts.trends_scrapers.stream_estimates"
        )
        ses.send_email(
            Source='BehavioralGraph <jenna@crosswalknyc.com>',
            Destination={'ToAddresses': [
                'jenna@crosswalknyc.com',
                'jessie@crosswalknyc.com',
            ]},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Text': {'Data': body_text}},
            },
        )
        logging.info("run_all: freshness drift alert sent to jenna+jessie")
    except Exception as e:
        logging.warning("run_all: freshness alert SES send failed: %s", e)


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

    # Manifest drift check runs first, before any scraper fans out.
    # See `_check_manifest` doc block above. Never blocks the run.
    try:
        scrapers_dir = os.path.dirname(os.path.abspath(__file__))
        missing = _check_manifest(scrapers_dir)
        if missing:
            _fire_manifest_drift_notice(missing, scrapers_dir)
    except Exception as e:
        logging.warning("run_all: manifest drift check crashed: %s", e)

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

    # headline_estimates: US daily-readership estimates (Claude Sonnet +
    # web_search per article) for every headline on the Trends IQ
    # Headlines tab. Runs AFTER philanthropy_news lands + inline
    # against the live NEWS_FEEDS pool (fetched inside the scraper).
    # Cost is ~90 web_search calls / day (~$2). Estimates stamp onto
    # `trending_headlines` + `articles_by_source[*].articles` +
    # `philanthropy_news` at request time via
    # `trends_iq._annotate_headlines_with_readers`.
    if (not only or 'headline_estimates' in only) and 'headline_estimates' not in skip:
        try:
            results.append(_run_one(
                'headline_estimates',
                'scripts.trends_scrapers.headline_estimates',
                'US Headline Readers',
                'meta',
            ))
        except Exception as e:
            logging.exception("run_all: headline_estimates post-step crashed")
            results.append({'source': 'headline_estimates', 'error': str(e), 'national': []})

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
        # Invalidate every LIVE compute_view cache entry first.  The
        # warm step below only rebuilds three canonical filter tuples
        # (National / 1d / 7d / 30d); any OTHER cached filter combo
        # (state cut, DMA cut, non-default lookback) would keep
        # serving its stale payload until its stale_until elapses -
        # up to 24 hours after this cron.  Invalidation forces every
        # user's first request to re-compute against the fresh
        # `latest/*.json` snapshots this run just wrote.  Historic
        # entries (asof=past-date) are permanent snapshots and are
        # NEVER touched.  Best-effort - if S3 isn't reachable the
        # canonical three still get warmed and everything else self-
        # heals within 24h anyway.
        try:
            n = trends_iq.invalidate_live_compute_view_caches()
            print(f"cache invalidate: {n} live compute_view entries cleared")
        except Exception as e:
            logging.warning("run_all: cache invalidation failed: %s", e)
        # Warm the three windows the dashboard actually renders. 1-day
        # is the new default ("live as of now") so it's warmed first
        # and most frequently checked by users; 7d and 30d cover the
        # medium-term views that some users still switch to.
        for lookback in (1, 7, 30):
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

    # Verify the dashboard is actually pointed at fresh research for the
    # completed day. If not, page jenna+jessie (system alert). Runs on
    # every cron invocation, not just the daily 12:00 UTC one - a manual
    # rerun that leaves latest/ pointed at the wrong day is also worth
    # paging on. Gated on `--only stream_estimates` NOT being set OR
    # stream_estimates being in the run: if the caller intentionally
    # skipped stream_estimates (--skip stream_estimates), don't alert.
    only_arg = set(_s.strip() for _s in (args.only or '').split(',') if _s.strip())
    skip_arg = set(_s.strip() for _s in (args.skip or '').split(',') if _s.strip())
    stream_est_ran = (not only_arg or 'stream_estimates' in only_arg) and \
                     'stream_estimates' not in skip_arg
    if stream_est_ran:
        try:
            fresh_msg = _verify_stream_estimates_freshness()
            if fresh_msg:
                print(f"FRESHNESS DRIFT: {fresh_msg}")
                _send_freshness_alert(fresh_msg)
            else:
                print("freshness: latest/stream_estimates.json target_date = yesterday UTC")
        except Exception:
            logging.exception("run_all: freshness verify crashed")

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
        # Non-meta scrapers put their items in `national`. Meta scrapers
        # (stream_estimates, headline_estimates, why_trending) return
        # `items` (a dict) and expose the row count as `count` while
        # leaving `national` empty. Read `count` first so the summary
        # column reflects what was actually written to S3; without this
        # a 7,132-item meta write shows as "0" in the log and looks
        # indistinguishable from a total failure.
        count = int(r.get('count') or 0) or len(r.get('national') or [])
        kind = r.get('kind') or ''
        # Retailers/streaming with 0 items are always cookie-donation
        # candidates. Social sources (including the old TikTok CC
        # preview-card guardrail) were removed 2026-08-20 when the
        # scrape was killed.
        if kind in {'retailer', 'streaming'} and count == 0:
            empty_sources.append((r.get('source', ''), kind))
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
            'max':        'hbomax.com',    'primevideo': 'amazon.com',
            # Social sources removed 2026-08-20 (scrape killed).
        }
        need = [domain_map[s] for s, _k in empty_sources if s in domain_map]
        if need:
            print()
            print(f"COOKIE_DONATION_NEEDED: {', '.join(need)}")
            print(f"From your laptop:  python3 scripts/trends_scrapers/donate_cookies.py {' '.join(need)}")

    return 0 if fail_count < len(results) else 2


if __name__ == '__main__':
    sys.exit(main())
