"""
Philanthropy news scraper.

Aggregates the four biggest free philanthropy news RSS feeds into a
single deduped, chronologically-ranked list. Each source has a
different angle:

  - Chronicle of Philanthropy  -> sector-wide news (grants, foundations, policy)
  - Nonprofit Quarterly        -> policy, movement building, systemic
  - Stanford SSIR              -> academic / systems-change lens
  - Blue Avocado               -> practical nonprofit operations
  - Guardian Global Development-> international aid + development

Snapshot shape (kind='news'):

    {
      "source":     "philanthropy_news",
      "kind":       "news",
      "label":      "Philanthropy news",
      "fetched_at": "...",
      "national":   [{ rank, title, url, source, source_label,
                        published, image? }, ...],
      "by_source":  { "chronicle": [...], "npq": [...], ... }
    }

Standalone:

    python3 -m scripts.trends_scrapers.philanthropy_news
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
import xml.etree.ElementTree as ET
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')

# (source_slug, display_label, rss_url, domain_for_favicon)
_FEEDS: list[tuple[str, str, str, str]] = [
    ('chronicle',       'Chronicle of Philanthropy',
        'https://www.philanthropy.com/rss',
        'philanthropy.com'),
    ('npq',             'Nonprofit Quarterly',
        'https://nonprofitquarterly.org/feed/',
        'nonprofitquarterly.org'),
    ('ssir',            'Stanford Social Innovation Review',
        'https://ssir.org/site/rss_2.0',
        'ssir.org'),
    ('blueavocado',     'Blue Avocado',
        'https://blueavocado.org/feed/',
        'blueavocado.org'),
    ('guardian_globdev','Guardian Global Development',
        'https://www.theguardian.com/global-development/rss',
        'theguardian.com'),
]


# Per-feed cap keeps a single prolific site (Guardian pushes 45 items /
# day) from crowding the combined view.
_PER_FEED_CAP = 12
_TOTAL_CAP    = 40


def _fetch_body(url: str, *, timeout: int = 15) -> str:
    try:
        r = requests.get(url,
                          headers={'User-Agent': _UA,
                                    'Accept': 'application/rss+xml, application/atom+xml, text/xml, */*'},
                          timeout=timeout,
                          allow_redirects=True)
    except Exception as e:
        logger.info("philanthropy_news %s: %s", url, e)
        return ''
    if not r.ok:
        logger.info("philanthropy_news %s: http %s", url, r.status_code)
        return ''
    return r.text or ''


def _parse_via_elementtree(body: str) -> list[dict]:
    """Standard RSS 2.0 / Atom parse. Returns [] if the feed has an
    unbound-prefix namespace or other structural quirk."""
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
        # NOTE: `elem or fallback` is unreliable for ElementTree - an
        # element with no children is falsy even when it exists. Always
        # use explicit `is not None` chains here.
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
    """Regex fallback for feeds with malformed namespaces (Chronicle of
    Philanthropy hits this because it declares an atom prefix in the
    feed body without binding it)."""
    if not body:
        return []
    items = re.findall(r'<item\b[^>]*>([\s\S]*?)</item>', body, flags=re.IGNORECASE)
    if not items:
        items = re.findall(r'<entry\b[^>]*>([\s\S]*?)</entry>', body, flags=re.IGNORECASE)
    out = []
    for chunk in items:
        t = re.search(r'<title[^>]*>\s*(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?\s*</title>', chunk, flags=re.IGNORECASE)
        if not t:
            continue
        title = _html.unescape(re.sub(r'\s+', ' ', t.group(1)).strip())
        if not title:
            continue
        l = re.search(r'<link[^>]*>\s*([^<]+)\s*</link>', chunk, flags=re.IGNORECASE)
        link = (l.group(1).strip() if l else '')
        if not link:
            # Atom-style link
            la = re.search(r'<link[^>]*href="([^"]+)"', chunk, flags=re.IGNORECASE)
            if la:
                link = la.group(1).strip()
        d = re.search(r'<pubDate>([^<]+)</pubDate>', chunk, flags=re.IGNORECASE)
        published = d.group(1).strip() if d else ''
        img = re.search(r'<media:(?:content|thumbnail)[^>]*url="([^"]+)"', chunk, flags=re.IGNORECASE)
        image = img.group(1) if img else ''
        if not image:
            # Try <img> inside description
            desc = re.search(r'<description>\s*(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?\s*</description>', chunk, flags=re.IGNORECASE)
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
    """Try ElementTree first, fall back to regex."""
    items = _parse_via_elementtree(body)
    if items:
        return items
    return _parse_via_regex(body)


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
        if rows:
            by_source[slug] = rows
        combined.extend(rows)
        logger.info("philanthropy_news %s: %d items", slug, len(rows))

    if not combined:
        return {
            'national':  [],
            'available': False,
            'by_source': {},
            'error':     'all philanthropy feeds returned empty',
        }

    # Dedupe by URL (highest fidelity) then title (for feeds that
    # syndicate each other with URL variants). First occurrence wins.
    seen_urls:   set[str] = set()
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for r in combined:
        url_key = (r.get('url') or '').strip().lower().rstrip('/')
        title_key = re.sub(r'[^\w\s]+', ' ', (r.get('title') or '').lower()).strip()
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        if url_key:   seen_urls.add(url_key)
        if title_key: seen_titles.add(title_key)
        deduped.append(r)

    # Stamp final rank on the deduped list.
    for i, r in enumerate(deduped[:_TOTAL_CAP], start=1):
        r['rank'] = i

    return {
        'national':  deduped[:_TOTAL_CAP],
        'available': True,
        'by_source': by_source,
    }


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('philanthropy_news', 'Philanthropy news', 'news', fetch)
    print(f"philanthropy_news: national={len(result.get('national', []))} error={result.get('error')}",
           file=sys.stderr)
    for r in (result.get('national') or [])[:12]:
        print(f"  #{r['rank']:>2} [{r['source_label'][:20]:<20}] {r['title'][:75]}",
               file=sys.stderr)
