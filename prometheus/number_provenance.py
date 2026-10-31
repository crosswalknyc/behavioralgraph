"""Where a number came from (2026-10-30 audit, item 5).

Alexia asked "where does the 12,004,203 potential ticket buyers number
come from?" and got a status update twice, then "12 is not one of the
brands we carry." Every figure Prometheus delivers sits in the thread,
and the reads that compose their figures carry a ledger of them. A
question that names a figure and asks where it came from is answered
from the thread: the read it belongs to, the sentence that carried it,
its label and how it was counted.

Pure functions over the history; no model, no store. Kill switch
``PM_NUMBER_PROVENANCE=0``.
"""
from __future__ import annotations

import os
import re

_ASK_RX = re.compile(
    r"(?:where (?:does|did|do|is|are|was) (?:the |this |that |your )?(?:number |figure |count |total )?.{0,60}?\b(?:come|coming) from|"
    r"how (?:did|do|does|was|were) (?:you|prometheus|it|that|this) (?:get|arrive at|reach|calculate|compute|count|derive|come up with|land on)\b|"
    r"what (?:is|was) (?:the |this |that )?(?:number |figure )?.{0,40}?\b(?:based on|made of|counting)|"
    r"explain (?:the |this |that )?(?:number|figure|count)\b|"
    r"(?:source|basis|math|methodology) (?:of|for|behind) (?:the |this |that )?(?:number|figure|count)?|"
    r"why (?:is|does) (?:the |this |that )?(?:number|figure|count)? ?.{0,40}?\b(?:so|that) (?:high|low|big|small|large)|"
    r"(?:is|are) (?:the |this |that )?.{0,40}?\b(?:right|correct|accurate)\b)", re.I)
_NUM_RX = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?|\d+(?:\.\d+)?\s?%|\d+(?:\.\d+)?\s?(?:m|million|k|thousand)\b", re.I)
_YEAR_RX = re.compile(r"^(?:19|20)\d{2}$")
_SOURCE_WORDS = {
    'profile_file': 'read straight off the {subject} profile',
    'subscriber_iq_file': 'read off the Subscriber IQ tracker for {subject}',
    'journey_file': 'read off the {subject} journey',
    'campaign_file': "read off the campaign's own counts for {subject}",
    'catalog_fact': 'a figure already on file for {subject}',
    'ledger': 'a figure already on file for {subject}',
    'research_anchor': 'the anchor the {subject} read was sized against',
    'view_data': 'read off the view that was on screen',
    'derived': 'arithmetic on two figures in the same read',
    'model_typed': 'written into the reply rather than read from a file; treat it as a note beside the counted figures',
}


def enabled():
    return str(os.environ.get('PM_NUMBER_PROVENANCE', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _norm_num(tok):
    return re.sub(r"[,\s]", '', str(tok or '')).lower()


def numbers_in(text):
    out = []
    for m in _NUM_RX.finditer(str(text or '')):
        tok = m.group(0).strip()
        if _YEAR_RX.match(_norm_num(tok)):
            continue
        out.append(tok)
    return out


def detect(text):
    """The figure the question asks about, or '' when this is not a
    where-does-this-come-from question."""
    if not enabled():
        return ''
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 600 or not _ASK_RX.search(t):
        return ''
    nums = numbers_in(t)
    return nums[0] if nums else ''


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", str(text or '')) if s.strip()]


def _read_head(txt):
    m = re.match(r"^On ([^:]{2,120}?)(?:\s+-\s+[^:]{1,120})?:\s", txt)
    return m.group(1).strip() if m else ''


def _value_of(tok):
    s = _norm_num(tok)
    mult = 1.0
    if s.endswith('%'):
        s = s[:-1]
    elif s.endswith('million'):
        s, mult = s[:-7], 1e6
    elif s.endswith('m'):
        s, mult = s[:-1], 1e6
    elif s.endswith('thousand'):
        s, mult = s[:-8], 1e3
    elif s.endswith('k'):
        s, mult = s[:-1], 1e3
    try:
        return float(s) * mult
    except ValueError:
        return None


def find(number, history):
    """{'turn', 'sentence', 'subject', 'figure'} for the most recent agent
    turn that carried the number; None when no turn did."""
    key = _norm_num(number)
    val = _value_of(number)
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns):
        if str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        txt = ' '.join(str(t.get('text') or '').split())
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        hit_sentence = ''
        for s in _sentences(txt):
            if any(_norm_num(n) == key for n in numbers_in(s)):
                hit_sentence = s
                break
        fig = None
        for f in (meta.get('figures') or []):
            try:
                fv = float(f.get('value'))
            except (TypeError, ValueError, AttributeError):
                continue
            if val is not None and fv and abs(fv - val) <= max(0.5, abs(val) * 0.0005):
                fig = f
                break
        if hit_sentence or fig:
            subject = str(meta.get('subject') or _read_head(txt) or '').strip()
            return {'turn': t, 'sentence': hit_sentence, 'subject': subject, 'figure': fig}
    return None


def answer(text, history):
    """The provenance payload, or None when the question is not one or
    the number is nowhere in the thread (the caller falls through)."""
    number = detect(text)
    if not number:
        return None
    hit = find(number, history)
    if not hit:
        # the number is not in anything delivered here: say so, name what is
        turns = [t for t in (history or []) if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant')]
        recent = []
        for t in reversed(turns[-6:]):
            for n in numbers_in(str(t.get('text') or ''))[:6]:
                if n not in recent:
                    recent.append(n)
            if len(recent) >= 6:
                break
        if not recent:
            return None
        reply = (f"I do not see {number} in anything delivered in this thread. The figures in the last reads here are "
                 + ', '.join(recent[:6]) + ". If it came from a file or an export, tell me which one and I will trace it there.")
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False, 'deck_angle': None,
                'number_provenance': {'number': number, 'found': False}}
    subject = hit['subject'] or 'that read'
    fig = hit.get('figure') or {}
    label = str(fig.get('label') or '').strip()
    source = str(fig.get('source') or '').strip()
    parts = []
    if label:
        parts.append(f"{number} is {label} on {subject}.")
    else:
        _art = '' if re.match(r"^(?:the|that|this|a|an)\b", subject, re.I) else 'the '
        parts.append(f"{number} comes from {_art}{subject} read in this thread.")
    if hit.get('sentence'):
        parts.append(f"The line that carried it: \"{hit['sentence']}\"")
    how = _SOURCE_WORDS.get(source)
    if how:
        parts.append("How it was counted: " + how.format(subject=subject) + ".")
    elif re.search(r"\b(?:exposed|reached|touch|visit|play|view|watch|stream|bought|purchase|signed up|subscrib)", hit.get('sentence') or '', re.I):
        parts.append("It is a count of distinct US people in the window who did what that sentence says, each person counted once.")
    reply = ' '.join(parts)
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False, 'deck_angle': None,
            'subject': subject, 'number_provenance': {'number': number, 'found': True, 'source': source or None}}
