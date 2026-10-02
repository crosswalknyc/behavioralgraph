#!/usr/bin/env python3
"""Emailed reads carry a shareable branded PDF of the body, sent From
Prometheus with Jenna BCC'd (Jenna 2026-09-30: "attach pdfs of the
prometheus emails of what the email body says").

Static source checks on app.py plus a live render of
prometheus_email_pdf. No network, no SES.
"""
import re
import sys
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
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        FAILS.append(name)


# ---- live render -----------------------------------------------------
import prometheus_email_pdf as pep  # noqa: E402

SAMPLE = (
    "You asked how the audience compares. Here is the read.\n\n"
    "The short version: the younger cohort leads on every digital "
    "surface.\n\n"
    "WHERE THE NUMBERS SIT\n\n"
    "Cohort / US audience / Female / Under 25\n"
    "Alpha / 1,835,631 / 76.2% / 58.5%\n"
    "Beta / 92,634,007 / 73.6% / 53.3%\n\n"
    "Alpha out-reaches Beta on the taste-maker corners of the "
    "internet while Beta wins on raw scale.\n\n"
    "Prometheus\nCrosswalk")

pdf = pep.render_answer_pdf("Alpha vs Beta: the audience case", SAMPLE,
                            date_label="September 30, 2026",
                            table_highlight_prefix="Alpha")
check("render_answer_pdf returns PDF bytes", pdf.startswith(b"%PDF"))
check("render_answer_pdf output is substantial", len(pdf) > 5000)
check("render_answer_pdf empty body fails soft",
      pep.render_answer_pdf("t", "") == b"")

# Never raises: feed it garbage.
try:
    junk = pep.render_answer_pdf(None, 12345)
    check("render_answer_pdf never raises", isinstance(junk, bytes))
except Exception:
    check("render_answer_pdf never raises", False)

# ---- app.py wiring ---------------------------------------------------
APP = (_pm_app_path()).read_text(encoding="utf-8")

m = re.search(r"def _pm_send_output_email\(.*?\n(?=def |@(?:_H\.)?app\.route)",
              APP, re.DOTALL)
check("_pm_send_output_email found", bool(m))
FN = m.group(0) if m else ""

check("read branch renders the body PDF",
      "import prometheus_email_pdf" in FN
      and "render_answer_pdf" in FN)
check("PDF render is fail-safe (email still sends)",
      re.search(r"except Exception:\s*\n\s+pdf_bytes, pdf_name = b'', ''",
                FN) is not None)
check("attachment is conditional on render success",
      "if pdf_bytes and pdf_name:" in FN)
check("sent From Prometheus",
      FN.count("Prometheus <prometheus@crosswalknyc.com>") >= 2)
check("Reply-To routes to Jenna",
      "msg['Reply-To'] = 'jenna@crosswalknyc.com'" in FN)
check("Jenna BCC'd on every user-facing send",
      "dests.append('jenna@crosswalknyc.com')" in FN)
check("BCC dedupes when Jenna is the recipient",
      "to_email.lower() != 'jenna@crosswalknyc.com'" in FN)
check("signature reads Prometheus / Crosswalk",
      "Prometheus<br>Crosswalk" in FN and "Crosswalk IQ" not in FN)
check("raw MIME send (attachment-capable)", "send_raw_email" in FN)
import re as _re
check("email names the shareable PDF",
      bool(_re.search(r'attached as a PDF you\s*"\s*"?\s*can\s*"?\s*"?\s*share', FN))
      or "attached as a PDF you can share" in FN.replace('"\n', '').replace('" "', ''))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all checks passed")
