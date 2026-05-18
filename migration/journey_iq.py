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
MAX_EVENTS_PER_UID    = 600      # protects memory; ~40+ sessions of activity
SESSION_GAP_MINUTES   = 30
TOP_N_DETOURS         = 8
TOP_N_KEYWORDS        = 25
TOP_N_POST_HOSTS      = 12


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
# Step labels rendered in the dashboard (in display order). The dashboard
# walks this list left-to-right; any step not present in the data is simply
# omitted from the funnel for that cluster.
# ─────────────────────────────────────────────────────────────────────────────

STEP_DISPLAY_ORDER = [
    'LANDING', 'BROWSE', 'DISCOUNTS', 'PDP', 'LOCATION',
    'FINANCING', 'WARRANTY', 'CART', 'CHECKOUT', 'CONVERSION',
]

INCEPTION_DISPLAY_ORDER = ['SEARCH', 'DIRECT', 'AD', 'SOCIAL', 'REFERRAL', 'AI_AGENT', 'OTHER']


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
    conv_patterns = tuple(CONVERSION_PATTERNS) + tuple(
        p.strip().lower() for p in (extra_conversion_patterns or []) if p and p.strip()
    )

    _p(2, 'Connecting to ClickHouse...')
    conn = ch_connect(settings={'max_execution_time': 1800})
    cur = conn.cursor()

    try:
        # ── Phase 1: resolve target → UIDs ───────────────────────────────
        _p(8, f'Searching clickstream for "{target}"...')
        term_variants = _target_variants(target)
        match_clause = _build_match_clause(term_variants)
        target_domain_guesses = _guess_target_domains(target)

        # Materialize matched UIDs + their per-UID first/last mention TS.
        cur.execute(f"DROP TABLE IF EXISTS journey_uids_{job_id}")
        cur.execute(f"""
            CREATE TEMPORARY TABLE journey_uids_{job_id} AS
            SELECT
                UID,
                min(VISIT_TS) AS first_mention_ts,
                max(VISIT_TS) AS last_mention_ts
            FROM clickstream.clickstream_final
            WHERE DELIVERED BETWEEN toDate('{sd}') AND toDate('{ed}')
              AND {match_clause}
            GROUP BY UID
            ORDER BY first_mention_ts ASC
            LIMIT {MAX_UIDS}
        """)
        cur.execute(f"SELECT count() FROM journey_uids_{job_id}")
        matched_uids = int((cur.fetchone() or [0])[0] or 0)

        if matched_uids == 0:
            _p(100, 'No users mentioned the target in the date range.')
            empty = _empty_summary(target, project_name, start_date, end_date,
                                   lookback_days, forward_days)
            s3_key = _persist(s3_client, empty, project_name, username, job_id)
            empty['s3_key'] = s3_key
            return {'status': 'completed', 's3_key': s3_key, 'summary': empty}

        _p(20, f'Matched {matched_uids:,} users. Pulling journey windows...')

        # ── Phase 2: pull symmetric per-UID journey window ────────────────
        # Rows are bounded per UID via a window-style filter so a single SQL
        # statement returns just the relevant slice of the clickstream.
        # ROW_NUMBER() caps to MAX_EVENTS_PER_UID newest-first to protect the
        # worker from a single hyperactive UID dominating memory.
        cur.execute(f"""
            SELECT UID, toUnixTimestamp64Milli(VISIT_TS) AS ts_ms,
                   URL, COMMON_NAME, DOMAIN, PLATFORM, BROWSER
            FROM (
                SELECT cf.UID,
                       cf.VISIT_TS,
                       cf.URL,
                       cf.COMMON_NAME,
                       cf.DOMAIN,
                       cf.PLATFORM,
                       cf.BROWSER,
                       row_number() OVER (PARTITION BY cf.UID ORDER BY cf.VISIT_TS DESC) AS rn
                FROM clickstream.clickstream_final AS cf
                INNER JOIN journey_uids_{job_id} AS u ON cf.UID = u.UID
                WHERE cf.DELIVERED BETWEEN
                          toDate(u.first_mention_ts) - {lookback_days}
                      AND toDate(u.last_mention_ts)  + {forward_days}
                  AND cf.VISIT_TS BETWEEN
                          u.first_mention_ts - INTERVAL {lookback_days} DAY
                      AND u.last_mention_ts  + INTERVAL {forward_days}  DAY
                  AND length(cf.URL) > 8
            ) sub
            WHERE rn <= {MAX_EVENTS_PER_UID}
            ORDER BY UID ASC, ts_ms ASC
        """)
        raw_rows = cur.fetchall()
        _p(50, f'Pulled {len(raw_rows):,} events. Building journeys...')

        # ── Phase 3-5: sessionize, classify, aggregate (pure Python) ──────
        target_lc = target.lower()
        per_uid_journeys = _build_per_uid_journeys(
            raw_rows,
            target_lc=target_lc,
            target_variants=term_variants,
            target_domains=target_domain_guesses,
            conv_patterns=conv_patterns,
        )
        _p(70, 'Aggregating funnel + detours...')
        clusters = _aggregate_clusters(per_uid_journeys)
        kpis = _aggregate_kpis(per_uid_journeys)
        keywords = _aggregate_inception_keywords(per_uid_journeys, top_n=TOP_N_KEYWORDS)
        post_hosts = _aggregate_post_non_conversion_hosts(per_uid_journeys, top_n=TOP_N_POST_HOSTS)

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
            ) or []
        except Exception as e:
            print(f"[Journey IQ] insights pass failed (non-fatal): {e}")

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
                'created_by':     username,
                'created_at':     datetime.utcnow().isoformat() + 'Z',
                'duration_sec':   round(time.time() - started, 1),
                'matched_uids':   matched_uids,
                'events_pulled':  len(raw_rows),
                'job_id':         job_id,
            },
            'kpis':       kpis,
            'clusters':   clusters,
            'keywords':   keywords,
            'post_hosts': post_hosts,
            'facts':      facts,
        }
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
    """OR-of-positions over URL + COMMON_NAME for each variant. Same shape
    as `_bpiq_filter_clause` but inlined so this module has no dependency on
    app.py."""
    if not variants:
        return '1=0'
    parts = []
    for v in variants:
        safe = v.replace("'", "''")
        parts.append(f"position(lower(URL), '{safe}') > 0")
        parts.append(f"position(lower(COMMON_NAME), '{safe}') > 0")
    return '(' + ' OR '.join(parts) + ')'


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
) -> list[dict]:
    """Group rows by UID, sessionize on a 30-min gap, classify each event.

    Returns a list of ``{uid, sessions, first_mention_ts, conversion_ts,
    converted, inception, journey_duration_sec, event_count}`` dicts.
    """
    by_uid: dict[str, list[tuple]] = defaultdict(list)
    for row in raw_rows:
        try:
            uid, ts_ms, url, common_name, domain, platform, browser = row
        except Exception:
            continue
        if not uid or not ts_ms:
            continue
        by_uid[uid].append((int(ts_ms), url or '', common_name or '', domain or '',
                            platform or '', browser or ''))

    out: list[dict] = []
    gap_ms = SESSION_GAP_MINUTES * 60 * 1000

    for uid, events in by_uid.items():
        events.sort(key=lambda e: e[0])
        # Sessionize
        sessions: list[list[dict]] = []
        cur_session: list[dict] = []
        prev_ts: Optional[int] = None
        for ts_ms, url, cn, domain, platform, browser in events:
            if prev_ts is not None and (ts_ms - prev_ts) > gap_ms:
                if cur_session:
                    sessions.append(cur_session)
                cur_session = []
            classified = _classify_event(
                url=url, common_name=cn, domain=domain,
                target_lc=target_lc, target_variants=target_variants,
                target_domains=target_domains, conv_patterns=conv_patterns,
            )
            cur_session.append({
                'ts_ms':       ts_ms,
                'url':         url,
                'common_name': cn,
                'domain':      domain,
                'platform':    platform,
                'browser':     browser,
                **classified,
            })
            prev_ts = ts_ms
        if cur_session:
            sessions.append(cur_session)

        # Identify first mention + first conversion.
        # "First mention" prefers on-target events (= a page on the target's
        # own site or a COMMON_NAME match). A Google search like
        # `?q=firestone+tires` would otherwise spuriously satisfy `is_mention`
        # and pull the timeline anchor BACK to the search itself — but we
        # want the search to be the inception, not the mention.
        first_on_target_ts: Optional[int] = None
        first_any_mention_ts: Optional[int] = None
        conversion_ts: Optional[int] = None
        for sess in sessions:
            for ev in sess:
                if ev['is_mention'] and first_any_mention_ts is None:
                    first_any_mention_ts = ev['ts_ms']
                if ev['on_target'] and first_on_target_ts is None:
                    first_on_target_ts = ev['ts_ms']
                if ev['step'] == 'CONVERSION' and ev['on_target']:
                    if conversion_ts is None or ev['ts_ms'] < conversion_ts:
                        conversion_ts = ev['ts_ms']
        first_mention_ts = first_on_target_ts or first_any_mention_ts

        if first_mention_ts is None:
            # Shouldn't happen — we filtered to mentioned UIDs upstream — but
            # be defensive.
            continue

        # Inception classification: the first event of the FIRST session that
        # contains the first mention.
        inception = 'OTHER'
        inception_keyword = None
        seed_session = None
        for sess in sessions:
            if sess and any(ev['ts_ms'] == first_mention_ts for ev in sess):
                seed_session = sess
                break
        if seed_session:
            first_ev = seed_session[0]
            inception, inception_keyword = _classify_inception(first_ev)

        last_ts = max(ev['ts_ms'] for sess in sessions for ev in sess)
        journey_duration_sec = max(0, (last_ts - first_mention_ts) // 1000)

        out.append({
            'uid':                  uid,
            'sessions':             sessions,
            'first_mention_ts':     first_mention_ts,
            'conversion_ts':        conversion_ts,
            'converted':            conversion_ts is not None,
            'inception':            inception,
            'inception_keyword':    inception_keyword,
            'journey_duration_sec': journey_duration_sec,
            'event_count':          sum(len(s) for s in sessions),
            'session_count':        len(sessions),
        })
    return out


def _classify_event(
    *,
    url: str,
    common_name: str,
    domain: str,
    target_lc: str,
    target_variants: list[str],
    target_domains: set[str],
    conv_patterns: tuple,
) -> dict:
    """Classify a single clickstream event."""
    url_lc = (url or '').lower()
    cn_lc = (common_name or '').lower()
    dom_lc = (domain or '').lower()
    try:
        parsed = urllib.parse.urlsplit(url_lc)
        host = parsed.netloc or dom_lc
        path = parsed.path or '/'
    except Exception:
        host = dom_lc
        path = '/'

    is_mention = any(v in url_lc or v in cn_lc for v in target_variants)

    # "On target host" = the event is on the target's own site. Use COMMON_NAME
    # equality (cheap + accurate for known brands), or a heuristic domain
    # guess as a fallback.
    on_target = False
    if cn_lc and cn_lc == target_lc:
        on_target = True
    elif host and any(d in host for d in target_domains):
        on_target = True

    step = 'DETOUR'
    if on_target:
        # CONVERSION is special-cased so the override hits before the generic
        # CHECKOUT rule (CHECKOUT pages are not yet a conversion).
        if any(p in path for p in conv_patterns):
            step = 'CONVERSION'
        else:
            step = _classify_target_step(path)
    elif _host_is_search_engine(host):
        step = 'SEARCH'

    return {
        'host':       host,
        'path':       path,
        'is_mention': bool(is_mention),
        'on_target':  bool(on_target),
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
    return any(h in host for h in SEARCH_ENGINE_HOSTS) if host else False


def _classify_inception(first_ev: dict) -> tuple[str, Optional[str]]:
    """Return (channel_label, optional_keyword)."""
    host = (first_ev.get('host') or '').lower()
    url = (first_ev.get('url') or '').lower()
    try:
        qs = urllib.parse.urlsplit(url).query
        params = urllib.parse.parse_qs(qs)
    except Exception:
        params = {}

    # Paid markers win over plain Search/Social.
    if any(k in params for k in PAID_QUERY_KEYS):
        return 'AD', _extract_search_keyword(params)
    if params.get('utm_medium') and any(
        m in (params['utm_medium'][0] or '').lower() for m in PAID_UTM_MEDIUM
    ):
        return 'AD', _extract_search_keyword(params)

    if _host_is_search_engine(host):
        return 'SEARCH', _extract_search_keyword(params)
    if any(h in host for h in AI_AGENT_HOSTS):
        return 'AI_AGENT', None
    if any(h in host for h in SOCIAL_HOSTS):
        return 'SOCIAL', None
    if first_ev.get('on_target'):
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
    AT LEAST ONCE (in any session). Drop-off is computed as
    1 - active(n+1)/active(n) using only steps that have non-zero reach."""
    reach: dict[str, set[str]] = defaultdict(set)
    for j in cluster_members:
        seen_steps: set[str] = set()
        for sess in j['sessions']:
            for ev in sess:
                if ev['step'] in STEP_DISPLAY_ORDER:
                    seen_steps.add(ev['step'])
        for s in seen_steps:
            reach[s].add(j['uid'])

    funnel: list[dict] = []
    prev_count: Optional[int] = None
    for step in STEP_DISPLAY_ORDER:
        cnt = len(reach.get(step, ()))
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
    """Top hosts visited after the user left the target site within the
    journey window. We count DETOUR events that happen AFTER the first
    mention (post-touch detours = "where did they go to comparison-shop")."""
    host_counts: Counter = Counter()
    for j in cluster_members:
        first = j['first_mention_ts']
        seen_hosts_this_uid: set[str] = set()
        for sess in j['sessions']:
            for ev in sess:
                if ev['ts_ms'] < first:
                    continue
                if ev['step'] in ('DETOUR', 'SEARCH'):
                    h = ev.get('host') or ''
                    if h and h not in seen_hosts_this_uid:
                        seen_hosts_this_uid.add(h)
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
    durations_days = [j['journey_duration_sec'] / 86400.0 for j in per_uid]

    avg_sessions_to_convert = 0.0
    if converters:
        sess_counts: list[int] = []
        for j in converters:
            n_sess_before_conv = 0
            for sess in j['sessions']:
                if any(ev['step'] == 'CONVERSION' for ev in sess):
                    n_sess_before_conv += 1
                    break
                n_sess_before_conv += 1
            sess_counts.append(n_sess_before_conv)
        avg_sessions_to_convert = round(sum(sess_counts) / len(sess_counts), 1)

    return {
        'total_users':                 n,
        'converted_users':             len(converters),
        'conversion_pct':              round(100.0 * len(converters) / n, 1),
        'avg_journey_duration_days':   round(sum(durations_days) / n, 1),
        'avg_sessions_to_convert':     avg_sessions_to_convert,
        'avg_events_per_user':         round(sum(j['event_count'] for j in per_uid) / n, 1),
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
    mention? This is the BSFS 'where they went after if they didn't convert'
    panel."""
    counts: Counter = Counter()
    non_converters = [j for j in per_uid if not j['converted']]
    n_non = max(1, len(non_converters))
    for j in non_converters:
        last_mention = max(
            (ev['ts_ms'] for sess in j['sessions'] for ev in sess if ev['is_mention']),
            default=j['first_mention_ts'],
        )
        seen_hosts_this_uid: set[str] = set()
        for sess in j['sessions']:
            for ev in sess:
                if ev['ts_ms'] <= last_mention:
                    continue
                h = ev.get('host') or ''
                if h and h not in seen_hosts_this_uid:
                    seen_hosts_this_uid.add(h)
                    counts[h] += 1
    return [
        {'host': h, 'users': c, 'users_pct': round(100.0 * c / n_non, 1)}
        for h, c in counts.most_common(top_n)
    ]


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
            'created_at':     datetime.utcnow().isoformat() + 'Z',
            'matched_uids':   0,
            'events_pulled':  0,
        },
        'kpis': {
            'total_users': 0, 'converted_users': 0, 'conversion_pct': 0.0,
            'avg_journey_duration_days': 0.0, 'avg_sessions_to_convert': 0.0,
            'avg_events_per_user': 0.0,
        },
        'clusters': [],
        'keywords': [],
        'post_hosts': [],
        'facts': [],
    }


__all__ = [
    'run_job',
    'list_runs',
    'load_run_from_s3',
    'CONVERSION_PATTERNS',
    'STEP_DISPLAY_ORDER',
    'INCEPTION_DISPLAY_ORDER',
    'DEFAULT_LOOKBACK_DAYS',
    'DEFAULT_FORWARD_DAYS',
    'S3_BUCKET',
    'S3_PREFIX',
    'S3_INDEX_KEY',
]
