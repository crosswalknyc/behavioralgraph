"""Prometheus chat threads (2026-10-02 RCA W3, first family out of chat.py).

The per-user thread index and the five thread routes (list, new,
activate, rename, delete). Reads the host through ``_H`` and the rest of
the legacy chat module through ``_C`` (``prometheus.legacy.C``), both
bound before this module is imported, so nothing here imports chat.py
and chat.py imports this module last. Behavior is byte-for-byte the
code that lived in chat.py; only the indirection changed.
"""
import uuid
from datetime import datetime, timezone
from flask import jsonify, request, session

from prometheus.legacy import H as _H, C as _C  # noqa: E402
from prometheus import seams as _seams  # noqa: E402

__all__ = ['api_synth_chat_threads', 'api_synth_chat_threads_fresh', 'api_synth_chat_threads_new', 'api_synth_chat_threads_activate', 'api_synth_chat_threads_bind', 'api_synth_chat_threads_rename', 'api_synth_chat_threads_delete', '_pm_threads_index_key', '_pm_thread_key', '_pm_thread_title_from', '_pm_project_label', '_load_threads_index', '_turn_epoch', '_is_server_turn', 'keep_server_turns', 'fill_blank_agent_turns']


# The dashboard's display name for a project key (2026-10-22, Scott's
# 'Shane Smith' thread on LeBron's key): the label of a bound thread is
# derived from its key on the server, never taken from the client. One
# small per-process cache for the two listings that cost a call.
_PM_PROJECT_NAMES = {'journey': (0.0, {}), 'campaign': (0.0, {})}
_PM_PROJECT_NAMES_TTL = 120.0


def _pm_project_names(kind):
    import time as _t
    at, names = _PM_PROJECT_NAMES.get(kind) or (0.0, {})
    if names and _t.time() - at < _PM_PROJECT_NAMES_TTL:
        return names
    fresh = {}
    try:
        if kind == 'journey':
            from prometheus import thread_binding as _tb
            idx = _H._pm_s3_json('journey-iq/_index.json', {}) or {}
            for r in (idx.get('runs') or []) if isinstance(idx, dict) else []:
                if isinstance(r, dict) and r.get('key'):
                    fresh[str(r['key'])] = _tb.prettify(r.get('project_name') or r.get('target') or '')
        elif kind == 'campaign':
            iq = _H.get('_intent_iq', None)
            if iq is not None:
                for t in (iq.list_titles() or {}).get('titles') or []:
                    if isinstance(t, dict) and t.get('title_slug'):
                        fresh[str(t['title_slug'])] = str(t.get('display_name') or '').strip()
    except Exception:
        fresh = {}
    if fresh or not names:
        _PM_PROJECT_NAMES[kind] = (_t.time(), fresh)
    return fresh or names


def _pm_project_label(kind, ident):
    """The display name the dashboard shows for a project key, or '' when
    the catalog does not name it (the caller then reads the key itself).
    Profiles from the in-memory profile catalog (hidden files included:
    an open file has a name whatever the rail shows), journeys from the
    journey index, campaigns from the Attribution IQ title list. Never
    raises."""
    try:
        if kind == 'profile':
            try:
                _H.maybe_refresh_persisted_cache_if_changed()
            except Exception:
                pass
            for j in (_H.get('s3_cache', None) or {}).get('jobs') or []:
                if isinstance(j, dict) and (j.get('s3_key') or j.get('key')) == ident:
                    return str(j.get('display_name') or '').strip()
            return ''
        if kind in ('journey', 'campaign'):
            return str(_pm_project_names(kind).get(ident) or '').strip()
    except Exception:
        return ''
    return ''


@_H.app.route('/api/brief-chat/threads/bind', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_bind():
    """The conversation follows the project (2026-10-21, Debi Pomerantz:
    "if I am switching between projects, it would be great if the
    conversation thread could switch with me"). The widget posts what
    the dashboard has open (``page_context`` or an explicit
    ``subject_key`` / ``subject_label`` / ``product``); the thread bound
    to that project becomes active, created when none exists; an empty
    key activates the general chat. Returns the active id, whether it
    switched or was created, the binding and the thread's history so the
    widget can swap in one round trip. Session-only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    body = request.get_json(silent=True) or {}
    from prometheus import service as _svc, thread_binding as _tb
    sk = str(body.get('subject_key') or '').strip()
    label = str(body.get('subject_label') or '').strip()
    product = str(body.get('product') or '').strip()
    if not sk and body.get('page_context'):
        sk, label, product = _tb.subject_key_for(body.get('page_context'))
        sk, label, product = sk or '', label or '', product or ''
    out = _svc.thread_for_subject(uname, sk, label, product, create=True)
    idx = _load_threads_index(uname)
    from prometheus import thread_presence as _tp
    return jsonify({'success': True, 'active': out.get('tid'),
                    'switched': bool(out.get('switched')),
                    'created': bool(out.get('created')),
                    'binding': out.get('binding'),
                    'history': out.get('history') or [],
                    'threads': sorted(_tp.visible_threads(idx.get('threads', [])),
                                      key=lambda t: str(t.get('updated') or ''),
                                      reverse=True)})


@_H.app.route('/api/brief-chat/threads', methods=['GET'])
@_H.requires_auth
def api_synth_chat_threads():
    """The caller's chat threads for the left rail, most recent
    first (2026-09-28 Jenna). Session-only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    from prometheus import service as _svc
    from prometheus import thread_presence as _tp
    idx, _changed = _svc.repair_thread_labels(uname)   # a stale label never survives the next look (2026-10-22)
    # an empty chat is never a rail entry (Jenna 2026-11-06): empties older
    # than fifteen minutes go (once per ten minutes), the rest stay hidden
    try:
        _tp.sweep_for_user(uname, idx)
    except Exception:
        pass
    threads = sorted(_tp.visible_threads(idx.get('threads', [])),
                     key=lambda t: str(t.get('updated') or ''),
                     reverse=True)
    return jsonify({'success': True, 'active': idx.get('active'),
                    'threads': threads})


@_H.app.route('/api/brief-chat/threads/fresh', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_fresh():
    """The reader asked for a new chat (Jenna 2026-11-06: "if a chat
    window is empty there is no need to save it to the left rail").
    Nothing is created; the index remembers the request so a reload or
    another tab opens the same empty pane. The thread is minted on the
    first message (threads/new from the send path). Session-only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    from prometheus import thread_presence as _tp
    idx = _load_threads_index(uname)
    _tp.mark_fresh(idx)
    _C._pm_s3_put_json(_pm_threads_index_key(uname), idx)
    # the armed intakes go with the thread that was left (prometheus.intake_reset)
    try:
        from prometheus import intake_reset as _ireset
        _ireset.on_new_thread(uname, None)
    except Exception:
        pass
    return jsonify({'success': True, 'fresh': True, 'active': None,
                    'threads': sorted(_tp.visible_threads(idx.get('threads', [])),
                                      key=lambda t: str(t.get('updated') or ''),
                                      reverse=True)})


@_H.app.route('/api/brief-chat/threads/new', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_new():
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    idx = _load_threads_index(uname)
    tid = _C._pm_new_thread_into(uname, idx)
    # a new thread starts clean: every armed intake (the valuation wizard,
    # the flywheel, a stashed brief) goes with the thread it was on
    # (Jenna 2026-11-05, prometheus.intake_reset)
    try:
        from prometheus import intake_reset as _ireset
        _ireset.on_new_thread(uname, tid)
    except Exception:
        pass
    from prometheus import thread_presence as _tp
    return jsonify({'success': True, 'active': tid,
                    'threads': sorted(_tp.visible_threads(idx['threads']),
                                      key=lambda t: str(t.get('updated') or ''),
                                      reverse=True)})


@_H.app.route('/api/brief-chat/threads/activate', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_activate():
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    body = request.get_json(silent=True) or {}
    tid = str(body.get('id') or '').strip()
    idx = _load_threads_index(uname)
    if not any(t.get('id') == tid for t in idx.get('threads', [])):
        return jsonify({'success': False, 'error': 'unknown thread'}), 404
    idx['active'] = tid
    idx.pop('fresh_at', None)   # the reader is in a thread again (prometheus.thread_presence)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for t in idx.get('threads', []):
        if t.get('id') == tid:
            t['opened'] = now
            t['last_seen'] = now   # opened from the rail by hand: activity
            break
    _C._pm_s3_put_json(_pm_threads_index_key(uname), idx)
    history = _C._pm_s3_json(_pm_thread_key(uname, tid), [])
    return jsonify({'success': True, 'active': tid, 'history': history})


@_H.app.route('/api/brief-chat/threads/rename', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_rename():
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    body = request.get_json(silent=True) or {}
    tid = str(body.get('id') or '').strip()
    title = str(body.get('title') or '').strip()[:60]
    if not title:
        return jsonify({'success': False, 'error': 'empty title'}), 400
    idx = _load_threads_index(uname)
    for t in idx.get('threads', []):
        if t.get('id') == tid:
            t['title'] = title
            _C._pm_s3_put_json(_pm_threads_index_key(uname), idx)
            return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'unknown thread'}), 404


@_H.app.route('/api/brief-chat/threads/delete', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_delete():
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    body = request.get_json(silent=True) or {}
    tid = str(body.get('id') or '').strip()
    idx = _load_threads_index(uname)
    before = len(idx.get('threads', []))
    idx['threads'] = [t for t in idx.get('threads', [])
                      if t.get('id') != tid]
    if len(idx['threads']) == before:
        return jsonify({'success': False, 'error': 'unknown thread'}), 404
    try:
        _H.s3_client.delete_object(Bucket=_H.S3_BUCKET,
                                Key=_pm_thread_key(uname, tid))
    except Exception:
        pass
    history = []
    if idx.get('active') == tid:
        if not idx['threads']:
            now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            nid = uuid.uuid4().hex[:10]
            idx['threads'] = [{'id': nid, 'title': 'New chat',
                               'created': now, 'updated': now,
                               'turns': 0}]
            idx['active'] = nid
            _C._pm_s3_put_json(_pm_thread_key(uname, nid), [])
        else:
            idx['active'] = sorted(
                idx['threads'],
                key=lambda t: str(t.get('updated') or ''),
                reverse=True)[0]['id']
            history = _C._pm_s3_json(
                _pm_thread_key(uname, idx['active']), [])
    else:
        history = None
    _C._pm_s3_put_json(_pm_threads_index_key(uname), idx)
    from prometheus import thread_presence as _tp
    return jsonify({'success': True, 'active': idx['active'],
                    'history': history,
                    'threads': sorted(_tp.visible_threads(idx['threads']),
                                      key=lambda t: str(t.get('updated') or ''),
                                      reverse=True)})


def _pm_threads_index_key(username):
    return f"{_C.SYNTH_CHAT_THREADS_PREFIX}/{_C._pm_safe_user(username)}/index.json"


def _pm_thread_key(username, tid):
    safe_t = ''.join(c for c in str(tid) if c.isalnum() or c in '-_')
    return f"{_C.SYNTH_CHAT_THREADS_PREFIX}/{_C._pm_safe_user(username)}/{safe_t}.json"


def _pm_thread_title_from(history):
    for t in (history or []):
        if (t.get('role') or '') == 'user' and str(t.get('text') or '').strip():
            return str(t['text']).strip()[:48]
    return 'New chat'


def _load_threads_index(username):
    """The user's thread index; migrates the legacy single history
    into thread one on first touch. Always returns a valid index with
    an active thread id."""
    idx = _C._pm_s3_json(_pm_threads_index_key(username), None)
    if isinstance(idx, dict) and idx.get('threads'):
        return idx
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    legacy = _C._pm_s3_json(_C._synth_chat_history_key(username), [])
    tid = uuid.uuid4().hex[:10]
    thread = {'id': tid,
              'title': (_pm_thread_title_from(legacy)
                        if legacy else 'New chat'),
              'created': now, 'updated': now,
              'turns': len(legacy or [])}
    idx = {'active': tid, 'threads': [thread]}
    try:
        if legacy:
            _C._pm_s3_put_json(_pm_thread_key(username, tid), legacy)
        _C._pm_s3_put_json(_pm_threads_index_key(username), idx)
    except Exception as e:
        print(f"[synth-chat] thread migration failed for {username}: {e}")
    return idx


# The thread store is append-only from the server's side (2026-11-04,
# Jenna: "the data should always be storing it so that it reconciles and
# should always keep track"). The merge lives in prometheus.thread_merge;
# these names stay for the callers that grew up on them. Before that
# date keep_server_turns started from the CLIENT's list and folded back
# only server-marked agent turns, so a tab holding a stale copy dropped
# every plain turn the server held (Jay Patel's own questions and three
# answers, twice in one afternoon).
from prometheus import thread_merge as _tm  # noqa: E402

SERVER_TURN_MARKS = _tm.SERVER_TURN_MARKS
_turn_epoch = _tm.turn_epoch
_is_server_turn = _tm.is_server_turn
fill_blank_agent_turns = _tm.fill_blank_agent_turns


def keep_server_turns(stored, incoming):
    """The stored thread with the client's turns merged in
    (prometheus.thread_merge.merge_turns). Every stored turn survives,
    plain or marked, user or agent; the client adds turns and patches
    the meta of turns it holds; corrections stick (a newer correction
    replaces an older one); exact repeats collapse; time order holds.
    Never raises; on any doubt the stored list plus the client's."""
    if not isinstance(incoming, list):
        return incoming
    return _tm.merge_turns(stored, incoming)
