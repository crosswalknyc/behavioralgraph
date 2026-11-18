"""Email Jenna and Liz when a plan allowance runs out.

Jenna 2026-11-17: Sony Television's $11k is use-it-or-lose-it. When
they hit it, email her and Liz: which company and seat, what they
tried to run, usage to date. Never mails the client. One notice per
company per run-out: the claim is keyed on the last time money landed
on the wallet, so a top-up or the next month's amount resets it and
repeated clicks never send a second copy. Crosswalk inboxes only.
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


def _episode_key(company: str, episode: str = "", now=None) -> str:
    """One run-out: the company and the last time money landed on its
    wallet. With no funding row on record the calendar month stands in."""
    ep = str(episode or "").strip()
    if not ep:
        return _month_key(company, now)
    fold = str(company or "").strip().lower() or "unknown"
    return f"{fold}|ep:{ep}"


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


def claim_month(company: str, now=None, episode: str = "") -> bool:
    """True once per company per run-out (per calendar month when the
    wallet has no funding row). Test file, then S3, then mem."""
    key = _episode_key(company, episode, now)
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


def _money(v) -> str:
    try:
        amt = float(v or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if abs(amt - round(amt)) < 0.001:
        return f"${amt:,.0f}"
    return f"${amt:,.2f}"


def render_runout(company: str, amount_usd, billed_via: str = "", *,
                  who: str = "", what: str = "", price_usd=None,
                  usage=None, ran: bool = False):
    """Subject and body. Plain words, no dashes, Crosswalk readers only."""
    name = str(company or "A company").strip() or "A company"
    try:
        amt = float(amount_usd or 0)
    except (TypeError, ValueError):
        amt = 0.0
    use = usage if isinstance(usage, dict) else {}
    if amt <= 0:
        try:
            amt = float(use.get("allowance_usd") or 0)
        except (TypeError, ValueError):
            amt = 0.0
    subject = f"{name} has used its plan allowance"
    size = f"the {_money(amt)} included with its plan" if amt > 0 \
        else "the usage included with its plan"
    try:
        left = float(use.get("left_usd") or 0)
    except (TypeError, ValueError):
        left = 0.0
    if ran or left < 1.0:
        lines = [f"{name} has used {size}."]
    else:
        lines = [f"{name} does not have enough left of {size} "
                 f"for its next run."]
    lines.append("")
    seat = str(who or billed_via or "").strip()
    if seat:
        lines.append(f"Who: {seat}")
    tried = str(what or "").strip()
    try:
        price = float(price_usd or 0)
    except (TypeError, ValueError):
        price = 0.0
    if tried or price > 0:
        label = "Last run" if ran else "Tried to run"
        bit = tried or "a priced run"
        if price > 0:
            bit += f" ({_money(price)})"
        lines.append(f"{label}: {bit}")
    if use:
        used = _money(use.get("used_usd") or 0)
        if amt > 0:
            line = f"Usage to date: {used} of {_money(amt)}, {_money(left)} left"
        else:
            line = f"Usage to date: {used}, {_money(left)} left"
        try:
            life = float(use.get("lifetime_usd") or 0)
        except (TypeError, ValueError):
            life = 0.0
        if life > 0:
            line += f" ({_money(life)} lifetime)"
        lines.append(line + ".")
    lines.append("")
    if ran:
        lines.append("That run went through. The next priced run will ask "
                     "them to top up.")
    else:
        lines.append("The run did not start and nothing was charged. They "
                     "were asked to top up.")
    lines.append("This notice goes out once. It resets when the wallet is "
                 "topped up or the next month's amount lands.")
    lines.extend(["", "Prometheus", "Crosswalk"])
    return subject, "\n".join(lines)


def send_runout(company: str, amount_usd=0, billed_via: str = "",
                send=None, *, who: str = "", what: str = "",
                price_usd=None, usage=None, episode: str = "",
                ran: bool = False) -> bool:
    """Claim the run-out, then mail Jenna and Liz. Never raises."""
    try:
        if str(billed_via or "").strip().lower().startswith("canary:"):
            return False            # a probe identity never mails
        if not claim_month(company, episode=episode):
            return False
        subject, body = render_runout(
            company, amount_usd, billed_via, who=who, what=what,
            price_usd=price_usd, usage=usage, ran=ran)
        recipients = [e for e in DEFAULT_TO
                      if str(e).endswith("@crosswalknyc.com")]
        if send is not None:
            send(subject, body, recipients)
            return True
    except Exception as e:
        print(f"[allowance-runout] not sent: {e}")
        return False
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
