#!/usr/bin/env python3
"""Center the metric + delta columns on the full-width Rankers sheets
(Music, Podcasts, Gaming-Xbox), matching the FAST Channel Ranker.

Adds an opt-in `wide` mode to _tiqSourceSheet: table-layout:fixed + a
trailing spacer column (title 36% / metric 15% / delta 15% / spacer 34%),
reusing the existing .tiq-sheet-chan width rules via a shared
.tiq-sheet-wide selector. Half-width grouped Gaming columns stay default.
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    assert src.count(old) == 1, "anchor not unique (%d): %s" % (src.count(old), label)
    return src.replace(old, new)

# ---------------------------------------------------------------------------
# 1) CSS: extend the .tiq-sheet-chan width rules to also cover .tiq-sheet-wide
# ---------------------------------------------------------------------------
CSS_OLD = (
    "            #trendsIQView .tiq-sheet-chan { table-layout: fixed; }\n"
    "            #trendsIQView .tiq-sheet-chan .tiq-sheet-td-title, #trendsIQView .tiq-sheet-chan th.tiq-sheet-th-title { width: 36% !important; white-space: normal; }\n"
    "            #trendsIQView .tiq-sheet-chan .tiq-chan-views { width: 15% !important; }\n"
    "            #trendsIQView .tiq-sheet-chan .tiq-chan-delta { width: 15% !important; padding-left: 0; }\n"
    "            #trendsIQView .tiq-sheet-chan th.tiq-sheet-th-spacer, #trendsIQView .tiq-sheet-chan .tiq-sheet-td-spacer { width: 34% !important; padding: 0; }\n"
)
CSS_NEW = (
    "            #trendsIQView .tiq-sheet-chan, #trendsIQView .tiq-sheet-wide { table-layout: fixed; }\n"
    "            #trendsIQView .tiq-sheet-chan .tiq-sheet-td-title, #trendsIQView .tiq-sheet-chan th.tiq-sheet-th-title, #trendsIQView .tiq-sheet-wide .tiq-sheet-td-title, #trendsIQView .tiq-sheet-wide th.tiq-sheet-th-title { width: 36% !important; white-space: normal; }\n"
    "            #trendsIQView .tiq-sheet-chan .tiq-chan-views, #trendsIQView .tiq-sheet-wide .tiq-chan-views { width: 15% !important; }\n"
    "            #trendsIQView .tiq-sheet-chan .tiq-chan-delta, #trendsIQView .tiq-sheet-wide .tiq-chan-delta { width: 15% !important; padding-left: 0; }\n"
    "            #trendsIQView .tiq-sheet-chan th.tiq-sheet-th-spacer, #trendsIQView .tiq-sheet-chan .tiq-sheet-td-spacer, #trendsIQView .tiq-sheet-wide th.tiq-sheet-th-spacer, #trendsIQView .tiq-sheet-wide .tiq-sheet-td-spacer { width: 34% !important; padding: 0; }\n"
)
src = sub_once(CSS_OLD, CSS_NEW, "css chan/wide rules")

# ---------------------------------------------------------------------------
# 2) _tiqSourceSheet: declare `wide`
# ---------------------------------------------------------------------------
src = sub_once(
    "            var entity = opts.entity || 'Music';\n",
    "            var entity = opts.entity || 'Music';\n            var wide = !!opts.wide;\n",
    "wide decl",
)

# ---------------------------------------------------------------------------
# 3) _tiqSourceSheet: row metric/delta tds + trailing spacer td (wide only)
# ---------------------------------------------------------------------------
ROW_OLD = (
    "                       '<td class=\"tiq-sheet-td-num tiq-sheet-td-views\" title=\"' + valTip + '\">' + (valNum || '<span class=\"tiq-sheet-dim\">\\u2014</span>') + '</td>' +\n"
    "                       '<td class=\"tiq-sheet-td-num\">' + deltaHtml + '</td>' +\n"
    "                       '</tr>';\n"
)
ROW_NEW = (
    "                       '<td class=\"tiq-sheet-td-num tiq-sheet-td-views' + (wide ? ' tiq-chan-views' : '') + '\" title=\"' + valTip + '\">' + (valNum || '<span class=\"tiq-sheet-dim\">\\u2014</span>') + '</td>' +\n"
    "                       '<td class=\"tiq-sheet-td-num' + (wide ? ' tiq-chan-delta' : '') + '\">' + deltaHtml + '</td>' +\n"
    "                       (wide ? '<td class=\"tiq-sheet-td-spacer\"></td>' : '') +\n"
    "                       '</tr>';\n"
)
src = sub_once(ROW_OLD, ROW_NEW, "row tds")

# ---------------------------------------------------------------------------
# 4) _tiqSourceSheet: return block (table class, header th classes, spacer th,
#    empty-row colspan)
# ---------------------------------------------------------------------------
RET_OLD = (
    "            return '<div class=\"tiq-card\">' + _tiqSheetCardHead(opts.label) +\n"
    "                   '<table class=\"tiq-sheet\" data-entity=\"' + entity + '\" data-metric=\"' + _tiqEsc(metricLabel) + '\" data-export=\"' + _tiqEsc(String(source) + '-' + (opts.label || '')) + '\">' +\n"
    "                     '<thead><tr>' +\n"
    "                       '<th class=\"tiq-sheet-th-title\"><span class=\"tiq-sheet-th-label\">Title</span><select class=\"tiq-sheet-titlesort\" onchange=\"tiqSortFastSheetTitle(this)\"><option value=\"\">Sort</option><option value=\"hi\">High \\u2192 Low</option><option value=\"lo\">Low \\u2192 High</option><option value=\"az\">A\\u2013Z</option></select></th>' +\n"
    "                       '<th class=\"tiq-sheet-th-num tiq-sheet-sortable\" onclick=\"tiqSortFastSheet(this,\\'views\\')\">' + _tiqEsc(metricLabel) + '<span class=\"tiq-sort-ind\">\\u21c5</span></th>' +\n"
    "                       '<th class=\"tiq-sheet-th-num tiq-sheet-sortable\" onclick=\"tiqSortFastSheet(this,\\'delta\\')\">\\u0394%<span class=\"tiq-sort-ind\">\\u21c5</span></th>' +\n"
    "                     '</tr></thead>' +\n"
    "                     '<tbody>' + (rowsHtml || '<tr><td colspan=\"3\" class=\"tiq-empty\">No data.</td></tr>') + '</tbody>' +\n"
    "                   '</table></div>';\n"
)
RET_NEW = (
    "            return '<div class=\"tiq-card\">' + _tiqSheetCardHead(opts.label) +\n"
    "                   '<table class=\"tiq-sheet' + (wide ? ' tiq-sheet-wide' : '') + '\" data-entity=\"' + entity + '\" data-metric=\"' + _tiqEsc(metricLabel) + '\" data-export=\"' + _tiqEsc(String(source) + '-' + (opts.label || '')) + '\">' +\n"
    "                     '<thead><tr>' +\n"
    "                       '<th class=\"tiq-sheet-th-title\"><span class=\"tiq-sheet-th-label\">Title</span><select class=\"tiq-sheet-titlesort\" onchange=\"tiqSortFastSheetTitle(this)\"><option value=\"\">Sort</option><option value=\"hi\">High \\u2192 Low</option><option value=\"lo\">Low \\u2192 High</option><option value=\"az\">A\\u2013Z</option></select></th>' +\n"
    "                       '<th class=\"tiq-sheet-th-num tiq-sheet-sortable' + (wide ? ' tiq-chan-views' : '') + '\" onclick=\"tiqSortFastSheet(this,\\'views\\')\">' + _tiqEsc(metricLabel) + '<span class=\"tiq-sort-ind\">\\u21c5</span></th>' +\n"
    "                       '<th class=\"tiq-sheet-th-num tiq-sheet-sortable' + (wide ? ' tiq-chan-delta' : '') + '\" onclick=\"tiqSortFastSheet(this,\\'delta\\')\">\\u0394%<span class=\"tiq-sort-ind\">\\u21c5</span></th>' +\n"
    "                       (wide ? '<th class=\"tiq-sheet-th-spacer\"></th>' : '') +\n"
    "                     '</tr></thead>' +\n"
    "                     '<tbody>' + (rowsHtml || '<tr><td colspan=\"' + (wide ? '4' : '3') + '\" class=\"tiq-empty\">No data.</td></tr>') + '</tbody>' +\n"
    "                   '</table></div>';\n"
)
src = sub_once(RET_OLD, RET_NEW, "return block")

# ---------------------------------------------------------------------------
# 5) Callers: turn on wide for Music, Podcasts, and Gaming-Xbox
# ---------------------------------------------------------------------------
src = sub_once(
    "                        metricLabel: 'Streams', entity: 'Music',\n",
    "                        metricLabel: 'Streams', entity: 'Music', wide: true,\n",
    "music wide",
)
src = sub_once(
    "                        metricLabel: 'Listeners', entity: 'Podcast',\n",
    "                        metricLabel: 'Listeners', entity: 'Podcast', wide: true,\n",
    "podcasts wide",
)
# Gaming: add wide param to gameCol; grouped columns pass no wide (false),
# Xbox single-list passes true.
src = sub_once(
    "            var gameCol = function(colLabel, rows) {\n"
    "                return _tiqSourceSheet({\n"
    "                    kind: 'gaming', source: 'gaming', label: colLabel, rows: rows || [],\n"
    "                    metricLabel: 'Plays', entity: 'Gaming',\n",
    "            var gameCol = function(colLabel, rows, wide) {\n"
    "                return _tiqSourceSheet({\n"
    "                    kind: 'gaming', source: 'gaming', label: colLabel, rows: rows || [],\n"
    "                    metricLabel: 'Plays', entity: 'Gaming', wide: !!wide,\n",
    "gaming gameCol signature",
)
src = sub_once(
    "                    body = gameCol(s.label || k, s.items);\n",
    "                    body = gameCol(s.label || k, s.items, true);\n",
    "gaming xbox wide call",
)

# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
assert src.rstrip().endswith("</html>"), "no </html>"
assert src.count(".tiq-sheet-wide") >= 5, "tiq-sheet-wide CSS not present"
assert src.count("wide: true,") == 2, "expected 2 wide:true callers, got %d" % src.count("wide: true,")
assert src.count("wide: !!wide,") == 1, "gaming wide passthrough missing"

new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
