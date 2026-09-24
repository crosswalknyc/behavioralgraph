"""Public site + self-serve Prometheus signup (Jenna 2026-09-24).

Serves the marketing site at /site (static files under bg-webapp/site/)
and runs the self-serve flow the site's Ask box leads into:

    1. POST /site/api/signup
         Creates the dashboard account on the Prometheus self-serve plan
         (status pending_payment) and returns a Stripe Checkout URL for
         the $5,000 opening balance. The card is saved on the customer
         so auto-reload can charge it later.
    2. Stripe webhook checkout.session.completed (billing_routes.py)
         Credits the wallet, saves the card, turns auto-reload on
         ($5,000 when the balance reaches $500), then calls
         activate_after_payment() below.
    3. activate_after_payment()
         Flips the account to active and emails jenna@, liz@, jessie@
         and czarina@ that someone signed up and paid.
    4. GET /site/api/signup/status?sid=cs_...
         The welcome page polls this. Once the account is active it
         logs the browser in (one time, within the signup window) so
         "Open Prometheus" lands straight in the dashboard.

The plan itself (what the user can see once logged in):

    * has_chatbot_profile_iq_access True, every other has_*_access False
    * allowed_runs [] and allowed_categories [] so nothing is granted
      by the fleet-wide auto-add; app.py grants each file the user
      pulls through Prometheus to that user only
    * paying_customer True, billing_mode auto_reload, threshold $500,
      amount $5,000 (wallet.py enforces the same floors)

No em dashes. Emails are plain text and never raise.
"""
from __future__ import annotations

import os
import re
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional, Tuple

from flask import (Blueprint, jsonify, redirect, request, send_from_directory,
                   session)

site_bp = Blueprint("site", __name__)

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
PLAN_KEY = "prometheus_self_serve"
OPENING_BALANCE_USD = 5000.0
AUTO_RELOAD_THRESHOLD_USD = 500.0
AUTO_RELOAD_AMOUNT_USD = 5000.0
SIGNUP_SOURCE = "self_serve_signup"

# Jenna 2026-09-24: "email me, liz, jessie and czarina when someone
# signs up and pays the 5k top up". Workflow / billing email, so Liz
# stays on it (profile-iq-pipeline-rules.mdc section 6).
SIGNUP_NOTICE_TO: Tuple[str, ...] = (
    "jenna@crosswalknyc.com",
    "liz@crosswalknyc.com",
    "jessie@crosswalknyc.com",
    "czarina@crosswalknyc.com",
)
EMAIL_SOURCE = "Crosswalk <jenna@crosswalknyc.com>"
AWS_REGION = "us-east-2"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FREE_MAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "me.com", "live.com", "msn.com", "proton.me",
    "protonmail.com", "ymail.com",
}


# ---------------------------------------------------------------------------
# Static site
# ---------------------------------------------------------------------------

@site_bp.route("/site")
def site_root_redirect():
    return redirect("/site/", code=302)


@site_bp.route("/site/")
def site_index():
    return send_from_directory(SITE_DIR, "index.html")


@site_bp.route("/site/<path:subpath>")
def site_file(subpath):
    """Static files under bg-webapp/site. Extensionless paths resolve to
    <name>.html so /site/signup and /site/signup.html both work."""
    clean = subpath.strip("/")
    if not clean:
        return send_from_directory(SITE_DIR, "index.html")
    candidate = os.path.normpath(os.path.join(SITE_DIR, clean))
    if not candidate.startswith(SITE_DIR + os.sep) and candidate != SITE_DIR:
        return jsonify({"error": "not_found"}), 404
    if os.path.isdir(candidate):
        return send_from_directory(SITE_DIR, os.path.join(clean, "index.html"))
    if os.path.isfile(candidate):
        return send_from_directory(SITE_DIR, clean)
    if os.path.isfile(candidate + ".html"):
        return send_from_directory(SITE_DIR, clean + ".html")
    return jsonify({"error": "not_found"}), 404


# ---------------------------------------------------------------------------
# Plan record
# ---------------------------------------------------------------------------

def is_self_serve_plan(user: Optional[dict]) -> bool:
    return bool(user) and str(user.get("plan") or "") == PLAN_KEY


def is_pending_payment(user: Optional[dict]) -> bool:
    return bool(user) and str(user.get("signup_status") or "") == "pending_payment"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_self_serve_user_record(*, password_hash: str, email: str,
                               first_name: str, last_name: str,
                               company: str, question: str,
                               came_from: str) -> dict:
    """The users.json record for a self-serve Prometheus account. Every
    product flag is off except Prometheus; nothing is pre-granted."""
    return {
        "password_hash": password_hash,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "company": company,
        "department": "",
        "role": "user",
        "plan": PLAN_KEY,
        "signup_status": "pending_payment",
        "signup_created_at": _now_iso(),
        "signup_first_question": question[:300],
        "signup_came_from": came_from[:200],
        "signup_checkout_session": "",
        "signup_paid_at": "",
        "signup_autologin_used": False,
        # Wallet + billing: own wallet, auto-reload on, card saved by
        # the checkout webhook.
        "billing_source": "user",
        "company_billing_admin": False,
        "company_spend_scope": "inherit",
        "paying_customer": True,
        "billing_mode": "auto_reload",
        "auto_reload_threshold_usd": AUTO_RELOAD_THRESHOLD_USD,
        "auto_reload_amount_usd": AUTO_RELOAD_AMOUNT_USD,
        "wallet_balance_usd": 0.0,
        "wallet_transactions": [],
        # Internal credit allowance stays at 0 so every pull routes to
        # the wallet.
        "credits": 0,
        "credits_used": 0,
        "consulting_hour_pool": 0,
        "consulting_hour_pool_used": 0,
        "created_at": datetime.now().isoformat(),
        "last_login": None,
        "access_expires": None,
        # Explicit empty lists: zero profiles granted, and the
        # fleet-wide auto-add (allowed_categories match) never fires.
        "allowed_categories": [],
        "allowed_runs": [],
        "allowed_behavioral_categories": ["*"],
        "has_profile_iq_access": False,
        "has_subscriber_iq_access": False,
        "has_ecommerce_iq_access": False,
        "has_ticket_sales_iq_access": False,
        "has_hedge_fund_iq_access": False,
        "gets_hedge_fund_iq_emails": False,
        "hedge_fund_iq_tabs": [],
        "hedge_fund_iq_tickers": [],
        "hedge_fund_iq_data_cutoff": None,
        "analysis_iq_modules": [],
        "has_ticket_sales_tracker_access": False,
        "has_rankers_iq_access": False,
        "rankers_iq_options": [],
        "has_talent_fit_access": False,
        "has_sf_conversion_access": False,
        "sf_conversion_journeys": None,
        "has_flywheel_conversion_access": False,
        "has_flywheel_iq_access": False,
        "has_brand_partnership_iq_access": False,
        "brand_partnership_iq_journeys": None,
        "has_sentiment_iq_access": False,
        "has_journey_iq_access": False,
        "allowed_journey_iq_runs": [],
        "has_intent_iq_access": False,
        "allowed_intent_iq_runs": [],
        "has_share_of_time_access": False,
        "has_share_of_time_run_access": False,
        "has_blue_iq_access": False,
        "has_brand_tracking_iq_access": False,
        "has_impact_iq_access": False,
        "impact_iq_journeys": [],
        "has_trends_iq_access": False,
        "has_microdramas_iq_access": False,
        "allowed_trends_tabs": [],
        "allowed_rankers_tabs": [],
        "allowed_lenses": None,
        "has_chatbot_profile_iq_access": True,
        "prometheus_access": "full",
        "prometheus_mode": "both",
        "pay_per_use_enabled": True,
        "collab_team": [],
        "auto_access_new": {},
    }


# ---------------------------------------------------------------------------
# Signup route
# ---------------------------------------------------------------------------

def _base_url() -> str:
    try:
        from billing_routes import _dashboard_base_url
        return _dashboard_base_url()
    except Exception:
        return (request.url_root or "").rstrip("/")


def _clean(v, n=200) -> str:
    return str(v or "").strip()[:n]


@site_bp.route("/site/api/signup", methods=["POST"])
def site_signup():
    """Create the account and hand back the Stripe Checkout URL."""
    try:
        from app import load_users, _users_cas_mutate, hash_password, verify_password  # type: ignore
    except Exception as e:
        print(f"[site-signup] app import failed: {e}")
        return jsonify({"error": "unavailable"}), 500
    try:
        import billing  # type: ignore
    except Exception:
        return jsonify({"error": "payments_unavailable"}), 503

    body = request.get_json(silent=True) or {}
    first = _clean(body.get("first_name"), 80)
    last = _clean(body.get("last_name"), 80)
    email = _clean(body.get("email"), 160).lower()
    company = _clean(body.get("company"), 120)
    password = str(body.get("password") or "")
    question = _clean(body.get("question"), 300)
    came_from = _clean(body.get("came_from"), 200)

    if not first or not last:
        return jsonify({"error": "Enter your first and last name."}), 400
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid work email."}), 400
    if email.rsplit("@", 1)[-1] in _FREE_MAIL:
        return jsonify({"error": "Use your work email address."}), 400
    if not company:
        return jsonify({"error": "Enter your company."}), 400
    if len(password) < 12:
        return jsonify({"error": "Choose a password of at least 12 characters."}), 400
    if not billing.is_enabled():
        return jsonify({"error": "payments_unavailable"}), 503

    username = email
    data = load_users() or {}
    existing = (data.get("users") or {}).get(username)
    if existing and not is_pending_payment(existing):
        return jsonify({
            "error": "account_exists",
            "message": "You already have a dashboard account. Log in instead.",
            "login_url": f"{_base_url()}/login",
        }), 409
    if existing and is_pending_payment(existing):
        # Resuming an unpaid signup: the password must match what they
        # set the first time, otherwise treat it as a fresh attempt on
        # the same email is not allowed.
        if not verify_password(existing.get("password_hash", ""), password):
            return jsonify({
                "error": "Your earlier signup is waiting on payment. "
                         "Use the same password to continue.",
            }), 400

    # Stripe customer first so the record carries the id from birth.
    try:
        cus_id = billing.ensure_customer(
            username, email=email, name=f"{first} {last}".strip(),
            existing_customer_id=str((existing or {}).get("stripe_customer_id") or ""))
    except billing.BillingDisabled:
        return jsonify({"error": "payments_unavailable"}), 503
    except billing.BillingError as e:
        print(f"[site-signup] ensure_customer failed: {e}")
        return jsonify({"error": "payments_unavailable"}), 502

    pw_hash = hash_password(password)

    def _create(doc):
        users = doc.setdefault("users", {})
        rec = users.get(username)
        if rec and not is_pending_payment(rec):
            return None
        if not rec:
            rec = new_self_serve_user_record(
                password_hash=pw_hash, email=email, first_name=first,
                last_name=last, company=company, question=question,
                came_from=came_from)
            users[username] = rec
        else:
            rec.update({
                "first_name": first, "last_name": last, "company": company,
                "signup_first_question": question[:300],
                "signup_came_from": came_from[:200],
            })
        if cus_id:
            rec["stripe_customer_id"] = cus_id
        return doc

    if _users_cas_mutate(_create) is None:
        return jsonify({
            "error": "account_exists",
            "message": "You already have a dashboard account. Log in instead.",
            "login_url": f"{_base_url()}/login",
        }), 409

    base = _base_url()
    success_url = f"{base}/site/welcome.html?sid={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base}/site/signup.html?cancelled=1"
    try:
        sess = billing.create_checkout_session(
            customer_id=cus_id,
            amount_usd=OPENING_BALANCE_USD,
            success_url=success_url,
            cancel_url=cancel_url,
            username=username,
            metadata={
                "subject_kind": "user",
                "subject_key": username,
                "billed_via_username": username,
                "enable_auto_reload": "1",
                "source": SIGNUP_SOURCE,
                "description": "Opening balance",
                "first_question": question[:200],
                "came_from": came_from[:120],
                "company": company[:120],
            },
        )
    except billing.BillingError as e:
        print(f"[site-signup] checkout session failed: {e}")
        return jsonify({"error": "payments_unavailable"}), 502

    sid = str(sess.get("id") or "")

    def _stamp(doc):
        rec = (doc.get("users") or {}).get(username)
        if not rec:
            return None
        rec["signup_checkout_session"] = sid
        return doc

    try:
        _users_cas_mutate(_stamp)
    except Exception:
        traceback.print_exc()

    print(f"[site-signup] created {username} ({company}); checkout {sid}")
    return jsonify({"url": sess.get("url"), "session_id": sid})


# ---------------------------------------------------------------------------
# Activation (called from the Stripe webhook)
# ---------------------------------------------------------------------------

def activate_after_payment(username: str, metadata: dict,
                           amount_usd: float, new_balance_usd: float,
                           card_brand: str = "", card_last4: str = "") -> None:
    """Flip the account live and notify the team. Never raises."""
    try:
        from app import _users_cas_mutate  # type: ignore
    except Exception as e:
        print(f"[site-signup] activate: app import failed: {e}")
        return
    snap = {}

    def _activate(doc):
        rec = (doc.get("users") or {}).get(username)
        if not rec:
            return None
        changed = False
        if rec.get("signup_status") != "active":
            rec["signup_status"] = "active"
            rec["signup_paid_at"] = _now_iso()
            changed = True
        if rec.get("plan") != PLAN_KEY:
            rec["plan"] = PLAN_KEY
            changed = True
        snap.update(rec)
        return doc if changed else None

    try:
        _users_cas_mutate(_activate)
    except Exception:
        traceback.print_exc()
    if not snap:
        print(f"[site-signup] activate: no record for {username}")
        return
    md = metadata if isinstance(metadata, dict) else {}
    _send_signup_notice_async(
        rec=snap, username=username, amount_usd=amount_usd,
        new_balance_usd=new_balance_usd, card_brand=card_brand,
        card_last4=card_last4,
        first_question=str(md.get("first_question")
                           or snap.get("signup_first_question") or ""),
        came_from=str(md.get("came_from")
                      or snap.get("signup_came_from") or ""),
    )


def _fmt_usd(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def build_signup_notice(*, rec: dict, username: str, amount_usd: float,
                        new_balance_usd: float, card_brand: str,
                        card_last4: str, first_question: str,
                        came_from: str) -> Tuple[str, str]:
    name = f"{rec.get('first_name', '')} {rec.get('last_name', '')}".strip() or username
    company = str(rec.get("company") or "").strip() or "(no company given)"
    when = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    card = f"{card_brand} ending {card_last4}".strip() if card_last4 else "card on file"
    subject = f"New dashboard signup: {company} paid the {_fmt_usd(amount_usd)} opening balance"
    lines = [
        f"{name} at {company} signed up for the dashboard on {when} and paid the "
        f"{_fmt_usd(amount_usd)} opening balance. The account is live on the "
        f"Prometheus plan.",
        "",
        f"Name: {name}",
        f"Work email: {rec.get('email') or username}",
        f"Company: {company}",
        f"Opening balance: {_fmt_usd(amount_usd)}, charged, {card}",
        f"Balance now: {_fmt_usd(new_balance_usd)}",
        "Plan: Prometheus only. Reports unlock as pulled. Auto top-up "
        f"{_fmt_usd(AUTO_RELOAD_AMOUNT_USD)} when the balance reaches "
        f"{_fmt_usd(AUTO_RELOAD_THRESHOLD_USD)}.",
    ]
    if first_question:
        lines.append(f"First question: {first_question}")
    if came_from:
        lines.append(f"Came from: {came_from}")
    lines += [
        "",
        f"Open the user in Admin to add access or change the top-up rule: "
        f"{_safe_base()}/admin/billing?user={username}",
        "",
        "Crosswalk",
        "Sent by the dashboard when a self-serve signup completes payment.",
    ]
    return subject, "\n".join(lines)


def _safe_base() -> str:
    try:
        from billing_routes import _dashboard_base_url
        return _dashboard_base_url()
    except Exception:
        return "https://dashboard.crosswalknyc.com"


def _send_signup_notice_async(**kw) -> None:
    subject, body = build_signup_notice(**kw)

    def _run():
        try:
            import boto3
            ses = boto3.client("ses", region_name=AWS_REGION)
            ses.send_email(
                Source=EMAIL_SOURCE,
                Destination={"ToAddresses": list(SIGNUP_NOTICE_TO)},
                Message={"Subject": {"Data": subject},
                         "Body": {"Text": {"Data": body}}},
            )
            print(f"[site-signup] notice sent to {SIGNUP_NOTICE_TO}")
        except Exception as e:
            print(f"[site-signup] notice send failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Welcome page poll + one-time auto login
# ---------------------------------------------------------------------------

@site_bp.route("/site/api/signup/status", methods=["GET"])
def site_signup_status():
    sid = _clean(request.args.get("sid"), 200)
    if not sid.startswith("cs_"):
        return jsonify({"paid": False, "error": "bad_session"}), 400
    try:
        from app import load_users, _users_cas_mutate, _normalize_role  # type: ignore
    except Exception:
        return jsonify({"paid": False}), 500
    data = load_users() or {}
    found = None
    for uname, rec in (data.get("users") or {}).items():
        if isinstance(rec, dict) and rec.get("signup_checkout_session") == sid:
            found = (uname, rec)
            break
    if not found:
        return jsonify({"paid": False, "known": False})
    uname, rec = found
    if rec.get("signup_status") != "active":
        return jsonify({"paid": False, "known": True})

    logged_in = False
    if session.get("username") == uname:
        logged_in = True
    elif not rec.get("signup_autologin_used"):
        # One-time login straight from the Stripe return, only inside
        # the first two hours after payment.
        try:
            paid_at = datetime.fromisoformat(str(rec.get("signup_paid_at")))
            fresh = (datetime.now(timezone.utc) - paid_at).total_seconds() < 7200
        except Exception:
            fresh = False
        if fresh:
            def _mark(doc):
                r = (doc.get("users") or {}).get(uname)
                if not r or r.get("signup_autologin_used"):
                    return None
                r["signup_autologin_used"] = True
                r["last_login"] = datetime.now().isoformat()
                return doc
            try:
                if _users_cas_mutate(_mark) is not None:
                    session["username"] = uname
                    session["role"] = _normalize_role(rec.get("role", "user"))
                    logged_in = True
            except Exception:
                traceback.print_exc()
    return jsonify({
        "paid": True,
        "known": True,
        "logged_in": logged_in,
        "balance_usd": float(rec.get("wallet_balance_usd") or 0.0),
        "first_question": rec.get("signup_first_question") or "",
    })


# ---------------------------------------------------------------------------
# Grant a pulled file to the user who pulled it
# ---------------------------------------------------------------------------

def grant_runs_to_user(username: str, s3_keys) -> bool:
    """Append profile keys to an explicit-list user's allowed_runs.
    No-op for '*' users. Used by app.py when a Prometheus pull lands
    (queue completion or an existing-library match)."""
    keys = [str(k).strip() for k in (s3_keys or []) if k]
    if not username or not keys:
        return False
    try:
        from app import _users_cas_mutate  # type: ignore
    except Exception:
        return False
    changed = {"v": False}

    def _mut(doc):
        u = (doc.get("users") or {}).get(username)
        if not isinstance(u, dict):
            return None
        cur = u.get("allowed_runs")
        if not isinstance(cur, list) or "*" in cur:
            return None
        add = [k for k in keys if k not in cur]
        if not add:
            return None
        u["allowed_runs"] = cur + add
        changed["v"] = True
        return doc

    try:
        _users_cas_mutate(_mut)
    except Exception:
        traceback.print_exc()
        return False
    if changed["v"]:
        print(f"[site-signup] granted {keys} to {username}")
    return changed["v"]


def register_site_blueprint(app) -> None:
    try:
        app.register_blueprint(site_bp)
        print("✅ Site blueprint registered (/site)")
    except Exception as e:
        print(f"⚠️ Site blueprint registration failed: {e}")
