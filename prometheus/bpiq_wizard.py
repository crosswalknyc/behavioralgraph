"""Brand Partnership Valuation as a Prometheus wizard (2026-10-28).

Chip "Value a brand partnership" walks one question at a time: talent,
campaign, dates, standard attribution window, control group, assets,
then charge. The existing valuation job stays; this module is intake,
pricing, persist, and the accept/decline door.

User-facing copy is Prometheus first person. No em dashes. No synth,
modeled, pipeline, or model names.
"""
from __future__ import annotations

import re
import traceback
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

FLOW = 'brand_partnership'
WIZARD_FLOW = 'brand_partnership_wizard'
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
DECLINE_REPLY = "Okay, I won't run it."
CANCEL_REPLY = 'No problem. Ask me anything else when ready.'
RUNNING_REPLY = ("It's running. Keep talking here as usual. I'll email "
                 "you when it is done.")
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
_CANCEL_RE = re.compile(
    r'^\s*(never ?mind|not now|cancel( (that|it))?)\s*[.!?]?\s*$', re.I)
_DECLINE_RE = re.compile(r'^\s*decline\s*[.!?]?\s*$', re.I)
_YES_RE = re.compile(r'^\s*(yes|yeah|yep|y|sure|ok|okay)\s*[.!?]?\s*$', re.I)
_NO_RE = re.compile(r'^\s*(no|nope|nah|n)\s*[.!?]?\s*$', re.I)
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
    try:
        from prometheus.intake_reader import find_windows
        win = find_windows(t) or {}
        if win.get('event_start') and win.get('event_end'):
            return win['event_start'], win['event_end']
    except Exception:
        pass
    return None, None


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
    return PRICE_CONTROL if control_group else PRICE_BASE


def price_label(usd):
    return f"${int(usd):,}"


def accept_chip(usd):
    return f"Accept {price_label(usd)}"


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
    (ASK_TALENT, STEP_TALENT),
    ('Who is the property or talent', STEP_TALENT),
    (ASK_CAMPAIGN, STEP_CAMPAIGN),
    ('What campaign should I value', STEP_CAMPAIGN),
    (ASK_DATES, STEP_DATES),
    ('What are the campaign dates', STEP_DATES),
    ('I could not read those dates', STEP_DATES),
    ('I read the campaign as', STEP_ATTR),
    (ASK_ATTR_CUSTOM, STEP_ATTR_CUSTOM),
    ('What attribution window should I use', STEP_ATTR_CUSTOM),
    (ASK_CONTROL, STEP_CONTROL),
    ('Include a control group', STEP_CONTROL),
    (ASK_ASSETS, STEP_ASSETS),
    ('Do you have specific campaign links', STEP_ASSETS),
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


def last_agent_is_wizard(history):
    turn = last_agent_turn(history)
    if not turn:
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    if meta.get('bpiq_wizard') or turn.get('bpiq_wizard'):
        return True
    txt = str(turn.get('text') or turn.get('content') or '').strip()
    return step_from_prompt(txt) is not None


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
            d2, err = apply_answer(d, txt, today=today)
            if not err:
                d = d2
            pending = None
            saw = True
    if pending:
        d['step'] = pending
        saw = True
    return _price_draft(d) if saw else None


def wants(text, body=None, history=None, store=None, user=None,
          thread_id=''):
    """True when this turn belongs to the wizard: a start phrase, an
    armed body, a draft on the shared store, or the last agent turn
    already asked a wizard step."""
    if is_start(text):
        return True
    body = body or {}
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    if any((body.get(k) or extra.get(k)) for k in (
            'bpiq_wizard', 'bpiq_inputs', 'bpiq_confirm')):
        return True
    if last_agent_is_wizard(history):
        return True
    if store is None and user:
        try:
            store = shared_store()
        except Exception:
            store = None
    if store is not None:
        try:
            hit = store.load(user, thread_id or '')
            if isinstance(hit, dict) and hit.get('step'):
                return True
        except Exception:
            pass
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


def apply_ahead(draft, text, today=None):
    """Fill any fields the message already named. Never overwrites a
    field the wizard already has."""
    d = dict(draft or empty_draft())
    t = str(text or '').strip()
    if not t or is_chip(t):
        return d
    urls = extract_urls(t)
    if urls and not d.get('links'):
        d['links'] = urls
        d['asset_mode'] = d.get('asset_mode') or 'links'
    start, end = parse_dates(t, today=today)
    if start and end and not d.get('campaign_start'):
        d['campaign_start'], d['campaign_end'] = start, end
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
    bits = [b.strip(' .,:;') for b in re.split(r'\s*,\s*', rest) if b.strip(' .,:;')]
    names = [b for b in bits if b and not parse_dates(b, today=today)[0]]
    if names and not d.get('talent'):
        # "Jennifer Beals, Ganni x Lululemon" or a lone name
        if len(names) == 1 and re.search(r'\s+[xX\u00d7]\s+', names[0]):
            # a campaign dump with no separate talent
            if not d.get('campaign'):
                d['campaign'] = names[0]
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


def engine_inputs(draft):
    """The payload the existing valuation job already accepts."""
    d = dict(draft or {})
    talent = str(d.get('talent') or '').strip()
    campaign = str(d.get('campaign') or '').strip()
    brand = str(d.get('brand_partner') or '').strip() or _brand_from_campaign(
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
    return out


def recap_lines(draft):
    d = draft or {}
    control = bool(d.get('control_group'))
    usd = price_usd(control)
    assets = ("I'll find the assets" if d.get('asset_mode') != 'links'
              else ('Links: ' + ', '.join(d.get('links') or [])))
    lines = [
        f"{d.get('talent')}",
        f"{d.get('campaign')}",
        f"Campaign dates: {pretty_range(d['campaign_start'], d['campaign_end'])}",
        f"Attribution window: {pretty_range(d['attr_start'], d['attr_end'])}",
        f"Control group: {'yes' if control else 'no'}",
        assets,
        f"This is {price_label(usd)}.",
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
    return (f"I read the campaign as {camp}. {why} "
            f"Attribution window: {attr}.")


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
    reply, chips = step_prompt(draft, today=today)
    return _payload(reply, chips, draft=draft)


def step_prompt(draft, today=None):
    step = (draft or {}).get('step') or next_step(draft)
    if step == STEP_TALENT:
        return ASK_TALENT, [CHIP_CANCEL]
    if step == STEP_CAMPAIGN:
        return ASK_CAMPAIGN, [CHIP_CANCEL]
    if step == STEP_DATES:
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
    return recap_reply(draft), [accept_chip(usd), CHIP_DECLINE, CHIP_CANCEL]


def _clean_name(text):
    t = str(text or '').strip().strip('"\'').strip()
    t = re.sub(r'^(?:it(?:\'s| is)|the (?:talent|property|campaign) is)\s+',
               '', t, flags=re.I)
    return t.strip(' .')


def apply_answer(draft, text, today=None):
    """Advance one step from `text`. Returns (draft, err)."""
    d = dict(draft or empty_draft())
    step = d.get('step') or next_step(d)
    t = str(text or '').strip()
    today = _today(today)

    if step == STEP_TALENT:
        name = _clean_name(t)
        if not name or is_chip(name):
            return d, ASK_TALENT
        d['talent'] = name
    elif step == STEP_CAMPAIGN:
        name = _clean_name(t)
        if not name:
            return d, ASK_CAMPAIGN
        d['campaign'] = name
        d['brand_partner'] = _brand_from_campaign(name, d.get('talent'))
    elif step == STEP_DATES:
        start, end = parse_dates(t, today=today)
        if not (start and end):
            return d, ('I could not read those dates. Try something like '
                       'October 7 - 28.')
        d['campaign_start'], d['campaign_end'] = start, end
        d['attr_start'], d['attr_end'] = attribution_window(
            start, end, today=today)
        d['attr_ok'] = False
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
            if start and end:
                d['attr_start'], d['attr_end'] = start, end
                d['attr_ok'] = True
            elif not (_YES_RE.match(t) or low in ('accept', 'ok', 'okay')):
                return d, (attr_reply(d, today=today)
                           + ' Accept this window, or use a different one.')
            else:
                d['attr_ok'] = True
    elif step == STEP_ATTR_CUSTOM:
        start, end = parse_dates(t, today=today)
        if not (start and end):
            return d, ASK_ATTR_CUSTOM
        d['attr_start'], d['attr_end'] = start, end
        d['attr_ok'] = True
    elif step == STEP_CONTROL:
        if _YES_RE.match(t) or _low(t) == 'yes':
            d['control_group'] = True
        elif _NO_RE.match(t) or _low(t) == 'no':
            d['control_group'] = False
        else:
            return d, ASK_CONTROL
    elif step == STEP_ASSETS:
        if _PASTE_RE.search(t) or _YES_RE.match(t) or _low(t) == _low(CHIP_PASTE):
            d['asset_mode'] = 'links'
            urls = extract_urls(t)
            if urls:
                d['links'] = urls
        elif _FIND_RE.search(t) or _NO_RE.match(t) or _low(t) == _low(CHIP_FIND):
            d['asset_mode'] = 'find'
            d['links'] = []
        else:
            return d, ASK_ASSETS
    elif step == STEP_LINKS:
        urls = extract_urls(t)
        if not urls:
            return d, ASK_LINKS
        d['links'] = urls
        d['asset_mode'] = 'links'
    d = _price_draft(d)
    d['step'] = next_step(d)
    return d, None


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
        return self.rows.get(self._key(user, thread_id)) or self.rows.get(
            self._key(user, ''))

    def save(self, user, thread_id, draft):
        self.rows[self._key(user, thread_id)] = dict(draft or {})
        if thread_id:
            self.rows[self._key(user, '')] = dict(draft or {})
        return True

    def clear(self, user, thread_id=''):
        self.rows.pop(self._key(user, thread_id), None)
        self.rows.pop(self._key(user, ''), None)
        return True


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
                if isinstance(hit, dict) and hit.get('step'):
                    stash_tid = str(hit.get('thread_id') or '')
                    if thread_id and stash_tid and stash_tid != str(thread_id):
                        pass
                    else:
                        return hit
            except Exception:
                pass
            try:
                obj = s3.get_object(Bucket=bucket, Key=self._key(user, thread_id))
                import json
                doc = json.loads(obj['Body'].read().decode('utf-8'))
                return doc if isinstance(doc, dict) else None
            except Exception:
                return None

        def save(self, user, thread_id, draft):
            s3 = self._s3()
            bucket = self._bucket()
            doc = dict(draft or {}, thread_id=thread_id or '')
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
    return _S()


def _load_draft(store, user, thread_id, body, history, today=None):
    if isinstance((body or {}).get('bpiq_wizard'), dict):
        hit = dict(body['bpiq_wizard'])
        if hit.get('step'):
            return hit
    if store is not None:
        try:
            hit = store.load(user, thread_id)
            if isinstance(hit, dict) and hit.get('step'):
                return dict(hit)
        except Exception:
            pass
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


def _seat_is_free(user):
    """True for the seats other priced Prometheus steps never bill:
    super_admin / Crosswalk staff, or an unlimited flag / credits."""
    try:
        from prometheus.priced_read import staff_user
        if staff_user(user):
            return True
    except Exception:
        pass
    if not isinstance(user, dict):
        return False
    role = str(user.get('role') or '').strip().lower().replace(' ', '_')
    if role in ('super_admin', 'superadmin'):
        return True
    if bool(user.get('unlimited')):
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
    check."""
    if _seat_is_free(user):
        return True
    if charge_fn is not None:
        return bool(charge_fn(uname or user, inputs, job_id))
    host = (g or {}).get('_H') if g else None
    if host is None:
        try:
            from .host import host as host
        except Exception:
            host = None
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


def _start_valuation(job_id, uname, inputs, extras, g, start_fn):
    """Enqueue the valuation the same way the other engine jobs do."""
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
    try:
        rr.start_engine_job(
            'bpiq', job_id, uname, inputs, extras, status_write, runner)
        return True
    except Exception:
        traceback.print_exc()
        return False


def _charge_and_start(draft, user, uname, ppu, g, charge_fn, start_fn,
                      store=None, thread_id=''):
    inputs = engine_inputs(draft)
    if (draft or {}).get('charged') or (draft or {}).get('job_id'):
        return _payload(RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                        bpiq_job_id=(draft or {}).get('job_id'))
    try:
        import uuid
        job_id = uuid.uuid4().hex[:12]
    except Exception:
        job_id = 'bpiq'
    ok = _charge_seat(user, uname, inputs, job_id, g, charge_fn)
    if not ok:
        return _payload(FUNDS_REPLY, [], draft=draft, bpiq_collect=False)
    marked = dict(draft or empty_draft())
    marked['charged'] = True
    marked['job_id'] = job_id
    if store is not None:
        try:
            store.save(uname or user, thread_id, marked)
        except Exception:
            pass
    extras = ppu or {}
    _start_valuation(job_id, uname, inputs, extras, g, start_fn)
    if store is not None:
        try:
            store.clear(uname or user, thread_id)
        except Exception:
            pass
    return _payload(RUNNING_REPLY, [], draft=None, bpiq_collect=False,
                    bpiq_job_id=job_id)


def analyze_hook(text, body, history, user, ppu, uname, g,
                 first=None, today=None, store=None, charge_fn=None,
                 start_fn=None):
    """One wizard turn. Returns a chat payload, or None when this is
    not a Brand Partnership Valuation ask and no draft is open."""
    try:
        return _analyze_hook(
            text, body, history, user, ppu, uname, g,
            first=first, today=today, store=store,
            charge_fn=charge_fn, start_fn=start_fn)
    except Exception:
        traceback.print_exc()
        return None


def _analyze_hook(text, body, history, user, ppu, uname, g,
                  first=None, today=None, store=None, charge_fn=None,
                  start_fn=None):
    t = str(text or '').strip()
    thread_id = str((body or {}).get('thread_id') or '')
    uname = uname or _uname(user)
    today = _today(today)
    # A start phrase always opens step 1. Cancel any leftover draft
    # first so a prior subject on the thread cannot become the talent
    # or fall through to a library-comp read.
    if is_start(t):
        if store is None:
            store = shared_store(g)
        return _start_fresh(store, uname or user, thread_id, today)
    if store is None:
        store = shared_store(g)
    draft = _load_draft(store, uname or user, thread_id, body, history,
                        today=today)
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
    collecting = bool((body or {}).get('bpiq_inputs')) or bool(draft)

    if is_cancel(t) and (draft or collecting or intent):
        if store is not None:
            store.clear(uname or user, thread_id)
        return _payload(CANCEL_REPLY, [], draft=None, bpiq_collect=False)

    if draft and draft.get('step') == STEP_CHARGE and is_decline(t):
        if store is not None:
            store.clear(uname or user, thread_id)
        return _payload(DECLINE_REPLY, [], draft=None, bpiq_collect=False)

    prev_txt = str((last_agent_turn(history) or {}).get('text') or '')
    if prev_txt.startswith("It's running") and (
            _ACCEPT_RE.match(t) or _low(t).startswith('accept')):
        return _payload(RUNNING_REPLY, [], draft=None, bpiq_collect=False)

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
            })
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
            store=store, thread_id=thread_id)
        return hit

    if draft and draft.get('step') == STEP_CHARGE and (
            _ACCEPT_RE.match(t) or _low(t).startswith('accept')):
        hit = _charge_and_start(
            draft, user, uname, ppu, g, charge_fn, start_fn,
            store=store, thread_id=thread_id)
        return hit

    if draft and is_decline(t):
        if store is not None:
            store.clear(uname or user, thread_id)
        return _payload(DECLINE_REPLY, [], draft=None, bpiq_collect=False)

    # Only Cancel (or Decline on the recap) leaves. A bare name, a
    # new-looking ask, or a subject-shaped reply is the next answer.
    if not draft:
        draft = replay_history(history, today=today)
        if draft:
            collecting = True
    if not draft and last_agent_is_wizard(history):
        draft = empty_draft()
        step = step_from_prompt(
            str((last_agent_turn(history) or {}).get('text') or ''))
        draft['step'] = step or STEP_TALENT
        collecting = True
    if not draft and not intent and not collecting:
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
        ahead = apply_ahead(empty_draft() if draft.get('step') == STEP_TALENT
                            and not draft.get('talent') else dict(draft),
                            t, today=today)
        if draft.get('step') == STEP_TALENT and not draft.get('talent'):
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
                return _payload(reply, chips, draft=draft)
        d2, err = apply_answer(draft, t, today=today)
        if err:
            return _payload(err, step_prompt(d2, today=today)[1], draft=d2)
        if store is not None:
            store.save(uname or user, thread_id, d2)
        reply, chips = step_prompt(d2, today=today)
        return _payload(reply, chips, draft=d2)

    if draft or last_agent_is_wizard(history) or collecting:
        return hold_payload(history, store, uname or user, thread_id, today)
    return None
