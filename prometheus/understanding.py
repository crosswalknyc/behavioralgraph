"""One typed Understanding of an ask, made once at the start of
service.ask (Prometheus rebuild stage 2, Jenna 2026-11-20: "all sound
good. let's update and put into action asap").

Until now the question "what is this person asking" was decided in five
places, four of them pattern matching, and the first lane whose words
matched had the first and last word. The Understanding is the one
reading every lane executes from:

    features()        the lane predicates run once as features (each is
                      a real case), the scope is read off the words (own
                      order / feature / people / page), and a small
                      deterministic reconciler settles the intent where
                      two features fire; the hard rules (an armed step,
                      an explicit order, no_build, a cancel) are never
                      left to a model
    absorb_reading()  the Reader's reading, when it runs, refines the
                      intent, the subjects and the window; a hard rule
                      still wins
    claims()          a lane keyed on the Understanding runs only when
                      the Understanding agrees; where it has taken no
                      position the lane's own predicate decides, as before
    stamp_log()       the Understanding rides the ask log beside the
                      route the lane walk chose (the shadow grade)

Shadow first: every ask carries an Understanding on the body
(``body['_pm_understanding']``) and in the ask log (``extra['u']``), and
nothing is keyed on it until an intent is flipped. A flipped intent has
a kill switch in the environment and nowhere else (never a request
field, per no-external-overrides): ``PM_UNDERSTANDING=0`` turns the step
off, ``PM_U_OFF_<INTENT>=1`` turns one flipped intent back to the lane
walk.

The two things that stay as they are until Jenna decides them: the
brief card's "Estimated audience" wording (not this module's), and
whether a confident reading may override an armed Brand Partnership
wizard step (the step wins; the Understanding records ``armed_step`` and
defers).

Pure Python; every outside call is fail-safe. No model call of its own
(the Reader is the one model read, absorbed when it runs). No S3. The
only Flask touch is ``g`` for the ask log, inside a try.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field, asdict

from . import intent_classes as _ic

# The Reader's intents plus the ones the lanes carry today.
LANE_INTENTS = (
    'order',                    # an explicit order to build / run / pull a product
    'pushback',                 # no / not that / wrong, with nothing new asked
    'continuation_rerun',       # the same again, on a window
    'continuation_correction',  # a correction of the last turn that still asks for something
    'reader_will_share',        # something new the reader will take to a third party (an insight ask by another name)
    'study_question',           # a question about a published study
    'send_file_order',          # email me / send me / download the file we hold
    'wayfinding',               # where is a product, tab, control or the dropdown
    'insight_ask',              # an open-ended insight ask on a subject
    'brand_fit',                # which brands suit a talent's audience (a read on the talent, never a valuation order)
    'build_fix', 'build_facts', 'data_floor', 'cancel', 'armed', 'multi_ask', 'provenance', 'offer_reply',
)
INTENTS = tuple(dict.fromkeys(tuple(getattr(__import__('prometheus.reader', fromlist=['INTENTS']), 'INTENTS', ())) + LANE_INTENTS))

# The flips: an executor keyed on the Understanding. Each has its kill switch PM_U_OFF_<NAME>=1.
# A name here is live; a name in SHADOW is computed and logged and keys nothing yet.
FLIPPED = ('wayfinding', 'status', 'send_file_order', 'insight_ask',
           'wizard_interrupt',      # an armed wizard step yields to a clearly unrelated ask and takes a plain correction (Jenna 2026-11-20)
           'valuation_referent',    # words after a delivered valuation that point at it (its deck, its dates, a re-run) are about it
           'brand_fit',             # which brands suit a talent is a read on the talent's audience, never a valuation (Jenna 2026-11-20)
           'named_subject',         # a subject the words name outranks the open page and the user's memory in the read (Emma Crouchen 2026-11-20)
           'capability',            # a question about the product itself is product knowledge, never a subject read (Liz 2026-11-20)
           'provenance',            # where a figure came from, two figures squared, the unprojected count, US or global (2026-11-20, night)
           'exists',                # is X in the library, what do we hold on X, the roster (the catalog lanes, 2026-11-20, night)
           'definition',            # what a term, a figure or a method means; the house sample; a brief's own terms (2026-11-20, night)
           'size',                  # how many people an audience holds, a demographic segment counted (the sizing lanes, 2026-11-20, night)
           'multi_ask')             # several questions in one message, each answered through the door in turn (2026-11-20, night)
SHADOW = ('named_title',)

# Intents the flipped lanes compete over: a positive reading of one of
# these (at STAND_DOWN_CONF or better) stands the others down. Anything
# else is "no position taken". Grows with each class moved onto the
# Understanding (2026-11-20, night).
_CONTESTED = frozenset(('wayfinding', 'status', 'send_file_order', 'insight_ask', 'dashboard_issue', 'capability', 'provenance',
                        'exists', 'definition', 'size', 'multi_ask'))
STAND_DOWN_CONF = 0.75  # a weak reading (a correction, the page, the generic status at 0.7) never stands a lane down
# intents a waiting wizard step yields to when the words are read with confidence (prometheus.wizard_interrupt)
WIZARD_YIELDS_TO = frozenset(('status', 'wayfinding', 'send_file_order', 'insight_ask', 'dashboard_issue', 'pricing', 'exists',
                              'definition', 'capability', 'study_question', 'provenance', 'brand_fit'))
WIZARD_YIELD_CONF = 0.8

ACTIONS = ('answer', 'build', 'deck', 'send', 'open', 'status', 'cancel', 'approve', '')


def enabled():
    return os.environ.get('PM_UNDERSTANDING', '1') != '0'


def flipped(name):
    """True when the executor for this intent is keyed on the Understanding."""
    n = str(name or '').strip().lower()
    return enabled() and n in FLIPPED and os.environ.get(f'PM_U_OFF_{n.upper()}', '0') != '1'


# -------------------------------------------------------------- the type
@dataclass
class Subject:
    as_written: str = ''
    catalog: str = ''
    kind: str = 'unknown'      # person | brand | title | audience | platform | unknown
    role: str = 'subject'      # subject | title | service | precedent | page


@dataclass
class Scope:
    own_order: bool = False    # about the reader's own run or delivery
    feature: bool = False      # about a product, tab or control of the dashboard
    people: bool = False       # about an audience, title, brand or behaviour
    page: bool = False         # points at what is on screen
    no_build: bool = False
    armed_step: str = ''


@dataclass
class Understanding:
    intent: str = 'analyze'
    subjects: list = field(default_factory=list)
    named_title: str = ''
    referent: str = ''         # page | last_read | thread_subject | ''
    window: str = ''
    platform: str = ''
    action: str = 'answer'
    scope: Scope = field(default_factory=Scope)
    confidence: float = 0.5
    source: str = 'features'   # features | reader | reconciled
    evidence: list = field(default_factory=list)
    flipped: bool = False
    text_key: str = ''         # a short hash of the words it was read from; a rewrite in a later lane makes it stale

    def to_dict(self):
        d = asdict(self)
        d['subjects'] = [asdict(s) if isinstance(s, Subject) else dict(s) for s in self.subjects]
        return d

    @classmethod
    def from_dict(cls, d):
        if isinstance(d, Understanding):
            return d
        if not isinstance(d, dict):
            return None
        subs = []
        for s in d.get('subjects') or []:
            if isinstance(s, dict):
                subs.append(Subject(**{k: s.get(k, getattr(Subject(), k)) for k in ('as_written', 'catalog', 'kind', 'role')}))
        sc = d.get('scope') if isinstance(d.get('scope'), dict) else {}
        scope = Scope(**{k: sc.get(k, getattr(Scope(), k)) for k in ('own_order', 'feature', 'people', 'page', 'no_build', 'armed_step')})
        return cls(intent=str(d.get('intent') or 'analyze'), subjects=subs, named_title=str(d.get('named_title') or ''),
                   referent=str(d.get('referent') or ''), window=str(d.get('window') or ''), platform=str(d.get('platform') or ''),
                   action=str(d.get('action') or 'answer'), scope=scope, confidence=float(d.get('confidence') or 0.5),
                   source=str(d.get('source') or 'features'), evidence=list(d.get('evidence') or []), flipped=bool(d.get('flipped')),
                   text_key=str(d.get('text_key') or ''))

    def compact(self):
        """One string for the ask log: intent, confidence, source, action, scope flags, the named title, the flip, the evidence."""
        flags = [k for k in ('own_order', 'feature', 'people', 'page', 'no_build') if getattr(self.scope, k)]
        if self.scope.armed_step:
            flags.append('armed:' + self.scope.armed_step[:16])
        parts = [f"intent={self.intent}", f"conf={self.confidence:.2f}", f"src={self.source}", f"act={self.action}",
                 "scope=" + ('|'.join(flags) if flags else '-')]
        if self.named_title:
            parts.append("title=" + re.sub(r"[;=]", ' ', self.named_title)[:40])
        parts.append(f"flip={1 if self.flipped else 0}")
        if self.evidence:
            parts.append("ev=" + ','.join(e[:18] for e in self.evidence[:8]))
        return ';'.join(parts)[:200]


def parse_compact(s):
    """The dict behind a compact string ('intent=status;conf=0.80;...')."""
    out = {}
    for part in str(s or '').split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    try:
        out['conf'] = float(out.get('conf') or 0)
    except ValueError:
        out['conf'] = 0.0
    out['scope'] = [x for x in str(out.get('scope') or '').split('|') if x and x != '-']
    out['ev'] = [x for x in str(out.get('ev') or '').split(',') if x]
    out['flip'] = str(out.get('flip') or '0') == '1'
    return out


def text_key(text):
    return hashlib.sha1(' '.join(str(text or '').lower().split()).encode('utf-8')).hexdigest()[:10]


# ------------------------------------------------------------ the words
_OWN_ORDER_RX = re.compile(
    r"\b(?:my\s+(?:\w+\s+){0,3}(?:profile|read|deck|file|report|results?|pull|run|build|cut|data|download|order|tracker|journey)|"
    r"i\s+(?:asked|ordered|requested|ran|queued|submitted|approved|paid)\b|we\s+(?:asked|ordered|requested|ran|queued|paid)\b|"
    r"(?:have|has|had)\s*(?:n'?t|\s+not)\s+(?:received|gotten|got|seen|arrived|landed|come|shown\s+up)|"
    r"(?:not|never)\s+(?:yet\s+)?(?:received|landed|arrived|delivered|come\s+through|shown\s+up)|still\s+(?:waiting|running|pending|not)|"
    r"is\s+(?:it|this|that|the\s+\w+(?:\s+\w+)?)\s+(?:done|ready|finished|complete|live)\b|where(?:'s|\s+is|\s+are)\s+my\b|when\s+will\s+(?:it|my|this|the)\b|"
    r"status\s+(?:of|on|update)|did\s+(?:it|my|the\s+\w+)\s+(?:run|finish|land|go\s+through|work)|that\s+i\s+(?:asked|ordered|requested)|"
    r"i\s+(?:don'?t|do\s+not|can'?t|cannot)\s+see\s+(?:it|my|the\s+\w+\s+i)|waiting\s+(?:on|for)\b|how\s+long\s+(?:until|till|before|will)|eta\b)", re.I)
# "if I ran a Profile IQ, what would my sample size be": a hypothetical, never the reader's own order
_HYPOTHETICAL_RX = re.compile(r"\b(?:if|when|should|suppose|supposing|say|imagine|assuming)\s+(?:i|we)\s+(?:ran|run|asked|ordered|requested|queued|paid|approved)\b", re.I)
_PAGE_RX = re.compile(r"\b(?:this|that|these|those|here|on\s+screen|the\s+open\s+(?:profile|page|read|file)|this\s+(?:audience|profile|page|read|file|group))\b", re.I)
_PEOPLE_RX = re.compile(
    r"\b(?:how\s+many|how\s+much|what\s+(?:share|percent|%)|who\s+(?:are|is|skews?|watch|buy|use)|audience|viewers?|fans?|customers?|users?|buyers?|shoppers?|"
    r"subscribers?|listeners?|players?|members?|households?|people|demograph|over[- ]?index|index(?:es)?|penetration|churn|retention|reach|overlap|"
    r"watched|streamed|bought|purchased|shop(?:ped|s)?|visited|engag)", re.I)
# "from the wicked tuna viewers, which brands ...": an audience named in lower case before the question (the binder
# reads capitalised names; the Reader has not run yet when the roster offer would answer)
_AUDIENCE_PHRASE_RX = re.compile(
    r"\b(?:from|of|among|for|in|within|across)\s+(?:the\s+)?[a-z0-9][a-z0-9'&.+-]*(?:\s+[a-z0-9][a-z0-9'&.+-]*){0,4}\s+"
    r"(?:viewers|fans|audience|listeners|subscribers|shoppers|buyers|users|customers|readers|players|members|watchers)\b", re.I)
_RERUN_RX = re.compile(r"\b(?:again|re-?run|rerun|same\s+(?:thing|read|one|question|analysis)|one\s+more\s+time|redo|repeat\s+(?:it|that|the))\b", re.I)
_CORRECTION_RX = re.compile(r"^\W*(?:no\b|not\s+(?:that|what|this)|wrong|that'?s\s+not|i\s+(?:said|meant|asked\s+for)|why\s+(?:do|did|are)\s+you\s+keep)", re.I)
_PUSHBACK_ONLY_RX = re.compile(r"^\W*(?:(?:no|nope|not\s+that|that'?s\s+not\s+(?:it|right|what\s+i\s+meant|what\s+i\s+asked)|wrong|"
                               r"i\s+didn'?t\s+ask\s+for\s+that)[\s,.!]*)+$", re.I)
_SEND_RX = re.compile(r"\b(?:e-?mail|send|forward|mail)\s+(?:me|us|this|that|it|the\s+\w+)\b|\bdownload\b", re.I)
_BUILD_VERB_RX = re.compile(r"\b(?:build|run|pull|create|make|set\s+up|start|queue|launch)\b", re.I)
_THANKS_RX = re.compile(r"^\W*(?:thanks?|thank\s+you|ty|cool|got\s+it|understood|noted|perfect|great|awesome|nice|ok(?:ay)?\s+thanks?)[.! ]*$", re.I)
_BARE_REPLY_RX = re.compile(r"^\W*(?:yes|yes\s+please|yep|yeah|sure|ok(?:ay)?|approve[d]?|go\s+ahead|do\s+it|run\s+it|proceed|confirm|accept|"
                            r"no|nope|not\s+now|no\s+thanks|cancel|skip|none|\d{1,2})\W*$", re.I)


def _armed_step(body):
    """The step the client is in the middle of, by its body key; '' when none."""
    try:
        extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
        for k in ('bpiq_wizard', 'bpiq_inputs', 'bpiq_confirm', 'jiq_inputs', 'jiq_confirm', 'fw_inputs', 'fw_confirm', 'aiq_inputs',
                  'aiq_confirm', 'twin_inputs', 'twin_confirm', 'panel_confirm', 'angle', 'confirm_open_screen', 'draft', 'step',
                  'locked_sample_tu', 'locked_sample_avid', 'bind_subject', 'bind_cohort', 'overall_ranks'):
            if extra.get(k) or body.get(k):
                return k
    except Exception:
        pass
    return ''


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _features(text, ctx, history, body):
    """(name -> value) for every lane predicate that fired; fail-safe per predicate."""
    out = {}

    def put(name, fn):
        v = _safe(fn, None)
        if v:
            out[name] = v

    def mod(name):
        return __import__(f'prometheus.{name}', fromlist=['x'])

    put('wayfinding', lambda: mod('wayfinding').wants(text))
    put('directions', lambda: mod('wayfinding').is_directions_ask(text))
    put('status_fast', lambda: mod('status_lane').wants_status_fast(text))
    put('status', lambda: mod('status_lane').wants_status(text))
    put('wait', lambda: mod('status_lane').is_wait_ask(text))
    put('deliverable', lambda: mod('deliverables').parse(text))
    put('insight', lambda: mod('insight_ask').wants(text))
    put('brand_fit', lambda: mod('intents').brand_fit_ask(text))
    put('reader_will_share', lambda: mod('insight_ask').reader_will_share(text))
    put('send_order', lambda: mod('insight_ask').is_send_order(text))
    put('send_file', lambda: mod('send_file').parse(text))
    put('dashboard_issue', lambda: mod('dashboard_behavior').is_dashboard_question(text))
    put('cancel', lambda: mod('intake_reset').is_cancel_text(text))
    put('order', lambda: mod('orders').is_order(text))
    put('order_product', lambda: mod('orders').product_of(text))
    put('named_titles', lambda: mod('named_title').named(text) if mod('named_title').enabled() else [])
    put('slot_title', lambda: mod('named_title').slot_title(text))
    put('points_back', lambda: mod('named_title').points_back(text))
    put('multi', lambda: mod('multi_ask').split(text))
    put('sample_size', lambda: mod('guards').is_sample_size_ask(text))
    put('capability', lambda: mod('can_do').answer(text) or mod('data_capability').answer(text))
    put('product_question', lambda: mod('product_facts').family_of(text))
    put('rerun_words', lambda: bool(_RERUN_RX.search(text)))
    put('correction_words', lambda: bool(_CORRECTION_RX.search(text)))
    put('pushback_only', lambda: bool(_PUSHBACK_ONLY_RX.match(text)))
    put('send_words', lambda: bool(_SEND_RX.search(text)))
    put('build_verb', lambda: bool(_BUILD_VERB_RX.search(text)))
    put('thanks', lambda: bool(_THANKS_RX.match(text)))
    put('bare_reply', lambda: bool(_BARE_REPLY_RX.match(text)) and len(text.split()) <= 3)
    put('offer_open', lambda: mod('offers').pending_in_history(history))
    put('pricing', lambda: _host_pricing(text))
    put('wizard', lambda: _host_wizard(text))
    put('wizard_start', lambda: mod('bpiq_wizard').is_start(text))
    # a Brand Partnership step is waiting on this thread and the message answers it (a question typed there is a
    # question, per the intake guard): the turn is the wizard's, as it is today
    put('wizard_open', lambda: mod('bpiq_wizard').last_agent_is_wizard(history) and not mod('intake_guard').is_question_shaped(text))
    # a brief that sizes the people around named titles is one read, whole (prometheus.read_brief); the keyword lanes stand aside for it
    put('brief', lambda: mod('read_brief').is_brief(text))
    put('provenance', lambda: _provenance_kind(text))
    # the catalog lanes: ('exists' | 'size' | 'roster', subject) when the words ask what the library holds
    put('catalog', lambda: (mod('catalog_lane').parse(text) or (None, None))[0])
    put('definition', lambda: _definition_kind(text, history))
    # the sizing lanes: a joint demographic segment counted, or how many people an audience holds
    put('sizing', lambda: 'segment' if mod('segment_sizing').is_segment_ask(text) else ('audience' if mod('sizing_lane').wants_sizing(text) else ''))
    return out


def _definition_kind(text, history=None):
    """Which definition lane the words are for, in the lane walk's own
    order: 'term' (a term or line on the thread's own brief), 'discussion'
    (talk the pull through before it runs; the brief is in the thread), 'meta' (a check on the last
    answer), 'drilldown' (a number on the open journey / a KPI), 'view'
    (what counts as a view), 'method' (how a figure is worked out),
    'sample' (how big the house sample is). '' when none."""
    def mod(name):
        return __import__(f'prometheus.{name}', fromlist=['x'])
    for kind, fn in (('term', lambda: mod('brief_terms').is_term_question(text) and bool(mod('brief_terms').brief_in_thread(history))),
                     ('discussion', lambda: mod('pull_discussion').wants_discussion(text)),
                     ('meta', lambda: mod('service').meta_check(text)),
                     ('drilldown', lambda: mod('drilldown').looks_like_drilldown(text) or mod('drilldown').looks_like_definition(text)),
                     ('view', lambda: mod('methodology_answers').is_view_definition_question(text)),
                     ('methodology', lambda: bool(mod('methodology_answers').answer(text))),   # one methodology question, or a numbered list of them
                     ('method', lambda: bool(mod('how_computed').answer(text))),   # the lane's own test: a method, a where, an about
                     ('sample', lambda: mod('guards').is_sample_size_ask(text))):
        if _safe(fn, False):
            return kind
    return ''


def _provenance_kind(text):
    """Which provenance lane the words are for, in the lane walk's own
    order: 'reconcile' (two or more figures that must agree), 'us_scope'
    (US or global), 'number' (where one figure came from), 'unprojected'
    (the count in the data behind a projected figure), 'question' (the
    general how-do-you-know). '' when none."""
    def mod(name):
        return __import__(f'prometheus.{name}', fromlist=['x'])
    for kind, fn in (('reconcile', lambda: mod('reconcile_lane').wants_reconcile(text)),
                     ('us_scope', lambda: mod('us_scope').wants_us_scope(text)),
                     ('number', lambda: bool(mod('number_provenance').detect(text))),
                     ('unprojected', lambda: mod('unprojected').wants_unprojected(text)),
                     ('question', lambda: mod('guards').is_provenance_question(text))):
        if _safe(fn, False):
            return kind
    return ''


_READ_INTENTS = frozenset(('analyze', 'analyze_page', 'insight_ask', 'brand_fit', 'compare', 'definition', 'multi_ask',
                           'continuation_rerun', 'continuation_correction', 'size', 'exists'))
_NAMED_KINDS = frozenset(('title', 'brand', 'person', 'audience'))


def _points_at_screen(text):
    """The words point at the open view itself (prometheus.screen_scope);
    the older, looser pattern when the rule is switched off."""
    try:
        from . import screen_scope as _ss
        if _ss.enabled():
            return _ss.points_at_screen(text)
    except Exception:
        pass
    return bool(_PAGE_RX.search(str(text or '')))


def _page_of(ctx):
    try:
        return str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def _words_subject(text, history, ctx, reading=None):
    """The subject the words name (prometheus.subject_rules.bind on the
    words, with the Reader's reading when there is one), as a Subject
    with role 'subject'; None when the words name nothing of their own or
    only point at the page or the thread."""
    try:
        from . import subject_rules as _sr
        s = _sr.bind(str(text or ''), history, _page_of(ctx), reading)
        if not (s and s.named and s.source == 'words' and s.kind in _NAMED_KINDS) or getattr(s, 'pointed', False):
            return None
        disp = str(s.display or s.name or '').strip()
        if not disp or _is_product_label(disp):
            return None
        return Subject(as_written=disp, kind=str(s.kind), role='subject')
    except Exception:
        return None


def _is_product_label(name):
    try:
        from . import dashboard_map as _dm
        return bool(_dm.is_product_label(name))
    except Exception:
        return False


def current():
    """The Understanding of the ask in flight (flask.g), or None."""
    try:
        from flask import g as _g
        return Understanding.from_dict(getattr(_g, '_pm_understanding', None))
    except Exception:
        return None


def named_subject(u, text='', page=''):
    """The subject the Understanding carries from the words that is not
    the open page's own (its family) and not a pointer: the one a read on
    these words is about. '' when the Understanding names none, or names
    the page. Never a product or a tab."""
    u = Understanding.from_dict(u) if not isinstance(u, Understanding) else u
    if u is None or not u.subjects:
        return ''
    try:
        from . import subject_rules as _sr
        pseudo = {'subjects': [{'as_written': s.as_written, 'catalog': s.catalog, 'kind': s.kind} for s in u.subjects
                               if isinstance(s, Subject) and (s.as_written or s.catalog)]}
        s = _sr.bind(str(text or ''), None, str(page or ''), pseudo)
        if not (s and s.named and s.source == 'words' and s.kind in _NAMED_KINDS) or getattr(s, 'pointed', False):
            return ''
        disp = str(s.display or s.name or '').strip()
        if not disp or _is_product_label(disp):
            return ''
        if page and _sr._same_family(disp, page, lambda x: re.sub(r"[^a-z0-9]+", '', str(x or '').lower())):
            return ''
        return disp
    except Exception:
        return ''


def read_subject_hint(text='', page=''):
    """The subject the ask in flight names, for the read's own subject
    resolution (the hint it starts from, the page it may not default to):
    the Understanding's named subject when the flip is live, else ''."""
    if not flipped('named_subject'):
        return ''
    u = current()
    if u is None or u.scope.armed_step or u.intent in ('armed', 'cancel'):
        return ''
    if u.text_key and text and text_key(' '.join(str(text).split())) != u.text_key:
        return ''   # the Understanding is of other words (a lane rewrote the text): no position on these
    return named_subject(u, text, page)


def _host_pricing(text):
    try:
        from .host import host as _h
        return bool(_h.pricing_question(text)) if _h.has('pricing_question') else False
    except Exception:
        return False


_WIZARD_INTENT = {'bpiq_intent': 'valuation', 'jiq_intent': 'journey', 'fw_intent': 'campaign', 'aiq_intent': 'campaign', 'twin_intent': 'twin'}


def _host_wizard(text):
    """The product wizard the host's own intent checks name for these words, or ''."""
    try:
        from .host import host as _h
        for name, intent in _WIZARD_INTENT.items():
            if _h.has(name):
                try:
                    if getattr(_h, name)(text):
                        return intent
                except Exception:
                    continue
    except Exception:
        pass
    return ''


def _carried_subject(history, ctx):
    """The subject the thread or the page is carrying, for the named-title check."""
    try:
        from . import named_title as _nt
        for t in reversed(list(history or [])):
            if isinstance(t, dict) and str(t.get('role') or '') != 'user':
                head = _nt.read_head(str(t.get('text') or ''))
                if head:
                    return head
    except Exception:
        pass
    try:
        return str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def features(text, ctx=None, history=None, body=None, *, is_order=False, no_build=False):
    """The Understanding from the words and the lane predicates alone (no
    model). Never raises; a failure returns the default analyze reading."""
    body = body if isinstance(body, dict) else {}
    t = ' '.join(str(text or '').split())
    u = Understanding(text_key=text_key(t))
    if not t:
        return u
    try:
        f = _features(t, ctx, history, body)
        ev = []
        sc = Scope()
        sc.no_build = bool(no_build)
        sc.armed_step = _armed_step(body)
        wiz_open = bool(f.get('wizard_open')) and not f.get('cancel') and not sc.armed_step
        sc.own_order = bool(_OWN_ORDER_RX.search(t)) and not _HYPOTHETICAL_RX.search(t)
        sc.feature = bool(f.get('directions')) or (bool(f.get('wayfinding')) and not sc.own_order)
        sc.page = bool(_points_at_screen(t)) and bool(ctx)
        sc.people = bool(_PEOPLE_RX.search(t))
        u.scope = sc
        # the title the words name, when it is not the one carried
        titles = list(f.get('named_titles') or [])
        if titles and not f.get('points_back'):
            carried = _carried_subject(history, ctx)
            try:
                from . import named_title as _nt
                other = _nt.other_than(t, carried, history=history) if carried else titles[0]
            except Exception:
                other = titles[0]
            if other:
                u.named_title = str(other)
                ev.append('named_title')
        for name in ('wayfinding', 'directions', 'status_fast', 'status', 'wait', 'deliverable', 'insight', 'reader_will_share',
                     'send_order', 'send_file', 'dashboard_issue', 'cancel', 'order', 'multi', 'sample_size', 'capability',
                     'rerun_words', 'correction_words', 'pushback_only', 'send_words', 'build_verb', 'thanks', 'bare_reply',
                     'offer_open', 'pricing', 'wizard', 'wizard_start', 'wizard_open', 'brief', 'provenance', 'catalog', 'definition', 'sizing'):
            if f.get(name):
                ev.append(name if name not in ('wizard', 'provenance', 'catalog', 'definition', 'sizing') else name + ':' + str(f[name]))
        # a delivered valuation in the thread is what the words point at (prometheus.valuation_referent): its deck,
        # its figures, a re-run on corrected dates. Read on the reader's own words when the order gate rewrote them.
        vref = None
        try:
            from . import valuation_referent as _vr
            if _vr.enabled():
                vref = _vr.read(str(body.get('_pm_order_words') or '') or t, history)
        except Exception:
            vref = None
        if vref:
            ev.append('valuation_ref:' + str(vref.get('kind') or ''))
        # "which brands do you cover" asks for the roster we hold, not which brands suit an audience: a roster ask that
        # names no audience of its own is the catalog's (2026-11-20, night)
        roster_bare = bool(f.get('catalog') == 'roster' and f.get('brand_fit') and _words_subject(t, history, ctx) is None
                           and not _AUDIENCE_PHRASE_RX.search(t))
        u.evidence = ev
        # ---- the reconciler: hard rules first, then the contested pairs, then the rest
        intent, action, conf = 'analyze', 'answer', 0.6
        if sc.armed_step:
            intent, action, conf = 'armed', '', 0.9              # the step wins (Jenna's call pending)
        elif f.get('cancel'):
            intent, action, conf = 'cancel', 'cancel', 0.9
        elif vref:
            # the thread's delivered valuation: a deck of it, the valuation again (the same or corrected dates), or one
            # of its figures pasted back (a definition of that figure on that valuation, 2026-11-20)
            intent, action, conf = (('deck', 'deck', 0.85) if vref.get('kind') == 'deck'
                                    else ('definition', 'answer', 0.85) if vref.get('kind') == 'figure'
                                    else ('valuation', 'build', 0.85))
            u.referent = 'last_valuation'
            u.subjects = [Subject(as_written=str(vref.get('subject') or ''), kind='audience', role='subject')]
            if vref.get('window'):
                u.window = f"{vref['window'][0]} to {vref['window'][1]}"
        elif is_order or f.get('order'):
            intent, action, conf = 'order', 'build', 0.85
            prod = str(f.get('order_product') or '')
            if prod:
                ev.append('product:' + prod[:12])
            if prod == 'deck':
                intent, action = 'deck', 'deck'
            elif prod in ('journey', 'valuation', 'campaign', 'twin'):
                intent = prod
        elif f.get('product_question') and not f.get('wizard_start'):
            # a question about Prometheus or Crosswalk itself (what it is, how it differs from an AI tool, a tagline, what it
            # can do, what a product costs): product knowledge, never a subject read (Liz 2026-11-20 drew "Which subject
            # should this be on?" on "a 3-5 word description for CW prometheus versus AI")
            intent, action, conf = 'capability', 'answer', 0.9
            ev.append('product:' + str(f['product_question'])[:16])
        elif f.get('brand_fit') and flipped('brand_fit') and not f.get('wizard_start') and not f.get('insight') and not roster_bare:
            # which brands suit a talent, with pitch angles: a read on the talent's audience; it names no pairing to
            # value, so it is never the valuation's and an open order yields to it (Jenna 2026-11-20, her own seat)
            intent, action, conf = 'brand_fit', 'answer', 0.85
        elif f.get('wizard') or f.get('wizard_start'):
            intent, action, conf = (str(f['wizard']) if f.get('wizard') else 'valuation'), 'build', 0.8
        elif f.get('insight') or (f.get('reader_will_share') and not f.get('send_order')):
            intent, action, conf = 'insight_ask', 'answer', 0.85   # the Lego class: an insight to share, never a send
        elif f.get('send_order') or (f.get('send_file') and not f.get('insight')):
            if f.get('deliverable') and not f.get('send_file'):
                # "send me that deck": a thing we produced, looked up and handed over (the deliverable lane sits ahead
                # of the send lane in the walk and the send lane's own parse does not take it)
                intent, action, conf = 'status', 'status', 0.75
            else:
                intent, action, conf = 'send_file_order', 'send', 0.85
        elif f.get('dashboard_issue') and not sc.own_order:
            intent, action, conf = 'dashboard_issue', 'answer', 0.8
        elif (f.get('wayfinding') or f.get('directions')) and not (sc.own_order or f.get('wait') or (f.get('deliverable') and not f.get('directions'))):
            # where-is on a feature is directions (the wayfinding lane sits ahead of everything below in the walk)
            intent, action, conf = 'wayfinding', 'open', 0.85 if f.get('directions') else 0.75
        elif f.get('provenance') and not f.get('brief'):
            # where a figure came from, two figures squared, the count in the data, US or global: answered from the
            # record, never a read; a brief that names titles is the read lane's whole (2026-11-20, night)
            intent, action, conf = 'provenance', 'answer', 0.8
        elif f.get('pushback_only'):
            intent, action, conf = 'pushback', 'answer', 0.8
        elif f.get('thanks'):
            intent, action, conf = 'chat', 'answer', 0.85
        elif f.get('bare_reply'):
            # a bare yes / no / ok / number answers the previous turn: an open offer when one is, else the short-reply lane
            intent, action, conf = ('offer_reply', 'approve', 0.8) if f.get('offer_open') else ('chat', 'answer', 0.7)
        elif f.get('pricing'):
            intent, action, conf = 'pricing', 'answer', 0.8
        elif f.get('sizing') == 'segment' and not f.get('brief'):
            # a joint demographic segment counted ("how many people in your database are blue-collar men 30 to 55 ..."):
            # the segment lane sits ahead of every definition answer in the walk (2026-11-20, night)
            intent, action, conf = 'size', 'answer', 0.75
        elif (f.get('sample_size') or (f.get('definition') and f.get('definition') != 'drilldown')) and not f.get('brief'):
            # what a term, a figure or a method means (one, or a numbered list of them), the house sample, a check on
            # the last answer: answered from the house glossary and the thread, never a read; these lanes sit ahead of
            # the multi_ask split in the walk (2026-11-20, night)
            intent, action, conf = 'definition', 'answer', 0.75
        elif f.get('multi') and len(f['multi']) > 1 and not f.get('brief'):
            # several questions in one message: each answered through the door in turn (the multi_ask lane sits ahead
            # of the status, catalog and sizing lanes in the walk)
            intent, action, conf = 'multi_ask', 'answer', 0.75
        elif (sc.own_order and (f.get('status') or f.get('status_fast') or f.get('deliverable'))) or f.get('wait') \
                or (f.get('deliverable') and not f.get('directions')):
            # the reader's own order, delivery or wait is status. A bare status shape with no own-order words ("where
            # can I find retention curves on this site") is read below, after the catalog and sizing answers that sit
            # ahead of the status lanes in the walk (2026-11-20, night)
            intent, action, conf = 'status', 'status', 0.85 if sc.own_order else 0.75
        elif f.get('catalog') in ('exists', 'roster') and not f.get('brief') and not f.get('capability'):
            # is X in the library, what do we hold on X, the roster of a title: the catalog answers from the shelf,
            # no model call (the catalog_exists and catalog lanes, 2026-11-20, night); "is there any X measurement
            # available in Crosswalk" is a question about the product (capability), not the shelf
            intent, action, conf = 'exists', 'answer', 0.8
        elif (f.get('sizing') == 'audience' or f.get('catalog') == 'size') and not f.get('brief'):
            # how many people an audience holds: the sizing lanes (2026-11-20, night)
            intent, action, conf = 'size', 'answer', 0.75
        elif f.get('definition') == 'drilldown' and not f.get('brief'):
            intent, action, conf = 'definition', 'answer', 0.75   # a number on the open journey, a KPI: the definitions lane, late in the walk
        elif f.get('status_fast') or f.get('status'):
            intent, action, conf = 'status', 'status', 0.7   # the generic status shape, nothing else read
        elif f.get('capability'):
            intent, action, conf = 'capability', 'answer', 0.7
        elif f.get('correction_words') and history:
            intent, action, conf = 'continuation_correction', 'answer', 0.65
        elif f.get('rerun_words') and history and not u.named_title:
            intent, action, conf = 'continuation_rerun', 'answer', 0.65
        elif sc.page and not u.named_title:
            intent, action, conf = 'analyze_page', 'answer', 0.6
            u.referent = 'page'
        if sc.no_build and vref and action == 'build':
            action = 'answer'
        if sc.page and not u.referent and not u.named_title and intent in _READ_INTENTS:
            u.referent = 'page'   # the words point at the screen: the size, the read or the definition is of what is open
        if u.named_title and intent in ('analyze', 'analyze_page', 'continuation_rerun'):
            u.subjects = [Subject(as_written=u.named_title, kind='title', role='title')]
            if intent != 'analyze':
                intent, u.referent = 'analyze', ''
        if not u.subjects and intent in _READ_INTENTS:
            # the subject the words themselves name (the binder, no reading yet): it rides the Understanding so the read
            # is on it and never on the page that happens to be open (Emma Crouchen 2026-11-20)
            _ws = _words_subject(t, history, ctx)
            if _ws is not None:
                u.subjects = [_ws]
                ev.append('named:' + str(_ws.kind))
        if wiz_open and not (intent in WIZARD_YIELDS_TO and conf >= WIZARD_YIELD_CONF and flipped('wizard_interrupt')):
            # a Brand Partnership step waits on this thread and the words do not read, with confidence, as something
            # else: the turn is the wizard's (the step wins); a confident unrelated ask keeps its own intent and the
            # wizard stands aside for it (Jenna 2026-11-20, interrupt)
            intent, action, conf = 'armed', '', 0.9
            sc.armed_step = 'bpiq_wizard'
        u.intent, u.action, u.confidence, u.source = intent, action, round(conf, 2), 'features'
        if sc.no_build and u.action == 'build':
            u.action = 'answer'
        return u
    except Exception:
        return Understanding(evidence=['features_failed'], text_key=text_key(t))


def absorb_reading(u, reading, *, min_conf=0.7):
    """Refine the Understanding with the Reader's reading. A hard rule
    (armed, cancel, order, no_build) stands; otherwise a confident
    reading's intent replaces a features intent it does not contradict,
    and its subjects, window and platform ride along."""
    u = Understanding.from_dict(u) if not isinstance(u, Understanding) else u
    if u is None:
        u = Understanding()
    if not isinstance(reading, dict) or not reading.get('intent'):
        return u
    try:
        r_int = str(reading.get('intent') or '')
        try:
            r_conf = float(reading.get('confidence') or 0)
        except (TypeError, ValueError):
            r_conf = 0.0
        subs = []
        for s in reading.get('subjects') or []:
            if isinstance(s, dict) and (s.get('as_written') or s.get('catalog')):
                subs.append(Subject(as_written=str(s.get('as_written') or ''), catalog=str(s.get('catalog') or ''),
                                    kind=str(s.get('kind') or 'unknown'), role='title' if str(s.get('kind')) == 'title' else 'subject'))
        if subs and not u.subjects:
            u.subjects = subs
        if reading.get('window') and not u.window:
            u.window = str(reading.get('window'))[:200]
        if reading.get('platform') and not u.platform:
            u.platform = str(reading.get('platform'))[:60]
        if 'refers_to_page' in (reading.get('flags') or []) and not u.referent:
            u.referent = 'page'
        u.evidence = list(u.evidence) + [f"reader:{r_int}@{r_conf:.2f}"]
        hard = u.intent in ('armed', 'cancel', 'order', 'data_floor') or u.scope.no_build and r_int in ('build_profile', 'cut', 'journey', 'valuation', 'campaign', 'tracker', 'twin', 'deck')
        if hard or r_conf < min_conf or r_int not in INTENTS:
            u.source = 'reconciled' if hard else u.source
            return u
        # the contested pairs are settled by the words' scope, never by the reading alone:
        # a reading of status on a feature question stays wayfinding; a reading of command on an insight ask stays insight
        if u.intent == 'wayfinding' and r_int == 'status' and not u.scope.own_order:
            u.source = 'reconciled'
            return u
        if u.intent == 'insight_ask' and r_int == 'command':
            u.source = 'reconciled'
            return u
        if u.intent == 'brand_fit' and r_int in ('valuation', 'command'):
            u.source = 'reconciled'   # which brands suit a talent names no pairing to value (Jenna 2026-11-20)
            return u
        if u.intent == 'capability' and any(str(e).startswith('product:') for e in u.evidence):
            u.source = 'reconciled'   # a question about the product itself is answered from product knowledge, whatever the Reader read (2026-11-20)
            return u
        if u.intent in _CONTESTED and r_int not in _CONTESTED and u.confidence >= 0.8:
            u.source = 'reconciled'   # the words settled a contested pair with a strong feature; the reading fills the rest
            return u
        u.intent = r_int
        u.confidence = round(max(u.confidence, r_conf), 2)
        u.source = 'reader'
        if r_int in ('build_profile', 'cut', 'journey', 'valuation', 'campaign', 'tracker', 'twin'):
            u.action = 'build'
        elif r_int == 'deck':
            u.action = 'deck'
        elif r_int == 'command':
            u.action = 'send'
        elif r_int == 'status':
            u.action = 'status'
        elif r_int == 'decline':
            u.action = 'cancel'
        else:
            u.action = 'answer'
        return u
    except Exception:
        return u


# ---------------------------------------------------------- the lanes
def on_body(body):
    """The Understanding a body carries, or None."""
    try:
        return Understanding.from_dict((body or {}).get('_pm_understanding'))
    except Exception:
        return None


def attach(body, u):
    """The Understanding on the body (a new dict) and on g for the ask log.
    A nested ask (a part of a split message, a replayed question) keeps
    its own on the body and never overwrites the outer ask's on g."""
    body = dict(body or {})
    if isinstance(u, Understanding):
        body['_pm_understanding'] = u.to_dict()
        if not (body.get('_multi_part') or body.get('_meta_replay')):
            try:
                from flask import g as _g
                _g._pm_understanding = body['_pm_understanding']
            except Exception:
                pass
    return body


def claims(body, name, fallback):
    """Whether the lane for ``name`` runs on this ask.

    Not flipped, or no Understanding on the body: the lane's own
    predicate (``fallback``, a callable) decides, as it always did. Flipped:
    the lane runs when the Understanding reads the ask as this intent and
    the lane's own predicate agrees (the lane must still be able to
    answer); it stands down when the Understanding read the ask as one of
    the other contested intents; where the Understanding took no position
    (a generic reading) the lane's own predicate decides."""
    def _fb():
        try:
            return bool(fallback()) if callable(fallback) else bool(fallback)
        except Exception:
            return False
    if not flipped(name):
        return _fb()
    u = on_body(body)
    if u is None:
        return _fb()
    # the words changed since the reading (a lane rewrote the text: a typed option, the turn gate's re-bind): the
    # reading is of other words and takes no position on these
    if u.text_key and body.get('text') is not None and text_key(body.get('text')) != u.text_key:
        return _fb()
    n = str(name or '').strip().lower()
    if u.intent == n:
        ok = _fb()
        if ok:
            _mark(body, u)
        return ok
    if u.intent in _CONTESTED and float(u.confidence or 0) >= STAND_DOWN_CONF and (u.intent not in FLIPPED or flipped(u.intent)):
        # a reading whose own flip is switched off is back on the lane walk: it stands nothing down either
        if _fb():
            _mark(body, u)   # the lane wanted the ask and stood down on the Understanding's reading: a keyed decision
        return False
    return _fb()


def mark_keyed(body):
    """An executor keyed on the Understanding answered this ask (the ask log reads flip=1)."""
    u = on_body(body)
    if u is not None:
        _mark(body, u)


def _mark(body, u):
    """The ask log reads that a decision was keyed on the Understanding (a
    lane ran on it, or stood down on it)."""
    try:
        u.flipped = True
        body['_pm_understanding'] = u.to_dict()
        if not (body.get('_multi_part') or body.get('_meta_replay')):
            from flask import g as _g
            _g._pm_understanding = body['_pm_understanding']
    except Exception:
        pass


# -------------------------------------------------------- the ask log
def stamp_log(extra, g):
    """The Reader's reading (as the log always carried it) and the
    Understanding's compact line, beside the route the lane walk chose."""
    extra = dict(extra) if isinstance(extra, dict) else {}
    try:
        rd = getattr(g, '_pm_reading', None)
        if rd:
            extra['reading'] = {k: rd.get(k) for k in ('intent', 'confidence', 'subjects', 'flags', 'restate', 'ms')}
    except Exception:
        pass
    try:
        ud = getattr(g, '_pm_understanding', None)
        if isinstance(ud, dict):
            u = Understanding.from_dict(ud)
            if u is not None:
                extra['u'] = u.compact()
    except Exception:
        pass
    return extra


def agrees(u, route, outcome='', surface=''):
    """True when the Understanding's intent lands in the route's class
    (the one table in prometheus.intent_classes)."""
    try:
        if isinstance(u, str):
            intent = parse_compact(u).get('intent') or ''
        elif isinstance(u, Understanding):
            intent = u.intent
        else:
            intent = str((u or {}).get('intent') or '')
        return _ic.agrees(intent, route, outcome, surface)
    except Exception:
        return False


def grade_log_rows(rows):
    """{'n', 'agree', 'by_class': {cls: {'n', 'agree'}}, 'disagree': [...]}
    over ask-log rows that carry a compact Understanding (extra.u)."""
    n = agree = 0
    by_class = {}
    dis = []
    for r in rows or []:
        extra = r.get('extra') if isinstance(r.get('extra'), dict) else {}
        c = str(extra.get('u') or '')
        if not c:
            continue
        p = parse_compact(c)
        cls = _ic.class_of(r.get('route'), r.get('outcome'), r.get('surface'))
        row = by_class.setdefault(cls, {'n': 0, 'agree': 0})
        row['n'] += 1
        n += 1
        if _ic.agrees(p.get('intent'), r.get('route'), r.get('outcome'), r.get('surface')):
            agree += 1
            row['agree'] += 1
        else:
            dis.append({'intent': p.get('intent'), 'conf': p.get('conf'), 'src': p.get('src'), 'route': r.get('route'),
                        'outcome': r.get('outcome'), 'class': cls, 'flip': p.get('flip')})
    return {'n': n, 'agree': agree, 'by_class': by_class, 'disagree': dis[:40]}


__all__ = ['current', 'named_subject', 'read_subject_hint', 'Understanding', 'Subject', 'Scope', 'INTENTS', 'LANE_INTENTS', 'FLIPPED', 'SHADOW', 'enabled', 'flipped',
           'features', 'absorb_reading', 'on_body', 'attach', 'claims', 'mark_keyed', 'stamp_log', 'agrees', 'grade_log_rows',
           'parse_compact', 'text_key']
