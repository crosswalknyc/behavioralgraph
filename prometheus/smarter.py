"""Smaller contracts from the 2026-10-13 audit ("find other weaknesses and
how we can make this smarter"), kept together so the legacy chat module
and the service each need a line or two:

  prune_rulebook        the brief-drafting rulebook carries only the
                        sections an ask needs (speed)
  draft_output_ceiling  a plain ask gets a short output budget; a list of
                        subjects keeps the long one
  followup_lane         a short follow-up inherits the previous turn's
                        lane and subject
  capability_ask        "is there a way to", "can we", "how do we set up"
                        are answered with what we can do, never the opener
  negated_build         "don't build", "not a profile", "answer this from"
                        force answer mode
  offer_declined        "not now", "no thanks" close an offer
  brief_from_history    a run order with no stashed brief re-derives it
                        from the last confirm in the thread
  adjacent_read         the one most useful read we already hold on the
                        same subject, offered as a chip
  bind_to_published     a stated figure within a published one's family
                        is replaced by the published one (self-grade)
  thread_replay         the same question asked again in a thread gets the
                        same answer, with when it was given
"""
from __future__ import annotations

import datetime as dt
import json
import re
import traceback

# ------------------------------------------------------------ rulebook
# a section heading: a line opening with two or more ALL-CAPS words (10+
# chars) then punctuation, a newline, or a lowercase continuation
_SECTION_RX = re.compile(r"(?m)^(?=(?:[A-Z][A-Z0-9/&'.,#-]*[ -]){1,7}[A-Z][A-Z0-9/&'.,#-]{1,}(?:\s*[(:=-]|\n|\s+[a-z]))(?=[A-Z][A-Z0-9 /&().,#'-]{9,})")
_SUBIQ_RX = re.compile(r"\b(subscriber iq|sub iq|subiq|tracker|signups?|reactivat|churn|season \d|episode)", re.I)
_LIST_RX = re.compile(r"(,|\band\b|\bor\b|\beach\b|\bboth\b|\ball of\b|\bthe following\b)", re.I)
_PLATFORM_RX = re.compile(r"\b(netflix|prime video|amazon|hulu|disney|max|hbo|peacock|paramount|apple tv|starz|roku|tubi|pluto|youtube|spotify|tiktok|instagram|est|tvod|pvod|svod|avod|fast)\b", re.I)
_AUTO_RX = re.compile(r"\b(car|auto|automobile|truck|suv|ford|toyota|honda|chevy|chevrolet|tesla|bmw|lincoln|jeep|dealer)\b", re.I)
_GENDER_RX = re.compile(r"\b(female|male|women|woman|men|man|girls?|boys?|moms?|dads?|mothers?|fathers?|ladies|guys|gender|she|he|her|his)\b", re.I)
_EVENT_RX = re.compile(r"\b(premiere|launch|opening|tour|release[sd]?|finale|season \d|episode|concert|game day|draft|super bowl|world series|playoffs?|awards?|debut|drop|campaign|event|week of|weekend of|since|after|before|during|around)\b", re.I)
_KIDS_RX = re.compile(r"\b(kids?|children|child|toddlers?|babies|baby|infants?|toys?|preschool|parents?|family|families|teens?|tweens?|youth)\b", re.I)
_WINDOW_RX = re.compile(r"\b(trailing|last|past|since|ytd|year|years|month|months|week|weeks|day|days|quarter|q[1-4]|fy\d*|fiscal|recent|current|now|today|window|period|between|through|until|from|to date|h[12]|19\d\d|20\d\d|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b", re.I)


def _window_phrasing(t, r=None):
    return bool(_WINDOW_RX.search(t)) or bool(_EVENT_RX.search(t)) or bool((r or {}).get('window')) or 'custom_window' in ((r or {}).get('flags') or [])


PRUNABLE = {
    'SUBSCRIBER IQ REQUESTS': lambda t, c, r=None: bool(_SUBIQ_RX.search(t)),
    'ANY-OF / ALL-OF TITLE LISTS': lambda t, c, r=None: bool(_LIST_RX.search(t)),
    'PARENT-SELECTION TIEBREAK': lambda t, c, r=None: bool(c),
    'UNIVERSE-QUALIFIER COMPATIBILITY': lambda t, c, r=None: bool(c),
    'SUBJECT-IDENTITY COMPATIBILITY': lambda t, c, r=None: bool(c),
    'PLATFORM SCOPE': lambda t, c, r=None: bool(_PLATFORM_RX.search(t)) or bool((r or {}).get('platform')),
    'CAR-BRAND SELF-PINS': lambda t, c, r=None: bool(_AUTO_RX.search(t)),
    'PURCHASE-ON-A-PLATFORM': lambda t, c, r=None: bool(_PLATFORM_RX.search(t)) or bool(re.search(r"\b(buy|purchas|shop|consumer)", t, re.I)),
    # Reader-aware (audit item 6, 2026-10-14): sections the reading says cannot apply
    'GENDER SKEW VOCABULARY': lambda t, c, r=None: bool(_GENDER_RX.search(t)),
    'EVENT-SCOPED WINDOWS': lambda t, c, r=None: bool(_EVENT_RX.search(t)) or bool((r or {}).get('window')) or 'custom_window' in ((r or {}).get('flags') or []),
    "KIDS' PRODUCTS": lambda t, c, r=None: bool(_KIDS_RX.search(t)),
    'SUBJECT IDENTITY RESOLUTION': lambda t, c, r=None: not _subject_resolved(r),
    # the diet toward 5,000 tokens (2026-10-14): candidate-only matching rules go
    # when there is no candidate; verification goes when the library already
    # holds the subject; the two window rules go when nothing names a window
    'NAME MATCHING IS CASE': lambda t, c, r=None: bool(c),
    'CORPORATE / LEGAL FORM ALIASES': lambda t, c, r=None: bool(c),
    'CURRENT-FILE PHRASING': lambda t, c, r=None: bool(c),
    'SUBJECT VERIFICATION': lambda t, c, r=None: not _subject_resolved(r),
    'RELATIVE WINDOWS BIND': lambda t, c, r=None: _window_phrasing(t, r),
    'DATE RANGE EXPLICIT FLAG': lambda t, c, r=None: _window_phrasing(t, r),
}

# ---- inside-section diet (2026-10-14). SEMANTIC GUARDS is fifteen numbered
# guards, each bound to a phrasing family; a guard whose family is absent
# from the ask (and from the reading's flags) is dead weight. The spec step
# re-checks every guard server-side, so a missed trigger degrades to the
# pre-prompt behaviour, never to a silent default.
_AGE_RX = re.compile(r"\b(gen ?z|gen ?x|millennials?|boomers?|zoomers?|\d{2}\s*(?:-|to)\s*\d{2}|under \d{2}|over \d{2}|\d{2}\+|ages?\b|teens?|seniors?|young|older|adults?)\b", re.I)
_GEO_RX = re.compile(r"\b(in|from|across|around) [A-Z][a-z]+|\b(dma|market|city|cities|state|county|la|nyc|new york|los angeles|chicago|dallas|houston|miami|atlanta|boston|philly|philadelphia|phoenix|seattle|denver|detroit|tampa|orlando|texas|florida|california|ohio)\b", re.I)
_COUNTRY_RX = re.compile(r"\b(uk|u\.k\.|britain|british|england|english market|ireland|irish|canada|canadian|mexico|mexican|germany|german|france|french|spain|spanish|italy|italian|australia|australian|japan|japanese|korea|korean|india|indian|brazil|brazilian|europe|european|latam|emea|apac|international|global|worldwide|overseas|outside the us|non-us)\b", re.I)
GUARD_TRIGGERS = {
    1: lambda t, r: bool(re.search(r"\b(don'?t|do not|doesn'?t|without|exclud\w*|except|never (?:watched|used|bought|subscribed)|non-?\w+|not (?:a |on |already )?(?:subscriber|member|customer|user)s?|no longer|lack|aren'?t)\b", t, re.I)) or 'negated_build' in _flags(r),
    2: lambda t, r: bool(re.search(r"\b(former|lapsed|cancel\w*|churn\w*|stopped|quit|left|ex-|used to|no longer|dormant|leavers?|unsubscrib\w*|win-?back|lost)\b", t, re.I)),
    3: lambda t, r: bool(re.search(r"\b(then|after|before|came to|went on to|followed by|switch\w*|moved|migrat\w*|convert\w*|journey)\b|->", t, re.I)),
    4: lambda t, r: bool(re.search(r"\b(q[1-4]|fy ?\d*|fiscal|quarter)\b", t, re.I)) or 'custom_window' in _flags(r),
    5: lambda t, r: _window_phrasing(t, r),
    6: lambda t, r: bool(_COUNTRY_RX.search(t)),
    7: lambda t, r: bool(re.search(r"\b(southeast|southwest|midwest|northeast|northwest|pacific northwest|sun ?belt|new england|rust belt|bible belt|the south|the north|east coast|west coast|region\w*|rural|small towns?|suburb\w*|urban|brooklyn|long island|queens|bronx|manhattan|county|tri-?state|coastal)\b", t, re.I)),
    8: lambda t, r: sum(1 for rx in (_GENDER_RX, _AGE_RX, _GEO_RX) if rx.search(t)) >= 2,
    9: lambda t, r: bool(re.search(r"\b(compar\w*|vs\.?|versus|against|side by side|difference between|better than|more than|than)\b", t, re.I)) or 'multiple_subjects' in _flags(r),
    10: lambda t, r: bool(re.search(r"\b(binge\w*|super ?fans?|die-?hard|hardcore|heavy|light|casual|avid|most engaged|top \d+%|at least \d|x a week|times a week|power users?|loyal\w*|devoted|obsess\w*|enthusiast\w*|fanatic\w*)\b", t, re.I)),
    11: lambda t, r: bool(re.search(r"\d[\d,.]*\s*(?:k|m|mm|million|thousand|billion)?\s*(?:subscribers|members|customers|users|followers|fans|people|accounts|buyers|viewers)\b|\bwe have\b|\bour (?:base|list|file) (?:is|of)\b", t, re.I)),
    12: lambda t, r: bool(re.search(r"\b(swifties|bey ?hive|little monsters|parrotheads|beliebers|barbz|directioners|blink|stans?|fandom|nation|-?heads|hive|army|navy|mafia|gang)\b", t, re.I)) or _alias_subject(r),
    13: lambda t, r: bool(_LIST_RX.search(t)) or 'multiple_subjects' in _flags(r),
    14: lambda t, r: bool(re.search(r"\b(tiers?|with[- ]ads|ad-?(?:free|supported|lite)|mobile|smart ?tv|devices?|app[- ]only|desktop|ios|android|connected tv|ctv|premium|basic plan|plan|bundle)\b", t, re.I)),
    15: lambda t, r: bool(_PLATFORM_RX.search(t)) or bool(re.search(r"\b(cut by|users?|members?|subscribers?|customers?|buyers?|followers?|viewers?|shoppers?|owners?)\b|\b[A-Z][\w+']+ audience\b", t)) or bool(re.search(r"\b(cut by|users?|members?|subscribers?|customers?|buyers?|followers?|viewers?|shoppers?|owners?)\b", t, re.I)),
}
# schema fields that only mean something when their guard or section survives
_FIELDS_BY_GUARD = {1: ('exclusions',), 2: ('universe_mode', 'universe_note'), 3: ('universe_mode', 'universe_note'),
                    10: ('intensity_note',), 11: ('user_supplied_anchor',), 14: ('scope_note',)}
_FIELDS_BY_SECTION = {'SUBSCRIBER IQ REQUESTS': ('subiq',), 'EVENT-SCOPED WINDOWS': ('event_window',),
                      "KIDS' PRODUCTS": ('kids_product',), 'PLATFORM SCOPE': ('platform_scope',),
                      'SUBJECT IDENTITY RESOLUTION': ('identity_note', 'identity_qualifier', 'identity_versions'),
                      'PARENT-SELECTION TIEBREAK': ('existing_match_s3_key', 'existing_match_display_name', 'existing_match_days_old', 'refresh_row_hypothesis')}
_IP_FIELDS = ('ip_scope', 'consumer_verb', 'seed_source', 'content_show', 'franchise_titles', 'consumers_sample_fraction')
_GUARD_RX = re.compile(r"(?ms)^  (\d{1,2})\. [A-Z].*?(?=^  \d{1,2}\. [A-Z]|\Z)")
_GUARD_REF_RX = re.compile(r"\s*//\s*see SEMANTIC GUARDS #[\d-]+")


def _flags(r):
    return set((r or {}).get('flags') or []) if isinstance(r, dict) else set()


def _alias_subject(r):
    """The reading mapped what was written onto a differently named library
    subject: the alias guard applies."""
    for s in ((r or {}).get('subjects') or []) if isinstance(r, dict) else []:
        a, c = str(s.get('as_written') or '').lower(), str(s.get('catalog') or '').lower()
        if a and c and a not in c and c not in a:
            return True
    return False


def _only_entities(r):
    """A confident reading whose every subject is a person or a brand: the
    title / franchise fields of the schema cannot apply."""
    if not isinstance(r, dict) or float(r.get('confidence') or 0) < 0.9:
        return False
    subs = r.get('subjects') or []
    return bool(subs) and all(str(s.get('kind') or '') in ('person', 'brand') for s in subs)


def prune_semantic_guards(section, text, reading=None):
    """Keep only the numbered guards whose phrasing family the ask carries.
    Returns (section_text, dropped_guard_numbers); an empty section_text
    means every guard went and the heading should go with them."""
    kept, dropped = [], []
    body_start = None
    for m in _GUARD_RX.finditer(section):
        if body_start is None:
            body_start = m.start()
        n = int(m.group(1))
        test = GUARD_TRIGGERS.get(n)
        if test is None or test(text, reading):
            kept.append(m.group(0))
        else:
            dropped.append(n)
    if body_start is None or not dropped:
        return section, []
    if not kept:
        return '', dropped
    head = section[:body_start]
    out = head + ''.join(kept)
    if not out.endswith('\n\n'):
        out = out.rstrip('\n') + '\n\n'
    return out, dropped


def prune_decision_logic(section):
    """With no catalog candidate, existing_match / time_shifted_refresh /
    derive_cut cannot be chosen: keep the two reachable branches and say
    why the others are absent."""
    i = section.find('  * existing_match:')
    j = section.find('  * cut_needs_parent:')
    if i < 0 or j < 0 or j < i:
        return section
    k = section.find('\n\nIMPORTANT: an ask like')
    tail = section[j:k] if k > j else section[j:]
    note = ("  * (no catalog candidate matched this ask, so existing_match, "
            "time_shifted_refresh and derive_cut do not apply here)\n")
    return section[:i] + tail.rstrip('\n') + '\n' + note + '\n'


def prune_output_shape(section, fields, guards_gone=False, gone_sections=(), dropped_guards=()):
    """Drop the schema lines (or blocks) for the given field names; a block
    runs from its line to the next field line or the closing brace. Cross
    references into guards and sections that went are removed with them."""
    out = section
    for f in dict.fromkeys(fields):
        if f == 'addon_cuts':
            out = re.sub(r'(?ms)^  "addon_cuts": \[\n.*?^  \],\n', '  "addon_cuts": [],\n', out, count=1)
            continue
        out = re.sub(r'(?ms)^  "%s":.*?(?=^  "|^\}$)' % re.escape(f), '', out, count=1)
    gone = set(dropped_guards or ())

    def _ref_gone(m):
        nums = [int(x) for x in re.findall(r"\d+", m.group(0))]
        if len(nums) == 2 and '-' in m.group(0):
            nums = list(range(nums[0], nums[1] + 1))
        return '' if guards_gone or (nums and all(n in gone for n in nums)) else m.group(0)
    out = _GUARD_REF_RX.sub(_ref_gone, out)
    if guards_gone or 15 in gone:
        out = re.sub(r"\s*See rule 15\.", '', out)
    for name in gone_sections:
        if name in ('SUBJECT IDENTITY RESOLUTION', 'SUBJECT VERIFICATION', 'PLATFORM SCOPE', "KIDS' PRODUCTS", 'EVENT-SCOPED WINDOWS', 'SUBSCRIBER IQ REQUESTS'):
            out = re.sub(r" \(see %s\)|; see %s\b|\s*-\s*see %s\b|\bsee %s: " % ((re.escape(name),) * 4), '', out)
    return re.sub(r',\n\}', '\n}', out, count=1)


def _schema_fields_to_drop(text, candidates, reading, dropped_sections, dropped_guards):
    fields = []
    for sec in dropped_sections:
        fields.extend(_FIELDS_BY_SECTION.get(sec, ()))
    for g in dropped_guards:
        fields.extend(_FIELDS_BY_GUARD.get(g, ()))
    if 2 in dropped_guards and 3 not in dropped_guards or 3 in dropped_guards and 2 not in dropped_guards:
        fields = [f for f in fields if f not in ('universe_mode', 'universe_note')]
    if not candidates and 'PARENT-SELECTION TIEBREAK' not in dropped_sections:
        fields.extend(_FIELDS_BY_SECTION['PARENT-SELECTION TIEBREAK'])
    if not (_PLATFORM_RX.search(text) or re.search(r"\b(buyers?|renters?|switchers?|cancel\w*|customers?|members?|subscribers?|users?|shoppers?|owners?|purchas\w*)\b", text, re.I)) \
            and not any(str(s.get('kind') or '') in ('audience', 'platform') for s in ((reading or {}).get('subjects') or []) if isinstance(reading, dict)):
        fields.append('clickstream_signals')
    if not (_GENDER_RX.search(text) or _AGE_RX.search(text) or _GEO_RX.search(text) or re.search(r"\b(cut|split|by)\b", text, re.I)):
        fields.append('addon_cuts')
    if _only_entities(reading):
        fields.extend(_IP_FIELDS)
    return fields


def _subject_resolved(reading):
    """True when the Reader named one subject with a library match at high
    confidence and no ambiguity flag: the 3,000-token identity-resolution
    rulebook has nothing left to resolve."""
    r = reading if isinstance(reading, dict) else None
    if not r or float(r.get('confidence') or 0) < 0.9:
        return False
    subs = r.get('subjects') or []
    if len(subs) != 1 or not subs[0].get('catalog'):
        return False
    flags = set(r.get('flags') or [])
    return not (flags & {'typo', 'multiple_subjects', 'ambiguous'})


def prune_rulebook(system_prompt, text, candidates=None, reading=None):
    """Drop whole rulebook sections an ask cannot need. A section is the
    text from one ALL-CAPS heading to the next. Anything not in PRUNABLE
    stays; when in doubt the section stays. The Reader's reading lets more
    go (gender, event, kids, identity resolution). Returns (prompt, dropped)."""
    try:
        s = str(system_prompt or '')
        t = str(text or '')
        starts = [m.start() for m in _SECTION_RX.finditer(s)]
        if len(starts) < 6:
            return s, []
        starts.append(len(s))
        out, dropped, dropped_guards = [], [], []
        shape_at = None
        pos = 0
        for a, b in zip(starts, starts[1:]):
            if a > pos:
                out.append(s[pos:a])
            sec = s[a:b]
            head = sec.split('\n', 1)[0].upper()
            keep = True
            for key, needed in PRUNABLE.items():
                if head.startswith(key):
                    keep = needed(t, candidates, reading)
                    if not keep:
                        dropped.append(key)
                    break
            if keep and head.startswith('SEMANTIC GUARDS'):
                sec, dropped_guards = prune_semantic_guards(sec, t, reading)
                if not sec:
                    keep = False
                    dropped.append('SEMANTIC GUARDS')
            elif keep and head.startswith('DECISION LOGIC') and not candidates:
                sec = prune_decision_logic(sec)
                if sec != s[a:b]:
                    dropped.append('DECISION LOGIC: candidate branches')
            if keep:
                if head.startswith('OUTPUT SHAPE'):
                    shape_at = len(out)
                out.append(sec)
            pos = b
        if pos < len(s):
            out.append(s[pos:])
        if shape_at is not None:
            fields = _schema_fields_to_drop(t, candidates, reading, dropped, dropped_guards)
            if fields:
                out[shape_at] = prune_output_shape(out[shape_at], fields, guards_gone='SEMANTIC GUARDS' in dropped, gone_sections=dropped, dropped_guards=dropped_guards)
                dropped.append('OUTPUT SHAPE: ' + ', '.join(dict.fromkeys(fields)))
        if dropped_guards:
            dropped.append('SEMANTIC GUARDS #' + ','.join(str(n) for n in dropped_guards))
        pruned = ''.join(out)
        # a broken split shows as a missing landmark or an implausibly small remainder
        floor = 0.2 if isinstance(reading, dict) and reading.get('intent') else 0.3
        if len(pruned) < len(s) * floor or not all(k in pruned for k in ('OUTPUT SHAPE', '"decision"', 'DECISION LOGIC', '\n}\n') if k in s):
            return s, []
        return pruned, dropped
    except Exception:
        traceback.print_exc()
        return system_prompt, []


def draft_output_ceiling(text, default=6000, long=16000):
    """A list of subjects (multi-spec array) keeps the long budget."""
    t = str(text or '')
    if len(re.findall(r",", t)) >= 2 or re.search(r"\b(each|both|all of|the following|separately|individual)\b", t, re.I) \
            or len(re.findall(r"\band\b", t, re.I)) >= 2 or len(t) > 600:
        return long
    return default


# ------------------------------------------------------------ follow-ups
INHERIT_LANES = {'catalog_lookup', 'status_lookup', 'deliverable_lookup', 'kpi_definition', 'brand_coverage'}
_NEW_SUBJECT_RX = re.compile(r"\b(for|about|on)\s+[A-Z][\w'&.-]+(\s+[A-Z][\w'&.-]+)*", re.U)


def followup_lane(text, history):
    """The previous turn's lane when this ask is a short follow-up with no
    new named subject ("any MMA fighters?" after a catalog answer). Returns
    the lane name or ''."""
    try:
        t = ' '.join(str(text or '').split())
        if not t or len(t.split()) > 12:
            return ''
        prev = None
        for turn in reversed(list(history or [])):
            if str(turn.get('role') or '') != 'user':
                prev = turn
                break
        if not isinstance(prev, dict):
            return ''
        meta = prev.get('meta') or {}
        lane = str(meta.get('reason') or meta.get('route') or (prev.get('decision') or {}).get('reason') or '')
        if lane not in INHERIT_LANES:
            return ''
        if re.match(r"^(and|also|what about|how about|any|same for|now|then|ok|okay)\b", t, re.I) or t.endswith('?') or len(t.split()) <= 6:
            return lane
        return ''
    except Exception:
        return ''


# ------------------------------------------------------- capability asks
_CAPABILITY_RX = re.compile(r"^\W*(is there a way to|is there any way to|can (?:we|you|crosswalk|prometheus)|could (?:we|you)|how (?:can|do|would|could) (?:we|i|you)\s+(?:set up|build|get|see|track|measure|do)|do you (?:do|offer|have a way)|are you able to|would it be possible to|what would it take to)", re.I)
_PRODUCT_WORDS = (
    (re.compile(r"\b(attribution|track(?:ing)? (?:a )?campaign|which (?:creator|post|asset)s? (?:drove|drive))", re.I),
     'Attribution IQ', 'tracks a campaign\'s creators, posts and assets to the people who reached a ticketing site, a product page or a signup, with first touch, last touch and assists',
     'Set up Attribution IQ'),
    (re.compile(r"\b(digital journey|journey|how people (?:came|got) to|path to)", re.I),
     'a Digital Journey', 'shows the path from a social post, clip or search to the watch, purchase or ticketing visit, step by step, with the paths off the page',
     'Build a Digital Journey'),
    (re.compile(r"\b(brand partnership|partnership|sponsor|valuation|what is .* worth)", re.I),
     'Brand Partnership IQ', 'values a talent or title against a brand: audience fit, earned media, brand lift and conversion lenses, one dollar figure',
     'Value a brand partnership'),
    (re.compile(r"\b(subscriber|signups?|churn|retention|reactivat)", re.I),
     'Subscriber IQ', 'tracks a title on a streaming service: accounts that watched, new and reactivated subscribers whose first watch was the title, and how long they stayed',
     'Pull a Subscriber IQ'),
    (re.compile(r"\b(audience|profile|fans?|viewers|buyers|attended|went to|people who)", re.I),
     'a Profile IQ profile', 'builds the audience of any person, title, brand or behavior (people who attended a tour, bought a product, watched a title) with demographics and the brands, platforms and talent they over-index on',
     'Run a profile'),
)


_POLITE_ORDER_RX = re.compile(r"^\W*(?:can|could|would) (?:you|we|crosswalk|prometheus) (?:please )?(?:build|make|create|run|pull|draft|generate|put together|give|send|get|do|show|set up|start|spin up|prepare|kick off|value|price|size|assess|map)\b(?: (?:me|us))?", re.I)


def polite_order(text):
    """'Can you build me a Nike profile' is an order with manners, not a
    question about what we can do: 'me' / 'us' after the verb, or a named
    subject (a capitalised word past the opening) marks it."""
    t = ' '.join(str(text or '').split())
    m = _POLITE_ORDER_RX.match(t)
    if not m:
        return False
    if re.search(r"\b(me|us)\b", m.group(0), re.I):
        return True
    rest = t[m.end():]
    return bool(re.search(r"\b[A-Z][\w'&.+-]+", rest))


_MATERIAL_RX = re.compile(r"\b(urls?|links?|csv|xlsx|spreadsheet|sheet|file|files|columns?|rows?|cells?|upload(?:ed|s)?|attach(?:ed|ment|ments)?|"
                          r"the full (?:url|link|list|thing)|what i (?:sent|uploaded|pasted|attached)|(?:this|that|the) (?:deck|pdf|document|doc|image|screenshot))\b", re.I)


def capability_ask(text):
    """{'reply', 'followups'} for a can-we / is-there-a-way ask, or None.
    'Can you see the full url now?' is about the material on the thread,
    not about what the product does (Alexia 2026-10-16 got a Profile IQ
    pitch); the document lane answers that."""
    t = ' '.join(str(text or '').split())
    if not t or not _CAPABILITY_RX.match(t) or polite_order(t) or _MATERIAL_RX.search(t):
        return None
    hits = [(name, what, chip) for rx, name, what, chip in _PRODUCT_WORDS if rx.search(t)]
    if not hits:
        hits = [_PRODUCT_WORDS[-1][1:]]
    name, what, chip = hits[0]
    subj = re.sub(_CAPABILITY_RX, '', t).strip(' ?.!')
    reply = (f"Yes. {name[0].upper() + name[1:]} {what}. "
             f"Tell me the subject{(' (' + subj[:80] + ')') if subj else ''} and the window and I will line it up; "
             f"it comes back as a brief to approve before anything is charged.")
    return {'reply': reply, 'followups': [chip]}


_NEGATED_BUILD_RX = re.compile(r"\b(don'?t|do not|no need to|without|not)\s+(?:build|run|pull|create|make|start)\b|\b(no|not) (?:a )?(?:new )?(?:profile|build|pull|run)\b|\banswer (?:this|it|that) (?:from|with|using)\b|\bjust (?:answer|tell me)\b|\bfrom (?:the )?existing\b", re.I)


def negated_build(text):
    """True when the ask forbids a build: answer mode only."""
    return bool(_NEGATED_BUILD_RX.search(str(text or '')))


_NEGATION_LEAD_RX = re.compile(
    r"^\W*(?:(?:please\s+)?(?:don'?t|do not|no need to)\s+(?:build|run|pull|create|make|start)[^.:;!?\n]*[.:;!?]?\s*)+"
    r"(?:(?:just\s+)?answer (?:this|it|that|me)(?: (?:from|with|using) [^:.\n]*)?[:.]?\s*)?", re.I)


def strip_build_negation(text):
    """The question itself, with the "don't build a new profile, answer
    this from the existing ones:" lead removed, when a question remains."""
    t = str(text or '')
    rest = _NEGATION_LEAD_RX.sub('', t, count=1).strip()
    return rest if len(rest.split()) >= 3 else t


_DECLINE_RX = re.compile(r"^\W*(not now|no thanks|no thank you|not yet|maybe later|later|no,? (?:that'?s )?(?:ok|fine|all)|skip|pass|nope|nah)\W*$", re.I)


def offer_declined(text):
    return bool(_DECLINE_RX.match(str(text or '')))


DECLINE_REPLY = "Understood, nothing is queued. Ask me anything else when you are ready."


# ------------------------------------------------- brief from history
_CONFIRM_RX = re.compile(r"^(here'?s the (?:digital journey|brand partnership|attribution|flywheel|profile)[^\n]*|i'?ll (?:build|run|value|set up)[^\n]*)", re.I)
_FLOW_RX = (('digital_journey', re.compile(r"digital journey", re.I)), ('brand_partnership', re.compile(r"brand partnership", re.I)),
            ('attribution', re.compile(r"attribution", re.I)), ('flywheel', re.compile(r"flywheel", re.I)))


def brief_from_history(history):
    """(flow, confirm_text) for the last confirm card in the thread, or
    (None, ''). The confirm text reads back through the flow's own parser
    as if the reader had typed it, so a run order never dead-ends on a
    missing stash."""
    for turn in reversed(list(history or [])):
        if str(turn.get('role') or '') == 'user':
            continue
        txt = str(turn.get('text') or turn.get('content') or '').strip()
        if not txt or not _CONFIRM_RX.match(txt):
            continue
        for flow, rx in _FLOW_RX:
            if rx.search(txt[:160]):
                return flow, txt
        return None, ''
    return None, ''


def rearm_brief_from_thread(body, history, user, ppu, intake_ui, intake_turn):
    """A run order with no stashed brief re-reads the last confirm card in
    the thread through its flow's own reader and arms that flow's confirm
    on the request ("yes, run it" dead-ended on "no brief waiting" whenever
    the stash was gone). Returns the flow or ''. `intake_ui` and
    `intake_turn` are the chat module's table and turn function."""
    try:
        flow, confirm_text = brief_from_history(history)
        if not flow or flow not in (intake_ui or {}):
            return ''
        kind, confirm_name, run_label, payload_key, collect_key, labels = intake_ui[flow]
        turn = intake_turn(flow, confirm_text, dict(body, text=confirm_text), history, user, ppu, False)
        parsed = turn.get(payload_key) if isinstance(turn, dict) else None
        if not isinstance(parsed, dict) or not parsed:
            return ''
        key = {'digital_journey': 'jiq_confirm', 'brand_partnership': 'bpiq_confirm',
               'attribution': 'aiq_confirm', 'flywheel': 'fw_confirm'}[flow]
        body[key] = parsed
        print(f"[brief-from-thread] {flow} re-read from the last confirm card")
        return flow
    except Exception:
        traceback.print_exc()
        return ''


def reading_line(reading):
    """One line that tells a drafting model how the Reader understood the
    request, or '' (2026-10-14)."""
    try:
        r = reading if isinstance(reading, dict) else None
        if not r or not r.get('intent'):
            return ''
        subs = ', '.join(((s.get('catalog') or s.get('as_written') or '') + (f" (library: {s['catalog']})" if s.get('catalog') and s.get('as_written') and s['catalog'] != s['as_written'] else ''))
                         for s in (r.get('subjects') or []) if (s.get('catalog') or s.get('as_written')))
        bits = [f"intent {r['intent']}"]
        if subs:
            bits.append(f"subject(s) {subs}")
        if r.get('window'):
            bits.append(f"window {r['window']}")
        if r.get('platform'):
            bits.append(f"platform {r['platform']}")
        if r.get('flags'):
            bits.append('flags ' + ', '.join(str(f) for f in r['flags']))
        if r.get('wants'):
            bits.append(f"they want: {r['wants']}")
        return "HOW THE REQUEST WAS READ (honor this; it was read with the whole thread and the library in view): " + '; '.join(bits) + '.'
    except Exception:
        return ''


def with_reading(user_prompt, reading):
    line = reading_line(reading)
    return (str(user_prompt or '') + "\n\n" + line) if line else user_prompt


def jsonable(obj):
    """A JSON-safe copy of a job's inputs (for the running doc the
    sweeper restarts from). Drops what json cannot carry."""
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return {}


# -------------------------------------------------- adjacent read offer
_KIND_CHIP = {'journey': 'Open the {name} Digital Journey', 'bpiq': 'Open the {name} Brand Partnership read',
              'attribution': 'Open the {name} Attribution IQ campaign', 'subiq': 'Open the {name} Subscriber IQ tracker',
              'profile': 'Open the {name} profile', 'package': 'Resend the {name} data files', 'deck': 'Open the {name} deck'}


def adjacent_read(subject, uname, *, host=None, s3=None, exclude_kind=''):
    """One chip naming the most useful other read we hold on the subject,
    or None."""
    try:
        if not str(subject or '').strip():
            return None
        from . import precedent as _prec
        found = _prec.resolve(subject, uname, host=host, s3=s3)
        if not found or str(found.get('kind') or '') == str(exclude_kind or ''):
            return None
        tpl = _KIND_CHIP.get(str(found.get('kind') or ''))
        if not tpl:
            return None
        name = str(found.get('name') or subject)
        return {'label': tpl.format(name=name)[:80], 'kind': found.get('kind'), 'key': found.get('key')}
    except Exception:
        return None


# ------------------------------------------------------ self-grade bind
_FIG_RX = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w.%])")
_UNIT_WORDS = ('viewers', 'accounts', 'people', 'users', 'subscribers', 'transactions', 'views', 'signups', 'buyers', 'visitors', 'minutes')


def _label_tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", str(s or '').lower()) if len(w) > 3 or w.isdigit()}


def bind_to_published(reply, subject, *, ledger=None, min_overlap=3):
    """Replace a stated count whose sentence shares at least `min_overlap`
    distinctive tokens with a published metric's name and differs from it
    by more than 5% with the published count. Conservative: one swap per
    metric, counts only, never percentages. Returns (reply, swaps)."""
    swaps = []
    try:
        il = ledger
        if il is None:
            import insights_ledger as il
        led = il.consult(subject=subject, question=str(reply or '')[:200]) or {}
        entries = led.get('entries') or []
        if not entries:
            return reply, swaps
        published = []
        for e in entries:
            for m in (e.get('metrics') or []):
                try:
                    v = float(m.get('value'))
                except (TypeError, ValueError):
                    continue
                unit = str(m.get('unit') or '').lower()
                if unit in ('%', 'pct', 'percent') or v < 1000:
                    continue
                published.append((str(m.get('name') or ''), v, _label_tokens(m.get('name'))))
        if not published:
            return reply, swaps
        out = str(reply or '')
        sentences = re.split(r"(?<=[.!?])\s+|\n", out)
        used = set()
        for sent in sentences:
            toks = _label_tokens(sent)
            for m in _FIG_RX.finditer(sent):
                try:
                    v = float(m.group(1).replace(',', ''))
                except ValueError:
                    continue
                best = None
                for name, pv, ptoks in published:
                    if name in used:
                        continue
                    ov = len(toks & ptoks)
                    if ov >= min_overlap and any(u in sent.lower() for u in _UNIT_WORDS):
                        if best is None or ov > best[0]:
                            best = (ov, name, pv)
                if best and best[2] > 0 and abs(v - best[2]) / best[2] > 0.05 and max(v, best[2]) / max(min(v, best[2]), 1) < 20:
                    new_s = sent.replace(m.group(1), f"{int(round(best[2])):,}", 1)
                    out = out.replace(sent, new_s, 1)
                    used.add(best[1])
                    swaps.append({'from': m.group(1), 'to': f"{int(round(best[2])):,}", 'metric': best[1]})
                    break
        return out, swaps
    except Exception:
        traceback.print_exc()
        return reply, swaps


# ------------------------------------------------- evidence bind (exact)
_EV_NUM_RX = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w,]|\.\d)")


def evidence_numbers(evidence_text):
    """Every count (1,000 and up) the evidence carries, as ints."""
    out = set()
    for m in _EV_NUM_RX.finditer(str(evidence_text or '')):
        try:
            out.add(int(m.group(1).replace(',', '')))
        except ValueError:
            pass
    return out


def bind_to_evidence(reply, evidence_text, tol=0.02):
    """A count in the reply that sits within `tol` of a count the evidence
    carries, but is not that count, becomes it (2026-10-14: a Subscriber
    IQ answer said 21,115 and 11,569 where the evidence read 21,011 and
    11,511; the model re-derived what it had been handed). Exact matches
    and figures with no close evidence neighbour are left alone, so sums
    and ratios the model computed stay. Returns (reply, swaps)."""
    swaps = []
    try:
        ev = evidence_numbers(evidence_text)
        if not ev:
            return reply, swaps
        ev_sorted = sorted(ev)
        out = str(reply or '')

        def _fix(m):
            raw = m.group(1)
            try:
                v = int(raw.replace(',', ''))
            except ValueError:
                return raw
            if v in ev or v < 1000:
                return raw
            best = min(ev_sorted, key=lambda e: abs(e - v))
            if best > 0 and abs(best - v) / float(best) <= tol and abs(best - v) >= 1:
                swaps.append({'from': raw, 'to': f"{best:,}"})
                return f"{best:,}"
            return raw
        out = _EV_NUM_RX.sub(_fix, out)
        return out, swaps
    except Exception:
        traceback.print_exc()
        return reply, swaps


# ---------------------------------------------------------- thread replay
_REJECT_RX = re.compile(r"\b(wrong|incorrect|not right|doesn'?t (?:look|seem) right|that'?s not|redo|try again|no,? that)\b", re.I)


def _fold_q(s):
    return re.sub(r"[^a-z0-9 ]", ' ', str(s or '').lower()).split()


def thread_replay(text, history):
    """The earlier answer to this exact question in this thread, with when
    it was given, or None."""
    try:
        q = _fold_q(text)
        if len(q) < 3:
            return None
        turns = list(history or [])
        for i, turn in enumerate(turns):
            if str(turn.get('role') or '') != 'user':
                continue
            if _fold_q(turn.get('text') or turn.get('content')) != q:
                continue
            nxt = next((x for x in turns[i + 1:] if str(x.get('role') or '') != 'user'), None)
            if not nxt:
                continue
            ans = str(nxt.get('text') or nxt.get('content') or '').strip()
            meta = nxt.get('meta') or {}
            if not ans or meta.get('job') or meta.get('options') or len(ans) < 40:
                continue
            if re.search(r"(give me, in one message|which time window|here'?s the|brief drafted|ready to run)", ans, re.I):
                continue
            # an answer the reader pushed back on is never replayed
            nxt_user = next((x for x in turns[i + 1:] if str(x.get('role') or '') == 'user' and x is not turn), None)
            if nxt_user and _REJECT_RX.search(str(nxt_user.get('text') or nxt_user.get('content') or '')):
                continue
            when = str(nxt.get('ts') or '')
            try:
                d = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
                when_txt = d.strftime('%b %-d at %H:%M UTC')
            except Exception:
                when_txt = ''
            lead = f"Same read as earlier in this thread{(' (' + when_txt + ')') if when_txt else ''}:"
            return {'reply': lead + '\n\n' + ans, 'followups': list(meta.get('followups') or [])[:3]}
        return None
    except Exception:
        return None


def on_it_line(reading, base):
    """The started-read line names what is being read (2026-10-14: 'On it'
    alone left 'who skews older, Gunna or Drake?' with no echo of either).
    The Reader's subjects first, then the bound base."""
    names = []
    try:
        for sdoc in (reading or {}).get('subjects') or []:
            nm = str((sdoc or {}).get('catalog') or (sdoc or {}).get('as_written') or '').strip()
            if nm and nm.lower() not in [n.lower() for n in names]:
                names.append(nm)
    except Exception:
        names = []
    if not names and isinstance(base, dict) and base.get('subject'):
        names = [str(base['subject'])]
    what = (' and '.join(names[:3]) if len(names) <= 3 else ', '.join(names[:3]) + ' and more') if names else ''
    lead = f"On it: reading {what} now." if what else "On it."
    return lead + " This one takes a real look at the data, so give me a moment - the read will land right here when it is ready."
