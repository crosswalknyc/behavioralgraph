"""One key for one question (2026-10-26, the week of Oct 19 report).

Three places decided whether a new ask was "the same question again":
the in-flight read mark (chat.py), the repeat guard (turn_gate.py) and
the thread replay (smarter.py). Each folded the text its own way and
all three matched the exact words only. Jay Patel asked "What did the
BookTok exposed viewers watch on Prime Video in the 30 days after the
film?" and then the same question with "can you give me the results to
this questio" on the end: three reads ran and three different answers
landed (87.3%, 83.7%, 83.9%). This module is the one fold they share.

`fold(text)` lowercases, drops leading courtesy ("please", "hey", "ok",
"again", "also") and trailing asks for the answer itself ("can you give
me the results to this question", "please", "thanks", "asap", "again"),
and keeps every content word. `key(text)` is the md5[:16] of the fold
(the in-flight doc's key shape). `same(a, b)` is fold equality on two
non-empty folds.
"""
from __future__ import annotations

import hashlib
import re

_LEAD_RX = re.compile(
    r"^\W*(?:(?:hi|hey|hello|ok|okay|so|please|prometheus|again|also|and|now)[,\s]+)+", re.I)
_TAIL_RX = re.compile(
    r"[\s,.;:!?-]*(?:"
    r"(?:and\s+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:give|send|show|get|tell)\s+me\s+"
    r"(?:the\s+)?(?:results?|answers?|numbers?|data|read|output)(?:\s+(?:to|for|of|on)\s+(?:this|that|the)(?:\s+questio?n?)?)?"
    r"|(?:and\s+)?(?:please\s+)?(?:give|send|show)\s+me\s+the\s+(?:results?|answers?|numbers?)(?:\s+(?:to|for|of)\s+(?:this|that)(?:\s+questio?n?)?)?"
    r"|(?:and\s+)?(?:i\s+)?(?:still\s+)?(?:need|want)\s+(?:the\s+)?(?:results?|answers?)(?:\s+(?:to|for)\s+this)?"
    r"|please|pls|plz|thanks?(?:\s+you)?|thx|asap|again|when\s+you\s+can|if\s+you\s+can|if\s+possible"
    r")[\s.!?]*$", re.I)


def fold(text) -> str:
    """The content words of a question, lowercased, one space apart."""
    t = ' '.join(str(text or '').split())
    t = _LEAD_RX.sub('', t)
    for _ in range(4):
        t2 = _TAIL_RX.sub('', t)
        if t2 == t:
            break
        t = t2
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def key(text) -> str:
    """Stable key for one question: md5[:16] of the fold."""
    return hashlib.md5(fold(text).encode('utf-8')).hexdigest()[:16]


def same(a, b) -> bool:
    fa, fb = fold(a), fold(b)
    return bool(fa) and fa == fb


__all__ = ['fold', 'key', 'same']
