#!/usr/bin/env python3
"""One refund path for every unit a charge can take (2026-11-05).

UNIT MAP. consume_credit (app.py) records a charge one of five ways; a
refund has to put back the same thing. The history entry is the row in
the seat's ``credit_usage_history``.

  unit            what moved at charge time                       history entry
  credits         user.credits -= n, user.credits_used += n       {used_at, description, job_id, pull_type,
                                                                   credits_used: n}
  company_pool    companies[c].credit_pool_used += n,             same shape, no wallet fields (the pool is
                  user.credits_used += n                           taken when the company has a pool and the
                                                                   seat's credit_source != 'personal')
  unlimited       user.credits_used += n only                     same shape (credits == -1 / unlimited / no_cap)
  wallet          wallet.apply_wallet_deduct on the USER record:  the entry also carries wallet_charged_usd
                  wallet_balance_usd -= usd and a transaction      (+ wallet_tool_key, wallet_addon_cuts,
                  {kind:'deduct', amount_usd:-usd, job_id, tool};  wallet_quantity); user.credits_used += n;
                  user.credits is NOT reduced                      user.credits is untouched
  company_wallet  the same deduct on companies[c] when the seat's  same wallet-stamped entry on the SEAT; the
                  billing_source == 'company'; the transaction is  company transaction names the seat in
                  stamped billed_via_username=<seat>               billed_via_username

Before today refund_credit (app.py) and the worker's
credit_refunds.refund_run_credits restored CREDITS for every unit: a
dollar seat got +n credits and a history row while wallet_balance_usd
never moved (ops refunded by hand, e.g. the $300 Gunna refund of
2026-10-07). apply_refund below reads the unit off the charge record
and restores that unit; refund_charge wraps it in the caller's
compare-and-swap writer.

Idempotent on (username, job_id, reason): a retry with the same key is
a no-op, and a job is never refunded past what it was charged (credits
and dollars both). A charge that cannot be found (legacy job_id='' rows)
is refunded in the seat's CURRENT billing mode and flagged
``unit_assumed`` so the caller can tell ops.
"""
from __future__ import annotations

import sys
import traceback

UNIT_CREDITS = "credits"
UNIT_POOL = "company_pool"
UNIT_UNLIMITED = "unlimited"
UNIT_WALLET = "wallet"
UNIT_COMPANY_WALLET = "company_wallet"
UNIT_NONE = "none"

_WALLET_UNITS = (UNIT_WALLET, UNIT_COMPANY_WALLET)


# ---------------------------------------------------------------- small helpers
def _num(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _usd(value):
    try:
        if value is None:
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _credits_balance(user) -> int:
    c = (user or {}).get("credits")
    if c is None:
        return 0
    return _num(c, 0)


def _is_unlimited(user) -> bool:
    return (
        _credits_balance(user) == -1
        or bool(user.get("unlimited"))
        or bool(user.get("no_cap"))
        or str(user.get("billing_mode") or "").strip().lower() == "unlimited"
    )


def _company_pool(data, company_name):
    """The company record when it carries a credit pool (mirror of
    app._get_company_pool, which returns None without a pool)."""
    name = str(company_name or "").strip()
    if not name:
        return None
    pool = (data.get("companies") or {}).get(name)
    if not isinstance(pool, dict):
        return None
    if pool.get("credit_pool") is None:
        return None
    return pool


def _is_refund_row(row) -> bool:
    return str((row or {}).get("pull_type") or "").strip().lower() == "refund"


def _now_iso() -> str:
    try:
        from wallet import ledger_now_iso
        return ledger_now_iso()
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- charge lookup
def find_charge(user, job_id="", charge_description=""):
    """The charge row this refund reverses: the newest non-refund entry
    with the given job_id, or (job_id unknown) with the exact
    description. None when there is nothing to anchor to."""
    jid = str(job_id or "").strip()
    desc = str(charge_description or "").strip()
    hist = (user or {}).get("credit_usage_history") or []
    if jid:
        for row in hist:
            if not isinstance(row, dict) or _is_refund_row(row):
                continue
            if str(row.get("job_id") or "").strip() != jid:
                continue
            if _num(row.get("credits_used"), 0) > 0 or (_usd(row.get("wallet_charged_usd")) or 0) > 0:
                return row
    if desc:
        for row in hist:
            if not isinstance(row, dict) or _is_refund_row(row):
                continue
            if str(row.get("description") or "").strip() != desc:
                continue
            if _num(row.get("credits_used"), 0) > 0 or (_usd(row.get("wallet_charged_usd")) or 0) > 0:
                return row
    return None


def prior_refunds(user, job_id="", charge_job_id=""):
    """Refund rows already written against this job (by the refund's
    own job_id, or by the charge id it names)."""
    keys = {k for k in (str(job_id or "").strip(), str(charge_job_id or "").strip()) if k}
    if not keys:
        return []
    out = []
    for row in (user or {}).get("credit_usage_history") or []:
        if not isinstance(row, dict) or not _is_refund_row(row):
            continue
        if str(row.get("job_id") or "").strip() in keys or str(row.get("charge_job_id") or "").strip() in keys:
            out.append(row)
    return out


def _wallet_subject_for_charge(data, username, user, job_id, tool_key=""):
    """Which wallet the deduct for this job landed on. Returns
    (kind, key, record) with kind 'user' | 'company'."""
    import wallet as _w
    jid = str(job_id or "").strip()
    if jid:
        if _w.existing_wallet_deduct(user, job_id=jid):
            return "user", username, user
        subject, kind, key = _w.resolve_billing_subject(user, data)
        if kind == "company" and isinstance(subject, dict) and _w.existing_wallet_deduct(subject, job_id=jid):
            return "company", key, subject
        for name, comp in (data.get("companies") or {}).items():
            if not isinstance(comp, dict):
                continue
            txn = _w.existing_wallet_deduct(comp, job_id=jid)
            if txn and str(txn.get("billed_via_username") or "").strip().lower() in (
                    str(username).strip().lower(), str(user.get("email") or "").strip().lower()):
                return "company", str(name), comp
    subject, kind, key = _w.resolve_billing_subject(user, data)
    if kind == "company" and isinstance(subject, dict):
        return "company", key, subject
    return "user", username, user


def charge_unit(data, username, job_id="", charge_job_id="", charge_description=""):
    """Read the unit a charge took. Returns (unit, charge_row, subject_kind,
    subject_key, subject_record, assumed). ``assumed`` is True when no
    charge row was found and the unit is the seat's current mode."""
    users = (data or {}).get("users") or {}
    user = users.get(username)
    if not isinstance(user, dict):
        return UNIT_NONE, None, "user", username, None, False
    anchor = str(charge_job_id or "").strip() or str(job_id or "").strip()
    row = find_charge(user, anchor, charge_description)
    if row is not None:
        if (_usd(row.get("wallet_charged_usd")) or 0) > 0:
            kind, key, rec = _wallet_subject_for_charge(
                data, username, user, str(row.get("job_id") or anchor), row.get("wallet_tool_key") or "")
            return (UNIT_COMPANY_WALLET if kind == "company" else UNIT_WALLET), row, kind, key, rec, False
        if _is_unlimited(user):
            return UNIT_UNLIMITED, row, "user", username, user, False
        pool = _company_pool(data, user.get("company"))
        if pool is not None and user.get("credit_source") != "personal":
            return UNIT_POOL, row, "company", str(user.get("company") or "").strip(), pool, False
        return UNIT_CREDITS, row, "user", username, user, False
    # No charge record: the seat's current billing mode decides.
    if _is_unlimited(user):
        return UNIT_UNLIMITED, None, "user", username, user, True
    pool = _company_pool(data, user.get("company"))
    pool_has_room = False
    if pool is not None and user.get("credit_source") != "personal":
        total = _num(pool.get("credit_pool"), 0)
        pool_has_room = total == -1 or (total - _num(pool.get("credit_pool_used"), 0)) > 0
    try:
        import wallet as _w
        subject, kind, key = _w.resolve_billing_subject(user, data)
        wallet_seat = bool(_w.is_paying_customer(subject)) and not _w.is_unlimited(subject)
    except Exception:
        subject, kind, key, wallet_seat = user, "user", username, False
    credits_left = _credits_balance(user) > 0
    if wallet_seat and not credits_left and not pool_has_room:
        return (UNIT_COMPANY_WALLET if kind == "company" else UNIT_WALLET), None, kind, key, subject, True
    if pool is not None and user.get("credit_source") != "personal":
        return UNIT_POOL, None, "company", str(user.get("company") or "").strip(), pool, True
    return UNIT_CREDITS, None, "user", username, user, True


# ---------------------------------------------------------------- the mutator
def apply_refund(data, username, *, job_id="", credits=0, reason="",
                 amount_usd=None, charge_job_id="", charge_description="",
                 tool_key="", pull_type="", correction=False):
    """Refund one charge IN PLACE on a users.json dict. Returns a result
    dict; ``refunded`` True means ``data`` changed and must be written.

    credits: the credit count the caller is giving back (the unit a
    credits seat uses; on a dollar seat it only unwinds credits_used).
    amount_usd: the dollars to restore on a wallet seat; when None the
    charge row's wallet_charged_usd is used, prorated by credits when
    the refund is partial.
    """
    res = {"refunded": False, "already_refunded": False, "username": username,
           "unit": UNIT_NONE, "credits": 0, "usd": 0.0, "subject_kind": "user",
           "subject_key": username, "unit_assumed": False, "note": "",
           "job_id": str(job_id or "").strip()}
    users = (data or {}).get("users") or {}
    user = users.get(username)
    if not isinstance(user, dict):
        res["note"] = f"no user {username!r}"
        return res
    jid = str(job_id or "").strip()
    cjid = str(charge_job_id or "").strip()
    reason = str(reason or "downstream failure").strip()
    n = max(_num(credits, 0), 0)

    unit, row, kind, key, subject, assumed = charge_unit(
        data, username, job_id=jid, charge_job_id=cjid, charge_description=charge_description)
    res.update(unit=unit, subject_kind=kind, subject_key=key, unit_assumed=assumed)
    if unit == UNIT_NONE or subject is None:
        res["note"] = "no billing subject"
        return res

    # Idempotency: same (username, job_id, reason) never refunds twice,
    # and a job never refunds past its charge.
    prior = prior_refunds(user, jid, cjid) if (jid or cjid) else []
    desc = f"REFUND: {reason}"
    if jid and any(str(p.get("description") or "") == desc for p in prior):
        res.update(already_refunded=True, note="refund already recorded for this job and reason")
        return res
    charge_credits = _num(row.get("credits_used"), 0) if row else 0
    charge_usd = _usd(row.get("wallet_charged_usd")) if row else None
    refunded_credits = sum(abs(_num(p.get("credits_used"), 0)) for p in prior)
    refunded_usd = round(sum(abs(_usd(p.get("wallet_charged_usd")) or 0.0) for p in prior), 2)
    if row is not None and charge_credits > 0 and refunded_credits >= charge_credits and (
            charge_usd is None or refunded_usd >= charge_usd):
        res.update(already_refunded=True, note="charge already fully refunded")
        return res
    if row is not None and charge_credits > 0:
        n = min(n or charge_credits, max(charge_credits - refunded_credits, 0))

    usd = 0.0
    if unit in _WALLET_UNITS:
        usd = _usd(amount_usd)
        if (usd is None or usd <= 0) and charge_usd:
            if charge_credits > 0 and 0 < n < charge_credits:
                usd = round(charge_usd * n / charge_credits, 2)
            else:
                usd = charge_usd
        if (usd is None or usd <= 0) and "app" in sys.modules:
            # A dollar seat with no charge record: price the pull the way
            # the charge would have (pricing lives behind the dashboard's
            # S3 client, so only inside the app; the worker falls through).
            try:
                import wallet as _w
                tk = tool_key or _w.pull_type_to_tool_key(pull_type)
                if tk:
                    priced, mode = _w.should_charge_wallet(subject, tk)
                    usd = priced if mode == "wallet" and priced > 0 else None
            except Exception:
                usd = None
        if usd is None or usd <= 0:
            res["note"] = "wallet seat but no dollar amount to restore; recorded as credits"
            unit = UNIT_CREDITS if not _is_unlimited(user) else UNIT_UNLIMITED
            kind, key, subject = "user", username, user
            res.update(unit=unit, subject_kind=kind, subject_key=key)
            usd = 0.0
        elif charge_usd:
            usd = round(min(usd, max(charge_usd - refunded_usd, 0.0)), 2)
            if usd <= 0:
                res.update(already_refunded=True, note="dollars already fully refunded")
                return res
    if n <= 0 and usd <= 0:
        res["note"] = "nothing to refund"
        return res

    # --- restore the unit
    if unit == UNIT_CREDITS:
        if not _is_unlimited(user):
            user["credits"] = _credits_balance(user) + n
    elif unit == UNIT_POOL:
        if _num(subject.get("credit_pool"), 0) != -1:
            subject["credit_pool_used"] = max(0, _num(subject.get("credit_pool_used"), 0) - n)
    elif unit in _WALLET_UNITS and usd > 0:
        import wallet as _w
        txn_desc = desc if not correction else f"REFUND (correction): {reason}"
        if not (jid and _w.existing_wallet_refund(subject, job_id=jid, description=txn_desc)):
            _w.apply_wallet_refund(
                subject, usd, description=txn_desc, job_id=jid, tool_key=tool_key or (row or {}).get("wallet_tool_key") or "",
                billed_via_username=username if kind == "company" else "")
    user["credits_used"] = max(0, _num(user.get("credits_used"), 0) - n)

    entry = {
        "used_at": _now_iso(),
        "description": desc if not correction else f"REFUND (correction): {reason}",
        "job_id": jid,
        "pull_type": "refund",
        "credits_used": -n,
        "refund_unit": unit,
    }
    if cjid and cjid != jid:
        entry["charge_job_id"] = cjid
    if unit in _WALLET_UNITS and usd > 0:
        entry["amount_usd"] = -usd
        entry["wallet_charged_usd"] = -usd
        if kind == "company":
            entry["wallet_subject"] = key
    if assumed:
        entry["refund_unit_assumed"] = True
    if correction:
        entry["correction"] = True
    hist = user.setdefault("credit_usage_history", [])
    if not isinstance(hist, list):
        hist = []
    hist.insert(0, entry)
    user["credit_usage_history"] = hist[:500]
    res.update(refunded=True, credits=n, usd=usd if unit in _WALLET_UNITS else 0.0)
    return res


# ---------------------------------------------------------------- the writer
def _default_mutate():
    app = sys.modules.get("app")
    fn = getattr(app, "_users_cas_mutate", None) if app is not None else None
    if fn is None:
        raise RuntimeError("refund_charge needs a users.json compare-and-swap writer (mutate=)")
    return fn


def refund_charge(username, job_id, credits, reason, *, amount_usd=None,
                  charge_job_id="", charge_description="", tool_key="",
                  pull_type="", mutate=None, notify=None, log=print,
                  correction=False):
    """Refund a charge through the caller's compare-and-swap writer.

    ``mutate(fn)`` applies fn(data) to a freshly read users.json and
    writes it when fn returns the doc (None skips the write): app.py's
    _users_cas_mutate, or the worker's update_json wrapper. ``notify``
    is called with a detail dict when the unit had to be assumed.
    Returns apply_refund's result dict (plus ``error`` on a write
    failure). Never raises.
    """
    result = {"refunded": False, "already_refunded": False, "username": username,
              "unit": UNIT_NONE, "credits": 0, "usd": 0.0, "note": ""}

    def _mut(data):
        r = apply_refund(data, username, job_id=job_id, credits=credits, reason=reason,
                         amount_usd=amount_usd, charge_job_id=charge_job_id,
                         charge_description=charge_description, tool_key=tool_key,
                         pull_type=pull_type, correction=correction)
        result.clear()
        result.update(r)
        return data if r.get("refunded") else None

    try:
        (mutate or _default_mutate())(_mut)
    except Exception as e:
        traceback.print_exc()
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    try:
        if result.get("refunded"):
            log(f"[refund] {username}: {result.get('credits')} credit(s)"
                + (f" / ${result.get('usd'):.2f}" if result.get("usd") else "")
                + f" back as {result.get('unit')} ({result.get('subject_kind')} {result.get('subject_key')})"
                + f" job={job_id or '-'}: {reason}")
        elif result.get("already_refunded"):
            log(f"[refund] {username}: job={job_id or '-'} already refunded ({result.get('note')})")
        else:
            log(f"[refund] {username}: nothing written for job={job_id or '-'} ({result.get('note')})")
        if result.get("refunded") and result.get("unit_assumed"):
            details = {"username": username, "job_id": job_id, "charge_job_id": charge_job_id,
                       "credits": result.get("credits"), "usd": result.get("usd"),
                       "unit": result.get("unit"), "reason": reason,
                       "note": "no charge record found; refunded in the seat's current billing mode"}
            log(f"[refund] UNIT ASSUMED {details}")
            if notify is not None:
                notify(details)
    except Exception:
        traceback.print_exc()
    return result


__all__ = ["apply_refund", "refund_charge", "charge_unit", "find_charge", "prior_refunds",
           "UNIT_CREDITS", "UNIT_POOL", "UNIT_UNLIMITED", "UNIT_WALLET", "UNIT_COMPANY_WALLET", "UNIT_NONE"]
