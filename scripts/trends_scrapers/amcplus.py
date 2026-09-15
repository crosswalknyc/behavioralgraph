"""
AMC+ trending scraper.

Top Film + TV titles on AMC+ (AMC Networks' premium subscription
streamer: The Walking Dead universe, Interview with the Vampire,
Mad Men, Anne Rice's Immortal Universe, Shudder and IFC Films
horror/indie catalog) via JustWatch's public GraphQL - the same
no-cookie, no-IP-block path the FAST tab and Paramount+ / Peacock
use, so this runs from Hetzner in the daily `run_all` batch. No
donated session, no residential hop. See `_justwatch_svod.py` for
the shared fetch, degrade ladder, and never-ship-empty posture.

JustWatch US package codes (verified against the live `packages`
query 2026-09-14). AMC+ is a SINGLE package, unlike Paramount+
(ppp + ppe) and Peacock (pct + pcp) which each carry two ad
tiers:

    acp  id 526   AMC+                       FLATRATE   <- used

Deliberately NOT unioned in:

    aat  id 1854  AMC Plus Apple TV channel  FLATRATE, FREE
        The same AMC+ catalog resold through the Apple TV
        storefront. A distribution path, not a tier, so unioning
        it would count the same subscriber base twice. It also
        carries a handful of Apple-channel-only extras that are
        not part of AMC+ proper.
    amc  id 80    AMC                        FLATRATE
        The AMC cable network's TV-Everywhere authenticated
        catalog. A different service with a different entitlement,
        not an AMC+ tier.
    amt  id 162   AMC Theatres               CINEMA
        The cinema chain. Unrelated to the streamer.

Standalone:
    python3 -m scripts.trends_scrapers.amcplus
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._justwatch_svod import fetch_svod_platform

logger = logging.getLogger(__name__)


SLUG     = 'amcplus'
LABEL    = 'AMC+'
PACKAGES = ['acp']


def fetch() -> dict[str, Any]:
    return fetch_svod_platform(SLUG, LABEL, PACKAGES)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
