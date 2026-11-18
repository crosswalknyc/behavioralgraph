"""A question about a word on the brief is answered, never drafted
(Grace Yin, Amazon, 2026-11-18 01:24 UTC).

With a brief on her screen ("... Estimated audience: about 493,695 to
670,093 US people (0.174% incidence) / Avid tier: ~3,837 / Cost:
Included with your plan ...") she typed

    "wjat does Avid tier: ~3,837 mean"

and got a third brief. The Reader had it right (a definition question,
0.93); the widget was sitting in its build flow, the first word was
mistyped so the question check did not see a question, and the build
intake drafted again from the thread.

The rule: a question about what a term or a line on the brief means
("what does X mean", "what is the avid tier", "what does ~3,837 refer
to", typos included) is answered in plain words from the thread's own
brief, with the number the reader quoted read in its place on that
brief. It never drafts or re-drafts a brief, never quotes, never costs,
and nothing is started.

The wording of each term is the product's own: the glossary in
prometheus.kpi_definitions (Avid Fan, Total Universe, projected US
audience, audience size) and the viewers wording in
prometheus.platform_cuts ("The Avid tier is the people who watched it
four or more times"). Nothing here says how a number is produced.

  brief_in_thread(history, number=None)  the brief card the question is
                                         about, parsed into its lines
  term_of(text, brief)                   which line the question means
  is_term_question(text)                 the question shape, typos and all
  answer(text, history)                  {'reply', 'followups', 'term',
                                         'subject'} or None

Pure text plus the thread's own turns; no model, no S3, no Flask. Never
raises. Wired in prometheus.service (lane: brief_terms) and, for any
path that reaches the build intake, prometheus.followup_refine
(at_interpret). Regression:
scripts/test_pm_grace_thread_leftovers_2026_11_18.py.
"""
from __future__ import annotations

import re

__all__ = ['brief_in_thread', 'parse_brief', 'is_term_question', 'term_of', 'answer', 'TERMS']

# the card the build flow draws (templates/index.html): a lead, then labeled lines
_LEAD_RX = re.compile(
    r"^(?:New\s+(?P<name>.+?)\s+profile(?:\s+plus\s+your\s+cut)?,\s+ready\s+to\s+run\.|"
    r"New\s+cut\s+of\s+the\s+existing\s+(?P<parent>.+?)\s+data,\s+ready\s+to\s+run\.|"
    r"(?P<refresh>.+?)\s+refresh,\s+ready\s+to\s+run\.)", re.I | re.M)
_LINE_RX = re.compile(r"^(?P<label>Subject|Cut|Window|Estimated audience|Avid tier|Markets|Cost|Build time):\s*(?P<value>.+?)\s*$",
                      re.I | re.M)
_NUM_RX = re.compile(r"(?<![\w.])~?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%?)")

TERMS = {
    'avid': ('avid tier', 'avid tiers', 'avid fan', 'avid fans', 'avid cut', 'avid audience', 'avid', 'the tier', 'tier'),
    'audience': ('estimated audience', 'estimated us audience', 'audience estimate', 'audience size', 'the audience line',
                 'us people', 'the estimate'),
    'total_universe': ('total universe', 'the total universe', 'tu'),
    'incidence': ('incidence', 'incidence rate', '% incidence', 'percent incidence'),
    'window': ('window', 'the window', 'date window', 'date range'),
    'cost': ('included with your plan', 'included with my plan', 'included', 'cost', 'the cost'),
    'build_time': ('build time', 'the build time'),
    'subject': ('subject', 'the subject line'),
}
_LABEL_KEY = {'avid tier': 'avid', 'estimated audience': 'audience', 'window': 'window', 'cost': 'cost',
              'build time': 'build_time', 'subject': 'subject'}

# "what", as people type it in a hurry
_WHAT = r"(?:w+h?[a-z]?a+t+|w[a-z]at|wh[a-z]t|waht|whta|wht|wat|what)(?:'?s)?"
_Q_RXS = (
    # what does X mean / refer to / stand for / represent
    re.compile(r"^\W*(?:(?:and|so|ok(?:ay)?|sorry|wait|um|hey|hi|also|but)[,\s]+)*" + _WHAT + r"\s+(?:does|do|did|dose|doe)\s+"
               r"(?:the\s+|that\s+|this\s+)?(?P<term>.+?)\s+(?:mean|means|meant|refer\s+to|refers\s+to|stand\s+for|stands\s+for|"
               r"represent|represents|signify|indicate|indicates|tell\s+(?:me|us))\b", re.I),
    # what is / what's (the) X [on the brief]
    re.compile(r"^\W*(?:(?:and|so|ok(?:ay)?|sorry|wait|um|hey|hi|also|but)[,\s]+)*" + _WHAT + r"\s*(?:is|are|was|s)?\s+"
               r"(?:the\s+|that\s+|this\s+|an?\s+)?(?P<term>.+?)\s*(?:on\s+(?:the|this|that|my)\s+brief|in\s+the\s+brief|here|above|"
               r"number|figure|line|again)?\s*[?.!]*$", re.I),
    # meaning of X / define X / explain X / what do you mean by X / not sure what X means
    re.compile(r"^\W*(?:(?:please|can\s+you|could\s+you|pls)\s+)?(?:define|explain|clarify|meaning\s+of|the\s+meaning\s+of|"
               + _WHAT + r"\s+do\s+you\s+mean\s+by)\s+(?:what\s+)?(?:the\s+|that\s+|this\s+)?(?P<term>.+?)\s*"
               r"(?:means|is|refers\s+to)?\s*[?.!]*$", re.I),
    re.compile(r"^\W*(?:i\s+am\s+|i'?m\s+)?(?:not\s+sure|unsure|confused|unclear)\s+(?:about\s+)?(?:what\s+)?(?:the\s+|that\s+|this\s+)?"
               r"(?P<term>.+?)\s+(?:means|is|refers\s+to)\s*[?.!]*$", re.I),
    # "avid tier means?", "avid tier: ~3,837 meaning?"
    re.compile(r"^\W*(?:the\s+|that\s+|this\s+)?(?P<term>.+?)\s+(?:means|meaning|mean)\s*\?+\s*$", re.I),
)
_NOT_A_TERM_RX = re.compile(r"\b(?:cost\s+to|how\s+much|price\s+of|can\s+you|could\s+you|please\s+(?:add|remove|change|run|build))\b", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def _fold(s):
    return ' '.join(re.sub(r"[^a-z0-9%+ ]+", ' ', str(s or '').lower()).split())


def _numbers(s):
    """The figures in a piece of text, as digit strings without commas ('3837', '0.174')."""
    out = []
    for m in _NUM_RX.finditer(str(s or '')):
        n = m.group(1).replace(',', '')
        if n not in out and not re.fullmatch(r"(?:19|20)\d{2}", n):
            out.append(n)
    return out


def parse_brief(text):
    """The brief card in an agent turn, as {'name', 'lines': {key: value},
    'numbers': {digits: key}}; None when the turn is not a brief."""
    try:
        t = str(text or '')
        lead = _LEAD_RX.search(t)
        if not lead:
            return None
        lines, numbers = {}, {}
        for m in _LINE_RX.finditer(t):
            key = _LABEL_KEY.get(m.group('label').lower())
            if not key or key in lines:
                continue
            val = _squash(m.group('value'))
            lines[key] = val
            for n in _numbers(val):
                numbers.setdefault(n, 'incidence' if (key == 'audience' and re.search(re.escape(n) + r"\s*%\s*incidence", val)) else key)
        if not lines:
            return None
        name = _squash(lead.group('name') or lead.group('parent') or lead.group('refresh') or '')
        return {'name': name, 'lines': lines, 'numbers': numbers}
    except Exception:
        return None


def brief_in_thread(history, number=None, max_turns=14):
    """The brief the question is about: the most recent brief in the
    thread that carries ``number`` when one is quoted, else the most
    recent brief. None when the thread holds none."""
    try:
        latest = None
        for t in reversed([x for x in (history or []) if isinstance(x, dict)][-max_turns:]):
            if str(t.get('role') or '').lower() == 'user':
                continue
            b = parse_brief(t.get('text') or t.get('content'))
            if not b:
                continue
            latest = latest or b
            if number and number in b['numbers']:
                return b
        return latest
    except Exception:
        return None


def _question_term(text):
    t = _squash(text)
    if not t or len(t) > 200 or t.count('?') > 2 or _NOT_A_TERM_RX.search(t):
        return None
    for rx in _Q_RXS:
        m = rx.match(t)
        if m:
            term = _squash(m.group('term')).strip(' :;,.?!"\'')
            if term and len(term.split()) <= 9:
                return term
    return None


def is_term_question(text):
    """True for the shape of a what-does-this-mean question, typos and
    all. Whether it is about the brief is ``term_of``'s to say."""
    return _question_term(text) is not None


def term_of(text, brief):
    """(key, number) the question means on this brief: the line the
    quoted number sits on, else the line the words name. (None, None)
    when the question is about nothing the brief carries."""
    try:
        term = _question_term(text)
        if not term or not isinstance(brief, dict):
            return None, None
        quoted = [n for n in _numbers(term) if n in brief['numbers']]
        ft = ' ' + _fold(re.sub(r"~?\s*\d[\d,.]*\s*%?", ' ', term)) + ' '
        named = None
        best = 0
        for key, aliases in TERMS.items():
            for a in aliases:
                fa = _fold(a)
                if fa and (' ' + fa + ' ') in ft and len(fa) > best:      # a line this brief does not carry is still explained
                    named, best = key, len(fa)
        if quoted:
            on_line = brief['numbers'][quoted[0]]
            # the words win when they name a line and the number sits on it or beside it ("avid tier: ~3,837")
            return (named or on_line), quoted[0]
        if named:
            left = [w for w in ft.split() if w not in _fold(' '.join(TERMS[named])).split()
                    and w not in ('the', 'a', 'an', 'on', 'my', 'this', 'that', 'brief', 'line', 'number', 'figure', 'of', 'in', 'here')]
            if len(left) <= 1:
                return named, None
        return None, None
    except Exception:
        return None, None


def _glossary(entry_id):
    try:
        from . import kpi_definitions as _kd
        for d in _kd.DEFINITIONS:
            if d.get('id') == entry_id:
                return str(d.get('definition') or '')
    except Exception:
        pass
    return ''


def _viewers_brief(brief):
    blob = f"{brief.get('name') or ''} {brief['lines'].get('subject') or ''}"
    m = re.search(r"\b(?:viewers|watchers|streamers)\b(?:\s+(?:only\s+)?on\s+(?P<svc>[A-Z][\w+&' ]+?))?(?:\s+only)?\s*(?:$|[;,.])", blob, re.I)
    if not m:
        return False, ''
    svc = _squash(m.group('svc') or '')
    if not svc:
        m2 = re.search(r"\bviewers\s+on\s+(?P<svc>[A-Z][\w+&' ]+?)(?:\s+only)?\s*(?:$|[;,.])", blob)
        svc = _squash(m2.group('svc')) if m2 else ''
    return True, svc


def _pretty(n):
    try:
        return f"{int(n):,}" if '.' not in n else n
    except Exception:
        return str(n)


def _for_what(brief):
    name = _squash(brief.get('name') or '')
    win = _squash(brief['lines'].get('window') or '')
    if name and win:
        return f"{name}, {win}"
    return name or win or 'this brief'


def _avid(brief, number):
    viewers, svc = _viewers_brief(brief)
    if viewers:
        # the viewers wording the product already uses (prometheus.platform_cuts)
        first = "The Avid tier is the people who watched it four or more times" + (f", on {svc}." if svc else ".")
    else:
        first = _glossary('avid_fan') or "The Avid tier is the most engaged part of the audience."
    n = number or next((k for k, v in brief['numbers'].items() if v == 'avid'), '')
    parts = [first]
    if not n and 'avid' not in brief['lines']:
        # the reader took it off, or the brief never had one: say so, and do not read a figure that is not there
        return first + " The brief in front of you does not carry one; it covers the full audience only."
    if n:
        parts.append(f"The ~{_pretty(n)} on your brief is how many of those people are in our data for {_for_what(brief)}.")
    aud = _squash(brief['lines'].get('audience') or '')
    if aud:
        aud = re.sub(r"^about\s+", '', re.sub(r"\s*\([^)]*incidence[^)]*\)", '', aud), flags=re.I)
        parts.append(f"The audience line above it ({aud}) is the full audience as a national count; "
                     f"the Avid tier sits inside it, not on top of it.")
    return ' '.join(parts)


def _audience(brief, number):
    aud = _squash(brief['lines'].get('audience') or '')
    first = _glossary('projection') or "The audience line is how many people across the US this audience reads as."
    parts = [first]
    if aud:
        parts.append(f"Your brief reads: {aud}, for {_for_what(brief)}.")
    return ' '.join(parts)


def _total_universe(brief, number):
    viewers, svc = _viewers_brief(brief)
    aud = _squash(brief['lines'].get('audience') or '')
    if viewers:
        first = "The Total Universe is everyone who watched it" + (f" on {svc}" if svc else '') + " in the window."
    else:
        first = _glossary('engager') or "The Total Universe is everyone in the audience."
    return first + (f" On your brief that is the audience line: {aud}." if aud else '')


def _incidence(brief, number):
    aud = _squash(brief['lines'].get('audience') or '')
    m = re.search(r"([\d.]+)\s*%\s*incidence", aud)
    pct = number if (number and '.' in str(number)) else (m.group(1) if m else '')
    first = "Incidence is the share of all US people who are in this audience."
    if pct:
        return f"{first} The {pct}% on your brief is that share for {_for_what(brief)}."
    return first


def _window(brief, number):
    win = _squash(brief['lines'].get('window') or '')
    return "The window is the dates the profile covers." + (f" Yours is {win}." if win else '')


def _cost(brief, number):
    cost = _squash(brief['lines'].get('cost') or '')
    if re.search(r"included with your plan", cost, re.I):
        return "Included with your plan means your plan covers this build. Approving it adds no charge."
    return "Cost is what approving the brief charges." + (f" Yours reads: {cost}." if cost else '')


def _build_time(brief, number):
    bt = _squash(brief['lines'].get('build_time') or '')
    return "Build time is about how long the profile takes to finish once you approve it." + (f" Yours reads {bt}." if bt else '')


def _subject(brief, number):
    sub = _squash(brief['lines'].get('subject') or '')
    return "The Subject line says who the profile is about." + (f" Yours reads: {sub}." if sub else '')


_COMPOSE = {'avid': _avid, 'audience': _audience, 'total_universe': _total_universe, 'incidence': _incidence, 'window': _window, 'cost': _cost,
            'build_time': _build_time, 'subject': _subject}
_CLOSE = "Your brief is unchanged and nothing has started."


def answer(text, history):
    """The plain answer to a question about a term or a line on the
    thread's own brief, or None when the turn is not one (no brief in
    the thread, another kind of question, an order, an adjustment).
    Never a draft, never a quote, never a job."""
    try:
        term = _question_term(text)
        if not term:
            return None
        quoted = _numbers(term)
        # the brief in front of the reader answers; an earlier one only when the number they quote sits on it
        latest = brief_in_thread(history)
        if not latest:
            return None
        brief = latest
        if quoted and quoted[0] not in latest['numbers']:
            brief = brief_in_thread(history, number=quoted[0]) or latest
        key, number = term_of(text, brief)
        if not key or key not in _COMPOSE:
            return None
        body = _COMPOSE[key](brief, number)
        if not body:
            return None
        return {'reply': f"{body}\n\n{_CLOSE}", 'followups': [], 'term': key, 'number': number or '',
                'subject': _squash(brief.get('name') or '')}
    except Exception:
        return None
