"""
Reddit trending scraper.

Primary path (2026-08 rewrite): Playwright renders
`www.reddit.com/r/<sub>/hot/` in real Chrome from the residential
runner. The rendered DOM includes `<shreddit-post>` custom elements
whose attributes (`score`, `comment-count`, `post-title`,
`permalink`, `author`) give us the engagement stats the RSS path
never carried. Reddit's public `.json` endpoints (and even Playwright
hits against `.json`) started returning 403 mid-2026 regardless of
TLS fingerprint. The shreddit HTML path is currently the only zero-
config way to pull scores + comment counts.

Fallback path (still runs when Playwright is unavailable or a specific
sub errs during render): the Atom `.rss` feed. RSS entries only carry
title / URL / thumbnail / subreddit - no engagement stats - so the
`score` and `comments` fields on those items will simply be absent
and the dashboard will render the row without them.

Render's datacenter IPs are blocked by Reddit's WAF; this scraper
runs from `local_residential_run.py` alongside TikTok / X /
Instagram.

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
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


# Reddit's RSS endpoint is quirky: plain `requests` (with a Firefox UA)
# returns 200s reliably, but the shared `http_get` helper via curl_cffi
# with Chrome TLS impersonation gets 429'd - the ja3 fingerprint plus
# the requests-from-datacenter pattern trips their WAF. Rolling our
# own small GET here bypasses curl_cffi for this scraper only.


# ---------------------------------------------------------------------------
# Playwright HTML path (primary): parse <shreddit-post> attributes
# ---------------------------------------------------------------------------
# Reddit renders each post twice in the modern shreddit DOM: once as
# the compact list card and once as a slot for the detail-view mount
# point (both share the same score / comment-count attrs). We dedupe
# by permalink so the same post doesn't appear twice in the output.
_SHREDDIT_POST_RE = re.compile(r'<shreddit-post([^>]{0,4000})>', re.IGNORECASE)


def _attr(attrs: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', attrs)
    return _html.unescape(m.group(1)) if m else ''


def _parse_shreddit_posts(html: str, sub: str, limit: int) -> list[dict]:
    """Extract posts from a rendered `www.reddit.com/r/<sub>/hot/` page.

    Every `<shreddit-post>` element carries these attributes we care
    about (documented via inspection 2026-08-04):
      - post-title, permalink, author, subreddit-prefixed-name
      - score (int, upvotes), comment-count (int)
      - post-type (image / video / link / self / gallery)
      - domain (external link host or self.<sub>)
    Poster thumbnails live on the inner `<img>` if any.
    """
    if not html:
        return []
    seen_permalinks: set[str] = set()
    out: list[dict] = []
    for m in _SHREDDIT_POST_RE.finditer(html):
        attrs = m.group(1)
        permalink = _attr(attrs, 'permalink')
        if not permalink or permalink in seen_permalinks:
            continue
        title = _attr(attrs, 'post-title').strip()
        if not title:
            continue
        seen_permalinks.add(permalink)
        try:
            score = int(_attr(attrs, 'score') or '0')
        except ValueError:
            score = 0
        try:
            comments = int(_attr(attrs, 'comment-count') or '0')
        except ValueError:
            comments = 0
        author = _attr(attrs, 'author')
        # subreddit-prefixed-name is `r/<sub>`; strip the prefix
        sub_name = _attr(attrs, 'subreddit-prefixed-name') or f'r/{sub}'
        sub_name = sub_name.replace('r/', '').strip() or sub
        # Absolute URL for the post
        if permalink.startswith('/'):
            url = 'https://www.reddit.com' + permalink
        else:
            url = permalink
        # Poster thumbnail: shreddit-post nests a media block; grab
        # the first non-emoji, non-avatar image within the element's
        # span in the source. The tag is self-closing in the SSR
        # markup (no </shreddit-post>), so pull a bounded window
        # after the tag for the img lookup.
        window = html[m.end():m.end() + 4000]
        img = ''
        img_m = re.search(r'<img[^>]+src="(https://[^"]+)"', window)
        if img_m:
            candidate = img_m.group(1)
            # Skip Reddit UI icons + user avatars (they live at
            # styles.redditmedia.com/*/avatars/ and .../icons/ paths).
            if ('redditstatic' not in candidate and
                    'styles.redditmedia.com/t2_' not in candidate and
                    '/avatars/' not in candidate):
                img = candidate
        out.append({
            'rank':      len(out) + 1,
            'title':     title[:260],
            'url':       url,
            'subreddit': sub_name,
            'image':     img,
            'score':     score,
            'comments':  comments,
            'author':    author,
        })
        if len(out) >= limit:
            break
    return out


def _fetch_sub_playwright(page, sub: str, limit: int = 20,
                            *, timeout_ms: int = 25000) -> list[dict]:
    """Load /r/<sub>/hot/ in a shared Playwright page and parse.

    `page` is a Playwright Page created by the caller so all subs
    reuse ONE browser context (35 renders would cost ~2 minutes if
    each launched its own browser; reusing keeps the whole scraper
    under 90s).
    """
    try:
        r = page.goto(f'https://www.reddit.com/r/{sub}/hot/',
                       wait_until='domcontentloaded', timeout=timeout_ms)
        status = r.status if r else -1
        if status != 200:
            logger.info("reddit shreddit r/%s: http %s", sub, status)
            return []
        try:
            page.wait_for_selector('shreddit-post', timeout=10_000)
        except Exception:
            logger.info("reddit shreddit r/%s: no shreddit-post after 10s", sub)
        # Nudge lazy loading so more than the first 5 posts hydrate.
        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(400)
        html = page.content() or ''
    except Exception as e:
        logger.info("reddit shreddit r/%s render err: %s", sub, e)
        return []
    return _parse_shreddit_posts(html, sub, limit)


def _mint_reddit_page(pw):
    """Launch one browser + one page. Returned tuple should be closed
    by the caller in a finally block. Returns (browser, page) or
    (None, None) on any failure."""
    try:
        from ._playwright import _launch_browser, _try_stealth, UA
    except Exception as e:
        logger.info("reddit: playwright helpers unavailable: %s", e)
        return None, None
    try:
        browser, _ch = _launch_browser(pw, prefer_chrome=True, proxy=None)
    except Exception as e:
        logger.warning("reddit: playwright launch failed: %s", e)
        return None, None
    try:
        ctx = browser.new_context(
            user_agent=UA,
            viewport={'width': 1440, 'height': 900},
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
        )
        page = ctx.new_page()
        _try_stealth(page)
        return browser, page
    except Exception as e:
        logger.warning("reddit: context/page create failed: %s", e)
        try: browser.close()
        except Exception: pass
        return None, None


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


def _fetch_sub_rss(sub: str, limit: int = 20, *, retries: int = 2,
                    timeout: int = 15) -> list[dict]:
    """Fallback path: pull up to `limit` entries from /r/<sub>/.rss.
    Returns [] on any failure so a single dead subreddit doesn't kill
    the run. RSS entries do NOT carry engagement stats (score /
    num_comments), so items produced here lack those fields and the
    dashboard row-meta strip renders without them.

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


# US-heavy subreddits used to build the national feed. r/popular is
# geo-inflected by the requester's IP (Hetzner is in Germany, so it
# was returning ~50% German r/de content), and r/all is content-neutral
# but includes a lot of niche international subs. This mix is
# intentionally news + culture + entertainment heavy so the "trending
# in the USA" framing on the dashboard is accurate.
_US_NATIONAL_SUBS = [
    'news', 'politics', 'movies', 'television', 'entertainment', 'Music',
    'gaming', 'technology', 'sports', 'nba', 'nfl', 'AskReddit',
    'mildlyinteresting', 'pics', 'videos', 'todayilearned', 'worldnews',
    'popculturechat', 'Fauxmoi', 'Marvel',
]


def _fetch_sub(sub: str, page, limit: int, *, allow_rss_fallback: bool = True) -> list[dict]:
    """Get posts for one subreddit. Uses the shared Playwright `page`
    when provided, falls back to RSS on error / empty. `page=None`
    forces the RSS path (used when Playwright never launched).
    """
    if page is not None:
        try:
            rows = _fetch_sub_playwright(page, sub, limit=limit)
            if rows:
                return rows
            logger.info("reddit r/%s: playwright empty, "
                         "falling back to RSS", sub)
        except Exception as e:
            logger.info("reddit r/%s playwright err %s, falling back to RSS",
                         sub, e)
    if not allow_rss_fallback:
        return []
    return _fetch_sub_rss(sub, limit=limit, retries=1, timeout=10)


def fetch() -> dict[str, Any]:
    """Pull hot posts from a curated set of US-heavy subreddits, merge
    them by score, then layer in per-state slices from state subs.
    r/popular is skipped because it geo-biases toward the caller's IP
    (Hetzner is in Germany, so r/popular was returning r/de-heavy
    noise instead of USA trending content).

    Primary source is Playwright rendering of `/r/<sub>/hot/`, which
    gives us score + comment-count per post. RSS is a per-sub
    fallback when Playwright errs or returns zero.
    """
    # Boot ONE browser context for the whole run. If Playwright is
    # unavailable (missing on the box, or launch fails), every sub
    # falls back to RSS and the scraper still produces titles/urls -
    # just without engagement stats. We swallow all playwright-init
    # errors so a broken chrome install never nukes the whole run.
    try:
        from ._playwright import _lazy_playwright
    except Exception as e:
        logger.info("reddit: cannot import playwright helper (%s), "
                     "falling back to RSS for all subs", e)
        _lazy_playwright = None  # type: ignore[assignment]

    sp = _lazy_playwright() if _lazy_playwright else None
    if sp is None:
        # RSS-only branch
        return _fetch_rss_only()

    aggregated: list[dict] = []
    by_state: dict[str, list[dict]] = {}
    with sp() as pw:
        browser, page = _mint_reddit_page(pw)
        if page is None:
            logger.info("reddit: playwright page mint failed, RSS-only")
            return _fetch_rss_only()
        try:
            for sub in _US_NATIONAL_SUBS:
                try:
                    rows = _fetch_sub(sub, page, limit=8)
                except Exception as e:
                    logger.info("reddit us-sub %s failed: %s", sub, e)
                    rows = []
                aggregated.extend(rows)
                # No inter-sub sleep needed - the browser + Reddit's
                # own rate limiter both throttle us naturally.
            for state, slug in _STATE_SUBREDDITS.items():
                try:
                    rows = _fetch_sub(slug, page, limit=15)
                except Exception as e:
                    logger.info("reddit rss %s (%s) failed: %s", state, slug, e)
                    rows = []
                if rows:
                    by_state[state] = _dedupe(rows)[:12]
        finally:
            try: browser.close()
            except Exception: pass

    national = _dedupe(aggregated)[:40]
    result: dict[str, Any] = {'national': national}
    if by_state:
        result['by_state'] = by_state
    if not national:
        result['error'] = ('Reddit shreddit + RSS both returned no entries. '
                            'Check residential-IP eligibility and cookies.')
    return result


def _fetch_rss_only() -> dict[str, Any]:
    """Pure-RSS branch retained for the case where Playwright can't
    boot. Items ship without score / comments (RSS doesn't carry them).
    """
    aggregated: list[dict] = []
    for sub in _US_NATIONAL_SUBS:
        try:
            rows = _fetch_sub_rss(sub, limit=8, retries=1, timeout=10)
        except Exception as e:
            logger.info("reddit us-sub %s failed: %s", sub, e)
            rows = []
        aggregated.extend(rows)
        time.sleep(_PER_REQUEST_SLEEP_S)

    national = _dedupe(aggregated)[:40]
    by_state: dict[str, list[dict]] = {}
    if not national:
        return {
            'national': [],
            'error':    'US-heavy subs returned no entries (likely IP-blocked by Reddit)',
        }

    for state, slug in _STATE_SUBREDDITS.items():
        try:
            rows = _dedupe(_fetch_sub_rss(slug, limit=15, retries=1, timeout=10))
        except Exception as e:
            logger.info("reddit rss %s (%s) failed: %s", state, slug, e)
            rows = []
        if rows:
            by_state[state] = rows[:12]
        time.sleep(_PER_REQUEST_SLEEP_S)

    result: dict[str, Any] = {'national': national}
    if by_state:
        result['by_state'] = by_state
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
