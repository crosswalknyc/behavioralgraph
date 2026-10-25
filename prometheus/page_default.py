"""The open profile is context, never a default subject.

Jenna 2026-10-06: "strip the dashboard assumptions out of the lanes ...
just ensure it always asks to confirm." Jenna 2026-10-24, after Scott
Carlin's small-kitchen-appliance market question ran on LeBron James
because that profile was open: "it should never default to that."

What went wrong on 2026-10-24, in order: the quote named "Amazon" (the
first brand the Reader saw, though Amazon, Walmart and Target were the
places people shop, not the audience); the Approve re-resolved the base
from scratch and the resolver's page fallback picked the open profile;
the read ran on LeBron James; "no! Lebron james has nothing to do with
this" was read as a new question and ran on LeBron James again.

Three rules, all here so the generation pass in legacy/chat.py stays
small:

  1. quoted_subject: the subject a quote named is the subject the
     Approve runs on. One resolution, carried by the confirm.
  2. page_base_stands: a page base stands only when the words point at
     the page, name it or one of its rows, the thread already landed on
     it or confirmed it, or the reader bound it by chip. A page the
     reader has said no to (this turn or earlier) never stands.
  3. no_base_subject: with no base, the subject is the bound or quoted
     one, "Everyone in the US" when the reader picked the whole panel,
     the Reader's cohort as written, or nothing, in which case the
     which-audience question goes out (priced_read.which_audience) and
     nothing is quoted or run.
"""
from __future__ import annotations

import re
import traceback

EVERYONE = "Everyone in the US"
_PLACEHOLDERS = frozenset(('', 'that subject', 'this subject', 'the subject'))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def is_everyone(label):
    try:
        from .named_subject import is_everyone as _ie
        return bool(_ie(label))
    except Exception:
        return _norm(label) in ('everyoneintheus', 'everyone', 'genpop', 'generalpopulation', 'allusadults', 'usadults')


def quoted_subject(panel_confirm, bind_subject=''):
    """The subject to bind for this turn: the chip the reader tapped, else
    the subject the quote named (the Approve carries it back), else ''."""
    b = str(bind_subject or '').strip()
    if b:
        return EVERYONE if is_everyone(b) else b
    if isinstance(panel_confirm, dict):
        q = str(panel_confirm.get('subject') or '').strip()
        if q.lower() not in _PLACEHOLDERS:
            return EVERYONE if is_everyone(q) else q
    return ''


def page_rejected(text, history, page):
    """The reader said no to the page, now or earlier in the thread."""
    try:
        from . import subject_rules as _sr
        turns = list(history or []) + [{'role': 'user', 'text': str(text or '')}]
        return bool(_sr.page_rejected_in_thread(turns, page))
    except Exception:
        traceback.print_exc()
        return False


def page_base_stands(text, history, page, page_key, bind_subject='', *, normalize, stop_tokens,
                     brand_named=None, ask_user=''):
    """(stands, why). The page is the base only on a positive signal."""
    page = str(page or '').strip()
    if not page:
        return False, 'no page'
    if page_rejected(text, history, page):
        return False, 'rejected'
    b = str(bind_subject or '').strip()
    if b and normalize(b.split(' - ')[0]) == normalize(page.split(' - ')[0]):
        return True, 'bound'
    try:
        from . import named_subject as _ns
        if _ns.view_deixis(text):
            return True, 'deixis'
        if _ns.page_named(text, page, normalize, stop_tokens):
            return True, 'named'
        if _ns.thread_on_page(history or [], page, normalize):
            return True, 'thread on page'
    except Exception:
        traceback.print_exc()
    try:
        from .legacy import watch as _w
        if _w._pm_thread_confirmed_page(history, page):
            return True, 'thread confirmed'
        if ask_user and _w._pm_page_confirmed_recently(ask_user, page):
            return True, 'confirmed recently'
    except Exception:
        traceback.print_exc()
    try:
        if callable(brand_named) and page_key and brand_named(text, page_key):
            return True, 'row named'
    except Exception:
        traceback.print_exc()
    try:
        import prometheus_analysis as _pma
        if _pma.ask_is_anaphoric(text):
            return True, 'anaphora'
    except Exception:
        traceback.print_exc()
    return False, 'not pointed at'


def base_stands(text, history, base, bind_subject='', *, normalize, stop_tokens, brand_named=None, ask_user=''):
    """(stands, why) for a resolved base. A catalog base the words named
    stands unless the reader is saying no to that name; a page base needs
    a positive signal (page_base_stands)."""
    subj = str((base or {}).get('subject') or '')
    if page_rejected(text, history, subj):
        return False, 'rejected'
    if str((base or {}).get('source') or '') == 'page':
        return page_base_stands(text, history, subj, (base or {}).get('s3_key'), bind_subject,
                                normalize=normalize, stop_tokens=stop_tokens, brand_named=brand_named, ask_user=ask_user)
    return True, 'named'


def place_in_list(name, text):
    """`name` only appears as a place after "at", "on", "across" in a list
    of two or more ("shopping at Amazon, Walmart and Target")."""
    try:
        from . import subject_rules as _sr
        if not _sr._place_in_list(name, text):
            return False
        head_rx = re.compile(r"\b(?:at|on|across|between|among|from|via|through|in|to|with)\s+((?:(?:the\s+)?[\w&'.+-]+,?\s+(?:and\s+|or\s+|&\s+)?){1,6})",
                             re.I)
        for m in head_rx.finditer(str(text or '')):
            names = [w for w in re.findall(r"[A-Z][\w&'.+-]*", m.group(1))]
            if len(names) >= 2 and any(_norm(n) == _norm(name) for n in names):
                return True
        return False
    except Exception:
        return False


_FRAGMENT_TAIL = frozenset(('among', 'and', 'or', 'the', 'a', 'an', 'of', 'in', 'on', 'at', 'with', 'for', 'to', 'from',
                            'by', 'as', 'than', 'then', 'that', 'which', 'who', 'whose', 'where', 'when', 'but', 'so', 'if'))


def fragment(name):
    """A guess that crossed a sentence boundary or stopped on a connective
    is prose, not a subject ("Target. Among" from "... Walmart and
    Target. Among people who ...", 2026-10-24)."""
    n = ' '.join(str(name or '').split())
    if not n:
        return False
    if re.search(r"[.?!;:](?:\s|$)", n.rstrip('.?!')) or re.search(r"[.?!]\s+\w", n):
        return True
    toks = re.findall(r"[A-Za-z']+", n.lower())
    return bool(toks) and (toks[-1] in _FRAGMENT_TAIL or toks[0] in ('and', 'or', 'but', 'then', 'which', 'among'))


def not_a_subject(name, text, plausible=None, history=None):
    """A place in a list of places, a prose fragment, ordinary words, or a
    name the reader is saying no to ("no! LeBron James has nothing to do
    with this" names LeBron James to rule it out, 2026-10-24)."""
    n = str(name or '').strip()
    if not n:
        return True
    if fragment(n) or place_in_list(n, text) or page_rejected(text, history, n):
        return True
    try:
        return bool(callable(plausible)) and not plausible(n)
    except Exception:
        return False


def no_base_subject(text, page, bind_subject='', subj_hint='', panel_confirm=None, *, display_for, reading_cohort,
                    plausible):
    """The subject a no-base read is quoted and run on, or ''."""
    q = quoted_subject(panel_confirm, bind_subject)
    if q:
        return q
    hint = str(subj_hint or '').strip()
    if hint and is_everyone(hint):
        return EVERYONE
    name = ''
    try:
        name = str(display_for(hint, text) or '').strip() if hint else ''
    except Exception:
        traceback.print_exc()
    if name and not_a_subject(name, text, plausible):
        name = ''
    if not name:
        try:
            name = str(reading_cohort(text, page) or '').strip()
        except Exception:
            name = ''
    return name


__all__ = ['EVERYONE', 'is_everyone', 'quoted_subject', 'page_rejected', 'page_base_stands', 'base_stands', 'place_in_list', 'fragment', 'not_a_subject', 'no_base_subject']
