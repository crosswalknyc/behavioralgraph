"""The person's own words, apart from what they pasted.

Bria Dotson (2026-10-23) pasted a monthly consumption read on The Office
under three sentences asking for the same report on calendar 2023, 2024
and 2025. The message ran past 2,000 characters, so every detector that
reads an ask (priced_read.classify and the lanes behind it) stepped aside
at its length guard and the read ran with no price and no Approve, and
the reader mined the paste for subjects ("On it: reading Back Into
Rotation and The Office now"). Jenna: "this would have been viewership
and $500".

`own_words(text)` returns the request itself: the lines before the first
line that reads as pasted material (a table row, a line thick with
figures, a quoted reply's opening, a bullet run), plus a short tail
after the paste when the person asked there. `split(text)` returns both
halves. A message with no paste comes back whole.
"""
from __future__ import annotations

import re

__all__ = ['own_words', 'split', 'has_paste']

# a figure is a count or a decimal; a bare four-digit year is a word in a request ("calendar 2023, 2024 and 2025")
_FIGURE_RX = re.compile(r"\d{1,3}(?:,\d{3})+|\b\d{5,}\b|\b\d+\.\d+\b|\b(?!(?:19|20)\d{2}\b)\d{4}\b")
_REPLY_OPENER_RX = re.compile(
    r"^\W*(?:you asked (?:for|about)|here it is\b|here is what|here are the|the short version|twelve-month reach|"
    r"what moved the line|if it helps|how these are counted|the numbers:|window:|month\s+viewers|"
    r"(?:step|section|stage|branch)\s+(?:accounts|us population)|spine\b)", re.I)
_HEADER_ROW_RX = re.compile(r"^\W*(?:month|year|quarter|week|date|platform|service|title|brand|segment)\b.*\b(?:viewers|hours|accounts|people|share|index|penetration|visits|conversion)\b", re.I)
_BULLET_RX = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+\S")
_TAIL_ASK_RX = re.compile(r"\?|\b(?:can you|could you|please|run|pull|show|give|send|make sure|same)\b", re.I)


def _pasted_line(line: str, prev_pasted: bool) -> bool:
    s = line.strip()
    if not s:
        return False
    if '\t' in line:
        return True
    figs = _FIGURE_RX.findall(s)
    if len(figs) >= 3:
        return True
    if len(figs) >= 2 and len(s.split()) <= 8:
        return True                       # "Oct 2025  10,654,292  3.8" without tabs
    if _REPLY_OPENER_RX.match(s) or _HEADER_ROW_RX.match(s):
        return True
    if prev_pasted and (_BULLET_RX.match(line) or len(figs) >= 1):
        return True                       # a bullet run or a figure line continuing the paste
    return False


def split(text) -> tuple:
    """(own_words, pasted). ``pasted`` is '' when the message carries none."""
    raw = str(text or '')
    if not raw.strip():
        return '', ''
    lines = raw.splitlines()
    if sum(1 for ln in lines if ln.strip()) < 2:
        return ' '.join(raw.split()), ''        # one line is the person's own sentence, figures and all
    flags, prev = [], False
    for ln in lines:
        f = _pasted_line(ln, prev)
        flags.append(f)
        prev = f or (prev and not ln.strip())    # a blank line inside a paste keeps the run alive
    if not any(flags):
        return ' '.join(raw.split()), ''
    first = flags.index(True)
    last = len(flags) - 1 - flags[::-1].index(True)
    head = ' '.join(' '.join(lines[:first]).split())
    tail = ' '.join(' '.join(lines[last + 1:]).split())
    pasted = ' '.join(' '.join(lines[first:last + 1]).split())
    own = head
    # the ask may sit under the paste ("<table> Can you run this for 2023?")
    if tail and len(tail) <= 600 and _TAIL_ASK_RX.search(tail):
        own = (head + ' ' + tail).strip() if head else tail
    elif not head and tail:
        own = tail
    if len(own) < 12 and pasted:
        # a one-word lead ("Below:") with everything else pasted: the paste's own first line is the ask
        return own, pasted
    return own, pasted


def has_paste(text) -> bool:
    return bool(split(text)[1])


def own_words(text) -> str:
    """The request without the pasted material; the whole message when there is none."""
    own, pasted = split(text)
    return own if (own or not pasted) else ' '.join(str(text or '').split())
