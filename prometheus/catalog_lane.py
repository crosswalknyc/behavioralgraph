"""Deterministic catalog lane (2026-10-06, Jenna: fix the speed gap).

"do we have a profile for Ms. Rachel?" took 77 seconds and drafted a
build; "Do you see the SWAT Exiles Season 1 Subscriber IQ?" took 84.
Those are lookups. The corpus catalog already knows every subject the
dashboard has published on, so existence, "do you see", "what do we
have on", and audience-size asks are answered here from the index with
no model call. Anything the lane cannot answer with certainty falls
through to the normal routing untouched.

Fail-safe: any error returns None and the ask proceeds as before.
"""
from __future__ import annotations

import re

_PRODUCT_WORDS = (
    r"(?:a |an |the |any |that |this )?(?:profile iq|profile|digital journey iq|journey iq|"
    r"journey|subscriber iq|sub iq|subiq|attribution iq|attribution|brand partnership iq|"
    r"partnership read|partnership|deck|one[- ]pager|read|data|anything|something|"
    r"audience|cut|file|report|analysis)s?"
)
_EXISTS_RX = re.compile(
    r"^\s*(?:hey |hi |prometheus,? )?(?:do (?:we|you) (?:already )?(?:have|see|hold|carry|know)|"
    r"is there|are there|have we (?:got|built|run|pulled|done)|did we (?:build|run|pull)|"
    r"what do (?:we|you) have (?:on|for|about)|do you have anything (?:on|for|about)|"
    r"show me what (?:we|you) have (?:on|for|about)|can you (?:find|see|pull up))\s+"
    r"(?P<rest>.+?)[\s?.!]*$", re.I)
_SIZE_RX = re.compile(
    r"^\s*(?:how (?:big|large) is (?:the )?(?P<s1>.+?)(?: audience| universe)?|"
    r"(?:what(?:'s| is) the )?(?:audience )?size (?:of|for) (?:the )?(?P<s2>.+?)(?: audience| universe)?|"
    r"how many (?:people|viewers|users|individuals) (?:watch|watched|stream|streamed|use|used|engage with|engaged with) (?P<s3>.+?)|"
    # the sample size of a held profile (2026-10-07, Scott: "I was asking
    # to know what the sample size within our 10million is for Will and
    # Grace on Hulu" drafted a build)
    r"(?:i was asking to know |i want to know |i need to know |tell me |can you tell me |just )?"
    r"what(?:'s| is)? (?:the )?(?:sample size|sample) (?:within|in|inside|of) (?:our |the )?"
    r"(?:10 ?(?:million|m)|sample|panel)(?: is)? (?:for|of|on) (?:the )?(?P<s4>.+?)|"
    r"(?:what(?:'s| is) (?:the )?)?sample size (?:for|of|on) (?:the )?(?P<s5>.+?)(?: audience| universe| profile)?)"
    r"[\s?.!]*$", re.I)
_STRIP_RX = re.compile(
    r"^(?:" + _PRODUCT_WORDS + r")\s+(?:for|on|about|of|covering)\s+", re.I)
_TAIL_RX = re.compile(
    r"\s+(?:" + _PRODUCT_WORDS + r")(?:\s+(?:yet|already|built|done|run|in the (?:library|dashboard|system)))?$", re.I)
_YET_RX = re.compile(r"\s+(?:yet|already|built|in the (?:library|dashboard|system|corpus))$", re.I)
_PRODUCT_LABEL = {
    'profile': 'Profile IQ', 'journey': 'Digital Journey IQ', 'attribution': 'Attribution IQ',
    'bpiq': 'Brand Partnership IQ', 'chat': 'an earlier answer', 'deck': 'a deck',
    'subiq': 'Subscriber IQ', 'trends': 'Trends IQ',
}


_GENERIC = frozenset((
    'budget', 'time', 'money', 'bandwidth', 'capacity', 'access', 'permission',
    'permissions', 'credits', 'credit', 'numbers', 'results', 'answer', 'answers',
    'option', 'options', 'ability', 'way', 'chance', 'plan', 'plans', 'idea', 'ideas',
    'it', 'this', 'that', 'them', 'these', 'those', 'anything', 'something', 'everything',
    'enough', 'more', 'yet', 'today', 'tomorrow', 'now', 'here', 'there', 'you', 'me', 'us',
))
_STOP = frozenset(('the', 'a', 'an', 'of', 'for', 'on', 'in', 'and', 'or', 'to', 'at', 'by', 'with'))


def _looks_like_entity(subj):
    toks = [t for t in re.split(r'[^a-z0-9+&]+', subj.lower()) if t]
    if not toks or any(t in _GENERIC for t in toks):
        return False
    content = [t for t in toks if t not in _STOP]
    return bool(content) and (len(content) >= 2 or len(content[0]) >= 4)


def _clean_subject(rest):
    s = str(rest or '').strip().strip('"\'')
    s = _STRIP_RX.sub('', s)
    s = _YET_RX.sub('', s)
    s = _TAIL_RX.sub('', s)
    s = _YET_RX.sub('', s)
    s = re.sub(r"^(?:a |an |the )", '', s, flags=re.I).strip(' ?.!,')
    return s


def parse(text):
    """('exists'|'size', subject) or None."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 200:
        return None
    m = _EXISTS_RX.match(t)
    if m:
        subj = _clean_subject(m.group('rest'))
        if subj and len(subj.split()) <= 10 and _looks_like_entity(subj):
            return 'exists', subj
        return None
    m = _SIZE_RX.match(t)
    if m:
        subj = _clean_subject(m.group('s1') or m.group('s2') or m.group('s3')
                              or m.group('s4') or m.group('s5'))
        if subj and len(subj.split()) <= 10 and _looks_like_entity(subj):
            return 'size', subj
    return None


_SUBIQ_RX = re.compile(r'\bsub(?:scriber)?\s*iq\b', re.I)


def _fmt_people(v):
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _window_text(w):
    if not isinstance(w, dict) or not w.get('start'):
        return ''
    return f" ({w.get('start')} to {w.get('end')})"


def _summarize(anchors):
    """One line per product the catalog holds on the subject."""
    lines = []
    facts = anchors.get('facts') or []
    by_product = {}
    for f in facts:
        by_product.setdefault(f.get('product'), []).append(f)
    for product in ('profile', 'journey', 'attribution', 'bpiq', 'subiq', 'trends', 'deck'):
        fs = by_product.get(product)
        if not fs:
            continue
        label = _PRODUCT_LABEL.get(product, product)
        if product == 'profile':
            parents = [f for f in fs if f.get('kind') == 'audience_size' and not f.get('cut')]
            cuts = {(f.get('note') or '').replace('Profile IQ: ', '') for f in fs if f.get('cut')}
            if parents:
                p = parents[0]
                lines.append(f"{label}: {_fmt_people(p['value'])} US people{_window_text(p.get('window'))}"
                             + (f", plus {len(cuts)} cut{'s' if len(cuts) != 1 else ''}" if cuts else ''))
            elif cuts:
                lines.append(f"{label}: {len(cuts)} cut{'s' if len(cuts) != 1 else ''}")
            else:
                lines.append(label)
        elif product == 'journey':
            ends = [f for f in fs if f.get('kind') == 'audience_size']
            if ends:
                e = ends[0]
                lines.append(f"{label}: {_fmt_people(e['value'])} US people at the end point{_window_text(e.get('window'))}")
            else:
                lines.append(label)
        elif product == 'attribution':
            top = [f for f in fs if f.get('kind') == 'stage_count']
            top.sort(key=lambda f: -float(f.get('value') or 0))
            if top:
                lines.append(f"{label}: {_fmt_people(top[0]['value'])} US people {top[0].get('label')}{_window_text(top[0].get('window'))}")
            else:
                lines.append(label)
        elif product == 'bpiq':
            aud = [f for f in fs if f.get('kind') == 'audience_size']
            if aud:
                lines.append(f"{label}: {_fmt_people(aud[0]['value'])} US people reached{_window_text(aud[0].get('window'))}")
            else:
                lines.append(label)
        else:
            lines.append(label)
    return lines


def answer(text, ctx=None):
    """A finished reply payload for an existence or size ask the
    catalog can settle, else None."""
    parsed = parse(text)
    if not parsed:
        return None
    kind, subj = parsed
    try:
        from migration import corpus_catalog as cc
    except Exception:
        return None
    try:
        anchors = cc.anchors_for(subj, with_ledger=False)
    except Exception:
        return None
    facts = anchors.get('facts') or []
    related = anchors.get('related') or []
    display = anchors.get('subject') or subj
    if kind == 'size':
        pa = cc.profile_anchor(anchors) if facts else None
        if not pa or not pa.get('audience_size'):
            return None  # the reasoning path sizes it
        reply = (f"{display}: {_fmt_people(pa['audience_size'])} US people"
                 f"{_window_text(pa.get('window'))}, {_fmt_people(pa.get('sample_size'))} "
                 f"inside the 10 million sample. That is the profile on the dashboard.")
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': [f"Open {display}", f"Who is the {display} audience?"],
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True,
                'subject': display}
    if facts:
        lines = _summarize(anchors)
        body = '; '.join(lines) if lines else 'it is on the dashboard'
        reply = f"Yes. On {display} we have {body}. Open it from the dashboard, or ask me about it here."
        chips = [f"Open {display}", f"Who is the {display} audience?"]
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True,
                'subject': display}
    if _SUBIQ_RX.search(str(text or '')):
        return None  # the Subscriber IQ lookup lane owns a miss here
    if related:
        names = [r.get('subject') for r in related[:4] if r.get('subject')]
        reply = (f"Not under that exact name. Close matches on the dashboard: "
                 f"{', '.join(names)}. Say which one you mean, or say 'build a profile on {subj}' "
                 f"and I will draft it.")
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': names[:3] + [f"Build a profile on {subj}"],
                'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True,
                'memory_confirm': {'question': text,
                                   'options': [{'label': n, 'subject': n} for n in names[:3]]}}
    reply = (f"No. There is nothing on {subj} on the dashboard yet. Say 'build a profile on {subj}' "
             f"and I will draft it for your approval.")
    return {'success': True, 'action': 'answer', 'reply': reply,
            'followups': [f"Build a profile on {subj}"],
            'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True}
