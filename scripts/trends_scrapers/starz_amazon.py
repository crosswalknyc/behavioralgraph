"""
Starz carried on Amazon Prime Video Channels.

Jenna 2026-09-15: "add a new tab under streaming called STARZ plus on
Amazon and put the ranker of the stars content on Amazon Prime Video.
Would likely be the same Ish to regular stars but just more views
right?"

THE CATALOG IS THE SAME. Starz sold through Prime Video Channels is
the same entitlement and the same title list as the Starz app. Starz
management says so directly (Baird Global Consumer conference, 2026):
"The Starz product, our D2C product, our Amazon product, is exactly
the same product that's on Comcast, on DirecTV." So this module does
NOT scrape a second catalog. It mirrors `latest/starz.json`, which
`starz.py` already pulls from starz.com, and republishes it under its
own slug so the Streaming tab renders it as its own panel and
`streaming_depth`'s Starz block extends it to the same depth.

WHAT DIFFERS IS THE AUDIENCE, and that split is researched, never
read off the clickstream (see `.cursor/rules/
trends-rankers-never-clickstream.mdc`). Published anchors, all US:

  1. Starz Entertainment Corp Form 10-KT filed 2026-02-26, nine
     months ended 2025-12-31: "Starz generated 29.0% of its revenue
     from Amazon.com, Inc. and its subsidiaries." The prior fiscal
     year (ended 2025-03-31) read 29.7%. Amazon is the only
     distributor Starz has to name under the customer-concentration
     disclosure.
  2. Same filer, Q4 2025 results (2026-02-26): nine-month revenue of
     $963.4M, of which OTT was $654.2M and linear and other $309.2M.
     Amazon Channels is an OTT path, so the disclosed Amazon dollars
     sit inside the OTT line: 0.290 x $963.4M = $279.4M, which is
     42.7% of OTT revenue.
  3. Same release, subscribers at 2025-12-31: 12.66M US OTT, 4.97M US
     linear, 17.63M US total. Starz stopped publishing subscriber
     counts after this quarter, so it is the current anchor.
  4. Starz management, Raymond James 47th Annual Institutional
     Investors Conference, 2026: "Amazon is our single biggest
     distributor"; direct to consumer is "our second-biggest
     distribution platform"; "We're basically two-thirds wholesale,
     one-third retail"; "70% of our business comes from digital."
  5. Starz CEO Jeffrey Hirsch, Q1 2026 earnings call (reported in
     Variety): the Universal pay-two exit was driven by "the high
     subscriber overlap between Amazon and Starz" and those films
     being "heavily watched before they come to us" on Prime Video.
  6. Antenna, State of Subscriptions Q3 2026: Amazon Channels is the
     dominant channels storefront at 67% of specialty SVOD gross adds
     in Q2 2026, and Starz is one of the seven premium services sold
     there.
  7. BTIG (2017, historical only): "upwards of 75%" of Starz OTT subs
     then came through Amazon Channels. Too old to anchor a 2026
     level, but it establishes that Amazon has carried the majority
     of the Starz streaming base for most of the service's OTT life.

READING THOSE TOGETHER. Amazon earns 42.7% of Starz OTT revenue on
wholesale economics, where Amazon keeps a distribution margin and
Channels promo pricing runs below the retail card rate, so a dollar
share of 42.7% implies a subscriber share at or a little above it.
Pulling the other way, "two-thirds wholesale, one-third retail" is
stated across the whole company including the 4.97M linear base, and
the non-Amazon OTT wholesale paths (Hulu, Roku, YouTube Primetime,
Apple, Xfinity) are real. Those two bracket the answer rather than
contradict it: Prime Video Channels carries about 44% of the Starz US
streaming audience, the Starz app about a third, and the remaining
channels storefronts and MVPD-sold OTT paths the rest.

So Jenna's expectation holds against the direct app and only against
the direct app: the Amazon-carried audience is the larger of the two
distribution paths, which is exactly what management means by calling
Amazon the biggest distributor and D2C the second. It is NOT larger
than Starz overall, because it is part of Starz overall. The existing
Starz panel stays the whole service and this panel is the slice of it
that watches inside Prime Video.

PER TITLE, the share is not flat. Hirsch's own explanation of the
Universal exit is that studio films reach Prime Video subscribers in
the pay-one window and get watched there first, so the film library
over-indexes to the Amazon-carried audience, while the Starz flagship
originals (the Power universe, Outlander, BMF, Spartacus, P-Valley)
under-index because that fandom is what drives people to install the
Starz app itself. Films therefore draw from a higher band than
series, and every title draws its own share deterministically inside
its band so no two titles share one.

HOW THE NUMBER IS PRODUCED (2026-09-17). This panel is a SUBSET of the
Starz panel, so its number is computed from that title's Starz number
at the point the board is produced, never carried alongside it. The
share bands above and the arithmetic that applies them live in
`scripts/trends_scrapers/derived_rails.py`, which is the general
mechanism for any rail that is one distribution path through another
rail's service; this module stays the evidence for the Starz case.

Standalone:
    python3 -m scripts.trends_scrapers.starz_amazon
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from .derived_rail_mirror import mirror
from .derived_rails import band_for, rail_for, share_for

logger = logging.getLogger(__name__)


SLUG         = 'starz_amazon'
LABEL        = 'Starz on Amazon'
SOURCE_SLUG  = 'starz'


# Share of the Starz US streaming audience that watches through Prime
# Video Channels, reasoned from the anchors in the module docstring.
# `AMAZON_SHARE_ANCHOR` is the service-level read; the two bands are
# the per-title spread around it, films above and series below, and
# they blend back to roughly the anchor across a panel that renders
# twice as many films as series.
#
# The numbers themselves live in the `derived_rails` registry, which is
# what the board actually reads, so there is one definition of the band
# rather than two that can drift. These names are kept because they
# read as the research this module documents, and because callers
# outside this file import them.
_RAIL               = rail_for(SLUG)
AMAZON_SHARE_ANCHOR = _RAIL.anchor_share if _RAIL else 0.44
_FILM_SHARE_BAND    = band_for(SLUG, 'Film')
_TV_SHARE_BAND      = band_for(SLUG, 'TV')


def amazon_share_for_title(title: str, category_display: str = '',
                           day_iso: str = '') -> float:
    """The share of this title's Starz audience that is carried
    through Prime Video Channels on `day_iso`.

    Deterministic per title and per day, so two titles never land on
    one share and one title does not sit on a constant across days. A
    film draws from the higher band and a series from the lower one;
    anything unclassified draws from the span of both, which is the
    same thing as not claiming to know which way it leans.

    Delegates to the shared registry so the Starz bands and any future
    service's bands are applied by one piece of arithmetic.
    """
    return share_for(SLUG, title, category_display, day_iso)


def fetch() -> dict[str, Any]:
    # Mirroring a parent catalog is now shared with every other
    # derived rail (`derived_rail_mirror`), so there is one
    # implementation of it rather than one per rail. Behaviour is
    # unchanged: republish the parent's title list under this slug,
    # keep the previous mirror and mark it stale if the parent reads
    # empty. What stays here is the evidence in the docstring above.
    return mirror(SLUG, SOURCE_SLUG)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
