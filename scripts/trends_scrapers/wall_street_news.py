"""
Wall Street news scraper - top articles from the Wall-Street-heavy
publications that cover markets, macro, corporate finance, options,
crypto, deals, earnings, and investor-facing analysis.

The user asked for a Wall Street breakout for Headlines that works
"like Business" (the NYT + WSJ desk feed). This scraper picks up
alongside it and serves the Wall Street-focused sub-tab.

Transport per outlet (mirrors the split established in
`business_news.py`):

  - MarketWatch      : native RSS (top stories, free)
  - CNBC Markets     : native RSS (id 15839135 is CNBC's Investing /
                        Markets vertical, free)
  - Investor's Business Daily : native RSS (free)
  - Seeking Alpha    : native RSS (market currents, free)

  - Wall Street Journal (Markets) : Google News RSS proxy scoped to
                                     WSJ markets / finance / economy
                                     URL prefixes. WSJ deprecated its
                                     public feeds; GN metadata is free.
  - Barron's         : Google News RSS proxy scoped to barrons.com.
                        Paywalled outlet; GN metadata is free.
  - Financial Times  : Google News RSS proxy scoped to ft.com.
                        Paywalled outlet; GN metadata is free.
  - Bloomberg Markets: Google News RSS proxy scoped to bloomberg.com
                        markets / news paths. Bloomberg's public RSS
                        largely gone; GN metadata is free.
  - Reuters Markets  : Google News RSS proxy scoped to reuters.com
                        markets / business paths. Reuters retired its
                        public RSS; GN metadata is free.

No cookies required for any transport (all are public metadata). The
full articles behind WSJ / Barron's / FT / Bloomberg are paywalled at
click time, but the RSS/GN metadata (title, url, publish time,
description) is free to fetch and index.

If a native-RSS outlet ever needs cookies to bypass a bot wall (WSJ
donation would light up direct feeds, for example), wire the same
`cookie_domain=<host>` pattern the retailer scrapers use. Ships
without any cookies on day one so the sub-tab is populated even
before any donation exists.

Snapshot shape (kind='news'):

    {
      "source":     "wall_street_news",
      "kind":       "news",
      "label":      "Wall Street news",
      "fetched_at": "...",
      "national":   [{ rank, title, url, source, source_label,
                        domain, published, image? }, ...],
      "by_source":  { "marketwatch": [...], "cnbc_markets": [...],
                       "wsj_markets": [...], ... }
    }

Standalone:

    python3 -m scripts.trends_scrapers.wall_street_news
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import requests

logger = logging.getLogger(__name__)


_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')


def _gn_query(site_query: str) -> str:
    """Build a Google News RSS search URL that returns the last 24
    hours of results for a given site query.  We use GN as a
    freshness-guaranteed proxy for outlets whose own RSS feeds have
    gone stale or that gate their metadata behind a paywall."""
    q = urllib.parse.quote(f'{site_query} when:1d', safe=':')
    return (f'https://news.google.com/rss/search?q={q}'
             '&hl=en-US&gl=US&ceid=US:en')


# (source_slug, display_label, rss_url, domain_for_favicon)
#
# Order matters: the deduped combined list keeps FIRST occurrence, so
# native-RSS outlets come first (fresher, no GN redirect on the URL),
# then the Google-News-proxied paywalled outlets. That way a story
# picked up by both MarketWatch (native) and Reuters Markets (GN)
# renders with the MarketWatch attribution / direct link.
_FEEDS: list[tuple[str, str, str, str]] = [
    # ---------- Native RSS (free, fresh) ----------
    ('marketwatch',
        'MarketWatch',
        'https://feeds.content.dowjones.io/public/rss/mw_topstories',
        'marketwatch.com'),
    ('cnbc_markets',
        'CNBC Markets',
        # CNBC's Investing / Markets feed. Public RSS, no cookies.
        'https://search.cnbc.com/rs/search/combinedcms/view.xml'
        '?partnerId=wrss01&id=15839135',
        'cnbc.com'),
    ('ibd',
        "Investor's Business Daily",
        'https://www.investors.com/feed/',
        'investors.com'),
    ('seeking_alpha',
        'Seeking Alpha',
        'https://seekingalpha.com/market_currents.xml',
        'seekingalpha.com'),

    # ---------- Google News proxy (paywalled or dead-RSS) ----------
    ('wsj_markets',
        'Wall Street Journal Markets',
        _gn_query('(site:wsj.com/articles OR site:wsj.com/markets '
                   'OR site:wsj.com/finance OR site:wsj.com/economy '
                   'OR site:wsj.com/business)'),
        'wsj.com'),
    ('barrons',
        "Barron's",
        _gn_query('site:barrons.com'),
        'barrons.com'),
    ('ft',
        'Financial Times',
        _gn_query('site:ft.com'),
        'ft.com'),
    ('bloomberg_markets',
        'Bloomberg Markets',
        # Scope to bloomberg.com/news + /markets so we don't fold in
        # opinion columns and green-tech features that would dilute
        # the Wall Street signal.
        _gn_query('(site:bloomberg.com/news OR site:bloomberg.com/markets)'),
        'bloomberg.com'),
    ('reuters_markets',
        'Reuters Markets',
        _gn_query('(site:reuters.com/markets OR site:reuters.com/business)'),
        'reuters.com'),
]


# Per-feed cap keeps a single prolific outlet (GN-via-WSJ can push
# ~100 items/day) from crowding the combined view; 12/feed x 9 feeds
# still leaves plenty of room after dedupe under the 50 total cap.
_PER_FEED_CAP = 12
_TOTAL_CAP    = 50


def _fetch_body(url: str, *, timeout: int = 15) -> str:
    try:
        r = requests.get(url,
                          headers={'User-Agent': _UA,
                                    'Accept': ('application/rss+xml, '
                                                'application/atom+xml, '
                                                'text/xml, */*')},
                          timeout=timeout,
                          allow_redirects=True)
    except Exception as e:
        logger.info("wall_street_news %s: %s", url, e)
        return ''
    if not r.ok:
        logger.info("wall_street_news %s: http %s", url, r.status_code)
        return ''
    return r.text or ''


def _parse_via_elementtree(body: str) -> list[dict]:
    """Standard RSS 2.0 / Atom parse."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    ns = {
        'media':   'http://search.yahoo.com/mrss/',
        'content': 'http://purl.org/rss/1.0/modules/content/',
        'atom':    'http://www.w3.org/2005/Atom',
    }
    items = list(root.iter('item'))
    if not items:
        items = list(root.iter('{http://www.w3.org/2005/Atom}entry'))
    out = []
    for it in items:
        title_el = it.find('title')
        if title_el is None:
            title_el = it.find('{http://www.w3.org/2005/Atom}title')
        title = (title_el.text or '').strip() if title_el is not None else ''
        if not title:
            continue
        link_el = it.find('link')
        if link_el is None:
            link_el = it.find('{http://www.w3.org/2005/Atom}link')
        if link_el is not None:
            link = (link_el.text or '').strip() or link_el.get('href', '')
        else:
            link = ''
        date_el = it.find('pubDate')
        if date_el is None:
            date_el = it.find('{http://purl.org/dc/elements/1.1/}date')
        if date_el is None:
            date_el = it.find('{http://www.w3.org/2005/Atom}published')
        if date_el is None:
            date_el = it.find('{http://www.w3.org/2005/Atom}updated')
        published = (date_el.text or '').strip() if date_el is not None else ''
        image = ''
        mc = it.find('media:content', ns)
        if mc is None:
            mc = it.find('media:thumbnail', ns)
        if mc is not None:
            image = mc.get('url', '') or ''
        if not image:
            desc_el = it.find('description')
            if desc_el is not None and desc_el.text:
                m = re.search(r'<img[^>]+src="([^"]+)"', desc_el.text)
                if m:
                    image = m.group(1)
        out.append({
            'title':     _html.unescape(title[:280]),
            'url':       link,
            'published': published,
            'image':     image,
        })
    return out


def _parse_via_regex(body: str) -> list[dict]:
    """Regex fallback for feeds with malformed namespaces."""
    if not body:
        return []
    items = re.findall(r'<item\b[^>]*>([\s\S]*?)</item>', body, flags=re.IGNORECASE)
    if not items:
        items = re.findall(r'<entry\b[^>]*>([\s\S]*?)</entry>', body, flags=re.IGNORECASE)
    out = []
    for chunk in items:
        t = re.search(r'<title[^>]*>\s*(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?\s*</title>',
                       chunk, flags=re.IGNORECASE)
        if not t:
            continue
        title = _html.unescape(re.sub(r'\s+', ' ', t.group(1)).strip())
        if not title:
            continue
        l = re.search(r'<link[^>]*>\s*([^<]+)\s*</link>', chunk, flags=re.IGNORECASE)
        link = (l.group(1).strip() if l else '')
        if not link:
            la = re.search(r'<link[^>]*href="([^"]+)"', chunk, flags=re.IGNORECASE)
            if la:
                link = la.group(1).strip()
        d = re.search(r'<pubDate>([^<]+)</pubDate>', chunk, flags=re.IGNORECASE)
        published = d.group(1).strip() if d else ''
        img = re.search(r'<media:(?:content|thumbnail)[^>]*url="([^"]+)"',
                          chunk, flags=re.IGNORECASE)
        image = img.group(1) if img else ''
        if not image:
            desc = re.search(r'<description>\s*(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?\s*</description>',
                               chunk, flags=re.IGNORECASE)
            if desc:
                m = re.search(r'<img[^>]+src="([^"]+)"', desc.group(1))
                if m:
                    image = m.group(1)
        out.append({
            'title':     title[:280],
            'url':       link,
            'published': published,
            'image':     image,
        })
    return out


def _parse_feed(body: str) -> list[dict]:
    items = _parse_via_elementtree(body)
    if items:
        return items
    return _parse_via_regex(body)


# ---------------------------------------------------------------------------
# Google News title / URL post-processing
# ---------------------------------------------------------------------------
# GN suffixes every headline with " - <Outlet>" (e.g. "Fed pauses on
# rate cut path - Bloomberg").  We strip that because the source label
# is already carried on the row via `source_label`.
_GN_TITLE_SUFFIX_RE = re.compile(
    r'\s*[-\u2010-\u2015]\s*(WSJ|The Wall Street Journal|WSJ\.com|'
    r"Barron's|Barrons|"
    r'Financial Times|FT\.com|FT|'
    r'Bloomberg(?: News| Markets)?|'
    r'Reuters|Reuters Markets|Reuters Business)\s*$'
)


def _strip_gn_title_suffix(title: str) -> str:
    return _GN_TITLE_SUFFIX_RE.sub('', title or '').strip()


# Slugs whose transport is Google News (title needs de-suffixing).
_GN_SLUGS = {
    'wsj_markets', 'barrons', 'ft', 'bloomberg_markets', 'reuters_markets',
}


def fetch() -> dict[str, Any]:
    combined: list[dict] = []
    by_source: dict[str, list[dict]] = {}
    for slug, label, url, domain in _FEEDS:
        body = _fetch_body(url)
        rows = _parse_feed(body)[:_PER_FEED_CAP]
        for r in rows:
            r['source']       = slug
            r['source_label'] = label
            r['domain']       = domain
            if slug in _GN_SLUGS:
                r['title'] = _strip_gn_title_suffix(r['title'])
        if rows:
            by_source[slug] = rows
        combined.extend(rows)
        logger.info("wall_street_news %s: %d items", slug, len(rows))

    if not combined:
        return {
            'national':  [],
            'available': False,
            'by_source': {},
            'error':     'all wall_street feeds returned empty',
        }

    # Dedupe by URL then by lowercased normalized title. GN sometimes
    # syndicates the same headline across multiple site scopes; also
    # WSJ / MarketWatch cross-publish under Dow Jones so the same
    # markets story can hit two of our feeds. First occurrence wins,
    # and native-RSS outlets are ordered first, so a cross-published
    # story renders with the free-to-click attribution.
    seen_urls:   set[str] = set()
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for r in combined:
        url_key   = (r.get('url') or '').strip().lower().rstrip('/')
        title_key = re.sub(r'[^\w\s]+', ' ', (r.get('title') or '').lower()).strip()
        if url_key   and url_key   in seen_urls:   continue
        if title_key and title_key in seen_titles: continue
        if url_key:   seen_urls.add(url_key)
        if title_key: seen_titles.add(title_key)
        deduped.append(r)

    for i, r in enumerate(deduped[:_TOTAL_CAP], start=1):
        r['rank'] = i

    return {
        'national':  deduped[:_TOTAL_CAP],
        'available': True,
        'by_source': by_source,
    }


if __name__ == '__main__':
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO,
                          format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('wall_street_news', 'Wall Street news', 'news', fetch)
    print(f"wall_street_news: national={len(result.get('national', []))} "
           f"error={result.get('error')}",
           file=sys.stderr)
    for r in (result.get('national') or [])[:12]:
        print(f"  #{r['rank']:>2} [{r['source_label'][:26]:<26}] "
               f"{r['title'][:75]}",
               file=sys.stderr)
