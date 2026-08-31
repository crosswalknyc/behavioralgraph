#!/usr/bin/env python3
"""Splice per-card search widgets into every Trends IQ ranker.

Mirrors the Profile IQ per-behavioral-category ranker search UX (the
inline `<input placeholder="Search...">` next to the sort/slice selects
in every behavioral card - see `searchInCard` in the same file).

Every Trends IQ card that renders a ranked list of rows (Movers,
Headlines, Searches, People, Music, Podcasts, Books, Comics,
Streaming, Films, FAST, Gaming, Watchlist) picks up a small search
input in its header. Typing filters the visible rows in that card;
match is case-insensitive substring against the row's rendered text
(title, subtitle, publisher, creator, artist, badges), debounced
~120ms. Split panels (Meta Quest Free/Paid, FAST Film/TV, Streaming
Film/TV, Steam Most Played/Top Sellers) render each column as its
own card, so a single search per column falls out naturally without
extra wiring. Sub-pill views (Streaming/FAST/Gaming platform pills,
Headlines sub-tabs) render each sub-panel with its own cards +
search, so switching pills exposes a fresh search tied to that panel.
CSV export walks the underlying data, so filter never changes what
ships. Xbox Game Pass (single-list panel with no wrapping card) is
covered by a second install pass that anchors the input at the top of
the bare `.tiq-social-panel`.

Follow `.cursor/rules/index-html-safety.mdc`. Idempotent via anchor
uniqueness; safe to re-run only against a clean pre-splice state.
"""
from pathlib import Path

INDEX = Path("bg-webapp/templates/index.html")
BACKUP = Path("/tmp/index.pre_trends_iq_ranker_search.html")


def splice(src: str, old: str, new: str, desc: str) -> str:
    n = src.count(old)
    if n == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if n > 1:
        raise RuntimeError(f"[{desc}] anchor found {n}x - not unique")
    return src.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Splice 1: helper JS block, inserted between `_tiqApplyTooltips` and
# `window.setTrendsIQTab = ...`.
# ---------------------------------------------------------------------------

SPLICE1_OLD = """                if (tip) {
                    el.classList.add('has-tooltip');
                    el.setAttribute('data-tooltip', tip);
                    // Remove native title so we don't get a doubled-up
                    // browser tooltip on hover.
                    el.removeAttribute('title');
                }
            });
        }

        window.setTrendsIQTab = function(key) {
"""

SPLICE1_NEW = """                if (tip) {
                    el.classList.add('has-tooltip');
                    el.setAttribute('data-tooltip', tip);
                    // Remove native title so we don't get a doubled-up
                    // browser tooltip on hover.
                    el.removeAttribute('title');
                }
            });
        }

        // ============================================================
        // Per-card search (Trends IQ ranker search).
        //
        // Every card that renders a ranked list of rows gets a small
        // search input in its header. Typing filters the visible rows
        // in that card. Match is case-insensitive substring against the
        // row's rendered text (title, subtitle, publisher, creator,
        // artist, badges), debounced ~120ms. Split panels (Meta Quest
        // Free/Paid, FAST Film/TV, Streaming Film/TV, Steam Most
        // Played/Top Sellers) render each column as its own card so
        // one search per column falls out naturally. Sub-pill views
        // (Streaming / FAST / Gaming platform pills, Headlines sub-
        // tabs) render each sub-panel with its own card + search, so
        // switching sub-pills exposes a fresh search input tied to
        // that panel's rows. CSV export walks the underlying data, so
        // the filter never changes what ships.
        // ============================================================
        function _tiqCardSearchDebounce(fn, ms) {
            var t = 0;
            return function() {
                var args = arguments, self = this;
                clearTimeout(t);
                t = setTimeout(function() { fn.apply(self, args); }, ms);
            };
        }

        // Every kind of row a Trends IQ card renders. Order does not
        // matter - the selector is a union. Container-scoped
        // (`.tiq-headline-list > .tiq-row`, etc.) so a card only
        // filters its own rows, never a sibling card's.
        var _TIQ_CARD_ROW_SELECTOR = [
            '.tiq-headline-list > .tiq-row',
            '.tiq-rows > .tiq-row',
            '.tiq-search-scroll > .tiq-row',
            '.tiq-mover-scroll > .tiq-row',
            '.tiq-people-grid > .tiq-person-card',
            '.tiq-product-grid > .tiq-product-card',
            '.tiq-watch-row',
            '.tiq-sheet > tbody > tr.tiq-sheet-row'
        ].join(', ');

        function _tiqCardApplySearch(card) {
            var input = card.querySelector(':scope > .tiq-card-search-wrap .tiq-card-search');
            var counter = card.querySelector(':scope > .tiq-card-search-wrap .tiq-card-search-count');
            if (!input) return;
            var q = String(input.value || '').trim().toLowerCase();
            var rows = card.querySelectorAll(_TIQ_CARD_ROW_SELECTOR);
            var total = rows.length;
            var shown = 0;
            for (var i = 0; i < rows.length; i++) {
                var row = rows[i];
                if (!q) {
                    row.style.display = '';
                    shown++;
                    continue;
                }
                var text = (row.textContent || '').toLowerCase();
                if (text.indexOf(q) >= 0) {
                    row.style.display = '';
                    shown++;
                } else {
                    row.style.display = 'none';
                }
            }
            if (counter) {
                counter.textContent = q ? ('Showing ' + shown + ' of ' + total) : '';
            }
        }

        function _tiqInstallCardSearches(root) {
            if (!root) return;
            var cards = root.querySelectorAll('.tiq-card');
            for (var c = 0; c < cards.length; c++) {
                var card = cards[c];
                // Idempotent: skip cards we already wired.
                if (card.querySelector(':scope > .tiq-card-search-wrap')) continue;
                // Skip cards with no rows to search (empty state,
                // placeholder cards, "Loading" cards).
                var rows = card.querySelectorAll(_TIQ_CARD_ROW_SELECTOR);
                if (!rows.length) continue;
                // Anchor: direct <h4> child or the FAST-style sheet-
                // cardhead wrapper that carries the h4. Skip cards
                // without a header - we have no good spot to put the
                // input.
                var anchor = card.querySelector(':scope > h4')
                          || card.querySelector(':scope > .tiq-sheet-cardhead');
                if (!anchor) continue;
                // If a `.tiq-card-sub` sits directly after the header,
                // insert below it so the subtitle keeps hugging the
                // heading.
                var afterEl = anchor;
                var next = anchor.nextElementSibling;
                if (next && next.classList && next.classList.contains('tiq-card-sub')) {
                    afterEl = next;
                }
                var wrap = document.createElement('div');
                wrap.className = 'tiq-card-search-wrap';
                wrap.style.cssText = 'display:flex;align-items:center;gap:8px;margin:6px 0 10px;';
                var input = document.createElement('input');
                input.type = 'search';
                input.className = 'tiq-card-search';
                input.placeholder = 'Search...';
                input.setAttribute('autocomplete', 'off');
                input.setAttribute('spellcheck', 'false');
                input.style.cssText =
                    'flex:1;min-width:0;background:#111827;color:#fff;' +
                    'border:1px solid #374151;border-radius:6px;' +
                    'padding:6px 10px;font-size:12px;line-height:1.2;';
                var counter = document.createElement('span');
                counter.className = 'tiq-card-search-count';
                counter.style.cssText = 'font-size:11px;opacity:0.7;white-space:nowrap;';
                wrap.appendChild(input);
                wrap.appendChild(counter);
                if (afterEl.nextSibling) {
                    card.insertBefore(wrap, afterEl.nextSibling);
                } else {
                    card.appendChild(wrap);
                }
                // Closure captures the card the input belongs to.
                (function(cardEl) {
                    var handler = _tiqCardSearchDebounce(function() {
                        _tiqCardApplySearch(cardEl);
                    }, 120);
                    input.addEventListener('input', handler);
                })(card);
            }
            // Second pass: bare-panel rankers that render a row list
            // directly under a `.tiq-social-panel` / `.tiq-retailer-panel`
            // without a wrapping `.tiq-card`. Today this is Xbox Game
            // Pass under the Gaming tab (single-list panel, no per-
            // column card). Insert the search input at the top of the
            // panel body. Skip any panel that already contains a
            // `.tiq-card` - the first pass handles those.
            var barePanels = root.querySelectorAll(
                '.tiq-social-panel[data-tiq-gaming], ' +
                '.tiq-social-panel[data-tiq-social], ' +
                '.tiq-social-panel[data-tiq-streaming], ' +
                '.tiq-social-panel[data-tiq-fast], ' +
                '.tiq-retailer-panel[data-tiq-retailer]'
            );
            for (var p = 0; p < barePanels.length; p++) {
                var panel = barePanels[p];
                if (panel.querySelector(':scope > .tiq-card-search-wrap')) continue;
                if (panel.querySelector('.tiq-card')) continue;
                var panelRows = panel.querySelectorAll(_TIQ_CARD_ROW_SELECTOR);
                if (!panelRows.length) continue;
                var pwrap = document.createElement('div');
                pwrap.className = 'tiq-card-search-wrap';
                pwrap.style.cssText = 'display:flex;align-items:center;gap:8px;margin:6px 0 10px;';
                var pinput = document.createElement('input');
                pinput.type = 'search';
                pinput.className = 'tiq-card-search';
                pinput.placeholder = 'Search...';
                pinput.setAttribute('autocomplete', 'off');
                pinput.setAttribute('spellcheck', 'false');
                pinput.style.cssText =
                    'flex:1;min-width:0;background:#111827;color:#fff;' +
                    'border:1px solid #374151;border-radius:6px;' +
                    'padding:6px 10px;font-size:12px;line-height:1.2;';
                var pcounter = document.createElement('span');
                pcounter.className = 'tiq-card-search-count';
                pcounter.style.cssText = 'font-size:11px;opacity:0.7;white-space:nowrap;';
                pwrap.appendChild(pinput);
                pwrap.appendChild(pcounter);
                if (panel.firstChild) {
                    panel.insertBefore(pwrap, panel.firstChild);
                } else {
                    panel.appendChild(pwrap);
                }
                (function(panelEl) {
                    var handler = _tiqCardSearchDebounce(function() {
                        _tiqCardApplySearch(panelEl);
                    }, 120);
                    pinput.addEventListener('input', handler);
                })(panel);
            }
        }

        window.setTrendsIQTab = function(key) {
"""


# ---------------------------------------------------------------------------
# Splice 2: wire the main render pass. `setTrendsIQTab(...)` fires at the
# tail of the top-level render fn; install search widgets one line before.
# Anchor is the `activeTab === 'social'` guard so the match is unique.
# ---------------------------------------------------------------------------

SPLICE2_OLD = """            if (window.__trendsIQ.activeTab === 'social') {
                window.__trendsIQ.activeTab = 'searches';
            }
            setTrendsIQTab(window.__trendsIQ.activeTab || 'searches');
"""

SPLICE2_NEW = """            if (window.__trendsIQ.activeTab === 'social') {
                window.__trendsIQ.activeTab = 'searches';
            }
            _tiqInstallCardSearches(document.getElementById('trendsIQPanels'));
            setTrendsIQTab(window.__trendsIQ.activeTab || 'searches');
"""


# ---------------------------------------------------------------------------
# Splice 3: wire `_tiqRepaintActivePanel` (called on watchlist toggle,
# which rebuilds every panel).
# ---------------------------------------------------------------------------

SPLICE3_OLD = """            // Re-run lens filter after the star-toggle repaint so rows
            // stay hidden/visible per the active lens.
            if (typeof applyTrendsIQLens === 'function') applyTrendsIQLens();
            setTrendsIQTab(window.__trendsIQ.activeTab || 'searches');
        }
"""

SPLICE3_NEW = """            // Re-run lens filter after the star-toggle repaint so rows
            // stay hidden/visible per the active lens.
            if (typeof applyTrendsIQLens === 'function') applyTrendsIQLens();
            _tiqInstallCardSearches(document.getElementById('trendsIQPanels'));
            setTrendsIQTab(window.__trendsIQ.activeTab || 'searches');
        }
"""


# ---------------------------------------------------------------------------
# Splice 4: wire `setTrendsIQHeadlinesView` (sub-tab flip inside the
# Headlines panel replaces panel.innerHTML with a fresh render).
# ---------------------------------------------------------------------------

SPLICE4_OLD = """            var panel = document.querySelector('#trendsIQPanels .tiq-panel[data-tiq-panel="headlines"]');
            if (panel) {
                panel.innerHTML = renderTIQHeadlines(
                    data.cards.trending_headlines || [],
                    data.cards.articles_by_source || [],
                    data.cards.philanthropy_news  || [],
                    data.cards.business_news      || [],
                    data.cards.wall_street_news   || []);
            }
"""

SPLICE4_NEW = """            var panel = document.querySelector('#trendsIQPanels .tiq-panel[data-tiq-panel="headlines"]');
            if (panel) {
                panel.innerHTML = renderTIQHeadlines(
                    data.cards.trending_headlines || [],
                    data.cards.articles_by_source || [],
                    data.cards.philanthropy_news  || [],
                    data.cards.business_news      || [],
                    data.cards.wall_street_news   || []);
                _tiqInstallCardSearches(panel);
            }
"""


# ---------------------------------------------------------------------------
# Splice 5: wire `setTrendsIQPeopleSort` (sort-select flip repaints the
# people panel in place).
# ---------------------------------------------------------------------------

SPLICE5_OLD = """            var panel = document.querySelector('#trendsIQPanels .tiq-panel[data-tiq-panel="people"]');
            if (panel) panel.innerHTML = renderTIQPeople(rows);
        };
"""

SPLICE5_NEW = """            var panel = document.querySelector('#trendsIQPanels .tiq-panel[data-tiq-panel="people"]');
            if (panel) {
                panel.innerHTML = renderTIQPeople(rows);
                _tiqInstallCardSearches(panel);
            }
        };
"""


# ---------------------------------------------------------------------------
# Splice 6: wire `setTrendsIQFastMode` (Titles <-> Channel Ranker flip
# repaints the whole FAST panel).
# ---------------------------------------------------------------------------

SPLICE6_OLD = """            if (panel && typeof renderTIQFast === 'function') {
                panel.innerHTML = renderTIQFast(fast);
                if (typeof _tiqApplyTooltips === 'function') {
                    _tiqApplyTooltips();
                }
            }
"""

SPLICE6_NEW = """            if (panel && typeof renderTIQFast === 'function') {
                panel.innerHTML = renderTIQFast(fast);
                if (typeof _tiqApplyTooltips === 'function') {
                    _tiqApplyTooltips();
                }
                _tiqInstallCardSearches(panel);
            }
"""


def main() -> None:
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[splice] backup written to {BACKUP} ({len(src):,} bytes)")

    src = splice(src, SPLICE1_OLD, SPLICE1_NEW, "add helper JS block")
    src = splice(src, SPLICE2_OLD, SPLICE2_NEW, "wire main render")
    src = splice(src, SPLICE3_OLD, SPLICE3_NEW, "wire _tiqRepaintActivePanel")
    src = splice(src, SPLICE4_OLD, SPLICE4_NEW, "wire setTrendsIQHeadlinesView")
    src = splice(src, SPLICE5_OLD, SPLICE5_NEW, "wire setTrendsIQPeopleSort")
    src = splice(src, SPLICE6_OLD, SPLICE6_NEW, "wire setTrendsIQFastMode")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote {INDEX} ({len(src):,} bytes)")


if __name__ == "__main__":
    main()
