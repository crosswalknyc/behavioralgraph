"""
Hulu's Popular rails, from the TV and Movies hubs.

`hulu.py` fills the catalog from the hub pages and JustWatch, neither
of which carries a Hulu ranking. Hulu publishes a 'Popular' rail on
each hub and those are the two charts here.

THE HOME 'TRENDING' RAIL IS NOT ONE OF THEM
-------------------------------------------
Hulu's signed-in home carries a rail headed 'Trending'. It is
deliberately not wired and must not be. Measured 2026-09-23 it was
half ID-style true crime (Jodi Arias, Susan Powell, Casey Anthony,
Captive Audience) on a page that also renders 'Because You Watched',
which is to say a personalised page showing a genre-clustered shelf.
Shipping that as a ranking would be the same defect as shipping a
brand hub, and harder to spot.

The two rails here are named by WHERE they are and by an EXACT
heading, never by a pattern: the rail headed exactly 'Popular' on
/hub/tv and on /hub/movies. 'Popular Sitcom', 'Popular TV' on the
home page and 'Trending' all fail that test on purpose. Hulu gives
its rails no stable slug, so page plus exact heading is the strongest
identifier available, and it is a narrow one.

The tile
--------
Each tile's aria-label carries the title AND its position:

    aria-label="The Secret Lives of Mormon Wives, Item 1 of many"

so position comes from the tile rather than from document order, and
a rail that half renders is skipped rather than renumbered from 1.

Runs residentially with a donated session: Hulu's WAF fingerprints
the build box's IP before the cookie check even runs.

Standalone:
    python3 -m scripts.trends_scrapers.hulu_popular
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from ._base import run_scraper

logger = logging.getLogger(__name__)


HOMEPAGE = 'https://www.hulu.com/'

# (page label, url, kind, chart name). The HOME hub is absent on
# purpose; see the module docstring.
CHARTS = [
    ('tv', 'https://www.hulu.com/hub/tv', 'TV', 'Popular TV'),
    ('movies', 'https://www.hulu.com/hub/movies', 'Film',
     'Popular Movies'),
]

_DEPTH = 15
_MIN_HEALTHY = 8

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/hulu_popular.json'
_MERGE_KEY = 'trends_iq_snapshots/latest/hulu.json'

_ITEM_RE = re.compile(r'^(.*?),\s*Item\s+(\d{1,3})\s+of\s+', re.I)


# Only the rail whose heading is EXACTLY 'Popular'.
_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const h of document.querySelectorAll(
        'h1,h2,h3,h4,[data-automationid="CollectionHeader__title"]')) {
    const t = clean(h.textContent || '').replace(/\s*VIEW ALL$/i, '');
    if (t.toLowerCase() !== 'popular') continue;
    let node = h, tiles = [];
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      tiles = [...node.querySelectorAll('a[href]')].filter(
          a => /\/(series|movie)\//.test(a.getAttribute('href') || ''));
      if (tiles.length >= 4) break;
    }
    if (tiles.length < 4) continue;
    const rows = [];
    for (const a of tiles) {
      rows.push({aria: clean(a.getAttribute('aria-label') || ''),
                 href: (a.getAttribute('href') || '').split('?')[0]});
    }
    out.push({rows: rows});
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
        logger.info("hulu_popular %s: rail read failed (%s)", label, e)
        return '[]'


def extract(blob: str, kind: str, chart: str) -> list[dict]:
    try:
        rails = json.loads(blob or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    if not rails:
        return []
    # The same rail matches twice (the heading and its automation id),
    # so take the fullest and dedupe.
    rows = max((r.get('rows') or [] for r in rails), key=len, default=[])
    out: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        m = _ITEM_RE.match(row.get('aria') or '')
        if not m:
            continue
        title = m.group(1).strip()
        rank = int(m.group(2))
        if not title or not (1 <= rank <= _DEPTH) or rank in seen:
            continue
        seen.add(rank)
        href = row.get('href') or ''
        out.append({
            'rank':             rank,
            'title':            title,
            'url':              (f'https://www.hulu.com{href}'
                                 if href.startswith('/') else HOMEPAGE),
            'category_display': kind,
            'collection':       chart,
        })
    out.sort(key=lambda r: r['rank'])
    return out


def _previous() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        return json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("hulu_popular: no previous snapshot (%s)", e)
        return {}


def _merge_into_service_snapshot(rows: list[dict]) -> None:
    """Put the charts at the top of `hulu.json`.

    Everything downstream reads a service's chart out of the snapshot
    named after the service. `hulu` rewrites that file and both run
    from the residential runner, this one immediately after, so the
    order holds. The merge is idempotent.
    """
    if not rows:
        return
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        snap = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_MERGE_KEY)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("hulu_popular: could not read %s to merge into "
                       "(%s); the charts are still in their own "
                       "snapshot", _MERGE_KEY, e)
        return

    charts = {r['collection'] for r in rows}
    prior = snap.get('national') or []
    keep = [r for r in prior
            if isinstance(r, dict)
            and (r.get('collection') or '') not in charts]
    charted = {(r['title'] or '').strip().lower() for r in rows}
    keep = [r for r in keep
            if (r.get('title') or '').strip().lower() not in charted]

    snap['national'] = rows + keep
    snap['chart_rails'] = sorted(charts)
    snap['chart_merged_at'] = datetime.now(timezone.utc).isoformat()
    try:
        s3.put_object(Bucket=_S3_BUCKET, Key=_MERGE_KEY,
                      Body=json.dumps(snap, ensure_ascii=False)
                      .encode('utf-8'),
                      ContentType='application/json')
        logger.info("hulu_popular: merged %d chart row(s) into %s "
                    "(%d catalog rows kept of %d)", len(rows),
                    _MERGE_KEY, len(keep), len(prior))
    except Exception as e:  # noqa: BLE001
        logger.warning("hulu_popular: merge write failed (%s)", e)


def fetch() -> dict[str, Any]:
    from ._playwright import render_pages

    rendered = dict(render_pages(
        [(label, url) for label, url, _k, _c in CHARTS],
        homepage=HOMEPAGE, cookie_domain='hulu.com', wait_ms=8000,
        scroll_ms=3000, timeout_ms=60000, hydration_wait_ms=16000,
        assert_signed_in='hulu.com', page_hook=_hook))

    rows: list[dict] = []
    for label, _url, kind, chart in CHARTS:
        got = extract(rendered.get(label, '[]'), kind, chart)
        logger.info("hulu_popular %s: %r -> %d row(s)", label, chart,
                    len(got))
        rows.extend(got)

    if len(rows) >= _MIN_HEALTHY:
        _merge_into_service_snapshot(rows)
        return {'national': rows,
                'chart_captured_at': datetime.now(timezone.utc).isoformat(),
                'chart_rails': [c for _l, _u, _k, c in CHARTS],
                'chart_positions': len(rows)}

    prev = _previous()
    prev_rows = prev.get('national') or []
    reason = (f'hulu_popular: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if len(prev_rows) >= _MIN_HEALTHY:
        logger.warning("hulu_popular: preserving previous capture from "
                       "%s (%d rows)", prev.get('chart_captured_at'),
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
    result = run_scraper('hulu_popular', 'Hulu Popular', 'streaming',
                         fetch)
    rows = result.get('national') or []
    print(f"hulu_popular: {len(rows)} rows  error={result.get('error')}",
          file=sys.stderr)
    for r in rows:
        print(f"  {r['collection'][:16]:<18} #{r['rank']:>2} {r['title']}",
              file=sys.stderr)
