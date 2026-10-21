"""Confirm-before-numbers for new data (Jenna 2026-10-21).

Jay Patel, Emmet Riley and Debi Pomerantz were getting incrementality,
path tables, ranked lists, where-watched and sample sizes at the
metered chat rate. Jenna: quote first, they accept, then answer.
Sizing is $100 (same-day amendment; rule 26 had been free). First new
frame is $500. Extra titles in the same thread frame are $100. Talk
on a paid file stays metered. Kartel stickers are untouched.

A Tania-style on-service split of a held audience stays the free
cross (rule 48). The ten million panel line stays free. Insights,
"what does this mean" and "where do I find it" stay metered.
"""
from __future__ import annotations

import re

# The one offer shape (Jenna 2026-10-21, twice: "it should not say 'I will
# not answer until you accept', just do the chips approve | cancel"). One
# sentence on what the reader gets, then the cost line, then two chips.
# Never a refusal, never 'That is a $N read', never 'It runs $N'.
COST_LINE = "Pulling this data will cost {price}."
APPROVE_CHIP = "Approve"
CANCEL_CHIP = "Cancel"
# Kept under its historic name for callers that import it; the offer text
# no longer carries a refusal sentence.
CONFIRM = ""

_TALK_RX = re.compile(
    r"\b(?:what does (?:this|that|it|the (?:number|figure|percent)).{0,24}mean|"
    r"craziest insights|what does (?:this|the) data tell|"
    r"where (?:do|can) i (?:find|see|get)|"
    r"are you still working|still (?:working|running)|"
    r"rewrite|script for|"
    r"is (?:it |that )?(?:on|in) (?:the |that |this )?(?:list|table|csv)|"
    r"how do you know)\b",
    re.I)
_LIST_RX = re.compile(
    r"\b(?:top\s+\d+|ranked?\s+lists?|who are the (?:top|biggest)|"
    r"list (?:of )?(?:the )?(?:top|biggest))\b.{0,80}"
    r"\b(?:creators?|influencers?|youtubers?|titles?|shows?|films?|"
    r"webtoons?|books?)\b",
    re.I)
_INC_RX = re.compile(
    r"\b(?:how\s+incremental(?:ly)?|incrementality|"
    r"incremental\s+(?:to|for|reach|lift|viewers?))\b",
    re.I)
# An offer turn in a thread (the current shape and the retired ones) is
# never mistaken for a delivered read when a frame is checked.
_OFFER_COPY_RX = re.compile(
    r"Pulling this data will cost|I will not answer until you accept|It runs \$|"
    r"Run the full read|Run the breakdown",
    re.I)


def is_talk(text):
    """Insights, meaning, file-find, in-flight: metered, never a quote."""
    t = ' '.join(str(text or '').split())
    return bool(t) and bool(_TALK_RX.search(t))


def is_ranked_list(text):
    t = ' '.join(str(text or '').split())
    return bool(t) and bool(_LIST_RX.search(t))


def frame_of(kind, text):
    t = ' '.join(str(text or '').lower().split())
    if kind in ('overlap', 'path') and _INC_RX.search(t):
        target = 'prime video' if 'prime' in t else 'generic'
        return 'incrementality|' + target
    if kind == 'list':
        if 'youtube' in t:
            return 'list|youtube'
        return 'list|generic'
    if kind == 'path':
        return 'path'
    if kind == 'where_watched':
        return 'where_watched'
    if kind == 'sizing':
        return 'sizing'
    if kind == 'viewership':
        return 'viewership'
    return str(kind or 'report')


def _looks_delivered(agent_text):
    t = str(agent_text or '')
    if not t or _OFFER_COPY_RX.search(t):
        return False
    return len(t) > 80 and bool(re.search(r'\d', t))


def same_frame(history, frame):
    """True when this thread already delivered a paid (or previously
    answered) read on the same frame, so the next title is $100."""
    if not frame:
        return False
    last_user = ''
    for t in history or []:
        if not isinstance(t, dict):
            continue
        role = str(t.get('role') or '')
        if role == 'user':
            last_user = str(t.get('text') or t.get('content') or '')
            continue
        if role not in ('agent', 'assistant') or not last_user:
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        paid = meta.get('priced_paid') or t.get('priced_paid')
        if isinstance(paid, dict) and str(paid.get('frame') or '') == frame:
            return True
        prev = classify(last_user, history=None)
        if prev and prev.get('frame') == frame and _looks_delivered(
                t.get('text') or t.get('content') or ''):
            return True
    return False


def _cross_ask(text):
    try:
        from . import cross_sizing as _cx
        return bool(_cx.parse(text))
    except Exception:
        return False


def _panel_self(text, ctx=None):
    try:
        from . import sizing_lane as _sz
        if not _sz.wants_sizing(text):
            return False
        subs = [s for s in _sz.subjects(text, ctx) if not _sz.is_panel_noun(s)
                and s != '__page__']
        return not subs
    except Exception:
        return False


def _sizing_subject(text, ctx=None):
    try:
        from . import sizing_lane as _sz
        for s in _sz.subjects(text, ctx):
            if s and s != '__page__' and not _sz.is_panel_noun(s):
                return _sz.strip_audience_nouns(s) or s
        page = ''
        try:
            page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
        except Exception:
            page = ''
        return page or 'that audience'
    except Exception:
        return 'that audience'


def _where_subject(text, history=None, ctx=None):
    try:
        from . import where_watched as _ww
        page = ''
        try:
            page = str(((ctx or {}).get('primary') or {}).get('name') or '')
        except Exception:
            page = ''
        parsed = _ww.parse(text, history, page=page)
        if parsed and parsed.get('title'):
            return str(parsed['title'])
    except Exception:
        pass
    return ''


def classify(text, history=None, ctx=None):
    """A priced read, or None when the ask stays metered / free / another
    product path. ``via`` is 'service' for deterministic lanes (sizing,
    where-watched) and 'generate' for compiled new-numbers."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 800:
        return None
    if is_talk(t):
        return None
    if _cross_ask(t):
        return None
    try:
        from . import overlap_read as _ov
        if _ov.is_overlap_read(t):
            kind = 'overlap'
            if re.search(r"\b(?:30[- ]days?|thirty\s+days?|t30d|after|next)\b", t, re.I) \
                    and re.search(r"\b(?:watch|stream|read)\b", t, re.I):
                kind = 'path'
            frame = frame_of(kind, t)
            addon = same_frame(history, frame) if history is not None else False
            return {'kind': kind, 'via': 'generate', 'usd': 100 if addon else 500,
                    'same_frame': addon, 'frame': frame,
                    'subject': ''}
    except Exception:
        pass
    if is_ranked_list(t):
        frame = frame_of('list', t)
        addon = same_frame(history, frame) if history is not None else False
        return {'kind': 'list', 'via': 'generate', 'usd': 100 if addon else 500,
                'same_frame': addon, 'frame': frame, 'subject': ''}
    try:
        from . import where_watched as _ww
        page = ''
        try:
            page = str(((ctx or {}).get('primary') or {}).get('name') or '')
        except Exception:
            page = ''
        if _ww.parse(t, history, page=page):
            frame = frame_of('where_watched', t)
            addon = same_frame(history, frame) if history is not None else False
            return {'kind': 'where_watched', 'via': 'service',
                    'usd': 100 if addon else 500, 'same_frame': addon,
                    'frame': frame, 'subject': _where_subject(t, history, ctx)}
    except Exception:
        pass
    try:
        from . import sizing_lane as _sz
        if _sz.wants_sizing(t) and not _panel_self(t, ctx):
            frame = frame_of('sizing', t)
            return {'kind': 'sizing', 'via': 'service', 'usd': 100,
                    'same_frame': False, 'frame': frame,
                    'subject': _sizing_subject(t, ctx)}
    except Exception:
        pass
    return None


def needs_generate_quote(text, history=None, ctx=None):
    """Compiled new-numbers that must quote even when a file is open."""
    hit = classify(text, history, ctx)
    return bool(hit and hit.get('via') == 'generate')


def price_plan(hit, username=None):
    """(label, credits, pull_type, offer_kind) for a classify() hit."""
    usd = int((hit or {}).get('usd') or 500)
    kind = str((hit or {}).get('kind') or 'report')
    if usd <= 100 or kind == 'sizing':
        label, credits = _derived_price(username)
        return label, credits, 'Derived Read', 'sizing' if kind == 'sizing' else 'derived'
    label, credits = _panel_price(username)
    offer_kind = 'report'
    if kind == 'where_watched':
        offer_kind = 'where_watched'
    elif kind in ('overlap', 'path', 'list'):
        offer_kind = kind
    return label, credits, 'Panel Report', offer_kind


def _derived_price(username=None):
    try:
        from prometheus.legacy.watch import _pm_derived_read_price
        return _pm_derived_read_price(username)
    except Exception:
        return '$100', 1


def _panel_price(username=None):
    try:
        from prometheus.legacy.watch import _pm_panel_price_label
        return _pm_panel_price_label(username)
    except Exception:
        return '$500', 5


_PLACEHOLDER_SUBJECTS = frozenset({
    'that subject', 'that audience', 'that title', 'this audience',
    'this subject', 'this title', 'the page', '__page__'})
_WH_LEAD_RX = re.compile(r"^(?:how|what|which|where|who|when|why)\b", re.I)
_SIZE_PREFIX_RX = re.compile(
    r"^(?:the\s+)?(?:market\s+size\s+(?:for|of)|size\s+of|audience\s+size\s+(?:for|of)|"
    r"sample\s+size\s+(?:for|of)|how\s+big\s+is|how\s+many)\s+(?:the\s+)?", re.I)
_SHARE_OF_RX = re.compile(
    r"(?:how\s+penetrated\s+is|penetration\s+of|share\s+of|how\s+(?:much|far)\s+(?:does\s+)?)\s*"
    r"(?P<plat>[A-Z][\w+&'. -]{1,40}?)(?:\s+(?:in|into|across|on)\b|'s\b|\?|$)")


def _fold(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def display_subject(subject, question='', page=''):
    """The subject a reader sees in an offer: the binder's clean display
    for the words in hand, '' when the raw is a placeholder, a metric
    phrase, a question fragment, a descriptor ('market size for ...'),
    or the open page's own label that the words did not name
    (2026-10-21, Casey Pearson's offer read 'market size for prank
    content', the open profile's title; Liz Huszarik's read 'The path
    for Netflix', the open tab). An empty display writes the line
    without a subject rather than with the wrong one."""
    s = ' '.join(str(subject or '').split()).strip(" ,;:.'\"")
    if not s or s.lower() in _PLACEHOLDER_SUBJECTS or _WH_LEAD_RX.match(s):
        return ''
    if _SIZE_PREFIX_RX.match(s):
        return ''
    try:
        from . import subject_rules as _sr
        if _sr.is_metric_phrase(s):
            return ''
        if page and _fold(s) == _fold(page):
            # the page's label stands only when the words bind it; a
            # subject the words bind instead replaces it; otherwise the
            # line is written without one
            q = ' '.join(str(question or '').split())
            try:
                b = _sr.bind(q, None, '')
            except Exception:
                b = None
            if b and b.source == 'words' and b.display:
                return _sr.clean_display(b.display, q) or ''
            return ''
        return _sr.clean_display(s, question) or ''
    except Exception:
        return s


def _join(parts):
    parts = [p for p in parts if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return ', '.join(parts[:-1]) + ' and ' + parts[-1]


def sizing_lead(text, ctx=None):
    """The one sentence a sizing offer opens with, built from the things
    the ask names: 'I can size the market for prank content, horror and
    comedy and Paramount+'s share of each.' '' when nothing in the ask
    names a thing (the caller writes the neutral lead)."""
    t = ' '.join(str(text or '').split())
    things = []
    try:
        from . import sizing_lane as _sz
        from . import subject_rules as _sr
        for s in _sz.subjects(t, ctx):
            s = ' '.join(str(s or '').split()).strip(" ,;:.'\"")
            if not s or s == '__page__' or _sz.is_panel_noun(s) or _WH_LEAD_RX.match(s):
                continue
            s = _SIZE_PREFIX_RX.sub('', s).strip(" ,;:.'\"")
            if not s or _sr.is_metric_phrase(s):
                continue
            s = _sz.strip_audience_nouns(s) or s
            if s and s.lower() not in [x.lower() for x in things]:
                things.append(s)
    except Exception:
        things = []
    if not things:
        return ''
    market = bool(re.search(r"\bmarket\s+size\b", t, re.I))
    lead = ("I can size the market for " if market else "I can size ") + _join(things)
    m = _SHARE_OF_RX.search(t)
    if m:
        plat = m.group('plat').strip(" ,;:.'\"")
        if plat and plat.lower() not in [x.lower() for x in things]:
            lead += f" and {plat}'s share of each" if len(things) > 1 else f" and {plat}'s share of it"
    return lead + '.'


def _lead(kind, subj):
    """The default first sentence per offer kind; written without a
    subject when none reads clean."""
    if kind == 'sizing':
        return (f"I can size {subj}: share and US people in the window."
                if subj else "I can size this audience: share and US people in the window.")
    if kind == 'where_watched':
        return (f"I can pull where people watched {subj}." if subj
                else "I can pull where people watched it.")
    if kind == 'list':
        return (f"I can pull the ranked list you asked for on {subj}." if subj
                else "I can pull the ranked list you asked for.")
    if kind == 'path':
        return (f"I can pull the path for {subj}." if subj
                else "I can pull the path you asked for.")
    if kind == 'overlap':
        return (f"I can pull the overlap you asked for on {subj}." if subj
                else "I can pull the overlap you asked for.")
    if kind == 'derived':
        return (f"I can add that title to the same read on {subj}." if subj
                else "I can add that title to the same read.")
    return (f"I can pull that as a custom read on {subj} and deliver the numbers in this chat."
            if subj else "I can pull that as a custom read and deliver the numbers in this chat.")


def page_name(ctx=None):
    """The open profile's label from the page context, else from the
    request's page context (flask.g._pm_page_ctx, set at the door), ''
    when none."""
    try:
        name = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
        if name:
            return name
    except Exception:
        pass
    try:
        from flask import g as _g
        pc = getattr(_g, '_pm_page_ctx', None) or {}
        return str(((pc or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def offer(subject, price_label, question, kind='report', frame='', years=1, what='', page=''):
    """(reply, followups, offer dict). One sentence on what the reader
    gets, then 'Pulling this data will cost $N.', then Approve / Cancel.
    ``what`` is a caller-written first sentence (the sizing lead); the
    subject shown is the binder's clean display, never the page label
    (pass ``page`` so the open profile's name is recognized)."""
    raw_subj = str(subject or '').strip()
    price = str(price_label or '').strip() or '$500'
    subj = display_subject(raw_subj, question, page or page_name())
    lead = ' '.join(str(what or '').split()) or _lead(kind, subj)
    reply = (lead + ' ' if lead else '') + COST_LINE.format(price=price)
    offer_body = {'question': str(question or '')[:600],
                  'subject': (raw_subj or subj or 'that subject')[:120],
                  'kind': kind, 'frame': frame}
    if years and years != 1:
        offer_body['years'] = years
    return reply, [APPROVE_CHIP, CANCEL_CHIP], offer_body


def out_of_credits(subject, price_label):
    subj = str(subject or 'that read').strip()
    price = str(price_label or '').strip()
    return {
        'success': False, 'guidance': True, 'analysis_read': True,
        'error': (f"The {subj} read runs {price} and your account cannot "
                  f"cover it right now. Top up or ask your admin, and I "
                  f"will pick it right back up."),
        'followups': [],
    }


def charge(host, username, subject, pull_type, credits, description):
    """True when the seat covered it (or is unlimited). False = no funds."""
    uname = str(username or '').strip()
    if not uname:
        return True
    consume = None
    if host is not None and hasattr(host, 'consume_credit'):
        consume = host.consume_credit
    if consume is None:
        return True
    try:
        return bool(consume(uname, description=description,
                            pull_type=pull_type, credits_used=int(credits or 1),
                            quantity=1))
    except Exception:
        return False


def service_turn(text, *, user=None, ctx=None, history=None, host=None,
                 panel_confirm=None):
    """Offer, charge-then-answer, or None (let the other lanes run).

    Handles sizing and where-watched here so a confirm never falls
    through to a $500 compiled read. Overlap / list / path return
    None so generate_metrics quotes them.
    """
    if host is None:
        try:
            from .host import host as _h
            host = _h
        except Exception:
            host = None
    uname = ''
    try:
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip()
    except Exception:
        uname = ''
    pc = panel_confirm if isinstance(panel_confirm, dict) else None
    pc_kind = str((pc or {}).get('kind') or '')
    hit = classify(text, history, ctx)
    if pc and pc_kind in ('sizing', 'where_watched') and not hit:
        hit = {'kind': pc_kind, 'via': 'service',
               'usd': 100 if pc_kind == 'sizing' else 500,
               'same_frame': pc_kind != 'sizing' and same_frame(
                   history, frame_of(pc_kind, text)),
               'frame': frame_of(pc_kind, text),
               'subject': str(pc.get('subject') or '')}
        if hit['same_frame']:
            hit['usd'] = 100
    if not hit or hit.get('via') != 'service':
        return None
    subject = str(hit.get('subject') or (pc or {}).get('subject') or '').strip()
    if not subject:
        subject = (_sizing_subject(text, ctx) if hit['kind'] == 'sizing'
                   else _where_subject(text, history, ctx) or 'that title')
    label, credits, pull_type, offer_kind = price_plan(hit, uname or None)
    if pc is None:
        what = sizing_lead(text, ctx) if hit['kind'] == 'sizing' else ''
        reply, chips, off = offer(subject, label, text, kind=offer_kind,
                                  frame=hit.get('frame') or '', what=what,
                                  page=page_name(ctx))
        return {
            'success': True, 'action': 'answer', 'reply': reply,
            'followups': chips, 'offer_deck': False, 'deck_angle': None,
            'panel_offer': off, 'subject': subject,
        }
    if hit['kind'] == 'sizing' and pc_kind not in ('sizing', 'derived', ''):
        return None
    if hit['kind'] == 'where_watched' and pc_kind not in (
            'where_watched', 'report', ''):
        return None
    desc = (('Audience size - ' if hit['kind'] == 'sizing'
             else 'Where watched - ') + subject)
    if not charge(host, uname, subject, pull_type, credits, desc):
        return out_of_credits(subject, label)
    raw = None
    try:
        from . import guards as _g
        staff = _g.is_staff(user)
    except Exception:
        staff = False
    if hit['kind'] == 'sizing':
        from . import sizing_lane as _sz
        raw = _sz.answer(text, ctx, host=host, history=history, staff=staff)
    else:
        from . import where_watched as _ww
        raw = _ww.answer(text, ctx, host=host, user=user, history=history)
    if not raw:
        return {
            'success': True, 'action': 'answer',
            'reply': f"I could not finish that {hit['kind'].replace('_', ' ')} on {subject}. Ask again and I will retry.",
            'followups': [], 'offer_deck': False, 'deck_angle': None,
            'priced_paid': {'kind': hit['kind'], 'frame': hit.get('frame'),
                            'usd': hit.get('usd')},
            'subject': subject,
        }
    out = dict(raw)
    out['priced_paid'] = {'kind': hit['kind'], 'frame': hit.get('frame'),
                          'usd': hit.get('usd')}
    out.setdefault('subject', subject)
    return out
