#!/usr/bin/env python3
"""Every data answer creates its CSV, downloadable and emailable
(Jenna 2026-09-29). Hermetic checks on the email intent and stash
helpers plus source wiring for the always-build and render paths."""
import io
import json
import os
import re
import sys
import time
from pathlib import Path
# Legacy-move shim (2026-10-01): the Prometheus code lives in
# bg-webapp/prometheus/legacy/chat.py; read app.py + legacy as one source.
import os as _pm_os, sys as _pm_sys
_pm_r = _pm_os.path.dirname(_pm_os.path.abspath(__file__))
while not _pm_os.path.exists(_pm_os.path.join(_pm_r, 'bg-webapp', 'app.py')):
    _pm_r = _pm_os.path.dirname(_pm_r)
_pm_sys.path.insert(0, _pm_os.path.join(_pm_r, 'scripts'))
from _pm_test_source import app_path as _pm_app_path, host_for as _pm_host_for  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
_APP = _pm_app_path()
# The suite lives in both repos: ROOT is the web app when run from
# bg-webapp/scripts, the parent repo when run from scripts/.
_WEB = ROOT if (ROOT / "templates" / "index.html").exists() else ROOT / "bg-webapp"
_IDX = _WEB / "templates" / "index.html"
SRC = _APP.read_text()
IDX = _IDX.read_text()

FAIL = 0


def check(name, ok):
    global FAIL
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        FAIL += 1


# ------------------------------------------------------------------
# Email intent + stash helpers, hermetically.
# ------------------------------------------------------------------
class _S3Stub:
    def __init__(self):
        self.docs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.docs:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.docs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.docs[Key] = Body if isinstance(Body, bytes) else Body


stub = _S3Stub()
ns = {"re": re, "json": json, "time": time, "s3_client": stub,
      "S3_BUCKET": "b", "traceback": __import__("traceback")}
m = re.search(r"(_PM_LAST_FILE_PREFIX = .*?)\n\n\ndef "
              r"_pm_answer_file_payload", SRC, re.S)
check("stash helpers present", m is not None)
_pm_host_for(ns)
exec(m.group(1), ns)
m2 = re.search(r"(_PM_EMAIL_FILE_RE = re\.compile.*?)\n\n\ndef "
               r"_pm_email_file_response", SRC, re.S)
check("email intent block present", m2 is not None)
_pm_host_for(ns)
exec(m2.group(1), ns)

intent = ns["_pm_email_file_intent"]
check("chip text is an email ask", intent("Email me this file"))
check("plain email ask detected", intent("email me the csv"))
check("email it to me detected", intent("can you email it to me"))
check("typed address detected",
      intent("email it to casey.pearson@paramount.com"))
check("send to my inbox detected", intent("send it to my inbox"))
check("data question is not an email ask",
      not intent("what were the total streaming hours by genre and "
                 "platform for paramount plus subscribers over the "
                 "trailing 12 months"))
check("download ask is not an email ask",
      not intent("download this data as a csv"))

addr_re = ns["_PM_EMAIL_ADDR_RE"]
check("address extraction works",
      addr_re.search("email it to casey.pearson@paramount.com")
      .group(0) == "casey.pearson@paramount.com")

w = ns["_pm_file_stash_write"]
r = ns["_pm_file_stash_read"]
w("CPearson", "https://u", "Hours.csv", "generated_data/x/Hours.csv",
  subject="Paramount+", question="hours by genre")
got = r("cpearson")
check("stash round-trips case-insensitively",
      got.get("filename") == "Hours.csv"
      and got.get("s3_key") == "generated_data/x/Hours.csv")
check("missing stash reads empty", r("nobody") == {})
check("blank username never writes", w("", "u", "f", "k") is None
      and not [k for k in stub.docs if k.endswith("/.json")])

# ------------------------------------------------------------------
# Source wiring.
# ------------------------------------------------------------------
check("generate builds on every data answer",
      "if res.get('breakdown') or res.get('metrics'):\n"
      "            _explicit = bool(_PM_FILE_ASK_RE.search" in SRC)
check("file link rides every data answer",
      "_file_payload = {'file_link': {" in SRC)
check("auto-save only on explicit file asks",
      "if _explicit:\n                _file_payload.update" in SRC)
check("generate stashes the file",
      "_pm_file_stash_write(pm_user, _furl, _fn, _fkey," in SRC)
check("email chip on data answers",
      SRC.count("followups.append('Email me this file')") >= 1)
check("replay builds the CSV",
      "_rp_file = _pm_answer_file_payload(" in SRC)
check("replay payload carries the file", "**_rp_file})" in SRC)
check("download chip retired from generate",
      "followups = [f for f in followups if f != pma.CSV_OFFER_CHIP]"
      in SRC)
check("email intercept runs before routing",
      "if _pm_email_file_intent(text):\n"
      "        return _pm_email_file_response(user, text)" in SRC)
check("email sends from Prometheus",
      "msg['From'] = 'Prometheus <prometheus@crosswalknyc.com>'"
      in SRC)
# One outbound mail door (2026-10-06): the CSV send goes through
# prometheus.outbound_mail.send_user_email, which BCCs Jenna on every
# user-facing send.
_om_src = (_WEB / 'prometheus' / 'outbound_mail.py').read_text(encoding='utf-8')
check("Jenna rides every send",
      "_om.send_user_email(" in SRC and "caller='csv-by-email'" in SRC
      and "for b in [JENNA]" in _om_src)
check("reply-to is Jenna",
      "msg['Reply-To'] = 'jenna@crosswalknyc.com'" in SRC)
check("typed-download path stashes too",
      "_pm_file_stash_write(\n        (session.get('username')" in SRC)

# Frontend render wiring.
check("analyze push renders the file link",
      "if (data.file_link && data.file_link.url &&" in IDX)
check("async delivery renders the file link",
      "if (p.file_link && p.file_link.url &&" in IDX)
check("async delivery auto-saves explicit asks",
      "try { _synthChatMaybeSaveFile(p); } catch (_) {}" in IDX)
check("renderer supports meta links already",
      "turn.meta.link.url" in IDX)

print()
if FAIL:
    print(f"{FAIL} FAILED")
    sys.exit(1)
print("all csv-every-answer checks passed")
