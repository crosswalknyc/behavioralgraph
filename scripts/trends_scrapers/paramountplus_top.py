"""
Paramount+'s own Most Watched charts, for shows and for films.

`paramountplus.py` fills the catalog from JustWatch, which is a third
party's popularity pool and carries no Paramount+ ranking. Paramount+
publishes one of its own on each browse page, and this reads it.

No session. The rails are on the public browse pages and render the
same signed out as signed in.

Identified by the rail's own slug
---------------------------------
The rail sits in an element whose ID is `most+watched`, which is
the CMS slug, and that is what this matches. The visible heading
happens to agree with it today, but naming a rail by what it displays
is how the HBO Max film chart got read as editorial: the elements
whose accessible names were "Top 10 Series Today" and "Top 10 Movies
Today" carried "Popular TV" and "Fresh Starts" as their text.

Why this reads as viewing and not merchandising
-----------------------------------------------
The shows rail holds South Park, SpongeBob, MobLand, NCIS, Everybody
Loves Raymond, The Challenge and Big Brother: animation, a 2025
prestige drama, a procedural, a 1996 sitcom and two reality
franchises. The films rail puts four PAW Patrol titles in its ten
alongside horror and comedy. Neither coheres around a genre, an era
or a launch, and both differ from the openly editorial rails beside
them ("Recently Added", "Hot Right Now", "Top Searched"). A kids
franchise stacking four entries in a ten-slot shelf is not something
a merchandiser does; it is what a viewing base does.

Standalone:
    python3 -m scripts.trends_scrapers.paramountplus_top
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from ._base import run_scraper

logger = logging.getLogger(__name__)


HOMEPAGE = 'https://www.paramountplus.com/'

# (page label, url, the kind everything on that page is, chart name)
CHART_PAGES = [
    ('shows',  'https://www.paramountplus.com/browse/', 'TV',
     'Most Watched Shows'),
    ('movies', 'https://www.paramountplus.com/movies/', 'Film',
     'Most Watched Movies'),
]

# The rail's CMS slug, which is the stable identifier.
RAIL_SLUG = 'most+watched'

_DEPTH = 16
_MIN_HEALTHY = 6

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/paramountplus_top10.json'


# Find the rail by its slug, then read its tiles in document order,
# which IS the ranking.
_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const pick = root => {
    const rows = [];
    const seen = new Set();
    for (const a of root.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href') || '';
      if (!/\/(shows|movies|video)\//.test(href)) continue;
      let n = clean(a.getAttribute('aria-label') || '') || clean(a.textContent || '');
      if (!n) { const im = a.querySelector('img[alt]'); if (im) n = clean(im.alt); }
      if (!n || n.length < 2 || n.length > 180) continue;
      const k = n.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      rows.push({title: n, href: href.split('?')[0]});
    }
    return rows;
  };
  const out = [];
  // The slug is the element's ID on this site. Read data-testid too so
  // the match survives Paramount+ moving it, which is where every
  // other service in this suite keeps the same kind of value.
  for (const el of document.querySelectorAll('[id],[data-testid]')) {
    const slug = (el.getAttribute('id') || '').toLowerCase();
    const tid = (el.getAttribute('data-testid') || '').toLowerCase();
    if (slug !== 'most+watched' && tid !== 'most+watched') continue;
    // Climb until the node actually holds the tiles.
    let node = el, rows = pick(el);
    for (let i = 0; i < 5 && rows.length < 3 && node.parentElement; i++) {
      node = node.parentElement;
      rows = pick(node);
    }
    if (rows.length >= 3) out.push({rows: rows});
  }
  return JSON.stringify(out);
}"""


def _hook(page, label):
    for _ in range(10):
        try:
            page.mouse.wheel(0, 1100)
        except Exception:
            break
        page.wait_for_timeout(900)
    try:
        return page.evaluate(_COLLECT_JS)
    except Exception as e:  # noqa: BLE001
        logger.info("paramountplus_top %s: rail read failed (%s)", label, e)
        return '[]'


def extract(blob: str, kind: str, chart: str) -> list[dict]:
    """Rows for one page's Most Watched rail."""
    try:
        rails = json.loads(blob or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    if not rails:
        return []
    # One rail per page. If the slug ever appears twice, the longest
    # is the chart and the rest are duplicates of its own markup.
    rows = max((r.get('rows') or [] for r in rails), key=len, default=[])
    out: list[dict] = []
    for i, row in enumerate(rows[:_DEPTH], 1):
        href = row.get('href') or ''
        out.append({
            'rank':             i,
            'title':            row['title'],
            'url':              (f'https://www.paramountplus.com{href}'
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
        logger.info("paramountplus_top: no previous snapshot (%s)", e)
        return {}


# The chart is folded into Paramount+'s OWN snapshot as well as being
# written here.
#
# Everything downstream reads a service's chart out of the snapshot
# named after the service. HBO Max works that way because max.json IS
# its chart; Tubi needed its own file and a `snapshot` key on the
# declaration because `fast_channels.json` is a shared JustWatch feed
# and mixing a platform's own chart into it would make the two
# indistinguishable a year from now. Paramount+ is the easy case in
# between: it has a snapshot of its own, `paramountplus.json`, holding
# its catalog, and the chart belongs in it.
#
# Ordering matters and is the one fragile thing here. `paramountplus`
# rewrites that file from JustWatch every night, so this has to run
# AFTER it or the chart is silently dropped. `run_all.SCRAPERS` places
# it immediately after for that reason. The merge is idempotent: chart
# rows are keyed by title and replaced rather than appended, so a
# second run in the same night does not double them.
_MERGE_KEY = 'trends_iq_snapshots/latest/paramountplus.json'


def _merge_into_service_snapshot(rows: list[dict]) -> None:
    """Put the chart rows at the top of `paramountplus.json`."""
    if not rows:
        return
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        obj = s3.get_object(Bucket=_S3_BUCKET, Key=_MERGE_KEY)
        snap = json.loads(obj['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("paramountplus_top: could not read %s to merge "
                       "the chart into (%s); the chart is still in its "
                       "own snapshot", _MERGE_KEY, e)
        return

    charts = {c for c in (r['collection'] for r in rows)}
    keep = [r for r in (snap.get('national') or [])
            if isinstance(r, dict)
            and (r.get('collection') or '') not in charts]
    dropped = len(snap.get('national') or []) - len(keep)

    # A catalog row for a title the chart already carries would shadow
    # it, so the chart's copy wins.
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
        logger.info("paramountplus_top: merged %d chart row(s) into %s "
                    "(replaced %d prior chart row(s), %d catalog rows "
                    "kept)", len(rows), _MERGE_KEY, dropped, len(keep))
    except Exception as e:  # noqa: BLE001
        logger.warning("paramountplus_top: merge write failed (%s)", e)


def fetch() -> dict[str, Any]:
    from ._playwright import render_pages

    rendered = render_pages(
        [(label, url) for label, url, _k, _c in CHART_PAGES],
        homepage=HOMEPAGE, wait_ms=6000, scroll_ms=3000,
        timeout_ms=60000, hydration_wait_ms=14000, page_hook=_hook)

    by_label = dict(rendered)
    rows: list[dict] = []
    for label, _url, kind, chart in CHART_PAGES:
        got = extract(by_label.get(label, '[]'), kind, chart)
        logger.info("paramountplus_top %s: %r -> %d row(s)",
                    label, chart, len(got))
        rows.extend(got)

    if len(rows) >= _MIN_HEALTHY:
        _merge_into_service_snapshot(rows)
        return {'national': rows,
                'chart_captured_at': datetime.now(timezone.utc).isoformat(),
                'chart_rails': [c for _l, _u, _k, c in CHART_PAGES],
                'chart_positions': len(rows)}

    # Never publish a short read over a good one.
    prev = _previous()
    prev_rows = prev.get('national') or []
    reason = (f'paramountplus_top: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if len(prev_rows) >= _MIN_HEALTHY:
        logger.warning("paramountplus_top: preserving previous capture "
                       "from %s (%d rows)",
                       prev.get('chart_captured_at'), len(prev_rows))
        return {'national': prev_rows,
                'chart_captured_at': prev.get('chart_captured_at'),
                'stale_from_previous': True,
                'soft_block_reason': reason}
    return {'national': [], 'soft_block_reason': reason}


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('paramountplus_top', 'Paramount+ Most Watched',
                         'streaming', fetch)
    rows = result.get('national') or []
    print(f"paramountplus_top: {len(rows)} rows  "
          f"error={result.get('error')}", file=sys.stderr)
    for r in rows:
        print(f"  {r['collection'][:20]:<22} #{r['rank']:>2} {r['title']}",
              file=sys.stderr)
