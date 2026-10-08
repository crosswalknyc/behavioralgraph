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

2026-09-14 (Jenna): nothing is ever free. Set-price products charge
their set price (un-priced report asks charge the Un-priced Ask rate);
every other answered ask is metered - including answers served from
the library with no fresh model call, which bill the flat
metered_answer_usd rate through the same session sweep.

This module is PURE math + state. It does not touch Stripe. Callers
(app.py routes, pay_per_use.py session close) wire it into their own
flows. The one exception: try_auto_reload, after a live card charge
credits the wallet, fires the same top-up receipt + internal notice
as the webhook. The webhook often arrives after the dollars already
landed and used to skip the email as a duplicate (Kartel 2026-10-02).

Public surface:

    load_pricing() -> dict                # per-tool USD costs
    save_pricing(pricing) -> dict
    tool_price_usd(tool_key) -> float
    prometheus_markup() -> float          # 2.10 currently
    metered_answer_usd() -> float         # billed per served answer

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
import re
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
        # Standard IQ pulls (dashboard). API keys below stay on the
        # partner sticker. Kartel overrides via profile_pull_usd /
        # tool_price_overrides on the company record.
        "profile_iq_build": 300.0,
        "profile_iq_derived_cut": 100.0,
        "subscriber_iq_build": 500.0,
        # Prometheus research report (2026-09-14 Jenna): the put-
        # together read on a subject with no base anywhere, and the
        # catch-all price for report asks with no other set price.
        "panel_report": 550.0,
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
        "api_subscriber_iq_build": 500.0,
        "api_chatbot_profile_iq_build": 300.0,
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
        # 2026-09-25 (Jenna): Brand Partnership is $1,000 in
        # Prometheus / dashboard. Kartel keeps $925 via company
        # tool_price_overrides. Digital Journey is $500 (2026-09-18).
        "brand_partnership_iq": 1000.0,
        "journey_iq": 500.0,
        # 2026-09-29: Flywheel report is $500 per pull.
        "flywheel_iq": 500.0,
        # 2026-09-22 (Jenna): Attribution IQ tracking - $500 first
        # setup pull, $100 per prepaid daily-refresh day (charged up
        # front through the user's chosen end date; no refunds).
        "attribution_iq_setup": 500.0,
        "attribution_iq_daily": 100.0,
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
    # 2026-09-29: Trends IQ, Rankers IQ (bundle) and Fin IQ start at
    # $5,000/mo, matching the public rate card.
    "per_tool_monthly_usd": {
        "trends_iq": 5000.0,
        "rankers_iq_access": 5000.0,
        "hedge_fund_iq": 5000.0,
    },
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
    # Jenna 2026-09-23: the only preset add-funds amounts are $5k /
    # $10k / $15k. Custom is allowed at $5k or any amount above.
    "top_up_packs_usd": [5000.0, 10000.0, 15000.0],
    "top_up_min_custom_usd": 5000.0,
    "prometheus_markup_multiplier": 2.10,
    # Published Prometheus meter (Jenna 2026-10-07, verbatim: "All
    # Prometheus (chat bot) usage is billed at a metered rate of
    # $10.50 / $52.50 per million in/out, plus $0.021 per search.").
    # Session billing prices tokens at THESE rates, whatever model
    # answered; cache reads and cache writes count as input tokens.
    "prometheus_meter_usd": {"input_per_m": 10.50, "output_per_m": 52.50,
                             "search": 0.021},
    # 2026-09-14 (Jenna, verbatim: "nothing should EVER be free. if it
    # doesnt have a set price it but is answerable from what's already
    # there that should all be the metered usage."). Billed USD per
    # answer served from the library with no fresh model call (insights
    # ledger replays, cache-served reads). Those answers record zero
    # token usage, so under pure consumption metering they billed $0;
    # this rate is what the session sweep bills instead. Default 2.10
    # (a $1.00 generation at the default markup). Editable by super
    # admins in /admin/billing next to the markup.
    "metered_answer_usd": 2.10,
    "auto_reload_defaults": {
        "threshold_usd": 500.0,
        "amount_usd": 5000.0,
    },
    "monthly_invoice_defaults": {
        "limit_usd": 5000.0,
    },
}

# Locked add-funds amounts (Jenna 2026-09-23). Presets are only
# $5,000 / $10,000 / $15,000. Custom must be $5,000 or more.
TOP_UP_PACKS_USD = (5000.0, 10000.0, 15000.0)
TOP_UP_MIN_USD = 5000.0
# Opening exception (Jenna 2026-09-25): a locked new seat can take a
# one-time $500 card + top-up. Regular Add Funds stays at $5,000.
# Admin amount-locked payment links may also mint at this floor.
OPENING_TOPUP_MIN_USD = 500.0
# Excel Sports Management (Jenna 2026-09-29): this company only.
# Auto-reload and Add Funds floor is $500. Any amount over $500 is
# allowed. Everyone else stays on the $5,000 floor.
EXCEL_SPORTS_COMPANY = "Excel Sports Management"
EXCEL_TOP_UP_MIN_USD = 500.0


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


# 2026-09-09 (Jenna, verbatim: 'no chatbot profile iq because that
# wouldnt work through an api then you wouldnt need chatbot profile iq
# build or api profile iq build under other right?'). Tool_keys that
# have been retired from MODULE_CATALOG but may still linger in
# pricing.json:per_tool_usd from earlier admin edits. Without this
# filter, module_catalog()'s orphan-fold defence-in-depth resurrects
# them as 'Other' rows and admins re-encounter dead tools. Behaviour:
#
#   1. The orphan-fold loop SKIPS these keys, so retired tools never
#      render in the admin pricing panel again.
#   2. `should_charge_wallet()` short-circuits to 0 for these keys, so
#      even if a stale pull_type route or a lingering per_tool_usd
#      entry existed, no charge would fire.
#
# When a tool is retired, add its tool_key here and drop the row from
# MODULE_CATALOG in the same change. Adding without removing the
# MODULE_CATALOG row is a no-op (the built-in row still wins).
RETIRED_TOOL_KEYS = frozenset({
    # Rolled into api_chatbot_profile_iq_build (partner API canonical
    # per the 2026-09-09 consolidation). The legacy row was removed
    # from MODULE_CATALOG at line ~412; any stale $275 entry in
    # per_tool_usd["api_profile_iq_build"] is now invisible + inert.
    "api_profile_iq_build",
    # Dashboard-side chatbot is a monthly-access product gated by
    # has_chatbot_profile_iq_access. No per-pull fires from the
    # dashboard chatbot path anymore. The pull_type dispatcher
    # keeps the mapping for compat, but tool_price_usd returns 0.
    "chatbot_profile_iq_build",
    # Analyze Ask + Deck Export were session-metered under Prometheus
    # from 2026-09-09; they were retired from MODULE_CATALOG at the
    # same time. Lingering per_tool_usd entries were pinned at 0 but
    # still folded as 'Other' orphans; filter them permanently.
    "chatbot_analysis",
    "chatbot_deck",
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
    # 2026-09-09 (Jenna): dashboard-side chatbot is a monthly-access
    # product. The per-user has_chatbot_profile_iq_access flag turns
    # the feature on; the monthly $ lives on this row. Distinct from
    # the partner API surface (api_chatbot_profile_iq_build), which is
    # per-pull priced.
    ("chatbot_profile_iq_access",  "Chatbot Profile IQ Access",
     "modules", 0, 0.0, "has_chatbot_profile_iq_access"),
    # ---------- Pulls (per-pull dashboard-side events) ----------
    # Per-pull priced events fired from the dashboard. Admin UI renders
    # PULLS rows with only the "/ pull" cell editable; monthly cell
    # hidden. Session-metered rows (Prometheus) render a metered note
    # in place of the per-pull input.
    ("profile_iq_build",           "Profile IQ - Full Build",
     "pulls", 5, 300.0, "has_profile_iq_access"),
    ("profile_iq_derived_cut",     "Profile IQ - Derived Cut",
     "pulls", 3, 100.0, "has_profile_iq_access"),
    ("subscriber_iq_build",        "Subscriber IQ - Pull",
     "pulls", 10, 500.0, "has_subscriber_iq_access"),
    # 2026-09-14 (Jenna, verbatim: "before it puts together any report
    # outside of a simple analysis of what already exists it should
    # charge them. if they request something that doesnt have a set
    # price it should charge $550."). A Prometheus research report -
    # the full put-together read on a subject with no base profile
    # anywhere - is a priced pull, not a metered question. This is
    # ALSO the catch-all price for report requests that map to no
    # other set-price product. Credits column = 6 (nearest whole
    # credit above $550 at the $100/credit build rate).
    # 2026-09-14 (Jenna, same day, verbatim: 'add something to the
    # billing tab that says "un-priced ask" and set that to the $550
    # and then the super admins can change it if needed'). The billing
    # panel row is labeled "Un-priced Ask"; the USD field is editable
    # like every other row and the edited value flows through
    # tool_price_usd() into both the Prometheus quote and the charge.
    ("panel_report",               "Un-priced Ask",
     "pulls", 6, 550.0, "has_prometheus_access"),
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
    ("impact_iq",                  "Ticket Sales IQ",
     "modules", 10, 0.0, "has_impact_iq_access"),
    ("ticket_sales",               "Ticket Sales - Runs",
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
     "modules", 15, 1000.0, "has_brand_partnership_iq_access"),
    ("journey_iq",                 "Digital Journey IQ",
     "modules", 10, 500.0, "has_journey_iq_access"),
    ("flywheel_iq",                "Flywheel IQ",
     "modules", 5, 500.0, "has_flywheel_iq_access"),
    ("attribution_iq_setup",       "Attribution IQ - Tracking Setup",
     "modules", 5, 500.0, "has_intent_iq_access"),
    ("attribution_iq_daily",       "Attribution IQ - Daily Refresh",
     "modules", 1, 100.0, "has_intent_iq_access"),
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
    # 2026-09-09 (Jenna, verbatim: 'wouldnt partner API just be API-
    # Profile IQ Build, Profile IQ Cut, Sub iq build. no chatbot profile
    # iq because that wouldnt work through an api'). Display label
    # dropped 'Chatbot' - partners never see a chatbot, they just POST
    # a prompt to the API and get a Profile IQ build back. Tool_key
    # kept as `api_chatbot_profile_iq_build` so pricing routing in
    # bg-webapp/app.py::_v1_price_usd_for and the pull_type dispatcher
    # keep working; the label is UI-only.
    ("api_chatbot_profile_iq_build", "API - Profile IQ Build",
     "api", 5, 300.0, None),
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
    #
    # 2026-09-09 (Jenna): retired tool_keys are SKIPPED here even if
    # they still carry a stale per_tool_usd entry from a legacy admin
    # edit. Prevents 'Api Profile Iq Build', 'Chatbot Profile Iq Build',
    # 'Chatbot Analysis', and 'Chatbot Deck' from resurrecting as
    # 'Other' rows after their MODULE_CATALOG entries were retired.
    orphan_keys = set()
    for k in list(per_tool.keys()) + list(per_tool_monthly.keys()):
        if k in builtin_keys or k in custom_keys or k in orphan_keys:
            continue
        if k in RETIRED_TOOL_KEYS:
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
            elif k in ("top_up_packs_usd", "top_up_min_custom_usd"):
                # Jenna 2026-09-23: presets and the $5k floor are
                # locked. Admin save cannot bring back $250 packs or
                # drop the minimum below $5,000.
                merged["top_up_packs_usd"] = list(TOP_UP_PACKS_USD)
                merged["top_up_min_custom_usd"] = float(TOP_UP_MIN_USD)
            elif k in ("prometheus_markup_multiplier",
                       "metered_answer_usd") \
                    and isinstance(v, (int, float)):
                merged[k] = float(v)
            elif k == "prometheus_markup" \
                    and isinstance(v, (int, float)):
                # Alias so the admin UI can POST either spelling.
                merged["prometheus_markup_multiplier"] = float(v)
            elif k == "prometheus_meter_usd" and isinstance(v, dict):
                cur = dict(merged.get("prometheus_meter_usd")
                           or DEFAULT_PRICING["prometheus_meter_usd"])
                for kk in ("input_per_m", "output_per_m", "search"):
                    try:
                        if kk in v and float(v[kk]) > 0:
                            cur[kk] = float(v[kk])
                    except (TypeError, ValueError):
                        pass
                merged["prometheus_meter_usd"] = cur
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
    merged["top_up_packs_usd"] = list(TOP_UP_PACKS_USD)
    merged["top_up_min_custom_usd"] = float(TOP_UP_MIN_USD)
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


# Full Profile IQ builds (not derived cuts). A company can set
# `profile_pull_usd` to override the global sticker on these keys
# only. Kartel is $275; everyone else stays on the $300 default.
PROFILE_BUILD_TOOL_KEYS = frozenset({
    "api_chatbot_profile_iq_build",
    "profile_iq_build",
    "chatbot_profile_iq_build",
})

# Leftover internal credits -> wallet dollars. One full Profile IQ
# pull used to cost 5 credits and now costs $300, so each leftover
# credit is $60. Used when a prepaid company (WME) moves onto the
# dollar wallet without changing how many profile pulls they have left.
LEGACY_CREDITS_PER_PROFILE_PULL = 5
GLOBAL_PROFILE_PULL_USD = 300.0
LEGACY_CREDIT_USD = (
    GLOBAL_PROFILE_PULL_USD / LEGACY_CREDITS_PER_PROFILE_PULL)


def leftover_credits_to_usd(credits) -> float:
    """Translate leftover internal credits into wallet dollars.

    5 credits = one Profile IQ pull = $300, so 1 credit = $60.
    Non-numeric or negative leftover becomes $0. Never raises.
    """
    try:
        n = int(credits or 0)
    except (TypeError, ValueError):
        return 0.0
    if n <= 0:
        return 0.0
    return round(n * LEGACY_CREDIT_USD, 2)


def subject_tool_price_usd(subject: dict, tool_key: str,
                           pricing: Optional[dict] = None) -> float:
    """USD price for `tool_key` on this billing subject.

    Order:
      1. Company/user `profile_pull_usd` when the tool is a full
         Profile IQ build and the field is a positive number.
      2. Subject `tool_price_overrides[tool_key]` if positive.
      3. Global pricing.json `per_tool_usd[tool_key]`.
    """
    tk = str(tool_key or "").strip()
    if not tk:
        return 0.0
    if isinstance(subject, dict):
        if tk in PROFILE_BUILD_TOOL_KEYS:
            try:
                special = float(subject.get("profile_pull_usd") or 0)
            except (TypeError, ValueError):
                special = 0.0
            if special > 0:
                return round(special, 2)
        overrides = subject.get("tool_price_overrides")
        if isinstance(overrides, dict) and tk in overrides:
            try:
                ov = float(overrides.get(tk) or 0)
            except (TypeError, ValueError):
                ov = 0.0
            if ov > 0:
                return round(ov, 2)
    pricing = pricing or load_pricing()
    try:
        return round(float(
            (pricing.get("per_tool_usd") or {}).get(tk, 0.0) or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def tool_price_usd(tool_key: str, subject: dict = None) -> float:
    """USD price for a single pull of the named tool. 0.0 for unset
    tools (free until admin sets a value).

    Reads directly from pricing.json - independent of the
    hidden_tools soft-hide mechanism. A hidden tool still charges
    its configured price when its pull_type fires; hide only
    affects admin panel visibility. Pass `subject` (user or company
    record) to honor a company profile-pull rate."""
    if subject is not None:
        return subject_tool_price_usd(subject, tool_key)
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
    "chatbot profile iq":            "api_chatbot_profile_iq_build",
    "chatbot profile iq build":      "api_chatbot_profile_iq_build",
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
    # ---- Prometheus research report / un-priced ask (2026-09-14) ----
    "panel report":                  "panel_report",
    "research report":               "panel_report",
    "prometheus report":             "panel_report",
    "un-priced ask":                 "panel_report",
    "unpriced ask":                  "panel_report",
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
    "flywheel iq":                   "flywheel_iq",
    "build a flywheel":              "flywheel_iq",
    "attribution iq setup":          "attribution_iq_setup",
    "attribution tracking setup":    "attribution_iq_setup",
    "attribution iq daily":          "attribution_iq_daily",
    "attribution daily refresh":     "attribution_iq_daily",
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
        # Dashboard Prometheus and Partner API v1 both charge the
        # live Profile IQ build price. The old dashboard-only key
        # (`chatbot_profile_iq_build`) is retired and returns $0, which
        # blocked paying wallets (Kartel $4,100, leftover credits 0)
        # from approving a $275 pull. Route both surfaces to the
        # canonical priced key.
        return "api_chatbot_profile_iq_build"

    # Normalized fallback: strip parens + collapse spaces/dashes.
    if "(" in pt:
        pt = pt.split("(", 1)[0].strip()
    return pt.replace(" ", "_").replace("-", "_").strip("_")


def prometheus_markup() -> float:
    """Multiplier applied to raw Anthropic cost for Prometheus session
    billing. Default 2.10 per Jenna's 110%-markup mandate."""
    p = load_pricing()
    return float(p.get("prometheus_markup_multiplier", 2.10))


def prometheus_meter() -> dict:
    """Published Prometheus meter: dollars per million input tokens,
    per million output tokens, and per web search (Jenna 2026-10-07:
    $10.50 / $52.50 / $0.021). Admin-tunable in pricing.json under
    prometheus_meter_usd; a missing or non-positive entry falls back
    to the published rate so usage can never bill nothing."""
    d = dict(DEFAULT_PRICING["prometheus_meter_usd"])
    try:
        p = load_pricing()
        v = p.get("prometheus_meter_usd")
        if isinstance(v, dict):
            for k in d:
                try:
                    if float(v.get(k, 0)) > 0:
                        d[k] = float(v[k])
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return d


def metered_answer_usd() -> float:
    """Billed USD per answer served from the library with no fresh
    model call (ledger replays, cache-served reads). 2026-09-14
    (Jenna): nothing is ever free; un-set-priced asks answerable from
    what's already there bill as metered usage. Admin-tunable in
    /admin/billing; a zero/negative admin entry falls back to the
    default so a served answer can never bill nothing."""
    p = load_pricing()
    try:
        val = float(p.get("metered_answer_usd", 2.10))
    except (TypeError, ValueError):
        return 2.10
    return val if val > 0 else 2.10


def _norm_company_token(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())


def is_excel_sports_subject(subject=None, subject_key: str = "") -> bool:
    """True for the Excel Sports Management company wallet or a
    seat whose company field is that name."""
    excel = _norm_company_token(EXCEL_SPORTS_COMPANY)
    if excel and _norm_company_token(subject_key) == excel:
        return True
    if not isinstance(subject, dict):
        return False
    for k in ("name", "company", "display_name"):
        if _norm_company_token(subject.get(k)) == excel:
            return True
    return False


def top_up_pack_sizes(subject=None, subject_key: str = "") -> list:
    """Preset USD amounts on the Add funds page.

    Global: locked $5k / $10k / $15k. Excel Sports: $500 / $1,000 /
    $1,500 (same 1x / 2x / 3x shape on their $500 floor).
    """
    floor = top_up_min_custom(subject, subject_key=subject_key)
    if floor + 1e-9 < TOP_UP_MIN_USD:
        return [float(floor), float(floor * 2), float(floor * 3)]
    return [float(x) for x in TOP_UP_PACKS_USD]


def top_up_min_custom(subject=None, subject_key: str = "") -> float:
    """Minimum amount a user can add.

    $5,000 globally; nothing lower. Custom amounts above the floor
    are allowed. Excel Sports Management is $500 (Jenna 2026-09-29).
    A stored top_up_min_usd at or above the $500 opening floor is
    honored so a company can keep that exception without a rename
    match.
    """
    if is_excel_sports_subject(subject, subject_key):
        return float(EXCEL_TOP_UP_MIN_USD)
    try:
        v = float((subject or {}).get("top_up_min_usd") or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= OPENING_TOPUP_MIN_USD:
        return round(v, 2)
    return float(TOP_UP_MIN_USD)


def opening_topup_usd(user: dict) -> float:
    """Required first top-up for a card-gated seat. 0 when the user
    is not on an opening lock."""
    if not user:
        return 0.0
    try:
        v = float(user.get("opening_topup_usd") or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return 0.0
    return round(v, 2)


def requires_card_to_view(user: dict) -> bool:
    """True when this seat may not see dashboard content until a
    card is on file and the opening top-up has landed."""
    return bool((user or {}).get("require_card_to_view"))


def _lifetime_topups_usd(subject: dict) -> float:
    try:
        return float((subject or {}).get("wallet_lifetime_topups_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def dashboard_view_locked(user: dict, users_data: dict = None,
                          subject: dict = None) -> bool:
    """True until the billing subject has a card AND lifetime
    top-ups meet the opening amount.

    Card + money live on the resolved subject (company wallet when
    billing_source is company). Cloak / super_admin bypass belongs
    to the request layer, not here.
    """
    if not requires_card_to_view(user):
        return False
    need = opening_topup_usd(user) or OPENING_TOPUP_MIN_USD
    if subject is None:
        if isinstance(users_data, dict):
            subject, _, _ = resolve_billing_subject(user, users_data)
        else:
            subject = user
    if not has_card_on_file(subject):
        return True
    return _lifetime_topups_usd(subject) + 1e-9 < float(need)


def opening_funding_unmet(user: dict, users_data: dict = None,
                          subject: dict = None) -> bool:
    """True until the billing subject has a card AND lifetime
    top-ups meet the opening amount on the user or the subject.

    This is the Prometheus / pull hold. It does not blank the rest
    of the dashboard. require_card_to_view still uses
    dashboard_view_locked for that.
    """
    if subject is None:
        if isinstance(users_data, dict):
            subject, _, _ = resolve_billing_subject(user or {}, users_data)
        else:
            subject = user
    need = max(opening_topup_usd(user), opening_topup_usd(subject))
    if need <= 0:
        return False
    if not has_card_on_file(subject):
        return True
    return _lifetime_topups_usd(subject) + 1e-9 < float(need)


def _parse_access_expires(raw):
    """Return (date_only, value) or (None, None).

    Date-only values stay the last UTC calendar day the seat can
    sign in. A datetime (ISO, with or without a timezone) is the
    exact cutoff. Naive datetimes are UTC.
    """
    s = str(raw or "").strip()
    if not s:
        return None, None
    if "T" in s or (len(s) > 10 and s[10] in " T"):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return False, dt
        except (TypeError, ValueError):
            pass
    try:
        return True, datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, None


def access_window_expired(user: dict, today=None) -> bool:
    """True when the seat is past access_expires.

    Date-only values expire after that UTC calendar day. Datetime
    values expire at that instant. Missing / unreadable dates never
    expire.
    """
    date_only, exp = _parse_access_expires((user or {}).get("access_expires"))
    if exp is None:
        return False
    if today is None:
        now = datetime.now(timezone.utc)
    elif isinstance(today, datetime):
        now = today if today.tzinfo else today.replace(tzinfo=timezone.utc)
    else:
        if date_only:
            return today > exp
        now = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    if date_only:
        return now.date() > exp
    return now > exp


def opening_checkout_allowed(amt, *, amount_locked: bool = False,
                             user: dict = None,
                             users_data: dict = None) -> bool:
    """Allow a sub-$5,000 amount only for an amount-locked admin
    payment link, or a seat whose opening top-up is still unpaid.
    Regular Add Funds stays at $5,000."""
    try:
        amt = float(amt)
    except (TypeError, ValueError):
        return False
    if amt + 1e-9 < OPENING_TOPUP_MIN_USD:
        return False
    if amt + 1e-9 >= TOP_UP_MIN_USD:
        return True
    if amount_locked:
        return True
    if user and opening_funding_unmet(user, users_data):
        return True
    return False


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

    Jenna 2026-09-23: paying accounts with a card default to
    auto-reload ($5,000 when the balance drops below $500). An
    explicit prepay_only or monthly_invoice choice always wins.
    """
    if not user:
        return "prepay_only"
    m = str(user.get("billing_mode") or "").strip().lower()
    if m in ("prepay_only", "auto_reload", "monthly_invoice"):
        return m
    if is_paying_customer(user) and has_card_on_file(user):
        return "auto_reload"
    return "prepay_only"


def billing_currency(subject) -> str:
    """Stripe charge currency for this billed subject.

    The wallet ledger stays in USD (`wallet_balance_usd`). A GBP
    company is charged in pounds at the same numeric amount: a
    5000 top-up is £5,000 on Stripe and credits 5000.00 on the
    wallet. Stripe's GBP-to-USD settlement is the spread we keep.
    """
    raw = str((subject or {}).get("billing_currency") or "usd")
    raw = raw.strip().lower()
    if raw in ("gbp", "£", "pound", "pounds", "sterling"):
        return "gbp"
    return "usd"


def money_symbol(subject=None, currency=None) -> str:
    cur = str(currency or billing_currency(subject) or "usd").strip().lower()
    return "£" if cur == "gbp" else "$"


def format_money(amount, subject=None, currency=None) -> str:
    cur = str(currency or billing_currency(subject) or "usd").strip().lower()
    if cur not in ("gbp", "usd"):
        cur = "usd"
    try:
        v = float(amount or 0)
    except (TypeError, ValueError):
        v = 0.0
    sign = "-" if v < 0 else ""
    return f"{sign}{money_symbol(currency=cur)}{abs(v):,.2f}"


def apply_auto_reload_preference(rec: dict, enabled: bool,
                                 subject_key: str = "") -> None:
    """Turn auto-reload on or off on a billed subject.

    On: billing_mode=auto_reload, fill missing threshold ($500) and
    lift any add amount below this subject's add-funds floor. Off:
    prepay_only. monthly_invoice is left alone. Always marks
    paying_customer.
    """
    if not isinstance(rec, dict):
        return
    rec["paying_customer"] = True
    mode = str(rec.get("billing_mode") or "").strip().lower()
    if mode == "monthly_invoice":
        return
    if enabled:
        rec["billing_mode"] = "auto_reload"
        try:
            thr = float(rec.get("auto_reload_threshold_usd"))
        except (TypeError, ValueError):
            thr = -1.0
        if thr < 0:
            rec["auto_reload_threshold_usd"] = 500.0
        try:
            amt = float(rec.get("auto_reload_amount_usd"))
        except (TypeError, ValueError):
            amt = 0.0
        floor = top_up_min_custom(rec, subject_key=subject_key)
        if amt < floor:
            rec["auto_reload_amount_usd"] = float(floor)
    else:
        rec["billing_mode"] = "prepay_only"


def parse_auto_reload_flag(md) -> object:
    """Payment-link checkout sends enable_auto_reload=1|0.

    Returns True / False / None. None means leave the existing
    preference alone (logged-in wallet top-up with no checkbox).
    A payment link with no flag defaults ON.
    """
    md = md if isinstance(md, dict) else {}
    raw = str(md.get("enable_auto_reload") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    if str(md.get("source") or "") == "admin_payment_link":
        return True
    return None


def auto_reload_threshold(user: dict) -> float:
    """Balance at or below which auto-reload triggers. Falls back to
    the pricing config's default."""
    if not user:
        return 500.0
    v = user.get("auto_reload_threshold_usd")
    if isinstance(v, (int, float)) and float(v) >= 0:
        return float(v)
    return float(load_pricing().get("auto_reload_defaults", {})
                 .get("threshold_usd", 500.0))


def auto_reload_amount(user: dict, subject_key: str = "") -> float:
    """How much to charge on an auto-reload trigger. Never below
    this subject's add-funds floor ($5,000 globally, $500 for
    Excel Sports Management)."""
    floor = top_up_min_custom(user, subject_key=subject_key)
    if not user:
        return float(floor)
    v = user.get("auto_reload_amount_usd")
    try:
        amt = float(v)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        try:
            amt = float(load_pricing().get("auto_reload_defaults", {})
                        .get("amount_usd", floor) or 0)
        except (TypeError, ValueError):
            amt = float(floor)
        if amt + 1e-9 < floor:
            amt = float(floor)
    return max(float(floor), amt)


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
                f"(balance {format_money(bal, user)} is at or below the "
                f"{format_money(thr, user)} threshold).")
        else:
            out["next_reload_note"] = (
                f"Auto-reload fires when balance drops below "
                f"{format_money(thr, user)}. Next top-up: "
                f"{format_money(auto_reload_amount(user), user)}.")
    elif mode == "monthly_invoice":
        out["next_reload_note"] = (
            "Monthly invoice reconciles on the 1st of every month.")
    return out


# ---------------------------------------------------------------------------
# Wallet writes (in-place, called under _users_cas_mutate)
# ---------------------------------------------------------------------------

_CREDIT_TXN_KINDS = ("topup", "auto_reload", "refund", "adjustment")


def _append_txn(user: dict, txn: dict, cap: int = 500):
    """Insert a transaction record at the head of the user's
    wallet_transactions list, capped at `cap` entries. Preserves
    audit history newest-first (same pattern as credit_usage_history).

    Top-ups, auto-reloads, refunds, and adjustments are never dropped
    to make room. Only usage rows age out when the list is full.
    """
    hist = user.setdefault("wallet_transactions", [])
    if not isinstance(hist, list):
        hist = []
    hist.insert(0, txn)
    if len(hist) <= cap:
        user["wallet_transactions"] = hist
        return
    credits = [t for t in hist
               if isinstance(t, dict)
               and str(t.get("kind") or "") in _CREDIT_TXN_KINDS]
    allow_deducts = max(0, cap - len(credits))
    out = []
    kept_deducts = 0
    for t in hist:
        kind = str((t or {}).get("kind") or "") if isinstance(t, dict) else ""
        if kind in _CREDIT_TXN_KINDS:
            out.append(t)
            continue
        if kept_deducts < allow_deducts:
            out.append(t)
            kept_deducts += 1
    user["wallet_transactions"] = out[:cap]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def existing_wallet_deduct(subject: dict, *, tool_key: str = "",
                           job_id: str = ""):
    """Return the deduct row for this (tool, job_id) when one exists.

    Used so a Prometheus session that already hit the wallet is not
    charged again when the closer retries after a deploy or a failed
    stamp write. Empty job_id never matches (those rows are not
    idempotent).
    """
    if not isinstance(subject, dict):
        return None
    jid = str(job_id or "").strip()
    if not jid:
        return None
    tk = str(tool_key or "").strip()
    for txn in subject.get("wallet_transactions") or []:
        if not isinstance(txn, dict):
            continue
        if str(txn.get("kind") or "") != "deduct":
            continue
        if str(txn.get("job_id") or "").strip() != jid:
            continue
        if tk and str(txn.get("tool") or "").strip() != tk:
            continue
        return txn
    return None


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

    A non-empty `job_id` is idempotent for this subject + tool: a
    retry returns the existing row and does not move the balance.
    That is what keeps a Prometheus session from charging twice
    when the closer runs again after a deploy.

    Callers with atomicity requirements MUST invoke this inside the
    same _users_cas_mutate closure that reads the subject record,
    so a concurrent top-up gets folded in on retry.
    """
    amt = round(float(amount_usd), 2)
    if amt <= 0:
        return {}
    existing = existing_wallet_deduct(
        subject, tool_key=tool_key, job_id=job_id)
    if existing:
        return existing
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
        "job_id": str(stripe_ref or ""),
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
                        pricing: Optional[dict] = None,
                        addon_cuts: int = 0) -> tuple:
    """Decide whether a pull for `tool_key` should hit the wallet AND
    at what dollar amount. `addon_cuts` embedded cuts price in on
    top of the tool (see addon_cuts_usd).

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
    if str(tool_key) in RETIRED_TOOL_KEYS:
        # 2026-09-09 (Jenna): retired tool_keys never fire a charge,
        # even if a stale per_tool_usd entry lingers from a legacy
        # admin edit. Belt + suspenders alongside the orphan-fold
        # filter in module_catalog() - the panel doesn't show these
        # rows AND the wallet doesn't debit them.
        return 0.0, "retired"
    pricing = pricing or load_pricing()
    usd = subject_tool_price_usd(user, tool_key, pricing)
    if usd <= 0:
        return 0.0, "no_charge"
    usd += addon_cuts_usd(user, tool_key, addon_cuts, pricing)
    return round(usd, 2), "wallet"


# Cut keys: a pull whose own tool_key IS a cut already prices its
# first cut; only the extra embedded cuts add on top.
CUT_TOOL_KEYS = frozenset({"api_profile_iq_cut", "profile_iq_derived_cut"})


def addon_cut_tool_key(tool_key: str) -> str:
    """Pricing key for one embedded add-on cut riding a pull priced at
    `tool_key`: the api_* family quotes api_profile_iq_cut (the same
    key _v1_price_usd_for quotes), everything else the dashboard
    derived-cut key."""
    tk = str(tool_key or "").strip()
    if tk in CUT_TOOL_KEYS:
        return tk
    return "api_profile_iq_cut" if tk.startswith("api_") \
        else "profile_iq_derived_cut"


def addon_cuts_usd(subject: dict, tool_key: str, addon_cuts: int = 0,
                   pricing: Optional[dict] = None) -> float:
    """Dollars the embedded add-on cuts add to a pull (Jenna
    2026-10-07, GoGo squeeZ SlymeZ for Kartel: 'charge kartel for
    that'). The approve card and the partner API both quote
    base + cut x n, but the wallet debit used to take the base tool
    price only, so every embedded cut rode free on a dollar wallet.
    The debit now carries the same cuts the quote did. A pull whose
    tool_key is itself a cut counts its first cut in the base."""
    try:
        n = max(int(addon_cuts or 0), 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 0.0
    tk = str(tool_key or "").strip()
    extra = n - 1 if tk in CUT_TOOL_KEYS else n
    if extra <= 0:
        return 0.0
    each = subject_tool_price_usd(subject, addon_cut_tool_key(tk),
                                  pricing or load_pricing())
    if each <= 0:
        return 0.0
    return round(each * extra, 2)


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
        # Do not treat a saved card as funding until the opening
        # top-up has landed. Otherwise the first Prometheus ask
        # would auto-charge $5,000 and skip the $500 opener.
        if opening_topup_usd(user) > 0 and (
                _lifetime_topups_usd(user) + 1e-9
                < opening_topup_usd(user)):
            return False, "opening_topup_unmet"
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

def needs_auto_reload(user: dict, subject_key: str = "") -> tuple:
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
    if opening_topup_usd(user) > 0 and (
            _lifetime_topups_usd(user) + 1e-9
            < opening_topup_usd(user)):
        return False, 0.0
    if wallet_balance(user) > auto_reload_threshold(user):
        return False, 0.0
    return True, auto_reload_amount(user, subject_key=subject_key)


def claim_topup_notice(subject_kind: str, subject_key: str,
                       stripe_ref: str) -> bool:
    """First caller for this credit txn wins the receipt email.

    Stamps `topup_emails_sent_at` on the matching topup / auto_reload
    row. Returns True when this caller should send. Returns False
    when another caller already claimed it.

    Fail-open: a missing txn, a CAS miss, or an import failure
    returns True so a notice is not dropped. Never raises.
    """
    ref = str(stripe_ref or "").strip()
    if not ref:
        return True
    claimed = {"ok": False, "already": False}
    try:
        from app import _users_cas_mutate  # type: ignore
    except Exception:
        return True

    def _apply(data):
        if not isinstance(data, dict):
            return None
        if str(subject_kind or "") == "company":
            subj = (data.get("companies") or {}).get(subject_key)
        else:
            subj = (data.get("users") or {}).get(subject_key)
        if not isinstance(subj, dict):
            return None
        for t in list(subj.get("wallet_transactions") or []):
            if not isinstance(t, dict):
                continue
            if str(t.get("kind") or "") not in ("topup", "auto_reload"):
                continue
            refs = {
                str(t.get("stripe_ref") or "").strip(),
                str(t.get("stripe_payment_intent") or "").strip(),
                str(t.get("stripe_checkout_session") or "").strip(),
            }
            if ref not in refs:
                continue
            if t.get("topup_emails_sent_at"):
                claimed["already"] = True
                return None
            t["topup_emails_sent_at"] = _now_iso()
            claimed["ok"] = True
            return data
        return None

    try:
        _users_cas_mutate(_apply)
    except Exception:
        return True
    if claimed["already"]:
        return False
    return True


def _notify_auto_reload_emails(*, subject_kind: str, subject_key: str,
                               subject_after: dict, amount_usd: float,
                               stripe_ref: str,
                               billed_via_username: str = "") -> None:
    """Fire the buyer receipt + jenna/liz/czarina notice.

    Never raises. Import and SES failures print and swallow so a
    charge that already landed is not rolled back.
    """
    try:
        from billing_routes import _emit_topup_emails_safe  # type: ignore
    except Exception as e:
        print(f"[wallet] auto-reload email module unavailable: {e}")
        return
    snap = subject_after if isinstance(subject_after, dict) else {}
    try:
        _emit_topup_emails_safe(
            subject_kind=subject_kind,
            subject_key=subject_key,
            subject_after=snap,
            amount_usd=amount_usd,
            new_balance_usd=wallet_balance(snap),
            stripe_ref=stripe_ref,
            kind="auto_reload",
            metadata={
                "purpose": "auto_reload",
                "subject_kind": subject_kind,
                "subject_key": subject_key,
                "dashboard_username": billed_via_username or "",
                "billed_via_username": billed_via_username or "",
            },
        )
    except Exception as e:
        print(f"[wallet] auto-reload email dispatch failed "
              f"(non-fatal): {e}")


def try_auto_reload(subject_key: str, subject_snapshot: dict, *,
                    subject_kind: str = "user",
                    billed_via_username: str = "") -> dict:
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
        should, amount = needs_auto_reload(
            subject_snapshot, subject_key=subject_key)
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
                    "dashboard_username": billed_via_username or "",
                    "billed_via_username": billed_via_username or "",
                    "charge_currency": billing_currency(subject_snapshot),
                },
                currency=billing_currency(subject_snapshot),
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

        final = _users_cas_mutate(_apply)
        applied = final is not None
        result.update({
            "fired": applied,
            "amount_usd": amount,
            "payment_intent_id": pi_id,
        })
        if not applied:
            # Webhook credited first. That handler sends the notice
            # when it lands the dollars, or the skip path sends it
            # when this function already did the credit.
            return result
        snap = subject_snapshot
        if isinstance(final, dict):
            if subject_kind == "company":
                snap = ((final.get("companies") or {}).get(subject_key)
                        or snap)
            else:
                snap = ((final.get("users") or {}).get(subject_key)
                        or snap)
        _notify_auto_reload_emails(
            subject_kind=subject_kind,
            subject_key=subject_key,
            subject_after=snap if isinstance(snap, dict) else {},
            amount_usd=amount,
            stripe_ref=pi_id,
            billed_via_username=billed_via_username,
        )
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
            return user, "user", _user_key(user, users_data)
        company_name = str(user.get("company") or "").strip()
        if not company_name:
            return user, "user", _user_key(user, users_data)
        companies = users_data.get("companies") or {}
        company = companies.get(company_name)
        if not isinstance(company, dict):
            return user, "user", _user_key(user, users_data)
        return company, "company", company_name
    except Exception:
        return user, "user", (
            _user_key(user, users_data) if isinstance(user, dict) else "")


def ensure_company_record(users_data: dict, company_name: str,
                          seed_user: Optional[dict] = None):
    """Create a real shared company wallet when a seat is billed
    through the company.

    Jenna 2026-09-23: adding a user and clicking Company shared
    wallet must create that company wallet automatically. A bare
    companies[name] stub (credit_pool only) is not enough: the
    charge path requires paying_customer plus a wallet balance.
    Existing wallets keep their money and flags. Stubs get the
    missing wallet fields filled in.
    """
    if not isinstance(users_data, dict):
        return None
    name = str(company_name or "").strip()
    if not name:
        return None
    companies = users_data.setdefault("companies", {})
    rec = companies.get(name)
    created = False
    if not isinstance(rec, dict):
        rec = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "credit_pool": 0,
            "credit_pool_used": 0,
        }
        companies[name] = rec
        created = True
    rec.setdefault("credit_pool", 0)
    rec.setdefault("credit_pool_used", 0)
    rec.setdefault("wallet_balance_usd", 0.0)
    rec.setdefault("wallet_lifetime_spend_usd", 0.0)
    rec.setdefault("wallet_lifetime_topups_usd", 0.0)
    rec.setdefault("wallet_transactions", [])
    if rec.get("paying_customer") is None:
        rec["paying_customer"] = True
    if created or not str(rec.get("billing_mode") or "").strip():
        rec["billing_mode"] = rec.get("billing_mode") or "auto_reload"
        if rec.get("billing_mode") not in (
                "prepay_only", "auto_reload", "monthly_invoice"):
            rec["billing_mode"] = "auto_reload"
        if rec.get("billing_mode") == "auto_reload":
            if not isinstance(rec.get("auto_reload_threshold_usd"),
                              (int, float)):
                rec["auto_reload_threshold_usd"] = 500.0
            if not isinstance(rec.get("auto_reload_amount_usd"),
                              (int, float)):
                rec["auto_reload_amount_usd"] = float(
                    top_up_min_custom(rec, subject_key=name))
    if is_excel_sports_subject(rec, name):
        rec.setdefault("top_up_min_usd", EXCEL_TOP_UP_MIN_USD)
    if seed_user and isinstance(seed_user, dict):
        email = str(seed_user.get("email") or "").strip()
        if email and not str(rec.get("billing_email") or "").strip():
            rec["billing_email"] = email
    return rec


# WBD (Jenna 2026-09-28): one shared company wallet. Every WBD seat
# manages it. Prometheus only. A report lands in a tab only after
# they pay to pull it.
WBD_COMPANY_NAME = "WBD"
PROMETHEUS_SELF_SERVE_PLAN = "prometheus_self_serve"
PUBLIC_SIGNUP_SOURCE = "self_serve_signup"
_PROMETHEUS_ONLY_FALSE_FLAGS = (
    "has_profile_iq_access",
    "has_subscriber_iq_access",
    "has_ecommerce_iq_access",
    "has_ticket_sales_iq_access",
    "has_hedge_fund_iq_access",
    "gets_hedge_fund_iq_emails",
    "has_ticket_sales_tracker_access",
    "has_rankers_iq_access",
    "has_talent_fit_access",
    "has_sf_conversion_access",
    "has_flywheel_conversion_access",
    "has_flywheel_iq_access",
    "has_brand_partnership_iq_access",
    "has_sentiment_iq_access",
    "has_journey_iq_access",
    "has_intent_iq_access",
    "has_share_of_time_access",
    "has_share_of_time_run_access",
    "has_blue_iq_access",
    "has_brand_tracking_iq_access",
    "has_impact_iq_access",
    "has_trends_iq_access",
    "has_microdramas_iq_access",
)
_PROMETHEUS_ONLY_EMPTY_LISTS = (
    "allowed_categories",
    "allowed_behavioral_categories",
    "allowed_journey_iq_runs",
    "allowed_flywheel_iq_runs",
    "allowed_subscriber_iq_runs",
    "allowed_intent_iq_runs",
    "allowed_trends_tabs",
    "allowed_rankers_tabs",
    "analysis_iq_modules",
    "rankers_iq_options",
    "hedge_fund_iq_tabs",
    "hedge_fund_iq_tickers",
    "impact_iq_journeys",
)
_CATALOG_LIST_FIELDS = (
    "allowed_runs",
    "allowed_flywheel_iq_runs",
    "allowed_subscriber_iq_runs",
    "allowed_journey_iq_runs",
)
_FLAG_FOR_CATALOG = {
    "allowed_runs": "has_profile_iq_access",
    "allowed_flywheel_iq_runs": "has_flywheel_iq_access",
    "allowed_subscriber_iq_runs": "has_subscriber_iq_access",
    "allowed_journey_iq_runs": "has_journey_iq_access",
}
_PRODUCT_FOR_CATALOG = {
    "allowed_runs": "profile_iq",
    "allowed_flywheel_iq_runs": "flywheel_iq",
    "allowed_subscriber_iq_runs": "subscriber_iq",
    "allowed_journey_iq_runs": "journey_iq",
}
# Jenna 2026-09-28: WBD sees Gilmore Girls for free in Profile IQ,
# Flywheel, and Subscriber IQ, plus Dexter's Lab and Young Sheldon
# in Digital Journey IQ. Title needles keep new matching files in
# those tabs without opening the rest of the fleet.
WBD_COMPLIMENTARY_LISTS = {
    "allowed_runs": [
        "Gilmore Girls Viewers - Avid Fan.csv",
        "Gilmore_Girls_Viewers_08_19_2026_18_53.csv",
    ],
    "allowed_flywheel_iq_runs": [
        "Gilmore_Girls_Acquired_Reactivated_Amazon_Flywheel_2026_09_17.csv",
    ],
    "allowed_subscriber_iq_runs": [
        "Gilmore_Girls_09_17_2026_15_56.csv",
    ],
    "allowed_journey_iq_runs": [
        "__demo_dexters_lab_pvod__",
        "__demo_young_sheldon_pvod__",
        "journey-iq/demos/__demo_dexters_lab_pvod__.json.gz",
        "journey-iq/demos/__demo_young_sheldon_pvod__.json.gz",
    ],
}
WBD_COMPLIMENTARY_TITLES = {
    "profile_iq": ["gilmore girls"],
    "flywheel_iq": ["gilmore girls"],
    "subscriber_iq": ["gilmore girls"],
    "journey_iq": [
        "young sheldon",
        "dexters lab",
        "dexter's laboratory",
        "dexters laboratory",
    ],
}


def is_wbd_company(name: str) -> bool:
    return str(name or "").strip().upper() == WBD_COMPANY_NAME


def _norm_title_blob(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def title_matches_needles(text, needles) -> bool:
    blob = _norm_title_blob(text)
    if not blob:
        return False
    for raw in needles or []:
        needle = _norm_title_blob(raw)
        if needle and needle in blob:
            return True
    return False


def complimentary_needles(holder, product: str) -> list:
    titles = holder.get("complimentary_titles") if isinstance(holder, dict) else None
    if not isinstance(titles, dict):
        return []
    out = []
    seen = set()
    for raw in titles.get(product) or []:
        n = str(raw or "").strip()
        fold = n.lower()
        if n and fold not in seen:
            seen.add(fold)
            out.append(n)
    return out


def owns_complimentary_title(holder, product: str, *name_parts) -> bool:
    blob = " ".join(str(p) for p in name_parts if p)
    return title_matches_needles(blob, complimentary_needles(holder, product))


def complimentary_keys(holder, product: str) -> list:
    keys = holder.get("complimentary_keys") if isinstance(holder, dict) else None
    if not isinstance(keys, dict):
        return []
    return _clean_paid_runs(keys.get(product))


def _holder_has_complimentary(holder) -> bool:
    if not isinstance(holder, dict):
        return False
    keys = holder.get("complimentary_keys")
    if isinstance(keys, dict):
        for lst in keys.values():
            if _clean_paid_runs(lst):
                return True
    titles = holder.get("complimentary_titles")
    if isinstance(titles, dict):
        for lst in titles.values():
            if any(str(x or "").strip() for x in (lst or [])):
                return True
    return False


def has_complimentary_grant(user, users_data=None) -> bool:
    """True when this seat or its company wallet holds a freebie."""
    if is_public_signup_seat(user):
        return _holder_has_complimentary(user)
    if _holder_has_complimentary(user):
        return True
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return False
    try:
        _subject, kind, name = resolve_billing_subject(user, users_data)
    except Exception:
        return False
    if kind != "company" or not name:
        return False
    rec = (users_data.get("companies") or {}).get(name)
    return _holder_has_complimentary(rec)


def complimentary_view_unlocked(user, users_data=None) -> bool:
    """Complimentary files do not need a card.

    A paid-only seat with a free grant can open those files with
    no card on file and no top-up. Full-access card locks (the
    opening $500 seat) stay locked so the fleet does not leak.
    """
    if not is_paid_only_plan(user):
        return False
    return has_complimentary_grant(user, users_data)


def _key_in_list(item_key, keys) -> bool:
    want = str(item_key or "").strip()
    if not want:
        return False
    want_base = want.rsplit("/", 1)[-1]
    for key in _clean_paid_runs(keys):
        if key == want or key.rsplit("/", 1)[-1] == want_base:
            return True
        if want.startswith("__demo_") and (key == want or want in key):
            return True
        if key.startswith("__demo_") and (key == want or key in want):
            return True
    return False


def catalog_item_allowed(holder, field: str, item_key, item_label="",
                         *, default_open: bool = True) -> bool:
    """True when this seat may open one catalog item.

    Full-access users (missing list or '*') stay open when
    default_open is True. Paid-only seats always carry an explicit
    list, so missing-or-star never reopens the fleet for them.
    Complimentary keys and title needles cover the free grant.
    """
    if not isinstance(holder, dict):
        return False
    raw = holder.get(field)
    if default_open and (raw is None or (isinstance(raw, list) and "*" in raw)):
        return True
    if _key_in_list(item_key, raw):
        return True
    product = _PRODUCT_FOR_CATALOG.get(field, "")
    if _key_in_list(item_key, complimentary_keys(holder, product)):
        return True
    return owns_complimentary_title(holder, product, item_key, item_label)


def refresh_paid_only_product_flags(user: dict) -> dict:
    """Turn on only the tabs that have a granted file or title."""
    if not isinstance(user, dict) or not is_paid_only_plan(user):
        return user
    for field, flag in _FLAG_FOR_CATALOG.items():
        keys = _clean_paid_runs(user.get(field))
        product = _PRODUCT_FOR_CATALOG.get(field, "")
        gifted = complimentary_keys(user, product)
        user[flag] = bool(
            keys or gifted or complimentary_needles(user, product))
    user["has_chatbot_profile_iq_access"] = True
    bpiq = _clean_paid_runs(user.get("brand_partnership_iq_journeys"))
    if bpiq:
        user["has_brand_partnership_iq_access"] = True
        mods = _clean_paid_runs(user.get("analysis_iq_modules"))
        if "brand_partnership_iq" not in mods:
            mods.append("brand_partnership_iq")
        user["analysis_iq_modules"] = mods
    return user


def _merge_title_map(into, incoming) -> dict:
    out = dict(into) if isinstance(into, dict) else {}
    if not isinstance(incoming, dict):
        return out
    for product, needles in incoming.items():
        have = []
        seen = set()
        for raw in list(out.get(product) or []) + list(needles or []):
            n = str(raw or "").strip()
            fold = n.lower()
            if n and fold not in seen:
                seen.add(fold)
                have.append(n)
        out[str(product)] = have
    return out


def _merge_key_map(into, incoming) -> dict:
    out = dict(into) if isinstance(into, dict) else {}
    if not isinstance(incoming, dict):
        return out
    for product, keys in incoming.items():
        cur = _clean_paid_runs(out.get(product))
        for key in _clean_paid_runs(keys):
            if key not in cur:
                cur.append(key)
        out[str(product)] = cur
    return out


def seed_wbd_complimentary(rec: dict) -> dict:
    """Keep WBD's free Gilmore / Dexter / Young Sheldon grant on the
    company record. Paid pulls stay on allowed_runs; this gift lives
    on complimentary_keys so a free file is not billed as a purchase.
    No card and no top-up are required to open these files."""
    if not isinstance(rec, dict):
        return rec
    gifted = {}
    for field, keys in WBD_COMPLIMENTARY_LISTS.items():
        product = _PRODUCT_FOR_CATALOG.get(field)
        if product:
            gifted[product] = list(keys)
    rec["complimentary_keys"] = _merge_key_map(
        rec.get("complimentary_keys"), gifted)
    rec["complimentary_titles"] = _merge_title_map(
        rec.get("complimentary_titles"), WBD_COMPLIMENTARY_TITLES)
    return rec


def ensure_wbd_shared_wallet(users_data: dict, seed_user=None) -> dict:
    """Create or refresh the WBD company wallet and the seat template
    every WBD member inherits. Existing money and card stay put."""
    rec = ensure_company_record(
        users_data, WBD_COMPANY_NAME, seed_user=seed_user)
    if not isinstance(rec, dict):
        return rec
    rec["member_prometheus_only"] = True
    rec["member_company_billing_admin"] = True
    rec["default_spend_scope"] = "*"
    rec["paying_customer"] = True
    rec["unlimited"] = False
    if not str(rec.get("billing_mode") or "").strip():
        rec["billing_mode"] = "auto_reload"
    if not isinstance(rec.get("allowed_runs"), list):
        rec["allowed_runs"] = []
    elif "*" in rec["allowed_runs"]:
        rec["allowed_runs"] = _clean_paid_runs(rec["allowed_runs"])
    seed_wbd_complimentary(rec)
    return rec


def apply_prometheus_only_seat(user: dict, *, wipe_catalog: bool = False) -> dict:
    """Prometheus only. No product tabs. Catalog starts empty so a
    report appears only after they pay to pull it. Complimentary
    company grants are copied back on inherit."""
    if not isinstance(user, dict):
        return user
    user["plan"] = PROMETHEUS_SELF_SERVE_PLAN
    user["paying_customer"] = True
    user["unlimited"] = False
    user["credits"] = 0
    user["has_chatbot_profile_iq_access"] = True
    user["prometheus_access"] = "full"
    user["prometheus_mode"] = "both"
    user["pay_per_use_enabled"] = True
    user["auto_access_new"] = {"profile_iq": False}
    user["sf_conversion_journeys"] = None
    user["brand_partnership_iq_journeys"] = None
    user["allowed_lenses"] = []
    for flag in _PROMETHEUS_ONLY_FALSE_FLAGS:
        user[flag] = False
    for key in _PROMETHEUS_ONLY_EMPTY_LISTS:
        user[key] = []
    runs = user.get("allowed_runs")
    if wipe_catalog or not isinstance(runs, list) or "*" in runs:
        user["allowed_runs"] = []
    if wipe_catalog or not isinstance(user.get("complimentary_titles"), dict):
        user["complimentary_titles"] = {}
    if wipe_catalog or not isinstance(user.get("complimentary_keys"), dict):
        user["complimentary_keys"] = {}
    return user


def _clean_paid_runs(runs) -> list:
    if not isinstance(runs, list):
        return []
    out = []
    # Dedup through a set, not `key not in out`. That list scan made this
    # quadratic in the seat's run count, and callers like _key_in_list rebuild
    # the list on every lookup: a 5,959-run seat cost 87ms per call, so the
    # per-profile check in list_jobs needed 6.5 min of CPU per request.
    seen = set()
    for raw in runs:
        key = str(raw or "").strip()
        if key and key != "*" and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def company_catalog_list(users_data: dict, company_name: str,
                         field: str = "allowed_runs") -> list:
    if not isinstance(users_data, dict) or not company_name:
        return []
    rec = (users_data.get("companies") or {}).get(company_name)
    if not isinstance(rec, dict):
        return []
    return _clean_paid_runs(rec.get(field))


def company_paid_runs(users_data: dict, company_name: str) -> list:
    """Profile keys this company paid to pull. Empty if none."""
    return company_catalog_list(users_data, company_name, "allowed_runs")


def grant_company_paid_runs(users_data: dict, company_name: str,
                            keys) -> bool:
    """Record paid pulls on the company and give every teammate the
    same list. A WBD employee sees what any other WBD employee paid
    for, and nothing from the rest of the catalog."""
    add = _clean_paid_runs(keys)
    if not isinstance(users_data, dict) or not company_name or not add:
        return False
    companies = users_data.setdefault("companies", {})
    rec = companies.get(company_name)
    paid = _clean_paid_runs(
        rec.get("allowed_runs") if isinstance(rec, dict) else None)
    wrote = False
    for key in add:
        if key not in paid:
            paid.append(key)
            wrote = True
    if isinstance(rec, dict):
        rec["allowed_runs"] = list(paid)
    for _uname, member in company_members(company_name, users_data):
        cur = member.get("allowed_runs")
        if not isinstance(cur, list) or "*" in cur:
            continue
        extra = [k for k in paid if k not in cur]
        if extra:
            member["allowed_runs"] = list(cur) + extra
            wrote = True
        refresh_paid_only_product_flags(member)
    return wrote


def journey_iq_run_access(user) -> tuple:
    """Resolve Digital Journey IQ per-run access.

    Returns ``(is_admin, allow_all, allowed_keys)``.
    An explicit ``allowed_journey_iq_runs`` list (including empty)
    wins over a full Profile IQ catalog. Staff and star / missing
    lists stay open. Paid-only seats with an empty list stay closed
    except for complimentary grants checked by the caller.
    """
    if not isinstance(user, dict):
        user = {}
    role = str(user.get("role") or "").strip().lower()
    is_admin = role in ("admin", "super_admin")
    if is_admin:
        return True, True, set()
    if is_internal_staff_seat(user):
        return False, True, set()
    raw = user.get("allowed_journey_iq_runs")
    if isinstance(raw, list) and "*" not in raw:
        return False, False, {
            str(k).strip() for k in raw if str(k or "").strip()}
    if has_full_profile_catalog(user):
        return False, True, set()
    if raw is None:
        return False, True, set()
    if isinstance(raw, list) and "*" in raw:
        return False, True, set()
    return False, True, set()


def inherit_company_explicit_journey_iq(user: dict, users_data: dict) -> dict:
    """Copy an explicit company Journey IQ allow-list onto a full-catalog seat.

    Paid-only seats already inherit every catalog list. Full-catalog
    seats keep Profile IQ open and take only the company's Digital
    Journey IQ list when that list is explicit (no '*').
    """
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return user
    if is_public_signup_seat(user) or is_paid_only_plan(user):
        return user
    if is_internal_staff_seat(user):
        return user
    key = str(user.get("company") or "").strip()
    rec = (users_data.get("companies") or {}).get(key)
    if not isinstance(rec, dict):
        return user
    raw = rec.get("allowed_journey_iq_runs")
    if not isinstance(raw, list) or "*" in raw:
        return user
    user["allowed_journey_iq_runs"] = _clean_paid_runs(raw)
    if user["allowed_journey_iq_runs"]:
        user["has_journey_iq_access"] = True
    return user


def inherit_company_paid_runs(user: dict, users_data: dict, *,
                              replace: bool = False) -> dict:
    """Copy the company's paid and complimentary lists onto this seat."""
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return user
    if is_public_signup_seat(user):
        user["complimentary_titles"] = {}
        user["complimentary_keys"] = {}
        refresh_paid_only_product_flags(user)
        return user
    _subject, kind, key = resolve_billing_subject(user, users_data)
    if kind != "company" or not key:
        refresh_paid_only_product_flags(user)
        return user
    rec = (users_data.get("companies") or {}).get(key)
    for field in _CATALOG_LIST_FIELDS:
        paid = company_catalog_list(users_data, key, field)
        cur = user.get(field)
        if replace or not isinstance(cur, list) or "*" in cur:
            user[field] = list(paid)
        else:
            user[field] = list(cur) + [k for k in paid if k not in cur]
    if isinstance(rec, dict) and not is_public_signup_seat(user):
        if replace or not isinstance(user.get("complimentary_titles"), dict):
            user["complimentary_titles"] = _merge_title_map(
                {}, rec.get("complimentary_titles"))
        else:
            user["complimentary_titles"] = _merge_title_map(
                user.get("complimentary_titles"),
                rec.get("complimentary_titles"))
        if replace or not isinstance(user.get("complimentary_keys"), dict):
            user["complimentary_keys"] = _merge_key_map(
                {}, rec.get("complimentary_keys"))
        else:
            user["complimentary_keys"] = _merge_key_map(
                user.get("complimentary_keys"),
                rec.get("complimentary_keys"))
        company_bpiq = _clean_paid_runs(
            rec.get("brand_partnership_iq_journeys"))
        if company_bpiq:
            cur = user.get("brand_partnership_iq_journeys")
            if replace or not isinstance(cur, list):
                user["brand_partnership_iq_journeys"] = list(company_bpiq)
            else:
                have = _clean_paid_runs(cur)
                user["brand_partnership_iq_journeys"] = have + [
                    k for k in company_bpiq if k not in have]
    elif is_public_signup_seat(user):
        user["complimentary_titles"] = {}
        user["complimentary_keys"] = {}
    refresh_paid_only_product_flags(user)
    return user


def attach_wbd_seat(user: dict, users_data: dict, *,
                    wipe_catalog: bool = False, seed: bool = True):
    """Point this user at the shared WBD wallet and apply the seat."""
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return None
    rec = ensure_wbd_shared_wallet(
        users_data, seed_user=user if seed else None)
    apply_prometheus_only_seat(user, wipe_catalog=wipe_catalog)
    user["company"] = WBD_COMPANY_NAME
    if is_public_signup_seat(user):
        user["billing_source"] = "user"
        user["complimentary_titles"] = {}
        user["complimentary_keys"] = {}
        refresh_paid_only_product_flags(user)
        return rec
    user["billing_source"] = "company"
    user["company_billing_admin"] = True
    inherit_company_paid_runs(user, users_data, replace=wipe_catalog)
    return rec


_STAFF_USERNAMES = frozenset({
    "admin", "jenna", "jessie", "liz", "anastasia",
})


def is_internal_staff_seat(user: dict, username: str = "") -> bool:
    """Crosswalk staff never get the paid-reports-only lock."""
    if not isinstance(user, dict):
        user = {}
    if str(user.get("role") or "").strip().lower() == "super_admin":
        return True
    uname = str(username or "").strip().lower()
    if uname in _STAFF_USERNAMES:
        return True
    email = str(user.get("email") or "").strip().lower()
    if email.endswith("@crosswalknyc.com"):
        return True
    return False


def has_full_profile_catalog(user, username: str = "") -> bool:
    """True when this seat is not on the Prometheus-only governor.

    Only prometheus_self_serve seats are limited to files they paid
    for (plus any complimentary grant). Jessie, other staff, Kartel,
    and every regular dashboard seat see the full catalog even if an
    old allowed_runs snapshot is still on the record.

    This is the FILE governor. It does not turn product modules on.
    Admins still flip Profile IQ / Subscriber IQ / Flywheel / etc.
    on Create User and Edit User via the has_*_iq_access flags.
    """
    if not isinstance(user, dict):
        return False
    if is_internal_staff_seat(user, username):
        return True
    return not is_paid_only_plan(user)


def profile_iq_module_enabled(user, username: str = "") -> bool:
    """True when this seat may open the Profile IQ product.

    Separate from has_full_profile_catalog. Staff always have the
    module. Regular seats follow the admin Create / Edit User
    checkbox (default on). Prometheus-only seats follow the flag
    that refresh_paid_only_product_flags sets when they hold a file.
    """
    if not isinstance(user, dict):
        return False
    if is_internal_staff_seat(user, username):
        return True
    if str(user.get("role") or "").strip().lower() == "super_admin":
        return True
    if is_paid_only_plan(user):
        return bool(user.get("has_profile_iq_access"))
    return user.get("has_profile_iq_access", True) is not False


def is_paid_only_plan(user) -> bool:
    return (isinstance(user, dict)
            and str(user.get("plan") or "").strip()
            == PROMETHEUS_SELF_SERVE_PLAN)


def is_public_signup_seat(user) -> bool:
    """True when the seat was created on /site/signup.html.

    Public signups are Prometheus-only. They never inherit the WBD
    complimentary grant. They pay retail for an existing catalog
    file until they themselves pull it.
    """
    if not isinstance(user, dict):
        return False
    return (str(user.get("signup_source") or "").strip()
            == PUBLIC_SIGNUP_SOURCE)


def pays_retail_for_library_match(user, username: str = "") -> bool:
    """Prometheus-only seats pay retail for a catalog file they do
    not already own. Full-access seats keep the free library reuse.
    Staff never pay this way."""
    if not isinstance(user, dict):
        return False
    if is_internal_staff_seat(user, username):
        return False
    return is_paid_only_plan(user)


def subject_profile_pull_usd(subject) -> float:
    """Company or user Profile IQ sticker. Kartel is 275. 0 means
    the global catalog price applies (or no override is set)."""
    if not isinstance(subject, dict):
        return 0.0
    try:
        usd = float(subject.get("profile_pull_usd") or 0)
    except (TypeError, ValueError):
        return 0.0
    return usd if usd > 0 else 0.0


def charges_profile_for_library_match(user, username: str = "",
                                      users_data=None) -> bool:
    """True when this seat is in the class that pays for a catalog
    file they have not already bought. Prometheus-only seats pay
    retail. A company with its own profile_pull_usd (Kartel $275)
    pays that sticker only when they did not already run that
    profile. Staff never pay this way."""
    if not isinstance(user, dict):
        return False
    if is_internal_staff_seat(user, username):
        return False
    if pays_retail_for_library_match(user, username):
        return True
    subject = user
    if isinstance(users_data, dict):
        try:
            subject, _kind, _key = resolve_billing_subject(user, users_data)
        except Exception:
            subject = user
    return subject_profile_pull_usd(subject) > 0


_PAID_PROFILE_KEY = "paid_profile_keys"
_PROFILE_DATE_STAMP_RE = re.compile(r"_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}.*$")


def fold_profile_label(value: str) -> str:
    """Compare-key for a profile name or S3 file. Drops path, .csv,
    and the dated filename stamp so a reuse matches the original
    pull."""
    s = str(value or "").strip().replace("\\", "/")
    s = s.rsplit("/", 1)[-1]
    if s.lower().endswith(".csv"):
        s = s[:-4]
    s = _PROFILE_DATE_STAMP_RE.sub("", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def subject_from_usage_description(desc: str) -> str:
    """Folded subject from a wallet or credit-history description."""
    s = str(desc or "").strip()
    if not s or s.upper().startswith("REFUND"):
        return ""
    if "] - " in s:
        s = s.split("] - ", 1)[1]
    elif s.lower().startswith("profile build - "):
        s = s[16:]
    elif s.lower().startswith("profile cut - "):
        s = s[14:]
    elif " - " in s:
        head, tail = s.split(" - ", 1)
        hl = head.lower()
        if "profile" in hl or "chatbot" in hl:
            s = tail
    return fold_profile_label(s)


def profile_labels_match(left: str, right: str) -> bool:
    a = fold_profile_label(left)
    b = fold_profile_label(right)
    if not a or not b:
        return False
    if a == b:
        return True
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    return len(shorter) >= 12 and longer.startswith(shorter)


def paid_profile_keys(rec) -> list:
    if not isinstance(rec, dict):
        return []
    out = []
    seen = set()
    for raw in rec.get(_PAID_PROFILE_KEY) or []:
        tok = fold_profile_label(raw)
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def record_paid_profile(rec, s3_key: str = "",
                        subject_name: str = "") -> bool:
    """Remember a profile this wallet already paid to run."""
    if not isinstance(rec, dict):
        return False
    add = []
    for raw in (s3_key, subject_name):
        tok = fold_profile_label(raw) if raw else ""
        if tok:
            add.append(tok)
    if not add:
        return False
    cur = list(rec.get(_PAID_PROFILE_KEY) or [])
    seen = {fold_profile_label(x) for x in cur}
    wrote = False
    for tok in add:
        if tok not in seen:
            cur.append(tok)
            seen.add(tok)
            wrote = True
    if wrote:
        rec[_PAID_PROFILE_KEY] = cur[-2000:]
    return wrote


def _add_paid_token(seen: set, out: list, tok: str) -> None:
    tok = fold_profile_label(tok)
    if tok and tok not in seen:
        seen.add(tok)
        out.append(tok)


def paid_profile_tokens(user, users_data=None) -> list:
    """Every profile this seat (or its company wallet) already paid
    to run. Built from the paid_profile_keys stamp plus wallet and
    credit-history descriptions. Does not use allowed_runs: a full
    catalog list is not proof of payment."""
    out = []
    seen = set()
    if isinstance(user, dict):
        for tok in paid_profile_keys(user):
            _add_paid_token(seen, out, tok)
        for h in user.get("credit_usage_history") or []:
            if str(h.get("pull_type") or "").lower() == "refund":
                continue
            try:
                used = float(h.get("credits_used") or 0)
            except (TypeError, ValueError):
                used = 0.0
            if used <= 0 and not h.get("wallet_charged_usd"):
                continue
            _add_paid_token(seen, out,
                            subject_from_usage_description(
                                h.get("description")))
    if not isinstance(users_data, dict) or not isinstance(user, dict):
        return out
    try:
        subject, kind, cname = resolve_billing_subject(user, users_data)
    except Exception:
        return out
    if isinstance(subject, dict):
        for tok in paid_profile_keys(subject):
            _add_paid_token(seen, out, tok)
        for t in subject.get("wallet_transactions") or []:
            if str(t.get("kind") or "") != "deduct":
                continue
            try:
                if float(t.get("amount_usd") or 0) >= 0:
                    continue
            except (TypeError, ValueError):
                continue
            _add_paid_token(seen, out,
                            subject_from_usage_description(
                                t.get("description")))
    if kind == "company" and cname:
        for _uname, member in company_members(cname, users_data):
            if member is user:
                continue
            for h in member.get("credit_usage_history") or []:
                if str(h.get("pull_type") or "").lower() == "refund":
                    continue
                try:
                    used = float(h.get("credits_used") or 0)
                except (TypeError, ValueError):
                    used = 0.0
                if used <= 0 and not h.get("wallet_charged_usd"):
                    continue
                _add_paid_token(seen, out,
                                subject_from_usage_description(
                                    h.get("description")))
    return out


def already_paid_for_profile(user, users_data, s3_key="",
                             subject_name="", username: str = "") -> bool:
    """True when this seat already paid to run this profile.

    Prometheus-only seats use the explicit paid-key list. Kartel (and
    any profile_pull_usd company) uses the paid-profile stamp plus
    wallet / credit history. A catalog * list is not a prior payment.
    """
    if not isinstance(user, dict):
        return False
    if pays_retail_for_library_match(user, username):
        return already_owns_paid_run(
            user, users_data, s3_key, username=username)
    if not charges_profile_for_library_match(user, username, users_data):
        return False
    wanted = [w for w in (
        fold_profile_label(s3_key),
        fold_profile_label(subject_name),
        subject_from_usage_description(subject_name),
    ) if w]
    if not wanted:
        return False
    for tok in paid_profile_tokens(user, users_data):
        for w in wanted:
            if profile_labels_match(w, tok):
                return True
    return False


def already_owns_paid_run(user, users_data, s3_key,
                          username: str = "") -> bool:
    """True when this seat or its company wallet already holds this
    profile key, including a complimentary Gilmore Girls grant.
    Public signup seats only own files on their own record."""
    wanted = str(s3_key or "").strip()
    if not wanted or not isinstance(user, dict):
        return False
    if catalog_item_allowed(user, "allowed_runs", wanted, default_open=False):
        return True
    if is_public_signup_seat(user):
        return False
    if not isinstance(users_data, dict):
        return False
    try:
        _subject, kind, cname = resolve_billing_subject(user, users_data)
    except Exception:
        return False
    if kind != "company" or not cname:
        return False
    rec = (users_data.get("companies") or {}).get(cname)
    return catalog_item_allowed(rec, "allowed_runs", wanted, default_open=False)


def company_wants_paid_only(users_data: dict, company_name: str) -> bool:
    """True when new company seats should inherit paid-reports-only."""
    name = str(company_name or "").strip()
    if not name:
        return False
    if is_wbd_company(name):
        return True
    rec = ((users_data or {}).get("companies") or {}).get(name)
    return bool(isinstance(rec, dict) and rec.get("member_prometheus_only"))


def mark_company_paid_only(users_data: dict, company_name: str,
                           seed_user=None) -> dict:
    """Stamp a company so new members only see paid Prometheus pulls."""
    name = str(company_name or "").strip()
    if not name or not isinstance(users_data, dict):
        return {}
    if is_wbd_company(name):
        return ensure_wbd_shared_wallet(users_data, seed_user=seed_user)
    rec = ensure_company_record(users_data, name, seed_user=seed_user)
    if not isinstance(rec, dict):
        return rec
    rec["member_prometheus_only"] = True
    rec["paying_customer"] = True
    rec["unlimited"] = False
    if not isinstance(rec.get("allowed_runs"), list):
        rec["allowed_runs"] = []
    elif "*" in rec["allowed_runs"]:
        rec["allowed_runs"] = _clean_paid_runs(rec["allowed_runs"])
    return rec


def attach_paid_only_seat(user: dict, users_data: dict, *,
                          wipe_catalog: bool = True,
                          username: str = ""):
    """Lock one seat to Prometheus + reports they pay to pull.

    If they bill through a company wallet, that company is stamped so
    the next person added there gets the same access. WBD still gives
    every member wallet-admin. Other companies keep their own admin
    flags.
    """
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return None
    if is_internal_staff_seat(user, username):
        return None
    company = str(user.get("company") or "").strip()
    if is_wbd_company(company):
        return attach_wbd_seat(user, users_data, wipe_catalog=wipe_catalog)
    apply_prometheus_only_seat(user, wipe_catalog=wipe_catalog)
    billed = str(user.get("billing_source") or "").strip().lower() == "company"
    if company and billed:
        mark_company_paid_only(users_data, company, seed_user=user)
        inherit_company_paid_runs(user, users_data, replace=wipe_catalog)
    return user


def attach_paid_only_company(users_data: dict, company_name: str, *,
                             wipe_members: bool = True):
    """Lock a company wallet and every current member on that wallet."""
    name = str(company_name or "").strip()
    if not name or not isinstance(users_data, dict):
        return None, [], []
    rec = mark_company_paid_only(users_data, name)
    applied = []
    skipped = []
    if not wipe_members:
        return rec, applied, skipped
    for uname, member in company_members(name, users_data):
        if is_internal_staff_seat(member, uname):
            skipped.append(uname)
            continue
        if is_wbd_company(name):
            attach_wbd_seat(member, users_data, wipe_catalog=True, seed=False)
        else:
            apply_prometheus_only_seat(member, wipe_catalog=True)
            inherit_company_paid_runs(member, users_data, replace=True)
        applied.append(uname)
    return rec, applied, skipped


def apply_full_dashboard_seat(user: dict) -> dict:
    """Undo apply_prometheus_only_seat. Full catalog. Product tabs on.

    Does not touch wallet, card, password, or billing_source. WBD and
    public-signup seats are not converted here.
    """
    if not isinstance(user, dict):
        return user
    if is_public_signup_seat(user):
        return user
    if is_wbd_company(str(user.get("company") or "")):
        return user
    if str(user.get("plan") or "").strip() == PROMETHEUS_SELF_SERVE_PLAN:
        user["plan"] = None
    user["has_chatbot_profile_iq_access"] = True
    user["prometheus_access"] = "full"
    if str(user.get("prometheus_mode") or "").strip() not in (
            "analysis", "pull", "both"):
        user["prometheus_mode"] = "both"
    for flag in _PROMETHEUS_ONLY_FALSE_FLAGS:
        user[flag] = True
    user["allowed_runs"] = ["*"]
    user["allowed_categories"] = ["*"]
    user["allowed_behavioral_categories"] = ["*"]
    for key in _PROMETHEUS_ONLY_EMPTY_LISTS:
        if key in ("allowed_categories", "allowed_behavioral_categories"):
            continue
        if key.endswith("_runs") or key.endswith("_tabs") or key in (
                "impact_iq_journeys", "rankers_iq_options",
                "hedge_fund_iq_tickers"):
            user[key] = ["*"]
    for field in _CATALOG_LIST_FIELDS:
        user[field] = ["*"]
    user["allowed_lenses"] = ["*"]
    aan = user.get("auto_access_new")
    if not isinstance(aan, dict):
        aan = {}
    aan["profile_iq"] = True
    user["auto_access_new"] = aan
    return user


def mark_company_full_access(users_data: dict, company_name: str) -> dict:
    """Clear the paid-reports-only stamp so new seats stay full access."""
    name = str(company_name or "").strip()
    if not name or not isinstance(users_data, dict):
        return {}
    if is_wbd_company(name):
        return ((users_data.get("companies") or {}).get(name) or {})
    rec = ((users_data.get("companies") or {}).get(name) or {})
    if not isinstance(rec, dict):
        return {}
    rec["member_prometheus_only"] = False
    return rec


def detach_paid_only_seat(user: dict, users_data: dict, *,
                          username: str = "",
                          unstamp_company: bool = False):
    """Turn paid-reports-only off for one seat.

    WBD and public-signup seats stay locked. Staff are a no-op.
    """
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return None
    if is_internal_staff_seat(user, username):
        return None
    if is_public_signup_seat(user):
        return None
    company = str(user.get("company") or "").strip()
    if is_wbd_company(company):
        return None
    apply_full_dashboard_seat(user)
    billed = str(user.get("billing_source") or "").strip().lower() == "company"
    if unstamp_company and company and billed:
        mark_company_full_access(users_data, company)
    return user


def detach_paid_only_company(users_data: dict, company_name: str):
    """Turn paid-reports-only off for a company wallet and members."""
    name = str(company_name or "").strip()
    if not name or not isinstance(users_data, dict):
        return None, [], []
    if is_wbd_company(name):
        return None, [], []
    rec = mark_company_full_access(users_data, name)
    applied = []
    skipped = []
    for uname, member in company_members(name, users_data):
        if is_internal_staff_seat(member, uname):
            skipped.append(uname)
            continue
        if is_public_signup_seat(member):
            skipped.append(uname)
            continue
        apply_full_dashboard_seat(member)
        applied.append(uname)
    return rec, applied, skipped


def company_teammate_usernames(user: dict, users_data: dict,
                               username: str = "") -> list:
    """Usernames that share this user's company wallet, buyer first.

    A paid Prometheus pull belongs to the company that paid, so every
    teammate on that wallet can open the report.
    """
    names = []
    if username:
        names.append(str(username))
    if not isinstance(user, dict) or not isinstance(users_data, dict):
        return names
    _subject, kind, key = resolve_billing_subject(user, users_data)
    if kind != "company" or not key:
        return names
    for uname, _u in company_members(key, users_data):
        if uname not in names:
            names.append(uname)
    return names


def admin_billing_row_for_user(username: str, user: dict,
                               users_data: dict) -> dict:
    """Admin Users-tab snapshot. Company-billed people show the
    shared company wallet (balance, spend, card, txns), not their
    empty personal wallet. That is why Kartel money vanished from
    the Users list even though the company wallet was live.
    """
    if not isinstance(user, dict):
        user = {}
    subject, kind, key = resolve_billing_subject(user, users_data or {})
    if not isinstance(subject, dict):
        subject, kind, key = user, "user", username
    billed = kind == "company"
    paying = (is_paying_customer(subject) if billed
              else bool(user.get("paying_customer")))
    return {
        "username": username,
        "email": str(user.get("email") or ""),
        "role": str(user.get("role") or ""),
        "company": str(user.get("company") or ""),
        "billing_source": str(user.get("billing_source") or "user"),
        "company_billing_admin": bool(user.get("company_billing_admin")),
        "paying_customer": paying,
        "unlimited": is_unlimited(user),
        "billing_mode": billing_mode(subject),
        "wallet_balance_usd": wallet_balance(subject),
        "wallet_lifetime_topups_usd": float(subject.get(
            "wallet_lifetime_topups_usd", 0.0) or 0.0),
        "wallet_lifetime_spend_usd": float(subject.get(
            "wallet_lifetime_spend_usd", 0.0) or 0.0),
        "auto_reload_threshold_usd": auto_reload_threshold(subject),
        "auto_reload_amount_usd": auto_reload_amount(
            subject, subject_key=key),
        "top_up_min_custom_usd": top_up_min_custom(
            subject, subject_key=key),
        "monthly_invoice_limit_usd": monthly_invoice_limit(subject),
        "has_card_on_file": has_card_on_file(subject),
        "card_brand": str(subject.get(
            "stripe_payment_method_brand") or ""),
        "card_last4": str(subject.get(
            "stripe_payment_method_last4") or ""),
        "wallet_transactions": list(subject.get(
            "wallet_transactions") or [])[:500],
        "billed_via_company": billed,
        "company_wallet_name": key if billed else "",
        "plan": str(user.get("plan") or ""),
        "paid_only_access": is_paid_only_plan(user),
        "billing_currency": billing_currency(subject),
        "money_symbol": money_symbol(subject),
    }


def norm_usage_desc(s) -> str:
    """Fold 'Title (revised to $275)' onto 'Title' so a wallet
    deduct and a credit-history row of the same pull collapse."""
    t = str(s or "").strip().lower()
    if "(revised" in t:
        t = t.split("(revised")[0].strip()
    return t


def collect_usage_ledger_rows(data, company_f="", user_f=""):
    """Union credit_usage_history with wallet deducts.

    Wallet-only pulls used to skip the user history write, so Kartel
    usage (Five9, Metamucil, Superside, ...) never appeared on the
    Usage Ledger even though the company wallet was charged.
    """
    company_f = (company_f or "").strip().lower()
    user_f = (user_f or "").strip().lower()
    data = data or {}
    users = data.get("users") or {}
    companies_map = data.get("companies") or {}
    raw = []
    companies = set()
    user_list = []

    def _usd(val):
        try:
            return abs(float(val or 0))
        except (TypeError, ValueError):
            return 0.0

    def _walk_wallet(txns, *, company, default_username, email=""):
        for t in (txns or []):
            if not isinstance(t, dict):
                continue
            if str(t.get("kind") or "") != "deduct":
                continue
            via = str(t.get("billed_via_username")
                      or default_username or "").strip()
            raw.append({
                "used_at": str(t.get("ts") or "")[:19],
                "company": company or "",
                "username": via or default_username or "",
                "email": email or "",
                "description": str(t.get("description") or "Usage"),
                "pull_type": t.get("tool_key") or "wallet",
                "credits": 0,
                "usd": _usd(t.get("amount_usd")),
                "job_id": t.get("job_id") or "",
            })

    for uname, u in users.items():
        if not isinstance(u, dict):
            continue
        comp = str(u.get("company") or "").strip()
        if comp:
            companies.add(comp)
        email = str(u.get("email") or "")
        user_list.append({
            "username": uname, "company": comp, "email": email,
        })
        for h in (u.get("credit_usage_history") or []):
            if not isinstance(h, dict):
                continue
            raw.append({
                "used_at": str(h.get("used_at") or "")[:19],
                "company": comp,
                "username": uname,
                "email": email,
                "description": str(h.get("description") or ""),
                "pull_type": h.get("pull_type") or "",
                "credits": h.get("credits_used") or 0,
                "usd": _usd(h.get("wallet_charged_usd")),
                "job_id": h.get("job_id") or "",
            })
        source = str(u.get("billing_source") or "").strip().lower()
        if source != "company":
            _walk_wallet(
                u.get("wallet_transactions"),
                company=comp,
                default_username=uname,
                email=email,
            )

    for cname, c in companies_map.items():
        if not isinstance(c, dict):
            continue
        companies.add(str(cname))
        _walk_wallet(
            c.get("wallet_transactions"),
            company=str(cname),
            default_username="",
        )

    seen = set()
    rows = []
    for row in raw:
        key = (
            (row.get("username") or "").lower(),
            norm_usage_desc(row.get("description")),
            str(row.get("job_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        if user_f and (row.get("username") or "").lower() != user_f:
            continue
        if company_f and (row.get("company") or "").lower() != company_f:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["used_at"], reverse=True)
    return rows, sorted(companies), user_list


_TXN_KIND_LABELS = {
    "deduct": "Usage",
    "topup": "Top-up",
    "auto_reload": "Auto-reload",
    "refund": "Refund",
    "adjustment": "Adjustment",
    "monthly_access": "Monthly access",
}


def user_can_export_company_history(user) -> bool:
    """True when this user may download the shared company wallet.

    Jenna 2026-09-23: credits click downloads their own history, and
    the organization history if they have company wallet privileges.
    That privilege is `company_billing_admin` plus a company name.
    """
    if not isinstance(user, dict):
        return False
    if not bool(user.get("company_billing_admin")):
        return False
    return bool(str(user.get("company") or "").strip())


def _history_identities(username, user) -> set:
    toks = {str(username or "").strip().lower()}
    if isinstance(user, dict):
        toks.add(str(user.get("email") or "").strip().lower())
        toks.add(str(user.get("username") or "").strip().lower())
    toks.discard("")
    return toks


def _history_row(*, used_at="", kind="", company="", username="",
                 email="", description="", pull_type="", credits=0,
                 usd=0.0, balance_after="", job_id="", stripe_ref=""):
    try:
        usd_n = round(float(usd or 0), 2)
    except (TypeError, ValueError):
        usd_n = 0.0
    try:
        cred_n = int(credits or 0)
    except (TypeError, ValueError):
        cred_n = 0
    return {
        "used_at": str(used_at or "")[:19].replace("T", " "),
        "kind": kind or "Usage",
        "company": company or "",
        "username": username or "",
        "email": email or "",
        "description": description or "",
        "pull_type": pull_type or "",
        "credits": cred_n,
        "usd": usd_n,
        "balance_after": balance_after if balance_after != "" else "",
        "job_id": job_id or "",
        "stripe_ref": stripe_ref or "",
    }


def _rows_from_credit_history(hist, *, company, username, email):
    out = []
    for h in hist or []:
        if not isinstance(h, dict):
            continue
        usd = h.get("wallet_charged_usd")
        try:
            usd_n = -abs(float(usd or 0))
        except (TypeError, ValueError):
            usd_n = 0.0
        out.append(_history_row(
            used_at=h.get("used_at") or "",
            kind="Usage",
            company=company,
            username=username,
            email=email,
            description=h.get("description") or "",
            pull_type=h.get("pull_type") or h.get("wallet_tool_key") or "",
            credits=h.get("credits_used") or 0,
            usd=usd_n,
            job_id=h.get("job_id") or "",
        ))
    return out


def _rows_from_wallet_txns(txns, *, company, default_username="",
                           email="", identities=None, usage_only=False,
                           kinds=None):
    out = []
    identities = identities or set()
    for t in txns or []:
        if not isinstance(t, dict):
            continue
        kind = str(t.get("kind") or "").strip().lower()
        if kinds is not None:
            if kind not in kinds:
                continue
        elif usage_only and kind != "deduct":
            continue
        via = str(t.get("billed_via_username") or "").strip()
        if identities:
            via_l = via.lower()
            if via_l and via_l not in identities:
                continue
            if not via_l and kind == "deduct":
                continue
        try:
            usd_n = round(float(t.get("amount_usd") or 0), 2)
        except (TypeError, ValueError):
            usd_n = 0.0
        bal = t.get("balance_after_usd")
        bal_s = ""
        if bal is not None and bal != "":
            try:
                bal_s = f"{float(bal):.2f}"
            except (TypeError, ValueError):
                bal_s = ""
        out.append(_history_row(
            used_at=t.get("ts") or "",
            kind=_TXN_KIND_LABELS.get(kind, kind or "Usage"),
            company=company,
            username=via or default_username,
            email=email if (via or default_username) else "",
            description=t.get("description") or "",
            pull_type=t.get("tool") or t.get("tool_key") or "",
            credits=0,
            usd=usd_n,
            balance_after=bal_s,
            job_id=t.get("job_id") or "",
            stripe_ref=(
                str(t.get("stripe_ref") or "").strip()
                or str(t.get("stripe_payment_intent") or "").strip()
                or str(t.get("stripe_checkout_session") or "").strip()
            ),
        ))
    return out



_MODAL_SKIP_TOOLS = frozenset({"prometheus"})


def usage_row_usd(row) -> float:
    """Dollar amount for a credits-modal row.

    Prefer the wallet amount that was actually charged or refunded.
    Fall back to leftover-credit conversion only when no dollar
    field is present. Refunds stay negative.
    """
    if not isinstance(row, dict):
        return 0.0
    for key in ("amount_usd", "wallet_charged_usd"):
        if row.get(key) is None or row.get(key) == "":
            continue
        try:
            return round(float(row[key]), 2)
        except (TypeError, ValueError):
            continue
    try:
        return round(float(row.get("credits_used") or 0) * LEGACY_CREDIT_USD, 2)
    except (TypeError, ValueError):
        return 0.0


def credit_usage_for_modal(user, username, users_data=None):
    """History plus this user's company-wallet deducts and refunds.

    Prometheus sessions stay off this list. They have their own
    Questions row. Charges are positive. Refunds are negative so
    Spend to date nets them out.
    """
    user = user if isinstance(user, dict) else {}
    users_data = users_data if isinstance(users_data, dict) else {}
    username = str(username or "").strip()
    identities = _history_identities(username, user)
    rows = []
    seen_job = set()
    seen_refund_day = set()

    def _is_refund(row, usd):
        ptype = str(row.get("pull_type") or "").strip().lower()
        return ptype == "refund" or usd < 0

    def _add(row):
        if not isinstance(row, dict):
            return
        usd = usage_row_usd(row)
        jid = str(row.get("job_id") or "").strip()
        refund = _is_refund(row, usd)
        tag = "refund" if refund else "charge"
        day = str(row.get("used_at") or row.get("ts") or "")[:10]
        if jid:
            k = (jid, tag)
            if k in seen_job:
                return
            seen_job.add(k)
        elif refund:
            rk = (day, round(abs(usd), 2))
            if rk in seen_refund_day:
                return
            seen_refund_day.add(rk)
        out = dict(row)
        out["usd"] = usd
        if out.get("amount_usd") is None:
            out["amount_usd"] = usd
        rows.append(out)

    for h in user.get("credit_usage_history") or []:
        _add(h)

    subject, kind, _key = resolve_billing_subject(user, users_data)
    txns = []
    if isinstance(subject, dict):
        txns.extend(subject.get("wallet_transactions") or [])
    if kind != "company":
        txns = list(user.get("wallet_transactions") or []) + txns
    for t in txns:
        if not isinstance(t, dict):
            continue
        tk = str(t.get("kind") or "").strip().lower()
        if tk not in ("deduct", "refund"):
            continue
        tool = str(t.get("tool") or t.get("tool_key") or "").strip().lower()
        if tool in _MODAL_SKIP_TOOLS:
            continue
        via = str(t.get("billed_via_username") or "").strip().lower()
        if identities and via and via not in identities:
            continue
        if identities and (not via) and tk == "deduct":
            continue
        try:
            raw = float(t.get("amount_usd") or 0)
        except (TypeError, ValueError):
            raw = 0.0
        usd = -raw
        _add({
            "used_at": str(t.get("ts") or ""),
            "description": t.get("description") or "",
            "job_id": t.get("job_id") or "",
            "pull_type": "refund" if tk == "refund" else (
                t.get("tool") or t.get("tool_key") or "wallet"),
            "credits_used": 0,
            "amount_usd": usd,
            "wallet_charged_usd": usd,
        })

    rows.sort(key=lambda r: str(r.get("used_at") or ""), reverse=True)
    spend = round(sum(float(r.get("usd") or 0) for r in rows), 2)
    return rows, spend


def collect_transaction_history(data, username="", scope="self",
                                company_name=""):
    """Full downloadable ledger for one person or one company wallet.

    `self` is that user's credit log plus any company usage billed
    through them. Top-ups stay on the company file.

    `company` is every wallet movement on the shared company record
    plus every member's credit log. Caller must have already checked
    `user_can_export_company_history` (or admin).
    """
    data = data or {}
    users = data.get("users") or {}
    companies_map = data.get("companies") or {}
    scope = str(scope or "self").strip().lower()
    username = str(username or "").strip()
    user = users.get(username) if username else {}
    if not isinstance(user, dict):
        user = {}
    company = (str(company_name or "").strip()
               or str(user.get("company") or "").strip())
    rows = []
    if scope == "company":
        if not company:
            return []
        crec = companies_map.get(company)
        if isinstance(crec, dict):
            rows.extend(_rows_from_wallet_txns(
                crec.get("wallet_transactions"),
                company=company,
            ))
        for uname, u in company_members(company, data):
            rows.extend(_rows_from_credit_history(
                u.get("credit_usage_history"),
                company=company,
                username=uname,
                email=str(u.get("email") or ""),
            ))
    else:
        email = str(user.get("email") or "")
        company = str(user.get("company") or "").strip()
        rows.extend(_rows_from_credit_history(
            user.get("credit_usage_history"),
            company=company,
            username=username,
            email=email,
        ))
        source = str(user.get("billing_source") or "").strip().lower()
        if source != "company":
            rows.extend(_rows_from_wallet_txns(
                user.get("wallet_transactions"),
                company=company,
                default_username=username,
                email=email,
            ))
        elif company:
            crec = companies_map.get(company)
            if isinstance(crec, dict):
                rows.extend(_rows_from_wallet_txns(
                    crec.get("wallet_transactions"),
                    company=company,
                    default_username=username,
                    email=email,
                    identities=_history_identities(username, user),
                    kinds=("deduct", "refund"),
                ))
    seen = set()
    out = []
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind in ("Top-up", "Auto-reload", "Refund", "Adjustment"):
            key = (
                kind,
                row.get("used_at") or "",
                str(row.get("usd") or ""),
                str(row.get("stripe_ref") or row.get("job_id") or ""),
                norm_usage_desc(row.get("description")),
            )
        else:
            key = (
                (row.get("username") or "").lower(),
                norm_usage_desc(row.get("description")),
                str(row.get("job_id") or ""),
                kind,
            )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda r: r.get("used_at") or "", reverse=True)
    return out


def lookup_user(users_data: dict, key: str):
    """Resolve a user record from a subject_key that may be the
    users.json dict key, an email, or a username field.

    Login keys are short usernames (`vsanders`). `_user_key` used to
    return email first, so payment links and Stripe metadata minted
    before 2026-09-15 stored `vernon.sanders327@gmail.com`. Checkout
    then did users.get(email) and 404'd. Accept all three spellings
    and return the canonical dict key.

    Returns (canonical_key, user_dict) or (None, None).
    """
    users = (users_data or {}).get("users") if isinstance(
        users_data, dict) else None
    raw = str(key or "").strip()
    if not raw or not isinstance(users, dict):
        return None, None
    rec = users.get(raw)
    if isinstance(rec, dict):
        return raw, rec
    fold = raw.lower()
    email_hit = None
    for k, u in users.items():
        if not isinstance(u, dict):
            continue
        if str(k).strip().lower() == fold:
            return k, u
        if str(u.get("username") or "").strip().lower() == fold:
            return k, u
        if str(u.get("email") or "").strip().lower() == fold:
            email_hit = (k, u)
    if email_hit:
        return email_hit
    return None, None


def _user_key(user: dict, users_data: dict = None) -> str:
    """Return the key in users_data['users'] for this record.

    The dict key (login username) is the identity. Email is only a
    fallback when the record cannot be found in the map. Never prefer
    email over the live key: every dashboard user is keyed by a short
    username and a different email (vsanders /
    vernon.sanders327@gmail.com, 2026-09-15).
    """
    if not isinstance(user, dict):
        return ""
    users = (users_data or {}).get("users") if isinstance(
        users_data, dict) else None
    if isinstance(users, dict):
        for k, u in users.items():
            if u is user:
                return str(k)
        email = str(user.get("email") or "").strip()
        uname_field = str(user.get("username") or "").strip()
        for candidate in (uname_field, email):
            if not candidate:
                continue
            found_key, _rec = lookup_user({"users": users}, candidate)
            if found_key:
                return found_key
    return str(user.get("username") or user.get("email") or "")


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


def user_wallet_covers_pull(user: dict, users_data: dict,
                            pull_type: str = None,
                            addon_cuts: int = 0) -> bool:
    """True when the resolved billing subject (personal or company
    wallet) can pay for this pull on dollars, not leftover credits.

    Dashboard approve and the Partner API still preflight through
    `has_credits_for`, which used to look only at the credits /
    credit_pool integers. A paying Kartel user with $4,100 on the
    company wallet and 0 credits was blocked even though
    consume_credit would have taken the dollars. This is the
    wallet-side half of that preflight.
    """
    try:
        if not isinstance(user, dict):
            return False
        subject, kind, _key = resolve_billing_subject(user, users_data or {})
        if not is_paying_customer(subject):
            return False
        tool_key = pull_type_to_tool_key(pull_type) if pull_type else ""
        if not tool_key:
            tool_key = "profile_iq_build"
        usd, mode = should_charge_wallet(subject, tool_key,
                                         addon_cuts=addon_cuts)
        if mode != "wallet" or usd <= 0:
            return False
        if kind == "company":
            allowed, _why, _scope = user_can_spend_from_company(
                user, tool_key, subject)
            if not allowed:
                return False
        ok, _reason = wallet_can_absorb(subject, usd)
        return bool(ok)
    except Exception:
        return False


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
    "RETIRED_TOOL_KEYS",
    "load_pricing", "save_pricing",
    "tool_price_usd", "subject_tool_price_usd",
    "PROFILE_BUILD_TOOL_KEYS",
    "LEGACY_CREDIT_USD", "leftover_credits_to_usd",
    "tool_monthly_usd", "prometheus_markup",
    "metered_answer_usd",
    "compute_user_monthly_charge", "compute_company_monthly_charge",
    "top_up_pack_sizes", "top_up_min_custom",
    "EXCEL_SPORTS_COMPANY", "EXCEL_TOP_UP_MIN_USD",
    "is_excel_sports_subject",
    "OPENING_TOPUP_MIN_USD", "opening_topup_usd",
    "requires_card_to_view", "dashboard_view_locked",
    "opening_funding_unmet", "access_window_expired",
    "has_complimentary_grant", "complimentary_view_unlocked",
    "opening_checkout_allowed",
    "wallet_balance", "wallet_stats",
    "is_paying_customer", "is_unlimited", "admits_wallet_ui",
    "billing_mode", "billing_currency", "money_symbol", "format_money",
    "apply_auto_reload_preference",
    "parse_auto_reload_flag",
    "auto_reload_threshold", "auto_reload_amount",
    "monthly_invoice_limit", "has_card_on_file",
    "existing_wallet_deduct",
    "usage_row_usd", "credit_usage_for_modal",
    "apply_wallet_deduct", "apply_wallet_topup", "apply_wallet_refund",
    "should_charge_wallet", "wallet_can_absorb",     "needs_auto_reload",
    "claim_topup_notice",
    "try_auto_reload",
    "add_custom_tool", "remove_custom_tool", "CustomToolError",
    "hide_builtin_tool", "unhide_builtin_tool", "hidden_builtin_tools",
    "resolve_billing_subject", "ensure_company_record",
    "WBD_COMPANY_NAME", "is_wbd_company", "ensure_wbd_shared_wallet",
    "apply_prometheus_only_seat", "attach_wbd_seat",
    "is_internal_staff_seat", "has_full_profile_catalog",
    "profile_iq_module_enabled",
    "is_paid_only_plan",
    "PUBLIC_SIGNUP_SOURCE", "is_public_signup_seat",
    "pays_retail_for_library_match",
    "subject_profile_pull_usd",
    "charges_profile_for_library_match",
    "fold_profile_label", "subject_from_usage_description",
    "profile_labels_match", "paid_profile_keys", "record_paid_profile",
    "paid_profile_tokens", "already_paid_for_profile",
    "already_owns_paid_run",
    "company_wants_paid_only", "mark_company_paid_only",
    "attach_paid_only_seat", "attach_paid_only_company",
    "apply_full_dashboard_seat", "mark_company_full_access",
    "detach_paid_only_seat", "detach_paid_only_company",
    "company_paid_runs", "grant_company_paid_runs",
    "inherit_company_paid_runs",
    "inherit_company_explicit_journey_iq",
    "journey_iq_run_access",
    "company_teammate_usernames",
    "admin_billing_row_for_user",
    "norm_usage_desc", "collect_usage_ledger_rows",
    "user_can_export_company_history", "collect_transaction_history",
    "lookup_user", "company_billing_admins",
    "company_members", "iter_paying_subjects",
    "user_can_spend_from_company", "user_spend_scope_summary",
    "user_wallet_covers_pull",
    "SpendNotAuthorizedError",
]
