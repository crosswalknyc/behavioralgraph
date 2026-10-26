"""Digital Twins IQ in Prometheus (2026-10-26, Jenna: "you should be
able to easily input your things or upload them through prometheus").

The lanes, all served before the single router on the analyze surface
(chat.py calls ``analyze_hook`` once; everything else lives here so the
legacy module stays under its line ratchet):

  intake      'Build a twin of Gilmore Girls Hulu viewers' opens the
              guided intake (dual read, propose the missing field,
              confirm, never the same question twice); the confirm
              charges and queues ``twin_build``.
  labs        'Run Greenlight Lab on the Gilmore Girls twin ...', 'Run
              Launch Lab on this plan' (an attached budget sheet or a
              pasted channel list), 'Put the signal-led plan in the
              Spend Engine on the rule: max 15% a day', 'Compare the X
              and Y twins on ...': the twin resolves (named, open on
              the page, or the reader's newest), the inputs are read
              from the words and the attached file, the run is charged
              and queued (Spend Engine activates on the request).
  focus_group 'Open a focus group with the Mark Rober twin and ask whether
              they would pay $29 for the Build Box', then 'why?', 'show
              of hands who watched season 1', 'ask them ...', 're-ask
              at $19': answered in the chat in seconds with the % table
              and the voices; follow-ups in the thread stay in the
              same room.
  ask         a question on an open or named twin that maps to a run
              kind (odds, price, sequel, move, attach, drop) runs it.
  catalog     'do you have a twin for X' / 'which twins do we have'.
  status      'is my twin ready'.
  how_to      'how do I use Digital Twins IQ' / 'how do I run
              Greenlight Lab': the directions, free.

Every exit runs the vocabulary scrub. No email is sent from here.
"""
from __future__ import annotations

import json
import re
import time
import traceback
import uuid

from .host import host

FLOW = 'digital_twin'
RUN_LABEL = 'Build the twin'
ASK_COPY = ('Happy to build a twin. Tell me the subject: a title, a franchise, a creator, a brand, an audience, or a bare idea, '
            'and what you want to use the twin for (odds on a new season, a launch plan, a price change, a partnership). '
            'For example: "Build a twin of Gilmore Girls Hulu viewers for a new season on Netflix".')
LABELS = ['the subject', 'what you want to use it for']
ACTIVE_PREFIX = 'system/digital_twins/panel_active/'
ACTIVE_TTL_S = 6 * 3600
OPEN_CHIP = 'Open in Digital Twins IQ'

_BUILD_RX = re.compile(r"\b(build|make|create|spin up|start|run|pull|set up)\b[^.?!]{0,40}\b(?:digital\s+)?twins?\b|\b(?:digital\s+)?twin\s+(?:of|for|on)\b", re.I)
_GREENLIGHT_RX = re.compile(r"\bgreen\s?light(?:\s+lab)?\b|\bodds\b[^.?!]{0,80}\b(?:clear|hit|reach|beat|make)s?\b|\bwhat are the odds\b", re.I)
_LAUNCH_RX = re.compile(r"\blaunch\s+lab\b|\brun\s+launch\b|\b(?:run|test|try)\s+(?:this|the|my|our)\s+(?:media\s+)?plan\b", re.I)
_SPEND_RX = re.compile(r"\bspend\s+engine\b|\bactivate\b[^.?!]{0,30}\bplan\b|\bput\b[^.?!]{0,40}\bplan\b[^.?!]{0,30}\b(?:in|into|on)\b", re.I)
_PANEL_OPEN_RX = re.compile(r"\b(?:open|start|create|set up|convene|run|seat)\s+(?:a\s+|the\s+)?(?:panel(?:\s+lab)?|room|focus\s+group(?:\s+iq)?|survey)\b|\b(?:panel\s+lab|focus\s+group\s+iq)\b", re.I)
_PANEL_ASK_RX = re.compile(r"^\s*(?:ask\s+(?:them|the\s+room|the\s+twins?|the\s+group|the\s+panel|the\s+focus\s+group)\b|show\s+of\s+hands\b|hands\s+up\b|re-?ask\b|follow\s+up\b|probe\b|poll\s+(?:them|the\s+room)\b|"
                           r"why\??$|why\s+not\??$|what\s+would\s+change\s+(?:their|your)\s+mind|how\s+many\s+of\s+(?:them|you)\b|would\s+they\b|do\s+they\b|rate\b|which\s+do\s+they\b|"
                           r"(?:trailer|option|version|logline)\s+a\s+(?:vs\.?|versus|or)\b)", re.I)
_COMPARE_RX = re.compile(r"\bcompare\b[^.?!]{0,80}\btwins?\b|\btwins?\b[^.?!]{0,60}\b(?:side by side|against|vs\.?|versus)\b", re.I)
_CATALOG_RX = re.compile(r"\b(?:do (?:we|you) have|is there|are there|which|what|list|show me)\b[^.?!]{0,30}\btwins?\b|\btwins?\b[^.?!]{0,20}\b(?:do (?:we|you) have|are there|in the library|on the shelf)\b", re.I)
_STATUS_RX = re.compile(r"\b(?:is|are)\s+(?:my|the|our)\s+twins?\s+(?:ready|done|built|finished|up)\b|\btwins?\s+(?:status|ready\s+yet)\b|\bwhere\s+is\s+(?:my|the)\s+twin\b", re.I)
_PRICE_RX = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?")
_GOAL_RX = re.compile(r"\b(?:goal|clears?|hits?|reach(?:es)?|beats?|target)\b[^.?!\d$]{0,20}\$?\s?(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\s*(viewers|buyers|listeners|players|readers|people|subscribers|subs|first actions|signups)?", re.I)
_WEEKS_RX = re.compile(r"(\d{1,2})\s*weeks?\b", re.I)
_DAYS_RX = re.compile(r"(\d{1,3})\s*days?\b", re.I)
_PLATFORMS = ('Netflix', 'Hulu', 'Peacock', 'Max', 'Disney+', 'Paramount+', 'Prime Video', 'Apple TV+', 'YouTube', 'Tubi', 'Pluto TV', 'Roku Channel', 'Spotify', 'Theaters', 'Starz', 'AMC+')
_IN_ROOM_RX = re.compile(r"(\d{1,2})\s+(?:twins?|people|seats?|voices?)\s+in\s+the\s+room", re.I)
_RULE_MAX_RX = re.compile(r"max(?:imum)?\s*(?:daily\s+)?(?:shift\s+)?(?:of\s+)?(\d{1,2})\s*%|(\d{1,2})\s*%\s*(?:a|per)\s+day", re.I)
_RULE_FLOOR_RX = re.compile(r"floor\s*(?:of\s+)?\$?\s?(\d[\d,]*)\s*([kK])?", re.I)
_WITH_TWIN_RX = re.compile(r"\b(?:with|on|for|of|using)\s+(?:the\s+)?(?P<name>[A-Z][\w'&.:+-]*(?:\s+[A-Za-z0-9][\w'&.:+-]*){0,5}?)\s+twins?\b", re.I)
_THE_X_TWIN_RX = re.compile(r"\bthe\s+(?P<name>[A-Za-z0-9][\w'&.:+-]*(?:\s+[A-Za-z0-9][\w'&.:+-]*){0,5}?)\s+twins?\b", re.I)

PARSE_SYSTEM_PROMPT = """You read a request to build a digital twin of an audience. Return JSON only:
{"subject": "<the title, franchise, creator, brand, audience or idea, as named; empty when none>",
 "subject_kind": "audience|ip|brand|talent|idea",
 "purpose": "<what they want to use the twin for, in their words; empty when not said>",
 "platform_home": "<the service or place named, or empty>",
 "format": "film|series|book|game|podcast|creator|brand|none|",
 "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} or null}
Never invent a subject. 'Gilmore Girls Hulu viewers' is subject 'Gilmore Girls Hulu viewers', kind audience, platform_home Hulu."""

VOICES_SYSTEM_PROMPT = """You voice a few twins in a focus-group room. Each twin stands for a group of real people; none is a real person. Given the question, the room's answer distribution and the roster, write 3 or 4 short first-person lines (one sentence each, 8 to 24 words, plain spoken English) from named twins in the roster, spread across the answer sides in proportion to the distribution, and one plain read of the room in one sentence. Never use the words: synthetic, model, estimated, research, panel, sample, data, confidence. No em dashes.
Return JSON only: {"voices": [{"name": "<a roster name>", "line": "<the line>"}], "read": "<one sentence>"}"""


# ---------------------------------------------------------------- utils
def _low(t):
    return ' '.join(str(t or '').lower().split())


def _s3():
    return host.s3_client, (host.bucket if host.has('bucket') else 'dashboard-inputs')


def _scrub(s):
    from migration.digital_twin_synthesis import scrub_text
    return scrub_text(s)


def _payload(reply, followups=None, **extra):
    out = {'success': True, 'action': 'answer', 'reply': _scrub(reply), 'followups': [_scrub(f) for f in (followups or [])], 'offer_deck': False, 'deck_angle': None}
    out.update(extra)
    return out


def _uname(user):
    return str((user or {}).get('username') or (user or {}).get('email') or '').strip() if isinstance(user, dict) else str(user or '')


def _is_super(user):
    return str((user or {}).get('role') or '').lower().replace(' ', '_') in ('super_admin', 'superadmin')


def _num(s, k=''):
    try:
        v = float(str(s).replace(',', ''))
    except (TypeError, ValueError):
        return 0.0
    k = (k or '').lower()
    return v * (1000 if k == 'k' else 1_000_000 if k == 'm' else 1)


def _price_label(key, username):
    try:
        from prometheus.legacy.watch import _pm_usd, _pm_usd_label
        from migration.digital_twins import PRICING
        return _pm_usd_label(_pm_usd(key, PRICING[key], username), username)
    except Exception:
        from migration.digital_twins import PRICING
        return f"${PRICING[key]:,.0f}"


def _charge(user, key, description, job_id):
    """None when charged or free (super admins), else the funds payload."""
    from migration.digital_twins import PULL_TYPES
    credits = {'digital_twins_iq': 15, 'digital_twin_run': 3, 'digital_twin_spend': 5, 'digital_twin_panel': 4, 'digital_twin_panel_more': 3}
    if _is_super(user) or not host.has('consume_credit'):
        return None
    try:
        ok = bool(host.consume_credit(_uname(user), description=description, pull_type=PULL_TYPES[key], credits_used=credits[key], job_id=job_id))
    except Exception:
        traceback.print_exc()
        ok = False
    if ok:
        return None
    return _payload(f"That step runs {_price_label(key, _uname(user))} and your account cannot cover it right now. Add funds or ask your admin, and it runs the moment you are set.")


# -------------------------------------------------------------- intents
def twin_intent(text, chip=''):
    """A build order for a twin, a lab run, a panel order, a compare of
    twins, a twin catalog or status question, or the directions: the
    ask belongs on the analyze surface, where ``analyze_hook`` serves it."""
    low = _low(text)
    if chip and low == chip.lower():
        return True
    if not low or len(low) > 1200:
        return False
    if re.search(r"\bpanel report\b|\bhow big is your panel\b|\bpanel size\b|\bthe panel\b(?!\s+lab)", low) and 'twin' not in low and 'panel lab' not in low and 'focus group' not in low:
        return False
    if _BUILD_RX.search(low) and not re.search(r"\btwin\s+(?:bed|peaks|cities|falls|flame|turbo|engine)\b", low):
        return True
    if 'twin' in low and (_GREENLIGHT_RX.search(low) or _LAUNCH_RX.search(low) or _SPEND_RX.search(low) or _COMPARE_RX.search(low) or _CATALOG_RX.search(low) or _STATUS_RX.search(low)):
        return True
    if _PANEL_OPEN_RX.search(low) and ('twin' in low or 'panel lab' in low or 'focus group' in low or 'survey' in low):
        return True
    if re.search(r"\b(greenlight lab|launch lab|spend engine|panel lab|focus group iq|digital twins? iq|twin iq)\b", low):
        return True
    if re.search(r"\btwins?\b", low) and not re.search(r"\btwin\s+(?:bed|peaks|cities|falls|flame|turbo|engine|sister|brother|size)s?\b", low) and question_kind(low):
        return True   # a question on a named twin that maps to a run (price, sequel, move, attach, drop, odds, plan)
    return False


def lab_kind(text):
    low = _low(text)
    if _PANEL_OPEN_RX.search(low) or 'panel lab' in low or 'focus group' in low:
        return 'panel'
    if _SPEND_RX.search(low):
        return 'spend'
    if _LAUNCH_RX.search(low):
        return 'launch'
    if _COMPARE_RX.search(low):
        return 'compare'
    if _GREENLIGHT_RX.search(low):
        return 'greenlight'
    return ''


def question_kind(text):
    """A free question on a twin mapped to a run kind, or ''."""
    low = _low(text)
    if _GREENLIGHT_RX.search(low) or re.search(r"\b(logline|idea|greenlight|first month|first 30 days)\b", low):
        return 'greenlight'
    if re.search(r"\b(price|pricing|\$\d|a month|per month|take rate|packaging|bundle)\b", low):
        return 'pricing'
    if re.search(r"\b(sequel|next season|season \d|another season|episode drop|new season|renewal)\b", low):
        return 'content'
    if re.search(r"\b(move|moves|moving|window|exclusive|leaves|shift(?:s|ed)? to|from \w+ to \w+)\b", low) and re.search(r"\b(netflix|hulu|peacock|max|disney|paramount|prime|apple|theatrical|theaters|window)\b", low):
        return 'platform_move'
    if re.search(r"\b(partnership|partner with|sponsor|brand deal|\bx\b)\b", low):
        return 'partnership'
    if re.search(r"\b(attach|cast|casting|host|lead actor|starring|feature)\b", low):
        return 'talent_attach'
    if re.search(r"\b(merch|drop|product|box|kit|collectible|bundle)\b", low) and re.search(r"\b(sell|buy|buyers|revenue|units|\$)\b", low):
        return 'merch_drop'
    if re.search(r"\b(plan|budget|channels?|spend)\b", low):
        return 'launch'
    return ''


# -------------------------------------------------------------- intake
def keyword_parse_twin(text):
    """Rules over the words: the subject after 'twin of / for', the
    purpose after 'for' / 'to' / ':' , the platform named, the kind."""
    t = ' '.join(str(text or '').split())
    out = {'subject': None, 'subject_kind': None, 'purpose': None, 'platform_home': None, 'format': None, 'window': None}
    m = re.search(r"\btwins?\s+(?:of|for|on)\s+(?:the\s+)?(?P<s>[^.,;:?!]+?)(?=\s+(?:for|to|so|that|and ask|and run|,|\.|$)|$)", t, re.I)
    if m:
        subj = m.group('s').strip()
        out['subject'] = subj[:140]
    else:
        # the collect step: "Mark Rober", or "Build a twin\n\nMark Rober" (the opener's answer joined to the order)
        lines = [ln.strip(' .') for ln in re.split(r"\n+", t) if ln.strip(' .')]
        bare = lines[-1] if lines else ''
        bare = re.sub(r"^(?:(?:please\s+)?(?:build|make|create|spin up|start|run|pull|set up)\s+(?:a|me a|the|another|new)?\s*(?:digital\s+)?twins?\s*(?:of|for|on)?\s*)", '', bare, flags=re.I).strip(' .:')
        if bare and 1 <= len(bare.split()) <= 8 and not re.match(r"^(what|how|why|who|which|when|where|do|does|is|are|can|could|would|should|yes|no|ok|okay|cancel|never|thanks?|thank you|hi|hello|hey|great|cool|nice|sure|yep|yeah|please)\b", bare, re.I) \
                and not re.search(r"\b(twin|twins|profile|journey|deck)\b", bare, re.I):
            out['subject'] = bare[:140]
    m2 = re.search(r"\b(?:for|to use (?:it )?for|to)\s+(?P<p>(?:a|an|the)?\s?[^.;]{6,160})$", t[m.end():] if m else t, re.I)
    if m2 and out['subject'] and m:
        out['purpose'] = m2.group('p').strip(' .')[:300]
    plats = _platforms_in(t)
    if plats:
        inside = [p for p in plats if out['subject'] and p.lower() in str(out['subject']).lower()]
        out['platform_home'] = (inside or plats)[0]
    low = t.lower()
    if re.search(r"\b(idea|logline|concept|pitch)\b", low):
        out['subject_kind'] = 'idea'
    elif re.search(r"\b(viewers|fans|audience|listeners|readers|players|subscribers|customers|shoppers|users)\b", low):
        out['subject_kind'] = 'audience'
    elif re.search(r"\b(brand|retailer|store|product line)\b", low):
        out['subject_kind'] = 'brand'
    elif re.search(r"\b(creator|youtuber|host|actor|actress|musician|band|influencer|podcaster)\b", low):
        out['subject_kind'] = 'talent'
    elif re.search(r"\b(film|movie|series|show|season|franchise|book|novel|game|podcast)\b", low):
        out['subject_kind'] = 'ip'
    for fmt, rx in (('film', r"\b(film|movie)\b"), ('series', r"\b(series|show|season)\b"), ('book', r"\b(book|novel)\b"), ('game', r"\bgame\b"), ('podcast', r"\bpodcast\b"), ('creator', r"\b(creator|youtuber|channel)\b")):
        if re.search(rx, low):
            out['format'] = fmt
            break
    out['missing'] = [k for k in ('subject', 'purpose') if not out.get(k)]
    return out


def parse_twin(text, usage_extras=None):
    """The model read, then the rules fill what it left empty."""
    parsed = {}
    try:
        if host.has('claude_data'):
            got = host.claude_data(PARSE_SYSTEM_PROMPT, str(text or ''), max_tokens=500, temperature=0.0, surface='twin_parse', usage_extras=usage_extras)
            if isinstance(got, dict):
                parsed = {k: got.get(k) for k in ('subject', 'subject_kind', 'purpose', 'platform_home', 'format', 'window')}
    except Exception:
        traceback.print_exc()
    kw = keyword_parse_twin(text)
    for k, v in kw.items():
        if k != 'missing' and not parsed.get(k) and v:
            parsed[k] = v
    if parsed.get('subject'):
        parsed['subject'] = str(parsed['subject']).strip()[:140]
    return parsed


def twin_inputs_complete(parsed):
    return bool(isinstance(parsed, dict) and str(parsed.get('subject') or '').strip() and str(parsed.get('purpose') or '').strip())


def propose_twin_field(parsed):
    """A subject with no stated purpose gets the general one proposed on
    the confirm card: run plans, titles and ideas forward on it."""
    p = parsed or {}
    p.pop('_ask', None)
    if p.get('subject') and not p.get('purpose'):
        return 'purpose', 'run plans, titles and ideas forward on this audience, with the IP lifetime value when it applies'
    return None


def confirm_reply(parsed, username=None):
    from migration.digital_twin_synthesis import default_window
    w = parsed.get('window') if isinstance(parsed.get('window'), dict) and parsed['window'].get('start') else default_window()
    kind = str(parsed.get('subject_kind') or 'audience')
    lines = [f"Here is the twin brief:",
             f"- Subject: {parsed.get('subject')}",
             f"- Kind: {kind}" + (f", {parsed.get('format')}" if parsed.get('format') else '') + ("; whether it is IP-related is decided from the catalog at build time" if kind != 'ip' else ''),
             f"- Purpose: {parsed.get('purpose')}",
             f"- Window: {w.get('label') or 'Trailing 12 months'} ({w.get('start')} to {w.get('end')})"]
    if parsed.get('platform_home'):
        lines.append(f"- Home: {parsed.get('platform_home')}")
    lines.append(f"- Price: {_price_label('digital_twins_iq', username)} for the twin, its IP lifetime value and three runs; each further run {_price_label('digital_twin_run', username)}")
    lines.append(f"Say '{RUN_LABEL}' and I will line it up.")
    return '\n'.join(lines)


def flow_table_entry():
    """The intake table row: (parse_fn, complete_fn, required, ask_copy, fallback_fn, propose_fn)."""
    return (parse_twin, twin_inputs_complete, ('subject', 'purpose'), ASK_COPY, keyword_parse_twin, propose_twin_field)


def ui_entry():
    """The _PM_INTAKE_UI row: (screen-draft kind, confirm reply fn name, run chip, payload key, collect key, labels)."""
    return ('', '_pm_twin_confirm_reply', RUN_LABEL, 'twin_confirm_payload', 'twin_collect', LABELS)


def intake_armed(history):
    """The last agent turn was this intake's opener, stall or card."""
    for t in reversed(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        txt = str(t.get('text') or t.get('content') or '')
        return txt.startswith('Happy to build a twin') or ('Almost there - I still need' in txt and 'the subject' in txt) or txt.startswith('Here is the twin brief')
    return False


# ------------------------------------------------------------- resolve
def _runs(user):
    from migration import digital_twins as DT
    s3, _b = _s3()
    return DT.visible_runs(DT.list_runs(s3), user)


def _open_twin_key(body):
    try:
        vc = ((body or {}).get('page_context') or {}).get('view_context') or {}
        if str(vc.get('view_id') or '') == 'digitalTwinsIQ':
            key = str((vc.get('data') or {}).get('key') or '')
            if key.startswith('digital-twins/'):
                return key
    except Exception:
        pass
    return ''


def _recent_twin_key(history):
    for t in reversed(history or []):
        if isinstance(t, dict):
            meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
            if meta.get('twin_key'):
                return str(meta['twin_key'])
            m = re.search(r"digital-twins/[\w./-]+\.json\.gz", str(t.get('text') or ''))
            if m:
                return m.group(0)
    return ''


def resolve_twins(text, body, history, user, limit=2, strict=False):
    """The twins the words mean: named, then the one open on the page,
    then the one this thread was about, then the reader's newest.
    ``strict`` keeps only the twins the words name (the catalog lane)."""
    from migration import digital_twins as DT
    runs = _runs(user)
    found = []
    low = str(text or '')
    names = [m.group('name') for m in _WITH_TWIN_RX.finditer(low)] + [m.group('name') for m in _THE_X_TWIN_RX.finditer(low)]
    for nm in names:
        hit = DT.find_twin(runs, nm)
        if hit and hit not in found:
            found.append(hit)
    if not found:
        hit = DT.find_twin(runs, low)
        if hit:
            found.append(hit)
    if strict:
        return found[:limit], runs
    if len(found) < limit:
        k = _open_twin_key(body)
        if k and not any(r.get('key') == k for r in found):
            hit = next((r for r in runs if r.get('key') == k), None)
            if hit:
                found.append(hit)
    if not found:
        k = _recent_twin_key(history)
        hit = next((r for r in runs if r.get('key') == k), None) if k else None
        if hit:
            found.append(hit)
    if not found:
        k = active_twin(user)
        hit = next((r for r in runs if r.get('key') == k), None) if k else None
        if hit:
            found.append(hit)
    if not found:
        own = [r for r in runs if r.get('created_by') == _uname(user)]
        if len(own) == 1 or (own and re.search(r"\b(my|the|this|that|our)\s+twin\b|\bthem\b|\bthe room\b", low, re.I)):
            found.append(own[0])
    for r in found[:1]:
        set_active_twin(user, r.get('key'))
    return found[:limit], runs


ACTIVE_TWIN_PREFIX = 'system/digital_twins/active_twin/'


def _active_twin_key_path(user):
    import prometheus_memory as pmm
    return ACTIVE_TWIN_PREFIX + pmm._user_key(user) + '.json'


def active_twin(user):
    """The twin this reader last worked on (six hours), so 'put the
    signal-led plan in the Spend Engine' needs no name in the words."""
    try:
        s3, bucket = _s3()
        doc = json.loads(s3.get_object(Bucket=bucket, Key=_active_twin_key_path(user))['Body'].read().decode('utf-8'))
        if time.time() - float(doc.get('ts') or 0) > ACTIVE_TTL_S:
            return ''
        return str(doc.get('key') or '')
    except Exception:
        return ''


def set_active_twin(user, key):
    try:
        s3, bucket = _s3()
        s3.put_object(Bucket=bucket, Key=_active_twin_key_path(user), Body=json.dumps({'key': key, 'ts': time.time()}).encode('utf-8'), ContentType='application/json')
    except Exception:
        pass


def _which_twin(runs, what='run this'):
    names = [str(r.get('name') or r.get('subject')) for r in runs[:5]]
    if not names:
        return _payload(f"There is no twin in the library yet. Say 'Build a twin of <subject>' and I will build one ({_price_label('digital_twins_iq', None)}, with its IP lifetime value and three runs).",
                        ['Build a twin of Gilmore Girls Hulu viewers'])
    return _payload(f"Which twin should I {what} on? " + ', '.join(names) + '.', [f"The {n} twin" for n in names[:4]])


# --------------------------------------------------------- attachments
def attached_text(uname, tid, limit=6000):
    """The newest file attached to the thread, as text (the budget sheet,
    the script, the treatment); '' when none."""
    if not tid:
        return ''
    try:
        from . import documents as _docs
        s3, bucket = _s3()
        recs = _docs.attachments(uname, tid, s3, bucket)
        if not recs:
            return ''
        return str(_docs.document_text(recs[-1], s3, bucket) or '')[:limit]
    except Exception:
        return ''


def _platforms_in(text):
    """The services named, in the order they appear in the words."""
    low = _low(text)
    found = []
    for p in _PLATFORMS:
        m = re.search(r"\b" + re.escape(p.lower()) + r"\b", low)
        if m and p not in [f[1] for f in found]:
            found.append((m.start(), p))
    return [p for _i, p in sorted(found)]


def _goal_in(text):
    m = _GOAL_RX.search(str(text or ''))
    if not m:
        return None
    val = _num(m.group(1), m.group(2))
    if val <= 0:
        return None
    noun = (m.group(3) or 'viewers').lower()
    noun = {'subs': 'subscribers', 'people': 'viewers'}.get(noun, noun)
    days = 30
    if re.search(r"\bfirst\s+(?:week|7 days)\b", str(text or ''), re.I):
        days = 7
    elif re.search(r"\bfirst\s+(?:quarter|90 days|three months)\b", str(text or ''), re.I):
        days = 90
    return {'metric': noun, 'value': int(val), 'window_days': days}


def greenlight_inputs(text, doc_text=''):
    plats = _platforms_in(text) or _platforms_in(doc_text)
    goal = _goal_in(text) or _goal_in(doc_text)
    title = ''
    m = re.search(r"[\"\u201c]([^\"\u201d]{3,120})[\"\u201d]", str(text or ''))
    if m:
        title = m.group(1)
    logline = ''
    m = re.search(r"(?:logline|idea|concept|pitch|treatment)\s*:\s*(.+)$", str(text or ''), re.I | re.S)
    if m:
        logline = m.group(1).strip()[:600]
    elif doc_text:
        logline = doc_text.strip()[:600]
    inputs = {'platforms': plats}
    if goal:
        inputs['goal'] = goal
    if title:
        inputs['title'] = title
    if logline:
        inputs['logline'] = logline
    if not title and not logline:
        m = re.search(r"\b(?:a new|the next|another)\s+(season|sequel|film|movie|book|game|episode|special)\b", str(text or ''), re.I)
        if m:
            inputs['title'] = f"a new {m.group(1).lower()}"
    return inputs


def launch_inputs(text, doc_text=''):
    from migration.digital_twin_engine import parse_channels
    chans = parse_channels(text) or parse_channels(doc_text)
    weeks = 6
    m = _WEEKS_RX.search(str(text or '')) or _WEEKS_RX.search(str(doc_text or ''))
    if m:
        weeks = int(m.group(1))
    else:
        m = _DAYS_RX.search(str(text or ''))
        if m:
            weeks = max(1, int(round(int(m.group(1)) / 7)))
    assets = re.findall(r"[\"\u201c]([^\"\u201d]{3,80})[\"\u201d]", str(text or ''))
    plan = {'channels': chans, 'window_weeks': weeks, 'assets': assets[:6]}
    goal = _goal_in(text) or _goal_in(doc_text)
    if goal:
        plan['goal'] = goal
    plats = _platforms_in(text)
    if plats:
        plan['platform'] = plats[0]
    return {'plan': plan}


def spend_inputs(text, doc_text=''):
    from migration.digital_twin_engine import parse_channels
    low = _low(text)
    chans = parse_channels(text) or parse_channels(doc_text)
    days = 14
    m = _DAYS_RX.search(str(text or ''))
    if m:
        days = int(m.group(1))
    else:
        m = _WEEKS_RX.search(str(text or ''))
        if m:
            days = int(m.group(1)) * 7
    mode = 'rule' if re.search(r"\b(on the rule|rule mode|auto[- ]?approve|automatically)\b", low) else 'chip'
    rule = {}
    m = _RULE_MAX_RX.search(str(text or ''))
    if m:
        rule['max_daily_shift_pct'] = float(m.group(1) or m.group(2))
        mode = 'rule'
    m = _RULE_FLOOR_RX.search(str(text or ''))
    if m:
        rule['floor_per_channel'] = _num(m.group(1), m.group(2))
        mode = 'rule'
    plan_label = 'Signal-led' if 'signal' in low else 'Minimum' if 'minimum' in low else 'As allocated' if 'allocated' in low else ''
    goal = _goal_in(text)
    plan = {'channels': chans, 'window': {'days': days}}
    if goal:
        plan['goal'] = goal
    return {'plan': plan, 'mode': mode, 'rule': rule, 'plan_label': plan_label}


# ------------------------------------------------- focus group iq (panel)
def _active_key(user):
    import prometheus_memory as pmm
    return ACTIVE_PREFIX + pmm._user_key(user) + '.json'


def active_session(user):
    try:
        s3, bucket = _s3()
        doc = json.loads(s3.get_object(Bucket=bucket, Key=_active_key(user))['Body'].read().decode('utf-8'))
        if time.time() - float(doc.get('ts') or 0) > ACTIVE_TTL_S:
            return None
        return doc
    except Exception:
        return None


def set_active_session(user, key, sid):
    try:
        s3, bucket = _s3()
        s3.put_object(Bucket=bucket, Key=_active_key(user), Body=json.dumps({'key': key, 'sid': sid, 'ts': time.time()}).encode('utf-8'), ContentType='application/json')
    except Exception:
        pass


def voices_call(claude_json, ctx):
    """One short model call for the voices and the read; the panel
    engine runs it under the time budget and falls back on its own."""
    user = json.dumps({'question': ctx.get('question'), 'kind': ctx.get('kind'), 'options': ctx.get('options'), 'distribution': ctx.get('distribution'),
                       'read': ctx.get('read'), 'roster': ctx.get('roster'), 'twin': ctx.get('twin'), 'traits': ctx.get('traits'),
                       'last_question': ctx.get('last_question'), 'stimulus': ctx.get('stimulus')}, default=str)[:6000]
    got = claude_json(VOICES_SYSTEM_PROMPT, user, max_tokens=600, temperature=0.8, surface='twin_panel_voices')
    return got if isinstance(got, dict) else None


def _voices_fn():
    if not host.has('claude_data'):
        return None
    cj = host.claude_data
    return lambda ctx: voices_call(cj, ctx)


def panel_segments(text, twins_found):
    """Segments from the words: 'women 25 to 44', 'lapsed Netflix
    subscribers in Dallas', split on ' and ' / ';' after the twin
    names; one per twin when none are stated."""
    t = str(text or '')
    segs = []
    m = re.search(r"\b(?:cohorts?|segments?|groups?)\s*(?::|of|=)\s*(.+?)(?:\.\s|$|\sand ask\b|\sthen ask\b)", t, re.I)
    body = m.group(1) if m else ''
    if not body:
        m = re.search(r"\btwin\b\s*[,:]\s*(.+?)(?:\.\s|$|\sand ask\b|\sthen ask\b|\bask\b)", t, re.I)
        body = m.group(1) if m else ''
    for part in re.split(r"\s*;\s*|\s+and\s+(?=(?:women|men|lapsed|new|avid|casual|gen|millennials|people|those|anyone))", body):
        part = part.strip(' ,.')
        if part and re.search(r"\b(women|men|female|male|\d{2}\s*(?:to|-)\s*\d{2}|lapsed|new|avid|casual|gen z|millennials|boomers|in [A-Z])", part, re.I):
            segs.append({'label': part[:120]})
    if not segs:
        return None
    if twins_found:
        for i, s in enumerate(segs):
            s['twin'] = twins_found[min(i, len(twins_found) - 1)].get('key')
    return segs


def panel_question(text):
    """The question inside 'open a room with X and ask whether they would
    pay $29': the part after ask / whether / if; else the text itself
    for a follow-up."""
    t = ' '.join(str(text or '').split())
    m = re.search(r"\bask\s+(?:them|the\s+room|the\s+twins?|the\s+group|the\s+panel|the\s+focus\s+group)?\s*(?:whether|if|:)?\s*(.+)$", t, re.I)
    if m:
        q = m.group(1).strip(' .')
        q = re.sub(r"^(?:they|them)\s+would\b", 'would they', q, flags=re.I)
        q = re.sub(r"^(?:would|do|did|are|is|will|can|have)\s+they\b", lambda mm: mm.group(0).replace('they', 'you'), q, flags=re.I)
        q = re.sub(r"\btheir\b", 'your', q, flags=re.I)
        q = re.sub(r"\bthey\b", 'you', q, flags=re.I)
        q = re.sub(r"\bthem\b", 'you', q, flags=re.I)
        return (q[0].upper() + q[1:] + ('?' if not q.endswith('?') else '')) if q else ''
    m = re.search(r"^\s*re-?ask\s+(?:at|with|for)\s+(.+)$", t, re.I)
    if m:
        return ''
    return re.sub(r"\bthey\b", 'you', re.sub(r"\btheir\b", 'your', t, flags=re.I), flags=re.I)


def _stimulus(text, last=None):
    st = {}
    m = _PRICE_RX.search(str(text or ''))
    if m and re.search(r"^\s*re-?ask\b", str(text or ''), re.I):
        st['price'] = _num(m.group(1), m.group(2))
    m = re.search(r"\bwith\s+([A-Z][\w.' -]{2,40}?)\s+(?:attached|cast|as the lead|hosting|starring)\b", str(text or ''))
    if m:
        st['cast'] = m.group(1)
    plats = _platforms_in(text)
    if plats and re.search(r"\bre-?ask\b|\binstead\b|\bon\s+(?:netflix|hulu|peacock|max)\b", str(text or ''), re.I):
        st['platform'] = plats[0]
    return st


def _reask_text(text, last, session=None):
    """'Re-ask at $19' becomes the most recent price question with the
    price swapped; a platform re-ask swaps the service on the last
    question that named one (else adds it)."""
    qs = list((session or {}).get('questions') or ([last] if last else []))
    m = _PRICE_RX.search(str(text or ''))
    if m:
        for q in reversed(qs):
            lq = str((q or {}).get('q') or '')
            if _PRICE_RX.search(lq):
                return _PRICE_RX.sub(f"${m.group(1)}", lq, count=1)
    lq = str((last or {}).get('q') or '')
    if not lq:
        return str(text or '')
    if m:
        return lq
    plats = _platforms_in(text)
    if plats:
        for p in _PLATFORMS:
            if re.search(r"\b" + re.escape(p.lower()) + r"\b", lq.lower()):
                return re.sub(re.escape(p), plats[0], lq, flags=re.I)
        return lq + f" on {plats[0]}"
    return lq


def panel_reply(payload, session, rec, key, user):
    from migration import digital_twin_panel as P
    lines = P.session_lines(session, rec)
    asked = len(session.get('questions') or [])
    paid = int(session.get('questions_paid') or 12)
    tail = f" ({asked} of {paid} questions in this session used.)" if asked >= paid - 2 else ''
    reply = '\n'.join(lines) + tail
    chips = ['Ask them why', 'What would change their mind?', 'Open Focus Group IQ']
    if rec and rec.get('kind') == 'price' and rec.get('price'):
        try:
            chips.insert(1, f"Re-ask at ${max(1, int(float(rec['price']) * 0.7))}")
        except Exception:
            pass
    return _payload(reply, chips, panel_session={'key': key, 'sid': session.get('id'), 'question_id': (rec or {}).get('id')},
                    open_view='digitalTwinsIQ', twin_key=key, action_hint={'view': 'digitalTwinsIQ', 'tab': 'panel', 'twin_key': key, 'session_id': session.get('id')})


def run_panel(text, body, history, user, ppu=None):
    """Open a room, or ask the active one. None when the words are not a
    panel order."""
    from migration import digital_twins as DT
    from migration import digital_twin_panel as P
    s3, _b = _s3()
    uname = _uname(user)
    low = _low(text)
    opening = bool(_PANEL_OPEN_RX.search(low))
    active = active_session(user)
    if not opening:
        if not active or not _PANEL_ASK_RX.search(str(text or '').strip()):
            return None
        payload = DT.load_twin(s3, active['key'])
        sess = next((s for s in (payload or {}).get('panel_sessions') or [] if s.get('id') == active['sid']), None) if payload else None
        if not payload or not sess:
            return None
        last = sess['questions'][-1] if sess.get('questions') else None
        q = _reask_text(text, last, sess) if re.search(r"^\s*re-?ask\b", str(text or ''), re.I) else panel_question(text)
        if not q:
            return _payload('What should I ask the room?', ['Would you watch a new season?', 'Would you pay $29 for the box?', 'Show of hands, who watched season 1?'])
        asked = len(sess.get('questions') or [])
        paid = int(sess.get('questions_paid') or DT.PANEL_QUESTIONS_INCLUDED)
        if asked >= paid:
            funds = _charge(user, 'digital_twin_panel_more', f"Focus Group IQ, {DT.PANEL_QUESTIONS_INCLUDED} more questions: {payload['meta'].get('name')}", f"{sess['id']}-{asked}")
            if funds is not None:
                return funds
            sess['questions_paid'] = paid + DT.PANEL_QUESTIONS_INCLUDED
        twins_by_key = {active['key']: payload}
        for tk in sess.get('twin_keys') or []:
            if tk not in twins_by_key:
                t = DT.load_twin(s3, tk)
                if t:
                    twins_by_key[tk] = t
        rec = P.ask(sess, twins_by_key, q, stimulus=_stimulus(text, last), voices_fn=_voices_fn(), asked_by=uname)
        DT.save_twin(s3, active['key'], payload)
        set_active_session(user, active['key'], sess['id'])
        try:
            host.ask_hint(route='panel_lab', outcome='answered', subject=payload['meta'].get('name'))
        except Exception:
            pass
        return panel_reply(payload, sess, rec, active['key'], user)
    found, runs = resolve_twins(text, body, history, user, limit=3)
    if not found:
        return _which_twin(runs, 'open the room')
    key = found[0]['key']
    payload = DT.load_twin(s3, key)
    if not payload:
        return _which_twin(runs, 'open the room')
    twins = [payload]
    for r in found[1:]:
        t = DT.load_twin(s3, r['key'])
        if t:
            twins.append(t)
    in_room = P.DEFAULT_IN_ROOM
    m = _IN_ROOM_RX.search(str(text or ''))
    if m:
        in_room = int(m.group(1))
    sid = uuid.uuid4().hex[:10]
    funds = _charge(user, 'digital_twin_panel', f"Focus Group IQ session: {payload['meta'].get('name')}", sid)
    if funds is not None:
        return funds
    sess = P.open_session(twins, segments=panel_segments(text, found), in_room=in_room, created_by=uname, session_id=sid)
    sess['twin_keys'] = [str((t.get('meta') or {}).get('key') or '') for t in twins]
    sess['questions_paid'] = DT.PANEL_QUESTIONS_INCLUDED
    payload.setdefault('panel_sessions', []).insert(0, sess)
    q = panel_question(text) if re.search(r"\bask\b", low) else ''
    rec = None
    if q:
        rec = P.ask(sess, {str((t.get('meta') or {}).get('key') or ''): t for t in twins}, q, stimulus=_stimulus(text), voices_fn=_voices_fn(), asked_by=uname)
    DT.save_twin(s3, key, payload)
    set_active_session(user, key, sid)
    try:
        host.ask_hint(route='panel_lab', outcome='opened', subject=payload['meta'].get('name'))
    except Exception:
        pass
    if rec:
        return panel_reply(payload, sess, rec, key, user)
    g = sess['group']
    roster = ', '.join(f"{r['name']} ({r['age']}, {r['city']})" for r in g['roster'][:8])
    return _payload(f"The room is open: {g['in_room']} twins standing for {g['size_people']:,} people, "
                    + '; '.join(f"{s['label']} {s['share_pct']}%" for s in g['segments']) + f". In the room: {roster}. What should I ask them?",
                    ['Would you watch a new season?', 'Would you pay $29 for the box?', 'Show of hands, who watched season 1?', 'Open Focus Group IQ'],
                    panel_session={'key': key, 'sid': sid}, open_view='digitalTwinsIQ', twin_key=key, action_hint={'view': 'digitalTwinsIQ', 'tab': 'panel', 'twin_key': key, 'session_id': sid})


# ------------------------------------------------------------ the labs
def run_lab(kind, text, body, history, user, ppu, thread_id=''):
    from migration import digital_twins as DT
    from migration.digital_twin_engine import KIND_LABEL
    from . import digital_twin_job as J
    s3, _b = _s3()
    uname = _uname(user)
    found, runs = resolve_twins(text, body, history, user, limit=2 if kind == 'compare' else 1)
    if not found:
        return _which_twin(runs, f"run {KIND_LABEL.get(kind, kind)}")
    key = found[0]['key']
    name = str(found[0].get('name') or found[0].get('subject'))
    doc_text = attached_text(uname, thread_id)
    if kind == 'spend':
        payload = DT.load_twin(s3, key)
        if not payload:
            return _which_twin(runs, 'activate the plan')
        si = spend_inputs(text, doc_text)
        plan, source = si['plan'], 'manual'
        if not plan.get('channels'):
            launch = next((r for r in payload.get('runs') or [] if r.get('kind') == 'launch'), None)
            if launch:
                label = si.get('plan_label') or 'Signal-led'
                pl = next((p for p in (launch.get('outputs') or {}).get('plans') or [] if str(p.get('label') or '').lower().startswith(label.lower()[:6])), None)
                if pl:
                    plan = {'channels': pl.get('channels'), 'window': {'days': int((launch.get('outputs') or {}).get('window_weeks') or 2) * 7},
                            'goal': (launch.get('outputs') or {}).get('goal'), 'assets': (launch.get('inputs') or {}).get('assets') or [], 'platform': (launch.get('outputs') or {}).get('platform')}
                    source = 'launch'
        if not plan.get('channels'):
            return _payload(f"Which plan should the Spend Engine run on the {name} twin? Paste the channels with spend (Meta $60K, YouTube $40K over 14 days) or attach the budget sheet, and say chip or rule mode (for example, on the rule: max 15% a day).",
                            ['Meta $60K, YouTube $40K over 14 days, approve by chip', 'Run Launch Lab first'])
        from migration import digital_twin_spend as SP
        plan_id = uuid.uuid4().hex[:10]
        funds = _charge(user, 'digital_twin_spend', f"Spend Engine plan: {name}", plan_id)
        if funds is not None:
            return funds
        try:
            doc = SP.activate_plan(payload, plan, mode=si['mode'], rule=si['rule'] or None, source=source, created_by=uname, plan_id=plan_id)
        except ValueError as e:
            return _payload(str(e))
        payload.setdefault('labs', {}).setdefault('spend', []).insert(0, doc)
        payload.setdefault('runs', []).insert(0, {'id': uuid.uuid4().hex[:10], 'kind': 'spend', 'label': 'Spend Engine', 'question': f"Run the plan forward: {doc.get('label')}",
                                                 'inputs': {'plan_id': plan_id, 'mode': doc.get('mode')}, 'outputs': {'expected': (doc.get('ledger') or {}).get('signal_led_outcome'), 'noun': 'first actions', 'plan_id': plan_id, 'ledger': doc.get('ledger')},
                                                 'drivers': SP.spend_lines(doc)[:3], 'comparison': {}, 'recommendation': (doc.get('ledger') or {}).get('read') or '',
                                                 'read': {'tldr': SP.spend_lines(doc)[:3], 'reads': [], 'what_to_do': [(doc.get('holdout') or {}).get('lift_read') or '']},
                                                 'created_by': uname, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        DT.save_twin(s3, key, payload)
        lines = SP.spend_lines(doc)
        pend = [(d['day'], mv) for d in doc.get('days') or [] for mv in d.get('moves') or [] if not mv.get('approved')][:2]
        chips = [f"Approve the day {d} move" for d, _mv in pend] + ['Export the moves', OPEN_CHIP]
        try:
            host.ask_hint(route='spend_engine', outcome='activated', subject=name)
        except Exception:
            pass
        return _payload(f"The Spend Engine is running the plan on the {name} twin. " + ' '.join(lines[:4]), chips, open_view='digitalTwinsIQ', twin_key=key,
                        action_hint={'view': 'digitalTwinsIQ', 'tab': 'spend', 'twin_key': key, 'plan_id': plan_id})
    if kind == 'greenlight':
        scenario = {'kind': 'greenlight', 'question': str(text)[:300], 'inputs': greenlight_inputs(text, doc_text)}
    elif kind == 'launch':
        li = launch_inputs(text, doc_text)
        if not li['plan'].get('channels'):
            return _payload(f"What is the plan for the {name} twin? Paste the channels with spend and the window (Meta $120K, Google $80K, TikTok $40K over 6 weeks, goal 3M viewers) or attach the budget sheet.",
                            ['Meta $120K, Google $80K, TikTok $40K over 6 weeks, goal 3M viewers'])
        scenario = {'kind': 'launch', 'question': str(text)[:300], 'inputs': li}
    elif kind == 'compare':
        if len(found) < 2:
            return _which_twin(runs, 'compare')
        inner = question_kind(re.sub(r"\btwins?\b", '', text)) or 'greenlight'
        inputs = greenlight_inputs(text, doc_text) if inner == 'greenlight' else {}
        inputs['scenario_kind'] = inner
        scenario = {'kind': 'compare', 'question': str(text)[:300], 'inputs': inputs}
    else:
        scenario = {'kind': kind, 'question': str(text)[:300], 'inputs': _inputs_for(kind, text)}
    job_id = uuid.uuid4().hex[:12]
    funds = _charge(user, 'digital_twin_run', f"Digital Twin run ({kind}): {name}", job_id)
    if funds is not None:
        return funds
    J.start('twin_run', job_id, uname, {'op': 'run', 'key': key, 'scenario': scenario, 'other_key': found[1]['key'] if (kind == 'compare' and len(found) > 1) else ''}, thread_id=thread_id, extras=ppu or {})
    tab = {'greenlight': 'greenlight', 'launch': 'launch', 'compare': 'greenlight'}.get(kind, 'twins')
    return _payload(J.run_promise(kind, name), [OPEN_CHIP], twin_job_id=job_id, job_id=job_id, job_type='twin_run', open_view='digitalTwinsIQ', twin_key=key,
                    action_hint={'view': 'digitalTwinsIQ', 'tab': tab, 'twin_key': key, 'job_id': job_id})


def _inputs_for(kind, text):
    t = str(text or '')
    low = _low(t)
    if kind == 'pricing':
        prices = [_num(m.group(1), m.group(2)) for m in _PRICE_RX.finditer(t)]
        out = {}
        if len(prices) >= 2:
            out['current_price'], out['new_price'] = prices[0], prices[1]
        elif prices:
            out['new_price'] = prices[0]
        m = re.search(r"\b(bundle|annual|ad-supported|with ads|family plan)\b", low)
        if m:
            out['packaging'] = m.group(1)
        return out
    if kind == 'content':
        m = re.search(r"\bseason\s+(\d+)\b", low)
        return {'kind': 'season' if 'season' in low else 'sequel' if 'sequel' in low else 'episode', 'number': int(m.group(1)) if m else 2, 'platform': (_platforms_in(t) or [''])[0]}
    if kind == 'platform_move':
        plats = _platforms_in(t)
        out = {}
        if len(plats) >= 2:
            out['from'], out['to'] = plats[0], plats[1]
        elif plats:
            out['to'] = plats[0]
        m = re.search(r"(\d{1,3})\s*days?\s+(?:shorter|earlier|sooner)", low)
        if m:
            out['window_change_days'] = int(m.group(1))
        return out
    if kind == 'partnership':
        m = re.search(r"\b([A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,2})\s+x\s+", t) or re.search(r"\bwith\s+([A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,2})", t)
        return {'brand': m.group(1) if m else 'the brand'}
    if kind == 'talent_attach':
        m = re.search(r"\b(?:attach(?:ing)?|cast(?:ing)?|with|starring|feature)\s+([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,2})", t)
        return {'talent': m.group(1) if m else 'the name', 'role': 'host' if 'host' in low else 'lead'}
    if kind == 'merch_drop':
        m = re.search(r"\b(?:drop|sell|selling|launch|launching)\s+(?:a|an|the)?\s*([\w' -]{3,50}?)\s+(?:at|for)\s+\$", t, re.I)
        pr = _PRICE_RX.search(t)
        return {'product': m.group(1).strip() if m else 'the drop', 'price': _num(pr.group(1), pr.group(2)) if pr else 39.0}
    return greenlight_inputs(t)


# ------------------------------------------------------- small lanes
def catalog_reply(text, user):
    from migration import digital_twins as DT
    found, runs = resolve_twins(text, {}, [], user, limit=1, strict=True)
    low = _low(text)
    names = [str(r.get('name') or r.get('subject')) for r in runs[:10]]
    if re.search(r"\b(which|what|list|show me|all)\b", low) and not found:
        if not names:
            return _payload("No twins in the library yet. Say 'Build a twin of <subject>' and I will build the first one.", ['Build a twin of Gilmore Girls Hulu viewers'])
        return _payload(f"{len(runs)} twin{'s' if len(runs) != 1 else ''} in the library: " + ', '.join(names) + '. Name one and I will open it, run a lab on it, or open a room with it.',
                        [f"Open the {n} twin" for n in names[:3]])
    if found:
        r = found[0]
        from migration.digital_twin_engine import human_people, human_money
        line = f"Yes: the {r.get('name')} twin is in the library, {human_people(r.get('size_us'))} people in the US, built {str(r.get('created_at') or '')[:10]}"
        if r.get('ltv_5y'):
            line += f", worth {human_money(r['ltv_5y'])} over five years as IP"
        line += f". It carries {r.get('runs') or 0} run{'s' if (r.get('runs') or 0) != 1 else ''}. Ask it anything, run a lab on it, or open a room with it."
        return _payload(line, [f"Run Greenlight Lab on the {r.get('name')} twin", f"Open a focus group with the {r.get('name')} twin", OPEN_CHIP], twin_key=r.get('key'), open_view='digitalTwinsIQ',
                        action_hint={'view': 'digitalTwinsIQ', 'tab': 'twins', 'twin_key': r.get('key')})
    subj = re.sub(r".*\btwins?\s+(?:of|for|on)\s+", '', str(text or ''), flags=re.I).strip(' ?.')
    return _payload(f"No twin for {subj or 'that'} yet. Say 'Build a twin of {subj or '<subject>'}' and I will build it ({_price_label('digital_twins_iq', _uname(user))}, with its IP lifetime value and three runs).",
                    [f"Build a twin of {subj}"] if subj else ['Build a twin of Gilmore Girls Hulu viewers'])


def status_reply(user):
    from . import digital_twin_job as J
    s3, bucket = _s3()
    uname = _uname(user)
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=J.JOB_PREFIX)
        keys = sorted([o['Key'] for o in resp.get('Contents') or []], reverse=True)[:60]
    except Exception:
        keys = []
    mine = []
    for k in keys:
        rec = J.status_read(k.rsplit('/', 1)[-1][:-5])
        if rec and str(rec.get('user') or '') == uname:
            mine.append(rec)
    mine.sort(key=lambda r: float(r.get('updated_at') or 0), reverse=True)
    if not mine:
        return _payload('Nothing of yours is building in Digital Twins IQ right now. Say "Build a twin of <subject>" to start one.')
    running = [r for r in mine if str(r.get('status') or '') not in ('done', 'error', 'failed', 'cancelled')]
    builds = [r for r in mine if str(r.get('op') or (r.get('inputs') or {}).get('op') or 'build') == 'build']
    r = running[0] if running else (builds[0] if builds else mine[0])
    st = str(r.get('status') or '')
    subj = str(r.get('subject') or (r.get('inputs') or {}).get('subject') or 'your twin')
    if st == 'done':
        return _payload(r.get('ready_message') or f"{subj} is ready on the Twins tab.", [OPEN_CHIP], twin_key=r.get('s3_key'), open_view='digitalTwinsIQ')
    if st in ('error', 'failed'):
        return _payload(f"{subj} did not finish. Nothing more is charged; say 'Build a twin of {subj}' and it runs fresh.")
    return _payload(f"{subj} is still building ({r.get('stage') or st}). It lands on the Twins tab in a few minutes; I will confirm here.")


def _poll_payload(rec):
    return rec


# ---------------------------------------------------------- the hook
def analyze_hook(text, body, history, user, ppu, uname, g):
    """Serve a Digital Twins IQ ask on the analyze surface. Returns the
    reply payload, or None when the ask is not this product's. ``g`` is
    the legacy chat module's globals (its intake helpers and host)."""
    try:
        from migration import digital_twins as DT
        from . import digital_twin_job as J
        t = str(text or '').strip()
        low = _low(t)
        thread_id = str((body or {}).get('thread_id') or '')
        # 1. the confirm: the echoed card, the stashed brief, or the run chip
        confirm = (body or {}).get('twin_confirm') or g['_pm_pending_intake']('pick', user, FLOW, t)
        if isinstance(confirm, dict) and confirm.get('subject'):
            job_id = uuid.uuid4().hex[:12]
            funds = _charge(user, 'digital_twins_iq', f"Digital Twin: {confirm.get('subject')}", job_id)
            if funds is not None:
                return funds
            J.start('twin_build', job_id, uname, {**{k: confirm.get(k) for k in ('subject', 'subject_kind', 'purpose', 'platform_home', 'format', 'window')}, 'op': 'build', 'ask_text': t},
                    thread_id=thread_id, extras=ppu or {})
            return _payload(J.build_promise(confirm.get('subject')), [], twin_job_id=job_id, job_id=job_id, job_type='twin_build', open_view='digitalTwinsIQ')
        # 2. the collecting intake
        if ((body or {}).get('twin_inputs') or intake_armed(history)) and not g['_pm_intake_breakout'](t) and not twin_intent(t):
            if re.match(r"^\s*(never ?mind|no( thanks?)?|not now|cancel( (that|it))?)[.! ]*$", low):
                return _payload('No problem, nothing was charged. Ask me anything else when ready.')
            return g['_pm_intake_turn'](FLOW, t, body, history, user, ppu, first=False)
        if not twin_intent(t) and not (active_session(user) and _PANEL_ASK_RX.search(t)) and not (DT.is_how_to_ask(t)):
            # a free question on an open twin that maps to a run
            key = _open_twin_key(body)
            if key and question_kind(t) and (low.endswith('?') or re.match(r"^(what|how|would|will|does|do|is|are|can|should)\b", low)):
                return run_lab(question_kind(t), t, body, history, user, ppu, thread_id)
            return None
        # 3. the directions
        if DT.is_how_to_ask(t) or re.search(r"\bhow (?:do|does|can) (?:i|we|it) (?:use|run|open|work)\b.*\b(twin|greenlight|launch lab|spend engine|panel lab|focus group)", low):
            try:
                from prometheus.legacy.watch import _pm_usd
                pf = lambda k, d: _pm_usd(k, d, uname)
            except Exception:
                pf = None
            return _payload(DT.how_to_text(t, pf), ['Build a twin of Gilmore Girls Hulu viewers', 'Open a focus group with the Gilmore Girls twin', OPEN_CHIP], open_view='digitalTwinsIQ',
                            action_hint={'view': 'digitalTwinsIQ', 'tab': 'howto'})
        # 4. status
        if _STATUS_RX.search(low):
            return status_reply(user)
        # 5. the panel
        pr = run_panel(t, body, history, user, ppu)
        if pr is not None:
            return pr
        # 6. the labs on a twin
        lk = lab_kind(t)
        if lk and lk != 'panel':
            return run_lab(lk, t, body, history, user, ppu, thread_id)
        # 7. the catalog: a question about what we hold, never a build order
        if _CATALOG_RX.search(low) and (low.endswith('?') or re.match(r"^(do|does|is|are|which|what|list|show)\b", low)) \
                and not re.search(r"\b(build|make|create|spin up|start|run|pull|set up)\b", low):
            return catalog_reply(t, user)
        # 8. a question that names a twin and maps to a run
        if 'twin' in low and question_kind(t) and not _BUILD_RX.search(low):
            return run_lab(question_kind(t), t, body, history, user, ppu, thread_id)
        # 9. a build order: the intake
        if _BUILD_RX.search(low) or re.search(r"\bdigital twins? iq\b", low):
            return g['_pm_intake_turn'](FLOW, t, body, history, user, ppu, first=True)
        return None
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------------------------------------ the deck
def deck_from_twin(text, ctx, user, uname, s3, bucket):
    """(ctx, material) when the words or the open page name a twin and
    the ask is for a deck of it; None otherwise."""
    try:
        from migration import digital_twins as DT
        low = _low(text)
        key = ''
        try:
            vc = (ctx or {}).get('view_context') or {}
            if str(vc.get('view_id') or '') == 'digitalTwinsIQ':
                key = str((vc.get('data') or {}).get('key') or '')
        except Exception:
            key = ''
        if not key:
            if 'twin' not in low:
                return None
            runs = DT.visible_runs(DT.list_runs(s3), user)
            hit = DT.find_twin(runs, text)
            if not hit and re.search(r"\b(this|the|my|our)\s+twin\b", low):
                own = [r for r in runs if r.get('created_by') == uname]
                hit = own[0] if own else None
            key = str((hit or {}).get('key') or '')
        if not key:
            return None
        payload = DT.load_twin(s3, key)
        if not payload:
            return None
        c = DT.deck_context(None, payload)
        c['view_context']['data']['key'] = key
        return c, c['view_context']['material']
    except Exception:
        traceback.print_exc()
        return None


__all__ = ['twin_intent', 'lab_kind', 'question_kind', 'analyze_hook', 'parse_twin', 'keyword_parse_twin', 'twin_inputs_complete', 'propose_twin_field',
           'confirm_reply', 'flow_table_entry', 'ui_entry', 'intake_armed', 'resolve_twins', 'greenlight_inputs', 'launch_inputs', 'spend_inputs',
           'panel_segments', 'panel_question', 'run_panel', 'run_lab', 'catalog_reply', 'status_reply', 'voices_call', 'deck_from_twin',
           'active_session', 'set_active_session', 'FLOW', 'RUN_LABEL', 'ASK_COPY', 'OPEN_CHIP']
