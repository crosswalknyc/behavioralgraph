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
    python3 -m scripts.trends_scrapers.run_all --only music_charts,book_charts   # subset
    python3 -m scripts.trends_scrapers.run_all --skip hulu,primevideo            # skip Playwright

Each scraper is executed in a thread pool so a slow one (a Playwright
warm-up, or a JustWatch top-100 page) doesn't block the fast ones.
Playwright-based scrapers still launch their own browsers in parallel;
if you're on a small VM cap the workers with `--workers 3`.

Retailer scrapers (bestbuy, target, walmart, etsy, sephora, nike,
lululemon) were sunset 2026-09-10 (Jenna). The dashboard's Products
card was already retired 2026-07-28; this cleanup removes the daily
scrapes that were still running against dead selectors / anti-bot
walls.
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
    # Vizio WatchFree+ and MyFree DIRECTV (2026-09-22, Jenna: "for FAST
    # Vizio, DirecTV, and LG be included"). Neither has a JustWatch
    # package, so neither has a titles catalogue; both ship the Channel
    # Ranker only, off the platform's own public guide. Vizio's answers
    # the build box identically to a residential address; DIRECTV's
    # needs Chrome's TLS fingerprint but no residential hop. LG Channels
    # is the third of the set and is NOT here: its API is geo-gated, so
    # it runs from `local_residential_run.py`.
    ('vizio_watchfree',    'scripts.trends_scrapers.vizio_watchfree',    'Vizio WatchFree+',     'fast'),
    ('myfree_directv',     'scripts.trends_scrapers.myfree_directv',     'MyFree DIRECTV',       'fast'),
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
    # Retailer scrapers (bestbuy, nike, lululemon, etsy, sephora, target,
    # walmart) were sunset 2026-09-10 (Jenna: "remove retailers, that's
    # sunset"). The Products card was already retired from the dashboard
    # 2026-07-28; the scrapes had been silently returning 0 categories
    # against site-selector drift / anti-bot walls for weeks. If a retail
    # signal comes back into scope, re-add its tuple here.
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
    # AMC+ (2026-09-14). Same JustWatch path as Paramount+ / Peacock,
    # single package `acp`. The Apple TV channel package `aat` is a
    # storefront for the same catalog, not a tier, so it stays out.
    ('amcplus',       'scripts.trends_scrapers.amcplus',       'AMC+',       'streaming'),
    # Starz on Amazon (2026-09-15). Not a scrape: Starz sold through
    # Prime Video Channels is the same entitlement and the same title
    # list as Starz, so this mirrors `latest/starz.json` under its own
    # slug. Runs here rather than residentially because it only reads
    # S3. Sits after the other streaming scrapers and before the depth
    # extender, which serves both panels off the one Starz block.
    ('starz_amazon',  'scripts.trends_scrapers.starz_amazon',  'Starz on Amazon', 'streaming'),
    # Streaming depth extender (2026-09-09, Jenna: every list carries
    # 100+ items where the source has them). JustWatch top-100 films +
    # top-100 shows per platform for the residential-scraped streamers
    # (Netflix / Hulu / Disney+ / HBO Max / BritBox / MGM+ / Starz) and
    # Prime Video. trends_iq merges these under each platform's own
    # snapshot rows, so official orderings keep the top ranks.
    ('streaming_depth', 'scripts.trends_scrapers.streaming_depth', 'Streaming Depth', 'streaming'),
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


# ---------------------------------------------------------------------------
# Which lane the nightly estimator uses.
#
# 2026-09-14 (Jenna approved): the nightly pass now goes through the
# discounted asynchronous lane instead of issuing one request per item.
# Same prompts, same tiering, same web_search behaviour - the only
# differences are the price (half) and that the work is handed over as
# one job and polled to completion.
#
# Ordering is unchanged. The estimator call below still blocks until
# every result is back and written, so the coverage gate, the dated
# snapshot write, the index write, and the cache warm all still run
# strictly after the values land. Nothing downstream can race ahead.
#
# Set TRENDS_ESTIMATOR_SERIAL=1 in the environment to fall back to the
# per-item lane for one run (ops escape hatch on the box only; this is
# never a request field on any dashboard or partner surface).
_ESTIMATOR_BATCH_MODE = (
    os.environ.get('TRENDS_ESTIMATOR_SERIAL', '').strip().lower()
    not in ('1', 'true', 'yes')
)


def _run_one(source: str, module_path: str, label: str, kind: str,
              fetch_kwargs: dict | None = None) -> dict:
    """Import `module_path`, run its `fetch` through `run_scraper`, and
    return the payload with an elapsed stamp.

    `fetch_kwargs` lets a caller pass options into a scraper's `fetch`
    without changing that scraper's default behaviour for anyone else
    (used by the nightly estimator to take the discounted lane while
    the CLI and the backfill tool keep their own defaults)."""
    started = time.time()
    try:
        module = __import__(module_path, fromlist=['fetch'])
        from scripts.trends_scrapers._base import run_scraper  # local import
        if fetch_kwargs:
            def _fetch(_f=module.fetch, _kw=dict(fetch_kwargs)):
                return _f(**_kw)
        else:
            _fetch = module.fetch
        payload = run_scraper(source, label, kind, _fetch)
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
        from scripts.trends_scrapers._base import S3_BUCKET
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
    """Take the run lock, arm the runtime watchdog, then run.

    The lock keeps two runs off the same `latest/*.json` keys. Before
    2026-09-15 nothing did: a run from 2026-07-16 was still on the
    process table 61 days later, blocked on a driver handshake, and
    every nightly cron since had started alongside it.
    """
    from scripts.trends_scrapers.run_guard import RunLock, start_watchdog

    with RunLock() as lock:
        if not lock.acquired:
            # RunLock has already logged and alerted. Exit quietly
            # rather than starting a second pass over the same keys.
            return 3
        watchdog_done = start_watchdog()
        try:
            return _run_main(argv)
        finally:
            watchdog_done.set()


def _run_main(argv: list[str] | None = None) -> int:
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
                fetch_kwargs={'batch_mode': _ESTIMATOR_BATCH_MODE},
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

    # ------------------------------------------------------------------
    # Coverage gate (2026-09-09): after estimates land, recompute the
    # rendered payload and price EVERY non-Film item still missing a
    # researched US Audience value through the same machinery (tiering
    # intact, no budget cap - Jenna 2026-09-09: completeness wins over
    # cost). Universe derives from the payload itself, so a tab added
    # later is covered the day it ships. Merges into latest/ + today's
    # dated snapshot, purges live caches, logs the final coverage
    # percentage, and emails jenna@ + jessie@ if anything is STILL
    # missing after the pass (the dashboard renders such rows with a
    # neutral blank chip, never an error).
    coverage_summary = None
    if (not only or 'coverage_gate' in only) and 'coverage_gate' not in skip:
        try:
            from scripts.trends_scrapers.coverage_gate import run_gate
            coverage_summary = run_gate()
            results.append({
                'source':  'coverage_gate',
                'kind':    'meta',
                'count':   coverage_summary.get('priced_stream', 0)
                           + coverage_summary.get('priced_headline', 0),
                'elapsed': 0,
                'national': [],
            })
        except Exception as e:
            logging.exception("run_all: coverage gate crashed")
            results.append({'source': 'coverage_gate', 'error': str(e),
                            'national': []})

        # Single-provenance rank: a platform tile's rank is the
        # title's position ordered by that day's audience. This runs
        # AFTER the gate, not before it (where it sat until
        # 2026-09-15), because the gate re-prices whatever the
        # estimator missed. Ordering the stored snapshots first meant
        # any sizeable gate pass left the ranking describing values
        # that were no longer the ones on the page: Wednesday at #198
        # with 885,913 against Stranger Things at #88 with 225,041.
        # Re-seats view-carrying rows in each platform snapshot
        # (latest + today's dated copy) and keeps items' chart labels
        # in step. The render side derives rank from the values it is
        # actually showing, so this keeps the stored copy in agreement
        # with the page rather than being the page's only defence.
        # Non-fatal.
        try:
            from scripts.trends_scrapers.stream_estimates import (
                align_snapshot_ranks)
            today_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            for folder in ('latest', today_iso):
                align_snapshot_ranks(folder)
        except Exception:
            logging.exception("run_all: platform rank alignment crashed "
                               "(non-fatal)")

        # The dashboard reads up to 60 dated days of estimates per
        # request and only needs three fields out of each, so it reads
        # them from a lean sibling index roughly twenty times smaller
        # than the snapshot. The index is written beside every snapshot
        # as it lands, but the gate re-prices and the rank alignment
        # above rewrites the day in place, so today's index is stale by
        # the time we get here and some earlier day may have been
        # touched by a repair script since. This pass puts the trailing
        # window back in agreement.
        #
        # Cheap in the healthy case: a day whose index already matches
        # its snapshot costs a HEAD and a small read, and only a day
        # that actually drifted pays for the full download. An index
        # that does not match is ignored at read time anyway, so a
        # failure here costs latency, never correctness. Non-fatal.
        try:
            from scripts.trends_scrapers import stream_window_index
            _swi_t0 = time.time()
            _swi = stream_window_index.reconcile()
            logging.info(
                "run_all: stream window index reconcile in %.1fs "
                "(%d checked, %d current, %d rebuilt, %d without a "
                "snapshot, %d failed)",
                time.time() - _swi_t0, _swi['checked'], _swi['current'],
                _swi['rebuilt'], _swi['missing_source'], _swi['failed'])
        except Exception:
            logging.exception("run_all: stream window index reconcile "
                               "crashed (non-fatal)")

        # The re-seated ranks are in the snapshots but the payload the
        # gate warmed was built before them, so drop it and let the
        # warm step below rebuild.
        try:
            import trends_iq
            n = trends_iq.invalidate_live_compute_view_caches()
            logging.info("run_all: purged %d cached payload(s) after rank "
                          "alignment", n)
        except Exception:
            logging.exception("run_all: post-rank cache purge crashed "
                               "(non-fatal)")

        # Quality alarm on what actually got published, measured in
        # two parts. A row with no reading of its own now carries its
        # own most recent one forward, which is honest but stale; only
        # a row with no reading anywhere falls to a number derived
        # from its rank slot, which reads on the page exactly like a
        # real one. Both are alertable and the second should be rare.
        # On 2026-09-15 it was most of the board for most of the day
        # and nothing noticed until a colleague did.
        try:
            from scripts.trends_scrapers.run_guard import check_baseline_share
            if coverage_summary:
                check_baseline_share(coverage_summary)
        except Exception:
            logging.exception("run_all: provenance share check crashed "
                               "(non-fatal)")

        # A bulk rewrite can quietly make the numbers stop looking
        # counted. The cheapest way to force values apart is to skip
        # digits, and an earlier build did exactly that to avoid round
        # numbers, leaving zero unused corpus-wide until an outside
        # reader spotted the gap.
        try:
            from scripts.trends_scrapers.run_guard import (
                check_last_digit_distribution)
            import boto3 as _b3
            _blob = _b3.client('s3').get_object(
                Bucket='dashboard-inputs',
                Key='trends_iq_snapshots/latest/stream_estimates.json'
            )['Body'].read()
            _items = (json.loads(_blob).get('items') or {})
            check_last_digit_distribution(
                v.get('us_estimate') for v in _items.values()
                if isinstance(v, dict))
        except Exception:
            logging.exception("run_all: last-digit check crashed "
                               "(non-fatal)")

    # ------------------------------------------------------------------
    # Publish what the streaming section used to work out per request.
    #
    # Its cold cost was 42.0s, and the two largest pieces of it were
    # answers that only change when this run lands: the weeks-on-chart
    # history (1,008 small archive reads) and poster art (279 lookups
    # against three outside services). Both are resolved once here and
    # read as a single object on the request path.
    #
    # Both run after the platform scrapers and the depth extension, so
    # they describe the board this run just published, and both sit
    # outside the coverage-gate block because neither depends on it.
    # Non-fatal either way: a failure costs the old latency on the
    # read side, never a wrong value, because the read side falls back
    # to working it out itself whenever a published answer is missing
    # or does not cover what it needs.
    if 'streaming_reads' not in skip:
        try:
            from scripts.trends_scrapers import streaming_weeks_index
            import trends_iq as _tiq
            _swx_t0 = time.time()
            _swx = streaming_weeks_index.rebuild(
                [s for s, _, _ in _tiq.STREAMING_PLATFORMS])
            logging.info(
                "run_all: streaming weeks index in %.1fs "
                "(%d platforms, %d titles, anchored %s, %d days covered)",
                time.time() - _swx_t0, _swx['platforms'], _swx['titles'],
                _swx['anchor_date'], _swx['cover_days'])
        except Exception:
            logging.exception("run_all: streaming weeks index crashed "
                               "(non-fatal)")

        try:
            from scripts.trends_scrapers import streaming_poster_cache
            _spc_t0 = time.time()
            _spc = streaming_poster_cache.build()
            logging.info(
                "run_all: streaming poster art in %.1fs "
                "(%d entries, %d with art, %d carried, %d resolved now, "
                "%d refreshed)",
                time.time() - _spc_t0, _spc['entries'], _spc['with_art'],
                _spc['carried'], _spc['resolved_now'], _spc['refreshed'])
        except Exception:
            logging.exception("run_all: streaming poster art crashed "
                               "(non-fatal)")

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
    if coverage_summary:
        print(f"US Audience coverage: "
              f"researched={coverage_summary.get('researched_after_pct')}% "
              f"rendered={coverage_summary.get('rendered_after_pct')}% "
              f"(priced {coverage_summary.get('priced_stream', 0)} stream + "
              f"{coverage_summary.get('priced_headline', 0)} headline items, "
              f"${coverage_summary.get('spend_usd', 0.0):.2f}, "
              f"still_missing={coverage_summary.get('still_missing', 0)})")
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
        # Streaming feeds with 0 items are cookie-donation candidates.
        # Retailer scrapers (bestbuy/target/walmart/etsy/sephora/nike/
        # lululemon) were sunset 2026-09-10; social sources were killed
        # 2026-08-20. Only streaming remains.
        if kind == 'streaming' and count == 0:
            empty_sources.append((r.get('source', ''), kind))
        print(f"{r.get('source', ''):<12} {kind:<9} "
               f"{count:>6}  "
               f"{(r.get('orchestrator_elapsed_s') or r.get('scrape_elapsed_s') or 0):>7.1f}s  "
               f"{err[:60]}")

    # Empty streaming feeds are almost always a bot-block or a missing
    # session. Print the exact `donate_cookies.py` command the operator
    # needs to run. Netflix uses public TSVs so it's never in this list
    # even when it fails (that would be a network issue, not a cookie
    # issue). Retailer domain map was removed 2026-09-10 when those
    # scrapers were sunset.
    if empty_sources:
        domain_map = {
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
