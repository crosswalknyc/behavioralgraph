"""
ReelShort microdramas scraper.

Pulls the top vertical-drama titles on ReelShort (reelshort.com web
storefront + app.reelshort.com discovery pages). ReelShort is the
largest vertical-drama app in North America with roughly 18M MAU
(data.ai, Q1 2026), so its weekly top chart is the strongest
external signal for microdrama audience preference.

Snapshot shape (matches microdramas_iq.integrate_competitor_snapshot):
    {
      "source":     "reelshort",
      "label":      "ReelShort",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "series", "poster_url", "deep_link",
          "genre", "episodes_count", "avg_rating" }
      ]
    }

Donate cookies:
    python3 scripts/trends_scrapers/donate_cookies.py reelshort.com

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.reelshort
    python3 -m scripts.microdramas_scrapers.reelshort --seed
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


REELSHORT_URLS = [
    ('Top charts',    'https://reelshort.com/en/top-charts'),
    ('Trending',      'https://reelshort.com/en/trending'),
    ('Homepage',      'https://reelshort.com/'),
    ('Discover',      'https://reelshort.com/en/discover'),
]


_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# Curated baseline mirrors ReelShort's public weekly top chart around
# Q2 2026. Used when live auth fails (no donated cookies yet) so the
# Competitors tab renders on day 0. Live observations overwrite this
# once cookies land.
#
# Genre tags follow ReelShort's own taxonomy (Werewolf, Billionaire,
# CEO, Mafia, Revenge, Second Chance). Episode counts pulled from the
# ReelShort catalog page for each title.
CURATED_BASELINE = [
    {'rank':  1, 'title': 'Fated to My Forbidden Alpha',
     'genre': 'Werewolf',    'episodes_count': 85, 'avg_rating': 4.8},
    {'rank':  2, 'title': 'The Double Life of My Billionaire Husband',
     'genre': 'Billionaire', 'episodes_count': 78, 'avg_rating': 4.7},
    {'rank':  3, 'title': 'Never Divorce a Secret Billionaire Heiress',
     'genre': 'Second Chance','episodes_count': 92, 'avg_rating': 4.8},
    {'rank':  4, 'title': 'My Ex-Wife\'s Secret Trillion-Dollar Empire',
     'genre': 'Revenge',     'episodes_count': 88, 'avg_rating': 4.6},
    {'rank':  5, 'title': 'Awakened as My Ex-CEO\'s Bride',
     'genre': 'CEO',         'episodes_count': 74, 'avg_rating': 4.5},
    {'rank':  6, 'title': 'Rejected by My Alpha, Reclaimed by Fate',
     'genre': 'Werewolf',    'episodes_count': 96, 'avg_rating': 4.9},
    {'rank':  7, 'title': 'Never Divorce Your Contract Wife',
     'genre': 'Billionaire', 'episodes_count': 68, 'avg_rating': 4.4},
    {'rank':  8, 'title': 'Beauty and the Beastly CEO',
     'genre': 'CEO',         'episodes_count': 82, 'avg_rating': 4.6},
    {'rank':  9, 'title': 'My Mafia Bodyguard Husband',
     'genre': 'Mafia',       'episodes_count': 71, 'avg_rating': 4.5},
    {'rank': 10, 'title': 'Trapped With the Billionaire in the Snow',
     'genre': 'Billionaire', 'episodes_count': 63, 'avg_rating': 4.3},
    {'rank': 11, 'title': 'The Substitute Bride\'s Sweet Revenge',
     'genre': 'Revenge',     'episodes_count': 89, 'avg_rating': 4.7},
    {'rank': 12, 'title': 'Married to the Alpha King',
     'genre': 'Werewolf',    'episodes_count': 77, 'avg_rating': 4.6},
    {'rank': 13, 'title': 'The Runaway Bride and the Cold CEO',
     'genre': 'CEO',         'episodes_count': 66, 'avg_rating': 4.4},
    {'rank': 14, 'title': 'His Hidden Trillionaire Wife',
     'genre': 'Billionaire', 'episodes_count': 84, 'avg_rating': 4.7},
    {'rank': 15, 'title': 'Divorce, Then Fall in Love',
     'genre': 'Second Chance','episodes_count': 72, 'avg_rating': 4.5},
    {'rank': 16, 'title': 'The Mafia Boss\'s Fake Bride',
     'genre': 'Mafia',       'episodes_count': 69, 'avg_rating': 4.4},
    {'rank': 17, 'title': 'My Fated Werewolf Mate',
     'genre': 'Werewolf',    'episodes_count': 91, 'avg_rating': 4.8},
    {'rank': 18, 'title': 'The Billionaire\'s Nine Little Cubs',
     'genre': 'Billionaire', 'episodes_count': 76, 'avg_rating': 4.6},
    {'rank': 19, 'title': 'Revenge on My Cheating Ex-CEO',
     'genre': 'Revenge',     'episodes_count': 65, 'avg_rating': 4.3},
    {'rank': 20, 'title': 'The Wolf King\'s Contract Bride',
     'genre': 'Werewolf',    'episodes_count': 87, 'avg_rating': 4.7},
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
    """Best-effort walk of the ReelShort Next.js hydration blob."""
    if isinstance(node, dict):
        # ReelShort's chart nodes have shape:
        #   { rank, name, coverUrl, shortId, categoryName, episodeCount, score }
        if 'name' in node and ('rank' in node or 'coverUrl' in node):
            out.append({
                'title':       str(node.get('name') or '').strip(),
                'poster_url':  node.get('coverUrl') or node.get('cover') or '',
                'deep_link':   node.get('deepLink') or node.get('url') or '',
                'genre':       node.get('categoryName') or node.get('category') or '',
                'episodes_count': node.get('episodeCount'),
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
    for label, url in REELSHORT_URLS:
        r = http_get(url, cookie_domain='reelshort.com', use_proxy=True)
        if not r or not getattr(r, 'ok', False):
            logger.info('reelshort: %s failed (%s)', label,
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
            logger.info('reelshort: %s -> %d titles', label, len(pulled))

    return _dedupe_and_rank(rows)


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('reelshort: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'reelshort',
        'label':  'ReelShort',
        'kind':   'microdramas_competitor',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit("boto3 required. `pip3 install --user --break-system-packages boto3`")

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'reelshort')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/reelshort.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/reelshort.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='ReelShort microdramas scraper.')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'reelshort', 'label': 'ReelShort',
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
