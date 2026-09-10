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
        # 2026-09-09 (Jenna, verbatim: 'please remove chatbot things
        # from modules. those are just access monthly not any per pull
        # things'). The dashboard-side Chatbot Profile IQ pull_type
        # still resolves to `chatbot_profile_iq_build` via
        # pull_type_to_tool_key so nothing crashes on route, but with
        # the default gone AND no MODULE_CATALOG row, tool_price_usd()
        # returns 0.0 - fires no per-pull charge. Chatbot access is
        # controlled solely by the has_chatbot_profile_iq_access
        # feature flag (a monthly-access product).
        # Partner API family - MUST mirror MODULE_CATALOG defaults so a
        # fresh install without a pricing.json override still charges
        # the sticker price (should_charge_wallet reads per_tool_usd
        # with no MODULE_CATALOG fallback, so an unlisted key silently
        # falls to $0). 2026-09-09 defence-in-depth add.
        "api_profile_iq_cut": 100.0,
        "api_subscriber_iq_build": 1000.0,
        "api_chatbot_profile_iq_build": 500.0,
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
    # Per-tool MONTHLY access fee (recurring). Split from per_tool_usd
    # (which is the per-pull metered charge) on Jenna's 2026-09-09
    # request: 'one would be a monthly fee for access to the features
    # ... and then one for pulling one'. Empty by default; admin sets
    # values in /admin/billing -> Pricing. When both are set, a paying
    # customer is billed the monthly for access + the per-pull on use.
    "per_tool_monthly_usd": {},
    # Built-in tools an admin has HIDDEN from the pricing panel to
    # reduce clutter (Jenna 2026-09-09: 'needs to be a way to delete
    # from there too'). Soft-hide only: the MODULE_CATALOG code still
    # defines the tool, tool_price_usd() still returns its price for
    # active billing, and users' has_*_access flags still function.
    # Only the admin panel skips these rows unless "Show hidden" is on.
    # Custom tools are HARD-DELETED (via remove_custom_tool) and do
    # not use this list.
    "hidden_tools": [],
    # 2026-09-09 (Jenna): admin-configured additions to the locked
    # metered default set. Prometheus is ALWAYS metered regardless
    # of what's here. This list lets an admin flip any OTHER tool to
    # session-metered via the /admin/billing per-row checkbox.
    "metered_tools": [],
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
# 2026-09-09 (Jenna): metered tools are session-billed via
# pay_per_use, never a per-pull line item. The admin billing pricing
# panel renders them with a METERED badge and no price fields (any
# price silently sent for these keys is a no-op). Only real pipeline
# pulls (Profile IQ, Subscriber IQ, their chatbot / partner API twins)
# carry a discrete per-pull charge.
#
# The set below is the LOCKED default - Prometheus is always metered
# by design and cannot be un-toggled from the admin panel. Admins can
# ADD additional tools to the metered set via pricing.json:
# metered_tools[] using mark_metered() / unmark_metered() (surfaced
# as per-row checkboxes on /admin/billing).
#
# 2026-09-09 (Jenna): `chatbot_analysis` and `chatbot_deck` were
# retired from MODULE_CATALOG entirely (dashboard chatbot access is
# a monthly product, not per-pull), so they no longer need to sit in
# this locked set - there's no row to render a METERED badge on.
METERED_TOOL_KEYS = frozenset({
    "prometheus",
})


def metered_tool_keys() -> frozenset:
    """Full set of tool_keys currently flagged as session-metered.

    Union of the LOCKED defaults (METERED_TOOL_KEYS - Prometheus,
    Analyze Ask, Deck Export) and any additions admins have made via
    the pricing panel toggle (persisted in pricing.json:metered_tools).
    """
    try:
        p = load_pricing()
    except Exception:
        # If pricing.json is unreadable for any reason, degrade to
        # the locked defaults - the Prometheus family stays metered
        # no matter what.
        return METERED_TOOL_KEYS
    admin = p.get("metered_tools") or []
    if not isinstance(admin, (list, tuple, set)):
        admin = []
    extras = {
        str(x).strip().lower() for x in admin
        if isinstance(x, str) and x.strip()
    }
    return frozenset(METERED_TOOL_KEYS | extras)


def is_metered_tool(tool_key: str) -> bool:
    """True iff the tool is session-metered (not per-pull priced).

    Reads from `metered_tool_keys()` which unions the locked
    defaults with the admin-configured pricing.json:metered_tools[].
    Used by /admin/billing to render a METERED badge in place of the
    per-month and per-pull price inputs, and by /api/admin/pricing to
    reject/ignore any incoming price for a metered tool.
    """
    return str(tool_key or "").strip().lower() in metered_tool_keys()


def is_metered_locked(tool_key: str) -> bool:
    """True iff the tool is one of the LOCKED metered defaults
    (Prometheus family). Admins cannot un-toggle these; the admin
    panel's per-row checkbox renders as checked + disabled."""
    return str(tool_key or "").strip().lower() in METERED_TOOL_KEYS


def mark_metered(tool_key: str) -> dict:
    """Add a tool to the admin-configured metered set. Idempotent.
    Raises CustomToolError if tool_key isn't a MODULE_CATALOG built-in
    OR a currently-registered custom tool. Locked defaults stay
    locked; marking one of them is a no-op that returns
    metered=True."""
    tk = str(tool_key or "").strip().lower()
    if not tk:
        raise CustomToolError("tool key is required")
    if tk in METERED_TOOL_KEYS:
        # Already metered by design; no state change needed.
        return {"tool_key": tk, "metered": True, "locked": True}
    builtin_keys = {t for t, *_ in MODULE_CATALOG}
    custom = load_pricing().get("custom_tools") or []
    custom_keys = {
        str(c.get("tool_key") or "").strip().lower()
        for c in custom if isinstance(c, dict)
    }
    if tk not in builtin_keys and tk not in custom_keys:
        raise CustomToolError(
            f"'{tk}' is not a known tool")
    current = load_pricing(force_reload=True)
    existing = [str(x) for x in (current.get("metered_tools") or [])]
    if tk not in existing:
        existing.append(tk)
    save_pricing({"metered_tools": existing})
    # A newly-metered tool should not silently keep an old per-pull
    # or monthly price sitting on it - force both to 0 so the row
    # visibly matches the metered contract on next load.
    save_pricing({
        "per_tool_usd": {tk: 0.0},
        "per_tool_monthly_usd": {tk: 0.0},
    })
    return {"tool_key": tk, "metered": True, "locked": False}


def unmark_metered(tool_key: str) -> dict:
    """Remove a tool from the admin-configured metered set.
    Idempotent. Raises CustomToolError if tool_key is a LOCKED
    default (Prometheus family)."""
    tk = str(tool_key or "").strip().lower()
    if not tk:
        raise CustomToolError("tool key is required")
    if tk in METERED_TOOL_KEYS:
        raise CustomToolError(
            f"'{tk}' is a locked metered tool. Prometheus, Analyze "
            f"Ask, and Deck Export are always metered by design.")
    current = load_pricing(force_reload=True)
    existing = [
        str(x) for x in (current.get("metered_tools") or [])
        if str(x).strip().lower() != tk
    ]
    save_pricing({"metered_tools": existing})
    return {"tool_key": tk, "metered": False, "locked": False}


MODULE_CATALOG = [
    # ---------- Module ACCESS (monthly, gated by has_*_access) ----------
    # 2026-09-09 (Jenna, verbatim: 'move these to a new section called
    # PULLS and add a subscriber IQ Pull. the one in modules should be
    # the one per month that is just accerss.'). The Modules section is
    # now a monthly-access product list: each row here represents the
    # recurring subscription for having the feature turned on for a
    # user. Per-pull events for these features live in the PULLS
    # section below (profile_iq_build, profile_iq_derived_cut,
    # subscriber_iq_build, prometheus). Admin UI renders modules rows
    # with only the "/ month" cell editable; per-pull cell hidden.
    ("profile_iq_access",          "Profile IQ Access",
     "modules", 0, 0.0, "has_profile_iq_access"),
    ("subscriber_iq_access",       "Subscriber IQ Access",
     "modules", 0, 0.0, "has_subscriber_iq_access"),
    ("prometheus_access",          "Prometheus Access",
     "modules", 0, 0.0, "has_prometheus_access"),
    # ---------- Pulls (per-pull dashboard-side events) ----------
    # Per-pull priced events fired from the dashboard. Admin UI renders
    # PULLS rows with only the "/ pull" cell editable; monthly cell
    # hidden. Session-metered rows (Prometheus) render a metered note
    # in place of the per-pull input.
    ("profile_iq_build",           "Profile IQ - Full Build",
     "pulls", 5, 500.0, "has_profile_iq_access"),
    ("profile_iq_derived_cut",     "Profile IQ - Derived Cut",
     "pulls", 3, 100.0, "has_profile_iq_access"),
    ("subscriber_iq_build",        "Subscriber IQ - Pull",
     "pulls", 10, 1000.0, "has_subscriber_iq_access"),
    # 2026-09-09 (Jenna, verbatim: 'please remove chatbot things
    # from modules. those are just access monthly not any per pull
    # things'). Three rows retired here:
    #
    #   chatbot_profile_iq_build      "Chatbot Profile IQ"
    #   chatbot_analysis              "Chatbot - Analyze Ask"
    #   chatbot_deck                  "Chatbot - Deck Export"
    #
    # Dashboard-side chatbot access is a MONTHLY product gated by the
    # per-user `has_chatbot_profile_iq_access` flag (set in the User
    # admin modal). A user with that flag runs the chatbot freely -
    # every conversational turn AND every real pipeline build fired
    # from inside the chatbot is bundled into their monthly access.
    #
    # Nothing in the routing changed. `pull_type_to_tool_key` still
    # maps 'Chatbot Profile IQ (new_build)' to
    # `chatbot_profile_iq_build`; that tool_key just has no MODULE
    # _CATALOG row and no DEFAULT_PRICING entry now, so
    # `tool_price_usd()` returns 0.0 and no per-pull charge fires.
    # If a future partner wants a paid chatbot-driven build, that
    # path is `api_chatbot_profile_iq_build` below (partner API,
    # per-pull priced).
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
    # ---------- Rankers (monthly-access family) ----------
    # 2026-09-09 (Jenna, verbatim: 'rankers should all be monthly no
    # pulls and rankers base access would be Rankers IQ - ALL and that
    # would be a better price so if you got the bundle youc ould have
    # that but if not it would be more expensive per one'). Rankers IQ
    # - ALL is the bundle tier - subscribing here unlocks every ranker
    # at a preferred bundle rate. The individual ranker rows carry a
    # HIGHER per-ranker monthly for solo access. Rankers render with
    # monthly-only pricing on the admin panel (no per-pull cell) and
    # no metered toggle (never session-metered).
    ("rankers_iq_access",          "Rankers IQ - ALL",
     "rankers", 0, 0.0, "has_rankers_iq_access"),
    ("ranker_fast",                "FAST Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_gaming",              "Gaming Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_music",               "Music Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_podcast",             "Podcast Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_streaming",           "Streaming Ranker",
     "rankers", 0, 0.0, None),
    ("ranker_talent",              "Talent Ranker",
     "rankers", 0, 0.0, None),
    # ---------- Partner API surface ----------
    # 2026-09-09 (Jenna): the legacy `api_profile_iq_build` row was
    # retired here. Every partner-API-driven Profile IQ build routes
    # through the interpret / chatbot path (`pull_type_to_tool_key`
    # maps 'Chatbot Profile IQ v1 (new_build)' to
    # api_chatbot_profile_iq_build), so keeping a second row was pure
    # drift risk - admin could edit one and forget the other. The
    # price quote in bg-webapp/app.py::_v1_price_usd_for now reads
    # api_chatbot_profile_iq_build directly, so quote == debit.
    ("api_profile_iq_cut",         "API - Profile IQ Cut",
     "api", 3, 100.0, None),
    ("api_subscriber_iq_build",    "API - Subscriber IQ Build",
     "api", 10, 1000.0, None),
    ("api_chatbot_profile_iq_build", "API - Chatbot Profile IQ",
     "api", 5, 500.0, None),
    # ---------- Ask-metered (Prometheus) ----------
    # Prometheus is priced by prometheus_markup_multiplier applied to
    # per-session usage, not by a flat per-pull rate. It lives in the
    # catalog so admin UIs (spend-scope picker, module list) can
    # reference it. Per-pull tool_price_usd stays 0.0; the actual
    # session billing amount is computed in migration/prometheus_*.
    ("prometheus",                 "Prometheus (Ask-metered)",
     "pulls", 0, 0.0, "has_prometheus_access"),
    # ---------- Recurring ----------
    ("monthly_service",            "Monthly Service (base access)",
     "subscription", 0, 0.0, None),
]


def module_catalog(*, include_hidden: bool = False) -> list:
    """Return the module catalog as list of dicts. Auto-registration
    for new access flags lands here per pricing-catalog-registration.mdc.

    include_hidden=False (default): built-in tools listed in
      pricing.json:hidden_tools are OMITTED from the returned list.
      This is what the admin pricing panel uses in its normal
      render, so hidden built-ins stop cluttering the UI.
    include_hidden=True: every built-in row is returned, with
      is_hidden=True stamped on the hidden ones so the caller
      (admin "Show hidden" toggle) can render them differently.

    Custom tools (added via add_custom_tool) are NEVER hidden via
    this list - they are HARD-DELETED via remove_custom_tool. The
    hidden_tools list is built-in-only by design.

    Three row sources, in this order:
      1. MODULE_CATALOG built-in tools (code-defined, is_custom=False,
         is_builtin=True). Admins can price them but NEVER delete them.
      2. pricing.json:custom_tools admin-added entries (is_custom=True,
         is_builtin=False). Admins can rename / re-price / delete.
      3. Orphan per_tool_usd keys that don't match #1 or #2 (defence-
         in-depth so an ad-hoc price never goes invisible).
         is_custom=False, is_builtin=False, section="extras".
    """
    p = load_pricing()
    per_tool = p.get("per_tool_usd", {}) or {}
    per_tool_monthly = p.get("per_tool_monthly_usd", {}) or {}
    hidden_tools = set(p.get("hidden_tools") or [])
    custom = p.get("custom_tools") or []
    rows = []
    for tool_key, display, section, def_cr, def_usd, flag in MODULE_CATALOG:
        is_hidden = tool_key in hidden_tools
        if is_hidden and not include_hidden:
            # Soft-hide: omit from default listings so the admin
            # panel stays clean. tool_price_usd() still reads the
            # price directly from pricing.json, so live billing is
            # unaffected - a hidden tool is invisible in the panel
            # but still charges its configured price when its
            # pull_type fires.
            continue
        rows.append({
            "tool_key": tool_key,
            "display_name": display,
            "section": section,
            "credits": int(def_cr),
            "usd": float(per_tool.get(tool_key, def_usd)),
            "default_usd": float(def_usd),
            "monthly_usd": float(per_tool_monthly.get(tool_key, 0.0)),
            "default_monthly_usd": 0.0,
            "access_flag": flag,
            "is_builtin": True,
            "is_custom": False,
            "is_hidden": is_hidden,
            # 2026-09-09 (Jenna): the admin billing pricing panel
            # renders metered tools with a METERED badge in place of
            # per-month / per-pull price inputs. `metered` reads from
            # the union of LOCKED defaults + admin-added set;
            # `metered_locked` marks the Prometheus family (which
            # admins cannot un-toggle from the panel).
            "metered": is_metered_tool(tool_key),
            "metered_locked": tool_key in METERED_TOOL_KEYS,
        })
    builtin_keys = {tk for tk, *_ in MODULE_CATALOG}
    custom_keys = set()
    for c in custom:
        if not isinstance(c, dict):
            continue
        tk = str(c.get("tool_key") or "").strip()
        if not tk or tk in builtin_keys or tk in custom_keys:
            # Skip empties, collisions with builtins (builtin wins),
            # and dup custom keys within the array.
            continue
        custom_keys.add(tk)
        rows.append({
            "tool_key": tk,
            "display_name": str(c.get("display_name")
                                or tk.replace("_", " ").title()),
            "section": str(c.get("section") or "custom"),
            "credits": int(c.get("credits") or 0),
            "usd": float(per_tool.get(tk, c.get("default_usd") or 0.0)),
            "default_usd": float(c.get("default_usd") or 0.0),
            "monthly_usd": float(per_tool_monthly.get(
                tk, c.get("default_monthly_usd") or 0.0)),
            "default_monthly_usd": float(
                c.get("default_monthly_usd") or 0.0),
            "access_flag": (str(c.get("access_flag"))
                            if c.get("access_flag") else None),
            "is_builtin": False,
            "is_custom": True,
            "is_hidden": False,
            # Custom tools can also be toggled metered via the admin
            # panel. `metered` reads from the same admin-added set;
            # only the Prometheus family is locked (custom tools
            # are never locked, so admins can always un-toggle).
            "metered": is_metered_tool(tk),
            "metered_locked": False,
        })
    # Fold in any orphan per_tool_usd / per_tool_monthly_usd keys
    # (neither builtin nor custom) so a rogue price never goes
    # invisible in the admin panel.
    orphan_keys = set()
    for k in list(per_tool.keys()) + list(per_tool_monthly.keys()):
        if k in builtin_keys or k in custom_keys or k in orphan_keys:
            continue
        orphan_keys.add(k)
        try:
            usd = float(per_tool.get(k, 0.0))
        except (TypeError, ValueError):
            usd = 0.0
        try:
            monthly = float(per_tool_monthly.get(k, 0.0))
        except (TypeError, ValueError):
            monthly = 0.0
        rows.append({
            "tool_key": k,
            "display_name": k.replace("_", " ").title(),
            "section": "extras",
            "credits": 0,
            "usd": usd,
            "default_usd": 0.0,
            "monthly_usd": monthly,
            "default_monthly_usd": 0.0,
            "access_flag": None,
            "is_builtin": False,
            "is_custom": False,
            "is_hidden": False,
            # Orphan rows honour the standing metered set so a stray
            # per_tool_usd entry for a metered key still renders as
            # metered (defence in depth). Locked = default only.
            "metered": is_metered_tool(k),
            "metered_locked": k in METERED_TOOL_KEYS,
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

    Merge behavior (2026-09-09 tightened): start from the CURRENT
    on-disk pricing (falling back to DEFAULT_PRICING when there is
    no on-disk state), then overlay incoming keys. This preserves
    unrelated keys on partial saves (e.g. saving just the markup no
    longer wipes per_tool_usd or custom_tools).
    """
    # Start from current on-disk state so a partial save preserves
    # every other key.
    try:
        base = load_pricing(force_reload=True)
        if not isinstance(base, dict):
            base = {}
    except Exception:
        base = {}
    merged = json.loads(json.dumps(DEFAULT_PRICING))
    # Overlay base on defaults (base wins).
    for k, v in base.items():
        if k == "per_tool_usd" and isinstance(v, dict):
            merged["per_tool_usd"].update({
                kk: float(vv) for kk, vv in v.items()
                if isinstance(vv, (int, float))})
        elif k == "per_tool_monthly_usd" and isinstance(v, dict):
            merged.setdefault("per_tool_monthly_usd", {})
            merged["per_tool_monthly_usd"].update({
                kk: float(vv) for kk, vv in v.items()
                if isinstance(vv, (int, float))})
        elif k == "hidden_tools" and isinstance(v, list):
            # De-duped, string-normalized. Replaces (does not merge)
            # so unhide removes an entry cleanly on save.
            merged["hidden_tools"] = sorted({
                str(x) for x in v if isinstance(x, str) and x.strip()
            })
        elif k == "metered_tools" and isinstance(v, list):
            # Same shape as hidden_tools. Replaces on save so unmark
            # cleanly drops an entry.
            merged["metered_tools"] = sorted({
                str(x).strip().lower() for x in v
                if isinstance(x, str) and x.strip()
            })
        else:
            merged[k] = v
    # Ensure the monthly dict + hidden + metered lists exist even
    # when the on-disk doc predates the split (older pricing.json).
    merged.setdefault("per_tool_monthly_usd", {})
    merged.setdefault("hidden_tools", [])
    merged.setdefault("metered_tools", [])
    if isinstance(new_pricing, dict):
        # Explicit-delete support (needed by remove_custom_tool). The
        # additive .update() below can't drop keys from per_tool_usd
        # on a partial save; process the delete lists first so their
        # entries are gone before we merge in any incoming prices.
        _delete_keys = new_pricing.get("_delete_per_tool_keys")
        if isinstance(_delete_keys, (list, tuple, set)):
            for _k in _delete_keys:
                merged.get("per_tool_usd", {}).pop(str(_k), None)
        _delete_monthly = new_pricing.get("_delete_per_tool_monthly_keys")
        if isinstance(_delete_monthly, (list, tuple, set)):
            for _k in _delete_monthly:
                merged.get("per_tool_monthly_usd", {}).pop(str(_k), None)
        for k, v in new_pricing.items():
            if k in ("_delete_per_tool_keys",
                     "_delete_per_tool_monthly_keys"):
                continue
            if k == "per_tool_usd" and isinstance(v, dict):
                merged["per_tool_usd"].update({
                    kk: float(vv) for kk, vv in v.items()
                    if isinstance(vv, (int, float)) and float(vv) >= 0})
            elif k == "per_tool_monthly_usd" and isinstance(v, dict):
                merged["per_tool_monthly_usd"].update({
                    kk: float(vv) for kk, vv in v.items()
                    if isinstance(vv, (int, float)) and float(vv) >= 0})
            elif k == "hidden_tools" and isinstance(v, list):
                # Replace, don't merge. Callers pass the full desired
                # list (hide_builtin_tool + unhide_builtin_tool below
                # rebuild the list before calling save_pricing).
                merged["hidden_tools"] = sorted({
                    str(x) for x in v
                    if isinstance(x, str) and x.strip()
                })
            elif k == "metered_tools" and isinstance(v, list):
                # Same replace-not-merge shape as hidden_tools.
                # mark_metered / unmark_metered rebuild the desired
                # list before calling save_pricing.
                merged["metered_tools"] = sorted({
                    str(x).strip().lower() for x in v
                    if isinstance(x, str) and x.strip()
                })
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
            elif k == "custom_tools" and isinstance(v, list):
                # Admin-added tools list. Validate + normalize each
                # entry; drop anything that would collide with a
                # MODULE_CATALOG builtin (builtins always win).
                builtin_keys = {tk for tk, *_ in MODULE_CATALOG}
                seen = set()
                clean = []
                for entry in v:
                    if not isinstance(entry, dict):
                        continue
                    tk = _slugify_tool_key(entry.get("tool_key"))
                    if not tk or tk in builtin_keys or tk in seen:
                        continue
                    seen.add(tk)
                    clean.append({
                        "tool_key": tk,
                        "display_name": str(
                            entry.get("display_name") or tk.replace(
                                "_", " ").title())[:120],
                        "section": str(
                            entry.get("section") or "custom")[:32],
                        "credits": int(entry.get("credits") or 0),
                        "default_usd": float(
                            entry.get("default_usd") or 0.0),
                        "access_flag": (str(entry.get("access_flag"))
                                        if entry.get("access_flag")
                                        else None),
                    })
                merged["custom_tools"] = clean
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


# ---------------------------------------------------------------------------
# Custom-tools management (super_admin only; called from billing_routes.py)
# ---------------------------------------------------------------------------

def _slugify_tool_key(raw) -> str:
    """Coerce a free-form input into a canonical snake_case tool key.

    'Custom Sponsorship Deck'   -> 'custom_sponsorship_deck'
    'Weekly-Report v2'          -> 'weekly_report_v2'
    '  chatbot_analysis  '      -> 'chatbot_analysis' (unchanged shape)
    ''                          -> '' (caller rejects)
    """
    import re
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    # Replace anything that isn't alphanumeric with underscore, then
    # collapse repeats and trim edges.
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80]


class CustomToolError(Exception):
    """Raised for invalid add / remove operations. Message is
    partner-safe (no internal jargon) and OK to render to admins."""
    pass


def add_custom_tool(tool_key: str, display_name: str, *,
                    section: str = "custom",
                    credits: int = 0,
                    usd: float = 0.0,
                    monthly_usd: float = 0.0,
                    access_flag: str = None) -> dict:
    """Register a new admin-added tool.

    Returns the fully-normalized entry that got persisted. Raises
    CustomToolError on any validation failure.

    Guardrails:
      - tool_key must slugify to something non-empty (>= 2 chars).
      - tool_key must NOT collide with a MODULE_CATALOG builtin
        (builtins always win; adding a duplicate would be silently
        shadowed and confusing).
      - display_name must be non-empty.
      - section defaults to 'custom' (renders as its own header in
        the admin UI); MODULE_CATALOG section names are OK too.
      - usd (per-pull) must be >= 0.
      - monthly_usd (recurring access fee) must be >= 0.
      - access_flag is optional; when present it should match one
        of the existing has_*_access flags (not enforced strictly
        so admins can wire flags before the code lands).
    """
    tk = _slugify_tool_key(tool_key)
    if not tk or len(tk) < 2:
        raise CustomToolError("tool key must be at least 2 characters")
    display = str(display_name or "").strip()
    if not display:
        raise CustomToolError("display name is required")
    if len(display) > 120:
        display = display[:120]
    section = str(section or "custom").strip().lower() or "custom"
    if len(section) > 32:
        section = section[:32]
    try:
        credits_i = max(0, int(credits or 0))
    except (TypeError, ValueError):
        credits_i = 0
    try:
        usd_f = float(usd or 0.0)
    except (TypeError, ValueError):
        usd_f = 0.0
    if usd_f < 0:
        raise CustomToolError("USD price must be zero or positive")
    try:
        monthly_f = float(monthly_usd or 0.0)
    except (TypeError, ValueError):
        monthly_f = 0.0
    if monthly_f < 0:
        raise CustomToolError(
            "monthly access fee must be zero or positive")
    builtin_keys = {t for t, *_ in MODULE_CATALOG}
    if tk in builtin_keys:
        raise CustomToolError(
            f"'{tk}' is a built-in tool key and cannot be re-added")
    current = load_pricing(force_reload=True)
    existing = list(current.get("custom_tools") or [])
    for entry in existing:
        if isinstance(entry, dict) and \
                _slugify_tool_key(entry.get("tool_key")) == tk:
            raise CustomToolError(
                f"'{tk}' already exists; edit or delete it instead")
    new_entry = {
        "tool_key": tk,
        "display_name": display,
        "section": section,
        "credits": credits_i,
        "default_usd": usd_f,
        "default_monthly_usd": monthly_f,
        "access_flag": (str(access_flag) if access_flag else None),
    }
    existing.append(new_entry)
    # Persist the tool metadata + both prices in one save.
    per_tool = dict(current.get("per_tool_usd") or {})
    per_tool[tk] = usd_f
    per_tool_monthly = dict(current.get("per_tool_monthly_usd") or {})
    per_tool_monthly[tk] = monthly_f
    save_pricing({
        "custom_tools": existing,
        "per_tool_usd": per_tool,
        "per_tool_monthly_usd": per_tool_monthly,
    })
    return new_entry


def remove_custom_tool(tool_key: str) -> dict:
    """Delete a previously-added custom tool and drop its price.

    Returns {'tool_key': <canonical>, 'removed': True}. Raises
    CustomToolError if the tool_key is a MODULE_CATALOG builtin or
    is not currently registered as a custom tool.

    Also removes the per_tool_usd + per_tool_monthly_usd entries so
    the row can't reappear via the orphan-price fold-in path.
    """
    tk = _slugify_tool_key(tool_key)
    if not tk:
        raise CustomToolError("tool key is required")
    builtin_keys = {t for t, *_ in MODULE_CATALOG}
    if tk in builtin_keys:
        raise CustomToolError(
            f"'{tk}' is a built-in tool and cannot be deleted")
    current = load_pricing(force_reload=True)
    existing = list(current.get("custom_tools") or [])
    filtered = [e for e in existing
                if not (isinstance(e, dict)
                        and _slugify_tool_key(e.get("tool_key")) == tk)]
    if len(filtered) == len(existing):
        raise CustomToolError(f"'{tk}' is not a custom tool")
    # Persist the shortened custom_tools list AND explicitly delete
    # both the per-pull + monthly price entries (the additive per-
    # tool merge can't remove keys on its own).
    save_pricing({
        "custom_tools": filtered,
        "_delete_per_tool_keys": [tk],
        "_delete_per_tool_monthly_keys": [tk],
    })
    return {"tool_key": tk, "removed": True}


def hide_builtin_tool(tool_key: str) -> dict:
    """Soft-hide a built-in tool from the admin pricing panel.

    Adds tool_key to pricing.json:hidden_tools. Does NOT touch
    per_tool_usd, per_tool_monthly_usd, or the code-defined
    MODULE_CATALOG - live billing is completely unaffected. Only
    the admin panel's default listing skips this row until
    unhide_builtin_tool is called.

    Raises CustomToolError if:
      - tool_key doesn't match a MODULE_CATALOG built-in (custom
        tools use remove_custom_tool instead - they are hard-
        deleted, not hidden).

    Idempotent: hiding an already-hidden tool is a no-op.
    """
    tk = str(tool_key or "").strip()
    if not tk:
        raise CustomToolError("tool key is required")
    builtin_keys = {t for t, *_ in MODULE_CATALOG}
    if tk not in builtin_keys:
        raise CustomToolError(
            f"'{tk}' is not a built-in tool. "
            f"Custom tools use delete, not hide.")
    current = load_pricing(force_reload=True)
    existing = list(current.get("hidden_tools") or [])
    if tk not in existing:
        existing.append(tk)
    save_pricing({"hidden_tools": existing})
    return {"tool_key": tk, "hidden": True}


def unhide_builtin_tool(tool_key: str) -> dict:
    """Un-hide a previously hidden built-in tool.

    Removes tool_key from pricing.json:hidden_tools. Idempotent:
    un-hiding a tool that isn't hidden is a no-op. Raises
    CustomToolError only if tool_key isn't a MODULE_CATALOG
    built-in (defense-in-depth; a stray custom-tool key in
    hidden_tools would be silently cleaned by save_pricing's
    dedupe pass anyway).
    """
    tk = str(tool_key or "").strip()
    if not tk:
        raise CustomToolError("tool key is required")
    builtin_keys = {t for t, *_ in MODULE_CATALOG}
    if tk not in builtin_keys:
        raise CustomToolError(
            f"'{tk}' is not a built-in tool")
    current = load_pricing(force_reload=True)
    existing = [str(x) for x in (current.get("hidden_tools") or [])
                if str(x) != tk]
    save_pricing({"hidden_tools": existing})
    return {"tool_key": tk, "hidden": False}


def hidden_builtin_tools() -> list:
    """Return the list of currently-hidden built-in tool_keys."""
    p = load_pricing()
    return list(p.get("hidden_tools") or [])


def tool_price_usd(tool_key: str) -> float:
    """USD price for a single pull of the named tool. 0.0 for unset
    tools (free until admin sets a value).

    Reads directly from pricing.json - independent of the
    hidden_tools soft-hide mechanism. A hidden tool still charges
    its configured price when its pull_type fires; hide only
    affects admin panel visibility."""
    p = load_pricing()
    return float(p.get("per_tool_usd", {}).get(str(tool_key), 0.0))


def tool_monthly_usd(tool_key: str) -> float:
    """Monthly recurring access fee for the named tool. 0.0 = no
    recurring charge (tool is free to have access to; only pull-time
    metering applies). 2026-09-09 split."""
    p = load_pricing()
    return float(
        p.get("per_tool_monthly_usd", {}).get(str(tool_key), 0.0))


def compute_user_monthly_charge(username: str,
                                *, user_record: dict = None) -> dict:
    """Return the monthly access charge summary for a user.

    Jenna 2026-09-09 policy:
      - Unlimited users: always $0 (never billed).
      - monthly_service override: if the monthly_service row's
        monthly_usd is > 0, it REPLACES the per-feature sum for
        every paying user (bundle price wins).
      - Otherwise: sum the per-feature monthly fees for the tools
        this user has has_*_access=true on. Custom tools without an
        access_flag count for every paying user (treated as
        universal).

    Returns:
      {
        'total_usd': float,        # what the cron should charge
        'lines': [                 # per-tool breakdown for receipt
          {'tool_key', 'display_name', 'access_flag', 'monthly_usd'},
          ...
        ],
        'unlimited': bool,
        'monthly_service_override_usd': float,
                                  # non-zero when override is active;
                                  # lines[] is a single bundle line
                                  # in that case.
        'bundle_active': bool,     # true when total_usd came from
                                  # the override, false when it came
                                  # from the per-feature sum.
      }

    Never raises. Never deducts or charges; the caller (the monthly
    cron) applies the charge.
    """
    if user_record is None:
        try:
            from app import load_users  # type: ignore
            users = (load_users() or {}).get("users") or {}
            if isinstance(users, dict):
                user_record = users.get(username) or {}
            else:
                user_record = {}
        except Exception:
            user_record = {}
    if not isinstance(user_record, dict):
        user_record = {}
    if user_record.get("unlimited"):
        return {
            "total_usd": 0.0,
            "lines": [],
            "unlimited": True,
            "monthly_service_override_usd": 0.0,
            "bundle_active": False,
        }
    lines = []
    per_feature_total = 0.0
    override = 0.0
    bundle_display = "Monthly Service (base access)"
    for row in module_catalog():
        monthly = float(row.get("monthly_usd") or 0.0)
        if monthly <= 0.0:
            continue
        if row.get("tool_key") == "monthly_service":
            override = monthly
            bundle_display = row.get(
                "display_name") or bundle_display
            continue
        flag = row.get("access_flag")
        if flag and not user_record.get(flag):
            continue
        lines.append({
            "tool_key": row.get("tool_key"),
            "display_name": row.get("display_name"),
            "access_flag": flag,
            "monthly_usd": monthly,
        })
        per_feature_total += monthly
    # Apply the bundle-override rule (Jenna 2026-09-09):
    # 'monthly_service always wins for that user if > 0'.
    if override > 0.0:
        return {
            "total_usd": round(override, 2),
            "lines": [{
                "tool_key": "monthly_service",
                "display_name": bundle_display,
                "access_flag": None,
                "monthly_usd": round(override, 2),
            }],
            "unlimited": False,
            "monthly_service_override_usd": round(override, 2),
            "bundle_active": True,
        }
    return {
        "total_usd": round(per_feature_total, 2),
        "lines": lines,
        "unlimited": False,
        "monthly_service_override_usd": 0.0,
        "bundle_active": False,
    }


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
    # 2026-09-09 (Jenna): retired 'chatbot analysis' and 'chatbot
    # deck' pull_types here. The consume_credit call sites for those
    # flows were removed earlier the same day (analyze / deck build
    # routes no longer charge per action - metered via the monthly
    # chatbot access product).
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


def apply_wallet_deduct(subject: dict, amount_usd: float, *,
                        description: str = "",
                        tool_key: str = "",
                        job_id: str = "",
                        stripe_ref: str = "",
                        billed_via_username: str = "") -> dict:
    """Debit the wallet in place. Returns the transaction row.

    `subject` is the wallet-holding record - typically a user, but
    a company when the calling user's billing_source == 'company'
    (see resolve_billing_subject). All wallet fields are the same
    shape on either record; this function doesn't care which.

    `billed_via_username` (optional): who triggered the pull, when
    the subject is a company. Stamped on the transaction so the
    company admin can see per-user attribution. Ignored (empty
    string) when subject is a user.

    Called from consume_credit's _consume mutator AFTER internal
    credits (if any) have been decided. Amount is positive dollars;
    the wallet moves by -amount. May take balance negative for
    monthly_invoice mode (caller enforces the limit).

    Callers with atomicity requirements MUST invoke this inside the
    same _users_cas_mutate closure that reads the subject record,
    so a concurrent top-up gets folded in on retry.
    """
    amt = round(float(amount_usd), 2)
    if amt <= 0:
        return {}
    old = wallet_balance(subject)
    new = round(old - amt, 2)
    subject["wallet_balance_usd"] = new
    subject["wallet_lifetime_spend_usd"] = round(
        float(subject.get("wallet_lifetime_spend_usd", 0.0) or 0.0)
        + amt, 2)
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
    if billed_via_username:
        txn["billed_via_username"] = billed_via_username
    _append_txn(subject, txn)
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


def try_auto_reload(subject_key: str, subject_snapshot: dict, *,
                    subject_kind: str = "user") -> dict:
    """Post-CAS-write hook: fire the Stripe auto-reload charge when
    needed and credit the wallet.

    `subject_kind` is 'user' (default, individual wallet) or
    'company' (shared wallet - top-up lands on the company record).
    `subject_key` is the username OR the company name accordingly.

    Called AFTER the deducting CAS mutation commits. Must not run
    inside a CAS mutator because it makes a Stripe network call.

    Idempotency: keyed by (subject_kind, subject_key, minute_bucket,
    amount_cents). Two rapid deductions at the same company within
    the same minute fold into a single Stripe charge.

    Never raises. All error paths return {"fired": False,
    "error": "..."}.
    """
    result = {"fired": False, "amount_usd": 0.0,
              "payment_intent_id": "", "error": ""}
    try:
        should, amount = needs_auto_reload(subject_snapshot)
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
        cus_id = str(subject_snapshot.get("stripe_customer_id") or "")
        pm_id = str(subject_snapshot.get("stripe_payment_method_id") or "")
        if not (cus_id and pm_id):
            result["error"] = "missing_card_on_file"
            return result
        # Minute-bucket idempotency, keyed by subject so two users at
        # the same company can't accidentally double-charge in the
        # same minute.
        from datetime import datetime, timezone
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        idem = (f"auto-reload-{subject_kind}-{subject_key}-"
                f"{int(amount * 100)}-{bucket}")
        try:
            charge = _billing.charge_saved_card(
                customer_id=cus_id,
                payment_method_id=pm_id,
                amount_usd=amount,
                description="Auto-reload (wallet threshold)",
                username=subject_key,
                metadata={
                    "purpose": "auto_reload",
                    "subject_kind": subject_kind,
                    "subject_key": subject_key,
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
            if subject_kind == "company":
                subj = (data.get("companies") or {}).get(subject_key)
            else:
                subj = (data.get("users") or {}).get(subject_key)
            if not subj:
                return None
            # Idempotency: if we already logged this pi as a topup,
            # skip (webhook may have arrived first).
            for t in list(subj.get("wallet_transactions") or [])[:20]:
                if (str(t.get("stripe_ref") or "") == pi_id
                        and str(t.get("kind") or "")
                        in ("topup", "auto_reload")):
                    return None
            apply_wallet_topup(
                subj, amount,
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

# ---------------------------------------------------------------------------
# Company-shared wallet routing (Jenna 2026-09-09)
# ---------------------------------------------------------------------------
#
# Jenna: "how is this working if I need to assign one master account for a
# company that bills but splits amongst the users for that company?"
#
# Model: a user record MAY carry `billing_source='company'`. When set and
# the user's `company` field names a company that exists in
# users_data['companies'], every wallet operation for that user routes to
# the COMPANY record instead of the user record. The company holds the
# card on file, the balance, the auto-reload settings, and the monthly
# access billing marker. Multiple users at the same company share one
# pool - top up once, everyone at the company draws from it.
#
# Backward compat: users with no `billing_source` field (i.e. every user
# who existed before this feature shipped) resolve to themselves. No
# migration needed. Individual wallets keep working.
#
# Access flags: the USER still owns has_*_access flags (they gate whether
# the user can even trigger a pull). The COMPANY only holds the wallet
# state. For monthly access billing on a company, we UNION the access
# flags of every member routing through that company.
#
# Safety: no wallet function ever routes silently to nowhere. If the
# `company` field points to a name that no longer exists in
# users_data['companies'], the resolver falls back to the user record.
# The pull will then just fail the "not a paying customer" check if the
# individual isn't billed, which is the correct fallback.


def resolve_billing_subject(user: dict, users_data: dict) -> tuple:
    """Resolve which record holds the wallet + card for a given user.

    Returns (subject_dict, subject_kind, subject_key):
      subject_dict: the live dict to read/mutate (user OR company)
      subject_kind: 'user' or 'company'
      subject_key:  the key in users_data['users'] or
                    users_data['companies']

    A user whose billing_source == 'company' AND whose 'company' field
    names a company that exists in users_data['companies'] resolves to
    that company. Every other case resolves to the user itself.

    Never raises. On any lookup failure, returns the user record so
    the pull still charges someone (the individual) instead of
    silently free.
    """
    try:
        if not isinstance(user, dict) or not isinstance(users_data, dict):
            return user, "user", ""
        source = str(user.get("billing_source") or "user").strip().lower()
        if source != "company":
            return user, "user", _user_key(user)
        company_name = str(user.get("company") or "").strip()
        if not company_name:
            return user, "user", _user_key(user)
        companies = users_data.get("companies") or {}
        company = companies.get(company_name)
        if not isinstance(company, dict):
            return user, "user", _user_key(user)
        return company, "company", company_name
    except Exception:
        return user, "user", _user_key(user) if isinstance(user, dict) else ""


def _user_key(user: dict) -> str:
    """Best-effort primary key for a user record (email > username)."""
    if not isinstance(user, dict):
        return ""
    return str(user.get("email") or user.get("username") or "")


def company_billing_admins(company_name: str, users_data: dict) -> list:
    """Return the usernames of users flagged company_billing_admin=True
    for this company. Empty list if none set.

    A company's billing admins are the only users allowed (in the UI)
    to manage the company's card on file, top up the balance, and
    change auto-reload settings. Any user at the company can still
    RUN pulls that debit the company wallet; only admins can move
    money in or change payment method."""
    admins = []
    for username, u in (users_data.get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        if str(u.get("company") or "").strip() != company_name:
            continue
        if bool(u.get("company_billing_admin")):
            admins.append(username)
    return admins


def company_members(company_name: str, users_data: dict) -> list:
    """Every user routing through this company (billing_source=company
    AND company == company_name). Returns list of (username, user_dict)
    tuples. Includes members even if they aren't billing admins."""
    out = []
    for username, u in (users_data.get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        if str(u.get("company") or "").strip() != company_name:
            continue
        if str(u.get("billing_source") or "").strip().lower() != "company":
            continue
        out.append((username, u))
    return out


def compute_company_monthly_charge(company_name: str,
                                   users_data: dict) -> dict:
    """Total monthly access fee for a company, based on the UNION of
    its members' has_*_access flags.

    Jenna 2026-09-09: 'Union of every member's access flags (if ANY
    member has Profile IQ access, the company pays the Profile IQ
    monthly)'.

    Returns the same shape as compute_user_monthly_charge:
      {
        'company': str,
        'total_usd': float,
        'lines': [{tool_key, display_name, monthly_usd}, ...],
        'unlimited': bool,       # True if the COMPANY has 'unlimited'
        'bundle_active': bool,   # monthly_service > 0
        'monthly_service_override_usd': float,
        'member_count': int,
      }

    Never raises. Zero members OR zero pricing -> total 0.
    """
    company = (users_data.get("companies") or {}).get(company_name) or {}
    members = company_members(company_name, users_data)
    result = {
        "company": company_name,
        "total_usd": 0.0,
        "lines": [],
        "unlimited": bool(company.get("unlimited")),
        "bundle_active": False,
        "monthly_service_override_usd": 0.0,
        "member_count": len(members),
    }
    if result["unlimited"] or not members:
        return result
    # Union of every member's has_*_access flags. A flag is "on" for
    # the company if ANY member has it True.
    union_flags = set()
    for _uname, u in members:
        for k, v in u.items():
            if not isinstance(k, str):
                continue
            if not k.startswith("has_") or not k.endswith("_access"):
                continue
            if v:
                union_flags.add(k)
    # Bundle override: monthly_service_usd wins if > 0.
    ms_key = "monthly_service"
    ms_usd = tool_monthly_usd(ms_key)
    if ms_usd > 0:
        result["monthly_service_override_usd"] = ms_usd
        result["bundle_active"] = True
        result["total_usd"] = round(ms_usd, 2)
        # Look up the bundle's display_name so the receipt reads right.
        display = "Monthly service"
        for tk, disp, *_ in MODULE_CATALOG:
            if tk == ms_key:
                display = disp
                break
        result["lines"] = [{
            "tool_key": ms_key,
            "display_name": display,
            "monthly_usd": ms_usd,
        }]
        return result
    # Per-feature sum across the tools whose access flag is in the union.
    total = 0.0
    lines = []
    for tk, disp, _sec, _cr, _def_usd, flag in MODULE_CATALOG:
        if not flag or flag not in union_flags:
            continue
        m = tool_monthly_usd(tk)
        if m <= 0:
            continue
        total += m
        lines.append({
            "tool_key": tk,
            "display_name": disp,
            "monthly_usd": round(m, 2),
        })
    # Custom tools too (they may carry access_flag + monthly_usd).
    p = load_pricing()
    for c in (p.get("custom_tools") or []):
        if not isinstance(c, dict):
            continue
        flag = str(c.get("access_flag") or "").strip()
        if not flag or flag not in union_flags:
            continue
        tk = str(c.get("tool_key") or "")
        if not tk:
            continue
        m = tool_monthly_usd(tk)
        if m <= 0:
            continue
        total += m
        lines.append({
            "tool_key": tk,
            "display_name": str(c.get("display_name") or tk),
            "monthly_usd": round(m, 2),
        })
    result["total_usd"] = round(total, 2)
    result["lines"] = lines
    return result


class SpendNotAuthorizedError(Exception):
    """Raised when a member tries to spend a company's shared wallet on
    a tool they aren't scoped for. Callers should catch this and surface
    a partner-safe message (no internal terminology).

    Attributes:
      tool_key:     the pricing key that was blocked (e.g. profile_iq_build)
      display_name: friendly tool label (from MODULE_CATALOG when known)
      company_name: the company that holds the shared wallet
      allowed:      the list of tool_keys this member CAN spend on
                    ('*' when unrestricted; empty list means none)
    """

    def __init__(self, tool_key: str, display_name: str,
                 company_name: str, allowed):
        self.tool_key = tool_key
        self.display_name = display_name or tool_key
        self.company_name = company_name
        self.allowed = allowed
        super().__init__(
            f"Not authorized to spend the {company_name} shared "
            f"wallet on {self.display_name}."
        )


def _normalize_spend_scope(value) -> object:
    """Normalize a raw spend-scope config value to one of:

      * "*"          -> no restriction (default when unset / garbage)
      * "inherit"    -> defer to company default (user-side only)
      * frozenset()  -> explicit list of tool_keys (empty = deny all)

    Fail-open bias: any unparseable value is treated as "*" so a
    corrupted config never silently blocks paying customers. An empty
    list `[]`, however, is treated as an explicit "deny everything" -
    that's a legitimate restrictive config.
    """
    if value is None:
        return "*"
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("", "*", "all", "any", "unrestricted"):
            return "*"
        if s == "inherit":
            return "inherit"
        # Single tool_key as a string is legitimate. Wrap it.
        return frozenset({value.strip()})
    if isinstance(value, (list, tuple, set, frozenset)):
        # Explicit list. Empty list = deny all (legitimate).
        cleaned = frozenset(
            str(x).strip() for x in value
            if isinstance(x, str) and str(x).strip()
        )
        return cleaned
    # Anything else (int, dict, ...) -> fail-open.
    return "*"


def _tool_display_name(tool_key: str) -> str:
    """Best-effort friendly label for a tool_key (MODULE_CATALOG first,
    custom_tools next, tool_key itself as last resort). Never raises."""
    try:
        for tk, disp, *_ in MODULE_CATALOG:
            if tk == tool_key:
                return disp
        p = load_pricing() or {}
        for c in p.get("custom_tools") or []:
            if isinstance(c, dict) and c.get("tool_key") == tool_key:
                return str(c.get("display_name") or tool_key)
    except Exception:
        pass
    return tool_key


def user_can_spend_from_company(user: dict, tool_key: str,
                                company: dict) -> tuple:
    """Return (allowed: bool, reason: str, allowed_scope).

    Checks whether `user` is authorized to spend `company`'s shared
    wallet on a pull with the given `tool_key`. Only meaningful when
    the user's billing subject resolves to `company` (caller must
    have already established that via resolve_billing_subject).

    Semantics:

      1. `company_billing_admin=True` -> implicit "*" (billing admins
         can always spend). This is a convenience so a company owner
         who set up the wallet doesn't lock themselves out.

      2. User `company_spend_scope`:
           - missing / None / "inherit" -> fall through to company
           - "*" or "all" or "any"     -> allow every tool
           - non-empty frozenset       -> allow iff tool_key in set
           - empty frozenset ([])      -> deny all (explicit no-spend)
           - unparseable               -> fail-open to "*"

      3. Company `default_spend_scope`:
           - missing / None / "*"      -> allow every tool (backward
                                          compat; existing companies
                                          keep working exactly as
                                          before)
           - non-empty frozenset       -> allow iff tool_key in set
           - empty frozenset ([])      -> deny all
           - unparseable               -> fail-open to "*"

    Fail-safe: never raises. On any unexpected input structure, returns
    (True, "fail_open", "*") so a corrupt config can't silently deny a
    paying customer. Explicit list-based restrictions (including []) are
    respected exactly.

    Third return value `allowed_scope` is the effective scope that made
    the call: "*" (unrestricted), "billing_admin" (bypass), or the
    frozenset of tool_keys.
    """
    tk = str(tool_key or "").strip()
    try:
        if not isinstance(user, dict) or not isinstance(company, dict):
            return True, "fail_open", "*"
        # 1. Billing-admin bypass.
        if bool(user.get("company_billing_admin")):
            return True, "billing_admin", "billing_admin"
        # 2. User-side scope. IMPORTANT distinction from company side:
        #    a MISSING user field means "inherit" (defer to the
        #    company default). Only an explicit "*" / "all" / "any"
        #    is a user-level unrestricted grant. A missing COMPANY
        #    field, by contrast, means "*" (permissive default,
        #    backward compat for pre-scope-feature companies).
        raw_user = user.get("company_spend_scope")
        if raw_user is None or raw_user == "":
            u_scope = "inherit"
        else:
            u_scope = _normalize_spend_scope(raw_user)
        if u_scope != "inherit":
            if u_scope == "*":
                return True, "user_star", "*"
            # frozenset
            if tk in u_scope:
                return True, "user_list", u_scope
            return False, "user_list", u_scope
        # 3. Fall through to company default. Missing here means "*".
        c_scope = _normalize_spend_scope(company.get("default_spend_scope"))
        if c_scope in ("*", "inherit"):
            # "inherit" doesn't make sense on a company; treat as "*".
            return True, "company_star", "*"
        # frozenset
        if tk in c_scope:
            return True, "company_list", c_scope
        return False, "company_list", c_scope
    except Exception as _e:
        # Fail-open: never deny a paying customer over a config bug.
        return True, "fail_open", "*"


def user_spend_scope_summary(user: dict, users_data: dict,
                             company: dict = None) -> dict:
    """Return a UI-safe summary of what this user can spend the shared
    wallet on. Only meaningful when the user routes through a company.
    Solo users get {"routed_to_company": False}.

    Shape:
      {
        "routed_to_company": True,
        "company_name": "Acme Corp",
        "billing_admin": bool,
        "scope_kind": "star" | "list",
        "scope_tool_keys": ["prometheus", "profile_iq_build", ...],
        "scope_display_names": ["Prometheus (Ask-metered)", ...],
        "source": "billing_admin" | "user" | "company_default"
      }

    Never raises.
    """
    try:
        if not isinstance(user, dict):
            return {"routed_to_company": False}
        if company is None:
            subj, kind, key = resolve_billing_subject(
                user, users_data or {})
            if kind != "company":
                return {"routed_to_company": False}
            company = subj
            company_name = key
        else:
            company_name = ""
            companies = (users_data or {}).get("companies") or {}
            for cn, c in companies.items():
                if c is company:
                    company_name = cn
                    break
        if bool(user.get("company_billing_admin")):
            return {
                "routed_to_company": True,
                "company_name": company_name,
                "billing_admin": True,
                "scope_kind": "star",
                "scope_tool_keys": [],
                "scope_display_names": [],
                "source": "billing_admin",
            }
        raw_user = user.get("company_spend_scope")
        if raw_user is None or raw_user == "":
            u_scope = "inherit"
        else:
            u_scope = _normalize_spend_scope(raw_user)
        if u_scope == "inherit":
            c_scope = _normalize_spend_scope(
                company.get("default_spend_scope"))
            eff = c_scope
            source = "company_default"
        else:
            eff = u_scope
            source = "user"
        if eff in ("*", "inherit"):
            return {
                "routed_to_company": True,
                "company_name": company_name,
                "billing_admin": False,
                "scope_kind": "star",
                "scope_tool_keys": [],
                "scope_display_names": [],
                "source": source,
            }
        keys = sorted(eff)
        return {
            "routed_to_company": True,
            "company_name": company_name,
            "billing_admin": False,
            "scope_kind": "list",
            "scope_tool_keys": keys,
            "scope_display_names": [_tool_display_name(k) for k in keys],
            "source": source,
        }
    except Exception:
        return {"routed_to_company": False}


def iter_paying_subjects(users_data: dict) -> list:
    """Yield every subject the monthly billing engine should consider.

    Order: users first (skipping any user who routes through a
    company - the company charge covers them), then companies. A
    user whose billing_source == 'company' is EXCLUDED from the
    user pass (no double-billing). A company with zero paying-
    members is still eligible if company.paying_customer is set,
    but its computed charge will be 0 (nobody has access flags).

    Returns list of (subject_kind, subject_key, subject_dict).
    """
    out = []
    for username, u in (users_data.get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        # Skip users routing through a company - the company gets
        # billed instead. Prevents double-billing.
        source = str(u.get("billing_source") or "").strip().lower()
        if source == "company":
            company_name = str(u.get("company") or "").strip()
            if company_name and company_name in (
                    users_data.get("companies") or {}):
                continue
        out.append(("user", username, u))
    for company_name, c in (users_data.get("companies") or {}).items():
        if not isinstance(c, dict):
            continue
        out.append(("company", company_name, c))
    return out


__all__ = [
    "PRICING_S3_KEY",
    "DEFAULT_PRICING",
    "MODULE_CATALOG", "module_catalog",
    "METERED_TOOL_KEYS", "is_metered_tool", "is_metered_locked",
    "metered_tool_keys", "mark_metered", "unmark_metered",
    "load_pricing", "save_pricing",
    "tool_price_usd", "tool_monthly_usd", "prometheus_markup",
    "compute_user_monthly_charge", "compute_company_monthly_charge",
    "top_up_pack_sizes", "top_up_min_custom",
    "wallet_balance", "wallet_stats",
    "is_paying_customer", "is_unlimited", "admits_wallet_ui",
    "billing_mode", "auto_reload_threshold", "auto_reload_amount",
    "monthly_invoice_limit", "has_card_on_file",
    "apply_wallet_deduct", "apply_wallet_topup", "apply_wallet_refund",
    "should_charge_wallet", "wallet_can_absorb", "needs_auto_reload",
    "try_auto_reload",
    "add_custom_tool", "remove_custom_tool", "CustomToolError",
    "hide_builtin_tool", "unhide_builtin_tool", "hidden_builtin_tools",
    "resolve_billing_subject", "company_billing_admins",
    "company_members", "iter_paying_subjects",
    "user_can_spend_from_company", "user_spend_scope_summary",
    "SpendNotAuthorizedError",
]
