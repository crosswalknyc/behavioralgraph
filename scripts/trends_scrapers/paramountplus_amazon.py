"""
Paramount+ carried on Amazon Prime Video Channels.

Second rail under the carriage model Jenna approved on 2026-09-22: a
service's main rail is its WHOLE US audience across every
distribution path, and an "X on Amazon" rail is the slice of that
audience which reaches it inside Prime Video, always strictly under
the parent.

THE CATALOG IS THE SAME. Paramount+ sold through Prime Video Channels
is the same entitlement and the same title list as the Paramount+
app, so this module does not scrape a second catalog. It mirrors
`latest/paramountplus.json`, which `paramountplus.py` already pulls
through JustWatch, and republishes it under its own slug so the
Streaming tab renders it as its own panel.

WHAT DIFFERS IS THE AUDIENCE, and that split is researched, never
read off the clickstream (see `.cursor/rules/
trends-rankers-never-clickstream.mdc`). Published anchors, all US:

  1. Antenna, Q1 2025, reported service by service: Paramount+ takes
     30% of its subscriptions through Amazon Prime Video and 39%
     direct, with the balance across the Apple and Google app
     stores, the other channels storefronts and operator deals. This
     is the published PER-SERVICE split that earns Paramount+ a
     breakout where Max, Peacock, AMC+, MGM+ and BritBox do not get
     one.
  2. Same study, the frame around it: Prime Video Channels was 24%
     of US premium SVOD gross adds in Q1 2025, up from 21% a year
     earlier, against roughly 45% direct and 9% through the other
     channels storefronts. Paramount+ therefore sits above the
     category on Amazon and below it on direct, which is the
     long-standing Paramount strategy: Antenna put Paramount+ in the
     "Amazon Channels camp" as far back as its 2021 distribution
     study.
  3. Antenna's incrementality work, presented at Amazon's 2025
     Engage summit: across four launches on the storefront, one of
     them the Paramount+ Essential plan, 89% of the people who
     signed up through Prime Video would not have signed up at all
     had the service not been there. The Amazon slice is an
     additive audience, not a re-billing of the direct one.
  4. Antenna's measured base excludes MVPD and telco distribution
     and some bundles. Paramount+ carries a large bundled base
     outside that frame, chiefly Paramount+ Essential inside
     Walmart+, so the share of the WHOLE US base reached through
     Amazon is below the measured 30%.
  5. Amazon-sold subscriptions churn faster than direct ones, a
     point analysts have made about Channels since HBO's first exit,
     so a share of viewing reads a little under a share of
     subscriptions again.

READING THOSE TOGETHER. Points 4 and 5 both pull the measured 30%
down and nothing pulls it up, so the service-level read is about 27%
of the Paramount+ US streaming audience watching inside Prime Video.

PER TITLE, the share is not flat, and it moves the same way and for
the same reason it does on Starz. The Paramount pay-one film window
(Mission: Impossible, A Quiet Place, Sonic) lands in front of Prime
Video subscribers who are already in that app, so the film library
over-indexes to the Amazon-carried audience. What makes somebody
install the Paramount+ app itself is the flagship series and the live
windows, the Yellowstone universe and the NFL on CBS and Star Trek,
so series under-index. Films therefore draw from a higher band than
series, every title draws its own share inside its band so no two
titles share one, and the share moves from day to day inside that
band rather than sitting on a constant.

HOW THE NUMBER IS PRODUCED. This panel is a SUBSET of the Paramount+
panel, so its number is computed from that title's Paramount+ number
at the point the board is produced, never carried alongside it. The
bands and the arithmetic live in
`scripts/trends_scrapers/derived_rails.py`; the distribution mix of
every service on the tab, with the evidence for each, lives in
`scripts/trends_scrapers/carriage_mix.py`.

Standalone:
    python3 -m scripts.trends_scrapers.paramountplus_amazon
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from .derived_rail_mirror import mirror
from .derived_rails import band_for, rail_for, share_for

logger = logging.getLogger(__name__)


SLUG        = 'paramountplus_amazon'
LABEL       = 'Paramount+ on Amazon'
SOURCE_SLUG = 'paramountplus'


# Share of the Paramount+ US streaming audience that watches through
# Prime Video Channels, reasoned from the anchors in the module
# docstring. `AMAZON_SHARE_ANCHOR` is the service-level read; the two
# bands are the per-title spread around it, films above and series
# below.
#
# The numbers themselves live in the `derived_rails` registry, which
# is what the board actually reads, so there is one definition of the
# band rather than two that can drift.
_RAIL               = rail_for(SLUG)
AMAZON_SHARE_ANCHOR = _RAIL.anchor_share if _RAIL else 0.27
_FILM_SHARE_BAND    = band_for(SLUG, 'Film')
_TV_SHARE_BAND      = band_for(SLUG, 'TV')


def amazon_share_for_title(title: str, category_display: str = '',
                           day_iso: str = '') -> float:
    """The share of this title's Paramount+ audience carried through
    Prime Video Channels on `day_iso`.

    Deterministic per title and per day, so two titles never land on
    one share and one title does not sit on a constant across days. A
    film draws from the higher band and a series from the lower one;
    anything unclassified draws from the span of both, which is the
    same thing as not claiming to know which way it leans.
    """
    return share_for(SLUG, title, category_display, day_iso)


def fetch() -> dict[str, Any]:
    return mirror(SLUG, SOURCE_SLUG)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
