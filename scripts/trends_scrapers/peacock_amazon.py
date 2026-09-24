"""
Peacock on Amazon: Peacock carried on Amazon Prime Video Channels.

One of the four rails Jenna asked for on 2026-09-24 ("now we need to
add a on amazon for hbo max, peacock, britbox, mgm+ again, not
formulaic so that it can ever look synthetic or be tracked as fake"),
under the carriage model approved on 2026-09-22: a service's main rail
is its WHOLE US audience across every distribution path, and an "X on
Amazon" rail is the slice of that audience which reaches it inside
Prime Video, always strictly under the parent.

THE CATALOG IS THE SAME. Peacock sold through Prime Video Channels is
the same entitlement and the same title list as the Peacock app, so
this module scrapes nothing. It mirrors `latest/peacock.json` under
its own slug so the Streaming tab renders it as its own panel, and the
board extends it with the same depth block the parent reads.

WHAT DIFFERS IS THE AUDIENCE, and that split is researched, never read
off the clickstream (`.cursor/rules/trends-rankers-never-clickstream.
mdc`). Peacock publishes no Amazon split, so the share is a BAND
bracketed from the published quantities around the service; the band,
the anchors and the working live in
`scripts/trends_scrapers/carriage_mix.py` under `peacock`, and the
registry entry that applies it lives in
`scripts/trends_scrapers/derived_rails.py` under `peacock_amazon`.

WHERE A TITLE SITS INSIDE THE BAND IS REASONED, title by title, in
`scripts/trends_scrapers/carriage_leans.py`: catalog film and library
TV that an older Prime Video audience adds as a channel sit high,
the originals people install the Peacock app for sit low, and the
daily movement rides on top. This module runs that refresh for any
title on the catalog that has no lean yet, once a night, then mirrors
the catalog. A refresh that cannot run (no key, cap reached) never
blocks the mirror; the title takes the draw until the next run.

HOW THE NUMBER IS PRODUCED. This panel is a SUBSET of the Peacock
panel, so its number is computed from that title's Peacock number at
the point the board is produced, never carried alongside it.

Standalone:
    python3 -m scripts.trends_scrapers.peacock_amazon
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from .derived_rail_mirror import mirror
from .derived_rails import band_for, rail_for, share_for

logger = logging.getLogger(__name__)


SLUG        = 'peacock_amazon'
LABEL       = 'Peacock on Amazon'
SOURCE_SLUG = 'peacock'

# The numbers themselves live in the `derived_rails` registry, which
# is what the board actually reads, so there is one definition of the
# band rather than two that can drift.
_RAIL               = rail_for(SLUG)
AMAZON_SHARE_ANCHOR = _RAIL.anchor_share if _RAIL else 0.0
_FILM_SHARE_BAND    = band_for(SLUG, 'Film')
_TV_SHARE_BAND      = band_for(SLUG, 'TV')


def amazon_share_for_title(title: str, category_display: str = '',
                           day_iso: str = '') -> float:
    """The share of this title's Peacock audience carried through
    Prime Video Channels on `day_iso`. Reasoned per title, moved per
    day, always inside the researched band."""
    return share_for(SLUG, title, category_display, day_iso)


def _refresh_leans() -> None:
    try:
        from .carriage_leans import refresh_leans
        stats = refresh_leans(SLUG)
        logger.info('%s: leans %s', SLUG, stats)
    except Exception as e:
        logger.warning('%s: lean refresh skipped this run: %s', SLUG, e)


def fetch() -> dict[str, Any]:
    _refresh_leans()
    return mirror(SLUG, SOURCE_SLUG)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
