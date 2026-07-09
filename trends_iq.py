"""
trends_iq.py - Trends dashboard module.

Grouped with Talent Ranker under the "Trends" section of the SELECT
PRODUCT dropdown. Answers:
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
from datetime import date, datetime, timedelta, timezone
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
        _trends_snap_get,
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

    def _trends_snap_get(geo, day_iso):
        return None


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
    ('MSNBC',           'https://www.msnbc.com/feed/',                                  'msnbc.com'),
    ('CNBC',            'https://www.cnbc.com/id/100003114/device/rss/rss.html',        'cnbc.com'),
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


# ============================================================================
# Streaming platform trending config
# ============================================================================
# Netflix uses the public per-country / global TSV feeds and is always
# available (no auth). The rest are cookie-donation Playwright scrapers
# populated by the daily suite - `available` flips to True at read time
# once a fresh snapshot lands. Notes:
#   - Disney+ and ESPN+ run from a residential IP (Jenna's laptop via
#     `scripts/trends_scrapers/local_residential_run.py`) because Disney's
#     Bamgrid CDN IP-gates Hetzner's datacenter range. ESPN+ programming
#     lives on disneyplus.com/browse/espn.
#   - Max entitlement comes bundled with Hulu on the Disney+/Hulu/Max
#     plan but streams on max.com; scraper hits max.com directly.
STREAMING_PLATFORMS = [
    ('netflix',    'Netflix',      True),
    ('disneyplus', 'Disney+',      False),
    ('hulu',       'Hulu',         False),
    ('max',        'Max',          False),
    ('primevideo', 'Prime Video',  False),
    ('espnplus',   'ESPN+',        False),
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


def _snapshot_items_for_geo(snap: dict, state: Optional[str],
                              keywords: Optional[list[str]] = None) -> list[dict]:
    """Pick the right slice out of a snapshot: state-scoped when the
    user selected a state and the snapshot has per-state data, else
    return `national` reordered so region-matching items rise to the
    top (national items still stay in the list as filler)."""
    if not snap:
        return []
    if state:
        by_state = snap.get('by_state') or {}
        if isinstance(by_state, dict):
            if state in by_state and by_state[state]:
                return list(by_state[state])
    national = list(snap.get('national') or [])
    if keywords:
        return _reorder_by_region(national, keywords)
    return national


# ============================================================================
# Geo keyword expansion — state + DMA -> list of case-insensitive match
# strings used to reorder national feeds so region-relevant items float
# to the top. Never drops content; only reorders.
# ============================================================================
_STATE_KEYWORDS: dict[str, list[str]] = {
    'Alabama':        ['alabama', 'birmingham', 'huntsville', 'montgomery', 'mobile', 'tuscaloosa', 'auburn'],
    'Alaska':         ['alaska', 'anchorage', 'fairbanks', 'juneau'],
    'Arizona':        ['arizona', 'phoenix', 'tucson', 'mesa', 'scottsdale', 'tempe', 'flagstaff'],
    'Arkansas':       ['arkansas', 'little rock', 'fayetteville', 'fort smith', 'jonesboro'],
    'California':     ['california', 'los angeles', 'san francisco', 'san diego', 'sacramento',
                       'oakland', 'san jose', 'silicon valley', 'hollywood', 'napa',
                       'palm springs', 'bay area', 'socal', 'norcal', 'calif'],
    'Colorado':       ['colorado', 'denver', 'boulder', 'colorado springs', 'aurora', 'fort collins'],
    'Connecticut':    ['connecticut', 'hartford', 'new haven', 'stamford', 'bridgeport', 'greenwich'],
    'Delaware':       ['delaware', 'wilmington', 'dover'],
    'Florida':        ['florida', 'miami', 'orlando', 'tampa', 'jacksonville', 'tallahassee',
                       'st petersburg', 'fort lauderdale', 'palm beach', 'gainesville', 'daytona'],
    'Georgia':        ['georgia', 'atlanta', 'savannah', 'augusta', 'macon', 'athens'],
    'Hawaii':         ['hawaii', 'honolulu', 'maui', 'oahu', 'kauai', 'big island'],
    'Idaho':          ['idaho', 'boise', 'meridian', 'nampa', 'idaho falls', 'pocatello'],
    'Illinois':       ['illinois', 'chicago', 'springfield', 'peoria', 'rockford', 'naperville', 'joliet'],
    'Indiana':        ['indiana', 'indianapolis', 'fort wayne', 'evansville', 'south bend'],
    'Iowa':           ['iowa', 'des moines', 'cedar rapids', 'davenport', 'ames'],
    'Kansas':         ['kansas', 'wichita', 'topeka', 'kansas city', 'overland park'],
    'Kentucky':       ['kentucky', 'louisville', 'lexington', 'bowling green'],
    'Louisiana':      ['louisiana', 'new orleans', 'baton rouge', 'shreveport', 'lafayette'],
    'Maine':          ['maine', 'portland maine', 'bangor', 'augusta maine'],
    'Maryland':       ['maryland', 'baltimore', 'annapolis', 'silver spring'],
    'Massachusetts':  ['massachusetts', 'boston', 'cambridge', 'worcester', 'springfield mass', 'lowell'],
    'Michigan':       ['michigan', 'detroit', 'grand rapids', 'ann arbor', 'lansing', 'flint'],
    'Minnesota':      ['minnesota', 'minneapolis', 'st paul', 'saint paul', 'rochester minn', 'duluth'],
    'Mississippi':    ['mississippi', 'jackson miss', 'gulfport', 'biloxi'],
    'Missouri':       ['missouri', 'kansas city', 'st louis', 'saint louis', 'springfield mo', 'columbia mo'],
    'Montana':        ['montana', 'billings', 'missoula', 'bozeman', 'helena'],
    'Nebraska':       ['nebraska', 'omaha', 'lincoln neb'],
    'Nevada':         ['nevada', 'las vegas', 'reno', 'henderson', 'sparks'],
    'New Hampshire':  ['new hampshire', 'manchester nh', 'nashua', 'concord nh'],
    'New Jersey':     ['new jersey', 'newark', 'jersey city', 'trenton', 'atlantic city', 'hoboken', 'princeton'],
    'New Mexico':     ['new mexico', 'albuquerque', 'santa fe', 'las cruces'],
    'New York':       ['new york', 'nyc', 'brooklyn', 'queens', 'bronx', 'manhattan', 'buffalo',
                       'albany', 'rochester', 'syracuse', 'long island'],
    'North Carolina': ['north carolina', 'charlotte', 'raleigh', 'greensboro', 'durham', 'winston salem'],
    'North Dakota':   ['north dakota', 'fargo', 'bismarck', 'grand forks'],
    'Ohio':           ['ohio', 'columbus', 'cleveland', 'cincinnati', 'toledo', 'akron', 'dayton'],
    'Oklahoma':       ['oklahoma', 'oklahoma city', 'tulsa', 'norman'],
    'Oregon':         ['oregon', 'portland oregon', 'eugene', 'salem oregon', 'bend oregon'],
    'Pennsylvania':   ['pennsylvania', 'philadelphia', 'philly', 'pittsburgh', 'allentown',
                       'harrisburg', 'lancaster', 'erie'],
    'Rhode Island':   ['rhode island', 'providence', 'newport'],
    'South Carolina': ['south carolina', 'charleston sc', 'columbia sc', 'greenville sc', 'myrtle beach'],
    'South Dakota':   ['south dakota', 'sioux falls', 'rapid city'],
    'Tennessee':      ['tennessee', 'nashville', 'memphis', 'knoxville', 'chattanooga'],
    'Texas':          ['texas', 'houston', 'dallas', 'austin', 'san antonio', 'fort worth',
                       'el paso', 'plano', 'arlington texas', 'corpus christi', 'lubbock'],
    'Utah':           ['utah', 'salt lake city', 'provo', 'orem'],
    'Vermont':        ['vermont', 'burlington vt', 'montpelier'],
    'Virginia':       ['virginia', 'richmond', 'virginia beach', 'norfolk', 'chesapeake',
                       'arlington va', 'alexandria va'],
    'Washington':     ['washington state', 'seattle', 'tacoma', 'spokane', 'bellevue', 'olympia'],
    'West Virginia':  ['west virginia', 'charleston wv', 'huntington wv', 'morgantown'],
    'Wisconsin':      ['wisconsin', 'milwaukee', 'madison', 'green bay', 'kenosha'],
    'Wyoming':        ['wyoming', 'cheyenne', 'casper', 'jackson hole'],
    'District of Columbia': ['washington dc', 'washington d.c.', 'the district'],
}


def _dma_keywords(dma_value: str) -> list[str]:
    """Extract search-friendly keywords from a Nielsen DMA display name.

    DMA names are space-joined multi-city ("San Francisco Oakland San Jose",
    "Dallas Fort Worth"). We emit the raw name, all adjacent 2-word windows,
    and every single word of >=4 letters so headline/product titles that
    mention any city in the DMA hit.
    """
    if not dma_value:
        return []
    raw = dma_value.strip()
    parts = raw.lower().replace('-', ' ').split()
    kws = [raw.lower()]
    for i in range(len(parts) - 1):
        two = f"{parts[i]} {parts[i+1]}"
        if len(two) >= 6:
            kws.append(two)
    for p in parts:
        if len(p) >= 4:
            kws.append(p)
    seen = set()
    out = []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _geo_keywords(state: Optional[str], dma_value: Optional[str]) -> list[str]:
    """All lowercased match strings that count as "in this region"."""
    kws: list[str] = []
    if state:
        kws.extend(_STATE_KEYWORDS.get(state, [state.lower()]))
    if dma_value:
        kws.extend(_dma_keywords(dma_value))
    # De-dupe while preserving order so higher-priority (state-name)
    # keywords still lead the list.
    seen = set()
    out = []
    for k in kws:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _reorder_by_region(items: list[dict],
                         keywords: list[str],
                         text_getter=None) -> list[dict]:
    """Hoist items whose text matches any of `keywords` to the top,
    keep the rest in their original order behind them. Never drops rows.

    `text_getter(item) -> str` overrides the default text field probe
    (`title`, then `name`, then `term`). Case-insensitive substring
    match.
    """
    if not keywords or not items:
        return list(items)
    def text_for(it: dict) -> str:
        if text_getter is not None:
            return (text_getter(it) or '').lower()
        return (it.get('title') or it.get('name') or it.get('term') or '').lower()
    matches: list[dict] = []
    rest: list[dict] = []
    for it in items:
        t = text_for(it)
        (matches if any(k in t for k in keywords) else rest).append(it)
    return matches + rest


# ============================================================================
# Geo normalization
# ============================================================================
def _resolve_geo(filters: dict) -> tuple[str, Optional[str], Optional[str]]:
    """Return (label, state_name_or_none, dma_name_or_none) for the filter.

    State name is what the external helpers accept (California, etc.).
    For DMA filters we bias to the DMA's home state so state-scoped
    endpoints still return something meaningful AND we pass the DMA
    display name back so downstream keyword expansion can lift
    DMA-specific city mentions to the top of national feeds.
    """
    geo_type = (filters.get('geo_type') or 'National').strip()
    geo_value = (filters.get('geo_value') or '').strip()
    if geo_type == 'National' or not geo_value:
        return 'National', None, None
    if geo_type == 'State':
        name = normalize_state(geo_value) if _HAS_EXTERNAL_SIGNALS else geo_value
        return (name or geo_value), name, None
    if geo_type == 'DMA':
        usps = _DMA_HOME_STATE.get(geo_value)
        state_name = _USPS_TO_NAME.get(usps) if usps else None
        return geo_value, state_name, geo_value
    return 'National', None, None


# ============================================================================
# Card 1: Trending searches
# ============================================================================
# Wide-pool aggregator lives further down (see _wide_pool_get and
# scripts/trends_scrapers/google_trends_wide.py). For US-national we
# prefer that pool because it contains ~10x more unique terms per day
# than the narrow single-geo RSS. State/DMA queries fall back to the
# narrow snapshot path (state-level wide pool would require per-state
# aggregation which we skip for now).
_SEARCH_POOL_CAP = 300  # top-N returned to the frontend; overall card scrolls


def _wide_pool_aggregate(lookback_days: int) -> list[dict]:
    """Aggregate the wide multi-geo daily snapshots across a window.

    Returns rows shaped like `trends_top_issues` (`term`, `score`,
    `related`, `days_trending`, `first_seen`, `last_seen`) so callers
    can treat both paths identically. Empty list if no wide-pool
    snapshots exist yet.
    """
    today = datetime.now(timezone.utc).date()
    by_term: dict[str, dict] = {}
    for off in range(max(1, lookback_days)):
        d_iso = (today - timedelta(days=off)).isoformat()
        rows = _wide_pool_get(d_iso)
        if not rows:
            continue
        for r in rows:
            term = (r.get('term') or '').strip()
            if not term:
                continue
            key = term.lower()
            score = int(r.get('score') or 0)
            rich = {
                'volume':             int(r.get('volume') or 0),
                'volume_growth_pct':  int(r.get('volume_growth_pct') or 0),
                'started_ts':         int(r.get('started_ts') or 0),
                'trend_keywords':     list(r.get('trend_keywords') or []),
                'news_articles':      list(r.get('news_articles') or []),
            }
            entry = by_term.get(key)
            if entry is None:
                by_term[key] = {
                    'term':           term,
                    'score':          score,
                    'related':        list(r.get('related') or [])[:6],
                    'days_trending':  1,
                    'first_seen':     d_iso,
                    'last_seen':      d_iso,
                    **rich,
                }
                continue
            if score > entry['score']:
                entry['score'] = score
                entry['term']  = term
                for k in ('volume', 'volume_growth_pct', 'started_ts',
                           'trend_keywords', 'news_articles'):
                    if rich.get(k):
                        entry[k] = rich[k]
            entry['days_trending'] += 1
            if d_iso < entry['first_seen']:
                entry['first_seen'] = d_iso
            if d_iso > entry['last_seen']:
                entry['last_seen'] = d_iso
            seen = {x.lower() for x in entry['related']}
            for rel in r.get('related') or []:
                if rel.lower() not in seen:
                    entry['related'].append(rel)
                    seen.add(rel.lower())
            entry['related'] = entry['related'][:6]
            if rich.get('trend_keywords'):
                seen_kw = {kw.lower() for kw in (entry.get('trend_keywords') or [])}
                for kw in rich['trend_keywords']:
                    if kw and kw.lower() not in seen_kw:
                        entry.setdefault('trend_keywords', []).append(kw)
                        seen_kw.add(kw.lower())
                entry['trend_keywords'] = (entry.get('trend_keywords') or [])[:12]
    return sorted(by_term.values(), key=lambda x: -x['score'])


def _fetch_trending_searches(state: Optional[str], lookback_days: int) -> list[dict]:
    """Google Trends daily search snapshots for the requested geography.

    Primary source is `trends_top_issues` which now prefers trendspy
    under the hood (matches the trends.google.com/trending UI - gives
    ~360 US trends/day with volume, growth %, started time, trend
    breakdown keywords, and per-trend news articles). The pre-existing
    wide multi-geo daily pool (populated by scripts/trends_scrapers/
    google_trends_wide.py on Hetzner) supplements when trendspy is
    thin - we union both sources for the US-national feed so any term
    that shows up in either lands on the dashboard.
    """
    rows: list[dict] = []
    try:
        if state:
            rows = trends_top_issues(state=state, lookback_days=lookback_days) or []
        else:
            primary = trends_top_issues(state=None, lookback_days=lookback_days) or []
            wide    = _wide_pool_aggregate(lookback_days) or []
            # Union: keep every row from primary (rich fields intact),
            # then append wide-pool rows whose term isn't already in
            # primary (so we don't overwrite trendspy's rich fields
            # with the older snapshot shape).
            if primary and wide:
                seen = {(r.get('term') or '').lower() for r in primary}
                for w in wide:
                    t = (w.get('term') or '').strip()
                    if t and t.lower() not in seen:
                        primary.append(w)
                        seen.add(t.lower())
                rows = primary
            else:
                rows = primary or wide
    except Exception as e:
        logger.debug("trends_iq searches failed: %s", e)
        return []
    out = []
    for r in rows[:_SEARCH_POOL_CAP]:
        term = (r.get('term') or '').strip()
        if not term:
            continue
        out.append({
            'term':               term,
            'score':              int(r.get('score') or 0),
            'related':            list(r.get('related') or [])[:5],
            'days_trending':      int(r.get('days_trending') or 1),
            'first_seen':         r.get('first_seen') or '',
            'last_seen':          r.get('last_seen') or '',
            # Rich fields from trendspy (empty when only RSS is
            # available - the frontend handles both).
            'volume':             int(r.get('volume') or 0),
            'volume_growth_pct':  int(r.get('volume_growth_pct') or 0),
            'started_ts':         int(r.get('started_ts') or 0),
            'trend_keywords':     list(r.get('trend_keywords') or [])[:12],
            'news_articles':      list(r.get('news_articles') or [])[:6],
        })
    return out


# ============================================================================
# Card 1a: Search-term categorization
# ----------------------------------------------------------------------------
# Slices the "Trending searches" pool into topical buckets so each card
# in the 5-up layout (Overall / Entertainment / Retail / Politics /
# Finance) shows a coherent, scannable list. Matching is done on the
# term itself PLUS the related-news titles Google Trends returns with
# each item; the related snippets are much richer text than the raw
# search term and dramatically improve category recall.
#
# Rules:
#   - A term can match multiple categories (e.g. "trump tariff nvidia"
#     hits politics + finance). We de-duplicate at bucket time by
#     capping each bucket to the top N by score.
#   - Category priority is intentional. When a term is ambiguous the
#     earlier category in the dict wins for the visible ordering. In
#     practice ordering rarely matters because we cap each list at 20
#     and each list is independently score-sorted.
#   - Word-boundary matching for short tokens (e.g. "fed", "gop") to
#     avoid false hits like "federal express" -> finance.
# ============================================================================
_SEARCH_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # Sports gets its own bucket and its own card (2026-07-09). Live
    # in front of `entertainment` in the priority list below so game /
    # team / league / athlete searches don't get absorbed by the "TV
    # + music + celebs" bucket.
    'sports': [
        # leagues / governing bodies
        'nfl', 'nba', 'mlb', 'nhl', 'mls', 'wnba', 'ncaa', 'ufc', 'wwe',
        'aew', 'pga', 'lpga', 'atp', 'wta', 'fifa', 'uefa', 'concacaf',
        'formula 1', 'formula one', ' f1 ', 'nascar', 'indycar', 'motogp',
        'espn', 'fox sports', 'sportscenter',
        # tournaments / trophies / megaevents
        'super bowl', 'super bowl lviii', 'super bowl lix', 'super bowl lx',
        'world series', 'nba finals', 'stanley cup', 'stanley cup final',
        'world cup', 'copa america', 'euro cup', 'euros final',
        'olympics', 'summer olympics', 'winter olympics', 'paralympics',
        'march madness', 'final four', 'sweet 16', 'college football playoff',
        'champions league', 'premier league', 'la liga', 'serie a',
        'bundesliga', 'ligue 1', 'europa league', 'concacaf champions',
        'wimbledon', 'us open', 'australian open', 'french open',
        'roland garros', 'grand slam', 'masters tournament', 'ryder cup',
        'the masters',
        # match / roster / game vocabulary
        ' vs ', 'vs.', 'defeat', 'beats', 'scores', 'goal', 'match',
        ' fc', 'united fc', 'city fc', 'championship', 'playoff',
        'playoffs', 'draft', 'rookie', 'halftime', 'overtime',
        'starting lineup', 'signed a contract', 'traded to', 'traded from',
        'head coach', 'assistant coach', 'general manager',
        'injury report', 'game score', 'game recap', 'game 7',
        'series clinched', 'walk-off', 'walk off',
        # marquee athletes across sports (unambiguous)
        'lebron', 'lebron james', 'stephen curry', 'kevin durant',
        'giannis', 'nikola jokic', 'luka doncic', 'joel embiid',
        'jayson tatum', 'anthony edwards', 'victor wembanyama',
        'caitlin clark', 'angel reese', 'aja wilson',
        'aaron judge', 'shohei ohtani', 'ohtani', 'mookie betts',
        'juan soto', 'kyle schwarber', 'freddie freeman', 'bryce harper',
        'ronald acuna', 'jose altuve', 'mike trout',
        'patrick mahomes', 'josh allen', 'joe burrow', 'lamar jackson',
        'jalen hurts', 'travis kelce', 'saquon barkley',
        'connor mcdavid', 'auston matthews', 'nathan mackinnon',
        'messi', 'ronaldo', 'mbappe', 'haaland', 'erling haaland',
        'jude bellingham', 'vinicius', 'kevin de bruyne', 'harry kane',
        'djokovic', 'nadal', 'federer', 'alcaraz', 'sinner',
        'sabalenka', 'gauff', 'swiatek', 'coco gauff',
        'tiger woods', 'rory mcilroy', 'scottie scheffler',
        'verstappen', 'lewis hamilton', 'lando norris', 'charles leclerc',
        'conor mcgregor', 'jon jones', 'islam makhachev',
        # unambiguous major-sport team names (single-word teams like
        # "Chiefs" or "Patriots" are omitted because they hit non-sport
        # meanings too often; "Giants", "Rangers", "Tigers" omitted for
        # the same reason - too many non-sport hits).
        'warriors', 'lakers', 'celtics', 'clippers', 'knicks',
        'bulls', 'bucks', '76ers', 'sixers', 'mavericks', 'nuggets',
        'thunder', 'pelicans', 'grizzlies', 'timberwolves',
        'yankees', 'dodgers', 'red sox', 'astros', 'phillies',
        'padres', 'blue jays', 'brewers', 'diamondbacks', 'cardinals',
        'mets', 'braves', 'cubs', 'marlins', 'guardians',
        'nationals', 'orioles', 'rays', 'athletics', 'pirates',
        'twins', 'royals', 'reds', 'rockies', 'mariners',
        '49ers', 'seahawks', 'steelers', 'packers', 'ravens',
        'raiders', 'bengals', 'buccaneers', 'commanders', 'jaguars',
        'cardinals nfl', 'texans', 'lions', 'vikings', 'saints',
        'canadiens', 'canucks', 'oilers', 'penguins', 'flyers',
        'avalanche', 'tampa bay lightning', 'panthers nhl', 'golden knights',
        # WNBA / NWSL context terms
        'fever', 'sparks', 'aces', 'liberty', 'storm', 'lynx',
        'sky wnba', 'mystics', 'sun wnba', 'wings',
        # soccer clubs
        'real madrid', 'barcelona', 'liverpool', 'chelsea', 'arsenal',
        'manchester united', 'manchester city', 'man united', 'man city',
        'tottenham', 'psg', 'bayern munich', 'juventus', 'ac milan',
        'inter milan', 'dortmund', 'atletico madrid', 'roma', 'napoli',
        'inter miami',
    ],
    'entertainment': [
        # streaming platforms + shows. "max" and "hbo" alone false-hit
        # "iphone 18 pro max", "hbo documentary" etc. - require the
        # compound form "hbo max" (or specific show names) so this
        # bucket doesn't sweep in every product with "Max" in its SKU.
        'netflix', 'disney+', 'disney plus', 'hulu', 'hbo max',
        'amazon prime video', 'paramount+', 'paramount plus', 'peacock',
        'apple tv', 'apple tv+', 'youtube tv',
        # tv / film
        'movie', 'film', 'trailer', 'series', 'season', 'episode',
        'premiere', 'sequel', 'reboot', 'oscar', 'emmy', 'grammy',
        'golden globes', 'sundance', 'cannes', 'box office',
        # music
        'concert', 'tour', 'album', 'song', 'billboard', 'lyrics',
        'spotify', 'apple music',
        # non-sports celebs / creators often searched
        'taylor swift', 'beyonce', 'kardashian', 'kanye', 'drake',
        'zendaya', 'timothee chalamet', 'ariana grande', 'sabrina carpenter',
        'olivia rodrigo', 'billie eilish', 'harry styles', 'bad bunny',
        'chappell roan', 'megan thee stallion',
    ],
    'retail': [
        # retailers
        'amazon', 'walmart', 'target', 'costco', 'best buy', 'macy',
        'kohl', 'nordstrom', 'wayfair', 'home depot', 'lowe',
        'ikea', 'trader joe', 'whole foods', 'aldi', 'shein', 'temu',
        # apparel / footwear brands (avoid ambiguous single words like 'gap')
        'nike', 'adidas', 'lululemon', 'zara', 'old navy',
        'american eagle', 'abercrombie', 'uniqlo', 'patagonia',
        'jordan brand', 'yeezy', 'new balance',
        # beauty
        'sephora', 'ulta', 'rare beauty', 'fenty', 'drunk elephant',
        # shopping events / deals - keep compound forms only; single
        # words like 'sale' and 'deal' catch "on sale", "one-year deal",
        # "record deal", etc. and pollute the retail card.
        'summer sale', 'flash sale', 'clearance sale', 'discount code',
        'coupon code', 'black friday', 'cyber monday', 'prime day',
        'holiday deals', 'best deals', 'top deals', 'clearance',
        # products
        'iphone', 'airpods', 'ipad', 'macbook', 'ps5', 'xbox',
        'nintendo switch', 'meta quest', 'pixel phone', 'samsung galaxy',
        'sneaker', 'jeans',
    ],
    # Conservative-coded political interest. Priority-ordered ABOVE
    # `politics` so a Trump / MAGA / border-wall search peels off into
    # this bucket instead of the neutral politics bucket. Terms are
    # chosen to be diagnostic of right-leaning search interest, not
    # just "any Republican appears in the story" - compound forms and
    # named figures dominate for precision. Global leaders and neutral
    # process terms (Congress, ballot, etc.) stay in `politics`.
    'conservative': [
        # Trump orbit
        'donald trump', 'president trump', 'trump admin', 'trump administration',
        'trump rally', 'trump indictment', 'trump verdict', 'melania',
        'ivanka trump', 'don trump jr', 'donald trump jr', 'don jr',
        'eric trump', 'barron trump', 'lara trump',
        # MAGA / movement
        'maga', 'make america great again', 'america first',
        # right-lean figures
        'jd vance', 'marjorie taylor greene', 'mtg', 'matt gaetz',
        'lauren boebert', 'josh hawley', 'ted cruz', 'jim jordan',
        'mike johnson', 'kevin mccarthy', 'ron desantis', 'desantis',
        'vivek ramaswamy', 'ramaswamy', 'nikki haley', 'tulsi gabbard',
        'rfk jr', 'robert kennedy jr', 'pete hegseth', 'kash patel',
        'stephen miller', 'scott bessent', 'linda mcmahon', 'elise stefanik',
        # right-lean media / commentators
        'fox news', 'newsmax', 'oann', 'breitbart', 'daily wire',
        'the federalist', 'national review', 'epoch times',
        'tucker carlson', 'ben shapiro', 'charlie kirk', 'matt walsh',
        'dan bongino', 'jack posobiec', 'laura ingraham', 'sean hannity',
        'megyn kelly', 'candace owens',
        # border / immigration (right framing)
        'border wall', 'border crisis', 'mass deportation', 'deportation raid',
        'ice raid', 'illegal alien', 'illegal aliens', 'illegals',
        'sanctuary city', 'chain migration', 'secure the border',
        'invasion at the border',
        # guns / 2A
        'second amendment', 'gun rights', 'concealed carry',
        # culture war (right framing)
        'parental rights', 'dont say gay', "don't say gay",
        'critical race theory', 'crt', 'anti-woke', 'woke agenda',
        'trans athlete', 'trans athletes', 'gender ideology',
        "save women's sports", 'protect women',
        # election integrity (right framing)
        'voter id', 'ballot harvesting', 'dominion voting', 'stolen election',
        'rigged election', 'election fraud', '2020 stolen',
        # religious / values
        'religious liberty', 'family values',
        # Trump-legal / opposition figures (right POV = "witch hunt")
        'mar-a-lago raid', 'hunter biden', 'biden crime family',
        'laptop from hell', 'jan 6 hostages', 'january 6 hostages',
        'alvin bragg', 'letitia james', 'jack smith prosecutor',
        # right-lean economic framings
        'tax cut', 'tax cuts', 'drill baby drill', 'energy dominance',
    ],
    # Progressive-coded political interest. Same rationale as
    # `conservative` above - peels partisan-left searches off the
    # neutral politics bucket. Democratic figures whose search
    # audience skews activist / policy-focused live here; centrist
    # institution names stay in `politics`.
    'progressive': [
        # Dem figures
        'joe biden', 'president biden', 'kamala harris', 'kamala',
        'obama', 'michelle obama', 'elizabeth warren',
        'bernie sanders', 'bernie', 'aoc', 'ocasio-cortez',
        'ilhan omar', 'rashida tlaib', 'jasmine crockett',
        'pramila jayapal', 'ayanna pressley', 'ro khanna',
        'katie porter', 'raphael warnock', 'jon ossoff',
        'tim walz', 'gavin newsom', 'newsom', 'pete buttigieg',
        'buttigieg', 'gretchen whitmer', 'whitmer', 'andy beshear',
        'wes moore', 'hakeem jeffries', 'chuck schumer',
        'nancy pelosi', 'zohran mamdani', 'mamdani',
        # left-lean media
        'msnbc', 'mother jones', 'the nation magazine', 'jacobin',
        'democracy now', 'common dreams',
        # climate
        'climate crisis', 'climate change', 'green new deal',
        'fossil fuel', 'big oil', 'oil pipeline', 'keystone xl',
        'climate emergency', 'greenhouse gas',
        # gun-control framing
        'gun control', 'gun violence', 'assault weapons ban',
        'red flag law', 'gun reform',
        # reproductive rights (left framing)
        'reproductive rights', 'abortion rights', 'roe v wade', 'roe',
        'dobbs decision', 'abortion access', 'ivf access',
        'contraception access', 'medication abortion',
        # LGBTQ
        'lgbtq', 'trans rights', 'trans healthcare',
        'gender-affirming care', 'gender affirming care',
        'pride month', 'marriage equality',
        # healthcare
        'medicare for all', 'universal healthcare', 'single payer',
        'single-payer', 'health care for all',
        # student debt
        'student debt', 'student loan forgiveness',
        'student loan cancellation',
        # labor
        'minimum wage', 'living wage', 'fight for 15', 'uaw strike',
        'unionize',
        # racial justice
        'black lives matter', 'blm', 'george floyd', 'defund the police',
        'criminal justice reform', 'mass incarceration', 'cash bail',
        'systemic racism', 'reparations',
        # immigrant rights (left framing)
        'immigrant rights', 'family separation', 'dreamers',
        'daca', 'path to citizenship',
        # voting rights (left framing)
        'voting rights', 'voter suppression', 'gerrymandering',
        'john lewis voting rights',
        # Gaza / Palestine (left framing)
        'gaza ceasefire', 'free palestine', 'palestine solidarity',
        'ceasefire now',
        # economic populism (left framing)
        'wealth tax', 'billionaire tax', 'tax the rich',
    ],
    'politics': [
        # Global leaders and neutral process terms only. Named
        # partisan figures now peel off into `conservative` or
        # `progressive` above; this bucket catches genuinely neutral
        # or center political news (Congress procedure, foreign
        # policy, generic election coverage) that doesn't lean either
        # way.
        'clinton',
        # global leaders
        'putin', 'xi jinping', 'netanyahu', 'zelensky', 'keir starmer',
        'emmanuel macron',
        # process / institutions (bipartisan)
        'presidential election', 'us president', 'vice president',
        'senator', 'congress', 'senate', 'house of representatives',
        'primary election', 'presidential debate', 'ballot', 'vote count',
        'polling place', 'gop', 'democrat', 'republican', 'campaign trail',
        'inauguration', 'impeach', 'indictment', 'guilty verdict',
        # geopolitics (neutral coverage)
        'ukraine war', 'israel', 'gaza', 'palestin', 'russia sanction',
        'iran nuclear', 'north korea', 'china tariff', 'nato',
        # institutions
        'white house', 'pentagon', 'state department', 'supreme court',
        'scotus', 'doj', 'fbi', 'immigration policy',
    ],
    'finance': [
        # markets - compound forms only; single words like 'stock' and
        # 'shares' catch "livestock", "share the news", "market a product",
        # and pollute the finance card.
        'nasdaq', 'dow jones', 's&p 500', 'sp500',
        'stock market', 'stock price', 'stock jumps', 'stock drops',
        'shares climb', 'shares fall', 'shares surge', 'shares plunge',
        'market rally', 'market sell-off', 'market sell off',
        'stock sell-off', 'stock sell off', 'ipo pricing',
        'quarterly earnings', 'dividend', 'buyback',
        # "merger" and "acquisition" without qualifiers hit sports
        # trade news ("Mets acquire reliever...") - tighten to compound
        # M&A forms only.
        'corporate merger', 'company acquisition', 'merger deal',
        'acquisition deal',
        # macro
        'inflation', 'interest rate', 'rate cut', 'rate hike',
        'recession', 'gdp', 'unemployment rate', 'cpi', 'ppi', 'tariff',
        'jobs report', 'nonfarm payroll',
        # institutions (compound to avoid short-token false hits)
        'federal reserve', 'fed rate', 'fed cut', 'fed hike',
        'fed meeting', 'jerome powell', 'janet yellen', 'us treasury',
        'wall street', 'goldman sachs', 'jpmorgan', 'blackrock',
        'berkshire', 'warren buffett',
        # crypto
        'bitcoin', 'btc', 'ethereum', 'crypto', 'coinbase',
        'stablecoin',
        # bellwether tickers - use "stock" suffix for common-word brands
        'nvidia', 'tesla stock', 'apple stock', 'microsoft stock',
        'meta stock', 'amazon stock', 'palantir', 'amd stock',
    ],
    # Gaming (video-game content). Sits after entertainment/sports so
    # broadly popular events still peel off first, but before tech so
    # a Fortnite / Elden Ring / GTA search doesn't get pulled into the
    # tech card. Gaming *hardware* (PS5 Pro, Xbox next-gen, Switch 2)
    # lives in tech; gaming *content* lives here.
    'gaming': [
        # marquee live-service / franchise titles
        'fortnite', 'roblox', 'minecraft', 'call of duty', 'cod',
        'warzone', 'apex legends', 'valorant', 'league of legends',
        'lol worlds', 'league worlds', 'counter-strike', 'cs2',
        'dota 2', 'overwatch', 'overwatch 2', 'destiny 2',
        'genshin impact', 'wuthering waves', 'honkai star rail',
        'zenless zone zero', 'palworld',
        # single-player / new releases
        'elden ring', 'baldurs gate 3', "baldur's gate 3",
        'gta 6', 'gta vi', 'grand theft auto 6', 'grand theft auto vi',
        'red dead 3', 'red dead redemption 3', 'zelda tears of the kingdom',
        'zelda tears', 'the legend of zelda tears',
        'silksong', 'hollow knight silksong',
        'starfield', 'diablo 4', 'diablo iv', 'assassins creed shadows',
        "assassin's creed shadows", 'monster hunter wilds',
        'stellar blade', 'final fantasy 7 rebirth', 'ff7 rebirth',
        # esports / streaming
        'twitch streamer', 'twitch clip', 'twitch ban', 'kick streamer',
        'esports championship', 'the international dota',
        'evo tournament', 'evo championship',
        # major creators / personalities (unambiguous gaming names)
        'kai cenat', 'mrbeast gaming', 'shroud', 'ninja fortnite',
        'pokimane', 'dr disrespect',
        # storefronts / launchers
        'steam sale', 'steam summer sale', 'epic games store free',
        'xbox game pass', 'game pass', 'ps plus games',
        'nintendo direct', 'ps state of play', 'summer game fest',
        # community drama
        'game of the year 2025', 'game of the year 2026', 'goty',
    ],
    # Tech / AI / big-tech news. Priority-ordered ABOVE retail so
    # searches like "iphone 18 pro max" (product news, not a shopping
    # cart) go to tech instead of retail. Retail keeps the physical /
    # consumer-goods brands (Costco, Target, Ulta, sneakers, etc.).
    # Retail's own iphone/airpods/ipad/xbox entries are kept as a
    # secondary catch for actual purchase-intent phrasing, but the
    # tech-priority ordering means those hits land here whenever the
    # search is about the product itself rather than shopping for it.
    'tech': [
        # AI companies / labs
        'openai', 'chatgpt', 'gpt-4', 'gpt-5', 'gpt5', 'gpt 4', 'gpt 5',
        'sora openai', 'sora video', 'sora ai',
        'anthropic', 'claude ai', 'claude 4', 'claude 5',
        'google gemini', 'gemini ai', 'gemini pro', 'gemini 3',
        'perplexity ai', 'perplexity', 'grok ai', 'grok 3', 'grok 4',
        'xai', 'x.ai', 'mistral ai', 'deepseek', 'llama 3',
        'nvidia earnings', 'nvidia gtc', 'nvidia keynote',
        'jensen huang', 'sam altman', 'demis hassabis', 'dario amodei',
        # AI + generic
        'ai chatbot', 'generative ai', 'ai model', 'ai models',
        'large language model', 'llm', 'ai agents', 'agentic ai',
        'ai code', 'ai coding', 'copilot', 'github copilot',
        'ai video generator', 'ai image generator', 'text to video',
        'text to image', 'deepfake', 'ai regulation', 'ai executive order',
        # Big Tech companies (product / policy / earnings context; not
        # stock-ticker context - that stays in finance with " stock").
        'apple event', 'wwdc', 'apple wwdc', 'apple vision pro',
        'vision pro headset', 'apple intelligence', 'apple silicon',
        'iphone 17', 'iphone 18', 'iphone 19', 'iphone launch',
        'iphone event', 'macbook pro', 'macbook air', 'm4 chip',
        'm5 chip', 'ipad pro', 'apple watch series',
        'google i/o', 'google io', 'google pixel', 'pixel 10', 'pixel 11',
        'pixel launch', 'pixel event', 'android release', 'android update',
        'chromecast', 'chromebook',
        'microsoft build', 'microsoft ignite', 'microsoft surface',
        'windows 11', 'windows 12', 'copilot pc', 'copilot plus pc',
        'meta connect', 'meta quest', 'quest 3', 'quest 4',
        'ray-ban meta', 'orion glasses', 'llama model',
        'amazon web services', 'aws re:invent', 'aws outage',
        'alexa plus', 'ring camera',
        # Tesla / EV tech (Tesla the product/tech, not the stock)
        'tesla robotaxi', 'tesla cybertruck', 'tesla model', 'tesla fsd',
        'tesla autopilot', 'tesla ai day', 'optimus robot',
        'elon musk', 'starlink', 'spacex launch', 'spacex starship',
        'neuralink', 'boring company',
        # Other AI-adjacent / robotics
        'humanoid robot', 'figure ai', '1x robot', 'boston dynamics',
        'waymo', 'cruise robotaxi',
        # Cybersecurity / outages (huge trending drivers)
        'data breach', 'ransomware attack', 'cyberattack',
        'crowdstrike outage', 'aws outage', 'cloudflare outage',
        'okta breach', 'zero-day exploit', 'zero day exploit',
        # Consumer electronics events
        'ces 2026', 'ces 2027', 'ces las vegas',
        # Streaming tech / codecs / other geek staples
        'vision pro', 'apple silicon', 'arm chip', 'tsmc',
        # Gaming hardware / launches (gaming *hardware* is tech, gaming
        # *content* stays in entertainment)
        'ps5 pro', 'ps6', 'xbox series', 'xbox next gen',
        'nintendo switch 2', 'switch 2', 'steam deck', 'rog ally',
        # Social media as platform news (product launches / policy /
        # bans - not "who tweeted what")
        'threads app', 'bluesky app', 'x platform outage',
        'tiktok ban', 'tiktok divest', 'tiktok algorithm',
        'instagram algorithm', 'youtube algorithm',
        # Web3 / crypto tech (protocols vs finance-side prices)
        'ethereum upgrade', 'ethereum layer 2', 'crypto exchange hack',
        'nft', 'defi protocol',
    ],
    # Weather + natural disasters. High priority so "hurricane milton"
    # / "wildfires" / "earthquake magnitude" peel off before politics
    # (disaster politics coverage) or crime (disaster looting).
    'weather': [
        'hurricane', 'tropical storm', 'typhoon', 'cyclone',
        'category 4 hurricane', 'category 5 hurricane', 'landfall',
        'storm surge', 'evacuation order', 'evacuation zone',
        'flood warning', 'flash flood', 'flooding',
        'wildfire', 'wildfires', 'brush fire', 'fire evacuation',
        'palisades fire', 'eaton fire', 'california wildfire',
        'earthquake', 'magnitude earthquake', 'aftershock',
        'tsunami warning', 'tsunami advisory', 'volcano eruption',
        'volcanic eruption', 'volcanic ash',
        'heatwave', 'heat wave', 'heat dome', 'excessive heat',
        'polar vortex', 'winter storm', 'blizzard', 'snowstorm',
        'nor-easter', "nor'easter", 'ice storm',
        'tornado warning', 'tornado watch', 'ef4 tornado', 'ef5 tornado',
        'derecho', 'atmospheric river',
        'lightning strike', 'lightning strikes', 'lightning bolt',
        'noaa', 'nws', 'national weather service',
        # newsy specifics (2025-2026)
        'hurricane milton', 'hurricane helene', 'hurricane erin',
        'hurricane melissa',
    ],
    # Crime / true-crime. Before politics so a Karen-Read-style trial
    # or a Diddy-style federal case peels off crime instead of getting
    # counted as political news.
    'crime': [
        # trial vocabulary
        'trial', 'murder trial', 'murder charges', 'jury verdict',
        'guilty verdict', 'not guilty verdict', 'sentencing hearing',
        'plea deal', 'plea agreement', 'grand jury indictment',
        'federal indictment', 'racketeering', 'rico charges',
        'search warrant', 'fbi raid',
        # trafficking / abuse
        'sex trafficking', 'human trafficking', 'child trafficking',
        'kidnapping', 'kidnapped',
        # true-crime blockbusters (2024-2026)
        'karen read', 'diddy trial', 'sean combs', 'p diddy trial',
        'ghislaine maxwell', 'jeffrey epstein', 'epstein list',
        'epstein files', 'idaho murders', 'bryan kohberger',
        'chad daybell', 'lori vallow', 'susan smith', 'ryan wesley routh',
        'gilgo beach killer', 'delphi murders',
        # missing / disappearance
        'missing person', 'missing woman', 'missing hiker', 'amber alert',
        'gone missing',
        # shootings / attacks
        'mass shooting', 'active shooter', 'school shooting',
        'shooting suspect', 'gunman opens fire',
        # attempts on public figures (high traffic driver)
        'assassination attempt', 'assassinated', 'suspect arrested',
    ],
    # Health & wellness. GLP-1s / Ozempic drove massive 2024-2026
    # trend traffic; mental health, workouts, and wellness fads
    # complete the cluster.
    'health': [
        # weight-loss / GLP-1 wave
        'ozempic', 'wegovy', 'mounjaro', 'zepbound', 'saxenda',
        'glp-1', 'glp 1', 'compounded semaglutide', 'compounded tirzepatide',
        'weight loss drug', 'weight loss shot', 'weight loss injection',
        # workouts / fitness fads
        'peloton', 'orange theory', 'orangetheory', 'crossfit',
        'hyrox', 'run club', 'zone 2 training', 'zone two training',
        'cold plunge', 'sauna', 'red light therapy',
        # mental health
        'mental health day', 'burnout symptoms', 'anxiety symptoms',
        'depression symptoms', 'therapy trend',
        'ssri', 'antidepressant', 'ketamine therapy',
        'psilocybin therapy',
        # diet / nutrition fads
        'protein powder', 'creatine benefits', 'electrolyte drink',
        'liquid iv', 'lmnt', 'element',
        'seed oils', 'raw milk', 'carnivore diet', 'keto diet',
        'intermittent fasting',
        # supplement / wellness brand universe
        'ag1', 'athletic greens', 'huberman', 'andrew huberman',
        'attia', 'peter attia', 'bryan johnson', "don't die",
        # medical news
        'rsv vaccine', 'measles outbreak', 'bird flu', 'h5n1',
        'covid variant', 'norovirus outbreak', 'mpox',
        # institutions
        'cdc guidelines', 'fda approval', 'fda advisory',
        'rfk hhs', 'rfk secretary',
    ],
    # Food & recipes. Restaurant news + viral recipes + celebrity
    # chef beats. Priority above retail so a "chick-fil-a menu"
    # search doesn't get absorbed into shopping.
    'food': [
        # QSR / chain drops
        'chick-fil-a', 'chick fil a', 'chipotle', 'panera',
        'shake shack', 'in-n-out', 'in n out', 'popeyes',
        'kfc', 'wendys', "wendy's", 'burger king', 'mcdonalds',
        "mcdonald's", 'taco bell', 'raising canes', "raising cane's",
        'jimmy johns', 'sweetgreen', 'crumbl cookie', 'crumbl',
        'starbucks menu', 'dutch bros', 'dunkin',
        # fine dining / restaurant news
        'michelin star', 'michelin guide', 'bib gourmand',
        'best new restaurant', 'james beard award',
        'restaurant closes', 'restaurant closing', 'restaurant reopens',
        # viral recipes / trends
        'viral recipe', 'tiktok recipe', 'recipe tiktok',
        'butter board', 'girl dinner', 'cottage cheese',
        'grimace shake', 'grimace milkshake',
        # celebrity chefs
        'gordon ramsay', 'guy fieri', 'anthony bourdain',
        'jose andres', 'jos\u00e9 andr\u00e9s', 'ina garten',
        # groceries / food news. Note: 'gas prices' intentionally NOT
        # here - it's an auto/inflation term. Search "egg prices" for
        # the grocery-inflation angle.
        'grocery prices', 'food prices', 'egg prices',
        'listeria recall', 'salmonella recall', 'e coli outbreak',
        'food recall',
    ],
    # Travel. Airlines + destinations + hotel drama + cruise news.
    # Priority above retail so travel intent is separated from
    # generic shopping.
    'travel': [
        # airlines / airports
        'southwest airlines', 'united airlines', 'american airlines',
        'delta air lines', 'delta airlines', 'jetblue', 'spirit airlines',
        'alaska airlines', 'frontier airlines', 'boeing 737 max',
        'boeing 787', 'airbus a350',
        'faa ground stop', 'flight cancellations', 'flight delays',
        'flight diverted', 'flight emergency landing',
        'tsa precheck', 'global entry', 'clear tsa',
        # hotels / stays
        'four seasons', 'ritz carlton', 'marriott', 'hilton hotel',
        'airbnb ban', 'airbnb regulation',
        'hyatt', 'aman resort', 'bulgari hotel',
        # cruises
        'royal caribbean', 'carnival cruise', 'norwegian cruise',
        'disney cruise', 'icon of the seas', 'star of the seas',
        'cruise passenger', 'cruise ship',
        # destinations / tourism
        'iceland tourism', 'santorini crowds', 'venice tax',
        'barcelona tourism', 'bali tourism',
        'top destinations 2026', 'best places to visit',
        'travel advisory', 'state department warning',
        'passport wait time', 'passport processing',
        # theme parks
        'universal epic universe', 'epic universe orlando',
        'disney world', 'disneyland', 'walt disney world',
        'universal studios',
    ],
    # Auto / EVs. Tesla news splits: robotaxi + FSD + Optimus + Elon
    # live in tech; Tesla stock lives in finance; general car launches
    # / recalls / non-Tesla EV news live here.
    'auto': [
        # legacy OEMs
        'ford f-150', 'ford f150', 'ford bronco', 'ford maverick',
        'ford mustang', 'ford ranger', 'chevrolet silverado',
        'chevy silverado', 'chevy tahoe', 'gmc yukon', 'gmc hummer',
        'toyota camry', 'toyota tacoma', 'toyota tundra',
        'toyota 4runner', 'toyota land cruiser', 'toyota supra',
        'honda civic', 'honda accord', 'honda crv', 'honda cr-v',
        'nissan altima', 'nissan rogue', 'nissan pathfinder',
        'jeep wrangler', 'jeep grand cherokee', 'ram 1500', 'ram trx',
        'dodge charger', 'dodge challenger',
        # luxury / performance
        'porsche 911', 'porsche taycan', 'bmw m3', 'bmw m5',
        'audi rs6', 'mercedes eqs', 'lucid air',
        'rivian r1s', 'rivian r1t',
        # EVs (non-Tesla)
        'ev tax credit', 'ev rebate', 'ev charger', 'ev range',
        'ev battery fire', 'byd auto', 'byd ev', 'nio ev',
        'xpeng', 'polestar 3', 'polestar 4', 'kia ev6', 'kia ev9',
        'hyundai ioniq', 'ford lightning', 'ford f-150 lightning',
        'chevy bolt', 'chevy blazer ev',
        # recalls / news
        'car recall', 'vehicle recall', 'airbag recall',
        'takata airbag', 'nhtsa investigation',
        # racing (separate from sports team names)
        'formula 1 race', 'monaco grand prix', 'daytona 500',
        'indianapolis 500', 'indy 500', 'le mans',
        # gas / EV crossover
        'gas prices', 'gas prices today', 'aaa gas prices',
    ],
    # Fashion & beauty. Priority above retail so a "sephora sale" or
    # "rare beauty launch" search lands here (brand + product news)
    # instead of the generic shopping bucket.
    'fashion': [
        # events
        'met gala', 'met gala 2026', 'met gala theme',
        'oscars red carpet', 'grammys red carpet', 'golden globes red carpet',
        'red carpet look', 'red carpet dress',
        'paris fashion week', 'milan fashion week', 'new york fashion week',
        'london fashion week', 'nyfw', 'lfw', 'mfw', 'pfw',
        # luxury houses
        'chanel', 'louis vuitton', 'gucci', 'prada', 'hermes',
        'her\u00e8mes', 'balenciaga', 'saint laurent', 'ysl',
        'dior', 'givenchy', 'valentino', 'burberry', 'fendi',
        'versace', 'bottega veneta', 'the row', 'jacquemus',
        # streetwear / new-wave
        'supreme drop', 'supreme x', 'off-white', 'off white',
        'aime leon dore', 'aim\u00e9 leon dore', 'kith', 'ssense',
        # sneakers
        'jordan 1', 'jordan 4', 'jordan 11', 'nike dunk',
        'yeezy', 'new balance 990', 'new balance 550',
        'nike vomero', 'onitsuka tiger', 'salomon xt-6',
        'hoka clifton', 'asics gel',
        # beauty brands
        'rare beauty', 'fenty beauty', 'kylie cosmetics', 'r.e.m beauty',
        'rhode skin', "hailey bieber's rhode", 'summer fridays',
        'drunk elephant', 'skinceuticals', 'la mer', 'la roche posay',
        'la roche-posay', 'sol de janeiro',
        # beauty concepts
        'lip gloss viral', 'clean girl aesthetic', 'strawberry makeup',
        'tomato girl', 'coquette aesthetic',
    ],
    # Home & real estate. Housing market + HGTV shows + home reno
    # trends. Priority above retail so "home depot deals" style
    # searches route here (home category context) rather than
    # generic retail.
    'home': [
        # housing market
        'mortgage rates', '30 year mortgage', 'housing market',
        'zillow', 'redfin', 'realtor.com', 'realtor com',
        'home prices', 'housing crash', 'housing bubble',
        'rent prices', 'rent hike',
        # HGTV / home reno personalities
        'joanna gaines', 'chip gaines', 'fixer upper',
        'property brothers', 'jonathan scott', 'drew scott',
        'christina hall', 'flip or flop', 'love it or list it',
        'martha stewart',
        # home reno / DIY
        'home renovation', 'home reno', 'kitchen remodel',
        'bathroom remodel', 'diy home', 'ikea hack',
        'home depot', 'lowes', "lowe's", 'ace hardware',
        'wayfair', 'west elm', 'crate and barrel', 'pottery barn',
        # aesthetics
        'cottagecore', 'modern farmhouse', 'coastal grandma',
        'coastal grandmother', 'dark academia decor',
        'quiet luxury home',
        # notable real estate
        'celebrity mansion', 'celebrity home', 'zillow gone wild',
    ],
    # Business & startups. Layoffs, IPOs, funding rounds, exec moves,
    # unicorn news. Distinct from finance (which is stocks + macro +
    # crypto prices) - this is corporate / operational business news.
    'business': [
        # unicorns / startups
        'startup funding', 'series a', 'series b', 'series c',
        'unicorn startup', 'yc demo day', 'y combinator',
        'stripe valuation', 'databricks valuation', 'canva valuation',
        # IPO wave
        'ipo filing', 'ipo debut', 'ipo priced', 'ipo dropped',
        'direct listing', 'spac merger', 'reverse merger',
        # famous private company news
        'openai valuation', 'anthropic funding', 'anthropic valuation',
        'perplexity funding', 'databricks earnings',
        'stripe ipo', 'databricks ipo',
        # layoffs / RIFs
        'layoffs', 'mass layoffs', 'tech layoffs', 'layoff round',
        'workforce reduction', 'reduction in force',
        'severance package', 'severance offer',
        # exec moves
        'ceo resigns', 'ceo fired', 'ceo steps down', 'new ceo',
        'cfo resigns', 'cfo fired', 'cto resigns',
        'shareholder lawsuit', 'shareholder revolt',
        # antitrust / regulation
        'ftc lawsuit', 'ftc investigation', 'doj antitrust',
        'antitrust ruling', 'antitrust case',
        # famous execs (business context, not stock price)
        'satya nadella', 'sundar pichai', 'tim cook', 'andy jassy',
        'mark zuckerberg', 'evan spiegel', 'brian chesky',
        'shou zi chew',
        # brand strategy news
        'rebrand', 'brand refresh', 'logo redesign',
    ],
}
# Short tokens matched by word boundary to prevent false positives like
# "gop" matching "gopro", "btc" matching "batch", "gdp" matching
# "gdpr", etc.
_SHORT_KEYWORD_TOKENS = {
    'gop', 'btc', 'gdp', 'cpi', 'ppi', 'ipo pricing',
    'aoc', 'doj', 'fbi', 'nato', 'scotus',
    # partisan short tokens
    'maga', 'mtg', 'blm', 'crt', 'dei', '2a',
    'rfk jr', 'ccw',
    # tech short tokens - word-boundary matching prevents "llm"
    # hitting "still murky", "nft" hitting "shift", etc. Skip bare
    # "ai" (too broad - hits the 2001 film, "A.I. Artificial
    # Intelligence", etc.); the compound forms in tech keywords
    # like "ai chatbot", "generative ai" catch legit AI searches.
    'llm', 'nft', 'gpt',
}


# Priority order for single-category assignment. A term that matches
# multiple category keyword lists is placed in the first matching
# category from this list. Rationale: sports and celebrity terms
# often bleed into political or financial headlines via their related
# text (e.g. Gianni Infantino's related stories mention "President
# Trump has been a 'great leader'"; the Mets trade news mentions
# "Bigger Sell-Off Coming?"). Sports and entertainment therefore get
# the highest priority so those bleeds get correctly classified.
#
# `conservative` and `progressive` sit BEFORE `politics` so partisan
# searches (Trump / MAGA / AOC / roe / etc.) peel off into their
# lean-specific buckets first, leaving `politics` as the neutral /
# institutional / centrist catch-all (Congress procedure, foreign
# policy, ballots, etc.). The DISPLAY order on the dashboard has
# `politics` before conservative/progressive but the categorization
# priority is separate from that: we want partisan-diagnostic terms
# labeled correctly first, then everything else falls to neutral
# politics.
_CATEGORY_PRIORITY = (
    # High-signal, unambiguous first. Sports/entertainment/gaming are
    # top-of-funnel content categories, then weather + crime (real-
    # world events with strong keywords), then vertical consumer
    # categories, finally the political + finance cluster.
    'sports', 'entertainment', 'gaming',
    # `tech` sits BEFORE `retail` so "iphone 18 pro max", "vision pro",
    # "ps5 pro" land in tech (product/company news) rather than retail
    # (shopping intent). Retail still catches "iphone deals",
    # "airpods sale", "black friday" via its own broader vocabulary.
    'tech',
    # Real-world events: weather + crime beat every other bucket for
    # storm names / trial names / disaster locations that could
    # otherwise leak into politics or entertainment.
    'weather', 'crime',
    # Vertical consumer categories all run BEFORE `retail` so a
    # "chick-fil-a menu" / "ozempic cost" / "southwest airlines"
    # search lands in its specific bucket, not the generic retail
    # catch-all. Retail then absorbs true shopping-intent searches.
    'health', 'food', 'travel', 'auto', 'fashion', 'home', 'business',
    'retail',
    # Political cluster: partisan buckets first, neutral politics
    # third, so Trump / MAGA / AOC peel off cleanly.
    'conservative', 'progressive', 'politics',
    'finance',
)


def _categorize_search_term(term: str, related: Iterable[str]) -> list[str]:
    """Return the single-category assignment for a search term as a
    one-element list (kept as a list for API stability with previous
    callers that iterated over `cats`).

    Empty list = uncategorized. The term appears ONLY in the Overall
    card (the catch-all) - it does not appear in any specific bucket.

    Categorization is done term-first (strongest signal), then falls
    back to related-text (weaker signal). Once a category matches at
    ANY priority level, we return immediately without checking lower-
    priority categories. This prevents multi-bucket duplication and
    lets a "fifa" hit in entertainment beat a "president trump" hit
    that appears in the same related-text blob.
    """
    term_l = (term or '').lower()
    related_l = [(r or '').lower() for r in (related or [])]

    def _kw_matches(hay: str, kw_l: str) -> bool:
        if kw_l in _SHORT_KEYWORD_TOKENS:
            return re.search(r'\b' + re.escape(kw_l.strip()) + r'\b', hay) is not None
        return kw_l in hay

    # Pass 1: try to match against the TERM only. A term hit is a
    # much stronger signal than a related-text hit ("gianni infantino"
    # in the term is definitely a sports search; "president trump" in
    # related text is just news context).
    for cat in _CATEGORY_PRIORITY:
        for kw in _SEARCH_CATEGORY_KEYWORDS.get(cat, []):
            if _kw_matches(term_l, kw.lower()):
                return [cat]

    # Pass 2: fall back to related-text matching. Same priority order,
    # so entertainment still wins over politics/finance when both
    # match in the related blob.
    for cat in _CATEGORY_PRIORITY:
        for kw in _SEARCH_CATEGORY_KEYWORDS.get(cat, []):
            kw_l = kw.lower()
            if any(_kw_matches(r, kw_l) for r in related_l):
                return [cat]

    return []


def _bucket_searches_by_category(rows: list[dict], per_bucket: int = 30
                                    ) -> dict[str, list[dict]]:
    """Split trending searches into the four topical buckets PLUS an
    "overall" catch-all of uncategorized terms.

    Rows arrive already sorted by score descending. Each term goes to
    AT MOST one topical bucket (per `_categorize_search_term`). Terms
    that don't match any category are appended to the "overall" bucket
    instead - so Overall is a proper catch-all and doesn't duplicate
    what's already visible in Entertainment / Retail / Politics /
    Finance.
    """
    buckets: dict[str, list[dict]] = {
        'sports':        [],
        'entertainment': [],
        'gaming':        [],
        'tech':          [],
        'weather':       [],
        'crime':         [],
        'health':        [],
        'food':          [],
        'travel':        [],
        'auto':          [],
        'fashion':       [],
        'home':          [],
        'business':      [],
        'retail':        [],
        'politics':      [],
        'conservative':  [],
        'progressive':   [],
        'finance':       [],
        'overall':       [],
    }
    for r in rows:
        cats = _categorize_search_term(r.get('term') or '', r.get('related') or [])
        if not cats:
            buckets['overall'].append(r)
            continue
        for c in cats:
            if c in buckets and c != 'overall' and len(buckets[c]) < per_bucket:
                buckets[c].append(r)
    return buckets


# ============================================================================
# Card 1b: Movers (Rising / Falling / Breakout / Sustained)
# ----------------------------------------------------------------------------
# Diffs a small "current window" of Google Trends snapshots against an
# older "baseline window" and buckets terms by directional signal. Uses
# the same per-day snapshot storage the aggregate trending-searches
# view already reads from, so no new data collection required.
#
# We aggregate ACROSS a window on both sides (rather than single-day
# vs single-day) because Google Trends returns very different top-N
# sets each day; single-day comparisons rarely overlap enough for
# Rising / Sustained buckets to fire meaningfully.
# ============================================================================
_MOVERS_CURRENT_WINDOW  = 2    # days included in "now" (today + yesterday)
_MOVERS_BASELINE_START  = 4    # days ago the baseline window starts
_MOVERS_BASELINE_WINDOW = 4    # days included in the baseline window
_MOVERS_RANK_THRESHOLD  = 3    # rank delta to count as rising/falling
_MOVERS_SCORE_UP_RATIO  = 2.0  # score ratio to count as rising
_MOVERS_SCORE_DN_RATIO  = 0.5  # score ratio to count as falling


def _movers_state_to_geo(state: Optional[str]) -> str:
    """Return the geo code the per-day trends snapshot uses (US or US-XX)."""
    if state and _HAS_EXTERNAL_SIGNALS:
        code = US_STATE_TO_ISO.get(state)
        if code:
            return code
    return 'US'


# Wide-pool location. The wide daily snapshot (US + top 15 states unioned)
# is written by scripts.trends_scrapers.google_trends_wide as part of the
# nightly scraper suite. When present, we prefer it over the narrow US-only
# snapshot because it has ~10-20x more unique terms per day, which is what
# actually makes day-over-day overlap possible (and therefore Climbing /
# Sustained buckets non-empty).
_WIDE_TRENDS_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
_WIDE_TRENDS_PREFIX = 'blue_iq/trends_rss_wide/v1/'


def _wide_pool_get(day_iso: str) -> Optional[list[dict]]:
    """Read the wide (multi-geo) daily pool for `day_iso`. None on miss."""
    try:
        import boto3  # type: ignore
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
        key = f'{_WIDE_TRENDS_PREFIX}{day_iso}.json'
        resp = s3.get_object(Bucket=_WIDE_TRENDS_BUCKET, Key=key)
        data = json.loads(resp['Body'].read().decode('utf-8'))
        return data.get('terms') or []
    except Exception:
        return None


def _movers_aggregate_window(geo: str, day_offsets: Iterable[int],
                                today: datetime) -> tuple[list[dict], Optional[str], Optional[str]]:
    """Merge snapshots across a window of days into a single ranked list.

    UNIONS both sources per day:
      - The narrow per-geo trendspy snapshot (~300-400 US terms with
        volume/news_articles/trend_keywords) - the source of truth.
      - The wide multi-geo RSS pool (~50-80 US terms), included so
        state-level RSS trends that never appear in trendspy's US
        list still show up.

    On collision (same term in both sources), keep the RICHER copy
    (has a volume field) so downstream code sees trendspy fields even
    when the wide-pool row is the higher-scored one. Absent that
    signal, keep the copy with the higher score.

    Rich fields (volume, news_articles, trend_keywords,
    volume_growth_pct, started_ts) are preserved through the merge so
    platform_mix computation and history matching can use them.

    Rank = position in the score-desc sorted output. Returns the
    merged rows plus the earliest and latest snapshot dates that
    contributed.
    """
    def _copy_row(r: dict, term: str, score: int) -> dict:
        return {
            'term':               term,
            'score':              score,
            'related':            list(r.get('related') or [])[:5],
            'volume':             r.get('volume'),
            'volume_growth_pct':  r.get('volume_growth_pct'),
            'started_ts':         r.get('started_ts'),
            'news_articles':      list(r.get('news_articles')  or [])[:6],
            'trend_keywords':     list(r.get('trend_keywords') or [])[:8],
        }

    def _is_rich(r: dict) -> bool:
        return (r.get('volume') is not None) or bool(r.get('news_articles'))

    by_term: dict[str, dict] = {}
    days_used: set[str] = set()
    use_wide = (geo == 'US')  # wide pool is US-national only
    for off in day_offsets:
        d_iso = (today - timedelta(days=off)).date().isoformat()
        pools: list[list[dict]] = []
        narrow = _trends_snap_get(geo, d_iso)
        if narrow:
            pools.append(narrow)
        if use_wide:
            wide = _wide_pool_get(d_iso)
            if wide:
                pools.append(wide)
        if not pools:
            continue
        days_used.add(d_iso)
        for rows in pools:
            for r in rows:
                term = (r.get('term') or '').strip()
                if not term:
                    continue
                key = term.lower()
                score = int(r.get('score') or 0)
                existing = by_term.get(key)
                if existing is None:
                    by_term[key] = _copy_row(r, term, score)
                    continue
                this_rich = _is_rich(r)
                existing_rich = _is_rich(existing)
                # Prefer the rich version regardless of score so
                # trendspy fields aren't overwritten by a wide-pool
                # RSS row that happens to have a higher score int.
                if this_rich and not existing_rich:
                    by_term[key] = _copy_row(r, term, score)
                elif not this_rich and existing_rich:
                    continue
                elif score > int(existing.get('score') or 0):
                    by_term[key] = _copy_row(r, term, score)
    if not by_term:
        return [], None, None
    merged = sorted(by_term.values(), key=lambda r: -int(r.get('score') or 0))
    days_sorted = sorted(days_used)
    return merged, days_sorted[0], days_sorted[-1]


def _movers_annotate(row: dict, today_rank: Optional[int],
                       prev_rank: Optional[int], prev_score: Optional[int],
                       bucket: str) -> dict:
    """Attach movers metadata to a copy of the source row."""
    out = dict(row)
    out['bucket']       = bucket
    out['rank_today']   = today_rank + 1 if today_rank is not None else None
    out['rank_prev']    = prev_rank  + 1 if prev_rank  is not None else None
    out['score_prev']   = int(prev_score) if prev_score is not None else None
    if today_rank is not None and prev_rank is not None:
        out['rank_change'] = prev_rank - today_rank
    else:
        out['rank_change'] = None
    if prev_score and out.get('score'):
        out['score_ratio'] = round(out['score'] / max(prev_score, 1), 2)
    else:
        out['score_ratio'] = None
    return out


def compute_search_movers(state: Optional[str]) -> dict:
    """Compute Movers buckets for trending searches.

    Compares today's per-day snapshot to the closest baseline snapshot
    around _MOVERS_BASELINE_DAYS ago. Returns:

      {
        'breakout':   [...],   # new in today's list, wasn't in baseline
        'rising':     [...],   # rank up >= 3 OR score >= 2x baseline
        'falling':    [...],   # rank down >= 3 OR score <= 0.5x baseline
        'sustained':  [...],   # in both, mostly flat
        'baseline_day': 'YYYY-MM-DD',
        'today_day':    'YYYY-MM-DD',
        'available':    True/False,
        'note':         optional string when unavailable,
      }

    When there isn't enough history yet, returns
    `{available: False, note: 'warming up'}` so the UI can render a
    "collecting data" placeholder instead of empty tiles.
    """
    result = {
        'breakout':   [],
        'rising':     [],
        'falling':    [],
        'sustained':  [],
        'baseline_day': None,
        'today_day':    None,
        'available':    False,
        'note':         None,
    }
    if not _HAS_EXTERNAL_SIGNALS:
        result['note'] = 'external_signals unavailable'
        return result

    now = datetime.now(timezone.utc)
    geo = _movers_state_to_geo(state)

    current_offsets  = list(range(0, _MOVERS_CURRENT_WINDOW))
    baseline_offsets = list(range(_MOVERS_BASELINE_START,
                                    _MOVERS_BASELINE_START + _MOVERS_BASELINE_WINDOW))

    today_rows, today_start, today_end = _movers_aggregate_window(
        geo, current_offsets, now)
    baseline_rows, baseline_start, baseline_end = _movers_aggregate_window(
        geo, baseline_offsets, now)

    if not today_rows or not baseline_rows:
        result['note'] = 'warming up - need more days of history'
        result['today_day']    = today_end   or today_start   or None
        result['baseline_day'] = baseline_end or baseline_start or None
        return result

    today_by_term = {(r.get('term') or '').strip().lower(): (i, r)
                       for i, r in enumerate(today_rows)
                       if (r.get('term') or '').strip()}
    base_by_term  = {(r.get('term') or '').strip().lower(): (i, r)
                       for i, r in enumerate(baseline_rows)
                       if (r.get('term') or '').strip()}

    seen_keys: set[str] = set()

    for term_key, (rank_today, row) in today_by_term.items():
        seen_keys.add(term_key)
        if term_key not in base_by_term:
            result['breakout'].append(
                _movers_annotate(row, rank_today, None, None, 'breakout')
            )
            continue
        rank_prev, prev_row = base_by_term[term_key]
        rank_change = rank_prev - rank_today
        score_now   = int(row.get('score') or 0)
        score_prev  = int(prev_row.get('score') or 0)
        ratio = (score_now / max(score_prev, 1)) if score_prev else float('inf')
        annotated = _movers_annotate(row, rank_today, rank_prev, score_prev,
                                       bucket='sustained')
        if rank_change >= _MOVERS_RANK_THRESHOLD or ratio >= _MOVERS_SCORE_UP_RATIO:
            annotated['bucket'] = 'rising'
            result['rising'].append(annotated)
        elif rank_change <= -_MOVERS_RANK_THRESHOLD or (score_prev > 0 and ratio <= _MOVERS_SCORE_DN_RATIO):
            annotated['bucket'] = 'falling'
            result['falling'].append(annotated)
        else:
            result['sustained'].append(annotated)

    # Terms that were in the baseline but have dropped off today.
    for term_key, (rank_prev, prev_row) in base_by_term.items():
        if term_key in seen_keys:
            continue
        result['falling'].append(
            _movers_annotate(prev_row, None, rank_prev, int(prev_row.get('score') or 0),
                              bucket='falling')
        )

    # Deterministic ordering within each bucket.
    result['breakout'].sort(key=lambda r: (r.get('rank_today') or 999,
                                              -int(r.get('score') or 0)))
    result['rising'].sort(   key=lambda r: (-(r.get('rank_change') or 0),
                                              -int(r.get('score') or 0)))
    result['falling'].sort(  key=lambda r: (r.get('rank_change') or 0,
                                              -int(r.get('score_prev') or 0)))
    result['sustained'].sort(key=lambda r: (r.get('rank_today') or 999,
                                              -int(r.get('score') or 0)))

    for bucket in ('breakout', 'rising', 'falling', 'sustained'):
        result[bucket] = result[bucket][:25]

    result['available']    = True
    result['today_day']    = today_end or today_start
    result['baseline_day'] = baseline_end or baseline_start
    return result


# ============================================================================
# Movers platform-mix annotation
# ============================================================================
# Attributes each mover row's buzz across three channels so the UI can
# show WHERE the interest is being driven from:
#
#   search  - Google Trends search volume (trendspy `volume` field)
#   media   - Real news-article pickup (trendspy `news_articles` count,
#              or `related` news-titles count as fallback)
#   social  - How many social platforms (Reddit, X, TikTok, YouTube,
#              Instagram) have the term in their top items
#
# Each channel gets a 0-1 "intensity" score (saturating ceilings picked
# so a top-tier trend maxes each out): 50K searches, 3 news articles,
# 3 social platforms. Intensities are normalized to fractions summing
# to 1 (or all-zero when no signal is present).
# ============================================================================
_MIX_SEARCH_CEILING  = 50_000  # volume that maxes the search intensity
_MIX_MEDIA_CEILING   = 3       # news_articles count that maxes media
_MIX_SOCIAL_CEILING  = 3       # # of social platforms that max social


def _term_in_social_platform_items(term: str, items: list) -> bool:
    """True when `term` appears (case-insensitive substring) in any of
    a social platform's top items. Only checks the first 15 items per
    platform - past that a match reads as coincidental rather than
    "this is what's trending on the platform"."""
    if not term or not items:
        return False
    t = term.strip().lower()
    if len(t) < 3:  # avoid noise like "vs" / "us" matching everywhere
        return False
    for it in items[:15]:
        if not isinstance(it, dict):
            continue
        for k in ('title', 'topic', 'hashtag', 'name', 'text', 'term'):
            v = it.get(k)
            if v and t in str(v).lower():
                return True
    return False


def _compute_platform_mix_for_term(term: str, rich_row: dict,
                                     social_trending: dict) -> dict:
    """Attribute a single trend's buzz across search / media / social.

    `rich_row` is the trending-search row from today's snapshot (or the
    mover row itself as a fallback) so we can read `volume` and
    `news_articles`. `social_trending` is the full social-platforms
    dict from the payload (reddit / x / tiktok / youtube / instagram).

    Returns a dict with normalized fractions + raw counts for the
    hover tooltip:

        {
          'search': 0.65, 'media': 0.25, 'social': 0.10,
          'search_raw': 200000, 'media_raw': 3, 'social_raw': 1,
          'social_platforms': ['reddit'],
          'primary': 'search',
        }
    """
    volume = int(rich_row.get('volume') or rich_row.get('score') or 0)
    news_articles = rich_row.get('news_articles') or []
    news_count = len(news_articles)
    if news_count == 0:
        # RSS-only day: fall back to related news titles count
        news_count = min(len(rich_row.get('related') or []), _MIX_MEDIA_CEILING)

    social_hits: list[str] = []
    for slug, block in (social_trending or {}).items():
        if not isinstance(block, dict) or not block.get('available'):
            continue
        if _term_in_social_platform_items(term, block.get('items') or []):
            social_hits.append(slug)

    search_i = min(volume / float(_MIX_SEARCH_CEILING), 1.0) if volume > 0 else 0.0
    media_i  = min(news_count / float(_MIX_MEDIA_CEILING), 1.0)
    social_i = min(len(social_hits) / float(_MIX_SOCIAL_CEILING), 1.0)

    total = search_i + media_i + social_i
    if total <= 0:
        return {
            'search':           0.0,
            'media':            0.0,
            'social':           0.0,
            'search_raw':       volume,
            'media_raw':        news_count,
            'social_raw':       len(social_hits),
            'social_platforms': social_hits,
            'primary':          None,
        }
    mix = {
        'search':           round(search_i / total, 3),
        'media':            round(media_i  / total, 3),
        'social':           round(social_i / total, 3),
        'search_raw':       volume,
        'media_raw':        news_count,
        'social_raw':       len(social_hits),
        'social_platforms': social_hits,
    }
    channels = sorted(
        [('search', mix['search']), ('media', mix['media']), ('social', mix['social'])],
        key=lambda p: -p[1],
    )
    mix['primary'] = channels[0][0] if channels[0][1] > 0 else None
    return mix


def _annotate_movers_with_platform_mix(movers: dict,
                                         trending_searches: list,
                                         social_trending: dict) -> dict:
    """Attach `platform_mix` to every mover row so the frontend can
    render the mini stacked bar next to each trend.

    Reads rich fields from `trending_searches` when possible (movers
    aggregation drops volume / news_articles for aggregation
    performance). Falls back to whatever's on the mover row itself.
    Idempotent - safe to call twice.
    """
    if not movers or not movers.get('available'):
        return movers
    search_lookup: dict[str, dict] = {}
    for s in trending_searches or []:
        key = (s.get('term') or '').strip().lower()
        if key and key not in search_lookup:
            search_lookup[key] = s
    for bucket in ('breakout', 'rising', 'falling', 'sustained'):
        for row in movers.get(bucket) or []:
            term = (row.get('term') or '').strip()
            key = term.lower()
            rich = search_lookup.get(key) or row
            row['platform_mix'] = _compute_platform_mix_for_term(
                term, rich, social_trending)
    return movers


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


def _filter_by_state(items: list[dict], keywords: Optional[list[str]]) -> list[dict]:
    """Reorder items so region-relevant ones lead the list; never drops rows.

    The old behavior (`return matches if any else items`) meant most
    state selections rendered as pure national feeds because typical
    headlines don't repeat the state name. Now we always keep the full
    list but hoist keyword-matching items to the top.
    """
    if not keywords or not items:
        return list(items)
    return _reorder_by_region(items, keywords)


def _fetch_trending_headlines_and_sources(keywords: Optional[list[str]]
                                            ) -> tuple[list[dict], list[dict]]:
    """Return (trending_headlines[:15], articles_by_source[all_outlets]).

    Aggregates the top item per outlet into the flat "trending headlines"
    board, and keeps a per-outlet list for the "by source" board. When
    `keywords` is non-empty, region-matching items rise to the top of
    each outlet's slice so state / DMA selections visibly re-rank the
    boards without ever emptying a tile.
    """
    per_source = _fetch_all_news_feeds()
    flat: list[dict] = []
    by_source: list[dict] = []
    for outlet_items in per_source:
        if not outlet_items:
            continue
        source = outlet_items[0].get('source', '')
        ordered = _filter_by_state(outlet_items, keywords)
        by_source.append({
            'source':   source,
            'domain':   outlet_items[0].get('domain', ''),
            'articles': ordered[:5],
        })
        if ordered:
            flat.append(ordered[0])
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
# Proper-noun extractor tuned for headlines, with anti-false-positive
# filters so events / teams / tournaments / media titles don't get
# counted as people.
#
# Rules (a captured Title Case run is REJECTED if ANY of these fire):
#   1. Any single token is in _STOPWORDS_UC (nationalities, days, months,
#      generic headline noise). Never appears in a real person's name.
#   2. Any single token is in _NON_PERSON_TOKENS (event/team/tournament
#      words like "Cup", "Bowl", "Derby", "Aces", "Cowboys",
#      "Wimbledon", "Quarterfinals", "Bracket", "Prediction", ...).
#   3. The whole normalized phrase is in _NON_PERSON_PHRASES (curated
#      multi-word patterns that pass token checks but aren't people:
#      "Home Run", "Wall Street", "Supreme Court", ...).
_STOPWORDS_UC = {
    'The', 'This', 'That', 'These', 'Those', 'Their', 'These', 'His', 'Her',
    'US', 'USA', 'UK', 'EU', 'UN', 'NATO', 'NASA', 'FBI', 'CIA', 'SEC', 'IRS',
    'Live', 'Breaking', 'Watch',
    # NOTE: 'North', 'South', 'East', 'West', 'New', 'Old' intentionally
    # NOT blocked as tokens - they're valid name components (Kanye West,
    # Adam West, Simon East, Oliver North, Cindi Old, etc.). The
    # _NON_PERSON_PHRASES check handles "New York", "Los Angeles", etc.
    'Video', 'Photos', 'Report', 'Update', 'Exclusive', 'Opinion', 'Editorial',
    'Analysis', 'Explainer', 'Fact', 'Check', 'Guide', 'Column', 'Podcast',
    'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December',
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
    'America', 'American', 'Americans', 'China', 'Chinese', 'Russia', 'Russian',
    'Israel', 'Israeli', 'Ukraine', 'Ukrainian', 'Iran', 'Iranian',
    'Trump', 'Biden',
    'Republicans', 'Democrats', 'Republican', 'Democrat',
    'Congress', 'Senate',
    # NOTE: 'White', 'West', 'House', 'Court', 'Supreme', 'Wall',
    # 'Street', 'Main', 'North', 'South', 'East', 'Harris' intentionally
    # NOT blocked here because they're common surname components
    # (Betty White, Kanye West, Kamala Harris, Adam West, ...). The
    # _NON_PERSON_PHRASES check catches "White House", "Wall Street",
    # "Supreme Court", "Main Street" as full phrases instead.
}

# Tokens that appear in events / tournaments / teams / media titles and
# NEVER appear as a component of a real person's name. If any token in
# a captured Title Case run is in this set, we reject the run.
_NON_PERSON_TOKENS = {
    # sports events / tournaments / rounds
    'Cup', 'Bowl', 'Derby', 'Bracket', 'Brackets', 'Series', 'League',
    'Championship', 'Championships', 'Tournament', 'Tournaments',
    'Semifinal', 'Semifinals', 'Quarterfinal', 'Quarterfinals',
    'Finals', 'Playoff', 'Playoffs', 'Marathon', 'Master', 'Masters',
    'Slam', 'Preseason', 'Postseason', 'All-Star', 'Allstar',
    'Draft', 'Combine',
    # major events by proper name (never a person's first/last name)
    'Wimbledon', 'Olympics', 'Olympiad', 'Paralympics', 'Coachella',
    'Sundance', 'Emmys', 'Oscars', 'Grammys',
    # TV / media descriptors
    'Season', 'Episode', 'Trailer', 'Premiere', 'Finale', 'Recap',
    'Preview', 'Highlights', 'Prediction', 'Predictions', 'Odds',
    'Rankings', 'Roundup', 'Standings', 'Boxscore', 'Scorecard',
    # journalism descriptors
    'Report', 'Update', 'Analysis', 'Editorial', 'Column', 'Podcast',
    'Interview', 'Exclusive', 'Breaking', 'Newsletter', 'Bulletin',
    # descriptors that never appear in a person's name
    'Late', 'Early', 'Best', 'Top', 'Latest', 'First', 'Live', 'Real',
    'Fake', 'Full', 'Total', 'Final', 'Ultimate', 'Home', 'Away',
    'Home-Run', 'Homerun',
    # match/game units
    'Round', 'Match', 'Matchup', 'Matchups', 'Game', 'Games', 'Ranked',
    # common title-case verbs / function words that show up in headlines
    # when a headline uses Title Case ("Matchups Are Set", "Wins Big",
    # "Says Trade Off"). These NEVER form part of a real person's name.
    'Are', 'Is', 'Was', 'Were', 'Will', 'Would', 'Could', 'Should',
    'Been', 'Have', 'Had', 'Has', 'May', 'Might', 'Must', 'Set',
    'Ends', 'Wins', 'Loses', 'Sets', 'Beats', 'Falls', 'Rises',
    'Says', 'Said', 'Reveals', 'Reports', 'Announced', 'Announces',
    'Backs', 'Blasts', 'Brings', 'Calls', 'Confirms', 'Denies',
    'Faces', 'Files', 'Finds', 'Gets', 'Hits', 'Joins', 'Leads',
    'Leaves', 'Loses', 'Meets', 'Names', 'Opens', 'Picks', 'Plans',
    'Plays', 'Posts', 'Reaches', 'Rejects', 'Returns', 'Ruled',
    'Rules', 'Sees', 'Sells', 'Sends', 'Shares', 'Shows', 'Signs',
    'Sits', 'Speaks', 'Sparks', 'Stops', 'Suspends', 'Takes',
    'Talks', 'Tells', 'Tops', 'Trades', 'Turns', 'Warns', 'Wants',
    'Reveal', 'Reveals', 'Reveals',
    # MLB teams
    'Yankees', 'Dodgers', 'Mets', 'Braves', 'Cubs', 'Marlins',
    'Guardians', 'Nationals', 'Orioles', 'Athletics', 'Pirates',
    'Cardinals', 'Padres', 'Phillies', 'Astros', 'Brewers',
    'Diamondbacks', 'Rangers', 'Rays', 'Twins', 'Angels', 'Royals',
    'Reds', 'Sox', 'Giants', 'Mariners',
    # NBA / WNBA teams
    'Warriors', 'Lakers', 'Celtics', 'Bulls', 'Bucks', 'Knicks',
    'Clippers', 'Mavericks', 'Nuggets', 'Rockets', 'Timberwolves',
    'Pistons', 'Cavaliers', 'Raptors', 'Grizzlies', 'Pelicans',
    'Suns', 'Blazers', 'Spurs', 'Magic', 'Heat', 'Hawks', 'Sixers',
    '76ers', 'Trail', 'Sparks', 'Storm', 'Aces', 'Liberty', 'Sun',
    'Mercury', 'Sky', 'Mystics', 'Dream', 'Wings', 'Fever',
    # NFL teams
    'Chiefs', 'Patriots', 'Bills', 'Dolphins', 'Jets', 'Ravens',
    'Bengals', 'Browns', 'Steelers', 'Texans', 'Colts', 'Jaguars',
    'Titans', 'Broncos', 'Raiders', 'Chargers', 'Falcons', 'Panthers',
    'Saints', 'Vikings', 'Packers', 'Lions', 'Bears', 'Buccaneers',
    'Rams', '49ers', 'Seahawks', 'Cowboys', 'Eagles', 'Giants',
    'Commanders',
    # NHL teams
    'Canadiens', 'Canucks', 'Oilers', 'Penguins', 'Flyers', 'Bruins',
    'Blackhawks', 'Sharks', 'Ducks', 'Coyotes', 'Predators', 'Blues',
    'Wild', 'Avalanche', 'Stars', 'Hurricanes', 'Panthers',
    'Lightning', 'Devils', 'Islanders', 'Capitals', 'Senators',
    'Maple', 'Leafs',
    # college mascots that show up in sports headlines
    'Wolverines', 'Buckeyes', 'Sooners', 'Longhorns', 'Aggies',
    'Volunteers', 'Bulldogs', 'Gators', 'Seminoles', 'Tar',
    'Heels', 'Blue', 'Devils', 'Wildcats', 'Cardinals', 'Cavaliers',
    'Hokies', 'Hurricanes', 'Wolfpack', 'Tigers', 'Bulldogs',
    'Ducks', 'Trojans', 'Bruins', 'Huskies', 'Cougars',
    # cities whose Title Case form is almost always a team name
    'Vegas',
    # music / entertainment brand tokens
    'BTS', 'BLACKPINK', 'Grammys', 'Coachella',
}

# Curated multi-word phrases that pass the token check but aren't
# people. Normalized lowercase, hyphen -> space, whitespace collapsed.
_NON_PERSON_PHRASES = {
    'world cup', 'super bowl', 'stanley cup', 'home run',
    'all star', 'all stars', 'grand slam', 'champions league',
    'premier league', 'la liga', 'serie a', 'ryder cup',
    'us open', 'french open', 'australian open', 'masters tournament',
    'formula 1', 'formula one', 'nascar cup',
    'wall street', 'main street', 'white house', 'oval office',
    'supreme court', 'united states', 'united nations',
    'new york', 'los angeles', 'san francisco', 'las vegas',
    'silicon valley', 'wall street',
    # common headline stock phrases
    'trade deadline', 'free agency', 'training camp', 'summer league',
    'preseason game', 'playoff run',
    # team-name color-word fragments (surface only when the "Sox"/"Jays"
    # suffix has already been trimmed off): "Boston Red" from "Boston
    # Red Sox", "Chicago White" from "Chicago White Sox", etc.
    'boston red', 'chicago white', 'toronto blue',
    # venues that show up as Title Case runs in sports headlines
    'madison square garden', 'yankee stadium', 'fenway park',
    'wrigley field', 'dodger stadium', 'staples center',
    'crypto arena', 'chase center', 'sofi stadium',
    'radio city', 'radio city music hall', 'kia forum',
    # geographic proper-noun phrases that shouldn't count as people
    'new york city', 'los angeles', 'san francisco', 'las vegas',
    'silicon valley', 'south beach', 'south park', 'new jersey',
    'new mexico', 'new orleans', 'new hampshire',
    'north carolina', 'south carolina', 'north dakota',
    'south dakota', 'west virginia', 'east coast', 'west coast',
    # media / show titles that repeat in headlines
    'saturday night', 'saturday night live', 'sunday night',
    'monday night', 'thursday night',
}

_NAME_RE = re.compile(
    # 2-4 capitalized-word runs. Each word starts with a capital letter
    # followed by AT LEAST ONE lowercase letter (so all-caps abbrevs like
    # WNBA / NBA / NFL / MLB / MLS / NHL / NCAA / UFC / PGA / LPGA don't
    # get captured as a name token), then any mix of letters + apostrophe
    # + hyphen. Allows single or multi-word runs joined by space.
    r"\b([A-Z][a-z][A-Za-z'\-]*(?: [A-Z][a-z][A-Za-z'\-]*){1,3})\b"
)


def _extract_person_names(text: str) -> list[str]:
    """Extract likely person names from a headline / query / caption.

    Applies four cascading filters:
      1. Reject if any token is in _STOPWORDS_UC (generic headline noise)
      2. Reject if any token is in _NON_PERSON_TOKENS (event/team/media)
      3. Reject if the normalized full phrase is in _NON_PERSON_PHRASES
      4. Trim trailing possessive tokens (e.g. "Mets' Carson Benge" ->
         "Carson Benge") so team names on the left don't inflate the
         captured span.
    """
    if not text:
        return []
    hits: list[str] = []
    for m in _NAME_RE.finditer(text):
        name = m.group(1).strip()
        parts = [p for p in name.split(' ') if p]
        # Trim tokens from either end that are clearly non-person (team,
        # league, tournament, media word). This handles patterns like
        # "Mets' Carson Benge" -> "Carson Benge" and "Geno Auriemma WNBA"
        # -> "Geno Auriemma". Middle-of-run reject is left to the whole-
        # phrase check below.
        def _is_bad_token(tok: str) -> bool:
            stripped = tok.rstrip("'s").rstrip("'")
            return (stripped in _STOPWORDS_UC
                    or stripped in _NON_PERSON_TOKENS
                    or tok in _STOPWORDS_UC
                    or tok in _NON_PERSON_TOKENS)
        while parts and _is_bad_token(parts[0]):
            parts = parts[1:]
        while parts and _is_bad_token(parts[-1]):
            parts = parts[:-1]
        if len(parts) < 2:
            continue
        # After trim, any bad token INSIDE the run (rare, but possible
        # when a team name sits between two people names) is a full
        # reject: the run isn't a single person's name.
        if any(_is_bad_token(p) for p in parts):
            continue
        # Additional hyphen-split token check: catches "All-Star",
        # "Home-Run" as single tokens that would slip through the
        # space-only split above.
        hyphen_parts = re.split(r'[- ]', ' '.join(parts))
        if any(hp in _STOPWORDS_UC or hp in _NON_PERSON_TOKENS
               for hp in hyphen_parts):
            continue
        clean = ' '.join(parts)
        normalized = re.sub(r'[-_]+', ' ', clean).strip().lower()
        normalized = re.sub(r'\s+', ' ', normalized)
        if normalized in _NON_PERSON_PHRASES:
            continue
        hits.append(clean)
    return hits


def _fetch_trending_people(headlines: list[dict],
                            search_terms: list[dict],
                            lookback_days: int,
                            articles_by_source: Optional[list[dict]] = None,
                            social_trending: Optional[dict] = None) -> list[dict]:
    """Mine trending people from headlines + searches + news articles + social.

    Every source contributes to a shared mention counter, weighted so
    that a name appearing across DIFFERENT source types (news + search +
    social) ranks above a name that only spikes in one silo. Extra
    filters in `_extract_person_names` keep events / teams / tournaments
    from getting counted as people.

    Sources (all optional except headlines + search_terms):
      - headlines: trending news headlines (weight 1)
      - search_terms: Google Trends queries + related snippets (weight 2)
      - articles_by_source: full article-by-outlet grid (weight 1)
      - social_trending: Reddit/X/YouTube/TikTok/Instagram items (weight 2)
    """
    corpus: list[tuple[str, str, str, str]] = []
    for h in (headlines or []):
        corpus.append(('headline', h.get('title', ''),
                        h.get('source', ''), h.get('url', '')))
    for s in (search_terms or []):
        corpus.append(('search', s.get('term', ''), '', ''))
        for rel in (s.get('related') or []):
            corpus.append(('search', rel, '', ''))
    for src in (articles_by_source or []):
        source_name = src.get('source', '') or ''
        for art in (src.get('articles') or []):
            corpus.append(('article', art.get('title', '') or '',
                            source_name, art.get('url', '') or ''))
    if social_trending:
        for slug, block in social_trending.items():
            if not isinstance(block, dict):
                continue
            label = block.get('label', slug) or slug
            for it in (block.get('items') or []):
                if not isinstance(it, dict):
                    continue
                for field in ('title', 'description', 'caption'):
                    text_val = it.get(field) or ''
                    if text_val:
                        corpus.append(('social', text_val, label,
                                        it.get('url', '') or ''))

    counts: Counter = Counter()
    source_diversity: dict[str, set[str]] = defaultdict(set)
    contexts: dict[str, list[str]] = defaultdict(list)
    for kind, text, source, url in corpus:
        for name in _extract_person_names(text):
            if kind == 'search' or kind == 'social':
                counts[name] += 2
            else:
                counts[name] += 1
            source_diversity[name].add(kind)
            snippet = (text or '').strip()
            if snippet and len(contexts[name]) < 3:
                contexts[name].append(snippet[:140])

    # Cross-source diversity bonus: names that show up across multiple
    # source types (news + search + social) get a lift so they beat
    # single-silo spikes.
    for name in list(counts.keys()):
        kinds = source_diversity.get(name, set())
        if len(kinds) >= 3:
            counts[name] += 3
        elif len(kinds) == 2:
            counts[name] += 1

    people: list[dict] = []
    for name, cnt in counts.most_common(80):
        if cnt < 2:
            continue
        people.append({
            'name':      name,
            'mentions':  cnt,
            'context':   contexts.get(name, [])[:3],
            'sources':   sorted(source_diversity.get(name, [])),
        })
        if len(people) >= 40:
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

    # Project raw counts up to US gen pop (329.99M). Both fields are the
    # raw count multiplied by 329_990_000 so the front end can display
    # "gen-pop-scaled" numbers consistent with the rest of the dashboard.
    # Raw counts stay on each row too so the frontend can toggle if needed.
    _US_GEN_POP = 329_990_000
    for p in people:
        p['mentions_projected']  = int(p.get('mentions')  or 0) * _US_GEN_POP
        p['pageviews_projected'] = int(p.get('pageviews') or 0) * _US_GEN_POP

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


def _fetch_social_trending(state: Optional[str], lookback_days: int,
                             keywords: Optional[list[str]] = None) -> dict:
    """Fan out to every social platform's trending endpoint.

    Reddit is live at request time via the Atom `/.rss` feed. YouTube,
    TikTok, and X are populated from S3 snapshots written by
    `scripts/trends_scrapers/`. Instagram is scaffolded but doesn't
    have a source wired yet - its snapshot returns `available=False`
    which cascades to a "coming soon" tile in the UI.

    For state / DMA selections, non-Reddit snapshots are reordered by
    `keywords` so region-mentioning items rise to the top; Reddit is
    already state-aware via its per-state subreddit merge.
    """
    result = {slug: {'label': label, 'items': [], 'available': avail}
              for slug, label, avail in SOCIAL_PLATFORMS}

    # Reddit: prefer the daily Hetzner snapshot (residential egress
    # succeeds where Render's datacenter egress is blocked by Reddit).
    # Fall back to the live Atom RSS fetch only if the snapshot is
    # missing or empty - keeps local dev usable before the first cron
    # run has landed a snapshot.
    reddit_snap = _read_snapshot('reddit')
    reddit_items = _snapshot_items_for_geo(reddit_snap, state, keywords=keywords) if reddit_snap else []
    if not reddit_items:
        try:
            reddit_items = _fetch_reddit_popular(state, lookback_days)
        except Exception as e:
            logger.debug("trends_iq reddit live fallback failed: %s", e)
            reddit_items = []
    result['reddit'] = {
        'label':      'Reddit',
        'items':      reddit_items[:20],
        'available':  bool(reddit_items),
        'fetched_at': (reddit_snap or {}).get('fetched_at'),
    }
    if reddit_snap and reddit_snap.get('error') and not reddit_items:
        result['reddit']['note'] = f"latest snapshot: {reddit_snap['error']}"

    for slug, label, _static_avail in SOCIAL_PLATFORMS:
        if slug == 'reddit':
            continue
        snap = _read_snapshot(slug)
        if not snap:
            continue
        items = _snapshot_items_for_geo(snap, state, keywords=keywords)
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
# Card 5b: Trending on streaming (Netflix, Disney+, Hulu, Max, Prime, ESPN+)
# ============================================================================
# Every platform's items are split into Film + TV and stamped with a
# `weeks_in_top10` count so all six platforms get the same treatment
# Netflix has always had (which comes from Netflix's own weekly TSV).
# For the non-Netflix platforms:
#   - Film/TV: infer from category_display when the scraper set it, and
#     from URL / collection heuristics when it didn't (Disney+, ESPN+).
#   - Weeks-on-chart: count distinct ISO-weeks the title appeared in the
#     dated snapshot history at `trends_iq_snapshots/{YYYY-MM-DD}/
#     {slug}.json`. Snapshots are retained indefinitely (no S3 lifecycle
#     policy on that prefix) so this window grows every day.
#
# Retention: `write_snapshot(also_dated=True)` (the default) writes
# BOTH `latest/{source}.json` AND `{YYYY-MM-DD}/{source}.json`. No purge
# job runs against the dated prefix, so day-N history is fully preserved
# for retrospective analysis, movers, and this weeks-on-chart tally.

# URL / collection substrings that pin an item to Film vs TV when the
# scraper didn't set category_display. The lists are intentionally short;
# ambiguous items fall through to "other" and end up in TV (the safer
# default because most streaming platforms are TV-heavy).
_FILM_URL_HINTS = (
    '/movie', '/movies', '/film', '/films',
    '/detail/',                              # Prime Video's per-movie deep-link
    '/gp/video/detail',
    '/browse/entity-',                       # Disney+ specials/features
)
_TV_URL_HINTS = (
    '/series', '/tv-series', '/show', '/shows',
    '/season', '/seasons', '/episode',
)
_FILM_TITLE_HINTS = (
    ' movie', ' the movie', ' feature',
)
_TV_TITLE_HINTS = (
    ' season ', ' episode ', ' series', ' show', ' presents',
    ': a docuseries', 'docuseries',
)


def _classify_film_or_tv(item: dict) -> str:
    """Return 'Film', 'TV', or '' when we can't tell.

    Priority:
      1. Explicit `category_display` from the scraper (Netflix, Hulu,
         Prime Video, Max all set this reliably).
      2. URL substring hints.
      3. Title substring hints.
      4. Fall through to '' (frontend will bucket into TV as the
         default catch-all).
    """
    cd = (item.get('category_display') or '').strip().lower()
    if cd in ('film', 'films', 'movie', 'movies'):
        return 'Film'
    if cd in ('tv', 'series', 'show', 'shows'):
        return 'TV'
    # `category` field (Netflix) sometimes has raw string like "Films".
    cat = (item.get('category') or '').strip().lower()
    if cat in ('films', 'film', 'movies', 'movie'):
        return 'Film'
    if cat in ('tv', 'shows', 'series'):
        return 'TV'
    url = (item.get('url') or '').lower()
    # `/browse/entity-` (Disney+ / ESPN+) is used for BOTH films and
    # shows, so we only take it as a Film signal when title/collection
    # hints don't say otherwise. ESPN+ specifically is 100% sports
    # programming - its entity URLs are all studio/live shows and
    # should never be classified as Film.
    coll = (item.get('collection') or '').lower()
    if 'espn' in coll or 'sportscenter' in url or 'espnplus' in url:
        return 'TV'
    for hint in _FILM_URL_HINTS:
        if hint in url:
            if hint == '/browse/entity-' and any(h in url for h in _TV_URL_HINTS):
                continue
            return 'Film'
    for hint in _TV_URL_HINTS:
        if hint in url:
            return 'TV'
    title = ' ' + (item.get('title') or '').lower() + ' '
    for hint in _TV_TITLE_HINTS:
        if hint in title:
            return 'TV'
    for hint in _FILM_TITLE_HINTS:
        if hint in title:
            return 'Film'
    return ''


def _split_streaming_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Bucket items into (films, tv). Unclassified items default to TV
    since most streaming catalogs are TV-heavy and users expect the TV
    tab to be the fuller list."""
    films: list[dict] = []
    tv:    list[dict] = []
    for it in items:
        bucket = _classify_film_or_tv(it)
        # Copy so we don't mutate the caller's items (they're often
        # shared references coming out of the snapshot cache).
        row = dict(it)
        if bucket == 'Film':
            row['category_display'] = 'Film'
            films.append(row)
        else:
            row['category_display'] = 'TV'
            tv.append(row)
    # Preserve rank ordering within each bucket.
    for i, r in enumerate(films, 1):
        r['bucket_rank'] = i
    for i, r in enumerate(tv, 1):
        r['bucket_rank'] = i
    return films, tv


# Lookback window for the weeks-on-chart tally. 12 weeks matches Netflix's
# "Top 10 - weeks in top 10" limit and is small enough that the S3 scan
# stays fast (`get_object` per date, at most 84 calls, in parallel).
_STREAMING_HISTORY_WEEKS = 12
_STREAMING_HISTORY_TTL_S = 15 * 60  # in-process cache

# Cache: {slug: (fetched_at_epoch, {title_norm: set[(iso_year, iso_week)]})}
_STREAMING_WEEKS_CACHE: dict[str, tuple[float, dict[str, set[tuple[int, int]]]]] = {}


def _title_norm(t: str) -> str:
    """Case-insensitive, whitespace/punct-collapsed title key so
    'The Fox Hollow Murders' and 'the fox hollow murders' resolve to
    the same slot when comparing across snapshots."""
    t = (t or '').strip().lower()
    return re.sub(r'[^a-z0-9]+', '', t)


def _iso_week_key(d) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


def _load_streaming_history_weeks(slug: str) -> dict[str, set[tuple[int, int]]]:
    """For `slug`, scan the past `_STREAMING_HISTORY_WEEKS` weeks of
    dated snapshots and return `{title_norm: {(iso_year, iso_week), ...}}`.

    We fetch snapshots in parallel because each is a single S3
    get_object. Even 12 weeks x 7 days = 84 keys completes in <2s.

    Returned sets are what the annotator unions with today's ISO week
    to produce the final `weeks_in_top10` count. Storing sets (not
    counts) is what lets a title that appeared on Sat + Sun of last
    week and Mon of this week read `wk 2` instead of `wk 1`.
    """
    now = time.time()
    cached = _STREAMING_WEEKS_CACHE.get(slug)
    if cached and (now - cached[0]) < _STREAMING_HISTORY_TTL_S:
        return cached[1]

    s3 = _s3_client()
    if s3 is None:
        _STREAMING_WEEKS_CACHE[slug] = (now, {})
        return {}

    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(1, _STREAMING_HISTORY_WEEKS * 7 + 1)]

    weeks_by_title: dict[str, set[tuple[int, int]]] = {}

    def _fetch_one(d):
        key = f'trends_iq_snapshots/{d.isoformat()}/{slug}.json'
        try:
            resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
            data = json.loads(resp['Body'].read().decode('utf-8'))
            return d, (data.get('national') or [])
        except Exception:
            return d, []

    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for d, items in ex.map(_fetch_one, dates):
                wkey = _iso_week_key(d)
                for it in items:
                    tn = _title_norm(it.get('title') or '')
                    if not tn:
                        continue
                    weeks_by_title.setdefault(tn, set()).add(wkey)
    except Exception as e:
        logger.info("_load_streaming_history_weeks(%s) failed: %s", slug, e)

    _STREAMING_WEEKS_CACHE[slug] = (now, weeks_by_title)
    return weeks_by_title


def _annotate_streaming_weeks(slug: str, items: list[dict]) -> None:
    """Stamp each item with `weeks_in_top10` (in place).

    For Netflix we KEEP the value the TSV shipped (their tally goes back
    much further than our snapshot history). For everything else we
    union the historical ISO-weeks the title appeared in with THIS
    week, so a title that appeared 3 days last week and 1 day this week
    reads `wk 2`.
    """
    if not items:
        return
    if slug == 'netflix':
        return  # TSV-provided value is authoritative.
    history = _load_streaming_history_weeks(slug)
    today_key = _iso_week_key(datetime.now(timezone.utc).date())
    for it in items:
        tn = _title_norm(it.get('title') or '')
        past = history.get(tn, set())
        combined = past | {today_key}
        it['weeks_in_top10'] = min(len(combined), _STREAMING_HISTORY_WEEKS + 1)


def _fetch_streaming_trending(state: Optional[str], lookback_days: int,
                                keywords: Optional[list[str]] = None) -> dict:
    """Fan out to every streaming platform's daily snapshot.

    Netflix is populated by the public TSV scraper (no auth). The rest
    are Playwright + donated-cookie scrapers - they'll return
    `available=False` until Jenna donates cookies for that domain.

    Every returned platform payload includes:
      - `items`: the full ranked list (backward compat)
      - `films`: films-only slice, re-ranked from 1
      - `tv`:    TV-only slice, re-ranked from 1
      - each item stamped with `weeks_in_top10` from S3 history
      - `week_us`: the ISO date the current snapshot's rankings
        represent (Netflix ships it as their week label; for the others
        we derive from `fetched_at`).

    Geographic filtering is minimal here: rankings are inherently
    national. `keywords` still nudges region-flavored titles up.
    """
    result = {slug: {'label': label, 'items': [], 'films': [], 'tv': [],
                       'available': avail}
              for slug, label, avail in STREAMING_PLATFORMS}

    for slug, label, _static_avail in STREAMING_PLATFORMS:
        snap = _read_snapshot(slug)
        if not snap:
            continue
        items = _snapshot_items_for_geo(snap, state, keywords=keywords)
        items = items[:25]
        _annotate_streaming_weeks(slug, items)
        snap_available = snap.get('available')
        if snap_available is None:
            snap_available = bool(items)

        films, tv = _split_streaming_items(items)

        # Netflix's own scraper already writes us_films/us_tv from the
        # official TSV. Prefer those (they preserve category ordering
        # from Netflix's own weekly ranking); otherwise use our split.
        if slug == 'netflix':
            netflix_films = snap.get('us_films') or []
            netflix_tv    = snap.get('us_tv')    or []
            if netflix_films or netflix_tv:
                films = netflix_films[:20]
                tv    = netflix_tv[:20]

        payload = {
            'label':      label,
            'items':      items[:20],
            'films':      films[:20],
            'tv':         tv[:20],
            'available':  bool(snap_available),
            'fetched_at': snap.get('fetched_at'),
        }

        # Every platform gets a `week_us` label so the frontend can
        # show "Week of YYYY-MM-DD" consistently. Netflix uses its own
        # weekly release date; others fall back to the fetched_at day.
        if snap.get('week_us'):
            payload['week_us'] = snap['week_us']
        elif snap.get('fetched_at'):
            try:
                dt = datetime.fromisoformat(snap['fetched_at'].replace('Z', '+00:00'))
                # Anchor to the most recent Sunday to align with Netflix's
                # weekly cadence.
                sunday = dt.date() - timedelta(days=(dt.weekday() + 1) % 7)
                payload['week_us'] = sunday.isoformat()
            except Exception:
                pass

        # Preserve Netflix's global lists for the "US vs global" panel
        # the frontend might grow into.
        if slug == 'netflix':
            for extra in ('week_global',
                           'global_films_en', 'global_tv_en',
                           'global_films_nonen', 'global_tv_nonen'):
                if extra in snap:
                    payload[extra] = snap[extra]

        if snap.get('error'):
            payload['note'] = f"latest snapshot: {snap['error']}"
        result[slug] = payload
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
# Amazon renders the visible price via a whole+fraction span pair AND
# a screen-reader `<span class="a-offscreen">$X.YY</span>` copy. The
# offscreen span is the most stable target across their layout churn,
# and the p13n-sc-price class shows up on the bestsellers grid. We
# accept either.
_AMAZON_PRICE_RE = re.compile(
    r'<span[^>]*class="[^"]*(?:a-offscreen|_cDEzb_p13n-sc-price|p13n-sc-price)[^"]*"[^>]*>'
    r'\s*(\$[0-9][0-9,]*(?:\.[0-9]{2})?)\s*</span>',
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
        img_m   = _AMAZON_IMG_RE.search(chunk)
        dp_m    = _AMAZON_DP_RE.search(chunk)
        price_m = _AMAZON_PRICE_RE.search(chunk)
        image   = img_m.group(1) if img_m else ''
        dp_path = dp_m.group(1)  if dp_m  else ''
        # The a-offscreen span often duplicates the price for accessibility
        # AND for the "was" strikethrough; take the first hit (current price).
        price   = price_m.group(1).strip() if price_m else ''
        name = _slug_to_name(dp_path)
        if not name:
            continue
        seen_asins.add(asin)
        items.append({
            'rank':  len(items) + 1,
            'name':  name[:180],
            'url':   f'https://www.amazon.com{dp_path.split("/ref=")[0]}',
            'image': image,
            'price': price,
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


def _fetch_trending_products(keywords: Optional[list[str]] = None) -> list[dict]:
    """Aggregate the retailer tiles.

    Amazon runs live at request time. Every other retailer is populated
    from S3 snapshots written by `scripts/trends_scrapers/` on the
    Hetzner nightly cron. Missing / stale snapshots degrade to the
    coming-soon placeholder for that tile.

    Retailer feeds are national by nature (bestseller listings don't
    ship in per-DMA cuts). For state / DMA selections we reorder each
    retailer's product list so items whose names mention the region
    (regional brands, city-tied SKUs, etc.) surface first. National
    items stay in the list as filler so tiles never render empty.
    """
    def _reorder_products(items: list[dict]) -> list[dict]:
        if not keywords or not items:
            return items
        return _reorder_by_region(items, keywords, text_getter=lambda it: it.get('name') or '')

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
                    reordered_cats = []
                    for c in cats:
                        reordered_cats.append({
                            'category': c.get('category') or c.get('label') or '',
                            'items':    _reorder_products(c.get('items') or []),
                        })
                    entry['categories'] = reordered_cats
                    entry['items']      = reordered_cats[0].get('items') or []
            except Exception as e:
                logger.debug("trends_iq amazon movers failed: %s", e)
            result.append(entry)
            continue

        snap = _read_snapshot(slug)
        if snap:
            items = _reorder_products(list(snap.get('national') or []))
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
                        'items':    _reorder_products((c.get('items') or []))[:10],
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

    label, state, dma_value = _resolve_geo(filters)
    lookback_days = int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS)
    geo_kws = _geo_keywords(state, dma_value)

    tasks = {
        'trending_searches':   lambda: _fetch_trending_searches(state, lookback_days),
        'headlines_pack':      lambda: _fetch_trending_headlines_and_sources(geo_kws),
        'social_trending':     lambda: _fetch_social_trending(state, lookback_days,
                                                                 keywords=geo_kws),
        'streaming_trending':  lambda: _fetch_streaming_trending(state, lookback_days,
                                                                    keywords=geo_kws),
        'products_by_retailer':lambda: _fetch_trending_products(keywords=geo_kws),
        'movers':              lambda: compute_search_movers(state),
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
    social_trending    = results.get('social_trending') or {}
    streaming_trending = results.get('streaming_trending') or {}
    products           = results.get('products_by_retailer') or []
    movers             = results.get('movers') or {'available': False,
                                                     'note': 'warming up'}

    trending_people = _fetch_trending_people(
        headlines,
        trending_searches,
        lookback_days,
        articles_by_source=articles_by_source,
        social_trending=social_trending,
    )

    # Stamp a rising/falling/stable/new trend on every trending-person
    # row by comparing today's mentions + rank against yesterday's
    # snapshot. MUST run BEFORE _write_history_snapshots writes
    # today's copy - otherwise the "prior" lookup would find today's
    # own data and every row would read as stable.
    trending_people = _annotate_people_with_trend(trending_people)

    # Attach a search/media/social buzz-mix to every Movers row so the
    # UI can show where each trend's interest is being driven from.
    movers = _annotate_movers_with_platform_mix(
        movers, trending_searches, social_trending)

    # Split the trending search pool into the 5-card layout the UI renders:
    # Overall (all rows, scrollable) + Entertainment / Retail / Politics /
    # Finance (top 20 each). Category buckets are computed from the same
    # underlying list so counts add up predictably.
    searches_by_category = _bucket_searches_by_category(trending_searches, per_bucket=100)

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
            'trending_searches':              trending_searches,
            'trending_searches_by_category':  searches_by_category,
            'trending_headlines':             headlines,
            'articles_by_source':             articles_by_source,
            'trending_people':                trending_people,
            'social_trending':                social_trending,
            'streaming_trending':             streaming_trending,
            'products_by_retailer':           products,
            'movers':                         movers,
        },
        'counts': {
            'searches':      len(trending_searches),
            'sports':        len(searches_by_category.get('sports')        or []),
            'entertainment': len(searches_by_category.get('entertainment') or []),
            'gaming':        len(searches_by_category.get('gaming')        or []),
            'tech':          len(searches_by_category.get('tech')          or []),
            'weather':       len(searches_by_category.get('weather')       or []),
            'crime':         len(searches_by_category.get('crime')         or []),
            'health':        len(searches_by_category.get('health')        or []),
            'food':          len(searches_by_category.get('food')          or []),
            'travel':        len(searches_by_category.get('travel')        or []),
            'auto':          len(searches_by_category.get('auto')          or []),
            'fashion':       len(searches_by_category.get('fashion')       or []),
            'home':          len(searches_by_category.get('home')          or []),
            'business':      len(searches_by_category.get('business')      or []),
            'retail':        len(searches_by_category.get('retail')        or []),
            'politics':      len(searches_by_category.get('politics')      or []),
            'conservative':  len(searches_by_category.get('conservative')  or []),
            'progressive':   len(searches_by_category.get('progressive')   or []),
            'finance':       len(searches_by_category.get('finance')       or []),
            'headlines':     len(headlines),
            'sources':       len(articles_by_source),
            'people':        len(trending_people),
            'retailers':     len(products),
            'streaming':     sum(1 for p in streaming_trending.values()
                                    if (p or {}).get('available')),
            'movers':    (len(movers.get('breakout') or []) +
                           len(movers.get('rising')   or []) +
                           len(movers.get('falling')  or []) +
                           len(movers.get('sustained') or [])),
        },
    }

    _cache_put(filters, payload)
    _write_history_snapshots(headlines, trending_people)
    payload['from_cache'] = False
    return payload


# ============================================================================
# Historical snapshot writer for headlines + trending people
# ============================================================================
# The history endpoint reads dated JSON at
# `trends_iq_snapshots/{YYYY-MM-DD}/gdelt.json` (headlines) and
# `trends_iq_snapshots/{YYYY-MM-DD}/gdelt-people.json` (people). Every
# scraper source drops its own file at that same prefix; this helper
# does the same for the two GDELT-derived cards so their history
# button has data to render.
#
# Idempotent per UTC day: uses an if-not-exists check so hot dashboard
# reloads don't rewrite S3 constantly. First-of-day request wins.
_HISTORY_SNAPSHOT_PREFIX = 'trends_iq_snapshots/'
_SNAPSHOT_WRITE_STATE: dict[str, str] = {}


def _snapshot_key(source: str, day_iso: str) -> str:
    return f'{_HISTORY_SNAPSHOT_PREFIX}{day_iso}/{source}.json'


def _put_dated_snapshot(source: str, day_iso: str, rows: list[dict]) -> None:
    """Write a dated snapshot if today's copy doesn't already exist.

    Wrapped in a broad try/except: history is nice-to-have, we never
    want to fail the dashboard build if S3 hiccups.
    """
    if _SNAPSHOT_WRITE_STATE.get(source) == day_iso:
        return
    try:
        s3 = _s3_client()
        key = _snapshot_key(source, day_iso)
        try:
            s3.head_object(Bucket=S3_CACHE_BUCKET, Key=key)
            _SNAPSHOT_WRITE_STATE[source] = day_iso
            return
        except Exception:
            pass
        payload = {
            'source':     source,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'national':   rows,
        }
        s3.put_object(
            Bucket=S3_CACHE_BUCKET, Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
        )
        _SNAPSHOT_WRITE_STATE[source] = day_iso
        logger.info("wrote history snapshot s3://%s/%s (%d rows)",
                     S3_CACHE_BUCKET, key, len(rows))
    except Exception as e:
        logger.debug("history snapshot write failed for %s: %s", source, e)


def _write_history_snapshots(headlines: list[dict],
                              trending_people: list[dict]) -> None:
    """Snapshot the two GDELT-derived cards for `trends_history`.

    Both files share the `{'national': [...]}` layout so
    `trends_history.history_for_scraper` reads them the same way it
    reads Netflix/Target/Instagram snapshots.
    """
    day_iso = date.today().isoformat()

    headline_rows = []
    for i, h in enumerate(headlines or []):
        title = (h.get('title') or '').strip()
        if not title:
            continue
        headline_rows.append({
            'rank':   i + 1,
            'title':  title,
            'url':    h.get('url', ''),
            'source': h.get('source', ''),
            'geo':    h.get('geo', 'National'),
        })
    if headline_rows:
        _put_dated_snapshot('gdelt', day_iso, headline_rows)

    people_rows = []
    for i, p in enumerate(trending_people or []):
        name = (p.get('name') or '').strip()
        if not name:
            continue
        people_rows.append({
            'rank':      i + 1,
            'name':      name,
            'mentions':  int(p.get('mentions') or 0),
            'pageviews': int(p.get('pageviews') or 0),
            'context':   p.get('context') or [],
        })
    if people_rows:
        _put_dated_snapshot('gdelt-people', day_iso, people_rows)


# ============================================================================
# Trending-people trend annotation (rising / falling / stable / new)
# ============================================================================
# Compares today's per-person mentions + rank against yesterday's
# `gdelt-people.json` snapshot (falls back to the most recent
# available snapshot within the last 7 days) and stamps a `trend`
# field on each row so the UI can render an up / down / flat arrow.
#
# Thresholds:
#   NEW      - name not present in the compared snapshot
#   RISING   - mentions grew >=25% OR rank improved by >=3
#   FALLING  - mentions dropped >=25% OR rank worsened by >=3
#   STABLE   - everything else (name present, minor movement only)
#
# Comparing against the CLOSEST prior snapshot (not against the same
# calendar day last week) keeps the signal fresh on days where the
# scraper has just started collecting: we simply don't stamp a trend
# on people we've never seen before.
# ============================================================================
_PEOPLE_TREND_RISE_PCT = 0.25   # mentions grew 25% or more -> rising
_PEOPLE_TREND_DROP_PCT = 0.25   # mentions dropped 25% or more -> falling
_PEOPLE_TREND_RANK_DELTA = 3    # rank moved by 3 or more -> rising/falling
_PEOPLE_TREND_LOOKBACK_DAYS = 7 # max days back we'll search for a baseline


def _load_prior_people_snapshot(now: datetime) -> tuple[Optional[dict], Optional[str]]:
    """Find the most recent gdelt-people snapshot BEFORE today.

    Walks yesterday, day-before, ... up to 7 days back. Returns
    `(rows_by_name_lower, day_iso)` for the first day that has one,
    or `(None, None)` when no prior data is available (fresh install).
    """
    try:
        s3 = _s3_client()
    except Exception:
        return None, None
    for off in range(1, _PEOPLE_TREND_LOOKBACK_DAYS + 1):
        d_iso = (now - timedelta(days=off)).date().isoformat()
        key = _snapshot_key('gdelt-people', d_iso)
        try:
            obj = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
            body = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            continue
        rows = body.get('national') or []
        if not rows:
            continue
        by_name: dict[str, dict] = {}
        for r in rows:
            name = (r.get('name') or '').strip().lower()
            if name:
                by_name[name] = r
        if by_name:
            return by_name, d_iso
    return None, None


def _annotate_people_with_trend(people_rows: list[dict]) -> list[dict]:
    """Stamp `trend` + baseline metadata on every trending-person row.

    Mutates and returns the same list (also safe if called twice).
    Every returned row has:
      - trend: 'rising' | 'falling' | 'stable' | 'new' | None
      - trend_baseline_day: 'YYYY-MM-DD' or None
      - mentions_change_pct: float or None (relative delta from
         baseline, e.g. 0.42 = +42%)
      - rank_delta: int or None (positive = improved rank)
    Rows with no baseline data get trend=None so the UI can skip
    rendering the arrow rather than falsely stamping "stable".
    """
    if not people_rows:
        return people_rows or []
    now = datetime.now(timezone.utc)
    prior, prior_day = _load_prior_people_snapshot(now)
    if not prior:
        for r in people_rows:
            r['trend'] = None
            r['trend_baseline_day']  = None
            r['mentions_change_pct'] = None
            r['rank_delta']          = None
        return people_rows

    for i, r in enumerate(people_rows):
        name = (r.get('name') or '').strip().lower()
        r['trend_baseline_day']  = prior_day
        r['mentions_change_pct'] = None
        r['rank_delta']          = None
        if not name:
            r['trend'] = None
            continue
        prev = prior.get(name)
        if not prev:
            r['trend'] = 'new'
            continue
        prev_mentions = int(prev.get('mentions') or 0)
        prev_rank     = int(prev.get('rank')     or 0)
        cur_mentions  = int(r.get('mentions')    or 0)
        cur_rank      = i + 1
        change_pct = None
        if prev_mentions > 0:
            change_pct = (cur_mentions - prev_mentions) / float(prev_mentions)
        rank_delta = (prev_rank - cur_rank) if prev_rank else None
        r['mentions_change_pct'] = change_pct
        r['rank_delta']          = rank_delta
        rising = False
        falling = False
        if change_pct is not None:
            if change_pct >=  _PEOPLE_TREND_RISE_PCT: rising  = True
            if change_pct <= -_PEOPLE_TREND_DROP_PCT: falling = True
        if rank_delta is not None:
            if rank_delta >=  _PEOPLE_TREND_RANK_DELTA: rising  = True
            if rank_delta <= -_PEOPLE_TREND_RANK_DELTA: falling = True
        if rising and not falling:
            r['trend'] = 'rising'
        elif falling and not rising:
            r['trend'] = 'falling'
        else:
            r['trend'] = 'stable'
    return people_rows
