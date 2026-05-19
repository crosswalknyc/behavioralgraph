"""
journey_iq.py — Digital Journey IQ backend pipeline.

Given a single target string (brand / movie / show / etc.) and a date range,
this module reconstructs BSFS-style digital journeys:

  1. Resolve target → list of UIDs whose URL or COMMON_NAME mentions the
     target inside the date window (re-using the same predicate semantics
     as Profile IQ / Brand Partnership IQ).

  2. Pull each UID's symmetric journey window —
     [first_mention - lookback_days, last_mention_or_conversion + forward_days]
     — selecting UID, VISIT_TS, URL, COMMON_NAME, DOMAIN, PLATFORM. Capped
     at MAX_EVENTS_PER_UID to keep the worker bounded.

  3. Sessionize each UID's events on a 30-minute inactivity gap.

  4. Classify each event into a step bucket (LANDING / BROWSE / PDP / CART /
     CHECKOUT / DISCOUNTS / LOCATION / FINANCING / WARRANTY / CONVERSION /
     DETOUR) plus an inception channel (SEARCH / DIRECT / SOCIAL / REFERRAL /
     AD). Rules live in STEP_RULES + INCEPTION_RULES so they are trivial to
     extend per industry.

  5. Aggregate into the deck-style view: per-inception-cluster funnel of
     step-to-step transitions, with active-consumer counts and drop-off
     percentages, plus detour destinations, post-non-conversion hosts, and
     top inception keywords.

  6. Hand the aggregated JSON to journey_insights for the Claude prose
     "interesting facts" pass.

  7. Gzip-write the final JSON to
     s3://dashboard-inputs/journey-iq/{username}/{job_id}.json.

The module is intentionally framework-agnostic: it does not import flask.
The Flask route is responsible for spawning run_job() in a heavy-analysis
worker thread and surfacing status via the existing update_job_status()
helper, which is passed in as a callback.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import time
import urllib.parse
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional


# ── Defaults / safety bounds ─────────────────────────────────────────────────

DEFAULT_LOOKBACK_DAYS = 14   # symmetric: 14 days before first mention
DEFAULT_FORWARD_DAYS  = 7    # 7 days after last mention or conversion
MAX_UIDS              = 75_000   # hard cap on cohort size per run
MAX_EVENTS_PER_UID    = 350      # cap per UID; preserves journey shape, halves Python work
SESSION_GAP_MINUTES   = 30
TOP_N_DETOURS         = 8
TOP_N_KEYWORDS        = 25
TOP_N_POST_HOSTS      = 12

# ClickHouse parallelism / safety settings applied to every query in a run.
# max_threads=32 keeps us under the per-user cap on typical CH boxes while
# still scaling the big window-pull. max_memory_usage caps a single runaway
# query so a 10M-UID brand can't OOM the node.
CH_RUN_SETTINGS = {
    'max_execution_time':            1800,
    'max_threads':                   32,
    'max_block_size':                65_536,
    'max_memory_usage':              30_000_000_000,   # 30 GB hard cap
    'join_use_nulls':                0,
    'distributed_aggregation_memory_efficient': 1,
}


# ── S3 layout ────────────────────────────────────────────────────────────────

S3_BUCKET            = os.environ.get('JOURNEY_IQ_S3_BUCKET', 'dashboard-inputs')
S3_PREFIX            = 'journey-iq/'
S3_INDEX_KEY         = 'journey-iq/_index.json'


# ── Conversion patterns (locked decision: auto-detect) ───────────────────────
# A pageview counts as a conversion when (URL host is on the target domain
# OR COMMON_NAME == target) AND the path matches one of these substrings.

CONVERSION_PATTERNS = (
    '/checkout',
    '/thank-you', '/thanks',
    '/confirmation', '/confirmed',
    '/order-complete', '/order-success', '/order-received', '/order/complete',
    '/booking-confirmed', '/booking-success', '/booking/complete',
    '/success',
    '/receipt',
    '/purchase-complete', '/purchase-success',
    '/payment-complete', '/payment-success',
)


# ── Step classification ──────────────────────────────────────────────────────
# Rules are applied in order; first match wins. Each rule is a (label, list of
# path-substring needles). They run against the URL path component (lowercased).
# Inception classification is run separately against the FIRST event of the
# session that contains the first target mention.

STEP_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Highest priority: terminal conversion events.
    ('CONVERSION',     CONVERSION_PATTERNS),
    ('CART',           ('/cart', '/basket', '/bag', '/add-to-cart')),
    ('CHECKOUT',       ('/checkout', '/place-order', '/billing', '/shipping')),
    ('FINANCING',      ('/financing', '/finance', '/payment-plan', '/affirm', '/klarna', '/installments')),
    ('WARRANTY',       ('/warranty', '/protection-plan', '/extended-service')),
    ('LOCATION',       ('/find-a-store', '/find-store', '/locations', '/store-locator', '/dealers', '/locate', '/find-an-installer')),
    ('DISCOUNTS',      ('/coupons', '/coupon', '/deals', '/deal', '/offers', '/offer', '/promotions', '/promo', '/sale', '/clearance', '/rebates')),
    ('PDP',            ('/p/', '/product/', '/products/', '/item/', '/pdp/', '/dp/', '/sku/', '/buy/')),
    ('BROWSE',         ('/c/', '/category/', '/categories/', '/shop/', '/browse/', '/collections/', '/search', '/filter', '/results')),
    ('LANDING',        ('/', '/home', '/index')),  # last on the target-host branch
]

# Inception channels — applied to the FIRST event of the journey's first
# session. Matches by host substring or URL query param. Order matters.

SEARCH_ENGINE_HOSTS = (
    'google.', 'bing.', 'duckduckgo.', 'yahoo.', 'yandex.', 'ecosia.',
    'startpage.', 'brave.com/search', 'search.brave.com',
)

SOCIAL_HOSTS = (
    'facebook.', 'fb.com', 'instagram.', 'twitter.', 'x.com/',
    't.co/', 'tiktok.', 'snapchat.', 'pinterest.', 'reddit.',
    'linkedin.', 'youtube.', 'youtu.be', 'threads.net',
)

AI_AGENT_HOSTS = (
    'chat.openai', 'chatgpt.', 'claude.ai', 'perplexity.', 'gemini.google',
    'copilot.microsoft', 'you.com',
)

# Paid-traffic markers in the URL query string.
PAID_QUERY_KEYS = ('gclid', 'fbclid', 'msclkid', 'ttclid', 'yclid', 'dclid')
PAID_UTM_MEDIUM = ('cpc', 'paid', 'paidsearch', 'paid-search', 'paidsocial', 'paid-social', 'display')


# ─────────────────────────────────────────────────────────────────────────────
# Touchpoint classification (the "marketing surface area" layer).
#
# Each rule is (label, needles). A needle starting with '/' is a path/URL
# substring; otherwise it's a host substring. Channel touchpoints
# (TRAILER, SOCIAL_*, PRESS, REVIEW, …) only count an event when the event
# is_mention=True or matches an extra-keyword (else half the open web would
# look like a "TikTok touch"). Extra-keyword touchpoints (TALENT_MENTION,
# BRAND_PARTNERSHIP, SOUNDTRACK, …) fire purely on the user-supplied
# keyword list — perfect for "Steph Curry / Mercedes / Goat soundtrack"
# style attribution surfaces that aren't a host or path on their own.
# ─────────────────────────────────────────────────────────────────────────────

TOUCHPOINT_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Highest-signal channel surfaces first — first match still scoped to
    # the brand because of the is_mention gate in the classifier.
    ('TRAILER',           ('/trailer', 'trailers.apple.', '/watch?v=', 'youtu.be/')),
    ('REVIEW',            ('rottentomatoes.', 'metacritic.', 'letterboxd.',
                           'imdb.com/title', '/reviews', '/review/')),
    ('PRESS',             ('deadline.com', 'variety.com', 'hollywoodreporter.',
                           'thewrap.', 'indiewire.', 'collider.', 'slashfilm.',
                           'screenrant.', 'gamespot.', 'ign.', 'entertainmentweekly.',
                           'ew.com', 'people.com', 'usmagazine.', 'tmz.')),
    ('TICKETING',         ('fandango.', 'amctheatres.', 'regmovies.',
                           'cinemark.', 'alamo', 'atomtickets.', 'movietickets.',
                           'marcustheatres.', 'harkins.', 'showcasecinemas.')),
    ('SHOWTIME_LOOKUP',   ('/showtimes', '/showtime', '/cinema', '/movies/showtimes',
                           '/find-a-theater', '/locations/movies')),
    ('GOOGLE_REVIEW',     ('google.com/maps', 'google.com/search?', '/reviews?',
                           'maps.google.', 'google.com/local')),
    ('STREAMING',         ('netflix.', 'hulu.', 'max.com', 'disneyplus.',
                           'peacocktv.', 'paramountplus.', 'primevideo.',
                           'appletv.', 'youtube.com/movies')),
    ('DATABASE',          ('imdb.', 'themoviedb.', 'tmdb.', 'boxofficemojo.',
                           'wikipedia.', 'wikidata.')),

    # Social platforms
    ('SOCIAL_TIKTOK',     ('tiktok.',)),
    ('SOCIAL_INSTAGRAM',  ('instagram.',)),
    ('SOCIAL_X',          ('twitter.', 'x.com', 't.co')),
    ('SOCIAL_FACEBOOK',   ('facebook.', 'fb.com')),
    ('SOCIAL_YOUTUBE',    ('youtube.', 'youtu.be')),
    ('SOCIAL_REDDIT',     ('reddit.',)),
    ('SOCIAL_THREADS',    ('threads.net',)),
    ('SOCIAL_PINTEREST',  ('pinterest.',)),
    ('SOCIAL_SNAPCHAT',   ('snapchat.',)),

    # Acquisition surfaces (gated separately on URL query keys; see _classify_touchpoints_for_event)
    ('PAID_AD',           ()),   # populated dynamically when PAID_QUERY_KEYS / utm_medium hits
    ('ORGANIC_SEARCH',    ()),   # populated dynamically when host is a search engine and no paid markers
    ('AI_AGENT',          ('chat.openai', 'chatgpt.', 'claude.ai',
                           'perplexity.', 'gemini.google', 'copilot.microsoft', 'you.com')),

    # Extra-keyword categories — populated by user-supplied keywords below.
    ('TALENT_MENTION',     ()),
    ('CREATOR_INFLUENCER', ()),
    ('BRAND_PARTNERSHIP',  ()),
    ('SOUNDTRACK',         ()),
    ('CUSTOM',             ()),
]

# Touchpoint display order — used by the dashboard.
TOUCHPOINT_DISPLAY_ORDER = [
    'TRAILER', 'SOCIAL_TIKTOK', 'SOCIAL_INSTAGRAM', 'SOCIAL_YOUTUBE',
    'SOCIAL_X', 'SOCIAL_FACEBOOK', 'SOCIAL_REDDIT', 'SOCIAL_THREADS',
    'SOCIAL_PINTEREST', 'SOCIAL_SNAPCHAT',
    'CREATOR_INFLUENCER', 'TALENT_MENTION', 'BRAND_PARTNERSHIP', 'SOUNDTRACK',
    'PRESS', 'REVIEW', 'GOOGLE_REVIEW', 'DATABASE',
    'TICKETING', 'SHOWTIME_LOOKUP', 'STREAMING',
    'PAID_AD', 'ORGANIC_SEARCH', 'AI_AGENT',
    'CUSTOM',
]

# Channel-touchpoint host index — used to skip the rule scan when host is
# nowhere near any rule's needles (saves ~95% of work for off-rule hosts).
_TP_HOST_NEEDLES_FLAT: tuple[str, ...] = tuple(
    n for _, needles in TOUCHPOINT_RULES for n in needles
)


# ─────────────────────────────────────────────────────────────────────────────
# Demographic bucketing (applied after the JOIN to userdata.user_data_sanitized).
# Plain-language bucket labels keep the dashboard human-readable.
# ─────────────────────────────────────────────────────────────────────────────

def _bucket_age(age) -> str:
    if age is None:
        return 'Unknown'
    # The CH column is LowCardinality(String) and is normally PRE-BUCKETED
    # (e.g. "25-34"). Pass through as-is when it doesn't look numeric.
    s = str(age).strip()
    if not s or s.lower() in ('unknown', 'null', 'na'):
        return 'Unknown'
    try:
        a = int(float(s))
    except Exception:
        return s
    if a < 18:    return '<18'
    if a < 25:    return '18-24'
    if a < 35:    return '25-34'
    if a < 45:    return '35-44'
    if a < 55:    return '45-54'
    if a < 65:    return '55-64'
    return '65+'


def _bucket_income(income) -> str:
    # ClickHouse INCOME column is LowCardinality(String) — already bucketed.
    # If it's numeric, coerce.
    if income is None:
        return 'Unknown'
    s = str(income).strip()
    if not s:
        return 'Unknown'
    try:
        n = int(float(s.replace('$', '').replace(',', '')))
        if n < 50_000:   return '<$50K'
        if n < 100_000:  return '$50K-$100K'
        if n < 150_000:  return '$100K-$150K'
        if n < 200_000:  return '$150K-$200K'
        return '$200K+'
    except Exception:
        return s   # pass through pre-bucketed strings


def _bucket_children(children) -> str:
    """CHILDREN field → Family vs Non-family (the cut the client asked for)."""
    if children is None:
        return 'Unknown'
    s = str(children).strip().lower()
    if not s or s in ('unknown', 'null', 'na'):
        return 'Unknown'
    if s in ('y', 'yes', 'true', '1') or s.startswith('y'):
        return 'Family (has children)'
    if s in ('n', 'no', 'false', '0') or s.startswith('n'):
        return 'Non-family (no children)'
    return s.title()


def _bucket_str(v) -> str:
    if v is None:
        return 'Unknown'
    s = str(v).strip()
    return s if s else 'Unknown'


# ─────────────────────────────────────────────────────────────────────────────
# Step labels rendered in the dashboard (in display order). The dashboard
# walks this list left-to-right; any step not present in the data is simply
# omitted from the funnel for that cluster.
# ─────────────────────────────────────────────────────────────────────────────

STEP_DISPLAY_ORDER = [
    'LANDING', 'BROWSE', 'DISCOUNTS', 'PDP', 'LOCATION',
    'FINANCING', 'WARRANTY', 'CART', 'CHECKOUT', 'CONVERSION',
]

INCEPTION_DISPLAY_ORDER = ['SEARCH', 'DIRECT', 'AD', 'SOCIAL', 'REFERRAL', 'AI_AGENT', 'OTHER']

# Multi-axis cluster display order — drives the "Cut by:" dropdown.
CUT_DISPLAY_ORDER = [
    ('inception', 'Inception channel'),
    ('interest',  'Interest (Sports / Movies / Family / …)'),
    ('children',  'Family vs Non-family'),
    ('gender',    'Gender'),
    ('age',       'Age bracket'),
    ('ethnicity', 'Ethnicity'),
    ('income',    'Income'),
    ('education', 'Education'),
    ('marital',   'Marital status'),
]


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_job(
    *,
    job_id: str,
    target: str,
    start_date: str,
    end_date: str,
    project_name: str,
    username: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    forward_days: int = DEFAULT_FORWARD_DAYS,
    extra_conversion_patterns: Optional[list[str]] = None,
    extra_touchpoint_keywords: Optional[str] = None,
    narrow_url_patterns: Optional[Any] = None,
    cohort_mode: str = 'mention',
    conversion_url_patterns: Optional[Any] = None,
    days_before_conversion: int = 90,
    steps_before_conversion: int = 10,
    is_movie: bool = False,
    box_office_millions: float = 0.0,
    avg_ticket_price: float = 15.0,
    progress_cb: Optional[Callable[..., None]] = None,
    s3_client: Any = None,
    ch_connect: Optional[Callable[..., Any]] = None,
) -> dict:
    """Run a full Digital Journey IQ pipeline end-to-end.

    Returns a dict ``{'status': 'completed'|'failed', 's3_key': ..., 'summary': ...}``.

    ``progress_cb`` is invoked with ``(progress: int 0-100, message: str)`` at
    each phase boundary; pass the Flask `update_job_status`-bound lambda from
    the route. ``s3_client`` is a boto3 S3 client (the same one Flask owns).
    ``ch_connect`` is the ClickHouse connection factory (defaults to importing
    `migration.clickhouse_connector.connect_clickhouse`).
    """
    def _p(prog: int, msg: str):
        if progress_cb:
            try:
                progress_cb(progress=prog, message=msg)
            except Exception:
                pass

    started = time.time()
    if ch_connect is None:
        from migration.clickhouse_connector import connect_clickhouse as _connect
        ch_connect = _connect

    # ── Phase 0: validate inputs ─────────────────────────────────────────
    target = (target or '').strip()
    cohort_mode = (cohort_mode or 'mention').strip().lower()
    if cohort_mode not in ('mention', 'conversion'):
        cohort_mode = 'mention'
    conv_url_patterns = _normalize_narrow_patterns(conversion_url_patterns)

    if cohort_mode == 'conversion':
        # In conversion-anchored mode the URL patterns ARE the cohort, so target
        # is optional (used only for downstream "is_mention" tagging). If both
        # are missing, fall back to mention-mode rules.
        if not conv_url_patterns and not target:
            return {'status': 'failed',
                    'error': 'conversion_url_patterns or target is required'}
        if not conv_url_patterns:
            # User picked conversion mode but didn't supply patterns -> degrade
            # to mention mode so we still produce a result.
            cohort_mode = 'mention'
        elif not target:
            # Synthesize a display target from the first URL pattern so the
            # downstream prose/dashboard has SOMETHING to call it.
            seed = conv_url_patterns[0]
            target = re.sub(r'[^a-z0-9]+', ' ', seed.lower()).strip() or 'cohort'
    if not target:
        return {'status': 'failed', 'error': 'target is required'}
    try:
        sd = datetime.strptime(start_date, '%Y-%m-%d').date()
        ed = datetime.strptime(end_date, '%Y-%m-%d').date()
    except Exception:
        return {'status': 'failed', 'error': 'start_date / end_date must be YYYY-MM-DD'}
    if ed < sd:
        return {'status': 'failed', 'error': 'end_date must be >= start_date'}

    lookback_days = max(0, min(int(lookback_days or DEFAULT_LOOKBACK_DAYS), 60))
    forward_days  = max(0, min(int(forward_days  or DEFAULT_FORWARD_DAYS), 60))
    days_before   = max(1, min(int(days_before_conversion or 90), 365))
    steps_before  = max(1, min(int(steps_before_conversion or 10), 50))
    is_movie      = bool(is_movie)
    try:
        box_office_millions = max(0.0, float(box_office_millions or 0.0))
    except Exception:
        box_office_millions = 0.0
    try:
        avg_ticket_price = max(1.0, float(avg_ticket_price or 15.0))
    except Exception:
        avg_ticket_price = 15.0
    conv_patterns = tuple(CONVERSION_PATTERNS) + tuple(
        p.strip().lower() for p in (extra_conversion_patterns or []) if p and p.strip()
    )
    extra_kw_map = parse_extra_touchpoint_keywords(extra_touchpoint_keywords or '')
    narrow_patterns = _normalize_narrow_patterns(narrow_url_patterns)

    _p(2, 'Connecting to ClickHouse...')
    conn = ch_connect(settings=CH_RUN_SETTINGS)
    cur = conn.cursor()

    try:
        # ── Phase 1: resolve cohort → UIDs ───────────────────────────────
        # Two cohort modes:
        #   * 'mention'    — UIDs whose URL/COMMON_NAME contain the target
        #                    keyword (optionally narrowed by URL patterns).
        #                    Anchor per UID = first/last mention timestamp.
        #   * 'conversion' — UIDs who hit one of the conversion URL patterns
        #                    (Fandango, AMC, Sony /checkout, etc.). Anchor
        #                    per UID = first such hit = purchase_ts.
        # multiSearchAny(lower(URL), [...]) routes through the ngrambf_v1
        # skip index defined on lower(URL) (and lower(COMMON_NAME)), which
        # is dramatically faster than ORing N position() calls.
        term_variants = _target_variants(target)
        match_clause = _build_match_clause(term_variants)
        narrow_clause = _build_narrow_url_clause(narrow_patterns)
        conv_clause = _build_narrow_url_clause(conv_url_patterns)
        target_domain_guesses = _guess_target_domains(target)
        # Seed narrowing / conversion patterns as implicit target domains too,
        # so the journey classifier treats those surfaces as LANDING/BROWSE
        # for the brand instead of generic DETOUR events.
        for p in (list(narrow_patterns) + list(conv_url_patterns)):
            d = p.split('/', 1)[0].strip()
            if d and '.' in d:
                target_domain_guesses.add(d)

        cur.execute(f"DROP TABLE IF EXISTS journey_uids_{job_id}")
        # CRITICAL: filter out epoch-zero VISIT_TS rows. clickstream_final
        # has a small fraction of rows whose VISIT_TS came in as NULL and
        # got coerced to 1970-01-01. min(VISIT_TS) on the cohort would
        # then return 1970-01-01, and `toDate(1970-01-01) - lookback_days`
        # unsigned-underflows to year 2149, making Phase 2's date filter
        # impossible and returning 0 events. Dropping epoch rows here costs
        # nothing (it's an AND on a column we're already touching) and
        # protects every downstream window computation.
        if cohort_mode == 'conversion':
            _p(8, f'Resolving conversion cohort across {len(conv_url_patterns)} URL pattern(s)...')
            # purchase_ts = first time the UID hit any conversion URL.
            cur.execute(f"""
                CREATE TEMPORARY TABLE journey_uids_{job_id} AS
                SELECT
                    UID,
                    min(VISIT_TS) AS purchase_ts,
                    min(VISIT_TS) AS first_mention_ts,
                    max(VISIT_TS) AS last_mention_ts
                FROM clickstream.clickstream_final
                WHERE DELIVERED BETWEEN toDate('{sd}') AND toDate('{ed}')
                  AND VISIT_TS > toDateTime('2020-01-01 00:00:00')
                  AND {conv_clause}
                GROUP BY UID
                LIMIT {MAX_UIDS}
            """)
        else:
            if narrow_patterns:
                _p(8, f'Searching clickstream for "{target}" narrowed by {len(narrow_patterns)} URL pattern(s)...')
            else:
                _p(8, f'Searching clickstream for "{target}"...')
            narrow_sql = f"\n                  AND {narrow_clause}" if narrow_clause else ""
            cur.execute(f"""
                CREATE TEMPORARY TABLE journey_uids_{job_id} AS
                SELECT
                    UID,
                    toDateTime(0)        AS purchase_ts,
                    min(VISIT_TS)        AS first_mention_ts,
                    max(VISIT_TS)        AS last_mention_ts
                FROM clickstream.clickstream_final
                WHERE DELIVERED BETWEEN toDate('{sd}') AND toDate('{ed}')
                  AND VISIT_TS > toDateTime('2020-01-01 00:00:00')
                  AND {match_clause}{narrow_sql}
                GROUP BY UID
                LIMIT {MAX_UIDS}
            """)
        cur.execute(f"SELECT count() FROM journey_uids_{job_id}")
        matched_uids = int((cur.fetchone() or [0])[0] or 0)

        # ── Auto-fallback: conversion cohort too small → widen to anyone
        # whose URL matched a narrow_url_pattern (or, if no narrow patterns
        # were supplied, anyone whose URL/COMMON_NAME matched the target).
        cohort_fallback_used = False
        FALLBACK_THRESHOLD = 50
        if cohort_mode == 'conversion' and 0 < matched_uids < FALLBACK_THRESHOLD:
            _p(12, f'Only {matched_uids} converters — widening cohort for visual journey...')
            cur.execute(f"DROP TABLE IF EXISTS journey_uids_{job_id}")
            wide_clause = narrow_clause if narrow_clause else match_clause
            cur.execute(f"""
                CREATE TEMPORARY TABLE journey_uids_{job_id} AS
                SELECT
                    UID,
                    toDateTime(0) AS purchase_ts,
                    min(VISIT_TS) AS first_mention_ts,
                    max(VISIT_TS) AS last_mention_ts
                FROM clickstream.clickstream_final
                WHERE DELIVERED BETWEEN toDate('{sd}') AND toDate('{ed}')
                  AND VISIT_TS > toDateTime('2020-01-01 00:00:00')
                  AND {wide_clause}
                GROUP BY UID
                LIMIT {MAX_UIDS}
            """)
            cur.execute(f"SELECT count() FROM journey_uids_{job_id}")
            wide_uids = int((cur.fetchone() or [0])[0] or 0)
            if wide_uids > matched_uids:
                cohort_fallback_used = True
                matched_uids = wide_uids
                cohort_mode = 'mention'  # downstream window logic follows

        if matched_uids == 0:
            msg = ('No users hit any conversion URL in the date range.'
                   if cohort_mode == 'conversion'
                   else 'No users mentioned the target in the date range.')
            _p(100, msg)
            empty = _empty_summary(target, project_name, start_date, end_date,
                                   lookback_days, forward_days)
            s3_key = _persist(s3_client, empty, project_name, username, job_id)
            empty['s3_key'] = s3_key
            return {'status': 'completed', 's3_key': s3_key, 'summary': empty}

        _p(18, f'Matched {matched_uids:,} users. Computing window bounds...')

        # Compute the GLOBAL date range so ClickHouse can prune partitions
        # before evaluating the per-UID time filter. In conversion mode the
        # window is purely backwards from purchase_ts (+ 1 day forward to
        # capture immediate post-purchase events like the confirmation
        # email click); in mention mode it's the original symmetric window.
        if cohort_mode == 'conversion':
            cur.execute(f"""
                SELECT toDate(min(purchase_ts)) - {days_before},
                       toDate(max(purchase_ts)) + 1
                FROM journey_uids_{job_id}
            """)
        else:
            cur.execute(f"""
                SELECT toDate(min(first_mention_ts)) - {lookback_days},
                       toDate(max(last_mention_ts))  + {forward_days}
                FROM journey_uids_{job_id}
            """)
        d_lo, d_hi = cur.fetchone()

        _p(22, f'Pulling journey events for {matched_uids:,} users ({d_lo} → {d_hi})...')

        # ── Phase 2: pull per-UID journey window ──────────────────────────
        # In conversion mode we walk BACKWARDS from purchase_ts and take the
        # last STEPS_BEFORE × 4 events per UID (4× buffer so sessionization
        # has room to group multi-page visits). In mention mode we keep the
        # original symmetric window centred on first/last mention.
        if cohort_mode == 'conversion':
            evt_cap = max(steps_before * 4, 40)
            cur.execute(f"""
                SELECT cf.UID,
                       toUnixTimestamp64Milli(cf.VISIT_TS) AS ts_ms,
                       cf.URL,
                       cf.COMMON_NAME,
                       cf.DOMAIN
                FROM clickstream.clickstream_final AS cf
                INNER JOIN journey_uids_{job_id} AS u ON cf.UID = u.UID
                WHERE cf.DELIVERED >= toDate('{d_lo}')
                  AND cf.DELIVERED <= toDate('{d_hi}')
                  AND cf.VISIT_TS  >= u.purchase_ts - INTERVAL {days_before} DAY
                  AND cf.VISIT_TS  <= u.purchase_ts + INTERVAL 1 DAY
                  AND length(cf.URL) > 8
                ORDER BY cf.UID ASC,
                         cf.VISIT_TS DESC
                LIMIT {evt_cap} BY cf.UID
            """)
        else:
            # Three speedups vs naive:
            #   1. WHERE DELIVERED BETWEEN d_lo AND d_hi → partition pruning fires.
            #   2. LIMIT N BY cf.UID → native CH "top-N per group" path.
            #   3. Drop BROWSER + PLATFORM → smaller rows over the wire.
            cur.execute(f"""
                SELECT cf.UID,
                       toUnixTimestamp64Milli(cf.VISIT_TS) AS ts_ms,
                       cf.URL,
                       cf.COMMON_NAME,
                       cf.DOMAIN
                FROM clickstream.clickstream_final AS cf
                INNER JOIN journey_uids_{job_id} AS u ON cf.UID = u.UID
                WHERE cf.DELIVERED >= toDate('{d_lo}')
                  AND cf.DELIVERED <= toDate('{d_hi}')
                  AND cf.VISIT_TS  >= u.first_mention_ts - INTERVAL {lookback_days} DAY
                  AND cf.VISIT_TS  <= u.last_mention_ts  + INTERVAL {forward_days}  DAY
                  AND length(cf.URL) > 8
                ORDER BY cf.UID ASC,
                         abs(dateDiff('second', u.first_mention_ts, cf.VISIT_TS)) ASC
                LIMIT {MAX_EVENTS_PER_UID} BY cf.UID
            """)
        raw_rows = cur.fetchall()
        _p(46, f'Pulled {len(raw_rows):,} events. Pulling interest tags...')

        # ── Phase 2.5: Interest tags — join matched UIDs against host_mapping ─
        # Same join shape BG.py uses (LOWER(COMMON_NAME) = LOWER(BRAND));
        # we count CATEGORY hits per UID across the journey-window date range,
        # then assign each UID a primary interest = top category + a list of
        # secondary tags (any category >= 15% of their hits). This is what
        # powers the Sports-fan / Family / Entertainment cuts in the dashboard.
        uid_interests: dict[str, dict] = {}
        try:
            cur.execute(f"""
                SELECT cf.UID,
                       hm.CATEGORY    AS cat,
                       count()        AS hits
                FROM clickstream.clickstream_final AS cf
                INNER JOIN journey_uids_{job_id} AS u  ON cf.UID = u.UID
                INNER JOIN reference.host_mapping AS hm
                       ON lower(cf.COMMON_NAME) = lower(hm.BRAND)
                WHERE cf.DELIVERED >= toDate('{d_lo}')
                  AND cf.DELIVERED <= toDate('{d_hi}')
                  AND hm.CATEGORY != ''
                GROUP BY cf.UID, hm.CATEGORY
            """)
            interest_rows = cur.fetchall() or []
            tmp: dict[str, Counter] = defaultdict(Counter)
            for uid, cat, hits in interest_rows:
                if uid and cat:
                    tmp[uid][cat] += int(hits or 0)
            for uid, counter in tmp.items():
                total_hits = sum(counter.values()) or 1
                top = counter.most_common()
                primary = top[0][0] if top else 'Unknown'
                secondary = [c for c, h in top if (h / total_hits) >= 0.15 and c != primary]
                uid_interests[uid] = {'primary': primary, 'secondary': secondary[:5]}
            _p(54, f'Got interest tags for {len(uid_interests):,} users.')
        except Exception as e:
            print(f"[Journey IQ] interest tags failed (non-fatal): {e}")

        # ── Phase 2.55: per-UID purchase_ts (conversion mode only) ────────
        # Pulled separately (cheap — temp table) so the Python-side
        # path-to-purchase aggregator can window the last K events
        # before each user's purchase. In mention mode this map is empty
        # and the aggregator will fall back to per-UID "last K events".
        uid_purchase_ts: dict[str, int] = {}
        if cohort_mode == 'conversion':
            try:
                cur.execute(f"""
                    SELECT UID, toUnixTimestamp64Milli(purchase_ts) AS ts_ms
                    FROM journey_uids_{job_id}
                """)
                for row in cur.fetchall() or []:
                    uid, ts_ms = row
                    if uid and ts_ms is not None:
                        uid_purchase_ts[uid] = int(ts_ms)
            except Exception as e:
                print(f"[Journey IQ] purchase_ts fetch failed (non-fatal): {e}")

        # ── Phase 2.6: Demographics — join against user_data_sanitized ────
        uid_demo: dict[str, dict] = {}
        try:
            cur.execute(f"""
                SELECT u.UID,
                       d.GENDER, d.AGE, d.ETHNICITY, d.INCOME,
                       d.CHILDREN, d.MARITAL_STATUS, d.EDUCATION
                FROM journey_uids_{job_id} AS u
                LEFT JOIN userdata.user_data_sanitized AS d ON u.UID = d.UID
            """)
            for row in cur.fetchall() or []:
                uid, gender, age, eth, inc, children, marital, edu = row
                if not uid:
                    continue
                uid_demo[uid] = {
                    'gender':    _bucket_str(gender),
                    'age':       _bucket_age(age),
                    'ethnicity': _bucket_str(eth),
                    'income':    _bucket_income(inc),
                    'children':  _bucket_children(children),
                    'marital':   _bucket_str(marital),
                    'education': _bucket_str(edu),
                }
            _p(58, f'Got demographics for {len(uid_demo):,} users.')
        except Exception as e:
            print(f"[Journey IQ] demographics failed (non-fatal): {e}")

        _p(60, 'Building journeys + tagging touchpoints...')

        # ── Phase 3-5: sessionize, classify, aggregate (pure Python) ──────
        target_lc = target.lower()
        per_uid_journeys = _build_per_uid_journeys(
            raw_rows,
            target_lc=target_lc,
            target_variants=term_variants,
            target_domains=target_domain_guesses,
            conv_patterns=conv_patterns,
            extra_kw_map=extra_kw_map,
            uid_interests=uid_interests,
            uid_demo=uid_demo,
            uid_purchase_ts=uid_purchase_ts,
            progress_cb=progress_cb,
        )
        _p(72, 'Aggregating funnel + detours + touchpoints...')
        clusters = _aggregate_clusters(per_uid_journeys)
        kpis = _aggregate_kpis(per_uid_journeys)
        keywords = _aggregate_inception_keywords(per_uid_journeys, top_n=TOP_N_KEYWORDS)
        post_hosts = _aggregate_post_non_conversion_hosts(per_uid_journeys, top_n=TOP_N_POST_HOSTS)
        cuts = _aggregate_cuts(per_uid_journeys)
        touchpoints = _aggregate_touchpoints(per_uid_journeys)
        path_to_purchase = _aggregate_path_to_purchase(
            per_uid_journeys,
            steps=steps_before,
            cohort_mode=cohort_mode,
        )

        # ── Phase 6: Claude prose pass (best-effort, no-op when disabled) ─
        _p(85, 'Mining interesting facts...')
        facts = []
        try:
            from migration.journey_insights import generate_interesting_facts
            facts = generate_interesting_facts(
                target=target,
                project_name=project_name,
                start_date=start_date,
                end_date=end_date,
                kpis=kpis,
                clusters=clusters,
                keywords=keywords,
                post_hosts=post_hosts,
                cuts=cuts,
                touchpoints=touchpoints,
            ) or []
        except Exception as e:
            print(f"[Journey IQ] insights pass failed (non-fatal): {e}")

        # ── Phase 6.5: Movie scaling + Claude synthesis (movie mode only) ─
        # When is_movie=True AND box_office_millions > 0, compute the
        # implied audience and stash both:
        #   * `panel`    — the raw panel observation (always real)
        #   * `modeled`  — Claude's estimate sized to the implied audience
        #   * `scaled`   — panel scaled up to match implied audience
        # The dashboard's view-mode toggle (Panel / Modeled / Scaled / Blended)
        # picks which one to render; `path_to_purchase` etc. at the top
        # level always reflect the "blended" default per
        # `blend_real_and_modeled`.
        modeled_block: Optional[dict] = None
        scaled_block:  Optional[dict] = None
        implied_audience = 0
        scaling_factor = 1.0
        if is_movie and box_office_millions > 0:
            try:
                from migration.journey_iq_synthesize import (
                    compute_implied_audience, compute_scaling_factor,
                    synthesize_movie_journey, synth_to_dashboard_payload,
                    scale_summary_counts,
                )
                _p(88, 'Movie mode: scaling to box office + synthesizing canonical journey...')
                real_converters = int(kpis.get('converted_users') or 0)
                implied_audience = compute_implied_audience(
                    box_office_millions=box_office_millions,
                    ticket_price=avg_ticket_price,
                )
                scaling_factor = compute_scaling_factor(
                    implied_audience=implied_audience,
                    panel_converters=real_converters,
                )

                # Claude synthesis — always generated in movie mode so the
                # dashboard can show "Modeled" side-by-side with "Panel".
                synth = synthesize_movie_journey(
                    target=target,
                    project_name=project_name,
                    start_date=start_date,
                    end_date=end_date,
                    box_office_millions=box_office_millions,
                    ticket_price=avg_ticket_price,
                    extra_touchpoint_keywords=extra_kw_map,
                    panel_converters=real_converters,
                    panel_observed_touchpoints=(touchpoints.get('rows') or [])[:15],
                    panel_top_paths=(path_to_purchase.get('top_paths') or [])[:5],
                    steps=steps_before,
                ) or {}
                if synth:
                    modeled_block = synth_to_dashboard_payload(
                        synth, target_audience=max(implied_audience, 1),
                    )
                    modeled_block['source'] = synth.get('source', 'fallback')
                    modeled_block['notes']  = synth.get('notes', '')
            except Exception as e:
                print(f"[Journey IQ] movie synthesis failed (non-fatal): {e}")

        # ── Phase 7: write to S3 ──────────────────────────────────────────
        _p(95, 'Writing results to S3...')
        summary = {
            'meta': {
                'project_name':   project_name,
                'target':         target,
                'target_variants': term_variants,
                'start_date':     start_date,
                'end_date':       end_date,
                'lookback_days':  lookback_days,
                'forward_days':   forward_days,
                'conversion_patterns': list(conv_patterns),
                'extra_touchpoint_keywords': extra_kw_map,
                'narrow_url_patterns': list(narrow_patterns),
                'cohort_mode':    cohort_mode,
                'cohort_fallback_used': cohort_fallback_used,
                'conversion_url_patterns': list(conv_url_patterns),
                'days_before_conversion': days_before,
                'steps_before_conversion': steps_before,
                'is_movie':       is_movie,
                'box_office_millions': box_office_millions,
                'avg_ticket_price': avg_ticket_price,
                'implied_audience': implied_audience,
                'scaling_factor':   scaling_factor,
                'cut_options':    [{'key': k, 'label': lbl} for k, lbl in CUT_DISPLAY_ORDER],
                'created_by':     username,
                'created_at':     datetime.utcnow().isoformat() + 'Z',
                'duration_sec':   round(time.time() - started, 1),
                'matched_uids':   matched_uids,
                'events_pulled':  len(raw_rows),
                'job_id':         job_id,
            },
            'kpis':        kpis,
            'clusters':    clusters,
            'cuts':        cuts,
            'touchpoints': touchpoints,
            'keywords':    keywords,
            'post_hosts':  post_hosts,
            'path_to_purchase': path_to_purchase,
            'facts':       facts,
        }
        # Attach the modeled + scaled views when movie mode is active. The
        # dashboard reads these from `summary.modeled_view` /
        # `summary.scaled_view`; the legacy top-level keys above always
        # hold the raw panel observation so older dashboards still render.
        if modeled_block is not None:
            summary['modeled_view'] = modeled_block
        if is_movie and box_office_millions > 0 and scaling_factor > 1.0:
            try:
                from migration.journey_iq_synthesize import scale_summary_counts
                scaled_block = scale_summary_counts(summary, scaling_factor)
                summary['scaled_view'] = {
                    'kpis':             scaled_block.get('kpis'),
                    'touchpoints':      scaled_block.get('touchpoints'),
                    'path_to_purchase': scaled_block.get('path_to_purchase'),
                    'cuts':             scaled_block.get('cuts'),
                    'clusters':         scaled_block.get('clusters'),
                    'keywords':         scaled_block.get('keywords'),
                    'post_hosts':       scaled_block.get('post_hosts'),
                    'scaling_factor':   scaling_factor,
                    'implied_audience': implied_audience,
                }
            except Exception as e:
                print(f"[Journey IQ] scaling failed (non-fatal): {e}")
        s3_key = _persist(s3_client, summary, project_name, username, job_id)
        summary['s3_key'] = s3_key
        _p(100, 'Digital Journey IQ complete.')
        return {'status': 'completed', 's3_key': s3_key, 'summary': summary}

    finally:
        try:
            cur.execute(f"DROP TABLE IF EXISTS journey_uids_{job_id}")
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Target / brand resolution
# ─────────────────────────────────────────────────────────────────────────────

def _target_variants(target: str) -> list[str]:
    """Generate match variants for the target string.

    Mirrors the BPIQ variant logic so users typing "Door Dash" still match
    `doordash.com`. Always lowercased, deduped, and each variant >= 2 chars.
    """
    base = (target or '').lower().strip()
    if not base:
        return []
    out = {base}
    collapsed = ' '.join(base.split())
    if collapsed:
        out.add(collapsed)
        out.add(collapsed.replace(' ', ''))
        out.add(collapsed.replace(' ', '-'))
        out.add(collapsed.replace(' ', '_'))
    return [v for v in sorted(out, key=len, reverse=True) if len(v) >= 2]


def _build_match_clause(variants: list[str]) -> str:
    """Single multiSearchAny(lower(...), [...]) per column.

    Routes through the ngrambf_v1 skip indexes on lower(URL) and
    lower(COMMON_NAME) (see migration/clickhouse_setup.sql) in one index
    probe per column, instead of N OR'd position() calls. For a target
    that expands to 4 variants this turns ~8 substring evaluations per
    row into 2 index lookups per granule.
    """
    if not variants:
        return '1=0'
    seen: set[str] = set()
    needles: list[str] = []
    for v in variants:
        s = (v or '').lower().strip()
        if not s or s in seen:
            continue
        seen.add(s)
        needles.append("'" + s.replace("'", "''") + "'")
    if not needles:
        return '1=0'
    arr = '[' + ','.join(needles) + ']'
    return (f"(multiSearchAny(lower(URL), {arr}) "
            f"OR multiSearchAny(lower(COMMON_NAME), {arr}))")


def _normalize_narrow_patterns(raw: Optional[Any]) -> list[str]:
    """Accept either a list/tuple or a free-text blob (newline/comma split).
    Returns a deduped lowercase list of URL substrings, each >= 3 chars.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        # Split on newlines AND commas — clients paste either way.
        parts = re.split(r'[\n,]+', raw)
    else:
        try:
            parts = list(raw)
        except Exception:
            return []
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if not isinstance(p, str):
            continue
        s = p.strip().lower()
        # Strip surrounding quotes and protocol noise to be friendly.
        s = s.strip('"\'')
        s = re.sub(r'^https?://', '', s)
        if len(s) < 3 or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _build_narrow_url_clause(patterns: list[str]) -> str:
    """ANDed URL-only filter. Same ngrambf index path as _build_match_clause,
    but URL-only (the caller said the target's mention may not live in
    COMMON_NAME but always somewhere in the URL).

    Returns an SQL fragment like ``multiSearchAny(lower(URL), [...])`` or
    empty string when no patterns supplied (caller must skip the AND).
    """
    if not patterns:
        return ''
    needles = ["'" + p.replace("'", "''") + "'" for p in patterns]
    arr = '[' + ','.join(needles) + ']'
    return f"multiSearchAny(lower(URL), {arr})"


def _guess_target_domains(target: str) -> set[str]:
    """Heuristic domain set for the target. Used to classify which events
    are on the target's own site (LANDING / BROWSE / PDP / etc.) vs DETOUR.

    Most brand names map cleanly to ``<brand>.com``; for movies/people this
    will return only weak guesses (the COMMON_NAME match still picks them
    up via the brand-mention path).
    """
    base = re.sub(r'[^a-z0-9]+', '', (target or '').lower())
    if not base or len(base) < 3:
        return set()
    return {
        f'{base}.com',
        f'{base}.net',
        f'{base}.io',
        f'www.{base}.com',
        f'shop.{base}.com',
        f'store.{base}.com',
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-UID journey reconstruction
# ─────────────────────────────────────────────────────────────────────────────

def _build_per_uid_journeys(
    raw_rows: Iterable[tuple],
    *,
    target_lc: str,
    target_variants: list[str],
    target_domains: set[str],
    conv_patterns: tuple,
    extra_kw_map: Optional[dict[str, list[str]]] = None,
    uid_interests: Optional[dict[str, dict]] = None,
    uid_demo: Optional[dict[str, dict]] = None,
    uid_purchase_ts: Optional[dict[str, int]] = None,
    progress_cb: Optional[Callable[..., None]] = None,
) -> list[dict]:
    """Group rows by UID, sessionize on a 30-min gap, classify each event.

    Returns a list of journey dicts. Each per-event entry is intentionally
    slim — (ts_ms, host, step, is_mention, on_target) plus an optional
    `_url` on the FIRST event of each session (needed by the inception
    classifier). Stripping URL/COMMON_NAME/PLATFORM from every event
    cuts memory by ~6× on a 20M-event run.

    Touchpoint set + first-seen timestamps are accumulated per UID so the
    aggregator can compute reach %, cadence, lift, and co-occurrence in a
    single linear pass — no second walk per touchpoint.
    """
    extra_kw_map = extra_kw_map or {}
    uid_interests = uid_interests or {}
    uid_demo = uid_demo or {}
    uid_purchase_ts = uid_purchase_ts or {}
    by_uid: dict[str, list[tuple]] = defaultdict(list)
    for row in raw_rows:
        # Row shape after the Phase 2 optimization: (uid, ts_ms, url,
        # common_name, domain). BROWSER + PLATFORM removed.
        try:
            uid, ts_ms, url, common_name, domain = row[:5]
        except Exception:
            continue
        if not uid or not ts_ms:
            continue
        by_uid[uid].append((int(ts_ms), url or '', common_name or '', domain or ''))

    out: list[dict] = []
    gap_ms = SESSION_GAP_MINUTES * 60 * 1000
    n_uids = len(by_uid)
    progress_every = max(1, n_uids // 20)  # ~5% steps

    for idx, (uid, events) in enumerate(by_uid.items()):
        events.sort(key=lambda e: e[0])
        # Sessionize + classify in a single pass to avoid building an
        # intermediate event list per UID.
        sessions: list[list[dict]] = []
        cur_session: list[dict] = []
        prev_ts: Optional[int] = None

        first_on_target_ts: Optional[int] = None
        first_any_mention_ts: Optional[int] = None
        conversion_ts: Optional[int] = None
        step_set: set[str] = set()
        detour_hosts: set[str] = set()
        post_mention_hosts: set[str] = set()  # filled after we know first_mention
        # Touchpoint bookkeeping: which categories did this UID hit, when
        # FIRST, and how many TOTAL touches.
        touchpoint_set: set[str] = set()
        touchpoint_first_ts: dict[str, int] = {}
        touchpoint_counts: dict[str, int] = defaultdict(int)

        for ts_ms, url, cn, domain in events:
            if prev_ts is not None and (ts_ms - prev_ts) > gap_ms:
                if cur_session:
                    sessions.append(cur_session)
                cur_session = []
            classified = _classify_event_fast(
                url=url, common_name=cn, domain=domain,
                target_lc=target_lc, target_variants=target_variants,
                target_domains=target_domains, conv_patterns=conv_patterns,
            )
            host = classified['host']
            step = classified['step']
            is_mention = classified['is_mention']
            on_target = classified['on_target']

            ev = {
                'ts_ms':      ts_ms,
                'host':       host,
                'step':       step,
                'is_mention': is_mention,
                'on_target':  on_target,
            }
            # URL is only needed by _classify_inception on the seed event.
            # Stash it on the FIRST event of each session so we have it
            # available without keeping URL on every event in memory.
            if not cur_session:
                ev['_url'] = url
            cur_session.append(ev)

            if is_mention and first_any_mention_ts is None:
                first_any_mention_ts = ts_ms
            if on_target and first_on_target_ts is None:
                first_on_target_ts = ts_ms
            if step == 'CONVERSION' and on_target:
                if conversion_ts is None or ts_ms < conversion_ts:
                    conversion_ts = ts_ms
            step_set.add(step)

            # Touchpoint tagging: cheap when neither is_mention nor any
            # extra-keyword is present, since _classify_touchpoints_for_event
            # short-circuits immediately.
            tps = _classify_touchpoints_for_event(
                url_lc=url.lower() if url else '',
                cn_lc=cn.lower() if cn else '',
                host=host,
                is_mention=is_mention,
                extra_kw_map=extra_kw_map,
            )
            if tps:
                # Sorted list (not set) keeps the JSON shape stable across
                # runs and is small enough that memory cost is negligible.
                ev['touchpoints'] = sorted(tps)
                for tp in tps:
                    touchpoint_counts[tp] += 1
                    if tp not in touchpoint_first_ts:
                        touchpoint_first_ts[tp] = ts_ms
                touchpoint_set |= tps

            prev_ts = ts_ms

        if cur_session:
            sessions.append(cur_session)

        # Prefer on-target events as the journey anchor. A Google search like
        # `?q=firestone+tires` would otherwise spuriously satisfy `is_mention`
        # and pull the timeline anchor BACK to the search itself — but we
        # want the search to be the inception, not the mention.
        first_mention_ts = first_on_target_ts or first_any_mention_ts
        # Conversion-mode fallback: if a UID was cohorted by ticketing-page
        # visit but never independently "mentioned" the brand (the URL had
        # the slug; COMMON_NAME / target keyword may not appear elsewhere),
        # use the cohort purchase_ts as the anchor so we still keep them.
        if first_mention_ts is None:
            cp = uid_purchase_ts.get(uid)
            if cp is not None:
                first_mention_ts = cp
            else:
                continue

        # Post-mention detour hosts: now that we know first_mention_ts, walk
        # once to capture detour/search hosts seen AFTER the brand was
        # touched. Pre-computing here means _aggregate_detours_for_cluster
        # never has to re-walk sessions per cluster.
        last_mention_ts = first_mention_ts
        for sess in sessions:
            for ev in sess:
                if ev['is_mention']:
                    last_mention_ts = max(last_mention_ts, ev['ts_ms'])
                if ev['ts_ms'] >= first_mention_ts:
                    if ev['step'] in ('DETOUR', 'SEARCH') and ev['host']:
                        detour_hosts.add(ev['host'])

        # Post-non-conversion hosts: hosts visited AFTER the last target
        # mention. We pre-compute the set; whether to use it is decided by
        # `_aggregate_post_non_conversion_hosts` based on converted flag.
        for sess in sessions:
            for ev in sess:
                if ev['ts_ms'] > last_mention_ts and ev['host']:
                    post_mention_hosts.add(ev['host'])

        # Inception: first event of the session containing first_mention_ts.
        inception = 'OTHER'
        inception_keyword: Optional[str] = None
        for sess in sessions:
            if not sess:
                continue
            if sess[0]['ts_ms'] <= first_mention_ts <= sess[-1]['ts_ms']:
                first_ev = sess[0]
                inception, inception_keyword = _classify_inception_fast(
                    host=first_ev['host'], url=first_ev.get('_url', ''),
                    on_target=first_ev['on_target'],
                )
                break

        last_ts = sessions[-1][-1]['ts_ms'] if sessions else first_mention_ts
        journey_duration_sec = max(0, (last_ts - first_mention_ts) // 1000)

        # Compute sessions-before-conversion once so KPI rollup is O(UIDs).
        sessions_to_convert = 0
        if conversion_ts is not None:
            for sess in sessions:
                sessions_to_convert += 1
                if any(ev['ts_ms'] == conversion_ts for ev in sess):
                    break

        interest = uid_interests.get(uid) or {}
        demo = uid_demo.get(uid) or {}
        # If the cohort was defined by a known conversion URL (conversion
        # mode), promote that purchase_ts to be the canonical conversion
        # anchor — overriding any heuristic /checkout match. This makes
        # downstream "converted" / cadence / path-to-purchase metrics use
        # the AUTHORITATIVE purchase moment instead of a guess.
        cohort_purchase_ts = uid_purchase_ts.get(uid)
        if cohort_purchase_ts is not None:
            conversion_ts = cohort_purchase_ts
        out.append({
            'uid':                  uid,
            'sessions':             sessions,
            'first_mention_ts':     first_mention_ts,
            'last_mention_ts':      last_mention_ts,
            'conversion_ts':        conversion_ts,
            'converted':            conversion_ts is not None,
            'cohort_purchase_ts':   cohort_purchase_ts,
            'inception':            inception,
            'inception_keyword':    inception_keyword,
            'journey_duration_sec': journey_duration_sec,
            'event_count':          sum(len(s) for s in sessions),
            'session_count':        len(sessions),
            # Pre-computed sets that aggregation functions can sum directly,
            # avoiding a second walk of every session per cluster.
            'step_set':             step_set,
            'detour_hosts':         detour_hosts,
            'post_mention_hosts':   post_mention_hosts,
            'sessions_to_convert':  sessions_to_convert,
            # Touchpoint + cohort tags for the new cuts and touchpoint panel.
            'touchpoint_set':       touchpoint_set,
            'touchpoint_first_ts':  dict(touchpoint_first_ts),
            'touchpoint_counts':    dict(touchpoint_counts),
            'interest_primary':     interest.get('primary', 'Unknown'),
            'interest_secondary':   interest.get('secondary', []),
            'demo':                 demo,
        })

        if progress_cb and (idx % progress_every == 0):
            try:
                pct = 50 + int(15 * (idx + 1) / max(1, n_uids))  # 50→65
                progress_cb(progress=pct,
                            message=f'Classifying journeys ({idx+1:,}/{n_uids:,})...')
            except Exception:
                pass
    return out


def _classify_event_fast(
    *,
    url: str,
    common_name: str,
    domain: str,
    target_lc: str,
    target_variants: list[str],
    target_domains: set[str],
    conv_patterns: tuple,
) -> dict:
    """Hot-path event classifier. Optimizations vs the original:

      * Prefer the DOMAIN column (already lower-cardinality, no URL parse)
        instead of urllib.parse.urlsplit on every row.
      * Only extract `path` when the event is on-target (needs STEP_RULES
        matching). Off-target events are classified as DETOUR/SEARCH using
        host alone, so they never pay the parse cost.
      * is_mention short-circuits on first variant hit.
    """
    url_lc = url.lower() if url else ''
    cn_lc = common_name.lower() if common_name else ''
    dom_lc = domain.lower() if domain else ''

    # Host: prefer the canonical DOMAIN column. Fall back to a cheap
    # netloc extract (split on '//' then on '/') if DOMAIN is empty.
    host = dom_lc
    if not host and url_lc:
        try:
            tail = url_lc.split('://', 1)[-1]
            slash = tail.find('/')
            host = tail[:slash] if slash >= 0 else tail
        except Exception:
            host = ''

    # is_mention: short-circuit on first variant.
    is_mention = False
    if target_variants:
        for v in target_variants:
            if v in url_lc or v in cn_lc:
                is_mention = True
                break

    on_target = False
    if cn_lc and cn_lc == target_lc:
        on_target = True
    elif host and target_domains:
        for d in target_domains:
            if d in host:
                on_target = True
                break

    if on_target:
        # Only on-target events need path parsing — STEP_RULES are all
        # target-site path matchers (CONVERSION / PDP / CART / etc.). Pull
        # path with cheap string ops, no urllib.
        path = '/'
        if url_lc:
            tail = url_lc.split('://', 1)[-1]
            slash = tail.find('/')
            if slash >= 0:
                path = tail[slash:]
                q = path.find('?')
                if q >= 0:
                    path = path[:q]
        # Conversion first (overrides CHECKOUT / etc.).
        step = 'DETOUR'
        matched = False
        for p in conv_patterns:
            if p in path:
                step = 'CONVERSION'
                matched = True
                break
        if not matched:
            step = _classify_target_step(path)
    elif host and _host_is_search_engine(host):
        step = 'SEARCH'
    else:
        step = 'DETOUR'

    return {
        'host':       host,
        'is_mention': is_mention,
        'on_target':  on_target,
        'step':       step,
    }


def _classify_target_step(path: str) -> str:
    """Map a target-host path to a step bucket. Order matters."""
    p = path or '/'
    for label, needles in STEP_RULES:
        if label == 'CONVERSION':
            continue  # handled by caller
        for needle in needles:
            if needle == '/' and p in ('/', '/home', '/index', ''):
                return 'LANDING'
            if needle in p:
                return label
    return 'LANDING'


def _host_is_search_engine(host: str) -> bool:
    if not host:
        return False
    for h in SEARCH_ENGINE_HOSTS:
        if h in host:
            return True
    return False


def _classify_inception_fast(*, host: str, url: str, on_target: bool
                             ) -> tuple[str, Optional[str]]:
    """Inception classifier — only called once per UID (the seed event).

    Cheap full urllib parse is fine here because of the per-UID frequency.
    """
    host = (host or '').lower()
    url_lc = (url or '').lower()
    params: dict = {}
    if '?' in url_lc:
        try:
            qs = urllib.parse.urlsplit(url_lc).query
            params = urllib.parse.parse_qs(qs)
        except Exception:
            params = {}

    if any(k in params for k in PAID_QUERY_KEYS):
        return 'AD', _extract_search_keyword(params)
    if params.get('utm_medium') and any(
        m in (params['utm_medium'][0] or '').lower() for m in PAID_UTM_MEDIUM
    ):
        return 'AD', _extract_search_keyword(params)

    if _host_is_search_engine(host):
        return 'SEARCH', _extract_search_keyword(params)
    if host and any(h in host for h in AI_AGENT_HOSTS):
        return 'AI_AGENT', None
    if host and any(h in host for h in SOCIAL_HOSTS):
        return 'SOCIAL', None
    if on_target:
        return 'DIRECT', None
    if host:
        return 'REFERRAL', None
    return 'OTHER', None


def _extract_search_keyword(params: dict) -> Optional[str]:
    """Extract the user's search query from common engine query strings."""
    for key in ('q', 'query', 'p', 'wd', 'k', 'text'):
        vals = params.get(key)
        if vals and vals[0]:
            kw = vals[0].strip().lower()
            if kw and len(kw) <= 80:
                return kw
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Touchpoint classification — runs per event, returns a set of labels.
# Cheap: short-circuits when no candidate needle is anywhere in URL/host.
# ─────────────────────────────────────────────────────────────────────────────

def parse_extra_touchpoint_keywords(text: str) -> dict[str, list[str]]:
    """Parse the form's free-text into ``{CATEGORY: [kw1, kw2, ...]}``.

    Input format (one rule per line) is intentionally forgiving::

        TALENT=steph curry, michael jordan
        BRAND_PARTNERSHIP=mercedes
        SOUNDTRACK=the goat soundtrack
        CREATOR_INFLUENCER=mrbeast, sneako

    Bare lines without ``CATEGORY=`` are filed under ``CUSTOM``. Categories
    are upper-cased and clamped to the touchpoint label set so a typo
    can't create a phantom column in the dashboard.
    """
    out: dict[str, list[str]] = defaultdict(list)
    allowed = {label for label, _ in TOUCHPOINT_RULES}
    # Permissive aliases — user-friendly → canonical label.
    alias = {
        'TALENT':           'TALENT_MENTION',
        'INFLUENCER':       'CREATOR_INFLUENCER',
        'CREATOR':          'CREATOR_INFLUENCER',
        'PARTNERSHIP':      'BRAND_PARTNERSHIP',
        'PARTNER':          'BRAND_PARTNERSHIP',
        'MUSIC':            'SOUNDTRACK',
    }
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            cat, _, kws = line.partition('=')
            cat = cat.strip().upper()
            cat = alias.get(cat, cat)
            if cat not in allowed:
                cat = 'CUSTOM'
        else:
            cat, kws = 'CUSTOM', line
        for kw in kws.split(','):
            k = kw.strip().lower()
            if not k or len(k) < 2:
                continue
            # Expand into the variants a URL is likely to use: space → hyphen
            # / underscore / collapsed. Without this, "steph curry" misses
            # "/steph-curry-the-goat" — and that's exactly the attribution
            # surface the analyst is trying to tag.
            variants = {k}
            if ' ' in k:
                collapsed = ' '.join(k.split())
                variants.update({
                    collapsed,
                    collapsed.replace(' ', '-'),
                    collapsed.replace(' ', '_'),
                    collapsed.replace(' ', ''),
                })
            for v in variants:
                if v and v not in out[cat]:
                    out[cat].append(v)
    return dict(out)


def _classify_touchpoints_for_event(
    *,
    url_lc: str,
    cn_lc: str,
    host: str,
    is_mention: bool,
    extra_kw_map: dict[str, list[str]],
    params: Optional[dict] = None,
) -> set[str]:
    """Return the set of touchpoint labels this event hits.

    Channel touchpoints (TRAILER, SOCIAL_*, PRESS, …) only count when the
    event already references the target (``is_mention``) OR matches one of
    the user-supplied extra keywords — otherwise an unrelated TikTok visit
    in the journey window would falsely inflate "social touches".
    Extra-keyword touchpoints (TALENT_MENTION, BRAND_PARTNERSHIP, …) fire
    on the keyword alone, since those are explicitly named by the analyst.
    """
    tps: set[str] = set()

    # ── 1. Extra-keyword categories — fire on keyword alone ──────────────
    extra_kw_hit = False
    if extra_kw_map:
        text = url_lc + ' ' + cn_lc
        for cat, kws in extra_kw_map.items():
            for kw in kws:
                if kw in text:
                    tps.add(cat)
                    extra_kw_hit = True
                    break

    # Channel touchpoints require the event to be ABOUT the target.
    relevant = is_mention or extra_kw_hit
    if not relevant:
        return tps

    # ── 2. Channel touchpoints — host / path needle scan ─────────────────
    # Cheap pre-filter: any rule needle present anywhere?
    # (skip the rule loop for the bulk of off-target hosts).
    if not any(n in url_lc or (n and n in host) for n in _TP_HOST_NEEDLES_FLAT):
        # Still allow paid/organic acquisition tagging below.
        pass
    else:
        for label, needles in TOUCHPOINT_RULES:
            if not needles:
                continue
            for n in needles:
                if n.startswith('/'):
                    if n in url_lc:
                        tps.add(label)
                        break
                else:
                    if n and (n in host or n in url_lc):
                        tps.add(label)
                        break

    # ── 3. PAID_AD / ORGANIC_SEARCH dynamic tagging ──────────────────────
    if params is None and '?' in url_lc:
        try:
            qs = urllib.parse.urlsplit(url_lc).query
            params = urllib.parse.parse_qs(qs)
        except Exception:
            params = {}
    paid_hit = bool(params) and (
        any(k in params for k in PAID_QUERY_KEYS) or
        (params.get('utm_medium') and any(
            m in (params['utm_medium'][0] or '').lower() for m in PAID_UTM_MEDIUM))
    )
    if paid_hit:
        tps.add('PAID_AD')
    elif host and _host_is_search_engine(host):
        tps.add('ORGANIC_SEARCH')

    return tps


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation: clusters, funnel edges, drop-off, detours
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_clusters(per_uid: list[dict]) -> list[dict]:
    """Produce one cluster per inception channel. Each cluster holds:
        - users (count, % of total)
        - converted (count, conversion %)
        - funnel: ordered list of {step, active_users, drop_off_pct,
            avg_time_to_next_sec}
        - detours: top destinations after a non-conversion drop-off
    """
    total = max(1, len(per_uid))
    by_inc: dict[str, list[dict]] = defaultdict(list)
    for j in per_uid:
        by_inc[j['inception']].append(j)
    # Always also emit an "ALL" cluster so the dashboard has a default view.
    by_inc['ALL'] = list(per_uid)

    cluster_order = ['ALL'] + INCEPTION_DISPLAY_ORDER
    clusters: list[dict] = []
    for inc in cluster_order:
        members = by_inc.get(inc) or []
        if not members:
            continue
        converted = sum(1 for j in members if j['converted'])
        funnel = _build_funnel(members)
        detours = _aggregate_detours_for_cluster(members)
        clusters.append({
            'inception':       inc,
            'users':           len(members),
            'users_pct':       round(100.0 * len(members) / total, 1),
            'converted':       converted,
            'conversion_pct': round(100.0 * converted / max(1, len(members)), 1),
            'funnel':          funnel,
            'detours':         detours,
        })
    return clusters


def _build_funnel(cluster_members: list[dict]) -> list[dict]:
    """For each step in STEP_DISPLAY_ORDER, count how many UIDs reached it
    AT LEAST ONCE. Uses the per-UID `step_set` pre-computed in
    _build_per_uid_journeys, so we never re-walk sessions per cluster.
    """
    reach: dict[str, int] = {step: 0 for step in STEP_DISPLAY_ORDER}
    for j in cluster_members:
        sset = j.get('step_set') or ()
        for s in sset:
            if s in reach:
                reach[s] += 1

    funnel: list[dict] = []
    prev_count: Optional[int] = None
    for step in STEP_DISPLAY_ORDER:
        cnt = reach[step]
        if cnt == 0 and step not in ('LANDING', 'CONVERSION'):
            continue
        drop_off_pct = None
        if prev_count is not None and prev_count > 0:
            drop_off_pct = round(100.0 * max(0, prev_count - cnt) / prev_count, 1)
        funnel.append({
            'step':         step,
            'active_users': cnt,
            'drop_off_pct': drop_off_pct,
        })
        prev_count = cnt
    return funnel


def _aggregate_detours_for_cluster(cluster_members: list[dict]) -> list[dict]:
    """Top detour hosts visited after first mention. Uses the per-UID
    `detour_hosts` set pre-computed once during journey construction."""
    host_counts: Counter = Counter()
    for j in cluster_members:
        for h in (j.get('detour_hosts') or ()):
            host_counts[h] += 1
    top = host_counts.most_common(TOP_N_DETOURS)
    n_members = max(1, len(cluster_members))
    return [
        {'host': h, 'users': c, 'users_pct': round(100.0 * c / n_members, 1)}
        for h, c in top
    ]


def _aggregate_kpis(per_uid: list[dict]) -> dict:
    """Roll-up KPIs across the entire cohort (mirrors BSFS deck headers)."""
    n = len(per_uid)
    if n == 0:
        return {'total_users': 0, 'converted_users': 0, 'conversion_pct': 0.0,
                'avg_journey_duration_days': 0.0, 'avg_sessions_to_convert': 0.0,
                'avg_events_per_user': 0.0}
    converters = [j for j in per_uid if j['converted']]
    total_duration_days = 0.0
    total_events = 0
    for j in per_uid:
        total_duration_days += j['journey_duration_sec'] / 86400.0
        total_events += j['event_count']

    avg_sessions_to_convert = 0.0
    if converters:
        # `sessions_to_convert` is pre-computed in _build_per_uid_journeys
        # so this rolls up in O(converters) with no session re-walk.
        total_sess = sum(j.get('sessions_to_convert', 0) for j in converters)
        avg_sessions_to_convert = round(total_sess / len(converters), 1)

    return {
        'total_users':                 n,
        'converted_users':             len(converters),
        'conversion_pct':              round(100.0 * len(converters) / n, 1),
        'avg_journey_duration_days':   round(total_duration_days / n, 1),
        'avg_sessions_to_convert':     avg_sessions_to_convert,
        'avg_events_per_user':         round(total_events / n, 1),
    }


def _aggregate_inception_keywords(per_uid: list[dict], *, top_n: int) -> list[dict]:
    counts: Counter = Counter()
    for j in per_uid:
        kw = j.get('inception_keyword')
        if kw:
            counts[kw] += 1
    return [{'keyword': k, 'users': v} for k, v in counts.most_common(top_n)]


def _aggregate_post_non_conversion_hosts(per_uid: list[dict], *, top_n: int) -> list[dict]:
    """For NON-converters: what hosts did they visit AFTER the last target
    mention. Uses the per-UID `post_mention_hosts` set pre-computed once."""
    counts: Counter = Counter()
    non_converters = [j for j in per_uid if not j['converted']]
    n_non = max(1, len(non_converters))
    for j in non_converters:
        for h in (j.get('post_mention_hosts') or ()):
            counts[h] += 1
    return [
        {'host': h, 'users': c, 'users_pct': round(100.0 * c / n_non, 1)}
        for h, c in counts.most_common(top_n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Multi-axis cuts (interest / demographics) — produces one cluster collection
# per axis so the dashboard's "Cut by:" dropdown can swap without re-querying.
# ─────────────────────────────────────────────────────────────────────────────

# Minimum group size we render — sub-100-user buckets are too thin to draw
# conclusions from and clutter the dashboard.
MIN_BUCKET_USERS    = 100
MAX_BUCKETS_PER_AXIS = 10


def _bucket_for_axis(j: dict, axis: str) -> Optional[str]:
    """Return the bucket label for a single UID on a given cut axis."""
    if axis == 'inception':
        return j.get('inception') or 'OTHER'
    if axis == 'interest':
        return j.get('interest_primary') or 'Unknown'
    demo = j.get('demo') or {}
    if axis in ('gender', 'age', 'ethnicity', 'income', 'education', 'marital', 'children'):
        return demo.get(axis) or 'Unknown'
    return None


def _build_axis_clusters(per_uid: list[dict], axis: str) -> list[dict]:
    """For a single cut axis, partition UIDs into buckets and run the same
    funnel + detour + touchpoint roll-ups we already compute per inception.
    """
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for j in per_uid:
        label = _bucket_for_axis(j, axis)
        if label:
            by_bucket[label].append(j)
    # Drop tiny buckets — they're noise. ALWAYS keep an ALL bucket.
    items = [(lbl, members) for lbl, members in by_bucket.items()
             if len(members) >= MIN_BUCKET_USERS]
    items.sort(key=lambda kv: len(kv[1]), reverse=True)
    items = items[:MAX_BUCKETS_PER_AXIS]

    total = max(1, len(per_uid))
    out: list[dict] = []
    # Prepend ALL so the dashboard always has a baseline.
    all_members = per_uid
    out.append(_summarize_bucket('ALL', all_members, total, with_touchpoints=True))
    for lbl, members in items:
        out.append(_summarize_bucket(lbl, members, total, with_touchpoints=True))
    return out


def _summarize_bucket(label: str, members: list[dict], cohort_total: int,
                      *, with_touchpoints: bool = False) -> dict:
    """Build the per-cluster summary used by both inception-clusters and
    cut-axis clusters (interest / demographic). Shape matches existing
    `_aggregate_clusters` output so the dashboard renderer is reusable.
    """
    converted = sum(1 for j in members if j['converted'])
    n = max(1, len(members))
    summary = {
        'label':          label,
        'inception':      label,                 # backward-compat key for renderer
        'users':          len(members),
        'users_pct':      round(100.0 * len(members) / max(1, cohort_total), 1),
        'converted':      converted,
        'conversion_pct': round(100.0 * converted / n, 1),
        'funnel':         _build_funnel(members),
        'detours':        _aggregate_detours_for_cluster(members),
    }
    if with_touchpoints:
        # A condensed top-5 touchpoint list per bucket so the cluster card
        # can show "what's driving this slice" without re-rendering the full
        # touchpoint table.
        tp_reach: Counter = Counter()
        for j in members:
            for tp in (j.get('touchpoint_set') or ()):
                tp_reach[tp] += 1
        summary['top_touchpoints'] = [
            {'label': tp, 'reach': c, 'reach_pct': round(100.0 * c / n, 1)}
            for tp, c in tp_reach.most_common(5)
        ]
    return summary


def _aggregate_cuts(per_uid: list[dict]) -> dict[str, list[dict]]:
    """Produce one cluster collection per cut axis. The dashboard's
    "Cut by:" dropdown chooses which one to render.

    Note: the existing `_aggregate_clusters` (by inception channel) is
    re-computed here under axis="inception" so the cuts dict is
    self-contained and the renderer doesn't need a special case.
    """
    return {
        axis: _build_axis_clusters(per_uid, axis)
        for axis, _label in CUT_DISPLAY_ORDER
    }


# ─────────────────────────────────────────────────────────────────────────────
# Touchpoint aggregation — reach %, conversion lift, cadence, co-occurrence.
# This is the bulk of what the client asked for in the Goat brief:
# "how many touches did purchasers get", "what drives conversion vs
# conversation", "cadence between asset engagement and sales", "overlap
# between trailer + influencer".
# ─────────────────────────────────────────────────────────────────────────────

# Cap the co-occurrence matrix to the top N most-reached touchpoints so the
# UI renders cleanly; full Cartesian explodes too fast.
TOP_N_TOUCHPOINTS_FOR_OVERLAP = 12
TOP_N_OVERLAP_PAIRS           = 25


def _aggregate_touchpoints(per_uid: list[dict]) -> dict:
    """For each touchpoint label compute:

        reach            — # users who hit at least once
        reach_pct        — % of cohort
        converters_reached      — # converters who hit
        conv_rate_when_reached  — conv % among reached
        conv_rate_when_not      — conv % among un-reached
        lift_pct                — (rate_when_reached - rate_when_not) / rate_when_not
        avg_days_to_conversion  — for converters: days from first-touch → conversion
        avg_touches_per_user    — among reached
        share_of_converters     — % of converters who saw this touchpoint
    Plus a co-occurrence list of the top pairs.
    """
    n = max(1, len(per_uid))
    converted_uids = [j for j in per_uid if j['converted']]
    n_conv = max(1, len(converted_uids))
    base_conv_rate = 100.0 * len(converted_uids) / n

    # Pre-collect per-label cohorts.
    reach_users: dict[str, list[dict]] = defaultdict(list)
    touch_counts_per_user: dict[str, list[int]] = defaultdict(list)
    days_to_conv: dict[str, list[float]] = defaultdict(list)
    for j in per_uid:
        tset = j.get('touchpoint_set') or ()
        tcounts = j.get('touchpoint_counts') or {}
        tfirst = j.get('touchpoint_first_ts') or {}
        conv_ts = j.get('conversion_ts')
        for tp in tset:
            reach_users[tp].append(j)
            touch_counts_per_user[tp].append(tcounts.get(tp, 1))
            if conv_ts is not None and tp in tfirst:
                delta_ms = max(0, conv_ts - tfirst[tp])
                days_to_conv[tp].append(delta_ms / 86_400_000.0)

    rows: list[dict] = []
    seen_labels: set[str] = set()
    # Display-order first, then any unknown labels we happened to tag.
    label_order = list(TOUCHPOINT_DISPLAY_ORDER) + sorted(
        set(reach_users.keys()) - set(TOUCHPOINT_DISPLAY_ORDER)
    )
    for tp in label_order:
        if tp in seen_labels:
            continue
        seen_labels.add(tp)
        users = reach_users.get(tp) or []
        if not users:
            continue
        reach = len(users)
        converters_reached = sum(1 for j in users if j['converted'])
        not_reached = n - reach
        non_converters_reached = reach - converters_reached
        conv_rate_when_reached = 100.0 * converters_reached / max(1, reach)
        # n_conv = converters_reached + converters_not_reached
        converters_not_reached = len(converted_uids) - converters_reached
        conv_rate_when_not = (
            100.0 * converters_not_reached / max(1, not_reached) if not_reached else 0.0
        )
        if conv_rate_when_not > 0:
            lift_pct = round(
                100.0 * (conv_rate_when_reached - conv_rate_when_not) / conv_rate_when_not, 1
            )
        else:
            lift_pct = None
        touches = touch_counts_per_user.get(tp) or []
        avg_touches = round(sum(touches) / max(1, len(touches)), 2)
        d2c = days_to_conv.get(tp) or []
        avg_d2c = round(sum(d2c) / max(1, len(d2c)), 1) if d2c else None
        rows.append({
            'label':                  tp,
            'reach':                  reach,
            'reach_pct':              round(100.0 * reach / n, 1),
            'converters_reached':     converters_reached,
            'share_of_converters':    round(100.0 * converters_reached / n_conv, 1),
            'conv_rate_when_reached': round(conv_rate_when_reached, 2),
            'conv_rate_when_not':     round(conv_rate_when_not, 2),
            'baseline_conv_rate':     round(base_conv_rate, 2),
            'lift_pct':               lift_pct,
            'avg_days_to_conversion': avg_d2c,
            'avg_touches_per_user':   avg_touches,
        })

    # ── Co-occurrence matrix on top-N touchpoints ────────────────────────
    top_labels = [r['label'] for r in rows[:TOP_N_TOUCHPOINTS_FOR_OVERLAP]]
    top_set = set(top_labels)
    pair_users: Counter = Counter()
    pair_converters: Counter = Counter()
    for j in per_uid:
        tset = (j.get('touchpoint_set') or set()) & top_set
        if len(tset) < 2:
            continue
        tlist = sorted(tset)
        for i in range(len(tlist)):
            for k in range(i + 1, len(tlist)):
                pair = (tlist[i], tlist[k])
                pair_users[pair] += 1
                if j['converted']:
                    pair_converters[pair] += 1

    overlap = []
    for pair, users in pair_users.most_common(TOP_N_OVERLAP_PAIRS):
        convs = pair_converters[pair]
        overlap.append({
            'a':                pair[0],
            'b':                pair[1],
            'users':            users,
            'users_pct':        round(100.0 * users / n, 1),
            'converters':       convs,
            'conv_rate':        round(100.0 * convs / max(1, users), 2),
        })

    # ── Touch-count distribution ─────────────────────────────────────────
    # Buckets: 0, 1, 2-3, 4-6, 7-10, 11+. For converters and non-converters
    # separately — this answers "how many touches did purchasers receive".
    def _touch_bucket(n_touches: int) -> str:
        if n_touches == 0:  return '0'
        if n_touches == 1:  return '1'
        if n_touches <= 3:  return '2-3'
        if n_touches <= 6:  return '4-6'
        if n_touches <= 10: return '7-10'
        return '11+'

    bucket_order = ['0', '1', '2-3', '4-6', '7-10', '11+']
    conv_buckets: Counter = Counter()
    nonconv_buckets: Counter = Counter()
    for j in per_uid:
        total_touches = sum((j.get('touchpoint_counts') or {}).values())
        b = _touch_bucket(total_touches)
        if j['converted']:
            conv_buckets[b] += 1
        else:
            nonconv_buckets[b] += 1
    touch_distribution = [{
        'bucket':         b,
        'converters':     conv_buckets.get(b, 0),
        'non_converters': nonconv_buckets.get(b, 0),
        'total':          conv_buckets.get(b, 0) + nonconv_buckets.get(b, 0),
        'conv_pct':       round(
            100.0 * conv_buckets.get(b, 0) /
            max(1, conv_buckets.get(b, 0) + nonconv_buckets.get(b, 0)), 2),
    } for b in bucket_order]

    return {
        'baseline_conv_rate': round(base_conv_rate, 2),
        'cohort_size':        n,
        'converters':         len(converted_uids),
        'rows':               rows,
        'overlap':            overlap,
        'touch_distribution': touch_distribution,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Path-to-purchase aggregation (BSFS-style visual journey)
# ─────────────────────────────────────────────────────────────────────────────

PATH_TOP_HOSTS_PER_STEP   = 8
PATH_TOP_TOUCHPOINTS_PER_STEP = 6
PATH_TOP_NGRAMS           = 25
PATH_NGRAM_MIN_USERS      = 5


def _step_label_for_event(ev: dict) -> str:
    """Best human label for a single event in the path ribbon.
    Prefer the most specific touchpoint we tagged; otherwise use STEP/host."""
    tps = ev.get('touchpoints') or []
    if tps:
        # Stable preference order: known display order, then any extra.
        for tp in TOUCHPOINT_DISPLAY_ORDER:
            if tp in tps:
                return tp
        return sorted(tps)[0]
    step = ev.get('step') or 'DETOUR'
    host = ev.get('host') or ''
    if step == 'SEARCH' or 'google.com' in host or 'bing.com' in host:
        return 'ORGANIC_SEARCH'
    if step == 'DETOUR':
        return 'DETOUR'
    return step


def _aggregate_path_to_purchase(
    per_uid: list[dict],
    *,
    steps: int = 10,
    cohort_mode: str = 'mention',
) -> dict:
    """BSFS-style "path to purchase" ribbon.

    For each converter (or each user in mention-mode fallback):
      * Take the last `steps` events that occurred AT OR BEFORE conversion_ts.
        These are right-aligned to the conversion column. Step −K is the
        oldest of the K, step −1 is the most recent before purchase, then a
        synthetic CONVERSION column at the right.
      * Bucket each event into (column_index → label) using
        `_step_label_for_event`.
      * Aggregate: per-column histograms of top labels + top hosts, plus the
        K-gram of labels (ordered sequence) so we can rank "the most common
        path to purchase". Users with fewer than 2 events still contribute
        to whatever columns they cover.

    Returns:
      {
        'mode': 'converters'|'all',
        'cohort_size': int,        'steps': int,        'columns': [
          {'index': -K, 'label': 'Step -K',
           'users': N, 'top_labels': [...], 'top_hosts': [...]}, ...
          {'index': 0,  'label': 'CONVERSION', ...}
        ],
        'top_paths': [
          {'path': ['ORGANIC_SEARCH', 'TRAILER', 'SHOWTIME_LOOKUP', 'TICKETING'],
           'users': N, 'users_pct': X}
        ],
      }
    """
    # Pick the cohort to walk: converters in conversion mode; fall back to
    # everyone in mention mode if there are too few converters (the user
    # still wants a visual).
    converters = [j for j in per_uid if j.get('converted')]
    if cohort_mode == 'conversion' and converters:
        cohort = converters
        cohort_label = 'converters'
    elif converters and len(converters) >= 25:
        cohort = converters
        cohort_label = 'converters'
    else:
        cohort = list(per_uid)
        cohort_label = 'all'

    n = max(1, len(cohort))

    # Per-column accumulators (column index 0 = CONVERSION; negative steps
    # are the lookback columns at -1, -2, ... -steps).
    col_label_counts: dict[int, Counter] = defaultdict(Counter)
    col_host_counts:  dict[int, Counter] = defaultdict(Counter)
    col_user_sets:    dict[int, set]     = defaultdict(set)
    ngram_counter: Counter = Counter()

    for j in cohort:
        uid = j.get('uid')
        conv_ts = j.get('conversion_ts')
        # Flatten sessions to a single event list.
        all_events: list[dict] = []
        for sess in (j.get('sessions') or []):
            for ev in sess:
                all_events.append(ev)
        all_events.sort(key=lambda e: e.get('ts_ms', 0))

        # Anchor: use conversion_ts when known; otherwise the LAST event
        # the user produced in the window (so "path to the end of the
        # observed journey" still renders meaningfully in mention mode).
        anchor_ts = conv_ts if conv_ts is not None else (
            all_events[-1].get('ts_ms') if all_events else None
        )
        if anchor_ts is None:
            continue

        # Slice: keep events strictly before anchor; anchor itself is
        # column 0. Then keep only the last `steps` of those, in time order.
        before = [ev for ev in all_events if ev.get('ts_ms', 0) < anchor_ts]
        slice_evs = before[-steps:]

        # Pre-classify with the existing touchpoint tagger so column histograms
        # collapse hosts into channel categories (TRAILER/SOCIAL/REVIEW/…).
        # We re-call _classify_touchpoints_for_event lazily by using the host /
        # step / is_mention already on each `ev`.
        ngram_parts: list[str] = []

        # Right-align: slice_evs[-1] is closest to purchase → column -1.
        # slice_evs[-K] is oldest → column -K.
        k = len(slice_evs)
        for idx, ev in enumerate(slice_evs):
            col_idx = -(k - idx)  # -K, -K+1, ... -1
            label = _step_label_for_event(ev)
            host = ev.get('host') or ''
            col_label_counts[col_idx][label] += 1
            if host:
                col_host_counts[col_idx][host] += 1
            col_user_sets[col_idx].add(uid)
            ngram_parts.append(label)

        # CONVERSION column itself.
        col_label_counts[0]['CONVERSION'] += 1
        col_user_sets[0].add(uid)
        ngram_parts.append('CONVERSION')

        # Collapse adjacent dups in the ngram so e.g.
        # [TRAILER, TRAILER, TICKETING, TICKETING, CONVERSION] becomes
        # [TRAILER, TICKETING, CONVERSION] — same story, less noise.
        collapsed = []
        for tok in ngram_parts:
            if not collapsed or collapsed[-1] != tok:
                collapsed.append(tok)
        if len(collapsed) >= 2:
            ngram_counter[tuple(collapsed)] += 1

    # Materialize columns in display order: -steps … -1, 0
    columns: list[dict] = []
    for col_idx in list(range(-steps, 0)) + [0]:
        labels = col_label_counts.get(col_idx) or Counter()
        hosts = col_host_counts.get(col_idx) or Counter()
        users = len(col_user_sets.get(col_idx) or ())
        if col_idx == 0:
            col_lbl = 'CONVERSION'
        else:
            col_lbl = f'Step {col_idx}'
        columns.append({
            'index':      col_idx,
            'label':      col_lbl,
            'users':      users,
            'users_pct':  round(100.0 * users / n, 1),
            'top_labels': [
                {'label': lab, 'users': cnt,
                 'pct': round(100.0 * cnt / max(1, users), 1)}
                for lab, cnt in labels.most_common(PATH_TOP_TOUCHPOINTS_PER_STEP)
            ],
            'top_hosts':  [
                {'host': h, 'users': cnt,
                 'pct': round(100.0 * cnt / max(1, users), 1)}
                for h, cnt in hosts.most_common(PATH_TOP_HOSTS_PER_STEP)
            ],
        })

    top_paths = []
    for path, users in ngram_counter.most_common(PATH_TOP_NGRAMS):
        if users < PATH_NGRAM_MIN_USERS:
            continue
        top_paths.append({
            'path':      list(path),
            'users':     users,
            'users_pct': round(100.0 * users / n, 1),
        })

    return {
        'mode':         cohort_label,
        'cohort_size':  len(cohort),
        'steps':        steps,
        'columns':      columns,
        'top_paths':    top_paths,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def _persist(s3_client, summary: dict, project_name: str, username: str, job_id: str) -> str:
    """Write summary JSON.gz to S3 and append to the lightweight index file.

    S3 path:
      s3://dashboard-inputs/journey-iq/{username}/{slug}_{ts}_{job}.json.gz
    """
    safe_user = re.sub(r'[^a-zA-Z0-9_-]+', '_', username or 'anon').strip('_') or 'anon'
    safe_proj = re.sub(r'[^a-zA-Z0-9_-]+', '_', project_name or 'journey').strip('_') or 'journey'
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    key = f"{S3_PREFIX}{safe_user}/{safe_proj}_{ts}_{job_id}.json.gz"

    if s3_client is None:
        print(f"[Journey IQ] no s3_client; would have written {key} "
              f"({len(json.dumps(summary)):,} bytes)")
        return key

    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='wb') as gz:
        gz.write(json.dumps(summary, ensure_ascii=False).encode('utf-8'))
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=body.getvalue(),
        ContentType='application/json',
        ContentEncoding='gzip',
    )
    _append_to_index(s3_client, key, summary)
    return key


def _append_to_index(s3_client, key: str, summary: dict) -> None:
    """Lightweight, best-effort index for fast /list endpoint."""
    try:
        idx: dict = {'runs': []}
        try:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)
            idx = json.loads(obj['Body'].read().decode('utf-8')) or {'runs': []}
        except Exception:
            pass
        meta = summary.get('meta', {}) or {}
        kpis = summary.get('kpis', {}) or {}
        idx['runs'] = [r for r in (idx.get('runs') or []) if r.get('key') != key]
        idx['runs'].append({
            'key':            key,
            'project_name':   meta.get('project_name'),
            'target':         meta.get('target'),
            'start_date':     meta.get('start_date'),
            'end_date':       meta.get('end_date'),
            'created_by':     meta.get('created_by'),
            'created_at':     meta.get('created_at'),
            'total_users':    kpis.get('total_users'),
            'conversion_pct': kpis.get('conversion_pct'),
        })
        # Keep the index bounded — drop the oldest if over 500 entries.
        idx['runs'] = idx['runs'][-500:]
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=S3_INDEX_KEY,
            Body=json.dumps(idx, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception as e:
        print(f"[Journey IQ] index append failed (non-fatal): {e}")


def load_run_from_s3(s3_client, key: str) -> Optional[dict]:
    """Read a previously-persisted run back from S3 (handles .json + .json.gz)."""
    if s3_client is None or not key:
        return None
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        raw = obj['Body'].read()
        if key.endswith('.gz') or obj.get('ContentEncoding') == 'gzip':
            raw = gzip.decompress(raw)
        return json.loads(raw.decode('utf-8'))
    except Exception as e:
        print(f"[Journey IQ] load_run_from_s3 failed for {key}: {e}")
        return None


def list_runs(s3_client, *, username: Optional[str] = None,
              limit: int = 100) -> list[dict]:
    """Return the index entries, newest first, optionally filtered by user."""
    if s3_client is None:
        return []
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_INDEX_KEY)
        idx = json.loads(obj['Body'].read().decode('utf-8')) or {}
        runs = idx.get('runs') or []
    except Exception:
        runs = []
    runs = sorted(runs, key=lambda r: r.get('created_at') or '', reverse=True)
    if username:
        runs = [r for r in runs if r.get('created_by') == username]
    return runs[:limit]


def _empty_summary(target, project_name, start_date, end_date,
                   lookback_days, forward_days) -> dict:
    return {
        'meta': {
            'project_name':   project_name,
            'target':         target,
            'target_variants': _target_variants(target),
            'start_date':     start_date,
            'end_date':       end_date,
            'lookback_days':  lookback_days,
            'forward_days':   forward_days,
            'conversion_patterns': list(CONVERSION_PATTERNS),
            'extra_touchpoint_keywords': {},
            'cut_options':    [{'key': k, 'label': lbl} for k, lbl in CUT_DISPLAY_ORDER],
            'created_at':     datetime.utcnow().isoformat() + 'Z',
            'matched_uids':   0,
            'events_pulled':  0,
        },
        'kpis': {
            'total_users': 0, 'converted_users': 0, 'conversion_pct': 0.0,
            'avg_journey_duration_days': 0.0, 'avg_sessions_to_convert': 0.0,
            'avg_events_per_user': 0.0,
        },
        'clusters':    [],
        'cuts':        {axis: [] for axis, _ in CUT_DISPLAY_ORDER},
        'touchpoints': {'baseline_conv_rate': 0.0, 'cohort_size': 0,
                        'converters': 0, 'rows': [], 'overlap': [],
                        'touch_distribution': []},
        'keywords':    [],
        'post_hosts':  [],
        'path_to_purchase': {
            'mode': 'converters', 'cohort_size': 0, 'steps': 0,
            'columns': [], 'top_paths': [],
        },
        'facts':       [],
    }


__all__ = [
    'run_job',
    'list_runs',
    'load_run_from_s3',
    'parse_extra_touchpoint_keywords',
    'CONVERSION_PATTERNS',
    'STEP_DISPLAY_ORDER',
    'INCEPTION_DISPLAY_ORDER',
    'TOUCHPOINT_DISPLAY_ORDER',
    'CUT_DISPLAY_ORDER',
    'DEFAULT_LOOKBACK_DAYS',
    'DEFAULT_FORWARD_DAYS',
    'S3_BUCKET',
    'S3_PREFIX',
    'S3_INDEX_KEY',
]
