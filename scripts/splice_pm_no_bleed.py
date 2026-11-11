#!/usr/bin/env python3
"""A turn belongs to the thread it was asked in (Jenna 2026-11-11, Armine
Meseldzhyan at Lionsgate: "sometimes when I start a new chat with
Prometheus and ask a question about a new profile, such as Cruel Summer,
it will pull in information from past chats, like Mad Men, and switch
the subject").

The widget half of prometheus/thread_scope.py. Her 2026-11-10 threads show
the widget writing every reply and every finished read into whichever
thread was on screen when it arrived: the Mad Men "On it" line landed in
the Cruel Summer thread she had opened while it ran, the Cruel Summer read
landed under the Mad Men question, and two replies landed in fresh panes
that then became threads of their own.

What this splice changes in templates/index.html:

  * the ask in flight and every poller carry the thread they started from
    (the origin). A turn whose origin is not the thread on screen is saved
    to its own thread; the pane shows one line saying where it went;
  * every request of an ask names the origin thread and sends the origin
    thread's turns, whatever the pane shows by then;
  * whatever a reply armed (a clarify step, an offer, a brief) stays with
    the thread that asked; a pending brief is parked when the pane leaves
    its thread and comes back with it; Approve never runs another
    thread's brief;
  * carried_from is gone: a thread the page opened carries nothing over
    from the one it left;
  * the pane moves to another thread only when the reader or the page
    moved it and that thread's turns came with it; a thread the reader's
    list lost never swaps the pane to another chat;
  * leaving every project returns to the general chat only when it was
    used in the last fifteen minutes (the server says ``fresh`` otherwise);
  * the header always says which chat this is.

Python byte-level splice per index-html-safety.mdc: every anchor must
occur exactly once. Idempotent: a second run reports already applied.
"""
from __future__ import annotations

import sys
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_pm_no_bleed.html")
MARK = "var _pmAskOrigin = null;      // the foreground ask: { tid, hist, q }"


class Splicer:
    def __init__(self, src):
        self.src = src
        self.done = []

    def rep(self, old, new, desc):
        n = self.src.count(old)
        if n != 1:
            raise RuntimeError(f"[{desc}] anchor found {n}x (want 1)")
        self.src = self.src.replace(old, new)
        self.done.append(desc)

    def region(self, start, end, desc):
        """(i, j) of the one region from ``start`` up to the next ``end``."""
        n = self.src.count(start)
        if n != 1:
            raise RuntimeError(f"[{desc}] region start found {n}x (want 1)")
        i = self.src.index(start)
        j = self.src.index(end, i + len(start))
        return i, j

    def in_region(self, start, end, pairs, desc, counts=None):
        """Replace every ``old`` with ``new`` inside one region. ``counts``
        maps old -> the exact number expected (None: at least one)."""
        i, j = self.region(start, end, desc)
        body = self.src[i:j]
        for old, new in pairs:
            n = body.count(old)
            want = (counts or {}).get(old)
            if want is not None and n != want:
                raise RuntimeError(f"[{desc}] {old!r} found {n}x in region (want {want})")
            if want is None and n < 1:
                raise RuntimeError(f"[{desc}] {old!r} not found in region")
            body = body.replace(old, new)
        self.src = self.src[:i] + body + self.src[j:]
        self.done.append(desc)


# --------------------------------------------------------------------------
# 1. the origin machinery + the two push functions
# --------------------------------------------------------------------------
OLD_PUSH = """        function synthChatPushTurn(role, text) {
            text = _synthChatAgentText(role, text);
            synthChatHistory.push({ role: role, text: text, ts: new Date().toISOString(), turn_id: _pmTurnId() });
            _synthChatBumpActivity();
            synthChatRerender();
            _pmHistorySave();
        }
"""
NEW_PUSH = """        // A turn belongs to the thread it was asked in (Jenna 2026-11-11, Armine at Lionsgate: "it will pull in
        // information from past chats, like Mad Men, and switch the subject"). Every reply and every finished job
        // used to be written into whichever thread was on screen when it arrived: her Mad Men "On it" line landed
        // in the Cruel Summer thread she had opened while it ran, and the Cruel Summer read landed under the Mad
        // Men question. The ask in flight and every poller now carry the thread they started from (the origin).
        // A turn whose origin is not the thread on screen is saved to its own thread, and the pane only says
        // where it went.
        var _pmAskOrigin = null;      // the foreground ask: { tid, hist, q }
        var _pmPushOrigin = null;     // set for the length of one push by a poller that knows its thread
        var _pmPaneTid = '';          // the thread the pane showed as of the last adopt ('' for a fresh pane)
        var _pmDraftsByThread = {};   // a pending brief waits on the thread it was drafted in
        var _pmDraftHere = null;      // the brief the pane restored for the thread on screen: { tid, draft, batch, shown }
        var _pmElsewhereNotes = [];   // "the answer is in the chat it was asked in" lines for this pane
        function _pmHereTid() {
            try {
                return (typeof _pmThreadsIdx !== 'undefined' && _pmThreadsIdx && _pmThreadsIdx.active &&
                        !(typeof _pmPendingNew !== 'undefined' && _pmPendingNew))
                    ? String(_pmThreadsIdx.active) : '';
            } catch (_) { return ''; }
        }
        function _pmOriginNow() {
            // the thread work started from: a poller's own, the ask in flight's, else the one on screen
            if (_pmPushOrigin && _pmPushOrigin.tid) return _pmPushOrigin;
            if (_pmAskOrigin && _pmAskOrigin.tid) return _pmAskOrigin;
            var tid = _pmHereTid();
            return tid ? { tid: tid, hist: synthChatHistory, q: '' } : null;
        }
        function _pmOriginMoved(o) {
            // the pane no longer shows the thread this work belongs to
            return !!(o && o.tid && _pmHereTid() !== String(o.tid));
        }
        function _pmAskHist() {
            // the turns of the thread the ask in flight belongs to, wherever the pane is now
            var o = _pmAskOrigin;
            if (o && o.tid && Array.isArray(o.hist) && _pmOriginMoved(o)) return o.hist;
            return synthChatHistory;
        }
        function _pmWithOrigin(o, fn) {
            var prev = _pmPushOrigin;
            _pmPushOrigin = (o && o.tid) ? o : null;
            try { return fn(); } finally { _pmPushOrigin = prev; }
        }
        function _pmPushTurnFor(o, role, text) {
            return _pmWithOrigin(o, function () { synthChatPushTurn(role, text); });
        }
        function _pmPushMetaFor(o, role, text, meta) {
            return _pmWithOrigin(o, function () { _synthChatPushTurnWithMeta(role, text, meta); });
        }
        function _pmPushGuidanceFor(o, text, data) {
            return _pmWithOrigin(o, function () { _pmPushGuidanceTurn(text, data); });
        }
        function _pmThreadTitleOf(tid) {
            try {
                var th = ((_pmThreadsIdx && _pmThreadsIdx.threads) || []).filter(function (t) { return t && String(t.id) === String(tid); })[0];
                return (th && th.title) ? String(th.title) : '';
            } catch (_) { return ''; }
        }
        function _pmElsewhereDraw(box, note) {
            var n = document.createElement('div');
            n.className = 'pm-msg pm-msg-system';
            n.setAttribute('data-pm-system', '1');
            n.setAttribute('data-pm-elsewhere', String(note.tid));
            n.setAttribute('role', 'button');
            n.style.cssText = 'text-align:center; color:#9AA09B; font-size:0.7rem; letter-spacing:0.01em; padding:0.45rem 0.6rem 0.3rem; cursor:pointer; text-decoration:underline; text-underline-offset:2px;';
            n.textContent = note.text;
            n.onclick = function () { try { pmSwitchThread(String(note.tid)); } catch (_) {} };
            box.appendChild(n);
        }
        function _pmElsewhereRedraw(box) {
            // the lines survive a rerender of the pane they were shown in
            for (var i = 0; i < _pmElsewhereNotes.length; i++) { _pmElsewhereDraw(box, _pmElsewhereNotes[i]); }
        }
        function _pmElsewhereNote(o) {
            // one line per thread: the reply is in the chat that asked, one click away
            try {
                if (!o || !o.tid) return;
                for (var i = 0; i < _pmElsewhereNotes.length; i++) { if (_pmElsewhereNotes[i].tid === String(o.tid)) return; }
                var name = String(o.q || _pmThreadTitleOf(o.tid) || '').replace(/\\s+/g, ' ').trim();
                if (name === 'New chat') name = '';
                if (name.length > 60) name = name.slice(0, 57).trim() + '...';
                var note = { tid: String(o.tid),
                             text: name ? ('The answer to "' + name + '" is in the chat it was asked in. Open that chat.')
                                        : 'A reply just landed in the chat it was asked in. Open that chat.' };
                _pmElsewhereNotes.push(note);
                var box = document.getElementById('synthChatMessages');
                if (!box) return;
                _pmElsewhereDraw(box, note);
                box.scrollTop = box.scrollHeight;
            } catch (_) {}
        }
        function _pmDeliverElsewhere(o, turn) {
            // the reply goes to the thread that asked: its turns, the server's copy of that thread; never this pane
            try { if (Array.isArray(o.hist) && o.hist !== synthChatHistory) o.hist.push(turn); } catch (_) {}
            try {
                fetch('/api/brief-chat/history', {
                    method: 'POST', credentials: 'same-origin', keepalive: true,
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ history: (Array.isArray(o.hist) && o.hist.length && o.hist !== synthChatHistory) ? o.hist : [turn],
                                           thread_id: String(o.tid) })
                }).then(function () { try { _pmThreadsLoad(); } catch (_) {} }).catch(function () {});
            } catch (_) {}
            _pmElsewhereNote(o);
        }
        function _pmDeliverTurn(turn) {
            var o = (_pmPushOrigin && _pmPushOrigin.tid) ? _pmPushOrigin : _pmAskOrigin;
            if (o && o.tid && _pmOriginMoved(o)) { _pmDeliverElsewhere(o, turn); return false; }
            synthChatHistory.push(turn);
            _synthChatBumpActivity();
            synthChatRerender();
            _pmHistorySave();
            try { _pmThreadHeaderRefresh(); } catch (_) {}
            return true;
        }
        function _pmAskOriginPin(text) {
            // the ask is pinned to its thread once the thread exists (a fresh pane mints one on the first message)
            try {
                var o = _pmAskOrigin;
                if (o && !o.tid) { o.tid = _pmHereTid(); o.hist = synthChatHistory; o.q = String(text || '').slice(0, 80); }
            } catch (_) {}
        }
        function _pmAskOriginBeginHere() {
            // work started by a button outside the composer (Approve) belongs to the thread on screen; an ask
            // already in flight keeps its own origin
            if (_pmAskOrigin && _pmAskOrigin.tid) return null;
            var own = { tid: _pmHereTid(), hist: synthChatHistory, q: '' };
            _pmAskOrigin = own;
            return own;
        }
        function _pmAskOriginEnd(own) {
            if (!own) return;
            try {
                if (own && own.tid && _pmAskOrigin === own && _pmOriginMoved(own)) {
                    // the reply went to the thread that asked: whatever it armed (a clarify step, an offer, a brief)
                    // waits there, never in the pane the reader moved to. A brief the pane had restored for the
                    // thread now on screen is that thread's and stays.
                    var here = _pmDraftHere;
                    var untouched = !!(here && synthChatCurrentDraft === here.draft && synthChatBatchDrafts === here.batch);
                    if (!untouched) {
                        _pmDraftPark(own.tid);
                        if (here) {
                            _pmDraftsByThread[here.tid] = { draft: here.draft, batch: here.batch, shown: here.shown };
                            _pmDraftRestore(here.tid);
                        }
                    }
                    _pmReleaseArmedSteps();
                }
            } catch (_) {}
            if (_pmAskOrigin === own) _pmAskOrigin = null;
        }
        function _pmDraftPark(tid) {
            // a pending brief belongs to the thread it was drafted in: out of the pane when the pane leaves
            var panel = document.getElementById('synthChatBriefPreview');
            var has = !!(synthChatCurrentDraft || (synthChatBatchDrafts && synthChatBatchDrafts.length));
            if (has && tid) {
                _pmDraftsByThread[String(tid)] = { draft: synthChatCurrentDraft, batch: synthChatBatchDrafts,
                                                   shown: !!(panel && panel.style.display !== 'none') };
            }
            synthChatCurrentDraft = null;
            synthChatBatchDrafts = null;
            _pmDraftHere = null;
            if (panel) panel.style.display = 'none';
        }
        function _pmDraftRestore(tid) {
            // and back when the pane returns to that thread
            var d = tid ? _pmDraftsByThread[String(tid)] : null;
            if (!d) return;
            delete _pmDraftsByThread[String(tid)];
            synthChatCurrentDraft = d.draft || null;
            synthChatBatchDrafts = d.batch || null;
            _pmDraftHere = { tid: String(tid), draft: synthChatCurrentDraft, batch: synthChatBatchDrafts, shown: !!d.shown };
            if (d.shown && (synthChatCurrentDraft || (synthChatBatchDrafts && synthChatBatchDrafts.length))) {
                try { _synthChatShowBrief(synthChatCurrentDraft || {}); } catch (_) {}
            }
        }
        function synthChatPushTurn(role, text) {
            text = _synthChatAgentText(role, text);
            _pmDeliverTurn({ role: role, text: text, ts: new Date().toISOString(), turn_id: _pmTurnId() });
        }
"""

OLD_PUSH_META = """            var turn = { role: role, text: text, ts: new Date().toISOString(), meta: meta || {}, turn_id: _pmTurnId() };
            synthChatHistory.push(turn);
            _synthChatBumpActivity();
            synthChatRerender();
            _pmHistorySave();
        }
"""
NEW_PUSH_META = """            var turn = { role: role, text: text, ts: new Date().toISOString(), meta: meta || {}, turn_id: _pmTurnId() };
            _pmDeliverTurn(turn);
        }
"""

# --------------------------------------------------------------------------
# 2. every request names the origin thread; carried_from is retired
# --------------------------------------------------------------------------
OLD_V1 = """            try {
                if (!b.thread_id && typeof _pmThreadsIdx !== 'undefined' && _pmThreadsIdx && _pmThreadsIdx.active) {
                    b.thread_id = String(_pmThreadsIdx.active);
                }
            } catch (_) {}
            try {
                if (_pmCarried && _pmCarried.from && _pmCarried.count < 4 &&
                    (Date.now() - _pmCarried.at) < 30 * 60 * 1000) {
                    b.carried_from = _pmCarried.from;
                    _pmCarried.count++;
                } else if (_pmCarried) {
                    _pmCarried = null;
                }
            } catch (_) {}
            return { url: '/api/prometheus/v1/ask', body: b };
"""
NEW_V1 = """            try {
                // the ask in flight keeps the thread it started on, whatever the pane shows by the time a later
                // step of it posts (2026-11-11); otherwise the thread this tab is on
                if (!b.thread_id && typeof _pmAskOrigin !== 'undefined' && _pmAskOrigin && _pmAskOrigin.tid) {
                    b.thread_id = String(_pmAskOrigin.tid);
                }
                if (!b.thread_id && typeof _pmThreadsIdx !== 'undefined' && _pmThreadsIdx && _pmThreadsIdx.active) {
                    b.thread_id = String(_pmThreadsIdx.active);
                }
            } catch (_) {}
            // nothing rides in from another thread (2026-11-11): the thread a page switch left used to be named
            // here for the first asks in the new one, and its turns answered the new chat's question
            return { url: '/api/prometheus/v1/ask', body: b };
"""
OLD_CARRIED_DECL = "        var _pmCarried = null;   // the thread an automatic switch left: { from, at, count }\n"
OLD_CARRIED_FRESH = """            _pmPendingNew = true;
            _pmCarried = null;
            window._pmFreshOpenedAt = Date.now();
"""
NEW_CARRIED_FRESH = """            _pmPendingNew = true;
            window._pmFreshOpenedAt = Date.now();
"""
OLD_CARRIED_NEW = """        function pmNewChat(done) {
            _pmCarried = null;   // a fresh chat starts clean (2026-10-30)
"""
NEW_CARRIED_NEW = """        function pmNewChat(done) {
"""
OLD_CARRIED_SWITCH = """        function pmSwitchThread(tid) {
            _pmCarried = null;   // a thread opened by hand carries nothing over (2026-10-30)
"""
NEW_CARRIED_SWITCH = """        function pmSwitchThread(tid) {
"""

# --------------------------------------------------------------------------
# 3. the follow: no move mid-ask, only a real move adopts, fresh when the
#    general chat is stale, nothing carried
# --------------------------------------------------------------------------
OLD_FOLLOW_HEAD = """            var key = ident ? ident.subject_key : '';
            if (key === _pmFollow.key) return;
            if (_pmFollow.inflight) { _pmFollow.pending = true; return; }
"""
NEW_FOLLOW_HEAD = """            var key = ident ? ident.subject_key : '';
            if (key === _pmFollow.key) return;
            // an ask in flight finishes in the thread it was asked in before the pane follows the page
            // (2026-11-11); the two-second check picks the move up the moment the reply lands
            try { if (typeof synthChatInflightController !== 'undefined' && synthChatInflightController) return; } catch (_) {}
            if (_pmFollow.inflight) { _pmFollow.pending = true; return; }
"""
OLD_FOLLOW_BODY = """                    _pmFollow.key = key;
                    var _prevActive = _pmThreadsIdx && _pmThreadsIdx.active;
                    _pmThreadsIdx = { active: d.active, threads: d.threads || [] };
                    // a fresh pane stays a fresh pane unless the conversation actually moved
                    // to the project's thread (2026-11-06)
                    if (_pmPendingNew && !(d.switched || d.created)) _pmThreadsIdx.active = null;
                    if (d.switched || d.created) {
                        _pmPendingNew = false;
                        window._pmLastActivityAt = Date.now();
                        // the conversation this switch left rides as context for
                        // the first asks in the new thread (2026-10-30)
                        _pmCarried = (_prevActive && _prevActive !== d.active)
                            ? { from: _prevActive, at: Date.now(), count: 0 } : null;
                        _pmAdoptHistory(d.history || []);
                        _pmThreadSwitchNote(d.binding && d.binding.subject_label);
                    }
"""
NEW_FOLLOW_BODY = """                    _pmFollow.key = key;
                    var _prevActive = _pmThreadsIdx && _pmThreadsIdx.active;
                    if (d.fresh) {
                        // no project open and no general chat used in the last fifteen minutes: an empty pane,
                        // no thread until the first message (2026-11-11; the pane used to drop into the most
                        // recently updated general thread, however old)
                        if (!_pmPendingNew) _pmGoFresh(null, false);
                        if (Array.isArray(d.threads) && _pmThreadsIdx) _pmThreadsIdx.threads = d.threads;
                        _pmThreadsRender();
                        _pmThreadHeaderRefresh();
                        if (_pmFollow.pending) { _pmFollow.pending = false; _pmThreadFollow(); }
                        return;
                    }
                    // the pane moves only when the conversation moved (2026-11-11): the server's active pointer
                    // can be another tab's, and taking it without that thread's turns sent this tab's saves to a
                    // thread it was not showing
                    var _moved = !!(d.switched || d.created);
                    _pmThreadsIdx = { active: _moved ? d.active : (_prevActive || null), threads: d.threads || [] };
                    // a fresh pane stays a fresh pane unless the conversation actually moved
                    // to the project's thread (2026-11-06)
                    if (_pmPendingNew && !_moved) _pmThreadsIdx.active = null;
                    if (_moved) {
                        _pmPendingNew = false;
                        window._pmLastActivityAt = Date.now();
                        // a thread the page opened carries nothing over from the one it left (2026-11-11)
                        _pmAdoptHistory(d.history || []);
                        _pmThreadSwitchNote(d.binding && d.binding.subject_label);
                    }
"""

# --------------------------------------------------------------------------
# 4. a thread the reader's list lost never swaps the pane to another chat
# --------------------------------------------------------------------------
OLD_RESYNC = """        function _pmHistoryResync() {
            // the thread this tab was on is gone (deleted or rolled off the rail elsewhere): take the server's active one
            fetch('/api/brief-chat/history?peek=1', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (!d || !Array.isArray(d.history)) return;
                    _pmHistoryTrack(d, true);
                    _pmAdoptHistory(d.history);
                    if (d.rev) _pmHistoryRev = String(d.rev);
                    try { _pmThreadsLoad(true); } catch (_) {}
                }).catch(function () {});
        }
"""
NEW_RESYNC = """        var _pmResyncTimer = null;
        function _pmHistoryResync() {
            // The thread this tab is on is not in the reader's list (deleted elsewhere, or its entry was lost). The
            // pane keeps its own conversation and never takes another thread's (2026-11-11): it used to adopt the
            // server's ACTIVE thread on the spot, so an earlier chat's turns appeared under a question still being
            // typed. With nothing typed the pane is simply fresh. With turns on screen it stays as it is: the next
            // ask re-lists the thread under its own id on the server, and the save is tried once more after it.
            try {
                var typed = Array.isArray(synthChatHistory) && synthChatHistory.some(function (t) { return t && t.role === 'user'; });
                if (!typed) { if (!_pmPendingNew) _pmGoFresh(null, false); return; }
                if (_pmResyncTimer) return;
                _pmResyncTimer = setTimeout(function () {
                    _pmResyncTimer = null;
                    try { _pmHistorySave({ leaving: true }); } catch (_) {}
                }, 6000);
            } catch (_) {}
        }
"""

# --------------------------------------------------------------------------
# 5. adopt: the brief parks with its thread; the pane knows its thread
# --------------------------------------------------------------------------
OLD_ADOPT = """        function _pmAdoptHistory(hist) {
            synthChatHistory = Array.isArray(hist) ? hist : [];
            _pmHistoryRev = null;
            _pmResetArmedStates();
            _pmSearchQ = '';
            var s = document.getElementById('pmThreadSearch');
            if (s) s.value = '';
            synthChatRerender();
        }
"""
NEW_ADOPT = """        function _pmAdoptHistory(hist) {
            // a pending brief waits on the thread it was drafted in: parked when the pane leaves that thread,
            // back when the pane returns to it; Approve never runs another thread's brief (2026-11-11)
            try { _pmDraftPark(_pmPaneTid); } catch (_) {}
            synthChatHistory = Array.isArray(hist) ? hist : [];
            _pmHistoryRev = null;
            _pmResetArmedStates();
            _pmSearchQ = '';
            var s = document.getElementById('pmThreadSearch');
            if (s) s.value = '';
            try { _pmPaneTid = _pmHereTid(); _pmElsewhereNotes = []; } catch (_) {}
            synthChatRerender();
            try { _pmDraftRestore(_pmPaneTid); } catch (_) {}
            try { _pmThreadHeaderRefresh(); } catch (_) {}
        }
"""
OLD_ENSURE = """                    _pmHistoryRev = null;
                    try { var idn = _pmProjectIdentity(); _pmFollow.key = idn ? idn.subject_key : ''; } catch (_) {}
                    _pmThreadsRender();
                    return String(d.active);
"""
NEW_ENSURE = """                    _pmHistoryRev = null;
                    try { _pmPaneTid = String(d.active); } catch (_) {}   // the pane is this thread from its first message on (2026-11-11)
                    try { var idn = _pmProjectIdentity(); _pmFollow.key = idn ? idn.subject_key : ''; } catch (_) {}
                    _pmThreadsRender();
                    return String(d.active);
"""
OLD_INIT = """                    synthChatHistory = (d && d.history) || [];
                    _pmHistoryTrack(d);
                    window._pmLastActivityAt = Date.now();
"""
NEW_INIT = """                    synthChatHistory = (d && d.history) || [];
                    _pmHistoryTrack(d);
                    try { _pmPaneTid = _pmHereTid(); _pmThreadHeaderRefresh(); } catch (_) {}   // the header names the thread a load resumed (2026-11-11)
                    window._pmLastActivityAt = Date.now();
"""

# --------------------------------------------------------------------------
# 6. the header says which chat this is
# --------------------------------------------------------------------------
OLD_HEADER = """                var label = _pmThreadBindingLabel();
                sub.textContent = label ? ('On ' + label) : _PM_FOLLOW_SUB_DEFAULT;
                sub.title = label ? ('This chat follows ' + label + '. Open another project and the chat follows it; open a thread from the rail to go back.') : '';
            } catch (_) {}
        }
"""
NEW_HEADER = """                var label = _pmThreadBindingLabel();
                sub.textContent = label ? ('On ' + label) : _PM_FOLLOW_SUB_DEFAULT;
                sub.title = label ? ('This chat follows ' + label + '. Open another project and the chat follows it; open a thread from the rail to go back.') : '';
                // a chat with no project still says which chat it is (2026-11-11): its own title, or New chat for
                // a pane with no thread yet, so a resumed thread never passes for a new one
                if (!label) { try { var _nm = _pmThreadNameHere(); if (_nm) { sub.textContent = _nm.text; sub.title = _nm.tip; } } catch (_) {} }
            } catch (_) {}
        }
        function _pmThreadNameHere() {
            var fresh = { text: 'New chat', tip: 'A new chat. Nothing carries over from your earlier chats; they are in Chats & Tasks.' };
            var tid = _pmHereTid();
            if (!tid) return fresh;
            var t = _pmThreadTitleOf(tid);
            if (!t || t === 'New chat') {
                // the rail has not titled it yet: the first question on screen names it
                t = '';
                for (var i = 0; i < synthChatHistory.length; i++) {
                    var h = synthChatHistory[i];
                    if (h && h.role === 'user' && h.text) { t = String(h.text); break; }
                }
            }
            t = String(t || '').replace(/\\s+/g, ' ').trim();
            if (!t) return fresh;
            return { text: 'Chat: ' + t, tip: 'This chat: ' + t + '. Your other chats are in Chats & Tasks.' };
        }
"""

# --------------------------------------------------------------------------
# 7. the ask keeps its thread: submit and approve
# --------------------------------------------------------------------------
OLD_SUBMIT = """        async function synthChatSubmit() {
            var _stopBtn = document.getElementById('synthChatSubmitBtn');
            if (_stopBtn && _stopBtn.dataset.pmStop === '1') {
"""
NEW_SUBMIT = """        async function synthChatSubmit() {
            // the ask keeps the thread it was sent from until its last reply lands (2026-11-11)
            var _sb = document.getElementById('synthChatSubmitBtn');
            if (_sb && _sb.dataset.pmStop === '1') return _synthChatSubmitAsk();   // Stop: the ask in flight keeps its origin
            var _own = { tid: '', hist: null, q: '' };
            _pmAskOrigin = _own;
            try { return await _synthChatSubmitAsk(); }
            finally { _pmAskOriginEnd(_own); }
        }
        async function _synthChatSubmitAsk() {
            var _stopBtn = document.getElementById('synthChatSubmitBtn');
            if (_stopBtn && _stopBtn.dataset.pmStop === '1') {
"""
OLD_SUBMIT_PIN = """            // a fresh pane gets its thread now, on the first message (2026-11-06)
            try { await _pmEnsureThread(); } catch (_) {}
            synthChatPushTurn('user', text);
"""
NEW_SUBMIT_PIN = """            // a fresh pane gets its thread now, on the first message (2026-11-06)
            try { await _pmEnsureThread(); } catch (_) {}
            _pmAskOriginPin(text);   // and the ask is this thread's from here on (2026-11-11)
            synthChatPushTurn('user', text);
"""
OLD_APPROVE = """        async function synthChatApproveBrief(opts) {
            // 2026-09-04 Jenna existing_match chip UX. opts.forceNewBuild
"""
NEW_APPROVE = """        async function synthChatApproveBrief(opts) {
            if (!(opts && opts._pmRun)) {
                // the build and its status lines stay in the thread the brief was drafted in (2026-11-11): the
                // approve runs once more under an origin pinned to the thread on screen (an ask already in flight
                // keeps its own), and whatever it arms stays with that thread
                var _apOwn = null;
                try { _apOwn = _pmAskOriginBeginHere(); } catch (_) {}
                try { return await synthChatApproveBrief(Object.assign({}, opts || {}, { _pmRun: true })); }
                finally { try { _pmAskOriginEnd(_apOwn); } catch (_) {} }
            }
            // 2026-09-04 Jenna existing_match chip UX. opts.forceNewBuild
"""

# --------------------------------------------------------------------------
# 8. the request bodies send the origin thread's turns
# --------------------------------------------------------------------------
OLD_HIST_ANALYZE = "                    history: synthChatHistory.slice(0, -1).slice(-12),\n"
NEW_HIST_ANALYZE = "                    history: _pmAskHist().slice(0, -1).slice(-12),   // the asking thread's turns, wherever the pane is (2026-11-11)\n"
OLD_HIST_INTERPRET = """                    text: _interpretText,
                    history: synthChatHistory.slice(0, -1)
                };
"""
NEW_HIST_INTERPRET = """                    text: _interpretText,
                    history: _pmAskHist().slice(0, -1)   // the asking thread's turns, wherever the pane is (2026-11-11)
                };
"""
OLD_HIST_DECK = "                    history: synthChatHistory.slice(-14)\n"
NEW_HIST_DECK = "                    history: _pmAskHist().slice(-14)   // the asking thread's turns, wherever the pane is (2026-11-11)\n"

# --------------------------------------------------------------------------
# 9. pollers carry their thread
# --------------------------------------------------------------------------
PO_LINE = "            var _po = _pmOriginNow();   // the thread this was started from; its lines land there (2026-11-11)\n"
PUSH_PAIRS = [("synthChatPushTurn(", "_pmPushTurnFor(_po, "),
              ("_synthChatPushTurnWithMeta(", "_pmPushMetaFor(_po, ")]
FN_END = "\n        }\n"

OLD_TRACK = """        function _pmTrackReadJob(id) {
            var jobs = _pmReadJobsLoad().filter(function(j) {
                return j && j.id !== id;
            });
            jobs.push({ id: id, ts: Date.now() });
            _pmReadJobsSave(jobs);
        }
"""
NEW_TRACK = """        function _pmTrackReadJob(id, tid) {
            // the entry names the thread the read was asked in (2026-11-11): the list is shared by every tab of
            // this browser, and a resumed poll used to write the read into whatever thread that tab was showing
            var prev = null;
            var jobs = _pmReadJobsLoad().filter(function(j) {
                if (j && j.id === id) { prev = j; return false; }
                return !!j;
            });
            jobs.push({ id: id, ts: Date.now(), tid: String(tid || (prev && prev.tid) || '') });
            _pmReadJobsSave(jobs);
        }
"""
OLD_READ_HEAD = """        async function _pmPollReadJob(jobId) {
            _pmTrackReadJob(jobId);
"""
NEW_READ_HEAD = """        async function _pmPollReadJob(jobId, originTid) {
            // the thread the read was asked in: named by a resume after a reload, else the ask in flight's (2026-11-11)
            var _po = originTid ? { tid: String(originTid), hist: null, q: '' } : _pmOriginNow();
            _pmTrackReadJob(jobId, _po && _po.tid);
"""
OLD_RESUME = """                if (j && j.id && (now - (j.ts || 0)) < 1200000) {
                    try { _pmPollReadJob(j.id); } catch (_) {}
"""
NEW_RESUME = """                if (j && j.id && (now - (j.ts || 0)) < 1200000) {
                    try { _pmPollReadJob(j.id, j.tid || ''); } catch (_) {}
"""
OLD_PENDING_Q = """                        if (cur === 'complete' && data.pending_question) {
"""
NEW_PENDING_Q = """                        if (cur === 'complete' && data.pending_question && _pmOriginMoved(_po)) {
                            // the reader is in another chat now: the question waits in the thread that asked it,
                            // never asked in this one (2026-11-11)
                            _pmPushTurnFor(_po, 'agent', 'The profile is ready. Your original question is waiting here: "' +
                                String(data.pending_question) + '". Send it again and I will answer it on the new profile.');
                        } else if (cur === 'complete' && data.pending_question) {
"""
OLD_DECK_RELEASE = """                try { _pmShowNotifyOffer(job, 'deck'); } catch (_) {}
                var waited = 0;
"""
NEW_DECK_RELEASE = """                try { _pmShowNotifyOffer(job, 'deck'); } catch (_) {}
                // the build is on its own from here (up to ten minutes): its lines carry _po, and the ask in
                // flight is over, so nothing typed meanwhile is filed under this thread (2026-11-11)
                try { if (_pmAskOrigin === _po) _pmAskOrigin = null; } catch (_) {}
                var waited = 0;
"""
OLD_UPLOAD_TID = """                    var _tid = (_pmThreadsIdx && _pmThreadsIdx.active) ? String(_pmThreadsIdx.active) : '';
                    if (_tid) fd.append('thread_id', _tid);
"""
NEW_UPLOAD_TID = """                    var _tid = (_po && _po.tid) ? String(_po.tid) : ((_pmThreadsIdx && _pmThreadsIdx.active) ? String(_pmThreadsIdx.active) : '');
                    if (_tid) fd.append('thread_id', _tid);
"""
OLD_UPLOAD_ENSURE = "            try { await _pmEnsureThread(); } catch (_) {}   // a file joins a thread, so a fresh pane gets one first (2026-11-06)\n"
NEW_UPLOAD_ENSURE = OLD_UPLOAD_ENSURE + PO_LINE

# --------------------------------------------------------------------------
# 10. the "it is in the chat it was asked in" line survives a rerender
# --------------------------------------------------------------------------
OLD_RERENDER_EMPTY = """                    '</div></div>';
                return;
            }
            _synthChatActiveOptions = null;
"""
NEW_RERENDER_EMPTY = """                    '</div></div>';
                try { _pmElsewhereRedraw(box); } catch (_) {}
                return;
            }
            _synthChatActiveOptions = null;
"""
OLD_RERENDER_TAIL = """            var _lastTurnEl = box.lastElementChild;
            _pmReattachLiveBubbles(box);
"""
NEW_RERENDER_TAIL = """            var _lastTurnEl = box.lastElementChild;
            _pmReattachLiveBubbles(box);
            try { _pmElsewhereRedraw(box); } catch (_) {}
"""


def apply(src):
    s = Splicer(src)
    s.rep(OLD_PUSH, NEW_PUSH, 'origin machinery + synthChatPushTurn')
    s.rep(OLD_PUSH_META, NEW_PUSH_META, '_synthChatPushTurnWithMeta delivers by origin')
    s.rep(OLD_V1, NEW_V1, '_pmV1Request names the origin thread; carried_from retired')
    s.rep(OLD_CARRIED_DECL, '', '_pmCarried declaration removed')
    s.rep(OLD_CARRIED_FRESH, NEW_CARRIED_FRESH, '_pmGoFresh: no carried state')
    s.rep(OLD_CARRIED_NEW, NEW_CARRIED_NEW, 'pmNewChat: no carried state')
    s.rep(OLD_CARRIED_SWITCH, NEW_CARRIED_SWITCH, 'pmSwitchThread: no carried state')
    s.rep(OLD_FOLLOW_HEAD, NEW_FOLLOW_HEAD, '_pmThreadFollow waits for the ask in flight')
    s.rep(OLD_FOLLOW_BODY, NEW_FOLLOW_BODY, '_pmThreadFollow: fresh, real moves only, nothing carried')
    s.rep(OLD_RESYNC, NEW_RESYNC, '_pmHistoryResync never adopts another thread')
    s.rep(OLD_ADOPT, NEW_ADOPT, '_pmAdoptHistory parks the brief and tracks the pane thread')
    s.rep(OLD_ENSURE, NEW_ENSURE, '_pmEnsureThread sets the pane thread')
    s.rep(OLD_INIT, NEW_INIT, 'init sets the pane thread')
    s.rep(OLD_HEADER, NEW_HEADER, 'the header names the chat')
    s.rep(OLD_SUBMIT, NEW_SUBMIT, 'synthChatSubmit keeps its origin')
    s.rep(OLD_SUBMIT_PIN, NEW_SUBMIT_PIN, 'the ask is pinned after the thread exists')
    s.rep(OLD_APPROVE, NEW_APPROVE, 'synthChatApproveBrief keeps its origin')
    s.rep(OLD_HIST_ANALYZE, NEW_HIST_ANALYZE, 'analyze body: the asking thread turns')
    s.rep(OLD_HIST_INTERPRET, NEW_HIST_INTERPRET, 'interpret body: the asking thread turns')
    s.rep(OLD_HIST_DECK, NEW_HIST_DECK, 'deck body: the asking thread turns')
    # pollers
    for name in ('_pmPollFwJob', '_pmPollAiqJob', '_pmPollTwinJob', '_pmPollJiqJob', '_pmPollBpiqJob'):
        head = f"        async function {name}(jobId) {{\n"
        s.rep(head, head + PO_LINE, f'{name}: origin captured')
        s.in_region(head, FN_END, PUSH_PAIRS[:1], f'{name}: lines land in the origin thread')
    # the twin poller also pushes with meta
    s.in_region("        async function _pmPollTwinJob(jobId) {\n", FN_END, PUSH_PAIRS[1:], '_pmPollTwinJob: meta line lands in the origin thread')
    s.rep(OLD_TRACK, NEW_TRACK, '_pmTrackReadJob names the thread')
    s.rep(OLD_READ_HEAD, NEW_READ_HEAD, '_pmPollReadJob: origin captured')
    s.in_region("        async function _pmPollReadJob(jobId, originTid) {\n", "        async function _pmRefreshLandedRead(", PUSH_PAIRS,
                '_pmPollReadJob: lines land in the origin thread')
    s.rep(OLD_RESUME, NEW_RESUME, '_pmResumeReadJobs passes the thread')
    # the build status poller
    poll_head = "        function _synthChatStartPolling(runId, subjectHint, knownStatus) {\n            if (synthChatPollTimer) clearInterval(synthChatPollTimer);\n"
    s.rep(poll_head, poll_head + PO_LINE, '_synthChatStartPolling: origin captured')
    s.rep(OLD_PENDING_Q, NEW_PENDING_Q, 'the pending question waits in its thread')
    s.in_region("        function _synthChatStartPolling(runId, subjectHint, knownStatus) {\n",
                "        // Custom Analysis Profile IQ now uses original formView", PUSH_PAIRS,
                '_synthChatStartPolling: lines land in the origin thread')
    # the deck
    deck_head = "        async function _pmStartDeck(text, boundCtx, angleOverride, confirmOpen, bindSubject) {\n"
    s.rep(deck_head, deck_head + PO_LINE, '_pmStartDeck: origin captured')
    s.in_region(deck_head, "        async function synthChatSubmit() {",
                PUSH_PAIRS + [("_pmPushGuidanceTurn(", "_pmPushGuidanceFor(_po, ")],
                '_pmStartDeck: lines land in the origin thread')
    s.rep(OLD_DECK_RELEASE, NEW_DECK_RELEASE, '_pmStartDeck: the ask is over once the build is accepted')
    # uploads
    s.rep(OLD_UPLOAD_ENSURE, NEW_UPLOAD_ENSURE, 'upload: origin captured')
    s.rep(OLD_UPLOAD_TID, NEW_UPLOAD_TID, 'upload: the file joins the origin thread')
    s.in_region("        async function synthChatFilesChosen(files) {\n", "        (function _synthChatWireDrop() {", PUSH_PAIRS,
                'upload: lines land in the origin thread')
    s.rep(OLD_RERENDER_EMPTY, NEW_RERENDER_EMPTY, 'rerender (empty pane) keeps the elsewhere line')
    s.rep(OLD_RERENDER_TAIL, NEW_RERENDER_TAIL, 'rerender keeps the elsewhere line')
    return s


def main():
    src = INDEX.read_text(encoding="utf-8")
    if MARK in src:
        print("[splice] already applied")
        return 0
    BACKUP.write_text(src, encoding="utf-8")
    s = apply(src)
    for forbidden in ('_pmCarried', 'carried_from'):
        if forbidden in s.src:
            raise RuntimeError(f"{forbidden} still present after the splice")
    INDEX.write_text(s.src, encoding="utf-8")
    print(f"[splice] {len(s.done)} edits applied; {len(src):,} -> {len(s.src):,} bytes")
    for d in s.done:
        print("  -", d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
