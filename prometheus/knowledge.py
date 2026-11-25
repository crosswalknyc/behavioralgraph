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
ANCHOR_FRESH_S = 14 * 24 * 3600          # anchors on an open window are researched again after two weeks (the default; PM_KNOWLEDGE_ANCHOR_TTL_DAYS)
ANCHOR_INDEX_PREFIX = 'system/pm_anchors/_by_subject/'   # one small index per subject: which anchor documents it has and their windows
ENV_ANCHOR_TTL = 'PM_KNOWLEDGE_ANCHOR_TTL_DAYS'
ENV_RESEARCH_GATE = 'PM_KNOWLEDGE_RESEARCH_GATE'
ENV_RESEARCH_BUDGET = 'PM_KNOWLEDGE_RESEARCH_BUDGET_S'   # the research call's time budget inside a read (0: no budget)
ENV_RESEARCH_MAX_USES = 'PM_KNOWLEDGE_RESEARCH_MAX_USES'  # web searches the research call may run
RESEARCH_BUDGET_S = 90.0
RESEARCH_MAX_USES = 4
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


_AUDIENCE_NOUNS = frozenset(('audience', 'audiences', 'viewer', 'viewers', 'fan', 'fans', 'subscriber', 'subscribers', 'buyer', 'buyers',
                             'shopper', 'shoppers', 'listener', 'listeners', 'watcher', 'watchers', 'follower', 'followers', 'customer',
                             'customers', 'consumer', 'consumers', 'user', 'users', 'player', 'players', 'reader', 'readers', 'member',
                             'members', 'owner', 'owners', 'the', 'a', 'an', 'of', 'and', 'on', 'in', 'for', 'to', 'with', 'avid', 'casual',
                             'us', 'people'))


# The words a cohort label adds to its subject's name ("people that searched
# Paul Rodriguez in the US", "Nike buyers who also shop Target", "the avid
# tier of Yellowstone viewers"): behavior, who-words and audience nouns.
# Stripped, a cohort of the base's own subject leaves only that subject's
# name, spelled as the reader spelled it.
_COHORT_WORDS = frozenset(('that', 'who', 'whom', 'which', 'those', 'these', 'this', 'their', 'his', 'her', 'its',
                           'searched', 'search', 'searches', 'searching', 'googled', 'looked', 'up', 'watched', 'watch', 'watching',
                           'viewed', 'view', 'streamed', 'stream', 'streaming', 'played', 'play', 'playing', 'listened', 'listen',
                           'read', 'bought', 'buy', 'purchased', 'purchase', 'shopped', 'shop', 'followed', 'follow', 'following',
                           'engaged', 'engage', 'visited', 'visit', 'clicked', 'liked', 'subscribed', 'subscribe', 'attended', 'went',
                           'name', 'names', 'tier', 'cut', 'cohort', 'group', 'segment', 'slice', 'set', 'pool', 'base', 'universe',
                           'audience', 'audiences', 'only', 'just', 'also', 'still', 'already', 'ever', 'once', 'again', 'at', 'least',
                           'one', 'any', 'all', 'every', 'each', 'most', 'heavy', 'light', 'new', 'returning', 'lapsed', 'active',
                           'window', 'year', 'month', 'months', 'days', 'last', 'past', 'trailing', 'within', 'during', 'between',
                           'via', 'through', 'across', 'from', 'into', 'over', 'under', 'about', 'than', 'more', 'less', 'times',
                           'online', 'digital', 'web', 'app', 'site',
                           'searchers', 'searcher', 'engagers', 'engager', 'visitors', 'visitor', 'streamers', 'streamer',
                           'bettors', 'bettor', 'attendees', 'attendee', 'voters', 'voter', 'gamers', 'gamer', 'watchers', 'watcher'))


def _token_near(a: str, b: str) -> bool:
    """Two name tokens are the same word allowing one slip of spelling
    (Rodriquez / Rodriguez) on a word of five letters or more."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    s_, l_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(l_)):
        if l_[:i] + l_[i + 1:] == s_:
            return True
    return False


def cohort_of_base(named: str, base_subject: str) -> bool:
    """True when the named subject is a behavioral cohort of the base's
    own subject: once the cohort words and audience nouns come off, every
    word left is a word of the base subject's name (one spelling slip
    allowed). 'people that Searched Paul Rodriquez in the US' is a cohort
    of 'Paul Rodriguez'; 'Nike fans' on the Paul Rodriguez profile is not
    (2026-11-24, Armine: that cohort was sized by research at 102,000 US
    people and every count in the read was cut to it, under a table whose
    rows still read off the 1,742,869 the thread had stated)."""
    n = str(named or '').strip()
    bs = str(base_subject or '').strip().split(' - ')[0]
    if not n or not bs:
        return False
    # a service named as where ('on YouTube', 'via Prime Video') scopes the cohort; it is not a second subject
    try:
        from . import platform_scope as _ps
        for pname in (_ps.named(n) or {}):
            n = re.sub(r'(?i)(?<![a-z0-9])' + re.escape(str(pname)) + r'(?![a-z0-9])', ' ', n)
    except Exception:
        pass
    alltoks = [w for w in re.split(r'[^a-z0-9]+', n.lower()) if w]
    ntoks = [w for w in alltoks if w not in _AUDIENCE_NOUNS and w not in _COHORT_WORDS]
    btoks = [w for w in re.split(r'[^a-z0-9]+', bs.lower()) if w and w not in _AUDIENCE_NOUNS]
    if not ntoks or not btoks:
        return False
    if len(ntoks) == len(alltoks):
        return False            # only the name: the subject itself, not a cohort of it
    if not all(any(_token_near(w, b) for b in btoks) for w in ntoks):
        return False
    # at least one distinctive word of the base's name is in the label (never a bare 'the US people')
    return any(len(w) >= 3 and any(_token_near(w, b) for b in btoks) for w in ntoks)


def base_is_borrowed(text, named: str, base: Optional[dict]) -> bool:
    """True when the read's base is another subject's profile: the words
    name a subject (`named`), the base is a page, catalog or view base
    for a different name, and the words do not name the base's own
    subject (a distinctive token of it). A resume's pin counts as a
    page base; a base the reader chose by its words
    (`chosen_by_words`) never counts. A behavioral cohort of the base's
    own subject (cohort_of_base) is the base's, never borrowed."""
    b = base if isinstance(base, dict) else {}
    bs = str(b.get('subject') or '').strip()
    if not (named and bs) or b.get('chosen_by_words'):
        return False
    if str(b.get('source') or '') not in ('page', 'catalog', 'view', ''):
        return False
    if _twin.same_name(named, bs) or _twin.same_name(named, bs.split(' - ')[0]):
        return False
    if cohort_of_base(named, bs):
        return False
    # the named subject inside the base's own name ('Anne Hathaway' on the
    # Anne Hathaway x Shiseido valuation, 'Yellowstone' on its avid cut,
    # 'Nike fans' with Nike open): the base is the subject's; an audience
    # noun names the kind of audience, never another subject
    ntoks = [w for w in re.split(r'[^a-z0-9]+', str(named).lower()) if w and w not in _AUDIENCE_NOUNS]
    btoks = set(re.split(r'[^a-z0-9]+', bs.lower()))
    if not ntoks or all(w in btoks for w in ntoks):
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
_CATALOG_MEMO: dict = {}
_CATALOG_INFLIGHT: dict = {}
_CATALOG_MEMO_TTL_S = 60.0


def catalog_anchors(name, window=None, products=None, with_ledger=False):
    """corpus_catalog.anchors_for, memoized for a minute per (subject,
    window, products): held() and subject_facts() read the same subject
    in one pass, so the catalog is scanned once (2026-11-23)."""
    from migration import corpus_catalog as _cc
    w = window if isinstance(window, dict) else {}
    key = (_fold(name), str(w.get('start') or ''), str(w.get('end') or ''), tuple(products or ()), bool(with_ledger))
    now = time.time()
    with _PENDING_LOCK:
        hit = _CATALOG_MEMO.get(key)
        if hit and now - hit[0] < _CATALOG_MEMO_TTL_S:
            return json.loads(json.dumps(hit[1]))
        flight = _CATALOG_INFLIGHT.get(key)
        if flight is None:
            flight = threading.Event()
            _CATALOG_INFLIGHT[key] = flight
            mine = True
        else:
            mine = False
    if not mine:
        # another lookup on the same subject is scanning right now (the held
        # and the shared-figures lookups run side by side): wait for its answer
        flight.wait(timeout=20.0)
        with _PENDING_LOCK:
            hit = _CATALOG_MEMO.get(key)
        if hit:
            return json.loads(json.dumps(hit[1]))
    try:
        out = _cc.anchors_for(name, window=window, products=products, with_ledger=with_ledger)
        try:
            with _PENDING_LOCK:
                if len(_CATALOG_MEMO) > 200:
                    for k in [k for k, v in _CATALOG_MEMO.items() if now - v[0] > _CATALOG_MEMO_TTL_S]:
                        _CATALOG_MEMO.pop(k, None)
                _CATALOG_MEMO[key] = (now, json.loads(json.dumps(out, default=str)))
        except Exception:
            pass
    finally:
        with _PENDING_LOCK:
            _CATALOG_INFLIGHT.pop(key, None)
        flight.set()
    return out


def filter_held_to_valuation(h: dict, bpiq_source_key) -> dict:
    """held() for a read about one stored valuation: the other valuation
    files on the subject leave the facts and the block."""
    if not bpiq_source_key or not isinstance(h, dict):
        return h
    try:
        from migration import corpus_catalog as _cc
        want = str(bpiq_source_key).split('/')[-1]
        facts = [f for f in (h.get('facts') or []) if not (isinstance(f, dict) and f.get('product') == 'bpiq'
                                                          and str(((f.get('source') or {}).get('key')) or f.get('source_key') or '').split('/')[-1] != want)]
        if len(facts) == len(h.get('facts') or []):
            return h
        out = dict(h, facts=facts)
        out['block'] = _cc.anchors_block({'facts': facts}) if facts else ''
        return out
    except Exception:
        return h


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
            anchors = catalog_anchors(n, window=window, products=products, with_ledger=False)
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


def anchor_ttl_s() -> float:
    """How long anchors on an open window are read back before a new
    research call: 30 days unless PM_KNOWLEDGE_ANCHOR_TTL_DAYS says
    otherwise (2026-11-23: a trailing-year ask moves its window a day at
    a time, and the anchors do not change with it)."""
    try:
        days = float(os.environ.get(ENV_ANCHOR_TTL, '30') or 30)
    except (TypeError, ValueError):
        days = 30.0
    return max(1.0, days) * 86400.0


def _fresh(doc: dict, ask_window: dict) -> bool:
    """A saved research document is read back when its window is closed
    (the facts of a finished window do not change) or when it is under
    the anchor TTL (30 days by default)."""
    try:
        ts = float(doc.get('saved_ts') or 0)
    except (TypeError, ValueError):
        ts = 0.0
    end = _date((ask_window or {}).get('end'))
    today = _dt.date.today()
    if end and end < today - _dt.timedelta(days=3):
        return True
    return bool(ts) and (time.time() - ts) < anchor_ttl_s()


def _subject_index_key(subject) -> str:
    return f"{ANCHOR_INDEX_PREFIX}{hashlib.sha1(_fold(subject).encode()).hexdigest()[:16]}.json"


def _index_anchor_doc(store, ask: dict, key: str, saved_ts: float) -> None:
    """List a saved anchor document on its subject's index (ETag-guarded
    when the store supports it), so a later ask on a nearby window finds
    it without a research call."""
    try:
        w = _window_of(ask.get('window'))
        row = {'key': key, 'product': ask.get('product') or 'read', 'family': ask.get('family') or 'audience',
               'scope': _fold(ask.get('scope'))[:40], 'window': {'start': w['start'] or '', 'end': w['end'] or ''}, 'saved_ts': saved_ts}
        ik = _subject_index_key(ask.get('subject'))
        doc = store.load(ik) if hasattr(store, 'load') else None
        doc = doc if isinstance(doc, dict) else {'subject': str(ask.get('subject') or ''), 'docs': []}
        rows = [r for r in (doc.get('docs') or []) if isinstance(r, dict) and r.get('key') != key]
        rows.append(row)
        doc['docs'] = rows[-60:]
        store.save(ik, doc)
    except Exception as e:
        print(f"[pm-knowledge] anchor index skipped: {type(e).__name__}: {e}")


def _near_saved_anchors(store, ask: dict):
    """A saved research document on the same subject, product, family and
    scope whose window is near the ask's (the near-twin window test) and
    still fresh; None when none. The index is one small document per
    subject."""
    try:
        doc = store.load(_subject_index_key(ask.get('subject'))) if hasattr(store, 'load') else None
        rows = [r for r in ((doc or {}).get('docs') or []) if isinstance(r, dict)]
        if not rows:
            return None
        w = _window_of(ask.get('window'))
        want = {'start': w['start'] or '', 'end': w['end'] or ''}
        best = None
        for r in rows:
            if (r.get('product') or 'read') != (ask.get('product') or 'read') or (r.get('family') or 'audience') != (ask.get('family') or 'audience'):
                continue
            if (r.get('scope') or '') != _fold(ask.get('scope'))[:40]:
                continue
            rw = r.get('window') or {}
            if want['start'] and rw.get('start'):
                ok, _why = _twin.windows_near(rw, want)
                if not ok:
                    continue
            elif want['start'] != (rw.get('start') or ''):
                continue
            if best is None or float(r.get('saved_ts') or 0) > float(best.get('saved_ts') or 0):
                best = r
        if not best:
            return None
        saved = store.load(_anchor_doc_key(best['key']))
        if isinstance(saved, dict) and (saved.get('anchors') or saved.get('events') is not None) and _fresh(saved, ask.get('window') or {}):
            return saved
    except Exception as e:
        print(f"[pm-knowledge] near anchors skipped: {type(e).__name__}: {e}")
    return None


def research(ask: dict, claude_json: Optional[Callable] = None, tools=None, changed_windows=None,
             store=None, force: bool = False, call: bool = True) -> dict:
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
            if not (isinstance(saved, dict) and (saved.get('anchors') or saved.get('events') is not None)) and not changed_windows:
                # anchors researched for a nearby window (the trailing year a
                # day or a week on) are the same anchors: read them back
                # (2026-11-23, faster answers)
                near = _near_saved_anchors(store, a)
                if near:
                    saved = near
                    out['near_window'] = True
        if isinstance(saved, dict) and (saved.get('anchors') or saved.get('events') is not None) and _fresh(saved, a.get('window') or {}):
            out['anchors'] = _clean_anchors(saved.get('anchors'))
            out['events'] = _clean_events(saved.get('events'))
            out['cached'] = True
        elif not call:
            out['skipped'] = True           # the held and recorded figures already answer; no call (2026-11-23)
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
                kw['tools'] = _research_tools(tools)    # a handful of anchors needs a few searches, not six (2026-11-23)
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
                    if not changed_windows:
                        _index_anchor_doc(store, a, key, time.time())
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


def saved_people(subject, window=None) -> Optional[int]:
    """US people in the audience size saved for a subject
    (migration/saved_subject_size: a decision of record, the estimates
    table, the saved-size table, a record anchor) when its window fits the
    read's; None otherwise. Never raises."""
    try:
        from migration import saved_subject_size as _ss
        got = _ss.lookup(subject)
        if not got:
            return None
        bw = _ss.parse_window(window) if isinstance(window, dict) and window.get('start') else None
        if not _ss.window_ok(bw, got.get('window')):
            return None
        return int(got['us'])
    except Exception:
        return None


def _refresh_events(subject) -> list:
    """Dated events a refresh of the subject researched for its new window
    (migration/refresh_sway), [{date, kind, name}]; [] when none or
    unavailable. Read by the near-twin holds as their research override.
    Never raises."""
    try:
        from migration import refresh_sway as _rsw
        return _rsw.recorded_events(subject) if subject else []
    except Exception:
        return []


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
        event = _twin.event_in_changed_window(changed, list(pend.get('events') or []) + _refresh_events(subject)) if changed else None
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
def _side_by_side(jobs: dict, timeout_s: float = 25.0) -> dict:
    """Run independent lookups on threads and collect what finished; a
    job that raises or outruns the timeout contributes nothing (the read
    goes on without it). One job runs inline; the switch
    PM_KNOWLEDGE_PARALLEL=0 runs them in turn."""
    out = {}
    names = list(jobs.keys())
    if len(names) <= 1 or os.environ.get('PM_KNOWLEDGE_PARALLEL', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        for n in names:
            try:
                out[n] = jobs[n]()
            except Exception as e:
                print(f"[pm-knowledge] {n} lookup skipped: {type(e).__name__}: {e}")
        return out
    from concurrent.futures import ThreadPoolExecutor, wait
    import contextvars
    ex = ThreadPoolExecutor(max_workers=min(4, len(names)), thread_name_prefix='pm-knowledge')
    futs = {}
    try:
        for n in names:
            ctx = contextvars.copy_context()
            futs[ex.submit(ctx.run, jobs[n])] = n
        done, pending = wait(list(futs.keys()), timeout=timeout_s)
        for f in done:
            n = futs[f]
            try:
                out[n] = f.result()
            except Exception as e:
                print(f"[pm-knowledge] {n} lookup skipped: {type(e).__name__}: {e}")
        for f in pending:
            print(f"[pm-knowledge] {futs[f]} lookup outran {timeout_s:.0f}s; the read goes on without it")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return out


def research_gate_enabled() -> bool:
    return os.environ.get(ENV_RESEARCH_GATE, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def research_budget_s() -> float:
    """How long a read waits for the research call (2026-11-23: the
    knowledge step ran 82 to 308 s on eleven of the day's seventeen reads,
    the research call with six web searches most of it). 0 waits as long
    as it takes."""
    try:
        v = float(os.environ.get(ENV_RESEARCH_BUDGET, str(RESEARCH_BUDGET_S)))
    except (TypeError, ValueError):
        v = RESEARCH_BUDGET_S
    return max(0.0, v)


def research_max_uses() -> int:
    try:
        v = int(os.environ.get(ENV_RESEARCH_MAX_USES, str(RESEARCH_MAX_USES)))
    except (TypeError, ValueError):
        v = RESEARCH_MAX_USES
    return max(1, v)


def _research_tools(tools):
    """The web search tool capped at the research step's own number of
    searches; anything else passes through."""
    out = []
    for t in (tools or []):
        if isinstance(t, dict) and str(t.get('type') or '').startswith('web_search'):
            t = dict(t, max_uses=min(int(t.get('max_uses') or RESEARCH_MAX_USES), research_max_uses()))
        out.append(t)
    return out


def research_bounded(ask: dict, claude_json=None, tools=None, *, changed_windows=None, call: bool = True,
                     budget_s: Optional[float] = None, stages: Optional[dict] = None) -> dict:
    """`research` under a time budget. The saved anchors come back at
    once; a call that outruns the budget leaves the read to go on without
    its anchors while the call finishes on its own thread and saves what
    it found for the next ask on the subject. `stages['research_timeout']`
    marks a read that went on without them."""
    budget = research_budget_s() if budget_s is None else float(budget_s)
    key = ask_key(ask if isinstance(ask, dict) else {})
    empty = {'anchors': [], 'events': [], 'block': '', 'marks': [], 'cached': False, 'key': key}
    if budget <= 0 or not call:
        return research(ask, claude_json, tools, changed_windows=changed_windows, call=call)
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout
    import contextvars
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pm-research')
    try:
        ctx = contextvars.copy_context()
        fut = ex.submit(ctx.run, lambda: research(ask, claude_json, tools, changed_windows=changed_windows, call=call))
        try:
            return fut.result(timeout=budget)
        except _Timeout:
            print(f"[pm-knowledge] research outran its {budget:.0f}s budget; the read goes on without the anchors "
                  f"(the call finishes on its own and saves them for the next ask)")
            if isinstance(stages, dict):
                stages['research_timeout'] = int(budget * 1000)
            empty['timed_out'] = True
            return empty
        except Exception as e:
            print(f"[pm-knowledge] research step skipped: {type(e).__name__}: {e}")
            return empty
    finally:
        ex.shutdown(wait=False)


def research_needed(text, *, researched=False, h=None, led=None, facts=None, twin=None, changed=None) -> bool:
    """Whether the read needs a research call. Always for a subject we
    hold no profile on or read off another base, for a question about
    the world, and for a near twin whose window changed (the dated events
    decide the band). Otherwise only when nothing is on file: no profile
    in the catalog, no stored figures in the ledger, no figures across
    products. The gate is off under PM_KNOWLEDGE_RESEARCH_GATE=0 (every
    read researches, as before)."""
    if not research_gate_enabled():
        return True
    if researched:
        return True
    try:
        from . import catalog_lane as _cat
        if _cat.world_question(text):
            return True
    except Exception:
        pass
    if twin and changed:
        return True
    have_profile = isinstance((h or {}).get('profile'), dict) and bool((h or {}).get('facts'))
    have_stored = bool([e for e in ((led or {}).get('entries') or []) if isinstance(e, dict) and (e.get('metrics') or e.get('reply'))])
    have_shared = bool((facts or {}).get('figures')) or bool((facts or {}).get('size'))
    return not (have_profile or have_stored or have_shared)


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
        # The independent lookups run side by side (2026-11-23, faster
        # answers): the valuation on file, the subject's catalog facts, the
        # shared figures across products and the near twin in the ledger.
        # Each is fail-safe on its own; a slow one never holds the others.
        _win = ask.get('window') if (ask.get('window') or {}).get('start') else None
        _pairing = _pairing_of(text, ask, base, mr)

        def _f_valuation():
            return valuation_for_read(text, ask, base, mr)

        def _f_held():
            return held(ask.get('subject'), window=_win)

        def _f_facts():
            return subject_facts(ask.get('subject') or base_subject, window=_win, pairing=_pairing, attribution=False)

        def _f_twin():
            if not (_win and ask.get('subject')):
                return None
            _led = led if isinstance(led, dict) and led.get('entries') is not None else None
            if _led is None:
                import insights_ledger as _il
                _led = _il.consult(subject=ask.get('subject'), question=text, history=history)
            return _twin.near_twin_entry({'subject': _led.get('subject') or ask.get('subject'),
                                          'family': ask.get('family'), 'window': ask.get('window')},
                                         list(_led.get('entries') or []))

        results = _side_by_side({'valuation': _f_valuation, 'held': _f_held, 'facts': _f_facts, 'twin': _f_twin})
        bound, others = results.get('valuation') or (None, [])
        h = results.get('held') or {'subject': ask.get('subject'), 'facts': [], 'profile': None, 'block': '', 'marks': []}
        sf = results.get('facts') or None
        st = {'twin': results.get('twin'), 'marks': []}
        # a read about a valuation on file reads that file, for exactly
        # the window asked, and never mixes runs (Jenna 2026-11-20)
        if bound:
            vb = _twin.valuation_block(bound, others)
            if vb:
                parts.append(vb)
            leave_valuation(ask.get('subject') or base.get('subject'), text, bound, others)
            h = filter_held_to_valuation(h, (bound or {}).get('ref'))
        elif callable(saved):
            try:
                blk = saved(text, window=ask.get('window')) if (ask.get('window') or {}).get('start') else saved(text)
            except TypeError:
                blk = saved(text)
            except Exception:
                blk = ''
            if blk:
                parts.append(str(blk))
        if h.get('block'):
            parts.append(h['block'])
        elif callable(catalog) and not borrowed:
            try:
                blk = catalog(base.get('subject'))
            except Exception:
                blk = ''
            if blk:
                parts.append(str(blk))
        if st['twin']:
            tb = _twin.twin_block(st['twin'], ask.get('window'))
            if tb:
                parts.append(tb)
        changed = _twin.changed_windows((st['twin'] or {}).get('window') or {}, ask.get('window') or {}) if st['twin'] else None
        unheld = bool(researched and not h.get('profile'))   # a subject we hold no profile on (the build offer reads this, 2026-11-23)
        if unheld:
            ask['size_needed'] = True
        # The research call runs only when the held and recorded figures do
        # not already answer (2026-11-23): a subject we hold no profile on,
        # a question about the world, a twin whose window changed (its dated
        # events decide the band), or a subject with nothing on file. Saved
        # anchors are still read back without a call.
        need_call = research_needed(text, researched=researched, h=h, led=led, facts=sf, twin=st['twin'], changed=changed)
        if need_call and callable(stage):
            try:
                stage('researching')
            except Exception:
                pass
        r = research_bounded(ask, claude_json, tools, changed_windows=changed or None, call=need_call, stages=stages)
        if r.get('block'):
            parts.append(r['block'])
        cap, cap_source, cohort_base = None, '', ''
        if researched:
            cap = _twin.anchor_low_people(r.get('anchors') or [])
            cap_source = 'research' if cap else ''
            _saved = saved_people(ask.get('subject'), ask.get('window')) if unheld else None
            if _saved:
                # the size already saved for the subject binds the read, so
                # later answers and builds land on one figure (2026-11-23)
                cap, cap_source = _saved, 'saved'
        elif isinstance(h.get('profile'), dict) and _twin.same_name(h['profile'].get('subject') or ask.get('subject'), ask.get('subject') or ''):
            cap = _num(h['profile'].get('audience_size'))
            cap_source = 'profile' if cap else ''
        elif named and base_subject and not _twin.same_name(named, base_subject) and cohort_of_base(named, base_subject):
            # a cohort of the base's own subject: its ceiling is the base profile's whole audience, never a
            # researched size for the cohort's label (2026-11-24, Armine)
            try:
                hb = held(base_subject, window=_win)
                cap = _num((hb.get('profile') or {}).get('audience_size')) if isinstance(hb.get('profile'), dict) else None
            except Exception:
                cap = None
            cap_source = 'profile' if cap else ''
            cohort_base = base_subject if cap else ''
        # one set of figures across products (2026-11-23): the subject's saved
        # size, records, valuations, title universe and campaign ride the read
        # as one block, and the whole audience on file caps every count
        try:
            # a read bound to one valuation never carries the other runs' totals
            # as binding figures (the valuation block already names them as not
            # this answer, 2026-11-20); the pairing's valuations ride only when
            # no file is bound
            sf = sf if isinstance(sf, dict) else {'figures': [], 'ceiling': None, 'block': ''}
            if bound and sf.get('figures'):
                sf = dict(sf, figures=[x for x in sf['figures'] if x.get('product') != 'bpiq'])
                sf['block'] = shared_block(sf)
            if sf.get('block'):
                parts.append(sf['block'])
            if not cap and sf.get('ceiling'):
                cap, cap_source = int(sf['ceiling']), 'shared'
        except Exception as e:
            print(f"[pm-knowledge] shared figures skipped: {type(e).__name__}: {e}")
        sb = _twin.size_block(ask.get('subject'), cap, researched=researched,
                              borrowed_from=(base_subject if borrowed else ''), window=default_win, cohort_of=cohort_base)
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
                              'cached': bool(r.get('cached')), 'research_call': bool(need_call and not r.get('cached') and not r.get('skipped')),
                              'research_skipped': bool(r.get('skipped')), 'near_window': bool(r.get('near_window')), 'marks': marks_of(h, r)[:60],
                              'named_subject': named, 'borrowed_base': base_subject if borrowed else '', 'size_cap': cap, 'size_source': cap_source,
                              'subject': str(ask.get('subject') or named or ''), 'unheld': unheld,
                              'shared_figures': len((sf or {}).get('figures') or []),
                              'size_window': {'start': str((ask.get('window') or {}).get('start') or ''),
                                              'end': str((ask.get('window') or {}).get('end') or '')}}
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


# ------------------------------------------------------------------------
# One set of figures across products (Jenna 2026-11-23, round 2, item 2)
#
# Profile IQ, Brand Partnership IQ, Attribution IQ, Journey IQ, Subscriber
# IQ and the generated read all consult the same held figures, decision-
# of-record entries and saved subject sizes for a subject BEFORE computing,
# through `subject_facts`; a product's own figure for the subject is then
# held to them through `cross_hold`: a record for the same window is the
# figure; a people count never exceeds the subject's whole audience; a
# figure of the same kind on a nearby window sits within the near-twin
# band of the one another product already published unless a dated event
# in the changed part of the window moved it. journey_synthesis.corpus_anchors
# did part of this for journeys; this is the general form every product
# reads. Switch PM_SHARED_FIGURES=0 (env only).
# ------------------------------------------------------------------------
ENV_SHARED = 'PM_SHARED_FIGURES'
PEOPLE_KINDS = ('audience_people', 'reach_people', 'viewers_people')
PRODUCT_LABELS = {'profile': 'Profile IQ', 'bpiq': 'Brand Partnership IQ', 'attribution': 'Attribution IQ',
                  'journey': 'Journey IQ', 'subiq': 'Subscriber IQ', 'subscriber_iq': 'Subscriber IQ', 'profile_iq': 'Profile IQ',
                  'chat': 'Prometheus', 'record': 'On record', 'saved': 'The saved size', 'title': 'The title universe',
                  'ledger': 'On record', 'ledger_record': 'On record', 'estimates': 'The saved size', 'saved_table': 'The saved size',
                  'anchor_store': 'The saved size', 'title_anchor': 'The title universe'}
_FACTS_CACHE: dict = {}
_FACTS_TTL_S = 90.0
LIVE_FOR_TESTS = False       # a test that wants the title anchor and campaign reads sets this
_WHOLE_RX = re.compile(r"\b(?:us audience|audience size|total universe|whole audience|us people|us_audience|universe|fans?|audience)\b", re.I)
_REACH_RX = re.compile(r"\breach(?:ed)?\b|\bexposed\b|\bpartnership\b|\bcampaign\b", re.I)
_VIEW_RX = re.compile(r"\bview(?:er|ers|ed|s)?\b|\bwatch(?:ed|ers)?\b|\bstream(?:ed|ers)?\b|\blisten(?:ed|ers)?\b", re.I)
_ORDER_RX = re.compile(r"\border|\bbuyer|\bpurchas|\bconver", re.I)


def shared_enabled() -> bool:
    return enabled() and os.environ.get(ENV_SHARED, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def figure_kind(label='', unit='', name='') -> str:
    """The class a figure belongs to across products: share_pct,
    value_usd, orders, reach_people, viewers_people, audience_people,
    people (an unlabeled count), or '' for anything else."""
    u = str(unit or '').lower()
    text = f"{label or ''} {str(name or '').replace('_', ' ')}"
    if u in ('pct', 'percent', '%', 'share', 'pct1', 'pct2'):
        return 'share_pct'
    if u in ('usd', 'money', 'dollars', '$'):
        return 'value_usd'
    if u in ('orders',) or (_ORDER_RX.search(text) and 'people' not in text.lower() and 'audience' not in text.lower()):
        return 'orders'
    if u in ('people', 'individuals', 'persons', 'users', 'viewers', 'accounts', 'buyers', 'subscribers', 'count', ''):
        if _REACH_RX.search(text):
            return 'reach_people'
        if _VIEW_RX.search(text):
            return 'viewers_people'
        if _WHOLE_RX.search(text):
            return 'audience_people'
        return 'people' if u else ''
    return ''


def _fig(product, kind, value, *, label='', window=None, ref='', as_of='', record=False, unit='') -> Optional[dict]:
    v = _num(value)
    if v is None or v <= 0 or not kind:
        return None
    w = window if isinstance(window, dict) else {}
    return {'product': str(product or '')[:24], 'kind': kind, 'value': v, 'unit': unit or ('usd' if kind == 'value_usd' else 'pct' if kind == 'share_pct' else 'people'),
            'label': str(label or '')[:120], 'window': {'start': str(w.get('start') or '')[:10], 'end': str(w.get('end') or '')[:10]},
            'ref': str(ref or '')[:200], 'as_of': str(as_of or '')[:32], 'record': bool(record)}


def subject_facts(subject, *, window=None, pairing=None, extra_subjects=(), attribution=True, exclude_ref='') -> dict:
    """Everything Crosswalk already holds on a subject, as one set of
    figures: `size` (the saved audience size every build starts from,
    `migration.saved_subject_size`), `figures` (each product's published
    figures with their windows: Profile IQ sizes, Brand Partnership IQ
    valuations, Attribution IQ campaign reach, Journey IQ stages,
    Subscriber IQ title universes, the ledger's decisions of record),
    `ceiling` (the subject's whole US audience: no count of it goes
    higher) and `block` (the prompt block every product carries). Never
    raises; every source is optional."""
    out = {'subject': str(subject or '').strip(), 'size': None, 'figures': [], 'records': [], 'ceiling': None, 'block': '', 'marks': [],
           'events': []}
    if not shared_enabled() or not out['subject']:
        return out
    w0 = window if isinstance(window, dict) else {}
    ck = (_fold(out['subject']), str(w0.get('start') or ''), str(w0.get('end') or ''), tuple(_fold(x) for x in (pairing or ())),
          tuple(_fold(x) for x in (extra_subjects or ())), bool(attribution), str(exclude_ref or ''))
    now = time.time()
    with _PENDING_LOCK:
        hit = _FACTS_CACHE.get(ck)
    if hit and now - hit[0] < _FACTS_TTL_S:
        return json.loads(json.dumps(hit[1]))
    figs = out['figures']
    # 1. the saved size (the one size a fresh build of the subject starts from)
    try:
        from migration import saved_subject_size as _sss
        sz = _sss.lookup(out['subject'])
        if sz and _num(sz.get('us')):
            w = sz.get('window')
            win = {'start': w[0], 'end': w[1]} if isinstance(w, (list, tuple)) and len(w) >= 2 else {}
            out['size'] = {'us': int(sz['us']), 'tu': int(sz.get('tu') or 0), 'source': str(sz.get('source') or ''), 'ref': str(sz.get('ref') or ''), 'window': win}
            f = _fig(str(sz.get('source') or 'saved'), 'audience_people', sz['us'], label=f"{out['subject']} US audience (saved size)", window=win,
                     ref=sz.get('ref'), record=str(sz.get('source') or '') == 'ledger')
            if f:
                figs.append(f)
    except Exception as e:
        print(f"[pm-knowledge] saved size skipped: {type(e).__name__}: {e}")
    # 2. the corpus catalog: each product's published figures
    try:
        names = [out['subject']] + [str(x) for x in (extra_subjects or []) if str(x or '').strip()]
        for name in names:
            cat = catalog_anchors(name, window=window, with_ledger=False)
            for f in (cat.get('facts') or []):
                if not isinstance(f, dict) or f.get('product') == 'chat' or f.get('kind') in ('stated_number', 'sample_size'):
                    continue
                if exclude_ref and str((f.get('source') or {}).get('key') or '').endswith(exclude_ref):
                    continue
                kind = {'audience_size': None, 'platform_count': 'viewers_people', 'valuation': None, 'title_viewers': 'viewers_people',
                        'stage': 'people', 'reach': 'reach_people'}.get(str(f.get('kind') or ''), None)
                if kind is None:
                    kind = figure_kind(f.get('label'), f.get('unit'))
                    if f.get('kind') == 'audience_size' and f.get('product') == 'profile':
                        kind = 'audience_people'
                    elif f.get('kind') == 'audience_size' and f.get('product') == 'bpiq':
                        kind = 'reach_people'
                if not kind:
                    continue
                fig = _fig(f.get('product'), kind, f.get('value'), label=f.get('label'), window=f.get('window'), ref=(f.get('source') or {}).get('key'),
                           as_of=f.get('as_of'), unit=f.get('unit'))
                if fig:
                    fig['cut'] = bool(f.get('cut'))
                    figs.append(fig)
            for m in (cat.get('marks') or [])[:20]:
                out['marks'].append(m)
    except Exception as e:
        print(f"[pm-knowledge] catalog facts skipped: {type(e).__name__}: {e}")
    # 3. the ledger's decisions of record on the subject
    try:
        import insights_ledger as _il
        led = _il.consult(subject=out['subject'])
        for e in (led.get('entries') or []):
            if not isinstance(e, dict) or not _il.is_record_entry(e) or e.get('retired'):
                continue
            out['records'].append({k: e.get(k) for k in ('k', 'route', 'ws', 'we', 'q', 'family')})
            for m in (e.get('metrics') or []):
                if not isinstance(m, dict):
                    continue
                kind = figure_kind(m.get('label'), m.get('unit'), m.get('name'))
                if str(m.get('name') or '').endswith('_us_audience') or str(m.get('name') or '') == 'us_audience':
                    kind = 'audience_people'
                fig = _fig('record', kind, m.get('value'), label=m.get('label') or m.get('name'), window={'start': e.get('ws'), 'end': e.get('we')},
                           ref=e.get('k'), as_of=e.get('ts'), record=True, unit=m.get('unit'))
                if fig:
                    fig['metric'] = str(m.get('name') or '')[:48]
                    figs.append(fig)
    except Exception as e:
        print(f"[pm-knowledge] record lookup skipped: {type(e).__name__}: {e}")
    # 4. valuations on the pairing (both orders of the two names)
    if pairing and len(pairing) == 2:
        try:
            for c in _twin.valuation_candidates(str(pairing[0] or ''), str(pairing[1] or ''), exclude_ref=exclude_ref):
                if not isinstance(c, dict):
                    continue
                w = c.get('window') or {}
                d = c.get('drivers') or {}
                fg = c.get('figures') or {}
                for kind, val, label in (('reach_people', d.get('audience_reached'), 'US audience reached by the partnership'),
                                         ('value_usd', fg.get('total_usd'), 'total brand value'),
                                         ('value_usd', fg.get('attributable_usd'), 'value attributable to the partnership')):
                    fig = _fig('bpiq', kind, val, label=label, window=w, ref=c.get('ref'), as_of=c.get('as_of'))
                    if fig:
                        fig['eligible'] = c.get('eligible', True)
                        figs.append(fig)
        except Exception as e:
            print(f"[pm-knowledge] valuation figures skipped: {type(e).__name__}: {e}")
    hermetic = os.environ.get('REGRESSION_TEST_MODE') == '1' and not LIVE_FOR_TESTS
    # 5. the title universe (whichever product fixed it first)
    try:
        if hermetic:
            raise RuntimeError('hermetic: no title anchor read')
        from migration.title_anchors import get_title_anchor as _gta
        ta = _gta(out['subject'], window=window)
        if ta and _num(ta.get('us_viewers')):
            fig = _fig(str(ta.get('source_product') or 'title'), 'viewers_people', ta['us_viewers'], label=f"US viewers of {out['subject']} (the title universe)",
                       window=ta.get('window'), ref='title_anchor', as_of=ta.get('recorded_at') or ta.get('updated_at'))
            if fig:
                fig['title_universe'] = True
                figs.append(fig)
    except Exception as e:
        if not hermetic:
            print(f"[pm-knowledge] title anchor skipped: {type(e).__name__}: {e}")
    # 6. the Attribution IQ campaign on the subject
    if attribution and not hermetic:
        try:
            from migration import journey_synthesis as _js
            camp = _js.find_attribution_campaign(out['subject'])
            nest = (camp or {}).get('nest') or {}
            total = None
            if isinstance(nest, dict):
                for k in ('reached', 'reach', 'exposed', 'total', 'aware'):
                    if _num(nest.get(k)):
                        total = nest[k]
                        break
                if total is None:
                    vals = [v for v in nest.values() if _num(v)]
                    total = max(vals) if vals else None
            if camp and total:
                fig = _fig('attribution', 'reach_people', total, label=f"US people the {camp.get('display_name') or 'campaign'} reached",
                           window={'start': camp.get('campaign_start'), 'end': camp.get('as_of')}, ref=camp.get('slug') or '', as_of=camp.get('as_of'))
                if fig:
                    figs.append(fig)
        except Exception as e:
            print(f"[pm-knowledge] attribution campaign skipped: {type(e).__name__}: {e}")
    # 7. the dated events a refresh of this subject researched for its new
    #    window (migration/refresh_sway, 2026-11-24): the near-twin holds read
    #    them as their research override. Internal; never in a reader block.
    try:
        from migration import refresh_sway as _rsw
        out['events'] = _rsw.recorded_events(out['subject'])
    except Exception as e:
        print(f"[pm-knowledge] refresh events skipped: {type(e).__name__}: {e}")
    # the whole audience: the ceiling on every count of this subject
    wholes = [f['value'] for f in figs if f['kind'] == 'audience_people' and not f.get('cut')]
    if wholes:
        out['ceiling'] = int(round(max(wholes)))
    out['block'] = shared_block(out)
    with _PENDING_LOCK:
        if len(_FACTS_CACHE) > 200:
            for k in [k for k, v in _FACTS_CACHE.items() if now - v[0] > _FACTS_TTL_S]:
                _FACTS_CACHE.pop(k, None)
        _FACTS_CACHE[ck] = (now, json.loads(json.dumps(out)))
    return out


def _plain_win(w) -> str:
    try:
        return _twin._plain_window(w) if isinstance(w, dict) and w.get('start') else ''
    except Exception:
        return ''


def shared_block(facts: dict, *, max_lines: int = 18) -> str:
    """The prompt block every product carries for a subject: the saved
    size first, then each product's figures with their windows, then the
    rules. Nothing a reader sees; prompt text only."""
    f = facts if isinstance(facts, dict) else {}
    subj = str(f.get('subject') or 'the subject')
    figs = [x for x in (f.get('figures') or []) if isinstance(x, dict)]
    if not figs and not f.get('size'):
        return ''
    lines = [f"FIGURES ON FILE FOR {subj} (one set of numbers across every Crosswalk product; binding):"]
    if f.get('size'):
        sz = f['size']
        w = _plain_win(sz.get('window'))
        lines.append(f"- Audience size: {int(sz['us']):,} US people{(' (' + w + ')') if w else ''}, the size every build of {subj} starts from. "
                     f"No count of this audience exceeds it.")
    seen = set()
    for x in sorted(figs, key=lambda y: (y.get('record') is False, y.get('product') or '', y.get('window', {}).get('start') or '')):
        key = (x['product'], x['kind'], round(x['value'], 2), x['window'].get('start'), x['window'].get('end'))
        if key in seen or (f.get('size') and x['kind'] == 'audience_people' and x['product'] in ('ledger', 'saved', 'estimates', 'saved_table', 'anchor_store')):
            continue
        seen.add(key)
        if x['kind'] == 'value_usd':
            val = f"${x['value']:,.2f}"
        elif x['kind'] == 'share_pct':
            val = f"{x['value']:.1f}%"
        else:
            val = f"{int(round(x['value'])):,} US people"
        prod = PRODUCT_LABELS.get(x['product'], x['product'] or 'Crosswalk')
        w = _plain_win(x.get('window'))
        on_rec = ' (on record)' if x.get('record') and prod != 'On record' else ''
        lines.append(f"- {prod}{(', ' + w) if w else ''}: {val} ({x.get('label') or x['kind'].replace('_', ' ')}){on_rec}.")
        if len(lines) >= max_lines:
            lines.append('- (more on file)')
            break
    lines.append(f"Rules: {subj} carries one set of numbers in every product. A figure on record for the same window IS the figure. "
                 f"A new figure of the same kind on a nearby window sits within a quarter of the one on file unless a dated event in the "
                 f"changed part of the window moved it; then name the event and its date in one plain line. A count of part of this "
                 f"audience never exceeds the audience.")
    return '\n'.join(lines)


def cross_hold(value, *, kind, facts, window=None, salt='', events=None, label='', product='', exclude_products=()) -> tuple:
    """(value, record) for one figure a product is about to publish on a
    subject. A decision of record of the same kind for the same window is
    the figure. A people count never exceeds the subject's whole audience
    (reduced under it with a salted inset). A figure of the same kind on
    a nearby window is held inside the near-twin band of the earliest one
    another product published, unless `events` (or the dated events a
    refresh of the subject recorded, `facts['events']`) carries a dated
    event in the changed part of the window (then it is left, lifted).
    Pure apart from the record it returns; never raises."""
    rec = {'kind': kind, 'label': label, 'product': product, 'before': _num(value), 'after': _num(value), 'why': 'kept', 'anchor': None}
    v = _num(value)
    f = facts if isinstance(facts, dict) else {}
    if not shared_enabled() or v is None or v <= 0:
        return value, rec
    try:
        figs = [x for x in (f.get('figures') or []) if isinstance(x, dict) and x.get('kind') == kind
                and x.get('product') not in (exclude_products or ()) and x.get('eligible', True) is not False]
        w = window if isinstance(window, dict) else {}
        # a record for the same window is the figure
        for x in figs:
            if x.get('record') and w.get('start') and x['window'].get('start') == str(w.get('start'))[:10] and x['window'].get('end') == str(w.get('end'))[:10]:
                rec.update(after=x['value'], why='record', anchor={k: x.get(k) for k in ('product', 'value', 'window', 'ref', 'label')})
                return (int(round(x['value'])) if kind != 'value_usd' and kind != 'share_pct' else x['value']), rec
        # a count of the subject never exceeds its whole audience
        ceiling = _num(f.get('ceiling'))
        if kind in PEOPLE_KINDS and ceiling and v > ceiling:
            inset = 0.005 + 0.025 * _twin._unit(salt, 'cross-ceiling', kind, label)
            held = _twin._messy_int(salt, ('cross-ceiling', kind, label), ceiling * (1.0 - inset))
            rec.update(after=held, why='ceiling', anchor={'value': ceiling, 'label': 'the whole audience on file'})
            v = float(held)
        # the near twin across products: a record first, else the earliest
        # figure of this kind on a nearby window
        near = []
        for x in figs:
            if not x['window'].get('start') or not w.get('start'):
                continue
            ok, why = _twin.windows_near(x['window'], {'start': str(w.get('start'))[:10], 'end': str(w.get('end'))[:10]})
            if ok:
                near.append(((0 if x.get('record') else 1), x.get('as_of') or x['window'].get('start') or '', x, why))
        if near:
            near.sort(key=lambda t: (t[0], t[1]))
            near = [(t[1], t[2], t[3]) for t in near]
            _, anchor, why = near[0]
            b = _twin.band('value' if kind == 'value_usd' else 'audience')
            lo, hi = anchor['value'] * (1.0 - b), anchor['value'] * (1.0 + b)
            if v < lo or v > hi:
                changed = _twin.changed_windows(anchor['window'], {'start': str(w.get('start'))[:10], 'end': str(w.get('end'))[:10]})
                ev = _twin.event_in_changed_window(changed, [e for e in (list(events or []) + list(f.get('events') or []))
                                                             if isinstance(e, dict)])
                if ev:
                    rec.update(why='lifted', anchor={k: anchor.get(k) for k in ('product', 'value', 'window', 'ref', 'label')}, event=ev)
                else:
                    inset = 0.02 + 0.06 * _twin._unit(salt, 'cross-twin', kind, label)
                    target = (lo * (1.0 + inset)) if v < lo else (hi * (1.0 - inset))
                    held = round(target, 2) if kind in ('value_usd', 'share_pct') else _twin._messy_int(salt, ('cross-twin', kind, label), target)
                    rec.update(after=held, why='held', anchor={k: anchor.get(k) for k in ('product', 'value', 'window', 'ref', 'label')}, near=why)
                    v = float(held)
        rec['after'] = v if kind in ('value_usd', 'share_pct') else int(round(v))
        return rec['after'], rec
    except Exception as e:
        print(f"[pm-knowledge] cross hold skipped: {type(e).__name__}: {e}")
        return value, rec


def record_cross_hold(rec: dict, *, subject='', question='', store=None) -> None:
    """A held, capped or record-bound figure leaves a staff record beside
    the twin records (system/pm_consistency/). Never raises."""
    try:
        if not isinstance(rec, dict) or rec.get('why') in (None, 'kept'):
            return
        _twin.write_record(dict(rec, verdict=f"cross_{rec.get('why')}", product=str(rec.get('product') or 'cross'), subject=str(subject or ''),
                                pair_key=_pieces.property_fold(subject), question=str(question or '')[:300], line='', line_shipped=False), store)
    except Exception:
        pass


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


__all__ = ['enabled', 'research_enabled', 'ask_from', 'ask_key', 'held', 'stored', 'research', 'research_bounded', 'research_budget_s', 'research_max_uses', 'anchors_block',
           'facts_block', 'marks_of', 'leave_twin', 'take_twin', 'peek_twin', 'hold_pending', 'read_blocks', 'insight_blocks',
           'leave_valuation', 'peek_valuation', 'bind_after', 'inline_window', 'valuation_for_read', 'world_block',
           'understanding_on_request', 'named_subject', 'base_is_borrowed', 'leave_size', 'peek_size', 'saved_people',
           'valuation_events', 'RESEARCH_PROMPT', 'ANCHOR_PREFIX', 'ENV', 'ENV_RESEARCH', 'PRODUCTS',
           'shared_enabled', 'figure_kind', 'subject_facts', 'shared_block', 'cross_hold', 'record_cross_hold', 'ENV_SHARED', 'PEOPLE_KINDS',
           'research_needed', 'research_gate_enabled', 'catalog_anchors', 'filter_held_to_valuation', 'anchor_ttl_s', 'ENV_ANCHOR_TTL', 'ENV_RESEARCH_GATE', 'cohort_of_base']
