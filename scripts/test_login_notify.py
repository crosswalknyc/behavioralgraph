#!/usr/bin/env python3
"""First-login emails Jenna, once per seat, never the seat."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LOGIN_NOTIFY_STATE_FILE"] = tempfile.mkstemp(suffix=".json")[1]
os.environ.setdefault("SKIP_LOAD_PRICING", "1")

import login_notify as n  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


sent = []


def capture(to_addrs, subject, body):
    sent.append({"to": list(to_addrs), "subject": subject, "body": body})


david = {
    "first_name": "David",
    "last_name": "Graber",
    "email": "david.graber@wbd.com",
    "username": "david.graber",
    "company": "WBD Licensing",
    "notify_login_email": "jenna@crosswalknyc.com",
    "notify_login_group": "wbd_licensing",
}
madison = {
    "first_name": "Madison",
    "last_name": "Wojciechowski",
    "email": "madison.wojciechowski@wbd.com",
    "username": "madison.wojciechowski",
    "company": "WBD Licensing",
    "notify_login_email": "jenna@crosswalknyc.com",
    "notify_login_group": "wbd_licensing",
}
users_data = {"users": {
    "david.graber": david,
    "madison.wojciechowski": madison,
}}
now = datetime(2026, 11, 17, 15, 10, tzinfo=timezone.utc)

n._mem.clear()
sent.clear()
n.notify_first_login(david, "david.graber", users_data,
                     now=now, send=capture, sync=True)
check("first of the pair emails Jenna",
      len(sent) == 1 and sent[0]["to"] == ["jenna@crosswalknyc.com"])
check("first subject names David",
      sent[0]["subject"] == "David Graber signed in")
check("first body waits on Madison",
      "Still waiting on: Madison Wojciechowski." in sent[0]["body"])
check("body has no em dash",
      "\u2014" not in sent[0]["body"] and "\u2013" not in sent[0]["body"]
      and "\u2014" not in sent[0]["subject"])
check("body never mails the seat",
      "david.graber@wbd.com" in sent[0]["body"]
      and sent[0]["to"] == ["jenna@crosswalknyc.com"])

n.notify_first_login(david, "david.graber", users_data,
                     now=now, send=capture, sync=True)
check("repeat login is silent", len(sent) == 1)

n.notify_first_login(madison, "madison.wojciechowski", users_data,
                     now=now, send=capture, sync=True)
check("second of the pair emails Jenna", len(sent) == 2)
check("second subject says both signed in",
      sent[1]["subject"] == "David Graber and Madison Wojciechowski have both signed in"
      or sent[1]["subject"] == "Madison Wojciechowski and David Graber have both signed in")
check("second body says both are in",
      "Both of them have now signed in." in sent[1]["body"])

plain = dict(david)
plain["notify_login_email"] = "david.graber@wbd.com"
n._mem.clear()
os.environ["LOGIN_NOTIFY_STATE_FILE"] = tempfile.mkstemp(suffix=".json")[1]
before = len(sent)
n.notify_first_login(plain, "david.graber", users_data,
                     now=now, send=capture, sync=True)
check("client addresses are not mailed", len(sent) == before)

none = dict(david)
none.pop("notify_login_email")
n.notify_first_login(none, "david.graber", users_data,
                     now=now, send=capture, sync=True)
check("no flag means no email", len(sent) == before)

app = (ROOT / "app.py").read_text()
check("login path calls the notifier",
      "login_notify" in app and "notify_first_login" in app)

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("ALL PASS")
