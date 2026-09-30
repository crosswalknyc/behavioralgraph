#!/usr/bin/env python3
"""Attribution view follow-ups ground in the campaign on screen
(Jenna 2026-09-30: "where is the fall-off from the ticket checkout
page?" drew "Do you mean for Paw Patrol Series Viewers, or
Obsession?"). Covers the funnel vocabulary, the view-vocabulary
overlap, the last-rung campaign steer, the intentIQ hydration, the
headed-block renderer expansion, and the correction-email tool that
banks fixes into the user's chat history."""
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_APP = ROOT / "app.py"
if not _APP.exists():
    _APP = ROOT / "bg-webapp" / "app.py"
SRC = _APP.read_text()

FAIL = 0


def check(name, ok):
    global FAIL
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        FAIL += 1


# ------------------------------------------------------------------
# View-owns helpers: funnel vocabulary + on-screen vocabulary.
# ------------------------------------------------------------------
m = re.search(r"(_PM_VIEW_DEIXIS_RE = re\.compile.*?)\n\n\ndef "
              r"_pm_open_screen_confirm", SRC, re.S)
check("helpers span present", m is not None)
ns = {"re": re}
exec(m.group(1), ns)
owns = ns["_pm_view_owns_ask"]
vocab_hit = ns["_pm_view_vocab_hit"]

IIQ = {"view_context": {"view_id": "intentIQ",
                        "view_title": "Attribution IQ", "data": {}}}
SUB = {"view_context": {"view_id": "subscriberIQ",
                        "view_title": "Subscriber IQ", "data": {}}}

check("the defect ask grounds on the Attribution view",
      owns("where is the fall-off from the ticket checkout page?", IIQ))
check("checkout vocabulary owns the Attribution view",
      owns("why do people abandon at checkout?", IIQ))
check("touch vocabulary owns the Attribution view",
      owns("what was the strongest last-touch?", IIQ))
check("funnel vocabulary does not own other views bare",
      not owns("where is the fall-off from the ticket checkout page?",
               SUB))
check("original campaign vocabulary still owns",
      owns("What drove conversion in this campaign window?", IIQ))
check("no view context never owns",
      not owns("where is the fall-off from checkout?", {}))

# On-screen vocabulary overlap binds any data view.
HYD = {"view_context": {
    "view_id": "subscriberIQ", "view_title": "Subscriber IQ",
    "data": {"show": "Landman", "episodes": [
        {"title": "The Death of Us", "signups": 24138},
        {"title": "Sins of the Father", "signups": 18211}]}}}
check("ask naming on-screen items binds the view",
      owns("how did Sins of the Father do against Landman overall?",
           HYD))
check("generic ask with no on-screen words stays unbound",
      not owns("how are our competitors doing this quarter?", HYD))
check("vocab hit needs real overlap",
      not vocab_hit("tell me about the weather",
                    HYD["view_context"]))

# ------------------------------------------------------------------
# Last rung: subjectless funnel asks steer to the campaign, never to
# profile disambiguation.
# ------------------------------------------------------------------
check("last-rung funnel steer wired",
      "_lr_funnel = False" in SRC
      and "or _lr_funnel:" in SRC
      and SRC.find("_lr_funnel = False")
      < SRC.find("outcome='campaign_clarify'"))
check("funnel steer guarded to subjectless asks",
      re.search(r"_lr_funnel = bool\(\s*\n?\s*_PM_FUNNEL_ASK_RE"
                r".*?guess_subject_from_text", SRC, re.S) is not None)

# ------------------------------------------------------------------
# intentIQ hydration: campaign numbers ride the view context.
# ------------------------------------------------------------------
check("hydrator defined",
      "def _pm_intent_view_hydrate(" in SRC
      and "def _pm_intent_compact_numbers(" in SRC)
check("hydration wired into the context validator",
      "_pm_intent_view_hydrate(view_ctx)" in SRC
      and SRC.find("_pm_intent_view_hydrate(view_ctx)")
      < SRC.find("primary = page_context.get('primary') or {}"))

mc = re.search(r"(def _pm_intent_compact_numbers.*?)\n\n\ndef "
               r"_pm_intent_view_hydrate", SRC, re.S)
check("compactor extractable", mc is not None)
cns = {"re": re, "json": json, "traceback": traceback}
exec(mc.group(1), cns)
compact = cns["_pm_intent_compact_numbers"]

DOC = {
    "display_name": "The Influencer Project - Hades",
    "as_of": "2026-09-30",
    "overall": {
        "conversion_rate": 0.0613,
        "paths": {
            "nest": [
                {"stage": "0_tam", "label": "US average",
                 "us_accounts": 329900000},
                {"stage": "1_exposed", "label": "Exposed",
                 "us_accounts": 1115908},
                {"stage": "2_infoseek", "label": "Info-seek within 7d",
                 "us_accounts": 339661},
                {"stage": "3_ticketer",
                 "label": "Reached the cart/purchase page within 7d",
                 "us_accounts": 92816},
                {"stage": "4_paid",
                 "label": "Reached the checkout page within 7d",
                 "us_accounts": 68473}],
            "forks": [{"of_stage": "3_ticketer",
                       "question": "multi-surface",
                       "yes": 22203, "no": 70613}],
            "where": {"ticketer_partition": [
                {"surface": "Fandango", "us_accounts": 28963,
                 "pct": 31.2}]},
            "attribution": {
                "first_touch": [{"touchpoint": "TikTok",
                                 "us_accounts": 22664, "pct": 33.1}],
                "last_touch": [{"touchpoint": "YouTube",
                                "us_accounts": 19515, "pct": 28.5}],
                "assists": [{"touchpoint": "Coupon query",
                             "us_accounts": 26795, "pct": 39.1}]},
            "time_to_conversion": [
                {"bucket": "2-7 days", "us_accounts": 29375,
                 "pct": 42.9}]}},
    "audiences": {
        "social_media_power_users": {"paths": {"nest": [
            {"stage": "3_ticketer", "us_accounts": 6544},
            {"stage": "4_paid", "us_accounts": 4704}]}},
        "content_creators_influencers": {"paths": {"nest": [
            {"stage": "3_ticketer", "us_accounts": 6208},
            {"stage": "4_paid", "us_accounts": 5034}]}}},
}
out = compact(DOC)
steps = out.get("journey_steps") or []
falls = out.get("step_falloff") or []
last_fall = falls[-1] if falls else {}
check("compactor drops the US-average row and keeps 4 steps",
      len(steps) == 4 and steps[0]["accounts"] == 1115908)
check("checkout fall-off math is exact",
      last_fall.get("lost_accounts") == 24343
      and last_fall.get("lost_pct") == 26.2)
check("audience fall-off sorted worst first",
      (out.get("audience_checkout_falloff") or [{}])[0].get("audience")
      == "Social Media Power Users"
      and out["audience_checkout_falloff"][0]["checkout_falloff_pct"]
      == 28.1)
check("surfaces, touches, assists, timing all ride",
      out.get("ticket_checkout_surfaces")
      and out.get("first_touch") and out.get("assists")
      and out.get("time_to_convert")
      and out.get("compared_multiple_ticket_surfaces",
                  {}).get("yes") == 22203)
blob = json.dumps(out).lower()
check("compact payload carries no internal vocabulary",
      not any(b in blob for b in
              ("coefficient", "regression", "logit", "mta",
               "odds ratio", "panel", "clickstream")))
check("compactor fails soft on junk", compact(None) == {}
      and compact({"overall": "x"}) == {})

# The hydrated context makes the defect ask bind through vocabulary
# alone (even without the funnel regex).
hyd_vc = {"view_id": "intentIQ", "view_title": "Attribution IQ",
          "data": {"title": "the_influencer_project_hades",
                   "campaign_numbers": out}}
check("hydrated context binds the defect ask by vocabulary",
      vocab_hit("where is the fall-off from the ticket checkout page?",
                hyd_vc))

# ------------------------------------------------------------------
# Renderers: a header stacked on its rows still renders richly.
# ------------------------------------------------------------------
sys.path.insert(0, str(ROOT))
import prometheus_email_html as peh  # noqa: E402
import prometheus_email_pdf as pep  # noqa: E402

BODY = ("Hi,\n\nTHE STEP IN NUMBERS\nReached a ticket page / 92,816\n"
        "Reached the checkout page / 68,473\n\nWHAT THE STEP LOOKS "
        "LIKE\n- Price-checking, not lost interest. Detail follows.\n"
        "- The recovery window is real. Detail follows.\n\n"
        "Prometheus\nCrosswalk")
h = peh.render_answer_email_html("T", BODY)
check("html: headed KPI block renders as table",
      bool(re.search(r"<td[^>]*>92,816</td>", h)))
check("html: headed bullet block renders as bullets",
      "<ul" in h and h.count("<li") == 2)
check("html: header renders as header div",
      ">THE STEP IN NUMBERS</div>" in h)
p = pep.render_answer_pdf("T", BODY)
check("pdf: headed blocks build", bool(p) and len(p) > 1500)
check("both renderers carry the expansion helper",
      "_expand_blocks" in (ROOT / "prometheus_email_html.py").read_text()
      and "_expand_blocks" in (ROOT / "prometheus_email_pdf.py").read_text())

# ------------------------------------------------------------------
# Correction tool: email + history bank in one command.
# ------------------------------------------------------------------
tool = (ROOT / "scripts" / "pm_correction_email.py").read_text()
check("correction tool sends from Prometheus",
      "Prometheus <prometheus@crosswalknyc.com>" in tool)
check("correction tool BCCs Jenna and replies to Jenna",
      "BCC = 'jenna@crosswalknyc.com'" in tool
      and "REPLY_TO = 'jenna@crosswalknyc.com'" in tool)
check("correction tool banks the thread",
      "correction_email" in tool and "synth_chat_threads" in tool
      and "'updated'" in tool.replace('"', "'"))
check("correction tool scrubs banned vocabulary",
      "_scrub_assert" in tool and "banned vocabulary" in tool)

print(("\nALL PASS" if FAIL == 0 else f"\n{FAIL} FAILURES"))
sys.exit(1 if FAIL else 0)
