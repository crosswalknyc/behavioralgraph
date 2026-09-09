"""Dollar-balance wallet + per-tool pricing (2026-09-08).

Jenna's mandate: "I want to enter something where people can either
sign up online for the dashboard and pay a fee that is charged to
their credit card or can buy additional credits with their credit
card or where an admin can put a credit card in and it charges their
prometheus charges to it."

Resolution (2026-09-08): single dollar wallet, admin sets per-tool
prices, Prometheus deducts metered Anthropic x 2.10 in real time.
Internal Crosswalk allowances (`credits` field) drain first, then the
wallet.

This module is PURE math + state. It does not touch Stripe. It does
not send emails. Callers (app.py routes, pay_per_use.py session close)
wire it into their own flows.

Public surface:

    load_pricing() -> dict                # per-tool USD costs
    save_pricing(pricing) -> dict
    tool_price_usd(tool_key) -> float
    prometheus_markup() -> float          # 2.10 currently

    wallet_balance(user) -> float
    is_paying_customer(user) -> bool
    admits_wallet_ui(user) -> bool        # who sees Buy Credits

    deduct_from_wallet(username, amount_usd, description, ...)
                        -> (ok, new_balance, txn_row)
    topup_wallet(username, amount_usd, description, ...)
                        -> (ok, new_balance, txn_row)

Deduction ordering (called from consume_credit's mutator):

    should_charge_wallet(user, credits_used, pricing) -> (usd, tool_key)
        Returns (usd_to_charge, tool_key) when the wallet should be hit
        AFTER credits are exhausted. (0.0, '') when internal allowance
        covers it OR user isn't a paying customer.

Idempotency and atomicity live in the caller (`_users_cas_mutate`).
This module writes to a users.json dict passed by reference; the CAS
loop retries on collision. That's the same pattern consume_credit
already uses.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Pricing config
# ---------------------------------------------------------------------------

# S3 key for the per-tool pricing dict. Admin panel writes here, all
# code paths read here. Bucket = METADATA_BUCKET from app.py.
PRICING_S3_KEY = "system/pricing.json"

# Local mirror (also read on cold-start when S3 is unreachable). Same
# treatment as other config files.
_LOCAL_PRICING_MIRROR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "_state", "pricing.json",
)

# Hardcoded fallback if neither S3 nor local mirror has anything. Set
# by Jenna 2026-09-08.
DEFAULT_PRICING = {
    "per_tool_usd": {
        # Standard IQ pulls
        "profile_iq_build": 500.0,
        "profile_iq_derived_cut": 100.0,
        "subscriber_iq_build": 1000.0,
        "chatbot_profile_iq_build": 500.0,
        # Analysis / journey / attribution modules (default 0 = free
        # until the admin sets a value). Every key here MUST have a
        # matching row in MODULE_CATALOG - otherwise it renders as
        # a phantom row in the admin billing "OTHER" (extras) section.
        # The 5 legacy keys that once lived here (analysis_iq,
        # attribution_iq, digital_journey_iq, rankers_iq, sf_conversion)
        # were removed on 2026-09-09 (Jenna): each duplicated a proper
        # catalog row under a different tool_key (intent_iq, journey_iq,
        # rankers_iq_access, sf_lf_conversion) or, in the case of
        # analysis_iq, was an obsolete umbrella flag with no per-tool
        # cost meaning.
        "impact_iq": 0.0,
        "trends_iq": 0.0,
        "sentiment_iq": 0.0,
        "brand_partnership_iq": 0.0,
        "flywheel_conversion": 0.0,
        "intent_iq": 0.0,
        "share_of_time": 0.0,
        "hedge_fund_iq": 0.0,
    },
    "top_up_packs_usd": [250, 500, 1000, 2500],
    "top_up_min_custom_usd": 100.0,
    "prometheus_markup_multiplier": 2.10,
    "auto_reload_defaults": {
        "threshold_usd": 500.0,
        "amount_usd": 1000.0,
    },
    "monthly_invoice_defaults": {
        "limit_usd": 5000.0,
    },
}


# ---------------------------------------------------------------------------
# Module catalog (Jenna 2026-09-09).
# ---------------------------------------------------------------------------
#
# Every access-controlled feature must have a row here so the pricing
# panel in /admin/billing renders a line item for it. When you add a
# new has_*_iq_access flag to the user admin section, ALSO add a row
# here, per pricing-catalog-registration.mdc.
#
# Columns:
#   tool_key      - stable key used by consume_credit's pull_type map
#                   and by system/pricing.json.per_tool_usd
#   display_name  - what the admin sees in the pricing table
#   section       - one of 'modules', 'rankers', 'api', 'subscription'
#                   Groups the pricing table into collapsible sections.
#   default_credits - matches the CREDITS_* constants in app.py
#                     (display-only in the pricing UI; the constants
#                     are the source of truth for credit deduction).
#   default_usd   - matches DEFAULT_PRICING.per_tool_usd (canonical
#                   dollar price when admin has not overridden).
#   access_flag   - the has_*_access field on the user record that
#                   toggles visibility in the dashboard. None when
#                   the tool is not directly gated by a flag (e.g.
#                   ranker sub-tabs live under has_rankers_iq_access).
#
MODULE_CATALOG = [
    # ---------- Core builds ----------
    ("profile_iq_build",           "Profile IQ - Full Build",
     "modules", 5, 500.0, "has_profile_iq_access"),
    ("profile_iq_derived_cut",     "Profile IQ - Derived Cut",
     "modules", 3, 100.0, "has_profile_iq_access"),
    ("subscriber_iq_build",        "Subscriber IQ",
     "modules", 10, 1000.0, "has_subscriber_iq_access"),
    ("chatbot_profile_iq_build",   "Chatbot Profile IQ",
     "modules", 5, 500.0, "has_chatbot_profile_iq_access"),
    ("chatbot_analysis",           "Chatbot - Analyze Ask",
     "modules", 1, 0.0, "has_chatbot_profile_iq_access"),
    ("chatbot_deck",               "Chatbot - Deck Export",
     "modules", 5, 0.0, "has_chatbot_profile_iq_access"),
    # ---------- Analysis / attribution ----------
    ("ecommerce_iq",               "Ecommerce IQ",
     "modules", 5, 0.0, "has_ecommerce_iq_access"),
    ("impact_iq",                  "Impact IQ",
     "modules", 10, 0.0, "has_impact_iq_access"),
    ("ticket_sales",               "Impact IQ - Ticket Sales",
     "modules", 10, 0.0, "has_ticket_sales_iq_access"),
    ("ticket_sales_tracker",       "Ticket Sales Tracker",
     "modules", 10, 0.0, "has_ticket_sales_tracker_access"),
    ("campaign_roi",               "Campaign ROI",
     "modules", 5, 0.0, None),
    ("roas_iq",                    "ROAS IQ",
     "modules", 8, 0.0, None),
    ("watch_time",                 "Watch Time",
     "modules", 1, 0.0, None),
    ("sf_lf_conversion",           "SF-LF Conversion",
     "modules", 10, 0.0, "has_sf_conversion_access"),
    ("flywheel_conversion",        "Flywheel Conversion",
     "modules", 25, 0.0, None),  # access_flag retired 2026-09-09
    ("brand_partnership_iq",       "Brand Partnership IQ",
     "modules", 15, 0.0, "has_brand_partnership_iq_access"),
    ("journey_iq",                 "Digital Journey IQ",
     "modules", 10, 0.0, "has_journey_iq_access"),
    ("share_of_time",              "Share of Time - View",
     "modules", 0, 0.0, "has_share_of_time_access"),
    ("share_of_time_run",          "Share of Time - Run",
     "modules", 0, 0.0, "has_share_of_time_run_access"),
    ("intent_iq",                  "Intent IQ",
     "modules", 50, 0.0, "has_intent_iq_access"),
    ("hedge_fund_iq",              "Hedge Fund IQ",
     "modules", 0, 0.0, "has_hedge_fund_iq_access"),
    ("blue_iq",                    "Blue IQ",
     "modules", 0, 0.0, "has_blue_iq_access"),
    ("brand_tracking_iq",          "Brand Tracking IQ",
     "modules", 0, 0.0, "has_brand_tracking_iq_access"),
    ("talent_fit",                 "Talent Fit",
     "modules", 5, 0.0, "has_talent_fit_access"),
    ("sentiment_iq",               "Sentiment IQ",
     "modules", 0, 0.0, None),  # access_flag retired 2026-09-09
    ("trends_iq",                  "Trends IQ",
     "modules", 0, 0.0, "has_trends_iq_access"),
    ("microdramas_iq",             "Microdramas IQ",
     "modules", 0, 0.0, "has_microdramas_iq_access"),
    # ---------- Rankers (sub-tabs under has_rankers_iq_access) ----------
    ("rankers_iq_access",          "Rankers IQ - Base Access",
     "rankers", 0, 0.0, "has_rankers_iq_access"),
    ("ranker_fast",                "FAST Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_music",               "Music Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_podcast",             "Podcast Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_streaming",           "Streaming Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_gaming",              "Gaming Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_talent",              "Talent Ranker",
     "rankers", 0, 0.0, None),
    # ---------- Partner API surface ----------
    ("api_profile_iq_build",       "API - Profile IQ Build",
     "api", 5, 500.0, None),
    ("api_profile_iq_cut",         "API - Profile IQ Cut",
     "api", 3, 100.0, None),
    ("api_subscriber_iq_build",    "API - Subscriber IQ Build",
     "api", 10, 1000.0, None),
    ("api_chatbot_profile_iq_build", "API - Chatbot Profile IQ",
     "api", 5, 500.0, None),
    # ---------- Recurring ----------
    ("monthly_service",            "Monthly Service (base access)",
     "subscription", 0, 0.0, None),
]


def module_catalog() -> list:
    """Return the module catalog as list of dicts. Auto-registration
    for new access flags lands here per pricing-catalog-registration.mdc.
    """
    p = load_pricing()
    per_tool = p.get("per_tool_usd", {}) or {}
    rows = []
    for tool_key, display, section, def_cr, def_usd, flag in MODULE_CATALOG:
        rows.append({
            "tool_key": tool_key,
            "display_name": display,
            "section": section,
            "credits": int(def_cr),
            "usd": float(per_tool.get(tool_key, def_usd)),
            "default_usd": float(def_usd),
            "access_flag": flag,
        })
    # Fold in any pricing keys the admin has set that AREN'T in
    # MODULE_CATALOG yet (defence-in-depth so an ad-hoc price never
    # goes invisible). They land in an "extras" section so ops can
    # see them.
    catalog_keys = {tk for tk, *_ in MODULE_CATALOG}
    for k, v in per_tool.items():
        if k in catalog_keys:
            continue
        try:
            usd = float(v)
        except (TypeError, ValueError):
            continue
        rows.append({
            "tool_key": k,
            "display_name": k.replace("_", " ").title(),
            "section": "extras",
            "credits": 0,
            "usd": usd,
            "default_usd": 0.0,
            "access_flag": None,
        })
    return rows


# Cache the last-read pricing so repeated tool_price_usd() calls in a
# request don't refetch. Callers who need fresh values (admin save)
# invalidate via _clear_pricing_cache.
_pricing_cache: dict = {"value": None, "loaded_at": 0.0}
_PRICING_CACHE_TTL_S = 30.0


def _clear_pricing_cache():
    _pricing_cache["value"] = None
    _pricing_cache["loaded_at"] = 0.0


def _now_ts() -> float:
    import time
    return time.time()


def load_pricing(*, force_reload: bool = False) -> dict:
    """Return the effective pricing dict. Cached ~30s.

    Loads from S3 first; falls back to the local mirror; falls back to
    DEFAULT_PRICING. Never raises: a missing pricing file is a fresh
    install and DEFAULT_PRICING is authoritative.
    """
    if (not force_reload
            and _pricing_cache["value"] is not None
            and (_now_ts() - _pricing_cache["loaded_at"])
                < _PRICING_CACHE_TTL_S):
        return _pricing_cache["value"]
    doc = None
    # S3 first (canonical). Import lazily so this module has no hard
    # dep on app.py's boto3 client during tests.
    try:
        from app import s3_client, METADATA_BUCKET  # type: ignore
        if s3_client:
            try:
                resp = s3_client.get_object(
                    Bucket=METADATA_BUCKET, Key=PRICING_S3_KEY)
                raw = resp["Body"].read().decode("utf-8")
                doc = json.loads(raw)
            except Exception as e:
                _msg = str(e)
                if "NoSuchKey" not in _msg and "404" not in _msg:
                    print(f"[wallet] pricing S3 load failed: {e}")
    except Exception:
        # app not importable (tests) - fall through to local mirror.
        pass
    if doc is None:
        try:
            if os.path.exists(_LOCAL_PRICING_MIRROR):
                with open(_LOCAL_PRICING_MIRROR, "r") as fh:
                    doc = json.load(fh)
        except Exception as e:
            print(f"[wallet] pricing local mirror load failed: {e}")
    if doc is None or not isinstance(doc, dict):
        doc = json.loads(json.dumps(DEFAULT_PRICING))  # deep copy
    # Ensure the shape has at least the DEFAULT_PRICING keys. New
    # per-tool keys added to DEFAULT_PRICING after a customer already
    # saved their own pricing land at their default (0.0 for optional,
    # canonical for standard pulls).
    merged = json.loads(json.dumps(DEFAULT_PRICING))
    for k, v in doc.items():
        if k == "per_tool_usd" and isinstance(v, dict):
            merged["per_tool_usd"].update(
                {kk: float(vv) for kk, vv in v.items()
                 if isinstance(vv, (int, float))})
        else:
            merged[k] = v
    _pricing_cache["value"] = merged
    _pricing_cache["loaded_at"] = _now_ts()
    return merged


def save_pricing(new_pricing: dict) -> dict:
    """Persist pricing to S3 + local mirror. Admin-only surface.

    Returns the effective merged dict on success. Does NOT enforce
    role gating; callers must already have confirmed the caller is a
    super_admin.
    """
    # Merge over defaults so partial saves (admin only changed
    # profile_iq_build) don't clobber the rest.
    merged = json.loads(json.dumps(DEFAULT_PRICING))
    if isinstance(new_pricing, dict):
        for k, v in new_pricing.items():
            if k == "per_tool_usd" and isinstance(v, dict):
                merged["per_tool_usd"].update({
                    kk: float(vv) for kk, vv in v.items()
                    if isinstance(vv, (int, float)) and float(vv) >= 0})
            elif k in ("top_up_packs_usd",) and isinstance(v, list):
                merged[k] = [
                    float(x) for x in v
                    if isinstance(x, (int, float)) and float(x) > 0]
            elif k in ("top_up_min_custom_usd",
                       "prometheus_markup_multiplier") \
                    and isinstance(v, (int, float)):
                merged[k] = float(v)
            elif k == "prometheus_markup" \
                    and isinstance(v, (int, float)):
                # Alias so the admin UI can POST either spelling.
                merged["prometheus_markup_multiplier"] = float(v)
            elif k in ("auto_reload_defaults",
                       "monthly_invoice_defaults") \
                    and isinstance(v, dict):
                merged[k] = {kk: float(vv) for kk, vv in v.items()
                             if isinstance(vv, (int, float))}
    # Write to S3
    try:
        from app import s3_client, METADATA_BUCKET  # type: ignore
        if s3_client:
            body = json.dumps(merged, indent=2).encode("utf-8")
            s3_client.put_object(
                Bucket=METADATA_BUCKET, Key=PRICING_S3_KEY,
                Body=body, ContentType="application/json")
    except Exception as e:
        print(f"[wallet] pricing S3 save failed: {e}")
    # Local mirror best-effort
    try:
        os.makedirs(os.path.dirname(_LOCAL_PRICING_MIRROR), exist_ok=True)
        with open(_LOCAL_PRICING_MIRROR, "w") as fh:
            json.dump(merged, fh, indent=2)
    except Exception as e:
        print(f"[wallet] pricing local mirror save failed: {e}")
    _clear_pricing_cache()
    return merged


def tool_price_usd(tool_key: str) -> float:
    """USD price for a single pull of the named tool. 0.0 for unset
    tools (free until admin sets a value)."""
    p = load_pricing()
    return float(p.get("per_tool_usd", {}).get(str(tool_key), 0.0))


# Map free-form pull_type strings (as passed to consume_credit) to the
# canonical pricing key. Admins configure prices against the canonical
# keys in system/pricing.json + MODULE_CATALOG (bg-webapp/wallet.py).
# Unknown pull_types map to '' -> no wallet charge, existing credits-
# only behavior preserved.
#
# EVERY key here MUST match a MODULE_CATALOG tool_key. Adding a new
# consume_credit call site? Add the pull_type -> tool_key mapping here
# in the same commit, add a MODULE_CATALOG row for the tool_key, and
# extend scripts/test_pull_type_mapping.py.
#
# Prior bug (2026-09-08 -> 2026-09-09): every Attribution IQ tool
# (ticket_sales, campaign_roi, watch_time, sf_lf_conversion,
# flywheel_conversion, roas_iq, ticket_sales_tracker) collapsed to a
# fake 'attribution_iq' key that doesn't exist in MODULE_CATALOG -> a
# paying customer running those tools got a $0 wallet charge every
# time regardless of the admin-set price. Similarly, every Chatbot
# Profile IQ variant landed under `chatbot_profile_iq_(new_build)`
# with parens preserved in the normalized key. Fix: exact map for the
# simple cases + prefix logic for the parenthesized decision variants
# and the `v1` (partner API) suffix.
_PULL_TYPE_TO_TOOL_KEY = {
    # ---- Profile IQ / Chatbot Profile IQ ----
    "profile analysis":              "profile_iq_build",
    "profile iq":                    "profile_iq_build",
    "profile iq build":              "profile_iq_build",
    "chatbot profile iq":            "chatbot_profile_iq_build",
    "chatbot profile iq build":      "chatbot_profile_iq_build",
    # Derived cuts (any cohort cut of an existing profile).
    "derived cut":                   "profile_iq_derived_cut",
    "derive cut":                    "profile_iq_derived_cut",
    "avid cut":                      "profile_iq_derived_cut",
    "gender cut":                    "profile_iq_derived_cut",
    "age cut":                       "profile_iq_derived_cut",
    "geo cut":                       "profile_iq_derived_cut",
    "behavioral cut":                "profile_iq_derived_cut",
    # ---- Subscriber IQ ----
    "subscriber iq":                 "subscriber_iq_build",
    "subscriber iq build":           "subscriber_iq_build",
    "svod":                          "subscriber_iq_build",
    # ---- Attribution / marketing modules ----
    # Each has its OWN MODULE_CATALOG row so the admin billing panel
    # can price them independently. NEVER collapse to a shared bucket.
    "ticket sales":                  "ticket_sales",
    "ticket sales tracker":          "ticket_sales_tracker",
    "sf-lf conversion":              "sf_lf_conversion",
    "sf lf conversion":              "sf_lf_conversion",
    "flywheel conversion":           "flywheel_conversion",
    "campaign roi":                  "campaign_roi",
    "watch time":                    "watch_time",
    "roas iq":                       "roas_iq",
    # ---- Digital Journey / Intent / Sentiment ----
    "digital journey iq":            "journey_iq",
    "journey iq":                    "journey_iq",
    "attribution iq ingest":         "intent_iq",
    "intent iq":                     "intent_iq",
    "intent ingest":                 "intent_iq",
    "sentiment iq":                  "sentiment_iq",
    # ---- Impact / Brand ----
    "impact iq":                     "impact_iq",
    "brand partnership iq":          "brand_partnership_iq",
    "brand partnership valuation":   "brand_partnership_iq",
    "brand tracking iq":             "brand_tracking_iq",
    # ---- Talent Fit ----
    "talent fit assessment":         "talent_fit",
    "talent fit":                    "talent_fit",
    "find me talent":                "talent_fit",
    # ---- Chatbot secondary flows (analysis + deck) ----
    "chatbot analysis":              "chatbot_analysis",
    "chatbot deck":                  "chatbot_deck",
    # ---- Rankers ----
    "rankers iq":                    "rankers_iq_access",
    "ranker fast":                   "ranker_fast",
    "ranker music":                  "ranker_music",
    "ranker podcast":                "ranker_podcast",
    "ranker streaming":              "ranker_streaming",
    "ranker gaming":                 "ranker_gaming",
    "ranker talent":                 "ranker_talent",
    # ---- Other modules currently gated only by feature flag ----
    "ecommerce iq":                  "ecommerce_iq",
    "hedge fund iq":                 "hedge_fund_iq",
    "blue iq":                       "blue_iq",
    "trends iq":                     "trends_iq",
    "microdramas iq":                "microdramas_iq",
    "share of time":                 "share_of_time",
    "share of time run":             "share_of_time_run",
}


def pull_type_to_tool_key(pull_type: str) -> str:
    """Map a consume_credit pull_type argument to a pricing key.

    Handles three shapes:
      1. Exact matches from _PULL_TYPE_TO_TOOL_KEY (simple case).
      2. Chatbot Profile IQ variants with a decision suffix. Two
         call sites emit these:
           - dashboard chatbot: "Chatbot Profile IQ (new_build)",
             "Chatbot Profile IQ (existing_match)",
             "Chatbot Profile IQ (derive_cut)", etc.
           - partner API v1: "Chatbot Profile IQ v1 (new_build)", etc.
         The `v1` suffix routes to the api_* pricing keys so admins
         can set a different price for API-driven builds than for
         dashboard-driven builds. `derive_cut` in either decision
         routes to the derived-cut price; `cut_needs_parent` builds
         a fresh parent AND derives the cut, so it charges the FULL
         build price (higher of the two).
      3. Normalized fallback for pull_types not in the map (e.g. an
         ad-hoc "custom_flow"): strip any parenthesized suffix, lower
         + underscore. Admins can price these by adding the resulting
         key to system/pricing.json.

    NEVER returns a paren-wrapped key like "chatbot_profile_iq_(new_build)".
    """
    pt = str(pull_type or "").strip().lower()
    if not pt:
        return ""
    if pt in _PULL_TYPE_TO_TOOL_KEY:
        return _PULL_TYPE_TO_TOOL_KEY[pt]

    # Chatbot Profile IQ variants with a decision suffix.
    if pt.startswith("chatbot profile iq"):
        is_v1 = " v1 " in pt or pt.startswith("chatbot profile iq v1")
        decision = ""
        if "(" in pt and ")" in pt:
            decision = pt[pt.index("(") + 1:pt.index(")")].strip()
        # `derive_cut` = pure cut of an existing parent -> cut price.
        # `cut_needs_parent` = fresh parent + cut -> full build price.
        # (The word "cut" appears in both decision names, so we match
        # on the exact decision string.)
        if decision == "derive_cut":
            return "api_profile_iq_cut" if is_v1 else "profile_iq_derived_cut"
        return "api_chatbot_profile_iq_build" if is_v1 else "chatbot_profile_iq_build"

    # Normalized fallback: strip parens + collapse spaces/dashes.
    if "(" in pt:
        pt = pt.split("(", 1)[0].strip()
    return pt.replace(" ", "_").replace("-", "_").strip("_")


def prometheus_markup() -> float:
    """Multiplier applied to raw Anthropic cost for Prometheus session
    billing. Default 2.10 per Jenna's 110%-markup mandate."""
    p = load_pricing()
    return float(p.get("prometheus_markup_multiplier", 2.10))


def top_up_pack_sizes() -> list:
    """Ordered list of USD amounts for the Buy Credits page."""
    p = load_pricing()
    packs = p.get("top_up_packs_usd") or []
    return [float(x) for x in packs if float(x) > 0]


def top_up_min_custom() -> float:
    """Minimum custom top-up amount (Stripe charges $0.30 flat + 2.9%,
    so we set a floor to avoid churning tiny top-ups)."""
    p = load_pricing()
    return float(p.get("top_up_min_custom_usd", 100.0))


# ---------------------------------------------------------------------------
# Wallet reads
# ---------------------------------------------------------------------------

def wallet_balance(user: dict) -> float:
    """Current $ balance for a users.json user record. 0.0 for a
    non-paying customer."""
    if not user:
        return 0.0
    try:
        return float(user.get("wallet_balance_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def is_paying_customer(user: dict) -> bool:
    """Whether this user's pulls should route through the wallet
    after internal allowance drains. `paying_customer` flag is
    admin-set."""
    if not user:
        return False
    return bool(user.get("paying_customer"))


def is_unlimited(user: dict) -> bool:
    """True when the user carries the unlimited-credits sentinel
    (``credits == -1``).

    Jenna 2026-09-09 (verbatim): *"if osmeone has unlimited enabled
    it shouldnt ever actually charge them so in that case billing
    would be off for that perosn"*.

    consume_credit already skips the wallet fallback when
    ``user_unlimited`` is True (see app.py :: consume_credit), so
    unlimited users never trigger a wallet deduction organically.
    This helper is the defence-in-depth: every wallet-charging code
    path consults it and short-circuits, so a future new charging
    path cannot accidentally bill an unlimited user.

    Company-pool unlimited (pool total == -1) is enforced in
    consume_credit's pool branch; that state doesn't live on the
    user record itself so this helper only inspects the personal
    ``credits`` value.
    """
    if not user:
        return False
    try:
        return int(user.get("credits", 0) or 0) == -1
    except (TypeError, ValueError):
        return False


def admits_wallet_ui(user: dict) -> bool:
    """Whether the 'Add Funds' / wallet-balance UI should render for
    this user. Jenna 2026-09-08: super_admin + paying_customer=true.
    Everyone else keeps the existing credits-only UX."""
    if not user:
        return False
    if user.get("role") == "super_admin":
        return True
    return is_paying_customer(user)


def billing_mode(user: dict) -> str:
    """One of 'prepay_only', 'auto_reload', 'monthly_invoice'.

    Default 'prepay_only' - no card on file, user must top up
    manually before every empty-wallet event.
    """
    if not user:
        return "prepay_only"
    m = str(user.get("billing_mode") or "").strip().lower()
    if m in ("prepay_only", "auto_reload", "monthly_invoice"):
        return m
    return "prepay_only"


def auto_reload_threshold(user: dict) -> float:
    """Balance at or below which auto-reload triggers. Falls back to
    the pricing config's default."""
    if not user:
        return top_up_min_custom()
    v = user.get("auto_reload_threshold_usd")
    if isinstance(v, (int, float)) and float(v) >= 0:
        return float(v)
    return float(load_pricing().get("auto_reload_defaults", {})
                 .get("threshold_usd", 500.0))


def auto_reload_amount(user: dict) -> float:
    """How much to charge on an auto-reload trigger."""
    if not user:
        return 1000.0
    v = user.get("auto_reload_amount_usd")
    if isinstance(v, (int, float)) and float(v) >= 0:
        return float(v)
    return float(load_pricing().get("auto_reload_defaults", {})
                 .get("amount_usd", 1000.0))


def monthly_invoice_limit(user: dict) -> float:
    """Wallet may run negative up to this dollar amount when
    billing_mode='monthly_invoice'. Beyond the limit, pulls block."""
    if not user:
        return 0.0
    v = user.get("monthly_invoice_limit_usd")
    if isinstance(v, (int, float)) and float(v) >= 0:
        return float(v)
    return float(load_pricing().get("monthly_invoice_defaults", {})
                 .get("limit_usd", 5000.0))


def has_card_on_file(user: dict) -> bool:
    return bool(
        (user or {}).get("stripe_customer_id")
        and (user or {}).get("stripe_payment_method_id"))


def wallet_stats(user: dict) -> dict:
    """Compute rolling stats from wallet_transactions for the wallet
    page (Jenna 2026-09-09). Never raises. All figures are in USD.

    Returns:
        spend_this_month_usd: sum of `deduct` txns since the 1st of
                              the current calendar month (UTC).
        spend_last_30d_usd:   sum of `deduct` txns in the past 30 days.
        pulls_this_month:     count of `deduct` txns since the 1st.
        top_tool: {tool_key, display_name, usd} for the tool with
                  the highest spend this month, or None.
        next_reload_note:     short human string describing when the
                              next automatic action will fire, or ""
                              when nothing scheduled.
    """
    out = {
        "spend_this_month_usd": 0.0,
        "spend_last_30d_usd": 0.0,
        "pulls_this_month": 0,
        "top_tool": None,
        "next_reload_note": "",
    }
    if not user:
        return out
    txns = user.get("wallet_transactions") or []
    if not isinstance(txns, list):
        return out
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0,
                              microsecond=0)
    thirty_days_ago = now - timedelta(days=30)
    per_tool_month = {}  # tool_key -> total usd this month
    for t in txns:
        if not isinstance(t, dict):
            continue
        if str(t.get("kind") or "").lower() != "deduct":
            continue
        try:
            amt = abs(float(t.get("amount_usd", 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
        ts_str = str(t.get("ts") or "")
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
            ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= month_start:
            out["spend_this_month_usd"] = round(
                out["spend_this_month_usd"] + amt, 2)
            out["pulls_this_month"] += 1
            tk = str(t.get("tool") or "").strip()
            if tk:
                per_tool_month[tk] = round(
                    per_tool_month.get(tk, 0.0) + amt, 2)
        if ts >= thirty_days_ago:
            out["spend_last_30d_usd"] = round(
                out["spend_last_30d_usd"] + amt, 2)
    if per_tool_month:
        top_key, top_amt = max(per_tool_month.items(),
                               key=lambda kv: kv[1])
        # Map tool_key -> display via MODULE_CATALOG (fallback: title-case)
        display = top_key.replace("_", " ").title()
        for tk, disp, *_ in MODULE_CATALOG:
            if tk == top_key:
                display = disp
                break
        out["top_tool"] = {
            "tool_key": top_key,
            "display_name": display,
            "usd": top_amt,
        }
    # Next-action note
    mode = billing_mode(user)
    if mode == "auto_reload" and has_card_on_file(user):
        bal = wallet_balance(user)
        thr = auto_reload_threshold(user)
        if bal <= thr:
            out["next_reload_note"] = (
                f"Auto-reload will fire on your next pull "
                f"(balance ${bal:.2f} is at or below the "
                f"${thr:.2f} threshold).")
        else:
            out["next_reload_note"] = (
                f"Auto-reload fires when balance drops below "
                f"${thr:.2f}. Next top-up: ${auto_reload_amount(user):.2f}.")
    elif mode == "monthly_invoice":
        out["next_reload_note"] = (
            "Monthly invoice reconciles on the 1st of every month.")
    return out


# ---------------------------------------------------------------------------
# Wallet writes (in-place, called under _users_cas_mutate)
# ---------------------------------------------------------------------------

def _append_txn(user: dict, txn: dict, cap: int = 500):
    """Insert a transaction record at the head of the user's
    wallet_transactions list, capped at `cap` entries. Preserves
    audit history newest-first (same pattern as credit_usage_history)."""
    hist = user.setdefault("wallet_transactions", [])
    if not isinstance(hist, list):
        hist = []
    hist.insert(0, txn)
    user["wallet_transactions"] = hist[:cap]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_wallet_deduct(user: dict, amount_usd: float, *,
                        description: str = "",
                        tool_key: str = "",
                        job_id: str = "",
                        stripe_ref: str = "") -> dict:
    """Debit the wallet in place. Returns the transaction row.

    Called from consume_credit's _consume mutator AFTER internal
    credits (if any) have been decided. Amount is positive dollars;
    the wallet moves by -amount. May take balance negative for
    monthly_invoice mode (caller enforces the limit).

    Callers with atomicity requirements MUST invoke this inside the
    same _users_cas_mutate closure that reads the user record, so a
    concurrent top-up gets folded in on retry.
    """
    amt = round(float(amount_usd), 2)
    if amt <= 0:
        return {}
    old = wallet_balance(user)
    new = round(old - amt, 2)
    user["wallet_balance_usd"] = new
    user["wallet_lifetime_spend_usd"] = round(
        float(user.get("wallet_lifetime_spend_usd", 0.0) or 0.0) + amt, 2)
    txn = {
        "ts": _now_iso(),
        "kind": "deduct",
        "amount_usd": -amt,
        "balance_after_usd": new,
        "description": description or "Usage",
        "job_id": job_id,
        "tool": tool_key,
        "stripe_ref": stripe_ref,
    }
    _append_txn(user, txn)
    return txn


def apply_wallet_topup(user: dict, amount_usd: float, *,
                       description: str = "",
                       stripe_ref: str = "",
                       kind: str = "topup") -> dict:
    """Credit the wallet in place. Returns the transaction row.

    `kind` is one of 'topup' (customer prepay), 'auto_reload' (Stripe
    charged the saved card on auto-reload), 'refund' (Stripe refund
    or admin adjustment), 'adjustment' (admin manual credit).
    """
    amt = round(float(amount_usd), 2)
    if amt <= 0:
        return {}
    old = wallet_balance(user)
    new = round(old + amt, 2)
    user["wallet_balance_usd"] = new
    if kind in ("topup", "auto_reload"):
        user["wallet_lifetime_topups_usd"] = round(
            float(user.get("wallet_lifetime_topups_usd", 0.0) or 0.0)
            + amt, 2)
    txn = {
        "ts": _now_iso(),
        "kind": kind,
        "amount_usd": amt,
        "balance_after_usd": new,
        "description": description or "Top up",
        "job_id": "",
        "tool": "",
        "stripe_ref": stripe_ref,
    }
    _append_txn(user, txn)
    return txn


def apply_wallet_refund(user: dict, amount_usd: float, *,
                        description: str = "",
                        stripe_ref: str = "") -> dict:
    """Refund a previous deduction. Positive dollars adds to wallet."""
    return apply_wallet_topup(user, amount_usd,
                              description=description or "Refund",
                              stripe_ref=stripe_ref, kind="refund")


# ---------------------------------------------------------------------------
# Deduction routing (called by consume_credit)
# ---------------------------------------------------------------------------

def should_charge_wallet(user: dict, tool_key: str,
                        pricing: Optional[dict] = None) -> tuple:
    """Decide whether a pull for `tool_key` should hit the wallet AND
    at what dollar amount.

    Returns (usd_to_charge, mode) where:

      usd > 0  -> the wallet should be debited by this dollar amount.
                 The caller (consume_credit mutator) must call
                 apply_wallet_deduct() with the same amount.
      usd == 0 -> the wallet should not be touched. Either the user
                 isn't a paying customer, the tool is free, or the
                 caller's internal-credits path already covered it.

    Mode is diagnostic: 'wallet' | 'no_charge' | 'not_paying'.

    This function does NOT decide whether internal credits cover the
    pull - the existing consume_credit path handles that. When
    consume_credit determines the internal-credits path FAILED
    because the user is out of internal credits, THEN it consults
    should_charge_wallet to see if the wallet should absorb the pull.
    """
    if not user:
        return 0.0, "not_paying"
    if not is_paying_customer(user):
        return 0.0, "not_paying"
    if is_unlimited(user):
        # Defence-in-depth per Jenna 2026-09-09: unlimited users are
        # NEVER charged even if a future code path forgets to check.
        return 0.0, "unlimited"
    pricing = pricing or load_pricing()
    usd = float(pricing.get("per_tool_usd", {}).get(str(tool_key), 0.0))
    if usd <= 0:
        return 0.0, "no_charge"
    return round(usd, 2), "wallet"


def wallet_can_absorb(user: dict, amount_usd: float) -> tuple:
    """Whether the wallet has room for this deduction.

    Returns (can_absorb, reason_when_false).

    Rules by billing_mode:
      - prepay_only: balance must be >= amount.
      - auto_reload: balance >= amount OR (has_card_on_file and
                    after-auto-reload balance >= amount).
      - monthly_invoice: balance - amount >= -monthly_invoice_limit.
    """
    if amount_usd <= 0:
        return True, ""
    bal = wallet_balance(user)
    mode = billing_mode(user)
    if bal >= amount_usd:
        return True, ""
    if mode == "prepay_only":
        return False, "insufficient_balance_prepay"
    if mode == "auto_reload":
        if not has_card_on_file(user):
            return False, "auto_reload_no_card"
        # Assume auto-reload will succeed. The caller triggers the
        # Stripe charge synchronously and rolls back the deduct on
        # Stripe failure.
        return True, ""
    if mode == "monthly_invoice":
        limit = monthly_invoice_limit(user)
        after = bal - amount_usd
        if after >= -abs(limit):
            return True, ""
        return False, "monthly_invoice_limit_exceeded"
    return False, "unknown_billing_mode"


# ---------------------------------------------------------------------------
# Auto-reload trigger evaluation (post-deduction hook)
# ---------------------------------------------------------------------------

def needs_auto_reload(user: dict) -> tuple:
    """After a deduction, decide whether we should fire an auto-reload
    charge against the saved card.

    Returns (should_fire, amount_usd). should_fire=True only when:
      - billing_mode is 'auto_reload'
      - user has a saved card
      - current balance is at or below the threshold
    """
    if billing_mode(user) != "auto_reload":
        return False, 0.0
    if not has_card_on_file(user):
        return False, 0.0
    if wallet_balance(user) > auto_reload_threshold(user):
        return False, 0.0
    return True, auto_reload_amount(user)


def try_auto_reload(username: str, user_snapshot: dict) -> dict:
    """Post-CAS-write hook: fire the Stripe auto-reload charge when
    needed and credit the wallet.

    Called AFTER the deducting CAS mutation commits. Must not run
    inside a CAS mutator because it makes a Stripe network call.
    Returns a status dict:

      {"fired": bool, "amount_usd": float, "payment_intent_id": str,
       "error": str}

    Idempotency: keyed by (username, minute_bucket, amount_cents). A
    double-invocation within the same minute at the same amount folds
    into a single Stripe charge.

    Never raises. All error paths return {"fired": False,
    "error": "..."}.
    """
    result = {"fired": False, "amount_usd": 0.0,
              "payment_intent_id": "", "error": ""}
    try:
        should, amount = needs_auto_reload(user_snapshot)
        if not should or amount <= 0:
            return result
        try:
            import billing as _billing  # type: ignore
        except Exception as e:
            result["error"] = f"billing_module_unavailable: {e}"
            return result
        if not _billing.is_enabled():
            result["error"] = "stripe_not_enabled"
            return result
        cus_id = str(user_snapshot.get("stripe_customer_id") or "")
        pm_id = str(user_snapshot.get("stripe_payment_method_id") or "")
        if not (cus_id and pm_id):
            result["error"] = "missing_card_on_file"
            return result
        # Minute-bucket idempotency: two rapid deductions that both
        # drop the balance below threshold should not double-charge.
        from datetime import datetime, timezone
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        idem = (f"auto-reload-{username}-{int(amount * 100)}-"
                f"{bucket}")
        try:
            charge = _billing.charge_saved_card(
                customer_id=cus_id,
                payment_method_id=pm_id,
                amount_usd=amount,
                description="Auto-reload (wallet threshold)",
                username=username,
                metadata={
                    "purpose": "auto_reload",
                    "idempotency_key": idem,
                },
            )
        except _billing.BillingError as e:
            result["error"] = str(e)
            return result
        status = str(charge.get("status") or "").lower()
        if status != "succeeded":
            result["error"] = f"charge_not_completed: {status}"
            return result
        # Charge succeeded. Credit wallet under a fresh CAS.
        try:
            from app import _users_cas_mutate  # type: ignore
        except Exception:
            result["error"] = "cas_unavailable"
            return result

        pi_id = str(charge.get("id") or "")

        def _apply(data):
            u = (data.get("users") or {}).get(username)
            if not u:
                return None
            # Idempotency: if we already logged this pi as a topup,
            # skip (webhook may have arrived first).
            for t in list(u.get("wallet_transactions") or [])[:20]:
                if (str(t.get("stripe_ref") or "") == pi_id
                        and str(t.get("kind") or "")
                        in ("topup", "auto_reload")):
                    return None
            apply_wallet_topup(
                u, amount,
                description="Auto-reload",
                stripe_ref=pi_id,
                kind="auto_reload")
            return data

        _users_cas_mutate(_apply)
        result.update({
            "fired": True,
            "amount_usd": amount,
            "payment_intent_id": pi_id,
        })
        return result
    except Exception as e:
        result["error"] = f"unexpected: {e}"
        return result


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "PRICING_S3_KEY",
    "DEFAULT_PRICING",
    "MODULE_CATALOG", "module_catalog",
    "load_pricing", "save_pricing",
    "tool_price_usd", "prometheus_markup",
    "top_up_pack_sizes", "top_up_min_custom",
    "wallet_balance", "wallet_stats",
    "is_paying_customer", "is_unlimited", "admits_wallet_ui",
    "billing_mode", "auto_reload_threshold", "auto_reload_amount",
    "monthly_invoice_limit", "has_card_on_file",
    "apply_wallet_deduct", "apply_wallet_topup", "apply_wallet_refund",
    "should_charge_wallet", "wallet_can_absorb", "needs_auto_reload",
    "try_auto_reload",
]
