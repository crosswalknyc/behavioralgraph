"""
Instagram trending scraper (third-party aggregator TBD).

Instagram doesn't publish a public trending endpoint. Per Jenna's
follow-up decision, this scraper is scaffolded to point at a
third-party aggregator (rss.app / instagrapi with our own login /
similar) once we commit to one.

For now the scraper writes an empty snapshot with `available=False`
so the UI keeps showing the coming-soon tile without an error card.
When we pick a source, replace `fetch()` below with the real
implementation and flip the "available" flag downstream.

Standalone:

    python3 -m scripts.trends_scrapers.instagram
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper

logger = logging.getLogger(__name__)


def fetch() -> dict[str, Any]:
    return {
        'national': [],
        'available': False,
        'note': ('Instagram trending not wired yet. Waiting on a chosen '
                  'third-party aggregator (rss.app, instagrapi login, or '
                  'IG session cookie).'),
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('instagram', 'Instagram', 'social', fetch)
    print(f"instagram: national={len(result.get('national', []))} "
           f"error={result.get('error')}", file=sys.stderr)
