#!/usr/bin/env python3
"""The light email design is the standing design for Prometheus sends
(Jenna 2026-09-30: "I prefer this design for emails moving forward").

Covers prometheus_email_html (the email body renderer), the light
retheme of prometheus_email_pdf, and the _pm_send_output_email wiring
in app.py. No network, no SES.
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


import prometheus_email_html as peh  # noqa: E402
import prometheus_email_pdf as pep  # noqa: E402

SAMPLE = (
    "Hi Alexia,\n\n"
    "You asked what drove conversion. Here is the read.\n\n"
    "The short answer\n\n"
    "TikTok opened the journey and the trailer closed it.\n\n"
    "The window in four numbers\n\n"
    "Exposed to the campaign / 1,115,908 US accounts\n"
    "Reached the checkout page / 68,473\n\n"
    "What drove it\n\n"
    "- Horror-fan posts punched hardest per view. Converting at nearly "
    "twice the campaign average.\n"
    "- The trailer closed. 71.4% of converters touched it within 48 "
    "hours.\n\n"
    "Artist / US audience / Female\n"
    "Alpha / 1,835,631 / 76.2%\n"
    "Beta / 92,634,007 / 73.6%\n\n"
    "Prometheus\nCrosswalk")

html = peh.render_answer_email_html(
    "The Influencer Project: what drove conversion in this window.",
    SAMPLE, table_highlight_prefix="Alpha")

check("html renders", bool(html) and "</html>" in html)
check("soft off-white page", "#F4F3EE" in html)
check("white content column", "#FFFFFF" in html)
check("Signal Olive eyebrow (light-surface twin)",
      "#5E7E12" in html and "PROMETHEUS" in html)
check("Signal Green never on a light surface", "#C7F23E" not in html)
check("old navy shell tokens gone", "#0a1929" not in html
      and "#66d9ef" not in html)
check("section headers render bold ink",
      "font-weight:700'>The short answer</div>" in html
      and "font-size:17px" in html)
check("label/value table with right-aligned bold values",
      "1,115,908 US accounts" in html
      and "font-weight:700;text-align:right" in html)
check("grid table highlights the subject row in olive",
      re.search(r"color:#5E7E12;font-size:13px;\s*font-weight:700",
                html) is not None)
check("bullets bold their first sentence",
      "<strong style='color:#0C1618'>Horror-fan posts punched hardest "
      "per view.</strong>" in html)
check("signature reads Prometheus / Crosswalk",
      "Prometheus<br>" in html and "Crosswalk" in html)
check("footer lockup present",
      "BEHAVIORAL INTELLIGENCE ENGINE" in html)
check("empty body fails soft", peh.render_answer_email_html("t", "") == "")

cta = peh.render_answer_email_html(
    "Your deck", "Your deck is ready.\n\nPrometheus\nCrosswalk",
    cta_url="https://example.com/deck.pptx", cta_text="Download the deck")
check("cta button renders on https urls",
      "https://example.com/deck.pptx" in cta
      and "Download the deck" in cta)
check("cta rejected on non-https", "javascript" not in
      peh.render_answer_email_html(
          "t", "x\n\nPrometheus\nCrosswalk",
          cta_url="javascript:alert(1)", cta_text="x"))

# ---- the PDF twin ------------------------------------------------------
pdf = pep.render_answer_pdf("Test read", SAMPLE,
                            table_highlight_prefix="Alpha")
check("light PDF renders", pdf.startswith(b"%PDF") and len(pdf) > 5000)
PSRC = (ROOT / "prometheus_email_pdf.py").read_text(encoding="utf-8")
check("PDF module carries the light palette",
      'PAGE = "#F4F3EE"' in PSRC and 'OLIVE = "#5E7E12"' in PSRC)
check("PDF module dropped the dark ground",
      "GRAPHITE" not in PSRC and "#C7F23E" not in PSRC)
check("PDF handles bullets", "_is_bullets" in PSRC
      and "ListFlowable" in PSRC)
check("PDF handles label/value tables", "if n == 2:" in PSRC)

# ---- app wiring --------------------------------------------------------
APP = (_pm_app_path()).read_text(encoding="utf-8")
m = re.search(r"def _pm_send_output_email\(.*?\n(?=def |@(?:_H\.)?app\.route)",
              APP, re.DOTALL)
FN = m.group(0) if m else ""
check("read branch renders the light email html",
      FN.count("import prometheus_email_html") == 2
      and "render_answer_email_html" in FN)
check("legacy shell survives only as fallback",
      FN.count("_wrap_email_html") == 2
      and FN.count("if not body_html:") == 2)
check("deck branch passes the download cta",
      "cta_text='Download the deck'" in FN)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all checks passed")
