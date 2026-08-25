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
    list_available_dates(max_days: int = 120) -> list[str]

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
        wikipedia_descriptions,
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

    def wikipedia_descriptions(titles, timeout_s=6, max_workers=12):
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
CACHE_TTL_S        = int(os.environ.get('TRENDS_IQ_CACHE_TTL', '86400'))      # 24h - matches the daily-cron cadence of the underlying trends_iq_snapshots/latest/ writes. There is no reason to invalidate the aggregated dashboard payload before the next scraper cron produces new upstream snapshots.
DEFAULT_LOOKBACK_DAYS = int(os.environ.get('TRENDS_IQ_LOOKBACK_DAYS', '1'))
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
    ('MS NOW',          'https://www.msnbc.com/feed/',                                  'msnbc.com'),
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
# Instagram and X removed from Social 2026-07-28 per Jenna. Removing
# them here drops them from every surface that reads through this list:
#   * The Social tab (no more Instagram / X sub-tabs)
#   * Movers cross-platform overlap
#   * Trending People cross-platform overlap
#   * Cross-platform badges on Music / Trending People rows
# Their scrapers still run daily and their snapshots still land in
# `trends_iq_snapshots/latest/{instagram,x}.json` for historical arcs
# via the History button and for future re-enablement. To restore
# either surface, re-add the tuple in the desired display order.
SOCIAL_PLATFORMS = [
    ('reddit',    'Reddit',    True),
    ('youtube',   'YouTube',   False),
    ('tiktok',    'TikTok',    False),
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
    # slug stays 'max' for backwards compat with existing S3 snapshot
    # keys (trends_iq_snapshots/latest/max.json etc.). Display label
    # updated to 'HBO Max' 2026-07-30 per Jenna: WBD kept HBO in the
    # official brand and the shorter "Max" alone tested confusingly
    # in the streaming sub-tab strip.
    ('max',        'HBO Max',      False),
    ('primevideo', 'Prime Video',  False),
    ('espnplus',   'ESPN+',        False),
    # 2026-08-20: BritBox (BBC + ITV joint venture, US premium British
    # TV catalog) and MGM+ (Amazon-owned premium, formerly Epix). Both
    # run residentially from Jenna's laptop via local_residential_run
    # because their WAFs fingerprint Hetzner's datacenter IP. BritBox
    # is a plain-HTTP scrape of /us/home (title anchors in-DOM); MGM+
    # needs Playwright to hydrate the React SPA on /movies + /series
    # + /browse.
    ('britbox',    'BritBox',      False),
    ('mgmplus',    'MGM+',         False),
    # 2026-08-20: Starz (Lionsgate, ~12M US subs, home of Power +
    # Outlander + Spartacus + Starz Originals). Plain-HTTP scrape of
    # /us/en/movies + /us/en/series - both pages ship the full browse
    # catalog inline as __NEXT_DATA__.props.pageProps.<movie|series>
    # Blocks[N].data.slides[M]. Runs residentially because Starz's
    # Akamai config fingerprints Hetzner's datacenter IP.
    ('starz',      'Starz',        False),
]

# 2026-08-20: Gaming tab. First platform is Xbox Game Pass Ultimate;
# PlayStation Plus / Nintendo Switch Online / Steam trending can slot
# in here later without touching the frontend or payload shape - they
# just need a scraper that writes trends_iq_snapshots/latest/{slug}.json
# with the same {national: [{title, image, publisher, genre, url}, ...]}
# structure.
GAMING_PLATFORMS = [
    ('xbox_gamepass', 'Xbox Game Pass Ultimate', False),
]

# How old a snapshot can be before we treat the source as unavailable
# again. Two days = one missed nightly + one buffer. Bump if a scraper
# is flaky.
_SNAPSHOT_MAX_AGE_S = int(os.environ.get('TRENDS_IQ_SNAPSHOT_MAX_AGE_S',
                                            str(2 * 24 * 3600)))
_SNAPSHOT_PREFIX    = 'trends_iq_snapshots/latest/'
# Historic snapshots (dated). Every scraper writes to BOTH
# `latest/{source}.json` and `{YYYY-MM-DD}/{source}.json` via
# scripts/trends_scrapers/_base.py::write_snapshot, so the daily archive
# accumulates automatically. `_read_snapshot(source, asof=DATE)` reads
# from the dated prefix; asof=None keeps the current "latest" behavior.
_SNAPSHOT_DATED_PREFIX = 'trends_iq_snapshots/{date}/'


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


def _today_iso() -> str:
    """Today's UTC date as YYYY-MM-DD. Cache-day boundary."""
    return datetime.now(timezone.utc).date().isoformat()


def _cache_key(filters: dict) -> str:
    """Build the S3 cache key for the given filter tuple.

    The `asof` field participates in the hash so:
      - "Live / today" queries hit ONE cache entry that rotates daily
        (the daily scraper cron warms tomorrow's entry before users hit
        it; today's entry gets read all day).
      - Historic queries (asof=YYYY-MM-DD) each get their own permanent
        cache entry. Once populated, that entry never rotates - it IS
        the historic view of that day.
    """
    asof = filters.get('asof') or _today_iso()
    payload = json.dumps({
        'asof':          asof,
        'geo_type':      filters.get('geo_type') or 'National',
        'geo_value':     filters.get('geo_value') or '',
        'lookback_days': int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS),
    }, sort_keys=True)
    h = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]
    return f"{S3_CACHE_PREFIX}{h}.json"


def _is_historic(filters: dict) -> bool:
    """True when filters['asof'] refers to a past UTC day.

    Live/today queries have TTL semantics; historic queries do NOT -
    the payload is a permanent snapshot of that day.
    """
    asof = filters.get('asof')
    if not asof:
        return False
    return asof < _today_iso()


def _cache_get(filters: dict) -> Optional[dict]:
    s3 = _s3_client()
    if s3 is None:
        return None
    historic = _is_historic(filters)
    try:
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=_cache_key(filters))
        raw = resp['Body'].read().decode('utf-8')
        data = json.loads(raw)
        # Historic queries: any cached entry is authoritative. There's
        # nothing "stale" about a snapshot of a past day - that IS the
        # data we want to return.
        if historic:
            return data
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
def _read_snapshot(source: str, asof: Optional[str] = None) -> Optional[dict]:
    """Read a scraper snapshot from S3.

    asof:
      None  -> read `latest/{source}.json` and enforce the max-age
               freshness gate (returns None if the snapshot is stale).
      DATE  -> read `{YYYY-MM-DD}/{source}.json` and SKIP the freshness
               gate. Historic snapshots are meant to be old; the gate
               would reject every historic read otherwise.

    Every scraper writes to both the `latest/` and the dated prefix via
    scripts/trends_scrapers/_base.py::write_snapshot, so any date the
    scraper was healthy on has a dated copy on S3.
    """
    s3 = _s3_client()
    if s3 is None:
        return None
    if asof:
        prefix = _SNAPSHOT_DATED_PREFIX.format(date=asof)
    else:
        prefix = _SNAPSHOT_PREFIX
    key = f'{prefix}{source}.json'
    try:
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
    except Exception:
        return None
    try:
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.debug("trends_iq _read_snapshot %s parse failed: %s", source, e)
        return None
    # Freshness gate only applies to the "live" latest/ read. Dated
    # historic reads deliberately bypass it - the whole point of asof
    # queries is to see stale data.
    if asof is None:
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
        # left-lean media (2026-07-31: brand rebranded MSNBC -> MS NOW;
        # keep both aliases so we match legacy hostmap entries + fresh
        # feed byline strings during the transition window)
        'ms now', 'msnbc', 'mother jones', 'the nation magazine', 'jacobin',
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
        'nasdaq', 'dow jones', 's&p 500', 'sp500', 's & p 500',
        'russell 2000', 'russell 1000', 'ftse', 'nikkei',
        'stock market', 'stock price', 'stock jumps', 'stock drops',
        'stock plunges', 'stock soars', 'stock surges', 'stock crashes',
        'stock hits record', 'stock hit record', 'record high',
        'shares climb', 'shares fall', 'shares surge', 'shares plunge',
        'shares tumble', 'shares slide', 'shares rally',
        'market rally', 'market sell-off', 'market sell off',
        'market crash', 'market rout', 'market swoon',
        'stock sell-off', 'stock sell off', 'ipo pricing',
        'quarterly earnings', 'dividend', 'dividend hike',
        'dividend cut', 'buyback', 'stock buyback', 'share buyback',
        # macro
        'inflation', 'inflation report', 'inflation cools',
        'inflation hot', 'core inflation', 'interest rate', 'rate cut',
        'rate hike', 'rate hold', 'rate pause', 'recession',
        'recession fears', 'recession odds', 'soft landing',
        'gdp', 'gdp growth', 'gdp report', 'unemployment rate',
        'jobless claims', 'cpi', 'ppi', 'pce', 'pce inflation',
        'tariff', 'tariffs', 'trade deficit', 'trade war',
        'jobs report', 'nonfarm payroll', 'jobs data',
        'consumer sentiment', 'consumer confidence',
        'yield curve', 'yield inversion', '10-year yield',
        'treasury yield', 'bond yield',
        # institutions (compound to avoid short-token false hits)
        'federal reserve', 'fed rate', 'fed cut', 'fed hike',
        'fed meeting', 'fomc', 'fomc minutes', 'jerome powell',
        'janet yellen', 'scott bessent', 'us treasury',
        'treasury secretary', 'wall street', 'goldman sachs',
        'jpmorgan', 'jp morgan', 'morgan stanley', 'citigroup',
        'bank of america', 'blackrock', 'vanguard', 'fidelity',
        'berkshire', 'berkshire hathaway', 'warren buffett',
        'charlie munger', 'jamie dimon', 'david solomon',
        # crypto
        'bitcoin', 'btc', 'bitcoin price', 'ethereum', 'eth price',
        'crypto crash', 'crypto rally', 'crypto', 'coinbase',
        'stablecoin', 'usdc', 'usdt', 'tether', 'binance', 'solana',
        'sol price', 'bitcoin etf', 'ethereum etf', 'spot etf',
        'crypto etf',
        # bellwether tickers - use "stock" suffix for common-word brands
        'nvidia', 'nvda stock', 'tesla stock', 'tsla stock',
        'apple stock', 'aapl stock', 'microsoft stock', 'msft stock',
        'meta stock', 'meta earnings', 'amazon stock', 'amzn stock',
        'palantir', 'palantir stock', 'plt stock', 'amd stock',
        'super micro', 'smci', 'broadcom', 'arm stock',
        'oracle stock',
        # gold / oil / commodities
        'gold price', 'gold rally', 'gold record', 'silver price',
        'oil price', 'crude oil', 'brent crude', 'wti crude',
        'gas price', 'natural gas', 'copper price', 'commodity prices',
        # bonds / rates - 'mortgage rate/rates/30-year' are in `home`
        # since they're primarily a housing-market signal, not a
        # markets signal. Keep the pure-yield tickers here.
        'refi rate', 'auto loan rate', 'high yield savings',
    ],
    # Philanthropy / nonprofit sector (added 2026-07-10). Sits in the
    # priority list BEFORE `politics` so philanthropy-adjacent policy
    # news ("proposed grant rules", "foundation funding") lands here
    # instead of leaking into politics. Terms are compound where
    # possible - single-word "grant" catches Grant Cardone, Grant Hill,
    # Ulysses S. Grant so we don't include it bare.
    'philanthropy': [
        # sector-defining vocabulary
        'philanthropy', 'philanthropist', 'philanthropic',
        'nonprofit', 'non-profit', 'nonprofits', 'ngo', '501c3', '501(c)(3)',
        'charity', 'charities', 'charitable',
        # "foundation" alone would false-positive on makeup and sports
        # club names; use context compounds instead. Together these
        # catch every real philanthropy story: "Gates Foundation",
        # "Ford Foundation", "family foundation", "foundation
        # announced", "'s foundation", etc.
        'foundation grant', 'foundation funding', 'foundation gift',
        'foundation announced', 'foundation launches', 'foundation says',
        'foundation moves', 'foundation to donate', 'foundation to give',
        'foundation pledges', 'foundation commits',
        'family foundation', 'family foundations',
        'charitable foundation', 'private foundation',
        'community foundation', 'nonprofit foundation',
        'philanthropic foundation', "'s foundation",
        'grantmaking', 'grant funding', 'grant rules',
        'grantmaker', 'grantee', 'grant program',
        'endowment', 'donor advised fund', 'daf ',
        # fundraising surface
        'fundraiser', 'fundraising', 'fundraise', 'gofundme',
        'kickstarter charity', 'indiegogo relief',
        'giving pledge', 'giving tuesday', 'year-end giving',
        'year end giving', 'planned giving', 'donation drive',
        'donate', 'donation', 'donations', 'donor',
        # marquee nonprofits / NGOs
        'red cross', 'american red cross', 'salvation army',
        'unicef', 'united way', 'feeding america', 'meals on wheels',
        'doctors without borders', 'msf ', 'oxfam', 'care international',
        'save the children', 'world vision', 'habitat for humanity',
        'goodwill', 'boys and girls club', 'boys & girls club',
        'make a wish', 'make-a-wish', 'st jude', "st. jude",
        "st. jude children's", 'toys for tots', 'mackenzie scott',
        # foundations / major philanthropists
        'gates foundation', 'bill and melinda gates',
        'ford foundation', 'macarthur foundation', 'rockefeller foundation',
        'carnegie corporation', 'buffett giving',
        'chan zuckerberg', 'open society foundations',
        'bloomberg philanthropies', 'walton foundation',
        # relief / humanitarian
        'disaster relief', 'humanitarian aid', 'humanitarian crisis',
        'famine relief', 'refugee aid', 'hurricane relief',
        'wildfire relief', 'earthquake relief', 'flood relief',
        'ukraine relief', 'gaza aid', 'sudan aid',
        # sector coverage
        'chronicle of philanthropy', 'nonprofit quarterly',
        'giving usa report', 'inside philanthropy',
        # celebrity-driven giving (common trending pattern)
        'megadonation', 'mega-donation', 'anonymous donor',
        'billionaire donation', 'celebrity donation',
        'celebrity giving', 'celebrity philanthropy',
        'annual donation', 'annual gift', 'gift of $', 'donates $',
        'donated $', 'pledges $', 'pledged $',
        'gift to', 'donation to', 'donates to',
        # additional foundations (Ariana Grande's Protect & Defend,
        # Buffett family, Bezos day one, etc.)
        'ariana grande foundation', "grande's foundation",
        'buffett family foundation', 'day one fund', 'earth fund',
        'bezos earth', 'melinda french', 'melinda gates',
        'pivotal ventures', "warren buffett's",
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
        # trial / court vocabulary
        'murder trial', 'homicide trial', 'murder charges', 'murder charge',
        'jury verdict', 'jury deliberations', 'guilty verdict',
        'not guilty verdict', 'sentencing hearing', 'sentenced to',
        'sentenced to life', 'life sentence', 'death penalty',
        'death row', 'plea deal', 'plea agreement', 'plea bargain',
        'guilty plea', 'no contest', 'nolo contendere',
        'grand jury indictment', 'grand jury indicts', 'federal indictment',
        'racketeering', 'rico charges', 'search warrant', 'fbi raid',
        'atf raid', 'dea raid', 'arraigned', 'arraignment', 'extradited',
        'extradition', 'mistrial', 'hung jury', 'reversed conviction',
        'exonerated', 'wrongful conviction', 'cold case',
        # violence types + verbs (compound where a bare noun would
        # collide with sports / music - 'shot' alone hits "took the shot",
        # so use 'shot dead' / 'shot and killed' / 'shot to death')
        'shot dead', 'shot and killed', 'shot to death', 'fatal shooting',
        'gunned down', 'stabbed to death', 'fatal stabbing', 'stabbing death',
        'beaten to death', 'strangled', 'strangulation death',
        'found dead', 'body found', 'body recovered', 'body identified',
        'human remains', 'remains found', 'remains identified',
        'dismembered', 'decapitated',
        'homicide investigation', 'homicide case', 'double homicide',
        'triple homicide', 'quadruple homicide', 'murder-suicide',
        'murder suicide', 'suspected homicide', 'ruled a homicide',
        'ruled homicide', 'brutal murder',
        # trafficking / abuse / exploitation
        'sex trafficking', 'human trafficking', 'child trafficking',
        'labor trafficking', 'sexual assault', 'sexual abuse',
        'sexual battery', 'sex offender', 'child predator',
        'child sex abuse', 'child sexual abuse', 'pedophile',
        'sextortion', 'sextortion scheme', 'grooming charges',
        'kidnapping', 'kidnapped', 'child abduction', 'abduction',
        # missing / disappearance
        'missing person', 'missing woman', 'missing man', 'missing girl',
        'missing boy', 'missing hiker', 'missing camper', 'missing child',
        'missing since', 'amber alert', 'silver alert', 'endangered missing',
        'gone missing', 'vanished without', 'last seen',
        'search for missing', 'body of missing',
        # manhunt / wanted / fugitive
        'manhunt', 'nationwide manhunt', 'multi-state manhunt',
        'wanted man', 'wanted woman', 'wanted for', 'fugitive',
        'on the run', 'at large', 'suspect at large', 'america\'s most wanted',
        # shootings / attacks
        'mass shooting', 'active shooter', 'school shooting',
        'grocery store shooting', 'mall shooting', 'movie theater shooting',
        'church shooting', 'synagogue shooting', 'mosque shooting',
        'workplace shooting', 'shooting suspect', 'shooting victim',
        'shooting rampage', 'gunman opens fire', 'gunman shot',
        # police / officer involved
        'officer killed', 'officer shot', 'officer down',
        'police shooting', 'police-involved shooting', 'trooper killed',
        'deputy killed', 'deputy shot', 'sheriff shot', 'officer ambush',
        'police standoff', 'hostage situation', 'barricaded suspect',
        # property / theft
        'armed robbery', 'bank robbery', 'home invasion',
        'carjacking', 'attempted carjacking', 'grand theft auto arrest',
        'burglary ring', 'arson', 'arson attack', 'arson suspect',
        # hate / bias / terror
        'hate crime', 'bias crime', 'antisemitic attack',
        'antisemitic incident', 'terrorist attack', 'domestic terror',
        'bomb threat', 'pipe bomb', 'suicide bomber', 'mass casualty',
        # cartels / gangs / organized
        'cartel violence', 'cartel leader', 'sinaloa cartel',
        'jalisco cartel', 'cjng', 'gang violence', 'gang shooting',
        'organized crime', 'mafia bust', 'drug bust', 'meth lab',
        'fentanyl bust', 'cocaine bust', 'trafficking ring',
        # domestic - use compound forms so bare 'restraining order' (which
        # often refers to a corporate injunction / court order in a business
        # deal) doesn't fold in as crime.
        'domestic violence', 'domestic incident',
        'domestic restraining order', 'domestic violence restraining',
        'protective order violation', 'stalking charges',
        'stalker arrested', 'intimate partner violence',
        'ex-boyfriend charged', 'ex-husband charged',
        # fraud / financial crime (leans a bit finance-adjacent but
        # story-driven fraud is a crime beat, not a markets beat)
        'wire fraud', 'securities fraud', 'ponzi scheme',
        'insurance fraud', 'medicare fraud', 'medicaid fraud',
        'tax fraud', 'crypto fraud', 'sam bankman-fried', 'sbf trial',
        'elizabeth holmes', 'theranos',
        # true-crime blockbusters (2024-2026)
        'karen read', 'diddy trial', 'sean combs trial', 'p diddy trial',
        'ghislaine maxwell', 'jeffrey epstein', 'epstein list',
        'epstein files', 'epstein documents', 'idaho murders',
        'bryan kohberger', 'chad daybell', 'lori vallow',
        'susan smith', 'ryan wesley routh', 'gilgo beach killer',
        'delphi murders', 'richard allen', 'gabby petito',
        'brian laundrie', 'menendez brothers', 'harvey weinstein',
        'kelsey berreth', 'patrick frazee', 'travis rudolph',
        'luigi mangione', 'mangione', 'daniel penny',
        'alec baldwin rust', 'rust shooting', 'halyna hutchins',
        # public-figure attempts / assassinations (huge traffic drivers)
        'assassination attempt', 'assassinated', 'suspect arrested',
        'suspect in custody', 'person of interest', 'suspect identified',
        # generic sector nouns (kept late so more-specific compounds hit
        # first)
        'homicide', 'murder case', 'murder mystery', 'cold-blooded',
        'true crime',
    ],
    # Health & wellness. GLP-1s / Ozempic drove massive 2024-2026
    # trend traffic; mental health, workouts, and wellness fads
    # complete the cluster.
    'health': [
        # weight-loss / GLP-1 wave
        'ozempic', 'wegovy', 'mounjaro', 'zepbound', 'saxenda',
        'glp-1', 'glp 1', 'compounded semaglutide', 'compounded tirzepatide',
        'weight loss drug', 'weight loss shot', 'weight loss injection',
        'weight loss pill', 'semaglutide', 'tirzepatide',
        # workouts / fitness fads
        'peloton', 'orange theory', 'orangetheory', 'crossfit',
        'hyrox', 'run club', 'zone 2 training', 'zone two training',
        'cold plunge', 'ice bath', 'sauna', 'red light therapy',
        # mental health
        'mental health', 'mental health day', 'burnout symptoms',
        'anxiety symptoms', 'depression symptoms', 'therapy trend',
        'ssri', 'antidepressant', 'ketamine therapy',
        'psilocybin therapy', 'suicide prevention', 'suicide hotline',
        # diet / nutrition fads
        'protein powder', 'creatine benefits', 'electrolyte drink',
        'liquid iv', 'lmnt', 'element',
        'seed oils', 'raw milk', 'carnivore diet', 'keto diet',
        'intermittent fasting', 'mediterranean diet',
        # supplement / wellness brand universe
        'ag1', 'athletic greens', 'huberman', 'andrew huberman',
        'attia', 'peter attia', 'bryan johnson', "don't die",
        # outbreaks + infectious disease (2024-2026 heavy beat)
        'rsv vaccine', 'measles outbreak', 'measles cases',
        'measles case', 'bird flu', 'h5n1', 'h5n1 outbreak',
        'avian flu', 'covid variant', 'covid cases', 'long covid',
        'norovirus outbreak', 'norovirus', 'salmonella outbreak',
        'salmonella recall', 'e coli outbreak', 'e. coli outbreak',
        'listeria outbreak', 'listeria recall', 'mpox', 'monkeypox',
        'polio case', 'polio outbreak', 'zika virus',
        'dengue outbreak', 'malaria', 'ebola outbreak',
        # cancer / chronic disease (huge search vertical)
        'cancer diagnosis', 'cancer treatment', 'cancer survivor',
        'stage 4 cancer', 'stage 4', 'breast cancer',
        'colon cancer', 'colorectal cancer', 'lung cancer',
        'prostate cancer', 'pancreatic cancer', 'brain tumor',
        'chemotherapy', 'chemo treatment', 'radiation therapy',
        'diabetes', 'type 1 diabetes', 'type 2 diabetes',
        'alzheimer', 'dementia', 'parkinson',
        # medical devices / procedures
        'brain implant', 'neuralink patient', 'cochlear implant',
        'organ transplant', 'kidney transplant', 'heart transplant',
        # institutions
        'cdc guidelines', 'cdc report', 'cdc advisory',
        'fda approval', 'fda advisory', 'fda recall', 'fda ban',
        'rfk hhs', 'rfk secretary', 'hhs secretary', 'nih director',
        'surgeon general', 'who report', 'world health organization',
        # public-health drugs / recalls
        'drug recall', 'medication recall', 'birth control recall',
        'tylenol recall', 'ibuprofen recall',
        # sexual / reproductive health
        'ivf ruling', 'ivf treatment', 'in vitro fertilization',
        'abortion pill', 'mifepristone', 'plan b',
        # public-figure health news (huge search driver)
        'health scare', 'hospitalized', 'in the hospital',
        'medical emergency', 'health update', 'medical leave',
        # general medical vocab (kept late so specific compounds hit
        # first)
        'symptoms', 'medication', 'prescription', 'antibiotic',
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
        # Brand-only tokens. These are the big ones - "subaru recall",
        # "bmw starter relay", "lucid" all come through as bare-brand
        # searches, so requiring "subaru ascent" or "lucid air" to match
        # was too tight. Match any bare brand and let priority handle
        # entertainment overlaps ("kim kardashian ford bronco").
        'tesla', 'ford', 'chevy', 'chevrolet', 'toyota', 'honda',
        'nissan', 'hyundai', 'kia', 'jeep', 'ram truck', 'dodge',
        'chrysler', 'bmw', 'mercedes', 'mercedes-benz', 'mercedes benz',
        'audi', 'porsche', 'volkswagen', 'vw', 'volvo', 'subaru',
        'mazda', 'lucid', 'rivian', 'polestar', 'byd', 'nio ev',
        'xpeng', 'genesis auto', 'cadillac', 'buick', 'lincoln car',
        'infiniti', 'acura', 'lexus', 'mini cooper', 'maserati',
        'lamborghini', 'ferrari', 'aston martin', 'mclaren car',
        'bentley', 'rolls royce', 'rolls-royce', 'jaguar', 'land rover',
        'gm', 'general motors', 'stellantis', 'waymo', 'fsd',
        'nhtsa', 'iihs',
        # legacy OEMs / trucks / SUVs
        'ford f-150', 'ford f150', 'ford bronco', 'ford maverick',
        'ford mustang', 'ford ranger', 'ford explorer', 'ford edge',
        'chevrolet silverado', 'chevy silverado', 'chevy tahoe',
        'chevy suburban', 'chevy equinox', 'chevy trax',
        'gmc yukon', 'gmc hummer', 'gmc sierra',
        'toyota camry', 'toyota tacoma', 'toyota tundra',
        'toyota 4runner', 'toyota land cruiser', 'toyota supra',
        'toyota rav4', 'toyota corolla', 'toyota highlander',
        'toyota sienna',
        'honda civic', 'honda accord', 'honda crv', 'honda cr-v',
        'honda pilot', 'honda odyssey', 'honda ridgeline',
        'nissan altima', 'nissan rogue', 'nissan pathfinder',
        'nissan sentra', 'nissan frontier',
        'jeep wrangler', 'jeep grand cherokee', 'jeep gladiator',
        'jeep compass', 'jeep renegade',
        'ram 1500', 'ram trx', 'ram 2500',
        'dodge charger', 'dodge challenger', 'dodge durango',
        'subaru ascent', 'subaru forester', 'subaru crosstrek',
        'subaru outback', 'subaru wrx', 'subaru impreza',
        'subaru legacy', 'subaru brz',
        # luxury / performance / models
        'porsche 911', 'porsche taycan', 'porsche cayenne',
        'porsche macan', 'porsche 718',
        'bmw m3', 'bmw m5', 'bmw x5', 'bmw x7', 'bmw i4', 'bmw ix',
        'audi rs6', 'audi q5', 'audi q7', 'audi e-tron', 'audi a4',
        'mercedes eqs', 'mercedes gle', 'mercedes s-class',
        'mercedes c-class', 'mercedes g-wagon', 'g wagon',
        'lucid air', 'lucid gravity',
        'rivian r1s', 'rivian r1t',
        # Tesla models
        'model 3', 'model s', 'model x', 'model y',
        'tesla model 3', 'tesla model s', 'tesla model x',
        'tesla model y', 'cybertruck', 'tesla cybertruck',
        'tesla roadster', 'tesla semi',
        # EVs (non-Tesla)
        'ev tax credit', 'ev rebate', 'ev charger', 'ev range',
        'ev battery fire', 'ev market', 'ev sales', 'ev sale',
        'electric vehicle', 'electric car', 'electric truck',
        'plug-in hybrid', 'plug in hybrid',
        'byd auto', 'byd ev', 'byd atto', 'byd seal',
        'polestar 3', 'polestar 4', 'polestar 2',
        'kia ev6', 'kia ev9', 'kia ev5', 'kia sorento', 'kia telluride',
        'kia sportage', 'kia seltos', 'kia forte',
        'hyundai ioniq', 'hyundai kona', 'hyundai tucson',
        'hyundai palisade', 'hyundai santa fe', 'hyundai elantra',
        'ford lightning', 'ford f-150 lightning',
        'chevy bolt', 'chevy blazer ev', 'chevy equinox ev',
        'chevy silverado ev', 'chevy volt',
        # recalls / news / regulator
        'car recall', 'auto recall', 'vehicle recall', 'airbag recall',
        'nhtsa recall', 'ford recall', 'gm recall', 'toyota recall',
        'honda recall', 'tesla recall', 'chevy recall', 'kia recall',
        'hyundai recall', 'nissan recall', 'bmw recall',
        'subaru recall', 'mercedes recall', 'audi recall',
        'volkswagen recall', 'vw recall', 'ram recall',
        'jeep recall', 'stellantis recall', 'ev recall',
        'takata airbag', 'nhtsa investigation', 'iihs',
        'stellantis', 'automaker', 'auto sales', 'car sales',
        'used car', 'used cars', 'car price', 'car prices',
        'auto insurance rate',
        # self-driving
        'autopilot', 'full self-driving', 'full self driving', 'fsd',
        'super cruise', 'blue cruise', 'autonomous vehicle',
        'robotaxi', 'waymo', 'cruise robotaxi',
        # auto shows / industry
        'detroit auto show', 'geneva motor show',
        'los angeles auto show', 'la auto show', 'ces auto',
        # racing (separate from sports team names)
        'formula 1 race', 'formula 1', 'formula one', ' f1 race',
        'nascar race', 'nascar cup', 'monaco grand prix',
        'daytona 500', 'indianapolis 500', 'indy 500',
        'le mans', 'imsa', 'sebring', 'daytona 24',
        'lewis hamilton', 'max verstappen', 'charles leclerc',
        'lando norris',
        # gas / EV crossover
        'gas prices', 'gas prices today', 'aaa gas prices',
        'gas price', 'diesel price', 'oil price',
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
        # housing market - broadened. Anything with "housing" or
        # "real estate" or "mortgage" or "rent" in it lands here first.
        'housing', 'housing market', 'housing crash', 'housing bubble',
        'housing crisis', 'housing shortage', 'housing affordability',
        'affordable housing', 'affordability crisis',
        'real estate', 'realestate', 'realtor', 'realtor.com',
        'realtor com', 'realtors',
        'mortgage', 'mortgage rate', 'mortgage rates',
        '30 year mortgage', '30-year mortgage', '15 year mortgage',
        '15-year mortgage', 'fha loan', 'va loan', 'jumbo loan',
        'home loan', 'refi', 'refinance', 'refinancing',
        'home equity', 'heloc', 'home equity line',
        'zillow', 'redfin', 'compass real estate', 'opendoor',
        'homes for sale', 'home for sale', 'home listing',
        'open house', 'home buyer', 'first time home buyer',
        'first-time home buyer', 'starter home', 'starter homes',
        'home price', 'home prices', 'median home price',
        'home sale', 'home sales', 'existing home sales',
        'new home sales', 'pending home sales',
        'rent prices', 'rent hike', 'rent price', 'rising rent',
        'rent control', 'apartment rent', 'section 8',
        'landlord', 'tenant', 'eviction', 'eviction moratorium',
        'apartment', 'apartments', 'condo', 'condominium',
        'townhouse', 'townhome', 'foreclosure', 'foreclosures',
        # HGTV / home reno personalities
        'joanna gaines', 'chip gaines', 'fixer upper', 'magnolia',
        'magnolia network', 'magnolia market',
        'property brothers', 'jonathan scott', 'drew scott',
        'christina hall', 'christina haack', 'flip or flop',
        'love it or list it', 'martha stewart',
        'ty pennington', 'extreme home makeover',
        'ryan serhant', 'million dollar listing',
        'selling sunset', 'selling the oc',
        'nate berkus', 'jeremiah brent',
        'shea mcgee', 'studio mcgee',
        # home reno / DIY
        'home renovation', 'home reno', 'renovation', 'remodel',
        'kitchen remodel', 'bathroom remodel', 'basement remodel',
        'diy home', 'diy project', 'ikea hack', 'ikea',
        'home depot', 'lowes', "lowe's", 'ace hardware',
        'menards', 'harbor freight',
        'wayfair', 'west elm', 'crate and barrel', 'pottery barn',
        'cb2', 'anthropologie home', 'restoration hardware', 'rh home',
        'article furniture', 'floyd home', 'burrow furniture',
        'homegoods', 'home goods', 'threshold target',
        'hearth and hand', 'studio mcgee target',
        # aesthetics
        'cottagecore', 'modern farmhouse', 'coastal grandma',
        'coastal grandmother', 'dark academia decor',
        'quiet luxury home', 'grandmillennial style',
        'japandi', 'wabi-sabi decor', 'organic modern',
        # notable real estate
        'celebrity mansion', 'celebrity home', 'zillow gone wild',
        'billion dollar home', 'most expensive house',
        'housing bill', 'first time buyer credit',
        # landscaping / outdoor
        'landscaping', 'garden design', 'backyard reno',
        'outdoor living', 'pool build',
    ],
    # Business & startups. Layoffs, IPOs, funding rounds, exec moves,
    # unicorn news. Distinct from finance (which is stocks + macro +
    # crypto prices) - this is corporate / operational business news.
    'business': [
        # M&A / deal news (2026 is a big M&A cycle - Paramount / WBD /
        # etc. - so make sure these land here, not in finance or crime)
        'merger', 'acquisition', 'acquires', 'acquiring',
        'buyout', 'takeover', 'take-private', 'take private',
        'go private', 'going private', 'spin-off', 'spinoff',
        'divestiture', 'carve-out', 'carve out',
        'paramount merger', 'warner bros merger', 'warner bros discovery',
        'wbd merger', 'skydance paramount', 'skydance merger',
        'disney fox', 'microsoft activision', 'us steel nippon',
        'kroger albertsons', 'capital one discover', 'chevron hess',
        'temporary restraining order', 'preliminary injunction',
        'deal blocked', 'deal cleared', 'deal approved',
        'antitrust review', 'antitrust ruling', 'antitrust case',
        'antitrust lawsuit', 'ftc lawsuit', 'ftc investigation',
        'doj antitrust', 'doj lawsuit', 'sec charges',
        'sec settlement', 'consent decree',
        # unicorns / startups
        'startup funding', 'series a funding', 'series b funding',
        'series c funding', 'unicorn startup', 'yc demo day',
        'y combinator', 'stripe valuation', 'databricks valuation',
        'canva valuation', 'perplexity funding',
        # IPO wave
        'ipo filing', 'ipo debut', 'ipo priced', 'ipo dropped',
        'ipo pop', 'direct listing', 'spac merger', 'reverse merger',
        'files for ipo', 'plans to ipo', 'files s-1',
        # famous private company news
        'openai valuation', 'openai funding', 'anthropic funding',
        'anthropic valuation', 'stripe ipo', 'databricks ipo',
        'databricks earnings',
        # earnings / quarterly moves (business-side beats)
        'quarterly loss', 'quarterly profit', 'earnings beat',
        'earnings miss', 'revenue beat', 'revenue miss',
        'guidance cut', 'guidance raised', 'lowered guidance',
        'raised guidance', 'profit warning',
        # layoffs / RIFs (massive traffic driver 2024-2026)
        'layoffs', 'laid off', 'laying off', 'mass layoffs',
        'tech layoffs', 'layoff round', 'workforce reduction',
        'reduction in force', 'severance package', 'severance offer',
        'job cuts', 'cutting jobs', 'cut jobs', 'workforce cuts',
        'hiring freeze', 'restructuring plan', 'restructuring charge',
        'shutting down office', 'closing headquarters',
        # exec moves
        'ceo resigns', 'ceo fired', 'ceo steps down', 'new ceo',
        'ceo replaced', 'ceo ousted', 'ceo out', 'named ceo',
        'cfo resigns', 'cfo fired', 'cto resigns',
        'chair resigns', 'chairman resigns', 'chief resigns',
        'board fires', 'board ousts', 'shareholder lawsuit',
        'shareholder revolt', 'proxy fight', 'activist investor',
        # famous execs (business context, not stock price)
        'satya nadella', 'sundar pichai', 'tim cook', 'andy jassy',
        'mark zuckerberg', 'evan spiegel', 'brian chesky',
        'shou zi chew', 'sam altman', 'dario amodei',
        'jensen huang', 'lisa su', 'linda yaccarino',
        'bob iger', 'david zaslav',
        # bankruptcy / distress
        'chapter 11', 'files bankruptcy', 'bankruptcy filing',
        'bankruptcy protection', 'chapter 7', 'insolvency',
        'creditors', 'debt restructuring',
        # brand strategy news
        'rebrand', 'brand refresh', 'logo redesign',
        # sector-defining companies in a business-story context (as
        # opposed to consumer / tech / finance context)
        'boeing quality', 'boeing scandal', 'starbucks earnings',
        'nike layoffs', 'target earnings', 'target layoffs',
        'gm layoffs', 'ford layoffs', 'meta layoffs', 'google layoffs',
        'amazon layoffs', 'microsoft layoffs',
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
    # Auto brand tokens - bare-brand names are collision-prone as
    # substrings. Word-boundary matching stops "ford" hitting
    # "pickford / sanford / rutherford", "audi" hitting "audio /
    # audition", "kia" hitting "arkia", "gm" hitting "gmail",
    # "lucid" hitting "elucidate / lucidly", and "vw" hitting stray
    # letter pairs. Long unique brands (chevrolet, volkswagen,
    # lamborghini) don't collide but including them here is free.
    'ford', 'chevy', 'chevrolet', 'toyota', 'honda', 'nissan',
    'hyundai', 'kia', 'jeep', 'dodge', 'chrysler', 'bmw', 'audi',
    'porsche', 'volkswagen', 'vw', 'volvo', 'subaru', 'mazda',
    'lucid', 'rivian', 'polestar', 'byd', 'cadillac', 'buick',
    'acura', 'lexus', 'infiniti', 'maserati', 'lamborghini',
    'ferrari', 'bentley', 'jaguar', 'tesla', 'gm', 'stellantis',
    'waymo', 'fsd', 'iihs', 'nhtsa',
    # Home tokens with substring-collision risk. "refi" hits
    # "refinery / referee / referred / referred". "condo" hits
    # "condone / condoning / condor". Others are safe as substrings
    # but including them here is idempotent.
    'refi', 'condo',
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
    # Philanthropy sits ahead of the political cluster so grant / policy
    # news that also carries a partisan angle ("proposed grant rules")
    # peels into the philanthropy bucket first. Nonprofit / charity /
    # foundation keywords are unambiguous enough that we don't lose
    # true political stories to it.
    'philanthropy',
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
    # Bucket dict MUST list every non-overall category from
    # `_CATEGORY_PRIORITY` - the harvest pass (and the frontend cards)
    # skip anything not present here. Missing `philanthropy` was the
    # bug that kept the Philanthropy card empty even when the Chronicle
    # of Philanthropy feed had 40 items ready to fold in.
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
        'philanthropy':  [],
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


# Minimum items in a category bucket before we STOP folding in
# secondary-signal rows (headlines / trending-people / wikipedia). Any
# bucket at or above this count is left as pure Google-Trends. Buckets
# below this count get augmented with matching rows from other pools
# so a category never renders thin when the news / people / wiki
# pools obviously carry the beat.
#
# 2026-07-22 (Jenna): bumped 8 -> 10 to guarantee every trending card
# on the Trends tab shows at least ~10 hits when there's matching
# signal available anywhere in the secondary pools.
_THIN_BUCKET_THRESHOLD = 10

# Category-specific cap on how many fold-in rows we'll add from
# secondary pools. Prevents one big headline pool from turning a card
# into a news-only feed WHEN the other pools also have content.
#
# 2026-07-22 (Jenna): bumped 6 -> 10 so a single pool CAN fully fill a
# card up to the threshold when the other pools happen to miss the
# category. Previously a bucket with 6 news items and 0 elsewhere
# stalled at 6 (below the threshold) because the news-cap fired
# before the target was reached. Since we still stop at
# _THIN_BUCKET_THRESHOLD overall, a healthy multi-pool card is
# unchanged; only "one pool has the beat" cards benefit.
_HARVEST_CAP_PER_POOL = 10


def _augment_thin_buckets_from_pools(
        by_cat:              dict[str, list[dict]],
        trending_headlines:  list[dict],
        trending_people:     list[dict],
        wikipedia_trending:  list[dict],
        articles_by_source:  Optional[list[dict]] = None,
        movers:              Optional[dict]      = None,
        philanthropy_news:   Optional[list[dict]] = None,
        business_news:       Optional[list[dict]] = None,
        wall_street_news:    Optional[list[dict]] = None,
) -> dict[str, list[dict]]:
    """Fold matching secondary-signal rows into category buckets that
    came out thin (< `_THIN_BUCKET_THRESHOLD` items) from the
    Google-Trends-only bucketing pass.

    Runs the SAME `_categorize_search_term` matcher against the title
    of each headline / person / wiki article, so a "Karen Read verdict"
    headline lands in `crime` and a "Jack Smith" GDELT person lands in
    `crime` too.  Each fold-in row is stamped with `origin` so the UI
    can render it with a subtle "via news" / "via people" / "via wiki"
    badge instead of pretending it's a Google search.

    This is the fix for the "how can crime be empty for 7 days" UX
    complaint: Google Trends is only ONE of five trending signals, and
    when the top-search pool skews sports-and-entertainment on a given
    day, the other pools still carry the crime / health / finance /
    business beat that the user expects to see.
    """
    if not by_cat:
        return by_cat

    def _add(bucket_key: str, row: dict, origin: str, source_label: str,
              url_key: str, seen: set):
        # Dedupe within a single bucket by URL first, then by lowercased
        # title so a headline and its wiki article don't both fold in.
        key = (bucket_key, url_key.lower() if url_key else '',
                (row.get('_harvest_key') or ''))
        if key in seen:
            return
        seen.add(key)
        by_cat[bucket_key].append(row)

    # We build a shared "seen" set across the whole pass so a Karen
    # Read headline doesn't get folded in twice as (news) and (person).
    seen: set = set()

    # 1) Headlines. First mine `articles_by_source` (the wider pool -
    #    up to ~110 articles across all outlets), then fall back to the
    #    top-15 flat list. Both feed the same news fold-in path.
    news_pool: list[dict] = []
    for outlet in (articles_by_source or []):
        for a in (outlet.get('articles') or []):
            merged = dict(a)
            merged.setdefault('source',        outlet.get('source', ''))
            merged.setdefault('source_label',  outlet.get('source', ''))
            merged.setdefault('domain',        outlet.get('domain', ''))
            news_pool.append(merged)
    # Fall through with the flat list too (dedup by lowercased title).
    _news_seen_titles = {(h.get('title') or '').strip().lower()
                          for h in news_pool if h.get('title')}
    for h in (trending_headlines or []):
        t = (h.get('title') or '').strip().lower()
        if t and t not in _news_seen_titles:
            news_pool.append(h)
            _news_seen_titles.add(t)

    # Philanthropy-specific feed (Chronicle of Philanthropy, NPQ, SSIR,
    # Blue Avocado, Guardian Global Dev) - ~40 items dedicated to
    # philanthropy stories. Merge into the news pool so the philanthropy
    # bucket can fold them in when Google Trends misses the beat.
    for h in (philanthropy_news or []):
        t = (h.get('title') or '').strip().lower()
        if t and t not in _news_seen_titles:
            news_pool.append(h)
            _news_seen_titles.add(t)

    # Business-desk feed (NYT Business + WSJ Business via GN proxy) -
    # ~40 items dedicated to corporate/markets stories. Fold into the
    # news pool so the `business` bucket in the Search tab picks them
    # up on days Google Trends misses the corporate beat.
    for h in (business_news or []):
        t = (h.get('title') or '').strip().lower()
        if t and t not in _news_seen_titles:
            news_pool.append(h)
            _news_seen_titles.add(t)

    # Wall Street desk feed (WSJ/Barron's/FT/Bloomberg/MarketWatch/CNBC
    # Markets/IBD/Seeking Alpha/Reuters Markets) - ~50 items focused
    # on markets, macro, earnings, and investor-facing analysis. Fold
    # into the news pool so `finance` / `business` buckets on the
    # Search tab pick these up on days Google Trends misses the
    # markets beat.
    for h in (wall_street_news or []):
        t = (h.get('title') or '').strip().lower()
        if t and t not in _news_seen_titles:
            news_pool.append(h)
            _news_seen_titles.add(t)

    for h in news_pool:
        title = (h.get('title') or '').strip()
        if not title:
            continue
        cats = _categorize_search_term(title, [])
        if not cats:
            continue
        cat = cats[0]
        if cat not in by_cat:
            continue
        if len(by_cat[cat]) >= _THIN_BUCKET_THRESHOLD:
            continue
        # Cap per-pool fold-ins.
        already_news = sum(1 for r in by_cat[cat] if r.get('origin') == 'news')
        if already_news >= _HARVEST_CAP_PER_POOL:
            continue
        folded = {
            'term':          title,
            'value':         None,
            'growth':        None,
            'related':       [],
            'related_queries': [],
            'url':           h.get('url'),
            'image':         h.get('image'),
            'origin':        'news',
            'origin_label':  (h.get('source_label') or h.get('source')
                               or 'news'),
            'origin_domain': h.get('domain') or '',
            'seendate':      h.get('seendate') or '',
            '_harvest_key':  'news:' + title.lower(),
        }
        _add(cat, folded, 'news',
              folded['origin_label'], h.get('url') or '', seen)

    # 2) Trending people. Row shape (post-annotate): {name, mentions,
    #    projected_mentions, projected_pageviews, trend, ...}.
    for p in (trending_people or []):
        name = (p.get('name') or '').strip()
        if not name:
            continue
        cats = _categorize_search_term(name, [])
        if not cats:
            continue
        cat = cats[0]
        if cat not in by_cat:
            continue
        if len(by_cat[cat]) >= _THIN_BUCKET_THRESHOLD:
            continue
        already_people = sum(1 for r in by_cat[cat] if r.get('origin') == 'person')
        if already_people >= _HARVEST_CAP_PER_POOL:
            continue
        folded = {
            'term':          name,
            'value':         p.get('mentions'),
            'growth':        None,
            'related':       [],
            'related_queries': [],
            'url':           None,
            'image':         None,
            'origin':        'person',
            'origin_label':  'trending people',
            'projected_mentions':   p.get('projected_mentions'),
            'projected_pageviews':  p.get('projected_pageviews'),
            'trend':         p.get('trend'),
            '_harvest_key':  'person:' + name.lower(),
        }
        _add(cat, folded, 'person', 'trending people',
              '', seen)

    # 3) Movers rising / breakout / falling / sustained. Each list is
    #    a set of {term, source, growth, ...} rows. Terms here often
    #    are the freshest signal in the whole dashboard - if a crime
    #    story is breaking out today it'll be in `breakout` even before
    #    it hits the top trending list. So we mine movers WITH a
    #    slightly higher per-pool cap because these are highest-signal.
    if movers and isinstance(movers, dict):
        for bucket_name in ('breakout', 'rising', 'sustained', 'falling'):
            for m in (movers.get(bucket_name) or []):
                term = (m.get('term') or m.get('name') or '').strip()
                if not term:
                    continue
                cats = _categorize_search_term(term, [])
                if not cats:
                    continue
                cat = cats[0]
                if cat not in by_cat:
                    continue
                if len(by_cat[cat]) >= _THIN_BUCKET_THRESHOLD:
                    continue
                already_mv = sum(1 for r in by_cat[cat]
                                    if r.get('origin') == 'mover')
                if already_mv >= _HARVEST_CAP_PER_POOL:
                    continue
                folded = {
                    'term':           term,
                    'value':          m.get('growth'),
                    'growth':         m.get('growth'),
                    'related':        [],
                    'related_queries': [],
                    'url':            None,
                    'image':          None,
                    'origin':         'mover',
                    'origin_label':   bucket_name,
                    '_harvest_key':   'mover:' + term.lower(),
                }
                _add(cat, folded, 'mover', bucket_name, '', seen)

    # 4) Wikipedia trending. Row shape: {title, url, views_today,
    #    delta_pct, is_new, ...}.
    for w in (wikipedia_trending or []):
        title = (w.get('title') or '').strip()
        if not title:
            continue
        # Wikipedia article titles often include underscores; normalize
        # for matching but keep the original for display.
        haystack = title.replace('_', ' ')
        cats = _categorize_search_term(haystack, [])
        if not cats:
            continue
        cat = cats[0]
        if cat not in by_cat:
            continue
        if len(by_cat[cat]) >= _THIN_BUCKET_THRESHOLD:
            continue
        already_wiki = sum(1 for r in by_cat[cat] if r.get('origin') == 'wikipedia')
        if already_wiki >= _HARVEST_CAP_PER_POOL:
            continue
        folded = {
            'term':          haystack,
            'value':         w.get('views_today'),
            'growth':        w.get('delta_pct'),
            'related':       [],
            'related_queries': [],
            'url':           w.get('url'),
            'image':         None,
            'origin':        'wikipedia',
            'origin_label':  'Wikipedia',
            'is_new':        w.get('is_new'),
            '_harvest_key':  'wiki:' + title.lower(),
        }
        _add(cat, folded, 'wikipedia', 'Wikipedia',
              w.get('url') or '', seen)

    return by_cat


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
# Cross-platform "cultural moment" detector
# ============================================================================
# An item is a "true cultural moment" when the same name/topic is trending
# on 3+ distinct platforms in the same window. We aggregate normalized
# entity names across searches / wikipedia / people / social and mutate
# matching rows in-place with a `cross_platform: True` flag + the source
# list, which the frontend renders as a 🔥 badge.
_CROSS_PLATFORM_MIN_SOURCES = 3

_CP_STOPWORDS = {
    'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at', 'is',
    'trending', 'today', 'now', 'news', 'latest', 'best',
}


def _cp_normalize(text: str) -> str:
    """Case-fold, strip punctuation, drop common stopwords, collapse
    whitespace. Two strings hash to the same key iff they refer to the
    same underlying entity."""
    if not text:
        return ''
    # Lowercase, remove leading # (hashtag), strip everything non-alnum.
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _CP_STOPWORDS]
    return ' '.join(tokens)


# ─────────────────────────────────────────────────────────────────────────
# Stream-estimate annotators
# ─────────────────────────────────────────────────────────────────────────
# The daily `stream_estimates` scraper (Claude Sonnet + web_search per
# item) writes a US-audience estimate + day-over-day trend for every
# top podcast / song / streaming title. We stamp each of those fields
# onto the matching chart row here so the frontend can render
# "12.5M weekly US listeners · ↑20% vs yesterday" under the card.
#
# Keys are `podcast:<norm>` / `song:<norm(title+artist)>` /
# `film:<norm>` / `tv:<norm>` — mirrored from
# `scripts/trends_scrapers/stream_estimates._lookup_key`.

_STREAM_FIELDS = (
    'us_estimate', 'us_estimate_low', 'us_estimate_high',
    'unit_label', 'confidence', 'method', 'sources',
    'delta_pct', 'direction', 'prev_estimate',
    'prev_date', 'as_of_date',
)


# Default unit label per kind - used as fallback when the per-platform
# block doesn't carry its own unit (which it doesn't - we derive from
# kind). Matches `stream_estimates._default_unit_for_kind`.
_DEFAULT_UNIT_BY_KIND = {
    'song':    'weekly US streams',
    'podcast': 'weekly US listeners',
    'film':    'weekly US views',
    'tv':      'weekly US views',
    'title':   'weekly US views',
    # For books the noun depends on the platform (readers vs listeners
    # vs library borrows). The aggregate fallback below is generic; the
    # per-platform stamp prefers `_PLATFORM_UNIT_LABEL` when set.
    'book':    'weekly US audience',
    # FAST channels: ad-supported free viewers. Same "views" noun as
    # paid streaming (Nielsen's household definition), but the daily
    # Claude research is calibrated separately against FAST Gauge /
    # TVREV data - see stream_estimates._FAST_PLATFORMS_META.
    'fast_film': 'weekly US views',
    'fast_tv':   'weekly US views',
    # Gaming: Xbox Game Pass Ultimate "plays" = unique US subscribers
    # who launched the title on console / PC / cloud in the past
    # 7 days. See stream_estimates._GAMING_PLATFORMS_META for anchor
    # language + ceiling.
    'game':      'weekly US plays',
    # FAST channels (Channel Ranker sub-tab, 2026-08-21): unique US
    # households tuning to a 24/7 linear channel on Roku Channel /
    # Tubi / Pluto TV / Amazon Live TV for >=1 minute in the past
    # 7 days. Not to be confused with `fast_film` / `fast_tv` which
    # are per-title reach on the same platforms.
    'fast_channel': 'weekly US viewers',
}

# Per (kind, platform) unit label. Wins over Claude's aggregate
# `unit_label` when the stamp resolves to a specific platform. Keeps
# the dashboard chip short + the tooltip noun ("readers" / "listeners"
# / "borrows") crisp. Falls back to `_DEFAULT_UNIT_BY_KIND[kind]` when
# no override exists for the (kind, platform) pair. `amazon` /
# `apple` show different labels depending on whether the row is a
# song / podcast / book, which is why we key by (kind, platform)
# instead of just platform.
_PLATFORM_UNIT_LABEL = {
    # Books
    ('book', 'amazon'):       'weekly US readers',
    ('book', 'apple'):        'weekly US readers',
    ('book', 'audible'):      'weekly US listeners',
    ('book', 'libby_ebook'):  'weekly US library borrows',
    ('book', 'libby_audio'):  'weekly US library borrows',
}


def _stamp_stream_estimate(row: dict, entry: dict,
                            platform_key: str = '',
                            kind_hint: str = '') -> None:
    """Copy stream-estimate fields onto a chart row under the
    `us_streams` namespace.

    When `platform_key` is provided AND `entry.by_platform[platform_key]`
    exists, we stamp THAT platform's number (Spotify song row shows
    Spotify-only US streams; Apple Music row shows Apple-Music-only;
    etc.). When the per-platform block is missing we fall back to the
    aggregate estimate so old-shape snapshots still render.

    `kind_hint` fills in `unit_label` when the entry didn't carry one
    (per-platform blocks don't - the frontend derives it from kind
    via `_DEFAULT_UNIT_BY_KIND`)."""
    if not entry:
        return

    by_platform = entry.get('by_platform') or {}
    per = by_platform.get(platform_key) if platform_key else None

    if per and (per.get('us_estimate') or 0) > 0:
        # Per-platform source of truth.
        unit_label = (
            _PLATFORM_UNIT_LABEL.get((kind_hint, platform_key))
            or _DEFAULT_UNIT_BY_KIND.get(kind_hint)
            or entry.get('unit_label')
            or 'weekly US audience'
        )
        out = {
            'us_estimate':      per.get('us_estimate'),
            'us_estimate_low':  per.get('us_estimate_low'),
            'us_estimate_high': per.get('us_estimate_high'),
            'confidence':       per.get('confidence'),
            'direction':        per.get('direction'),
            'delta_pct':        per.get('delta_pct'),
            'prev_estimate':    per.get('prev_estimate'),
            'prev_date':        per.get('prev_date') or entry.get('prev_date'),
            'as_of_date':       per.get('as_of_date') or entry.get('as_of_date'),
            # Note is per-platform reasoning. Aggregate `method` +
            # `sources` still travel for completeness but the frontend
            # tooltip is simplified.
            'method':           per.get('note') or entry.get('method'),
            'sources':          entry.get('sources'),
            'unit_label':       unit_label,
            'platform':         platform_key,
        }
    else:
        # Fallback to aggregate. This still preserves old-snapshot
        # rendering while the daily cron picks up the new schema.
        out = {k: entry.get(k) for k in _STREAM_FIELDS
                if entry.get(k) is not None}
        if kind_hint and not out.get('unit_label'):
            out['unit_label'] = _DEFAULT_UNIT_BY_KIND.get(kind_hint,
                                                            'weekly US audience')

    row['us_streams'] = {k: v for k, v in out.items() if v is not None}


# Panel-slug -> platform key for the stream_estimates.by_platform block.
# See `stream_estimates._SONG_PLATFORMS` / `_PODCAST_PLATFORMS` /
# `_STREAMING_PLATFORMS_META` - the `key` value there must match.
# Panels that don't have a per-platform anchor (TikTok, Shazam) fall
# back to the aggregate estimate.
_MUSIC_PANEL_TO_PLATFORM = {
    'spotify': 'spotify',
    'apple':   'apple',
    'youtube': 'youtube',
    'amazon':  'amazon',
    # tiktok + shazam intentionally omitted - they don't represent
    # per-track streams, so they get the aggregate (or nothing).
}
_PODCAST_PANEL_TO_PLATFORM = {
    'apple':   'apple',
    'spotify': 'spotify',
    'netflix': 'netflix',
    'amazon':  'amazon',
    'audible': 'audible',
}
_STREAMING_PANEL_TO_PLATFORM = {
    'netflix':    'netflix',
    'disneyplus': 'disneyplus',
    'hulu':       'hulu',
    'max':        'max',
    'primevideo': 'primevideo',
    'espnplus':   'espnplus',
    'britbox':    'britbox',
    'mgmplus':    'mgmplus',
    'starz':      'starz',
}
# FAST-channel panel slug -> platform key inside
# `stream_estimates.items[<kind_prefix>:<norm>].by_platform`. See
# `stream_estimates._FAST_PLATFORMS_META` - the `key` value there must
# match. All 4 FAST panels have per-platform anchors so every FAST
# row surfaces a platform-specific weekly-views number.
_FAST_PANEL_TO_PLATFORM = {
    'roku':   'roku',
    'tubi':   'tubi',
    'pluto':  'pluto',
    'amazon': 'amazon',
}
# Gaming: currently one platform. Same shape as the other tabs so
# adding PS Plus / Nintendo Switch Online / Steam later is a
# one-line addition here (plus a new platform entry in
# stream_estimates._GAMING_PLATFORMS_META).
_GAMING_PANEL_TO_PLATFORM = {
    'xbox_gamepass': 'xbox_gamepass',
}
# book_charts panels -> per-platform key. Libby panels come from a
# separate snapshot (`libby_trends`) but plug into the same book
# estimate rows because `_collect_books` unifies them in
# stream_estimates.py; the annotate walker below handles both.
_BOOK_PANEL_TO_PLATFORM = {
    # book_charts.sources
    'amazon':    'amazon',
    'apple':     'apple',
    'audible':   'audible',
}
_LIBBY_PANEL_TO_PLATFORM = {
    # libby_trends.sources.
    # Magazines aren't researched by the Claude pass (see
    # `_collect_books` -> only ebook + audiobook), so magazine rows
    # get their us_streams entirely from the LA-County-holds fallback
    # inside `_annotate_books_with_streams`. Adding 'magazine' here
    # is what unlocks the fallback path.
    'ebook':     'libby_ebook',
    'audiobook': 'libby_audio',
    'magazine':  'libby_magazine',
}


# Fields the headline_estimates scraper stamps onto each headline row.
# Mirrors `_STREAM_FIELDS` but scoped to article-level "us_readers"
# metadata. Any field the scraper adds later (e.g. `citations`) can be
# added here without touching the annotator.
_READER_FIELDS = (
    'us_estimate', 'us_estimate_low', 'us_estimate_high',
    'unit_label', 'confidence', 'method', 'sources',
    'delta_pct', 'direction', 'prev_estimate',
    'prev_date', 'as_of_date',
)


def _headline_lookup_key(title: str) -> str:
    """Byte-for-byte match with `headline_estimates._lookup_key`."""
    return _cp_normalize(title or '')


def _stamp_reader_estimate(row: dict, entry: Optional[dict]) -> None:
    """Copy the sanitized reader-estimate fields from `entry` (a
    single record in the headline_estimates snapshot) onto `row` as
    `row['us_readers']`. No-op when the estimate is missing or when
    the mid-point is 0 - the frontend badge silently drops in that
    case, so the row stays clean."""
    if not entry:
        return
    mid = entry.get('us_estimate') or 0
    if mid <= 0:
        return
    us_readers = {k: entry[k] for k in _READER_FIELDS if k in entry}
    if not us_readers.get('unit_label'):
        us_readers['unit_label'] = 'daily US readers'
    row['us_readers'] = us_readers


def _annotate_headlines_with_readers(trending_headlines: list,
                                       articles_by_source: list,
                                       philanthropy_news: list,
                                       estimates: dict,
                                       business_news: Optional[list] = None,
                                       wall_street_news: Optional[list] = None) -> None:
    """Stamp `us_readers` on every headline row across the surfaces
    the Headlines tab renders:
      1. `trending_headlines`         - flat "top" list
      2. `articles_by_source[i].articles` - per-outlet lists
      3. `philanthropy_news`          - the Philanthropy sub-tab
      4. `business_news`              - the Business sub-tab (NYT + WSJ)
      5. `wall_street_news`           - the Wall Street sub-tab
                                        (WSJ / Barron's / FT / Bloomberg
                                         / MarketWatch / CNBC Markets /
                                         IBD / Seeking Alpha / Reuters)

    All surfaces key by normalized title so a single Claude estimate
    powers every surface the article appears on. Missing snapshot
    -> silent no-op (rows just render without the reader chip)."""
    if not estimates:
        return
    items = estimates.get('items') or {}
    if not items:
        return

    def _stamp(row: dict) -> None:
        title = (row.get('title') or '').strip()
        if not title:
            return
        key = _headline_lookup_key(title)
        entry = items.get(key)
        if entry:
            _stamp_reader_estimate(row, entry)

    for row in (trending_headlines or []):
        _stamp(row)
    for outlet in (articles_by_source or []):
        for row in (outlet.get('articles') or []):
            _stamp(row)
    for row in (philanthropy_news or []):
        _stamp(row)
    for row in (business_news or []):
        _stamp(row)
    for row in (wall_street_news or []):
        _stamp(row)


# Tier order for Wall Street outlets when a row has no us_readers
# estimate yet (right after the scraper landed, before the daily
# reader-estimate cron catches up). Lower index = ranked higher.
# Order picks the paywalled Wall-Street-native mastheads first
# (WSJ, Bloomberg, Reuters, FT), then the free-to-read markets desks
# (CNBC Markets, Barron's), then the retail-investor mastheads
# (MarketWatch, IBD, Seeking Alpha).
_WS_TIER_ORDER: tuple[str, ...] = (
    'wsj_markets',
    'bloomberg_markets',
    'reuters_markets',
    'ft',
    'cnbc_markets',
    'barrons',
    'marketwatch',
    'ibd',
    'seeking_alpha',
)
_WS_TIER_INDEX: dict[str, int] = {slug: i for i, slug in enumerate(_WS_TIER_ORDER)}
_WS_TIER_FALLBACK: int = len(_WS_TIER_ORDER)


def _ws_publish_epoch(row: dict) -> float:
    """Best-effort RFC-822 / ISO parse of a Wall Street row's publish
    timestamp so recency ties break deterministically. Returns 0.0
    when nothing parses so the row falls to the end of the recency
    band rather than crashing the sort."""
    pub = (row.get('published') or '').strip()
    if not pub:
        return 0.0
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub)
        if dt is not None:
            return dt.timestamp()
    except Exception:
        pass
    try:
        cleaned = pub.replace('Z', '+00:00')
        dt = datetime.fromisoformat(cleaned)
        return dt.timestamp()
    except Exception:
        return 0.0


def _sort_wall_street_by_readership(rows: list[dict]) -> list[dict]:
    """Return `rows` sorted so the "most read" articles surface first,
    with a clean fallback ordering for the rows the reader-estimate
    cron hasn't priced yet.

    Priority (Jenna 2026-08-25):
      1. Rows carrying `us_readers.us_estimate > 0`, sorted by that
         estimate descending. This is the true "most read" signal
         once the daily estimator has caught up.
      2. Un-priced rows fall in after all priced ones, sorted by:
         a. Publication tier (WSJ Markets, Bloomberg Markets,
            Reuters Markets, FT, CNBC Markets, Barron's,
            MarketWatch, IBD, Seeking Alpha),
         b. Publish recency (most recent first),
         c. Title alphabetical.

    After sorting, `rank` is renumbered 1..N so the frontend can
    render a straight ranked list without touching the data further.
    """
    if not rows:
        return rows or []

    def _key(r: dict) -> tuple:
        us = r.get('us_readers') or {}
        est = us.get('us_estimate') if isinstance(us, dict) else 0
        try:
            est_val = float(est or 0)
        except (TypeError, ValueError):
            est_val = 0.0
        priced_flag = 0 if est_val > 0 else 1
        neg_est = -est_val
        slug = (r.get('source') or '').strip()
        tier = _WS_TIER_INDEX.get(slug, _WS_TIER_FALLBACK)
        neg_recency = -_ws_publish_epoch(r)
        title_key = (r.get('title') or '').strip().lower()
        return (priced_flag, neg_est, tier, neg_recency, title_key)

    sorted_rows = sorted(rows, key=_key)
    for i, r in enumerate(sorted_rows, start=1):
        r['rank'] = i
    return sorted_rows


def _annotate_music_with_streams(music_charts: dict, estimates: dict) -> None:
    """Attach per-platform `us_streams` to every song row: a row on
    the Spotify panel gets the Spotify-only US weekly stream count;
    a row on the Apple Music panel gets the Apple-Music-only count.
    Songs key by title+artist because "Home" appears on multiple
    charts with different artists.

    TikTok / Shazam panels don't have per-platform anchors (they
    don't represent streams), so those rows fall back to the
    aggregate estimate."""
    if not music_charts or not estimates:
        return
    items_lookup = estimates.get('items') or {}
    for panel_slug, panel in (music_charts or {}).items():
        platform_key = _MUSIC_PANEL_TO_PLATFORM.get(panel_slug, '')
        for row in (panel or {}).get('items') or []:
            title  = (row.get('title')  or '').strip()
            artist = (row.get('artist') or '').strip()
            key = f'song:{_cp_normalize(f"{title} {artist}")}'
            _stamp_stream_estimate(row, items_lookup.get(key),
                                     platform_key=platform_key,
                                     kind_hint='song')


def _annotate_podcasts_with_streams(podcast_charts: dict, estimates: dict) -> None:
    """Attach per-platform `us_streams` to every podcast row: Apple
    Podcasts panel shows Apple-Podcasts-only US weekly listeners;
    Spotify panel shows Spotify-only. Keyed by normalized title."""
    if not podcast_charts or not estimates:
        return
    items_lookup = estimates.get('items') or {}
    for panel_slug, panel in (podcast_charts or {}).items():
        platform_key = _PODCAST_PANEL_TO_PLATFORM.get(panel_slug, '')
        for row in (panel or {}).get('items') or []:
            title = (row.get('title') or '').strip()
            key = f'podcast:{_cp_normalize(title)}'
            _stamp_stream_estimate(row, items_lookup.get(key),
                                     platform_key=platform_key,
                                     kind_hint='podcast')


def _annotate_streaming_with_streams(streaming_trending: dict,
                                       estimates: dict) -> None:
    """Attach per-platform `us_streams` to every Film/TV row: Netflix
    panel shows Netflix-only US weekly views; Disney+ panel shows
    Disney-only; etc. Tries the film/tv key first (respecting
    `category_display`), falls back to whichever variant the estimate
    was stored under (Disney/ESPN don't ship per-row category_display
    consistently, so scraper stores those under `title:<norm>`)."""
    if not streaming_trending or not estimates:
        return
    items_lookup = estimates.get('items') or {}
    for panel_slug, panel in (streaming_trending or {}).items():
        if not panel:
            continue
        platform_key = _STREAMING_PANEL_TO_PLATFORM.get(panel_slug, '')
        # Same item object appears in `items` + (`films`|`tv`) so
        # stamping one also stamps the other, but we iterate all
        # three for safety in case the app ever splits them.
        for bucket_key in ('items', 'films', 'tv'):
            for row in panel.get(bucket_key) or []:
                title = (row.get('title') or '').strip()
                cat   = (row.get('category_display') or '').lower()
                norm  = _cp_normalize(title)
                if not norm:
                    continue
                order: list[str]
                if 'film' in cat:
                    order = [f'film:{norm}', f'tv:{norm}', f'title:{norm}']
                elif 'tv' in cat:
                    order = [f'tv:{norm}', f'film:{norm}', f'title:{norm}']
                else:
                    order = [f'title:{norm}', f'film:{norm}', f'tv:{norm}']
                entry = None
                kind_hint = 'title'
                for k in order:
                    entry = items_lookup.get(k)
                    if entry:
                        # First key that resolved wins; use its kind
                        # (drop the `<kind>:` prefix).
                        kind_hint = k.split(':', 1)[0]
                        break
                _stamp_stream_estimate(row, entry,
                                         platform_key=platform_key,
                                         kind_hint=kind_hint)


def _annotate_fast_with_streams(fast_trending: dict,
                                  estimates: dict) -> None:
    """Attach per-platform `us_streams` to every FAST-channel row: a
    row on the Roku Channel panel gets the Roku-only US weekly views;
    a row on the Tubi panel gets Tubi-only; etc.

    FAST estimate keys are `fast_film:<norm>` / `fast_tv:<norm>` to
    keep them separate from paid-SVOD estimates for the same title
    (see stream_estimates._collect_fast). We resolve using
    `category_display` first, then fall back the other way in case a
    title's category flipped between snapshots."""
    if not fast_trending or not estimates:
        return
    items_lookup = estimates.get('items') or {}
    for panel_slug, panel in (fast_trending or {}).items():
        if not panel:
            continue
        platform_key = _FAST_PANEL_TO_PLATFORM.get(panel_slug, '')
        # `items` is authoritative; `films`/`tv` may or may not be
        # populated depending on how the frontend renderer splits.
        for bucket_key in ('items', 'films', 'tv'):
            for row in panel.get(bucket_key) or []:
                title = (row.get('title') or '').strip()
                cat   = (row.get('category_display') or '').lower()
                norm  = _cp_normalize(title)
                if not norm:
                    continue
                if cat == 'film':
                    order = [f'fast_film:{norm}', f'fast_tv:{norm}']
                    kind_hint = 'fast_film'
                else:
                    order = [f'fast_tv:{norm}', f'fast_film:{norm}']
                    kind_hint = 'fast_tv'
                entry = None
                for k in order:
                    entry = items_lookup.get(k)
                    if entry:
                        kind_hint = k.split(':', 1)[0]
                        break
                _stamp_stream_estimate(row, entry,
                                         platform_key=platform_key,
                                         kind_hint=kind_hint)


def _annotate_fast_channels_with_views(fast_trending: dict,
                                          estimates: dict) -> None:
    """Attach `us_streams` (weekly US viewers) to every micro-channel
    row inside every FAST platform, then re-sort each platform's
    channel list by view estimate desc so the Channel Ranker sub-tab
    ranks channels by real audience instead of raw airings.

    Keys are `fast_channel:<platform_slug>:<norm_name>` - the same
    key format `stream_estimates._collect_fast_channels` writes AND
    `stream_estimates._lookup_key('fast_channel', name, slug)`
    produces. Same channel on Pluto vs Roku vs Amazon = three
    separate rows with three separate keys and three distinct view
    estimates.

    Channels with no view estimate (never got a Claude call, or the
    call failed) keep their original airings-based order at the tail
    of the sorted list so the ranker still shows every channel.

    Added 2026-08-21 (Jenna: Channel Ranker sub-tab, "ranks based on
    views and give an estimate of how many views each channel had").
    """
    if not fast_trending:
        return
    items_lookup = ((estimates or {}).get('items') or {})
    for panel_slug, panel in (fast_trending or {}).items():
        if not panel:
            continue
        channels = panel.get('channels') or []
        if not channels:
            continue
        # Stamp us_streams on every channel row.
        for row in channels:
            name = (row.get('name') or '').strip()
            norm = _cp_normalize(name)
            if not norm:
                continue
            key = f'fast_channel:{panel_slug}:{norm}'
            entry = items_lookup.get(key)
            # Stream estimates keep the fast_channel entry's number
            # inside `by_platform[<platform_slug>]` where
            # <platform_slug> matches the panel slug (roku/tubi/
            # pluto/amazon). Same platform_key semantics as the
            # other FAST annotator so `_stamp_stream_estimate`
            # sees the right per-platform block.
            _stamp_stream_estimate(row, entry,
                                     platform_key=panel_slug,
                                     kind_hint='fast_channel')

        # Re-sort by view estimate desc. Channels without an
        # estimate (`us_streams` missing or 0) sink to the tail but
        # keep their relative airings-based order via the secondary
        # sort key. Re-stamp `rank` so the frontend renders 1..N
        # matching the new order.
        def _sort_key(row: dict) -> tuple:
            us = (row.get('us_streams') or {})
            v  = us.get('us_estimate') or 0
            # Sort direction: primary desc by views (0 -> last), then
            # secondary asc by original airings-based rank so the
            # untracked tail stays in a deterministic order.
            return (0 if v > 0 else 1, -v, row.get('rank') or 10_000)

        channels.sort(key=_sort_key)
        for i, row in enumerate(channels, 1):
            row['rank'] = i


def _annotate_gaming_with_streams(gaming_trending: dict,
                                    estimates: dict) -> None:
    """Attach per-platform `us_streams` to every game row: a row on
    the Xbox Game Pass Ultimate panel gets Xbox-only weekly US plays.

    Gaming estimates are keyed `game:<norm_title>` (title-only, no
    publisher qualifier since AAA game titles don't collide in the
    same launch window - see stream_estimates._lookup_key)."""
    if not gaming_trending or not estimates:
        return
    items_lookup = estimates.get('items') or {}
    for panel_slug, panel in (gaming_trending or {}).items():
        if not panel:
            continue
        platform_key = _GAMING_PANEL_TO_PLATFORM.get(panel_slug, '')
        for row in panel.get('items') or []:
            title = (row.get('title') or '').strip()
            key = f'game:{_cp_normalize(title)}'
            _stamp_stream_estimate(row, items_lookup.get(key),
                                     platform_key=platform_key,
                                     kind_hint='game')


# Libby local-to-US projection formula. LA County Library serves
# ~10M residents (roughly 3-4% of the ~285M US public-library-served
# population), so the conservative 25x scale-up is the LOW END of the
# 25-35x range the Claude prompt already documents. Dividing by 7
# converts the current-queue hold count into a weekly borrow rate
# (holds are a queue snapshot, not weekly circulation).
#
# Formula:    weekly_us_borrows = round(la_county_holds * 25 / 7)
#
# Only used when the daily Claude pass hasn't produced an estimate
# for this specific row (rank > _MAX_BOOK_ITEMS or fresh title that
# missed today's cron). Ensures EVERY visible Libby row carries a
# US-projected number per Jenna 2026-08-04.
_LIBBY_LOCAL_TO_US_SCALE = 25
_LIBBY_HOLDS_TO_WEEKLY   = 7


def _libby_fallback_us_estimate(holds: int, platform_key: str) -> dict:
    """Return a us_streams dict projecting LA County holds up to a
    weekly US-library-wide borrow count. Conservative low-end scale."""
    if not holds or holds <= 0:
        return {}
    weekly_us = int(round(holds * _LIBBY_LOCAL_TO_US_SCALE
                            / _LIBBY_HOLDS_TO_WEEKLY))
    if weekly_us <= 0:
        return {}
    # unit_label matches what the Claude-produced Libby estimates
    # carry, so the frontend badge reads identically.
    if platform_key == 'libby_ebook':
        unit = 'weekly US library borrows'
    elif platform_key == 'libby_audio':
        unit = 'weekly US library audiobook borrows'
    elif platform_key == 'libby_magazine':
        unit = 'weekly US library magazine reads'
    else:
        unit = 'weekly US library borrows'
    return {
        'us_estimate':      weekly_us,
        'us_estimate_low':  int(weekly_us * 0.7),
        'us_estimate_high': int(weekly_us * 1.4),
        'confidence':       'low',
        'unit_label':       unit,
        'platform':         platform_key,
        'method':           (f'Projected from {holds:,} LA County holds via '
                              f'25x scale-up / 7 = weekly US borrows '
                              f'(conservative low-end).'),
    }


def _annotate_books_with_streams(book_charts: dict,
                                   libby_trends: dict,
                                   estimates: dict) -> None:
    """Attach per-platform `us_streams` to every book row.
      - book_charts panels (amazon/apple/audible) -> platform-specific
        weekly US reader/listener count.
      - libby_trends panels (ebook/audiobook) -> Libby's projected
        weekly US public-library-wide borrows (NOT LA County holds).
    Rows key by (normalized title + artist) - matches `_collect_books`.

    Libby fallback: rows the daily Claude pass hasn't covered get a
    projected US number computed from their raw LA County holds so
    EVERY visible Libby row carries a US number (per Jenna 2026-08-04:
    "make sure in books all numbers are projected up to the us gen pop").
    """
    items_lookup = (estimates or {}).get('items') or {}

    def _stamp_panel(panel_dict: dict,
                      slug_to_platform: dict,
                      kind_hint: str,
                      libby_fallback: bool = False) -> None:
        if not panel_dict:
            return
        for panel_slug, panel in (panel_dict.get('sources')
                                   or panel_dict).items():
            platform_key = slug_to_platform.get(panel_slug, '')
            if not platform_key:
                continue
            for row in (panel or {}).get('items') or []:
                title  = (row.get('title')  or '').strip()
                artist = (row.get('artist') or '').strip()
                key = f'book:{_cp_normalize(f"{title} {artist}")}'
                _stamp_stream_estimate(row, items_lookup.get(key),
                                         platform_key=platform_key,
                                         kind_hint=kind_hint)
                # Libby fallback: if the Claude pass produced nothing
                # for this row but the row carries a raw LA County
                # hold count, project it up ourselves so the tile
                # never shows a local number.
                if libby_fallback and not row.get('us_streams'):
                    fb = _libby_fallback_us_estimate(
                        int(row.get('holds') or 0),
                        platform_key,
                    )
                    if fb:
                        row['us_streams'] = fb

    _stamp_panel(book_charts,   _BOOK_PANEL_TO_PLATFORM,   'book')
    _stamp_panel(libby_trends,  _LIBBY_PANEL_TO_PLATFORM,  'book',
                  libby_fallback=True)


def _annotate_cross_platform_moments(
    trending_people:      list[dict],
    wikipedia_trending:   list[dict],
    trending_searches:    list[dict],
    social_trending:      dict,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Flag items that appear on >=3 platforms with `cross_platform=True`.

    Mutates each list/dict in-place and returns them for chainability.
    Idempotent: re-running clears + recomputes the flags.
    """
    # Build a map: normalized_key -> set of source platform slugs.
    source_map: dict[str, set[str]] = {}

    def _add(source: str, text: str) -> None:
        key = _cp_normalize(text)
        if not key or len(key) < 3:
            return
        source_map.setdefault(key, set()).add(source)

    for p in trending_people or []:
        _add('people', p.get('name') or '')

    for w in wikipedia_trending or []:
        _add('wikipedia', w.get('title') or '')

    for s in trending_searches or []:
        _add('searches', s.get('term') or s.get('query') or '')

    for platform_slug, panel in (social_trending or {}).items():
        for it in (panel or {}).get('items') or []:
            # Reddit/YouTube use `title`; X uses `topic`; Instagram uses
            # `title` = "#tag"; TikTok uses `topic` = "#tag".
            _add(platform_slug, it.get('title') or it.get('topic') or it.get('hashtag') or '')

    # Now mutate each list to stamp the flag + source list on matching
    # items. We reset first so this stays idempotent across cache hits.
    def _stamp(rows: list, source: str, text_field: str) -> None:
        for row in rows or []:
            key = _cp_normalize(row.get(text_field) or '')
            sources = source_map.get(key) or set()
            if len(sources) >= _CROSS_PLATFORM_MIN_SOURCES:
                row['cross_platform']         = True
                row['cross_platform_sources'] = sorted(sources)
                row['cross_platform_count']   = len(sources)
            else:
                # Explicitly clear stale flags from an earlier cache
                # payload so consumers don't have to guard for None.
                for k in ('cross_platform', 'cross_platform_sources',
                          'cross_platform_count'):
                    row.pop(k, None)

    _stamp(trending_people,    'people',    'name')
    _stamp(wikipedia_trending, 'wikipedia', 'title')
    _stamp(trending_searches,  'searches',  'term')
    for platform_slug, panel in (social_trending or {}).items():
        items = (panel or {}).get('items') or []
        for row in items:
            # Social rows can use either `title` or `topic` for the entity
            # name; try both.
            key = _cp_normalize(row.get('title') or row.get('topic') or row.get('hashtag') or '')
            sources = source_map.get(key) or set()
            if len(sources) >= _CROSS_PLATFORM_MIN_SOURCES:
                row['cross_platform']         = True
                row['cross_platform_sources'] = sorted(sources)
                row['cross_platform_count']   = len(sources)
            else:
                for k in ('cross_platform', 'cross_platform_sources',
                          'cross_platform_count'):
                    row.pop(k, None)

    return trending_people, wikipedia_trending, trending_searches, social_trending


def _annotate_with_why(
    trending_people:    list[dict],
    wikipedia_trending: list[dict],
    trending_searches:  list[dict],
) -> None:
    """Stamp a 1-line "why is this trending" context on any row whose
    normalized name matches an entry in today's why_trending snapshot.

    The snapshot is produced daily by scripts/trends_scrapers/why_trending.py.
    Missing snapshot / missing key -> no `why` field on the row (frontend
    just doesn't render the context line).

    Idempotent - safe to call twice.
    """
    snap = _read_snapshot('why_trending') or {}
    items = snap.get('items') or {}
    if not items:
        return

    def _stamp(rows: list, text_field: str) -> None:
        for row in rows or []:
            key = _cp_normalize(row.get(text_field) or '')
            why = items.get(key)
            if why:
                row['why'] = why
            else:
                row.pop('why', None)

    _stamp(trending_people,    'name')
    _stamp(wikipedia_trending, 'title')
    _stamp(trending_searches,  'term')


def _annotate_movers_with_why(movers: dict) -> None:
    """Stamp a `why` field on every mover row (breakout / rising /
    falling / sustained) so every bucket carries a 1-line context
    subhead in the UI - not just Breakout.

    Fallback ladder per row:
      1. `related[0]` - a news headline attached by the trendspy
         source. Breakout rows typically have this; rising/falling
         often do not.
      2. `news_articles[0].title` - same content, alternate field
         (some pool merges strip `related` but preserve `news_articles`).
      3. `why_trending.json` lookup by normalized term. The daily
         Claude pass covers breakout + rising + falling + sustained
         terms, so this catches every mover the ladder didn't already.

    Missing on all 3 -> no `why` set (frontend just skips the caption).
    Idempotent - safe to call twice.
    """
    if not movers or not isinstance(movers, dict):
        return
    snap  = _read_snapshot('why_trending') or {}
    items = snap.get('items') or {}

    def _resolve_why(row: dict) -> str:
        rel = row.get('related') or []
        if rel and isinstance(rel[0], str) and rel[0].strip():
            return rel[0].strip()
        na = row.get('news_articles') or []
        if na and isinstance(na[0], dict):
            t = (na[0].get('title') or na[0].get('headline') or '').strip()
            if t:
                return t
        if items:
            key = _cp_normalize(row.get('term') or '')
            why = items.get(key)
            if why and isinstance(why, str) and why.strip():
                return why.strip()
        return ''

    for bucket in ('breakout', 'rising', 'falling', 'sustained'):
        for row in movers.get(bucket) or []:
            why = _resolve_why(row)
            if why:
                row['why'] = why
            else:
                row.pop('why', None)


# ============================================================================
# Fused Trending feed
# ============================================================================
# The "Trending" tab is a single ranked feed that fuses every signal in
# the dashboard: searches, people, wikipedia, movers, music, podcasts,
# books, social, streaming, films, headlines, philanthropy. Each item's
# score is:
#
#     score = sum_over_sources( rank_score * source_weight )
#           + (distinct_platforms - 1) * CROSS_PLATFORM_BONUS
#
# where rank_score linearly decays from 1.0 (rank #1) to 0 (last rank
# on that source), source_weight reflects how strong / diverse each
# signal is, and CROSS_PLATFORM_BONUS rewards items appearing on many
# distinct tabs (the true "cultural moment" pattern).
#
# Weights (higher = louder signal). Movers/breakout gets an outsized
# multiplier because breakout status IS the definition of "trending"
# in day-over-day / week-over-week momentum terms; a breakout that
# also shows on 3 other tabs vaults to the top of the feed.
_FUSE_WEIGHTS = {
    'search':            1.00,   # mass intent
    'people':            1.00,   # already a fused person index
    'wikipedia':         0.80,   # cultural interest
    'mover_breakout':    1.50,   # momentum king
    'mover_rising':      1.10,
    'mover_sustained':   0.80,
    'social':            0.90,   # real-time
    'headlines':         0.70,
    'streaming':         0.70,
    'music':             0.70,
    'films':             0.60,   # theatrical
    'podcasts':          0.50,
    'books':             0.50,
    'philanthropy':      0.40,
    'business':          0.55,   # NYT + WSJ business desks (higher
                                  # signal than philanthropy - both
                                  # outlets skew hard toward market-
                                  # moving news that also drives
                                  # search interest)
    'wall_street':       0.55,   # WSJ / Barron's / FT / Bloomberg /
                                  # MarketWatch / CNBC Markets / IBD /
                                  # Seeking Alpha / Reuters. Same
                                  # weight as `business` - both signal
                                  # market-moving news; matched so
                                  # cross-desk hits don't over-stack
                                  # a story that both sections cover.
}
_FUSE_CROSS_PLATFORM_BONUS = 0.25
_FUSE_MIN_KEY_LEN          = 3
_FUSE_TOP_N                = 60

# Strip 4-digit year tokens so "The Odyssey (2026)" collapses to
# "odyssey" - same underlying entity as bare "The Odyssey". Only years
# in the 1900-2099 range to avoid nuking legit numeric tokens.
_FUSE_YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')


def _fuse_normalize(text: str) -> str:
    """Fusion-key normalization: `_cp_normalize` + year-stripping.

    Two strings hash to the same key iff they refer to the same
    underlying entity - even across year suffixes and casing / punctuation
    variations. Used only for the fusion feed; the 🔥 cultural-moment
    detector keeps using `_cp_normalize` so its cross-platform threshold
    stays consistent with the row-level `cross_platform` flag.
    """
    key = _cp_normalize(text or '')
    if not key:
        return ''
    key = _FUSE_YEAR_RE.sub(' ', key)
    return re.sub(r'\s+', ' ', key).strip()


def _fuse_display_name_choice(current: str, candidate: str) -> str:
    """Pick the "better looking" display name across two variants of
    the same entity. Prefers: (1) fewer trailing digits (drops year
    suffixes), (2) more Title-Cased words, (3) longer non-shouty text.
    """
    if not candidate:
        return current
    if not current:
        return candidate
    # Prefer strings without trailing year parens
    cur_has_year = bool(_FUSE_YEAR_RE.search(current))
    can_has_year = bool(_FUSE_YEAR_RE.search(candidate))
    if cur_has_year and not can_has_year:
        return candidate
    if can_has_year and not cur_has_year:
        return current
    # Prefer the one with more Title-Cased tokens
    def _titled(t: str) -> int:
        return sum(1 for w in t.split() if w[:1].isupper() and not w.isupper())
    if _titled(candidate) > _titled(current):
        return candidate
    # Prefer the shorter one when both are Title Case (dropping a
    # trailing platform tag)
    if len(candidate) < len(current) and _titled(candidate) >= _titled(current):
        return candidate
    return current


def _compute_fused_trending(cards: dict, limit: int = _FUSE_TOP_N) -> list[dict]:
    """Rank every trending signal in `cards` into a single fused feed.

    Returns a list of `{rank, name, score, platform_count, sources,
    url, image, why}` dicts sorted by score descending. `sources` is
    a list of `{tab, tab_label, subtab, rank, url?, image?}` showing
    where the item is trending.

    Idempotent + pure - safe to call on cached or fresh payloads.
    """
    fused: dict[str, dict] = {}

    def _add(text: str, display: str, source_type: str,
             tab_label: str, subtab_label: Optional[str],
             rank: int, max_rank: int,
             url: Optional[str] = None,
             image: Optional[str] = None) -> None:
        norm = _fuse_normalize(text)
        if not norm or len(norm) < _FUSE_MIN_KEY_LEN:
            return
        # Rank score: #1 -> 1.0, last -> ~0. Clamp at 0.
        denom = max(max_rank, 1)
        rank_score = max(0.0, 1.0 - (rank - 1) / denom)
        weight  = _FUSE_WEIGHTS.get(source_type, 0.5)
        weighted = rank_score * weight

        row = fused.setdefault(norm, {
            'name':     display or text,
            'score':    0.0,
            'sources':  [],
            'image':    None,
            'best_url': None,
        })
        row['score'] += weighted
        row['sources'].append({
            'tab':        source_type,
            'tab_label':  tab_label,
            'subtab':     subtab_label,
            'rank':       rank,
            'url':        url,
            'image':      image,
        })
        row['name'] = _fuse_display_name_choice(row['name'], display or text)
        # First non-empty image wins (usually people/wikipedia have the
        # cleanest headshot).
        if image and not row['image']:
            row['image'] = image
        # First non-empty URL wins.
        if url and not row['best_url']:
            row['best_url'] = url

    # ---------------- Trending searches ----------------
    searches = cards.get('trending_searches') or []
    max_s = min(len(searches), 80)
    for i, s in enumerate(searches[:max_s]):
        _add(s.get('term') or s.get('query') or '',
             s.get('term') or s.get('query') or '',
             'search', 'Search', 'Overall', i + 1, max_s)

    # ---------------- Trending people ----------------
    people = cards.get('trending_people') or []
    max_p = min(len(people), 30)
    for i, p in enumerate(people[:max_p]):
        _add(p.get('name') or '', p.get('name') or '',
             'people', 'Trending people', None, i + 1, max_p,
             image=p.get('image') or p.get('thumb') or p.get('thumbnail'))

    # ---------------- Wikipedia ----------------
    # Wikipedia rolls up under the same UI tab as trending people
    # ("Trending people" is the tab label users see). Sharing the tab
    # label here means the fusion chip strip collapses a person-hit +
    # a wiki-hit into ONE chip (via the frontend's tab-label dedupe)
    # and the cross-platform bonus counts them as one platform, which
    # matches how users read the dashboard.
    wiki = cards.get('wikipedia_trending') or []
    max_w = min(len(wiki), 30)
    for i, w in enumerate(wiki[:max_w]):
        _add(w.get('title') or '', w.get('title') or '',
             'wikipedia', 'Trending people', None, i + 1, max_w,
             url=w.get('url'),
             image=w.get('image') or w.get('thumbnail'))

    # ---------------- Movers (breakout / rising / sustained) ----------------
    movers = cards.get('movers') or {}
    for bucket_name, source_type in (
        ('breakout',  'mover_breakout'),
        ('rising',    'mover_rising'),
        ('sustained', 'mover_sustained'),
    ):
        bucket = movers.get(bucket_name) or []
        max_b = min(len(bucket), 20)
        for i, m in enumerate(bucket[:max_b]):
            _add(m.get('term') or '', m.get('term') or '',
                 source_type, 'Movers', bucket_name.title(), i + 1, max_b)

    # ---------------- Music (per-source cards) ----------------
    for src_slug, panel in (cards.get('music_trending') or {}).items():
        items = (panel or {}).get('items') or []
        max_i = min(len(items), 25)
        panel_label = (panel or {}).get('label') or src_slug
        for i, it in enumerate(items[:max_i]):
            _add(it.get('title') or '', it.get('title') or '',
                 'music', 'Music', panel_label, i + 1, max_i,
                 url=it.get('url'), image=it.get('image'))

    # ---------------- Podcasts ----------------
    for src_slug, panel in (cards.get('podcasts_trending') or {}).items():
        items = (panel or {}).get('items') or []
        max_i = min(len(items), 20)
        panel_label = (panel or {}).get('label') or src_slug
        for i, it in enumerate(items[:max_i]):
            _add(it.get('title') or '', it.get('title') or '',
                 'podcasts', 'Podcasts', panel_label, i + 1, max_i,
                 url=it.get('url'), image=it.get('image'))

    # ---------------- Books ----------------
    for src_slug, panel in (cards.get('books_trending') or {}).items():
        items = (panel or {}).get('items') or []
        max_i = min(len(items), 20)
        panel_label = (panel or {}).get('label') or src_slug
        for i, it in enumerate(items[:max_i]):
            _add(it.get('title') or '', it.get('title') or '',
                 'books', 'Books', panel_label, i + 1, max_i,
                 url=it.get('url'), image=it.get('image'))

    # ---------------- Films (theatrical ticketing) ----------------
    for src_slug, panel in (cards.get('films_ticketing') or {}).items():
        items = (panel or {}).get('items') or []
        max_i = min(len(items), 20)
        panel_label = (panel or {}).get('label') or src_slug
        for i, it in enumerate(items[:max_i]):
            _add(it.get('title') or '', it.get('title') or '',
                 'films', 'Films', panel_label, i + 1, max_i,
                 url=it.get('url'), image=it.get('image'))

    # ---------------- Streaming (film + tv per platform) ----------------
    for platform_slug, panel in (cards.get('streaming_trending') or {}).items():
        panel_label = (panel or {}).get('label') or platform_slug
        for kind in ('film', 'tv'):
            items = (panel or {}).get(kind) or []
            max_i = min(len(items), 15)
            subtab = f'{panel_label} - {kind.title()}'
            for i, it in enumerate(items[:max_i]):
                _add(it.get('title') or '', it.get('title') or '',
                     'streaming', 'Streaming', subtab, i + 1, max_i,
                     url=it.get('url'), image=it.get('image'))

    # ---------------- Social (posts / videos / topics) ----------------
    for platform_slug, panel in (cards.get('social_trending') or {}).items():
        items = (panel or {}).get('items') or []
        max_i = min(len(items), 20)
        panel_label = (panel or {}).get('label') or platform_slug
        for i, it in enumerate(items[:max_i]):
            text = it.get('title') or it.get('topic') or it.get('hashtag') or ''
            if not text:
                continue
            _add(text, text, 'social', 'Social', panel_label, i + 1, max_i,
                 url=it.get('url'),
                 image=it.get('image') or it.get('thumb') or it.get('thumbnail'))

    # ---------------- Headlines ----------------
    headlines = cards.get('trending_headlines') or []
    max_h = min(len(headlines), 30)
    for i, h in enumerate(headlines[:max_h]):
        _add(h.get('title') or '', h.get('title') or '',
             'headlines', 'Headlines', None, i + 1, max_h,
             url=h.get('url'), image=h.get('image'))

    # ---------------- Philanthropy ----------------
    phil = cards.get('philanthropy_news') or []
    max_ph = min(len(phil), 15)
    for i, p in enumerate(phil[:max_ph]):
        _add(p.get('title') or '', p.get('title') or '',
             'philanthropy', 'Philanthropy', None, i + 1, max_ph,
             url=p.get('url'), image=p.get('image'))

    # ---------------- Business ----------------
    biz = cards.get('business_news') or []
    max_b = min(len(biz), 15)
    for i, b in enumerate(biz[:max_b]):
        _add(b.get('title') or '', b.get('title') or '',
             'business', 'Business', None, i + 1, max_b,
             url=b.get('url'), image=b.get('image'))

    # ---------------- Wall Street ----------------
    ws = cards.get('wall_street_news') or []
    max_w = min(len(ws), 15)
    for i, w in enumerate(ws[:max_w]):
        _add(w.get('title') or '', w.get('title') or '',
             'wall_street', 'Wall Street', None, i + 1, max_w,
             url=w.get('url'), image=w.get('image'))

    # Cross-platform bonus: count DISTINCT tab labels (not source
    # instances). "Spotify + Apple Music" is 1 tab (Music), not 2.
    for row in fused.values():
        tabs = {s['tab_label'] for s in row['sources'] if s.get('tab_label')}
        row['platform_count'] = len(tabs)
        row['score'] += max(0, row['platform_count'] - 1) * _FUSE_CROSS_PLATFORM_BONUS

    # Attach `why` from the daily why_trending snapshot when the
    # normalized key matches. The snapshot is keyed by `_cp_normalize`
    # so we look up via that (not the year-stripped fusion key) - a
    # miss just leaves `why` empty and the frontend renders without
    # the caption row.
    why_snap  = _read_snapshot('why_trending') or {}
    why_items = why_snap.get('items') or {}
    if why_items:
        # Build a fusion-key -> why lookup by re-hashing whatever the
        # snapshot has. Cheaper than reversing every fused entry.
        why_by_fuse: dict[str, str] = {}
        for k, v in why_items.items():
            fk = _fuse_normalize(k)
            if fk and v and fk not in why_by_fuse:
                why_by_fuse[fk] = v
        for norm, row in fused.items():
            w = why_by_fuse.get(norm) or why_items.get(norm)
            if w:
                row['why'] = w

    # Sort by score, take top N.
    ranked = sorted(fused.values(), key=lambda r: r['score'], reverse=True)

    output: list[dict] = []
    for i, r in enumerate(ranked[:limit]):
        # Deduplicate source appearances by (tab, subtab) so the chip
        # strip doesn't repeat "Music - Spotify Top 200" twice for the
        # same song. Keep the best (lowest) rank on each (tab, subtab).
        by_key: dict[tuple, dict] = {}
        for s in r['sources']:
            k = (s.get('tab'), s.get('subtab'))
            existing = by_key.get(k)
            if not existing or (s.get('rank') or 999) < (existing.get('rank') or 999):
                by_key[k] = s
        clean_sources = sorted(by_key.values(),
                                key=lambda s: (s.get('rank') or 999))
        output.append({
            'rank':           i + 1,
            'name':           r['name'],
            'score':          round(r['score'], 3),
            'platform_count': r['platform_count'],
            'sources':        clean_sources,
            'url':            r['best_url'],
            'image':          r['image'],
            'why':            r.get('why') or '',
        })
    return output


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
    # ADD 2026-08-14 (Jenna feedback): Title-Case prepositions /
    # conjunctions / determiners / networks that appear MID-headline.
    # Without these, runs like "Fans React To Nickelodeon's" survive
    # after "Fans" / "React" are trimmed off the front, because "To"
    # is Title Case in a Title Case headline and "Nickelodeon" is a
    # network name.
    'To', 'For', 'From', 'With', 'By', 'In', 'On', 'At', 'As',
    'And', 'Or', 'But', 'Nor', 'So', 'Yet', 'Of',
    'All', 'Any', 'Some', 'Each', 'Every', 'Many', 'Most', 'Much',
    'A', 'An',
    # US networks / studios / streamers that dominate entertainment
    # headlines but are never a person's name
    'Nickelodeon', 'Netflix', 'Hulu', 'Disney', 'Peacock', 'HBO',
    'Warner', 'Paramount', 'Universal', 'MGM', 'Lionsgate', 'Fox',
    'NBC', 'CBS', 'ABC', 'PBS', 'CNN', 'MSNBC', 'ESPN', 'YouTube',
    'TikTok', 'Instagram', 'Twitter', 'Reddit', 'Facebook', 'Meta',
    'Amazon', 'Apple', 'Google', 'Microsoft', 'Spotify', 'Threads',
    'Twitch',
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
    # ADD 2026-08-14 (Jenna feedback): YouTube / music-video title
    # fragments were being extracted as Title Case "people". Troye Sivan's
    # "She's the Best (Official Music Video)" surfaced "Official Music"
    # as a trending person; a Nickelodeon comeback headline surfaced
    # "Fans React" as a trending person. None of these tokens appear
    # in real US person names.
    'Official', 'Music', 'Video', 'Videos', 'Audio', 'Playlist',
    'Playlists', 'Album', 'Albums', 'Song', 'Songs', 'Single',
    'Singles', 'Track', 'Tracks', 'Mixtape', 'Mixtapes',
    'Fans', 'React', 'Reacts', 'Reaction', 'Reactions',
    'Reacted', 'Reacting',
    'Reboot', 'Reboots', 'Rebooted', 'Rebooting', 'Revival',
    'Revivals', 'Sequel', 'Sequels', 'Prequel', 'Prequels',
    'Announcement', 'Announcements', 'Lineup', 'Lineups',
    'Debut', 'Debuts', 'Release', 'Releases', 'Cameo', 'Cameos',
    'Cast', 'Casting', 'Roster', 'Rosters', 'Fall', 'Winter',
    'Spring', 'Summer', 'Autumn',
    # US city Title Case tokens - never a common first/last name, but
    # very common as a possessive prefix in team-attribution headlines
    # ("Chicago's DiJonai Carrington", "Boston's Jaylen Brown"). The
    # possessive-strip on the first token normalizes "Chicago's" to
    # "Chicago" which then hits this set and gets trimmed off, leaving
    # just the actual person name. Cities that are ALSO common names
    # (Brooklyn, Charlotte, Dallas, Phoenix, Houston, Cleveland) are
    # intentionally left out to avoid false positives.
    'Chicago', 'Boston', 'Miami', 'Denver', 'Portland', 'Seattle',
    'Tampa', 'Baltimore', 'Pittsburgh', 'Cincinnati', 'Milwaukee',
    'Nashville', 'Atlanta', 'Detroit', 'Sacramento', 'Oakland',
    'Minneapolis', 'Jacksonville', 'Indianapolis', 'Anaheim',
    'Toronto', 'Vancouver', 'Montreal', 'Edmonton', 'Calgary',
    'Philadelphia',
    # ADD 2026-08-17 (Jenna feedback): role / title prefixes that
    # appear before a person's name in headlines. Without these,
    # "Actress Hayden Panettiere" surfaces as a separate Trending
    # People row from the standalone "Hayden Panettiere" - same
    # individual, two counter buckets. Front-trim now normalizes both
    # to "Hayden Panettiere" so the merge is automatic.
    #
    # Roles that could ALSO be real first/last names (Prince, King,
    # Queen, Duke, Bishop, Cardinal, Sir, Lord, Star) are intentionally
    # left out to avoid rejecting Prince (the musician), Regina King,
    # Queen Latifah, etc.
    #
    # Entertainment / media
    'Actor', 'Actress', 'Comedian', 'Comic', 'Comedienne',
    'Singer', 'Rapper', 'Musician', 'Producer', 'Songwriter',
    'Composer', 'Conductor', 'Director', 'Author', 'Writer',
    'Screenwriter', 'Novelist', 'Playwright', 'Poet',
    'Model', 'Supermodel', 'Chef', 'Baker', 'Host', 'Anchor',
    'Reporter', 'Journalist', 'Correspondent', 'Columnist',
    'Editor', 'Photographer', 'Painter', 'Artist', 'Sculptor',
    'Dancer', 'Choreographer', 'Illustrator',
    'Filmmaker', 'Cinematographer', 'Broadcaster',
    'Podcaster', 'Streamer', 'Influencer', 'YouTuber',
    # Sports
    'Coach', 'Manager', 'Trainer', 'Athlete', 'Player',
    'Pitcher', 'Catcher', 'Quarterback', 'Striker', 'Defender',
    'Goalie', 'Goalkeeper', 'Referee', 'Umpire', 'Rookie',
    'Veteran', 'Legend', 'Champion', 'Champ', 'Contender',
    'Boxer', 'Fighter', 'Wrestler', 'Gymnast', 'Skier',
    'Skater', 'Runner', 'Swimmer', 'Cyclist', 'Golfer',
    'Jockey',
    # Politics / government
    'President', 'Senator', 'Congressman', 'Congresswoman',
    'Congressperson', 'Governor', 'Mayor', 'Councilman',
    'Councilwoman', 'Councilperson', 'Judge', 'Justice',
    'Attorney', 'Lawyer', 'Solicitor', 'Ambassador', 'Diplomat',
    'Speaker', 'Chancellor', 'Premier', 'Minister', 'Vice',
    'Former', 'Rep', 'Sen', 'Gov',
    # Business
    'Founder', 'Cofounder', 'Executive', 'Entrepreneur',
    'Investor', 'Billionaire', 'Millionaire', 'Mogul', 'Tycoon',
    'Owner', 'Chairman', 'Chairwoman', 'Chair', 'Chairperson',
    'Boss',
    # Titles / honorifics that head-of-run in headlines
    'Doctor', 'Reverend', 'Pastor', 'Rabbi', 'Imam',
    'General', 'Colonel', 'Captain', 'Lieutenant', 'Sergeant',
    'Officer', 'Detective', 'Sheriff', 'Deputy', 'Constable',
    'Marshal',
    # Family-relation prefixes ("Widow of firefighter John Smith",
    # "Son of former president...")
    'Widow', 'Widower', 'Wife', 'Husband', 'Son', 'Daughter',
    'Brother', 'Sister', 'Mother', 'Father', 'Mom', 'Dad',
    'Grandma', 'Grandpa', 'Grandmother', 'Grandfather',
    'Auntie', 'Uncle', 'Aunt',
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
    # ADD 2026-08-05 (Jenna feedback): non-person phrases that leaked
    # into the Trending People card because the extractor caught them
    # as Title Case runs in news headlines. "Salt Lake" is a city
    # (surfaced from a Spanish-language soccer headline for Leagues
    # Cup); "Election Results" is a topic; "Oath Keepers" is a right-
    # wing organization; etc.
    'salt lake', 'salt lake city', 'real salt lake',
    'election results', 'election night', 'primary election',
    'general election', 'special election', 'runoff election',
    'oath keepers', 'proud boys', 'antifa',
    'january 6', 'jan 6',
    'leagues cup', 'gold cup', 'copa america',
    # ADD 2026-08-14 (Jenna feedback): defense-in-depth phrase rejects
    # in case a token doesn't get caught up top (mixed casing, hyphen
    # variants, etc.). Belt-and-suspenders alongside the new token
    # entries above.
    'official music', 'official video', 'official audio',
    'music video', 'lyric video', 'live performance',
    'fans react', 'fans reacts', 'fan reaction', 'fan reactions',
    'watch party', 'watch parties', 'live stream',
    'trailer drop', 'trailer drops',
}


# Person alias map (2026-08-14): canonicalize name variants so the same
# individual is counted once. Keys are lowercase-normalized, values are
# the canonical display name. Add rows as we spot duplicates in the
# Trending People card - the source-of-truth is whichever spelling most
# outlets use today.
_PERSON_ALIASES = {
    # Enes Kanter legally changed his name to Enes Kanter Freedom in
    # 2021 after becoming a US citizen. Some outlets still use the old
    # name, others use the new one; both variants were surfacing as
    # separate rows.
    'enes kanter': 'Enes Kanter Freedom',
}


def _canonical_person_name(name: str) -> str:
    """Fold known name-change / alias pairs so both variants count as
    one entry. Callers pass the raw extracted string; we return the
    canonical display name (or the input unchanged when no alias
    exists)."""
    if not name:
        return name
    return _PERSON_ALIASES.get(name.strip().lower(), name)

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
        # Fold possessive suffix on the trailing token so "Hayden
        # Panettiere's ex-boyfriend..." captures as "Hayden Panettiere",
        # not "Hayden Panettiere's" (a separate counter bucket). The
        # _NAME_RE character class [A-Za-z'\-]* absorbs the apostrophe
        # inside the trailing token, so the strip has to happen after
        # the run has been split into parts.
        if parts:
            last = parts[-1]
            if last.endswith("'s") and len(last) > 2:
                parts[-1] = last[:-2]
            elif last.endswith("'"):
                parts[-1] = last[:-1]
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
    # Parallel list of {text, source, url, kind} dicts for each context
    # snippet - lets the frontend render "<headline> - <outlet>" like the
    # Movers card, and lets a click deep-link back to the article.
    context_meta: dict[str, list[dict]] = defaultdict(list)
    for kind, text, source, url in corpus:
        for raw_name in _extract_person_names(text):
            # Fold name-change / alias pairs (e.g. "Enes Kanter" +
            # "Enes Kanter Freedom") so a single individual doesn't
            # get two rows in the Trending People card.
            name = _canonical_person_name(raw_name)
            if kind == 'search' or kind == 'social':
                counts[name] += 2
            else:
                counts[name] += 1
            source_diversity[name].add(kind)
            snippet = (text or '').strip()
            if snippet and len(contexts[name]) < 3:
                contexts[name].append(snippet[:140])
                context_meta[name].append({
                    'text':   snippet[:140],
                    'source': source or '',
                    'url':    url or '',
                    'kind':   kind,
                })

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
            'name':         name,
            'mentions':     cnt,
            'context':      contexts.get(name, [])[:3],
            'context_meta': context_meta.get(name, [])[:3],
            'sources':      sorted(source_diversity.get(name, [])),
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

    # US-person classifier pass (Jenna 2026-08-05). GDELT-side people
    # come from parsing Title Case runs in news headlines - which
    # leaked non-people (events like "Election Results", places like
    # "Salt Lake", orgs like "Oath Keepers") and foreign celebs
    # (Indian actors dominating international news RSS). We look up
    # each candidate's Wikipedia description in parallel, then drop
    # any name whose description doesn't classify as a US person.
    # Names with NO Wikipedia page are kept (benefit of doubt - the
    # extractor's own filters already caught most junk).
    if people:
        try:
            from scripts.trends_scrapers.wikipedia_trending import (
                _classify_person_row,
            )
            names   = [p['name'] for p in people]
            descs   = wikipedia_descriptions(names, timeout_s=8) or {}
            kept: list[dict] = []
            for p in people:
                d = descs.get(p['name']) or {}
                desc    = d.get('description') or ''
                extract = d.get('extract')     or ''
                thumb   = d.get('thumbnail')   or ''
                # Stamp the Wikipedia thumbnail URL onto every row we
                # keep so the frontend can render a headshot next to
                # the name. The CSS-side purple silhouette fallback
                # handles rows where Wikipedia has no image (or where
                # we got no article hit at all).
                if not desc and not extract:
                    # No Wikipedia hit - keep the row (benefit of
                    # doubt) but leave `image` blank; the frontend
                    # falls back to the silhouette placeholder.
                    p['image'] = thumb  # usually '' here but pass through
                    kept.append(p)
                    continue
                if _classify_person_row(desc, extract):
                    p['wikipedia_description'] = desc
                    p['image']                 = thumb
                    kept.append(p)
                # else: drop the row (event / org / place / foreign)
            people = kept
        except Exception as e:
            logger.debug("trends_iq person classifier failed: %s", e)

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


# ---------------------------------------------------------------------------
# Poster / thumbnail enrichment for streaming Top-10 rows.
#
# None of the six streaming scrapers capture thumbnails (Netflix's HTML
# has box art but it's user-personalized; the others' Playwright DOM
# extractors focus on titles). Rather than teach each scraper to grab a
# poster, we look them up centrally via Wikipedia:
#
#   1. OpenSearch to resolve fuzzy title -> exact page slug
#      (adds " TV series" / " film" as a suffix hint for disambiguation)
#   2. REST summary API to grab the infobox thumbnail
#   3. MediaWiki pageimages action as a fallback (finds title-card art
#      on pages where the summary API's thumbnail was stripped)
#
# iTunes Search was tried first and hit ~0% for streaming exclusives
# (Netflix/Disney+/Prime originals aren't sold on iTunes), so this
# ended up being the right lookup path. Wikipedia hits ~80% on the
# common Top-10 titles; the ~20% that miss (mostly ESPN+ studio shows,
# brand-new series without a settled Wikipedia article) fall back to
# the film-strip SVG placeholder in the frontend.
#
# Substring guard on match: we normalize both the queried title and
# the candidate page title (strip punctuation + lowercase) and require
# the query to be a substring of the candidate. This prevents wrong
# matches like Landman -> "Lawman (TV series)" that fuzzy OpenSearch
# hits would otherwise return.
#
# Cached at module scope so lookups are ~one-time per unique title
# across the life of the Flask worker.
# ---------------------------------------------------------------------------

_WIKI_POSTER_CACHE: dict[tuple[str, str], str] = {}
_WIKI_POSTER_UA = 'BehavioralGraphTrendsBot/1.0 (jenna@crosswalknyc.com)'
_WIKI_OPENSEARCH_URL = 'https://en.wikipedia.org/w/api.php'
_WIKI_SUMMARY_URL    = 'https://en.wikipedia.org/api/rest_v1/page/summary/'
_WIKI_PAGEIMAGES_URL = 'https://en.wikipedia.org/w/api.php'


def _norm_title_for_poster(title: str) -> str:
    """Strip trailing "(S1)" / "Season 1" / ": Season 2" style suffixes,
    year tags, and marketing suffixes like "Trailer" / "Sneak Peek" /
    "Official Trailer" so the poster lookup match rate is higher."""
    t = str(title or '').strip()
    if not t:
        return ''
    # Marketing suffixes ("Trailer", "Official Trailer", "Sneak Peek",
    # "Behind the Scenes", "Teaser") - streaming platforms slot these
    # into their Top-10 alongside real titles. Strip the noun after
    # optional year so "Shark Week 2026 Trailer" resolves to "Shark Week".
    t = re.sub(
        r'\s*\d{4}\s*(?:official\s+)?(?:trailer|teaser|sneak\s+peek|'
        r'behind\s+the\s+scenes|first\s+look|clip|featurette)\s*$',
        '', t, flags=re.IGNORECASE)
    t = re.sub(
        r'\s*(?:official\s+)?(?:trailer|teaser|sneak\s+peek|'
        r'behind\s+the\s+scenes|first\s+look|clip|featurette)\s*$',
        '', t, flags=re.IGNORECASE)
    # ": Season 3" or " Season 3"
    t = re.sub(r'[:\s]+season\s+\d+.*$', '', t, flags=re.IGNORECASE)
    # " (S3)" / ": S3"
    t = re.sub(r'[:\s]*\(?s\d+\)?$', '', t, flags=re.IGNORECASE)
    # "(2026)" year tag
    t = re.sub(r'\s*\(\d{4}\)\s*$', '', t)
    return t.strip(' :-')


def _norm_for_match(s: str) -> str:
    """Lowercase + strip non-alphanumeric for substring matching between
    a query title and a candidate Wikipedia page title. This lets
    "Deadpool & Wolverine" match "Deadpool & Wolverine" via alpha-only
    comparison while rejecting "Landman" -> "Lawman (TV series)".
    """
    return re.sub(r'[^a-z0-9]+', '', str(s).lower())


def _wiki_opensearch_titles(query: str, limit: int = 5) -> list[str]:
    try:
        r = requests.get(
            _WIKI_OPENSEARCH_URL,
            params={'action': 'opensearch', 'search': query, 'limit': limit,
                    'format': 'json', 'namespace': 0},
            headers={'User-Agent': _WIKI_POSTER_UA},
            timeout=6,
        )
        if not r.ok:
            return []
        j = r.json()
        # Response shape: [query, [titles], [descriptions], [urls]]
        if isinstance(j, list) and len(j) >= 2 and isinstance(j[1], list):
            return [str(t) for t in j[1]]
    except Exception as e:
        logger.debug("wiki opensearch failed for %r: %s", query, e)
    return []


def _wiki_summary_thumb(title: str) -> str:
    """REST page-summary lookup. Returns originalimage preferred,
    thumbnail fallback. Skips disambiguation pages."""
    try:
        slug = urllib.parse.quote(title.replace(' ', '_'), safe='()&')
        r = requests.get(
            _WIKI_SUMMARY_URL + slug,
            headers={'User-Agent': _WIKI_POSTER_UA},
            timeout=6,
        )
        if not r.ok:
            return ''
        d = r.json() or {}
        if d.get('type') == 'disambiguation':
            return ''
        img = d.get('originalimage') or d.get('thumbnail') or {}
        return img.get('source') or ''
    except Exception as e:
        logger.debug("wiki summary failed for %r: %s", title, e)
        return ''


def _wiki_pageimages_thumb(title: str) -> str:
    """MediaWiki pageimages fallback - picks up lead images the REST
    summary endpoint sometimes strips (title cards, free-license
    infobox art)."""
    try:
        r = requests.get(
            _WIKI_PAGEIMAGES_URL,
            params={'action': 'query', 'titles': title, 'prop': 'pageimages',
                    'format': 'json', 'pithumbsize': 500, 'redirects': 1},
            headers={'User-Agent': _WIKI_POSTER_UA},
            timeout=6,
        )
        if not r.ok:
            return ''
        pages = ((r.json() or {}).get('query') or {}).get('pages') or {}
        for _, p in pages.items():
            thumb = (p.get('thumbnail') or {}).get('source') or ''
            if thumb:
                return thumb
    except Exception as e:
        logger.debug("wiki pageimages failed for %r: %s", title, e)
    return ''


# ---------------------------------------------------------------------------
# iTunes Search API poster fallback
#
# When Wikipedia misses (either no article, or article carries a logo
# instead of a real poster like House of the Dragon, or infobox has no
# free-license image like Euphoria's American TV series page), fall
# through to iTunes Search - Apple's public catalog covers ~90% of
# theatrical films and most TV series that sell digitally (which is
# almost everything on the streaming Top-10 rails).
#
# No API key, no signup, no rate limit for light traffic. Poster URL is
# returned as artworkUrl100 which we upgrade to 600x600 by URL rewrite
# (Apple exposes multiple sizes at the same path with just a size token
# swap).
#
# Coverage counter-examples (still miss):
#   - HBO Max / Peacock / Paramount+ exclusives that never sold on iTunes
#     (Life Larry & Pursuit of Unhappiness, Rick & Morty pre-Netflix)
#   - Trailers / sneak peeks (they should have been normalized away by
#     _norm_title_for_poster now)
#   - Non-English titles with romanization mismatches
#
# For those, the frontend film-strip SVG placeholder still fires. That's
# fine - it's the truest "no known art" signal and doesn't misrepresent.
# ---------------------------------------------------------------------------

_ITUNES_SEARCH_URL = 'https://itunes.apple.com/search'


def _itunes_poster_lookup(title: str, kind: str) -> str:
    """iTunes Search API poster lookup. Returns hi-res artwork URL or ''.

    kind='Film' searches `media=movie`; kind='TV' searches
    `media=tvShow`. We deliberately do NOT set the `entity` param -
    Apple's tvSeason entity only surfaces season SKUs sold as digital
    bundles, which excludes most current premium TV. Dropping the
    entity restriction returns tv-episode entries whose
    `collectionName` = the show name, which is what we want here.

    Match strategy: normalized-substring guard against the candidate's
    `collectionName`. For movies we additionally accept `trackName` as
    a fallback since Apple sometimes ships a movie only as a trackName.
    kind mismatch is rejected outright (a tv-episode row for a movie
    query, or a feature-movie row for a TV query).
    """
    q = _norm_title_for_poster(title)
    if not q:
        return ''
    is_film = str(kind or '').strip().lower() in ('film', 'films', 'movie', 'movies')
    media   = 'movie' if is_film else 'tvShow'
    accept_kinds = {'feature-movie'} if is_film else {'tv-episode', 'tv-season'}
    try:
        r = requests.get(
            _ITUNES_SEARCH_URL,
            params={'term': q, 'country': 'US', 'media': media, 'limit': 10},
            headers={'User-Agent': _WIKI_POSTER_UA},
            timeout=6,
        )
        if not r.ok:
            return ''
        results = (r.json() or {}).get('results') or []
    except Exception as e:
        logger.debug("itunes search failed for %r: %s", title, e)
        return ''
    tn = _norm_for_match(q)
    for it in results:
        # Kind guard: skip wrong-kind (audiobook, music, etc.) and
        # cross-kind results.
        row_kind = (it.get('kind') or '').lower()
        if row_kind and row_kind not in accept_kinds:
            continue
        # Substring guard: match the show/movie name field that
        # corresponds to `kind`.
        if is_film:
            candidates = [it.get('trackName') or '', it.get('collectionName') or '']
        else:
            candidates = [it.get('collectionName') or '', it.get('trackName') or '']
        if not any(tn and tn in _norm_for_match(c) for c in candidates):
            continue
        art = (it.get('artworkUrl100')
               or it.get('artworkUrl60')
               or '')
        if not art:
            continue
        # Upgrade the size token. iTunes serves the same asset at
        # 100x100, 300x300, 600x600, 1200x1200, etc. via a path-segment
        # swap; 600 is a reasonable ceiling for streaming thumbnails.
        art = re.sub(r'/\d+x\d+bb\.\w+$', '/600x600bb.jpg', art)
        return art
    return ''


# Disambiguation-tag whitelists on Wikipedia page titles. When a user is
# looking at the streaming Top-10 they expect a movie/TV poster, not a
# novel cover or a hip-hop song. These tags in a page's disambiguation
# suffix cause the candidate to be rejected outright.
_WIKI_KIND_REJECT_TAGS = (
    'novel', 'book', 'song', 'album', 'video game', 'game', 'poem',
    'play', 'musical', 'opera', 'comic', 'manga', 'painting',
    'ballet', 'short story', 'anthology', 'franchise', 'disambiguation',
)


def _candidate_kind_hint(candidate: str) -> str:
    """Extract the disambiguation kind from a Wikipedia page title.

    "It Ends with Us (film)"  -> 'film'
    "The Bear (TV series)"    -> 'tv'
    "Fallout series"          -> 'tv'   (bare " series" suffix)
    "Squid Game"              -> ''      (no disambiguator)
    "It Ends with Us (Colleen Hoover novel)" -> 'novel'
    """
    s = str(candidate or '').lower()
    m = re.search(r'\(([^)]+)\)\s*$', s)
    tag = (m.group(1) if m else '').strip()
    # Bare " series" or " film" with no parens (rare but happens)
    if not tag:
        for suffix in (' tv series', ' tv show', ' film', ' movie', ' series'):
            if s.endswith(suffix):
                tag = suffix.strip()
                break
    if not tag:
        return ''
    if 'tv' in tag or 'television' in tag or tag.endswith('series') or 'show' in tag:
        return 'tv'
    if 'film' in tag or 'movie' in tag or re.match(r'\d{4}\s*film', tag):
        return 'film'
    if any(reject in tag for reject in _WIKI_KIND_REJECT_TAGS):
        return 'reject'
    return 'other'


def _wiki_poster_lookup(title: str, kind: str) -> str:
    """Two-layer poster lookup: Wikipedia first, iTunes Search fallback.

    Wikipedia's infobox art has the best resolution when it exists but
    misses on streaming exclusives and shows whose infobox carries a
    logo instead of a real poster (House of the Dragon, Rick and Morty).
    iTunes Search catches those - Apple's catalog covers ~90% of
    theatrical films and most digital-distribution TV series.

    kind: 'Film' or 'TV' - biases the OpenSearch disambiguation.
    Returns '' on miss. Cached in module scope.

    Wikipedia filter policy per candidate:
      - Substring guard: normalized query must be a substring of
        normalized candidate (rejects "Landman" -> "Lawman").
      - Kind guard: rejects novel/song/album/game/etc disambiguations
        outright. Allows the exact-kind disambiguation (film for films,
        tv for TV) and bare candidates (no disambiguator).
      - Logo reject: Wikipedia often has a logo PNG in the infobox for
        long-running series (House of the Dragon logo, Rick and Morty
        anime logo). Those are downgraded below the iTunes result.
    """
    q = _norm_title_for_poster(title)
    if not q:
        return ''
    is_film = str(kind or '').strip().lower() in ('film', 'films', 'movie', 'movies')
    want_kind = 'film' if is_film else 'tv'
    cache_key = (q.lower(), want_kind)
    if cache_key in _WIKI_POSTER_CACHE:
        return _WIKI_POSTER_CACHE[cache_key]
    suffix = ' film' if is_film else ' TV series'
    # Try suffixed query first (disambiguates "Fallout" -> TV vs game),
    # then raw as fallback, then union the two so we score across a
    # wider pool.
    suffixed = _wiki_opensearch_titles(q + suffix, limit=5)
    bare     = _wiki_opensearch_titles(q,          limit=5)
    seen: set[str] = set()
    candidates: list[str] = []
    for c in suffixed + bare:
        if c not in seen:
            candidates.append(c)
            seen.add(c)
    tn = _norm_for_match(q)

    # Bucket candidates by kind so we can enforce strict fallback rules
    # instead of just ranking. Order of preference:
    #   1. want-kind (film for films, tv for TV) - safest match
    #   2. bare (no disambiguator) - only if NO want-kind candidate
    #      showed up in OpenSearch at all; guards against Wednesday
    #      resolving to the day of the week / Odin painting.
    # Reject-kinds (novel, song, album, video game, etc.) are dropped
    # unconditionally.
    want_bucket: list[str] = []
    bare_bucket: list[str] = []
    for c in candidates:
        k = _candidate_kind_hint(c)
        if k == 'reject':
            continue
        if k == want_kind:
            want_bucket.append(c)
        elif k == '':
            bare_bucket.append(c)
        # 'other' and wrong-kind are ignored - too risky to lift a
        # poster from "The Diplomat" -> "Dipset (hip hop group)" or
        # "Reacher" -> some obscure town.

    # If any want-kind candidate exists, we ONLY try those. This makes
    # Wednesday MISS (better than showing an Odin painting) rather than
    # fall through to the bare "Wednesday" article.
    try_order = want_bucket if want_bucket else bare_bucket

    art = ''
    for cand in try_order[:5]:
        # Substring guard against fuzzy mismatches.
        if tn not in _norm_for_match(cand):
            continue
        art = _wiki_summary_thumb(cand) or _wiki_pageimages_thumb(cand)
        if art:
            # Wikipedia often carries a text-logo PNG in the infobox
            # instead of a real poster (House of the Dragon, Rick and
            # Morty, Squid Game). If the file name contains 'logo',
            # we prefer to try iTunes for a real poster. Keep this
            # candidate as a last-ditch fallback in case iTunes misses.
            if 'logo' not in art.lower():
                break
            wiki_logo_fallback = art
            art = ''
    # Fallback: iTunes Search API. Catches shows with no Wikipedia
    # article, articles missing infobox art, and articles that only
    # have a logo. Streaming-exclusive originals that never sold on
    # iTunes still miss here (correctly) - those fall through to the
    # frontend film-strip placeholder.
    if not art:
        art = _itunes_poster_lookup(title, kind)
    # If iTunes also misses AND Wikipedia had a logo, use the logo -
    # a text-only show logo is better than the film-strip placeholder.
    if not art:
        art = locals().get('wiki_logo_fallback', '') or ''
    _WIKI_POSTER_CACHE[cache_key] = art
    return art


def _enrich_streaming_with_posters(items: list[dict], default_kind: str,
                                    max_workers: int = 8) -> None:
    """Mutate items in place, adding an `image` field via Wikipedia.

    default_kind: 'Film' or 'TV', used when the item doesn't carry a
    category_display.

    Skips items that already have an image. Thread-pools lookups so a
    full six-platform payload (~120 unique titles across films + tv)
    doesn't add 30+ seconds of latency on a cold cache; each lookup is
    ~250-500ms serial. On a warm cache (repeat renders) this is
    effectively free.
    """
    if not items:
        return
    needs: list[tuple[dict, str, str]] = []
    for it in items:
        if it.get('image'):
            continue
        title = it.get('title') or ''
        if not title:
            continue
        kind = it.get('category_display') or default_kind or 'TV'
        needs.append((it, title, kind))
    if not needs:
        return
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_wiki_poster_lookup, title, kind): it
                for (it, title, kind) in needs
            }
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    art = fut.result() or ''
                except Exception:
                    art = ''
                if art:
                    it['image'] = art
    except Exception as e:
        logger.info("streaming poster batch failed: %s", e)


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

        # ESPN+ is a sports platform. Any item that lexically resembles
        # a "film" (misclassified documentary, mislabeled 30-for-30
        # short, etc.) still belongs in the TV rail alongside live
        # sports and studio shows. Force the film split empty so the
        # frontend collapses the platform panel to a single TV column
        # rather than showing an ESPN+ "Film" header at all.
        if slug == 'espnplus':
            tv    = films + tv
            films = []
            for i, r in enumerate(tv, 1):
                r['category_display'] = 'TV'
                r['bucket_rank']      = i

        # Enrich Film + TV rows with an `image` field via iTunes Search.
        # Cached at module scope so subsequent renders (same title, same
        # kind) return instantly. First cold render of a new title
        # costs ~150ms; batched across a full payload it's ~500ms total.
        _enrich_streaming_with_posters(films, 'Film')
        _enrich_streaming_with_posters(tv,    'TV')
        # Also enrich the flat `items` list so any consumer that reads
        # it (drilldown, legacy renderer) gets thumbnails too.
        _enrich_streaming_with_posters(items, 'TV')

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
# Card 5b: FAST channels (Roku, Tubi, Pluto, Amazon)
# ============================================================================
# The four platforms in dashboard sub-tab order. Slug matches
# `fast_channels.FAST_PLATFORMS`; `available_default` is what the panel
# reports before the first daily snapshot lands.
FAST_PLATFORMS = [
    ('roku',    'The Roku Channel', False),
    ('tubi',    'Tubi',             False),
    ('pluto',   'Pluto TV',         False),
    ('amazon',  'Amazon',           False),
]


def _fetch_fast_trending(state: Optional[str], lookback_days: int,
                          keywords: Optional[list[str]] = None,
                          asof: Optional[str] = None) -> dict:
    """Read the daily FAST-channels snapshot and shape it into the same
    per-platform dict the frontend already consumes for streaming.

    The scraper writes a single S3 object
    `trends_iq_snapshots/latest/fast_channels.json` whose `sources`
    key contains one entry per platform (roku / tubi / pluto / amazon),
    each with an `items` list of up to 100 titles (mixed Film + TV) in
    JustWatch popularity order.
    """
    snap = _read_snapshot('fast_channels', asof) if asof else _read_snapshot('fast_channels')
    sources = (snap or {}).get('sources') or {}

    # Channel lineups (per-platform micro-channels: e.g. Waypoint TV,
    # Nick Jr. Pluto TV, Forensic Files 24/7) come from a separate
    # snapshot seeded by scripts/trends_scrapers/build_fast_channel_
    # lineups.py from MediaBiz's weekly Stream Metric Schedules dump.
    # If the snapshot is missing (before the first seed run), the FAST
    # panel just doesn't render the channel strip - everything else
    # works unchanged.
    lineups_snap = _read_snapshot('fast_channel_lineups', asof) if asof \
        else _read_snapshot('fast_channel_lineups')
    channel_sources = (lineups_snap or {}).get('sources') or {}

    result: dict[str, dict] = {}
    for slug, label, _default_avail in FAST_PLATFORMS:
        block = sources.get(slug) or {}
        items = list(block.get('items') or [])
        # Bucket rank stamped by the scraper is already correct;
        # do a defensive re-rank in case an upstream dedupe pass ever
        # touches the list.
        for i, it in enumerate(items, 1):
            it['rank'] = i
        # Split into films / tv the same way streaming does so the
        # frontend can render whichever shape it prefers. For FAST the
        # default view is a single flat top-100, but exposing the
        # split for free keeps the rendering flexible.
        films = [dict(it) for it in items
                  if (it.get('category_display') or '').lower() == 'film']
        tv    = [dict(it) for it in items
                  if (it.get('category_display') or '').lower() == 'tv']
        for i, r in enumerate(films, 1):
            r['bucket_rank'] = i
        for i, r in enumerate(tv, 1):
            r['bucket_rank'] = i

        # Channel lineup for this platform (top-N by weekly airings).
        # Cap at 50 - the frontend renders these as a scrollable strip
        # and 50 covers every channel a viewer would actually see on
        # the FAST grid; the long-tail placeholder channels aren't
        # useful for a dashboard reader.
        lineup_block = channel_sources.get(slug) or {}
        raw_channels = lineup_block.get('channels') or []
        channels_out: list[dict] = []
        for i, ch in enumerate(raw_channels[:50], 1):
            if not isinstance(ch, dict):
                continue
            channels_out.append({
                'rank':         i,
                'name':         ch.get('name') or '',
                'airings':      int(ch.get('airings') or 0),
                'content_type': ch.get('content_type') or '',
            })

        result[slug] = {
            'label':          block.get('label') or label,
            'items':          items,
            'films':          films,
            'tv':             tv,
            'channels':       channels_out,
            'channels_total': len(raw_channels),
            'available':      bool(block.get('available') and items),
            'fetched_at':     (snap or {}).get('fetched_at'),
        }
        if (snap or {}).get('error'):
            result[slug]['note'] = f"latest snapshot: {(snap or {}).get('error')}"
    return result


# ============================================================================
# Card 5c: Trending games (Xbox Game Pass Ultimate + future providers)
# ============================================================================
def _fetch_gaming_trending(state: Optional[str], lookback_days: int,
                             keywords: Optional[list[str]] = None,
                             asof: Optional[str] = None) -> dict:
    """Fan out to every gaming platform's daily snapshot. Same shape as
    `_fetch_streaming_trending` so the frontend can reuse render
    helpers.

    Each platform ships an `items` list of up to 25 games with:
      { rank, title, image, publisher, genre, url, product_id,
        category_display: 'Game', recently_added: bool }
    """
    result: dict[str, dict] = {}
    for slug, label, _default_avail in GAMING_PLATFORMS:
        snap = _read_snapshot(slug, asof) if asof else _read_snapshot(slug)
        if not snap:
            result[slug] = {'label': label, 'items': [], 'available': False}
            continue
        items = _snapshot_items_for_geo(snap, state, keywords=keywords)
        items = items[:25]
        for i, it in enumerate(items, 1):
            it['rank'] = i
        result[slug] = {
            'label':      label,
            'items':      items,
            'available':  bool(items),
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
def list_available_dates(max_days: int = 120) -> list[str]:
    """List UTC dates (YYYY-MM-DD, descending) that have historic data.

    Walks the S3 prefix `trends_iq_snapshots/` for date-shaped
    directories. Only includes dates that have at least one snapshot
    file (i.e. we won't advertise an empty day). `max_days` caps how
    far back we look - the daily archive can grow unbounded, but the
    picker doesn't need years of data.

    Today is always included first regardless of what's on S3; the
    live path serves it out of the `latest/` prefix.
    """
    today  = _today_iso()
    dates: set[str] = {today}
    s3 = _s3_client()
    if s3 is None:
        return sorted(dates, reverse=True)
    try:
        # Delimiter='/' returns CommonPrefixes = the top-level "sub-
        # folders" under `trends_iq_snapshots/`. Each looks like
        # `trends_iq_snapshots/2026-07-30/`. Filter to date-shaped
        # prefixes to skip `latest/`.
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_CACHE_BUCKET,
                                          Prefix='trends_iq_snapshots/',
                                          Delimiter='/'):
            for cp in (page.get('CommonPrefixes') or []):
                pfx = cp.get('Prefix') or ''
                # Extract the segment between the two slashes.
                # 'trends_iq_snapshots/2026-07-30/' -> '2026-07-30'
                parts = pfx.strip('/').split('/')
                if len(parts) != 2:
                    continue
                day = parts[1]
                if len(day) == 10 and day[4] == '-' and day[7] == '-':
                    dates.add(day)
    except Exception as e:
        logger.debug("list_available_dates s3 walk failed: %s", e)
        return sorted(dates, reverse=True)
    # Cap at max_days looking back from today
    try:
        cutoff = (datetime.now(timezone.utc).date()
                   - timedelta(days=max_days)).isoformat()
        dates = {d for d in dates if d >= cutoff}
    except Exception:
        pass
    return sorted(dates, reverse=True)


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

    filters.asof (YYYY-MM-DD, optional): historic-view date. When set
    AND < today, snapshot-based cards read from the dated prefix
    (`trends_iq_snapshots/{asof}/{source}.json`) instead of `latest/`.
    Live-fetch surfaces (searches / headlines / social / streaming
    aggregators / movers) are omitted from historic reconstructions -
    the frontend renders "not available for historic view" in their
    place. The whole payload is cached in S3 forever under a
    date-scoped key so a historic view is a single-read after the
    first user hits it.
    """
    if not force_refresh:
        cached = _cache_get(filters)
        if cached is not None:
            cached['from_cache'] = True
            return cached

    asof     = filters.get('asof') or None
    historic = _is_historic(filters)

    label, state, dma_value = _resolve_geo(filters)
    lookback_days = int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS)
    geo_kws = _geo_keywords(state, dma_value)

    if historic:
        # Historic view: every surface reads from the dated snapshot
        # prefix. Live-fetch aggregators (trending searches / headlines
        # / social / streaming / movers) can't reach into the past
        # cheaply, so we serve the snapshot-based cards for that day
        # and return empty placeholders for the live surfaces. Users
        # know they're in the time machine because the UI stamps a
        # "Viewing YYYY-MM-DD" banner from the payload's `asof` field.
        tasks = {
            'wikipedia_trending':  lambda: _read_snapshot('wikipedia_trending', asof),
            'music_charts':        lambda: _read_snapshot('music_charts',       asof),
            'podcast_charts':      lambda: _read_snapshot('podcast_charts',     asof),
            'book_charts':         lambda: _read_snapshot('book_charts',        asof),
            'film_ticketing':      lambda: _read_snapshot('film_ticketing',     asof),
            'libby_trends':        lambda: _read_snapshot('libby_trends',       asof),
            'philanthropy_news':   lambda: _read_snapshot('philanthropy_news',  asof),
            'business_news':       lambda: _read_snapshot('business_news',      asof),
            'wall_street_news':    lambda: _read_snapshot('wall_street_news',   asof),
            'stream_estimates':    lambda: _read_snapshot('stream_estimates',   asof),
            'lens_scores':         lambda: _read_snapshot('lens_scores',        asof),
            'fast_trending':       lambda: _fetch_fast_trending(state, lookback_days,
                                                                  keywords=geo_kws, asof=asof),
        }
    else:
        tasks = {
            'trending_searches':   lambda: _fetch_trending_searches(state, lookback_days),
            'headlines_pack':      lambda: _fetch_trending_headlines_and_sources(geo_kws),
            # social_trending: dropped 2026-08-20 (Jenna: "kill the
            # scrape too"). Was Reddit + YouTube + TikTok + Instagram +
            # X. Downstream consumers (mine_trending_people, movers
            # buzz-mix, cross-platform moment badge) still receive the
            # kwarg but it's an empty dict so they no-op gracefully.
            'streaming_trending':  lambda: _fetch_streaming_trending(state, lookback_days,
                                                                        keywords=geo_kws),
            'fast_trending':       lambda: _fetch_fast_trending(state, lookback_days,
                                                                   keywords=geo_kws),
            'gaming_trending':     lambda: _fetch_gaming_trending(state, lookback_days,
                                                                     keywords=geo_kws),
            # Products by retailer removed from the dashboard 2026-07-28.
            # Aggregator + scrapers preserved in code so re-enabling is
            # a one-line change - just re-add the task here and the panel
            # in index.html.
            'movers':              lambda: compute_search_movers(state),
            'wikipedia_trending':  lambda: _read_snapshot('wikipedia_trending'),
            'music_charts':        lambda: _read_snapshot('music_charts'),
            'podcast_charts':      lambda: _read_snapshot('podcast_charts'),
            'book_charts':         lambda: _read_snapshot('book_charts'),
            'film_ticketing':      lambda: _read_snapshot('film_ticketing'),
            'libby_trends':        lambda: _read_snapshot('libby_trends'),
            'philanthropy_news':   lambda: _read_snapshot('philanthropy_news'),
            'business_news':       lambda: _read_snapshot('business_news'),
            'wall_street_news':    lambda: _read_snapshot('wall_street_news'),
            'stream_estimates':    lambda: _read_snapshot('stream_estimates'),
            'headline_estimates':  lambda: _read_snapshot('headline_estimates'),
            'lens_scores':         lambda: _read_snapshot('lens_scores'),
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
    # FAST-channel rankings (Roku / Tubi / Pluto / Amazon ad-tier).
    # Same shape as `streaming_trending` (per-platform dict with
    # `items`, `films`, `tv`, `available`) so the frontend can reuse
    # every helper it built for the Streaming panel.
    fast_trending      = results.get('fast_trending') or {}
    gaming_trending    = results.get('gaming_trending') or {}
    # Products tab retired 2026-07-28; keep an empty list bound so the
    # payload contract (`cards.products_by_retailer` present + list-typed)
    # stays intact for any older cached frontend still deployed.
    products           = []
    movers             = results.get('movers') or {'available': False,
                                                     'note': 'warming up'}
    # Wikipedia trending is a straight snapshot read (built daily by
    # scripts/trends_scrapers/wikipedia_trending.py). Missing snapshot
    # -> empty list; the frontend handles the empty-state.
    wiki_snap          = results.get('wikipedia_trending') or {}
    # `national` is the full Wikipedia top-viewed list (people +
    # events + orgs + places). Kept for downstream consumers that
    # want everything. `people` is the US-person-only subset the
    # scraper computes via `_classify_person_row` - what the
    # Trending People card actually renders. Falls back to the raw
    # `national` list on legacy snapshots that predate the classifier.
    wikipedia_trending = list(wiki_snap.get('people')
                                or wiki_snap.get('national')
                                or [])[:30]

    # Music charts (Spotify + Apple Music + Shazam + TikTok + Amazon).
    # Scraper returns {sources: {spotify:{items:...}, apple:{...}, ...}}.
    # Pass the whole sources dict through; frontend renders one card
    # per source.
    music_snap    = results.get('music_charts') or {}
    music_charts  = music_snap.get('sources') or {}

    # Podcast charts (Apple Podcasts primary; Spotify / Amazon / Audible
    # stubbed with operator messaging until cookies land).
    podcast_snap   = results.get('podcast_charts') or {}
    podcast_charts = podcast_snap.get('sources') or {}

    # Book charts (Amazon Best-Sellers + Apple Books; Audible stubbed).
    book_snap    = results.get('book_charts') or {}
    book_charts  = book_snap.get('sources') or {}

    # Film ticketing (Fandango + Cinemark live; AMC / Regal / Atom
    # stubbed with cookie-donation guidance until a signed-in session
    # bypass lands). Runs from Jenna's laptop cron because Hetzner is
    # IP-blocked by every ticketing platform.
    film_snap    = results.get('film_ticketing') or {}
    film_sources = film_snap.get('sources') or {}

    # Stamp Wikipedia posters on any film-ticketing tile that lacks one.
    # AMC in particular never carries a poster: its /movies page keeps
    # posters behind Queue-It, so both the curl_cffi path and the
    # sitemap fallback return items with `image=''`. Fandango/Cinemark/
    # Regal already carry their own poster URLs, so the enricher's
    # skip-when-image-already-present guard keeps them untouched. Runs
    # thread-pooled (max_workers=8) so a full 4-platform enrichment
    # (~100 unique titles) settles in <=5s on a cold cache.
    for _plat, _block in list(film_sources.items()):
        _items = _block.get('items') or []
        if _items:
            _enrich_streaming_with_posters(_items, 'Film')

    # Libby popular for LA County (ebook / audiobook / magazine split).
    libby_snap    = results.get('libby_trends') or {}
    libby_trends  = libby_snap.get('sources') or {}

    # Philanthropy news snapshot -> combined list + per-source split.
    # Frontend picks how to slice; both shapes travel in the payload.
    phil_snap        = results.get('philanthropy_news') or {}
    philanthropy_news = list(phil_snap.get('national') or [])[:40]
    philanthropy_by_source = phil_snap.get('by_source') or {}

    # Business news (NYT Business RSS + WSJ Business via Google News
    # RSS proxy). Same shape as philanthropy_news - a `national` list
    # for the flat "Business" sub-tab and a `by_source` split so the
    # UI can render per-outlet cards if we want that later.
    biz_snap         = results.get('business_news') or {}
    business_news    = list(biz_snap.get('national') or [])[:40]
    business_by_source = biz_snap.get('by_source') or {}

    # Wall Street news (MarketWatch + CNBC Markets + IBD + Seeking
    # Alpha via native RSS; WSJ Markets + Barron's + FT + Bloomberg
    # Markets + Reuters Markets via Google News RSS proxy). Same
    # shape as business_news / philanthropy_news - a `national` list
    # for the flat "Wall Street" sub-tab and a `by_source` split so
    # the UI can render per-outlet cards if we want that later.
    ws_snap          = results.get('wall_street_news') or {}
    wall_street_news = list(ws_snap.get('national') or [])[:50]
    wall_street_by_source = ws_snap.get('by_source') or {}

    # Persona-lens relevance scores.  Daily Claude pass over every
    # visible item scoring 0-100 for each configured lens (MS NOW
    # Reader, Millennials, ...).  Frontend uses this to instantly
    # filter every card when the user selects a lens from the
    # dropdown - no server round-trip needed.  Missing snapshot ->
    # the dropdown just doesn't show the lens options + all items
    # render as normal.
    lens_snap        = results.get('lens_scores') or {}
    lens_config      = list(lens_snap.get('lenses') or [])
    lens_scores_map  = dict(lens_snap.get('items') or {})
    # Per-kind top-50% cutoffs computed at scrape time (see
    # lens_relevance._compute_cutoffs).  Shape:
    #   {lens_id: {kind: min_score_to_show}, ...}
    # Frontend uses these instead of a global threshold so tabs where
    # the persona scores everything low (e.g. MS NOW songs) still
    # filter meaningfully rather than blanking.
    lens_cutoffs     = dict(lens_snap.get('cutoffs') or {})

    # Stamp US audience estimates (weekly listeners / streams / views)
    # + day-over-day direction onto every song / podcast / streaming
    # title row. Estimates come from a daily Claude Sonnet + web_search
    # pass (see scripts/trends_scrapers/stream_estimates.py). Missing
    # snapshot -> rows just don't carry `us_streams` and the frontend
    # renders without the extra chip.
    stream_estimates_snap = results.get('stream_estimates') or {}
    _annotate_music_with_streams(music_charts,       stream_estimates_snap)
    _annotate_podcasts_with_streams(podcast_charts,  stream_estimates_snap)
    _annotate_streaming_with_streams(streaming_trending, stream_estimates_snap)
    # FAST channels: same annotator pattern as streaming but keyed by
    # `fast_film:` / `fast_tv:` so estimates don't collide with paid-
    # SVOD estimates for the same title (see stream_estimates
    # ._collect_fast for the split rationale).
    _annotate_fast_with_streams(fast_trending, stream_estimates_snap)
    # FAST-channel ranker (2026-08-21): attach weekly-US-viewers to
    # every micro-channel inside each FAST platform + re-sort each
    # platform's channel list by views desc so the "Channel Ranker"
    # sub-tab reads top-audience-first. Keyed
    # `fast_channel:<platform>:<norm_name>` per platform (no cross-
    # platform dedup).
    _annotate_fast_channels_with_views(fast_trending, stream_estimates_snap)
    # Gaming: Xbox Game Pass Ultimate rows get a weekly-US-plays
    # estimate. Keyed `game:<norm_title>` in the estimates snapshot.
    _annotate_gaming_with_streams(gaming_trending, stream_estimates_snap)
    # Books: pass BOTH the book_charts sub-dict (amazon/apple/audible)
    # AND the libby_trends sub-dict (ebook/audiobook) - a single item
    # can appear on both, and both share the same `book:<title
    # artist>` estimate key. Wrap each in `{'sources': ...}` so the
    # annotator's dispatch stays consistent.
    _annotate_books_with_streams(
        {'sources': book_charts},
        {'sources': libby_trends},
        stream_estimates_snap,
    )

    # Stamp `us_readers` (daily US-gen-pop reader estimate + DoD trend)
    # onto every headline surface: the flat "Top trending" list, the
    # per-outlet "By news source" lists, and the Philanthropy sub-tab.
    # Estimates come from a daily Claude Sonnet + web_search pass (see
    # scripts/trends_scrapers/headline_estimates.py). Missing snapshot
    # -> rows just don't carry `us_readers` and the frontend renders
    # without the extra chip.
    headline_estimates_snap = results.get('headline_estimates') or {}
    _annotate_headlines_with_readers(
        headlines, articles_by_source, philanthropy_news,
        headline_estimates_snap,
        business_news    = business_news,
        wall_street_news = wall_street_news)

    # Rank the Wall Street sub-tab so the single flat list reads
    # "most-read first" instead of stacking one publisher's block
    # after another (the scraper's `combined.extend(rows)` order).
    # Rows with a `us_readers.us_estimate` sort by that value desc;
    # everything the daily reader-estimate cron hasn't priced yet
    # falls in behind them ordered by publication tier + recency +
    # title so the pill isn't empty while the estimator catches up.
    wall_street_news = _sort_wall_street_by_readership(wall_street_news)

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

    # Stamp `cross_platform=True` on any name/topic trending on 3+
    # platforms in this window (searches + wikipedia + people + each
    # social platform). This lights up the 🔥 "cultural moment" badge
    # in the UI. Must run AFTER trending_people is fully assembled and
    # AFTER wikipedia_trending is unpacked, otherwise items get missed.
    _annotate_cross_platform_moments(
        trending_people, wikipedia_trending, trending_searches, social_trending)

    # Stamp `why` (one-line context) on any row whose normalized name
    # matches an entry in the daily why_trending snapshot. Best-effort:
    # if the snapshot is missing (Claude was skipped, cron hasn't run
    # yet, etc.) every row just goes without a `why` field and the UI
    # renders normally.
    _annotate_with_why(
        trending_people, wikipedia_trending, trending_searches)

    # Same treatment for every mover bucket. Uses a wider fallback
    # ladder (related[0] -> news_articles[0].title -> why_trending
    # lookup) because Rising/Falling rows often don't carry the news
    # headlines that Breakout rows inherit from trendspy.
    _annotate_movers_with_why(movers)

    # Split the trending search pool into the 5-card layout the UI renders:
    # Overall (all rows, scrollable) + Entertainment / Retail / Politics /
    # Finance (top 20 each). Category buckets are computed from the same
    # underlying list so counts add up predictably.
    searches_by_category = _bucket_searches_by_category(trending_searches, per_bucket=100)

    # A category card should NEVER render empty just because Google
    # Trends happened to skew sports+entertainment that day. Fold in
    # matching rows from the three other pools we already have -
    # trending news headlines, GDELT trending people, and Wikipedia
    # trending articles - stamped with `origin` so the UI can label
    # them as "via news / people / wiki" instead of pretending they're
    # Google searches. Only buckets under `_THIN_BUCKET_THRESHOLD` get
    # augmented, so healthy buckets stay pure. See
    # `_augment_thin_buckets_from_pools`.
    searches_by_category = _augment_thin_buckets_from_pools(
        searches_by_category,
        trending_headlines  = headlines,
        trending_people     = trending_people,
        wikipedia_trending  = wikipedia_trending,
        articles_by_source  = articles_by_source,
        movers              = movers,
        philanthropy_news   = philanthropy_news,
        business_news       = business_news,
        wall_street_news    = wall_street_news,
    )

    now = datetime.now(timezone.utc)
    payload = {
        'success':      True,
        'filters': {
            'geo_type':      filters.get('geo_type') or 'National',
            'geo_value':     filters.get('geo_value') or '',
            'geo_label':     label,
            'lookback_days': lookback_days,
            'asof':          asof or _today_iso(),
            'historic':      historic,
        },
        'generated_at': now.isoformat(),
        'stale_until':  (now + timedelta(seconds=CACHE_TTL_S)).isoformat(),
        'cards': {
            'trending_searches':              trending_searches,
            'trending_searches_by_category':  searches_by_category,
            'trending_headlines':             headlines,
            'articles_by_source':             articles_by_source,
            'trending_people':                trending_people,
            'wikipedia_trending':             wikipedia_trending,
            'music_trending':                 music_charts,
            'podcasts_trending':              podcast_charts,
            'books_trending':                 book_charts,
            'films_ticketing':                film_sources,
            'libby_trending':                 libby_trends,
            # `fused_trending` is populated below after the payload
            # dict is built - it needs the full `cards` slice to fuse.
            'fused_trending':                 [],
            'philanthropy_news':              philanthropy_news,
            'philanthropy_news_by_source':    philanthropy_by_source,
            'business_news':                  business_news,
            'business_news_by_source':        business_by_source,
            'wall_street_news':               wall_street_news,
            'wall_street_news_by_source':     wall_street_by_source,
            'lens_config':                    lens_config,
            'lens_scores':                    lens_scores_map,
            'lens_cutoffs':                   lens_cutoffs,
            # social_trending: dropped from the shipped payload 2026-08-20.
            # Reddit / YouTube / TikTok / Instagram / X scrapers are no
            # longer scheduled, so this key would be an empty {} on every
            # request anyway. Removed to stop shipping the dead field.
            'streaming_trending':             streaming_trending,
            'fast_trending':                  fast_trending,
            'gaming_trending':                gaming_trending,
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
            'fast':          sum(len(((fast_trending.get(k) or {}).get('items') or []))
                                  for k in ('roku', 'tubi', 'pluto', 'amazon')),
            'gaming':        sum(len(((gaming_trending.get(k) or {}).get('items') or []))
                                  for k, _l, _a in GAMING_PLATFORMS),
            'music':         sum(len(((music_charts.get(k) or {}).get('items') or []))
                                  for k in ('spotify', 'apple', 'tiktok', 'shazam', 'amazon')),
            'podcasts':      sum(len(((podcast_charts.get(k) or {}).get('items') or []))
                                  for k in ('apple', 'spotify', 'amazon', 'audible')),
            # Libby folds into the Books tab as three sibling cards
            # (Popular eBooks / Audiobooks / Magazines), so its item
            # counts roll into `books` for the tab badge.
            'books':         (sum(len(((book_charts.get(k) or {}).get('items') or []))
                                  for k in ('amazon', 'apple', 'audible', 'spotify')) +
                              sum(len(((libby_trends.get(k) or {}).get('items') or []))
                                  for k in ('ebook', 'audiobook', 'magazine'))),
            'films':         sum(len(((film_sources.get(k) or {}).get('items') or []))
                                  for k in ('fandango', 'cinemark', 'amc', 'regal', 'atom')),
            'philanthropy':  (len(philanthropy_news) +
                              len(searches_by_category.get('philanthropy') or [])),
            'movers':    (len(movers.get('breakout') or []) +
                           len(movers.get('rising')   or []) +
                           len(movers.get('falling')  or []) +
                           len(movers.get('sustained') or [])),
            # `trending` is populated below alongside `fused_trending`.
            'trending':      0,
        },
    }

    # Fused Trending feed - computed after the payload is assembled so
    # it can score every signal in one pass. Populated in-place on both
    # the `cards` and `counts` dicts.
    try:
        fused = _compute_fused_trending(payload['cards'])
    except Exception as e:
        logger.warning("fused trending compute failed: %s", e)
        fused = []
    payload['cards']['fused_trending'] = fused
    payload['counts']['trending']      = len(fused)

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
