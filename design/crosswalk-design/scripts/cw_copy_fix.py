#!/usr/bin/env python3
"""Repair the copy the plain-English check flags, in place (2026-11-11).

Two decks left the builder marked ready on the same day with a failed
readability check, and the flagged lines were fixed by hand in the
finished files. This is that repair as a step: ``fix_file`` reads the
deck the way ``readability_check.py`` reads it, rewrites only the text
that carries a flag, writes it back into the same shapes, and checks
again. A file is never held and nothing raises.

Order of work on one file:

  1. the check runs; no flag, nothing changes.
  2. when the caller supplies a ``rewriter`` (a model pass), the flagged
     units go to it, each with what is wrong and what must be kept. A
     rewrite is taken only when it keeps every figure, stays about the
     same length and breaks none of the house rules. Two passes at most.
  3. whatever is still flagged gets the fixed repair for its kind: a long
     sentence is split at its join, a phrase used in three places is
     reworded or dropped from the later ones, a stock filler is replaced
     from a table, a jargon word takes its plain substitute, a third card
     that opens like its neighbors is turned around, a header that runs
     long, asks a question or stacks fragments is made one plain sentence.
  4. the check runs once more; the count it reports is the file's state.

Usage:
    python3 cw_copy_fix.py deck.pptx           # repair in place, print what changed
    python3 cw_copy_fix.py deck.pptx --check   # report only

Every rule repair is checked before it is written (``sane``): a cut that
doubles a word, leaves loose punctuation or too few words is not made,
and the flag it would have cleared is reported as left. This step is for
decks whose copy the builder wrote. A deck whose copy is data (verbatim
answers, an export that must match its PDF) is checked and reported,
never rewritten: ``design_skill.finish`` repairs only when asked.

The pieces are importable: ``scrub_stock``, ``split_long_sentence``,
``small_word_case``, ``short_cell``, ``sane``, ``fix_file``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import readability_check as rc  # noqa: E402

try:
    from cw_door_copy import scrub_door_metaphor
except Exception:  # pragma: no cover
    def scrub_door_metaphor(text):
        return re.sub(r"\s{2,}", " ", re.sub(r"\bdoors?\b", "", text or "", flags=re.I)).strip()

NBSP = "\u00a0"
LLM_PASSES = 2
FALLBACK_ROUNDS = 5

# ------------------------------------------------------------ small tools
_STARTERS = {
    "the", "a", "an", "this", "that", "these", "those", "it", "they", "their", "its", "most", "many",
    "some", "each", "every", "both", "all", "of", "in", "on", "at", "for", "with", "from", "by", "as",
    "when", "while", "where", "if", "because", "after", "before", "her", "his", "she", "he", "we",
    "our", "there", "here", "one", "two", "three", "four", "five", "more", "fewer", "over", "under",
    "nearly", "about", "among", "across", "inside", "within", "during", "no", "not", "never", "none",
    "nothing", "only", "even", "still", "also", "now", "then", "how", "what", "why", "who", "new", "direct",
    "total", "average", "overall", "viewers", "people", "fans", "buyers", "shoppers", "users", "accounts",
}


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace(NBSP, " ")).strip()


def _cap(text: str) -> str:
    m = re.search(r"[A-Za-z]", text or "")
    if not m or re.search(r"\d", text[:m.start()]):
        return text
    i = m.start()
    return text[:i] + text[i].upper() + text[i + 1:]


def _lower_start(text: str) -> str:
    """Lower the first letter when the sentence opens on a common word (an
    article, a pronoun, a preposition); a name or a figure is left alone."""
    m = re.match(r"\s*([A-Za-z']+)", text or "")
    if not m:
        return text
    w = m.group(1)
    if w.lower() in _STARTERS and not w.isupper():
        i = m.start(1)
        return text[:i] + w[0].lower() + w[1:] + text[m.end(1):]
    return text


def _tidy(text: str) -> str:
    t = re.sub(r"[ \t]{2,}", " ", text or "")
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])\s*([,;:.])", r"\2", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"^[\s,;:]+", "", t)
    t = re.sub(r"[,;:]\s*$", "", t)
    t = re.sub(r"\.{2,}$", ".", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _figures(text: str) -> list:
    """Every figure in a string, as written, sorted (a rewrite must keep them)."""
    return sorted(re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?[KMBx]?", str(text or "").replace(NBSP, " ")))


def _words(text: str) -> list:
    return rc.words_of(text or "")


_ABBR = re.compile(r"(?:\b(?:U\.S|U\.K|e\.g|i\.e|vs|etc|approx|Inc|Co|Corp|Ltd|No|St|Mr|Mrs|Ms|Dr|Jr|Sr|Jan|Feb|Mar|Apr|Jun|Jul|"
                   r"Aug|Sep|Sept|Oct|Nov|Dec)|\b[A-Z])\.$")


def _sentences(text: str) -> list:
    """Sentences of a unit, without breaking on an abbreviation ("U.S.
    consumers", "vs. the benchmark") or an initial. The check's own
    splitter breaks there; a repair must not cut a line in half on it."""
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    out = []
    for p_ in parts:
        if out and _ABBR.search(out[-1]):
            out[-1] = out[-1] + " " + p_
        else:
            out.append(p_)
    return [s.strip() for s in out if s.strip()]


# ------------------------------------------------------- label casing (2)
_SMALL = ("to", "and", "or", "of", "the", "vs", "vs.")
_BEFORE_THE = {"of", "in", "on", "at", "to", "for", "and", "or", "by", "with", "from", "across", "over", "under",
               "into", "among", "inside", "within", "vs", "vs."}
_RANGE_RE = re.compile(r"(?<=[\d%+$])(\s+)(To|And|Or|Through|Thru)(\s+)(?=\$?\d)")


def small_word_case(text) -> str:
    """Lower the small joining words a title-caser raised: a range reads
    ``25 to 34`` and ``$75,000 to $99,999``; inside a title-cased label
    ``Arts And Culture`` reads ``Arts and Culture`` and ``Comedy Vs Horror``
    reads ``Comedy vs Horror``. The first word of a line or sentence is
    never touched, and running prose (words already in lower case) is
    left exactly as it was."""
    s = "" if text is None else str(text)
    if not s or not re.search(r"\b(To|And|Or|Of|The|Vs|Through|Thru)\b", s):
        return s
    s = _RANGE_RE.sub(lambda m: m.group(1) + m.group(2).lower() + m.group(3), s)

    def fix_segment(seg: str) -> str:
        toks = re.findall(r"\S+|\s+", seg)
        words = [t for t in toks if t.strip()]
        if len(words) < 3 or len(words) > 9:
            return seg
        alpha = [w for w in words if re.match(r"[A-Za-z]", w)]
        big = [w for w in alpha if w[:1].isupper()]
        # a label set in title case: every word capitalized, at least one small word among them
        if len(alpha) < 3 or len(big) != len(alpha):
            return seg
        if not any(w.lower().strip(",;") in _SMALL for w in alpha[1:]):
            return seg
        prev, out = "", []
        for t in toks:
            core = t.strip(",;")
            if t.strip() and prev and core.lower() in _SMALL and core[:1].isupper() and not core.isupper() \
                    and (core.lower() != "the" or prev in _BEFORE_THE):
                t = t.replace(core, core.lower(), 1)
            if t.strip():
                prev = core.lower()
            out.append(t)
        return "".join(out)

    # sentence by sentence, so the word that opens a sentence keeps its capital
    parts = re.split(r"((?<=[.!?:])\s+|\s+[/|·]\s+|\n)", s)
    return "".join(fix_segment(p) if i % 2 == 0 else p for i, p in enumerate(parts))


# ----------------------------------------------------- stock filler (8)
_STOCK_TABLE = [
    (r"\bdoes\s+(?:all\s+)?the\s+work\b(?!\s+(?:of|for|that|to)\b)", "is enough"),
    (r"\bdo\s+(?:all\s+)?the\s+work\b(?!\s+(?:of|for|that|to)\b)", "are enough"),
    (r"\bdid\s+(?:all\s+)?the\s+work\b(?!\s+(?:of|for|that|to)\b)", "was enough"),
    (r"\bdoing\s+(?:all\s+)?the\s+work\b(?!\s+(?:of|for|that|to)\b)", "carrying the result"),
    (r"\bdoes\s+any\s+work\b(?!\s+(?:of|for|that|to)\b)", "adds anything"),
    (r"\bdo\s+any\s+work\b(?!\s+(?:of|for|that|to)\b)", "add anything"),
    (r"\bdid\s+any\s+work\b(?!\s+(?:of|for|that|to)\b)", "added anything"),
    (r"\bdoing\s+any\s+work\b(?!\s+(?:of|for|that|to)\b)", "adding anything"),
    (r"\bdoes\s+(?:all\s+|most\s+of\s+|a\s+lot\s+of\s+)?the\s+heavy\s+lifting\b", "carries most of it"),
    (r"\bdo\s+(?:all\s+|most\s+of\s+|a\s+lot\s+of\s+)?the\s+heavy\s+lifting\b", "carry most of it"),
    (r"\bdid\s+(?:all\s+|most\s+of\s+|a\s+lot\s+of\s+)?the\s+heavy\s+lifting\b", "carried most of it"),
    (r"\bdoing\s+(?:all\s+|most\s+of\s+|a\s+lot\s+of\s+)?the\s+heavy\s+lifting\b", "carrying most of it"),
    (r"\bdoes\s+(?:most\s+of\s+the|a\s+lot\s+of)\s+work\b", "carries most of it"),
    (r"\bdo\s+(?:most\s+of\s+the|a\s+lot\s+of)\s+work\b", "carry most of it"),
    (r"\bdid\s+(?:most\s+of\s+the|a\s+lot\s+of)\s+work\b", "carried most of it"),
    (r"\bdoing\s+(?:most\s+of\s+the|a\s+lot\s+of)\s+work\b", "carrying most of it"),
    (r"\bdoes\s+(?:all\s+)?the\s+talking\b", "makes the case"),
    (r"\bdo\s+(?:all\s+)?the\s+talking\b", "make the case"),
    (r"\bdid\s+(?:all\s+)?the\s+talking\b", "made the case"),
    (r"\bdoing\s+(?:all\s+)?the\s+talking\b", "making the case"),
    # the filler with a word in the middle ("does the conversion work"): the thing carries what the word names
    (r"\bdoes\s+((?:most|all|much)\s+of\s+)?the\s+([a-z-]+)\s+work\b(?!\s+(?:of|for|that|to)\b)", r"carries \1the \2"),
    (r"\bdo\s+((?:most|all|much)\s+of\s+)?the\s+([a-z-]+)\s+work\b(?!\s+(?:of|for|that|to)\b)", r"carry \1the \2"),
    (r"\bdid\s+((?:most|all|much)\s+of\s+)?the\s+([a-z-]+)\s+work\b(?!\s+(?:of|for|that|to)\b)", r"carried \1the \2"),
    (r"\bdoing\s+((?:most|all|much)\s+of\s+)?the\s+([a-z-]+)\s+work\b(?!\s+(?:of|for|that|to)\b)", r"carrying \1the \2"),
]
_STOCK_TABLE = [(re.compile(p, re.I), r) for p, r in _STOCK_TABLE]


def scrub_stock(text) -> str:
    """Replace stock filler ("does the work", "does the heavy lifting")
    with the plain verb. A string without one comes back unchanged."""
    s = "" if text is None else str(text)
    if not s or not rc.stock_hits(s.replace(NBSP, " ")):
        return s
    out = s.replace(NBSP, " ")
    for pat, repl in _STOCK_TABLE:
        def _sub(m, r=repl):
            got = re.sub(r"\s{2,}", " ", m.expand(re.sub(r"\\(\d)", lambda g: f"\\g<{g.group(1)}>", r)))
            return (got[0].upper() + got[1:]) if m.group(0)[:1].isupper() else got
        out = pat.sub(_sub, out)
    return out


# ------------------------------------------------------ long sentences
_SPLITS = [
    # (pattern, how the second sentence opens)
    (re.compile(r";\s+"), ""),
    (re.compile(r",\s+and\s+"), ""),
    (re.compile(r",\s+but\s+"), "But "),
    (re.compile(r",\s+so\s+"), "So "),
    (re.compile(r",\s+yet\s+"), "Yet "),
    (re.compile(r",\s+while\s+"), ""),
    (re.compile(r",\s+which\s+"), "That "),
    (re.compile(r",\s+who\s+"), "They "),
    (re.compile(r",\s+where\s+"), "There, "),
    (re.compile(r":\s+"), ""),
    (re.compile(r",\s+"), ""),
    (re.compile(r"\s+and\s+"), ""),
    (re.compile(r"\s+because\s+"), "That is because "),
    (re.compile(r"\s+"), ""),
]


def split_long_sentence(sentence: str, ceiling: int = rc.SENTENCE_WORD_CEILING, _depth: int = 0) -> str:
    """One sentence over the ceiling becomes two (or three) at its best
    join: a semicolon, ", and", ", but", ", which", a colon, a comma, and
    only then a plain word gap. Every word and figure is kept."""
    s = str(sentence or "").strip()
    n = len(_words(s))
    if n <= ceiling or _depth > 3:
        return s
    best = None
    for pat, opener in _SPLITS:
        cands = []
        for m in pat.finditer(s):
            left, right = s[:m.start()], s[m.end():]
            lw, rw = len(_words(left)), len(_words(right))
            if lw < 5 or rw < 5:
                continue
            # never between the two ends of a range ("25 to 34", "$75,000 to $99,999")
            if re.search(r"\bto$", left.strip()) or re.match(r"to\b", right.strip()):
                continue
            cands.append((abs(lw - rw), m.start(), m.end(), opener))
        if cands:
            cands.sort()
            best = cands[0]
            break
    if best is None:
        return s
    _d, a, b, opener = best
    left = s[:a].rstrip(" ,;:")
    right = s[b:].strip()
    if not re.search(r"[.!?]$", left):
        left += "."
    right = (opener + _lower_start(right)) if opener else _cap(right)
    return (split_long_sentence(left, ceiling, _depth + 1) + " "
            + split_long_sentence(right, ceiling, _depth + 1)).strip()


# --------------------------------------------------- repeated phrases
_SWAPS = [
    (r"\bfour[- ]year degree\b", "bachelor's degree"),
    (r"\bbachelor'?s degree\b", "college degree"),
    (r"\bor better\b", "or higher"),
    (r"\bor higher\b", "or above"),
    (r"\bor more\b", "or above"),
    (r"\bmore than\b", "over"),
    (r"\bless than\b", "under"),
    (r"\bfour or more times\b", "at least four times"),
    (r"\bat least once\b", "one time or more"),
    (r"\bof the audience\b", "of this audience"),
    (r"\bof this audience\b", "of these viewers"),
    (r"\bof the five\b", "among the five"),
    (r"\bacross the five\b", "over the five"),
    (r"\bthe highest\b", "the top"),
    (r"\bhighest\b", "top"),
    (r"\bthe largest\b", "the biggest"),
    (r"\bthe biggest\b", "the largest"),
    (r"\bthe smallest\b", "the least"),
    (r"\bthe average American\b", "the US average"),
    (r"\bthe US average\b", "the national average"),
    (r"\bagainst\b", "versus"),
    (r"\bin the window\b", "over the window"),
    (r"\bon the path\b", "in the path"),
    (r"\bpeer median\b", "median of peers"),
    (r"\bcompared with\b", "next to"),
    (r"\bcompared to\b", "next to"),
    (r"\bas well as\b", "and"),
    (r"\bin order to\b", "to"),
]
_SWAPS = [(re.compile(p, re.I), r) for p, r in _SWAPS]
_CLAUSE_SPLIT = re.compile(r"(,\s+(?:and\s+|but\s+|which\s+|while\s+|so\s+|with\s+)?|;\s+|\s+and\s+(?=\$?\d)|:\s+)")


def _tok_spans(text: str) -> list:
    out = []
    for m in re.finditer(r"[A-Za-z0-9$%'.,-]+", text):
        if not re.search(r"[A-Za-z0-9]", m.group(0)):
            continue
        t = re.sub(r"[^a-z0-9']", "", m.group(0).lower())
        if t:
            out.append((t, m.start(), m.end()))
    return out


def _phrase_span(text: str, phrase: str):
    """(start, end) of the stretch of ``text`` that carries the phrase (or
    the longest part of it the unit shares), or None."""
    ptoks = phrase.split()
    grams = {tuple(ptoks[i:i + rc.PHRASE_N]) for i in range(len(ptoks) - rc.PHRASE_N + 1)} or {tuple(ptoks)}
    spans = _tok_spans(text)
    toks = [t for t, _a, _b in spans]
    covered = set()
    for i in range(len(toks) - rc.PHRASE_N + 1):
        if tuple(toks[i:i + rc.PHRASE_N]) in grams:
            covered.update(range(i, i + rc.PHRASE_N))
    if not covered:
        return None
    a, b = spans[min(covered)][1], spans[max(covered)][2]
    while b > a and text[b - 1] in ".,;:!?":      # the stretch ends on its last word, not on the stop after it
        b -= 1
    return a, b


def _carries(text: str, phrase: str) -> bool:
    return _phrase_span(_norm(text), phrase) is not None


_PREPS = {"in", "on", "at", "of", "for", "with", "across", "among", "against", "inside", "within", "over",
          "under", "from", "by", "than", "versus", "vs", "while", "where", "when", "which", "that", "as", "and"}


def drop_phrase(unit: str, phrase: str, spare=None, title: bool = False) -> str:
    """One unit, said without a phrase the deck already uses twice. Tried
    in order: a plain reword of the phrase's own words; the sentence that
    carries it comes out when the unit has another; a later clause that
    carries it comes out (never the clause the sentence opens on); a
    closing "across ... / in ... / with ..." stretch comes out. A stretch
    is taken out only when every figure in it is in ``spare`` (the
    figures the deck prints somewhere else), so no figure leaves the deck,
    and only when what is left still says what the line said: the words
    in the middle of a claim are never cut ("Search demand is concentrated
    on where to watch" never becomes "Search demand is"). A header is
    only ever reworded. When nothing safe applies the unit comes back
    unchanged and the flag is reported as left."""
    u = _norm(unit)
    spare = set(spare or ())

    def can_go(part: str) -> bool:
        return set(_figures(part)) <= spare

    if not _carries(u, phrase):
        return u
    # 1. reword
    out = u
    for pat, repl in _SWAPS:
        span = _phrase_span(out, phrase)
        if span is None:
            break
        for m in pat.finditer(out):
            if m.end() >= span[0] - 4 and m.start() <= span[1] + 4:
                rep = (repl[0].upper() + repl[1:]) if m.group(0)[:1].isupper() else repl
                out = out[:m.start()] + rep + out[m.end():]
                break
    if not _carries(out, phrase):
        return out
    if title:
        return u                    # a header is one claim: reword or leave
    # 2. the carrying sentence, when another sentence stays
    sents = _sentences(u)
    if len(sents) >= 2:
        keep = [s for s in sents if not _carries(s, phrase)]
        gone = [s for s in sents if _carries(s, phrase)]
        if keep and len(_words(" ".join(keep))) >= 4 and all(can_go(s) for s in gone):
            return " ".join(keep)
    # 3. the carrying clause
    new_sents = []
    changed = False
    for s in sents or [u]:
        if not _carries(s, phrase):
            new_sents.append(s)
            continue
        end = re.search(r"[.!?]+$", s)
        core = s[:end.start()] if end else s
        parts = _CLAUSE_SPLIT.split(core)
        clauses = parts[0::2]
        seps = parts[1::2]
        kept, kept_seps = [], []
        for i, c in enumerate(clauses):
            if i > 0 and _carries(c, phrase) and can_go(c):      # never the clause the sentence opens on
                changed = True
                continue
            if kept:
                kept_seps.append(seps[i - 1] if i - 1 < len(seps) else ", ")
            kept.append(c)
        if kept and len(_words(" ".join(kept))) >= 3 and len(kept) < len(clauses):
            joined = kept[0]
            for sep, c in zip(kept_seps, kept[1:]):
                joined += sep + c
            new_sents.append(_cap(_tidy(joined)) + (end.group(0) if end else ""))
        else:
            new_sents.append(s)
    out = " ".join(new_sents)
    if changed and not _carries(out, phrase):
        return out
    # 4. a closing stretch that hangs off the claim: it opens on a joining
    #    word ("across", "in", "with", "which") and runs to the end of its
    #    sentence. Words in the middle of a claim are never cut.
    span = _phrase_span(out, phrase)
    if span is None:
        return out
    before = out[:span[0]].rstrip()
    prev = re.search(r"([A-Za-z]+)$", before)
    first = re.match(r"([A-Za-z]+)", out[span[0]:])
    start = None
    if first and first.group(1).lower() in _PREPS:
        start = span[0]
    elif prev and prev.group(1).lower() in _PREPS:
        start = prev.start(1)
    tail = out[span[1]:]
    ends = re.match(r"\s*([.!?])(\s|$)", tail)
    if start is None or not ends or not can_go(out[start:span[1]]):
        return u
    cut = _tidy(out[:start].rstrip(" ,;:") + ends.group(1) + tail[ends.end(1):])
    if len(_words(cut)) < 5 or _carries(cut, phrase) or not sane(u, cut):
        return u
    return cut


# ------------------------------------------------------ openers / shape
_LEADS = ("In this audience, ", "Across the window, ", "On this measure, ", "Here, ")
_TITLE_LEADS = ("In total, ", "All told, ", "Overall, ")
_TAIL_PREP = re.compile(
    r"\s(within|inside|during|across|among|after|before|over|under|against|on|in|at)\s((?:\S+\s){1,5}\S+?)([.!?]?)$", re.I)


def _front_tail(sentence: str):
    """"A did B within 90 days." -> "Within 90 days, A did B." or None."""
    s = sentence.strip()
    m = _TAIL_PREP.search(s)
    if not m:
        return None
    head = s[:m.start()].rstrip(" ,")
    if len(_words(head)) < 4 or "," in m.group(2) or re.search(r"[.!?]\s", head):
        return None
    tail = f"{m.group(1)} {m.group(2)}".strip()
    return f"{tail[0].upper()}{tail[1:]}, {_lower_start(head)}{m.group(3) or '.'}"


def vary_opener(text: str, k: int = 0, avoid: str = "", title: bool = False) -> str:
    """The same statement opening another way: a later sentence moves to
    the front when the unit has one that opens differently; a closing
    "within / across / in ..." phrase moves to the front; failing both, a
    short lead-in. ``avoid`` is the opener class the neighbors share."""
    u = _norm(text)
    if not u:
        return u
    sents = _sentences(u)
    if not title and len(sents) >= 2:
        for i in range(1, len(sents)):
            # a sentence that leans on the one before it ("That is ...") cannot lead
            if rc.opener_class(sents[i]) != avoid and rc.is_prose(sents[i]) and re.match(r"[A-Z]", sents[i]) \
                    and not re.match(r"(That|This|It|They|Both|These|Those|Which|He|She|So|But|And|Then|Together|"
                                     r"Its|Their|Either|Neither|Such)\b", sents[i]):
                return " ".join([sents[i]] + sents[:i] + sents[i + 1:])
    first = sents[0] if sents else u
    fronted = _front_tail(first)
    if fronted and rc.opener_class(fronted) != avoid:
        return " ".join([fronted] + sents[1:])
    leads = _TITLE_LEADS if title else _LEADS
    if u.startswith(tuple(_TITLE_LEADS + _LEADS)):
        return u                    # one lead-in at most: never "In total, in total, ..."
    lead = leads[k % len(leads)]
    return lead + _lower_start(u)


# --------------------------------------------------------------- headers
_HEAD_CUTS = re.compile(r",\s+|\s+(?:and|while|with|because|as|so|but)\s+", re.I)
_TRAIL = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "but", "with", "from", "by", "as",
          "than", "that", "which", "is", "are", "was", "were", "its", "their", "this", "these", "over", "per"}


def shorten_header(text: str, ceiling: int = rc.HEADER_WORD_CEILING) -> str:
    """A header over the ceiling is cut at its last clause join inside the
    ceiling, or at the ceiling on a word boundary; never mid-word, never
    ending on a joining word."""
    t = _norm(text).rstrip(".!?")
    words = t.split()
    if len(words) <= ceiling:
        return _norm(text)
    best = None
    for m in _HEAD_CUTS.finditer(t):
        n = len(t[:m.start()].split())
        if 5 <= n <= ceiling:
            best = t[:m.start()]
    if best is None:
        words = words[:ceiling]
        while len(words) > 4 and words[-1].lower().strip(",;:") in _TRAIL:
            words.pop()
        best = " ".join(words)
    return best.rstrip(" ,;:") + "."


def fix_header(text: str, issues: list) -> str:
    """One plain sentence from a header the check flagged."""
    t = _norm(text)
    for issue in issues:
        if issue.startswith("door metaphor"):
            t = scrub_door_metaphor(t)
        elif issue.startswith("jargon"):
            m = re.match(r"jargon '([^']+)'(?: -> '([^']*)')?", issue)
            if m:
                t = _replace_word(t, m.group(1), m.group(2) or "")
        elif issue.startswith("stock phrase"):
            t = scrub_stock(t)
        elif issue.startswith("idiom or metaphor"):
            m = re.match(r"idiom or metaphor: '([^']+)'", issue)
            if m:
                cut = _tidy(re.sub(rf"\b{re.escape(m.group(1))}\b", "", t, flags=re.I))
                if len(cut.split()) >= 3:
                    t = _cap(cut)
        elif issue == "wordplay contraction":
            t = re.sub(r"\bisn't\b", "is not", t, flags=re.I)
            t = re.sub(r"\bain't\b", "is not", t, flags=re.I)
        elif issue == "rhetorical question":
            t = t.rstrip("?").rstrip() + "."
        elif issue == "colon reveal":
            t = re.sub(r"\s*:\s*", ", ", t)
        elif issue == "fragment cadence" or issue.endswith("a header is one sentence"):
            frags = [f.strip() for f in re.split(r"(?<=[.!?])\s+", t) if f.strip()]
            if len(frags) >= 2:
                glue = ", " if issue == "fragment cadence" else ", and "
                joined = frags[0].rstrip(".!?")
                for f in frags[1:]:
                    f = re.sub(r"^(?:And|But|So)\s+", "", f)
                    joined += glue + _lower_start(f.rstrip(".!?"))
                t = joined + "."
    if len(t.split()) > rc.HEADER_WORD_CEILING:
        t = shorten_header(t)
    if t and not re.search(r"[.!?]$", t):
        t += "."
    return _tidy(t)


def _replace_word(text: str, word: str, sub: str) -> str:
    def rep(m):
        if not sub:
            return ""
        return (sub[0].upper() + sub[1:]) if m.group(0)[:1].isupper() else sub
    out = re.sub(rf"\b{re.escape(word)}\b", rep, text, flags=re.I)
    return _cap(_tidy(out)) if not sub else _tidy(out)


# ----------------------------------------------------- table cells (5)
def short_cell(text: str, fits) -> str:
    """The longest opening of a cell that ``fits`` (a callable on a
    candidate string) and still reads whole. Two cuts are tried: at a
    clause join (a comma, a slash, a semicolon) and at a word. The clause
    cut wins when it keeps most of what the word cut keeps (it reads as a
    finished thought); otherwise the word cut, which never ends on a
    joining word or a loose comma. Never inside a word, never an ellipsis.
    The full text comes back when it already fits."""
    t = _norm(text)
    if not t or fits(t):
        return t
    by_clause = ""
    for c in reversed([m.start() for m in re.finditer(r"\s*[,;/]\s+|\s+\(", t)]):
        cand = t[:c].rstrip(" ,;:/(")
        if cand and fits(cand):
            by_clause = cand
            break
    by_word = ""
    words = t.split()
    while len(words) > 1:
        words.pop()
        while len(words) > 1 and words[-1].lower().strip(",;:") in _TRAIL:
            words.pop()
        cand = " ".join(words).rstrip(" ,;:/(")
        if fits(cand):
            by_word = cand
            break
    if by_clause and len(by_clause) >= 0.6 * len(by_word):
        return by_clause
    if by_word:
        return by_word
    return by_clause or (t.split()[0].rstrip(" ,;:/(") if t.split() else t)


# ============================================================ the deck
def _iter_text_shapes(shapes):
    for sh in shapes:
        try:
            if sh.shape_type is not None and int(sh.shape_type) == 6:
                yield from _iter_text_shapes(sh.shapes)
                continue
        except Exception:
            pass
        if getattr(sh, "has_text_frame", False) and sh.text_frame is not None:
            yield sh


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _para_text(p) -> str:
    return "".join((r.text or "") for r in p.runs)


def _set_para(p, text: str) -> None:
    runs = list(p.runs)
    if not runs:
        return
    runs[0].text = text
    for r in runs[1:]:
        r._r.getparent().remove(r._r)
    for br in p._p.findall(f"{_A}br"):
        p._p.remove(br)


def _apply_edit_to_shape(sh, old_unit: str, new_unit: str) -> bool:
    paras = [p for p in sh.text_frame.paragraphs if _para_text(p).strip()]
    if not paras or any(p._p.find(f"{_A}fld") is not None for p in paras):
        return False
    if len(paras) == 1:
        _set_para(paras[0], new_unit)
        return True
    # several paragraphs: change only the paragraph(s) that differ
    texts = [_norm(_para_text(p)) for p in paras]
    rest = new_unit
    plan = []
    for i, t in enumerate(texts):
        if rest.startswith(t):
            plan.append((i, None))
            rest = rest[len(t):].lstrip()
        else:
            # this paragraph changed: it runs to the next unchanged paragraph's text
            nxt = next((u for u in texts[i + 1:] if u and u in rest), None)
            cut = rest.index(nxt) if nxt else len(rest)
            plan.append((i, rest[:cut].strip()))
            rest = rest[cut:].lstrip()
    if rest.strip():
        _set_para(paras[0], new_unit)
        for p in paras[1:]:
            _set_para(p, "")
        return True
    for i, new in plan:
        if new is not None:
            _set_para(paras[i], new)
    return True


def apply_edits(path, edits: list, guard=None, applied=None) -> int:
    """Write ``edits`` into the deck: each is (label or None, old unit text,
    new unit text); ``label`` is "slide N" to pin the page. ``guard`` is
    an optional callable (shape, old text, new text) -> bool that says
    whether the new text may go into that shape (it fits the box; the
    guard may step the type down to make it fit). An edit the guard turns
    away is not written. ``applied`` collects the edits that were written.
    Returns how many shapes changed."""
    from pptx import Presentation  # type: ignore
    if not edits:
        return 0
    prs = Presentation(str(path))
    n = 0
    for idx, slide in enumerate(prs.slides, start=1):
        label = f"slide {idx}"
        mine = [(o, nw) for lab, o, nw in edits if lab in (None, label)]
        if not mine:
            continue
        for sh in _iter_text_shapes(slide.shapes):
            cur = _norm(sh.text_frame.text)
            if not cur:
                continue
            for old, new in mine:
                if cur == _norm(old) and _norm(new) != cur:
                    if guard is not None:
                        try:
                            if not guard(sh, cur, _norm(new)):
                                continue
                        except Exception:
                            pass
                    if _apply_edit_to_shape(sh, cur, _norm(new)):
                        n += 1
                        if applied is not None:
                            applied.append((label, cur, _norm(new)))
                        cur = _norm(new)
    if n:
        prs.save(str(path))
    return n


# ------------------------------------------------------------- targets
class _Deck:
    """The deck as the check reads it: titles by page, units by page."""

    def __init__(self, analysis: dict):
        self.titles = {}
        for h in analysis.get("headers") or []:
            lab, _, text = h.partition(": ") if h.startswith("slide ") else ("", "", h)
            self.titles[_norm(text or h)] = lab or None
        self.units = []
        for lab, units in analysis.get("pages") or []:
            for u in units:
                self.units.append((lab, _norm(u)))

    def label_of(self, text: str):
        t = _norm(text)
        if t in self.titles:
            return self.titles[t]
        for lab, u in self.units:
            if u == t:
                return lab
        return None

    def is_title(self, text: str) -> bool:
        return _norm(text) in self.titles

    def holding(self, fragment: str) -> list:
        """(label, unit) for every unit (title or body) that carries the fragment."""
        f = _norm(fragment).rstrip(".")
        low = f.lower()
        out = [(lab, t) for t, lab in self.titles.items() if low in t.lower()]
        out += [(lab, u) for lab, u in self.units if low in u.lower()]
        return out


def _targets(analysis: dict) -> dict:
    """{(label, unit text): {"problems": [...], "title": bool, ...}} for every
    unit a flag points at; the units a repair should leave alone (the first
    two carriers of a phrase, the first two cards of a shape) are not in it."""
    deck = _Deck(analysis)
    out: dict = {}

    def add(label, text, problem, **extra):
        t = _norm(text)
        if not t:
            return
        key = (label if label is not None else deck.label_of(t), t)
        d = out.setdefault(key, {"problems": [], "title": deck.is_title(t), "kinds": []})
        if problem not in d["problems"]:
            d["problems"].append(problem)
        for k, v in extra.items():
            if isinstance(v, list):
                d.setdefault(k, [])
                d[k] += [x for x in v if x not in d[k]]
            else:
                d[k] = v

    for f in analysis.get("flags") or []:
        kind = f.get("kind")
        if kind == "header":
            add(f.get("label") or None, f["text"], "header: " + "; ".join(f["issues"]),
                kinds=["header"], issues=list(f["issues"]))
        elif kind == "long_sentence":
            for lab, u in deck.holding(f["text"]):
                add(lab, u, f"a sentence of {f['words']} words (30 at most): split it into two sentences",
                    kinds=["long_sentence"], long=[f["text"]])
        elif kind == "grade":
            sents = sorted(f.get("sentences") or [], key=lambda s: -len(_words(s)))[:6]
            for s in sents:
                if len(_words(s)) < 18:
                    continue
                for lab, u in deck.holding(s):
                    add(lab, u, "the deck reads above grade 10: shorter sentences, plainer words",
                        kinds=["grade"], grade=[s])
        elif kind == "jargon":
            for lab, u in deck.holding(f["word"]):
                if re.search(rf"\b{re.escape(f['word'])}\b", u, re.I):
                    add(lab, u, f"jargon '{f['word']}'" + (f": say '{f['sub']}'" if f["sub"] else ": cut it"),
                        kinds=["jargon"], jargon=[(f["word"], f["sub"])])
        elif kind == "overuse":
            hits = [(lab, u) for lab, u in deck.holding(f["word"]) if re.search(rf"\b{re.escape(f['word'])}\b", u, re.I)]
            for lab, u in hits[int(f.get("limit") or 0):] or hits:
                add(lab, u, f"'{f['word']}' is used {f['count']} times: cut it here",
                    kinds=["overuse"], overuse=[f["word"]])
        elif kind == "stock_phrase":
            for lab, u in deck.holding(f["phrase"]):
                add(lab, u, f"stock filler '{f['phrase']}': say what happened instead",
                    kinds=["stock_phrase"])
        elif kind == "door":
            for lab, u in [(l, t) for t, l in deck.titles.items()] + deck.units:
                if rc.door_hits(u):
                    add(lab, u, "the word door: state the claim without it", kinds=["door"])
        elif kind == "same_shape":
            for k, card in enumerate(f["cards"][2:]):
                add(f.get("label"), card,
                    f"three or more cards on this page open the same way ({f['opener']}): open this one differently",
                    kinds=["same_shape"], avoid_opener=f["opener"], vary_k=k)
        elif kind == "header_opener":
            for k, t in enumerate(f["titles"][2:]):
                add(None, t, f"three or more headers open '{f['key']} ...': open this one differently",
                    kinds=["header_opener"], avoid_opener=rc.opener_class(t), vary_k=k)
        elif kind == "header_run":
            for k, i in enumerate(range(2, len(f["titles"]), 3)):
                add(None, f["titles"][i],
                    f"three or more headers in a row open on '{f['opener']}': open this one differently",
                    kinds=["header_run"], avoid_opener=f["opener"], vary_k=k)
        elif kind == "repeated_phrase":
            carriers = list(zip(f["units"], f.get("is_title") or [False] * len(f["units"])))
            # the first two say it; the rest give way, body copy before a header, later pages first
            order = sorted(range(len(carriers)), key=lambda i: (carriers[i][1], -i))
            for i in order[:max(0, len(carriers) - 2)]:
                add(None, carriers[i][0],
                    f"the phrase '{f['phrase']}' is used in {f['count']} places in this deck: "
                    "say this without those words", kinds=["repeated_phrase"], phrases=[f["phrase"]])
    # the figures each flagged unit shares with the rest of the deck: a repair
    # may take a stretch out only when its figures are printed somewhere else
    everything = [t for t in deck.titles] + [u for _lab, u in deck.units]
    counts: dict = {}
    for u in everything:
        for f in set(_figures(u)):
            counts[f] = counts.get(f, 0) + 1
    for (_lab, text), d in out.items():
        d["spare"] = sorted(f for f in set(_figures(text)) if counts.get(f, 0) >= 2)
    return out


# -------------------------------------------------------------- rewrite
def acceptable(old: str, new, *, title: bool = False, phrases=(), avoid_opener: str = "") -> bool:
    """A model rewrite is taken only when it keeps every figure, stays
    about the same length, and breaks no house rule."""
    if not isinstance(new, str):
        return False
    o, n = _norm(old), _norm(new)
    if not n or n == o or "\n" in new.strip():
        return False
    if _figures(o) != _figures(n):
        return False
    if len(n) > len(o) * 1.08 + 6 or len(n) < max(8, len(o) * 0.35):
        return False
    if re.search(r"[\u2014\u2013]", n) or rc.door_hits(n) or rc.stock_hits(n):
        return False
    if re.search(r"[.!?]$", o) and not re.search(r"[.!?]$", n):
        return False
    if any(_carries(n, p) for p in phrases or ()):
        return False
    if avoid_opener and rc.opener_class(n) == avoid_opener:
        return False
    if any(len(_words(s)) > rc.SENTENCE_WORD_CEILING for s in rc.sentences_of(n)):
        return False
    if title:
        if len(n.split()) > rc.HEADER_WORD_CEILING or rc.clever_flags(n):
            return False
    return True


_DOUBLED = re.compile(r"\b([A-Za-z]{2,})\s+\1\b", re.I)
_LOOSE = re.compile(r",\s*[,.;:]|\.\.|\(\s*\)|\s[,.;:]|^[,.;:]|[,;:]$")


def sane(old: str, new: str) -> bool:
    """A rule repair is written only when the result still reads: no word
    doubled by a cut ("pilot pilot"), no loose punctuation, no figure that
    was not there, the same kind of ending, and enough words left to be a
    line. A repair that fails this is not made; the flag stays and the
    status says so."""
    o, n = _norm(old), _norm(new)
    if not n:
        return False
    if _DOUBLED.search(n) and not _DOUBLED.search(o):
        return False
    if _LOOSE.search(n) and not _LOOSE.search(o):
        return False
    if set(_figures(n)) - set(_figures(o)):
        return False
    if re.search(r"[.!?]$", o) and not re.search(r"[.!?]$", n):
        return False
    if len(_words(n)) < min(3, len(_words(o))):
        return False
    if re.match(r"[a-z]", n) and not re.match(r"[a-z]", o):
        return False
    return True


def _fallback(text: str, t: dict) -> str:
    """The fixed repair for one flagged unit."""
    out = _norm(text)
    kinds = t.get("kinds") or []
    if "door" in kinds:
        out = scrub_door_metaphor(out)
    if "stock_phrase" in kinds:
        out = scrub_stock(out)
    for word, sub in t.get("jargon") or []:
        out = _replace_word(out, word, sub)
    for word in t.get("overuse") or []:
        cut = _tidy(re.sub(rf",?\s*\b{re.escape(word)}\b,?", " ", out, flags=re.I))
        out = _cap(cut) if cut else out
    if "header" in kinds:
        out = fix_header(out, t.get("issues") or [])
    for phrase in t.get("phrases") or []:
        out = drop_phrase(out, phrase, t.get("spare"), title=bool(t.get("title")))
    if "long_sentence" in kinds or "grade" in kinds:
        ceiling = rc.SENTENCE_WORD_CEILING if "long_sentence" in kinds and "grade" not in kinds else 18
        closed = bool(re.search(r"[.!?]$", out))
        parts = [split_long_sentence(s, ceiling) if len(_words(s)) > ceiling else s for s in _sentences(out)]
        out = " ".join(parts)
        if not closed:
            out = out.rstrip(".")
    # a lead-in goes on a sentence, never on a figure or a label standing alone
    if any(k in kinds for k in ("same_shape", "header_opener", "header_run")) \
            and len(re.findall(r"[A-Za-z]{2,}", out)) >= 3:
        out = vary_opener(out, int(t.get("vary_k") or 0), str(t.get("avoid_opener") or ""),
                          title=bool(t.get("title")))
        if t.get("title") and len(out.split()) > rc.HEADER_WORD_CEILING:
            out = shorten_header(out)
    return out


def fix_file(path, rewriter=None, *, llm_passes: int = LLM_PASSES, rounds: int = FALLBACK_ROUNDS,
             guard=None) -> dict:
    """Repair a deck's flagged copy in place. ``rewriter`` is an optional
    callable: it takes a list of ``{"id", "text", "role", "problems",
    "keep"}`` and returns ``{id: new text}``. ``guard`` (see
    ``apply_edits``) keeps a repair out of a box it does not fit. Returns ``{"before",
    "after", "rewritten", "fallback", "notes", "edits"}`` (``edits`` is every
    (label, old text, new text) written, in order). Never raises."""
    res = {"before": None, "after": None, "rewritten": 0, "fallback": 0, "notes": [], "edits": []}
    try:
        if Path(str(path)).suffix.lower() != ".pptx":
            a = rc.analyze_path(path)
            res["before"] = res["after"] = a["count"]
            return res
        a = rc.analyze_path(path)
        res["before"] = res["after"] = a["count"]
        if a["count"] == 0:
            return res
        # the model pass: only the flagged text, one or two passes
        for _pass in range(max(0, int(llm_passes)) if rewriter else 0):
            tg = _targets(a)
            if not tg:
                break
            keys = list(tg.keys())
            items = [{"id": f"u{i + 1}", "text": k[1], "role": "header" if tg[k]["title"] else "body",
                      "problems": list(tg[k]["problems"]), "keep": _figures(k[1])}
                     for i, k in enumerate(keys)]
            try:
                got = rewriter(items) or {}
            except Exception as e:
                res["notes"].append(f"rewrite pass failed: {type(e).__name__}")
                got = {}
            edits = []
            for i, k in enumerate(keys):
                new = got.get(f"u{i + 1}")
                if acceptable(k[1], new, title=tg[k]["title"], phrases=tg[k].get("phrases") or (),
                              avoid_opener=str(tg[k].get("avoid_opener") or "")):
                    edits.append((k[0], k[1], _norm(new)))
            if not edits:
                break
            n = apply_edits(path, edits, guard, res["edits"])
            res["rewritten"] += n
            a = rc.analyze_path(path)
            res["after"] = a["count"]
            if a["count"] == 0:
                return res
        # the fixed repair for whatever is left
        for _round in range(max(0, int(rounds))):      # rounds=0: the model pass only
            tg = _targets(a)
            edits = []
            for k, t in tg.items():
                new = _fallback(k[1], t)
                if new and _norm(new) != k[1] and sane(k[1], new):
                    edits.append((k[0], k[1], new))
            if not edits:
                break
            n = apply_edits(path, edits, guard, res["edits"])
            if not n:
                break
            res["fallback"] += n
            a = rc.analyze_path(path)
            res["after"] = a["count"]
            if a["count"] == 0:
                break
        if res["after"]:
            res["notes"] += [f.get("line") or f.get("kind") for f in a.get("flags") or []][:8]
    except Exception as e:  # never hold the file
        res["notes"].append(f"copy fix stopped: {type(e).__name__}: {e}"[:200])
    return res


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    if "--check" in args:
        a = rc.analyze_path(path)
        print("\n".join(a["lines"]))
        return 0 if a["count"] == 0 else 1
    res = fix_file(path)
    print(f"flags before: {res['before']}; after: {res['after']}; "
          f"units repaired: {res['rewritten'] + res['fallback']}")
    for n in res["notes"]:
        print("  left:", n)
    return 0 if not res["after"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
