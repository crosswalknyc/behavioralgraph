"""An armed wizard step yields to a clearly unrelated question and takes
a plain correction (Jenna 2026-11-20, decision on the rebuild report:
"wizard_override = INTERRUPT").

Two halves, both keyed on the Understanding and both behind one switch,
PM_U_OFF_WIZARD_INTERRUPT=1:

  * `unrelated(body, text, draft)`: while a Brand Partnership step waits,
    a message the Understanding reads with high confidence as something
    else (the reader's own order or delivery, a feature of the dashboard,
    a file to send, an insight to find, the dashboard misbehaving, a
    price, a library lookup, a definition) is answered as that; the
    wizard stands aside, its draft stays at its step, the reply carries
    the way back, and the next on-topic reply resumes it. A message that
    answers the step (a chip, a name, dates, an accept, a cancel) or
    names a slot is never unrelated. Before this only a question-shaped
    message escaped; "where is my Nurses file" typed at the dates step
    was read as the dates.
  * `correction(text, draft)`: a plain correction to the waiting step
    replaces that step's value instead of being ignored. At the confirm
    card ("I read the campaign as Feb 4 to 6, 2025 ...") and every step
    after it, "the campaign dates are Sep 1 to 8, 2026", "I gave the
    wrong dates, it ran 8/27 to 9/8", "the correct dates are ..." replace
    the campaign dates, the attribution window is recomputed and the
    card is shown again. A bare range typed at the confirm card still
    sets the attribution window, as it always did.

Pure text plus the draft. Never raises. Nothing here charges, mails or
writes a thread.
"""
from __future__ import annotations

import re

from .understanding import WIZARD_YIELDS_TO as UNRELATED_INTENTS, WIZARD_YIELD_CONF as MIN_CONF   # one list, in the Understanding

FLIP = 'wizard_interrupt'

_CAMPAIGN_DATES_RX = re.compile(
    r"\b(?:campaign|flight|activation|partnership|it|this|that|the\s+dates?|the\s+period|the\s+window)\s+(?:dates?\s+)?"
    r"(?:is|are|was|were|ran|runs|should\s+be|needs?\s+to\s+be|went|goes)\b|"
    r"\b(?:wrong|incorrect|correct|right|actual|real|new|different)\s+(?:campaign\s+)?(?:dates?|period|window)\b|"
    r"\bi\s+(?:had\s+)?(?:gave|given|entered|typed|put|sent)\s+(?:you\s+)?the\s+wrong\b|\bi\s+meant\b|\bactually\b|\bshould\s+(?:be|have\s+been)\b|"
    r"\bnot\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|\bthe\s+period\s+of\b|\bthe\s+dates?\s+(?:of\s+)?the\s+campaign\b", re.I)


def enabled():
    try:
        from . import understanding as _und
        return _und.flipped(FLIP)
    except Exception:
        return False


def _understanding(body):
    try:
        from . import understanding as _und
        return _und.on_body(body)
    except Exception:
        return None


def unrelated(body, text, draft, today=None):
    """True when the message should be answered on its own while the
    wizard's draft waits."""
    if not enabled():
        return False
    try:
        u = _understanding(body)
        if u is None or u.intent not in UNRELATED_INTENTS or float(u.confidence or 0) < MIN_CONF:
            return False
        from . import bpiq_wizard as _w
        t = ' '.join(str(text or '').split())
        if not t:
            return False
        step = (draft or {}).get('step') or _w.next_step(draft or _w.empty_draft())
        # anything that answers the step, names a slot, or carries dates belongs to the step
        if _w.is_chip(t) or _w.is_cancel(t) or _w.is_decline(t) or _w._is_resume(t) or _w._bpiq_intent(t):
            return False
        if _w.names_slots(t, step, today=today) or _w.parse_dates(t, today=today)[0]:
            return False
        # the campaign links, or the word that they are coming, belong to the asset step wherever the walk is
        # (Emi Hill 2026-11-24: 53 pasted URLs at the card are the asset list, never an unrelated message)
        if _w.extract_urls(t) or _w.links_switch(t):
            return False
        # a statement shaped like the step's answer belongs to the step; an ask (a question, an order for something
        # else) never does, however the step's own shape check reads it
        try:
            from . import intake_guard as _ig
            is_ask = bool(_ig.is_ask(t))
        except Exception:
            is_ask = False
        if not is_ask and _w._shaped_answer(t, draft or _w.empty_draft(), today=today):
            return False
        try:
            from . import understanding as _und
            _und.mark_keyed(body)   # the ask log reads the stand-aside as a decision keyed on the Understanding
        except Exception:
            pass
        return True
    except Exception:
        return False


def correction(text, draft, today=None):
    """{'campaign_start', 'campaign_end'} when the message corrects the
    campaign dates of a step that is past the dates step; None otherwise."""
    if not enabled():
        return None
    try:
        from . import bpiq_wizard as _w
        d = dict(draft or {})
        step = d.get('step') or ''
        if step in ('', _w.STEP_TALENT, _w.STEP_CAMPAIGN, _w.STEP_DATES):
            return None
        t = ' '.join(str(text or '').split())
        if not t or '?' in t or not _CAMPAIGN_DATES_RX.search(t):
            return None
        a, b = _w.parse_dates(t, today=today)
        if not (a and b):
            return None
        if step == _w.STEP_ATTR and d.get('campaign_start') and (str(a)[:10], str(b)[:10]) == (str(d.get('campaign_start'))[:10], str(d.get('campaign_end'))[:10]):
            return None   # the same dates again: the card's own accept path reads it
        return {'campaign_start': str(a)[:10], 'campaign_end': str(b)[:10]}
    except Exception:
        return None


def apply_correction(draft, corr, text='', today=None):
    """The draft with the corrected campaign dates, its attribution
    window recomputed, back at the confirm card."""
    from . import bpiq_wizard as _w
    d = dict(draft or _w.empty_draft())
    d['campaign_start'], d['campaign_end'] = corr['campaign_start'], corr['campaign_end']
    d['attr_start'], d['attr_end'] = _w.attribution_window(d['campaign_start'], d['campaign_end'], today=today)
    d['attr_ok'] = False
    d.pop('dates_inferred', None)
    d.pop('pinned_read', None)
    d.pop('given_read', None)
    try:
        _w.mark_window(d, text or f"{d['campaign_start']} to {d['campaign_end']}")
    except Exception:
        pass
    d['step'] = _w.STEP_ATTR
    d['corrected_dates'] = True
    return _w._price_draft(d)


__all__ = ['FLIP', 'MIN_CONF', 'UNRELATED_INTENTS', 'enabled', 'unrelated', 'correction', 'apply_correction']
