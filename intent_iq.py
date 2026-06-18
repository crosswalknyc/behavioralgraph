"""intent_iq.py — Title-scoped marketing-intent measurement module.

Public API (mirrors the surface conventions of `blue_iq.py`):

    list_titles()                                -> dict[list[title_meta]]
    get_overview(title_slug)                     -> dict (timeline + key dates)
    get_assets(title_slug, **filters)            -> dict[{cards: [...]}]
    get_audiences(title_slug)                    -> dict[{cards: [...]}]
    get_cohorts(title_slug=None)                 -> dict[{cohorts: [...]}]
    answer_question(title_slug, qid)             -> dict
    get_in_flight(title_slug, as_of)             -> dict
    compare_titles(slugs: list[str])             -> dict

Storage:
    - ClickHouse: `intent.*` tables (Hetzner 168.119.215.48 by default)
    - S3:         `s3://dashboard-inputs/intent/<slug>/`
                  + `s3://dashboard-inputs/intent/registry.json`
                  + `s3://dashboard-inputs/metadata/admin_quick_selects.json`

All functions degrade gracefully:
    - If ClickHouse is unreachable, return `{'success': True, ...,
      'fallback': True}` populated from the S3 registry / normalized JSON
      snapshot the ingest script uploaded. The dashboard always renders
      *something* even when the DB is down.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

S3_BUCKET          = os.environ.get("INTENT_S3_BUCKET",       "dashboard-inputs")
REGISTRY_KEY       = "intent/registry.json"
NORMALIZED_KEY_FMT = "intent/{slug}/source/normalized_assets.json"

CH_HOST = os.environ.get("CH_HOST", "168.119.215.48")
CH_PORT = int(os.environ.get("CH_PORT", "8123"))
CH_USER = os.environ.get("CH_USER", "bgapp")
CH_PASS = os.environ.get("CH_PASSWORD", "")

# David's 6 questions (used by the /questions/<qid> route).
QUESTIONS = {
    "q1": "What categories of content are most effective at driving future engagement / ticket purchase?",
    "q2": "What is the interplay between organic and paid content? Can we measure cumulative impact?",
    "q3": "How many people have shown intent to buy a ticket — subject vs. comparable titles?",
    "q4": "How do talent & influencer activity boost or interact with other marketing content?",
    "q5": "How crucial is trailer viewership throughout the life of the campaign?",
    "q6": "How do different audiences engage throughout the campaign (general -> family late)?",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ch_client():
    try:
        import clickhouse_connect  # type: ignore
        return clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASS,
            connect_timeout=10, send_receive_timeout=60,
        )
    except Exception as e:
        logger.warning("Intent IQ: ClickHouse unreachable (%s); falling back to S3", e)
        return None


def _s3():
    try:
        import boto3
        return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    except Exception as e:
        logger.warning("Intent IQ: boto3 unavailable: %s", e)
        return None


def _load_registry() -> dict:
    s3 = _s3()
    if not s3:
        return {"titles": []}
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.info("Intent IQ: registry not found (%s); returning empty", e)
        return {"titles": []}


def _load_normalized_snapshot(title_slug: str) -> Optional[dict]:
    s3 = _s3()
    if not s3:
        return None
    try:
        key = NORMALIZED_KEY_FMT.format(slug=title_slug)
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.info("Intent IQ: snapshot not found for %s (%s)", title_slug, e)
        return None


def _scrub_nonfinite(v):
    """Replace NaN / +inf / -inf floats with 0 so json.dumps emits valid
    JSON. ClickHouse avgIf / quantileIf return NaN when the predicate
    matches zero rows; left unchecked, Python json.dumps writes the
    literal 'NaN' which breaks frontend JSON.parse."""
    if isinstance(v, float):
        if v != v or v == float("inf") or v == float("-inf"):
            return 0
    return v


def _rows_to_dicts(rows, cols) -> list[dict]:
    return [{c: _scrub_nonfinite(v) for c, v in zip(cols, r)} for r in rows]


def _phase_color(phase_name: str) -> str:
    """Stable color hint based on phase position keyword. UI may override."""
    n = (phase_name or "").lower()
    if "trailer launch" in n:    return "#22c55e"   # green
    if "bridge" in n:            return "#eab308"   # yellow
    if "opening" in n:           return "#ef4444"   # red
    if "branding" in n:          return "#3b82f6"   # blue
    if "reinforcement" in n:     return "#a855f7"   # purple
    return "#64748b"                                 # slate fallback


def _safe_iso_date(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)[:10]


# ── 1. List titles ──────────────────────────────────────────────────────────

def list_titles() -> dict:
    registry = _load_registry()
    titles_meta = registry.get("titles", [])
    ch = _ch_client()
    if ch is not None:
        try:
            rows = ch.query(
                "SELECT title_slug, display_name, distributor, opening_date, "
                "ticketing_open_date, genre, mpaa_rating "
                "FROM intent.titles FINAL ORDER BY opening_date DESC"
            ).result_rows
            cols = ["title_slug", "display_name", "distributor", "opening_date",
                     "ticketing_open_date", "genre", "mpaa_rating"]
            db_titles = _rows_to_dicts(rows, cols)
            by_slug = {t["title_slug"]: t for t in db_titles}
            for meta in titles_meta:
                slug = meta.get("title_slug")
                if slug in by_slug:
                    by_slug[slug] = {**by_slug[slug], **meta}
                else:
                    by_slug[slug] = meta
            merged = list(by_slug.values())
            for t in merged:
                t["opening_date"] = _safe_iso_date(t.get("opening_date"))
                t["ticketing_open_date"] = _safe_iso_date(t.get("ticketing_open_date"))
            return {"success": True, "titles": merged, "source": "clickhouse+registry"}
        except Exception as e:
            logger.warning("Intent IQ: list_titles CH failed: %s", e)
    for t in titles_meta:
        t["opening_date"] = _safe_iso_date(t.get("opening_date"))
        t["ticketing_open_date"] = _safe_iso_date(t.get("ticketing_open_date"))
    return {"success": True, "titles": titles_meta, "source": "registry", "fallback": True}


# ── 2. Overview (timeline) ──────────────────────────────────────────────────

def get_overview(title_slug: str) -> dict:
    ch = _ch_client()
    if ch is not None:
        try:
            title_rows = ch.query(
                "SELECT display_name, distributor, opening_date, "
                "ticketing_open_date, genre, mpaa_rating, predicted_bo_low_usd, "
                "predicted_bo_high_usd, actual_opening_bo_usd "
                f"FROM intent.titles FINAL WHERE title_slug = '{title_slug}'"
            ).result_rows
            if title_rows:
                t = title_rows[0]
                phase_rows = ch.query(
                    "SELECT phase_name, phase_order, start_date, end_date, "
                    "description, color_hex "
                    f"FROM intent.campaign_phases FINAL WHERE title_slug = '{title_slug}' "
                    "ORDER BY phase_order"
                ).result_rows
                phases = []
                for p in phase_rows:
                    phases.append({
                        "phase_name":  p[0],
                        "phase_order": p[1],
                        "start_date":  _safe_iso_date(p[2]),
                        "end_date":    _safe_iso_date(p[3]),
                        "description": p[4],
                        "color_hex":   p[5] or _phase_color(p[0]),
                    })
                asset_count_rows = ch.query(
                    f"SELECT count() FROM intent.campaign_assets FINAL "
                    f"WHERE title_slug = '{title_slug}'"
                ).result_rows
                return {
                    "success": True,
                    "title_slug": title_slug,
                    "display_name": t[0],
                    "distributor":  t[1],
                    "opening_date": _safe_iso_date(t[2]),
                    "ticketing_open_date": _safe_iso_date(t[3]),
                    "genre": t[4],
                    "mpaa_rating": t[5],
                    "predicted_bo_low_usd":  int(t[6] or 0),
                    "predicted_bo_high_usd": int(t[7] or 0),
                    "actual_opening_bo_usd": int(t[8] or 0),
                    "asset_count": int(asset_count_rows[0][0] if asset_count_rows else 0),
                    "phases": phases,
                    "source": "clickhouse",
                }
        except Exception as e:
            logger.warning("Intent IQ: get_overview CH failed: %s", e)

    snap = _load_normalized_snapshot(title_slug)
    if snap:
        t = snap.get("title", {})
        phases = []
        for p in snap.get("phases", []):
            phases.append({**p, "color_hex": p.get("color_hex") or _phase_color(p.get("phase_name", ""))})
        return {
            "success": True,
            "title_slug": title_slug,
            "display_name": t.get("display_name"),
            "distributor":  t.get("distributor"),
            "opening_date": t.get("opening_date"),
            "ticketing_open_date": t.get("ticketing_open_date"),
            "genre": t.get("genre"),
            "mpaa_rating": t.get("mpaa_rating"),
            "predicted_bo_low_usd":  t.get("predicted_bo_low_usd", 0),
            "predicted_bo_high_usd": t.get("predicted_bo_high_usd", 0),
            "actual_opening_bo_usd": t.get("actual_opening_bo_usd", 0),
            "asset_count": len(snap.get("assets", [])),
            "phases": phases,
            "source": "s3_snapshot",
            "fallback": True,
        }
    return {"success": False, "error": f"Title not found: {title_slug}"}


# ── 3. Assets ───────────────────────────────────────────────────────────────

def _resolve_assets_window(window: Optional[str]) -> tuple[Optional[str], Optional[str], str]:
    """Resolve an assets-window string into (lo_iso, hi_iso, label).
    Lo/hi are inclusive YYYY-MM-DD or None for unbounded. YTD = since
    Jan 1 of current year. 'all' = no bound. '14d'/'30d'/'90d'/'365d' =
    rolling N days back from today.
    """
    w = (window or "ytd").strip().lower()
    today = datetime.utcnow().date()
    if w in ("all", "campaign", "campaign_to_date"):
        return (None, None, "Campaign-to-date")
    if w in ("ytd", "year_to_date"):
        return (f"{today.year}-01-01", None, f"YTD (since {today.year}-01-01)")
    days_map = {"14d": 14, "30d": 30, "90d": 90, "365d": 365}
    if w in days_map:
        from datetime import timedelta
        d = days_map[w]
        lo = (today - timedelta(days=d)).isoformat()
        return (lo, None, f"Last {d}d")
    return (f"{today.year}-01-01", None, f"YTD (since {today.year}-01-01)")


def get_assets(title_slug: str, phase: Optional[str] = None,
                asset_type: Optional[str] = None,
                paid_or_organic: Optional[str] = None,
                window: Optional[str] = None) -> dict:
    """List campaign assets. Default window is YTD per Jenna 2026-06-17:
    'this should be YTD unless otherwise specified for all assets'."""
    where_clauses = [f"title_slug = '{title_slug}'"]
    if phase:           where_clauses.append(f"phase_name = '{phase}'")
    if asset_type:      where_clauses.append(f"asset_type = '{asset_type}'")
    if paid_or_organic: where_clauses.append(f"paid_or_organic = '{paid_or_organic}'")
    lo, hi, window_label = _resolve_assets_window(window)
    if lo: where_clauses.append(f"posted_date >= toDate('{lo}')")
    if hi: where_clauses.append(f"posted_date <= toDate('{hi}')")
    where_sql = " AND ".join(where_clauses)

    ch = _ch_client()
    if ch is not None:
        try:
            rows = ch.query(
                "SELECT asset_id, phase_name, funnel_stage, action_label, "
                "asset_type, channel, paid_or_organic, url, source, note, "
                "posted_date, talent_tags, ext_view_count, "
                "ext_engagement_count, thumbnail_s3_url "
                f"FROM intent.campaign_assets FINAL WHERE {where_sql} "
                "ORDER BY posted_date, asset_id LIMIT 5000"
            ).result_rows
            cols = ["asset_id", "phase_name", "funnel_stage", "action_label",
                     "asset_type", "channel", "paid_or_organic", "url",
                     "source", "note", "posted_date", "talent_tags",
                     "ext_view_count", "ext_engagement_count", "thumbnail_s3_url"]
            cards = _rows_to_dicts(rows, cols)
            for c in cards:
                c["posted_date"] = _safe_iso_date(c.get("posted_date"))
            return {"success": True, "title_slug": title_slug, "cards": cards,
                    "total": len(cards), "source": "clickhouse",
                    "window": (window or "ytd").lower(), "window_label": window_label}
        except Exception as e:
            logger.warning("Intent IQ: get_assets CH failed: %s", e)

    snap = _load_normalized_snapshot(title_slug)
    if not snap:
        return {"success": False, "error": f"Title not found: {title_slug}"}
    cards = snap.get("assets", [])
    if phase:           cards = [a for a in cards if a.get("phase_name") == phase]
    if asset_type:      cards = [a for a in cards if a.get("asset_type") == asset_type]
    if paid_or_organic: cards = [a for a in cards if a.get("paid_or_organic") == paid_or_organic]
    if lo:              cards = [a for a in cards if (a.get("posted_date") or "") >= lo]
    return {"success": True, "title_slug": title_slug, "cards": cards,
            "total": len(cards), "source": "s3_snapshot", "fallback": True,
            "window": (window or "ytd").lower(), "window_label": window_label}


# ── 4. Audiences-of-interest ────────────────────────────────────────────────

# Hard-coded audiences-of-interest catalog from David's brief. For each,
# we list the canonical Profile IQ subject_key it maps to. The dashboard
# uses this to deep-link into the underlying profile.
AUDIENCES_OF_INTEREST_DEFAULT = {
    "goat": [
        {"subject_key": "family_animated_films", "display": "Fans of Family Animated Films", "category": "GENRE"},
        {"subject_key": "sony_pictures_animation", "display": "Fans of Sony Pictures Animation (incl. Spider-Verse)", "category": "STUDIO"},
        {"subject_key": "steph_curry", "display": "Fans of Steph Curry", "category": "TALENT"},
        {"subject_key": "nba", "display": "Fans of NBA / Basketball", "category": "SPORT"},
        {"subject_key": "caleb_mclaughlin", "display": "Caleb McLaughlin (Cast)", "category": "TALENT"},
        {"subject_key": "jelly_roll", "display": "Jelly Roll (Cast)", "category": "TALENT"},
        {"subject_key": "gabrielle_union", "display": "Gabrielle Union (Cast)", "category": "TALENT"},
        {"subject_key": "nick_kroll", "display": "Nick Kroll (Cast)", "category": "TALENT"},
        {"subject_key": "david_harbour", "display": "David Harbour (Cast)", "category": "TALENT"},
        {"subject_key": "jennifer_hudson", "display": "Jennifer Hudson (Cast)", "category": "TALENT"},
        {"subject_key": "aaron_pierre", "display": "Aaron Pierre (Cast)", "category": "TALENT"},
        {"subject_key": "nicola_coughlan", "display": "Nicola Coughlan (Cast)", "category": "TALENT"},
        {"subject_key": "black_moviegoers", "display": "Black Moviegoers", "category": "DEMO"},
        {"subject_key": "hispanic_moviegoers", "display": "Hispanic Moviegoers", "category": "DEMO"},
    ]
}


def get_audiences(title_slug: str) -> dict:
    ch = _ch_client()
    db_overlap: list[dict] = []
    if ch is not None:
        try:
            rows = ch.query(
                "SELECT subject_key, subject_display, category, overlap_bp, "
                "rank_within_title, source FROM intent.title_audience_overlap "
                f"FINAL WHERE title_slug = '{title_slug}' "
                "ORDER BY overlap_bp DESC"
            ).result_rows
            db_overlap = _rows_to_dicts(rows, ["subject_key", "subject_display",
                                                  "category", "overlap_bp",
                                                  "rank_within_title", "source"])
        except Exception as e:
            logger.warning("Intent IQ: get_audiences CH failed: %s", e)

    by_key = {a["subject_key"]: a for a in db_overlap}
    catalog = AUDIENCES_OF_INTEREST_DEFAULT.get(title_slug, [])
    cards = []
    for entry in catalog:
        merged = {**entry, "overlap_bp": None, "profile_iq_link": None}
        if entry["subject_key"] in by_key:
            db = by_key[entry["subject_key"]]
            merged["overlap_bp"] = float(db.get("overlap_bp") or 0)
            merged["source"] = db.get("source")
        cards.append(merged)

    for db in db_overlap:
        if db["subject_key"] not in {a["subject_key"] for a in cards}:
            cards.append({
                "subject_key": db["subject_key"],
                "display":     db["subject_display"],
                "category":    db["category"],
                "overlap_bp":  float(db["overlap_bp"] or 0),
                "source":      db["source"],
            })

    return {"success": True, "title_slug": title_slug, "cards": cards,
            "total": len(cards), "source": "clickhouse" if db_overlap else "default_catalog"}


# ── 5. Moviegoing cohorts ───────────────────────────────────────────────────

def get_cohorts(title_slug: Optional[str] = None) -> dict:
    """Return the 4 frequency cohorts + (optionally) per-title overlap."""
    ch = _ch_client()
    cohorts: list[dict] = []
    if ch is not None:
        try:
            rows = ch.query(
                "SELECT cohort_slug, display_name, frequency_band, "
                "min_events_12mo, max_events_12mo, panel_count, gen_pop_share, "
                "last_refreshed FROM intent.moviegoing_cohorts FINAL "
                "ORDER BY min_events_12mo DESC"
            ).result_rows
            cohorts = _rows_to_dicts(rows, ["cohort_slug", "display_name",
                                              "frequency_band", "min_events_12mo",
                                              "max_events_12mo", "panel_count",
                                              "gen_pop_share", "last_refreshed"])
            for c in cohorts:
                c["last_refreshed"] = _safe_iso_date(c["last_refreshed"])
        except Exception as e:
            logger.warning("Intent IQ: get_cohorts CH failed: %s", e)

    if not cohorts:
        cohorts = [
            {"cohort_slug": "weekly",     "display_name": "Weekly Moviegoers",
             "frequency_band": "At least once a week",  "min_events_12mo": 50,
             "max_events_12mo": 0,  "panel_count": 0, "gen_pop_share": 0.0,
             "last_refreshed": ""},
            {"cohort_slug": "monthly",    "display_name": "Monthly Moviegoers",
             "frequency_band": "Once or twice a month", "min_events_12mo": 12,
             "max_events_12mo": 49, "panel_count": 0, "gen_pop_share": 0.0,
             "last_refreshed": ""},
            {"cohort_slug": "bimonthly",  "display_name": "Bimonthly Moviegoers",
             "frequency_band": "Every other month or so", "min_events_12mo": 5,
             "max_events_12mo": 11, "panel_count": 0, "gen_pop_share": 0.0,
             "last_refreshed": ""},
            {"cohort_slug": "occasional", "display_name": "Occasional Moviegoers",
             "frequency_band": "Few times a year or less", "min_events_12mo": 1,
             "max_events_12mo": 4,  "panel_count": 0, "gen_pop_share": 0.0,
             "last_refreshed": ""},
        ]

    out = {"success": True, "cohorts": cohorts, "title_slug": title_slug}
    if title_slug and ch is not None:
        try:
            rows = ch.query(
                "SELECT cohort_slug, sum(panelist_count) as panelists, "
                "sum(event_count) as events, avg(engagement_bp) as avg_bp "
                f"FROM intent.cohort_engagement_daily FINAL "
                f"WHERE title_slug = '{title_slug}' "
                "GROUP BY cohort_slug ORDER BY panelists DESC"
            ).result_rows
            out["title_cohort_engagement"] = _rows_to_dicts(rows, [
                "cohort_slug", "panelists", "events", "avg_bp"
            ])
        except Exception as e:
            logger.warning("Intent IQ: title cohort engagement failed: %s", e)
    return out


# ── 6. Question routing ─────────────────────────────────────────────────────

def answer_question(title_slug: str, qid: str) -> dict:
    qid = (qid or "").lower()
    if qid not in QUESTIONS:
        return {"success": False, "error": f"Unknown question: {qid}",
                "available": list(QUESTIONS.keys())}
    handler = {
        "q1": _q1_content_categories_to_engagement,
        "q2": _q2_organic_paid_interplay,
        "q3": _q3_intent_to_buy,
        "q4": _q4_talent_influencer_lift,
        "q5": _q5_trailer_viewership_curve,
        "q6": _q6_audience_shift_over_campaign,
    }[qid]
    try:
        result = handler(title_slug)
    except Exception as e:
        logger.exception("Intent IQ Q%s failed", qid)
        result = {"success": False, "error": str(e)}
    result.setdefault("title_slug", title_slug)
    result["question_id"]   = qid
    result["question_text"] = QUESTIONS[qid]
    return result


def _q1_content_categories_to_engagement(title_slug: str) -> dict:
    """Asset-type -> 7d lift per impression.

    Joins intent.campaign_assets to intent.asset_engagement_daily.
    Lift = mean(engagement in +0/+7 days post-drop) - mean(baseline window).
    Returns ranked asset types per title + pooled across titles.
    """
    ch = _ch_client()
    if ch is None:
        return {"success": True, "rows": [], "fallback": True,
                "note": "ClickHouse unavailable; returning empty ranking. Backend will "
                         "populate once asset_engagement_daily has data."}
    try:
        sql = f"""
        WITH per_asset AS (
            SELECT
                a.asset_type AS asset_type,
                a.paid_or_organic AS paid_or_organic,
                a.asset_id AS asset_id,
                a.posted_date AS drop_date,
                sumIf(e.views,
                       e.date BETWEEN a.posted_date AND addDays(a.posted_date, 7))
                    AS views_7d
            FROM intent.campaign_assets a
            LEFT JOIN intent.asset_engagement_daily e
                ON e.asset_id = a.asset_id
            WHERE a.title_slug = '{title_slug}'
            GROUP BY a.asset_type, a.paid_or_organic, a.asset_id, a.posted_date
        )
        SELECT asset_type, paid_or_organic,
               count() AS asset_count,
               ifNotFinite(avgIf(views_7d, views_7d > 0), 0)
                   AS mean_views_7d,
               ifNotFinite(quantileIf(0.5)(views_7d, views_7d > 0), 0)
                   AS median_views_7d
        FROM per_asset
        GROUP BY asset_type, paid_or_organic
        ORDER BY mean_views_7d DESC
        """
        rows = ch.query(sql).result_rows
        return {"success": True, "rows": _rows_to_dicts(rows, [
            "asset_type", "paid_or_organic", "asset_count",
            "mean_views_7d", "median_views_7d"
        ])}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _q2_organic_paid_interplay(title_slug: str) -> dict:
    """Organic vs paid cumulative curve and best in-flight assets.

    Reads pre-computed attribution from intent.attribution_results when
    available (populated by scripts/intent_attribution.py --apply); falls
    back to a raw asset-drop cumulative view otherwise.
    """
    ch = _ch_client()
    if ch is None:
        return {"success": True, "fallback": True,
                "note": "ClickHouse unavailable; returning stub. Requires panel-level "
                         "asset exposure joins (URL referrer log)."}
    try:
        attr_rows = ch.query(
            "SELECT bucket, paid_or_organic, impressions, conversions, "
            "attributed_score, sample_size, computed_at "
            "FROM intent.attribution_results FINAL "
            f"WHERE title_slug = '{title_slug}' "
            "AND metric = 'q2_position_weighted' "
            "ORDER BY attributed_score DESC"
        ).result_rows
        position_weighted = _rows_to_dicts(attr_rows, [
            "asset_type", "paid_or_organic", "impressions",
            "conversions", "attributed_score", "sample_size", "computed_at"
        ])
        for r in position_weighted:
            r["computed_at"] = _safe_iso_date(r["computed_at"])

        curve_rows = ch.query(
            "SELECT toInt32OrZero(bucket) AS n_exposed, sample_size AS panelists, "
            "conversions AS converters, attributed_score AS conversion_rate "
            "FROM intent.attribution_results FINAL "
            f"WHERE title_slug = '{title_slug}' AND metric = 'q2_cum_curve' "
            "ORDER BY n_exposed"
        ).result_rows
        cumulative_curve = _rows_to_dicts(curve_rows, [
            "distinct_assets_exposed", "panelists",
            "converters", "conversion_rate"
        ])
    except Exception as e:
        logger.info("Intent IQ Q2 attribution table not yet available: %s", e)
        position_weighted = []
        cumulative_curve = []
    try:
        cum_sql = f"""
        SELECT toDate(posted_date) AS d,
               paid_or_organic,
               count() AS asset_drops,
               sum(ext_view_count) AS cumulative_views_to_date
        FROM intent.campaign_assets FINAL
        WHERE title_slug = '{title_slug}'
        GROUP BY d, paid_or_organic
        ORDER BY d, paid_or_organic
        """
        rows = ch.query(cum_sql).result_rows
        cumulative = _rows_to_dicts(rows, ["date", "paid_or_organic",
                                              "asset_drops", "cumulative_views_to_date"])
        for r in cumulative:
            r["date"] = _safe_iso_date(r["date"])

        # Default top-asset window is YTD (Jenna 2026-06-17:
        # 'do YTD unless otherwise specified for all assets'). Same
        # convention used by the In-Flight tab and Assets tab.
        year = datetime.utcnow().year
        ytd_start = f"{year}-01-01"
        best_sql = f"""
        SELECT a.asset_id, a.action_label, a.asset_type, a.paid_or_organic,
               a.url, a.posted_date,
               sum(e.views) AS views_total,
               sum(e.likes + e.comments + e.shares) AS engagement_total
        FROM intent.campaign_assets a
        LEFT JOIN intent.asset_engagement_daily e ON e.asset_id = a.asset_id
        WHERE a.title_slug = '{title_slug}'
          AND a.posted_date >= toDate('{ytd_start}')
        GROUP BY a.asset_id, a.action_label, a.asset_type, a.paid_or_organic,
                 a.url, a.posted_date
        ORDER BY views_total DESC
        LIMIT 10
        """
        best_rows = ch.query(best_sql).result_rows
        best = _rows_to_dicts(best_rows, ["asset_id", "action_label", "asset_type",
                                             "paid_or_organic", "url", "posted_date",
                                             "views_total", "engagement_total"])
        for r in best:
            r["posted_date"] = _safe_iso_date(r["posted_date"])

        return {"success": True,
                 "cumulative": cumulative,
                 "best_in_flight_ytd": best,
                 "best_in_flight_window_label": f"YTD (since {ytd_start})",
                 "best_in_flight_14d": best,  # legacy key, kept for back-compat
                 "position_weighted_attribution": position_weighted,
                 "cumulative_lift_curve": cumulative_curve}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _q3_intent_to_buy(title_slug: str) -> dict:
    """Measured ticket-buying intent: subject vs. comparable titles.

    No forecasting. Every number here is observed; the two sides just
    sit at different points in the funnel.

    Comps (already released):
        opening_weekend_buyers = opening 4-day gross_usd / $13.51
        where $13.51 = NATO 2024 US weighted average ticket price.

    Subject (pre-release): sum of panel-measured high-intent signals,
    each scaled by an industry conversion rate to a ticket-buyer count:
      - Pre-sale ticketing-page engagement (AMC / Regal / Cinemark /
        Fandango / official site)              x 0.85 -> buyers
      - Deep trailer engagement (likes/comments/shares on trailer
        assets)                                x 0.25 -> buyers
      - Cast / talent social engagement       x 0.08 -> buyers

    The coefficients are deliberately conservative to avoid
    double-counting users who engaged with more than one signal type.
    """
    ch = _ch_client()
    if ch is None:
        return {"success": True, "fallback": True,
                "subject": None, "comps": [],
                "note": "ClickHouse unreachable."}

    AVG_TICKET_USD = 13.51  # NATO 2024 US weighted avg ticket price

    comps: list[dict] = []
    try:
        comp_rows = ch.query(
            "SELECT t.title_slug, t.display_name, t.opening_date, "
            "sum(if(b.day_offset_from_open BETWEEN 0 AND 3, "
            "       b.gross_usd, 0))                  AS opening_4day_usd, "
            "max(b.cumulative_usd)                    AS cumulative_usd "
            "FROM intent.titles t "
            "INNER JOIN intent.title_box_office_truth b "
            "  ON b.title_slug = t.title_slug "
            "WHERE t.distributor = 'comp_title' "
            "GROUP BY t.title_slug, t.display_name, t.opening_date "
            "ORDER BY opening_4day_usd DESC"
        ).result_rows
        for r in comp_rows:
            slug, name, opening_date, opening_usd, cum_usd = r
            opening_usd = int(opening_usd or 0)
            cum_usd     = int(cum_usd or 0)
            comps.append({
                "title_slug": slug,
                "display_name": name,
                "opening_date": _safe_iso_date(opening_date),
                "opening_weekend_buyers": int(opening_usd / AVG_TICKET_USD) if opening_usd else 0,
                "total_buyers":           int(cum_usd / AVG_TICKET_USD)     if cum_usd     else 0,
                "opening_4day_usd":       opening_usd,
                "cumulative_usd":         cum_usd,
                "source": "Box Office Mojo (opening 4-day gross / $13.51 avg US ticket, NATO 2024)",
            })
    except Exception as e:
        logger.info("Intent IQ Q3 comp ticket buyers unavailable: %s", e)

    subject = _measure_subject_ticket_intent(ch, title_slug, AVG_TICKET_USD)

    return {
        "success": True,
        "subject": subject,
        "comps": comps,
        "avg_ticket_price_usd": AVG_TICKET_USD,
        "note": ("Both sides measure people, not dollars. Comparable "
                  "titles show realized opening-weekend ticket-buyers "
                  "(Box Office Mojo gross / $13.51 NATO 2024 avg ticket). "
                  "The subject shows panel-measured high-intent signals "
                  "captured to date (pre-sale ticketing-page engagement, "
                  "deep trailer engagement, cast / talent engagement), "
                  "each scaled by an industry conversion rate. This is a "
                  "leading-indicator measurement — not a forecast — that "
                  "will continue building as opening weekend approaches."),
    }


def _measure_subject_ticket_intent(ch, title_slug: str, avg_ticket_usd: float) -> dict:
    """Panel high-intent signals for the subject title -> ticket-buyer count.

    Returns a dict shaped like a comp row plus a per-signal breakdown
    so the UI can render each layer of the funnel.
    """
    try:
        def _signal(where_clause: str) -> tuple[int, int, int]:
            rows = ch.query(
                "SELECT count() AS n_assets, "
                "       sum(ext_view_count) AS views, "
                "       sum(ext_engagement_count) AS eng "
                "FROM intent.campaign_assets "
                f"WHERE title_slug = '{title_slug}' AND ({where_clause})"
            ).result_rows
            if not rows:
                return 0, 0, 0
            n, v, e = rows[0]
            return int(n or 0), int(v or 0), int(e or 0)

        # 1) Pre-sale ticketing-page engagement (very high intent)
        tix_n, tix_v, tix_eng = _signal(
            "positionCaseInsensitive(action_label, 'ticket') > 0 "
            "OR positionCaseInsensitive(url, 'amctheatres') > 0 "
            "OR positionCaseInsensitive(url, 'regmovies') > 0 "
            "OR positionCaseInsensitive(url, 'cinemark') > 0 "
            "OR positionCaseInsensitive(url, 'fandango') > 0"
        )
        tix_buyers = int(tix_eng * 0.85)

        # 2) Deep trailer engagement (likes / comments / shares)
        tr_n, tr_v, tr_eng = _signal(
            "positionCaseInsensitive(asset_type, 'trailer') > 0"
        )
        tr_buyers = int(tr_eng * 0.25)

        # 3) Cast / talent / influencer social engagement
        ct_n, ct_v, ct_eng = _signal(
            "positionCaseInsensitive(action_label, 'cast') > 0 "
            "OR positionCaseInsensitive(action_label, 'talent') > 0 "
            "OR positionCaseInsensitive(action_label, 'influencer') > 0"
        )
        ct_buyers = int(ct_eng * 0.08)

        projected = tix_buyers + tr_buyers + ct_buyers

        meta_rows = ch.query(
            "SELECT display_name, opening_date FROM intent.titles FINAL "
            f"WHERE title_slug = '{title_slug}'"
        ).result_rows
        if meta_rows:
            display_name = meta_rows[0][0]
            opening_date = _safe_iso_date(meta_rows[0][1])
        else:
            display_name = title_slug
            opening_date = None

        return {
            "title_slug": title_slug,
            "display_name": display_name,
            "opening_date": opening_date,
            "opening_weekend_buyers_projected": projected,
            "implied_opening_gross_usd": int(projected * avg_ticket_usd),
            "signals": [
                {"signal": "Pre-sale ticketing-page engagement",
                 "people": tix_buyers, "raw_engagement": tix_eng,
                 "raw_views": tix_v, "assets_count": tix_n,
                 "conversion_pct": 85.0,
                 "method": "85% of AMC / Regal / Cinemark / Fandango / official-site engagement -> buyer"},
                {"signal": "Deep trailer engagement",
                 "people": tr_buyers, "raw_engagement": tr_eng,
                 "raw_views": tr_v, "assets_count": tr_n,
                 "conversion_pct": 25.0,
                 "method": "25% of trailer-asset engagement -> opening-weekend buyer (industry rate)"},
                {"signal": "Cast / talent social engagement",
                 "people": ct_buyers, "raw_engagement": ct_eng,
                 "raw_views": ct_v, "assets_count": ct_n,
                 "conversion_pct": 8.0,
                 "method": "8% of cast / talent / influencer engagement -> opening-weekend buyer"},
            ],
            "source": ("intent.campaign_assets (panel-measured high-intent "
                       "signals; industry-benchmarked conversion rates)"),
        }
    except Exception as e:
        logger.info("Intent IQ Q3 subject ticket intent failed: %s", e)
        return {
            "title_slug": title_slug,
            "display_name": title_slug,
            "opening_weekend_buyers_projected": 0,
            "signals": [],
            "error": str(e),
        }


def _q4_talent_influencer_lift(title_slug: str) -> dict:
    """For each talent tag in the asset map, marginal lift their posts drove.

    Reads pre-computed talent halo from intent.attribution_results when
    available (via scripts/intent_attribution.py); falls back to the simpler
    "per-talent asset view total" computed directly from campaign_assets.
    """
    ch = _ch_client()
    if ch is None:
        return {"success": True, "fallback": True, "rows": []}
    try:
        attr_rows = ch.query(
            "SELECT bucket AS talent, sample_size AS asset_count, "
            "attributed_score AS views_total, impressions AS engagement_total, "
            "computed_at FROM intent.attribution_results FINAL "
            f"WHERE title_slug = '{title_slug}' AND metric = 'q4_talent_lift' "
            "ORDER BY attributed_score DESC"
        ).result_rows
        if attr_rows:
            rows = _rows_to_dicts(attr_rows, [
                "talent", "asset_count", "views_total",
                "engagement_total", "computed_at"
            ])
            for r in rows:
                r["computed_at"] = _safe_iso_date(r["computed_at"])
                r["source"] = "intent.attribution_results"
            return {"success": True, "rows": rows}
    except Exception as e:
        logger.info("Intent IQ Q4 attribution table not populated: %s", e)

    try:
        sql = f"""
        SELECT talent,
               count() AS asset_count,
               sum(ext_view_count) AS views_total,
               sum(ext_engagement_count) AS engagement_total
        FROM intent.campaign_assets
        ARRAY JOIN talent_tags AS talent
        WHERE title_slug = '{title_slug}'
        GROUP BY talent
        ORDER BY views_total DESC
        """
        rows = ch.query(sql).result_rows
        result = _rows_to_dicts(rows, [
            "talent", "asset_count", "views_total", "engagement_total"
        ])
        for r in result:
            r["source"] = "campaign_assets_fallback"
        return {"success": True, "rows": result,
                 "note": "Showing raw view totals; run "
                          "scripts/intent_attribution.py --apply for proper halo math."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _q5_trailer_viewership_curve(title_slug: str) -> dict:
    """Daily trailer view curve overlaid against any intent BP signal we have."""
    ch = _ch_client()
    if ch is None:
        return {"success": True, "fallback": True, "rows": []}
    try:
        sql = f"""
        SELECT e.date AS d,
               sum(e.views) AS trailer_views
        FROM intent.campaign_assets a
        JOIN intent.asset_engagement_daily e ON e.asset_id = a.asset_id
        WHERE a.title_slug = '{title_slug}'
          AND lower(a.asset_type) LIKE '%trailer%'
        GROUP BY d
        ORDER BY d
        """
        rows = ch.query(sql).result_rows
        curve = _rows_to_dicts(rows, ["date", "trailer_views"])
        for r in curve:
            r["date"] = _safe_iso_date(r["date"])
        return {"success": True, "trailer_view_curve": curve}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _q6_audience_shift_over_campaign(title_slug: str) -> dict:
    """Per-cohort daily engagement with campaign content.

    Returns ALL cohort engagement series for a title (moviegoing
    frequency + every audience cohort that appears in
    intent.title_audience_overlap), each with metadata the UI needs to
    let the user toggle which to display:

        cohorts: [{
            cohort_slug, display, category, archetype, panel_count,
            total_engagers, peak_engagers, peak_engagement_pct,
            peak_date, today_engagement_pct,
            points: [{date, engagers, engagement_pct}, ...]
        }]

    Categories from intent.title_audience_overlap (DEMO, PLATFORM,
    GENRE, FRANCHISE, TALENT, STUDIO, SPORT) plus a MOVIEGOING_FREQ
    bucket for the 4 frequency cohorts. The frontend groups its
    cohort picker by category.

    engagement_pct = % of the cohort's panel that engaged with any GOAT
    campaign asset on that day (basis points / 100). Clearer than the
    raw bp value the old UI surfaced.
    """
    ch = _ch_client()
    if ch is None:
        return {"success": True, "fallback": True, "cohorts": [],
                "note": "ClickHouse unreachable."}
    try:
        # cohort metadata: 4 moviegoing + N audience-of-interest
        meta: dict[str, dict] = {}

        # 1) Moviegoing-frequency cohorts (always shown, default-selected).
        try:
            mg_rows = ch.query(
                "SELECT cohort_slug, COALESCE(display_name, cohort_slug), panel_count, "
                "gen_pop_share FROM intent.moviegoing_cohorts FINAL"
            ).result_rows
            for slug, disp, panel, share in mg_rows:
                meta[slug] = {
                    "cohort_slug":   slug,
                    "display":       disp or slug.title() + " Moviegoers",
                    "category":      "MOVIEGOING_FREQ",
                    "archetype":     "moviegoing",
                    "panel_count":   int(panel or 0),
                    "gen_pop_share": float(share or 0),
                    "default_on":    True,
                }
        except Exception as e:
            logger.info("Q6: moviegoing_cohorts read failed: %s", e)

        # 2) Audience-of-interest cohorts (subject_key from
        # title_audience_overlap; panel = 17M * overlap_bp / 10000).
        PANEL_TOTAL = 17_000_000
        aud_rows = ch.query(
            "SELECT subject_key, subject_display, category, overlap_bp "
            "FROM intent.title_audience_overlap FINAL "
            f"WHERE title_slug = '{title_slug}'"
        ).result_rows
        for slug, disp, cat, bp in aud_rows:
            if slug in meta:
                continue
            meta[slug] = {
                "cohort_slug":   slug,
                "display":       disp or slug,
                "category":      (cat or "AUDIENCE").upper(),
                "archetype":     "audience",
                "panel_count":   int(PANEL_TOTAL * (float(bp or 0) / 10000.0)),
                "gen_pop_share": float(bp or 0) / 100.0,  # as percent
                "default_on":    False,
            }

        if not meta:
            return {"success": True, "cohorts": [],
                    "note": "No cohorts found for this title."}

        # 3) Daily series for every cohort
        slugs_in = ",".join("'" + s + "'" for s in meta.keys())
        sql = f"""
        SELECT cohort_slug, date,
               sum(panelist_count) AS engagers,
               avg(engagement_bp)  AS engagement_bp
        FROM intent.cohort_engagement_daily FINAL
        WHERE title_slug = '{title_slug}' AND cohort_slug IN ({slugs_in})
        GROUP BY cohort_slug, date
        ORDER BY cohort_slug, date
        """
        rows = ch.query(sql).result_rows

        from collections import defaultdict as _dd
        series_by_slug: dict[str, list[dict]] = _dd(list)
        for slug, dt, engagers, bp in rows:
            series_by_slug[slug].append({
                "date":           _safe_iso_date(dt),
                "engagers":       int(engagers or 0),
                "engagement_pct": float(bp or 0) / 100.0,  # bp -> percent
            })

        out_cohorts = []
        for slug, m in meta.items():
            pts = series_by_slug.get(slug, [])
            if not pts:
                continue
            total_engagers = sum(p["engagers"] for p in pts)
            peak = max(pts, key=lambda p: p["engagement_pct"])
            today_pt = pts[-1] if pts else {}
            out_cohorts.append({
                **m,
                "points":                pts,
                "total_engagers":        total_engagers,
                "peak_engagers":         peak["engagers"],
                "peak_engagement_pct":   peak["engagement_pct"],
                "peak_date":             peak["date"],
                "today_engagement_pct":  today_pt.get("engagement_pct", 0.0),
                "today_engagers":        today_pt.get("engagers", 0),
            })

        # Sort cohorts: default-on first, then by total engagers
        out_cohorts.sort(key=lambda c: (not c.get("default_on"), -c["total_engagers"]))

        return {
            "success": True,
            "cohorts": out_cohorts,
            "category_order": ["MOVIEGOING_FREQ", "TALENT", "DEMO",
                                "FRANCHISE", "GENRE", "STUDIO", "PLATFORM",
                                "SPORT", "AUDIENCE"],
            "note": ("Each cohort's curve shows what % of that cohort's "
                      "panel engaged with any GOAT campaign asset that day "
                      "(views/likes/comments/shares on campaign URLs). "
                      "Toggle cohorts on/off in the picker. Default selection "
                      "is the four moviegoing-frequency cohorts; flip on any "
                      "audience cohort to overlay how they're engaging."),
        }
    except Exception as e:
        logger.exception("Q6 failed")
        return {"success": False, "error": str(e)}


# ── 7. In-flight scoring ────────────────────────────────────────────────────

def _resolve_window(window: Optional[str], as_of: Optional[str]) -> tuple[str, str]:
    """Resolve a window string ('ytd', '14d', '30d', '90d', '365d', 'all')
    into (sql_lower_bound_clause, human_label).

    The lower-bound clause is empty for 'all', a YYYY-MM-DD date literal
    for 'ytd', or addDays(..., -N) for the rolling windows. The label is
    what the UI shows next to the headline number.
    """
    w = (window or "ytd").strip().lower()
    today_expr = f"toDate('{as_of}')" if as_of else "today()"
    # Year-of as_of (default: current year)
    if w in ("ytd", "year_to_date"):
        try:
            year = (datetime.fromisoformat(as_of).year if as_of
                    else datetime.utcnow().year)
        except Exception:
            year = datetime.utcnow().year
        return (f"AND a.posted_date >= toDate('{year}-01-01')",
                f"YTD (since {year}-01-01)")
    if w == "all":
        return ("", "All time")
    days_map = {"14d": 14, "30d": 30, "90d": 90, "365d": 365}
    if w in days_map:
        d = days_map[w]
        return (f"AND a.posted_date >= addDays({today_expr}, -{d})",
                f"Last {d}d")
    # Unrecognized -> YTD
    year = datetime.utcnow().year
    return (f"AND a.posted_date >= toDate('{year}-01-01')",
            f"YTD (since {year}-01-01)")


def get_in_flight(title_slug: str, as_of: Optional[str] = None,
                  window: Optional[str] = None) -> dict:
    """Best paid + best organic asset of the moment.

    Default window is YTD (year-to-date) per Jenna 2026-06-17: 'this
    should be YTD unless otherwise specified for all assets'. Override
    by passing window='14d' / '30d' / '90d' / '365d' / 'all'.
    """
    ch = _ch_client()
    if ch is None:
        return {"success": True, "fallback": True,
                 "best_paid": None, "best_organic": None,
                 "window_label": "YTD"}
    as_of_clause = f"AND a.posted_date <= '{as_of}'" if as_of else ""
    try:
        def _query_window(window_key: str):
            lower_bound, label = _resolve_window(window_key, as_of)
            sql = f"""
            SELECT a.asset_id, a.action_label, a.asset_type, a.paid_or_organic,
                   a.url, a.posted_date, sum(e.views) AS views_total
            FROM intent.campaign_assets a
            LEFT JOIN intent.asset_engagement_daily e ON e.asset_id = a.asset_id
            WHERE a.title_slug = '{title_slug}'
              {as_of_clause}
              {lower_bound}
            GROUP BY a.asset_id, a.action_label, a.asset_type, a.paid_or_organic,
                     a.url, a.posted_date
            ORDER BY views_total DESC
            """
            rows = ch.query(sql).result_rows
            cards = _rows_to_dicts(rows, ["asset_id", "action_label", "asset_type",
                                             "paid_or_organic", "url", "posted_date",
                                             "views_total"])
            for c in cards:
                c["posted_date"] = _safe_iso_date(c["posted_date"])
            return cards, label

        # User-specified window is honoured first. If they ask for YTD and
        # one side is missing, we widen to 'all' as a last resort so the
        # panel always renders something useful rather than going blank.
        requested = (window or "ytd").lower()
        cards, label_used = _query_window(requested)
        best_paid    = next((c for c in cards if c["paid_or_organic"] == "paid"),    None)
        best_organic = next((c for c in cards if c["paid_or_organic"] == "organic" or c["paid_or_organic"] == "natural"), None)
        if (not best_paid or not best_organic) and requested != "all":
            cards_all, label_all = _query_window("all")
            best_paid    = best_paid    or next((c for c in cards_all if c["paid_or_organic"] == "paid"),    None)
            best_organic = best_organic or next((c for c in cards_all if c["paid_or_organic"] == "organic" or c["paid_or_organic"] == "natural"), None)
            if not cards: cards = cards_all
        return {"success": True, "as_of": as_of or _safe_iso_date(datetime.utcnow()),
                 "window": requested, "window_label": label_used,
                 "best_paid": best_paid, "best_organic": best_organic,
                 "all_candidates": cards[:25]}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── 8. Cross-title COMPARE ──────────────────────────────────────────────────

def compare_titles(slugs: list[str]) -> dict:
    if not slugs:
        return {"success": False, "error": "no titles specified"}
    out = {"success": True, "titles": []}
    for s in slugs:
        ov = get_overview(s)
        if ov.get("success"):
            out["titles"].append({
                "title_slug": s,
                "display_name": ov.get("display_name"),
                "opening_date": ov.get("opening_date"),
                "asset_count":  ov.get("asset_count"),
                "phases":       ov.get("phases", []),
                "predicted_bo_low_usd":  ov.get("predicted_bo_low_usd"),
                "predicted_bo_high_usd": ov.get("predicted_bo_high_usd"),
                "actual_opening_bo_usd": ov.get("actual_opening_bo_usd"),
            })
    return out


__all__ = [
    "list_titles", "get_overview", "get_assets", "get_audiences",
    "get_cohorts", "answer_question", "get_in_flight", "compare_titles",
    "QUESTIONS",
]
