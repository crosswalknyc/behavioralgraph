"""One valuation deck in the product (Jenna 2026-11-11, Samantha Kolton at CAA).

A Brand Partnership valuation paid for in chat used to end on a line
that pointed at the Brand Partnership tab, and a deck asked for in the
thread afterwards was laid out by the insights deck builder from the
words of the chat. The tab's own export was the only place the
valuation deck (`migration/bpiq_deck_builder.py`) was ever built. Three
ways to order one deck, two different decks.

Every way now goes through the one builder:

- `deliver(job_id, payload, host=...)`: the deck a finished valuation
  hands over. Built from the valuation's own file, finished by the
  design pack inside the builder (fonts embedded and checked, lint, the
  plain-English check), stored under the job's own folder and signed.
- `note(payload, deck, charge)`: the line the thread gets. The total,
  to the cent, and that the deck is ready; the charge on its own line.
  Nothing about how anything was made.
- `thread_valuation(history, text, pick)`: the valuation a deck asked
  for in a thread is on (the note that handed one over, when the words
  are about it), so "turn this into a deck" after a valuation is the
  valuation deck again, never a second deck from the chat's words.
- `start_reexport(...)`: that deck, built again from the saved file.
- `running_valuation(...)`: the valuation still running on a thread a
  deck was asked of. The answer is one line (`running_line`): it is
  running and the deck comes with it. No second order is walked.

The tab's export (`migration/bpiq_deck_export.py`) already calls the
same builder, so chat, the tab and a re-export are one deck.

User-facing copy is Prometheus first person. No em dashes. Never raises
into a job or a request.
"""
from __future__ import annotations

import copy
import io
import json
import re
import threading
import time
import traceback
import uuid
import zipfile

DECK_PREFIX = 'generated_decks/'
RESULT_PREFIX = 'brand-partnership-iq/'
LINK_LABEL = 'Download the deck'
LINK_SECONDS = 7 * 24 * 3600
PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
INCLUDED_LINE = 'Included with your plan.'
_INTERNAL_KEYS = ('_bpiq_category', '_category_for_deck', 'delivered_read')


def money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ''


def deck_data(payload):
    """The valuation as the deck builder reads it: the saved file's own
    fields, the engine's working keys off. The input is not changed."""
    d = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    for k in _INTERNAL_KEYS:
        d.pop(k, None)
    return d


def page_count(pptx_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as z:
            return sum(1 for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    except Exception:
        return 0


def build(payload, *, category=None, image_url=None, s3_client=None):
    """(bytes, report, filename) from the one valuation deck builder.
    Raises when the builder does; `deliver` is the door that never does."""
    from migration.bpiq_deck_builder import build_deck_report, suggested_filename
    data = deck_data(payload)
    body, report = build_deck_report(data, image_url=image_url, category=category, s3_client=s3_client)
    return body, report, suggested_filename(data, ext='pptx')


def _s3_of(host):
    s3 = getattr(host, 's3_client', None)
    bucket = getattr(host, 'S3_BUCKET', None) or getattr(host, 'bucket', None) or 'dashboard-inputs'
    return s3, bucket


def sidecar(host, bare_key):
    """(image_url, category) the tab's export would use for this file."""
    try:
        meta = (host.load_bpiq_metadata() or {}).get(str(bare_key or '')) or {}
        return (str(meta.get('image_url') or '').strip() or None), (str(meta.get('category') or '').strip() or None)
    except Exception:
        return None, None


def sign(s3, bucket, key, filename):
    return s3.generate_presigned_url(
        'get_object', Params={'Bucket': bucket, 'Key': key,
                              'ResponseContentDisposition': f'attachment; filename="{filename}"'},
        ExpiresIn=LINK_SECONDS)


def deliver(job_id, payload, *, host, bare_key='', category=None, image_url=None, keep_bytes=True):
    """Build the valuation deck, store it under the job and sign it.
    Returns {'s3_key', 'url', 'filename', 'title', 'slides', 'ready',
    'bytes'} or {} when it could not be built or stored. Never raises."""
    try:
        s3, bucket = _s3_of(host)
        if s3 is None or not job_id or not isinstance(payload, dict) or not hasattr(s3, 'generate_presigned_url'):
            return {}
        img, cat = sidecar(host, bare_key) if bare_key else (None, None)
        body, report, fname = build(payload, category=(category or cat), image_url=(image_url or img), s3_client=s3)
        if not body:
            return {}
        key = f"{DECK_PREFIX}{job_id}/{fname}"
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=PPTX_MIME)
        url = sign(s3, bucket, key, fname)
        subject = str(payload.get('project_name') or '').strip()
        ready = getattr(report, 'ready', None) if report is not None else None
        print(f"[bpiq-deck {job_id}] {key} ({page_count(body)} pages, ready={ready})")
        out = {'s3_key': key, 'url': url, 'filename': fname, 'slides': page_count(body), 'ready': ready,
               'title': (f"{subject} valuation" if subject else 'Brand Partnership valuation')}
        if keep_bytes:
            out['bytes'] = body
        return out
    except Exception:
        traceback.print_exc()
        return {}


def public(deck):
    """The deck record without its bytes (what a status document and a
    thread turn may carry)."""
    return {k: v for k, v in (deck or {}).items() if k != 'bytes'}


# ------------------------------------------------------------------ copy
def charge_line(charge):
    """'You were charged $500 once for this valuation.' from what the
    Accept recorded; the plan line for an included seat; '' when the
    order did not record one."""
    c = charge if isinstance(charge, dict) else {}
    if c.get('included'):
        return INCLUDED_LINE
    label = str(c.get('label') or '').strip()
    return f"You were charged {label} once for this valuation." if label else ''


def note(payload, deck=None, charge=None):
    """The thread line that hands a finished valuation over: the total
    and the deck, then the charge on its own line."""
    p = payload if isinstance(payload, dict) else {}
    subject = str(p.get('project_name') or '').strip() or 'the partnership'
    total = money((p.get('valuation') or {}).get('total_brand_value'))
    line = f"Your Brand Partnership valuation is ready: {subject}"
    line += f", total brand value {total}." if total else '.'
    if deck and deck.get('url'):
        line += " The deck is below, and the link is good for 7 days."
    else:
        line += " It is in the Brand Partnership tab now."
    paid = charge_line(charge)
    return f"{line}\n{paid}" if paid else line


def turn_meta(job_id, payload, bare_key, deck=None):
    subject = str((payload or {}).get('project_name') or '').strip()
    meta = {'bpiq_job_id': job_id, 'kind': 'bpiq', 'subject': subject[:120], 's3_key': bare_key}
    if deck and deck.get('url'):
        meta['link'] = {'url': deck['url'], 'label': LINK_LABEL}
        meta['deck_s3_key'] = deck.get('s3_key')
        meta['files'] = [deck.get('filename') or 'deck.pptx']
    return meta


def status_fields(payload, deck=None, charge=None):
    """What the job's finished status document carries for the widget."""
    out = {'note': note(payload, deck, charge)}
    if deck and deck.get('url'):
        out['link'] = {'url': deck['url'], 'label': LINK_LABEL}
        out['deck'] = public(deck)
    return out


# -------------------------------------------- a deck asked for in a thread
def _meta(turn):
    m = (turn or {}).get('meta') if isinstance(turn, dict) else None
    return m if isinstance(m, dict) else {}


def _is_agent(turn):
    return isinstance(turn, dict) and str(turn.get('role') or '') in ('agent', 'assistant')


def valuation_turn(history):
    """(index, turn) of the newest note that handed a valuation over in
    this thread, or (None, None)."""
    hist = list(history or [])
    for i in range(len(hist) - 1, -1, -1):
        t = hist[i]
        m = _meta(t)
        if _is_agent(t) and m.get('bpiq_job_id') and (m.get('kind') == 'bpiq' or m.get('s3_key')):
            return i, t
    return None, None


_VALUATION_WORD_RX = re.compile(r"\bvaluations?\b", re.I)


def thread_valuation(history, text='', pick=''):
    """The valuation a deck asked for in this thread is on, or None:
    {'job_id', 'subject', 's3_key'}. It is the valuation when the words
    (or the subject picked) name its pairing or say valuation, or when
    they point at the thread ("turn this into a deck") and the note
    that handed the valuation over is the last thing delivered here."""
    try:
        from prometheus import partnership_value as _pv
        from prometheus import deck_pick as _dp
        i, turn = valuation_turn(history)
        if turn is None:
            return None
        m = _meta(turn)
        subject = str(m.get('subject') or '').strip()
        pair = _dp.pair_parts(subject) or ()
        hit = {'job_id': str(m.get('bpiq_job_id')), 'subject': subject, 's3_key': str(m.get('s3_key') or '')}
        p = ' '.join(str(pick or '').split())
        if p:
            return hit if pair and not _pv._names_other_subject(p, pair) else None
        t = ' '.join(str(text or '').split())
        if not t or not _pv.deck_ask(t) or _pv._names_other_subject(t, pair):
            return None
        # the valuation by name: the word itself, or both sides of the pairing (one name alone may be
        # that side's own audience)
        named = bool(_VALUATION_WORD_RX.search(t)) or (
            len(pair) == 2 and all(_pv._fold(x) and _pv._fold(x) in _pv._fold(t) for x in pair))
        if named:
            return hit
        later_read = any(_pv._is_read_turn(x) for x in list(history or [])[i + 1:])
        return None if later_read else hit
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------- a deck asked for while the valuation runs
RUNNING_STATES = ('queued', 'running', 'researching', 'building', 'started')
RUNNING_FRESH_SECONDS = 2 * 3600
JUST_FINISHED_SECONDS = 10 * 60


def running_line(subject=''):
    """The whole answer to a deck asked for while its valuation runs."""
    s = ' '.join(str(subject or '').split())
    return (f"The {s} valuation is still running, and the deck comes with it when it finishes." if s
            else "The valuation is still running, and the deck comes with it when it finishes.")


def finished_line(subject=''):
    """A deck asked for in the moment between the valuation finishing
    and its note reaching the thread."""
    s = ' '.join(str(subject or '').split())
    return (f"The {s} valuation has just finished, and its deck is arriving in this thread now." if s
            else "The valuation has just finished, and its deck is arriving in this thread now.")


def _job_doc(job_id, g):
    try:
        s3, bucket = _s3_of(_cap(g, '_H'))
        if s3 is None or not re.fullmatch(r"[0-9a-f]{12}", str(job_id or '')):
            return None
        prefix = _cap(g, '_PM_BPIQ_JOB_PREFIX', 'system/pm_bpiq_jobs/')
        doc = json.loads(s3.get_object(Bucket=bucket, Key=f"{prefix}{job_id}.json")['Body'].read().decode('utf-8'))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _age_seconds(stamp):
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


def running_valuation(history, text='', pick='', *, marker=None, g=None):
    """The valuation this thread's last Accept started, when a deck is
    asked of it before it has been handed over: {'job_id', 'subject',
    'done'} or None. `marker` is the run marker that Accept left (the
    job id it owns and the pairing). 'done' is True when the job has
    finished and its note has not reached the thread yet (the caller
    builds the valuation deck from the saved file); otherwise the
    valuation is still running and the answer is `running_line`.

    None when the thread already holds the hand-over note for that job
    (`thread_valuation` answers then), when the job failed, when the
    words or the picked subject name something else, or when a read
    landed after the Accept and the words do not name the valuation."""
    try:
        from prometheus import partnership_value as _pv
        from prometheus import deck_pick as _dp
        m = marker if isinstance(marker, dict) else {}
        job_id = str(m.get('job_id') or '')
        if not job_id:
            return None
        hist = list(history or [])
        _i, note_turn = valuation_turn(hist)
        if note_turn is not None and str(_meta(note_turn).get('bpiq_job_id')) == job_id:
            return None
        doc = _job_doc(job_id, g) or {}
        status = str(doc.get('status') or '').strip().lower()
        if status in ('error', 'failed', 'refunded', 'cancelled', 'canceled'):
            return None
        done = status == 'done' and bool(str(doc.get('s3_key') or '').strip())
        if not done:
            # "still running" is only said of a run that started recently: the job's record says so,
            # or (no record to read) the Accept itself is the evidence. A run left in a running
            # state for hours is not called running.
            age = _age_seconds(m.get('started_at'))
            if (status and status not in RUNNING_STATES) or age is None or age > RUNNING_FRESH_SECONDS:
                return None
        talent = ' '.join(str(m.get('talent') or '').split())
        campaign = ' '.join(str(m.get('campaign') or '').split())
        subject = ' '.join(str(doc.get('subject') or '').split()) or campaign or talent
        pair = tuple(_dp.pair_parts(subject) or _dp.pair_parts(campaign) or ())
        hit = {'job_id': job_id, 'subject': subject, 'done': done, 's3_key': str(doc.get('s3_key') or '')}
        if done:
            try:
                hit['finished_seconds_ago'] = max(0.0, time.time() - float(doc.get('finished_at')))
            except (TypeError, ValueError):
                hit['finished_seconds_ago'] = None
        p = ' '.join(str(pick or '').split())
        if p:
            return hit if pair and not _pv._names_other_subject(p, pair) else None
        t = ' '.join(str(text or '').split())
        if not t or not _pv.deck_ask(t) or (pair and _pv._names_other_subject(t, pair)):
            return None
        named = bool(_VALUATION_WORD_RX.search(t)) or (
            len(pair) == 2 and all(_pv._fold(x) and _pv._fold(x) in _pv._fold(t) for x in pair))
        if named:
            return hit
        # "turn this into a deck": the valuation, unless a read landed in the thread after the Accept
        start = max((i for i, x in enumerate(hist) if _is_agent(x) and (
            str(_meta(x).get('bpiq_job_id') or '') == job_id
            or str(x.get('text') or '').startswith("It's running"))), default=-1)
        later_read = any(_pv._is_read_turn(x) for x in hist[start + 1:]) if start >= 0 else False
        return None if later_read else hit
    except Exception:
        traceback.print_exc()
        return None


def _cap(g, name, default=None):
    try:
        v = (g or {}).get(name)
        return v if v is not None else default
    except Exception:
        return default


def owned_file(job_id, user, g):
    """(bare key, job document) of the valuation this job delivered,
    read from the job's own record and only for its owner. (None, None)
    otherwise. The key is never taken from the words of a thread."""
    try:
        host = _cap(g, '_H')
        s3, bucket = _s3_of(host)
        if s3 is None or not re.fullmatch(r"[0-9a-f]{12}", str(job_id or '')):
            return None, None
        prefix = _cap(g, '_PM_BPIQ_JOB_PREFIX', 'system/pm_bpiq_jobs/')
        doc = json.loads(s3.get_object(Bucket=bucket, Key=f"{prefix}{job_id}.json")['Body'].read().decode('utf-8'))
        owner_ok = _cap(g, '_pm_job_owner_ok')
        if not callable(owner_ok):
            from prometheus.legacy.jobs import _pm_job_owner_ok as owner_ok
        if not owner_ok(doc.get('user'), user if isinstance(user, dict) else {'username': str(user or '')}):
            return None, None
        bare = str(doc.get('s3_key') or '').strip()
        if str(doc.get('status') or '') != 'done' or not bare:
            return None, None
        return bare, doc
    except Exception:
        return None, None


def confirm_line(subject):
    return f"Building the {subject} valuation deck." if subject else 'Building the valuation deck.'


def run_reexport(deck_job_id, username, bare_key, g, started_at=None):
    """Build the valuation deck from the saved file and land it in the
    thread the way a deck lands. Never raises."""
    write = _cap(g, '_pm_deck_status_write')
    base = {'job_id': deck_job_id, 'user': username, 'angle': 'valuation deck', 'started_at': started_at or time.time()}
    try:
        host = _cap(g, '_H')
        s3, bucket = _s3_of(host)
        payload = json.loads(s3.get_object(Bucket=bucket, Key=f"{RESULT_PREFIX}{bare_key}")['Body'].read().decode('utf-8'))
        deck = deliver(deck_job_id, payload, host=host, bare_key=bare_key, keep_bytes=False)
        if not deck:
            raise RuntimeError('the valuation deck could not be built')
        status = {**base, 'status': 'done', 'url': deck['url'], 'filename': deck['filename'], 's3_key': deck['s3_key'],
                  'title': deck['title'], 'slides': deck['slides'], 'valuation_key': bare_key}
        if callable(write):
            write(deck_job_id, status)
        land = _cap(g, '_pm_append_deck_to_history')
        if callable(land):
            land(username, deck_job_id, status)
        return status
    except Exception as e:
        traceback.print_exc()
        try:
            if callable(write):
                write(deck_job_id, {**base, 'status': 'error', 'error': str(e)[:400]})
        except Exception:
            pass
        return None


def start_reexport(user, hit, g, sync=False):
    """The deck surface's reply for a deck of a valuation this thread
    already holds: the same deck, built again from the saved file. None
    when the file is not this seat's to open (the caller goes on as it
    did before)."""
    try:
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip() \
            if isinstance(user, dict) else str(user or '').strip()
        bare, _doc = owned_file(hit.get('job_id'), user, g)
        if not bare:
            return None
        deck_job_id = uuid.uuid4().hex[:12]
        subject = str(hit.get('subject') or '').strip()
        out = {'success': True, 'job_id': deck_job_id, 'subject': subject, 'confirm_line': confirm_line(subject),
               'valuation_deck': True}
        probe = False
        try:
            faults = _cap(g, '_faults')
            probe = bool(faults.is_probe()) if faults is not None else False
        except Exception:
            probe = False
        if probe:       # a probe's ask is graded on its routing and builds nothing
            print(f"[bpiq-deck] probe ask: valuation deck {deck_job_id} on {subject!r} not started")
            return out
        bind = _cap(g, '_pm_job_bind_thread')
        if callable(bind):
            try:
                bind(deck_job_id, uname)
            except Exception:
                traceback.print_exc()
        write = _cap(g, '_pm_deck_status_write')
        started = time.time()
        if callable(write):
            write(deck_job_id, {'job_id': deck_job_id, 'user': uname, 'status': 'rendering', 'angle': 'valuation deck',
                                'started_at': started})
        if sync:
            run_reexport(deck_job_id, uname, bare, g, started)
        else:
            threading.Thread(target=run_reexport, args=(deck_job_id, uname, bare, g, started), daemon=True).start()
        return out
    except Exception:
        traceback.print_exc()
        return None


__all__ = ['build', 'deliver', 'note', 'charge_line', 'turn_meta', 'status_fields', 'thread_valuation', 'valuation_turn',
           'running_valuation', 'running_line', 'finished_line',
           'start_reexport', 'run_reexport', 'owned_file', 'deck_data', 'page_count', 'public', 'confirm_line', 'money',
           'LINK_LABEL', 'DECK_PREFIX']
