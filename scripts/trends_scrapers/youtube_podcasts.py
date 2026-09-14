"""
YouTube Podcasts standalone runner.

The YouTube podcast rail lives inside the consolidated
`podcast_charts.json` snapshot (same as Apple / Spotify / Amazon /
Audible / Netflix) - the parsing logic lives in `podcast_charts.py`
as `_fetch_youtube_podcasts`. This module is a thin operator-facing
entry point that:

  - runs `_fetch_youtube_podcasts()` on its own for quick smoke
    tests (no S3 write, no daily-cron entanglement),
  - writes a per-source snapshot at
    `s3://dashboard-inputs/trends_iq_snapshots/latest/youtube_podcasts.json`
    (plus the standard dated copy) so a partner-facing pull that
    only wants the YouTube rail has one clean key to read,
  - stays in-sync with the consolidated snapshot: both are produced
    from the same parser, so `youtube_podcasts.json` and the
    `youtube_podcasts` source inside `podcast_charts.json` never
    drift.

Standalone:

    python3 -m scripts.trends_scrapers.youtube_podcasts
    python3 -m scripts.trends_scrapers.youtube_podcasts --dry-run

Nothing else consumes the standalone `youtube_podcasts.json` today -
the dashboard reads `podcast_charts.json` at request time - but
publishing it costs one PUT and makes the operator surface obvious.
Per Jenna 2026-08-31.
"""

from __future__ import annotations

import argparse
import logging
import sys

from ._base import run_scraper
from .podcast_charts import _fetch_youtube_podcasts

logger = logging.getLogger(__name__)


def fetch() -> dict:
    """Standalone fetch: just the YouTube Popular Podcasts shelf,
    normalized into the same row shape as every other podcast source.
    """
    items, sub = _fetch_youtube_podcasts(limit=50)
    return {
        # `national` powers the `_index.json` count summary. Populate
        # with the same list so ops sees an accurate row count in the
        # run log without cross-referencing the consolidated file.
        'national':  items,
        'available': bool(items),
        'sources': {
            'youtube_podcasts': {
                'label':     'YouTube Popular Podcasts (US)',
                'sub':       (sub or "YouTube's Popular Podcasts shelf. What US "
                                       "viewers are watching on youtube.com/podcasts "
                                       "right now."),
                'items':     items,
                'available': bool(items),
            },
        },
    }


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                         help=('Fetch and print a summary without writing to S3. '
                                 'Handy for local smoke tests.'))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    if args.dry_run:
        result = fetch()
        items = result.get('national') or []
        print(f"youtube_podcasts: {len(items)} items  ok={result.get('available')}",
               file=sys.stderr)
        for it in items[:10]:
            print(f"  #{it['rank']}  {it['title']}  -  {it.get('artist') or ''}",
                   file=sys.stderr)
        return 0

    result = run_scraper('youtube_podcasts',
                          'YouTube Popular Podcasts',
                          'podcast',
                          fetch)
    items = result.get('national') or []
    print(f"youtube_podcasts: n={len(items)}  ok={result.get('available')}",
           file=sys.stderr)
    for it in items[:5]:
        print(f"  #{it['rank']}  {it['title']}  -  {it.get('artist') or ''}",
               file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
