"""
Film-ticketing scraper - top movies on Fandango, Cinemark, AMC, and Regal.

Aggregates the "now-playing / in-theaters" listings from the 4 biggest
US movie-ticketing platforms into a single snapshot the dashboard
renders as the Films tab. Order of movies on each source's default
browse is popularity / sales-density driven (that's how these sites
merchandise their own front page), so ranks map cleanly to "what's
selling now" on each platform.

Atom Tickets was previously in this list but was removed 2026-07-29
per Jenna: their React DOM uses non-standard slug shapes the generic
parser doesn't catch, and Fandango + Cinemark cover the same
theatrical signal at higher fidelity.

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
        "regal":    {"label": "Regal Cinemas", "items": [{...}]}
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
    'now playing', 'now showing', 'in theaters', 'in theatres',
    'upcoming', 'imax', 'dolby', 'more info',
    'read more', 'view all', 'see all', 'sign in', 'log in',
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

# Variant-suffix patterns Fandango tacks onto pre-release / special-
# screening cards. When we see one of these in a grid card title, we
# strip the suffix and look up the CANONICAL title's /movie-overview
# anchor elsewhere on the same page. This is how a card like
# `Spider-Man: Brand New Day Amazon Prime Early Access Screenings (2026)`
# gets normalized back to `Spider-Man: Brand New Day (2026)` while
# preserving the merchandising signal (Fandango puts the variant card
# in the grid because that's the on-sale ticket, but Cinemark shows
# the canonical title - normalizing lets us match across sources).
_FANDANGO_VARIANT_SUFFIXES = (
    ' amazon prime early access screenings',
    ' amazon prime early access',
    ' dolby opening night fan event',
    ' dolby opening night',
    ' opening night fan event',
    ' sensory friendly screening',
    ' sensory friendly',
    ' open caption',
    ' subtitled',
    ' fan event',
    ' early access',
    ' imax opening night',
    ' private theatre rental',
)


def _slugify_title(t: str) -> str:
    """Normalize a title for cross-source matching."""
    s = re.sub(r'\s*\(\d{4}\)\s*$', '', (t or '').strip())
    s = re.sub(r'[^\w\s]+', ' ', s.lower())
    return ' '.join(s.split())


def _fandango_canonicalize_title(title: str) -> str:
    """Given a Fandango grid title like `Spider-Man: Brand New Day
    Amazon Prime Early Access Screenings (2026)`, strip any known
    variant suffix and return the canonical `Spider-Man: Brand New
    Day (2026)`. Idempotent - titles without a variant suffix pass
    through untouched."""
    if not title:
        return title
    # Detect + strip the trailing "(YYYY)" so we can compare against
    # the variant list on the bare title, then re-attach.
    m_year = re.search(r'\s*\((\d{4})\)\s*$', title)
    year = ''
    core = title
    if m_year:
        year = m_year.group(0)
        core = title[:m_year.start()]
    lo = core.lower()
    for suf in _FANDANGO_VARIANT_SUFFIXES:
        if lo.endswith(suf):
            core = core[:-len(suf)].rstrip(' :-')
            break
    return (core + year).strip()


def _fandango_lookup_canonical(text: str,
                                canonical_title: str,
                                fallback_href: str) -> tuple[str, str]:
    """Search the full page HTML for a `/movie-overview` anchor whose
    slug matches the canonical title (i.e. the slug does NOT contain
    any known variant suffix). Returns (url, image_url) or falls back
    to `fallback_href` (the variant's own anchor) plus '' image."""
    slug = _slugify_title(canonical_title)
    if not slug:
        return ('https://www.fandango.com' + fallback_href, '')
    tokens = [t for t in slug.split() if len(t) >= 3]
    if not tokens:
        return ('https://www.fandango.com' + fallback_href, '')
    # Skip anchors whose slug contains ANY variant suffix keyword.
    variant_kw = ('early-access', 'fan-event', 'opening-night',
                  'sensory-friendly', 'open-caption', 'subtitled',
                  'private-theatre', 'dolby-opening')
    anchor_re = re.compile(r'href="(/([a-z0-9-]+)-\d{5,}/movie-overview)"')
    for m in anchor_re.finditer(text):
        href     = m.group(1)
        slug_txt = m.group(2)
        if any(kw in slug_txt for kw in variant_kw):
            continue
        if not all(t in slug_txt for t in tokens):
            continue
        # Grab the poster img in the surrounding 3KB window.
        img_m = re.search(
            r'src="([^"]*images\.fandango\.com/[^"]+)"',
            text[max(0, m.start() - 3000):m.start()],
        )
        image = img_m.group(1) if img_m else ''
        if 'default_poster' in image and '/images/MasterRepository/' not in image:
            image = ''
        return 'https://www.fandango.com' + href, image
    return ('https://www.fandango.com' + fallback_href, '')


def _fetch_fandango(limit: int = 25) -> list[dict]:
    """Server-rendered - parse the poster-card grid, then normalize any
    variant-suffix cards back to their canonical title so pre-release
    marquee films like `Spider-Man: Brand New Day` render as the base
    title (not `... Amazon Prime Early Access Screenings`).

    Fandango's `/movies-in-theaters` grid is sales-density ordered but
    surfaces the on-sale variant of pre-release films (early-access,
    fan-event, dolby-opening-night) rather than the canonical entry.
    We keep the grid order (which is Fandango's editorial ranking) and
    just rewrite each variant row to point at the canonical /movie-
    overview URL + poster. The end result matches Cinemark/AMC/Regal's
    naming so downstream fusion + display stays clean.

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

    text = r.text or ''
    items: list[dict] = []
    seen_canonical: set[str] = set()
    for m in _FANDANGO_CARD_RE.finditer(text):
        card = m.group(1)
        href_m  = _FANDANGO_HREF_RE.search(card)
        title_m = _FANDANGO_TITLE_RE.search(card)
        img_m   = _FANDANGO_IMG_RE.search(card)
        if not (href_m and title_m):
            continue
        raw_title  = _clean_title(title_m.group(1))
        if not _is_title(raw_title):
            continue
        canon_title = _fandango_canonicalize_title(raw_title)
        canon_key   = _slugify_title(canon_title)
        # Dedupe: a canonical title may appear multiple times if
        # Fandango lists both the variant AND the canonical grid row.
        if canon_key in seen_canonical:
            continue
        seen_canonical.add(canon_key)

        if canon_title != raw_title:
            # Variant row - look up canonical URL + poster.
            url, image = _fandango_lookup_canonical(
                text, canon_title, href_m.group(1))
        else:
            url   = 'https://www.fandango.com' + href_m.group(1)
            image = img_m.group(1) if img_m else ''
            if 'default_poster' in image and '/images/MasterRepository/' not in image:
                image = ''

        items.append({
            'rank':  len(items) + 1,
            'title': canon_title,
            'url':   url,
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
# User rule 2026-07-29: NEVER surface operator-facing text to the
# dashboard. When a bot-walled source can't be scraped, show a neutral
# "warming up" line and let `cookie_gap_notify.notify_cookie_gap()`
# handle the offline re-donation ask via SES to jenna+jessie (deduped
# to one email per source/domain per day).
_WARMING_UP_HINT = 'Warming up. Check back later.'


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    """Fire the operator-facing SES notification. Best-effort; never
    raises. Called from any fetcher that returns 0 items because the
    donated cookie session is missing or has been rejected by the
    site. The dashboard tile only ever sees `_WARMING_UP_HINT`."""
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                     source, domain, e)


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
# `www.amctheatres.com/movies` returns 403 to plain requests and returns
# a 4KB Akamai bot-challenge shell body to Playwright regardless of
# `--headless=new` + stealth + donated session cookies. Turns out
# Akamai on AMC only checks TLS handshake fingerprint (JA3/JA4), so a
# curl_cffi request that impersonates real Chrome's TLS stack passes
# through and returns the full ~1MB React SSR HTML (verified
# 2026-07-29 with impersonate='chrome131').
#
# The SSR HTML doesn't embed a structured movie list JSON we can json-
# parse. Instead each movie is referenced 6-16 times across the page
# (hero, carousel, filter chips, etc.) via `/movies/<slug>-<numeric-id>`
# anchor hrefs. We treat the reference count as a merchandising signal:
# the more times a slug is repeated in the SSR, the more prominent
# AMC's home page is pushing that title. Sort by count descending and
# take the top N. Slug -> title is the trailing "-<digits>" strip
# plus a title-case normalization.
#
# Slugs to drop: 'uxrow' (React chrome / grid layout token that
# happens to match /movies/<slug>) and anything that looks like an
# event / anniversary / fan-event / special screening (those live on
# the same page but under a separate rail).
_AMC_URL      = 'https://www.amctheatres.com/movies'
_AMC_HOMEPAGE = 'https://www.amctheatres.com/'

# Anchor pattern for AMC slugs. Numeric-id suffix is required so we
# don't match `/movies/uxrow` (chrome) or `/movies/now-playing`
# (rail nav).
_AMC_SLUG_RE = re.compile(r'/movies/([a-z0-9][a-z0-9\-]{2,120}-\d+)')

# Slug fragments that indicate an event / fan-event / anniversary
# screening / duplicate-format tile rather than a regular "Now
# Playing" title. Matched anywhere in the slug (case sensitive - AMC
# slugs are always lowercase).
_AMC_EVENT_TOKENS = (
    '-fan-event', '-opening-night', '-anniversary-', 'anniversary-studio',
    '-meet-up-', 'ghibli-fest', 'private-theatre-rental', 'dci-2026',
    'dci-20', '-early-access', 'big-loud-and-live', 'singles-opening',
    'grateful-dead', 'wnba-', '-nba-', 'mlb-', '-live-in-theaters',
    # Duplicate-format tiles that echo an already-listed mainstream
    # title (sensory-friendly, open-caption, subtitled, etc.) - these
    # crowd out real titles from the top-25.
    'sensory-friendly', '-open-caption', '-subtitled', '-with-subs',
    '-dubbed-', '-in-imax', '-in-dolby', '-scream-unseen',
    # Concert / theater simulcasts and one-off celebration days.
    'the-musical-live', '-day-2026', 'texas-chainsaw-day', 'katseye',
)


# Known franchise / compound-word title fixes for AMC slug-to-title.
# AMC's slugs lose hyphens and colons that appear in the real title
# (e.g. `spider-man-brand-new-day` should display as
# `Spider-Man: Brand New Day` to match Cinemark/Fandango/Regal).
# Left side = slug prefix (all-lowercase, hyphen-separated), right
# side = corresponding proper title prefix. Match longest first.
_AMC_TITLE_FIXUPS = (
    ('spider-man-brand-new-day',      'Spider-Man: Brand New Day'),
    ('spider-man',                    'Spider-Man'),
    ('star-wars-the-mandalorian-and-grogu', 'Star Wars: The Mandalorian and Grogu'),
    ('star-wars',                     'Star Wars'),
    ('mission-impossible',            'Mission: Impossible'),
    ('avengers-doomsday',             'Avengers: Doomsday'),
    ('minions-monsters',              'Minions & Monsters'),
    ('bad-guys',                      'Bad Guys'),
    ('paw-patrol',                    'PAW Patrol'),
    ('hadestown-the-musical',         'Hadestown: The Musical'),
    ('toy-story',                     'Toy Story'),
)


def _amc_slug_to_title(slug: str) -> str:
    """AMC slug: `the-odyssey-76238` -> `The Odyssey`.
    Preserves sequel numbers: `toy-story-5-72482` -> `Toy Story 5`,
    `the-bad-guys-2-83811` -> `The Bad Guys 2`.
    Restores hyphen + colon punctuation for known franchise titles via
    `_AMC_TITLE_FIXUPS` so display matches Cinemark/Fandango/Regal.

    Strategy: strip trailing numeric AMC-id, check the fixups table
    for a longest-prefix match, then title-case any remaining tail.
    Fixups table is longest-first so `spider-man-brand-new-day` beats
    the shorter `spider-man` match.
    """
    # Drop trailing numeric AMC id.
    slug_no_id = re.sub(r'-\d+$', '', slug).strip('-')
    if not slug_no_id:
        return ''

    # Try longest-prefix match against the fixups table.
    for prefix, proper in sorted(_AMC_TITLE_FIXUPS,
                                  key=lambda p: -len(p[0])):
        if slug_no_id == prefix:
            return proper
        if slug_no_id.startswith(prefix + '-'):
            tail = slug_no_id[len(prefix) + 1:]
            # Title-case the tail words with the standard rules.
            return proper + ' ' + _amc_titlecase_tail(tail)

    return _amc_titlecase_tail(slug_no_id)


def _amc_titlecase_tail(s: str) -> str:
    """Title-case a hyphen-separated slug tail. Lowercases stopwords
    when they're not the first token, then re-attaches apostrophe-s
    fragments (`founder-s-story` -> `Founder's Story`)."""
    parts = s.split('-')
    if not parts:
        return ''
    lowercase = {'of', 'the', 'a', 'an', 'and', 'in', 'to', 'for', 'on',
                 'with', 'at', 'by'}
    out = []
    for i, tok in enumerate(parts):
        if i > 0 and tok in lowercase:
            out.append(tok)
        else:
            out.append(tok.capitalize())
    title = ' '.join(out)
    title = re.sub(r"\b([A-Za-z]{2,}) S\b", r"\1's", title)
    return title


def _fetch_amc(limit: int = 25) -> tuple[list[dict], str]:
    # Lazy-import curl_cffi so machines without it (older Hetzner
    # boxes) still boot the module and just skip AMC.
    try:
        from curl_cffi import requests as _ccr  # type: ignore
    except Exception:
        return [], ('curl_cffi not installed. `pip3 install --break-'
                    'system-packages curl_cffi` on the scraper host.')

    from ._base import load_donated_cookies
    cookies = load_donated_cookies('amctheatres.com')
    if not cookies:
        _mark_cookie_gap('amc', 'amctheatres.com',
                          reason='no donated cookies for amctheatres.com')
        return [], _WARMING_UP_HINT

    try:
        resp = _ccr.get(_AMC_URL, impersonate='chrome131',
                         cookies=cookies, timeout=20)
    except Exception as e:
        logger.info("amc curl_cffi request failed: %s", e)
        _mark_cookie_gap('amc', 'amctheatres.com',
                          reason=f'curl_cffi request failed: {e}')
        return [], _WARMING_UP_HINT
    html = resp.text or ''
    if resp.status_code != 200 or len(html) < 20000:
        logger.info("amc: got status=%d bytes=%d (looks like a bot challenge)",
                     resp.status_code, len(html))
        _mark_cookie_gap('amc', 'amctheatres.com',
                          reason=(f'AMC returned status={resp.status_code} '
                                  f'bytes={len(html)} - likely bot-challenge '
                                  f'shell; cookies may be stale'))
        return [], _WARMING_UP_HINT

    # Count slug occurrences. AMC's SSR HTML repeats each merchandised
    # title 6-16 times; use count as a merchandising rank proxy.
    #
    # KEY DESIGN (2026-07-29): AMC also lists SPECIAL SCREENING variants
    # of a title as SEPARATE slugs whose name-part BEGINS with the
    # canonical title's name-part, e.g.
    #    spider-man-brand-new-day-78598                        (real canonical)
    #    spider-man-brand-new-day-dolby-opening-night-fan-event-84005
    #    spider-man-brand-new-day-sensory-friendly-screening-84001
    #    spider-man-brand-new-day-private-theatre-rental-83943
    # All variants START with `spider-man-brand-new-day-` in their
    # name-part. Every variant contains at least one `_AMC_EVENT_TOKENS`
    # substring (that's how we identify them as variants). We fold
    # each variant's count INTO the canonical slug's count so
    # merchandising signal accrues to the base title.
    #
    # Grouping algorithm:
    #   1. Collect all slugs with their raw counts.
    #   2. Split by "canonical" (no event token in slug) vs "variant".
    #   3. Sort canonicals by name-part length DESCENDING so a longer
    #      canonical name never gets absorbed into a shorter one that
    #      happens to be its prefix.
    #   4. For each variant, attach to the canonical whose name-part
    #      is the longest prefix of the variant's name-part.
    #   5. Variants that don't match any canonical stand alone (rare;
    #      happens when AMC has only variant pages listed for a title).
    from collections import Counter
    all_counts: Counter = Counter(m.group(1) for m in _AMC_SLUG_RE.finditer(html))

    def _name_part(slug: str) -> str:
        # Everything before the trailing -<digits> AMC-id.
        return re.sub(r'-\d+$', '', slug)

    canonicals: list[tuple[str, str, int]] = []   # (name_part, slug, count)
    variants:   list[tuple[str, str, int]] = []
    for slug, cnt in all_counts.items():
        name  = _name_part(slug)
        is_variant = any(tok in slug for tok in _AMC_EVENT_TOKENS)
        (variants if is_variant else canonicals).append((name, slug, cnt))

    # Sort canonicals longest-first so we match the most specific
    # canonical name before its shorter prefix. E.g. we prefer
    # `the-bad-guys-2` over `the-bad-guys` when the variant is
    # `the-bad-guys-2-imax-72998`.
    canonicals.sort(key=lambda t: -len(t[0]))

    # Seed each group with the canonical's own count.
    groups: dict[str, dict] = {}
    for name, slug, cnt in canonicals:
        groups.setdefault(slug, {'slug': slug, 'total': cnt, 'name': name})

    def _match_canonical(variant_name: str) -> Optional[str]:
        """Return the canonical slug whose name-part is the longest
        prefix (followed by `-`) of `variant_name`. None if no match."""
        for name, slug, _cnt in canonicals:
            if variant_name == name or variant_name.startswith(name + '-'):
                return slug
        return None

    for vname, vslug, vcnt in variants:
        target = _match_canonical(vname)
        if target:
            groups[target]['total'] += vcnt
        else:
            # No canonical - keep the variant as its own group so we
            # still surface titles AMC only ever links via variant
            # pages (e.g. one-off preview screenings that don't have
            # a base page yet).
            groups.setdefault(vslug, {'slug': vslug, 'total': vcnt,
                                       'name': vname})

    # Rank groups by total merchandising count.
    items: list[dict] = []
    ranked = sorted(groups.values(), key=lambda g: -g['total'])
    for g in ranked[:limit * 4]:
        slug = g['slug']
        cnt  = g['total']
        title = _clean_title(_amc_slug_to_title(slug))
        if not _is_title(title):
            continue
        # Filter: don't surface variant-only titles (any event token
        # in the slug means the title looks like "X Sensory Friendly
        # Screening" which is ugly on the dashboard). If a title only
        # has variant listings on the current /movies page, better to
        # skip it than show the awkward variant name.
        if any(tok in slug for tok in _AMC_EVENT_TOKENS):
            continue
        # Require merged count >= 3 so filter chips / nav artifacts
        # (single-digit counts on chrome anchors) don't rank.
        if cnt < 3:
            continue
        items.append({
            'rank':  len(items) + 1,
            'title': title,
            'url':   f'https://www.amctheatres.com/movies/{slug}',
            'image': '',  # AMC doesn't expose poster URLs in the SSR HTML
        })
        if len(items) >= limit:
            break

    if items:
        return items, ''
    _mark_cookie_gap('amc', 'amctheatres.com',
                      reason='AMC HTML parsed 0 titles - slug regex may need update')
    return [], _WARMING_UP_HINT


# ---------------------------------------------------------------------------
# Regal Cinemas — All Movies in Theatres
# ---------------------------------------------------------------------------
# 403 on plain requests. Playwright + stealth passes. Regal's page is
# server-rendered Next.js and inlines the "Now Playing" list as a JSON
# blob (`"MovieFeedEntries": [{"Order": 0, "Movie": {"Title": ...,
# "Poster": ..., "FilmCode": ...}}]`) rather than exposing it as
# `<a href="/movies/...">` anchors. Parse that blob directly - the
# anchor-scraping path finds zero movie links even on a fully rendered
# page (verified 2026-07-29).
# 2026-07-29: switched from `/movies/all-movies-in-theatres` (which now
# returns HTTP 404 and serves a stale "now playing only" list missing
# the top marquee pre-release like Spider-Man: Brand New Day) to
# `/movies` (HTTP 200, ~140 titles in MovieFeedEntries with the top
# marquee title at position #1). Verified 2026-07-29.
_REGAL_URL      = 'https://www.regmovies.com/movies'
_REGAL_HOMEPAGE = 'https://www.regmovies.com/'
_REGAL_FEED_KEY_RE = re.compile(r'"MovieFeedEntries"\s*:\s*\[')


def _extract_json_array(html: str, start_idx: int) -> Optional[str]:
    """Given the index of a '[' in `html`, walk forward counting brackets
    (respecting string literals) and return the matching '[...]' slice or
    None if the array doesn't close cleanly."""
    n = len(html)
    depth = 0
    i = start_idx
    while i < n:
        c = html[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return html[start_idx:i + 1]
        elif c == '"':
            j = i + 1
            while j < n:
                if html[j] == '\\':
                    j += 2
                    continue
                if html[j] == '"':
                    break
                j += 1
            i = j
        i += 1
    return None


def _fetch_regal(limit: int = 25) -> tuple[list[dict], str]:
    html = _playwright_render(
        _REGAL_URL, _REGAL_HOMEPAGE,
        wait_selectors=[
            # Regal renders the list as `<img>` posters + a Next.js
            # data blob rather than /movies/ anchors, so wait on
            # anything that signals hydration completed.
            'img[src*="regalcdn"]',
            '[class*="Poster"], [class*="poster"]',
        ],
        cookie_domain='regmovies.com',
    )
    if not html:
        _mark_cookie_gap('regal', 'regmovies.com',
                          reason='Playwright returned empty body; cookies may be stale')
        return [], _WARMING_UP_HINT

    import json as _json
    m = _REGAL_FEED_KEY_RE.search(html)
    if not m:
        _mark_cookie_gap('regal', 'regmovies.com',
                          reason='Regal MovieFeedEntries JSON key missing - page shape may have changed')
        return [], _WARMING_UP_HINT
    blob = _extract_json_array(html, m.end() - 1)
    if not blob:
        _mark_cookie_gap('regal', 'regmovies.com',
                          reason='Regal MovieFeedEntries JSON did not close cleanly')
        return [], _WARMING_UP_HINT
    try:
        entries = _json.loads(blob)
    except Exception as e:
        _mark_cookie_gap('regal', 'regmovies.com',
                          reason=f'Regal MovieFeedEntries JSON parse failed: {e}')
        return [], _WARMING_UP_HINT

    items: list[dict] = []
    # Regal preserves the merchandising order on the "Now Playing" feed
    # via the Order field. Sort by it and take the first `limit`.
    for e in sorted(entries, key=lambda x: x.get('Order', 999)):
        mv = e.get('Movie') or {}
        title = _clean_title(mv.get('Title') or '')
        if not _is_title(title):
            continue
        image = mv.get('Poster') or ''
        # Build a real URL via FilmCode. Regal's search page accepts
        # `?filmCode=<code>` and deep-links to the film's page. If we
        # have neither slug nor code, fall back to the browse root.
        film_code = mv.get('FilmCode') or ''
        if film_code:
            url = f'https://www.regmovies.com/showtimes?filmCode={film_code}'
        else:
            url = _REGAL_URL
        items.append({
            'rank':  len(items) + 1,
            'title': title,
            'url':   url,
            'image': image,
        })
        if len(items) >= limit:
            break
    if items:
        return items, ''
    _mark_cookie_gap('regal', 'regmovies.com',
                      reason='Regal MovieFeedEntries returned zero valid titles')
    return [], _WARMING_UP_HINT


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

    return {
        # Mirror Fandango as the "national" list because it has the
        # broadest US theater reach (~40% of US ticketing volume).
        'national': fandango_items[:30] or cinemark_items[:30],
        'available': bool(fandango_items or cinemark_items or amc_items
                          or regal_items),
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
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    p = argparse.ArgumentParser(description='Film-ticketing scraper')
    p.add_argument('--only', default='',
                    help='comma-separated source keys: '
                          'fandango,cinemark,amc,regal')
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
