"""One record per thread: a save merges, nothing a client sends removes a turn.

Jenna, 2026-11-04: "the data should always be storing it so that it
reconciles and should always keep track."

A thread lives in S3 as a list of turns. Two parties write it: the
dashboard widget, which pushes its whole in-memory copy on every save,
and the server, which appends landings, notes, offers and deliveries
while the widget may not be looking. Until this module the save
REPLACED the stored list and only server-marked agent turns were folded
back in (keep_server_turns, 2026-10-16). A tab holding a stale copy (a
second tab, a page left open across a landing or a note, a thread the
rail moved under it) dropped every plain turn the server held. Jay
Patel, 2026-11-04: his own questions and three answers vanished from
his thread twice in one afternoon.

The rule now, for every writer:

* The stored list is the base. Every stored turn survives a save.
* A client may ADD turns and PATCH the meta of a turn it holds (a
  build status, a chosen option). It never removes one. Deleting a
  thread is its own endpoint (threads/delete), never a shorter list.
* Identity is the client's turn_id when both copies carry one, else the
  same moment and role with the same text. The same moment and role
  with a different text is still one turn when one side is the server's
  correction (meta.corrected_at), the server's own turn, or a blank the
  server filled: two different turns never share a millisecond and a
  role. A correction keeps its text; a newer correction replaces an
  older one; a server turn keeps its text; a client's own turn takes
  the client's text.
* Exact repeats (same moment, role and text) collapse to one turn with
  the union of their meta.
* Order is by timestamp, stable; a turn with no usable stamp keeps its
  place at the end.
* The write is guarded by the object's ETag: a writer that lands between
  the read and the write means a re-read and a re-merge, never a lost
  turn (merge_save).

Pure functions; no Flask, no host. prometheus.legacy.threads and
prometheus.service call in. Regression: scripts/test_pm_thread_history_merge.py.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import datetime

# Marks only the server writes onto a turn (a landing, a delivery, a note,
# a correction). The widget pulls these in; it never mints them.
SERVER_TURN_MARKS = ('corrected_at', 'read_job_id', 'deck_job_id', 'jiq_job_id', 'bpiq_job_id', 'fw_job_id',
                     'aiq_job_id', 'tlist_job_id', 'files', 'server_note', 'delivery')
CALM_LINE = "Working on it. Confirmed your task is in progress and I will email you when it completes."
MAX_TURNS = 200
_TERMINAL = ('complete', 'error', 'failed')


def turn_epoch(t):
    v = (t or {}).get('ts') if isinstance(t, dict) else None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0


def _meta(t):
    m = t.get('meta') if isinstance(t, dict) else None
    return m if isinstance(m, dict) else {}


def is_server_turn(t):
    return isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant') \
        and any(_meta(t).get(k) for k in SERVER_TURN_MARKS)


def fill_blank_agent_turns(turns, line=CALM_LINE):
    """No agent turn is stored blank (2026-10-22, Casey Pearson: the
    widget saved {role: 'agent', ts} after a draft reached its analyze
    handler). A blank agent turn with nothing attached becomes the calm
    line (the widget's own), marked so the next pass can tell it apart
    from an answer."""
    try:
        out = []
        for t in (turns or []):
            if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant') \
                    and not str(t.get('text') or '').strip():
                meta = _meta(t)
                if not any(meta.get(k) for k in ('link', 'chart', 'files', 'options')):
                    t = dict(t, text=line, meta=dict(meta, blank_filled=True))
            out.append(t)
        return out
    except Exception:
        return turns


def text_key(text):
    return ' '.join(str(text or '').split())


def stamp(t):
    """(moment, role): the moment as an epoch rounded to the millisecond
    when the timestamp parses (so '19:31:01Z' and '19:31:01.000Z' are one
    moment), else the raw string."""
    e = turn_epoch(t)
    ts = round(e, 3) if e else str((t or {}).get('ts') or '')
    return (ts, str((t or {}).get('role') or ''))


def same_turn(a, b):
    """0 when a and b are different turns, 2 when they are the same turn
    (one id, or one moment, role and text), 1 when they share a moment and
    a role with different texts and one side is a correction, a server
    turn or a filled blank (the same turn, two readings)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0
    ia, ib = a.get('turn_id'), b.get('turn_id')
    if ia and ib and str(ia) == str(ib):
        return 2
    if stamp(a) != stamp(b):
        return 0
    if text_key(a.get('text')) == text_key(b.get('text')):
        return 2
    if ia and ib:
        return 0          # two ids, two texts: two turns
    ma, mb = _meta(a), _meta(b)
    if ma.get('corrected_at') or mb.get('corrected_at') or ma.get('blank_filled') or mb.get('blank_filled') \
            or is_server_turn(a) or is_server_turn(b):
        return 1
    return 0


def _find(out, c):
    best, grade = None, 0
    for j, s in enumerate(out):
        g = same_turn(s, c)
        if g == 2:
            return j
        if g > grade:
            best, grade = j, g
    return best


def _resolve(base, inc):
    """One turn from its two readings. The server's correction and the
    server's own turn keep their text and marks; a newer correction
    replaces an older one; a client's own turn takes the client's text.
    Meta is the union: the client's patch wins on its own turn, the
    server's mark wins on the server's, and a build status never goes
    back from done (one tab's poller may lag another's)."""
    bm, im = _meta(base), _meta(inc)
    bc, ic = str(bm.get('corrected_at') or ''), str(im.get('corrected_at') or '')
    if ic and ic > bc:
        out, meta = dict(inc), dict(bm)
        meta.update(im)
    elif bc or is_server_turn(base):
        out, meta = dict(base), dict(im)
        meta.update(bm)
    else:
        out, meta = dict(inc), dict(bm)
        meta.update(im)
    bs, is_ = str(bm.get('status') or '').lower(), str(im.get('status') or '').lower()
    if bs in _TERMINAL and is_ not in _TERMINAL:
        meta['status'] = bm['status']
    elif is_ in _TERMINAL and bs not in _TERMINAL:
        meta['status'] = im['status']
    tid = base.get('turn_id') or inc.get('turn_id')
    if tid:
        out['turn_id'] = tid
    if meta:
        out['meta'] = meta
    return out


def _collapse(turns):
    """Exact repeats (one moment, role and text, or one id) become one
    turn carrying the union of their meta."""
    out, seen = [], {}
    for t in turns:
        if not isinstance(t, dict):
            continue
        keys = [('k',) + stamp(t) + (text_key(t.get('text')),)]
        if t.get('turn_id'):
            keys.append(('i', str(t['turn_id'])))
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit is None:
            out.append(t)
            for k in keys:
                seen[k] = len(out) - 1
            continue
        out[hit] = _resolve(out[hit], t)
    return out


def _ordered(turns):
    keyed = [(turn_epoch(t) or float('inf'), n, t) for n, t in enumerate(turns)]
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [t for _e, _n, t in keyed]


def merge_turns(stored, incoming):
    """The stored thread with the incoming turns merged in. Every stored
    turn survives; incoming turns are added or patch the copy already
    held; exact repeats collapse; the result is in time order. Never
    raises: on any doubt the stored list plus the incoming list, as given."""
    inc = [dict(t) for t in (incoming or []) if isinstance(t, dict)] if isinstance(incoming, list) else []
    base = [dict(t) for t in (stored or []) if isinstance(t, dict)] if isinstance(stored, list) else []
    try:
        inc = fill_blank_agent_turns(inc)
        out = list(base)
        for c in inc:
            j = _find(out, c)
            if j is None:
                out.append(c)
            else:
                out[j] = _resolve(out[j], c)
        return _ordered(_collapse(out))
    except Exception:
        return base + [c for c in inc if c not in base]


def keys_of(turns):
    return [stamp(t) + (text_key(t.get('text')),) for t in (turns or []) if isinstance(t, dict)]


def differs(incoming, merged):
    """True when the merged thread is not the list the client sent (the
    client lacked turns the server held, or sent a repeat): the client
    should take the merged copy."""
    try:
        return keys_of(fill_blank_agent_turns([t for t in (incoming or []) if isinstance(t, dict)])) != keys_of(merged)
    except Exception:
        return True


def thread_rev(turns):
    """A short revision tag for a thread: changes whenever a turn is
    added, corrected, re-texted or moved to a done status. The widget
    compares it to skip a merge it does not need."""
    h = hashlib.sha1()
    for t in (turns or []):
        if not isinstance(t, dict):
            continue
        m = _meta(t)
        h.update(('%s|%s|%s|%s|%s|%s\n' % (
            t.get('turn_id') or '', t.get('ts') or '', t.get('role') or '',
            hashlib.sha1(text_key(t.get('text')).encode('utf-8')).hexdigest()[:12],
            m.get('corrected_at') or '', m.get('status') or '')).encode('utf-8'))
    return h.hexdigest()[:16]


# ------------------------------------------------------------ S3 writes

_SUPPORTS_CONDITIONAL_PUT = True


def _missing(err):
    s = str(err)
    code = ''
    try:
        code = str(((getattr(err, 'response', None) or {}).get('Error') or {}).get('Code') or '')
    except Exception:
        code = ''
    return code in ('NoSuchKey', '404', 'NotFound') or 'NoSuchKey' in s


def _precondition_failed(err):
    try:
        r = getattr(err, 'response', None) or {}
        code = str((r.get('Error') or {}).get('Code') or '')
        status = (r.get('ResponseMetadata') or {}).get('HTTPStatusCode')
        return code in ('PreconditionFailed', '412') or status == 412
    except Exception:
        return False


def read_turns(s3, bucket, key):
    """(turns, etag) for a stored thread; ([], None) when there is none."""
    try:
        r = s3.get_object(Bucket=bucket, Key=key)
    except Exception as e:
        if _missing(e):
            return [], None
        raise
    body = r['Body'].read()
    if isinstance(body, bytes):
        body = body.decode('utf-8')
    try:
        obj = json.loads(body) if str(body).strip() else []
    except Exception:
        obj = []
    etag = str(r.get('ETag') or '').strip('"') or None
    return (obj if isinstance(obj, list) else []), etag


def _put(s3, bucket, key, turns, etag):
    """True when written, False when the object changed under us."""
    global _SUPPORTS_CONDITIONAL_PUT
    kw = dict(Bucket=bucket, Key=key, Body=json.dumps(turns, indent=2).encode('utf-8'),
              ContentType='application/json')
    if _SUPPORTS_CONDITIONAL_PUT:
        guarded = dict(kw, **({'IfMatch': etag} if etag else {'IfNoneMatch': '*'}))
        try:
            s3.put_object(**guarded)
            return True
        except Exception as e:
            if type(e).__name__ == 'ParamValidationError' or 'Unknown parameter' in str(e):
                _SUPPORTS_CONDITIONAL_PUT = False     # older botocore: unguarded from here on
            elif _precondition_failed(e):
                return False
            else:
                raise
    s3.put_object(**kw)
    return True


def merge_save(s3, bucket, key, incoming, *, trim=MAX_TURNS, retries=4):
    """Read the stored thread, merge the incoming turns in, write it back
    guarded by the ETag. Another writer landing between the read and the
    write means a re-read and a re-merge. Returns the list written."""
    merged = []
    for attempt in range(retries + 1):
        stored, etag = read_turns(s3, bucket, key)
        merged = merge_turns(stored, incoming)
        if trim and len(merged) > trim:
            merged = merged[-trim:]
        if _put(s3, bucket, key, merged, etag):
            return merged
        time.sleep(min(0.15 * (2 ** attempt), 2.0) + random.uniform(0, 0.1))
    # every attempt raced another writer: merge once more over what is there now and write
    stored, _etag = read_turns(s3, bucket, key)
    merged = merge_turns(stored, incoming)
    if trim and len(merged) > trim:
        merged = merged[-trim:]
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(merged, indent=2).encode('utf-8'),
                  ContentType='application/json')
    return merged
