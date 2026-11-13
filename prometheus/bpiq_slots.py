"""What a sentence names for the Brand Partnership Valuation wizard
(2026-11-11, Samantha Kolton at CAA).

The wizard asked "Who is the property or talent?" and the reader
answered in one sentence: "JYT is the talent and GAP is the brand. the
partnership started in September 2026". The step waited for a short
name, the sentence read as a new question, and a general read ran in
the wizard's place. A sentence that names its own slots (who the talent
is, who the brand is, when it started) is the wizard's answer whatever
step is open. This module reads those slots and nothing else:

- `parse(text, today)`: talent, brand, campaign, the start (a day, or a
  month when only a month was given), the end, and whether the words
  say it is still running.
- `single_date(text, today, hint)`: one date typed on its own ("9/22/26",
  "September 22", "the 22nd" under a month already known).
- `says_ongoing(text)`: "to today", "still running", "ongoing".
- `is_filler(name)`: yes / no / ok and the like, which are never a
  talent, a brand or a campaign.
- `finds_assets(text)` / `has_no_links(text)`: the ways a reader says
  "you find the campaign assets".
- `known_casing(name, lookup)`: a name typed in capitals takes the
  spelling the library already uses for it, when it has one.

Pure: no network, no store. User-facing copy lives in the wizard.
"""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
                'October', 'November', 'December')

_TALENT_ROLE = (r"(?:talent|property|creator|influencer|artist|athlete|celebrity|ambassador|spokesperson|"
                r"musician|band|group|star|personality|host)")
_BRAND_ROLE = r"(?:brand(?:\s+partner)?|sponsor|advertiser|client|retailer)"
_CAMPAIGN_ROLE = r"(?:campaign(?:\s+name)?|collection|collab(?:oration)?|capsule|initiative)"
_ANY_ROLE = r"(?:" + _TALENT_ROLE[3:-1] + r"|" + _BRAND_ROLE[3:-1] + r"|" + _CAMPAIGN_ROLE[3:-1] + r")"
_BE = r"(?:is|are|was|were|will\s+be|would\s+be|=)"
_DET = r"(?:the|our|my|their|a|an)"
_NAME = r"[A-Za-z0-9@#][^.;,?!:\n]{0,58}?"

# "JYT is the talent", "GAP is the brand", "Gap x JYT is the campaign"
_NAME_THEN_ROLE_RX = re.compile(
    r"(?:^|[.;,!:\n]\s*|\band\s+|\bbut\s+|\bwhere\s+)(?P<name>" + _NAME + r")\s+" + _BE + r"\s+(?:" + _DET + r"\s+)?"
    r"(?P<role>" + _ANY_ROLE + r")\b(?!\s+(?:of|for|is|are|was|were|that|who|which)\b)", re.I)
# "the talent is JYT", "brand: Gap", "talent = JYT and brand = Gap"
_ROLE_THEN_NAME_RX = re.compile(
    r"(?:^|[.;,!\n]\s*|\band\s+|\bbut\s+)(?:" + _DET + r"\s+)?(?P<role>" + _ANY_ROLE + r")\s*(?:" + _BE + r"|:|-)\s*"
    r"(?P<name>" + _NAME + r")(?=\s*(?:$|[.;,!\n]|\band\s+(?:" + _DET + r"\s+)?" + _ANY_ROLE + r"\b|"
    r"\b(?:and\s+)?(?:it|they|the\s+partnership|the\s+campaign|the\s+deal|which|that)\s+(?:start|began|launch|kick|went|ran|is|was)|"
    r"\b(?:start(?:ed|s|ing)?|began|launched|since)\b))", re.I)
# "the partnership between JYT and Gap", "JYT partnered with Gap"
_BETWEEN_RX = re.compile(
    r"\b(?:partnership|deal|collab(?:oration)?|sponsorship)\s+between\s+(?P<a>" + _NAME + r")\s+and\s+(?P<b>" + _NAME + r")"
    r"(?=\s*(?:$|[.;,!\n]|\b(?:start(?:ed|s|ing)?|began|launched|since|which|that|it)\b))", re.I)
_PARTNERED_RX = re.compile(
    r"(?:^|[.;,!\n]\s*)(?P<a>" + _NAME + r")\s+(?:partnered|is\s+partnering|teamed\s+up|collaborated|collabed|signed)\s+with\s+"
    r"(?P<b>" + _NAME + r")(?=\s*(?:$|[.;,!\n]|\b(?:start(?:ed|s|ing)?|began|launched|since|in|on|for|which|that|and\s+it)\b))", re.I)

_DATE_TOKEN = (
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?"
    r"|(?P<mon>" + _MON + r")\.?\s+(?P<md>\d{1,2})(?:st|nd|rd|th)?(?!\d)(?:,?\s+(?P<my>(?:19|20)\d{2}))?"
    r"|(?P<dd>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<dmon>" + _MON + r")\.?(?:,?\s+(?P<dy>(?:19|20)\d{2}))?"
    r"|(?:(?P<part>early|mid|late|(?:the\s+)?(?:start|beginning|middle|end)\s+of)[\s-]+)?(?P<onlymon>" + _MON + r")\.?(?:,?\s+(?:of\s+)?(?P<oy>(?:19|20)\d{2}))?"
)
_START_RX = re.compile(
    r"\b(?:started|starts|starting|start(?:\s+date)?(?:\s+(?:is|was))?|began|begins|beginning|launched|launches|launching|"
    r"kicked\s+off|kicks\s+off|went\s+live|goes\s+live|debuted|dropped|(?:live|running|active)\s+since|since)\s+"
    r"(?:back\s+)?(?:(?:in|on|at|around|about|from|as\s+of)\s+)?(?:the\s+)?(?:" + _DATE_TOKEN + r")", re.I)
_END_RX = re.compile(
    r"\b(?:ended|ends|ending|end(?:\s+date)?(?:\s+(?:is|was))?|ran\s+(?:through|until|till|to)|runs\s+(?:through|until|till|to)|"
    r"wrapped|wraps|through|thru|until|till)\s+(?:(?:in|on|at|around|about)\s+)?(?:the\s+)?(?:" + _DATE_TOKEN + r")", re.I)
_ONGOING_RX = re.compile(
    r"\b(?:ongoing|on-going|still\s+(?:running|live|going|active|on)|(?:to|through|thru|until|till|til)\s+"
    r"(?:today|now|date|present|the\s+present|this\s+week)|to-date|so\s+far|and\s+counting|has\s+not\s+ended|hasn'?t\s+ended|"
    r"not\s+(?:over|ended|finished)\s*(?:yet)?|currently\s+(?:running|live))\b|-\s*(?:today|now|present)\b", re.I)
_SINGLE_DATE_RX = re.compile(r"^\W*(?:(?:it\s+)?(?:started|began|launched|starts)\s+)?(?:(?:on|from|since)\s+)?(?:the\s+)?(?:" + _DATE_TOKEN + r")", re.I)
_BARE_DAY_RX = re.compile(r"^\W*(?:(?:on\s+)?the\s+)?(\d{1,2})(?:st|nd|rd|th)?\W*$", re.I)

_FILLER_RX = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|y|ya|sure|ok|okay|k|fine|correct|right|no|nope|nah|n|none|nothing|skip|thanks?|thank\s+you|"
    r"please|go\s+ahead|do\s+it|run\s+it|continue|next|same|ditto|idk|not\s+sure|unsure|no\s+idea|i\s+(?:do\s+not|don'?t|dont)\s+know|"
    r"both|either|neither|accept|decline|approve|confirm|done|hello|hi|hey|test|\?+|\d{1,2})\W*$", re.I)
# a reply that points somewhere else or gives nothing: never a name (2026-11-11: "same as before", "you find it",
# "I'm not sure" typed at a name step)
_SAME_RX = re.compile(
    r"^\W*(?:(?:the\s+)?same(?:\s+(?:one|thing|ones))?(?:\s+as)?(?:\s+(?:before|last\s+time|earlier|above|previous(?:ly)?|"
    r"the\s+last\s+(?:one|time)|the\s+previous\s+one))?|ditto|like\s+(?:before|last\s+time)|as\s+before)\W*$", re.I)
_NON_NAME_RX = re.compile(
    r"^\W*(?:you\s+(?:find|pick|choose|decide|tell\s+me)(?:\s+(?:it|one|them))?|find\s+(?:it|one|them)|up\s+to\s+you|whatever|"
    r"n\s*/?\s*a|tbd|i\s*(?:'|\u2019)?m\s+not\s+sure|i\s+am\s+not\s+sure|not\s+sure\s+(?:yet|really)|dunno|no\s+clue|"
    r"h+m+|u+h+|u+m+|maybe|i\s+guess|good\s+question|what\s+do\s+you\s+mean)\W*$", re.I)
_PRONOUN_RX = re.compile(r"^(?:it|this|that|they|them|he|she|we|who|what|which|there|here|one|someone|something)$", re.I)

# "find the campaign asset", "find it", "you find it", "I don't have one", "not sure" (2026-11-11)
_FIND_RX = re.compile(
    r"\bfind\s+(?:it|them|those|these|that|one|some|everything|all|whatever|what\s+you\s+can)\b|"
    r"\b(?:find|locate|get|pull|grab|gather|collect|research|search\s+for|look\s+for|look\s+up|track\s+down|source|use)\s+"
    r"(?:(?:the|every|all|any|each|my|our|their|its|whatever|applicable|relevant|campaign|campaign'?s|brand|talent|of|social)\s+){0,4}"
    r"(?:assets?|links?|posts?|content|creative|creatives|materials?|urls?|pieces|videos?|ads?)\b|"
    r"\byou\s+(?:find|do\s+it|look|search|decide|pick|choose|handle|go\s+find|can\s+find|should\s+find|locate|research)\b|"
    r"\b(?:look|search)\s+(?:it|them)\s+up\b|\b(?:look|search)\s+for\s+(?:it|them)\b|"
    r"\bup\s+to\s+you\b|\byour\s+call\b|\bwhatever\s+you\s+(?:can\s+)?(?:find|think|want)\b|\bevery\s*thing\b|\ball\s+of\s+(?:them|it)\b|"
    r"\b(?:every|all)\s+(?:applicable\s+|campaign\s+)?assets?\b|\bjust\s+find\b|\bgo\s+find\b|\bfind\s+them\b|\bauto(?:matic(?:ally)?)?\b", re.I)
_NO_LINKS_RX = re.compile(
    r"\b(?:i|we)\s+(?:do\s+not|don'?t|dont)\s+(?:have|know|got)\b|\b(?:i|we)\s+have\s+(?:none|no(?:thing|\s+links?|\s+assets?|\s+idea)?)\b|"
    r"\bno\s+(?:specific\s+)?(?:links?|urls?|assets?)\b|\bnot\s+sure\b|\bunsure\b|\bno\s+idea\b|\bidk\b|\bnone\b|\bnothing\b|\bnot\s+really\b|"
    r"\bdon'?t\s+have\b|\bdont\s+have\b|\bdo\s+not\s+have\b|\bhaven'?t\s+got\b|\bskip\b|\bno\s+preference\b|\beither\b", re.I)

_AUDIENCE_TAIL_RX = re.compile(
    r"\s+(?:customers?|shoppers?|buyers?|viewers?|watchers?|listeners?|readers?|players?|subscribers?|users?|fans?|audience|members?)$", re.I)


def _norm(text):
    return ' '.join(str(text or '').split())


def _fold(text):
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def is_filler(name):
    """yes / no / ok / thanks / a bare number: never a talent, a brand or
    a campaign name."""
    t = _norm(name)
    return (not t) or bool(_FILLER_RX.match(t)) or bool(_SAME_RX.match(t)) or bool(_NON_NAME_RX.match(t))


def says_same(text):
    """'same as before', 'same as last time', 'ditto': the reader points
    at what they gave last time."""
    return bool(_SAME_RX.match(_norm(text)))


def _clean_name(name):
    n = _norm(name).strip(' \'"`.,:;!?-')
    n = re.sub(r"^(?:it(?:'s| is)|so|and|well|ok(?:ay)?|hi|hey)\s+", '', n, flags=re.I).strip()
    n = re.sub(r"^(?:the\s+(?:band|group|brand|company|artist|talent|creator|show)\s+)", '', n, flags=re.I).strip()
    if not n or len(n.split()) > 7 or is_filler(n) or _PRONOUN_RX.match(n):
        return ''
    if re.search(r"\b" + _ANY_ROLE + r"\b", n, re.I) and len(n.split()) <= 2:
        return ''     # "the brand", "our talent": a role, not a name
    if re.search(r"\b(?:start(?:ed|s|ing)?|began|launched|since)\b", n, re.I):
        return ''
    return n


def _role_of(word):
    w = _norm(word).lower()
    if re.fullmatch(_TALENT_ROLE, w, re.I):
        return 'talent'
    if re.fullmatch(_BRAND_ROLE, w, re.I):
        return 'brand'
    if re.fullmatch(_CAMPAIGN_ROLE, w, re.I):
        return 'campaign'
    return ''


def _year(tok, default):
    if not tok:
        return default
    y = int(tok)
    return y + 2000 if y < 100 else y


def _date_from_match(m, today, default_month=None, default_year=None):
    """(date, precise, label) from a _DATE_TOKEN match. A month with no
    day is not precise: the date is the first of the month (the 15th for
    'mid', the 21st for 'late') and the label is the month as said. A
    date with no year that lands after today is last year's."""
    g = m.groupdict()
    explicit_year = bool(g.get('y') or g.get('my') or g.get('dy') or g.get('oy') or g.get('iso'))
    try:
        if g.get('iso'):
            d = date.fromisoformat(g['iso'])
            return d, True, ''
        if g.get('m') and g.get('d'):
            d = date(_year(g.get('y'), default_year or today.year), int(g['m']), int(g['d']))
            precise = True
            label = ''
        elif g.get('mon') and g.get('md'):
            d = date(_year(g.get('my'), default_year or today.year), _MONTHS[g['mon'].lower().rstrip('.')], int(g['md']))
            precise, label = True, ''
        elif g.get('dd') and g.get('dmon'):
            d = date(_year(g.get('dy'), default_year or today.year), _MONTHS[g['dmon'].lower().rstrip('.')], int(g['dd']))
            precise, label = True, ''
        elif g.get('onlymon'):
            mo = _MONTHS[g['onlymon'].lower().rstrip('.')]
            yr = _year(g.get('oy'), default_year or today.year)
            part = str(g.get('part') or '').lower()
            day = 15 if ('mid' in part) else (21 if ('late' in part or 'end' in part) else 1)
            d = date(yr, mo, day)
            precise = False
            label = f"{_MONTH_NAMES[mo - 1]} {yr}"
            if 'mid' in part:
                label = 'mid ' + label
            elif 'late' in part or 'end' in part:
                label = 'late ' + label
            elif part:
                label = 'early ' + label
        else:
            return None, False, ''
    except (ValueError, KeyError):
        return None, False, ''
    if d > today and not explicit_year:
        try:
            d = d.replace(year=d.year - 1)
            if label:
                label = re.sub(r"\d{4}$", str(d.year), label)
        except ValueError:
            return None, False, ''
    return d, precise, label


def says_ongoing(text):
    """The words say the campaign is still running ("to today", "ongoing")."""
    return bool(_ONGOING_RX.search(_norm(text)))


def single_date(text, today, hint=None):
    """One date typed on its own, as ISO: "9/22/26", "September 22",
    "22 September", "the 22nd" or "22" when `hint` (an ISO date already
    known for the start) supplies the month and year. None when the
    words carry no single date."""
    t = _norm(text)
    if not t:
        return None
    hd = None
    if hint:
        try:
            hd = date.fromisoformat(str(hint)[:10])
        except ValueError:
            hd = None
    m = _BARE_DAY_RX.match(t)
    if m and hd is not None:
        try:
            return date(hd.year, hd.month, int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = _SINGLE_DATE_RX.match(t)
    if not m:
        return None
    d, precise, _label = _date_from_match(m, today, default_year=(hd.year if hd else None))
    if d is None or not precise:
        return None
    return d.isoformat()


def parse(text, today=None):
    """The slots a sentence names. Returns a dict with any of:
    talent, brand, campaign, start (ISO), start_precise (bool),
    start_label ('September 2026' when only a month was given), end
    (ISO), ongoing (bool), and `roles` (the role words that were read,
    so a caller can tell an explicit "X is the talent" from a guess).
    Empty dict when the sentence names nothing."""
    today = today or date.today()
    t = _norm(text)
    out = {}
    if not t or len(t) > 600:
        return out
    roles = []
    for m in _NAME_THEN_ROLE_RX.finditer(t):
        role, name = _role_of(m.group('role')), _clean_name(m.group('name'))
        if role and name and role not in out:
            out[role] = name
            roles.append(role)
    for m in _ROLE_THEN_NAME_RX.finditer(t):
        role, name = _role_of(m.group('role')), _clean_name(m.group('name'))
        if role and name and role not in out:
            out[role] = name
            roles.append(role)
    if not (out.get('talent') and out.get('brand')):
        m = _BETWEEN_RX.search(t) or _PARTNERED_RX.search(t)
        if m:
            a, b = _clean_name(m.group('a')), _clean_name(m.group('b'))
            if a and b:
                out.setdefault('talent', a)
                out.setdefault('brand', b)
                roles.append('pair')
    m = _START_RX.search(t)
    if m:
        d, precise, label = _date_from_match(m, today)
        if d is not None:
            out['start'] = d.isoformat()
            out['start_precise'] = bool(precise)
            if label:
                out['start_label'] = label
    m = _END_RX.search(t)
    if m:
        d, precise, _label = _date_from_match(m, today)
        if d is not None and precise:
            out['end'] = d.isoformat()
    if says_ongoing(t):
        out['ongoing'] = True
    if roles:
        out['roles'] = roles
    return out


_DATE_SUBJECT_RX = (r"(?:[,.;]?\s*(?:and\s+)?(?:it|they|which|that|the\s+(?:partnership|campaign|deal|collab(?:oration)?|"
                    r"collection|drop|program))\s+)?")
_STRIP_START_RX = re.compile(_DATE_SUBJECT_RX + _START_RX.pattern, re.I)
_STRIP_END_RX = re.compile(r"(?:[,.;]?\s*(?:and\s+)?(?:it\s+)?)?" + _END_RX.pattern, re.I)


def strip_dates(text):
    """The sentence without its start and end clauses ("Stanley x Target.
    It launched October 1" -> "Stanley x Target."), so the names can be
    read from what is left."""
    t = _norm(text)
    t = _STRIP_START_RX.sub(' ', t)
    t = _STRIP_END_RX.sub(' ', t)
    t = _ONGOING_RX.sub(' ', t)
    return re.sub(r"\s+([.,;!?])", r"\1", ' '.join(t.split())).strip(' ,;')


def names_slots(text, today=None):
    """True when the sentence names a wizard slot by its role ("X is the
    talent", "the brand is Y", "the partnership between X and Y") and is
    not a question."""
    t = _norm(text)
    if not t or '?' in t:
        return False
    got = parse(t, today)
    return bool(got.get('roles'))


def finds_assets(text):
    """The reader wants Prometheus to find the campaign assets."""
    return bool(_FIND_RX.search(_norm(text)))


def has_no_links(text):
    """The reader has no links to paste ("I don't have one", "not sure",
    "none")."""
    return bool(_NO_LINKS_RX.search(_norm(text)))


def known_casing(name, lookup=None):
    """A name typed in all capitals or all lower case takes the spelling
    the library already uses when `lookup(folded) -> display` knows it
    ("GAP" -> "Gap" from "Gap Customers" or "Gap - Avid Fan"). The
    library's display is reduced to its head: a cut suffix and a trailing
    audience noun are dropped. A name the library does not know, or one
    typed in mixed case, is returned as typed."""
    s = _norm(name)
    if not s or not callable(lookup):
        return s
    if not (s.isupper() or s.islower()):
        return s
    try:
        hit = lookup(_fold(s))
    except Exception:
        hit = None
    hit = _norm(hit)
    if not hit:
        return s
    head = hit.split(' - ')[0].strip()
    head = _AUDIENCE_TAIL_RX.sub('', head).strip() or head
    return head if _fold(head) == _fold(s) else s


# ---------------------------------------------------------------------------
# Two names in one answer, and more than one partnership in one ask
# (2026-11-12, Samantha Kolton at CAA). "Your Friends & Neighbors (Apple
# TV show) and Pure Insurance" answered "Who is the property or talent?"
# with both sides of the partnership; "MATCHBOX THE FILM ON APPLE TV ...
# DID TWO PARTNERSHIPS WITH LEATHERMAN AND JIFFYLUBE, THE LEATHERMAN
# PARTNERSHIP LAUNCHED SEPT 29 AND THE JIFFY LUBE ONE LAUNCHED OCT 9, CAN
# YOU VALUE THESE PARTNERSHIPS IN THE 30 DAYS AFTER THE LAUNCH?" named a
# property, two brands, two launch days and the window. Neither was read.
_TALENT_HINT_RX = re.compile(
    r"\b(?:show|series|film|movie|title|property|talent|artist|athlete|creator|podcast|game|franchise|team|band|"
    r"league|tour|festival|book|documentary|actor|actress|singer|musician|comedian|host)\b", re.I)
_BRAND_HINT_RX = re.compile(r"\b(?:brand|sponsor|advertiser|retailer|company|insurer|bank|client)\b", re.I)
_PROPERTY_HINT_RX = re.compile(
    r"\b(?:show|series|film|movie|title|property|podcast|game|franchise|team|league|tour|festival|book|documentary)\b", re.I)
_PAREN_RX = re.compile(r"\s*\(([^()]{1,60})\)")
_SENTENCE_WORD_RX = re.compile(
    r"\b(?:is|are|was|were|did|does|do|has|have|had|will|can|could|should|would|want|need|value|worth|please|"
    r"partnership|partnerships|campaign|launched|started|ran)\b", re.I)
_PAIR_TAIL_LEAD_RX = re.compile(r"^(?:the|a|an|his|her|their|its|other|others|where|how|why|what|more|me|you|us|them|so|then)\b", re.I)
_SMALL_WORDS = frozenset(('a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'from', 'in', 'of', 'on', 'or', 'the', 'to',
                          'with', 'x', 'vs'))


def tidy_caps(name):
    """A name typed in all capitals inside a sentence ("MATCHBOX THE FILM
    ON APPLE TV") in title case: small words lower, a short token that
    could be initials (TV, UFC, JYT) left as typed. A name that already
    carries lower case is returned as typed."""
    s = _norm(name)
    if not s or not s.isupper() or len(s) <= 4:
        return s
    out = []
    for i, w in enumerate(s.split()):
        core = re.sub(r"[^A-Za-z]", '', w)
        if i and w.lower() in _SMALL_WORDS:
            out.append(w.lower())
        elif 0 < len(core) <= 3 and len(s.split()) > 1:
            out.append(w)
        else:
            out.append(w[:1] + w[1:].lower())
    return ' '.join(out)


def pair_answer(text):
    """(a, b, talent_side, kind) when one short answer names both sides of a
    partnership joined by "and" ("Your Friends & Neighbors (Apple TV
    show) and Pure Insurance", "Kevin Hart and Chase Bank"); None for
    anything else. talent_side is 'a' or 'b' when a note in brackets
    says which side is the property ("(Apple TV show)", "(the brand)"),
    else ''; kind is 'property' when that note names a title, a team or
    a show rather than a person. A single name that carries "and" is never split: with no
    note, both sides must run two words or more and the second must not
    open like the rest of a title ("... and the Goblet of Fire")."""
    t = _norm(text)
    if not t or '?' in t or len(t) > 160 or re.search(r"https?://", t, re.I):
        return None
    cut = None
    depth = 0
    for i, ch in enumerate(t):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif depth == 0 and t[i:i + 5].lower() == ' and ':
            cut = i
    if cut is None:
        return None
    raw_a, raw_b = t[:cut], t[cut + 5:]
    note_a = ' '.join(_PAREN_RX.findall(raw_a))
    note_b = ' '.join(_PAREN_RX.findall(raw_b))
    a = _clean_name(_PAREN_RX.sub(' ', raw_a))
    b = _clean_name(_PAREN_RX.sub(' ', raw_b))
    if not a or not b or _fold(a) == _fold(b):
        return None
    if _SENTENCE_WORD_RX.search(a) or _SENTENCE_WORD_RX.search(b) or ',' in a or ',' in b:
        return None
    side = ''
    if (note_a and _TALENT_HINT_RX.search(note_a)) or (note_b and _BRAND_HINT_RX.search(note_b)):
        side = 'a'
    elif (note_b and _TALENT_HINT_RX.search(note_b)) or (note_a and _BRAND_HINT_RX.search(note_a)):
        side = 'b'
    kind = 'property' if _PROPERTY_HINT_RX.search(note_a if side == 'a' else (note_b if side == 'b' else '')) else ''
    if not side:
        if len(a.split()) < 2 or len(b.split()) < 2 or _PAIR_TAIL_LEAD_RX.match(b):
            return None
    return a, b, side, kind


_MANY_RX = re.compile(
    r"\b(?:(?:two|three|four|five|2|3|4|5|both|several|multiple|a\s+couple\s+of|a\s+few)\s+)?(?:brand\s+)?"
    r"(?:partnerships|deals|collabs|collaborations|sponsorships|campaigns|tie[- ]ins)\s+with\s+(?P<list>[^.;:?!\n]{3,400})", re.I)
_OWNER_VERB_TAIL_RX = re.compile(
    r"\s+(?:did|does|has|had|have|ran|runs|signed|launched|announced|made|struck|inked|is\s+doing|is\s+running|"
    r"has\s+done|had\s+done|did\s+do|has\s+signed|has\s+launched)\s*$", re.I)
_OWNER_CUT_RX = re.compile(
    r"\s+(?:that|which|who)\s+(?:was|is|were|are|got|came|will|just|recently)\b|\s+(?:released|premiered|debuted|out)\s+(?:in|on)\b|\s*\(|,", re.I)
_DAYS_AFTER_RX = re.compile(
    r"\b(?:the\s+)?(?:first\s+)?(?P<n>\d{1,3})\s+days?\s+(?:after|following|from|post|since|of)\s+(?:the\s+|each\s+|its\s+|their\s+|every\s+)?"
    r"(?:launch(?:es)?|release|start|premiere|debut|drop|kick[- ]?off)\b", re.I)


def days_after_launch(text):
    """30 for "in the 30 days after the launch"; None when the words give
    no such window (or one outside 1 to 366 days)."""
    m = _DAYS_AFTER_RX.search(_norm(text))
    if not m:
        return None
    n = int(m.group('n'))
    return n if 1 <= n <= 366 else None


def many_partnerships(text, today=None):
    """What one ask names when it names more than one partnership:
    {'property', 'brands' (two or more, as typed), 'starts' (folded brand
    -> ISO launch day, where the words give one) and 'days_after' (30 for
    "the 30 days after the launch")}. None when the words name fewer than
    two partners. The property is '' when it cannot be read as a short
    name."""
    today = today or date.today()
    t = _norm(text)
    if not t or len(t) > 900:
        return None
    m = _MANY_RX.search(t)
    if not m:
        return None
    brands = []
    for piece in re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", m.group('list'), flags=re.I):
        p = re.sub(r"\s+(?:that|which|who|for|in|on|from|this|last|over|during|since)\b.*$", '', piece, flags=re.I).strip(" '\"")
        if not p or len(p.split()) > 4 or _SENTENCE_WORD_RX.search(p) or re.match(r"(?i)^(?:the|a|an|then|also|can|could|would)\b", p):
            break
        name = _clean_name(p)
        if not name:
            break
        if _fold(name) not in {_fold(x) for x in brands}:
            brands.append(name)
    if len(brands) < 2:
        return None
    # a partner written two ways in one ask ("JIFFYLUBE ... THE JIFFY LUBE ONE") takes the spaced spelling
    toks = re.findall(r"[A-Za-z0-9'&.\-]+", t)
    for i, b in enumerate(brands):
        if ' ' in b:
            continue
        for n in (2, 3):
            hit = next((' '.join(toks[j:j + n]) for j in range(len(toks) - n + 1)
                        if _fold(' '.join(toks[j:j + n])) == _fold(b)), '')
            if hit:
                brands[i] = hit
                break
    owner = t[:m.start()]
    owner = re.sub(r"\s+(?:two|three|four|five|2|3|4|5|both|several|multiple|a\s+couple\s+of|a\s+few)\s*$", '', owner, flags=re.I)
    owner = _OWNER_VERB_TAIL_RX.sub('', ' ' + owner).strip()
    cut = _OWNER_CUT_RX.search(owner)
    if cut:
        owner = owner[:cut.start()]
    owner = re.sub(r"^\W*(?:(?:hey|hi|ok|okay|so)[,\s]+)?(?:please\s+|can you\s+|could you\s+|would you\s+)?(?:value\s+)?", '', owner, flags=re.I)
    prop = _clean_name(re.sub(r"^(?:the|our|my|their|a|an|these|those|all|some)\b\s*", '', owner.strip(), flags=re.I))
    if prop and (_SENTENCE_WORD_RX.search(prop) or len(prop.split()) > 7):
        prop = ''
    starts = {}
    clauses = [c for c in re.split(r"[,;]|\band\s+the\b|\.\s", t, flags=re.I) if c.strip()]
    for b in brands:
        fb = _fold(b)
        for c in clauses:
            if fb and fb in _fold(c):
                sm = _START_RX.search(c)
                if sm:
                    d, precise, _label = _date_from_match(sm, today)
                    if d is not None and precise:
                        starts[fb] = d.isoformat()
                        break
    return {'property': prop, 'brands': brands, 'starts': starts, 'days_after': days_after_launch(t)}


__all__ = ['parse', 'names_slots', 'strip_dates', 'single_date', 'says_ongoing', 'is_filler', 'finds_assets', 'has_no_links',
           'known_casing', 'pair_answer', 'many_partnerships', 'days_after_launch', 'tidy_caps']
