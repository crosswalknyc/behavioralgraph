"""KPI definitions and the thread number bank (2026-10-02, S4).

Two classes of ask used to cost a model call and still came back
loose: "how is this calculated?" and "why is this different from
before?". Both are lookups.

- DEFINITIONS is the house glossary: every KPI a dashboard view
  shows, in plain words, with how it is computed. `find_definition`
  resolves the KPI an ask names (aliases, the open view, and the
  labels on screen all count); `definition_reply` is the finished
  answer. No model call.
- `stated_numbers` reads every figure Prometheus already stated in
  this thread (the agent turns in the history the widget sends) with
  the sentence it sat in. `numbers_block` hands them to the reconcile
  prompt as binding context, so a "why is this smaller" answer names
  the earlier figure exactly instead of re-deriving it.

Copy rules: plain English, no internal vocabulary, no em dashes,
Accounts never households.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------
# views: '*' means every view; otherwise a tuple of view ids the KPI
# belongs to. A view match only breaks ties; a KPI named outright wins
# on any view.
DEFINITIONS = [
    {
        'id': 'penetration',
        'label': 'Penetration',
        'views': '*',
        'aliases': ('penetration', 'brand penetration', 'pen', 'pen.',
                    'audience penetration', 'share of the audience',
                    'share of this audience', 'percent of the audience',
                    'percent of this audience'),
        'definition': (
            "Penetration is the share of this audience that was active "
            "with the brand in the window. 21.8% penetration means about "
            "1 in 5 people in the audience showed that brand somewhere in "
            "their digital life over the trailing 12 months."),
        'computed': (
            "People in the audience with at least one qualifying touch "
            "with the brand, divided by everyone in the audience, over "
            "the window printed on the file."),
    },
    {
        'id': 'index',
        'label': 'Index',
        'views': '*',
        'aliases': ('index', 'idx', 'the index', 'index vs gen pop',
                    'index against gen pop', 'over-index', 'over index',
                    'overindex', 'under-index', 'indexes'),
        'definition': (
            "The index compares this audience to the US general "
            "population. 100 is average. 213 means the behavior is 2.1x "
            "as common in this audience as in the country as a whole; 50 "
            "means half as common."),
        'computed': (
            "This audience's penetration for the brand, divided by the US "
            "general population's penetration for the same brand, times "
            "100."),
    },
    {
        'id': 'projection',
        'label': 'Projected US audience',
        'views': '*',
        'aliases': ('projection', 'projected', 'projected us audience',
                    'us projection', 'us audience', 'gen pop projection',
                    'projected audience', 'projected people',
                    'projected viewers', 'projected users',
                    'us-projected', 'national number'),
        'definition': (
            "The projected US audience is how many people across the "
            "country this reads as, at the individual level. It turns "
            "the share we measure into a national count."),
        'computed': (
            "The audience's share of the country, applied to 329.9M US "
            "people, gives the audience's projected size. A brand's "
            "projected count is that size times the brand's penetration: "
            "10% penetration in an audience that projects to 3.31M people "
            "reads as about 331K people nationally."),
    },
    {
        'id': 'sample_size',
        'label': 'Audience size',
        'views': '*',
        'aliases': ('sample size', 'sample', 'audience size',
                    'how many people are in this audience',
                    'size of the audience', 'total universe size',
                    'base size', 'n size', 'the base'),
        'definition': (
            "The audience size is the count of people in the file: "
            "everyone who qualified into this audience in the window. "
            "Every share on the page is a share of this number."),
        'computed': (
            "Each person counts once, at the individual level. A person "
            "qualifies with at least one touch with the subject in the "
            "trailing 12 months unless the file names a different "
            "window."),
    },
    {
        'id': 'category_share',
        'label': 'Share of category',
        'views': '*',
        'aliases': ('category share', 'share of category',
                    'share within the category', 'share of the category',
                    'category split'),
        'definition': (
            "Share of category is each brand's slice of all the activity "
            "this audience showed in that category. The slices in one "
            "category add up to 100."),
        'computed': (
            "The brand's penetration divided by the sum of every brand's "
            "penetration in the same category on this file."),
    },
    {
        'id': 'avid_fan',
        'label': 'Avid Fan',
        'views': '*',
        'aliases': ('avid fan', 'avid fans', 'avid', 'avid share',
                    'avid cut', 'what counts as avid', 'casual fan',
                    'casual fans'),
        'definition': (
            "Avid Fans are the most engaged slice of the audience: the "
            "people who come back to the subject again and again across "
            "their digital life, not a one-touch visitor. Every Avid Fan "
            "is also in the full audience; the full audience is the "
            "casual read."),
        'computed': (
            "A subject-specific share of the full audience, read from "
            "how often and how many ways people engaged in the window. "
            "The Avid Fan file carries the same brands, re-read for that "
            "slice, sized at that share of the full audience."),
    },
    {
        'id': 'engager',
        'label': 'Engager',
        'views': '*',
        'aliases': ('engager', 'engagers', 'total universe',
                    'who counts as an engager', 'what is an engager',
                    'what counts as engagement', 'qualifying touchpoint',
                    'touchpoint'),
        'definition': (
            "An Engager is anyone with at least one touch with the "
            "subject in the window, anywhere in their digital life: a "
            "search, a visit, a stream, a purchase, a post. The Total "
            "Universe is every Engager."),
        'computed': (
            "One person, one count. A person qualifies on their first "
            "touch and is not counted again for later ones."),
    },
    {
        'id': 'subiq_accounts_viewed',
        'label': 'Total Accounts Viewed',
        'views': ('subscriberIQ',),
        'aliases': ('total accounts viewed', 'accounts viewed',
                    'total show watchers', 'show watchers',
                    'accounts that watched', 'watchers'),
        'definition': (
            "Total Accounts Viewed is the number of accounts on the "
            "platform that watched the title in the window, at the "
            "individual account level."),
        'computed': (
            "Accounts with at least one viewing session on the title in "
            "the window, each counted once, then projected to the US."),
    },
    {
        'id': 'subiq_attributed_signups',
        'label': 'Attributed Signups',
        'views': ('subscriberIQ',),
        'aliases': ('attributed signups', 'attributed sign-ups',
                    'attributed', 'net-new subscribers',
                    'net new subscribers', 'new accounts acquisition',
                    'new signups', 'signups attributed', 'acquisition'),
        'definition': (
            "Attributed Signups are brand-new platform accounts whose "
            "first viewing after signing up was this title. The title is "
            "what they came for."),
        'computed': (
            "Accounts created in the window with no prior history on the "
            "platform, whose first play lands on the title within the "
            "attribution window after signup."),
    },
    {
        'id': 'subiq_reactivated',
        'label': 'Reactivated Accounts',
        'views': ('subscriberIQ',),
        'aliases': ('reactivated accounts', 'reactivations', 'reactivated',
                    'dormant to reactive', 'reactivation',
                    'came back', 'win-backs', 'winbacks'),
        'definition': (
            "Reactivated Accounts are accounts that had gone quiet on "
            "the platform and came back, with this title as the first "
            "thing they watched on return."),
        'computed': (
            "Accounts with no platform activity for the dormancy window "
            "before the return, whose first play on return is the title."),
    },
    {
        'id': 'subiq_acquired_rate',
        'label': 'Acquired and Reactivated Rate',
        'views': ('subscriberIQ',),
        'aliases': ('acquired and reactivated rate', 'acquisition rate',
                    'reactivation rate', 'nps rate',
                    'new platform signups rate', 'acquired rate'),
        'definition': (
            "The Acquired and Reactivated Rate is the share of everyone "
            "who watched the title that the title brought to the "
            "platform, either as a new account or as a returning one."),
        'computed': (
            "Attributed Signups plus Reactivated Accounts, divided by "
            "Total Accounts Viewed."),
    },
    {
        'id': 'subiq_churn',
        'label': 'Churn',
        'views': ('subscriberIQ', 'journeyIQ', 'flywheelConversion'),
        'aliases': ('churn', 'churned', 'cancels', 'cancellations',
                    'monthly churn', 'churn rate', 'retention',
                    'stayed subscribed', 'kept the subscription'),
        'definition': (
            "Churn is the share of the accounts the title brought in "
            "that stopped using the platform afterward. Retention is the "
            "share that stayed."),
        'computed': (
            "Accounts attributed to the title with no platform activity "
            "for a full month after their last session, divided by all "
            "accounts attributed to the title, by month."),
    },
    {
        'id': 'trend_reads',
        'label': 'Trend read',
        'views': ('trendsIQ',),
        'aliases': ('trend score', 'trend reads', 'trending',
                    'search interest', 'momentum', 'daily reads',
                    'what makes something trending', 'velocity',
                    'rank change'),
        'definition': (
            "A trend read ranks what the country is searching, reading, "
            "streaming, playing, and buying each day, nationally and by "
            "market. Rank is where it sits today; the change is against "
            "yesterday."),
        'computed': (
            "Daily counts of people engaging with each item across their "
            "digital life, ranked within the tab, with the day-over-day "
            "difference shown as the change."),
    },
    {
        'id': 'journey_steps',
        'label': 'Journey steps',
        'views': ('journeyIQ',),
        'aliases': ('journey steps', 'journey', 'the path', 'conversion step',
                    'step conversion', 'drop-off', 'drop off', 'dropoff',
                    'paywall bounce', 'bounce'),
        'definition': (
            "A journey is the ordered set of things people did on the "
            "way to the outcome, one person at a time. Each step shows "
            "how many people reached it and how many moved on; the gap "
            "between steps is the drop-off."),
        'computed': (
            "Each person's sessions in the window, ordered in time, "
            "matched against the steps of the journey; a person counts "
            "at a step the first time they reach it."),
    },
    {
        'id': 'attribution_lift',
        'label': 'Attributed lift',
        'views': ('attributionIQ', 'impactIQ', 'roasIQ', 'sfConversion'),
        'aliases': ('attributed lift', 'lift', 'incremental', 'incremental users',
                    'incrementality', 'attributed conversions',
                    'exposed vs control', 'control group', 'roas'),
        'definition': (
            "Lift is the extra outcome among people who were exposed, "
            "compared with people who were not. Incremental users are "
            "the exposed people who converted and would not have "
            "otherwise."),
        'computed': (
            "Conversion rate of the exposed group minus the conversion "
            "rate of the matched unexposed group, applied to the exposed "
            "group's size, then projected to the US."),
    },
    {
        'id': 'brand_value',
        'label': 'Brand value',
        'views': ('brandPartnershipIQ',),
        'aliases': ('brand value', 'total brand value', 'emv', 'earned media value',
                    'bev', 'blv', 'brand exposure value', 'brand lift value',
                    'conversion value', 'partnership value',
                    'value attributable'),
        'definition': (
            "Brand value is what the partnership was worth to the brand "
            "in dollars, read four ways: the media it earned, the "
            "exposure it created, the lift in brand engagement, and the "
            "conversions it drove."),
        'computed': (
            "Each lens prices a measured count: incremental people "
            "reached or converted, times the market rate for that "
            "outcome in the brand's category. The total is the sum of "
            "the lenses, printed exact."),
    },
    {
        'id': 'share_of_time',
        'label': 'Share of time',
        'views': ('shareOfTimeIQ',),
        'aliases': ('share of time', 'time share', 'hours share',
                    'share of hours', 'share of streaming time'),
        'definition': (
            "Share of time is the slice of this audience's streaming "
            "hours that went to each platform or title in the window."),
        'computed': (
            "Hours spent on the platform or title, divided by all "
            "streaming hours the audience logged in the window."),
    },
]

_VIEW_FALLBACK_LABEL = {
    'subscriberIQ': 'Subscriber IQ',
    'trendsIQ': 'Trends IQ',
    'journeyIQ': 'Digital Journey IQ',
    'brandPartnershipIQ': 'Brand Partnership IQ',
    'attributionIQ': 'Attribution IQ',
}

# ---------------------------------------------------------------------------
# Ask shapes
# ---------------------------------------------------------------------------
_DEFINITION_ASK_RES = (
    re.compile(r"\bhow\s+(?:is|are|was|were|do\s+you|does\s+\w+)\s+.{0,60}?"
               r"\b(?:calculated|derived|measured|computed|determined|"
               r"defined|counted|arrived\s+at|figured)\b", re.I),
    re.compile(r"\bwhat\s+(?:does|do)\s+.{0,50}?\b(?:mean|measure|count)\b",
               re.I),
    re.compile(r"\b(?:define|definition\s+of|what\s+counts\s+as|"
               r"what\s+qualifies\s+as|how\s+do\s+you\s+define)\b", re.I),
    re.compile(r"\bwhat\s+(?:goes|went)\s+into\b", re.I),
)

_RECONCILE_ASK_RES = (
    re.compile(r"\bwhy\s+(?:is|was|does|did|are|were)\b.{0,80}?"
               r"\b(?:higher|lower|bigger|smaller|larger|different|"
               r"not\s+the\s+same)\b", re.I),
    re.compile(r"\b(?:doesn'?t|does\s+not|didn'?t|did\s+not|don'?t)\s+"
               r"(?:match|line\s+up|square|agree|add\s+up|reconcile)\b",
               re.I),
    re.compile(r"\b(?:earlier|before|last\s+time|previously|yesterday|"
               r"the\s+other\s+day)\b.{0,60}?\b(?:said|told|gave|showed|"
               r"was|read|had)\b", re.I),
    re.compile(r"\b(?:you|it)\s+(?:said|told\s+me|gave\s+me|showed)\b"
               r".{0,60}?\b(?:now|but)\b", re.I),
    re.compile(r"\bseems?\s+(?:too\s+|very\s+|really\s+|way\s+too\s+)?"
               r"(?:high|low|off|wrong|inflated|small|big|large)\b", re.I),
    re.compile(r"\bcan'?t\s+be\s+right\b|\bdouble[- ]check\b|"
               r"\bwhich\s+(?:one\s+)?is\s+(?:right|correct)\b", re.I),
)


_WHAT_IS_RE = re.compile(r"^\s*what(?:'s|\s+is|\s+are)\s+(.+?)\s*\??\s*$", re.I)
_WHAT_IS_FILLER = {'what', 'is', 'are', 'an', 'a', 'the', 'here', 'this',
                   'that', 'on', 'page', 'screen', 'mean', 'means', 'does',
                   'do', 'exactly', 'your', 'you', 'by', 'in', 'of', 'it',
                   'number', 'figure', 'metric', 'column', 'tile', 'kpi',
                   'again', 'please', 'actually', 'crosswalk', 'prometheus'}


def _bare_what_is(text):
    """'what is penetration?' style: the ask is a what-is whose
    remainder, minus a glossary alias and filler, is empty. 'what is
    the index for nike' keeps 'nike' and is a data ask, not this."""
    m = _WHAT_IS_RE.match(str(text or ''))
    if not m:
        return False
    nt = _norm(m.group(1))
    for d in DEFINITIONS:
        for alias in d['aliases']:
            if _alias_in(alias, nt):
                rest = re.sub(r'(?<![a-z0-9])' + re.escape(_norm(alias))
                              + r'(?![a-z0-9])', ' ', nt)
                left = [w for w in rest.split() if w not in _WHAT_IS_FILLER]
                if not left:
                    return True
    return False


def is_definition_ask(text):
    """A short, single definition question. A message carrying several
    questions, or a long one, is an analysis ask even when one clause
    says "define" (2026-10-06, Emmet: "identify brand partnerships that
    index the highest ... Can you define who my key audience is? Is my
    audience non-tech" drew the Index glossary entry)."""
    t = str(text or '').strip()
    if not t or len(t) > 240:
        return False
    if t.count('?') > 1 or len(t.split()) > 24:
        return False
    if any(rx.search(t) for rx in _DEFINITION_ASK_RES):
        return True
    return _bare_what_is(t)


def is_reconcile_ask(text):
    t = str(text or '').strip()
    if not t or len(t) > 400:
        return False
    return any(rx.search(t) for rx in _RECONCILE_ASK_RES)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _norm(s):
    return re.sub(r'[^a-z0-9%]+', ' ', str(s or '').lower()).strip()


def _alias_in(alias, nt):
    a = _norm(alias)
    if not a:
        return False
    return re.search(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', nt) \
        is not None


def find_definition(text, view_id='', on_screen_labels=None):
    """The glossary entry the ask names, or None.

    The longest alias present in the ask wins; a tie goes to the entry
    whose views include the open view. When the ask names nothing but
    points at the screen ("how is this calculated?") and exactly one
    on-screen label resolves to an entry, that entry wins."""
    nt = _norm(text)
    view_id = str(view_id or '')
    # the alias must sit in the same sentence as the definition cue
    # ("define X", "what does X mean"), never elsewhere in the message
    cue_sent = None
    for sent in re.split(r'(?<=[.!?])\s+', str(text or '')):
        if any(rx.search(sent) for rx in _DEFINITION_ASK_RES) or _bare_what_is(sent):
            cue_sent = _norm(sent)
            break
    if cue_sent is not None:
        nt = cue_sent
    best = None
    for d in DEFINITIONS:
        for alias in d['aliases']:
            if _alias_in(alias, nt):
                score = (len(_norm(alias)),
                         1 if (d['views'] == '*' or view_id in d['views'])
                         else 0)
                if best is None or score > best[0]:
                    best = (score, d)
    if best:
        return best[1]
    # A quoted phrase the glossary does not know is a label on the page
    # (a journey row, a tile): the generic entry never answers for it
    # (2026-10-06, Alexia's "Saw a retarget").
    if re.search(r"[\"\u201c\u201d']([^\"\u201c\u201d']{3,80})[\"\u201c\u201d']", str(text or '')):
        return None
    # No silent fallback to whatever is on the screen (2026-10-06,
    # Jenna: strip the dashboard assumptions; always ask to confirm).
    # A deictic ask ("how is this calculated?") goes to
    # which_figure_options, which asks with the on-screen labels as
    # chips instead of guessing one.
    return None


_DEICTIC_RX = re.compile(r'\b(?:this|that|it|these|those)\b', re.I)


def which_figure_options(text, on_screen_labels, limit=5):
    """The on-screen labels a deictic definition / reconcile ask could
    mean, for a which-figure confirm. [] when the ask names a glossary
    term already, is not deictic, or the screen offers nothing."""
    t = str(text or '')
    if not _DEICTIC_RX.search(t) or not on_screen_labels:
        return []
    if find_definition(t) is not None:
        return []
    out, seen = [], set()
    for lab in on_screen_labels:
        ls = str(lab or '').strip()
        nl = _norm(ls)
        if not nl or nl in seen or len(ls) > 60 or len(nl) < 3:
            continue
        if nl in {'label', 'name', 'title', 'id', 'rows', 'data', 'items', 'value', 'values'}:
            continue
        seen.add(nl)
        out.append(ls)
        if len(out) >= limit:
            break
    return out


def definition_reply(defn, view_id=''):
    """The finished plain-English answer for one glossary entry."""
    if not defn:
        return ''
    lines = [f"{defn['label']}: {defn['definition']}",
             '',
             f"How it is computed: {defn['computed']}"]
    return '\n'.join(lines)


def view_definitions_block(view_id, on_screen_labels=None, limit=8):
    """The glossary entries relevant to the open view (and any KPI the
    screen labels name), rendered for a prompt. '' when none."""
    view_id = str(view_id or '')
    picked = []
    labels_norm = [_norm(l) for l in (on_screen_labels or []) if _norm(l)]
    for d in DEFINITIONS:
        in_view = d['views'] != '*' and view_id in d['views']
        named = any(any(_alias_in(a, nl) for a in d['aliases'])
                    for nl in labels_norm)
        if in_view or named:
            picked.append(d)
    if not picked:
        picked = [d for d in DEFINITIONS if d['views'] == '*'][:limit]
    picked = picked[:limit]
    if not picked:
        return ''
    out = ["KPI DEFINITIONS FOR THIS VIEW (house glossary, use these "
           "words when a reader asks what a number means)"]
    for d in picked:
        out.append(f"- {d['label']}: {d['definition']} "
                   f"Computed: {d['computed']}")
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Thread number bank
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(
    r"(?<![\w.])(?:\$\s?)?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:\s?[KMB]\b)?%?"
    r"|(?<![\w.])(?:\$\s?)?\d+(?:\.\d+)?\s?(?:[KMB]\b|%|x\b|pp\b|pts?\b)"
    r"|(?<![\w.])(?:\$\s?)\d+(?:\.\d+)?"
    r"|(?<![\w.])\d+(?:\.\d+)?\s?(?:million|billion|thousand)\b"
    r"|(?<![\w.,])\d{4,}(?![\w.,])")
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?])\s+|\n+')
_SKIP_SENT_RE = re.compile(r"\b(?:credit|credits|\$\d+(?:,\d{3})*\s*(?:per|each|/))\b",
                           re.I)
_YEAR_ONLY_RE = re.compile(r'^(?:19|20)\d{2}$')


def stated_numbers(history, max_turns=12, max_items=40):
    """Every figure Prometheus stated in this thread, newest last:
    [{'turn': i, 'value': '412,387', 'sentence': '...'}]. Pricing
    lines and bare years are skipped; a sentence yields each figure it
    carries once."""
    turns = [h for h in (history or []) if isinstance(h, dict)]
    out = []
    agent_turns = [(i, h) for i, h in enumerate(turns)
                   if str(h.get('role') or '').lower() in ('agent', 'assistant')]
    for i, h in agent_turns[-max_turns:]:
        txt = str(h.get('text') or h.get('content') or '')
        if not txt:
            continue
        for sent in _SENT_SPLIT_RE.split(txt):
            s = sent.strip()
            if not s or len(s) > 400 or _SKIP_SENT_RE.search(s):
                continue
            seen = set()
            for m in _NUM_RE.finditer(s):
                v = m.group(0).strip()
                if _YEAR_ONLY_RE.match(v) or v in seen:
                    continue
                seen.add(v)
                out.append({'turn': i, 'value': v, 'sentence': s})
                if len(out) >= max_items:
                    return out
    return out


def numbers_block(history, max_chars=2400):
    """Prompt block of the figures already stated in this thread, each
    with its sentence, oldest first. '' when the thread has none."""
    items = stated_numbers(history)
    if not items:
        return ''
    lines = ["NUMBERS STATED EARLIER IN THIS THREAD (binding: restate "
             "these exactly; when the reader asks why a figure differs, "
             "name the earlier figure and what differs in window, cohort, "
             "or definition; never re-derive it)"]
    seen_sent = set()
    for it in items:
        key = (it['turn'], it['sentence'])
        if key in seen_sent:
            continue
        seen_sent.add(key)
        vals = sorted({x['value'] for x in items if (x['turn'], x['sentence']) == key},
                      key=len, reverse=True)
        lines.append(f"- {', '.join(vals)}: {it['sentence']}")
    block = '\n'.join(lines)
    if len(block) > max_chars:
        block = block[:max_chars].rsplit('\n', 1)[0]
    return block


def on_screen_labels(view_data, limit=40):
    """Flat list of KPI-like labels found in a view-context data dict
    (keys and any 'label' / 'lbl' / 'name' values), for resolution."""
    out = []

    def walk(v, depth):
        if depth > 4 or len(out) >= limit:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                ks = str(k)
                if ks in ('label', 'lbl', 'name', 'title') and isinstance(x, str):
                    out.append(x)
                elif isinstance(x, (dict, list)):
                    out.append(ks.replace('_', ' '))
                    walk(x, depth + 1)
                else:
                    out.append(ks.replace('_', ' '))
        elif isinstance(v, list):
            for x in v[:12]:
                walk(x, depth + 1)
    walk(view_data, 0)
    return out[:limit]


def which_figure_payload(text, on_screen_labels):
    """The which-figure confirm as an analyze payload, or None. Never
    raises (a failure means no confirm, and the ask continues)."""
    try:
        if not (is_definition_ask(text) or is_reconcile_ask(text)):
            return None
        opts = which_figure_options(text, on_screen_labels)
        if not opts:
            return None
        return {'success': True, 'action': 'answer',
                'reply': 'Which figure do you mean? Pick one and I will define '
                         'it and show how it is counted.',
                'followups': [f'Define "{o}"' for o in opts],
                'offer_deck': False, 'deck_angle': None}
    except Exception:
        return None
