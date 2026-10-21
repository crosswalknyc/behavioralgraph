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

CONFIRM = "I will not answer until you accept."

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
_OFFER_COPY_RX = re.compile(
    r"I will not answer until you accept|It runs \$|Run the full read|Run the breakdown",
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


def offer(subject, price_label, question, kind='report', frame='', years=1):
    """(reply, followups, offer dict). Copy names the price and says
    we will not answer until they accept."""
    subj = str(subject or '').strip() or 'that subject'
    price = str(price_label or '').strip() or '$500'
    run = (f"Run the full read - {price}" if kind != 'derived'
           else f"Run the breakdown - {price}")[:160]
    if kind == 'sizing':
        reply = (f"{subj}: sample, share, and US people in the window. "
                 f"That is a {price} read. {CONFIRM}")
    elif kind == 'where_watched':
        reply = (f"Where people watched {subj}. That is a {price} read. "
                 f"{CONFIRM}")
    elif kind == 'list':
        reply = (f"The ranked list you asked for on {subj}. That is a "
                 f"{price} read. {CONFIRM}")
    elif kind == 'path':
        reply = (f"The path for {subj}. That is a {price} read. {CONFIRM}")
    elif kind == 'derived':
        reply = (f"I can add that title to the same read on {subj}. "
                 f"That is a {price} read. {CONFIRM}")
        run = (f"Run the breakdown - {price}")[:160]
    else:
        reply = (f"I can answer that as a custom read on {subj} and "
                 f"deliver the numbers in this chat. That is a {price} "
                 f"read. {CONFIRM}")
    offer_body = {'question': str(question or '')[:600],
                  'subject': subj[:120], 'kind': kind, 'frame': frame}
    if years and years != 1:
        offer_body['years'] = years
    return reply, [run, 'Never mind'], offer_body


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
        reply, chips, off = offer(subject, label, text, kind=offer_kind,
                                  frame=hit.get('frame') or '')
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
