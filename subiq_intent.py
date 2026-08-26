"""Subscriber IQ intent detection for the dashboard chatbot (2026-08-26).

Jenna: "think through all the ways someone could ask you to pull a
subscriber iq like 'first watch' 'subscriber aqcuisiont' that kinda
thing and make sure that's fully wired." Note her own typo - misspelling
tolerance is part of the mandate.

This module is the deterministic net UNDER the interpret step: when a
request plainly reads as a Subscriber IQ pull but the interpret step
routed it elsewhere, app.py promotes the decision to subscriber_iq (see
_promote_subiq_intent). It also backs the client-side mirror
_pmLooksSubscriberIq in templates/index.html, which keeps Subscriber IQ
asks out of the page-aware analysis flow when a profile is open.

WHAT SUBSCRIBER IQ ACTUALLY PRODUCES (SVOD_Churn_Attribution.py):
signup / acquisition attribution for a title on a platform (with
per-episode splits), first-watch conversion (new subscribers whose
first watch was the title), signup timing, post-signup touchpoints,
monthly signups, monthly churn / cancellations, reactivations vs truly
new subscribers, competitive platforms, and age / gender demographics.
The phrasing families below map onto those real capabilities.

FAMILY -> ROUTE DECISIONS
  product      route: names the product ('subscriber iq', 'sub iq',
               'subiq', signup/subscriber/acquisition tracker).
               Overrides every veto.
  acquisition  route: signup / subscriber acquisition + attribution
               asks ('what drove signups', 'did Landman bring
               subscribers', 'subs gained', 'who signed up because of
               Y', 'how many people joined Peacock for X').
  first_watch  route: 'first watch', 'first title watched', 'what did
               new subs watch first', 'entry title', 'front-door
               title'.
  churn        route when tied to a title event: 'cancellations after
               the finale', 'did people leave when X ended',
               'win-back', 'reactivations', 'dormant subscribers
               coming back'. A bare 'churn' / 'retention' with no
               event context does NOT route deterministically - the
               interpret step decides (a churned-audience PROFILE ask
               is a Profile IQ build with universe_mode='churned').
  impact       route: 'The Pitt season 2 subscriber impact', 'signups
               during the finale week', 'subscriber lift'.

NEGATIVE LIST (never hijacked; vetoes below):
  * profile-build asks: 'build a profile of Nike shoppers', 'audience
    of The Pitt fans', 'profile of Paramount+ subscribers'
  * cohort definitions: 'people who signed up for Netflix', 'viewers
    who churned', 'churned from Hulu', switcher builds
  * demographic asks: 'subscriber demographics', 'demographics of ...'
  * search-demand flow: 'how are people finding X', 'search demand',
    'first touch', 'where to watch searches'
  * incidence / count questions: 'how many panelists', 'how many
    subscribers does Netflix have', 'subscriber count'
  * avid/casual fan cut vocabulary

Typo tolerance: token-level canonicalization using a letter-multiset
distance (catches transpositions for free plus 1-2 dropped/added
letters) against the key vocabulary: subscriber(s), subscribe(d),
subscription(s), acquisition(s), attribution, reactivation(s),
cancellation(s), signup(s). 'aqcuisiont' -> 'acquisition',
'subsciber' -> 'subscriber', 'acquisiton' -> 'acquisition'.

Zero third-party imports so tests and the interpret path load it
instantly.
"""
import re
from collections import Counter

# Canonical key vocabulary the fuzzy pass snaps typos onto. Order
# matters only for tie-breaks (first minimal-diff match wins).
_CANON_VOCAB = (
    'subscriber', 'subscribers', 'subscribed', 'subscribes', 'subscribe',
    'subscription', 'subscriptions',
    'acquisition', 'acquisitions',
    'attribution',
    'reactivation', 'reactivations', 'reactivated',
    'cancellation', 'cancellations',
    'signup', 'signups',
)
_CANON_SET = frozenset(_CANON_VOCAB)
# Tokens shorter than this never fuzz (protects 'subs', 'sub', 'sign').
_FUZZ_MIN_LEN = 5
# Maximum letter-multiset difference to accept a canonicalization.
_FUZZ_MAX_DIFF = 2


def _letter_diff(a, b):
    """Letter-multiset difference between two tokens. Transpositions
    cost 0 (same letters), each dropped/added/substituted letter costs
    1-2. Cheap and order-free, which is exactly the typo shape real
    people produce ('aqcuisiont', 'subsciber')."""
    ca, cb = Counter(a), Counter(b)
    return sum((ca - cb).values()) + sum((cb - ca).values())


def _fuzzy_canon_token(tok):
    """Snap a single token onto the canonical vocabulary when it is a
    1-2 letter mangle of one of them; otherwise return it unchanged."""
    if len(tok) < _FUZZ_MIN_LEN or tok in _CANON_SET:
        return tok
    best, best_diff = None, _FUZZ_MAX_DIFF + 1
    for canon in _CANON_VOCAB:
        if canon[0] != tok[0]:
            continue
        if abs(len(canon) - len(tok)) > 2:
            continue
        # The acquisition family is gated on 'q' so unrelated a-words
        # can never snap onto it.
        if 'q' in canon and 'q' not in tok:
            continue
        diff = _letter_diff(tok, canon)
        if diff < best_diff:
            best, best_diff = canon, diff
    return best if best is not None and best_diff <= _FUZZ_MAX_DIFF else tok


def normalize_subiq_text(text):
    """Lowercase, strip apostrophes, collapse punctuation/whitespace,
    join 'sign up(s)' / 'win back' bigrams, snap typos onto the key
    vocabulary. The family patterns run against this canonical form."""
    t = str(text or '').lower()
    if not t.strip():
        return ''
    t = re.sub(r"[\u2018\u2019\u02bc'`]", '', t)
    t = re.sub(r'[^a-z0-9+]+', ' ', t)
    toks = [_fuzzy_canon_token(tok) for tok in t.split()]
    t = ' ' + ' '.join(toks) + ' '
    t = t.replace(' sign ups ', ' signups ').replace(' sign up ', ' signup ')
    t = t.replace(' win back ', ' winback ').replace(' win backs ', ' winbacks ')
    return t.strip()


# ---------------------------------------------------------------------------
# Family patterns. All run against normalize_subiq_text output: lowercase,
# single-spaced, apostrophe-free, typo-canonicalized.
# ---------------------------------------------------------------------------

# Product-name asks. These override every veto: 'pull a subscriber iq
# for The Pitt' must route even though 'pull' also appears in
# profile-build vocabulary.
_PRODUCT_PATTERNS = [
    r'\bsub(?:scri\w*)?s? ?iq\b',               # subscriber iq / sub iq / subiq / subs iq / typo forms
    r'\b(?:signups?|subscri\w*|acquisitions?|subs?) trackers?\b',
]

_ACQUISITION_PATTERNS = [
    r'\b(?:subscri\w*|signups?|subs?) acquisitions?\b',
    r'\bacquisitions? (?:drivers?|analysis|report|read|numbers|story|breakdown|attribution|funnel|split|deck)\b',
    r'\bacquisitions? (?:for|on)\b',
    r'\b(?:signups?|subscri\w*|subs) attribution\b',
    r'\battribution (?:of|for|on) (?:signups?|subscri\w*|subs)\b',
    r'\b(?:drove|drive|drives|driving|driven|brought|bring|brings|bringing)\b[^.?!]{0,40}\b(?:signups?|subscri\w*|subs)\b',
    r'\b(?:signups?|subscri\w*|subs)\b[^.?!]{0,30}\b(?:driven by|attributed to|because of|thanks to|due to)\b',
    r'\b(?:subscri\w*|subs|signups?) (?:gained|added|acquired|won)\b',
    r'\bnew (?:subscri\w*|subs|signups?) (?:from|off|because|after|thanks|due)\b',
    r'\b(?:who|how many(?: people| viewers| users| folks)?) (?:signed up|subscribed|joined)\b',
    r'\bsign(?:ed|s)? up (?:because|after|for|off|from|thanks|due)\b',
    r'\bsubscribed (?:because|after|for|off|from|thanks|due)\b',
    r'\bjoin(?:ed|s|ing)?\b[^.?!]{0,25}\b(?:because of|to watch|for)\b',
    r'\b\w+ driven signups?\b',
    r'\bsignups? (?:for|from|off)\b',
    r'\bconversion to (?:subscri\w*|subs|signups?)\b',
]

_FIRST_WATCH_PATTERNS = [
    r'\bfirst watch\w*\b',
    r'\bfirst (?:title|show|series|movie|thing|content) (?:watched|streamed|played|viewed)\b',
    r'\bwatch(?:ed)? first\b',
    r'\bfirst stream\w*\b',
    r'\bentry title\b',
    r'\bfront door title\b',
    r'\bgateway (?:title|show|series)\b',
]

_CHURN_PATTERNS = [
    r'\bcancell?ations? (?:after|when|following|during|spike)\b',
    r'\bchurn\w* (?:after|when|following|during)\b',
    r'\b(?:did|do|will|are) (?:people|subscri\w*|subs|viewers|users|anyone|they) (?:leave|leaving|cancel|churn|drop|unsubscribe)\b',
    r'\b(?:leave|left|cancell?ed|churned) (?:when|after|once)\b',
    r'\bwin ?backs?\b',
    r'\breactivat\w*\b',
    r'\bdormant (?:subscri\w*|subs|accounts?|users)\b',
    r'\b(?:subscri\w*|subs|accounts?) (?:coming|came|come) back\b',
    r'\bretention (?:after|through|during|impact)\b',
    r'\b(?:subscri\w*|subs) (?:lost|churned)\b',
]

_IMPACT_PATTERNS = [
    r'\b(?:subscri\w*|signups?) impact\b',
    r'\bsubscri\w* (?:lift|bump|effect)\b',
    r'\bsignups? (?:during|over|around) (?:the )?\w+',
    r'\b(?:finale|premiere|launch) (?:week )?signups?\b',
    r'\bimpact on (?:signups?|subscri\w*|subs)\b',
]

# Vetoes: asks that belong to OTHER flows. Checked after the product
# family (a literal product-name ask always wins) and before every
# other family.
_VETO_PATTERNS = [
    # Profile-build verb + deliverable noun ('build a profile of ...').
    r'\b(?:build|make|create|pull|run|generate|queue|derive|spin up)\b[^.?!]{0,50}\b(?:profile|audience|universe|persona|cut)s?\b',
    # Universe / cohort definitions ('audience of X fans').
    r'\b(?:profile|audience|universe|cohort|persona)s? (?:of|for|on)\b',
    r'\bpeople who\b',
    r'\b(?:viewers|users|subscri\w*|fans|members|customers|households|adults|shoppers|buyers) who\b',
    # Demographic-first asks may be Profile IQ pulls.
    r'\b(?:subscri\w*|audience|viewers?) demographics?\b',
    r'\bdemographics? (?:of|for|breakdown)\b',
    r'\bdemo breakdown\b',
    # Search-journey demand flow vocabulary.
    r'\bsearch demand\b',
    r'\bsearch journey\b',
    r'\bfirst touch\w*\b',
    r'\bsearch to play\b',
    r'\bwhere to watch\b',
    r'\bhow (?:are|were|do|did|is|was) (?:people|viewers|users|searchers|audiences?|everyone|subscribers) (?:find|finding|discover|discovering)\b',
    # Switcher / churned-universe profile builds.
    r'\bswitchers?\b',
    r'\bchurned (?:from|off|out of|away from)\b',
    # Incidence / count questions.
    r'\bhow many (?:panelists?|panel|sample)\b',
    r'\bhow many (?:subscri\w*|subs|members) (?:does|do|did)\b',
    r'\bsubscri\w* counts?\b',
    # Profile cut vocabulary.
    r'\b(?:avid|casual) fans?\b',
]

_PRODUCT_COMPILED = [re.compile(p) for p in _PRODUCT_PATTERNS]
_VETO_COMPILED = [re.compile(p) for p in _VETO_PATTERNS]
_FAMILY_COMPILED = [
    ('acquisition', [re.compile(p) for p in _ACQUISITION_PATTERNS]),
    ('first_watch', [re.compile(p) for p in _FIRST_WATCH_PATTERNS]),
    ('churn', [re.compile(p) for p in _CHURN_PATTERNS]),
    ('impact', [re.compile(p) for p in _IMPACT_PATTERNS]),
]


def subiq_intent_family(text):
    """Return the matched phrasing family name ('product',
    'acquisition', 'first_watch', 'churn', 'impact') when the text
    reads as a Subscriber IQ pull, else None. Conservative by design:
    profile builds, cohort definitions, demographic asks, search-demand
    questions, and count questions never match (see _VETO_PATTERNS)."""
    t = normalize_subiq_text(text)
    if not t:
        return None
    for rx in _PRODUCT_COMPILED:
        if rx.search(t):
            return 'product'
    for rx in _VETO_COMPILED:
        if rx.search(t):
            return None
    for family, rxs in _FAMILY_COMPILED:
        for rx in rxs:
            if rx.search(t):
                return family
    return None


def detect_subscriber_iq_intent(text):
    """True when the message plainly asks for a Subscriber IQ pull."""
    return subiq_intent_family(text) is not None


# Literals the client-side mirror (_pmLooksSubscriberIq in
# templates/index.html) must contain. scripts/test_subiq_intent_coverage.py
# enforces the sync: every token below has to appear inside the mirror
# function's source so the two detectors cannot silently drift.
CLIENT_MIRROR_TOKENS = (
    '_pmSubIqCanonToken',
    '_pmSubIqLetterDiff',
    'subscriber',
    'subscription',
    'acquisition',
    'attribution',
    'reactivat',
    'cancellation',
    'signup',
    'first watch',
    'entry title',
    'front door title',
    'winback',
    'dormant',
    'tracker',
    'gained',
    'joined',
    'demographics',
    'search demand',
    'first touch',
    'switcher',
    'people who',
    'avid',
)
