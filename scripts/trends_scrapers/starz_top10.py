"""
Starz's own Top 10 Movies Today.

`starz.py` fills the catalog from starz.com's __NEXT_DATA__, which is
a browse listing and carries no ranking. Starz publishes one chart of
its own, on its movies page, and names it outright: 'STARZ Top 10
Movies Today', with a '#1 Movie on STARZ Today' card beside it.

Films only, on purpose
----------------------
Starz's series page carries a rail headed 'Popular', and it is NOT
wired. It is almost entirely the Power universe and Outlander, which
is an Originals shelf: it coheres around exactly the thing a premium
service merchandises hardest. The films chart does not cohere around
anything, which is what makes it readable as viewing. Taking the one
and not the other is the same call made against Xumo's 'Most Popular'
and Hulu's home 'Trending'.

The tile
--------
Rail tiles are anchors whose CSS module is named for the chart:

    <a class="Top10Slide_key-art-container__Ed2n9"
       href="movies/in-the-grey-71959">
      <img class="Top10Slide_key-art__uKGjk" alt="In The Grey">

So the tile is matched on `Top10Slide`, which says what it is, and
the title is the poster's alt, which is the title and nothing else.
The anchor's own text is a badge ('New Movie', 'Leaving Soon') and is
not the title.

There is no rank in the markup, so position is document order within
the rail. That is sound HERE and only here: this rail is the chart
itself rather than a rail that happens to contain one, so its order
is the ranking. Where a service numbers its tiles (Disney+, HBO Max)
the number is read instead, because a partly rendered rail would
otherwise renumber from 1.

Runs residentially: starz.com's Akamai config fingerprints the build
box's datacenter IP. No session needed.

Standalone:
    python3 -m scripts.trends_scrapers.starz_top10
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from ._base import run_scraper

logger = logging.getLogger(__name__)


MOVIES_URL = 'https://www.starz.com/us/en/movies'
HOMEPAGE = 'https://www.starz.com/us/en/'

CHART_NAME = 'STARZ Top 10 Movies Today'

_DEPTH = 10
_MIN_HEALTHY = 6

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/starz_top10.json'
_MERGE_KEY = 'trends_iq_snapshots/latest/starz.json'


_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [];
  const seen = new Set();
  // The CSS module is named for the chart, so the tile says what it
  // is without relying on the heading above it.
  for (const a of document.querySelectorAll('a[class*="Top10Slide"]')) {
    const im = a.querySelector('img[alt]');
    const title = clean(im ? im.alt : '');
    const href = a.getAttribute('href') || '';
    if (!title) continue;
    const k = title.toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    rows.push({title: title, href: href});
  }
  return JSON.stringify(rows);
}"""


def _hook(page, label):
    for _ in range(14):
        try:
            page.mouse.wheel(0, 1100)
        except Exception:
            break
        page.wait_for_timeout(900)
    try:
        return page.evaluate(_COLLECT_JS)
    except Exception as e:  # noqa: BLE001
        logger.info("starz_top10 %s: rail read failed (%s)", label, e)
        return '[]'


def extract(blob: str) -> list[dict]:
    try:
        rows = json.loads(blob or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for i, row in enumerate(rows[:_DEPTH], 1):
        href = (row.get('href') or '').lstrip('/')
        out.append({
            'rank':             i,
            'title':            row['title'],
            'url':              (f'https://www.starz.com/us/en/{href}'
                                 if href else HOMEPAGE),
            'category_display': 'Film',
            'collection':       CHART_NAME,
        })
    return out


def _previous() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        return json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("starz_top10: no previous snapshot (%s)", e)
        return {}


def _merge_into_service_snapshot(rows: list[dict]) -> None:
    """Put the chart at the top of `starz.json`.

    Everything downstream reads a service's chart out of the snapshot
    named after the service. `starz` rewrites that file, and both run
    from the residential runner with this one immediately after, so
    the order holds. The merge is idempotent.
    """
    if not rows:
        return
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        snap = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_MERGE_KEY)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("starz_top10: could not read %s to merge into "
                       "(%s); the chart is still in its own snapshot",
                       _MERGE_KEY, e)
        return

    prior = snap.get('national') or []
    keep = [r for r in prior
            if isinstance(r, dict)
            and (r.get('collection') or '') != CHART_NAME]
    charted = {(r['title'] or '').strip().lower() for r in rows}
    keep = [r for r in keep
            if (r.get('title') or '').strip().lower() not in charted]

    snap['national'] = rows + keep
    snap['chart_rails'] = [CHART_NAME]
    snap['chart_merged_at'] = datetime.now(timezone.utc).isoformat()
    try:
        s3.put_object(Bucket=_S3_BUCKET, Key=_MERGE_KEY,
                      Body=json.dumps(snap, ensure_ascii=False)
                      .encode('utf-8'),
                      ContentType='application/json')
        logger.info("starz_top10: merged %d chart row(s) into %s "
                    "(%d catalog rows kept of %d)", len(rows), _MERGE_KEY,
                    len(keep), len(prior))
    except Exception as e:  # noqa: BLE001
        logger.warning("starz_top10: merge write failed (%s)", e)


def fetch() -> dict[str, Any]:
    from ._playwright import render_pages

    rendered = render_pages(
        [('movies', MOVIES_URL)], homepage=HOMEPAGE, wait_ms=6000,
        scroll_ms=3000, timeout_ms=60000, hydration_wait_ms=14000,
        page_hook=_hook)

    rows = extract(rendered[0][1]) if rendered else []
    logger.info("starz_top10: %r -> %d row(s)", CHART_NAME, len(rows))
    if len(rows) >= _MIN_HEALTHY:
        _merge_into_service_snapshot(rows)
        return {'national': rows,
                'chart_captured_at': datetime.now(timezone.utc).isoformat(),
                'chart_rails': [CHART_NAME],
                'chart_positions': len(rows)}

    prev = _previous()
    prev_rows = prev.get('national') or []
    reason = (f'starz_top10: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if len(prev_rows) >= _MIN_HEALTHY:
        logger.warning("starz_top10: preserving previous capture from %s "
                       "(%d rows)", prev.get('chart_captured_at'),
                       len(prev_rows))
        return {'national': prev_rows,
                'chart_captured_at': prev.get('chart_captured_at'),
                'stale_from_previous': True,
                'soft_block_reason': reason}
    return {'national': [], 'soft_block_reason': reason}


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('starz_top10', 'Starz Top 10', 'streaming', fetch)
    rows = result.get('national') or []
    print(f"starz_top10: {len(rows)} rows  error={result.get('error')}",
          file=sys.stderr)
    for r in rows:
        print(f"  #{r['rank']:>2} {r['title']}", file=sys.stderr)
