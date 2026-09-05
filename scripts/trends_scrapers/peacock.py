"""
Peacock trending scraper.

Top Film + TV titles on Peacock (NBCUniversal's subscription
streamer: Love Island USA, The Traitors, Poker Face, Yellowstone
library, The Office library, Sunday Night Football, Olympics windows)
via JustWatch's public GraphQL - the same no-cookie, no-IP-block path
the FAST tab uses, so this runs from Hetzner in the daily `run_all`
batch. No donated session, no residential hop. See
`_justwatch_svod.py` for the shared fetch + failure posture.

Note: peacocktv.com donated cookies DO exist in the donation store,
but they serve the separate Microdramas IQ module's hub scrape - the
Peacock home rail itself is paid-only and datacenter-hostile, which
is exactly why this scraper rides JustWatch instead.

JustWatch US package codes (verified against the live `packages`
query 2026-09-04): Peacock Premium (`pct`, packageId 386) and
Peacock Premium Plus (`pcp`, packageId 387). The union is the full
Peacock catalog; per-title dedupe in the shared helper collapses the
tier overlap. The Amazon channel-store variant (`pep`) and the FAST
grid (`ptf`, Peacock TV Live) are intentionally left out - the FAST
surface belongs to the FAST tab if it ever gets added there.

Standalone:
    python3 -m scripts.trends_scrapers.peacock
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._justwatch_svod import fetch_svod_platform

logger = logging.getLogger(__name__)


SLUG     = 'peacock'
LABEL    = 'Peacock'
PACKAGES = ['pct', 'pcp']


def fetch() -> dict[str, Any]:
    return fetch_svod_platform(SLUG, LABEL, PACKAGES)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
