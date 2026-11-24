"""Which audience a question is about when the words do not say: the base clarify tokens and questions, the file ask, the clarify turn and its decline, answer-this-now, and the merge of a clarify answer into the ask.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import re
from prometheus import named_subject as _ns_mod

# <<moved>>
# <<block: 1>>
_PM_BASE_GENERIC_TOKENS = {
    'viewers', 'viewer', 'fans', 'fan', 'series', 'audience', 'buyers',
    'buyer', 'shoppers', 'shopper', 'watchers', 'universe', 'total',
    'tu', 'the', 'of', 'and', 'a', 'an', 'profile', 'consumers',
    'consumer', 'customers', 'customer', 'avid', 'casual', 'movie',
    'show', 'subscribers', 'members', 'users', 'potential',
    'prospective', 'purchasers', 'purchaser',
    # 2026-09-24 (PA-09 vs Florida Gubernatorial): audience nouns that
    # let unrelated electorates / cohorts partial-match on the noun
    # alone carry no identity.
    'voters', 'voter', 'listeners', 'listener', 'readers', 'reader',
}


# <<block: 2>>
# BASE CLARIFY (2026-09-24 Jenna, verbatim: 'so that this kind of
# thing doesnt happen if someone makes a request can prometheus say
# "did you want this on X (whatever is open on the screen) or
# something else?"'). The rdesocio asks: six Emily in Paris messages
# sent while the Landman profile was open, answered against Landman.
# When an ask NAMES a subject in subject position (a profile / journey
# / data / audience phrase) that shares no distinctive token with the
# open page, Prometheus asks before answering. The chips ride the
# existing memory-confirm machinery: the widget re-sends the original
# ask with bind_subject, which lands a measured read on the picked
# subject or the build-first offer when that subject has no base yet.
_PM_CLARIFY_STOP_TOKENS = _PM_BASE_GENERIC_TOKENS | {
    'this', 'that', 'these', 'those', 'their', 'them', 'they', 'it',
    'my', 'our', 'your', 'here', 'there', 'iq', 'for', 'in', 'on',
    'to', 'with', 'about', 'at', 'from', 'by', 'data', 'numbers',
    'cut', 'cuts', 'file', 'view', 'page', 'one', 'same',
    # Interrogatives / auxiliaries / ask-verbs: lead-strip fodder so
    # 'how big is the yellowstone audience' resolves to Yellowstone,
    # not the whole question.
    'how', 'what', 'whats', 'who', 'whos', 'when', 'where', 'why',
    'which', 'is', 'are', 'was', 'were', 'am', 'be', 'been', 'do',
    'does', 'did', 'can', 'could', 'will', 'would', 'should', 'may',
    'might', 'have', 'has', 'had', 'give', 'get', 'gets', 'tell',
    'me', 'please', 'many', 'much', 'i', 'we', 'you', 'us', 'big',
    'bigger', 'biggest', 'lets', 'let', 'want', 'wants', 'need',
    'needs', 'see', 'look', 'looks', 'people', 'person', 'folks',
    'everyone', 'anyone', 'anybody',
    # Category / metric nouns that sit in subject position on page
    # questions ('report on their qsr numbers') but never name a
    # subject on their own.
    'qsr', 'retailers', 'categories', 'category', 'brands', 'brand',
    'spend', 'sales', 'revenue', 'churn', 'retention', 'engagement',
    'penetration', 'reach', 'index', 'indexes', 'metrics', 'kpis',
    'kpi', 'stats', 'breakdowns', 'summary', 'summaries', 'percent',
    'percentages', 'share', 'shares', 'split', 'splits', 'totals',
}


# <<block: 3>>
_PM_CLARIFY_NAME_RES = _ns_mod.NAME_RES


# <<block: 4>>
_PM_CLARIFY_COMPARE_RE = _ns_mod.COMPARE_CUE_RX


# <<block: 5>>
_PM_CLARIFY_METRIC_RE = _ns_mod.METRIC_RX


# <<block: 6>>
def _pm_ask_names_its_audiences(text):
    """True when the ask already names who it is about (two cuts, or a
    total universe named alongside another audience)."""
    return _ns_mod.ask_names_its_audiences(text)


# <<block: 7>>
_PM_FILE_ASK_RE = re.compile(
    r"\b(?:as|in|into|to)\s+(?:a\s+|an\s+)?(?:csv|spreadsheet|excel|xlsx?)\b"
    r"|\b(?:provide|give|send|export|download|output)\b[^.?!]{0,40}"
    r"\b(?:csv|spreadsheet|excel|xlsx?)\b"
    r"|\bcsv\s+(?:file|format|output|export)\b",
    re.I)


# <<block: 8>>
_PM_CLARIFY_TURN_RE = re.compile(
    r"(?:do|did) you want this on |which audience should i use", re.I)


# <<block: 9>>
_PM_CLARIFY_DECLINE_WORDS = frozenset((
    'no', 'nope', 'neither', 'something else', 'not that', 'none',
    'none of those', 'none of these', 'not those', 'not these',
    'not that one', 'no thanks', 'not on screen', 'not the screen',
    'not whats open', "not what's open", 'different audience',
    'a different one'))


# <<block: 10>>
# "answer this now: <question>" and its cousins (2026-10-02 S5, scott
# on 2026-09-30 typed it after two open-screen questions and got the
# question a third time). The prefix is a command: do not ask again.
_PM_ANSWER_NOW_RE = re.compile(
    r"^\W*(?:(?:just|please|ok|okay)[\s,]+)*"
    r"(?:answer (?:this|it|me|the question)(?: now| already| please)?"
    r"|stop asking(?: me)?(?: questions)?|don'?t ask(?: me)?(?: again)?"
    r"|no (?:more )?questions|skip the question|just tell me)"
    r"\s*[:,.!-]*\s*", re.I)


# <<block: 11>>
def _pm_answer_now_strip(text):
    """Return (clean_text, True) when the ask opens with an answer-now
    command, else (text, False). Only strips when a real question is
    left over."""
    t = str(text or '')
    m = _C._PM_ANSWER_NOW_RE.match(t)
    if not m:
        return t, False
    rest = t[m.end():].strip()
    if len(rest) < 8:
        return t, False
    return rest, True


# <<block: 12>>
def _pm_answer_now_active():
    try:
        from flask import g as _g
        return bool(getattr(_g, '_pm_answer_now', False))
    except Exception:
        return False


# <<block: 13>>
def _pm_clarify_declined(history, text):
    """True when the previous agent turn was the open-screen or
    which-audience question and this turn turns it down ("no",
    "neither", "something else"). The original ask then runs AWAY
    from the page, never back into the same question (2026-10-02 S5)."""
    tl = str(text or '').strip().lower().strip(' .!?')
    if tl not in _C._PM_CLARIFY_DECLINE_WORDS:
        return False
    turns = [h for h in (history or []) if isinstance(h, dict)]
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role in ('agent', 'assistant'):
            last_agent = str(turns[i].get('text')
                             or turns[i].get('content') or '')
            return bool(_C._PM_CLARIFY_TURN_RE.search(last_agent))
        if role == 'user':
            return False
    return False


# <<block: 14>>
def _pm_clarify_answer_merge(history, text):
    """When the previous agent turn asked which audience the ask is
    about, this turn is the answer. Merge it back into the question
    that triggered the clarify so the original ask is not lost.

    Casey Pearson, 2026-09-29: hours by genre and platform, two cuts,
    as a csv. Her answer to the which-audience prompt was routed as a
    brand new ask, the original question fell away, and the confirm
    fired again. Returns the merged question, or '' when this turn is
    not a clarify answer."""
    t = str(text or '').strip()
    if not t or len(t) > 240 or '?' in t:
        return ''
    turns = [h for h in (history or []) if isinstance(h, dict)]
    last_agent = ''
    idx = -1
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role in ('agent', 'assistant'):
            last_agent = str(turns[i].get('text')
                             or turns[i].get('content') or '')
            idx = i
            break
        if role == 'user':
            break
    if not last_agent or not _C._PM_CLARIFY_TURN_RE.search(last_agent):
        return ''
    orig = ''
    for i in range(idx - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role != 'user':
            continue
        cand = str(turns[i].get('text')
                   or turns[i].get('content') or '').strip()
        if len(cand) >= 25 and cand.lower() not in (
                'something else', 'yes', 'no'):
            orig = cand
            break
    if not orig or orig.strip().lower() == t.lower():
        return ''
    # A bare yes / no / something-else / open-on-screen answer (typos
    # included: scott, "open on sceren", 2026-09-29) re-runs the
    # original question clean - gluing "Audience: no" onto it would
    # read as an audience named no.
    tl = t.lower().strip(' .!?')
    if tl in ('yes', 'yep', 'yeah', 'correct', 'sure', 'no', 'nope',
              'neither', 'something else', 'not that', 'the screen',
              'on screen', 'whats open', "what's open",
              'use the screen') or tl.startswith('open on'):
        return orig
    return f"{orig}\n\nAudience: {t}"


__all__ = ['_PM_BASE_GENERIC_TOKENS', '_PM_CLARIFY_STOP_TOKENS', '_PM_CLARIFY_NAME_RES', '_PM_CLARIFY_COMPARE_RE', '_PM_CLARIFY_METRIC_RE', '_pm_ask_names_its_audiences', '_PM_FILE_ASK_RE', '_PM_CLARIFY_TURN_RE', '_PM_CLARIFY_DECLINE_WORDS', '_PM_ANSWER_NOW_RE', '_pm_answer_now_strip', '_pm_answer_now_active', '_pm_clarify_declined', '_pm_clarify_answer_merge']
