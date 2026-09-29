#!/usr/bin/env python3
"""Prometheus chat threads, server side (2026-09-28 Jenna: "new chat
windows... save them in a collapsable left rail so it shows your
threads like in claude or chatgpt").

The existing history GET/POST endpoints stay the interface; they now
route to the user's ACTIVE thread, so every existing consumer (saves,
reopen resume, the watch email, ops corrections through the helpers)
keeps working per-thread with no call-site changes. A legacy single
history migrates into thread one on first touch.

Replayable string patch (clean-worktree shipping)."""
import sys
from pathlib import Path

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
src = APP.read_text(encoding="utf-8")

if "SYNTH_CHAT_THREADS_PREFIX" in src:
    print("already applied")
    sys.exit(0)

# ---- 1. thread store helpers + rewired load/save --------------------
OLD_HELPERS = '''def _load_synth_chat_history(username):
    key = _synth_chat_history_key(username)
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        # NoSuchKey is expected for first-time users; any other error we log
        if 'NoSuchKey' not in str(e):
            print(f"[synth-chat] history load failed for {username}: {e}")
        return []


def _save_synth_chat_history(username, history):
    key = _synth_chat_history_key(username)
    try:
        # Trim to last 200 turns to bound growth
        trimmed = list(history or [])[-200:]
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=key,
            Body=json.dumps(trimmed, indent=2).encode('utf-8'),
            ContentType='application/json',
        )'''

NEW_HELPERS = '''SYNTH_CHAT_THREADS_PREFIX = "system/synth_chat_threads"
_PM_MAX_THREADS = 40


def _pm_safe_user(username):
    return ''.join(c for c in (username or 'anon')
                   if c.isalnum() or c in '-_.@').lower()


def _pm_threads_index_key(username):
    return f"{SYNTH_CHAT_THREADS_PREFIX}/{_pm_safe_user(username)}/index.json"


def _pm_thread_key(username, tid):
    safe_t = ''.join(c for c in str(tid) if c.isalnum() or c in '-_')
    return f"{SYNTH_CHAT_THREADS_PREFIX}/{_pm_safe_user(username)}/{safe_t}.json"


def _pm_s3_json(key, default):
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        if 'NoSuchKey' not in str(e):
            print(f"[synth-chat] read failed {key}: {e}")
        return default


def _pm_s3_put_json(key, obj):
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=key,
        Body=json.dumps(obj, indent=2).encode('utf-8'),
        ContentType='application/json')


def _pm_thread_title_from(history):
    for t in (history or []):
        if (t.get('role') or '') == 'user' and str(t.get('text') or '').strip():
            return str(t['text']).strip()[:48]
    return 'New chat'


def _load_threads_index(username):
    """The user's thread index; migrates the legacy single history
    into thread one on first touch. Always returns a valid index with
    an active thread id."""
    idx = _pm_s3_json(_pm_threads_index_key(username), None)
    if isinstance(idx, dict) and idx.get('threads'):
        return idx
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    legacy = _pm_s3_json(_synth_chat_history_key(username), [])
    tid = uuid.uuid4().hex[:10]
    thread = {'id': tid,
              'title': (_pm_thread_title_from(legacy)
                        if legacy else 'New chat'),
              'created': now, 'updated': now,
              'turns': len(legacy or [])}
    idx = {'active': tid, 'threads': [thread]}
    try:
        if legacy:
            _pm_s3_put_json(_pm_thread_key(username, tid), legacy)
        _pm_s3_put_json(_pm_threads_index_key(username), idx)
    except Exception as e:
        print(f"[synth-chat] thread migration failed for {username}: {e}")
    return idx


def _load_synth_chat_history(username):
    try:
        idx = _load_threads_index(username)
        tid = idx.get('active') or (idx['threads'][0]['id']
                                    if idx.get('threads') else None)
        if not tid:
            return []
        return _pm_s3_json(_pm_thread_key(username, tid), [])
    except Exception as e:
        print(f"[synth-chat] history load failed for {username}: {e}")
        return []


def _save_synth_chat_history(username, history):
    try:
        # Trim to last 200 turns to bound growth
        trimmed = list(history or [])[-200:]
        idx = _load_threads_index(username)
        tid = idx.get('active') or (idx['threads'][0]['id']
                                    if idx.get('threads') else None)
        if not tid:
            return False
        _pm_s3_put_json(_pm_thread_key(username, tid), trimmed)
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        for th in idx.get('threads', []):
            if th.get('id') == tid:
                th['updated'] = now
                th['turns'] = len(trimmed)
                if th.get('title') in (None, '', 'New chat'):
                    th['title'] = _pm_thread_title_from(trimmed)
                break
        _pm_s3_put_json(_pm_threads_index_key(username), idx)'''

count = src.count(OLD_HELPERS)
if count != 1:
    raise RuntimeError(f"helpers anchor found {count}x")
src = src.replace(OLD_HELPERS, NEW_HELPERS)

# The old save body continues with the watch hook + return True; the
# replacement above ends right before that shared tail, so nothing
# else in the function changes.

# ---- 2. thread management endpoints ---------------------------------
OLD_ROUTE = "@app.route('/api/me/prometheus-queries')"
NEW_ROUTE = '''@app.route('/api/brief-chat/threads', methods=['GET'])
@requires_auth
@_chatbot_route_guard('brief-chat/threads')
def api_synth_chat_threads():
    """The caller's chat threads for the left rail, most recent
    first (2026-09-28 Jenna). Session-only."""
    user, err = _synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    idx = _load_threads_index(uname)
    threads = sorted(idx.get('threads', []),
                     key=lambda t: str(t.get('updated') or ''),
                     reverse=True)
    return jsonify({'success': True, 'active': idx.get('active'),
                    'threads': threads})


@app.route('/api/brief-chat/threads/new', methods=['POST'])
@requires_auth
@_chatbot_route_guard('brief-chat/threads-new')
def api_synth_chat_threads_new():
    user, err = _synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    idx = _load_threads_index(uname)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    tid = uuid.uuid4().hex[:10]
    idx.setdefault('threads', []).append(
        {'id': tid, 'title': 'New chat', 'created': now,
         'updated': now, 'turns': 0})
    # Bound growth: the oldest empty-or-stale threads roll off.
    if len(idx['threads']) > _PM_MAX_THREADS:
        idx['threads'] = sorted(
            idx['threads'], key=lambda t: str(t.get('updated') or ''),
            reverse=True)[:_PM_MAX_THREADS]
    idx['active'] = tid
    _pm_s3_put_json(_pm_threads_index_key(uname), idx)
    _pm_s3_put_json(_pm_thread_key(uname, tid), [])
    return jsonify({'success': True, 'active': tid,
                    'threads': sorted(idx['threads'],
                                      key=lambda t: str(t.get('updated') or ''),
                                      reverse=True)})


@app.route('/api/brief-chat/threads/activate', methods=['POST'])
@requires_auth
@_chatbot_route_guard('brief-chat/threads-activate')
def api_synth_chat_threads_activate():
    user, err = _synth_chat_gate(allow_api_key=False)
    if err:
        return err
    uname = session.get('username') or ''
    body = request.get_json(silent=True) or {}
    tid = str(body.get('id') or '').strip()
    idx = _load_threads_index(uname)
    if not any(t.get('id') == tid for t in idx.get('threads', [])):
        return jsonify({'success': False, 'error': 'unknown thread'}), 404
    idx['active'] = tid
    _pm_s3_put_json(_pm_threads_index_key(uname), idx)
    history = _pm_s3_json(_pm_thread_key(uname, tid), [])
    return jsonify({'success': True, 'active': tid, 'history': history})


@app.route('/api/brief-chat/threads/rename', methods=['POST'])
@requires_auth
@_chatbot_route_guard('brief-chat/threads-rename')
def api_synth_chat_threads_rename():
    user, err = _synth_chat_gate(allow_api_key=False)
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
            _pm_s3_put_json(_pm_threads_index_key(uname), idx)
            return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'unknown thread'}), 404


@app.route('/api/brief-chat/threads/delete', methods=['POST'])
@requires_auth
@_chatbot_route_guard('brief-chat/threads-delete')
def api_synth_chat_threads_delete():
    user, err = _synth_chat_gate(allow_api_key=False)
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
        s3_client.delete_object(Bucket=S3_BUCKET,
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
            _pm_s3_put_json(_pm_thread_key(uname, nid), [])
        else:
            idx['active'] = sorted(
                idx['threads'],
                key=lambda t: str(t.get('updated') or ''),
                reverse=True)[0]['id']
            history = _pm_s3_json(
                _pm_thread_key(uname, idx['active']), [])
    else:
        history = None
    _pm_s3_put_json(_pm_threads_index_key(uname), idx)
    return jsonify({'success': True, 'active': idx['active'],
                    'history': history,
                    'threads': sorted(idx['threads'],
                                      key=lambda t: str(t.get('updated') or ''),
                                      reverse=True)})


@app.route('/api/me/prometheus-queries')'''

count = src.count(OLD_ROUTE)
if count != 1:
    raise RuntimeError(f"route anchor found {count}x")
src = src.replace(OLD_ROUTE, NEW_ROUTE, 1)

# ---- 3. queries endpoint aggregates across threads -------------------
OLD_Q = """    try:
        hist = _load_synth_chat_history(uname) or []
    except Exception:
        hist = []
    out = []
    for t in reversed(hist):"""
NEW_Q = """    try:
        idx = _load_threads_index(uname)
        hist = []
        for th in sorted(idx.get('threads', []),
                         key=lambda t: str(t.get('updated') or '')):
            hist.extend(_pm_s3_json(
                _pm_thread_key(uname, th.get('id')), []))
    except Exception:
        hist = []
    out = []
    for t in reversed(hist):"""

count = src.count(OLD_Q)
if count != 1:
    raise RuntimeError(f"queries anchor found {count}x")
src = src.replace(OLD_Q, NEW_Q)

APP.write_text(src, encoding="utf-8")
print("app.py threads patch applied")
