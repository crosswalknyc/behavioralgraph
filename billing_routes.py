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

    Non-paying users get a minimal payload (balance=0, ui_visible=false)
    so the front-end can decide whether to show the wallet at all.
    """
    uname, u, err = _require_login()
    if err:
        return err
    import wallet  # type: ignore
    import billing  # type: ignore

    pricing = wallet.load_pricing()
    ui_visible = wallet.admits_wallet_ui(u)

    payload = {
        "username": uname,
        "ui_visible": ui_visible,
        "paying_customer": _get_paying_flag(u),
        "wallet_balance_usd": wallet.wallet_balance(u),
        "lifetime_topups_usd": float(u.get(
            "wallet_lifetime_topups_usd", 0.0) or 0.0),
        "lifetime_spend_usd": float(u.get(
            "wallet_lifetime_spend_usd", 0.0) or 0.0),
        "billing_mode": wallet.billing_mode(u),
        "auto_reload_threshold_usd":
            wallet.auto_reload_threshold(u),
        "auto_reload_amount_usd": wallet.auto_reload_amount(u),
        "monthly_invoice_limit_usd":
            wallet.monthly_invoice_limit(u),
        "has_card_on_file": wallet.has_card_on_file(u),
        "card_display": {
            "last4": str(u.get("stripe_payment_method_last4", "")),
            "brand": str(u.get("stripe_payment_method_brand", "")),
        },
        "transactions": list(u.get("wallet_transactions", []))[:100],
        "top_up_packs_usd": wallet.top_up_pack_sizes(),
        "top_up_min_custom_usd": wallet.top_up_min_custom(),
        "stripe_enabled": billing.is_enabled(),
        "stripe_publishable_key": billing.publishable_key(),
    }
    return jsonify(payload)


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
    """
    uname, u, err = _require_login()
    if err:
        return err
    import wallet  # type: ignore
    import billing  # type: ignore

    if not billing.is_enabled():
        return jsonify({"error": "billing_not_configured"}), 503

    if not wallet.admits_wallet_ui(u):
        return jsonify({"error": "not_a_paying_customer"}), 403

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

    # Idempotent Customer create + persist the id if it was new.
    try:
        cus_id = billing.ensure_customer(
            uname,
            email=str(u.get("email") or ""),
            name=(f"{u.get('first_name', '')} "
                  f"{u.get('last_name', '')}").strip() or uname,
            existing_customer_id=str(u.get("stripe_customer_id") or ""),
        )
    except billing.BillingDisabled:
        return jsonify({"error": "billing_not_configured"}), 503
    except billing.BillingError as e:
        return jsonify({"error": str(e)}), 502

    if cus_id and cus_id != str(u.get("stripe_customer_id") or ""):
        _persist_customer_id(uname, cus_id)

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
            username=uname,
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
    return jsonify(wallet.load_pricing(force_reload=True))


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
    saved = wallet.save_pricing(body)
    return jsonify({"success": True, "pricing": saved})


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
            "paying_customer": bool(u.get("paying_customer")),
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
    """Combined save endpoint: paying_customer flag + billing_mode +
    auto-reload thresholds + monthly-invoice limit, in one call.

    Body: {
      "paying_customer": bool,
      "billing_mode": "prepay_only" | "auto_reload" | "monthly_invoice",
      "auto_reload_threshold_usd": 500,
      "auto_reload_amount_usd": 1000,
      "monthly_invoice_limit_usd": 5000,
    }
    """
    _, _, err = _require_super_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("billing_mode") or "prepay_only").strip().lower()
    if mode not in ("prepay_only", "auto_reload", "monthly_invoice"):
        return jsonify({"error": "invalid_mode"}), 400

    def _apply(u):
        if "paying_customer" in body:
            u["paying_customer"] = bool(body.get("paying_customer"))
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
    uname, u = _find_user_by_event(event)
    if not uname or not u:
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

    def _apply(usr):
        wallet.apply_wallet_topup(
            usr, amt, description=desc, stripe_ref=ref, kind="topup")
        # If a card was captured in this checkout, persist the id so
        # future auto-reload works without a separate SetupIntent
        # (this is why we set setup_future_usage='off_session' when
        # creating the session).
        pi = obj.get("payment_intent")
        if isinstance(pi, dict) and pi.get("payment_method"):
            pm_id = str(pi.get("payment_method") or "")
            if pm_id and not usr.get("stripe_payment_method_id"):
                usr["stripe_payment_method_id"] = pm_id
        return True

    _mutate_target_user(uname, _apply)
    print(f"[billing] webhook credited ${amt:.2f} to {uname} "
          f"(session={ref})")


def _handle_payment_intent_succeeded(event: dict):
    """Fires on auto-reload charges + admin custom charges. The
    admin_custom_charge route already credits the wallet inline on
    success; this webhook is a belt-and-suspenders backstop. Because
    we key idempotency off Stripe event id AND stripe_ref, a duplicate
    credit is avoided."""
    obj = ((event or {}).get("data") or {}).get("object") or {}
    uname, u = _find_user_by_event(event)
    if not uname or not u:
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
    txns = list(u.get("wallet_transactions") or [])
    already = any(
        str(t.get("stripe_ref") or "") == ref
        and str(t.get("kind") or "") in ("topup", "auto_reload")
        for t in txns)
    if already:
        return

    import wallet  # type: ignore
    kind = "auto_reload" if md.get("purpose") == "auto_reload" \
        else "topup"

    def _apply(usr):
        wallet.apply_wallet_topup(
            usr, amt,
            description=str(md.get("description")
                            or "Card charge"),
            stripe_ref=ref, kind=kind)
        return True

    _mutate_target_user(uname, _apply)
    print(f"[billing] webhook confirmed ${amt:.2f} to {uname} "
          f"(pi={ref}, kind={kind})")


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
    uname, u = _find_user_by_event(event)
    if not uname or not u:
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

    def _apply(usr):
        wallet.apply_wallet_refund(
            usr, amt,
            description="Refund",
            stripe_ref=str(newest.get("id") or ""))
        return True

    _mutate_target_user(uname, _apply)


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
# Registration
# ---------------------------------------------------------------------------

def register_billing_blueprint(app):
    """Called once from app.py after `app` is created."""
    try:
        app.register_blueprint(billing_bp)
        print("✅ Billing blueprint registered")
    except Exception as e:
        print(f"⚠️ Billing blueprint registration failed: {e}")
