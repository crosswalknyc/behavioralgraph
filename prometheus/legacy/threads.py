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

__all__ = ['api_synth_chat_threads', 'api_synth_chat_threads_new', 'api_synth_chat_threads_activate', 'api_synth_chat_threads_rename', 'api_synth_chat_threads_delete', '_pm_threads_index_key', '_pm_thread_key', '_pm_thread_title_from', '_load_threads_index']


@_H.app.route('/api/brief-chat/threads', methods=['GET'])
@_H.requires_auth
def api_synth_chat_threads():
    """The caller's chat threads for the left rail, most recent
    first (2026-09-28 Jenna). Session-only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    idx = _load_threads_index(uname)
    threads = sorted(idx.get('threads', []),
                     key=lambda t: str(t.get('updated') or ''),
                     reverse=True)
    return jsonify({'success': True, 'active': idx.get('active'),
                    'threads': threads})


@_H.app.route('/api/brief-chat/threads/new', methods=['POST'])
@_H.requires_auth
def api_synth_chat_threads_new():
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    idx = _load_threads_index(uname)
    tid = _C._pm_new_thread_into(uname, idx)
    return jsonify({'success': True, 'active': tid,
                    'threads': sorted(idx['threads'],
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
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for t in idx.get('threads', []):
        if t.get('id') == tid:
            t['opened'] = now
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
    return jsonify({'success': True, 'active': idx['active'],
                    'history': history,
                    'threads': sorted(idx['threads'],
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
