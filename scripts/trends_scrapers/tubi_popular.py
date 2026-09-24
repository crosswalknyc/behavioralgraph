"""
Tubi's own Most Popular chart.

Jenna 2026-09-23, verbatim: *"for tubi when I go there and see Most
Popular those are not showing up in tubi. like I dont see everybody
hates chris at all and it looks like their top show. we likely need to
do this on everything for rankers"*.

Until this landed, every Tubi row on the board carried a position taken
from JustWatch's cross-service popularity pool (`fast_channels.py`,
package `tbv`) and rendered it as `Tubi #3`. That is a third party's
ranking wearing Tubi's name. Tubi publishes its own, at
`tubitv.com/category/most_popular`, sixty titles deep, with no session
and no paywall, and that list is what a viewer sees when they open the
app. It is the one Jenna checks.

Why this reads as viewing rather than merchandising
---------------------------------------------------
A curated shelf coheres around something: a genre, a season, a launch.
This one does not. A single capture holds a 1999 cartoon, a 2026 Tubi
original, a Scooby-Doo series, a 2004 Will Smith vehicle and a
daytime court show. Tubi merchandises elsewhere on the same page and
those rails look nothing like it: `Only Free on Tubi`, `Cult Classics`
and `Black Storytelling` each hold together on an obvious theme, and
none of them share this rail's ordering. The membership also turns
over against the catalog rails rather than with them.

Stability was measured before trusting the order: six loads across two
independent browser contexts returned all sixty positions identical.
The chart does move through the day, which is what the capture pin
below is for.

Capture pin
-----------
The chart is recomputed by Tubi through the day. Two runs hours apart
therefore disagree, and the board would read that disagreement as
day-over-day movement when it is only scrape timing. So the FIRST
healthy capture of each UTC day wins: a later run on the same day
re-reads the page, logs what it saw, and keeps the earlier ordering.
The capture time rides on the snapshot either way.

Session: none. Anonymous is the whole chart.

Standalone:
    python3 -m scripts.trends_scrapers.tubi_popular
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from ._base import run_scraper

logger = logging.getLogger(__name__)


CHART_URL = 'https://tubitv.com/category/most_popular'
HOMEPAGE = 'https://tubitv.com/'

# The collection name every row is tagged with. `_PUBLISHED_CHARTS` in
# `stream_estimates` matches on this string, so the two move together.
COLLECTION = 'Most Popular'

# Tubi serves sixty. Read what is there rather than assuming the count.
_MAX_ROWS = 60

# Below this a capture is not healthy enough to pin the day to, or to
# publish over a good one. A partial render is the realistic failure
# here: the grid hydrates in chunks, so a short read means we caught it
# mid-hydration, not that Tubi shortened its chart.
_MIN_HEALTHY = 20

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/tubi_popular.json'


# Tiles are anchors carrying the display title as their text, with the
# kind in the path: `/series/<id>/<slug>` or `/movies/<id>/<slug>`.
# Read in document order, which IS the ranking.
_EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const a of document.querySelectorAll('a.web-content-tile__title')) {
    const href = a.getAttribute('href') || '';
    const title = clean(a.innerText);
    if (!title) continue;
    out.push({href, title});
  }
  return JSON.stringify(out);
}
"""


def _classify(href: str) -> str:
    """`/series/...` is TV, `/movies/...` is Film.

    Matches the `Film` / `TV` spelling `fast_channels` already writes so
    both feed the same reader.
    """
    h = (href or '').lower()
    if h.startswith('/movies/'):
        return 'Film'
    if h.startswith('/series/'):
        return 'TV'
    return ''


def _scrape() -> list[dict]:
    """Render the chart page and return its rows in published order."""
    from ._playwright import render_pages

    def _hook(page, _label):
        # The grid lazy-loads as it scrolls, so walk the page before
        # reading. Sixty tiles is roughly four screens.
        for _ in range(6):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(900)
        page.wait_for_timeout(1200)
        return page.evaluate(_EXTRACT_JS)

    rendered = render_pages(
        [(COLLECTION, CHART_URL)], homepage=HOMEPAGE,
        wait_ms=6000, scroll_ms=3000, timeout_ms=60000,
        hydration_wait_ms=14000, page_hook=_hook)
    if not rendered:
        logger.warning("tubi_popular: nothing rendered")
        return []

    try:
        raw = json.loads(rendered[0][1])
    except Exception:
        logger.warning("tubi_popular: could not parse extraction payload")
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        title = (entry.get('title') or '').strip()
        href = (entry.get('href') or '').strip()
        if not title or len(title) > 200:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            'rank':             len(rows) + 1,
            'title':            title,
            'url':              f'https://tubitv.com{href.split("?")[0]}',
            'category_display': _classify(href),
            'collection':       COLLECTION,
        })
        if len(rows) >= _MAX_ROWS:
            break
    return rows


def _previous_snapshot() -> Optional[dict]:
    """Today's stored capture, for the pin and the soft-block fallback."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        obj = s3.get_object(Bucket=_S3_BUCKET, Key=_S3_LATEST)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:  # noqa: BLE001
        logger.info("tubi_popular: no previous snapshot (%s)", e)
        return None


def _healthy(rows: Any) -> bool:
    return isinstance(rows, list) and len(rows) >= _MIN_HEALTHY


def _drift(before: list[dict], after: list[dict]) -> tuple[int, float]:
    """How far the chart moved: set churn, and median rank shift.

    Counting positions that differ is the obvious measure and it lies.
    One title entering at the top pushes every row below it down a
    place, which reads as near-total change when the ordering barely
    moved at all. So report the two things that are actually true: how
    many titles came and went, and how far the surviving ones travelled.
    """
    pos_a = {(r.get('title') or '').lower(): i
             for i, r in enumerate(before, 1)}
    pos_b = {(r.get('title') or '').lower(): i
             for i, r in enumerate(after, 1)}
    both = set(pos_a) & set(pos_b)
    churn = len(set(pos_a) ^ set(pos_b))
    if not both:
        return churn, 0.0
    shifts = sorted(abs(pos_a[t] - pos_b[t]) for t in both)
    mid = len(shifts) // 2
    median = (float(shifts[mid]) if len(shifts) % 2
              else (shifts[mid - 1] + shifts[mid]) / 2.0)
    return churn, median


def fetch() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    prev = _previous_snapshot() or {}
    prev_rows = prev.get('national') or []
    prev_date = prev.get('chart_capture_date') or ''
    prev_at = prev.get('chart_captured_at') or ''

    rows = _scrape()
    logger.info("tubi_popular: read %d row(s) from %s", len(rows), CHART_URL)

    # The pin. A healthy capture already exists for this UTC day, so
    # that one is the day's chart and this read is only an observation.
    if prev_date == today and _healthy(prev_rows):
        if _healthy(rows):
            churn, shift = _drift(prev_rows, rows)
            logger.info(
                "tubi_popular: keeping today's pinned capture from %s "
                "(%d rows); this read is not adopted. Drift since the "
                "pin: %d title(s) entered or left, median rank shift "
                "%.1f place(s) among the titles on both",
                prev_at, len(prev_rows), churn, shift)
        else:
            logger.info(
                "tubi_popular: keeping today's pinned capture from %s; "
                "this read returned %d row(s)", prev_at, len(rows))
        return {
            'national':            prev_rows,
            'chart_captured_at':   prev_at,
            'chart_capture_date':  today,
            'chart_url':           CHART_URL,
            'chart_collection':    COLLECTION,
            'capture_pinned':      True,
            'last_observed_at':    now.isoformat(),
            'last_observed_count': len(rows),
        }

    # First healthy capture of the day: this one is the day's chart.
    if _healthy(rows):
        return {
            'national':           rows,
            'chart_captured_at':  now.isoformat(),
            'chart_capture_date': today,
            'chart_url':          CHART_URL,
            'chart_collection':   COLLECTION,
            'capture_pinned':     False,
        }

    # Nothing usable. Never publish a short read over a good one: the
    # rail would lose most of its chart and the tail would be promoted
    # into positions Tubi never gave it.
    reason = (f'tubi_popular: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if _healthy(prev_rows):
        logger.warning("tubi_popular: preserving previous capture from %s "
                       "(%d rows)", prev_at, len(prev_rows))
        return {
            'national':            prev_rows,
            'chart_captured_at':   prev_at,
            'chart_capture_date':  prev_date,
            'chart_url':           CHART_URL,
            'chart_collection':    COLLECTION,
            'capture_pinned':      True,
            'stale_from_previous': True,
            'soft_block_reason':   reason,
            'last_observed_at':    now.isoformat(),
            'last_observed_count': len(rows),
        }
    return {'national': [], 'soft_block_reason': reason,
            'chart_url': CHART_URL, 'chart_collection': COLLECTION}


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('tubi_popular', 'Tubi Most Popular', 'fast', fetch)
    rows = result.get('national') or []
    print(f"tubi_popular: {len(rows)} rows  "
          f"pinned={result.get('capture_pinned')}  "
          f"captured_at={result.get('chart_captured_at')}  "
          f"error={result.get('error')}", file=sys.stderr)
    for r in rows[:10]:
        print(f"  {r['rank']:>2}. [{r['category_display']:<4}] {r['title']}",
              file=sys.stderr)
