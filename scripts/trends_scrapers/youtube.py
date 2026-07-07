"""
YouTube trending scraper via YouTube Data API v3.

Uses YouTube's official `videos?chart=mostPopular` endpoint. Free tier
allows 10K queries per day, plenty for a once-daily job.

Requires: environment variable `YOUTUBE_API_KEY` set to a Google Cloud
API key with the YouTube Data API v3 enabled. Add to
`/root/finished_codes/.env.trends_scrapers` on Hetzner (gitignored,
mode 600) - run_all.py will source it before spawning workers.

Standalone:

    YOUTUBE_API_KEY=... python3 -m scripts.trends_scrapers.youtube
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from ._base import browser_headers, http_get, run_scraper

logger = logging.getLogger(__name__)


API_ROOT = 'https://www.googleapis.com/youtube/v3/videos'


def _pull_page(api_key: str, page_token: str = '') -> dict:
    params = {
        'part':          'snippet,statistics,contentDetails',
        'chart':         'mostPopular',
        'regionCode':    'US',
        'maxResults':    '25',
        'key':           api_key,
    }
    if page_token:
        params['pageToken'] = page_token
    from urllib.parse import urlencode
    url = f'{API_ROOT}?{urlencode(params)}'
    r = http_get(url, headers=browser_headers())
    if r is None or not r.ok:
        logger.warning("youtube api page fetch failed (status=%s body=%s)",
                        getattr(r, 'status_code', None),
                        getattr(r, 'text', '')[:200] if r else '')
        return {}
    try:
        return r.json()
    except Exception as e:
        logger.warning("youtube api json decode failed: %s", e)
        return {}


def _parse_items(items: list) -> list[dict]:
    out: list[dict] = []
    for i, v in enumerate(items):
        vid = v.get('id') or ''
        sn  = v.get('snippet') or {}
        st  = v.get('statistics') or {}
        title = sn.get('title') or ''
        if not title:
            continue
        thumbs = (sn.get('thumbnails') or {})
        image = ''
        for pref in ('maxres', 'standard', 'high', 'medium', 'default'):
            t = thumbs.get(pref)
            if isinstance(t, dict) and t.get('url'):
                image = t['url']
                break
        out.append({
            'rank':      i + 1,
            'title':     title[:200],
            'channel':   sn.get('channelTitle') or '',
            'url':       f'https://www.youtube.com/watch?v={vid}',
            'image':     image,
            'views':     int(st.get('viewCount') or 0),
            'likes':     int(st.get('likeCount') or 0),
            'comments':  int(st.get('commentCount') or 0),
            'published': sn.get('publishedAt') or '',
        })
    return out


def fetch() -> dict[str, Any]:
    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not api_key:
        return {
            'national': [],
            'error':    'YOUTUBE_API_KEY not set; add to '
                        '/root/finished_codes/.env.trends_scrapers on Hetzner',
        }
    page = _pull_page(api_key)
    items = page.get('items') or []
    national = _parse_items(items)[:20]
    return {'national': national}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('youtube', 'YouTube', 'social', fetch)
    print(f"youtube: national={len(result.get('national', []))} "
           f"error={result.get('error')}", file=sys.stderr)
