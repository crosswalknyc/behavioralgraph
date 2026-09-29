#!/usr/bin/env python3
"""Prometheus header redesign (2026-09-28 Jenna: "design of this is
bad... sleek and amazing"). One clean row: rail button, mark, title
with an ellipsizing subtitle, a magnifier that expands into the search
field, a compact builds pill, collapse. Python byte-level splice per
index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_pm_header_redesign.html")

# 1. CSS: subtitle never wraps; search pill; compact badge ------------
OLD_CSS = """        #prometheusSub { font-size: 11px; color: #7C878A; line-height: 1.3;
            margin-top: 2px; text-transform: none !important;
            letter-spacing: -0.005em; font-weight: 400; }
        #prometheusWidget #synthChatQueueBadge {
            font-size: 11px !important; color: #7C878A !important; font-weight: 400 !important;
            letter-spacing: 0; text-transform: none !important;
        }"""
NEW_CSS = """        #prometheusSub { font-size: 11px; color: #7C878A; line-height: 1.3;
            margin-top: 2px; text-transform: none !important;
            letter-spacing: -0.005em; font-weight: 400;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        #prometheusTitle { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        #prometheusWidget #synthChatQueueBadge {
            font-size: 10.5px !important; color: #9AA09B !important; font-weight: 500 !important;
            letter-spacing: 0.01em; text-transform: none !important;
            background: rgba(233,232,225,0.06); border: 1px solid rgba(233,232,225,0.12);
            border-radius: 999px; padding: 4px 10px; white-space: nowrap;
            font-variant-numeric: tabular-nums; flex: 0 1 auto; min-width: 0;
            overflow: hidden; text-overflow: ellipsis; max-width: 170px;
        }
        #prometheusWidget #synthChatQueueBadge:empty { display: none; }
        #pmThreadSearchWrap { display: inline-flex; align-items: center; flex: 0 1 auto; min-width: 0; }
        #pmSearchBtn {
            background: none; border: 1px solid rgba(233,232,225,0.16); border-radius: 8px;
            color: #9AA09B; width: 26px; height: 26px; display: inline-flex;
            align-items: center; justify-content: center; cursor: pointer; padding: 0; flex: 0 0 auto;
            transition: color .15s ease, border-color .15s ease;
        }
        #pmSearchBtn:hover { color: #E9E8E1; border-color: rgba(233,232,225,0.32); }
        #pmThreadSearch {
            width: 0; opacity: 0; padding: 0 0; margin-left: 0;
            border: 1px solid transparent; height: 26px; box-sizing: border-box;
            background: rgba(233,232,225,0.07); border-radius: 999px; color: #E9E8E1;
            font-size: 11px; outline: none; font-family: inherit;
            transition: width .18s ease, opacity .14s ease, padding .18s ease, margin .18s ease;
        }
        #pmThreadSearch::placeholder { color: #7C878A; }
        #pmThreadSearchWrap.open #pmThreadSearch {
            width: 150px; opacity: 1; padding: 0 10px; margin-left: 6px;
            border-color: rgba(233,232,225,0.2);
        }
        #pmThreadRailBtn { transition: color .15s ease, border-color .15s ease; }
        #pmThreadRailBtn:hover { color: #E9E8E1 !important; border-color: rgba(233,232,225,0.32) !important; }"""

# 2. Markup: right group shrinks; search becomes icon + expanding field
OLD_RIGHT = """                <div style="display:flex; align-items:center; gap:0.5rem; flex:0 0 auto;">
                    <input type="text" id="pmThreadSearch" placeholder="Search queries" aria-label="Search your queries" oninput="_pmThreadSearch(this.value)" style="width:132px; background:rgba(233,232,225,0.07); border:1px solid rgba(233,232,225,0.16); border-radius:999px; color:#E9E8E1; font-size:0.68rem; padding:0.28rem 0.65rem; outline:none; font-family:inherit;">
                    <span id="synthChatQueueBadge"></span>"""
NEW_RIGHT = """                <div style="display:flex; align-items:center; gap:0.5rem; flex:0 1 auto; min-width:0; justify-content:flex-end;">
                    <span id="synthChatQueueBadge"></span>
                    <div id="pmThreadSearchWrap">
                        <button type="button" id="pmSearchBtn" onclick="pmSearchToggle()" aria-label="Search your queries" title="Search queries">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.4" y2="16.4"></line></svg>
                        </button>
                        <input type="text" id="pmThreadSearch" placeholder="Search queries" aria-label="Search your queries" oninput="_pmThreadSearch(this.value)" onblur="if(!this.value){var w=document.getElementById('pmThreadSearchWrap'); if(w) w.classList.remove('open');}">
                    </div>"""

# 3. JS: search toggle ------------------------------------------------
OLD_JS = """        var _pmSearchQ = '';
        function _pmThreadSearch(q) {"""
NEW_JS = """        function pmSearchToggle() {
            var w = document.getElementById('pmThreadSearchWrap');
            var i = document.getElementById('pmThreadSearch');
            if (!w || !i) return;
            var open = w.classList.toggle('open');
            if (open) { setTimeout(function () { i.focus(); }, 80); }
            else { i.value = ''; _pmThreadSearch(''); }
        }
        var _pmSearchQ = '';
        function _pmThreadSearch(q) {"""

# 4. Badge copy: compact ---------------------------------------------
OLD_BADGE = """                    badge.textContent =
                        'builds: ' + q.pending + ' pending, ' + q.running + ' running, ' + q.completed + ' completed';"""
NEW_BADGE = """                    var parts = [];
                    if (q.pending > 0) parts.push(q.pending + ' queued');
                    if (q.running > 0) parts.push(q.running + ' running');
                    parts.push(q.completed + ' done');
                    badge.textContent = parts.join(' \\u00b7 ');
                    badge.title = 'Builds: ' + q.pending + ' queued, '
                        + q.running + ' running, ' + q.completed
                        + ' completed';"""


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
    (OLD_CSS, NEW_CSS, "header css"),
    (OLD_RIGHT, NEW_RIGHT, "right group markup"),
    (OLD_JS, NEW_JS, "search toggle js"),
    (OLD_BADGE, NEW_BADGE, "badge copy"),
):
    src = splice(src, old, new, desc)
INDEX.write_text(src, encoding="utf-8")
print("spliced 4 changes")
