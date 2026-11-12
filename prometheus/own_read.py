"""A question about the reader's own read is answered about that read
(Jenna 2026-11-11: "these errors cannot happen").

Armine Meseldzhyan (Lionsgate), 21:06 UTC on 2026-11-11, one turn after
a read on which categories of digital original content do best: "Can we
have all these categories Comedy, lifestyle/Arts & Culture, Young
Adult, Horror, Faith, Drama. Also list example sof each". The reply was
"Yes. A Profile IQ profile builds the audience of any person, title,
brand or behavior ... Tell me the subject and the window and I will
line it up".

The cause: a message that opens "Can we ..." was taken as a question
about what the product does, and when it named no product at all the
answer defaulted to the Profile IQ description. Nothing looked at what
the question pointed at.

The rule: a follow-up that points at what is on the thread ("these",
"all of them", "the same for", "of each"), or that lists the things the
thread's last answer was about, or that names no product while a read
sits in the thread, is a question about that read. The product
description lanes stand aside for it and the answer lanes take it. A
product description is the answer only to a question about a product.

Pure functions over the words and the thread. Nothing here raises.
"""
from __future__ import annotations

import re

__all__ = ['about_thread', 'points_at_thread', 'lists_thread_items', 'names_a_product', 'thread_has_answer']

# words that point at things already on the thread
_POINTER_RX = re.compile(
    r"\b(?:all\s+(?:of\s+)?(?:these|those|them)|(?:these|those)\b(?!\s+days)|(?:each|every\s+one|any|some|both|all|most|none)\s+of\s+"
    r"(?:them|these|those|the\s+above)|of\s+each|for\s+each(?:\s+one)?|each\s+one|the\s+same\s+(?:for|on|with|thing|split|read|cut|breakdown)|"
    r"same\s+for|the\s+above|listed\s+above|from\s+above|mentioned\s+above|the\s+ones\s+(?:above|you\s+(?:listed|gave|named|showed)))\b", re.I)
# the products a can-we question can be about
_PRODUCT_RX = re.compile(
    r"\b(?:profile\s+iq|subscriber\s+iq|digital\s+journey(?:\s+iq)?|journey\s+iq|attribution\s+iq|brand\s+partnership(?:\s+iq)?|"
    r"impact\s+iq|trends\s+iq|analysis\s+iq|focus\s+groups?|digital\s+twins?|a\s+profile|new\s+profile|profiles|a\s+journey|"
    r"a\s+valuation|api|dashboard|prometheus|crosswalk)\b", re.I)
_SPLIT_RX = re.compile(r"\s*(?:,|;|/|\band\b|\bor\b|&|\n)\s*", re.I)
_LEAD_RX = re.compile(
    r"^\W*(?:can|could|would|will|do|does|is|are|should)\s+(?:we|you|i|it|there)\b\s*(?:have|get|see|do|add|include|show|list|"
    r"give\s+(?:me|us)|pull|run|also)?\s*", re.I)


def _squash(text):
    return ' '.join(str(text or '').split())


def _fold(text):
    return re.sub(r"[^a-z0-9]+", ' ', str(text or '').lower()).strip()


def _turn_text(turn):
    return str((turn or {}).get('text') or (turn or {}).get('content') or '')


def thread_has_answer(history):
    """True when the thread holds an answer a question could be about: a
    read that landed (its record on the turn) or any written answer of
    some length."""
    for turn in reversed(list(history or [])[-12:]):
        if not isinstance(turn, dict) or str(turn.get('role') or '') in ('user', 'human'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        if meta.get('read_job_id') or str(meta.get('kind') or '') == 'read' or meta.get('figures'):
            return True
        if len(_turn_text(turn).strip()) > 160:
            return True
    return False


def points_at_thread(text):
    """True when the words point at things already said ("all these
    categories", "the same for", "examples of each")."""
    return bool(_POINTER_RX.search(_squash(text)))


def names_a_product(text):
    """True when the words name a product or the thing a product does
    (a profile, a journey, a campaign to track, signups and churn)."""
    t = _squash(text)
    if _PRODUCT_RX.search(t):
        return True
    try:
        from . import smarter as _sm
        return any(rx.search(t) for rx, _n, _w, _c in _sm._PRODUCT_WORDS)
    except Exception:
        return False


def lists_thread_items(text, history, least=2):
    """True when the question lists things the thread already carries
    (her six categories were in the question and the read before)."""
    t = _LEAD_RX.sub('', _squash(text))
    items = []
    for part in re.split(r"[.?!]\s+", t):
        for it in _SPLIT_RX.split(part):
            f = _fold(it)
            # a listed thing is short: a name or a label, never a clause
            f = re.sub(r"^(?:all|these|those|the|categories|category|list|also|of|for|examples?|each)\s+", '', f)
            f = re.sub(r"^(?:all|these|those|the|categories|category)\s+", '', f)
            if 3 <= len(f) <= 40 and len(f.split()) <= 4:
                items.append(f)
    if len(items) < least:
        return False
    blob = ' ' + ' '.join(_fold(_turn_text(tn)) for tn in list(history or [])[-8:]) + ' '
    hits = {it for it in items if f' {it} ' in blob}
    return len(hits) >= least


def about_thread(text, history, ctx=None):
    """True when a can-we question is about what the thread holds, not
    about what a product does."""
    try:
        if not thread_has_answer(history):
            return False
        if points_at_thread(text) or lists_thread_items(text, history):
            return True
        return not names_a_product(text) and bool(_last_is_read(history))
    except Exception:
        return False


def _last_is_read(history):
    """The thread's last answer is a read (its record is on the turn)."""
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict) or str(turn.get('role') or '') in ('user', 'human'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        return bool(meta.get('read_job_id') or str(meta.get('kind') or '') == 'read' or meta.get('figures'))
    return False
