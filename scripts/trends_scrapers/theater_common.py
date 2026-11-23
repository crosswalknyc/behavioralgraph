"""
Shared helpers for the theater-chain cards on the Films tab.

Every chain card lists the films with the most showtimes today across
that chain's US theaters. Showtimes are the chain's own published
schedule, and a theater gives its screens to the films it expects to
sell, so the count is the closest thing a chain publishes to a demand
chart. The chain's own listing order only breaks a tie: most chain
sites show a few featured titles and then everything else A to Z, and
a place in an alphabetical list is not a rank (the same defect the
FAST channel rails had on 2026-10-14).

Each chain lives in its own `theater_<chain>.py` with this contract:

    KEY            = 'harkins'                 # source key on the card
    LABEL          = 'Harkins Theatres'        # card title
    COOKIE_DOMAIN  = None                      # or 'marcustheatres.com'

    def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
        '''(items, meta). Items come from `rank_by_showtimes`. meta
        carries at least {'theaters': <theaters read>, 'date':
        'YYYY-MM-DD'} and 'blocked': True when a bot check stopped the
        read.'''

`theater_chains.py` holds the registry and runs every chain for the
film_ticketing snapshot (Mac residential lane).
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
import unicodedata
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.0.0 Safari/537.36')

DEFAULT_LIMIT = 30
HTTP_TIMEOUT_S = 25


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def session(extra_headers: Optional[dict] = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': UA,
        'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    if extra_headers:
        s.headers.update(extra_headers)
    return s


def get_text(sess: requests.Session, url: str, *, params: Optional[dict] = None,
             headers: Optional[dict] = None, retries: int = 2,
             timeout: int = HTTP_TIMEOUT_S) -> Optional[str]:
    """Body text, or None after `retries` extra attempts. Never raises."""
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.text
            logger.info("theaters: %s answered %s", url[:120], r.status_code)
            if r.status_code in (401, 403, 404):
                return None
        except Exception as e:
            logger.info("theaters: %s failed (%s)", url[:120], str(e)[:120])
        time.sleep(1.5 * (attempt + 1))
    return None


def get_json(sess: requests.Session, url: str, **kw) -> Any:
    """Parsed JSON, or None. Never raises."""
    text = get_text(sess, url, **kw)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        logger.info("theaters: %s did not answer JSON", url[:120])
        return None


def today_iso(tz: str = 'America/Chicago') -> str:
    """Today's date where the theaters are. The Mac lane runs at 8 AM
    Pacific, so every US theater is on the same calendar day."""
    return datetime.now(ZoneInfo(tz)).date().isoformat()


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------
# A chain lists the same film several times: subtitled, dubbed, open
# captioned, 3D, a premium screen, a sensory-friendly show. Those are
# one film to a moviegoer, so their showtimes are added together under
# the plain title. A re-release or an anniversary edition is a
# different product and keeps its own row.
_BRACKETS_RE = re.compile(r'\s*\[[^\]]*\]\s*')
_CURLY_YEAR_RE = re.compile(r'\s*\{\s*((?:19|20)\d\d)\s*\}\s*')
_RATING_RE = re.compile(r'\s*\((?:G|PG|PG-13|R|NC-17|NR|Not Rated|Unrated|TV-MA|TV-14)\)\s*', re.I)
_FORMAT_WORDS = (
    r'open[ -]?capt(?:ion|ioned|ions)?|closed[ -]?capt(?:ion|ioned|ions)?'
    r'|on-?screen captions?|\boc\b|sensory[ -]?friendly|subtitled|subtitles'
    r'|dubbed|dub|en espa(?:n|\u00f1)ol|espa(?:n|\u00f1)ol|spanish|english'
    r'|reald 3d|real d 3d|3d|2d|imax(?: with laser)?(?: experience)?|dolby(?: cinema| atmos| vision)?'
    r'|d-?box|4dx|screenx|\bxd\b|\brpx\b|\bplf\b|premium large format|ultrascreen'
    r'|superscreen|mx4d|prime|bigd|big d|epic xl|grand screen|ultra|lux|dine-?in'
    r'|mxt(?:[- ]atmos)?|open cap|special needs'
    r'|fan event|fan screenings?|early access|advance screenings?'
    r'|insider screenings?|spellbound screenings?'
)
_PAREN_FORMAT_RE = re.compile(r'\s*\((?:[^)]*\b(?:' + _FORMAT_WORDS + r')\b[^)]*)\)\s*', re.I)
_PREFIX_RE = re.compile(
    r'^(?:open[ -]?caption(?:ed)?|on-?screen captions?|oc|sensory[ -]?friendly'
    r'|special needs|early access|advance screening|fan event)\s*[:\-]\s*', re.I)
# 'in' and 'the' ride along so 'Dune: Part Three in IMAX' and
# 'The Odyssey: The IMAX Experience' lose the whole tag, not leave
# 'in' or 'The' hanging on the title.
_SUFFIX_RE = re.compile(
    r'(?:\s*[-:|\u2013]\s*|\s+)(?:(?:in|the)\s+)?(?:' + _FORMAT_WORDS + r')'
    r'(?:\s+(?:' + _FORMAT_WORDS + r'))*\s*$', re.I)
_SPACE_RE = re.compile(r'\s+')


_LONG_DASH_RE = re.compile(r'\s*[\u2013\u2014]\s*')


def clean_title(raw: str) -> str:
    """Entities decoded, spaces collapsed, a long dash made a plain
    hyphen (the board never prints one), nothing else changed."""
    if not raw:
        return ''
    s = unicodedata.normalize('NFC', _html.unescape(str(raw)))
    s = _LONG_DASH_RE.sub(' - ', s)
    return _SPACE_RE.sub(' ', s).strip()


def base_title(raw: str) -> str:
    """The plain film title a moviegoer would say, with format and
    language tags taken off: 'Godzilla Minus Zero [Japanese, subtitled]'
    -> 'Godzilla Minus Zero', 'On-Screen Captions: Wicked' -> 'Wicked',
    'Avengers: Doomsday RealD 3D Fan Event' -> 'Avengers: Doomsday'.
    A curly-brace year becomes the usual '(1945)'."""
    s = clean_title(raw)
    if not s:
        return ''
    s = _BRACKETS_RE.sub(' ', s)
    s = _CURLY_YEAR_RE.sub(r' (\1) ', s)
    s = _RATING_RE.sub(' ', s)
    for _ in range(3):
        before = s
        s = _PREFIX_RE.sub('', s.strip())
        s = _PAREN_FORMAT_RE.sub(' ', s)
        s = _SUFFIX_RE.sub('', s.strip())
        if s == before:
            break
    s = _SPACE_RE.sub(' ', s).strip(' -:|\u2013')
    return s or clean_title(raw)


def title_key(raw: str) -> str:
    """Merge key for one chain's list: base title, lower case, letters
    and digits only, a trailing release year ignored."""
    b = base_title(raw).lower()
    b = re.sub(r'\((?:19|20)\d\d\)\s*$', '', b)
    b = unicodedata.normalize('NFKD', b)
    b = ''.join(ch for ch in b if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+', '', b)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def rank_by_showtimes(rows: Iterable[dict], *,
                      limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Merge a chain's film rows and rank them by showtimes today.

    Each input row: {'title', 'showtimes' (int), 'url'?, 'image'?,
    'theater_ids'? (iterable) or 'theaters'? (int), 'site_order'?
    (int, the chain's own listing position)}. Rows for the same film
    (a subtitled print, a 3D print) are added together. Films with no
    showtime today are dropped: a presale for next month is not
    playing. Ties go to the chain's own listing order.

    Returns [{'rank', 'title', 'url', 'image', 'showtimes',
    'theaters'}], at most `limit`.
    """
    groups: dict[str, dict] = {}
    for i, r in enumerate(rows or []):
        raw = r.get('title') or ''
        key = title_key(raw)
        if not key:
            continue
        shows = int(r.get('showtimes') or 0)
        order = r.get('site_order')
        order = i if order is None else int(order)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                'title': base_title(raw), 'url': '', 'image': '',
                'showtimes': 0, 'ids': set(), 'theaters_max': 0,
                'order': order, 'lead_shows': -1, 'plain': False,
            }
        g['showtimes'] += shows
        g['order'] = min(g['order'], order)
        ids = r.get('theater_ids')
        if ids:
            g['ids'].update(str(x) for x in ids)
        elif r.get('theaters'):
            g['theaters_max'] = max(g['theaters_max'], int(r['theaters']))
        # The plain print names the row and lends its link and poster;
        # failing that, the print with the most showtimes does.
        plain = clean_title(raw) == base_title(raw)
        if (plain and not g['plain']) or (not g['plain'] and shows > g['lead_shows']):
            g['title'] = base_title(raw)
            g['url'] = r.get('url') or g['url']
            g['image'] = r.get('image') or g['image']
            g['lead_shows'] = shows
            g['plain'] = plain
        else:
            g['url'] = g['url'] or (r.get('url') or '')
            g['image'] = g['image'] or (r.get('image') or '')

    live = [g for g in groups.values() if g['showtimes'] > 0]
    live.sort(key=lambda g: (-g['showtimes'], g['order'], g['title'].lower()))
    out = []
    for g in live[:max(0, int(limit))]:
        out.append({
            'rank': len(out) + 1,
            'title': g['title'],
            'url': g['url'],
            'image': g['image'],
            'showtimes': g['showtimes'],
            'theaters': len(g['ids']) or g['theaters_max'] or None,
        })
    return out


# ---------------------------------------------------------------------------
# Headless Chrome, for sites behind a bot check or built in the browser
# ---------------------------------------------------------------------------
def render_html(url: str, *, homepage: Optional[str] = None,
                wait_selectors: Optional[list[str]] = None,
                cookie_domain: Optional[str] = None,
                wait_ms: int = 4500, hydration_wait_ms: int = 15000) -> str:
    """Rendered HTML of `url` from headless Chrome, '' on any failure.
    `cookie_domain` injects the cookies donated from the laptop's own
    Chrome (donate_cookies.py) for sites whose check wants a visitor
    who has already passed it."""
    try:
        from ._playwright import render_pages
    except Exception as e:
        logger.info("theaters: headless Chrome unavailable (%s)", e)
        return ''
    try:
        pages = render_pages([(url.split('//', 1)[-1], url)], homepage=homepage,
                             cookie_domain=cookie_domain,
                             wait_selectors=wait_selectors,
                             hydration_wait_ms=hydration_wait_ms, wait_ms=wait_ms)
    except Exception as e:
        logger.info("theaters: render of %s failed (%s)", url[:120], e)
        return ''
    return (pages[0][1] if pages else '') or ''


_FETCH_JS = """
async ({urls, concurrency, timeoutMs}) => {
  const out = {};
  let next = 0;
  async function worker() {
    while (next < urls.length) {
      const u = urls[next++];
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), timeoutMs);
      try {
        const r = await fetch(u, {credentials: 'include', signal: ctl.signal,
                                  headers: {'Accept': 'application/json, text/plain, */*'}});
        out[u] = r.ok ? await r.text() : null;
      } catch (e) {
        out[u] = null;
      } finally {
        clearTimeout(t);
      }
    }
  }
  await Promise.all(Array.from({length: Math.max(1, concurrency)}, worker));
  return out;
}
"""


def render_then_fetch(page_url: str, urls: list[str], *,
                      homepage: Optional[str] = None,
                      wait_selectors: Optional[list[str]] = None,
                      cookie_domain: Optional[str] = None,
                      concurrency: int = 4,
                      timeout_ms: int = 20000) -> dict[str, Optional[str]]:
    """Open `page_url` in headless Chrome the way a visitor would (so a
    bot check sets its cookies), then fetch each of `urls` from inside
    that page. The requests carry the page's own cookies and origin,
    so a site's data endpoints answer the way they answer its own
    front end. Returns {url: body text or None}; {} when the page
    could not be opened. Absolute or same-origin relative URLs."""
    urls = list(dict.fromkeys(u for u in urls if u))
    if not urls:
        return {}
    try:
        from ._playwright import render_pages
    except Exception as e:
        logger.info("theaters: headless Chrome unavailable (%s)", e)
        return {}
    box: dict[str, Optional[str]] = {}

    def _hook(page, _label):
        try:
            res = page.evaluate(_FETCH_JS, {'urls': urls,
                                            'concurrency': int(concurrency),
                                            'timeoutMs': int(timeout_ms)})
            box.update(res or {})
        except Exception as e:
            logger.info("theaters: in-page fetch failed (%s)", str(e)[:160])
        return json.dumps({'fetched': len(box)})

    try:
        render_pages([('fetch', page_url)], homepage=homepage,
                     cookie_domain=cookie_domain, wait_selectors=wait_selectors,
                     hydration_wait_ms=12000, wait_ms=3500, page_hook=_hook)
    except Exception as e:
        logger.info("theaters: render of %s failed (%s)", page_url[:120], e)
    return box


_TITLE_TAG_RE = re.compile(r'<title[^>]*>([^<]{0,120})', re.I)


def looks_walled(text: str) -> bool:
    """True when a body is a bot-check page rather than the site.

    Reads the page title and the challenge markers themselves, never
    the bare word 'captcha': real theater pages load reCAPTCHA for
    their sign-up forms (Malco's mention it 7,600 characters in), and
    a page that merely carries the script is not a wall."""
    body = text or ''
    head = body[:8000].lower()
    m = _TITLE_TAG_RE.search(body[:8000])
    title = (m.group(1) if m else '').strip().lower()
    if any(w in title for w in ('just a moment', 'attention required',
                                'access denied', 'verify you are human',
                                'are you a robot', 'security check')):
        return True
    # Not '/cdn-cgi/challenge-platform/': Cloudflare loads that script on
    # ordinary pages too. `_cf_chl_opt` only exists on the challenge.
    if any(w in head for w in ('_incapsula_resource', 'incapsula incident',
                               'cf-challenge', '_cf_chl_opt',
                               'px-captcha', 'captcha-delivery.com')):
        return True
    # A tiny body that is only a challenge (no site around it).
    return len(body) < 3000 and ('captcha' in head or 'hcaptcha' in head)
