#!/usr/bin/env python3
"""Profile IQ: category reach on the Behavioral page, and the Min % /
Min index filter on the Interests page (2026-11-11, Armine).

1. Each behavioral category card carries one line under its eyebrow:
   "61.3% engage, index 119 (1.2x the US average)". The figures come
   from /api/profile-iq/category-reach/<key> (migration/category_rollup,
   the same rollup the insights deck and Prometheus use); the page never
   computes them.
2. The Jump to menu lists each category with its percent and index and
   can be sorted by either.
3. The Behavioral CSV's title row carries the same two figures.
4. The Interests list gets the Behavioral cards' Filter button and the
   same Min % / Min index panel, on the same state and handlers
   (window._piqBehMins, key '__interests__'); the shared row test is
   _piqMinHides. Rows with no Gen Pop match are never hidden by the
   index floor, exactly as on the Behavioral cards.

Byte-level splice (templates/index.html is too large for an in-editor
replace). Every anchor must occur exactly once. Run, then
`python3 scripts/validate_index_html.py`.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_category_reach.html")

EDITS = []


def edit(desc, old, new):
    EDITS.append((desc, old, new))


# ------------------------------------------------------------------ CSS
edit("css", """        .behavioral-item.beh-min-hidden { display: none !important; }
""", """        .behavioral-item.beh-min-hidden { display: none !important; }
        /* Category reach line under a behavioral card's eyebrow (2026-11-11) */
        #dashboardView.piq-v4 .piq-cat-reach {
            margin: -2px 0 8px; font-size: 12px; line-height: 1.45; color: #9AA09B;
            text-transform: none; letter-spacing: 0;
        }
        #dashboardView.piq-v4 .piq-cat-reach b { color: #E9E8E1; font-weight: 600; }
        body[data-theme="light"] #dashboardView.piq-v4 .piq-cat-reach { color: #5C6560; }
        body[data-theme="light"] #dashboardView.piq-v4 .piq-cat-reach b { color: #0C1618; }
        :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-fig {
            margin-left: auto; padding-left: 16px; font-size: 11px; color: #9AA09B;
            white-space: nowrap; font-variant-numeric: tabular-nums;
        }
        :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-menu.has-fig { min-width: 300px; }
        :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-sort {
            display: flex; align-items: center; gap: 6px; padding: 6px 10px 8px; margin: 0 0 2px;
            position: sticky; top: -6px; z-index: 2; background: #15252A;
            font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #7C878A;
        }
        body[data-theme="light"] :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-sort { background: #FFFFFF; }
        :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-sort button {
            padding: 3px 9px; border-radius: 6px; background: transparent; border: 1px solid #3B3D38;
            color: #9AA09B; font: inherit; letter-spacing: inherit; text-transform: inherit; cursor: pointer;
        }
        :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-sort button.is-on { border-color: #C7F23E; color: #C7F23E; }
        body[data-theme="light"] :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-fig { color: #5C6560; }
        body[data-theme="light"] :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-sort button { border-color: #C9C6BA; color: #5C6560; }
        body[data-theme="light"] :is(#dashboardView, #piqStickyChrome).piq-v4 #piqBehJump .piq-jump-sort button.is-on { border-color: #5E7E12; color: #5E7E12; }
        #dashboardView.piq-v4 #tab-interests .piq-beh-filter { margin: 10px 0 10px; }
        #dashboardView.piq-v4 #tab-interests .piq-int-empty { padding: 18px 12px; font-size: 12px; color: #9AA09B; }
        body[data-theme="light"] #dashboardView.piq-v4 #tab-interests .piq-int-empty { color: #5C6560; }
""")

# ---------------------------------------- the jump menu keeps its label
edit("jump label", """                    if (curEl) curEl.textContent = opt.textContent;
""", """                    if (curEl) curEl.textContent = opt.getAttribute('data-label') || opt.textContent;
""")

# ------------------------------- paint after the behavioral page dresses
edit("dress hook", """                window._piqV4SetHtml(call, next);
            }
        };
        window._piqV4DressInterests = function() {
""", """                window._piqV4SetHtml(call, next);
            }
            try { if (window._piqCatReachEnsure) { window._piqCatReachEnsure(); window._piqCatReachPaint(); } } catch (_cr) {}
        };
        window._piqV4DressInterests = function() {
""")

# ------------------------------------ Interests head: the Filter button
edit("interests head", """            if (head) {
                head.innerHTML = '<div class="stack"><div class="piq-eyebrow"><span class="num">01</span>All Interests</div><h3>Ranked by index vs Gen Pop</h3></div><div class="piq-head-actions"><span class="piq-chip">' + window._piqV4Esc(count || 'tracked') + '</span>' +
                    (window._piqV4CsvBtn ? window._piqV4CsvBtn('interests', 'All Interests') : '') + '</div>';
            }
""", """            // Rewriting on every pass swaps the buttons out from under a
            // click, so only when the count changes (as the cards do).
            if (head && (head.dataset.piqHead !== ('c|' + count) || !head.querySelector('.piq-beh-filter-btn'))) {
                head.innerHTML = '<div class="stack"><div class="piq-eyebrow"><span class="num">01</span>All Interests</div><h3>Ranked by index vs Gen Pop</h3></div><div class="piq-head-actions">' +
                    '<button type="button" class="piq-beh-filter-btn" data-cat="__interests__" title="Hide rows below a minimum % or index">Filter</button>' +
                    '<span class="piq-chip">' + window._piqV4Esc(count || 'tracked') + '</span>' +
                    (window._piqV4CsvBtn ? window._piqV4CsvBtn('interests', 'All Interests') : '') + '</div>';
                head.dataset.piqHead = 'c|' + count;
            }
            try { if (window._piqApplyIntMins) window._piqApplyIntMins(); } catch (_im) {}
""")

# --------------------------- one row test and one panel for both pages
edit("shared row test", """                    let hide = false;
                    if (active) {
                        const pct = Number(row.getAttribute('data-pct'));
                        const rawIdx = row.getAttribute('data-index');
                        if (Number.isFinite(m.pct) && !(pct >= m.pct)) hide = true;
                        if (!hide && Number.isFinite(m.idx) && rawIdx !== null && rawIdx !== '' && Number(rawIdx) < m.idx) hide = true;
                    }
""", """                    const hide = _piqMinHides(m, row.getAttribute('data-pct'), row.getAttribute('data-index'));
""")

edit("shared panel", """                    panel.innerHTML =
                        '<label>Min %<input type="number" min="0" step="0.1" data-k="pct" placeholder="0"></label>' +
                        '<label>Min index<input type="number" min="0" step="1" data-k="idx" placeholder="0"></label>' +
                        '<button type="button" class="piq-beh-filter-clear">Clear</button>' +
                        '<span class="piq-beh-filter-status"></span>';
""", """                    panel.innerHTML = _piqMinPanelHtml();
""")

edit("apply entry", """        function _piqApplyBehMins() {
            const grid = document.getElementById('behavioralGrid');
            if (!grid) return;
""", """        // One row test for the Behavioral cards and the Interests list:
        // true when the row sits below the minimum % or the minimum index.
        // A row with no index (no Gen Pop match) is never hidden by the
        // index floor.
        function _piqMinHides(m, pct, rawIdx) {
            if (!m || !(Number.isFinite(m.pct) || Number.isFinite(m.idx))) return false;
            if (Number.isFinite(m.pct) && !(Number(pct) >= m.pct)) return true;
            if (Number.isFinite(m.idx) && rawIdx !== null && rawIdx !== undefined && rawIdx !== '' && Number(rawIdx) < m.idx) return true;
            return false;
        }
        function _piqMinPanelHtml() {
            return '<label>Min %<input type="number" min="0" step="0.1" data-k="pct" placeholder="0"></label>' +
                '<label>Min index<input type="number" min="0" step="1" data-k="idx" placeholder="0"></label>' +
                '<button type="button" class="piq-beh-filter-clear">Clear</button>' +
                '<span class="piq-beh-filter-status"></span>';
        }
        // Interests list (2026-11-11): the same filter, the same state
        // (key '__interests__'), the same handlers. The list filters its
        // data, so its CSV carries the rows on screen.
        function _piqIntMinFilter(items, m) {
            return (items || []).filter(function(it) {
                var hasGp = Number(it && it.genPopPct) > 0;
                return !_piqMinHides(m, it ? it.pct : 0, (hasGp && it.index != null) ? it.index : null);
            });
        }
        window._piqApplyIntMins = function() {
            const KEY = '__interests__';
            const sec = document.querySelector('#tab-interests .interests-list-section');
            if (!sec) return;
            const m = window._piqBehMins[KEY];
            const active = _piqBehMinActive(m);
            const open = !!window._piqBehMinsOpen[KEY];
            const btn = sec.querySelector('.piq-beh-filter-btn');
            if (btn) {
                btn.classList.toggle('is-on', active);
                if (btn.getAttribute('aria-expanded') !== (open ? 'true' : 'false')) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
            }
            let panel = sec.querySelector('.piq-beh-filter');
            if ((open || active) && !panel) {
                panel = document.createElement('div');
                panel.className = 'piq-beh-filter';
                panel.setAttribute('data-cat', KEY);
                panel.innerHTML = _piqMinPanelHtml();
                const grid = document.getElementById('interestsGrid');
                if (grid && grid.parentNode) grid.parentNode.insertBefore(panel, grid);
                else sec.appendChild(panel);
                panel.querySelectorAll('input').forEach(inp => {
                    const v = m ? m[inp.getAttribute('data-k')] : null;
                    inp.value = Number.isFinite(v) ? String(v) : '';
                });
            }
            if (panel && panel.hidden !== !open) panel.hidden = !open;
            const sig = active ? (String(m.pct) + '|' + String(m.idx)) : '';
            if (sig !== (window._piqIntMinSig || '')) {
                window._piqIntMinSig = sig;
                try {
                    if (typeof allInterestsData !== 'undefined' && allInterestsData.length) renderInterestsList(allInterestsData);
                } catch (_) {}
            }
            const status = panel && panel.querySelector('.piq-beh-filter-status');
            if (status) {
                const c = window._piqIntMinCount || {};
                const txt = (active && Number.isFinite(c.total)) ? (c.shown + ' of ' + c.total + ' shown') : '';
                if (status.textContent !== txt) status.textContent = txt;
            }
        };
        function _piqApplyBehMins() {
            try { window._piqApplyIntMins(); } catch (_) {}
            const grid = document.getElementById('behavioralGrid');
            if (!grid) return;
""")

# ------------------------------ Interests list: filter the data, then render
edit("interests filter", """            // Apply search filter
            if (searchInput && searchInput.value.trim()) {
                const searchTerm = searchInput.value.toLowerCase();
                filtered = filtered.filter(item => item.name.toLowerCase().includes(searchTerm));
            }
            
            // Sort based on the active column + direction. Clicking a
""", """            // Apply search filter
            if (searchInput && searchInput.value.trim()) {
                const searchTerm = searchInput.value.toLowerCase();
                filtered = filtered.filter(item => item.name.toLowerCase().includes(searchTerm));
            }

            // Minimum % / minimum index: the Behavioral cards' filter, on
            // the same state (2026-11-11).
            const _intMin = (window._piqBehMins || {})['__interests__'];
            const _intMinOn = (typeof _piqBehMinActive === 'function') && _piqBehMinActive(_intMin);
            const _intMinTotal = filtered.length;
            if (_intMinOn && typeof _piqIntMinFilter === 'function') filtered = _piqIntMinFilter(filtered, _intMin);
            window._piqIntMinCount = { shown: filtered.length, total: _intMinTotal };
            
            // Sort based on the active column + direction. Clicking a
""")

edit("interests render", """            container.innerHTML = headerRow + itemsHtml;

            // Re-inject any active comparison cohort columns (the innerHTML
""", """            container.innerHTML = headerRow + (
                (_intMinOn && !sorted.length)
                    ? '<div class="piq-int-empty">No interests meet these minimums. Lower a minimum or clear the filter to see all ' + _intMinTotal + '.</div>'
                    : itemsHtml);
            try { if (window._piqApplyIntMins) window._piqApplyIntMins(); } catch (_) {}

            // Re-inject any active comparison cohort columns (the innerHTML
""")

# ----------------------------------------- Behavioral CSV: the title row
edit("csv title", """                csvContent = `${profileName} - ${category}${csvSliceLabel ? ' - ' + csvSliceLabel : ''}\\n`;
                csvContent += _piqBehCsvTable(category, getBehavioralCsvItems(category, sliceValue));
""", """                csvContent = `${profileName} - ${category}${csvSliceLabel ? ' - ' + csvSliceLabel : ''}${csvSliceLabel ? '' : _piqCatReachCsvCells(category)}\\n`;
                csvContent += _piqBehCsvTable(category, getBehavioralCsvItems(category, sliceValue));
""")

# ------------------------------------------------- the category reach JS
edit("category reach js", """        const BEH_INDEX_FILTER_LS_KEY = 'cw.behIndexFilter.v1';
""", """        // ============================================================
        // CATEGORY REACH (2026-11-11, Armine)
        // ------------------------------------------------------------
        // Each behavioral category card shows the share of the audience
        // that engages with at least one brand in the category, and the
        // index against the US average. The figures are read from
        // /api/profile-iq/category-reach/<key>; the page never computes
        // them (the insights deck and Prometheus print the same ones).
        // ============================================================
        window._piqCatReach = window._piqCatReach || { key: null, cats: null, sort: 'default' };
        function _piqCatNorm(c) {
            return String(c == null ? '' : c).trim().toUpperCase().replace(/[_\\s]+/g, ' ');
        }
        function _piqCatReachFor(cat) {
            var cats = (window._piqCatReach && window._piqCatReach.cats) || null;
            var r = cats ? cats[_piqCatNorm(cat)] : null;
            return (r && isFinite(Number(r.reach)) && Number(r.reach) > 0) ? r : null;
        }
        // "61.3% engage, index 119 (1.2x the US average)"
        function _piqCatReachText(r) {
            if (!r || !isFinite(Number(r.reach))) return '';
            var s = Number(r.reach).toFixed(1) + '% engage';
            if (r.index != null && isFinite(Number(r.index))) {
                s += ', index ' + Math.round(Number(r.index));
                if (r.times) s += ' (' + r.times + ')';
            }
            return s;
        }
        function _piqCatReachHtml(r) {
            if (!r || !isFinite(Number(r.reach))) return '';
            var h = '<b>' + Number(r.reach).toFixed(1) + '%</b> engage';
            if (r.index != null && isFinite(Number(r.index))) {
                h += ', index <b>' + Math.round(Number(r.index)) + '</b>';
                if (r.times) h += ' (' + String(r.times).replace(/[<>&"]/g, '') + ')';
            }
            return h;
        }
        function _piqCatReachCsvCells(category) {
            var r = _piqCatReachFor(category);
            if (!r) return '';
            var out = ',"' + Number(r.reach).toFixed(1) + '% engage"';
            if (r.index != null && isFinite(Number(r.index))) out += ',"index ' + Math.round(Number(r.index)) + '"';
            return out;
        }
        // rows: [{cat, ord, reach, index}]; mode: 'default' | 'pct' | 'index'
        function _piqCatReachOrder(rows, mode) {
            var out = (rows || []).slice();
            var key = mode === 'pct' ? 'reach' : (mode === 'index' ? 'index' : null);
            out.sort(function(a, b) {
                if (key) {
                    var av = (a[key] == null || !isFinite(Number(a[key]))) ? -Infinity : Number(a[key]);
                    var bv = (b[key] == null || !isFinite(Number(b[key]))) ? -Infinity : Number(b[key]);
                    if (av !== bv) return bv - av;
                }
                return (a.ord || 0) - (b.ord || 0);
            });
            return out;
        }
        window._piqCatReachEnsure = function() {
            var st = window._piqCatReach;
            var key = '';
            try { key = (typeof currentProfileData !== 'undefined' && currentProfileData && currentProfileData.s3Key) || ''; } catch (_) {}
            if (!key) return;
            if (st.key === key) return;
            st.key = key;
            st.cats = null;
            if (/gen_pop/i.test(key)) return;
            fetch('/api/profile-iq/category-reach/' + key.split('/').map(encodeURIComponent).join('/'), { credentials: 'same-origin' })
                .then(function(r) { return r.ok ? r.json() : null; })
                .then(function(d) {
                    if (st.key !== key) return;
                    st.cats = (d && d.success && d.categories) ? d.categories : {};
                    window._piqCatReachPaint();
                })
                .catch(function() {});
        };
        window._piqCatReachPaint = function() {
            var tab = document.getElementById('tab-behavioral');
            if (!tab) return;
            tab.querySelectorAll('.behavioral-card[data-category]').forEach(function(card) {
                var cat = card.getAttribute('data-category') || '';
                var eye = card.querySelector('.piq-beh-eye');
                var line = card.querySelector('.piq-cat-reach');
                var r = _piqCatReachFor(cat);
                var html = r ? _piqCatReachHtml(r) : '';
                if (!html || !eye) { if (line) line.remove(); return; }
                if (!line) {
                    line = document.createElement('div');
                    line.className = 'piq-cat-reach';
                }
                if (eye.nextSibling !== line) eye.parentNode.insertBefore(line, eye.nextSibling);
                if (line.getAttribute('data-sig') !== html) {
                    line.setAttribute('data-sig', html);
                    line.innerHTML = html;
                    line.setAttribute('title', Number(r.reach).toFixed(1) + '% of this audience engage with at least one brand in this category.' +
                        (r.genpop != null ? ' US average ' + Number(r.genpop).toFixed(1) + '%.' : '') +
                        (r.index != null ? ' Index ' + Math.round(Number(r.index)) + (r.times ? ', ' + r.times : '') + '.' : ''));
                }
            });
            var menu = document.getElementById('piqBehJumpMenu');
            if (!menu) return;
            var st = window._piqCatReach;
            var opts = Array.prototype.slice.call(menu.querySelectorAll('.piq-jump-opt[data-cat]'));
            var any = false;
            var rows = opts.map(function(o, i) {
                if (!o.hasAttribute('data-label')) {
                    o.setAttribute('data-label', o.textContent);
                    o.setAttribute('data-ord', String(i));
                }
                var r = _piqCatReachFor(o.getAttribute('data-cat'));
                var fig = o.querySelector('.piq-jump-fig');
                var txt = r ? (Number(r.reach).toFixed(1) + '%' + (r.index != null ? '  /  index ' + Math.round(Number(r.index)) : '')) : '';
                if (!txt) { if (fig) fig.remove(); }
                else {
                    any = true;
                    if (!fig) { fig = document.createElement('span'); fig.className = 'piq-jump-fig'; o.appendChild(fig); }
                    if (fig.textContent !== txt) fig.textContent = txt;
                }
                return { el: o, ord: Number(o.getAttribute('data-ord')) || 0, reach: r ? r.reach : null, index: r ? r.index : null };
            });
            var bar = menu.querySelector('.piq-jump-sort');
            if (!any) { if (bar) bar.remove(); menu.classList.remove('has-fig'); return; }
            if (!menu.classList.contains('has-fig')) menu.classList.add('has-fig');
            if (!bar) {
                bar = document.createElement('div');
                bar.className = 'piq-jump-sort';
                bar.innerHTML = '<span>Sort</span>' +
                    '<button type="button" data-sort="default">Default</button>' +
                    '<button type="button" data-sort="pct">% engage</button>' +
                    '<button type="button" data-sort="index">Index</button>';
                bar.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    var b = e.target.closest('[data-sort]');
                    if (!b) return;
                    window._piqCatReach.sort = b.getAttribute('data-sort') || 'default';
                    window._piqCatReachPaint();
                });
                menu.insertBefore(bar, menu.firstChild);
            }
            bar.querySelectorAll('[data-sort]').forEach(function(b) {
                b.classList.toggle('is-on', b.getAttribute('data-sort') === (st.sort || 'default'));
            });
            var want = _piqCatReachOrder(rows, st.sort || 'default');
            var same = want.every(function(w, i) { return w.el === opts[i]; });
            if (!same) want.forEach(function(w) { menu.appendChild(w.el); });
        };

        const BEH_INDEX_FILTER_LS_KEY = 'cw.behIndexFilter.v1';
""")


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    before = len(src.encode("utf-8"))
    for desc, old, new in EDITS:
        n = src.count(old)
        if n != 1:
            raise RuntimeError(f"[{desc}] anchor found {n}x (want exactly 1)")
        src = src.replace(old, new)
    INDEX.write_text(src, encoding="utf-8")
    after = len(src.encode("utf-8"))
    print(f"spliced {len(EDITS)} edits: {before:,} -> {after:,} bytes (+{after - before:,})")


if __name__ == "__main__":
    main()
