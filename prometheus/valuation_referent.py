"""A delivered Brand Partnership valuation is what the next words about
"that campaign", "the deck", "this" or its dates refer to (Jenna
2026-11-20, Samantha Kolton's thread, rebuild stage 2 follow-up).

Right after Prometheus handed over the Anne Hathaway x Shiseido
valuation, four asks about it missed:

  * "I want the period of 8/27/26 to 9/8/26 - i had given the wrong
    dates for that campaign" drew "Which campaign is this about? Open it
    in the Attribution IQ tab". A date correction right after a delivered
    valuation names that valuation; never ask which campaign, never
    route a Brand Partnership valuation to Attribution IQ.
  * "Rebuild the deck for the Aug 27 to Sep 8 window" treated the window
    as a subject: "I do not have a profile or a finished read on Aug 27
    to Sep 8 window".
  * "can you rerun this and break the campaign into the 4 parts?" drew a
    promise ("Working on it ... I will email you when it completes") with
    nothing running.
  * "Can you just send a refreshed deck for the brand valuation for the
    correct dates of 8/27/26 to 9/6/26" started a generic deck instead
    of the valuation on the new window.

One reading, in the Understanding: when the thread holds a delivered
valuation (the note that handed it over, prometheus.bpiq_deck) and the
words point at it (its pairing, the word valuation, "that campaign",
"this", a deck of it, a re-run of it, or a correction of its dates), the
ask is about that valuation. The window the words carry is compared with
the delivered one:

    same window, a deck or its parts   -> the valuation deck again, from the saved file
    same window, the figures           -> the delivered total, restated from the thread
    a different window                 -> the order opens at its confirm card with the pairing
                                          and the new dates filled in (Accept runs it; the
                                          wizard answers "already exists" when it does)

Nothing here starts work on its own, charges, mails or writes a thread;
the wizard's own store holds the opened order the way it holds any
order. Keyed on the Understanding (`understanding.flipped('valuation_referent')`),
kill switch PM_U_OFF_VALUATION_REFERENT=1. Pure text plus the thread;
never raises.
"""
from __future__ import annotations

import re
import traceback

FLIP = 'valuation_referent'

_DECK_RX = re.compile(r"\b(?:decks?|slides?|slide\s+deck|one[- ]pagers?|pptx|powerpoints?|presentations?)\b", re.I)
_PARTS_RX = re.compile(r"\b(?:break(?:\s+\w+){0,3}\s+(?:into|down)|broken\s+(?:into|down)|the\s+(?:4|four)\s+parts|four\s+parts|"
                       r"by\s+(?:component|lens|part)|each\s+(?:component|lens|part)|components?|the\s+parts)\b", re.I)
_RERUN_RX = re.compile(r"\b(?:re-?run|run\s+(?:it|this|that)\s+again|redo|re-?do|again|refresh(?:ed)?|rebuild|re-?build|update[sd]?|"
                       r"new\s+dates|correct(?:ed)?\s+dates|right\s+dates|wrong\s+dates|different\s+dates|the\s+period\s+(?:of|is|should)|"
                       r"the\s+dates?\s+(?:are|is|should|were|was)|i\s+(?:had\s+)?(?:gave|given|entered|typed|put)\s+the\s+wrong)\b", re.I)
_REFERENT_RX = re.compile(r"\b(?:that|this|the)\s+(?:campaign|valuation|partnership|read|one|deck|brand\s+valuation)\b|\b(?:this|that|it)\b|"
                          r"\bthe\s+(?:brand\s+)?valuation\b|\bvaluation\b", re.I)
_VALUE_WORDS_RX = re.compile(r"\b(?:what(?:'s| is| was)\s+(?:the\s+)?(?:value|total|figure|number)|how\s+much|the\s+(?:value|total|figures?)|"
                             r"show\s+me|resend|send\s+(?:me\s+)?(?:it|that|the\s+(?:figures?|numbers?|total))|the\s+period\s+of)\b", re.I)
_CAMPAIGN_LINE_RX = re.compile(r"(?:campaign\s+dates?:|i\s+read\s+the\s+campaign\s+as)\s*(?P<win>[^\n.]+)", re.I)
_RANGE_TOKEN_RX = re.compile(
    r"(?:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,?\s*20\d{2})?\s*(?:-|to|through|until|thru)\s*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?\d{1,2}(?:,?\s*20\d{2})?)|"
    r"(?:\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*(?:-|to|through|until|thru)\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?)|"
    r"(?:\b20\d{2}-\d{2}-\d{2}\s*(?:-|to|through)\s*20\d{2}-\d{2}-\d{2})", re.I)
# words that say what is wanted or describe the window, never a subject
_FILLER_RX = re.compile(r"\b(?:please|just|can|could|would|you|me|us|i|we|want|need|like|the|a|an|for|of|on|to|and|or|with|in|at|by|"
                        r"window|period|dates?|campaign|valuation|brand|partnership|deck|slides?|one[- ]pager|pptx|refreshed|refresh|rebuild|"
                        r"re-?run|rerun|run|redo|again|send|resend|give|get|show|build|make|create|correct|corrected|right|wrong|new|different|"
                        r"had|have|has|given|gave|entered|typed|put|that|this|it|those|these|is|are|was|were|be|should|into|parts?|four|"
                        r"break|broken|down|each|component|components|lens|lenses|total|value|figures?|numbers?|how|much|what|its|their|"
                        r"same|one|only|also|too|so|up|out|full|whole|entire|version|copy|file|pdf|email|mail|time|earlier|above|previous|last)\b", re.I)


def enabled():
    try:
        from . import understanding as _und
        return _und.flipped(FLIP)
    except Exception:
        return False


def _meta(turn):
    m = turn.get('meta') if isinstance(turn, dict) and isinstance(turn.get('meta'), dict) else {}
    return m


def _text(turn):
    return str((turn or {}).get('text') or (turn or {}).get('content') or '')


def _fold(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


def _parse_dates(text):
    try:
        from . import bpiq_wizard as _w
        a, b = _w.parse_dates(text)
        return (a, b) if (a and b) else (None, None)
    except Exception:
        return (None, None)


def delivered(history):
    """The valuation this thread handed over, or None:
    {'job_id', 'subject', 's3_key', 'talent', 'brand', 'start', 'end',
    'total', 'index'}. The window is read off the order's own lines in
    the thread ("Campaign dates: ...", "I read the campaign as ...") when
    they are there; the total off the note that handed it over."""
    try:
        from . import bpiq_deck as _bd
        i, turn = _bd.valuation_turn(history)
        if turn is None:
            return None
        m = _meta(turn)
        subject = ' '.join(str(m.get('subject') or '').split())
        note = _text(turn)
        if not subject:
            mm = re.search(r"valuation is ready:\s*(?P<s>[^,\n]{2,120}?),\s*total brand value", note, re.I)
            subject = ' '.join(mm.group('s').split()) if mm else ''
        talent = brand = ''
        try:
            from . import deck_pick as _dp
            pr = _dp.pair_parts(subject)
            if pr:
                talent, brand = pr
        except Exception:
            pass
        if not talent:
            pr = re.split(r"\s+[xX\u00d7]\s+", subject, maxsplit=1)
            if len(pr) == 2:
                talent, brand = pr[0].strip(), pr[1].strip()
        start = end = None
        hist = list(history or [])
        for t in reversed(hist[:i]):
            mm = _CAMPAIGN_LINE_RX.search(_text(t))
            if mm:
                start, end = _parse_dates(mm.group('win'))
                if start and end:
                    break
        total = ''
        mt = re.search(r"total brand value\s+(\$[\d,]+(?:\.\d{2})?)", note, re.I)
        if mt:
            total = mt.group(1)
        return {'job_id': str(m.get('bpiq_job_id') or ''), 'subject': subject, 's3_key': str(m.get('s3_key') or ''),
                'talent': talent, 'brand': brand, 'start': start, 'end': end, 'total': total, 'index': i}
    except Exception:
        traceback.print_exc()
        return None


def _names_other(text, held):
    """True when the words, with the window, the verbs and the pairing
    taken out, still name something else."""
    t = _RANGE_TOKEN_RX.sub(' ', str(text or ''))
    t = re.sub(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b20\d{2}\b|\b\d{1,2}\b", ' ', t)
    for n in (held.get('subject'), held.get('talent'), held.get('brand')):
        for w in re.findall(r"[A-Za-z0-9][\w&'.\-]*", str(n or '')):
            t = re.sub(r"\b" + re.escape(w) + r"\b", ' ', t, flags=re.I)
    t = _FILLER_RX.sub(' ', t)
    rest = [w for w in re.findall(r"[A-Za-z][\w&'.\-]*", t) if len(_fold(w)) >= 3]
    return bool(rest)


def _later_delivery(history, index):
    """A read, a deck or another valuation landed in the thread after turn `index`."""
    try:
        for t in list(history or [])[int(index) + 1:]:
            if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
                continue
            m = _meta(t)
            if m.get('read_job_id') or m.get('deck_job_id') or m.get('kind') in ('read', 'deck', 'bpiq') or m.get('landed_read_id'):
                return True
    except Exception:
        return False
    return False


def _last_turn_on_pair(history, held):
    """The thread's last agent turn names the talent or the brand."""
    try:
        for t in reversed(list(history or [])):
            if isinstance(t, dict) and str(t.get('role') or '') != 'user':
                low = _fold(_text(t))
                return any(_fold(x) and _fold(x) in low for x in (held.get('talent'), held.get('brand'), held.get('subject')))
    except Exception:
        return False
    return False


def kind_of(text):
    """'deck', 'rerun', 'restate' or ''."""
    t = ' '.join(str(text or '').split())
    if not t:
        return ''
    if _DECK_RX.search(t) or _PARTS_RX.search(t):
        return 'deck'
    if _RERUN_RX.search(t):
        return 'rerun'
    if _VALUE_WORDS_RX.search(t):
        return 'restate'
    return ''


def read(text, history):
    """The valuation the words are about, or None: the delivered record
    plus 'kind' ('deck' | 'rerun' | 'restate'), 'window' ((start, end) or
    None) and 'same_window' (True when the words carry no window or the
    delivered one)."""
    try:
        t = ' '.join(str(text or '').split())
        if not t or len(t) > 400:
            return None
        held = delivered(history)
        if not held or not held.get('subject'):
            return None
        kind = kind_of(t)
        if not kind:
            return None
        names_pair = all(_fold(x) and _fold(x) in _fold(t) for x in (held['talent'], held['brand']) if x) and bool(held['talent'])
        points = bool(_REFERENT_RX.search(t)) or bool(_RANGE_TOKEN_RX.search(t))
        if not (names_pair or points):
            return None
        if _names_other(t, held):
            return None
        start, end = _parse_dates(t)
        window = (start, end) if (start and end) else None
        # a read or a deck landed after the valuation and the words name neither the pairing, the word valuation nor
        # a window: they point at it only when the thread's last turn is still about this pairing
        if not (names_pair or re.search(r"\bvaluations?\b", t, re.I) or window) and _later_delivery(history, held.get('index')):
            if not _last_turn_on_pair(history, held):
                return None
        same = window is None or (held.get('start') and held.get('end') and (start, end) == (held['start'], held['end']))
        return dict(held, kind=kind, window=window, same_window=bool(same))
    except Exception:
        traceback.print_exc()
        return None


def window_label(start, end):
    try:
        from . import bpiq_wizard as _w
        return _w.pretty_range(start, end)
    except Exception:
        return f"{start} to {end}"


# ------------------------------------------------------------ the answer
def restate_payload(hit):
    """The delivered figure, said again, for the window the words asked
    for when it is the delivered one."""
    subject = hit.get('subject') or 'the valuation'
    win = window_label(hit['start'], hit['end']) if hit.get('start') and hit.get('end') else ''
    head = f"That is the valuation delivered above: {subject}" + (f", {win}" if win else '') + '.'
    if hit.get('total'):
        head += f" Total brand value {hit['total']}."
    head += " It is in the Brand Partnership tab. Say deck and I will send the deck again, or give me new dates and I will run it on those."
    return {'success': True, 'action': 'answer', 'reply': head, 'followups': ['Send the deck', 'New dates'],
            'offer_deck': False, 'deck_angle': None, 'valuation_referent': {'job_id': hit.get('job_id'), 'kind': 'restate'}}


def rerun_payload(hit, start, end, *, user, uname, thread_id, body, store=None):
    """The order at its confirm card, the pairing and the new dates
    filled in. Accept runs it through the wizard as any order."""
    from . import bpiq_wizard as _w
    d = _w.empty_draft()
    d['talent'] = hit.get('talent') or hit.get('subject') or ''
    if hit.get('brand'):
        d['brand_partner'] = hit['brand']
        d['brand_named'] = True
        d['campaign'] = hit['brand']
    d['campaign_start'], d['campaign_end'] = start, end
    d['attr_start'], d['attr_end'] = _w.attribution_window(start, end)
    d['attr_ok'] = False
    try:
        _w.mark_window(d, f"{start} to {end}")
    except Exception:
        pass
    d = _w._price_draft(d)
    d['step'] = _w.STEP_ATTR
    d['from_valuation'] = hit.get('job_id') or ''
    reply, chips = _w.step_prompt(d)
    lead = f"New dates for {hit.get('subject') or 'the valuation'}: {window_label(start, end)}. "
    try:
        if store is None:
            store = _w.store_for(body)
        if store is not None:
            store.save(uname or user, thread_id or '', d)
    except Exception:
        traceback.print_exc()
    out = _w._payload(lead + reply, chips, draft=d)
    out['valuation_referent'] = {'job_id': hit.get('job_id'), 'kind': 'rerun', 'window': [start, end]}
    return out


def answer(text, history, *, user, uname, thread_id, body, host=None, g=None, store=None):
    """The payload for an ask about the thread's delivered valuation, or
    None when the Understanding did not read the words as being about it
    (or the flip is off). A deck of it on its own window comes back as
    {'hand_to_deck': True, 'subject', 'job_id'}: the caller runs the deck
    surface with the valuation picked, which rebuilds the saved file's
    deck through the one valuation deck path."""
    if not enabled():
        return None
    try:
        from . import understanding as _und
        u = _und.on_body(body)
        if u is None or u.referent != 'last_valuation':
            return None
        hit = read(text, history)
        if not hit:
            return None
        try:
            _und.mark_keyed(body)
        except Exception:
            pass
        if hit['kind'] == 'deck' and hit['same_window']:
            return {'hand_to_deck': True, 'subject': hit['subject'], 'job_id': hit.get('job_id'), 'kind': 'deck'}
        if hit['same_window'] or not hit.get('window'):
            # the figures asked for are the delivered ones ("the period of <the same dates>", "rerun this")
            if hit['kind'] == 'rerun' and not hit.get('window'):
                return rerun_payload(hit, hit['start'], hit['end'], user=user, uname=uname, thread_id=thread_id, body=body, store=store) \
                    if (hit.get('start') and hit.get('end')) else None
            return restate_payload(hit)
        start, end = hit['window']
        return rerun_payload(hit, start, end, user=user, uname=uname, thread_id=thread_id, body=body, store=store)
    except Exception:
        traceback.print_exc()
        return None


__all__ = ['FLIP', 'enabled', 'delivered', 'kind_of', 'read', 'answer', 'restate_payload', 'rerun_payload']
