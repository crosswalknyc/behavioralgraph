"""A cross of behaviors inside one audience (2026-11-11, Jenna, feedback
she forwarded: "The cross-tab functionality, open up to across
behaviors. between behavior A, B and C... For example the Chime users
who shop at Target and have an interest in X.").

"Chime users who shop at Target and are interested in home
improvement", "of Wicked Tuna viewers, how many use Hulu and drive a
Ford", "cross Target shoppers with Netflix and Costco in this profile"
are QUESTIONS: the size of the group inside one audience that does
every behavior named. Before this the first drew a build draft, the
second a sizing of the whole audience, the third nothing.

The read:

- the audience is the profile the words name, else the profile on
  screen when the words point at it or name no audience; a named
  audience with no profile is not this lane (the caller's standing
  lanes run as before);
- every behavior binds to its row on THAT profile (a brand in the
  category the verb points at, an interest row, a whole category, a
  demographic bucket). A behavior the profile does not carry is said
  plainly with the nearest rows offered, before any price is named;
  nothing is ever substituted;
- the figure comes from the one estimator
  (migration/behavior_crosstab.py): share of the audience, US people,
  the same group in the US, the index with its plain comparison, the
  single shares beside it and, for three behaviors, each pair;
- once stated, the figure is on the record for the profile and the
  group (``bank``), so a later chat, the page and a deck return the
  same number and a narrower group never exceeds a wider one;
- it is free when every behavior is a row on a profile the reader can
  open (Jenna, the same day: "Build it on the page, free for anyone
  with the profile"): no quote, no Approve, no wallet touch, on the
  page (``page_options`` / ``page_read``, the Cross behaviors section in
  the Data Cuts panel) and in chat alike, through one ``deliver``. A
  reader who cannot open the profile keeps the standing custom ask
  quote (prometheus.priced_read). An order (build / run / create a
  profile or a cut) is never this lane: the build flow takes it.

Fail-safe: ``parse`` returns None for anything that is not this ask and
``plan`` returns None when the audience has no profile, so the standing
lanes are unchanged. Never raises into the dispatcher.
Regression: scripts/test_pm_crosstab_behaviors.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback

__all__ = ['parse', 'wants', 'decline', 'declined', 'plan', 'answer', 'pair_on_profile', 'deliver', 'page_options', 'page_read',
           'can_open', 'load_profile', 'bank_get', 'bank_put', 'compute', 'BANK_PREFIX', 'is_clean']

BANK_PREFIX = 'system/insights_ledger/crosstab/'
LEDGER_SLOTS = 6
GENPOP_KEY = 'Gen_Pop_2026.csv'

_AUD_NOUN = (r"(?:users?|viewers?|fans?|customers?|shoppers?|subscribers?|audience|listeners?|buyers?|members?|"
             r"readers?|watchers?|players?|purchasers?|consumers?|engagers?|followers?|people)")
# an audience that is everyone is not a profile's audience (the words name their own universe)
_GENERIC_AUD_RX = re.compile(
    r"^(?:(?:all|the|us|u\.s\.|american|adult|total|online|digital)\s+)*(?:persons|adults|americans|households|everyone|"
    r"everybody|population|public|men|women|" + _AUD_NOUN + r")$", re.I)
# an audience phrase that is really another question, a list or a reply ("other titles watched by people")
_NOT_AUD_RX = re.compile(r"[:;()]|https?:|\b(?:by|watched|bought|other|titles?|shows?|brands?|what|which|there|those who)\b", re.I)
# a date or a window is never a behavior ("in November", "December 2025", "last year")
_DATE_PIECE_RX = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?|q[1-4]|(?:19|20)\d{2}|last\s+(?:year|month|week|quarter)|this\s+(?:year|month|week|quarter)|"
    r"the\s+window|past\s+\d+|\d+\s+days?|yesterday|today)\b", re.I)
_PTR_PIECE_RX = re.compile(r"\b(?:there|those|these|them|neither|none|either)\b|https?:|[()]", re.I)
_DEMO_NAME_RX = re.compile(
    r"^(?:(?:aged?|ages)\s+)?\d{2}\s*(?:-|to|and)\s*\d{2}$|^(?:aged?\s+)?\d{2}\s*(?:\+|plus|or older|and older|and over)$|"
    r"^(?:female|male|women|men|parents?|moms|dads|mothers|fathers|married|single|hispanic|latino|latina|latinx|black|"
    r"african american|asian|white|lgbtq\+?|lgbt|gay|college graduates?|college grads?|seniors|straight)$", re.I)
_POINTER_RX = re.compile(
    r"^(?:(?:of|in|on|for|among|within|inside)\s+)?(?:(?:this|that|these|those|the|our|my)\s+(?:open\s+|current\s+|same\s+)?"
    r"(?:audience|profile|file|page|group|" + _AUD_NOUN + r")|them|those|these|they|people\s+here|" + _AUD_NOUN
    + r"\s+here)(?:\s+here)?$", re.I)
_SCOPE_TAIL_RX = re.compile(
    r"[\s,]+(?:in|on|for|within|among|inside|across|from)\s+(?P<scope>(?:this|that|the|these|those|our|my)\s+"
    r"(?:open\s+|current\s+)?(?:profile|audience|file|page|group|" + _AUD_NOUN + r")(?:\s+here)?|"
    r"(?:the\s+)?[^,?]{2,70}?\s+(?:profile|audience|file)|[^,?]{2,70}?\s+" + _AUD_NOUN + r")\s*$", re.I)
_HERE_TAIL_RX = re.compile(r"[\s,]+(?:here|on (?:my |the )?screen)\s*$", re.I)
# leads that ask for a size or a share (the only questions this lane answers)
_LEAD_RX = re.compile(
    r"^\W*(?:ok[,.]?\s+|so[,.]?\s+|hey[,.]?\s+|and\s+)?(?:please\s+)?"
    r"(?:(?:can|could|would) you\s+(?:please\s+)?(?:tell me|give me|show me|pull|get me|size|find)\s+|"
    r"(?:tell|show|give) me\s+|pull\s+(?:me\s+)?|i(?:'d| would)? (?:like|want|need)(?: to (?:see|know|size))?\s+|"
    r"we (?:want|need)(?: to (?:see|know|size))?\s+|do (?:we|you) know\s+|what(?:'s| is| are)\s+)?"
    r"(?:(?:the\s+)?(?:size|number|count|share|percent(?:age)?|portion|fraction|overlap|cross(?:[- ]?tab)?)\s+(?:of|for|on)\s+|"
    r"how\s+(?:many|big\s+(?:is|are)|large\s+(?:is|are))\s+(?:of\s+)?|"
    r"what\s+(?:share|percent(?:age)?|portion|fraction|number|proportion)\s+of\s+|"
    r"which\s+share\s+of\s+)?(?:the\s+group\s+of\s+|the\s+|all\s+)?", re.I)
_NOT_SIZE_LEAD_RX = re.compile(
    r"^\W*(?:what|which|why|where|who|whom|when|how|is|are|was|were|do|does|did|can|could|should|would|will|tell|"
    r"describe|explain|compare|list|rank|top|give|show|find|name|summarize|profile|build|create|make|run|queue|"
    r"email|send|export|download|open|add|deck|draft|write)\b", re.I)
_OF_RX = re.compile(
    r"^\W*(?:of|among|amongst|out of|for|within|inside|in)\s+(?:all\s+)?(?:the\s+)?(?P<aud>.{2,90}?)\s*[,:]?\s*"
    r"(?:how\s+many|what\s+(?:share|percent(?:age)?|portion|fraction|number|proportion)|which\s+share)\s*"
    r"(?:of\s+(?:them|those|these)\s+)?(?:also\s+)?(?P<body>.+)$", re.I)
_WHO_RX = re.compile(
    r"^(?P<aud>.{2,90}?)\s+(?:who|that|which)\s+(?P<also>also\s+)?(?P<body>.+)$", re.I)
_CROSS_RX = re.compile(
    r"^\W*(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:(?:run|do|give me|show me|pull)\s+(?:a\s+|the\s+)?)?"
    r"(?:cross[- ]?tab(?:ulat(?:e|ion))?|cross)(?:\s+of)?\s+(?P<body>.+)$", re.I)
# the page is the base when the words point at it or name no universe at all ("how many
# shop at Target and use Hulu"); "how many people ..." names its own universe and is not this
_HOW_MANY_LEAD_RX = re.compile(
    r"^\W*(?:how\s+many|what\s+(?:share|percent(?:age)?|portion|fraction|number|proportion)\s+of|which\s+share\s+of|"
    r"what\s+(?:share|percent(?:age)?|portion|fraction|number|proportion))\s+", re.I)
_HERE_PTR_RX = re.compile(
    r"^(?:(?:of\s+)?(?:this audience|the audience|this profile|this group|(?:these|those)\s+" + _AUD_NOUN
    + r"|them|those|these|people\s+here|" + _AUD_NOUN + r"\s+here)|of\s+(?:the|our|my)\s+" + _AUD_NOUN + r")\s+", re.I)
_AUD_NOUN_RX = re.compile(r"\b" + _AUD_NOUN + r"\b", re.I)
_ALL_WORDS_RX = re.compile(r"\b(?:all\s+(?:three|four|of\s+(?:them|these|those))|both|at\s+the\s+same\s+time|every\s+one\s+of)\b", re.I)
# an ampersand is part of a row's name ('Home & Living'), never a joiner
_SPLIT_RX = re.compile(r"\s*(?:,\s*and\s+|,\s*|;\s*|\s+and\s+also\s+|\s+as\s+well\s+as\s+|\s+and\s+|\s+plus\s+|\s+\+\s+)\s*", re.I)
_CROSS_SPLIT_RX = re.compile(r"\s*(?:,\s*and\s+|,\s*|\s+with\s+|\s+against\s+|\s+by\s+|\s+x\s+|\s+vs\.?\s+|\s+and\s+|\s+\+\s+)\s*", re.I)
_TRAIL_RX = re.compile(r"\s*(?:too|as well|also|at all|in (?:the\s+)?(?:us|u\.s\.|united states)|right now|today)\s*$", re.I)
_WH_PIECE_RX = re.compile(r"^(?:what|which|why|where|who|when|how|is|do|does|did|can|could|should|would|if|then|give|show|tell)\b", re.I)
_DEMO_LEAD_RX = re.compile(r"^(?P<d>female|male|women|men)\s+(?P<rest>.+)$", re.I)
_WHOLE_RX = re.compile(r"^(?:any|at\s+least\s+one|one\s+or\s+more|some)\s+(?:of\s+the\s+)?(?P<n>.+?)(?:\s+(?:brands?|at\s+all))?$", re.I)
_WHOLE_TAIL_RX = re.compile(r"^(?:an?\s+|the\s+)?(?P<n>.+?)\s+(?:category|categories)$", re.I)

# verb -> where the row lives (behavior_crosstab.HINT_COLUMNS keys). First match wins.
_VERBS = (
    (r"(?:(?:are|is|were)\s+)?(?:also\s+)?interested\s+in|(?:have|has|had|with|show|shows|showing)\s+(?:an?\s+|some\s+)?"
     r"(?:strong\s+|keen\s+)?interest\s+in|(?:are|is)\s+into|care\s+about|(?:whose\s+)?interests?\s+(?:include|is|are)", 'interest'),
    (r"(?:also\s+)?shop(?:s|ped|ping)?\s+(?:at|on|with|from|in)|(?:are|is)\s+(?:also\s+)?shoppers?\s+(?:at|of)", 'shop'),
    (r"(?:also\s+)?(?:buy|buys|bought|purchase[sd]?|order(?:s|ed)?)(?:\s+(?:from|at|on))?", 'buy'),
    (r"(?:also\s+)?(?:eat|eats|ate|dine|dines|dined)(?:\s+(?:at|in))?", 'eat'),
    (r"(?:also\s+)?(?:drive|drives|drove|own|owns|owned)(?:\s+an?)?", 'drive'),
    (r"(?:also\s+)?(?:watch|watches|watched|stream|streams|streamed)(?:\s+on)?", 'watch'),
    (r"(?:also\s+)?(?:bank|banks|banked)\s+(?:with|at)", 'bank'),
    (r"(?:also\s+)?(?:listen|listens|listened)\s+to", 'listen'),
    (r"(?:also\s+)?(?:play|plays|played)", 'play'),
    (r"(?:also\s+)?(?:follow|follows|followed|(?:are|is)\s+fans?\s+of|like|likes|love|loves)", 'follow'),
    (r"(?:also\s+)?(?:fly|flies|flew|stay|stays|stayed|travel|travels|traveled)(?:\s+(?:with|at|on))?", 'travel'),
    (r"(?:also\s+)?(?:wear|wears|wore)", 'wear'),
    (r"(?:also\s+)?(?:bet|bets)(?:\s+(?:on|with|at))?", 'bet'),
    (r"(?:also\s+)?(?:read|reads)", 'read'),
    (r"(?:also\s+)?(?:work\s+out|works\s+out|exercise[sd]?)\s+at", 'workout'),
    (r"(?:also\s+)?(?:use|uses|used|using|(?:are|is)\s+(?:also\s+)?on|have|has)(?:\s+an?)?", 'use'),
    (r"(?:also\s+)?(?:visit|visits|visited|engage[sd]?\s+with|go(?:es)?\s+to|went\s+to)", ''),
    (r"(?:are|is|were)(?:\s+also)?", 'demo'),
)
_VERB_RXS = tuple((re.compile(r"^(?:" + rx + r")\s+(?P<name>.+)$", re.I), hint) for rx, hint in _VERBS)
_NOUN_HINT = (('shoppers', 'shop'), ('shopper', 'shop'), ('buyers', 'buy'), ('purchasers', 'buy'), ('users', 'use'),
              ('user', 'use'), ('subscribers', 'use'), ('members', 'use'), ('viewers', 'watch'), ('watchers', 'watch'),
              ('drivers', 'drive'), ('owners', 'drive'), ('listeners', 'listen'), ('diners', 'eat'), ('fans', 'follow'),
              ('followers', 'follow'), ('players', 'play'), ('customers', ''))
_BANNED_RX = re.compile(
    r"\b(?:modell?ed|modeling|synth\w*|estimated?|estimates?|joint probabilit\w*|independen\w*|projected[- ]from|"
    r"household\w*|\bhhs?\b|panelists?|confidence (?:interval|level|tier)|margin of error|per (?:pew|nielsen|statista)|"
    r"source:|copula|loading|unrelated figure)\b|[\u2013\u2014]", re.I)


def is_clean(reply):
    """True when a reply carries none of the words a reader never sees."""
    return not _BANNED_RX.search(str(reply or ''))


# ------------------------------------------------------------------ parse
def _squash(text):
    return ' '.join(str(text or '').split()).strip()


def _is_order(text):
    try:
        from . import orders as _o
        if _o.is_order(text):
            return True
    except Exception:
        pass
    try:
        from . import priced_read as _pr
        if _pr.is_product_order(text):
            return True
    except Exception:
        pass
    return False


def _piece(raw, prev_hint=''):
    """One behavior out of a piece of the sentence: {'name', 'hint',
    'whole', 'verb'} or None when the piece is not a behavior."""
    p = _TRAIL_RX.sub('', _squash(raw).strip(' ?.!,;:"\''))
    p = re.sub(r"^(?:who|that|which|they|and|also|who also|that also)\s+", '', p, flags=re.I)
    if not p or _WH_PIECE_RX.match(p):
        return None
    hint, verb, name = prev_hint, False, p
    kids = re.match(r"^(?:(?:are|is)\s+)?(?:have|has|with)\s+(?P<no>no\s+)?(?:kids|children)$", p, re.I)
    if kids:
        return {'name': 'no children' if kids.group('no') else 'parents', 'hint': 'demo', 'whole': False, 'verb': True}
    for rx, h in _VERB_RXS:
        m = rx.match(p)
        if m:
            hint, verb, name = h, True, m.group('name')
            break
    name = re.sub(r"^(?:the|a|an)\s+", '', name.strip(' ?.!,;:"\''), flags=re.I)
    if _DATE_PIECE_RX.search(name) or _PTR_PIECE_RX.search(name):
        return None
    if hint == 'demo' and verb and not _DEMO_NAME_RX.match(name):
        return None      # "are there", "are new sign ups": not a bucket to cross
    whole = False
    m = _WHOLE_RX.match(name)
    if m:
        whole, name = True, m.group('n')
    else:
        m = _WHOLE_TAIL_RX.match(name)
        if m:
            whole, name = True, m.group('n')
    if not verb:
        low = name.lower()
        for noun, h in _NOUN_HINT:
            if low.endswith(' ' + noun):
                hint = h or hint
                break
    toks = name.split()
    if not name or len(toks) > 7 or len(name) > 60:
        return None
    if hint == 'demo' and not verb:
        hint = ''
    return {'name': name, 'hint': hint, 'whole': whole, 'verb': verb}


def _pieces(body, splitter=_SPLIT_RX):
    out, prev = [], ''
    for raw in splitter.split(_squash(body)):
        if not raw or not raw.strip():
            continue
        if _ALL_WORDS_RX.fullmatch(raw.strip(' ?.!,')):
            continue
        b = _piece(raw, prev)
        if b is None:
            return None
        if b['verb'] or not prev:
            prev = b['hint']
        out.append(b)
    return out


def _aud(aud):
    """(audience phrase, points at the page, demo names led with)."""
    a = _squash(aud).strip(' ?.!,;:"\'')
    if a and _POINTER_RX.match(a):
        return '', True, []
    a = re.sub(r"^(?:the|all|our|my)\s+", '', a, flags=re.I)
    demo = []
    m = _DEMO_LEAD_RX.match(a)
    if m and len(m.group('rest').split()) >= 2:
        demo.append(m.group('d'))
        a = m.group('rest')
    if not a or _POINTER_RX.match(a):
        return '', True, demo
    return a, False, demo


def parse(text, ctx=None):
    """{'audience', 'page', 'behaviors': [{'name', 'hint', 'whole'}],
    'frame'} for a cross of behaviors inside one audience, else None.
    Pure text; an order is never a cross."""
    try:
        t = _squash(text)
        if not t or len(t) > 420 or '\n' in str(text or '').strip():
            return None
        if _is_order(t):
            return None
        if ';' in t or re.search(r"https?:|www\.", t, re.I):
            return None
        try:
            from . import overlap_read as _ov
            if _ov.is_overlap_read(t) or _ov.also_watched(t):
                return None      # a subscribe overlap or a two-service co-viewing ask keeps its own lane
        except Exception:
            pass
        t = t.rstrip(' ?.!')
        frame, aud, body, also, demo = '', '', '', False, []
        m = _CROSS_RX.match(t)
        if m:
            frame, body = 'cross', m.group('body')
            sm = _SCOPE_TAIL_RX.search(body)
            if sm:
                aud, body = sm.group('scope'), body[:sm.start()]
            else:
                hm = _HERE_TAIL_RX.search(body)
                if hm:
                    body = body[:hm.start()]
            behaviors = _pieces(body, _CROSS_SPLIT_RX)
        else:
            m = _OF_RX.match(t)
            if m:
                frame, aud, body = 'of', m.group('aud'), m.group('body')
            else:
                lead = _LEAD_RX.match(t)
                rest = t[lead.end():] if lead else t
                m = _WHO_RX.match(rest)
                if m and re.search(r"\b" + _AUD_NOUN + r"$", m.group('aud'), re.I) and not _NOT_SIZE_LEAD_RX.match(rest) \
                        and not re.match(r"^(?:about|on|regarding)\b", rest, re.I):
                    frame, aud, body, also = 'who', m.group('aud'), m.group('body'), bool(m.group('also'))
                else:
                    hm = _HOW_MANY_LEAD_RX.match(t)
                    if not hm:
                        return None
                    tail = t[hm.end():]
                    pm = _HERE_PTR_RX.match(tail)
                    body = re.sub(r"^here\s+", '', tail[pm.end():] if pm else tail, flags=re.I)
                    also = bool(re.match(r"^also\b", body, re.I))
                    frame, aud, body = 'here', '', re.sub(r"^also\s+", '', body, flags=re.I)
                    sm = _SCOPE_TAIL_RX.search(body)
                    if sm:
                        aud, body = sm.group('scope'), body[:sm.start()]
                    first = _pieces(body)
                    if not pm and not (first and first[0]['verb']):
                        # "how many Chime users shop at Target and use Hulu": the audience is named ahead of the verbs
                        frame, aud, body = '', '', ''
                        for nm in _AUD_NOUN_RX.finditer(tail):
                            cand = _pieces(tail[nm.end():])
                            if cand and cand[0]['verb'] and len(cand) >= 2:
                                frame, aud, body = 'who', tail[:nm.end()], tail[nm.end():]
                                break
                        if not frame:
                            return None
            behaviors = _pieces(body)
        if not behaviors or len(behaviors) < 2:
            return None
        verbs = sum(1 for b in behaviors if b['verb'])
        if frame != 'cross':
            if not behaviors[0]['verb']:
                return None      # "users who Target and Hulu" is not a sentence about behaviors
            # one verb over a list after "also" ("that also shop at Target, Walmart and
            # Kohl's") asks for each overlap, not the people who do all of them
            if (also or re.match(r"^also\b", _squash(body), re.I)) and verbs == 1 and not _ALL_WORDS_RX.search(t):
                return None
            if verbs == 1 and len(behaviors) >= 4 and not _ALL_WORDS_RX.search(t):
                return None
        audience, page, demo = _aud(aud)
        if audience and (_GENERIC_AUD_RX.match(audience) or _NOT_AUD_RX.search(audience) or len(audience.split()) > 9):
            return None      # everyone is not a profile's audience; another question is not an audience
        for d in demo:
            behaviors.append({'name': d, 'hint': 'demo', 'whole': False, 'verb': False})
        if len(behaviors) > 5:
            return None
        if not audience:
            page = True
        return {'audience': audience, 'page': bool(page), 'frame': frame,
                'behaviors': [{'name': b['name'], 'hint': b['hint'], 'whole': bool(b['whole']), 'verb': bool(b['verb'])}
                              for b in behaviors]}
    except Exception:
        traceback.print_exc()
        return None


def _page(ctx):
    try:
        p = (ctx or {}).get('primary') or {}
        return str(p.get('name') or '').strip(), str(p.get('s3_key') or '').strip()
    except Exception:
        return '', ''


def wants(text, ctx=None):
    """True when the words are a cross of behaviors this lane can try:
    they name an audience, or a profile is open for them to point at."""
    q = parse(text, ctx)
    if not q:
        return False
    if q['audience']:
        return True
    name, key = _page(ctx)
    return bool(name and key)


_DECLINED = {'text': ''}


def decline(text):
    """Note that this ask is not this lane's (its audience has no
    profile), so the words are read again by the standing lanes for the
    rest of the request."""
    t = _squash(text)
    _DECLINED['text'] = t
    try:
        from flask import g as _g
        _g._pm_crosstab_declined = t
    except Exception:
        pass


def declined(text):
    t = _squash(text)
    try:
        from flask import g as _g
        return bool(t) and getattr(_g, '_pm_crosstab_declined', None) == t
    except Exception:
        return bool(t) and _DECLINED['text'] == t


def subject_hint(text, ctx=None):
    q = parse(text, ctx) or {}
    return q.get('audience') or _page(ctx)[0] or 'that audience'


# ---------------------------------------------------------------- resolve
def _engine():
    try:
        from migration import behavior_crosstab as bx
    except Exception:
        import behavior_crosstab as bx  # type: ignore
    return bx


def _frame(host, key):
    from .brand_coverage import _frame as _bf
    return _bf(host, key)


_GP_MAP = {'frame': None, 'map': {}}
_CTX = {}      # {profile key: (frame, US lookup, bind context)}: a search box asks many times in a row


def load_profile(profile_key, host=None):
    """(profile frame, US lookup, bind context) for one profile: the one
    loader the chat answer and the page read share, so both see the same
    rows and the same US figures."""
    bx = _engine()
    host = _host(host)
    df = _frame(host, profile_key)
    try:
        gdf = _frame(host, GENPOP_KEY)
    except Exception:
        gdf = None
    if gdf is None:
        gp_map = {}
    elif _GP_MAP['frame'] is gdf:
        gp_map = _GP_MAP['map']
    else:
        gp_map = bx.genpop_map(gdf)
        _GP_MAP['frame'], _GP_MAP['map'] = gdf, gp_map
    hit = _CTX.get(profile_key)
    if hit and hit[0] is df and hit[1] is gp_map:
        return df, gp_map, hit[2]
    bctx = bx.profile_context(df, gp_map)
    if len(_CTX) > 24:
        _CTX.clear()
    _CTX[profile_key] = (df, gp_map, bctx)
    return df, gp_map, bctx


def can_open(user, profile_key, host=None):
    """True when this reader can open the profile file (the profile
    page's own gate). A host that cannot say is a no."""
    try:
        host = _host(host)
        if not host.has('can_open_profile'):
            return False
        return bool(host.can_open_profile(user, profile_key))
    except Exception:
        return False


def _host(host):
    if host is not None:
        return host
    from .host import host as _h
    return _h


def _same(a, b):
    f = lambda s: re.sub(r"[^a-z0-9]+", '', str(s or '').lower())
    return bool(f(a)) and f(a) == f(b)


def _fold(s):
    return re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).strip()


def _catalog_bases(bare):
    """Profiles whose subject begins with the name the words gave
    ('Chime' -> 'Chime Banking Customers'): [{'name', 'key'}], whole
    audiences only (a cut of one is never offered as the audience)."""
    out, seen = [], set()
    try:
        from . import sizing_lane as _sz
        cc = _sz._catalog()
        want = _fold(bare)
        if cc is None or len(want) < 3:
            return out
        idx = cc.load_index() or {}
        for k, ent in (idx.get('subjects') or {}).items():
            spaced = str(k).replace('_', ' ')
            if not (spaced == want or spaced.startswith(want + ' ')):
                continue
            als = (ent or {}).get('aliases') or []
            name = cc.best_display(als, spaced) if hasattr(cc, 'best_display') else (als[0] if als else spaced)
            hit, _a = _sz._anchor(cc, name)
            key = str((hit or {}).get('key') or '')
            if not key or ' - ' in key or key in seen:
                continue
            seen.add(key)
            out.append({'name': str(hit.get('name') or name), 'key': key})
            if len(out) >= 6:
                break
    except Exception:
        traceback.print_exc()
    return out


def _resolve_profile(q, ctx, host):
    """{'name', 'key'} of the audience's profile; {'choices': [...]}
    when the name fits more than one audience; None when it fits none."""
    page_name, page_key = _page(ctx)
    if q.get('audience'):
        hit = None
        try:
            from . import sizing_lane as _sz
            hit = _sz.resolve(q['audience'], ctx)
            bare = _sz.strip_audience_nouns(q['audience']) or q['audience']
        except Exception:
            traceback.print_exc()
            hit, bare = None, q['audience']
        if hit and hit.get('key'):
            return {'name': str(hit.get('name') or q['audience']), 'key': str(hit['key'])}
        if page_key and page_name:
            # the page's own audience under a shorter name ('Chime users' with Chime Banking Customers open)
            pf, bf = _fold(page_name.split(' - ')[0]), _fold(bare)
            if _same(q['audience'], page_name) or _same(bare, page_name) or (len(bf) >= 3 and (pf == bf or pf.startswith(bf + ' '))):
                return {'name': page_name, 'key': page_key}
        bases = _catalog_bases(bare)
        if len(bases) == 1:
            return bases[0]
        if len(bases) > 1:
            return {'choices': bases}
        return None
    if q.get('page') and page_key and page_name:
        return {'name': page_name, 'key': page_key}
    return None


def _display(name):
    try:
        from .subject_display import strip_emoji as _se
        name = _se(name) or name
    except Exception:
        pass
    return str(name or '').strip()


# ------------------------------------------------------------------- bank
_BANK_HOOK = {'get': None, 'put': None}      # tests swap the store here


def _bank_key(profile_key):
    return BANK_PREFIX + hashlib.sha1(str(profile_key).encode('utf-8', 'replace')).hexdigest()[:24] + '.json'


def bank_get(profile_key, host=None):
    """{set key: cell} on the record for one profile ({} when none)."""
    try:
        if _BANK_HOOK['get']:
            doc = _BANK_HOOK['get'](profile_key)
        else:
            h = _host(host)
            body = h.s3_client.get_object(Bucket=h.bucket, Key=_bank_key(profile_key))['Body'].read()
            doc = json.loads(body)
        cells = (doc or {}).get('cells')
        return cells if isinstance(cells, dict) else {}
    except Exception:
        return {}


def _may_record(username):
    """A real person in a real process, outside a probe request."""
    try:
        from . import faults as _f
        if _f.is_probe():
            return False
    except Exception:
        pass
    try:
        from . import probe_identity as _pi
        return bool(_pi.may_record(username))
    except Exception:
        return False


def bank_put(profile_key, subject, cells, *, window=None, by='', host=None, force=False):
    """Put cells on the record. A cell already there is never replaced.
    Probes and test processes write nothing unless a test store is
    swapped in. Returns the number of new cells written."""
    if not cells or not profile_key:
        return 0
    if not force and not _BANK_HOOK['put'] and not _may_record(by):
        return 0
    try:
        doc = None
        if _BANK_HOOK['get']:
            doc = _BANK_HOOK['get'](profile_key)
        else:
            h = _host(host)
            try:
                doc = json.loads(h.s3_client.get_object(Bucket=h.bucket, Key=_bank_key(profile_key))['Body'].read())
            except Exception:
                doc = None
        if not isinstance(doc, dict):
            doc = {}
        have = doc.get('cells') if isinstance(doc.get('cells'), dict) else {}
        new = 0
        for k, c in cells.items():
            if k in have or not isinstance(c, dict):
                continue
            have[k] = dict(c, ts=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), by=str(by or '')[:80])
            new += 1
        if not new:
            return 0
        doc.update({'profile_key': str(profile_key), 'subject': str(subject or '')[:160], 'cells': have,
                    'window': window or doc.get('window') or {}, 'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        if _BANK_HOOK['put']:
            _BANK_HOOK['put'](profile_key, doc)
        else:
            h = _host(host)
            h.s3_client.put_object(Bucket=h.bucket, Key=_bank_key(profile_key), Body=json.dumps(doc).encode('utf-8'),
                                   ContentType='application/json')
        return new
    except Exception:
        traceback.print_exc()
        return 0


# ------------------------------------------------------------------- plan
def _verb_for(cond, hint=''):
    bx = _engine()
    if cond.get('kind') == 'demo':
        return 'are'
    if cond.get('kind') == 'category':
        return {'QSR': 'eat at', 'WHERE THEY DINE': 'eat at', 'WHERE THEY SHOP': 'shop at'}.get(cond['column'], 'use')
    if hint == 'watch' and cond['column'] in bx.HINT_COLUMNS['watch']:
        return 'watch'
    return bx.COLUMN_VERB.get(cond['column'], 'engage with')


def _noun_phrase(cond, hint=''):
    v = _verb_for(cond, hint)
    if cond.get('kind') == 'demo':
        col, lab = str(cond.get('column') or '').replace(' ', '_'), str(cond['label'])
        if col == 'AGE':
            return f"are aged {lab}"
        if col == 'PARENTAL_STATUS':
            return 'have children' if lab.lower().startswith('has') else ('have no children' if lab.lower().startswith('no') else f"are {lab}")
        if col == 'INCOME':
            return f"have an income of {lab}"
        if col in ('GENDER', 'RELATIONSHIP'):
            return f"are {lab.lower()}"
        return f"are {lab}"
    if cond.get('kind') == 'category':
        return f"{v} {cond['label']}"
    return f"{v} {cond['label']}"


def _join_or(parts):
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return ''.join(parts)
    return ', '.join(parts[:-1]) + f" or {parts[-1]}"


def _join(parts):
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return ''.join(parts)
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ', '.join(parts[:-1]) + f" and {parts[-1]}"


def _free(reply, subject, chips=None, **extra):
    out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': list(chips or [])[:4],
           'offer_deck': False, 'deck_angle': None, 'subject': subject, 'crosstab_unbound': True,
           'priced_lane': 'crosstab'}
    out.update(extra)
    return out


def plan(text, ctx=None, *, host=None, history=None):
    """None when this is not a cross this lane can read (not the ask, or
    the audience has no profile). {'reply': payload} when a behavior is
    not on the profile (said plainly, free, before any price). Else the
    bound plan: {'subject', 'key', 'conditions', 'phrases', 'label',
    'lead', 'df', 'window', 'sample', 'question'}."""
    try:
        q = parse(text, ctx)
        if not q:
            return None
        host = _host(host)
        prof = _resolve_profile(q, ctx, host)
        if not prof:
            return None
        if prof.get('choices'):
            names = [_display(c['name']) for c in prof['choices'][:4]]
            chips = [_swap(text, q['audience'], n) for n in names[:3]]
            return {'reply': _free("Which audience do you mean: " + _join_or(names) + "? Name one and I will size the group. "
                                   "Nothing has been pulled or charged.", '', [c for c in chips if c])}
        try:
            df, _gp_map, bctx = load_profile(prof['key'], host)
        except Exception:
            traceback.print_exc()
            return None
        bx = _engine()
        subject = _display(prof['name'])
        bound, missing, everyone, phrases = [], [], [], {}
        todo = list(q['behaviors'])
        while todo:
            b = todo.pop(0)
            r = bx.bind(df, b['name'], hint=b['hint'], whole=b['whole'], context=bctx)
            if r['status'] == 'missing' and todo and not todo[0].get('verb') and not b['whole']:
                # a row whose own name carries an "and" ('Health and Wellness', 'Bed Bath and Beyond')
                joined = bx.bind(df, f"{b['name']} and {todo[0]['name']}", hint=b['hint'], context=bctx)
                if joined['status'] == 'bound':
                    todo.pop(0)
                    r = joined
            if r['status'] == 'bound':
                c = r['condition']
                if c['key'] not in phrases:
                    bound.append(c)
                    phrases[c['key']] = _noun_phrase(c, b['hint'])
            elif r['status'] == 'everyone':
                everyone.append(r)
            else:
                missing.append(dict(r, hint=b['hint']))
        # a brand named beside its own whole category adds nothing: the brand is inside it
        cats = {c['column'] for c in bound if c['kind'] == 'category'}
        if cats and any(c['kind'] != 'category' and c['column'] in cats for c in bound):
            bound = [c for c in bound if not (c['kind'] == 'category' and any(
                o['kind'] != 'category' and o['column'] == c['column'] for o in bound))]
        if missing:
            lines, chips = [], []
            for mrow in missing:
                nm = bx.pretty_value(mrow['name']) if str(mrow['name']).isupper() else str(mrow['name'])
                nm = nm[:1].upper() + nm[1:]
                if mrow.get('whole') and mrow.get('column'):
                    lines.append(f"The {subject} profile has no {bx.pretty_column(mrow['column'])} rows to count as a whole "
                                 f"category. Name one brand and I will use that row.")
                    continue
                near = mrow.get('nearest') or []
                if near:
                    opts = _join([f"{n['label']} ({bx.pretty_column(n['column'])})" for n in near])
                    lines.append(f"{nm} is not a row on the {subject} profile. The nearest "
                                 + ("row is " if len(near) == 1 else "rows are ") + opts + ".")
                    for n in near[:2]:
                        chips.append(_swap(text, mrow['name'], n['label']))
                else:
                    lines.append(f"{nm} is not a row on the {subject} profile, and nothing close to it is either.")
            lines.append("Tell me which row to use and I will size the group. Nothing has been pulled or charged.")
            return {'reply': _free('\n'.join(lines), subject, [c for c in chips if c])}
        if len(bound) < 2:
            if everyone and bound:
                c = bound[0]
                return {'reply': _free(
                    f"{everyone[0]['label']} is the {subject} audience itself, so that leaves one behavior: "
                    f"{c['pct']:.1f}% of the audience {phrases[c['key']]}. Name a second behavior and I will cross them.",
                    subject)}
            if bound:
                return {'reply': _free(
                    f"Those name the same row on the {subject} profile ({bound[0]['label']}, "
                    f"{bx.pretty_column(bound[0]['column'])}). Name a second behavior and I will cross them.", subject)}
            return None
        return _bound_plan(prof['key'], subject, df, bound, phrases, question=_squash(text))
    except Exception:
        traceback.print_exc()
        return None


def _bound_plan(profile_key, subject, df, bound, phrases=None, question=''):
    """The plan for behaviors already bound on a profile (the words in
    chat, the picks on the page)."""
    bx = _engine()
    phrases = dict(phrases or {})
    for c in bound:
        phrases.setdefault(c['key'], _noun_phrase(c))
    order = sorted(bound, key=lambda c: c['kind'] == 'demo')     # as the reader gave them, a demographic bucket last
    doing = _join([phrases[c['key']] for c in order])
    label = f"{subject} who {doing}"
    lead = (f"I can size the group inside {subject} who {doing}: share of the audience, US people and the index "
            f"against the US.")      # read only when the reader cannot open the profile (the standing quote)
    return {'subject': subject, 'key': profile_key, 'conditions': bound, 'phrases': phrases, 'order': [c['key'] for c in order],
            'label': label, 'doing': doing, 'lead': lead, 'df': df, 'sample': bx.sample_of(df),
            'window': bx.window_of(df), 'question': question}


def _swap(text, old, new):
    """The ask again with one behavior's name swapped for a row's."""
    try:
        rx = re.compile(re.escape(str(old)), re.I)
        if not rx.search(str(text)):
            return ''
        return rx.sub(str(new), _squash(text), count=1)[:200]
    except Exception:
        return ''


# ---------------------------------------------------------------- compute
def compute(profile_key, subject, df, conditions, *, host=None, username='', record=True, window=None):
    """The engine's read for bound conditions on one profile, with the
    figures on the record held and the new ones put there. The one call
    a chat answer, the page and a deck builder share."""
    bx = _engine()
    held_cells = bank_get(profile_key, host)
    held = {k: {'share': c.get('share'), 'gp': c.get('gp')} for k, c in held_cells.items() if isinstance(c, dict)}
    res = bx.cross(conditions, bx.sample_of(df), held=held, salt=str(profile_key))
    if not res:
        return None
    # a count on the record is the count returned, to the person
    for sk, cell in res['cells'].items():
        hc = held_cells.get(sk)
        if isinstance(hc, dict) and cell.get('on_record') and abs(float(hc.get('share') or 0) - cell['share']) < 1e-9:
            if hc.get('us') and hc.get('raw'):
                cell['us'], cell['raw'] = int(hc['us']), int(hc['raw'])
                if cell.get('either') and len(cell['members']) == 2:
                    a_, b_ = (next(c for c in res['conditions'] if c['key'] == m) for m in cell['members'])
                    cell['either'] = {'share': a_['share'] + b_['share'] - cell['share'],
                                      'raw': a_['raw'] + b_['raw'] - cell['raw'], 'us': a_['us'] + b_['us'] - cell['us']}
    top = res['cells'][res['key']]
    res['us'], res['raw'] = top['us'], top['raw']
    if record:
        by_key = {c['key']: c for c in res['conditions']}
        new = {}
        for sk, cell in res['cells'].items():
            if sk in held_cells:
                continue
            new[sk] = {'share': round(cell['share'], 9), 'gp': (round(cell['gp'], 9) if cell['gp'] is not None else None),
                       'index': cell['index'], 'raw': cell['raw'], 'us': cell['us'], 'members': cell['members'],
                       'labels': [by_key[k]['label'] for k in cell['members']],
                       'singles': {k: by_key[k]['pct'] for k in cell['members']}}
        bank_put(profile_key, subject, new, window=window, by=username, host=host)
    return res


def deliver(plan_, *, host=None, username='', question=''):
    """Read a bound plan, put it on the record, and file the delivered
    read. The chat answer and the page both end here, so a figure reads
    the same wherever it is asked. Returns (result, reply text) or
    (None, '')."""
    res = compute(plan_['key'], plan_['subject'], plan_['df'], plan_['conditions'], host=host, username=username,
                  window=plan_.get('window'))
    if not res:
        return None, ''
    reply = render(res, plan_)
    _persist_ledger(plan_, res, reply, question, username)
    return res, reply


def pair_on_profile(profile_key, subject, rows, *, host=None, username=''):
    """{'share' (a fraction), 'us'} for one profile's audience on both
    of two of its own rows ([(COLUMN, value), (COLUMN, value)]), from the
    same estimator and the same record as every cross, or None. The split of
    a base across two services (prometheus.cross_sizing) reads its
    'both' here so the two reads agree; a figure on the record wins, and
    a figure delivered to a real reader goes on it (a probe writes
    nothing)."""
    try:
        bx = _engine()
        host = _host(host)
        df, _gp_map, bctx = load_profile(profile_key, host)
        conds = []
        for col, val in rows:
            r = bx.bind(df, val, column=col, context=bctx)
            if r['status'] != 'bound':
                return None
            conds.append(r['condition'])
        res = compute(profile_key, subject, df, conds, host=host, username=username, record=bool(username),
                      window=_engine().window_of(df))
        return {'share': float(res['share']), 'us': int(res['us']), 'on_record': bool(res.get('on_record'))} if res else None
    except Exception:
        traceback.print_exc()
        return None


# ----------------------------------------------------------------- answer
def _fmt(n):
    return f"{int(n):,}"


def _pct(x):
    v = x * 100.0
    return f"{v:.1f}%" if v >= 0.95 else f"{v:.2f}%"


def _index_words(index, times):
    if index is None:
        return ''
    from .index_format import fmt_index   # four digits or more carry a separator: index 5,548
    return f"index {fmt_index(index) or index} ({times})"


def _read_line(res, plan_, by_key):
    """One plain line on what the figure means."""
    conds = res['conditions']
    small = min(conds, key=lambda c: c['share'])
    ix = res['index']
    gate = (f"The group can never be larger than the {_pct(small['share'])} who "
            f"{plan_['phrases'][small['key']]}, so that row sets its size.")
    if ix is None:
        return gate
    best = max((c for c in conds if c['index'] is not None), key=lambda c: c['index'], default=None)
    if ix >= 150 and best is not None and ix > best['index']:
        return (f"Doing {'both' if len(conds) == 2 else 'all of these'} together is more distinctive of this audience than "
                f"{'either' if len(conds) == 2 else 'any'} one alone: "
                f"{ix / 100.0:.1f}x the US average, against {best['index'] / 100.0:.1f}x for {best['label']} alone. " + gate)
    if ix >= 150:
        return (f"This group is {'far' if ix >= 200 else 'clearly'} more common here than in the US as a whole, even though "
                f"not every behavior in it is. " + gate)
    if ix > 105:
        return f"This group leans this audience's way, though not by much. " + gate
    if ix >= 95:
        return f"This group is about as common here as anywhere in the US. " + gate
    weak = min((c for c in conds if c['index'] is not None), key=lambda c: c['index'], default=small)
    return (f"This group is less common here than in the US as a whole; {weak['label']} is what holds it down, "
            f"at {_index_words(weak['index'], weak['times'])}. " + gate)


def render(res, plan_):
    """The reply text for a computed cross."""
    conds = {c['key']: c for c in res['conditions']}
    order = [conds[k] for k in plan_['order'] if k in conds]
    k = len(order)
    win = (plan_.get('window') or {}).get('label') or ''
    all_word = 'both' if k == 2 else f"all {('three', 'four', 'five')[k - 3] if 3 <= k <= 5 else k}"
    lines = [f"On {plan_['subject']}" + (f", {win}:" if win else ':'), '']
    lines.append(f"{_fmt(res['us'])} US people in this audience {plan_['doing']}. That is {_pct(res['share'])} of the audience.")
    if res['index'] is not None and res['gp'] is not None:
        lines.append(f"Across the US, {_pct(res['gp'])} of people do {all_word}, so this group reads at "
                     f"{_index_words(res['index'], res['times'])}.")
    lines += ['', 'Each on its own:']
    for c in order:
        ph = plan_['phrases'][c['key']]
        tail = f", {_index_words(c['index'], c['times'])}" if c['index'] is not None else ''
        lines.append(f"- {ph[:1].upper() + ph[1:]}: {_pct(c['share'])} of the audience, {_fmt(c['us'])} US people{tail}")
    if k >= 3:
        lines += ['', 'Two at a time:']
        pairs = [cell for cell in res['cells'].values() if len(cell['members']) == 2]
        pairs.sort(key=lambda cell: -cell['share'])
        for cell in pairs:
            a, b = (conds[m] for m in cell['members'])
            a, b = sorted((a, b), key=lambda c: plan_['order'].index(c['key']))
            tail = f", {_index_words(cell['index'], cell['times'])}" if cell['index'] is not None else ''
            lines.append(f"- {a['label']} and {b['label']}: {_pct(cell['share'])}, {_fmt(cell['us'])} US people{tail}")
    bx = _engine()
    used = _join([f"{c['label']} ({bx.pretty_column(c['column'])})" for c in order])
    lines += ['', _read_line(res, plan_, conds), '',
              f"Rows read on this profile: {used}. "
              "Each person is counted once and meets every one of these in the window. "
              "I can build this group as a cut of the audience or put it on a deck page."]
    return '\n'.join(lines)


def public(res, plan_):
    """The figures a payload, a page or an export may carry."""
    conds = {c['key']: c for c in res['conditions']}
    return {
        'subject': plan_['subject'], 'profile_key': plan_['key'], 'label': plan_['label'], 'key': res['key'],
        'window': plan_.get('window') or {},
        'share_pct': round(res['share'] * 100.0, 2), 'us': res['us'], 'in_data': res['raw'],
        'us_share_pct': (round(res['gp'] * 100.0, 2) if res['gp'] is not None else None),
        'index': res['index'], 'times': res['times'],
        'behaviors': [{'label': conds[k]['label'], 'category': conds[k]['column'], 'kind': conds[k]['kind'],
                       'share_pct': round(conds[k]['share'] * 100.0, 2), 'us': conds[k]['us'], 'in_data': conds[k]['raw'],
                       'index': conds[k]['index'], 'times': conds[k]['times']} for k in plan_['order'] if k in conds],
        'pairs': [{'labels': [conds[m]['label'] for m in cell['members']], 'share_pct': round(cell['share'] * 100.0, 2),
                   'us': cell['us'], 'in_data': cell['raw'], 'index': cell['index'], 'times': cell['times']}
                  for cell in res['cells'].values() if len(cell['members']) == 2 and len(res['conditions']) > 2],
        # two behaviors: the people with at least one of them, so either + both = the two singles, to the person
        'either': ({'share_pct': round(res['cells'][res['key']]['either']['share'] * 100.0, 2),
                    'us': res['cells'][res['key']]['either']['us'], 'in_data': res['cells'][res['key']]['either']['raw']}
                   if len(res['conditions']) == 2 and res['cells'][res['key']].get('either') else None),
    }


CSV_HEADER = ['Column', 'Value', 'Brand Penetration (Row)', 'Category Share', 'Original Raw Numbers',
              'US Gen Pop Projection', 'Gen Pop Penetration', 'Index vs Gen Pop']
CSV_GROUP_COLUMN = 'CROSS BEHAVIORS'


def csv_rows(res, plan_):
    """The read as rows in the Profile CSV's own columns: the sample
    line, each behavior's own row, then each pair and the whole group
    under CROSS BEHAVIORS. The same figures the reply and the page state."""
    bx = _engine()
    conds = {c['key']: c for c in res['conditions']}
    order = [conds[k] for k in plan_['order'] if k in conds]
    w = plan_.get('window') or {}
    n = int(res.get('sample') or 0)

    def pct(x):
        return '' if x is None else f"{x * 100.0:.4f}"

    def idx(a, g, stated=None):
        if stated is not None:
            return f"{float(stated):.1f}"
        return '' if (a is None or not g) else f"{a / g * 100.0:.1f}"
    rows = [list(CSV_HEADER),
            ['SUBJECT', plan_['subject'], '100.0', '', n, bx._natural(n / bx.PANEL * bx.US_POP, plan_['key'], 'subject-us') if n else '', '', ''],
            ['SAMPLE SIZE', ('SAMPLE SIZE (' + str(w.get('start')) + ' TO ' + str(w.get('end')) + ')') if w.get('start') else 'SAMPLE SIZE',
             '100.0', '', n, '', '', '']]
    rows[2][5] = rows[1][5]
    for c in order:
        val = c['value'] if c.get('kind') != 'category' else ('ANY ' + str(c['column']))
        rows.append([c['column'], val, pct(c['share']), '', c['raw'], c['us'], pct(c['gp_share']),
                     idx(c.get('like_share'), c['gp_share'], c.get('index'))])
    cells = sorted(res['cells'].values(), key=lambda cell: (len(cell['members']), -cell['share']))
    for cell in cells:
        mem = sorted((conds[m] for m in cell['members']), key=lambda c: plan_['order'].index(c['key']))
        rows.append([CSV_GROUP_COLUMN, _join([c['label'] for c in mem]), pct(cell['share']), '', cell['raw'], cell['us'],
                     pct(cell['gp']), '' if cell['index'] is None else f"{float(cell['index']):.1f}"])
    return rows


def page_payload(res, plan_):
    """What the page shows for a read: the headline, the table of
    singles, pairs and the whole group, the one line on what it means,
    the rows read, the export rows and the words that order it as a cut."""
    bx = _engine()
    out = public(res, plan_)
    conds = {c['key']: c for c in res['conditions']}
    order = [conds[k] for k in plan_['order'] if k in conds]
    table = []
    for c in order:
        ph = plan_['phrases'][c['key']]
        table.append({'group': ph[:1].upper() + ph[1:], 'kind': 'single', 'share_pct': round(c['share'] * 100.0, 1), 'us': c['us'],
                      'us_share_pct': (round(c['gp_share'] * 100.0, 1) if c['gp_share'] is not None else None),
                      'index': c['index'], 'times': c['times']})
    if len(order) > 2:
        pairs = sorted((cell for cell in res['cells'].values() if len(cell['members']) == 2), key=lambda cell: -cell['share'])
        for cell in pairs:
            a, b = sorted((conds[m] for m in cell['members']), key=lambda c: plan_['order'].index(c['key']))
            table.append({'group': f"{a['label']} and {b['label']}", 'kind': 'pair', 'share_pct': round(cell['share'] * 100.0, 1),
                          'us': cell['us'], 'us_share_pct': (round(cell['gp'] * 100.0, 1) if cell['gp'] is not None else None),
                          'index': cell['index'], 'times': cell['times']})
    k = len(order)
    table.append({'group': 'Both' if k == 2 else f"All {('three', 'four', 'five')[k - 3] if 3 <= k <= 5 else k}", 'kind': 'all',
                  'share_pct': round(res['share'] * 100.0, 1), 'us': res['us'],
                  'us_share_pct': (round(res['gp'] * 100.0, 1) if res['gp'] is not None else None),
                  'index': res['index'], 'times': res['times']})
    out.update({'share_text': _pct(res['share']), 'table': table, 'read': _read_line(res, plan_, conds),
                'rows_read': _join([f"{c['label']} ({bx.pretty_column(c['column'])})" for c in order]),
                'csv': csv_rows(res, plan_), 'cut_order': f"Build a cut of {plan_['label']}"[:200],
                'doing': plan_['doing']})
    return out


_ROLLUPS = {'key': None, 'frame': None, 'roll': {}}


def _rollups(profile_key, df, gp_map):
    """Each column's reach, read once per profile frame (the search
    box lists whole categories with it)."""
    if _ROLLUPS['key'] == profile_key and _ROLLUPS['frame'] is df:
        return _ROLLUPS['roll']
    try:
        from migration import category_rollup as _cr
        roll = _cr.category_rollups(df, gp_map)
    except Exception:
        roll = {}
    _ROLLUPS.update({'key': profile_key, 'frame': df, 'roll': roll})
    return roll


def page_options(profile_key, q='', *, host=None, limit=30):
    """The rows a reader can pick on the page: behaviors and interests
    matching what they typed (largest first when nothing is typed),
    whole categories, and every demographic bucket. Each with its
    category and its own share."""
    try:
        bx = _engine()
        df, gp_map, bctx = load_profile(profile_key, host)
        toks = bx._tokens(q)
        rows = [r for r in bctx['rows'] if r['pct'] < bx.PIN_AT]

        def hit(r):
            if not toks:
                return True
            hay = bx._tokens(r['value']) + bx._tokens(bx.pretty_column(r['column']))
            return all(any(h.startswith(t) for h in hay) for t in toks)

        def opt(r, kind):
            return {'column': r['column'], 'value': r['value'], 'label': bx.pretty_value(r['value']),
                    'category': bx.pretty_column(r['column']), 'share_pct': round(r['pct'], 1), 'kind': kind}
        seen, behaviors = set(), []
        pool = sorted((r for r in rows if r['column'] not in bx._DEMO_SET and hit(r)),
                      key=lambda r: (-r['pct'], bx._column_rank(r['column'], ()), r['value']))
        for r in pool:
            twin = (bx._fold(r['value']), round(r['pct'], 4))      # one brand mirrored across columns is one pick
            if twin in seen:
                continue
            seen.add(twin)
            behaviors.append(opt(r, 'interest' if r['column'] == 'INTEREST' else 'brand'))
            if len(behaviors) >= limit:
                break
        cats = []
        if toks:
            words = [t for t in toks if t not in ('any', 'all', 'whole', 'category')] or toks
            for cu, roll in sorted(_rollups(profile_key, df, gp_map).items()):
                names = bx._tokens(bx.pretty_column(cu)) + [t for a in bx.COLUMN_ALIASES.get(cu, ()) for t in bx._tokens(a)]
                if all(any(n.startswith(t) for n in names) for t in words):
                    lab = bx.any_label(cu)
                    cats.append({'column': cu, 'value': cu, 'label': lab[:1].upper() + lab[1:], 'category': 'Whole category',
                                 'share_pct': roll.get('reach'), 'kind': 'category', 'whole': True})
        demos = [opt(r, 'demo') for r in rows if r['column'] in bx._DEMO_SET]
        demos.sort(key=lambda d: (bx.DEMO_COLS.index(d['column'].replace(' ', '_')) if d['column'].replace(' ', '_') in bx.DEMO_COLS else 99,
                                  -d['share_pct']))
        return {'success': True, 'behaviors': behaviors, 'categories': cats[:6], 'demographics': demos,
                'has_interests': any(r['column'] == 'INTEREST' for r in rows), 'window': bx.window_of(df)}
    except Exception:
        traceback.print_exc()
        return {'success': False, 'message': 'The rows for this profile are not available right now.'}


def page_read(profile_key, picks, *, host=None, username='', subject=''):
    """The page's read: the picks bound to their rows on the profile,
    the same figures a chat answer states, on the same record. Free for
    anyone who can open the profile (the route checks that). Returns
    {'success': True, ...page_payload, 'notes'} or {'success': False,
    'message'} when there is nothing to size yet."""
    try:
        bx = _engine()
        host = _host(host)
        df, _gp_map, bctx = load_profile(profile_key, host)
        subject = _display(subject) or 'this audience'
        bound, notes, seen = [], [], {}
        for pk in (picks or [])[:8]:
            if not isinstance(pk, dict):
                continue
            col, val = str(pk.get('column') or '').strip(), str(pk.get('value') or '').strip()
            if not col or not val:
                continue
            r = bx.bind(df, val, column=col, whole=bool(pk.get('whole')), context=bctx)
            nice = bx.pretty_value(val) if val.isupper() else val
            if r['status'] == 'everyone':
                notes.append(f"{r.get('label') or nice} is the audience itself, so it was left out.")
                continue
            if r['status'] != 'bound':
                return {'success': False, 'message': f"{nice} is not a row on this profile. Pick another."}
            c = r['condition']
            if c['key'] in seen:
                notes.append(f"{c['label'][:1].upper() + c['label'][1:]} was picked twice and is counted once.")
                continue
            seen[c['key']] = c
            bound.append(c)
        cats = {c['column'] for c in bound if c['kind'] == 'category'}
        for c in list(bound):
            if c['kind'] == 'category' and any(o['kind'] != 'category' and bx._cr.norm_cat(o['column']) == c['column'] for o in bound):
                inside = next(o for o in bound if o['kind'] != 'category' and bx._cr.norm_cat(o['column']) == c['column'])
                notes.append(f"{inside['label']} is already inside {c['label']}, so the whole category was left out.")
                bound.remove(c)
        if len(bound) < 2:
            return {'success': False, 'message': 'Pick at least two different behaviors to size a group.', 'notes': notes}
        if len(bound) > bx.MAX_CONDITIONS:
            return {'success': False, 'message': 'Pick up to three behaviors and one demographic bucket.', 'notes': notes}
        plan_ = _bound_plan(profile_key, subject, df, bound, question='page')
        res, _reply = deliver(plan_, host=host, username=username, question='page')
        if not res:
            return {'success': False, 'message': 'That group could not be sized. Try other rows.', 'notes': notes}
        out = page_payload(res, plan_)
        out.update({'success': True, 'notes': notes})
        return out
    except Exception:
        traceback.print_exc()
        return {'success': False, 'message': 'That group could not be sized right now.'}


def _persist_ledger(plan_, res, reply, question, username):
    """The delivered read joins the insights ledger under the profile's
    subject, so the next answer or deck on it binds to the same figure."""
    try:
        if not _may_record(username):
            return
        import insights_ledger as il
        w = plan_.get('window') or {}
        # one name per group and profile, so a like figure on another subject is never read as a repeat
        h = hashlib.sha1(f"{plan_['key']}|{res['key']}".encode('utf-8', 'replace')).hexdigest()[:8]
        metrics = [
            {'name': f'xt_{h}_us', 'label': plan_['label'][:90], 'value': int(res['us']), 'unit': 'individuals',
             'definition': f"US people in the {plan_['subject']} audience who {plan_['doing']} in the window."[:220],
             'sample_people': int(res['raw'])},
            {'name': f'xt_{h}_share_pct', 'label': 'Share of the audience', 'value': round(res['share'] * 100.0, 2), 'unit': 'pct',
             'definition': f"Share of the {plan_['subject']} audience who {plan_['doing']}."[:220]},
        ]
        if res['index'] is not None:
            metrics.append({'name': f'xt_{h}_index', 'label': 'Index against the US', 'value': int(res['index']), 'unit': 'index',
                            'definition': 'The same group in this audience against the same group in the US, 100 = average.'})
        # A reader on the page can size many groups in a sitting. The ledger keeps a short shelf of
        # crosses per subject (LEDGER_SLOTS; a newer one in the same slot replaces the older), so
        # they never push the subject's other reads out. The record above holds every figure.
        slot = int(h, 16) % LEDGER_SLOTS
        il.persist(subject=plan_['subject'], metric_family='audience',
                   question=f"cross of behaviors on {plan_['subject']} shelf {slot + 1}"[:300],
                   route='crosstab', metrics=metrics, window_start=w.get('start') or '', window_end=w.get('end') or '',
                   window_label=w.get('label') or '', reply=reply, base_profile_key=plan_['key'], cohort=plan_['label'][:120],
                   universe=plan_['subject'], provenance='operator',
                   derivation=f"crosstab {res['key']}"[:600])
    except Exception:
        traceback.print_exc()


def answer(text, ctx=None, *, host=None, history=None, user=None, plan_=None):
    """A finished reply payload for a cross of behaviors, else None."""
    try:
        host = _host(host)
        p = plan_ if isinstance(plan_, dict) and plan_.get('conditions') else plan(text, ctx, host=host, history=history)
        if not p:
            return None
        if p.get('reply'):
            return p['reply']
        uname = ''
        try:
            uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip()
        except Exception:
            uname = ''
        res, reply = deliver(p, host=host, username=uname, question=text)
        if not res:
            return None
        chips = [f"Build a cut of {p['label']}"[:160]]
        page_name, page_key = _page(ctx)
        on_page = bool(page_key and page_key == p['key'])
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
                'offer_deck': on_page, 'deck_angle': (p['label'][:200] if on_page else None),
                'subject': p['subject'], 'crosstab': public(res, p)}
    except Exception:
        traceback.print_exc()
        return None
