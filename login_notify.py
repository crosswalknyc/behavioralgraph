"""Email Jenna the first time a watched seat signs in.

Jenna 2026-11-17: email her when David Graber and Madison
Wojciechowski (WBD Licensing) log in. A seat opts in with
`notify_login_email` (Crosswalk addresses only) and an optional
`notify_login_group` so a pair can be reported as both in. One email
per seat, first sign-in only. Never raises into the request path.
Never mails the seat.
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
STATE_KEY = "system/login_notices.json"
FROM = "Prometheus <prometheus@crosswalknyc.com>"
FROM_ADDR = "prometheus@crosswalknyc.com"
REPLY_TO = "jenna@crosswalknyc.com"

_mem = {}
_mem_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def _recipients(user: dict) -> list:
    raw = (user or {}).get("notify_login_email") or ""
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


def _group_id(user: dict) -> str:
    return str((user or {}).get("notify_login_group") or "").strip().lower()


def _peers(users_data, group: str, username: str):
    """Other seats in the same login-watch group, as (uname, user)."""
    if not group or not isinstance(users_data, dict):
        return []
    out = []
    for uname, rec in (users_data.get("users") or {}).items():
        if not isinstance(rec, dict):
            continue
        if str(uname or "").strip().lower() == str(username or "").strip().lower():
            continue
        if _group_id(rec) == group:
            out.append((str(uname), rec))
    out.sort(key=lambda row: row[0])
    return out


def render_login(user: dict, username: str, peers_in, peers_waiting):
    """Return (subject, text body) for the ops ping."""
    who = _display_name(user, username)
    email = str((user or {}).get("email") or "").strip()
    login = (username or "").strip() or "unknown"
    company = str((user or {}).get("company") or "").strip()
    waiting_names = [_display_name(p, u) for u, p in (peers_waiting or [])]
    in_names = [_display_name(p, u) for u, p in (peers_in or [])]
    both = bool(peers_in) and not waiting_names
    if both:
        names = [who] + in_names
        subj = f"{names[0]} and {names[1]} have both signed in" if len(names) == 2 else (
            ", ".join(names[:-1]) + f" and {names[-1]} have all signed in")
    else:
        subj = f"{who} signed in"
    lines = [
        f"{who} signed in to Crosswalk just now.",
        "",
        f"Login: {login}",
    ]
    if email:
        lines.append(f"Email: {email}")
    if company:
        lines.append(f"Company: {company}")
    if both:
        lines.extend([
            "",
            "Both of them have now signed in.",
        ])
    elif waiting_names:
        waiting = ", ".join(waiting_names)
        lines.extend([
            "",
            f"Still waiting on: {waiting}.",
        ])
    lines.extend([
        "",
        "Prometheus",
        "Crosswalk",
    ])
    return subj, "\n".join(lines)


def _state_path():
    return (os.environ.get("LOGIN_NOTIFY_STATE_FILE") or "").strip()


def _claim_file(key: str, now: datetime, group: str) -> dict | None:
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
    seats = state.setdefault("seats", {})
    if not isinstance(seats, dict):
        seats = {}
        state["seats"] = seats
    fold = str(key or "").strip().lower()
    if fold in seats:
        return None
    seats[fold] = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "group": group,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)
    return state


def _claim_s3(key: str, now: datetime, group: str) -> dict | None:
    from migration.s3_json_state import update_json
    box = {"state": None}

    def mutate(obj):
        cur = obj if isinstance(obj, dict) else {}
        seats = cur.setdefault("seats", {})
        if not isinstance(seats, dict):
            seats = {}
            cur["seats"] = seats
        fold = str(key or "").strip().lower()
        if fold in seats:
            return None
        seats[fold] = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "group": group,
        }
        box["state"] = cur
        return cur

    update_json(BUCKET, STATE_KEY, mutate)
    return box["state"]


def _claimed(username: str, now: datetime, group: str):
    """Return the state after claiming this first login, or None if
    this seat already fired."""
    key = str(username or "").strip().lower()
    if not key:
        return None
    with _mem_lock:
        if key in _mem:
            return None
        _mem[key] = now.timestamp()
    try:
        if _state_path():
            return _claim_file(key, now, group)
        return _claim_s3(key, now, group)
    except Exception:
        traceback.print_exc()
        return {"seats": {key: {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "group": group}}}


def _ses_send(to_addrs, subject, body) -> None:
    import boto3
    from prometheus.mail_guard import sending_allowed, suppressed_note
    if not sending_allowed():
        print(suppressed_note("login notify", subject))
        return
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


def _split_peers(peers, state, username: str):
    seats = (state or {}).get("seats") or {}
    if not isinstance(seats, dict):
        seats = {}
    me = str(username or "").strip().lower()
    signed = []
    waiting = []
    for uname, rec in peers:
        fold = str(uname or "").strip().lower()
        if fold == me:
            continue
        if fold in seats:
            signed.append((uname, rec))
        else:
            waiting.append((uname, rec))
    return signed, waiting


def _deliver(user, username, users_data, now, send):
    recips = _recipients(user)
    if not recips:
        return
    group = _group_id(user)
    state = _claimed(username, now, group)
    if state is None:
        return
    peers = _peers(users_data, group, username)
    signed, waiting = _split_peers(peers, state, username)
    subj, body = render_login(user, username, signed, waiting)
    if any(ch in subj or ch in body for ch in ("\u2014", "\u2013")):
        subj = subj.replace("\u2014", "-").replace("\u2013", "-")
        body = body.replace("\u2014", "-").replace("\u2013", "-")
    sender = send or _ses_send
    sender(recips, subj, body)


def notify_first_login(user, username="", users_data=None, *,
                       now=None, send=None, sync=False):
    """Ping the opted-in Crosswalk inbox on first sign-in. Never raises."""
    try:
        if str(os.environ.get("LOGIN_NOTIFY_DISABLED") or "") == "1":
            return
        user = user if isinstance(user, dict) else {}
        uname = str(username or user.get("username") or "").strip()
        if not _recipients(user) or not uname:
            return
        when = now or _now()
        if sync:
            _deliver(user, uname, users_data, when, send)
            return
        t = threading.Thread(
            target=_deliver,
            args=(user, uname, users_data, when, send),
            daemon=True,
        )
        t.start()
    except Exception:
        traceback.print_exc()
