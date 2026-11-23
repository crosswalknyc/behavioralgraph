/*
 * Profile IQ: the worldwide layout for a '<Subject> - Global' profile.
 *
 * Jenna 2026-11-23 (approved layout): "you would have US demographics, then
 * list out demo by country so that you can see them all in each country's
 * nomenclature". A file whose SAMPLE SIZE row carries `| MARKET: GLOBAL`
 * gets three things, and nothing else on the page changes:
 *   1. a worldwide summary at the top (people in the audience, where they
 *      live, worldwide age and gender as written in the file);
 *   2. a country switcher over the demographics card (United States first,
 *      then by share, then Rest of world); each country in its own terms;
 *   3. LOCATION grouped by country, each country's share shown.
 * Countries shown = every country with at least 3% of the audience, plus
 * Rest of world. The rows this reads are the GLOBAL PROFILE CONTRACT
 * (COUNTRY, '<DEMO> | <Country>', worldwide AGE / GENDER, 'City, Country'
 * LOCATION). A US or single-country profile is never touched.
 *
 * Wiring: the page's own functions are wrapped after they run
 * (parseAllData, parseComparisonData, renderLocationTable,
 * _piqV4OnProfile, _piqV4OnTab, _piqV4OnClear). On a global file the US
 * DMA list (parsed.locations) keeps only the US cities, so a world city is
 * never read as a US DMA, and COUNTRY plus the per-country blocks never show
 * up as behavioral categories.
 */
(function () {
    'use strict';

    var G = window.CWGlobal = window.CWGlobal || {};
    if (G._installed) return;
    G._installed = true;

    var THRESHOLD = 3.0;
    var REST = 'Rest of world';
    var CITY_ROWS_SHOWN = 8;
    var DEMOS = ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'OCCUPATION',
        'RELATIONSHIP', 'PARENTAL_STATUS', 'SEXUAL_ORIENTATION'];
    var BLOCK_RX = /^\s*(AGE|GENDER|ETHNICITY|INCOME|EDUCATION|OCCUPATION|RELATIONSHIP|PARENTAL_STATUS|SEXUAL_ORIENTATION)\s*\|\s*(.+?)\s*$/i;
    var MARKET_RX = /MARKET\s*:\s*([^|]+?)(?:\s*\||\s*$)/i;
    var GLOBAL_MARKETS = {
        'GLOBAL': 1, 'WORLDWIDE': 1, 'WORLD WIDE': 1, 'WORLD': 1, 'INTERNATIONAL': 1,
        'ALL MARKETS': 1, 'GLOBAL FOOTPRINT': 1, 'AROUND THE WORLD': 1, 'GLOBALLY': 1,
        'ALL COUNTRIES': 1, 'EVERYWHERE': 1
    };
    // Tabs that show the worldwide summary band.
    var SUMMARY_TABS = { insights: 1, demographics: 1 };

    // Everything a reader sees, in one place (plain English, no dashes).
    var COPY = {
        summaryEyebrow: 'Worldwide audience',
        people: 'People in the audience',
        inCountries: 'In {n} countries',
        where: 'Where they live',
        ageWorld: 'Age, worldwide',
        genderWorld: 'Gender, worldwide',
        demoEyebrow: 'Demographics by country',
        demoEyebrowWorld: 'Demographics, worldwide',
        peopleIn: 'People in {country}',
        peopleRest: 'People in the rest of the world',
        peopleWorld: 'Everyone in the audience',
        switchLabel: 'Show the people in',
        noteCountry: 'Each country is shown in its own terms. Each share is out of the people who live there.',
        noteWorld: 'Each share is out of everyone in the audience, worldwide.',
        largest: '{label} is the largest group, {plain}.',
        locEyebrow: 'Where the audience lives',
        locTitle: 'Cities by country',
        locLede: '{cities} have the most people in this audience.',
        ofAudience: '{pct} of the audience',
        cities: '{n} cities',
        city: '1 city',
        citiesIn: '{n} cities in {m} countries',
        showAll: 'Show all {n} cities',
        showFewer: 'Show fewer',
        find: 'Find a city or country',
        noMatch: 'No city or country matches that search.',
        csvTip: 'Export CSV',
        cityTip: '{city}: {pct} of the audience. That is {plain} of the people in {country}.',
        titles: {
            AGE: 'Age', GENDER: 'Gender', ETHNICITY: 'Ethnicity', INCOME: 'Income',
            EDUCATION: 'Education', OCCUPATION: 'Occupation', RELATIONSHIP: 'Relationship status',
            PARENTAL_STATUS: 'Parental status', SEXUAL_ORIENTATION: 'Sexual orientation'
        },
        usEthnicity: 'Race and ethnicity'
    };
    G.COPY = COPY;
    G.THRESHOLD = THRESHOLD;

    // ---- small helpers -------------------------------------------------------

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function fill(tpl, vals) {
        return String(tpl).replace(/\{(\w+)\}/g, function (_m, k) { return vals[k] == null ? '' : vals[k]; });
    }
    function num(v) {
        if (typeof v === 'number') return isFinite(v) ? v : NaN;
        var n = parseFloat(String(v == null ? '' : v).replace(/,/g, ''));
        return isFinite(n) ? n : NaN;
    }
    function pct1(n) {
        n = Number(n);
        if (!isFinite(n)) return '-';
        return (Math.round(n * 10) / 10).toFixed(1) + '%';
    }
    function pct2(n) {
        n = Number(n);
        if (!isFinite(n)) return '-';
        return n.toFixed(2) + '%';
    }
    function count(n) {
        n = Math.round(Number(n));
        if (!isFinite(n) || n <= 0) return '';
        return n.toLocaleString('en-US');
    }
    function foldKey(s) {
        var x = String(s == null ? '' : s);
        if (x.normalize) x = x.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        return x.toUpperCase()
            .replace(/[\u2018\u2019\u02bc`]/g, "'")
            .replace(/[^A-Z0-9' ]+/g, ' ').replace(/\s+/g, ' ').trim();
    }
    function getValue(row, key) {
        if (!row) return undefined;
        return row[key];
    }
    function bp(row) {
        var v = num(getValue(row, 'Brand Penetration (Row)'));
        if (!isFinite(v) || v === 0) {
            var c = num(getValue(row, 'Category Share'));
            if (isFinite(c) && c > 0) v = c;
        }
        return isFinite(v) ? v : NaN;
    }

    // A plain comparison a 7th grader reads at once: about 1 in 3, about
    // half, about 7 in 100.
    function plainShare(p) {
        p = Number(p);
        if (!isFinite(p) || p <= 0) return 'none';
        if (p >= 95) return 'nearly all';
        if (p < 1) return 'fewer than 1 in 100';
        if (p < 10) return 'about ' + Math.round(p) + ' in 100';
        if (Math.abs(p - 50) <= 2.5) return 'about half';
        var best = null;
        SIMPLE_FRACTIONS.forEach(function (f) {
            var diff = Math.abs(p / 100 - f[0] / f[1]);
            if (!best || diff < best.diff - 1e-9) best = { n: f[0], d: f[1], diff: diff };
        });
        return 'about ' + best.n + ' in ' + best.d;
    }
    // Halves, thirds, quarters, fifths and tenths only: 'about 2 in 9'
    // makes a reader do arithmetic, 'about 1 in 5' does not.
    var SIMPLE_FRACTIONS = [[1, 10], [1, 5], [1, 4], [3, 10], [1, 3], [2, 5], [3, 5], [2, 3],
        [7, 10], [3, 4], [4, 5], [9, 10]];
    G.plainShare = plainShare;

    // ---- countries -----------------------------------------------------------

    var US = 'United States';
    var COUNTRY_ALIASES = {
        'US': US, 'USA': US, 'U S': US, 'U S A': US, 'UNITED STATES': US,
        'UNITED STATES OF AMERICA': US, 'AMERICA': US, 'THE UNITED STATES': US,
        'UK': 'United Kingdom', 'U K': 'United Kingdom', 'GB': 'United Kingdom',
        'GREAT BRITAIN': 'United Kingdom', 'BRITAIN': 'United Kingdom',
        'UNITED KINGDOM': 'United Kingdom', 'THE UNITED KINGDOM': 'United Kingdom',
        'ENGLAND': 'United Kingdom', 'SCOTLAND': 'United Kingdom', 'WALES': 'United Kingdom',
        'NORTHERN IRELAND': 'United Kingdom',
        'UAE': 'United Arab Emirates', 'U A E': 'United Arab Emirates',
        'UNITED ARAB EMIRATES': 'United Arab Emirates',
        'IVORY COAST': 'Ivory Coast', "COTE D'IVOIRE": 'Ivory Coast', 'COTE DIVOIRE': 'Ivory Coast',
        "C TE D'IVOIRE": 'Ivory Coast',
        'CZECHIA': 'Czechia', 'CZECH REPUBLIC': 'Czechia',
        'SOUTH KOREA': 'South Korea', 'KOREA': 'South Korea', 'REPUBLIC OF KOREA': 'South Korea',
        'TRINIDAD AND TOBAGO': 'Trinidad and Tobago', 'TRINIDAD TOBAGO': 'Trinidad and Tobago',
        'HOLLAND': 'Netherlands', 'THE NETHERLANDS': 'Netherlands', 'NETHERLANDS': 'Netherlands',
        'PHILIPPINES': 'Philippines', 'THE PHILIPPINES': 'Philippines',
        'BAHAMAS': 'Bahamas', 'THE BAHAMAS': 'Bahamas',
        'DEUTSCHLAND': 'Germany', 'TURKIYE': 'Turkey', 'TURKEY': 'Turkey',
        'REST OF WORLD': REST, 'REST OF THE WORLD': REST, 'ROW': REST,
        'OTHER COUNTRIES': REST, 'ALL OTHER COUNTRIES': REST, 'OTHER': REST,
        'ELSEWHERE': REST, 'EVERYWHERE ELSE': REST
    };
    var LOWER_WORDS = { 'AND': 1, 'OF': 1, 'THE': 1, 'DA': 1, 'DE': 1, 'DEL': 1 };
    function titleCase(s) {
        return String(s).toLowerCase().split(' ').map(function (w, i) {
            if (!w) return w;
            if (i > 0 && LOWER_WORDS[w.toUpperCase()]) return w;
            return w.charAt(0).toUpperCase() + w.slice(1);
        }).join(' ');
    }
    function canonCountry(raw) {
        var s = String(raw == null ? '' : raw).replace(/\s+/g, ' ').trim();
        if (!s) return '';
        var k = foldKey(s).replace(/\./g, ' ').replace(/\s+/g, ' ').trim();
        if (COUNTRY_ALIASES[k]) return COUNTRY_ALIASES[k];
        if (s === s.toUpperCase() || s === s.toLowerCase()) return titleCase(s);
        return s;
    }
    G.canonCountry = canonCountry;
    var THE = {
        'United States': 1, 'United Kingdom': 1, 'United Arab Emirates': 1, 'Netherlands': 1,
        'Philippines': 1, 'Bahamas': 1, 'Czech Republic': 1, 'Dominican Republic': 1,
        'Gambia': 1, 'Maldives': 1, 'Cayman Islands': 1, 'Central African Republic': 1
    };
    function withThe(country) { return THE[country] ? 'the ' + country : country; }
    G.withThe = withThe;

    var US_STATES = {};
    ('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ ' +
     'NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC').split(' ').forEach(function (c) { US_STATES[c] = 1; });

    function isUsDma(name) {
        try {
            if (typeof window.getDMARank !== 'function') return false;
            if (window.getDMARank(name) != null) return true;
            var canon = (typeof window.matchCanonicalDMA === 'function') ? window.matchCanonicalDMA(name) : null;
            return !!(canon && window.getDMARank(canon) != null);
        } catch (_e) { return false; }
    }

    // The country a LOCATION value names: the text after the last comma
    // ('London, UK'), a US state code ('Portland, OR', 'Chicago IL'), or a
    // bare US DMA. '' when nothing names a country (read as Rest of world).
    function locationCountry(name) {
        var s = String(name == null ? '' : name).replace(/\s+/g, ' ').trim();
        if (!s) return { city: '', country: '' };
        var i = s.lastIndexOf(',');
        if (i > -1) {
            var tail = s.slice(i + 1).trim();
            var head = s.slice(0, i).trim();
            if (/^[A-Za-z]{2}$/.test(tail) && US_STATES[tail.toUpperCase()]) {
                return { city: s, country: US };
            }
            if (tail) {
                var c = canonCountry(tail);
                return { city: head || s, country: c === REST ? '' : c };
            }
        }
        var m = s.match(/\s([A-Za-z]{2})$/);
        if (m && US_STATES[m[1].toUpperCase()] && m[1] === m[1].toUpperCase()) return { city: s, country: US };
        if (isUsDma(s)) return { city: s, country: US };
        return { city: s, country: '' };
    }
    G.locationCountry = locationCountry;

    // ---- the file -> the worldwide model -------------------------------------

    function marketOf(rows) {
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            if (!r) continue;
            if (String(r.Column || '').trim().toUpperCase() !== 'SAMPLE SIZE') continue;
            var m = String(r.Value == null ? '' : r.Value).match(MARKET_RX);
            return m ? m[1].trim() : '';
        }
        return '';
    }
    function isGlobalMarket(market) {
        return !!GLOBAL_MARKETS[foldKey(market)];
    }
    function isGlobalRows(rows) {
        return Array.isArray(rows) && isGlobalMarket(marketOf(rows));
    }
    G.isGlobalRows = isGlobalRows;
    G.isGlobalMarket = isGlobalMarket;

    // True for the Columns a global file carries that are not behavioral:
    // COUNTRY and every '<DEMO> | <Country>' block.
    function isGeoColumn(col) {
        var c = String(col == null ? '' : col).trim();
        return c.toUpperCase() === 'COUNTRY' || BLOCK_RX.test(c);
    }
    G.isGeoColumn = isGeoColumn;

    function ageRank(label) {
        var s = String(label == null ? '' : label).toUpperCase();
        if (/PREFER|NOT SAY|UNKNOWN/.test(s)) return 1e6;
        var m = s.match(/\d+/);
        var n = m ? Number(m[0]) : 999;
        if (/UNDER|YOUNGER|LESS THAN/.test(s)) return n - 1000;
        if (/OLDER|OVER|PLUS|\+/.test(s)) return n + 1000;
        return n;
    }
    function incomeRank(label) {
        var s = String(label == null ? '' : label).toUpperCase().replace(/,/g, '');
        if (/PREFER|NOT SAY|UNKNOWN/.test(s)) return 9e15;
        var m = s.match(/\d+(?:\.\d+)?/);
        var n = m ? Number(m[0]) : 1e9;
        var after = m ? s.slice(m.index + m[0].length, m.index + m[0].length + 2) : '';
        if (/^\s*K/.test(after)) n *= 1000;
        else if (/^\s*M/.test(after)) n *= 1000000;
        if (/UNDER|LESS THAN|BELOW|UP TO/.test(s)) return n - 1e12;
        if (/\+|OR MORE|OR ABOVE|AND UP|AND OVER|AND ABOVE|OVER/.test(s)) return n + 1e12;
        return n;
    }
    function lastly(label) {
        return /PREFER|NOT SAY|UNKNOWN/.test(String(label || '').toUpperCase());
    }
    function orderRows(demo, rows) {
        var out = rows.slice();
        if (demo === 'AGE') out.sort(function (a, b) { return ageRank(a.label) - ageRank(b.label); });
        else if (demo === 'INCOME') out.sort(function (a, b) { return incomeRank(a.label) - incomeRank(b.label); });
        else out.sort(function (a, b) {
            var la = lastly(a.label), lb = lastly(b.label);
            if (la !== lb) return la ? 1 : -1;
            return b.pct - a.pct;
        });
        return out;
    }
    G.orderRows = orderRows;

    function addRow(map, demo, label, pct) {
        if (!label || !isFinite(pct)) return;
        var list = map[demo] || (map[demo] = []);
        for (var i = 0; i < list.length; i++) {
            if (foldKey(list[i].label) === foldKey(label)) { list[i].pct += pct; return; }
        }
        list.push({ label: label, pct: pct });
    }

    function build(rows) {
        if (!isGlobalRows(rows)) return null;
        var model = {
            isGlobal: true, market: marketOf(rows), sample: 0, audience: 0,
            countryRows: {}, blocks: {}, world: {}, locations: []
        };
        rows.forEach(function (r) {
            if (!r) return;
            var col = String(r.Column == null ? '' : r.Column).trim();
            var colU = col.toUpperCase();
            var val = String(r.Value == null ? '' : r.Value).trim();
            if (!col) return;
            if (colU === 'BRAND INPUT') {
                var raw = num(getValue(r, 'Original Raw Numbers'));
                var proj = num(getValue(r, 'US Gen Pop Projection'));
                if (isFinite(raw) && raw > 0) model.sample = raw;
                if (isFinite(proj) && proj > 0) model.audience = proj;
                return;
            }
            if (colU === 'SAMPLE SIZE') {
                var s = num(getValue(r, 'Original Raw Numbers'));
                if (!model.sample && isFinite(s) && s > 0) model.sample = s;
                return;
            }
            var p = bp(r);
            if (!val || !isFinite(p)) return;
            if (colU === 'COUNTRY') {
                var c = canonCountry(val);
                if (c) model.countryRows[c] = (model.countryRows[c] || 0) + p;
                return;
            }
            var m = col.match(BLOCK_RX);
            if (m) {
                var demo = m[1].toUpperCase();
                var country = canonCountry(m[2]);
                if (!country) return;
                var blk = model.blocks[country] || (model.blocks[country] = {});
                addRow(blk, demo, val, p);
                return;
            }
            if (DEMOS.indexOf(colU) > -1) {
                addRow(model.world, colU, val, p);
                return;
            }
            if (colU === 'LOCATION') {
                var lc = locationCountry(val);
                model.locations.push({ name: val, city: lc.city, country: lc.country, pct: p });
            }
        });
        finish(model);
        return model;
    }
    G.build = build;

    function finish(model) {
        var shares = {};
        var fromCountry = Object.keys(model.countryRows).length > 0;
        var restRow = 0;
        if (fromCountry) {
            Object.keys(model.countryRows).forEach(function (c) {
                if (c === REST) restRow += model.countryRows[c];
                else shares[c] = model.countryRows[c];
            });
        } else {
            model.locations.forEach(function (l) {
                if (l.country) shares[l.country] = (shares[l.country] || 0) + l.pct;
            });
        }
        var shown = Object.keys(shares).filter(function (c) { return shares[c] >= THRESHOLD; });
        shown.sort(function (a, b) {
            if (a === US) return -1;
            if (b === US) return 1;
            return shares[b] - shares[a];
        });
        var shownSum = shown.reduce(function (t, c) { return t + shares[c]; }, 0);
        var rest;
        if (fromCountry) {
            rest = restRow;
            Object.keys(shares).forEach(function (c) { if (shares[c] < THRESHOLD) rest += shares[c]; });
        } else {
            rest = Math.max(0, 100 - shownSum);
        }
        var countries = shown.map(function (c, i) {
            return { name: c, share: shares[c], color: i, hasBlocks: hasBlocks(model, c) };
        });
        if (rest > 0.0001 || model.locations.some(function (l) { return shown.indexOf(l.country) < 0; })) {
            countries.push({ name: REST, share: rest, color: 'rest', hasBlocks: hasBlocks(model, REST), rest: true });
        }
        model.shareSource = fromCountry ? 'country' : 'location';
        model.countries = countries;
        model.switcher = countries.filter(function (c) { return c.hasBlocks; }).map(function (c) { return c.name; });
        var known = {};
        model.locations.forEach(function (l) { if (l.country) known[l.country] = 1; });
        model.nCountries = Object.keys(known).length;
        // Locations grouped by country, the same order as the countries.
        var groups = countries.map(function (c) {
            return { name: c.name, share: c.share, color: c.color, rest: !!c.rest, cities: [] };
        });
        var byName = {};
        groups.forEach(function (g) { byName[g.name] = g; });
        model.locations.forEach(function (l) {
            var g = byName[l.country] && !byName[l.country].rest ? byName[l.country] : byName[REST];
            if (!g) return;
            var city = l.city || l.name;
            g.cities.push({
                name: g.rest ? (l.country ? (city + ', ' + l.country) : l.name) : (g.name === US ? usCityLabel(city) : city),
                raw: l.name, country: l.country, pct: l.pct
            });
        });
        groups.forEach(function (g) {
            g.cities.sort(function (a, b) { return b.pct - a.pct; });
            if (g.rest) {
                var cs = {};
                g.cities.forEach(function (x) { if (x.country) cs[x.country] = 1; });
                g.nCountries = Object.keys(cs).length;
            }
        });
        model.groups = groups.filter(function (g) { return g.cities.length || g.share > 0; });
        model.topCities = model.locations.slice().sort(function (a, b) { return b.pct - a.pct; })
            .slice(0, 3).map(function (l) { return l.country === US ? leadCity(l.city || l.name) : (l.city || l.name); });
    }

    // In a sentence a US DMA reads as its lead city: 'New York Ny' -> 'New
    // York', 'Boston Ma Manchester Nh' -> 'Boston', 'Chicago IL' -> 'Chicago'.
    function leadCity(name) {
        var words = String(name || '').replace(/,/g, ' ').split(/\s+/).filter(Boolean);
        for (var i = 1; i < words.length; i++) {
            if (words[i].length === 2 && US_STATES[words[i].toUpperCase()]) return words.slice(0, i).join(' ');
        }
        return words.join(' ');
    }
    G.leadCity = leadCity;

    // A US city reads the way the US LOCATION page prints a DMA ('New York
    // Ny' -> 'New York NY').
    function usCityLabel(city) {
        try {
            if (typeof window.normalizeDmaDisplay === 'function') return String(window.normalizeDmaDisplay(city) || city);
        } catch (_e) { /* the city as written */ }
        return city;
    }

    function hasBlocks(model, country) {
        var b = model.blocks[country];
        return !!(b && Object.keys(b).some(function (k) { return (b[k] || []).length > 0; }));
    }

    // ---- the parse hooks -----------------------------------------------------

    function stripGeo(dict) {
        if (!dict || typeof dict !== 'object') return;
        Object.keys(dict).forEach(function (k) { if (isGeoColumn(k)) delete dict[k]; });
    }
    function isUsCity(name) {
        return locationCountry(name).country === US;
    }
    G.isUsCity = isUsCity;

    // parseAllData: the worldwide model rides on the parsed profile, the US
    // DMA list keeps the US cities only, COUNTRY and the blocks leave the
    // behavioral categories.
    function afterParseAll(result, csvData) {
        if (!result || typeof result !== 'object') return;
        var model = build(csvData);
        if (!model) return;
        result._cwGlobal = model;
        if (Array.isArray(result.locations)) {
            result.locations = result.locations.filter(function (l) { return l && isUsCity(l.name); });
        }
        stripGeo(result.behavioral);
        stripGeo(result.categories);
    }
    function afterParseComparison(result, csvData) {
        if (!result || typeof result !== 'object' || !isGlobalRows(csvData)) return;
        stripGeo(result.behavioral);
        stripGeo(result.behavioralProjection);
        stripGeo(result.categories);
    }
    G.afterParseAll = afterParseAll;
    G.afterParseComparison = afterParseComparison;

    // ---- rendering -----------------------------------------------------------

    var state = { country: {}, open: {} };
    G.state = state;

    function current() {
        var d = null;
        try { d = (typeof currentDashboardData !== 'undefined') ? currentDashboardData : null; } catch (_e) { d = null; }
        return d && d._cwGlobal ? d : null;
    }
    function currentKey() {
        try { return String((typeof currentS3Key !== 'undefined' && currentS3Key) || ''); } catch (_e) { return ''; }
    }
    function profileName() {
        try {
            var n = (typeof currentProfileData !== 'undefined' && currentProfileData && currentProfileData.name) || '';
            if (n) return String(n);
        } catch (_e) { /* fall through */ }
        return 'Profile';
    }
    function ensureHost(parent, id, cls) {
        var el = document.getElementById(id);
        if (!el) {
            el = document.createElement('section');
            el.id = id;
            el.className = cls;
        }
        if (el.parentNode !== parent || parent.firstChild !== el) parent.insertBefore(el, parent.firstChild);
        return el;
    }
    function drop(id) {
        var el = document.getElementById(id);
        if (el && el.parentNode) el.parentNode.removeChild(el);
    }
    function csvBtn(arg) {
        var attrs = 'type="button" class="piq-csv-btn cwg-csv has-tooltip" data-tooltip="' + esc(COPY.csvTip) +
            '" data-piq-csv-fn="cwGlobalCsv" data-piq-csv-arg="' + esc(arg) + '"';
        return '<button ' + attrs + '>CSV</button>';
    }
    function colorCls(c) { return c === 'rest' ? 'cwg-crest' : 'cwg-c' + Math.min(Number(c) || 0, 6); }
    function bars(rows, max) {
        max = max || rows.reduce(function (m, r) { return Math.max(m, r.pct); }, 0) || 100;
        return rows.map(function (r) {
            var w = Math.max(0, Math.min(100, r.pct / max * 100));
            return '<div class="cwg-bar-row" title="' + esc(r.label + ': ' + pct1(r.pct) + ', ' + plainShare(r.pct)) + '">' +
                '<span class="name">' + esc(r.label) + '</span>' +
                '<span class="track"><i style="width:' + w.toFixed(1) + '%"></i></span>' +
                '<span class="pct">' + pct1(r.pct) + '</span></div>';
        }).join('');
    }
    function headline(model) {
        var top = model.countries.filter(function (c) { return !c.rest; })
            .slice().sort(function (a, b) { return b.share - a.share; })[0];
        if (!top) return COPY.summaryEyebrow;
        var plain = plainShare(top.share);
        var where = withThe(top.name);
        if (plain === 'about half') return 'About half of this audience lives in ' + where + '.';
        if (plain === 'nearly all') return 'Nearly all of this audience lives in ' + where + '.';
        return plain.charAt(0).toUpperCase() + plain.slice(1) + ' people in this audience live in ' + where + '.';
    }
    G.headline = headline;

    function renderSummary(parsed) {
        var model = parsed._cwGlobal;
        var content = document.getElementById('dashboardContent');
        if (!content) return;
        var host = ensureHost(content, 'cwGlobalSummary', 'cwg-summary');
        var audience = Number(model.audience) || Number(parsed.projectedUS) || 0;
        var stack = model.countries.map(function (c) {
            var w = Math.max(0, Math.min(100, c.share));
            return '<span class="seg ' + colorCls(c.color) + '" style="width:' + w.toFixed(2) + '%" title="' +
                esc(c.name + ': ' + pct1(c.share) + ', ' + plainShare(c.share)) + '"></span>';
        }).join('');
        var legend = model.countries.map(function (c) {
            return '<div class="cwg-leg-row" title="' + esc(c.name + ': ' + plainShare(c.share)) + '">' +
                '<span class="sw ' + colorCls(c.color) + '"></span>' +
                '<span class="name">' + esc(c.name) + '</span>' +
                '<span class="pct">' + pct1(c.share) + '</span></div>';
        }).join('');
        var age = orderRows('AGE', model.world.AGE || []);
        var gender = orderRows('GENDER', model.world.GENDER || []);
        var html = '<div class="cwg-head"><div class="cwg-eyebrow">' + esc(COPY.summaryEyebrow) + '</div>' +
            '<h3>' + esc(headline(model)) + '</h3></div>' +
            '<div class="cwg-sum-grid">' +
            '<div class="cwg-tile cwg-total"><div class="lbl">' + esc(COPY.people) + '</div>' +
            '<div class="val">' + esc(count(audience) || '-') + '</div>' +
            (model.nCountries > 1 ? '<div class="sub">' + esc(fill(COPY.inCountries, { n: model.nCountries })) + '</div>' : '') +
            '</div>' +
            '<div class="cwg-tile cwg-where"><div class="lbl">' + esc(COPY.where) + '</div>' +
            '<div class="cwg-stack" role="img" aria-label="' + esc(model.countries.map(function (c) { return c.name + ' ' + pct1(c.share); }).join(', ')) + '">' + stack + '</div>' +
            '<div class="cwg-legend">' + legend + '</div></div>' +
            (age.length ? '<div class="cwg-tile cwg-age"><div class="lbl">' + esc(COPY.ageWorld) + '</div>' + bars(age) + '</div>' : '') +
            (gender.length ? '<div class="cwg-tile cwg-gender"><div class="lbl">' + esc(COPY.genderWorld) + '</div>' + bars(gender) + '</div>' : '') +
            '</div>';
        if (host._cwgHtml !== html) {
            host.innerHTML = html;
            host._cwgHtml = html;
        }
        syncSummaryTab();
    }

    function syncSummaryTab(tabName) {
        var host = document.getElementById('cwGlobalSummary');
        if (!host) return;
        var tab = tabName;
        if (!tab) {
            var view = document.getElementById('dashboardView');
            tab = (view && view.getAttribute('data-piq-tab')) || 'insights';
        }
        host.hidden = !SUMMARY_TABS[tab];
    }

    function demoTitle(demo, country) {
        if (demo === 'ETHNICITY' && country === US) return COPY.usEthnicity;
        return COPY.titles[demo] || demo;
    }

    function renderDemo(parsed) {
        var model = parsed._cwGlobal;
        var tab = document.getElementById('tab-demographics');
        if (!tab) return;
        var key = currentKey();
        var opts = model.switcher;
        var sel = state.country[key];
        if (opts.indexOf(sel) < 0) sel = opts[0] || '';
        state.country[key] = sel;
        var source = sel ? model.blocks[sel] : model.world;
        var html = '<div class="cwg-panel-head"><div class="stack">' +
            '<div class="cwg-eyebrow">' + esc(sel ? COPY.demoEyebrow : COPY.demoEyebrowWorld) + '</div>' +
            '<h3>' + esc(!sel ? COPY.peopleWorld : (sel === REST ? COPY.peopleRest : fill(COPY.peopleIn, { country: withThe(sel) }))) + '</h3>' +
            '</div><div class="cwg-actions">' + csvBtn('demo') + '</div></div>';
        if (opts.length) {
            var shareOf = {};
            model.countries.forEach(function (c) { shareOf[c.name] = c.share; });
            html += '<div class="cwg-switch" role="group" aria-label="' + esc(COPY.switchLabel) + '">' +
                '<span class="cwg-switch-lbl">' + esc(COPY.switchLabel) + '</span>' +
                opts.map(function (c) {
                    var on = c === sel;
                    return '<button type="button" class="cwg-opt' + (on ? ' is-on' : '') + '" aria-pressed="' + (on ? 'true' : 'false') +
                        '" data-cwg-country="' + esc(c) + '"><span class="name">' + esc(c) + '</span>' +
                        (isFinite(shareOf[c]) ? '<span class="pct">' + pct1(shareOf[c]) + '</span>' : '') + '</button>';
                }).join('') + '</div>';
        }
        html += '<p class="cwg-note">' + esc(sel ? COPY.noteCountry : COPY.noteWorld) + '</p>';
        var cards = [];
        DEMOS.forEach(function (demo) {
            var rows = (source && source[demo]) || [];
            if (!rows.length) return;
            var ordered = orderRows(demo, rows);
            var top = rows.slice().sort(function (a, b) { return b.pct - a.pct; })[0];
            var n = cards.length + 1;
            cards.push('<div class="cwg-card" data-cwg-demo="' + esc(demo) + '">' +
                '<div class="cwg-card-head"><span class="title">' + (n < 10 ? '0' : '') + n + '  \u00b7  ' + esc(demoTitle(demo, sel)) + '</span></div>' +
                (top ? '<p class="cwg-lede">' + esc(fill(COPY.largest, { label: top.label, plain: plainShare(top.pct) })) + '</p>' : '') +
                '<div class="cwg-bars">' + bars(ordered) + '</div></div>');
        });
        html += '<div class="cwg-cards">' + cards.join('') + '</div>';
        var host = ensureHost(tab, 'cwGlobalDemo', 'cwg-demo');
        host.innerHTML = html;
        tab.classList.add('cw-global-own');
    }

    function renderLocations(parsed) {
        var model = parsed._cwGlobal;
        var tab = document.getElementById('tab-location');
        if (!tab) return;
        var key = currentKey();
        var open = state.open[key] || (state.open[key] = {});
        var lede = model.topCities.length ? fill(COPY.locLede, { cities: joinNames(model.topCities) }) : '';
        var html = '<div class="cwg-panel-head"><div class="stack">' +
            '<div class="cwg-eyebrow">' + esc(COPY.locEyebrow) + '</div><h3>' + esc(COPY.locTitle) + '</h3></div>' +
            '<div class="cwg-actions"><input type="search" class="cwg-find" placeholder="' + esc(COPY.find) +
            '" aria-label="' + esc(COPY.find) + '">' + csvBtn('location') + '</div></div>' +
            (lede ? '<p class="cwg-lede">' + esc(lede) + '</p>' : '') +
            '<div class="cwg-groups">';
        model.groups.forEach(function (g) {
            var max = g.cities.reduce(function (m, x) { return Math.max(m, x.pct); }, 0) || 1;
            var n = g.cities.length;
            var countTxt = g.rest && g.nCountries > 1 ? fill(COPY.citiesIn, { n: n, m: g.nCountries })
                : (n === 1 ? COPY.city : fill(COPY.cities, { n: n }));
            var isOpen = !!open[g.name];
            html += '<div class="cwg-group' + (isOpen ? ' is-open' : '') + '" data-cwg-group="' + esc(g.name) + '">' +
                '<div class="cwg-group-head"><span class="sw ' + colorCls(g.color) + '"></span>' +
                '<span class="name">' + esc(g.name) + '</span>' +
                '<span class="meta"><span class="share">' + esc(fill(COPY.ofAudience, { pct: pct1(g.share) })) + '</span>' +
                (n ? '<span class="count">' + esc(countTxt) + '</span>' : '') + '</span></div>' +
                '<div class="cwg-group-bar"><i class="' + colorCls(g.color) + '" style="width:' + Math.max(0, Math.min(100, g.share)).toFixed(2) + '%"></i></div>' +
                '<div class="cwg-cities">';
            g.cities.forEach(function (x, i) {
                var within = g.share > 0 && !g.rest ? x.pct / g.share * 100 : NaN;
                var tip = (!g.rest && isFinite(within))
                    ? fill(COPY.cityTip, { city: x.name, pct: pct2(x.pct), plain: plainShare(within), country: withThe(g.name) })
                    : (x.name + ': ' + pct2(x.pct) + ' of the audience.');
                html += '<div class="cwg-city' + (i >= CITY_ROWS_SHOWN ? ' is-extra' : '') + '" data-cwg-find="' +
                    esc((x.name + ' ' + (x.country || g.name)).toLowerCase()) + '" title="' + esc(tip) + '">' +
                    '<span class="name">' + esc(x.name) + '</span>' +
                    '<span class="track"><i style="width:' + (x.pct / max * 100).toFixed(1) + '%"></i></span>' +
                    '<span class="pct">' + pct2(x.pct) + '</span></div>';
            });
            html += '</div>';
            if (n > CITY_ROWS_SHOWN) {
                html += '<button type="button" class="cwg-more" data-cwg-more="' + esc(g.name) + '">' +
                    esc(isOpen ? COPY.showFewer : fill(COPY.showAll, { n: n })) + '</button>';
            }
            html += '</div>';
        });
        html += '</div><p class="cwg-empty" hidden>' + esc(COPY.noMatch) + '</p>';
        var host = ensureHost(tab, 'cwGlobalLocations', 'cwg-loc');
        var prevFind = host.querySelector('.cwg-find');
        var keep = prevFind ? prevFind.value : '';
        host.innerHTML = html;
        tab.classList.add('cw-global');
        if (keep) {
            var f = host.querySelector('.cwg-find');
            if (f) { f.value = keep; applyFind(host, keep); }
        }
    }
    function joinNames(list) {
        if (list.length <= 1) return list.join('');
        if (list.length === 2) return list[0] + ' and ' + list[1];
        return list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
    }
    G.joinNames = joinNames;

    function applyFind(host, q) {
        q = String(q || '').toLowerCase().trim();
        var any = false;
        host.classList.toggle('is-finding', !!q);
        Array.prototype.forEach.call(host.querySelectorAll('.cwg-group'), function (g) {
            var hits = 0;
            Array.prototype.forEach.call(g.querySelectorAll('.cwg-city'), function (row) {
                var ok = !q || (row.getAttribute('data-cwg-find') || '').indexOf(q) > -1;
                row.classList.toggle('is-hit', !!q && ok);
                row.hidden = !!q && !ok;
                if (ok) hits++;
            });
            var groupName = (g.getAttribute('data-cwg-group') || '').toLowerCase();
            var show = !q || hits > 0 || groupName.indexOf(q) > -1;
            g.hidden = !show;
            if (show) any = true;
        });
        var empty = host.querySelector('.cwg-empty');
        if (empty) empty.hidden = any;
    }

    function teardown() {
        drop('cwGlobalSummary');
        drop('cwGlobalDemo');
        drop('cwGlobalLocations');
        var d = document.getElementById('tab-demographics');
        if (d) d.classList.remove('cw-global-own');
        var l = document.getElementById('tab-location');
        if (l) l.classList.remove('cw-global');
    }

    function apply(parsed) {
        parsed = (parsed && parsed._cwGlobal) ? parsed : current();
        if (!parsed) { teardown(); return; }
        var steps = [renderSummary, renderDemo, renderLocations];
        steps.forEach(function (fn) {
            try { fn(parsed); } catch (e) { if (window.console) console.error('[global-profile] ' + (fn.name || 'render') + ' failed:', e); }
        });
    }
    G.apply = apply;
    G.teardown = teardown;

    // ---- events ----------------------------------------------------------------

    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        var opt = t.closest('#cwGlobalDemo .cwg-opt');
        if (opt) {
            e.preventDefault();
            state.country[currentKey()] = opt.getAttribute('data-cwg-country') || '';
            var p = current();
            if (p) { try { renderDemo(p); } catch (err) { if (window.console) console.error('[global-profile]', err); } }
            var again = document.querySelector('#cwGlobalDemo .cwg-opt.is-on');
            if (again && again.focus) again.focus();
            return;
        }
        var more = t.closest('#cwGlobalLocations .cwg-more');
        if (more) {
            e.preventDefault();
            var name = more.getAttribute('data-cwg-more') || '';
            var open = state.open[currentKey()] || (state.open[currentKey()] = {});
            open[name] = !open[name];
            var grp = more.closest('.cwg-group');
            if (grp) grp.classList.toggle('is-open', !!open[name]);
            var n = grp ? grp.querySelectorAll('.cwg-city').length : 0;
            more.textContent = open[name] ? COPY.showFewer : fill(COPY.showAll, { n: n });
        }
    });
    document.addEventListener('input', function (e) {
        var t = e.target;
        if (!t || !t.classList || !t.classList.contains('cwg-find')) return;
        var host = document.getElementById('cwGlobalLocations');
        if (host) applyFind(host, t.value);
    });

    // ---- CSV -----------------------------------------------------------------

    function csvCell(v) {
        var s = String(v == null ? '' : v);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }
    function download(name, text) {
        try {
            var blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = name;
            document.body.appendChild(a);
            a.click();
            setTimeout(function () { URL.revokeObjectURL(a.href); if (a.parentNode) a.parentNode.removeChild(a); }, 0);
        } catch (e) { if (window.console) console.error('[global-profile] CSV failed:', e); }
    }
    function csvFor(model, what, country) {
        var lines = [];
        if (what === 'location') {
            lines.push(['Country', 'Country share of the audience (%)', 'City', 'City share of the audience (%)'].join(','));
            model.groups.forEach(function (g) {
                g.cities.forEach(function (x) {
                    lines.push([csvCell(g.rest ? (x.country || REST) : g.name), g.share.toFixed(4),
                        csvCell(g.rest ? x.raw : x.name), x.pct.toFixed(4)].join(','));
                });
            });
        } else {
            var src = country ? model.blocks[country] : model.world;
            lines.push(['Country', 'Category', 'Group', 'Share (%)'].join(','));
            DEMOS.forEach(function (demo) {
                orderRows(demo, (src && src[demo]) || []).forEach(function (r) {
                    lines.push([csvCell(country || 'Worldwide'), csvCell(demoTitle(demo, country)), csvCell(r.label), r.pct.toFixed(4)].join(','));
                });
            });
        }
        return lines.join('\n') + '\n';
    }
    G.csvFor = csvFor;
    window.cwGlobalCsv = function (what) {
        var p = current();
        if (!p) return;
        var model = p._cwGlobal;
        var name = profileName();
        if (what === 'location') {
            download(name + ' - Cities by country.csv', csvFor(model, 'location'));
        } else {
            var c = state.country[currentKey()] || '';
            download(name + ' - Demographics - ' + (c || 'Worldwide') + '.csv', csvFor(model, 'demo', c));
        }
    };

    // ---- install ---------------------------------------------------------------

    function wrap(name, after, before) {
        var orig = window[name];
        if (typeof orig !== 'function' || orig.__cwGlobal) return false;
        var w = function () {
            if (before) { try { before.apply(this, arguments); } catch (e) { if (window.console) console.error('[global-profile] ' + name, e); } }
            var out = orig.apply(this, arguments);
            try { after.call(this, out, arguments); } catch (e) { if (window.console) console.error('[global-profile] ' + name, e); }
            return out;
        };
        w.__cwGlobal = true;
        w.__cwOrig = orig;
        window[name] = w;
        return true;
    }
    G.wrap = wrap;

    function install() {
        var done = G._hooks || (G._hooks = {});
        if (!done.parseAllData) done.parseAllData = wrap('parseAllData', function (out, args) { afterParseAll(out, args[0]); });
        if (!done.parseComparisonData) done.parseComparisonData = wrap('parseComparisonData', function (out, args) { afterParseComparison(out, args[0]); });
        if (!done.renderLocationTable) done.renderLocationTable = wrap('renderLocationTable', function () {
            var p = current();
            if (p) renderLocations(p);
            else {
                drop('cwGlobalLocations');
                var l = document.getElementById('tab-location');
                if (l) l.classList.remove('cw-global');
            }
        });
        if (!done._piqV4OnProfile) done._piqV4OnProfile = wrap('_piqV4OnProfile', function (_out, args) { apply(args[0]); });
        if (!done._piqV4OnTab) done._piqV4OnTab = wrap('_piqV4OnTab', function (_out, args) { syncSummaryTab(args[0]); });
        if (!done._piqV4OnClear) done._piqV4OnClear = wrap('_piqV4OnClear', function () { teardown(); });
        return done;
    }
    G.install = install;
    install();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', install);
    }
})();
