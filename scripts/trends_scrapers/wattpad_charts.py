"""
Wattpad trending scraper.

Pulls the top 6 story rails from Wattpad's public API + browse
surfaces into a single snapshot the dashboard renders as one panel
inside the Books tab (`renderTIQBooks`), mirroring the multi-rail
shape `libby_trends` and `steam_charts` use.

Rails (6 total, ~175 stories):

    - wattpad_hot           top ~50 hot stories globally
    - wattpad_originals     ~25 Wattpad Originals (paid / studio-
                              invested serialized fiction — pulled
                              from the curated
                              /user/wattpadoriginals reading list
                              "Wattpad Originals: Trending", list id
                              1484727023, which is Wattpad's own
                              editorial rail)
    - wattpad_romance       ~25 top hot stories tagged Romance
    - wattpad_teen_fiction  ~25 top hot stories tagged Teen Fiction
    - wattpad_fanfiction    ~25 top hot stories tagged Fanfiction
    - wattpad_fantasy       ~25 top hot stories tagged Fantasy

Snapshot shape (kind='wattpad'):

    {
      "source":     "wattpad_charts",
      "kind":       "wattpad",
      "label":      "Wattpad",
      "fetched_at": "...",
      "sources": {
        "wattpad_hot":          {"label": "Wattpad - Hot Stories",
                                  "sub":   "...",
                                  "items": [...],
                                  "available": bool},
        "wattpad_originals":    {"label": "Wattpad - Originals",   ...},
        "wattpad_romance":      {"label": "Wattpad - Romance",     ...},
        "wattpad_teen_fiction": {"label": "Wattpad - Teen Fiction",...},
        "wattpad_fanfiction":   {"label": "Wattpad - Fanfiction",  ...},
        "wattpad_fantasy":      {"label": "Wattpad - Fantasy",     ...}
      }
    }

Every `items[i]` has:

    {
      "rank":                    int,
      "title":                   str,
      "artist":                  str,        # author (kept named
                                             # `artist` to match music
                                             # + book rows so the
                                             # frontend row renderer
                                             # can be shared)
      "author":                  str,        # duplicate of artist
                                             # for readability
      "reads":                   int,        # cumulative all-time
                                             # reads (native Wattpad
                                             # signal)
      "reads_display":           str,        # "12.3M" style abbrev
      "votes":                   int,
      "chapters":                int,
      "genre_primary":           str,        # single-word genre
                                             # (Romance / Teen
                                             # Fiction / Fanfiction
                                             # / Fantasy /
                                             # Paranormal / ...)
      "cover_url":               str,        # 256px cover jpg
      "image":                   str,        # alias for cover_url
                                             # so the frontend row
                                             # renderer can share
                                             # code with other book
                                             # rows
      "story_url":               str,        # canonical
                                             # /story/<id>/<slug>
      "url":                     str,        # alias for story_url
      "is_completed":            bool,
      "wattpad_originals_flag":  bool,       # True on the Originals
                                             # rail
      "is_new":                  bool,       # published in last
                                             # 14 days
      "story_id":                int,        # numeric wattpad id
    }

Cookies: NOT required. Wattpad's browse + API surfaces are open.
`curl_cffi` Chrome-TLS impersonation is used defensively (edge
cache occasionally 403s a raw requests fingerprint under load).

Standalone:

    python3 -m scripts.trends_scrapers.wattpad_charts
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# curl_cffi is a drop-in requests replacement that impersonates real
# Chrome at the TLS layer (JA3 fingerprint). Wattpad's Cloudflare edge
# is normally permissive but has been observed to 403 raw requests
# clients under regional load spikes. Chrome-TLS impersonation adds a
# reliability margin at zero cost. Falls back to plain requests when
# curl_cffi isn't installed (which shouldn't happen on Hetzner - it's
# pinned in requirements.txt).
try:
    from curl_cffi import requests as _cc_requests  # type: ignore
    _HAS_CURL_CFFI = True
except ImportError:
    import requests as _cc_requests  # type: ignore
    _HAS_CURL_CFFI = False

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/'
        '537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# The "Wattpad Originals: Trending" reading list on the official
# `wattpadoriginals` staff account. Confirmed 2026-09-01 as the
# canonical editorially-curated Originals rail (46 stories).
# Wattpad has no single "Originals landing page" that server-renders
# story tiles; the /premium/ URL is a marketing page, /wattpadoriginals
# is a staff-account redirect that lists only 4 stories, and the
# public API does not expose the `paidModel` field, so filtering the
# Hot API by "paid stories" is not possible. This curated list is the
# only reliable source of the current Originals catalogue.
_ORIGINALS_LIST_ID = 1484727023

# Genre rails. `browse_slug` matches Wattpad's `/stories/<slug>` HTML
# browse-page URL. `display` is the human-readable label used in the
# frontend + CSV export. `panel_key` is the snapshot key. Note we
# deliberately do NOT use the API's `?tags=<slug>` filter here - it
# is silently ignored server-side and returns the global Hot ranking
# unchanged, so all 4 genre rails would collapse to the same top-N.
# The HTML browse page's `hotStoriesForTag` module returns genre-
# specific rankings and is what Wattpad's own /stories/<genre>
# surface uses.
_GENRE_RAILS = [
    ('romance',      'Romance',      'wattpad_romance'),
    ('teen-fiction', 'Teen Fiction', 'wattpad_teen_fiction'),
    ('fanfiction',   'Fanfiction',   'wattpad_fanfiction'),
    ('fantasy',      'Fantasy',      'wattpad_fantasy'),
]

# Wattpad `categories` array uses positional integer ids into a fixed
# category vocab. Mapping lifted from Wattpad's public genre picker.
_CATEGORY_ID_TO_NAME = {
    1:  'Teen Fiction',
    2:  'Poetry',
    3:  'Fantasy',
    4:  'Romance',
    5:  'Science Fiction',
    6:  'Fanfiction',
    7:  'Humor',
    8:  'Mystery / Thriller',
    9:  'Horror',
    10: 'Classics',
    11: 'Adventure',
    12: 'Paranormal',
    13: 'Spiritual',
    14: 'Action',
    16: 'Non-Fiction',
    17: 'Short Story',
    18: 'Vampire',
    21: 'Random',
    22: 'General Fiction',
    23: 'Werewolf',
    24: 'Historical Fiction',
    26: 'ChickLit',
    27: 'LGBTQ+',
}

_WARMING_UP_HINT = 'Warming up. Check back later.'


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    """Fire the operator-facing SES notification. Best-effort; never
    raises. The dashboard tile only ever sees `_WARMING_UP_HINT`.
    """
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                     source, domain, e)


def _get_json(url: str, *, retries: int = 3, timeout: int = 25) -> dict:
    """GET a Wattpad JSON API response. Chrome-TLS impersonate when
    available. Retries with jittered backoff. Returns {} on total
    failure so callers can no-op cleanly.
    """
    last_err = None
    for attempt in range(retries):
        try:
            kwargs = {'headers': {'User-Agent': _UA,
                                    'Accept':     'application/json'},
                       'timeout': timeout,
                       'allow_redirects': True}
            if _HAS_CURL_CFFI:
                kwargs['impersonate'] = 'chrome124'
            r = _cc_requests.get(url, **kwargs)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
            continue
        status = getattr(r, 'status_code', 0)
        if status == 429 or status >= 500:
            last_err = f"http {status}"
            time.sleep(3 * (attempt + 1))
            continue
        if not r.ok:
            logger.info("wattpad api %s: http %s", url, status)
            return {}
        try:
            return r.json()
        except Exception as e:
            last_err = f"json parse: {e}"
            time.sleep(1 + attempt)
    logger.warning("wattpad api %s: exhausted retries; last=%s",
                    url, last_err)
    return {}


_REMIX_RE = re.compile(
    r'<script[^>]*>window\.__remixContext\s*=\s*(\{.*?\});</script>',
    re.DOTALL,
)


def _get_browse_html_stories(url: str, *,
                                module_type: str = 'hotStoriesForTag',
                                retries: int = 3,
                                timeout: int = 25) -> list[dict]:
    """GET a `/stories/<genre>` browse page and extract the raw
    stories list from the `hotStoriesForTag` remix module. Wattpad
    embeds the module payload in a large `window.__remixContext = {...}`
    JSON blob at the bottom of the HTML. Falls back to [] on any
    error so callers can no-op cleanly.
    """
    last_err = None
    for attempt in range(retries):
        try:
            kwargs = {'headers': {'User-Agent': _UA,
                                    'Accept':     ('text/html,application/'
                                                    'xhtml+xml,application/'
                                                    'xml;q=0.9,*/*;q=0.8'),
                                    'Accept-Language': 'en-US,en;q=0.9'},
                       'timeout': timeout,
                       'allow_redirects': True}
            if _HAS_CURL_CFFI:
                kwargs['impersonate'] = 'chrome124'
            r = _cc_requests.get(url, **kwargs)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
            continue
        status = getattr(r, 'status_code', 0)
        if status == 429 or status >= 500:
            last_err = f"http {status}"
            time.sleep(3 * (attempt + 1))
            continue
        if not r.ok:
            logger.info("wattpad browse %s: http %s", url, status)
            return []
        m = _REMIX_RE.search(r.text or '')
        if not m:
            last_err = 'no __remixContext'
            time.sleep(1 + attempt)
            continue
        try:
            data = json.loads(m.group(1))
        except Exception as e:
            last_err = f"remixContext json: {e}"
            time.sleep(1 + attempt)
            continue

        # Walk the object tree looking for module lists. Wattpad
        # sometimes nests these under different keys per page-router
        # version, so a recursive walk is more robust than a hardcoded
        # dotted path.
        stories: list[dict] = []

        def _walk(obj):
            nonlocal stories
            if stories:
                return
            if isinstance(obj, dict):
                # A module list.
                if obj.get('type') == module_type:
                    payload = ((obj.get('data') or {}).get('items') or {})
                    st = payload.get('stories') or []
                    if isinstance(st, list) and st:
                        stories = st
                        return
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    _walk(v)

        _walk(data)
        return stories

    logger.warning("wattpad browse %s: exhausted retries; last=%s",
                    url, last_err)
    return []


def _reads_display(n: int) -> str:
    """"12345678" -> "12.3M" the same way Wattpad renders it. Preserves
    a leading zero for really small counts (<1K stays as "923"). Used
    ONLY for the display string on the frontend chip; the exact int
    stays in `reads`."""
    if n is None:
        return ''
    try:
        n = int(n)
    except Exception:
        return ''
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        v = n / 1_000
        # 1.0K -> "1K"; 12.3K -> "12.3K"; 123K -> "123K"
        if v >= 100:
            return f'{int(round(v))}K'
        return f'{v:.1f}K'.replace('.0K', 'K')
    if n < 1_000_000_000:
        v = n / 1_000_000
        if v >= 100:
            return f'{int(round(v))}M'
        return f'{v:.1f}M'.replace('.0M', 'M')
    v = n / 1_000_000_000
    return f'{v:.1f}B'.replace('.0B', 'B')


def _canonical_story_url(story_id: int, slug_hint: str = '') -> str:
    """Build a canonical `/story/<id>-<slug>` URL. Wattpad's own URLs
    percent-encode Unicode titles; we return the plain
    `/story/<id>` form since it always resolves and reads cleaner in
    a CSV export.
    """
    if not story_id:
        return ''
    return f'https://www.wattpad.com/story/{story_id}'


def _cover_url_from_api(cover: str, story_id: int) -> str:
    """Wattpad's API returns cover at 256px width by default; keep
    that resolution (renders sharp at retina card sizes without
    ballooning payload). If cover is missing, synthesize the
    canonical URL from story_id.
    """
    if cover:
        return cover
    if story_id:
        return f'https://img.wattpad.com/cover/{story_id}-256.jpg'
    return ''


def _pick_genre(story: dict, fallback: str = '') -> str:
    """Prefer the primary category id from Wattpad's `categories`
    list. Falls back to the first tag that matches a known genre, then
    to the caller-supplied fallback (e.g. the genre rail we pulled
    the story from)."""
    cats = story.get('categories') or []
    for cid in cats:
        try:
            cid = int(cid)
        except Exception:
            continue
        name = _CATEGORY_ID_TO_NAME.get(cid)
        if name:
            return name
    # Tag fallback (defensive - Wattpad has flipped id vocabularies
    # before). Try to match a tag to a canonical genre name.
    tags = [str(t).lower() for t in (story.get('tags') or [])]
    for tag in tags:
        for cid, name in _CATEGORY_ID_TO_NAME.items():
            if tag == name.lower().replace(' ', '').replace('/', ''):
                return name
        # Common tag spellings the API sometimes uses even without a
        # matching category id.
        if tag in ('romance', 'werewolf', 'vampire', 'humor',
                    'poetry', 'horror', 'adventure', 'paranormal',
                    'lgbtq', 'lgbtq+'):
            return tag.title().replace('Lgbtq+', 'LGBTQ+').replace('Lgbtq', 'LGBTQ+')
        if tag in ('teenfiction', 'teen-fiction'):
            return 'Teen Fiction'
        if tag == 'fanfiction':
            return 'Fanfiction'
        if tag == 'fantasy':
            return 'Fantasy'
        if tag == 'sciencefiction':
            return 'Science Fiction'
    return fallback


def _is_recently_published(create_date: str) -> bool:
    """True if the story was first published in the last 14 days.
    Best-effort parse; unknown / malformed dates return False so we
    don't accidentally flag every row as new."""
    if not create_date:
        return False
    try:
        # Wattpad uses ISO-8601 with a Z terminator.
        dt = datetime.fromisoformat(create_date.replace('Z', '+00:00'))
    except Exception:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    return dt >= cutoff


def _normalize_story(raw: dict, rank: int, *,
                       wattpad_originals_flag: bool,
                       genre_fallback: str = '') -> dict:
    """Shape a raw Wattpad story dict (from the API or the browse
    remixContext) into the standard chart-row format the frontend
    consumes. Returns None-shaped dicts only if the underlying object
    is completely malformed (no id + no title); every field defaults
    to a safe empty value so downstream renderers never crash on a
    missing key.

    The API and the browse remixContext ship slightly different
    schemas for the SAME story object; this normalizer accepts
    either. Differences:
      - readCount / readCount (both)
      - voteCount / voteCount (both)
      - numParts (both)
      - user.name (both)
      - cover (API: full URL) / cover (browse: full URL) - same
      - firstPublishedPart.createDate (API) - browse omits
      - paidModel: browse only; if set, this row is a Wattpad
        Original regardless of which rail we pulled it on
      - isPaywalled: browse only, alternate signal for Originals
    """
    if not isinstance(raw, dict):
        return {}
    story_id = raw.get('id') or 0
    try:
        story_id = int(story_id)
    except Exception:
        story_id = 0
    title = _html.unescape((raw.get('title') or '').strip())
    if not story_id and not title:
        return {}

    user = raw.get('user') or {}
    author = _html.unescape((user.get('name') or user.get('fullname')
                              or '').strip())

    reads = raw.get('readCount') or 0
    try:
        reads = int(reads)
    except Exception:
        reads = 0
    votes = raw.get('voteCount') or 0
    try:
        votes = int(votes)
    except Exception:
        votes = 0
    parts = raw.get('numParts') or 0
    try:
        parts = int(parts)
    except Exception:
        parts = 0

    cover = _cover_url_from_api(raw.get('cover') or '', story_id)
    story_url = _canonical_story_url(story_id, title)
    genre = _pick_genre(raw, fallback=genre_fallback)
    # Browse-page rows carry `completed` as bool; API rows carry it
    # under the same key. Either way just cast.
    completed = bool(raw.get('completed'))
    is_new = _is_recently_published(raw.get('firstPublishedPart', {})
                                       .get('createDate')
                                       or raw.get('createDate') or '')
    # Elevate to Originals if the raw row itself flags a paid model
    # (browse-page rows carry `paidModel`, API rows don't). A story
    # on a genre rail that also has a paid model is genuinely an
    # Original and the flag should ride along so the frontend
    # renders it with the Originals badge even outside the Originals
    # panel.
    paid_model = str(raw.get('paidModel') or '').strip()
    if paid_model and paid_model.lower() not in ('', 'none', 'null'):
        wattpad_originals_flag = True

    return {
        'rank':                    rank,
        'title':                   title,
        # Mirror the "artist" convention book / music / podcast rows
        # use so the frontend row renderer can share code.
        'artist':                  author,
        'author':                  author,
        'reads':                   reads,
        'reads_display':           _reads_display(reads),
        'votes':                   votes,
        'chapters':                parts,
        'genre_primary':           genre,
        'cover_url':               cover,
        'image':                   cover,
        'story_url':               story_url,
        'url':                     story_url,
        'is_completed':            completed,
        'wattpad_originals_flag':  bool(wattpad_originals_flag),
        'is_new':                  is_new,
        'story_id':                story_id,
    }


# ---------------------------------------------------------------------------
# Rail: Hot Stories (top ~50 globally trending)
# ---------------------------------------------------------------------------
# `limit=50` on the Hot API silently caps at 44 rows; `limit=100`
# returns ~91. Requesting 100 and taking the top 50 gets us the full
# requested count.
_API_HOT = 'https://www.wattpad.com/api/v3/stories?filter=hot&limit=100'


def _fetch_wattpad_hot(limit: int = 50) -> list[dict]:
    j = _get_json(_API_HOT)
    raw = j.get('stories') or []
    items: list[dict] = []
    for i, s in enumerate(raw[:limit], start=1):
        row = _normalize_story(s, i, wattpad_originals_flag=False)
        if row:
            items.append(row)
    # Re-rank contiguously in case a normalize returned empty.
    for i, r in enumerate(items, start=1):
        r['rank'] = i
    return items


# ---------------------------------------------------------------------------
# Rail: Wattpad Originals (curated reading list 1484727023)
# ---------------------------------------------------------------------------
_API_LIST_TMPL = ('https://www.wattpad.com/api/v3/lists/{list_id}/'
                    'stories?limit=50')


def _fetch_wattpad_originals(limit: int = 25) -> list[dict]:
    j = _get_json(_API_LIST_TMPL.format(list_id=_ORIGINALS_LIST_ID))
    raw = j.get('stories') or []
    items: list[dict] = []
    for i, s in enumerate(raw[:limit], start=1):
        row = _normalize_story(s, i, wattpad_originals_flag=True)
        if row:
            items.append(row)
    for i, r in enumerate(items, start=1):
        r['rank'] = i
    return items


# ---------------------------------------------------------------------------
# Rail: Per-genre hot (~25 each)
# ---------------------------------------------------------------------------
_BROWSE_TMPL = 'https://www.wattpad.com/stories/{slug}'


def _fetch_wattpad_genre(browse_slug: str, display: str,
                            limit: int = 25) -> list[dict]:
    """Pull top hot stories for a genre from Wattpad's `/stories/<slug>`
    browse page. The page's `hotStoriesForTag` module contains the
    genuinely genre-specific ranking (~19-20 rows per page). If we
    haven't hit `limit` yet, top up from the `newStoriesForTag`
    module so the rail always fills out to the requested count.
    """
    hot = _get_browse_html_stories(
        _BROWSE_TMPL.format(slug=browse_slug),
        module_type='hotStoriesForTag',
    )
    items: list[dict] = []
    seen_ids: set[int] = set()
    for i, s in enumerate(hot, start=1):
        row = _normalize_story(s, i,
                                 wattpad_originals_flag=False,
                                 genre_fallback=display)
        if not row:
            continue
        sid = row.get('story_id') or 0
        if sid and sid in seen_ids:
            continue
        if sid:
            seen_ids.add(sid)
        items.append(row)
        if len(items) >= limit:
            break

    # Top up from newStoriesForTag if we're short of the target.
    if len(items) < limit:
        new = _get_browse_html_stories(
            _BROWSE_TMPL.format(slug=browse_slug),
            module_type='newStoriesForTag',
        )
        for s in new:
            row = _normalize_story(s, len(items) + 1,
                                     wattpad_originals_flag=False,
                                     genre_fallback=display)
            if not row:
                continue
            sid = row.get('story_id') or 0
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)
            items.append(row)
            if len(items) >= limit:
                break

    # Re-rank contiguously.
    for i, r in enumerate(items, start=1):
        r['rank'] = i
    return items


# ---------------------------------------------------------------------------
# Prior-snapshot recovery + main fetch
# ---------------------------------------------------------------------------
def _load_previous_panel(source: str, panel_key: str) -> list[dict]:
    """When today's fetch of a rail comes back empty, fall back to
    yesterday's snapshot for THAT rail so the tile always has
    something to render. Follows the standard `read_snapshot` path
    in `_base` (which reads `latest/{source}.json`).
    """
    try:
        from ._base import read_snapshot
        prior = read_snapshot(source) or {}
    except Exception:
        return []
    return ((prior.get('sources') or {}).get(panel_key) or {}).get('items') or []


def fetch() -> dict[str, Any]:
    sources: dict[str, dict] = {}
    all_flat: list[dict] = []

    _RAIL_LABELS = {
        'wattpad_hot':          ('Wattpad - Hot Stories',
                                  "Top trending stories on Wattpad "
                                  "right now."),
        'wattpad_originals':    ('Wattpad - Originals',
                                  "Wattpad's editorially curated "
                                  "serialized fiction imprint."),
        'wattpad_romance':      ('Wattpad - Romance',
                                  "Top hot Romance stories."),
        'wattpad_teen_fiction': ('Wattpad - Teen Fiction',
                                  "Top hot Teen Fiction stories."),
        'wattpad_fanfiction':   ('Wattpad - Fanfiction',
                                  "Top hot Fanfiction stories."),
        'wattpad_fantasy':      ('Wattpad - Fantasy',
                                  "Top hot Fantasy stories."),
    }

    def _wrap(panel_key: str, items: list[dict]) -> None:
        label, sub = _RAIL_LABELS[panel_key]
        available = bool(items)
        if not available:
            prior = _load_previous_panel('wattpad_charts', panel_key)
            if prior:
                items = prior
                # `available` stays False so the tile can still show
                # a warming-up sub-line if the panel is important, but
                # the rendered items land the same as a live fetch.
                logger.info("wattpad %s: falling back to prior snapshot (%d items)",
                             panel_key, len(prior))
                _mark_cookie_gap('wattpad_charts', 'wattpad.com',
                                  reason=f'{panel_key} empty response')
        sources[panel_key] = {
            'label':     label,
            'sub':       sub if available else _WARMING_UP_HINT,
            'items':     items,
            'available': available,
        }
        for it in items:
            all_flat.append({**it, 'rail': panel_key})

    # 1. Hot Stories
    _wrap('wattpad_hot', _fetch_wattpad_hot(limit=50))
    time.sleep(0.5)  # be polite

    # 2. Wattpad Originals
    _wrap('wattpad_originals', _fetch_wattpad_originals(limit=25))
    time.sleep(0.5)

    # 3. Per-genre hot rails
    for tag, display, panel_key in _GENRE_RAILS:
        _wrap(panel_key, _fetch_wattpad_genre(tag, display, limit=25))
        time.sleep(0.5)

    # National fold: top-ranked union of Hot + Originals for the
    # summary index (mirrors `libby_trends`'s national build).
    national: list[dict] = []
    seen_ids: set[int] = set()
    for panel_key in ('wattpad_hot', 'wattpad_originals'):
        for it in (sources.get(panel_key) or {}).get('items') or []:
            sid = it.get('story_id') or 0
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)
            national.append(it)
            if len(national) >= 50:
                break
        if len(national) >= 50:
            break

    return {
        'national':  national,
        'available': any(s['available'] for s in sources.values()),
        'sources':   sources,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('wattpad_charts', 'Wattpad', 'wattpad', fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in (panel.get('items') or [])[:3]:
            print(f"   #{it['rank']} {it['title'][:50]:<50} - "
                   f"{it['author'][:20]:<20} "
                   f"reads={it['reads_display']:>8} "
                   f"votes={it['votes']:>6}  {it['genre_primary']}",
                   file=sys.stderr)
