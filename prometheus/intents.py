"""Product-intent gates for the analyze surface (extracted from the legacy
chat module 2026-10-14). Each answers "is this ask an order for that
product?" from the words alone; the Reader's reading, when present and
confident, answers the same question without the words (`read_intent`).

  bpiq_intent   Brand Partnership valuation
  jiq_intent    Digital Journey
  aiq_intent    Attribution IQ campaign tracking
  fw_intent     Flywheel
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


def bpiq_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    if 'brand partnership' in low and any(k in low for k in ('valuation', 'value', 'pull', 'run one', 'measure')):
        return True
    # 'value the Stanley x Target partnership', 'can you value Gunna x Nike' (2026-10-14)
    return (re.search(r"\b(value|valuation|valuing|worth)\b", low) is not None
            and any(k in low for k in (' x ', 'partnership', 'sponsorship', 'collab', 'deal', 'campaign')))


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
    return 'journey' in low and any(k in low for k in ('start from', 'end at', 'ends at', 'conversion'))


def aiq_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    if 'attribution' in low and any(k in low for k in ('set up', 'setup', 'track', 'pull', 'build', 'start', 'create')):
        return True
    # the filled-in form: 'Campaign: Back to School. Conversion is a checkout on Amazon. ... Assets: https://...' (2026-10-14)
    return bool(re.match(r"^\W*campaign\s*:", low)) or ('assets' in low and 'conversion' in low and 'http' in low)


def fw_intent(text, chip=''):
    low = _low(text)
    if chip and low == chip.lower():
        return True
    return 'flywheel' in low and any(k in low for k in _BUILD_VERBS)
