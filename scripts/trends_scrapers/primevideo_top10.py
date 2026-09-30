"""
Prime Video's own Top 10 charts, read off the signed-in US storefront.

`primevideo.py` runs on the build box at 06:00 UTC and fills the
catalog from the storefront's hydration blob. It also tries to read
the Top 10 rails, and for an anonymous visitor from that box they
never hydrate: every morning since the rails were declared it has
logged "no Top 10 rail hydrated this run; shipping the storefront
with no chart". Measured 2026-09-29 from the residential Mac with the
donated Amazon session, the same storefront renders both rails, ten
deep, in chart order. So the chart is read here, the way Netflix,
HBO Max, Disney+ and Peacock already are, and folded into the
snapshot the build box wrote.

Which rails
-----------
Two independent rankings, both on the main storefront:

    Top 10 in the US             what Prime members are watching on
                                 Prime itself
    Top 10 with subscriptions    what they are watching on the add-on
                                 channels

`_PUBLISHED_CHARTS['primevideo']` in `stream_estimates` declares
exactly these two, and the row's `collection` is what it matches on,
so the two move together. 'Top 10 purchases in the US' renders beside
them and is NOT a viewing chart: it ranks what people bought, which is
a different behaviour over a different population. It is excluded by
name in `primevideo._is_published_chart_rail`, which this imports so
the exclusion lives in one place.

Charts are read from the STOREFRONT only. The movies storefront and
the TV page carry Top 10 rails of their own ('Top 10 in the US' again,
'Top 10 TV shows in the US'), and reading more than one page put
twenty rows in one collection and numbered them 1 to 20. One page,
one chart.

Identified by the rail's own test id
------------------------------------
Rail identity comes from `[data-testid="carousel-title"]`, not from the
heading's text: the H2's textContent is the rail name with the
trending icon's label run onto the end of it ('Top 10 in the US
Trending'). Same lesson HBO Max taught, where the visible heading and
the published name were different strings.

The rails render below the fold and enter the DOM as the page is
scrolled, and which of the two arrive varies by render: a storefront
measured twice on 2026-09-29 showed 'Top 10 in the US' both times and
'Top 10 with subscriptions' once. That is what the one-rail guard in
`_chart_rail_guard` is for: render once more, then carry the still-
missing rail from the archive marked stale rather than shipping half
a day.

Session required. The anonymous storefront renders a browsable
catalog and the word "Watchlist" in its nav, so nothing here may
publish without proving the session first (see `_auth_guard`, which
reads the nav greeting for `amazon.com`).

Standalone:
    python3 -m scripts.trends_scrapers.primevideo_top10
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from . import _chart_rail_guard as _guard
from ._base import run_scraper
from .primevideo import _is_published_chart_rail, _kind_from_badge

logger = logging.getLogger(__name__)


HOME_URL = 'https://www.amazon.com/gp/video/storefront'
HOMEPAGE = 'https://www.amazon.com/'

# The two charts, spelled the way `_PUBLISHED_CHARTS['primevideo']`
# declares them. A rail's recorded `collection` is the name Amazon
# renders; the guard keys rows back to one of these by containment,
# so a decorated heading still resolves to the chart it is.
_EXPECTED_CHARTS = ('top 10 in the us', 'top 10 with subscriptions')

_DEPTH = 10

# Below this a capture is not worth publishing over a good one. A short
# read means a rail was caught mid-hydration, not that Amazon shortened
# its chart.
_MIN_HEALTHY = 6

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/primevideo_top10.json'

# Something to wait on before the walk starts. The storefront's blob
# hydrates first; the rails follow.
_HYDRATE_SELECTORS = ['[data-testid="carousel-title"]',
                      'a[href*="/gp/video/detail/"]']


def _chart_key(row: Any) -> str:
    """Which declared chart a row belongs to, or '' for none.

    Containment rather than equality, in one direction: the rendered
    name may carry decoration ('Top 10 in the US Trending'), and
    `stream_estimates._collection_matches` reads it the same loose
    way, so the guard and the board agree on what is charted.
    """
    coll = _guard.collection_key(row)
    if not coll:
        return ''
    coll = ' '.join(coll.replace('.', '').split())
    for name in _EXPECTED_CHARTS:
        if name in coll:
            return name
    return ''


# Every rail on screen whose title element is the storefront's own
# carousel heading, with the detail links under it in document order.
# Document order within a rail IS the ranking.
_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('[data-testid="carousel-title"]')) {
    const name = clean(el.textContent || '');
    if (!name) continue;
    let node = el, n = 0;
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      n = node.querySelectorAll('a[href*="/gp/video/detail/"]').length;
      if (n >= 3) break;
    }
    if (n < 3) continue;
    const rows = [];
    const seen = new Set();
    for (const a of node.querySelectorAll('a[href*="/gp/video/detail/"]')) {
      const title = clean(a.textContent || '');
      if (!title) continue;
      const k = title.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      const card = a.closest('article,[data-testid="card"]') || a.parentElement;
      rows.push({title: title,
                 href: (a.getAttribute('href') || '').split('?')[0],
                 badge: clean((card || {}).textContent || '').slice(0, 120)});
    }
    if (rows.length) out.push({rail: name, rows: rows});
  }
  return JSON.stringify(out);
}"""


def _harvest(page, acc: dict) -> None:
    """Fold whatever is on screen into the per-chart accumulator.

    Order within a rail is kept by first-seen position, so a later
    pass appends tiles that were off screen rather than renumbering
    the ones already recorded.
    """
    try:
        rails = json.loads(page.evaluate(_COLLECT_JS) or '[]')
    except Exception as e:  # noqa: BLE001
        logger.debug("primevideo_top10: harvest failed: %s", e)
        return
    for rail in rails:
        name = (rail.get('rail') or '').strip()
        if not _is_published_chart_rail(name):
            continue
        key = _chart_key({'collection': name})
        if not key:
            continue
        seen = acc.setdefault(key, {'name': name, 'tiles': []})
        have = {t['title'].lower() for t in seen['tiles']}
        for t in rail.get('rows') or []:
            title = (t.get('title') or '').strip()
            if not (2 <= len(title) <= 220) or title.lower() in have:
                continue
            have.add(title.lower())
            seen['tiles'].append({'title': title,
                                  'href': t.get('href') or '',
                                  'badge': t.get('badge') or ''})


def _complete(acc: dict) -> bool:
    return (len(acc) >= len(_EXPECTED_CHARTS)
            and all(len(v['tiles']) >= _DEPTH for v in acc.values()))


def collect_charts(page, label: str) -> str:
    """Walk the storefront down until both charts have been seen."""
    acc: dict[str, dict] = {}
    _harvest(page, acc)

    last_y, stuck = -1, 0
    for _ in range(30):
        if _complete(acc):
            break
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            break
        page.wait_for_timeout(900)
        _harvest(page, acc)
        try:
            y = page.evaluate('() => Math.round(window.scrollY)')
        except Exception:
            y = last_y
        stuck = stuck + 1 if y == last_y else 0
        last_y = y
        if stuck >= 3:
            break

    logger.info("primevideo_top10 %s: %s", label,
                ', '.join(f"{v['name']} {len(v['tiles'])}/{_DEPTH}"
                          for v in acc.values()) or 'no chart rails seen')
    return json.dumps(acc)


def _catalog_kinds() -> dict[str, str]:
    """Film or TV for every title the build box's catalog pull knows.

    A chart tile carries a kind only when Amazon badges it (NEW
    SERIES, NEW MOVIE, NEW SEASON). The hydration blob the catalog
    came from names every entity's type, so a badge-less chart row
    still keys to the right half of the estimates store rather than
    landing kind-less. Best effort; an empty map only costs the kind.
    """
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        snap = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_MERGE_KEY)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("primevideo_top10: catalog kinds unavailable (%s)", e)
        return {}
    out: dict[str, str] = {}
    for r in snap.get('national') or []:
        if not isinstance(r, dict):
            continue
        t = str(r.get('title') or '').strip().lower()
        k = str(r.get('category_display') or '').strip()
        if t and k in ('Film', 'TV'):
            out.setdefault(t, k)
    return out


def _rows_from(acc: dict, kinds: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for key in _EXPECTED_CHARTS:
        chart = acc.get(key)
        if not chart:
            continue
        for i, t in enumerate(chart['tiles'][:_DEPTH], 1):
            href = t.get('href') or ''
            title = t['title']
            rows.append({
                'rank':             i,
                'title':            title,
                'url':              (f'https://www.amazon.com{href}'
                                     if href.startswith('/') else HOMEPAGE),
                'category_display': (_kind_from_badge(t.get('badge') or '')
                                     or kinds.get(title.lower(), '')),
                'collection':       chart['name'],
            })
    return rows


def _previous() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        return json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("primevideo_top10: no previous snapshot (%s)", e)
        return {}


# The chart is folded into Prime Video's OWN snapshot as well as this
# one, because everything downstream reads a service's chart out of
# the snapshot named after the service.
#
# `primevideo` rewrites that file from the storefront blob on the
# build box at 06:00 UTC; this runs from the operator's laptop hours
# later, so the ordering holds in practice the way Peacock's does. If
# the catalog pull ever lands AFTER this one, Prime Video renders that
# day with no chart, which is what it did before this existed, and the
# board's own carry reads this snapshot's archive
# (`_CHART_ARCHIVE_SOURCES['primevideo']`). The merge is idempotent:
# chart rows are keyed by collection and replaced, never appended.
_MERGE_KEY = 'trends_iq_snapshots/latest/primevideo.json'


def _merge_into_service_snapshot(rows: list[dict]) -> None:
    """Put the chart at the top of `primevideo.json`."""
    if not rows:
        return
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        snap = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_MERGE_KEY)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("primevideo_top10: could not read %s to merge into "
                       "(%s); the chart is still in its own snapshot",
                       _MERGE_KEY, e)
        return

    # Prior chart rows, however the build box spelled the rail that
    # day, go; the catalog rows stay.
    prior = snap.get('national') or []
    keep = [r for r in prior
            if isinstance(r, dict) and not _chart_key(r)]
    # A catalog row for a title the chart already carries would shadow
    # it, so the chart's copy wins.
    charted = {(r['title'] or '').strip().lower() for r in rows}
    keep = [r for r in keep
            if (r.get('title') or '').strip().lower() not in charted]

    snap['national'] = rows + keep
    snap['chart_rails'] = sorted({r['collection'] for r in rows})
    snap['chart_merged_at'] = datetime.now(timezone.utc).isoformat()
    try:
        s3.put_object(Bucket=_S3_BUCKET, Key=_MERGE_KEY,
                      Body=json.dumps(snap, ensure_ascii=False)
                      .encode('utf-8'),
                      ContentType='application/json')
        logger.info("primevideo_top10: merged %d chart row(s) into %s "
                    "(%d catalog rows kept of %d)", len(rows), _MERGE_KEY,
                    len(keep), len(prior))
    except Exception as e:  # noqa: BLE001
        logger.warning("primevideo_top10: merge write failed (%s)", e)


def _render() -> dict:
    from ._playwright import render_pages

    rendered = render_pages(
        [('Storefront', HOME_URL)], homepage=HOMEPAGE,
        cookie_domain='amazon.com', wait_ms=6000, scroll_ms=3000,
        timeout_ms=70000, wait_selectors=_HYDRATE_SELECTORS,
        hydration_wait_ms=14000, assert_signed_in='amazon.com',
        page_hook=collect_charts)
    if not rendered:
        return {}
    try:
        return json.loads(rendered[0][1]) or {}
    except (TypeError, json.JSONDecodeError):
        return {}


def fetch() -> dict[str, Any]:
    kinds = _catalog_kinds()
    rows = _rows_from(_render(), kinds)
    now = datetime.now(timezone.utc).isoformat()

    # Which of the two rails Amazon renders varies by run, and the row
    # floor below counts across both, so ten clean rows on one chart
    # read as healthy while the other is missing. Render once more,
    # then carry the still-absent rail from the last capture or the
    # dated archive, marked stale.
    unresolved: list[str] = []
    if rows and _guard.missing_rails(rows, _EXPECTED_CHARTS,
                                     key_of=_chart_key):
        rows = _guard.rerender_recovered(
            rows, _rows_from(_render(), kinds), _EXPECTED_CHARTS,
            key_of=_chart_key, label='primevideo_top10')
        rows, unresolved = _guard.carry_missing(
            rows, _previous().get('national'), _EXPECTED_CHARTS,
            key_of=_chart_key, label='primevideo_top10',
            archive_source='primevideo_top10')

    if len(rows) >= _MIN_HEALTHY:
        _merge_into_service_snapshot(rows)
        out = {'national': rows, 'chart_captured_at': now,
               'chart_rails': sorted({str(r.get('collection') or '')
                                      for r in rows} - {''}),
               'chart_positions': len(rows)}
        if unresolved:
            out['charts_unresolved'] = unresolved
        if any(r.get(_guard.STALE_FIELD) for r in rows):
            out['stale_from_previous'] = True
        return out

    # Never publish a short read over a good one: the rail would lose
    # most of its chart and the catalog would be promoted into
    # positions Amazon never gave it.
    prev = _previous()
    prev_rows = prev.get('national') or []
    reason = (f'primevideo_top10: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if len(prev_rows) >= _MIN_HEALTHY:
        logger.warning("primevideo_top10: preserving previous capture "
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
    result = run_scraper('primevideo_top10', 'Prime Video Top 10',
                         'streaming', fetch)
    rows = result.get('national') or []
    print(f"primevideo_top10: {len(rows)} rows  error={result.get('error')}",
          file=sys.stderr)
    for r in rows:
        print(f"  {r['collection'][:26]:<28} #{r['rank']:>2} {r['title']}",
              file=sys.stderr)
