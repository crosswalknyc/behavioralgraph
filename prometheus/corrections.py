"""Learn from every correction (Jenna 2026-11-23).

A correction is one of three things:

* a reader saying the answer was wrong ("that's not right", "this doesn't
  make sense"), with or without the right figure in their words;
* a reader pushing back on a replayed answer inside a thread;
* a staff member correcting a thread turn by hand (the ``meta.corrected_at``
  path used all of the week of 2026-11-16, until now with direct S3 writes).

Whichever way it arrives, the same three things happen, through this one
module:

a. the wrong ledger entry is retired by its words, and the corrected
   figure (when one was given) is recorded for the subject and window: a
   staff correction as the decision of record, a reader's figure as a
   correction row that outranks a read and never a record;
b. the wrong words are set aside, so no replay path (the ledger, a job
   payload, a thread shortcut) hands them back;
c. a sanitized replay fixture candidate is written to the staff-only
   queue under ``system/pm_replay_corpus/candidates/`` for the weekly
   corpus rebuild (another worker owns that rebuild; this module only
   writes the queue).

Staff corrections go through `correct_turn`: the thread turn is rewritten
in place (merge-safe: ``meta.corrected_at`` is what the thread merge reads
as the server's correction, a newer stamp wins), the read job document
behind the turn takes the same correction so the registry never replays
it, and the ledger and fixture steps run in the same call. The CLI is
``scripts/pm_correct_turn.py``. Nobody hand-edits S3.

Switch: ``PM_CORRECTIONS=0`` (env only) turns the learning off; the thread
correction itself still works. Never raises into a chat path; every
public function returns what it did.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

ENV = 'PM_CORRECTIONS'
CANDIDATES_PREFIX = 'system/pm_replay_corpus/candidates/'
CANDIDATES_INDEX = CANDIDATES_PREFIX + 'index.json'
CANDIDATES_MAX = 2000
STAFF_SOURCE = 'staff_correction'
READER_ROUTE = 'correction_reader'
READER_PROVENANCE = 'delivered_artifact'     # a reader's figure outranks a read, never a staff record

__all__ = ['enabled', 'figures_in', 'wrong_turn', 'learn_from_reader', 'learn_from_rejected_entry', 'correct_turn',
           'sanitize_text', 'candidate_case', 'write_candidate', 'list_candidates', 'CANDIDATES_PREFIX',
           'CANDIDATES_INDEX', 'STAFF_SOURCE', 'READER_ROUTE', 'SYNC_FOR_TESTS', 'is_stock_line', 'own_question',
           'CorrectionHost', 'job_chain', 'resolve_subject', 'merge_check']

SYNC_FOR_TESTS = False


def enabled() -> bool:
    return os.environ.get(ENV, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ledger():
    import insights_ledger as _il
    return _il


def _fold(s) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


# ------------------------------------------------------------ figures in a correction
_SCALE = {'k': 1e3, 'thousand': 1e3, 'm': 1e6, 'mm': 1e6, 'million': 1e6, 'mil': 1e6, 'b': 1e9, 'bn': 1e9, 'billion': 1e9}
_NUM_RX = re.compile(
    r"(?P<money>\$)?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<scale>thousand|million|billion|mil|bn|mm|k|m|b)?(?P<pct>\s*%|\s*percent\b)?",
    re.IGNORECASE)
_CORRECTS_RX = re.compile(
    r"\b(?:should\s+be|is\s+(?:actually|really|more\s+like|closer\s+to)|actually|the\s+(?:right|correct|real|actual)\s+"
    r"(?:number|figure|value|total|count|answer)\s+is|not\s+\d|it'?s\s+\d|its\s+\d|more\s+like|closer\s+to|"
    r"try\s+\d|use\s+\d|it\s+is\s+\d|is\s+\d)", re.IGNORECASE)
_NEG_RX = re.compile(r"\b(?:not|never|wrong|isn'?t|instead\s+of)\b", re.IGNORECASE)


def figures_in(text) -> list:
    """The figures a correction states: [{'value', 'unit', 'raw'}], unit
    'pct' | 'usd' | 'count'. A figure the words reject ("not 208M") is
    left out; a bare year is left out; a small bare integer with no scale,
    sign or percent (a list length, "3-5 word") is left out."""
    t = str(text or '')
    out = []
    for m in _NUM_RX.finditer(t):
        raw = m.group(0).strip()
        num = m.group('num').replace(',', '')
        try:
            v = float(num)
        except ValueError:
            continue
        scale = (m.group('scale') or '').lower()
        pct = bool(m.group('pct'))
        money = bool(m.group('money'))
        if scale and scale in _SCALE:
            v *= _SCALE[scale]
        elif scale:
            continue
        unit = 'pct' if pct else ('usd' if money else 'count')
        if unit == 'count' and not scale and not ',' in m.group('num'):
            if 1900 <= v <= 2100 and float(v).is_integer():
                continue                       # a year
            if v < 1000:
                continue                       # a list length, a word count, a page
        before = t[max(0, m.start() - 24):m.start()]
        if re.search(r"\b(?:not|never|instead\s+of|rather\s+than|isn'?t|wasn'?t)\s*$", before, re.IGNORECASE):
            continue                           # the figure the words reject
        out.append({'value': v, 'unit': unit, 'raw': raw})
    return out


def states_a_figure(text) -> bool:
    """True when the message gives a figure as the right one."""
    return bool(figures_in(text)) and bool(_CORRECTS_RX.search(str(text or '')) or _NEG_RX.search(str(text or '')))


# ------------------------------------------------------------ the turn the correction points at
_PROGRESS_RX = re.compile(r"^\s*(?:working on it|on it[:.]|still reading|picking the read back up|my fault)", re.IGNORECASE)
_QUESTION_RX = re.compile(r"\?\s*$")


def _meta(t) -> dict:
    m = t.get('meta') if isinstance(t, dict) else None
    return m if isinstance(m, dict) else {}


def wrong_turn(history, complaint='') -> tuple:
    """(index, turn) of the agent turn a complaint is about: the newest
    agent turn before the complaint that is an answer (not a progress
    line, not a bare question back, not already corrected), preferring
    one that carries figures. (None, None) when none."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    stop = len(turns)
    if complaint:
        want = ' '.join(str(complaint).split())
        for i in range(len(turns) - 1, -1, -1):
            if turns[i].get('role') == 'user' and ' '.join(str(turns[i].get('text') or '').split()) == want:
                stop = i
                break
    fallback = (None, None)
    for i in range(stop - 1, -1, -1):
        t = turns[i]
        if str(t.get('role') or '') == 'user':
            if fallback[0] is not None:
                break
            continue
        text = str(t.get('text') or '')
        m = _meta(t)
        if not text.strip() or _PROGRESS_RX.match(text) or m.get('corrected_at') or m.get('withdrawn'):
            continue
        if m.get('figures') or re.search(r"\d", text):
            return i, t
        if fallback[0] is None and not _QUESTION_RX.search(text.strip()):
            fallback = (i, t)
    return fallback


# ------------------------------------------------------------ what the turn is about
def _turn_facts(turn, job_doc=None) -> dict:
    """subject, window, question, job_id, figures of a landed turn."""
    m = _meta(turn)
    pl = (job_doc or {}).get('payload') if isinstance(job_doc, dict) else None
    pl = pl if isinstance(pl, dict) else {}
    return {
        'job_id': str(m.get('read_job_id') or m.get('landed_job_id') or '')[:40],
        'subject': str(m.get('subject') or pl.get('profile') or pl.get('subject') or (job_doc or {}).get('subject') or '')[:120],
        'window_start': str(pl.get('window_start') or '')[:10],
        'window_end': str(pl.get('window_end') or '')[:10],
        'window_label': str(pl.get('window_label') or '')[:80],
        'question': str((job_doc or {}).get('question') or '')[:300],
        'figures': [f for f in (m.get('figures') or pl.get('figures') or []) if isinstance(f, dict)],
        'metrics': [x for x in (pl.get('metrics') or []) if isinstance(x, dict)],
    }


def _prev_question(history, before_index) -> str:
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for i in range(min(before_index, len(turns)) - 1, -1, -1):
        if turns[i].get('role') == 'user' and str(turns[i].get('text') or '').strip():
            return str(turns[i]['text']).strip()[:300]
    return ''


def own_question(history, index) -> str:
    """The question the turn at `index` answered: the user turn right
    before it with no other agent turn in between. '' when the turn
    follows another agent turn (a resume's progress line, a second
    landing), so a neighbor turn's question is never taken for it
    (2026-11-23: a correction was filed under a subject read off an
    earlier turn)."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    if index is None or not (0 <= index < len(turns)):
        return ''
    for i in range(index - 1, -1, -1):
        role = str(turns[i].get('role') or '')
        if role == 'user':
            return str(turns[i].get('text') or '').strip()[:300]
        if str(turns[i].get('text') or '').strip():
            return ''
    return ''


_STOCK_RX = (
    re.compile(r"^\s*(?:working on it|on it[:.]|still reading|picking the read back up)", re.I),
    re.compile(r"^\s*(?:my fault|thank you|thanks|got it|understood|noted|sure|okay|ok)\b", re.I),
    re.compile(r"\b(?:which|what) (?:audience|subject|campaign|profile|title|window|dates?) (?:should|is|do|did|would|are)\b.{0,80}\?", re.I),
    re.compile(r"^\s*(?:the answer to your question is just above|this line is withdrawn|this build could not be completed|stopped at your request)", re.I),
    re.compile(r"\b(?:i can pull that read|pulling this data will|approve & run|approve and run|credits? (?:for this|will be charged)|will cost|runs? (?:it|all \w+) for)\b", re.I),
    re.compile(r"\b(?:deck|file|read|report) (?:is|are) (?:ready|attached|on its way|in your inbox)\b", re.I),
    re.compile(r"\bi (?:have|will) (?:built|sent|email(?:ed)?|send)\b.{0,60}\b(?:deck|inbox|email)\b", re.I),
)


def is_stock_line(text) -> bool:
    """A house line that is never a wrong answer to retire or set aside
    by its words: a promise or progress line, a held line, an ack, a
    clarify, an offer or a quote, a delivery note, a withdrawn or failed
    line, or any short line with no figure in it. Setting one of these
    aside by its words would set it aside for every user (2026-11-23)."""
    t = ' '.join(str(text or '').split())
    if not t:
        return True
    try:
        from . import promise_truth as _pt
        if _pt.is_calm(t) or _pt.promise_kind(t):
            return True
    except Exception:
        pass
    try:
        import insights_ledger as _il
        if _il.is_offer_reply(t) or _il.is_retired_surface_reply(t):
            return True
    except Exception:
        pass
    for rx in _STOCK_RX:
        if rx.search(t):
            return True
    if len(t) < 160 and not re.search(r"\d", t):
        return True
    return False


def _unit_class(unit_or_kind) -> str:
    u = str(unit_or_kind or '').lower()
    if u in ('pct', 'percent', '%', 'share', 'pct1', 'pct2'):
        return 'pct'
    if u in ('usd', 'money', 'dollars', '$'):
        return 'usd'
    return 'count'


def _slug(label) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(label or '').lower()).strip('_')[:48] or 'corrected_figure'


def corrected_metrics(stated: list, facts: dict) -> list:
    """The stated figures as ledger metrics: each takes the wrong turn's
    first figure of the same class (its name and label), else a plain
    'corrected figure' row. A stated figure replaces, so no magnitude
    matching is done."""
    out, used = [], set()
    pool = []
    for mm in facts.get('metrics') or []:
        pool.append({'name': mm.get('name'), 'label': mm.get('label') or mm.get('name'), 'unit': mm.get('unit')})
    for f in facts.get('figures') or []:
        pool.append({'name': _slug(f.get('label')), 'label': f.get('label'), 'unit': f.get('kind')})
    for s_ in stated:
        klass = _unit_class(s_.get('unit'))
        pick = None
        for i, cand in enumerate(pool):
            if i in used or _unit_class(cand.get('unit')) != klass:
                continue
            pick = (i, cand)
            break
        if pick:
            used.add(pick[0])
            name, label = str(pick[1].get('name') or _slug(pick[1].get('label'))), str(pick[1].get('label') or pick[1].get('name'))
        else:
            name, label = ('corrected_share' if klass == 'pct' else 'corrected_value' if klass == 'usd' else 'corrected_count'), 'Corrected figure'
        unit = 'pct' if klass == 'pct' else ('usd' if klass == 'usd' else 'people')
        out.append({'name': name, 'label': label[:90], 'value': s_['value'], 'unit': unit,
                    'definition': 'corrected figure given in the conversation'})
    return out


# ------------------------------------------------------------ sanitizing
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RX = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PHONE_RX = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
_TOKEN_RX = re.compile(r"\b(?:sk|key|token|bearer)[-_ ]?[A-Za-z0-9]{12,}\b", re.IGNORECASE)


def sanitize_text(text, names=()) -> str:
    """Addresses, links, phone numbers and keys come out; the seat and
    any name in `names` are replaced with a placeholder."""
    t = str(text or '')
    t = _EMAIL_RX.sub('[email]', t)
    t = _URL_RX.sub('[link]', t)
    t = _PHONE_RX.sub('[phone]', t)
    t = _TOKEN_RX.sub('[key]', t)
    for n in sorted({str(x) for x in (names or []) if str(x or '').strip()}, key=len, reverse=True):
        t = re.sub(re.escape(n), '[seat]', t, flags=re.IGNORECASE)
    return ' '.join(t.split())


def _guess_class(question, complaint='') -> str:
    try:
        from . import intent_classes as _ic
        c = _ic.class_of('reasoned_metrics', 'answered', 'analyze')
        return str(c or 'read')
    except Exception:
        return 'read'


def candidate_case(*, question, wrong_text, correction_text='', corrected=None, subject='', window=None, history=None,
                   seat='', by='reader', view='', job_id='', kind='wrong_answer', names=()) -> dict:
    """One sanitized fixture candidate in the corpus case shape: the
    question, the last turns (text only), the words that were wrong,
    the correction, and `expected` for the weekly rebuild to pin. No
    seat, no thread id, no address. Names the weekly worker should
    invent are listed under `names_to_invent`."""
    hide = [seat] + [n for n in (names or []) if n]
    hist = []
    for t in [x for x in (history or []) if isinstance(x, dict)][-6:]:
        txt = sanitize_text(t.get('text'), hide)
        if txt:
            hist.append({'role': 'user' if str(t.get('role') or '') == 'user' else 'agent', 'text': txt[:600]})
    q = sanitize_text(question, hide)
    cid = 'cand_' + hashlib.sha1(f"{_fold(q)}|{_fold(wrong_text)[:200]}".encode()).hexdigest()[:12]
    w = window if isinstance(window, dict) else {}
    case = {
        'id': cid,
        'question': q[:500],
        'view': str(view or '')[:40],
        'surface': 'analyze',
        'subject': sanitize_text(subject, hide)[:120] or None,
        'history': hist,
        'reading': None,
        'recorded': None,
        'expected': {
            'never_says': sanitize_text(wrong_text, hide)[:400],
            'correction': sanitize_text(correction_text, hide)[:400],
            'figures': [dict(m) for m in (corrected or []) if isinstance(m, dict)][:12],
            'window': {'start': str(w.get('start') or '')[:10], 'end': str(w.get('end') or '')[:10]},
        },
        'seat': 'fx',
        'class': _guess_class(q),
        'kind': str(kind or 'wrong_answer')[:40],
        'by': 'staff' if str(by or '').startswith('staff') else 'reader',
        'created': _now_iso(),
        'needs_invented_names': True,
        'names_to_invent': [str(n)[:80] for n in (names or []) if n][:8] + ([str(subject)[:80]] if subject else []),
        'source_job_id': str(job_id or '')[:40],
        'note': 'a correction: the weekly corpus rebuild invents the names, pins recorded on main and commits it as a fixture',
    }
    return case


def _client():
    return _ledger()._client()


def _bucket():
    return _ledger().S3_BUCKET


def write_candidate(case: dict) -> str:
    """Write the candidate under the staff-only queue and list it in the
    index (ETag-guarded). Returns its key, '' on failure."""
    if not isinstance(case, dict) or not case.get('id'):
        return ''
    key = f"{CANDIDATES_PREFIX}{case['id']}.json"
    try:
        s3 = _client()
        s3.put_object(Bucket=_bucket(), Key=key, Body=json.dumps(case, indent=2).encode('utf-8'), ContentType='application/json')
        from migration.s3_json_state import update_json as _upd

        def _mut(doc):
            doc = doc if isinstance(doc, dict) else {}
            rows = [r for r in (doc.get('candidates') or []) if isinstance(r, dict) and r.get('id') != case['id']]
            rows.append({'id': case['id'], 'key': key, 'created': case.get('created'), 'kind': case.get('kind'), 'by': case.get('by'),
                         'class': case.get('class'), 'subject': case.get('subject'), 'status': 'pending'})
            doc['candidates'] = rows[-CANDIDATES_MAX:]
            doc['updated'] = _now_iso()
            return doc
        _upd(_bucket(), CANDIDATES_INDEX, _mut, default={'candidates': []}, s3=s3)
        return key
    except Exception as e:
        print(f"[pm-corrections] candidate not written: {type(e).__name__}: {e}")
        return ''


def list_candidates(status='pending') -> list:
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=CANDIDATES_INDEX)
        doc = json.loads(resp['Body'].read().decode('utf-8'))
        rows = [r for r in (doc.get('candidates') or []) if isinstance(r, dict)]
        return [r for r in rows if not status or r.get('status') == status]
    except Exception:
        return []


# ------------------------------------------------------------ the learning itself
def _learn(*, username, thread_id, history, wrong_text, correction_text, question, facts, by, kind, view='', stated=None,
           corrected_reply='', names=()) -> dict:
    """The three steps on one wrong answer. Returns what was done."""
    il = _ledger()
    done = {'retired': [], 'set_aside': 0, 'recorded': [], 'candidate': '', 'catalog_retired': 0, 'skipped': ''}
    subject = str(facts.get('subject') or '')
    ws, we = str(facts.get('window_start') or ''), str(facts.get('window_end') or '')
    stock = is_stock_line(wrong_text)
    if not subject and question:
        try:
            from . import knowledge as _know
            subject = str(_know.named_subject(question) or '').strip()   # the turn's own question, never a neighbor's
        except Exception:
            subject = ''
    if stock:
        # a promise line, an ack, a held line or an offer is never a wrong
        # answer by its words: retiring or setting it aside would reach
        # every user's copy of the same line (2026-11-23). The staff's
        # corrected figures still bank below.
        done['skipped'] = 'stock_line'
        print(f"[pm-corrections] {kind} by {by}: the corrected turn is a house line; nothing retired or set aside by its words")
    else:
        try:
            # only this subject's rows: never another subject's row that
            # happens to carry the same words. The turn's own window first;
            # the same words on the same subject on another window are the
            # same wrong answer and go on the second pass
            hits = il.retire_by_reply(wrong_text, by=by, note=str(correction_text or '')[:160], subject=subject or None,
                                      window=({'start': ws, 'end': we} if ws and we else None)) if subject else []
            if subject and not hits and ws and we:
                hits = il.retire_by_reply(wrong_text, by=by, note=str(correction_text or '')[:160], subject=subject)
            done['retired'] = [h.get('k') for h in hits]
            if hits and not (ws and we):
                ws, we = str(hits[0].get('ws') or ''), str(hits[0].get('we') or '')
            if hits and not question:
                question = str(hits[0].get('q') or '')
        except Exception:
            traceback.print_exc()
        try:
            done['set_aside'] = il.set_aside_reply(wrong_text, subject=subject, question=question, window_start=ws, window_end=we,
                                                   by=by, reason=str(correction_text or kind)[:200], job_id=facts.get('job_id') or '',
                                                   user=username or '', thread=thread_id or '')
        except Exception:
            traceback.print_exc()
        try:
            from migration import corpus_catalog as _cc
            done['catalog_retired'] = int(_cc.retire_answer(username or '', thread_id or '', wrong_text, subject=subject or None, by=by) or 0)
        except Exception:
            done['catalog_retired'] = 0
    stated = list(stated or [])
    if stated and all(isinstance(x, dict) and x.get('name') for x in stated):
        corrected = corrected_metrics_from(stated)        # staff named the figures
    else:
        corrected = corrected_metrics(stated, facts) if stated else []
    if subject and (corrected or corrected_reply):
        try:
            if str(by or '').startswith('staff'):
                done['recorded'] = il.bank_record(subject=subject, source=STAFF_SOURCE, metrics=corrected, question=question or f"{subject}",
                                                  reply=corrected_reply or '', window_start=ws, window_end=we,
                                                  window_label=facts.get('window_label') or '', metric_family=il.family_from_question(question) or '',
                                                  retire_others=bool(corrected), asker=username or None) or []
            elif corrected:
                il.persist(subject=subject, metric_family=il.family_from_question(question) or '', question=question or subject,
                           route=READER_ROUTE, metrics=corrected, window_start=ws, window_end=we,
                           window_label=facts.get('window_label') or '', reply='', provenance=READER_PROVENANCE, sync=True,
                           derivation=f"a reader's correction in the conversation: {str(correction_text or '')[:160]}", asker=username or None)
                done['recorded'] = [il.entry_key(subject, il.family_from_question(question) or '', ws, we)]
        except Exception:
            traceback.print_exc()
    if not stock or (subject and (corrected or corrected_reply)):
        try:
            case = candidate_case(question=question, wrong_text=wrong_text, correction_text=correction_text, corrected=corrected,
                                  subject=subject, window={'start': ws, 'end': we}, history=history, seat=username or '',
                                  by=by, view=view, job_id=facts.get('job_id') or '', kind=kind, names=names)
            done['candidate'] = write_candidate(case)
        except Exception:
            traceback.print_exc()
    print(f"[pm-corrections] {kind} by {by} on {subject or '?'}: retired {len(done['retired'])} ledger row(s), "
          f"set aside {done['set_aside']} signature(s), recorded {len(done['recorded'])}, candidate {done['candidate'] or 'none'}")
    return done


def _run(fn, *a, **k):
    if SYNC_FOR_TESTS:
        return fn(*a, **k)
    threading.Thread(target=fn, args=a, kwargs=k, daemon=True).start()
    return None


def learn_from_reader(username, thread_id, history, complaint, *, view='', job_doc_loader=None):
    """A reader said the answer was wrong (with or without the right
    figure). Off the request thread unless SYNC_FOR_TESTS. Returns what
    was done when synchronous, else None."""
    if not enabled():
        return None
    try:
        idx, turn = wrong_turn(history, complaint)
        if turn is None:
            return None
        doc = None
        jid = str(_meta(turn).get('read_job_id') or _meta(turn).get('landed_job_id') or '')
        if jid and callable(job_doc_loader):
            try:
                doc = job_doc_loader(jid)
            except Exception:
                doc = None
        facts = _turn_facts(turn, doc)
        question = facts.get('question') or _prev_question(history, idx)
        stated = figures_in(complaint) if states_a_figure(complaint) else []
        return _run(_learn, username=username, thread_id=thread_id, history=history, wrong_text=str(turn.get('text') or ''),
                    correction_text=str(complaint or ''), question=question, facts=facts, by='reader',
                    kind='wrong_answer', view=view, stated=stated)
    except Exception:
        traceback.print_exc()
        return None


def learn_from_rejected_entry(username, thread_id, history, entry, *, view=''):
    """A stored answer the reader pushed back on in this thread (the replay
    guard found it): its words are set aside and the row retired, so the
    same words are not handed back on any later path either."""
    if not enabled() or not isinstance(entry, dict) or not entry.get('reply'):
        return None
    try:
        facts = {'subject': entry.get('subject') or '', 'window_start': entry.get('ws') or '', 'window_end': entry.get('we') or '',
                 'window_label': entry.get('wl') or '', 'question': entry.get('q') or '', 'figures': [], 'metrics': list(entry.get('metrics') or []),
                 'job_id': ''}
        complaint = ''
        turns = [t for t in (history or []) if isinstance(t, dict)]
        for t in reversed(turns):
            if t.get('role') == 'user':
                complaint = str(t.get('text') or '')[:300]
                break
        stated = figures_in(complaint) if states_a_figure(complaint) else []
        return _run(_learn, username=username, thread_id=thread_id, history=history, wrong_text=str(entry.get('reply') or ''),
                    correction_text=complaint, question=str(entry.get('q') or ''), facts=facts, by='reader',
                    kind='rejected_replay', view=view, stated=stated)
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------------------------------------ the staff helper
def _find_turn(history, *, turn_id='', job_id='', index=None, text_prefix=''):
    turns = [t for t in (history or []) if isinstance(t, dict)]
    if index is not None:
        try:
            i = int(index)
            if -len(turns) <= i < len(turns):
                return (i % len(turns)) if turns else None
        except (TypeError, ValueError):
            pass
    for i, t in enumerate(turns):
        m = _meta(t)
        if turn_id and str(t.get('turn_id') or '') == str(turn_id):
            return i
        if job_id and (m.get('read_job_id') == job_id or m.get('landed_job_id') == job_id
                       or (isinstance(m.get('read_job_chain'), list) and job_id in m['read_job_chain'])):
            return i
    if text_prefix:
        want = ' '.join(str(text_prefix).split()).lower()
        for i in range(len(turns) - 1, -1, -1):
            if turns[i].get('role') != 'user' and ' '.join(str(turns[i].get('text') or '').split()).lower().startswith(want):
                return i
    return None


class CorrectionHost:
    """What a staff correction reads and writes: the thread, the read job
    documents (a resumed chain included), the chain markers, the email
    side-files, the held records and the backup copies. The CLI builds one
    on S3 with the store's client hook installed; a test hands in a fake.
    Every method is optional; a missing one is a no-op."""

    def __init__(self, *, load_thread=None, save_thread=None, read_thread=None, restore_thread=None, job_doc=None, job_write=None,
                 chain_marker=None, chain_marker_write=None, notify_delete=None, backup=None,
                 held_list=None, held_get=None, held_write=None, registry_clear=None):
        self.load_thread = load_thread
        self.save_thread = save_thread
        self.read_thread = read_thread or load_thread
        self.restore_thread = restore_thread      # writes the backup copy back verbatim (the merge cannot undo a stamped correction)
        self.job_doc = job_doc
        self.job_write = job_write
        self.chain_marker = chain_marker
        self.chain_marker_write = chain_marker_write
        self.notify_delete = notify_delete
        self.backup = backup
        self.held_list = held_list
        self.held_get = held_get
        self.held_write = held_write
        self.registry_clear = registry_clear

    @classmethod
    def from_chat(cls):
        """The running app's own helpers (the thread merge-save, the job
        writer, the chain markers, the notify side-files)."""
        from prometheus.legacy import chat as c
        from prometheus.legacy import jobs as _jobs
        from prometheus.legacy import notify as _notify
        from prometheus import read_recovery as rr

        def _marker_write(jid, doc):
            s3, bucket = rr._s3()
            s3.put_object(Bucket=bucket, Key=f"{rr.CHAIN_PREFIX}{jid}.json", Body=json.dumps(doc).encode('utf-8'),
                          ContentType='application/json')

        return cls(load_thread=lambda u, t: c._pm_load_thread_or_active(u, t),
                   save_thread=lambda u, t, h: c._pm_save_thread_or_active(u, t, h),
                   job_doc=lambda j: c._pm_read_job_doc(j),
                   job_write=lambda j, d: _jobs._pm_read_status_write(j, d),
                   chain_marker=lambda j: rr.chain_marker(j),
                   chain_marker_write=_marker_write,
                   notify_delete=lambda j: _notify._pm_notify_delete(j))


HELD_LOOKBACK_DAYS = 14       # held records are filed by day; a correction looks back this far

# the wrong answer's chips, figures, links and marks come off: set EMPTY, not
# popped, because the thread merge unions meta (a popped key would ride back
# in from the stored copy)
_STALE_META_EMPTY = {'options': [], 'figures': [], 'link': '', 'chart': None, 'file_link': '', 'files': [], 'pdf_link': '',
                     'faulted': False, 'held_ref': '', 'drilldown': None}


def job_chain(job_id, host: CorrectionHost) -> list:
    """Every job id of a resumed read's chain, root first, through the
    chain markers (root) and the records (`resumed_as`). [job_id] for a
    read never resumed."""
    jid = str(job_id or '').strip()
    if not jid or not callable(host.job_doc):
        return [jid] if jid else []
    ids = []
    try:
        marker = host.chain_marker(jid) if callable(host.chain_marker) else {}
        cur, hops = str((marker or {}).get('root') or jid), 0
        while cur and cur not in ids and hops < 8:
            ids.append(cur)
            doc = host.job_doc(cur) or {}
            cur = str((doc or {}).get('resumed_as') or '').strip()
            hops += 1
    except Exception:
        traceback.print_exc()
    if jid not in ids:
        ids.append(jid)
    return ids


def resolve_subject(turn, job_doc, history, index, *, given='') -> tuple:
    """(subject, question, how) for a corrected turn: the subject staff
    gave; else the job document's own subject (its profile or base, the
    thread's pinned subject); else the subject the turn's OWN question
    names (`own_question`: the user turn right before it, never a
    neighbor's); else nothing. The question is the turn's own, else the
    job document's."""
    m = _meta(turn)
    jd = job_doc if isinstance(job_doc, dict) else {}
    pl = jd.get('payload') if isinstance(jd.get('payload'), dict) else {}
    question = own_question(history, index) or str(jd.get('question') or (jd.get('resume') or {}).get('text') or '')[:300]
    if str(given or '').strip():
        return str(given).strip(), question, 'given'
    for cand, how in ((pl.get('profile'), 'job profile'), (jd.get('subject'), 'job subject'), (m.get('subject'), 'turn subject'),
                      (((jd.get('resume') or {}).get('base') or {}).get('subject'), 'thread base'), ((jd.get('resume') or {}).get('bind_subject'), 'thread subject')):
        if str(cand or '').strip():
            return str(cand).strip(), question, how
    if question:
        try:
            from . import knowledge as _know
            named = re.sub(r"(?:'|\u2019)s$", '', str(_know.named_subject(question) or '').strip()).strip()
            if named:
                return named, question, 'own question'
        except Exception:
            pass
    return '', question, 'none'


def merge_check(before, after, corrected_index=None, new_text='') -> dict:
    """The correction survives a merge: the pre-correction copy merged
    into the corrected thread (the way a stale client copy would arrive)
    leaves the corrected text in place and adds no turn. {'ok', 'why'}."""
    try:
        from . import thread_merge as _tm
        merged = _tm.merge_turns(list(after or []), list(before or []))
        if len(merged) != len(after or []):
            return {'ok': False, 'why': f"a stale copy would add {len(merged) - len(after or [])} turn(s)"}
        if corrected_index is not None and 0 <= corrected_index < len(merged):
            if ' '.join(str(merged[corrected_index].get('text') or '').split()) != ' '.join(str(new_text or '').split()):
                return {'ok': False, 'why': 'a stale copy would write over the corrected text'}
            if not _meta(merged[corrected_index]).get('corrected_at'):
                return {'ok': False, 'why': 'the correction stamp would not survive a merge'}
        return {'ok': True, 'why': ''}
    except Exception as e:
        return {'ok': False, 'why': f"merge check did not run: {type(e).__name__}"}


def correct_turn(username, thread_id, new_text, *, by, turn_id='', job_id='', index=None, text_prefix='',
                 figures=None, subject='', window=None, note='', load=None, save=None, job_doc=None, job_write=None,
                 learn=True, view='', host: Optional[CorrectionHost] = None) -> dict:
    """Correct one thread turn, the one complete path a staff correction
    needs (Jenna 2026-11-23, after the defect found on Armine's thread):

    1. a backup of the thread (and of every job document the correction
       touches) first;
    2. the turn (found by turn_id, by the job id on it, by index, or by
       the words it starts with) takes `new_text`; its meta takes
       `corrected_at` and `landed_at` (the merge reads the stamp as the
       server's correction; a newer stamp wins), `corrected_by`,
       `retired` (the old words, for audit) and `server_note`; the stale
       follow-up chips, figures, links and held marks of the wrong answer
       come off (new `figures` ride on when given); the save is the
       thread merge (ETag-guarded, mirrored to the store), never a raw put;
    3. every read job document behind the turn, the resumed chain
       included, is settled through the house writer: the corrected reply
       and the stamp on the payload, status done and final, the fault
       cleared; the email-promise marker is turned off on every pass
       (the chain marker, the notify side-file, the open held records);
    4. the ledger retires the old words and sets them aside, scoped to
       this subject, window, seat and thread (never a house stock line,
       never another subject's row), banks `figures` with `new_text` as
       the decision of record for the subject (resolved from the turn's
       own question or the thread's own subject, never a neighbor turn)
       and queues the fixture candidate;
    5. a merge check against the backup before it returns: a stale copy
       of the thread merged in later cannot write over the correction; when
       it could, the thread is put back from the backup and the report
       says so.

    Returns a report dict; raises only on a turn that cannot be found."""
    if host is None:
        if load is None or save is None or job_doc is None or job_write is None:
            host = CorrectionHost.from_chat()
            host.load_thread = load or host.load_thread
            host.save_thread = save or host.save_thread
            host.job_doc = job_doc or host.job_doc
            host.job_write = job_write or host.job_write
            host.read_thread = host.load_thread
        else:
            host = CorrectionHost(load_thread=load, save_thread=save, job_doc=job_doc, job_write=job_write)
    by = str(by or 'staff').strip()
    by_label = by if by.startswith('staff') else f"staff:{by}"
    history = list(host.load_thread(username, thread_id) or [])
    before = json.loads(json.dumps(history))
    i = _find_turn(history, turn_id=turn_id, job_id=job_id, index=index, text_prefix=text_prefix)
    if i is None:
        raise LookupError('turn not found (give --turn-id, --job, --index or --starts-with)')
    report = {'thread_id': thread_id, 'index': i, 'turn_id': history[i].get('turn_id'), 'corrected_at': '', 'job_id': '', 'jobs_settled': [],
              'promise_off': [], 'held_closed': 0, 'backup': {}, 'subject': '', 'subject_from': '', 'learned': None, 'merge_check': None}
    # 1. the backup first
    if callable(host.backup):
        try:
            report['backup']['thread'] = host.backup('thread', f"{username}/{thread_id}", before) or True
        except Exception:
            traceback.print_exc()
    old = dict(history[i])
    old_text = str(old.get('text') or '')
    stamp = _now_iso()
    meta = dict(_meta(old))
    prior = str(meta.get('corrected_at') or '')
    if prior and prior >= stamp:
        # the merge keeps the newer correction: a second correction inside the
        # same second takes the next second so it is the newer one
        try:
            stamp = (datetime.strptime(prior, '%Y-%m-%dT%H:%M:%SZ') + timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        except Exception:
            pass
    report['corrected_at'] = stamp
    # 2. the turn: stamps on, the wrong answer's chips, figures and links off
    for k, empty in _STALE_META_EMPTY.items():
        if k in meta:
            meta[k] = empty
    stated = _parse_figures(figures)
    if stated:
        meta['figures'] = [{'value': x['value'], 'kind': ('pct' if x.get('unit') == 'pct' else 'money' if x.get('unit') == 'usd' else 'count'),
                            'label': str(x.get('label') or x.get('name') or 'figure')[:80], 'source': 'staff correction'} for x in stated]
    meta.update({'corrected_at': stamp, 'landed_at': stamp, 'corrected_by': by_label[:60], 'server_note': True, 'kind': meta.get('kind') or 'read',
                 'retired': old_text[:400], **({'correction_note': str(note)[:200]} if note else {})})
    new = dict(old, text=str(new_text), role='agent', meta=meta)
    history[i] = new
    saved = host.save_thread(username, thread_id, history)
    # 3. the job documents behind the turn, the chain included
    jid = str(meta.get('read_job_id') or meta.get('landed_job_id') or job_id or '')
    doc = None
    if jid and callable(host.job_doc):
        report['job_id'] = jid
        for cid in job_chain(jid, host):
            try:
                d = host.job_doc(cid)
                if not isinstance(d, dict):
                    continue
                if doc is None or cid == jid:
                    doc = d                      # the corrected turn's own record wins; the root stands in until it is read
                if callable(host.backup):
                    try:
                        host.backup('job', cid, d)
                    except Exception:
                        traceback.print_exc()
                pl = dict(d.get('payload') or {}) if isinstance(d.get('payload'), dict) else {}
                pl.update({'reply': str(new_text), 'corrected_at': stamp, 'retired_reply': str(pl.get('reply') or '')[:400] or old_text[:400],
                           'success': True, 'action': 'answer', 'followups': []})
                for k in ('faulted', 'held_ref', 'offer_deck', 'deck_angle', 'file_link', 'chart'):
                    pl.pop(k, None)
                settled = {**d, 'payload': pl, 'corrected_at': stamp, 'corrected_by': by_label[:60], 'status': 'done', 'final': True,
                           'final_reason': f"corrected by staff {stamp}"[:200], 'settled_by_correction': True}
                for k in ('fault', 'held_retries', 'claimed_by', 'claimed_at'):
                    settled.pop(k, None)
                try:
                    from . import seams as _seams
                    settled = _seams.tag_job(settled, 'read')      # the house writer's tag, whatever writer carries it
                except Exception:
                    pass
                if callable(host.job_write):
                    host.job_write(cid, settled)
                report['jobs_settled'].append(cid)
                # the email promise, off on every pass
                try:
                    if callable(host.chain_marker) and callable(host.chain_marker_write):
                        mk = host.chain_marker(cid)
                        if isinstance(mk, dict) and mk:
                            host.chain_marker_write(cid, {**mk, 'promised_email': False, 'corrected_at': stamp})
                    if callable(host.notify_delete):
                        host.notify_delete(cid)
                    report['promise_off'].append(cid)
                except Exception:
                    traceback.print_exc()
            except Exception:
                traceback.print_exc()
        try:
            if callable(host.registry_clear):
                host.registry_clear(username, str((doc or {}).get('question') or ''))
        except Exception:
            traceback.print_exc()
    # the open held records behind the promise close as delivered by a person
    try:
        if callable(host.held_list) and callable(host.held_get) and callable(host.held_write):
            from . import promise_truth as _pt
            chain = set(report['jobs_settled']) | ({jid} if jid else set())
            days = [(datetime.now(timezone.utc) - timedelta(days=n)).strftime('%Y-%m-%d') for n in range(HELD_LOOKBACK_DAYS)]
            keys = []
            for day in days:
                keys.extend(host.held_list(f"{_pt.HELD_PREFIX}{day}/") or [])
            for key in keys:
                hd = host.held_get(key)
                if not isinstance(hd, dict) or str(hd.get('status') or 'open') != 'open':
                    continue
                if str(hd.get('user') or '') != str(username or ''):
                    continue
                if (hd.get('job_id') and str(hd['job_id']) in chain) or (hd.get('thread_id') and str(hd['thread_id']) == str(thread_id)):
                    if _pt.close(key, how='corrected by staff', by=by_label[:60], job_id=jid, emailed=False, thread_id=str(thread_id or ''),
                                 getter=host.held_get, writer=host.held_write):
                        report['held_closed'] += 1
    except Exception:
        traceback.print_exc()
    # 4. the learning, scoped to this subject, window, seat and thread
    subj, question, how = resolve_subject(old, doc, before, i, given=subject)
    report['subject'], report['subject_from'] = subj, how
    if learn and enabled():
        facts = _turn_facts(old, doc)
        facts['subject'] = subj
        facts['question'] = question
        w = window if isinstance(window, dict) else {}
        if w.get('start') and w.get('end'):
            facts['window_start'], facts['window_end'] = str(w['start'])[:10], str(w['end'])[:10]
        report['learned'] = _learn(username=username, thread_id=thread_id, history=history[:i + 1], wrong_text=old_text,
                                   correction_text=str(note or 'corrected by staff'), question=question, facts=facts, by=by_label,
                                   kind='staff_correction', view=view, stated=stated, corrected_reply=str(new_text))
    # 5. the merge check against the backup
    try:
        after = list(host.read_thread(username, thread_id) or []) if callable(host.read_thread) else list(saved or history)
    except Exception:
        after = list(saved or history)
    chk = merge_check(before, after, corrected_index=i, new_text=str(new_text))
    report['merge_check'] = chk
    if not chk.get('ok'):
        try:
            if callable(host.restore_thread):
                host.restore_thread(username, thread_id, before)
            else:
                host.save_thread(username, thread_id, before)
            report['restored_from_backup'] = True
            print(f"[pm-corrections] correction on {username}/{thread_id} put back from the backup: {chk.get('why')}")
        except Exception:
            traceback.print_exc()
    return report


def _parse_figures(figures) -> list:
    """[{'value','unit','name','label'}] from dicts or 'name=value unit' strings."""
    out = []
    for f in (figures or []):
        if isinstance(f, dict) and f.get('value') is not None:
            try:
                out.append({'value': float(f['value']), 'unit': _unit_class(f.get('unit')), 'name': f.get('name'), 'label': f.get('label')})
            except (TypeError, ValueError):
                continue
        elif isinstance(f, str) and '=' in f:
            name, _, rest = f.partition('=')
            parts = rest.strip().split()
            try:
                val = float(parts[0].replace(',', '').replace('$', ''))
            except (ValueError, IndexError):
                continue
            unit = parts[1] if len(parts) > 1 else ('usd' if '$' in parts[0] else 'count')
            out.append({'value': val, 'unit': _unit_class(unit), 'name': _slug(name), 'label': name.strip().replace('_', ' ')})
    return out


def corrected_metrics_from(stated: list) -> list:
    """Named figures from `_parse_figures` as ledger metrics."""
    out = []
    for s_ in stated:
        klass = _unit_class(s_.get('unit'))
        unit = 'pct' if klass == 'pct' else ('usd' if klass == 'usd' else 'people')
        out.append({'name': s_.get('name') or _slug(s_.get('label')), 'label': str(s_.get('label') or s_.get('name') or 'Corrected figure')[:90],
                    'value': s_['value'], 'unit': unit, 'definition': 'corrected figure given by staff'})
    return out
