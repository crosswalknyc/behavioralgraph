#!/usr/bin/env python3
"""Regression tests for newsletter engagement-recording hardening.

Established 2026-11-04 after the Digital Twins send went out with
tracking tokens the public host rejected: the sender's environment had
no SECRET_KEY, so tokens were signed with the legacy fallback while the
public host verified with its own SECRET_KEY. Every open and click for
73 minutes was silently dropped.

Three layers under test, all hermetic (S3 and SES stubbed):

1. Shared signing secret. A secret minted once into S3 leads the
   candidate list, so two processes with completely different
   environments sign and verify each other's tokens.
2. Pre-send probe. The /n/ok/<token> route answers ok only for a valid
   probe token, so a sender can prove the public host accepts its
   signatures before the first copy goes out.
3. Dead-tracking watchdog. A finished send with zero recorded opens and
   clicks 45+ minutes later pages ops exactly once; healthy sends,
   young sends, tiny sends, and already-paged sends never page.

Run: python3 scripts/test_newsletter_tracking_hardening.py
"""
import os
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Fake the CAS helper before newsletter imports anything.
_fake_store = {}


def _fake_update_json(bucket, key, mutate, default=None):
    cur = _fake_store.get(key, default if default is not None else {})
    nxt = mutate(cur)
    if nxt is not None:
        _fake_store[key] = nxt
    return _fake_store.get(key)


_fake_mod = types.ModuleType("s3_json_state")
_fake_mod.update_json = _fake_update_json
sys.modules["s3_json_state"] = _fake_mod

import newsletter as nl  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}")


# Stub S3 json get/put onto the same fake store.
nl._get_json = lambda key: _fake_store.get(key)
nl._put_json = lambda key, obj: _fake_store.__setitem__(key, obj)

print("1. shared signing secret crosses environments")
os.environ["SECRET_KEY"] = "env-of-the-sender"
os.environ.pop("NEWSLETTER_SIGNING_SECRET", None)
nl._shared_secret_cache["val"] = None
token = nl.sign_token({"p": "o", "c": "nl-test", "e": "x@y.com"})
minted = (_fake_store.get(nl.SIGNING_SECRET_KEY) or {}).get("secret")
check("secret minted into the store", bool(minted))

# Second "process": different env, cold cache.
os.environ["SECRET_KEY"] = "env-of-the-public-host"
nl._shared_secret_cache["val"] = None
payload = nl.verify_token(token)
check("token verifies across mismatched environments", payload == {"p": "o", "c": "nl-test", "e": "x@y.com"})
check("same secret read back, not re-minted",
      (_fake_store.get(nl.SIGNING_SECRET_KEY) or {}).get("secret") == minted)

print("2. legacy and env-signed tokens still verify")
import base64 as _b64
import hashlib as _hl
import hmac as _hm
import json as _json


def _sign_with(secret, payload):
    body = _json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = _hm.new(secret.encode("utf-8"), body.encode("utf-8"), _hl.sha256).hexdigest()[:24]
    raw = f"{body}|{sig}".encode("utf-8")
    return _b64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


legacy_tok = _sign_with("crosswalk-newsletter-dev", {"p": "c", "c": "nl-old", "i": 0})
check("legacy-signed token verifies", nl.verify_token(legacy_tok) is not None)
env_tok = _sign_with("env-of-the-public-host", {"p": "u", "c": "nl-old", "e": "a@b.com"})
check("env-signed token verifies", nl.verify_token(env_tok) is not None)
check("garbage token refuses", nl.verify_token("not-a-token") is None)

print("3. probe route answers ok only for a valid probe token")
from flask import Flask

app = Flask(__name__)
app.register_blueprint(nl.newsletter_bp)
client = app.test_client()
good = nl.sign_token({"p": "k", "c": "nl-test", "ts": "now"})
resp = client.get(f"/n/ok/{good}")
check("valid probe answers 200 ok", resp.status_code == 200 and resp.data == b"ok")
bad = nl.sign_token({"p": "o", "c": "nl-test"})
check("non-probe token refused", client.get(f"/n/ok/{bad}").status_code == 410)
check("garbage refused", client.get("/n/ok/zzzz").status_code == 410)

print("4. dead-tracking watchdog pages once, and only on true zeros")
_sent_emails = []


class _FakeSes:
    def send_email(self, **kw):
        _sent_emails.append(kw)
        return {"MessageId": "fake"}


nl._ses = lambda: _FakeSes()

NOW = datetime.now(timezone.utc)
_state = {
    "version": 1,
    "campaigns": [
        {"id": "nl-dead", "name": "Dead Send", "status": "sent",
         "sent_at": (NOW - timedelta(hours=2)).isoformat(), "stats": {"opens": 0, "clicks": 0}},
        {"id": "nl-healthy", "name": "Healthy Send", "status": "sent",
         "sent_at": (NOW - timedelta(hours=2)).isoformat(), "stats": {"opens": 50, "clicks": 9}},
        {"id": "nl-young", "name": "Too Young", "status": "sent",
         "sent_at": (NOW - timedelta(minutes=10)).isoformat(), "stats": {"opens": 0, "clicks": 0}},
        {"id": "nl-tiny", "name": "Tiny Send", "status": "sent",
         "sent_at": (NOW - timedelta(hours=2)).isoformat(), "stats": {"opens": 0, "clicks": 0}},
        {"id": "nl-old", "name": "Ancient Send", "status": "sent",
         "sent_at": (NOW - timedelta(days=30)).isoformat(), "stats": {"opens": 0, "clicks": 0}},
        {"id": "nl-draft", "name": "Draft", "status": "draft"},
    ],
}
_snaps = {
    "nl-dead": {"recipients": {f"u{i}@x.com": {"status": "sent"} for i in range(60)}},
    "nl-healthy": {"recipients": {f"u{i}@x.com": {"status": "sent", "open_count": 1,
                                                  "opened_at": "t"} for i in range(60)}},
    "nl-young": {"recipients": {f"u{i}@x.com": {"status": "sent"} for i in range(60)}},
    "nl-tiny": {"recipients": {"one@x.com": {"status": "sent"}}},
    "nl-old": {"recipients": {f"u{i}@x.com": {"status": "sent"} for i in range(60)}},
}
nl.load_state_raw = lambda: _state
nl.get_send_snapshot = lambda cid: _snaps.get(cid, {})


def _fake_cas(mutate):
    nxt = mutate(_state)
    return _state if nxt is not None else None


nl._cas_update_state = _fake_cas

nl._watch_dead_tracking()
check("exactly one page on first pass", len(_sent_emails) == 1)
check("page names the dead send", "Dead Send" in str(_sent_emails))
check("page goes to Jenna + Jessie only",
      _sent_emails and _sent_emails[0]["Destination"]["ToAddresses"]
      == ["jenna@crosswalknyc.com", "jessie@crosswalknyc.com"])
check("flag set on the campaign",
      bool(_state["campaigns"][0].get("ops_alert_dead_tracking_at")))
nl._watch_dead_tracking()
check("second pass pages nothing", len(_sent_emails) == 1)

print("5. probe alert dedupes the same way")
_sent_emails.clear()
fired = nl._ops_alert_once("nl-dead", "probe", "probe failed")
check("first probe alert sends", fired and len(_sent_emails) == 1)
fired = nl._ops_alert_once("nl-dead", "probe", "probe failed")
check("second probe alert suppressed", not fired and len(_sent_emails) == 1)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
