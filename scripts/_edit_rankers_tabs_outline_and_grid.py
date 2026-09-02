#!/usr/bin/env python3
"""Rankers:
1. Light-mode outlines for the main tab strip (.tiq-tab) and the source
   sub-tabs (.tiq-social-tab) - their translucent-white borders vanished
   on light backgrounds.
2. Music / Podcasts / Gaming: drop the one-platform-at-a-time sub-tabs and
   show ALL platforms on one page, each in its own card, keeping the
   3-column sortable sheet (Title | metric | Delta%), 3 cards per row.
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
# 1a. 3-up cards-grid modifier next to .tiq-cards-grid base rule.
# ------------------------------------------------------------------ #
grid_anchor = ("            #trendsIQView .tiq-cards-grid { display: grid; "
               "grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }")
grid_new = grid_anchor + (
    "\n            /* Exactly 3 cards per row (Music/Podcasts/Gaming all-platforms"
    " view). Collapses to 2, then 1, on narrower viewports. */"
    "\n            #trendsIQView .tiq-cards-grid.tiq-cards-3up { grid-template-columns: repeat(3, minmax(0, 1fr)); }"
    "\n            @media (max-width: 1100px) { #trendsIQView .tiq-cards-grid.tiq-cards-3up { grid-template-columns: repeat(2, minmax(0, 1fr)); } }"
    "\n            @media (max-width: 720px)  { #trendsIQView .tiq-cards-grid.tiq-cards-3up { grid-template-columns: 1fr; } }"
)
src = sub_once(grid_anchor, grid_new, "cards-3up css")

# ------------------------------------------------------------------ #
# 1b. Light-mode tab outlines (main strip + sub-tabs).
# ------------------------------------------------------------------ #
tab_anchor = "            #trendsIQView .tiq-social-tab.unavailable { opacity: 0.5; cursor: not-allowed; }"
tab_new = tab_anchor + (
    "\n            /* Light mode: inactive tabs used translucent-white borders that"
    " disappeared on light backgrounds. Give them a visible outline."
    " (Jessie 2026-09-02) */"
    '\n            body[data-theme="light"] #trendsIQView .tiq-tab {'
    " background: #ffffff; border-color: var(--border-color, #d1d9e6); }"
    '\n            body[data-theme="light"] #trendsIQView .tiq-tab:hover {'
    " background: rgba(124,58,237,0.08); border-color: #7c3aed; }"
    '\n            body[data-theme="light"] #trendsIQView .tiq-social-tab {'
    " background: #ffffff; border-color: var(--border-color, #d1d9e6); }"
    '\n            body[data-theme="light"] #trendsIQView .tiq-social-tab:hover {'
    " background: rgba(124,58,237,0.08); border-color: #7c3aed; }"
)
src = sub_once(tab_anchor, tab_new, "light tab outlines")

# ------------------------------------------------------------------ #
# 2a. Music: sub-tabs -> 3-up grid of source cards.
# ------------------------------------------------------------------ #
music_old = (
    "            if (!window.__trendsIQ.activeMusic || !sources[window.__trendsIQ.activeMusic]) {\n"
    "                window.__trendsIQ.activeMusic = keys.find(function(k) {\n"
    "                    return (sources[k] || {}).available;\n"
    "                }) || keys[0];\n"
    "            }\n"
    "\n"
    "            var tabs = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var active = (k === window.__trendsIQ.activeMusic) ? ' active' : '';\n"
    "                var unavail = !s.available ? ' unavailable' : '';\n"
    "                return '<div class=\"tiq-social-tab' + active + unavail + '\" onclick=\"setTrendsIQMusic(\\'' + k + '\\')\">' + _tiqEsc(s.label || k) + '</div>';\n"
    "            }).join('');\n"
    "\n"
    "            var panels = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var items = s.items || [];\n"
    "                var active = (k === window.__trendsIQ.activeMusic) ? ' active' : '';\n"
    "                var body;\n"
    "                if (!s.available || !items.length) {\n"
    "                    body = '<div class=\"tiq-placeholder\">' +\n"
    "                           '<div class=\"tiq-placeholder-title\">' + _tiqEsc(s.label || k) + '</div>' +\n"
    "                           '<div>' + _tiqEsc(s.sub || 'Warming up.') + '</div>' +\n"
    "                           '</div>';\n"
    "                } else {\n"
    "                    body = _tiqSourceSheet({\n"
    "                        kind: 'music', source: k, label: s.label || k, rows: items,\n"
    "                        metricLabel: 'Streams', entity: 'Music', wide: true,\n"
    "                        keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },\n"
    "                        subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },\n"
    "                        badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }\n"
    "                    });\n"
    "                }\n"
    "                return '<div class=\"tiq-social-panel' + active + '\" data-tiq-music=\"' + k + '\">' + body + '</div>';\n"
    "            }).join('');\n"
    "\n"
    "            return '<div class=\"tiq-social-tabs\">' + tabs + '</div>' +\n"
    "                   '<div class=\"tiq-social-panels\">' + panels + '</div>';"
)
music_new = (
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
src = sub_once(music_old, music_new, "music grid")

# ------------------------------------------------------------------ #
# 2b. Podcasts: sub-tabs -> 3-up grid of source cards.
# ------------------------------------------------------------------ #
pod_old = (
    "            if (!window.__trendsIQ.activePodcasts || !sources[window.__trendsIQ.activePodcasts]) {\n"
    "                window.__trendsIQ.activePodcasts = keys.find(function(k) {\n"
    "                    return (sources[k] || {}).available;\n"
    "                }) || keys[0];\n"
    "            }\n"
    "\n"
    "            var tabs = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var active = (k === window.__trendsIQ.activePodcasts) ? ' active' : '';\n"
    "                var unavail = !s.available ? ' unavailable' : '';\n"
    "                return '<div class=\"tiq-social-tab' + active + unavail + '\" onclick=\"setTrendsIQPodcasts(\\'' + k + '\\')\">' + _tiqEsc(s.label || k) + '</div>';\n"
    "            }).join('');\n"
    "\n"
    "            var panels = keys.map(function(k) {\n"
    "                var s = sources[k] || {};\n"
    "                var items = s.items || [];\n"
    "                var active = (k === window.__trendsIQ.activePodcasts) ? ' active' : '';\n"
    "                var body;\n"
    "                if (!s.available || !items.length) {\n"
    "                    body = '<div class=\"tiq-placeholder\">' +\n"
    "                           '<div class=\"tiq-placeholder-title\">' + _tiqEsc(s.label || k) + '</div>' +\n"
    "                           '<div>' + _tiqEsc(s.sub || 'Warming up.') + '</div>' +\n"
    "                           '</div>';\n"
    "                } else {\n"
    "                    body = _tiqSourceSheet({\n"
    "                        kind: 'podcast', source: k, label: s.label || k, rows: items,\n"
    "                        metricLabel: 'Listeners', entity: 'Podcast', wide: true,\n"
    "                        keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },\n"
    "                        subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },\n"
    "                        badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }\n"
    "                    });\n"
    "                }\n"
    "                return '<div class=\"tiq-social-panel' + active + '\" data-tiq-podcasts=\"' + k + '\">' + body + '</div>';\n"
    "            }).join('');\n"
    "\n"
    "            return '<div class=\"tiq-social-tabs\">' + tabs + '</div>' +\n"
    "                   '<div class=\"tiq-social-panels\">' + panels + '</div>';"
)
pod_new = (
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
src = sub_once(pod_old, pod_new, "podcasts grid")

# ------------------------------------------------------------------ #
# 2c. Gaming: sub-tabs -> 3-up grid; grouped buckets become own cards.
# ------------------------------------------------------------------ #
game_old = (
    "            if (!window.__trendsIQ.activeGaming ||\n"
    "                !gaming[window.__trendsIQ.activeGaming]) {\n"
    "                window.__trendsIQ.activeGaming = keys.find(function(k) {\n"
    "                    return (gaming[k] || {}).available;\n"
    "                }) || keys[0];\n"
    "            }\n"
    "\n"
    "            var tabs = keys.map(function(k) {\n"
    "                var s = gaming[k] || {};\n"
    "                var active = (k === window.__trendsIQ.activeGaming) ? ' active' : '';\n"
    "                var unavail = !s.available ? ' unavailable' : '';\n"
    "                return '<div class=\"tiq-social-tab' + active + unavail + '\" onclick=\"setTrendsIQGaming(\\'' + k + '\\')\">' + _tiqEsc(s.label || k) + '</div>';\n"
    "            }).join('');\n"
)
game_new = ""  # remove the active-default + sub-tab strip entirely
src = sub_once(game_old, game_new, "gaming tabs removal")

game_panels_old = (
    "            var panels = keys.map(function(k) {\n"
    "                var s = gaming[k] || {};\n"
    "                var active = (k === window.__trendsIQ.activeGaming) ? ' active' : '';\n"
    "                var body;\n"
    "                var buckets = groupedBuckets[k];\n"
    "                if (!s.available) {\n"
    "                    body = '<div class=\"tiq-placeholder\">' +\n"
    "                           '<div class=\"tiq-placeholder-title\">' + _tiqEsc(s.label || k) + '</div>' +\n"
    "                           '<div>Loading.</div>' +\n"
    "                           '</div>';\n"
    "                } else if (buckets && buckets.some(function(b) { return Array.isArray(s[b[0]]); })) {\n"
    "                    // Grouped panel: render each populated bucket as its own\n"
    "                    // side-by-side sheet; collapse to one column when only\n"
    "                    // one bucket has rows (same fallback FAST uses).\n"
    "                    var populated = buckets.filter(function(b) {\n"
    "                        return (s[b[0]] || []).length;\n"
    "                    });\n"
    "                    if (!populated.length) {\n"
    "                        body = '<div class=\"tiq-empty\">No trending games right now.</div>';\n"
    "                    } else {\n"
    "                        var cols = populated.length === 1\n"
    "                            ? '1fr'\n"
    "                            : populated.map(function() { return '1fr'; }).join(' ');\n"
    "                        var gridInner = populated.map(function(b) {\n"
    "                            return gameCol((s.label ? s.label + ' \\u00b7 ' : '') + b[1], s[b[0]]);\n"
    "                        }).join('');\n"
    "                        body = '<div class=\"tiq-cards-grid\" style=\"grid-template-columns: ' + cols + ';\">' +\n"
    "                               gridInner +\n"
    "                               '</div>';\n"
    "                    }\n"
    "                } else if (!(s.items || []).length) {\n"
    "                    body = '<div class=\"tiq-empty\">No trending games right now.</div>';\n"
    "                } else {\n"
    "                    // Single-list panel (Xbox).\n"
    "                    body = gameCol(s.label || k, s.items, true);\n"
    "                }\n"
    "                return '<div class=\"tiq-social-panel' + active + '\" data-tiq-gaming=\"' + k + '\">' + body + '</div>';\n"
    "            }).join('');\n"
    "\n"
    "            return '<div class=\"tiq-social-tabs\">' + tabs + '</div>' +\n"
    "                   '<div class=\"tiq-social-panels\">' + panels + '</div>';"
)
game_panels_new = (
    "            // 2026-09-02 (v2): all platforms on one page, each source (and\n"
    "            // each grouped bucket) as its own card in a 3-up grid, keeping\n"
    "            // the sortable 3-column sheet (Title | Plays | Delta%).\n"
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
src = sub_once(game_panels_old, game_panels_new, "gaming grid")

assert src.rstrip().endswith("</html>")
new_lines = src.count("\n")
print("lines %d -> %d (%+d)" % (orig_lines, new_lines, new_lines - orig_lines))
with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
