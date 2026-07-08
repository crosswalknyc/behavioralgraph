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
            entry = by_term.get(key)
            if entry is None:
                by_term[key] = {
                    'term':           term,
                    'score':          score,
                    'related':        list(r.get('related') or [])[:6],
                    'days_trending':  1,
                    'first_seen':     d_iso,
                    'last_seen':      d_iso,
                }
                continue
            if score > entry['score']:
                entry['score'] = score
                entry['term']  = term
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
    return sorted(by_term.values(), key=lambda x: -x['score'])


def _fetch_trending_searches(state: Optional[str], lookback_days: int) -> list[dict]:
    """Google Trends daily search snapshots for the requested geography.

    For US-national requests, reads the wide multi-geo daily pool
    (union of US + 15 large states, ~80 unique terms/day) so the
    Overall card has real depth to scroll through and the category
    cards have enough material to slice up. Falls back to the narrow
    single-geo snapshot when the wide pool hasn't been populated yet.
    For state / DMA queries, uses the state-level RSS snapshot.
    """
    rows: list[dict] = []
    try:
        if state:
            rows = trends_top_issues(state=state, lookback_days=lookback_days) or []
        else:
            wide = _wide_pool_aggregate(lookback_days)
            if wide:
                rows = wide
            else:
                rows = trends_top_issues(state=None, lookback_days=lookback_days) or []
    except Exception as e:
        logger.debug("trends_iq searches failed: %s", e)
        return []
    out = []
    for r in rows[:_SEARCH_POOL_CAP]:
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
        # sports (culturally entertainment)
        'fifa', 'world cup', 'super bowl', 'stanley cup', 'olympics',
        'world series', 'nba finals', 'wnba', 'ncaa', 'march madness',
        'nfl', 'nba', 'mlb', 'nhl', 'mls', 'ufc', 'boxing', 'wwe',
        'espn', 'fox sports', 'sportscenter',
        ' vs ', 'vs.', 'defeat', 'beats', 'scores', 'goal', 'match',
        ' fc', 'united fc', 'city fc', 'championship', 'playoff',
        'draft', 'rookie',
        # celebs / people categories often searched
        'taylor swift', 'beyonce', 'kardashian', 'kanye', 'drake',
        'travis kelce', 'lebron', 'messi', 'ronaldo',
        # unambiguous major-sport team names (single-word teams like
        # "Chiefs" or "Patriots" are omitted because they hit non-sport
        # meanings too often; "Giants", "Rangers", "Tigers" omitted for
        # the same reason - too many non-sport hits).
        'warriors', 'lakers', 'celtics', 'clippers', 'knicks',
        'bulls', 'bucks', '76ers', 'sixers', 'mavericks', 'nuggets',
        'yankees', 'dodgers', 'red sox', 'astros', 'phillies',
        'padres', 'blue jays', 'brewers', 'diamondbacks', 'cardinals',
        'mets', 'braves', 'cubs', 'marlins', 'guardians',
        'nationals', 'orioles', 'rays', 'athletics', 'pirates',
        '49ers', 'seahawks', 'steelers', 'packers', 'ravens',
        'raiders', 'bengals', 'buccaneers',
        'canadiens', 'canucks', 'oilers', 'penguins', 'flyers',
        # tennis / golf / global-sport surnames that trend regularly
        'djokovic', 'nadal', 'federer', 'alcaraz', 'sinner',
        'sabalenka', 'gauff', 'swiatek',
        'tiger woods', 'rory mcilroy', 'scottie scheffler',
        # F1
        'formula 1', 'formula one', ' f1 ', 'verstappen', 'hamilton',
        # tennis / motorsport / soccer meta terms
        'wimbledon', 'us open', 'australian open', 'french open',
        'grand slam', 'masters tournament', 'ryder cup',
        'champions league', 'premier league', 'la liga', 'serie a',
        'bundesliga',
        # league-generic phrases that scan as roster / game news
        'starting lineup', 'signed a contract', 'traded to', 'traded from',
        'head coach', 'assistant coach', 'general manager',
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
    'politics': [
        # figures (current + recent). Full names so we don't false-hit
        # sports figures with common first names (Vance Joseph, Ryan Walz).
        'donald trump', 'joe biden', 'kamala harris', 'obama', 'clinton',
        'bernie sanders', 'aoc', 'ocasio-cortez', 'desantis', 'ramaswamy',
        'nikki haley', 'jd vance', 'tim walz', 'gavin newsom',
        'chuck schumer', 'mitch mcconnell', 'nancy pelosi', 'mike johnson',
        'hakeem jeffries',
        # global leaders
        'putin', 'xi jinping', 'netanyahu', 'zelensky', 'keir starmer',
        'emmanuel macron',
        # process / concepts (avoid single-word 'president' which hits
        # FIFA / league / union / bank presidents. Use compound forms.)
        'president trump', 'president biden', 'president harris',
        'vice president', 'presidential election', 'us president',
        'senator', 'congress', 'senate', 'house of representatives',
        'primary election', 'presidential debate', 'ballot', 'vote count',
        'polling place', 'gop', 'democrat', 'republican', 'campaign trail',
        'inauguration', 'impeach', 'indictment', 'guilty verdict',
        # geopolitics
        'ukraine war', 'israel', 'gaza', 'palestin', 'russia sanction',
        'iran nuclear', 'north korea', 'china tariff', 'nato',
        # institutions / policy
        'white house', 'pentagon', 'state department', 'supreme court',
        'scotus', 'doj', 'fbi', 'immigration policy', 'border wall',
        'abortion ban', 'roe v wade',
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
}
# Short tokens matched by word boundary to prevent false positives like
# "gop" matching "gopro", "btc" matching "batch", "gdp" matching
# "gdpr", etc.
_SHORT_KEYWORD_TOKENS = {
    'gop', 'btc', 'gdp', 'cpi', 'ppi', 'ipo pricing',
    'aoc', 'doj', 'fbi', 'nato', 'scotus',
}


# Priority order for single-category assignment. A term that matches
# multiple category keyword lists is placed in the first matching
# category from this list. Rationale: sports and celebrity terms very
# often bleed into political or financial headlines via their related
# text (e.g. Gianni Infantino's related stories mention "President
# Trump has been a 'great leader'"; the Mets trade news mentions
# "Bigger Sell-Off Coming?"). Entertainment gets highest priority so
# those bleeds get correctly classified as sports/entertainment.
_CATEGORY_PRIORITY = ('entertainment', 'retail', 'politics', 'finance')


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
        'entertainment': [],
        'retail':        [],
        'politics':      [],
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

    Prefers the wide multi-geo daily pool when present (much larger term
    set -> real day-over-day overlap); falls back to the narrow single-geo
    snapshot for backward compatibility.

    Terms are deduped case-insensitively. Score = MAX seen in the window.
    Rank = position in the score-desc sorted output. Returns the merged
    rows plus the earliest and latest snapshot dates that contributed.
    """
    by_term: dict[str, dict] = {}
    days_used: list[str] = []
    use_wide = (geo == 'US')  # wide pool is US-national only
    for off in day_offsets:
        d_iso = (today - timedelta(days=off)).date().isoformat()
        rows: Optional[list[dict]] = None
        if use_wide:
            rows = _wide_pool_get(d_iso)
        if rows is None:
            rows = _trends_snap_get(geo, d_iso)
        if not rows:
            continue
        days_used.append(d_iso)
        for r in rows:
            term = (r.get('term') or '').strip()
            if not term:
                continue
            key = term.lower()
            score = int(r.get('score') or 0)
            existing = by_term.get(key)
            if existing is None or score > int(existing.get('score') or 0):
                by_term[key] = {
                    'term':    term,
                    'score':   score,
                    'related': list(r.get('related') or [])[:5],
                }
    if not by_term:
        return [], None, None
    merged = sorted(by_term.values(), key=lambda r: -int(r.get('score') or 0))
    return merged, min(days_used), max(days_used)


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
def _fetch_streaming_trending(state: Optional[str], lookback_days: int,
                                keywords: Optional[list[str]] = None) -> dict:
    """Fan out to every streaming platform's daily snapshot.

    Netflix is populated by the public TSV scraper (no auth). The rest
    are Playwright + donated-cookie scrapers - they'll return
    `available=False` until Jenna donates cookies for that domain.

    Geographic filtering is minimal here: rankings are inherently
    national (Netflix per-country data is US; the others are all
    US-Netflix / US-Disney+ etc.). We still honor `keywords` for the
    reordering pass so state / DMA selections nudge locally-flavored
    titles up (e.g. "Yellowstone" for MT, "The Wire" for MD).
    """
    result = {slug: {'label': label, 'items': [], 'available': avail}
              for slug, label, avail in STREAMING_PLATFORMS}

    for slug, label, _static_avail in STREAMING_PLATFORMS:
        snap = _read_snapshot(slug)
        if not snap:
            continue
        items = _snapshot_items_for_geo(snap, state, keywords=keywords)
        snap_available = snap.get('available')
        if snap_available is None:
            snap_available = bool(items)
        payload = {
            'label':      label,
            'items':      items[:20],
            'available':  bool(snap_available),
            'fetched_at': snap.get('fetched_at'),
        }
        # Netflix ships extra metadata (week, per-category breakouts)
        # the frontend uses for the "week of..." subtitle.
        if slug == 'netflix':
            for extra in ('week_us', 'week_global',
                           'us_films', 'us_tv',
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

    # Split the trending search pool into the 5-card layout the UI renders:
    # Overall (all rows, scrollable) + Entertainment / Retail / Politics /
    # Finance (top 20 each). Category buckets are computed from the same
    # underlying list so counts add up predictably.
    searches_by_category = _bucket_searches_by_category(trending_searches, per_bucket=30)

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
            'entertainment': len(searches_by_category.get('entertainment') or []),
            'retail':        len(searches_by_category.get('retail')        or []),
            'politics':      len(searches_by_category.get('politics')      or []),
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
    payload['from_cache'] = False
    return payload
