"""A question about what we can see or tell apart gets the answer, never
the product description (Jenna 2026-11-13: "the answer here would have
been yes, not what it gave").

David Carter (Sony Pictures Television), 19:12 and 19:14 UTC on
2026-11-13, two questions in one thread:

    "Is there any podcast audience measurement available in Crosswalk?"
    "Can you differentiate between audio podcasts and video podcasts?"

Both drew "Yes." bolted onto the Profile IQ description ("A Profile IQ
profile builds the audience of any person, title, brand or behavior ...
Tell me the subject and the window and I will line it up; it comes back
as a brief to approve before anything is charged"). Neither reply said a
word about podcasts.

The cause, two paths. The second question opens "Can you", names no
product, and its verb ("differentiate") was not one the yes lane knew
(prometheus.data_capability), so smarter.capability_ask took it and,
with no product named, defaulted to the last entry of its product table.
The first question opens "Is there any ... available", which neither
lane read; the Reader called it a capability question and
reader.capability_reply picked a product off the Reader's own note (it
held the word "audience"). Samantha Kolton's "can you explan the
pinterst more?" (2026-11-06) was the same default; the 2026-11-11 fix
(prometheus.own_read) stood the lane aside only when a read sat in the
thread.

The rule. A question about what Crosswalk can see, tell apart, measure
or hold is answered in this order: yes, no or partly first; then how, in
a sentence or two specific to what was asked, with the line where it
stops; then one next step. A product description is the answer only to
a question that names a product or the thing a product does. A build
brief is never the answer to a question.

What this module holds, and the two ways a question is answered:

  * the coverage sheet (FACTS): the lines we are asked about most that
    have a limit worth stating, each written once from what the product
    does (the services a title is matched on, episode by episode; the
    YouTube full-episode default with clips as the option; app and device
    coverage; watching a service against paying for it). It is a sheet
    of what is measured, not a list of questions: ``answer(text)``
    returns the standing payload when a question lands on one of its
    lines, so the limit is always stated the same way;
  * ``explain(text)``: the next can-you question, the one no line holds
    (2026-11-16). One model call, grounded in the same sheet and the
    house boundary, held to the same shape (yes, no or partly first; how
    in a sentence or two; one next step) and checked before it goes out:
    a reply that describes a product, offers a brief, states a figure or
    opens any other way is dropped, and the one-word yes stands.

Everything else keeps its lane, and nothing here widens what they claim:

  * behavior with no digital trace (linear TV, in-store, radio) is the
    quantifiability gate's decline with the nearest measurable read;
  * a methodology shape (devices, viewing time, age, a view) is
    prometheus.methodology_answers';
  * a yes-answerable question about the data with no line to state is
    "Yes." with the person's own question as the chip
    (prometheus.data_capability, Jenna 2026-10-26);
  * a question that names a product keeps the product answer; an order
    with manners, work behind a can-you frame, a question about material
    on the thread and a question that names its own subject ("do you
    have a podcast profile for Call Her Daddy") stay what they are.

Pure module: no Flask, no S3, no model. Never raises. Kill switch
``PM_CAN_DO=0``.
"""
from __future__ import annotations

import os
import re

__all__ = ['enabled', 'is_can_question', 'fact_for', 'answer', 'explain', 'shape_ok', 'FACTS', 'reads_as_data_ability', 'is_offline']

_MAX_LEN = 320

_LEAD = (r"^\W*(?:(?:so|and|also|hey|hi|hello|ok|okay|thanks|thank you|great|got it|just curious|quick question|one more|"
         r"one more question|one more thing|new question|another question|a different question|different question|"
         r"separate question|side question|unrelated|question|prometheus|crosswalk)[,.:!\s-]+)*")
_WHO = r"(?:you|we|i|crosswalk|prometheus|it|the data|your data|the platform|the dashboard)"
_ABILITY = (r"(?:tell|see|know|measure|track|count|capture|cover|detect|identify|distinguish|differentiate|separate|split|"
            r"break|isolate|show|report|read|size|pick|parse|recognize|recognise|flag|single)")
_HOLD = (r"(?:have|hold|carry|cover|capture|measure|track|see|include|offer|support|collect|count|report|distinguish|"
         r"differentiate|separate|break out|know|do)")
_MEASURE_NOUN = (r"(?:measurement|measurements|data|coverage|tracking|visibility|reporting|numbers|figures|metrics|a read|reads|"
                 r"insight|insights|information|info|analytics|signal)")

# the frames a can-you question opens with
_FRAMES = tuple(re.compile(_LEAD + p, re.I) for p in (
    rf"(?:can|could|would|will) {_WHO}\b",
    rf"(?:are|is) {_WHO} able to\b",
    r"is (?:it|this|that) (?:even |at all |actually |really )?(?:possible|feasible|doable) (?:for (?:you|crosswalk|prometheus) )?to\b",
    r"(?:is there|would there be) (?:a|any|some) way (?:for (?:you|crosswalk|prometheus) )?to\b",
    rf"(?:do|does|did) {_WHO} (?:(?:already|actually|even|also|currently|happen to) )?{_HOLD}\b",
    rf"(?:is|are) there (?:any |a |some |good |reliable )?[\w' /&+-]{{0,60}}?\b{_MEASURE_NOUN}\b",
    rf"how (?:do|does|can|would|could) {_WHO} (?:tell|know|distinguish|differentiate|separate)\b",
    rf"(?:is|are) [\w' /&+-]{{2,60}}? (?:measured|tracked|counted|covered|captured|separated|split out|broken out|reported|available)\b",
    # "do the Apple Podcasts and Spotify rankers combine audio and video into one figure?"
    r"(?:do|does|did|are|is) (?:the|your|our|crosswalk'?s?|those|these) [\w' /&+-]{2,80}?\b(?:rankers?|rankings?|boards?|leaderboards?|"
    r"charts?|figures?|counts?|numbers)\b[\w' /&+-]{0,40}?\b(?:combine[sd]?|include[sd]?|separate[sd]?|split|count(?:s|ed)?|distinguish|"
    r"differentiate|merge[sd]?|blend(?:s|ed)?|add(?:s|ed)?|roll(?:s|ed)?|break|broken|lump(?:s|ed)?|mix(?:es|ed)?)\b",
))

# words that show the question is about telling things apart or seeing
# them at all (the yes lane's verbs, and the ones it did not know)
_DATA_ABILITY_RX = re.compile(
    rf"\b(?:{_ABILITY}|tell (?:apart|the difference)|the difference between|versus|vs\.?|as opposed to|rather than|"
    rf"{_MEASURE_NOUN}|available|measured|tracked|counted|covered)\b", re.I)

# names that are never the question's own subject: services, the product
# and the words a sentence can capitalize
_NOT_A_SUBJECT = frozenset("""
i i'm i'd i've crosswalk prometheus us u.s usa america american tv ctv youtube spotify apple podcasts podcast iheart
iheartradio pandora siriusxm sirius xm amazon music audible npr netflix hulu peacock max hbo disney paramount prime video
tiktok instagram facebook meta x twitter reddit twitch roku tubi pluto fast svod avod tvod pvod est iq profile subscriber
digital journey attribution brand partnership twins shorts reels live android ios iphone ipad google chrome safari windows
mac web app apps ok okay yes no and or the a an is are can could do does on in of for with from by to at as if
premium plus basic free kids originals original music tv+ gen z
""".split())
_CAP_TOKEN_RX = re.compile(r"\b[A-Z][\w'&.+-]*")


def enabled():
    return str(os.environ.get('PM_CAN_DO', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(str(text or '').split())


def _frame(t):
    for rx in _FRAMES:
        m = rx.match(t)
        if m:
            return m
    return None


def _names_own_subject(t, start):
    """True when the words past the frame name a subject of their own (a
    show, a person, a brand): that is an ask about that subject."""
    rest = t[start:]
    for m in _CAP_TOKEN_RX.finditer(rest):
        w = m.group(0).strip(".'").lower()
        if w and w not in _NOT_A_SUBJECT and len(w) > 1:
            return True
    return False


def _stays_elsewhere(t):
    """An order, work behind the frame, material on the thread, a deck, an
    action we do not take, or a status or delivery ask keeps its lane."""
    try:
        from . import orders as _orders
        if _orders.is_order(t):
            return True
    except Exception:
        pass
    try:
        from . import smarter as _sm
        if _sm.polite_order(t) or _sm.advisory_order(t) or _sm._MATERIAL_RX.search(t) or _sm._DECK_ASK_RX.search(t):
            return True
    except Exception:
        pass
    try:
        from . import capabilities as _caps
        if _caps.action_request(t):
            return True
    except Exception:
        pass
    try:
        from . import status_lane as _stl
        from . import deliverables as _dlv
        if _stl.wants_status_fast(t) or _dlv.parse(t):
            return True
    except Exception:
        pass
    return False


def is_can_question(text):
    """True for a question about what Crosswalk can see, tell apart,
    measure or hold, asked in general (no subject of its own named)."""
    t = _clean(text)
    if not t or len(t) > _MAX_LEN:
        return False
    m = _frame(t)
    if not m:
        return False
    if len(t[m.end():].strip(' ?.!,').split()) < 1 and not _DATA_ABILITY_RX.search(t):
        return False
    if _names_own_subject(t, m.end()):
        return False
    return not _stays_elsewhere(t)


def reads_as_data_ability(text):
    """True when a can-you question is about seeing or telling things
    apart (never about building, pricing or sending something)."""
    t = _clean(text)
    return bool(is_can_question(t) and _DATA_ABILITY_RX.search(t))


# ------------------------------------------------------------------ facts
# Each: key, verdict ('yes' | 'partly' | 'no'), when (every pattern must
# be found), unless (none may be found), how (where the line sits, stated
# once), next (one step). Order matters: the first match answers.
_PODCAST = r"\b(?:podcasts?|podcasters?|podcasting|vodcasts?)\b"
_AUDIO = r"\b(?:audio|listen(?:s|ed|ing|ers?)?|listenership|heard|hear|audio[- ]only)\b"
_VIDEO = r"\b(?:video|videos|watch(?:es|ed|ing|ers?)?|view(?:s|ed|ing|ers?)?|vodcasts?|on camera|filmed|picture)\b"
_ONE_APP = (r"\b(?:on|in|inside|within) (?:the )?(?:same|one|a single) (?:app|service|platform)\b|"
            r"\b(?:on|in|inside|within) spotify\b|\bspotify(?:'s)? video\b|\bvideo (?:on|in) spotify\b|"
            r"\bsame (?:app|episode|service)\b|\bin[- ]app\b")
# behavior that leaves nothing on a person's digital record. The
# quantifiability gate declines most of these before this module is
# reached; this is the same boundary read a little wider ("heard it on the
# radio" passes the gate's "listen ... on the radio"), so a can-you
# question about one of them is never answered yes here.
_OFFLINE = (r"\b(?:radio|linear|broadcast tv|broadcast television|over[- ]the[- ]air|terrestrial|cable (?:tv|television|box)|"
            r"in[- ]?store|in[- ]person|brick[- ]and[- ]mortar|billboards?|out[- ]of[- ]home|print (?:ads?|magazines?|newspapers?)|"
            r"newspapers?|word of mouth|offline|foot traffic|at the register|at the checkout counter)\b")
_OFFLINE_RX = re.compile(_OFFLINE, re.I)

FACTS = (
    {'key': 'offline', 'verdict': 'no',
     'when': (_OFFLINE,),
     'unless': (),
     'lead': 'No.',
     'how': ("Radio, linear TV, in-store and in-person activity leave nothing on a person's digital record, so that is "
             "outside what I measure. What I can read is the digital side of the same behavior: streaming, search, social, "
             "app and online purchase activity."),
     'next': 'Name the subject and I will read that side.'},
    {'key': 'podcast_rankers', 'verdict': 'yes',
     'when': (_PODCAST, r"\b(?:rankers?|rankings?|boards?|leaderboards?|charts?)\b", _AUDIO + '|' + _VIDEO),
     'unless': (),
     'lead': 'Yes, each board is one figure per show.',
     'how': ("A podcast ranker counts the people who played a show on that one service, so on the Apple Podcasts and Spotify "
             "boards a show's audio and video plays are a single figure. Video podcasts also have boards of their own, for "
             "YouTube and for Netflix, so the video side can be read there beside the audio boards."),
     'next': 'Name a show and I will set its boards side by side.'},
    {'key': 'podcast_one_app', 'verdict': 'no',
     'when': (_PODCAST + '|' + r"\bepisodes?\b|\bspotify\b", _VIDEO, _ONE_APP),
     'unless': (),
     'lead': 'No, not inside one app.',
     'how': ("When an app plays the same episode with or without the picture, as Spotify does, I count the play and do not "
             "split audio from video. What I can separate is where it was played: the audio apps on one side and the video "
             "episodes on YouTube on the other."),
     'next': 'Name the show and I will split it that way.'},
    {'key': 'podcast_audio_video', 'verdict': 'yes',
     'when': (_PODCAST + '|' + r"\blisteners?\b.*\bviewers?\b|\bviewers?\b.*\blisteners?\b", _AUDIO, _VIDEO),
     'unless': (),
     'lead': 'Yes.',
     'how': ("A podcast is matched episode by episode on every service it plays on. So the people who listened on the audio "
             "apps (Spotify, Apple Podcasts, iHeartRadio, Pandora, Amazon Music, SiriusXM, NPR) are counted apart from the "
             "people who watched its video episodes on YouTube, and I can show each group and the people in both. The one "
             "limit is inside a single app that plays an episode with or without the picture, such as Spotify: there I "
             "count the play and do not split audio from video."),
     'next': 'Name the show and I will split its listeners from its viewers.'},
    {'key': 'episodes_clips', 'verdict': 'yes',
     'when': (r"\b(?:full[- ]?(?:length )?episodes?|episodes?|long[- ]form)\b", r"\b(?:clips?|highlights?|shorts|cut[- ]?downs?|excerpts?)\b"),
     'unless': (),
     'lead': 'Yes.',
     'how': ("On YouTube a show's audience is built on its full episodes by default, and the channel's clips can be added as "
             "a second read, so the people who only watched clips are the difference between the two. Shorts are left out "
             "of both."),
     'next': 'Name the show and say which you want: full episodes, or full episodes with clips.'},
    {'key': 'podcast_measured', 'verdict': 'yes',
     'when': (_PODCAST,),
     'unless': (),
     'lead': 'Yes.',
     'how': ("A podcast's audience is the people who played its episodes on Spotify, Apple Podcasts, iHeartRadio, Pandora, "
             "Amazon Music, SiriusXM, NPR or YouTube, read with who they are and the brands, platforms and talent they "
             "over-index on. The listeners on the audio apps and the viewers of the video episodes on YouTube can be "
             "read together or apart."),
     'next': 'Name a show or a host and I will size its audience.'},
    {'key': 'paying_against_free', 'verdict': 'partly',
     'when': (r"\b(?:subscribers?|subscrib(?:e|es|ed|ing)|paying|paid|pay for|premium|ad[- ]free)\b",
              r"\b(?:free|ad[- ]supported|with ads|ad tier|non[- ]?subscribers?|unpaid|do not pay|don't pay|without (?:a )?subscription)\b"),
     'unless': (),
     'lead': 'Partly.',
     'how': ("People who signed up for a service are counted apart from people who watched it, so subscribers and viewers "
             "are two separate reads, and the free services that run on ads are read on their own. What I do not split is "
             "the plan inside one paid service, with ads or without."),
     'next': 'Name the service or the title and I will set its subscribers beside its viewers.'},
    {'key': 'app_use', 'verdict': 'yes',
     'when': (r"\b(?:apps?|in[- ]app|mobile apps?)\b", r"\b(?:use|usage|used|using|activity|sessions?|opens?|time spent|engagement|behaviou?r)\b"),
     'unless': (r"\bsame app\b",),
     'lead': 'Yes.',
     'how': ("App activity is measured alongside sites, on phones, computers and connected TVs, tied to the same person, so "
             "what someone does in an app is counted with what they do everywhere else."),
     'next': 'Name the app or the audience and I will read it.'},
)


def is_offline(text):
    """True when the words name behavior with no digital record (radio,
    linear TV, in-store, in-person). 'iHeartRadio' is a service, not radio."""
    try:
        return bool(_OFFLINE_RX.search(_clean(text)))
    except Exception:
        return False


def fact_for(text):
    """The fact a question is about, or None."""
    t = _clean(text)
    for f in FACTS:
        try:
            if all(re.search(p, t, re.I) for p in f['when']) and not any(re.search(p, t, re.I) for p in f['unless']):
                return f
        except re.error:
            continue
    return None


def _gate(t):
    """The no-digital-trace decline owns its questions."""
    try:
        import prometheus_analysis as _pma
        return bool(_pma.classify_quantifiability(t))
    except Exception:
        return False


def _methodology(t):
    try:
        from . import methodology_answers as _ma
        return bool(_ma.shape_of(t))
    except Exception:
        return False


def _asks_for_a_product(t):
    """The question is about a product by name or about making, pricing or
    sending something: the product answer, the pricing lane or the order
    keeps it. A plain word like 'subscribers' or 'audience' does not make
    a question a product question."""
    return bool(re.search(
        r"\b(?:profile iq|subscriber iq|digital journey|journey iq|attribution iq|brand partnership|digital twins?|focus groups?|"
        r"a profile|new profile|profiles|a journey|a valuation|a deck|decks?|slides?|a cut|"
        r"build|builds|building|run|running|pull|pulling|create|creating|make|making|generate|draft|set up|queue|refresh|"
        r"price|prices|pricing|cost|costs|credits?|csv|export|download|e-?mail (?:me|us|it|this|that|the)|send)\b", t, re.I))


def answer(text):
    """The standing payload for a can-you question we hold a fact on:
    yes, no or partly first, then how, then one next step. None when the
    question is not one of these (the other lanes keep it)."""
    try:
        if not enabled():
            return None
        t = _clean(text)
        if not is_can_question(t):
            return None
        if _gate(t) or _methodology(t):
            return None
        f = fact_for(t)
        if not f:
            return None
        if _asks_for_a_product(t):
            return None
        if f['verdict'] == 'yes' and _own_question(t):
            # "can you see which podcasts young men listen to": the question inside is the next step; "Yes." and
            # the person's own question as the chip (prometheus.data_capability, Jenna 2026-10-26)
            return None
        reply = f"{f['lead']} {f['how']} {f['next']}"
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'can_do': f['key'], 'can_do_verdict': f['verdict']}
    except Exception:
        return None


def _own_question(t):
    """The data question a can-you frame carries ("which podcasts young
    men listen to"), or ''."""
    try:
        from . import data_capability as _dc
        return _dc.question(t)
    except Exception:
        return ''


# --------------------------------------------------- the next can-you question
# What Crosswalk measures, in the house's words, and the lines of the
# sheet. This is the only ground the written answer may stand on.
_WHAT_WE_MEASURE = (
    "Crosswalk measures what US consumers do across their digital life on one opted-in identity: search, social, "
    "streaming video and audio, sites, apps, games and online purchases, on phones, computers and connected TVs, "
    "each person counted once. A title or a show is matched on each service it plays on, episode by episode, so "
    "activity can be read by service, by title and by person. Behavior with no digital record (radio, linear and "
    "over the air TV, in-store and in-person activity, print) is outside what is measured.")


def _sheet():
    return '\n'.join(f"- {f['lead']} {f['how']}" for f in FACTS)


_SYSTEM = (
    "You are Prometheus, the analyst inside Crosswalk's dashboard. The person asked whether Crosswalk can see, "
    "measure, hold or tell something apart. Answer that question and nothing else.\n\n"
    "SHAPE, exactly: the first word is Yes, No or Partly, followed by a period. Then one or two plain sentences on "
    "how, specific to what they asked, and where it stops if there is a limit. Then one short sentence giving the "
    "one next step, which is what they should name or ask. Under 80 words. One paragraph, no list.\n\n"
    "WHAT IS MEASURED:\n{measure}\n\nLINES ALREADY SETTLED (do not contradict them):\n{sheet}\n\n"
    "RULES: If the behavior has no digital record, the answer is No, with the nearest digital read. If a settled "
    "line says a split is not made, never claim it: answer Partly or No. If nothing above covers the question and "
    "it is behavior on a person's phone, computer or connected TV, the answer is Yes with a plain how. If you "
    "cannot tell whether a split is possible, answer Partly and say what can be read. Never describe a product, "
    "never offer a profile, a brief, a build, a price or a deck. Never state a number. Never say synthetic, "
    "modeled, estimated, panel, pipeline, clickstream or name any model or data source. Never use a dash as "
    "punctuation. Plain words a 7th grader would follow.")

_SHAPE_LEAD_RX = re.compile(r"^(?:Yes|No|Partly)\b[.,]")
_SHAPE_BANNED_RX = re.compile(
    r"\b(?:brief|charged|charge|credits?|price[sd]?|pricing|quote[sd]?|profile iq|subscriber iq|journey iq|attribution iq|"
    r"brand partnership|digital twins?|a profile|line it up|approve|deck|synth\w*|model(?:ed|led|ing)?|estimat\w*|"
    r"panel\w*|pipeline|clickstream|hostmap|household\w*|nielsen|comscore|statista|pew|claude|anthropic|openai|gpt|llm|"
    r"real[- ]time)\b|[$%\u2014\u2013]|\d{3,}", re.I)


def shape_ok(reply):
    """True when a written answer keeps the shape: yes, no or partly
    first, short, no product description, no offer, no figure."""
    r = _clean(reply)
    if not r or not _SHAPE_LEAD_RX.match(r):
        return False
    if len(r.split()) > 110 or '\n' in str(reply or '').strip():
        return False
    return not _SHAPE_BANNED_RX.search(r)


def explain(text, *, reason=None, max_tokens=900):
    """The written answer to a can-you question the sheet holds no line
    for: yes, no or partly first, then how, then one next step. One
    model call (``reason(system, user)`` returns text), grounded in what
    is measured and the sheet, and checked by ``shape_ok``. None when the
    question is not one of these, when no model is at hand, or when what
    came back does not keep the shape (the caller's one-word yes
    stands)."""
    try:
        if not enabled():
            return None
        t = _clean(text)
        if not reads_as_data_ability(t) or _gate(t) or _methodology(t) or is_offline(t) or _asks_for_a_product(t):
            return None
        if _own_question(t):
            return None      # the question inside is the next step: "Yes." and the chip
        if reason is None:
            if os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_REPLAY_CHILD'):
                return None  # a test or a replay never spends a call it did not hand in
            try:
                from claude_client import claude_reason_json as _crj
            except Exception:
                return None
            reason = lambda sys_, usr: _crj(system=sys_, user=usr, max_tokens=max_tokens, temperature=0.2,  # noqa: E731
                                            usage_tag=('analyze', 'can-do'))
        system = _SYSTEM.format(measure=_WHAT_WE_MEASURE, sheet=_sheet())
        try:
            out = str(reason(system, "THE QUESTION:\n" + t + "\n\nWrite the answer now.") or '').strip()
        except Exception:
            return None
        out = out.strip().strip('"').strip()
        if not shape_ok(out):
            return None
        out = ' '.join(out.split())
        verdict = out.split('.', 1)[0].split(',', 1)[0].strip().lower()
        return {'success': True, 'action': 'answer', 'reply': out, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'can_do': 'written', 'can_do_verdict': verdict}
    except Exception:
        return None
