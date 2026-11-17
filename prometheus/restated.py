"""The reader says the last reply missed, and says the ask again (Cara
Dorfman, Amazon, 2026-11-16; Jenna: "Its answering cara horribly. look at
her questions and fix so it doesnt happen again").

Four turns in a row, each a no and the ask said again in other words, each
routed on its surface words as if it were a brand new message:

  "i don't care about content. i just care about the number of subscribers
   to say for example paramount+. do you estimate that anywhere?"
  "no. i just want to see where the metric of retention curves are on the
   crosswalk dashboard"                       -> a read on the open profile
  "I DONT WANT THE TITLE. I WANT TO KNOW ABOUT THE SURVIVAL CURVE"
        -> "Understood. THE TITLE. I WANT is out of this thread for good."
  "NO. NOT TITLES. SUBSCRIBER COUNTS. LIKE HOW MANY CUSTOMERS SUBSCRIBE TO A
   SERVICE, THIER CHURN AND SURVIVAL CURVE."                 -> a $100 quote

Three rules live here, each pure text:

  split(text)        the part that says no, and the ask as it was said
                     again. The ask said again is what gets answered; the
                     no is never routed (a no is not a name, not an
                     audience and not an order).
  is_generic(name)   "the title", "content", "titles", "that profile": a
                     kind of thing, never a name. A correction never drops
                     one from the thread ("... is out of this thread for
                     good" is for a name the reader pushed away).
  fold_shout(text)   a message typed in capitals is read as the same words
                     in sentence case. Capitals are volume, not names; the
                     lanes that take a capitalised run as a name took whole
                     sentences as one.

Never raises. Regression: scripts/test_pm_cara_2026_11_16.py.
"""
from __future__ import annotations

import re

__all__ = ['split', 'is_generic', 'is_shout', 'fold_shout', 'clip_name']

_SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+")

# a sentence that only says no
_NO_RX = re.compile(
    r"^\W*(?:no+|nope|nah|wrong|incorrect|negative|not\s+that|not\s+quite|not\s+right|not\s+it|"
    r"that(?:'s| is| was)\s+(?:not\s+(?:it|right|correct|what\s+i\s+(?:asked|meant|want|wanted|said|need))|wrong|incorrect)|"
    r"this\s+(?:is|was)\s+(?:not\s+(?:it|right|correct|what\s+i\s+(?:asked|meant|want|wanted|said|need))|wrong|incorrect)|"
    r"not\s+what\s+i\s+(?:asked|meant|wanted|want|said|need)(?:\s+for)?|you\s+(?:misunderstood|misread)(?:\s+me)?|"
    r"you\s+got\s+(?:it|that)\s+wrong|that\s+did\s*n[o']?t\s+answer(?:\s+(?:it|my\s+question|the\s+question))?)\W*$", re.I)
# a sentence that pushes a kind of thing away: "i don't care about content", "I dont want the title", "not titles"
_NOT_WANT_RX = re.compile(
    r"^\W*(?:no[,.!]?\s+)?(?:i\s+(?:do\s*n[o']?t|did\s*n[o']?t|never|do\s+not|did\s+not)\s+"
    r"(?:want|need|care\s+(?:about|for)|mean|ask(?:ed)?\s+(?:for|about)|say|said)|"
    r"i\s*(?:am|'m)\s+not\s+(?:asking|talking|interested)\s+(?:about|in|for)|i\s+was\s*n[o']?t\s+(?:asking|talking)\s+(?:about|for)|"
    r"not|no)\s+(?P<what>[^.!?]{1,60}?)\W*$", re.I)
# one sentence that does both: "I don't want the title, I want the survival curve", "not titles but subscriber counts"
_BOTH_RX = re.compile(
    r"^\W*(?:no[,.!]?\s+)?(?:i\s+(?:do\s*n[o']?t|do\s+not)\s+(?:want|need|care\s+(?:about|for)|mean)|not)\s+(?P<what>[^,;]{1,50}?)"
    r"\s*(?:[,;]\s*(?:but\s+)?|\s+but\s+)(?P<rest>(?:i\s+(?:just\s+|only\s+)?(?:want|need|care|mean)\b|just\s+|only\s+|rather\s+).{6,})$", re.I)
# "no problem", "no thanks", "not sure": not a no to the last reply
_NOT_A_NO_RX = re.compile(
    r"^\W*(?:no\s+(?:problem|worries|thanks|thank\s+you|rush|need|idea|way|more|longer|doubt|wonder|matter|big\s+deal)|"
    r"not\s+(?:sure|yet|now|really|bad|necessarily|at\s+all|a\s+problem)|nothing|none|never\s*mind)\b", re.I)
# "no a pdf was created": the reader is stating what is true, which is context for the ask and stays with it
_STATES_A_FACT_RX = re.compile(r"\b(?:was|is|are|were|has|have|had|did|does|do|can|could|will|would|should|exists?)\b", re.I)
_LEAD_NO_RX = re.compile(r"^\W*(?:no+|nope|nah|wrong|not\s+that|not\s+quite)\s*[,:;\-\u2013\u2014]+\s*(?=\S)", re.I)
_LEAD_MEAN_RX = re.compile(r"^\W*(?:i\s+(?:just\s+)?mean(?:t)?|what\s+i\s+(?:mean(?:t)?|want(?:ed)?|need(?:ed)?|am\s+after)\s+(?:is|was)|rather|instead)[,:]?\s+", re.I)

_GENERIC = frozenset("""
title titles content contents show shows movie movies film films series season seasons episode episodes program programs
profile profiles audience audiences brand brands viewer viewers subject subjects name names thing things stuff
that this it those these them one ones number numbers figure figures data read reads build builds answer answers
deck decks report reports list lists info information pitch product products page pages screen tab tabs
metric metrics detail details specifics example examples option options kind kinds type types sort
""".split())
_GENERIC_LEAD = frozenset(('the', 'a', 'an', 'any', 'that', 'this', 'those', 'these', 'your', 'my', 'our', 'single', 'specific',
                           'particular', 'one', 'some', 'all', 'each', 'every', 'just', 'only', 'more', 'another', 'other'))


def _squash(text):
    return ' '.join(str(text or '').split())


def is_generic(name):
    """True for a kind of thing ("the title", "content", "any single
    title"), which is never a name to drop or to size."""
    words = [w for w in re.split(r"[^a-z0-9+&']+", _squash(name).lower()) if w]
    while words and words[0] in _GENERIC_LEAD:
        words.pop(0)
    if not words:
        return True
    return all(w in _GENERIC or w in _GENERIC_LEAD for w in words)


def clip_name(name):
    """A pushed-away name ends where its sentence ends: "THE TITLE. I WANT"
    is "THE TITLE"."""
    s = _squash(name)
    m = re.search(r"[.!?;]", s)
    if m:
        s = s[:m.start()]
    return s.strip(' ,;:.!?')


def split(text):
    """(rejected, restated). ``rejected`` is the list of sentences that
    only say no or push a kind of thing away; ``restated`` is the ask as
    it was said again, in the reader's words, '' when the turn carries
    none (a bare "no", "not that")."""
    try:
        t = _squash(text)
        if not t or len(t) > 900:
            return [], ''
        if _NOT_A_NO_RX.match(t):
            return [], ''
        sents = [s.strip() for s in _SENT_SPLIT_RX.split(t) if s.strip()]
        rejected, i = [], 0
        while i < len(sents):
            s = sents[i]
            if _NO_RX.match(s):
                rejected.append(s)
                i += 1
                continue
            m = _NOT_WANT_RX.match(s)
            if m and len(sents) > i + 1 and not _STATES_A_FACT_RX.search(m.group('what')) \
                    and (is_generic(m.group('what')) or len(m.group('what').split()) <= 4):
                rejected.append(s)
                i += 1
                continue
            break
        rest = sents[i:]
        if rest:
            b = _BOTH_RX.match(rest[0])
            if b:
                rejected.append(rest[0][:b.start('rest')].strip(' ,;'))
                rest[0] = b.group('rest').strip()
            else:
                stripped = _LEAD_NO_RX.sub('', rest[0], count=1)
                if stripped != rest[0] and stripped.strip():
                    rejected.append(rest[0][:len(rest[0]) - len(stripped)].strip(' ,;:-'))
                    rest[0] = stripped.strip()
        if not rejected:
            return [], ''
        restated = ' '.join(rest).strip()
        restated = _LEAD_MEAN_RX.sub('', restated, count=1).strip()
        if len(restated.split()) < 2:
            return rejected, ''
        return rejected, restated[:1].upper() + restated[1:]
    except Exception:
        return [], ''


# ------------------------------------------------------------------- shout
def is_shout(text):
    """A message typed in capitals: five words or more, nearly every
    letter upper case. A short all-caps name ("NCIS LA", "UFC 300") is a
    name and stays as typed."""
    t = _squash(text)
    letters = [c for c in t if c.isalpha()]
    if len(t.split()) < 5 or len(letters) < 20:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / float(len(letters)) >= 0.9


def fold_shout(text):
    """The same words in sentence case when the message was typed in
    capitals; the text unchanged otherwise."""
    try:
        if not is_shout(text):
            return str(text or '')
        t = str(text or '').lower()
        t = re.sub(r"(^\s*|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
        t = re.sub(r"\bi\b(?=(?:'|\u2019)?(?:m|ve|ll|d)?\b)", 'I', t)
        return t
    except Exception:
        return str(text or '')
