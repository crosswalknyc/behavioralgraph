"""
Business news scraper - top articles from the business desks of the
New York Times and Wall Street Journal.

Transport per outlet:

  - NYT Business  : native RSS  (rss.nytimes.com/.../Business.xml)
                    Public, fresh, 50 items/pull with media:content
                    thumbnails.

  - WSJ Business  : Google News RSS proxy.  WSJ deprecated its public
                    RSS feeds (RSSWorldNews, WSJcomUSBusiness, etc.
                    all haven't updated since Jan 2025), so we go
                    through Google News with a `site:wsj.com when:1d`
                    query.  100 fresh items/pull; URLs are Google
                    News redirect links that resolve to wsj.com when
                    clicked.

No cookies required for either transport (both are public).  WSJ
articles themselves are paywalled but the RSS metadata (title, url,
publish time, description) is free.

Snapshot shape (kind='news'):

    {
      "source":     "business_news",
      "kind":       "news",
      "label":      "Business news",
      "fetched_at": "...",
      "national":   [{ rank, title, url, source, source_label,
                        domain, published, image? }, ...],
      "by_source":  { "nyt_business": [...], "wsj_business": [...] }
    }

Standalone:

    python3 -m scripts.trends_scrapers.business_news
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
    gone stale."""
    q = urllib.parse.quote(f'{site_query} when:1d', safe=':')
    return (f'https://news.google.com/rss/search?q={q}'
             '&hl=en-US&gl=US&ceid=US:en')


# (source_slug, display_label, rss_url, domain_for_favicon)
_FEEDS: list[tuple[str, str, str, str]] = [
    ('nyt_business',
        'New York Times Business',
        'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',
        'nytimes.com'),
    ('wsj_business',
        'Wall Street Journal Business',
        # `site:wsj.com` scoped to the business/markets URL prefixes so
        # we don't fold in WSJ's lifestyle / opinion / world coverage.
        _gn_query('(site:wsj.com/articles OR site:wsj.com/business '
                   'OR site:wsj.com/economy OR site:wsj.com/markets '
                   'OR site:wsj.com/tech OR site:wsj.com/finance)'),
        'wsj.com'),
]


# Per-feed cap keeps NYT (50 items) and WSJ-via-GN (100 items) from
# fighting for a fixed slot count in the combined view.
_PER_FEED_CAP = 20
_TOTAL_CAP    = 40


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
        logger.info("business_news %s: %s", url, e)
        return ''
    if not r.ok:
        logger.info("business_news %s: http %s", url, r.status_code)
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
# GN suffixes every headline with " - <Outlet>" (e.g. "Boeing to Fold Its
# Flying-Taxi Venture Into Rival Archer Aviation - WSJ").  We strip that
# because the source label is already carried on the row via
# `source_label` and the visible " - WSJ" is redundant noise on the card.
_GN_TITLE_SUFFIX_RE = re.compile(
    r'\s*[-–—]\s*(WSJ|The Wall Street Journal|WSJ\.com)\s*$'
)


def _strip_gn_title_suffix(title: str) -> str:
    return _GN_TITLE_SUFFIX_RE.sub('', title or '').strip()


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
            # WSJ is piped through Google News, so strip GN's outlet
            # suffix from the title for a clean card render.  URLs
            # stay as-is (GN redirect resolves to wsj.com on click).
            if slug == 'wsj_business':
                r['title'] = _strip_gn_title_suffix(r['title'])
        if rows:
            by_source[slug] = rows
        combined.extend(rows)
        logger.info("business_news %s: %d items", slug, len(rows))

    if not combined:
        return {
            'national':  [],
            'available': False,
            'by_source': {},
            'error':     'all business feeds returned empty',
        }

    # Dedupe by URL then by lowercased title (GN sometimes echoes an
    # outlet's own headline verbatim across two of its taxonomy paths).
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
    result = run_scraper('business_news', 'Business news', 'news', fetch)
    print(f"business_news: national={len(result.get('national', []))} "
           f"error={result.get('error')}",
           file=sys.stderr)
    for r in (result.get('national') or [])[:12]:
        print(f"  #{r['rank']:>2} [{r['source_label'][:26]:<26}] "
               f"{r['title'][:75]}",
               file=sys.stderr)
