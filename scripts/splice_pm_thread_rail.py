#!/usr/bin/env python3
"""Prometheus thread rail, client side (2026-09-28 Jenna: collapsible
left rail with your threads, like Claude / ChatGPT). Python byte-level
splice per index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_pm_thread_rail.html")

# 1. Header: rail toggle button on the far left ------------------------
OLD_HEADER_LEFT = """                <div style="display:flex; align-items:center; gap:12px; min-width:0;">
                    <span class="pm-header-mark" aria-hidden="true">"""
NEW_HEADER_LEFT = """                <div style="display:flex; align-items:center; gap:12px; min-width:0;">
                    <button type="button" id="pmThreadRailBtn" onclick="pmThreadRailToggle()" aria-label="Your threads" title="Your threads" style="background:none; border:1px solid rgba(233,232,225,0.16); border-radius:8px; color:#9AA09B; width:26px; height:26px; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; flex:0 0 auto; padding:0;">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="9" y1="4" x2="9" y2="20"></line></svg>
                    </button>
                    <span class="pm-header-mark" aria-hidden="true">"""

# 2. Body: the rail overlay, first child of prometheusBody -------------
OLD_BODY_OPEN = """            <div id="prometheusBody">
                <div id="prometheusFundsLock\""""
NEW_BODY_OPEN = """            <div id="prometheusBody">
                <div id="pmThreadRail" style="position:absolute; top:0; left:0; bottom:0; width:222px; background:#15252A; border-right:1px solid rgba(233,232,225,0.12); z-index:60; transform:translateX(-105%); transition:transform 0.18s ease; display:flex; flex-direction:column; border-radius:0 0 0 12px;">
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:0.5rem; padding:0.7rem 0.75rem 0.5rem;">
                        <span style="font-size:0.62rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#7C878A;">Threads</span>
                        <button type="button" onclick="pmNewChat()" style="background:rgba(199,242,62,0.14); border:1px solid rgba(199,242,62,0.45); color:#C7F23E; border-radius:999px; padding:0.22rem 0.7rem; font-size:0.68rem; font-weight:700; cursor:pointer; font-family:inherit;">+ New chat</button>
                    </div>
                    <div id="pmThreadList" style="flex:1; overflow-y:auto; padding:0 0.4rem 0.6rem;"></div>
                </div>
                <div id="prometheusFundsLock\""""

# 3. JS: thread state machine before the thread-search block -----------
OLD_JS = """        var _pmSearchQ = '';
        function _pmThreadSearch(q) {"""
NEW_JS = """        // Chat threads (2026-09-28 Jenna): new chats + a collapsible
        // left rail, like Claude. The server routes the existing
        // history GET/POST to the active thread, so switching threads
        // is: save current, activate target, load its history.
        var _pmThreadsIdx = null;
        var _pmRailOpen = false;
        function pmThreadRailToggle() {
            var rail = document.getElementById('pmThreadRail');
            var body = document.getElementById('prometheusBody');
            if (!rail || !body) return;
            if (getComputedStyle(body).position === 'static') {
                body.style.position = 'relative';
            }
            _pmRailOpen = !_pmRailOpen;
            rail.style.transform = _pmRailOpen ? 'translateX(0)'
                                               : 'translateX(-105%)';
            if (_pmRailOpen) _pmThreadsLoad();
        }
        function _pmThreadsLoad() {
            fetch('/api/brief-chat/threads', { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (!d || !d.success) return;
                    _pmThreadsIdx = d;
                    _pmThreadsRender();
                }).catch(function () {});
        }
        function _pmThreadsRender() {
            var list = document.getElementById('pmThreadList');
            if (!list || !_pmThreadsIdx) return;
            var act = _pmThreadsIdx.active;
            var rows = (_pmThreadsIdx.threads || []).map(function (t) {
                var isAct = t.id === act;
                var d = t.updated ? new Date(t.updated) : null;
                var when = (d && !isNaN(d)) ? _walletShortDate(d) : '';
                return '<div onclick="pmSwitchThread(\\'' + t.id + '\\')" style="display:flex; align-items:center; gap:0.4rem; padding:0.45rem 0.5rem; margin:0.15rem 0; border-radius:8px; cursor:pointer; background:' + (isAct ? 'rgba(199,242,62,0.10)' : 'transparent') + '; border:1px solid ' + (isAct ? 'rgba(199,242,62,0.35)' : 'transparent') + ';">'
                     + '<div style="min-width:0; flex:1;">'
                     +   '<div style="color:#E9E8E1; font-size:0.72rem; line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">' + escapeHtml(String(t.title || 'New chat')) + '</div>'
                     +   '<div style="color:#7C878A; font-size:0.6rem; margin-top:0.1rem;">' + escapeHtml(when) + (t.turns ? ' \\u00b7 ' + t.turns + ' turns' : '') + '</div>'
                     + '</div>'
                     + '<button type="button" onclick="pmDeleteThread(\\'' + t.id + '\\', event)" aria-label="Delete thread" title="Delete" style="background:none; border:none; color:#7C878A; cursor:pointer; font-size:0.8rem; padding:0.1rem 0.25rem; flex:0 0 auto;">\\u00d7</button>'
                     + '</div>';
            }).join('');
            list.innerHTML = rows || '<div style="color:#7C878A; font-size:0.68rem; padding:0.4rem 0.5rem;">No threads yet.</div>';
        }
        function _pmResetArmedStates() {
            try { _pmMemoryConfirm = null; } catch (_) {}
            try { _pmPanelOffer = null; } catch (_) {}
            try { _pmAiqConfirm = null; } catch (_) {}
            try { _pmFwConfirm = null; } catch (_) {}
        }
        function _pmAdoptHistory(hist) {
            synthChatHistory = Array.isArray(hist) ? hist : [];
            _pmResetArmedStates();
            _pmSearchQ = '';
            var s = document.getElementById('pmThreadSearch');
            if (s) s.value = '';
            synthChatRerender();
        }
        function pmNewChat() {
            fetch('/api/brief-chat/threads/new', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: '{}',
            }).then(function (r) { return r.json(); })
              .then(function (d) {
                    if (!d || !d.success) return;
                    _pmThreadsIdx = d;
                    _pmAdoptHistory([]);
                    _pmThreadsRender();
                    var inp = document.getElementById('synthChatInput');
                    if (inp) inp.focus();
              }).catch(function () {});
        }
        function pmSwitchThread(tid) {
            if (_pmThreadsIdx && _pmThreadsIdx.active === tid) return;
            // Persist the current thread before leaving it.
            try {
                fetch('/api/brief-chat/history', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ history: synthChatHistory }),
                });
            } catch (_) {}
            fetch('/api/brief-chat/threads/activate', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: tid }),
            }).then(function (r) { return r.json(); })
              .then(function (d) {
                    if (!d || !d.success) return;
                    if (_pmThreadsIdx) _pmThreadsIdx.active = tid;
                    _pmAdoptHistory(d.history || []);
                    _pmThreadsLoad();
              }).catch(function () {});
        }
        function pmDeleteThread(tid, ev) {
            if (ev) ev.stopPropagation();
            if (!confirm('Delete this thread? Its conversation goes with it.')) return;
            fetch('/api/brief-chat/threads/delete', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: tid }),
            }).then(function (r) { return r.json(); })
              .then(function (d) {
                    if (!d || !d.success) return;
                    _pmThreadsIdx = d;
                    if (d.history !== null && d.history !== undefined) {
                        _pmAdoptHistory(d.history);
                    }
                    _pmThreadsRender();
              }).catch(function () {});
        }

        var _pmSearchQ = '';
        function _pmThreadSearch(q) {"""


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
for old, new, desc in (
    (OLD_HEADER_LEFT, NEW_HEADER_LEFT, "header rail button"),
    (OLD_BODY_OPEN, NEW_BODY_OPEN, "rail markup"),
    (OLD_JS, NEW_JS, "threads js"),
):
    src = splice(src, old, new, desc)
INDEX.write_text(src, encoding="utf-8")
print("spliced 3 changes")
