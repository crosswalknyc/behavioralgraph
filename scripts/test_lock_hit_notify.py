#!/usr/bin/env python3
"""Card-lock hit emails Jenna, once per 30 minutes, never the seat."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LOCK_HIT_STATE_FILE"] = tempfile.mkstemp(suffix=".json")[1]
os.environ.setdefault("SKIP_LOAD_PRICING", "1")

import lock_hit_notify as n  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


sent = []


def capture(to_addrs, subject, body):
    sent.append({"to": list(to_addrs), "subject": subject, "body": body})


user = {
    "first_name": "Samantha",
    "last_name": "Kolton",
    "email": "samantha.kolton@caa.com",
    "username": "skolton",
    "opening_topup_usd": 250,
    "notify_lock_hit_email": "jenna@crosswalknyc.com",
}
now = datetime(2026, 11, 16, 16, 40, tzinfo=timezone.utc)

n._mem.clear()
sent.clear()
n.notify_opening_lock_hit(user, "skolton", where="dashboard",
                          now=now, send=capture, sync=True)
check("first dashboard hit emails Jenna",
      len(sent) == 1 and sent[0]["to"] == ["jenna@crosswalknyc.com"])
check("subject names her",
      "Samantha Kolton" in sent[0]["subject"])
check("body has the $250 opener",
      "$250" in sent[0]["body"] and "skolton" in sent[0]["body"])
check("body has no em dash",
      "\u2014" not in sent[0]["body"] and "\u2013" not in sent[0]["body"])
check("body never mails the seat",
      "samantha.kolton@caa.com" in sent[0]["body"]
      and sent[0]["to"] == ["jenna@crosswalknyc.com"])

n.notify_opening_lock_hit(user, "skolton", where="dashboard",
                          now=now + timedelta(minutes=5),
                          send=capture, sync=True)
check("second hit inside 30 minutes is silent", len(sent) == 1)

n.notify_opening_lock_hit(user, "skolton", where="prometheus",
                          now=now + timedelta(minutes=31),
                          send=capture, sync=True)
check("hit after 30 minutes emails again", len(sent) == 2)
check("prometheus hit names Prometheus",
      "Prometheus" in sent[1]["body"])

plain = dict(user)
plain["notify_lock_hit_email"] = "samantha.kolton@caa.com"
n._mem.clear()
before = len(sent)
n.notify_opening_lock_hit(plain, "skolton", where="login",
                          now=now, send=capture, sync=True)
check("client addresses are not mailed", len(sent) == before)

none = dict(user)
none.pop("notify_lock_hit_email")
n.notify_opening_lock_hit(none, "skolton", where="dashboard",
                          now=now, send=capture, sync=True)
check("no flag means no email", len(sent) == before)

check("dashboard redirect calls the notifier",
      "lock_hit_notify" in (ROOT / "app.py").read_text()
      and 'where="dashboard"' in (ROOT / "app.py").read_text())
check("login redirect calls the notifier",
      'where="login"' in (ROOT / "app.py").read_text())
check("prometheus funds gate calls the notifier",
      'where="prometheus"' in
      (ROOT / "prometheus" / "legacy" / "chat.py").read_text())

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("ALL PASS")
