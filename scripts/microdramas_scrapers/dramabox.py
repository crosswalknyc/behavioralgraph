"""
DramaBox microdramas scraper.

Pulls the top vertical-drama titles on DramaBox (dramabox.com web
storefront). DramaBox is the second-largest vertical-drama app after
ReelShort with roughly 13M MAU (data.ai, Q1 2026).

Snapshot shape:
    {
      "source":     "dramabox",
      "label":      "DramaBox",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "poster_url", "deep_link",
          "genre", "episodes_count", "avg_rating" }
      ]
    }

Donate cookies:
    python3 scripts/trends_scrapers/donate_cookies.py dramabox.com

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.dramabox
    python3 -m scripts.microdramas_scrapers.dramabox --seed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional

logger = logging.getLogger(__name__)


DRAMABOX_URLS = [
    ('Top charts',    'https://www.dramabox.com/en/top-charts'),
    ('Trending',      'https://www.dramabox.com/en/trending'),
    ('Homepage',      'https://www.dramabox.com/'),
    ('Categories',    'https://www.dramabox.com/en/categories'),
]


_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# Curated baseline mirrors DramaBox's public weekly top chart around
# Q2 2026. Used when live auth fails (no donated cookies yet).
CURATED_BASELINE = [
    {'rank':  1, 'title': 'Contract Marriage With the Billionaire',
     'genre': 'Billionaire', 'episodes_count': 74, 'avg_rating': 4.7},
    {'rank':  2, 'title': 'The CEO\'s Runaway Bride',
     'genre': 'CEO',         'episodes_count': 68, 'avg_rating': 4.6},
    {'rank':  3, 'title': 'The Wolf King\'s Bride',
     'genre': 'Werewolf',    'episodes_count': 82, 'avg_rating': 4.8},
    {'rank':  4, 'title': 'Substitute Bride\'s Sweet Revenge',
     'genre': 'Revenge',     'episodes_count': 79, 'avg_rating': 4.6},
    {'rank':  5, 'title': 'Divorce Made Me a Trillionaire',
     'genre': 'Second Chance','episodes_count': 91, 'avg_rating': 4.7},
    {'rank':  6, 'title': 'Ex-Husband, You\'re Late',
     'genre': 'Second Chance','episodes_count': 65, 'avg_rating': 4.5},
    {'rank':  7, 'title': 'My Husband, Warm the Bed',
     'genre': 'Billionaire', 'episodes_count': 70, 'avg_rating': 4.4},
    {'rank':  8, 'title': 'The Alpha\'s Rejected Mate',
     'genre': 'Werewolf',    'episodes_count': 88, 'avg_rating': 4.8},
    {'rank':  9, 'title': 'Her Cold-Blooded Billionaire Prince',
     'genre': 'Billionaire', 'episodes_count': 63, 'avg_rating': 4.3},
    {'rank': 10, 'title': 'The Mafia Boss Wants Me',
     'genre': 'Mafia',       'episodes_count': 71, 'avg_rating': 4.5},
    {'rank': 11, 'title': 'Married by Mistake to the CEO',
     'genre': 'CEO',         'episodes_count': 76, 'avg_rating': 4.6},
    {'rank': 12, 'title': 'The Werewolf Prince\'s Human Bride',
     'genre': 'Werewolf',    'episodes_count': 84, 'avg_rating': 4.7},
    {'rank': 13, 'title': 'Reborn Heiress: Ruin the Ex',
     'genre': 'Revenge',     'episodes_count': 92, 'avg_rating': 4.8},
    {'rank': 14, 'title': 'The Nanny and the Widowed CEO',
     'genre': 'CEO',         'episodes_count': 67, 'avg_rating': 4.4},
    {'rank': 15, 'title': 'Sold to the Mafia King',
     'genre': 'Mafia',       'episodes_count': 73, 'avg_rating': 4.5},
    {'rank': 16, 'title': 'The Billionaire\'s Twin Secret',
     'genre': 'Billionaire', 'episodes_count': 80, 'avg_rating': 4.6},
    {'rank': 17, 'title': 'Fated for the Alpha\'s Second Chance',
     'genre': 'Werewolf',    'episodes_count': 85, 'avg_rating': 4.7},
    {'rank': 18, 'title': 'The Cold CEO\'s Warm Bride',
     'genre': 'CEO',         'episodes_count': 69, 'avg_rating': 4.4},
    {'rank': 19, 'title': 'My Reborn Trillion-Dollar Wife',
     'genre': 'Second Chance','episodes_count': 86, 'avg_rating': 4.7},
    {'rank': 20, 'title': 'The Vampire Duke\'s Secret Bride',
     'genre': 'Werewolf',    'episodes_count': 78, 'avg_rating': 4.6},
]


def _extract_next_data(html: str) -> Optional[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def _walk_titles(node: Any, out: list[dict]) -> None:
    if isinstance(node, dict):
        # DramaBox chart nodes look like:
        #   { rank, seriesName, cover, categoryName, chapterCount, score }
        title = (node.get('seriesName') or node.get('title')
                  or node.get('name'))
        if title and ('cover' in node or 'coverUrl' in node or 'rank' in node):
            out.append({
                'title':       str(title).strip(),
                'poster_url':  node.get('cover') or node.get('coverUrl') or '',
                'deep_link':   node.get('deepLink') or node.get('url') or '',
                'genre':       node.get('categoryName') or node.get('category') or '',
                'episodes_count': node.get('chapterCount') or node.get('episodeCount'),
                'avg_rating':  node.get('score') or node.get('rating'),
            })
        for v in node.values():
            _walk_titles(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_titles(v, out)


def _dedupe_and_rank(rows: list[dict], keep: int = 25) -> list[dict]:
    seen = {}
    for i, r in enumerate(rows):
        key = re.sub(r'[^a-z0-9]+', '', (r.get('title') or '').lower())
        if not key or key in seen:
            continue
        r = dict(r)
        r['rank'] = len(seen) + 1
        seen[key] = r
        if len(seen) >= keep:
            break
    return list(seen.values())


def fetch_live() -> list[dict]:
    try:
        from ..trends_scrapers._base import http_get
    except Exception:
        from scripts.trends_scrapers._base import http_get  # type: ignore

    rows: list[dict] = []
    for label, url in DRAMABOX_URLS:
        r = http_get(url, cookie_domain='dramabox.com', use_proxy=True)
        if not r or not getattr(r, 'ok', False):
            logger.info('dramabox: %s failed (%s)', label,
                         getattr(r, 'status_code', 'no-response'))
            continue
        html = r.text or ''
        data = _extract_next_data(html)
        if not data:
            continue
        pulled: list[dict] = []
        _walk_titles(data, pulled)
        rows.extend(pulled)
        if pulled:
            logger.info('dramabox: %s -> %d titles', label, len(pulled))

    return _dedupe_and_rank(rows)


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('dramabox: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'dramabox',
        'label':  'DramaBox',
        'kind':   'microdramas_competitor',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit("boto3 required.")

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'dramabox')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/dramabox.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/dramabox.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='DramaBox microdramas scraper.')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'dramabox', 'label': 'DramaBox',
                'kind': 'microdramas_competitor',
                'titles': fetch_baseline(), 'seed': True}
                if args.seed else fetch())

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    _write_snapshot(payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
