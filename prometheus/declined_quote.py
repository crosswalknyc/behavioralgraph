"""A quote the reader turned down is not drawn again in that thread
(Jenna 2026-11-12).

Two different things happen when a quoted question is sent again:

  * the reader was quoted and has not answered: the quote stands. The
    same quote at the same price, no new run, no second quote stacked on
    it, no charge. Nothing was rejected (prometheus.turn_gate).
  * the reader was quoted and said no ("No, run what I asked", "not
    that", "cancel"), then sends the question again: that pair was
    rejected and is never routed again in that thread. On prod the lanes
    drew the same quote a second time, and "No, run what I asked" drew a
    second, differently worded quote on the spot.

This module reads the thread for the second case. It decides nothing
about price and runs nothing:

  `declined(text, history)`  the quote this same question drew earlier in
      the thread, when the reader then turned it down and has not been
      quoted since; else None.
  `says_no(text)`            a turn that only turns something down (no
      new question in it).
  `turned_down_now(text, history)`  this turn is the no itself, said
      straight after a quote.
  `wants_requote(text, history)`  the reader asked for the quote again
      after the line below; the question it was for.

The turn gate answers the first with DECLINED_LINE (no run, no charge,
no quote) and lets the last one through to be quoted again, because the
reader asked for exactly that.
"""
from __future__ import annotations

import re

__all__ = ['DECLINED_LINE', 'NO_LINE', 'is_quote_turn', 'says_no', 'declined', 'turned_down_now', 'wants_requote', 'is_quote_text', 'quote_stands', 'standing', 'stored_raw', 'STANDS_LEAD']

DECLINED_LINE = ("You turned that read down, so nothing ran and nothing was charged. Tell me what you want changed "
                 "(the audience, the window or the cut), or say quote it again and I will.")

NO_LINE = ("Understood. Nothing ran and nothing was charged. Tell me what you want instead (the audience, the window "
           "or the cut) and I will take it from there.")

_PRICE_RX = re.compile(r"(?:will cost|runs? (?:it|all \w+) for|for)\s+(?:US)?[$\u00a3\u20ac]\s?\d[\d,]*(?:\.\d\d)?", re.I)
_NO_RX = re.compile(
    r"^\s*(?:(?:no+|nope|nah|not that|not this|cancel(?: it| that)?|never ?mind|stop|forget it|skip it|"
    r"that(?:'s| is) not what i asked(?: for)?|not what i asked(?: for)?|i did(?:n't| not) ask for that|wrong)\b[\s,.!-]*)+"
    r"(?:(?:thanks?|thank you|please)[\s,.!]*)?"
    r"(?:(?:just\s+)?(?:run|do|answer|give me|pull)\s+(?:what|the (?:one|thing|read|question))\s+i\s+asked(?:\s+for)?[\s.!]*)?$", re.I)
_REQUOTE_RX = re.compile(
    r"^\s*(?:ok(?:ay)?[,.!]?\s+|yes[,.!]?\s+|please\s+)*(?:re-?quote(?: it| that)?|quote (?:it|that|me|this)(?: again)?|"
    r"quote (?:it|that) again|give me the quote(?: again)?)\s*(?:please)?[\s.!]*$", re.I)


def _text(turn):
    return str((turn or {}).get('text') or (turn or {}).get('content') or '')


def _is_user(turn):
    return str((turn or {}).get('role') or '').lower() == 'user'


def is_quote_turn(turn):
    """An agent turn that quoted a price: it carries the offer, or its
    words are a price line."""
    if not isinstance(turn, dict) or _is_user(turn):
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    if isinstance(meta.get('panel_offer'), dict) or meta.get('panel_offer'):
        return True
    return bool(_PRICE_RX.search(_text(turn)))


def says_no(text):
    """True when the words only turn something down: "No", "not that",
    "cancel", "No, run what I asked". A no that goes on to ask for
    something else ("no, I want the Hulu audience") is a new ask."""
    t = ' '.join(str(text or '').split())
    return bool(t) and len(t) <= 80 and '?' not in t and bool(_NO_RX.match(t))


def _fold(text):
    try:
        from . import turn_gate as _tg
        return _tg._norm_q(_tg._asked(text))
    except Exception:
        return re.sub(r"[^a-z0-9]+", ' ', str(text or '').lower()).strip()


def _walk(q, turns):
    """The thread read in order for one question: ('quoted' | 'declined'
    | None, the quote turn). A quote stands until the reader turns it
    down in the very next turn; once turned down it stays turned down
    until a quote is drawn again because the reader asked for one."""
    state, quote, asked, prev_agent = None, None, False, None
    for t in turns:
        if _is_user(t):
            txt = _text(t)
            if _fold(txt) == q or (state == 'declined' and _REQUOTE_RX.match(' '.join(txt.split()))):
                asked = True
            elif says_no(txt):
                if state == 'quoted' and prev_agent is quote:
                    state = 'declined'
                asked = False
            else:
                asked = False
            continue
        prev_agent = t
        if not asked:
            continue
        asked = False
        if is_quote_turn(t):
            state, quote = 'quoted', t
        elif _text(t).startswith(DECLINED_LINE[:40]):
            pass                                     # the declined line itself: still turned down
        elif state != 'declined':
            state, quote = None, None                # the latest ask of it drew something else
    return state, quote


def declined(text, history):
    """The quote turn this question drew earlier in the thread that the
    reader then turned down, or None. A quote that was approved, or a
    newer quote for the same question that has not been turned down,
    means nothing stands declined."""
    try:
        q = _fold(text)
        if not q or len(q.split()) < 3:
            return None
        turns = [t for t in (history or []) if isinstance(t, dict)]
        if turns and _is_user(turns[-1]) and _fold(_text(turns[-1])) == q:
            turns = turns[:-1]                       # the turn being asked now
        state, quote = _walk(q, turns)
        return quote if state == 'declined' else None
    except Exception:
        return None


def turned_down_now(text, history):
    """True when this turn is a no said straight after a quote. The reply
    is NO_LINE: never a second quote, never a run."""
    try:
        if not says_no(text):
            return False
        turns = [t for t in (history or []) if isinstance(t, dict)]
        if turns and _is_user(turns[-1]) and _text(turns[-1]).strip() == str(text or '').strip():
            turns = turns[:-1]
        return bool(turns) and is_quote_turn(turns[-1])
    except Exception:
        return False


def wants_requote(text, history):
    """The question to quote again when the reader asks for the quote
    after DECLINED_LINE; '' otherwise."""
    try:
        if not _REQUOTE_RX.match(' '.join(str(text or '').split())):
            return ''
        turns = [t for t in (history or []) if isinstance(t, dict)]
        if turns and _is_user(turns[-1]) and _REQUOTE_RX.match(_text(turns[-1])):
            turns = turns[:-1]
        agent = next((t for t in reversed(turns) if not _is_user(t)), None)
        if not agent or not _text(agent).startswith(DECLINED_LINE[:40]):
            return ''
        i = turns.index(agent)
        prev_user = next((t for t in reversed(turns[:i]) if _is_user(t)), None)
        return _text(prev_user).strip() if prev_user else ''
    except Exception:
        return ''


def is_quote_text(reply):
    """True when a reply's words quote a price for a read."""
    return bool(_PRICE_RX.search(str(reply or '')))


def _priced_sentences(reply):
    out = []
    for part in re.split(r"(?<=[.!?])\s+", ' '.join(str(reply or '').split())):
        if _AMOUNT_RX.search(part):
            out.append(part.strip().lower())
    return out


# ------------------------------------------------ a quote sent again, judged by what happened
_AMOUNT_RX = re.compile(r"(?:US)?[$\u00a3\u20ac]\s?(\d[\d,]*(?:\.\d\d)?)")
_RUN_RX = re.compile(r"(?:^|[.!:\n]\s*)(?:on it\b|working on it\b|running (?:it|that|this) now\b|i am running\b|reading [^.]{1,120} now\b|"
                     r"your read is running\b|this read is running\b)", re.I)
_CHARGE_RX = re.compile(r"\b(?:charged [$\u00a3\u20ac]\s?\d|you were charged|has been charged|were billed|was billed|taken from your (?:wallet|balance))", re.I)
_RUN_OUTCOMES = frozenset({'read_started', 'queued', 'charged', 'panel_confirm', 'approved', 'run_started', 'new_build', 'building'})


# ------------------------------------------------ the quote that stands, handed back as it was stored
STANDS_LEAD = "This is the quote from a moment ago and it still stands. Nothing new was started and nothing was charged."


_OFFER_KEYS = ('question', 'subject', 'kind', 'frame', 'dimension', 'years', 'price_label', 'parts')


def _offer_on_record(uname, tid, history, q, ctx=None):
    """The open offer the server kept for this thread, as an offer body,
    when it is the offer for this question; else None. A client that
    sends its own turns sends the quote's words without its offer; the
    record the server keeps for a typed approval is the same offer."""
    try:
        if not uname:
            return None
        from . import pending_offers as _po
        hit = _po.find(uname, history, tid, ctx)
        if not isinstance(hit, dict):
            return None
        asked = _fold(str(hit.get('question') or ''))
        if not asked or not (asked == q or (len(asked) > 200 and q.startswith(asked))):
            return None
        body = {k: hit[k] for k in _OFFER_KEYS if hit.get(k) not in (None, '', [])}
        return body if body.get('subject') or body.get('parts') else None
    except Exception:
        return None


def standing(text, history, uname='', tid='', ctx=None):
    """The stored quote turn when this question is the one it answered,
    it carries its offer, and nothing was said since; else None. The turn
    gate hands it back as it was stored: the same words, the same price,
    the same offer behind Approve. Nothing is run to make it again, so a
    part that already ran is never started twice and a part's price
    cannot move between two sends (a three-part question sent twice
    started its free part twice and re-priced its third part)."""
    try:
        q = _fold(text)
        if not q:
            return None
        turns = [t for t in (history or []) if isinstance(t, dict)]
        if turns and _is_user(turns[-1]) and _fold(_text(turns[-1])) == q and len(turns) >= 3 and not _is_user(turns[-2]):
            turns = turns[:-1]                       # the turn being asked now
        if len(turns) < 2 or _is_user(turns[-1]) or not _is_user(turns[-2]):
            return None
        quote, ask = turns[-1], turns[-2]
        meta = quote.get('meta') if isinstance(quote.get('meta'), dict) else {}
        if not _text(quote).strip() or _fold(_text(ask)) != q:
            return None
        if not isinstance(meta.get('panel_offer'), dict):
            body = _offer_on_record(uname, tid, history, q, ctx) if is_quote_turn(quote) else None
            if not body:
                return None                          # no offer to hand back: the lanes draw the quote as before
            quote = dict(quote, meta=dict(meta, panel_offer=body))
        try:
            from . import pending_offers as _po
            if not _po._fresh(quote.get('ts')):
                return None                          # an old quote is drawn fresh; its offer has lapsed
        except Exception:
            pass
        return quote
    except Exception:
        return None


def stored_raw(quote):
    """The lane payload for a stored quote turn: its words, its offer,
    its choices. A quote in parts whose free part already ran says, in
    one line first, that nothing new was started."""
    meta = quote.get('meta') if isinstance(quote.get('meta'), dict) else {}
    po = dict(meta.get('panel_offer') or {})
    words = _text(quote).strip()
    if _RUN_RX.search(words) and not words.startswith(STANDS_LEAD):
        words = STANDS_LEAD + '\n\n' + words
    chips = []
    for x in (meta.get('options') or []):            # a stored turn keeps its choices as {'label', 'send'}; a client may send plain words
        label = str((x.get('send') or x.get('label') or '') if isinstance(x, dict) else (x or '')).strip()
        if label and label not in chips:
            chips.append(label)
    try:
        from .priced_read import APPROVE_CHIP, CANCEL_CHIP
    except Exception:
        APPROVE_CHIP, CANCEL_CHIP = 'Approve', 'Cancel'
    if not any(c.lower().startswith('approve') for c in chips):
        chips = [APPROVE_CHIP, CANCEL_CHIP]          # a quote always carries its two choices
    return {'success': True, 'action': 'answer', 'reply': words, 'followups': chips, 'offer_deck': False, 'deck_angle': None,
            'panel_offer': po, 'subject': str(po.get('subject') or meta.get('subject') or ''), 'quote_stands': True}


def quote_stands(first_reply, second_reply, second_outcome='', first_jobs=(), second_jobs=()):
    """(ok, why) for a quoted question sent again before the reader
    answered the quote. The rule is about what happened, not which lane
    logged it: the same quote at the same price, no new run, no second
    quote stacked on it, no charge."""
    a, b = str(first_reply or ''), str(second_reply or '')
    if not is_quote_text(a):
        return False, 'the first reply was not a quote'
    if _CHARGE_RX.search(b) or str(second_outcome or '') in ('charged',):
        return False, 'the second send reads as a charge'
    b = b[len(STANDS_LEAD):].strip() if b.startswith(STANDS_LEAD) else b
    if [j for j in (second_jobs or ()) if j and j not in set(first_jobs or ())]:
        return False, 'the second send started a new run'
    runs_a = [m.group(0).strip(' .!').lower() for m in _RUN_RX.finditer(a)]
    runs_b = [m.group(0).strip(' .!').lower() for m in _RUN_RX.finditer(b)]
    if len(runs_b) > len(runs_a) or str(second_outcome or '') in _RUN_OUTCOMES:
        return False, 'the second send started a run with no approval'
    if not is_quote_text(b):
        return False, 'the second send dropped the quote'
    pa, pb = _AMOUNT_RX.findall(a), _AMOUNT_RX.findall(b)
    if sorted(pa) != sorted(pb):
        if len(pb) > len(pa):
            return False, f"a second quote was stacked on the first ({', '.join('$' + x for x in pb)} against {', '.join('$' + x for x in pa)})"
        return False, f"the price changed ({', '.join('$' + x for x in pa)} became {', '.join('$' + x for x in pb)})"
    sa, sb = _priced_sentences(a), _priced_sentences(b)
    if len(sb) > len(sa):
        return False, 'a second quote was stacked on the first'
    return True, ''
