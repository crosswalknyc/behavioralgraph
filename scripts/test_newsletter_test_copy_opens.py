#!/usr/bin/env python3
"""Regression: opens on a [Test] copy never make an unsent letter sent.

2026-11-24 incident (the-read-black-friday-deals): two staff members
opened [Test] copies of a draft. The open pixel wrote each opener into the
campaign's send snapshot with status "sent", so the stats read
recipients 2 / sent 2, _effective_status read the draft as sent, and the
normalize_state heal made that sticky with a sent_at. A letter that reads
as sent qualifies for the public The Read archive, so the unsent draft
could have been listed publicly, and the test opens leaked into stats.

All hermetic: NEWSLETTER_LOCAL=1 against a temp dir, mail and the
pre-send network check stubbed, and a fake CAS helper that refuses the
live store.

1. Test-copy opens and clicks on a draft land as "test" rows. The draft
   stays a draft with no sent_at through the heal, stays off the public
   archive, its stats count nothing, and the report and export skip it.
2. Rows a test open wrote before this fix (status "sent", no claim, no
   sent_at) never promote a draft, and a later open never downgrades them.
3. A scheduled letter's test opens never promote it.
4. The real send queues the test-copy addresses with zeroed engagement,
   never sends to a test-only address, and starts its stats from zero.
5. After the send, a standalone copy's opener counts as before.
6. A real send that crashed back to draft still reads as sent; a claim
   that delivered nothing does not.
7. A stray "test" row is never a send target.

Run: python3 scripts/test_newsletter_test_copy_opens.py
"""
import os
import re
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

os.environ["NEWSLETTER_LOCAL"] = "1"
os.environ["NEWSLETTER_LOCAL_DIR"] = tempfile.mkdtemp(prefix="nl-test-copy-")


def _no_live_store(*args, **kwargs):
    raise RuntimeError("hermetic test reached the live store")


# Local mode never needs the live CAS helper; anything that reaches for it fails.
_fake_mod = types.ModuleType("s3_json_state")
_fake_mod.update_json = _no_live_store
sys.modules["s3_json_state"] = _fake_mod

import newsletter as nl  # noqa: E402
from flask import Flask  # noqa: E402

nl._shared_secret_cache["val"] = b"newsletter-test-copy-secret"

_mail = []


def _fake_send(to_email, subject, html, *args, **kwargs):
    _mail.append({"to": to_email, "subject": subject, "html": html})


class _NoSes:
    def send_email(self, **kwargs):
        raise RuntimeError("hermetic test tried to send mail")


nl.send_one_email = _fake_send
nl._ses = lambda: _NoSes()
nl._tracking_probe = lambda base, campaign_id: True
nl.bank_issue_figures = lambda *args, **kwargs: {"banked": [], "source": "", "warning": ""}
nl.nli.maybe_autopost = lambda cid: None

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


LIST_ID = "list-main"
DRAFT = "the-read-black-friday-deals"
LEGACY = "nl-legacy-draft"
SCHEDULED = "nl-scheduled"
STRAY = "nl-stray-row"
STRAY_DONE = "nl-stray-row-done"
CRASHED = "nl-crashed-send"
UNDELIVERED = "nl-claimed-undelivered"
TESTER_A = "tester-a@example.com"
TESTER_B = "tester-b@example.com"
READER = "reader@example.com"
GUEST = "guest@example.org"   # gets a [Test] copy, not on the list
PRESS = "press@example.org"   # gets a standalone copy after the send
HTML = (
    "<html><body><p>Black Friday streaming deals.</p>"
    '<a href="https://www.crosswalknyc.com/">Crosswalk</a> '
    '<a href="%%unsubscribe%%">Unsubscribe</a></body></html>'
)
T0 = "2026-11-24T15:46:54+00:00"


def _camp(cid, status, **extra):
    row = {
        "id": cid, "name": cid, "subject": f"Subject {cid}", "list_id": LIST_ID,
        "status": status, "created_at": T0, "updated_at": T0, "sent_at": None,
        "scheduled_at": None, "stats": nl._empty_stats(),
    }
    row.update(extra)
    return row


def _seed(st):
    st["subscribers"] = [
        {"email": email, "name": name, "status": "subscribed", "list_ids": [LIST_ID], "tags": []}
        for email, name in ((TESTER_A, "Tester A"), (TESTER_B, "Tester B"), (READER, "Reader"))
    ]
    st.setdefault("lists", []).append({"id": LIST_ID, "name": "Main list", "kind": "manual"})
    st["campaigns"] = [
        _camp(DRAFT, "draft"),
        _camp(LEGACY, "draft", stats={**nl._empty_stats(), "recipients": 2, "sent": 2,
                                      "opens": 37, "unique_opens": 2, "clicks": 1}),
        _camp(SCHEDULED, "scheduled", scheduled_at="2099-01-05T17:00:00+00:00"),
        _camp(STRAY, "sending", stats={**nl._empty_stats(), "recipients": 1}),
        # stale stats that still count the test row, so the heal cannot finish it first
        _camp(STRAY_DONE, "sending", stats={**nl._empty_stats(), "recipients": 2, "sent": 1}),
    ]
    return st


nl._cas_update_state(_seed)
for _cid in (DRAFT, LEGACY, SCHEDULED, STRAY, STRAY_DONE):
    nl.put_campaign_html(_cid, HTML)

# What the pre-fix tracker left on the incident draft: engagement-only rows marked sent.
nl.put_send_snapshot(LEGACY, {"recipients": {
    TESTER_A: {"name": "", "status": "sent", "open_count": 20, "opened_at": T0, "clicks": []},
    TESTER_B: {"name": "", "status": "sent", "open_count": 17, "opened_at": T0, "clicks": [
        {"url": "https://www.crosswalknyc.com/", "count": 1, "at": T0, "last_at": T0}]},
}})
nl.put_send_snapshot(CRASHED, {
    "campaign_id": CRASHED, "started_at": "2026-11-20T18:00:00+00:00", "finished_at": None,
    "recipients": {
        TESTER_A: {"status": "sent", "sent_at": "2026-11-20T18:00:05+00:00", "open_count": 0, "clicks": []},
        READER: {"status": "queued", "sent_at": None, "open_count": 0, "clicks": []},
    },
})
nl.put_send_snapshot(UNDELIVERED, {
    "campaign_id": UNDELIVERED, "started_at": "2026-11-20T18:00:00+00:00", "finished_at": None,
    "recipients": {
        TESTER_A: {"status": "failed", "error": "throttled", "open_count": 0, "clicks": []},
        GUEST: {"status": "sent", "open_count": 2, "opened_at": T0, "clicks": []},
    },
})
nl.put_send_snapshot(STRAY, {
    "campaign_id": STRAY, "started_at": "2026-11-24T18:39:51+00:00", "finished_at": None,
    "recipients": {
        READER: {"name": "Reader", "status": "queued", "sent_at": None, "open_count": 0, "clicks": []},
        GUEST: {"name": "", "status": nl.NON_COUNTING_STATUS, "open_count": 3, "opened_at": T0, "clicks": []},
    },
})
nl.put_send_snapshot(STRAY_DONE, {
    "campaign_id": STRAY_DONE, "started_at": "2026-11-24T18:39:51+00:00", "finished_at": None,
    "recipients": {
        READER: {"name": "Reader", "status": "sent", "sent_at": "2026-11-24T18:40:02+00:00",
                 "open_count": 0, "clicks": []},
        GUEST: {"name": "", "status": nl.NON_COUNTING_STATUS, "open_count": 1, "opened_at": T0, "clicks": []},
    },
})

app = Flask(
    __name__,
    template_folder=os.path.join(HERE, "templates"),
    static_folder=os.path.join(HERE, "static"),
)
app.config["TESTING"] = True
app.secret_key = "newsletter-test-copy"
app.register_blueprint(nl.newsletter_bp)
client = app.test_client()
with client.session_transaction() as sess:
    sess["nl_studio"] = "studio@crosswalknyc.com"

PIXEL_RE = re.compile(r"/n/o/[A-Za-z0-9_\-]+\.gif")
CLICK_RE = re.compile(r"/n/c/[A-Za-z0-9_\-]+")


def _test_copy(cid, email):
    """Send a [Test] copy through send_test; return its subject, pixel path, click path."""
    before = len(_mail)
    ok, msg = nl.send_test(cid, email)
    if not ok or len(_mail) != before + 1:
        raise RuntimeError(f"send_test failed for {cid} -> {email}: {msg}")
    copy = _mail[before]
    return copy["subject"], PIXEL_RE.search(copy["html"]).group(0), CLICK_RE.search(copy["html"]).group(0)


def _archive_ids(state=None):
    return [row["id"] for row in nl.public_archive_issues(state if state is not None else nl.load_state_raw())]


def _stat_tuple(stats):
    stats = stats or {}
    keys = ("recipients", "sent", "opens", "unique_opens", "clicks", "unique_clicks")
    return tuple(int(stats.get(k) or 0) for k in keys)


print("1. [Test] copy opens and clicks on a draft never make it sent")
subj_a, pix_a, click_a = _test_copy(DRAFT, TESTER_A)
_subj_b, pix_b, _click_b = _test_copy(DRAFT, TESTER_B)
_subj_g, pix_g, _click_g = _test_copy(DRAFT, GUEST)
check("test copy carries the [Test] subject", subj_a.startswith("[Test]"))
check("open pixel answers 200", client.get(pix_a).status_code == 200)
client.get(pix_a)
client.get(pix_a)
check("click redirects", client.get(click_a).status_code == 302)
client.get(pix_b)
client.get(pix_b)
client.get(pix_g)
snap = nl.get_send_snapshot(DRAFT)
rows = snap.get("recipients") or {}
check("every opener lands as a test row",
      all((rows.get(e) or {}).get("status") == nl.NON_COUNTING_STATUS for e in (TESTER_A, TESTER_B, GUEST)))
check("the test row keeps its own opens and clicks",
      rows[TESTER_A].get("open_count") == 3 and len(rows[TESTER_A].get("clicks") or []) == 1)
check("no claim is stamped on the snapshot", not snap.get("started_at"))
check("stats from the snapshot count nothing", _stat_tuple(nl._stats_from_snapshot(snap)) == (0,) * 6)
state = nl.load_state()  # runs the normalize_state heal and persists it
camp = nl._campaign(state, DRAFT)
check("draft stays draft through the heal", camp.get("status") == "draft")
check("no sent_at is stamped", not camp.get("sent_at"))
check("effective status reads draft", nl._effective_status(camp) == "draft")
check("campaign stats count no recipients, sends, opens or clicks", _stat_tuple(camp.get("stats")) == (0,) * 6)
check("draft is not in the public archive computation", DRAFT not in _archive_ids(state))
feed = client.get("/n/archive.json").get_json() or {}
check("public archive feed leaves it out", DRAFT not in [row.get("id") for row in feed.get("issues") or []])
check("public issue page refuses it", client.get(f"/the-read/{DRAFT}").status_code == 404)
report = client.get(f"/api/admin/newsletter/campaigns/{DRAFT}/report")
body = report.get_json() or {}
check("report lists no test rows and no test clicks",
      report.status_code == 200 and body.get("recipients") == [] and body.get("top_links") == [])
check("report stats count nothing", _stat_tuple(body.get("stats")) == (0,) * 6)
check("report campaign reads draft", (body.get("campaign") or {}).get("status") == "draft")
_name, _headers, export_rows = nl.campaign_results_export_table(DRAFT, state=state, snap=snap, store={})
check("results export skips test rows", export_rows == [])
client.get(pix_a)
row_a = nl.get_send_snapshot(DRAFT)["recipients"][TESTER_A]
check("a later open keeps the row non-counting",
      row_a.get("status") == nl.NON_COUNTING_STATUS and row_a.get("open_count") == 4)

print("2. rows a test open wrote before the fix never promote a draft")
legacy = nl._campaign(nl.load_state_raw(), LEGACY)
check("pre-fix draft with stats.sent 2 reads as draft", nl._effective_status(legacy) == "draft")
state = nl.load_state()
legacy = nl._campaign(state, LEGACY)
check("heal leaves it a draft with no sent_at", legacy.get("status") == "draft" and not legacy.get("sent_at"))
check("it stays out of the public archive computation", LEGACY not in _archive_ids(state))
_s, pix_legacy, _c = _test_copy(LEGACY, TESTER_A)
client.get(pix_legacy)
row_legacy = nl.get_send_snapshot(LEGACY)["recipients"][TESTER_A]
check("a later open never downgrades an existing sent row",
      row_legacy.get("status") == "sent" and row_legacy.get("open_count") == 21)
legacy = nl._campaign(nl.load_state(), LEGACY)
check("still a draft after that open recomputes its stats",
      legacy.get("status") == "draft" and nl._effective_status(legacy) == "draft" and not legacy.get("sent_at"))

print("3. a scheduled letter's test opens never promote it")
_s, pix_sched, _c = _test_copy(SCHEDULED, TESTER_B)
client.get(pix_sched)
row_sched = nl.get_send_snapshot(SCHEDULED)["recipients"][TESTER_B]
check("the scheduled letter's opener is a test row", row_sched.get("status") == nl.NON_COUNTING_STATUS)
sched = nl._campaign(nl.load_state(), SCHEDULED)
check("stays scheduled with no sent_at", sched.get("status") == "scheduled" and not sched.get("sent_at"))
check("scheduled never reads as sent, whatever its stats say",
      nl._effective_status({**sched, "stats": {"recipients": 5, "sent": 5}}) == "scheduled")
check("scheduled stays out of the public archive computation", SCHEDULED not in _archive_ids())

print("4. the real send queues the test-copy addresses with zeroed engagement")
recips, err = nl._claim_send(DRAFT, LIST_ID)
check("claim succeeds", err is None)
check("claim covers the list, not the test-only guest", set(recips or {}) == {TESTER_A, TESTER_B, READER})
check("test-copy addresses are queued with zeroed opens, clicks and unsubs", all(
    recips[e]["status"] == "queued" and recips[e]["opened_at"] is None and recips[e]["open_count"] == 0
    and recips[e]["clicks"] == [] and recips[e]["unsubscribed_at"] is None
    for e in (TESTER_A, TESTER_B)
))
check("claim stamps started_at", bool(nl.get_send_snapshot(DRAFT).get("started_at")))
before = len(_mail)
result = nl._run_send(DRAFT)
real = _mail[before:]
check("send delivers to the list, never the test-only guest",
      sorted(m["to"] for m in real) == sorted([TESTER_A, TESTER_B, READER]) and result.get("sent") == 3)
check("real copies carry no [Test] subject", all(not m["subject"].startswith("[Test]") for m in real))
camp = nl._campaign(nl.load_state_raw(), DRAFT)
check("campaign is sent with a sent_at", camp.get("status") == "sent" and bool(camp.get("sent_at")))
check("send stats start from zero", _stat_tuple(camp.get("stats")) == (3, 3, 0, 0, 0, 0))
check("the sent letter is in the public archive computation", DRAFT in _archive_ids())
client.get(pix_a)  # the token is per letter and address, so this is also the real copy's pixel
row_a = nl.get_send_snapshot(DRAFT)["recipients"][TESTER_A]
check("a real open counts on the sent row", row_a.get("status") == "sent" and row_a.get("open_count") == 1)
camp = nl._campaign(nl.load_state_raw(), DRAFT)
check("stats count the real open", _stat_tuple(camp.get("stats")) == (3, 3, 1, 1, 0, 0))

print("5. after the send a standalone copy's opener counts as before")
_s, pix_press, _c = _test_copy(DRAFT, PRESS)
client.get(pix_press)
row_press = nl.get_send_snapshot(DRAFT)["recipients"].get(PRESS) or {}
check("post-send copy opener lands as a sent row", row_press.get("status") == "sent")
camp = nl._campaign(nl.load_state_raw(), DRAFT)
check("and is counted", _stat_tuple(camp.get("stats")) == (4, 4, 2, 2, 0, 0))
check("campaign stays sent", camp.get("status") == "sent")

print("6. a real send that crashed back to draft still reads as sent")
crashed = {"id": CRASHED, "status": "draft", "stats": {"recipients": 2, "sent": 1}}
check("claimed and delivered draft reads as sent", nl._effective_status(crashed) == "sent")
healed, changed = nl.normalize_state({
    "settings": {}, "lists": [], "subscribers": [], "segments": [], "campaigns": [dict(crashed)],
})
check("heal persists it as sent with a sent_at",
      changed and healed["campaigns"][0]["status"] == "sent" and bool(healed["campaigns"][0].get("sent_at")))
undelivered = {"id": UNDELIVERED, "status": "draft", "stats": {"recipients": 2, "sent": 1, "failed": 1}}
check("a claim that delivered nothing never promotes", nl._effective_status(undelivered) == "draft")
kept, _changed = nl.normalize_state({
    "settings": {}, "lists": [], "subscribers": [], "segments": [], "campaigns": [dict(undelivered)],
})
check("heal leaves it a draft", kept["campaigns"][0]["status"] == "draft" and not kept["campaigns"][0].get("sent_at"))

print("7. a stray test row is never a send target")
before = len(_mail)
nl._run_send(STRAY)
check("send skips the test row", [m["to"] for m in _mail[before:]] == [READER])
stray_snap = nl.get_send_snapshot(STRAY)
check("the test row is left as it was", stray_snap["recipients"][GUEST].get("status") == nl.NON_COUNTING_STATUS)
stray = nl._campaign(nl.load_state_raw(), STRAY)
check("send finishes as sent", stray.get("status") == "sent")
check("stats leave the test row out", _stat_tuple(stray.get("stats")) == (1, 1, 0, 0, 0, 0))
before = len(_mail)
nl._kick_due_sends()
done = nl._campaign(nl.load_state_raw(), STRAY_DONE)
check("scheduler finishes a send whose only unsent row is a test row", done.get("status") == "sent")
check("without starting a send thread or mailing the test row",
      STRAY_DONE not in nl._send_threads and len(_mail) == before)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
