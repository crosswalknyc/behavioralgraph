"""
X (Twitter) trending scraper via trends24.in.

trends24.in is a public aggregator that mirrors X's trending topics
worldwide, per-country, and per-US-state. It scrapes X's own trending
endpoint under the hood; publishing our snapshot from their board keeps
us out of the "X changed auth again this month" tarpit and gives
per-state breakouts for free.

Source URLs:

    https://trends24.in/united-states/                      # National
    https://trends24.in/united-states/{state-slug}/          # State
    (no per-DMA endpoint - state is the finest bucket)

Standalone:

    python3 -m scripts.trends_scrapers.x_twitter
"""

from __future__ import annotations

import logging
import re
import sys
import urllib.parse
from typing import Any
from html import unescape

from ._base import browser_headers, http_get, run_scraper

logger = logging.getLogger(__name__)


# trends24 uses lower-cased hyphenated slugs
STATE_SLUGS = [
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
    'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
    'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
    'new-hampshire', 'new-jersey', 'new-mexico', 'new-york',
    'north-carolina', 'north-dakota', 'ohio', 'oklahoma', 'oregon',
    'pennsylvania', 'rhode-island', 'south-carolina', 'south-dakota',
    'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
    'west-virginia', 'wisconsin', 'wyoming', 'district-of-columbia',
]

_TREND_LI_RE = re.compile(
    r'<li[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>'
    r'(?:\s*<span[^>]*class="[^"]*tweet-count[^"]*"[^>]*>([^<]+)</span>)?',
    re.IGNORECASE,
)


def _slug_to_state(slug: str) -> str:
    return slug.replace('-', ' ').title()


def _parse_trends24(html: str, limit: int = 25) -> list[dict]:
    """Parse the "1 hour ago" trending block off a trends24 page.

    trends24 renders each hourly snapshot in a `<div class="list-container">`.
    The first one is the most recent. We take the union of the last few
    snapshots (dedup by topic) so a topic that dominated across multiple
    hours ranks higher.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for m in _TREND_LI_RE.finditer(html):
        href = m.group(1)
        topic = unescape(m.group(2).strip())
        volume = (m.group(3) or '').strip()
        if not topic or topic.lower() in seen:
            continue
        seen.add(topic.lower())
        if href.startswith('/'):
            href = 'https://trends24.in' + href
        x_query = urllib.parse.quote(topic)
        out.append({
            'rank':   len(out) + 1,
            'topic':  topic[:120],
            'volume': volume,
            'url':    f'https://x.com/search?q={x_query}&src=trend_click',
        })
        if len(out) >= limit:
            break
    return out


def _fetch_one(url: str) -> list[dict]:
    r = http_get(url, headers=browser_headers(referer='https://trends24.in/'))
    if r is None or not r.ok:
        return []
    return _parse_trends24(r.text, limit=25)


def fetch() -> dict[str, Any]:
    national = _fetch_one('https://trends24.in/united-states/')

    by_state: dict[str, list[dict]] = {}
    for slug in STATE_SLUGS:
        items = _fetch_one(f'https://trends24.in/united-states/{slug}/')
        if items:
            by_state[_slug_to_state(slug)] = items

    if not national and by_state:
        # Fallback: if the national feed failed but state feeds worked,
        # roll up state trends into a proxy national list ordered by
        # cross-state occurrence.
        from collections import Counter
        c: Counter = Counter()
        for state_items in by_state.values():
            for it in state_items:
                c[it['topic']] += 1
        top = [t for t, _ in c.most_common(25)]
        national = [{
            'rank':  i + 1,
            'topic': t,
            'volume': '',
            'url':   f'https://x.com/search?q={urllib.parse.quote(t)}&src=trend_click',
        } for i, t in enumerate(top)]

    return {
        'national': national,
        'by_state': by_state,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('x', 'X', 'social', fetch)
    print(f"x: national={len(result.get('national', []))} "
           f"states={len(result.get('by_state') or {})} "
           f"error={result.get('error')}", file=sys.stderr)
