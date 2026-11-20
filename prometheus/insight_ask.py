"""An open-ended insight request is the analyst's job (Jenna 2026-11-19).

Mike Pusateri at Bent Pixels, 2026-11-19 17:18 UTC: "what's a unique
insight we can email to Lego around lego shopping behaviour this month"
drew "Nothing has landed in this chat yet for me to send. Ask me a data
question first and then say Email me this file." The word "email" made
the send-a-file check take the message (it reads "email ... this" within
a few words as an order to email the last file), ahead of every lane
that reads a question.

Jenna, verbatim: "prometheus shouild find cool data it could give that
would be impressive to lego doe that make sense."

Two rules live here.

1. WHO THE SEND IS FOR. "Email", "send", "share", "pitch", "take to",
   "show" followed by a third party ("email to Lego", "send to the
   client", "something we can take to Nike") says what the READER will do
   with the answer. The send-a-file action is an order to Prometheus to
   send something that already exists to the reader ("email me this
   file", "send me that"). Prometheus never emails a third party.
   ``reader_will_share`` reads the clause; ``not_a_send`` stands the
   send-a-file check down for a request for something new.

2. THE REQUEST ITSELF. "What's a unique / interesting / cool / surprising
   insight about X", "what would impress X", "find me something we can
   pitch X", "what stands out about X's shoppers this month", "give me a
   hook for X": the reader wants Prometheus to look and pick. ``parse``
   reads the subject, who it is for, the angle and the window. When the
   library holds the subject, ``core_hook`` reads the whole file (every
   row against the US, not the top of each section), hands the strongest
   rows to the same analysis pass an open profile gets, and shapes the
   reply: the one strongest finding first, why it matters to that brand,
   the supporting numbers each with its US comparison, a forward-ready
   version the reader can paste into their own email, and the offer of a
   one-pager. Nothing is sent to the third party.

What it costs is unchanged. An insights question on a file we hold is
talk on that file: metered like any other turn, never quoted
(prometheus.priced_read.is_talk). A subject the library does not hold is
left to the standing flow, which quotes the custom read with Approve and
Cancel; the brief below rides that read's prompt so it lands in the same
shape. A question the price rules read as a put-together read (a ranked
list, a cross, a table) is never taken here: it keeps its quote.

Nothing here takes a request field. Kill switch ``PM_INSIGHT_ASK=0`` on
the host only. Regression: scripts/test_pm_insight_ask.py.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

__all__ = ['enabled', 'reader_will_share', 'is_send_order', 'not_a_send', 'stands_down', 'parse', 'wants', 'findings',
           'findings_block', 'brief', 'read_brief', 'offer_lead', 'compose', 'finish', 'core_hook', 'window_plain', 'window_line',
           'window_chip', 'asked_month', 'ROUTE', 'FORWARD_LABEL', 'ONE_PAGER_CHIP', 'ONE_PAGER_LINE']

ROUTE = 'insight_ask'
FORWARD_LABEL = 'Forward-ready version:'
ONE_PAGER_CHIP = 'Turn this into a one-pager'
ONE_PAGER_LINE = 'Say the word and I will turn this into a one-pager you can attach.'
MAX_LEN = 280


def enabled():
    return str(os.environ.get('PM_INSIGHT_ASK', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(str(text or '').replace('\u2019', "'").replace('\u2018', "'").split())


# ------------------------------------------------------------------ who the send is for
_ADDR_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SELF_RX = re.compile(
    r"^(?:me|myself|us|ourselves|my\s*self|my\s+(?:own\s+)?(?:e-?mail|inbox|address|account|phone|desk)|our\s+(?:e-?mail|inbox)|"
    r"the\s+(?:e-?mail|address)\s+on\s+(?:file|my\s+account))\b", re.I)
# verbs whose object is the one being shown: "pitch Nike", "email Lego"
_SHARE_DIRECT = r"e-?mail(?:ing)?|pitch(?:ing)?"
# verbs that take "to" or "with" before the one being shown, or a name written as a name: "send to the client", "show Nike"
_SHARE_NAMED = (r"mail(?:ing)?|send(?:ing)?|shar(?:e|ing)|forward(?:ing)?|present(?:ing)?|show(?:ing)?|tak(?:e|ing)|bring(?:ing)?|"
                r"giv(?:e|ing)|hand(?:ing)?|pass(?:ing)?|tell(?:ing)?")
# verbs that say it only with "to", "with", "into" or "in front of": "use with Nike", "put in front of the client"
_SHARE_PREP = r"us(?:e|ing)|put(?:ting)?|get(?:ting)?|walk(?:ing)?|go(?:ing)?|drop(?:ping)?|post(?:ing)?|say(?:ing)?"
# the reader is the one who shares: "we can email to", "I could pitch", "something to send to", "worth sharing with"
_READER_SHARES_RX = re.compile(
    r"\b(?:(?:(?:that\s+)?(?:we|i|my\s+team|our\s+team|the\s+team|sales|you\s+and\s+i)\s*(?:can|could|should|might|may|would|will|"
    r"want\s+to|need\s+to|have\s+to|get\s+to|plan\s+to|are\s+going\s+to|am\s+going\s+to|'ll|'d)"
    r"|(?:can|could|should|might|would|shall|do)\s+(?:we|i))\s+(?:then\s+|all\s+|just\s+|also\s+)?"
    r"(?P<verb>" + _SHARE_DIRECT + r"|" + _SHARE_NAMED + r"|" + _SHARE_PREP + r")"
    r"|(?:to|worth|for)\s+(?P<verb2>" + _SHARE_DIRECT + r"|" + _SHARE_NAMED + r"))\b"
    r"(?P<rest>[^.?!\n]{0,80})", re.I)
_DIRECT_RX = re.compile(r"^(?:" + _SHARE_DIRECT + r")$", re.I)
_PREP_ONLY_RX = re.compile(r"^(?:" + _SHARE_PREP + r")$", re.I)
# an order to Prometheus to send something new to someone else: "can you email Lego a unique insight about their shoppers"
_ORDER_TO_THIRD_RX = re.compile(
    r"^\W*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?(?P<verb>e-?mail|send|pitch|show|tell)\s+"
    r"(?P<rest>(?!me\b|us\b|it\b|this\b|that\b|these\b|those\b|them\b|over\b|the\s|my\s|your\s|our\s)[^.?!\n]{1,80})", re.I)
# "for our pitch to Nike", "for the Lego meeting", "in front of the client"
_FOR_MEETING_RX = re.compile(
    r"\b(?:for|in|at|ahead\s+of|before)\s+(?:our|my|the|a|an|this|that|next\s+week'?s?|tomorrow'?s?)\s+"
    r"(?:(?P<who1>[A-Za-z0-9][\w&+.'-]*(?:\s+[A-Za-z0-9&][\w&+.'-]*){0,2})\s+)?"
    r"(?:pitch|meeting|call|deck|proposal|presentation|outreach|e-?mail|note|intro|conversation|chat|renewal|review|qbr|rfp)"
    r"(?:\s+(?:to|with|for)\s+(?P<rest>[^.?!\n]{0,60}))?"
    r"|\bin\s+front\s+of\s+(?P<rest2>[^.?!\n]{0,60})", re.I)
# an order to Prometheus to send something that exists: "email me this file", "send me that deck", "email this to me",
# "please forward that to the client". The standing send lanes own every one of these.
_SEND_ORDER_RX = re.compile(
    r"^\W*(?:ok(?:ay)?[, ]+|yes[, ]+|great[, ]+|thanks?[, ]+|now[, ]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+|will\s+you\s+|go\s+ahead\s+and\s+)?"
    r"(?:please\s+)?(?:e-?mail|mail|send|forward|shoot)\b", re.I)
_EXISTING_OBJ_RX = re.compile(
    r"^\W*(?:[\w' ,]{0,40}?\b)?(?:e-?mail|mail|send|forward|shoot)\s+(?:me\s+|us\s+|over\s+)?(?:a\s+copy\s+of\s+)?"
    r"(?:it|this|that|these|those|them|the\s+(?:file|csv|deck|read|report|data|answer|results?|numbers|spreadsheet|export|profile|"
    r"one-?pager|pdf|table|breakdown)|(?:this|that|the|my|your|our)\s+[\w' -]{0,40}?"
    r"(?:file|csv|deck|read|report|data|answer|results?|numbers|spreadsheet|export|profile|one-?pager|pdf|table|breakdown))\b", re.I)
_GENERIC_WHO = frozenset((
    'client', 'clients', 'customer', 'customers', 'brand', 'brands', 'prospect', 'prospects', 'buyer', 'buyers', 'partner',
    'partners', 'agency', 'team', 'boss', 'manager', 'cmo', 'ceo', 'cfo', 'coo', 'vp', 'svp', 'evp', 'president', 'founder',
    'founders', 'board', 'leadership', 'execs', 'executives', 'exec', 'sponsor', 'sponsors', 'advertiser', 'advertisers',
    'marketer', 'marketers', 'stakeholders', 'studio', 'network', 'label', 'them', 'him', 'her', 'they', 'people', 'folks',
    'room', 'sales', 'colleagues', 'colleague', 'investors', 'investor'))
_WHO_STOP = frozenset((
    'about', 'around', 'on', 'regarding', 're', 'in', 'this', 'that', 'these', 'those', 'for', 'from', 'over', 'during', 'today',
    'tomorrow', 'tonight', 'right', 'now', 'next', 'last', 'and', 'or', 'but', 'so', 'because', 'when', 'if', 'which', 'who',
    'whom', 'as', 'by', 'before', 'after', 'based', 'using', 'showing', 'please', 'asap', 'soon', 'later', 'lately',
    'recently', 'currently', 'is', 'are', 'was', 'will', 'would', 'can', 'could', 'should', 'might', 'do', 'does', 'did',
    'then', 'instead', 'too', 'also', 'again', 'per', 'via', 'into', 'out', 'up', 'at', 'with', 'to', 'of', 'what', 'how',
    'why', 'where', 'we', 'i', 'you', 'they', 'it', 'something', 'anything', 'thing', 'things', 'insight', 'insights',
    'a', 'an', 'some', 'one', 'any', 'their', 'its', 'our', 'my', 'your', 'more', 'new', 'every', 'each'))
_WHO_TAIL_RX = re.compile(
    r"\s+(?:team|teams|folks|people|guys|marketing|brand|insights?|leadership|execs?|group|crew|side|org|organization|"
    r"corporate|hq|headquarters|pitch|meeting|deck|call)\s*$", re.I)
_LEAD_DET_RX = re.compile(r"^(?:the|our|my|a|an|their|his|her|some|any|all|both|that|this)\s+", re.I)
_OBJ_LEAD_RX = re.compile(
    r"^(?:it|this|that|these|those|them|one|some|something|anything|(?:a|an|the|this|that|some)\s+(?:note|line|e-?mail|stat|fact|"
    r"finding|insight|number|point|story|deck|slide|file|one-?pager|message))\s+", re.I)
_PREP_RX = re.compile(r"^(?:over\s+)?(?:to|with|at|for|in\s+front\s+of|into|on)\s+", re.I)


def _name_at(rest, *, bare='any'):
    """The name a clause leads with ('to Lego around lego shopping ...' -> 'Lego'), '' for the reader's own
    self ('me', 'my inbox'). ``bare`` says what a name with no "to" or "with" before it must look like: 'any',
    'name' (written as a name, or the client / the team) or 'none'. Never raises."""
    s = str(rest or '').strip(' ,:;-')
    s = _OBJ_LEAD_RX.sub('', s).strip()
    had_prep = bool(_PREP_RX.match(s))
    s = _PREP_RX.sub('', s).strip()
    if not s or (not had_prep and bare == 'none') or _SELF_RX.match(s):
        return ''
    had_det = bool(_LEAD_DET_RX.match(s))
    s = _LEAD_DET_RX.sub('', s).strip()
    out = []
    for tok in re.split(r"\s+", s):
        word = tok.strip(",;:()[]\"").rstrip('.?!')
        low = re.sub(r"'s$", '', word.lower())
        if not word or low in _WHO_STOP:
            break
        out.append(re.sub(r"'s$", '', word))
        if (tok != word and not tok.endswith("'s")) or len(out) >= 5:
            break
    name = ' '.join(out).strip()
    prev = None
    while prev != name and len(name.split()) > 1:
        prev = name
        name = _WHO_TAIL_RX.sub('', name).strip()
    if not re.search(r"[A-Za-z0-9]", name):
        return ''
    if not had_prep and bare == 'name' and not (name[:1].isupper() or (had_det and name.lower() in _GENERIC_WHO)):
        return ''
    return name


def reader_will_share(text):
    """The third party the reader says they will take the answer to
    ('Lego' in "an insight we can email to Lego", 'the client' in
    "something to send to the client"), else ''. A send to the reader's
    own self, or to a typed address, is ''."""
    t = _clean(text)
    if not t or _ADDR_RX.search(t):
        return ''
    for m in _READER_SHARES_RX.finditer(t):
        verb = m.group('verb') or m.group('verb2') or ''
        who = _name_at(m.group('rest'), bare=('any' if _DIRECT_RX.match(verb) else ('none' if _PREP_ONLY_RX.match(verb) else 'name')))
        if who:
            return _who_label(who)
    m = _FOR_MEETING_RX.search(t)
    if m:
        who = _name_at('to ' + (m.group('rest') or m.group('rest2') or '')) if (m.group('rest') or m.group('rest2')) else ''
        who = who or str(m.group('who1') or '').strip()
        if who and who.lower() not in _WHO_STOP and not _SELF_RX.match(who):
            return _who_label(who)
    return ''


def _who_label(who):
    w = str(who or '').strip()
    if w.lower() in ('them', 'him', 'her', 'they', 'people', 'folks', 'sales'):
        return 'them' if w.lower() == 'they' else w.lower()
    return ('the ' + w.lower()) if w.lower() in _GENERIC_WHO else w


def _is_generic_who(who):
    return re.sub(r"^the\s+", '', str(who or '').strip().lower()) in _GENERIC_WHO


def is_send_order(text):
    """An order to send something that already exists ('email me this
    file', 'send me that deck', 'email this to me', 'please forward that
    to the client'). The standing send lanes own it."""
    t = _clean(text)
    return bool(t) and bool(_SEND_ORDER_RX.match(t)) and bool(_EXISTING_OBJ_RX.match(t))


_CONTENT_REQUEST_RX = re.compile(
    r"^\W*(?:what|which|who|where|how|why|is\s+there|are\s+there|any|anything|something|do\s+(?:you|we)|does|can\s+(?:you|we|i)|"
    r"could\s+(?:you|we|i)|give|find|get|show|tell|pull|dig|surface|spot|need|want|looking|help|i\s+(?:need|want|am|'m|would|'d)|"
    r"we\s+(?:need|want|are|'re|would|'d)|got|have\s+you|write|draft|suggest|recommend|share|"
    r"send\s+(?:me|us)\s+(?:something|anything|a|an|some|one|two|three)|(?:please\s+)?(?:can|could|would|will)\s+you)\b", re.I)


def stands_down(text):
    """True when a message that mentions emailing or sending is a request
    for something new the reader will share, never an order to send the
    last file. A typed address, and an order on a thing that exists, keep
    the send lanes."""
    try:
        t = _clean(text)
        if not t or not enabled() or _ADDR_RX.search(t) or is_send_order(t):
            return False
        return bool(parse(t)) or bool(reader_will_share(t))
    except Exception:
        return False


def not_a_send(fn):
    """Wrap the send-a-file intent check: a request for something new the
    reader will take to a third party is never that intent."""
    def _intent(text, *a, **k):
        if stands_down(text):
            return False
        return fn(text, *a, **k)
    _intent.__name__ = getattr(fn, '__name__', 'intent')
    _intent.__doc__ = getattr(fn, '__doc__', None)
    return _intent


# ------------------------------------------------------------------ the request itself
_ADJ_SURPRISE = (r"(?:unique|interesting|cool|surprising|compelling|striking|impressive|unexpected|non-?obvious|juicy|killer|"
                 r"stand-?out|eye-?opening|eye-?catching|counter-?intuitive|crazy|wild|remarkable|unusual|exciting|fun|neat|"
                 r"fascinating|mind-?blowing|wow|differentiat(?:ed|ing)|fresh|novel|coolest|craziest|wildest|juiciest|"
                 r"most\s+(?:interesting|surprising|impressive|compelling|striking|unexpected|unique))")
_ADJ_ANY = (r"(?:" + _ADJ_SURPRISE[3:-1] + r"|strong|strongest|great|good|best|top|big|biggest|new|sharp|sharpest|smart|key|"
            r"powerful|notable|valuable|meaningful|solid|quick|nice|real|single|one)")
_NOUN_STRONG = (r"(?:insights?|nuggets?|hooks?|angles?|talking\s+points?|take-?aways?|gems?|tid-?bits?|aha(?:\s+moments?)?|"
                r"learnings?|conversation\s+starters?|ice-?breakers?|openers?|headline\s+(?:finding|stat|number)s?)")
_NOUN_WEAK = (r"(?:findings?|stats?|statistics?|data\s*points?|facts?|numbers?|stor(?:y|ies)|headlines?|observations?|signals?|"
              r"proof\s+points?|pieces?\s+of\s+(?:data|info(?:rmation)?)|bits?\s+of\s+data|reads?|things?)")
_ASK = (r"(?:what(?:'s|\s+is|\s+are|\s+would\s+be|s)?|which|give\s+(?:me|us)|find\s+(?:me|us)?|get\s+(?:me|us)|show\s+(?:me|us)|"
        r"tell\s+(?:me|us)|send\s+(?:me|us)|share|pull\s+(?:me|us|out|up)?|dig\s+up|surface|spot|identify|come\s+up\s+with|got|"
        r"any|do\s+(?:you|we)\s+have|have\s+you\s+got|is\s+there|are\s+there|can\s+(?:you|we|i)\s+(?:find|get|give|have|pull|share|"
        r"surface|spot|dig\s+up|come\s+up\s+with|see)|could\s+(?:you|we)\s+(?:find|give|get|pull|share)|i\s+(?:need|want|would\s+like|'d\s+like|"
        r"am\s+looking\s+for|'m\s+looking\s+for)|we\s+(?:need|want|are\s+looking\s+for|'re\s+looking\s+for)|looking\s+for|need|want|"
        r"help\s+me\s+find|write\s+(?:me|us)?|draft\s+(?:me|us)?|suggest|recommend|"
        r"(?:e-?mail|send|pitch|show|tell)\s+(?!me\b|us\b|it\b|this\b|that\b)[\w&'.-]+(?:\s+[\w&'.-]+)?)")
_DET = r"(?:(?:a|an|one|some|any|the|two|three|2|3|a\s+few|a\s+couple(?:\s+of)?|our|my|your)\s+)?"
# between the ask and the noun only "me", "us", "please": a name there ("can we see New Heights podcast insights on
# Amazon") makes "insights" a word for data, never a request for the striking one
_GAP = r"(?:\s+(?:me|us|please|for\s+me|for\s+us))?\s+"
_INTENS = r"(?:(?:really|truly|genuinely|super|very|most|more|single|one|the)\s+)*"
# 1. an insight, a hook, an angle, a talking point: the noun says it
_F_STRONG_RX = re.compile(r"\b" + _ASK + _GAP + _DET + _INTENS + r"(?:" + _ADJ_ANY + r"[\s,/]+(?:and\s+|or\s+)?){0,3}"
                          + _NOUN_STRONG + r"\b", re.I)
# 2. a stat, a fact, a finding, a story: only with a word that asks for the striking one
_F_WEAK_RX = re.compile(r"\b" + _ASK + _GAP + _DET + _INTENS + r"(?:" + _ADJ_ANY + r"[\s,/]+(?:and\s+|or\s+)?){0,2}"
                        + _ADJ_SURPRISE + r"[\s,/]+(?:(?:and|or)\s+)?(?:" + _ADJ_ANY + r"\s+)?" + _NOUN_WEAK + r"\b", re.I)
# 3. "something / anything" the reader will take somewhere, or that is striking
_F_SOMETHING_RX = re.compile(
    r"\b(?:" + _ASK + r"\b[^.?!\n]{0,24}?\b)?(?:something|anything|some\s+things?|a\s+few\s+things|one\s+thing|a\s+thing|things)\b"
    r"(?P<tail>[^?!\n]{0,120})", re.I)
# a stat, a fact, a story the reader says they will take to someone: the share clause says it is the striking one
_F_WEAK_SHARE_RX = re.compile(
    r"\b" + _ASK + _GAP + _DET + _INTENS + r"(?:" + _ADJ_ANY + r"[\s,/]+(?:and\s+|or\s+)?){0,3}"
    r"(?:findings?|stats?|statistics?|data\s*points?|facts?|stor(?:y|ies)|headlines?|observations?|signals?|proof\s+points?)\b", re.I)
_SOMETHING_ADJ_RX = re.compile(r"^\s*(?:really\s+|truly\s+|genuinely\s+|super\s+)?" + _ADJ_SURPRISE + r"\b", re.I)
_HAS_SURPRISE_RX = re.compile(r"\b" + _ADJ_SURPRISE + r"\b", re.I)
# "what can we email Lego about their shoppers", "what should I pitch Nike"
_F_WHAT_SHARE_RX = re.compile(r"^\W*(?:so\s+|ok(?:ay)?[, ]+)?what(?:\s+else)?\s+(?:can|could|should|would|might|do|shall)\s+(?:we|i)\b", re.I)
# 4. what would impress X, what would X want to know, what does X not know about its own shoppers
_IMPRESS = (r"(?:impress|wow|surprise|interest|excite|intrigue|delight|matter\s+(?:most\s+)?to|resonate\s+(?:most\s+)?with|land\s+(?:best\s+)?with|"
            r"grab|hook|catch\s+the\s+eye\s+of|get\s+the\s+attention\s+of|blow\s+away|turn\s+heads\s+at|be\s+(?:most\s+)?(?:interesting|"
            r"impressive|useful|valuable|new|news|surprising|compelling)\s+to)")
_F_IMPRESS_RX = re.compile(
    r"\b(?:what|which\s+(?:finding|stat|number|insight)s?|how)\b[^.?!\n]{0,30}?\b(?:would|will|could|might|can|do\s+(?:we|i)|should)\s+"
    r"(?:we\s+|i\s+)?(?:really\s+|most\s+|actually\s+|best\s+)?" + _IMPRESS + r"\s+(?P<who>[^?!\n]{1,80})", re.I)
_F_WOULD_FIND_RX = re.compile(
    r"\bwhat\s+(?:would|will|might|could|does|do)\s+(?P<who>[^?!\n]{1,60}?)\s+(?:find|consider|think\s+is|see\s+as)\s+"
    r"(?:most\s+|really\s+)?" + _ADJ_ANY + r"\b", re.I)
_F_WANT_KNOW_RX = re.compile(
    r"\bwhat\s+(?:would|will|might|could|does|do|should)\s+(?P<who>[^?!\n]{1,60}?)\s+(?:most\s+)?(?:want|love|like|need|care|be\s+"
    r"(?:surprised|interested|excited|glad|keen))\s+to\s+(?:know|see|hear|learn)\b", re.I)
_F_NOT_KNOW_RX = re.compile(
    r"\bwhat\s+(?:does|do|doesn't|don't|does\s+not|do\s+not|can't|cannot|can|might|would)\s+(?P<who>[^?!\n]{1,60}?)\s+(?:not\s+)?"
    r"(?:already\s+)?(?:know|see|realize|realise|have)\s+(?:about|in|on)\s+(?:its|their|his|her|the)\s+(?:own\s+)?", re.I)
# 5. what stands out about <a named subject>
_F_STANDS_OUT_RX = re.compile(
    r"\b(?:what|anything|something|what\s+else)\s*(?:'s|\s+is|\s+are|\s+that)?\s*(?:really\s+|most\s+|the\s+most\s+)?"
    r"(?:stands?\s+out|standing\s+out|jumps?\s+out|jumping\s+out|pops?(?:\s+out)?|sticks?\s+out|leaps?\s+out|"
    r"surprising|interesting|unusual|unique|notable|striking|unexpected|cool|special|remarkable|weird|odd|distinctive|different)"
    r"(?:\s+(?:to\s+you|the\s+most|most))?\s+(?:about|with|in|on|for|among|across)\s+(?P<subj>[^?!\n]{1,90})", re.I)
# never the family: an order, a deck or file order, a definition, a price, a how-to
_NOT_FAMILY_RX = re.compile(
    r"\b(?:linkedin|social\s+post|tweet|caption|press\s+release|blog\s+post|newsletter)\b"
    r"|^\W*(?:build|create|make|run|pull|queue|start|launch|generate|cut|refresh|export|download)\s+(?:me\s+|us\s+)?(?:a|an|the|this|that|my|our|new)?\s*"
    r"(?:\w+\s+){0,3}(?:profile|deck|powerpoint|presentation|slides?|journey|valuation|tracker|cut|report|csv|file|twin|room)\b"
    r"|\bhow\s+(?:do|does|can|should)\s+(?:i|we|you|prometheus|crosswalk)\s+(?:use|read|find|get|calculate|measure|define)\b"
    r"|\bwhat\s+(?:does|do)\s+[\w ]{1,30}\s+(?:mean|cost|stand\s+for)\b"
    r"|\b(?:insights?\s+(?:tab|page|ledger|deck|report)|analysis\s+iq|key\s+insights\s+(?:section|tab|card))\b", re.I)

_AUD = (r"(?:shopping\s+behaviou?rs?|shopping\s+habits?|buying\s+behaviou?rs?|buying\s+habits?|purchase\s+behaviou?rs?|"
        r"purchas(?:es|ing)(?:\s+behaviou?rs?)?|shopp(?:ers?|ing)|buy(?:ers?|ing)|customers?|consumers?|audiences?|fans?|fan\s*base|"
        r"viewers?|viewing(?:\s+behaviou?rs?|\s+habits?)?|viewership|watchers?|listeners?|players?|gamers?|followers?|users?|"
        r"subscribers?|members?|guests?|diners?|drinkers?|readers?|owners?|drivers?|travell?ers?|visitors?|base|behaviou?rs?|habits?|data|"
        r"people|crowd|community|household[s]?)")
_AUD_RX = re.compile(r"^" + _AUD + r"$", re.I)
_SUBJ_PREP_RX = re.compile(r"\b(?:about|around|regarding|concerning|re:|on|into|for|from|among|within|across|inside|in|with)\s+", re.I)
_DEIXIS_RX = re.compile(
    r"^(?:this|that|these|those|the|our|my|their|its|his|her)?\s*(?:here|it|them|they|this|that|one|ones|audience|profile|brand|"
    r"file|data|page|shoppers?|customers?|viewers?|fans?|buyers?|users?|people|group|cohort|segment|read|report|study|title|show|"
    r"series|film|movie|talent|creator|client|campaign)s?$", re.I)
_WINDOW_RX = re.compile(
    r"\b(?P<w>this\s+(?:month|week|quarter|year|season|holiday(?:\s+season)?|fall|autumn|winter|spring|summer)|"
    r"last\s+(?:month|week|quarter|year|30\s+days|7\s+days|90\s+days)|past\s+(?:month|week|quarter|year|30\s+days|7\s+days|90\s+days|few\s+weeks)|"
    r"(?:right\s+now|at\s+the\s+moment|currently|lately|recently|these\s+days|today|so\s+far\s+this\s+(?:month|year)|month\s+to\s+date|"
    r"mtd|ytd|year\s+to\s+date)|(?:in|during|for)\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+20\d{2})?)\b", re.I)
_ANGLES = (
    ('shopping', re.compile(r"\b(?:shop\w*|buy\w*|bought|purchas\w*|retail\w*|spend\w*|basket|cart|checkout|e-?comm\w*|stores?|gift\w*|"
                            r"customers?|consumers?|brands?\s+they)\b", re.I)),
    ('viewing', re.compile(r"\b(?:watch\w*|view\w*|stream\w*|tv|shows?|series|films?|movies?|binge\w*|tun(?:e|ing)\s+in)\b", re.I)),
    ('social', re.compile(r"\b(?:social|creators?|influencers?|tiktok|instagram|youtube|follow\w*|posts?)\b", re.I)),
    ('listening', re.compile(r"\b(?:music|listen\w*|songs?|artists?|playlists?|podcasts?)\b", re.I)),
    ('gaming', re.compile(r"\b(?:gam(?:e|es|ing|ers?)|play(?:ers?|ing))\b", re.I)),
    ('dining', re.compile(r"\b(?:eat\w*|din(?:e|ing|ers?)|restaurants?|qsr|food|snack\w*|drink\w*)\b", re.I)),
    ('travel', re.compile(r"\b(?:travel\w*|trips?|flights?|hotels?|vacation\w*)\b", re.I)),
    ('money', re.compile(r"\b(?:bank\w*|credit|pay(?:ment|ing)?s?|invest\w*|insur\w*|financ\w*)\b", re.I)),
)
# a first word that is never the name of a subject: "in the data", "for the pitch", "on the dashboard"
_NOT_A_NAME = frozenset((
    'data', 'numbers', 'number', 'file', 'files', 'profile', 'profiles', 'read', 'reads', 'report', 'reports', 'audience', 'results',
    'dashboard', 'panel', 'library', 'market', 'category', 'space', 'industry', 'world', 'way', 'terms', 'general', 'particular',
    'mind', 'hand', 'case', 'fact', 'light', 'context', 'email', 'e-mail', 'pitch', 'meeting', 'deck', 'call', 'note', 'front',
    'order', 'addition', 'short', 'total', 'time', 'month', 'week', 'year', 'quarter', 'window', 'period', 'moment', 'end',
    'us', 'me', 'them', 'you', 'our', 'my', 'here', 'there', 'now', 'today', 'detail', 'depth', 'one', 'two', 'three', 'us.',
    'example', 'instance', 'sure', 'real', 'there', 'their', 'its', 'his', 'her', 'who', 'what', 'which', 'how', 'why', 'where',
    'in', 'on', 'at', 'for', 'to', 'with', 'of', 'from', 'by', 'about', 'around', 'a', 'an', 'some', 'any', 'all', 'each', 'every',
    'trends', 'trend', 'rankers', 'ranker', 'news', 'headlines', 'culture', 'streaming', 'tv', 'social', 'search', 'retail',
    'gaming', 'music', 'sports', 'general', 'everyone', 'america', 'americans', 'consumers', 'people'))
_NAME_JOIN = frozenset(('of', 'and', '&', 'the', 'vs', 'x', 'on', 'in'))
_NAME_HARD_STOP = frozenset((
    'we', 'i', 'that', 'which', 'who', 'this', 'these', 'those', 'today', 'tomorrow', 'lately', 'recently', 'currently', 'right',
    'now', 'so', 'because', 'when', 'if', 'please', 'asap', 'about', 'around', 'regarding', 'based', 'using', 'last', 'next',
    'over', 'during', 'per', 'via', 'something', 'anything', 'what', 'how', 'why', 'where', 'can', 'could', 'should', 'would',
    'will', 'might', 'do', 'does', 'did', 'are', 'was', 'then', 'instead', 'too', 'also', 'again', 'they', 'from'))


def _subject_from(clause):
    """('Lego', 'shopping behaviour') out of 'lego shopping behaviour this month'; ('', ...) for a pointer at the screen."""
    s = str(clause or '').strip(' ,:;-')
    s = _LEAD_DET_RX.sub('', s).strip()
    toks = []
    for tok in re.split(r"\s+", s):
        bare = tok.strip(",;:()[]\"").rstrip('.?!')
        if not bare:
            break
        low = re.sub(r"'s$", '', bare.lower())
        if low in _NAME_HARD_STOP and (toks or low not in ('the',)):
            break
        toks.append(bare)
        if (tok != bare and not tok.endswith("'s")) or len(toks) >= 8:
            break
    # the audience words close the name: "lego shopping behaviour", "Nike's shoppers", "Landman viewers"
    name, aud = [], []
    i = 0
    while i < len(toks):
        two = ' '.join(toks[i:i + 2])
        one = toks[i]
        if i and _AUD_RX.match(re.sub(r"'s$", '', two.lower())):
            aud = toks[i:i + 2]
            break
        if i and _AUD_RX.match(re.sub(r"'s$", '', one.lower())):
            aud = [one]
            break
        if i and one.lower() in ('in', 'on', 'for', 'with', 'at', 'to', 'of', 'and', '&', 'vs', 'x') and not (
                one.lower() in _NAME_JOIN and i + 1 < len(toks) and toks[i + 1][:1].isupper() and name[-1][:1].isupper()
                and not re.match(r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Q[1-4]|20\d\d)", toks[i + 1])):
            break
        name.append(one)
        i += 1
        if len(name) >= 5:
            break
    nm = re.sub(r"'s$", '', ' '.join(name).strip(" '"))
    nm = _WHO_TAIL_RX.sub('', nm).strip() if len(nm.split()) > 1 else nm
    if not nm or _DEIXIS_RX.match(nm) or nm.lower() in _WHO_STOP or _SELF_RX.match(nm) or nm.split()[0].lower() in _NOT_A_NAME \
            or _AUD_RX.match(nm.lower()) or not re.search(r"[A-Za-z]", nm):
        return '', ' '.join(aud).lower()
    return nm, ' '.join(aud).lower()


def _find_subject(t):
    """The subject the words name and the audience word that rides it."""
    # "X's shoppers", "X shoppers" anywhere
    best = ('', '')
    for m in _SUBJ_PREP_RX.finditer(t):
        nm, aud = _subject_from(t[m.end():])
        if nm and (aud or not best[0]):
            best = (nm, aud)
            if aud:
                break
    if not best[0]:
        m = re.search(r"\b(?P<n>[A-Z][\w&+.'-]*(?:\s+[A-Z0-9&][\w&+.'-]*){0,3})(?:'s)?\s+(?P<a>" + _AUD + r")\b", t)
        if m and not _DEIXIS_RX.match(m.group('n')) and m.group('n').lower() not in _WHO_STOP \
                and not re.match(r"^(?:What|Which|Give|Find|Get|Show|Tell|Any|Is|Are|Do|Can|Could|I|We|Need|Want|Looking|Got)\b", m.group('n')):
            best = (re.sub(r"'s$", '', m.group('n')), m.group('a').lower())
    return best


def _points_at_screen(t):
    try:
        from . import screen_scope as _ss
        if _ss.enabled():
            return _ss.points_at_screen(t)   # one pointer for every lane (Jenna 2026-11-20)
    except Exception:
        pass
    return bool(re.search(
        r"\b(?:this|these|that|those)\s+(?:audience|profile|brand|file|data|page|shoppers?|customers?|viewers?|fans?|buyers?|users?|"
        r"people|group|cohort|segment|read|report|title|show|series|film|talent|creator)s?\b|\b(?:here|on\s+(?:this|the)\s+(?:page|screen)|"
        r"on\s+screen|in\s+front\s+of\s+me|what\s+i(?:'m|\s+am)\s+looking\s+at)\b", t, re.I))


def _angle(t, aud=''):
    for name, rx in _ANGLES:
        if rx.search(f"{aud} {t}"):
            return name
    return 'general'


def _cased(name):
    n = str(name or '').strip()
    if not n:
        return ''
    if n != n.lower():
        return n
    small = ('of', 'the', 'and', 'in', 'on', 'for', 'to', 'at', 'by', 'with', 'a', 'an', 'vs')
    return ' '.join(w if (i and w in small) else (w[:1].upper() + w[1:]) for i, w in enumerate(n.split()))


def parse(text):
    """The insight request in the words: {'subject', 'recipient',
    'audience', 'angle', 'window', 'points_at_screen', 'form'} or None.
    ``subject`` is who the finding is about, ``recipient`` who the reader
    will take it to; one named and the other not means they are the same.
    Never raises."""
    try:
        t = _clean(text)
        if not t or not enabled() or len(t) > MAX_LEN or _ADDR_RX.search(t):
            return None
        if _NOT_FAMILY_RX.search(t) or is_send_order(t):
            return None
        who = reader_will_share(t)
        form, m_who = '', ''
        m = _F_IMPRESS_RX.search(t)
        if m:
            form, m_who = 'impress', _name_at('to ' + m.group('who'))
        if not form:
            for rx in (_F_WOULD_FIND_RX, _F_WANT_KNOW_RX, _F_NOT_KNOW_RX):
                m = rx.search(t)
                if m:
                    nm = _name_at('to ' + m.group('who'))
                    if nm and nm.lower() not in ('we', 'i', 'you', 'they', 'it', 'people'):
                        form, m_who = 'impress', nm
                        break
        if not form and _F_STRONG_RX.search(t):
            form = 'insight'
        if not form and _F_WEAK_RX.search(t):
            form = 'insight'
        if not who:
            m = _ORDER_TO_THIRD_RX.match(t)
            if m:
                who = _who_label(_name_at(m.group('rest'), bare=('any' if _DIRECT_RX.match(m.group('verb')) else 'name')) or '')
        if not form and who and _F_WEAK_SHARE_RX.search(t):
            form = 'insight'
        if not form:
            m = _F_SOMETHING_RX.search(t)
            if m and (who or _SOMETHING_ADJ_RX.match(m.group('tail') or '')) and _CONTENT_REQUEST_RX.match(t):
                form = 'something'
        if not form and who and _F_WHAT_SHARE_RX.match(t):
            form = 'something'
        stands = None
        if not form:
            stands = _F_STANDS_OUT_RX.search(t)
            if stands:
                form = 'stands_out'
        if not form:
            return None
        subj, aud = _find_subject(t)
        if form == 'stands_out':
            s2, a2 = _subject_from(stands.group('subj'))
            if not s2:
                s2, a2 = _find_subject(' ' + stands.group('subj'))
            subj, aud = (s2, a2 or aud) if s2 else ('', aud)
            if not subj:
                return None        # "what stands out about this audience" stays the standing page read
        recipient = who or m_who
        if not subj and (not recipient or _is_generic_who(recipient)) and not (recipient or _HAS_SURPRISE_RX.search(t)):
            return None            # "key takeaways from this profile" names no one to take it to: the standing page read
        if subj and recipient and _fold(subj) == _fold(recipient):
            recipient = _cased(recipient)
        if not subj and recipient and not _is_generic_who(recipient):
            subj = recipient
        if subj and not recipient:
            recipient = subj if form in ('impress',) or who else ''
        w = _WINDOW_RX.search(t)
        return {'subject': _cased(subj), 'recipient': _cased(recipient) if recipient and not _is_generic_who(recipient) else recipient,
                'audience': aud, 'angle': _angle(t, aud), 'window': (w.group('w').lower() if w else ''),
                'points_at_screen': _points_at_screen(t), 'form': form}
    except Exception:
        return None


def wants(text):
    return parse(text) is not None


def _fold(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


# ------------------------------------------------------------------ what the whole file holds
_SKIP_CATS = frozenset((
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT', 'INPUT METADATA', 'AVID FAN', 'CASUAL FAN', 'LOCATION',
    'AGE', 'GENDER', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION', 'PARENTAL STATUS', 'RELATIONSHIP', 'RELATIONSHIP STATUS',
    'SEXUAL ORIENTATION', 'PRIMARY LANGUAGE', 'NUMBER OF CHILDREN', 'AGE OF CHILDREN', 'PORN MEDIA', 'TALENT', 'SPORTS TEAM',
    'PODCAST RANKER', 'ORGANIZATIONAL MEMBERSHIPS', 'POLITICS/ACTIVIST', 'NEWS/POLITICS CONTENT', 'FAITH/SPIRITUALITY CONTENT'))
_DIVISION_RX = re.compile(r"\b(?:AFC|NFC|AL|NL|EAST|WEST|NORTH|SOUTH|CENTRAL|DIVISION|CONFERENCE)\b")
_PURCHASE_CATS = ('WHERE THEY SHOP', 'RETAILERS', 'MOST PURCHASED BRANDS', 'MOST PURCHASED CATEGORIES', 'TOYS', 'TOY', 'CPG',
                  'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TECHNOLOGY BRAND',
                  'CREDIT PROVIDER', 'GROCERY', 'BEVERAGE')
_ANGLE_CATS = {
    'shopping': _PURCHASE_CATS,
    'viewing': ('STREAMING/PLATFORM', 'TV SHOW', 'FRANCHISE', 'BROADCAST/CABLE', 'VIRTUAL MVPD FAST', 'MOVIE THEATER', 'MOVIE',
                'ACTOR', 'HOST/PERSONALITY', 'ENTERTAINMENT CONTENT', 'MEDIA'),
    'social': ('SOCIAL MEDIA', 'CREATOR/INFLUENCER', 'APP/PLATFORM USAGE', 'APP/PLATFORM', 'ENTERTAINMENT CONTENT', 'LIFESTYLE CONTENT',
               'FAMILY CONTENT', 'GAMING CONTENT', 'FOOD CONTENT', 'FASHION CONTENT', 'BEAUTY CONTENT', 'FITNESS/WELLNESS CONTENT'),
    'listening': ('STREAMING/MUSIC', 'STREAMING MUSIC', 'MUSICIAN/BAND', 'PODCAST', 'MUSIC/DANCE CONTENT'),
    'gaming': ('GAMES', 'GAMING CONTENT', 'FRANCHISE', 'TECHNOLOGY/DEVICE'),
    'dining': ('QSR', 'WHERE THEY DINE', 'CASUAL DINING', 'BEVERAGE', 'CPG', 'FOOD CONTENT', 'GROCERY'),
    'travel': ('TRAVEL', 'AMUSEMENT PARKS', 'EVENTS', 'VENUE', 'TRAVEL CONTENT', 'AUTOMOBILE'),
    'money': ('BANKING', 'DIGITAL BANKING', 'CREDIT PROVIDER', 'INVESTMENTS', 'TRADING', 'INSURANCE', 'BETTING'),
}
# the columns a row of which is a confirmed online purchase (prometheus_analysis._CONFIRMED_ORDER_COLS)
_BOUGHT_CATS = frozenset(('MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'APPAREL', 'FOOTWEAR', 'BEAUTY/WELLNESS', 'BEAUTY',
                          'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TOYS', 'TOY', 'TECHNOLOGY BRAND', 'TECHNOLOGY BRAND/DEVICE',
                          'HEAVY MACHINERY', 'WHERE THEY SHOP', 'BEVERAGE'))
_CASING = {'ebay': 'eBay', 'ikea': 'IKEA', 'tj maxx': 'TJ Maxx', 'tjmaxx': 'TJ Maxx', 'h&m': 'H&M', 'cvs': 'CVS', 'qvc': 'QVC',
           'hsn': 'HSN', 'rei': 'REI', 'dsw': 'DSW', 'at&t': 'AT&T', 'usps': 'USPS', 'ups': 'UPS', 'hbo max': 'HBO Max',
           'youtube': 'YouTube', 'youtube kids': 'YouTube Kids', 'tiktok': 'TikTok', 'linkedin': 'LinkedIn', 'byutv': 'BYUtv',
           'mcdonalds': "McDonald's", 'chick-fil-a': 'Chick-fil-A', 'gamestop': 'GameStop', 'l.o.l. surprise': 'L.O.L. Surprise',
           'bjs': "BJ's", 'jcpenney': 'JCPenney', 'paypal': 'PayPal', 'doordash': 'DoorDash', 'espn': 'ESPN', 'amc': 'AMC',
           'nfl': 'NFL', 'nba': 'NBA', 'mlb': 'MLB', 'nhl': 'NHL', 'apple tv+': 'Apple TV+', 'disney+/hulu': 'Disney+ and Hulu',
           'kitchenaid': 'KitchenAid', 'cerave': 'CeraVe', 'jansport': 'JanSport', 'playstation': 'PlayStation', 'xbox': 'Xbox',
           'iphone': 'iPhone', 'macys': "Macy's", 'kohls': "Kohl's", 'lowes': "Lowe's", 'cabelas': "Cabela's", 'wendys': "Wendy's",
           'dominos': "Domino's", 'claires': "Claire's", 'sams club': "Sam's Club", 'dicks sporting goods': "Dick's Sporting Goods",
           'trader joes': "Trader Joe's", 'the childrens place': "The Children's Place", 'toys r us': 'Toys R Us'}


def _pretty(value):
    v = ' '.join(str(value or '').split())
    if not v:
        return v
    hit = _CASING.get(v.lower())
    if hit:
        return hit
    if v != v.upper():
        return v
    small = ('of', 'the', 'and', 'in', 'on', 'for', 'to', 'at', 'by', 'with', 'a', 'an')
    out = []
    for i, w in enumerate(v.split()):
        lw = w.lower()
        if i and lw in small:
            out.append(lw)
        elif len(w) <= 3 and not re.search(r"[AEIOUY]", w) and w.isalpha():
            out.append(w)
        elif re.search(r"\d", w) and len(w) <= 4:
            out.append(w)
        else:
            out.append('-'.join(p[:1].upper() + p[1:].lower() for p in w.split('-')))
    return ' '.join(out)


def _num(v):
    try:
        s = str(v).replace('%', '').replace(',', '').strip()
        return float(s) if s not in ('', 'nan', 'None') else None
    except (TypeError, ValueError):
        return None


def _col(df, *needles, avoid=()):
    for c in df.columns:
        lc = str(c).lower()
        if all(n in lc for n in needles) and not any(a in lc for a in avoid):
            return c
    return None


def findings(df, genpop=None, *, subject='', angle='general', audience_people=None, limit=26):
    """The rows of the whole file that stand furthest from the US:
    {'over': [...], 'under': [...]}, the angle's own sections first; each
    row {'cat', 'value', 'pen', 'us', 'x', 'people', 'bought', 'score',
    'angle'}. Rows of the subject itself, thin rows, rows that read near
    zero, and mirrored copies of one brand are left out; a brand that
    reads low in one section and high in another is not a low. Never
    raises."""
    out = {'over': [], 'under': []}
    try:
        if df is None or not len(df):
            return out
        c_cat = _col(df, 'column') or 'Column'
        c_val = _col(df, 'value') or 'Value'
        c_pen = _col(df, 'penetration', avoid=('gen pop', 'genpop')) or _col(df, 'penetration')
        c_us = _col(df, 'gen pop', 'penetration') or _col(df, 'genpop', 'penetration')
        c_ppl = _col(df, 'projection') or _col(df, 'proj')
        if not (c_pen and c_cat in df.columns and c_val in df.columns):
            return out
        gp = genpop if isinstance(genpop, dict) else {}
        subj = _fold(subject)
        angle_cats = set(_ANGLE_CATS.get(angle) or ())
        seen, best_pen = {}, {}
        cats = df[c_cat].astype(str).tolist()
        vals = df[c_val].astype(str).tolist()
        pens = df[c_pen].tolist()
        uss = df[c_us].tolist() if c_us else [None] * len(cats)
        ppls = df[c_ppl].tolist() if c_ppl else [None] * len(cats)
        for cat_raw, val, pen_raw, us_raw, ppl_raw in zip(cats, vals, pens, uss, ppls):
            cat = re.sub(r"[_\s]+", ' ', str(cat_raw or '').strip().upper())
            if not cat or cat in _SKIP_CATS or _DIVISION_RX.search(cat) or cat.endswith(' ATHLETE'):
                continue
            pen = _num(pen_raw)
            if pen is None or pen >= 99.5 or pen <= 0:
                continue
            vf = _fold(val)
            if not vf or (subj and (vf == subj or subj in vf or (len(vf) >= 4 and vf in subj))):
                continue
            best_pen[vf] = max(best_pen.get(vf, 0.0), pen)
            us = _num(us_raw)
            if us is None:
                us = gp.get((cat, vf))
            if us is None or us <= 0:
                continue
            x = pen / us
            people = _num(ppl_raw)
            if (people is None or people <= 0) and audience_people:
                people = round(float(audience_people) * pen / 100.0)
            in_angle = cat in angle_cats
            row = {'cat': cat, 'value': _pretty(val), 'pen': round(pen, 1), 'us': round(us, 1), 'x': x, '_pen': pen,
                   'people': int(people) if people else None, 'bought': cat in _BOUGHT_CATS, 'angle': in_angle}
            if pen >= 4.0 and us >= 0.5 and x >= 1.5 and (pen - us) >= 2.0:
                row['score'] = (pen - us) * min(x, 6.0)
                kind = 'over'
            elif us >= 12.0 and pen >= 3.0 and 0.25 <= x <= 0.62 and (us - pen) >= 6.0:
                row['score'] = (us - pen) * min(1.0 / x, 4.0)
                kind = 'under'
            else:
                continue
            key = (kind, vf)
            prev = seen.get(key)
            # one brand, one row: the angle's own section first, then the stronger reading
            if prev is None or (row['angle'] and not prev['angle']) or (row['angle'] == prev['angle'] and row['score'] > prev['score']):
                seen[key] = row
        for (kind, vf), row in seen.items():
            if kind == 'under' and best_pen.get(vf, 0.0) > row['_pen'] * 1.5:
                continue          # the same brand reads higher in another section: not a low
            out[kind].append(row)

        def _pick(rows, n, per_cat):
            picked, count = [], {}
            for r in sorted(rows, key=lambda r: (-r['score'], r['value'])):
                if count.get(r['cat'], 0) >= per_cat:
                    continue
                count[r['cat']] = count.get(r['cat'], 0) + 1
                picked.append(r)
                if len(picked) >= n:
                    break
            return picked
        for kind, share in (('over', 1.0), ('under', 0.4)):
            n = max(4, int(limit * share))
            mine = _pick([r for r in out[kind] if r['angle']], max(2, int(n * 0.6)), 6)
            rest = _pick([r for r in out[kind] if not r['angle']], n - len(mine), 2 if mine else 3)
            out[kind] = mine + rest
            for r in out[kind]:
                r.pop('_pen', None)
    except Exception:
        return {'over': [], 'under': []}
    return out


def _times(x):
    return f"{x:.1f} times" if x < 9.95 else f"{round(x):,} times"


def window_plain(window):
    """'2025-10-20 to 2026-10-20' -> 'the 12 months to October 20, 2026'."""
    try:
        m = re.findall(r"(\d{4})-(\d{2})-(\d{2})|(\d{2})[/_.-](\d{2})[/_.-](\d{4})", str(window or ''))
        ds = []
        for g in m:
            ds.append(date(int(g[0]), int(g[1]), int(g[2])) if g[0] else date(int(g[5]), int(g[3]), int(g[4])))
        if len(ds) < 2:
            return ''
        a, b = ds[0], ds[1]
        days = (b - a).days
        end = f"{b.strftime('%B')} {b.day}, {b.year}"
        if 360 <= days <= 371:
            return f"the 12 months to {end}"
        return f"{a.strftime('%B')} {a.day}, {a.year} to {end}"
    except Exception:
        return ''


def findings_block(found, *, subject, audience_people=None, window='', angle='general'):
    """The prompt block: the strongest rows of the whole file, each with
    its US comparison, the sections of the angle asked about first. ''
    when the file gave none."""
    over, under = list((found or {}).get('over') or []), list((found or {}).get('under') or [])
    if not over and not under:
        return ''
    head = f"MEASURED FINDINGS FROM THE FULL FILE: {subject}"
    if audience_people:
        head += f", {int(audience_people):,} US people"
    wp = window_plain(window)
    if wp:
        head += f", {wp}"
    lines = [head,
             "Every row of the file was read against the US, not only the top of each section. Each line: section | row | share of "
             "this audience | share of US people | multiple of the US rate | US people behind the share."]
    words = _ANGLE_WORDS.get(str(angle or 'general'), '')

    def _line(r, low=False):
        if low:
            return f"  {r['cat']} | {r['value']} | {r['pen']:.1f}% | {r['us']:.1f}% | {r['x']:.2f} of the US rate"
        ppl = f" | {r['people']:,} people" if r.get('people') else ''
        return (f"  {r['cat']} | {r['value']} | {r['pen']:.1f}% | {r['us']:.1f}% | {_times(r['x'])} the US rate{ppl}"
                + (' | confirmed online purchase' if r.get('bought') else ''))
    mine, rest = [r for r in over if r.get('angle')], [r for r in over if not r.get('angle')]
    if mine:
        lines.append("HIGHER THAN THE US, IN WHAT WAS ASKED ABOUT" + (f" ({words})" if words else '') + ", strongest first:")
        lines += [_line(r) for r in mine]
    if rest:
        lines.append("HIGHER THAN THE US, ELSEWHERE IN THE FILE:" if mine else "HIGHER THAN THE US (strongest first):")
        lines += [_line(r) for r in rest]
    if under:
        lines.append("LOWER THAN THE US (big in the US, weak here):")
        lines += [_line(r, low=True) for r in under]
    return '\n'.join(lines)


# ------------------------------------------------------------------ the brief the analysis pass reads
_ANGLE_WORDS = {'shopping': 'shopping behaviour', 'viewing': 'what they watch', 'social': 'social and creator behaviour',
                'listening': 'what they listen to', 'gaming': 'what they play', 'dining': 'where they eat and what they drink',
                'travel': 'travel behaviour', 'money': 'banking and payment behaviour', 'general': ''}


# what a file's own name says its people are ("Costco Members", "EST Buyers", "Landman Viewers")
_UNIVERSE_NOUN_RX = re.compile(
    r"\b(shoppers|buyers|customers|consumers|purchasers|viewers|fans|listeners|subscribers|members|users|players|owners|renters|"
    r"voters|enthusiasts|drinkers|guests|readers|followers|travelers|travellers|switchers)\b", re.I)
# the reader's word for them, when it claims nothing the file does not hold (a purchase is claimed row by row, never for everyone)
_READER_NOUNS = (('view', 'viewers'), ('watch', 'viewers'), ('fan', 'fans'), ('listen', 'listeners'), ('play', 'players'),
                 ('gamer', 'players'), ('follower', 'followers'), ('user', 'users'), ('subscriber', 'subscribers'),
                 ('member', 'members'), ('reader', 'readers'))


def _aud_noun(p, subject=''):
    """The noun for the people in the file. A brand's file holds the
    people who engaged with the brand online, not only its buyers, so
    "shoppers" and "customers" are used only when the file's own name
    says so; the reader's "viewers" or "fans" stands."""
    m = _UNIVERSE_NOUN_RX.search(str(subject or ''))
    if m:
        return m.group(1).lower()
    a = str((p or {}).get('audience') or '')
    for w, n in _READER_NOUNS:
        if w in a:
            return n
    return 'audience'


def _they(p, subject):
    """'the Lego audience', 'Landman viewers', 'Costco Members'."""
    if _UNIVERSE_NOUN_RX.search(str(subject or '')):
        return str(subject)
    n = _aud_noun(p, subject)
    return f"the {subject} audience" if n == 'audience' else f"{subject} {n}"


def _your(p, subject):
    n = _aud_noun(p, subject)
    return f"your {n}"


def _for_whom(p, subject):
    r = str((p or {}).get('recipient') or '').strip()
    return r or subject


def brief(p, *, subject, window=''):
    """The instruction block for an insight request, appended to the
    analysis prompt. ``subject`` is the name of the file's audience."""
    p = p if isinstance(p, dict) else {}
    who = _for_whom(p, subject)
    same = _fold(who) == _fold(subject) or not str(p.get('recipient') or '').strip()
    they, your = _they(p, subject), _your(p, subject)
    angle = _ANGLE_WORDS.get(str(p.get('angle') or 'general'), '')
    wp = window_plain(window)
    asked = str(p.get('window') or '').strip()
    lines = [
        "INSIGHT REQUEST (binding for this reply; it overrides the default format and length rules)",
        "==========================================================================================",
        (f"The reader is asking for the single most striking thing in the data about {subject}"
         + (f" ({angle})" if angle else '') + f" that they can put in front of {who}. "
         + (f"{who} already knows its own sales and its own site. What it cannot see is everything else these people do: where else "
            f"they shop, which other brands they buy, what they watch, play and follow, and where they sit below the US." if same else
            f"{who} is a third party the reader wants to impress with what {they} do: lead with how these people behave around {who} "
            f"and its category when the data carries it, then what else sets them apart.")),
        (f"WHO THESE PEOPLE ARE: call them \"{they}\". They are the people who engaged with {subject} online in the window; not all "
         "of them bought from it, so never call them shoppers, buyers or customers as a group and never write \"the same people "
         f"buying {subject}\". Say bought only for a row marked confirmed online purchase."),
        "Do the analyst's job. Look, pick, and hand it over:",
        f"1. Pick the ONE finding {who}'s own team would not already know and could act on. Read MEASURED FINDINGS FROM THE FULL FILE "
        "first: a large share with a large multiple of the US rate beats a huge multiple on a sliver (4 times the US rate on a quarter "
        "of the audience beats 12 times on 4%). Skip what is obvious for this subject (its own products, a family brand's audience "
        "having children, a toy brand's audience buying toys). A pattern across two or three rows (a kind of store, a kind of brand) "
        "is stronger than one row.",
        "2. The first sentence of the reply IS that finding, in plain words, naming one row only, with its number and the US "
        "comparison in the same sentence. Rows that complete a pattern go in the supporting lines, never in the opening. The shape of "
        f"that sentence: \"In our profile of {they}, 23.8% bought from <a store>, against 5.5% of US people, 4.4 times the US "
        f"rate\". The words \"In our profile of {they}\" open the reply: every figure here is that profile's.",
        f"3. Then one or two sentences on why it would matter to {who}: name the pattern in plain words and what {who} could do "
        "with it. This part is interpretation, so it leans, skews or reads as; it never states a motive as fact, and it never "
        "comments on the read itself (no \"the read is directional\", no \"early signal\", no \"worth validating\").",
        "4. Then one or two supporting findings as short \"- \" lines, each with its share, the US share and the multiple, and the "
        "people count where the data prints one. Three rows at most above the forward-ready version (the opening row and up to two "
        "supporting rows), each stated once.",
        f"5. Then a line that reads exactly \"{FORWARD_LABEL}\" and under it three or four sentences the reader can paste into their "
        f"own email to {who}. Written to {who} in the reader's voice (\"{your}\"), opening with the words \"In Crosswalk's "
        f"profile of {they},\" so {who} knows whose figures these are; the numbers stated plainly with the US comparison, no "
        "greeting, no sign-off, no jargon (never idx, pp, penetration, index), no mention of Prometheus, a file or a dashboard.",
        f"6. Close with one short line offering to turn this into a one-pager. Nothing is sent to {who} by you, now or later; never "
        "say or imply that it was, will be or can be.",
    ]
    if wp:
        w_line = f"WINDOW: every figure covers {wp}. Say that once, plainly, in the reply and once in the forward-ready version."
        if asked:
            w_line += (f" The reader asked about \"{asked}\". The data in hand does not separate that period, so no figure is "
                       f"described as from {asked}, as new, as rising or falling, or as a change over time. Do not apologise, do "
                       "not explain and do not offer another read (a line about that is added for you); state the window and "
                       "give the finding.")
        lines.append(w_line)
    lines.append(
        "RULES: every number is quoted exactly from the data above and carries its US comparison; write \"N times the US rate\", "
        "never idx or index, never \"4.4x\"; a multiple is quoted as its line prints it, never worked out again and never written "
        "as a calc token; a number is never softened with about, roughly, around or nearly; count people or accounts, never "
        "households; plain words a 7th grader would follow, so no marketing jargon (territory, play pattern, "
        "co-merchandising, whitespace, lane, pool, activation); rows marked confirmed online purchase "
        "are things they bought, say bought; state measured numbers flat with no words about how sure the read is; no em dashes or "
        "en dashes; never \"real-time\" or \"today\" (the data is a day behind); never say on screen, open profile, the file, the "
        "digest or the block; action is \"answer\"; offer_deck is false; followups are up to three next questions the reader would "
        f"type, the first exactly \"{ONE_PAGER_CHIP}\". Length 170 to 260 words.")
    return '\n'.join(lines)


def read_brief(text):
    """The shape of the reply for the measured-read pass (a subject the
    library does not hold, read after its quote is approved): the same
    hand-over, with the read's own fields left as its prompt defines
    them. '' when the words are not an insight request."""
    try:
        p = parse(text)
        if not p or not p.get('subject'):
            return ''
        subject = p['subject']
        who = _for_whom(p, subject)
        noun = _aud_noun(p, subject)
        angle = _ANGLE_WORDS.get(str(p.get('angle') or 'general'), '')
        return '\n'.join([
            "INSIGHT REQUEST (how the reply text is shaped; every other field stays as the system prompt defines it)",
            "========================================================================================================",
            f"The reader wants the single most striking thing about {subject}" + (f" ({angle})" if angle else '')
            + f" that they can put in front of {who}: something {who}'s own team cannot see in its own data (where else these "
            "people shop, which other brands they buy, what they watch, play and follow, where they sit below the US).",
            "- The reply opens with the ONE strongest finding in one plain sentence, with its number and the US comparison in the "
            "same sentence.",
            f"- Then one or two sentences on why it would matter to {who}; this part leans, skews or reads as.",
            "- Then one or two supporting findings as short \"- \" lines, each with its US comparison. Three findings at most.",
            f"- Then a line that reads exactly \"{FORWARD_LABEL}\" and under it three or four sentences the reader can paste into "
            f"their own email to {who}, in the reader's voice (\"your {noun}\"), no greeting, no sign-off, no jargon, no mention of "
            "Crosswalk or Prometheus.",
            f"- Close with one short line offering to turn this into a one-pager. Nothing is sent to {who} by you; never say or "
            "imply that it was or will be.",
            "- A count that is not a direct read sits at the low end of what the evidence supports. Say the window once, plainly; "
            "never \"real-time\" or \"today\". Count people or accounts, never households. No em dashes.",
            ''])
    except Exception:
        return ''


def offer_lead(question, subject):
    """The first sentence of the standing quote when the words are an
    insight request on a subject the library does not hold: what can be
    pulled and what the reader gets. '' for any other question, so every
    other quote reads as it always did. The price line is not written
    here."""
    try:
        p = parse(question)
        subj = ' '.join(str(subject or '').split())
        if not p or not subj or subj.lower() == 'that subject':
            return ''
        return f"I can pull a read on {subj} and hand you the strongest finding in a form you can forward."
    except Exception:
        return ''


# ------------------------------------------------------------------ the reply
_SENT_CLAIM_RX = re.compile(
    r"\b(?:i|we|prometheus)\s*(?:'ve|\s+have|\s+will|'ll|\s+can|\s+could|\s+just)?\s*(?:also\s+)?(?:sent|send|e-?mailed|e-?mail|"
    r"shared|share|forwarded|forward|delivered|deliver)\b[^.?!\n]{0,60}\b(?:to|with)\s+(?!you\b|your\b)", re.I)
_BANNED_SWAPS = (
    (re.compile(r"\b(?:in\s+)?real[- ]time\b", re.I), 'day by day'),
    (re.compile(r"\s+\u2014\s+|\s+\u2013\s+"), ', '),
    (re.compile(r"\u2014|\u2013"), '-'),
    (re.compile(r"\bhousehold\s+income\b", re.I), 'income'),
    (re.compile(r"\bhouseholds\b", re.I), 'people'),
    (re.compile(r"\bhousehold\b", re.I), 'family'),
)


def _verb(r):
    if r['cat'] in ('WHERE THEY SHOP', 'RETAILERS'):
        return 'bought from'
    return 'bought' if r.get('bought') else 'engaged with'


def _sentences(par):
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(\[-])", str(par or '').strip()) if s.strip()]


def _mention(r, they, lead=False):
    verb = _verb(r)
    if lead:
        return (f"In our profile of {they}, {r['pen']:.1f}% {verb} {r['value']}, against {r['us']:.1f}% of US people, "
                f"{_times(r['x'])} the US rate")
    return (f"{r['pen']:.1f}% of {they} {verb} {r['value']} in the window, against {r['us']:.1f}% of US people"
            + (f" ({_times(r['x'])} the US rate" + (f", {r['people']:,} people" if r.get('people') else '') + ")" if not lead
               else f", {_times(r['x'])} the US rate"))


def compose(p, *, subject, found, window='', audience_people=None):
    """The reply written straight from the rows, for when the analysis
    pass gives nothing usable. Same shape: the finding, why it matters,
    the support, the forward-ready version, the one-pager line."""
    p = p if isinstance(p, dict) else {}
    over = list((found or {}).get('over') or [])
    if not over:
        return ''
    who = _for_whom(p, subject)
    they = _they(p, subject)
    top = [r for r in over if r.get('angle')][:3] or over[:3]
    wp = window_plain(window)
    lead = top[0]
    parts = [_mention(lead, they, lead=True) + '.']
    parts.append(f"{who} cannot see this in its own sales, because it happens somewhere else."
                 + (f" It reads as {lead['people']:,} people {who} could reach there." if lead.get('people') else ''))
    if len(top) > 1:
        parts.append('\n'.join('- ' + _mention(r, they) + '.' for r in top[1:]))
    if wp:
        parts.append(f"Every figure covers {wp}.")
    fw = [f"In Crosswalk's profile of {they}, {lead['pen']:.1f}% {_verb(lead)} {lead['value']}"
          + (f" over {wp}" if wp else '') + f", against {lead['us']:.1f}% of US people. That is {_times(lead['x'])} the US rate, "
          "and it is not something your own numbers will show."]
    if len(top) > 1:
        r = top[1]
        fw.append(f"The same pattern shows with {r['value']}: {r['pen']:.1f}% against {r['us']:.1f}% of US people.")
    fw.append("Happy to walk you through what else these people do when they are not with you.")
    parts.append(FORWARD_LABEL + '\n' + ' '.join(fw))
    parts.append(ONE_PAGER_LINE)
    return '\n\n'.join(parts)


_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')


def asked_month(p, today=None):
    """The month the reader asked about, as dates the data can cover
    (it is a day behind): {'label', 'start', 'end'} or None. Only "this
    month" and "last month" are a month."""
    try:
        w = str((p or {}).get('window') or '').lower()
        if not re.search(r"\b(?:this|last)\s+month\b|\bmonth\s+to\s+date\b|\bmtd\b|so\s+far\s+this\s+month", w):
            return None
        d = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
        if 'last month' in w:
            end = d.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        else:
            start, end = d.replace(day=1), d
        return {'label': _MONTHS[start.month - 1], 'start': start, 'end': end,
                'range': f"{_MONTHS[start.month - 1]} {start.day} to {_MONTHS[end.month - 1]} {end.day}, {end.year}"}
    except Exception:
        return None


def window_line(p, window, today=None):
    """One plain line when the reader asked about a month the file does
    not separate: what the figures cover, and that the month on its own
    is another read. '' when there is nothing to say."""
    wp = window_plain(window)
    mo = asked_month(p, today)
    if not (wp and mo):
        return ''
    return f"These figures cover {wp}. {mo['label']} on its own is a separate read; ask for it and I will run it."


def window_chip(p, subject, today=None):
    mo = asked_month(p, today)
    if not (mo and subject):
        return ''
    what = _ANGLE_WORDS.get(str((p or {}).get('angle') or 'general'), '') or 'audience behaviour'
    return f"{subject} {what} from {mo['range']}"


def _bind_multiples(text, found):
    """Every multiple of the US rate in the reply is the file's own: a
    multiple worked out again from the rounded shares ("4.3x" beside 23.8%
    and 5.5%, where the file reads 4.4) is set back, and "Nx the US rate"
    is written in words."""
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s?x\s+(the\s+US\s+rate)", r"\1 times \2", text)
    for r in list((found or {}).get('over') or []):
        want = _times(r['x']).split(' ')[0]
        rx = re.compile(r"(" + re.escape(f"{r['pen']:.1f}%") + r"[^\n%]{0,90}" + re.escape(f"{r['us']:.1f}%")
                        + r"[^\n%]{0,40}?\b)(\d+(?:\.\d+)?)(\s+times\s+the\s+US\s+rate)")
        text = rx.sub(lambda m, w=want: m.group(1) + w + m.group(3), text)
    return text


def finish(reply, p, *, subject, found=None, window='', today=None):
    """The reply as it leaves: nothing claims a send to the third party,
    the forward-ready version and the one-pager line are there, a month
    the file does not separate is said plainly, the house words hold.
    Never raises; returns the text."""
    try:
        text = str(reply or '').strip()
        if not text:
            return text
        for rx, sub in _BANNED_SWAPS:
            text = rx.sub(sub, text)
        text = _bind_multiples(text, found)
        text = re.sub(r"(?im)^[^\w\n]*forward[- ]ready\s+version[^\w\n]*$", FORWARD_LABEL, text)
        # a sentence that says Prometheus sent, or will send, something to someone else goes whole; the forward-ready
        # version is the reader's own voice and is left as written
        head, sep, tail = text.partition(FORWARD_LABEL)
        kept = []
        for par in head.split('\n'):
            if not par.strip():
                kept.append(par)
                continue
            ss = [s for s in _sentences(par) if not _SENT_CLAIM_RX.search(s)]
            if ss:
                kept.append(' '.join(ss))
        text = re.sub(r"\n{3,}", '\n\n', '\n'.join(kept) + sep + tail).strip()
        if FORWARD_LABEL.lower() not in text.lower():
            fb = compose(p, subject=subject, found=found, window=window)
            if FORWARD_LABEL in fb:
                tail = fb.split(FORWARD_LABEL, 1)[1].split('\n\n')[0].strip()
                text = text.rstrip() + '\n\n' + FORWARD_LABEL + '\n' + tail
        if not re.search(r"\bone-?pager\b|\bone[- ]page\b", text, re.I):
            text = text.rstrip() + '\n\n' + ONE_PAGER_LINE
        wl = window_line(p, window, today)
        if wl and 'on its own is a separate read' not in text:
            wp = window_plain(window)
            if wp and wp in text:
                wl = wl.split('. ', 1)[1]          # the window is already said: only the month's own read is news
            text = text.rstrip() + '\n\n' + wl
        return text
    except Exception:
        return str(reply or '')


def _chips(followups, p, subject='', today=None):
    out = [ONE_PAGER_CHIP]
    wc = window_chip(p, subject, today)
    if wc:
        out.append(wc)
    for f in (followups or []):
        f = ' '.join(str(f or '').split())
        if f and f.lower() != ONE_PAGER_CHIP.lower() and not re.search(r"one-?pager", f, re.I) and f not in out \
                and not re.search(r"\b(?:e-?mail|send)\b.{0,30}\bto\b(?!\s+me\b)", f, re.I):
            out.append(f[:160])
    return out[:4]


# ------------------------------------------------------------------ the lane inside the analyze core
# words a library file adds to a subject's name without making it another subject ("Lego Buyers", "Nike - Avid Fan")
_SAME_EXTRA = frozenset((
    'the', 'a', 'an', 'of', 'and', 'audience', 'audiences', 'profile', 'tu', 'total', 'universe', 'avid', 'casual', 'fan', 'fans',
    'shoppers', 'buyers', 'customers', 'consumers', 'purchasers', 'viewers', 'listeners', 'subscribers', 'members', 'users',
    'players', 'owners', 'followers', 'readers', 'potential', 'us', 'brand', 'inc', 'co', 'company', 'group'))


def _same_subject(a, b):
    """True when the library's file is the subject the words name: the
    same name, or the name with only audience words around it ("Lego"
    and "Lego Buyers"). "Amazon" is not "Amazon Prime Video" and "Power"
    is not "Power On Amazon Prime": one name inside another is another
    subject (the named-title rule)."""
    head = str(b or '').split(' - ')[0]
    fa, fb = _fold(a), _fold(head)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    ta = set(re.findall(r"[a-z0-9]+", str(a).lower()))
    tb = set(re.findall(r"[a-z0-9]+", head.lower()))
    core_a, core_b = ta - _SAME_EXTRA, tb - _SAME_EXTRA
    core_a = {t for t in core_a if not re.fullmatch(r"20\d\d", t)}
    core_b = {t for t in core_b if not re.fullmatch(r"20\d\d", t)}
    return bool(core_a) and core_a == core_b


def _insight_only():
    """True while the door's own lane is asking the core for the insight
    answer and nothing else (prometheus.service, lane insight_ask)."""
    try:
        from flask import g as _g
        return bool(getattr(_g, '_pm_insight_only', False))
    except Exception:
        return False


def core_hook(text, body, history, user, ppu, G):
    """An insight request on a subject the library holds, answered from
    the whole file through the analysis pass. Returns the Flask response,
    or None when the words are not that request, the library does not
    hold the subject, the reader cannot open the file, a step is being
    answered, or the price rules read the words as a put-together read
    (the standing flow then quotes it as it always did). Asked by the
    door's own lane, a pass is a marked empty response instead of None,
    so the core never runs the standing flow twice."""
    only = _insight_only()
    if not only and _passed_on(text):
        return None               # the door's lane already asked on these words this turn: the standing flow runs once
    out = _core(text, body, history, user, ppu, G)
    if out is None and only:
        try:
            from flask import g as _g
            _g._pm_insight_passed = _fold(text)
            return G['jsonify']({'success': True, 'insight_declined': True})
        except Exception:
            return None
    return out


def _passed_on(text):
    try:
        from flask import g as _g
        return bool(_fold(text)) and getattr(_g, '_pm_insight_passed', None) == _fold(text)
    except Exception:
        return False


def _core(text, body, history, user, ppu, G):
    try:
        if not enabled():
            return None
        b = body if isinstance(body, dict) else {}
        if any(b.get(k) for k in ('panel_confirm', 'bind_subject', 'mode', 'overall_ranks', 'bpiq_confirm', 'jiq_confirm', 'fw_confirm',
                                  'aiq_confirm', 'twin_confirm', '_pm_order', '_meta_replay', 'confirm_open_screen')):
            return None
        p = parse(text)
        if not p:
            return None
        try:
            from . import priced_read as _prg
            if _prg.classify(text, history, b.get('page_context')):
                return None       # a ranked list, a cross, a table: a put-together read keeps its quote
        except Exception:
            pass
        import prometheus_analysis as pma
        H = G['_H']
        page_ctx, ctx_err = G['_pm_validate_page_context'](b.get('page_context'))
        if ctx_err is not None:
            page_ctx = None
        page = str(((page_ctx or {}).get('primary') or {}).get('name') or '').strip()
        subject = str(p.get('subject') or '').strip()
        base = None
        if subject:
            # the subject the words name: the library's own file for it, never the page for another name
            cand = G['_pm_generation_base'](subject, subject, ctx=page_ctx, prefer_catalog=True)
            if cand and str(cand.get('s3_key') or '').lower().endswith('.csv') and _same_subject(subject, cand.get('subject')):
                base = cand
        elif p.get('points_at_screen') and page and str(((page_ctx or {}).get('primary') or {}).get('s3_key') or '').lower().endswith('.csv'):
            base = {'subject': page, 's3_key': str(page_ctx['primary']['s3_key']), 'source': 'page'}
        elif str(p.get('recipient') or '').strip():
            # the words name who it is for and no subject ("a cool insight we can send them"): the subject this thread is
            # on, never the page by default. With no one to take it to, the standing read answers as it always did.
            try:
                ts = [x for x in (G['_subject_rules'].thread_subjects(history, page='', limit=1) or []) if str(x or '').strip()]
            except Exception:
                ts = []
            if ts:
                cand = G['_pm_generation_base'](str(ts[0]), str(ts[0]), ctx=None, prefer_catalog=True)
                if cand and str(cand.get('s3_key') or '').lower().endswith('.csv') and _same_subject(ts[0], cand.get('subject')):
                    base = cand
        if not base:
            return None
        ctx, err = G['_pm_validate_page_context']({'primary': {'s3_key': base['s3_key'], 'name': base['subject']}, 'cuts': []})
        if err is not None or not (ctx or {}).get('primary'):
            return None           # a file this reader cannot open is not read for them
        ask_hint, stage = G['_pm_ask_hint'], G.get('_pm_ask_stage') or (lambda *a, **k: None)
        ask_hint(route=ROUTE)
        uname = ''
        try:
            uname = (G['session'].get('username') or (user or {}).get('username') or '').strip()
        except Exception:
            uname = str((user or {}).get('username') or '').strip()
        digest, meta = pma.get_digest_bundle(H.s3_client, H.S3_BUCKET, ctx)
        name = str(meta.get('name') or base['subject'])
        df = pma.load_profile_df(H.s3_client, H.S3_BUCKET, base['s3_key'])[0]
        found = findings(df, pma.load_genpop_map(H.s3_client, H.S3_BUCKET), subject=name, angle=p.get('angle') or 'general',
                         audience_people=meta.get('proj'))
        block = findings_block(found, subject=name, audience_people=meta.get('proj'), window=meta.get('window') or '',
                               angle=p.get('angle') or 'general')
        if not block:
            return None           # nothing in the file stands apart from the US: the standing read answers
        led_block, anchors_block = '', ''
        try:
            # the knowledge layer (2026-11-20): the answers already given on the subject, and the researched
            # anchors when the ledger is thin; the lane's own lookup when the layer is off
            from . import knowledge as _know
            led_block, anchors_block = _know.insight_blocks(
                text, name, history=history, window=meta.get('window'), body=b,
                consult=lambda: str((__import__('insights_ledger').consult(subject=name, question=text, history=history) or {}).get('block') or ''),
                claude_json=lambda _s, _u, **_kw: G['_pm_claude_json'](_s, _u, usage_extras=ppu, **_kw),
                tools=[pma.WEB_SEARCH_TOOL])
        except Exception:
            led_block, anchors_block = '', ''
        try:
            ub = G['_pm_user_block'](uname)
            if ub:
                led_block = (led_block + '\n\n' + ub) if led_block else ub
        except Exception:
            pass
        fig = G['_fig']
        figs = fig.registry_for(block, digest, led_block, anchors_block)
        evidence = '\n\n'.join(x for x in (digest, block, anchors_block) if x)
        user_prompt = pma.build_analysis_user_prompt(evidence, history, text, ledger_block=led_block) + figs.prompt_suffix() \
            + '\n\n' + brief(p, subject=name, window=meta.get('window') or '')
        res = G['_pm_claude_json'](pma.ANALYSIS_SYSTEM_PROMPT, user_prompt, max_tokens=6000, temperature=0.5, usage_extras=ppu)
        data = (res.get('data') or {}) if isinstance(res, dict) and res.get('success') else {}
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict)), {})
        reply = pma.scrub_user_text(str(data.get('reply') or '').strip()) if str(data.get('action') or 'answer') == 'answer' else ''
        fig_ledger = []
        if reply:
            try:
                reply, fig_ledger = fig.compose(reply, figs, block)
            except Exception:
                fig_ledger = []
        refusal = False
        try:
            refusal = bool(reply) and bool(G['_pm_reads_as_refusal'](reply, ''))
        except Exception:
            refusal = False
        if not reply or refusal or not re.search(r"\d", reply):
            # the pass gave nothing usable: the same shape, written from the rows
            reply = compose(p, subject=name, found=found, window=meta.get('window') or '', audience_people=meta.get('proj'))
            fig_ledger = []
            stage('insight_composed', count=1)
            if not reply:
                return None
        try:
            from . import arith_coherence as _arith
            if meta.get('proj'):
                ai = _arith.check(reply, counts=[('the file', int(meta['proj']))], subject=name)
                if ai.get('changed'):
                    reply = ai['text']
        except Exception:
            pass
        reply = finish(reply, p, subject=name, found=found, window=meta.get('window') or '')
        chips = _chips([pma.scrub_user_text(str(f).strip()) for f in (data.get('followups') or []) if str(f).strip()], p, subject=name)
        ask_hint(route=ROUTE, outcome='answered', subject=name)
        try:
            G['_shared'].after_answer(__import__('insights_ledger'), G['_pm_claude_data'], user=uname, question=text, reply=reply,
                                      subject=name, followups=chips, base_profile_key=str(base['s3_key']), figures=fig_ledger)
        except Exception:
            pass
        return G['jsonify']({'success': True, 'action': 'answer', 'reply': reply, 'figures': fig_ledger, 'followups': chips,
                             'offer_deck': False, 'deck_angle': None, 'model': (res or {}).get('model') if isinstance(res, dict) else None,
                             'profile': name, 'insight_ask': {'subject': name, 'for': _for_whom(p, name), 'angle': p.get('angle'),
                                                              'window': window_plain(meta.get('window') or ''),
                                                              'asked_window': p.get('window') or ''}})
    except Exception as e:
        try:
            from . import faults as _faults
            _faults.note('insight_ask', e)
        except Exception:
            pass
        return None
