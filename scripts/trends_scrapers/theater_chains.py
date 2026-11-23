"""
The theater chains on the Films tab, and the one call that reads them
all for the film_ticketing snapshot (Mac residential lane, with
Fandango, Cinemark, AMC and Regal).

Each chain card lists the films with the most showtimes today across
that chain's US theaters. theater_common.py says why showtimes and not
the site's own listing order. Every reader is independent and never
raises. A chain that cannot be read today keeps its last list for up
to two days instead of leaving a blank card; past that the card reads
as warming up like any other empty source.

Order on the tab: the four national ticket sites first (film_ticketing
owns those), then these chains by how many theaters they run.

Added 2026-11-23 (Jenna: "we should add more theaters ... lets add all
of them").
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from .theater_common import DEFAULT_LIMIT

logger = logging.getLogger(__name__)

CARRY_DAYS = 2


@dataclass(frozen=True)
class Chain:
    key: str            # source key in film_ticketing.sources
    label: str          # card title
    module: str         # reader module in this package
    short: str          # name in the card line ("across 33 Harkins theaters")
    site: Optional[str] = None   # site key for a reader that serves several chains


CHAINS: tuple[Chain, ...] = (
    Chain('marcus',     'Marcus Theatres',        'theater_marcus',    'Marcus'),
    Chain('bbtheatres', 'B&B Theatres',           'theater_webedia',   'B&B', site='bbtheatres'),
    Chain('alamo',      'Alamo Drafthouse',       'theater_alamo',     'Alamo Drafthouse'),
    Chain('harkins',    'Harkins Theatres',       'theater_harkins',   'Harkins'),
    Chain('malco',      'Malco Theatres',         'theater_malco',     'Malco'),
    Chain('emagine',    'Emagine Theatres',       'theater_emagine',   'Emagine'),
    Chain('santikos',   'Santikos Entertainment', 'theater_santikos',  'Santikos'),
    Chain('cinepolis',  'Cin\u00e9polis',         'theater_cinepolis', 'Cin\u00e9polis'),
    Chain('landmark',   'Landmark Theatres',      'theater_webedia',   'Landmark', site='landmark'),
    Chain('megaplex',   'Megaplex Theatres',      'theater_megaplex',  'Megaplex'),
    Chain('smg',        'Studio Movie Grill',     'theater_smg',       'Studio Movie Grill'),
    Chain('galaxy',     'Galaxy Theatres',        'theater_galaxy',    'Galaxy'),
    Chain('showcase',   'Showcase Cinemas',       'theater_webedia',   'Showcase', site='showcase'),
    Chain('epic',       'EPIC Theatres',          'theater_webedia',   'EPIC', site='epic'),
    Chain('marquee',    'Marquee Cinemas',        'theater_webedia',   'Marquee', site='marquee'),
)

CHAIN_KEYS = tuple(c.key for c in CHAINS)


def card_line(chain: Chain, theaters: Optional[int]) -> str:
    """The plain line under the card title."""
    n = int(theaters or 0)
    if n > 1:
        return f"The films with the most showtimes today across {n:,} {chain.short} theaters."
    return f"The films with the most showtimes today at {chain.label}."


def _read_one(chain: Chain, limit: int) -> tuple[list[dict], dict]:
    mod = importlib.import_module(f'{__package__}.{chain.module}')
    if chain.site:
        return mod.fetch(chain.site, limit=limit)
    return mod.fetch(limit=limit)


def _carry(prev_panel: Optional[dict], today: date) -> Optional[dict]:
    """The last good list, if it is recent enough to show."""
    if not prev_panel or not (prev_panel.get('items') or []):
        return None
    try:
        seen = date.fromisoformat(str(prev_panel.get('date') or '')[:10])
    except ValueError:
        return None
    if (today - seen).days > CARRY_DAYS:
        return None
    panel = dict(prev_panel)
    panel['carried'] = True
    return panel


def fetch_chains(only: Optional[set[str]] = None, *,
                 limit: int = DEFAULT_LIMIT,
                 previous: Optional[dict] = None) -> dict[str, dict]:
    """{key: source panel} for every chain (or the keys in `only`).

    `previous` is the last film_ticketing snapshot's `sources`, used to
    carry a chain that could not be read today. Chains run one after
    another: each one is seconds, and a headless fallback inside a
    reader must not share a browser thread with another.
    """
    out: dict[str, dict] = {}
    previous = previous or {}
    today = datetime.now(timezone.utc).date()
    for chain in CHAINS:
        if only and chain.key not in only and 'chains' not in only:
            continue
        t0 = time.monotonic()
        try:
            items, meta = _read_one(chain, limit)
        except Exception as e:      # a reader is not supposed to raise
            logger.warning("theaters: %s raised %s", chain.key, str(e)[:200])
            items, meta = [], {'reason': 'reader raised'}
        meta = meta or {}
        secs = time.monotonic() - t0
        if items:
            panel = {
                'label': chain.label,
                'sub': card_line(chain, meta.get('theaters')),
                'items': items,
                'available': True,
                'kind': 'showtimes',
                'group': 'chain',
                'theaters': meta.get('theaters'),
                'date': meta.get('date') or today.isoformat(),
            }
            logger.info("theaters: %s %d film(s) from %s theater(s) in %.1fs; "
                        "#1 %s (%s showtimes)", chain.key, len(items),
                        meta.get('theaters'), secs, items[0].get('title'),
                        items[0].get('showtimes'))
        else:
            panel = _carry(previous.get(chain.key), today)
            if panel:
                logger.warning("theaters: %s read nothing today (%s); keeping "
                               "the list from %s", chain.key,
                               meta.get('reason') or ('blocked' if meta.get('blocked') else 'empty'),
                               panel.get('date'))
            else:
                logger.warning("theaters: %s read nothing today (%s)", chain.key,
                               meta.get('reason') or ('blocked' if meta.get('blocked') else 'empty'))
                panel = {
                    'label': chain.label, 'sub': '', 'items': [],
                    'available': False, 'kind': 'showtimes', 'group': 'chain',
                }
        out[chain.key] = panel
    return out


if __name__ == '__main__':  # pragma: no cover - live smoke
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser(description='Read every theater chain once (no upload).')
    ap.add_argument('--only', default='', help='comma-separated chain keys')
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(',') if s.strip()} or None
    res = fetch_chains(only)
    for key, panel in res.items():
        items = panel.get('items') or []
        print(f"{key}: {len(items)} film(s), theaters={panel.get('theaters')} "
              f"{'(kept from ' + str(panel.get('date')) + ')' if panel.get('carried') else ''}",
              file=sys.stderr)
        for it in items[:5]:
            print(f"   #{it['rank']} {it['title']}  {it.get('showtimes')} showtimes"
                  f" / {it.get('theaters')} theaters", file=sys.stderr)
