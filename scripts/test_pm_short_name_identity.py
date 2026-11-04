#!/usr/bin/env python3
"""Short-name subjects are disambiguated before a build (2026-11-04).

The EJAE defect: "Build a new profile on EJAE" drafted a US R&B
songwriter with Beyonce credits while the client meant Kim Eun-jae, the
singing voice of Rumi in KPop Demon Hunters. The guard researches a
short name once, anchors the draft to the figure with the largest
current digital footprint, names that person on the card in one plain
line, rewrites prose drafted from the wrong identity, and offers the
other figures behind a chip.

Hermetic: drives prometheus/short_name_identity with research dicts
(no network, no Flask) plus source-wiring asserts on app.py, the
legacy chat and templates/index.html.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prometheus import short_name_identity as sni  # noqa: E402

FAIL = 0


def check(name, ok):
    global FAIL
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        FAIL += 1


EJAE_RESEARCH = {
    "candidates": [
        {
            "name": "EJAE",
            "who": "The singer behind Golden from KPop Demon Hunters",
            "kind": "singer-songwriter",
            "qualifier": "K-pop songwriter",
            "footprint_note": "22M Spotify monthly listeners, 1.9M Instagram followers",
            "footprint_score": 22000000,
            "facts": [
                "Kim Eun-jae, Korean-American singer-songwriter and producer",
                "Singing voice of Rumi in Netflix's KPop Demon Hunters (2025)",
                "Co-wrote Golden, which topped the Billboard Hot 100",
                "Former SM Entertainment trainee; wrote Red Velvet's Psycho",
            ],
            "handles": {"instagram": "ejae_k", "tiktok": "@ejae_k",
                        "x": None, "youtube": "https://www.youtube.com/@EJAEofficial"},
            "persona_notes": (
                "EJAE's audience found her through KPop Demon Hunters on "
                "Netflix, where she is the singing voice of Rumi and the "
                "co-writer of Golden, and followed her into her own singles "
                "In Another World and Time After Time. It skews female and "
                "young, with K-pop fans, animation fans and parents of kids "
                "who watched the film on repeat. They live on TikTok, "
                "Instagram, YouTube and Spotify, follow dance covers and "
                "vocal covers of Golden, and track her songwriting credits "
                "for Red Velvet, aespa and NMIXX. Recent awards season "
                "coverage widened the audience into general pop listeners "
                "who know the song before the name."),
        },
        {
            "name": "Ejae",
            "who": "a US R&B producer with independent releases",
            "kind": "producer",
            "qualifier": "R&B producer",
            "footprint_note": "about 40K Spotify monthly listeners",
            "footprint_score": 40000,
            "facts": ["Independent R&B producer based in the US"],
            "handles": {},
            "persona_notes": "",
        },
    ],
    "drafted_matches": None,
    "confidence": "high",
}


def wrong_draft():
    return {
        "subject": "EJAE", "file_stem": "EJAE", "medium": "person",
        "brand_category": "MUSICIAN/BAND", "decision": "new_build",
        "identity_confident": True,
        "identity_note": ("EJAE is a US-based R&B/pop singer-songwriter "
                          "known for her songwriting credits for artists "
                          "including Beyonce and her own solo releases"),
        "persona_notes": ("EJAE's audience is built on her Beyonce "
                          "songwriting credits and her R&B solo releases."),
        "extra_rows": [["TALENT", "Beyonce"], ["MUSICIAN/BAND", "Beyonce"]],
        "subject_rows": [["MUSICIAN/BAND", "EJAE"]],
        "assumptions": ["old"], "category_note": "old",
    }


# ── 1. what counts as a short name ────────────────────────────────────
for s, want in (("EJAE", True), ("Ado", True), ("Zoomaa", True),
                ("Cloakzy", True), ("TimTheTatman", True),
                ("iJustine", True), ("Ye", True), ("EJAE - Avid Fan", True),
                ("Blondish", False), ("Taylor Swift", False),
                ("Beyonce Knowles", False), ("Chipotle Mexican Grill", False),
                ("Starbucks", False), ("Microsoft", False), ("", False),
                ("X", False)):
    check(f"is_short_name({s!r}) == {want}", sni.is_short_name(s) is want)
for s, want in (("Blondish", True), ("Shakira", True), ("EJAE", True),
                ("Taylor Swift", False), ("", False)):
    check(f"is_short_name({s!r}, person=True) == {want}",
          sni.is_short_name(s, person=True) is want)

# ── 2. identity context in the request ────────────────────────────────
check("bare build order has no context",
      sni.has_identity_context("Build a new profile on EJAE", "EJAE") is False)
check("name alone has no context",
      sni.has_identity_context("EJAE", "EJAE") is False)
check("one stray word is not context",
      sni.has_identity_context("profile on ejae pls", "EJAE") is False)
check("a described person has context",
      sni.has_identity_context(
          "Build a profile on EJAE the KPop Demon Hunters singer", "EJAE") is True)

# ── 3. the EJAE case: anchor, line, prose, alternatives ───────────────
d = wrong_draft()
info = sni.apply(d, EJAE_RESEARCH)
check("guard applied", info["applied"] is True)
check("card line names the pick in one plain line",
      d["resolved_identity"] == "EJAE, the singer behind Golden from KPop Demon Hunters")
check("identity_note is the same line", d["identity_note"] == d["resolved_identity"])
check("no em or en dash in the line",
      "\u2014" not in d["resolved_identity"] and "\u2013" not in d["resolved_identity"])
check("subject keeps its official styling", d["subject"] == "EJAE")
check("wrong prose replaced", "Beyonce" not in d["persona_notes"]
      and "Rumi" in d["persona_notes"])
check("decision reason from the pick",
      "Golden" in d["decision_reason"] and "Beyonce" not in d["decision_reason"])
check("assumptions rewritten", any("Pick another" in a for a in d["assumptions"]))
check("category_note dropped", "category_note" not in d)
check("affinity rows drafted for the wrong person dropped", d["extra_rows"] == [])
check("self-pin rows kept", d["subject_rows"] == [["MUSICIAN/BAND", "EJAE"]])
check("category stays MUSICIAN/BAND", d["brand_category"] == "MUSICIAN/BAND")
check("confident after the pick", d["identity_confident"] is True)
check("medium is person", d["medium"] == "person")
check("one alternative counted", info["alternatives"] == 1)
alts = d.get("identity_alternatives") or []
check("alternatives carry pick first then the other figure",
      len(alts) == 2 and alts[0]["picked"] is True and alts[1]["picked"] is False)
check("dominant figure keeps the bare name", alts[0]["qualifier"] == "")
check("other figure carries a parenthesised qualifier",
      alts[1]["qualifier"] == "(R&B producer)")
check("alternatives are clarify-step version dicts",
      all(k in alts[1] for k in ("title", "version_label", "medium", "qualifier"))
      and alts[1]["medium"] == "person" and alts[1]["title"] == "EJAE")
check("handles kept clean on the pick",
      d["identity_pick"]["handles"] == {"instagram": "ejae_k", "tiktok": "ejae_k",
                                        "youtube": "EJAEofficial"})
chips = sni.chip_options(d)
check("chip offered", chips == [{"id": "identity_other",
                                 "label": "Not this EJAE? Pick another"}])

# ── 4. ordering: the larger current footprint wins ────────────────────
swapped = {"candidates": list(reversed(EJAE_RESEARCH["candidates"])),
           "drafted_matches": None}
d2 = wrong_draft()
sni.apply(d2, swapped)
check("largest footprint wins regardless of list order",
      "Golden" in d2["resolved_identity"])

# ── 5. drafted identity already right: prose kept, line added ─────────
right = {"candidates": [EJAE_RESEARCH["candidates"][0]], "drafted_matches": 0}
d3 = wrong_draft()
d3["persona_notes"] = ("EJAE, the singing voice of Rumi, has a young "
                       "female-leaning audience from KPop Demon Hunters.")
d3["extra_rows"] = [["STREAMING/PLATFORM", "Netflix"]]
info3 = sni.apply(d3, right)
check("matching draft keeps its rows", d3["extra_rows"] == [["STREAMING/PLATFORM", "Netflix"]])
check("matching draft keeps its notes", "young female-leaning" in d3["persona_notes"])
check("matching draft still gets the line", "Golden" in d3["resolved_identity"])
check("single figure means no chip", sni.chip_options(d3) is None
      and info3["alternatives"] == 0)

# ── 6. a different kind flips the talent bucket, never a brand ───────
ath = {"candidates": [{"name": "Ado", "who": "the Japanese pop singer behind Usseewa",
                       "kind": "singer", "footprint_score": 9000000,
                       "facts": [], "handles": {}, "persona_notes": ""}]}
d4 = {"subject": "Ado", "brand_category": "ACTOR", "persona_notes": "x",
      "medium": "person", "extra_rows": [["TALENT", "y"]]}
sni.apply(d4, ath)
check("kind corrects the talent bucket", d4["brand_category"] == "MUSICIAN/BAND")
check("bucket flip rewrites prose and rows", d4["extra_rows"] == []
      and "Usseewa" in d4["persona_notes"])
d5 = {"subject": "Ado", "brand_category": "QSR", "persona_notes": "x",
      "medium": "person"}
sni.apply(d5, ath)
check("a brand bucket is never flipped", d5["brand_category"] == "QSR")

# ── 7. nothing researched means nothing touched ──────────────────────
d6 = wrong_draft()
info6 = sni.apply(d6, {"candidates": []})
check("empty research is a no-op", info6["applied"] is False
      and d6["identity_note"].startswith("EJAE is a US-based"))
info7 = sni.apply(d6, None)
check("None research is a no-op", info7["applied"] is False)

# ── 8. picking the other figure in the clarify step ──────────────────
d8 = wrong_draft()
sni.apply(d8, EJAE_RESEARCH)
pick = d8["identity_alternatives"][1]
# the generic version apply puts the qualifier into the subject first
d8["subject"] = f"{pick['title']} {pick['qualifier']}"
check("pick re-anchors", sni.apply_pick(d8, pick) is True)
check("picked line names the other figure",
      d8["resolved_identity"] == "EJAE (R&B producer), a US R&B producer with independent releases")
check("picked prose is thin but right",
      "Independent R&B producer" in d8["persona_notes"] and "Rumi" not in d8["persona_notes"])
check("alternatives cleared after the pick", "identity_alternatives" not in d8)
check("confirm head reads from the pick",
      sni.confirm_head(pick, d8["subject"]).startswith("Confirmed - EJAE (R&B producer), a US R&B"))
check("plain version pick is left alone",
      sni.apply_pick({"subject": "The Office US"}, {"title": "The Office",
                                                     "version_label": ""}) is False)

# ── 9. research prompt carries the two rules ─────────────────────────
system, user = sni.research_prompt("EJAE", "Build a new profile on EJAE",
                                   "EJAE is a US-based R&B songwriter")
check("prompt prefers the larger current digital footprint",
      "larger current digital footprint" in system)
check("prompt never invents credits", "Never invent credits" in system)
check("prompt carries the name and the drafted description",
      "NAME: EJAE" in user and "R&B songwriter" in user)
check("prompt has no em dash", "\u2014" not in system + user)

# ── 10. source wiring ────────────────────────────────────────────────
app_src = (ROOT / "app.py").read_text(encoding="utf-8")
chat_src = (ROOT / "prometheus" / "legacy" / "chat.py").read_text(encoding="utf-8")
html_src = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
check("app.py: verify call defined",
      "def _verify_short_name_identity(draft, prompt):" in app_src)
check("app.py: guard defined",
      "def _guard_short_name_identity(draft, prompt, allow_ask=True):" in app_src)
m = re.search(r"def _resolve_subject_identity\(.*?\n(.*?)\n\n\n", app_src, re.S)
check("app.py: guard runs inside _resolve_subject_identity before the "
      "identity line on person subjects",
      bool(m) and "_guard_short_name_identity(draft, prompt, allow_ask=allow_ask)"
      in m.group(1)
      and m.group(1).index("_guard_short_name_identity(")
      < m.group(1).index("_set_resolved_identity_line(draft)\n        return draft"))
check("chat.py: identity chip rides the interpret response",
      "_sni_chip.chip_options(spec_draft)" in chat_src
      and "'identity_other': {" in chat_src)
check("chat.py: picker pick re-anchors the brief",
      "_sni_pick.apply_pick(draft, pick)" in chat_src
      and "head = _person_head or f\"Confirmed - {title}, the {medium}{where}.\"" in chat_src)
check("chat.py: interpret prompt carries the short-name rule",
      "SHORT NAMES AND STAGE NAMES (HARD RULE - 2026-11-04)" in chat_src
      and "LARGER CURRENT DIGITAL FOOTPRINT" in chat_src
      and "Never invent credits" in chat_src)
check("index.html: identity chip attaches on the summary bubble",
      "String(o.id || '') === 'identity_other'" in html_src)
check("index.html: chip click opens the picker",
      "o.chipAction === 'identity_other'" in html_src
      and "function _synthChatIdentityOtherChipClick(o)" in html_src)
check("index.html: picker arms the identity clarify step with the alternatives",
      "versions: alts" in html_src
      and "synthChatClarify = { steps: ['identity'], idx: 0, draft: d };" in html_src)
check("no em dash introduced in the guard module",
      "\u2014" not in (ROOT / "prometheus" / "short_name_identity.py").read_text(encoding="utf-8"))

print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAILED'}")
sys.exit(1 if FAIL else 0)
