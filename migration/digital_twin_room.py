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

OUTPUT SHAPE (the question record on session['questions']; the UI reads this)
  {id, q, kind, framing,
   options: [{id, label, lean, pct, people, seats: [seat ids]}],
   option_labels: [str],                       the labels alone
   distribution: [{label, pct, people}],       the same rows for the table (older readers)
   by_segment: [{segment, people, distribution: [{label, pct, people}], yes_pct}],
   read: str,
   voices: [{seat, name, age, city, segment, line}],
   answers: [{seat, name, option, option_label, line, confidence, reason}],
   probes_next: [str], facts_used: [str],
   stage: researching | the room is thinking | moderator is reading | done | stopped,
   job_id, started_at, finished_at, asked_at, asked_by, follows, stimulus, topic, price, yes_pct,
   trail: {research_key, facts_key, models: {research, moderator, persona}, tokens: {in, out, cache_read, cache_write},
           searches, cost_usd, calls: [...], timings: {...}, prior: {...}, stages: [...], fallbacks: [...]}}
  Client-facing strings (read, framing, voices, option labels, facts_used, probes_next, answer lines) pass the house
  scrub: no synthetic, modeled, estimated, research, panel, sample, confidence, pipeline, corpus, lane, no source names,
  no em or en dashes, individual-level nouns. The trail is internal and never rendered.

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

from migration.digital_twin_engine import clamp, unit

ROOM_VERSION = 'room-1'
S3_BUCKET = 'dashboard-inputs'
JOB_PREFIX = 'system/digital_twins/jobs/'
RESEARCH_PREFIX = 'system/digital_twins/research/'
FACTS_PREFIX = 'system/digital_twins/facts/'
STAGES = ('researching', 'the room is thinking', 'moderator is reading', 'done')
STAGE_LABEL = {'researching': 'Researching the question', 'the room is thinking': 'The room is thinking', 'moderator is reading': 'The moderator is reading the room',
               'done': 'Done', 'stopped': 'Stopped'}

MODELS = {
    'research': (os.environ.get('DTIQ_ROOM_RESEARCH_MODEL') or '').strip() or None,     # None: the analysis chain (Opus class first)
    'moderator': (os.environ.get('DTIQ_ROOM_MODERATOR_MODEL') or '').strip() or None,
    'persona': [m for m in ((os.environ.get('DTIQ_ROOM_PERSONA_MODEL') or '').strip(), 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001') if m],
    # the strong chain when the Prometheus host is not bound (a script, the box): the same candidates the analysis chain walks
    'strong': ['claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6'],
}
BUDGET_S = {'research': 75.0, 'options': 40.0, 'persona': 50.0, 'read': 50.0}
MAX_OPTIONS = 5
VOICES_N = 4
RESEARCH_CACHE_TTL_S = 14 * 86400
RESEARCH_MAX_SEARCHES = 4
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
    t = str(s or '')
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
    hits = [m.group(0) for m in _BANNED_RX.finditer(t)] + [m.group(0) for m in _SOURCE_NAMES_RX.finditer(t)]
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
    def _tools():
        """The web_search tool for the research agent, capped at
        RESEARCH_MAX_SEARCHES rounds so a question stays inside the
        budget (the brief is cached per subject + topic)."""
        try:
            import prometheus_analysis as _pma
            return [dict(_pma.WEB_SEARCH_TOOL, max_uses=RESEARCH_MAX_SEARCHES)]
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
        if role == 'persona':
            data, model = self._chain(system, user, max_tokens, temperature, surface, MODELS['persona'])
            if data is not None:
                return data, self._log(role, model, self._stats_now(), t0, True)
        want = MODELS.get(role) if role in ('research', 'moderator') else None
        research_tools = tools if tools is not None else (self._tools() if role == 'research' else None)
        if want:
            data, model = self._chain(system, user, max_tokens, temperature, surface, [want] + [m for m in MODELS['strong'] if m != want], tools=research_tools)
            if data is not None:
                return data, self._log(role, model, self._stats_now(), t0, True)
        cj = self._host_claude()
        if cj is None:
            chain = MODELS['strong'] if role in ('research', 'moderator') else MODELS['persona']
            data, model = self._chain(system, user, max_tokens, temperature, surface, chain, tools=research_tools)
            return data, self._log(role, model, self._stats_now(), t0, data is not None)
        try:
            got = cj(system, user, max_tokens=max_tokens, temperature=temperature, surface=surface, usage_extras=self.extras,
                     tools=(tools if tools is not None else (self._tools() if role == 'research' else None)))
        except TypeError:
            got = cj(system, user)
        except Exception as e:
            print(f"[twin-room] model call failed ({role}): {str(e)[:200]}")
            got = None
        data = got if isinstance(got, dict) and got else None
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


def topic_hash(q: str) -> str:
    """The question's topic: content words without prices and numbers,
    so a re-ask at another price shares the research."""
    words = re.findall(r"[a-z]+", re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", ' ', str(q or '').lower()))
    stop = {'would', 'you', 'the', 'a', 'an', 'for', 'if', 'at', 'to', 'of', 'in', 'on', 'it', 'is', 'do', 'does', 'did', 'and', 'or', 'your', 'be', 'that', 'this',
            'what', 'which', 'who', 'how', 'many', 'much', 'pay', 'buy', 'with', 'from', 'by', 'about', 'them', 'they', 'their', 'we', 'our', 'i', 'me', 'my',
            'sold', 'sell', 'were', 'was', 'are', 'am', 'will', 'can', 'could', 'should', 'make', 'get', 'take', 'go', 'not', 'no', 'yes', 'any', 'some', 'all'}
    keep = sorted({w for w in words if w not in stop and len(w) > 2})
    return hashlib.sha1(' '.join(keep).encode('utf-8')).hexdigest()[:12]


def research_key(subject: str, q: str) -> str:
    return f"{RESEARCH_PREFIX}{_slug(subject)}/{topic_hash(q)}.json"


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
    gender = 'woman' if str(seat.get('gender') or 'F') == 'F' else 'man'
    pron = ('she', 'her') if gender == 'woman' else ('he', 'his')
    lc = (twin or {}).get('lifecycle') or {}
    stages = [('discovering', float(lc.get('discovering_pct') or 15)), ('trying', float(lc.get('trying_pct') or 15)), ('in the habit', float(lc.get('habit_pct') or 40)),
              ('lapsing', float(lc.get('lapsing_pct') or 18)), ('returning', float(lc.get('returning_pct') or 12))]
    stage = _pick_by_share([{'label': a, 'pct': b} for a, b in stages], unit('stage', sid))
    income = _pick_by_share(demo.get('income') or [], unit('income', sid), '')
    education = _pick_by_share(demo.get('education') or [], unit('education', sid), '')
    parental = _pick_by_share(demo.get('parental') or [], unit('parental', sid), '')
    relationship = _pick_by_share(demo.get('relationship') or [], unit('relationship', sid), '')
    fmt = str(m.get('format') or 'series')
    legacy = str(m.get('franchise_stage') or '') in ('legacy', 'established')
    price = {'low': f"spends freely on the things {pron[0]} loves and rarely waits for a sale", 'mid': 'watches price but pays for the right thing',
             'high': 'very price-conscious; compares, waits for deals, hates paying twice for the same thing'}[_level(g('price_sensitivity'), 35, 62)]
    loyal = {'low': 'casual about the title; it is one of many things', 'mid': 'fond of the title and watches when it comes around',
             'high': 'deeply loyal to the title; knows it by heart and rewatches'}[_level(g('franchise_loyalty'), 40, 65)]
    merch = {'low': 'rarely buys merchandise of any kind', 'mid': 'has bought a piece or two of show merchandise over the years',
             'high': 'buys show merchandise now and then: a mug, a shirt, a keepsake, a gift for a friend who loves it too'}[_level(g('merch_propensity'), 35, 60)]
    u_phys = unit('physical', sid)
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
    wom = {'low': f"keeps {pron[1]} viewing to {pron[1]}self" if gender == 'woman' else 'keeps his viewing to himself', 'mid': 'talks about shows with a couple of friends',
           'high': 'texts the group chat about what they are watching and gets friends onto things'}[_level(g('word_of_mouth'), 40, 65)]
    live = {'low': 'does not go to fan events or live shows for this', 'mid': 'has been to one fan event or screening',
            'high': 'goes to fan events, screenings and live shows when they come near'}[_level(g('live_events'), 30, 55)]
    ads = {'low': 'pays extra to skip ads', 'mid': 'takes the cheaper ad tier on some services', 'high': 'fine with ads if it saves money'}[_level(g('ad_tolerance'), 40, 62)]
    binge = 'binges a season in a weekend' if g('binge_vs_appointment') >= 55 else 'watches an episode or two a night'
    second = 'scrolls the phone while watching' if g('second_screen') >= 55 else 'puts the phone down to watch'
    name = str(m.get('name') or m.get('subject') or 'the title')
    if stage == 'discovering':
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
    owns = []
    if 'owns DVDs' in physical and stage in ('in the habit', 'returning') and g('merch_propensity') >= 45:
        owns.append(f"the complete {name} on DVD from years ago")
    if g('merch_propensity') >= 55 and unit('owns', sid) < 0.6:
        owns.append(f"a {name} mug or shirt")
    ident = (twin or {}).get('identity') or {}
    plats = [str(p.get('label')) for p in sorted((ident.get('platforms') or []), key=lambda p: -float(p.get('reach_pct') or 0)) if isinstance(p, dict)][:6]
    services = [p for i, p in enumerate(plats) if unit('pl', sid, p) < (0.95 - 0.12 * i)][:4] or plats[:2]
    shops = [str(p.get('label')) for p in (ident.get('purchase') or [])[:6] if isinstance(p, dict)]
    shops = [s for s in shops if unit('shop', sid, s) < 0.7][:3]
    return {'seat': seat.get('seat'), 'name': seat.get('name'), 'age': age, 'gender': gender, 'city': seat.get('city'), 'segment': str(seat.get('segment') or ''),
            'income': income, 'education': education, 'parental': parental, 'relationship_status': relationship,
            'lifecycle_stage': stage, 'relationship_to_subject': rel, 'how_often': freq, 'owns': owns,
            'habits': [price, loyal, merch, physical, churn, social, wom, live, ads, binge, second],
            'streams_on': services, 'shops_at': shops}


def _card_text(card: dict) -> str:
    lines = [f"{card['name']}, {card['age']}, {card['gender']}, {card['city']}." + (f" Income {card['income']}." if card.get('income') else '') + (f" {card['education']}." if card.get('education') else ''),
             (f"{card['parental']}. " if card.get('parental') else '') + (f"{card['relationship_status']}." if card.get('relationship_status') else ''),
             f"Relationship to the subject: {card['relationship_to_subject']}; {card['how_often']}." + (f" Owns {', '.join(card['owns'])}." if card.get('owns') else ''),
             'Habits: ' + '; '.join(card['habits']) + '.',
             (f"Streams on {', '.join(card['streams_on'])}." if card.get('streams_on') else '') + (f" Shops at {', '.join(card['shops_at'])}." if card.get('shops_at') else '')]
    return '\n'.join(l for l in lines if l.strip())


# ------------------------------------------------------------- prompts
RESEARCH_SYSTEM = """You are the research desk for a focus group moderator. Before the room answers a question you gather what the moderator needs, with the web_search tool when you have it, from approved sources only: SEC filings and earnings reports, Pew Research, Nielsen, Statista, eMarketer, YouGov, Circana (NPD) for physical media and retail sales, Box Office Mojo and The Numbers, Sensor Tower and data.ai, the platforms' own published counts, and trade press (Variety, Deadline, The Hollywood Reporter, Media Play News, Home Media Magazine) for sales history and release facts.
Gather what THIS question needs, no more:
1. The subject's facts: what it is, its status today (running, ended and when, revived), where it streams now, its physical media and merchandise history (complete-series sets, special editions, what they sold for, where).
2. How comparable things did in the past: for a boxed set or collectible, how complete-series sets of ended shows have sold at Target and Walmart, who buys them, price points, holiday skew, published unit or dollar figures; for a new season, how revivals and late seasons drew; for a price change, what comparable services did.
3. How devoted this audience is: revival or event viewing, fan events, merchandise lines, rewatch culture, the avid share the moderator gives you.
4. Anything specific to the stimulus: the price asked, exclusivity, extras, the retailer named.
Rules: never fabricate a number, a date or a sale; a fact you cannot support goes in not_found, said plainly. Tag every fact with its source class. Prefer the most recent figure. Keep every string short.
Return JSON only:
{"subject_status": {"line": "<one sentence: what the subject is and its status today>", "ended": true|false|null, "year_started": <yyyy|null>, "year_ended": <yyyy|null>, "revival": "<what and when, or empty>", "streams_on": ["<service>", ...], "physical_media": "<what exists: complete-series DVD, Blu-ray, special editions, release years, typical price; or empty>", "merch": "<the merchandise lines and licensing that exist, or empty>"},
 "facts": [{"fact": "<one plain sentence with the number, date or finding>", "source_class": "<earnings|SEC filing|Pew|Nielsen|Statista|eMarketer|YouGov|Circana|Box Office Mojo|The Numbers|Sensor Tower|platform published|trade press|retailer listing>", "topic": "subject|comparables|fandom|stimulus", "year": <yyyy|null>}],
 "not_found": ["<what you looked for and could not support>"],
 "implications": ["<two to four sentences on what these facts mean for how this audience answers this question>"]}
At most fourteen facts; the whole answer under 1,600 tokens."""

OPTIONS_SYSTEM = """You are a seasoned focus group moderator. Before the room answers you put the coded answer options on the board for THIS stimulus. Rules:
- Three to five options, mutually exclusive, in the room's own plain words, each one a real way a person in this audience could answer the question as asked.
- True to the facts brief. Never an option the facts rule out: for a show that ended years ago and already streams, 'wait for it to come out' or 'wait until it is free' is not an answer; for a collectible the real options turn on keepsake value, unseen material, giving it as a gift, already owning it, and price. For a show still running, options turn on when and where they would watch.
- Each option carries a lean: yes (a purchase, watch or positive intent), maybe (conditional), no (a decline), other.
- When the question kind is probe, the options are the reasons or the levers you expect to hear, tied to what the room answered on the question it follows.
- Write the framing: how you would put the question to the room in one or two sentences, giving the room the facts it should have in plain words. Never name a source; never use the words research, panel, sample, model, synthetic, estimated, confidence. No em dashes.
Return JSON only: {"options": [{"id": "a", "label": "<short label, under 12 words>", "lean": "yes|maybe|no|other"}], "framing": "<one or two sentences>", "listen_for": ["<two or three things you want to hear from the room>"]}"""

PERSONA_SYSTEM = """You are one person in a focus group, answering as yourself. You are given who you are (a card), what you said earlier in this session, the facts the moderator put in front of the room, and the question with its coded options.
Stay in character: your age, your city, how you came to the subject, your habits with money, merchandise and discs, your streaming life. Reason from the facts the way you would (if the show ended years ago and you can stream it any night, say what that means for you; if you own the DVDs already, say so). Answer the moderator in two or three natural spoken sentences, concrete the way real people are (what you own, what you paid, who you would give it to), with no lists and no hedging. Never mention being an AI, a twin, a persona or a character. When this is a follow-up, stay consistent with what you said earlier. Pick exactly one option by id, or 'other' with a short label when none fits. Give a confidence from 1 (unsure) to 5 (certain) and a one-sentence reason. No em dashes.
Return JSON only: {"option": "<id or other>", "other_label": "<when other>", "line": "<two or three sentences>", "confidence": <1 to 5>, "reason": "<one sentence>"}"""

READ_SYSTEM = """You are the moderator writing up the room for a senior executive. Plain English a high school student follows; the thinking is senior consultant. You are given the question, the options with the room's split (percent and people, already computed; never change a number), the split by segment, every seat's answer, the facts brief and a prior note.
Write:
- read: one paragraph, three to five sentences, on what the room said and why, grounded in the facts and the comparables in plain words. Say what the share means (who these people are, what it would take). Vary the sentence shapes. Never name a source or a publication; never use the words research, model, modeled, synthetic, estimated, panel, sample, confidence, pipeline, data. If the prior note says the seats departed from the audience's own leaning, explain what accounts for it in the read rather than overriding the room.
- voices: the seat ids of the three or four most telling answers, spread across the options in proportion to the split.
- probes_next: two follow-up questions you would ask this room next, in the moderator's words.
- facts_used: two to five one-line facts the read leans on, in plain words with no source names.
No em dashes. Return JSON only: {"read": "...", "voices": [<seat ids>], "probes_next": ["...", "..."], "facts_used": ["..."]}"""


# ---------------------------------------------------------- the stages
def facts_lines(brief: dict, limit: int = 14) -> list:
    out = []
    st = (brief or {}).get('subject_status') or {}
    if isinstance(st, dict) and st.get('line'):
        out.append(str(st['line']))
    for f in (brief or {}).get('facts') or []:
        if isinstance(f, dict) and f.get('fact'):
            out.append(str(f['fact']))
        elif isinstance(f, str):
            out.append(f)
    return out[:limit]


def research(models: Models, twin: dict, q: str, kind: str, stimulus: Optional[dict], s3, *, force: bool = False, log=None) -> dict:
    """The facts brief for this question: cached per subject + topic;
    the subject facts cache rides in and is written when new. {} when
    nothing could be gathered (the moderator reasons from the twin)."""
    subject = str(_meta(twin).get('subject') or _twin_name(twin))
    rkey = research_key(subject, q)
    fkey = facts_key(subject)
    cached = _get_json(s3, rkey) if (s3 is not None and not force) else None
    if isinstance(cached, dict) and cached.get('brief') and (time.time() - float(cached.get('at') or 0)) < RESEARCH_CACHE_TTL_S:
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
    data, call = models.call('research', RESEARCH_SYSTEM, user, max_tokens=2600, temperature=0.2, tools=None)
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
        _put_json(s3, rkey, {'subject': subject, 'question': q, 'topic_hash': topic_hash(q), 'at': time.time(), 'brief': {k: v for k, v in brief.items() if not k.startswith('_')}, 'version': ROOM_VERSION})
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
    user = json.dumps({'question': q, 'kind': kind, 'stimulus': stimulus or {}, 'supplied_options': opts,
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


def seat_answer(models: Models, card: dict, q: str, framing: str, options: list, brief: dict, memory: list, seed: str, log=None) -> dict:
    """One seat's answer in character; the deterministic fallback when
    the model is late or off."""
    user = json.dumps({'you': _card_text(card), 'earlier_in_this_session': memory,
                       'facts_the_moderator_gave_the_room': facts_lines(brief, 12), 'what_could_not_be_confirmed': (brief or {}).get('not_found') or [],
                       'moderator': framing or q, 'question': q, 'options': [{'id': o['id'], 'label': o['label']} for o in options]}, default=str)[:7000]
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


def weigh(answers: list, options: list, roster: list, segments: list) -> tuple:
    """Seats weighted by the people they stand for: (options with pct,
    people and seats; by_segment rows). Percentages sum to 100.0 and
    people to the room, in each segment too."""
    seg_people = {s['label']: int(s.get('people') or 0) for s in segments}
    seg_seats = {}
    for r in roster:
        seg_seats.setdefault(r['segment'], []).append(r)
    weight = {}
    for r in roster:
        n = len(seg_seats.get(r['segment']) or [1])
        weight[int(r['seat'])] = seg_people.get(r['segment'], 0) / float(n or 1)
    by_opt = {o['id']: [] for o in options}
    others = []
    for a in answers:
        if a.get('option') in by_opt:
            by_opt[a['option']].append(int(a['seat']))
        else:
            others.append(int(a['seat']))
    rows_spec = [(o, sorted(by_opt[o['id']])) for o in options]
    if others:
        rows_spec.append(({'id': 'other', 'label': 'Other', 'lean': 'other'}, sorted(others)))
    total = sum(weight.values()) or 1.0
    opts_out = []
    for o, seats in rows_spec:
        ppl = sum(weight.get(s, 0.0) for s in seats)
        opts_out.append({'id': o['id'], 'label': o['label'], 'lean': o.get('lean') or 'other', 'pct': ppl / total * 100.0, 'people': ppl, 'seats': seats})
    _settle(opts_out, int(round(total)))
    by_segment = []
    for s in segments:
        seat_ids = {int(r['seat']) for r in (seg_seats.get(s['label']) or [])}
        rows = []
        for o in opts_out:
            mine = [x for x in o['seats'] if x in seat_ids]
            ppl = sum(weight.get(x, 0.0) for x in mine)
            rows.append({'label': o['label'], 'pct': (ppl / seg_people[s['label']] * 100.0) if seg_people.get(s['label']) else 0.0, 'people': ppl, 'lean': o['lean']})
        _settle(rows, int(s.get('people') or 0))
        by_segment.append({'segment': s['label'], 'people': int(s.get('people') or 0), 'distribution': [{'label': r['label'], 'pct': r['pct'], 'people': r['people']} for r in rows],
                           'yes_pct': round(sum(r['pct'] for r in rows if r.get('lean') == 'yes'), 1)})
    return opts_out, by_segment


def _settle(rows: list, total_people: int):
    """Round the pcts to one decimal summing to 100.0 and the people to
    integers summing to the total; the largest row takes the remainder."""
    if not rows:
        return
    pcts = [round(float(r.get('pct') or 0.0), 1) for r in rows]
    gap = round(100.0 - sum(pcts), 1)
    k = max(range(len(rows)), key=lambda i: float(rows[i].get('pct') or 0.0))
    pcts[k] = round(pcts[k] + gap, 1)
    counts = [int(round(float(r.get('people') or 0.0))) for r in rows]
    counts[k] += int(total_people) - sum(counts)
    for r, p, c in zip(rows, pcts, counts):
        r['pct'] = max(0.0, p)
        r['people'] = max(0, int(c))


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


def _fallback_read(q: str, kind: str, opts_out: list, by_segment: list, prior: dict) -> str:
    rows = sorted(opts_out, key=lambda o: -float(o.get('pct') or 0))
    if not rows:
        return ''
    top = rows[0]
    line = f"{top['label']} leads at {top['pct']}% ({int(top['people']):,} people)"
    if len(rows) > 1:
        line += f", with {rows[1]['label'].lower()} at {rows[1]['pct']}%"
    if len(rows) > 2:
        line += f" and {rows[2]['label'].lower()} at {rows[2]['pct']}%"
    line += '.'
    yes = round(sum(float(o.get('pct') or 0) for o in opts_out if o.get('lean') == 'yes'), 1)
    if any(o.get('lean') == 'yes' for o in opts_out):
        line += f" Taken together, {yes}% of the room is a yes."
    if len(by_segment) > 1:
        best = max(by_segment, key=lambda s: float(s.get('yes_pct') or 0))
        worst = min(by_segment, key=lambda s: float(s.get('yes_pct') or 0))
        if best is not worst and abs(float(best.get('yes_pct') or 0) - float(worst.get('yes_pct') or 0)) >= 1.5:
            line += f" Strongest among {best['segment']} ({best['yes_pct']}%), softest among {worst['segment']} ({worst['yes_pct']}%)."
    return line


def moderator_read(models: Models, twin: dict, q: str, kind: str, framing: str, opts_out: list, by_segment: list, answers: list, brief: dict, prior: dict, roster: list, log=None) -> dict:
    """The synthesis: read, voices, probes_next, facts_used; the
    deterministic write-up when the model is off or late."""
    tb = _twin_brief(twin)
    user = json.dumps({'question': q, 'kind': kind, 'framing': framing,
                       'options': [{'id': o['id'], 'label': o['label'], 'lean': o['lean'], 'pct': o['pct'], 'people': o['people'], 'seats': o['seats']} for o in opts_out],
                       'by_segment': by_segment,
                       'answers': [{'seat': a['seat'], 'name': a['name'], 'option': a['option_label'], 'confidence': a['confidence'], 'line': a['line'], 'reason': a['reason']} for a in answers],
                       'facts_brief': {k: v for k, v in (brief or {}).items() if not k.startswith('_')}, 'prior_note': prior.get('note') if prior else None,
                       'audience': {'name': tb['name'], 'size_us': tb['size_us'], 'avid_share_pct': tb['avid_share_pct'], 'lifecycle': tb['lifecycle_read']}}, default=str)[:14000]
    data, call = models.call('moderator', READ_SYSTEM, user, max_tokens=2400, temperature=0.4, tools=None)
    if log:
        log('read', call)
    by_seat = {int(a['seat']): a for a in answers}
    out = {'read': '', 'voices': [], 'probes_next': [], 'facts_used': [], 'fallback': False}
    if isinstance(data, dict) and str(data.get('read') or '').strip():
        out['read'] = scrub(str(data['read']).strip())[:1400]
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
        out['read'] = _fallback_read(q, kind, opts_out, by_segment, prior)
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
    """Plain lines for chat: the question, the % table, the read and
    the voices."""
    out = [str(rec.get('q') or '')]
    if rec.get('framing'):
        out.append(str(rec['framing']))
    for r in (rec.get('distribution') or [])[:8]:
        out.append(f"{r['label']}: {r['pct']}% ({int(r['people']):,} people)")
    if rec.get('read'):
        out.append(str(rec['read']))
    for v in (rec.get('voices') or [])[:VOICES_N]:
        out.append(f"{v['name']}, {v['age']}, {v['city']}: {v['line']}")
    return out


def _twin_for_seat(seat: dict, twins_by_key: dict) -> dict:
    return twins_by_key.get(str(seat.get('twin') or '')) or next(iter(twins_by_key.values()), {})


def _cached_brief(s3, key: str) -> dict:
    doc = _get_json(s3, key) if (s3 and key) else None
    if isinstance(doc, dict) and isinstance(doc.get('brief'), dict):
        b = dict(doc['brief'])
        b['_cached'] = True
        b['_key'] = key
        return b
    return {}


def _update_payload(s3, twin_key: str, session_id: str, rec: dict):
    """The record in the twin payload, replaced in place (load, modify,
    save) so the page's session read sees the stage."""
    if s3 is None or not twin_key:
        return False
    try:
        from migration import digital_twin_synthesis as SYN
        payload = SYN.load_twin(s3, twin_key)
        if not isinstance(payload, dict):
            return False
        sess = next((s for s in payload.get('panel_sessions') or [] if s.get('id') == session_id), None)
        if sess is None:
            return False
        qs = sess.setdefault('questions', [])
        for i, q in enumerate(qs):
            if q.get('id') == rec.get('id'):
                qs[i] = rec
                break
        else:
            qs.append(rec)
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
            sess = next((s for s in (payload or {}).get('panel_sessions') or [] if s.get('id') == session_id), None)
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
               'started_at': rec.get('started_at'), 'updated_at': time.time(), 'created_at': ctx.get('created_at')}
        if extra:
            doc.update(extra)
        _put_json(s3, job_key(ctx['job_id']), doc)
        if payload_too:
            _update_payload(s3, ctx.get('twin_key') or '', ctx['session'].get('id'), rec)


def run_question(ctx: dict) -> dict:
    """The stages, in order, on the calling thread. Always lands an
    answer: a stage that fails falls back deterministically."""
    rec, session, twins_by_key, models = ctx['rec'], ctx['session'], ctx['twins_by_key'], ctx['models']
    s3, roster, seed = ctx.get('s3'), ctx['roster'], ctx['seed']
    q, kind, last, stim = rec['q'], rec['kind'], ctx.get('last'), rec.get('stimulus') or {}
    twin = ctx['twin']
    trail = rec.setdefault('trail', {})
    timings = trail.setdefault('timings', {})
    fallbacks = trail.setdefault('fallbacks', [])
    log = lambda stage, call: trail.setdefault('call_log', []).append({'stage': stage, **(call or {})})
    try:
        if ctx.get('wait_for_save'):
            _wait_for_record(s3, ctx.get('twin_key') or '', session.get('id'), rec['id'])
        # 1. research (a probe rides the brief of the question it follows)
        t = time.time()
        brief = {}
        if kind == 'probe' and last:
            brief = _cached_brief(s3, str(((last.get('trail') or {}).get('research_key')) or ''))
        if not brief:
            brief = research(models, twin, q if kind != 'probe' else str((last or {}).get('q') or q), kind, stim, s3, log=log)
        if not brief:
            fallbacks.append('research')
        trail['research_key'] = brief.get('_key') or research_key(str(_meta(twin).get('subject') or _twin_name(twin)), q)
        trail['facts_key'] = facts_key(str(_meta(twin).get('subject') or _twin_name(twin)))
        trail['research_cached'] = bool(brief.get('_cached'))
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
            return seat_answer(models, card, q, rec.get('framing') or '', options, brief, memories[seat_no], seed, log=log)
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
                with lock:
                    rec['answers'].append(a)
            if time.time() - last_pub[0] >= 1.0 and s3 is not None:
                last_pub[0] = time.time()
                rec['answers'].sort(key=lambda a: int(a['seat']))
                _publish(ctx, 'the room is thinking')
        for f in pending:   # late seats land from habit; the thread keeps running and is dropped
            f.cancel()
            a = seat_answer(Models(model_fn=lambda *a_, **k_: None), cards[futures[f]], q, rec.get('framing') or '', options, brief, [], seed)
            rec['answers'].append(a)
            fallbacks.append(f"seat {futures[f]} late")
        pool.shutdown(wait=False)
        rec['answers'].sort(key=lambda a: int(a['seat']))
        for a in rec['answers']:
            if a.get('fallback') and f"seat {a['seat']} late" not in fallbacks:
                fallbacks.append(f"seat {a['seat']}")
        timings['seats_s'] = round(time.time() - t, 2)
        # 4. the arithmetic: seats weighted into the room
        opts_out, by_segment = weigh(rec['answers'], options, roster, session['group']['segments'])
        rec['options'] = opts_out
        rec['option_labels'] = [o['label'] for o in opts_out]
        rec['distribution'] = [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts_out]
        rec['by_segment'] = by_segment
        rec['yes_pct'] = round(sum(float(o['pct']) for o in opts_out if o['lean'] == 'yes'), 1) if any(o['lean'] == 'yes' for o in opts_out) else None
        prior = prior_note(twin, q if kind != 'probe' else str((last or {}).get('q') or q), kind, opts_out, stim, last, seed)
        trail['prior'] = prior
        # 5. the moderator reads the room
        t = time.time()
        _publish(ctx, 'moderator is reading', payload_too=True)
        mr = moderator_read(models, twin, q, kind, rec.get('framing') or '', opts_out, by_segment, rec['answers'], brief, prior, roster, log=log)
        if mr.get('fallback'):
            fallbacks.append('read')
        by_seat = {int(a['seat']): a for a in rec['answers']}
        seats = {int(r['seat']): r for r in roster}
        rec['read'] = mr['read']
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
    rec['options'] = [{'id': chr(97 + i), 'label': r['label'], 'lean': 'other', 'pct': r['pct'], 'people': r['people'], 'seats': []} for i, r in enumerate(rows)]
    rec['option_labels'] = [r['label'] for r in rows]
    rec['distribution'] = rows
    rec['by_segment'] = old.get('by_segment') or []
    rec['read'] = scrub(str(old.get('read') or ''))
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
           'read': '', 'voices': [], 'answers': [], 'probes_next': [], 'facts_used': [], 'stage': 'researching', 'job_id': job_id, 'topic': P.topic_of(q if kind != 'probe' else str((last or {}).get('q') or q)),
           'stimulus': stim, 'follows': last['id'] if (kind == 'probe' and last) else None, 'price': stim.get('price'), 'yes_pct': None,
           'asked_by': asked_by or session.get('created_by'), 'asked_at': _now(), 'started_at': _now(), 'finished_at': None, 'engine': ROOM_VERSION,
           'trail': {'stages': [], 'fallbacks': []}}
    session.setdefault('questions', []).append(rec)
    session['updated_at'] = _now()
    ctx = {'rec': rec, 'session': session, 'twins_by_key': twins_by_key, 'twin': twin, 'roster': list(group.get('roster') or []), 'models': Models(model_fn=model_fn, usage_extras=usage_extras),
           's3': s3, 'twin_key': twin_key, 'job_id': job_id, 'seed': f"{session['id']}|{qi}|{q}|{VERSION}|{ROOM_VERSION}", 'session_seed': f"room|{session['id']}|{VERSION}",
           'last': last, 'asked_by': asked_by or session.get('created_by') or '', 'subject': _twin_name(twin), 't0': time.time(), 'created_at': time.time(),
           'notify': notify or {}, 'wait_for_save': bool(run_async)}
    _publish(ctx, 'researching')
    if not run_async:
        return run_question(ctx)
    payload = twins_by_key.get(twin_key)
    if s3 is not None and isinstance(payload, dict) and any(s is session for s in payload.get('panel_sessions') or []):
        try:   # the record is on the twin before the thread starts; the route's own save is a harmless repeat
            from migration import digital_twin_synthesis as SYN
            SYN.save_twin(s3, twin_key, payload)
            ctx['wait_for_save'] = False
        except Exception as e:
            print(f"[twin-room] early save skipped: {e}")
    threading.Thread(target=run_question, args=(ctx,), daemon=True, name=f"twin-room-{job_id}").start()
    return rec


__all__ = ['ask_room', 'run_question', 'research', 'moderator_options', 'seat_answer', 'moderator_read', 'weigh', 'persona_card', 'memory_for', 'prior_note',
           'drop_ruled_out', 'scrub', 'has_banned', 'chat_lines', 'room_enabled', 'model_available', 'set_model_fn', 'topic_hash', 'research_key', 'facts_key',
           'job_key', 'job_read', 'Models', 'MODELS', 'BUDGET_S', 'STAGES', 'STAGE_LABEL', 'ROOM_VERSION', 'JOB_PREFIX', 'RESEARCH_PREFIX', 'FACTS_PREFIX']
