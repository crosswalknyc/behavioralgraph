"""
Hallmark+ on Amazon: Hallmark+ carried on Amazon Prime Video Channels.

Jenna 2026-11-18: "lets add halmark plus to rankers ... also need to
add hallmark+ on amazon". Built under the carriage model approved on
2026-09-22: a service's main rail is its WHOLE US audience across every
distribution path, and an "X on Amazon" rail is the slice of that
audience which reaches it inside Prime Video, always strictly under
the parent.

THE CATALOG IS THE SAME. Hallmark+ sold through Prime Video Channels is
the same entitlement and the same title list as the Hallmark+ app
(Hallmark's own care page says the difference is billing and the
Crown Rewards benefits, which are direct-only), so this module scrapes
nothing. It mirrors `latest/hallmarkplus.json` under its own slug so
the Streaming tab renders it as its own panel, and the board extends
it with the same depth block the parent reads.

WHAT DIFFERS IS THE AUDIENCE, and that split is researched, never read
off the clickstream (`.cursor/rules/trends-rankers-never-clickstream.
mdc`). Hallmark publishes no Amazon split, so the share is a BAND
bracketed from the published quantities around the service; the band,
the anchors and the working live in
`scripts/trends_scrapers/carriage_mix.py` under `hallmarkplus`, and the
registry entry that applies it lives in
`scripts/trends_scrapers/derived_rails.py` under `hallmarkplus_amazon`.

WHERE A TITLE SITS INSIDE THE BAND IS REASONED, title by title, in
`scripts/trends_scrapers/carriage_leans.py`: the Christmas and mystery
catalog an older Prime Video browser adds as a channel sits high, the
Hallmark+ exclusive series people install the app and join the
membership for sit low, and the daily movement rides on top. This
module runs that refresh for any title on the catalog that has no lean
yet, once a night, then mirrors the catalog. A refresh that cannot run
(no key, cap reached) never blocks the mirror; the title takes the
draw until the next run.

HOW THE NUMBER IS PRODUCED. This panel is a SUBSET of the Hallmark+
panel, so its number is computed from that title's Hallmark+ number at
the point the board is produced, never carried alongside it.

Runs in the nightly batch on the build box (it only reads S3); the
parent is read residentially earlier the same day.

Standalone:
    python3 -m scripts.trends_scrapers.hallmarkplus_amazon
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from .derived_rail_mirror import mirror
from .derived_rails import band_for, rail_for, share_for

logger = logging.getLogger(__name__)


SLUG        = 'hallmarkplus_amazon'
LABEL       = 'Hallmark+ on Amazon'
SOURCE_SLUG = 'hallmarkplus'

# The numbers themselves live in the `derived_rails` registry, which
# is what the board actually reads, so there is one definition of the
# band rather than two that can drift.
_RAIL               = rail_for(SLUG)
AMAZON_SHARE_ANCHOR = _RAIL.anchor_share if _RAIL else 0.0
_FILM_SHARE_BAND    = band_for(SLUG, 'Film')
_TV_SHARE_BAND      = band_for(SLUG, 'TV')


def amazon_share_for_title(title: str, category_display: str = '',
                           day_iso: str = '') -> float:
    """The share of this title's Hallmark+ audience carried through
    Prime Video Channels on `day_iso`. Reasoned per title, moved per
    day, always inside the researched band."""
    return share_for(SLUG, title, category_display, day_iso)


def _refresh_leans() -> None:
    try:
        from .carriage_leans import refresh_leans
        stats = refresh_leans(SLUG)
        logger.info('%s: leans %s', SLUG, stats)
    except Exception as e:  # noqa: BLE001
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
