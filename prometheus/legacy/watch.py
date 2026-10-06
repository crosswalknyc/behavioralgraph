"""Prometheus watch, attribution and context helpers (2026-10-06, third
family out of chat.py).

Leaf helpers with no routes: the ask-log user resolver, the catalog
and user prompt blocks, the real-time watch flag, the held-reply
promise, the repeat-guard options, and the welcome status line. They
read the host through ``_H`` and the legacy chat module through
``_C``; see threads.py for the pattern. Behavior unchanged.
"""
import json
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from flask import session

from prometheus.legacy import H as _H, C as _C  # noqa: E402

__all__ = ['_PM_WATCH_FLAGGED', '_PM_USER_BLOCK_CACHE', '_PM_USER_BLOCK_LOCK', '_pm_user_block', '_pm_catalog_block', '_pm_ask_log_user', '_pm_watch_flag', '_pm_record_held_reply', '_pm_gate_options', '_pm_open_status_line']


_PM_WATCH_FLAGGED = frozenset({'clarified_repeat', 'empty', 'faulted', 'error',
                               'mismatched', 'failed_by_user'})


_PM_USER_BLOCK_CACHE = {}


_PM_USER_BLOCK_LOCK = threading.Lock()


def _pm_user_block(username):
    """ABOUT THIS USER block for the analysis prompts (2026-10-06,
    audit item 8). Built off-thread on first use (the ask-log scan is
    slow) and cached an hour per user; '' until it is ready."""
    uname = str(username or '').strip()
    if not uname:
        return ''
    now = time.time()
    with _PM_USER_BLOCK_LOCK:
        hit = _PM_USER_BLOCK_CACHE.get(uname)
        if hit and now - hit[0] < 3600:
            return hit[1]
        if hit and hit[1] == '__building__':
            return ''
        _PM_USER_BLOCK_CACHE[uname] = (now, '__building__')

    def _run():
        try:
            import prometheus_memory as _pmm
            prof = _pmm.user_profile(uname, users_doc=(_H.load_users() or {}))
            blk = _pmm.user_block(prof)
        except Exception:
            traceback.print_exc()
            blk = ''
        with _PM_USER_BLOCK_LOCK:
            _PM_USER_BLOCK_CACHE[uname] = (time.time(), blk)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass
    return ''


def _pm_catalog_block(subject, window=None, extra_subjects=()):
    """The corpus catalog's binding figures for a subject (2026-10-05,
    Jenna): every number Profile IQ, Digital Journey IQ, Attribution IQ,
    Brand Partnership IQ or an earlier chat already published on it.
    One cached dict hit plus one small page read; '' on any trouble."""
    names = [str(subject or '').strip()] + [str(x or '').strip() for x in extra_subjects]
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return ''
    try:
        from migration import corpus_catalog as _cc
        parts = []
        for n in names[:3]:
            anchors = _cc.anchors_for(n, window=window, with_ledger=False)
            blk = _cc.anchors_block(anchors)
            if blk:
                parts.append(blk)
        return '\n\n'.join(parts)
    except Exception:
        traceback.print_exc()
        return ''


def _pm_ask_log_user(default='unknown'):
    """The user label an ask is logged under (2026-10-06). Session user,
    then the API-key / job owner the route set on g, then a synthetic
    caller marker so canary and smoke asks never read as a real user
    with no name, else `default`."""
    try:
        u = session.get('username')
        if u:
            return str(u)
    except Exception:
        pass
    try:
        from flask import g as _g, request as _rq
        u = getattr(_g, '_pm_ask_user', None) or getattr(_g, '_pm_api_key_owner', None)
        if u:
            return str(u)
        ua = str(_rq.headers.get('User-Agent') or '')
        caller = str(_rq.headers.get('X-Prometheus-Caller') or '')
        if caller:
            return 'canary:' + re.sub(r'[^a-z0-9_-]+', '', caller.lower())[:40]
        if 'canary' in ua.lower() or 'smoke' in ua.lower() or 'regression' in ua.lower():
            return 'canary'
        if getattr(_g, '_pm_api_key_id', None):
            return 'apikey:' + str(getattr(_g, '_pm_api_key_id'))[:24]
    except Exception:
        pass
    return default


def _pm_watch_flag(user, question, route, outcome, extra=None):
    """Real-time watch feed (2026-10-06): a flagged ask (repeated
    clarify, empty / faulted reply, error, a build drafted for a task,
    a user rejecting the previous answer) lands in
    system/ops/pm_watch_recent.json, bounded to the last 80, which the
    admin System Status tile reads. Off the request thread; never
    raises."""
    try:
        sig = str((extra or {}).get('user_signal') or '')
        if outcome not in _PM_WATCH_FLAGGED and sig not in ('rejected', 'wrong', 'no'):
            return
        if str(user or '').startswith('canary') or str(user or '') in ('', 'unknown', 'replay'):
            return
    except Exception:
        return

    def _run():
        try:
            key = 'system/ops/pm_watch_recent.json'
            doc = _C._pm_s3_json(key, {}) or {}
            items = [x for x in (doc.get('items') or []) if isinstance(x, dict)]
            items.append({'ts': _C._pm_iso_now(), 'user': str(user or '')[:60],
                          'route': str(route or '')[:40], 'outcome': str(outcome or '')[:30],
                          'signal': sig[:20], 'question': str(question or '')[:200]})
            doc['items'] = items[-80:]
            doc['updated_at'] = _C._pm_iso_now()
            _C._pm_s3_put_json(key, doc)
        except Exception:
            traceback.print_exc()
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


def _pm_record_held_reply(username, question, told, reason, kind):
    """A held or repeated reply opens a task with a due time (2026-10-06,
    dead ends): system/ops/held_replies/<day>/<ts>_<id>.json, read by the
    ops watch so an unanswered promise is flagged, never forgotten."""
    try:
        import hashlib as _hl
        now = datetime.now(timezone.utc)
        rid = _hl.sha1(f"{username}|{question}|{now.isoformat()}".encode()).hexdigest()[:10]
        key = (f"system/ops/held_replies/{now.strftime('%Y-%m-%d')}/"
               f"{now.strftime('%H%M%S')}_{rid}.json")
        doc = {'id': rid, 'user': username or '', 'question': str(question or '')[:400],
               'told': str(told or '')[:400], 'reason': str(reason or '')[:400], 'kind': kind,
               'opened_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'due_by': (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),
               'status': 'open'}
        _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=key,
                                Body=json.dumps(doc).encode('utf-8'),
                                ContentType='application/json')
    except Exception:
        traceback.print_exc()


def _pm_gate_options(history):
    """The choices the previous agent turn offered (chip labels, or the
    names in a 'Do you mean for X, or Y?' line), minus utility chips."""
    try:
        prev = None
        for t in reversed(history or []):
            if isinstance(t, dict) and str(t.get('role') or '') == 'agent':
                prev = t
                break
        if not prev:
            return []
        out = []
        meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
        mc = meta.get('memory_confirm') if isinstance(meta.get('memory_confirm'), dict) else {}
        for o in (mc.get('options') or meta.get('options') or []):
            lbl = str((o.get('label') if isinstance(o, dict) else o) or '').strip()
            if lbl and not re.match(r'^(?:something else|email me|send me|cancel|no\b|none\b|skip)', lbl, re.I):
                out.append(lbl)
        if not out:
            m = re.match(r'^\s*Do you mean (?:for )?(.+?)(?:,? or (.+?))?\s*\?\s*$',
                         str(prev.get('text') or ''), re.I | re.S)
            if m:
                out = [g.strip() for g in (m.group(1), m.group(2)) if g and g.strip()]
        return out[:4]
    except Exception:
        return []


def _pm_open_status_line(user, hours=72):
    """One plain sentence for the welcome bubble. '' when nothing to say."""
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        return ''
    user_id = (user.get('email') or user.get('username') or '').strip()
    if not user_id:
        return ''
    import requests as _requests
    try:
        resp = _requests.get(f"{_H.SYNTH_QUEUE_URL}/synth/list",
                             params={'user': user_id, 'limit': 40},
                             headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET}, timeout=8)
        raw = resp.json() if resp.status_code == 200 else []
    except Exception:
        return ''
    if not isinstance(raw, list):
        return ''
    cutoff = time.time() - hours * 3600

    def _ts(doc):
        for k in ('completed_at', 'updated_at', 'requested_at'):
            v = doc.get(k)
            if v:
                try:
                    return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
                except Exception:
                    continue
        return 0

    done, running, reused = [], [], []
    for doc in raw:
        if not isinstance(doc, dict):
            continue
        st = str(doc.get('status') or '')
        subj = str(doc.get('subject') or doc.get('profile_name') or '').strip()
        if st in ('complete', 'completed', 'done'):
            if _ts(doc) >= cutoff and subj:
                done.append(subj)
                if doc.get('existing_window_match') or doc.get('quarter_cuts_reused'):
                    reused.append(subj)
        elif st not in ('failed', 'error', 'canceled', 'cancelled', ''):
            running.append(subj or 'a build')
    parts = []
    if done:
        shown = done[:3]
        more = len(done) - len(shown)
        parts.append("Since you were last here, " + ('1 build finished' if len(done) == 1 else f"{len(done)} builds finished")
                     + ': ' + ', '.join(shown) + (f" and {more} more" if more > 0 else '') + '.')
    if reused:
        parts.append(('One of them was' if len(reused) == 1 else f"{len(reused)} of them were")
                     + ' already on the dashboard for that window, so I reused the file instead of rebuilding it.')
    if running:
        parts.append(('1 build is' if len(running) == 1 else f"{len(running)} builds are") + ' still in motion.')
    return ' '.join(parts)
