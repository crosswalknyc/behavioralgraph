"""One list of outside names a reader never sees.

A researched or built number stays. The name, the "according to" / "per"
clause, a parenthetical, and a "Source:" line come off. A sentence that
is only the citation goes. A sentence that still names a measurement
firm or a research house after that rewrite goes. A news outlet is
taken off when it is used as a citation. The number is left as a plain
fact. Nothing here calls it measured, counted, or ours.

The view-counting lines in prometheus.methodology are not this module's
concern. Callers that must keep those exact lines hold them out before
they call in, and put them back after. This module never imports that
file, so the allowlist stays where it is.

No em dashes.
"""
from __future__ import annotations

import re

# Measurement firms, research houses, app trackers, chart and sales
# trackers. These are not audience brands. A sentence that still names
# one after the rewrite is dropped. Longer forms sit first.
_HARD = (
    r"Antenna Analytics",
    r"Parrot Analytics",
    r"Google Trends",
    r"Box Office Mojo",
    r"Insider Intelligence",
    r"Sensor Tower",
    r"Morning Consult",
    r"Edison Research",
    r"Ampere Analysis",
    r"Leichtman Research",
    r"GlobalWebIndex",
    r"Global Web Index",
    r"MRI[- ]Simmons",
    r"Samba\s?TV",
    r"VideoAmp",
    r"iSpot(?:\.tv)?",
    r"App Annie",
    r"data\.ai",
    r"the-numbers\.com",
    r"NPD Group",
    r"Pew(?:\s+Research(?:\s+Center)?)?",
    r"eMarketer",
    r"Similarweb",
    r"Chartmetric",
    r"BookScan",
    r"OverDrive",
    r"Apptopia",
    r"Comscore",
    r"YouGov",
    r"Statista",
    r"Numerator",
    r"Luminate",
    r"Circana",
    r"Nielsen",
    r"Kantar",
    r"Wikipedia",
    r"MarketCast",
    r"Fishdog",
    r"Magid",
    r"NRG",
    r"GWI",
    r"GfK",
    r"Ipsos",
)

# News outlets and chart names that are also ordinary words or brands.
# Case-sensitive, and only in a citation frame, so "a variety of" and a
# Forbes audience read stay.
_CASE = (
    "The Hollywood Reporter",
    "Hollywood Reporter",
    "The New York Times",
    "New York Times",
    "The Wall Street Journal",
    "Wall Street Journal",
    "Media Play News",
    "Home Media Magazine",
    "Knight Foundation",
    "Brennan Center",
    "Business Insider",
    "IndieWire",
    "TechCrunch",
    "WordStream",
    "Ayzenberg",
    "TVLine",
    "Adweek",
    "Digiday",
    "Variety",
    "Deadline",
    "Billboard",
    "Reuters",
    "Bloomberg",
    "Forbes",
    "Axios",
    "Vulture",
    "Tudum",
    "HubSpot",
    "The Wrap",
    "The Verge",
    "IMDbPro",
    "IMDb",
    "CNBC",
    "THR",
    "Edison",
)

# Citation phrases that are not a firm. Stripped in a citation frame.
# Not used as a "drop the whole sentence" name, so "earnings" in ordinary
# prose stays.
_PHRASE = (
    r"press reports?",
    r"trade press",
    r"the trades",
    r"SEC filings?",
    r"earnings (?:calls?|reports?)",
)

_SAFE_LEAD = (
    r"the (?:latest |our |own |current )?(?:profile|file|data|read|audience|room|dashboard)|"
    r"your dashboard|crosswalk|prometheus|us"
)

def _alt(parts) -> str:
    ordered = sorted(parts, key=len, reverse=True)
    return "(?:" + "|".join(ordered) + ")"


HARD_ALT = _alt(_HARD)
CASE_ALT = _alt(("(?-i:%s)" % re.escape(p) for p in _CASE))
PHRASE_ALT = _alt(_PHRASE)
# Known citation targets. Safe inside a case-insensitive pattern: the
# outlet names keep their capitals via (?-i:).
ATTRIB_ALT = "(?:%s|%s|%s)" % (HARD_ALT, CASE_ALT, PHRASE_ALT)

HARD_RX = re.compile(r"\b(?:%s)\b" % HARD_ALT, re.I)
_CASE_RX = re.compile(r"(?<![A-Za-z])(?:%s)(?![A-Za-z])" % "|".join(re.escape(p) for p in sorted(_CASE, key=len, reverse=True)))

_VERBS = (
    r"reports?|reported|says?|said|puts?|put|counts?|counted|measured|measures|"
    r"found|finds|shows?|showed|estimates?|estimated|pegs?|pegged|"
    r"lists?|listed|recorded|records|tracked|tracks|places|placed|"
    r"saw|sees|had|has|have|notes?|noted|claims?|claimed|ranks?|ranked"
)
_PREP = (
    r"according to|as reported by|as measured by|as published by|"
    r"as tracked by|as counted by|as quoted by|reported by|"
    r"sourced from|citing|per|via"
)

# 'According to <name>, <fact>' at the start of a sentence. The safe
# targets (the profile, the file, the data) stay.
_LEAD_GENERIC_RX = re.compile(
    r"^(?:according to|as reported by|reported by|sourced from|citing)\s+"
    r"(?!(?:%s)\b).+?,\s*" % _SAFE_LEAD,
    re.I,
)
# 'per Nielsen', ', per Variety', 'via Statista', including at the start.
_KNOWN_ATTRIB_RX = re.compile(
    r"(?:,\s*)?\b(?:%s)\s+(?:the\s+)?(?:%s)(?:'s|\u2019s)?\s*(?:[,;:]\s*|\s+)?"
    % (_PREP, ATTRIB_ALT),
    re.I,
)
_PAREN_RX = re.compile(
    r"\s*\((?:source:\s*|sources:\s*|per\s+|via\s+|according to\s+)?(?:the\s+)?(?:%s)[^)]{0,80}\)"
    % ATTRIB_ALT,
    re.I,
)
# "Source: <outlet>" goes; the house's own line ("Source: Crosswalk", the one the brand standards put on a deck
# page, or "our" / "first-party") is the reader's own attribution and stays (2026-11-20: the BPIQ valuation deck
# lost its source line on every page)
_SOURCE_LINE_RX = re.compile(r"\(?\s*sources?\s*:(?!\s*(?:crosswalk\b|our\b|first[- ]party\b|the house\b))\s*[^.;\n)]*", re.I)
_LEAD_VERB_RX = re.compile(
    r"^(?:the\s+)?(?:%s)(?:'s|\u2019s)?\s+(?:%s)\s+(?:it\s+at\s+)?" % (ATTRIB_ALT, _VERBS),
    re.I,
)
_BY_NAME_RX = re.compile(
    r",?\s*\bby\s+(?:the\s+)?(?:%s)(?:'s|\u2019s)?\b" % ATTRIB_ALT,
    re.I,
)
# A held view-definition token starts with a lowercase x. Split in front
# of it too, so a citation in the previous sentence is still rewritten.
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?=[\"'\u201c\u2018(\[A-Z0-9])|(?<=[.!?])\s+(?=xqviewdefq)"
)
_SHIELD_MARK = "xqviewdefq"


def names_source(text: str) -> bool:
    """True when text still names a measurement firm, a research house,
    an app tracker, or a chart tracker."""
    return bool(HARD_RX.search(str(text or "")))


def _tidy(sentence: str, original: str) -> str:
    s = sentence
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"^[\s,;:]+", "", s)
    s = re.sub(r"\s+$", "", s)
    if s and s[0].islower() and (original[:1].isupper() or original[:1].isdigit()):
        s = s[0].upper() + s[1:]
    return s.strip()


def _thin(sentence: str) -> bool:
    """A remainder that is only the citation: no number, and too short
    to be the fact on its own."""
    if re.search(r"\d", sentence):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z']*", sentence)
    return len(words) < 5


def _rewrite_sentence(sentence: str) -> str:
    s = sentence.strip()
    if not s or _SHIELD_MARK in s:
        return s
    changed = False
    for rx in (_SOURCE_LINE_RX, _PAREN_RX, _LEAD_GENERIC_RX, _KNOWN_ATTRIB_RX, _LEAD_VERB_RX, _BY_NAME_RX):
        nxt = rx.sub("", s)
        if nxt != s:
            changed = True
            s = nxt
    s = _tidy(s, sentence)
    if _SHIELD_MARK in sentence:
        return sentence.strip()
    if HARD_RX.search(s):
        return ""
    # a short sentence that is only an outlet name ("See Variety.") goes;
    # a real line about that outlet as a subject stays
    if _CASE_RX.search(s) and _thin(s):
        return ""
    if changed and (not s or _thin(s)):
        return ""
    return s


def strip_named_sources(text: str) -> str:
    """The reader-facing rewrite. Newlines stay. A sentence that still
    names a measurement firm or a research house is dropped. A fact that
    shared the sentence with the citation stays. Idempotent."""
    t = str(text or "")
    if not t:
        return t
    if _SHIELD_MARK in t and not HARD_RX.search(t) and not _CASE_RX.search(t):
        # only a held definition line; nothing to rewrite
        if not _LEAD_GENERIC_RX.search(t) and not _SOURCE_LINE_RX.search(t):
            return t
    out_lines = []
    changed = False
    for line in t.split("\n"):
        if not line.strip():
            out_lines.append(line)
            continue
        lead = line[:len(line) - len(line.lstrip())]
        body = line.strip()
        kept = []
        for sent in _SENTENCE_SPLIT.split(body):
            nxt = _rewrite_sentence(sent)
            if nxt != sent.strip():
                changed = True
            if nxt:
                kept.append(nxt)
        out_lines.append(lead + " ".join(kept))
    if not changed:
        return t
    return "\n".join(out_lines)


def _rewrite_visible(text, hold=None, release=None) -> str:
    """One text node. ``hold`` / ``release`` are the view-definition
    shield (the caller owns that list; this module does not import it)."""
    held = []
    s = text
    if hold is not None:
        try:
            got = hold(s)
            if isinstance(got, tuple) and len(got) == 2:
                s, held = got
        except Exception:
            held = []
    nxt = strip_named_sources(s)
    if held and release is not None:
        try:
            nxt = release(nxt, held)
        except Exception:
            pass
    return nxt


def scrub_office_file(path, hold=None, release=None) -> int:
    """Take source names off reader-visible text in a pptx, docx, or
    html file. A pdf is already drawn; its builder scrubs before it
    draws. ``hold`` and ``release`` keep an exact view-definition line
    when the caller has one. Returns how many text nodes changed.
    Never raises into the caller that wraps it."""
    try:
        p = str(path or "")
        low = p.lower()
        if low.endswith((".html", ".htm")):
            return _scrub_html(p, hold, release)
        if low.endswith((".pptx", ".docx")):
            return _scrub_zip(p, hold, release)
    except Exception:
        return 0
    return 0


_A_T = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
_W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
_A_P = "{http://schemas.openxmlformats.org/drawingml/2006/main}p"
_W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"


def _scrub_zip(path: str, hold=None, release=None) -> int:
    import os
    import zipfile

    try:
        from lxml import etree
    except Exception:
        return 0
    changed = 0
    replacements = {}
    tag_p, tag_t = (_A_P, _A_T) if path.lower().endswith(".pptx") else (_W_P, _W_T)
    parser = etree.XMLParser(remove_blank_text=False)
    with zipfile.ZipFile(path) as z:
        blobs = {n: z.read(n) for n in z.namelist()}
    for n, blob in blobs.items():
        if not n.endswith(".xml"):
            continue
        if path.lower().endswith(".pptx") and not (
            n.startswith("ppt/slides/") or n.startswith("ppt/notesSlides/")
        ):
            continue
        if path.lower().endswith(".docx") and not n.startswith("word/"):
            continue
        try:
            root = etree.fromstring(blob, parser)
        except Exception:
            continue
        touched = False
        for para in root.iter(tag_p):
            runs = [t for t in para.iter(tag_t) if t.text]
            if not runs:
                continue
            whole = "".join(t.text or "" for t in runs)
            nxt = _rewrite_visible(whole, hold, release)
            if nxt == whole:
                continue
            runs[0].text = nxt
            for extra in runs[1:]:
                extra.text = ""
            touched = True
            changed += 1
        if touched:
            replacements[n] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if not replacements:
        return 0
    tmp = path + ".srcname"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = replacements.get(item.filename, zin.read(item.filename))
            zout.writestr(item, data)
    os.replace(tmp, path)
    return changed


def _scrub_html(path: str, hold=None, release=None) -> int:
    from pathlib import Path

    raw = Path(path).read_text(encoding="utf-8", errors="replace")

    def _one(m):
        inner = m.group(1)
        nxt = _rewrite_visible(inner, hold, release)
        return ">" + nxt + "<"

    nxt = re.sub(r">([^<]+)<", _one, raw)
    if nxt == raw:
        return 0
    Path(path).write_text(nxt, encoding="utf-8")
    return 1
