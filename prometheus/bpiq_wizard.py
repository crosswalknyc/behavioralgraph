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


def wants(text, body=None, history=None):
    """True when this turn belongs to the wizard: a start phrase, an
    armed body, or the last agent turn already asked a wizard step."""
    if is_start(text):
        return True
    body = body or {}
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    if any((body.get(k) or extra.get(k)) for k in (
            'bpiq_wizard', 'bpiq_inputs', 'bpiq_confirm')):
        return True
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict):
            continue
        if str(turn.get('role') or '') not in ('agent', 'assistant'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        if meta.get('bpiq_wizard') or turn.get('bpiq_wizard'):
            return True
        txt = str(turn.get('text') or turn.get('content') or '').strip()
        if txt == ASK_TALENT or txt.startswith('Who is the property or talent'):
            return True
        break
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
    return out


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


def _safe_store(g):
    """Best-effort draft store. A host miss still starts the wizard."""
    try:
        from .host import host as _host
        if _host.has('s3_client'):
            return _s3_store(_host)
    except Exception:
        pass
    try:
        if g:
            return _s3_store(type('H', (), {
                's3_client': getattr(g.get('_H'), 's3_client', None),
                'S3_BUCKET': getattr(g.get('_H'), 'S3_BUCKET',
                                    'dashboard-inputs'),
            })())
    except Exception:
        pass
    return MemoryStore()


def _s3_store(host):
    """Draft store on the thread (and a per-user peek) so a refresh
    does not lose the in-progress valuation."""
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

        def load(self, user, thread_id=''):
            s3 = getattr(host, 's3_client', None)
            bucket = getattr(host, 'S3_BUCKET', None) or getattr(
                host, 'bucket', None) or 'dashboard-inputs'
            if s3 is None:
                return None
            try:
                from prometheus import pending_intake as pi
                hit = pi.peek(s3, bucket, user, WIZARD_FLOW)
                if isinstance(hit, dict) and hit.get('step'):
                    if thread_id and hit.get('thread_id') not in (
                            None, '', thread_id):
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
            s3 = getattr(host, 's3_client', None)
            bucket = getattr(host, 'S3_BUCKET', None) or getattr(
                host, 'bucket', None) or 'dashboard-inputs'
            doc = dict(draft or {}, thread_id=thread_id or '')
            if s3 is None:
                return False
            try:
                from prometheus import pending_intake as pi
                pi.stash(s3, bucket, user, WIZARD_FLOW, doc, run_label=CHIP)
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
                return False

        def clear(self, user, thread_id=''):
            s3 = getattr(host, 's3_client', None)
            bucket = getattr(host, 'S3_BUCKET', None) or getattr(
                host, 'bucket', None) or 'dashboard-inputs'
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


def _load_draft(store, user, thread_id, body, history):
    if isinstance((body or {}).get('bpiq_wizard'), dict):
        return dict(body['bpiq_wizard'])
    if store is not None:
        hit = store.load(user, thread_id)
        if isinstance(hit, dict) and hit.get('step'):
            return dict(hit)
    for turn in reversed(list(history or [])):
        if str(turn.get('role') or '') not in ('agent', 'assistant'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        wiz = meta.get('bpiq_wizard') or turn.get('bpiq_wizard')
        if isinstance(wiz, dict) and wiz.get('step'):
            return dict(wiz)
    return None


def _uname(user):
    if isinstance(user, dict):
        return str(user.get('username') or user.get('email') or '').strip()
    return str(user or '').strip()


def _charge_and_start(draft, user, uname, ppu, g, charge_fn, start_fn):
    inputs = engine_inputs(draft)
    usd = int(inputs['price'])
    control = bool(inputs.get('control_group'))
    job_id = None
    try:
        import uuid
        job_id = uuid.uuid4().hex[:12]
    except Exception:
        job_id = 'bpiq'
    subj = f"{inputs.get('qualifier')} x {inputs.get('campaign') or inputs.get('brand_partner')}"
    pull = PULL_CONTROL if control else PULL_BASE
    credits = CREDITS_CONTROL if control else CREDITS_BASE
    desc = f'Brand Partnership Valuation: {subj}'
    if charge_fn is not None:
        ok = charge_fn(uname or user, inputs, job_id)
    else:
        ok = True
        host = None
        if g:
            host = g.get('_H')
        if host is None:
            try:
                from .host import host as host
            except Exception:
                host = None
        consume = getattr(host, 'consume_credit', None) if host is not None else None
        if consume:
            try:
                ok = bool(consume(
                    uname,
                    description=desc,
                    pull_type=pull,
                    credits_used=credits,
                    job_id=job_id))
            except Exception:
                traceback.print_exc()
                ok = False
    if not ok:
        return _payload(FUNDS_REPLY, [], draft=draft, bpiq_collect=False)
    extras = ppu or {}
    if start_fn is not None:
        start_fn(job_id, uname, inputs, extras)
    elif g:
        try:
            rr = g.get('_rr')
            if rr is None:
                from prometheus import read_recovery as rr
            rr.start_engine_job(
                'bpiq', job_id, uname, inputs, extras,
                g.get('_pm_bpiq_status_write'), g.get('_pm_run_bpiq_job'))
        except Exception:
            traceback.print_exc()
            return _payload(FUNDS_REPLY, [], draft=draft, bpiq_collect=False)
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
            store = _safe_store(g)
        return _start_fresh(store, uname or user, thread_id, today)
    from .host import host as _host
    if store is None:
        store = _s3_store(_host if _host.has('s3_client') else type('H', (), {
            's3_client': getattr(g.get('_H'), 's3_client', None) if g else None,
            'S3_BUCKET': getattr(g.get('_H'), 'S3_BUCKET', 'dashboard-inputs') if g else 'dashboard-inputs',
        })())
    draft = _load_draft(store, uname or user, thread_id, body, history)
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
        hit = _charge_and_start(draft, user, uname, ppu, g, charge_fn, start_fn)
        if store is not None and (hit or {}).get('bpiq_job_id'):
            store.clear(uname or user, thread_id)
        return hit

    if draft and draft.get('step') == STEP_CHARGE and (
            _ACCEPT_RE.match(t) or _low(t).startswith('accept')):
        hit = _charge_and_start(draft, user, uname, ppu, g, charge_fn, start_fn)
        if store is not None and (hit or {}).get('bpiq_job_id'):
            store.clear(uname or user, thread_id)
        return hit

    if draft and is_decline(t):
        if store is not None:
            store.clear(uname or user, thread_id)
        return _payload(DECLINE_REPLY, [], draft=None, bpiq_collect=False)

    # A new product ask while a draft is open leaves the wizard.
    if draft and t and not collecting and not intent:
        try:
            from prometheus.intake_reader import is_new_ask
            if is_new_ask(t) and not is_chip(t):
                return None
        except Exception:
            pass

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

    return None
