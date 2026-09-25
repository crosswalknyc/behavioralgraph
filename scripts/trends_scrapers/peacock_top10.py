"""
Peacock's own Top 10 Movies and Top 10 TV charts.

`peacock.py` fills the catalog from JustWatch, which is a third party's
popularity pool and carries no Peacock ranking. Peacock publishes two
of its own, and this reads them.

Identified by SLUG, never by heading text
-----------------------------------------
The two rails render as "Movies Today" and "TV Shows Today". Searching
the markup for "Top 10" or "Most Popular" returns nothing useful,
because the words TOP 10 are artwork rather than text. What does
identify them is the container's own test id, which is the CMS path
of the rail:

    data-testid="/watch/home/top-10-tv"
    data-testid="/watch/home/top-10-movies"

That is the stable identifier and it is what this scraper matches. The
display text is not: HBO Max's two charts carry the internal CMS names
"Popular TV" and "Fresh Starts" in the very elements whose accessible
names read "Top 10 Series Today" and "Top 10 Movies Today", and a
reader that trusted the visible string called one of them editorial
and dropped it. Peacock's page config also carries `"top10Rail": true`
alongside the rails, which is a second, independent confirmation that
the feature is on for this account.

The rails are VIRTUALISED both ways: they sit below the fold and only
enter the DOM once scrolled to, and each holds only the few tiles
currently on screen. So the page is walked down and each rail is then
advanced sideways until all ten positions have been seen.

Session required. Signed out, peacocktv.com is a plan picker that
parses perfectly well into tiles, so nothing here may publish without
proving the session first (see `_auth_guard`).

Standalone:
    python3 -m scripts.trends_scrapers.peacock_top10
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from . import _chart_rail_guard as _guard
from ._base import run_scraper

logger = logging.getLogger(__name__)


HOME_URL = 'https://www.peacocktv.com/watch/home'
HOMEPAGE = 'https://www.peacocktv.com/'

# The CMS slug of each chart, and the name we record it under. The
# recorded name is what `_PUBLISHED_CHARTS` in `stream_estimates`
# matches on, so the two move together.
CHART_SLUGS = {
    '/watch/home/top-10-tv':     ('Top 10 TV Today', 'TV'),
    '/watch/home/top-10-movies': ('Top 10 Movies Today', 'Film'),
}

# Peacock publishes two charts on one page, so one of them coming back
# empty is a collector that stopped walking. The floor below counts
# rows across both and cannot see it. See `_chart_rail_guard`.
_EXPECTED_CHARTS = ('series', 'movies')

_DEPTH = 10

# Below this a capture is not worth publishing over a good one. A short
# read means the rail was caught mid-hydration, not that Peacock
# shortened its chart.
_MIN_HEALTHY = 6

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/peacock_top10.json'


# Read the rails by slug. Tiles carry their title in a nested test id;
# document order within the rail IS the ranking.
_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('[data-testid^="/watch/"]')) {
    const slug = el.getAttribute('data-testid');
    const tiles = [];
    for (const t of el.querySelectorAll('[data-testid="rail-tile"]')) {
      const ti = t.querySelector('[data-testid="title"]');
      let n = clean(ti ? ti.textContent : '');
      if (!n) n = clean(t.getAttribute('aria-label') || '');
      if (!n) { const im = t.querySelector('img[alt]'); if (im) n = clean(im.alt); }
      const a = t.querySelector('a[href]');
      if (n) tiles.push({title: n, href: a ? a.getAttribute('href') : ''});
    }
    if (tiles.length) out.push({slug: slug, tiles: tiles});
  }
  return JSON.stringify(out);
}"""


def _harvest(page, acc: dict) -> None:
    """Fold whatever is on screen into the per-slug accumulator.

    Order within a rail is kept by first-seen position, so advancing
    the rail appends the tiles that were off screen rather than
    renumbering the ones already recorded.
    """
    try:
        rails = json.loads(page.evaluate(_COLLECT_JS) or '[]')
    except Exception as e:  # noqa: BLE001
        logger.debug("peacock_top10: harvest failed: %s", e)
        return
    for rail in rails:
        slug = rail.get('slug') or ''
        if slug not in CHART_SLUGS:
            continue
        seen = acc.setdefault(slug, [])
        have = {t['title'].lower() for t in seen}
        for t in rail.get('tiles') or []:
            title = (t.get('title') or '').strip()
            if not title or title.lower() in have:
                continue
            have.add(title.lower())
            seen.append({'title': title, 'href': t.get('href') or ''})


def _complete(acc: dict) -> bool:
    return (len(acc) >= len(CHART_SLUGS)
            and all(len(v) >= _DEPTH for v in acc.values()))


def collect_charts(page, label: str) -> str:
    """Walk the page down, then walk each chart sideways."""
    acc: dict[str, list] = {}
    _harvest(page, acc)

    last_y, stuck = -1, 0
    for _ in range(26):
        if _complete(acc):
            break
        try:
            page.mouse.wheel(0, 1000)
        except Exception:
            break
        page.wait_for_timeout(1000)
        _harvest(page, acc)
        try:
            y = page.evaluate('() => Math.round(window.scrollY)')
        except Exception:
            y = last_y
        stuck = stuck + 1 if y == last_y else 0
        last_y = y
        if stuck >= 3:
            break

    # Horizontal pass. A rail holds only the tiles on screen, so the
    # last few positions of a ten-wide chart are never in the DOM until
    # the rail itself is advanced.
    for slug in list(CHART_SLUGS):
        if len(acc.get(slug, [])) >= _DEPTH:
            continue
        for _ in range(8):
            if len(acc.get(slug, [])) >= _DEPTH:
                break
            try:
                rail = page.query_selector(f'[data-testid="{slug}"]')
                if rail is None:
                    break
                rail.scroll_into_view_if_needed(timeout=3000)
                page.wait_for_timeout(600)
                nxt = rail.query_selector(
                    'button[aria-label*="next" i],[data-testid*="chevron-right"]')
                if nxt is None:
                    break
                nxt.click(timeout=3000)
            except Exception:
                break
            page.wait_for_timeout(900)
            _harvest(page, acc)

    logger.info("peacock_top10 %s: %s", label,
                ', '.join(f'{CHART_SLUGS[s][0]} {len(v)}/{_DEPTH}'
                          for s, v in acc.items()) or 'no chart rails seen')
    return json.dumps(acc)


def _rows_from(acc: dict) -> list[dict]:
    rows: list[dict] = []
    for slug, (name, kind) in CHART_SLUGS.items():
        tiles = (acc.get(slug) or [])[:_DEPTH]
        for i, t in enumerate(tiles, 1):
            href = t.get('href') or ''
            rows.append({
                'rank':             i,
                'title':            t['title'],
                'url':              (f'https://www.peacocktv.com{href}'
                                     if href.startswith('/')
                                     else HOMEPAGE),
                'category_display': kind,
                'collection':       name,
            })
    return rows


def _previous() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        return json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("peacock_top10: no previous snapshot (%s)", e)
        return {}


# The chart is folded into Peacock's OWN snapshot as well as this one,
# because everything downstream reads a service's chart out of the
# snapshot named after the service.
#
# `peacock` rewrites that file from JustWatch on the nightly run, and
# unlike Disney+ the two do not live on the same machine: the JustWatch
# pull needs no session and runs on the build box, while this needs the
# donated session and runs from the operator's laptop, which is
# normally some hours later. So the ordering holds in practice but is
# not enforced by a list the way Disney+'s and Paramount+'s are.
#
# The failure that leaves is benign and worth stating: if the JustWatch
# pull ever lands AFTER this one, Peacock renders that day with no
# chart, which is what it did before any of this existed. It does not
# render a wrong chart. The merge is idempotent, so the next run puts
# it back.
_MERGE_KEY = 'trends_iq_snapshots/latest/peacock.json'


def _merge_into_service_snapshot(rows: list[dict]) -> None:
    """Put the chart at the top of `peacock.json`."""
    if not rows:
        return
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        snap = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_MERGE_KEY)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("peacock_top10: could not read %s to merge into "
                       "(%s); the chart is still in its own snapshot",
                       _MERGE_KEY, e)
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
        logger.info("peacock_top10: merged %d chart row(s) into %s "
                    "(%d catalog rows kept of %d)", len(rows), _MERGE_KEY,
                    len(keep), len(prior))
    except Exception as e:  # noqa: BLE001
        logger.warning("peacock_top10: merge write failed (%s)", e)


def _render() -> dict:
    from ._playwright import render_pages

    rendered = render_pages(
        [('Home', HOME_URL)], homepage=HOMEPAGE,
        cookie_domain='peacocktv.com', wait_ms=8000, scroll_ms=3000,
        timeout_ms=70000, hydration_wait_ms=16000,
        assert_signed_in='peacocktv.com', page_hook=collect_charts)
    if not rendered:
        return {}
    try:
        return json.loads(rendered[0][1]) or {}
    except (TypeError, json.JSONDecodeError):
        return {}


def fetch() -> dict[str, Any]:
    acc = _render()
    rows = _rows_from(acc)
    now = datetime.now(timezone.utc).isoformat()

    # Both rails are virtualised on one page, so one of them absent is
    # a walk that ended before it entered the DOM rather than Peacock
    # publishing an empty chart. The row floor below counts across
    # both charts and reads ten movie rows as healthy while the TV
    # chart is missing. Render once more, then carry yesterday's rows
    # for whichever rail is still absent.
    unresolved: list[str] = []
    if rows and _guard.missing_rails(rows, _EXPECTED_CHARTS,
                                     key_of=_guard.kind_key):
        rows = _guard.rerender_recovered(
            rows, _rows_from(_render()), _EXPECTED_CHARTS,
            key_of=_guard.kind_key, label='peacock_top10')
        rows, unresolved = _guard.carry_missing(
            rows, _previous().get('national'), _EXPECTED_CHARTS,
            key_of=_guard.kind_key, label='peacock_top10')

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
    # positions Peacock never gave it.
    prev = _previous()
    prev_rows = prev.get('national') or []
    reason = (f'peacock_top10: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if len(prev_rows) >= _MIN_HEALTHY:
        logger.warning("peacock_top10: preserving previous capture "
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
    result = run_scraper('peacock_top10', 'Peacock Top 10', 'streaming',
                         fetch)
    rows = result.get('national') or []
    print(f"peacock_top10: {len(rows)} rows  error={result.get('error')}",
          file=sys.stderr)
    for r in rows:
        print(f"  {r['collection'][:22]:<24} #{r['rank']:>2} {r['title']}",
              file=sys.stderr)
