#!/usr/bin/env python3
"""Follow-up splice: unify per-column search inputs on split platform
panels (FAST Titles Film/TV/All, Streaming Film/TV, Meta Quest Free/
Paid, Steam Most Played/Top Sellers) into a single panel-level search.

The spec was explicit: SPLIT columns should share ONE search input,
not carry one per column. The initial per-card implementation gave
each column its own input, which is more UI than value.

Design:
- Add a Pass A at the top of `_tiqInstallCardSearches` that scans for
  `.tiq-social-panel[data-tiq-fast], .tiq-social-panel[data-tiq-
  streaming], .tiq-social-panel[data-tiq-gaming]` containing 2 or more
  `.tiq-card` descendants. For those, install ONE search input at the
  panel level; a single input filters rows across every nested card.
  Mark inner cards as `data-tiq-search-consolidated` so the per-card
  pass skips them.
- Pass B (per-card, existing) now skips cards that Pass A owned.
  Single-card panels (FAST Channel Ranker sheet, Streaming with only
  Film or only TV) still fall through here.
- Pass C (bare panels: Xbox) unchanged, guards existing `.tiq-card-
  search-wrap` so it doesn't collide with Pass A.

This is a byte-level splice on `bg-webapp/templates/index.html` per
`.cursor/rules/index-html-safety.mdc` (StrReplace truncates files
above ~8 MB).
"""
from pathlib import Path
import subprocess
import sys

INDEX = Path("bg-webapp/templates/index.html")
BACKUP = Path("/tmp/index.pre_tiq_unify_split.html")
VALIDATOR = Path("bg-webapp/scripts/validate_index_html.py")


def splice(src: str, old: str, new: str, desc: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x (need unique match)")
    return src.replace(old, new)


OLD_COMMENT = """        // Every card that renders a ranked list of rows gets a small
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
        // ============================================================"""

NEW_COMMENT = """        // Every card that renders a ranked list of rows gets a small
        // search input in its header. Typing filters the visible rows
        // in that card. Match is case-insensitive substring against the
        // row's rendered text (title, subtitle, publisher, creator,
        // artist, badges), debounced ~120ms.
        //
        // Split platform panels (FAST Titles Film/TV/All, Streaming
        // Film/TV, Meta Quest Free/Paid, Steam Most Played/Top Sellers)
        // share ONE search input at the panel level that filters rows
        // across every nested column card - the per-card install skips
        // those cards so the layout carries a single search, not one
        // per column. Single-card platform panels (FAST Channel Ranker
        // sheet, Streaming platforms with only Film or only TV) still
        // fall through to per-card install so the one card carries its
        // own search.
        //
        // Sub-pill views (Streaming / FAST / Gaming platform pills,
        // Headlines sub-tabs) render each sub-panel fresh, so switching
        // sub-pills exposes a fresh search input tied to that panel's
        // rows. CSV export walks the underlying data, so the filter
        // never changes what ships.
        // ============================================================"""


OLD_INSTALL = """        function _tiqInstallCardSearches(root) {
            if (!root) return;
            var cards = root.querySelectorAll('.tiq-card');
            for (var c = 0; c < cards.length; c++) {
                var card = cards[c];
                // Idempotent: skip cards we already wired.
                if (card.querySelector(':scope > .tiq-card-search-wrap')) continue;
                // Skip cards with no rows to search (empty state,
                // placeholder cards, "Loading" cards).
                var rows = card.querySelectorAll(_TIQ_CARD_ROW_SELECTOR);
                if (!rows.length) continue;"""

NEW_INSTALL = """        // Shared widget builder - keeps Pass A / Pass B / Pass C in
        // sync on styling, placeholder, debounce, and closure shape.
        function _tiqBuildCardSearchWidget(targetEl) {
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
            var handler = _tiqCardSearchDebounce(function() {
                _tiqCardApplySearch(targetEl);
            }, 120);
            input.addEventListener('input', handler);
            return wrap;
        }

        function _tiqInstallCardSearches(root) {
            if (!root) return;

            // Pass A: unify search on multi-card platform panels. FAST
            // Titles (Film + TV + All), Streaming (Film + TV), Meta
            // Quest (Free + Paid), and Steam (Most Played + Top
            // Sellers) all render each column as its own `.tiq-card`
            // inside a `.tiq-social-panel[data-tiq-*]` wrapper. One
            // panel-level search reads more cleanly than one per
            // column, so install a single widget above the grid and
            // mark inner cards as consolidated so Pass B skips them.
            var splitPanels = root.querySelectorAll(
                '.tiq-social-panel[data-tiq-fast], ' +
                '.tiq-social-panel[data-tiq-streaming], ' +
                '.tiq-social-panel[data-tiq-gaming]'
            );
            for (var sp = 0; sp < splitPanels.length; sp++) {
                var splitPanel = splitPanels[sp];
                if (splitPanel.querySelector(':scope > .tiq-card-search-wrap')) continue;
                var innerCards = splitPanel.querySelectorAll('.tiq-card');
                // Only unify when there are 2+ inner cards. Single-
                // card panels (FAST Channel Ranker sheet, Streaming
                // with only Film or only TV) fall through to Pass B.
                if (innerCards.length < 2) continue;
                var totalRows = splitPanel.querySelectorAll(_TIQ_CARD_ROW_SELECTOR);
                if (!totalRows.length) continue;
                for (var ic = 0; ic < innerCards.length; ic++) {
                    innerCards[ic].setAttribute('data-tiq-search-consolidated', '1');
                }
                var splitWrap = _tiqBuildCardSearchWidget(splitPanel);
                if (splitPanel.firstChild) {
                    splitPanel.insertBefore(splitWrap, splitPanel.firstChild);
                } else {
                    splitPanel.appendChild(splitWrap);
                }
            }

            // Pass B: per-card search. Every `.tiq-card` that owns
            // its own set of rows and carries a header gets a search
            // input. Skips cards consolidated by Pass A.
            var cards = root.querySelectorAll('.tiq-card');
            for (var c = 0; c < cards.length; c++) {
                var card = cards[c];
                if (card.getAttribute('data-tiq-search-consolidated') === '1') continue;
                // Idempotent: skip cards we already wired.
                if (card.querySelector(':scope > .tiq-card-search-wrap')) continue;
                // Skip cards with no rows to search (empty state,
                // placeholder cards, "Loading" cards).
                var rows = card.querySelectorAll(_TIQ_CARD_ROW_SELECTOR);
                if (!rows.length) continue;"""


OLD_PASSB_TAIL = """                (function(cardEl) {
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
        }"""


NEW_PASSB_TAIL = """                (function(cardEl) {
                    var handler = _tiqCardSearchDebounce(function() {
                        _tiqCardApplySearch(cardEl);
                    }, 120);
                    input.addEventListener('input', handler);
                })(card);
            }

            // Pass C: bare-panel rankers that render a row list
            // directly under a `.tiq-social-panel` / `.tiq-retailer-panel`
            // without a wrapping `.tiq-card`. Today this is Xbox Game
            // Pass under the Gaming tab (single-list panel, no per-
            // column card). Insert the search input at the top of the
            // panel body. Skip any panel that Pass A or Pass B already
            // wired (idempotent guard) or that contains a `.tiq-card`
            // (Pass B owns those).
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
                var pwrap = _tiqBuildCardSearchWidget(panel);
                if (panel.firstChild) {
                    panel.insertBefore(pwrap, panel.firstChild);
                } else {
                    panel.appendChild(pwrap);
                }
            }
        }"""


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[splice] backed up pre-splice bytes to {BACKUP}")

    src = splice(src, OLD_COMMENT, NEW_COMMENT, "top-of-block comment refresh")
    src = splice(src, OLD_INSTALL, NEW_INSTALL, "install-pass A unification")
    src = splice(src, OLD_PASSB_TAIL, NEW_PASSB_TAIL, "install-pass C dedupe via shared widget builder")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote {INDEX} ({len(src):,} bytes)")

    result = subprocess.run(
        ["python3", str(VALIDATOR)],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print("[splice] validator FAILED - restoring backup")
        INDEX.write_text(BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
        return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
