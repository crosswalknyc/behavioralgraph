#!/usr/bin/env python3
"""Threads drawer expands the widget leftward (2026-09-28 Jenna:
"have it expand to the left so it can be left open if you want and
wont overtake the chat window"). The widget is right-anchored, so
widening it grows left; the rail becomes a full-height side column
beside the header and chat, and the open state persists. Python
byte-level splice per index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_pm_rail_expand.html")

# 1. Move the rail out of the body: remove it there... ---------------
OLD_RAIL_IN_BODY = """            <div id="prometheusBody">
                <button type="button" id="pmChatsTab" onclick="pmThreadRailToggle()" aria-label="Chats and tasks" title="Chats &amp; Tasks">
                    <span class="pm-tab-dot" aria-hidden="true"></span>
                    <span class="pm-tab-label">Chats &amp; Tasks</span>
                    <span class="pm-tab-chevron" aria-hidden="true">\u203a</span>
                </button>
                <div id="pmThreadRail" style="position:absolute; top:0; left:0; bottom:0; width:222px; background:#15252A; border-right:1px solid rgba(233,232,225,0.12); z-index:60; transform:translateX(-105%); transition:transform 0.18s ease; display:flex; flex-direction:column; border-radius:0 0 0 12px;">
                    <div style="display:flex; align-items:center; justify-content:space-between; gap:0.5rem; padding:0.7rem 0.75rem 0.5rem;">
                        <span style="font-size:0.62rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#7C878A;">Threads</span>
                        <button type="button" onclick="pmNewChat()" style="background:rgba(199,242,62,0.14); border:1px solid rgba(199,242,62,0.45); color:#C7F23E; border-radius:999px; padding:0.22rem 0.7rem; font-size:0.68rem; font-weight:700; cursor:pointer; font-family:inherit;">+ New chat</button>
                    </div>
                    <div id="pmThreadList" style="flex:1; overflow-y:auto; padding:0 0.4rem 0.6rem;"></div>
                </div>
                <div id="prometheusFundsLock\""""
# ...and mount it as a widget-level column before the header.
NEW_RAIL_AT_WIDGET = """            <div id="pmThreadRail" style="position:absolute; top:0; left:0; bottom:0; width:222px; background:#15252A; border-right:1px solid rgba(233,232,225,0.14); z-index:5; transform:translateX(-105%); transition:transform 0.18s ease; display:flex; flex-direction:column;">
                <div style="display:flex; align-items:center; justify-content:space-between; gap:0.5rem; padding:0.85rem 0.75rem 0.5rem;">
                    <span style="font-size:0.62rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#7C878A;">Chats &amp; Tasks</span>
                    <button type="button" onclick="pmNewChat()" style="background:rgba(199,242,62,0.14); border:1px solid rgba(199,242,62,0.45); color:#C7F23E; border-radius:999px; padding:0.22rem 0.7rem; font-size:0.68rem; font-weight:700; cursor:pointer; font-family:inherit;">+ New chat</button>
                </div>
                <div id="pmThreadList" style="flex:1; overflow-y:auto; padding:0 0.4rem 0.6rem;"></div>
            </div>
            <div id="prometheusHeader">"""
OLD_HEADER_OPEN = """            <div id="prometheusHeader">"""

NEW_BODY_OPEN = """            <div id="prometheusBody">
                <button type="button" id="pmChatsTab" onclick="pmThreadRailToggle()" aria-label="Chats and tasks" title="Chats &amp; Tasks">
                    <span class="pm-tab-dot" aria-hidden="true"></span>
                    <span class="pm-tab-label">Chats &amp; Tasks</span>
                    <span class="pm-tab-chevron" aria-hidden="true">\u203a</span>
                </button>
                <div id="prometheusFundsLock\""""

# 2. CSS: open state shifts header/body; tab stays at the seam --------
OLD_TAB_OPEN_CSS = """        #pmChatsTab.rail-open { left: 222px; }"""
NEW_TAB_OPEN_CSS = """        /* Open: header and body shift right of the rail column, so the
           tab (inside the body) already sits at the seam. */
        #prometheusWidget.rail-open #prometheusHeader,
        #prometheusWidget.rail-open #prometheusBody { margin-left: 222px; }
        #prometheusWidget #prometheusHeader,
        #prometheusWidget #prometheusBody { transition: margin-left .18s ease; }"""

# 3. JS: widen the widget leftward, persist the state -----------------
OLD_TOGGLE = """        function pmThreadRailToggle() {
            var rail = document.getElementById('pmThreadRail');
            var body = document.getElementById('prometheusBody');
            if (!rail || !body) return;
            if (getComputedStyle(body).position === 'static') {
                body.style.position = 'relative';
            }
            _pmRailOpen = !_pmRailOpen;
            rail.style.transform = _pmRailOpen ? 'translateX(0)'
                                               : 'translateX(-105%)';
            var tab = document.getElementById('pmChatsTab');
            if (tab) tab.classList.toggle('rail-open', _pmRailOpen);
            if (_pmRailOpen) _pmThreadsLoad();
        }"""
NEW_TOGGLE = """        var _PM_RAIL_W = 222;
        function _pmRailApply(open) {
            var rail = document.getElementById('pmThreadRail');
            var widget = document.getElementById('prometheusWidget');
            if (!rail || !widget) return;
            var was = _pmRailOpen;
            _pmRailOpen = !!open;
            rail.style.transform = _pmRailOpen ? 'translateX(0)'
                                               : 'translateX(-105%)';
            widget.classList.toggle('rail-open', _pmRailOpen);
            var tab = document.getElementById('pmChatsTab');
            if (tab) tab.classList.toggle('rail-open', _pmRailOpen);
            // The widget is right-anchored, so width growth extends
            // LEFT: the chat keeps its full width and the drawer can
            // stay open beside it.
            if (_pmRailOpen && !was) {
                var w = widget.getBoundingClientRect().width;
                widget.style.width = Math.min(
                    w + _PM_RAIL_W,
                    Math.floor(window.innerWidth * 0.94)) + 'px';
            } else if (!_pmRailOpen && was) {
                var w2 = widget.getBoundingClientRect().width;
                widget.style.width = Math.max(w2 - _PM_RAIL_W, 360) + 'px';
            }
            try {
                localStorage.setItem('pmRailOpen_v1',
                                     _pmRailOpen ? '1' : '');
            } catch (_) {}
            if (_pmRailOpen) _pmThreadsLoad();
        }
        function pmThreadRailToggle() {
            _pmRailApply(!_pmRailOpen);
        }
        // Left open on purpose stays open (2026-09-28): restore the
        // saved state once the DOM is ready.
        setTimeout(function () {
            try {
                if (localStorage.getItem('pmRailOpen_v1') === '1') {
                    _pmRailApply(true);
                }
            } catch (_) {}
        }, 400);"""


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
# order matters: first swap the body block (removes rail from body),
# then mount the rail before the header, then css + js.
src = splice(src, OLD_RAIL_IN_BODY, NEW_BODY_OPEN, "rail out of body")
src = splice(src, OLD_HEADER_OPEN, NEW_RAIL_AT_WIDGET, "rail at widget level")
src = splice(src, OLD_TAB_OPEN_CSS, NEW_TAB_OPEN_CSS, "open-state css")
src = splice(src, OLD_TOGGLE, NEW_TOGGLE, "toggle js")
INDEX.write_text(src, encoding="utf-8")
print("spliced 4 changes")
