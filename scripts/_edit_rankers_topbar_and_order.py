#!/usr/bin/env python3
"""Rankers: (1) light-mode legibility for the top filter bar, and
(2) reorder tabs so FAST is first/default and Talent Ranker is last.

The filter bar used var(--bg-secondary/--bg-primary) which are never
defined, so it stayed dark in light mode while its text went dark ->
illegible. Add light-mode-scoped overrides. Then reorder _rankersOrder
and switch the default active tab from 'talentranker' to 'fast'.
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    assert src.count(old) == 1, "anchor not unique (%d): %s" % (src.count(old), label)
    return src.replace(old, new)

# 1) Light-mode filter-bar overrides, inserted after the apply-btn hover rule.
anchor = "            #trendsIQView .tiq-apply-btn:hover { filter: brightness(1.1); }\n"
light_css = anchor + (
    "            /* Light mode: the filter bar + inputs use undefined\n"
    "               --bg-secondary/--bg-primary vars that fall back to dark,\n"
    "               so in light mode they stayed dark with dark text (illegible).\n"
    "               Give them a light surface. (Jessie 2026-09-02) */\n"
    '            body[data-theme="light"] #trendsIQView .tiq-filter-bar {\n'
    "                background: #f1f4f9;\n"
    "                border-color: var(--border-color, #d1d9e6);\n"
    "            }\n"
    '            body[data-theme="light"] #trendsIQView .tiq-filter-bar select,\n'
    '            body[data-theme="light"] #trendsIQView .tiq-filter-bar input {\n'
    "                background: #ffffff;\n"
    "                color: var(--text-primary, #0a1929);\n"
    "                border-color: var(--border-color, #d1d9e6);\n"
    "            }\n"
    '            body[data-theme="light"] #trendsIQView .tiq-filter-bar button[onclick*="tiqResetAsOf"] {\n'
    "                background: #ffffff !important;\n"
    "                color: var(--text-primary, #0a1929) !important;\n"
    "                border: 1px solid var(--border-color, #d1d9e6) !important;\n"
    "            }\n"
)
src = sub_once(anchor, light_css, "filter-bar light css")

# 2a) Reorder the Rankers tab strip: FAST first, Talent Ranker last.
src = sub_once(
    "            var _rankersOrder = ['talentranker', 'music', 'podcasts', 'streaming', 'fast', 'gaming'];",
    "            var _rankersOrder = ['fast', 'music', 'podcasts', 'streaming', 'gaming', 'talentranker'];",
    "_rankersOrder",
)

# 2b) Default active tab in renderTrendsIQ: talentranker -> fast.
src = sub_once(
    "                if (!_rankersSet[window.__trendsIQ.activeTab]) window.__trendsIQ.activeTab = 'talentranker';",
    "                if (!_rankersSet[window.__trendsIQ.activeTab]) window.__trendsIQ.activeTab = 'fast';",
    "renderTrendsIQ default",
)

# 2c) Default active tab in showCultureRankerIQ: talentranker -> fast.
src = sub_once(
    "            if (_rOnly.indexOf(window.__trendsIQ.activeTab) < 0) window.__trendsIQ.activeTab = 'talentranker';",
    "            if (_rOnly.indexOf(window.__trendsIQ.activeTab) < 0) window.__trendsIQ.activeTab = 'fast';",
    "showCultureRankerIQ default",
)

assert src.rstrip().endswith("</html>")
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
