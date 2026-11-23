"""Chat thread history: the per-user store, idle rotation, corrected turns, the active thread, the history route, and the thread a background job lands in.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import uuid
import threading
import traceback
from prometheus import faults as _faults
from prometheus import store as _store
from datetime import datetime, timedelta, date, timezone
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response

# <<moved>>
# <<block: 1>>
SYNTH_CHAT_HISTORY_KEY_PREFIX = "system/synth_chat_history"


# <<block: 2>>
def _synth_chat_history_key(username):
    safe = ''.join(c for c in (username or 'anon')
                    if c.isalnum() or c in '-_.@').lower()
    return f"{_C.SYNTH_CHAT_HISTORY_KEY_PREFIX}/{safe}.json"


# <<block: 3>>
SYNTH_CHAT_THREADS_PREFIX = "system/synth_chat_threads"


# <<block: 4>>
_PM_MAX_THREADS = 40


# <<block: 5>>
def _load_synth_chat_history(username):
    try:
        idx = _C._load_threads_index(username)
        tid = idx.get('active') or (idx['threads'][0]['id']
                                    if idx.get('threads') else None)
        if not tid:
            return []
        return _C._pm_s3_json(_C._pm_thread_key(username, tid), [])
    except Exception as e:
        print(f"[synth-chat] history load failed for {username}: {e}")
        return []


# <<block: 6>>
# Idle rotation (2026-10-01 Jenna: "if you've been gone for a while
# and come back it should open a new thread like ChatGPT or Claude
# and move the tired thread to the side bar"). Since 2026-11-06 the
# decision is prometheus.thread_presence.fresh_decision, read on the
# widget's open (history GET with open=1): a thread idle this long opens
# a FRESH PANE, and no thread is created until the reader sends
# something (Jenna: "if a chat window is empty there is no need to save
# it to the left rail ... only go to a new window if it has been
# inactive for 15m or more").
_PM_IDLE_NEW_THREAD_SECONDS = 15 * 60


# <<block: 7>>
def _pm_parse_iso(s):
    try:
        s = str(s or '').strip()
        if not s:
            return None
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


# <<block: 8>>
def _pm_history_has_running_job(history):
    """True when the newest build in this thread is still polling;
    rotation waits so the finished status lands where it started."""
    terminal = ('complete', 'error', 'failed')
    for t in reversed(list(history or [])):
        meta = t.get('meta') if isinstance(t, dict) else None
        if not isinstance(meta, dict) or not meta.get('run_id'):
            continue
        return str(meta.get('status') or 'queued').lower() not in terminal
    return False


# <<block: 9>>
def _pm_new_thread_into(username, idx):
    """Make a fresh New chat the active thread and persist. An active
    thread that is still empty is reused so the rail never stacks
    blank chats. Returns the active thread id."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    threads = idx.setdefault('threads', [])
    act = next((t for t in threads if t.get('id') == idx.get('active')),
               None)
    # Reuse only a thread that is empty where it is stored, and never
    # wipe it (2026-11-05, Jenna: "if you click for a new one it opens
    # a new thread not an old one"). The index's turn count lags the
    # thread body: the widget's save of the thread it is leaving and
    # its New-thread call race, and a count still at 0 made this hand
    # back the SAME id and write [] over the turns the save then
    # merged straight back in. The new thread was the old one.
    # the reader is in a thread again: a pending fresh-pane request is
    # over, and the thread is seen now (prometheus.thread_presence)
    idx.pop('fresh_at', None)
    if act and not act.get('turns') \
            and act.get('title') in (None, '', 'New chat') \
            and not (_C._pm_s3_json(_C._pm_thread_key(username, act['id']), []) or []):
        act['updated'] = now
        act['last_seen'] = now
        _C._pm_s3_put_json(_C._pm_threads_index_key(username), idx)
        return act['id']
    tid = uuid.uuid4().hex[:10]
    threads.append({'id': tid, 'title': 'New chat', 'created': now,
                    'updated': now, 'last_seen': now, 'turns': 0})
    # Bound growth: the oldest empty-or-stale threads roll off.
    if len(threads) > _C._PM_MAX_THREADS:
        idx['threads'] = sorted(
            threads, key=lambda t: str(t.get('updated') or ''),
            reverse=True)[:_C._PM_MAX_THREADS]
    idx['active'] = tid
    _C._pm_s3_put_json(_C._pm_threads_index_key(username), idx)
    _C._pm_s3_put_json(_C._pm_thread_key(username, tid), [])
    return tid


# <<block: 10>>
def _pm_rotate_idle_thread(username):
    """Retired 2026-11-06 (Jenna: an empty chat is never saved to the
    rail). The two-hour rotation created a New chat on the server the
    moment a thread went idle; those are the empty 'New chat' entries
    that stacked in the rail. The idle decision now lives in
    prometheus.thread_presence.fresh_decision and opens a fresh PANE
    (no thread until the first message). Kept as a no-op for the
    callers and tests that grew up on it."""
    return None


# <<block: 11>>
def _pm_keep_corrected_turns(username, tid, incoming):
    """The stored thread with the incoming turns merged in: every turn the server holds survives a client's save
    (2026-11-04); corrections (meta.corrected_at) and server-appended turns included (2026-10-16). prometheus.thread_merge."""
    try:
        from prometheus.legacy import threads as _thr
        return _thr.keep_server_turns(_C._pm_s3_json(_C._pm_thread_key(username, tid), []) or [], incoming)
    except Exception:
        traceback.print_exc()
        return incoming


# <<block: 12>>
def _pm_merge_save_thread(username, tid, history):
    """Write one thread: the stored turns with ``history`` merged in, guarded
    by the object's ETag so a landing or a note that arrives mid-save is
    re-read and kept (prometheus.thread_merge.merge_save). Never shorter
    than what was stored, except the 200-turn growth bound."""
    from prometheus import thread_merge as _tm
    return _tm.merge_save(_H.s3_client, _H.S3_BUCKET, _C._pm_thread_key(username, tid), list(history or []))


# <<block: 13>>
def _save_synth_chat_history(username, history, thread_id=None):
    """Merge the client's history into its thread and refresh the index.
    Returns {'thread_id', 'history'} (the merged thread as written), or
    False when nothing could be saved. A save naming a thread the caller
    no longer has (deleted, rolled off the rail) is refused with
    {'unknown_thread': True} rather than landed on another thread
    (2026-11-04: that redirect is how one tab's stale copy reached
    another tab's thread)."""
    try:
        idx = _C._load_threads_index(username)
        known = {th.get('id') for th in idx.get('threads', [])}
        want = str(thread_id).strip() if thread_id and str(thread_id).strip() else ''
        if want and want not in known:
            return {'unknown_thread': True, 'thread_id': want}
        # the save names its thread (2026-10-21): a save that races a switch lands on the thread it came from, never the one just opened
        tid = want or idx.get('active') or (idx['threads'][0]['id'] if idx.get('threads') else None)
        if not tid:
            return False
        # a greeting is a bubble the widget draws, never a turn: a save with
        # no user turn into a thread that holds nothing writes nothing, so
        # an empty pane never becomes a rail entry (Jenna 2026-11-06)
        if not any(isinstance(t, dict) and str(t.get('role') or '') == 'user' for t in history):
            stored = _C._pm_s3_json(_C._pm_thread_key(username, tid), []) or []
            if not stored:
                return {'thread_id': tid, 'history': []}
        merged = _C._pm_merge_save_thread(username, tid, history)
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        for th in idx.get('threads', []):
            if th.get('id') == tid:
                th['updated'] = now
                th['last_seen'] = now   # a turn is activity (prometheus.thread_presence)
                th['turns'] = len(merged)
                if th.get('title') in (None, '', 'New chat'):
                    th['title'] = _C._pm_thread_title_from(merged)
                break
        _C._pm_s3_put_json(_C._pm_threads_index_key(username), idx)
        return {'thread_id': tid, 'history': merged}
    except Exception as e:
        print(f"[synth-chat] history save failed for {username}: {e}")
        return False


# <<block: 14>>
def _pm_active_thread_history(username):
    """(thread id, turns) of the caller's active thread."""
    try:
        idx = _C._load_threads_index(username)
        tid = idx.get('active') or (idx['threads'][0]['id'] if idx.get('threads') else None)
        if not tid:
            return None, []
        return tid, (_C._pm_s3_json(_C._pm_thread_key(username, tid), []) or [])
    except Exception as e:
        print(f"[synth-chat] history load failed for {username}: {e}")
        return None, []


# <<block: 15>>
@_H.app.route('/api/brief-chat/history', methods=['GET', 'POST'])
@_H.app.route('/api/synth-chat/history', methods=['GET', 'POST'])  # legacy alias
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/history')
def api_synth_chat_history():
    """Per-user chat message history persistence (S3-backed).

    Session-authenticated dashboard users only. Chat history is a UI
    concern and never exposed via the partner API.
    """
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = user.get('username') or user.get('email') or 'anon'
    from prometheus import thread_merge as _tm
    if request.method == 'GET':
        from prometheus import thread_presence as _tp
        _flag = lambda k: str(request.args.get(k) or '').strip() in ('1', 'true', 'yes')   # noqa: E731
        opening = _flag('open')     # the widget is opening: resume or a fresh pane (2026-11-06)
        seen = _flag('seen')        # the open widget's pull: the thread is on screen
        want = str(request.args.get('thread_id') or '').strip()
        idx = _C._load_threads_index(uname)
        if want and not any(t.get('id') == want for t in idx.get('threads', [])):
            if not opening:
                return jsonify({'success': False, 'error': 'unknown thread'}), 404
            # a tab reopening on a thread the index does not list keeps its own pane (2026-11-11); it used to be
            # handed the reader's ACTIVE thread, another chat's turns under the question about to be typed
            return jsonify({'success': True, 'history': [], 'thread_id': want, 'unlisted': True, 'why': 'unlisted', 'rev': ''})
        if opening:
            # Reopen resumes unless idle 15 minutes (Jenna 2026-11-06): the
            # decision is the index's; a fresh pane names no thread and
            # creates none. The resumed thread is seen now. The reader's
            # empty threads older than fifteen minutes go on the same load.
            fresh, why = _tp.fresh_decision(idx, tid=want or None)
            tid = want or idx.get('active') or (idx['threads'][0]['id'] if idx.get('threads') else None)
            try:
                if _tp.sweep_due(idx):
                    swept = _tp.sweep_for_user(uname, idx, force=True)   # writes the index itself
                    if swept is not None and tid and not any(t.get('id') == tid for t in idx.get('threads', [])):
                        tid = idx.get('active')
            except Exception as _fx:
                _faults.note('chat.sweep', _fx)
            if fresh:
                return jsonify({'success': True, 'history': [], 'thread_id': None, 'fresh': True,
                                'fresh_from': tid, 'why': why, 'rev': ''})
            if tid and _tp.mark_seen(idx, tid, min_gap_s=0):
                _C._pm_s3_put_json(_C._pm_threads_index_key(uname), idx)
            hist = (_C._pm_s3_json(_C._pm_thread_key(uname, tid), []) or []) if tid else []
            return jsonify({'success': True, 'history': hist, 'thread_id': tid, 'resumed': True,
                            'why': why, 'rev': _tm.thread_rev(hist)})
        if want:
            # One thread by id, read only (2026-11-04): a tab pulling its own
            # thread's turns moves nothing (no rotation, no active switch).
            # seen=1 stamps the thread as on screen (prometheus.thread_presence).
            if seen and _tp.mark_seen(idx, want):
                _C._pm_s3_put_json(_C._pm_threads_index_key(uname), idx)
            hist = _C._pm_s3_json(_C._pm_thread_key(uname, want), []) or []
            return jsonify({'success': True, 'history': hist, 'thread_id': want,
                            'rev': _tm.thread_rev(hist)})
        peek = str(request.args.get('peek') or '').strip() in ('1', 'true', 'yes')
        rotated_from = None if peek else _C._pm_rotate_idle_thread(uname)
        tid, hist = _C._pm_active_thread_history(uname)
        out = {'success': True, 'history': hist, 'thread_id': tid,
               'rev': _tm.thread_rev(hist)}
        if rotated_from is not None:
            out['rotated'] = True
            out['rotated_from'] = rotated_from
        return jsonify(out)
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/history', e)
        return jsonify(_H._chatbot_calm_payload())
    history = body.get('history') or []
    if not isinstance(history, list):
        _H._chatbot_error_email('brief-chat/history',
                             'history payload is not a list',
                             tb='(request validation)')
        return jsonify(_H._chatbot_calm_payload())
    try:   # a calm line a tab wrote on its own gets the held record behind it (2026-11-11, prometheus.promise_truth)
        history = __import__('prometheus.promise_truth', fromlist=['back_thread']).back_thread(
            uname, body.get('thread_id'), history, load=lambda: (_C._pm_s3_json(_C._pm_thread_key(uname, body.get('thread_id')), []) if body.get('thread_id') else None))
    except Exception as _fx:
        _faults.note('chat', _fx)
    saved = _C._save_synth_chat_history(uname, history, body.get('thread_id'))
    if not saved:
        return jsonify({'success': False})
    if saved.get('unknown_thread'):
        return jsonify({'success': False, 'error': 'unknown thread',
                        'thread_id': saved.get('thread_id')}), 404
    # The merged thread is the record (2026-11-04). When it is not the
    # list the client sent (the client lacked turns the server held), it
    # rides back so the tab takes them in instead of pushing over them.
    merged = saved['history']
    out = {'success': True, 'thread_id': saved['thread_id'],
           'rev': _tm.thread_rev(merged), 'turns': len(merged)}
    if _tm.differs(history, merged):
        out['changed'] = True
        out['history'] = merged
    return jsonify(out)


# <<block: 16>>
@_H.app.route('/api/prometheus/proactive', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('prometheus/proactive')
def api_prometheus_proactive():
    """Per-account openers for the chat welcome bubble (2026-10-01
    proactive mode). Read-only; computed from the account's recent
    asks plus the measured tracker movers; cached an hour per
    account. Session-authenticated dashboard users only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = user.get('username') or user.get('email') or 'anon'
    chips = []
    try:
        import prometheus_proactive as _ppro
        out = _ppro.suggestions(uname)
        chips = (out or {}).get('chips') or []
    except Exception:
        traceback.print_exc()
    # Status line on open (2026-10-06, audit item 8): what finished
    # since the user was last here, what is still running, and what was
    # reused from the dashboard instead of rebuilt. Composed here, in
    # the API, so every client shows the same sentence.
    status_line = ''
    try:
        status_line = _C._pm_open_status_line(user)
    except Exception:
        traceback.print_exc()
    return jsonify({'success': True, 'chips': chips, 'status_line': status_line})


# <<block: 17>>
# Thread targeting for background jobs (2026-10-01, Prometheus v1).
# A caller that names a thread (the app, the API) sets a marker for
# the life of the request thread; the job launch binds it to the job
# id; the finish lands the result in THAT thread. Dashboard requests
# name no thread and keep the legacy active-thread landing.
_PM_REQ_THREAD = threading.local()


# <<block: 18>>
_PM_JOB_THREAD = {}


# <<block: 19>>
def _pm_req_thread_set(body):
    try:
        _C._PM_REQ_THREAD.tid = str((body or {}).get('thread_id') or '').strip()
    except Exception:
        _C._PM_REQ_THREAD.tid = ''


# <<block: 20>>
def _pm_read_job_doc(job_id):
    """One read job's document (status, payload, resume), or None. The
    thread-base hold reads a thread's earlier landed reads through it
    (prometheus.thread_bases, 2026-11-10). Never raises."""
    jid = ''.join(c for c in str(job_id or '') if c.isalnum() or c in '-_')
    if not jid:
        return None
    doc = _C._pm_s3_json(f"{_C._PM_READ_PREFIX}{jid}.json", None)   # the shared read: the store's mirror first under PM_STORE_READ (stage 6, 2026-11-20), S3 on a miss
    return doc if isinstance(doc, dict) else None


# <<block: 21>>
def _pm_job_bind_thread(job_id, username=None, resumed=False, question=''):
    """The thread a job lands in, pinned at launch (prometheus.thread_scope.job_thread).
    A job launched through the key surface never takes the dashboard's active thread: an ask that
    named no conversation gets a key thread of its own (prometheus.api_threads, 2026-11-10).
    The launch's own record wins: ``resumed`` (the read worker running a queued job, a
    recovered copy) reads the thread already on the job document and never re-derives it
    (2026-11-11, Armine: the worker re-read "the reader's active thread" and her Mad Men
    read landed in the Cruel Summer thread she had opened meanwhile). Then the request's
    own thread, then the one the ask service stashed on the request (``g._pm_thread_id``);
    the active thread, then the first, only for a web request that named none."""
    from prometheus import thread_scope as _tscope
    _rec = str((_C._pm_read_job_doc(job_id) or {}).get('thread_id') or '') if (resumed and job_id) else ''
    _stashed = ''
    try:
        from flask import g as _g_bind, has_app_context as _has_ctx
        if _has_ctx():
            _stashed = str(getattr(_g_bind, '_pm_thread_id', '') or '')
    except Exception:
        _stashed = ''

    def _active():
        idx = _C._load_threads_index(username)
        tid = str(idx.get('active') or '')
        if not tid and idx.get('threads'):
            tid = str((idx['threads'][0] or {}).get('id') or '')
        return tid
    from prometheus import api_threads as _apit_b
    _key_ask = _apit_b.is_api_request()
    tid = _tscope.job_thread(recorded=_rec, request=('' if resumed else str(getattr(_C._PM_REQ_THREAD, 'tid', '') or '')),
                             stashed=_stashed, active=(_active if (username and not _key_ask) else None), web=not resumed)
    if not tid and _key_ask and username and job_id and not resumed:
        from prometheus import service as _svc_at
        tid = _apit_b.mint_for_job(username, job_id, question, ensure=_svc_at.ensure_thread, save=_svc_at.save_thread)
    if tid and job_id:
        _C._PM_JOB_THREAD[job_id] = tid
    return tid or None


# <<block: 22>>
def _pm_thread_for_job(username, job_id):
    """The thread a finished job should land in, or None for the
    caller's active thread. Verifies the id still belongs to the user.
    The job document's thread_id serves a finishing process that did not launch it (2026-10-14)."""
    _svc_tj = __import__('prometheus.service', fromlist=['job_thread_id'])
    tid = _C._PM_JOB_THREAD.pop(job_id, None) or _svc_tj.job_thread_id(job_id)
    if not username:
        return None
    try:
        # a key-surface job lands in a key thread, listed again when the index lost it; never the active thread (2026-11-10)
        return __import__('prometheus.api_threads', fromlist=['landing_thread']).landing_thread(
            username, job_id, tid, exists=lambda t: any(x.get('id') == t for x in _C._load_threads_index(username).get('threads', [])),
            ensure=_svc_tj.ensure_thread, relist=_svc_tj.relist_thread, job_doc=(None if tid else (_svc_tj.find_job_doc(job_id)[0] or {})))
    except Exception as _fx:
        _faults.note('chat', _fx)
    return None


# <<block: 23>>
def _pm_load_thread_or_active(username, tid):
    if not tid:
        return _C._load_synth_chat_history(username) or []
    return _C._pm_s3_json(_C._pm_thread_key(username, tid), []) or []


# <<block: 24>>
def _pm_save_thread_or_active(username, tid, history):
    if not tid:
        return _C._save_synth_chat_history(username, history)
    # merged into what is stored, ETag-guarded: a landing never writes over a turn that arrived mid-save (2026-11-04)
    trimmed = _C._pm_merge_save_thread(username, tid, history)
    _store.enabled() and _store.upsert_thread(username, tid, trimmed, _C._pm_thread_title_from(trimmed))   # shadow row (audit item 7)
    try:
        idx = _C._load_threads_index(username)
        for th in idx.get('threads', []):
            if th.get('id') == tid:
                th['updated'] = _C._pm_iso_now()
                th['turns'] = len(trimmed)
                if th.get('title') in (None, '', 'New chat'):
                    th['title'] = _C._pm_thread_title_from(trimmed)
                break
        _C._pm_s3_put_json(_C._pm_threads_index_key(username), idx)
    except Exception:
        traceback.print_exc()
    return True


__all__ = ['SYNTH_CHAT_HISTORY_KEY_PREFIX', '_synth_chat_history_key', 'SYNTH_CHAT_THREADS_PREFIX', '_PM_MAX_THREADS', '_load_synth_chat_history', '_PM_IDLE_NEW_THREAD_SECONDS', '_pm_parse_iso', '_pm_history_has_running_job', '_pm_new_thread_into', '_pm_rotate_idle_thread', '_pm_keep_corrected_turns', '_pm_merge_save_thread', '_save_synth_chat_history', '_pm_active_thread_history', 'api_synth_chat_history', 'api_prometheus_proactive', '_PM_REQ_THREAD', '_PM_JOB_THREAD', '_pm_req_thread_set', '_pm_read_job_doc', '_pm_job_bind_thread', '_pm_thread_for_job', '_pm_load_thread_or_active', '_pm_save_thread_or_active']
