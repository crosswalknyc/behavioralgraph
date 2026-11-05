"""The Flywheel wizard: three questions, then the build.

Jenna, 2026-11-03 (Grace Yin, Amazon, tried a flywheel for Gilmore Girls
on Prime Video and was told the feature hit a bug): "when someone wants
to do a flywheel it should be a wizard that asks them the flywheel's
entry point. that could be anything. so she would say watched gilmore
girls on APV. then you should ask what ecosystem should the flywheel
examine? the user would enter 'Amazon' then it would ask for the date
range. they would then enter date range then it would come back and
give penetration levels for all amazon owned companies before they
watched gilmore girls and after they watched gilmore girls."

Before this the intake asked for four things in one message (the title,
a "captured action", the ecosystem and a "conversion inside that
ecosystem") with a dense example; Grace pasted the example. Now:

  1. the entry point ("watched Gilmore Girls on Prime Video")
  2. the ecosystem ("Amazon")
  3. the date range (trailing 12 months is the default)

then one confirm card with the price, and "Run the flywheel" starts the
build the existing run branch owns. A message that carries more than one
answer fills ahead; the wizard asks only for what is still missing. The
draft lives in the shared pending-intake stash so a reload, a second tab
or a reply typed later picks up where it stopped.

The engine inputs the card carries are the ones `flywheel_synthesis`
reads (subject, captured_action, ecosystem, conversion_event, windows),
derived from the three answers.
"""
from __future__ import annotations

import re
import traceback
from datetime import date, timedelta

FLOW = 'flywheel'
WIZARD_FLOW = 'flywheel_wizard'
CHIP = 'Build a Flywheel'
RUN_CHIP = 'Run the flywheel'

STEP_ENTRY = 'entry'
STEP_ECOSYSTEM = 'ecosystem'
STEP_DATES = 'dates'
STEP_EXISTS = 'exists'     # a study for this entry and ecosystem is already on the page (2026-11-03)
STEP_CONFIRM = 'confirm'
REBUILD_CHIP = 'Build it again'
OPEN_LABEL = 'Open it on Flywheel IQ'

ASK_ENTRY = ("Happy to build a Flywheel. What is the entry point? The thing people did that starts it, in one "
             "line: for example, watched Gilmore Girls on Prime Video, or bought a Ring doorbell on Amazon.")
ASK_ECOSYSTEM = ("Which ecosystem should the flywheel examine? Name the company whose owned services and products I "
                 "measure before and after the entry: Amazon, Disney, Apple, Google, Walmart, Paramount, NBCUniversal, "
                 "Warner Bros. Discovery, Netflix, or another.")
ASK_DATES = ("What date range? The entry events come from this window, and each person is measured over the 30 days "
             "before their entry against the 30 days after it. Trailing 12 months is the default.")
ASK_ECOSYSTEM_OTHER = ("Name the company whose ecosystem I should examine, for example Amazon, Disney, Apple, Google, "
                       "Walmart, Paramount, NBCUniversal, Warner Bros. Discovery or Netflix.")
CANCEL_REPLY = 'No problem. Nothing is queued and nothing was charged. Ask me anything else when ready.'
DATE_CHIPS = ('Trailing 12 months', 'Calendar 2025', 'Other dates')
ECO_CHIPS = ('Amazon', 'Disney', 'Apple', 'Google')
OTHER_CHIP = 'Other'
OTHER_DATES_REPLY = 'Give me the start and end, for example March 1, 2026 to August 31, 2026.'
PRE_DAYS = 30
POST_DAYS = 30

_ENTRY_VERBS = ('watched', 'streamed', 'played', 'listened to', 'read', 'bought', 'purchased', 'ordered', 'signed up for',
                'subscribed to', 'joined', 'downloaded', 'installed', 'visited', 'rented', 'binged', 'finished', 'started',
                'opened', 'tried', 'used', 'searched for', 'followed', 'pre-ordered', 'preordered', 'saw', 'attended')
_ENTRY_RX = re.compile(
    r"(?:^|\b(?:people|users|accounts|viewers|readers|listeners|anyone|those|everyone|customers|members|subscribers) who\b|"
    r"\bentry(?: point)?(?: is|:)\s*|\bstart(?:ing)?(?: point| from)?(?: is|:)?\s*)\s*"
    r"(?P<verb>" + '|'.join(re.escape(v) for v in sorted(_ENTRY_VERBS, key=len, reverse=True)) + r")\s+"
    r"(?P<obj>.+?)(?=\s*(?:[,;.!]|\bin the (?:trailing|last|past)\b|\bfrom\s+\w+\s+\d|\bbetween\b|\becosystem\b|\bacross\b|$))", re.I)
_ON_RX = re.compile(r"^(?P<title>.+?)\s+(?:on|via|through|from|at)\s+(?P<plat>[A-Za-z][\w+ .&'/-]{1,40})$", re.I)

# the services a person enters through, the company that owns them, the display spelling
_PLATFORMS = {
    'prime video': ('Prime Video', 'Amazon'), 'amazon prime video': ('Prime Video', 'Amazon'), 'apv': ('Prime Video', 'Amazon'),
    'pv': ('Prime Video', 'Amazon'), 'prime': ('Prime Video', 'Amazon'), 'amazon prime': ('Prime Video', 'Amazon'),
    'amazon': ('Amazon', 'Amazon'), 'amazon.com': ('Amazon', 'Amazon'), 'audible': ('Audible', 'Amazon'), 'kindle': ('Kindle', 'Amazon'),
    'twitch': ('Twitch', 'Amazon'), 'amazon music': ('Amazon Music', 'Amazon'), 'whole foods': ('Whole Foods', 'Amazon'),
    'freevee': ('Prime Video', 'Amazon'), 'mgm+': ('MGM+', 'Amazon'),
    'netflix': ('Netflix', 'Netflix'),
    'hulu': ('Hulu', 'Disney'), 'disney+': ('Disney+', 'Disney'), 'disney plus': ('Disney+', 'Disney'), 'espn+': ('ESPN+', 'Disney'), 'espn': ('ESPN', 'Disney'),
    'abc': ('ABC', 'Disney'), 'fx': ('FX', 'Disney'),
    'peacock': ('Peacock', 'NBCUniversal'), 'nbc': ('NBC', 'NBCUniversal'), 'bravo': ('Bravo', 'NBCUniversal'),
    'max': ('HBO Max', 'Warner Bros. Discovery'), 'hbo max': ('HBO Max', 'Warner Bros. Discovery'), 'hbo': ('HBO Max', 'Warner Bros. Discovery'),
    'discovery+': ('Discovery+', 'Warner Bros. Discovery'), 'cnn': ('CNN', 'Warner Bros. Discovery'),
    'paramount+': ('Paramount+', 'Paramount'), 'paramount plus': ('Paramount+', 'Paramount'), 'pluto tv': ('Pluto TV', 'Paramount'), 'pluto': ('Pluto TV', 'Paramount'),
    'cbs': ('CBS', 'Paramount'), 'showtime': ('Paramount+', 'Paramount'),
    'apple tv+': ('Apple TV+', 'Apple'), 'apple tv plus': ('Apple TV+', 'Apple'), 'apple tv': ('Apple TV+', 'Apple'), 'apple music': ('Apple Music', 'Apple'),
    'apple': ('Apple', 'Apple'), 'app store': ('App Store', 'Apple'),
    'youtube': ('YouTube', 'Google'), 'youtube tv': ('YouTube TV', 'Google'), 'youtube premium': ('YouTube Premium', 'Google'), 'google': ('Google', 'Google'),
    'google play': ('Google Play', 'Google'),
    'spotify': ('Spotify', 'Spotify'), 'tubi': ('Tubi', 'Fox'), 'fox': ('Fox', 'Fox'), 'roku': ('Roku', 'Roku'), 'roku channel': ('The Roku Channel', 'Roku'),
    'walmart': ('Walmart', 'Walmart'), 'walmart+': ('Walmart+', 'Walmart'), 'sam\'s club': ("Sam's Club", 'Walmart'), 'vizio': ('Vizio', 'Walmart'),
    'target': ('Target', 'Target'), 'costco': ('Costco', 'Costco'), 'instagram': ('Instagram', 'Meta'), 'facebook': ('Facebook', 'Meta'),
    'threads': ('Threads', 'Meta'), 'whatsapp': ('WhatsApp', 'Meta'), 'tiktok': ('TikTok', 'ByteDance'), 'tiktok shop': ('TikTok Shop', 'ByteDance'),
    'xbox': ('Xbox', 'Microsoft'), 'game pass': ('Xbox Game Pass', 'Microsoft'), 'linkedin': ('LinkedIn', 'Microsoft'),
    'playstation': ('PlayStation', 'Sony'), 'crunchyroll': ('Crunchyroll', 'Sony'), 'starz': ('Starz', 'Starz'), 'amc+': ('AMC+', 'AMC Networks'),
    'nintendo': ('Nintendo', 'Nintendo'), 'steam': ('Steam', 'Valve'), 'uber': ('Uber', 'Uber'), 'uber eats': ('Uber Eats', 'Uber'), 'doordash': ('DoorDash', 'DoorDash'),
}
_ECOSYSTEMS = {
    'amazon': 'Amazon', 'amazon-owned': 'Amazon', 'amazon owned': 'Amazon', 'apv': 'Amazon', 'prime': 'Amazon', 'amzn': 'Amazon',
    'disney': 'Disney', 'the walt disney company': 'Disney', 'walt disney': 'Disney',
    'apple': 'Apple', 'google': 'Google', 'alphabet': 'Google', 'youtube': 'Google',
    'walmart': 'Walmart', 'target': 'Target', 'costco': 'Costco',
    'paramount': 'Paramount', 'paramount skydance': 'Paramount', 'paramount global': 'Paramount', 'skydance': 'Paramount',
    'nbcuniversal': 'NBCUniversal', 'nbcu': 'NBCUniversal', 'nbc universal': 'NBCUniversal', 'comcast': 'NBCUniversal', 'versant': 'NBCUniversal',
    'warner bros. discovery': 'Warner Bros. Discovery', 'warner bros discovery': 'Warner Bros. Discovery', 'wbd': 'Warner Bros. Discovery',
    'warner': 'Warner Bros. Discovery', 'warner bros': 'Warner Bros. Discovery', 'hbo': 'Warner Bros. Discovery',
    'netflix': 'Netflix', 'meta': 'Meta', 'facebook': 'Meta', 'microsoft': 'Microsoft', 'sony': 'Sony', 'spotify': 'Spotify',
    'roku': 'Roku', 'fox': 'Fox', 'bytedance': 'ByteDance', 'tiktok': 'ByteDance', 'nintendo': 'Nintendo', 'uber': 'Uber',
    'starbucks': 'Starbucks', 'nike': 'Nike', 'lvmh': 'LVMH', 'kering': 'Kering', 'l\'oreal': "L'Oreal", 'unilever': 'Unilever', 'pepsico': 'PepsiCo',
}
_ECO_FAMILY = {
    'Amazon': 'every Amazon-owned service and product with a digital surface: Amazon.com, Prime Video, Amazon Music, Audible, Kindle, '
              'Whole Foods, Amazon Fresh, Alexa, Ring, Fire TV, Twitch, IMDb, Amazon Pharmacy, Amazon Photos, Prime Gaming, Zappos, '
              'Goodreads, Wondery, Luna, Blink, eero, One Medical, MGM+ and the rest',
    'Disney': 'every Disney-owned service and product with a digital surface: Disney+, Hulu, ESPN, ABC, FX, National Geographic, '
              'Marvel, Star Wars, Pixar, shopDisney, Disney Parks and resorts, Disney Cruise Line and the rest',
    'Apple': 'every Apple service: Apple TV+, Apple Music, Apple Arcade, Apple News+, Fitness+, iCloud+, Apple One, the App Store, Apple Pay and the rest',
    'Google': 'every Google service: YouTube, YouTube TV, YouTube Premium, YouTube Music, Google Play, Pixel, Nest, Fitbit, Google One and the rest',
    'Walmart': 'every Walmart-owned surface: Walmart.com, Walmart+, Sam\'s Club, Vizio, Walmart Connect and the rest',
    'Paramount': 'every Paramount-owned service: Paramount+, Pluto TV, CBS, MTV, Nickelodeon, Comedy Central, BET, Showtime and the rest',
    'NBCUniversal': 'every NBCUniversal-owned service: Peacock, NBC, Bravo, Telemundo, Universal Pictures, Fandango, Universal Parks and the rest',
    'Warner Bros. Discovery': 'every Warner Bros. Discovery service: HBO Max, Discovery+, CNN, TNT, TBS, Food Network, HGTV, Warner Bros. Games and the rest',
    'Netflix': 'every Netflix surface: the service, Netflix Games, Netflix House, Tudum and the shop',
    'Meta': 'every Meta app and product: Facebook, Instagram, Threads, WhatsApp, Messenger, Meta Quest, Ray-Ban Meta',
    'Microsoft': 'every Microsoft consumer surface: Xbox, Game Pass, Microsoft 365, LinkedIn, Bing, Copilot, Surface',
    'Sony': 'every Sony consumer surface: PlayStation, PlayStation Plus, Crunchyroll, Sony Pictures, Sony Music, Sony electronics',
}

_CANCEL_RX = re.compile(r"^\s*(?:never ?mind|no(?: thanks?)?|not now|cancel(?: (?:that|it|this))?|stop|forget it|quit)\s*[.!]?\s*$", re.I)
_TRAILING_RX = re.compile(r"\b(?:trailing|last|past|previous)\s+(?:12|twelve)\s+months?\b|\bt12\b|\blast year\b|\bdefault\b|\btrailing year\b", re.I)
_CAL_YEAR_RX = re.compile(r"\b(?:calendar\s+)?(20\d{2})\b(?!\s*[-/]\s*\d)", re.I)
_OTHER_RX = re.compile(r"^\s*other(?: dates?)?\s*[.!]?\s*$", re.I)
# "Other" at the ecosystem step is a request to name one, never a company called Other
_OTHER_ECO_RX = re.compile(r"^\s*(?:other|another|an ?other one|a different one|different|something else|none of (?:these|those|them)|"
                           r"not (?:these|those)|(?:an?\s+)?(?:other|another|different) (?:ecosystem|company|one))\s*[.!]?\s*$", re.I)
# at the exists step: build anyway, or open the one that is there
_REBUILD_RX = re.compile(r"^\s*(?:build (?:it |this |that )?(?:again|anyway|this window|a new one|fresh)|build this window|rebuild(?: it)?|run it anyway|again|yes,? build(?: it)?(?: again)?|new one|build another)\s*[.!]?\s*$", re.I)
_OPEN_RX = re.compile(r"^\s*(?:open|view|show|see|look at|take me to)\b.*$", re.I)
# a bare yes at the ecosystem step takes the company the entry already pointed at
_YES_RX = re.compile(r"^\s*(?:yes|yep|yeah|sure|ok(?:ay)?|correct|right|fine|that works|sounds good|do it|go with (?:it|that)|use (?:it|that)|that one)\s*[.!]?\s*$", re.I)


def _low(t):
    return ' '.join(str(t or '').lower().split())


def _today(today=None):
    if isinstance(today, date):
        return today
    if today:
        return date.fromisoformat(str(today)[:10])
    return date.today()


def is_cancel(text):
    return bool(_CANCEL_RX.match(str(text or '')))


def is_start(text):
    """The chip, or a build-verb flywheel ask ('run a flywheel for Gilmore Girls on PV')."""
    low = _low(text)
    if not low:
        return False
    if low == CHIP.lower() or low in ('flywheel', 'a flywheel', 'new flywheel', 'build flywheel', 'flywheel iq'):
        return True
    try:
        from . import intents as _intents
        return bool(_intents.fw_intent(low, CHIP))
    except Exception:
        return 'flywheel' in low and any(k in low for k in ('pull', 'build', 'run', 'create', 'new', 'make', 'do a', 'try a'))


def empty_draft():
    return {'step': STEP_ENTRY, 'entry_point': '', 'subject': '', 'platform': '', 'ecosystem': '',
            'start_date': None, 'end_date': None, 'window_label': ''}


# ------------------------------------------------------------------ parsing
def normalize_platform(name):
    """('Prime Video', 'Amazon') for 'APV'; (as written, '') when unknown."""
    key = _low(name).strip(' .,;')
    if key in _PLATFORMS:
        return _PLATFORMS[key]
    for k in sorted(_PLATFORMS, key=len, reverse=True):
        if key == k or key.endswith(' ' + k):
            return _PLATFORMS[k]
    return (str(name or '').strip(), '')


def normalize_ecosystem(name):
    key = _low(name).strip(' .,;:!').rstrip('s') if _low(name).endswith('ecosystems') else _low(name).strip(' .,;:!')
    key = re.sub(r"^(?:the\s+)?", '', key)
    key = re.sub(r"\s*(?:ecosystem|owned surfaces?|owned|family|universe|companies|company|-owned)\s*$", '', key).strip()
    key = re.sub(r"^(?:the\s+)?", '', key)
    if key in _ECOSYSTEMS:
        return _ECOSYSTEMS[key]
    plat, owner = normalize_platform(key)
    if owner:
        return owner
    return ''


def parse_entry(text):
    """{'entry_point', 'verb', 'subject', 'platform', 'owner'} from 'watched Gilmore Girls on APV', or {}."""
    t = ' '.join(str(text or '').split()).strip(' .!?"\'')
    if not t:
        return {}
    m = _ENTRY_RX.search(t)
    if not m:
        # "Gilmore Girls on Prime Video" with no verb: a title on a service is a watch (the first clause
        # when the message goes on: "Gilmore Girls on PV, Amazon, trailing 12 months")
        cands = [t] + [c.strip(' .,;') for c in re.split(r"[,;]", t) if c.strip()]
        cands += [c.strip(' .,;') for c in re.split(r"\s+(?:for|in|during|across|within|inside|over)\s+(?:the\s+)?", t, flags=re.I)[:1] if c.strip()]
        for cand in cands:
            mo = _ON_RX.match(cand)
            if mo and re.search(r"\bflywheel\b", mo.group('title'), re.I):
                continue   # the ask's own framing is never the title
            if mo and normalize_platform(mo.group('plat'))[1]:
                disp, owner = normalize_platform(mo.group('plat'))
                title = _cased(mo.group('title').strip(' .,;'))
                return {'entry_point': f"watched {title} on {disp}", 'verb': 'watched', 'subject': title, 'platform': disp, 'owner': owner}
        return {}
    verb = m.group('verb').lower()
    obj = m.group('obj').strip(' .,;')
    obj = re.sub(r"\s+(?:in|during|over)\s+(?:the\s+)?(?:trailing|last|past)\b.*$", '', obj, flags=re.I).strip()
    subject, plat, owner = obj, '', ''
    mo = _ON_RX.match(obj)
    if mo:
        disp, own = normalize_platform(mo.group('plat'))
        if own or len(mo.group('plat').split()) <= 3:
            subject, plat, owner = mo.group('title').strip(' .,;'), disp, own
    else:
        # "bought a Ring doorbell on Amazon" handled above; "signed up for Peacock" names the service itself
        disp, own = normalize_platform(obj)
        if own:
            subject, plat, owner = disp, disp, own
    subject = _cased(subject)
    entry = f"{verb} {subject}" + (f" on {plat}" if plat and plat != subject else '')
    return {'entry_point': entry, 'verb': verb, 'subject': subject, 'platform': plat, 'owner': owner}


def _cased(name):
    """A title typed in lower or sentence case reads in title case ('gilmore girls', 'Gilmore girls' -> 'Gilmore
    Girls'); a name with its own inner capitals (iCarly, SpongeBob, MGM+) stays as typed."""
    s = str(name or '').strip()
    words = s.split()
    if not words or any(any(ch.isupper() for ch in w[1:]) for w in words):
        return s
    if words[0][:1].islower() and any(w[:1].isupper() for w in words[1:]):
        return s   # "a Ring doorbell": the person already cased the name inside the phrase
    small = {'a', 'an', 'the', 'of', 'and', 'or', 'on', 'in', 'to', 'for', 'at', 'by', 'vs'}
    return ' '.join(w if (i and w in small) else (w[:1].upper() + w[1:]) for i, w in enumerate(words))


def parse_window(text, today=None):
    """(start, end, label) or (None, None, '')."""
    today = _today(today)
    t = str(text or '').strip()
    if not t:
        return None, None, ''
    if _TRAILING_RX.search(t) or _low(t) in ('trailing 12 months', 'last 12 months', 'ttm', 'the default'):
        return (today - timedelta(days=365)).isoformat(), today.isoformat(), 'Trailing 12 months'
    try:
        from .bpiq_wizard import parse_dates, pretty_range
        a, b = parse_dates(t, today=today)
        if a and b:
            return a, b, pretty_range(a, b)
    except Exception:
        pass
    m = _CAL_YEAR_RX.search(t)
    if m and re.search(r"\b(?:calendar|year|all of|in)\b", t, re.I) or (m and _low(t) == m.group(1)):
        y = int(m.group(1))
        end = date(y, 12, 31)
        if end > today:
            end = today
        return date(y, 1, 1).isoformat(), end.isoformat(), f'Calendar {y}'
    return None, None, ''


def apply_ahead(draft, text, today=None):
    """Everything one message carries, into the draft."""
    d = dict(draft or empty_draft())
    t = ' '.join(str(text or '').split())
    if not t or _low(t) == CHIP.lower():
        return d
    # everything up to the flywheel ask itself is framing ("I tried to run a flywheel for ...", "can you build a
    # flywheel on ..."); what follows is the entry point and whatever else the message carries
    body = re.sub(r"^.*?\bflywheel\s*(?:iq|analysis|study|read)?\s*(?:for|on|of|about|around|:)?\s*", '', t, flags=re.I) if re.search(r"\bflywheel\b", t, re.I) else t
    body = re.sub(r"^(?:the\s+)?(?:people|users|accounts|viewers|those|everyone|customers)\s+who\s+", '', body, flags=re.I)
    if not d.get('entry_point'):
        e = parse_entry(body)
        if e:
            d.update({'entry_point': e['entry_point'], 'subject': e['subject'], 'platform': e['platform']})
            if e.get('owner') and not d.get('ecosystem_hint'):
                d['ecosystem_hint'] = e['owner']
    if not d.get('ecosystem'):
        m = re.search(r"\b(?:the\s+)?([A-Z][\w.&'+-]*(?:\s+[A-Z][\w.&'+-]*){0,3})\s+(?:ecosystem|family|universe)\b", body)   # "the Amazon ecosystem"
        if not (m and normalize_ecosystem(m.group(1))):
            m = re.search(r"\b(?:ecosystem|examine|across|inside|within|look at)\s*(?:is|:|should be)?\s*(?:the\s+)?([A-Za-z][\w.&' +-]{1,40}?)(?:[,.;]|\s+(?:ecosystem|owned|from|between|in the|over|for)\b|$)", body, re.I)
        if m and normalize_ecosystem(m.group(1)):
            d['ecosystem'] = normalize_ecosystem(m.group(1))
        elif d.get('entry_point'):
            # the ecosystem named outright after the entry ("..., Amazon, trailing 12 months"; "Disney ecosystem")
            for part in re.split(r"[,;]| and ", body)[1:]:
                part = part.strip()
                if not part or len(part.split()) > 4:
                    continue
                eco = normalize_ecosystem(part)
                if eco:
                    d['ecosystem'] = eco
                    break
    if not d.get('start_date'):
        a, b, lab = parse_window(body, today=today)
        if a and b:
            d.update({'start_date': a, 'end_date': b, 'window_label': lab})
    d['step'] = next_step(d)
    return d


def next_step(d):
    if not d.get('entry_point'):
        return STEP_ENTRY
    if not d.get('ecosystem'):
        return STEP_ECOSYSTEM
    if not d.get('start_date'):
        return STEP_DATES
    return STEP_CONFIRM


def apply_answer(draft, text, today=None):
    """One step's answer. (draft, error) where error is a plain line to
    show when the answer could not be read."""
    d = dict(draft or empty_draft())
    step = d.get('step') or next_step(d)
    t = ' '.join(str(text or '').split())
    if step == STEP_ENTRY:
        e = parse_entry(t)
        if not e:
            # anything with a title on a service, or a plain phrase, still stands as the entry point
            if len(t.split()) >= 2 and not is_start(t):
                e = {'entry_point': t.strip(' .'), 'subject': t.strip(' .'), 'platform': '', 'owner': ''}
            else:
                return d, 'Tell me the entry point in one line, like: watched Gilmore Girls on Prime Video.'
        d.update({'entry_point': e['entry_point'], 'subject': e['subject'], 'platform': e['platform']})
        if e.get('owner'):
            d['ecosystem_hint'] = e['owner']
        # the same message may carry the rest
        d = apply_ahead(d, t, today=today) if not d.get('ecosystem') or not d.get('start_date') else d
    elif step == STEP_ECOSYSTEM:
        if _OTHER_ECO_RX.match(t):
            return d, ASK_ECOSYSTEM_OTHER
        if _YES_RX.match(t) and d.get('ecosystem_hint'):
            t = str(d['ecosystem_hint'])
        eco = normalize_ecosystem(t)
        if not eco:
            if 1 <= len(t.split()) <= 4 and re.match(r"^[A-Za-z][\w.&' +-]+$", t):
                eco = t.strip().title() if t.islower() else t.strip()
            else:
                return d, 'Name the company whose ecosystem I should examine, like Amazon or Disney.'
        d['ecosystem'] = eco
        if not d.get('start_date'):
            a, b, lab = parse_window(t, today=today)
            if a and b:
                d.update({'start_date': a, 'end_date': b, 'window_label': lab})
    elif step == STEP_DATES:
        if _OTHER_RX.match(t):
            return d, OTHER_DATES_REPLY
        a, b, lab = parse_window(t, today=today)
        if not (a and b):
            return d, 'Give me the start and end, for example March 1, 2026 to August 31, 2026, or say trailing 12 months.'
        d.update({'start_date': a, 'end_date': b, 'window_label': lab})
    d['step'] = next_step(d)
    return d, ''


# ---------------------------------------------------------------- copy
def engine_inputs(d):
    """The inputs the flywheel engine reads, from the three answers."""
    eco = str(d.get('ecosystem') or '').strip()
    entry = str(d.get('entry_point') or '').strip()
    subject = str(d.get('subject') or entry).strip()
    plat = str(d.get('platform') or '').strip()
    return {
        'subject': subject, 'platform': plat, 'entry_point': entry,
        'captured_action': entry, 'cohort_count': None, 'ecosystem': eco,
        'conversion_event': f"any {eco}-owned checkout, sign-up or first use inside {POST_DAYS} days of the entry",
        'pre_days': PRE_DAYS, 'post_days': POST_DAYS, 'splits_hint': None,
        'start_date': d.get('start_date'), 'end_date': d.get('end_date'),
        'notes': (f"Entry point: {entry}. Ecosystem: {eco}: {_ECO_FAMILY.get(eco, 'every ' + eco + '-owned service and product with a digital surface')}. "
                  f"Every owned surface is a row, before the entry against after it."),
    }


def confirm_reply(d, price_label='$500', today=None):
    eco = str(d.get('ecosystem') or '')
    entry = str(d.get('entry_point') or '')
    fam = _ECO_FAMILY.get(eco, f"every {eco}-owned service and product with a digital surface")
    try:
        from .bpiq_wizard import pretty_range
        rng = pretty_range(d['start_date'], d['end_date']) if d.get('start_date') and d.get('end_date') else ''
    except Exception:
        rng = f"{d.get('start_date')} to {d.get('end_date')}"
    lab = str(d.get('window_label') or '')
    win = f"{lab} ({rng})" if lab and rng and lab.lower() != rng.lower() else (rng or lab or 'trailing 12 months')
    return (f"Here's the Flywheel I'll build:\n"
            f"- Entry point: {entry}\n"
            f"- Ecosystem: {eco}, {fam}\n"
            f"- Window: {win}; each person is measured over the {PRE_DAYS} days before their entry against the {POST_DAYS} days after it\n\n"
            f"It starts at the people who {entry} and shows who they are, the share of them on each {eco}-owned surface before the entry and "
            f"after it, and what the buyers bought. It lands on the Flywheel IQ page when finished. It prices at {price_label}. Run it?")


def family_short(eco, n=9):
    """The first few owned surfaces of an ecosystem, for the question line."""
    fam = _ECO_FAMILY.get(eco)
    if not fam:
        return f"every {eco}-owned service and product with a digital surface"
    _head, _, tail = fam.partition(':')
    names = [x.strip() for x in tail.replace(' and the rest', '').split(',') if x.strip()]
    if len(names) > n:
        return ', '.join(names[:n]) + ' and the rest'
    return ', '.join(names) + (' and the rest' if 'and the rest' in fam else '')


def step_prompt(d):
    step = d.get('step') or next_step(d)
    if step == STEP_ENTRY:
        return ASK_ENTRY, ['Cancel']
    if step == STEP_ECOSYSTEM:
        hint = str(d.get('ecosystem_hint') or '')
        lead = f"Got it: {d.get('entry_point')}. " if d.get('entry_point') else ''
        if hint:
            # the entry's own service names the company (APV -> Prime Video -> Amazon): propose it, one tap to
            # accept, Other to name a different one (Jenna 2026-11-03)
            plat = str(d.get('platform') or '')
            tie = (f"{plat} belongs to {hint}" if plat and plat != hint else f"The entry is on {hint} itself")
            return (lead + f"{tie}, so the flywheel would examine the {hint} ecosystem: {family_short(hint)}. "
                    f"Go with {hint}, or name a different company."), [hint, OTHER_CHIP, 'Cancel']
        return lead + ASK_ECOSYSTEM, list(ECO_CHIPS) + [OTHER_CHIP, 'Cancel']
    if step == STEP_DATES:
        return ASK_DATES, list(DATE_CHIPS) + ['Cancel']
    if step == STEP_EXISTS:
        return '', [REBUILD_CHIP, 'Cancel']
    return '', []


def _payload(reply, chips, **extra):
    out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': list(chips or []),
           'offer_deck': False, 'deck_angle': None}
    out.update(extra)
    return out


# ---------------------------------------------------------------- store
def _host_cap(host, *names):
    for n in names:
        try:
            if callable(getattr(host, 'has', None)) and not host.has(n):
                continue
            v = getattr(host, n, None)
            if v is not None:
                return v
        except Exception:
            continue
    return None


def _same_thread(doc_thread_id, thread_id):
    try:
        from . import intake_guard as _ig
        return bool(_ig.same_thread(doc_thread_id, thread_id))
    except Exception:
        a, b = str(doc_thread_id or '').strip(), str(thread_id or '').strip()
        return not a or not b or a == b


# The intake guard (prometheus.intake_guard, Jenna 2026-11-05): a step
# consumes a message only when it reads as an answer to that step. The
# kind each step waits on.
_STEP_KIND = {STEP_ENTRY: 'text', STEP_ECOSYSTEM: 'short', STEP_DATES: 'dates', STEP_EXISTS: 'text', STEP_CONFIRM: 'choice'}


def answers_step(text, draft, today=None):
    """True when `text` reads as the answer to the draft's open step
    (its chips, a control word, dates at the dates step, a one-line
    entry at the entry step). A question is not."""
    try:
        from . import intake_guard as _ig
    except Exception:
        return True
    d = dict(draft or {})
    step = d.get('step') or next_step(d)
    try:
        _reply, chips = step_prompt(d)
    except Exception:
        chips = ['Cancel']
    patterns = (_REBUILD_RX, _OPEN_RX, _YES_RX, _OTHER_ECO_RX) if step == STEP_EXISTS else (_YES_RX, _OTHER_ECO_RX, _OTHER_RX)
    if step == STEP_ECOSYSTEM:
        # any company name answers the ecosystem step ('Warner Bros. Discovery', 'Paramount Skydance')
        low = _low(text)
        if low in _ECOSYSTEMS or low in _PLATFORMS:
            return True
    if step == STEP_DATES and (_TRAILING_RX.search(str(text or '')) or _CAL_YEAR_RX.search(str(text or ''))):
        return True
    return _ig.is_answer_to_step(text, _STEP_KIND.get(step, 'text'), chips=list(chips) + [RUN_CHIP, REBUILD_CHIP, 'Build this window'],
                                 answer_patterns=patterns)


def last_agent_is_wizard(history):
    """The last agent turn in the thread asked a flywheel step (or
    carried the way back to one)."""
    try:
        from . import intake_guard as _ig
        turn = _ig.last_agent_turn(history)
    except Exception:
        turn = None
    if not turn:
        return False
    txt = str(turn.get('text') or turn.get('content') or '').strip()
    if any(txt.startswith(p[:40]) for p in (ASK_ENTRY, ASK_ECOSYSTEM, ASK_DATES, ASK_ECOSYSTEM_OTHER, OTHER_DATES_REPLY, "Here's the Flywheel I'll build")):
        return True
    if txt.startswith('Got it: ') or txt.startswith('A flywheel for ') or txt.startswith('It is on the Flywheel IQ page'):
        return True
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    want = 'back to the flywheel'
    for o in (meta.get('options') or turn.get('options') or turn.get('followups') or []):
        lbl = (o.get('send') or o.get('label')) if isinstance(o, dict) else o
        if str(lbl or '').strip().lower() == want:
            return True
    return False


class MemoryStore:
    def __init__(self):
        self.d = {}

    def load(self, user, thread_id=''):
        return self.d.get((str(user), str(thread_id or '')))

    def save(self, user, thread_id, draft):
        self.d[(str(user), str(thread_id or ''))] = dict(draft or {})
        return True

    def clear(self, user, thread_id=''):
        self.d.pop((str(user), str(thread_id or '')), None)
        return True


def _s3_store(host):
    class _S:
        def _s3(self):
            return _host_cap(host, 's3_client')

        def _bucket(self):
            return _host_cap(host, 'bucket', 'S3_BUCKET') or 'dashboard-inputs'

        def load(self, user, thread_id=''):
            s3 = self._s3()
            if s3 is None:
                return None
            try:
                from . import pending_intake as pi
                hit = pi.peek(s3, self._bucket(), user, WIZARD_FLOW)
                if not (isinstance(hit, dict) and hit.get('step')):
                    return None
                # the stash is per reader; it answers only for the thread it
                # was saved on (Jenna 2026-11-05: an intake armed on one
                # thread never reads a message on another)
                if not _same_thread(hit.get('thread_id'), thread_id):
                    return None
                return hit
            except Exception:
                return None

        def save(self, user, thread_id, draft):
            s3 = self._s3()
            if s3 is None:
                return False
            try:
                from . import pending_intake as pi
                return bool(pi.stash(s3, self._bucket(), user, WIZARD_FLOW, dict(draft or {}, thread_id=thread_id or ''), run_label=CHIP))
            except Exception:
                traceback.print_exc()
                return False

        def clear(self, user, thread_id=''):
            s3 = self._s3()
            if s3 is None:
                return False
            try:
                from . import pending_intake as pi
                pi.clear(s3, self._bucket(), user, WIZARD_FLOW)
            except Exception:
                pass
            return True
    return _S()


def store_for(body, g=None):
    try:
        if isinstance(body, dict) and body.get('persist') is False:
            return MemoryStore()
        from . import faults as _faults
        if _faults.is_probe():
            return MemoryStore()
    except Exception:
        pass
    try:
        from .host import host as _host
        if _host_cap(_host, 's3_client') is not None:
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


def _uname(user):
    if isinstance(user, dict):
        return str(user.get('username') or user.get('email') or '').strip()
    return str(user or '').strip()


# ---------------------------------------------------------------- the turn
def analyze_hook(text, body, history, user, ppu, uname, g, first=None, store=None, price_label='$500', today=None,
                 existing_lookup=None):
    """One wizard turn: a chat payload, or None when this is not a
    flywheel ask and no draft is open. `existing_lookup(inputs)` returns
    the studies already on the page for the same entry and ecosystem
    (see flywheel_synthesis.existing_studies); with one the wizard offers
    the existing study before anything is charged. Never raises."""
    try:
        return _analyze_hook(text, body, history, user, ppu, uname, g, first=first, store=store,
                             price_label=price_label, today=today, existing_lookup=existing_lookup)
    except Exception:
        traceback.print_exc()
        return None


def _analyze_hook(text, body, history, user, ppu, uname, g, first=None, store=None, price_label='$500', today=None,
                  existing_lookup=None):
    t = str(text or '').strip()
    uname = uname or _uname(user)
    thread_id = str((body or {}).get('thread_id') or '')
    if store is None:
        store = store_for(body, g)
    # The widget's collect flag (fw_inputs) is page state and follows the
    # reader across threads; it arms this turn only when the thread
    # itself could be waiting (its last agent turn is not a job notice,
    # and when it asked anything it asked a flywheel step). The store is
    # per (reader, thread). (Jenna 2026-11-05)
    collecting = bool((body or {}).get('fw_inputs'))
    if collecting:
        try:
            from . import intake_guard as _ig
            if not _ig.armed_flag_applies(history):
                collecting = False
            elif _ig.last_agent_turn(history) is not None and not last_agent_is_wizard(history):
                collecting = False
        except Exception:
            pass
    draft = store.load(uname, thread_id) if store is not None else None
    if not draft and not collecting and last_agent_is_wizard(history):
        collecting = True
    if is_start(t) or (first is True and not draft and not collecting):
        # a start always opens fresh; the words after the verb fill ahead ("run a flywheel for Gilmore Girls on PV")
        d = apply_ahead(empty_draft(), t, today=today)
        return _advance(d, store, uname, thread_id, price_label, today, existing_lookup=existing_lookup)
    if not draft and not collecting:
        return None
    if is_cancel(t):
        if store is not None:
            store.clear(uname, thread_id)
        return _payload(CANCEL_REPLY, [], fw_collect=False)
    d = dict(draft) if isinstance(draft, dict) else empty_draft()
    if not d.get('step'):
        d['step'] = next_step(d)
    # The intake guard (Jenna 2026-11-05): a message that does not read
    # as the answer to the open step (a question, an order on something
    # else) is not the wizard's. None; the draft stays at its step; the
    # service routes the words and the reply carries 'Back to the
    # flywheel'. 'Back to the flywheel' or 'back to it' re-asks the step.
    try:
        from . import intake_guard as _ig
        if _ig.is_resume(t):
            if store is not None:
                store.save(uname, thread_id, d)
            reply, chips = step_prompt(d)
            if d.get('step') == STEP_EXISTS:
                return exists_reply(d, d.get('existing') or [], again=True)
            return _payload(reply or ASK_ENTRY, chips or ['Cancel'], fw_collect=True)
        if first is not True and not answers_step(t, d, today=today):
            _ig.mark_resume(FLOW, step=str(d.get('step') or ''))
            return None
    except Exception:
        traceback.print_exc()
    if d.get('step') == STEP_EXISTS:
        # the study is already on the page: build anyway, open it, or stop
        if _REBUILD_RX.match(t) or _YES_RX.match(t):
            return _advance(d, store, uname, thread_id, price_label, today, existing_lookup=None)
        if _OPEN_RX.match(t):
            if store is not None:
                store.save(uname, thread_id, d)
            return exists_reply(d, d.get('existing') or [], again=True)
        # anything else restarts the wizard with the new words
        d2 = apply_ahead(empty_draft(), t, today=today)
        if d2.get('entry_point'):
            return _advance(d2, store, uname, thread_id, price_label, today, existing_lookup=existing_lookup)
        if store is not None:
            store.save(uname, thread_id, d)
        return exists_reply(d, d.get('existing') or [], again=True)
    d, err = apply_answer(d, t, today=today)
    if err:
        if store is not None:
            store.save(uname, thread_id, d)
        _reply, chips = step_prompt(d)
        if err in (ASK_ECOSYSTEM_OTHER, OTHER_DATES_REPLY) or d.get('step') == STEP_CONFIRM:
            chips = ['Cancel']   # they asked to type it; the chips they just declined do not come back
        return _payload(err, chips, fw_collect=True)
    return _advance(d, store, uname, thread_id, price_label, today)


def _advance(d, store, uname, thread_id, price_label, today, existing_lookup=None):
    d['step'] = next_step(d)
    if d['step'] == STEP_CONFIRM:
        inputs = engine_inputs(d)
        if existing_lookup is not None:
            try:
                found = list(existing_lookup(inputs) or [])
            except Exception:
                traceback.print_exc()
                found = []
            if found:
                d['step'] = STEP_EXISTS
                d['existing'] = [{k: v for k, v in (f or {}).items() if k in ('key', 'title', 'window_start', 'window_end', 'cohort')} for f in found[:3]]
                if store is not None:
                    store.save(uname, thread_id, d)
                return exists_reply(d, d['existing'])
        if store is not None:
            store.clear(uname, thread_id)
        return _payload(confirm_reply(d, price_label=price_label, today=today), [RUN_CHIP, 'Cancel'],
                        fw_confirm_payload=inputs, fw_wizard_done=True)
    if store is not None:
        store.save(uname, thread_id, d)
    reply, chips = step_prompt(d)
    return _payload(reply, chips, fw_collect=True)


def _pretty(a, b):
    try:
        from .bpiq_wizard import pretty_range
        return pretty_range(a, b) if a and b else ''
    except Exception:
        return f"{a} to {b}" if a and b else ''


def exists_reply(d, found, again=False):
    """The study is already on the Flywheel IQ page: say so, link it, offer the rebuild (2026-11-03)."""
    try:
        from migration.flywheel_synthesis import study_url
    except Exception:
        def study_url(key):
            from urllib.parse import quote
            return 'https://dashboard.crosswalknyc.com/?view=flywheelIQ&run=' + quote(str(key or '').rsplit('/', 1)[-1], safe='')
    top = (found or [{}])[0] or {}
    entry = str(d.get('entry_point') or '')
    eco = str(d.get('ecosystem') or '')
    have = _pretty(top.get('window_start'), top.get('window_end'))
    want = _pretty(d.get('start_date'), d.get('end_date'))
    same = bool(have) and have == want
    who = f"the people who {entry}" if entry and re.match(r'^[a-z]', entry) else (entry or 'this entry')
    scope = f"{who} across the {eco} ecosystem" if eco else who
    if same:
        line = (f"A flywheel for {scope} already exists for that date range ({have}). "
                f"Open it on the Flywheel IQ page, or I can build it again.")
    elif have:
        line = (f"A flywheel for {scope} already exists, for {have}. Your ask covers {want or 'a different window'}. "
                f"Open the existing one on the Flywheel IQ page, or I can build this window.")
    else:
        line = f"A flywheel for {scope} is already on the Flywheel IQ page. Open it there, or I can build it again."
    if again:
        line = f"It is on the Flywheel IQ page: {top.get('title') or 'the existing study'}. " + ("Build it again, or ask me anything else." if same else "Build this window, or ask me anything else.")
    chips = [REBUILD_CHIP if same or not have else 'Build this window', 'Cancel']
    return _payload(line, chips, fw_collect=True,
                    file_link={'url': study_url(top.get('key') or ''), 'label': OPEN_LABEL})


def running_reply(inputs):
    """The line after 'Run the flywheel' lands."""
    entry = str((inputs or {}).get('entry_point') or (inputs or {}).get('captured_action') or 'the entry')
    eco = str((inputs or {}).get('ecosystem') or 'the ecosystem')
    return (f"On it. Building the {eco} flywheel for the people who {entry} now: who they are, and every {eco}-owned surface "
            f"before the entry against after it. It lands on the Flywheel IQ page, and I will confirm here when it is ready.")


__all__ = ['analyze_hook', 'is_start', 'is_cancel', 'parse_entry', 'parse_window', 'normalize_ecosystem', 'normalize_platform',
           'apply_ahead', 'apply_answer', 'engine_inputs', 'confirm_reply', 'step_prompt', 'family_short', 'running_reply', 'empty_draft',
           'next_step', 'exists_reply', 'CHIP', 'RUN_CHIP', 'OTHER_CHIP', 'REBUILD_CHIP', 'OPEN_LABEL', 'ECO_CHIPS', 'ASK_ENTRY',
           'ASK_ECOSYSTEM', 'ASK_ECOSYSTEM_OTHER', 'ASK_DATES', 'STEP_EXISTS', 'MemoryStore', 'store_for']
