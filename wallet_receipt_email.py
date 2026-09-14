"""Wallet top-up receipt + internal notification emails (2026-09-09).

Jenna 2026-09-09 (verbatim):
    "when someone buys credits it should email them a reciept does it?
    also it should send a system email to me and liz and czarina
    showing who bought it and the amount spent"

Prior to this module, a successful wallet top-up produced no email at
all. Stripe's built-in "Successful payment receipts" setting (if
enabled at the Stripe dashboard level) may send a generic branded
receipt, but nobody at Crosswalk got notified when a purchase landed
and the buyer never got a Crosswalk-branded confirmation with their
new balance. This module fills both gaps.

Two emails per top-up, both fire-and-forget:

    send_topup_receipt(...)          -> buyer's email address
    send_topup_internal_notice(...)  -> jenna@ + liz@ + czarina@

Wiring lives in `billing_routes.py`. Both webhook handlers
(`_handle_checkout_session_completed` + `_handle_payment_intent_succeeded`)
call these after a successful credit lands. The purpose filter in the
PI handler skips `wallet_topup` so a Hosted Checkout doesn't double
email (session.completed already sent).

Recipient rule note (per `profile-iq-pipeline-rules.mdc`):
    - The internal notice is a WORKFLOW email (billing / CRM), not a
      failure alert. Jenna's explicit list is me + Liz + Czarina.
      Liz is intentionally IN scope here (see 2026-09-09 mandate);
      the 2026-09-03 "no Liz on failure/system alerts" rule does not
      apply to workflow emails like this one.

No em dashes anywhere in this module. All emails are plain text.
Never raises: SES failure logs and swallows so the webhook still
returns 200 to Stripe.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Iterable, Optional, Tuple


EMAIL_SOURCE = 'BehavioralGraph <jenna@crosswalknyc.com>'
AWS_REGION = 'us-east-2'

# Jenna 2026-09-09 (verbatim): "it should send a system email to me
# and liz and czarina showing who bought it and the amount spent".
# This is a workflow / billing notification, so Liz is IN scope here
# per the recipient-intent rule in profile-iq-pipeline-rules.mdc
# section 6 (workflow = keep Liz; failure alerts = drop Liz).
INTERNAL_TO: Tuple[str, ...] = (
    'jenna@crosswalknyc.com',
    'liz@crosswalknyc.com',
    'czarina@crosswalknyc.com',
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_usd(amount) -> str:
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _fmt_ts(ts_iso: Optional[str] = None) -> str:
    """Render a UTC timestamp readable to non-engineers.

    Uses %I / %M / %d without the platform-specific `-` no-pad flag so
    it renders identically on Render (Linux) and dev laptops (macOS).
    """
    dt = None
    if ts_iso:
        try:
            dt = datetime.fromisoformat(
                str(ts_iso).replace('Z', '+00:00'))
        except Exception:
            dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime('%B %d, %Y at %H:%M UTC')


def _clean(s) -> str:
    return str(s or '').strip()


# ---------------------------------------------------------------------------
# SES wrapper
# ---------------------------------------------------------------------------

def _send_ses(subject: str, body: str, to: Iterable[str]) -> bool:
    """Actual SES send. Called from a daemon thread so a slow SES
    response never blocks the webhook return."""
    recipients = [r for r in (to or []) if r]
    if not recipients:
        print("[wallet-receipt] no recipients; skipping send")
        return False
    try:
        import boto3
        ses = boto3.client('ses', region_name=AWS_REGION)
        ses.send_email(
            Source=EMAIL_SOURCE,
            Destination={'ToAddresses': list(recipients)},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Text': {'Data': body}},
            },
        )
        print(f"[wallet-receipt] sent {subject!r} to {recipients}")
        return True
    except Exception as e:
        print(f"[wallet-receipt] SES send failed for "
              f"{subject!r}: {e}")
        return False


def _send_async(subject: str, body: str,
                to: Iterable[str]) -> None:
    """Fire and forget. Daemon thread so a slow SES call never holds
    up the Stripe webhook return (Stripe times out after ~10s)."""
    threading.Thread(
        target=_send_ses,
        args=(subject, body, tuple(to or ())),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_KIND_TO_BUYER_HEADER = {
    'topup': "Thanks for your Crosswalk wallet top-up.",
    'auto_reload': "Your Crosswalk wallet auto-reloaded.",
    'admin_charge': "A charge has been applied to your saved card.",
    'monthly_invoice': "Your Crosswalk monthly invoice was paid.",
}

_KIND_TO_BUYER_BODY_INTRO = {
    'topup':
        "You added funds to your Crosswalk dashboard wallet.",
    'auto_reload':
        "Your Crosswalk wallet balance dropped below your "
        "auto-reload threshold, so we charged your saved card and "
        "credited the difference.",
    'admin_charge':
        "A Crosswalk super admin charged your saved card and "
        "credited your wallet. If this was not expected, reply to "
        "this email and we will sort it out.",
    'monthly_invoice':
        "Your accrued Crosswalk usage for the month was charged to "
        "your saved card.",
}

_KIND_TO_INTERNAL_LABEL = {
    'topup': 'prepay top-up',
    'auto_reload': 'auto-reload charge',
    'admin_charge': 'admin custom charge',
    'monthly_invoice': 'monthly invoice',
    'adjustment': 'manual admin adjustment (no card charge)',
}


def send_topup_receipt(*,
                       buyer_email: str,
                       buyer_display_name: str = '',
                       amount_usd: float,
                       new_balance_usd: float,
                       stripe_ref: str = '',
                       card_brand: str = '',
                       card_last4: str = '',
                       kind: str = 'topup',
                       ts_iso: str = '') -> None:
    """Send a Crosswalk-branded receipt to the buyer.

    kind: one of 'topup' | 'auto_reload' | 'admin_charge' |
    'monthly_invoice'. 'adjustment' (no card charge) is not sent as a
    receipt on the buyer side; use send_topup_internal_notice for
    those.
    """
    buyer_email = _clean(buyer_email)
    if not buyer_email:
        print("[wallet-receipt] no buyer email; skipping receipt")
        return
    amt = _fmt_usd(amount_usd)
    bal = _fmt_usd(new_balance_usd)
    ts_str = _fmt_ts(ts_iso)

    header = _KIND_TO_BUYER_HEADER.get(
        kind, _KIND_TO_BUYER_HEADER['topup'])
    intro = _KIND_TO_BUYER_BODY_INTRO.get(
        kind, _KIND_TO_BUYER_BODY_INTRO['topup'])

    name = _clean(buyer_display_name) or 'there'
    card_line = ''
    if _clean(card_brand) and _clean(card_last4):
        card_line = (f"Payment method:  {card_brand.title()} "
                     f"ending in {card_last4}\n")

    subject = f"Crosswalk wallet receipt: {amt}"
    body = (
        f"Hi {name},\n\n"
        f"{header}\n\n"
        f"{intro}\n\n"
        f"Amount:          {amt}\n"
        f"Date:            {ts_str}\n"
        f"{card_line}"
        f"Confirmation:    {_clean(stripe_ref) or '(unavailable)'}\n"
        f"New balance:     {bal}\n\n"
        f"Manage your wallet at "
        f"https://dashboard.crosswalknyc.com/wallet\n\n"
        f"Questions? Reply to this email and we will help.\n\n"
        f"Crosswalk Technologies\n"
    )
    _send_async(subject, body, (buyer_email,))


def send_topup_internal_notice(*,
                               buyer_username: str = '',
                               buyer_email: str = '',
                               buyer_display_name: str = '',
                               buyer_company: str = '',
                               subject_kind: str = 'user',
                               subject_key: str = '',
                               amount_usd: float,
                               new_balance_usd: float,
                               stripe_ref: str = '',
                               kind: str = 'topup',
                               ts_iso: str = '',
                               card_brand: str = '',
                               card_last4: str = '') -> None:
    """Notify jenna@ + liz@ + czarina@ about a wallet top-up.

    Fire and forget. Never raises. `subject_kind` is 'user' or
    'company'; `subject_key` is the username or company name that
    holds the wallet.
    """
    amt = _fmt_usd(amount_usd)
    bal = _fmt_usd(new_balance_usd)
    ts_str = _fmt_ts(ts_iso)
    kind_label = _KIND_TO_INTERNAL_LABEL.get(kind, kind or 'top-up')

    if _clean(subject_kind).lower() == 'company':
        wallet_line = (f"Company wallet:  "
                       f"{_clean(subject_key) or '(unknown)'}")
    else:
        wallet_line = (f"User wallet:     "
                       f"{_clean(subject_key) or '(unknown)'}")

    buyer_line_bits = []
    disp = _clean(buyer_display_name)
    uname = _clean(buyer_username)
    if disp and uname and disp.lower() != uname.lower():
        buyer_line_bits.append(f"{disp} ({uname})")
    elif disp:
        buyer_line_bits.append(disp)
    elif uname:
        buyer_line_bits.append(uname)
    else:
        buyer_line_bits.append('(unknown)')
    buyer_line = ' '.join(buyer_line_bits)

    card_line = ''
    if _clean(card_brand) and _clean(card_last4):
        card_line = (f"Card:            {card_brand.title()} "
                     f"ending in {card_last4}\n")

    subject_line = (f"Wallet top-up: {amt} from "
                    f"{disp or uname or 'unknown buyer'}")
    body = (
        f"A wallet top-up just landed.\n\n"
        f"Amount:          {amt}\n"
        f"Kind:            {kind_label}\n"
        f"Buyer:           {buyer_line}\n"
        f"Buyer email:     {_clean(buyer_email) or '(unknown)'}\n"
        f"Company:         {_clean(buyer_company) or '(none)'}\n"
        f"{wallet_line}\n"
        f"New balance:     {bal}\n"
        f"Date:            {ts_str}\n"
        f"{card_line}"
        f"Reference:       "
        f"{_clean(stripe_ref) or '(unavailable)'}\n"
    )
    _send_async(subject_line, body, INTERNAL_TO)


__all__ = [
    'send_topup_receipt',
    'send_topup_internal_notice',
    'INTERNAL_TO',
    'EMAIL_SOURCE',
    'AWS_REGION',
]
