"""A check with real people (Digital Twins IQ, 2026-11-17).

A client can ask for any room or pilot test to be put to real people as
well: the same questions, to people recruited to the audience's own
shape, delivered as a second column beside the twins' answers. Three
reasons it exists: a go or no-go decision wants it; a check with people
is how the twins get better (every pair of columns is a scored row on the
outcome ledger); and it is a line item.

The flow
  1. ``request_check`` writes the request (the questions as asked, the
     twins' split on each, the audience and its cuts, the people the room
     stands for, the quote) under system/digital_twins/validation/<id>.json,
     notes it on the session (``checks``), and mails the team (the
     recruit and the study are run by hand; nothing here recruits).
  2. ``ingest_results`` takes the study's answers per question (rows of
     label and share, and how many people answered), writes them on the
     request and beside each question on the session as ``people_check``
     ({n, rows, gap_pp}), and records the outcome on the ledger's
     room_count row for the question (source class 'people study'), so
     the firmness layer learns from it.
  3. The page, the exports and the chat read ``people_check`` beside the
     twins' split as a second column headed 'People'.

The quote is QUOTE_USD (the team can set another figure on the request);
nothing is charged here: the study is fulfilled by hand and billed when
it is delivered. Never raises into a request.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

S3_BUCKET = 'dashboard-inputs'
PREFIX = 'system/digital_twins/validation/'
QUOTE_USD = 7500.0
VERSION = 1
MAX_QUESTIONS = 12


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _key(vid: str) -> str:
    return f"{PREFIX}{vid}.json"


def load(s3, vid: str) -> Optional[dict]:
    if s3 is None:
        return None
    try:
        raw = s3.get_object(Bucket=S3_BUCKET, Key=_key(vid))['Body'].read()
        doc = json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _save(s3, doc: dict) -> bool:
    if s3 is None:
        return False
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=_key(doc['id']), Body=json.dumps(doc, ensure_ascii=False, indent=1).encode('utf-8'), ContentType='application/json')
        return True
    except Exception as e:
        print(f"[twin-check] write failed: {str(e)[:120]}")
        return False


def _questions_of(sess: dict, ids: Optional[list] = None) -> list:
    out = []
    for q in (sess or {}).get('questions') or []:
        if not isinstance(q, dict) or str(q.get('kind') or '') in ('probe', 'rating'):
            continue
        if ids and str(q.get('id')) not in {str(i) for i in ids}:
            continue
        rows = q.get('distribution') or [{'label': o.get('label'), 'pct': o.get('pct'), 'people': o.get('people')} for o in (q.get('options') or []) if isinstance(o, dict)]
        out.append({'id': str(q.get('id') or ''), 'q': str(q.get('q') or '')[:400], 'kind': str(q.get('kind') or ''),
                    'twins': [{'label': r.get('label'), 'pct': r.get('pct'), 'people': r.get('people')} for r in rows if isinstance(r, dict)],
                    'firmness': ((q.get('firmness') or {}).get('word') if isinstance(q.get('firmness'), dict) else None)})
        if len(out) >= MAX_QUESTIONS:
            break
    return out


def request_check(s3, payload: dict, sess: dict, *, username: str = '', question_ids: Optional[list] = None, note: str = '', quote_usd: Optional[float] = None,
                  notify: bool = True) -> dict:
    """The request record, noted on the session, the team mailed."""
    m = (payload or {}).get('meta') or {}
    g = (sess or {}).get('group') or {}
    vid = 'chk_' + uuid.uuid4().hex[:10]
    doc = {'id': vid, 'version': VERSION, 'status': 'requested', 'requested_at': _now(), 'requested_by': str(username or ''),
           'twin_key': str(m.get('key') or ''), 'twin': str(m.get('name') or m.get('subject') or ''), 'session_id': str((sess or {}).get('id') or ''),
           'session_kind': 'pilot' if (sess or {}).get('scorecard') is not None or (sess or {}).get('mode') else 'panel', 'session_title': str((sess or {}).get('title') or ''),
           'audience': {'people': g.get('size_people'), 'segments': [{'label': s.get('label'), 'people': s.get('people'), 'composition': s.get('composition') or ''} for s in (g.get('segments') or []) if isinstance(s, dict)],
                        'seats': len(g.get('roster') or [])},
           'questions': _questions_of(sess, question_ids), 'note': str(note or '')[:600], 'quote_usd': float(quote_usd if quote_usd is not None else QUOTE_USD),
           'results': None, 'completed_at': None}
    _save(s3, doc)
    if isinstance(sess, dict):
        checks = [c for c in (sess.get('checks') or []) if isinstance(c, dict)]
        checks.append({'id': vid, 'status': 'requested', 'requested_at': doc['requested_at'], 'questions': len(doc['questions']), 'quote_usd': doc['quote_usd']})
        sess['checks'] = checks
        sess['updated_at'] = _now()
    if notify:
        try:
            from prometheus.twin_notify import notify_check_requested
            notify_check_requested(payload, sess, doc, username=username, s3=s3)
        except Exception as e:
            print(f"[twin-check] notice skipped: {str(e)[:120]}")
    return doc


def public_request(doc: Optional[dict]) -> Optional[dict]:
    if not isinstance(doc, dict):
        return None
    return {k: doc.get(k) for k in ('id', 'status', 'requested_at', 'completed_at', 'session_id', 'session_title', 'questions', 'quote_usd', 'note', 'results')}


def _norm_rows(rows) -> list:
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get('label') is None:
            continue
        try:
            out.append({'label': str(r['label']), 'pct': round(float(r.get('pct') or 0), 1)})
        except (TypeError, ValueError):
            continue
    return out


def ingest_results(s3, payload: dict, sess: dict, vid: str, results: dict, *, n_people: int = 0, note: str = '', doc: Optional[dict] = None) -> Optional[dict]:
    """The study's answers onto the request and the session, and onto the
    ledger. ``results`` is {question id: [{label, pct}]}. Returns the
    request record, or None when it is not on file."""
    doc = doc or load(s3, vid)
    if not isinstance(doc, dict):
        return None
    from migration import digital_twin_ledger as LG
    by_q = {str(q.get('id')): q for q in (sess or {}).get('questions') or [] if isinstance(q, dict)}
    out_results = {}
    for qid, rows in (results or {}).items():
        rows = _norm_rows(rows)
        if not rows:
            continue
        q = by_q.get(str(qid))
        twin_rows = []
        if q is not None:
            twin_rows = [{'label': r.get('label'), 'pct': r.get('pct')} for r in (q.get('distribution') or []) if isinstance(r, dict)]
        gap = LG.tvd(twin_rows, rows) if twin_rows else None
        out_results[str(qid)] = {'rows': rows, 'n': int(n_people or 0), 'gap_pp': gap}
        if q is not None:
            q['people_check'] = {'n': int(n_people or 0), 'rows': rows, 'gap_pp': gap, 'check_id': vid}
            try:
                subject = str(((payload or {}).get('meta') or {}).get('subject') or ((payload or {}).get('meta') or {}).get('name') or '')
                lead = max(twin_rows, key=lambda r: float(r.get('pct') or 0)) if twin_rows else None
                rid = LG.record_id('room_count', subject, str((sess or {}).get('id') or ''), str(qid), f"share:{str((lead or {}).get('label') or '')[:60]}")
                LG.record_outcome(s3, rid, distribution=rows, source_class='people study', note=str(note or '')[:200])
            except Exception as e:
                print(f"[twin-check] ledger outcome skipped: {str(e)[:120]}")
    doc['results'] = out_results
    doc['status'] = 'complete' if out_results else doc.get('status')
    doc['completed_at'] = _now() if out_results else None
    doc['results_note'] = str(note or '')[:400]
    _save(s3, doc)
    if isinstance(sess, dict):
        for c in sess.get('checks') or []:
            if isinstance(c, dict) and c.get('id') == vid:
                c['status'] = doc['status']
                c['completed_at'] = doc['completed_at']
        sess['updated_at'] = _now()
    return doc


def check_lines(q: dict) -> list:
    """The 'People' column as plain lines for the chat and the exports:
    'People: Yes 41.3%, No 58.7% (n = 412); the twins were 3.1 points
    off.'"""
    pc = (q or {}).get('people_check') if isinstance((q or {}).get('people_check'), dict) else None
    if not pc or not pc.get('rows'):
        return []
    parts = ', '.join(f"{r['label']} {float(r['pct']):.1f}%" for r in pc['rows'][:6])
    line = f"People: {parts}" + (f" (n = {int(pc['n']):,})" if pc.get('n') else '') + '.'
    if pc.get('gap_pp') is not None:
        line += f" The twins were {float(pc['gap_pp']):.1f} points off."
    return [line]


__all__ = ['QUOTE_USD', 'VERSION', 'MAX_QUESTIONS', 'request_check', 'ingest_results', 'load', 'public_request', 'check_lines']
