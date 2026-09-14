#!/usr/bin/env python3
"""Convert Rankers Music / Podcasts / Gaming tabs to the FAST/Streaming
sortable-sheet structure (Title | metric | Δ%), with per-source sub-tabs,
full-number display, poster thumbnails, watchlist/drilldown row actions,
and the shared Export-CSV card head. Talent Ranker is left untouched.

Genre / release-year filter pills and the combined "ALL" card are
intentionally omitted for these three tabs: their rows carry no
genres[]/year arrays, and their sources measure different units
(streams vs listeners vs plays) so a merged ranking would mislead.

Safe editor for the very large templates/index.html: anchor-based slice
replacement + str.replace, with strict integrity checks before write.
"""
import io, sys

PATH = "templates/index.html"

with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

orig_len = len(src)
orig_lines = src.count("\n")

def count(sub):
    return src.count(sub)

# ---------------------------------------------------------------------------
# Shared sheet helper (hoisted; used by Music/Podcasts/Gaming renderers).
# ---------------------------------------------------------------------------
HELPER = r'''        // ------------------------------------------------------------------
        // Rankers source sheet (Music / Podcasts / Gaming)
        //
        // Mirrors renderTIQFast's inner renderCol: a sortable, lineated
        // sheet with [Title | <metric> | Delta%] columns, the Title sort
        // mini-dropdown, full-number display, poster thumbnails, watchlist
        // + drilldown row actions, and the shared Export-CSV card head.
        // Genre / release-year filter pills are intentionally omitted here
        // (these rows carry no genres[]/year), and no combined ALL card is
        // built (sources measure different units - streams vs listeners vs
        // plays - so a merged ranking would mislead).
        //
        // opts = { kind, source, label, rows, metricLabel, entity,
        //          subOf(it), badgeOf(it), keyOf(it) }
        // ------------------------------------------------------------------
        function _tiqSourceSheet(opts) {
            opts = opts || {};
            var kind = opts.kind || 'music';
            var source = opts.source || '';
            var rows = opts.rows || [];
            var metricLabel = opts.metricLabel || 'Streams';
            var entity = opts.entity || 'Music';
            var subOf   = (typeof opts.subOf   === 'function') ? opts.subOf   : function() { return ''; };
            var badgeOf = (typeof opts.badgeOf === 'function') ? opts.badgeOf : function() { return ''; };
            var keyOf   = (typeof opts.keyOf   === 'function') ? opts.keyOf   : function(it) { return it.title; };
            var rowsHtml = rows.map(function(it, i) {
                var us = it.us_streams || {};
                var estimate = Number(us.us_estimate);
                var hasVal = isFinite(estimate) && estimate > 0;
                var valNum = hasVal ? _tiqFullNum(estimate) : '';
                var deltaHtml = '<span class="tiq-sheet-delta flat">\u2014</span>';
                var deltaSort = '';
                if (us.delta_kind === 'rank' && typeof us.delta_positions === 'number' && us.delta_positions !== 0 && us.direction) {
                    var pos = Math.abs(us.delta_positions);
                    var glyph = (us.direction === 'up') ? '\u2191' : (us.direction === 'down') ? '\u2193' : '\u00b7';
                    var dcls  = (us.direction === 'up') ? 'up' : (us.direction === 'down') ? 'down' : 'flat';
                    deltaHtml = '<span class="tiq-sheet-delta ' + dcls + '">' + glyph + ' ' + pos + '</span>';
                    deltaSort = String((us.direction === 'up') ? pos : -pos);
                } else if (typeof us.delta_pct === 'number' && us.direction) {
                    var pct = Math.round(Math.abs(us.delta_pct) * 100);
                    if (pct >= 1) {
                        var glyph2 = (us.direction === 'up') ? '\u2191' : (us.direction === 'down') ? '\u2193' : '\u00b7';
                        var dcls2  = (us.direction === 'up') ? 'up' : (us.direction === 'down') ? 'down' : 'flat';
                        var signed = (us.direction === 'up') ? pct : (us.direction === 'down') ? -pct : 0;
                        deltaHtml = '<span class="tiq-sheet-delta ' + dcls2 + '">' + glyph2 + ' ' + pct + '%</span>';
                        deltaSort = String(signed);
                    }
                }
                var tipParts = [];
                if (hasVal) {
                    tipParts.push(valNum + ' ' + (us.unit_label || metricLabel.toLowerCase()));
                    if (us.method) tipParts.push(String(us.method));
                    if (us.as_of_date) tipParts.push('As of ' + us.as_of_date);
                }
                var valTip = _tiqEsc(tipParts.join(' \u2014 '));
                var img = _tiqPosterCell(it.image);
                var wkey = keyOf(it);
                var sub = subOf(it) || '';
                var newBadge = it.recently_added ? ' <span class="tiq-gaming-new">NEW</span>' : '';
                var rk = it.bucket_rank || it.rank || (i + 1);
                var sortTitle = _tiqEsc(String(it.title || '').toLowerCase());
                var dispName = _tiqEsc(String(it.title || ''));
                var artistAttr = _tiqEsc(String(it.artist || it.publisher || ''));
                var genreAttr = _tiqEsc(String(it.genre || ''));
                return '<tr class="tiq-sheet-row" data-order="' + i + '" data-title="' + sortTitle + '" data-name="' + dispName + '" data-artist="' + artistAttr + '" data-genres="' + genreAttr + '" data-views="' + (hasVal ? estimate : '') + '" data-delta="' + deltaSort + '">' +
                       '<td class="tiq-sheet-td-title">' +
                         '<div class="tiq-sheet-titlecell">' +
                           '<span class="tiq-sheet-rank">' + rk + '</span>' +
                           img +
                           '<div class="tiq-sheet-titlemain">' +
                             '<div class="tiq-row-title">' + _tiqEsc(it.title) + newBadge + badgeOf(it) +
                             _tiqActions(kind, source, wkey, wkey, 'National') + '</div>' +
                             (sub ? '<div class="tiq-fast-genres">' + sub + '</div>' : '') +
                           '</div>' +
                         '</div>' +
                       '</td>' +
                       '<td class="tiq-sheet-td-num tiq-sheet-td-views" title="' + valTip + '">' + (valNum || '<span class="tiq-sheet-dim">\u2014</span>') + '</td>' +
                       '<td class="tiq-sheet-td-num">' + deltaHtml + '</td>' +
                       '</tr>';
            }).join('');
            return '<div class="tiq-card">' + _tiqSheetCardHead(opts.label) +
                   '<table class="tiq-sheet" data-entity="' + entity + '" data-metric="' + _tiqEsc(metricLabel) + '" data-export="' + _tiqEsc(String(source) + '-' + (opts.label || '')) + '">' +
                     '<thead><tr>' +
                       '<th class="tiq-sheet-th-title"><span class="tiq-sheet-th-label">Title</span><select class="tiq-sheet-titlesort" onchange="tiqSortFastSheetTitle(this)"><option value="">Sort</option><option value="hi">High \u2192 Low</option><option value="lo">Low \u2192 High</option><option value="az">A\u2013Z</option></select></th>' +
                       '<th class="tiq-sheet-th-num tiq-sheet-sortable" onclick="tiqSortFastSheet(this,\'views\')">' + _tiqEsc(metricLabel) + '<span class="tiq-sort-ind">\u21c5</span></th>' +
                       '<th class="tiq-sheet-th-num tiq-sheet-sortable" onclick="tiqSortFastSheet(this,\'delta\')">\u0394%<span class="tiq-sort-ind">\u21c5</span></th>' +
                     '</tr></thead>' +
                     '<tbody>' + (rowsHtml || '<tr><td colspan="3" class="tiq-empty">No data.</td></tr>') + '</tbody>' +
                   '</table></div>';
        }

'''

# ---------------------------------------------------------------------------
# renderTIQMusic  +  setTrendsIQMusic
# ---------------------------------------------------------------------------
NEW_MUSIC = r'''        function renderTIQMusic(sources) {
            // Amazon Music is live as of 2026-07-27 (Playwright + donated
            // music.amazon.com cookies scrape the "All Hits" editorial
            // flagship playlist). If cookies expire the card gracefully
            // falls back to a "warming up" placeholder.
            //
            // 2026-09-02: converted from a flat card-per-source grid to the
            // FAST/Streaming structure - one source at a time via sub-tabs,
            // each a sortable, lineated sheet (Title | Streams | Delta%)
            // with full-number display + Export CSV.
            var order = ['spotify', 'apple', 'youtube', 'shazam', 'amazon'];
            var allKeys = Object.keys(sources || {});
            if (!allKeys.length) {
                return '<div class="tiq-empty">Music charts are warming up. Check back later.</div>';
            }
            var keys = order.filter(function(k) { return sources[k]; })
                .concat(allKeys.filter(function(k) { return order.indexOf(k) === -1; }));

            if (!window.__trendsIQ.activeMusic || !sources[window.__trendsIQ.activeMusic]) {
                window.__trendsIQ.activeMusic = keys.find(function(k) {
                    return (sources[k] || {}).available;
                }) || keys[0];
            }

            var tabs = keys.map(function(k) {
                var s = sources[k] || {};
                var active = (k === window.__trendsIQ.activeMusic) ? ' active' : '';
                var unavail = !s.available ? ' unavailable' : '';
                return '<div class="tiq-social-tab' + active + unavail + '" onclick="setTrendsIQMusic(\'' + k + '\')">' + _tiqEsc(s.label || k) + '</div>';
            }).join('');

            var panels = keys.map(function(k) {
                var s = sources[k] || {};
                var items = s.items || [];
                var active = (k === window.__trendsIQ.activeMusic) ? ' active' : '';
                var body;
                if (!s.available || !items.length) {
                    body = '<div class="tiq-placeholder">' +
                           '<div class="tiq-placeholder-title">' + _tiqEsc(s.label || k) + '</div>' +
                           '<div>' + _tiqEsc(s.sub || 'Warming up.') + '</div>' +
                           '</div>';
                } else {
                    body = _tiqSourceSheet({
                        kind: 'music', source: k, label: s.label || k, rows: items,
                        metricLabel: 'Streams', entity: 'Music',
                        keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },
                        subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },
                        badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }
                    });
                }
                return '<div class="tiq-social-panel' + active + '" data-tiq-music="' + k + '">' + body + '</div>';
            }).join('');

            return '<div class="tiq-social-tabs">' + tabs + '</div>' +
                   '<div class="tiq-social-panels">' + panels + '</div>';
        }
        window.setTrendsIQMusic = function(key) {
            window.__trendsIQ.activeMusic = key;
            var sources = (window.__trendsIQ.lastData &&
                           window.__trendsIQ.lastData.cards &&
                           window.__trendsIQ.lastData.cards.music_trending) || {};
            document.querySelectorAll('#trendsIQPanels [data-tiq-panel="music"] .tiq-social-tab').forEach(function(el) {
                var s = sources[key] || {};
                var isActive = el.textContent.trim().toLowerCase() === (s.label || key).toLowerCase();
                el.classList.toggle('active', isActive);
            });
            document.querySelectorAll('#trendsIQPanels [data-tiq-music]').forEach(function(el) {
                el.classList.toggle('active', el.getAttribute('data-tiq-music') === key);
            });
            if (typeof applyTrendsIQLens === 'function') applyTrendsIQLens();
        };

'''

# ---------------------------------------------------------------------------
# renderTIQPodcasts  +  setTrendsIQPodcasts
# ---------------------------------------------------------------------------
NEW_POD = r'''        function renderTIQPodcasts(sources) {
            // Order: Apple (biggest chart) -> Spotify -> YouTube Podcasts
            // -> Netflix (curated video-podcast lineup) -> Amazon -> Audible.
            //
            // 2026-09-02: converted to the FAST/Streaming sheet structure -
            // one source at a time via sub-tabs, each a sortable sheet
            // (Title | Listeners | Delta%) with full-number display + Export.
            var order = ['apple', 'spotify', 'youtube_podcasts', 'netflix', 'amazon', 'audible'];
            var allKeys = Object.keys(sources || {});
            if (!allKeys.length) {
                return '<div class="tiq-empty">Podcast charts are warming up. Check back later.</div>';
            }
            var keys = order.filter(function(k) { return sources[k]; })
                .concat(allKeys.filter(function(k) { return order.indexOf(k) === -1; }));

            if (!window.__trendsIQ.activePodcasts || !sources[window.__trendsIQ.activePodcasts]) {
                window.__trendsIQ.activePodcasts = keys.find(function(k) {
                    return (sources[k] || {}).available;
                }) || keys[0];
            }

            var tabs = keys.map(function(k) {
                var s = sources[k] || {};
                var active = (k === window.__trendsIQ.activePodcasts) ? ' active' : '';
                var unavail = !s.available ? ' unavailable' : '';
                return '<div class="tiq-social-tab' + active + unavail + '" onclick="setTrendsIQPodcasts(\'' + k + '\')">' + _tiqEsc(s.label || k) + '</div>';
            }).join('');

            var panels = keys.map(function(k) {
                var s = sources[k] || {};
                var items = s.items || [];
                var active = (k === window.__trendsIQ.activePodcasts) ? ' active' : '';
                var body;
                if (!s.available || !items.length) {
                    body = '<div class="tiq-placeholder">' +
                           '<div class="tiq-placeholder-title">' + _tiqEsc(s.label || k) + '</div>' +
                           '<div>' + _tiqEsc(s.sub || 'Warming up.') + '</div>' +
                           '</div>';
                } else {
                    body = _tiqSourceSheet({
                        kind: 'podcast', source: k, label: s.label || k, rows: items,
                        metricLabel: 'Listeners', entity: 'Podcast',
                        keyOf: function(it) { return it.title + ' - ' + (it.artist || ''); },
                        subOf: function(it) { return it.artist ? _tiqEsc(it.artist) : ''; },
                        badgeOf: function(it) { return _tiqCrossPlatformBadge(it); }
                    });
                }
                return '<div class="tiq-social-panel' + active + '" data-tiq-podcasts="' + k + '">' + body + '</div>';
            }).join('');

            return '<div class="tiq-social-tabs">' + tabs + '</div>' +
                   '<div class="tiq-social-panels">' + panels + '</div>';
        }
        window.setTrendsIQPodcasts = function(key) {
            window.__trendsIQ.activePodcasts = key;
            var sources = (window.__trendsIQ.lastData &&
                           window.__trendsIQ.lastData.cards &&
                           window.__trendsIQ.lastData.cards.podcasts_trending) || {};
            document.querySelectorAll('#trendsIQPanels [data-tiq-panel="podcasts"] .tiq-social-tab').forEach(function(el) {
                var s = sources[key] || {};
                var isActive = el.textContent.trim().toLowerCase() === (s.label || key).toLowerCase();
                el.classList.toggle('active', isActive);
            });
            document.querySelectorAll('#trendsIQPanels [data-tiq-podcasts]').forEach(function(el) {
                el.classList.toggle('active', el.getAttribute('data-tiq-podcasts') === key);
            });
            if (typeof applyTrendsIQLens === 'function') applyTrendsIQLens();
        };

'''

# ---------------------------------------------------------------------------
# renderTIQGaming  (keeps existing setTrendsIQGaming untouched)
# ---------------------------------------------------------------------------
NEW_GAMING = r'''        function renderTIQGaming(gaming) {
            var allKeys = Object.keys(gaming || {});
            if (!allKeys.length) {
                return '<div class="tiq-empty">Gaming rankings are warming up.</div>';
            }
            var order = ['xbox_gamepass', 'meta_quest', 'steam'];
            var keys = order.filter(function(k) { return gaming[k]; })
                .concat(allKeys.filter(function(k) { return order.indexOf(k) === -1; }));

            if (!window.__trendsIQ.activeGaming ||
                !gaming[window.__trendsIQ.activeGaming]) {
                window.__trendsIQ.activeGaming = keys.find(function(k) {
                    return (gaming[k] || {}).available;
                }) || keys[0];
            }

            var tabs = keys.map(function(k) {
                var s = gaming[k] || {};
                var active = (k === window.__trendsIQ.activeGaming) ? ' active' : '';
                var unavail = !s.available ? ' unavailable' : '';
                return '<div class="tiq-social-tab' + active + unavail + '" onclick="setTrendsIQGaming(\'' + k + '\')">' + _tiqEsc(s.label || k) + '</div>';
            }).join('');

            // 2026-09-02: converted to the FAST/Streaming sheet structure.
            // Each column (Xbox list, or Meta Quest Free/Paid, or Steam
            // Most Played/Top Sellers) is a sortable sheet (Title | Plays |
            // Delta%) with full-number display + Export CSV.
            var gsub = function(it) {
                var pub = it.publisher ? _tiqEsc(it.publisher) : '';
                var genre = it.genre ? _tiqEsc(it.genre) : '';
                return pub + ((pub && genre) ? ' \u00b7 ' : '') + genre;
            };
            var gameCol = function(colLabel, rows) {
                return _tiqSourceSheet({
                    kind: 'gaming', source: 'gaming', label: colLabel, rows: rows || [],
                    metricLabel: 'Plays', entity: 'Gaming',
                    keyOf: function(it) { return it.title; },
                    subOf: gsub
                });
            };

            // Grouped-panel bucket schema (Meta Quest, Steam): [key, header].
            var groupedBuckets = {
                'meta_quest': [['free',        'Free'],
                                ['paid',        'Paid']],
                'steam':      [['most_played', 'Most Played'],
                                ['top_sellers', 'Top Sellers']]
            };

            var panels = keys.map(function(k) {
                var s = gaming[k] || {};
                var active = (k === window.__trendsIQ.activeGaming) ? ' active' : '';
                var body;
                var buckets = groupedBuckets[k];
                if (!s.available) {
                    body = '<div class="tiq-placeholder">' +
                           '<div class="tiq-placeholder-title">' + _tiqEsc(s.label || k) + '</div>' +
                           '<div>Loading.</div>' +
                           '</div>';
                } else if (buckets && buckets.some(function(b) { return Array.isArray(s[b[0]]); })) {
                    // Grouped panel: render each populated bucket as its own
                    // side-by-side sheet; collapse to one column when only
                    // one bucket has rows (same fallback FAST uses).
                    var populated = buckets.filter(function(b) {
                        return (s[b[0]] || []).length;
                    });
                    if (!populated.length) {
                        body = '<div class="tiq-empty">No trending games right now.</div>';
                    } else {
                        var cols = populated.length === 1
                            ? '1fr'
                            : populated.map(function() { return '1fr'; }).join(' ');
                        var gridInner = populated.map(function(b) {
                            return gameCol((s.label ? s.label + ' \u00b7 ' : '') + b[1], s[b[0]]);
                        }).join('');
                        body = '<div class="tiq-cards-grid" style="grid-template-columns: ' + cols + ';">' +
                               gridInner +
                               '</div>';
                    }
                } else if (!(s.items || []).length) {
                    body = '<div class="tiq-empty">No trending games right now.</div>';
                } else {
                    // Single-list panel (Xbox).
                    body = gameCol(s.label || k, s.items);
                }
                return '<div class="tiq-social-panel' + active + '" data-tiq-gaming="' + k + '">' + body + '</div>';
            }).join('');

            return '<div class="tiq-social-tabs">' + tabs + '</div>' +
                   '<div class="tiq-social-panels">' + panels + '</div>';
        }
'''

# ===========================================================================
# Apply slice replacements
# ===========================================================================
def replace_span(src, start_anchor, end_anchor, new_text, label):
    assert src.count(start_anchor) == 1, "start anchor not unique: " + label
    assert src.count(end_anchor) == 1, "end anchor not unique: " + label
    si = src.index(start_anchor)
    ei = src.index(end_anchor)
    assert si < ei, "start after end: " + label
    return src[:si] + new_text + src[ei:]

# Music: from the function up to the "Podcasts / Books / Libby" divider block.
MUSIC_START = "        function renderTIQMusic(sources) {\n"
PODCASTS_DIVIDER = ("        // ============================================================\n"
                    "        // Podcasts / Books / Libby\n")
src = replace_span(src, MUSIC_START, PODCASTS_DIVIDER, HELPER + NEW_MUSIC, "music")

# Podcasts: from the function up to renderTIQBooks.
POD_START = "        function renderTIQPodcasts(sources) {\n"
BOOKS_START = "        function renderTIQBooks(sources, libbySources) {\n"
src = replace_span(src, POD_START, BOOKS_START, NEW_POD, "podcasts")

# Gaming: from the function up to its (unchanged) setter.
GAME_START = "        function renderTIQGaming(gaming) {\n"
GAME_SETTER = "        window.setTrendsIQGaming = function(key) {\n"
src = replace_span(src, GAME_START, GAME_SETTER, NEW_GAMING, "gaming")

# ===========================================================================
# Extend tiqExportFastCsv: data-metric + Music/Podcast/Gaming entity schemas
# ===========================================================================
OLD_HEADER = ("            var header = isTitle\n"
              "                ? ['Rank', 'Title', 'Year', 'Genres', 'Views', 'Change %']\n"
              "                : ['Rank', 'Channel', 'Views', 'Change %'];\n")
NEW_HEADER = ("            var metricLabel = table.getAttribute('data-metric') || 'Views';\n"
              "            var header;\n"
              "            if (entity === 'Channel')      header = ['Rank', 'Channel', metricLabel, 'Change %'];\n"
              "            else if (entity === 'Music')   header = ['Rank', 'Title', 'Artist', metricLabel, 'Change %'];\n"
              "            else if (entity === 'Podcast') header = ['Rank', 'Title', 'Publisher', metricLabel, 'Change %'];\n"
              "            else if (entity === 'Gaming')  header = ['Rank', 'Title', 'Publisher', 'Genre', metricLabel, 'Change %'];\n"
              "            else                           header = ['Rank', 'Title', 'Year', 'Genres', metricLabel, 'Change %'];\n")
assert src.count(OLD_HEADER) == 1, "export header anchor not unique"
src = src.replace(OLD_HEADER, NEW_HEADER)

OLD_COLS = ("                var cols = isTitle\n"
            "                    ? [rank, name, r.getAttribute('data-year') || '', r.getAttribute('data-genres') || '', views, deltaStr]\n"
            "                    : [rank, name, views, deltaStr];\n")
NEW_COLS = ("                var cols;\n"
            "                if (entity === 'Channel')      cols = [rank, name, views, deltaStr];\n"
            "                else if (entity === 'Music')   cols = [rank, name, r.getAttribute('data-artist') || '', views, deltaStr];\n"
            "                else if (entity === 'Podcast') cols = [rank, name, r.getAttribute('data-artist') || '', views, deltaStr];\n"
            "                else if (entity === 'Gaming')  cols = [rank, name, r.getAttribute('data-artist') || '', r.getAttribute('data-genres') || '', views, deltaStr];\n"
            "                else                           cols = [rank, name, r.getAttribute('data-year') || '', r.getAttribute('data-genres') || '', views, deltaStr];\n")
assert src.count(OLD_COLS) == 1, "export cols anchor not unique"
src = src.replace(OLD_COLS, NEW_COLS)

# ===========================================================================
# Integrity checks
# ===========================================================================
assert src.rstrip().endswith("</html>"), "file no longer ends with </html>"
for tok, n in [
    ("function _tiqSourceSheet(opts) {", 1),
    ("function renderTIQMusic(sources) {", 1),
    ("function renderTIQPodcasts(sources) {", 1),
    ("function renderTIQGaming(gaming) {", 1),
    ("window.setTrendsIQMusic = function(key)", 1),
    ("window.setTrendsIQPodcasts = function(key)", 1),
    ("window.setTrendsIQGaming = function(key)", 1),
    ('data-tiq-music="', 1),
    ('data-tiq-podcasts="', 1),
    ('data-metric="', None),  # >=1
]:
    c = src.count(tok)
    if n is None:
        assert c >= 1, "missing token: " + tok
    else:
        assert c == n, "token count %d != %d for: %s" % (c, n, tok)

new_lines = src.count("\n")
print("orig chars=%d lines=%d  ->  new chars=%d lines=%d (delta lines=%+d)" % (
    orig_len, orig_lines, len(src), new_lines, new_lines - orig_lines))

with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WROTE", PATH)
