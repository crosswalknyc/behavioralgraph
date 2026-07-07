"""
TikTok trending scraper via TikTok Creative Center.

The main tiktok.com/foryou and /discover feeds require an auth session
plus captcha-solving, but TikTok publishes the same trending inventory
without auth via their Creative Center (ads-partner surface):

    https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en

    JSON API:
    https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list
        ?period=7&page=1&limit=20&country_code=US

That endpoint returns a JSON array of trending hashtags with post counts,
view counts, and a top video URL per hashtag. No token required, but the
site does check for a browser-style Accept-Language + Referer.

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

# Anonymous timestamp param the CC uses to gate its "trending hashtag" API
# ('anonymous_user_id' or 'device_id') - not always required but including
# a stable one seems to bypass the 40101 no-permission error more often.
_ANON_HEADERS = {
    'anonymous-user-id': 'crosswalk-trends-iq',
    'timestamp':         '1700000000',
    'user-sign':         '00000000000000000000000000000000',
}


# Fallback: TikTok's Creative Center SSR page ships an inline `<script
# id="__NEXT_DATA__">` blob that carries an initial list of trending
# hashtags identical to the API response. If the JSON API refuses us we
# scrape the HTML embed instead.
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
    re.DOTALL,
)


def _norm_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


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
        logger.info("tiktok API fetch failed (status=%s), falling back to HTML",
                     getattr(r, 'status_code', None))
        return []
    try:
        data = r.json()
    except Exception as e:
        logger.info("tiktok API json decode failed: %s", e)
        return []
    hashtags = _parse_hashtags(data)
    if not hashtags:
        code = data.get('code') if isinstance(data, dict) else None
        logger.info("tiktok API returned no hashtags (code=%s)", code)
    return hashtags


def _try_html_ssr() -> list[dict]:
    r = http_get(CC_HTML, headers=browser_headers(
        referer='https://www.google.com/'))
    if r is None or not r.ok:
        logger.warning("tiktok html fallback failed (status=%s)",
                        getattr(r, 'status_code', None))
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


def fetch() -> dict[str, Any]:
    hashtags = _try_api()
    if not hashtags:
        hashtags = _try_html_ssr()
    return {'national': hashtags}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('tiktok', 'TikTok', 'social', fetch)
    print(f"tiktok: national={len(result.get('national', []))} "
           f"error={result.get('error')}", file=sys.stderr)
