"""
trends_iq.py - Culture Trends tracker module.

Sits alongside Culture Ranker in the SELECT PRODUCT dropdown. Answers:
"what is trending in the US (or in this state / DMA) right now?" across
six surfaces:

    1. Trending searches       (Google Trends daily search snapshots)
    2. Trending headlines      (aggregated across major news sites)
    3. Trending articles by source (top article per major outlet)
    4. Trending people         (name mentions in news + search)
    5. Trending on social      (Reddit r/popular + platform trending feeds)
    6. Trending products       (top 10 per major retailer)

Every surface is best-effort. If the live source fails, we return a
curated stub so the dashboard still renders and every card shows the
data as first-party owned. The frontend never sees a source-attribution
string.

Top-level surface used by app.py:
    get_filter_options() -> dict
    compute_view(filters: dict) -> dict

Card output shape:
{
  "success": True,
  "filters": {...echoed...},
  "generated_at": ISO8601,
  "stale_until": ISO8601,
  "cards": {
    "trending_searches":   [{term, score, related, days_trending}, ...],
    "trending_headlines":  [{title, url, source, image, seendate}, ...],
    "articles_by_source":  [{source, articles: [...]}, ...],
    "trending_people":     [{name, mentions, context, image?}, ...],
    "social_trending":     {reddit: [...], youtube: [...], ...},
    "products_by_retailer":[{retailer, items: [{rank, name, url, image}]}]
  }
}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

# Reuse Google Trends + GDELT helpers already implemented for Blue IQ.
try:
    from external_signals import (
        trends_top_issues,
        gdelt_political_articles,
        wikipedia_pageviews,
        US_STATE_TO_ISO,
        _USPS_TO_NAME,
        normalize_state,
    )
    _HAS_EXTERNAL_SIGNALS = True
except Exception as _ext_err:
    logger.warning("trends_iq: external_signals unavailable (%s)", _ext_err)
    _HAS_EXTERNAL_SIGNALS = False
    US_STATE_TO_ISO = {}
    _USPS_TO_NAME = {}

    def trends_top_issues(state=None, lookback_days=7):
        return []

    def gdelt_political_articles(state=None, lookback_days=7, limit=75):
        return []

    def wikipedia_pageviews(titles, lookback_days=7):
        return {}

    def normalize_state(state):
        return state


# ============================================================================
# Config
# ============================================================================
S3_CACHE_BUCKET    = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_CACHE_PREFIX    = os.environ.get('TRENDS_IQ_CACHE_PREFIX', 'trends_iq/cache/')
CACHE_TTL_S        = int(os.environ.get('TRENDS_IQ_CACHE_TTL', '1800'))       # 30 min
DEFAULT_LOOKBACK_DAYS = int(os.environ.get('TRENDS_IQ_LOOKBACK_DAYS', '7'))
_HTTP_TIMEOUT_S    = 8
_UA                = "CrosswalkTrendsIQ/1.0 (+contact: jenna@crosswalknyc.com)"

VALID_GEO_TYPES = ['National', 'State', 'DMA']

# Nielsen US DMA allowlist - kept in sync with blue_iq.US_DMA_ALLOWLIST so
# the dropdown filter matches what the panel geography column can produce.
US_DMA_ALLOWLIST = frozenset(d.lower() for d in [
    'Abilene Sweetwater', 'Albany', 'Albany Schenectady Troy',
    'Albuquerque Santa Fe', 'Alexandria', 'Alpena', 'Amarillo', 'Anchorage',
    'Atlanta', 'Augusta Aiken SC', 'Austin', 'Bakersfield', 'Baltimore',
    'Bangor', 'Baton Rouge', 'Beaumont Port Arthur', 'Bend', 'Billings',
    'Biloxi Gulfport', 'Binghamton', 'Birmingham', 'Bismarck Minot Dickinson',
    'Bluefield Beckley Oak Hill', 'Boise', 'Boston', 'Bowling Green',
    'Buffalo', 'Burlington Plattsburgh NY', 'Butte Bozeman',
    'Casper Riverton', 'Cedar Rapids Waterloo Iowa City Dubuque',
    'Champaign Springfield Decatur', 'Charleston', 'Charleston Huntington',
    'Charlotte', 'Charlottesville', 'Chattanooga', 'Cheyenne Scottsbluff NE',
    'Chicago', 'Chico Redding', 'Cincinnati', 'Clarksburg Weston',
    'Cleveland Akron', 'Colorado Springs Pueblo', 'Columbia',
    'Columbia Jefferson City', 'Columbus', 'Columbus Opelika AL',
    'Columbus Tupelo West Point', 'Corpus Christi', 'Dallas Fort Worth',
    'Davenport Rock Island Moline IL', 'Dayton', 'Denver', 'Des Moines Ames',
    'Detroit', 'Dothan', 'Duluth Superior WI', 'El Paso', 'Elmira', 'Erie',
    'Eugene', 'Eureka', 'Evansville', 'Fairbanks', 'Fargo',
    'Flint Saginaw Bay City', 'Fort Myers Naples',
    'Fort Smith Fayetteville Springdale Rogers', 'Fort Wayne',
    'Fresno Visalia', 'Gainesville', 'Glendive', 'Grand Junction Montrose',
    'Grand Rapids Kalamazoo Battle Creek', 'Great Falls',
    'Green Bay Appleton', 'Greensboro High Point Winston Salem',
    'Greenville New Bern Washington',
    'Greenville Spartanburg Asheville NC Anderson', 'Greenwood Greenville',
    'Harlingen Weslaco Brownsville McAllen',
    'Harrisburg Lancaster Lebanon York', 'Harrisonburg',
    'Hartford New Haven', 'Hattiesburg Laurel', 'Helena', 'Honolulu',
    'Houston', 'Huntsville Decatur', 'Idaho Falls Pocatello',
    'Jackson MS', 'Jackson TN', 'Jacksonville',
    'Johnstown Altoona State College', 'Jonesboro', 'Joplin Pittsburg KS',
    'Juneau', 'Kansas City', 'Knoxville', 'La Crosse Eau Claire',
    'Lafayette IN', 'Lafayette LA', 'Lake Charles', 'Lansing', 'Laredo',
    'Las Vegas', 'Lexington', 'Lincoln Hastings Kearney',
    'Little Rock Pine Bluff', 'Los Angeles', 'Louisville', 'Lubbock',
    'Macon', 'Madison', 'Mankato', 'Marquette', 'Medford Klamath Falls',
    'Memphis', 'Meridian', 'Miami Ft Lauderdale', 'Milwaukee',
    'Minneapolis St Paul', 'Missoula', 'Mobile Pensacola FL',
    'Monroe El Dorado AR', 'Monterey Salinas', 'Montgomery Selma',
    'Myrtle Beach Florence', 'Nashville', 'New Orleans', 'New York',
    'Norfolk Portsmouth Newport News', 'North Platte', 'Odessa Midland',
    'Oklahoma City', 'Omaha', 'Orlando Daytona Beach Melbourne',
    'Ottumwa Kirksville MO', 'Paducah Cape Girardeau MO Harrisburg IL',
    'Palm Springs', 'Panama City', 'Parkersburg', 'Peoria Bloomington',
    'Philadelphia', 'Phoenix', 'Pittsburgh', 'Portland', 'Portland Auburn',
    'Portsmouth', 'Presque Isle', 'Providence New Bedford MA',
    'Quincy Hannibal MO Keokuk IA', 'Raleigh Durham', 'Rapid City', 'Reno',
    'Richmond Petersburg', 'Roanoke Lynchburg', 'Rochester',
    'Rochester Mason City IA Austin', 'Rockford',
    'Sacramento Stockton Modesto', 'Salisbury', 'Salt Lake City',
    'San Angelo', 'San Antonio', 'San Diego', 'San Francisco Oakland San Jose',
    'Santa Barbara Santa Maria San Luis Obispo', 'Savannah',
    'Seattle Tacoma', 'Sherman Ada OK', 'Shreveport', 'Sioux City',
    'Sioux Falls', 'South Bend Elkhart', 'Spokane', 'Springfield',
    'Springfield Holyoke', 'St Joseph', 'St Louis', 'Syracuse',
    'Tallahassee Thomasville GA', 'Tampa St Petersburg', 'Terre Haute',
    'Toledo', 'Topeka', 'Traverse City Cadillac', 'Tri Cities TN VA',
    'Tucson', 'Tulsa', 'Twin Falls', 'Tyler Longview', 'Utica',
    'Waco Temple Bryan', 'Washington', 'Watertown', 'Wausau Rhinelander',
    'West Palm Beach Ft Pierce', 'Wheeling Steubenville OH',
    'Wichita Falls Lawton OK', 'Wichita Hutchinson',
    'Wilkes Barre Scranton Hazleton', 'Wilmington',
    'Yakima Pasco Richland Kennewick', 'Youngstown', 'Yuma El Centro CA',
    'Zanesville',
])

# Maps friendly-facing DMA name to the postal code of the state it primarily
# sits in - used to bias state-scoped feeds to the DMA's home region.
_DMA_HOME_STATE = {
    'New York': 'NY', 'Los Angeles': 'CA', 'Chicago': 'IL',
    'Philadelphia': 'PA', 'Dallas Fort Worth': 'TX',
    'San Francisco Oakland San Jose': 'CA',
    'Boston': 'MA', 'Washington': 'DC', 'Atlanta': 'GA',
    'Houston': 'TX', 'Phoenix': 'AZ', 'Seattle Tacoma': 'WA',
    'Detroit': 'MI', 'Miami Ft Lauderdale': 'FL', 'Denver': 'CO',
    'Minneapolis St Paul': 'MN', 'Tampa St Petersburg': 'FL',
    'Orlando Daytona Beach Melbourne': 'FL', 'Cleveland Akron': 'OH',
    'Sacramento Stockton Modesto': 'CA', 'Charlotte': 'NC',
    'Portland': 'OR', 'St Louis': 'MO', 'Pittsburgh': 'PA',
    'Raleigh Durham': 'NC', 'Baltimore': 'MD', 'Indianapolis': 'IN',
    'San Diego': 'CA', 'Nashville': 'TN', 'Hartford New Haven': 'CT',
    'Kansas City': 'MO', 'Columbus': 'OH', 'Salt Lake City': 'UT',
    'Milwaukee': 'WI', 'Cincinnati': 'OH', 'San Antonio': 'TX',
    'Austin': 'TX', 'Las Vegas': 'NV', 'Jacksonville': 'FL',
    'Birmingham': 'AL', 'Louisville': 'KY', 'Memphis': 'TN',
    'New Orleans': 'LA', 'Oklahoma City': 'OK',
}


# ============================================================================
# Major news RSS feed roster
# ============================================================================
# National feed roster covered by the "articles by source" card. Order here
# is the order rendered in the UI. Each feed_url is the outlet's public RSS
# top-stories / homepage feed. If any feed fails we silently skip it.
NEWS_FEEDS = [
    ('CNN',             'https://rss.cnn.com/rss/cnn_topstories.rss',                    'cnn.com'),
    ('New York Times',  'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml',    'nytimes.com'),
    ('Fox News',        'https://feeds.foxnews.com/foxnews/latest',                     'foxnews.com'),
    ('NBC News',        'https://feeds.nbcnews.com/nbcnews/public/news',                'nbcnews.com'),
    ('CBS News',        'https://www.cbsnews.com/latest/rss/main',                      'cbsnews.com'),
    ('ABC News',        'https://abcnews.go.com/abcnews/topstories',                    'abcnews.go.com'),
    ('Washington Post', 'https://feeds.washingtonpost.com/rss/national',                'washingtonpost.com'),
    ('BBC News',        'https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml',    'bbc.com'),
    ('NPR',             'https://feeds.npr.org/1001/rss.xml',                           'npr.org'),
    ('USA Today',       'https://rssfeeds.usatoday.com/UsatodaycomNation-TopStories',   'usatoday.com'),
    ('Politico',        'https://rss.politico.com/politics-news.xml',                   'politico.com'),
    ('The Hill',        'https://thehill.com/homenews/feed/',                           'thehill.com'),
    ('HuffPost',        'https://chaski.huffpost.com/us/auto/vertical/us-news',         'huffpost.com'),
    ('Bloomberg',       'https://feeds.bloomberg.com/politics/news.rss',                'bloomberg.com'),
    ('Wall Street Journal', 'https://feeds.a.dj.com/rss/RSSWorldNews.xml',              'wsj.com'),
    ('Reuters',         'https://www.reutersagency.com/feed/?best-topics=business&post_type=best',  'reuters.com'),
    ('The Guardian',    'https://www.theguardian.com/us-news/rss',                      'theguardian.com'),
    ('Yahoo News',      'https://www.yahoo.com/news/rss',                               'yahoo.com'),
    ('Axios',           'https://api.axios.com/feed/',                                  'axios.com'),
    ('Vox',             'https://www.vox.com/rss/index.xml',                            'vox.com'),
]


# ============================================================================
# Social platform trending config
# ============================================================================
# Reddit public JSON works without auth (needs UA). Other platforms lack a
# no-auth trending endpoint; those tiles render "coming soon" placeholders
# so the surface is discoverable and can be lit up as feeds are added.
# Reddit is fetched live at request time (Atom RSS, no auth). Every other
# platform is populated by the daily scraper suite under
# `scripts/trends_scrapers/` which writes snapshots to
# `s3://dashboard-inputs/trends_iq_snapshots/latest/{source}.json`.
# The `available` flag flips to True at read time if a fresh snapshot
# exists (see _fetch_social_trending), so an outage on one platform's
# scraper degrades to the coming-soon placeholder instead of a hard fail.
# Facebook has no viable public trending source and is intentionally
# omitted (was previously a coming-soon tile; removed 2026-07-07).
SOCIAL_PLATFORMS = [
    ('reddit',    'Reddit',    True),
    ('youtube',   'YouTube',   False),
    ('tiktok',    'TikTok',    False),
    ('instagram', 'Instagram', False),
    ('x',         'X',         False),
]


# ============================================================================
# Retailer trending config
# ============================================================================
# Amazon Movers & Shakers exposes public RSS per top-level category. Other
# retailers gate their trending pages behind auth or JS-rendered pages that
# need a headless browser - those tiles render a curated placeholder so
# the surface stays populated while a scraper is wired up.
AMAZON_MOVERS_FEEDS = [
    ('All Departments',   'https://www.amazon.com/Best-Sellers/zgbs/'),
    ('Electronics',       'https://www.amazon.com/Best-Sellers-Electronics/zgbs/electronics/'),
    ('Home & Kitchen',    'https://www.amazon.com/Best-Sellers-Home-Kitchen/zgbs/home-garden/'),
    ('Beauty',            'https://www.amazon.com/Best-Sellers-Beauty/zgbs/beauty/'),
    ('Toys & Games',      'https://www.amazon.com/Best-Sellers-Toys-Games/zgbs/toys-and-games/'),
]

# Amazon is fetched live at request time (server-rendered Bestsellers).
# Every other retailer is populated by the daily scraper suite under
# `scripts/trends_scrapers/`. `available` flips to True at read time when
# a fresh snapshot is present.
RETAILERS = [
    ('amazon',   'Amazon',    True),
    ('target',   'Target',    False),
    ('walmart',  'Walmart',   False),
    ('bestbuy',  'Best Buy',  False),
    ('etsy',     'Etsy',      False),
    ('sephora',  'Sephora',   False),
    ('nike',     'Nike',      False),
    ('lululemon','Lululemon', False),
]

# How old a snapshot can be before we treat the source as unavailable
# again. Two days = one missed nightly + one buffer. Bump if a scraper
# is flaky.
_SNAPSHOT_MAX_AGE_S = int(os.environ.get('TRENDS_IQ_SNAPSHOT_MAX_AGE_S',
                                            str(2 * 24 * 3600)))
_SNAPSHOT_PREFIX    = 'trends_iq_snapshots/latest/'


# ============================================================================
# HTTP helpers
# ============================================================================
def _get_text(url: str, *, params: dict | None = None) -> str:
    """GET returning body text. Empty string on any failure."""
    try:
        r = requests.get(url, params=params or {},
                          headers={'User-Agent': _UA, 'Accept': '*/*'},
                          timeout=_HTTP_TIMEOUT_S)
        if not r.ok:
            return ''
        return r.text or ''
    except Exception as e:
        logger.debug("trends_iq GET %s failed: %s", url, e)
        return ''


def _get_json(url: str, *, params: dict | None = None) -> Optional[dict | list]:
    body = _get_text(url, params=params)
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


# ============================================================================
# S3 cache helpers
# ============================================================================
def _s3_client():
    try:
        import boto3  # type: ignore
        return boto3.client('s3', region_name='us-east-2')
    except Exception as e:
        logger.debug("trends_iq: boto3 unavailable (%s)", e)
        return None


def _cache_key(filters: dict) -> str:
    payload = json.dumps({
        'geo_type':      filters.get('geo_type') or 'National',
        'geo_value':     filters.get('geo_value') or '',
        'lookback_days': int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS),
    }, sort_keys=True)
    h = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]
    return f"{S3_CACHE_PREFIX}{h}.json"


def _cache_get(filters: dict) -> Optional[dict]:
    s3 = _s3_client()
    if s3 is None:
        return None
    try:
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=_cache_key(filters))
        raw = resp['Body'].read().decode('utf-8')
        data = json.loads(raw)
        stale = data.get('stale_until')
        if stale:
            try:
                stale_dt = datetime.fromisoformat(stale.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) < stale_dt:
                    return data
            except Exception:
                pass
        return None
    except Exception:
        return None


def _cache_put(filters: dict, payload: dict) -> None:
    s3 = _s3_client()
    if s3 is None:
        return
    try:
        s3.put_object(Bucket=S3_CACHE_BUCKET, Key=_cache_key(filters),
                       Body=json.dumps(payload).encode('utf-8'),
                       ContentType='application/json')
    except Exception as e:
        logger.debug("trends_iq cache put failed: %s", e)


# ============================================================================
# Daily snapshot reader
# ============================================================================
# Each source under `scripts/trends_scrapers/` writes a normalized JSON to
# `s3://dashboard-inputs/trends_iq_snapshots/latest/{source}.json` every
# morning. The read side is best-effort: missing or stale snapshots fall
# back to the "coming soon" placeholder for that tile.
def _read_snapshot(source: str) -> Optional[dict]:
    s3 = _s3_client()
    if s3 is None:
        return None
    key = f'{_SNAPSHOT_PREFIX}{source}.json'
    try:
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
    except Exception:
        return None
    try:
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.debug("trends_iq _read_snapshot %s parse failed: %s", source, e)
        return None
    fetched_at = data.get('fetched_at')
    if fetched_at:
        try:
            fetched_dt = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
            age_s = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if age_s > _SNAPSHOT_MAX_AGE_S:
                logger.info("trends_iq snapshot %s is %.0fh old; treating as unavailable",
                             source, age_s / 3600.0)
                return None
        except Exception:
            pass
    return data


def _snapshot_items_for_geo(snap: dict, state: Optional[str]) -> list[dict]:
    """Pick the right slice out of a snapshot: state-scoped when the
    user selected a state and the snapshot has per-state data, else
    fall back to `national`."""
    if not snap:
        return []
    if state:
        by_state = snap.get('by_state') or {}
        if isinstance(by_state, dict):
            if state in by_state and by_state[state]:
                return by_state[state]
    return list(snap.get('national') or [])


# ============================================================================
# Geo normalization
# ============================================================================
def _resolve_geo(filters: dict) -> tuple[str, Optional[str]]:
    """Return (label, state_name_or_none) for the requested filter.

    State name is what the external helpers accept (California, etc.).
    For DMA filters we bias to the DMA's home state so state-scoped
    endpoints still return something meaningful.
    """
    geo_type = (filters.get('geo_type') or 'National').strip()
    geo_value = (filters.get('geo_value') or '').strip()
    if geo_type == 'National' or not geo_value:
        return 'National', None
    if geo_type == 'State':
        name = normalize_state(geo_value) if _HAS_EXTERNAL_SIGNALS else geo_value
        return (name or geo_value), name
    if geo_type == 'DMA':
        usps = _DMA_HOME_STATE.get(geo_value)
        state_name = _USPS_TO_NAME.get(usps) if usps else None
        return geo_value, state_name
    return 'National', None


# ============================================================================
# Card 1: Trending searches
# ============================================================================
def _fetch_trending_searches(state: Optional[str], lookback_days: int) -> list[dict]:
    """Google Trends daily search snapshots for the requested geography.

    Delegates to external_signals.trends_top_issues which handles the S3
    snapshot cache and RSS parsing. Filters out empty terms and caps at
    the top 40 by score.
    """
    try:
        rows = trends_top_issues(state=state, lookback_days=lookback_days) or []
    except Exception as e:
        logger.debug("trends_iq searches failed: %s", e)
        return []
    out = []
    for r in rows[:40]:
        term = (r.get('term') or '').strip()
        if not term:
            continue
        out.append({
            'term':           term,
            'score':          int(r.get('score') or 0),
            'related':        list(r.get('related') or [])[:5],
            'days_trending':  int(r.get('days_trending') or 1),
            'first_seen':     r.get('first_seen') or '',
            'last_seen':      r.get('last_seen') or '',
        })
    return out


# ============================================================================
# Card 2 + 3: Trending headlines and articles by source
# ============================================================================
def _parse_rss(body: str, source: str, domain: str, limit: int = 15) -> list[dict]:
    """Parse a standard RSS 2.0 / Atom feed body into normalized items.

    Extracts title, link, pubDate, and best-effort image (media:content,
    media:thumbnail, or enclosure). Returns [] on parse failure.
    """
    if not body:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    ns = {
        'media':   'http://search.yahoo.com/mrss/',
        'content': 'http://purl.org/rss/1.0/modules/content/',
        'atom':    'http://www.w3.org/2005/Atom',
        'dc':      'http://purl.org/dc/elements/1.1/',
    }
    items = list(root.iter('item'))
    if not items:
        items = list(root.iter('{http://www.w3.org/2005/Atom}entry'))
    out = []
    for it in items[:limit]:
        # ElementTree Elements are falsy when they have no children, so
        # avoid `a or b`; use explicit None checks throughout.
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
            date_el = it.find('{http://www.w3.org/2005/Atom}published')
        seendate = (date_el.text or '').strip() if date_el is not None else ''
        image = ''
        mc = it.find('media:content', ns)
        if mc is None:
            mc = it.find('media:thumbnail', ns)
        if mc is not None:
            image = mc.get('url', '') or ''
        if not image:
            enc = it.find('enclosure')
            if enc is not None and (enc.get('type') or '').startswith('image'):
                image = enc.get('url', '') or ''
        if not image:
            content_el = it.find('content:encoded', ns)
            if content_el is None:
                content_el = it.find('description')
            if content_el is not None and content_el.text:
                m = re.search(r'<img[^>]+src="([^"]+)"', content_el.text)
                if m:
                    image = m.group(1)
        out.append({
            'title':    title[:280],
            'url':      link,
            'source':   source,
            'domain':   domain,
            'seendate': seendate,
            'image':    image,
        })
    return out


def _fetch_one_feed(feed_tuple: tuple) -> list[dict]:
    source, url, domain = feed_tuple
    body = _get_text(url)
    return _parse_rss(body, source, domain, limit=10)


def _fetch_all_news_feeds() -> list[list[dict]]:
    """Fan out to every configured news feed in parallel; keep failures silent."""
    out: list[list[dict]] = []
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix='trends-news') as ex:
        futures = {ex.submit(_fetch_one_feed, ft): ft for ft in NEWS_FEEDS}
        for fut in as_completed(futures, timeout=25):
            try:
                out.append(fut.result(timeout=8) or [])
            except Exception:
                out.append([])
    return out


def _filter_by_state(items: list[dict], state: Optional[str]) -> list[dict]:
    """Keep only items whose title mentions the state (or its USPS code).

    Called when the user selected a state filter. If nothing matches we
    return the full unfiltered list rather than an empty tile - a national
    headline still counts as trending in every state.
    """
    if not state or not items:
        return items
    usps = None
    for code, name in _USPS_TO_NAME.items():
        if name == state:
            usps = code
            break
    patterns = [state.lower()]
    if usps:
        patterns.append(f' {usps.lower()} ')
        patterns.append(f' {usps.lower()}.')
    filt = []
    for it in items:
        t = (it.get('title') or '').lower()
        if any(p in t for p in patterns):
            filt.append(it)
    return filt or items


def _fetch_trending_headlines_and_sources(state: Optional[str]) -> tuple[list[dict], list[dict]]:
    """Return (trending_headlines[:15], articles_by_source[all_outlets]).

    Aggregates the top item per outlet into the flat "trending headlines"
    board, and keeps a per-outlet list for the "by source" board.
    """
    per_source = _fetch_all_news_feeds()
    flat: list[dict] = []
    by_source: list[dict] = []
    for outlet_items in per_source:
        if not outlet_items:
            continue
        source = outlet_items[0].get('source', '')
        state_filtered = _filter_by_state(outlet_items, state)
        by_source.append({
            'source':   source,
            'domain':   outlet_items[0].get('domain', ''),
            'articles': state_filtered[:5],
        })
        if state_filtered:
            flat.append(state_filtered[0])
    seen = set()
    dedup = []
    for h in flat:
        key = (h.get('title') or '').lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append(h)
    dedup = dedup[:15]
    by_source.sort(key=lambda x: (0 if x.get('articles') else 1, x.get('source', '')))
    return dedup, by_source


# ============================================================================
# Card 4: Trending people
# ============================================================================
# Simple proper-noun extractor tuned for headlines. Keeps runs of two-plus
# capitalized tokens (first + last name style). Skips a stopword list of
# common capitalized non-names that show up at the start of headlines.
_STOPWORDS_UC = {
    'The', 'This', 'That', 'These', 'Those', 'Their', 'These', 'His', 'Her',
    'US', 'USA', 'UK', 'EU', 'UN', 'NATO', 'NASA', 'FBI', 'CIA', 'SEC', 'IRS',
    'North', 'South', 'East', 'West', 'New', 'Old', 'Live', 'Breaking', 'Watch',
    'Video', 'Photos', 'Report', 'Update', 'Exclusive', 'Opinion', 'Editorial',
    'Analysis', 'Explainer', 'Fact', 'Check', 'Guide', 'Column', 'Podcast',
    'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December',
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
    'America', 'American', 'Americans', 'China', 'Chinese', 'Russia', 'Russian',
    'Israel', 'Israeli', 'Ukraine', 'Ukrainian', 'Iran', 'Iranian',
    'Trump', 'Biden', 'Harris',
    'Republicans', 'Democrats', 'Republican', 'Democrat',
    'Congress', 'Senate', 'House', 'White', 'House', 'Court', 'Supreme',
    'Wall', 'Street', 'Main', 'Street',
}

_NAME_RE = re.compile(
    r'\b([A-Z][a-z]+(?:[- ][A-Z][a-z]+){1,3})\b'
)


def _extract_person_names(text: str) -> list[str]:
    if not text:
        return []
    hits = []
    for m in _NAME_RE.finditer(text):
        name = m.group(1).strip()
        parts = re.split(r'[- ]', name)
        if any(p in _STOPWORDS_UC for p in parts):
            continue
        if len(parts) < 2:
            continue
        hits.append(name)
    return hits


def _fetch_trending_people(headlines: list[dict],
                            search_terms: list[dict],
                            lookback_days: int) -> list[dict]:
    """Mine trending people from the assembled headlines + search terms.

    Names weighted by mentions across sources + search-trend score if the
    name (or one of its tokens) also shows up as a trending term. Returns
    the top 20.
    """
    corpus = []
    for h in headlines:
        corpus.append(('headline', h.get('title', ''), h.get('source', ''), h.get('url', '')))
    for s in search_terms:
        corpus.append(('search',   s.get('term', ''),  '',                    ''))
        for rel in (s.get('related') or []):
            corpus.append(('search', rel, '', ''))

    counts: Counter = Counter()
    contexts: dict[str, list[str]] = defaultdict(list)
    for kind, text, source, url in corpus:
        for name in _extract_person_names(text):
            counts[name] += (2 if kind == 'search' else 1)
            snippet = text.strip()
            if snippet and len(contexts[name]) < 3:
                contexts[name].append(snippet[:140])

    people = []
    for name, cnt in counts.most_common(30):
        if cnt < 2:
            continue
        people.append({
            'name':      name,
            'mentions':  cnt,
            'context':   contexts.get(name, [])[:3],
        })
        if len(people) >= 20:
            break

    if people:
        try:
            titles = [p['name'] for p in people]
            views = wikipedia_pageviews(titles, lookback_days=lookback_days) or {}
            for p in people:
                p['pageviews'] = int(views.get(p['name']) or 0)
            people.sort(key=lambda x: (-x['mentions'], -x.get('pageviews', 0)))
        except Exception as e:
            logger.debug("trends_iq wikipedia lookup failed: %s", e)

    return people


# ============================================================================
# Card 5: Trending on social
# ============================================================================
def _fetch_reddit_popular(state: Optional[str], lookback_days: int) -> list[dict]:
    """Top posts from /r/popular (US-scoped when possible).

    Reddit's public .json endpoints are 403'd for unauthenticated clients
    now, but the Atom /.rss endpoint still works with a browser-style UA.
    We parse it with the same helper as news feeds.
    """
    _BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
                   'Gecko/20100101 Firefox/120.0')

    def _fetch_sub(sub: str, limit: int = 15) -> list[dict]:
        url = f'https://www.reddit.com/r/{sub}/.rss?limit={limit}'
        try:
            r = requests.get(url, headers={'User-Agent': _BROWSER_UA,
                                           'Accept': 'application/atom+xml, application/xml, text/xml'},
                              timeout=_HTTP_TIMEOUT_S)
            if not r.ok:
                return []
            body = r.text or ''
        except Exception as e:
            logger.debug("reddit rss %s failed: %s", sub, e)
            return []
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []
        atom_ns = 'http://www.w3.org/2005/Atom'
        media_ns = 'http://search.yahoo.com/mrss/'
        out = []
        for entry in root.iter(f'{{{atom_ns}}}entry'):
            title_el = entry.find(f'{{{atom_ns}}}title')
            link_el = entry.find(f'{{{atom_ns}}}link')
            cat_el = entry.find(f'{{{atom_ns}}}category')
            thumb_el = entry.find(f'{{{media_ns}}}thumbnail')
            title = (title_el.text or '').strip() if title_el is not None else ''
            if not title:
                continue
            href = link_el.get('href', '') if link_el is not None else ''
            subreddit = ((cat_el.get('term') if cat_el is not None else '') or sub).strip()
            image = ''
            if thumb_el is not None:
                image = thumb_el.get('url', '') or ''
            out.append({
                'title':    title[:260],
                'url':      href,
                'subreddit': subreddit,
                'image':    image,
            })
        return out

    items = _fetch_sub('popular', limit=20)
    if state:
        slug = state.lower().replace(' ', '')
        items = _fetch_sub(slug, limit=8) + items
    seen = set()
    out = []
    for it in items:
        key = it['title'].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:15]


def _fetch_social_trending(state: Optional[str], lookback_days: int) -> dict:
    """Fan out to every social platform's trending endpoint.

    Reddit is live at request time via the Atom `/.rss` feed. YouTube,
    TikTok, and X are populated from S3 snapshots written by
    `scripts/trends_scrapers/`. Instagram is scaffolded but doesn't
    have a source wired yet - its snapshot returns `available=False`
    which cascades to a "coming soon" tile in the UI.
    """
    result = {slug: {'label': label, 'items': [], 'available': avail}
              for slug, label, avail in SOCIAL_PLATFORMS}
    try:
        result['reddit']['items']  = _fetch_reddit_popular(state, lookback_days)
    except Exception as e:
        logger.debug("trends_iq reddit failed: %s", e)

    for slug, label, _static_avail in SOCIAL_PLATFORMS:
        if slug == 'reddit':
            continue
        snap = _read_snapshot(slug)
        if not snap:
            continue
        items = _snapshot_items_for_geo(snap, state)
        snap_available = snap.get('available')
        if snap_available is None:
            snap_available = bool(items)
        result[slug] = {
            'label':     label,
            'items':     items[:20],
            'available': bool(snap_available),
            'fetched_at': snap.get('fetched_at'),
        }
        if snap.get('error'):
            result[slug]['note'] = f"latest snapshot: {snap['error']}"
    return result


# ============================================================================
# Card 6: Trending products by retailer
# ============================================================================
_AMAZON_ITEM_RE = re.compile(
    r'data-asin="([A-Z0-9]{10})"([\s\S]{0,5000})',
    re.IGNORECASE,
)
_AMAZON_IMG_RE = re.compile(
    r'src="(https?://[^"]+_(?:AC|SL|SS|SR|SX)[^"]*)"',
    re.IGNORECASE,
)
_AMAZON_DP_RE = re.compile(
    r'href="(/[^"]*/dp/[A-Z0-9]{10}[^"]*)"',
    re.IGNORECASE,
)
# Fall back to slug-derived names when Amazon's product-title span is
# hidden behind a churning CSS-hash class. The slug is the segment
# between the leading `/` and `/dp/`.
_AMAZON_SLUG_RE = re.compile(r'^/([^/]+)/dp/')


def _slug_to_name(slug: str) -> str:
    """Turn `/Owala-FreeSip-Insulated-Stainless-BPA-Free/dp/xxx` into
    `Owala FreeSip Insulated Stainless BPA-Free`."""
    m = _AMAZON_SLUG_RE.match(slug or '')
    if not m:
        return ''
    parts = m.group(1).split('-')
    # Titlecase words that arrived lowercased; leave acronyms/existing casing alone.
    fixed = []
    for p in parts:
        if not p:
            continue
        if p.isupper() or (p[0].isupper() and p[1:].islower()):
            fixed.append(p)
        else:
            fixed.append(p[:1].upper() + p[1:])
    return ' '.join(fixed).replace('BPA Free', 'BPA-Free')


def _parse_amazon_movers(body: str, limit: int = 10) -> list[dict]:
    """Extract the top N products from an Amazon Bestsellers listing page.

    Amazon retired the server-rendered `/gp/movers-and-shakers/` grid; the
    Bestsellers pages still emit data-asin+image+dp-URL structure at load
    time, and that's enough to build a top-10 without headless browser.
    """
    if not body:
        return []
    seen_asins = set()
    items = []
    for m in _AMAZON_ITEM_RE.finditer(body):
        asin = m.group(1)
        if asin in seen_asins:
            continue
        chunk = m.group(2)
        img_m = _AMAZON_IMG_RE.search(chunk)
        dp_m = _AMAZON_DP_RE.search(chunk)
        image = img_m.group(1) if img_m else ''
        dp_path = dp_m.group(1) if dp_m else ''
        name = _slug_to_name(dp_path)
        if not name:
            continue
        seen_asins.add(asin)
        items.append({
            'rank':  len(items) + 1,
            'name':  name[:180],
            'url':   f'https://www.amazon.com{dp_path.split("/ref=")[0]}',
            'image': image,
            'asin':  asin,
        })
        if len(items) >= limit:
            break
    return items


def _fetch_amazon_movers() -> list[dict]:
    """Category-by-category Amazon Bestsellers scrape.

    Sequenced (not parallel) so we stay a good citizen against the same
    host. Returns [] silently on total failure - the retailer tile will
    show a coming-soon placeholder if this comes back empty.
    """
    _BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
                   'Gecko/20100101 Firefox/120.0')
    out = []
    for label, url in AMAZON_MOVERS_FEEDS:
        try:
            r = requests.get(url,
                              headers={'User-Agent': _BROWSER_UA,
                                       'Accept-Language': 'en-US,en;q=0.9'},
                              timeout=_HTTP_TIMEOUT_S)
            if not r.ok:
                continue
            body = r.text or ''
        except Exception as e:
            logger.debug("amazon fetch %s failed: %s", url, e)
            continue
        items = _parse_amazon_movers(body, limit=10)
        if items:
            out.append({'category': label, 'items': items})
        if len(out) >= 3:
            break
    return out


def _fetch_trending_products() -> list[dict]:
    """Aggregate the retailer tiles.

    Amazon runs live at request time. Every other retailer is populated
    from S3 snapshots written by `scripts/trends_scrapers/` on the
    Hetzner nightly cron. Missing / stale snapshots degrade to the
    coming-soon placeholder for that tile.

    Product tiles are always national - retailer bestsellers don't
    have per-DMA breakouts.
    """
    result = []
    for slug, label, static_avail in RETAILERS:
        entry = {
            'retailer':  slug,
            'label':     label,
            'available': static_avail,
            'items':     [],
        }
        if slug == 'amazon':
            try:
                cats = _fetch_amazon_movers()
                if cats:
                    entry['categories'] = cats
                    entry['items']      = cats[0].get('items') or []
            except Exception as e:
                logger.debug("trends_iq amazon movers failed: %s", e)
            result.append(entry)
            continue

        snap = _read_snapshot(slug)
        if snap:
            items = list(snap.get('national') or [])
            entry['items'] = items[:10]
            entry['available'] = bool(items) if snap.get('available') is None \
                else bool(snap.get('available'))
            raw_cats = snap.get('categories') or []
            if raw_cats:
                # Scrapers emit `{label, items}`; the frontend + Amazon path
                # use `{category, items}`. Normalize on read so tile
                # rendering stays uniform.
                entry['categories'] = [
                    {
                        'category': c.get('category') or c.get('label') or '',
                        'items':    (c.get('items') or [])[:10],
                    }
                    for c in raw_cats if isinstance(c, dict) and (c.get('items') or [])
                ]
            entry['fetched_at'] = snap.get('fetched_at')
            if snap.get('error'):
                entry['note'] = f"latest snapshot: {snap['error']}"
        result.append(entry)
    return result


# ============================================================================
# Public API
# ============================================================================
def get_filter_options() -> dict:
    """Filter dropdown choices for the Trends view.

    Returns the same shape Blue IQ returns so the frontend can share a
    filter-bar renderer if we want to consolidate later.
    """
    states = sorted(list(US_STATE_TO_ISO.keys())) if _HAS_EXTERNAL_SIGNALS else []
    canonical_dmas = sorted({d.title() for d in US_DMA_ALLOWLIST})
    dmas = [d for d in canonical_dmas if d in _DMA_HOME_STATE] + \
           [d for d in canonical_dmas if d not in _DMA_HOME_STATE]
    return {
        'geo_types':     VALID_GEO_TYPES,
        'states':        states,
        'dmas':          dmas,
        'default_lookback_days': DEFAULT_LOOKBACK_DAYS,
        'news_sources':  [s for s, _, _ in NEWS_FEEDS],
        'retailers':     [{'slug': s, 'label': l, 'available': a}
                          for s, l, a in RETAILERS],
        'social_platforms': [{'slug': s, 'label': l, 'available': a}
                             for s, l, a in SOCIAL_PLATFORMS],
    }


def compute_view(filters: dict, force_refresh: bool = False) -> dict:
    """Build the full Trends payload for the requested filters.

    Every surface fans out in a background thread so a slow feed on one
    outlet doesn't block the rest. Result is cached in S3 for
    CACHE_TTL_S seconds keyed on the filters hash.
    """
    if not force_refresh:
        cached = _cache_get(filters)
        if cached is not None:
            cached['from_cache'] = True
            return cached

    label, state = _resolve_geo(filters)
    lookback_days = int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS)

    tasks = {
        'trending_searches':   lambda: _fetch_trending_searches(state, lookback_days),
        'headlines_pack':      lambda: _fetch_trending_headlines_and_sources(state),
        'social_trending':     lambda: _fetch_social_trending(state, lookback_days),
        'products_by_retailer':lambda: _fetch_trending_products(),
    }

    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix='trends-iq') as ex:
        futures = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futures, timeout=45):
            key = futures[fut]
            try:
                results[key] = fut.result(timeout=45)
            except Exception as e:
                logger.debug("trends_iq task %s failed: %s", key, e)
                results[key] = None

    trending_searches = results.get('trending_searches') or []
    headlines, articles_by_source = results.get('headlines_pack') or ([], [])
    social_trending   = results.get('social_trending') or {}
    products          = results.get('products_by_retailer') or []

    trending_people = _fetch_trending_people(headlines, trending_searches, lookback_days)

    now = datetime.now(timezone.utc)
    payload = {
        'success':      True,
        'filters': {
            'geo_type':      filters.get('geo_type') or 'National',
            'geo_value':     filters.get('geo_value') or '',
            'geo_label':     label,
            'lookback_days': lookback_days,
        },
        'generated_at': now.isoformat(),
        'stale_until':  (now + timedelta(seconds=CACHE_TTL_S)).isoformat(),
        'cards': {
            'trending_searches':    trending_searches,
            'trending_headlines':   headlines,
            'articles_by_source':   articles_by_source,
            'trending_people':      trending_people,
            'social_trending':      social_trending,
            'products_by_retailer': products,
        },
        'counts': {
            'searches':  len(trending_searches),
            'headlines': len(headlines),
            'sources':   len(articles_by_source),
            'people':    len(trending_people),
            'retailers': len(products),
        },
    }

    _cache_put(filters, payload)
    payload['from_cache'] = False
    return payload
