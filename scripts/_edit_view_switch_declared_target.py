#!/usr/bin/env python3
"""Replace the visibility-diff view sweep with a declared-target sweep.

templates/index.html is edited by read/replace/write, never by an editor
tool: the tail has been truncated before.
"""
import io
import os
import re

PATH = 'templates/index.html'

ANCHOR = ("<script>\n"
          "    // Product switching leaves one view standing (2026-09-29). Each")

NEW = r'''<script>
    // Product switching leaves exactly one view standing (2026-09-29).
    //
    // An earlier pass inferred intent by diffing visibility: it hid
    // whatever was not newly revealed and bailed when nothing new
    // appeared. That bail fired in the common case, because
    // showProfileIQ hides a stale hardcoded list. It still targets
    // rankerIQView, an id that no longer exists in this document, and
    // it never hides Trends, Blue, Microdramas, Intent, Share of Time
    // or Flywheel, so those stay mounted underneath Profile IQ.
    // Clicking back to one of them revealed nothing new, so the sweep
    // returned early, and the early return also skipped the Profile IQ
    // chrome teardown. That chrome is the only part that matters
    // visually: it is pinned to document.body at z-index 2147481000,
    // outside .container, so no hide loop over container children can
    // reach it. Rankers and Trends also share trendsIQView, so
    // switching between those two never revealed anything new either.
    //
    // Each product declares the one view it owns instead of the sweep
    // guessing. After the call, if that view is up, every other owned
    // view comes down and the Profile IQ chrome is always settled.
    (function () {
        var TARGET = {
            showProfileIQ: 'dashboardView',
            showDashboardView: 'dashboardView',
            showSubscriberIQ: 'subscriberIQView',
            showSFConversion: 'sfConversionView',
            showCultureRankerIQ: 'trendsIQView',
            showTrendsIQ: 'trendsIQView',
            showTicketSalesIQ: 'ticketSalesIQView',
            showTicketSalesTracker: 'ticketSalesTrackerView',
            showHedgeFundIQ: 'hedgeFundIQView',
            showSentimentIQ: 'sentimentIQView',
            showEcommerceIQ: 'ecommerceIQView',
            showJourneyIQ: 'journeyIQView',
            showShareOfTimeIQ: 'shareOfTimeIQView',
            showFlywheelConversionDashboard: 'flywheelConversionView',
            showTalentFitIQ: 'talentFitIQView',
            showBlueIQ: 'blueIQView',
            showMicrodramasIQ: 'microdramasIQView',
            showIntentIQ: 'intentIQView',
            showRoasIQ: 'roasIQView',
            showLLMOIQ: 'llmoIQView',
            showTalentSearchIQ: 'talentSearchIQView',
            showTalentTheaterIQ: 'talentTheaterIQView',
            showCustomAnalysis: 'customAnalysisView',
            showFormView: 'formView'
        };
        // helmIQView is opened by an inline handler in the product menu
        // rather than a show function, so there is nothing to wrap for
        // it. It still has to come down when another product opens.
        var EXTRA = ['helmIQView'];
        // Only views a product owns are swept. Views that live inside a
        // product are deliberately absent, because hiding them would
        // break the product that owns them: comparisonReportView sits
        // in Profile IQ's compare tab, and the daily/historic metric
        // tables and charts sit inside their dashboards. So are the
        // non-product panels (deck editor, ribbon, chat), and
        // flywheelIQView and impactIQView, which no code ever shows.
        var OWNED = (function () {
            var seen = {}, out = [], k, i;
            for (k in TARGET) {
                if (!TARGET.hasOwnProperty(k)) continue;
                if (!seen[TARGET[k]]) { seen[TARGET[k]] = 1; out.push(TARGET[k]); }
            }
            for (i = 0; i < EXTRA.length; i++) {
                if (!seen[EXTRA[i]]) { seen[EXTRA[i]] = 1; out.push(EXTRA[i]); }
            }
            return out;
        })();

        function isVisible(el) {
            if (!el) return false;
            if (el.style && el.style.display === 'none') return false;
            try {
                return getComputedStyle(el).display !== 'none';
            } catch (e) {
                return true;
            }
        }
        // Profile IQ pins its hero and tab bar to document.body, so the
        // chrome outlives the swap and paints over whatever opens next.
        // An inline display cannot stand it down from here; its own pin
        // function reads the view as hidden the moment it is and does
        // the full teardown, so call that. On Profile IQ it re-pins.
        function settleProfileChrome() {
            if (typeof window._piqV4PinChrome !== 'function') return;
            try { window._piqV4PinChrome(); } catch (e) { /* chrome only */ }
        }
        function sweep(keepId) {
            for (var i = 0; i < OWNED.length; i++) {
                if (OWNED[i] === keepId) continue;
                var el = document.getElementById(OWNED[i]);
                if (el && isVisible(el)) el.style.display = 'none';
            }
            settleProfileChrome();
        }
        function wrap() {
            Object.keys(TARGET).forEach(function (name) {
                var original = window[name];
                if (typeof original !== 'function' || original._cwSweep) return;
                var wrapped = function () {
                    var keep = TARGET[name];
                    // try/finally, because these functions reveal their
                    // own view early and then keep working: Rankers
                    // touches window.__trendsIQ and calls
                    // loadTrendsIQFilterOptions after the view is up. A
                    // throw past that point used to skip the sweep
                    // entirely, leaving the new product on screen with
                    // Profile IQ still under it. The sweep has to run
                    // even when the product it swept to is broken.
                    try {
                        return original.apply(this, arguments);
                    } finally {
                        // A show function returns without opening
                        // anything when it bails on its access check.
                        // Sweep only once its own view is actually up,
                        // so a product the seat cannot open never
                        // blanks the page.
                        if (isVisible(document.getElementById(keep))) {
                            sweep(keep);
                        }
                    }
                };
                wrapped._cwSweep = true;
                window[name] = wrapped;
            });
        }
        // Install at parse time rather than on DOMContentLoaded. This is
        // the last script in the document, so every top-level show
        // function already exists. The product restore that runs on
        // load and the menu dispatcher both build handler maps inside
        // DOMContentLoaded callbacks registered earlier than this one,
        // and several of their entries hold the function object itself
        // rather than looking it up when called. Waiting would let them
        // capture the unwrapped originals, which is how a restored
        // product came up stacked on top of Profile IQ.
        wrap();
        // showIntentIQ is assigned inside an earlier DOMContentLoaded
        // callback, so it does not exist yet at parse time. Our listener
        // is registered last and therefore runs after it is defined.
        // Wrapping is idempotent.
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', wrap);
        }
    })();
</script>'''


def main():
    with io.open(PATH, encoding='utf-8') as fh:
        src = fh.read()
    before_lines = src.count('\n')

    assert src.count(ANCHOR) == 1, 'anchor not unique: %d' % src.count(ANCHOR)
    start = src.index(ANCHOR)
    end_tag = '\n</script>'
    end = src.index(end_tag, start) + len(end_tag)
    old = src[start:end]
    assert old.startswith('<script>'), 'bad block start'
    assert old.rstrip().endswith('</script>'), 'bad block end'
    assert '_cwSweep' in old, 'not the sweep block'
    assert 'visibleIds' in old, 'not the diff-based sweep block'

    out = src[:start] + NEW + src[end:]

    # The replacement must be the only change.
    assert out.count('_cwSweep') == 2, 'unexpected _cwSweep count'
    assert 'visibleIds' not in out, 'old diff sweep still present'
    assert out.endswith('</html>\n'), 'tail damaged: %r' % out[-40:]
    assert out.count('<body') == src.count('<body'), 'body count changed'
    # Everything outside the replaced span is byte-identical.
    assert out[:start] == src[:start], 'head changed'
    assert out[start + len(NEW):] == src[end:], 'tail changed'

    with io.open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(out)

    print('block replaced: %d -> %d bytes' % (len(old), len(NEW)))
    print('lines: %d -> %d' % (before_lines, out.count('\n')))
    print('ends with </html>: %s' % out.rstrip().endswith('</html>'))


if __name__ == '__main__':
    main()
