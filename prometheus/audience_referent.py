"""A bare "this audience" binds before any work starts (2026-10-26, Mark
Valdez at Bent Pixels).

Mark asked, with the TikTok profile open, "can you provide a list of 100
channels that are the best fit on youtube for an ad campaign related to
this audience?" The lane promised "reading Ad Campaign Related and
TikTok now": the words of the ask were read as its subject and the
audience the pronoun pointed at was never confirmed.

The rule: a bare referent ("this audience", "these viewers", "this
group", ...) in an ask that starts work binds to the audience on the
reader's screen, or failing that to the one named earlier in the thread,
and is confirmed in ONE line before the work starts. The confirmed name
rides into the job as its subject. Neither on screen nor in the thread:
ask which audience, with no chips to guess from.

Pure functions; no Flask, no S3. The chat read launch and the talent
list lane call ``gate`` / ``resolve``.
"""
from __future__ import annotations

import re

REFERENT_RX = re.compile(
    r"\b(?:this|these|that|those)\s+(?:same\s+)?(?:audience|viewers|group|cohort|fans|users|people|segment|crowd|profile|base)\b",
    re.I)
# "this base (New Heights Podcast Listeners, 2025-10-21 through ...)",
# "this same audience: New Heights Podcast Listeners" (Jessie 2026-11-04):
# the pronoun and the name in one breath is a named audience, not a bare
# referent. The name is the parenthetical or colon segment up to its
# first comma, semicolon, digit run or closing bracket.
INLINE_NAME_RX = re.compile(
    r"\b(?:this|these|that|those)\s+(?:same\s+)?(?:audience|viewers|group|cohort|fans|users|people|segment|crowd|profile|base)\b"
    r"\s*(?:\(|:|,\s*(?:i\.?e\.?|that is|namely)\s*)\s*(?P<name>[A-Za-z][A-Za-z0-9&'./+ -]{2,80}?)\s*(?=[,;)\]]|\s+\d|\s*$)",
    re.I)
# the lead a landed read opens with ("On New Heights Podcast Listeners:")
_READ_LEAD_RX = re.compile(r"^\s*On\s+([^:\n]{1,120}):", re.I)
# an ask that starts work: a list, a report, a build, a pull, a ranking
WORK_RX = re.compile(
    r"\b(?:list|lists|report|build|pull|run|rank|ranking|find|identify|provide|give me|show me|put together|"
    r"recommend|suggest|which (?:channels|creators|brands|shows)|top \d+|\d+ (?:channels|creators|brands|shows|influencers|podcasts))\b",
    re.I)
DIFFERENT_RX = re.compile(r"^\s*(?:a |the )?different audience\s*[.!]*\s*$", re.I)
GO_RX = re.compile(r"^\s*go(?: with (?P<name>.+?))?\s*[.!]*\s*$", re.I)

NEITHER_LINE = "Which audience? Name it or open its profile and I will run the list."


def _clean(s):
    return ' '.join(str(s or '').split()).strip()


def _fold(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def same_entity(a, b):
    fa, fb = _fold(a), _fold(b)
    return bool(fa) and bool(fb) and (fa == fb or fa.split(' - ')[0] == fb.split(' - ')[0])


def inline_name(text):
    """The audience named in the same breath as the pronoun ("this base
    (New Heights Podcast Listeners, 2025-10-21 through 2026-10-21 ...)"
    -> 'New Heights Podcast Listeners'), else ''. A window, a count or a
    stray word is not a name."""
    m = INLINE_NAME_RX.search(str(text or ''))
    if not m:
        return ''
    name = _clean(m.group('name')).strip(' .,;:-')
    if not name or not re.search(r"[A-Za-z]{3}", name) or re.fullmatch(r"[\d ,./-]+", name):
        return ''
    if len(name.split()) > 8 or _fold(name) in ('the', 'that', 'this', 'it', 'one', 'same'):
        return ''
    return name


def bare_referent(text):
    """The referent phrase ("this audience") when the ask points at an
    audience without naming it, else ''. A referent followed by its own
    name ("this base (New Heights Podcast Listeners, ...)") is named, not
    bare."""
    m = REFERENT_RX.search(str(text or ''))
    if not m:
        return ''
    if inline_name(text):
        return ''
    return _clean(m.group(0))


def starts_work(text):
    return bool(WORK_RX.search(str(text or '')))


def screen_audience(ctx):
    """The audience open on the reader's screen (page_context.primary.name)."""
    pc = ctx if isinstance(ctx, dict) else {}
    prim = pc.get('primary') if isinstance(pc.get('primary'), dict) else {}
    name = _clean(prim.get('name') or '')
    if not name and prim.get('s3_key'):
        name = re.sub(r"\.csv$", '', str(prim['s3_key'])).replace('_', ' ')
        name = re.sub(r"\s\d{2} \d{2} \d{4} \d{2} \d{2}$", '', name)
    return name


def thread_audience(thread, history=None):
    """The audience the thread follows: its binding first, then the last
    subject a landed read named in this chat."""
    tb = thread if isinstance(thread, dict) else {}
    name = _clean(tb.get('subject_label') or '')
    if name:
        return name
    for t in reversed(list(history or [])):
        meta = t.get('meta') if isinstance(t, dict) and isinstance(t.get('meta'), dict) else {}
        if _clean(meta.get('subject')):
            return _clean(meta.get('subject'))
    # A resumed read's history carries words only, no meta (the read
    # worker, 2026-11-04): the subject a landed read opened with ("On New
    # Heights Podcast Listeners:") is the audience this thread follows.
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        m = _READ_LEAD_RX.match(str(t.get('text') or t.get('content') or ''))
        if m:
            lead = _clean(m.group(1)).split(' - ')[0].strip()
            if lead and len(lead) <= 100:
                return lead
    return ''


def resolve(text, ctx=None, thread=None, history=None):
    """(name, source) for a bare referent: ('TikTok', 'screen'),
    ('TikTok', 'thread') or ('', '')."""
    if not bare_referent(text):
        return '', ''
    name = screen_audience(ctx)
    if name:
        return name, 'screen'
    name = thread_audience(thread, history)
    if name:
        return name, 'thread'
    return '', ''


def confirm_payload(text, name, source, referent='this audience'):
    """The one-line confirm with its chips, as the chat payload. The
    'Go with <name>' chip carries the subject so the widget re-sends the
    ask bound to it; 'A different audience' comes back as words."""
    if not name:
        return {'success': True, 'action': 'answer', 'reply': NEITHER_LINE, 'followups': [],
                'offer_deck': False, 'deck_angle': None, 'outcome': 'audience_unbound'}
    if source == 'thread':
        reply = f"I read '{referent}' as {name} from earlier in this chat. Go, or name another?"
    else:
        reply = f"Using the {name} audience open on your screen. Say go, or name a different audience."
    opts = [{'label': f'Go with {name}', 'subject': name}]
    return {'success': True, 'action': 'answer', 'reply': reply,
            'followups': [f'Go with {name}', 'A different audience'],
            'offer_deck': False, 'deck_angle': None,
            'memory_confirm': {'question': text, 'options': opts},
            'audience_referent': {'name': name, 'source': source},
            'subject': name, 'outcome': f'audience_confirm_{source}'}


def gate(text, bind_subject='', *, ctx=None, thread=None, history=None, confirmed='', pinned=''):
    """None when the work may start; else the payload to return instead.

    Work starts when the ask carries no bare referent, or does not start
    work, or the referent was confirmed: the reader tapped the chip (the
    confirmed subject arrives as ``confirmed`` / ``bind_subject`` and
    matches the resolved audience). A ``bind_subject`` parsed out of the
    words ("Ad Campaign Related") is not a confirmation.

    ``pinned`` is the base a resumed read was started on (the read
    worker, a recovery): that read already passed this gate on the web
    process, with the screen it had then. It never asks again (Jessie
    2026-11-04: the worker had no screen and no thread meta, asked
    "Which audience?" into a job record, and the read landed as "the
    data it was started from was not available")."""
    t = str(text or '')
    if DIFFERENT_RX.match(t):
        return dict(confirm_payload(t, '', ''), outcome='audience_different')
    ref = bare_referent(t)
    if not ref or not starts_work(t):
        return None
    if _clean(pinned):
        return None    # a resumed read runs on the base it was started on
    name, source = resolve(t, ctx=ctx, thread=thread, history=history)
    for c in (confirmed, bind_subject):
        if c and name and same_entity(c, name):
            return None
    if confirmed and not name:
        return None    # the reader named it on the chip; nothing on screen to disagree with
    last = ''
    for turn in reversed(list(history or [])):
        if isinstance(turn, dict) and str(turn.get('role') or '') != 'user':
            last = str(turn.get('text') or turn.get('content') or ''); break
    if name and (last.startswith(f'Using the {name} audience') or last.startswith(f"I read '{ref}' as {name}")):
        return None    # asked once in this thread; the next ask runs on it
    return confirm_payload(t, name, source, referent=ref)


def job_audience(text, bind_subject='', *, ctx=None, thread=None, history=None):
    """The audience a queued job carries: the audience named in the same
    breath as the pronoun, else the confirmed subject when the ask
    pointed at one, else the resolved screen / thread audience, else ''."""
    named = inline_name(text)
    if named and not bind_subject:
        return named
    if bare_referent(text):
        name, _src = resolve(text, ctx=ctx, thread=thread, history=history)
        if bind_subject and (not name or same_entity(bind_subject, name)):
            return _clean(bind_subject)
        return name
    return _clean(bind_subject)
