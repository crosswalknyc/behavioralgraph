"""
TikTok trending scraper via TikTok Creative Center.

TikTok publishes their weekly trending hashtags via the Creative Center
(ads-partner surface):

    https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en

Historically we scraped this three ways:

  1. The raw JSON API  /creative_radar_api/v1/popular_trend/hashtag/list
     -> now returns `code: 40101 no permission` because TikTok added
        JS-based request signing (X-Bogus / _signature). Not fixable
        without executing their JS.
  2. Inline `<script id="__NEXT_DATA__">` on the SSR HTML
     -> gone. The CC is now a client-side-rendered React SPA; the
        initial HTML is a ~20KB shell with no hashtag payload.
  3. Playwright DOM render (current path)
     -> load the page in real Chrome, scroll to trigger lazy-load, and
        read hashtag names + rank + post/view counts from the DOM.

Anonymous vs. logged-in yield
-----------------------------
As of 2026-07, TikTok gates the full trending list behind a Creative
Center login. Anonymous visitors see 3 preview cards and a "Log in or
sign up" wall. To unlock the full ~20-hashtag weekly list, run:

    python3 scripts/trends_scrapers/donate_cookies.py ads.tiktok.com

from Chrome after logging in to
https://ads.tiktok.com/business/creativecenter/ (any personal TikTok
account works). The scraper auto-loads the donated cookies on the next
Hetzner cron run.

Standalone:

    python3 -m scripts.trends_scrapers.tiktok
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from ._base import browser_headers, http_get, run_scraper

logger = logging.getLogger(__name__)


HASHTAG_API = ('https://ads.tiktok.com/creative_radar_api/v1/popular_trend/'
                'hashtag/list?period=7&page=1&limit=20&country_code=US')
CC_HTML     = 'https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en'
CC_REFERER  = CC_HTML

# Anonymous headers the CC used to accept before adding X-Bogus signing.
# Kept for the legacy _try_api() path in case TikTok relaxes signing again.
_ANON_HEADERS = {
    'anonymous-user-id': 'crosswalk-trends-iq',
    'timestamp':         '1700000000',
    'user-sign':         '00000000000000000000000000000000',
}


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
    re.DOTALL,
)


def _norm_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_shorthand_count(s: str) -> int:
    """Turn '1.2M', '340.5K', '9,876' into an int. Returns 0 on failure."""
    if not s:
        return 0
    txt = s.strip().replace(',', '').replace(' ', '')
    if not txt:
        return 0
    m = re.match(r'^([\d.]+)\s*([KkMmBb])?$', txt)
    if not m:
        return 0
    try:
        num = float(m.group(1))
    except ValueError:
        return 0
    suf = (m.group(2) or '').upper()
    mult = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(suf, 1)
    return int(num * mult)


def _parse_hashtags(data: dict) -> list[dict]:
    """The Creative Center API returns { code, data: { list: [ ... ] } }."""
    if not isinstance(data, dict):
        return []
    if data.get('code') not in (0, '0', None):
        logger.info("tiktok cc api returned non-zero code: %s (msg=%s)",
                     data.get('code'), data.get('msg'))
    lst = ((data.get('data') or {}).get('list')) or []
    out: list[dict] = []
    for i, row in enumerate(lst):
        name = row.get('hashtag_name') or row.get('hashtag') or ''
        if not name:
            continue
        posts = _norm_int(row.get('publish_cnt'))
        views = _norm_int(row.get('view_cnt') or row.get('video_view_cnt'))
        out.append({
            'rank':  i + 1,
            'topic': f'#{name}',
            'posts': posts,
            'views': views,
            'url':   f'https://www.tiktok.com/tag/{name}',
        })
    return out


def _walk_ssr_for_hashtags(node, out: list[dict], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        name = node.get('hashtag_name') or node.get('hashtag')
        posts = node.get('publish_cnt')
        views = node.get('view_cnt') or node.get('video_view_cnt')
        if name and (posts is not None or views is not None):
            out.append({
                'rank':  len(out) + 1,
                'topic': f'#{name}',
                'posts': _norm_int(posts),
                'views': _norm_int(views),
                'url':   f'https://www.tiktok.com/tag/{name}',
            })
            if len(out) >= limit:
                return
        for v in node.values():
            _walk_ssr_for_hashtags(v, out, limit)
    elif isinstance(node, list):
        for v in node:
            _walk_ssr_for_hashtags(v, out, limit)


def _try_api() -> list[dict]:
    """Legacy JSON API path. Returns 40101 no-permission as of mid-2026
    because TikTok added X-Bogus request signing, but we still probe it
    on the off chance they relax signing again."""
    extra = {
        'Accept':               'application/json, text/plain, */*',
        'Sec-Fetch-Dest':       'empty',
        'Sec-Fetch-Mode':       'cors',
        'Sec-Fetch-Site':       'same-origin',
        'X-Requested-With':     'XMLHttpRequest',
    }
    extra.update(_ANON_HEADERS)
    r = http_get(HASHTAG_API, headers=browser_headers(
        referer=CC_REFERER, extra=extra,
    ))
    if r is None or not r.ok:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    return _parse_hashtags(data)


def _try_html_ssr() -> list[dict]:
    """Legacy SSR HTML path. Empty as of mid-2026 because the CC is now
    a client-side-rendered SPA; kept as a cheap probe."""
    r = http_get(CC_HTML, headers=browser_headers(
        referer='https://www.google.com/'))
    if r is None or not r.ok:
        return []
    body = r.text or ''
    m = _NEXT_DATA_RE.search(body)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    _walk_ssr_for_hashtags(data, out, limit=20)
    return out


# ────────────────────────────────────────────────────────────────────────────
# Playwright DOM path (primary as of 2026-07)
# ────────────────────────────────────────────────────────────────────────────
# The CC's DOM class names are hashed (Emotion-style CSS) so we can't
# key off classes. Structure of a hydrated card:
#
#   <span ...>1</span>                       (rank)
#   <span class="...">#happy4thofjuly</span>  (hashtag)
#   <span class="...">News & Entertainment</span>  (category)
#   <div><span>82.5K</span><span>Posts</span></div>
#   <div><span>70.6M</span><span>Views</span></div>
#
# Only the first 3 cards render on load; the rest lazy-load as the user
# scrolls. We drive Playwright through a scroll loop until either we've
# harvested enough hashtags or we hit the DOM's `Show more` end. The
# hashtag-count-in-DOM check is our progress signal; if two consecutive
# scrolls fail to grow it we bail.

# Matches every "#hashtag" that appears as a text node (i.e. is preceded
# by a `>` and followed by a `<`). Unicode-friendly for non-ASCII tags.
_TIKTOK_TAG_TEXT_RE = re.compile(
    r'>(#[A-Za-z0-9_\u00c0-\uffff][A-Za-z0-9_\u00c0-\uffff]{1,79})<'
)


def _try_playwright(limit: int = 20) -> list[dict]:
    """Render the Creative Center page in real Chrome, scroll through
    the hashtag list until we've harvested enough cards, then extract
    everything from the final DOM.

    Returns [] if Playwright isn't installed or the page fails to
    hydrate (caller falls through to the legacy paths in that case).
    """
    sp = None
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        sp = sync_playwright
    except ImportError:
        logger.info("tiktok: playwright not installed; skipping DOM render")
        return []

    from ._playwright import UA, _launch_browser, _try_stealth
    from ._proxy import get_proxy_config, playwright_proxy
    from ._base import load_donated_cookies_playwright, cookie_donation_status

    proxy_dict = playwright_proxy(get_proxy_config()) or None

    # Look for donated Creative Center cookies. Without login the CC
    # anonymously exposes only 3 hashtags (the rest is gated behind
    # "Log in or sign up"). If Jenna has donated a logged-in CC session
    # via `donate_cookies.py ads.tiktok.com`, we inject those cookies
    # and get the full 20-hashtag weekly list instead.
    donated_cookies = load_donated_cookies_playwright('ads.tiktok.com')
    donation = cookie_donation_status('ads.tiktok.com')
    if donated_cookies:
        logger.info("tiktok: injecting %d donated cookies for ads.tiktok.com "
                     "(age=%.1fh)", len(donated_cookies),
                     donation.get('age_hours') or -1)
    else:
        logger.info("tiktok: no ads.tiktok.com cookie donation - the CC will "
                     "return only 3 preview hashtags. Run "
                     "`python3 scripts/trends_scrapers/donate_cookies.py "
                     "ads.tiktok.com` from a browser that's logged in to "
                     "https://ads.tiktok.com/business/creativecenter/ to "
                     "unlock the full list.")

    final_html = ''
    with sp() as pw:
        try:
            browser, _channel = _launch_browser(pw, prefer_chrome=True,
                                                 proxy=proxy_dict)
        except Exception as e:
            logger.warning("tiktok: playwright launch failed: %s", e)
            return []
        try:
            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1440, 'height': 900},
                locale='en-US',
                timezone_id='America/New_York',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            if donated_cookies:
                try:
                    ctx.add_cookies(donated_cookies)
                except Exception as e:
                    logger.info("tiktok: cookie inject failed: %s", e)
            page = ctx.new_page()
            _try_stealth(page)
            try:
                page.goto('https://ads.tiktok.com/', wait_until='domcontentloaded',
                           timeout=30000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            try:
                page.goto(CC_HTML, wait_until='domcontentloaded', timeout=45000)
            except Exception as e:
                logger.warning("tiktok: cc page nav failed: %s", e)
                return []

            # Wait until at least one "Posts" stat label appears (that's
            # our proxy for hydration having started).
            try:
                page.wait_for_selector('span:has-text("Posts")',
                                         timeout=15000, state='attached')
            except Exception:
                logger.info("tiktok: 'Posts' label never appeared - "
                             "page may be captcha-gated for this IP")

            # Progressive scroll: each pass jumps to the current bottom
            # of the document via window.scrollTo (mouse.wheel doesn't
            # reliably trigger the CC's IntersectionObserver-based
            # lazy-load). Stop when growth stalls or we hit the limit.
            last_count = 0
            stalled = 0
            for i in range(25):
                try:
                    html_now = page.content()
                except Exception:
                    break
                count = len(set(_TIKTOK_TAG_TEXT_RE.findall(html_now)))
                logger.debug("tiktok scroll pass %d: %d hashtags visible",
                              i, count)
                if count >= limit + 3:
                    final_html = html_now
                    break
                if count == last_count:
                    stalled += 1
                    if stalled >= 4:
                        final_html = html_now
                        break
                else:
                    stalled = 0
                    last_count = count
                try:
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    page.wait_for_timeout(1600)
                except Exception:
                    break
            logger.info("tiktok: harvested %d hashtags across %d scroll passes",
                         last_count, i + 1)
            if not final_html:
                try:
                    final_html = page.content()
                except Exception:
                    pass
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    return _parse_hydrated_dom(final_html, limit=limit)


# Each hydrated hashtag card is a leaf `<div>` (no anchor wrapper). We
# split the page body on `>#hashtag<` text nodes: everything between
# consecutive hashtag markers (plus a bit before the first one for the
# rank number) is that card's payload. Category label, "82.5K Posts",
# "70.6M Views" all live in that window.

_TIKTOK_STAT_RE = re.compile(
    r'>([\d.,]+\s*[KMB]?)</span>\s*<span[^>]*>\s*(Posts|Views|Publish|Play)',
    re.IGNORECASE,
)
_TIKTOK_RANK_RE = re.compile(r'>(\d{1,3})</span>')


def _parse_hydrated_dom(html: str, *, limit: int = 20) -> list[dict]:
    if not html:
        return []
    # Locate every "#tagname" text-node position in the DOM. That's the
    # anchor for each card. Slice `html` between consecutive matches to
    # get each card's payload.
    matches = list(_TIKTOK_TAG_TEXT_RE.finditer(html))
    if not matches:
        if len(html) > 5000:
            logger.info("tiktok: playwright DOM had %d bytes but 0 hashtag "
                         "text nodes; selectors may have drifted", len(html))
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for i, m in enumerate(matches):
        tag_text = m.group(1)          # includes leading '#'
        name = tag_text[1:]
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        # Card window = from this tag match to the next tag match, plus
        # a ~600-char rewind before this match to capture the rank
        # number that appears just above the tag name.
        rewind_start = max(0, m.start() - 800)
        window_end = matches[i + 1].start() if i + 1 < len(matches) else min(len(html), m.end() + 4000)
        window = html[rewind_start:window_end]

        posts = 0
        views = 0
        for sm in _TIKTOK_STAT_RE.finditer(window):
            val = _parse_shorthand_count(sm.group(1))
            label = sm.group(2).lower()
            if 'post' in label or 'publish' in label:
                posts = max(posts, val)
            elif 'view' in label or 'play' in label:
                views = max(views, val)

        rank = len(out) + 1
        rank_m = list(_TIKTOK_RANK_RE.finditer(window[:m.start() - rewind_start]))
        if rank_m:
            try:
                candidate = int(rank_m[-1].group(1))
                if 1 <= candidate <= 200:
                    rank = candidate
            except ValueError:
                pass

        out.append({
            'rank':  rank,
            'topic': tag_text,
            'posts': posts,
            'views': views,
            'url':   f'https://www.tiktok.com/tag/{name}',
        })
        if len(out) >= limit:
            break

    out.sort(key=lambda r: r['rank'])
    for i, r in enumerate(out, 1):
        r['rank'] = i
    return out


def fetch() -> dict[str, Any]:
    # Primary path (2026-07+): Playwright DOM render.
    hashtags = _try_playwright(limit=20)
    if hashtags:
        return {'national': hashtags}
    # Legacy probes - only useful if TikTok relaxes their locks. Both
    # empty as of 2026-07 but they cost <200ms each so keep them in
    # case the equation flips.
    hashtags = _try_api()
    if hashtags:
        return {'national': hashtags}
    hashtags = _try_html_ssr()
    return {'national': hashtags}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('tiktok', 'TikTok', 'social', fetch)
    print(f"tiktok: national={len(result.get('national', []))} "
           f"error={result.get('error')}", file=sys.stderr)
