"""
HBO Max Top 10 scraper.

Reads the platform's own numbered Top 10 off a signed-in
`play.hbomax.com`, which states the chart outright rather than
leaving it to be inferred from where tiles sit on a page.

Requires a donated SESSION, not just cookies:

    python3 scripts/trends_scrapers/donate_cookies.py --login hbomax.com

Signed out, `play.hbomax.com` bounces to
`www.hbomax.com/?reason=anonymous` and serves the full marketing site
at HTTP 200: plan cards, promotional artwork, title text. It parses.
Publishing it would put a plan picker on the board as a viewership
chart, so this scraper proves the session before it reads anything
and refuses when it cannot (see `_auth_guard`).

Where the session actually lives (measured 2026-09-23): not in
IndexedDB. HBO Max's IndexedDB on play.hbomax.com is five databases
of Amplitude and Braze analytics with no auth store at all. The
session rides on the `st` cookie scoped to `.api.hbomax.com`, which
is why the donation has to reach that host and not just `hbomax.com`.

Naming history: WBD launched "Max" (max.com) in mid-2023, then
reverted to "HBO Max" in mid-2025. The rebrand pushed the app back to
play.hbomax.com. `max.com` no longer resolves the app shell. The
scraper source key stays `max` for backwards compat with the S3
snapshot path (`trends_iq_snapshots/latest/max.json`); everything
customer-facing is HBO Max.

Note: this module is named `max_streaming.py` (not `max.py`) because
`max` shadows Python's builtin `max()` and shows up first in the
package's namespace at import time. The scraper registry in
`run_all.py` uses source key `max`.

Standalone:
    python3 -m scripts.trends_scrapers.max_streaming
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any

from . import _chart_rail_guard as _guard
from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


MAX_URLS = [
    # 2026-07 rebrand-revert: play.hbomax.com's home rail carries
    # enough tiles for the dashboard on its own (typically 25-40
    # deduped titles across Featured / Trending Now / Continue
    # Watching / Because You Watched). The pre-rebrand /pages/series
    # and /pages/movies routes now redirect-loop and are dropped.
    ('Home', 'https://play.hbomax.com/'),
]


# HBO Max ships its home rail as fully-rendered DOM tiles. Once we
# can find at least a few show / movie anchors the page is hydrated.
# Match BOTH the singular /show/ /movie/ path (currently live as of
# 2026-08-31 - reverted from the plural /shows/ /movies/ that shipped
# briefly in mid-2025) AND the plural variants, so the scraper keeps
# working if HBO Max flips the URL shape again.
_MAX_HYDRATE_SELECTORS = [
    'a[href*="/show/"]',
    'a[href*="/movie/"]',
    'a[href*="/shows/"]',
    'a[href*="/movies/"]',
]


# ────────────────────────────────────────────────────────────────────
# The Top 10 rail
# ────────────────────────────────────────────────────────────────────
# A signed-in play.hbomax.com states its chart outright. Every tile in
# a ranked rail carries its position in its own aria-label:
#
#     Number 1: Lanterns. 1 of 10
#
# That is strictly better than inferring an order from where tiles sit
# on the page, which is how our board ended up showing Lanterns at 4
# while HBO Max itself had it at 1.
#
# Three things make it easy to get wrong, and the third is the one
# that actually bites.
#
# 1. The label is wrapped in Unicode directional isolates (U+2066 to
#    U+2069). They are invisible and sit BETWEEN the words, so a
#    pattern written against what the label looks like matches
#    nothing and reports an empty chart rather than an error.
#
# 2. The rail is virtualised horizontally. The DOM only holds the few
#    tiles currently on screen, so one snapshot yields three or four
#    of the ten. It is also virtualised VERTICALLY: the ranked rails
#    are not in the DOM at all until the page has been scrolled down
#    to them.
#
# 3. "N of 10" is a POSITION WITHIN A RAIL, not a chart rank, and
#    more than one rail uses it. Measured 2026-09-23, the same home
#    page carried two complete numbered rails: "Popular TV" led by
#    Lanterns, and a second led by Supergirl, The Revenant and
#    Beetlejuice. Taking whichever rendered first published the film
#    rail as the HBO Max Top 10. It had ten rows, clean ranks and
#    real titles, and it was the wrong chart. So a rail has to be
#    identified by NAME before any of its rows are believed.
#
# Because the heading that names a rail is not an ancestor of its
# tiles, the association has to be made in document order, in the
# page. `collect_chart_rails` does that and hands back a normalised
# record of what it saw; `extract_chart` reads that record. Keeping
# the DOM walk in one place and the parsing in another is what makes
# the parsing testable without a browser.
_ISOLATES = '\u2066\u2067\u2068\u2069\u200e\u200f\u061c'
_ISOLATE_RE = re.compile(f'[{_ISOLATES}]')

# Headings whose rail is a chart, best first.
#
# HBO Max names both of its charts outright, in the aria-label of the
# rail's H2: "Top 10 Series Today" and "Top 10 Movies Today". That was
# read off the live signed-in home on 2026-09-24 and the series rail
# matches, position for position, what the app shows on screen.
#
# The CMS names that sit in those same headings' textContent are
# "Popular TV" and "Fresh Starts". They are kept at the END of this
# list, as a fallback for a layout that stops labelling its headings,
# and deliberately below every published spelling so they can never
# outrank the real name. A rail called "Fresh Starts" is exactly the
# kind of thing that should NOT be believed on its name alone, which
# is why it only counts when the accessible name is missing entirely.
_CHART_HEADINGS = (
    'top 10 series today', 'top 10 movies today',
    'top 10 in the u.s. today', 'top 10 today', 'top 10 series',
    'top 10 movies', 'top 10 shows', 'top 10 tv', 'top 10',
    'popular tv', 'popular series', 'popular shows', 'fresh starts',
)

# HBO Max publishes TWO charts and they are independent of each other:
# its top series and its top film are both #1 and neither outranks the
# other. Both ship, each tagged with its own name, the same way
# Netflix's films and series do.
_SERIES_CHART = 'top 10 series today'
_MOVIES_CHART = 'top 10 movies today'

_RAIL_RE = re.compile(
    r'<rail\s+name="([^"]*)">(.*?)</rail>', re.DOTALL | re.IGNORECASE)
_ROW_RE = re.compile(
    r'<row\s+rank="(\d{1,2})"\s+href="([^"]*)"\s+title="([^"]*)"\s*/>',
    re.IGNORECASE)


def strip_isolates(s: str) -> str:
    """Remove the invisible directional marks HBO Max wraps labels in."""
    return _ISOLATE_RE.sub('', s or '')


def _esc(s: str) -> str:
    return (s.replace('&', '&amp;').replace('"', '&quot;')
             .replace('<', '&lt;').replace('>', '&gt;'))


# The DOM walk. Collects every ranked tile on screen and attributes it
# to the nearest heading BEFORE it in document order, which is how
# these rails are actually associated with their titles.
#
# Two passes, and the order is not cosmetic. These rails are
# recycling virtual lists: they reuse a handful of tile elements and
# rewrite their contents as the view moves. Anything that forces a
# synchronous reflow part way through the walk gives the list its
# chance to recycle, and the tiles whose labels we have not read yet
# get rewritten to other content underneath us.
#
# Measured 2026-09-23. A single-pass version that read a heading's
# `innerText` before it reached the tiles returned ZERO rows while
# ten numbered labels were provably on the page at that instant,
# twice in a row and in both evaluation orders. The identical loop
# with that one read removed returned all ten. `innerText` is
# layout-dependent and forces the reflow; `textContent` is not.
#
# So: read every aria-label first, cheaply, recording each one's
# index in the static node list. Only then read the headings, with
# `textContent`. Associate afterwards by position, which is what
# actually names a rail, because a rail's heading is not an ancestor
# of its tiles.
_COLLECT_JS = r"""() => {
  const strip = s => (s || '')
      .replace(/[\u2066-\u2069\u200e\u200f\u061c]/g, '');
  const nodes = document.querySelectorAll(
      'h1,h2,h3,h4,[role="heading"],[aria-label]');

  // Pass A: attribute reads only. Nothing here forces layout, so the
  // virtual lists have no reason to recycle while we read them.
  const tiles = [];
  for (let i = 0; i < nodes.length; i++) {
    const label = strip(nodes[i].getAttribute('aria-label') || '');
    if (!label) continue;
    const m = label.match(
        /Number\s+(\d{1,2})\s*:\s*(.+?)\.\s*\d{1,2}\s+of\s+(\d{1,2})/);
    if (!m) continue;
    const a = nodes[i].closest ? nodes[i].closest('a[href]') : null;
    tiles.push({i: i, rank: +m[1], of: +m[3], title: m[2].trim(),
                href: a ? a.getAttribute('href') : ''});
  }
  if (!tiles.length) return [];

  // Pass B: headings, by position. The ACCESSIBLE NAME first, then
  // textContent, and never innerText.
  //
  // The two are different things on this page and only one of them is
  // the chart's name. The rail the app renders as "TOP 10 SERIES
  // TODAY" has textContent "Popular TV"; the one it renders as "TOP 10
  // MOVIES TODAY" has textContent "Fresh Starts". Those are internal
  // CMS names that survive into the DOM, and "Fresh Starts" describes
  // none of what is in it (The Revenant, Beetlejuice, Miss
  // Congeniality). The aria-label on the same H2 is the published
  // name, so it is what identifies the rail.
  //
  // Reading it is also cheaper than the fallback: an attribute read
  // cannot force the reflow that lets these virtual lists recycle
  // mid-walk. textContent stays as the fallback for a layout that
  // stops labelling its headings.
  const heads = [];
  for (let i = 0; i < nodes.length; i++) {
    const el = nodes[i];
    const isHead = /^H[1-4]$/.test(el.tagName)
        || el.getAttribute('role') === 'heading';
    if (!isHead) continue;
    const t = strip(el.getAttribute('aria-label') || '').trim()
        || strip(el.textContent || '').trim();
    if (t) heads.push({i: i, text: t});
  }

  return tiles.map(t => {
    let heading = '';
    for (const h of heads) { if (h.i < t.i) heading = h.text; else break; }
    return {heading: heading, rank: t.rank, of: t.of,
            title: t.title, href: t.href};
  });
}"""

# How many times to nudge the page before giving up. The ranked rails
# sit below the fold and each pass reveals a few more tiles; the loop
# stops as soon as a chart rail is complete.
_RAIL_PASSES = 20


def collect_chart_rails(page, label: str) -> str:
    """Scroll the home page and record every ranked rail it renders.

    Returns a normalised fragment, which is what `render_pages` then
    hands to the parser:

        <rail name="Popular TV">
          <row rank="1" href="/show/..." title="Lanterns"/>
          ...
        </rail>

    This is a record of what the page showed, not a synthetic page.
    Writing it down in one shape is what lets the parser be tested
    without a browser, and what keeps rail identity attached to the
    rows all the way through.
    """
    rails: dict[str, dict[int, tuple[str, str]]] = {}

    def harvest() -> None:
        try:
            rows = page.evaluate(_COLLECT_JS) or []
        except Exception as e:
            logger.debug('rail harvest failed: %s', e)
            return
        for r in rows:
            try:
                rank = int(r.get('rank') or 0)
            except (TypeError, ValueError):
                continue
            title = (r.get('title') or '').strip()
            if not (1 <= rank <= 10) or not title:
                continue
            if title.lower() in _NAV_STOPWORDS:
                continue
            rails.setdefault(r.get('heading') or '', {}).setdefault(
                rank, (title, r.get('href') or ''))

    def complete_charts() -> int:
        return sum(1 for k, v in rails.items()
                   if len(v) >= 10 and _is_chart_heading(k))

    def complete() -> bool:
        # BOTH charts, not the first one. Stopping at the first
        # complete rail is why an earlier run shipped the series chart
        # alone: 'Top 10 Series Today' renders well above 'Top 10
        # Movies Today', so the walk ended before the film chart had
        # entered the DOM at all. The scroll below also stops at the
        # foot of the page, so a service that publishes only one chart
        # does not pay for this.
        return complete_charts() >= 2

    # Scrolling alone is what works. The ranked rails sit below the
    # fold and render all ten tiles once they come into view, so
    # walking down the page is enough to see a whole chart.
    #
    # Clicking the rail's Next control is NOT a harmless addition.
    # `button[aria-label*="Next"]` matches the hero carousel's own
    # control near the top of the page, and clicking that re-renders
    # and pulls the view back, so a collector that clicks on every
    # pass never travels far enough down to reach a ranked rail at
    # all. Measured 2026-09-23: scroll plus click found nothing;
    # scroll alone found both rails complete.
    harvest()
    stuck = 0
    last_y = -1
    for _ in range(_RAIL_PASSES):
        if complete():
            break
        try:
            page.mouse.wheel(0, 1000)
        except Exception:
            break
        page.wait_for_timeout(1100)
        harvest()
        # Stop at the foot of the page rather than burning the whole
        # budget on a service that publishes one chart, or none.
        try:
            y = page.evaluate('() => Math.round(window.scrollY)')
        except Exception:
            y = last_y
        stuck = stuck + 1 if y == last_y else 0
        last_y = y
        if stuck >= 2:
            break

    # Only if a chart rail is on screen but short do we advance it by
    # hand, and only then, when the rail is the thing in view.
    if not complete() and any(_is_chart_heading(k) for k in rails):
        for _ in range(6):
            if complete():
                break
            try:
                nxt = page.query_selector('button[aria-label*="Next" i]')
                if not nxt:
                    break
                nxt.click(timeout=3000)
            except Exception:
                break
            page.wait_for_timeout(1200)
            harvest()

    parts = []
    for heading, rows in rails.items():
        parts.append(f'<rail name="{_esc(heading)}">')
        for rank in sorted(rows):
            title, href = rows[rank]
            parts.append(f'<row rank="{rank}" href="{_esc(href)}" '
                         f'title="{_esc(title)}"/>')
        parts.append('</rail>')
    logger.info("max %s: ranked rails seen -> %s", label,
                ', '.join(f'{k!r} {len(v)}/10' for k, v in rails.items())
                or 'none')
    return ''.join(parts)


def _is_chart_heading(heading: str) -> bool:
    h = (heading or '').strip().lower()
    return any(c in h for c in _CHART_HEADINGS)


def _chart_rank(heading: str) -> int:
    """Lower is a better chart. Used to pick between numbered rails."""
    h = (heading or '').strip().lower()
    for i, c in enumerate(_CHART_HEADINGS):
        if c in h:
            return i
    return len(_CHART_HEADINGS)


def extract_charts(html: str) -> list[tuple[str, list[dict]]]:
    """Every chart rail on the page, best-named first.

    Only a rail whose heading names a chart is eligible. A page full
    of ranked tiles that belong to a merchandising rail yields
    nothing, which is the point: ten clean rows from the wrong rail
    is the failure this function exists to prevent.

    Returns a list rather than one rail because HBO Max publishes a
    series chart AND a film chart, and they are separate rankings.
    Collapsing them into one would ask which of two different #1s
    outranks the other, a question neither chart answers.
    """
    out: list[tuple[tuple, str, list[dict]]] = []
    seen_names: set = set()
    for m in _RAIL_RE.finditer(html or ''):
        name = unescape(m.group(1))
        if not _is_chart_heading(name) or name in seen_names:
            continue
        rows = []
        for r in _ROW_RE.finditer(m.group(2)):
            rank = int(r.group(1))
            href = unescape(r.group(2))
            title = unescape(r.group(3))
            rows.append({
                'rank':             rank,
                'title':            title,
                'url':              (f'https://play.hbomax.com{href}'
                                     if href.startswith('/')
                                     else 'https://play.hbomax.com/'),
                'category_display': _classify_from_path(href),
                'collection':       name,
            })
        if not rows:
            continue
        seen_names.add(name)
        rows.sort(key=lambda x: x['rank'])
        out.append(((_chart_rank(name), -len(rows)), name, rows))
    out.sort(key=lambda t: t[0])
    return [(name, rows) for _score, name, rows in out]


def extract_chart(html: str) -> tuple[str, list[dict]]:
    """The single best chart rail. Kept for callers that want one."""
    charts = extract_charts(html)
    return charts[0] if charts else ('', [])


def _classify_from_path(path: str) -> str:
    if '/movie/' in path or '/movies/' in path:
        return 'Film'
    if '/show/' in path or '/shows/' in path:
        return 'TV'
    return ''


# Same nav-word blacklist we use elsewhere - after stripping isolates
# some interactive elements (Search, Menu, My Stuff) end up looking
# title-shaped.
_NAV_STOPWORDS = frozenset({
    'search', 'menu', 'my stuff', 'my list', 'home', 'browse',
    'sign in', 'log in', 'sign out', 'log out', 'account', 'settings',
    'notifications', 'help', 'downloads', 'watch now', 'sports',
    'live tv', 'main', 'browse menu', 'h b o max home', 'next title',
    'unmute preview', 'mute preview',
})






# HBO Max publishes a series chart AND a film chart, and both render
# off the same page, so one of them coming back empty is a collector
# that stopped walking rather than HBO Max publishing an empty chart.
# See `_chart_rail_guard` for why a row-count floor cannot catch this.
_EXPECTED_CHARTS = ('series', 'movies')

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/max.json'


def _previous() -> dict:
    """The last published max snapshot. Empty on any failure: a chart
    we cannot carry is reported, never invented."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        d = json.loads(s3.get_object(
            Bucket=_S3_BUCKET, Key=_S3_LATEST)['Body'].read())
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.info("max: no previous snapshot to carry from (%s)", e)
        return {}


def _render() -> list:
    # Max IP-gates non-US ranges (including Hetzner Falkenstein and any
    # residential proxy that lands outside the US). Route through the
    # IPRoyal residential proxy so we hit a US exit. This is a no-op
    # when IPROYAL_PROXY_* env vars aren't set - the scraper just tries
    # the direct route and (on Hetzner) will get a ~10KB rejection page.
    #
    # NOTE: for the proxy to reliably land US exits, the IPRoyal
    # dashboard's Country/Region dropdown must be set to
    # "United States". The default "Random" rotation gives US only
    # ~12% of the time.
    return render_pages(MAX_URLS,
                         homepage='https://www.hbomax.com/',
                         cookie_domain='hbomax.com',
                         wait_selectors=_MAX_HYDRATE_SELECTORS,
                         wait_ms=4000,
                         scroll_ms=3000,
                         hydration_wait_ms=12000,
                         use_proxy=True,
                         # Logged out, play.hbomax.com bounces to
                         # the marketing site and serves plan cards
                         # and promotional artwork at HTTP 200. That
                         # parses into tiles, so nothing here may be
                         # published without proving the session.
                         assert_signed_in='hbomax.com',
                         page_hook=collect_chart_rails)


def _charts_from(rendered: list) -> list[tuple[str, list[dict]]]:
    for _label, html in rendered or []:
        charts = extract_charts(html)
        if charts:
            return charts
    return []


def fetch() -> dict[str, Any]:
    # The platform's own ranked rail decides the order. Reading tile
    # position instead is how our board ended up putting Lanterns at 4
    # while HBO Max itself had it at 1. Ordering downstream is the
    # ranking agent's business; which titles the platform charts, and
    # in what order, is this scraper's, and the platform says so.
    charts = _charts_from(_render())

    if charts:
        rows: list[dict] = []
        for name, chart in charts:
            logger.info("max: chart rail %r -> %s", name,
                        ', '.join(f"{c['rank']} {c['title']}"
                                  for c in chart))
            rows.extend(chart)

        # One rail short. On 2026-09-25 that shipped the movies chart
        # whole and the series chart absent, and the six-row floor
        # every scraper here carries reported health, because five
        # clean movie rows are not a short read. Render once more, and
        # if the rail is still absent carry yesterday's rather than
        # publishing a half-width archive day, which does not come
        # back.
        missing = _guard.missing_rails(rows, _EXPECTED_CHARTS,
                                       key_of=_guard.kind_key)
        if missing:
            logger.info("max: %s chart absent from this render; "
                        "rendering once more", ', '.join(missing))
            rows = _guard.rerender_recovered(
                rows, [r for _n, c in _charts_from(_render()) for r in c],
                _EXPECTED_CHARTS, key_of=_guard.kind_key, label='max')
            rows, unresolved = _guard.carry_missing(
                rows, _previous().get('national'), _EXPECTED_CHARTS,
                key_of=_guard.kind_key, label='max')
        else:
            unresolved = []

        rails = sorted({str(r.get('collection') or '') for r in rows}
                       - {''})
        out = {'national': rows,
               'chart_rail': charts[0][0],
               'chart_rails': rails,
               'chart_positions': len(rows)}
        if unresolved:
            out['charts_unresolved'] = unresolved
        if any(r.get(_guard.STALE_FIELD) for r in rows):
            out['stale_from_previous'] = True
        return out

    # The pre-flight already proved the session, so reaching here
    # means the ranked rail did not render or is no longer named
    # anything we recognise as a chart. Publishing the surrounding
    # tiles would look like a chart and be a merchandising carousel,
    # which is the exact substitution this scraper exists to stop.
    raise RuntimeError(
        'HBO Max is signed in but no ranked rail with a chart heading '
        'rendered. Nothing was published, because the other rails on '
        'that page are merchandising and would read as a chart: on '
        '2026-09-23 the home page carried a second complete numbered '
        'rail of films alongside the real one. The chart tiles label '
        'themselves "Number N: Title. N of 10" under a heading in '
        f'{_CHART_HEADINGS[:3]}...; if HBO Max renamed the rail, add '
        'the new heading to _CHART_HEADINGS.')



if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('max', 'HBO Max', 'streaming', fetch)
    print(f"max: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
