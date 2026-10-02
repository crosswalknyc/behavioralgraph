"""Answer-quality guards shared by every Prometheus surface.

Audit of 2026-10-02 (Jenna: "find all errors and things that could be
errors and make it smarter and better and ensure no bad query
answers"). Each helper here closes one class of bad answer that real
sessions produced in the week before. All pure: no Flask, no S3, no
model call, so the routing step, the ask service, the legacy chat
module, and the tests all run the same code.

``is_question_shaped(text)``
    A question mark anywhere, or an interrogative opener on any
    sentence. The old test only looked at the very start and the very
    end, so "58.3% stayed for more. is this a strong number? for the
    other franchises ..." read as a build.

``is_capability_question(text)``
    "Can I ...", "is it possible to ...", "how do I ...", "do you
    support ...". A question about what the product can do is never
    a batch of builds and never a draft.

``subiq_is_explicit_pull(text)``
    A Subscriber IQ build needs a pull verb or the product name. A
    question that merely mentions new or reactivated viewers while
    reading the open Subscriber IQ page is a question, not a build.

``bare_reply_kind(text)``
    'affirm' | 'negative' | 'number' | 'none' | 'other' | None for a
    message that is only an acknowledgement, a pick, or a refusal.
    With nothing armed, these must never reach a reasoning pass that
    guesses a subject out of them ("approved" became a new build).

``is_streaming_platform(name)``
    Subscriber IQ reads one title on a platform. A platform name in
    the title slot ("Pull Subscriber IQ for Starz") must ask for the
    title at once instead of researching an air window for a network.

``sanitize_subject_label(s)``
    Strips model reasoning that leaked into a subject ("The prior
    turn asked for Starz; this turn asks for Starz+ ...") and the
    abstract-noun frames that turned a question fragment into a
    build ("Appeal of the Spiderwick Franchise" -> "Spiderwick
    Franchise"). Returns '' when nothing usable is left.

``time_window_cut_ask(text)``
    Detects "cuts by quarter / month / year / date" replies on the cut
    step and parses any named quarters into dated windows. The old
    path answered "that piece needs a closer look and I'll come back
    to you on it" and never did.

``humanize_name(s)``
    "THE_ROKU_CHANNEL" -> "The Roku Channel" for anything a reader
    sees. Short all-caps tokens (TV, NFL, HBO) stay as they are.
"""
import re

# --------------------------------------------------------------- shape
_Q_OPENERS = (
    r'what|which|who|whose|whom|how|why|where|when|do|does|did|are|is|'
    r'was|were|can|could|would|should|will|shall|may|might|have|has|'
    r'had|am|isn\'?t|aren\'?t|doesn\'?t|don\'?t|didn\'?t|can\'?t|'
    r'couldn\'?t|wouldn\'?t|shouldn\'?t|show me|tell me|give me|'
    r'compare|analy[sz]e|top \d+|break ?down|explain|define'
)
_Q_SENTENCE_RX = re.compile(
    r'(?:^|[.!?]\s+|\n\s*)(?:' + _Q_OPENERS + r')\b', re.I)
_Q_MARK_RX = re.compile(r'\?')

_CAPABILITY_RX = re.compile(
    r'^\s*(?:so\s+|and\s+|also\s+|just\s+curious[,:]?\s+|quick\s+'
    r'question[,:]?\s+)?'
    r'(?:can|could|would|will|do|does|did|is|are|should|may)\s+'
    r'(?:i|we|you|it|prometheus|crosswalk|this|that|the\s+tool|'
    r'the\s+system|the\s+dashboard|one|'
    r'(?:subscriber|profile|journey|digital\s+journey|attribution|'
    r'trends|impact|brand\s+partnership|analysis)\s*iq)\b'
    r'|^\s*(?:is\s+(?:it|there)\s+(?:possible|a\s+way|any\s+way)|'
    r'how\s+(?:do|can|could|would|should)\s+(?:i|we|you)|'
    r'what\s+(?:if|about|happens)\b|'
    r'do\s+you\s+(?:support|offer|have|allow|handle))', re.I)

_SUBIQ_PULL_RX = re.compile(
    r'\b(?:pull|run|build|start|launch|queue|kick\s+off|set\s+up|'
    r'order|get\s+me|give\s+me|send\s+me|i\s+(?:want|need|would\s+like)|'
    r'can\s+(?:you|we|i)\s+(?:run|pull|get|build|do)|please\s+run|'
    r'let\'?s\s+(?:run|pull|do))\b'
    r'[^.?!\n]{0,60}?'
    r'\b(?:subscriber\s*iq|sub\s*iq|subiq|churn|sign-?ups?|'
    r'retention|cancell?ations?|subscriber\s+read|svod\s+read)\b', re.I)
_SUBIQ_PRODUCT_RX = re.compile(r'\b(?:subscriber\s*iq|sub\s*iq|subiq)\b', re.I)
_ON_SCREEN_RX = re.compile(
    r'\b(?:it\s+says|on\s+(?:this|the)\s+(?:page|screen|tab|view|'
    r'chart|graph|table|card|tile)|this\s+(?:page|screen|chart|graph|'
    r'number|figure|stat)|the\s+(?:page|screen)\s+(?:says|shows)|'
    r'(?:is|does)\s+(?:that|this)\s+(?:mean|number|figure|percent|%))',
    re.I)


def is_question_shaped(text):
    t = str(text or '').strip()
    if not t:
        return False
    if _Q_MARK_RX.search(t):
        return True
    return bool(_Q_SENTENCE_RX.search(t))


def is_capability_question(text):
    t = str(text or '').strip()
    if not t:
        return False
    if not is_question_shaped(t):
        return False
    return bool(_CAPABILITY_RX.search(t))


def subiq_is_explicit_pull(text):
    """True when the ask plainly orders a Subscriber IQ build. A
    question that only mentions churn / reactivated / new viewers
    while reading an open page is not one."""
    t = str(text or '').strip()
    if not t:
        return False
    if _SUBIQ_PULL_RX.search(t):
        return True
    # The product named with no question shape: "Subscriber IQ on
    # Landman", "Landman subscriber iq please".
    if _SUBIQ_PRODUCT_RX.search(t) and not is_question_shaped(t):
        return True
    return False


def subiq_question_not_build(text, has_ctx=False):
    """A Subscriber IQ family hit that is really a question about the
    page or about the product: route it as a question."""
    t = str(text or '').strip()
    if not t or subiq_is_explicit_pull(t):
        return False
    if not is_question_shaped(t):
        return False
    if _ON_SCREEN_RX.search(t):
        return True
    if is_capability_question(t):
        return True
    # A question with data open and no pull verb is about the data.
    return bool(has_ctx)


# ---------------------------------------------------------- bare reply
_AFFIRM_RX = re.compile(
    r'^(?:y|yes|yeah|yep|yup|ya|sure|ok|okay|k|kk|fine|good|great|'
    r'perfect|correct|right|exactly|confirm(?:ed)?|approve[d]?|'
    r'approve\s+it|go|go\s+ahead|do\s+it|run\s+it|ship\s+it|proceed|'
    r'sounds\s+good|looks\s+good|that\s+works|works\s+for\s+me|'
    r'that\'?s\s+(?:right|it|fine|correct)|please\s+do|yes\s+please|'
    r'thanks?|thank\s+you|ty|cool|got\s+it|understood|noted)[.! ]*$',
    re.I)
_NEGATIVE_RX = re.compile(
    r'^(?:n|no|nope|nah|not\s+that|not\s+this|neither|not\s+quite|'
    r'wrong|incorrect|that\'?s\s+(?:wrong|not\s+(?:it|right))|'
    r'no\s+thanks?|no\s+thank\s+you|something\s+else|different|'
    r'other|another|never\s*mind|nevermind|cancel|stop|forget\s+it|'
    r'scratch\s+that|skip|skip\s+(?:it|that|this))[.! ]*$', re.I)
_NONE_RX = re.compile(
    r'^(?:none|no\s+cuts?|nothing|none\s+of\s+(?:those|them|these)|'
    r'n/?a|nil)[.! ]*$', re.I)
_NUMBER_RX = re.compile(
    r'^(?:option\s*)?(?:\d{1,2}|one|two|three|four|five|first|second|'
    r'third|the\s+first(?:\s+one)?|the\s+second(?:\s+one)?|'
    r'the\s+third(?:\s+one)?|the\s+last(?:\s+one)?)'
    r'(?:\s*(?:,|and|&)\s*(?:\d{1,2}|one|two|three|four|five))*'
    r'[.! ]*$', re.I)


def bare_reply_kind(text):
    """Classify a message that carries no ask of its own."""
    t = ' '.join(str(text or '').strip().split())
    if not t or len(t) > 40:
        return None
    if _NONE_RX.match(t):
        return 'none'
    if _NEGATIVE_RX.match(t):
        return 'negative'
    if _AFFIRM_RX.match(t):
        return 'affirm'
    if _NUMBER_RX.match(t):
        return 'number'
    return None


# ------------------------------------------------------------ platforms
_PLATFORMS = {
    'netflix', 'hulu', 'max', 'hbo max', 'hbo', 'disney+', 'disney plus',
    'disney', 'peacock', 'paramount+', 'paramount plus', 'paramount',
    'apple tv+', 'apple tv plus', 'apple tv', 'prime video',
    'amazon prime video', 'amazon prime', 'prime', 'starz', 'starz+',
    'starz plus', 'showtime', 'amc+', 'amc plus', 'discovery+',
    'discovery plus', 'espn+', 'espn plus', 'espn', 'tubi', 'pluto tv',
    'pluto', 'roku channel', 'the roku channel', 'roku', 'freevee',
    'crunchyroll', 'britbox', 'acorn tv', 'acorn', 'mgm+', 'mgm plus',
    'epix', 'fubo', 'fubotv', 'sling', 'sling tv', 'youtube tv',
    'youtube premium', 'youtube', 'philo', 'directv stream', 'hallmark+',
    'hallmark plus', 'bet+', 'bet plus', 'allblk', 'shudder',
    'criterion channel', 'kanopy', 'plex', 'xumo', 'vix', 'vix+',
    'telemundo', 'univision', 'cbs', 'nbc', 'abc', 'fox', 'the cw',
    'cw', 'fx', 'amc', 'bravo', 'tnt', 'tbs', 'usa network',
    'lifetime', 'hallmark', 'nickelodeon', 'cartoon network',
    'adult swim', 'comedy central', 'mtv', 'vh1', 'e!', 'tlc', 'hgtv',
    'food network', 'discovery', 'history', 'a&e', 'syfy', 'freeform',
    'own', 'bet', 'spectrum', 'xfinity', 'dazn', 'nfl+', 'nba league pass',
    'mlb.tv', 'peacock premium',
}
_PLAT_NORM_RX = re.compile(r'[^a-z0-9+& ]+')


def _norm_platform(s):
    t = _PLAT_NORM_RX.sub(' ', str(s or '').lower())
    t = ' '.join(t.split())
    for tail in (' subscribers', ' subscriber', ' subs', ' viewers',
                 ' users', ' members', ' app', ' streaming', ' network',
                 ' channel'):
        if t.endswith(tail) and t[:-len(tail)].strip() in _PLATFORMS:
            t = t[:-len(tail)].strip()
    return t


def is_streaming_platform(name):
    """True when the string is a streaming service or network name
    rather than a title."""
    t = _norm_platform(name)
    if not t:
        return False
    if t in _PLATFORMS:
        return True
    return t.replace(' plus', '+') in _PLATFORMS


# -------------------------------------------------------------- subject
_REASONING_LEAK_RX = re.compile(
    r'\b(?:the\s+)?(?:prior|previous|earlier|last|this|current|new)\s+'
    r'(?:turn|message|ask|request|prompt)\b|'
    r'\bthe\s+user\b|\busers?\s+(?:asked|wants?|said|meant)\b|'
    r'\bnot\s+the\s+original\b|\bNOT\b|\bi\.e\.|\be\.g\.|'
    r'\bnote[:\s]|\bclarif(?:y|ied|ication)\b|\bassum(?:e|ed|ing)\b|'
    r'\binterpret(?:ed|ing|ation)?\b|\bdecision\b', re.I)
_ABSTRACT_FRAME_RX = re.compile(
    r'^(?:the\s+)?(?:appeal|impact|future|growth|performance|'
    r'popularity|potential|value|reach|strength|awareness|success|'
    r'rise|decline|state|size|scale|momentum|health|trajectory|'
    r'outlook|prospects?|overview|analysis|assessment|review|'
    r'breakdown|profile|audience)\s+(?:of|for)\s+(?:the\s+)?', re.I)
_POTENTIAL_TITLE_RX = re.compile(
    r'^(?i:potential)\s+(?=(?i:the|a|an)\s+[A-Z]|[A-Z][a-z]+\s+[A-Z])')
_PERSONA_TAIL_RX = re.compile(
    r'\b(?:customers?|consumers?|buyers?|shoppers?|users?|subscribers?|'
    r'members?|owners?|renters?|viewers?|fans?|enthusiasts?|'
    r'switchers?|adopters?|intenders?|prospects?)\s*$', re.I)
# A modal + pronoun anywhere ("Can I Cut", "2Q 2026 Can I Cut") is a
# question clause, never a subject.
_INTERROGATIVE_CLAUSE_RX = re.compile(
    r'\b(?:can|could|would|should|will|do|does|did|is|are|was|were|may|'
    r'might)\s+(?:i|we|you|it|they|he|she|there|one)\b', re.I)
_ABBREV_LEAD_RX = re.compile(r'^(?:i\.?e\.?|e\.?g\.?|etc\.?)\b', re.I)

_QUESTION_WORDS_RX = re.compile(
    r'^(?:what|which|who|how|why|where|when|can|could|would|should|'
    r'is|are|do|does|did)\b', re.I)
_STOPWORDS = {
    'the', 'a', 'an', 'of', 'for', 'and', 'or', 'to', 'in', 'on', 'at',
    'by', 'with', 'from', 'about', 'as', 'is', 'are', 'was', 'were', 'be',
    'this', 'that', 'these', 'those', 'it', 'its', 'their', 'our', 'your',
    'my', 'his', 'her', 'we', 'i', 'you', 'they', 'he', 'she',
}


def sanitize_subject_label(s, max_len=90):
    """Return a clean subject string or '' when the input is not one."""
    t = ' '.join(str(s or '').replace('\n', ' ').split()).strip(' "\'')
    if not t:
        return ''
    # Model reasoning leaked into the field: keep the first clause
    # and only if it is itself clean.
    if _REASONING_LEAK_RX.search(t):
        first = re.split(r';|\s[-]{1,2}\s|\.\s|\(', t, 1)[0].strip()
        if _REASONING_LEAK_RX.search(first):
            first = first.split(':', 1)[0].strip()
        if not first or _REASONING_LEAK_RX.search(first):
            return ''
        t = first
    # Abstract frames: "Appeal of the Spiderwick Franchise".
    for _ in range(2):
        t2 = _ABSTRACT_FRAME_RX.sub('', t)
        if t2 == t:
            break
        t = t2.strip()
    # "Potential The Influencer Project" (a title, not a persona).
    if _POTENTIAL_TITLE_RX.match(t) and not _PERSONA_TAIL_RX.search(t):
        t = t[len('potential'):].strip()
    t = t.strip(' ,.;:-')
    if not t:
        return ''
    if _ABBREV_LEAD_RX.match(t) or _INTERROGATIVE_CLAUSE_RX.search(t):
        return ''
    if _QUESTION_WORDS_RX.match(t) and is_question_shaped(t + '?'):
        # "What Drives Purchases" is a question fragment, not a subject
        # (only when it reads as a full interrogative clause).
        words = t.split()
        if len(words) >= 3:
            return ''
    if len(t) > max_len:
        t = t[:max_len].rsplit(' ', 1)[0].strip(' ,.;:-')
    words = [w for w in re.findall(r"[A-Za-z0-9&+'.-]+", t)]
    if not words:
        return ''
    content = [w for w in words if w.lower() not in _STOPWORDS]
    if not content:
        return ''
    if words[-1].lower() in ('and', 'or', 'of', 'the', 'for', 'with',
                             'to', 'in', 'on', 'a', 'an'):
        return ''
    return t


# ------------------------------------------------------- time windows
_QUARTER_RX = re.compile(
    r'\b(?:(?P<q1>[1-4])\s*q|q\s*(?P<q2>[1-4]))\s*'
    r'(?:[\'’]?(?P<y>\d{2}|\d{4}))?\b', re.I)
_QUARTER_WORD_RX = re.compile(
    r'\b(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter'
    r'(?:\s+(?:of\s+)?(?P<y>\d{4}))?\b', re.I)
_WINDOW_CUT_RX = re.compile(
    r'\b(?:cuts?|slices?|splits?|breaks?|break\s*downs?|reads?|'
    r'views?|versions?)\b[^.?!\n]{0,40}\bby\s+'
    r'(?:quarter|quarters|q[1-4]|month|months|year|years|week|weeks|'
    r'date|dates|period|periods|time|season)\b'
    r'|\b(?:quarterly|monthly|yearly|annual|weekly)\s+'
    r'(?:cuts?|slices?|splits?|reads?|views?|breakdowns?)\b'
    r'|\bby\s+(?:quarter|month|year|week|date|time\s+period)\b', re.I)
_ORD_TO_Q = {'first': 1, '1st': 1, 'second': 2, '2nd': 2, 'third': 3,
             '3rd': 3, 'fourth': 4, '4th': 4}
_Q_BOUNDS = {1: ('01-01', '03-31'), 2: ('04-01', '06-30'),
             3: ('07-01', '09-30'), 4: ('10-01', '12-31')}


def _year_norm(y, default_year):
    if not y:
        return default_year
    y = str(y)
    if len(y) == 2:
        return 2000 + int(y)
    return int(y)


def parse_quarters(text, default_year=None):
    """[{'label': '2Q 2026', 'start': '2026-04-01', 'end': '2026-06-30'}]
    for every quarter named in the text, in order, de-duplicated."""
    import datetime as _dt
    default_year = default_year or _dt.date.today().year
    out, seen = [], set()
    t = str(text or '')
    hits = []
    for m in _QUARTER_RX.finditer(t):
        q = int(m.group('q1') or m.group('q2'))
        hits.append((m.start(), q, m.group('y')))
    for m in _QUARTER_WORD_RX.finditer(t):
        q = _ORD_TO_Q[m.group('ord').lower()]
        hits.append((m.start(), q, m.group('y')))
    # A year named anywhere in the reply carries to the quarters that
    # name none ("first quarter of 2025 and second quarter").
    named_years = [_year_norm(y, None) for _, _, y in hits if y]
    fill_year = named_years[0] if named_years else default_year
    resolved = []
    for pos, q, y in sorted(hits):
        if y:
            fill_year = _year_norm(y, default_year)
        resolved.append((pos, q, fill_year))
    for _, q, y in resolved:
        if (q, y) in seen:
            continue
        seen.add((q, y))
        a, b = _Q_BOUNDS[q]
        out.append({'label': f'{q}Q {y}', 'start': f'{y}-{a}',
                    'end': f'{y}-{b}'})
    return out


def time_window_cut_ask(text, default_year=None):
    """None when the text is not a time-window cut request. Otherwise
    {'kind': 'quarter'|'month'|'year'|'week'|'date', 'quarters': [...]}
    where quarters holds the parsed, dated quarters (possibly empty)."""
    t = str(text or '')
    if not t.strip():
        return None
    quarters = parse_quarters(t, default_year=default_year)
    m = _WINDOW_CUT_RX.search(t)
    if not m and not quarters:
        return None
    low = t.lower()
    if quarters or 'quarter' in low or re.search(r'\bq[1-4]\b', low):
        kind = 'quarter'
    elif 'month' in low:
        kind = 'month'
    elif 'year' in low or 'annual' in low:
        kind = 'year'
    elif 'week' in low:
        kind = 'week'
    else:
        kind = 'date'
    return {'kind': kind, 'quarters': quarters}


_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
           'Oct', 'Nov', 'Dec')


def window_phrase(start, end):
    """'Apr 1 to Jun 30, 2026' from two ISO dates; falls back to the
    raw strings."""
    try:
        sy, sm, sd = [int(x) for x in str(start)[:10].split('-')]
        ey, em, ed = [int(x) for x in str(end)[:10].split('-')]
        if sy == ey:
            return f'{_MONTHS[sm - 1]} {sd} to {_MONTHS[em - 1]} {ed}, {ey}'
        return (f'{_MONTHS[sm - 1]} {sd}, {sy} to '
                f'{_MONTHS[em - 1]} {ed}, {ey}')
    except Exception:
        return f'{start} to {end}'


_PROFILE_NAMED_RX = re.compile(
    r'\b(?:the\s+)?(?:existing\s+|current\s+|finished\s+)?'
    r'(?P<n>[A-Z][\w+&\'.-]*(?:\s+[A-Z0-9][\w+&\'.-]*){0,4})\s+'
    r'(?:profile|read|file|audience)\b')


def _profile_named_in(text):
    m = _PROFILE_NAMED_RX.search(str(text or ''))
    if not m:
        return ''
    n = m.group('n').strip()
    if n.lower() in ('can', 'the', 'this', 'that', 'a', 'an', 'my',
                     'our', 'i', 'existing', 'current'):
        return ''
    return n


# ------------------------------------------------------ capability answers
_CAP_COMPARE_RX = re.compile(r'\bcompare|\bside\s+by\s+side|\boverlap\b', re.I)
_CAP_CUT_RX = re.compile(
    r'\b(?:cut|slice|split|segment|break\s*down|breakdown)\b', re.I)
_CAP_DEMO_RX = re.compile(
    r'\b(?:gender|female|male|women|men|age|ages|generation|gen\s*z|'
    r'millennial|boomer|income|hhi|ethnicity|hispanic|black|asian|'
    r'market|dma|city|state|region|geo|parents?|kids|avid|casual)\b',
    re.I)


def capability_answer(text, cut_credits=3):
    """A plain answer for a question about what the product can do,
    or None when the ask is not one of the families answered here.
    Only fires on capability-shaped questions (see
    is_capability_question) so a real build or read never lands
    here."""
    t = str(text or '').strip()
    if not t or not is_capability_question(t):
        return None
    win = time_window_cut_ask(t)
    if win:
        kind = win['kind']
        named = win.get('quarters') or []
        prof = _profile_named_in(t)
        if kind == 'quarter':
            base = ('A profile reads a 12 month window by default. For '
                    'quarters I run one dated read per quarter on the '
                    'same audience, so the numbers line up quarter to '
                    'quarter, and the brief shows the credits before '
                    'anything runs. ')
            if named:
                lst = ', '.join(
                    f"{q['label']} ({window_phrase(q['start'], q['end'])})"
                    for q in named)
                who = prof or 'that profile'
                sends = [f"Run {who} for {q['label']} "
                         f"({window_phrase(q['start'], q['end'])})"
                         for q in named[:3]]
                reply = (f"Yes. {base}You named {lst}. Say \"{sends[0]}\" "
                         "and I will set up that dated read.")
            else:
                sends = ['2Q 2026 and 3Q 2026']
                reply = (f"Yes. {base}Which quarters do you want? For "
                         "example \"2Q 2026 and 3Q 2026\".")
            return {'reply': reply, 'followups': sends,
                    'family': 'time_window_cut'}
        unit = {'month': 'month', 'year': 'year', 'week': 'week'}.get(
            kind, 'date range')
        return {
            'reply': (
                f'Yes. A profile reads a 12 month window by default. For a '
                f'{unit} view I run one dated read per {unit} on the same '
                'audience, so the numbers line up period to period, and '
                'the brief shows the credits before anything runs. Name '
                f'the {unit}s you want and I will set it up.'),
            'followups': [], 'family': 'time_window_cut'}
    if _CAP_CUT_RX.search(t) and _CAP_DEMO_RX.search(t):
        return {
            'reply': (
                'Yes. Any finished profile can be cut by gender, age or '
                'generation, market, income, parents, or a behavior, at '
                f'{cut_credits} credits per cut, each derived from the '
                'parent so the numbers ladder up. Tell me the profile and '
                'the cut, for example "cut Apple TV+ by gender".'),
            'followups': [], 'family': 'cut'}
    if _CAP_COMPARE_RX.search(t):
        return {
            'reply': (
                'Yes. Open the profiles you want in tabs and say "compare '
                'open tabs", or name them, for example "compare Peacock '
                'against Paramount+", and I will run the side by side.'),
            'followups': ['Compare open tabs'], 'family': 'compare'}
    return None


# ---------------------------------------------------------------- names
_KEEP_CAPS = {'tv', 'nfl', 'nba', 'mlb', 'nhl', 'mls', 'ufc', 'wwe',
              'hbo', 'amc', 'cbs', 'nbc', 'abc', 'fox', 'cnn', 'espn',
              'bet', 'mtv', 'tlc', 'hgtv', 'usa', 'uk', 'us', 'ai',
              'iq', 'svod', 'avod', 'fast', 'pvod', 'est', 'tvod',
              'cw', 'fx', 'tnt', 'tbs', 'own', 'dc', 'la', 'nyc',
              'bbc', 'itv', 'pbs', 'npr', 'ea', 'xbox', 'ps5', 'ps4',
              'ios', 'mgm', 'cnbc', 'msnbc', 'nfl+', 'ii', 'iii', 'iv'}
_SMALL = {'of', 'the', 'and', 'a', 'an', 'in', 'on', 'at', 'to', 'for',
          'by', 'or', 'vs', 'de', 'la', 'le', 'du'}


def humanize_name(s):
    """Readable form of a slug or shouting name; other strings pass
    through unchanged."""
    t = str(s or '').strip()
    if not t:
        return t
    slug = '_' in t and ' ' not in t
    shouting = t.isupper() and len(t) > 4 and any(c.isalpha() for c in t)
    if not (slug or shouting):
        return t
    words = re.split(r'[_\s]+', t)
    out = []
    for i, w in enumerate(words):
        if not w:
            continue
        lw = w.lower()
        if lw in _KEEP_CAPS:
            out.append(w.upper())
        elif i > 0 and lw in _SMALL:
            out.append(lw)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return ' '.join(out)
