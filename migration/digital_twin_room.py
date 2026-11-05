"""Focus Group IQ room engine: a question answered by a small multi-agent
session (2026-10-26, Jenna: "overall twins has to be really smart. it
needs to have each answer and response grounded in real research from
noted external sources, look at how things did in the past ... then it
needs to spin up agent personas for each of the twins to act on behalf
of those 'people' then with the full context of the question and who
they are supposed to be give an answer. so you have one agent acting as
a moderator and other agents acting as panelists and it all needs to be
extremely well reasoned ... and then it should all be put together in a
coherent output").

The failed case this replaces: the Gilmore Girls room was asked about a
$50 boxed set sold at Target and answered with template options ('I
will wait until it is free online'). The show ended in 2007, the
revival landed on Netflix in 2016, it streams on Hulu today and a
complete-series DVD exists, so the real question is whether these fans
buy a collectible: a keepsake, unseen material, a gift, or they already
own it.

THE AGENTS (one question = one job)
  1. Research agent (strong model, web search on approved sources):
     the subject's facts (status, years, where it streams, physical
     media and merch history), how comparable things did in the past,
     how devoted this audience is, and anything specific to the
     stimulus (price, exclusivity, extras). A compact facts brief, each
     fact tagged by source class; never fabricated; what could not be
     found is said so. Cached per subject + topic under
     system/digital_twins/research/<slug>/<topic-hash>.json; durable
     subject facts under system/digital_twins/facts/<slug>.json.
  2. Moderator agent, first pass (strong model): the coded options a
     real moderator would put on the board for THIS stimulus, 3 to 5,
     true to the facts (never an option the facts rule out), each with
     a lean (yes / maybe / no / other), plus the framing.
  3. Persona agents, one per seat (fast model, parallel threads): each
     seat carries a full persona built from the twin (name, age, city,
     segment, demographics, the traits as plain habits, lifecycle
     stage, relationship to the subject, media diet) and its own memory
     of earlier questions in this room; it answers in character in two
     or three sentences, picks one coded option (or other), gives a
     confidence 1 to 5 and the reason. Seats are stable for the session.
  4. Moderator agent, second pass (strong model): the synthesis. The
     distribution is arithmetic (each seat weighted by the people it
     stands for, so twelve answers become percentages and people counts
     that sum to the room, and by segment); the model writes the
     one-paragraph read grounded in the facts in plain words, picks the
     three or four most telling voices, suggests two probes and lists
     the facts used. The room's trait-based prior is a sanity bound:
     when the seats depart far from it the moderator reasons about why
     (flagged on the internal trail); it never overrides the room.

THE PUBLISHED SPLIT (the seats drive the shape, the prior fills the tails)
  Twelve seats make a coarse tally (0 / 1 / 8 / 3 of 12) and a bare 0.0% reads as 'nobody in America would'. The
  published share is the seat votes smoothed with the twin's trait-based prior: the deterministic engine's distribution
  over the moderator's options (``prior_over_options``: the yes / maybe / no masses of the old engine spread over the
  options by lean and keyword affinity; stars, A/B sides and supplied choices map one to one). Each seat's people carry
  its vote by its confidence (0.8 at 1, 1.0 at 5); the rest of its people plus one seat's worth of the room follow the
  prior (``ALPHA_SEATS``). Shares sum to 100.0 and people to the room, in each segment too (the room rows are the sum of
  the segments); no row prints 0.0% or a bare k/n multiple of the seats; people counts end the way real counts do. The
  raw tally stays on every option (``seats``, ``seats_n``, ``tally`` 'eight of twelve', ``room_pct``) so the console can
  show 'in the room: 8 of 12' beside the projected share, and the read says shares in plain words ('about two in three',
  'a sliver').

THE SEATS MIRROR THE COUNT (room-4, Jenna 2026-11-05: "why would it say 0 people would watch?")
  The published split (after the anchor) is the count. The seats are then allocated to it (``mirror_seats``: largest
  remainder per segment, and any side worth half a seat of the room, 4.2% with twelve, holds a seat); a seat that keeps
  its own answer keeps its line, a seat that moves argues its new stance in character (``revoice_seat``). The headline
  (``headline_for``: 'about one in twenty of the 6.09M viewers, roughly 329.1K people, are a yes: one of the twelve
  seats') takes its people from the share, never from the tally; '0 people' only when the count is zero. The read and
  the chat line pass ``cohere_read``: one split, stated once; a nobody / none / all twelve claim against a non-zero
  count is rewritten to the count and the seats.

OUTPUT SHAPE (the question record on session['questions']; the UI reads this)
  {id, q, kind, framing,
   options: [{id, label, lean, pct, people, seats: [seat ids], seats_n, tally: '8 of 12', room_pct}],   pct = published share
   option_labels: [str],                       the labels alone
   distribution: [{label, pct, people}],       the same rows for the table (older readers)
   tally: [{label, seats, of, room_pct}], seats_total,     the raw seat tally for the console (mirrors the count)
   by_segment: [{segment, people, distribution: [{label, pct, people}], yes_pct, tally: [{label, seats}]}],
   headline: str, headline_people: int,        the lead side's share in words and its people (share x the room's people)
   read: str,
   voices: [{seat, name, age, city, segment, line}],
   answers: [{seat, name, option, option_label, line, confidence, reason}],     the raw seat answers
   probes_next: [str], facts_used: [str],
   stage: researching | the room is thinking | moderator is reading | done | stopped,
   timings: {research_s, options_s, seats_s, read_s, total_s}          for the page's stage display (filled as stages finish)
   job_id, started_at, finished_at, asked_at, asked_by, follows, stimulus, topic, price, yes_pct,
   trail: {research_key, facts_key, research_cached, research_reused_from, models: {research, moderator, persona},
           tokens: {in, out, cache_read, cache_write}, searches, cost_usd, calls: [...], timings, prior: {...},
           prior_over_options: {...}, stages: [...], fallbacks: [...], facts_brief: {...}, persona_cards: {...}}}
  Client-facing strings (read, framing, voices, option labels, facts_used, probes_next, answer lines) pass the house
  scrub: no synthetic, modeled, estimated, research, panel, sample, confidence, pipeline, corpus, lane, no source names,
  no em or en dashes, individual-level nouns. The trail is internal and never rendered (the job document omits it).

THE JOB
  ask_room() appends the record at stage 'researching' and returns at once with job_id; the stages run on a background
  thread (the same way portraits do) with the job document system/digital_twins/jobs/<job_id>.json as the handoff
  (status, stage, the partial record with seat answers appended as they arrive, ready_message when done) and the record
  in the twin payload updated at each stage. Budget: 30 to 75 seconds a question with twelve seats.

MODELS (one place: MODELS below; env DTIQ_ROOM_RESEARCH_MODEL / DTIQ_ROOM_MODERATOR_MODEL / DTIQ_ROOM_PERSONA_MODEL)
  research and moderator: the project's analysis chain (Opus class, Sonnet fallback) through host.claude_data;
  personas: Sonnet class, then Haiku class, then the analysis chain, through claude_client.claude_reason_json.
  Kill switches: REGRESSION_TEST_MODE=1 never calls a model (the deterministic engine answers unless a test installs
  a fake model with set_model_fn); DTIQ_ROOM=0 restores the deterministic engine everywhere.
  Cost per question is logged on the trail (tokens in and out, dollars at list price) and per call in the usage log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import traceback
import uuid
from typing import Callable, Optional

from migration.digital_twin_engine import clamp, human_people, unit

ROOM_VERSION = 'room-4'   # room-2 (2026-11-04): the media desk and the result anchor; room-3 (2026-11-04): the read opens with the room, a short read for the chat, answers stamped as they land
#                           room-4 (2026-11-05): the seats mirror the count (headline, headline_people, answers[].mirrored, trail.mirror), one split stated once
S3_BUCKET = 'dashboard-inputs'
JOB_PREFIX = 'system/digital_twins/jobs/'
RESEARCH_PREFIX = 'system/digital_twins/research/'
FACTS_PREFIX = 'system/digital_twins/facts/'
STAGES = ('researching', 'the room is thinking', 'moderator is reading', 'done')
STAGE_LABEL = {'researching': 'Researching the question', 'the room is thinking': 'The room is thinking', 'moderator is reading': 'Prometheus is reading the room',
               'done': 'Done', 'stopped': 'Stopped'}

MODELS = {
    'research': (os.environ.get('DTIQ_ROOM_RESEARCH_MODEL') or '').strip() or None,     # None: the analysis chain (Opus class first)
    'moderator': (os.environ.get('DTIQ_ROOM_MODERATOR_MODEL') or '').strip() or None,
    'persona': [m for m in ((os.environ.get('DTIQ_ROOM_PERSONA_MODEL') or '').strip(), 'claude-sonnet-4-6', 'claude-haiku-5-5') if m],
    # the strong chain when the Prometheus host is not bound (a script, the box): the same candidates the analysis chain walks
    'strong': ['claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6'],
}
BUDGET_S = {'research': 75.0, 'media': 150.0, 'options': 40.0, 'persona': 50.0, 'mirror': 35.0, 'read': 50.0}   # the media desk's searches run longer than the facts desk's
MIRROR_FLOOR_SEATS = 0.5      # an option holding at least half a seat's worth of the audience (4.2% with twelve seats) holds a seat (Jenna 2026-11-05)
MAX_OPTIONS = 5
VOICES_N = 4
RESEARCH_CACHE_TTL_S = 14 * 86400
RESEARCH_MAX_SEARCHES = 4
# The media desk (Jenna 2026-11-04): what the audience has been seeing, hearing and
# saying, and how an event came out. Cached by the day; a brief from this morning
# is still this morning's, a brief from yesterday is not today's.
MEDIA_PREFIX = 'system/digital_twins/media/'
MEDIA_CACHE_TTL_S = 6 * 3600
MEDIA_MAX_SEARCHES = 8
ANCHOR_POLL_WEIGHT = 2.0      # a published poll outweighs the seats two to one before the event
ANCHOR_RESULT_JITTER = 0.6    # the published split sits on the count, a few tenths off it
_SOURCE_NAMES_RX = re.compile(r"\b(Nielsen|Circana|NPD(?: Group)?|Statista|eMarketer|Insider Intelligence|YouGov|Pew(?: Research(?: Center)?)?|Box Office Mojo|The Numbers|Sensor Tower|data\.ai|App Annie|Comscore|Luminate|Deadline|Variety|The Hollywood Reporter|Hollywood Reporter|Media Play News|Home Media Magazine|TVLine|Vulture|Tudum|SEC filings?|10-K|earnings (?:call|report)s?|per (?:the )?trade press|trade press)\b", re.I)
_BANNED_RX = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|estimate|research(?:ed)?|panel|panelists?|sample|confidence|pipeline|corpus|lane)\b", re.I)
_MODEL_FN: Optional[Callable] = None


# ------------------------------------------------------------- switches
def _flag(name, default='1'):
    return (os.environ.get(name) or default).strip().lower() not in ('0', 'false', 'no', 'off')


def room_enabled() -> bool:
    """DTIQ_ROOM=0 restores the deterministic engine."""
    return _flag('DTIQ_ROOM')


def regression_mode() -> bool:
    return (os.environ.get('REGRESSION_TEST_MODE') or '').strip().lower() in ('1', 'true', 'yes', 'on')


def set_model_fn(fn: Optional[Callable]):
    """Tests install a fake model: fn(role, system, user, **kw) -> dict.
    The dict may carry '_stats' {model, input_tokens, output_tokens}."""
    global _MODEL_FN
    _MODEL_FN = fn


def model_available(model_fn: Optional[Callable] = None) -> bool:
    """A model answers: an installed fake, or the project's client
    outside REGRESSION_TEST_MODE (which never calls a model)."""
    if model_fn is not None or _MODEL_FN is not None:
        return True
    if regression_mode():
        return False
    try:
        from prometheus.host import host
        if host.has('claude_data'):
            return True
    except Exception:
        pass
    try:
        from claude_client import get_claude_client
        return get_claude_client() is not None
    except Exception:
        return False


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", '_', str(s or '').lower()).strip('_')[:60] or 'subject'


def _parse_json(raw):
    if isinstance(raw, dict):
        return raw
    s = str(raw or '').strip()
    if not s:
        return None
    s = re.sub(r"^```(?:json)?\s*|\s*```$", '', s, flags=re.S)
    try:
        return json.loads(s)
    except Exception:
        pass
    a, b = s.find('{'), s.rfind('}')
    if a >= 0 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except Exception:
            return None
    return None


# ------------------------------------------------------------ the scrub
def scrub(s: str) -> str:
    """The house scrub plus the room's own: no source names, no banned
    words, no em or en dashes, individual-level nouns."""
    from migration.digital_twin_synthesis import scrub_text
    from migration.digital_twin_copy import strip_calibration_language
    t = strip_calibration_language(str(s or ''))   # no calibrated / anchored / as aired / ratings-service sentence reaches a reader (Jenna 2026-11-05)
    t = _SOURCE_NAMES_RX.sub('the record', t)
    t = re.sub(r"\b(according to|per) the record\b,?\s*", '', t, flags=re.I)
    # the room's own swaps first, so the grammar survives ('the panel' -> 'the room')
    for rx, rep in ((r"\bresearch(?:ed|ing)?\b", 'record'), (r"\bmodel(?:ed|led|ing|s)?\b", 'read'), (r"\bestimated\b", 'expected'), (r"\bestimates?\b", 'figures'),
                    (r"\bpanel(?:ists?|s)?\b", 'room'), (r"\bsamples?\b", 'group'), (r"\bconfidence\b", 'certainty'), (r"\bsynthetic(?:ally)?\b", 'twin-based'),
                    (r"\bsynth\b", 'twin'), (r"\bpipelines?\b", 'process'), (r"\bcorpus\b", 'library'), (r"\blanes?\b", 'path')):
        t = re.sub(rx, rep, t, flags=re.I)
    t = scrub_text(t)
    t = re.sub(r"\b(the|a|an) (the|a|an)\b", r"\1", t, flags=re.I)
    t = t.replace('\u2014', '-').replace('\u2013', '-')
    t = re.sub(r"\s{2,}", ' ', t).strip()
    return t


def has_banned(s: str) -> list:
    t = str(s or '')
    from migration.digital_twin_copy import calibration_hits
    hits = [m.group(0) for m in _BANNED_RX.finditer(t)] + [m.group(0) for m in _SOURCE_NAMES_RX.finditer(t)] + calibration_hits(t)
    if '\u2014' in t or '\u2013' in t:
        hits.append('dash')
    return hits


# ---------------------------------------------------------- model layer
class Models:
    """Every call goes through the project's client. ``call`` returns
    (data or None, call record) and keeps the cost trail."""

    def __init__(self, model_fn: Optional[Callable] = None, usage_extras: Optional[dict] = None):
        self.fn = model_fn or _MODEL_FN
        self.extras = usage_extras or None
        self.calls = []
        self.lock = threading.Lock()

    @staticmethod
    def _host_claude():
        try:
            from prometheus.host import host
            if host.has('claude_data'):
                return host.claude_data
        except Exception:
            pass
        return None

    @staticmethod
    def _tools(max_uses: int = RESEARCH_MAX_SEARCHES):
        """The web_search tool for the research and media desks, capped
        so a question stays inside the budget (the facts brief is cached
        per subject + topic, the media brief per day)."""
        try:
            import prometheus_analysis as _pma
            return [dict(_pma.WEB_SEARCH_TOOL, max_uses=int(max_uses))]
        except Exception:
            return None

    @staticmethod
    def _stats_now():
        try:
            from claude_client import last_call_stats
            return dict(last_call_stats() or {})
        except Exception:
            return {}

    def _log(self, role, model, stats, t0, ok):
        cost = 0.0
        try:
            from render_usage_log import cost_usd
            usage = {'input_tokens': int(stats.get('input_tokens') or 0), 'output_tokens': int(stats.get('output_tokens') or 0),
                     'cache_read_input_tokens': int(stats.get('cache_read_input_tokens') or 0), 'cache_creation_input_tokens': int(stats.get('cache_creation_input_tokens') or 0),
                     'server_tool_use': {'web_search_requests': int(stats.get('web_search_requests') or 0)}}
            if usage['input_tokens'] or usage['output_tokens']:
                cost = float(cost_usd(model or stats.get('model') or '', usage))
        except Exception:
            cost = 0.0
        rec = {'role': role, 'model': model or stats.get('model') or '', 'ok': bool(ok), 'duration_s': round(time.time() - t0, 2),
               'input_tokens': int(stats.get('input_tokens') or 0), 'output_tokens': int(stats.get('output_tokens') or 0),
               'cache_read': int(stats.get('cache_read_input_tokens') or 0), 'cache_write': int(stats.get('cache_creation_input_tokens') or 0),
               'searches': int(stats.get('web_search_requests') or 0), 'cost_usd': round(cost, 6)}
        with self.lock:
            self.calls.append(rec)
        return rec

    def call(self, role: str, system: str, user: str, *, max_tokens: int = 1200, temperature: float = 0.4, tools=None) -> tuple:
        t0 = time.time()
        if self.fn is not None:   # a test's fake
            try:
                got = self.fn(role, system, user, max_tokens=max_tokens, temperature=temperature, tools=tools)
            except Exception as e:
                print(f"[twin-room] fake model failed ({role}): {e}")
                got = None
            data = got if isinstance(got, dict) else _parse_json(got)
            stats = {}
            if isinstance(data, dict):
                data = dict(data)
                stats = dict(data.pop('_stats', None) or {})
            rec = self._log(role, stats.get('model') or 'fake', stats, t0, data is not None)
            return (data if isinstance(data, dict) else None), rec
        surface = f'twin_room_{role}'
        if regression_mode():   # REGRESSION_TEST_MODE never calls a model: a test without a fake lands on the fallbacks
            return None, self._log(role, 'off', {}, t0, False)
        if role == 'persona':
            data, model = self._chain(system, user, max_tokens, temperature, surface, MODELS['persona'])
            if data is not None:
                return data, self._log(role, model, self._stats_now(), t0, True)
        want = MODELS.get('research' if role == 'media' else role) if role in ('research', 'media', 'moderator') else None
        research_tools = tools if tools is not None else (self._tools(MEDIA_MAX_SEARCHES if role == 'media' else RESEARCH_MAX_SEARCHES) if role in ('research', 'media') else None)
        if want:
            data, model = self._chain(system, user, max_tokens, temperature, surface, [want] + [m for m in MODELS['strong'] if m != want], tools=research_tools)
            if data is not None:
                return data, self._log(role, model, self._stats_now(), t0, True)
        cj = self._host_claude()
        if cj is None:
            chain = MODELS['strong'] if role in ('research', 'media', 'moderator') else MODELS['persona']
            data, model = self._chain(system, user, max_tokens, temperature, surface, chain, tools=research_tools)
            return data, self._log(role, model, self._stats_now(), t0, data is not None)
        try:
            got = cj(system, user, max_tokens=max_tokens, temperature=temperature, surface=surface, usage_extras=self.extras,
                     tools=research_tools)
        except TypeError:
            got = cj(system, user)
        except Exception as e:
            print(f"[twin-room] model call failed ({role}): {str(e)[:200]}")
            got = None
        data = got if isinstance(got, dict) and got else None
        if data is None:
            # the host's client answered nothing (its reasoning switch off in a local shell, a refused
            # call): the named chain answers directly rather than the room falling back to defaults
            # (2026-11-04: a re-run from a shell without USE_CLAUDE_REASONING landed yes/maybe/no options)
            chain = MODELS['strong'] if role in ('research', 'media', 'moderator') else MODELS['persona']
            data, model = self._chain(system, user, max_tokens, temperature, surface, chain, tools=research_tools)
            if data is not None:
                return data, self._log(role, model, self._stats_now(), t0, True)
        stats = self._stats_now()
        return data, self._log(role, stats.get('model') or '', stats, t0, data is not None)

    def _chain(self, system, user, max_tokens, temperature, surface, chain, tools=None):
        """Named models through claude_reason_json; (data, model) or
        (None, '')."""
        try:
            from claude_client import claude_reason_json
        except Exception:
            return None, ''
        tag = (surface, 'chatbot', self.extras) if self.extras else (surface, 'chatbot')
        plans = [tools]
        if tools:
            # the current web_search tool type, then the legacy type, then none: research is additive and never blocks the room
            try:
                import prometheus_analysis as _pma
                plans.append([_pma.WEB_SEARCH_TOOL_LEGACY])
            except Exception:
                pass
            plans.append(None)
        for plan in plans:
            for m in chain:
                try:
                    raw = claude_reason_json(system=system, user=user, model=m, max_tokens=max_tokens, temperature=temperature, max_retries=2,
                                             raise_on_error=True, usage_tag=tag, tools=plan)
                except Exception as e:
                    print(f"[twin-room] {m} failed on {surface}: {str(e)[:160]}; trying next")
                    continue
                data = _parse_json(raw)
                if isinstance(data, dict) and data:
                    return data, m
        return None, ''

    def totals(self) -> dict:
        with self.lock:
            calls = list(self.calls)
        models = {}
        for c in calls:
            if c.get('ok') and c.get('model'):
                models.setdefault(c['role'], c['model'])
        return {'models': models, 'tokens': {'in': sum(c['input_tokens'] for c in calls), 'out': sum(c['output_tokens'] for c in calls),
                                             'cache_read': sum(c['cache_read'] for c in calls), 'cache_write': sum(c['cache_write'] for c in calls)},
                'searches': sum(c['searches'] for c in calls), 'cost_usd': round(sum(c['cost_usd'] for c in calls), 4), 'calls': calls}


# --------------------------------------------------------------- the s3
def _s3_default():
    try:
        from prometheus.host import host
        if host.has('s3_client'):
            return host.s3_client
    except Exception:
        pass
    try:
        import sys as _sys
        a = _sys.modules.get('app')
        if a is not None and getattr(a, 's3_client', None) is not None:
            return a.s3_client
    except Exception:
        pass
    return None


def _get_json(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _put_json(s3, key, doc):
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, default=str, ensure_ascii=False).encode('utf-8'), ContentType='application/json')
        return True
    except Exception as e:
        print(f"[twin-room] write failed {key}: {e}")
        return False


def job_key(job_id: str) -> str:
    return f"{JOB_PREFIX}{job_id}.json"


def job_read(s3, job_id: str) -> Optional[dict]:
    return _get_json(s3, job_key(job_id)) if s3 is not None else None


# ------------------------------------------------------------- the twin
def _meta(t):
    return (t or {}).get('meta') or {}


def _twin_name(t):
    return str(_meta(t).get('name') or _meta(t).get('subject') or 'the twin')


_TOPIC_STOP = {'would', 'you', 'the', 'a', 'an', 'for', 'if', 'at', 'to', 'of', 'in', 'on', 'it', 'is', 'do', 'does', 'did', 'and', 'or', 'your', 'be', 'that', 'this',
               'what', 'which', 'who', 'how', 'many', 'much', 'pay', 'buy', 'with', 'from', 'by', 'about', 'them', 'they', 'their', 'we', 'our', 'i', 'me', 'my',
               'sold', 'sell', 'were', 'was', 'are', 'am', 'will', 'can', 'could', 'should', 'make', 'get', 'take', 'go', 'not', 'no', 'yes', 'any', 'some', 'all',
               'show', 'hands', 'rate', 'scale', 'one', 'five', 'out', 'star', 'stars', 'why', 'change', 'mind', 'still', 'ever', 'really', 'just', 'like', 'want', 'tell', 'think', 'feel', 'say', 'know', 'see', 'look', 'own', 'have', 'has', 'had'}


def _stem(w: str) -> str:
    for suf in ('ing', 'ed', 'es', 's'):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def topic_tokens(q: str, subject: str = '') -> set:
    """The question's content words, stemmed, without prices, numbers and
    the subject's own name."""
    text = re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", ' ', str(q or '').lower())
    subj = {_stem(w) for w in re.findall(r"[a-z]+", str(subject or '').lower())}
    return {_stem(w) for w in re.findall(r"[a-z]+", text) if w not in _TOPIC_STOP and _stem(w) not in _TOPIC_STOP and len(w) > 2 and _stem(w) not in subj}


def topic_hash(q: str, subject: str = '') -> str:
    """The question's topic key, so a re-ask at another price (or in
    other words) shares the research."""
    return hashlib.sha1(' '.join(sorted(topic_tokens(q, subject))).encode('utf-8')).hexdigest()[:12]


def topics_overlap(a: str, b: str, subject: str = '') -> float:
    """Jaccard overlap of two questions' topic words (0 to 1)."""
    ta, tb = topic_tokens(a, subject), topic_tokens(b, subject)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def research_key(subject: str, q: str) -> str:
    return f"{RESEARCH_PREFIX}{_slug(subject)}/{topic_hash(q, subject)}.json"


def media_key(subject: str, q: str, day: str = '') -> str:
    """The media brief's key: subject + topic + the day it was gathered."""
    day = day or time.strftime('%Y-%m-%d', time.gmtime())
    return f"{MEDIA_PREFIX}{_slug(subject)}/{day}/{topic_hash(q, subject)}.json"


def facts_key(subject: str) -> str:
    return f"{FACTS_PREFIX}{_slug(subject)}.json"


def _twin_brief(twin: dict) -> dict:
    """What the agents know about the twin: the whole audience in plain
    numbers (the research anchors ride in; the rest of the diagnostics
    never do)."""
    m = _meta(twin)
    ident = (twin or {}).get('identity') or {}
    demo = ident.get('demographics') or {}
    traits = {k: {'value': (v.get('value') if isinstance(v, dict) else v), 'read': (v.get('read') if isinstance(v, dict) else '')} for k, v in ((twin or {}).get('traits') or {}).items()}
    lc = (twin or {}).get('lifecycle') or {}
    diag = (twin or {}).get('diagnostics') or {}
    anchors = diag.get('research') if isinstance(diag.get('research'), dict) else (diag.get('anchors') if isinstance(diag.get('anchors'), dict) else {})
    return {'name': m.get('name'), 'subject': m.get('subject'), 'kind': m.get('subject_kind'), 'format': m.get('format'), 'stage': m.get('franchise_stage'),
            'platform_home': m.get('platform_home'), 'size_us': m.get('size_us'), 'avid_share_pct': m.get('avid_share_pct'),
            'window': {'start': m.get('window_start'), 'end': m.get('window_end')},
            'demographics': {k: [{'label': r.get('label'), 'pct': round(float(r.get('pct') or 0), 1)} for r in (v or []) if isinstance(r, dict)][:8] for k, v in demo.items()},
            'platforms': [{'label': p.get('label'), 'reach_pct': round(float(p.get('reach_pct') or 0), 1)} for p in (ident.get('platforms') or [])[:8] if isinstance(p, dict)],
            'purchase': [{'label': p.get('label'), 'pct': round(float(p.get('pct') or 0), 1)} for p in (ident.get('purchase') or [])[:8] if isinstance(p, dict)],
            'talent': (ident.get('talent') or [])[:6], 'traits': traits, 'lifecycle': {k: v for k, v in lc.items() if k != 'read'}, 'lifecycle_read': lc.get('read'),
            'identity_read': ident.get('read'), 'anchors': {k: anchors.get(k) for k in ('title_history', 'notes', 'franchise_stage', 'platform_home') if anchors.get(k)}}


# --------------------------------------------------------- the personas
def _pick_by_share(rows, u, default=''):
    tot = sum(float(r.get('pct') or 0) for r in rows) or 100.0
    acc = 0.0
    for r in rows:
        acc += float(r.get('pct') or 0) / tot
        if u <= acc:
            return str(r.get('label') or default)
    return str(rows[-1].get('label') or default) if rows else default


def _level(v, lo, hi):
    return 'low' if v < lo else ('high' if v > hi else 'mid')


_PLATFORM_NAMES = ('paramount+', 'paramount plus', 'netflix', 'hulu', 'disney+', 'disney plus', 'max', 'hbo max', 'peacock', 'prime video', 'amazon prime video',
                   'apple tv+', 'apple tv plus', 'apple tv', 'tubi', 'pluto tv', 'pluto', 'the roku channel', 'roku channel', 'roku', 'freevee', 'xumo', 'plex', 'crackle',
                   'sling', 'sling tv', 'fubo', 'fubotv', 'philo', 'youtube tv', 'youtube', 'youtube premium', 'crunchyroll', 'starz', 'amc+', 'mgm+', 'bet+', 'discovery+',
                   'espn+', 'espn', 'britbox', 'acorn tv', 'shudder', 'hallmark+', 'hallmark movies now', 'spotify', 'apple music', 'amazon music', 'pandora', 'siriusxm',
                   'audible', 'kindle', 'twitch', 'tiktok', 'instagram', 'snapchat', 'facebook', 'x', 'threads', 'pinterest', 'reddit', 'dramabox', 'reelshort', 'goodshort',
                   'xbox game pass', 'game pass', 'playstation plus', 'nintendo switch online', 'directv stream', 'directv', 'dish', 'xfinity', 'spectrum', 'verizon fios',
                   'walmart+', 'amazon prime', 'prime', 'costco', 'doordash', 'uber eats', 'instacart')


def subject_class(twin: dict) -> str:
    """What the twin is built around, for the persona's vocabulary
    (Jenna 2026-11-03: 'Paramount+ is a streaming platform, not a show;
    you would not have Paramount+ merch or DVDs'):

      civic     an electorate or the residents of a place
      platform  a streaming service, app, network or subscription
      brand     a product, retailer or company people buy from
      talent    a person
      title     a series, film, franchise, game, podcast or book
      audience  an interest group or persona universe
    """
    m = _meta(twin)
    ident = (twin or {}).get('identity') or {}
    base_kind = str((ident.get('base') or {}).get('kind') or '')
    if base_kind in ('electorate', 'residents'):
        return 'civic'
    kind = str(m.get('subject_kind') or '').lower()
    fmt = str(m.get('format') or '').lower()
    name = str(m.get('name') or m.get('subject') or '').strip()
    low = name.lower()
    cat = str(m.get('catalog_category') or m.get('category') or '').upper()
    home = str(m.get('platform_home') or '').strip().lower()
    if kind == 'talent' or fmt in ('talent', 'person', 'creator', 'influencer', 'host', 'personality', 'podcaster', 'musician', 'band', 'artist', 'author', 'comedian', 'actor', 'athlete'):
        return 'talent'
    if low in _PLATFORM_NAMES or (home and home == low) or cat in ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'APP/PLATFORM', 'PLATFORMS', 'SOCIAL MEDIA', 'STREAMING MUSIC',
                                                                   'FAST PLATFORM', 'FAST CHANNEL', 'VIRTUAL MVPD/FAST', 'VMVPD', 'BROADCAST/CABLE', 'MEDIA', 'SEARCH ENGINE/AI', 'TELECOM') \
            or re.search(r"\b(?:tv|channel|network|streaming|app|platform|plus)\b|\+$", low):
        return 'platform'
    if kind == 'brand' or fmt == 'brand':
        return 'brand'
    if kind == 'ip' or fmt in ('series', 'film', 'movie', 'franchise', 'game', 'podcast', 'book', 'short', 'special', 'event'):
        return 'title'
    return 'audience'


PERSONA_BASE_VERSION = 1
PERSONA_BASE_SYSTEM = """You research who the base-level person behind a subject really is, so a room of digital twins is built on the right kind of person (the way a Profile IQ build is researched before it is written). Approved sources only: SEC filings and earnings reports, Pew Research, Nielsen, Statista, eMarketer, YouGov, Antenna and similar subscriber-behavior trackers, Sensor Tower or data.ai, Circana for retail, the service's or brand's own published counts and plan names, and trade press (Variety, Deadline, The Hollywood Reporter) for lineup and release facts. Use the web_search tool when you have it. Never store visits, device counts or total-brand-reach figures that mix offline.
The subject is one of: a streaming service, app or network (its subscribers or users); a brand or retailer (its buyers); a person (their followers or fans); a title (its viewers, readers or players); an interest audience. Read which it is from the input and answer for that base.
Return JSON only, plain words, every number a plain number, the whole answer under 1,400 tokens:
{"who": "<two sentences: the typical person in this base and how they differ from US adults overall>",
 "motivations": ["<why they are here, three to six, each a plain noun phrase in the third person (no I, my or we): 'the NFL on CBS', 'Yellowstone and 1923', 'kids shows for the family', 'live news'>"],
 "tastes": ["<the titles, genres or categories this base reaches for most, four to eight, by name where the record gives them>"],
 "plan_mix": {"<plan as the service names it, with ads or without>": <pct>} (a subscription service only; {} otherwise),
 "tenure": {"under a year": <pct>, "one to three years": <pct>, "three years or more": <pct>} ({} when unknown),
 "churn": "<one sentence on how this base comes and goes: seasonal, around shows, through bundles, through a carrier or a free trial>",
 "devices": {"TV": <pct>, "phone": <pct>, "laptop or tablet": <pct>} ({} when unknown),
 "household": "<one sentence on the household shape and the income skew>",
 "co_subscriptions": ["<other services most of them also hold, up to five>"],
 "shopping": ["<retailers, channels or brands this base buys through or alongside, up to five>"] (a brand or retailer only; [] otherwise),
 "sayings": ["<six to ten short first-person lines a typical member would say about the subject, varied, plain, no two alike in shape>"],
 "notes": "<two sentences on what moves this base and what loses it>"}
Use null or an empty value where you have no approved-source basis; never invent a plan name, a title or a number. Write "plan" not "tier", and "home" or "family" not "household". The motivations are the base's own reasons for being there, never the question the twin was built to answer."""


def _base_list(v, n: int, cap: int = 90) -> list:
    out = []
    for x in (v if isinstance(v, list) else []):
        t = ' '.join(str(x or '').split()).strip(' .')
        if t and t not in out:
            out.append(t[:cap])
        if len(out) >= n:
            break
    return out


def _base_shares(v, n: int = 6) -> dict:
    out = {}
    if isinstance(v, dict):
        for k, x in list(v.items())[:n]:
            try:
                f = float(x)
            except (TypeError, ValueError):
                continue
            k2 = ' '.join(str(k or '').split())[:60]
            if k2 and f > 0:
                out[k2] = round(f, 1)
    return out


_BASE_FIXES = ((re.compile(r"\bad[- ]levels?\b", re.I), 'ad plan'), (re.compile(r"\blevels? price\b", re.I), 'plan price'), (re.compile(r"\bthe people account\b", re.I), 'the family account'),
               (re.compile(r"\bpeople viewer\b", re.I), 'viewer'), (re.compile(r"\bcouple people\b", re.I), 'couple'), (re.compile(r"\bfamily or couple\b", re.I), 'family or a couple'))


def _base_text(v, cap: int) -> str:
    """A scrubbed sentence or two, cut at a sentence end, never mid-word."""
    t = scrub(' '.join(str(v or '').split()))
    for rx, rep in _BASE_FIXES:
        t = rx.sub(rep, t)
    if len(t) > cap:
        cut = t[:cap]
        t = cut[:cut.rfind('. ') + 1] if '. ' in cut[cap // 2:] else cut.rsplit(' ', 1)[0]
    return t.strip()


def normalize_persona_base(got: dict, klass: str) -> dict:
    """The research answer as the room keeps it: short lists, clean
    shares, scrubbed sentences."""
    if not isinstance(got, dict):
        return {}

    def fixed(x):
        t = scrub(x)
        for rx, rep in _BASE_FIXES:
            t = rx.sub(rep, t)
        return t
    out = {
        'version': PERSONA_BASE_VERSION, 'class': klass, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'who': _base_text(got.get('who'), 420),
        'motivations': [fixed(x) for x in _base_list(got.get('motivations'), 6)],
        'tastes': [fixed(x) for x in _base_list(got.get('tastes'), 8)],
        'plan_mix': _base_shares(got.get('plan_mix')) if klass == 'platform' else {},
        'tenure': _base_shares(got.get('tenure'), 3),
        'churn': _base_text(got.get('churn'), 260),
        'devices': _base_shares(got.get('devices'), 3),
        'household': _base_text(got.get('household'), 240),
        'co_subscriptions': [fixed(x) for x in _base_list(got.get('co_subscriptions'), 5)],
        'shopping': [fixed(x) for x in _base_list(got.get('shopping'), 5)] if klass in ('brand', 'audience') else [],
        'sayings': [fixed(x) for x in _base_list(got.get('sayings'), 10, 160)],
        'notes': _base_text(got.get('notes'), 420),
    }
    if not (out['who'] or out['motivations'] or out['tastes']):
        return {}
    return out


def persona_base(twin: dict, *, models=None, usage_extras: Optional[dict] = None, force: bool = False) -> dict:
    """The researched base persona behind a twin (Jenna 2026-11-03: 'for
    bios it should do the same kind of external research it does when
    creating profile iqs to ensure it's anchored in the right type of
    base level persona'). One approved-source call per twin, kept on the
    twin under ``persona_base`` and reused by every room on it. {} when
    the model is off (REGRESSION_TEST_MODE, no key) or the call fails:
    the bios then stand on the class vocabulary alone."""
    have = (twin or {}).get('persona_base')
    if isinstance(have, dict) and have.get('motivations') and not force and int(have.get('version') or 0) >= PERSONA_BASE_VERSION:
        return have
    klass = subject_class(twin)
    if klass == 'civic':
        return {}
    if models is None and regression_mode():
        return {}
    m = _meta(twin)
    diag = ((twin or {}).get('diagnostics') or {}).get('research') or {}
    user = json.dumps({'subject': m.get('name') or m.get('subject'), 'base_class': klass, 'format': m.get('format'), 'platform_home': m.get('platform_home'),
                       'subject_kind': m.get('subject_kind'), 'what_we_know': str(diag.get('notes') or '')[:400],
                       'today': time.strftime('%Y-%m-%d', time.gmtime())})
    try:
        M = models or Models(usage_extras=usage_extras)
        data, _rec = M.call('research', PERSONA_BASE_SYSTEM, user, max_tokens=2000, temperature=0.2)
    except Exception as e:
        print(f"[twin-room] persona base research failed: {str(e)[:160]}")
        data = None
    out = normalize_persona_base(data, klass) if isinstance(data, dict) else {}
    if out and isinstance(twin, dict):
        twin['persona_base'] = out
    return out


def _pick_list(items: list, u: float) -> str:
    items = [str(x) for x in (items or []) if str(x).strip()]
    return items[min(len(items) - 1, int(u * len(items)))] if items else ''


def _device_phrase(label: str, age: int) -> str:
    lab = str(label or '').lower()
    if 'phone' in lab:
        return 'watches on the phone as much as the TV'
    if 'laptop' in lab or 'tablet' in lab:
        return 'watches on a laptop or tablet more than the TV'
    if 'tv' in lab:
        return 'watches on the living room TV, the phone only for a clip' if age >= 40 else 'watches on the TV at night and the phone on the move'
    return ''


def base_picks(twin: dict, sid: str, age: int) -> dict:
    """What the researched base gives one seat, picked by the seat's seed:
    a reason they are here, one or two tastes, the plan, the screen, a
    store, a line they might say."""
    b = (twin or {}).get('persona_base') or {}
    if not isinstance(b, dict) or not (b.get('motivations') or b.get('tastes')):
        return {}
    why = _pick_list(b.get('motivations'), unit('base-why', sid))
    tastes = [t for t in b.get('tastes') or [] if t]
    t1 = _pick_list(tastes, unit('base-t1', sid))
    rest = [t for t in tastes if t != t1]
    t2 = _pick_list(rest, unit('base-t2', sid)) if rest and unit('base-two', sid) < 0.6 else ''
    plan = ''
    if b.get('plan_mix'):
        plan = _pick_by_share([{'label': k, 'pct': v} for k, v in b['plan_mix'].items()], unit('base-plan', sid))
    device = ''
    if b.get('devices'):
        device = _device_phrase(_pick_by_share([{'label': k, 'pct': v} for k, v in b['devices'].items()], unit('base-dev', sid)), age)
    shop = _pick_list(b.get('shopping'), unit('base-shop', sid))
    voice = _pick_list(b.get('sayings'), unit('base-voice', sid))
    return {'why': why, 'tastes': [t for t in (t1, t2) if t], 'plan': plan, 'device': device, 'shop': shop, 'voice': voice, 'churn': str(b.get('churn') or '')}


def _third_person(text: str, pron: str) -> str:
    """A phrase the base said in the first person, said about the seat."""
    poss = {'she': 'her', 'he': 'his'}.get(pron, 'their')
    obj = {'she': 'her', 'he': 'him'}.get(pron, 'them')
    t = str(text or '')
    t = re.sub(r"\bmy\b", poss, t)
    t = re.sub(r"\bme\b", obj, t)
    t = re.sub(r"\bI\b", pron, t)
    t = re.sub(r"\bour\b", 'their', t)
    t = re.sub(r"\bwe\b", 'they', t)
    t = re.sub(r"\bus\b", 'them', t)
    return t


def base_sentence(picks: dict, klass: str, name: str, pron: str) -> str:
    """The sentence the researched base adds to a bio, by class."""
    if not picks or not (picks.get('why') or picks.get('tastes')):
        return ''
    P = pron.capitalize()
    why = _third_person(picks.get('why') or '', pron)
    tastes = [_third_person(t, pron) for t in (picks.get('tastes') or []) if t]
    plan, shop = picks.get('plan') or '', picks.get('shop') or ''
    tl = (tastes[0] if len(tastes) == 1 else ' and '.join(tastes)) if tastes else ''
    if klass == 'platform':
        s = (f"{P} came to {name} for {why}" if why else f"{P} keeps {name}") + (f" and reaches for {tl} when {pron} opens it" if tl else '') + '.'
        if plan:
            s += f" {P} {'is' if pron != 'they' else 'are'} on the {plan} plan."
        return s
    if klass == 'brand':
        s = (f"{P} buys {name} for {why}" if why else f"{P} buys {name}") + (f"; {tl} {'is' if len(tastes) == 1 else 'are'} in the basket too" if tl else '') + '.'
        if shop:
            s += f" {P} {'gets' if pron != 'they' else 'get'} it at {shop} most of the time."
        return s
    if klass == 'talent':
        return (f"{P} follows {name} for {why}" if why else f"{P} follows {name}") + (f" and also keeps up with {tl}" if tl else '') + '.'
    if klass == 'audience':
        return (f"What keeps {pron} in it is {why}" if why else f"{P} {'is' if pron != 'they' else 'are'} in it") + (f"; {tl} {'is' if len(tastes) == 1 else 'are'} part of the week too" if tl else '') + '.'
    return (f"{P} came to {name} through {why}" if why else f"{P} watches {name}") + (f" and also watches {tl}" if tl else '') + '.'


def talent_is_creator(twin: dict) -> bool:
    """A creator, host or musician puts work out; an actor is in it."""
    m = _meta(twin)
    fmt = str(m.get('format') or '').lower()
    home = str(m.get('platform_home') or '').lower()
    return fmt in ('creator', 'influencer', 'host', 'personality', 'podcaster', 'musician', 'band', 'artist', 'author') or 'youtube' in home or 'tiktok' in home or 'twitch' in home


def persona_card(twin: dict, seat: dict, session_seed: str) -> dict:
    """The full persona for one seat, deterministic for the session: the
    seat's identity, the twin's demographics as they apply, the traits
    as plain habits, the lifecycle stage, the relationship to the
    subject, the media diet."""
    m = _meta(twin)
    sid = f"{session_seed}|seat{seat.get('seat')}|{seat.get('name')}"
    demo = ((twin or {}).get('identity') or {}).get('demographics') or {}
    tv = {k: float((v.get('value') if isinstance(v, dict) else v) or 50) for k, v in ((twin or {}).get('traits') or {}).items()}
    g = lambda k: tv.get(k, 50.0)
    age = int(seat.get('age') or 35)
    gc = str(seat.get('gender') or 'F')
    if gc == 'X':
        gender, pron = 'nonbinary', ('they', 'their')
    elif gc == 'F':
        gender, pron = 'woman', ('she', 'her')
    else:
        gender, pron = 'man', ('he', 'his')
    lc = (twin or {}).get('lifecycle') or {}
    stages = [('discovering', float(lc.get('discovering_pct') or 15)), ('trying', float(lc.get('trying_pct') or 15)), ('in the habit', float(lc.get('habit_pct') or 40)),
              ('lapsing', float(lc.get('lapsing_pct') or 18)), ('returning', float(lc.get('returning_pct') or 12))]
    stage = _pick_by_share([{'label': a, 'pct': b} for a, b in stages], unit('stage', sid))
    income = str(seat.get('income') or '') or _pick_by_share(demo.get('income') or [], unit('income', sid), '')
    education = str(seat.get('education') or '') or _pick_by_share(demo.get('education') or [], unit('education', sid), '')
    parental = str(seat.get('parental') or '') or _pick_by_share(demo.get('parental') or [], unit('parental', sid), '')
    relationship = str(seat.get('relationship') or '') or _pick_by_share(demo.get('relationship') or [], unit('relationship', sid), '')
    orientation = str(seat.get('orientation') or '')
    occupation = str(seat.get('occupation') or '')
    ethnicity = str(seat.get('ethnicity') or '')
    fmt = str(m.get('format') or 'series')
    legacy = str(m.get('franchise_stage') or '') in ('legacy', 'established')
    klass = subject_class(twin)
    name0 = str(m.get('name') or m.get('subject') or 'the title')
    price = {'low': f"spends freely on the things {pron[0]} loves and rarely waits for a sale", 'mid': 'watches price but pays for the right thing',
             'high': 'very price-conscious; compares, waits for deals, hates paying twice for the same thing'}[_level(g('price_sensitivity'), 35, 62)]
    lv_loyal, lv_merch = _level(g('franchise_loyalty'), 40, 65), _level(g('merch_propensity'), 35, 60)
    u_phys = unit('physical', sid)
    if klass == 'platform':
        # a service: loyalty is where it sits among the services, the extras are tiers and add-ons, the screen is the device
        loyal = {'low': f"casual about {name0}; it is one service among several", 'mid': f"fond of what {name0} has and checks it most weeks",
                 'high': f"{name0} is {pron[1]} first stop when {pron[0]} sits down to watch"}[lv_loyal]
        merch = {'low': 'stays on the basic plan and skips the add-ons', 'mid': 'has added a channel or a bundle once or twice when a show called for it',
                 'high': 'pays for the top plan and adds channels when a show calls for it'}[lv_merch]
        if age >= 40 and u_phys < 0.6:
            physical = 'watches on the living room TV, the phone only for a clip'
        elif age < 30:
            physical = 'watches on the phone and the laptop as much as the TV'
        else:
            physical = 'watches on the TV at night and the phone on the move'
    elif klass == 'brand':
        loyal = {'low': 'casual about brands; buys what is on sale', 'mid': f"fond of {name0} and reaches for it when it is there",
                 'high': f"loyal to {name0}; would go to a second store for it"}[lv_loyal]
        merch = {'low': 'rarely buys the branded extras', 'mid': 'has picked up a branded item or two over the years',
                 'high': 'buys the branded extras and the gift sets when they show up'}[lv_merch]
        physical = ('shops the aisle in person more than online' if (age >= 40 and u_phys < 0.6) else ('orders most of it online' if age < 30 or u_phys > 0.7 else 'splits it between the store and online'))
    elif klass == 'talent':
        loyal = {'low': f"casual about {name0}; one of many names {pron[0]} follows", 'mid': f"fond of {name0} and watches when something new comes out",
                 'high': f"a real fan of {name0}; knows the work by heart and rewatches"}[lv_loyal]
        merch = {'low': 'rarely buys merchandise of any kind', 'mid': 'has bought a piece or two of merchandise over the years',
                 'high': 'buys merchandise now and then: a shirt, a poster, a keepsake, a gift for a friend who is a fan too'}[lv_merch]
        if talent_is_creator(twin):
            physical = ('watches on the living room TV, the phone only for a clip' if (age >= 40 and u_phys < 0.6) else ('watches on the phone and the laptop as much as the TV' if age < 30 else 'watches on the TV at night and the phone on the move'))
        elif age >= 40 and (g('merch_propensity') >= 50 or u_phys < 0.5):
            physical = 'still owns DVDs and a few box sets and likes a shelf copy of a favorite'
        elif age < 30:
            physical = 'streams everything; owns almost no discs and has nowhere to play one'
        else:
            physical = 'kept a few box sets from years back; mostly streams now' if u_phys < 0.45 else 'gave the discs away years ago; streaming only'
    elif klass == 'audience':
        loyal = {'low': 'casual about it; it is one of many things', 'mid': 'fond of it and makes time for it',
                 'high': 'deeply into it; it is a big part of the week'}[lv_loyal]
        merch = {'low': 'rarely buys the extras', 'mid': 'buys a related item now and then', 'high': 'spends on it happily: the gear, the extras, the gifts for friends who are into it too'}[lv_merch]
        physical = ('watches on the living room TV, the phone only for a clip' if (age >= 40 and u_phys < 0.6) else ('watches on the phone and the laptop as much as the TV' if age < 30 else 'watches on the TV at night and the phone on the move'))
    else:
        loyal = {'low': 'casual about the title; it is one of many things', 'mid': 'fond of the title and watches when it comes around',
                 'high': 'deeply loyal to the title; knows it by heart and rewatches'}[lv_loyal]
        merch = {'low': 'rarely buys merchandise of any kind', 'mid': 'has bought a piece or two of show merchandise over the years',
                 'high': 'buys show merchandise now and then: a mug, a shirt, a keepsake, a gift for a friend who loves it too'}[lv_merch]
        if age >= 40 and (g('merch_propensity') >= 50 or u_phys < 0.5):
            physical = 'still owns DVDs and a few box sets and likes a shelf copy of a favorite'
        elif age < 30:
            physical = 'streams everything; owns almost no discs and has nowhere to play one'
        elif u_phys < 0.45:
            physical = 'kept a few box sets from years back; mostly streams now'
        else:
            physical = 'gave the discs away years ago; streaming only'
    churn = {'low': 'keeps subscriptions for years without thinking about it', 'mid': 'trims a service now and then when the bill creeps up',
             'high': 'cancels and re-subscribes around the shows; rarely keeps more than two or three services'}[_level(g('churn_propensity'), 35, 60)]
    social = {'low': 'finds things by searching and through friends, not by scrolling', 'mid': 'finds new things half from the feed, half from friends',
              'high': 'finds most new things on social feeds and short clips'}[_level(g('discovery_social_vs_search'), 40, 62)]
    wom = {'low': f"keeps {pron[1]} viewing to {'themself' if gender == 'nonbinary' else (pron[1] + 'self')}", 'mid': 'talks about shows with a couple of friends',
           'high': 'texts the group chat about what they are watching and gets friends onto things'}[_level(g('word_of_mouth'), 40, 65)]
    lv_live = _level(g('live_events'), 30, 55)
    if klass == 'platform':
        live = {'low': 'does not go to premieres or fan events for its shows', 'mid': 'has been to a premiere or a fan screening for one of its shows',
                'high': 'goes to premieres and fan screenings for its shows when they come near'}[lv_live]
    elif klass == 'brand':
        live = {'low': 'does not go to brand events or pop-ups', 'mid': 'has been to a pop-up or a sampling event once',
                'high': 'goes to pop-ups and launch events when they come near'}[lv_live]
    else:
        live = {'low': 'does not go to fan events or live shows for this', 'mid': 'has been to one fan event or screening',
                'high': 'goes to fan events, screenings and live shows when they come near'}[lv_live]
    ads = {'low': 'pays extra to skip ads', 'mid': 'takes the cheaper plan with ads on some services', 'high': 'fine with ads if it saves money'}[_level(g('ad_tolerance'), 40, 62)]
    binge = 'binges a season in a weekend' if g('binge_vs_appointment') >= 55 else 'watches an episode or two a night'
    second = 'scrolls the phone while watching' if g('second_screen') >= 55 else 'puts the phone down to watch'
    name = str(m.get('name') or m.get('subject') or 'the title')
    ident0 = (twin or {}).get('identity') or {}
    base_kind = str((ident0.get('base') or {}).get('kind') or '')
    civic = base_kind in ('electorate', 'residents')
    party = str(seat.get('party') or '') or _pick_by_share(demo.get('party') or [], unit('party', sid), '')
    urbanicity = str(seat.get('urbanicity') or '') or _pick_by_share(demo.get('urbanicity') or [], unit('urban', sid), '')
    all_issues = [str(x) for x in (ident0.get('issues') or []) if str(x).strip()]
    my_issues = []
    if all_issues:
        order = sorted(range(len(all_issues)), key=lambda i: unit('issue', sid, i) * (1.0 + 0.35 * i))   # the base's top issues come up most
        my_issues = [all_issues[i] for i in order[:min(3, len(all_issues))]]
    if civic and base_kind == 'electorate':
        # an electorate: the relationship is a voting record, not a viewing one (Jenna 2026-10-30)
        rel = {'discovering': 'registered to vote in the last two years and has voted once so far',
               'trying': 'votes in presidential years and is deciding whether this race is worth the trip',
               'in the habit': 'has voted in every November election for years, most primaries too',
               'lapsing': 'voted for years but sat out the last two elections',
               'returning': 'sat out a few cycles and is back on the rolls this year'}[stage]
        freq = {'discovering': 'is still working out where ' + pron[0] + ' stands', 'trying': 'follows a race only when it gets close',
                'in the habit': 'follows state politics through the year', 'lapsing': 'tunes in a week or two before an election',
                'returning': 'is paying attention again this year'}[stage]
    elif civic:
        rel = {'discovering': f"moved to the area in the last two years", 'trying': 'has lived in the area a few years and is still finding ' + pron[1] + ' places',
               'in the habit': 'has lived in the area most of ' + pron[1] + ' life', 'lapsing': 'has lived in the area for years and talks about leaving',
               'returning': 'moved back to the area after years away'}[stage]
        freq = 'keeps up with local news most weeks' if unit('local', sid) < 0.6 else 'catches local news when something big happens'
    elif klass == 'platform':
        # a service is subscribed to, kept, dropped and taken back; nobody rewatches a platform
        rel, freq = {
            'discovering': (f"signed up for {name} in the last few months", f"is still finding {pron[1]} way around what is on it"),
            'trying': (f"took {name} for one show and is deciding whether to keep it", 'opens it a couple of nights a week'),
            'in the habit': ((f"has had {name} for years and keeps it through the year" if (legacy and age >= 34) else f"has kept {name} for a few years now"), 'has a show or two going on it at any time'),
            'lapsing': (f"still pays for {name} but has barely opened it this past year", 'is close to cancelling'),
            'returning': (f"came back to {name} this year after cancelling for a while", 'resubscribed for a new season and is catching up'),
        }[stage]
    elif klass == 'brand':
        rel, freq = {
            'discovering': (f"tried {name} for the first time in the last year", 'has bought it a few times since'),
            'trying': (f"has {name} in the rotation but has not settled on it", f"buys it when it is on sale or in front of {pron[1] if gender != 'nonbinary' else 'them'}"),
            'in the habit': (f"has bought {name} for years", 'it is on the list most weeks'),
            'lapsing': (f"used to buy {name} all the time; has drifted to other brands this past year", 'picks it up now and then'),
            'returning': (f"came back to {name} after trying other brands", 'is buying it again most trips'),
        }[stage]
    elif klass == 'talent':
        creator = talent_is_creator(twin)
        rel, freq = {
            'discovering': (f"found {name} in the last year", 'is working back through the earlier videos' if creator else 'is working back through the earlier work'),
            'trying': ((f"has watched a few of {name}'s videos and is still deciding" if creator else f"has watched a few things with {name} and is still deciding"),
                       (f"catches {name} when a new one comes up in the feed" if creator else f"catches {name} when a new project comes around")),
            'in the habit': (f"has followed {name} for years", f"watches whatever {name} puts out" if creator else f"watches whatever {name} is in"),
            'lapsing': (f"used to follow {name} closely; has drifted this past year", 'has not kept up lately'),
            'returning': (f"came back to {name} after a long break", f"is catching up on what {name} has put out lately" if creator else f"is catching up on what {name} has done lately"),
        }[stage]
    elif klass == 'audience':
        rel, freq = {
            'discovering': ('got into this in the last year', 'is still finding the corners of it'),
            'trying': (f"is still working out how much of this is {pron[1]} thing", 'dips in now and then'),
            'in the habit': ('has been into this for years', 'makes time for it most weeks'),
            'lapsing': ('has drifted from this over the past year', 'has not made time for it in months'),
            'returning': ('came back to this after a break', 'is picking it up again'),
        }[stage]
    elif stage == 'discovering':
        rel = f"found {name} in the last year" + (' on streaming' if fmt in ('series', 'film') else '')
        freq = 'still working through it for the first time'
    elif stage == 'trying':
        rel = f"started {name} recently and is still deciding how much it is {pron[1]} thing"
        freq = 'watches now and then'
    elif stage == 'in the habit':
        rel = (f"has watched {name} since the original run" if (legacy and age >= 34) else f"has been with {name} for years")
        freq = 'rewatches it every year or two and knows the episodes'
    elif stage == 'lapsing':
        rel = f"used to watch {name} a lot; has drifted away this past year"
        freq = 'has not put it on in months'
    else:
        rel = f"came back to {name} after a long break"
        freq = 'rewatching with fresh eyes'
    if klass in ('platform', 'brand', 'talent', 'audience') and lv_loyal == 'high' and stage in ('discovering', 'trying'):
        loyal = {'platform': f"warming to {name0} fast", 'brand': f"taking to {name0} fast", 'talent': f"becoming a fan of {name0} fast", 'audience': 'getting into it fast'}[klass]
    elif klass in ('platform', 'brand', 'talent', 'audience') and lv_loyal == 'high' and stage == 'lapsing':
        loyal = {'platform': f"still fond of {name0}, even with it barely open lately", 'brand': f"still partial to {name0}, even while buying other brands",
                 'talent': f"still a fan of {name0}, even while not keeping up", 'audience': 'still into it at heart, even while not making time for it'}[klass]
    owns = []
    if civic or klass in ('platform', 'brand', 'audience'):
        pass    # nobody owns a service, a retailer or an interest on DVD, and a platform sells no mugs
    elif 'owns DVDs' in physical and stage in ('in the habit', 'returning') and g('merch_propensity') >= 45:
        owns.append(f"the complete {name} on DVD from years ago" if klass != 'talent' else f"{name}'s films on DVD from years ago")
    if klass in ('title', 'talent') and g('merch_propensity') >= 55 and unit('owns', sid) < 0.6:
        owns.append(f"a {name} mug or shirt" if klass == 'title' else f"a {name} shirt or poster")
    ident = (twin or {}).get('identity') or {}
    plats = [str(p.get('label')) for p in sorted((ident.get('platforms') or []), key=lambda p: -float(p.get('reach_pct') or 0)) if isinstance(p, dict)][:6]
    services = [p for i, p in enumerate(plats) if unit('pl', sid, p) < (0.95 - 0.12 * i)][:4] or plats[:2]
    shops = [str(p.get('label')) for p in (ident.get('purchase') or [])[:6] if isinstance(p, dict)]
    shops = [s for s in shops if unit('shop', sid, s) < 0.7][:3]
    picks = base_picks(twin, sid, age)
    if picks.get('device') and klass in ('platform', 'audience', 'title') or (picks.get('device') and klass == 'talent' and talent_is_creator(twin)):
        physical = picks['device']   # the screen the base really watches on, from the record, over the age prior
    card = {'seat': seat.get('seat'), 'name': seat.get('name'), 'age': age, 'gender': gender, 'city': seat.get('city'), 'segment': str(seat.get('segment') or ''),
            'income': income, 'education': education, 'parental': parental, 'relationship_status': relationship,
            'orientation': orientation, 'occupation': occupation, 'ethnicity': ethnicity,
            'language': str(seat.get('language') or ''), 'cohort': str(seat.get('cohort') or ''),
            'party': party, 'urbanicity': urbanicity, 'issues': my_issues, 'civic': civic, 'base_kind': base_kind, 'subject_class': klass,
            'base': picks,
            'values': str(ident0.get('values') or ''),
            'lifecycle_stage': stage, 'relationship_to_subject': rel, 'how_often': freq, 'owns': owns,
            'habits': [price, loyal, merch, physical, churn, social, wom, live, ads, binge, second],
            'streams_on': services, 'shops_at': shops}
    card['bio'] = str(seat.get('persona') or '') or persona_bio(card, twin, sid)   # the bio on the seat, once written, is the one the agent acts on
    return card


_JOBS_EARLY = ('a barista finishing a degree', 'a server at a neighborhood restaurant', 'an assistant manager at a grocery store', 'a receptionist at a dental practice', 'a line cook',
               'a retail associate at a big-box store', 'a preschool aide', 'a warehouse picker', 'a hair stylist building a client list', 'a bank teller')
_JOBS_MID = ('a dental hygienist', 'a middle school teacher', 'a nurse on night shifts', 'a paralegal', 'a project manager at a logistics firm', 'a graphic designer who freelances',
             'an HR coordinator', 'a software tester', 'a real estate agent', 'a bookkeeper for small businesses', 'an event planner', 'a social worker', 'an electrician',
             'an insurance claims adjuster', 'an office manager', 'a high school counselor', 'a flight attendant', 'a veterinary tech', 'a librarian', 'a sales rep for a medical device company')
_JOBS_HIGH = ('a pharmacist', 'an accountant at a regional firm', 'a physical therapist', 'a bank branch manager', 'a senior project manager', 'an attorney at a small practice',
              'a marketing director', 'a nurse practitioner', 'an engineer at a utility', 'a sales director who travels most weeks', 'a dentist', 'a product manager at a software company')
_HUMAN_DETAILS = ('keeps a running list of comfort shows for sick days', 'has a group chat that has been going since college', 'cooks for the week on Sundays with something on in the background',
                  'plans one big trip a year and saves for it in a separate account', 'volunteers at the library on Saturday mornings', 'collects vinyl and still buys CDs at shows',
                  'redoes one room of the house every winter', 'plays bar trivia on Tuesdays and takes it seriously', 'has two rescue dogs that sleep through everything {pron} watches',
                  'keeps the TV on for company while working from home', 'sends the family a watch list every Thanksgiving', 'buys the birthday gifts for the whole friend group',
                  'runs before work most mornings with a podcast on', 'reads two books a month and talks about them more than TV', 'drives to a sister\'s place for Sunday dinner most weeks',
                  'coaches a kids\' soccer team in the fall', 'grows tomatoes on the balcony every summer', 'has not missed a high school friend\'s birthday in twenty years',
                  'rewatches the same three movies every December', 'keeps a spreadsheet of every concert {pron} has been to')
_STREAMING_NAMES = ('netflix', 'hulu', 'prime video', 'amazon prime video', 'max', 'hbo max', 'disney+', 'disney plus', 'peacock', 'paramount+', 'paramount plus', 'apple tv+', 'apple tv',
                    'youtube', 'youtube tv', 'tiktok', 'instagram', 'facebook', 'spotify', 'pandora', 'tubi', 'roku channel', 'pluto tv', 'twitch', 'snapchat', 'reddit', 'pinterest',
                    'x', 'twitter', 'sling tv', 'fubo', 'audible', 'kindle', 'siriusxm', 'crunchyroll', 'starz', 'amc+', 'britbox', 'philo', 'plex', 'freevee', 'discovery+', 'espn')


def _income_words(label: str) -> str:
    s = str(label or '').strip()
    if not s or 'prefer' in s.lower():
        return ''
    low = s.lower()
    if low.startswith('less than') or low.startswith('under'):
        amount = re.sub(r"(?i)^(less than|under)\s*", '', s).strip()
        return f"under {amount} a year"
    if 'or more' in low or s.endswith('+'):
        amount = re.sub(r"(?i)\s*(or more|\+)$", '', s).strip()
        return f"{amount} or more a year"
    m = re.match(r"^\s*(\$[\d,]+)\s*(?:-|to|\u2013)\s*(\$[\d,]+)\s*$", s)
    if m:
        return f"{m.group(1)} to {m.group(2)} a year"
    return f"{s.lower()} a year"


def _education_words(label: str, pron: str) -> str:
    low = str(label or '').lower()
    if not low or 'prefer' in low:
        return ''
    they = pron.lower() == 'they'
    has = 'have' if they else 'has'
    holds = 'hold' if they else 'holds'
    if 'graduate' in low and ('degree' in low or 'professional' in low) and 'college graduate' not in low:
        return f"{pron} {holds} a graduate degree"
    if 'bachelor' in low or 'college graduate' in low:
        return f"{pron} {has} a bachelor's degree"
    if 'some college' in low or 'associate' in low:
        obj = 'them' if they else ('her' if pron.lower() == 'she' else 'him')
        return f"{pron} {has} some college behind {obj}"
    if 'high school' in low:
        return f"{pron} finished high school"
    return ''


def _orientation_words(label: str, gender: str) -> str:
    """Lesbian / gay / queer from the seat's orientation and gender.
    Straight is omitted so the bio does not lead with it."""
    low = str(label or '').lower()
    if not low or 'prefer' in low or 'straight' in low or 'heterosexual' in low:
        return ''
    if 'lesbian' in low:
        return 'lesbian'
    if 'bisexual' in low:
        return 'bisexual'
    if 'gay' in low:
        return 'gay'
    if 'queer' in low:
        return 'queer'
    if not re.search(r"lgbtq", low):
        return ''
    g = str(gender or '')
    if g == 'woman':
        return 'lesbian'
    if g == 'man':
        return 'gay'
    return 'queer'


def _occupation_words(label: str, pron: str) -> str:
    lab = str(label or '').strip()
    if not lab or 'prefer' in lab.lower() or lab.lower() in ('other', 'none', 'n/a'):
        return ''
    they = pron.lower() == 'they'
    return f"{pron} work{'' if they else 's'} in {lab.lower()}"


def _parental_words(label: str) -> str:
    low = str(label or '').lower()
    if not low or 'prefer' in low:
        return ''
    if low.startswith('no') or low.startswith('not'):
        return 'no children at home'
    return 'children at home'


def _relationship_words(label: str) -> str:
    low = str(label or '').lower()
    if not low or 'prefer' in low:
        return ''
    for key, words in (('married', 'married'), ('partner', 'married or partnered'), ('relationship', 'in a relationship'), ('divorc', 'divorced'), ('separat', 'separated'),
                       ('widow', 'widowed'), ('single', 'single')):
        if key in low:
            return words
    return low


def _media_names(labels: list, keep: int = 4) -> list:
    out = [l for l in labels if str(l).strip().lower() in _STREAMING_NAMES or any(str(l).lower().startswith(p) for p in ('netflix', 'hulu', 'prime', 'amazon prime', 'disney', 'paramount', 'apple tv', 'youtube', 'peacock', 'max', 'hbo'))]
    return out[:keep] or [l for l in labels if str(l).lower() not in ('gmail', 'google maps', 'weather', 'google', 'outlook', 'yahoo mail', 'calendar', 'maps')][:2]


def persona_bio(card: dict, twin: dict, sid: str) -> str:
    """The bio the persona agent acts on, written for a reader (120 to
    200 words, third person): who they are, household and income as the
    twin's demographics put it, how they relate to the subject, their
    habits as plain traits, their media diet, one or two human details.
    Deterministic for the seat; scrubbed."""
    m = _meta(twin)
    name = str(card.get('name') or 'This person')
    age = int(card.get('age') or 35)
    g = str(card.get('gender') or 'woman')
    if g in ('nonbinary', 'non-binary', 'x'):
        pron, obj, poss = ('they', 'them', 'their')
        they = True
    elif g == 'woman':
        pron, obj, poss = ('she', 'her', 'her')
        they = False
    else:
        pron, obj, poss = ('he', 'him', 'his')
        they = False
    be = 'are' if they else 'is'
    income = _income_words(card.get('income') or '')
    low_income = bool(re.search(r"under|\$25,000 to|\$35,000 to|\$25,000 -", income))
    high_income = bool(re.search(r"\$1\d\d,\d{3}|\$2\d\d,\d{3}|or more", income))
    bank = _JOBS_HIGH if high_income else _JOBS_EARLY if (low_income or age < 25) else _JOBS_MID
    occ = _occupation_words(card.get('occupation') or '', pron)
    if occ and age >= 66:
        work_sent = f"{name}, {age}, lives in {card.get('city') or 'the US'}. {pron.capitalize()} retired after years in {str(card.get('occupation') or '').lower()}."
    elif occ:
        work_sent = f"{name}, {age}, lives in {card.get('city') or 'the US'}. {occ[0].upper() + occ[1:]}."
    elif age < 22:
        work_sent = f"{name}, {age}, lives in {card.get('city') or 'the US'} and is a college student working part time."
    elif age >= 66:
        work_sent = f"{name}, {age}, lives in {card.get('city') or 'the US'} and is retired after years as {bank[int(unit('job', sid) * len(bank))]}."
    else:
        work_sent = f"{name}, {age}, lives in {card.get('city') or 'the US'} and works as {bank[int(unit('job', sid) * len(bank))]}."
    stage = {'discovering': 'just finding it', 'trying': 'still trying it on', 'in the habit': 'firmly in the habit', 'lapsing': 'drifting away from it lately', 'returning': 'back to it after a break'}.get(str(card.get('lifecycle_stage') or ''), 'in the habit')
    sent = [work_sent]
    ow = _orientation_words(card.get('orientation') or '', g)
    if ow:
        sent.append(f"{pron.capitalize()} {be} {ow}.")
    rel_w = _relationship_words(card.get('relationship_status') or '')
    par_w = _parental_words(card.get('parental') or '')
    bits = ([rel_w] if rel_w else []) + ([('with ' if rel_w else '') + par_w] if par_w else [])
    hh = f"{pron.capitalize()} {be} {' '.join(bits)}" if bits else ''
    if income:
        hh = (hh + f", earning {income}") if hh else f"{pron.capitalize()} earns {income}"
    if hh:
        sent.append(hh + '.')
    edu = _education_words(card.get('education') or '', pron.capitalize())
    if edu:
        sent.append(edu + '.')
    if str(card.get('language') or '').strip().lower() == 'spanish':
        # a Spanish-speaking cohort (2026-11-02): the seat's home language is part of who they are and what they watch
        sent.append(f"At home {pron} {'speak' if pron == 'they' else 'speaks'} Spanish, and {pron} {'move' if pron == 'they' else 'moves'} between Spanish and English TV without thinking about it.")
    civic = bool(card.get('civic'))
    urb = str(card.get('urbanicity') or '').strip().lower()
    if urb and card.get('city'):
        where = {'urban': f"in the city itself", 'suburban': f"in the suburbs outside {card.get('city')}", 'rural': f"out in the country, a drive from {card.get('city')}"}.get(urb, '')
        if where:
            sent.append(f"Home is {where}.")
    party = str(card.get('party') or '').strip()
    issues = [str(x) for x in (card.get('issues') or []) if str(x).strip()]
    if party or issues:
        pl = party.lower()
        if 'republican' in pl:
            lean = f"{pron} {be} a Republican"
        elif 'democrat' in pl:
            lean = f"{pron} {be} a Democrat"
        elif 'independent' in pl or 'undecided' in pl or 'unaffiliated' in pl:
            lean = f"{pron} {be} an independent who has not settled on this race"
        elif party:
            lean = f"{pron} {be} {party}"
        else:
            lean = ''
        iss = ''
        if issues:
            iss = f"the issues {pron} weighs first are {', '.join(issues[:-1]) + (' and ' if len(issues) > 1 else '') + issues[-1]}".replace('weighs first are', 'weighs first ' + ('are' if len(issues) > 1 else 'is'))
        if lean and iss:
            sent.append(f"Politically, {lean}; {iss}.")
        elif lean:
            sent.append(f"Politically, {lean}.")
        elif iss:
            sent.append(f"{iss[0].upper() + iss[1:]}.")
    rel = str(card.get('relationship_to_subject') or '')
    klass = str(card.get('subject_class') or '')
    if civic:
        sent.append(f"{pron.capitalize()} {rel} and {card.get('how_often')}." if rel else f"{pron.capitalize()} lives in the area.")
    else:
        if rel and not re.match(r"^(?:she|he|they)\b", rel, re.I):
            rel = f"{pron.capitalize()} {rel}"
        sent.append(f"{rel} and {card.get('how_often')}; {pron} {be} {stage}." if rel else f"{pron.capitalize()} {be} {stage} with {m.get('name') or 'the subject'}.")
        bs = base_sentence(card.get('base') or {}, klass, str(m.get('name') or m.get('subject') or 'it'), pron)   # the researched base: why they are here, what they reach for
        if bs:
            sent.append(bs)
    if card.get('owns'):
        sent.append(f"{pron.capitalize()} owns {' and '.join(card['owns'])}.")
    h = list(card.get('habits') or [])
    if civic and len(h) >= 11:
        # a voter or a resident: money, subscriptions, how news and shows reach them; no title loyalty, merchandise or box sets
        price, _loyal, _merch, _physical, churn, social, wom, _live, ads, _binge, second = h[:11]
        sent.append(f"On money {pron} {price}.")
        sent.append(f"{pron.capitalize()} {churn}, and {second}.")
        diet = []
        if card.get('streams_on'):
            diet.append(f"streams on {', '.join(_media_names(card['streams_on']))}")
        if card.get('shops_at'):
            diet.append(f"shops at {', '.join(card['shops_at'][:3])}")
        diet.append(social)
        diet.append(wom)
        sent.append(f"{pron.capitalize()} " + ', '.join(diet[:-1]) + f", and {diet[-1]}.")
        sent.append(f"{pron.capitalize()} {ads}.")
        h = []
    if len(h) >= 11:
        price, loyal, merch, physical, churn, social, wom, live, ads, binge, second = h[:11]
        stage_key = str(card.get('lifecycle_stage') or '')
        if stage_key in ('discovering', 'trying') and loyal.startswith('deeply loyal'):
            loyal = 'getting attached to it fast'
        elif stage_key == 'lapsing' and loyal.startswith('deeply loyal'):
            loyal = 'still loyal to it and knows it by heart, even with the TV off lately'
        if klass == 'platform' and loyal.startswith(str(m.get('name') or m.get('subject') or '~')):
            sent.append(f"On money {pron} {price}; {loyal}.")   # "Paramount+ is her first stop", not "she is Paramount+ is"
        else:
            sent.append(f"On money {pron} {price}, and {pron} is {loyal}.")
        sent.append(f"{pron.capitalize()} {merch}, and {pron} {physical}.")
        sent.append(f"{pron.capitalize()} {churn}, {binge}, and {second}.")
        diet = []
        if card.get('streams_on'):
            diet.append(f"streams on {', '.join(_media_names(card['streams_on']))}")
        if card.get('shops_at'):
            diet.append(f"shops at {', '.join(card['shops_at'][:3])}")
        diet.append(social)
        diet.append(wom)
        sent.append(f"{pron.capitalize()} " + ', '.join(diet[:-1]) + f", and {diet[-1]}.")
        if live and 'does not' not in live:
            sent.append(f"{pron.capitalize()} {live} and {ads}.")
        else:
            sent.append(f"{pron.capitalize()} {ads}.")
    talent = [str(t.get('label') if isinstance(t, dict) else t) for t in (((twin or {}).get('identity') or {}).get('talent') or [])][:6]
    picks_t = [t for t in talent if unit('talent', sid, t) < 0.45][:2]
    if picks_t:
        sent.append(f"Online {pron} keeps up with {' and '.join(picks_t)}.")
    d1 = _HUMAN_DETAILS[int(unit('detail1', sid) * len(_HUMAN_DETAILS))]
    d2 = _HUMAN_DETAILS[int(unit('detail2', sid) * len(_HUMAN_DETAILS))]
    if d2 == d1:
        d2 = _HUMAN_DETAILS[(int(unit('detail2', sid) * len(_HUMAN_DETAILS)) + 7) % len(_HUMAN_DETAILS)]
    sent.append(f"{pron.capitalize()} {d1.replace('{pron}', pron)}, and {d2.replace('{pron}', pron)}.")
    text = ' '.join(s for s in sent if s and s.strip() not in ('.', ''))
    text = re.sub(r"\s+,", ',', re.sub(r"\s{2,}", ' ', text)).replace(' .', '.').replace(',.', '.')
    if they:
        text = re.sub(r"\b[Tt]hey is\b", 'They are', text)
        _fix = {'buys': 'buy', 'finds': 'find', 'keeps': 'keep', 'owns': 'own', 'streams': 'stream', 'shops': 'shop',
                'goes': 'go', 'pays': 'pay', 'watches': 'watch', 'cancels': 'cancel', 'trims': 'trim', 'texts': 'text',
                'talks': 'talk', 'holds': 'hold', 'works': 'work', 'has': 'have', 'earns': 'earn'}
        text = re.sub(r"\bThey (" + '|'.join(_fix) + r")\b", lambda mm: 'They ' + _fix[mm.group(1)], text)
    text = scrub(text)
    words = text.split()
    if len(words) > 200:   # trim from the end at a sentence boundary
        cut = ' '.join(words[:200])
        text = cut[:cut.rfind('.') + 1] if '.' in cut else cut
    return text


def _card_text(card: dict) -> str:
    """What the persona agent is given about itself: the bio the reader
    sees on the seat (``roster[].persona``), verbatim; the structured
    lines when a bio is missing."""
    if card.get('bio'):
        vals = ' '.join(str(card.get('values') or '').split())
        voice = str(((card.get('base') or {}).get('voice')) or '').strip()
        return (str(card['bio']) + (f"\n\nWhat people like {card.get('name') or 'this'} tend to believe and weigh: {vals}" if vals else '')
                + (f"\n\nA line someone like {card.get('name') or 'this'} might say about it: \"{voice}\"" if voice else ''))
    lines = [f"{card['name']}, {card['age']}, {card['gender']}, {card['city']}." + (f" Income {card['income']}." if card.get('income') else '') + (f" {card['education']}." if card.get('education') else ''),
             (f"{card['parental']}. " if card.get('parental') else '') + (f"{card['relationship_status']}." if card.get('relationship_status') else ''),
             f"Relationship to the subject: {card['relationship_to_subject']}; {card['how_often']}." + (f" Owns {', '.join(card['owns'])}." if card.get('owns') else ''),
             'Habits: ' + '; '.join(card.get('habits') or []) + '.',
             (f"Streams on {', '.join(card['streams_on'])}." if card.get('streams_on') else '') + (f" Shops at {', '.join(card['shops_at'])}." if card.get('shops_at') else '')]
    return '\n'.join(l for l in lines if l.strip())


def ensure_personas(session: dict, twins_by_key: dict, session_seed: str = '', *, models=None, usage_extras: Optional[dict] = None, research: bool = True) -> int:
    """Every seat in the room carries its bio (``roster[].persona``) and
    its memory list; written once, stable for the life of the room.
    The researched base persona is fetched first for any twin that does
    not carry one yet (one call per twin, kept on the twin), so the bios
    are anchored in the right kind of person. Returns the number of
    seats filled."""
    from migration.digital_twin_engine import VERSION
    seed = session_seed or f"room|{session.get('id')}|{VERSION}"
    if research:
        for tw in (twins_by_key or {}).values():
            try:
                if isinstance(tw, dict) and any(not s.get('persona') for s in ((session.get('group') or {}).get('roster') or [])):
                    persona_base(tw, models=models, usage_extras=usage_extras)
            except Exception as e:
                print(f"[twin-room] persona base skipped: {str(e)[:160]}")
    n = 0
    for seat in (session.get('group') or {}).get('roster') or []:
        if not seat.get('persona'):
            twin = twins_by_key.get(str(seat.get('twin') or '')) or next(iter(twins_by_key.values()), {})
            seat['persona'] = persona_card(twin, seat, seed)['bio']
            n += 1
        seat.setdefault('memory', [])
    return n


def refresh_memory(session: dict) -> None:
    """``roster[].memory``: what each seat said in this room so far
    (question, the option it picked, its words), for the seat panel."""
    for seat in (session.get('group') or {}).get('roster') or []:
        seat['memory'] = memory_for(session, int(seat.get('seat') or 0), limit=24)


# ------------------------------------------------------------- prompts
RESEARCH_SYSTEM = """You are the research desk for Prometheus, the focus group moderator. Before the room answers a question you gather what Prometheus needs, with the web_search tool when you have it, from approved sources only: SEC filings and earnings reports, Pew Research, Nielsen, Statista, eMarketer, YouGov, Circana (NPD) for physical media and retail sales, Box Office Mojo and The Numbers, Sensor Tower and data.ai, the platforms' own published counts, and trade press (Variety, Deadline, The Hollywood Reporter, Media Play News, Home Media Magazine) for sales history and release facts.
Gather what THIS question needs, no more:
1. The subject's facts: what it is, its status today (running, ended and when, revived), where it streams now, its physical media and merchandise history (complete-series sets, special editions, what they sold for, where).
2. How comparable things did in the past: for a boxed set or collectible, how complete-series sets of ended shows have sold at Target and Walmart, who buys them, price points, holiday skew, published unit or dollar figures; for a new season, how revivals and late seasons drew; for a price change, what comparable services did.
3. How devoted this audience is: revival or event viewing, fan events, merchandise lines, rewatch culture, the avid share Prometheus gives you.
4. Anything specific to the stimulus: the price asked, exclusivity, extras, the retailer named.
Rules: never fabricate a number, a date or a sale; a fact you cannot support goes in not_found, said plainly. Tag every fact with its source class. Prefer the most recent figure. Keep every string short.
Return JSON only:
{"subject_status": {"line": "<one sentence: what the subject is and its status today>", "ended": true|false|null, "year_started": <yyyy|null>, "year_ended": <yyyy|null>, "revival": "<what and when, or empty>", "streams_on": ["<service>", ...], "physical_media": "<what exists: complete-series DVD, Blu-ray, special editions, release years, typical price; or empty>", "merch": "<the merchandise lines and licensing that exist, or empty>"},
 "facts": [{"fact": "<one plain sentence with the number, date or finding>", "source_class": "<earnings|SEC filing|Pew|Nielsen|Statista|eMarketer|YouGov|Circana|Box Office Mojo|The Numbers|Sensor Tower|platform published|trade press|retailer listing>", "topic": "subject|comparables|fandom|stimulus", "year": <yyyy|null>}],
 "not_found": ["<what you looked for and could not support>"],
 "implications": ["<two to four sentences on what these facts mean for how this audience answers this question>"]}
At most fourteen facts; the whole answer under 1,600 tokens."""

# What the facts desk looks for depends on what the twin is built around (the
# boxed-set paragraphs above are written for a title; a room of Texas voters
# or Costco members needs its own list).
RESEARCH_CLASS_GUIDANCE = {
    'civic': ("This audience is an electorate or the residents of a place. The subject's facts are the race, measure or issue the question turns on: the candidates or sides, the offices, the date, "
              "how the race stood in the latest published polls and averages (with dates), turnout and registration, the money in the race and who spent it, endorsements, and the result when the "
              "vote has been counted or the race called (the official count or the call, the shares, the margin). Approved sources add: the state's election authority, AP race calls, the FEC, "
              "university and major-outlet polls and their averages, and the national and local news outlets that covered the race. Comparables are the last two elections for the same office."),
    'brand': ("This audience is a brand's buyers or members. The subject's facts are the brand's products, prices, stores and app, what changed lately (a price move, a recall, a launch, a fee), "
              "its published counts (members, stores, sales), and how comparable brands fared through the same kind of change."),
    'platform': ("This audience is a service's subscribers or users. The subject's facts are the service's plans and prices, what it carries, what changed lately, its published counts, and how "
                 "comparable services fared through the same kind of change. It is a service, not a show: no merchandise or disc history."),
    'talent': ("This audience is a person's following. The subject's facts are the person's work, releases and appearances, what is current, their public business (tours, deals, channels), "
               "and how comparable figures' audiences behaved through the same kind of moment."),
    'audience': ("This audience is an interest group. The subject's facts are the category's products, brands and services, what changed lately, published counts, and how comparable categories "
                 "behaved through the same kind of change."),
}

MEDIA_SYSTEM = """You are the media desk for Prometheus, the focus group moderator. Before the room answers a question you find what the people in THIS audience have actually been seeing, hearing and saying about it, with the web_search tool: news coverage of the last two weeks (national outlets and the outlets local to where this audience lives), what people are saying on X, Reddit, TikTok, Facebook groups, YouTube comments, talk radio, letters and interviews, the latest polls or published numbers, and, when the question turns on an event (an election, a premiere, a launch, a game, a verdict, a price change, an announcement), whether that event has already happened by today and how it came out.
You are given what the room already knows (the subject's facts on file, the names of the candidates, sides, titles or products in play). Use those names in your searches; never spend a search on what is already on file.
Work in this order, and spend your searches in this order:
1. Date the question. 'today' is given. Name the event the question turns on, its date, and its status: upcoming, today, or happened. When the event's date is on or before today it has happened (a question that says 'today' the day after still means that event): your FIRST search is its result, by the names on file ('<race> results <candidate> <candidate>', '<race> called'), and your second search is the count and turnout when the first did not carry them. Report the official count or the call, the shares, who won and by how much, and turnout under results. Never guess a result; a race not yet called is 'leading' with the count so far.
2. Coverage: the six to ten storylines that carried the subject in the last two weeks, each one plain sentence, with how wide it ran (dominant, common, niche), whether local outlets carried it, and how many days ago.
3. Talk: the six to ten things people in this audience were actually saying, in their own words: the recurring lines, the phrases that trended, the complaints, the pride, the jokes. Each with who says it (which part of this audience), how loud it is (dominant, common, niche) and its feeling. Include the lines that cut against the coverage, and give both sides of a theme that split the audience.
4. Polls and numbers: the latest polls or averages with their dates, turnout, money, published counts, and the result.
5. Local: three to five things the audience's own places were talking about.
Rules: never fabricate a number, a date, a quote or a result; a thing you looked for and could not support goes in not_found, said plainly. Quote people's lines the way they say them, short, no handles and no private names. Keep every string short.
Return JSON only:
{"event": {"what": "<the event the question turns on, or empty>", "date": "<yyyy-mm-dd or empty>", "status": "upcoming|today|happened|none", "outcome": "<one plain sentence on how it came out, or empty>",
  "results": [{"label": "<the side or candidate as a voter would name it, with the party in parentheses when there is one>", "share_pct": <number or null>, "status": "final|called|leading|projected"}], "turnout_pct": <number or null>},
 "polls": [{"label": "<side or candidate>", "share_pct": <number>, "as_of": "<yyyy-mm-dd>", "source_class": "<poll|average>"}],
 "coverage": [{"line": "<one plain sentence>", "reach": "dominant|common|niche", "local": true|false, "days_ago": <n>}],
 "talk": [{"line": "<what they say, in their words>", "who": "<which part of this audience>", "loudness": "dominant|common|niche", "feeling": "angry|proud|worried|hopeful|relieved|dismissive|amused|mixed"}],
 "numbers": [{"fact": "<one plain sentence with the number and its date>", "source_class": "poll|results|turnout|official|platform published|news"}],
 "local": ["<one plain sentence>"], "not_found": ["<what you looked for and could not support>"]}
At most ten coverage lines, ten talk lines, eight numbers; the whole answer under 1,900 tokens. The searches you have are enough for the result, the talk and the coverage when you use the names on file; do not stop to say the budget ran out."""

OPTIONS_SYSTEM = """You are Prometheus, a seasoned focus group moderator. Before the room answers you put the coded answer options on the board for THIS stimulus. Rules:
- Three to five options, mutually exclusive, in the room's own plain words, each one a real way a person in this audience could answer the question as asked.
- True to the facts brief. Never an option the facts rule out: for a show that ended years ago and already streams, 'wait for it to come out' or 'wait until it is free' is not an answer; for a collectible the real options turn on keepsake value, unseen material, giving it as a gift, already owning it, and price. For a show still running, options turn on when and where they would watch.
- Each option carries a lean: yes (a purchase, watch or positive intent), maybe (conditional), no (a decline), other.
- When the question kind is probe, the options are the reasons or the levers you expect to hear, tied to what the room answered on the question it follows.
- When the brief carries talk (what people in this audience have been saying), the options reflect the real reasons people give, in the words they use; a theme the talk shows splitting the audience gets both of its sides as options when the question turns on it. When the brief carries how an event came out, never an option the result rules out, and the framing says how it came out when the question turns on it.
- When the stimulus carries what_just_played (the room is watching a cut and it was paused at that clock), the question is about that moment: open the framing with the clock and the scene in plain words ('At 12:40, right after the hallway reveal: would you keep watching?'), make the options about the cut as watched (keep watching, who they are following, what confused them, what landed), never about buying or subscribing unless the question asks, and never name an actor.
- Write the framing: how you would put the question to the room in one or two sentences, giving the room the facts it should have in plain words. Never name a source; never use the words research, panel, sample, model, synthetic, estimated, confidence. No em dashes.
Return JSON only: {"options": [{"id": "a", "label": "<short label, under 12 words>", "lean": "yes|maybe|no|other"}], "framing": "<one or two sentences>", "listen_for": ["<two or three things you want to hear from the room>"]}"""

PERSONA_SYSTEM = """You are one person in a focus group, answering as yourself. The moderator is Prometheus. You are given who you are (a card), what you said earlier in this session, the facts Prometheus put in front of the room, and the question with its coded options.
Stay in character: your age, your city, who you are (orientation, education, the work the card gives you), how you came to the subject, your habits with money, merchandise and discs, your streaming life. Reason from the facts the way you would (if the show ended years ago and you can stream it any night, say what that means for you; if you own the DVDs already, say so). Answer Prometheus in two or three natural spoken sentences, concrete the way real people are (what you own, what you paid, who you would give it to), with no lists and no hedging. Never mention being an AI, a twin, a persona or a character. When this is a follow-up, stay consistent with what you said earlier. Pick exactly one option by id, or 'other' with a short label when none fits. Give a confidence from 1 (unsure) to 5 (certain) and a one-sentence reason. No em dashes.
You are also given what people like you have been hearing and saying lately (the coverage, the talk, how an event came out). Let it show the way it would in a real person: pick up the lines you agree with in your own words, push back on the ones that are not you, ignore the rest; what made people like you angry or proud is part of your answer when it fits who you are. When the event the question is about has already happened, you know how it came out and speak from after it.
When you are given what_just_played, you are in a screening and the cut was paused at that clock: answer about what you just saw, name the scene in your own words (the hallway, the diner argument), let your own dial in those seconds and what you said during them show in the answer, and never name an actor.
Return JSON only: {"option": "<id or other>", "other_label": "<when other>", "line": "<two or three sentences>", "confidence": <1 to 5>, "reason": "<one sentence>"}"""

MIRROR_SYSTEM = """You are one person in a focus group, answering as yourself. The moderator is Prometheus. You are given who you are (a card), what you said earlier in this session, the facts Prometheus put in front of the room, the question with its coded options, and the answer you are giving: in this audience a real share of people land on that option, and you are one of them.
Say in two or three natural spoken sentences, in character, why that is you: what you do, what you own, what you watch and when, the way real people talk, concrete, with no lists and no hedging. Argue the answer you were given and no other; if your card leans the other way, say what makes you the exception (the friend who got you into it, the night it is on, the money, the habit). Never mention being an AI, a twin, a persona, a character or a seat, and never say you were given or assigned an answer. When this is a follow-up, stay consistent with what you said earlier. Give a confidence from 1 (unsure) to 5 (certain) and a one-sentence reason. No em dashes.
You are also given what people like you have been hearing and saying lately; let it show the way it would in a real person.
Return JSON only: {"line": "<two or three sentences>", "confidence": <1 to 5>, "reason": "<one sentence>"}"""

READ_SYSTEM = """You are Prometheus, the moderator, writing up the room for a senior executive. Plain English a high school student follows; the thinking is senior consultant. You are given the question, the options with the room's split (the published share of the whole audience in percent and people, and the raw tally of the seats in the room, both already computed; never change a number), a headline, the split by segment, every seat's answer, the facts brief and a prior note.
The published share is the count: the whole audience, in percent and people. The seats in the room are allocated to that count (a share worth half a seat or more holds a seat), so the tally and the count tell one story; the seats are the voices. People always come from the share, never from the tally: 'about one in twenty, roughly 329K people' is right, '0 people' is wrong unless the count itself is zero. Say shares in plain words first ('about two in three', 'about one in ten', 'about one in twenty', 'a sliver') and cite the raw count second when it helps ('one of the twelve seats'); never print a percentage with a decimal in the read.
State one split, once, and never contradict it: when the count gives a side a share, never write nobody, no one, none, not one, zero or 0 for that side, and never write all twelve or every seat when a seat sits elsewhere.
Write:
- read: one paragraph, three to five sentences, on what the room said and why, grounded in the facts and the comparables in plain words. Open with the headline's fact in your own words (the share in plain words, the people it comes to, then the seats). When the brief carries how an event came out, say so as fact in the first sentence and read the seats against it: the published split follows the count, the twelve seats are twelve people, so say what they show that the count does not. When the brief carries talk, name the loudest lines people used, in plain words. Say what the share means (who these people are, what it would take); when a small side holds a seat, that seat's own words are what makes the small share real. Vary the sentence shapes. Never name a source or a publication; never use the words research, model, modeled, synthetic, estimated, panel, sample, confidence, pipeline, data. If the prior note says the seats departed from the audience's own leaning, explain what accounts for it in the read rather than overriding the room. When the question was asked on the clock (the stimulus carries what_just_played), open the read with what the room said (the share in words and the reason that carried it), never with the clock or the scene; the reader is looking at that moment already. Keep the read about that moment and the seats' own dials.
- chat: the read for the room's chat, two or three sentences: the headline's share in words and people, and the one reason that carried it. No clock, no scene, no preamble; it opens with what the room said.
- voices: the seat ids of the three or four most telling answers, spread across the options in proportion to the split.
- probes_next: two follow-up questions you would ask this room next, in your own words as the moderator.
- facts_used: two to five one-line facts the read leans on, in plain words with no source names.
No em dashes. Return JSON only: {"read": "...", "chat": "...", "voices": [<seat ids>], "probes_next": ["...", "..."], "facts_used": ["..."]}"""


# ---------------------------------------------------------- the stages
def facts_lines(brief: dict, limit: int = 14) -> list:
    """What the room knew, in order: how the event came out (when it
    has), the subject's status, the facts, then the media desk's
    numbers."""
    out = []
    ev = (brief or {}).get('event') or {}
    if isinstance(ev, dict) and str(ev.get('status') or '') == 'happened' and str(ev.get('outcome') or '').strip():
        out.append(str(ev['outcome']).strip())
    st = (brief or {}).get('subject_status') or {}
    if isinstance(st, dict) and st.get('line'):
        out.append(str(st['line']))
    for f in (brief or {}).get('facts') or []:
        if isinstance(f, dict) and f.get('fact'):
            out.append(str(f['fact']))
        elif isinstance(f, str):
            out.append(f)
    seen, uniq = set(), []
    for x in out:
        k = re.sub(r'[^a-z0-9]+', ' ', x.lower()).strip()
        if k and k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq[:limit]


_LOUD_WORD = {'dominant': 'heard everywhere', 'common': 'heard often', 'niche': 'heard in pockets'}


def talk_lines(brief: dict, limit: int = 8) -> list:
    """What people were saying, as plain lines for the room and the
    page: the line in their words, who says it, how loud."""
    out = []
    for t in (brief or {}).get('talk') or []:
        if not isinstance(t, dict) or not str(t.get('line') or '').strip():
            continue
        line = str(t['line']).strip().strip('"').strip()
        who = str(t.get('who') or '').strip()
        loud = _LOUD_WORD.get(str(t.get('loudness') or '').lower(), '')
        tail = '; '.join(x for x in (who, loud) if x)
        out.append(f"\u201c{line}\u201d" + (f" ({tail})" if tail else ''))
    return out[:limit]


def coverage_lines(brief: dict, limit: int = 6) -> list:
    out = []
    for c in (brief or {}).get('coverage') or []:
        if isinstance(c, dict) and str(c.get('line') or '').strip():
            out.append(str(c['line']).strip())
        elif isinstance(c, str) and c.strip():
            out.append(c.strip())
    return out[:limit]


def _norm_media(data: dict) -> dict:
    """The media desk's answer, shaped and bounded; {} when it carries
    nothing the room can use."""
    if not isinstance(data, dict):
        return {}
    ev = data.get('event') if isinstance(data.get('event'), dict) else {}
    results = []
    for r in (ev.get('results') or []):
        if isinstance(r, dict) and str(r.get('label') or '').strip():
            try:
                share = float(r['share_pct']) if r.get('share_pct') not in (None, '') else None
            except (TypeError, ValueError):
                share = None
            results.append({'label': str(r['label']).strip()[:80], 'share_pct': share, 'status': str(r.get('status') or '').lower()[:12]})
    status = str(ev.get('status') or 'none').lower()
    if status not in ('upcoming', 'today', 'happened', 'none'):
        status = 'none'
    try:
        turnout = float(ev['turnout_pct']) if ev.get('turnout_pct') not in (None, '') else None
    except (TypeError, ValueError):
        turnout = None
    event = {'what': str(ev.get('what') or '').strip()[:160], 'date': str(ev.get('date') or '').strip()[:10], 'status': status,
             'outcome': scrub(str(ev.get('outcome') or '').strip())[:300], 'results': results[:8], 'turnout_pct': turnout}
    polls = []
    for r in (data.get('polls') or []):
        if isinstance(r, dict) and str(r.get('label') or '').strip() and r.get('share_pct') not in (None, ''):
            try:
                polls.append({'label': str(r['label']).strip()[:80], 'share_pct': float(r['share_pct']), 'as_of': str(r.get('as_of') or '')[:10], 'source_class': str(r.get('source_class') or '')[:20]})
            except (TypeError, ValueError):
                continue
    # the latest reading per side: an older poll never adds to a newer one
    polls.sort(key=lambda r: r.get('as_of') or '', reverse=True)
    seen_sides, latest = set(), []
    for r in polls:
        k = ' '.join(sorted(_label_tokens(r['label']))) or r['label'].lower()
        if k in seen_sides:
            continue
        seen_sides.add(k)
        latest.append(r)
    polls = latest
    coverage = [{'line': scrub(str(c.get('line')).strip())[:220], 'reach': str(c.get('reach') or '')[:10], 'local': bool(c.get('local')), 'days_ago': c.get('days_ago')}
                for c in (data.get('coverage') or []) if isinstance(c, dict) and str(c.get('line') or '').strip()][:10]
    talk = [{'line': scrub(str(t.get('line')).strip())[:200], 'who': scrub(str(t.get('who') or '').strip())[:80], 'loudness': str(t.get('loudness') or '')[:10], 'feeling': str(t.get('feeling') or '')[:12]}
            for t in (data.get('talk') or []) if isinstance(t, dict) and str(t.get('line') or '').strip()][:10]
    numbers = [{'fact': scrub(str(n.get('fact')).strip())[:240], 'source_class': str(n.get('source_class') or 'news')[:24], 'topic': 'media'}
               for n in (data.get('numbers') or []) if isinstance(n, dict) and str(n.get('fact') or '').strip()][:8]
    local = [scrub(str(x).strip())[:200] for x in (data.get('local') or []) if str(x).strip()][:5]
    not_found = [str(x).strip()[:160] for x in (data.get('not_found') or []) if str(x).strip()][:6]
    if not (coverage or talk or numbers or event['outcome'] or results or polls):
        return {}
    return {'event': event, 'polls': polls[:8], 'coverage': coverage, 'talk': talk, 'numbers': numbers, 'local': local, 'not_found': not_found}


def media_desk(models: Models, twin: dict, q: str, kind: str, stimulus: Optional[dict], s3, *, force: bool = False, log=None, known: Optional[list] = None) -> dict:
    """What the audience has been seeing, hearing and saying about this
    question, and how the event it turns on came out (Jenna 2026-11-04:
    the room predicted a win the day after the loss, and never heard the
    'democracy is not for sale' line that ran through the state). Cached
    per subject + topic + day; {} when nothing could be gathered."""
    subject = str(_meta(twin).get('subject') or _twin_name(twin))
    today = time.strftime('%Y-%m-%d', time.gmtime())
    mkey = media_key(subject, q, today)
    cached = _get_json(s3, mkey) if (s3 is not None and not force) else None
    if isinstance(cached, dict) and cached.get('media') and (time.time() - float(cached.get('at') or 0)) < MEDIA_CACHE_TTL_S:
        media = dict(cached['media'])
        media['_cached'] = True
        media['_key'] = mkey
        return media
    tb = _twin_brief(twin)
    facts_doc = _get_json(s3, facts_key(subject)) if s3 is not None else None
    on_file = []
    if isinstance(facts_doc, dict):
        st = facts_doc.get('subject_status') if isinstance(facts_doc.get('subject_status'), dict) else {}
        if st.get('line'):
            on_file.append(str(st['line']))
        on_file += [str(f.get('fact')) for f in (facts_doc.get('facts') or []) if isinstance(f, dict) and f.get('fact')][:8]
    on_file += [str(x) for x in (known or []) if str(x).strip()]
    user = json.dumps({'question': q, 'kind': kind, 'stimulus': stimulus or {}, 'today': today,
                       'audience': {'name': tb['name'], 'subject': tb['subject'], 'kind': tb['kind'], 'class': subject_class(twin), 'size_us': tb['size_us'],
                                    'where_they_live': tb.get('identity_read'), 'demographics': {k: v for k, v in (tb.get('demographics') or {}).items() if k in ('age', 'gender', 'ethnicity', 'income', 'location', 'party')}},
                       'what_the_room_already_knows': list(dict.fromkeys(on_file))[:14], 'anchors_on_file': tb.get('anchors')},
                      default=str)[:7000]
    data, call = models.call('media', MEDIA_SYSTEM, user, max_tokens=6500, temperature=0.2, tools=None)   # the search rounds' interim text rides inside the budget; the brief itself stays under 1,900 tokens
    if log:
        log('media', call)
    media = _norm_media(data)
    if not media:
        return {}
    media['_key'] = mkey
    if s3 is not None:
        _put_json(s3, mkey, {'subject': subject, 'question': q, 'topic_hash': topic_hash(q, subject), 'day': today, 'at': time.time(),
                             'media': {k: v for k, v in media.items() if not k.startswith('_')}, 'version': ROOM_VERSION})
    return media


def merge_media(brief: dict, media: dict) -> dict:
    """The media desk's brief folded into the facts brief: the event, the
    polls, the coverage, the talk, the local lines; its numbers join the
    facts under the media topic."""
    out = dict(brief or {})
    if not media:
        return out
    out['event'] = media.get('event') or {}
    out['polls'] = list(media.get('polls') or [])
    out['coverage'] = list(media.get('coverage') or [])
    out['talk'] = list(media.get('talk') or [])
    out['local'] = list(media.get('local') or [])
    facts = [f for f in (out.get('facts') or []) if not (isinstance(f, dict) and f.get('topic') == 'media')]
    have = {re.sub(r'[^a-z0-9]+', ' ', str(f.get('fact') if isinstance(f, dict) else f).lower()).strip() for f in facts}
    for n in media.get('numbers') or []:
        k = re.sub(r'[^a-z0-9]+', ' ', str(n.get('fact') or '').lower()).strip()
        if k and k not in have:
            facts.append(dict(n))
            have.add(k)
    out['facts'] = facts[:20]
    nf = list(out.get('not_found') or [])
    for x in media.get('not_found') or []:
        if x not in nf:
            nf.append(x)
    out['not_found'] = nf[:10]
    out['_media_cached'] = bool(media.get('_cached'))
    out['_media_key'] = media.get('_key')
    return out


def research(models: Models, twin: dict, q: str, kind: str, stimulus: Optional[dict], s3, *, force: bool = False, log=None) -> dict:
    """The facts brief for this question: cached per subject + topic;
    the subject facts cache rides in and is written when new. {} when
    nothing could be gathered (the moderator reasons from the twin)."""
    subject = str(_meta(twin).get('subject') or _twin_name(twin))
    rkey = research_key(subject, q)
    fkey = facts_key(subject)
    cached = _get_json(s3, rkey) if (s3 is not None and not force) else None
    ttl = 86400.0 if subject_class(twin) == 'civic' else RESEARCH_CACHE_TTL_S   # a race's facts turn over by the day
    if isinstance(cached, dict) and cached.get('brief') and (time.time() - float(cached.get('at') or 0)) < ttl:
        brief = dict(cached['brief'])
        brief['_cached'] = True
        brief['_key'] = rkey
        return brief
    facts_doc = _get_json(s3, fkey) if s3 is not None else None
    tb = _twin_brief(twin)
    user = json.dumps({'question': q, 'kind': kind, 'stimulus': stimulus or {}, 'today': time.strftime('%Y-%m-%d', time.gmtime()),
                       'subject': {'name': tb['name'], 'subject': tb['subject'], 'kind': tb['kind'], 'format': tb['format'], 'stage': tb['stage'], 'platform_home': tb['platform_home'],
                                   'size_us': tb['size_us'], 'avid_share_pct': tb['avid_share_pct'], 'window': tb['window']},
                       'subject_facts_on_file': (facts_doc or {}).get('subject_status') if isinstance(facts_doc, dict) else None,
                       'audience_read': tb.get('identity_read'), 'lifecycle': tb.get('lifecycle_read'), 'anchors_on_file': tb.get('anchors')}, default=str)[:7000]
    klass = subject_class(twin)
    system = RESEARCH_SYSTEM + ("\n" + RESEARCH_CLASS_GUIDANCE[klass] if klass in RESEARCH_CLASS_GUIDANCE else '')
    data, call = models.call('research', system, user, max_tokens=5000, temperature=0.2, tools=None)   # room for the search rounds' interim text; the brief itself stays under 1,600 tokens
    if log:
        log('research', call)
    if not isinstance(data, dict) or not (data.get('facts') or data.get('subject_status')):
        if isinstance(facts_doc, dict) and facts_doc.get('subject_status'):
            return {'subject_status': facts_doc['subject_status'], 'facts': facts_doc.get('facts') or [], 'not_found': ['the record for this question could not be gathered this time'],
                    'implications': [], '_key': rkey, '_from_facts_cache': True}
        return {}
    brief = {'subject_status': data.get('subject_status') if isinstance(data.get('subject_status'), dict) else {},
             'facts': [f for f in (data.get('facts') or []) if isinstance(f, dict) and f.get('fact')][:16],
             'not_found': [str(x) for x in (data.get('not_found') or [])][:8], 'implications': [str(x) for x in (data.get('implications') or [])][:5], '_key': rkey}
    if s3 is not None:
        _put_json(s3, rkey, {'subject': subject, 'question': q, 'topic_hash': topic_hash(q, subject), 'at': time.time(), 'brief': {k: v for k, v in brief.items() if not k.startswith('_')}, 'version': ROOM_VERSION})
        subj_facts = [f for f in brief['facts'] if str(f.get('topic') or '') == 'subject']
        if brief['subject_status'] or subj_facts:
            _put_json(s3, fkey, {'subject': subject, 'slug': _slug(subject), 'subject_status': brief['subject_status'] or (facts_doc or {}).get('subject_status'),
                                 'facts': subj_facts or (facts_doc or {}).get('facts') or [], 'updated_at': _now(), 'version': ROOM_VERSION})
    return brief


def _default_options(kind: str, opts: list) -> list:
    if kind == 'rating':
        return [{'id': chr(97 + i), 'label': f"{s} star{'s' if s != 1 else ''}", 'lean': ('yes' if s >= 4 else 'maybe' if s == 3 else 'no')} for i, s in enumerate(range(1, 6))]
    if kind == 'ab':
        a, b = (list(opts) + ['A', 'B'])[:2]
        return [{'id': 'a', 'label': str(a), 'lean': 'yes'}, {'id': 'b', 'label': str(b), 'lean': 'yes'}, {'id': 'c', 'label': 'Neither', 'lean': 'no'}]
    if kind == 'multiple_choice' and len(opts) >= 2:
        return [{'id': chr(97 + i), 'label': str(o), 'lean': 'other'} for i, o in enumerate(opts[:MAX_OPTIONS + 1])]
    if kind in ('yes_no', 'show_of_hands', 'price'):
        return [{'id': 'a', 'label': 'Yes', 'lean': 'yes'}, {'id': 'b', 'label': 'Maybe, it depends', 'lean': 'maybe'}, {'id': 'c', 'label': 'No', 'lean': 'no'}]
    return [{'id': 'a', 'label': 'Yes, I would', 'lean': 'yes'}, {'id': 'b', 'label': 'Only on the right terms', 'lean': 'maybe'}, {'id': 'c', 'label': 'Not for me', 'lean': 'no'}]


_RULED_OUT_RX = re.compile(r"\bwait(?:ing)?\b.*\b(free|comes? out|released?|streaming|drops?|available)\b|\buntil it(?:'s| is)? free\b|\bwhen it (?:is|comes) (?:out|free)\b", re.I)


def drop_ruled_out(options: list, brief: dict) -> list:
    """An option the facts rule out never reaches the room: when the
    subject has ended and already streams, 'wait for it to be free' is
    not an answer."""
    st = (brief or {}).get('subject_status') or {}
    ended = bool(isinstance(st, dict) and (st.get('ended') is True or st.get('year_ended')))
    streams = bool(isinstance(st, dict) and st.get('streams_on'))
    if not (ended or streams):
        return options
    kept = [o for o in options if not _RULED_OUT_RX.search(str(o.get('label') or ''))]
    return kept if len(kept) >= 2 else options


def moderator_options(models: Models, twin: dict, q: str, kind: str, opts: list, stimulus: Optional[dict], brief: dict, last: Optional[dict], log=None) -> dict:
    """{options: [{id, label, lean}], framing, listen_for, fallback, fixed}."""
    fixed = _default_options(kind, opts)
    if kind in ('rating', 'ab') or (kind == 'multiple_choice' and len(opts) >= 2):
        return {'options': fixed, 'framing': '', 'listen_for': [], 'fallback': False, 'fixed': True}
    tb = _twin_brief(twin)
    stim_view = ({'what_just_played': _moment_view(stimulus), 'cut': stimulus.get('cut') or {}} if _in_cut(stimulus) else (stimulus or {}))
    user = json.dumps({'question': q, 'kind': kind, 'stimulus': stim_view, 'supplied_options': opts,
                       'follows': ({'question': last.get('q'), 'options': last.get('option_labels') or last.get('options'),
                                    'split': [{'label': r.get('label'), 'pct': r.get('pct')} for r in (last.get('distribution') or [])[:6]]} if last else None),
                       'audience': {'name': tb['name'], 'format': tb['format'], 'stage': tb['stage'], 'size_us': tb['size_us'], 'avid_share_pct': tb['avid_share_pct'],
                                    'lifecycle': tb['lifecycle_read'], 'read': tb['identity_read'], 'traits': {k: v.get('read') for k, v in tb['traits'].items()}},
                       'facts_brief': {k: v for k, v in (brief or {}).items() if not k.startswith('_')}}, default=str)[:9000]
    data, call = models.call('moderator', OPTIONS_SYSTEM, user, max_tokens=1400, temperature=0.3, tools=None)
    if log:
        log('options', call)
    out = []
    if isinstance(data, dict):
        for o in (data.get('options') or [])[:MAX_OPTIONS]:
            if isinstance(o, dict) and str(o.get('label') or '').strip():
                lean = str(o.get('lean') or 'other').lower()
                out.append({'id': '', 'label': scrub(str(o['label']).strip().rstrip('.'))[:80], 'lean': lean if lean in ('yes', 'maybe', 'no', 'other') else 'other'})
    out = drop_ruled_out(out, brief)
    if len(out) < 2:
        out = drop_ruled_out(fixed, brief)
        for i, o in enumerate(out):
            o['id'] = chr(97 + i)
        return {'options': out, 'framing': '', 'listen_for': [], 'fallback': True, 'fixed': False}
    for i, o in enumerate(out):
        o['id'] = chr(97 + i)
    return {'options': out, 'framing': scrub(str((data or {}).get('framing') or ''))[:400], 'listen_for': [scrub(str(x)) for x in ((data or {}).get('listen_for') or [])][:3],
            'fallback': False, 'fixed': False}


def memory_for(session: dict, seat_no: int, limit: int = 6) -> list:
    """What this seat said earlier in the room: the persona memory."""
    out = []
    for rec in (session.get('questions') or []):
        for a in rec.get('answers') or []:
            if int(a.get('seat') or 0) == int(seat_no) and a.get('line'):
                out.append({'question': rec.get('q'), 'answered': a.get('option_label') or a.get('option'), 'said': a.get('line'), 'reason': a.get('reason')})
    return out[-limit:]


def _in_cut(stimulus: Optional[dict]) -> bool:
    return bool(isinstance(stimulus, dict) and stimulus.get('in_cut') and isinstance(stimulus.get('window'), dict))


def _moment_view(stimulus: Optional[dict]) -> Optional[dict]:
    """The moment for the moderator: the clock, the scene on screen, the
    last minute of beats, the top events, the room's dial."""
    if not _in_cut(stimulus):
        return None
    win = dict(stimulus.get('window') or {})
    return {'clock': win.get('clock'), 'from_clock': win.get('from_clock'), 'frame': win.get('frame'), 'on_screen_now': (win.get('scene') or {}).get('on_screen'),
            'dialogue_now': (win.get('scene') or {}).get('dialogue'),
            'the_last_minute': [{'clock': b.get('clock'), 'on_screen': b.get('on_screen'), 'dialogue': b.get('dialogue'), 'room_dial': b.get('dial')} for b in (win.get('beats') or [])[-12:]],
            'what_the_room_did': win.get('events') or [], 'room_dial_now': win.get('dial_now'), 'room_dial_last_minute': win.get('dial_window_mean'),
            'the_cut': stimulus.get('cut') or {}}


def _moment_for_seat(stimulus: Optional[dict], seat_no) -> Optional[dict]:
    """What just played, with this seat's own dial in those seconds
    (Pilot Testing questions on the clock)."""
    if not _in_cut(stimulus):
        return None
    win = dict(stimulus.get('window') or {})
    mine = (stimulus.get('seat_windows') or {}).get(str(seat_no)) or {}
    return {'clock': win.get('clock'), 'from_clock': win.get('from_clock'), 'on_screen_now': (win.get('scene') or {}).get('on_screen'),
            'the_last_minute': [{'clock': b.get('clock'), 'on_screen': b.get('on_screen'), 'dialogue': b.get('dialogue')} for b in (win.get('beats') or [])[-10:]],
            'your_dial_in_these_seconds': {'now': mine.get('dial_now'), 'a_minute_ago': mine.get('dial_start'), 'moved': mine.get('moved')},
            'what_you_said_in_these_seconds': mine.get('events') or [], 'the_cut': stimulus.get('cut') or {}}


def _seat_body(card: dict, q: str, framing: str, options: list, brief: dict, memory: list, stimulus: Optional[dict]) -> dict:
    """What a seat is handed: its card, its memory, the facts, the talk,
    how the event came out, the moment on the clock, the question and
    the board."""
    body = {'you': _card_text(card), 'earlier_in_this_session': memory,
            'facts_the_moderator_gave_the_room': facts_lines(brief, 12), 'what_could_not_be_confirmed': (brief or {}).get('not_found') or [],
            'moderator': framing or q, 'question': q, 'options': [{'id': o['id'], 'label': o['label']} for o in options]}
    ev = (brief or {}).get('event') or {}
    if isinstance(ev, dict) and str(ev.get('status') or '') == 'happened' and ev.get('outcome'):
        body['how_it_came_out'] = {'what': ev.get('what'), 'date': ev.get('date'), 'outcome': ev.get('outcome')}
    cov, talk, local = coverage_lines(brief, 6), talk_lines(brief, 8), list((brief or {}).get('local') or [])[:4]
    if cov or talk or local:
        body['what_people_like_you_have_been_hearing_and_saying'] = {'in_the_news': cov, 'what_people_say': talk, 'closer_to_home': local}
    moment = _moment_for_seat(stimulus, card.get('seat'))
    if moment:
        body['what_just_played'] = moment
    return body


def seat_answer(models: Models, card: dict, q: str, framing: str, options: list, brief: dict, memory: list, seed: str, log=None, stimulus: Optional[dict] = None) -> dict:
    """One seat's answer in character; the deterministic fallback when
    the model is late or off."""
    body = _seat_body(card, q, framing, options, brief, memory, stimulus)
    user = json.dumps(body, default=str)[:9000]
    data, call = models.call('persona', PERSONA_SYSTEM, user, max_tokens=420, temperature=0.85, tools=None)
    if log:
        log('persona', call)
    ids = {o['id']: o for o in options}
    if isinstance(data, dict) and str(data.get('line') or '').strip():
        oid = str(data.get('option') or '').strip().lower()
        if oid not in ids and oid != 'other':
            hit = next((o for o in options if str(o['label']).lower() == oid), None)
            oid = hit['id'] if hit else 'other'
        try:
            conf = int(clamp(int(float(data.get('confidence') or 3)), 1, 5))
        except (TypeError, ValueError):
            conf = 3
        other = scrub(str(data.get('other_label') or ''))[:60] if oid == 'other' else ''
        return {'seat': card['seat'], 'name': card['name'], 'option': oid, 'option_label': (ids[oid]['label'] if oid in ids else (other or 'Other')),
                'line': scrub(str(data['line']).strip())[:520], 'confidence': conf, 'reason': scrub(str(data.get('reason') or '').strip())[:240], 'fallback': False}
    u = unit('fallback', seed, card['seat'])
    habits = ' '.join(card.get('habits') or [])
    pos = 0.45 if 'deeply loyal' in habits else 0.3
    if 'very price-conscious' in habits:
        pos -= 0.12
    leans = {k: [o for o in options if o['lean'] == k] for k in ('yes', 'maybe', 'no', 'other')}
    pool = (leans['yes'] if u < pos else leans['maybe'] if u < pos + 0.25 else leans['no']) or leans['other'] or options
    o = pool[int(unit('fb-opt', seed, card['seat']) * len(pool)) % len(pool)]
    return {'seat': card['seat'], 'name': card['name'], 'option': o['id'], 'option_label': o['label'], 'line': f"{o['label']}. That is where I land on this one.",
            'confidence': 2, 'reason': 'answered from habit', 'fallback': True}


ALPHA_SEATS = 1.0          # the prior's weight: one seat's worth of the room (a pseudo-count), on top of the seats' own doubt
OTHER_PRIOR_FLOOR = 0.02   # an option the prior has no view on still gets a sliver


def _conf_factor(c) -> float:
    """How much of a seat's people its own vote carries: 0.8 at
    confidence 1, 1.0 at 5. The rest of the seat's people follow the
    audience's prior (an unsure seat leaves room for it)."""
    try:
        c = int(clamp(int(float(c or 3)), 1, 5))
    except (TypeError, ValueError):
        c = 3
    return 0.75 + 0.05 * c


def prior_over_options(twin: dict, q: str, kind: str, options: list, stimulus: Optional[dict], last: Optional[dict], seed: str) -> dict:
    """The deterministic engine's distribution over the moderator's
    options: {option_id: share} summing to 1. The engine's rows carry
    lean masses (yes / maybe / no, or stars, or the A/B sides, or the
    supplied choices); each lean's mass spreads over the options with
    that lean, tilted by the engine's keyword affinity."""
    from migration import digital_twin_panel as P
    ids = [o['id'] for o in options]
    if not ids:
        return {}
    uniform = {i: 1.0 / len(ids) for i in ids}
    try:
        aff = lambda label: max(0.4, 1.0 + P._affinity(twin, label))
        out = {}
        if kind == 'rating' and len(options) == 5:
            d = P.distribution(twin, q, 'rating', [], 1000, seed, stimulus, last)
            rows = d.get('rows') or []
            out = {o['id']: float(rows[i]['pct']) / 100.0 for i, o in enumerate(options) if i < len(rows)}
        elif kind == 'ab' and len(options) >= 2:
            d = P.distribution(twin, q, 'ab', [options[0]['label'], options[1]['label']], 1000, seed, stimulus, last)
            rows = d.get('rows') or []
            out = {o['id']: float(rows[i]['pct']) / 100.0 for i, o in enumerate(options) if i < len(rows)}
        elif kind == 'multiple_choice' and all(o.get('lean') == 'other' for o in options):
            d = P.distribution(twin, q, 'multiple_choice', [o['label'] for o in options], 1000, seed, stimulus, last)
            by_label = {r['label']: float(r['pct']) / 100.0 for r in d.get('rows') or []}
            out = {o['id']: by_label.get(o['label'], 0.0) for o in options}
        else:
            if kind == 'probe':
                mass = {'yes': 0.45, 'maybe': 0.2, 'no': 0.35}
            else:
                d = P.distribution(twin, q, 'price' if kind == 'price' else 'yes_no', [], 1000, seed, stimulus, last)
                rows = d.get('rows') or []
                p = [float(r['pct']) / 100.0 for r in rows]
                if kind == 'price' and len(p) >= 3:
                    mass = {'yes': p[0], 'maybe': p[1], 'no': p[2]}
                elif len(p) >= 3:
                    mass = {'yes': p[0], 'maybe': p[1], 'no': p[2]}
                elif len(p) == 2:
                    mass = {'yes': p[0], 'maybe': 0.0, 'no': p[1]}
                else:
                    mass = {'yes': 0.4, 'maybe': 0.2, 'no': 0.4}
            leans = {k: [o for o in options if o.get('lean') == k] for k in ('yes', 'maybe', 'no')}
            if not leans['maybe'] and mass['maybe'] > 0:
                mass['yes'] += mass['maybe'] / 2.0
                mass['no'] += mass['maybe'] / 2.0
                mass['maybe'] = 0.0
            if leans['maybe'] and mass['maybe'] <= 0:
                carve = 0.25 * (mass['yes'] + mass['no'])
                mass['yes'] *= 0.75
                mass['no'] *= 0.75
                mass['maybe'] = carve
            for k in ('yes', 'maybe', 'no'):
                if not leans[k]:
                    continue
                w = {o['id']: aff(o['label']) for o in leans[k]}
                tot = sum(w.values()) or 1.0
                for o in leans[k]:
                    out[o['id']] = mass[k] * w[o['id']] / tot
            orphan = [o for o in options if o['id'] not in out]
            if orphan and not out:
                out = dict(uniform)
            elif orphan:
                for o in orphan:
                    out[o['id']] = 0.0
        tot = sum(out.values())
        if tot <= 0:
            return uniform
        out = {k: v / tot for k, v in out.items()}
        # a sliver for every option the prior has no view on, so nothing in the room can print a hard zero
        low = [k for k in ids if out.get(k, 0.0) < OTHER_PRIOR_FLOOR]
        if low:
            need = sum(OTHER_PRIOR_FLOOR - out.get(k, 0.0) for k in low)
            rest = sum(out[k] for k in ids if k not in low) or 1.0
            out = {k: (OTHER_PRIOR_FLOOR if k in low else out[k] * (1 - need / rest)) for k in ids}
        return out
    except Exception:
        return uniform


def _published_rows(rows: list, total_people: int, n_seats: int, seed: str):
    """Round the shares to one decimal summing to 100.0 and the people to
    integers summing to the total, with no row at 0.0% and no row on a
    bare k/n multiple of the seats, and people counts that end the way
    real counts do."""
    if not rows:
        return
    n = len(rows)
    k_max = max(range(n), key=lambda i: float(rows[i].get('pct') or 0.0))
    pcts = [round(float(r.get('pct') or 0.0), 1) for r in rows]
    multiples = {round(100.0 * k / n_seats, 1) for k in range(0, n_seats + 1)} if n_seats else set()
    for i in range(n):
        if i == k_max:
            continue
        if pcts[i] < 0.1:
            pcts[i] = 0.1 if unit('zero', seed, i) < 0.6 else 0.2
        if pcts[i] in multiples:
            pcts[i] = round(pcts[i] + (0.1 if unit('mult', seed, i) < 0.5 or pcts[i] <= 0.1 else -0.1), 1)
    pcts[k_max] = round(100.0 - sum(p for i, p in enumerate(pcts) if i != k_max), 1)
    if pcts[k_max] in multiples and n > 1:
        j = max((i for i in range(n) if i != k_max), key=lambda i: pcts[i])
        pcts[k_max] = round(pcts[k_max] - 0.1, 1)
        pcts[j] = round(pcts[j] + 0.1, 1)
    counts = [int(round(p / 100.0 * total_people)) for p in pcts]
    if total_people >= 2 * n:
        counts = [max(1, c) if i != k_max else c for i, c in enumerate(counts)]
    counts[k_max] = total_people - sum(c for i, c in enumerate(counts) if i != k_max)
    for i in range(n):
        if i != k_max and counts[i] > 0 and counts[i] % 10 == 0:
            d = 1 + int(unit('digit', seed, i) * 8)
            counts[i] += d
            counts[k_max] -= d
    if counts[k_max] % 10 == 0 and n > 1:
        j = max((i for i in range(n) if i != k_max), key=lambda i: counts[i])
        counts[k_max] -= 1
        counts[j] += 1
    for r, p, c in zip(rows, pcts, counts):
        r['pct'] = max(0.0, p)
        r['people'] = max(0, int(c))


def weigh(answers: list, options: list, roster: list, segments: list, priors: Optional[dict] = None, seed: str = '') -> tuple:
    """The seat votes smoothed with the audience's prior: (options with
    the published share, people, the raw tally and the seat ids;
    by_segment rows). Each seat's people carry its vote by its
    confidence; the rest of its people, plus one seat's worth of the
    room, follow the prior (``priors`` = {segment label: {option id:
    share}}), so the seats drive the shape and the prior fills the
    tails. Shares sum to 100.0 and people to the room, in each segment
    too; the room rows are the sum of the segments."""
    seg_people = {s['label']: int(s.get('people') or 0) for s in segments}
    seg_seats = {}
    for r in roster:
        seg_seats.setdefault(r['segment'], []).append(r)
    n_total = len(roster) or 1
    by_seat = {int(a['seat']): a for a in answers}
    ids = [o['id'] for o in options]
    others = sorted(int(a['seat']) for a in answers if a.get('option') not in ids)
    rows_spec = [dict(o) for o in options]
    if others:
        rows_spec.append({'id': 'other', 'label': 'Other', 'lean': 'other'})
    row_ids = [o['id'] for o in rows_spec]
    room_people = {i: 0 for i in row_ids}
    by_segment = []
    for s in segments:
        rs = seg_seats.get(s['label']) or []
        n_seg = len(rs)
        w_seg = float(seg_people.get(s['label']) or 0)
        w_seat = (w_seg / n_seg) if n_seg else 0.0
        prior = dict((priors or {}).get(s['label']) or {})
        if others:
            prior.setdefault('other', 0.0)
        if not prior or sum(prior.values()) <= 0:
            prior = {i: 1.0 / len(row_ids) for i in row_ids}
        tot_p = sum(prior.get(i, 0.0) for i in row_ids) or 1.0
        prior = {i: prior.get(i, 0.0) / tot_p for i in row_ids}
        alpha = ALPHA_SEATS * w_seg / n_total
        mass = {i: 0.0 for i in row_ids}
        residual = alpha
        for r in rs:
            a = by_seat.get(int(r['seat']))
            if a is None:
                residual += w_seat
                continue
            f = _conf_factor(a.get('confidence'))
            oid = a.get('option') if a.get('option') in ids else 'other'
            mass[oid] += w_seat * f
            residual += w_seat * (1.0 - f)
        for i in row_ids:
            mass[i] += prior[i] * residual
        denom = (w_seg + alpha) or 1.0
        rows = [{'label': o['label'], 'lean': o.get('lean') or 'other', 'pct': mass[o['id']] / denom * 100.0, 'people': mass[o['id']] / denom * w_seg} for o in rows_spec]
        _published_rows(rows, int(w_seg), n_seg, f"{seed}|{s['label']}")
        by_segment.append({'segment': s['label'], 'people': int(w_seg), 'distribution': rows,
                           'yes_pct': 0.0,
                           'tally': [{'label': o['label'], 'seats': len([r for r in rs if (by_seat.get(int(r['seat'])) or {}).get('option') == o['id'] or (o['id'] == 'other' and int(r['seat']) in others)])} for o in rows_spec]})
    total = sum(seg_people.values()) or 1
    _room_from_segments(by_segment, len(rows_spec), int(total), n_total, seed)
    opts_out = []
    for i, o in enumerate(rows_spec):
        seats = sorted(int(a['seat']) for a in answers if (a.get('option') == o['id']) or (o['id'] == 'other' and int(a['seat']) in others))
        ppl = sum(int(sr['distribution'][i]['people']) for sr in by_segment)
        opts_out.append({'id': o['id'], 'label': o['label'], 'lean': o.get('lean') or 'other', 'pct': 0.0, 'people': ppl, 'seats': seats,
                         'seats_n': len(seats), 'tally': f"{len(seats)} of {n_total}", 'room_pct': round(len(seats) / n_total * 100.0, 1)})
    _pcts_from_people(opts_out, int(total))
    for sr in by_segment:
        sr['distribution'] = [{'label': r['label'], 'pct': r['pct'], 'people': r['people']} for r in sr['distribution']]
        sr['yes_pct'] = round(sum(r['pct'] for r, o in zip(sr['distribution'], rows_spec) if (o.get('lean') or 'other') == 'yes'), 1)
    return opts_out, by_segment


def _pcts_from_people(rows: list, total_people: int):
    """One-decimal shares from the people, summing to 100.0 (the largest
    row takes the rounding gap)."""
    if not rows:
        return
    tot = float(total_people or sum(int(r['people']) for r in rows) or 1)
    pcts = [round(int(r['people']) / tot * 100.0, 1) for r in rows]
    k = max(range(len(rows)), key=lambda i: int(rows[i]['people']))
    pcts[k] = round(pcts[k] + round(100.0 - sum(pcts), 1), 1)
    for r, p in zip(rows, pcts):
        r['pct'] = max(0.0, p)


def _room_from_segments(by_segment: list, n_rows: int, total_people: int, n_seats: int, seed: str):
    """The room rows are the exact sum of the segments. When a room
    count would end in 0 or a room share would sit on a bare k/n
    multiple, a few people move between two rows inside the largest
    segment (its own sum holds), then every share is recomputed from
    the people."""
    if not by_segment or n_rows == 0:
        return
    big = max(by_segment, key=lambda sr: int(sr.get('people') or 0))
    rows_big = big['distribution']

    def _room_people(i):
        return sum(int(sr['distribution'][i]['people']) for sr in by_segment)

    def _shift(i, d):
        """d people into row i of the largest segment, out of its largest other row."""
        j = max((k for k in range(n_rows) if k != i), key=lambda k: int(rows_big[k]['people']), default=None)
        if j is None:
            return
        d = int(max(-int(rows_big[i]['people']) + 1, min(d, int(rows_big[j]['people']) - 1)))
        rows_big[i]['people'] = int(rows_big[i]['people']) + d
        rows_big[j]['people'] = int(rows_big[j]['people']) - d

    multiples = {round(100.0 * k / n_seats, 1) for k in range(0, n_seats + 1)} if n_seats else set()
    k_max = max(range(n_rows), key=_room_people)
    for i in range(n_rows):
        if i == k_max:
            continue
        p = _room_people(i)
        if p % 10 == 0:
            _shift(i, 1 + int(unit('room-digit', seed, i) * 8))
    for i in range(n_rows):
        if i == k_max:
            continue
        pct = round(_room_people(i) / float(total_people or 1) * 100.0, 1)
        if pct in multiples or pct < 0.1:
            _shift(i, max(1, int(round(0.0012 * total_people))))
    pct_max = round(_room_people(k_max) / float(total_people or 1) * 100.0, 1)
    if pct_max in multiples:
        _shift(k_max, -max(1, int(round(0.0012 * total_people))))

    def _unzero(i):
        """A few people into row i so neither it nor the row they come from ends in 0 (2026-11-05: the old one-person
        shift into the largest row left the donor on a 0)."""
        j = max((k for k in range(n_rows) if k != i), key=lambda k: int(rows_big[k]['people']), default=None)
        if j is None:
            return
        for d in range(1, 10):
            if (_room_people(i) + d) % 10 != 0 and (_room_people(j) - d) % 10 != 0 and int(rows_big[j]['people']) - d >= 1:
                _shift(i, d)
                return

    for i in sorted(range(n_rows), key=lambda k: (k == k_max, k)):
        if _room_people(i) % 10 == 0 and _room_people(i) > 0:
            _unzero(i)
    for sr in by_segment:
        _pcts_from_people(sr['distribution'], int(sr.get('people') or 0))


# --------------------------------------------------------- the anchor
# Jenna 2026-11-04: the room predicted Abbott would win the day after he lost.
# When the event the question turns on has a count, the published split sits
# on the count (calibrate-low-anchor-to-actuals: actuals set the anchor); a
# published poll before the event outweighs the twelve seats two to one. The
# seats are the voices, the count is the count, and the read says what the
# seats show that the count does not. Since room-4 (2026-11-05) the seats are
# allocated to the count once it is set (``mirror_seats``, below).
_PARTY_RX = [(re.compile(r"\((?:r|rep|gop|republican)\)", re.I), ' republican '), (re.compile(r"\((?:d|dem|democrat|democratic)\)", re.I), ' democrat '),
             (re.compile(r"\((?:i|ind|independent)\)", re.I), ' independent '), (re.compile(r"\((?:l|lib|libertarian)\)", re.I), ' libertarian '),
             (re.compile(r"\((?:g|green)\)", re.I), ' green ')]
_LABEL_STOP = {'the', 'a', 'an', 'of', 'for', 'and', 'or', 'to', 'in', 'on', 'candidate', 'candidates', 'vote', 'voted', 'voting', 'today', 'party', 'ticket', 'side', 'option',
               'measure', 'proposition', 'prop', 'race', 'us', 'u', 's', 'senate', 'senator', 'governor', 'house', 'seat', 'nominee', 'incumbent', 'challenger', 'mr', 'ms', 'mrs'}
_GENERIC_LABEL = {'republican', 'democrat', 'democratic', 'independent', 'libertarian', 'green', 'yes', 'no', 'other', 'another', 'write', 'blank', 'none'}
# The stayed-home row, however the moderator words it (Jenna 2026-11-04: 'Did
# not end up voting in this one' was not found, the count filled the whole
# room and Talarico read 54.6% of 18.6M registered voters). A negation, up to
# three filler words that do not turn the sentence ('but', 'and'), then the
# verb family: vote / voting / voted / cast / turn out / make it / go /
# participate / show up. Plus the set phrases: stayed home, sat it out,
# skipped it, no ballot, non-voter. 'I voted for ...' has no negation and
# 'Voted the ballot but left Senate blank' is the blank row (_BLANK_RX).
_NONVOTE_NEG = r"(?:did\s*not|didn'?t|do\s*not|don'?t|does\s*not|doesn'?t|could\s*not|couldn'?t|would\s*not|wouldn'?t|was\s*not|wasn'?t|never|not|chose\s+not\s+to|decided\s+not\s+to|decided\s+against|unable\s+to|failed\s+to|without)"
# up to three filler words, none of which turns the sentence ('but voted'), asks a question ('who I voted for') or hedges ('sure')
_NONVOTE_FILL = r"(?:(?!(?:but|and|yet|still|however|though|although|then|so|sure|who|whom|which|what|how|if|whether|know)\b)[\w'-]+\s+){0,3}?(?:(?:to|in|of|on)\s+)?"
_NONVOTE_VERB = (r"(?:vot(?:e|ed|es|ing)|cast(?:ing)?|turn(?:ed|ing)?\s+out|ma(?:ke|de|king)\s+it|(?:go|went|going)(?!\s+(?:with|for|along|against|behind|back|near|all|big))"
                 r"|participat(?:e|ed|ing)|show(?:ed|ing|n)?\s+up)")
_NONVOTE_RX = re.compile(
    r"\b" + _NONVOTE_NEG + r"\s+" + _NONVOTE_FILL + _NONVOTE_VERB + r"\b"
    r"|\bno\s+(?:ballot|vote)s?\b|\bstay(?:ed|ing|s)?\s+(?:at\s+)?home\b|\bs(?:at|it|itting)\s+(?:this|it|that)(?:\s+one)?\s+out\b"
    r"|\bs(?:at|it|itting)\s+out\b(?!\s+(?:the|that|this)\s+(?:\w+\s+)?(?:race|line|contest|seat))"
    r"|\bskipp?(?:ed|ing|s)?\s+(?:it|this\s+one|that\s+one|the\s+election|this\s+election|voting|the\s+vote|the\s+whole\s+thing|the\s+polls)\b"
    r"|\bopted\s+out\b|\bnon-?\s?vot(?:er|ers|ing)\b|\bnot\s+a\s+voter\b|\bmissed\s+(?:it|the\s+election|the\s+vote|voting)\b|\bno-?\s?shows?\b",
    re.I)
_BLANK_RX = re.compile(
    r"\bleft (?:it|the race|this race|that race|the \w+ race|the \w+ line|that one|this one) (?:blank|empty)\b|\bblank\b|\bwrite-?\s?ins?\b|\banother candidate\b|\bsomeone else\b"
    r"|\bthird[\s-]party\b|\bundervot|\b(?:skipped|sat\s+out|passed\s+on)\s+(?:the|that|this)\s+(?:\w+\s+)?(?:race|line|contest|seat)\b", re.I)
_NEGATION_RX = re.compile(r"\b(?:not|never|none|without|neither|nor)\b|\b(?:did|does|do|could|would|should|was|were|is|are|has|have|had|ca|wo|ai)n'?t\b", re.I)
_VOTE_WORD_RX = re.compile(r"\bvot(?:e|ed|es|er|ers|ing)\b|\bballots?\b|\bpolls\b|\bturn(?:ed)?\s+out\b|\bturnout\b", re.I)


def is_nonvote_label(label: str) -> bool:
    """True when an option's label means the person did not vote at all
    (stayed home), not a blank line or a vote for someone else."""
    s = str(label or '')
    return bool(_NONVOTE_RX.search(s)) and not _BLANK_RX.search(s)


def nonvote_option(options: list, matched: Optional[set] = None, loose: bool = False) -> Optional[str]:
    """The id of the stayed-home option, or None. An option the count
    already landed on (a candidate's row) is never it, nor is the blank
    row. `loose` (only when the event happened and turnout is known): an
    option whose label is about voting with a negation anywhere, or a
    'no' lean whose label is about voting; never a row that is not about
    not voting."""
    skip = set(matched or ())
    rows = [o for o in options or [] if o.get('id') and o['id'] not in skip]
    for o in rows:
        if is_nonvote_label(o.get('label')):
            return o['id']
    if not loose:
        return None
    for o in rows:
        lab, lean = str(o.get('label') or ''), str(o.get('lean') or '').lower()
        if lean == 'yes' or _BLANK_RX.search(lab) or not _VOTE_WORD_RX.search(lab):
            continue
        if _NEGATION_RX.search(lab) or lean == 'no':
            return o['id']
    return None


def _label_tokens(label: str) -> set:
    t = str(label or '').lower()
    for rx, rep in _PARTY_RX:
        t = rx.sub(rep, t)
    return {w for w in re.sub(r"[^a-z0-9]+", ' ', t).split() if w and w not in _LABEL_STOP}


def match_targets(options: list, rows: list) -> dict:
    """{option_id: share_pct} for the rows (results or polls) that name an
    option: a row's distinctive words (a surname, a party) sit in the
    option's label. Two rows that land on one option add up; a row that
    could be two options matches neither. {} when fewer than two options
    match or the matched shares do not cover most of the vote."""
    opts = [(o['id'], _label_tokens(o.get('label'))) for o in options if o.get('id')]
    out = {}
    for r in rows or []:
        if not isinstance(r, dict) or r.get('share_pct') in (None, ''):
            continue
        try:
            share = float(r['share_pct'])
        except (TypeError, ValueError):
            continue
        rt = _label_tokens(r.get('label'))
        if not rt:
            continue
        scored = []
        wt = lambda toks: sum(2.0 if (tok not in _GENERIC_LABEL and len(tok) >= 4) else 1.0 for tok in toks)
        for oid, ot in opts:
            inter = rt & ot
            if not inter:
                continue
            # the row's words covered by the option, or the option's words covered by the row ('The Democrat' is all of itself)
            scored.append((max(wt(inter) / (wt(rt) or 1.0), wt(inter) / (wt(ot) or 1.0)), oid))
        scored.sort(reverse=True)
        if not scored or scored[0][0] < 0.34 or (len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9):
            continue
        out[scored[0][1]] = out.get(scored[0][1], 0.0) + share
    if len(out) < 2 or sum(out.values()) < 60.0:
        return {}
    return out


def anchor_plan(options: list, brief: dict, klass: str = '', turnout_fallback: Optional[float] = None) -> dict:
    """How the published split anchors for this question, or {}:
    {'kind': 'result'|'poll', 'targets': {option_id: pct of the whole
    audience}, 'line': <for the read>, 'weight': None|float}. A counted
    event anchors hard; a poll before the event blends two to one."""
    ev = (brief or {}).get('event') or {}
    results = [r for r in (ev.get('results') or []) if isinstance(r, dict) and str(r.get('status') or '') in ('final', 'called', 'projected')]
    turnout = ev.get('turnout_pct') or turnout_fallback   # the count's own turnout, else the twin's base (registered to voted)
    try:
        turnout_ok = bool(turnout) and 20.0 <= float(turnout) <= 95.0
    except (TypeError, ValueError):
        turnout_ok = False
    if str(ev.get('status') or '') == 'happened' and results:
        m = match_targets(options, results)
        if m:
            # the stayed-home row: by its label, else (the event happened, turnout is known) by a negated vote word or a 'no' lean; never a candidate's row
            nonvote = nonvote_option(options, matched=set(m), loose=bool(klass == 'civic' and turnout_ok))
            tot = sum(m.values()) or 1.0
            voting = 100.0
            targets = {}
            if klass == 'civic' and nonvote and turnout_ok:
                targets[nonvote] = round(100.0 - float(turnout), 2)
                voting = float(turnout)
                targets.update({k: round(v / 100.0 * voting, 2) for k, v in m.items()})     # the count's shares are of the votes cast
            else:
                targets.update({k: round(v / tot * voting, 2) for k, v in m.items()})
            parts = ', '.join(f"{next((o['label'] for o in options if o['id'] == k), k)} {round(float(m[k]), 1)}%" for k in m)
            status = results[0].get('status') or 'called'
            line = f"The published split sits on the count ({parts}; {status})."
            if klass == 'civic' and not nonvote:
                line += " The published split is of the votes cast; the room had no stayed-home option."
            line += " The seats are allocated to the count and voice it; say what they show that the count does not."
            return {'kind': 'result', 'targets': targets, 'weight': None, 'fill_voting': (voting if nonvote in targets else None), 'line': line}
    polls = (brief or {}).get('polls') or []
    if polls and str(ev.get('status') or '') in ('upcoming', 'today', 'none'):
        m = match_targets(options, polls)
        if m:
            tot = sum(m.values()) or 1.0
            targets = {k: round(v / tot * 100.0, 2) for k, v in m.items()}
            parts = ', '.join(f"{next((o['label'] for o in options if o['id'] == k), k)} {round(float(m[k]), 1)}%" for k in m)
            asof = next((str(pp.get('as_of') or '') for pp in polls if pp.get('as_of')), '')
            return {'kind': 'poll', 'targets': targets, 'weight': ANCHOR_POLL_WEIGHT, 'fill_voting': None,
                    'line': f"The published split leans on the latest published numbers ({parts}{', as of ' + asof if asof else ''}) two to one over the seats."}
    return {}


def _base_turnout_pct(twin: dict) -> Optional[float]:
    """Turnout as a share of the twin's base, from the twin's own identity
    (an electorate's registered count and the votes cast last time)."""
    try:
        base = ((twin or {}).get('identity') or {}).get('base') or {}
        reg, voted = float(base.get('count_us') or 0), float(base.get('turnout_us') or 0)
        if reg > 0 and voted > 0 and 0.2 <= voted / reg <= 0.95:
            return round(voted / reg * 100.0, 1)
    except (TypeError, ValueError):
        pass
    return None


def anchor_rows(opts_out: list, by_segment: list, plan: dict, total_people: int, n_seats: int, seed: str) -> bool:
    """Move the published people onto the anchor (in place). The matched
    rows take the plan's targets (a result exactly, a few tenths of
    jitter; a poll blended by its weight); the other rows keep their
    share of what is left; every segment moves in proportion so the room
    stays the sum of its segments. True when anything moved."""
    if not plan or not plan.get('targets') or not opts_out or total_people <= 0:
        return False
    ids = [o['id'] for o in opts_out]
    cur = {o['id']: float(o.get('pct') or 0.0) for o in opts_out}
    targets = {k: float(v) for k, v in plan['targets'].items() if k in cur}
    if len(targets) < 2 and plan.get('kind') != 'result':
        return False
    if not targets:
        return False
    w = plan.get('weight')
    rest_ids = [k for k in ids if k not in targets]
    rest_cur = sum(cur[k] for k in rest_ids)
    if plan.get('fill_voting') is None:
        # the targets are shares of the vote: they fill what the unmatched rows (a blank, a write-in, stayed home) leave as the seats had them
        tsum = sum(targets.values()) or 1.0
        targets = {k: v / tsum * max(0.0, 100.0 - rest_cur) for k, v in targets.items()}
    new = {}
    if w is None:
        for k, v in targets.items():
            j = (unit('anchor', seed, k) - 0.5) * 2.0 * ANCHOR_RESULT_JITTER
            new[k] = max(0.1, v + j)
    else:
        for k, v in targets.items():
            new[k] = (float(w) * v + cur[k]) / (float(w) + 1.0)
    matched_new = sum(new.values())
    if matched_new > 100.0:
        f = 100.0 / matched_new
        new = {k: v * f for k, v in new.items()}
        matched_new = 100.0
    leftover = max(0.0, 100.0 - matched_new)   # what the count did not name (a write-in, a blank, a stay-home when turnout is unknown) shares what is left
    for k in rest_ids:
        new[k] = (cur[k] / rest_cur * leftover) if rest_cur > 0 else (leftover / len(rest_ids) if rest_ids else 0.0)
    people_new = {k: int(round(new[k] / 100.0 * total_people)) for k in ids}
    gap = total_people - sum(people_new.values())
    k_max = max(ids, key=lambda k: people_new[k])
    people_new[k_max] += gap
    # every segment moves in proportion to its current people in each row
    for i, k in enumerate(ids):
        cur_seg = [int((sr['distribution'][i] or {}).get('people') or 0) for sr in by_segment]
        tot_seg = sum(cur_seg)
        sizes = [int(sr.get('people') or 0) for sr in by_segment]
        base = cur_seg if tot_seg > 0 else sizes
        tb = sum(base) or 1
        alloc = [int(round(people_new[k] * b / tb)) for b in base]
        alloc[max(range(len(alloc)), key=lambda j: alloc[j])] += people_new[k] - sum(alloc)
        for sr, a in zip(by_segment, alloc):
            sr['distribution'][i]['people'] = max(0, a)
    for sr in by_segment:   # each segment keeps its own total
        want = int(sr.get('people') or 0)
        have = sum(int(r.get('people') or 0) for r in sr['distribution'])
        if have != want and sr['distribution']:
            j = max(range(len(sr['distribution'])), key=lambda i: int(sr['distribution'][i].get('people') or 0))
            sr['distribution'][j]['people'] = max(0, int(sr['distribution'][j]['people']) + (want - have))
    _room_from_segments(by_segment, len(ids), int(total_people), n_seats, seed + '|anchor')
    for i, o in enumerate(opts_out):
        o['people'] = sum(int(sr['distribution'][i]['people']) for sr in by_segment)
    _pcts_from_people(opts_out, int(total_people))
    for sr in by_segment:
        _pcts_from_people(sr['distribution'], int(sr.get('people') or 0))
        sr['yes_pct'] = round(sum(float(r['pct']) for r, o in zip(sr['distribution'], opts_out) if (o.get('lean') or 'other') == 'yes'), 1)
    return True


def plain_share(pct: float) -> str:
    """A share in plain words for the read: 'about two in three', 'a
    sliver'."""
    p = float(pct or 0)
    if p >= 95:
        return 'nearly everyone'
    if p >= 85:
        return 'almost nine in ten'
    if p >= 78:
        return 'about four in five'
    if p >= 70:
        return 'about three in four'
    if p >= 62:
        return 'about two in three'
    if p >= 55:
        return 'about three in five'
    if p >= 45:
        return 'about half'
    if p >= 37:
        return 'about two in five'
    if p >= 30:
        return 'about one in three'
    if p >= 22:
        return 'about one in four'
    if p >= 16:
        return 'about one in five'
    if p >= 8:
        return 'about one in ten'
    if p >= 4:
        return 'about one in twenty'
    if p >= 2.5:
        return 'about one in thirty'
    if p >= 1.5:
        return 'about one in fifty'
    if p >= 0.7:
        return 'about one in a hundred'
    if p > 0:
        return 'a sliver'
    return 'none'


# ------------------------------------------- the seats mirror the count
# Jenna 2026-11-05 (the P-Valley room on S.W.A.T. Exiles: "why would it say 0
# people would watch?"): twelve seats landed on skip against a count that said
# about one in twenty watch most Fridays, and the read said 'nobody' beside
# 'one in twenty'. Three rules, held here:
#   1. people come from the audience share (share x the people the room stands
#      for), never from the seat tally; '0 people' only when the count is zero;
#   2. the seats are allocated to the count by largest remainder, and any side
#      holding half a seat's worth of the audience holds a seat and speaks;
#   3. the read states one split, once; a nobody / none / all twelve claim
#      against a non-zero count is rewritten.
_NUM_WORDS = ('no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
              'seventeen', 'eighteen', 'nineteen', 'twenty')
_LEAN_RANK = {'yes': 0.0, 'maybe': 1.0, 'other': 1.5, 'no': 2.0}


def num_word(n: int) -> str:
    n = int(n)
    return _NUM_WORDS[n] if 0 <= n < len(_NUM_WORDS) else str(n)


def allocate_seats(shares: dict, n_seats: int, floor_seats: float = MIRROR_FLOOR_SEATS) -> dict:
    """Seats per option from the count: {option id: seats}, summing to
    ``n_seats``. Largest remainder (Hamilton): each option takes the
    whole seats in its quota (share / 100 x seats) and the leftover seats
    go to the largest fractional remainders. Then the floor: an option
    with no seat whose share is worth at least ``floor_seats`` of a seat
    (half a seat, 50 / n percent: 4.2% with twelve) takes one from the
    option holding the most seats (ties: the smaller remainder gives).
    A zero share never holds a seat. 88 / 7 / 5 with twelve seats is
    10 / 1 / 1; 100 / 0 / 0 is 12 / 0 / 0."""
    ids = [k for k in shares]
    n = int(n_seats or 0)
    if not ids or n <= 0:
        return {k: 0 for k in ids}
    vals = {k: max(0.0, float(shares.get(k) or 0.0)) for k in ids}
    tot = sum(vals.values())
    if tot <= 0:
        return {k: 0 for k in ids}
    quota = {k: vals[k] / tot * n for k in ids}
    out = {k: int(quota[k]) for k in ids}
    rem = {k: quota[k] - out[k] for k in ids}
    left = n - sum(out.values())
    for k in sorted(ids, key=lambda k: (-rem[k], -vals[k], ids.index(k)))[:max(0, left)]:
        out[k] += 1
    floor = float(floor_seats or 0.0)
    for k in sorted(ids, key=lambda k: -vals[k]):
        if out[k] == 0 and vals[k] > 0 and quota[k] >= floor:
            donors = [d for d in ids if out[d] >= 2]
            if not donors:
                break
            d = max(donors, key=lambda d: (out[d], -rem[d]))
            out[d] -= 1
            out[k] = 1
    return out


def _lean_gap(a: str, b: str) -> float:
    return abs(_LEAN_RANK.get(str(a or 'other'), 1.5) - _LEAN_RANK.get(str(b or 'other'), 1.5))


def mirror_seats(opts_out: list, by_segment: list, answers: list, roster: list, segments: list) -> dict:
    """Allocate the seats to the count, in place on ``answers``: in each
    segment, largest remainder over the segment's own rows (the floor at
    half a seat of the room), then the room-level floor (a side worth
    half a room seat with no seat anywhere takes one in the segment
    where it runs strongest). Seats already on an option keep it, the
    surest first; the rest move to the nearest short option (a no moves
    to a maybe before a yes), the least sure first. Returns the plan:
    {'alloc': {option id: seats}, 'moved': [{seat, from, to}],
    'floor_pct': <half a seat, as a share of the room>}; the published
    shares and people (the count) are never touched."""
    ids = [o['id'] for o in opts_out]
    if not ids or not roster:
        return {'alloc': {}, 'moved': [], 'floor_pct': None}
    by_id = {o['id']: o for o in opts_out}
    n_total = len(roster)
    by_seat = {int(a['seat']): a for a in answers}
    seg_rows = {sr['segment']: sr for sr in by_segment}
    alloc_seg = {}
    for s in segments:
        rs = [r for r in roster if r.get('segment') == s['label']]
        if not rs:
            continue
        sr = seg_rows.get(s['label']) or {}
        dist = sr.get('distribution') or []
        shares = {ids[i]: float((dist[i] or {}).get('pct') or 0.0) for i in range(min(len(ids), len(dist)))} or {o['id']: float(o.get('pct') or 0.0) for o in opts_out}
        for k in ids:
            shares.setdefault(k, 0.0)
        alloc_seg[s['label']] = allocate_seats(shares, len(rs), MIRROR_FLOOR_SEATS * len(rs) / float(n_total))
    # the room-level floor: a side worth half a room seat holds one somewhere
    room_alloc = {k: sum(a.get(k, 0) for a in alloc_seg.values()) for k in ids}
    for k in sorted(ids, key=lambda k: -float(by_id[k].get('pct') or 0.0)):
        if room_alloc[k] == 0 and float(by_id[k].get('pct') or 0.0) >= 50.0 / n_total and float(by_id[k].get('pct') or 0.0) > 0:
            best = None
            for lab, a in alloc_seg.items():
                dist = (seg_rows.get(lab) or {}).get('distribution') or []
                share = float((dist[ids.index(k)] or {}).get('pct') or 0.0) if ids.index(k) < len(dist) else 0.0
                donors = [d for d in ids if a.get(d, 0) >= 2]
                if donors and (best is None or share > best[0]):
                    best = (share, lab, max(donors, key=lambda d: a[d]))
            if best:
                _, lab, d = best
                alloc_seg[lab][d] -= 1
                alloc_seg[lab][k] = 1
                room_alloc = {kk: sum(a.get(kk, 0) for a in alloc_seg.values()) for kk in ids}
    moved = []
    for s in segments:
        rs = [r for r in roster if r.get('segment') == s['label']]
        want = dict(alloc_seg.get(s['label']) or {})
        if not rs or not want:
            continue
        have = {k: [] for k in ids}
        stray = []   # seats on an option the board no longer carries ('other')
        for r in rs:
            a = by_seat.get(int(r['seat']))
            oid = (a or {}).get('option') if a and a.get('option') in by_id else None
            (have[oid] if oid else stray).append(int(r['seat']))
        # each short option takes a seat from a side holding more than the count gives it: the nearest stance first (a no
        # moves to a maybe before a yes), then the seat whose own words already touched that option, then the least sure
        for k in sorted(ids, key=lambda k: -(want.get(k, 0) - len(have[k]))):
            while len(have[k]) < want.get(k, 0):
                cands = [(j, sid) for j in ids if len(have[j]) > want.get(j, 0) for sid in have[j]] + [(None, sid) for sid in stray]
                if not cands:
                    break

                def _fit(c):
                    j, sid = c
                    a = by_seat.get(sid) or {}
                    return (_lean_gap(by_id[j].get('lean') if j else 'other', by_id[k].get('lean')), -_label_hits(a.get('line') or '', by_id[k].get('label')), int(a.get('confidence') or 0), sid)
                j, sid = min(cands, key=_fit)
                (have[j] if j else stray).remove(sid)
                have[k].append(sid)
                a = by_seat.get(sid) or {}
                moved.append({'seat': sid, 'from': a.get('option'), 'to': k})
                a['option'] = k
                a['option_label'] = by_id[k]['label']
                a['mirrored'] = True
    return {'alloc': room_alloc, 'moved': moved, 'floor_pct': round(50.0 / n_total, 2)}


def retally(opts_out: list, by_segment: list, answers: list, roster: list) -> None:
    """The raw tally on every option and segment row from the answers as
    they stand (after the seats are mirrored); the published shares and
    people stay."""
    n_total = len(roster) or 1
    by_seat = {int(a['seat']): a for a in answers}
    for o in opts_out:
        seats = sorted(int(a['seat']) for a in answers if a.get('option') == o['id'])
        o['seats'], o['seats_n'], o['tally'], o['room_pct'] = seats, len(seats), f"{len(seats)} of {n_total}", round(len(seats) / n_total * 100.0, 1)
    for sr in by_segment:
        rs = [r for r in roster if r.get('segment') == sr.get('segment')]
        sr['tally'] = [{'label': o['label'], 'seats': len([r for r in rs if (by_seat.get(int(r['seat'])) or {}).get('option') == o['id']])} for o in opts_out]


def _fallback_voice(label: str, lean: str, seed: str, seat: int) -> str:
    lines = {'yes': (f"{label}. That is me, and I am not the only one in my group chat.", f"{label}. Count me in that group; it is what my week actually looks like.",
                     f"{label}. That is honestly where I am with it right now."),
             'no': (f"{label}. That is me, and I do not see it changing.", f"{label}. That is honestly where I am with it.", f"{label}. Put me down for that one."),
             'maybe': (f"{label}. That is about where I am, somewhere in the middle.", f"{label}. That is me, and it could go either way.", f"{label}. I would not swear to more than that.")}
    pool = lines.get(str(lean or ''), lines['maybe'])
    return pool[int(unit('mirror-voice', seed, seat) * len(pool)) % len(pool)]


def revoice_seat(models: Models, card: dict, q: str, framing: str, options: list, option_id: str, brief: dict, memory: list, seed: str, log=None,
                 stimulus: Optional[dict] = None, earlier: str = '') -> dict:
    """A moved seat argues the stance the count gave it, in character;
    the deterministic line when the model is off or late."""
    o = next((x for x in options if x['id'] == option_id), None) or {'id': option_id, 'label': option_id, 'lean': 'other'}
    body = _seat_body(card, q, framing, options, brief, memory, stimulus)
    body['your_answer'] = {'id': o['id'], 'label': o['label']}
    body['why_you_are_asked_again'] = 'A real share of this audience lands on this answer and you are one of them; say why, as yourself.'
    data, call = models.call('persona', MIRROR_SYSTEM, json.dumps(body, default=str)[:9000], max_tokens=420, temperature=0.85, tools=None)
    if log:
        log('mirror', call)
    if isinstance(data, dict) and str(data.get('line') or '').strip():
        try:
            conf = int(clamp(int(float(data.get('confidence') or 3)), 1, 5))
        except (TypeError, ValueError):
            conf = 3
        return {'line': scrub(str(data['line']).strip())[:520], 'confidence': conf, 'reason': scrub(str(data.get('reason') or '').strip())[:240], 'fallback': False}
    return {'line': _fallback_voice(o['label'], o.get('lean') or 'other', seed, int(card.get('seat') or 0)), 'confidence': 3, 'reason': 'one of the people who land here', 'fallback': True}


def _lead_rows(opts_out: list) -> tuple:
    """(the side the headline leads with, the largest other row): the yes
    side when the board has one, else the largest row."""
    yes = [o for o in opts_out if (o.get('lean') or 'other') == 'yes']
    rows = sorted(opts_out, key=lambda o: -float(o.get('pct') or 0.0))
    if yes:
        lead = {'label': yes[0]['label'] if len(yes) == 1 else 'yes', 'pct': sum(float(o.get('pct') or 0.0) for o in yes), 'people': sum(int(o.get('people') or 0) for o in yes),
                'seats_n': sum(int(o.get('seats_n') or len(o.get('seats') or [])) for o in yes), 'lean': 'yes', 'ids': [o['id'] for o in yes]}
        rest = next((o for o in rows if o['id'] not in lead['ids']), None)
    else:
        lead = {'label': rows[0]['label'], 'pct': float(rows[0].get('pct') or 0.0), 'people': int(rows[0].get('people') or 0), 'seats_n': int(rows[0].get('seats_n') or len(rows[0].get('seats') or [])),
                'lean': rows[0].get('lean') or 'other', 'ids': [rows[0]['id']]} if rows else {}
        rest = rows[1] if len(rows) > 1 else None
    return lead, rest


def _quoted(label: str) -> str:
    s = re.sub(r"^(?:yes|no)\b[,:]?\s*", '', str(label or '').strip(), flags=re.I).strip().rstrip('.')
    return s[0].upper() + s[1:] if s else str(label or '')


def _seats_words(n: int, of: int) -> str:
    if of <= 0:
        return ''
    if n <= 0:
        return 'no seat in the room'
    if n == of:
        return f"all {num_word(of)} seats"
    return f"{num_word(n)} of the {num_word(of)} seats"


def headline_for(opts_out: list, total_people: int, n_seats: int, audience: str) -> tuple:
    """The headline and its people: the lead side's share in plain words,
    of the people the room stands for, the people that share comes to
    (never the seat tally), the option in the board's words, then the
    seats; then the largest other side the same way. ('about one in
    twenty of 6.09M P-Valley viewers, roughly 329.1K people, are a yes
    (...): one of the twelve seats.')"""
    if not opts_out:
        return '', 0
    lead, rest = _lead_rows(opts_out)
    if not lead:
        return '', 0
    aud = str(audience or 'people').strip()
    size = f"the {human_people(total_people)} {aud}" if total_people else aud
    share = plain_share(lead['pct'])
    if lead['people'] <= 0 and lead['pct'] <= 0:
        if lead['lean'] == 'yes':
            head = f"None of {size} are a yes" + (f" (\"{_quoted(lead['label'])}\")" if lead['label'] != 'yes' else '')
        else:
            head = f"None of {size} land on \"{_quoted(lead['label'])}\""
        head += f": {_seats_words(0, n_seats)}." if n_seats else '.'
    else:
        who = f"are a yes (\"{_quoted(lead['label'])}\")" if lead['lean'] == 'yes' and lead['label'] != 'yes' else ('are a yes' if lead['lean'] == 'yes' else f"land on \"{_quoted(lead['label'])}\"")
        head = f"{share[0].upper()}{share[1:]} of {size}, roughly {human_people(lead['people'])} people, {who}"
        head += f": {_seats_words(lead['seats_n'], n_seats)}." if n_seats else '.'
    if rest is not None and float(rest.get('pct') or 0.0) > 0:
        rs = plain_share(float(rest.get('pct') or 0.0))
        tail = f" {rs[0].upper()}{rs[1:]}, roughly {human_people(int(rest.get('people') or 0))} people, say \"{_quoted(rest['label'])}\""
        rn = int(rest.get('seats_n') or len(rest.get('seats') or []))
        tail += f": {_seats_words(rn, n_seats)}." if n_seats else '.'
        head += tail
    return scrub(head), int(lead['people'])


_SENT_SPLIT_RX = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])')
_ZERO_PEOPLE_RX = re.compile(r"\b(?:nobody|no one|no-one|none of (?:them|these|those|the room|the audience|this group|the group)|not (?:a )?single (?:person|one|viewer|fan)|not one of (?:them|these)|zero people|0 people)\b", re.I)
_ZERO_SEATS_RX = re.compile(r"\b(?:not (?:a )?single seat|no seats?|none of the (?:seats|twelve|\d{1,2})|not one (?:seat|of the seats|of the twelve)|zero seats|0 seats|0 of (?:the )?(?:\d{1,2}|twelve))\b", re.I)
_WHOLE_ROOM_RX = re.compile(r"\b(?:the\s+)?(?:whole|entire) room\b", re.I)


_COUNT_NOUNS = r"(?:episodes?|seasons?|months?|weeks?|days?|years?|hours?|minutes?|nights?|dollars?|bucks|shows?|films?|movies?|songs?|tracks?|albums?|games?|books?|times|seats? of)"


def _all_seats_rx(n_seats: int):
    """'all twelve seats', 'all 12 of them', 'every one of the twelve',
    'every seat', 'all of the seats', 'all twelve landed': the room's
    own seat count only, and never a count of something else ('all
    twelve episodes' is left alone). Group 1 is set when the seats are
    named, so the rewrite keeps the noun."""
    n = f"(?:{num_word(n_seats)}|{int(n_seats)})"
    return re.compile(r"\b(?:all|every one of|each of)\s+(?:(?:of\s+)?the\s+)?" + n + r"(?:\s+(seats?|of them|of the seats|of the room|in the room|people in the room)\b|\b(?!\s+" + _COUNT_NOUNS + r"\b))"
                      r"|\b(?:all|every one of|each of)\s+(?:(?:of\s+)?the\s+)?(seats|of them|of the seats)\b|\bevery (seat)\b", re.I)


def _label_hits(sentence: str, label: str) -> int:
    toks = {_stem(t) for t in re.findall(r"[a-z0-9']+", str(label or '').lower()) if len(t) >= 4 and t not in _LABEL_STOP and t not in _GENERIC_LABEL}
    sent = {_stem(t) for t in re.findall(r"[a-z0-9']+", str(sentence or '').lower())}
    return len(toks & sent)


def _claim_row(sentence: str, opts_out: list, lead_ids: list) -> Optional[dict]:
    """The row a nobody / none claim is about: the non-lead row whose
    label words sit in the sentence (stemmed, so 'watching' finds
    'watch'); None when the sentence names no row, so a claim about
    something else ('nobody is canceling') is left alone."""
    cands = [o for o in opts_out if o['id'] not in lead_ids]
    if not cands:
        return None
    scored = sorted(cands, key=lambda o: (-_label_hits(sentence, o.get('label')), -float(o.get('pct') or 0.0)))
    return scored[0] if _label_hits(sentence, scored[0].get('label')) > 0 else None


def cohere_read(text: str, opts_out: list, n_seats: int, headline: str = '') -> str:
    """One split, stated once. A nobody / no one / none / not one / 0
    people claim about a side the count gives a share becomes that share
    in plain words; a no seat / 0 of twelve claim becomes the seats it
    holds; all twelve / every seat / the whole room becomes the lead's
    own tally when a seat sits elsewhere. A read that does not open on
    the split opens with the headline. A zero count keeps its none."""
    s = str(text or '').strip()
    if not s or not opts_out:
        return s
    rows = sorted(opts_out, key=lambda o: -float(o.get('pct') or 0.0))
    lead = rows[0]
    lead_n = int(lead.get('seats_n') or len(lead.get('seats') or []))
    all_rx = _all_seats_rx(n_seats) if n_seats else None
    out = []
    for sent in _SENT_SPLIT_RX.split(s):
        t = sent
        if _ZERO_PEOPLE_RX.search(t):
            row = _claim_row(t, opts_out, [lead['id']])
            if row is not None and (float(row.get('pct') or 0.0) > 0 or int(row.get('people') or 0) > 0):
                words = plain_share(float(row.get('pct') or 0.0))
                t = _ZERO_PEOPLE_RX.sub(lambda m: (words + ' of them') if m.group(0).lower().startswith('none of') else words, t)
        if _ZERO_SEATS_RX.search(t):
            row = _claim_row(t, opts_out, [lead['id']])
            if row is not None:
                n = int(row.get('seats_n') or len(row.get('seats') or []))
                if n > 0:
                    t = _ZERO_SEATS_RX.sub(lambda m: (f"{n} of {n_seats}" if re.match(r"0 of", m.group(0)) else f"{num_word(n)} seat" + ('' if n == 1 else 's')), t)
        if all_rx is not None and lead_n < n_seats:
            if all_rx.search(t):
                t = all_rx.sub(lambda m: f"{num_word(lead_n)} of the {num_word(n_seats)}" + (' seats' if any(m.groups()) else ''), t)
            if _WHOLE_ROOM_RX.search(t):
                t = _WHOLE_ROOM_RX.sub('most of the room' if lead_n * 2 >= n_seats else 'much of the room', t)
        t = t.strip()
        if t and t[0].islower():
            t = t[0].upper() + t[1:]
        out.append(t)
    fixed = ' '.join(x for x in out if x)
    if headline and not _opens_on_split(fixed, opts_out):
        fixed = (headline.strip() + ' ' + fixed).strip()
    return re.sub(r"\s{2,}", ' ', fixed)


def _opens_on_split(text: str, opts_out: list) -> bool:
    """The first two sentences carry a share in words, a percent, or the
    seats."""
    parts = _SENT_SPLIT_RX.split(str(text or '').strip())
    head = ' '.join(parts[:2]).lower()
    if not head:
        return False
    if re.search(r"\d+(?:\.\d+)?\s*%|\bof the (?:twelve|sixteen|ten|eight|six|four|\d{1,2}) seats\b|\b(?:in|of) the room\b", head):
        return True
    words = {plain_share(float(o.get('pct') or 0.0)) for o in opts_out}
    words |= {'nearly everyone', 'almost nine in ten', 'about four in five', 'about three in four', 'about two in three', 'about three in five', 'about half', 'about two in five',
              'about one in three', 'about one in four', 'about one in five', 'about one in ten', 'about one in twenty', 'a sliver', 'none of'}
    return any(w in head for w in words if w)


def prior_note(twin: dict, q: str, kind: str, opts_out: list, stimulus: Optional[dict], last: Optional[dict], seed: str) -> dict:
    """The trait-based prior as a sanity bound (internal): the twin's own
    positive leaning against the seats' yes share."""
    try:
        from migration import digital_twin_panel as P
        if kind not in ('yes_no', 'show_of_hands', 'price', 'open', 'multiple_choice'):
            return {}
        yes_opts = [o for o in opts_out if o.get('lean') == 'yes']
        if not yes_opts:
            return {}
        d = P.distribution(twin, q, 'yes_no' if kind in ('open', 'multiple_choice') else kind, [], 1000, seed + '|prior', stimulus, last)
        prior = float(d.get('yes_pct') or 0.0)
        room = round(sum(float(o.get('pct') or 0) for o in yes_opts), 1)
        maybe = round(sum(float(o.get('pct') or 0) for o in opts_out if o.get('lean') == 'maybe'), 1)
        departed = abs(room - prior) > 25.0
        return {'prior_yes_pct': round(prior, 1), 'room_yes_pct': room, 'room_maybe_pct': maybe, 'departed': departed, 'topic': d.get('topic'),
                'note': (f"The audience's own leaning before the room spoke was about {round(prior)}% positive; the seats came in at {room}% positive"
                         + (' (a wide gap: say what accounts for it).' if departed else ' (in line).'))}
    except Exception:
        return {}


def _fallback_read(q: str, kind: str, opts_out: list, by_segment: list, prior: dict, headline: str = '') -> str:
    rows = sorted(opts_out, key=lambda o: -float(o.get('pct') or 0))
    if not rows:
        return ''
    top = rows[0]
    n = len(top.get('seats') or [])
    if headline:
        line = headline.strip()
    else:
        line = f"{plain_share(top['pct']).capitalize()} of this audience lands on {top['label'].lower()}" + (f" ({top.get('tally') or n} in the room)" if top.get('tally') else '') + f", about {int(top['people']):,} people"
        if len(rows) > 1:
            line += f"; {plain_share(rows[1]['pct'])} say {rows[1]['label'].lower()}"
        line += '.'
    if len(rows) > 2:
        tail = [r for r in rows[2:] if float(r.get('pct') or 0) < 3.0]
        if tail:
            line += ' The rest is a sliver each.'
        else:
            line += f" {plain_share(rows[2]['pct']).capitalize()} say \"{_quoted(rows[2]['label'])}\"."
    yes = sum(float(o.get('pct') or 0) for o in opts_out if o.get('lean') == 'yes')
    if any(o.get('lean') == 'yes' for o in opts_out) and sum(1 for o in opts_out if o.get('lean') == 'yes') > 1:
        line += f" Taken together, {plain_share(yes)} of the audience is a yes."
    if len(by_segment) > 1:
        best = max(by_segment, key=lambda s: float(s.get('yes_pct') or 0))
        worst = min(by_segment, key=lambda s: float(s.get('yes_pct') or 0))
        if best is not worst and abs(float(best.get('yes_pct') or 0) - float(worst.get('yes_pct') or 0)) >= 1.5:
            line += f" Strongest among {best['segment']} ({best['yes_pct']}%), softest among {worst['segment']} ({worst['yes_pct']}%)."
    return line


_CLOCK_OPEN = re.compile(r'^At\s+\d{1,3}:\d{2}(?::\d{2})?\b[,:]?\s*', re.I)
_SCENE_LEAD = ('with', 'after', 'right', 'just', 'as', 'when', 'once', 'while', 'on', 'in', 'before', 'following', 'at', 'off', 'coming', 'having', 'seconds', 'moments')


def _drop_clock_opening(text: str) -> str:
    """A read on the clock that still opens with the clock and the scene
    ('At 43:13, with the screen black and the network logo up, almost
    nine in ten seats said ...') opens with what the room said instead
    (Jenna, 2026-11-04: no context preamble, launch into what the seats
    say). Only a leading 'At m:ss' goes; a clock later in the read
    stays."""
    s = str(text or '').strip()
    m = _CLOCK_OPEN.match(s)
    if not m:
        return s
    rest = s[m.end():].lstrip()
    # the scene clause the clock introduces, set off by a comma ('with the screen black and the network logo up,')
    first = re.split(r'[\s,]', rest, 1)[0].lower() if rest else ''
    if first in _SCENE_LEAD:
        cut = rest.find(',')
        end = re.search(r'[.!?]', rest)
        if 0 < cut < 110 and (end is None or cut < end.start()):
            rest = rest[cut + 1:].lstrip()
    if not rest:
        return s
    return rest[0].upper() + rest[1:]


def short_read(text: str, n: int = 2) -> str:
    """The first sentences of a read for the chat: two, or three when the
    first two are short."""
    s = str(text or '').strip()
    if not s:
        return ''
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(])', s) if p.strip()]
    if not parts:
        return s
    out = parts[:n]
    if len(parts) > n and sum(len(p) for p in out) < 150:
        out = parts[:n + 1]
    return ' '.join(out)


def moderator_read(models: Models, twin: dict, q: str, kind: str, framing: str, opts_out: list, by_segment: list, answers: list, brief: dict, prior: dict, roster: list, log=None, in_cut: bool = False,
                   headline: str = '') -> dict:
    """The synthesis: read, read_short (the chat's version), voices,
    probes_next, facts_used; the deterministic write-up when the model
    is off or late. The read and the chat line pass ``cohere_read``: one
    split, stated once, people from the share."""
    tb = _twin_brief(twin)
    user = json.dumps({'question': q, 'kind': kind, 'framing': framing, 'headline': headline or None,
                       'options': [{'id': o['id'], 'label': o['label'], 'lean': o['lean'], 'published_pct': o['pct'], 'published_in_words': plain_share(o['pct']), 'people': o['people'],
                                    'in_the_room': o.get('tally') or f"{len(o['seats'])} of {len(roster)}", 'seats': o['seats']} for o in opts_out],
                       'lean_totals': {k: {'published_pct': round(sum(float(o['pct']) for o in opts_out if o['lean'] == k), 1), 'in_words': plain_share(sum(float(o['pct']) for o in opts_out if o['lean'] == k)),
                                            'people': sum(int(o.get('people') or 0) for o in opts_out if o['lean'] == k),
                                            'in_the_room': f"{sum(len(o['seats']) for o in opts_out if o['lean'] == k)} of {len(roster)}"} for k in ('yes', 'maybe', 'no') if any(o['lean'] == k for o in opts_out)},
                       'by_segment': [{k: v for k, v in sr.items() if k != 'tally'} for sr in by_segment],
                       'answers': [{'seat': a['seat'], 'name': a['name'], 'option': a['option_label'], 'confidence': a['confidence'], 'line': a['line'], 'reason': a['reason']} for a in answers],
                       'facts_brief': {k: v for k, v in (brief or {}).items() if not k.startswith('_')}, 'prior_note': prior.get('note') if prior else None,
                       'audience': {'name': tb['name'], 'size_us': tb['size_us'], 'avid_share_pct': tb['avid_share_pct'], 'lifecycle': tb['lifecycle_read']}}, default=str)[:14000]
    data, call = models.call('moderator', READ_SYSTEM, user, max_tokens=2400, temperature=0.4, tools=None)
    if log:
        log('read', call)
    by_seat = {int(a['seat']): a for a in answers}
    out = {'read': '', 'read_short': '', 'voices': [], 'probes_next': [], 'facts_used': [], 'fallback': False}
    if isinstance(data, dict) and str(data.get('read') or '').strip():
        out['read'] = scrub(str(data['read']).strip())[:1400]
        out['read_short'] = scrub(str(data.get('chat') or '').strip())[:600]
        if in_cut:   # a question on the clock: the read opens with the room, never the clock and the scene
            out['read'] = _drop_clock_opening(out['read'])
            out['read_short'] = _drop_clock_opening(out['read_short'])
        out['read'] = cohere_read(out['read'], opts_out, len(roster), headline)[:1600]
        if out['read_short']:
            out['read_short'] = cohere_read(out['read_short'], opts_out, len(roster), short_read(headline, 1) if headline else '')[:700]
        picks = []
        for v in (data.get('voices') or [])[:VOICES_N + 1]:
            try:
                sid = int(v if not isinstance(v, dict) else (v.get('seat') or 0))
            except (TypeError, ValueError):
                continue
            if sid in by_seat and sid not in picks:
                picks.append(sid)
        out['voices'] = picks[:VOICES_N]
        out['probes_next'] = [scrub(str(p).strip()) for p in (data.get('probes_next') or []) if str(p).strip()][:3]
        out['facts_used'] = [scrub(str(f).strip()) for f in (data.get('facts_used') or []) if str(f).strip()][:6]
    if not out['read']:
        out['fallback'] = True
        out['read'] = _fallback_read(q, kind, opts_out, by_segment, prior, headline)
    if not out['read_short']:
        out['read_short'] = short_read(out['read'])
    if not out['voices']:
        # spread across the options in proportion to the split, highest confidence first
        picks = []
        for o in sorted(opts_out, key=lambda o: -float(o.get('pct') or 0)):
            seats = sorted(o['seats'], key=lambda s: -int(by_seat.get(s, {}).get('confidence') or 0))
            for s in seats[:2 if o is opts_out[0] else 1]:
                if s in by_seat and not by_seat[s].get('fallback') and s not in picks:
                    picks.append(s)
        for a in answers:
            if len(picks) >= VOICES_N:
                break
            if int(a['seat']) not in picks:
                picks.append(int(a['seat']))
        out['voices'] = picks[:VOICES_N]
    if not out['facts_used']:
        out['facts_used'] = [scrub(x) for x in facts_lines(brief, 4)]
    if not out['probes_next']:
        out['probes_next'] = ['Why?', 'What would change your mind?'] if kind != 'probe' else ['Show of hands, who would buy it as a gift?', 'What would make this a yes for the no side?']
    return out


def chat_lines(session: dict, rec: dict) -> list:
    """Plain lines for chat: the question as asked, the % table, the read
    and the voices. The moderator's framing is the seats' briefing, never
    a chat line (Jenna, 2026-11-04: launch into what the seats say)."""
    out = [str(rec.get('q') or '')]
    rows = rec.get('options') if (rec.get('options') and isinstance(rec['options'][0], dict) and 'pct' in rec['options'][0]) else (rec.get('distribution') or [])
    for r in rows[:8]:
        tally = f"; in the room: {r['tally']}" if r.get('tally') else ''
        out.append(f"{r['label']}: {r['pct']}% ({int(r['people']):,} people{tally})")
    if rec.get('read'):
        out.append(str(rec['read']))
    for v in (rec.get('voices') or [])[:VOICES_N]:
        out.append(f"{v['name']}, {v['age']}, {v['city']}: {v['line']}")
    return out


def _twin_for_seat(seat: dict, twins_by_key: dict) -> dict:
    return twins_by_key.get(str(seat.get('twin') or '')) or next(iter(twins_by_key.values()), {})


def _cached_brief(s3, key: str, max_age_s: Optional[float] = None) -> dict:
    doc = _get_json(s3, key) if (s3 and key) else None
    if isinstance(doc, dict) and isinstance(doc.get('brief'), dict):
        if max_age_s is not None and (time.time() - float(doc.get('at') or 0)) > float(max_age_s):
            return {}
        b = dict(doc['brief'])
        b['_cached'] = True
        b['_key'] = key
        return b
    return {}


SESSION_LISTS = ('panel_sessions', 'pilot_sessions')


def all_sessions(payload: Optional[dict]) -> list:
    """Every room on a twin that can carry a question: the focus-group
    rooms (panel_sessions) and the Pilot Testing rooms (pilot_sessions).
    The room engine answers both; a Pilot Testing question asked at the
    playhead ran its stages and never landed because every write-back
    looked in panel_sessions alone (Jenna, 2026-11-02, "this never
    landed")."""
    out = []
    for name in SESSION_LISTS:
        for s in (payload or {}).get(name) or []:
            if isinstance(s, dict):
                out.append(s)
    return out


def find_session(payload: Optional[dict], session_id: str) -> Optional[dict]:
    """The room with this id on the twin, whichever list it lives in."""
    sid = str(session_id or '')
    if not sid:
        return None
    return next((s for s in all_sessions(payload) if str(s.get('id') or '') == sid), None)


def _carry(session: dict) -> dict:
    """What a question's landing carries onto the stored session besides
    the record: the talent read (what the room said about the people the
    setup named), rebuilt when the question named one of them."""
    out = {}
    if isinstance(session, dict) and isinstance(session.get('talent_read'), dict):
        out['talent_read'] = session['talent_read']
    return out


def _update_payload(s3, twin_key: str, session_id: str, rec: dict, roster: Optional[list] = None, carry: Optional[dict] = None):
    """The record in the twin payload, replaced in place (load, modify,
    save) so the page's session read sees the stage."""
    if s3 is None or not twin_key:
        return False
    try:
        from migration import digital_twin_synthesis as SYN
        payload = SYN.load_twin(s3, twin_key)
        if not isinstance(payload, dict):
            return False
        sess = find_session(payload, session_id)
        if sess is None:
            return False
        qs = sess.setdefault('questions', [])
        for i, q in enumerate(qs):
            if q.get('id') == rec.get('id'):
                qs[i] = rec
                break
        else:
            qs.append(rec)
        if roster:   # the seats' bios and memory ride along (roster[].persona, roster[].memory)
            by_seat = {int(r.get('seat') or 0): r for r in roster}
            for seat in (sess.get('group') or {}).get('roster') or []:
                src = by_seat.get(int(seat.get('seat') or 0))
                if src:
                    for k in ('persona', 'memory'):
                        if src.get(k) is not None:
                            seat[k] = src[k]
        if rec.get('finished_at'):
            # a finished question that bears on the scorecard moves it (Jenna 2026-11-04: the room said she was the draw)
            try:
                from migration.digital_twin_reconcile import after_question
                after_question(sess, rec, payload, s3=s3, twin_key=twin_key)
            except Exception as e:
                print(f"[twin-room] reconcile skipped: {e}")
        if carry:
            for k, v in carry.items():
                sess[k] = v
            if 'talent_read' in carry:
                try:
                    from migration.digital_twin_talent_read import stamp_rows
                    stamp_rows(sess)
                except Exception:
                    pass
        sess['updated_at'] = _now()
        SYN.save_twin(s3, twin_key, payload)
        return True
    except Exception as e:
        print(f"[twin-room] payload update skipped: {e}")
        return False


def _wait_for_record(s3, twin_key: str, session_id: str, qid: str, timeout: float = 6.0):
    """The route saves the twin right after ask_room returns; the thread
    waits for that save before its first in-place update."""
    if s3 is None or not twin_key:
        return
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            from migration import digital_twin_synthesis as SYN
            payload = SYN.load_twin(s3, twin_key)
            sess = find_session(payload, session_id)
            if sess and any(q.get('id') == qid for q in sess.get('questions') or []):
                return
        except Exception:
            pass
        time.sleep(0.5)


def _publish(ctx: dict, stage: str, status: str = 'running', extra: Optional[dict] = None, payload_too: bool = False):
    rec = ctx['rec']
    rec['stage'] = stage
    rec.setdefault('trail', {}).setdefault('stages', []).append({'stage': stage, 'at': _now(), 't': round(time.time() - ctx['t0'], 2)})
    s3 = ctx.get('s3')
    if s3 is not None:
        doc = {'job_id': ctx['job_id'], 'product': 'digital_twins_iq', 'op': 'panel_ask', 'user': ctx.get('asked_by') or '', 'status': status, 'stage': stage, 'stage_label': STAGE_LABEL.get(stage, stage),
               'twin_key': ctx.get('twin_key') or '', 's3_key': ctx.get('twin_key') or '', 'session_id': ctx['session'].get('id'), 'question_id': rec.get('id'), 'subject': ctx.get('subject') or '',
               'question': {k: v for k, v in rec.items() if k != 'trail'}, 'seats_answered': len(rec.get('answers') or []), 'seats': len(ctx['roster']),
               'started_at': rec.get('started_at'), 'updated_at': time.time(), 'created_at': ctx.get('created_at'),
               'resumes': int(ctx.get('resumes') or 0), 'notify': ctx.get('notify') or {}}
        if extra:
            doc.update(extra)
        _put_json(s3, job_key(ctx['job_id']), doc)
        if payload_too:
            _update_payload(s3, ctx.get('twin_key') or '', ctx['session'].get('id'), rec, (ctx['session'].get('group') or {}).get('roster'), carry=_carry(ctx['session']))


def run_question(ctx: dict) -> dict:
    """The stages, in order, on the calling thread. Always lands an
    answer: a stage that fails falls back deterministically."""
    rec, session, twins_by_key, models = ctx['rec'], ctx['session'], ctx['twins_by_key'], ctx['models']
    s3, roster, seed = ctx.get('s3'), ctx['roster'], ctx['seed']
    q, kind, last, stim = rec['q'], rec['kind'], ctx.get('last'), rec.get('stimulus') or {}
    twin = ctx['twin']
    trail = rec.setdefault('trail', {})
    timings = rec.setdefault('timings', {})     # on the record for the page's stage display; the trail points at the same dict
    trail['timings'] = timings
    fallbacks = trail.setdefault('fallbacks', [])
    log = lambda stage, call: trail.setdefault('call_log', []).append({'stage': stage, **(call or {})})
    try:
        if ctx.get('wait_for_save'):
            _wait_for_record(s3, ctx.get('twin_key') or '', session.get('id'), rec['id'])
        # 1. research: a probe rides the brief of the question it follows; a second question on the same stimulus
        #    (the same topic words, or most of them) rides the brief an earlier question in this room gathered
        t = time.time()
        brief = {}
        subject = str(_meta(twin).get('subject') or _twin_name(twin))
        _reuse_age = 86400.0 if subject_class(twin) == 'civic' else None   # a race's facts turn over by the day
        if kind == 'probe' and last and not ctx.get('fresh_research'):
            brief = _cached_brief(s3, str(((last.get('trail') or {}).get('research_key')) or ''), _reuse_age)
            if brief:
                brief['_reused_from'] = last.get('id')
        if not brief and not ctx.get('fresh_research'):
            for earlier in reversed([x for x in session.get('questions') or [] if x is not rec]):
                ek = str(((earlier.get('trail') or {}).get('research_key')) or '')
                if ek and (ek == research_key(subject, q) or topics_overlap(q, str(earlier.get('q') or ''), subject) >= 0.5):
                    brief = _cached_brief(s3, ek, _reuse_age)
                    if brief:
                        brief['_reused_from'] = earlier.get('id')
                        break
        if not brief and _in_cut(stim):
            # a question on the clock: the cut is the stimulus; the moderator and the seats reason from what just played
            win = stim.get('window') or {}
            brief = {'subject_status': {'line': f"The room is watching {((stim.get('cut') or {}).get('title') or 'the cut')} and the cut was paused at {win.get('clock')}."},
                     'facts': [], 'not_found': [], 'implications': [f"Answer about the moment: {win.get('frame') or win.get('clock')}."],
                     'what_just_played': _moment_view(stim), '_in_cut': True}
        base_q0 = q if kind != 'probe' else str((last or {}).get('q') or q)
        media = {}
        if not _in_cut(stim):
            # the media desk runs alongside the facts desk: what the audience has been seeing, hearing and
            # saying, and how the event came out (Jenna 2026-11-04); a cached facts brief still gets today's media
            from concurrent.futures import ThreadPoolExecutor as _TPE
            _mpool = _TPE(max_workers=1, thread_name_prefix='twin-media')
            _known = []
            for earlier in [x for x in session.get('questions') or [] if x is not rec]:
                _known += [str(f) for f in (earlier.get('facts_used') or [])[:4]]
            f_media = _mpool.submit(media_desk, models, twin, base_q0, kind, stim, s3, log=log, known=_known[-12:])
            if not brief:
                brief = research(models, twin, base_q0, kind, stim, s3, log=log, force=bool(ctx.get('fresh_research')))
            try:
                media = f_media.result(timeout=max(10.0, BUDGET_S['media'] - (time.time() - t))) or {}
            except Exception as e:
                print(f"[twin-room] media desk skipped: {str(e)[:120]}")
                media = {}
                fallbacks.append('media')
            _mpool.shutdown(wait=False)
        if not brief:
            fallbacks.append('research')
        if media:
            brief = merge_media(brief or {'subject_status': {}, 'facts': [], 'not_found': [], 'implications': []}, media)
        trail['media_key'] = media.get('_key')
        trail['media_cached'] = bool(media.get('_cached'))
        rec['talk'] = talk_lines(brief, 8)
        _ev = brief.get('event') or {}
        rec['event'] = {k: _ev.get(k) for k in ('what', 'date', 'status', 'outcome')} if _ev else {}
        rec['outcome'] = str(_ev.get('outcome') or '') if (isinstance(_ev, dict) and str(_ev.get('status') or '') == 'happened') else ''
        trail['research_key'] = brief.get('_key') or research_key(subject, q)
        trail['facts_key'] = facts_key(subject)
        trail['research_cached'] = bool(brief.get('_cached'))
        trail['research_reused_from'] = brief.get('_reused_from')
        trail['facts_brief'] = {k: v for k, v in brief.items() if not k.startswith('_')}
        timings['research_s'] = round(time.time() - t, 2)
        # 2. the moderator codes the options
        t = time.time()
        mo = moderator_options(models, twin, q, kind, list(rec.get('option_labels') or []), stim, brief, last, log=log)
        if mo.get('fallback'):
            fallbacks.append('options')
        options = mo['options']
        rec['framing'] = mo.get('framing') or ''
        rec['option_labels'] = [o['label'] for o in options]
        rec['options'] = [{'id': o['id'], 'label': o['label'], 'lean': o['lean'], 'pct': 0.0, 'people': 0, 'seats': []} for o in options]
        trail['listen_for'] = mo.get('listen_for') or []
        timings['options_s'] = round(time.time() - t, 2)
        # 3. the seats answer, in parallel
        t = time.time()
        _publish(ctx, 'the room is thinking', payload_too=True)
        rec['answers'] = []
        cards = {int(r['seat']): persona_card(_twin_for_seat(r, twins_by_key), r, ctx['session_seed']) for r in roster}
        trail['persona_cards'] = {str(k): {kk: vv for kk, vv in v.items() if kk in ('lifecycle_stage', 'relationship_to_subject', 'owns', 'income')} for k, v in cards.items()}
        memories = {int(r['seat']): memory_for(session, int(r['seat'])) for r in roster}
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        lock = threading.Lock()
        last_pub = [time.time()]

        def _one(seat_no):
            card = cards[seat_no]
            return seat_answer(models, card, q, rec.get('framing') or '', options, brief, memories[seat_no], seed, log=log, stimulus=stim)
        pool = ThreadPoolExecutor(max_workers=max(1, min(len(roster), 16)))
        futures = {pool.submit(_one, int(r['seat'])): int(r['seat']) for r in roster}
        pending = set(futures)
        deadline = time.time() + BUDGET_S['persona']
        while pending and time.time() < deadline:
            done, pending = wait(pending, timeout=max(0.2, min(1.0, deadline - time.time())), return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    a = f.result()
                except Exception as e:
                    print(f"[twin-room] seat {futures[f]} failed: {e}")
                    a = seat_answer(Models(model_fn=lambda *a_, **k_: None), cards[futures[f]], q, rec.get('framing') or '', options, brief, [], seed)
                a['answered_at'] = round(time.time(), 3)   # the page places each seat's line in the chat in the order the seats answered
                with lock:
                    rec['answers'].append(a)
            if time.time() - last_pub[0] >= 1.0 and s3 is not None:
                last_pub[0] = time.time()
                rec['answers'].sort(key=lambda a: int(a['seat']))
                _publish(ctx, 'the room is thinking')
        for f in pending:   # late seats land from habit; the thread keeps running and is dropped
            f.cancel()
            a = seat_answer(Models(model_fn=lambda *a_, **k_: None), cards[futures[f]], q, rec.get('framing') or '', options, brief, [], seed)
            a['answered_at'] = round(time.time(), 3)
            rec['answers'].append(a)
            fallbacks.append(f"seat {futures[f]} late")
        pool.shutdown(wait=False)
        rec['answers'].sort(key=lambda a: int(a['seat']))
        for a in rec['answers']:
            if a.get('fallback') and f"seat {a['seat']} late" not in fallbacks:
                fallbacks.append(f"seat {a['seat']}")
        timings['seats_s'] = round(time.time() - t, 2)
        # 4. the arithmetic: the seat votes smoothed with each segment twin's prior over the moderator's options
        base_q = q if kind != 'probe' else str((last or {}).get('q') or q)
        priors = {}
        for sgm in session['group']['segments']:
            seg_twin = twins_by_key.get(str(sgm.get('twin') or '')) or twin
            priors[sgm['label']] = prior_over_options(seg_twin, base_q, kind, options, stim, last, seed + '|' + sgm['label'])
        trail['prior_over_options'] = {lab: {k: round(v, 4) for k, v in pr.items()} for lab, pr in priors.items()}
        opts_out, by_segment = weigh(rec['answers'], options, roster, session['group']['segments'], priors, seed)
        # the anchor: a counted event sets the published split; a published poll before it leans on it (Jenna 2026-11-04)
        plan = {}
        try:
            plan = anchor_plan(opts_out, brief, subject_class(twin), turnout_fallback=_base_turnout_pct(twin))
            if plan and anchor_rows(opts_out, by_segment, plan, int(sum(int(sg.get('people') or 0) for sg in session['group']['segments'])), len(roster), seed):
                rec['anchor'] = {'kind': plan['kind'], 'line': plan['line'], 'targets': {next((o['label'] for o in opts_out if o['id'] == k), k): v for k, v in plan['targets'].items()}}
                trail['anchor'] = rec['anchor']
            else:
                plan = {}
        except Exception as e:
            print(f"[twin-room] anchor skipped: {str(e)[:120]}")
            plan = {}
        # 4b. the seats mirror the count (Jenna 2026-11-05): the published split is the count; the seats are allocated to it
        #     by largest remainder with a seat for any side worth half a seat, and a moved seat argues its stance in character
        t = time.time()
        total_people = int(sum(int(sg.get('people') or 0) for sg in session['group']['segments']))
        if total_people <= 0:
            try:
                from migration.digital_twin_pilot import room_people as _room_people
                total_people = int(_room_people(roster, session['group']['segments'], twin) or 0)
            except Exception:
                total_people = 0
        mirror = {'alloc': {}, 'moved': [], 'floor_pct': None}
        try:
            mirror = mirror_seats(opts_out, by_segment, rec['answers'], roster, session['group']['segments'])
            if mirror['moved']:
                by_seat0 = {int(a['seat']): a for a in rec['answers']}
                mpool = ThreadPoolExecutor(max_workers=max(1, min(len(mirror['moved']), 16)))
                mfut = {mpool.submit(revoice_seat, models, cards[int(m['seat'])], q, rec.get('framing') or '', options, m['to'], brief, memories[int(m['seat'])], seed, log, stim): int(m['seat'])
                        for m in mirror['moved'] if int(m['seat']) in cards}
                mpending = set(mfut)
                mdeadline = time.time() + BUDGET_S['mirror']
                while mpending and time.time() < mdeadline:
                    mdone, mpending = wait(mpending, timeout=max(0.2, min(1.0, mdeadline - time.time())), return_when=FIRST_COMPLETED)
                    for f in mdone:
                        sid = mfut[f]
                        try:
                            v = f.result()
                        except Exception as e:
                            print(f"[twin-room] seat {sid} revoice failed: {e}")
                            v = revoice_seat(Models(model_fn=lambda *a_, **k_: None), cards[sid], q, rec.get('framing') or '', options, by_seat0[sid]['option'], brief, [], seed)
                        by_seat0[sid].update({'line': v['line'], 'confidence': v['confidence'], 'reason': v['reason'], 'fallback': bool(v.get('fallback'))})
                        by_seat0[sid]['answered_at'] = round(time.time(), 3)
                for f in mpending:
                    f.cancel()
                    sid = mfut[f]
                    v = revoice_seat(Models(model_fn=lambda *a_, **k_: None), cards[sid], q, rec.get('framing') or '', options, by_seat0[sid]['option'], brief, [], seed)
                    by_seat0[sid].update({'line': v['line'], 'confidence': v['confidence'], 'reason': v['reason'], 'fallback': True})
                    fallbacks.append(f"seat {sid} late")
                mpool.shutdown(wait=False)
            retally(opts_out, by_segment, rec['answers'], roster)
        except Exception as e:
            traceback.print_exc()
            print(f"[twin-room] mirror skipped: {str(e)[:120]}")
            fallbacks.append('mirror')
        by_label = {o['id']: o['label'] for o in opts_out}
        trail['mirror'] = {'alloc': {by_label.get(k, k): v for k, v in (mirror.get('alloc') or {}).items()}, 'floor_pct': mirror.get('floor_pct'),
                           'moved': [{'seat': m['seat'], 'from': by_label.get(m['from'], m['from']), 'to': by_label.get(m['to'], m['to'])} for m in mirror.get('moved') or []]}
        timings['mirror_s'] = round(time.time() - t, 2)
        rec['options'] = opts_out
        rec['option_labels'] = [o['label'] for o in opts_out]
        rec['distribution'] = [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts_out]
        rec['by_segment'] = by_segment
        rec['seats_total'] = len(roster)
        rec['tally'] = [{'label': o['label'], 'seats': o['seats_n'], 'of': len(roster), 'room_pct': o['room_pct']} for o in opts_out]
        rec['yes_pct'] = round(sum(float(o['pct']) for o in opts_out if o['lean'] == 'yes'), 1) if any(o['lean'] == 'yes' for o in opts_out) else None
        rec['headline'], rec['headline_people'] = headline_for(opts_out, total_people, len(roster), _twin_name(twin))
        prior = prior_note(twin, base_q, kind, opts_out, stim, last, seed)
        if plan:
            prior = dict(prior or {})
            prior['anchor'] = {'kind': plan['kind'], 'line': plan['line']}
            prior['note'] = (plan['line'] + ' ' + str(prior.get('note') or '')).strip()
        trail['prior'] = prior
        # 5. the moderator reads the room
        t = time.time()
        _publish(ctx, 'moderator is reading', payload_too=True)
        mr = moderator_read(models, twin, q, kind, rec.get('framing') or '', opts_out, by_segment, rec['answers'], brief, prior, roster, log=log, in_cut=_in_cut(stim), headline=rec['headline'])
        if mr.get('fallback'):
            fallbacks.append('read')
        by_seat = {int(a['seat']): a for a in rec['answers']}
        seats = {int(r['seat']): r for r in roster}
        rec['read'] = mr['read']
        rec['read_short'] = mr.get('read_short') or short_read(mr['read'])
        rec['voices'] = []
        for sid in mr['voices']:
            a, r = by_seat.get(sid), seats.get(sid)
            if a and r:
                v = {'seat': sid, 'name': r['name'], 'age': r['age'], 'city': r['city'], 'segment': r['segment'], 'line': a['line']}
                if r.get('portrait_url'):
                    v['portrait_url'] = r['portrait_url']
                rec['voices'].append(v)
        rec['probes_next'] = mr['probes_next']
        rec['facts_used'] = mr['facts_used']
        if rec.get('outcome'):
            _oc = str(rec['outcome']).strip()
            _ock = re.sub(r'[^a-z0-9]+', ' ', _oc.lower()).strip()[:60]
            if not any(re.sub(r'[^a-z0-9]+', ' ', str(f).lower()).strip()[:60] == _ock for f in rec['facts_used']):
                rec['facts_used'] = ([_oc] + list(rec['facts_used']))[:6]
        timings['read_s'] = round(time.time() - t, 2)
    except Exception as e:
        traceback.print_exc()
        fallbacks.append(f"engine: {str(e)[:120]}")
        _deterministic_fill(ctx)
    # 6. the trail and the landing
    tot = models.totals()
    trail.update({'models': tot['models'], 'tokens': tot['tokens'], 'searches': tot['searches'], 'cost_usd': tot['cost_usd'], 'calls': tot['calls'], 'version': ROOM_VERSION})
    timings['total_s'] = round(time.time() - ctx['t0'], 2)
    rec['finished_at'] = _now()
    try:
        refresh_memory(session)
    except Exception:
        pass
    # a Pilot Testing question that bears on the scorecard moves it before anything reads the card (Jenna 2026-11-04: the room
    # said she was the draw); the in-place landing below runs the same step on the stored session
    try:
        if session.get('scorecard'):
            from migration.digital_twin_reconcile import after_question
            after_question(session, rec, twin, s3=s3, twin_key=ctx.get('twin_key') or '')
    except Exception as e:
        print(f"[twin-room] reconcile skipped: {str(e)[:160]}")
    # a Pilot Testing question that names a person the setup named re-reads what the room said about them, against the card's
    # character metrics as they stand now (Jenna 2026-11-04); a read built against older metrics is rebuilt the same way. Last
    # before the landing so it follows every other update the question brought; the landing carries it onto the stored session.
    try:
        if str(session.get('kind') or '') == 'pilot' and session.get('scorecard'):
            from migration import digital_twin_talent_read as TR
            if TR.question_names_talent(session, rec) or TR.is_stale(session):
                TR.attach_talent_read(session, twin, models=models, reason=f"question {rec.get('id')}", log=log)
    except Exception as e:
        print(f"[twin-room] talent read skipped: {str(e)[:160]}")
    # the names attached to the test read the room's verdict (the follow share, excellent, who named them as the reason to come
    # back), so they are recomputed against the card as it stands after the question (Jenna 2026-11-04, the Beals / Caan lift)
    try:
        if session.get('talent_attach') and session.get('scorecard'):
            from migration import digital_twin_compare as CMP
            CMP.recompute_talent_attach(session, twin, s3=s3)
    except Exception as e:
        print(f"[twin-room] talent attach refresh skipped: {str(e)[:160]}")
    ready = '\n'.join(chat_lines(session, rec))
    _publish(ctx, 'done', status='done', extra={'success': True, 'finished_at': time.time(), 'ready_message': ready, 'cost_usd': tot['cost_usd']}, payload_too=True)
    notify = ctx.get('notify') or {}
    if notify.get('thread_id') or notify.get('username'):
        try:
            from prometheus import digital_twin_job as J
            J._thread_note(notify.get('username') or ctx.get('asked_by') or '', notify.get('thread_id') or '', ready,
                           {'twin_key': ctx.get('twin_key') or '', 'twin_job_id': ctx['job_id'], 'open_view': 'digitalTwinsIQ', 'panel_session': {'key': ctx.get('twin_key') or '', 'sid': session.get('id'), 'question_id': rec.get('id')},
                            'options': [{'label': 'Open Focus Group IQ', 'view': 'digitalTwinsIQ', 'tab': 'panel', 'twin_key': ctx.get('twin_key') or '', 'session_id': session.get('id')}]})
        except Exception as e:
            print(f"[twin-room] thread note skipped: {e}")
    print(f"[twin-room] {ctx['job_id']} done in {timings['total_s']}s, ${tot['cost_usd']} ({tot['tokens']['in']} in / {tot['tokens']['out']} out, {tot['searches']} searches), fallbacks={fallbacks}")
    return rec


def _deterministic_fill(ctx: dict):
    """When the engine itself fails, the deterministic engine's answer
    lands under the same record so the question is never left open."""
    from migration import digital_twin_panel as P
    rec, session = ctx['rec'], ctx['session']
    scratch = {k: v for k, v in session.items() if k != 'questions'}
    scratch['questions'] = [q for q in session.get('questions') or [] if q.get('id') != rec['id']]
    old = P.ask(scratch, ctx['twins_by_key'], rec['q'], kind=rec['kind'], options=rec.get('option_labels') or None, stimulus=rec.get('stimulus') or None, room=False)
    rows = old.get('distribution') or []
    rec['options'] = [{'id': chr(97 + i), 'label': r['label'], 'lean': 'other', 'pct': r['pct'], 'people': r['people'], 'seats': [], 'seats_n': 0, 'tally': '', 'room_pct': None} for i, r in enumerate(rows)]
    rec['option_labels'] = [r['label'] for r in rows]
    rec['distribution'] = rows
    rec['tally'] = []
    rec['by_segment'] = old.get('by_segment') or []
    try:
        rec['headline'], rec['headline_people'] = headline_for(rec['options'], int(sum(int(sg.get('people') or 0) for sg in (session.get('group') or {}).get('segments') or [])), 0, _twin_name(ctx.get('twin') or {}))
    except Exception:
        rec['headline'], rec['headline_people'] = '', None
    rec['read'] = scrub(str(old.get('read') or ''))
    rec['read_short'] = short_read(rec['read'])
    rec['voices'] = [dict(v, seat=next((r['seat'] for r in ctx['roster'] if r['name'] == v.get('name')), None)) for v in (old.get('voices') or [])]
    rec.setdefault('answers', [])
    rec['probes_next'] = ['Why?', 'What would change your mind?']
    rec.setdefault('facts_used', [])
    rec['yes_pct'] = old.get('yes_pct')


def ask_room(session: dict, twins_by_key: dict, q: str, *, kind: Optional[str] = None, options: Optional[list] = None, stimulus: Optional[dict] = None,
             asked_by: str = '', s3=None, twin_key: str = '', run_async: bool = True, model_fn: Optional[Callable] = None, usage_extras: Optional[dict] = None,
             notify: Optional[dict] = None) -> dict:
    """Ask the room through the agents. Appends the record at stage
    'researching' and returns it; with run_async the stages run on a
    background thread with the job document as the handoff, else they
    run here and the finished record returns."""
    from migration import digital_twin_panel as P
    from migration.digital_twin_engine import VERSION
    q = ' '.join(str(q or '').split())[:400]
    last = session['questions'][-1] if session.get('questions') else None
    cls = P.classify_question(q, options, last, kind)
    kind = cls['kind']
    stim = dict(stimulus or {})
    if cls.get('price') is not None and 'price' not in stim:
        stim['price'] = cls['price']
    group = session['group']
    s3 = s3 if s3 is not None else _s3_default()
    twin_key = twin_key or str((session.get('twin_keys') or [''])[0] or '')
    twin = twins_by_key.get(twin_key) or twins_by_key.get(str((group.get('segments') or [{}])[0].get('twin') or '')) or next(iter(twins_by_key.values()), {})
    qi = len(session.get('questions') or [])
    job_id = uuid.uuid4().hex[:12]
    rec = {'id': f"q{qi + 1}", 'q': q, 'kind': kind, 'framing': '', 'options': [], 'option_labels': list(cls.get('options') or []), 'distribution': [], 'by_segment': [],
           'read': '', 'read_short': '', 'headline': '', 'headline_people': None, 'voices': [], 'answers': [], 'probes_next': [], 'facts_used': [], 'tally': [], 'seats_total': len(group.get('roster') or []), 'timings': {},
           'stage': 'researching', 'job_id': job_id, 'topic': P.topic_of(q if kind != 'probe' else str((last or {}).get('q') or q)),
           'stimulus': stim, 'follows': last['id'] if (kind == 'probe' and last) else None, 'price': stim.get('price'), 'yes_pct': None,
           'asked_by': asked_by or session.get('created_by'), 'asked_at': _now(), 'started_at': _now(), 'finished_at': None, 'engine': ROOM_VERSION,
           'trail': {'stages': [], 'fallbacks': []}}
    session.setdefault('questions', []).append(rec)
    session['updated_at'] = _now()
    try:
        ensure_personas(session, twins_by_key, f"room|{session['id']}|{VERSION}")   # roster[].persona and roster[].memory for the seat panel
    except Exception as e:
        print(f"[twin-room] personas skipped: {e}")
    ctx = {'rec': rec, 'session': session, 'twins_by_key': twins_by_key, 'twin': twin, 'roster': list(group.get('roster') or []), 'models': Models(model_fn=model_fn, usage_extras=usage_extras),
           's3': s3, 'twin_key': twin_key, 'job_id': job_id, 'seed': f"{session['id']}|{qi}|{q}|{VERSION}|{ROOM_VERSION}", 'session_seed': f"room|{session['id']}|{VERSION}",
           'last': last, 'asked_by': asked_by or session.get('created_by') or '', 'subject': _twin_name(twin), 't0': time.time(), 'created_at': time.time(),
           'notify': notify or {}, 'wait_for_save': bool(run_async)}
    _publish(ctx, 'researching')
    if not run_async:
        return run_question(ctx)
    payload = twins_by_key.get(twin_key)
    if s3 is not None and isinstance(payload, dict) and any(s is session for s in all_sessions(payload)):
        try:   # the record is on the twin before the thread starts; the route's own save is a harmless repeat
            from migration import digital_twin_synthesis as SYN
            SYN.save_twin(s3, twin_key, payload)
            ctx['wait_for_save'] = False
        except Exception as e:
            print(f"[twin-room] early save skipped: {e}")
    threading.Thread(target=run_question, args=(ctx,), daemon=True, name=f"twin-room-{job_id}").start()
    return rec


__all__ = ['ask_room', 'run_question', 'all_sessions', 'find_session', 'SESSION_LISTS', 'research', 'media_desk', 'merge_media', 'talk_lines', 'coverage_lines',
           'match_targets', 'anchor_plan', 'anchor_rows', 'media_key', 'MEDIA_SYSTEM', 'RESEARCH_CLASS_GUIDANCE', 'moderator_options', 'seat_answer', 'moderator_read', 'weigh', 'prior_over_options', 'plain_share', 'persona_card', 'persona_bio', 'ensure_personas', 'refresh_memory',
           'allocate_seats', 'mirror_seats', 'retally', 'revoice_seat', 'headline_for', 'cohere_read', 'num_word', 'MIRROR_FLOOR_SEATS', 'MIRROR_SYSTEM',
           'memory_for', 'prior_note', 'drop_ruled_out', 'scrub', 'has_banned', 'chat_lines', 'short_read', 'room_enabled', 'model_available', 'set_model_fn', 'topic_hash', 'topic_tokens',
           'topics_overlap', 'research_key', 'facts_key', 'job_key', 'job_read', 'Models', 'MODELS', 'BUDGET_S', 'ALPHA_SEATS', 'STAGES', 'STAGE_LABEL', 'ROOM_VERSION',
           'JOB_PREFIX', 'RESEARCH_PREFIX', 'FACTS_PREFIX']
