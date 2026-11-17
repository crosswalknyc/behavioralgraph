"""Email Jenna when a card-gated seat hits the lock page.

Jenna 2026-11-16: email her when Samantha Kolton tries to open the
dashboard and is shown the card-top-up message. A seat opts in with
`notify_lock_hit_email` (Crosswalk addresses only). One email per
seat per 30 minutes. Never raises into the request path. Never mails
the seat.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BUCKET = "dashboard-inputs"
STATE_KEY = "system/lock_hit_notices.json"
DEBOUNCE_SECONDS = 30 * 60
FROM = "Prometheus <prometheus@crosswalknyc.com>"
FROM_ADDR = "prometheus@crosswalknyc.com"
REPLY_TO = "jenna@crosswalknyc.com"

_mem = {}
_mem_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def _recipients(user: dict) -> list:
    raw = (user or {}).get("notify_lock_hit_email") or ""
    if isinstance(raw, list):
        parts = raw
    else:
        parts = str(raw).replace(";", ",").split(",")
    out = []
    seen = set()
    for p in parts:
        e = str(p or "").strip().lower()
        if not e.endswith("@crosswalknyc.com"):
            continue
        if e in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


def _display_name(user: dict, username: str) -> str:
    first = str((user or {}).get("first_name") or "").strip()
    last = str((user or {}).get("last_name") or "").strip()
    name = " ".join(p for p in (first, last) if p).strip()
    return name or username or "A user"


def _opening_need(user: dict) -> float:
    try:
        import wallet
        n = wallet.opening_topup_usd(user or {})
        if n > 0:
            return float(n)
    except Exception:
        pass
    try:
        return float((user or {}).get("opening_topup_usd") or 0) or 250.0
    except (TypeError, ValueError):
        return 250.0


def _where_line(where: str) -> str:
    w = str(where or "").strip().lower()
    if w == "prometheus":
        return "Prometheus"
    if w == "login":
        return "sign-in"
    return "the dashboard"


def _money(n: float) -> str:
    if abs(n - round(n)) < 1e-9:
        return f"${n:,.0f}"
    return f"${n:,.2f}"


def render_lock_hit(user: dict, username: str, where: str = "dashboard"):
    """Return (subject, text body) for the ops ping."""
    who = _display_name(user, username)
    need = _money(_opening_need(user))
    hit = _where_line(where)
    email = str((user or {}).get("email") or "").strip()
    login = (username or "").strip() or "unknown"
    subj = f"{who} was sent to the card page"
    lines = [
        f"{who} tried to open Crosswalk just now and was shown the card page.",
        "",
        f"Login: {login}",
    ]
    if email:
        lines.append(f"Email: {email}")
    lines.extend([
        f"What they hit: {hit}",
        "",
        f"They need to add at least {need} with a card to get back in.",
        "",
        "Prometheus",
        "Crosswalk",
    ])
    return subj, "\n".join(lines)


def _state_path():
    return (os.environ.get("LOCK_HIT_STATE_FILE") or "").strip()


def _parse_ts(raw) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _still_hot(rec, now: datetime) -> bool:
    if not isinstance(rec, dict):
        return False
    last = _parse_ts(rec.get("ts"))
    return bool(last and (now - last).total_seconds() < DEBOUNCE_SECONDS)


def _claim_file(key: str, now: datetime) -> bool:
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
    if _still_hot(state.get(key), now):
        return False
    state[key] = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)
    return True


def _claim_s3(key: str, now: datetime) -> bool:
    from migration.s3_json_state import update_json
    box = {"ok": False}

    def mutate(obj):
        cur = obj if isinstance(obj, dict) else {}
        if _still_hot(cur.get(key), now):
            return None
        cur[key] = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        box["ok"] = True
        return cur

    update_json(BUCKET, STATE_KEY, mutate)
    return bool(box["ok"])


def _claimed(username: str, now: datetime) -> bool:
    """True when this process should send. False when another hit
    was already recorded inside the debounce window."""
    key = str(username or "").strip().lower()
    if not key:
        return False
    with _mem_lock:
        prev = _mem.get(key)
        if prev is not None and (now.timestamp() - prev) < DEBOUNCE_SECONDS:
            return False
        _mem[key] = now.timestamp()
    try:
        if _state_path():
            return _claim_file(key, now)
        return _claim_s3(key, now)
    except Exception:
        traceback.print_exc()
        return True


def _ses_send(to_addrs, subject, body) -> None:
    import boto3
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM
    msg["To"] = ", ".join(to_addrs)
    msg["Reply-To"] = REPLY_TO
    msg.attach(MIMEText(body, "plain", "utf-8"))
    boto3.client("ses", region_name="us-east-2").send_raw_email(
        Source=FROM_ADDR,
        Destinations=list(to_addrs),
        RawMessage={"Data": msg.as_string().encode("utf-8")},
    )


def _deliver(user, username, where, now, send):
    recips = _recipients(user)
    if not recips:
        return
    if not _claimed(username, now):
        return
    subj, body = render_lock_hit(user, username, where)
    if any(ch in subj or ch in body for ch in ("\u2014", "\u2013")):
        subj = subj.replace("\u2014", "-").replace("\u2013", "-")
        body = body.replace("\u2014", "-").replace("\u2013", "-")
    sender = send or _ses_send
    sender(recips, subj, body)


def notify_opening_lock_hit(user, username="", where="dashboard", *,
                            now=None, send=None, sync=False):
    """Ping the opted-in Crosswalk inbox. Never raises."""
    try:
        if str(os.environ.get("LOCK_HIT_NOTIFY_DISABLED") or "") == "1":
            return
        user = user if isinstance(user, dict) else {}
        uname = str(username or user.get("username") or "").strip()
        if not _recipients(user) or not uname:
            return
        when = now or _now()
        if sync:
            _deliver(user, uname, where, when, send)
            return
        t = threading.Thread(
            target=_deliver,
            args=(user, uname, where, when, send),
            daemon=True,
        )
        t.start()
    except Exception:
        traceback.print_exc()
