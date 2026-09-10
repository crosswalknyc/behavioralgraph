"""Admin-generated wallet top-up links.

Jenna 2026-09-10 (verbatim): *"add where you can click to generate a
link for a user to click on to add money to their account. so if I
create an account I dont have to ask them for their credit card they
can do it themselves. I would just click to generate it or soething
from their tab"*

An admin mints a link from the user's tab, sends it over, and the
recipient adds their own card on Stripe's hosted form. Nobody has to
read a card number over the phone or paste one into a chat.

WHY A TOKEN AND NOT A RAW STRIPE URL. A Stripe Checkout Session URL
expires 24h after creation and is single-use, so a link emailed on a
Friday is dead by Monday. A token here is durable (default 30 days,
reusable, revocable) and the Checkout Session is minted fresh at the
moment the recipient clicks Continue.

WHY THIS WORKS WITHOUT A LOGIN. The `checkout.session.completed`
webhook in billing_routes resolves which wallet to credit from the
session's `subject_kind` / `subject_key` metadata, not from a browser
session. So a recipient who has never signed into the dashboard, or
who does not have a password yet, can still fund the right account.

SECURITY POSTURE. The token is the only credential, so:
  * 32-byte urlsafe token (~256 bits). Not guessable.
  * The token, not the request body, is the sole source of the target
    account. A caller cannot redirect funds by tampering with a
    payload. See mint_checkout_for_token in billing_routes.
  * `amount_locked` links ignore any amount in the request body.
  * The public page never renders an email address, a balance, or a
    transaction history. Only the display name of the account being
    funded, so the payer can confirm they are funding the right one.
  * Expiry and revocation are checked on every single read.

Storage is one S3 JSON doc under the same ETag-CAS helper the rest of
the system state uses, so two admins minting links at once cannot lose
each other's writes.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

S3_KEY = "system/billing/payment_links.json"

DEFAULT_TTL_DAYS = 30
TOKEN_BYTES = 32
# Guardrails on a preset amount. The floor is re-checked against
# wallet.top_up_min_custom() at mint time; this is just a sane bound.
MAX_AMOUNT_USD = 100_000.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        txt = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _bucket():
    from app import METADATA_BUCKET  # type: ignore
    return METADATA_BUCKET


def _update(mutate_fn):
    """ETag-guarded read-modify-write on the links doc."""
    try:
        from migration.s3_json_state import update_json  # type: ignore
    except ImportError:
        from s3_json_state import update_json  # type: ignore
    return update_json(
        _bucket(), S3_KEY, mutate_fn,
        default={"links": {}},
        put_extra_args={"CacheControl": "no-cache, max-age=0"})


def _read() -> dict:
    """Read the links doc. Returns {'links': {}} when absent."""
    try:
        from migration.s3_json_state import read_json_with_etag  # type: ignore
    except ImportError:
        from s3_json_state import read_json_with_etag  # type: ignore
    try:
        doc, _etag = read_json_with_etag(_bucket(), S3_KEY)
    except Exception as e:
        print(f"[payment_links] read failed: {e}")
        return {"links": {}}
    if not isinstance(doc, dict):
        return {"links": {}}
    if not isinstance(doc.get("links"), dict):
        doc["links"] = {}
    return doc


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------

def create_link(subject_kind: str, subject_key: str,
                display_name: str = "",
                amount_usd: Optional[float] = None,
                amount_locked: bool = False,
                created_by: str = "",
                ttl_days: int = DEFAULT_TTL_DAYS,
                single_use: bool = False,
                note: str = "") -> dict:
    """Mint a top-up link for one billing subject.

    `amount_usd=None` means the recipient chooses. A preset amount is
    a SUGGESTION the recipient can edit unless `amount_locked` is set.

    Returns the stored record (including its `token`).
    """
    subject_kind = "company" if subject_kind == "company" else "user"
    subject_key = str(subject_key or "").strip()
    if not subject_key:
        raise ValueError("subject_key is required")

    amt: Optional[float] = None
    if amount_usd is not None:
        try:
            amt = round(float(amount_usd), 2)
        except (TypeError, ValueError):
            raise ValueError("amount_usd must be a number")
        if amt <= 0:
            amt = None
        elif amt > MAX_AMOUNT_USD:
            raise ValueError("amount_usd above maximum")

    try:
        ttl = int(ttl_days)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_DAYS
    ttl = max(1, min(ttl, 365))

    token = secrets.token_urlsafe(TOKEN_BYTES)
    now = _now()
    rec = {
        "token": token,
        "subject_kind": subject_kind,
        "subject_key": subject_key,
        "display_name": str(display_name or subject_key)[:160],
        "amount_usd": amt,
        "amount_locked": bool(amount_locked and amt),
        "single_use": bool(single_use),
        "created_by": str(created_by or "")[:120],
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(days=ttl)),
        "revoked": False,
        "revoked_at": "",
        "uses": 0,
        "total_paid_usd": 0.0,
        "last_used_at": "",
        "note": str(note or "")[:300],
    }

    def _apply(doc):
        links = doc.setdefault("links", {})
        links[token] = rec
        _prune(links)
        doc["last_updated"] = _iso(_now())
        return doc

    _update(_apply)
    return dict(rec)


def _prune(links: dict, keep_days: int = 120) -> None:
    """Drop records that expired a long time ago so the doc does not
    grow without bound. Anything still live, or recently expired, is
    kept so the admin UI can still show history and so a late webhook
    can still be attributed."""
    cutoff = _now() - timedelta(days=keep_days)
    for tok in list(links.keys()):
        rec = links.get(tok) or {}
        exp = _parse_iso(str(rec.get("expires_at") or ""))
        if exp and exp < cutoff:
            links.pop(tok, None)


# ---------------------------------------------------------------------------
# Read + validate
# ---------------------------------------------------------------------------

def get_link(token: str) -> Optional[dict]:
    """Return the raw record for a token, or None when unknown.

    Does NOT validate expiry / revocation - use `validate` for that.
    """
    token = str(token or "").strip()
    if not token:
        return None
    rec = (_read().get("links") or {}).get(token)
    return dict(rec) if isinstance(rec, dict) else None


def validate(token: str):
    """Return `(record, None)` when the token may be used right now,
    or `(None, reason)` where reason is one of:
    'not_found' | 'revoked' | 'expired' | 'already_used'.
    """
    rec = get_link(token)
    if not rec:
        return None, "not_found"
    if rec.get("revoked"):
        return None, "revoked"
    exp = _parse_iso(str(rec.get("expires_at") or ""))
    if exp and _now() > exp:
        return None, "expired"
    if rec.get("single_use") and int(rec.get("uses") or 0) > 0:
        return None, "already_used"
    return rec, None


def list_for_subject(subject_kind: str, subject_key: str,
                     include_dead: bool = False) -> list:
    """All links for one subject, newest first."""
    out = []
    for rec in (_read().get("links") or {}).values():
        if not isinstance(rec, dict):
            continue
        if rec.get("subject_kind") != subject_kind:
            continue
        if str(rec.get("subject_key") or "") != str(subject_key or ""):
            continue
        if not include_dead:
            exp = _parse_iso(str(rec.get("expires_at") or ""))
            dead = (rec.get("revoked")
                    or (exp and _now() > exp)
                    or (rec.get("single_use")
                        and int(rec.get("uses") or 0) > 0))
            if dead:
                continue
        out.append(dict(rec))
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return out


def is_live(rec: dict) -> bool:
    """True when this record could still be paid against."""
    if not isinstance(rec, dict) or rec.get("revoked"):
        return False
    exp = _parse_iso(str(rec.get("expires_at") or ""))
    if exp and _now() > exp:
        return False
    if rec.get("single_use") and int(rec.get("uses") or 0) > 0:
        return False
    return True


# ---------------------------------------------------------------------------
# Mutate
# ---------------------------------------------------------------------------

def record_use(token: str, amount_usd: float = 0.0,
               stripe_ref: str = "") -> None:
    """Bump the use counter. Called when a Checkout Session is minted
    for this token - so `uses` counts genuine payment attempts, and a
    single_use link closes as soon as the recipient reaches Stripe.

    Never raises: a bookkeeping failure must not block a payment.
    """
    token = str(token or "").strip()
    if not token:
        return

    def _apply(doc):
        links = doc.setdefault("links", {})
        rec = links.get(token)
        if not isinstance(rec, dict):
            return None
        rec["uses"] = int(rec.get("uses") or 0) + 1
        rec["last_used_at"] = _iso(_now())
        try:
            rec["total_paid_usd"] = round(
                float(rec.get("total_paid_usd") or 0.0)
                + float(amount_usd or 0.0), 2)
        except (TypeError, ValueError):
            pass
        if stripe_ref:
            refs = rec.setdefault("stripe_refs", [])
            if isinstance(refs, list) and stripe_ref not in refs:
                refs.append(str(stripe_ref)[:120])
                del refs[:-20]  # keep the last 20
        doc["last_updated"] = _iso(_now())
        return doc

    try:
        _update(_apply)
    except Exception as e:
        print(f"[payment_links] record_use failed for {token[:8]}...: {e}")


def revoke(token: str, revoked_by: str = "") -> bool:
    """Kill a link. Returns True when a record was found and marked."""
    token = str(token or "").strip()
    if not token:
        return False
    found = {"ok": False}

    def _apply(doc):
        links = doc.setdefault("links", {})
        rec = links.get(token)
        if not isinstance(rec, dict):
            return None
        if rec.get("revoked"):
            found["ok"] = True
            return None  # already revoked; skip the write
        rec["revoked"] = True
        rec["revoked_at"] = _iso(_now())
        rec["revoked_by"] = str(revoked_by or "")[:120]
        found["ok"] = True
        doc["last_updated"] = _iso(_now())
        return doc

    try:
        _update(_apply)
    except Exception as e:
        print(f"[payment_links] revoke failed: {e}")
        return False
    return found["ok"]


def public_url(base_url: str, token: str) -> str:
    """The link an admin copies and sends."""
    return f"{str(base_url or '').rstrip('/')}/pay/{token}"
