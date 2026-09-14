"""
Paramount+ trending scraper.

Top Film + TV titles on Paramount+ (Paramount Skydance's flagship
subscription streamer: Landman, Tulsa King, Lioness, 1923, Star Trek,
South Park exclusive, NFL on CBS windows) via JustWatch's public
GraphQL - the same no-cookie, no-IP-block path the FAST tab uses, so
this runs from Hetzner in the daily `run_all` batch. No donated
session, no residential hop. See `_justwatch_svod.py` for the shared
fetch + failure posture.

JustWatch US package codes (verified against the live `packages`
query 2026-09-04): Paramount+ ships as two tier packages -
Premium (`ppp`, packageId 2303) and Essential (`ppe`, packageId
2616). The union is the full Paramount+ catalog; per-title dedupe in
the shared helper collapses the tier overlap. The Apple TV / Amazon /
Roku channel-store variants (`ppa` / `app` / `prk`) are the same
catalog resold through other storefronts and are intentionally left
out.

Standalone:
    python3 -m scripts.trends_scrapers.paramountplus
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._justwatch_svod import fetch_svod_platform

logger = logging.getLogger(__name__)


SLUG     = 'paramountplus'
LABEL    = 'Paramount+'
PACKAGES = ['ppp', 'ppe']


def fetch() -> dict[str, Any]:
    return fetch_svod_platform(SLUG, LABEL, PACKAGES)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
