"""
Film-ticketing scraper - top movies on Fandango, Cinemark, AMC, Regal,
and Atom Tickets.

Aggregates the "now-playing / in-theaters" listings from the 5 biggest
US movie-ticketing platforms into a single snapshot the dashboard
renders as the Films tab. Order of movies on each source's default
browse is popularity / sales-density driven (that's how these sites
merchandise their own front page), so ranks map cleanly to "what's
selling now" on each platform.

Snapshot shape (kind='film'):

    {
      "source":     "film_ticketing",
      "kind":       "film",
      "label":      "Films",
      "fetched_at": "...",
      "national":   [...]   # mirrors fandango (biggest reach)
      "sources": {
        "fandango": {"label": "Fandango",      "items": [{...}]},
        "cinemark": {"label": "Cinemark",      "items": [{...}]},
        "amc":      {"label": "AMC Theatres",  "items": [{...}]},
        "regal":    {"label": "Regal Cinemas", "items": [{...}]},
        "atom":     {"label": "Atom Tickets",  "items": [{...}]}
      }
    }

Each items[i] has at least: {rank, title, url, image?}.

Access notes (verified 2026-07-28):

    Fandango / Cinemark   plain HTTP from a residential IP - both are
                          server-rendered, 30 titles each, poster URLs
                          included. Blocked from Hetzner (403 / bot-
                          block interstitial).
    AMC                   Akamai captcha wall from EVERY IP tested,
                          including Playwright + real Chrome +
                          stealth on a residential IP. The 4KB
                          shell body contains "blocked" x7 + a
                          captcha challenge. Donated
                          amctheatres.com cookies from a signed-in
                          laptop Chrome MIGHT bypass (a passed
                          captcha session is trusted for a while)
                          - not guaranteed.
    Regal                 Same class of Akamai bot-block as AMC.
                          Same cookie-donation escape hatch.
    Atom Tickets          Fully client-rendered React app. Its DOM
                          uses non-standard slug/anchor shapes that
                          the generic movie-list parser doesn't
                          catch on the first pass. Ships as a
                          "warming up" tile; parser needs targeted
                          work once we have Playwright + a fresh
                          hydrated HTML capture to inspect.

Because Hetzner is IP-blocked by every one of these sites, this
scraper is registered in `local_residential_run.py` (Jenna's laptop
cron), NOT in the Hetzner `run_all.py`. Same pattern as
Netflix / Disney+ / ESPN+ / Max / Hulu.

Standalone:

    python3 -m scripts.trends_scrapers.film_ticketing
    python3 -m scripts.trends_scrapers.film_ticketing --only fandango,cinemark
"""

from __future__ import annotations

import argparse
import html as _html
import logging
import re
import sys
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36')

_TIMEOUT = 25

# Skip anchor labels that show up on ticketing pages but aren't titles.
# Match case-insensitively; anything containing these substrings is
# treated as chrome (buy button, trailer, showtimes, etc.) rather than
# a real movie name.
_TITLE_SKIP_SUBSTRINGS = {
    'advance ticket', 'get tickets', 'buy tickets', 'showtimes',
    'trailer', 'watch trailer', 'coming soon', 'sr-only',
    'now playing', 'in theaters', 'imax', 'dolby', 'more info',
    'read more', 'view all', 'see all',
}


def _clean_title(raw: str) -> str:
    """Strip whitespace, HTML entities, and marketing suffixes."""
    if not raw:
        return ''
    s = _html.unescape(raw).strip()
    s = re.sub(r'\s+', ' ', s)
    # Fandango stamps `(2026)` on every title; keep it for now — the
    # frontend gets a slightly cleaner-looking chart if we strip it,
    # but keeping it disambiguates re-releases (Moana (2026) vs
    # Moana (2016)) which studios do surface separately.
    return s


def _is_title(text: str) -> bool:
    """Reject obvious non-title anchor text (Buy Tickets, Trailer, etc.)."""
    if not text or len(text) < 2 or len(text) > 100:
        return False
    lo = text.lower()
    return not any(needle in lo for needle in _TITLE_SKIP_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Fandango — Movies In Theaters (server-rendered, popularity order)
# ---------------------------------------------------------------------------
# The list order on `/movies-in-theaters` is what Fandango merchandises
# as "what to see now" — driven by their editorial + a live sales-density
# signal for the current week. Higher-anticipated / higher-selling titles
# rise to the top of the grid. Structure inside each poster-card:
#
#   <li class="poster-card poster-card__fluid browse-movielist--item ...">
#     <a href="/{slug}-{year}-{id}/movie-overview">
#       <span class="poster-card--img-wrap visual-container">
#         <img class="... poster-card--img ..." src="...">
#         ...
#       </span>
#       <span class="browse-movielist--title poster-card--title"
#             aria-hidden="true">Title (2026)</span>
#     </a>
#   </li>
_FANDANGO_URL = 'https://www.fandango.com/movies-in-theaters'
_FANDANGO_CARD_RE = re.compile(
    r'<li class="poster-card poster-card__fluid browse-movielist--item[^"]*">'
    r'(.+?)</li>',
    re.DOTALL,
)
_FANDANGO_HREF_RE  = re.compile(r'href="(/[a-z0-9-]+-\d{5,}/movie-overview)"')
_FANDANGO_TITLE_RE = re.compile(
    r'<span class="browse-movielist--title poster-card--title"'
    r'[^>]*>([^<]+)</span>'
)
_FANDANGO_IMG_RE   = re.compile(
    r'<img[^>]+class="[^"]*poster-card--img[^"]*"[^>]+src="([^"]+)"'
)


def _fetch_fandango(limit: int = 25) -> list[dict]:
    """Server-rendered — parse the poster-card grid straight from HTML.

    Returns list of {rank, title, url, image}. Silent-fail returns []
    so the snapshot still writes with the other sources.
    """
    try:
        r = requests.get(_FANDANGO_URL,
                         headers={'User-Agent': _UA,
                                  'Accept': 'text/html'},
                         timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("fandango: %s", e)
        return []
    if not r.ok:
        logger.warning("fandango: http %s", r.status_code)
        return []
    items: list[dict] = []
    for m in _FANDANGO_CARD_RE.finditer(r.text or ''):
        card = m.group(1)
        href_m  = _FANDANGO_HREF_RE.search(card)
        title_m = _FANDANGO_TITLE_RE.search(card)
        img_m   = _FANDANGO_IMG_RE.search(card)
        if not (href_m and title_m):
            continue
        title = _clean_title(title_m.group(1))
        if not _is_title(title):
            continue
        image = img_m.group(1) if img_m else ''
        # Fandango serves a `default_poster--dark-mode.png` placeholder
        # for movies missing artwork - swallow those so posters look
        # clean on the dashboard.
        if 'default_poster' in image:
            image = ''
        items.append({
            'rank':  len(items) + 1,
            'title': title,
            'url':   'https://www.fandango.com' + href_m.group(1),
            'image': image,
        })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Cinemark — Now Showing (server-rendered)
# ---------------------------------------------------------------------------
# The `/movies` page renders each movie inside a `<div class="card__movie">`.
# There are usually TWO anchors per card:
#   1. class="movie-link" containing "Advance Tickets " + <span class="sr-only">Title</span>
#   2. plain <a href="/movies/{slug}">Title</a>  (used as the poster + title link)
# We match the plain-anchor form because it's cleaner and includes the
# visible title text.
_CINEMARK_URL = 'https://www.cinemark.com/movies'
_CINEMARK_CARD_RE = re.compile(
    r'<div class="card__movie[^"]*">(.+?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
# Match ANY /movies/{slug} anchor that has visible text (not just an
# icon or "Advance Tickets" chrome), then filter by _is_title().
_CINEMARK_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(/movies/[a-z0-9-]+)"[^>]*>([^<]{2,80})</a>'
)
_CINEMARK_IMG_RE = re.compile(
    r'<img[^>]+src="(https?://[^"]*cinemark\.com/media/[^"]+)"'
)


def _fetch_cinemark(limit: int = 25) -> list[dict]:
    """Server-rendered — parse each `card__movie` block. Dedupe by slug
    (Cinemark shows both an IMAX and a standard row for the same film
    sometimes; we keep only the first occurrence)."""
    try:
        r = requests.get(_CINEMARK_URL,
                         headers={'User-Agent': _UA,
                                  'Accept': 'text/html'},
                         timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("cinemark: %s", e)
        return []
    if not r.ok:
        logger.warning("cinemark: http %s", r.status_code)
        return []
    seen_slugs: set[str] = set()
    items: list[dict] = []
    text = r.text or ''
    # Cinemark occasionally wraps cards with nested divs, so the block
    # regex above misses some. Fall back to scanning every /movies/
    # anchor and grouping by slug.
    for m in _CINEMARK_ANCHOR_RE.finditer(text):
        slug  = m.group(1).rsplit('/', 1)[-1]
        title = _clean_title(m.group(2))
        if slug in seen_slugs:
            continue
        if not _is_title(title):
            continue
        # Look for the nearest image URL in a ~1500-char window around
        # the anchor. Cinemark's poster imgs live either just before
        # or inside the same card as the anchor.
        window_start = max(0, m.start() - 1500)
        window       = text[window_start:m.end() + 500]
        img_m        = _CINEMARK_IMG_RE.search(window)
        seen_slugs.add(slug)
        items.append({
            'rank':  len(items) + 1,
            'title': title,
            'url':   'https://www.cinemark.com' + m.group(1),
            'image': (img_m.group(1) if img_m else ''),
        })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Playwright helper for AMC / Regal / Atom Tickets
# ---------------------------------------------------------------------------
# These three all bot-block or client-render, so plain HTTP returns
# either a 403 shell or a hydration-empty React skeleton. We drive
# real Chrome via the shared Playwright helper and parse the resulting
# rendered HTML.
#
# Same parsing shape across all three: after hydration the page
# exposes `<a href="/movies/{slug}">Title</a>` anchors (or a very
# close variant), each associated with a poster <img>. Dedupe by
# slug, keep the first occurrence (which is always the top card in
# the popularity-ordered grid).

def _playwright_render(url: str, homepage: str,
                        wait_selectors: list[str],
                        cookie_domain: Optional[str] = None) -> str:
    """Return rendered HTML for `url` or '' on any failure. Wraps the
    shared `render_pages` helper so each site's parser stays a pure
    function of the HTML.

    `cookie_domain` is optional - AMC and Regal both Akamai-block
    anonymous Playwright sessions, and donated cookies from a signed-in
    Chrome session may (but aren't guaranteed to) bypass the challenge.
    Missing cookies logs a warning and the challenge page comes back
    as a 4KB shell that render_pages filters out."""
    try:
        from ._playwright import render_pages
    except Exception as e:
        logger.info("playwright helper unavailable: %s", e)
        return ''
    pages = render_pages(
        [(url.split('//', 1)[-1], url)],
        homepage=homepage,
        cookie_domain=cookie_domain,
        wait_selectors=wait_selectors,
        hydration_wait_ms=15000,
        wait_ms=4500,
    )
    if not pages:
        return ''
    _label, html = pages[0]
    return html or ''


# Copy in the "cookie donation to bypass" message so all three
# Akamai-walled sources ship the same operator guidance. Kept here as
# a template because each source substitutes its own domain.
_COOKIE_DONATION_HINT = (
    'Log into {site} once in your laptop Chrome, then run '
    '`python3 scripts/trends_scrapers/donate_cookies.py {domain}` '
    'so the daily scrape can carry that signed-in session past the '
    'bot-block.'
)


def _parse_generic_movie_list(html: str, host_prefix: str,
                                title_slug_prefix: str = '/movies/',
                                limit: int = 25) -> list[dict]:
    """Extract `<a href="{title_slug_prefix}{slug}">Title</a>` anchors and
    associate each with the nearest <img> src within a ~2KB window.
    Filters out obvious chrome anchors (Trailer, Buy, etc.) via
    _is_title(). Dedupe by slug."""
    if not html:
        return []
    # Match anchor with visible text OR anchor containing <img alt="Title">
    anchor_re = re.compile(
        r'<a[^>]+href="(' + re.escape(title_slug_prefix) + r'[a-z0-9][a-z0-9-]{2,60})"'
        r'[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    inner_text_re  = re.compile(r'>\s*([^<>]{2,80})\s*<', re.DOTALL)
    img_alt_re     = re.compile(r'<img[^>]+alt="([^"]{2,80})"')
    img_src_re     = re.compile(
        r'<img[^>]+(?:src|data-src|srcset)="([^" ]+\.(?:jpg|jpeg|png|webp)[^"]*)"'
    )

    seen_slugs: set[str] = set()
    items: list[dict] = []
    for m in anchor_re.finditer(html):
        slug = m.group(1).rsplit('/', 1)[-1]
        if slug in seen_slugs:
            continue
        inner = m.group(2)
        # Preferred title source: <img alt="..."> inside the anchor
        # (React sites almost always set alt for accessibility).
        title = ''
        alt_m = img_alt_re.search(inner)
        if alt_m:
            title = _clean_title(alt_m.group(1))
        if not _is_title(title):
            # Fall back to any visible text inside the anchor.
            for tm in inner_text_re.finditer(inner):
                cand = _clean_title(tm.group(1))
                if _is_title(cand):
                    title = cand
                    break
        if not _is_title(title):
            continue
        # Image: try inside the anchor first, then a ~2KB window after.
        img_m = img_src_re.search(inner)
        if not img_m:
            after = html[m.end():m.end() + 2000]
            img_m = img_src_re.search(after)
        if not img_m:
            before = html[max(0, m.start() - 2000):m.start()]
            img_m = img_src_re.search(before)
        image = img_m.group(1) if img_m else ''
        # Some sites (AMC) inline srcset — take the first URL.
        if ' ' in image:
            image = image.split(' ', 1)[0]
        seen_slugs.add(slug)
        items.append({
            'rank':  len(items) + 1,
            'title': title,
            'url':   host_prefix + m.group(1),
            'image': image,
        })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# AMC Theatres — Now Playing
# ---------------------------------------------------------------------------
# `www.amctheatres.com/movies` returns 403 to plain requests (Akamai
# / DataDome-style block). Real Chrome via Playwright + stealth passes.
_AMC_URL      = 'https://www.amctheatres.com/movies'
_AMC_HOMEPAGE = 'https://www.amctheatres.com/'


def _fetch_amc(limit: int = 25) -> tuple[list[dict], str]:
    html = _playwright_render(
        _AMC_URL, _AMC_HOMEPAGE,
        wait_selectors=[
            'a[href^="/movies/"]',
            '[data-testid*="movie"]',
            '.MovieCard, .movie-card',
        ],
        cookie_domain='amctheatres.com',
    )
    if not html:
        return [], _COOKIE_DONATION_HINT.format(
            site='amctheatres.com', domain='amctheatres.com')
    items = _parse_generic_movie_list(
        html, 'https://www.amctheatres.com',
        title_slug_prefix='/movies/', limit=limit)
    if items:
        return items, ''
    return [], _COOKIE_DONATION_HINT.format(
        site='amctheatres.com', domain='amctheatres.com')


# ---------------------------------------------------------------------------
# Regal Cinemas — All Movies in Theatres
# ---------------------------------------------------------------------------
# 403 on plain requests. Playwright + stealth passes. Regal's slug prefix
# is `/movies/` same as AMC.
_REGAL_URL      = 'https://www.regmovies.com/movies/all-movies-in-theatres'
_REGAL_HOMEPAGE = 'https://www.regmovies.com/'


def _fetch_regal(limit: int = 25) -> tuple[list[dict], str]:
    html = _playwright_render(
        _REGAL_URL, _REGAL_HOMEPAGE,
        wait_selectors=[
            'a[href^="/movies/"]',
            '[class*="MovieTile"], [class*="movie-tile"]',
        ],
        cookie_domain='regmovies.com',
    )
    if not html:
        return [], _COOKIE_DONATION_HINT.format(
            site='regmovies.com', domain='regmovies.com')
    items = _parse_generic_movie_list(
        html, 'https://www.regmovies.com',
        title_slug_prefix='/movies/', limit=limit)
    if items:
        return items, ''
    return [], _COOKIE_DONATION_HINT.format(
        site='regmovies.com', domain='regmovies.com')


# ---------------------------------------------------------------------------
# Atom Tickets — Now In Theaters
# ---------------------------------------------------------------------------
# Fully client-rendered React app. The homepage lists top movies in a
# hero carousel + a "Now in Theaters" grid; the standalone browse URL
# path shifted several times in 2025-2026 so we hit the homepage
# (stable) and pick titles off there.
_ATOM_URL      = 'https://www.atomtickets.com/'
_ATOM_HOMEPAGE = 'https://www.atomtickets.com/'


def _fetch_atom(limit: int = 25) -> tuple[list[dict], str]:
    html = _playwright_render(
        _ATOM_URL, _ATOM_HOMEPAGE,
        wait_selectors=[
            'a[href^="/movies/"]',
            '[data-csm*="Movie"], [class*="MovieTile"]',
        ],
    )
    if not html:
        return [], 'Warming up.'
    items = _parse_generic_movie_list(
        html, 'https://www.atomtickets.com',
        title_slug_prefix='/movies/', limit=limit)
    if items:
        return items, ''
    # Atom hydrates fine but uses a non-standard DOM shape the generic
    # anchor parser doesn't catch. Left as an operator-facing note
    # rather than a cookie-donation ask because cookies won't help.
    return [], ('Atom Tickets parser needs a targeted pass - their '
                'React DOM uses non-standard slug shapes. Fandango + '
                'Cinemark cover the same theatrical signal for now.')


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def fetch(only: Optional[set[str]] = None) -> dict[str, Any]:
    """Pull all sources sequentially. Playwright-driven sources are the
    slowest (30-60s per source with warm-up) but they run once per day
    from the cron so total wall time is bounded.

    `only` is an optional set of source keys to include; if unset, all
    sources run. Useful for standalone iteration during scraper dev
    (`--only fandango,cinemark` to skip the Playwright hop).
    """
    def _wanted(k: str) -> bool:
        return not only or k in only

    fandango_items = _fetch_fandango(limit=30) if _wanted('fandango') else []
    cinemark_items = _fetch_cinemark(limit=30) if _wanted('cinemark') else []
    amc_items, amc_sub     = (_fetch_amc(limit=25)   if _wanted('amc')   else ([], ''))
    regal_items, regal_sub = (_fetch_regal(limit=25) if _wanted('regal') else ([], ''))
    atom_items, atom_sub   = (_fetch_atom(limit=25)  if _wanted('atom')  else ([], ''))

    return {
        # Mirror Fandango as the "national" list because it has the
        # broadest US theater reach (~40% of US ticketing volume).
        'national': fandango_items[:30] or cinemark_items[:30],
        'available': bool(fandango_items or cinemark_items or amc_items
                          or regal_items or atom_items),
        'sources': {
            'fandango': {
                'label':     'Fandango',
                'sub':       'What people are buying tickets to right now on Fandango.',
                'items':     fandango_items,
                'available': bool(fandango_items),
            },
            'cinemark': {
                'label':     'Cinemark',
                'sub':       "Cinemark's Now Showing lineup, ordered by their front-page merchandising.",
                'items':     cinemark_items,
                'available': bool(cinemark_items),
            },
            'amc': {
                'label':     'AMC Theatres',
                'sub':       (amc_sub or "AMC's Now Playing lineup at the biggest US theater chain."),
                'items':     amc_items,
                'available': bool(amc_items),
            },
            'regal': {
                'label':     'Regal Cinemas',
                'sub':       (regal_sub or "Regal's in-theaters lineup, second-largest US chain."),
                'items':     regal_items,
                'available': bool(regal_items),
            },
            'atom': {
                'label':     'Atom Tickets',
                'sub':       (atom_sub or 'The mobile-first ticketing platform with strong younger-audience reach.'),
                'items':     atom_items,
                'available': bool(atom_items),
            },
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    p = argparse.ArgumentParser(description='Film-ticketing scraper')
    p.add_argument('--only', default='',
                    help='comma-separated source keys: '
                          'fandango,cinemark,amc,regal,atom')
    p.add_argument('--dry-run', action='store_true',
                    help='print results without writing an S3 snapshot')
    args = p.parse_args()

    only_set = {s.strip() for s in args.only.split(',') if s.strip()} or None

    if args.dry_run:
        result = fetch(only=only_set)
    else:
        from ._base import run_scraper
        # run_scraper doesn't take extra kwargs; wrap fetch() so the
        # standard entrypoint still calls it with no args when the
        # cron invokes this module.
        def _fetch_wrapped():  # pragma: no cover
            return fetch(only=only_set)
        result = run_scraper('film_ticketing', 'Films', 'film', _fetch_wrapped)

    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        items = panel.get('items') or []
        print(f"{slug}: n={len(items)}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in items[:5]:
            print(f"   #{it['rank']} {it['title']}", file=sys.stderr)
