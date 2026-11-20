"""The knowledge layer: one place every answer reads from, in a fixed
order (the Prometheus rebuild, stage 3, 2026-11-20).

1. `held(subject)`: the figures the company holds. The corpus catalog's
   facts on the subject (profiles, trackers, journeys, valuations,
   decks, chat figures) and the profile-level size facts.
2. `stored(ask)`: the answers already given. The insights ledger,
   keyed on the ask (subject, product, metric family, window, scope)
   and matched on that key first, the question's words as a tie-break.
   The near twin of the ask (prometheus.consistency) rides along as the
   anchor a new figure stays close to.
3. `research(ask)`: outside research as a step of its own, not a tool
   the reasoning call may or may not pick up (the review found 67% of
   generated reads made no search). One call with web search returns
   anchors (value, unit, window, class, kind of source) and dated events
   into the anchor store `system/pm_anchors/<key>.json`; a later ask on
   the same key reads them back without a call.
4. `facts_block(ask, ...)`: the one prompt block the three paths hand
   their model (held, stored, the near twin, the anchors), each line
   marked internally with where it came from.

The marks are for the ask log and the record only. The block is prompt
text; what a reader sees is the model's output after the exits, and the
consistency lines are fixed house sentences (prometheus.consistency).
No source name, no method word reaches a reader through this module.

Wiring:

- the generated read (`_pm_generate_read_core` in the legacy file) calls
  `read_blocks(...)` for its catalog / saved-valuation / Peacock block,
  which now also carries the near twin and the researched anchors, and
  leaves the twin for the hold that runs inside `read_repair.coherent`
  (`hold_pending`);
- the valuation (`migration/bpiq_synthesis._synthesize`) calls
  `valuation_events(...)` for the research override of the near-twin
  check and the pieces registry for its press;
- the insight lane (`prometheus/insight_ask._core`) calls
  `insight_blocks(...)` for its ledger block and anchors.

Kill switches, env only: `PM_KNOWLEDGE=0` (the layer hands back the
blocks the paths assembled before it, nothing researched),
`PM_KNOWLEDGE_RESEARCH=0` (no research call; stored anchors still read).
Fail-safe everywhere: any error returns what the path had.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
import time
from typing import Callable, Optional

from . import consistency as _twin
from . import pieces as _pieces

ENV = 'PM_KNOWLEDGE'
ENV_RESEARCH = 'PM_KNOWLEDGE_RESEARCH'
ANCHOR_PREFIX = 'system/pm_anchors/'
ANCHOR_FRESH_S = 14 * 24 * 3600          # anchors on an open window are researched again after two weeks
PRODUCTS = ('read', 'valuation', 'insight', 'journey', 'profile')

RESEARCH_PROMPT = """You gather outside anchors for one measurement question before it is
answered. Use web search when available. Approved sources only: company
filings and earnings reports, platform-reported figures, Pew, Nielsen,
Comscore, Statista, eMarketer, YouGov, Sensor Tower, data.ai, trade and
business press that states a figure. Return STRICT JSON only:

{
  "anchors": [
    {"name": str,                 // what the figure is (US monthly users, US viewers of the title, box office, units sold)
     "value": number,             // the figure as stated
     "unit": str,                 // people, usd, pct, views, units, hours
     "window": {"start": "YYYY-MM-DD"|null, "end": "YYYY-MM-DD"|null},
     "klass": str,                // footprint, audience, sales, reach, price, share
     "source_type": str,          // filing, earnings, platform, survey, tracker, press
     "as_of": "YYYY-MM-DD"|null}
  ],
  "events": [
    {"date": "YYYY-MM-DD",        // only an event you can date to a day
     "kind": "release"|"premiere"|"launch"|"campaign"|"press_cluster"|"award"|"appearance",
     "name": str, "where": str}
  ],
  "sources": [{"title": str, "url": str, "used_for": str}]
}

Rules: a figure you did not see is not listed. US figures first; say
when a figure is global. Events only inside the date ranges given when
ranges are given; otherwise inside the question's window. Nothing here
is shown to a reader; it is working material."""


# --------------------------------------------------------------- switches
def enabled() -> bool:
    return os.environ.get(ENV, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def research_enabled() -> bool:
    return enabled() and os.environ.get(ENV_RESEARCH, '1').strip().lower() not in ('0', 'false', 'no', 'off')


# ------------------------------------------------------------------- ask
def _date(s) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(s or '')[:10])
    except ValueError:
        return None


def _window_of(w) -> dict:
    """{start, end} from a dict, a 'YYYY-MM-DD to YYYY-MM-DD' string, or
    nothing."""
    if isinstance(w, dict):
        return {'start': str(w.get('start') or '')[:10], 'end': str(w.get('end') or '')[:10]}
    s = str(w or '')
    ds = re.findall(r'\d{4}-\d{2}-\d{2}', s)
    if len(ds) >= 2:
        return {'start': ds[0], 'end': ds[1]}
    return {'start': '', 'end': ''}


def _fold(s) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


def ask_from(body=None, text: str = '', base=None, mr=None, **over) -> dict:
    """The ask the layer keys on: {subject, product, family, window,
    scope, platform, text}. The Understanding on the body is the first
    source (stage 2: `body['_pm_understanding']`), then the base, then
    the measurement request. Keyword overrides win."""
    b = body if isinstance(body, dict) else {}
    u = b.get('_pm_understanding') if isinstance(b.get('_pm_understanding'), dict) else {}
    if not u:
        u = understanding_on_request()
    base = base if isinstance(base, dict) else {}
    mr = mr if isinstance(mr, dict) else {}
    subject = ''
    for s in (u.get('subjects') or []):
        if isinstance(s, dict) and str(s.get('role') or 'subject') in ('subject', 'title'):
            subject = str(s.get('catalog') or s.get('as_written') or '').strip()
            if subject:
                break
    if not subject:
        subject = str(u.get('named_title') or base.get('subject') or mr.get('subject') or '').strip()
    window = _window_of(over.get('window') or mr.get('window') or u.get('window') or base.get('window'))
    try:
        import insights_ledger as _il
        fam = _il.canon_family(over.get('family') or mr.get('family') or mr.get('metric_family') or '')
    except Exception:
        fam = str(over.get('family') or mr.get('family') or 'audience')
    scope = str(over.get('scope') or mr.get('cohort') or mr.get('dimension') or '').strip()
    out = {
        'subject': str(over.get('subject') or subject),
        'product': str(over.get('product') or 'read'),
        'family': fam,
        'window': window,
        'scope': scope,
        'platform': str(over.get('platform') or u.get('platform') or mr.get('platform') or '').strip(),
        'text': str(text or ''),
    }
    for k in ('talent', 'brand', 'property'):
        if over.get(k):
            out[k] = str(over[k])
    return out


def understanding_on_request() -> dict:
    """The Understanding the ask service left on the request (stage 2
    writes `g._pm_understanding`), for a core that is handed no body."""
    try:
        from flask import g as _g, has_app_context
        if has_app_context():
            u = getattr(_g, '_pm_understanding', None)
            return u if isinstance(u, dict) else {}
    except Exception:
        pass
    return {}


_OWN_WORDS = frozenset(('crosswalk', 'prometheus', 'cw', 'profile iq', 'brand partnership iq', 'digital journey iq', 'subscriber iq',
                        'focus group iq', 'digital twins iq', 'attribution iq', 'trends iq', 'impact iq', 'analysis iq', 'gen pop', 'genpop'))


def named_subject(text, ask: Optional[dict] = None, base: Optional[dict] = None) -> str:
    """The subject the words name, in subject position: the
    Understanding's subject or title; else the universe the question
    names (prometheus.question_universe); else the house guesser's
    plausible subject. '' when the words name none, when they name our
    own product, or when the only name is the base's own."""
    u = understanding_on_request()
    for sdict in (u.get('subjects') or []):
        if isinstance(sdict, dict) and str(sdict.get('role') or 'subject') in ('subject', 'title'):
            name = str(sdict.get('catalog') or sdict.get('as_written') or '').strip()
            if name and _fold(name) not in {_fold(w) for w in _OWN_WORDS}:
                return name
    if u.get('named_title'):
        return str(u['named_title']).strip()
    try:
        from . import question_universe as _qu
        uni = _qu.question_universe(str(text or ''), '')
        if getattr(uni, 'kind', '') == 'named' and getattr(uni, 'explicit', False) and getattr(uni, 'label', ''):
            return str(uni.label).strip()
    except Exception:
        pass
    try:
        from . import product_facts as _pf
        if _pf.looks_like(text):
            return ''
    except Exception:
        pass
    try:
        import prometheus_analysis as _pma
        from . import referents as _refs
        g = str(_pma.guess_subject_from_text(str(text or '')) or '').strip()
        if g and _refs.plausible_subject(g) and _fold(g) not in {_fold(w) for w in _OWN_WORDS} and len(_fold(g)) >= 3:
            return g
    except Exception:
        pass
    return ''


def base_is_borrowed(text, named: str, base: Optional[dict]) -> bool:
    """True when the read's base is another subject's profile: the words
    name a subject (`named`), the base is a page, catalog or view base
    for a different name, and the words do not name the base's own
    subject (a distinctive token of it). A resume's pin counts as a
    page base; a base the reader chose by its words
    (`chosen_by_words`) never counts."""
    b = base if isinstance(base, dict) else {}
    bs = str(b.get('subject') or '').strip()
    if not (named and bs) or b.get('chosen_by_words'):
        return False
    if str(b.get('source') or '') not in ('page', 'catalog', 'view', ''):
        return False
    if _twin.same_name(named, bs) or _twin.same_name(named, bs.split(' - ')[0]):
        return False
    # the named subject inside the base's own name ('Anne Hathaway' on the
    # Anne Hathaway x Shiseido valuation, 'Yellowstone' on its avid cut):
    # the base is the subject's
    ntoks = [w for w in re.split(r'[^a-z0-9]+', str(named).lower()) if len(w) >= 3]
    btoks = set(re.split(r'[^a-z0-9]+', bs.lower()))
    if ntoks and all(w in btoks for w in ntoks):
        return False
    try:
        from . import named_subject as _ns
        def _norm(x):
            return re.sub(r'[^a-z0-9 ]+', ' ', str(x or '').lower()).strip()
        if _ns.page_named(text, bs, _norm, frozenset(('the', 'a', 'an', 'of', 'and', 'on', 'in', 'for', 'to', 'with'))):
            return False
    except Exception:
        pass
    return True


def ask_key(ask: dict) -> str:
    """'subjectfold|product|family|ws|we|scope': the ledger key's shape
    with the product in front."""
    a = ask if isinstance(ask, dict) else {}
    w = _window_of(a.get('window'))
    try:
        import insights_ledger as _il
        subj = _il.fold_subject(a.get('subject')).replace(' ', '_')[:80] or _il.subject_key(a.get('subject'))
    except Exception:
        subj = _fold(a.get('subject'))[:80] or 'unknown'
    scope = _fold(a.get('scope'))[:40]
    return f"{subj}|{a.get('product') or 'read'}|{a.get('family') or 'audience'}|{w['start'] or 'any'}|{w['end'] or 'any'}|{scope}"


# ------------------------------------------------------------------ held
def held(subject, window=None, products=None, extra_subjects=(), bpiq_source_key=None) -> dict:
    """The figures the company holds on the subject: the catalog's facts
    (newest first, the ask's window first), the profile-level sizes and
    the prompt block. With `bpiq_source_key` (a read about one stored
    valuation) the other valuation files on the subject leave the block,
    so no other run's figures ride as binding. Never raises."""
    out = {'subject': str(subject or '').strip(), 'facts': [], 'profile': None, 'block': '', 'marks': []}
    if not enabled() or not out['subject']:
        return out
    try:
        from migration import corpus_catalog as _cc
        names = [out['subject']] + [str(x or '').strip() for x in (extra_subjects or ())]
        names = [n for n in dict.fromkeys(names) if n]
        parts = []
        for n in names[:3]:
            anchors = _cc.anchors_for(n, window=window, products=products, with_ledger=False)
            facts = list(anchors.get('facts') or [])
            if bpiq_source_key:
                want = str(bpiq_source_key).split('/')[-1]
                facts = [f for f in facts if not (isinstance(f, dict) and f.get('product') == 'bpiq'
                                                 and str(((f.get('source') or {}).get('key')) or f.get('source_key') or '').split('/')[-1] != want)]
                anchors = dict(anchors, facts=facts)
            out['facts'].extend(facts)
            if out['profile'] is None:
                try:
                    out['profile'] = _cc.profile_anchor(anchors, window)
                except Exception:
                    out['profile'] = None
            blk = _cc.anchors_block(anchors)
            if blk:
                parts.append(blk)
            for f in facts[:60]:
                out['marks'].append({'kind': 'held', 'product': f.get('product'), 'ref': (f.get('source') or {}).get('key'),
                                     'as_of': f.get('as_of')})
        out['block'] = '\n\n'.join(parts)
    except Exception as e:
        print(f"[pm-knowledge] held lookup skipped: {type(e).__name__}: {e}")
    return out


# ---------------------------------------------------------------- stored
def stored(ask: dict, question: str = '', history=None, prior_question: str = '') -> dict:
    """The answers already given on the ask: the ledger's entries and
    block, the exact replay when one fits, and the near twin of the ask
    (the earliest banked read on the subject whose family matches and
    whose window is near). Never raises."""
    out = {'entries': [], 'exact': None, 'twin': None, 'block': '', 'marks': [], 'key': ask_key(ask)}
    if not enabled():
        return out
    try:
        import insights_ledger as _il
        a = ask if isinstance(ask, dict) else {}
        w = _window_of(a.get('window'))
        led = _il.consult(subject=a.get('subject') or None, question=question or a.get('text') or None,
                          metric_family=a.get('family') or None,
                          window_start=w['start'] or None, window_end=w['end'] or None,
                          prior_question=prior_question or None, history=history)
        out['entries'] = list(led.get('entries') or [])
        out['exact'] = led.get('exact')
        out['block'] = str(led.get('block') or '')
        out['subject'] = led.get('subject')
        for e in out['entries'][:40]:
            if isinstance(e, dict):
                out['marks'].append({'kind': 'stored', 'ref': e.get('k'), 'as_of': e.get('ts'), 'route': e.get('route')})
        if w['start'] and w['end']:
            try:
                out['twin'] = _twin.near_twin_entry({'subject': led.get('subject') or a.get('subject'),
                                                     'family': a.get('family'), 'window': w}, out['entries'])
            except Exception as e:
                print(f"[pm-knowledge] twin lookup skipped: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"[pm-knowledge] stored lookup skipped: {type(e).__name__}: {e}")
    return out


# -------------------------------------------------------------- research
STORE_FOR_TESTS = None


def anchor_store():
    if STORE_FOR_TESTS is not None:
        return STORE_FOR_TESTS
    return _pieces.default_store()


def _anchor_doc_key(key: str) -> str:
    return f"{ANCHOR_PREFIX}{hashlib.sha1(str(key).encode()).hexdigest()[:20]}.json"


def _unwrap(result):
    """The parsed object from either model-call shape: the transport
    envelope ({'success', 'data'}) or the object itself."""
    if isinstance(result, dict) and 'success' in result and ('data' in result or 'error' in result):
        if not result.get('success'):
            return {}
        d = result.get('data')
        if isinstance(d, list):
            d = next((x for x in d if isinstance(x, dict)), {})
        return d if isinstance(d, dict) else {}
    if isinstance(result, list):
        result = next((x for x in result if isinstance(x, dict)), {})
    return result if isinstance(result, dict) else {}


def _num(v):
    try:
        if v is None or v == '' or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean_anchors(raw) -> list:
    out = []
    for a in (raw or []):
        if not isinstance(a, dict):
            continue
        v = _num(a.get('value'))
        name = str(a.get('name') or '').strip()
        if v is None or not name:
            continue
        out.append({'name': name[:120], 'value': v, 'unit': str(a.get('unit') or '').strip()[:24],
                    'window': _window_of(a.get('window')), 'klass': str(a.get('klass') or '').strip()[:24],
                    'source_type': str(a.get('source_type') or '').strip()[:24],
                    'as_of': str(a.get('as_of') or '')[:10]})
    return out[:40]


def _clean_events(raw) -> list:
    out = []
    for e in (raw or []):
        if not isinstance(e, dict):
            continue
        d = _date(e.get('date'))
        kind = str(e.get('kind') or '').strip().lower()
        if not d or kind not in _twin.EVENT_KINDS:
            continue
        out.append({'date': d.isoformat(), 'kind': kind, 'name': str(e.get('name') or '').strip()[:120],
                    'where': str(e.get('where') or '').strip()[:80]})
    return out[:40]


def _fresh(doc: dict, ask_window: dict) -> bool:
    """A saved research document is read back when its window is closed
    (the facts of a finished window do not change) or when it is under
    two weeks old."""
    try:
        ts = float(doc.get('saved_ts') or 0)
    except (TypeError, ValueError):
        ts = 0.0
    end = _date((ask_window or {}).get('end'))
    today = _dt.date.today()
    if end and end < today - _dt.timedelta(days=3):
        return True
    return bool(ts) and (time.time() - ts) < ANCHOR_FRESH_S


def research(ask: dict, claude_json: Optional[Callable] = None, tools=None, changed_windows=None,
             store=None, force: bool = False) -> dict:
    """Outside research as a step. Returns {'anchors', 'events', 'block',
    'marks', 'cached', 'key'}. The anchor store is read first; one call
    with web search when nothing usable is stored (or `force`); the
    result is saved under the ask key. With `changed_windows` the call
    is scoped to dated events inside those ranges. Never raises."""
    a = ask if isinstance(ask, dict) else {}
    key = ask_key(a)
    out = {'anchors': [], 'events': [], 'block': '', 'marks': [], 'cached': False, 'key': key}
    if not enabled():
        return out
    try:
        store = store if store is not None else anchor_store()
        if changed_windows:
            key = key + '|changed:' + hashlib.sha1(json.dumps(changed_windows, sort_keys=True).encode()).hexdigest()[:8]
            out['key'] = key
        saved = None
        if store is not None and not force:
            try:
                saved = store.load(_anchor_doc_key(key))
            except Exception:
                saved = None
            if not (isinstance(saved, dict) and (saved.get('anchors') or saved.get('events') is not None)) and not changed_windows:
                # the same ask banked without a window (a record saved for
                # every future pull, 2026-11-20: VICE Sports) answers a
                # dated ask whose own window moved a day; the standing
                # default moves daily and must not re-research a subject
                # the house already sized
                try:
                    open_key = ask_key(dict(a, window=None))
                    if open_key != key:
                        saved = store.load(_anchor_doc_key(open_key))
                except Exception:
                    saved = None
        if isinstance(saved, dict) and (saved.get('anchors') or saved.get('events') is not None) and _fresh(saved, a.get('window') or {}):
            out['anchors'] = _clean_anchors(saved.get('anchors'))
            out['events'] = _clean_events(saved.get('events'))
            out['cached'] = True
        elif research_enabled() and callable(claude_json):
            ask_doc = {
                'subject': a.get('subject'), 'question': str(a.get('text') or '')[:600],
                'metric_family': a.get('family'), 'window': a.get('window'), 'scope': a.get('scope'),
                'platform': a.get('platform'),
            }
            for k in ('talent', 'brand', 'property'):
                if a.get(k):
                    ask_doc[k] = a[k]
            if changed_windows:
                ask_doc['date_ranges'] = list(changed_windows)
                ask_doc['date_ranges_rule'] = ('Report only dated events inside these ranges: a release or premiere, '
                                               'a campaign or product launch, a run of coverage, an award, a notable appearance.')
            if a.get('size_needed'):
                # a subject we hold no profile on: its audience size is the
                # first anchor (Jenna 2026-11-20); the low end of what the
                # sources support, US people in the window, never a global
                # figure or a follower count standing in for people reached
                ask_doc['audience_size_rule'] = (
                    f"No profile is on file for {a.get('subject')}. The first anchor is its US audience size in the window: "
                    f"the US people who engaged with it (visited, watched, read, followed, searched) over the trailing twelve "
                    f"months, klass 'audience', unit 'people', at the LOW end of what the sources support. A follower or "
                    f"subscriber count is a footprint (klass 'footprint'), a global figure is marked global, and neither stands "
                    f"in for US people reached. Then the anchors the question itself needs.")
            kw = {'max_tokens': 2500, 'temperature': 0.1, 'surface': 'pm_knowledge_research'}
            if tools:
                kw['tools'] = tools
            raw = _unwrap(claude_json(RESEARCH_PROMPT, json.dumps(ask_doc), **kw))
            out['anchors'] = _clean_anchors(raw.get('anchors'))
            out['events'] = _clean_events(raw.get('events'))
            if store is not None:
                try:
                    store.save(_anchor_doc_key(key), {
                        'key': key, 'ask': {k: a.get(k) for k in ('subject', 'product', 'family', 'window', 'scope', 'platform')},
                        'changed_windows': list(changed_windows or []),
                        'anchors': out['anchors'], 'events': out['events'],
                        'sources': [s for s in (raw.get('sources') or []) if isinstance(s, dict)][:20],   # internal only
                        'saved_ts': time.time(), 'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    })
                except Exception as e:
                    print(f"[pm-knowledge] anchor save skipped: {type(e).__name__}: {e}")
        for x in out['anchors']:
            out['marks'].append({'kind': 'research', 'ref': x.get('name'), 'as_of': x.get('as_of'), 'source_type': x.get('source_type')})
        for ev in out['events']:
            out['marks'].append({'kind': 'research', 'ref': f"event:{ev.get('kind')}:{ev.get('date')}"})
        out['block'] = anchors_block(out['anchors'], out['events'])
    except Exception as e:
        print(f"[pm-knowledge] research step skipped: {type(e).__name__}: {e}")
    return out


def _fmt_num(v) -> str:
    f = _num(v)
    if f is None:
        return str(v)
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    if float(f).is_integer():
        return str(int(f))
    return f"{f:.4g}"


def anchors_block(anchors: list, events: Optional[list] = None) -> str:
    """The prompt block for researched anchors and dated events. Prompt
    text only, never shown to a reader; the model is told so."""
    lines = []
    for x in (anchors or [])[:30]:
        w = x.get('window') or {}
        win = f" ({w.get('start')} to {w.get('end')})" if w.get('start') and w.get('end') else ''
        lines.append(f"- {x.get('name')}: {_fmt_num(x.get('value'))} {x.get('unit') or ''}{win}".rstrip())
    for ev in (events or [])[:12]:
        lines.append(f"- EVENT {ev.get('date')}: {ev.get('kind')}, {ev.get('name') or ''}".rstrip(', '))
    if not lines:
        return ''
    return ("RESEARCHED ANCHORS (binding, internal). Every figure you state sits inside what these support; "
            "a count never exceeds the anchor for its measure. Never name a source, a tracker or a publication "
            "to the reader; state the figures as Crosswalk's own.\n" + '\n'.join(lines))


# ------------------------------------------------------------ the block
def facts_block(ask: dict, held_r: Optional[dict] = None, stored_r: Optional[dict] = None,
                research_r: Optional[dict] = None, extra: Optional[list] = None) -> str:
    """The one prompt block: held, stored, the near twin, the anchors,
    in that order, plus any extra blocks the path still carries."""
    parts = []
    if isinstance(held_r, dict) and held_r.get('block'):
        parts.append(held_r['block'])
    if isinstance(stored_r, dict):
        if stored_r.get('block'):
            parts.append(stored_r['block'])
        if stored_r.get('twin'):
            tb = _twin.twin_block(stored_r['twin'], (ask or {}).get('window'))
            if tb:
                parts.append(tb)
    if isinstance(research_r, dict) and research_r.get('block'):
        parts.append(research_r['block'])
    for x in (extra or []):
        if x:
            parts.append(str(x))
    return '\n\n'.join(p for p in parts if p)


def marks_of(*results) -> list:
    out = []
    for r in results:
        if isinstance(r, dict):
            out.extend(r.get('marks') or [])
    return out[:200]


# ----------------------------------------------------- the pending twin
_PENDING: dict = {}
_PENDING_LOCK = threading.Lock()
_PENDING_TTL_S = 1800


def _pending_key(subject, question) -> str:
    return hashlib.sha1(f"{_fold(subject)}|{_fold(question)[:200]}".encode()).hexdigest()[:16]


def leave_twin(subject, question, twin: Optional[dict], ask_window: Optional[dict] = None, events: Optional[list] = None):
    """Leave the near twin for the hold that runs after the model."""
    if not isinstance(twin, dict):
        return
    with _PENDING_LOCK:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if now - v.get('ts', 0) > _PENDING_TTL_S]:
            _PENDING.pop(k, None)
        _PENDING[_pending_key(subject, question)] = {'twin': twin, 'window': ask_window or {}, 'events': list(events or []), 'ts': now}


def take_twin(subject, question) -> Optional[dict]:
    with _PENDING_LOCK:
        _PENDING.pop('val|' + _pending_key(subject, question), None)
        _PENDING.pop('size|' + _pending_key(subject, question), None)
        return _PENDING.pop(_pending_key(subject, question), None)


def peek_twin(subject, question) -> Optional[dict]:
    with _PENDING_LOCK:
        return _PENDING.get(_pending_key(subject, question))


def leave_valuation(subject, question, bound: Optional[dict], others: Optional[list] = None):
    """Leave the stored valuation a read is about for the hold that runs
    after the model (and again after the coherence pass)."""
    if not isinstance(bound, dict):
        return
    with _PENDING_LOCK:
        _PENDING['val|' + _pending_key(subject, question)] = {'bound': bound, 'others': list(others or []), 'ts': time.time()}


def peek_valuation(subject, question) -> Optional[dict]:
    with _PENDING_LOCK:
        return _PENDING.get('val|' + _pending_key(subject, question))


def leave_size(subject, question, cap, *, window: Optional[dict] = None, source: str = ''):
    """Leave the subject's audience size (and the window a researched
    read defaults to) for the size gate that runs after the model."""
    if not (cap or window):
        return
    with _PENDING_LOCK:
        now = time.time()
        for k in [k for k, v in _PENDING.items() if now - v.get('ts', 0) > _PENDING_TTL_S]:
            _PENDING.pop(k, None)
        _PENDING['size|' + _pending_key(subject, question)] = {'cap': cap, 'window': window or {}, 'source': source, 'ts': now}


def peek_size(subject, question) -> Optional[dict]:
    with _PENDING_LOCK:
        return _PENDING.get('size|' + _pending_key(subject, question))


def bind_after(res, *, question='', subject=''):
    """After the coherence pass: the stored valuation's figures are put
    back on the read's metrics (the pass may have redrawn a digit or a
    thread figure may have been kept), the window and the bound names
    ride the result for the stored-figure binding that follows. Returns
    `res` as given when nothing is pending. Never raises."""
    try:
        if not enabled() or not isinstance(res, dict):
            return res
        pend = peek_valuation(subject, question)
        if pend:
            res, rec = _twin.hold_read_to_valuation(res, pend.get('bound') or {}, pend.get('others') or [])
        sp = peek_size(subject, question)
        if sp:
            res, _srec = _twin.size_gate(res, sp.get('cap'), salt=f"size|{_fold(subject)}", window=sp.get('window') or None)
        return res
    except Exception as e:
        print(f"[pm-knowledge] valuation re-bind skipped: {type(e).__name__}: {e}")
        return res


def hold_pending(data, *, question='', subject='', store=None):
    """The near-twin hold on a generated read's metrics, run inside
    read_repair.coherent before the coherence pass: the twin left by
    `read_blocks` for this subject and question, the band from
    prometheus.consistency, the record written. The twin stays pending
    until `take_twin` clears it (read_repair clears it once the
    coherence pass succeeds, so a second pass of the same read is held
    the same way). Returns `data` as given when nothing is pending.
    Never raises."""
    try:
        if not enabled() or not isinstance(data, dict):
            return data
        vp = peek_valuation(subject, question)
        if vp:
            data, vrec = _twin.hold_read_to_valuation(data, vp.get('bound') or {}, vp.get('others') or [])
            if vrec and (vrec.get('set') or vrec.get('dropped') or vrec.get('sentences_dropped')):
                print(f"[pm-knowledge] read bound to the valuation on file {vrec.get('ref')}: "
                      f"{len(vrec.get('set') or [])} figures set, {len(vrec.get('dropped') or [])} dropped, "
                      f"{vrec.get('sentences_dropped')} sentences on other runs dropped")
                try:
                    _twin.write_record(dict(vrec, product='read', subject=str(subject or ''), pair_key=_pieces.property_fold(subject),
                                            question=str(question or '')[:300], line='', line_shipped=False), store)
                except Exception:
                    pass
        sp = peek_size(subject, question)
        if sp:
            data, srec = _twin.size_gate(data, sp.get('cap'), salt=f"size|{_fold(subject)}", window=sp.get('window') or None)
            if srec and (srec.get('moves') or srec.get('window_set')):
                print(f"[pm-knowledge] size gate on {subject}: cap {srec.get('cap')}, {len(srec.get('moves') or [])} count(s) reduced, "
                      f"window set {srec.get('window_set')}")
                try:
                    _twin.write_record(dict(srec, verdict='size_gate', product='read', subject=str(subject or ''),
                                            pair_key=_pieces.property_fold(subject), question=str(question or '')[:300],
                                            source=sp.get('source'), line='', line_shipped=False), store)
                except Exception:
                    pass
        pend = peek_twin(subject, question)
        if not pend:
            return data
        twin = pend.get('twin')
        changed = _twin.changed_windows((twin or {}).get('window') or {}, pend.get('window') or {})
        event = _twin.event_in_changed_window(changed, pend.get('events') or []) if changed else None
        salt = f"twin-read|{_fold(subject)}|{(pend.get('window') or {}).get('start')}|{(pend.get('window') or {}).get('end')}"
        data, rec = _twin.hold_read_metrics(data, twin, salt=salt, event=event)
        if rec and rec.get('verdict') in ('held', 'lifted'):
            rec = dict(rec, product='read', subject=str(subject or ''), window=pend.get('window') or {},
                       changed_windows=changed, pair_key=_pieces.property_fold(subject),
                       line=(_twin.lifted_line(event, 'up') if event else _twin.held_line()),
                       line_shipped=False, bands=_twin.bands())
            _twin.write_record(rec, store)
        return data
    except Exception as e:
        print(f"[pm-knowledge] pending hold skipped: {type(e).__name__}: {e}")
        return data


# ------------------------------------------- the window and the file asked
def inline_window(text) -> dict:
    """The window the words state ('8/27/26-9/8/26', 'Aug 27 to Sep 8,
    2026'), as {start, end}; empty when none."""
    try:
        from . import inline_dates as _idates
        w = _idates.parse_inline_window(str(text or ''))
        if isinstance(w, dict) and w.get('start') and w.get('end'):
            return {'start': str(w['start'])[:10], 'end': str(w['end'])[:10]}
    except Exception:
        pass
    return {'start': '', 'end': ''}


def _pairing_of(text, ask, base, mr) -> Optional[tuple]:
    """(talent, brand) the read is about: the words first, then the
    subject the ask, the base or the request carries when it is a
    pairing ('Anne Hathaway x Shiseido')."""
    try:
        from migration import bpiq_anchors as _ba
        pr = _ba.pairing_in(text)
        if pr:
            return pr
        for cand in ((ask or {}).get('subject'), (base or {}).get('subject'), (mr or {}).get('subject')):
            pr = _ba.pair_of(cand) if cand else None
            if pr:
                return pr
    except Exception:
        pass
    return None


def valuation_for_read(text, ask, base=None, mr=None) -> tuple:
    """(bound, others): the stored valuation a read is about and every
    other valuation file on the same pairing. (None, []) when the words
    name no pairing with a valuation on file, or when the window asked
    touches none of them."""
    try:
        pr = _pairing_of(text, ask, base, mr)
        if not pr:
            return None, []
        cands = _twin.valuation_candidates(pr[0], pr[1])
        if not cands:
            return None, []
        bound = _twin.which_valuation(cands, (ask or {}).get('window'), text)
        return bound, cands
    except Exception as e:
        print(f"[pm-knowledge] valuation lookup skipped: {type(e).__name__}: {e}")
        return None, []


def world_block(text, subject='') -> str:
    """The guidance for a question about the world (awareness, buzz,
    interest, momentum for a subject; Jenna 2026-11-20, 'is there any
    awareness for the film Josephine'): answer the question asked, from
    the research, in the signals Crosswalk reads. Prompt text only."""
    try:
        from . import catalog_lane as _cat
        if not _cat.world_question(text):
            return ''
    except Exception:
        return ''
    who = str(subject or '').strip() or 'the subject'
    return (f"THE QUESTION IS ABOUT THE WORLD, NOT THE SHELF. The reader asks whether there is attention around {who} "
            f"and how much. Answer that first, plainly: yes or no, how much, where it sits (who is searching, talking, "
            f"watching the trailer or clips, reading the coverage) and where it is thin, from the researched anchors and "
            f"the dated events (a release date, a trailer, a festival premiere, named with its date). Speak in attention, "
            f"interest, conversation, searches and views; the words awareness, recall and intent never appear in the reply. "
            f"Never say the subject is not on the dashboard, never offer to build a profile, never answer about what is held.")


# ------------------------------------------------------- the three paths
def read_blocks(text, mr, base, *, pk=None, saved=None, catalog=None, stage=None, stages=None,
                claude_json=None, tools=None, history=None, body=None, led=None) -> str:
    """The generated read's one block: what the path assembled before
    (Peacock, the saved valuation, the catalog) through `held`, the
    near twin through `stored` (`led` is the ledger consult the path
    already made; without it the ledger is consulted once here), the
    anchors through `research`. The twin is left for `hold_pending`.
    With the layer off, exactly the block the path built before. Never
    raises."""
    def _legacy():
        parts = []
        for fn, args in ((pk, (text, mr, base)), (saved, (text,)), (catalog, ((base or {}).get('subject'),))):
            if callable(fn):
                try:
                    blk = fn(*args)
                except Exception:
                    blk = ''
                if blk:
                    parts.append(str(blk))
        return '\n\n'.join(parts)

    if not enabled():
        return _legacy()
    t0 = time.monotonic()
    try:
        base = base if isinstance(base, dict) else {}
        ask = ask_from(body, text, base, mr)
        if not (ask.get('window') or {}).get('start'):
            ask['window'] = inline_window(text)
        # the subject the words name outranks the open view (Jenna 2026-11-20,
        # Emma Crouchen's VICE Sports read on the open Pinterest profile): a
        # subject we hold no profile on is researched, never read off another
        # profile's base, and its audience size is the ceiling on every count
        named = named_subject(text, ask, base)
        base_subject = str(base.get('subject') or '').strip()
        borrowed = base_is_borrowed(text, named, base)
        researched = str(base.get('source') or '') == 'panel' or borrowed
        default_win = None
        if borrowed:
            ask['subject'] = named
            # the ledger for the named subject rides in the core's own
            # consult object (its block, its entries for the binding after
            # the model): a decision of record on the named subject replays
            # through it, and the base's stored figures never bind this read
            try:
                import insights_ledger as _il
                _led2 = _il.consult(subject=named, question=text, metric_family=ask.get('family') or None, history=history)
                if isinstance(led, dict):
                    led.clear()
                    led.update(_led2 if isinstance(_led2, dict) else {})
                    led.setdefault('entries', [])
                    led.setdefault('block', '')
                    led.setdefault('exact', None)
            except Exception as e:
                print(f"[pm-knowledge] named-subject ledger consult skipped: {type(e).__name__}: {e}")
        if researched and not (ask.get('window') or {}).get('start'):
            # a researched read defaults to the trailing twelve months, never
            # a calendar year taken from another file (Jenna 2026-11-20)
            try:
                from . import window_scope as _ws
                s0, e0 = _ws.standing_default()
                default_win = {'start': s0, 'end': e0}
                ask['window'] = dict(default_win, label='Trailing 12 months', default=True)
            except Exception:
                default_win = None
        parts = []
        if callable(pk):
            try:
                blk = pk(text, mr, base)
            except Exception:
                blk = ''
            if blk:
                parts.append(str(blk))
        # a read about a valuation on file reads that file, for exactly
        # the window asked, and never mixes runs (Jenna 2026-11-20)
        bound, others = valuation_for_read(text, ask, base, mr)
        if bound:
            vb = _twin.valuation_block(bound, others)
            if vb:
                parts.append(vb)
            leave_valuation(ask.get('subject') or base.get('subject'), text, bound, others)
        elif callable(saved):
            try:
                blk = saved(text, window=ask.get('window')) if (ask.get('window') or {}).get('start') else saved(text)
            except TypeError:
                blk = saved(text)
            except Exception:
                blk = ''
            if blk:
                parts.append(str(blk))
        h = held(ask.get('subject'), window=(ask.get('window') if (ask.get('window') or {}).get('start') else None),
                 bpiq_source_key=(bound or {}).get('ref'))
        if h.get('block'):
            parts.append(h['block'])
        elif callable(catalog) and not borrowed:
            try:
                blk = catalog(base.get('subject'))
            except Exception:
                blk = ''
            if blk:
                parts.append(str(blk))
        st = {'twin': None, 'marks': []}
        if (ask.get('window') or {}).get('start') and ask.get('subject'):
            try:
                _led = led if isinstance(led, dict) and led.get('entries') is not None else None
                if _led is None:
                    import insights_ledger as _il
                    _led = _il.consult(subject=ask.get('subject'), question=text, history=history)
                st['twin'] = _twin.near_twin_entry({'subject': _led.get('subject') or ask.get('subject'),
                                                    'family': ask.get('family'), 'window': ask.get('window')},
                                                   list(_led.get('entries') or []))
            except Exception as e:
                print(f"[pm-knowledge] twin lookup skipped: {type(e).__name__}: {e}")
        if st['twin']:
            tb = _twin.twin_block(st['twin'], ask.get('window'))
            if tb:
                parts.append(tb)
        if callable(stage):
            try:
                stage('researching')
            except Exception:
                pass
        changed = _twin.changed_windows((st['twin'] or {}).get('window') or {}, ask.get('window') or {}) if st['twin'] else None
        if researched and not h.get('profile'):
            ask['size_needed'] = True
        r = research(ask, claude_json, tools, changed_windows=changed or None)
        if r.get('block'):
            parts.append(r['block'])
        cap, cap_source = None, ''
        if researched:
            cap = _twin.anchor_low_people(r.get('anchors') or [])
            cap_source = 'research' if cap else ''
        elif isinstance(h.get('profile'), dict) and _twin.same_name(h['profile'].get('subject') or ask.get('subject'), ask.get('subject') or ''):
            cap = _num(h['profile'].get('audience_size'))
            cap_source = 'profile' if cap else ''
        sb = _twin.size_block(ask.get('subject'), cap, researched=researched,
                              borrowed_from=(base_subject if borrowed else ''), window=default_win)
        if sb:
            parts.append(sb)
        if cap or default_win:
            leave_size(ask.get('subject') or base_subject, text, cap, window=default_win, source=cap_source)
            if borrowed:
                leave_size(base_subject, text, cap, window=default_win, source=cap_source)   # the core may still key the read on the base
        if st['twin']:
            leave_twin(ask.get('subject'), text, st['twin'], ask.get('window'), r.get('events'))
        wb = world_block(text, ask.get('subject'))
        if wb:
            parts.append(wb)
        base['_knowledge'] = {'key': ask_key(ask), 'twin': (st['twin'] or {}).get('ref'), 'valuation': (bound or {}).get('ref'),
                              'anchors': len(r.get('anchors') or []), 'events': len(r.get('events') or []),
                              'cached': bool(r.get('cached')), 'marks': marks_of(h, r)[:60],
                              'named_subject': named, 'borrowed_base': base_subject if borrowed else '', 'size_cap': cap, 'size_source': cap_source}
        if isinstance(stages, dict):
            stages['knowledge'] = int((time.monotonic() - t0) * 1000)
        return '\n\n'.join(p for p in parts if p)
    except Exception as e:
        print(f"[pm-knowledge] read blocks fell back: {type(e).__name__}: {e}")
        return _legacy()


def insight_blocks(text, subject, *, history=None, consult=None, claude_json=None, tools=None,
                   window=None, body=None) -> tuple:
    """(ledger_block, anchors_block) for the insight lane: the stored
    answers through `stored`, the anchors through `research` when held
    and stored are thin. `consult` is the lane's own lookup, used when
    the layer is off. Never raises."""
    def _legacy():
        if callable(consult):
            try:
                return str(consult() or ''), ''
            except Exception:
                return '', ''
        return '', ''

    if not enabled():
        return _legacy()
    try:
        ask = ask_from(body, text, {'subject': subject, 'window': window}, None, product='insight')
        st = stored(ask, question=text, history=history)
        led_block = st.get('block') or ''
        thin = len(st.get('entries') or []) < 2
        if thin:
            h = held(ask.get('subject'))
            if not h.get('profile'):
                ask['size_needed'] = True      # a subject we hold no profile on: its audience size is the first anchor (2026-11-20)
            r = research(ask, claude_json, tools)
            cap = _twin.anchor_low_people(r.get('anchors') or []) if not h.get('profile') else None
            sb = _twin.size_block(ask.get('subject'), cap, researched=bool(cap), window=None) if cap else ''
            anchors = '\n\n'.join(x for x in ((r.get('block') or ''), sb) if x)
        else:
            anchors = ''
        return led_block, anchors
    except Exception as e:
        print(f"[pm-knowledge] insight blocks fell back: {type(e).__name__}: {e}")
        return _legacy()


def valuation_events(inputs: dict, changed_windows: list, claude_json=None, tools=None, store=None) -> list:
    """The research override for a valuation's near-twin check: dated
    events inside the parts of the window that changed, from the anchor
    store or one scoped research call. [] when none or off."""
    try:
        if not enabled() or not changed_windows:
            return []
        ask = ask_from(None, f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}",
                       {'subject': f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}"}, None,
                       product='valuation', talent=str(inputs.get('qualifier') or ''), brand=str(inputs.get('brand_partner') or ''),
                       window={'start': inputs.get('event_start'), 'end': inputs.get('event_end')})
        r = research(ask, claude_json, tools, changed_windows=changed_windows, store=store)
        return list(r.get('events') or [])
    except Exception as e:
        print(f"[pm-knowledge] valuation events skipped: {type(e).__name__}: {e}")
        return []


__all__ = ['enabled', 'research_enabled', 'ask_from', 'ask_key', 'held', 'stored', 'research', 'anchors_block',
           'facts_block', 'marks_of', 'leave_twin', 'take_twin', 'peek_twin', 'hold_pending', 'read_blocks', 'insight_blocks',
           'leave_valuation', 'peek_valuation', 'bind_after', 'inline_window', 'valuation_for_read', 'world_block',
           'understanding_on_request', 'named_subject', 'base_is_borrowed', 'leave_size', 'peek_size',
           'valuation_events', 'RESEARCH_PROMPT', 'ANCHOR_PREFIX', 'ENV', 'ENV_RESEARCH', 'PRODUCTS']
