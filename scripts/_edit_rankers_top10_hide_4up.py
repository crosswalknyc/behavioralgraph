#!/usr/bin/env python3
"""Rankers Music/Podcasts/Gaming grids:
1. Each source sheet shows ~top 10 rows then scrolls (sticky sort header).
2. Hide cards for sources with no data (Amazon Music, TikTok Sounds, etc.).
3. Music runs 4 cards across (Podcasts/Gaming stay 3).
"""
import io

PATH = "templates/index.html"
with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()
orig_lines = src.count("\n")

def sub_once(old, new, label):
    n = src.count(old)
    assert n == 1, "anchor not unique (%d): %s" % (n, label)
    return src.replace(old, new)

# ------------------------------------------------------------------ #
# A. _tiqSourceSheet: add `scroll` option + wrap table in scroll div.
# ------------------------------------------------------------------ #
src = sub_once(
    "            var wide = !!opts.wide;\n",
    "            var wide = !!opts.wide;\n            var scroll = !!opts.scroll;\n",
    "scroll var",
)
src = sub_once(
    "            return '<div class=\"tiq-card\">' + _tiqSheetCardHead(opts.label) +\n"
    "                   '<table class=\"tiq-sheet'",
    "            return '<div class=\"tiq-card\">' + _tiqSheetCardHead(opts.label) +\n"
    "                   (scroll ? '<div class=\"tiq-sheet-scroll\">' : '') +\n"
    "                   '<table class=\"tiq-sheet'",
    "scroll open",
)
src = sub_once(
    "                     '<tbody>' + (rowsHtml || '<tr><td colspan=\"' + (wide ? '4' : '3') + '\" class=\"tiq-empty\">No data.</td></tr>') + '</tbody>' +\n"
    "                   '</table></div>';",
    "                     '<tbody>' + (rowsHtml || '<tr><td colspan=\"' + (wide ? '4' : '3') + '\" class=\"tiq-empty\">No data.</td></tr>') + '</tbody>' +\n"
    "                   '</table>' + (scroll ? '</div>' : '') + '</div>';",
    "scroll close",
)

# ------------------------------------------------------------------ #
# B. CSS: 4-up grid + scroll container + sticky header.
# ------------------------------------------------------------------ #
css_anchor = (
    "            @media (max-width: 720px)  { #trendsIQView .tiq-cards-grid.tiq-cards-3up { grid-template-columns: 1fr; } }"
)
css_new = css_anchor + (
    "\n            /* Music runs 4 cards across; steps down on narrower viewports. */"
    "\n            #trendsIQView .tiq-cards-grid.tiq-cards-4up { grid-template-columns: repeat(4, minmax(0, 1fr)); }"
    "\n            @media (max-width: 1300px) { #trendsIQView .tiq-cards-grid.tiq-cards-4up { grid-template-columns: repeat(3, minmax(0, 1fr)); } }"
    "\n            @media (max-width: 1000px) { #trendsIQView .tiq-cards-grid.tiq-cards-4up { grid-template-columns: repeat(2, minmax(0, 1fr)); } }"
    "\n            @media (max-width: 720px)  { #trendsIQView .tiq-cards-grid.tiq-cards-4up { grid-template-columns: 1fr; } }"
    "\n            /* Top-10 preview: source sheets show ~10 rows then scroll,"
    " with the sort header pinned. (Jessie 2026-09-02) */"
    "\n            #trendsIQView .tiq-sheet-scroll { max-height: 640px; overflow-y: auto; }"
    "\n            #trendsIQView .tiq-sheet-scroll thead th { position: sticky; top: 0; z-index: 1; background: var(--bg-secondary, #1e1e2e); }"
    '\n            body[data-theme="light"] #trendsIQView .tiq-sheet-scroll thead th { background: #ffffff; }'
)
src = sub_once(css_anchor, css_new, "4up + scroll css")

# ------------------------------------------------------------------ #
# C. Music: hide no-data cards, scroll:true, 4-up grid.
# ------------------------------------------------------------------ #
music_old = (
    "            // 2026-09-02 (v2): Liz asked for all platforms on one page,\n"
    "            // each in its own card, 3 cards per row - keeping the sortable\n"
    "            // 3-column sheet (Title | Streams | Delta%).\n"
    "            var cards = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var items = s.items || [];\n"
    "                if (!s.available || !items.length) {\n"
    "                    return '<div class=\"tiq-card\"><div class=\"tiq-placeholder\">' +\n"
    "                           '<div class=\"tiq-placeholder-title\">' + _tiqEsc(s.label || k) + '</div>' +\n"
    "                           '<div>' + _tiqEsc(s.sub || 'Warming up.') + '</div>' +\n"
    "                           '</div></div>';\n"
    "                }\n"
    "                return _tiqSourceSheet({\n"
    "                    kind: 'music', source: k, label: s.label || k, rows: items,\n"
    "                    metricLabel: 'Streams', entity: 'Music', wide: false,\n"
    "                    keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },\n"
    "                    subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },\n"
    "                    badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }\n"
    "                });\n"
    "            }).join('');\n"
    "\n"
    "            return '<div class=\"tiq-cards-grid tiq-cards-3up\">' + cards + '</div>';"
)
music_new = (
    "            // 2026-09-02 (v3): hide no-data sources; each card previews the\n"
    "            // top 10 then scrolls; Music runs 4 cards across. Sortable\n"
    "            // 3-column sheet (Title | Streams | Delta%).\n"
    "            var cards = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var items = s.items || [];\n"
    "                if (!s.available || !items.length) return '';\n"
    "                return _tiqSourceSheet({\n"
    "                    kind: 'music', source: k, label: s.label || k, rows: items,\n"
    "                    metricLabel: 'Streams', entity: 'Music', wide: false, scroll: true,\n"
    "                    keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },\n"
    "                    subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },\n"
    "                    badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }\n"
    "                });\n"
    "            }).filter(Boolean);\n"
    "            if (!cards.length) return '<div class=\"tiq-empty\">Music charts are warming up. Check back later.</div>';\n"
    "            return '<div class=\"tiq-cards-grid tiq-cards-4up\">' + cards.join('') + '</div>';"
)
src = sub_once(music_old, music_new, "music v3")

# ------------------------------------------------------------------ #
# D. Podcasts: hide no-data cards, scroll:true, keep 3-up.
# ------------------------------------------------------------------ #
pod_old = (
    "            // 2026-09-02 (v2): all platforms on one page, each its own card,\n"
    "            // 3 per row, keeping the sortable 3-column sheet.\n"
    "            var cards = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var items = s.items || [];\n"
    "                if (!s.available || !items.length) {\n"
    "                    return '<div class=\"tiq-card\"><div class=\"tiq-placeholder\">' +\n"
    "                           '<div class=\"tiq-placeholder-title\">' + _tiqEsc(s.label || k) + '</div>' +\n"
    "                           '<div>' + _tiqEsc(s.sub || 'Warming up.') + '</div>' +\n"
    "                           '</div></div>';\n"
    "                }\n"
    "                return _tiqSourceSheet({\n"
    "                    kind: 'podcast', source: k, label: s.label || k, rows: items,\n"
    "                    metricLabel: 'Listeners', entity: 'Podcast', wide: false,\n"
    "                    keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },\n"
    "                    subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },\n"
    "                    badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }\n"
    "                });\n"
    "            }).join('');\n"
    "\n"
    "            return '<div class=\"tiq-cards-grid tiq-cards-3up\">' + cards + '</div>';"
)
pod_new = (
    "            // 2026-09-02 (v3): hide no-data sources; top 10 then scroll;\n"
    "            // 3 cards per row.\n"
    "            var cards = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var items = s.items || [];\n"
    "                if (!s.available || !items.length) return '';\n"
    "                return _tiqSourceSheet({\n"
    "                    kind: 'podcast', source: k, label: s.label || k, rows: items,\n"
    "                    metricLabel: 'Listeners', entity: 'Podcast', wide: false, scroll: true,\n"
    "                    keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },\n"
    "                    subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },\n"
    "                    badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }\n"
    "                });\n"
    "            }).filter(Boolean);\n"
    "            if (!cards.length) return '<div class=\"tiq-empty\">Podcast charts are warming up. Check back later.</div>';\n"
    "            return '<div class=\"tiq-cards-grid tiq-cards-3up\">' + cards.join('') + '</div>';"
)
src = sub_once(pod_old, pod_new, "podcasts v3")

# ------------------------------------------------------------------ #
# E. Gaming: scroll:true on gameCol + hide no-data sources/buckets.
# ------------------------------------------------------------------ #
src = sub_once(
    "                    metricLabel: 'Plays', entity: 'Gaming', wide: !!wide,\n",
    "                    metricLabel: 'Plays', entity: 'Gaming', wide: !!wide, scroll: true,\n",
    "gameCol scroll",
)
game_old = (
    "            var cards = [];\n"
    "            keys.forEach(function(k) {\n"
    "                var s = gaming[k] || {};\n"
    "                var buckets = groupedBuckets[k];\n"
    "                if (!s.available) {\n"
    "                    cards.push('<div class=\"tiq-card\"><div class=\"tiq-placeholder\">' +\n"
    "                        '<div class=\"tiq-placeholder-title\">' + _tiqEsc(s.label || k) + '</div>' +\n"
    "                        '<div>Loading.</div></div></div>');\n"
    "                } else if (buckets && buckets.some(function(b) { return Array.isArray(s[b[0]]); })) {\n"
    "                    var populated = buckets.filter(function(b) { return (s[b[0]] || []).length; });\n"
    "                    if (!populated.length) {\n"
    "                        cards.push('<div class=\"tiq-card\"><div class=\"tiq-empty\">No trending games right now.</div></div>');\n"
    "                    } else {\n"
    "                        populated.forEach(function(b) {\n"
    "                            cards.push(gameCol((s.label ? s.label + ' \\u00b7 ' : '') + b[1], s[b[0]]));\n"
    "                        });\n"
    "                    }\n"
    "                } else if (!(s.items || []).length) {\n"
    "                    cards.push('<div class=\"tiq-card\"><div class=\"tiq-empty\">No trending games right now.</div></div>');\n"
    "                } else {\n"
    "                    cards.push(gameCol(s.label || k, s.items));\n"
    "                }\n"
    "            });\n"
    "\n"
    "            return '<div class=\"tiq-cards-grid tiq-cards-3up\">' + cards.join('') + '</div>';"
)
game_new = (
    "            var cards = [];\n"
    "            keys.forEach(function(k) {\n"
    "                var s = gaming[k] || {};\n"
    "                var buckets = groupedBuckets[k];\n"
    "                if (!s.available) {\n"
    "                    return;   // hide sources with no data\n"
    "                } else if (buckets && buckets.some(function(b) { return Array.isArray(s[b[0]]); })) {\n"
    "                    buckets.filter(function(b) { return (s[b[0]] || []).length; })\n"
    "                        .forEach(function(b) {\n"
    "                            cards.push(gameCol((s.label ? s.label + ' \\u00b7 ' : '') + b[1], s[b[0]]));\n"
    "                        });\n"
    "                } else if ((s.items || []).length) {\n"
    "                    cards.push(gameCol(s.label || k, s.items));\n"
    "                }\n"
    "            });\n"
    "\n"
    "            if (!cards.length) return '<div class=\"tiq-empty\">Gaming rankings are warming up.</div>';\n"
    "            return '<div class=\"tiq-cards-grid tiq-cards-3up\">' + cards.join('') + '</div>';"
)
src = sub_once(game_old, game_new, "gaming v3")

assert src.rstrip().endswith("</html>")
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
