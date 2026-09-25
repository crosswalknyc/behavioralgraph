"""
Disney+'s own Top 10 Movies and Top 10 Series, for the US.

`disneyplus.py` fills the catalog from the brand hub pages
(/browse/disney, /browse/marvel, /browse/star-wars, /browse/pixar,
/browse/national-geographic). Those are merchandising shelves: on
2026-09-24 they put Mickey+ Shorts, Play Break and a Mickey Mouse
Halloween special at the top of the Disney+ rail, ordered by where a
tile sat on a brand hub. Disney+ publishes two real charts and this
reads them.

They are only there when you are signed in
------------------------------------------
Signed out, disneyplus.com is a sign-in wall, so the charts were
invisible for as long as the donated session was lapsed and the
service read as chartless. It is not. The signed-in US home carries
fifty rails and two of them are:

    Top 10 Movies in the US Today
    Top 10 Series in the US Today

Disney+ names them outright, which is the easy case. Nothing here
relies on that: the rails are matched on a heading that STARTS with
'top 10', and each tile's rank is read from its own aria-label rather
than from where it sits, so a reordered or partly rendered rail
cannot silently renumber the chart.

The tile
--------
    <a data-testid="set-item"
       aria-label="Number 1 New Movie Badge Toy Story 5 Rated PG
                   Released 2026. ... Select for details on this title.">
      <img data-testid="poster-vertical-title-art" alt="Toy Story 5">

The aria-label carries the rank but wraps the title in badges, a
rating and a genre list, so the title is taken from the poster art's
alt text instead, which is exactly the title and nothing else.

Why this reads as viewing
-------------------------
The films chart on 2026-09-24 was Toy Story 5 at 1 followed by Toy
Story, Toy Story 2, 3 and 4 at 2 to 5. A franchise sequel pulling its
own back catalogue up behind it is what an audience does, not what a
merchandiser does, and the same shape showed on Peacock (three
Twilight films) and Pluto TV (four John Wick films). The series chart
mixes a new FX drama, an ABC procedural, Dancing with the Stars and a
FOX medical show, which coheres around nothing.

Standalone:
    python3 -m scripts.trends_scrapers.disneyplus_top10
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from . import _chart_rail_guard as _guard
from ._base import run_scraper

logger = logging.getLogger(__name__)


HOME_URL = 'https://www.disneyplus.com/home'
HOMEPAGE = 'https://www.disneyplus.com/'

# Rail name -> the kind everything in it is. Matched on a heading that
# starts with 'top 10' so a wording change ('Top 10 Movies in the US'
# without 'Today') still lands, while a rail merely CONTAINING the
# words cannot qualify.
_MOVIES = 'Top 10 Movies in the US Today'
_SERIES = 'Top 10 Series in the US Today'

_DEPTH = 10
_MIN_HEALTHY = 6

# Disney+ publishes two charts on one page, so one of them coming back
# empty is a collector that stopped walking. The floor above counts
# rows across both and cannot see it. See `_chart_rail_guard`.
_EXPECTED_CHARTS = ('series', 'movies')

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/disneyplus_top10.json'
_MERGE_KEY = 'trends_iq_snapshots/latest/disneyplus.json'

_RANK_RE = re.compile(r'\bnumber\s+(\d{1,2})\b', re.I)


_COLLECT_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const h of document.querySelectorAll('h1,h2,h3,h4,[role="heading"]')) {
    const name = clean(h.getAttribute('aria-label') || '')
              || clean(h.textContent || '');
    if (!/^top\s*10\b/i.test(name)) continue;
    let node = h, items = [];
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      items = [...node.querySelectorAll('a[data-testid="set-item"]')];
      if (items.length >= 3) break;
    }
    // The title comes from the poster art's alt, which is the title
    // and nothing else. The test id sits on a wrapper on some tiles
    // and on the image itself on others, so try both, then fall back
    // to the first alt on the tile that is not a content rating.
    const RATING = /^(?:G|PG|PG-13|R|NC-17|NR|TV-Y7?|TV-G|TV-PG|TV-14|TV-MA)$/i;
    const titleOf = a => {
      const art = a.querySelector('[data-testid="poster-vertical-title-art"]');
      if (art) {
        const alt = clean(art.getAttribute('alt') || '');
        if (alt) return alt;
        const im = art.querySelector('img[alt]');
        if (im && clean(im.alt)) return clean(im.alt);
      }
      for (const im of a.querySelectorAll('img[alt]')) {
        const alt = clean(im.alt);
        if (alt && !RATING.test(alt)) return alt;
      }
      return '';
    };
    const rows = [];
    for (const a of items) {
      rows.push({aria: clean(a.getAttribute('aria-label') || ''),
                 title: titleOf(a),
                 href: a.getAttribute('href') || ''});
    }
    if (rows.length) out.push({rail: name, rows: rows});
  }
  return JSON.stringify(out);
}"""


def _hook(page, label):
    for _ in range(18):
        try:
            page.mouse.wheel(0, 1100)
        except Exception:
            break
        page.wait_for_timeout(950)
    try:
        return page.evaluate(_COLLECT_JS)
    except Exception as e:  # noqa: BLE001
        logger.info("disneyplus_top10 %s: rail read failed (%s)", label, e)
        return '[]'


def _kind_for(rail: str) -> str:
    r = (rail or '').lower()
    if 'movie' in r or 'film' in r:
        return 'Film'
    if 'series' in r or 'show' in r or ' tv' in r:
        return 'TV'
    return ''


def extract(blob: str) -> list[dict]:
    """Chart rows, with each tile's own rank rather than its position.

    A tile whose aria-label carries no rank is skipped rather than
    counted: a rail that half rendered would otherwise renumber the
    chart from 1 and publish an order Disney+ never gave.
    """
    try:
        rails = json.loads(blob or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for rail in rails:
        name = (rail.get('rail') or '').strip()
        kind = _kind_for(name)
        seen: set[int] = set()
        for row in rail.get('rows') or []:
            m = _RANK_RE.search(row.get('aria') or '')
            title = (row.get('title') or '').strip()
            if not m or not title:
                continue
            rank = int(m.group(1))
            if not (1 <= rank <= _DEPTH) or rank in seen:
                continue
            seen.add(rank)
            href = row.get('href') or ''
            out.append({
                'rank':             rank,
                'title':            title,
                'url':              (f'https://www.disneyplus.com{href}'
                                     if href.startswith('/') else HOMEPAGE),
                'category_display': kind,
                'collection':       name,
            })
        logger.info("disneyplus_top10: %r -> %d of %d position(s)",
                    name, len(seen), _DEPTH)
    out.sort(key=lambda r: (r['collection'], r['rank']))
    return out


def _previous() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        return json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.info("disneyplus_top10: no previous snapshot (%s)", e)
        return {}


def _merge_into_service_snapshot(rows: list[dict]) -> None:
    """Put the chart at the top of `disneyplus.json`.

    Everything downstream reads a service's chart out of the snapshot
    named after the service, so the chart has to be in that file.
    Ordering matters: `disneyplus` rewrites it from the brand hubs
    every night, so this runs after it. The merge is idempotent,
    keyed by chart name and by title.
    """
    if not rows:
        return
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        snap = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_MERGE_KEY)['Body'].read())
    except Exception as e:  # noqa: BLE001
        logger.warning("disneyplus_top10: could not read %s to merge into "
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
        logger.info("disneyplus_top10: merged %d chart row(s) into %s "
                    "(%d catalog rows kept of %d)", len(rows), _MERGE_KEY,
                    len(keep), len(prior))
    except Exception as e:  # noqa: BLE001
        logger.warning("disneyplus_top10: merge write failed (%s)", e)


def _render() -> list:
    from ._playwright import render_pages

    return render_pages(
        [('home', HOME_URL)], homepage=HOMEPAGE,
        cookie_domain='disneyplus.com', wait_ms=8000, scroll_ms=3000,
        timeout_ms=70000, hydration_wait_ms=16000,
        assert_signed_in='disneyplus.com', page_hook=_hook)


def fetch() -> dict[str, Any]:
    rendered = _render()
    rows = extract(rendered[0][1]) if rendered else []

    # Both charts render off the same page, so one of them absent is a
    # walk that stopped early rather than Disney+ publishing an empty
    # chart. The row-count floor below cannot see it: ten clean movie
    # rows clear a six-row floor while the series chart is missing
    # entirely. Render once more, then carry yesterday's rows for the
    # rail that is still absent.
    unresolved: list[str] = []
    if rows and _guard.missing_rails(rows, _EXPECTED_CHARTS,
                                     key_of=_guard.kind_key):
        retry = _render()
        rows = _guard.rerender_recovered(
            rows, extract(retry[0][1]) if retry else [],
            _EXPECTED_CHARTS, key_of=_guard.kind_key,
            label='disneyplus_top10')
        rows, unresolved = _guard.carry_missing(
            rows, _previous().get('national'), _EXPECTED_CHARTS,
            key_of=_guard.kind_key, label='disneyplus_top10',
            archive_source='disneyplus_top10')
        rows.sort(key=lambda r: (r['collection'], r['rank']))

    if len(rows) >= _MIN_HEALTHY:
        _merge_into_service_snapshot(rows)
        out = {'national': rows,
               'chart_captured_at': datetime.now(timezone.utc).isoformat(),
               'chart_rails': sorted({r['collection'] for r in rows}),
               'chart_positions': len(rows)}
        if unresolved:
            out['charts_unresolved'] = unresolved
        if any(r.get(_guard.STALE_FIELD) for r in rows):
            out['stale_from_previous'] = True
        return out

    prev = _previous()
    prev_rows = prev.get('national') or []
    reason = (f'disneyplus_top10: read {len(rows)} row(s), below the '
              f'{_MIN_HEALTHY}-row health floor')
    logger.warning(reason)
    if len(prev_rows) >= _MIN_HEALTHY:
        logger.warning("disneyplus_top10: preserving previous capture "
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
    result = run_scraper('disneyplus_top10', 'Disney+ Top 10',
                         'streaming', fetch)
    rows = result.get('national') or []
    print(f"disneyplus_top10: {len(rows)} rows  "
          f"error={result.get('error')}", file=sys.stderr)
    for r in rows:
        print(f"  {r['collection'][:30]:<32} #{r['rank']:>2} {r['title']}",
              file=sys.stderr)
