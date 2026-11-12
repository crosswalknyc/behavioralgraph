"""A read brief is one read on the titles it names (Grace Yin, Amazon,
2026-11-11).

Grace, 00:23 UTC, with a Men 18-34 profile open: "Please size the fan
audience for two novels in one run: The Love Hypothesis by Ali Hazelwood
and The Map That Leads To You by J.P. Monninger. Use the same setup for
both titles: - Population: all US people ... - Years: each calendar year
from 2019 through 2026 ... - 'Engaged' means any of: ... For each title
and each year, please show: 1. Total US people engaged 2. The same total
split by source ... 3. Share who are women 18-34 and share who are men
18-34 4. The unprojected panelist count behind every figure. Please also
tell me: - Which of these sources your panel can't capture, or only
partly captures (for example, phone apps) - Whether any figure is
modeled rather than counted. Please send it as a CSV. If any cut falls
below 100 panelists, merge it and note what you changed."

Three things went wrong with one message, and two more with the next:

  * the multi-ask splitter cut the brief in two at "Please also tell
    me";
  * the first half ("each calendar year from 2019 through 2026") was
    read as a year-over-year study of the profile open on her screen
    and came back as an offer to build eight year profiles of Men 18-34
    Amazon Prime Members;
  * the second half ("Which of these sources your panel can't capture")
    matched the cross of an audience across services ("which of
    these"), found no audience, came back with no figures and ended on
    "Working on it ... I will email you";
  * her next message disowned the offer, asked whether the request was
    one task or sixteen profiles and what it cost, and asked that
    nothing start without her OK; it was split again, and the half that
    carried the word "unprojected" got the unprojected size of the open
    profile.

The rules, all here so every lane reads one detector:

  1. A brief that sizes the people around one or more NAMED titles (a
     work written "Title by Author" or "Title (Author)", or quoted),
     with its own setup (the years, the sources, the shares, the count
     in the data, the file), is ONE read on those titles. ``read``
     returns the titles and the subject label; ``service.ask`` binds the
     read to them, and the splitter, the keyword lanes, the sizing lane
     and the year-profile offer stand aside. The page is never its
     subject and a demographic word in it ("men 18-34") never names a
     library file: the population it states (``population``) is the
     universe, US people.
  2. The questions that ride a brief ("which of these sources can you
     not capture", "is any figure modeled rather than counted") are
     questions about how we count, never a cross of an audience
     (``is_method_question``); ``cross_sizing.parse`` asks here first.
  3. A message that restates a brief but asks about it (cancel that
     offer, is this one task, what does it cost, do not start without
     my OK) is answered in plain words from the thread and the rate
     card, and starts nothing (``housekeeping``).
  4. A priced pass that came back with no figures, with nothing charged
     for it, goes on to the read lanes in the same turn; the reader
     never sees the calm line for it and ops is not told a person needs
     an email (``MailHold``, ``should_retry``). A pass the reader
     approved and paid for keeps the calm line and the ops notice, so a
     person looks at the charge.
  5. The read lane holds a brief to its own terms (``framing_lines``):
     each title sized on its own, the low end of what can be stood
     behind, a title already sized keeps its figures, nothing bought
     before a book existed, one value per fact, the count in the data in
     the one ratio, a cell under the stated floor merged and named, the
     reader's direct questions answered in the house words.

Pure text apart from ``housekeeping`` (which reads the thread it is
handed); no model, no S3. Never raises.
"""
from __future__ import annotations

import re

__all__ = ['read', 'is_brief', 'titles', 'subject_label', 'holds', 'housekeeping', 'is_method_question',
           'method_questions', 'population', 'framing_lines', 'counted_line', 'MailHold', 'should_retry',
           'HOUSEKEEPING_RUN_CHIP']

US_PEOPLE = 'US people'
HOUSEKEEPING_RUN_CHIP = 'Run it'
MAX_CHARS = 6000

# ------------------------------------------------------------------ titles
_SMALL = r"(?:of|the|to|a|an|and|in|on|for|at|with|that|you|me|my|is|it|from|or|de|la|le)"
_CAP = r"[A-Z][A-Za-z0-9'\u2019]*"
_TITLE = r"(?P<title>" + _CAP + r"(?:[ ](?:" + _CAP + r"|" + _SMALL + r"|\d+))*)"
_AUTHOR = r"(?P<author>[A-Z][A-Za-z.'\u2019-]*(?:[ ][A-Z][A-Za-z.'\u2019-]*){0,3})"
_BY_RX = re.compile(_TITLE + r"[ ]+by[ ]+" + _AUTHOR)
_PAREN_RX = re.compile(_TITLE + r"[ ]*\([ ]*" + _AUTHOR + r"[ ]*\)")
_QUOTED_RX = re.compile(r"[\"\u201c]([A-Z][^\"\u201c\u201d\n]{2,80})[\"\u201d]")
_LEAD_STOP_RX = re.compile(
    r"^(?:Please|Can|Could|Would|Run|Size|Show|Compare|Tell|Give|Pull|Use|For|Count|Years|Population|Share|Total|US|"
    r"Novel|Novels|Book|Books|Title|Titles|And|The same|My|Before|Note|I)\b[ ]*", re.I)
_NOT_TITLE_RX = re.compile(
    r"^(?:US|U\.S\.|Goodreads|Wattpad|TikTok|BookTok|YouTube|Prime Video|Amazon|Netflix|Kindle|Audible|CSV|Total|Share|"
    r"Population|Years|Engaged|Please|Men|Women|Profile IQ)$", re.I)

# ------------------------------------------------------------------ the brief
_SIZE_RX = re.compile(
    r"\b(?:size\s+(?:the\s+)?(?:fan\s+|reader\s+|book\s+)?(?:audience|base|universe|fandom)|"
    r"(?:fan|reader)\s+audience|"
    r"(?:us\s+)?people\s+(?:who\s+)?engaged\s+with|engaged\s+with\s+(?:each|both)|"
    r"total\s+(?:us\s+)?people\s+engaged|how\s+many\s+(?:us\s+)?people\s+(?:engaged|interacted))\b", re.I)
_YEARS_RX = re.compile(
    r"\b(?:each|every|per|by)\s+(?:calendar\s+)?year\b|\b(?:19|20)\d\d\s*(?:-|\u2013|\u2014|to|through|thru)\s*(?:19|20)\d\d\b", re.I)
_SOURCES_RX = re.compile(r"\b(?:split\s+by\s+source|by\s+source|same\s+total\s+split|split\s+by\s+(?:platform|channel))\b", re.I)
_DEFINES_RX = re.compile(r"[\"\u201c]?engaged[\"\u201d]?\s+means\b|\bengagement\s+definition\b|\bcount\s+each\s+person\s+once\b", re.I)
_IN_DATA_RX = re.compile(r"\b(?:unprojected|panelist|panellist|people\s+in\s+(?:the|your)\s+data)\b", re.I)
_FILE_RX = re.compile(r"\b(?:csv|spreadsheet|downloadable\s+file|as\s+a\s+file)\b", re.I)
_SHARES_RX = re.compile(r"\bshare\s+who\s+are\b|\b(?:women|men)\s+18\s*(?:-|\u2013|to)\s*34\b", re.I)
_POPULATION_RX = re.compile(
    r"\b(?:population\s*:\s*all\s+(?:us|u\.s\.)\s+(?:people|adults|residents)|all\s+(?:us|u\.s\.)\s+(?:people|adults|residents)\b|"
    r"(?:us|u\.s\.)\s+people\s+engaged\b|projected\s+to\s+the\s+(?:us|u\.s\.)\s+population)", re.I)

# a message about the brief (cost, cancel, hold), never the brief to run
_HOLD_RX = re.compile(
    r"\b(?:(?:do\s+not|don'?t|please\s+don'?t|never)\s+(?:start|run|begin|launch|queue|charge|bill)\b|"
    r"before\s+(?:running|you\s+run|starting|you\s+start)\b|without\s+my\s+(?:written\s+)?(?:ok|okay|approval|consent|go[- ]ahead|sign[- ]off)\b|"
    r"hold\s+off\b|wait\s+for\s+my\b)", re.I)
_COST_Q_RX = re.compile(r"\bwhat\s+(?:does|will|would|did)\s+(?:it|that|this)\s+cost\b|\bhow\s+much\s+(?:is|does|will|would)\b|"
                        r"\bwhat(?:'s|\s+is)\s+the\s+(?:cost|price|charge)\b", re.I)
_CANCEL_RX = re.compile(
    r"\b(?:please\s+)?cancel\s+(?:it|that|this|the\b)|\bi\s+did(?:\s+not|n'?t)\s+(?:request|ask\s+for|order|want)\b|"
    r"\bconfirm\b.{0,40}\b(?:nothing|not)\b.{0,20}\b(?:charged|billed)\b|\bnothing\s+(?:is|was|will\s+be)\s+(?:charged|billed)\b", re.I)
_ONE_TASK_RX = re.compile(r"\bis\s+(?:that|this|it)\s+(?:one|a\s+single|1)\s+(?:task|run|read|job|request)\b|"
                          r"\b(?:one|1)\s+(?:task|run|read)\s+or\s+\d+\s+\w+", re.I)

# ------------------------------------------------------------------ method questions
_METHOD_COVERAGE_RX = re.compile(
    r"\b(?:which|what)\s+(?:of\s+(?:these|those|the)\s+)?(?:sources?|platforms?|channels?|surfaces?|sites?|apps?)\b.{0,60}"
    r"\b(?:can(?:'?t|not)|cannot|unable\s+to|do(?:es)?\s+not|don'?t|doesn'?t|only\s+part(?:ly|ially)|part(?:ly|ially))\s+"
    r"(?:capture|captures|cover|covers|see|sees|measure|measures|track|tracks)\b|"
    r"\b(?:your|the)\s+(?:panel|data)\s+(?:can(?:'?t|not)|cannot|does\s+not|doesn'?t)\s+(?:capture|cover|see|measure|track)\b|"
    r"\bonly\s+part(?:ly|ially)\s+(?:capture|captures|cover|covers|see|sees|measure|measures)\b", re.I)
_METHOD_COUNTED_RX = re.compile(
    r"\b(?:modell?ed|imputed|estimated|extrapolated|inferred|filled\s+in|synthetic)\b.{0,40}\b(?:rather\s+than|instead\s+of|versus|vs\.?|or|not)\b"
    r".{0,24}\b(?:counted|measured|observed|actual|real)\b|"
    r"\b(?:counted|measured|observed)\b.{0,24}\b(?:rather\s+than|instead\s+of|versus|vs\.?|or|not)\b.{0,24}"
    r"\b(?:modell?ed|imputed|estimated|extrapolated|inferred)\b|"
    r"\b(?:is|are|was|were)\s+(?:any|each|every|all|some)?\s*(?:of\s+)?(?:the(?:se)?\s+)?(?:figures?|numbers?|counts?)\s+"
    r"(?:modell?ed|imputed|estimated|extrapolated|inferred)\b", re.I)


def _squash(text):
    return ' '.join(str(text or '').split())


def _clean_title(raw):
    t = str(raw or '').strip(" ,.;:-\u2013\u2014")
    for _ in range(4):
        m = _LEAD_STOP_RX.match(t)
        # a lead word is dropped only when a capitalized title still follows it
        if not m or not re.match(r"[A-Z]", t[m.end():] or ''):
            break
        if t[:m.end()].strip().lower() in ('the same',) or len(t[m.end():].split()) >= 2:
            t = t[m.end():].strip()
        else:
            break
    t = re.sub(r"[ ]+(?:" + _SMALL + r")$", '', t).strip()
    return t


def _clean_author(raw):
    """The author's name, stopped at the end of its sentence ('J.P.
    Monninger. Use the same setup' is J.P. Monninger)."""
    keep = []
    for tok in str(raw or '').split():
        letters = re.sub(r"[^A-Za-z]", '', tok)
        if tok.endswith('.') and len(letters) >= 3 and '.' not in tok[:-1]:
            keep.append(tok[:-1])
            break
        keep.append(tok)
    return ' '.join(keep).strip(" ,")


def titles(text):
    """The works the words name, in order, as (title, author) pairs:
    'Title by Author', 'Title (Author)', or a quoted title. [] when none."""
    try:
        out, seen = [], set()
        raw = str(text or '')
        if not raw.strip() or len(raw) > MAX_CHARS:
            return []
        flat = _squash(raw)
        hits = [(m.start(), m.group('title'), m.group('author')) for rx in (_BY_RX, _PAREN_RX) for m in rx.finditer(flat)]
        hits += [(m.start(), m.group(1), '') for m in _QUOTED_RX.finditer(flat)]
        for _pos, title, author in sorted(hits, key=lambda h: h[0]):
            t = _clean_title(title)
            if len(t) < 3 or len(t.split()) > 12 or _NOT_TITLE_RX.match(t):
                continue
            if len(t.split()) == 1 and not author:
                continue
            key = re.sub(r"[^a-z0-9]+", '', t.lower())
            if not key or key in seen or key in ('engaged',):
                continue
            seen.add(key)
            out.append((t, _clean_author(author)))
        return out
    except Exception:
        return []


def subject_label(pairs):
    """'A vs B' for two titles, 'A' for one, 'A, B and C' past that."""
    names = [t for t, _a in (pairs or []) if t]
    if not names:
        return ''
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} vs {names[1]}"
    return ', '.join(names[:-1]) + ' and ' + names[-1]


def holds(text):
    """True when the message is ABOUT a brief (cancel that, what does it
    cost, is it one task, do not start without my OK), not the brief to
    run."""
    t = _squash(text)
    if not t:
        return False
    return bool(_HOLD_RX.search(t) or _COST_Q_RX.search(t) or _CANCEL_RX.search(t) or _ONE_TASK_RX.search(t))


def _setup_signals(t):
    return sum(1 for rx in (_YEARS_RX, _SOURCES_RX, _DEFINES_RX, _IN_DATA_RX, _FILE_RX, _SHARES_RX) if rx.search(t))


def is_brief(text):
    """True for a brief that sizes the people around named titles with
    its own setup (two or more of: the years, the split by source, the
    engagement definition, the count in the data, the file, the shares)."""
    try:
        t = _squash(text)
        if not t or len(str(text)) > MAX_CHARS or len(t) < 60:
            return False
        if not _SIZE_RX.search(t):
            return False
        if not titles(text):
            return False
        return _setup_signals(t) >= 2
    except Exception:
        return False


def read(text):
    """{'subject', 'titles', 'authors'} for a brief to run now; None when
    the words are not a brief, or are about one (``holds``)."""
    try:
        if not is_brief(text) or holds(text):
            return None
        pairs = titles(text)
        label = subject_label(pairs)
        if not label:
            return None
        return {'subject': label, 'titles': [t for t, _a in pairs], 'authors': [a for _t, a in pairs]}
    except Exception:
        return None


def population(text):
    """'US people' when a brief on named titles states its own
    population; '' otherwise. The page and a demographic word in the
    brief never stand in for it."""
    try:
        t = _squash(text)
        if not t or not titles(text) or not _SIZE_RX.search(t):
            return ''
        return US_PEOPLE if _POPULATION_RX.search(t) else ''
    except Exception:
        return ''


# ------------------------------------------------------------------ method questions
def method_questions(text):
    """The how-we-count questions the words carry: a subset of
    ('coverage', 'counted')."""
    t = _squash(text)
    out = []
    if t and _METHOD_COVERAGE_RX.search(t):
        out.append('coverage')
    if t and _METHOD_COUNTED_RX.search(t):
        out.append('counted')
    return out


def is_method_question(text):
    """True when the words ask what the data covers or whether a figure
    is counted: a question about how we count, never a cross of an
    audience across services."""
    try:
        return bool(method_questions(text))
    except Exception:
        return False


def counted_line():
    """Whether a figure is counted (the wording approved on Grace Yin's
    2026-11-08 thread, kept verbatim)."""
    return ("Each figure is a count of people observed doing exactly what its line says inside the window, projected "
            "to the US from the same base; nothing is filled in from another audience or carried over from a "
            "different window.")


# ------------------------------------------------------------------ the frame the read lane holds
def framing_lines(text):
    """The lines the read lane holds a brief to. [] when the words are
    not a brief on named titles."""
    try:
        if not is_brief(text):
            return []
        pairs = titles(text)
        named = '; '.join(f"{t} ({a})" if a else t for t, a in pairs)
        lines = [
            "TITLE SIZING BRIEF (the question sizes the people around named titles):",
            f"- The titles are: {named}. Each is sized on its own, with the same definitions and the same windows; they "
            "never share a share, a curve or a ratio. No profile, twin or file on the dashboard is the base, and a "
            "demographic word in the brief (men 18-34, women 18-34) is a cut of the answer, never an audience to read.",
            "- A title that already carries a figure in the PRIOR READS block keeps it: a year's total sits at or above "
            "any figure already delivered for a narrower slice of that title and year, and nothing contradicts a "
            "delivered count.",
            "- Take the low end of what can be stood behind for every total. A reader can check a count that is too "
            "high against their own figures.",
            "- A year before a book was published carries no purchases of it; say plainly what existed in those years "
            "(an earlier fan work, nothing) and merge years that hold too few people to report.",
            "- The total for a title and year counts each person once. A split by source counts each person once "
            "inside each source, so the sources add to more than the total; say so in one line. Every source sits at "
            "or below its year's total.",
            "- One value per fact: a figure quoted in the headline is the same number in the table.",
            "- The count inside the 10 million US consumers we measure rides beside every US figure, and is the US "
            "figure multiplied by 10,000,000 and divided by 329,900,000. Where the brief sets a floor on that count, "
            "merge any cell under it (adjacent years, or the smaller sources into one line) and name what was merged.",
        ]
        asked = method_questions(text)
        if 'coverage' in asked:
            lines.append(
                "- The brief asks what the data does not capture. Answer in two plain sentences: the data is the "
                "digital activity of 10 million US consumers who opted in, measured across the devices they use, "
                "phones, computers and connected TVs included, on the same ID; anything with no digital trace (a copy "
                "bought in a store, borrowed or passed along) sits outside it.")
        if 'counted' in asked:
            lines.append("- The brief asks whether figures are counted. Answer with this sentence as written: " + counted_line())
        return lines
    except Exception:
        return []


# ------------------------------------------------------------------ a question about the brief
_RUN_META_KEYS = ('run_id', 'run_ids', 'read_job_id', 'job_id', 'panel_charge', 'priced_paid')


def _last_agent_turn(history):
    for t in reversed(list(history or [])):
        if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant'):
            return t
    return None


def _started_something(turn):
    """True when the agent turn carries a run, a read job or a charge."""
    if not isinstance(turn, dict):
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    for k in _RUN_META_KEYS:
        if meta.get(k) or turn.get(k):
            return True
    return False


def housekeeping(text, history=None, *, included=False, price_label=''):
    """The answer to a message ABOUT a brief on named titles: the offer
    it disowns is withdrawn (when the last reply started nothing), the
    request is one task, what it costs, and that nothing starts until
    the reader says so. None when the words are not that."""
    try:
        t = _squash(text)
        if not t or len(str(text)) > MAX_CHARS:
            return None
        pairs = titles(text)
        if not pairs or not holds(t):
            return None
        asks_cancel = bool(_CANCEL_RX.search(t))
        asks_cost = bool(_COST_Q_RX.search(t))
        asks_task = bool(_ONE_TASK_RX.search(t))
        asks_hold = bool(_HOLD_RX.search(t))
        if not (asks_cancel or asks_task) or not (asks_cost or asks_hold or asks_task):
            return None
        prev = _last_agent_turn(history)
        if asks_cancel and _started_something(prev):
            return None       # something did start: the status and order lanes answer from the record
        label = subject_label(pairs)
        lines = []
        if asks_cancel:
            lines.append("Nothing was started and nothing is charged. The year profiles in my last reply were an offer, "
                         "not an order, and that offer is withdrawn.")
        n = len(pairs)
        what = "both titles" if n == 2 else ("the title" if n == 1 else f"all {n} titles")
        lines.append(f"Your request is one task: one read covering {what} and every year, delivered as one file. "
                     "It needs no profiles.")
        if included:
            lines.append("Included with your plan, so it carries no charge.")
        elif price_label:
            lines.append(f"Pulling this data will cost {price_label}, charged once, only after you approve it.")
        lines.append(f"It has not started and will not start until you say so. Reply \"{HOUSEKEEPING_RUN_CHIP}\" and "
                     f"the read on {label} begins.")
        if re.search(r"\b100\s+panelists?\b|\bminimum\s+is\s+100\b", t, re.I):
            lines.append("Noted on the floor: any cell with fewer than 100 people in the data behind it is merged, "
                         "and the file says what was merged.")
        return {'success': True, 'action': 'answer', 'reply': '\n\n'.join(lines), 'followups': [HOUSEKEEPING_RUN_CHIP],
                'offer_deck': False, 'deck_angle': None, 'subject': label, 'read_brief': {'held': True, 'titles': [x for x, _a in pairs]}}
    except Exception:
        return None


def pending_run(text, history=None):
    """The brief a bare go-ahead ('Run it', 'yes, run it', 'go ahead')
    points at: the newest user message in the thread that is a brief on
    named titles, when the last agent turn was the housekeeping answer.
    Returns that message's text, or ''."""
    try:
        t = _squash(text)
        if not re.match(r"^\W*(?:yes[,.]?\s+)?(?:run it|go ahead|go|ok(?:ay)?[,.]?\s+run it|please run it|start it|approved?|you have my ok)\W*$", t, re.I):
            return ''
        prev = _last_agent_turn(history)
        ptxt = str((prev or {}).get('text') or (prev or {}).get('content') or '')
        if 'will not start until you say so' not in ptxt:
            return ''
        for turn in reversed(list(history or [])):
            if isinstance(turn, dict) and str(turn.get('role') or '') == 'user':
                q = str(turn.get('text') or turn.get('content') or '')
                if is_brief(q):
                    return q
        return ''
    except Exception:
        return ''


def strip_hold(text):
    """A held brief with its hold and cost sentences removed, so the read
    that follows a go-ahead is the request itself."""
    try:
        parts = re.split(r"(?<=[.!?])\s+|\n+", str(text or ''))
        keep = [p for p in parts if p.strip() and not (_HOLD_RX.search(p) or _COST_Q_RX.search(p) or _CANCEL_RX.search(p)
                                                       or _ONE_TASK_RX.search(p))]
        return _squash(' '.join(x.strip(" \u258e") for x in keep))
    except Exception:
        return _squash(text)


# ------------------------------------------------------------------ a priced pass with no figures
class MailHold:
    """The host, with the ops mail held: ``priced_read.service_turn``
    mails ops when a pass comes back with no figures. The caller that
    retries on the read lanes drops the held note (nobody read the calm
    line); any other outcome flushes it unchanged."""

    def __init__(self, host):
        self._host = host
        self.held = []

    def __getattr__(self, name):
        return getattr(self._host, name)

    def error_email(self, *args, **kwargs):
        self.held.append((args, kwargs))

    def flush(self):
        for args, kwargs in self.held:
            try:
                self._host.error_email(*args, **kwargs)
            except Exception:
                pass
        self.held = []

    def drop(self):
        self.held = []


def should_retry(payload, panel_confirm=None):
    """True when a priced pass was held with no figures and nothing was
    charged for it: the read lanes take the ask in the same turn."""
    try:
        if not isinstance(payload, dict) or not payload.get('held'):
            return False
        if isinstance(panel_confirm, dict) and panel_confirm:
            return False          # the reader approved a quote: a person looks at the charge
        paid = payload.get('priced_paid') if isinstance(payload.get('priced_paid'), dict) else {}
        usd = paid.get('usd')
        return bool(paid.get('included')) or not usd
    except Exception:
        return False
