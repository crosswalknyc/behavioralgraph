"""A dataset on an audience is a Profile IQ build (Jenna 2026-10-26,
Emmet Riley at Kartel).

Emmet: "Can you help me query a dataset on UVA consumers for the
unversity of virgina https://www.virginia.edu/". The words name an
audience and the site that identifies it; that IS a profile pull. It
ran as a question about an audience with no base and drew "I can pull
that read on UVA consumers for the unversity of virgina. Pulling this
data will cost $100.", the custom-read quote.

``parse`` reads the order: a fetch verb (query, pull, find, get, build,
need, want, looking for, help me ...), a data noun (dataset, data set,
data) and the audience after on / for / about. The URL in the words is
the brand input (prometheus.links carries it onto the draft as the
clickstream signal). A question about data we already hold ("how much
data do you have on X", "what is the Fox News dataset pulled from") is
not an order and parses to None: those stay with the catalog, status
and provenance lanes.

Pure text; no model, no network.
"""
from __future__ import annotations

import re

__all__ = ['parse', 'is_build_ask']

_URL_RX = re.compile(r"(?:https?://|www\.)[^\s<>()\"']+", re.I)
_DATA_NOUN = r"(?:data\s*sets?|datasets?|audience\s+data|data\s+files?|data\s+pulls?|data)"
_VERB = (r"(?:query|querry|pull|find|get|build|create|source|assemble|put\s+together|run|need|want|"
         r"(?:am|'m|i'm|we're|are)\s+(?:looking|trying\s+to\s+find|trying\s+to\s+get|trying\s+to\s+pull)|"
         r"looking|look)")
_ORDER_RX = re.compile(
    r"^\W*(?:(?:hey|hi|hello|ok(?:ay)?|so)[,.!\s]+)?"
    r"(?:(?:can|could|would|will)\s+(?:you|we|u)\s+(?:please\s+)?(?:help\s+(?:me|us)\s+(?:to\s+)?)?|"
    r"(?:please\s+)?help\s+(?:me|us)\s+(?:to\s+)?|i(?:'d| would)?\s+(?:like|need|want)\s+(?:you\s+)?to\s+|"
    r"(?:i|we)\s+(?:need|want|would\s+like)\s+(?:to\s+)?|(?:i|we)\s+|please\s+)?"
    r"(?P<verb>" + _VERB + r")\s+(?:for\s+)?(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|some\s+|any\s+)?(?:new\s+|fresh\s+|full\s+)?"
    r"(?P<noun>" + _DATA_NOUN + r")\s+(?:on|for|of|about|around|covering)\s+"
    r"(?P<subject>[^?\n]{2,160}?)\s*[.!?]*\s*$", re.I)
# a question about data we hold is never an order
_LOOKUP_LEAD_RX = re.compile(
    r"^\W*(?:how\s+(?:much|many|big|old|recent|fresh)|what|which|where|when|who|why|do\s+you\s+(?:have|see|hold|carry)|"
    r"does|is\s+there|are\s+there|did\s+you|have\s+you|has\s+the)\b", re.I)
_TRAIL_RX = re.compile(r"\s*(?:,|\band\b)?\s*(?:please|thanks?|thank\s+you|for\s+me|for\s+us|asap|today|now)\s*$", re.I)
_POINTERS = frozenset(('this', 'that', 'it', 'them', 'these', 'those', 'this one', 'the same', 'the above'))
# a metric over time or a split by a dimension is a read, never a build
_READ_SHAPE_RX = re.compile(
    r"\b(?:by|per)\s+(?:month|week|day|quarter|year|device|platform|surface|daypart|dma|state|age|gender|demographic)s?\b|"
    r"\bover\s+time\b|\bmonth\s+by\s+month\b|\bmonthly\b|\bweekly\b|\bquarterly\b|\byear\s+over\s+year\b|"
    r"\bhours\s+watched\b|\bminutes\s+watched\b|\bviewership\b|\bconsumption\b", re.I)


def parse(text):
    """{'subject', 'links': [...], 'brand_input_hint': url | ''} for an
    order to query / pull / find a dataset on an audience, else None."""
    raw = ' '.join(str(text or '').split())
    if not raw or len(raw) > 400:
        return None
    if _LOOKUP_LEAD_RX.match(raw) or _READ_SHAPE_RX.search(raw):
        return None
    try:
        from . import links as _links
        found = _links.extract(raw)
    except Exception:
        found = []
    bare = _URL_RX.sub(' ', raw)
    bare = ' '.join(bare.split()).strip(' ,;:-')
    m = _ORDER_RX.match(bare)
    if not m:
        return None
    subj = m.group('subject').strip(" .,;:'\"")
    subj = _TRAIL_RX.sub('', subj).strip(" .,;:'\"")
    subj = re.sub(r"^(?:the|a|an)\s+", '', subj, flags=re.I)
    if not subj or subj.lower() in _POINTERS or len(subj) < 2:
        return None
    # "data on people who ..." names a behavior audience; a bare pronoun or
    # a question clause ("data on what you have") is not an audience
    if re.match(r"^(?:what|which|how|who|whom|why|where)\b", subj, re.I):
        return None
    hint = ''
    for l in found:
        if l.get('kind') == 'website':
            hint = l.get('url') or ''
            break
    if not hint and found:
        hint = found[0].get('url') or ''
    return {'subject': subj, 'links': found, 'brand_input_hint': hint, 'noun': m.group('noun').lower()}


def is_build_ask(text):
    return parse(text) is not None
