"""
IQ Rankers — daily Talent / Brands leaderboards
================================================
For every Profile IQ profile, this module:

1.  Auto-provisions a Sentiment IQ tracker (using the BRAND INPUT row stored
    in the profile's CSV) so we have one tracker per profile.
2.  Runs Layer-1 (behavioral) sentiment scoring against
    `clickstream.clickstream_final` for yesterday — pure ClickHouse, no
    OpenAI cost — and writes one row per (profile, date) into
    `reference.profile_iq_daily_metrics`.
3.  Computes a CW IQ Score (0..100, ~50 = average for that entity) using a
    z-score-style composite of volume, reach, sentiment, momentum, recency.
4.  Provides aggregator functions the Flask routes use to render the
    Talent / Brands leaderboard tables (day / 7d / 28d / MTD / QTD / YTD /
    custom rollups, with delta vs the equivalent prior period).

Snowflake is NEVER used here — ClickHouse only.

This module is framework-agnostic. Flask routes inject `s3_client`, the
ClickHouse connector (`ch_connect`), and the Sentiment IQ module so this
file stays small and unit-testable.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

S3_DEFAULT_BUCKET = "dashboard-inputs"

# Master → list of subcategory strings (matches the Profile IQ optgroups in
# templates/index.html so the UI sub-tabs line up exactly).
MASTER_CATEGORIES: dict[str, list[str]] = {
    "BRAND": [
        "ACCESSORIES", "ACTIVEWEAR", "AMUSEMENT PARKS", "APPAREL",
        "APPAREL/FOOTWEAR", "AUTOMOBILE",
        "B2B", "BANKS", "BEAUTY", "BETTING", "BEVERAGE", "CASUAL DINING", "CPG",
        "CREDIT PROVIDERS", "CREDIT PROVIDER", "DIGITAL BANKING", "EVENTS",
        "FOOTWEAR", "FRANCHISE", "GROCERY", "INTIMATES", "JEWELRY", "MEMBERSHIP",
        "NON PROFIT/CHARITY", "PHARMA", "QSR", "RETAILERS", "SECURITY",
        "TELECOM", "TICKETING", "TOY", "TRAVEL", "VENUE", "WHERE THEY SHOP",
        "WORKOUT FACILITY",
    ],
    "TALENT": [
        "ACTOR", "ATHLETE", "INFLUENCER/CREATOR",
        "EMERGING TALENT", "HOST/PERSONALITY", "MUSICIAN/BAND", "PODCASTER",
        "POLITICS/ACTIVIST", "WRITER/DIRECTOR/AUTHOR/ARTIST",
    ],
    "CONTENT": [
        "GAME PLAYERS", "GAMES", "GAMES - PLAYERS", "MOVIE", "PODCAST",
        # SERIES variants are handled by startswith("SERIES") below.
    ],
    "PLATFORMS": [
        "APP/PLATFORM", "BROADCAST/CABLE", "MEDIA", "MOVIE THEATER", "PLATFORMS",
        "SEARCH ENGINE/AI", "SOCIAL MEDIA", "STREAMING MUSIC", "STREAMING VIDEO",
        "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST", "VMVPD/FAST", "VMVPD",
    ],
    "SPORT": ["MILB", "MLB", "NBA", "NFL", "SPORTS ORGANIZATIONS",
              "SPORTS ORGANIZATION", "WNBA"],
    "GEN POP": ["GEN POP"],
    "TRENDS": ["TRENDS"],
}


# Aliases that fold into a canonical subcategory at ingest time. Keys are
# case-normalized (UPPER, stripped). Add new aliases here as profile data
# drifts — keeps the leaderboard tabs and the persisted rows consistent.
SUBCATEGORY_ALIASES: dict[str, str] = {
    "CREATOR/INFLUENCER": "INFLUENCER/CREATOR",
}


# ── Streaming / VOD platform set (drives the EVC metric) ───────────────────
# A curated list of well-known "places where users actually watch content"
# — streaming services and major VOD platforms. Stored lowercase because
# clickstream COMMON_NAME is also lowercase. The EVC ("Engagement vs Viewing
# Correlation") column on the leaderboard is computed as the share of a
# profile's mention rows whose COMMON_NAME falls in this set, i.e. of all
# the times the panel touched a URL referencing this person/brand, what %
# of those impressions happened on a streaming destination. A high EVC
# indicates the audience is actively consuming this entity's content
# (e.g. an actor whose mentions cluster around Netflix); a low EVC means
# the engagement is mostly tangential (news, social, search).
#
# We keep this curated rather than driven by host_mapping.CATEGORY because
# host_mapping conflates show titles ("The Crown") with platforms; we only
# want the platforms themselves so the metric stays interpretable.
# Override at deploy time with IQ_RANKER_STREAMING_PLATFORMS (comma-sep).
_DEFAULT_STREAMING_PLATFORMS = [
    "netflix",
    "hulu",
    "disney+", "disneyplus", "disney plus",
    "hbo", "hbo max", "max",
    "prime video", "amazon prime video", "amazon prime",
    "apple tv", "apple tv+", "apple tv plus", "appletv",
    "paramount+", "paramount plus", "paramount",
    "peacock", "peacock tv",
    "youtube", "youtube tv",
    "tubi", "tubi tv",
    "pluto", "pluto tv",
    "sling", "sling tv",
    "fubo", "fubotv", "fubo tv",
    "philo",
    "crunchyroll",
    "showtime",
    "starz",
    "discovery+", "discovery plus",
    "amc+", "amc plus",
    "britbox",
    "shudder",
    "mubi",
    "kanopy",
    "vudu",
    "roku channel", "roku",
    "plex",
    "freevee", "amazon freevee", "imdb tv",
    "dazn",
    "espn+", "espn plus",
    "mlb tv", "mlb.tv",
    "nba league pass",
    "nfl+", "nfl plus",
    "twitch",
]


def _load_streaming_platforms() -> frozenset[str]:
    raw = os.environ.get("IQ_RANKER_STREAMING_PLATFORMS", "").strip()
    if raw:
        items = [p.strip().lower() for p in raw.split(",") if p.strip()]
        return frozenset(items)
    return frozenset(p.lower() for p in _DEFAULT_STREAMING_PLATFORMS)


STREAMING_PLATFORMS_LC: frozenset[str] = _load_streaming_platforms()


def normalize_subcategory(subcategory: str) -> str:
    """Canonicalize a raw subcategory string.

    Upper-cases, strips, and applies SUBCATEGORY_ALIASES so equivalent
    spellings collapse into one bucket (e.g. CREATOR/INFLUENCER →
    INFLUENCER/CREATOR). Empty / None becomes 'UNCATEGORIZED'.
    """
    if not subcategory:
        return "UNCATEGORIZED"
    sub = str(subcategory).upper().strip()
    return SUBCATEGORY_ALIASES.get(sub, sub)


def get_master_category(subcategory: str) -> str:
    """Return the master bucket for a raw BRAND CATEGORY value.

    Mirrors the UI's getMasterCategory() so backend filters match the
    optgroup the user saw when creating the profile.
    """
    if not subcategory:
        return "OTHER"
    sub = normalize_subcategory(subcategory)
    if sub == "SVOD ACQUISITION":
        return "SVOD ACQUISITION"
    for master, subs in MASTER_CATEGORIES.items():
        if sub in subs:
            return master
    if sub.startswith("SERIES"):
        return "CONTENT"
    return "OTHER"


# ── CW IQ Score weights (env-tunable) ───────────────────────────────────────
CW_IQ_WEIGHT_VOLUME    = float(os.environ.get("CW_IQ_WEIGHT_VOLUME",    "0.45"))
CW_IQ_WEIGHT_REACH     = float(os.environ.get("CW_IQ_WEIGHT_REACH",     "0.20"))
CW_IQ_WEIGHT_SENTIMENT = float(os.environ.get("CW_IQ_WEIGHT_SENTIMENT", "0.20"))
CW_IQ_WEIGHT_MOMENTUM  = float(os.environ.get("CW_IQ_WEIGHT_MOMENTUM",  "0.10"))
CW_IQ_WEIGHT_RECENCY   = float(os.environ.get("CW_IQ_WEIGHT_RECENCY",   "0.05"))

# How many days of history we use as the per-entity baseline for the z-score.
CW_IQ_BASELINE_DAYS    = int(os.environ.get("CW_IQ_BASELINE_DAYS", "28"))

# ── Gen-pop projection ──────────────────────────────────────────────────────
# Mentions / pos / neu / neg are panel-only counts. The Ranker UI projects
# each day's row up to the US adult population (default 329.9M) using a
# per-day dynamic multiplier:
#
#     projected_metric = round(raw_metric * US_POPULATION / panel_size)
#
# panel_size is the number of distinct UIDs that fired ANY event in
# clickstream_final on snapshot_date (captured at ingest time and stored
# alongside the row so reads don't have to re-count). Days with panel_size=0
# (pre-migration rows or fully-empty days) skip projection and show raw.
US_POPULATION          = int(os.environ.get("IQ_RANKER_US_POPULATION", "329900000"))


# ============================================================================
# Profile → brand-terms extraction
# ============================================================================


def read_brand_input_from_csv(s3_client, bucket: str, key: str) -> list[str]:
    """Read just enough of the profile CSV to recover the BRAND INPUT row.

    The BRAND INPUT row sits at the very top of every Profile IQ CSV
    (inserted by bg.py before save), so we only need the first ~32KB of
    the object.
    """
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key, Range="bytes=0-32768")
        body = resp["Body"].read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[iq_rankers] read brand_input from {key} failed: {e}")
        return []
    try:
        reader = csv.reader(io.StringIO(body))
        for row in reader:
            if not row:
                continue
            if (row[0] or "").strip().upper() == "BRAND INPUT":
                value = (row[1] if len(row) > 1 else "") or ""
                return [t.strip() for t in value.split(",") if t.strip()]
    except Exception as e:
        print(f"[iq_rankers] parse brand_input from {key} failed: {e}")
    return []


# ============================================================================
# Sentiment IQ tracker auto-provisioning
# ============================================================================
#
# We don't reinvent the per-tracker storage; we ride on top of sentiment_iq's
# S3 layout (sentiment-iq/trackers/<tracker_id>.json) so existing Sentiment
# IQ trackers a user already created keep working unchanged. The IQ rankers
# tracker is just an "owned by 'iq_rankers'" tracker with ongoing=True.


IQ_TRACKER_OWNER = "iq_rankers"  # marker so we can tell auto-trackers apart


def _tracker_id_for_profile(profile_subject: str) -> str:
    """Deterministic tracker_id so a profile only ever has one auto-tracker."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", (profile_subject or "")).lower()[:48]
    return f"iqr_{safe}" if safe else "iqr_unknown"


def ensure_tracker_for_profile(
    *,
    s3_client,
    sentiment_iq,
    profile_subject: str,
    project_name: str,
    s3_key: str,
    brand_terms: list[str],
) -> dict | None:
    """Ensure an ongoing tracker exists for this profile. Returns the cfg.

    Idempotent: if the tracker already exists we update brand_terms (in case
    they changed since last run) and re-confirm ongoing=True, then return.
    """
    if sentiment_iq is None or not s3_client:
        return None
    if not brand_terms:
        return None
    tid = _tracker_id_for_profile(profile_subject)
    key = sentiment_iq.tracker_key(tid)
    existing = sentiment_iq.s3_get_json(s3_client, key) or None
    if existing:
        changed = False
        if (existing.get("brand_terms") or []) != brand_terms:
            existing["brand_terms"] = brand_terms
            changed = True
        if not existing.get("ongoing"):
            existing["ongoing"] = True
            changed = True
        if existing.get("project_name") != project_name and project_name:
            existing["project_name"] = project_name
            changed = True
        if existing.get("s3_key") != s3_key and s3_key:
            existing["s3_key"] = s3_key
            changed = True
        # Backfill profile_subject for trackers created before this field
        # existed — the frontend's "View in Profile IQ" / "View in CW IQ
        # Ranker" buttons key off this exact string when calling the
        # ranker locate API + spotlight handlers.
        if existing.get("profile_subject") != profile_subject and profile_subject:
            existing["profile_subject"] = profile_subject
            changed = True
        if changed:
            existing["updated_at"] = datetime.utcnow().isoformat() + "Z"
            sentiment_iq.s3_put_json(s3_client, key, existing)
        return existing

    cfg = sentiment_iq.make_tracker_config(
        tracker_id=tid,
        owner=IQ_TRACKER_OWNER,
        project_name=project_name or profile_subject,
        brand_terms=brand_terms,
        competitor_terms=[],
        start_date=None,
        end_date=None,
        ongoing=True,
        alert_email=False,
    )
    cfg["s3_key"] = s3_key
    cfg["profile_subject"] = profile_subject
    sentiment_iq.s3_put_json(s3_client, key, cfg)
    return cfg


# ============================================================================
# Layer-1-only daily metrics for one profile
# ============================================================================


def _term_array_literal(terms: list[str]) -> str:
    cleaned = []
    seen = set()
    for t in terms:
        s = (t or "").lower().replace("'", "''").strip()
        if s and len(s) >= 3 and s not in seen:
            cleaned.append(s)
            seen.add(s)
    if not cleaned:
        return ""
    return ", ".join(f"'{t}'" for t in cleaned)


def compute_layer1_metrics_for_day(
    *,
    ch_connect: Callable,
    sentiment_iq,
    brand_terms: list[str],
    day: str,
    max_events: int = 250_000,
) -> dict[str, Any]:
    """Run Layer-1 scoring against ClickHouse for a single day.

    Returns:
        dict with mentions, unique_uids, pos, neu, neg, net_sentiment.
        On no-match returns zeros (still a valid row).
    """
    out = {
        "mentions": 0, "unique_uids": 0,
        "pos": 0, "neu": 0, "neg": 0,
        "net_sentiment": 0.0,
        "panel_size": 0,
        "streaming_hits": 0,
    }
    term_lit = _term_array_literal(brand_terms)
    if not term_lit:
        return out

    conn = ch_connect(settings={"max_execution_time": 600, "use_skip_indexes": 1})
    try:
        cur = conn.cursor()
        # Pull every event on `day` whose URL or COMMON_NAME mentions any
        # brand term. We don't bother with the audience-UID step here because
        # the Ranker leaderboard is "what got talked about today across the
        # whole panel" — we want the broad mention surface, not an audience-
        # constrained count.
        cur.execute(f"""
            SELECT URL, UID, COMMON_NAME, DOMAIN
            FROM clickstream.clickstream_final
            WHERE DELIVERED = toDate('{day}')
              AND length(URL) > 5
              AND (multiSearchAny(lower(URL), [{term_lit}])
                   OR multiSearchAny(lower(COMMON_NAME), [{term_lit}]))
            LIMIT {int(max_events)}
        """)
        rows = cur.fetchall()
    except Exception as e:
        print(f"[iq_rankers] CH query failed for {day}: {e}")
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not rows:
        return out

    pos = neg = neu = 0
    streaming_hits = 0
    uids: set[str] = set()
    for url, uid, common_name, _domain in rows:
        cn_lc = (common_name or "").strip().lower()
        if cn_lc and cn_lc in STREAMING_PLATFORMS_LC:
            streaming_hits += 1
        try:
            scored = sentiment_iq.score_behavioral_event(url or "", common_name or "")
            bucket = scored.get("sentiment", "neutral")
        except Exception:
            bucket = "neutral"
        if bucket == "positive":
            pos += 1
        elif bucket == "negative":
            neg += 1
        else:
            neu += 1
        if uid:
            uids.add(str(uid))

    total = pos + neg + neu
    out.update({
        "mentions": total,
        "unique_uids": len(uids),
        "pos": pos,
        "neu": neu,
        "neg": neg,
        "net_sentiment": round(100.0 * (pos - neg) / max(total, 1), 2),
        "streaming_hits": streaming_hits,
    })
    return out


# ============================================================================
# CW IQ Score
# ============================================================================


def _z(value: float, mean: float, std: float) -> float:
    """Stable z-score — std=0 collapses to 0 instead of NaN."""
    if std <= 1e-9:
        return 0.0
    return (value - mean) / std


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def compute_cw_iq_score(
    *,
    today: dict[str, float],
    history: list[dict[str, float]],
    snapshot_date: str | None = None,
) -> float:
    """Compose the 0..100 CW IQ Score.

    history is a list of prior-day metric dicts (most-recent first), used
    only as the per-entity baseline for the z-scores. If the entity is
    brand-new (no history), z collapses to 0 and the score is driven by
    sentiment, momentum, and recency alone.
    """
    mentions       = float(today.get("mentions") or 0)
    unique_uids    = float(today.get("unique_uids") or 0)
    net_sentiment  = float(today.get("net_sentiment") or 0)  # -100..+100

    # Per-entity baselines
    h_mentions = [float((d or {}).get("mentions") or 0) for d in history[:CW_IQ_BASELINE_DAYS]]
    h_uids     = [float((d or {}).get("unique_uids") or 0) for d in history[:CW_IQ_BASELINE_DAYS]]
    if h_mentions:
        m_mu = sum(h_mentions) / len(h_mentions)
        m_var = sum((x - m_mu) ** 2 for x in h_mentions) / max(len(h_mentions), 1)
        m_sd = math.sqrt(m_var)
    else:
        m_mu = m_sd = 0.0
    if h_uids:
        u_mu = sum(h_uids) / len(h_uids)
        u_var = sum((x - u_mu) ** 2 for x in h_uids) / max(len(h_uids), 1)
        u_sd = math.sqrt(u_var)
    else:
        u_mu = u_sd = 0.0

    z_volume = _z(math.log1p(mentions),    math.log1p(m_mu), math.log1p(m_sd) or 0.5)
    z_reach  = _z(math.log1p(unique_uids), math.log1p(u_mu), math.log1p(u_sd) or 0.5)

    # Cold-start damping: with <3 days of history, z-scores are unstable —
    # ramp them in linearly so brand-new profiles can't outscore established
    # ones purely on first-day novelty noise.
    history_factor = min(1.0, len(h_mentions) / 3.0) if h_mentions else 0.0
    z_volume *= history_factor
    z_reach  *= history_factor

    # Sentiment normalised to [-1, +1] then to [0, 1] only inside the linear blend.
    sentiment_norm = max(-1.0, min(1.0, net_sentiment / 100.0))

    # Day-over-day momentum: % change vs yesterday, capped at ±2 (clip to keep
    # one extreme day from dominating).
    yest = h_mentions[0] if h_mentions else 0.0
    if yest > 0:
        dod = (mentions - yest) / yest
    else:
        dod = 1.0 if mentions > 0 else 0.0
    dod_capped = max(-2.0, min(2.0, dod))

    # Recency bonus: small constant when there are mentions today, fades for
    # entities that haven't been mentioned in days.
    if mentions > 0:
        recency = 1.0
    elif h_mentions and h_mentions[0] > 0:
        recency = 0.5
    else:
        recency = 0.0

    # Linear combination → sigmoid → 0..100
    raw = (
        CW_IQ_WEIGHT_VOLUME    * z_volume
      + CW_IQ_WEIGHT_REACH     * z_reach
      + CW_IQ_WEIGHT_SENTIMENT * sentiment_norm
      + CW_IQ_WEIGHT_MOMENTUM  * dod_capped
      + CW_IQ_WEIGHT_RECENCY   * recency
    )
    return round(100.0 * _sigmoid(raw), 2)


# ============================================================================
# Persistence helpers
# ============================================================================


def get_history_for_profile(
    *,
    ch_connect: Callable,
    profile_subject: str,
    end_date: str,
    days: int = CW_IQ_BASELINE_DAYS,
) -> list[dict[str, Any]]:
    """Return the last `days` daily rows for this profile_subject, most
    recent first, ending strictly BEFORE end_date."""
    safe = (profile_subject or "").replace("'", "''")
    conn = ch_connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT snapshot_date, mentions, unique_uids, pos, neu, neg,
                   net_sentiment, cw_iq_score
            FROM reference.v_iq_daily_metrics
            WHERE profile_subject = '{safe}'
              AND snapshot_date < toDate('{end_date}')
            ORDER BY snapshot_date DESC
            LIMIT {int(days)}
        """)
        rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [
        {
            "snapshot_date": str(r[0]),
            "mentions": int(r[1] or 0),
            "unique_uids": int(r[2] or 0),
            "pos": int(r[3] or 0),
            "neu": int(r[4] or 0),
            "neg": int(r[5] or 0),
            "net_sentiment": float(r[6] or 0),
            "cw_iq_score": float(r[7] or 0),
        }
        for r in rows
    ]


def _esc_str(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "''")


def _ch_array_literal_str(items: list[str]) -> str:
    parts = ", ".join(f"'{_esc_str(x)}'" for x in (items or []))
    return f"[{parts}]"


def get_panel_size_for_day(*, ch_connect: Callable, day: str) -> int:
    """Return the count of distinct UIDs that fired any clickstream event on
    `day`. Used as the per-day denominator for gen-pop projection (each
    panel mention represents US_POPULATION / panel_size US adults).

    We capture this at ingest time so leaderboard reads don't have to scan
    1B+ rows; the value is stored in profile_iq_daily_metrics.panel_size.
    Returns 0 on error so the projection layer can fall back to raw counts.
    """
    conn = ch_connect(settings={"max_execution_time": 120, "use_skip_indexes": 1})
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT uniqExact(UID) FROM clickstream.clickstream_final "
            f"WHERE DELIVERED = toDate('{day}')"
        )
        row = cur.fetchone()
        return int((row or [0])[0] or 0)
    except Exception as e:
        print(f"[iq_rankers] panel-size query failed for {day}: {e}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def insert_daily_row(
    *,
    ch_connect: Callable,
    snapshot_date: str,
    profile_subject: str,
    project_name: str,
    category: str,
    subcategory: str,
    s3_key: str,
    tracker_id: str,
    brand_terms: list[str],
    metrics: dict[str, Any],
    cw_iq_score: float,
    prev_mentions: int,
    prev_cw_iq_score: float,
    panel_size: int = 0,
) -> bool:
    """Single-row insert using formatted VALUES so the bg-webapp's cursor-
    style ClickHouse wrapper handles it without driver-level parameter
    interpolation. ReplacingMergeTree de-dupes on (date, category, sub,
    profile_subject) so idempotent re-runs replace the previous row.

    panel_size is the count of distinct UIDs active in clickstream on
    snapshot_date — stored alongside each row so the leaderboard can
    project metrics up to gen pop without re-scanning the clickstream.
    """
    conn = ch_connect()
    try:
        cur = conn.cursor()
        sql = f"""
        INSERT INTO reference.profile_iq_daily_metrics
            (snapshot_date, profile_subject, project_name, category, subcategory,
             s3_key, tracker_id, brand_terms, mentions, unique_uids,
             pos, neu, neg, net_sentiment, cw_iq_score,
             prev_mentions, prev_cw_iq_score, panel_size, streaming_hits, generated_at)
        VALUES (
            toDate('{snapshot_date}'),
            '{_esc_str(profile_subject)}',
            '{_esc_str(project_name)}',
            '{_esc_str(category)}',
            '{_esc_str(subcategory)}',
            '{_esc_str(s3_key)}',
            '{_esc_str(tracker_id)}',
            {_ch_array_literal_str(brand_terms)},
            {int(metrics.get("mentions") or 0)},
            {int(metrics.get("unique_uids") or 0)},
            {int(metrics.get("pos") or 0)},
            {int(metrics.get("neu") or 0)},
            {int(metrics.get("neg") or 0)},
            {float(metrics.get("net_sentiment") or 0)},
            {float(cw_iq_score or 0)},
            {int(prev_mentions or 0)},
            {float(prev_cw_iq_score or 0)},
            {int(panel_size or 0)},
            {int(metrics.get("streaming_hits") or 0)},
            now()
        )
        """
        cur.execute(sql)
        return True
    except Exception as e:
        print(f"[iq_rankers] insert daily row failed: {e}")
        traceback.print_exc()
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================================
# Daily orchestration — run for every profile
# ============================================================================


def _iter_profile_jobs(s3_cache_jobs: list[dict]) -> Iterable[dict]:
    """Yield Profile IQ job entries we care about (skip non-profile sources
    and items in purgatory)."""
    seen: set[str] = set()
    for j in s3_cache_jobs or []:
        s3_key = j.get("s3_key") or j.get("job_id") or ""
        if not s3_key:
            continue
        if "purgatory" in s3_key.lower():
            continue
        # We dedupe on profile_subject so multi-year runs of the same person
        # only get one row per day.
        subject = j.get("profile_subject") or ""
        if not subject:
            continue
        if subject in seen:
            continue
        seen.add(subject)
        yield j


def run_daily_for_all_profiles(
    *,
    ch_connect: Callable,
    s3_client,
    sentiment_iq,
    s3_cache_jobs: list[dict],
    s3_bucket: str = S3_DEFAULT_BUCKET,
    snapshot_date: str | None = None,
    only_profile_subject: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """The nightly entry point. For every Profile IQ profile:
        1. Auto-provision tracker if missing.
        2. Run Layer-1 scoring for `snapshot_date` (defaults to yesterday).
        3. Compute CW IQ Score using up to 28d history.
        4. Insert one row in reference.profile_iq_daily_metrics.
    """
    def _log(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:
                pass
        print(f"[iq_rankers] {msg}")

    if not snapshot_date:
        snapshot_date = (date.today() - timedelta(days=1)).isoformat()

    summary = {"snapshot_date": snapshot_date, "ran": 0, "skipped": 0,
               "failed": 0, "details": []}
    started_at = time.time()

    # Compute the day's panel size ONCE up-front and reuse for every profile.
    # Per-day denominator for the gen-pop projection (~10M panelists today).
    panel_size = get_panel_size_for_day(ch_connect=ch_connect, day=snapshot_date)
    summary["panel_size"] = panel_size

    # Materialize the profile list so we can size the thread pool accurately
    # and so the executor doesn't share an iterator across threads.
    profiles: list[dict] = []
    for j in _iter_profile_jobs(s3_cache_jobs or []):
        subject = j.get("profile_subject") or ""
        if only_profile_subject and subject != only_profile_subject:
            continue
        profiles.append(j)

    # Each profile does 3+ ClickHouse round-trips serially (compute, history,
    # insert). With hundreds of profiles that adds up to multiple hours when
    # run sequentially — long enough that the daily cron's urlopen timeout
    # used to fire at 14400s. Parallelize across a small pool to amortize
    # the per-query latency. 4 workers matches what the standalone backfill
    # script uses and is well within ClickHouse HTTP throughput.
    try:
        max_workers = int(os.environ.get("IQ_RANKER_DAILY_WORKERS", "4"))
    except Exception:
        max_workers = 4
    max_workers = max(1, min(max_workers, 16, len(profiles) or 1))

    state_lock = threading.Lock()

    def _process_one(j: dict) -> None:
        subject = j.get("profile_subject") or ""
        s3_key       = j.get("s3_key") or j.get("job_id") or ""
        project_name = j.get("display_name") or j.get("project_name") or subject
        subcategory  = normalize_subcategory(j.get("category"))
        master       = get_master_category(subcategory)
        try:
            terms = read_brand_input_from_csv(s3_client, s3_bucket, s3_key)
            if not terms:
                # Fall back to project name as a single brand term so we still
                # get *something* for profiles that don't have a BRAND INPUT
                # row (older runs). Better than skipping silently.
                if project_name:
                    terms = [project_name]
            if not terms:
                with state_lock:
                    summary["skipped"] += 1
                    summary["details"].append({"subject": subject,
                                               "reason": "no brand_terms"})
                return

            cfg = ensure_tracker_for_profile(
                s3_client=s3_client,
                sentiment_iq=sentiment_iq,
                profile_subject=subject,
                project_name=project_name,
                s3_key=s3_key,
                brand_terms=terms,
            )
            tid = (cfg or {}).get("tracker_id") or _tracker_id_for_profile(subject)

            metrics = compute_layer1_metrics_for_day(
                ch_connect=ch_connect,
                sentiment_iq=sentiment_iq,
                brand_terms=terms,
                day=snapshot_date,
            )
            history = get_history_for_profile(
                ch_connect=ch_connect,
                profile_subject=subject,
                end_date=snapshot_date,
                days=CW_IQ_BASELINE_DAYS,
            )
            cw_iq = compute_cw_iq_score(today=metrics, history=history,
                                        snapshot_date=snapshot_date)
            prev_mentions = history[0]["mentions"] if history else 0
            prev_cw       = history[0]["cw_iq_score"] if history else 0.0

            ok = insert_daily_row(
                ch_connect=ch_connect,
                snapshot_date=snapshot_date,
                profile_subject=subject,
                project_name=project_name,
                category=master,
                subcategory=subcategory,
                s3_key=s3_key,
                tracker_id=tid,
                brand_terms=terms,
                metrics=metrics,
                cw_iq_score=cw_iq,
                prev_mentions=prev_mentions,
                prev_cw_iq_score=prev_cw,
                panel_size=panel_size,
            )
            with state_lock:
                if ok:
                    summary["ran"] += 1
                    summary["details"].append({
                        "subject": subject,
                        "category": master,
                        "subcategory": subcategory,
                        "mentions": metrics["mentions"],
                        "cw_iq": cw_iq,
                    })
                else:
                    summary["failed"] += 1
                    summary["details"].append({"subject": subject,
                                               "reason": "insert failed"})
        except Exception as e:
            traceback.print_exc()
            with state_lock:
                summary["failed"] += 1
                summary["details"].append({"subject": subject, "reason": str(e)[:200]})

    _log(f"processing {len(profiles)} profile(s) for {snapshot_date} "
         f"with {max_workers} worker(s)")

    if max_workers <= 1 or len(profiles) <= 1:
        for j in profiles:
            _process_one(j)
    else:
        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="iqr-daily") as ex:
            futs = [ex.submit(_process_one, j) for j in profiles]
            for _ in as_completed(futs):
                pass

    summary["elapsed_sec"] = round(time.time() - started_at, 1)
    _log(f"daily run done: ran={summary['ran']} skipped={summary['skipped']} "
         f"failed={summary['failed']} in {summary['elapsed_sec']}s")
    return summary


# ============================================================================
# Leaderboard aggregator (used by the API)
# ============================================================================


def _resolve_window(window: str, start: str | None, end: str | None) -> tuple[str, str, str, str]:
    """Translate a window key into (start, end, prev_start, prev_end) date strings.

    Windows: 1d / 7d / 28d / MTD / QTD / YTD / custom.
    The "previous period" used for delta is always the same length immediately
    preceding `start`. For MTD/QTD/YTD we use the equivalent prior period.
    """
    yesterday = date.today() - timedelta(days=1)
    today_obj = date.today()
    w = (window or "").lower()
    if w == "custom":
        if not (start and end):
            start = yesterday.isoformat()
            end = yesterday.isoformat()
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    elif w == "1d":
        s = e = yesterday
    elif w == "7d":
        e = yesterday
        s = e - timedelta(days=6)
    elif w == "28d":
        e = yesterday
        s = e - timedelta(days=27)
    elif w == "mtd":
        e = yesterday
        s = e.replace(day=1)
    elif w == "qtd":
        e = yesterday
        q_start_month = ((e.month - 1) // 3) * 3 + 1
        s = date(e.year, q_start_month, 1)
    elif w == "ytd":
        e = yesterday
        s = date(e.year, 1, 1)
    else:
        # Default: last 7 days
        e = yesterday
        s = e - timedelta(days=6)

    period_len = (e - s).days + 1
    prev_e = s - timedelta(days=1)
    prev_s = prev_e - timedelta(days=period_len - 1)
    return (s.isoformat(), e.isoformat(), prev_s.isoformat(), prev_e.isoformat())


def aggregate_leaderboard(
    *,
    ch_connect: Callable,
    master: str,
    subcategory: str | None = None,
    window: str = "1d",
    start: str | None = None,
    end: str | None = None,
    search: str | None = None,
    sort: str = "cw_iq_score",
    sort_dir: str = "desc",
    limit: int = 500,
) -> dict[str, Any]:
    """Build the leaderboard payload for one Talent / Brands sub-tab."""
    valid_sorts = {
        "rank": "cw_iq_score",
        "name": "project_name",
        "mentions": "mentions",
        "delta_mentions": "delta_mentions",
        "pos": "pos",
        "neu": "neu",
        "neg": "neg",
        "net_sentiment": "net_sentiment",
        "cw_iq_score": "cw_iq_score",
        "delta_cw_iq": "delta_cw_iq",
        "evc_score": "evc_score",
        "evc": "evc_score",
    }
    sort_col = valid_sorts.get((sort or "").lower(), "cw_iq_score")
    direction = "DESC" if (sort_dir or "desc").lower() != "asc" else "ASC"

    s, e, ps, pe = _resolve_window(window, start, end)
    master_clean = (master or "").replace("'", "''").upper()
    sub_clean = (subcategory or "").replace("'", "''").upper().strip()
    where_master = f"upper(category) = '{master_clean}'"
    where_sub    = f"AND upper(subcategory) = '{sub_clean}'" if sub_clean and sub_clean != "ALL" else ""
    where_search = ""
    if search:
        s_clean = search.replace("'", "''").lower()
        where_search = (f"AND (positionCaseInsensitive(project_name, '{s_clean}') > 0 "
                        f"OR positionCaseInsensitive(profile_subject, '{s_clean}') > 0)")

    # CW IQ Score across a multi-day window: mention-weighted average so
    # days with very low / partial clickstream data (e.g. yesterday before
    # the nightly pipeline finishes loading) contribute proportionally
    # little. Falls back to a plain average if total mentions is zero so a
    # quiet entity still gets a reasonable composite score.
    #
    # We alias aggregates to *_lbl / *_sum / *_calc to avoid name collision
    # with the source columns referenced in WHERE (ClickHouse complains
    # otherwise: "Aggregate function ... AS X is found in WHERE").
    # Mentions / pos / neu / neg are projected up to gen pop with a per-day
    # multiplier  ({US_POP} / panel_size). We sum the projected counts so
    # multi-day windows aggregate apples-to-apples even when daily panel
    # sizes drift. Days with panel_size = 0 (pre-migration rows or fully
    # empty days) skip projection (treat multiplier as 1) so old data is
    # still readable instead of dropping out.
    us_pop = int(US_POPULATION)
    proj_mentions = (f"toUInt64(round(mentions    * {us_pop} / panel_size))")
    proj_pos      = (f"toUInt64(round(pos         * {us_pop} / panel_size))")
    proj_neu      = (f"toUInt64(round(neu         * {us_pop} / panel_size))")
    proj_neg      = (f"toUInt64(round(neg         * {us_pop} / panel_size))")
    proj_uids     = (f"toUInt64(round(unique_uids * {us_pop} / panel_size))")
    # Wrap each projection in if(panel_size > 0, projected, raw) so legacy
    # rows that don't have a panel_size still surface their raw counts.
    def _wrap(raw_col: str, projected: str) -> str:
        return f"if(panel_size > 0, {projected}, {raw_col})"
    p_mentions = _wrap("mentions",    proj_mentions)
    p_pos      = _wrap("pos",         proj_pos)
    p_neu      = _wrap("neu",         proj_neu)
    p_neg      = _wrap("neg",         proj_neg)
    p_uids     = _wrap("unique_uids", proj_uids)

    sql = f"""
    WITH curr AS (
        SELECT profile_subject,
               anyHeavy(project_name)             AS project_name_lbl,
               anyHeavy(category)                 AS category_lbl,
               anyHeavy(subcategory)              AS subcategory_lbl,
               anyHeavy(s3_key)                   AS s3_key_lbl,
               sum({p_mentions})                  AS mentions_sum,
               sum({p_uids})                      AS unique_uids_sum,
               sum(unique_uids)                   AS raw_unique_uids_sum,
               sum({p_pos})                       AS pos_sum,
               sum({p_neu})                       AS neu_sum,
               sum({p_neg})                       AS neg_sum,
               sum(mentions)                      AS raw_mentions_sum,
               max(panel_size)                    AS max_panel_size,
               sum(streaming_hits)                AS streaming_hits_sum,
               -- EVC = Engagement vs Viewing Correlation: % of this
               -- profile's mention rows whose COMMON_NAME is a streaming
               -- platform (Netflix, Hulu, YouTube, etc). Reads as "of
               -- the times the panel touched a URL referencing this
               -- person/brand, what share of those touches happened on
               -- a streaming destination". Uses RAW mentions in the
               -- denominator so the ratio is honest at the panel level.
               -- Bounded to [0, 100]; falls back to 0 when no mentions.
               if(sum(mentions) > 0,
                  round(100.0 * sum(streaming_hits) / sum(mentions), 1),
                  0.0)                            AS evc_score_calc,
               round(100.0 * (sum({p_pos}) - sum({p_neg}))
                     / greatest(sum({p_pos}) + sum({p_neu}) + sum({p_neg}), 1), 2)
                                                  AS net_sentiment_calc,
               -- Mention-weighted CW IQ over the window (weight by RAW
               -- mentions so a partial-ingest day with low panel size
               -- contributes proportionally less). If the window has zero
               -- mentions for this profile, fall back to a plain average.
               if(sum(mentions) > 0,
                  round(sumOrNull(cw_iq_score * mentions) / sum(mentions), 2),
                  round(avg(cw_iq_score), 2))     AS cw_iq_score_calc
        FROM reference.v_iq_daily_metrics
        WHERE snapshot_date BETWEEN toDate('{s}') AND toDate('{e}')
          AND {where_master}
          {where_sub}
          {where_search}
        GROUP BY profile_subject
    ),
    prev AS (
        SELECT profile_subject,
               sum({p_mentions})                  AS prev_mentions_sum,
               if(sum(mentions) > 0,
                  round(sumOrNull(cw_iq_score * mentions) / sum(mentions), 2),
                  round(avg(cw_iq_score), 2))     AS prev_cw_iq_score_calc
        FROM reference.v_iq_daily_metrics
        WHERE snapshot_date BETWEEN toDate('{ps}') AND toDate('{pe}')
          AND {where_master}
          {where_sub}
        GROUP BY profile_subject
    )
    SELECT c.profile_subject                          AS profile_subject,
           c.project_name_lbl                         AS project_name,
           c.category_lbl                             AS category,
           c.subcategory_lbl                          AS subcategory,
           c.s3_key_lbl                               AS s3_key,
           c.mentions_sum                             AS mentions,
           c.unique_uids_sum                          AS unique_uids,
           c.pos_sum                                  AS pos,
           c.neu_sum                                  AS neu,
           c.neg_sum                                  AS neg,
           c.net_sentiment_calc                       AS net_sentiment,
           c.cw_iq_score_calc                         AS cw_iq_score,
           coalesce(p.prev_mentions_sum, 0)           AS prev_mentions,
           coalesce(p.prev_cw_iq_score_calc, 0)       AS prev_cw_iq_score,
           toInt64(c.mentions_sum) - toInt64(coalesce(p.prev_mentions_sum, 0))
                                                      AS delta_mentions,
           c.cw_iq_score_calc - coalesce(p.prev_cw_iq_score_calc, 0)
                                                      AS delta_cw_iq,
           c.raw_mentions_sum                         AS raw_mentions,
           c.raw_unique_uids_sum                      AS raw_unique_uids,
           c.max_panel_size                           AS panel_size,
           c.streaming_hits_sum                       AS streaming_hits,
           c.evc_score_calc                           AS evc_score
    FROM curr c
    LEFT JOIN prev p ON p.profile_subject = c.profile_subject
    ORDER BY {sort_col} {direction}, mentions DESC
    LIMIT {int(limit)}
    """

    # Let ClickHouse exceptions bubble up to the Flask route so the API
    # returns a real 5xx instead of a 200 with empty rows that the UI
    # silently shows as "No data yet". The route's outer try/except logs
    # and serializes the error for the client.
    conn = ch_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out_rows = []
    for i, r in enumerate(rows, start=1):
        out_rows.append({
            "rank": i,
            "profile_subject":  r[0],
            "project_name":     r[1],
            "category":         r[2],
            "subcategory":      r[3],
            "s3_key":           r[4],
            # Mentions / unique_uids / pos / neu / neg are PROJECTED to gen
            # pop (US_POPULATION / panel_size per day). raw_mentions,
            # raw_unique_uids + panel_size are also returned so the UI can
            # show "X panel mentions / N panel ≈ Y projected" if ever needed.
            "mentions":         int(r[5] or 0),
            "unique_uids":      int(r[6] or 0),
            "pos":              int(r[7] or 0),
            "neu":              int(r[8] or 0),
            "neg":              int(r[9] or 0),
            "net_sentiment":    float(r[10] or 0),
            "cw_iq_score":      float(r[11] or 0),
            "prev_mentions":    int(r[12] or 0),
            "prev_cw_iq_score": float(r[13] or 0),
            "delta_mentions":   int(r[14] or 0),
            "delta_cw_iq":      round(float(r[15] or 0), 2),
            "raw_mentions":     int(r[16] or 0) if len(r) > 16 else 0,
            "raw_unique_uids":  int(r[17] or 0) if len(r) > 17 else 0,
            "panel_size":       int(r[18] or 0) if len(r) > 18 else 0,
            "streaming_hits":   int(r[19] or 0) if len(r) > 19 else 0,
            "evc_score":        float(r[20] or 0) if len(r) > 20 else 0.0,
        })
    return {
        "rows": out_rows,
        "window": {
            "start": s, "end": e,
            "prev_start": ps, "prev_end": pe,
            "window_key": window,
        },
        "filters": {
            "master": master,
            "subcategory": subcategory or "",
            "search": search or "",
        },
        "sort": {"by": sort, "direction": direction},
        "projection": {
            "us_population": US_POPULATION,
            "note": "mentions/unique_uids/pos/neu/neg are projected to gen "
                    "pop using US_POPULATION / per-day panel_size; "
                    "cw_iq_score and net_sentiment are panel-relative "
                    "measures so they're unchanged. raw_mentions and "
                    "raw_unique_uids are also returned for any caller that "
                    "needs the underlying panel counts.",
        },
        "evc_formula": {
            "name": "Engagement vs Viewing Correlation",
            "description": (
                "Of the panel's URL hits that mentioned this profile in "
                "the selected window, the share whose COMMON_NAME is a "
                "streaming or VOD platform (Netflix, Hulu, Disney+, HBO "
                "Max, Prime Video, Apple TV+, Paramount+, Peacock, "
                "YouTube, Tubi, etc). Reads as: 'of all the times the "
                "panel touched something about this person/brand, what "
                "% of those touches happened on a streaming destination'. "
                "A high EVC means the audience is actively consuming "
                "this entity's content rather than just reading about it."
            ),
            "formula": "EVC = 100 * sum(streaming_hits) / sum(raw_mentions)",
            "platforms_count": len(STREAMING_PLATFORMS_LC),
        },
        "cw_iq_formula": {
            "weights": {
                "volume":    CW_IQ_WEIGHT_VOLUME,
                "reach":     CW_IQ_WEIGHT_REACH,
                "sentiment": CW_IQ_WEIGHT_SENTIMENT,
                "momentum":  CW_IQ_WEIGHT_MOMENTUM,
                "recency":   CW_IQ_WEIGHT_RECENCY,
            },
            "baseline_days": CW_IQ_BASELINE_DAYS,
            "description": (
                "0..100 sigmoid of a weighted z-score blend, computed per "
                "(profile, day):\n"
                "  z_volume    = z-score of log1p(mentions) vs that profile's "
                "trailing 28-day baseline\n"
                "  z_reach     = z-score of log1p(unique_uids) vs same baseline\n"
                "  sentiment   = (pos - neg) / total, clamped to [-1, +1]\n"
                "  momentum    = day-over-day mention % change, clamped to [-2, +2]\n"
                "  recency     = 1 if mentioned today, 0.5 if mentioned yesterday, else 0\n"
                "Cold-start damping: z_volume and z_reach are scaled by "
                "min(1, days_of_history / 3) so brand-new profiles don't "
                "outrank established ones on first-day novelty noise.\n"
                "Final = 100 * sigmoid(0.45·z_volume + 0.20·z_reach + "
                "0.20·sentiment + 0.10·momentum + 0.05·recency)"
            ),
        },
    }


def fetch_profile_timeseries(
    *,
    ch_connect: Callable,
    profile_subject: str,
    metric: str = "cw_iq_score",
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Daily series for one profile (for sparklines / drilldown).

    Count-bearing metrics (mentions, unique_uids, pos, neu, neg) are
    gen-pop projected per-day (US_POPULATION / panel_size) so the
    series matches the leaderboard + drill-down chart units. Days
    where panel_size = 0 fall back to the raw count so legacy rows
    still render. net_sentiment and cw_iq_score are panel-relative
    composites and are returned unchanged.
    """
    valid = {"cw_iq_score", "mentions", "unique_uids", "pos", "neu", "neg",
             "net_sentiment"}
    metric = metric if metric in valid else "cw_iq_score"
    if not end:
        end = (date.today() - timedelta(days=1)).isoformat()
    if not start:
        start = (date.fromisoformat(end) - timedelta(days=89)).isoformat()
    safe = (profile_subject or "").replace("'", "''")
    projectable = {"mentions", "unique_uids", "pos", "neu", "neg"}
    if metric in projectable:
        us_pop = int(US_POPULATION)
        metric_expr = (
            f"if(panel_size > 0, "
            f"toUInt64(round({metric} * {us_pop} / panel_size)), {metric})"
        )
    else:
        metric_expr = metric
    conn = ch_connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT snapshot_date, {metric_expr}
            FROM reference.v_iq_daily_metrics
            WHERE profile_subject = '{safe}'
              AND snapshot_date BETWEEN toDate('{start}') AND toDate('{end}')
            ORDER BY snapshot_date ASC
        """)
        rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return [{"date": str(r[0]), "value": float(r[1] or 0)} for r in rows]


def fetch_profile_full_timeseries(
    *,
    ch_connect: Callable,
    profile_subject: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """All metrics for one profile in a single round-trip — used by the
    leaderboard drill-down chart.

    Returns a dict shaped for direct JSON consumption:
        {
            "profile_subject": str,
            "project_name": str | None,
            "category": str | None,
            "subcategory": str | None,
            "us_population": int,
            "rows": [{
                "date": "YYYY-MM-DD",
                "mentions_raw":     int,    # panel-only count
                "mentions":         int,    # projected to gen pop
                "pos":              int,    # projected
                "neu":              int,    # projected
                "neg":              int,    # projected
                "net_sentiment":    float,  # -100..+100 (projection-invariant)
                "cw_iq_score":      float,  # 0..100 (projection-invariant)
                "unique_uids_raw":  int,    # panel-only reach
                "unique_uids":      int,    # projected to gen-pop reach
                "panel_size":       int,
            }, ...] (chronologically ordered)
        }

    Mentions / pos / neu / neg / unique_uids are projected day-by-day
    (per-row multiplier = US_POPULATION / panel_size). Days where
    panel_size = 0 fall back to raw counts so legacy rows still render.
    """
    if not end:
        end = (date.today() - timedelta(days=1)).isoformat()
    if not start:
        start = (date.fromisoformat(end) - timedelta(days=89)).isoformat()
    safe = (profile_subject or "").replace("'", "''")
    us_pop = int(US_POPULATION)
    conn = ch_connect()
    try:
        cur = conn.cursor()
        # v_iq_daily_metrics is already (snapshot_date, profile_subject)-
        # deduplicated by the view's argMax aggregation, so we don't need
        # an outer GROUP BY here. Per-row projection (panel_size = 0 falls
        # back to raw counts so legacy rows still surface).
        cur.execute(f"""
            SELECT
                toString(snapshot_date),
                mentions,
                if(panel_size > 0, toUInt64(round(mentions    * {us_pop} / panel_size)), mentions),
                if(panel_size > 0, toUInt64(round(pos         * {us_pop} / panel_size)), pos),
                if(panel_size > 0, toUInt64(round(neu         * {us_pop} / panel_size)), neu),
                if(panel_size > 0, toUInt64(round(neg         * {us_pop} / panel_size)), neg),
                net_sentiment,
                cw_iq_score,
                unique_uids,
                if(panel_size > 0, toUInt64(round(unique_uids * {us_pop} / panel_size)), unique_uids),
                panel_size,
                project_name,
                category,
                subcategory
            FROM reference.v_iq_daily_metrics
            WHERE profile_subject = '{safe}'
              AND snapshot_date BETWEEN toDate('{start}') AND toDate('{end}')
            ORDER BY snapshot_date ASC
        """)
        rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out_rows: list[dict[str, Any]] = []
    project_name = category = subcategory = None
    for r in rows:
        out_rows.append({
            "date":             str(r[0]),
            "mentions_raw":     int(r[1] or 0),
            "mentions":         int(r[2] or 0),
            "pos":              int(r[3] or 0),
            "neu":              int(r[4] or 0),
            "neg":              int(r[5] or 0),
            "net_sentiment":    float(r[6] or 0),
            "cw_iq_score":      float(r[7] or 0),
            "unique_uids_raw":  int(r[8] or 0),
            # unique_uids is gen-pop projected to match mentions / pos / neu /
            # neg; the raw panel count is still available as unique_uids_raw.
            "unique_uids":      int(r[9] or 0),
            "panel_size":       int(r[10] or 0),
        })
        # First-seen labels (every row in this view has them already
        # resolved via argMax, so the first one is fine).
        if project_name is None: project_name = r[11] or None
        if category    is None: category     = r[12] or None
        if subcategory is None: subcategory  = r[13] or None

    return {
        "profile_subject": profile_subject,
        "project_name":    project_name,
        "category":        category,
        "subcategory":     subcategory,
        "us_population":   us_pop,
        "start":           start,
        "end":             end,
        "rows":            out_rows,
    }


# ============================================================================
# 30-day backfill (one-shot)
# ============================================================================


def backfill_recent_days(
    *,
    ch_connect: Callable,
    s3_client,
    sentiment_iq,
    s3_cache_jobs: list[dict],
    s3_bucket: str = S3_DEFAULT_BUCKET,
    days: int = 30,
    only_profile_subject: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Re-run run_daily_for_all_profiles for each of the last `days` days
    (oldest first so CW IQ baselines build up correctly)."""
    summaries = []
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=max(days - 1, 0))
    cur_d = start
    while cur_d <= end:
        s = run_daily_for_all_profiles(
            ch_connect=ch_connect,
            s3_client=s3_client,
            sentiment_iq=sentiment_iq,
            s3_cache_jobs=s3_cache_jobs,
            s3_bucket=s3_bucket,
            snapshot_date=cur_d.isoformat(),
            only_profile_subject=only_profile_subject,
            log=log,
        )
        summaries.append(s)
        cur_d += timedelta(days=1)
    return {
        "days": days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "summaries": summaries,
        "totals": {
            "ran":     sum(s["ran"]     for s in summaries),
            "skipped": sum(s["skipped"] for s in summaries),
            "failed":  sum(s["failed"]  for s in summaries),
        },
    }


# ============================================================================
# Public API surface (for app.py imports)
# ============================================================================
__all__ = [
    "MASTER_CATEGORIES",
    "get_master_category",
    "ensure_tracker_for_profile",
    "read_brand_input_from_csv",
    "compute_layer1_metrics_for_day",
    "compute_cw_iq_score",
    "run_daily_for_all_profiles",
    "backfill_recent_days",
    "aggregate_leaderboard",
    "fetch_profile_timeseries",
]
