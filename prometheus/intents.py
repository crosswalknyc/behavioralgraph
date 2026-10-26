"""Product-intent gates for the analyze surface (extracted from the legacy
chat module 2026-10-14). Each answers "is this ask an order for that
product?" from the words alone; the Reader's reading, when present and
confident, answers the same question without the words (`read_intent`).

  bpiq_intent   Brand Partnership valuation
  jiq_intent    Digital Journey
  aiq_intent    Attribution IQ campaign tracking
  fw_intent     Flywheel
  twin_intent   Digital Twins IQ (build, lab, panel, catalog, status, directions)
"""
from __future__ import annotations

import re

_BUILD_VERBS = ('pull', 'build', 'run', 'create', 'new', 'make')


def _low(text):
    return ' '.join(str(text or '').lower().split())


def read_intent(body, intent, min_conf=0.7):
    """The Reader read this product intent with confidence (2026-10-14:
    'Value the Stanley x Target partnership' read valuation at 0.95 and
    still drafted a profile; the keyword gate wanted 'brand partnership')."""
    r = (body or {}).get('reading') if isinstance(body, dict) else None
    try:
        return isinstance(r, dict) and str(r.get('intent') or '') == intent and float(r.get('confidence') or 0) >= min_conf
    except Exception:
        return False


_PARTNER_FIT_RX = re.compile(
    r"\b(?:partner(?:ing|ed|s)?\s+with|partnership\s+(?:with|between)|sponsor(?:ing|ed|ship)?\s+(?:deal\s+)?(?:with|of)|"
    r"collab(?:oration|orate|orating)?\s+with|team(?:ing|ed)?\s+up\s+with|tie[- ]?up\s+with|"
    r"(?:good|great|strong|right|natural|smart|perfect|bad|poor|wrong)\s+(?:fit|match|partner|pairing)\s+(?:with|for))", re.I)
_FIT_WHY_RX = re.compile(
    r"\b(?:why|whether|should|makes?\s+sense|good\s+(?:idea|fit|match|partner)|right\s+(?:fit|partner|creator)|worth|value|case\s+for|"
    r"the\s+case|rationale|justif|over\s+(?:other|potential|the\s+other)|versus|vs\.?|compared\s+to|fit)\b", re.I)


def bpiq_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    if 'brand partnership' in low and any(k in low for k in ('valuation', 'value', 'pull', 'run one', 'measure')):
        return True
    # 'value the Stanley x Target partnership', 'can you value Gunna x Nike' (2026-10-14)
    if (re.search(r"\b(value|valuation|valuing|worth)\b", low) is not None
            and any(k in low for k in (' x ', 'partnership', 'sponsorship', 'collab', 'deal', 'campaign'))):
        return True
    # the partnership case: 'why it makes sense for Mastercard to partner with Mr. Beast over potential
    # other creators', 'Mastercard is partnering with Mr. Beast, I want to understand why this is a good
    # idea' (Debi 2026-10-26, asked on the Colin and Samir read and answered from it twice)
    if _PARTNER_FIT_RX.search(low) and _FIT_WHY_RX.search(low):
        return True
    # a pasted deal brief: two names around 'have a Lexus deal' plus the
    # campaign's own words (promo, ads, sponsored, launched, flight). Debi's
    # first message on 2026-10-21 was this and drew the open journey's line
    return bool(deal_brief(text))


_BRIEF_WORDS_RX = re.compile(
    r"\b(?:campaign|promo(?:s|tion)?|ads?|spots?|pre-?roll|mid-?roll|sponsored|sponsorship|launch(?:ed)?|flight|integration|activation|creative|videos?|episodes?)\b", re.I)


def deal_brief(text):
    """True when the words describe a brand x talent deal as a brief: the
    'X have a Y deal' shape with two names and at least two of the
    campaign's own words. Lazy import keeps intake_reader optional."""
    t = str(text or '')
    if len(t) < 40:
        return False
    try:
        from . import intake_reader as _ir
    except Exception:
        return False
    if not (_ir._BP_DEAL.search(t) or _ir._BP_SIGNED.search(t)):
        return False
    return len(_BRIEF_WORDS_RX.findall(t)) >= 2


def bpiq_reading(body, min_conf=0.6):
    """The Reader read a partnership valuation with a brand and a partner
    named: enough to open the intake even under the usual bar (Debi's
    Mastercard x MrBeast read valuation at 0.62 and went to the page)."""
    r = (body or {}).get('reading') if isinstance(body, dict) else None
    try:
        if not isinstance(r, dict) or str(r.get('intent') or '') != 'valuation' or float(r.get('confidence') or 0) < min_conf:
            return False
        kinds = [str(s.get('kind') or '') for s in (r.get('subjects') or []) if isinstance(s, dict)]
        return 'brand' in kinds and len(kinds) >= 2
    except Exception:
        return False


def jiq_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    # "Build a journey for Suits on Peacock" is the same order without the word digital (2026-10-13)
    if ('digital journey' in low or re.search(r"\b(?:a|the|me a|another|new)\s+journey\b", low) is not None) \
            and any(k in low for k in _BUILD_VERBS + ('map',)):
        return True
    # 'journey for Young Sheldon on Netflix, start from ..., end at ...' (2026-10-14)
    if re.match(r"^\W*(?:digital )?journey (?:for|on|of)\b", low):
        return True
    # the after-watch read (Jenna 2026-10-19): 'a viewership journey for NCIS viewers after they watch',
    # 'a week in the life of a Survivor viewer', 'what do Sistas watchers do in addition to watching'
    if re.search(r"\b(?:viewer|viewership|watcher|audience|fan)s?\b[^.?!]{0,40}\bjourney\b|\bjourney\b[^.?!]{0,60}\b(?:viewer|viewership|watcher|audience|fan)s?\b", low):
        return True
    if re.search(r"\b(?:day|week) in the (?:media |digital )?life of\b", low):
        return True
    return 'journey' in low and any(k in low for k in ('start from', 'end at', 'ends at', 'conversion'))


def aiq_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    if 'attribution' in low and any(k in low for k in ('set up', 'setup', 'track', 'pull', 'build', 'start', 'create')):
        return True
    # assets joining a campaign: 'add these assets to the attribution iq report for the influencer project' (Alexia 2026-10-16)
    if re.search(r"\b(add|append|include|attach|put|load)\b", low) and re.search(r"\b(assets?|urls?|links?|videos?|posts?|clips?)\b", low) \
            and re.search(r"\b(attribution|campaign|mta|multi.?touch)\b", low):
        return True
    # the filled-in form: 'Campaign: Back to School. Conversion is a checkout on Amazon. ... Assets: https://...' (2026-10-14)
    return bool(re.match(r"^\W*campaign\s*:", low)) or ('assets' in low and 'conversion' in low and 'http' in low)


def fw_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    return 'flywheel' in low and any(k in low for k in _BUILD_VERBS)


def twin_intent(text, chip=''):
    """A Digital Twins IQ ask: build a twin, run a lab or a room on one,
    the twin catalog or status, or the directions (2026-10-26)."""
    try:
        from . import digital_twin_lane as _tl
        return bool(_tl.twin_intent(text, chip))
    except Exception:
        return False


def twin_reading(body, min_conf=0.7):
    return read_intent(body, 'twin', min_conf)
