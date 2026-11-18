"""Email Jenna and Liz when a monthly included allowance runs out.

Jenna 2026-11-17: Sony Television's $11k is use-it-or-lose-it. When
they hit it, email her and Liz. Never mails the client. One notice
per company per calendar month. Crosswalk inboxes only.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

BUCKET = "dashboard-inputs"
STATE_KEY = "system/allowance_runout_notices.json"
FROM = "Prometheus <prometheus@crosswalknyc.com>"
FROM_ADDR = "prometheus@crosswalknyc.com"
REPLY_TO = "jenna@crosswalknyc.com"
DEFAULT_TO = (
    "jenna@crosswalknyc.com",
    "liz@crosswalknyc.com",
)

_mem = {}
_mem_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def _month_key(company: str, now=None) -> str:
    stamp = now or _now()
    fold = str(company or "").strip().lower() or "unknown"
    return f"{fold}|{stamp.strftime('%Y_%m')}"


def _state_path():
    return (os.environ.get("ALLOWANCE_RUNOUT_STATE_FILE") or "").strip()


def _claim_mem(key: str) -> bool:
    with _mem_lock:
        if key in _mem:
            return False
        _mem[key] = True
        return True


def _claim_file(key: str) -> bool:
    path = _state_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    months = state.setdefault("months", {})
    if not isinstance(months, dict):
        months = {}
        state["months"] = months
    if key in months:
        return False
    months[key] = {"ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)
    return True


def _claim_s3(key: str) -> bool:
    from migration.s3_json_state import update_json

    box = {"claimed": False}

    def mutate(doc):
        state = doc if isinstance(doc, dict) else {}
        months = state.setdefault("months", {})
        if not isinstance(months, dict):
            months = {}
            state["months"] = months
        if key in months:
            return state
        months[key] = {"ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ")}
        box["claimed"] = True
        return state

    update_json(BUCKET, STATE_KEY, mutate)
    return bool(box["claimed"])


def claim_month(company: str, now=None) -> bool:
    """True once per company per calendar month. Test file, then S3, then mem."""
    key = _month_key(company, now)
    if _state_path():
        if not _claim_file(key):
            return False
        _claim_mem(key)
        return True
    try:
        if not _claim_s3(key):
            return False
        _claim_mem(key)
        return True
    except Exception:
        return _claim_mem(key)


def render_runout(company: str, amount_usd, billed_via: str = ""):
    name = str(company or "A company").strip() or "A company"
    try:
        amt = float(amount_usd or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        money = "this month's included amount"
    elif abs(amt - round(amt)) < 0.001:
        money = f"${amt:,.0f}"
    else:
        money = f"${amt:,.2f}"
    subject = f"{name} has used this month's included amount"
    lines = [
        f"{name} has used this month's {money} included with their plan.",
        "",
        "Further pulls are no longer included. They will see prices until the next month's amount lands.",
    ]
    via = str(billed_via or "").strip()
    if via:
        lines.extend(["", f"Last pull was billed via {via}."])
    lines.extend(["", "Prometheus", "Crosswalk"])
    return subject, "\n".join(lines)


def send_runout(company: str, amount_usd=0, billed_via: str = "",
                send=None) -> bool:
    """Claim the month, then mail Jenna and Liz. Never raises."""
    if not claim_month(company):
        return False
    subject, body = render_runout(company, amount_usd, billed_via)
    recipients = [e for e in DEFAULT_TO if str(e).endswith("@crosswalknyc.com")]
    if send is not None:
        send(subject, body, recipients)
        return True
    try:
        from prometheus.mail_guard import sending_allowed
        if not sending_allowed():
            return True
    except Exception:
        pass
    try:
        import boto3
        ses = boto3.client("ses", region_name="us-east-2")
        ses.send_email(
            Source=FROM,
            Destination={"ToAddresses": recipients},
            ReplyToAddresses=[REPLY_TO],
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        return True
    except Exception as e:
        print(f"[allowance-runout] SES send failed: {e}")
        return False
