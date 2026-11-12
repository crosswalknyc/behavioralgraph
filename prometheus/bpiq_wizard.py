"""Brand Partnership Valuation as a Prometheus wizard (2026-10-28).

Chip "Value a brand partnership" walks one question at a time: talent,
campaign, dates, standard attribution window, control group, assets,
then charge. The existing valuation job stays; this module is intake,
pricing, persist, and the accept/decline door.

User-facing copy is Prometheus first person. No em dashes. No synth,
modeled, pipeline, or model names.
"""
from __future__ import annotations

import os
import re
import threading
import traceback
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from . import bpiq_slots as _slots   # what a sentence names: talent, brand, start (2026-11-11)

FLOW = 'brand_partnership'
WIZARD_FLOW = 'brand_partnership_wizard'
DRAFT_TTL_S = 24 * 3600      # a draft waits a day, then the thread is free (matches pending_intake.TTL_S)
CHIP = 'Value a brand partnership'
CHIP_ALIASES = (
    CHIP,
    'pull brand partnership valuation',
    'value a brand partnership',
    'run a brand partnership valuation',
    'brand partnership valuation',
    'value this partnership',
    'pull a brand partnership valuation',
    'run brand partnership valuation',
    'bpiq',
)
_START_EXACT = frozenset(
    re.sub(r'[^a-z0-9]+', ' ', c.lower()).strip() for c in CHIP_ALIASES
) | frozenset((
    'value brand partnership',
    'run bpiq',
    'pull bpiq',
    'start bpiq',
    'start a brand partnership valuation',
    'open a brand partnership valuation',
))
PRICE_BASE = 500
PRICE_CONTROL = 1000
CREDITS_BASE = 8
CREDITS_CONTROL = 15
PULL_BASE = 'Brand Partnership IQ Base'
PULL_CONTROL = 'Brand Partnership IQ'
TOOL_BASE = 'brand_partnership_iq_base'
TOOL_CONTROL = 'brand_partnership_iq'

STEP_TALENT = 'talent'
STEP_CAMPAIGN = 'campaign'
STEP_DATES = 'dates'
STEP_ATTR = 'attr'
STEP_ATTR_CUSTOM = 'attr_custom'
STEP_CONTROL = 'control'
STEP_ASSETS = 'assets'
STEP_LINKS = 'links'
STEP_CHARGE = 'charge'

ASK_TALENT = 'Who is the property or talent?'
ASK_CAMPAIGN = 'What campaign should I value?'
ASK_DATES = 'What are the campaign dates?'
ASK_ATTR_CUSTOM = 'What attribution window should I use?'
ASK_CONTROL = 'Include a control group?'
ASK_ASSETS = ('Do you have specific campaign links to include, or should '
              'I find every applicable campaign asset?')
ASK_LINKS = 'Paste the campaign links. One or many is fine.'
# A step is never asked in the same words twice in a row (2026-11-11,
# Samantha Kolton at CAA: "find the campaign asset" drew the assets
# question a second time, word for word). The first reply that does not
# answer a step gets the step again in other words; the second moves on
# with the step's default, said in one plain line, when it has one.
ASK_WHICH_ALT = 'I need to know which side is the talent or property. Is it {a} or {b}?'
PINNED_NOTE = (', the same window as the partnership figure you already have, so the valuation and that '
               'figure cover the same days')
ASK_TALENT_ALT = 'I need the name of the talent or property in the partnership. Who is it?'
ASK_CAMPAIGN_ALT = 'Which campaign or brand is the partnership with? A name like Gap x JYT works.'
ASK_DATES_ALT = ("When did the campaign run? Give me a start and an end date, or a start date "
                 "and 'to today' if it is still running.")
ASK_DATES_HINT = ('You said it started in {hint}. What day did it start? If it is still '
                  'running, I will run it through today.')
ASK_DATES_HINT_ALT = ('What day in {hint} did it start? A date like {example} works. If you are '
                      'not sure, I can run it from {first} through today.')
ASK_DATES_UNREAD = 'I could not read those dates. Try something like October 7 - 28.'
ASK_CONTROL_ALT = 'Should I include a control group? Yes or No.'
ASK_ASSETS_ALT = ("If you have campaign links, paste them. If not, say 'find them' and I will "
                  "look for every campaign asset myself.")
ASK_LINKS_ALT = ("I did not catch a link. Paste one or more, or say 'find them' and I will "
                 "look for the campaign assets myself.")
ATTR_NUDGE = ' Accept this window, or use a different one.'
ATTR_INFERRED_NOTE = ' If the campaign has ended, tell me the end date.'
CHARGE_NUDGE = 'Say Accept to run it, or Decline to stop.'
DEFAULT_FIND_LINE = "I'll find the campaign assets myself."
DEFAULT_FIND_NO_LINK_LINE = "I did not catch a link, so I'll find the campaign assets myself."
DECLINE_REPLY = "Okay, I won't run it."
CANCEL_REPLY = 'No problem. Ask me anything else when ready.'
RUNNING_REPLY = ("It's running. Keep talking here as usual. I'll email "
                 "you when it is done.")
ALREADY_RUNNING_REPLY = ("That valuation is already running, so I have not "
                         "started a second one. I'll email you when it is done.")
EXISTS_REPLY = ("That valuation already exists: {name} is in the Brand "
                "Partnership tab. Nothing was charged.")
CHARGE_FAILED_REPLY = ("I could not take payment for that valuation just now, so nothing has started and nothing "
                       "was charged. Say Accept to try again.")
START_FAILED_REPLY = ("That valuation did not start, so the charge was reversed and nothing was billed. "
                      "Say Accept to try again.")
START_FAILED_HELD_REPLY = ("That valuation did not start. I have flagged the charge to be reversed, and nothing "
                           "more will be billed. Say Accept to try again.")
FUNDS_REPLY = ('That valuation needs funds on the account first. Add '
               'funds or ask your admin, and I will run it the moment '
               'you are set.')

CHIP_ACCEPT = 'Accept'
CHIP_DIFFERENT = 'Use a different window'
CHIP_YES = 'Yes'
CHIP_NO = 'No'
CHIP_PASTE = "I'll paste links"
CHIP_FIND = 'Find the assets'
CHIP_DECLINE = 'Decline'
CHIP_CANCEL = 'Cancel'
# The way back after a question typed mid-wizard was answered on its own
# (Jenna 2026-11-05). The draft stays at its step; this chip, or 'back
# to it', re-asks it.
RESUME_CHIP = 'Back to the valuation'

_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
_MON = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
_URL_RE = re.compile(r'https?://[^\s,;<>\"]+', re.I)
_ISO_RANGE = re.compile(
    r'(?P<s>\d{4}-\d{2}-\d{2})\s*(?:-|to|through|thru|until|till)\s*(?P<e>\d{4}-\d{2}-\d{2})',
    re.I)
_MDY_RANGE = re.compile(
    r'(?P<m1>\d{1,2})/(?P<d1>\d{1,2})(?:/(?P<y1>\d{2,4}))?'
    r'\s*(?:-|to|through|thru)\s*'
    r'(?P<m2>\d{1,2})/(?P<d2>\d{1,2})(?:/(?P<y2>\d{2,4}))?',
    re.I)
_NAMED_RANGE = re.compile(
    r'(?P<m1>' + _MON + r')\.?\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?'
    r'(?:[,\s]+(?P<y1>(?:19|20)\d{2}))?'
    r'\s*(?:-|to|through|thru|until|till)\s*'
    r'(?:(?P<m2>' + _MON + r')\.?\s+)?'
    r'(?P<d2>\d{1,2})(?:st|nd|rd|th)?'
    r'(?:[,\s]+(?P<y2>(?:19|20)\d{2}))?',
    re.I)
_OPEN_RANGE = re.compile(
    r'^\W*(?:from\s+|since\s+)?(?P<s>.{3,40}?)\s*(?:-|to|through|thru|until|till|til)\s*'
    r'(?:today|now|date|present|the\s+present|current)\W*$', re.I)
_CANCEL_RE = re.compile(
    r'^\s*(never ?mind|not now|cancel( (that|it))?)\s*[.!?]?\s*$', re.I)
_DECLINE_RE = re.compile(r'^\s*decline\s*[.!?]?\s*$', re.I)
_YES_RE = re.compile(r'^\s*(yes|yeah|yep|y|sure|ok|okay)\s*[.!?]?\s*$', re.I)
_NO_RE = re.compile(r'^\s*(no|nope|nah|n)\s*[.!?]?\s*$', re.I)
# the control question answered in a few more words ("yes please",
# "no thanks", "without one", "include it")
_CONTROL_YES_RE = re.compile(
    r"^\W*(?:(?:yes|yeah|yep|yup|sure|ok|okay)\b(?!\W+(?:no|not|without)\b)|include\b|add\b|with\s+(?:a\s+|the\s+)?control\b|"
    r"please\s+(?:do|include|add)\b)", re.I)
_CONTROL_NO_RE = re.compile(
    r"^\W*(?:(?:no|nope|nah)\b|without\b|skip\b|none\b|not\s+(?:now|needed|this\s+time)\b|"
    r"(?:do\s+not|don'?t|dont)\s+(?:include|add|need)\b|no\s+control\b)", re.I)
_ACCEPT_RE = re.compile(
    r'^\s*(?:yes[,.!]?\s+)?(?:please\s+)?accept(?:\s+\$?\s*[\d,]+)?\s*[.!?]?\s*$',
    re.I)
_PASTE_RE = re.compile(
    r"i(?:'|’)?ll paste|paste (?:the )?links|i have (?:the )?links|specific (?:campaign )?links",
    re.I)
_FIND_RE = re.compile(
    r'find (?:the |every |all )?assets|you find|find them', re.I)
_BANNED_USER = (
    'synth', 'synthesize', 'synthesized', 'synthetic', 'modeled',
    'estimated', 'pipeline', 'hostmap', 'claude', 's3',
)
_EM = ('\u2014', '\u2013')


def _low(t):
    return ' '.join(str(t or '').lower().split())


def _today(today=None):
    if isinstance(today, date):
        return today
    if today:
        return date.fromisoformat(str(today)[:10])
    return date.today()


def _d(v):
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return date.fromisoformat(str(v)[:10])


def _year(s, default):
    if not s:
        return default
    y = int(s)
    if y < 100:
        y += 2000
    return y


def pretty_date(v):
    d = _d(v)
    return d.strftime('%B %-d, %Y').replace(' 0', ' ')


def pretty_range(start, end):
    a, b = _d(start), _d(end)
    if a == b:
        return pretty_date(a)
    if a.year == b.year and a.month == b.month:
        return f"{a.strftime('%B %-d').replace(' 0', ' ')} to {pretty_date(b)}"
    if a.year == b.year:
        return (f"{a.strftime('%B %-d').replace(' 0', ' ')} to "
                f"{pretty_date(b)}")
    return f"{pretty_date(a)} to {pretty_date(b)}"


def parse_dates(text, today=None):
    """(start, end) as ISO dates, or (None, None). Year defaults to
    today's year when omitted."""
    today = _today(today)
    t = str(text or '').strip()
    if not t:
        return None, None
    m = _ISO_RANGE.search(t)
    if m:
        return m.group('s'), m.group('e')
    m = _NAMED_RANGE.search(t)
    if m:
        m1 = _MONTHS[m.group('m1').lower().rstrip('.')]
        m2 = _MONTHS[m.group('m2').lower().rstrip('.')] if m.group('m2') else m1
        y2 = _year(m.group('y2'), today.year)
        y1 = _year(m.group('y1'), y2)
        try:
            a = date(y1, m1, int(m.group('d1')))
            b = date(y2, m2, int(m.group('d2')))
        except ValueError:
            return None, None
        if b < a:
            a, b = b, a
        return a.isoformat(), b.isoformat()
    m = _MDY_RANGE.search(t)
    if m:
        y2 = _year(m.group('y2'), today.year)
        y1 = _year(m.group('y1'), y2)
        try:
            a = date(y1, int(m.group('m1')), int(m.group('d1')))
            b = date(y2, int(m.group('m2')), int(m.group('d2')))
        except ValueError:
            return None, None
        if b < a:
            a, b = b, a
        return a.isoformat(), b.isoformat()
    # a start date and "to today" ("9/22/26 to today", "September 22
    # through now"): the end is the wizard's own today (2026-11-11)
    m = _OPEN_RANGE.match(t)
    if m:
        try:
            one = _slots.single_date(m.group('s'), today)
        except Exception:
            one = None
        if one and _d(one) <= today:
            return one, today.isoformat()
    try:
        from prometheus.intake_reader import find_windows
        win = find_windows(t) or {}
        if win.get('event_start') and win.get('event_end'):
            return win['event_start'], win['event_end']
    except Exception:
        pass
    return None, None


class _Turn(threading.local):
    """Who is asking and the thread so far, for the turn in hand: what
    the pairing lookups (which side is the talent, the figure the reader
    already holds) read without every step carrying them."""
    user = ''
    history = None


_TURN = _Turn()


def _set_turn(user, history):
    """Whose turn this is, set at every way into the wizard so a lookup
    never reads another reader's thread; returns what was there before
    (for the caller to put back)."""
    was = (_TURN.user, _TURN.history)
    _TURN.user = _uname(user) if not isinstance(user, str) else (user or '')
    _TURN.history = history
    return was


def _restore_turn(was):
    try:
        _TURN.user, _TURN.history = was
    except Exception:
        _TURN.user, _TURN.history = '', None


def mark_window(draft, text, inferred=False):
    """Note whether the campaign window the reader gave is open-ended
    ("to today", "so far", an end read as today) or ends on a date they
    named. An open-ended order on a pairing the reader already holds a
    figure for runs on that figure's window (2026-11-11); only an end
    date named in words makes a longer window a new read."""
    if not isinstance(draft, dict):
        return draft
    try:
        is_open = bool(inferred) or bool(_slots.says_ongoing(str(text or '')))
    except Exception:
        is_open = bool(inferred)
    draft['window_open'] = is_open
    draft['end_named'] = not is_open
    for k in ('pinned_read', 'given_read', 'read_checked'):
        draft.pop(k, None)
    return draft


def pin_to_read(draft, user, history=None):
    """The draft with its window pinned to the figure the reader's
    company already holds on this pairing and start date, when the
    window the reader gave is open-ended; and, either way, the note that
    the reader holds a figure for this pairing and window (the valuation
    then lands on it). One lookup per window. Never raises."""
    d = draft
    try:
        if not isinstance(d, dict) or d.get('read_checked') or not (
                d.get('talent') and d.get('brand_partner') and d.get('campaign_start') and d.get('campaign_end')):
            return d
        if d.get('charged') or d.get('job_id'):
            return d
        from migration import bpiq_anchors as _ba
        history = history if history is not None else _TURN.history
        user = user or _TURN.user
        d = dict(d)
        d['read_checked'] = True
        anchor = _ba.read_on_pair(d['talent'], d['brand_partner'])
        if not anchor or not anchor.get('value_usd'):
            return d
        w = anchor.get('window') or {}
        if str(w.get('start') or '') != str(d['campaign_start'])[:10]:
            return d
        if not _ba.given_to(anchor, _uname(user), history=history):
            return d
        note = {'key': anchor.get('key'), 'value_usd': anchor.get('value_usd'), 'start': w.get('start'), 'end': w.get('end')}
        if d.get('window_open') and str(w.get('end') or '') != str(d['campaign_end'])[:10]:
            d['campaign_end'] = str(w['end'])
            d['attr_start'], d['attr_end'] = str(w['start']), str(w['end'])
            # a window the reader already accepted stays accepted (the
            # recap states the dates it runs on); one not yet accepted is
            # shown as the window step
            d['attr_ok'] = bool(d.get('attr_ok'))
            d['pinned_read'] = note
            d['window_open'] = False
            d.pop('dates_inferred', None)
            d['step'] = next_step(d)
        if str(w.get('end') or '') == str(d['campaign_end'])[:10]:
            d['given_read'] = note
            # the campaign window is the held figure's own (the read's dates, seeded from the thread
            # or named by the reader): the valuation covers the same days, so the attribution window
            # is that window too, not thirty days past it (2026-11-11: a deck asked of the read opened
            # the order on Sep 22 to Nov 11 with attribution "through today", a day longer every day).
            # A window the reader already accepted, or typed, stays theirs.
            if not d.get('attr_ok') and (str(d.get('attr_start') or '')[:10], str(d.get('attr_end') or '')[:10]) \
                    != (str(w['start']), str(w['end'])):
                d['attr_start'], d['attr_end'] = str(w['start']), str(w['end'])
                d['pinned_read'] = note
                d.pop('dates_inferred', None)
        return d
    except Exception:
        traceback.print_exc()
        return draft


def attribution_window(campaign_start, campaign_end, today=None):
    """Standard window: campaign start through campaign end plus 30
    days, clipped to today when plus-30 would run past the last day
    we can see."""
    start, end, today = _d(campaign_start), _d(campaign_end), _today(today)
    plus30 = end + timedelta(days=30)
    if plus30 > today:
        return start.isoformat(), today.isoformat()
    return start.isoformat(), plus30.isoformat()


def price_usd(control_group):
    """The price the recap quotes: the seat's own rate for the valuation
    (the table the charge reads, so the quote and the charge are one
    number; `_analyze_hook` stamps it once per turn from the live seat),
    else the published rate."""
    base = PRICE_CONTROL if control_group else PRICE_BASE
    try:
        from flask import g as _g
        tab = getattr(_g, '_pm_bpiq_prices', None)
        if isinstance(tab, dict):
            v = float(tab.get(bool(control_group)) or 0)
            return (int(v) if abs(v - round(v)) < 0.009 else round(v, 2)) if v > 0 else base
    except Exception:
        pass
    return base


def _money_symbol():
    try:
        from flask import g as _g
        sym = getattr(_g, '_pm_bpiq_symbol', None)
        return sym if isinstance(sym, str) and sym.strip() and len(sym.strip()) <= 2 else '$'
    except Exception:
        return '$'


def price_label(usd):
    v = float(usd)
    return f"{_money_symbol()}{v:,.0f}" if abs(v - round(v)) < 0.009 else f"{_money_symbol()}{v:,.2f}"


def seat_prices(uname):
    """({False: base, True: with a control group}, symbol) from the
    seat's own price table (a company rate rides through), or (None,
    None) when the table cannot be read (a hermetic run, a thin host):
    the published rate stands."""
    try:
        import os
        if os.environ.get('REGRESSION_TEST_MODE') or not str(uname or '').strip():
            return None, None
        from prometheus.legacy.watch import _pm_usd, _pm_money_symbol
        tab = {False: float(_pm_usd(TOOL_BASE, float(PRICE_BASE), username=uname) or 0),
               True: float(_pm_usd(TOOL_CONTROL, float(PRICE_CONTROL), username=uname) or 0)}
        if tab[False] <= 0 or tab[True] <= 0:
            return None, None
        return tab, str(_pm_money_symbol(uname) or '$')
    except Exception:
        return None, None


def accept_chip(usd, included=False):
    """'Accept $500'; a bare 'Accept' for a seat whose plan includes the
    valuation (Jenna 2026-11-08: no price, no staff chip)."""
    return CHIP_ACCEPT if included else f"Accept {price_label(usd)}"


INCLUDED_LINE = "Included with your plan."


def included_seat(draft=None):
    """True when the recap and its chips carry no price: the draft is
    stamped for an unlimited seat (analyze_hook stamps it once per turn
    from the live seat), or the request's flag says so."""
    if isinstance(draft, dict) and draft.get('included'):
        return True
    try:
        from flask import g as _g
        return bool(getattr(_g, '_pm_bpiq_included', False))
    except Exception:
        return False


def extract_urls(text):
    out = []
    for m in _URL_RE.finditer(str(text or '')):
        u = m.group(0).rstrip('.,);]')
        try:
            p = urlparse(u)
        except Exception:
            continue
        if p.scheme in ('http', 'https') and p.netloc:
            out.append(u)
    return list(dict.fromkeys(out))


def is_chip(text):
    return _low(text) in {_low(c) for c in CHIP_ALIASES}


def is_start(text):
    """True when the words are a fresh Brand Partnership Valuation
    open (the chip, a typed equivalent, BPIQ, or a legacy one-shot
    phrasing). Punctuation and case do not matter. A longer question
    that only mentions a partnership is not a start."""
    f = re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()
    if not f:
        return False
    if f in _START_EXACT:
        return True
    for lead in ('please ', 'can you ', 'could you ', 'i want to ',
                 'i need to ', 'lets ', 'let us '):
        if f.startswith(lead) and f[len(lead):] in _START_EXACT:
            return True
    return False


# Last-agent prompts that mean the wizard is already open. A bare
# name like "Jennifer Beals" after any of these is the answer, never
# a new subject read.
_PROMPT_TO_STEP = (
    # a default taken on the reader's behalf is followed by the next
    # step's own prompt; that later prompt names the step, so the recap
    # and the window lead this table
    ("Here's what I'll run", STEP_CHARGE),
    ('I read the campaign as', STEP_ATTR),
    (ASK_TALENT, STEP_TALENT),
    ('Which one is the talent or property', STEP_TALENT),
    ('I need to know which side is the talent or property', STEP_TALENT),
    ('Who is the property or talent', STEP_TALENT),
    ('I need the name of the talent or property', STEP_TALENT),
    (ASK_CAMPAIGN, STEP_CAMPAIGN),
    ('What campaign should I value', STEP_CAMPAIGN),
    ('Which campaign or brand is the partnership with', STEP_CAMPAIGN),
    (ASK_DATES, STEP_DATES),
    ('What are the campaign dates', STEP_DATES),
    ('I could not read those dates', STEP_DATES),
    ('When did the campaign run', STEP_DATES),
    ('What day did it start', STEP_DATES),
    ('did it start? A date like', STEP_DATES),
    (ASK_ATTR_CUSTOM, STEP_ATTR_CUSTOM),
    ('What attribution window should I use', STEP_ATTR_CUSTOM),
    (ASK_CONTROL, STEP_CONTROL),
    ('Include a control group', STEP_CONTROL),
    ('Should I include a control group', STEP_CONTROL),
    (ASK_ASSETS, STEP_ASSETS),
    ('Do you have specific campaign links', STEP_ASSETS),
    ('If you have campaign links, paste them', STEP_ASSETS),
    ('I did not catch a link. Paste one or more', STEP_LINKS),
    (ASK_LINKS, STEP_LINKS),
    ('Paste the campaign links', STEP_LINKS),
    ("Here's what I'll run", STEP_CHARGE),
    ('Here is what I will run', STEP_CHARGE),
)


def _host_cap(host, *names):
    """Read a bound host capability without raising HostNotBound.

    The live host binds `bucket`, not `S3_BUCKET`. getattr on a missing
    name is a RuntimeError, so the usual getattr default never runs.
    """
    if host is None:
        return None
    checker = None
    try:
        checker = getattr(host, 'has', None)
    except Exception:
        checker = None
    for n in names:
        try:
            if callable(checker) and not checker(n):
                continue
            val = getattr(host, n)
            if val is not None:
                return val
        except Exception:
            continue
    return None


def last_agent_turn(history):
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict):
            continue
        if str(turn.get('role') or '') not in ('agent', 'assistant'):
            continue
        return turn
    return None


def step_from_prompt(text):
    t = str(text or '').strip()
    if not t:
        return None
    for head, step in _PROMPT_TO_STEP:
        if t == head or t.startswith(head) or head in t:
            return step
    return None


# The intake guard (prometheus.intake_guard, Jenna 2026-11-05): a step
# consumes a message only when it reads as an answer to that step. The
# kind each step waits on, and the step's own answer shapes that always
# count (an 'I'll paste links' phrasing at the assets step, an accept at
# the window or the recap).
_STEP_KIND = {
    STEP_TALENT: 'name', STEP_CAMPAIGN: 'short', STEP_DATES: 'dates',
    STEP_ATTR: 'choice', STEP_ATTR_CUSTOM: 'dates', STEP_CONTROL: 'yes_no',
    STEP_ASSETS: 'choice', STEP_LINKS: 'links', STEP_CHARGE: 'choice',
}
_STEP_ANSWER_PATTERNS = {
    STEP_ATTR: (_ACCEPT_RE, re.compile(r'\bdifferent\b', re.I), _YES_RE),
    # "find the campaign asset", "find it", "I don't have one", "not sure"
    # are the assets step's own answers (2026-11-11)
    STEP_ASSETS: (_PASTE_RE, _FIND_RE, _YES_RE, _NO_RE, _slots._FIND_RX, _slots._NO_LINKS_RX),
    STEP_LINKS: (_slots._FIND_RX, _slots._NO_LINKS_RX),
    STEP_CHARGE: (_ACCEPT_RE, _DECLINE_RE),
    STEP_CONTROL: (_YES_RE, _NO_RE, _CONTROL_YES_RE, _CONTROL_NO_RE),
}


def _bpiq_intent(text):
    """A fresh valuation ask in the words themselves ('can you value
    Gunna x Nike? campaign ran June through August'): the wizard's turn
    even when it reads as a question, it fills ahead."""
    try:
        from prometheus import intents as _intents
        return bool(_intents.bpiq_intent(text, CHIP))
    except Exception:
        return is_chip(text)


def pv_kind(text, history=None, pick='', body=None):
    """'value' when the words ask what a partnership is worth in
    dollars, 'deck' when they ask for a deck of a read in this thread
    that stated one, '' otherwise (prometheus.partnership_value, Jenna
    2026-11-11: that figure is the valuation's to state). The reader's
    own words are read ahead of an order rewritten onto a resolved
    subject ("turn this into a deck" becomes "Run a deck on <the open
    page>" before any lane sees it). Never raises."""
    try:
        from prometheus import partnership_value as _pv
        own = str((body or {}).get('_pm_order_words') or '').strip() if isinstance(body, dict) else ''
        if own and own != str(text or '').strip():
            hit = _pv.owns(own, history, pick=pick)
            if hit:
                return hit
        return _pv.owns(text, history, pick=pick) or ''
    except Exception:
        return ''


_ORDER_LEAD_RX = re.compile(
    r"^\W*(?:(?:hey|hi|ok|okay|so)[,\s]+)?(?:please\s+|can you\s+|could you\s+|would you\s+|can we\s+|"
    r"i want (?:you )?to\s+|i need (?:you )?to\s+|i'?d like (?:you )?to\s+|let'?s\s+|i want an?\s+|i need an?\s+)?"
    r"(?:please\s+)?(?:value|valuate|price|appraise|run|pull|do|start|build|create|make|queue|set\s+up|give\s+me|get\s+me|"
    r"(?:brand[- ]partnership\s+)?valuation\s+(?:on|of|for))\b", re.I)


def is_valuation_order(text):
    """An instruction to value a partnership ('value the Stanley x Target
    partnership', 'can you value Gunna x Nike?', 'run a brand partnership
    valuation for MMG x Lowe's'), as opposed to a question about what one
    is worth."""
    return bool(_ORDER_LEAD_RX.match(str(text or '').strip()))


def answers_step(text, draft, today=None):
    """The intake guard for the wizard: True when `text` reads as the
    answer to the draft's open step (its chips, its control words, its
    own answer shapes, a dates attempt at a dates step, a short name at
    a name step). A question or an order on something else is not."""
    try:
        from prometheus import intake_guard as _ig
    except Exception:
        return True
    d = dict(draft or empty_draft())
    step = d.get('step') or next_step(d)
    # A sentence that names the talent, the brand or when it started is
    # the answer whatever step is open, however long it runs (2026-11-11:
    # "JYT is the talent and GAP is the brand. the partnership started in
    # September 2026" read as a new question and a general read ran in
    # the wizard's place). A question is still a question.
    if names_slots(text, step, today=today):
        return True
    if _shaped_answer(text, d, today=today):
        return True
    # A statement typed while the order is open stays with the order
    # (Jenna 2026-11-11: a sentence that was not a question left the
    # order for a general read, and the order never reached its quote).
    # Only a question, or an order for something else, is let through;
    # the step is asked again in other words, never answered elsewhere.
    try:
        return not _ig.is_ask(text)
    except Exception:
        return True


def _shaped_answer(text, draft, today=None):
    """True when `text` has the shape of the open step's answer: a chip,
    a control word, the step's own phrasings, dates at a dates step, a
    short name at a name step."""
    try:
        from prometheus import intake_guard as _ig
    except Exception:
        return True
    d = dict(draft or empty_draft())
    step = d.get('step') or next_step(d)
    try:
        _reply, chips = step_prompt(d, today=today)
    except Exception:
        chips = [CHIP_CANCEL]
    return _ig.is_answer_to_step(text, _STEP_KIND.get(step, 'text'), chips=chips,
                                 answer_patterns=_STEP_ANSWER_PATTERNS.get(step))


def loose_statement(text, draft, today=None):
    """A statement the order keeps only because it is not a question: it
    names no slot and has no shape of an answer. It counts as a reply
    that did not answer the step (asked again in other words, then the
    step's default); nothing in it is stored as a name."""
    t = str(text or '').strip()
    if not t or extract_urls(t):
        return False
    d = dict(draft or empty_draft())
    step = d.get('step') or next_step(d)
    if names_slots(t, step, today=today) or _shaped_answer(t, d, today=today):
        return False
    if step in (STEP_DATES, STEP_ATTR, STEP_ATTR_CUSTOM) and parse_dates(t, today=today)[0]:
        return False
    return True


def names_slots(text, step=None, today=None):
    """True when the sentence names a slot by its role ("X is the talent",
    "the brand is Y", "the partnership between X and Y"), or, while a
    name or the dates are still open, says when the partnership started.
    Never a question."""
    t = str(text or '').strip()
    if not t or '?' in t:
        return False
    try:
        from prometheus import intake_guard as _ig
        if _ig.has_interrogative_opener(t) or _ig.asks_a_figure(t):
            return False
        if _ig.is_build_order(t) and not _bpiq_intent(t):
            return False      # an order for another product; a valuation order that names its slots is ours
    except Exception:
        pass
    try:
        got = _slots.parse(t, today=_today(today))
    except Exception:
        return False
    if got.get('roles'):
        return True
    return bool(got.get('start')) and step in (STEP_TALENT, STEP_CAMPAIGN, STEP_DATES)


def _stand_aside(draft):
    """Note for the service that the wizard let this message through as a
    new question: the reply carries the way back; the draft stays."""
    try:
        from prometheus import intake_guard as _ig
        _ig.mark_resume(FLOW, RESUME_CHIP, step=str((draft or {}).get('step') or ''))
    except Exception:
        pass


def _is_resume(text):
    try:
        from prometheus import intake_guard as _ig
        return bool(_ig.is_resume(text))
    except Exception:
        return False


def _same_thread(doc_thread_id, thread_id):
    """A stored draft belongs to this thread when either side is unknown
    or the ids agree (prometheus.intake_guard.same_thread)."""
    try:
        from prometheus import intake_guard as _ig
        return bool(_ig.same_thread(doc_thread_id, thread_id))
    except Exception:
        a, b = str(doc_thread_id or '').strip(), str(thread_id or '').strip()
        return not a or not b or a == b


def _body_draft(body, thread_id='', history=None):
    """The draft the widget echoed back, when it belongs to THIS thread.
    The widget keeps the draft in page state (and sessionStorage), so it
    follows the reader across threads (Jenna 2026-11-05: a valuation
    armed on one thread read her questions on another). A draft stamped
    with another thread's id is not this thread's. An echo counts only
    when this thread's own last agent turn asked a step (or carried the
    way back to one); the server's store and the thread are the truth,
    the echo fills in when the store missed. With no thread known (a
    direct call) the echo stands on its own."""
    body = body or {}
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    hit = body.get('bpiq_wizard') or extra.get('bpiq_wizard')
    if not (isinstance(hit, dict) and hit.get('step')):
        return None
    own = str(hit.get('thread_id') or '').strip()
    tid = str(thread_id or '').strip()
    if own and tid and own != tid:
        return None
    if tid and not last_agent_is_wizard(history):
        return None
    return dict(hit)


def _stamp(draft, thread_id=''):
    """The thread a draft belongs to rides on it, so the widget's echo
    can be told from another thread's."""
    if isinstance(draft, dict) and thread_id and not draft.get('thread_id'):
        draft['thread_id'] = str(thread_id)
    return draft


def open_draft(body, history, store, user, thread_id='', today=None):
    """The draft a mid-wizard turn would answer, or None when the wizard
    is not open on this thread: the body's own draft (this thread's),
    the store's (this thread's), the last agent turn's, a replay of the
    thread, else an empty draft at the step the last agent turn asked.
    A collect flag on the body alone never arms a thread (it is the
    widget's page state and follows the reader). One store read."""
    d = _load_draft(store, user, thread_id or '', body, history, today=today)
    if d:
        return d
    if not last_agent_is_wizard(history):
        return None
    d = empty_draft()
    d['step'] = step_from_prompt(str((last_agent_turn(history) or {}).get('text') or '')) or STEP_TALENT
    return d


def last_agent_is_wizard(history):
    turn = last_agent_turn(history)
    if not turn:
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    if meta.get('bpiq_wizard') or turn.get('bpiq_wizard'):
        return True
    txt = str(turn.get('text') or turn.get('content') or '').strip()
    if step_from_prompt(txt) is not None:
        return True
    low = txt.lower()
    if txt.startswith("Here's what I'll run") or txt.startswith("It's running"):
        return True
    if 'needs funds on the account first' in low:
        return True
    # a reply that answered a question typed mid-wizard carries the way
    # back; the step behind it is still open (2026-11-05)
    try:
        want = RESUME_CHIP.lower()
        for o in (meta.get('options') or turn.get('options') or turn.get('followups') or []):
            lbl = (o.get('send') or o.get('label')) if isinstance(o, dict) else o
            if str(lbl or '').strip().lower() == want:
                return True
    except Exception:
        pass
    return False


def replay_history(history, today=None):
    """Rebuild the draft from the thread when the shared store misses.

    Each agent prompt names the step; the user turn after it is the
    answer. A start phrase resets. Cancel or Decline ends the walk.
    """
    today = _today(today)
    d = empty_draft()
    d['step'] = STEP_TALENT
    pending = None
    saw = False
    for turn in list(history or []):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get('role') or '')
        txt = str(turn.get('text') or turn.get('content') or '').strip()
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        wiz = meta.get('bpiq_wizard') or turn.get('bpiq_wizard')
        if isinstance(wiz, dict) and wiz.get('step'):
            d = dict(wiz)
            saw = True
            pending = None
            continue
        if role in ('agent', 'assistant'):
            pending = step_from_prompt(txt)
            if pending:
                saw = True
            continue
        if role != 'user' or not txt:
            continue
        if is_start(txt):
            d = empty_draft()
            d['step'] = STEP_TALENT
            pending = None
            saw = True
            continue
        if is_cancel(txt) or is_decline(txt):
            return None
        if pending:
            d['step'] = pending
            if not answers_step(txt, d, today=today):
                # a question typed while the step waited (2026-11-05): it
                # was answered on its own; the step stays open
                saw = True
                continue
            d2, err = apply_answer(d, txt, today=today)
            if not err:
                d2.pop('_named', None)
                d = d2
            pending = None
            saw = True
    if pending:
        d['step'] = pending
        saw = True
    return _price_draft(d) if saw else None


def wants(text, body=None, history=None, store=None, user=None,
          thread_id=''):
    """True when this turn belongs to the wizard: a start phrase, a
    confirm payload, or an open draft (an armed body, the shared store,
    the last agent turn asked a step) AND the message reads as the
    answer to the open step (prometheus.intake_guard, Jenna 2026-11-05).
    A question typed while a step waits is not the wizard's: this
    returns False, the draft stays where it is, and the reply that
    answers the question carries the 'Back to the valuation' chip."""
    if is_start(text):
        return True
    was = _set_turn(user, history)
    try:
        return _wants(text, body, history, store, user, thread_id)
    finally:
        _restore_turn(was)


def _wants(text, body, history, store, user, thread_id):
    body = body or {}
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    if body.get('bpiq_confirm') or extra.get('bpiq_confirm'):
        return True
    # what a partnership is worth in dollars, asked cold or about this
    # thread's partnership, and a deck of a read that stated it, are the
    # valuation's (Jenna 2026-11-11)
    if pv_kind(text, history, body=body):
        return True
    if store is None and user:
        try:
            store = store_for(body)
        except Exception:
            store = None
    draft = open_draft(body, history, store, user, thread_id)
    if draft is None:
        return False
    t = str(text or '').strip()
    if _is_resume(t) or is_cancel(t) or is_decline(t) or is_chip(t) or _bpiq_intent(t):
        return True
    if answers_step(t, draft):
        return True
    _stand_aside(draft)
    return False


def _start_fresh(store, user, thread_id, today):
    """Cancel any leftover draft and open step 1. Never raises."""
    try:
        if store is not None:
            store.clear(user, thread_id)
    except Exception:
        pass
    draft = empty_draft()
    draft['step'] = STEP_TALENT
    try:
        if store is not None:
            store.save(user, thread_id, draft)
    except Exception:
        pass
    reply, chips = step_prompt(draft, today=today)
    return _payload(reply, chips, draft=draft)


def is_cancel(text):
    return bool(_CANCEL_RE.match(str(text or '')))


def _drop_everything(user, thread_id):
    """Cancel means gone (Jenna 2026-11-05). A cancel that reaches the
    wizard through any door other than the global intake_cancel lane
    (the widget's own Cancel chip echoing a draft, a probe turn, a
    direct hook call) drops the reader's other armed intakes and the
    thread's open priced offers too, through the one module that knows
    every store (prometheus.intake_reset). The wizard's own draft was
    cleared by the caller. Never raises into the turn."""
    try:
        from . import intake_reset as _ir
        _ir.drop_all(user, thread_id, close_offers=True)
    except Exception:
        traceback.print_exc()


def is_decline(text):
    return bool(_DECLINE_RE.match(str(text or ''))) or _low(text) == 'decline'


def empty_draft():
    return {
        'step': STEP_TALENT,
        'talent': '',
        'campaign': '',
        'campaign_start': '',
        'campaign_end': '',
        'attr_start': '',
        'attr_end': '',
        'attr_ok': False,
        'control_group': None,
        'asset_mode': '',
        'links': [],
        'price': 0,
        'brand_partner': '',
    }


def _brand_from_campaign(campaign, talent=''):
    """The brand the engine values. A 'X x Y' campaign uses the side
    that is not the talent, else the last name, else the campaign."""
    name = str(campaign or '').strip()
    if not name:
        return ''
    parts = re.split(r'\s+[xX\u00d7]\s+', name, maxsplit=1)
    if len(parts) == 2:
        a, b = parts[0].strip(), parts[1].strip()
        t = re.sub(r'[^a-z0-9]+', '', str(talent).lower())
        if t and re.sub(r'[^a-z0-9]+', '', a.lower()) == t:
            return b or name
        if t and re.sub(r'[^a-z0-9]+', '', b.lower()) == t:
            return a or name
        return b or a or name
    return name


# The words around a one-shot valuation ask that are not a name: the
# verb lead ("can you value", "value the", "what is ... worth"), the
# tail noun ("partnership", "deal", "campaign"), and the connective
# residue left when the dates are stripped ("ran", "is", "was").
_ASK_LEAD_RX = re.compile(
    r"^\W*(?:(?:hey|hi|ok|okay|so)[,\s]+)?"
    r"(?:please\s+|can you\s+|could you\s+|would you\s+|can we\s+|i want (?:you )?to\s+|i need (?:you )?to\s+|"
    r"i'?d like (?:you )?to\s+|let'?s\s+|i want a\s+|i need a\s+)?"
    r"(?:(?:please\s+)?(?:value|valuate|valuation(?: of| on| for)?|price|appraise|assess|size up|"
    r"(?:run|pull|do|start)\s+(?:a\s+|the\s+)?(?:brand[- ]partnership\s+|bpiq\s+)?valuation\s+(?:on|of|for)|"
    r"what(?:'s| is| was) the value of|how much (?:is|was)|what(?:'s| is| was))\s+)"
    r"(?:the\s+|a\s+|this\s+|our\s+|my\s+)?", re.I)
_ASK_TAIL_RX = re.compile(
    r"\s*(?:(?:brand\s+)?partnership|deal|campaign|sponsorship|collab(?:oration)?|activation|program)?"
    r"\s*(?:worth|valued at)?\s*[?.!]*\s*$", re.I)
_FRAME_LEFTOVER_RX = re.compile(
    r"^(?:ran|run|runs|running|is|was|were|are|from|between|starting|started|ended|ending|"
    r"through|thru|to|until|till|in|on|during|for|the|a|an|and|with|it|that|this|which|"
    r"please|thanks|thank you|ok|okay)(?:\s+(?:ran|run|is|was|from|between|through|to|in|on|during|for|the|a|an|and|with|it|that|this))*$",
    re.I)


def _strip_ask_frame(rest):
    """Drop the verb lead and the tail noun of a one-shot ask so only
    the names remain: "can you value Gunna x Nike? ran" -> "Gunna x
    Nike"; "Value the Stanley x Target partnership. is" -> "Stanley x
    Target". A message with no lead is returned as given."""
    r = str(rest or '')
    # A blank line ends the ask; what follows is context ("MMG x Lowe's
    # \n\n MMG partnered with Lowe's during their ...", Jenna 2026-10-16,
    # "my ask was so explicit"). Without this cut the prose was glued
    # onto the brand partner.
    paras = [x for x in re.split(r'\n\s*\n', r) if x.strip()]
    if len(paras) > 1 and re.search(r'[A-Za-z]', paras[0]):
        r = paras[0]
    # A parenthetical after a name is an aside (a platform list:
    # "MMG (YouTube, IG, TikTok) x Lowe's"), never a second name.
    r = re.sub(r'[ \t]{2,}', ' ', re.sub(r'\s*\([^()]{0,80}\)', ' ', r))
    m = _ASK_LEAD_RX.match(r)
    if m and m.end() > 0:
        r = r[m.end():]
    # The first sentence carries the names. A second sentence that is
    # only connective residue ("ran", "is") after the dates were taken
    # out is dropped; a second sentence with words of its own stays.
    parts = re.split(r'(?<=[A-Za-z])[.?!]\s+', r, maxsplit=1)
    if len(parts) == 2:
        tail = parts[1].strip(' .,:;?!')
        if not tail or _FRAME_LEFTOVER_RX.match(tail):
            r = parts[0]
    r = _ASK_TAIL_RX.sub('', r)
    return r.strip()


# A casing table a test (or a caller that already holds one) can set;
# None means "ask the library" outside a hermetic run.
CASING_LOOKUP = None


def _casing_lookup():
    """folded name -> the library's spelling, or None. Never raises and
    never reaches the network in a hermetic run."""
    if callable(CASING_LOOKUP):
        return CASING_LOOKUP
    try:
        import os
        if os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('CORPUS_CATALOG', '1').strip() == '0':
            return None
        from migration.bpiq_synthesis import catalog_lookup
        return catalog_lookup()
    except Exception:
        return None


def _pair_name(talent, brand):
    return f"{str(talent).strip()} x {str(brand).strip()}"


def merge_slots(draft, text, today=None):
    """(draft, named): the draft with every slot the sentence names
    filled in, and what was named. A slot named by its role ("X is the
    talent", "the brand is Y") is the reader's own word and replaces
    what the draft held; a start date fills only empty dates. With the
    talent and the brand both known and no campaign named, the campaign
    is the pairing itself. `named` is empty when the sentence names
    nothing (2026-11-11)."""
    today = _today(today)
    d = dict(draft or empty_draft())
    try:
        got = _slots.parse(str(text or ''), today=today)
    except Exception:
        got = {}
    if not got:
        return d, {}
    named = {}
    table = {'lk': False}

    def _case(n):
        n = str(n or '').strip()
        if n and (n.isupper() or n.islower()):
            if table['lk'] is False:
                table['lk'] = _casing_lookup()
            if table['lk']:
                return _slots.known_casing(n, table['lk'])
        return n

    old_t, old_b = str(d.get('talent') or ''), str(d.get('brand_partner') or '')
    _f = lambda x: re.sub(r'[^a-z0-9]+', '', str(x or '').lower())      # noqa: E731
    if got.get('talent'):
        d['talent'] = _case(got['talent'])
        named['talent'] = d['talent']
    if got.get('brand'):
        d['brand_partner'] = _case(got['brand'])
        d['brand_named'] = True
        named['brand'] = d['brand_partner']
    if d.get('pair') and (named.get('talent') or named.get('brand')):
        if named.get('talent') and not named.get('brand'):
            mine, other = _pair_pick(d, named['talent'])
            if mine:
                d['brand_partner'], d['brand_named'] = other, True
                named['brand'] = other
        elif named.get('brand') and not named.get('talent'):
            mine, other = _pair_pick(d, named['brand'])
            if mine and not d.get('talent'):
                d['talent'] = other
                named['talent'] = other
        if d.get('talent'):
            d.pop('pair', None)
    # the pairing was read the wrong way round and the reader named one
    # side by its role ("Gunna is the talent" under Nike x Gunna): the
    # other side takes the other role
    if named.get('talent') and not named.get('brand') and old_t and _f(named['talent']) == _f(old_b) != _f(old_t):
        d['brand_partner'] = old_t
        named['brand'] = old_t
    elif named.get('brand') and not named.get('talent') and old_b and _f(named['brand']) == _f(old_t) != _f(old_b):
        d['talent'] = old_b
        named['talent'] = old_b
    if got.get('campaign'):
        d['campaign'] = str(got['campaign']).strip()
        d.pop('campaign_auto', None)
        named['campaign'] = d['campaign']
        if not d.get('brand_named'):
            d['brand_partner'] = _brand_from_campaign(d['campaign'], d.get('talent'))
    if (named.get('talent') or named.get('brand')) and d.get('talent') and d.get('brand_partner') \
            and d.get('brand_named') and (not d.get('campaign') or d.get('campaign_auto')):
        d['campaign'] = _pair_name(d['talent'], d['brand_partner'])
        d['campaign_auto'] = True
    if got.get('start') and not d.get('campaign_start'):
        if got.get('start_precise'):
            end = got.get('end') or today.isoformat()
            a, b = sorted([got['start'], end])
            d['campaign_start'], d['campaign_end'] = a, b
            d['attr_start'], d['attr_end'] = attribution_window(a, b, today=today)
            d['attr_ok'] = False
            if not got.get('end'):
                d['dates_inferred'] = True      # the end is read as today; the window step says so
            mark_window(d, text, inferred=not got.get('end'))
            d.pop('start_hint', None)
            d.pop('start_guess', None)
            named['start'] = a
        else:
            # only a month was given: the dates step asks for the day
            d['start_hint'] = got.get('start_label') or pretty_date(got['start'])
            d['start_guess'] = got['start']
            named['start_hint'] = d['start_hint']
    return d, named


def ack_line(named):
    """One plain line saying what a sentence was read as: "Got it: JYT
    is the talent and Gap is the brand." Empty when nothing was named."""
    bits = []
    if (named or {}).get('talent'):
        bits.append(f"{named['talent']} is the talent")
    if (named or {}).get('brand'):
        bits.append(f"{named['brand']} is the brand")
    if (named or {}).get('campaign'):
        bits.append(f"the campaign is {named['campaign']}")
    if not bits:
        return ''
    body = bits[0] if len(bits) == 1 else (', '.join(bits[:-1]) + ' and ' + bits[-1])
    return f"Got it: {body}."


def apply_ahead(draft, text, today=None):
    """Fill any fields the message already named. Never overwrites a
    field the wizard already has."""
    d = dict(draft or empty_draft())
    t = str(text or '').strip()
    if not t or is_chip(t):
        return d
    # a sentence that names its own slots is read by role, never split on
    # its commas and periods into a talent and a campaign (2026-11-11)
    if names_slots(t, d.get('step') or next_step(d), today=today):
        base = dict(d)
        merged, named = merge_slots(d, t, today=today)
        if named.get('talent') or named.get('brand') or named.get('campaign'):
            for k in ('talent', 'campaign', 'brand_partner'):
                if base.get(k):          # never over a field the wizard already has
                    merged[k] = base[k]
            for u in extract_urls(t):
                if u not in (merged.get('links') or []):
                    merged['links'] = list(merged.get('links') or []) + [u]
                    merged['asset_mode'] = merged.get('asset_mode') or 'links'
            return merged
        if named:
            # only the start was said ("Value the Stanley x Target
            # partnership. It launched October 1"): keep it, and read the
            # names from the rest of the words as usual
            d = merged
            t = _slots.strip_dates(t) or t
    urls = extract_urls(t)
    if urls and not d.get('links'):
        d['links'] = urls
        d['asset_mode'] = d.get('asset_mode') or 'links'
    start, end = parse_dates(t, today=today)
    if start and end and not d.get('campaign_start'):
        d['campaign_start'], d['campaign_end'] = start, end
        mark_window(d, t)
        if not d.get('attr_start'):
            d['attr_start'], d['attr_end'] = attribution_window(
                start, end, today=today)
    rest = t
    for u in urls:
        rest = rest.replace(u, ' ')
    rest = re.sub(
        r'(?i)\b(?:campaign(?: dates?)?|dates?|window)\b[:\s]*', ' ', rest)
    # drop a date span we already used
    rest = _NAMED_RANGE.sub(' ', rest)
    rest = _ISO_RANGE.sub(' ', rest)
    rest = _MDY_RANGE.sub(' ', rest)
    rest = _strip_ask_frame(rest)
    bits = [re.sub(r'^(?:the\s+)?(?:is|was|=|:)\s+', '', b.strip(' .,:;?!'), flags=re.I).strip()
            for b in re.split(r'\s*(?:,|\.\s|;|\?|!)\s*', rest) if b.strip(' .,:;?!')]
    bits = [b for b in bits if b]
    names = [b for b in bits if b and not parse_dates(b, today=today)[0]
             and not _FRAME_LEFTOVER_RX.match(b) and not _slots.is_filler(b)]
    if names and not d.get('talent'):
        # "Jennifer Beals, Ganni x Lululemon" or a lone name
        if len(names) == 1 and re.search(r'\s+[xX\u00d7]\s+', names[0]):
            # a campaign dump with no separate talent ("Gunna x Nike", "Gap
            # x JYT"). Which side is the talent is never read from the
            # order the pairing was written in (Jenna 2026-11-11: "Gap x
            # JYT" was answered "Gap is the talent"): what is known decides
            # (the thread, a read on the pairing, the library), and when
            # nothing does the wizard asks which one, once. The names the
            # message gave are kept either way (goldens g055, g056).
            if not d.get('campaign'):
                d['campaign'] = names[0]
                a, b = re.split(r'\s+[xX\u00d7]\s+', names[0], maxsplit=1)
                if a.strip() and b.strip():
                    got = orient_pair(a.strip(), b.strip())
                    if got:
                        d['talent'], d['brand_partner'] = got[0], got[1]
                        d['brand_named'] = True
                        d.pop('pair', None)
                    else:
                        d['pair'] = [a.strip(), b.strip()]
                else:
                    d['brand_partner'] = _brand_from_campaign(names[0], '')
        else:
            d['talent'] = names[0]
            extra = names[1:]
            if extra and not d.get('campaign'):
                d['campaign'] = extra[0]
                d['brand_partner'] = _brand_from_campaign(extra[0], d['talent'])
    elif names and not d.get('campaign'):
        d['campaign'] = names[0]
        d['brand_partner'] = _brand_from_campaign(names[0], d.get('talent'))
    if d.get('campaign') and not d.get('brand_partner'):
        d['brand_partner'] = _brand_from_campaign(d['campaign'], d.get('talent'))
    return d


def orient_pair(a, b, history=None):
    """(talent, brand) for a pairing from what is known, or None when
    nothing settles it (prometheus.partnership_roles). Never raises."""
    try:
        from prometheus import partnership_roles as _roles
        read = None
        try:
            from migration import bpiq_anchors as _ba
            read = _ba.read_on_pair(a, b)
        except Exception:
            read = None
        got = _roles.orient(a, b, history=history if history is not None else _TURN.history, read=read)
        return (got[0], got[1]) if got else None
    except Exception:
        return None


def _pair_pick(draft, name):
    """The side of the draft's open pairing `name` is, and the other
    side; (None, None) when the draft holds no open pairing or the name
    is neither."""
    pair = (draft or {}).get('pair')
    if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
        return None, None
    f = re.sub(r'[^a-z0-9]+', '', str(name or '').lower())
    a, b = str(pair[0]), str(pair[1])
    if f and f == re.sub(r'[^a-z0-9]+', '', a.lower()):
        return a, b
    if f and f == re.sub(r'[^a-z0-9]+', '', b.lower()):
        return b, a
    return None, None


def next_step(draft):
    d = draft or {}
    if not d.get('talent'):
        return STEP_TALENT
    if not d.get('campaign'):
        return STEP_CAMPAIGN
    if not (d.get('campaign_start') and d.get('campaign_end')):
        return STEP_DATES
    if not d.get('attr_ok'):
        return STEP_ATTR
    if d.get('control_group') is None:
        return STEP_CONTROL
    if not d.get('asset_mode'):
        return STEP_ASSETS
    if d.get('asset_mode') == 'links' and not d.get('links'):
        return STEP_LINKS
    return STEP_CHARGE


def _price_draft(d):
    if d.get('control_group') is None:
        d['price'] = 0
    else:
        d['price'] = price_usd(bool(d['control_group']))
    return d


def _cased(name):
    """A name typed in all lower case takes title case for the file and
    the recap; one that already carries capitals is kept. The runner
    applies the catalog's own spelling on top."""
    try:
        from migration.bpiq_synthesis import canonical_name
        return canonical_name(name)
    except Exception:
        s = ' '.join(str(name or '').split())
        return s if any(c.isupper() for c in s) else s.title()


def engine_inputs(draft):
    """The payload the existing valuation job already accepts."""
    d = dict(draft or {})
    talent = _cased(str(d.get('talent') or '').strip())
    campaign = _cased(str(d.get('campaign') or '').strip())
    brand = _cased(str(d.get('brand_partner') or '').strip()) or _brand_from_campaign(
        campaign, talent)
    start, end = d.get('campaign_start'), d.get('campaign_end')
    attr_s, attr_e = d.get('attr_start'), d.get('attr_end')
    control = bool(d.get('control_group'))
    out = {
        'qualifier': talent,
        'brand_partner': brand,
        'campaign': campaign,
        'event_start': start,
        'event_end': end,
        'post_start': attr_s,
        'post_end': attr_e,
        'control_group': control,
        'include_control': control,
        'asset_mode': d.get('asset_mode') or 'find',
        'links': list(d.get('links') or []),
        'price': price_usd(control),
    }
    # the window as the reader gave it: open-ended or an end date named in
    # words, and the figure they already hold for it (2026-11-11)
    if d.get('end_named'):
        out['end_named'] = True
    if d.get('window_open'):
        out['window_open'] = True
    for k in ('pinned_read', 'given_read'):
        if isinstance(d.get(k), dict):
            out[k] = dict(d[k])
    return out


def recap_lines(draft):
    d = draft or {}
    control = bool(d.get('control_group'))
    usd = price_usd(control)
    assets = ("I'll find the assets" if d.get('asset_mode') != 'links'
              else ('Links: ' + ', '.join(d.get('links') or [])))
    lines = [
        f"{_cased(d.get('talent'))}",
        f"{_cased(d.get('campaign'))}",
        f"Campaign dates: {pretty_range(d['campaign_start'], d['campaign_end'])}",
        f"Attribution window: {pretty_range(d['attr_start'], d['attr_end'])}",
        f"Control group: {'yes' if control else 'no'}",
        assets,
        (INCLUDED_LINE if included_seat(d) else f"This is {price_label(usd)}."),
    ]
    return lines


def recap_reply(draft):
    return "Here's what I'll run:\n" + '\n'.join(recap_lines(draft))


def attr_reply(draft, today=None):
    d = draft or {}
    camp = pretty_range(d['campaign_start'], d['campaign_end'])
    attr = pretty_range(d['attr_start'], d['attr_end'])
    end = _d(d['campaign_end'])
    today = _today(today)
    plus30 = end + timedelta(days=30)
    if plus30 > today:
        why = ('The standard window is 30 days after the campaign ends. '
               'That would run past today, so I am using the campaign '
               f'dates through {pretty_date(today)}.')
    else:
        why = ('The standard window is the campaign dates plus 30 days '
               'after it ends.')
    # the pair leads, so a one-shot ask sees what was read before it
    # accepts the window ("Gunna x Nike: I read the campaign as ...",
    # goldens g055 and g056, 2026-11-02)
    lead = ''
    talent = str(d.get('talent') or '').strip()
    brand = str(d.get('brand_partner') or '').strip()
    camp_name = str(d.get('campaign') or '').strip()
    t_fold = re.sub(r'[^a-z0-9]+', '', talent.lower())
    if camp_name:
        # the campaign as the reader named it ("Ganni x Lululemon"); the
        # talent joins when the campaign does not carry it ("Jennifer
        # Beals, Ganni x Lululemon")
        in_camp = bool(t_fold) and t_fold in re.sub(r'[^a-z0-9]+', '', camp_name.lower())
        lead = (f"{camp_name}: " if (in_camp or not talent) else f"{_cased(talent)}, {camp_name}: ")
    elif talent and brand and brand.lower() != talent.lower():
        lead = f"{_cased(talent)} x {_cased(brand)}: "
    note = ATTR_INFERRED_NOTE if d.get('dates_inferred') else ''
    if d.get('pinned_read'):
        # the order runs on the window of the figure the reader already holds
        return (f"{lead}I read the campaign as {camp}{PINNED_NOTE}. "
                f"Attribution window: {attr}.")
    return (f"{lead}I read the campaign as {camp}. {why} "
            f"Attribution window: {attr}.{note}")


def _payload(reply, followups, draft=None, **extra):
    out = {
        'success': True,
        'action': 'answer',
        'reply': reply,
        'followups': list(followups or []),
        'offer_deck': False,
        'deck_angle': None,
        'bpiq_collect': True,
    }
    if draft is not None:
        out['bpiq_wizard'] = dict(draft)
        if draft.get('step') == STEP_CHARGE:
            out['bpiq_confirm_payload'] = engine_inputs(draft)
            out['bpiq_collect'] = False
    out.update(extra)
    # A wizard turn is never a long subject read. The email-me card
    # and the status lane key off these.
    out.pop('read_job_id', None)
    out.pop('job_id', None)
    return out


def hold_payload(history=None, store=None, user=None, thread_id='',
                 today=None):
    """Repeat the open step. Used when the hook must not fall through."""
    draft = None
    if store is not None:
        try:
            hit = store.load(user, thread_id or '')
            if isinstance(hit, dict) and hit.get('step'):
                draft = dict(hit)
        except Exception:
            draft = None
    if not draft:
        draft = replay_history(history, today=today)
    if not draft:
        draft = empty_draft()
        draft['step'] = STEP_TALENT
    draft = sane_draft(draft)
    reply, chips = step_prompt(draft, today=today)
    reply = vary(reply, draft.get('step') or next_step(draft), history, draft)
    return _payload(reply, chips, draft=_stamp(draft, thread_id))


def step_prompt(draft, today=None):
    if isinstance(draft, dict) and not draft.get('read_checked') and draft.get('campaign_end'):
        # the window the reader already holds a figure for (pin_to_read),
        # settled before the step is worded so the window shown is the
        # window the order runs on
        _pinned = pin_to_read(draft, _TURN.user)
        if _pinned is not draft:
            draft.update(_pinned)
    step = (draft or {}).get('step') or next_step(draft)
    if step == STEP_TALENT:
        pair = (draft or {}).get('pair')
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and not (draft or {}).get('talent'):
            # the pairing is known, which side is the talent is not: one short question
            from prometheus import partnership_roles as _roles
            return _roles.which_question(pair[0], pair[1]), [str(pair[0]), str(pair[1]), CHIP_CANCEL]
        return ASK_TALENT, [CHIP_CANCEL]
    if step == STEP_CAMPAIGN:
        return ASK_CAMPAIGN, [CHIP_CANCEL]
    if step == STEP_DATES:
        hint, guess = (draft or {}).get('start_hint'), (draft or {}).get('start_guess')
        if hint and guess:
            # the reader gave the month it started; ask for the day, with
            # the first of that month through today one tap away
            return ASK_DATES_HINT.format(hint=hint), [open_range_chip(guess), CHIP_CANCEL]
        return ASK_DATES, [CHIP_CANCEL]
    if step == STEP_ATTR:
        return attr_reply(draft, today=today), [
            CHIP_ACCEPT, CHIP_DIFFERENT, CHIP_CANCEL]
    if step == STEP_ATTR_CUSTOM:
        return ASK_ATTR_CUSTOM, [CHIP_CANCEL]
    if step == STEP_CONTROL:
        return ASK_CONTROL, [CHIP_YES, CHIP_NO, CHIP_CANCEL]
    if step == STEP_ASSETS:
        return ASK_ASSETS, [CHIP_PASTE, CHIP_FIND, CHIP_CANCEL]
    if step == STEP_LINKS:
        return ASK_LINKS, [CHIP_CANCEL]
    usd = price_usd(bool((draft or {}).get('control_group')))
    return recap_reply(draft), [accept_chip(usd, included=included_seat(draft)), CHIP_DECLINE, CHIP_CANCEL]


def open_range_chip(start):
    """'September 1 to today' for a start date."""
    return f"{_d(start).strftime('%B %-d')} to today"


# The wording a step falls back to when its own prompt was the last
# thing said: never the identical question twice in a row (2026-11-11).
_STEP_ALT = {
    STEP_TALENT: ASK_TALENT_ALT, STEP_CAMPAIGN: ASK_CAMPAIGN_ALT, STEP_DATES: ASK_DATES_ALT,
    STEP_ATTR_CUSTOM: ASK_DATES_ALT, STEP_CONTROL: ASK_CONTROL_ALT, STEP_ASSETS: ASK_ASSETS_ALT,
    STEP_LINKS: ASK_LINKS_ALT,
}


def vary(reply, step, history, draft=None):
    """`reply`, or the step's other wording when `reply` is word for word
    what the last agent turn said (or what it ended on, after a line that
    said what was read)."""
    prev = ' '.join(str((last_agent_turn(history) or {}).get('text') or '').split())
    cur = ' '.join(str(reply or '').split())
    if not prev or not cur or not (prev == cur or prev.endswith(' ' + cur)):
        return reply
    alt = _STEP_ALT.get(step)
    d = draft or {}
    if step == STEP_TALENT and isinstance(d.get('pair'), (list, tuple)) and len(d['pair']) == 2 and not d.get('talent'):
        alt = ASK_WHICH_ALT.format(a=d['pair'][0], b=d['pair'][1])
    if step == STEP_DATES and d.get('start_hint') and d.get('start_guess'):
        g = _d(d['start_guess'])
        alt = ASK_DATES_HINT_ALT.format(
            hint=d['start_hint'], example=f"{g.month}/{min(g.day + 14, 28)}",
            first=g.strftime('%B %-d'))
    if alt and not prev.endswith(' '.join(alt.split())):
        return alt
    if step == STEP_ATTR:
        return str(reply) + ATTR_NUDGE
    if step == STEP_CHARGE:
        return CHARGE_NUDGE + '\n' + str(reply)
    return reply


def miss_count(draft, step):
    """How many replies in a row have not answered `step`."""
    m = (draft or {}).get('misses')
    if isinstance(m, dict) and m.get('step') == step:
        try:
            return int(m.get('n') or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def default_for(step, draft, today=None):
    """(draft, line) with the step's default taken on the reader's
    behalf, and the one plain line that says so; (None, '') for a step
    with no default (a name, the control group: those are asked again in
    other words)."""
    d = dict(draft or {})
    today = _today(today)
    if step in (STEP_ASSETS, STEP_LINKS):
        d['asset_mode'] = 'find'
        d['links'] = []
        line = DEFAULT_FIND_NO_LINK_LINE if step == STEP_LINKS else DEFAULT_FIND_LINE
    elif step == STEP_DATES and d.get('start_guess'):
        a, b = sorted([str(d['start_guess'])[:10], today.isoformat()])
        d['campaign_start'], d['campaign_end'] = a, b
        d['attr_start'], d['attr_end'] = attribution_window(a, b, today=today)
        d['attr_ok'] = False
        d['dates_inferred'] = True
        mark_window(d, '', inferred=True)
        d.pop('start_hint', None)
        d.pop('start_guess', None)
        line = f"I'll run it from {pretty_date(a)} through today."
    elif step in (STEP_ATTR, STEP_ATTR_CUSTOM) and d.get('attr_start') and d.get('attr_end'):
        d['attr_ok'] = True
        line = f"I'll use the standard window, {pretty_range(d['attr_start'], d['attr_end'])}."
    else:
        return None, ''
    d.pop('misses', None)
    d = _price_draft(d)
    d['step'] = next_step(d)
    return d, line


def sane_draft(draft):
    """A stored draft whose talent, campaign or brand is a bare yes / no
    (a draft written before 2026-11-11) loses that field: the step is
    asked again and the word is never shown as a name."""
    if not isinstance(draft, dict):
        return draft
    d = dict(draft)
    dirty = False
    for k in ('talent', 'campaign', 'brand_partner'):
        if d.get(k) and _slots.is_filler(d[k]):
            d[k] = ''
            dirty = True
    if dirty:
        step = d.get('step')
        want = next_step(d)
        order = [STEP_TALENT, STEP_CAMPAIGN, STEP_DATES, STEP_ATTR, STEP_ATTR_CUSTOM, STEP_CONTROL,
                 STEP_ASSETS, STEP_LINKS, STEP_CHARGE]
        if step not in order or order.index(want) < order.index(step):
            d['step'] = want
    return d


def _clean_name(text):
    t = str(text or '').strip().strip('"\'').strip()
    t = re.sub(r'^(?:it(?:\'s| is)|the (?:talent|property|campaign) is)\s+',
               '', t, flags=re.I)
    return t.strip(' .')


def _too_long_for_name(name, budget):
    """A name is a few words. A sentence past the budget that is not
    typed as a title is a remark, never the talent or the campaign."""
    words = str(name or '').split()
    if len(words) <= budget:
        return False
    try:
        from prometheus import intake_guard as _ig
        return not _ig.looks_like_title(name)
    except Exception:
        return True


def apply_answer(draft, text, today=None):
    """Advance one step from `text`. Returns (draft, err)."""
    d = dict(draft or empty_draft())
    step = d.get('step') or next_step(d)
    t = str(text or '').strip()
    today = _today(today)
    d.pop('_named', None)

    # A sentence that names its own slots ("JYT is the talent and GAP is
    # the brand. the partnership started in September 2026") fills them
    # by role at any step before the recap, and the wizard moves to the
    # first step still open (2026-11-11).
    if step != STEP_CHARGE and names_slots(t, step, today=today):
        merged, named = merge_slots(d, t, today=today)
        if named:
            merged = _price_draft(merged)
            merged['step'] = next_step(merged)
            merged['_named'] = named
            return merged, None

    if step == STEP_TALENT:
        name = _clean_name(t)
        # a bare yes / no is never a talent (2026-11-11: "yes" was stored
        # as the talent and led the window line as "Yes, gap x jyt")
        if not name or is_chip(name) or _slots.is_filler(name) or _too_long_for_name(name, 8):
            return d, step_prompt(d, today=today)[0]
        mine, other = _pair_pick(d, name)
        if mine:
            # the reader said which side of the pairing is the talent: the other side is the brand
            name = mine
            d['brand_partner'] = other
            d['brand_named'] = True
        d.pop('pair', None)
        d['talent'] = name
        if d.get('brand_partner') and d.get('brand_named') and not d.get('campaign'):
            d['campaign'] = _pair_name(name, d['brand_partner'])
            d['campaign_auto'] = True
    elif step == STEP_CAMPAIGN:
        name = _clean_name(t)
        if not name or _slots.is_filler(name) or _too_long_for_name(name, 12):
            return d, ASK_CAMPAIGN
        d['campaign'] = name
        d.pop('campaign_auto', None)
        if not d.get('brand_named'):
            d['brand_partner'] = _brand_from_campaign(name, d.get('talent'))
    elif step == STEP_DATES:
        start, end = parse_dates(t, today=today)
        inferred = False
        if not (start and end):
            # one date on its own is the start; the campaign is read as
            # running through today and the window step says so
            one, end_said = None, None
            try:
                one = _slots.single_date(t, today, hint=d.get('start_guess'))
                if not one:
                    got = _slots.parse(t, today=today)
                    if got.get('start') and got.get('start_precise'):
                        one, end_said = got['start'], got.get('end')
            except Exception:
                one = None
            if one:
                start, end = one, (end_said or today.isoformat())
                inferred = not end_said
                if _d(end) < _d(start):
                    start, end = end, start
        if not (start and end):
            # the could-not-read line is for an attempt at dates; a reply
            # that never tried is asked plainly (2026-11-05)
            try:
                from prometheus import intake_guard as _ig
                tried = _ig.is_dates_attempt(t)
            except Exception:
                tried = True
            if not tried:
                return d, step_prompt(d, today=today)[0]
            return d, ASK_DATES_UNREAD
        d['campaign_start'], d['campaign_end'] = start, end
        d['attr_start'], d['attr_end'] = attribution_window(
            start, end, today=today)
        d['attr_ok'] = False
        if inferred:
            d['dates_inferred'] = True
        else:
            d.pop('dates_inferred', None)
        mark_window(d, t, inferred=inferred)
        d.pop('start_hint', None)
        d.pop('start_guess', None)
    elif step == STEP_ATTR:
        low = _low(t)
        if low == _low(CHIP_DIFFERENT) or 'different' in low:
            d['step'] = STEP_ATTR_CUSTOM
            d['attr_ok'] = False
            return _price_draft(d), None
        if low == _low(CHIP_ACCEPT) or _ACCEPT_RE.match(t) or _YES_RE.match(t):
            d['attr_ok'] = True
        else:
            start, end = parse_dates(t, today=today)
            end_only = None
            if not (start and end) and d.get('dates_inferred'):
                try:
                    end_only = _slots.parse(t, today=today).get('end') or (
                        _slots.single_date(t, today, hint=d.get('campaign_end')))
                except Exception:
                    end_only = None
            if d.get('dates_inferred') and ((start and end) or end_only):
                # the end was read as today; dates typed here are the
                # campaign's own, and the window is shown again
                a, b = (start, end) if (start and end) else sorted([d['campaign_start'], end_only])
                d['campaign_start'], d['campaign_end'] = a, b
                d['attr_start'], d['attr_end'] = attribution_window(a, b, today=today)
                d['attr_ok'] = False
                d.pop('dates_inferred', None)
                mark_window(d, t)
                d['step'] = STEP_ATTR
                return _price_draft(d), None
            if start and end:
                if d.get('pinned_read') and str(end)[:10] != str(d.get('campaign_end'))[:10] \
                        and not _slots.says_ongoing(t):
                    # a new end date, named in words, on an order that was
                    # running on the window of a figure already held: a new
                    # read on the window the reader asked for
                    a, b = sorted([str(d['campaign_start'])[:10], str(end)[:10]])
                    d['campaign_start'], d['campaign_end'] = a, b
                    d.pop('pinned_read', None)
                    d.pop('given_read', None)
                    d['window_open'], d['end_named'], d['read_checked'] = False, True, True
                d['attr_start'], d['attr_end'] = start, end
                d['attr_ok'] = True
            elif not (_YES_RE.match(t) or low in ('accept', 'ok', 'okay')):
                return d, (attr_reply(d, today=today) + ATTR_NUDGE)
            else:
                d['attr_ok'] = True
    elif step == STEP_ATTR_CUSTOM:
        start, end = parse_dates(t, today=today)
        if not (start and end):
            return d, ASK_ATTR_CUSTOM
        d['attr_start'], d['attr_end'] = start, end
        d['attr_ok'] = True
    elif step == STEP_CONTROL:
        if _YES_RE.match(t) or _low(t) == 'yes' or (_CONTROL_YES_RE.match(t) and not _CONTROL_NO_RE.match(t)):
            d['control_group'] = True
        elif _NO_RE.match(t) or _low(t) == 'no' or _CONTROL_NO_RE.match(t):
            d['control_group'] = False
        else:
            return d, ASK_CONTROL
    elif step == STEP_ASSETS:
        urls = extract_urls(t)
        if urls:
            d['asset_mode'] = 'links'
            d['links'] = urls
        elif (_FIND_RE.search(t) or _slots.finds_assets(t) or _slots.has_no_links(t) or _NO_RE.match(t)
              or _low(t) == _low(CHIP_FIND)):
            # "find the campaign asset", "find it", "you find it", "I don't
            # have one", "not sure": the engine researches the campaign's
            # assets itself (2026-11-11)
            d['asset_mode'] = 'find'
            d['links'] = []
        elif _PASTE_RE.search(t) or _YES_RE.match(t) or _low(t) == _low(CHIP_PASTE):
            d['asset_mode'] = 'links'
        else:
            return d, ASK_ASSETS
    elif step == STEP_LINKS:
        urls = extract_urls(t)
        if urls:
            d['links'] = urls
            d['asset_mode'] = 'links'
        elif _slots.finds_assets(t) or _slots.has_no_links(t) or _NO_RE.match(t) or _low(t) == _low(CHIP_FIND):
            d['asset_mode'] = 'find'
            d['links'] = []
        else:
            return d, ASK_LINKS
    d.pop('misses', None)
    d = _price_draft(d)
    d['step'] = next_step(d)
    return d, None


def saved_valuation_block(text, window=None):
    """The binding block a read's prompt carries when the question names
    a pairing Crosswalk has already valued (migration.bpiq_anchors,
    2026-11-11): the read states the valuation's own total and people
    reached, never a second figure. '' when the words name no valued
    pairing. Never raises."""
    try:
        from migration import bpiq_anchors as _ba
        hit = _ba.valuation_block(text, window=window) or ''
    except Exception:
        hit = ''
    if hit:
        return hit
    # no valuation saved on it: the read never prices the partnership
    # (prometheus.partnership_value, Jenna 2026-11-11)
    try:
        from prometheus import partnership_value as _pv
        return _pv.prompt_block(text) or ''
    except Exception:
        return ''


def copy_is_clean(text):
    """True when user-facing copy has no banned words and no em dash."""
    s = str(text or '')
    if any(ch in s for ch in _EM):
        return False
    low = s.lower()
    return not any(tok in low for tok in _BANNED_USER)


def email_copy(draft_or_inputs):
    """Subject and body for the finished-valuation email."""
    d = draft_or_inputs or {}
    talent = d.get('talent') or d.get('qualifier') or 'the talent'
    campaign = d.get('campaign') or (
        f"{d.get('qualifier')} x {d.get('brand_partner')}".strip(' x')
        or 'the campaign')
    subject = f"{campaign}: valuation with {talent} is ready"
    body = (
        f"The {campaign} valuation with {talent} is ready.\n\n"
        "Open it in Brand Partnership IQ.\n\n"
        "Prometheus\nCrosswalk"
    )
    return subject, body


class MemoryStore:
    """In-process draft store for tests and as a fail-safe."""

    def __init__(self):
        self.rows = {}

    def _key(self, user, thread_id):
        return f"{user or ''}|{thread_id or ''}"

    def load(self, user, thread_id=''):
        hit = self.rows.get(self._key(user, thread_id))
        if hit:
            return hit
        # the per-user copy serves a caller that knows no thread, or the
        # thread it was saved on; never another thread (2026-11-05)
        hit = self.rows.get(self._key(user, ''))
        if hit and not _same_thread(hit.get('thread_id'), thread_id):
            return None
        return hit

    def save(self, user, thread_id, draft):
        doc = dict(draft or {}, thread_id=str(thread_id or ''))
        self.rows[self._key(user, thread_id)] = dict(doc)
        if thread_id:
            self.rows[self._key(user, '')] = dict(doc)
        return True

    def clear(self, user, thread_id=''):
        self.rows.pop(self._key(user, thread_id), None)
        self.rows.pop(self._key(user, ''), None)
        return True

    def save_marker(self, user, thread_id, marker):
        self.rows['marker|' + self._key(user, thread_id)] = dict(marker or {})
        return True

    def load_marker(self, user, thread_id=''):
        return self.rows.get('marker|' + self._key(user, thread_id))


def shared_store(g=None):
    """The shared S3 store other intakes use. Never an in-process dict
    when the host has a client: Render runs more than one worker."""
    try:
        from .host import host as _host
        if _host_cap(_host, 's3_client') is not None or (
                callable(getattr(_host, 'has', None)) and (
                    _host.has('s3_client') or _host.has('bucket'))):
            return _s3_store(_host)
    except Exception:
        pass
    try:
        h = (g or {}).get('_H') if g else None
        if h is not None and _host_cap(h, 's3_client') is not None:
            return _s3_store(h)
    except Exception:
        pass
    return MemoryStore()


def _safe_store(g):
    return shared_store(g)


def store_for(body, g=None):
    """The draft store for this request. A probe ask (the golden set,
    the canary, a regression replay: body['persist'] is False) keeps
    its draft in this process, so one probe's open wizard never
    captures the next probe's unrelated question on the shared store
    (2026-11-02). Every real ask uses the shared store."""
    try:
        if isinstance(body, dict) and body.get('persist') is False:
            return MemoryStore()
        from . import faults as _faults
        if _faults.is_probe():
            return MemoryStore()
    except Exception:
        pass
    return shared_store(g)


def _s3_store(host):
    """Draft store on the same pending-intake document other guided
    flows use, plus a per-thread key. Survives workers and reloads."""
    class _S:
        def _ids(self, user, thread_id):
            try:
                import prometheus_memory as pmm
                uk = pmm._user_key(user)
            except Exception:
                uk = ''.join(c for c in str(user or 'anon')
                             if c.isalnum() or c in '-_.@').lower() or 'anon'
            tid = ''.join(c for c in str(thread_id or 'none')
                          if c.isalnum() or c in '-_') or 'none'
            return uk, tid

        def _key(self, user, thread_id):
            uk, tid = self._ids(user, thread_id)
            return f"system/pm_pending_intake/{uk}/bpiq_wizard_{tid}.json"

        def _s3(self):
            return _host_cap(host, 's3_client')

        def _bucket(self):
            return _host_cap(host, 'bucket', 'S3_BUCKET') or 'dashboard-inputs'

        def load(self, user, thread_id=''):
            s3 = self._s3()
            bucket = self._bucket()
            if s3 is None:
                return None
            try:
                from prometheus import pending_intake as pi
                hit = pi.peek(s3, bucket, user, WIZARD_FLOW)
                # the shared stash is per reader; it answers only for the
                # thread it was saved on (Jenna 2026-11-05: an intake armed
                # on one thread never reads a message on another)
                if isinstance(hit, dict) and hit.get('step') and _same_thread(hit.get('thread_id'), thread_id):
                    return hit
            except Exception:
                pass
            try:
                obj = s3.get_object(Bucket=bucket, Key=self._key(user, thread_id))
                import json
                doc = json.loads(obj['Body'].read().decode('utf-8'))
                if not isinstance(doc, dict):
                    return None
                # the per-thread copy keeps a day, like the shared stash
                # (2026-11-05): a draft left open weeks ago never arms
                # the thread again. A copy with no stamp is stale.
                import time as _time
                try:
                    ts = float(doc.get('ts') or 0)
                except (TypeError, ValueError):
                    ts = 0.0
                if not ts or _time.time() - ts > DRAFT_TTL_S:
                    return None
                return doc
            except Exception:
                return None

        def save(self, user, thread_id, draft):
            s3 = self._s3()
            bucket = self._bucket()
            import time as _time
            doc = dict(draft or {}, thread_id=thread_id or '', ts=_time.time())
            if s3 is None:
                return False
            ok = False
            try:
                from prometheus import pending_intake as pi
                ok = bool(pi.stash(s3, bucket, user, WIZARD_FLOW, doc,
                                   run_label=CHIP))
            except Exception:
                traceback.print_exc()
            try:
                import json
                s3.put_object(
                    Bucket=bucket, Key=self._key(user, thread_id),
                    Body=json.dumps(doc, default=str).encode('utf-8'),
                    ContentType='application/json')
                return True
            except Exception:
                traceback.print_exc()
                return ok

        def clear(self, user, thread_id=''):
            s3 = self._s3()
            bucket = self._bucket()
            if s3 is None:
                return False
            try:
                from prometheus import pending_intake as pi
                pi.clear(s3, bucket, user, WIZARD_FLOW)
            except Exception:
                pass
            try:
                s3.delete_object(Bucket=bucket, Key=self._key(user, thread_id))
            except Exception:
                pass
            return True

        # The run marker (the job id the last Accept on this thread
        # owns) lives on its own key, never in the pending-intake stash:
        # a stashed document would read as a brief waiting on a "yes".
        def _marker_key(self, user, thread_id):
            uk, tid = self._ids(user, thread_id)
            return f"system/pm_pending_intake/{uk}/bpiq_run_{tid}.json"

        def save_marker(self, user, thread_id, marker):
            s3 = self._s3()
            if s3 is None:
                return False
            try:
                import json
                s3.put_object(
                    Bucket=self._bucket(), Key=self._marker_key(user, thread_id),
                    Body=json.dumps(dict(marker or {}), default=str).encode('utf-8'),
                    ContentType='application/json')
                return True
            except Exception:
                return False

        def load_marker(self, user, thread_id=''):
            s3 = self._s3()
            if s3 is None:
                return None
            try:
                import json
                obj = s3.get_object(Bucket=self._bucket(), Key=self._marker_key(user, thread_id))
                doc = json.loads(obj['Body'].read().decode('utf-8'))
                return doc if isinstance(doc, dict) else None
            except Exception:
                return None
    return _S()


def _load_draft(store, user, thread_id, body, history, today=None):
    """The open draft for THIS thread: the body's echo when it is this
    thread's (_body_draft), the store's copy for (user, thread), the
    last agent turn's meta, else a replay of the thread. Keyed by
    (user, thread) throughout (Jenna 2026-11-05)."""
    hit = _body_draft(body, thread_id, history)
    if hit:
        return sane_draft(hit)
    if store is not None:
        try:
            hit = store.load(user, thread_id)
            if isinstance(hit, dict) and hit.get('step'):
                return sane_draft(dict(hit))
        except Exception:
            pass
    # the thread's own turns: only when its last agent turn asked a step
    # or carried the way back to one (a thread that moved on is not
    # replayed into an old wizard)
    if not last_agent_is_wizard(history):
        return None
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict):
            continue
        if str(turn.get('role') or '') not in ('agent', 'assistant'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        wiz = meta.get('bpiq_wizard') or turn.get('bpiq_wizard')
        if isinstance(wiz, dict) and wiz.get('step'):
            return dict(wiz)
        break
    return replay_history(history, today=today)


def _uname(user):
    if isinstance(user, dict):
        return str(user.get('username') or user.get('email') or '').strip()
    return str(user or '').strip()


def _seat_is_free(user, uname=None, g=None):
    """True for the seats other priced Prometheus steps never bill:
    super_admin / Crosswalk staff, or an unlimited flag / credits.
    Re-reads the live users row when the passed object is thin."""
    host = (g or {}).get('_H') if g else None
    try:
        from prometheus.priced_read import seat_is_free
        if seat_is_free(user, uname or _uname(user), host):
            return True
    except Exception:
        pass
    if not isinstance(user, dict):
        return False
    role = str(user.get('role') or '').strip().lower().replace(' ', '_')
    if role in ('super_admin', 'superadmin'):
        return True
    if bool(user.get('unlimited')) or bool(user.get('is_super_admin')) or bool(user.get('no_cap')):
        return True
    if str(user.get('billing_mode') or '').strip().lower() == 'unlimited':
        return True
    try:
        import wallet as _w
        if _w.is_unlimited(user):
            return True
    except Exception:
        try:
            if int(user.get('credits', 0) or 0) == -1:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _charge_seat(user, uname, inputs, job_id, g, charge_fn):
    """Same door as the other priced Prometheus reads: free for staff
    and unlimited, else priced_read.charge. Never a private balance
    check. Always re-evaluates the live seat; a stored funds verdict
    on the draft is ignored."""
    if _seat_is_free(user, uname, g):
        return True
    if charge_fn is not None:
        return bool(charge_fn(uname or user, inputs, job_id))
    host = (g or {}).get('_H') if g else None
    if host is None:
        try:
            from .host import host as host
        except Exception:
            host = None
    # Nothing priced starts without its charge (Jenna 2026-11-11). A
    # seat that is not included, with no name to bill or no way to take
    # the charge in this process, is not charged and nothing starts;
    # the shared door answers True in both cases, which is right for a
    # read and wrong for a $500 order.
    if not str(uname or '').strip() or _host_cap(host, 'consume_credit') is None:
        print(f"[bpiq-wizard] no charge door for {uname or 'unknown'!r}; the valuation was not started")
        return None
    subj = (f"{inputs.get('qualifier')} x "
            f"{inputs.get('campaign') or inputs.get('brand_partner')}")
    pull = PULL_CONTROL if inputs.get('control_group') else PULL_BASE
    credits = CREDITS_CONTROL if inputs.get('control_group') else CREDITS_BASE
    desc = f'Brand Partnership Valuation: {subj}'
    try:
        from prometheus.priced_read import charge as pr_charge
        return bool(pr_charge(host, uname, subj, pull, credits, desc))
    except Exception:
        traceback.print_exc()
        return False


def charge_stamp(draft, user, uname, g):
    """What this Accept charged, in the words the quote used: it rides
    the job so the line that hands the valuation over can say so
    (2026-11-11). {} when it cannot be read; never raises."""
    try:
        if _seat_is_free(user, uname, g):
            return {'included': True}
        return {'included': False, 'label': price_label(price_usd(bool((draft or {}).get('control_group'))))}
    except Exception:
        return {}


def _start_valuation(job_id, uname, inputs, extras, g, start_fn,
                     thread_id=''):
    """Enqueue the valuation the same way the other engine jobs do.
    The thread id rides the status document so the finished read's
    note lands in the thread that asked (the worker is another
    process)."""
    extras = dict(extras or {})
    if thread_id:
        extras.setdefault('thread_id', thread_id)
    if start_fn is not None:
        start_fn(job_id, uname, inputs, extras)
        return True
    status_write = (g or {}).get('_pm_bpiq_status_write') if g else None
    runner = (g or {}).get('_pm_run_bpiq_job') if g else None
    rr = (g or {}).get('_rr') if g else None
    host = (g or {}).get('_H') if g else None
    if host is None:
        try:
            from .host import host as host
        except Exception:
            host = None
    if runner is None:
        runner = _host_cap(host, 'run_bpiq_job')
    if status_write is None:
        try:
            from prometheus.legacy.jobs import _pm_bpiq_status_write
            status_write = _pm_bpiq_status_write
        except Exception:
            status_write = None
    if runner is None:
        try:
            from prometheus.legacy.chat import _pm_run_bpiq_job
            runner = _pm_run_bpiq_job
        except Exception:
            runner = None
    if rr is None:
        try:
            from prometheus import read_recovery as rr
        except Exception:
            rr = None
    if runner is None or rr is None:
        print('[bpiq-wizard] valuation runner missing; not started')
        return False
    if status_write is None:
        def status_write(job_id, payload):
            return None
    if thread_id:
        _inner_write = status_write

        def status_write(jid, payload, _w=_inner_write, _t=thread_id):   # noqa: F811
            doc = dict(payload or {})
            doc.setdefault('thread_id', _t)
            return _w(jid, doc)
    try:
        rr.start_engine_job(
            'bpiq', job_id, uname, inputs, extras, status_write, runner)
        return True
    except Exception:
        traceback.print_exc()
        return False


UNDERPINNINGS = None        # tests: the saved-research store the order is matched against


def _underpinnings(g):
    """The saved-research store, or None (a hermetic run with no test
    store never reaches the network)."""
    if UNDERPINNINGS is not None:
        return UNDERPINNINGS
    if os.environ.get('REGRESSION_TEST_MODE'):
        return None
    try:
        from migration.bpiq_synthesis import S3Underpinnings
        from prometheus.host import host as _host
        return S3Underpinnings(_host.s3_client, _host.S3_BUCKET)
    except Exception:
        return None


def adopt_saved(inputs, g=None):
    """The order named the way its saved research is named, when the
    pairing already has research under the other order of the names
    ("JYT x Gap" against "Gap x JYT"): the same key, so the run registry
    and the engine see one partnership, not two (2026-11-11). The
    order's dates and everything else stay its own. Never raises."""
    try:
        up = _underpinnings(g)
        if up is None:
            return inputs
        from migration import bpiq_anchors as _ba
        from migration.bpiq_synthesis import run_key as _rk
        doc, key = _ba.saved_record(inputs, up, _rk)
        if doc and key != _rk(inputs):
            return _ba.adopt_record(inputs, doc)
    except Exception:
        traceback.print_exc()
    return inputs


def _registry(g, registry=None):
    """The shared run registry (S3 through the host), else the
    process-local one. Never raises."""
    if registry is not None:
        return registry
    try:
        from migration import bpiq_synthesis as _bs
    except Exception:
        return None
    host = (g or {}).get('_H') if g else None
    if host is None:
        try:
            from .host import host as host
        except Exception:
            host = None
    try:
        return _bs.registry_for(host)
    except Exception:
        return None


def _run_marker(store, user, thread_id):
    """The step-less marker the last Accept on this thread left: the
    run id and the key it owns. Invisible to the wizard's routing."""
    if store is None:
        return None
    loader = getattr(store, 'load_marker', None)
    if not callable(loader):
        return None
    try:
        hit = loader(user, thread_id)
    except Exception:
        return None
    if isinstance(hit, dict) and hit.get('job_id') and hit.get('run_key'):
        return hit
    return None


def _refund_seat(user, uname, inputs, job_id, g, refund_fn=None):
    """Reverse the charge of a valuation that did not start. True when
    there was nothing to reverse (an included seat) or the reversal
    landed; False when it could not be made here (ops is told). Never
    raises."""
    try:
        if _seat_is_free(user, uname, g):
            return True
        control = bool((inputs or {}).get('control_group'))
        subj = f"{(inputs or {}).get('qualifier')} x {(inputs or {}).get('campaign') or (inputs or {}).get('brand_partner')}"
        desc = f'Brand Partnership Valuation: {subj}'
        pull = PULL_CONTROL if control else PULL_BASE
        credits = CREDITS_CONTROL if control else CREDITS_BASE
        if refund_fn is not None:
            return bool(refund_fn(uname or user, inputs, job_id))
        host = (g or {}).get('_H') if g else None
        if host is None:
            try:
                from .host import host as host
            except Exception:
                host = None
        fn = _host_cap(host, 'refund_credit')
        if fn is None:
            import sys as _sys
            fn = getattr(_sys.modules.get('app'), 'refund_credit', None)
        ok = False
        if callable(fn):
            ok = bool(fn(uname, credits=credits, reason='valuation did not start', job_id=job_id,
                         charge_description=desc, pull_type=pull))
        if not ok:
            try:
                mail = _host_cap(host, 'error_email')
                if callable(mail):
                    mail('bpiq-wizard/charge-not-reversed',
                         RuntimeError(f"valuation {job_id} for {uname} was charged, did not start, and the charge "
                                      f"could not be reversed here: {desc}"))
            except Exception:
                traceback.print_exc()
        return ok
    except Exception:
        traceback.print_exc()
        return False


def _charge_and_start(draft, user, uname, ppu, g, charge_fn, start_fn,
                      store=None, thread_id='', registry=None, refund_fn=None):
    """Accept. One run per ask (Jenna 2026-10-28): the run key
    (talent, campaign, dates, control group, spelling-insensitive) is
    claimed in the shared registry BEFORE the charge. A second Accept
    on the same draft, a re-walk of the wizard, or another caller with
    the same ask finds the claim and gets the same run id back; a
    finished one is pointed at. Nothing is charged twice and no second
    job is enqueued."""
    draft = dict(draft or empty_draft())
    # A leftover funds / unpaid stamp is never a verdict. Accept
    # re-reads the live seat every tap.
    for k in ('funds_required', 'charge_state', 'needs_funds',
              'unpaid', 'funds_ok'):
        draft.pop(k, None)
    if not draft.get('read_checked'):
        draft = pin_to_read(draft, uname or user)
    inputs = adopt_saved(engine_inputs(draft), g)
    if draft.get('charged') or draft.get('job_id'):
        return _payload(ALREADY_RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                        bpiq_job_id=draft.get('job_id'))
    try:
        from migration.bpiq_synthesis import run_key as _run_key, claim_run as _claim, \
            release_run as _release
    except Exception:
        _run_key = _claim = _release = None
    key = _run_key(inputs) if _run_key else ''
    # the draft's own run id from an earlier Accept on this thread
    marker = _run_marker(store, uname or user, thread_id)
    if marker and key and marker.get('run_key') == key:
        return _payload(ALREADY_RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                        bpiq_job_id=marker.get('job_id'))
    try:
        import uuid
        job_id = uuid.uuid4().hex[:12]
    except Exception:
        job_id = 'bpiq'
    reg = _registry(g, registry) if _claim else None
    if reg is not None:
        try:
            owner, created = _claim(reg, inputs, job_id, user=uname or _uname(user),
                                    thread_id=thread_id)
        except Exception:
            traceback.print_exc()
            owner, created = None, True
        if owner and not created:
            if store is not None:
                try:
                    store.clear(uname or user, thread_id)
                except Exception:
                    pass
            if str(owner.get('status') or '') == 'done':
                name = (owner.get('project_name')
                        or f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}")
                return _payload(EXISTS_REPLY.format(name=name), [], draft=None,
                                bpiq_collect=False, bpiq_job_id=owner.get('job_id'),
                                bpiq_existing_key=owner.get('s3_key'))
            return _payload(ALREADY_RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                            bpiq_job_id=owner.get('job_id'))
    ok = _charge_seat(user, uname, inputs, job_id, g, charge_fn)
    if not ok:
        if reg is not None and _release:
            try:
                _release(reg, inputs, job_id)
            except Exception:
                pass
        # None: the charge could not be taken at all (no door); False: the
        # seat could not cover it. Either way nothing starts.
        return _payload(CHARGE_FAILED_REPLY if ok is None else FUNDS_REPLY, [], draft=draft, bpiq_collect=False)
    extras = dict(ppu or {})
    extras['bpiq_charge'] = charge_stamp(draft, user, uname, g)
    try:
        started = _start_valuation(job_id, uname, inputs, extras, g, start_fn,
                                   thread_id=thread_id)
    except Exception:
        traceback.print_exc()
        started = False
    if started is False:
        # Charged and not started is never left standing (Jenna
        # 2026-11-11): the charge is reversed, the claim is released,
        # the draft stays on its recap and the reader is told plainly.
        if reg is not None and _release:
            try:
                _release(reg, inputs, job_id)
            except Exception:
                pass
        reversed_ok = _refund_seat(user, uname, inputs, job_id, g, refund_fn)
        return _payload(START_FAILED_REPLY if reversed_ok else START_FAILED_HELD_REPLY,
                        [accept_chip(price_usd(bool(draft.get('control_group'))), included=included_seat(draft)),
                         CHIP_DECLINE, CHIP_CANCEL], draft=draft, bpiq_collect=False)
    # The draft leaves the store (later turns on the thread are not the
    # wizard's); a step-less marker keeps the run id it owns.
    if store is not None:
        try:
            store.clear(uname or user, thread_id)
        except Exception:
            pass
        saver = getattr(store, 'save_marker', None)
        if callable(saver):
            try:
                saver(uname or user, thread_id, {
                    'job_id': job_id, 'run_key': key, 'charged': True,
                    'talent': inputs.get('qualifier'), 'campaign': inputs.get('campaign'),
                    'started_at': datetime.utcnow().isoformat() + 'Z'})
            except Exception:
                pass
    return _payload(RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                    bpiq_job_id=job_id)


def _open_on_value(kind, text, history, draft, store, user, thread_id, today):
    """The reply to a question the valuation owns (what a partnership is
    worth in dollars, or a deck of a read that stated it). With an order
    already open on the thread the open step is asked again, in other
    words when it was the last thing said. With none, the order opens
    filled from the words and the thread, and asks its first open step."""
    from prometheus import partnership_value as _pv
    if draft:
        d = sane_draft(dict(draft))
        d['step'] = d.get('step') or next_step(d)
        reply, chips = step_prompt(d, today=today)
        reply = vary(reply, d['step'], history, d)
        if store is not None:
            store.save(user, thread_id, d)
        return _payload(f"{_pv.lead(kind, had_draft=True)}\n\n{reply}", chips, draft=d)
    d = empty_draft()
    seed = ''
    try:
        seed = _pv.seed(text, history, kind)
    except Exception:
        seed = ''
    if seed:
        d = apply_ahead(d, seed, today=today)
    # dates the question itself gave ("... ran June 3 to August 20"), else
    # the window the thread's read ran on
    for src in (text, seed):
        if d.get('campaign_start') or not src:
            break
        a, b = parse_dates(src, today=today)
        if a and b:
            d['campaign_start'], d['campaign_end'] = a, b
            d['attr_start'], d['attr_end'] = attribution_window(a, b, today=today)
            mark_window(d, src)
            d.pop('start_hint', None)
            d.pop('start_guess', None)
    d = sane_draft(d)
    d['step'] = next_step(d)
    d = _price_draft(d)
    reply, chips = step_prompt(d, today=today)
    if store is not None:
        store.save(user, thread_id, d)
    bits = [_pv.lead(kind)]
    if d.get('talent') and d.get('brand_partner') and not d.get('pair') \
            and d.get('step') not in (STEP_ATTR, STEP_CHARGE):
        # which side is the talent came from what is known (the reader's
        # own words, a read on the pairing, the library), never from the
        # order the names were written in; when nothing settles it the
        # step below asks which one (Jenna 2026-11-11)
        ack = ack_line({'talent': d.get('talent'), 'brand': d.get('brand_partner')})
        if ack:
            bits.append(ack)
    return _payload(' '.join(bits) + '\n\n' + reply, chips, draft=d)


def deck_gate(user, body, text, history, pick='', g=None):
    """The deck surface asks here first: a deck of a read that stated a
    partnership's dollar value (the words, or the subject picked for
    the deck) is the valuation order, never a deck built from the read
    (Jenna 2026-11-11). A deck of a valuation this thread already holds
    is the valuation deck again, built by the one valuation deck builder
    from the saved file (prometheus.bpiq_deck), never a second deck laid
    out from the chat's words. Returns the order's payload or the deck
    job's, or None when the deck is any other deck. Never raises."""
    try:
        try:
            from prometheus import bpiq_deck as _bd
            held = _bd.thread_valuation(history, text, pick)
            if not held and isinstance(body, dict) and str(body.get('_pm_order_words') or '').strip():
                held = _bd.thread_valuation(history, str(body.get('_pm_order_words')), pick)
            if held:
                again = _bd.start_reexport(user, held, g or {})
                if again is not None:
                    return again
        except Exception:
            traceback.print_exc()
        if pv_kind(text, history, pick=pick, body=body) != 'deck':
            return None
        uname = _uname(user)
        b = dict(body or {}, _pv_pick=str(pick or ''))
        hit = analyze_hook(text, b, history, user, {}, uname, g or {})
        if isinstance(hit, dict):
            hit = dict(hit)
            hit.pop('read_job_id', None)
            hit.pop('job_id', None)
            # The deck surface's caller draws a reply with no deck job as
            # a guidance line with its chips; any other shape reads there
            # as a deck that failed to start.
            hit.update({'success': False, 'guidance': True, 'error': str(hit.get('reply') or '')})
        return hit
    except Exception:
        traceback.print_exc()
        return None


def analyze_hook(text, body, history, user, ppu, uname, g,
                 first=None, today=None, store=None, charge_fn=None,
                 start_fn=None, registry=None, refund_fn=None):
    """One wizard turn. Returns a chat payload, or None when this is
    not a Brand Partnership Valuation ask and no draft is open (or the
    message is a question the open step does not own: the service
    routes it and the reply carries the way back)."""
    was = _set_turn(uname or user, history)
    try:
        hit = _analyze_hook(
            text, body, history, user, ppu, uname, g,
            first=first, today=today, store=store,
            charge_fn=charge_fn, start_fn=start_fn, registry=registry, refund_fn=refund_fn)
        # the draft the widget echoes back names its thread, so another
        # thread can tell it is not its own (2026-11-05)
        if isinstance(hit, dict) and isinstance(hit.get('bpiq_wizard'), dict):
            _stamp(hit['bpiq_wizard'], str((body or {}).get('thread_id') or ''))
        return hit
    except Exception:
        traceback.print_exc()
        return None
    finally:
        _restore_turn(was)


def _analyze_hook(text, body, history, user, ppu, uname, g,
                  first=None, today=None, store=None, charge_fn=None,
                  start_fn=None, registry=None, refund_fn=None):
    t = str(text or '').strip()
    thread_id = str((body or {}).get('thread_id') or '')
    uname = uname or _uname(user)
    today = _today(today)
    # Included with your plan (Jenna 2026-11-08): an unlimited seat's recap
    # carries no price and a bare Accept; decided once per turn from the
    # live seat and read by recap_lines / step_prompt through included_seat
    try:
        from flask import g as _g_inc
        _g_inc._pm_bpiq_included = bool(_seat_is_free(user, uname, g))
        # the quote is the seat's own rate, the one the charge takes
        _tab, _sym = seat_prices(uname)
        _g_inc._pm_bpiq_prices = _tab
        _g_inc._pm_bpiq_symbol = _sym
    except Exception:
        pass
    # A start phrase always opens step 1. Cancel any leftover draft
    # first so a prior subject on the thread cannot become the talent
    # or fall through to a library-comp read.
    if is_start(t):
        if store is None:
            store = store_for(body, g)
        return _start_fresh(store, uname or user, thread_id, today)
    if store is None:
        store = store_for(body, g)
    draft = _load_draft(store, uname or user, thread_id, body, history,
                        today=today)
    if draft and not draft.get('read_checked') and draft.get('campaign_end'):
        # the window of the figure the reader already holds, settled
        # before this turn's answer is read
        draft = pin_to_read(draft, uname, history=history)
    intent = False
    if g and callable(g.get('_pm_bpiq_intent')):
        try:
            intent = bool(g['_pm_bpiq_intent'](t))
        except Exception:
            intent = is_chip(t)
    else:
        try:
            from prometheus import intents as _intents
            intent = bool(_intents.bpiq_intent(t, CHIP))
        except Exception:
            intent = is_chip(t)
    if first is True:
        intent = True
    confirm = (body or {}).get('bpiq_confirm')
    # The widget's collect flag (bpiq_inputs) is page state and follows
    # the reader across threads; it never arms a thread on its own. A
    # thread is collecting when ITS draft is open: the store's copy, the
    # body's echo for this thread, its last agent turn (2026-11-05).
    if not draft and last_agent_is_wizard(history):
        draft = empty_draft()
        draft['step'] = step_from_prompt(
            str((last_agent_turn(history) or {}).get('text') or '')) or STEP_TALENT
    collecting = bool(draft)

    if is_cancel(t) and (draft or collecting or intent):
        if store is not None:
            store.clear(uname or user, thread_id)
        # the other flows and this thread's open offers go with it
        _drop_everything(uname or user, thread_id)
        return _payload(CANCEL_REPLY, [], draft=None, bpiq_collect=False)

    if draft and draft.get('step') == STEP_CHARGE and is_decline(t):
        if store is not None:
            store.clear(uname or user, thread_id)
        return _payload(DECLINE_REPLY, [], draft=None, bpiq_collect=False)

    prev_txt = str((last_agent_turn(history) or {}).get('text') or '')
    if prev_txt.startswith("It's running") and (
            _ACCEPT_RE.match(t) or _low(t).startswith('accept')):
        marker = _run_marker(store, uname or user, thread_id)
        return _payload(ALREADY_RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                        bpiq_job_id=(marker or {}).get('job_id'))

    if isinstance(confirm, dict) and (confirm.get('qualifier') or confirm.get('talent')):
        if confirm.get('talent') and not (draft or {}).get('talent'):
            draft = dict(draft or empty_draft(), **{
                'talent': confirm.get('talent') or confirm.get('qualifier'),
                'campaign': confirm.get('campaign') or '',
                'campaign_start': confirm.get('event_start') or confirm.get('campaign_start'),
                'campaign_end': confirm.get('event_end') or confirm.get('campaign_end'),
                'attr_start': confirm.get('post_start') or confirm.get('attr_start'),
                'attr_end': confirm.get('post_end') or confirm.get('attr_end'),
                'control_group': bool(confirm.get('control_group')),
                'asset_mode': confirm.get('asset_mode') or (
                    'links' if confirm.get('links') else 'find'),
                'links': list(confirm.get('links') or []),
                'brand_partner': confirm.get('brand_partner') or '',
                'step': STEP_CHARGE,
            }, **{k: confirm[k] for k in ('window_open', 'end_named', 'pinned_read', 'given_read')
                  if confirm.get(k)})
            draft = _price_draft(draft)
        elif draft:
            draft = _price_draft(dict(draft, step=STEP_CHARGE))
        else:
            draft = _price_draft(dict(empty_draft(), **{
                'talent': confirm.get('qualifier'),
                'campaign': confirm.get('campaign') or confirm.get('brand_partner'),
                'campaign_start': confirm.get('event_start'),
                'campaign_end': confirm.get('event_end'),
                'attr_start': confirm.get('post_start') or confirm.get('event_start'),
                'attr_end': confirm.get('post_end') or confirm.get('event_end'),
                'control_group': bool(confirm.get('control_group')),
                'asset_mode': confirm.get('asset_mode') or (
                    'links' if confirm.get('links') else 'find'),
                'links': list(confirm.get('links') or []),
                'brand_partner': confirm.get('brand_partner') or '',
                'step': STEP_CHARGE,
            }))
        hit = _charge_and_start(
            draft, user, uname, ppu, g, charge_fn, start_fn,
            store=store, thread_id=thread_id, registry=registry, refund_fn=refund_fn)
        return hit

    if draft and draft.get('step') == STEP_CHARGE and (
            _ACCEPT_RE.match(t) or _low(t).startswith('accept')):
        hit = _charge_and_start(
            draft, user, uname, ppu, g, charge_fn, start_fn,
            store=store, thread_id=thread_id, registry=registry, refund_fn=refund_fn)
        return hit

    if draft and is_decline(t):
        if store is not None:
            store.clear(uname or user, thread_id)
        return _payload(DECLINE_REPLY, [], draft=None, bpiq_collect=False)

    # What a partnership is worth in dollars is this order's to state
    # (prometheus.partnership_value, Jenna 2026-11-11): asked cold, the
    # order opens with what the words and the thread already name; asked
    # while the order is open, the open step is asked again. A deck of a
    # read that stated a partnership's dollar value leads here too.
    _pvk = pv_kind(t, history, pick=str((body or {}).get('_pv_pick') or ''), body=body)
    # An order for a valuation ("run a brand partnership valuation for MMG
    # x Lowe's ... here are the links", "can you value Gunna x Nike?") is
    # read whole by the one-shot reader below, as it always was; a
    # question about what a partnership is worth, and a deck of a read
    # that stated it, open here.
    if _pvk == 'deck' or (_pvk == 'value' and not is_valuation_order(t)):
        return _open_on_value(_pvk, t, history, draft, store, uname or user, thread_id, today)

    # Only Cancel (or Decline on the recap) leaves. A bare name or a
    # subject-shaped reply is the next answer; a question is not (the
    # guard below). The draft was resolved for this thread above.
    if not draft and not intent and not collecting:
        return None

    # The intake guard (Jenna 2026-11-05). 'Back to the valuation' or
    # 'back to it' re-asks the open step. A message that does not read
    # as the answer to that step (a question, an order on something
    # else) is not the wizard's: None, the draft stays at its step, and
    # the service routes the words normally with the way back as a chip.
    if draft and not intent and not is_chip(t):
        if _is_resume(t):
            return hold_payload(history, store, uname or user, thread_id, today)
        if not answers_step(t, draft, today=today):
            _stand_aside(draft)
            return None

    if not draft or (intent and is_chip(t)):
        draft = empty_draft()
        if not is_chip(t):
            draft = apply_ahead(draft, t, today=today)
        draft['step'] = next_step(draft)
        draft = _price_draft(draft)
        if store is not None:
            store.save(uname or user, thread_id, draft)
        reply, chips = step_prompt(draft, today=today)
        return _payload(reply, chips, draft=draft)

    if collecting or (draft and not intent) or (intent and not is_chip(t)):
        # Mid-wizard answer. A full dump at the first step still fills
        # ahead after the single-field apply, so they are not punished.
        loose = bool(draft) and not intent and loose_statement(t, draft, today=today)
        ahead = {} if loose else apply_ahead(
            empty_draft() if draft.get('step') == STEP_TALENT and not draft.get('talent') else dict(draft),
            t, today=today)
        # an open pairing ("which one is the talent") is answered by the
        # step itself: one of the two names, or a sentence that gives a role
        if not loose and draft.get('step') == STEP_TALENT and not draft.get('talent') and not draft.get('pair'):
            if ahead.get('talent') or ahead.get('campaign') or ahead.get('campaign_start'):
                for k, v in ahead.items():
                    if v in (None, '', [], 0):
                        continue
                    if k == 'step':
                        continue
                    if not draft.get(k):
                        draft[k] = v
                draft = _price_draft(draft)
                draft['step'] = next_step(draft)
                if store is not None:
                    store.save(uname or user, thread_id, draft)
                reply, chips = step_prompt(draft, today=today)
                if names_slots(t, STEP_TALENT, today=today) and draft.get('step') not in (STEP_ATTR, STEP_CHARGE):
                    ack = ack_line({'talent': draft.get('talent'),
                                    'brand': draft.get('brand_partner') if draft.get('brand_named') else ''})
                    if ack:
                        reply = f"{ack} {reply}"
                return _payload(reply, chips, draft=draft)
        same = None
        if not loose and draft.get('step') in (STEP_TALENT, STEP_CAMPAIGN) and _slots.says_same(t):
            # "same as before": the pairing of the last valuation this
            # thread ran, when there is one; never the words as a name
            marker = _run_marker(store, uname or user, thread_id) or {}
            if marker.get('talent') and marker.get('campaign'):
                same = dict(draft, talent=str(marker['talent']), campaign=str(marker['campaign']))
                same['brand_partner'] = _brand_from_campaign(same['campaign'], same['talent'])
                same.pop('misses', None)
                same = _price_draft(same)
                same['step'] = next_step(same)
        if same is not None:
            if store is not None:
                store.save(uname or user, thread_id, same)
            reply, chips = step_prompt(same, today=today)
            return _payload(f"Same as your last valuation here: {same['talent']}, {same['campaign']}. {reply}", chips, draft=same)
        if loose:
            # a remark, not an answer: nothing in it is kept, and the step
            # is asked again the way any reply that missed it is
            d2 = dict(draft)
            err = step_prompt(d2, today=today)[0]
            if d2.get('step') == STEP_ATTR:
                err = err + ATTR_NUDGE
        else:
            d2, err = apply_answer(draft, t, today=today)
        if err:
            # A reply that did not answer the step. The first one gets the
            # step again in other words; the second moves on with the
            # step's default, said in one plain line (2026-11-11). A step
            # with no default (a name, the control group) is asked again,
            # never in the same words twice in a row.
            step = d2.get('step') or next_step(d2)
            n = miss_count(draft, step) + 1
            if n >= 2:
                d3, line = default_for(step, d2, today=today)
                if d3 is not None:
                    if store is not None:
                        store.save(uname or user, thread_id, d3)
                    reply, chips = step_prompt(d3, today=today)
                    return _payload(f"{line}\n\n{reply}", chips, draft=d3)
            d2['misses'] = {'step': step, 'n': n}
            if store is not None:
                store.save(uname or user, thread_id, d2)
            return _payload(vary(err, step, history, d2), step_prompt(d2, today=today)[1], draft=d2)
        named = d2.pop('_named', None) or {}
        if store is not None:
            store.save(uname or user, thread_id, d2)
        reply, chips = step_prompt(d2, today=today)
        ack = ack_line(named)
        if ack and d2.get('step') not in (STEP_ATTR, STEP_CHARGE):
            reply = f"{ack} {reply}"       # the reader sees what the sentence was read as
        else:
            reply = vary(reply, d2.get('step'), history, d2)
        return _payload(reply, chips, draft=d2)

    if draft or last_agent_is_wizard(history) or collecting:
        return hold_payload(history, store, uname or user, thread_id, today)
    return None
