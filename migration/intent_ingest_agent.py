"""Intent IQ ingest agent — form-driven brand-campaign builder.

Kicked off from the Analysis IQ "Build Intent Campaign" form. Takes a
compact set of user inputs (campaign name, brand, dates, attribution
window, asset URL list, notes) and produces a fully-formed brand-mode
Intent IQ snapshot ready to render in the dashboard.

Pipeline (called by `run_ingest` — one function so `spawn_heavy_analysis`
can invoke it directly, mirroring `migration/journey_iq.py::run_job`):

    1. Slugify campaign into title_slug (unique against S3 registry).
    2. Detect channel per asset URL (YouTube / TikTok / Instagram / etc.).
    3. Best-effort OG-metadata scrape per URL (title, description,
       thumbnail, posted_date). Failures fall back to sane defaults so
       the pipeline never breaks on a single dead URL.
    4. Claude synthesis pass 1 — TALENT extraction: for each asset with
       enough metadata, ask Claude to identify any named talent
       featured in the piece.
    5. Claude synthesis pass 2 — COHORTS: given the brand, campaign,
       and asset roll-up, ask Claude to design 4–6 audience cohorts
       most likely to have encountered / engaged with this content.
       Cohorts are brand-appropriate (existing customer / high-intent
       prospect / lapsed / awareness-only / demo cut / etc.) — never
       the moviegoing bands used for films.
    6. Claude synthesis pass 3 — AUDIENCES: extract audience cards for
       the Audiences tab. Talent (from step 4) + 3–5 lifestyle / demo
       cards inferred from the brand and campaign vibe.
    7. Terminology block: derived from the user-supplied brand_category
       via `BRAND_CATEGORY_TERMINOLOGY_PRESETS`. Falls back to generic
       brand terminology if the category isn't in the preset list.
    8. Phase grouping: if the campaign has a single "campaign_name",
       one phase. If the user supplied multiple `phases` in the form,
       assets get assigned to the phase whose date-window contains
       their posted_date.
    9. Write normalized snapshot to S3:
         s3://dashboard-inputs/intent/<slug>/source/normalized_assets.json
   10. Register the title in `intent/registry.json` via
       `migration.intent_register.register_intent_title` — brand-mode
       fields (title_type / terminology / enabled_tabs / brand_config)
       propagate all the way to the dashboard's title selector.

Runs INSIDE the Render gunicorn thread pool via `spawn_heavy_analysis`
(matches Journey IQ). No SSH, no separate worker box. All I/O is HTTP
out (Claude API + S3 + best-effort URL scrapes) so it fits cleanly.

Progress callback contract (progress_cb(pct: int, message: str)):
    5   — Job started
    10  — URL parsing complete
    25  — Asset metadata scraped
    45  — Talent extraction done
    65  — Cohort synthesis done
    80  — Audience synthesis done
    90  — Snapshot written to S3
    100 — Registered; done. Returns dict with `title_slug` + `s3_key`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date as date_cls
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

S3_BUCKET    = os.environ.get("INTENT_S3_BUCKET", "dashboard-inputs")
REGISTRY_KEY = "intent/registry.json"
SNAPSHOT_KEY_FMT = "intent/{slug}/source/normalized_assets.json"

# Longest single Claude call caps out around 60s in our stack; we chunk
# accordingly to avoid one bad response blocking the whole run.
CLAUDE_MAX_ASSETS_PER_CHUNK = 40


# =====================================================================
# Terminology presets by brand category
# ---------------------------------------------------------------------
# Extending this dict adds a new "smart default" to the form. Users pick
# from the dropdown; the ingest agent looks up the preset and any per-
# category fields override the generic brand default. Callers can also
# pass a full custom `terminology` dict via the form.
# =====================================================================

_GENERIC_BRAND_TERMINOLOGY = {
    "title_noun":                 "Campaign",
    "audience_noun":              "Audience",
    "opening_moment_noun":        "Campaign peak",
    "launch_date_label":          "Campaign launch",
    "presale_date_label":         "Attribution window opens",
    "parent_org_label":           "Brand",
    "top_funnel_label":           "Engagement",
    "mid_funnel_label":           "Research",
    "mid_funnel_full":            "Engaged another brand post, searched the brand, visited "
                                  "brand social profile, or opened brand app-store listing "
                                  "within the attribution window",
    "bottom_funnel_label":        "Site visit",
    "bottom_funnel_full":         "Visited the brand website within the attribution window",
    "conversion_noun":            "signup",
    "conversion_verb":            "sign up",
    "conversion_endpoint_label":  "brand website",
    "attribution_window_days":    14,
}

BRAND_CATEGORY_TERMINOLOGY_PRESETS: Dict[str, Dict[str, Any]] = {
    "Digital Banking": {
        "mid_funnel_full":            "Engaged another Chime/Cash-App/Sofi-shaped post, searched the brand, "
                                       "visited the brand's IG/TikTok profile, or opened its app-store "
                                       "listing within the attribution window",
        "bottom_funnel_label":        "Website visit",
        "conversion_noun":            "signup",
        "conversion_verb":            "sign up",
        "conversion_endpoint_label":  "brand website",
        "attribution_window_days":    14,
    },
    "DTC / eCommerce": {
        "mid_funnel_label":           "Consideration",
        "mid_funnel_full":            "Visited a product page, added to wishlist, engaged another brand post, "
                                       "or searched the brand within the attribution window",
        "bottom_funnel_label":        "Cart add",
        "conversion_noun":            "purchase",
        "conversion_verb":            "purchase",
        "conversion_endpoint_label":  "checkout",
        "attribution_window_days":    14,
    },
    "Streaming / SVOD": {
        "mid_funnel_label":           "Research",
        "bottom_funnel_label":        "Sign-up page visit",
        "conversion_noun":            "subscription",
        "conversion_verb":            "subscribe",
        "conversion_endpoint_label":  "sign-up page",
        "attribution_window_days":    14,
    },
    "QSR / Restaurant": {
        "mid_funnel_full":            "Searched the brand or opened its app / delivery-app listing within "
                                       "the attribution window",
        "bottom_funnel_label":        "Order intent",
        "conversion_noun":            "order",
        "conversion_verb":            "order",
        "conversion_endpoint_label":  "brand app / delivery app",
        "attribution_window_days":    7,
    },
    "Automotive": {
        "mid_funnel_label":           "Research",
        "bottom_funnel_label":        "Dealer / configurator visit",
        "conversion_noun":            "test-drive request",
        "conversion_verb":            "request a test drive",
        "conversion_endpoint_label":  "dealer site / configurator",
        "attribution_window_days":    30,
    },
    "CPG": {
        "mid_funnel_label":           "Recall",
        "bottom_funnel_label":        "Retailer / D2C visit",
        "conversion_noun":            "purchase intent",
        "conversion_verb":            "add to cart",
        "conversion_endpoint_label":  "retailer / brand.com",
        "attribution_window_days":    14,
    },
    "Retail": {
        "mid_funnel_label":           "Browse",
        "bottom_funnel_label":        "brand.com visit",
        "conversion_noun":            "purchase",
        "conversion_verb":            "purchase",
        "conversion_endpoint_label":  "brand.com",
        "attribution_window_days":    14,
    },
    "Fitness / Wellness": {
        "mid_funnel_label":           "Research",
        "bottom_funnel_label":        "Membership page visit",
        "conversion_noun":            "membership signup",
        "conversion_verb":            "sign up",
        "conversion_endpoint_label":  "membership page",
        "attribution_window_days":    14,
    },
}


def build_terminology_for_brand_category(brand_category: Optional[str],
                                          custom_overrides: Optional[dict] = None
                                          ) -> dict:
    """Compose the terminology block for a given brand category.

    Order of precedence (last wins):
      1. Generic brand defaults.
      2. Category preset (Digital Banking, DTC / eCommerce, etc.).
      3. User-supplied overrides from the form.
    """
    term = dict(_GENERIC_BRAND_TERMINOLOGY)
    if brand_category and brand_category in BRAND_CATEGORY_TERMINOLOGY_PRESETS:
        term.update(BRAND_CATEGORY_TERMINOLOGY_PRESETS[brand_category])
    if custom_overrides:
        term.update({k: v for k, v in custom_overrides.items() if v not in (None, "")})
    return term


# =====================================================================
# Slug + channel helpers
# =====================================================================

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_STRIP.sub("_", (name or "").strip().lower()).strip("_")
    return s or "campaign"


def _unique_slug(candidate: str, s3=None) -> str:
    """If the slug already exists in the registry, suffix with 2, 3, ..."""
    try:
        if s3 is None:
            import boto3
            s3 = boto3.client("s3")
        r = s3.get_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY)
        reg = json.loads(r["Body"].read().decode("utf-8"))
        existing = {t.get("title_slug") for t in reg.get("titles", []) if t}
    except Exception:
        existing = set()
    if candidate not in existing:
        return candidate
    for i in range(2, 100):
        alt = f"{candidate}_{i}"
        if alt not in existing:
            return alt
    return f"{candidate}_{int(time.time())}"


_CHANNEL_RULES = [
    ("YouTube",   ["youtube.com", "youtu.be"]),
    ("TikTok",    ["tiktok.com"]),
    ("Instagram", ["instagram.com"]),
    ("Facebook",  ["facebook.com", "fb.watch"]),
    ("X",         ["twitter.com", "x.com"]),
    ("LinkedIn",  ["linkedin.com"]),
    ("Reddit",    ["reddit.com"]),
    ("Snapchat",  ["snapchat.com"]),
    ("Threads",   ["threads.net"]),
    ("Pinterest", ["pinterest.com"]),
    ("Web",       []),  # fallback
]


def _detect_channel(url: str) -> str:
    if not url:
        return "Web"
    host = urlparse(url).netloc.lower().replace("www.", "")
    for channel, hosts in _CHANNEL_RULES:
        if any(h in host for h in hosts):
            return channel
    return "Web"


def _asset_id_for(url: str) -> str:
    return hashlib.sha1((url or "").encode()).hexdigest()[:16]


def _asset_type_for(channel: str) -> str:
    return {
        "YouTube":   "Organic Video",
        "TikTok":    "Organic Social",
        "Instagram": "Organic Social",
        "Facebook":  "Organic Social",
        "X":         "Organic Social",
        "LinkedIn":  "Organic Social",
        "Reddit":    "Organic Social",
        "Snapchat":  "Organic Social",
        "Threads":   "Organic Social",
        "Pinterest": "Organic Social",
    }.get(channel, "Web Asset")


# =====================================================================
# Best-effort URL scrape
# ---------------------------------------------------------------------
# We DON'T fail the job if a scrape fails. Every asset gets at minimum:
#   { url, channel, asset_type, asset_id, action_label (from URL slug),
#     ext_view_count: 0, ext_engagement_count: 0 }
# and any OG metadata we managed to fetch. YT/TikTok/IG counts can be
# refreshed later via scripts/refresh_intent_youtube_engagement.py.
# =====================================================================

_HTTP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15"
)


def _scrape_og_meta(url: str, timeout: float = 6.0) -> dict:
    """Fetch and parse OG meta tags. Never raises."""
    try:
        import requests
        resp = requests.get(url, timeout=timeout,
                             headers={"User-Agent": _HTTP_UA,
                                       "Accept": "text/html,application/xhtml+xml"},
                             allow_redirects=True)
        if resp.status_code != 200 or not resp.text:
            return {}
        html = resp.text[:400_000]   # 400KB cap; social pages fit easily
        out = {}
        for prop in ("og:title", "og:description", "og:image",
                      "og:site_name", "og:type", "og:video:duration",
                      "og:image:width", "og:image:height"):
            m = re.search(
                r'<meta[^>]+property=[\'"]' + re.escape(prop) + r'[\'"][^>]+content=[\'"]([^\'"]+)[\'"]',
                html, re.IGNORECASE)
            if m:
                out[prop] = m.group(1)
        # Title fallback
        if "og:title" not in out:
            m = re.search(r"<title>([^<]{1,200})</title>", html, re.IGNORECASE)
            if m:
                out["og:title"] = m.group(1).strip()
        return out
    except Exception as e:
        logger.info("Intent ingest: OG scrape failed for %s: %s", url, e)
        return {}


def _slug_from_url(url: str) -> str:
    """Extract a human-ish label from a URL path (used when OG scrape fails)."""
    try:
        p = urlparse(url).path.rstrip("/")
        tail = p.rsplit("/", 1)[-1] or "asset"
        tail = re.sub(r"\.(html?|php|aspx)$", "", tail, flags=re.IGNORECASE)
        tail = re.sub(r"[-_]+", " ", tail).strip()
        return tail[:80] or "asset"
    except Exception:
        return "asset"


def scrape_asset(url: str) -> dict:
    """Compose one asset row from a URL. Best-effort; never raises."""
    url = (url or "").strip()
    channel = _detect_channel(url)
    og = _scrape_og_meta(url) if url else {}

    label = og.get("og:title") or _slug_from_url(url) or channel + " post"
    label = re.sub(r"\s+", " ", label).strip()[:180]

    return {
        "asset_id":              _asset_id_for(url),
        "url":                   url,
        "channel":               channel,
        "asset_type":            _asset_type_for(channel),
        "action_label":          label,
        "og_metadata":           og,
        "note":                  og.get("og:description", "")[:280],
        "thumbnail_s3_url":      og.get("og:image", ""),
        "posted_date":           "",   # filled in during phase grouping
        "paid_or_organic":       "organic",
        "funnel_stage":          "Exposure",
        "talent_tags":           [],
        "audience_target_tags":  [],
        "ext_view_count":        0,
        "ext_engagement_count":  0,
        "ext_engagement_source": "not_yet_scraped",
        "source":                "form_ingest",
    }


# =====================================================================
# Claude synthesis
# ---------------------------------------------------------------------
# We use `migration.claude_client.claude_messages` for the 3 synthesis
# passes. Each pass has a strict JSON schema + a fallback if Claude
# returns garbage — the whole ingest MUST NOT fail because Claude
# hiccuped. Fallbacks are reasonable defaults, not blockers.
# =====================================================================

def _claude() -> Optional[Callable]:
    """Return the claude_messages callable, or None if unavailable."""
    try:
        # Prefer the parent-repo claude_client (has key pool) if present;
        # fall back to the submodule copy.
        try:
            sys.path.insert(0, "/root/finished_codes")
        except Exception:
            pass
        try:
            from migration.claude_client import claude_messages  # type: ignore
            return claude_messages
        except Exception:
            pass
        try:
            from bg_webapp.migration.claude_client import claude_messages  # type: ignore
            return claude_messages
        except Exception:
            return None
    except Exception as e:
        logger.info("Intent ingest: claude_messages unavailable: %s", e)
        return None


def _extract_json(text: str) -> Optional[dict]:
    """Robust-ish extraction of the first JSON object in text."""
    if not text:
        return None
    # Try ```json ... ``` fenced blocks first
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except Exception: pass
    m = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except Exception: pass
    # Try naked top-level {...}
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try: return json.loads(text[start:i+1])
                except Exception: return None
    return None


def _assets_summary_for_prompt(assets: List[dict], max_titles: int = 25) -> str:
    """Compact string summary of the asset roll-up for Claude."""
    chans = defaultdict(int)
    titles = []
    for a in assets:
        chans[a.get("channel") or "Unknown"] += 1
        lab = (a.get("action_label") or "").strip()
        if lab and lab not in titles and lab.lower() not in {"asset", "youtube post", "tiktok post"}:
            titles.append(lab)
    chan_str = ", ".join(f"{c}: {n}" for c, n in sorted(chans.items(), key=lambda x: -x[1]))
    title_str = "\n  - " + "\n  - ".join(titles[:max_titles]) if titles else "\n  (no titles scraped)"
    return f"{len(assets)} assets across channels: {chan_str}\nSample titles:{title_str}"


def synthesize_cohorts(brand: str, campaign: str, brand_category: str,
                       notes: str, assets: List[dict],
                       claude_fn: Optional[Callable] = None) -> List[dict]:
    """Ask Claude for 4–6 brand-appropriate cohorts. Falls back to a
    generic member/prospect/lapsed/aware split if Claude is unavailable."""
    claude_fn = claude_fn or _claude()
    fallback = [
        {"cohort_slug": "existing_customer",  "display_name": "Existing Customers",
         "frequency_band": "Currently active with the brand", "min_events_12mo": 1,
         "max_events_12mo": 0, "panel_count": 0, "gen_pop_share": 0.0,
         "last_refreshed": ""},
        {"cohort_slug": "high_intent_prospect", "display_name": "High-Intent Prospects",
         "frequency_band": "Actively comparing brands in category",
         "min_events_12mo": 3, "max_events_12mo": 0, "panel_count": 0,
         "gen_pop_share": 0.0, "last_refreshed": ""},
        {"cohort_slug": "aware_non_customer", "display_name": "Aware Non-Customers",
         "frequency_band": "Category-aware but not shopping",
         "min_events_12mo": 0, "max_events_12mo": 2, "panel_count": 0,
         "gen_pop_share": 0.0, "last_refreshed": ""},
        {"cohort_slug": "lapsed_customer", "display_name": "Lapsed Customers",
         "frequency_band": "Prior customer, inactive last 90d",
         "min_events_12mo": 0, "max_events_12mo": 0, "panel_count": 0,
         "gen_pop_share": 0.0, "last_refreshed": ""},
    ]
    if not claude_fn:
        return fallback
    prompt = f"""You are an audience-cohort strategist for a marketing analytics dashboard.

BRAND:        {brand}
CAMPAIGN:     {campaign}
CATEGORY:     {brand_category or "(unspecified)"}
NOTES:        {(notes or "").strip()[:800] or "(none)"}
ASSET SUMMARY:
{_assets_summary_for_prompt(assets)}

Define 4-6 audience cohorts that a media planner would use to measure how
this campaign lands. Cohorts must be:
  - Mutually exclusive and identifiable in behavioral panel data.
  - Brand-appropriate (member/customer status, category-shopping intensity,
    lifestyle segments). NEVER use moviegoing-frequency bands.
  - Sized meaningfully. Include a natural first cohort of existing customers
    or members if the brand has a customer base; a prospect / high-intent
    cohort; and 1-2 broader awareness/lifestyle cohorts.

Return ONLY JSON in this exact shape:
{{
  "cohorts": [
    {{
      "cohort_slug": "snake_case_slug",
      "display_name": "Human-Readable Name",
      "frequency_band": "one-line definition of who qualifies",
      "min_events_12mo": 0,
      "max_events_12mo": 0,
      "rationale": "why this cohort matters for THIS campaign"
    }}
  ]
}}
"""
    try:
        resp = claude_fn(
            system="You return only valid JSON. No prose outside the JSON block.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500, temperature=0.4,
        )
        parsed = _extract_json(resp)
        if not parsed or not isinstance(parsed.get("cohorts"), list):
            return fallback
        out = []
        for c in parsed["cohorts"][:6]:
            if not isinstance(c, dict) or not c.get("cohort_slug"):
                continue
            out.append({
                "cohort_slug":     _slugify(c.get("cohort_slug", "")),
                "display_name":    str(c.get("display_name") or c.get("cohort_slug"))[:64],
                "frequency_band":  str(c.get("frequency_band") or "")[:180],
                "min_events_12mo": int(c.get("min_events_12mo") or 0),
                "max_events_12mo": int(c.get("max_events_12mo") or 0),
                "panel_count":     0,
                "gen_pop_share":   0.0,
                "last_refreshed":  "",
                "rationale":       str(c.get("rationale") or "")[:400],
            })
        return out or fallback
    except Exception as e:
        logger.info("Intent ingest: cohort synthesis failed (%s), using fallback", e)
        return fallback


def synthesize_audiences(brand: str, campaign: str, brand_category: str,
                          notes: str, assets: List[dict], talents: List[str],
                          claude_fn: Optional[Callable] = None) -> List[dict]:
    """Return audience cards for the Audiences tab. Talent tags are
    added deterministically; Claude adds 3-5 lifestyle/demo cards.
    Falls back to a generic set if Claude is unavailable."""
    claude_fn = claude_fn or _claude()
    cards: List[dict] = []
    # Deterministic goal cards
    cards.append({"subject_key": "existing_customers",
                  "display": f"Existing {brand} Customers",
                  "category": "GOAL"})
    cards.append({"subject_key": "prospect_shoppers",
                  "display": "Prospect Shoppers in Category",
                  "category": "GOAL"})
    # Talent cards from scraped/extracted talent list
    for t in talents:
        if not t:
            continue
        cards.append({"subject_key": _slugify(t),
                      "display":     f"{t} (Cast)",
                      "category":    "TALENT"})
    # Claude-driven lifestyle/demo cards
    fallback_lifestyle = [
        {"subject_key": "gen_z_early_adopters", "display": "Gen Z Early Adopters",
         "category": "LIFESTYLE"},
        {"subject_key": "millennial_finance", "display": "Millennial Personal-Finance Enthusiasts",
         "category": "LIFESTYLE"},
        {"subject_key": "urban_multicultural", "display": "Urban Multicultural 25-44",
         "category": "DEMO"},
    ]
    if not claude_fn:
        cards.extend(fallback_lifestyle)
        return cards
    prompt = f"""You extract audience cards for a brand-campaign analytics dashboard.

BRAND:      {brand}
CAMPAIGN:   {campaign}
CATEGORY:   {brand_category or "(unspecified)"}
NOTES:      {(notes or "").strip()[:800]}
ASSETS:
{_assets_summary_for_prompt(assets)}

Return 3-6 LIFESTYLE and DEMO audience cards that a media planner
would over-index for this brand campaign. Do NOT include talent — those
are added separately. Categories must be one of: LIFESTYLE, DEMO, INTEREST.

Return ONLY JSON:
{{
  "audiences": [
    {{"subject_key": "snake_case", "display": "Human-Readable Name", "category": "LIFESTYLE"}}
  ]
}}
"""
    try:
        resp = claude_fn(
            system="You return only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000, temperature=0.4,
        )
        parsed = _extract_json(resp)
        if parsed and isinstance(parsed.get("audiences"), list):
            for a in parsed["audiences"][:6]:
                if not isinstance(a, dict): continue
                key = _slugify(a.get("subject_key") or a.get("display") or "")
                if not key: continue
                cat = str(a.get("category") or "LIFESTYLE").upper()
                if cat not in ("LIFESTYLE", "DEMO", "INTEREST", "GOAL"):
                    cat = "LIFESTYLE"
                cards.append({"subject_key": key,
                              "display": str(a.get("display") or key.title())[:80],
                              "category": cat})
        else:
            cards.extend(fallback_lifestyle)
    except Exception as e:
        logger.info("Intent ingest: audience synthesis failed (%s), using fallback", e)
        cards.extend(fallback_lifestyle)

    # Dedupe by subject_key
    seen = set(); dedup = []
    for c in cards:
        k = c.get("subject_key")
        if k and k not in seen:
            seen.add(k); dedup.append(c)
    return dedup


def extract_talent_from_assets(assets: List[dict],
                                claude_fn: Optional[Callable] = None
                                ) -> Dict[str, List[str]]:
    """Given a list of asset rows (with og_metadata), ask Claude to
    identify any named talent per asset. Returns {asset_id: [talent, ...]}.

    Falls back to {} (no talent tags) if Claude is unavailable or the
    asset metadata is too thin to reason about.
    """
    claude_fn = claude_fn or _claude()
    if not claude_fn:
        return {}
    # Build compact per-asset lines: asset_id | channel | title | short-description
    lines = []
    for a in assets[:CLAUDE_MAX_ASSETS_PER_CHUNK]:
        title = (a.get("action_label") or "").strip()[:120]
        desc  = (a.get("note") or "").strip()[:180]
        if not title and not desc:
            continue
        lines.append(f"{a['asset_id']} | {a.get('channel')} | {title} | {desc}")
    if not lines:
        return {}
    prompt = f"""For each of these marketing assets, list any NAMED PEOPLE
featured. Only include people whose full name (or well-known mononym) appears
in the title or description. If no named person is featured, return an empty
list for that asset.

Format: one line per asset, "asset_id | channel | title | description"

Assets:
{chr(10).join(lines)}

Return ONLY JSON:
{{
  "asset_talent": [
    {{"asset_id": "<id>", "talent": ["First Last", ...]}},
    ...
  ]
}}
"""
    try:
        resp = claude_fn(
            system="You return only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500, temperature=0.2,
        )
        parsed = _extract_json(resp)
        if not parsed or not isinstance(parsed.get("asset_talent"), list):
            return {}
        out: Dict[str, List[str]] = {}
        for r in parsed["asset_talent"]:
            if not isinstance(r, dict): continue
            aid = r.get("asset_id")
            names = r.get("talent")
            if not aid or not isinstance(names, list): continue
            clean = [str(n).strip()[:64] for n in names
                     if isinstance(n, str) and n.strip() and len(n.strip()) < 64]
            if clean:
                out[aid] = clean
        return out
    except Exception as e:
        logger.info("Intent ingest: talent extraction failed (%s)", e)
        return {}


# =====================================================================
# Phase grouping
# =====================================================================

_PHASE_COLORS = ["#22c55e", "#3b82f6", "#eab308", "#a855f7", "#ef4444",
                  "#14b8a6", "#f97316", "#ec4899", "#6366f1"]


def _parse_date(s: str) -> Optional[date_cls]:
    if not s: return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try: return datetime.strptime(s.strip()[:10], fmt).date()
        except Exception: pass
    return None


def _spread_dates(assets: List[dict], phase: dict) -> None:
    """Assign a synthetic posted_date to any asset in the phase that
    doesn't already have one, spread evenly across the phase window."""
    start = _parse_date(phase.get("start_date"))
    end   = _parse_date(phase.get("end_date")) or start
    if not start:
        return
    total = max((end - start).days if end else 0, 0) or 1
    step = total / max(len(assets), 1)
    for i, a in enumerate(assets):
        if not a.get("posted_date"):
            d = start + timedelta(days=int(step * i))
            a["posted_date"] = d.isoformat()


def build_phases(campaign_name: str,
                  campaign_launch: str,
                  campaign_end: str,
                  assets: List[dict],
                  user_phases: Optional[List[dict]] = None) -> List[dict]:
    """Turn user input into a phase list + assign each asset to a phase.

    If the user supplied `user_phases` (each: {name, start_date, end_date}),
    we honor that split. Otherwise the whole campaign is one phase.
    """
    if user_phases:
        phases_out = []
        for i, p in enumerate(user_phases):
            phases_out.append({
                "phase_name":  (p.get("name") or f"Phase {i+1}")[:120],
                "phase_order": i + 1,
                "start_date":  p.get("start_date") or campaign_launch,
                "end_date":    p.get("end_date") or campaign_end or None,
                "description": (p.get("description") or "")[:400],
                "color_hex":   _PHASE_COLORS[i % len(_PHASE_COLORS)],
            })
    else:
        phases_out = [{
            "phase_name":  campaign_name or "Campaign",
            "phase_order": 1,
            "start_date":  campaign_launch,
            "end_date":    campaign_end or None,
            "description": f"Full {campaign_name} campaign window.",
            "color_hex":   _PHASE_COLORS[0],
        }]

    # Assign assets to phases. If we scraped a posted_date, put it in
    # the containing phase; otherwise round-robin so no phase is empty.
    by_phase: Dict[str, List[dict]] = {p["phase_name"]: [] for p in phases_out}
    for i, a in enumerate(assets):
        d = _parse_date(a.get("posted_date"))
        chosen = None
        if d:
            for p in phases_out:
                s = _parse_date(p["start_date"])
                e = _parse_date(p["end_date"]) or date_cls.today()
                if s and s <= d <= e:
                    chosen = p; break
        if not chosen:
            chosen = phases_out[i % len(phases_out)]
        a["phase_name"] = chosen["phase_name"]
        by_phase[chosen["phase_name"]].append(a)

    for p in phases_out:
        _spread_dates(by_phase[p["phase_name"]], p)
    return phases_out


# =====================================================================
# Main entry
# =====================================================================

def run_ingest(job_id: str, params: dict,
                progress_cb: Optional[Callable[[int, str], None]] = None,
                s3_client=None) -> dict:
    """Full pipeline. Called by the Flask worker via spawn_heavy_analysis.

    Expected `params` keys (from the Analysis IQ form):
      brand_name           : str, required
      campaign_name        : str, required
      brand_category       : str, optional (dropdown from BRAND_CATEGORY_TERMINOLOGY_PRESETS)
      launch_date          : ISO date, required
      end_date             : ISO date, optional (blank = in-flight)
      attribution_window   : int (days), optional (defaults from category preset)
      asset_urls           : list[str], required
      notes                : str, optional
      phases               : list[dict], optional (each: name/start/end/description)
      terminology_overrides: dict, optional
      enabled_tabs_overrides: dict, optional
      brand_config_overrides: dict, optional

    Returns dict: { success, title_slug, s3_key, asset_count,
                    cohort_count, audience_count }
    """
    import boto3
    s3 = s3_client or boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))

    def _p(pct: int, msg: str):
        try:
            if progress_cb: progress_cb(pct, msg)
        except Exception:
            pass
        logger.info("Intent ingest [%s] %d%% %s", job_id, pct, msg)

    _p(5, "Starting brand-campaign ingest")

    brand    = (params.get("brand_name") or "").strip()
    campaign = (params.get("campaign_name") or "").strip()
    if not brand or not campaign:
        raise ValueError("brand_name and campaign_name are required")

    launch_date = (params.get("launch_date") or "").strip() \
                  or datetime.now(timezone.utc).date().isoformat()
    end_date    = (params.get("end_date") or "").strip()
    brand_cat   = (params.get("brand_category") or "").strip()
    notes       = (params.get("notes") or "").strip()
    attribution_days = int(params.get("attribution_window") or 0)

    asset_urls_raw = params.get("asset_urls") or []
    if isinstance(asset_urls_raw, str):
        asset_urls_raw = [u.strip() for u in re.split(r"[\s,;]+", asset_urls_raw)]
    urls = [u for u in (asset_urls_raw or []) if u and u.startswith(("http://", "https://"))]
    if not urls:
        raise ValueError("At least one asset URL is required")

    # Slug: brand + campaign combined so different campaigns of the same
    # brand co-exist. E.g. "chime__mama_i_made_it".
    slug_seed = f"{brand}__{campaign}" if brand.lower() not in campaign.lower() else campaign
    title_slug = _unique_slug(_slugify(slug_seed), s3=s3)

    _p(10, f"Parsed {len(urls)} URLs, title_slug='{title_slug}'")

    # ── Step 1: scrape asset metadata (thread pool for speed) ────────
    assets: List[dict] = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            for a in pool.map(scrape_asset, urls):
                a["title_slug"] = title_slug
                assets.append(a)
    except Exception:
        # Sequential fallback
        for u in urls:
            a = scrape_asset(u); a["title_slug"] = title_slug
            assets.append(a)

    _p(25, f"Scraped {len(assets)} assets ({sum(1 for a in assets if a.get('og_metadata'))} with OG meta)")

    # ── Step 2: Claude — talent extraction ───────────────────────────
    claude_fn = _claude()
    talent_map = extract_talent_from_assets(assets, claude_fn=claude_fn)
    all_talents: List[str] = []
    for aid, names in talent_map.items():
        for a in assets:
            if a["asset_id"] == aid:
                a["talent_tags"] = names
                for n in names:
                    if n not in all_talents:
                        all_talents.append(n)
                break
    _p(45, f"Extracted talent on {len(talent_map)} of {len(assets)} assets ({len(all_talents)} unique)")

    # ── Step 3: Claude — cohort synthesis ────────────────────────────
    cohorts = synthesize_cohorts(brand, campaign, brand_cat, notes, assets,
                                   claude_fn=claude_fn)
    _p(65, f"Synthesized {len(cohorts)} cohorts")

    # ── Step 4: Claude — audience synthesis ──────────────────────────
    audiences = synthesize_audiences(brand, campaign, brand_cat, notes, assets,
                                       talents=all_talents, claude_fn=claude_fn)
    _p(80, f"Synthesized {len(audiences)} audience cards (incl. {len(all_talents)} talent)")

    # ── Step 5: phases + terminology + enabled_tabs ──────────────────
    phases = build_phases(campaign, launch_date, end_date, assets,
                            user_phases=params.get("phases"))

    terminology = build_terminology_for_brand_category(
        brand_cat, custom_overrides=params.get("terminology_overrides"))
    if attribution_days > 0:
        terminology["attribution_window_days"] = attribution_days

    enabled_tabs = {
        "overview":            True,
        "assets":              True,
        "questions":           True,
        "q1_engagement":       True,
        "q2_paid_vs_organic":  True,
        "audiences":           True,
        "q3_intent_to_buy":    False,   # film-only
        "q4_talent":           True,
        "q5_trailer":          False,   # film-only
        "q6_cohorts":          True,
        "journey":             True,
        "inflight":            True,
        "conversion":          False,   # film-only (BO projector)
    }
    if isinstance(params.get("enabled_tabs_overrides"), dict):
        enabled_tabs.update(params["enabled_tabs_overrides"])

    brand_config = {
        "attribution_window_days": terminology.get("attribution_window_days", 14),
        "brand_category":          brand_cat,
    }
    if isinstance(params.get("brand_config_overrides"), dict):
        brand_config.update(params["brand_config_overrides"])

    # ── Step 6: assemble + write snapshot ────────────────────────────
    title_block = {
        "title_slug":            title_slug,
        "display_name":          campaign,
        "distributor":           brand,        # brand goes in the "distributor" slot (renders as "Brand" in brand-mode)
        "opening_date":          launch_date,
        "ticketing_open_date":   launch_date,
        "mpaa_rating":           "",
        "runtime_min":           0,
        "genre":                 brand_cat or "",
        "format_3d_imax":        "",
        "predicted_bo_low_usd":  0,
        "predicted_bo_high_usd": 0,
        "actual_opening_bo_usd": 0,
        "source":                "analysis_iq_form",
        "notes":                 notes,
        "ingested_by":           "migration/intent_ingest_agent.py",
        "title_type":            "brand",
        "terminology":           terminology,
        "enabled_tabs":          enabled_tabs,
        "brand_config":          brand_config,
    }
    snapshot = {
        "title":       title_block,
        "phases":      phases,
        "assets":      assets,
        "audiences":   audiences,
        "cohorts":     cohorts,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "job_id":      job_id,
    }
    snap_key = SNAPSHOT_KEY_FMT.format(slug=title_slug)
    body = json.dumps(snapshot, default=str).encode()
    s3.put_object(Bucket=S3_BUCKET, Key=snap_key, Body=body,
                    ContentType="application/json")
    _p(90, f"Wrote {len(body):,}-byte snapshot to s3://{S3_BUCKET}/{snap_key}")

    # ── Step 7: register in intent/registry.json ─────────────────────
    try:
        from migration.intent_register import register_intent_title
    except Exception:
        try:
            sys.path.insert(0, "/root/finished_codes")
            from migration.intent_register import register_intent_title  # type: ignore
        except Exception as e:
            logger.warning("Intent ingest: register_intent_title unavailable (%s); "
                            "writing registry directly", e)
            register_intent_title = None

    if register_intent_title:
        register_intent_title(
            title_slug=title_slug,
            display_name=campaign,
            distributor=brand,
            opening_date=launch_date,
            ticketing_open_date=launch_date,
            source_xlsx_s3_key=None,
            asset_count=len(assets),
            phases=[p["phase_name"] for p in phases],
            audiences_of_interest=[a["display"] for a in audiences],
            image_url=None,
            title_type="brand",
            terminology=terminology,
            enabled_tabs=enabled_tabs,
            brand_config=brand_config,
            s3_client=s3,
        )
    else:
        # Direct write fallback
        _direct_register(s3, title_slug, campaign, brand, launch_date,
                          len(assets), [p["phase_name"] for p in phases],
                          [a["display"] for a in audiences],
                          terminology, enabled_tabs, brand_config)

    _p(100, f"Complete — '{campaign}' now available in Intent IQ as '{title_slug}'")

    return {
        "success":         True,
        "title_slug":      title_slug,
        "display_name":    campaign,
        "s3_key":          snap_key,
        "asset_count":     len(assets),
        "cohort_count":    len(cohorts),
        "audience_count":  len(audiences),
        "talent_count":    len(all_talents),
        "phase_count":     len(phases),
    }


def _direct_register(s3, title_slug: str, display_name: str, brand: str,
                       launch_date: str, asset_count: int, phase_names: List[str],
                       audience_names: List[str], terminology: dict,
                       enabled_tabs: dict, brand_config: dict) -> None:
    """Fallback when migration.intent_register is unavailable."""
    try:
        r = s3.get_object(Bucket=S3_BUCKET, Key=REGISTRY_KEY)
        reg = json.loads(r["Body"].read().decode())
    except Exception:
        reg = {"titles": [], "schema_version": 1}
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "title_slug":            title_slug,
        "display_name":          display_name,
        "distributor":           brand,
        "opening_date":          launch_date,
        "ticketing_open_date":   launch_date,
        "source_xlsx_s3_key":    "",
        "asset_count":           asset_count,
        "phases":                phase_names,
        "audiences_of_interest": audience_names,
        "image_url":             "",
        "created_at":            now,
        "updated_at":            now,
        "title_type":            "brand",
        "terminology":           terminology,
        "enabled_tabs":          enabled_tabs,
        "brand_config":          brand_config,
    }
    others = [t for t in reg.get("titles", []) if t.get("title_slug") != title_slug]
    others.append(entry)
    reg["titles"] = others
    reg["updated_at"] = now
    reg["title_count"] = len(others)
    reg["schema_version"] = 1
    s3.put_object(
        Bucket=S3_BUCKET, Key=REGISTRY_KEY,
        Body=json.dumps(reg, indent=2, default=str).encode(),
        ContentType="application/json",
        CacheControl="no-cache, max-age=0",
    )


__all__ = [
    "run_ingest",
    "build_terminology_for_brand_category",
    "BRAND_CATEGORY_TERMINOLOGY_PRESETS",
    "scrape_asset",
    "synthesize_cohorts",
    "synthesize_audiences",
    "extract_talent_from_assets",
    "build_phases",
]
