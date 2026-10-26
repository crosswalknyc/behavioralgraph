"""A yes/no question about the read that just landed is answered from
that read (2026-10-26).

Jay Patel asked, after a Love Hypothesis read that listed Off Campus at
39.6%: "is the film you started with The Love Hypotheis, if so I should
expect Off Campus to be on the list?" The lanes read "The Love
Hypotheis" as a new subject and quoted $100. The read itself held the
answer. This module finds the names the question carries inside the
last read's own words and answers with the read's lines, free, with no
model call.

answer(text, history) -> {'reply', 'subject', 'found', 'missing'} or
None when the turn is not this shape (no landed read behind it, not a
question, or no name to look up).
"""
from __future__ import annotations

import re

from . import subject_rules as _sr

_YES_NO_RX = re.compile(
    r"^\W*(?:so\s+|and\s+|ok\s+|just\s+(?:to\s+)?(?:confirm|check)[,:]?\s+|to\s+confirm[,:]?\s+)?"
    r"(?:is|are|was|were|does|do|did|should|can|could|will|would|has|have|had|isn'?t|aren'?t|wasn'?t|doesn'?t|didn'?t)\b", re.I)
_IF_SO_RX = re.compile(r"\bif\s+so\b|\bthen\s+i\s+(?:should|would|assume|expect)\b|\bi\s+(?:should|would)\s+expect\b|\bon\s+the\s+list\b|\bin\s+the\s+list\b", re.I)
_CAP_RX = re.compile(r"\b[A-Z][A-Za-z0-9'&+.-]*(?:(?:,\s*|\s+)(?:of\s+|the\s+|and\s+|&\s+)?[A-Z][A-Za-z0-9'&+.-]*)*")
_SKIP = frozenset(('i', 'if', 'so', 'is', 'the', 'a', 'an', 'yes', 'no', 'ok', 'okay', 'prometheus', 'crosswalk', 'us', 'usa', 'tv',
                   'and', 'or', 'but', 'then', 'that', 'this', 'it', 'what', 'which', 'how', 'when', 'where', 'who', 'why',
                   'does', 'do', 'did', 'was', 'were', 'are', 'should', 'can', 'could', 'will', 'would', 'has', 'have'))


def _norm(s):
    n = re.sub(r"[^a-z0-9]+", '', str(s or '').lower())
    return n[3:] if n.startswith('the') and len(n) > 6 else n


def _close(a, b):
    """One name is the other up to a small spelling slip (Hypotheis / Hypothesis)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or (min(len(na), len(nb)) >= 6 and (na in nb or nb in na)):
        return True
    if abs(len(na) - len(nb)) > 2 or min(len(na), len(nb)) < 8:
        return False
    # edit distance <= 2, cheap
    prev = list(range(len(nb) + 1))
    for i, ca in enumerate(na, 1):
        cur = [i] + [0] * len(nb)
        for j, cb in enumerate(nb, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > 2:
            return False
        prev = cur
    return prev[-1] <= 2


def _names(text):
    out = []
    for m in _CAP_RX.finditer(str(text or '')):
        words = [w for w in re.findall(r"[A-Za-z0-9'&+.,-]+", m.group(0))]
        if m.start() == 0 and len(words) == 1:
            continue
        keep = [w for w in words if w.lower().strip(".',") not in _SKIP]
        if not keep:
            continue
        name = ' '.join(keep).strip(" .,'")
        if len(_norm(name)) >= 3 and name not in out:
            out.append(name)
    return out


_STATUS_LINE_RX = re.compile(r"^\s*(?:Your (?:read|profile|deck|Digital Journey|Brand Partnership|Attribution|Flywheel)\b|Still on it\b|Already on it\b|On it:|Working on it\b|I do not have anything running)", re.I)


def _last_read(history, lookback=6):
    """The most recent landed read in the thread: a tracked read turn or
    one that opens 'On <subject>:'. A status line, a promise or a
    question that came after it is stepped over (the status lane often
    answers between the read and the follow-up)."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    seen = 0
    for t in reversed(turns):
        if str(t.get('role') or '') == 'user':
            continue
        seen += 1
        if seen > lookback:
            break
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split('\r'))
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if meta.get('read_job_id') or str(meta.get('kind') or '') == 'read' or re.match(r"^On\s+[^:\n]{1,120}:", txt):
            return txt
        if _STATUS_LINE_RX.match(txt) or re.search(r"\?\s*$", txt.strip()) or meta.get('options') or meta.get('panel_offer'):
            continue
        if len(txt) >= 200 and re.search(r"\d", txt):
            return txt
    return ''


def _lines_with(read, name):
    out = []
    for raw in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z0-9])", read):
        line = ' '.join(raw.split()).strip(' -')
        if not line:
            continue
        toks = [w for w in re.findall(r"[A-Za-z0-9'&+.-]+", name)]
        if all(re.search(r"\b" + re.escape(w) + r"\b", line, re.I) for w in toks) or _close(name, line[:len(name) + 4]):
            out.append(line)
    return out


def answer(text, history):
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 400:
        return None
    if not (_YES_NO_RX.match(t) or _IF_SO_RX.search(t)) or '?' not in t and not _IF_SO_RX.search(t):
        return None
    read = _last_read(history)
    if not read:
        return None
    names = _names(t)
    if not names:
        return None
    head = re.match(r"^On\s+(?P<who>[^:\n(]{1,120}?)(?:\s*\([^)]*\))?\s*(?:-\s*[^:\n]{1,80})?:", read)
    subject = _sr.clean_display(re.split(r",\s+|\s+-\s+", head.group('who').strip())[0]) if head else ''
    found, missing, lines = [], [], []
    for n in names:
        if subject and _close(n, subject):
            found.append(n)
            continue
        hits = _lines_with(read, n)
        if hits:
            found.append(n)
            lines.extend(h for h in hits[:2] if h not in lines)
        else:
            missing.append(n)
    about_list = bool(_IF_SO_RX.search(t)) or bool(re.search(r"\b(?:that|the|this)\s+(?:list|read|table|set|breakdown)\b", t, re.I))
    if not found and not about_list:
        return None
    parts = []
    if subject and any(_close(n, subject) for n in names):
        parts.append(f"Yes, that read was on {subject}.")
    elif subject:
        parts.append(f"That read was on {subject}.")
    if lines:
        parts.append("Here is what it holds on " + ', '.join(n for n in found if not (subject and _close(n, subject))) + ':')
        parts.extend('- ' + ln for ln in lines[:6])
    for n in missing:
        parts.append(f"{n} is not in that read.")
    if not parts:
        return None
    return {'reply': '\n'.join(parts), 'subject': subject, 'found': found, 'missing': missing}


__all__ = ['answer']
