#!/usr/bin/env python3
"""Chats & Tasks drawer tab on the Prometheus widget (2026-09-28
Jenna: "in addition to the icon put a drawer on here that you can open
like how we have it for library"). Mirrors the Library rail tab:
vertical label, Signal Green edge and dot, chevron; rides out to the
rail's edge while open. Python byte-level splice per
index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_pm_chats_tab.html")

# 1. CSS after the rail-button hover rule (header redesign anchor) ----
OLD_CSS = """        #pmThreadRailBtn { transition: color .15s ease, border-color .15s ease; }
        #pmThreadRailBtn:hover { color: #E9E8E1 !important; border-color: rgba(233,232,225,0.32) !important; }"""
NEW_CSS = """        #pmThreadRailBtn { transition: color .15s ease, border-color .15s ease; }
        #pmThreadRailBtn:hover { color: #E9E8E1 !important; border-color: rgba(233,232,225,0.32) !important; }
        #prometheusBody { position: relative; }
        #pmChatsTab {
            position: absolute; left: 0; top: 50%; transform: translateY(-50%);
            z-index: 61; width: 30px; min-height: 148px;
            padding: 14px 0 12px; display: flex; flex-direction: column;
            align-items: center; justify-content: flex-start; gap: 10px;
            background: #1B2F35; border: 1px solid #3B3D38; border-left: 3px solid #C7F23E;
            border-radius: 0 10px 10px 0; color: #E9E8E1; cursor: pointer;
            font-family: inherit; box-shadow: 6px 8px 20px rgba(0, 0, 0, .28);
            transition: left .18s ease, width .15s ease;
        }
        #pmChatsTab:hover { width: 34px; }
        #pmChatsTab.rail-open { left: 222px; }
        #pmChatsTab .pm-tab-dot { width: 6px; height: 6px; border-radius: 50%; background: #C7F23E; flex: 0 0 6px; }
        #pmChatsTab .pm-tab-label {
            writing-mode: vertical-rl; transform: rotate(180deg);
            font-size: 10px; font-weight: 600; letter-spacing: .13em;
            text-transform: uppercase; color: #E9E8E1; white-space: nowrap;
        }
        #pmChatsTab .pm-tab-chevron { color: #C7F23E; font-size: 16px; line-height: 1; font-weight: 600; transition: transform .18s ease; }
        #pmChatsTab.rail-open .pm-tab-chevron { transform: rotate(180deg); }
        body[data-theme="light"] #pmChatsTab { background: #FFFFFF; border-color: #C9C6BA; border-left-color: #5E7E12; }
        body[data-theme="light"] #pmChatsTab .pm-tab-dot { background: #5E7E12; }
        body[data-theme="light"] #pmChatsTab .pm-tab-chevron { color: #5E7E12; }
        body[data-theme="light"] #pmChatsTab .pm-tab-label { color: #0C1618; }"""

# 2. Markup: the tab, first child of prometheusBody -------------------
OLD_BODY = """            <div id="prometheusBody">
                <div id="pmThreadRail\""""
NEW_BODY = """            <div id="prometheusBody">
                <button type="button" id="pmChatsTab" onclick="pmThreadRailToggle()" aria-label="Chats and tasks" title="Chats &amp; Tasks">
                    <span class="pm-tab-dot" aria-hidden="true"></span>
                    <span class="pm-tab-label">Chats &amp; Tasks</span>
                    <span class="pm-tab-chevron" aria-hidden="true">\u203a</span>
                </button>
                <div id="pmThreadRail\""""

# 3. JS: the toggle also moves the tab and flips its chevron ----------
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
            if (_pmRailOpen) _pmThreadsLoad();
        }"""
NEW_TOGGLE = """        function pmThreadRailToggle() {
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
    (OLD_CSS, NEW_CSS, "tab css"),
    (OLD_BODY, NEW_BODY, "tab markup"),
    (OLD_TOGGLE, NEW_TOGGLE, "toggle js"),
):
    src = splice(src, old, new, desc)
INDEX.write_text(src, encoding="utf-8")
print("spliced 3 changes")
