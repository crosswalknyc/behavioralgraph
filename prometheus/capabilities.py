"""Honest boundary for actions Prometheus cannot take (2026-10-02 S8).

Real sessions asked Prometheus to swap a card's key art, email a
third party, add a teammate, refund credits, rename a profile, change
a number by hand, set up a weekly run, and push a file into Google
Sheets. None of those are things this chat can do. Left to the model,
the ask either burned a paid read or came back as a cheerful "Done"
with nothing done.

``action_request(text)`` names the family of an out-of-scope action
request, or returns None when the message is a question, a read, or
one of the actions Prometheus DOES take (email the last file to any
address, notify an address when a build is ready, hand over the CSV,
report status, cancel a run, build, cut, and quote prices).

``cant_do_reply(kind)`` is the plain-English answer: what Prometheus
cannot do, what it can do instead, and who handles the rest. The
caller forwards the note to the team and never calls a model.

Pure module: no Flask, no S3, no model, so the chat module, the
replay harness, and the tests all run the same code. Kill switch:
``PM_CANT_DO_LANE=0``.
"""
from __future__ import annotations

import os
import re

KINDS = ('access', 'billing', 'library_edit', 'data_edit',
         'outreach', 'schedule', 'publish')

_MAX_LEN = 400

_ADDR = r"[\w.+-]+@[\w.-]+\.\w{2,}"

# Things Prometheus does. Any of these wins over the families below,
# so a supported command is never answered with a refusal.
_SUPPORTED_RX = re.compile(
    # email the last file to me / my inbox / an address
    r"\b(?:e-?mail|mail|send)\b[^.?!\n]{0,40}\b(?:me|my\s+inbox|this\s+file"
    r"|the\s+file|the\s+csv|it\s+to\s+" + _ADDR + r")\b"
    r"|\bemail\s+(?:it|this|that|the\s+(?:file|csv|data))\s+to\s+" + _ADDR +
    # ready notification
    r"|\bwhen\b[^.?!\n]{0,30}\b(?:ready|done|finished|complete[sd]?)\b"
    # the csv / open on screen
    r"|\b(?:download|csv|spreadsheet)\b"
    r"|\bopen\b[^.?!\n]{0,20}\b(?:on\s+screen|it\s+up|this\s+profile)\b"
    r"|\bshow\s+(?:me\s+)?(?:the|my|our|a|all|top|only|just)\b"
    # builds, cuts, reads
    r"|\b(?:run|build|pull|create|queue|start|launch|refresh|re-?run|cut)\b"
    r"[^.?!\n]{0,40}\b(?:profile|profiles|cut|read|audience|subscriber\s+iq"
    r"|brand\s+partnership|attribution|journey)\b"
    # run status, cancel a run, prices, definitions
    r"|\b(?:status|eta|how\s+long)\b[^.?!\n]{0,40}\b(?:build|run|pull|profile"
    r"|queue|it|that)\b"
    r"|\b(?:cancel|stop|kill)\b[^.?!\n]{0,20}\b(?:build|run|pull|job|that|it)\b"
    r"|\bhow\s+(?:is|are|was|were)\b[^.?!\n]{0,60}\b(?:calculated|measured"
    r"|defined|computed)\b"
    r"|\b(?:cost|costs|price|prices|pricing|rate\s+card)\b",
    re.I)

_DAY = (r"(?:week|month|day|quarter|morning|monday|tuesday|wednesday"
        r"|thursday|friday|saturday|sunday|weekday)")
_PUSH_TARGETS = (r"(?:google\s+(?:sheets?|drive|docs?)|sheets|g-?drive|drive"
                 r"|dropbox|box|sharepoint|onedrive|salesforce|hubspot|notion"
                 r"|airtable|tableau|looker|power\s*bi|snowflake|slack"
                 r"|linkedin|twitter|instagram|teams)")

_FAMILIES = (
    ('access', re.compile(
        r"\b(?:add|create|invite|onboard|remove|delete|deactivate|disable"
        r"|lock\s+out)\b[^.?!\n]{0,30}\b(?:a\s+)?(?:new\s+)?(?:user|users"
        r"|seat|seats|teammate|teammates|colleague|colleagues|login|logins"
        r"|account\s+for)\b"
        r"|\b(?:give|grant|revoke|extend|remove|take\s+away)\b[^.?!\n]{0,30}"
        r"\baccess\b"
        r"|\b(?:reset|change|update)\b[^.?!\n]{0,15}\b(?:my\s+)?(?:password"
        r"|login|username|email\s+address)\b"
        r"|\bforgot\s+my\s+password\b"
        r"|\b(?:add|get|invite)\b[^.?!\n]{0,20}\b(?:him|her|them|" + _ADDR +
        r")\b[^.?!\n]{0,20}\b(?:on|onto|into|to)\s+(?:the\s+)?(?:platform"
        r"|dashboard|account|tool)\b",
        re.I)),
    ('billing', re.compile(
        r"\b(?:refund|refunded|charge\s*back|credit\s+(?:me\s+)?back"
        r"|money\s+back)\b"
        r"|\b(?:add|buy|purchase|top\s+up|load|get\s+more|give\s+me\s+more"
        r"|restore)\b[^.?!\n]{0,15}\bcredits?\b"
        r"|\b(?:change|upgrade|downgrade|cancel|renew|pause)\b[^.?!\n]{0,15}"
        r"\b(?:my\s+|our\s+)?(?:plan|subscription|contract|seat)\b"
        r"|\b(?:send|resend|get|need|want|forward)\b[^.?!\n]{0,20}"
        r"\b(?:an?\s+)?(?:invoice|receipt|statement)\b"
        r"|\b(?:update|change|swap)\b[^.?!\n]{0,15}\b(?:card|billing"
        r"|payment\s+method)\b",
        re.I)),
    ('library_edit', re.compile(
        r"\b(?:rename|delete|remove|hide|archive|unpublish|take\s+down"
        r"|get\s+rid\s+of)\b[^.?!\n]{0,40}\b(?:profile|profiles|file|read"
        r"|report|deck|cut|entry|card)\b"
        r"|\b(?:change|update|edit|fix|set|swap|replace|upload|use)\b"
        r"[^.?!\n]{0,20}\b(?:key\s*art|artwork|image|images|photo|picture"
        r"|logo|thumbnail|poster|cover)\b"
        r"|\b(?:change|update|edit|rename|fix)\b[^.?!\n]{0,12}\b(?:the\s+)?"
        r"(?:profile\s+)?(?:name|title|category|display\s+name)\b"
        r"[^.?!\n]{0,30}\b(?:profile|file|read|card|this|it)\b"
        r"|\b(?:move|put)\b[^.?!\n]{0,30}\b(?:under|into)\b[^.?!\n]{0,20}"
        r"\b(?:category|folder|tab|bucket)\b",
        re.I)),
    ('data_edit', re.compile(
        r"\b(?:change|set|update|edit|fix|adjust|bump|lower|raise|override"
        r"|correct|make)\b[^.?!\n]{0,40}\b(?:number|numbers|value|values"
        r"|figure|figures|sample\s+size|penetration|percent|percentage"
        r"|split|index|reach|count|share|total)\b[^.?!\n]{0,40}\b(?:to|at)"
        r"\b\s*\$?\d"
        r"|\b(?:change|set|make|put)\b[^.?!\n]{0,40}\b(?:to|at)\s+\d+"
        r"(?:\.\d+)?\s*(?:%|percent)"
        r"|\b(?:remove|delete|drop|take\s+out|strip)\b[^.?!\n]{0,40}"
        r"\b(?:row|rows|brand|brands|line|lines)\b[^.?!\n]{0,30}\b(?:from"
        r"|off|out\s+of)\b[^.?!\n]{0,15}\b(?:the\s+)?(?:profile|file|read"
        r"|list)\b"
        r"|\b(?:hard\s*code|hardcode|manually\s+(?:set|change|edit|update))"
        r"\b",
        re.I)),
    ('outreach', re.compile(
        r"\b(?:reach\s+out\s+to|follow\s+up\s+with|loop\s+in)\s+\w+"
        r"|\b(?:slack|dm|text|call|phone|ping|message|cc)\b[^.?!\n]{0,30}\b(?:the\s+|my\s+"
        r"|our\s+)?(?:client|clients|customer|team|boss|partner|partners"
        r"|agency|brand|him|her|them|" + _ADDR + r")\b"
        r"|\b(?:send|forward|share)\b[^.?!\n]{0,40}\b(?:to|with)\b"
        r"[^.?!\n]{0,20}\b(?:the\s+|my\s+|our\s+)?(?:client|clients|customer"
        r"|team|boss|partner|partners|agency|brand\s+team|him|her|them)\b"
        r"|\b(?:set\s+up|schedule|book|arrange|put)\b[^.?!\n]{0,20}"
        r"\b(?:a\s+|an\s+)?(?:call|meeting|demo|zoom|time\s+with|time\s+to"
        r"\s+talk)\b",
        re.I)),
    ('schedule', re.compile(
        r"\b(?:schedule|automate|set\s+up)\b[^.?!\n]{0,30}\b(?:weekly|monthly"
        r"|daily|quarterly|recurring|every|automatic|automatically"
        r"|a\s+cadence)\b"
        r"|\b(?:every|each)\s+" + _DAY + r"\b[^.?!\n]{0,40}\b(?:run|pull"
        r"|refresh|send|email|rebuild|update|report|deliver)\b"
        r"|\b(?:run|pull|refresh|send|email|rebuild|update|deliver)\b"
        r"[^.?!\n]{0,40}\b(?:every|each)\s+" + _DAY + r"\b"
        r"|\b(?:weekly|monthly|daily|recurring|automatic)\s+(?:refresh"
        r"|rebuild|pull|run|delivery|email|report\s+to)\b"
        r"|\bremind\s+me\b|\bset\s+(?:a|an)\s+(?:reminder|alert|alarm)\b",
        re.I)),
    ('publish', re.compile(
        r"\b(?:upload|export|push|sync|save|load|send|put|post|publish"
        r"|connect)\b[^.?!\n]{0,30}\b(?:to|into|onto|on|in|with)\s+(?:my\s+"
        r"|our\s+|the\s+)?" + _PUSH_TARGETS + r"\b"
        r"|\b(?:integrate|integration)\b[^.?!\n]{0,20}\b(?:with\s+)?"
        + _PUSH_TARGETS + r"\b",
        re.I)),
)

# Priority when two families match. A refund that mentions a profile
# is billing; "share with the client" that names Slack is outreach.
_PRIORITY = ('billing', 'access', 'schedule', 'publish', 'outreach',
             'data_edit', 'library_edit')


def enabled():
    return str(os.environ.get('PM_CANT_DO_LANE', '1')).strip().lower() \
        not in ('0', 'false', 'no', 'off')


def action_request(text):
    """Family name for an out-of-scope action request, else None."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > _MAX_LEN:
        return None
    hits = [k for k, rx in _FAMILIES if rx.search(t)]
    if not hits:
        return None
    # A supported command wins, except where the family is clearly
    # outside the product (billing, access, schedule, publish never
    # overlap with a supported command the way outreach and the two
    # edit families can).
    if _SUPPORTED_RX.search(t) and not any(
            k in hits for k in ('billing', 'access', 'schedule', 'publish')):
        return None
    for k in _PRIORITY:
        if k in hits:
            return k
    return hits[0]


_REPLIES = {
    'access': (
        "I can't add users or change who has access from here. The "
        "Crosswalk account team handles seats and logins. I have passed "
        "your note to them, and they will reach you at the email on your "
        "account. In the meantime I can answer questions on any profile, "
        "pull a new one, or cut an existing one.",
        []),
    'billing': (
        "I can't change billing or move credits from here. The Crosswalk "
        "team handles refunds, credit top-ups, invoices, and plan changes. "
        "I have passed your note to them, and they will reach you at the "
        "email on your account. I can tell you what any pull costs before "
        "it runs.",
        ['What do credits cost?']),
    'library_edit': (
        "I can't rename, delete, or change the image on a profile from "
        "here. The Crosswalk team manages the library and the card "
        "images. I have passed your note to them. The read itself "
        "stands, and I can answer anything about it or cut it further.",
        []),
    'data_edit': (
        "I don't change numbers. Every figure is Crosswalk first-party "
        "measurement, so no value is edited by hand, including by me. If "
        "a number looks off, tell me which one and I will explain how it "
        "is measured and flag it for a second look.",
        ['How is this calculated?']),
    'outreach': (
        "I can't contact anyone for you: no calls, meetings, Slack, or "
        "messages to other people. What I can do is email the last file "
        "I handed you to any address you name, so the data reaches them "
        "straight from this chat. Say Email it to name@company.com.",
        ['Email me this file']),
    'schedule': (
        "I can't set up recurring runs or reminders yet. Each pull runs "
        "when you ask for it, and a finished profile stays in the "
        "library. Ask for the refresh when you want it and it runs right "
        "then. I have passed your note to the team so they know you want "
        "this on a schedule.",
        []),
    'publish': (
        "I can't push files into other tools such as Google Sheets, "
        "Drive, Slack, or a CRM. Every answer with data comes with a CSV "
        "you can download or have me email to any address, and that "
        "opens in any of those tools.",
        ['Email me this file']),
}


def cant_do_reply(kind):
    """(reply, followups) for a family from ``action_request``."""
    reply, chips = _REPLIES.get(kind, _REPLIES['library_edit'])
    return reply, list(chips)
