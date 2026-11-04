"""
YouTube top videos (US) scraper via YouTube Data API v3.

Uses YouTube's official `videos?chart=mostPopular&regionCode=US`
endpoint, paged to the chart's full depth (up to 200 rows, four pages
of 50). Free tier allows 10K queries per day, plenty for a once-daily
job.

The chart is YouTube's own: it owns the order of the rows and each
row's view count is the figure YouTube publishes for that video. The
Rankers tab renders both as published (Jenna 2026-11-03: "use the
stamped us stream number from youtube don't model"). Nothing here is
reasoned or scaled; a row with no published count stays blank.

Requires: environment variable `YOUTUBE_API_KEY` set to a Google Cloud
API key with the YouTube Data API v3 enabled. Lives in
`/root/finished_codes/.env.trends_scrapers` on the build box
(gitignored, mode 600) - run_all.py sources it before spawning workers.

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

# Snapshot slug + panel label. The slug is distinct from the dormant
# `youtube` social snapshot so the two never overwrite each other.
SOURCE = 'youtube_top_videos'
LABEL = 'YouTube Top Videos (US)'

# Rows kept on the rail. The API serves the chart 50 at a time and
# stops issuing `nextPageToken` once the chart is exhausted.
MAX_ITEMS = 100
_PAGE_SIZE = 50
_MAX_PAGES = 4

# YouTube's own per-category Most Popular charts (Jenna 2026-11-03:
# "we need a genre or type of content sorter for youtube"). The same
# endpoint takes `videoCategoryId`; each of these answered with a real
# US chart when measured on 2026-11-03 (50 to 200 rows). The ones it
# did not are left out on purpose: Travel & Events (19) and Education
# (27) answer 404, Nonprofits (29) carries two rows, and the film-genre
# ids (30-44, Horror / Drama / Trailers / Shorts) are legacy labels
# YouTube does not chart. Order here is the pill order on the tab.
# Labels are YouTube's own category names.
CATEGORY_CHARTS: list[tuple[str, str, str]] = [
    # (pill key, YouTube categoryId, YouTube's category name)
    ('music',         '10', 'Music'),
    ('gaming',        '20', 'Gaming'),
    ('entertainment', '24', 'Entertainment'),
    ('film',          '1',  'Film & Animation'),
    ('sports',        '17', 'Sports'),
    ('news',          '25', 'News & Politics'),
    ('comedy',        '23', 'Comedy'),
    ('people',        '22', 'People & Blogs'),
    ('howto',         '26', 'Howto & Style'),
    ('science',       '28', 'Science & Technology'),
    ('pets',          '15', 'Pets & Animals'),
    ('autos',         '2',  'Autos & Vehicles'),
]
# Rows kept per category chart: one page, YouTube's order.
CATEGORY_DEPTH = 50


def _pull_page(api_key: str, page_token: str = '',
               category_id: str = '') -> dict:
    params = {
        'part':          'snippet,statistics,contentDetails',
        'chart':         'mostPopular',
        'regionCode':    'US',
        'maxResults':    str(_PAGE_SIZE),
        'key':           api_key,
    }
    if category_id:
        params['videoCategoryId'] = category_id
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


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_items(items: list, start_rank: int = 1) -> list[dict]:
    out: list[dict] = []
    rank = start_rank
    for v in items:
        vid = v.get('id') or ''
        sn  = v.get('snippet') or {}
        st  = v.get('statistics') or {}
        title = sn.get('title') or ''
        if not title or not vid:
            continue
        thumbs = (sn.get('thumbnails') or {})
        image = ''
        for pref in ('maxres', 'standard', 'high', 'medium', 'default'):
            t = thumbs.get(pref)
            if isinstance(t, dict) and t.get('url'):
                image = t['url']
                break
        out.append({
            'rank':        rank,
            'video_id':    vid,
            'title':       title[:200],
            'channel':     sn.get('channelTitle') or '',
            'url':         f'https://www.youtube.com/watch?v={vid}',
            'image':       image,
            # YouTube's own published counts for the video, as served.
            'views':       _int(st.get('viewCount')),
            'likes':       _int(st.get('likeCount')),
            'comments':    _int(st.get('commentCount')),
            'published':   sn.get('publishedAt') or '',
            'category_id': str(sn.get('categoryId') or ''),
        })
        rank += 1
    return out


def fetch() -> dict[str, Any]:
    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not api_key:
        return {
            'national': [],
            'error':    'YOUTUBE_API_KEY not set; add to '
                        '/root/finished_codes/.env.trends_scrapers on the '
                        'build box',
        }
    national = _pull_chart(api_key, '', MAX_ITEMS)
    out: dict[str, Any] = {
        'national':  national,
        'label':     'YouTube',
        'region':    'US',
        'chart':     'mostPopular',
        'metric':    'views',
    }
    if not national:
        out['error'] = 'youtube mostPopular chart returned no rows'
    # The per-category charts ride on the same snapshot. A category
    # whose chart did not answer is simply absent from the list; the
    # tab shows the pills it has rather than an empty one.
    charts: list[dict] = []
    for key, cat_id, label in CATEGORY_CHARTS:
        rows = _pull_chart(api_key, cat_id, CATEGORY_DEPTH)
        if not rows:
            logger.warning("youtube category chart %s (%s) returned no rows",
                           label, cat_id)
            continue
        charts.append({'key': key, 'category_id': cat_id, 'label': label,
                       'items': rows})
    out['category_charts'] = charts
    return out


def _pull_chart(api_key: str, category_id: str, depth: int) -> list[dict]:
    """Page one Most Popular chart (overall when `category_id` is
    empty) to `depth` rows, in YouTube's order."""
    rows: list[dict] = []
    seen: set[str] = set()
    token = ''
    for _ in range(_MAX_PAGES):
        page = _pull_page(api_key, token, category_id)
        items = page.get('items') or []
        if not items:
            break
        fresh = [v for v in items
                 if (v.get('id') or '') and v.get('id') not in seen]
        for v in fresh:
            seen.add(v['id'])
        rows.extend(_parse_items(fresh, start_rank=len(rows) + 1))
        if len(rows) >= depth:
            break
        token = page.get('nextPageToken') or ''
        if not token:
            break
    return rows[:depth]


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'youtube', fetch)
    print(f"{SOURCE}: national={len(result.get('national', []))} "
           f"error={result.get('error')}", file=sys.stderr)
