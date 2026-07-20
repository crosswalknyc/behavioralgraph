"""
Instagram trending-hashtags scraper.

Instagram's Explore feed is only accessible via authenticated mobile-API
calls which get `login_required` when instagrapi hits `i.instagram.com`
from any datacenter IP - IG's WAF flags the Chrome-issued sessionid
against a mobile-context request. Realistically getting reliable
"trending posts on Instagram" data without Meta's paid Graph API isn't
possible today.

Trending-hashtag data IS scrapable from public trackers that maintain
their own daily-refreshed lists. We pull the top-100 IG hashtags by
post volume from `top-hashtags.com/instagram/` (clean HTML table, no
auth, no CAPTCHA), stamp each with a post count, and write to the
`instagram` source key so the dashboard's existing "Instagram" tab
lights up without needing any frontend rework.

Snapshot shape matches the "social" contract in `_base.py`:

    {
      "source":    "instagram",
      "label":     "Instagram",
      "kind":      "social",
      "national":  [ { rank, title:"#foo", url, posts_display, ... }, ... ],
      ...
    }

`title` = `#foo` (the frontend renders it as the clickable label).
`url`   = `https://www.instagram.com/explore/tags/foo/`
`posts_display` = `"2.1B"` etc. (the value we scraped)
`posts` = integer post count when we can parse it (else absent)

Once we've accumulated 7+ days of snapshots, a follow-up job can
compute WoW momentum (rank/volume delta) and re-order this list by
"trending" instead of "always big". Until then we're honest: the tab
subtitle just calls them "popular" hashtags.

Standalone:

    python3 -m scripts.trends_scrapers.instagram_hashtags
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
from typing import Any, Optional

from ._base import http_get, browser_headers

logger = logging.getLogger(__name__)


_SRC_URL = 'https://top-hashtags.com/instagram/'

# Post-count multipliers used by top-hashtags.com (also common on
# every hashtag-tracker site). Case-insensitive suffix match after we
# strip whitespace.
_SUFFIX_MULT = {
    'B': 1_000_000_000,
    'M': 1_000_000,
    'K': 1_000,
}


def _parse_posts(display: str) -> Optional[int]:
    """Turn `"2.100B"` / `"1.7M"` / `"850K"` into an integer count."""
    if not display:
        return None
    s = display.strip().upper().replace(',', '')
    m = re.match(r'^([0-9]+(?:\.[0-9]+)?)\s*([BMK])?$', s)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2) or ''
    mult = _SUFFIX_MULT.get(suffix, 1)
    return int(round(num * mult))


# Row layout on top-hashtags.com/instagram/ (as of 2026-07):
#
#   <tr>
#     <td>1</td>
#     <td><a href="/hashtag/love/">#love</a></td>
#     <td class="text-end">2.100B</td>
#   </tr>
#
# We match the `#tag` inside the anchor and the trailing count cell in
# one shot. The site's HTML is regenerated daily from their DB dump,
# so the structure has been stable for years.
_ROW_RE = re.compile(
    r'<a\s+href="/hashtag/([^"/]+)/"[^>]*>#([A-Za-z0-9_]+)</a>'
    r'.*?<td[^>]*class="[^"]*text-end[^"]*"[^>]*>\s*([^<]+?)\s*</td>',
    flags=re.IGNORECASE | re.DOTALL,
)


def _fetch_html() -> str:
    """top-hashtags.com is fronted by Cloudflare which 403s any plain
    `requests` call on the JA3 fingerprint alone. `http_get` in
    `_base.py` transparently uses curl_cffi's Chrome impersonation
    when available, which passes the fingerprint check and returns a
    normal 200 with the hashtag table."""
    r = http_get(_SRC_URL,
                 headers=browser_headers(referer='https://www.google.com/'),
                 impersonate='chrome124')
    if r is None:
        raise RuntimeError('top-hashtags.com: request exhausted retries')
    if not getattr(r, 'ok', False):
        raise RuntimeError(f'top-hashtags.com returned http '
                            f'{getattr(r, "status_code", "??")}')
    return r.text or ''


def _parse_rows(html: str, *, limit: int = 100) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for i, m in enumerate(_ROW_RE.finditer(html)):
        slug     = _html.unescape(m.group(1))
        tag      = _html.unescape(m.group(2))
        posts_d  = _html.unescape(m.group(3))
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        row = {
            'rank':          len(out) + 1,
            'title':         f'#{tag}',
            'hashtag':       tag,
            'url':           f'https://www.instagram.com/explore/tags/{slug}/',
            'posts_display': posts_d,
        }
        posts_int = _parse_posts(posts_d)
        if posts_int is not None:
            row['posts'] = posts_int
        out.append(row)
        if len(out) >= limit:
            break
    return out


def fetch() -> dict[str, Any]:
    """Pull the top 100 Instagram hashtags by post volume."""
    try:
        html = _fetch_html()
    except Exception as e:
        return {
            'national':  [],
            'available': False,
            'error':     f'{type(e).__name__}: {e}',
        }
    rows = _parse_rows(html, limit=100)
    if not rows:
        return {
            'national':  [],
            'available': False,
            'error':     'top-hashtags.com HTML parse yielded 0 rows',
        }
    return {
        'national':  rows,
        'available': True,
    }


if __name__ == '__main__':
    from ._base import run_scraper
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('instagram', 'Instagram', 'social', fetch)
    print(
        f"instagram (hashtags): national={len(result.get('national', []))} "
        f"error={result.get('error')}",
        file=sys.stderr,
    )
