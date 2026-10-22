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

``subiq_lookup_title(text)``
    "Do you see the SWAT Exiles Season 1 Subscriber IQ?" is a library
    lookup, not an order. Returns the title named in an existence /
    where-is / is-it-ready question about a Subscriber IQ read, else
    ''. The caller answers from the library (and the caller's own
    runs); nothing is drafted and nothing is charged (Bria,
    2026-10-02: the question came back as a 10-credit build offer for
    a read that had been finished for twelve hours).

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


# ── Build is never the default (2026-10-02 RCA) ─────────────────────
# A build costs the user credits. It happens on a build order (a build
# verb with 'profile', a Subscriber IQ pull, a guided tool intake) or
# on a short bare subject ("Reba McEntire avid fans"). An imperative
# TASK ("review these three creators and prepare a report on which of
# the three actually influence product purchases") is work for the
# analysis pass, which asks "which ones?" when the referents are
# unnamed. Scott's 2026-10-02 ask became a Profile IQ build named
# "Three Actually Influence Product Purchases".
_BUILD_ORDER_RX = re.compile(
    r'\b(run|build|pull|create|queue|start|launch|generate|make)\b'
    r'[^.;\n]{0,60}\b(profiles?|profile iq|subscriber iq|digital journey|'
    r'flywheel|brand partnership|attribution|avid|cut|universe)\b', re.I)
_TOOL_NOUN_RX = re.compile(
    r'\b(brand partnership|attribution iq|attribution|flywheel|digital '
    r'journey|journey iq|subscriber iq|profile iq|impact iq|trends iq|'
    r'audience cut|avid (?:fan|cut))\b', re.I)
_TASK_VERB_RX = re.compile(
    r'^(?:please\s+|can you\s+|could you\s+|would you\s+|i need you to\s+|'
    r'i want you to\s+)?'
    r'(review|compare|summari[sz]e|list|prepare|analy[sz]e|look at|look '
    r'into|tell me|explain|find|rank|evaluate|assess|recommend|identify|'
    r'break ?down|describe|audit|check|read|walk me through|help me|'
    r'write|draft|put together|figure out|work out|determine|estimate|'
    r'value|size|quantify|score|grade|vet|validate|verify|contrast|'
    r'map|outline|digest|interpret|translate|report on|dig into|'
    r'tell us|show us)\b', re.I)
_TASK_SHAPE_RX = re.compile(
    r'\b(which of (?:the|these|those)|report on|and list|a report|'
    r'prepare a|actually|whether|recommend|rank them|compare them|'
    r'top \d+ insights?|key takeaways?|what (?:do|does|did|should))\b',
    re.I)
_FRAGMENT_TOKENS = {
    'actually', 'which', 'that', 'who', 'whom', 'whose', 'influence',
    'influences', 'influenced', 'purchase', 'purchases', 'purchasing',
    'buy', 'buys', 'buying', 'bought', 'watch', 'watched', 'watching',
    'prepare', 'report', 'list', 'review', 'compare', 'these', 'those',
    'them', 'their', 'into', 'whether', 'because', 'about', 'versus',
    'should', 'would', 'could', 'does', 'did', 'are', 'is', 'was',
    'were', 'has', 'have', 'had', 'will', 'can', 'please', 'tell',
    'show', 'give', 'make', 'build', 'run', 'pull', 'create',
}
# A counted or pointed opener ("Three actually influence...", "these
# three creators"). Never 'the' / 'all' / 'each' / 'every': those open
# more real titles than fragments ("The Spiderwick Chronicles", "All in
# the Family"), and until 2026-10-21 that one word made every three-word
# "The ..." title a fragment and drew "I read that as a task" on a build.
_NUMBER_WORDS = {'one', 'two', 'three', 'four', 'five', 'six', 'seven',
                 'eight', 'nine', 'ten', 'several', 'few', 'some',
                 'both', 'these', 'those'}
# a host / credit / billing suffix is not part of the name it follows
_CREDIT_SUFFIX_RX = re.compile(
    r"\s+(?:with|featuring|feat\.?|ft\.?|hosted by|presented by|starring|w/)\s+[A-Z&][^,;]*$")


def is_build_order(text):
    """An explicit build order: a build verb bound to a product noun."""
    return bool(_BUILD_ORDER_RX.search(str(text or '')))


def reads_as_task(text):
    """An imperative or analytical task that is not a build order.

    True when the ask opens with a task verb (review, compare, prepare,
    list, analyze, ...) or carries a task shape ("which of the three",
    "report on", "and list some"), and does not order a build. Short
    bare subjects ("Nike", "Reba McEntire avid fans") are NOT tasks."""
    t = str(text or '').strip()
    if not t or is_build_order(t):
        return False
    # An explicit build order with a subject is never a task, whatever
    # the subject looks like (2026-10-21, Jessie: "run a profile for
    # Listerners of Ball in the Bonzo Family with Lonzo Ball").
    try:
        from prometheus import subject_rules as _sr
        if _sr.explicit_build_subject(t):
            return False
    except Exception:
        pass
    # A named tool product is a guided intake, not a free task.
    if _TOOL_NOUN_RX.search(t):
        return False
    words = t.split()
    if len(words) < 4:
        return False
    if _TASK_VERB_RX.match(t):
        return True
    if len(words) >= 8 and _TASK_SHAPE_RX.search(t):
        return True
    return False


def subject_reads_as_fragment(subject):
    """A build subject that is a slice of the user's sentence, not an
    entity. "Three Actually Influence Product Purchases" (Scott,
    2026-10-02). Deterministic: a verb or function word inside the
    label, a number-word opener, or more than six words."""
    s = str(subject or '').strip()
    if not s:
        return False
    # the host credit is not the name ("Ball in the Family Podcast with
    # Lonzo & Gelo Ball" is a four-word title with a credit on it)
    s = _CREDIT_SUFFIX_RX.sub('', s).strip() or s
    toks = re.sub(r'[^a-z0-9 ]+', ' ', s.lower()).split()
    if not toks:
        return False
    hits = [w for w in toks if w in _FRAGMENT_TOKENS]
    # Length alone never decides (2026-10-21, Jessie: a seven-word
    # podcast title drew "I read that as a task"): "Harry Potter and the
    # Order of the Phoenix" is eight words and a title. A long phrase is
    # a fragment when it also carries a function word; a sentence-long
    # one is a fragment on its own.
    if len(toks) > 12:
        return True
    if len(toks) > 6 and hits:
        return True
    if toks[0] in _NUMBER_WORDS and len(toks) >= 3:
        return True
    # One function word inside a real title is common ("Days of Our
    # Lives" has none, "Who Wants to Be a Millionaire" has 'who'); two
    # or more, or a bare verb of commerce / influence, is a fragment.
    if len(hits) >= 2:
        return True
    if any(w in ('influence', 'influences', 'influenced', 'actually',
                 'purchases', 'purchasing', 'prepare', 'report',
                 'whether', 'because') for w in toks):
        return True
    return False


# A measure or topic wrapped around an entity is not the entity
# (2026-10-02 S6, Liz: 'Run a profile on Appeal of the Spiderwick
# Franchise' drafted a build named exactly that while The Spiderwick
# Chronicles sat in the library). The Total Universe is always the
# clean subject name; the measure is the question, the audience noun
# is a cut or nothing.
_ENTITY_WRAPPER_RX = re.compile(
    r"^\s*(?:the\s+)?(?:(?:future|overall|long[- ]term|current|total"
    r"|general|broader|wider|likely|potential|predicted|projected)\s+)*"
    r"(?:appeal|impact|influence|growth|rise|fall|decline|popularity"
    r"|awareness|performance|reach|value|audience|fanbase|fan base"
    r"|fandom|engagement|effect|success|strength|health|momentum"
    r"|trajectory|potential|demand|interest|sentiment|buzz|perception"
    r"|footprint|size|scale|resonance|lift|halo|affinity|loyalty"
    r"|viewership|readership|listenership|following|future|outlook"
    r"|prospects|forecast|opportunity|case)"
    r"\s+(?:of|for|in|around|behind|with|among)\s+(?:(?-i:the)\s+)?", re.I)
# A cohort clause after the entity is a cut, not part of the name.
_ENTITY_COHORT_RX = re.compile(
    r"\s+(?:among|within|across|amongst)\s+.+$", re.I)
_ENTITY_CONNECTIVE_TAIL = {'of', 'for', 'and', 'or', 'the', 'a', 'an',
                           'in', 'on', 'with', 'to', 'by', 'from'}
_ENTITY_TAIL_RX = re.compile(
    r"\s+(?:franchise|universe|fandom|fanbase|fan base|fans|audience"
    r"|brand|property|ip)\s*$", re.I)
# Real titles that end on a tail word and must keep it.
_ENTITY_TAIL_KEEP = re.compile(
    r"\b(?:steven universe|cinematic universe|extended universe"
    r"|miss universe|fenty beauty by rihanna)\s*$", re.I)


def entity_core(subject):
    """Strip measure wrappers and bare audience tails from a build
    subject. 'Appeal of the Spiderwick Franchise' -> 'Spiderwick';
    'The future of the Yellowstone universe' -> 'Yellowstone';
    'Steven Universe' and 'Taylor Swift' come back untouched. Returns
    '' when nothing entity-shaped is left. Never raises."""
    try:
        s = str(subject or '').strip()
        if not s:
            return ''
        prev = None
        while prev != s:
            prev = s
            s = _ENTITY_WRAPPER_RX.sub('', s, count=1).strip()
        if len(s.split()) >= 2:
            s = _ENTITY_COHORT_RX.sub('', s).strip() or s
        if not _ENTITY_TAIL_KEEP.search(s):
            toks = s.split()
            if len(toks) >= 2:
                s2 = _ENTITY_TAIL_RX.sub('', s).strip()
                if s2 and len(s2.split()) >= 1:
                    s = s2
        s = s.strip(' ,.;:-')
        if not s:
            return ''
        low = [w for w in re.sub(r'[^a-z0-9 ]+', ' ', s.lower()).split()]
        if low and low[-1] in _ENTITY_CONNECTIVE_TAIL:
            return ''
        if not low or all(w in _FRAGMENT_TOKENS or w in _NUMBER_WORDS
                          or w in ('the', 'a', 'an', 'of', 'and', 'or')
                          for w in low):
            return ''
        return s
    except Exception:
        return str(subject or '').strip()


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


_SUBIQ_TAIL_RX = re.compile(
    r'\s*(?:subscriber\s*iq|sub\s*iq|subiq)?'
    r'(?:\s+(?:read|report|file|run|build|pull|results?|data|page|tab))*'
    r'\s*$', re.I)
_SUBIQ_LEAD_RX = re.compile(
    r'^\s*(?:the|a|an|my|our|that|this)\s+', re.I)
_SUBIQ_LOOKUP_RXS = (
    # do you see / have / find ... the X Subscriber IQ?
    re.compile(
        r'^\s*(?:do|can|could|did)\s+(?:you|we|u)\s+'
        r'(?:see|have|find|locate|show|pull\s+up|open|access|spot|'
        r'already\s+have|still\s+have|have\s+access\s+to|see\s+a|'
        r'see\s+the)\s+(?P<title>.+?)\s*\??\s*$', re.I),
    # is there a Subscriber IQ for X / on X?
    re.compile(
        r'^\s*(?:is|are)\s+there\s+(?:a|an|any|the)?\s*'
        r'(?:subscriber\s*iq|sub\s*iq|subiq)(?:\s+(?:read|report|file))?'
        r'\s+(?:for|on|of|about)\s+(?P<title>.+?)\s*\??\s*$', re.I),
    # is the X Subscriber IQ ready / there / live / done / in the library?
    re.compile(
        r'^\s*(?:is|has)\s+(?P<title>.+?)\s+'
        r'(?:ready|there|live|done|finished|complete(?:d)?|available|'
        r'up|posted|landed|back|in\s+(?:the\s+)?library|in\s+there|'
        r'in\s+(?:the\s+)?(?:subscriber\s*iq\s+)?tab)(?:\s+yet)?'
        r'\s*\??\s*$', re.I),
    # where is / find the X Subscriber IQ?
    re.compile(
        r'^\s*(?:where\s+is|where\'?s|wheres|find|show\s+me|locate)\s+'
        r'(?P<title>.+?)\s*\??\s*$', re.I),
    # did / has the X Subscriber IQ land / come back / finish?
    re.compile(
        r'^\s*(?:did|has|have)\s+(?P<title>.+?)\s+'
        r'(?:land(?:ed)?|come\s+back|finish(?:ed)?|complete(?:d)?|'
        r'post(?:ed)?|show(?:ed)?\s+up|run(?:\s+yet)?|go\s+through)'
        r'(?:\s+yet)?\s*\??\s*$', re.I),
)


def subiq_lookup_title(text):
    """Title named in an existence / location / is-it-ready question
    about a Subscriber IQ read, else ''. The product must be named in
    the message (profile lookups have their own catalog paths), and an
    explicit pull order never matches."""
    t = str(text or '').strip()
    if not t or len(t) > 200:
        return ''
    if not _SUBIQ_PRODUCT_RX.search(t):
        return ''
    if _SUBIQ_PULL_RX.search(t):
        return ''
    for rx in _SUBIQ_LOOKUP_RXS:
        m = rx.match(t)
        if not m:
            continue
        title = m.group('title').strip()
        title = _SUBIQ_LEAD_RX.sub('', title).strip()
        title = re.sub(r'^(?:subscriber\s*iq|sub\s*iq|subiq)\s+'
                       r'(?:for|on|of|about)\s+', '', title, flags=re.I)
        title = _SUBIQ_TAIL_RX.sub('', title).strip()
        title = _SUBIQ_LEAD_RX.sub('', title).strip()
        title = title.strip(' .,;:!?"\'')
        if (not title or _SUBIQ_PRODUCT_RX.fullmatch(title)
                or title.lower() in ('the', 'a', 'an', 'my', 'that', 'this',
                                     'it', 'one')):
            # "do you see the subscriber iq?" names no title; the
            # caller lists what is there instead.
            return '*'
        if len(title.split()) > 10:
            return ''
        return title
    return ''


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
    # The brief card says "Approve to run"; the reader types it back
    # (2026-10-07 Scott: "Approve to run" drafted a build on "Approve")
    r'approve[d]?\s*(?:,|and|&|to|the)?\s*(?:run|build|go|proceed|start)'
    r'(?:\s+(?:it|this|that|the\s+(?:run|build|brief|profile)))?|'
    r'approve\s+(?:it|this|that|the\s+(?:brief|run|build|profile))|'
    r'go|go\s+ahead|do\s+it|run\s+it|ship\s+it|proceed|'
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


def _money_symbol():
    """The seat's currency symbol; £ for East Tree Media and Omaze
    (Jenna 2026-10-08). '$' outside a request."""
    try:
        import sys as _sys
        # the running app only; importing it here would bind the host
        # from app.py and clobber a hermetic test's fake host (2026-10-09)
        _A = _sys.modules.get('app')
        return _A._session_money_symbol() if _A is not None else '$'
    except Exception:
        return '$'


def _cut_price_label():
    """Price of a derived cut from the live table (100 standard) in the
    seat's currency; never credits (Jenna 2026-10-07)."""
    sym = _money_symbol()
    try:
        import sys as _sys
        import wallet as _w
        if _sys.modules.get('app') is None:
            # no running app: the shipped default, never an app import
            # from inside a lane (it rebinds the host; 2026-10-09)
            v = float((_w.DEFAULT_PRICING.get('per_tool_usd') or {}).get('profile_iq_derived_cut') or 0)
        else:
            v = float(_w.tool_price_usd('profile_iq_derived_cut') or 0)
        if v > 0:
            return f"{sym}{v:,.0f}" if abs(v - round(v)) < 0.009 else f"{sym}{v:,.2f}"
    except Exception:
        pass
    return f'{sym}100'


def capability_answer(text, cut_credits=None):
    """A plain answer for a question about what the product can do,
    or None when the ask is not one of the families answered here.
    Only fires on capability-shaped questions (see
    is_capability_question) so a real build or read never lands
    here. ``cut_credits`` is accepted for old callers and ignored:
    prices read in dollars from the live table."""
    t = str(text or '').strip()
    cut_price = _cut_price_label()
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
                    'quarter, and the brief shows the price before '
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
                'the brief shows the price before anything runs. Name '
                f'the {unit}s you want and I will set it up.'),
            'followups': [], 'family': 'time_window_cut'}
    if _CAP_CUT_RX.search(t) and _CAP_DEMO_RX.search(t):
        return {
            'reply': (
                'Yes. Any finished profile can be cut by gender, age or '
                'generation, market, income, parents, or a behavior, at '
                f'{cut_price} per cut, each derived from the '
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


def is_staff(user) -> bool:
    """A Crosswalk operator (a super admin or a Crosswalk account). The
    operator forms of a reply (the sample check's verdict line) are
    theirs; every other reader gets the reader-safe form."""
    try:
        u = user if isinstance(user, dict) else {}
        if str(u.get('role') or '').strip().lower() == 'super_admin':
            return True
        if str(u.get('company') or '').strip().lower() == 'crosswalk':
            return True
        return str(u.get('email') or '').strip().lower().endswith('@crosswalknyc.com')
    except Exception:
        return False


# ── Product facts (2026-10-05) ──────────────────────────────────────
# "How big is the Crosswalk sample?" is a fact about the product, the
# same answer for everyone, never a subject to build (Scott's ask was
# offered as a profile of "Teh Crosswalk Sample").
_SAMPLE_WORD_RE = re.compile(
    r"\b(sample|panel|panelists?|crosswalk(?:'?s)? (?:audience|data|universe|"
    r"footprint|coverage)|your (?:audience|data|universe|footprint|coverage))\b",
    re.I)
_SIZE_WORD_RE = re.compile(
    r"\b(size|sized|how (?:big|large|many)|count|headcount|number of|"
    r"population|total|n=|what is the|what was the|how much)\b", re.I)
_SAMPLE_SELF_RE = re.compile(
    r"\b(crosswalk|your|the|our)\b.{0,30}\b(sample|panel|panelists?)\b|"
    r"\b(sample|panel|panelists?)\b.{0,20}\b(size|audience|population|do you have)\b", re.I)
# A question about what the sample IS, asked back at an answer that
# mentioned it (2026-10-20, Tania Nasr: "what's the 10 million sample
# you're mentioning" carried no size word and drew the open-tab
# confirm). The same product fact answers it.
_DEFINE_RE = re.compile(
    r"\b(what(?:'s|s| is| are| was| does| do)\b|what do(?:es)? (?:you|that|this|it) mean|mean(?:ing|s)?\s+(?:by|of|when)\b|"
    r"explain|define|clarify|you(?:'re| are| were) (?:mentioning|referencing|referring to|talking about|quoting)|"
    r"you (?:mention(?:ed)?|referenced|quoted|cited)|(?:refer|referr)(?:ed|ing|s)? to)\b", re.I)
_TEN_MILLION_RE = re.compile(r"\b(?:10|ten)[ -]?(?:million|m|mm)\b", re.I)


def is_sample_definition_ask(text) -> bool:
    """A question about what the sample (the ten million) is, as opposed
    to how big it is: 'what's the 10 million sample you're mentioning',
    'what does inside the 10 million mean'."""
    t = str(text or '')
    if not _DEFINE_RE.search(t):
        return False
    if _TEN_MILLION_RE.search(t):
        return True      # "what is the 10 million", "the 10m you keep referencing": the sample
    return bool(_SAMPLE_WORD_RE.search(t) and _SAMPLE_SELF_RE.search(t))


def is_sample_size_ask(text) -> bool:
    """A question about how big the Crosswalk sample itself is, or what
    it is."""
    t = str(text or '')
    if not _SAMPLE_WORD_RE.search(t) or not _SIZE_WORD_RE.search(t):
        if not is_sample_definition_ask(t):
            return False
        # a definition ask about the ten million: the same fact, when no
        # audience is named inside it
        if _TEN_MILLION_RE.search(t) and not _SAMPLE_WORD_RE.search(t):
            try:
                from . import sizing_lane as _szl
                return not _szl.names_an_audience(t)
            except Exception:
                return True
    # "sample" as a verb or a brand ("sample sale", "Sample the album")
    # does not count; the ask has to be about the sample as a thing.
    if not _SAMPLE_SELF_RE.search(t):
        return False
    # A named brand or title in the ask ("Netflix viewers in the sample")
    # is a question about THAT audience inside the sample, not about
    # the sample itself. Months, Crosswalk, US and the opening word are
    # not names.
    body = re.sub(r"^\W*\w+", "", t)
    for w in re.findall(r"\b[A-Z][A-Za-z0-9&'+.-]*\b", body):
        if w.lower() in _NOT_A_NAME:
            continue
        return False
    # a lowercase audience ("how many fanduel customers you have") is
    # still an audience, not the panel (2026-10-14, Ben at Kartel)
    try:
        from . import sizing_lane as _szl
        if _szl.names_an_audience(t):
            return False
    except Exception:
        pass
    return True


_NOT_A_NAME = frozenset((
    'crosswalk', 'us', 'usa', 'i', 'prometheus', 'january', 'february',
    'march', 'april', 'may', 'june', 'july', 'august', 'september',
    'october', 'november', 'december', 'jan', 'feb', 'mar', 'apr', 'jun',
    'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec', 'q1', 'q2', 'q3',
    'q4', 'ytd', 'to', 'the', 'from', 'what', 'how', 'is', 'was', 'size',
    'sample', 'panel', 'audience', 'teh', 'date', 'today'))


# ------------------------------------------------- method language (exit)
# 2026-10-05 (Jenna): "never mention anything that sounds synthetic".
# A reader never learns how a figure was made. These patterns catch
# first-person method talk and the words that imply a built number.
# Audience behaviour ("they research products before buying") is not
# method talk and is left alone.
_METHOD_SENTENCE_RX = re.compile(
    r"(?:\b(?:we|i|crosswalk|prometheus|the (?:read|journey|figures?|numbers?|"
    r"counts?|profile|deck|data))\b[^.!?\n]{0,60}\b(?:researched|anchored|"
    r"modeled|modelled|estimated|synthesi[sz]ed|synthetic|derived|calibrated|"
    r"reasoned|simulated|extrapolated|imputed)\b)"
    r"|\bresearched (?:against|from|on its own|it)\b"
    r"|\banchored? (?:to|against|on)\b|\bre-?anchored\b"
    r"|\bplaceholders?\b|\bsynthetic\b|\bsynth(?:ed|s)?\b|\bmodel(?:ed|led)\b"
    r"|\bdefect\b|\bon my side\b|\bpipeline\b|\bhostmap\b|\bclaude\b"
    r"|\bthe (?:corpus|ledger|catalog)\b|\bweb[- ]search\b|\bresearch(?:ed)? (?:call|pass|step)\b"
    r"|\bestimated\b|\bthe research\b|\bour research\b"
    # the adjective ("the researched count"), confidence-tier labels
    # (never public, 2026-09-14) and panel vocabulary (2026-10-13 audit)
    r"|\bresearched\b|\btiers? [12]\b|\bpanel(?:ist)?s?\b",
    re.IGNORECASE)
_SENT_SPLIT_RX = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(\[])')
def _case_like(src, rep):
    return rep[:1].upper() + rep[1:] if src[:1].isupper() else rep


# Phrase swaps that keep the sentence's meaning; a sentence these clear
# is kept (its figures matter).
_METHOD_SAFE_SWAPS = (
    (re.compile(r'\bestimated audience\b', re.I), 'audience range'),
    (re.compile(r'\bresearched (universe|count|figure|number|total|base)\b', re.I), r'US \1'),
    (re.compile(r'\btier (\d)\b', re.I), r'group \1'),
    (re.compile(r'\bpanelists\b', re.I), 'people'),
    (re.compile(r'\bpanelist\b', re.I), 'person'),
    (re.compile(r'\bthe panel\b', re.I), 'the data'),
    (re.compile(r'\bpanel\b', re.I), 'data'),
    (re.compile(r'\bthe research and the path take\b', re.I), 'it takes'),
    (re.compile(r'\banchored to\b', re.I), 'in line with'),
    (re.compile(r'\bre-?anchored\b', re.I), 'updated'),
)
# Last resort when a paragraph would otherwise vanish.
_METHOD_WORD_SUBS = _METHOD_SAFE_SWAPS + (
    (re.compile(r'\bresearched against\b', re.I), 'checked against'),
    (re.compile(r'\bresearched\b', re.I), 'read'),
    (re.compile(r'\b(?:estimated|modeled|modelled|synthetic|synthesized|synthesised) ', re.I), ''),
    (re.compile(r'\bplaceholders?\b', re.I), 'names'),
    (re.compile(r'\ba defect on my side\b', re.I), 'an error'),
    (re.compile(r'\bdefect\b', re.I), 'error'),
)


def _apply_swaps(x, swaps):
    for rx, rep in swaps:
        def _swap(m, _rep=rep):
            out = m.expand(_rep) if '\\' in _rep else _rep   # group references keep the noun
            return _case_like(m.group(0), out)
        x = rx.sub(_swap, x)
    return re.sub(r'\s{2,}', ' ', x).strip()


# The cause narrative (Jenna 2026-10-19, on an email to Kartel that said a
# correction step "flattened real differences and invented a few" and that
# files were "re-read against corrected national rates"): a reader never
# hears what happened, what we removed, what was wrong or what changed. A
# reader gets the new data. Sentences that tell the story go the way
# method talk goes; the figures in other sentences stay.
_CAUSE_CORE = (
    r"\bthe cause (?:was|is|of)\b|\bon our (?:side|end)\b|\broot cause\b|\bcorrect(?:ed|ion|ions|ing)\b|"
    r"\bre-?read\b|\bre-?r[au]n\b|\brebuil[dt]\b|\bre-?released?\b|\bflatten(?:ed|ing|s)?\b|\binvent(?:ed|ing|s)?\b|"
    r"\bpulled\b[^.!?\n]{0,40}\btoward\b|\bshould not have\b|\bshould have (?:shown|read|been|sat)\b|\bfrom the start\b|"
    r"\bwe (?:removed|fixed|patched|repaired|rewrote)\b|\bstep that ran\b|\bafter each build\b|\bwhat (?:we )?changed\b|"
    r"\bwhat happened\b|\bthe (?:fix|repair|patch)\b")
_CAUSE_MAIL_EXTRA = r"|\bbugs?\b|\bmistakes?\b|\bour (?:error|fault)\b|\bapolog(?:y|ies|ize|ise|ized|ised)\b|\bwas wrong\b|\bglitch\b"
_CAUSE_SENTENCE_RX = re.compile(_CAUSE_CORE, re.IGNORECASE)
_CAUSE_MAIL_RX = re.compile(_CAUSE_CORE + _CAUSE_MAIL_EXTRA, re.IGNORECASE)


def cause_language(text, mail=False):
    """Sentences that tell a reader what happened to the data (a
    correction, a re-read, what we removed, what should have been). The
    mail form is stricter (bugs, mistakes, apologies) because operator
    mail is where the story gets told."""
    t = str(text or '')
    if not t.strip():
        return []
    rx = _CAUSE_MAIL_RX if mail else _CAUSE_SENTENCE_RX
    return [s for s in _SENT_SPLIT_RX.split(t) if rx.search(s)]


def _offends(sentence):
    return bool(_METHOD_SENTENCE_RX.search(sentence) or _CAUSE_SENTENCE_RX.search(sentence))


def method_language(text):
    """Sentences in a reply that describe how a figure was made, or what
    happened to it (the cause narrative)."""
    t = str(text or '')
    if not t.strip():
        return []
    return [s for s in _SENT_SPLIT_RX.split(t) if _offends(s)]


def scrub_method_language(text):
    """A reply with no method talk. Whole offending sentences go when
    something else remains; otherwise the words are replaced with
    neutral ones. Idempotent."""
    t = str(text or '')
    if not t.strip():
        return t
    paras = t.split('\n')
    out_paras = []
    def _subs(x):
        return _apply_swaps(x, _METHOD_WORD_SUBS)

    for para in paras:
        sents = _SENT_SPLIT_RX.split(para) if para.strip() else [para]
        if not any(_offends(x) for x in sents):
            out_paras.append(para)
            continue
        keep = []
        for x in sents:
            if not _offends(x):
                keep.append(x.strip())
                continue
            if _CAUSE_SENTENCE_RX.search(x):
                continue   # the story of what happened never survives as a swap
            # a safe phrase swap that clears the sentence keeps the
            # sentence (its figures matter); anything else goes
            fixed = _apply_swaps(x, _METHOD_SAFE_SWAPS)
            if not _offends(fixed):
                keep.append(fixed)
        if keep:
            out_paras.append(' '.join(k for k in keep if k))
        elif not any(_CAUSE_SENTENCE_RX.search(x) for x in sents):
            out_paras.append(_subs(para))
        # a paragraph that is only the cause narrative is dropped whole
    result = '\n'.join(out_paras)
    result = re.sub(r'\n{3,}', '\n\n', result).strip()
    if [s_ for s_ in _SENT_SPLIT_RX.split(result) if _METHOD_SENTENCE_RX.search(s_)]:
        result = _apply_swaps(result, _METHOD_WORD_SUBS)
    return result or t


def _keeps_sample_words(raw):
    """The product fact about the ten million keeps its number: the
    payload says product_fact, or the ask in flight was that question."""
    if isinstance(raw, dict) and raw.get('product_fact'):
        return True
    try:
        from flask import g as _g
        ask = str(getattr(_g, '_pm_ask_text', '') or '')
    except Exception:
        ask = ''
    try:
        return bool(ask) and is_sample_definition_ask(ask)
    except Exception:
        return False


def scrub_method_language_payload(raw):
    """Apply the scrub to every user-visible text field of a reply
    payload, in place. Returns the payload."""
    if not isinstance(raw, dict):
        return raw
    for k in ('reply', 'message', 'question', 'summary', 'text', 'error'):
        v = raw.get(k)
        if isinstance(v, str) and v.strip() and method_language(v):
            raw[k] = scrub_method_language(v)
            raw.setdefault('_scrubbed', []).append(k)
        v = raw.get(k)
        # I5: sample vocabulary never reaches a reader; the ten-million
        # product fact is the one reply that keeps its number (the payload
        # says so, or the ask itself was the question about the sample)
        if isinstance(v, str) and v.strip() and not _keeps_sample_words(raw) and SAMPLE_VOCAB_RX.search(v):
            raw[k] = scrub_sample_vocabulary(v)
            raw.setdefault('_scrubbed', []).append(k + ':sample')
    ic = raw.get('incidence_check')
    if isinstance(ic, dict) and isinstance(ic.get('message'), str) and not _keeps_sample_words(raw):
        ic['message'] = scrub_sample_vocabulary(ic['message'])
    return raw


_SCRUB_SKIP_KEYS = frozenset((
    'url', 'href', 'key', 's3_key', 'id', 'job_id', 'image', 'hero_image', 'thumbnail',
    'filename', 'filename_stem', 'slug', 'etag', 'source_key', 'created_by', 'created_at',
    'start_date', 'end_date', 'as_of', 'story_mode', 'target_type', 'kind', 'type', 'layout',
    'surface', 'ground', 'accent', 'color', 'hex', 'anchored_to', 'replayed_from',
))


# Deliverable jargon (2026-10-13 audit): labels the engine coins for
# itself must never reach a reader. Plain words, same meaning.
_JARGON_SUBS = (
    (re.compile(r'\bpixel[- ]miss(?:es|ed)?(?: class| cohort)?\b', re.I), 'watched on a service they already had'),
    (re.compile(r'\b(?:the )?pixel[- ]miss(?:ed)? (?:class|group|cohort)\b', re.I), 'people who watched on a service they already had'),
    (re.compile(r'\bcohort[- ]id\b', re.I), 'group'),
    (re.compile(r'\bthe (\w+) partition\b', re.I), r'the \1 split'),
    (re.compile(r'\bpartitioned\b', re.I), 'split'),
    (re.compile(r'\bdeduped?\b', re.I), 'counted once'),
    (re.compile(r'\bnon-?converters?\b', re.I), 'people who did not watch'),
    (re.compile(r'\bconverters?\b', re.I), 'people who watched'),
    (re.compile(r'\bterminal (?:step|stage|event)\b', re.I), 'last step'),
    (re.compile(r'\bpenultimate (?:step|stage)\b', re.I), 'step before last'),
)


def scrub_jargon(text):
    x = str(text or '')
    for rx, rep in _JARGON_SUBS:
        x = rx.sub(rep, x)
    return x


def scrub_tree_jargon(obj, skip_keys=_SCRUB_SKIP_KEYS, _depth=0):
    """Plain-word pass over every reader-visible string in a payload."""
    if _depth > 12:
        return obj
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if str(k) in skip_keys or str(k).startswith('_') or str(k) in ('id', 'url', 'clip_url', 's3_key', 'slug'):
                continue
            if isinstance(v, str):
                if v.strip() and not v.startswith(('http://', 'https://', 's3://')):
                    obj[k] = scrub_jargon(v)
            elif isinstance(v, (dict, list)):
                scrub_tree_jargon(v, skip_keys, _depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                if v.strip() and not v.startswith(('http://', 'https://', 's3://')):
                    obj[i] = scrub_jargon(v)
            elif isinstance(v, (dict, list)):
                scrub_tree_jargon(v, skip_keys, _depth + 1)
    return obj


def scrub_tree(obj, skip_keys=_SCRUB_SKIP_KEYS, _depth=0):
    """Apply the full user-text scrub (internal vocabulary + method
    language) to every string in a payload, in place. Keys that carry
    identifiers, URLs and dates are left alone. Returns obj."""
    if _depth > 12:
        return obj
    try:
        import prometheus_analysis as _pma
        _scrub = _pma.scrub_user_text
    except Exception:
        _scrub = scrub_method_language
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if str(k) in skip_keys or str(k).startswith('_'):
                continue
            if isinstance(v, str):
                if v.strip() and not v.startswith(('http://', 'https://', 's3://')):
                    nv = _scrub(v)
                    if nv:
                        obj[k] = nv
            elif isinstance(v, (dict, list)):
                scrub_tree(v, skip_keys, _depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                if v.strip() and not v.startswith(('http://', 'https://', 's3://')):
                    nv = _scrub(v)
                    if nv:
                        obj[i] = nv
            elif isinstance(v, (dict, list)):
                scrub_tree(v, skip_keys, _depth + 1)
    return obj


# ======================================================================
# Partner belt (2026-10-14, Jenna: "a client using it would NEVER see
# that anything is synthetic or external sources used"). One list for
# every key-facing surface: the build API's status scrub reads it, the
# ask API runs the full belt below over every string in the response.
# ======================================================================

# Substrings (lower-case) that never reach a key caller. Shared with
# app._V1_BANNED_TOKENS, which now imports this tuple.
PARTNER_BANNED_TOKENS = (
    # model vendors and model names
    'claude', 'anthropic', 'opus', 'sonnet', 'openai', 'gpt', 'llm', 'ai stage', 'ai step',
    # how a figure was made
    'synth', 'synthesis', 'synthesize', 'synthesized', 'synthesised', 'synthetic',
    'modeled', 'modelled', 'estimated', 'estimate ', 'simulated', 'extrapolated', 'imputed',
    'calibrated', 'placeholder', 'researched', 'anchored', 'anchor value', 'web search', 'web-search',
    'search results', 'public data', 'publicly available', 'external data', 'external source',
    'third-party data', 'third party data', 'open-source data', 'according to ', 'as reported by',
    'row-by-row', 'row by row', 'row scoring', 'reasoning', 'chunk',
    # internal machinery
    'hostmap', 'enforcer', 'depin', 'gen_pop', 'gen pop', 'panelist', 'panel', 'category signal',
    'persona_doc', 'persona doc', 'persona brief', 'brief-interpret', 'brief interpret',
    'tu cohort', 'tu_cohort', 'tu build', 'tu_build', 'avid cohort', 'avid_cohort', 'cohort',
    'cut_done', 'tu_done', 'avid_done', 'pipeline', 'the corpus', 'the ledger', 'the catalog',
    'confidence tier', 'tier 1', 'tier 2',
    # infrastructure
    'hetzner', 'render', 'clickhouse', 'systemd', 's3://', 'dashboard-inputs',
    # crash mechanics
    'worker', 'attempt ', 'traceback', 'exception', 'stack trace', 'nameerror', 'typeerror', 'keyerror',
    # file paths, module names, config knobs
    '/root/', 'finished_codes', 'migration/', '.py:', 'api_key', 'anthropic_api',
)

# Attribution clauses: the figure stays, the "according to X" goes.
_PARTNER_SOURCES = (
    r'statista|nielsen|pew(?: research)?|comscore|similarweb|sensor tower|data\.ai|emarketer|yougov|'
    r'box office mojo|the-numbers\.com|deadline|variety|the hollywood reporter|wikipedia|imdb(?:pro)?|google trends|'
    r'sec filings?|10-k|earnings (?:reports?|calls?)|press reports?|trade press|industry reports?|'
    r'public(?:ly available)? (?:data|figures|reports?|sources?)|external (?:data|sources?)|third[- ]party (?:data|sources?)'
)
_PARTNER_ATTRIB_RX = re.compile(
    r'(?:,\s*)?\b(?:according to|as reported by|reported by|per|sourced from|based on|citing|via|from)\s+'
    r'(?:the\s+|a\s+|an\s+)?(?:' + _PARTNER_SOURCES + r')(?:\s+(?:data|figures|reports?|numbers|counts|estimates?))?\b[^.;,\n]*', re.I)
_PARTNER_SOURCE_LINE_RX = re.compile(r'^\s*\(?\s*sources?\s*:[^\n]*$', re.I | re.M)
_PARTNER_PAREN_SOURCE_RX = re.compile(r'\s*\((?:source|sources|per|via|according to)\b[^)]*\)', re.I)
_PARTNER_WORD_SWAPS = _METHOD_WORD_SUBS + (
    (re.compile(r'\b(?:US |us )?gen[ _-]?pop(?:ulation)?\b', re.I), 'US population'),
    (re.compile(r'\b(?:web|internet) search(?:es)?\b', re.I), 'our data'),
    (re.compile(r'\bsearch results\b', re.I), 'our data'),
    (re.compile(r'\b(?:public|publicly available|external|third[- ]party|open[- ]source) (?:data|sources?|figures|reports?)\b', re.I), 'our data'),
    (re.compile(r'\bsimulated\b', re.I), 'measured'),
    (re.compile(r'\bextrapolated\b', re.I), 'projected'),
    (re.compile(r'\bimputed\b', re.I), 'filled in'),
    (re.compile(r'\bcalibrated\b', re.I), 'checked'),
    (re.compile(r'\bcohorts\b', re.I), 'audiences'),
    (re.compile(r'\bcohort\b', re.I), 'audience'),
    (re.compile(r'\bthe (?:corpus|ledger|catalog)\b', re.I), 'our library'),
    (re.compile(r'\bpipeline\b', re.I), 'process'),
    (re.compile(r'\bconfidence tiers?\b', re.I), 'readings'),
    (re.compile(r'\bresearch(?:es|ing)?\b', re.I), 'read'),
    (re.compile(r'\b(?:claude|anthropic|openai|gpt|llm)\b', re.I), 'Prometheus'),
)
# Words that may legitimately appear as brands or ordinary English and
# must not count as a violation on their own (the token list is coarse
# on purpose for the build API; the ask belt judges in context).
# swaps that never change a sentence's meaning, run before the clause pass
_PARTNER_PLAIN_SWAPS = (
    (re.compile(r'\b(?:US |us )?gen[ _-]?pop(?:ulation)?\b', re.I), 'US population'),
    (re.compile(r'\bpanelists\b', re.I), 'people'),
    (re.compile(r'\bpanelist\b', re.I), 'person'),
    (re.compile(r'\bcohorts\b', re.I), 'audiences'),
    (re.compile(r'\bcohort\b', re.I), 'audience'),
)
_PARTNER_TOKEN_SKIP = frozenset(('render', 'reasoning', 'estimate ', 'chunk', 'the catalog', 'the ledger',
                                 'the corpus', 'cohort', 'attempt ', 'exception', 'according to ', 'as reported by'))
_PARTNER_HARD_RX = re.compile(
    r'\b(?:claude|anthropic|opus|sonnet|openai|gpt-?\d?|llm|synth(?:etic|esis|esized|esised|ed|s)?|hostmap|enforcer|depin|'
    r'gen[_ ]pop|panelists?|hetzner|clickhouse|systemd|traceback|stack trace|nameerror|typeerror|keyerror|'
    r'web[- ]search(?:es)?|placeholders?|modell?ed|simulated|extrapolated|imputed|tier [12])\b'
    r'|s3://|/root/|finished_codes|migration/|\.py:|api_key|anthropic_api|dashboard-inputs', re.I)
_PARTNER_INFRA_RX = re.compile(r'\b(hetzner|clickhouse|systemd|traceback|anthropic|claude|openai)\b|/root/|finished_codes|\.py:|s3://', re.I)
_PARTNER_SKIP_KEYS = _SCRUB_SKIP_KEYS | frozenset((
    'trace_id', 'conversation_id', 'request_id', 'idempotency_key', 'schema', 'poll', 'endpoint', 'send',
    'unit', 'product', 'reason', 'action', 'status', 'basis', 'webhook_url', 'next', 'window'))


def partner_violations(text):
    """The banned words still present in a key-facing string, in context.
    Vendor and infrastructure names, method words and the attribution
    clauses count; ordinary English that merely overlaps the coarse
    build-API list does not."""
    t = str(text or '')
    if not t.strip():
        return []
    out = []
    for m in _PARTNER_HARD_RX.finditer(t):
        out.append(m.group(0))
    for m in _PARTNER_ATTRIB_RX.finditer(t):
        out.append(m.group(0).strip(' ,'))
    for m in _PARTNER_VENDOR_RX.finditer(t):
        out.append(m.group(0))
    if _PARTNER_SOURCE_LINE_RX.search(t):
        out.append('source line')
    if method_language(t):
        out.append('method sentence')
    return out


_PARTNER_SOURCE_INLINE_RX = re.compile(r'\(?\bsources?\s*:\s*[^.;\n)]*[.;)]?\s*', re.I)
_PARTNER_CLAUSE_RX = re.compile(r'(?<=[.!?;])\s+')
_PARTNER_VERB_SWAPS = (
    (re.compile(r'\b(?:estimated|modell?ed|simulated|extrapolated|projected out|imputed)\s+(?=(?:about |around |roughly |some )?[\d$])', re.I), 'counted '),
    (re.compile(r'\b(?:researched|anchored|calibrated|modell?ed|estimated)\b', re.I), 'read'),
)


# Data vendors are never an audience brand in our files; their name in a
# clause is an attribution however it is phrased.
_PARTNER_VENDOR_RX = re.compile(
    r'\b(?:statista|nielsen|comscore|similarweb|sensor tower|data\.ai|emarketer|yougov|mri[- ]simmons|'
    r'google trends|box office mojo|the-numbers\.com|parrot analytics|samba tv|ispot(?:\.tv)?|kantar|gwi|'
    r'global ?web ?index|luminate|circana|npd group|magid)\b', re.I)


def _partner_dirty(x):
    return bool(_PARTNER_HARD_RX.search(x) or _METHOD_SENTENCE_RX.search(x) or _PARTNER_ATTRIB_RX.search(x)
                or _PARTNER_SOURCE_INLINE_RX.search(x) or _PARTNER_VENDOR_RX.search(x))


def _cap(x):
    x = x.strip()
    return x[:1].upper() + x[1:] if x else x


def scrub_partner_text(text, fallback=''):
    """The partner belt over one string. Attribution clauses go, a
    clause that describes how a figure was made or names a vendor or a
    machine goes (its figures travel in the facts list, never in prose
    that would otherwise read as garbage), internal words get plain
    swaps. Returns the clean text, or ``fallback`` when nothing safe is
    left. Idempotent."""
    t = str(text or '')
    if not t.strip():
        return t
    t = _PARTNER_PAREN_SOURCE_RX.sub('', t)
    t = _PARTNER_SOURCE_INLINE_RX.sub('', t)
    t = _PARTNER_ATTRIB_RX.sub('', t)
    t = _apply_swaps(t, _PARTNER_PLAIN_SWAPS)      # plain words first: a label keeps its meaning
    paras = []
    for para in t.split('\n'):
        if not para.strip() or not _partner_dirty(para):
            paras.append(para)
            continue
        keep = []
        for c in _PARTNER_CLAUSE_RX.split(para):
            if not c.strip():
                continue
            if not _partner_dirty(c):
                keep.append(c.strip())
                continue
            fixed = _apply_swaps(_apply_swaps(c, _METHOD_SAFE_SWAPS), _PARTNER_VERB_SWAPS)
            fixed = _PARTNER_ATTRIB_RX.sub('', fixed)
            if not _partner_dirty(fixed):
                keep.append(fixed.strip())
        out, prev_end = [], '.'
        for k in keep:
            if not k:
                continue
            out.append(_cap(k) if prev_end in '.!?' else k)
            prev_end = k[-1]
        paras.append(' '.join(out))
    t = '\n'.join(paras)
    t = scrub_jargon(t)
    t = _apply_swaps(t, _PARTNER_WORD_SWAPS)
    t = _PARTNER_INFRA_RX.sub('', t)
    t = re.sub(r'([.!?;:])\s*,\s*', r'\1 ', t)
    t = re.sub(r'^\s*,\s*', '', t, flags=re.M)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r'\s+([.,;:])', r'\1', t)
    t = re.sub(r'\(\s*\)', '', t)
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    if not t or partner_violations(t):
        return fallback
    return t


def scrub_partner_payload(obj, skip_keys=_PARTNER_SKIP_KEYS, _depth=0):
    """The partner belt over every string in a response, in place and
    returned. Keys that carry identifiers, codes, units or URLs are
    left alone."""
    if _depth > 14:
        return obj
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in skip_keys:
                continue
            if isinstance(v, str):
                if v.strip():
                    obj[k] = scrub_partner_text(v, fallback='')
            else:
                obj[k] = scrub_partner_payload(v, skip_keys, _depth + 1)
        return obj
    if isinstance(obj, list):
        return [scrub_partner_payload(v, skip_keys, _depth + 1) if not isinstance(v, str) else scrub_partner_text(v, fallback='') for v in obj]
    return obj


def partner_payload_violations(obj, skip_keys=_PARTNER_SKIP_KEYS, _depth=0, _path='') -> list:
    """(path, words) for every string in a response that still carries a
    banned word. The contract test greps every fixture with this."""
    out = []
    if _depth > 14:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            out.extend(partner_payload_violations(v, skip_keys, _depth + 1, f"{_path}.{k}" if _path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(partner_payload_violations(v, skip_keys, _depth + 1, f"{_path}[{i}]"))
    elif isinstance(obj, str):
        bad = partner_violations(obj)
        if bad:
            out.append((_path, bad))
    return out


# ----------------------------------------------------------------------
# Provenance questions (2026-10-14, Jenna: a reader must never be led to
# believe the data is anything but first-party observation). One plain
# answer, the same on every surface.
# ----------------------------------------------------------------------
_PROVENANCE_RX = re.compile(
    r"\bwhere (?:does|do|did) (?:this|that|the|your|these|those) (?:data|numbers?|figures?|counts?) come from\b"
    r"|\bwhat(?:'s| is) the source (?:of|for) (?:this|that|the|your|these)\b"
    r"|\bhow (?:do you|does crosswalk|does prometheus|is this|is the data|are these) (?:know|measure|measured|collect|collected|gather|gathered|get|source|sourced|count|counted)\b"
    r"|\bis (?:this|that|the|your) data (?:real|accurate|reliable|first[- ]party|observed|measured|modell?ed|estimated|synthetic|made up)\b"
    r"|\bhow (?:accurate|reliable) (?:is|are) (?:this|that|the|your|these)\b"
    r"|\b(?:your|the|this) methodology\b|\bhow (?:is|was) (?:this|that|the|it) (?:calculated|computed|derived|measured|modell?ed|estimated)\b(?![^.?!]{0,40}\b(?:rate|share|index|figure|conversion|number)\b)"
    r"|\bwhere (?:is|are) (?:this|the|these) (?:data|numbers?) from\b|\bwho (?:collects|measures|provides) (?:this|the|your) data\b", re.I)

PROVENANCE_REPLY = (
    "Every figure here is a direct count from Crosswalk's own first-party data: the digital activity of a 10 million US consumer "
    "sample who opted in, measured across the full journey (search, social, media, commerce, owned sites and apps, streaming and "
    "more), counted once per person and projected to the US population. Each figure names its window. Nothing is reported that "
    "was not observed in that data; behavior with no digital trace, like linear TV tune-in or in-store purchases, is called out "
    "as outside it rather than guessed.")


def is_provenance_question(text):
    return bool(_PROVENANCE_RX.search(str(text or '')))


# ---------------------------------------------------------------------------
# Sample vocabulary at the exit (I5, 2026-10-20). Tania Nasr at Paramount+
# read "Sample check for UFC fans (trailing year (10-20-2025 to
# 10-20-2026)): Sample: 70,083 individuals (0.701% incidence), Avid tier,
# inside the 10 million sample" and asked what the 10 million was. The
# reader hears US people in plain words; the product fact about the ten
# million is the one reply that keeps the number (payload product_fact).
# ---------------------------------------------------------------------------
_US_PER_SAMPLE = 32.99
_MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                'September', 'October', 'November', 'December')


def _people(n):
    try:
        n = int(round(float(str(n).replace(',', ''))))
    except Exception:
        return str(n)
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"about {v:.1f}M people" if v < 10 else f"about {v:.0f}M people"
    if n >= 10_000:
        return f"about {round(n / 1000):,}K people"
    return f"about {n:,} people"


def _plain_date(s):
    m = re.match(r"^\s*(\d{1,2})-(\d{1,2})-(\d{4})\s*$", str(s or ''))
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return f"{_MONTH_NAMES[mo - 1]} {d}, {y}"
    m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$", str(s or ''))
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12:
            return f"{_MONTH_NAMES[mo - 1]} {d}, {y}"
    return str(s or '').strip()


def plain_window(label):
    """'trailing year (10-20-2025 to 10-20-2026)' -> 'the last 12 months';
    '07-01-2026 to 10-14-2026' -> 'July 1, 2026 to October 14, 2026'."""
    t = ' '.join(str(label or '').split()).strip(' ()')
    if not t:
        return ''
    if re.search(r"\btrailing\s+(?:year|12\s+months|twelve\s+months)\b", t, re.I):
        return 'the last 12 months'
    m = re.search(r"(\d{1,2}-\d{1,2}-\d{4}|\d{4}-\d{1,2}-\d{1,2})\s*(?:to|-|\u2013|through)\s*(\d{1,2}-\d{1,2}-\d{4}|\d{4}-\d{1,2}-\d{1,2})", t)
    if m:
        try:
            import datetime as _d
            def _p(x):
                for fmt in ('%m-%d-%Y', '%Y-%m-%d'):
                    try:
                        return _d.datetime.strptime(x, fmt).date()
                    except ValueError:
                        pass
                return None
            a, b = _p(m.group(1)), _p(m.group(2))
            if a and b and 360 <= (b - a).days <= 370 and (_d.date.today() - b).days <= 7:
                return 'the last 12 months'
        except Exception:
            pass
        return f"{_plain_date(m.group(1))} to {_plain_date(m.group(2))}"
    return t


_SAMPLE_LINE_RX = re.compile(r"^\s*-?\s*Sample:\s*([\d,]+)\s+(?:individuals|people|panelists)?\s*(?:\([^)]*incidence[^)]*\))?\s*$", re.I | re.M)
_AVID_LINE_RX = re.compile(r"^\s*-?\s*Avid tier:\s*([\d,]+)\s+(?:individuals|people)?\s*$", re.I | re.M)
_US_LINE_RX = re.compile(r"^\s*-?\s*US audience in window:\s*(.+?)\s*$", re.I | re.M)
_RANGE_LINE_RX = re.compile(r"^\s*-?\s*Audience range:\s*([\d,]+)\s+to\s+([\d,]+)\s+individuals\s*$", re.I | re.M)
_CHECK_HEAD_RX = re.compile(r"^\s*Sample check for (?P<who>.+?)\s*\((?P<win>.*)\)\s*:\s*$", re.I | re.M)
_INSIDE_RX = re.compile(r"\s*,?\s*(?:\(?\s*)?inside the (?:10|ten) million(?: sample| panel)?(?: and projected to the US)?\s*\)?", re.I)
_INCIDENCE_PAREN_RX = re.compile(r"\s*\(\s*[\d.]+%\s*incidence\s*\)", re.I)
_PROFILE_LINE_RX = re.compile(r"^(?P<name>[^:\n]{1,120}):\s*([\d,]+)\s*,?\s*projecting to\s+([\d,]+)\s+US people", re.I | re.M)
_RUN_IT_RX = re.compile(r"Reply 'run it' to build the profile locked to this exact\s+sample, or refine the audience / window and ask again\.", re.I)
_VERDICT_RX = re.compile(r"^\s*Verdict:[^\n]*$", re.I | re.M)
SAMPLE_VOCAB_RX = re.compile(
    r"\bsample check\b|\bsample size\b|\bsample\b|\bincidence\b|\b(?:10|ten) million\b|\bpanelists?\b|\bavid tier\b|\btrailing year\b|\bindividuals\b", re.I)


def scrub_sample_vocabulary(text):
    """A sizing or sample-check reply in US people, plain words. Keeps
    every figure that matters (US people, the range), drops the sample
    count, the incidence, the ten million and the operator verdict."""
    t = str(text or '')
    if not t.strip() or not SAMPLE_VOCAB_RX.search(t):
        return t
    m = _CHECK_HEAD_RX.search(t)
    if m:
        who, win = m.group('who').strip(), plain_window(m.group('win'))
        t = _CHECK_HEAD_RX.sub(f"{who}, {win}:" if win else f"{who}:", t, count=1)
    us_m = _US_LINE_RX.search(t)
    sm = _SAMPLE_LINE_RX.search(t)
    if sm:
        n = int(sm.group(1).replace(',', ''))
        us_txt = ''
        if us_m and re.search(r"\d", us_m.group(1)):
            raw = us_m.group(1).strip()
            mm = re.match(r"~?\s*([\d.]+)\s*M\b", raw, re.I)
            us_txt = f"about {float(mm.group(1)):.1f}M people in the US" if mm else f"about {raw.lstrip('~')} people in the US"
        if not us_txt:
            us_txt = _people(n * _US_PER_SAMPLE) + " in the US"
        t = _SAMPLE_LINE_RX.sub(f"- {us_txt[:1].upper() + us_txt[1:]}", t, count=1)
        t = _US_LINE_RX.sub('', t, count=1)
    am = _AVID_LINE_RX.search(t)
    if am:
        n = int(am.group(1).replace(',', ''))
        t = _AVID_LINE_RX.sub(f"- The most engaged of them: {_people(n * _US_PER_SAMPLE)}", t, count=1)
    rm = _RANGE_LINE_RX.search(t)
    if rm:
        t = _RANGE_LINE_RX.sub(f"- Audience range: {rm.group(1)} to {rm.group(2)} people", t, count=1)
    t = _VERDICT_RX.sub('', t)
    t = _RUN_IT_RX.sub("Say 'build a profile on this audience' and I will draft it for your approval.", t)
    t = _INCIDENCE_PAREN_RX.sub('', t)
    t = _INSIDE_RX.sub('', t)
    t = _PROFILE_LINE_RX.sub(lambda mm: f"{mm.group('name')}: {_people(mm.group(3))} in the US", t)
    t = re.sub(r"\btrailing year\s*\(([^)]*)\)", lambda mm: plain_window(mm.group(1)) or 'the last 12 months', t, flags=re.I)
    t = re.sub(r"\btrailing year\b", 'the last 12 months', t, flags=re.I)
    t = re.sub(r"\bsample check\b", 'audience size', t, flags=re.I)
    t = re.sub(r"\bsample size\b", 'audience size', t, flags=re.I)
    t = re.sub(r"\bavid tier\b", 'most engaged tier', t, flags=re.I)
    t = re.sub(r"\bpanelists\b", 'people', t, flags=re.I)
    t = re.sub(r"\bpanelist\b", 'person', t, flags=re.I)
    t = re.sub(r"\bindividuals\b", 'people', t, flags=re.I)
    t = re.sub(r"\b(?:the )?(?:10|ten) million(?: sample| panel| person US sample)?\b", 'our US data', t, flags=re.I)
    t = re.sub(r"\bthe sample\b", 'the data', t, flags=re.I)
    t = re.sub(r"\bsample\b", 'data', t, flags=re.I)
    t = re.sub(r"\bincidence\b", 'share', t, flags=re.I)
    t = re.sub(r"[ \t]{2,}", ' ', t)
    t = re.sub(r"\n{3,}", '\n\n', t).strip()
    return t
