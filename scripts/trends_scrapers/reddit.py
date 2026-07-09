"""
Reddit trending scraper via public Atom RSS feed.

Reddit's public .json endpoints are 403'd for unauthenticated clients
now, but the Atom /r/popular/.rss endpoint still works with a
browser-style UA when hit from a residential IP. Render's datacenter
IPs are blocked, which is why the app-side live fetch returned zero
items in production and the tab rendered "No trending items right
now." Moving Reddit to a daily Hetzner cron (same egress that already
serves TikTok / X / YouTube / Instagram) makes it consistent with
every other social source.

Snapshot shape matches the "social" contract in `_base.py`:

    {
      "source":  "reddit",
      "label":   "Reddit",
      "kind":    "social",
      "national": [ { rank, title, url, subreddit, image, ...}, ... ],
      "by_state": { "New York": [...], "California": [...], ... },
      ...
    }

State-specific slices are populated for a curated set of major-market
subreddits (states with an active `/r/<state>` subreddit that mirrors
the geographic filter names the dashboard uses). Everything else
falls back to `national` at read time via `_snapshot_items_for_geo`.

Standalone:

    python3 -m scripts.trends_scrapers.reddit
"""

from __future__ import annotations

import html as _html
import logging
import random
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

logger = logging.getLogger(__name__)


# Reddit's RSS endpoint is quirky: plain `requests` (with a Firefox UA)
# returns 200s reliably, but the shared `http_get` helper via curl_cffi
# with Chrome TLS impersonation gets 429'd - the ja3 fingerprint plus
# the requests-from-datacenter pattern trips their WAF. Rolling our
# own small GET here bypasses curl_cffi for this scraper only.


# Curated set of the 15 largest-market states with an active `/r/<slug>`
# that reliably yields posts. Kept intentionally small so the scraper
# finishes under 60s and stays polite to Reddit's rate limits (all 50
# states would trigger throttling and drag the daily run past 30 min).
# Missing states transparently fall back to national via
# `_snapshot_items_for_geo` in the app.
_STATE_SUBREDDITS: dict[str, str] = {
    'California':     'california',
    'Texas':          'texas',
    'Florida':        'florida',
    'New York':       'newyork',
    'Pennsylvania':   'pennsylvania',
    'Illinois':       'illinois',
    'Ohio':           'ohio',
    'Georgia':        'georgia',
    'North Carolina': 'northcarolina',
    'Michigan':       'michigan',
    'New Jersey':     'newjersey',
    'Virginia':       'virginia',
    'Washington':     'washington',
    'Arizona':        'arizona',
    'Massachusetts':  'massachusetts',
}

# Delay between successive Reddit requests. 1s keeps us under the
# public RSS rate limit (roughly 60 req/min unauthenticated) with
# plenty of margin.
_PER_REQUEST_SLEEP_S = 1.0


_BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
                'Gecko/20100101 Firefox/120.0')

_ACCEPT_XML = 'application/atom+xml, application/xml, text/xml'


def _fetch_sub(sub: str, limit: int = 20, *, retries: int = 2,
                timeout: int = 15) -> list[dict]:
    """Pull up to `limit` entries from /r/<sub>/.rss. Returns [] on any
    failure so a single dead subreddit doesn't kill the run.

    Uses plain `requests` (not `_base.http_get`) because Reddit's WAF
    429s curl_cffi's Chrome TLS impersonation on this endpoint. Plain
    Python `requests` with a Firefox UA gets 200s consistently.
    """
    url = f'https://www.reddit.com/r/{sub}/.rss?limit={limit}'
    body = ''
    status = None
    for attempt in range(retries):
        try:
            r = requests.get(url,
                              headers={'User-Agent': _BROWSER_UA,
                                       'Accept':     _ACCEPT_XML},
                              timeout=timeout)
            status = r.status_code
            if status == 200:
                body = r.text or ''
                break
            if status in (429,) or status >= 500:
                sleep_s = 4 * (attempt + 1) + random.random()
                logger.info("reddit rss %s: http %d (attempt %d/%d, sleeping %.1fs)",
                             sub, status, attempt + 1, retries, sleep_s)
                time.sleep(sleep_s)
                continue
            logger.info("reddit rss %s: http %d (no retry)", sub, status)
            return []
        except Exception as e:
            sleep_s = 4 * (attempt + 1) + random.random()
            logger.info("reddit rss %s: %s (attempt %d/%d, sleeping %.1fs)",
                         sub, e, attempt + 1, retries, sleep_s)
            time.sleep(sleep_s)
    if not body:
        logger.info("reddit rss %s: no body (last status=%s)", sub, status)
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        logger.info("reddit rss %s: xml parse failed: %s", sub, e)
        return []
    atom_ns  = 'http://www.w3.org/2005/Atom'
    media_ns = 'http://search.yahoo.com/mrss/'
    out: list[dict] = []
    for i, entry in enumerate(root.iter(f'{{{atom_ns}}}entry')):
        title_el = entry.find(f'{{{atom_ns}}}title')
        link_el  = entry.find(f'{{{atom_ns}}}link')
        cat_el   = entry.find(f'{{{atom_ns}}}category')
        thumb_el = entry.find(f'{{{media_ns}}}thumbnail')
        title = (title_el.text or '').strip() if title_el is not None else ''
        if not title:
            continue
        title = _html.unescape(title)
        href = link_el.get('href', '') if link_el is not None else ''
        subreddit = ((cat_el.get('term') if cat_el is not None else '') or sub).strip()
        image = ''
        if thumb_el is not None:
            image = thumb_el.get('url', '') or ''
        out.append({
            'rank':      i + 1,
            'title':     title[:260],
            'url':       href,
            'subreddit': subreddit,
            'image':     image,
        })
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = (it.get('title') or '').lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def fetch() -> dict[str, Any]:
    """Pull r/popular + per-state subreddits. Non-fatal if a given
    state's sub is empty; the app falls back to national."""
    national = _dedupe(_fetch_sub('popular', limit=30))[:20]
    time.sleep(_PER_REQUEST_SLEEP_S)

    by_state: dict[str, list[dict]] = {}
    # If the top-level r/popular fetch was blocked (429 / 403), skip
    # the per-state loop entirely - Reddit will just keep 429-ing us
    # for the next several minutes and drag out the run for nothing.
    # An existing good snapshot on S3 will stay in place because we
    # bail out before write below when national is empty AND we have
    # no existing snapshot to preserve; when there IS existing data,
    # returning `{}` here lets write_snapshot overwrite with an empty
    # payload - which is fine because the app-side reader falls back
    # to the live fetch when the snapshot is empty.
    if not national:
        logger.warning("reddit: r/popular returned no entries; skipping state loop")
        return {
            'national': [],
            'error':    'r/popular returned no entries (likely IP-blocked by Reddit)',
        }

    for state, slug in _STATE_SUBREDDITS.items():
        try:
            # State fetches use retries=1 and a shorter timeout - they
            # supplement the geo view, so a slow subreddit shouldn't
            # blow the whole scraper budget. Missing state slices fall
            # back to national at read time.
            rows = _dedupe(_fetch_sub(slug, limit=15, retries=1, timeout=10))
        except Exception as e:
            logger.info("reddit rss %s (%s) failed: %s", state, slug, e)
            rows = []
        if rows:
            by_state[state] = rows[:12]
        time.sleep(_PER_REQUEST_SLEEP_S)

    result: dict[str, Any] = {'national': national}
    if by_state:
        result['by_state'] = by_state
    if not national:
        result['error'] = 'r/popular returned no entries (likely IP-blocked by Reddit)'
    return result


if __name__ == '__main__':
    from ._base import run_scraper
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('reddit', 'Reddit', 'social', fetch)
    print(
        f"reddit: national={len(result.get('national', []))} "
        f"by_state={len(result.get('by_state', {}))} "
        f"error={result.get('error')}",
        file=sys.stderr,
    )
