"""
X (Twitter) trending scraper - real trending posts from x.com (USA).

2026-07 switch: previously we scraped trends24.in for the topic list
("What's happening" - #hashtags + topic names). Jenna's ask ("show
trending content, not hashtags") means we now surface actual posts:
tweet text, author handle, engagement, and a permalink.

Approach:

  1. Load `x.com/explore/tabs/for_you` with Playwright + donated x.com
     cookies (the operator's real logged-in session). This tab shows a
     ranked list of currently-trending POSTS (not topics), curated for
     a US locale when the cookie jar has a US timezone / region.
  2. Extract each <article data-testid="tweet"> block: text, author,
     permalink, engagement counts.
  3. Fall back to `x.com/explore` (topic feed) and grab top posts from
     the "News" / "Sports" / "Entertainment" sub-tabs if the /for_you
     tab is empty.
  4. Diagnostic error if we get 0 items (usually means cookies expired
     or IP got flagged).

Snapshot shape (matches `_base.py` social contract):

    {
      "source":   "x",
      "label":    "X",
      "kind":     "social",
      "national": [
          {
              "rank":     1,
              "title":    "tweet text (first 260 chars)",
              "creator":  "@handle",
              "url":      "https://x.com/handle/status/1234567890",
              "image":    "https://pbs.twimg.com/media/..." (optional),
              "replies":  1234,
              "retweets": 4567,
              "likes":    12345,
              "views":    987654,
          }, ...
      ],
      ...
    }

Standalone:

    python3 -m scripts.trends_scrapers.x_twitter
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
from typing import Any

from ._base import run_scraper

logger = logging.getLogger(__name__)


# Tweet permalinks are /{user}/status/{id} with a 15-25 digit id.
_X_STATUS_HREF_RE = re.compile(
    r'href="(/([A-Za-z0-9_]{1,15})/status/(\d{15,25}))"',
    re.IGNORECASE,
)

# Twitter media urls
_X_MEDIA_IMG_RE = re.compile(
    r'src="(https://pbs\.twimg\.com/media/[^"]+)"',
    re.IGNORECASE,
)


def _parse_count(s: str) -> int:
    """'1.2K' -> 1200, '3.4M' -> 3400000, '947' -> 947."""
    if not s:
        return 0
    s = s.strip().replace(',', '')
    m = re.match(r'^([\d.]+)\s*([KMB])?$', s, re.IGNORECASE)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except (TypeError, ValueError):
        return 0
    suffix = (m.group(2) or '').upper()
    if suffix == 'K': return int(n * 1_000)
    if suffix == 'M': return int(n * 1_000_000)
    if suffix == 'B': return int(n * 1_000_000_000)
    return int(n)


def _extract_articles(html: str, limit: int = 30) -> list[dict]:
    """Split the rendered HTML on <article data-testid="tweet"> markers
    and pull tweet fields out of each block. Uses text-level regex
    because Twitter's DOM structure churns every quarter and BS4 selectors
    are brittle. Regex on stable attributes (data-testid, permalink
    shape, pbs.twimg.com hosts) survives longer.
    """
    if not html:
        return []
    # Split into per-tweet chunks. Twitter uses <article data-testid="tweet">
    # for every post in the timeline / for-you tab.
    chunks = re.split(r'<article[^>]+data-testid="tweet"', html)
    if len(chunks) < 2:
        return []

    items: list[dict] = []
    seen_ids: set[str] = set()
    for chunk in chunks[1:]:  # skip preamble
        # First status link in the chunk is the tweet's permalink.
        m = _X_STATUS_HREF_RE.search(chunk)
        if not m:
            continue
        path, handle, tweet_id = m.group(1), m.group(2), m.group(3)
        if tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)

        # Tweet text lives under <div data-testid="tweetText"> as a
        # nest of <span> tags. Extract the innerText by stripping tags.
        text = ''
        m_txt = re.search(
            r'data-testid="tweetText"[^>]*>(.*?)</div>',
            chunk,
            re.DOTALL,
        )
        if m_txt:
            raw = m_txt.group(1)
            # Strip nested tags, keep text.
            plain = re.sub(r'<[^>]+>', ' ', raw)
            text = _html.unescape(re.sub(r'\s+', ' ', plain)).strip()
        text = text[:260]

        # Media (first pbs.twimg.com image if present).
        image = ''
        m_img = _X_MEDIA_IMG_RE.search(chunk)
        if m_img:
            image = m_img.group(1)

        # Engagement counts. Twitter labels them with aria-label like
        # `aria-label="1234 replies"` on the button wrapper.
        def _grab(kind: str) -> int:
            pat = re.compile(
                r'aria-label="([\d.KMB,]+)\s+' + kind + r'\b',
                re.IGNORECASE,
            )
            mm = pat.search(chunk)
            return _parse_count(mm.group(1)) if mm else 0

        items.append({
            'rank':     len(items) + 1,
            'title':    text or '(no text)',
            'creator':  '@' + handle,
            'url':      f'https://x.com{path}',
            'image':    image,
            'replies':  _grab('repl(?:y|ies)'),
            'retweets': _grab('repost'),
            'likes':    _grab('lik(?:e|es)'),
            'views':    _grab('view'),
        })
        if len(items) >= limit:
            break
    return items


def _fetch_playwright() -> list[dict]:
    """Load x.com/explore/tabs/for_you with Playwright + donated cookies."""
    try:
        from ._playwright import (_lazy_playwright, _launch_browser,
                                  _try_stealth, UA)
        from ._base import (load_donated_cookies_playwright,
                            cookie_donation_status)
    except Exception as e:
        logger.info("x: playwright helpers unavailable: %s", e)
        return []
    sp = _lazy_playwright()
    if sp is None:
        logger.info("x: playwright not installed")
        return []

    domain = 'x.com'
    status = cookie_donation_status(domain)
    if not (status and status.get('donated')):
        logger.info("x: no donated %s cookies - /explore/tabs/for_you "
                    "requires login. Run donate_cookies.py x.com from "
                    "the operator's laptop.", domain)
        return []
    donated = load_donated_cookies_playwright(domain) or []
    if not donated:
        return []

    urls_to_try = [
        'https://x.com/explore/tabs/for_you',
        'https://x.com/explore/tabs/trending',
        'https://x.com/explore',
    ]

    html_out = ''
    with sp() as pw:
        try:
            browser, _ch = _launch_browser(pw, prefer_chrome=True, proxy=None)
        except Exception as e:
            logger.warning("x playwright launch failed: %s", e)
            return []
        try:
            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1440, 'height': 900},
                locale='en-US',
                timezone_id='America/New_York',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            try:
                ctx.add_cookies(donated)
                logger.info("x: injected %d x.com cookies (age=%.1fh)",
                            len(donated), status.get('age_hours') or -1)
            except Exception as e:
                logger.info("x: cookie inject failed: %s", e)

            page = ctx.new_page()
            _try_stealth(page)
            try:
                page.goto('https://x.com/home',
                          wait_until='domcontentloaded', timeout=30_000)
                page.wait_for_timeout(2500)
                for url in urls_to_try:
                    try:
                        page.goto(url, wait_until='domcontentloaded',
                                  timeout=45_000)
                    except Exception:
                        continue
                    try:
                        page.wait_for_selector(
                            'article[data-testid="tweet"]', timeout=15_000)
                    except Exception:
                        pass
                    for _ in range(5):
                        page.mouse.wheel(0, 1500)
                        page.wait_for_timeout(1000)
                    html_out = page.content() or ''
                    if 'data-testid="tweet"' in html_out:
                        logger.info("x: %s rendered tweets", url)
                        break
                    logger.info("x: %s did NOT render tweets", url)
            except Exception as e:
                logger.info("x navigation error: %s", e)
        finally:
            try: browser.close()
            except Exception: pass

    items = _extract_articles(html_out, limit=40)
    if not items:
        logger.info("x: extracted 0 items (html len=%d)", len(html_out))
    else:
        logger.info("x: extracted %d items", len(items))
    return items


def fetch() -> dict[str, Any]:
    items = _fetch_playwright()
    if not items:
        return {
            'national': [],
            'error':    ('x.com /explore returned no tweets. Check that '
                         'x.com cookies are donated and unexpired '
                         '(python3 scripts/trends_scrapers/donate_cookies.py '
                         'x.com) and that this ran from a residential IP.'),
        }
    return {'national': items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('x', 'X', 'social', fetch)
    print(f"x: national={len(result.get('national', []))} "
          f"error={result.get('error')}", file=sys.stderr)
