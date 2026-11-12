#!/usr/bin/env python3
"""Decks never use the word doors (Jenna 2026-10-28).

The defect is the metaphor: the door to new viewers, opens the door,
a door for, walk through the door, close the door, doorway to. Compound
brand tokens (DoorDash, Glassdoor, NextDoor) do not match. A proper
name that already contains Door is never touched: the venue (The Door,
The Door NYC) and any title, brand or person's name written with a
capital D inside a run of capitalized words (AD Open Door, The Doors,
Three Doors Down, Door County), when it is not followed by to/for.

``scrub_door_metaphor`` rewrites common frames and strips any leftover
door token. Builders, the brand lint, and ``design_skill.finish`` all
call it. A finish never holds the file: the rewrite lands in place.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import cw_names as _names
except Exception:  # pragma: no cover
    _names = None

DOOR_TOKEN = re.compile(r"\bdoors?\b", re.I)
# Title-case venue name only. "the door to" stays a metaphor.
ALLOWED_PROPER = re.compile(r"\bThe Door(?:\s+NYC)?\b")
# A title, a brand or a person's name is never touched (Jenna 2026-11-11:
# "AD Open Door", Architectural Digest's series, came back "AD brings";
# the same for The Doors, Three Doors Down, Door County). A deck writes
# the common noun in lower case (headers are sentence case), so a
# capital-D Door / Doors with a capitalized word beside it is a name.
# Not a name: ALL CAPS chrome, a label made only of general words
# ("Retail Door Mix", "This Door"), and the metaphor tile, where to /
# for / into follows the word ("The Door to new viewers").
_NAME_WORD = r"[A-Z][A-Za-z0-9'\u2019&.+-]*"
_NAME_LINK = r"(?:of|the|and|in|on|at|de|la|&)"
PROPER_NAME = re.compile(
    r"(?<![A-Za-z0-9])(?P<pre>(?:" + _NAME_WORD + r"(?:\s+" + _NAME_LINK + r")?\s+){0,5})"
    r"(?P<door>Doors?)(?![A-Za-z0-9])"
    r"(?P<post>(?:\s+(?:" + _NAME_LINK + r"\s+)?" + _NAME_WORD + r"){0,5})")
_GENERAL_WORDS = frozenset((
    "a", "an", "this", "that", "these", "those", "one", "another", "each", "every", "per", "by", "all", "both",
    "its", "their", "our", "your", "my", "his", "her", "same", "new", "other", "top", "retail", "retailer",
    "home", "store", "shop", "shopping", "physical", "front", "back", "side", "main", "first", "second",
    "mix", "count", "counts", "share", "shares", "list", "level", "and", "or", "but", "so", "then", "with",
    "for", "to", "into", "from", "is", "are", "was", "were", "it", "they", "we", "you", "i", "when", "where",
    "what", "which", "who", "why", "how", "if", "as", "at", "in", "on", "of", "no", "not", "more", "most"))


def proper_name_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every proper name that carries Door / Doors."""
    out = []
    for m in PROPER_NAME.finditer(text or ""):
        if re.match(r"\s+(?:to|for|into)\b", (text or "")[m.end("door"):], re.I):
            continue                      # the metaphor tile, however it is cased
        beside = [w for w in re.findall(r"[A-Za-z0-9'\u2019&.+-]+", m.group("pre") + " " + m.group("post"))
                  if not re.fullmatch(_NAME_LINK, w)]
        if not beside:
            continue                      # the word on its own is not a name
        if all(w.lower().strip(".'\u2019") in _GENERAL_WORDS for w in beside):
            continue                      # "Retail Door Mix", "This Door": a label, not a name
        out.append((m.start(), m.end()))
    return out

_FRAMES = [
    (re.compile(r"\bopens?\s+the\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopen(?:ed|ing)\s+the\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopens?\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopen(?:ed|ing)\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopens?\s+the\s+doors?\b", re.I), "brings"),
    (re.compile(r"\bopen(?:ed|ing)\s+the\s+doors?\b", re.I), "brings"),
    (re.compile(r"\bopens?\s+doors?\b", re.I), "brings"),
    (re.compile(r"\bopen(?:ed|ing)\s+doors?\b", re.I), "brings"),
    (re.compile(r"\ba\s+doors?\s+for\s+", re.I), ""),
    (re.compile(r"\bdoors?\s+for\s+", re.I), ""),
    (re.compile(r"\bwalk(?:s|ed|ing)?\s+through\s+(?:the\s+)?doors?\b", re.I), "arrives"),
    (re.compile(r"\bthrough\s+the\s+doors?\b", re.I), ""),
    (re.compile(r"\bclos(?:e|es|ed|ing)\s+the\s+doors?\b", re.I), ""),
    (re.compile(r"\b(?:the\s+)?doorways?\s+to\s+", re.I), ""),
    (re.compile(r"\b(?:the\s+)?doorways?\b", re.I), ""),
    (re.compile(r"\b(?:the\s+)?doors?\s+to\s+", re.I), ""),
]


def _is_allowed_span(text: str, start: int, end: int) -> bool:
    for m in ALLOWED_PROPER.finditer(text or ""):
        if m.start() <= start and end <= m.end():
            if re.match(r"\s+(to|for)\b", (text or "")[m.end():], re.I):
                return False
            return True
    return any(a <= start and end <= b for a, b in proper_name_spans(text))


def _blank_names(text: str, names=None) -> str:
    """The line with every protected name blanked (same length). A title,
    a brand or a person's name from the read's own data is a name, not a
    metaphor: "The Doors", "Stage Door", "Door County"."""
    if _names is None:
        return text
    try:
        return _names.mask(text, names)
    except Exception:
        return text


def door_hits(text: str, names=None) -> list[str]:
    """Short snippets for each disallowed door / doors token."""
    text = text or ""
    hits = []
    seen = _blank_names(text, names)
    for m in DOOR_TOKEN.finditer(seen):
        if _is_allowed_span(text, m.start(), m.end()):
            continue
        a = max(0, m.start() - 16)
        b = min(len(text), m.end() + 20)
        hits.append(re.sub(r"\s+", " ", text[a:b]).strip())
    return hits


def has_door_metaphor(text: str, names=None) -> bool:
    return bool(door_hits(text, names))


def _protect(text: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def hold(m: re.Match) -> str:
        if re.match(r"\s+(to|for)\b", text[m.end():], re.I):
            return m.group(0)
        held.append(m.group(0))
        return f"\x00DOOR{len(held) - 1}\x00"

    out = ALLOWED_PROPER.sub(hold, text or "")
    # a title, a brand or a person's name rides through whole, last span first so the offsets hold
    for a, b in reversed(proper_name_spans(out)):
        held.append(out[a:b])
        out = out[:a] + f"\x00DOOR{len(held) - 1}\x00" + out[b:]
    return out, held


def _restore(text: str, held: list[str]) -> str:
    for i, name in enumerate(held):
        text = text.replace(f"\x00DOOR{i}\x00", name)
    return text


_LEAD_RE = re.compile(r"^((?:\d+[.)]?\s+)*)(\S)")


def _tidy(text: str, orig: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"\n +", "\n", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s{2,}", " ", text)
    lead = re.match(r"^\s*", text).group(0)
    body = text[len(lead):]
    if body:
        # The opening letter is raised only when the rewrite took the head
        # off a line that opened on a capital ("01 The door to new viewers"
        # -> "01 New viewers"). A line that opened lower case stays lower
        # case: "25 to 34" is a range and "google.com/search" is an address
        # (2026-11-11: every such line came back "25 To 34", "Google.com").
        mo = _LEAD_RE.match((orig or "").lstrip())
        mn = _LEAD_RE.match(body)
        if mo and mn and mo.group(2).isupper() and mn.group(2).islower():
            i = mn.start(2)
            body = body[:i] + body[i].upper() + body[i + 1:]
    return (lead + body).strip() if (orig or "").strip() == (orig or "") else lead + body


def scrub_door_metaphor(text: str, names=None) -> str:
    """Rewrite door metaphors. The returned string has no door / doors token
    unless it is an allowed proper name or a name from the read's own data
    (``names``, or the names in force)."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not DOOR_TOKEN.search(text):
        return text          # nothing to rewrite: the string comes back byte for byte
    if not DOOR_TOKEN.search(_blank_names(text, names)):
        return text          # every door on the line is inside a name: byte for byte
    orig = text
    kept_names = []
    if _names is not None:
        try:
            text, kept_names = _names.hold(text, names)
        except Exception:
            text, kept_names = orig, []
    out = _scrub_held(text)
    if kept_names:
        out = _names.restore(out, kept_names)
    return out


def _scrub_held(text: str) -> str:
    protected, held = _protect(text)
    out = protected
    for pat, repl in _FRAMES:
        out = pat.sub(repl, out)
    def drop(m: re.Match) -> str:
        if _is_allowed_span(out, m.start(), m.end()):
            return m.group(0)
        return ""
    out = DOOR_TOKEN.sub(drop, out)
    out = _restore(out, held)
    return _tidy(out, text)


def _scrub_paragraph_runs(paragraph, names=None) -> int:
    runs = list(getattr(paragraph, "runs", []) or [])
    raw = "".join((r.text or "") for r in runs)
    if not raw and getattr(paragraph, "text", None):
        raw = paragraph.text or ""
    new = scrub_door_metaphor(raw, names)
    if new == raw:
        return 0
    if runs:
        runs[0].text = new
        for r in runs[1:]:
            r.text = ""
    else:
        try:
            paragraph.text = new
        except Exception:
            return 0
    return 1


def _scrub_text_frame(tf, names=None) -> int:
    n = 0
    try:
        for p in tf.paragraphs:
            n += _scrub_paragraph_runs(p, names)
    except Exception:
        pass
    return n


def _iter_shapes(shapes):
    for sh in shapes:
        yield sh
        try:
            if getattr(sh, "has_table", False):
                for row in sh.table.rows:
                    for cell in row.cells:
                        if cell.text_frame is not None:
                            yield cell
        except Exception:
            pass
        try:
            if sh.shape_type is not None and int(sh.shape_type) == 6:
                yield from _iter_shapes(sh.shapes)
        except Exception:
            pass


def _file_names(path):
    """The names that apply to this file: the ones it carries and the ones
    in force. None (the names in force) when the helper is absent."""
    if _names is None:
        return None
    try:
        return _names.for_file(path)
    except Exception:
        return None


def scrub_pptx(path) -> int:
    from pptx import Presentation  # type: ignore

    prs = Presentation(str(path))
    names = _file_names(path)
    n = 0
    for slide in prs.slides:
        for sh in _iter_shapes(slide.shapes):
            if getattr(sh, "has_text_frame", False) and sh.text_frame is not None:
                n += _scrub_text_frame(sh.text_frame, names)
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide
                if notes.notes_text_frame is not None:
                    n += _scrub_text_frame(notes.notes_text_frame, names)
                for sh in _iter_shapes(notes.shapes):
                    if getattr(sh, "has_text_frame", False) and sh.text_frame is not None:
                        n += _scrub_text_frame(sh.text_frame, names)
        except Exception:
            pass
    if n:
        prs.save(str(path))
    return n


def scrub_docx(path) -> int:
    from docx import Document  # type: ignore

    doc = Document(str(path))
    names = _file_names(path)
    n = 0
    for p in doc.paragraphs:
        n += _scrub_paragraph_runs(p, names)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    n += _scrub_paragraph_runs(p, names)
    if n:
        doc.save(str(path))
    return n


def scrub_text_file(path) -> int:
    # Line by line, and only inside the text of a line that carries the word:
    # every other line, and every tag, comes back byte for byte (the whole
    # file used to pass through the tidy, which closed up spacing in markup
    # and styles it had no business touching).
    p = Path(path)
    raw = p.read_bytes().decode("utf-8", errors="replace")
    if not DOOR_TOKEN.search(raw):
        return 0
    markup = Path(str(path)).suffix.lower() in (".html", ".htm")
    out, n, skip = [], 0, None
    for line in raw.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        end = line[len(body):]
        if markup:
            low = body.lower()
            if skip is None:
                m = re.search(r"<(script|style)\b", low)
                if m and not re.search(r"</%s\s*>" % m.group(1), low[m.end():]):
                    skip = m.group(1)
            elif re.search(r"</%s\s*>" % skip, low):
                skip = None
                out.append(line)
                continue
            if skip is not None:
                out.append(line)
                continue
        if not DOOR_TOKEN.search(body):
            out.append(line)
            continue
        if markup:
            pieces = re.split(r"(<[^>]*>)", body)
            new = "".join(pc if pc.startswith("<") else _scrub_piece(pc) for pc in pieces)
        else:
            new = _scrub_piece(body)
        if new != body:
            n += 1
        out.append(new + end)
    if not n:
        return 0
    p.write_bytes("".join(out).encode("utf-8"))
    return n


def _scrub_piece(piece: str) -> str:
    """One run of text inside a line: rewritten if it carries the word, with
    the spacing at its two ends kept (it is what joins it to its neighbors)."""
    if not DOOR_TOKEN.search(piece):
        return piece
    lead = re.match(r"^\s*", piece).group(0)
    trail = re.search(r"\s*$", piece).group(0) if piece.strip() else ""
    new = scrub_door_metaphor(piece.strip())
    return lead + new + trail if piece.strip() else piece


def scrub_file(path) -> int:
    """Rewrite door metaphors in a deck or document in place. Returns
    the number of rewritten units. Never raises; 0 on any failure."""
    try:
        ext = Path(str(path)).suffix.lower()
        if ext == ".pptx":
            return scrub_pptx(path)
        if ext == ".docx":
            return scrub_docx(path)
        if ext in (".html", ".htm", ".md", ".txt"):
            return scrub_text_file(path)
    except Exception:
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: cw_door_copy.py --text STRING | FILE")
        return 2
    if args[0] == "--text":
        src = " ".join(args[1:])
        print(scrub_door_metaphor(src))
        return 0 if not has_door_metaphor(scrub_door_metaphor(src)) else 1
    n = scrub_file(args[0])
    print(f"rewrote {n} unit(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
