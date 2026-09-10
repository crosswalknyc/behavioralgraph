"""Stripe integration (2026-09-08).

Wraps every Stripe SDK call the dashboard needs so app.py routes and
webhook handlers stay clean. Never leaks card data (all card capture
happens client-side via Stripe Elements / Checkout).

Gated by STRIPE_ENABLED env var: when 'false' or unset, every function
either returns a benign disabled-marker or raises BillingDisabled. The
admin UI checks `is_enabled()` and shows "Billing not configured. Set
STRIPE_* env vars to enable." until Jenna's keys land.

Env vars read:
  STRIPE_ENABLED         'true' | 'false' (default 'false')
  STRIPE_SECRET_KEY      sk_test_... or sk_live_...
  STRIPE_PUBLISHABLE_KEY pk_test_... or pk_live_...
  STRIPE_WEBHOOK_SECRET  whsec_... (used by verify_webhook)

Public surface:
  is_enabled() -> bool
  publishable_key() -> str
  ensure_customer(username, email, name, users_data)
                        -> (customer_id, users_data_updated_flag)
  create_setup_intent(customer_id) -> {client_secret, id}
  attach_payment_method(customer_id, payment_method_id) -> pm_meta
  create_checkout_session(customer_id, amount_usd, success_url,
                          cancel_url, metadata) -> {id, url}
  charge_saved_card(customer_id, payment_method_id, amount_usd,
                    description, metadata) -> {id, status, ...}
  refund_payment(payment_intent_id, amount_usd=None, reason='')
                        -> {id, status, amount_refunded_usd}
  detach_payment_method(payment_method_id) -> {id, detached}
  verify_webhook(payload_bytes, signature_header) -> event_dict

None of these touch users.json directly. Callers wire them into
users.json mutations under _users_cas_mutate.
"""
from __future__ import annotations

import json
import os
from typing import Optional


class BillingDisabled(Exception):
    """Raised by any Stripe call when STRIPE_ENABLED is false or
    keys are unset. Callers should catch and return a partner-safe
    "billing not configured" response."""


class BillingError(Exception):
    """Wraps stripe.error.* into a single billing exception so the
    caller doesn't need to import stripe.error itself. Carries a
    partner-safe message. Internal details logged server-side only.
    """
    def __init__(self, message: str, *, stripe_code: str = "",
                 http_status: int = 0):
        super().__init__(message)
        self.stripe_code = stripe_code
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Env + SDK bootstrap
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    """True when Stripe env vars are set and the flag is on."""
    if not _env_bool("STRIPE_ENABLED", False):
        return False
    if not os.environ.get("STRIPE_SECRET_KEY"):
        return False
    return True


def publishable_key() -> str:
    """Client-side key for Stripe Elements. Empty string when
    disabled - the UI treats empty as "billing not configured"."""
    if not is_enabled():
        return ""
    return os.environ.get("STRIPE_PUBLISHABLE_KEY", "") or ""


def _stripe():
    """Lazy Stripe SDK import + key setup. Raises BillingDisabled
    when not configured. Kept lazy so the dashboard cold-start
    doesn't require the `stripe` pip package to be installed until
    somebody actually flips the flag on."""
    if not is_enabled():
        raise BillingDisabled(
            "Stripe not configured. Set STRIPE_ENABLED=true + keys.")
    try:
        import stripe  # type: ignore
    except ImportError as e:
        raise BillingDisabled(
            "stripe SDK not installed. `pip install stripe`.") from e
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    # Stripe API version - pinned so schema changes don't surprise us.
    # Update deliberately after reading the Stripe upgrade guide.
    stripe.api_version = "2024-11-20.acacia"
    return stripe


def _wrap_stripe_error(fn):
    """Decorator: convert Stripe SDK exceptions into our BillingError.

    Logs internals server-side (traceback + Stripe error code +
    request_id) but the raised message is partner-safe - see
    _partner_safe_msg().
    """
    from functools import wraps

    @wraps(fn)
    def _wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except BillingDisabled:
            raise
        except Exception as e:
            import traceback
            # Lazy import; stripe may not be installed in test env.
            try:
                import stripe as _s  # type: ignore
                _err_types = (
                    _s.error.CardError, _s.error.RateLimitError,
                    _s.error.InvalidRequestError,
                    _s.error.AuthenticationError,
                    _s.error.APIConnectionError,
                    _s.error.StripeError,
                )
            except Exception:
                _err_types = tuple()
            traceback.print_exc()
            code = ""
            status = 0
            if _err_types and isinstance(e, _err_types):
                code = getattr(e, "code", "") or ""
                status = getattr(e, "http_status", 0) or 0
                msg = _partner_safe_msg(code, status,
                                        str(e))
            else:
                msg = "Billing request failed. Please retry."
            raise BillingError(msg, stripe_code=code,
                               http_status=status) from e
    return _wrapper


def _partner_safe_msg(code: str, status: int, raw: str) -> str:
    """Map Stripe error codes to short, user-safe messages. Never
    quotes the raw exception - that leaks Stripe internals."""
    if code == "card_declined":
        return "Your card was declined. Please try a different card."
    if code == "expired_card":
        return "Your card has expired. Please update it."
    if code == "incorrect_cvc":
        return "The CVC code is incorrect."
    if code == "processing_error":
        return ("There was an issue processing your card. Please "
                "try again.")
    if code == "insufficient_funds":
        return "Your card was declined for insufficient funds."
    if status == 401:
        return ("Billing is temporarily unavailable. Please contact "
                "support.")
    if status == 429:
        return "Too many requests. Please try again in a moment."
    return ("Billing request failed. Please try again or contact "
            "support.")


# ---------------------------------------------------------------------------
# Currency helpers (Stripe amounts are in the smallest currency unit,
# so USD -> cents integer)
# ---------------------------------------------------------------------------

def _to_cents(usd: float) -> int:
    """USD float -> integer cents. Rounds half-even; refuses values
    below $0.50 because Stripe's minimum charge is $0.50."""
    cents = int(round(float(usd) * 100))
    if cents < 50:
        raise BillingError(
            f"Amount ${usd:.2f} is below the $0.50 Stripe minimum.")
    return cents


def _from_cents(cents: int) -> float:
    return round(int(cents) / 100.0, 2)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@_wrap_stripe_error
def ensure_customer(username: str, email: str, name: str,
                    existing_customer_id: str = "") -> str:
    """Return the Stripe Customer id for this dashboard user. Creates
    one when none exists. The caller is responsible for persisting
    the returned id onto the user record.

    Idempotency: we pass the dashboard username as the Stripe
    Idempotency-Key so two concurrent creates for the same user
    resolve to the same Customer.
    """
    s = _stripe()
    if existing_customer_id:
        # Verify the Customer still exists; if Stripe returns not
        # found (e.g. object deleted in a Stripe dashboard cleanup)
        # we fall through to create a fresh one.
        try:
            c = s.Customer.retrieve(existing_customer_id)
            if not getattr(c, "deleted", False):
                return c.id
        except Exception:
            pass
    idem = f"dashboard-user-create-{username}"
    c = s.Customer.create(
        email=email or None,
        name=name or username,
        metadata={"dashboard_username": username},
        idempotency_key=idem,
    )
    return c.id


# ---------------------------------------------------------------------------
# Saving a card (SetupIntent flow: no charge, just capture + attach)
# ---------------------------------------------------------------------------

@_wrap_stripe_error
def create_setup_intent(customer_id: str) -> dict:
    """Create a SetupIntent for a customer to save a card without
    charging. The client-side Stripe Elements form uses the returned
    client_secret to confirm the setup.

    Returns {'id': 'seti_...', 'client_secret': 'seti_..._secret_...'}.
    """
    if not customer_id:
        raise BillingError("Missing customer id.")
    s = _stripe()
    si = s.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
        usage="off_session",  # for later auto-reload charges
    )
    return {"id": si.id, "client_secret": si.client_secret}


@_wrap_stripe_error
def attach_payment_method(customer_id: str,
                         payment_method_id: str) -> dict:
    """Attach a PaymentMethod to a Customer and set as default. Runs
    server-side after the client's Elements form confirmed the
    SetupIntent. Returns display metadata for the saved card.
    """
    if not customer_id or not payment_method_id:
        raise BillingError("Missing customer or payment_method id.")
    s = _stripe()
    pm = s.PaymentMethod.attach(payment_method_id,
                                customer=customer_id)
    s.Customer.modify(customer_id, invoice_settings={
        "default_payment_method": payment_method_id,
    })
    card = getattr(pm, "card", None)
    return {
        "id": pm.id,
        "last4": (card.last4 if card else "") or "",
        "brand": (card.brand if card else "") or "",
        "exp_month": (card.exp_month if card else 0) or 0,
        "exp_year": (card.exp_year if card else 0) or 0,
    }


@_wrap_stripe_error
def detach_payment_method(payment_method_id: str) -> dict:
    """Remove a saved card from a Customer (admin action)."""
    if not payment_method_id:
        return {"id": "", "detached": False}
    s = _stripe()
    pm = s.PaymentMethod.detach(payment_method_id)
    return {"id": pm.id, "detached": True}


# ---------------------------------------------------------------------------
# One-time prepay top-up (Checkout Session)
# ---------------------------------------------------------------------------

@_wrap_stripe_error
def create_checkout_session(customer_id: str, amount_usd: float,
                            success_url: str, cancel_url: str,
                            username: str,
                            metadata: Optional[dict] = None) -> dict:
    """Create a hosted Checkout Session for a prepay top-up.

    The user clicks "Add Funds" -> we call this -> we redirect to
    session.url. Stripe hosts the payment form. On success Stripe
    redirects to success_url and fires checkout.session.completed
    webhook, which credits the wallet.

    metadata is merged into the session's metadata so the webhook
    handler knows which user + which amount to credit. We always
    include 'dashboard_username' and 'topup_usd'.
    """
    if not customer_id:
        raise BillingError("Missing customer id.")
    if amount_usd <= 0:
        raise BillingError("Top-up amount must be positive.")
    cents = _to_cents(amount_usd)
    s = _stripe()
    md = dict(metadata or {})
    md.setdefault("dashboard_username", username)
    md.setdefault("topup_usd", f"{amount_usd:.2f}")
    md.setdefault("purpose", "wallet_topup")
    sess = s.checkout.Session.create(
        customer=customer_id,
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": "Crosswalk wallet top-up",
                    "description": (
                        f"Add ${amount_usd:,.2f} to your Crosswalk "
                        f"dashboard wallet."),
                },
                "unit_amount": cents,
            },
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=md,
        payment_intent_data={"metadata": md},
        # Save the card in this checkout to the customer so future
        # auto-reload works without a separate SetupIntent step.
        # Applies only when the checkout mode == 'payment'.
        payment_method_options={
            "card": {"setup_future_usage": "off_session"},
        },
    )
    return {"id": sess.id, "url": sess.url}


@_wrap_stripe_error
def create_guest_checkout_session(amount_usd: float, email: str,
                                  success_url: str, cancel_url: str,
                                  product_name: str,
                                  metadata: Optional[dict] = None) -> dict:
    """Hosted Checkout for someone who is not a dashboard user.

    Used by Newsletter paid downloads. No Stripe Customer is created.
    metadata.purpose should be newsletter_download so the webhook
    does not credit a wallet.
    """
    if amount_usd <= 0:
        raise BillingError("Amount must be positive.")
    email = (email or "").strip()
    if not email:
        raise BillingError("Email is required.")
    cents = _to_cents(amount_usd)
    s = _stripe()
    md = dict(metadata or {})
    md.setdefault("purpose", "newsletter_download")
    sess = s.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=email,
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": "usd",
                "product_data": {
                    "name": (product_name or "Crosswalk report")[:120],
                },
                "unit_amount": cents,
            },
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=md,
        payment_intent_data={"metadata": md},
    )
    return {"id": sess.id, "url": sess.url}


@_wrap_stripe_error
def retrieve_checkout_session(session_id: str) -> dict:
    """Fetch a Checkout Session. Used to unlock a paid download."""
    if not session_id:
        raise BillingError("Missing session id.")
    s = _stripe()
    sess = s.checkout.Session.retrieve(session_id)
    return {
        "id": sess.id,
        "status": getattr(sess, "status", "") or "",
        "payment_status": getattr(sess, "payment_status", "") or "",
        "customer_email": getattr(sess, "customer_email", "") or "",
        "amount_total": int(getattr(sess, "amount_total", 0) or 0),
        "metadata": dict(getattr(sess, "metadata", None) or {}),
    }


# ---------------------------------------------------------------------------
# Off-session charge (auto-reload, admin custom charge)
# ---------------------------------------------------------------------------

@_wrap_stripe_error
def charge_saved_card(customer_id: str, payment_method_id: str,
                      amount_usd: float, description: str,
                      username: str,
                      metadata: Optional[dict] = None) -> dict:
    """Off-session charge against a previously-saved card.

    Used by:
      1. Auto-reload trigger (post-deduction, when balance <= threshold).
      2. Monthly invoice cron (last day of the month, charge the
         accumulated Prometheus usage).
      3. Admin "charge custom amount" button.

    Idempotency: caller may pass metadata['idempotency_key']; if set
    Stripe folds duplicate calls under the same key into one charge.
    Recommended for cron jobs (month + username) and for admin
    charges (button click id).
    """
    if not customer_id or not payment_method_id:
        raise BillingError("Missing customer or payment_method id.")
    if amount_usd <= 0:
        raise BillingError("Charge amount must be positive.")
    cents = _to_cents(amount_usd)
    md = dict(metadata or {})
    md.setdefault("dashboard_username", username)
    md.setdefault("charge_usd", f"{amount_usd:.2f}")
    idem = md.pop("idempotency_key", None)
    s = _stripe()
    kwargs = dict(
        amount=cents,
        currency="usd",
        customer=customer_id,
        payment_method=payment_method_id,
        off_session=True,
        confirm=True,
        description=description,
        metadata=md,
    )
    if idem:
        kwargs["idempotency_key"] = idem
    pi = s.PaymentIntent.create(**kwargs)
    return {
        "id": pi.id,
        "status": pi.status,
        "amount_usd": _from_cents(pi.amount),
        "charge_id": (pi.latest_charge if isinstance(pi.latest_charge,
                                                     str)
                      else getattr(pi.latest_charge, "id", "")),
    }


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

@_wrap_stripe_error
def refund_payment(payment_intent_id: str, amount_usd: Optional[float] = None,
                   reason: str = "") -> dict:
    """Refund a previous PaymentIntent, full or partial. Reason is
    optional. Stripe fires charge.refunded webhook on success.

    NOTE: this only issues the Stripe refund. Wallet-side deduction of
    the refunded amount is handled by the charge.refunded webhook
    handler in app.py so the flow is uniform whether the refund
    originated from Stripe dashboard, an admin click here, or Stripe
    Radar."""
    if not payment_intent_id:
        raise BillingError("Missing payment_intent id.")
    s = _stripe()
    kwargs = {"payment_intent": payment_intent_id}
    if amount_usd is not None:
        kwargs["amount"] = _to_cents(amount_usd)
    if reason:
        kwargs["reason"] = "requested_by_customer"
    r = s.Refund.create(**kwargs)
    return {
        "id": r.id,
        "status": r.status,
        "amount_refunded_usd": _from_cents(r.amount),
    }


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------

def verify_webhook(payload_bytes: bytes, signature_header: str) -> dict:
    """Verify + parse a Stripe webhook payload. Returns the parsed
    Event as a dict.

    Raises BillingError on bad signature (401 semantics) or malformed
    payload (400 semantics). Callers should NOT catch the raised
    exception broadly; they should return the appropriate HTTP status
    to Stripe so the retry policy fires correctly.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise BillingError(
            "Webhook secret not configured.",
            stripe_code="missing_webhook_secret", http_status=500)
    if not signature_header:
        raise BillingError("Missing signature.",
                           stripe_code="missing_signature",
                           http_status=400)
    try:
        s = _stripe()
    except BillingDisabled as e:
        # Stripe SDK missing or STRIPE_ENABLED off. Still try to parse
        # the payload defensively for debugging - but never trust it.
        raise BillingError(str(e), stripe_code="disabled",
                           http_status=500) from e
    try:
        event = s.Webhook.construct_event(
            payload_bytes, signature_header, secret)
    except ValueError as e:
        raise BillingError("Invalid payload.",
                           stripe_code="bad_payload",
                           http_status=400) from e
    except Exception as e:
        # SignatureVerificationError and friends.
        raise BillingError("Invalid signature.",
                           stripe_code="bad_signature",
                           http_status=400) from e
    # Convert to plain dict for downstream handlers (avoid holding a
    # live Stripe object across an idempotency S3 write).
    try:
        as_dict = json.loads(json.dumps(event, default=lambda o:
                                        getattr(o, "__dict__", str(o))))
    except Exception:
        as_dict = dict(event) if isinstance(event, dict) else \
            {"id": getattr(event, "id", ""),
             "type": getattr(event, "type", ""),
             "data": {}}
    return as_dict


# ---------------------------------------------------------------------------
# Introspection helpers used by the admin UI
# ---------------------------------------------------------------------------

@_wrap_stripe_error
def retrieve_payment_method(payment_method_id: str) -> dict:
    """Get display metadata for a saved card (last4, brand, expiry).
    Returns empty dict when the id doesn't exist or is detached."""
    if not payment_method_id:
        return {}
    s = _stripe()
    try:
        pm = s.PaymentMethod.retrieve(payment_method_id)
    except Exception:
        return {}
    card = getattr(pm, "card", None)
    if not card:
        return {}
    return {
        "id": pm.id,
        "last4": card.last4 or "",
        "brand": card.brand or "",
        "exp_month": card.exp_month or 0,
        "exp_year": card.exp_year or 0,
    }


__all__ = [
    "BillingDisabled", "BillingError",
    "is_enabled", "publishable_key",
    "ensure_customer",
    "create_setup_intent", "attach_payment_method",
    "detach_payment_method",
    "create_checkout_session", "charge_saved_card",
    "refund_payment",
    "verify_webhook", "retrieve_payment_method",
]
