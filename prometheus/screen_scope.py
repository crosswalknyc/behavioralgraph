"""What is on screen is never the subject or the data unless the words
point at it (Jenna 2026-11-20, verbatim: "ive repeatedly said not to
use whats on screen for context to always ask").

The one rule, in one place, for every path that could let the open view
(the profile in Profile IQ, the valuation, campaign or journey open on a
product tab) decide what a read is about or where its numbers come from:

  * The words name a subject: that subject (prometheus.subject_rules,
    the Understanding). Never the page.
  * The words point at the screen: the open view is the subject. Only an
    explicit pointer counts: "this profile", "the one I have open", "this
    valuation", "this campaign", "on my screen", "this page", "the open
    file", "these numbers". A pronoun or an audience noun on its own
    ("this audience", "these people", "they", "it") is NOT a pointer: it
    is the thread's subject when the thread has one, and a question when
    it has none.
  * The thread's own earlier subject (the reader's words, or a delivered
    answer) carries forward: a thread whose last answer landed on the page,
    or whose reader said "Yes, <page>", is on the page.
  * Nothing else makes the page the subject: not a word that matches a
    row of the open file ("beauty" on a profile with a BEAUTY row), not a
    page confirmed in another thread, not a topic with no other candidate,
    not a product view's vocabulary on the screen, not a reading flag on
    its own, not a default when nothing else resolved.
  * With no subject and no pointer, Prometheus asks one short question
    ("Which audience should I read for this?") and may offer the open view
    as ONE tap inside it. It never chooses it silently.

Pure module: no Flask, no S3, no model. Kill switch PM_SCREEN_SCOPE=0
restores the older page signals (never on a request field).
"""
from __future__ import annotations

import os
import re

__all__ = ['enabled', 'points_at_screen', 'page_is_subject', 'page_named', 'page_named_outright', 'thread_on_page', 'thread_confirmed_page',
           'POINTER_RX', 'QUESTION']

QUESTION = 'Which audience should I read for this?'

# the dashboard objects a reader can point at, and the ways of pointing
_OBJECT = (r"(?:profile|profiles|page|screen|tab|view|file|dashboard|valuation|campaign|journey|study|board|leaderboard|"
           r"chart|table|report|data\s*set|dataset|pull|read|universe|deck|window)")
POINTER_RX = re.compile(
    r"\b(?:this|that|the\s+open|the\s+current|the\s+active|my\s+open|the\s+selected|the\s+loaded)\s+" + _OBJECT + r"\b"
    r"|\bthe\s+(?:one|" + _OBJECT + r")\s+(?:i|we)\s+(?:have|'ve|ve)\s+(?:got\s+)?(?:open|up|loaded|selected|pulled\s+up)\b"
    r"|\bthe\s+(?:one|" + _OBJECT + r")\s+(?:that\s+is\s+|that's\s+|thats\s+)?(?:open|up|loaded|selected|showing)\b"
    r"|\bthe\s+(?:one|" + _OBJECT + r")\s+(?:that\s+is\s+|that's\s+)?on\s+(?:my\s+|the\s+|this\s+)?screen\b"
    r"|\bon\s+(?:my\s+|the\s+|this\s+)?screen\b|\bon\s+(?:this|the\s+open|the\s+current)\s+(?:page|tab|view)\b"
    r"|\bwhat\s+(?:i|we)\s+(?:have|'ve)\s+(?:got\s+)?(?:open|up|loaded|pulled\s+up)\b"
    r"|\bwhat(?:'s|\s+is)\s+(?:open|up|loaded|showing)(?:\s+(?:now|here|right\s+now))?\b"
    r"|\b(?:this|these|those)\s+(?:numbers?|figures?|results?|rows?|stats?|charts?|tables?)\b"
    r"|\bthe\s+(?:numbers|figures|results|rows|stats)\s+(?:here|above|below|on\s+screen|on\s+the\s+screen)\b"
    r"|\bhere\s+on\s+(?:the\s+|this\s+|my\s+)?(?:page|screen|tab|dashboard)\b"
    r"|(?<![\w'])here\b(?!\s*(?:'s|is|are|was|were|comes?|goes|you\s+go|to\b|in\s+(?:the\s+)?(?:us|united\s+states)))", re.I)
# "these people" / "this audience" beside a figure read off the screen (a count with its window, "inside a cohort of
# 1,368,062 US viewers in the January 19 to February 15, 2024 window. are these people new sign ups"): the reader is
# reading the open file back, which is pointing at it (Grace Yin 2026-11-08)
_AUDIENCE_DEMONSTRATIVE_RX = re.compile(r"\b(?:this|these|those)\s+(?:audience|people|viewers|users|members|folks|consumers|customers|subscribers|"
                                        r"accounts|sign[- ]?ups|signups|fans|buyers|shoppers|individuals|persons|cohort|group)\b", re.I)
_SCREEN_FIGURE_RX = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3}){1,}(?:\.\d+)?(?![\w.])|(?<![\w.])\d{5,}(?![\w.])")


def enabled():
    return str(os.environ.get('PM_SCREEN_SCOPE', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def points_at_screen(text):
    """True only when the words point at the open view itself."""
    t = ' '.join(str(text or '').split())
    if not t:
        return False
    if POINTER_RX.search(t):
        return True
    return bool(_AUDIENCE_DEMONSTRATIVE_RX.search(t) and _SCREEN_FIGURE_RX.search(t))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).strip()


def page_named(text, page, normalize=None, stop=()):
    """The words name the open page's own subject (its distinctive tokens
    all written), through prometheus.named_subject when it is available."""
    if not page:
        return False
    try:
        from . import named_subject as _ns
        return bool(_ns.page_named(text, page, normalize or _norm, stop or ()))
    except Exception:
        pass
    pg = _norm(str(page).split(' - ')[0])
    return bool(pg) and pg in _norm(text)


_VIEW_ENTITY_KEYS = ('show', 'title', 'name', 'subject', 'campaign', 'brand', 'talent', 'property', 'series', 'film', 'label',
                     'audience', 'partner', 'creator', 'client', 'network', 'platform', 'program')
_VIEW_GENERIC = frozenset(('the', 'a', 'an', 'of', 'and', 'or', 'on', 'in', 'for', 'to', 'at', 'by', 'with', 'vs', 'x',
                           'audience', 'audiences', 'profile', 'campaign', 'journey', 'report', 'study', 'window', 'week', 'total',
                           'overall', 'premiere', 'episode', 'episodes', 'season', 'series', 'show', 'film', 'movie', 'launch',
                           'trailer', 'reviews', 'opening', 'conversion', 'conversions', 'signups', 'checkout', 'ticket', 'tickets',
                           'first', 'last', 'new', 'viewers', 'fans', 'users', 'people', 'data', 'value', 'brand', 'partnership'))


def _norm_words(text):
    return ' '.join(re.sub(r"[^a-z0-9 ]+", ' ', str(text or '').lower().replace('&', ' and ')).split())


def view_entities(view_ctx, depth=3):
    """What the open view is about, read from its own record: the show, campaign, title, brand or talent it carries
    (subject level) and the full labels of the items on it (episodes, assets, steps). Never the product's own name.
    Returns (subject_names, item_names)."""
    vc = view_ctx if isinstance(view_ctx, dict) else {}
    subjects, items = [], []

    def walk(node, d, in_list):
        if d < 0:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and str(k).lower() in _VIEW_ENTITY_KEYS and 1 < len(v.strip()) <= 80:
                    (items if in_list else subjects).append(v.strip())
                elif isinstance(v, (dict, list)) and str(k).lower() not in ('view_title', 'view_id'):
                    walk(v, d - 1, in_list)
        elif isinstance(node, list):
            for v in node[:400]:
                walk(v, d - 1, True)
    walk(vc.get('data'), depth, False)
    for k in ('subject', 'show', 'title', 'name'):
        v = vc.get(k)
        if isinstance(v, str) and v.strip():
            subjects.append(v.strip())
    return subjects, items


def view_named(text, view_ctx):
    """True when the reader's own words name what the open view is about: its subject (the show, campaign, brand or
    talent the view carries, as one distinctive word or whole) or the full label of an item on it typed whole
    ("how did Sins of the Father do against Landman overall?"). A generic word that happens to sit on the screen
    ("checkout", "conversion", "beauty") never counts: that is the screen deciding, which it never does."""
    t = f" {_norm_words(text)} "
    if len(t) < 4:
        return False
    subjects, items = view_entities(view_ctx)
    for name in subjects:
        n = _norm_words(name)
        toks = [w for w in n.split() if w not in _VIEW_GENERIC]
        if not toks:
            continue
        if f" {n} " in t:
            return True
        if len(toks) == 1 and len(toks[0]) >= 4 and re.search(rf"\b{re.escape(toks[0])}\b", t):
            return True
        hits = [w for w in toks if re.search(rf"\b{re.escape(w)}\b", t)]
        if len(toks) >= 2 and (len(hits) >= 2 or (len(hits) == 1 and len(hits[0]) >= 5 and len(toks) <= 4)):
            return True   # "the Lexus campaign" names Lexus x Colin and Samir; a shared short word does not
    for name in items:
        n = _norm_words(name)
        toks = [w for w in n.split() if w not in _VIEW_GENERIC]
        if len(toks) >= 2 and f" {n} " in t:
            return True
    return False


def thread_on_page(history, page, normalize=None):
    """This thread's latest answer landed on the page (a delivered answer
    is a thread subject and carries forward)."""
    if not page:
        return False
    try:
        from . import named_subject as _ns
        return bool(_ns.thread_on_page(history or [], page, normalize or (lambda s: _norm(s).replace(' ', ''))))
    except Exception:
        return False


_YES_RX = re.compile(r"^\W*(?:yes|use|go with|on)[,:\s]+(?P<name>.+?)\W*$", re.I)


def thread_confirmed_page(history, page, normalize=None):
    """The reader said "Yes, <page>" (or tapped "Use <page>" / "<page>
    (open on your screen)") earlier in THIS thread, or named the page
    outright in one of their last turns: their own words put the thread
    on the page. A confirm in another thread never counts."""
    if not page:
        return False
    norm = normalize or (lambda s: _norm(s).replace(' ', ''))
    pg = norm(str(page).split(' - ')[0])
    if not pg:
        return False
    seen = 0
    for t in reversed([h for h in (history or []) if isinstance(h, dict)]):
        if str(t.get('role') or '').lower() not in ('user', 'human'):
            continue
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split())
        if not txt:
            continue
        m = _YES_RX.match(txt)
        if m and norm(re.sub(r"\(open on your screen\)", '', m.group('name'), flags=re.I).split(' - ')[0]) == pg:
            return True
        if norm(re.sub(r"\(open on your screen\)", '', txt, flags=re.I).split(' - ')[0]) == pg:
            return True
        seen += 1
        if seen <= 4 and page_named_outright(txt, page, normalize):
            return True   # the reader wrote the page's name out in one of their last turns
    return False


def page_named_outright(text, page, normalize=None, stop=()):
    """The words write the page's subject out in full (every distinctive
    word of its family name), through prometheus.named_subject."""
    if not page:
        return False
    try:
        from . import named_subject as _ns
        return bool(_ns.page_named_outright(text, page, normalize or _norm, stop or ()))
    except Exception:
        pg = _norm(str(page).split(' - ')[0])
        return bool(pg) and pg in _norm(text)


def page_is_subject(text, history, page, *, normalize=None, stop=(), bind_subject=''):
    """(True, why) when the open page may be this ask's subject; (False,
    why) otherwise. The reasons a reader could give themselves: 'bound'
    (a chip or an approved quote named it), 'pointed' (the words point at
    the screen), 'named' (the words name it), 'thread on page' (the
    thread's last answer landed on it), 'thread confirmed' (the reader
    said yes to it in this thread). Nothing else."""
    pg = str(page or '').strip()
    if not pg:
        return False, 'no page'
    norm = normalize or (lambda s: _norm(s).replace(' ', ''))
    b = str(bind_subject or '').strip()
    if b and norm(b.split(' - ')[0]) == norm(pg.split(' - ')[0]):
        return True, 'bound'
    if page_rejected(text, history, pg):
        return False, 'rejected'   # a page the reader said no to, now or earlier in the thread, is never the subject
    if points_at_screen(text):
        return True, 'pointed'
    if page_named(text, pg, normalize, stop):
        return True, 'named'
    if thread_on_page(history, pg, normalize):
        return True, 'thread on page'
    if thread_confirmed_page(history, pg, normalize):
        return True, 'thread confirmed'
    return False, 'not pointed at'


def page_rejected(text, history, page):
    """The reader said no to the page in these words, in an earlier turn of the thread, or in the correction this
    ask continues from ("no! Lebron james hs nothing to do with this"). Never raises."""
    try:
        from . import subject_rules as _sr
        turns = [t for t in (history or []) if isinstance(t, dict)]
        extra = []
        try:
            from flask import g as _g
            cf = str(getattr(_g, '_pm_continued_from', '') or '')
            if cf:
                extra.append({'role': 'user', 'text': cf})
        except Exception:
            pass
        return bool(_sr.page_rejected_in_thread(turns + extra + [{'role': 'user', 'text': str(text or '')}], page))
    except Exception:
        return False
