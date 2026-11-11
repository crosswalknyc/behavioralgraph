"""An ask through the key surface has its own thread (Jenna 2026-11-10,
the day audit; Liz's Amazon versus direct reads).

What the record showed: six read jobs launched through the key surface
landed in Liz's dashboard chat, "the active thread at the time". An ask
that named no conversation ran on no thread (correct since 2026-11-11,
``prometheus.thread_scope``), but the job it launched still asked for
"the reader's active thread" and wrote its finished read there. And a
follow-up on a key conversation that named no subject of its own ("Day
90 retention of Channels starts versus direct starts, by service") was
handed to the build flow, which a key caller cannot follow, and came
back as "Which subject should this be on?".

The rules this module carries:

  * A job launched through the key surface lands in a thread of the key
    surface, never in a dashboard chat. An ask that named a
    conversation keeps that conversation's thread. An ask that named
    none gets a thread of its own when it launches a job
    (``mint_for_job``): ``api-<key>-job-<job id>``, listed with
    ``via: api`` and ``pinned`` (it never becomes the dashboard's active
    thread, and the empty-thread sweep leaves it alone while the job
    runs), holding the question as its first turn. The reply names it as
    the conversation (``job-<job id>``), so a follow-up that passes it
    continues there.
  * A finished job whose thread the index no longer lists is never
    handed to the active thread: a key thread is listed again under its
    own id; a job the launch marked ``via: api`` gets its own key thread
    (``landing_thread``).
  * A follow-up on a key conversation that names no subject of its own
    takes the thread's last read subject (``followup_subject``,
    ``retry_on_thread_subject``). An ask with no conversation starts
    clean: no thread, no history, nothing inherited.

Small reads of the request context plus pure functions; nothing here
raises into a request. Regression: scripts/test_pm_conversation_plumbing.py.
"""
from __future__ import annotations

import re

__all__ = ['PREFIX', 'is_api_thread', 'is_api_request', 'key_tag', 'job_thread_id', 'mint_for_job',
           'minted_conversation', 'landing_thread', 'followup_subject', 'wants_subject', 'retry_on_thread_subject',
           'index_entry']

PREFIX = 'api-'


def is_api_thread(tid):
    return str(tid or '').startswith(PREFIX)


def _g():
    try:
        from flask import g, has_app_context
        return g if has_app_context() else None
    except Exception:
        return None


def is_api_request():
    """True when the request in flight came through the key surface."""
    g = _g()
    if g is None:
        return False
    try:
        return bool(getattr(g, '_pm_api_key_owner', None))
    except Exception:
        return False


def key_tag():
    """The key's short tag, the same eight characters a named
    conversation's thread carries ('key' when the request has none)."""
    g = _g()
    try:
        kid = str(getattr(g, '_pm_api_key_tag', '') or '') if g is not None else ''
    except Exception:
        kid = ''
    kid = re.sub(r"[^A-Za-z0-9]", '', kid)[:8]
    return kid or 'key'


def job_thread_id(job_id, tag=None):
    jid = re.sub(r"[^A-Za-z0-9_-]", '', str(job_id or ''))[:40]
    return f"{PREFIX}{tag or key_tag()}-job-{jid}"


def index_entry(title='API conversation', *, auto=False):
    """The extra fields a key thread's index entry carries."""
    out = {'api': True, 'via': 'api', 'pinned': True}
    if auto:
        out['api_auto'] = True   # minted for one ask; the dashboard rail does not list it ('auto' is a project thread's mark)
    return out


def mint_for_job(uname, job_id, question='', *, ensure, save=None, now=None):
    """The thread a job launched by a conversation-less key ask lands
    in. ``ensure(uname, tid, title=, via=, auto=)`` lists it (never
    moving the active thread); ``save(uname, tid, turns)`` writes the
    question as its first turn. The request is put on the thread, and
    the conversation id the reply should name is remembered. Returns
    the thread id, or '' when it could not be made."""
    try:
        if not uname or not job_id:
            return ''
        tid = job_thread_id(job_id)
        if not str(question or '').strip():
            question = _request_text()
        title = ' '.join(str(question or '').split())[:44] or 'API ask'
        ensure(uname, tid, title=f"API: {title}"[:48], via='api', auto=True)
        q = ' '.join(str(question or '').split())
        if q and callable(save):
            try:
                from datetime import datetime, timezone
                ts = now or (datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z')
                save(uname, tid, [{'role': 'user', 'text': q[:4000], 'ts': ts, 'meta': {'via': 'api'}}])
            except Exception:
                pass
        g = _g()
        if g is not None:
            try:
                g._pm_thread_id = tid
                g._pm_thread_scoped = True
                g._pm_api_conversation = 'job-' + re.sub(r"[^A-Za-z0-9_-]", '', str(job_id))[:40]
            except Exception:
                pass
        return tid
    except Exception:
        return ''


def _request_text():
    """The words of the ask in flight (the request body's text), or ''."""
    try:
        from flask import has_request_context, request
        if has_request_context():
            body = request.get_json(silent=True) or {}
            return str(body.get('text') or '') if isinstance(body, dict) else ''
    except Exception:
        pass
    return ''


def minted_conversation():
    """The conversation id minted for this request's job, or ''."""
    g = _g()
    try:
        return str(getattr(g, '_pm_api_conversation', '') or '') if g is not None else ''
    except Exception:
        return ''


def landing_thread(uname, job_id, recorded, *, exists, ensure, job_doc=None, relist=None):
    """Where a finished job lands when the index does not list the
    thread the launch recorded.

    ``recorded`` is the thread id on the job (may be empty);
    ``exists(tid)`` says whether the index lists it; ``ensure`` lists a
    key thread; ``relist`` puts a dashboard thread back under its own
    id; ``job_doc`` is the job's own document when the caller has it. A
    thread the index lost is listed again under its own id. A job the
    launch marked ``via: api`` with no thread gets its own key thread.
    Anything else returns None (the caller's own rule applies). Never
    the dashboard's active thread for a key job."""
    try:
        rec = str(recorded or '').strip()
        if rec:
            try:
                if exists(rec):
                    return rec
            except Exception:
                pass
            if is_api_thread(rec):
                ensure(uname, rec, title='API conversation', via='api', auto=('-job-' in rec))
                return rec
            if callable(relist):
                # a dashboard thread the index lost is listed again under its own id (the rule
                # resolve_ask_thread follows): the read goes where it was asked, not to the active chat
                relist(uname, rec)
                return rec
            return None
        doc = job_doc if isinstance(job_doc, dict) else {}
        if str(doc.get('via') or '').lower() == 'api':
            tid = job_thread_id(job_id, tag='key')
            ensure(uname, tid, title='API ask', via='api', auto=True)
            return tid
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- follow-ups
def followup_subject(history):
    """The subject of the last read delivered in this thread, or ''."""
    try:
        from . import priced_read as _pr
        subj = str(_pr.thread_read_subject(history) or '').strip()
        if subj:
            return subj
    except Exception:
        pass
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if meta.get('subject'):
            return str(meta['subject']).strip()
    return ''


def wants_subject(raw):
    """True when a reply came back empty-handed for want of a subject:
    the blank handoff to the build flow (a key caller cannot follow it),
    or the which-subject question."""
    if not isinstance(raw, dict):
        return False
    if raw.get('job_id') or raw.get('read_job_id') or raw.get('panel_offer') or raw.get('spec_draft'):
        return False
    reply = str(raw.get('reply') or raw.get('error') or '').strip()
    if raw.get('subject_unclear'):
        return True
    if not reply and (raw.get('route_hint') == 'interpret' or raw.get('action') == 'build_profile'):
        return True
    return bool(re.match(r"^Which subject should this be on\?", reply))


def retry_on_thread_subject(raw, history, body, run, *, text=''):
    """Run the ask once more on the thread's own subject when it came
    back for want of one. ``run(body)`` runs the analyze core and
    returns its payload. Returns the payload to ship (the retry's when
    it is an answer or a job; the first otherwise). An order is never
    rewritten into a read."""
    try:
        if not wants_subject(raw) or (isinstance(body, dict) and body.get('bind_subject')):
            return raw
        try:
            from . import orders as _ord
            if _ord.is_order(text):
                return raw
        except Exception:
            pass
        subj = followup_subject(history)
        if not subj:
            return raw
        again = run(dict(body or {}, bind_subject=subj))
        if isinstance(again, dict) and not wants_subject(again) and (again.get('reply') or again.get('job_id') or again.get('read_job_id')):
            again.setdefault('subject', subj)
            again['inherited_subject'] = subj
            return again
    except Exception:
        pass
    return raw
