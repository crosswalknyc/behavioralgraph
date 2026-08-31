"""
Post-donation refresh chain for Trends IQ.

When the operator donates cookies for a domain (donate_cookies.py),
the affected scraper(s) should re-run IMMEDIATELY so the dashboard
tile comes back within minutes - not on the next scheduled cron.
This module is that chain:

    1. re-run the scraper(s) mapped to the donated domain(s)
    2. purge today's live Trends IQ dashboard cache entries
    3. warm the default dashboard view so the next page load is hot

It runs in two places:

    - On the operator's laptop, for residential-only scrapers
      (Disney+, Hulu, Netflix, BritBox, Starz, Xbox, film ticketing...).
      donate_cookies.py invokes it as a subprocess right after upload.
    - On Hetzner, for datacenter scrapers (retailers, Prime Video,
      music/podcast/book charts). donate_cookies.py triggers it over
      SSH as a detached nohup job so the laptop terminal isn't blocked
      by slow Playwright warm-ups.

CLI:
    # residential modules, then purge + warm
    python3 -m scripts.trends_scrapers.refresh_after_donation \
        --local-modules hulu,britbox --warm

    # Hetzner run_all sources, then purge + warm
    python3 -m scripts.trends_scrapers.refresh_after_donation \
        --runall-sources primevideo,music_charts --warm

    # purge + warm only (no scraping)
    python3 -m scripts.trends_scrapers.refresh_after_donation --purge-only --warm

Module-level imports are stdlib-only on purpose: donate_cookies.py
loads this file via importlib to read DOMAIN_REFRESH_MAP without
needing the bg-webapp package on sys.path.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger('refresh_after_donation')

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Which scraper(s) consume each donated cookie domain, and where they
# run. 'local' entries are residential-only scraper modules (run on the
# operator laptop, same modules local_residential_run.py drives).
# 'runall' entries are source keys understood by
# `scripts.trends_scrapers.run_all --only ...` on Hetzner.
#
# Domains with no entry (dead social scrapers, Microdramas IQ domains)
# get no auto-refresh; donate_cookies prints a "next scheduled run"
# note for those.
DOMAIN_REFRESH_MAP: dict[str, dict[str, list[str]]] = {
    # Residential streamers - laptop
    'disneyplus.com':   {'local': ['disneyplus', 'espnplus']},
    'hbomax.com':       {'local': ['max_streaming']},
    'netflix.com':      {'local': ['netflix']},
    'hulu.com':         {'local': ['hulu']},
    'britbox.com':      {'local': ['britbox']},
    'mgmplus.com':      {'local': ['mgmplus']},
    'starz.com':        {'local': ['starz']},
    'xbox.com':         {'local': ['xbox_gamepass']},
    # Meta Horizon Store (Top Free + Top Paid). Runs on Hetzner via
    # run_all (anonymous curl_cffi impersonation is sufficient today).
    # oculus.com listed alongside meta.com so a donation for either
    # domain triggers the same refresh (Meta rebranded from Oculus
    # but a few store paths still 302 through oculus.com).
    'meta.com':         {'runall': ['meta_quest']},
    'oculus.com':       {'runall': ['meta_quest']},
    # YouTube podcasts (public HTML on youtube.com/podcasts). The
    # scrape runs anonymously today so a donation is a no-op, but
    # keeping the mapping in place means the operator's "just donated
    # youtube" gesture triggers a `podcast_charts` refresh (YouTube
    # Podcasts is one of its sources) without any additional wiring
    # once youtube.com goes auth-gated for the podcasts surface.
    # music.youtube.com and podcasts.youtube.com listed alongside so
    # any of the three donation flows triggers the same refresh.
    'youtube.com':          {'runall': ['podcast_charts']},
    'music.youtube.com':    {'runall': ['podcast_charts']},
    'podcasts.youtube.com': {'runall': ['podcast_charts']},
    # Steam (Valve). All endpoints are public / anonymous so a
    # donation is a no-op today, but keeping the map in place means
    # a future auth requirement (login-gated storefront APIs) picks
    # up the same refresh chain without a wiring change. Both the
    # store host and the api host map to the same source key.
    'steampowered.com': {'runall': ['steam_charts']},
    'store.steampowered.com': {'runall': ['steam_charts']},
    'api.steampowered.com':   {'runall': ['steam_charts']},
    # Film ticketing - one module scrapes all sites
    'amctheatres.com':  {'local': ['film_ticketing']},
    'regmovies.com':    {'local': ['film_ticketing']},
    # Retailers - Hetzner
    'target.com':       {'runall': ['target']},
    'walmart.com':      {'runall': ['walmart']},
    'etsy.com':         {'runall': ['etsy']},
    'sephora.com':      {'runall': ['sephora']},
    'lululemon.com':    {'runall': ['lululemon']},
    'bestbuy.com':      {'runall': ['bestbuy']},
    'nike.com':         {'runall': ['nike']},
    # amazon.com session feeds Prime Video; music.amazon.com feeds the
    # Amazon Music chart + podcast rails (donated ad hoc, not in
    # DEFAULT_DOMAINS).
    'amazon.com':       {'runall': ['primevideo', 'comics_charts']},
    'music.amazon.com': {'runall': ['music_charts', 'podcast_charts']},
    'audible.com':      {'runall': ['podcast_charts', 'book_charts']},
    # Amazon.com session doesn't gate the Comics bestsellers endpoint
    # (public HTML, no auth) but we run comics_charts anyway on an
    # amazon.com donation so the operator's "just donated amazon"
    # gesture refreshes every amazon-adjacent snapshot in one shot.
    # TikTok Creative Center session feeds the viral-songs chart
    # inside music_charts.
    'ads.tiktok.com':   {'runall': ['music_charts']},
}

# Lookback windows the dashboard UI exposes (templates/index.html
# #trendsIQLookback) plus the backend default (1). Today's live cache
# entry for each of these is what we purge after a refresh.
_UI_LOOKBACK_DAYS = (1, 3, 7, 14, 30)

# The window the UI selects by default - the one we pre-warm.
_DEFAULT_UI_LOOKBACK = 7


def plan_refresh(domains: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split donated domains into (local_modules, runall_sources,
    unmapped_domains). Module/source lists are deduped, order-stable."""
    local: list[str] = []
    runall: list[str] = []
    unmapped: list[str] = []
    for domain in domains:
        spec = DOMAIN_REFRESH_MAP.get((domain or '').strip().lower())
        if not spec:
            unmapped.append(domain)
            continue
        for mod in spec.get('local', []):
            if mod not in local:
                local.append(mod)
        for src in spec.get('runall', []):
            if src not in runall:
                runall.append(src)
    return local, runall, unmapped


def _import_trends_iq():
    """Import bg-webapp's trends_iq with the repo root on sys.path."""
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import trends_iq  # noqa: PLC0415
    return trends_iq


def run_local_modules(modules: list[str]) -> int:
    """Run each residential scraper module as a subprocess (same
    isolation pattern as local_residential_run). Output streams to the
    parent terminal so the operator sees live progress. Returns the
    count of modules that exited 0."""
    ok = 0
    for mod in modules:
        logger.info("re-running scraper after donation: %s", mod)
        proc = subprocess.run(
            [sys.executable, '-m', f'scripts.trends_scrapers.{mod}'],
            cwd=str(_REPO_ROOT),
        )
        if proc.returncode == 0:
            ok += 1
        else:
            logger.warning("scraper %s exited %d", mod, proc.returncode)
    return ok


def run_runall_sources(sources: list[str]) -> int:
    """Run `run_all --only <sources>` as a subprocess (Hetzner side).
    Returns the subprocess exit code."""
    logger.info("re-running run_all --only %s after donation", ','.join(sources))
    proc = subprocess.run(
        [sys.executable, '-m', 'scripts.trends_scrapers.run_all',
         '--only', ','.join(sources)],
        cwd=str(_REPO_ROOT),
    )
    if proc.returncode != 0:
        logger.warning("run_all exited %d", proc.returncode)
    return proc.returncode


def purge_dashboard_cache() -> int:
    """Delete today's live Trends IQ dashboard cache entries so the
    next dashboard load recomputes from the fresh snapshots. Historic
    (asof < today) entries are untouched - their keys hash a past date
    and never collide with today's. Returns the number of keys deleted."""
    trends_iq = _import_trends_iq()
    import boto3  # noqa: PLC0415
    keys = [trends_iq._cache_key({'lookback_days': d}) for d in _UI_LOOKBACK_DAYS]
    s3 = boto3.client('s3')
    resp = s3.delete_objects(
        Bucket=trends_iq.S3_CACHE_BUCKET,
        Delete={'Objects': [{'Key': k} for k in keys], 'Quiet': True},
    )
    errors = resp.get('Errors') or []
    deleted = len(keys) - len(errors)
    logger.info("purged %d/%d live dashboard cache entries", deleted, len(keys))
    return deleted


def warm_default_view() -> bool:
    """Recompute + re-cache the dashboard's default view (7-day window)
    so the operator's next page load is instant. Best-effort: a failure
    just means the first load rebuilds on demand."""
    try:
        trends_iq = _import_trends_iq()
        logger.info("warming default dashboard view (lookback=%d)...",
                    _DEFAULT_UI_LOOKBACK)
        trends_iq.compute_view({'lookback_days': _DEFAULT_UI_LOOKBACK},
                               force_refresh=True)
        logger.info("default view warmed")
        return True
    except Exception as e:
        logger.warning("warm_default_view failed (%s); dashboard will "
                       "rebuild on next load instead", e)
        return False


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser(description='Post-donation Trends IQ refresh chain')
    ap.add_argument('--local-modules', default='',
                    help='comma-separated residential scraper modules to re-run')
    ap.add_argument('--runall-sources', default='',
                    help='comma-separated run_all source keys to re-run')
    ap.add_argument('--purge-only', action='store_true',
                    help='skip scraping; just purge cache (and warm if --warm)')
    ap.add_argument('--warm', action='store_true',
                    help='recompute the default dashboard view after the purge')
    args = ap.parse_args()

    local = [m for m in args.local_modules.split(',') if m.strip()]
    runall = [s for s in args.runall_sources.split(',') if s.strip()]

    if not args.purge_only:
        if local:
            run_local_modules(local)
        if runall:
            run_runall_sources(runall)
        if not local and not runall:
            logger.info("nothing to scrape (no modules/sources given)")

    try:
        purge_dashboard_cache()
    except Exception as e:
        logger.warning("cache purge failed: %s", e)

    if args.warm:
        warm_default_view()

    logger.info("refresh chain complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())
