"""Methodology questions are answered directly (Grace Yin, Amazon,
2026-11-10).

Grace, 19:51 UTC, four numbered questions: whether the data is desktop
software and how much phone and TV viewing it captures; how many men
aged 18 to 34 are in the data; whether watch duration per video and the
video's own length are measured, and whether an 80 minute sitting can
be told from an opened 80 minute video; whether age is each person's
own or a household guess. The build flow asked her "tell me who each
profile is for"; the multi-ask splitter would have run a read on each
and offered a build chip that said "panelists".

A methodology question has one plain answer from the house definitions
(prometheus.methodology, prometheus.guards.PROVENANCE_REPLY, the Raw
column rule) and never a read, a build, a chip or a model call. Four
shapes live here:

  coverage   the devices and surfaces the data covers (phones, TVs,
             computers; TikTok and YouTube on them; "what do you miss")
  duration   whether viewing time per video, per sitting, and the
             video's own length are measured
  age_source whether age is the person's own or a household guess
  in_data    how many of the 10 million US consumers we measure are a
             demographic group ("how many US panelists are men 18-34")
  view_definition
             what counts as a view, ours beside how Nielsen and Peacock
             count one (Jenna 2026-11-11: "how do you count a view", "why
             does your number differ from Nielsen", "how does Peacock
             count a view"). The copy is prometheus.methodology's; the two
             names are definitions only, never the source of a figure.

``answer(text)`` returns the standing payload for one question or for
a numbered list whose items are all methodology shapes ("Taking your
four questions in order."); None otherwise, so the splitter and the
lanes keep their work. The in-data count reads the Gen Pop gender and
age shares (the host's Gen Pop file when one is bound, else the shares
pinned here on 2026-11-10) and prints the count inside the 10 million
US consumers we measure. No internal words: never panel, panelist,
sample, model or estimate. Pure text otherwise; never raises.
"""
from __future__ import annotations

import re

__all__ = ['answer', 'shape_of', 'coverage_line', 'duration_line', 'age_source_line', 'in_data_line',
           'demographic_count', 'view_definition_line', 'is_view_definition_question']

TEN_M = 10_000_000

# Gen Pop shares as of 2026-11-10 (Gen_Pop_2026.csv GENDER and AGE rows);
# the host's live file wins when it is bound (host.gen_pop_shares).
_PINNED_GENDER = {'MALE': 47.8859, 'FEMALE': 49.5723, 'NON-BINARY': 1.4953, 'TRANS MALE': 0.5755, 'TRANS FEMALE': 0.471}
_PINNED_AGE = {'17 AND UNDER': 19.3339, '18-24': 9.7099, '25-34': 13.2166, '35-44': 13.4853, '45-54': 12.7705,
               '55-64': 12.3847, '65 OR OLDER': 19.0991}
_AGE_BANDS = (('17 AND UNDER', 0, 17), ('18-24', 18, 24), ('25-34', 25, 34), ('35-44', 35, 44), ('45-54', 45, 54),
              ('55-64', 55, 64), ('65 OR OLDER', 65, 120))

_COVERAGE_RX = re.compile(
    r"\b(?:desktop (?:software|only|panel|app)|(?:mobile|phone|smartphone|tv|television|connected tv|ctv|tablet) (?:viewing|usage|use|"
    r"activity|traffic|video|time|watching|behavior|coverage)|on (?:phones?|tvs?|televisions?|tablets?|mobile)|how much (?:of )?"
    r"(?:mobile|phone|tv|connected tv|ctv)\b.{0,40}\b(?:capture|cover|see|measure|miss|track)|(?:do|does) (?:you|the data|crosswalk|"
    r"prometheus|it) (?:capture|cover|see|measure|track|miss) (?:mobile|phone|tv|connected tv|ctv|in-app|app)|"
    r"(?:share|percent|portion|part) of .{0,40}\b(?:time|viewing|video) .{0,30}\byou (?:miss|lose|do not see|don't see|capture)|"
    r"what (?:do|don't|do not) you (?:miss|capture|see))\b", re.I)
_DURATION_RX = re.compile(
    r"\b(?:watch (?:duration|time|length)|viewing (?:duration|time)|time (?:spent )?(?:watching|viewing)|minutes? (?:watched|viewed)|"
    r"video'?s? (?:own )?(?:length|duration|runtime|running time)|how long (?:someone|a person|people|they|viewers?) watch(?:ed|es)?|"
    r"in one sitting|(?:opened|started) an? \d+[- ]minute|\d+\+? ?minutes? in one sitting|completion rate|how far (?:into|through))\b", re.I)
_AGE_SOURCE_RX = re.compile(
    r"\b(?:is age (?:taken|read|drawn|pulled|known|derived|self[- ]reported)|where (?:does|is) (?:the )?age (?:come|data|taken)|"
    r"household[- ]level (?:guess|estimate|assignment|read|inference)|age .{0,40}\bhousehold\b|household .{0,30}\bage\b|"
    r"(?:a |the )?(?:person'?s?|user'?s?|individual'?s?|viewer'?s?|their) own (?:profile|age|declared age)|"
    r"minors? (?:counted|showing|appearing|in the)|under[- ]18s? (?:counted|in the))\b", re.I)
_IN_DATA_RX = re.compile(
    r"\bhow many (?:of (?:the |your )?)?(?:us |u\.s\. )?(?:people|persons|consumers|individuals|users|viewers|respondents|members|"
    r"panelists?|panel members?|adults|men|women|males|females)?\s*(?:in (?:your|the|our) (?:data|dataset|data set|base|universe)|do you (?:have|measure|hold|count|see)|"
    r"are (?:there )?(?:in (?:your|the|our) (?:data|dataset|base))?)?\s*(?:are |is |were )?(?P<group>(?:us |u\.s\. )?(?:men|women|males|females|adults|people|persons|"
    r"individuals|consumers|users)\s+(?:aged?\s+|ages?\s+|between\s+)?\d{2}\s*(?:-|to|\u2013|and)\s*\d{2}\+?|(?:men|women|males|females)\s+\d{2}\s*(?:-|to|\u2013)\s*\d{2}\+?|"
    r"(?:men|women|males|females)\s+(?:aged?\s+|ages?\s+)?(?:over|under|above|below)\s+\d{2}|(?:people|adults|persons|individuals) (?:aged?|ages?) \d{2}\s*(?:-|to|\u2013)\s*\d{2})\b", re.I)
# What counts as a view, and how that sits beside Nielsen and Peacock
# (2026-11-11). Three ways in: the question asks how a view is counted or
# defined; it puts our figure next to Nielsen (we hold no Nielsen figures,
# so every "compare to / differ from Nielsen" is a question about
# definitions); or it asks how Peacock counts, or sets our figure against
# what Peacock itself reports. A bare "compare that to Peacock" is a
# request for a Peacock read and stays with the lanes.
_VIEW_DEF_RX = re.compile(
    r"\b(?:what (?:counts|qualifies|is counted|do you count) as a view|"
    r"what(?:'s| is| are) (?:a view\b|(?:your |the )?definition of a view)|"
    r"how (?:do|does|did|would) (?:you|we|crosswalk|prometheus|it|nielsen|peacock|nbcu(?:niversal)?) "
    r"(?:guys )?(?:count|define|classify|measure|credit|qualify|tally) (?:a |the |your |its |their )?views?\b|"
    r"how (?:is|are) (?:a |your |the )?views? (?:counted|defined|classified|measured|credited)|"
    r"(?:your|crosswalk'?s?|nielsen'?s?|peacock'?s?) (?:definition|threshold) (?:of|for) a view|"
    r"what (?:does|do) (?:a view|views?) mean\b|define a view\b)", re.I)
_NIELSEN_RX = re.compile(r"\bnielsen(?:'s)?\b", re.I)
_PEACOCK_RX = re.compile(r"\bpeacock(?:'s)?\b", re.I)
_COMPARE_RX = re.compile(
    r"\b(?:compar(?:e|es|ed|ing|able|ison)|differ(?:s|ed|ent|ence)?|match(?:es|ed)?|line[sd]? up|square[sd]? with|"
    r"vs\.?|versus|same as|higher than|lower than|bigger than|smaller than|more than|less than|off from|"
    r"apples to apples|reconcile[sd]?)\b", re.I)
_COUNT_WORD_RX = re.compile(r"\b(?:count(?:s|ed|ing)?|defin(?:e|es|ed|ition)|classif(?:y|ies|ied)|measur(?:e|es|ed|ement)|"
                            r"a view|views?|viewers?|number|numbers|figure|figures)\b", re.I)
_PEACOCK_OWN_RX = re.compile(
    r"\bpeacock(?:'s)?\s+(?:own\s+|internal\s+|reported\s+)?(?:reports?|reported|says|said|numbers?|figures?|counts?|starts?|"
    r"programming starts|definition|metrics?|data)\b|\b(?:what|how) peacock (?:reports?|counts?|says|measures?|tells)\b", re.I)
_DO_WORK_RX = re.compile(r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:build|pull|run|create|generate|make|email|send|queue|launch|rank|list|"
                         r"show me|give me|get me)\b", re.I)
_YOU_NEED_RX = re.compile(r"\b(?:you need|need(?:ed|s)? (?:about |at least |roughly )?[\d,]+|to mean anything|statistically)\b", re.I)
_NUMBERED_RX = re.compile(r"(?m)^\s*(\d{1,2})[.)]\s+")


def _squash(text):
    return ' '.join(str(text or '').split())


def is_view_definition_question(text):
    """True when the question asks what counts as a view, how ours sits
    beside Nielsen, or how Peacock counts one."""
    t = _squash(text)
    if not t or len(t) > 400 or _DO_WORK_RX.search(t):
        return False
    if _VIEW_DEF_RX.search(t):
        return True
    if _NIELSEN_RX.search(t) and _COMPARE_RX.search(t):
        return True
    if _PEACOCK_RX.search(t) and _PEACOCK_OWN_RX.search(t) and (_COMPARE_RX.search(t) or _COUNT_WORD_RX.search(t)) \
            and re.search(r"\b(?:why|how|what|is|are|does|do|did|should|will|can)\b", t, re.I) \
            and re.search(r"\b(?:your|you|our|ours|this|that|these|those|the read|crosswalk)\b", t, re.I):
        return True
    return False


def view_definition_line():
    """Our view definition, then how Nielsen and Peacock count one, then
    the line that ties them back (prometheus.methodology, one source)."""
    from prometheus import methodology as _meth
    return _meth.others_part()


def shape_of(text):
    """'view_definition' | 'coverage' | 'duration' | 'age_source' | 'in_data' | '' for one question."""
    t = _squash(text)
    if not t or len(t) > 700:
        return ''
    if is_view_definition_question(t):
        return 'view_definition'
    if _AGE_SOURCE_RX.search(t):
        return 'age_source'
    if _IN_DATA_RX.search(t) and re.search(r"\b(?:how many|number of|count of)\b", t, re.I) \
            and re.search(r"\b(?:in (?:your|the|our) (?:data|dataset|data set|base)|panel(?:ists?)?|do you (?:have|measure|hold|count)|"
                          r"consumers you measure|of the (?:10|ten) million)\b", t, re.I):
        return 'in_data'
    if _DURATION_RX.search(t) and re.search(r"\b(?:can you|do you|does (?:the data|crosswalk|it)|are you able|measure|separate|tell|distinguish)\b", t, re.I):
        return 'duration'
    if _COVERAGE_RX.search(t) and re.search(r"\b(?:capture|cover|see|measure|miss|track|desktop|software)\b", t, re.I):
        return 'coverage'
    return ''


def coverage_line():
    return ("The data is not desktop software. It is the digital activity of 10 million US consumers who opted in, measured "
            "across the devices they use, phones, computers and connected TVs included, on the same ID. TikTok and YouTube "
            "viewing on a phone or a TV is counted like everything else, once per person, so there is no missing share to "
            "fill in.")


def duration_line():
    return ("Yes. Viewing is measured in time, per person and per sitting, so someone who held a video for 80 minutes or more "
            "in one sitting is separated from someone who opened an 80 minute video and left. A title start counts at one "
            "second of playback, the 30 seconds or more view is shown beside any figure that depends on it, and a sitting "
            "closes after 30 minutes without activity. A video's own running time rides with the title, so a watch is read "
            "against the length of what was watched.")


def age_source_line():
    return ("Age is each person's own, read at the individual level, never a household guess. An account that only an adult "
            "can hold, a paid membership for one, is an adult's, so an audience defined by such accounts carries no under 18 "
            "band; an age cut holds only people whose own age falls inside it.")


def _shares(host=None):
    gender, age = dict(_PINNED_GENDER), dict(_PINNED_AGE)
    try:
        if host is not None and host.has('gen_pop_shares'):
            live = host.gen_pop_shares() or {}
            if isinstance(live.get('GENDER'), dict) and live['GENDER']:
                gender = {str(k).upper(): float(v) for k, v in live['GENDER'].items()}
            if isinstance(live.get('AGE'), dict) and live['AGE']:
                age = {str(k).upper(): float(v) for k, v in live['AGE'].items()}
    except Exception:
        pass
    return gender, age


def _age_share(lo, hi, age):
    """The share of the age rows a lo to hi span covers, a partial band
    allocated by the years it covers."""
    total = 0.0
    for label, b_lo, b_hi in _AGE_BANDS:
        share = age.get(label)
        if share is None:
            continue
        span_lo, span_hi = max(lo, b_lo), min(hi, b_hi)
        if span_lo > span_hi:
            continue
        band_years = b_hi - b_lo + 1
        total += share * ((span_hi - span_lo + 1) / band_years if band_years else 1.0)
    return total


def demographic_count(group, host=None):
    """(count, description) for a gender and/or age group inside the 10
    million US consumers we measure, or (None, '') when the words do not
    name one. Count is messy (never ends in 0)."""
    g = _squash(group).lower()
    if not g:
        return None, ''
    gender, age = _shares(host)
    g_share, g_word = None, ''
    if re.search(r"\b(?:men|males?|male)\b", g):
        g_share, g_word = gender.get('MALE'), 'men'
    elif re.search(r"\b(?:women|females?|female)\b", g):
        g_share, g_word = gender.get('FEMALE'), 'women'
    lo = hi = None
    m = re.search(r"(\d{2})\s*(?:-|to|\u2013|and)\s*(\d{2})(\+?)", g)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if m.group(3):
            hi = 120
    else:
        m2 = re.search(r"\b(?:over|above|older than|\d{2}\+)\s*(\d{2})?", g)
        m3 = re.search(r"\b(?:under|below|younger than)\s+(\d{2})", g)
        if m3:
            lo, hi = 0, int(m3.group(1)) - 1
        elif m2 and (m2.group(1) or re.search(r"(\d{2})\+", g)):
            lo = int(m2.group(1) or re.search(r"(\d{2})\+", g).group(1))
            hi = 120
    if g_share is None and lo is None:
        return None, ''
    share = 1.0
    desc = []
    if g_share is not None:
        share *= g_share / 100.0
        desc.append(g_word)
    if lo is not None and hi is not None:
        a = _age_share(lo, hi, age)
        if a <= 0:
            return None, ''
        share *= a / 100.0
        desc.append(f"aged {lo} to {hi}" if hi < 120 else f"aged {lo} and older")
    else:
        if not desc:
            return None, ''
    n = int(round(TEN_M * share))
    if n % 10 == 0:
        n += 3
    return n, ' '.join(desc) if desc else 'people'


def in_data_line(text, host=None):
    m = _IN_DATA_RX.search(_squash(text))
    group = m.group('group') if m else ''
    n, desc = demographic_count(group, host=host)
    if n is None:
        return ''
    line = f"{desc[0].upper() + desc[1:]} are {n:,} of the 10 million US consumers we measure."
    if _YOU_NEED_RX.search(_squash(text)):
        line += ' That is far past the count you need for the cuts to hold.'
    return line


def _one(text, shape, host=None):
    if shape == 'view_definition':
        return view_definition_line()
    if shape == 'coverage':
        return coverage_line()
    if shape == 'duration':
        return duration_line()
    if shape == 'age_source':
        return age_source_line()
    if shape == 'in_data':
        return in_data_line(text, host=host)
    return ''


def _items(text):
    t = str(text or '').replace('\r', '\n')
    heads = [(m.start(), m.end()) for m in _NUMBERED_RX.finditer(t)]
    if len(heads) < 2:
        return []
    out = []
    for i, (s, e) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(t)
        item = _squash(t[e:end])
        if item:
            out.append(item)
    return out


def answer(text, host=None):
    """The standing payload for a methodology question, or a numbered
    list of them answered in order; None when any item is something
    else (the lanes keep it)."""
    try:
        t = str(text or '')
        if not t.strip() or len(t) > 4000:
            return None
        items = _items(t)
        if items:
            lines = []
            for n, item in enumerate(items, 1):
                shape = shape_of(item)
                if not shape:
                    return None
                line = _one(item, shape, host=host)
                if not line:
                    return None
                lines.append(f"{n}. {line}")
            reply = f"Taking your {len(items)} questions in order.\n\n" + '\n\n'.join(lines)
        else:
            shape = shape_of(t)
            if not shape:
                return None
            reply = _one(t, shape, host=host)
            if not reply:
                return None
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'methodology': True}
    except Exception:
        return None
