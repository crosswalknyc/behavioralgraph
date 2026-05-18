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
    conn = ch_connect(settings=CH_RUN_SETTINGS)
    cur = conn.cursor()

    try:
        # ── Phase 1: resolve target → UIDs ───────────────────────────────
        # multiSearchAny(lower(URL), [...]) routes through the ngrambf_v1
        # skip index defined on lower(URL) (and lower(COMMON_NAME)), which
        # is dramatically faster than ORing N position() calls.
        _p(8, f'Searching clickstream for "{target}"...')
        term_variants = _target_variants(target)
        match_clause = _build_match_clause(term_variants)
        target_domain_guesses = _guess_target_domains(target)

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

        _p(18, f'Matched {matched_uids:,} users. Computing window bounds...')

        # Compute the GLOBAL [min(first_mention)-lookback, max(last_mention)+forward]
        # date range so ClickHouse can prune partitions before evaluating the
        # per-UID time filter. Without this, the per-row DELIVERED comparison
        # against u.first_mention_ts forces a full scan of the matched window.
        cur.execute(f"""
            SELECT toDate(min(first_mention_ts)) - {lookback_days},
                   toDate(max(last_mention_ts))  + {forward_days}
            FROM journey_uids_{job_id}
        """)
        d_lo, d_hi = cur.fetchone()

        _p(22, f'Pulling journey events for {matched_uids:,} users ({d_lo} → {d_hi})...')

        # ── Phase 2: pull symmetric per-UID journey window ────────────────
        # Three speedups vs the previous query:
        #   1. WHERE DELIVERED BETWEEN d_lo AND d_hi  → partition pruning fires.
        #   2. LIMIT N BY cf.UID                      → native CH "top-N per
        #      group" path; cheaper than row_number() + WHERE rn <= N.
        #   3. Drop BROWSER + PLATFORM                → smaller rows over the
        #      wire, fewer Python objects to build downstream.
        # Ordering by abs(VISIT_TS - first_mention_ts) keeps events CLOSEST
        # to the brand interaction when an extreme-tail UID exceeds the cap,
        # so we never lose the conversion event itself.
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
        _p(50, f'Pulled {len(raw_rows):,} events. Building journeys...')

        # ── Phase 3-5: sessionize, classify, aggregate (pure Python) ──────
        target_lc = target.lower()
        per_uid_journeys = _build_per_uid_journeys(
            raw_rows,
            target_lc=target_lc,
            target_variants=term_variants,
            target_domains=target_domain_guesses,
            conv_patterns=conv_patterns,
            progress_cb=progress_cb,
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
    progress_cb: Optional[Callable[..., None]] = None,
) -> list[dict]:
    """Group rows by UID, sessionize on a 30-min gap, classify each event.

    Returns a list of journey dicts. Each per-event entry is intentionally
    slim — (ts_ms, host, step, is_mention, on_target) plus an optional
    `_url` on the FIRST event of each session (needed by the inception
    classifier). Stripping URL/COMMON_NAME/PLATFORM from every event
    cuts memory by ~6× on a 20M-event run.
    """
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
            prev_ts = ts_ms

        if cur_session:
            sessions.append(cur_session)

        # Prefer on-target events as the journey anchor. A Google search like
        # `?q=firestone+tires` would otherwise spuriously satisfy `is_mention`
        # and pull the timeline anchor BACK to the search itself — but we
        # want the search to be the inception, not the mention.
        first_mention_ts = first_on_target_ts or first_any_mention_ts
        if first_mention_ts is None:
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

        out.append({
            'uid':                  uid,
            'sessions':             sessions,
            'first_mention_ts':     first_mention_ts,
            'last_mention_ts':      last_mention_ts,
            'conversion_ts':        conversion_ts,
            'converted':            conversion_ts is not None,
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
