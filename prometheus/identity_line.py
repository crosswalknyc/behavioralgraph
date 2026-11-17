"""The "Subject:" line on a brief says who or what the subject is. Nothing else.

2026-11-17 audit. Three briefs that day carried the working notes behind
the line onto the card the reader approves:

  "Sara Boustany is a lifestyle and fashion influencer/creator active
   across Instagram and TikTok; no major same-name collision detected,
   though she is a relatively niche figure - set to false pending user
   confirmation"
  "Euphoria, the HBO/Max teen drama series (US), not to be confused with
   any other property of the same name"
  "Euphoria is the HBO original teen drama series created by Sam
   Levinson, starring Zendaya - NOT a beauty brand or any other property
   sharing the name"

`clean(note)` keeps the description and drops everything from the first
clause that talks about the naming, the checking or the reader instead
of the subject. A note that is nothing but working returns '', and the
caller falls back to the plain "<title>, the <medium> on <platform>".

Pure: no model, no storage. Regression: scripts/test_pm_audit_2026_11_17.py.
"""
from __future__ import annotations

import re

# a clause that is about the naming or the checking, never about the subject.
# Narrow on purpose: a plain "not the skateboarder" and an honest "I could
# not place this name" are things the reader should see, and they stay.
_WORKING_RX = re.compile(
    r"\b(?:same[- ]name|collisions?|not\s+to\s+be\s+confused|sharing\s+th(?:e|is|at)\s+name|of\s+the\s+same\s+name|"
    r"with\s+th(?:e|is|at)\s+same\s+name|disambiguat\w*|"
    r"set\s+to\s+(?:true|false)|pending\s+(?:user\s+|their\s+|your\s+)?(?:confirmation|review|approval)|"
    r"assum(?:e|ed|es|ing|ption)\b|the\s+user\b|user'?s\s+(?:own|ask|request|words|prompt|intent)|flag(?:ged)?\b|"
    r"niche\s+figure|no\s+(?:major|notable|known|significant|obvious)\s+(?:same|name|collision|conflict|overlap)\w*)", re.I)
_SHOUT_NOT_RX = re.compile(r"\bNOT\b")          # "NOT a beauty brand": a note to self, in capitals
# an inline tail that starts mid-clause
_INLINE_TAIL_RX = re.compile(
    r"\s*[,(]\s*(?:and\s+|but\s+|though\s+|although\s+)?(?:not\s+to\s+be\s+confused\s+with|not\s+the\s+same\s+as|"
    r"distinct\s+from|as\s+opposed\s+to|rather\s+than\s+the|though\b|although\b|however\b)\b.*$", re.I | re.S)
_SPLIT_RX = re.compile(r"\s*;\s*|\s+-\s+|\.\s+(?=[A-Z])")


def _working(clause):
    return bool(_WORKING_RX.search(clause) or _SHOUT_NOT_RX.search(clause))


def clean(note):
    """The description of the subject, without the working behind it."""
    t = ' '.join(str(note or '').split())
    if not t:
        return ''
    # clause starts: the text is cut where the first working clause begins
    starts, ends = [0], []
    for m in _SPLIT_RX.finditer(t):
        ends.append(m.start())
        starts.append(m.end())
    ends.append(len(t))
    out = t
    for i, (a, b) in enumerate(zip(starts, ends)):
        clause = t[a:b]
        if not _working(clause):
            continue
        head = _INLINE_TAIL_RX.sub('', clause).strip()
        if i == 0 and head and head != clause.strip() and not _working(head):
            out = head                 # the description, with its tail cut
        else:
            out = t[:ends[i - 1]] if i else ''
        break
    out = out.strip(' ,;:-(').rstrip('.')
    if out.count('(') > out.count(')'):
        out = out[:out.rindex('(')].rstrip(' ,;:-')
    return out if len(out) >= 3 and not _working(out) else ''
