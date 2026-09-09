"""Flask blueprint for wallet + Stripe billing routes (2026-09-08).

Mounted from app.py with a single `app.register_blueprint(billing_bp)`
call so the app.py diff stays surgical.

Routes:

  Public (user-facing, requires login):
    GET  /api/wallet/state                   - balance + txns + billing mode
    POST /api/wallet/create-checkout-session - prepay top-up via Stripe Checkout
    GET  /wallet/success                     - Stripe redirects here on completion
    GET  /wallet/cancel                      - Stripe redirects here on cancel

  Admin (requires super_admin):
    GET  /api/admin/pricing                    - current per-tool USD prices
    POST /api/admin/pricing                    - save per-tool USD prices
    POST /api/admin/user/<u>/billing/setup-intent - card capture prep
    POST /api/admin/user/<u>/billing/attach-card  - persist saved card
    POST /api/admin/user/<u>/billing/detach-card  - remove saved card
    POST /api/admin/user/<u>/billing/charge        - custom off-session charge
    POST /api/admin/user/<u>/billing/topup         - manual admin credit
    POST /api/admin/user/<u>/billing/refund        - refund a prior charge
    POST /api/admin/user/<u>/billing/mode          - set auto_reload / monthly
    POST /api/admin/user/<u>/billing/paying-flag   - toggle paying_customer

  Stripe (unauthenticated, signature-verified):
    POST /api/stripe/webhook  - checkout.session.completed, payment_intent.*,
                                charge.refunded

The blueprint imports helpers lazily from app.py to avoid circular
imports at module load. It relies on:

  - app.get_current_user()          - session -> user dict
  - app.load_users()                - load users.json
  - app._users_cas_mutate(fn)       - atomic mutation
  - wallet.py                        - balance math + txn logging
  - billing.py                       - Stripe SDK wrapper
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from flask import (Blueprint, jsonify, redirect, render_template,
                   request, session, url_for)


billing_bp = Blueprint("billing", __name__)


# ---------------------------------------------------------------------------
# Auth helpers (thin wrappers over app.py's session model)
# ---------------------------------------------------------------------------

def _current_user_record():
    """(username, user_dict) or (None, None) when not logged in."""
    from app import load_users  # type: ignore
    uname = session.get("username")
    if not uname:
        return None, None
    data = load_users()
    u = (data.get("users") or {}).get(uname)
    return uname, u


def _current_user_context():
    """Same as _current_user_record but also returns the full
    users_data dict so callers that need the companies map (for
    company-shared wallet routing) don't have to re-load it.

    Returns (uname, user_dict, users_data) or (None, None, None).
    """
    from app import load_users  # type: ignore
    uname = session.get("username")
    if not uname:
        return None, None, None
    data = load_users()
    u = (data.get("users") or {}).get(uname)
    return uname, u, data


def _resolve_caller_billing_subject():
    """Resolve which record holds the wallet + card for the current
    caller. Central helper for every /api/wallet/* endpoint.

    Returns a dict:
      {
        "uname": str,               # logged-in user
        "user": dict,               # logged-in user record
        "users_data": dict,         # full users.json snapshot
        "subject": dict,            # user OR company record (whichever
                                    # owns the wallet)
        "subject_kind": "user"|"company",
        "subject_key": str,         # username OR company name
        "billed_via_company": bool, # True when routed to a company
        "company_name": str,        # empty unless routed to a company
        "viewer_is_billing_admin": bool,  # True when the caller is the
                                    # subject itself OR (for a company
                                    # subject) the caller has
                                    # company_billing_admin=True
      }

    Never raises. When routing fails (company field points to a
    non-existent company), falls back to the individual user record
    so the wallet UI still works.
    """
    uname, u, data = _current_user_context()
    if not uname or not u or not data:
        return None
    import wallet  # type: ignore
    subject, subject_kind, subject_key = wallet.resolve_billing_subject(
        u, data)
    billed_via_company = (subject_kind == "company")
    company_name = subject_key if billed_via_company else ""
    if billed_via_company:
        viewer_is_admin = bool(u.get("company_billing_admin"))
    else:
        viewer_is_admin = True  # own wallet -> can manage themselves
    return {
        "uname": uname,
        "user": u,
        "users_data": data,
        "subject": subject,
        "subject_kind": subject_kind,
        "subject_key": subject_key,
        "billed_via_company": billed_via_company,
        "company_name": company_name,
        "viewer_is_billing_admin": viewer_is_admin,
    }


def _require_login():
    """Return (username, user_dict) or a (jsonify_response, 401) tuple."""
    uname, u = _current_user_record()
    if not uname or not u:
        return None, None, (jsonify({"error": "not_logged_in"}), 401)
    return uname, u, None


def _require_super_admin():
    """Return (username, user_dict) or a (jsonify_response, 403) tuple."""
    uname, u, err = _require_login()
    if err:
        return None, None, err
    if str(u.get("role") or "").strip().lower() != "super_admin":
        return None, None, (jsonify({"error": "not_authorized"}), 403)
    return uname, u, None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _dashboard_base_url() -> str:
    """Return the public URL of the running dashboard (for Stripe
    success/cancel redirects). Prefers request.host_url so it works
    on Render, dev, and localhost identically."""
    base = str(request.host_url or "").rstrip("/")
    return base or "https://dashboard.crosswalknyc.com"


def _get_paying_flag(u: dict) -> bool:
    return bool((u or {}).get("paying_customer"))


def _fmt_ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Wallet state (public, requires login)
# ---------------------------------------------------------------------------

@billing_bp.route("/api/wallet/state", methods=["GET"])
def wallet_state():
    """Return the caller's wallet balance, billing mode, saved-card
    display metadata, transaction history, and the pricing snapshot
    the UI needs to render the Add Funds packs.

    Company-shared wallet (Jenna 2026-09-09): if the caller has
    billing_source='company' AND their company record exists, this
    payload reports the COMPANY's wallet - balance, billing mode,
    transactions, card, spend stats. The company's `paying_customer`
    flag flips the UI on. `viewer_is_billing_admin=false` for regular
    members hides top-up + card management client-side.

    Non-paying users (individual OR unrouted) get a minimal payload
    (balance=0, ui_visible=false) so the front-end can decide whether
    to show the wallet at all.
    """
    ctx = _resolve_caller_billing_subject()
    if not ctx:
        return jsonify({"error": "not_logged_in"}), 401
    uname = ctx["uname"]
    u = ctx["user"]
    subject = ctx["subject"]
    import wallet  # type: ignore
    import billing  # type: ignore

    pricing = wallet.load_pricing()
    # Wallet UI visibility: user's own role/paying flag OR company's
    # paying flag when routed. Company routing turns the wallet UI on
    # for every member so they can see balance and (if admin) top up.
    ui_visible = wallet.admits_wallet_ui(u)
    if ctx["billed_via_company"]:
        ui_visible = ui_visible or bool(subject.get("paying_customer"))

    _first = str(u.get("first_name") or "").strip()
    _last = str(u.get("last_name") or "").strip()
    _display = " ".join(x for x in (_first, _last) if x) or uname
    payload = {
        "username": uname,
        "display_name": _display,
        "email": str(u.get("email") or ""),
        "role": str(u.get("role") or "user"),
        "ui_visible": ui_visible,
        # Subject fields: report the wallet-holding record's state
        # (user OR company). Regular access flags stay on the user.
        "paying_customer": bool(subject.get("paying_customer")),
        "unlimited": bool(subject.get("unlimited")) if ctx[
            "billed_via_company"] else wallet.is_unlimited(u),
        "wallet_balance_usd": wallet.wallet_balance(subject),
        "lifetime_topups_usd": float(subject.get(
            "wallet_lifetime_topups_usd", 0.0) or 0.0),
        "lifetime_spend_usd": float(subject.get(
            "wallet_lifetime_spend_usd", 0.0) or 0.0),
        "billing_mode": wallet.billing_mode(subject),
        "auto_reload_threshold_usd":
            wallet.auto_reload_threshold(subject),
        "auto_reload_amount_usd": wallet.auto_reload_amount(subject),
        "monthly_invoice_limit_usd":
            wallet.monthly_invoice_limit(subject),
        "has_card_on_file": wallet.has_card_on_file(subject),
        "card_display": {
            "last4": str(subject.get("stripe_payment_method_last4", "")),
            "brand": str(subject.get("stripe_payment_method_brand", "")),
        },
        "transactions": list(subject.get(
            "wallet_transactions", []))[:100],
        "top_up_packs_usd": wallet.top_up_pack_sizes(),
        "top_up_min_custom_usd": wallet.top_up_min_custom(),
        "stats": wallet.wallet_stats(subject),
        "auto_reload_defaults": (pricing.get("auto_reload_defaults")
                                 or {"threshold_usd": 500.0,
                                     "amount_usd": 1000.0}),
        "stripe_enabled": billing.is_enabled(),
        "stripe_publishable_key": billing.publishable_key(),
        # Company-shared wallet context (Jenna 2026-09-09).
        "billed_via_company": ctx["billed_via_company"],
        "company_name": ctx["company_name"],
        "viewer_is_billing_admin": ctx["viewer_is_billing_admin"],
    }
    return jsonify(payload)


# ---------------------------------------------------------------------------
# User self-serve auto-reload prefs (Jenna 2026-09-09)
# ---------------------------------------------------------------------------

@billing_bp.route("/api/wallet/auto-reload", methods=["POST"])
def wallet_auto_reload():
    """User saves their own auto-reload preferences. Body:

      {
        "enabled": true | false,
        "threshold_usd": 500,
        "amount_usd": 1000
      }

    Setting `enabled: true` requires a card on file (added via
    /api/wallet/setup-intent + /api/wallet/attach-card). Users can
    only choose between prepay_only and auto_reload; monthly_invoice
    stays admin-only per no-external-overrides.mdc (external users
    can't grant themselves a credit line).

    Company-shared wallet: when the caller routes through a company,
    this mutates the COMPANY's auto-reload settings. Only company
    billing admins may call this route in that case.
    """
    ctx = _resolve_caller_billing_subject()
    err = _require_wallet_write_access(ctx)
    if err:
        return err
    import wallet  # type: ignore
    subject = ctx["subject"]

    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    try:
        threshold = float(body.get("threshold_usd") or 500)
        amount = float(body.get("amount_usd") or 1000)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amounts"}), 400

    # Bounds. Threshold 0-10K, amount 100-10K, and threshold < amount
    # (nonsense to top up by less than the threshold — you'd just
    # trip again on the very next pull).
    if threshold < 0 or threshold > 10_000:
        return jsonify({"error": "threshold_out_of_range",
                        "min": 0, "max": 10000}), 400
    if amount < 100 or amount > 10_000:
        return jsonify({"error": "amount_out_of_range",
                        "min": 100, "max": 10000}), 400

    if enabled and not wallet.has_card_on_file(subject):
        return jsonify({"error": "no_card_on_file"}), 400

    def _apply(rec):
        rec["billing_mode"] = "auto_reload" if enabled else "prepay_only"
        rec["auto_reload_threshold_usd"] = float(threshold)
        rec["auto_reload_amount_usd"] = float(amount)
        return True

    ok, _ = _mutate_billing_subject(
        ctx["subject_kind"], ctx["subject_key"], _apply)
    if not ok:
        return jsonify({"error": "save_failed"}), 500
    return jsonify({
        "success": True,
        "billing_mode": "auto_reload" if enabled else "prepay_only",
        "auto_reload_threshold_usd": float(threshold),
        "auto_reload_amount_usd": float(amount),
    })


# ---------------------------------------------------------------------------
# User self-serve card management (mirrors admin card routes)
# ---------------------------------------------------------------------------

@billing_bp.route("/api/wallet/setup-intent", methods=["POST"])
def wallet_setup_intent():
    """User clicks 'Add card' on /wallet -> create a Stripe Customer
    if needed, then a SetupIntent, and return the client_secret to
    embed in the Stripe Elements iframe.

    Company-shared wallet: routes to the COMPANY's Stripe Customer
    when the caller is a company billing admin. Regular members are
    rejected."""
    ctx = _resolve_caller_billing_subject()
    err = _require_wallet_write_access(ctx)
    if err:
        return err
    import billing  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503
    subject = ctx["subject"]

    # Customer identity: use company name/email surrogate when
    # routing to a company. Company records rarely carry an email so
    # fall back to the acting user's email (Stripe just wants a
    # non-empty identifier for the Customer record).
    if ctx["billed_via_company"]:
        cust_ident = ctx["company_name"]
        cust_email = str(ctx["user"].get("email") or "")
        cust_name = ctx["company_name"]
    else:
        cust_ident = ctx["uname"]
        cust_email = str(ctx["user"].get("email") or "")
        cust_name = (
            f"{ctx['user'].get('first_name', '')} "
            f"{ctx['user'].get('last_name', '')}").strip() or ctx["uname"]

    try:
        cus_id = billing.ensure_customer(
            cust_ident,
            email=cust_email,
            name=cust_name,
            existing_customer_id=str(subject.get("stripe_customer_id") or ""),
        )
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502
    if cus_id and cus_id != str(subject.get("stripe_customer_id") or ""):
        _persist_subject_customer_id(
            ctx["subject_kind"], ctx["subject_key"], cus_id)

    try:
        si = billing.create_setup_intent(cus_id)
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({
        "setup_intent_id": si["id"],
        "client_secret": si["client_secret"],
        "customer_id": cus_id,
        "publishable_key": billing.publishable_key(),
    })


@billing_bp.route("/api/wallet/attach-card", methods=["POST"])
def wallet_attach_card():
    """User confirmed the SetupIntent client-side. Attach the
    PaymentMethod to their customer + persist display metadata.
    Body: {"payment_method_id": "pm_..."}.

    Company-shared wallet: attaches to the COMPANY's Stripe Customer
    when the caller is a company billing admin."""
    ctx = _resolve_caller_billing_subject()
    err = _require_wallet_write_access(ctx)
    if err:
        return err
    import billing  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503
    subject = ctx["subject"]

    body = request.get_json(silent=True) or {}
    pm_id = str(body.get("payment_method_id") or "").strip()
    if not pm_id:
        return jsonify({"error": "missing_payment_method_id"}), 400
    cus_id = str(subject.get("stripe_customer_id") or "")
    if not cus_id:
        return jsonify({"error": "no_stripe_customer"}), 400

    try:
        display = billing.attach_payment_method(cus_id, pm_id)
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    def _apply(rec):
        rec["stripe_payment_method_id"] = display["id"]
        rec["stripe_payment_method_last4"] = display["last4"]
        rec["stripe_payment_method_brand"] = display["brand"]
        return True

    ok, _ = _mutate_billing_subject(
        ctx["subject_kind"], ctx["subject_key"], _apply)
    return jsonify({"success": ok, "card_display": display})


@billing_bp.route("/api/wallet/detach-card", methods=["POST"])
def wallet_detach_card():
    """User removes their card on file. Automatically downshifts
    billing_mode from auto_reload to prepay_only so the wallet
    doesn't sit in a broken 'auto-reload with no card' state.

    Company-shared wallet: routes to the COMPANY when the caller is
    a company billing admin."""
    ctx = _resolve_caller_billing_subject()
    err = _require_wallet_write_access(ctx)
    if err:
        return err
    import billing  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503
    subject = ctx["subject"]

    pm_id = str(subject.get("stripe_payment_method_id") or "")
    if pm_id:
        try:
            billing.detach_payment_method(pm_id)
        except billing.BillingError:
            # Best effort; still remove locally so the wallet page
            # matches what the user sees.
            pass

    def _apply(rec):
        rec["stripe_payment_method_id"] = ""
        rec["stripe_payment_method_last4"] = ""
        rec["stripe_payment_method_brand"] = ""
        # If they were on auto-reload, drop to prepay_only.
        if str(rec.get("billing_mode") or "").strip() == "auto_reload":
            rec["billing_mode"] = "prepay_only"
        return True

    ok, _ = _mutate_billing_subject(
        ctx["subject_kind"], ctx["subject_key"], _apply)
    return jsonify({"success": ok})


# ---------------------------------------------------------------------------
# Prepay top-up: create a Checkout Session (public, requires login)
# ---------------------------------------------------------------------------

@billing_bp.route("/api/wallet/create-checkout-session", methods=["POST"])
def create_checkout_session():
    """Body: {"amount_usd": 500}.

    Returns {"session_id": "...", "url": "..."} that the caller
    redirects the browser to (Stripe hosts the payment form). On
    success Stripe redirects to /wallet/success and fires the
    checkout.session.completed webhook, which credits the wallet.

    Company-shared wallet (Jenna 2026-09-09): only company billing
    admins may top up a company wallet. The webhook credits the
    COMPANY record via subject_kind + subject_key metadata.
    """
    ctx = _resolve_caller_billing_subject()
    err = _require_wallet_write_access(ctx)
    if err:
        return err
    import wallet  # type: ignore
    import billing  # type: ignore

    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    subject = ctx["subject"]

    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    try:
        amt = float(body.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400
    if amt < wallet.top_up_min_custom():
        return jsonify({
            "error": "below_minimum",
            "min_usd": wallet.top_up_min_custom(),
        }), 400
    if amt > 100_000:
        return jsonify({"error": "above_maximum"}), 400

    # Customer identity: company routes to company; user routes to user.
    if ctx["billed_via_company"]:
        cust_ident = ctx["company_name"]
        cust_email = str(ctx["user"].get("email") or "")
        cust_name = ctx["company_name"]
    else:
        cust_ident = ctx["uname"]
        cust_email = str(ctx["user"].get("email") or "")
        cust_name = (
            f"{ctx['user'].get('first_name', '')} "
            f"{ctx['user'].get('last_name', '')}").strip() or ctx["uname"]

    try:
        cus_id = billing.ensure_customer(
            cust_ident,
            email=cust_email,
            name=cust_name,
            existing_customer_id=str(subject.get("stripe_customer_id") or ""),
        )
    except billing.BillingDisabled:
        return jsonify({"error": "billing_not_configured"}), 503
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    if cus_id and cus_id != str(subject.get("stripe_customer_id") or ""):
        _persist_subject_customer_id(
            ctx["subject_kind"], ctx["subject_key"], cus_id)

    base = _dashboard_base_url()
    success_url = (f"{base}/wallet/success"
                   f"?session_id={{CHECKOUT_SESSION_ID}}")
    cancel_url = f"{base}/wallet/cancel"

    try:
        sess = billing.create_checkout_session(
            customer_id=cus_id,
            amount_usd=amt,
            success_url=success_url,
            cancel_url=cancel_url,
            # Pass the acting user as the dashboard_username so
            # audit/print statements remain identifiable, but attach
            # subject_kind + subject_key metadata so the webhook
            # credits the right record.
            username=ctx["uname"],
            metadata={
                "subject_kind": ctx["subject_kind"],
                "subject_key": ctx["subject_key"],
                "billed_via_username": ctx["uname"],
            },
        )
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify(sess)


@billing_bp.route("/wallet", methods=["GET"])
def wallet_page():
    """Render the standalone Wallet page (Buy Credits UI). Kept as a
    separate template so we don't have to splice the 10MB index.html
    (see index-html-safety.mdc). The page is a static shell; it
    fetches /api/wallet/state on load to render the balance, packs,
    and transaction list.
    """
    _, _, err = _require_login()
    if err:
        # Redirect anonymous visitors to the dashboard login instead
        # of a JSON 401 so browser links behave as expected.
        return redirect(url_for("index"))
    return render_template("wallet.html")


@billing_bp.route("/wallet/success", methods=["GET"])
def wallet_success():
    """Stripe redirects here on completed Checkout. The wallet is
    credited by the webhook (not this route). We bounce the user
    back to /wallet with a banner query param so the standalone
    wallet page can render the success confirmation."""
    return redirect(url_for("billing.wallet_page") +
                    "?wallet=topup_success")


@billing_bp.route("/wallet/cancel", methods=["GET"])
def wallet_cancel():
    return redirect(url_for("billing.wallet_page") +
                    "?wallet=topup_cancelled")


# ---------------------------------------------------------------------------
# Pricing (admin-only)
# ---------------------------------------------------------------------------

@billing_bp.route("/api/admin/pricing", methods=["GET"])
def admin_pricing_get():
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    p = wallet.load_pricing(force_reload=True)
    # Return hidden built-ins alongside the visible catalog so the UI
    # can render a "Show N hidden" toggle. is_hidden on each row lets
    # the frontend decide default visibility.
    catalog = wallet.module_catalog(include_hidden=True)
    # Bundle a UI-friendly `tools` block grouped by section, plus
    # `sections` metadata so the admin panel can render section
    # headers without hardcoding names.
    tools = {}
    sections = {
        "modules":      {"label": "Modules",      "order": 1},
        "rankers":      {"label": "Rankers",      "order": 2},
        "api":          {"label": "Partner API",  "order": 3},
        "subscription": {"label": "Subscription", "order": 4},
        "custom":       {"label": "Custom",       "order": 5},
        "extras":       {"label": "Other",        "order": 99},
    }
    for row in catalog:
        tools[row["tool_key"]] = {
            "display_name": row["display_name"],
            "section":      row["section"],
            "credits":      row["credits"],
            "usd":          row["usd"],
            "default_usd":  row["default_usd"],
            "monthly_usd":  row.get("monthly_usd", 0.0),
            "default_monthly_usd":
                            row.get("default_monthly_usd", 0.0),
            "access_flag":  row["access_flag"],
            "is_custom":    bool(row.get("is_custom", False)),
            "is_builtin":   bool(row.get("is_builtin", False)),
            "is_hidden":    bool(row.get("is_hidden", False)),
        }
    resp = dict(p)
    resp["tools"] = tools
    resp["sections"] = sections
    resp["hidden_tools"] = list(p.get("hidden_tools") or [])
    resp["prometheus_markup"] = float(
        p.get("prometheus_markup_multiplier", 2.10))
    return jsonify(resp)


@billing_bp.route("/api/admin/pricing", methods=["POST"])
def admin_pricing_set():
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return jsonify({"error": "invalid_body"}), 400

    # Accept EITHER the new nested `tools` shape (from the admin UI
    # after 2026-09-09) OR the legacy `per_tool_usd` shape (any
    # older callers). Merge into normalized per_tool_usd + per_tool_
    # monthly_usd dicts. The nested shape carries both prices per
    # tool: {tool_key: {credits, usd, monthly_usd}}.
    tools = body.get("tools") if isinstance(body.get("tools"), dict) else {}
    if tools:
        merged_per_tool = dict(body.get("per_tool_usd") or {})
        merged_monthly = dict(body.get("per_tool_monthly_usd") or {})
        for tool_key, spec in tools.items():
            if not isinstance(spec, dict):
                continue
            try:
                merged_per_tool[str(tool_key)] = float(spec.get("usd", 0) or 0)
            except (TypeError, ValueError):
                pass
            # Only overwrite monthly if the field is present so a
            # legacy caller that omits monthly_usd keeps the existing
            # value.
            if "monthly_usd" in spec:
                try:
                    merged_monthly[str(tool_key)] = float(
                        spec.get("monthly_usd", 0) or 0)
                except (TypeError, ValueError):
                    pass
        body["per_tool_usd"] = merged_per_tool
        body["per_tool_monthly_usd"] = merged_monthly

    saved = wallet.save_pricing(body)
    return jsonify({"success": True, "pricing": saved})


# ---------------------------------------------------------------------------
# Custom tools (super-admin CRUD - Jenna 2026-09-09: 'allow super admins
# the ability to add new products or delete remove from this page')
# ---------------------------------------------------------------------------
#
# Design:
#   - Built-in tools (MODULE_CATALOG in wallet.py) are ALWAYS present.
#     They can be re-priced but never deleted; the admin UI hides the
#     Delete button on those rows.
#   - Custom tools live in pricing.json:custom_tools[]. They render
#     alongside the built-ins in the pricing table. Admin can edit
#     display name / section / price and remove them.
#   - When a paying customer runs a tool whose pull_type slugifies to
#     a custom tool key, the wallet fallback picks up the admin-set
#     price via the normalized-fallback branch in pull_type_to_tool_key
#     (already in place).

@billing_bp.route("/api/admin/pricing/tools", methods=["POST"])
def admin_pricing_tool_add():
    """Add a new custom tool to the pricing catalog.

    Body: {
        tool_key: str (required, gets slugified to snake_case),
        display_name: str (required),
        section: str (optional, defaults 'custom'),
        credits: int (optional, defaults 0),
        usd: float (optional, defaults 0.0),
        access_flag: str (optional; only useful if you're wiring
                        access gating for this tool in app.py)
    }
    """
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return jsonify({"error": "invalid_body"}), 400
    try:
        entry = wallet.add_custom_tool(
            tool_key=body.get("tool_key"),
            display_name=body.get("display_name"),
            section=body.get("section") or "custom",
            credits=body.get("credits") or 0,
            usd=body.get("usd") or 0,
            monthly_usd=body.get("monthly_usd") or 0,
            access_flag=body.get("access_flag") or None,
        )
    except wallet.CustomToolError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[billing] add_custom_tool failed: {e}")
        return jsonify({"error": "could not add tool"}), 500
    return jsonify({"success": True, "tool": entry})


@billing_bp.route(
    "/api/admin/pricing/tools/<tool_key>/delete", methods=["POST"])
def admin_pricing_tool_delete(tool_key):
    """Remove a custom tool from the pricing catalog. Built-in tools
    (MODULE_CATALOG entries) are refused with a 400 - they are code-
    defined and always present. Use /hide instead for built-ins."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    try:
        result = wallet.remove_custom_tool(tool_key)
    except wallet.CustomToolError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[billing] remove_custom_tool failed: {e}")
        return jsonify({"error": "could not remove tool"}), 500
    return jsonify({"success": True, **result})


@billing_bp.route(
    "/api/admin/pricing/tools/<tool_key>/hide", methods=["POST"])
def admin_pricing_tool_hide(tool_key):
    """Soft-hide a built-in tool from the admin pricing panel.

    Jenna 2026-09-09: 'needs to be a way to delete from there too'.
    Custom tools use /delete (hard delete). Built-in tools use /hide
    (soft) because their MODULE_CATALOG code still routes real billing
    to them - a hard delete would leak charges. Hiding stashes the
    tool_key in pricing.json:hidden_tools; the admin panel omits it
    from default listings but the price still applies when the
    tool's pull_type fires.

    Idempotent - hiding an already-hidden tool is a no-op success."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    try:
        result = wallet.hide_builtin_tool(tool_key)
    except wallet.CustomToolError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[billing] hide_builtin_tool failed: {e}")
        return jsonify({"error": "could not hide tool"}), 500
    return jsonify({"success": True, **result})


@billing_bp.route(
    "/api/admin/pricing/tools/<tool_key>/unhide", methods=["POST"])
def admin_pricing_tool_unhide(tool_key):
    """Un-hide a previously-hidden built-in tool.

    Idempotent - un-hiding a tool that isn't hidden is a no-op."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    try:
        result = wallet.unhide_builtin_tool(tool_key)
    except wallet.CustomToolError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[billing] unhide_builtin_tool failed: {e}")
        return jsonify({"error": "could not un-hide tool"}), 500
    return jsonify({"success": True, **result})


# ---------------------------------------------------------------------------
# Admin billing routes (per-user)
# ---------------------------------------------------------------------------

def _mutate_target_user(target_username: str, mutator):
    """Apply mutator(user_dict) to the target user's record under CAS.
    Returns (True, users_data_final) or (False, error_msg)."""
    from app import _users_cas_mutate  # type: ignore

    outcome = {"ok": False, "msg": ""}

    def _apply(data):
        u = (data.get("users") or {}).get(target_username)
        if not u:
            outcome["msg"] = "user_not_found"
            return None
        rv = mutator(u)
        if rv is False:
            # Mutator declined the mutation. Do NOT write.
            outcome["msg"] = getattr(mutator, "_last_error", "mutator_declined")
            return None
        outcome["ok"] = True
        return data

    final = _users_cas_mutate(_apply)
    if final is None:
        return False, (outcome["msg"] or "cas_write_skipped")
    return True, final


def _persist_customer_id(target_username: str, cus_id: str):
    def _apply(u):
        u["stripe_customer_id"] = cus_id
        return True
    _mutate_target_user(target_username, _apply)


# ---------------------------------------------------------------------------
# Company-shared wallet helpers (Jenna 2026-09-09)
# ---------------------------------------------------------------------------

def _mutate_target_company(company_name: str, mutator):
    """Apply mutator(company_dict) to the target company's record
    under CAS. Returns (True, users_data_final) or (False, error_msg).

    Auto-creates the company record if the caller wants a wallet on a
    company that only exists implicitly (i.e. some users have it in
    their 'company' field but no explicit companies entry yet). This
    matches the pattern used by the credit-pool code which also
    lazy-creates company records on first pool grant."""
    from app import _users_cas_mutate  # type: ignore

    outcome = {"ok": False, "msg": ""}

    def _apply(data):
        companies = data.setdefault("companies", {})
        c = companies.get(company_name)
        if c is None:
            # Lazy-create empty company record so the wallet has a
            # place to live. Fields default via the mutator + the
            # wallet getters.
            c = {}
            companies[company_name] = c
        rv = mutator(c)
        if rv is False:
            outcome["msg"] = getattr(mutator, "_last_error",
                                     "mutator_declined")
            return None
        outcome["ok"] = True
        return data

    final = _users_cas_mutate(_apply)
    if final is None:
        return False, (outcome["msg"] or "cas_write_skipped")
    return True, final


def _persist_company_customer_id(company_name: str, cus_id: str):
    def _apply(c):
        c["stripe_customer_id"] = cus_id
        return True
    _mutate_target_company(company_name, _apply)


def _mutate_billing_subject(subject_kind: str, subject_key: str,
                            mutator):
    """Subject-agnostic CAS mutator. Routes to _mutate_target_user or
    _mutate_target_company based on subject_kind. Used by the wallet
    self-serve write endpoints so a single code path handles both
    individual users and company-shared wallets."""
    if subject_kind == "company":
        return _mutate_target_company(subject_key, mutator)
    return _mutate_target_user(subject_key, mutator)


def _persist_subject_customer_id(subject_kind: str, subject_key: str,
                                 cus_id: str):
    """Persist a Stripe customer id onto whichever record owns the
    wallet (user or company)."""
    if subject_kind == "company":
        _persist_company_customer_id(subject_key, cus_id)
    else:
        _persist_customer_id(subject_key, cus_id)


def _require_wallet_write_access(ctx):
    """Guard for self-serve wallet write endpoints. A caller routed
    through a company wallet must be a company_billing_admin to
    top up, add/remove a card, or change auto-reload prefs. Returns
    None on success, or a (jsonify, http_status) tuple to short-
    circuit the request with a 403.

    Also enforces the paying_customer flag on the SUBJECT that
    holds the wallet (user or company). This replaces the older
    _get_paying_flag(u) checks so a paying company covers its
    non-paying members automatically."""
    if not ctx:
        return jsonify({"error": "not_logged_in"}), 401
    subject = ctx["subject"]
    if not bool(subject.get("paying_customer")):
        return jsonify({"error": "not_a_paying_customer"}), 403
    if ctx["billed_via_company"] and not ctx["viewer_is_billing_admin"]:
        return jsonify({
            "error": "not_company_billing_admin",
            "company_name": ctx["company_name"],
        }), 403
    return None


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/setup-intent",
    methods=["POST"])
def admin_setup_intent(target_username):
    """Admin clicks 'Add card' -> we create a Stripe Customer if
    needed, then a SetupIntent, and return the client_secret to
    embed in the Stripe Elements iframe."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore

    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    from app import load_users  # type: ignore
    users = load_users().get("users") or {}
    target = users.get(target_username)
    if not target:
        return jsonify({"error": "user_not_found"}), 404

    try:
        cus_id = billing.ensure_customer(
            target_username,
            email=str(target.get("email") or ""),
            name=(f"{target.get('first_name', '')} "
                  f"{target.get('last_name', '')}").strip()
                 or target_username,
            existing_customer_id=str(
                target.get("stripe_customer_id") or ""),
        )
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    if cus_id and cus_id != str(target.get("stripe_customer_id") or ""):
        _persist_customer_id(target_username, cus_id)

    try:
        si = billing.create_setup_intent(cus_id)
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({
        "setup_intent_id": si["id"],
        "client_secret": si["client_secret"],
        "customer_id": cus_id,
        "publishable_key": billing.publishable_key(),
    })


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/attach-card",
    methods=["POST"])
def admin_attach_card(target_username):
    """Called by the admin UI after Stripe Elements confirms the
    SetupIntent client-side. Body: {"payment_method_id": "pm_..."}.
    """
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore

    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    body = request.get_json(silent=True) or {}
    pm_id = str(body.get("payment_method_id") or "").strip()
    if not pm_id:
        return jsonify({"error": "missing_payment_method_id"}), 400

    from app import load_users  # type: ignore
    users = load_users().get("users") or {}
    target = users.get(target_username)
    if not target:
        return jsonify({"error": "user_not_found"}), 404
    cus_id = str(target.get("stripe_customer_id") or "")
    if not cus_id:
        return jsonify({"error": "no_stripe_customer"}), 400

    try:
        display = billing.attach_payment_method(cus_id, pm_id)
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    def _apply(u):
        u["stripe_payment_method_id"] = display["id"]
        u["stripe_payment_method_last4"] = display["last4"]
        u["stripe_payment_method_brand"] = display["brand"]
        return True

    ok, _ = _mutate_target_user(target_username, _apply)
    return jsonify({"success": ok, "card_display": display})


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/detach-card",
    methods=["POST"])
def admin_detach_card(target_username):
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore

    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    from app import load_users  # type: ignore
    users = load_users().get("users") or {}
    target = users.get(target_username)
    if not target:
        return jsonify({"error": "user_not_found"}), 404
    pm_id = str(target.get("stripe_payment_method_id") or "")
    if pm_id:
        try:
            billing.detach_payment_method(pm_id)
        except billing.BillingError:
            pass  # Log-only; we still remove locally

    def _apply(u):
        u["stripe_payment_method_id"] = ""
        u["stripe_payment_method_last4"] = ""
        u["stripe_payment_method_brand"] = ""
        # Downshift to prepay_only if a card was required by the mode.
        if str(u.get("billing_mode") or "").strip() in (
                "auto_reload", "monthly_invoice"):
            u["billing_mode"] = "prepay_only"
        return True

    ok, _ = _mutate_target_user(target_username, _apply)
    return jsonify({"success": ok})


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/charge",
    methods=["POST"])
def admin_custom_charge(target_username):
    """Admin charges an arbitrary amount to the user's saved card.
    Body: {"amount_usd": 250.00, "description": "..."}.

    The charge either succeeds (adds to wallet + logs a topup txn)
    or fails partner-safely with a BillingError message.
    """
    _, actor, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore
    import wallet  # type: ignore

    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    body = request.get_json(silent=True) or {}
    try:
        amt = float(body.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400
    if amt <= 0:
        return jsonify({"error": "amount_must_be_positive"}), 400
    desc = str(body.get("description") or "Admin charge").strip()

    from app import load_users  # type: ignore
    users = load_users().get("users") or {}
    target = users.get(target_username)
    if not target:
        return jsonify({"error": "user_not_found"}), 404
    cus_id = str(target.get("stripe_customer_id") or "")
    pm_id = str(target.get("stripe_payment_method_id") or "")
    if not (cus_id and pm_id):
        return jsonify({"error": "no_card_on_file"}), 400

    # Idempotency: hash the admin username + target + amount + minute
    # bucket so a double-click on the button within the same minute
    # coalesces to one charge.
    idem_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    idem_key = (f"admin-charge-{session.get('username', 'admin')}-"
                f"{target_username}-{int(amt*100)}-{idem_bucket}")

    try:
        result = billing.charge_saved_card(
            customer_id=cus_id,
            payment_method_id=pm_id,
            amount_usd=amt,
            description=desc,
            username=target_username,
            metadata={
                "purpose": "admin_custom_charge",
                "admin_username": session.get("username", "admin"),
                "idempotency_key": idem_key,
            },
        )
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 402

    if str(result.get("status", "")).lower() != "succeeded":
        return jsonify({
            "error": "charge_not_completed",
            "status": result.get("status", ""),
        }), 402

    # Charge succeeded -> top up wallet (webhook may also arrive; we
    # dedupe on stripe_ref in the webhook path).
    def _apply(u):
        wallet.apply_wallet_topup(
            u, amt,
            description=f"Admin charge: {desc}",
            stripe_ref=str(result.get("id") or ""),
            kind="topup")
        return True

    ok, _ = _mutate_target_user(target_username, _apply)
    return jsonify({
        "success": ok,
        "payment_intent_id": result.get("id"),
        "amount_usd": amt,
    })


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/topup",
    methods=["POST"])
def admin_manual_topup(target_username):
    """Admin credits the wallet WITHOUT charging a card (comp / trial
    / adjustment). Body: {"amount_usd": 100.00, "note": "..."}."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    body = request.get_json(silent=True) or {}
    try:
        amt = float(body.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400
    if amt == 0:
        return jsonify({"error": "amount_must_be_nonzero"}), 400
    note = str(body.get("note") or "Admin adjustment").strip()

    def _apply(u):
        if amt > 0:
            wallet.apply_wallet_topup(
                u, amt, description=note, kind="adjustment")
        else:
            # Negative topup = manual deduction (rare, admin cleanup).
            wallet.apply_wallet_deduct(
                u, -amt, description=note, tool_key="admin_adjust")
        return True

    ok, _ = _mutate_target_user(target_username, _apply)
    return jsonify({"success": ok})


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/mode",
    methods=["POST"])
def admin_set_billing_mode(target_username):
    """Body: {
        "billing_mode": "prepay_only" | "auto_reload" | "monthly_invoice",
        "auto_reload_threshold_usd": 500,
        "auto_reload_amount_usd": 1000,
        "monthly_invoice_limit_usd": 5000,
    }"""
    _, _, err = _require_super_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("billing_mode") or "").strip().lower()
    if mode not in ("prepay_only", "auto_reload", "monthly_invoice"):
        return jsonify({"error": "invalid_mode"}), 400

    def _apply(u):
        u["billing_mode"] = mode
        for k in ("auto_reload_threshold_usd", "auto_reload_amount_usd",
                  "monthly_invoice_limit_usd"):
            if k in body:
                try:
                    v = float(body[k])
                    if v >= 0:
                        u[k] = v
                except (TypeError, ValueError):
                    pass
        return True

    ok, _ = _mutate_target_user(target_username, _apply)
    return jsonify({"success": ok})


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/paying-flag",
    methods=["POST"])
def admin_set_paying_flag(target_username):
    """Body: {"paying_customer": true|false}."""
    _, _, err = _require_super_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    v = bool(body.get("paying_customer"))

    def _apply(u):
        u["paying_customer"] = v
        return True

    ok, _ = _mutate_target_user(target_username, _apply)
    return jsonify({"success": ok})


# ---------------------------------------------------------------------------
# Admin Billing UI (page + combined endpoints)
# ---------------------------------------------------------------------------

@billing_bp.route("/admin/billing", methods=["GET"])
def admin_billing_page():
    """Render the standalone admin Billing page. Separate from
    admin.html so we don't touch that 1MB file for this feature.
    """
    _, _, err = _require_super_admin()
    if err:
        # Anonymous or unauthorized -> bounce to dashboard.
        return redirect(url_for("index"))
    return render_template("admin_billing.html")


@billing_bp.route("/api/admin/users_billing", methods=["GET"])
def admin_users_billing():
    """Return a compact billing snapshot for every user. Fuels the
    admin Billing page table (username / email / paying flag / mode /
    card display / balance / lifetime spend)."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    import billing  # type: ignore
    from app import load_users  # type: ignore

    rows = []
    for uname, u in ((load_users() or {}).get("users") or {}).items():
        if not isinstance(u, dict):
            continue
        rows.append({
            "username": uname,
            "email": str(u.get("email") or ""),
            "role": str(u.get("role") or ""),
            "company": str(u.get("company") or ""),
            "billing_source": str(
                u.get("billing_source") or "user"),
            "company_billing_admin": bool(
                u.get("company_billing_admin")),
            "paying_customer": bool(u.get("paying_customer")),
            "unlimited": wallet.is_unlimited(u),
            "billing_mode": wallet.billing_mode(u),
            "wallet_balance_usd": wallet.wallet_balance(u),
            "wallet_lifetime_topups_usd": float(u.get(
                "wallet_lifetime_topups_usd", 0.0) or 0.0),
            "wallet_lifetime_spend_usd": float(u.get(
                "wallet_lifetime_spend_usd", 0.0) or 0.0),
            "auto_reload_threshold_usd":
                wallet.auto_reload_threshold(u),
            "auto_reload_amount_usd": wallet.auto_reload_amount(u),
            "monthly_invoice_limit_usd":
                wallet.monthly_invoice_limit(u),
            "has_card_on_file": wallet.has_card_on_file(u),
            "card_brand": str(u.get(
                "stripe_payment_method_brand") or ""),
            "card_last4": str(u.get(
                "stripe_payment_method_last4") or ""),
            "wallet_transactions": list(u.get(
                "wallet_transactions") or [])[:50],
        })
    rows.sort(key=lambda r: (
        not r["paying_customer"],  # paying first
        r["username"].lower(),
    ))
    return jsonify({
        "users": rows,
        "stripe_enabled": billing.is_enabled(),
        "stripe_publishable_key": billing.publishable_key(),
    })


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/config",
    methods=["POST"])
def admin_billing_config(target_username):
    """Combined save endpoint: unlimited flag + paying_customer flag +
    billing_mode + auto-reload thresholds + monthly-invoice limit,
    in one call.

    Body: {
      "unlimited": bool,        // sets credits to -1 (on) or 0 (off)
      "paying_customer": bool,
      "billing_mode": "prepay_only" | "auto_reload" | "monthly_invoice",
      "auto_reload_threshold_usd": 500,
      "auto_reload_amount_usd": 1000,
      "monthly_invoice_limit_usd": 5000,
    }

    The `unlimited` field mirrors the user-admin "Unlimited credits"
    checkbox so admins can flip the state without leaving the billing
    view (Jenna 2026-09-09).
    """
    _, _, err = _require_super_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("billing_mode") or "prepay_only").strip().lower()
    if mode not in ("prepay_only", "auto_reload", "monthly_invoice"):
        return jsonify({"error": "invalid_mode"}), 400

    def _apply(u):
        # Unlimited toggle: -1 sentinel is the pipeline-wide "never
        # charge" state (see wallet.is_unlimited + consume_credit).
        # Turning it OFF resets credits to 0 so the user starts
        # flowing through the normal metered / wallet path.
        if "unlimited" in body:
            was_unlimited = int(u.get("credits", 0) or 0) == -1
            wants_unlimited = bool(body.get("unlimited"))
            if wants_unlimited:
                u["credits"] = -1
            elif was_unlimited and not wants_unlimited:
                u["credits"] = 0
        if "paying_customer" in body:
            u["paying_customer"] = bool(body.get("paying_customer"))
        # Company-shared wallet routing (Jenna 2026-09-09). Admin
        # flips a user between 'user' (own wallet) and 'company'
        # (shared wallet at the company they belong to). The
        # 'company' field must already point to a real company for
        # 'company' routing to take effect - the resolver falls
        # back to the user record when the company is missing.
        if "billing_source" in body:
            src = str(body.get("billing_source") or "user").strip().lower()
            if src in ("user", "company"):
                u["billing_source"] = src
        if "company_billing_admin" in body:
            u["company_billing_admin"] = bool(
                body.get("company_billing_admin"))
        u["billing_mode"] = mode
        for k in ("auto_reload_threshold_usd",
                  "auto_reload_amount_usd",
                  "monthly_invoice_limit_usd"):
            if k in body:
                try:
                    v = float(body[k])
                    if v >= 0:
                        u[k] = v
                except (TypeError, ValueError):
                    pass
        return True

    ok, msg = _mutate_target_user(target_username, _apply)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"success": True})


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/adjust",
    methods=["POST"])
def admin_billing_adjust(target_username):
    """Adjust a user's wallet balance without touching a card. Body:
    {"amount_usd": +100 or -50, "description": "..."}.

    Positive amounts credit; negative amounts debit. Used for manual
    corrections, trial credits, or debit adjustments.
    """
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    body = request.get_json(silent=True) or {}
    try:
        amt = float(body.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400
    if amt == 0:
        return jsonify({"error": "amount_must_be_nonzero"}), 400
    desc = str(body.get("description") or "Manual adjustment").strip()

    def _apply(u):
        if amt > 0:
            wallet.apply_wallet_topup(
                u, amt, description=desc, kind="adjustment")
        else:
            wallet.apply_wallet_deduct(
                u, -amt, description=desc, tool_key="admin_adjust")
        return True

    ok, msg = _mutate_target_user(target_username, _apply)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"success": True, "amount_usd": amt})


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/charge-card",
    methods=["POST"])
def admin_billing_charge_card(target_username):
    """Alias for /billing/charge; matches the URL used by the admin
    Billing UI. Body: {"amount_usd": 250.00, "description": "..."}.
    """
    return admin_custom_charge(target_username)


@billing_bp.route(
    "/api/admin/user/<target_username>/billing/remove-card",
    methods=["POST"])
def admin_billing_remove_card(target_username):
    """Alias for /billing/detach-card; matches the URL used by the
    admin Billing UI."""
    return admin_detach_card(target_username)


# ---------------------------------------------------------------------------
# Company-shared wallet admin endpoints (Jenna 2026-09-09)
# ---------------------------------------------------------------------------
#
# One master account (company record) holds the card + balance. Every
# user at that company routes their pulls through the same pool. The
# endpoints below mirror the per-user admin surface exactly but operate
# on data['companies'][company_name] instead of data['users'][...].

@billing_bp.route("/api/admin/companies_billing", methods=["GET"])
def admin_companies_billing():
    """List every company with a wallet-ready snapshot. Same shape as
    /api/admin/users_billing, plus a `members` count of users routing
    through each company."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    import billing  # type: ignore
    from app import load_users  # type: ignore

    data = load_users() or {}
    companies = data.get("companies") or {}
    rows = []
    for cname, c in companies.items():
        if not isinstance(c, dict):
            continue
        members = wallet.company_members(cname, data)
        admins = wallet.company_billing_admins(cname, data)
        rows.append({
            "company": cname,
            "paying_customer": bool(c.get("paying_customer")),
            "unlimited": bool(c.get("unlimited")),
            "billing_mode": wallet.billing_mode(c),
            "wallet_balance_usd": wallet.wallet_balance(c),
            "wallet_lifetime_topups_usd": float(c.get(
                "wallet_lifetime_topups_usd", 0.0) or 0.0),
            "wallet_lifetime_spend_usd": float(c.get(
                "wallet_lifetime_spend_usd", 0.0) or 0.0),
            "auto_reload_threshold_usd":
                wallet.auto_reload_threshold(c),
            "auto_reload_amount_usd": wallet.auto_reload_amount(c),
            "monthly_invoice_limit_usd":
                wallet.monthly_invoice_limit(c),
            "has_card_on_file": wallet.has_card_on_file(c),
            "card_brand": str(c.get(
                "stripe_payment_method_brand") or ""),
            "card_last4": str(c.get(
                "stripe_payment_method_last4") or ""),
            "monthly_access_last_billed_ym": str(c.get(
                "monthly_access_last_billed_ym") or ""),
            "member_count": len(members),
            "member_usernames": [u for u, _ in members],
            "billing_admin_usernames": list(admins),
            "wallet_transactions": list(c.get(
                "wallet_transactions") or [])[:50],
        })
    rows.sort(key=lambda r: (
        not r["paying_customer"],
        r["company"].lower(),
    ))
    return jsonify({
        "companies": rows,
        "stripe_enabled": billing.is_enabled(),
        "stripe_publishable_key": billing.publishable_key(),
    })


@billing_bp.route(
    "/api/admin/company/<company_name>/billing/config",
    methods=["POST"])
def admin_company_billing_config(company_name):
    """Set company billing flags. Mirrors admin_billing_config for a
    user record."""
    _, _, err = _require_super_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("billing_mode") or "prepay_only").strip().lower()
    if mode not in ("prepay_only", "auto_reload", "monthly_invoice"):
        return jsonify({"error": "invalid_mode"}), 400

    def _apply(c):
        if "paying_customer" in body:
            c["paying_customer"] = bool(body.get("paying_customer"))
        if "unlimited" in body:
            c["unlimited"] = bool(body.get("unlimited"))
        c["billing_mode"] = mode
        for k in ("auto_reload_threshold_usd",
                  "auto_reload_amount_usd",
                  "monthly_invoice_limit_usd"):
            if k in body:
                try:
                    v = float(body[k])
                    if v >= 0:
                        c[k] = v
                except (TypeError, ValueError):
                    pass
        return True

    ok, msg = _mutate_target_company(company_name, _apply)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"success": True})


@billing_bp.route(
    "/api/admin/company/<company_name>/billing/adjust",
    methods=["POST"])
def admin_company_billing_adjust(company_name):
    """Manually credit / debit a company wallet. No card interaction."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import wallet  # type: ignore
    body = request.get_json(silent=True) or {}
    try:
        amt = float(body.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400
    if amt == 0:
        return jsonify({"error": "amount_must_be_nonzero"}), 400
    desc = str(body.get("description") or "Manual adjustment").strip()

    def _apply(c):
        if amt > 0:
            wallet.apply_wallet_topup(
                c, amt, description=desc, kind="adjustment")
        else:
            wallet.apply_wallet_deduct(
                c, -amt, description=desc, tool_key="admin_adjust")
        return True

    ok, msg = _mutate_target_company(company_name, _apply)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"success": True, "amount_usd": amt})


@billing_bp.route(
    "/api/admin/company/<company_name>/billing/setup-intent",
    methods=["POST"])
def admin_company_setup_intent(company_name):
    """Create a Stripe SetupIntent so an admin can add a card to the
    company record. Auto-provisions the Stripe Customer on the
    company's behalf the first time this is called."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    from app import load_users  # type: ignore
    company = ((load_users() or {}).get("companies") or {}
               ).get(company_name) or {}

    try:
        cus_id = billing.ensure_customer(
            f"company:{company_name}",
            email="",  # companies don't have their own email
            name=company_name,
            existing_customer_id=str(
                company.get("stripe_customer_id") or ""),
        )
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    if cus_id and cus_id != str(company.get("stripe_customer_id") or ""):
        _persist_company_customer_id(company_name, cus_id)

    try:
        si = billing.create_setup_intent(cus_id)
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({
        "setup_intent_id": si["id"],
        "client_secret": si["client_secret"],
        "customer_id": cus_id,
        "publishable_key": billing.publishable_key(),
    })


@billing_bp.route(
    "/api/admin/company/<company_name>/billing/attach-card",
    methods=["POST"])
def admin_company_attach_card(company_name):
    """Attach a PaymentMethod to a company. Body:
    {"payment_method_id": "pm_..."}."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    from app import load_users  # type: ignore
    company = ((load_users() or {}).get("companies") or {}
               ).get(company_name) or {}

    body = request.get_json(silent=True) or {}
    pm_id = str(body.get("payment_method_id") or "").strip()
    if not pm_id:
        return jsonify({"error": "missing_payment_method_id"}), 400
    cus_id = str(company.get("stripe_customer_id") or "")
    if not cus_id:
        return jsonify({"error": "no_stripe_customer"}), 400

    try:
        display = billing.attach_payment_method(cus_id, pm_id)
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    def _apply(c):
        c["stripe_payment_method_id"] = display["id"]
        c["stripe_payment_method_last4"] = display["last4"]
        c["stripe_payment_method_brand"] = display["brand"]
        return True

    ok, _ = _mutate_target_company(company_name, _apply)
    return jsonify({"success": ok, "card_display": display})


@billing_bp.route(
    "/api/admin/company/<company_name>/billing/detach-card",
    methods=["POST"])
def admin_company_detach_card(company_name):
    """Remove the company's card on file. Downshifts billing_mode
    off auto_reload."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    from app import load_users  # type: ignore
    company = ((load_users() or {}).get("companies") or {}
               ).get(company_name) or {}
    pm_id = str(company.get("stripe_payment_method_id") or "")
    if pm_id:
        try:
            billing.detach_payment_method(pm_id)
        except billing.BillingError:
            pass  # best-effort remove locally

    def _apply(c):
        c["stripe_payment_method_id"] = ""
        c["stripe_payment_method_last4"] = ""
        c["stripe_payment_method_brand"] = ""
        if str(c.get("billing_mode") or "").strip() == "auto_reload":
            c["billing_mode"] = "prepay_only"
        return True

    ok, _ = _mutate_target_company(company_name, _apply)
    return jsonify({"success": ok})


@billing_bp.route(
    "/api/admin/company/<company_name>/billing/charge-card",
    methods=["POST"])
def admin_company_charge_card(company_name):
    """Off-session Stripe charge against the company card. On
    success, credits the company wallet. Body: {"amount_usd": 500,
    "description": "..."}."""
    _, _, err = _require_super_admin()
    if err:
        return err
    import billing  # type: ignore
    import wallet  # type: ignore
    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    body = request.get_json(silent=True) or {}
    try:
        amt = float(body.get("amount_usd") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400
    if amt < 0.50:
        return jsonify({"error": "amount_below_stripe_min"}), 400
    desc = (str(body.get("description") or "").strip()
            or f"Admin top-up ({company_name})")

    from app import load_users  # type: ignore
    company = ((load_users() or {}).get("companies") or {}
               ).get(company_name) or {}
    cus_id = str(company.get("stripe_customer_id") or "")
    pm_id = str(company.get("stripe_payment_method_id") or "")
    if not (cus_id and pm_id):
        return jsonify({"error": "no_card_on_file"}), 400

    try:
        pi = billing.charge_saved_card(
            customer_id=cus_id,
            payment_method_id=pm_id,
            amount_usd=amt,
            description=desc,
            username=f"company:{company_name}",
            metadata={
                "purpose": "admin_topup",
                "subject_kind": "company",
                "subject_key": company_name,
            },
        )
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502
    if str(pi.get("status") or "").lower() != "succeeded":
        return jsonify({
            "error": "charge_not_completed",
            "stripe_status": pi.get("status"),
        }), 502

    pi_id = str(pi.get("id") or "")

    def _apply(c):
        for t in list(c.get("wallet_transactions") or [])[:20]:
            if (str(t.get("stripe_ref") or "") == pi_id
                    and str(t.get("kind") or "")
                    in ("topup", "auto_reload", "adjustment")):
                return False  # idempotent
        wallet.apply_wallet_topup(
            c, amt, description=desc, stripe_ref=pi_id, kind="topup")
        return True

    ok, msg = _mutate_target_company(company_name, _apply)
    if not ok:
        # `_apply` returning False means webhook already logged it.
        return jsonify({
            "success": True, "payment_intent_id": pi_id,
            "note": "already_recorded"})
    return jsonify({
        "success": True,
        "payment_intent_id": pi_id,
        "amount_usd": amt,
    })


# ---------------------------------------------------------------------------
# Stripe webhook (public, signature-verified, idempotent)
# ---------------------------------------------------------------------------

# S3 key where we log processed webhook event ids for idempotency.
WEBHOOK_EVENTS_KEY = "system/billing/stripe_events_processed.json"
_WEBHOOK_EVENT_CAP = 5000  # keep last N event ids to bound the dict


def _webhook_event_already_processed(event_id: str) -> bool:
    """Check + record whether we've processed this Stripe event id.
    Returns True when the event was ALREADY processed (skip)."""
    try:
        from app import s3_client, METADATA_BUCKET  # type: ignore
    except Exception:
        return False  # in-process test env: never dedupe
    if not s3_client:
        return False
    try:
        try:
            resp = s3_client.get_object(
                Bucket=METADATA_BUCKET, Key=WEBHOOK_EVENTS_KEY)
            doc = json.loads(resp["Body"].read().decode("utf-8"))
        except Exception:
            doc = {}
        events = doc.get("events") or {}
        if event_id in events:
            return True
        # Mark it. Evict oldest when we exceed the cap.
        events[event_id] = _fmt_ts_utc()
        if len(events) > _WEBHOOK_EVENT_CAP:
            # Sort by ts + keep newest.
            items = sorted(events.items(), key=lambda kv: kv[1] or "")
            events = dict(items[-_WEBHOOK_EVENT_CAP:])
        doc["events"] = events
        s3_client.put_object(
            Bucket=METADATA_BUCKET, Key=WEBHOOK_EVENTS_KEY,
            Body=json.dumps(doc).encode("utf-8"),
            ContentType="application/json")
        return False
    except Exception as e:
        print(f"[billing] webhook idempotency check failed "
              f"(fail-open): {e}")
        return False


def _find_user_by_customer_id(customer_id: str):
    """Return (username, user_dict) matching a Stripe customer id, or
    (None, None) when no user has that id set."""
    from app import load_users  # type: ignore
    users = (load_users() or {}).get("users") or {}
    for uname, u in users.items():
        if str(u.get("stripe_customer_id") or "") == customer_id:
            return uname, u
    return None, None


def _find_user_by_event(event: dict):
    """Try to locate the target user from a webhook event. Preference
    order:
      1. metadata.dashboard_username on the object or payment_intent.
      2. Customer id -> user lookup.
    """
    from app import load_users  # type: ignore
    obj = ((event or {}).get("data") or {}).get("object") or {}
    md = obj.get("metadata") or {}
    uname = str(md.get("dashboard_username") or "").strip()
    if uname:
        users = (load_users() or {}).get("users") or {}
        if uname in users:
            return uname, users[uname]
    cus_id = str(obj.get("customer") or "")
    if cus_id:
        return _find_user_by_customer_id(cus_id)
    return None, None


def _find_subject_by_event(event: dict):
    """Resolve the SUBJECT that should receive a wallet credit from
    a webhook event. Preference order (Jenna 2026-09-09 company-
    shared wallet):

      1. metadata.subject_kind + metadata.subject_key -> user or
         company record.
      2. Customer id lookup: check companies first (a company Stripe
         customer id trumps a user match), then users.
      3. Fall back to _find_user_by_event (legacy user-only route).

    Returns (subject_kind, subject_key, subject_dict, users_data) or
    (None, None, None, None) if nothing matched. users_data is
    returned so callers can persist via _mutate_billing_subject.
    """
    from app import load_users  # type: ignore
    obj = ((event or {}).get("data") or {}).get("object") or {}
    md = obj.get("metadata") or {}
    # Also inspect the nested payment_intent metadata (Checkout mode).
    pi = obj.get("payment_intent")
    if isinstance(pi, dict):
        pi_md = pi.get("metadata") or {}
        for k, v in pi_md.items():
            md.setdefault(k, v)

    data = load_users() or {}
    kind = str(md.get("subject_kind") or "").strip().lower()
    key = str(md.get("subject_key") or "").strip()
    if kind == "company" and key:
        c = (data.get("companies") or {}).get(key)
        if isinstance(c, dict):
            return "company", key, c, data
    if kind == "user" and key:
        u = (data.get("users") or {}).get(key)
        if isinstance(u, dict):
            return "user", key, u, data

    # Customer id fallback: check companies first.
    cus_id = str(obj.get("customer") or "")
    if cus_id:
        for cname, c in (data.get("companies") or {}).items():
            if isinstance(c, dict) and str(
                    c.get("stripe_customer_id") or "") == cus_id:
                return "company", cname, c, data
        for uname, u in (data.get("users") or {}).items():
            if isinstance(u, dict) and str(
                    u.get("stripe_customer_id") or "") == cus_id:
                return "user", uname, u, data

    # Legacy metadata.dashboard_username fallback.
    uname, u = _find_user_by_event(event)
    if uname and u:
        return "user", uname, u, data
    return None, None, None, None


def _amount_from_object(obj: dict) -> float:
    """Extract a USD amount from various Stripe object shapes.

    - checkout.session: amount_total (in cents)
    - payment_intent: amount_received (in cents), fallback to amount
    - refund: amount (in cents), sign flipped by caller
    """
    for k in ("amount_total", "amount_received", "amount"):
        v = obj.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return round(int(v) / 100.0, 2)
    return 0.0


def _handle_checkout_session_completed(event: dict):
    obj = ((event or {}).get("data") or {}).get("object") or {}
    subject_kind, subject_key, subject, _ = _find_subject_by_event(event)
    if not subject:
        print(f"[billing] webhook checkout.session.completed "
              f"unmapped: event_id={event.get('id')}")
        return
    amt = _amount_from_object(obj)
    if amt <= 0:
        return

    import wallet  # type: ignore
    ref = str(obj.get("id") or event.get("id") or "")
    md = obj.get("metadata") or {}
    desc = str(md.get("description") or "Wallet top-up").strip()

    def _apply(rec):
        wallet.apply_wallet_topup(
            rec, amt, description=desc, stripe_ref=ref, kind="topup")
        # If a card was captured in this checkout, persist the id so
        # future auto-reload works without a separate SetupIntent
        # (this is why we set setup_future_usage='off_session' when
        # creating the session).
        pi = obj.get("payment_intent")
        if isinstance(pi, dict) and pi.get("payment_method"):
            pm_id = str(pi.get("payment_method") or "")
            if pm_id and not rec.get("stripe_payment_method_id"):
                rec["stripe_payment_method_id"] = pm_id
        return True

    _mutate_billing_subject(subject_kind, subject_key, _apply)
    print(f"[billing] webhook credited ${amt:.2f} to "
          f"{subject_kind}:{subject_key} (session={ref})")


def _handle_payment_intent_succeeded(event: dict):
    """Fires on auto-reload charges + admin custom charges. The
    admin_custom_charge route already credits the wallet inline on
    success; this webhook is a belt-and-suspenders backstop. Because
    we key idempotency off Stripe event id AND stripe_ref, a duplicate
    credit is avoided."""
    obj = ((event or {}).get("data") or {}).get("object") or {}
    subject_kind, subject_key, subject, _ = _find_subject_by_event(event)
    if not subject:
        return
    md = obj.get("metadata") or {}
    if str(md.get("purpose") or "") not in (
            "admin_custom_charge", "auto_reload", "wallet_topup",
            "monthly_invoice"):
        # Not a wallet-affecting charge (e.g. subscription); skip.
        return
    amt = _amount_from_object(obj)
    if amt <= 0:
        return

    # Idempotency by stripe_ref: if we already logged this PI id as a
    # topup, skip. The prior admin_custom_charge inline write leaves
    # exactly this ref on the txn row.
    ref = str(obj.get("id") or "")
    txns = list(subject.get("wallet_transactions") or [])
    already = any(
        str(t.get("stripe_ref") or "") == ref
        and str(t.get("kind") or "") in ("topup", "auto_reload")
        for t in txns)
    if already:
        return

    import wallet  # type: ignore
    kind = "auto_reload" if md.get("purpose") == "auto_reload" \
        else "topup"

    def _apply(rec):
        wallet.apply_wallet_topup(
            rec, amt,
            description=str(md.get("description")
                            or "Card charge"),
            stripe_ref=ref, kind=kind)
        return True

    _mutate_billing_subject(subject_kind, subject_key, _apply)
    print(f"[billing] webhook confirmed ${amt:.2f} to "
          f"{subject_kind}:{subject_key} (pi={ref}, kind={kind})")


def _handle_payment_intent_failed(event: dict):
    """Card declined on an off-session charge. Email admin + user."""
    obj = ((event or {}).get("data") or {}).get("object") or {}
    uname, u = _find_user_by_event(event)
    if not uname:
        return
    amt = _amount_from_object(obj)
    md = obj.get("metadata") or {}
    print(f"[billing] webhook DECLINED ${amt:.2f} for {uname} "
          f"(pi={obj.get('id')}, purpose={md.get('purpose')})")

    # Best-effort ops email; never raises.
    try:
        from app import _send_ops_email_safe  # type: ignore
    except Exception:
        _send_ops_email_safe = None
    if _send_ops_email_safe:
        try:
            _send_ops_email_safe(
                subject=(f"Card declined for {uname} "
                         f"(${amt:.2f})"),
                body_text=(
                    f"Stripe declined an off-session charge for "
                    f"user {uname}.\n\n"
                    f"Amount: ${amt:.2f}\n"
                    f"Purpose: {md.get('purpose', '')}\n"
                    f"Payment intent: {obj.get('id', '')}\n"
                    f"Failure reason (Stripe code): "
                    f"{obj.get('last_payment_error', {}).get('code', '')}"
                    f"\n\nUser is not locked out; wallet balance is "
                    f"unchanged. Consider following up."))
        except Exception:
            pass


def _handle_charge_refunded(event: dict):
    obj = ((event or {}).get("data") or {}).get("object") or {}
    subject_kind, subject_key, subject, _ = _find_subject_by_event(event)
    if not subject:
        return
    # `charge.refunded` fires with the WHOLE charge object, and the
    # refunds are nested under obj.refunds.data. Take the newest
    # refund's amount; alternatively Stripe fires charge.refund.updated
    # per refund. We reconcile by amount + ref key.
    refunds = ((obj.get("refunds") or {}).get("data") or [])
    if not refunds:
        return
    newest = refunds[0]
    amt = round(int(newest.get("amount") or 0) / 100.0, 2)
    if amt <= 0:
        return
    import wallet  # type: ignore

    def _apply(rec):
        wallet.apply_wallet_refund(
            rec, amt,
            description="Refund",
            stripe_ref=str(newest.get("id") or ""))
        return True

    _mutate_billing_subject(subject_kind, subject_key, _apply)


_WEBHOOK_HANDLERS = {
    "checkout.session.completed": _handle_checkout_session_completed,
    "payment_intent.succeeded": _handle_payment_intent_succeeded,
    "payment_intent.payment_failed": _handle_payment_intent_failed,
    "charge.refunded": _handle_charge_refunded,
}


@billing_bp.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Public endpoint hit by Stripe. Verified via
    STRIPE_WEBHOOK_SECRET. Idempotent by event.id."""
    import billing  # type: ignore

    payload = request.get_data(cache=False, as_text=False) or b""
    sig = request.headers.get("Stripe-Signature", "") or ""
    try:
        event = billing.verify_webhook(payload, sig)
    except billing.BillingError as e:
        # Return 400 on bad signature so Stripe retries only on
        # transient issues, not misconfiguration.
        print(f"[billing] webhook verify failed: {e}")
        return jsonify({"error": str(e)}), (
            getattr(e, "http_status", 400) or 400)

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if _webhook_event_already_processed(event_id):
        return jsonify({"received": True, "duplicate": True})

    handler = _WEBHOOK_HANDLERS.get(event_type)
    if not handler:
        # Unhandled events: acknowledge with 200 so Stripe stops
        # retrying. We can add a handler later.
        return jsonify({"received": True, "handled": False})

    try:
        handler(event)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Return 500 so Stripe retries. Idempotency check upstream
        # means a successful retry won't double-credit.
        return jsonify({"error": "handler_failed"}), 500

    return jsonify({"received": True, "handled": True})


# ---------------------------------------------------------------------------
# Usage export (Jenna 2026-09-09)
# ---------------------------------------------------------------------------
#
# The user-admin modal's billing snapshot ships an Export CSV button
# that streams from here. Unifies two ledgers into one CSV:
#   1. credit_usage_history: the classic internal-allowance ledger
#      (recorded even for unlimited users). Every priced pull writes
#      one row here.
#   2. wallet_transactions: dollar-side ledger (topup / deduct /
#      refund) for paying customers.
# Sorted newest-first so the export opens on the most recent activity.
#
# Access: any logged-in admin or super_admin. Regular admins have
# read-only access to their users' usage per Jenna 2026-09-09
# ("regular admins can see what they've run, export it, etc.").


def _require_admin_or_super():
    """Return (username, user_dict) or (jsonify_response, 403) tuple.
    Allows role in {'admin', 'super_admin'}. Everyone else gets 403."""
    uname, u, err = _require_login()
    if err:
        return None, None, err
    role = str((u or {}).get("role") or "").strip().lower()
    if role not in ("admin", "super_admin"):
        return None, None, (jsonify({"error": "not_authorized"}), 403)
    return uname, u, None


@billing_bp.route(
    "/api/admin/user/<target_username>/export_usage.csv",
    methods=["GET"])
def admin_export_user_usage_csv(target_username):
    """Stream a unified usage CSV for ONE user. Columns:
        at, source, kind, description, tool_key, pull_type,
        credits, usd, job_id
    Where source is 'credit_usage_history' or 'wallet_transactions'.
    Empty cells are legit - not every row has every field."""
    _, _, err = _require_admin_or_super()
    if err:
        return err
    try:
        from app import load_users  # type: ignore
    except Exception:
        return jsonify({"error": "app_unavailable"}), 500
    from flask import Response  # local import so tests can stub Flask

    users = ((load_users() or {}).get("users") or {})
    u = users.get(target_username)
    if not isinstance(u, dict):
        return jsonify({"error": "not_found"}), 404

    # Merge the two ledgers into a single time-sorted list.
    import csv
    import io

    rows: list[dict] = []
    for e in (u.get("credit_usage_history") or []):
        if not isinstance(e, dict):
            continue
        rows.append({
            "at": str(e.get("used_at") or ""),
            "source": "credit_usage_history",
            "kind": str(e.get("pull_type") or "usage"),
            "description": str(e.get("description") or ""),
            "tool_key": "",
            "pull_type": str(e.get("pull_type") or ""),
            "credits": e.get("credits_used") or "",
            "usd": "",
            "job_id": str(e.get("job_id") or ""),
        })
    for t in (u.get("wallet_transactions") or []):
        if not isinstance(t, dict):
            continue
        amt = t.get("amount_usd")
        try:
            amt_f = float(amt) if amt is not None else 0.0
        except (TypeError, ValueError):
            amt_f = 0.0
        rows.append({
            "at": str(t.get("at") or ""),
            "source": "wallet_transactions",
            "kind": str(t.get("kind") or t.get("type") or "wallet"),
            "description": str(t.get("description") or ""),
            "tool_key": str(t.get("tool_key") or ""),
            "pull_type": "",
            "credits": "",
            "usd": f"{amt_f:.2f}",
            "job_id": str(t.get("job_id") or ""),
        })

    # Newest-first: stable sort so equal timestamps preserve source
    # order.
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["at", "source", "kind", "description", "tool_key",
                    "pull_type", "credits", "usd", "job_id"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(r)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"usage_{target_username}_{ts}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            # Cache-Control: private + no-store so an admin exporting
            # a user's history doesn't leave the CSV in a shared
            # cache (e.g. Cloudflare).
            "Cache-Control": "private, no-store, max-age=0",
        },
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_billing_blueprint(app):
    """Called once from app.py after `app` is created."""
    try:
        app.register_blueprint(billing_bp)
        print("✅ Billing blueprint registered")
    except Exception as e:
        print(f"⚠️ Billing blueprint registration failed: {e}")
