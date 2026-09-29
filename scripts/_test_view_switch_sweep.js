// Harness: run the old and new view-switch sweeps against a fake DOM and
// the product-switch sequences from the 2026-09-29 stacking report.
// Driven by scripts/_test_view_switch_sweep.py, which injects both blocks.

function makeEnv(opts) {
    opts = opts || {};
    var OWNED_IDS = ['dashboardView', 'subscriberIQView', 'sfConversionView',
        'trendsIQView', 'ticketSalesIQView', 'ticketSalesTrackerView',
        'hedgeFundIQView', 'sentimentIQView', 'ecommerceIQView',
        'journeyIQView', 'shareOfTimeIQView', 'flywheelConversionView',
        'talentFitIQView', 'blueIQView', 'microdramasIQView', 'intentIQView',
        'roasIQView', 'llmoIQView', 'talentSearchIQView',
        'talentTheaterIQView', 'customAnalysisView', 'formView',
        'helmIQView'];
    // Mirrors the real document: these two sit outside .container, so the
    // old DOM-derived sweep is structurally blind to them.
    var OUTSIDE = { sentimentIQView: 1, talentSearchIQView: 1,
                    talentTheaterIQView: 1, helmIQView: 1 };

    var els = {};
    OWNED_IDS.forEach(function (id) {
        els[id] = { id: id, tagName: 'DIV', style: { display: 'none' },
                    classList: { contains: function () { return false; } } };
    });
    var chrome = { visible: false };

    var container = { children: [] };
    OWNED_IDS.forEach(function (id) {
        if (!OUTSIDE[id]) container.children.push(els[id]);
    });

    var document = {
        readyState: 'complete',
        getElementById: function (id) { return els[id] || null; },
        querySelector: function (sel) {
            return sel === '.container' ? container : null;
        },
        addEventListener: function () {}
    };
    function getComputedStyle(el) {
        return { display: el.style.display === 'none' ? 'none' : 'flex' };
    }

    var window = { document: document };
    // Stand-in for _piqV4PinChrome: the real one tears the body-pinned
    // hero + tab bar down when dashboardView reads hidden, and re-pins
    // it when visible.
    window._piqV4PinChrome = function () {
        chrome.visible = els.dashboardView.style.display !== 'none';
    };

    function show(id) { els[id].style.display = 'flex'; }
    function hide(id) { if (els[id]) els[id].style.display = 'none'; }

    // showProfileIQ's real hide list, verbatim in spirit: it targets
    // rankerIQView (an id that no longer exists) and omits Trends, Blue,
    // Microdramas, Intent, Share of Time and Flywheel.
    window.showProfileIQ = function () {
        show('dashboardView');
        ['subscriberIQView', 'sfConversionView', 'rankerIQView',
         'ticketSalesIQView', 'ticketSalesTrackerView', 'hedgeFundIQView',
         'talentSearchIQView', 'talentTheaterIQView', 'formView',
         'customAnalysisView', 'roasIQView', 'ecommerceIQView',
         'talentFitIQView', 'journeyIQView', 'llmoIQView',
         'helmIQView'].forEach(hide);
        window._piqV4PinChrome();
    };
    // These hide nothing of their own (hides=0 in the real file).
    // opts.throwAfterShow reproduces the real shape of these functions:
    // they reveal their own view first, then keep working (Rankers
    // touches window.__trendsIQ and calls loadTrendsIQFilterOptions), so
    // a throw lands after the view is already up.
    function maybeThrow(fn) {
        if (opts.throwAfterShow === fn) {
            throw new TypeError(
                "Cannot set property 'product' of undefined");
        }
    }
    window.showBlueIQ = function () {
        show('blueIQView'); maybeThrow('showBlueIQ');
    };
    window.showTrendsIQ = function () { show('trendsIQView'); };
    window.showCultureRankerIQ = function () {
        show('trendsIQView'); maybeThrow('showCultureRankerIQ');
    };
    window.showSentimentIQ = function () { show('sentimentIQView'); };
    window.showSubscriberIQ = function () {
        show('subscriberIQView');
        ['dashboardView', 'sfConversionView'].forEach(hide);
    };

    return { window: window, document: document,
             getComputedStyle: getComputedStyle, els: els, chrome: chrome,
             owned: OWNED_IDS, show: show };
}

function visibleOwned(env) {
    return env.owned.filter(function (id) {
        return env.els[id].style.display !== 'none';
    });
}

function runSequence(env, steps) {
    steps.forEach(function (fn) {
        // A real caller lets the exception propagate; what matters here
        // is whether the page was left coherent.
        try { env.window[fn](); } catch (e) { /* expected in throw cases */ }
    });
    var vis = visibleOwned(env);
    return {
        visible: vis,
        chrome: env.chrome.visible,
        // The invariant: one view standing, and the Profile IQ chrome up
        // only when Profile IQ is the one standing.
        ok: vis.length === 1 &&
            env.chrome.visible === (vis[0] === 'dashboardView')
    };
}

var SEQUENCES = [
    { name: 'Profile IQ -> Rankers',
      steps: ['showProfileIQ', 'showCultureRankerIQ'] },
    { name: 'Profile IQ -> Blue IQ',
      steps: ['showProfileIQ', 'showBlueIQ'] },
    { name: 'Trends IQ -> Rankers (shared trendsIQView)',
      steps: ['showTrendsIQ', 'showCultureRankerIQ'] },
    { name: 'Blue IQ -> Profile IQ -> Blue IQ',
      steps: ['showBlueIQ', 'showProfileIQ', 'showBlueIQ'] },
    { name: 'Rankers -> Profile IQ -> Rankers',
      steps: ['showCultureRankerIQ', 'showProfileIQ', 'showCultureRankerIQ'] },
    { name: 'Profile IQ -> Sentiment IQ (view outside .container)',
      steps: ['showProfileIQ', 'showSentimentIQ'] },
    { name: 'walk four products',
      steps: ['showProfileIQ', 'showBlueIQ', 'showCultureRankerIQ',
              'showSubscriberIQ'] },
    { name: 'restore Rankers with Profile IQ already up',
      steps: ['showProfileIQ', 'showCultureRankerIQ', 'showCultureRankerIQ'] },
    { name: 'back to Profile IQ last',
      steps: ['showBlueIQ', 'showCultureRankerIQ', 'showProfileIQ'] },
    // The reported 911: the product renders, then its own code throws,
    // and Profile IQ is left standing underneath.
    { name: 'Profile IQ -> Rankers, Rankers throws after showing',
      steps: ['showProfileIQ', 'showCultureRankerIQ'],
      opts: { throwAfterShow: 'showCultureRankerIQ' } },
    { name: 'Profile IQ -> Blue IQ, Blue IQ throws after showing',
      steps: ['showProfileIQ', 'showBlueIQ'],
      opts: { throwAfterShow: 'showBlueIQ' } }
];

function evaluate(label, blockSrc) {
    var lines = [];
    lines.push('=== ' + label + ' ===');
    var pass = 0, fail = 0;
    SEQUENCES.forEach(function (seq) {
        var env = makeEnv(seq.opts);
        // The sweep block is an IIFE referencing window/document/
        // getComputedStyle; hand it the fakes as parameters.
        var install = new Function('window', 'document', 'getComputedStyle',
                                   blockSrc);
        install(env.window, env.document, env.getComputedStyle);
        var r = runSequence(env, seq.steps);
        if (r.ok) { pass++; } else { fail++; }
        lines.push((r.ok ? '  PASS  ' : '  FAIL  ') + seq.name +
                   '\n          visible=[' + r.visible.join(', ') + ']' +
                   ' profileChrome=' + r.chrome);
    });
    lines.push('  ' + pass + ' passed, ' + fail + ' failed');
    return lines.join('\n');
}
