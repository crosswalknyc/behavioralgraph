"""
Pluto TV's own Most Popular Movies and Top TV Series.

Pluto's titles reach the board through `fast_channels.py`, which is
JustWatch's cross-service popularity pool, so every Pluto row carried
a position a third party assigned and rendered it as 'Pluto TV #3'.
Pluto publishes two charts of its own, anonymously, and this reads
them. Same defect and same fix as Tubi.

Identified by category UUID, not by heading
-------------------------------------------
Each rail's own 'view all' link points at the category behind it:

    /us/category/1e16a0e9-2317-4758-917f-e4aeb90762c0/   movies
    /us/category/fff75417-3a50-4042-9ec2-bec991e9ca02/   series

That UUID is the stable identifier and it is what this matches, with
the heading kept only as a fallback. Naming a rail by what it
displays is how HBO Max's film chart was read as editorial: the
elements whose accessible names were 'Top 10 Series Today' and 'Top 10
Movies Today' carried 'Popular TV' and 'Fresh Starts' as their text.

Why these read as viewing
-------------------------
The films chart holds four John Wick titles beside Friday, Just Go
with It and Ferris Bueller's Day Off: a franchise cluster inside a
spread of decades and genres, which is the shape Disney+ showed with
five Toy Story films and Peacock with three Twilight films. Crucially
the John Wick titles are absent from Pluto's own 'First Time on
Pluto TV' and 'Recent Releases' rails, so they are not a new-arrival
push being merchandised. The series chart runs Gunsmoke, Jane the
Virgin, The Andy Griffith Show, Mama's Family and The 100, which is
classic television and modern CW drama together and coheres around
neither.

Capture pin
-----------
Same as Tubi: the first healthy capture of each UTC day wins. A later
run re-reads the page, logs the drift and keeps the earlier
ordering, so a chart that moves through the day cannot be read as
day-over-day movement that never happened.

Standalone:
    python3 -m scripts.trends_scrapers.pluto_popular
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from . import _chart_rail_guard as _guard
from ._base import run_scraper

logger = logging.getLogger(__name__)


HOMEPAGE = 'https://pluto.tv/'

# (page label, url, category UUID, heading fallback, kind, chart name)
CHARTS = [
    ('movies', 'https://pluto.tv/us/movies/',
     '1e16a0e9-2317-4758-917f-e4aeb90762c0',
     'most popular movies', 'Film', 'Most Popular Movies'),
    ('shows', 'https://pluto.tv/us/shows/',
     'fff75417-3a50-4042-9ec2-bec991e9ca02',
     'top tv series', 'TV', 'Top TV Series'),
]

_DEPTH = 20
_MIN_HEALTHY = 12

# Pluto publishes two charts, one per page. The floor above counts
# rows across both, so a page that never hydrates reads as healthy.
# See `_chart_rail_guard`.
_EXPECTED_CHARTS = ('series', 'movies')

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/pluto_popular.json'


_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  // A rail is a carousel whose own title link points at a category.
  for (const link of document.querySelectorAll('a[class*="carousel-title"]')) {
    const href = link.getAttribute('href') || '';
    const m = href.match(/\/category\/([0-9a-f-]{16,})\//i);
    let node = link, tiles = [];
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      tiles = [...node.querySelectorAll('a[class*="TileLink"]')];
      if (tiles.length >= 3) break;
    }
    if (!tiles.length) continue;
    const rows = [];
    const seen = new Set();
    for (const a of tiles) {
      const im = a.querySelector('img[alt]');
      const title = clean(im ? im.alt : '');
      if (!title) continue;
      const k = title.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      rows.push({title: title, href: a.getAttribute('href') || ''});
    }
    if (rows.length) {
      out.push({uuid: m ? m[1] : '',
                heading: clean(link.textContent || ''),
                rows: rows});
    }
  }
  return JSON.stringify(out);
}"""


def _hook(page, label):
    for _ in range(12):
        try:
            page.mouse.wheel(0, 1100)
        except Exception:
            break
        page.wait_for_timeout(900)
    try:
        return page.evaluate(_COLLECT_JS)
    except Exception as e:  # noqa: BLE001
        logger.info("pluto_popular %s: rail read failed (%s)", label, e)
        return '[]'


def extract(blob: str, uuid: str, heading: str, kind: str,
            chart: str) -> list[dict]:
    """The one rail whose category UUID matches, or whose heading does."""
    try:
        rails = json.loads(blob or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    hit = next((r for r in rails if (r.get('uuid') or '') == uuid), None)
    if hit is None:
        hit = next((r for r in rails
                    if (r.get('heading') or '').strip().lower() == heading),
                   None)
        if hit is not None:
            logger.info("pluto_popular: %r matched on its heading; the "
                        "category UUID moved from %s to %r", chart, uuid,
                        hit.get('uuid'))
    if hit is None:
        return []
    out: list[dict] = []
    for i, row in enumerate((hit.get('rows') or [])[:_DEPTH], 1):
        href = row.get('href') or ''
        out.append({
            'rank':             i,
            'title':            row['title'],
            'url':              (f'https://pluto.tv{href}'
                                 if href.startswith('/') else HOMEPAGE),
            'category_display': kind,
            'collection':       chart,
        })
    return out


def _previous() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        return json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("pluto_popular: no previous snapshot (%s)", e)
        return {}


def _drift(before: list[dict], after: list[dict]) -> tuple[int, float]:
    """Set churn and median rank shift. Counting differing positions
    lies: one title entering at the top moves every row below it."""
    pa = {(r.get('title') or '').lower(): i for i, r in enumerate(before, 1)}
    pb = {(r.get('title') or '').lower(): i for i, r in enumerate(after, 1)}
    both = set(pa) & set(pb)
    churn = len(set(pa) ^ set(pb))
    if not both:
        return churn, 0.0
    s = sorted(abs(pa[t] - pb[t]) for t in both)
    mid = len(s) // 2
    return churn, (float(s[mid]) if len(s) % 2
                   else (s[mid - 1] + s[mid]) / 2.0)


def _render_charts(entries: list) -> list[dict]:
    """Render the given CHARTS entries and return their rows."""
    from ._playwright import render_pages

    if not entries:
        return []
    rendered = dict(render_pages(
        [(label, url) for label, url, _u, _h, _k, _c in entries],
        homepage=HOMEPAGE, wait_ms=6000, scroll_ms=3000,
        timeout_ms=60000, hydration_wait_ms=14000, page_hook=_hook))

    rows: list[dict] = []
    for label, _url, uuid, heading, kind, chart in entries:
        got = extract(rendered.get(label, '[]'), uuid, heading, kind,
                      chart)
        logger.info("pluto_popular %s: %r -> %d row(s)", label, chart,
                    len(got))
        rows.extend(got)
    return rows


def fetch() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    prev = _previous() or {}
    prev_rows = prev.get('national') or []
    prev_date = prev.get('chart_capture_date') or ''
    prev_at = prev.get('chart_captured_at') or ''

    rows = _render_charts(CHARTS)

    # Pluto's two charts live on two pages, so one of them failing is
    # one page that did not hydrate rather than Pluto publishing an
    # empty chart. The row floor counts across both and reads twenty
    # clean movie rows as healthy while the series chart is missing
    # entirely. Render the missing page once more, then carry
    # yesterday's rows for it rather than publishing half a day.
    unresolved: list[str] = []
    missing = _guard.missing_rails(rows, _EXPECTED_CHARTS,
                                   key_of=_guard.kind_key)
    if rows and missing:
        again = [c for c in CHARTS
                 if _guard.chart_kind(c[4]) in set(missing)]
        rows = _guard.rerender_recovered(
            rows, _render_charts(again), _EXPECTED_CHARTS,
            key_of=_guard.kind_key, label='pluto_popular')
        rows, unresolved = _guard.carry_missing(
            rows, prev_rows, _EXPECTED_CHARTS, key_of=_guard.kind_key,
            label='pluto_popular')

    healthy = len(rows) >= _MIN_HEALTHY
    prev_healthy = len(prev_rows) >= _MIN_HEALTHY

    # The pin. A healthy capture already exists for this UTC day, so
    # that one is the day's chart and this read is an observation.
    if prev_date == today and prev_healthy:
        if healthy:
            churn, shift = _drift(prev_rows, rows)
            logger.info(
                "pluto_popular: keeping today's pinned capture from %s "
                "(%d rows); not adopted. Drift since the pin: %d title(s) "
                "entered or left, median rank shift %.1f place(s)",
                prev_at, len(prev_rows), churn, shift)
        return {'national': prev_rows, 'chart_captured_at': prev_at,
                'chart_capture_date': today, 'capture_pinned': True,
                'last_observed_at': now.isoformat(),
                'last_observed_count': len(rows)}

    if healthy:
        out = {'national': rows,
               'chart_captured_at': now.isoformat(),
               'chart_capture_date': today,
               'chart_rails': [c for _l, _u2, _u, _h, _k, c in CHARTS],
               'capture_pinned': False}
        if unresolved:
            out['charts_unresolved'] = unresolved
        if any(r.get(_guard.STALE_FIELD) for r in rows):
            out['stale_from_previous'] = True
        return out

    reason = (f'pluto_popular: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if prev_healthy:
        logger.warning("pluto_popular: preserving previous capture from "
                       "%s (%d rows)", prev_at, len(prev_rows))
        return {'national': prev_rows, 'chart_captured_at': prev_at,
                'chart_capture_date': prev_date, 'capture_pinned': True,
                'stale_from_previous': True, 'soft_block_reason': reason,
                'last_observed_at': now.isoformat(),
                'last_observed_count': len(rows)}
    return {'national': [], 'soft_block_reason': reason}


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('pluto_popular', 'Pluto TV Most Popular', 'fast',
                         fetch)
    rows = result.get('national') or []
    print(f"pluto_popular: {len(rows)} rows  "
          f"pinned={result.get('capture_pinned')}  "
          f"error={result.get('error')}", file=sys.stderr)
    for r in rows[:12]:
        print(f"  {r['collection'][:20]:<22} #{r['rank']:>2} {r['title']}",
              file=sys.stderr)
