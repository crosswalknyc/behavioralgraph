#!/usr/bin/env python3
"""Names that come from the read's own data are never flagged or rewritten.

A show title, a brand or a person's name is spelled the way it is spelled.
"The Mandela Catalogue" is not a British spelling of catalog, "The Doors"
is not a door metaphor, and "Me And You" is not a label a title-caser
raised (2026-11-11: the lint failed a deck on a real show title and the
title was deleted by hand to pass).

The builder hands the names in (``set_names``), the finisher writes them
into the file as a document property so a later pass over the same file
(the mail gate, a re-check) reads the same list (``from_file``), and a
check run as its own process takes them from ``CW_PROTECTED_NAMES`` (a
path to a JSON list). Every spelling, casing and word-ban check asks
``mask`` or ``hold`` before it looks at a line.

    cw_names.py FILE                 print the names the file carries
    cw_names.py FILE --set NAMES     write a JSON list of names into FILE
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import zipfile
from pathlib import Path

ENV = "CW_PROTECTED_NAMES"
PROP = "CrosswalkNames"
MASK = "\u25a1"                      # stands in for a protected character: not a letter, not a digit
_OPEN, _CLOSE = "\ue000", "\ue001"   # a held name: private-use characters no pattern matches
_MAX_NAMES = 4000
_CHUNK = 240                         # one property value stays short; a long list is split across several

_LOCAL = threading.local()
_ENV_CACHE: dict = {}

_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_NS_CP = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_NS_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"


# Joining and measuring words: a "name" made only of these ("OR MORE", "AND UNDER", "LESS THAN", a
# band label's tail in a profile row) is not a name, and holding it would stop the casing rule.
_NOT_A_NAME = {"or", "more", "and", "of", "the", "to", "less", "than", "under", "over", "in", "on", "at", "for", "with",
               "a", "an", "vs", "not", "no", "all", "other", "others", "total", "plus", "up", "per", "by", "from", "as",
               "is", "are", "it", "this", "that", "us", "none", "some", "any", "each", "every"}


def clean(names) -> list[str]:
    """One spelling per name, longest first (a longer name is matched
    before a name it contains). Bare figures, one-letter strings and
    strings made only of joining words are not names."""
    seen, out = set(), []
    for n in names or []:
        s = re.sub(r"\s+", " ", str(n or "")).strip().strip(".,;:")
        if len(s) < 2 or not re.search(r"[A-Za-z]", s) or len(s) > 120:
            continue
        toks = [t for t in re.sub(r"[^a-z0-9]+", " ", s.lower()).split() if not t.isdigit()]
        if not toks or all(t in _NOT_A_NAME for t in toks):
            continue
        k = s.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= _MAX_NAMES:
            break
    out.sort(key=lambda s: (-len(s), s))
    return out


def set_names(names) -> None:
    """The names in force for this thread (a builder sets them for the
    length of one build and clears them after)."""
    _LOCAL.names = clean(names)


def clear() -> None:
    _LOCAL.names = []


def _env_names() -> list[str]:
    p = os.environ.get(ENV) or ""
    if not p:
        return []
    try:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
        if key not in _ENV_CACHE:
            _ENV_CACHE.clear()
            _ENV_CACHE[key] = clean(json.loads(Path(p).read_text(encoding="utf-8")))
        return _ENV_CACHE[key]
    except Exception:
        return []


def active() -> list[str]:
    """Names in force now: the thread's own plus the ones the environment
    points at."""
    mine = list(getattr(_LOCAL, "names", None) or [])
    env = _env_names()
    return clean(mine + env) if (mine and env) else (mine or env)


# ------------------------------------------------------------------ the file
def _prop_values(xml: str) -> list[str]:
    out = []
    for m in re.finditer(r'<property\b[^>]*\bname="%s(\d*)"[^>]*>\s*<vt:lpwstr>(.*?)</vt:lpwstr>' % PROP, xml, re.S):
        out.append((int(m.group(1) or 0), m.group(2)))
    out.sort()
    return [v for _i, v in out]


def _unescape(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&"))


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def from_file(path) -> list[str]:
    """The names a deck or a document carries (written by the finisher).
    Empty when it carries none or cannot be read."""
    try:
        if Path(str(path)).suffix.lower() not in (".pptx", ".docx"):
            return []
        with zipfile.ZipFile(str(path)) as z:
            if "docProps/custom.xml" not in z.namelist():
                return []
            xml = z.read("docProps/custom.xml").decode("utf-8", "replace")
        joined = "".join(_unescape(v) for v in _prop_values(xml))
        return clean(joined.split("|")) if joined else []
    except Exception:
        return []


def for_file(path) -> list[str]:
    """Every name that applies to a check of this file."""
    mine, theirs = active(), from_file(path)
    return clean(mine + theirs) if (mine and theirs) else (mine or theirs)


def _chunks(names: list[str]) -> list[str]:
    out, cur = [], ""
    for n in names:
        piece = (n.replace("|", " ") + "|")
        if cur and len(cur) + len(piece) > _CHUNK:
            out.append(cur)
            cur = ""
        cur += piece
    if cur:
        out.append(cur)
    return out


def write_to_file(path, names) -> int:
    """Write the names into a .pptx or .docx as document properties, in
    place. Returns how many were written; 0 on any failure (the file is
    left as it was). Properties that are not ours are kept."""
    names = clean(names)
    path = str(path)
    try:
        if Path(path).suffix.lower() not in (".pptx", ".docx"):
            return 0
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            data = {i.filename: z.read(i.filename) for i in infos}
        ct = data.get("[Content_Types].xml", b"").decode("utf-8")
        rels = data.get("_rels/.rels", b"").decode("utf-8")
        if not ct or not rels:
            return 0
        old = data.get("docProps/custom.xml", b"").decode("utf-8", "replace")
        keep = []
        if old:
            for m in re.finditer(r"<property\b[^>]*>.*?</property>", old, re.S):
                if not re.search(r'\bname="%s\d*"' % PROP, m.group(0)):
                    keep.append(re.sub(r'\bpid="\d+"', 'pid="@"', m.group(0), count=1))
        if not names and not old:
            return 0
        props = list(keep)
        for i, chunk in enumerate(_chunks(names)):
            props.append(f'<property fmtid="{_FMTID}" pid="@" name="{PROP}{i if i else ""}">'
                         f"<vt:lpwstr>{_escape(chunk)}</vt:lpwstr></property>")
        body = "".join(p.replace('pid="@"', f'pid="{n}"', 1) for n, p in enumerate(props, start=2))
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               f'<Properties xmlns="{_NS_CP}" xmlns:vt="{_NS_VT}">{body}</Properties>')
        data["docProps/custom.xml"] = xml.encode("utf-8")
        if "/docProps/custom.xml" not in ct:
            ct = ct.replace("</Types>", f'<Override PartName="/docProps/custom.xml" ContentType="{_CT}"/></Types>')
            data["[Content_Types].xml"] = ct.encode("utf-8")
        if "docProps/custom.xml" not in rels:
            rels = rels.replace("</Relationships>",
                                f'<Relationship Id="rIdCwNames" Type="{_REL}" Target="docProps/custom.xml"/></Relationships>')
            data["_rels/.rels"] = rels.encode("utf-8")
        tmp = path + ".names.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            order = [i.filename for i in infos]
            if "docProps/custom.xml" not in order:
                order.append("docProps/custom.xml")
            for name in order:
                # the content types part leads the package and is stored the way the source stored it
                src = next((i for i in infos if i.filename == name), None)
                comp = src.compress_type if src is not None else zipfile.ZIP_DEFLATED
                out.writestr(zipfile.ZipInfo(name, date_time=(src.date_time if src is not None else (2026, 1, 1, 0, 0, 0))),
                             data[name], compress_type=comp)
        os.replace(tmp, path)
        return len(names)
    except Exception:
        try:
            os.remove(path + ".names.tmp")
        except OSError:
            pass
        return 0


# --------------------------------------------------------------- the matching
def _pattern(name: str) -> re.Pattern:
    """A name as it may appear: whole words, any run of spaces between its
    words (a line may be bound with a no-break space), straight or curly
    apostrophes. A name of two words or more matches in any case (an
    eyebrow sets it in capitals); a one-word name matches as it is written
    or in capitals, so 'Colour' the brand does not excuse 'colour'."""
    parts = [re.escape(p).replace("'", "['\u2019]").replace("\u2019", "['\u2019]") for p in re.split(r"\s+", name) if p]
    body = r"[\s\u00a0]+".join(parts)
    pat = r"(?<![\w])" + body + r"(?![\w])"
    if len(parts) >= 2:
        return re.compile(pat, re.I)
    return re.compile(pat + "|" + r"(?<![\w])" + re.escape(name.upper()) + r"(?![\w])")


_PAT_CACHE: dict = {}


def spans(text: str, names=None, cased_only: bool = False) -> list[tuple[int, int]]:
    """Where protected names sit in ``text``, merged, in order.
    ``cased_only`` is for a pass about letter case: a name its source set
    all in capitals (a profile row, "ARTS AND CULTURE") says nothing about
    how it is cased, so it does not hold a line against that pass."""
    if not text:
        return []
    names = active() if names is None else names
    if cased_only:
        names = [n for n in names if re.search(r"[a-z]", n)]
    if not names:
        return []
    low = text.casefold()
    found = []
    for n in names:
        first = n.split()[0].casefold() if n.split() else ""
        if not first or first not in low:
            continue
        pat = _PAT_CACHE.get(n)
        if pat is None:
            if len(_PAT_CACHE) > 20000:
                _PAT_CACHE.clear()
            pat = _PAT_CACHE[n] = _pattern(n)
        found.extend((m.start(), m.end()) for m in pat.finditer(text))
    if not found:
        return []
    found.sort()
    merged = [found[0]]
    for s, e in found[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def mask(text: str, names=None) -> str:
    """``text`` with every protected name blanked, same length (so an
    offset into the masked text is an offset into the original). For a
    check that only reads."""
    sp = spans(text, names)
    if not sp:
        return text
    out, last = [], 0
    for s, e in sp:
        out.append(text[last:s])
        out.append("".join(c if c.isspace() else MASK for c in text[s:e]))
        last = e
    out.append(text[last:])
    return "".join(out)


def hold(text: str, names=None, cased_only: bool = False) -> tuple[str, list[str]]:
    """``text`` with every protected name swapped for a token no pattern
    matches, and the names held. For a pass that rewrites: run it on the
    held text, then ``restore``."""
    sp = spans(text, names, cased_only)
    if not sp:
        return text, []
    held, out, last = [], [], 0
    for s, e in sp:
        out.append(text[last:s])
        out.append(_OPEN + chr(0xE100 + len(held)) + _CLOSE)
        held.append(text[s:e])
        last = e
    out.append(text[last:])
    return "".join(out), held


def restore(text: str, held: list[str]) -> str:
    if not held or not text:
        return text
    for i, name in enumerate(held):
        text = text.replace(_OPEN + chr(0xE100 + i) + _CLOSE, name)
    return text


def kept(old: str, new: str, names=None) -> bool:
    """Does a rewrite keep every protected name the line carried, spelled
    as it was?"""
    for s, e in spans(old, names):
        if old[s:e] not in new:
            return False
    return True


_CAP = r"[A-Z][A-Za-z0-9'\u2019]*[a-z][A-Za-z0-9'\u2019]*"
_NAME_LIKE = re.compile(r"(?<![\w])(?:%s)(?:[ \u00a0]+(?:%s))+(?![\w])" % (_CAP, _CAP))


def in_name_like_run(text: str, start: int, end: int) -> bool:
    """Is the word at [start, end) a capitalized word standing next to
    another capitalized word ("the Mandela Catalogue", "Grey's Anatomy")?
    That is a proper name by its shape, whoever built the file."""
    for m in _NAME_LIKE.finditer(text or ""):
        if m.start() <= start and end <= m.end():
            return True
        if m.start() > end:
            break
    return False


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__.strip().splitlines()[-2].strip())
        return 2
    path = args[0]
    if len(args) >= 3 and args[1] == "--set":
        names = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        n = write_to_file(path, names)
        print(f"wrote {n} name(s)")
        return 0 if n or not names else 1
    for n in from_file(path):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
