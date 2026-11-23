        (function() {
            var S = window._dtw = window._dtw || { runs: [], twin: null, key: '', tab: 'twins', openRun: null, session: null, pilot: null, plan: null, howto: null, loaded: false, horizon: '10', spendDay: 1, spendMode: 'chip', pricing: [], summary: null, panelKind: '', portraitTimers: [], roomPollTimer: null, roomTick: null, libKind: 'all', libQ: '', coverTries: 0 };
            var TABS = ['twins', 'predict', 'greenlight', 'launch', 'spend', 'panel', 'pilot', 'trailer', 'titles', 'norms'];
            // Hidden for now (Jenna 2026-11-18): Launch runs on hand-written constants, Norms fills only as tests are filed, and the
            // Greenlight, Trailers and Titles and Key Art asks are carried by Predictive Analytics and Focus Group IQ. The buttons stay
            // in the page hidden, a deep link lands on Twins; one list with migration.digital_twins.HIDDEN_TABS.
            var DTW_HIDDEN_TABS = ['greenlight', 'launch', 'trailer', 'titles', 'norms'];
            window._dtwHiddenTabs = DTW_HIDDEN_TABS;
            // Greenlight is two options inside one tab (Jenna 2026-10-28): dtwTab('script') and dtwTab('concept') land on Greenlight with that option picked.
            var GL_OPTIONS = ['script', 'concept'];
            var PI_TABS = { pilot: ['pilot', 'feature', 'episode', 'binge'], trailer: ['trailer', 'spot'], titles: ['title', 'key_art'], script: ['script'], concept: ['concept'] };
            var PI_TAB_LABEL = { pilot: 'Pilot Testing', trailer: 'Trailers', titles: 'Titles and Key Art', script: 'Script read', concept: 'Concept', norms: 'Norms' };
            var PI_MODE_LABEL = { pilot: 'Pilot test', feature: 'Feature screening', episode: 'Episode test', binge: 'Binge test', trailer: 'Trailer test', spot: 'TV spot test', title: 'Title test', key_art: 'Key art test', script: 'Script read', concept: 'Concept test' };
            var PI_HOST = { pilot: 'dtwPiConsole', trailer: 'dtwTrConsole', titles: 'dtwTiConsole', script: 'dtwScConsole', concept: 'dtwCoConsole' };
            var LAB_LABEL = { greenlight: 'Greenlight', launch: 'Launch', spend: 'Spend Engine', panel: 'Focus Group IQ', pilot: 'Pilot Testing', compare: 'Compare', content: 'Content drop', platform_move: 'Platform move', partnership: 'Partnership', pricing: 'Pricing', talent_attach: 'Talent attach', merch_drop: 'Merch drop' };
            // Stored runs of the retired Greenlight kind stay on the twin and never reach the page (the API filters too; this is the belt).
            function isRetiredRun(r) { if (!r) return false; if (r.kind === 'greenlight') return true; return r.kind === 'compare' && String(((r.inputs || {}).scenario_kind) || '') === 'greenlight'; }
            function productRuns(t) { return ((t || {}).runs || []).filter(function(r) { return !isRetiredRun(r); }); }
            // Fixed hues for the initials tiles, one per seat, from the brand palette. Never Signal Green.
            var HUES = ['#3358FF', '#E682FF', '#B7B3D8', '#8E3FA8', '#5E7E12'];
            function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
            function fmtN(n) { n = Number(n || 0); return isFinite(n) ? Math.round(n).toLocaleString('en-US') : String(n); }
            function fmtMoney(v) { v = Number(v || 0); return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
            function human(n) { n = Number(n || 0); if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M'; if (n >= 1e4) return (n / 1e3).toFixed(1) + 'K'; return fmtN(n); }
            function humanMoney(v) { v = Number(v || 0); if (v >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B'; if (v >= 1e6) return '$' + (v / 1e6).toFixed(2) + 'M'; if (v >= 1e4) return '$' + (v / 1e3).toFixed(1) + 'K'; return fmtMoney(v); }
            function pct(v, d) { v = Number(v || 0); return v.toFixed(d == null ? 1 : d) + '%'; }
            function signed(v, d) { v = Number(v || 0); return (v >= 0 ? '+' : '') + pct(v, d); }
            var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            function fmtDate(s) { var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || '')); if (!m) return String(s || ''); return MONTHS[parseInt(m[2], 10) - 1] + ' ' + parseInt(m[3], 10) + ', ' + m[1]; }
            function fmtDay(s) { var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || '')); if (!m) return String(s || ''); var y = new Date().getFullYear(); return MONTHS[parseInt(m[2], 10) - 1] + ' ' + parseInt(m[3], 10) + (parseInt(m[1], 10) !== y ? ', ' + m[1] : ''); }
            function titleWords(s) { return String(s || '').replace(/(^|\s)([a-z])/g, function(m, a, b) { return a + b.toUpperCase(); }); }
            function fmtWhen(s) { var m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(s || '')); if (!m) return fmtDate(s); return fmtDate(s) + ', ' + m[4] + ':' + m[5]; }
            function titleCase(s) { return String(s || '').toLowerCase().replace(/(^|[\s\-'.])([a-z])/g, function(m, a, b) { return a + b.toUpperCase(); }); }
            function initials(name) { var p = String(name || '').trim().split(/\s+/).filter(Boolean); if (!p.length) return '?'; if (p.length === 1) return p[0].slice(0, 2).toUpperCase(); return (p[0][0] + p[p.length - 1][0]).toUpperCase(); }
            function hueFor(i) { return HUES[Math.abs(parseInt(i, 10) || 0) % HUES.length]; }
            function el(id) {
                var e = document.getElementById(id);
                if (e) return e;
                // the room's elements live in the popped-out window while it is open (2026-11-03)
                var p = S.roomPop; if (p && p.win && !p.win.closed) { try { return p.win.document.getElementById(id); } catch (_) {} }
                return null;
            }
            function jsq(s) { return String(s || '').replace(/\\/g, '').replace(/'/g, '').replace(/"/g, ''); }
            function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
            function kindLabel(k) { return LAB_LABEL[k] || cap(String(k || '').replace(/_/g, ' ')); }
            async function api(path, opts) {
                var r = await fetch('/api/digital-twins' + path, Object.assign({ credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } }, opts || {}));
                var d = null; try { d = await r.json(); } catch (_) { d = null; }
                if (!r.ok || !d || d.success === false) { var err = new Error((d && d.error) || ('Request failed (' + r.status + ')')); err.doc = d || {}; err.status = r.status; throw err; }
                return d;
            }
            // ---- busy buttons (Jenna 2026-11-05: 'when you click on this something needs to show an indication that it is working because
            // I can't tell if it is or I need to click again'). dtwBusy(btn, label, line) disables the control at once, swaps its label for a
            // working label with a spinner, holds the other buttons in the same row, and opens the stage line (an existing status element,
            // or true for a line made under the row); dtwStage(tok, text) moves the line as the request moves; dtwIdle(tok, err) puts everything
            // back and, on a failure, prints what happened in plain words. dtwBusy returns false when the control is already working, so a
            // handler that starts with `if (tok === false) return;` can never run twice for one click. dtwBtn() finds the clicked control.
            var DTW_BUSY_ROWS = '.dtw-lab-run, .dtw-actions, .dtw-bw-nav, .dtw-pi-askrow, .dtw-pi-controls, .dtw-assign, .dtw-fg-field, .dtw-fg-acts, .dtw-fg-nextp, .dtw-pi-editing, .dtw-fg-land-acts';
            function dtwBusyHtml(label) { return '<span class="dtw-spin" aria-hidden="true"></span>' + esc(label || 'Working'); }
            function dtwBusyLineHtml(text) { return '<div class="dtw-busy-line" aria-live="polite">' + dtwBusyHtml(text || 'Working') + '</div>'; }
            function dtwIsBusy(b) { return !!(b && b.classList && b.classList.contains('dtw-busy')); }
            function dtwBtn(fallback) {
                var ev = null, t = null;
                try { ev = window.event; } catch (_) { ev = null; }
                if (ev) { try { t = (ev.currentTarget && ev.currentTarget.nodeType === 1) ? ev.currentTarget : ev.target; } catch (_) { t = null; } }
                if (t && t.closest) { var b = t.closest('button, a'); if (b && (b.ownerDocument !== document || b.closest('#digitalTwinsIQView'))) return b; }
                if (!fallback) return null;
                var q = null, p = S.roomPop;
                if (p && p.win && !p.win.closed) { try { q = p.win.document.querySelector(fallback); } catch (_) { q = null; } }
                if (!q) { try { q = document.querySelector('#digitalTwinsIQView ' + fallback); } catch (_) { q = null; } }
                return q;
            }
            function dtwBusy(b, label, line) {
                if (b && dtwIsBusy(b)) return false;
                var tok = { btn: b || null, html: '', wasDisabled: false, held: [], line: null, made: false };
                var row = (b && b.closest) ? b.closest(DTW_BUSY_ROWS) : null;
                if (b) {
                    tok.html = b.innerHTML; tok.wasDisabled = !!b.disabled;
                    b.classList.add('dtw-busy'); b.setAttribute('aria-busy', 'true');
                    if (b.tagName !== 'A') b.disabled = true;
                    b.innerHTML = dtwBusyHtml(label);
                    var scope = row || b.parentNode;
                    if (scope && scope.querySelectorAll) scope.querySelectorAll('button, a.dtw-btn, a.dtw-fg-act').forEach(function(o) {
                        if (o === b || dtwIsBusy(o)) return;
                        if (o.tagName === 'A') { if (o.classList.contains('dtw-held')) return; o.classList.add('dtw-held'); o.setAttribute('aria-disabled', 'true'); }
                        else { if (o.disabled) return; o.disabled = true; }
                        tok.held.push(o);
                    });
                }
                if (line === true) {
                    if (row && row.parentNode) {
                        var nx = row.nextElementSibling;
                        if (nx && nx.classList && nx.classList.contains('dtw-busy-line')) tok.line = nx;
                        else { var d = (row.ownerDocument || document).createElement('div'); d.className = 'dtw-busy-line'; d.setAttribute('aria-live', 'polite'); row.parentNode.insertBefore(d, row.nextSibling); tok.line = d; }
                        tok.made = true;
                    }
                } else if (line) { tok.line = (typeof line === 'string') ? el(line) : line; }
                if (tok.line) { tok.line.classList.remove('err'); tok.line.innerHTML = dtwBusyHtml('Working'); }
                return tok;
            }
            function dtwStage(tok, text) { if (!tok || !tok.line) return; tok.line.classList.remove('err'); tok.line.innerHTML = dtwBusyHtml(text || 'Working'); }
            function dtwLabel(tok, label) { if (tok && tok.btn && dtwIsBusy(tok.btn)) tok.btn.innerHTML = dtwBusyHtml(label); }
            // more controls held with this one, outside its row (the test bar's Exit test / Duplicate / Delete while Play starts)
            function dtwHoldMore(tok, sel) {
                if (!tok || !sel) return;
                var root = (tok.btn && tok.btn.ownerDocument) || document, nodes = [];
                try { nodes = Array.prototype.slice.call(root.querySelectorAll(sel)); } catch (_) { nodes = []; }
                nodes.forEach(function(o) {
                    if (o === tok.btn || dtwIsBusy(o)) return;
                    if (o.tagName === 'A') { if (o.classList.contains('dtw-held')) return; o.classList.add('dtw-held'); o.setAttribute('aria-disabled', 'true'); }
                    else { if (o.disabled) return; o.disabled = true; }
                    tok.held.push(o);
                });
            }
            // play() with the same ceiling: a stalled media link never holds the controls (the player keeps loading on its own)
            function dtwPlayWait(v, ms) { return Promise.race([Promise.resolve().then(function() { return v.play(); }), new Promise(function(res) { setTimeout(res, ms || 8000); })]); }
            // the moment the player shows a frame (a frame is already decoded, or playing / loadeddata fires), with a ceiling so a silent stream never holds the page
            function dtwFirstFrame(v, ms) {
                return new Promise(function(res) {
                    if (!v || v.readyState >= 2) { res(true); return; }
                    var done = false, t = setTimeout(function() { fin(false); }, ms || 6000);
                    function fin(ok) { if (done) return; done = true; clearTimeout(t); try { v.removeEventListener('playing', onp); v.removeEventListener('loadeddata', onp); } catch (_) {} res(ok); }
                    function onp() { fin(true); }
                    v.addEventListener('playing', onp); v.addEventListener('loadeddata', onp);
                });
            }
            // what the reader sees on a failure: the request's own line when it is plain, else one calm sentence
            function dtwSafeErr(e, fallback) {
                var m = (e == null) ? '' : (typeof e === 'string' ? e : String(e.message || ''));
                m = m.replace(/[\u2014\u2013]/g, '-').trim();
                if (!m || /^request failed/i.test(m) || /<\/?[a-z][^>]*>/i.test(m) || /traceback|internal server error|exception|stack/i.test(m) || m.length > 320) m = fallback || 'That did not go through. Try again in a moment.';
                return m;
            }
            function dtwIdle(tok, err) {
                if (!tok) return;
                var b = tok.btn;
                if (b) { b.classList.remove('dtw-busy'); b.removeAttribute('aria-busy'); b.innerHTML = tok.html; if (b.tagName !== 'A') b.disabled = tok.wasDisabled; }
                tok.held.forEach(function(o) { if (o.tagName === 'A') { o.classList.remove('dtw-held'); o.removeAttribute('aria-disabled'); } else o.disabled = false; });
                tok.held = [];
                var line = tok.line; if (!line) return;
                if (err != null && err !== false) { line.classList.add('err'); line.textContent = dtwSafeErr(err); }
                else if (tok.made) { if (line.parentNode) line.parentNode.removeChild(line); }
                else { line.classList.remove('err'); line.textContent = ''; }
            }
            // the export links: the file is fetched while the link shows it is working, then saved under the name the server gives it
            window.dtwDownload = async function(a, ev) {
                if (ev && ev.preventDefault) ev.preventDefault();
                if (!a || dtwIsBusy(a)) return false;
                var href = a.getAttribute('href'); if (!href) return false;
                var tok = dtwBusy(a, 'Preparing the file', true);
                try {
                    var r = await fetch(href, { credentials: 'same-origin' });
                    if (!r.ok) { var d = null; try { d = await r.json(); } catch (_) { d = null; } throw new Error((d && d.error) || 'The file could not be prepared. Try again in a moment.'); }
                    var blob = await r.blob();
                    var cd = r.headers.get('Content-Disposition') || '', m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
                    var name = m ? decodeURIComponent(m[1]) : (href.split('/').pop().split('?')[0] || 'download');
                    var url = URL.createObjectURL(blob), t = document.createElement('a'); t.href = url; t.download = name; t.style.display = 'none'; document.body.appendChild(t); t.click();
                    setTimeout(function() { try { document.body.removeChild(t); } catch (_) {} try { URL.revokeObjectURL(url); } catch (_) {} }, 1500);
                    dtwIdle(tok);
                } catch (e) { dtwIdle(tok, (e && e.message) || 'The file could not be prepared. Try again in a moment.'); }
                return false;
            };
            // ---- the per-group card (Jenna 2026-11-05): S.card from /list carries the seat's plan, tiers, rates and quote lines
            function groupCard() { return S.card || null; }
            function groupTierDefault() { var c = groupCard(); return c && c.default_tier ? String(c.default_tier) : '20'; }
            function groupTierOptions(sel) {
                var c = groupCard(), tiers = (c && c.tiers && c.tiers.length) ? c.tiers : [20, 40], cur = String(sel || groupTierDefault());
                return tiers.map(function(t) { var lab = (c && c.labels && c.labels[String(t)]) ? ' (' + c.labels[String(t)] + ')' : ''; return '<option value="' + t + '"' + (String(t) === cur ? ' selected' : '') + '>1-' + t + ' questions' + lab + '</option>'; }).join('');
            }
            function groupQuote(tier) {
                var c = groupCard(); if (!c) return '';
                if (c.free) return 'Crosswalk seats are never charged.';
                return (c.quote && c.quote[String(tier || groupTierDefault())]) || '';
            }
            function bar(label, v, max, alt, suffix) { var w = max > 0 ? Math.max(1, Math.min(100, v / max * 100)) : 0; return '<div class="dtw-bar"><span title="' + esc(label) + '">' + esc(label) + '</span><div class="t"><div class="f' + (alt ? ' alt' : '') + '" style="width:' + w.toFixed(1) + '%"></div></div><span class="n">' + esc(suffix != null ? suffix : pct(v)) + '</span></div>'; }
            // The figures strip: [value, label, sub?, lead?]. One lead figure in the accent, the rest in ink.
            function figs(items) { return '<div class="dtw-figs">' + items.map(function(t) { return '<div class="dtw-fig"><div class="k">' + esc(t[1]) + '</div><div class="v' + (t[3] ? ' lead' : '') + '">' + esc(t[0]) + '</div>' + (t[2] ? '<div class="s">' + esc(t[2]) + '</div>' : '') + '</div>'; }).join('') + '</div>'; }
            function kv(items) { return '<div class="dtw-kv">' + items.filter(function(i) { return i[0]; }).map(function(i) { return '<div><div class="k">' + esc(i[1]) + '</div><div class="v">' + esc(i[0]) + '</div></div>'; }).join('') + '</div>'; }
            function card(title, sub, body, extra) { return '<div class="dtw-card"><div class="dtw-card-h"><div><h3>' + esc(title) + '</h3>' + (sub ? '<div class="dtw-sub">' + esc(sub) + '</div>' : '') + '</div>' + (extra || '') + '</div>' + body + '</div>'; }
            function paras(list, cls) { return (list || []).map(function(s) { return '<p class="' + (cls || 'dtw-p') + '">' + esc(s) + '</p>'; }).join(''); }
            function twinName() { return ((S.twin || {}).meta || {}).name || ((S.twin || {}).meta || {}).subject || ''; }
            function exportRow(label) {
                if (!S.key) return '';
                return card(label || 'Export', 'The twin and every run on it, in the format you need.', '<div class="dtw-actions"><a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/export.csv">CSV</a><a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/export.pdf">PDF</a><a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/export.pptx">Deck</a><button type="button" class="dtw-btn" onclick="dtwChatSend(\'Build a deck of the ' + esc(jsq(twinName())) + ' twin\')">Deck via Prometheus</button></div>');
            }
            function chatSend(text) { try { if (typeof window.prometheusExpand === 'function') window.prometheusExpand(); } catch (_) {} try { if (typeof synthChatChipSend === 'function') synthChatChipSend(text); } catch (_) {} }
            function chatFill(text) { try { if (typeof window.prometheusExpand === 'function') window.prometheusExpand(); } catch (_) {} try { if (typeof synthChatChipFill === 'function') synthChatChipFill(text); } catch (_) {} }
            window.dtwChatSend = chatSend; window.dtwChatFill = chatFill;
            window._dtwHelpers = { esc: esc, fmtN: fmtN, fmtMoney: fmtMoney, human: human, humanMoney: humanMoney, pct: pct, el: el, api: api, bar: bar, figs: figs, kv: kv, card: card, jsq: jsq, fmtDate: fmtDate, initials: initials, hueFor: hueFor, titleCase: titleCase };

            // ---------------------------------------------------------------- page
            // Every other product view; hidden when this one shows (the dropdown guard does the same on a user pick).
            var HIDE_IDS = ['formView', 'dashboardView', 'subscriberIQView', 'llmoIQView', 'rankerIQView', 'ticketSalesIQView', 'ticketSalesTrackerView', 'hedgeFundIQView', 'sentimentIQView', 'roasIQView', 'ecommerceIQView', 'shareOfTimeIQView', 'sfConversionView', 'flywheelConversionView', 'flywheelIQView', 'customAnalysisView', 'talentSearchIQView', 'talentTheaterIQView', 'talentFitIQView', 'journeyIQView', 'blueIQView', 'intentIQView', 'helmIQView', 'microdramasIQView', 'trendsIQView', 'impactIQView', 'brandTrackingIQView'];
            // Where the reader was (twin and tab) survives a reload, like the product itself (localStorage['bg_last_product']).
            function remember() { try { localStorage.setItem('bg_dtw_last', JSON.stringify({ key: S.key || '', tab: S.tab || 'twins', glo: S.glOption || 'script', sid: S.session ? S.session.id : '', pid: S.pilot ? S.pilot.id : '' })); } catch (_) {} }
            function isPiTab(tab) { return tab === 'greenlight' || !!PI_HOST[tab]; }
            function remembered() { try { var d = JSON.parse(localStorage.getItem('bg_dtw_last') || 'null'); return d && typeof d === 'object' ? d : null; } catch (_) { return null; } }
            // A deep link: ?view=digitalTwinsIQ&twin=<key>&tab=<tab>, or #digitalTwinsIQ?twin=<key>&tab=<tab>. Read once per page load.
            function urlHint() {
                try {
                    var q = new URLSearchParams(window.location.search), twin = q.get('twin') || '', tab = q.get('tab') || '', room = q.get('room') || '', record = q.get('record') || '';
                    var h = String(window.location.hash || '').replace(/^#/, '');
                    if (h.indexOf('digitalTwinsIQ') === 0) { var hp = new URLSearchParams(h.split('?')[1] || ''); twin = twin || hp.get('twin') || ''; tab = tab || hp.get('tab') || ''; room = room || hp.get('room') || ''; }
                    if (!twin && !tab && !record) return null;
                    return { twin_key: twin, tab: tab, room: room, record: record };
                } catch (_) { return null; }
            }
            window.showDigitalTwinsIQ = function(hint) {
                if (window.digitalTwinsLocked) return;
                var _lockOpt = document.querySelector('#viewNavDropdown option[value="digitalTwinsIQ"]');
                if (_lockOpt && _lockOpt.getAttribute('data-locked') === '1') return;
                hint = hint || window._dtwLastHint || null;
                window._dtwLastHint = null;
                try {
                    var dd = document.getElementById('viewNavDropdown');
                    if (typeof setViewNavDropdown === 'function') setViewNavDropdown('digitalTwinsIQ'); else if (dd) dd.value = 'digitalTwinsIQ';
                    HIDE_IDS.forEach(function(id) { var e = document.getElementById(id); if (e) e.style.display = 'none'; });
                    try { if (typeof hideLLMOIQView === 'function') hideLLMOIQView(); } catch (_) {}
                    var v = el('digitalTwinsIQView'); if (v) { v.style.display = 'flex'; v.style.visibility = 'visible'; }
                    try { if (typeof window.dtwApplyTabGrant === 'function') window.dtwApplyTabGrant(); } catch (_) {}
                    try { localStorage.setItem('bg_last_product', 'digitalTwinsIQ'); } catch (_) {}
                } catch (_) {}
                var fromUrl = null;
                if (!hint && !S.urlUsed) { S.urlUsed = true; fromUrl = urlHint(); }
                var mem = (!hint && !fromUrl && !S.twin) ? remembered() : null;
                dtwLoadList(false).then(function() {
                    var known = function(k) { return !!k && S.runs.some(function(r) { return r.key === k; }); };
                    if (hint && hint.twin_key) { dtwOpen(hint.twin_key, hint.run_id || null, hint.session_id || null, hint.plan_id || null, hint.tab || null); }
                    else if (hint && hint.tab) { dtwTab(hint.tab); }
                    else if (fromUrl && known(fromUrl.twin_key)) { dtwOpen(fromUrl.twin_key, null, fromUrl.room || null, null, fromUrl.tab || (fromUrl.room ? 'panel' : null)); }
                    else if (fromUrl && fromUrl.tab) { if (!S.twin && S.runs.length === 1) dtwOpen(S.runs[0].key, null, null, null, fromUrl.tab); else dtwTab(fromUrl.tab); }
                    else if (mem && known(mem.key)) { if (mem.glo && GL_OPTIONS.indexOf(mem.glo) >= 0) S.glOption = mem.glo; dtwOpen(mem.key, null, isPiTab(mem.tab) ? (mem.pid || mem.sid) : mem.sid, null, mem.tab || null); }
                    else if (!S.twin && S.runs.length === 1) { dtwOpen(S.runs[0].key, null, (mem && mem.key === S.runs[0].key && mem.sid) || null, null, (mem && mem.tab) || null); }
                    else if (mem && mem.tab) { dtwTab(mem.tab); }
                    if (fromUrl && fromUrl.record) { setTimeout(function() { try { dtwRecordOpen(true); } catch (_) {} }, 250); }
                });
                if (!S.howto) { api('/how-to').then(function(d) { S.howto = d; renderHowto(); renderEmptyAll(); }).catch(function() {}); }
            };
            window.dtwTab = function(tab) {
                if (tab === 'howto') { dtwHowtoOpen(true); return; }
                if (GL_OPTIONS.indexOf(tab) >= 0) { S.glOption = tab; tab = 'greenlight'; }
                // Launch holds the plan and the Spend Engine (2026-11-03): 'spend' lands on Launch with that option picked
                if (tab === 'spend') { S.laOption = 'spend'; tab = 'launch'; }
                else if (tab === 'launch' && S.laOption !== 'spend') { S.laOption = 'plan'; }
                if (DTW_HIDDEN_TABS.indexOf(tab) >= 0) tab = 'twins';
                if (TABS.indexOf(tab) < 0) tab = 'twins';
                if (typeof window.dtwTabAllowed === 'function' && !window.dtwTabAllowed(tab)) {
                    var _first = (typeof window.dtwFirstAllowedTab === 'function') ? window.dtwFirstAllowedTab() : 'twins';
                    if (_first && _first !== tab) { dtwTab(_first); return; }
                    return;
                }
                S.tab = tab;
                try { rpTick(); } catch (_) {}
                document.querySelectorAll('#digitalTwinsIQView .dtw-seg').forEach(function(b) { b.classList.toggle('active', b.getAttribute('data-tab') === tab); });
                document.querySelectorAll('#digitalTwinsIQView .dtw-pane').forEach(function(p) { p.classList.remove('active'); });
                var pane = el('dtwTab' + tab.charAt(0).toUpperCase() + tab.slice(1)); if (pane) pane.classList.add('active');
                syncInputCards();
                syncFgBody();
                try { piFocusScroll(); } catch (_) {}
                if (tab === 'panel') { renderPanelSessions(); if (!S.session || !el('dtwFgHero')) renderPanelConsole(); else fgShown(); }
                var piT = tab === 'greenlight' ? (GL_OPTIONS.indexOf(S.glOption) >= 0 ? S.glOption : 'script') : (PI_HOST[tab] ? tab : '');
                if (piT) { S.piTab = piT; if (S.pilot && piTabFor(S.pilot.mode) !== piT) { S.pilot = null; } if (tab === 'greenlight') syncGreenlightOptions(); renderPilotConsole(); }
                if (tab === 'norms') { renderNormsTab(); }
                if (tab === 'predict') { renderPredict(); }
                if (tab === 'launch') {
                    syncLaunchOptions();
                    if (S.laOption === 'spend') { syncSpendSources(); if (!S.plan) { var plans = ((S.twin || {}).labs || {}).spend || []; if (plans.length) { S.plan = plans[0]; S.spendDay = 1; } } renderSpend(); }
                    else if (el('dtwLaResults') && !el('dtwLaResults').innerHTML) renderEmpty('dtwLaResults', 'launch');
                }
                var body = document.querySelector('#digitalTwinsIQView .dtw-body'); if (body) body.scrollTop = 0;
                remember();
                try { if (typeof _pmRefreshStarterChips === 'function') _pmRefreshStarterChips(); } catch (_) {}
            };
            // The two Launch options: the plan and the Spend Engine, one body shown at a time.
            var LA_OPTIONS = ['plan', 'spend'];
            function syncLaunchOptions() {
                var opt = LA_OPTIONS.indexOf(S.laOption) >= 0 ? S.laOption : 'plan';
                S.laOption = opt;
                document.querySelectorAll('#dtwTabLaunch .dtw-la-opt').forEach(function(b) { var on = b.getAttribute('data-la') === opt; b.classList.toggle('active', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
                var la = el('dtwLaBody'), sp = el('dtwSpBody');
                if (la) la.hidden = opt !== 'plan';
                if (sp) sp.hidden = opt !== 'spend';
            }
            window.dtwLaunchPick = function(opt) {
                S.laOption = LA_OPTIONS.indexOf(opt) >= 0 ? opt : 'plan';
                dtwTab(S.laOption === 'spend' ? 'spend' : 'launch');
            };
            // The two Greenlight options: the chooser at the top of the pane, one console shown at a time.
            function syncGreenlightOptions() {
                var opt = GL_OPTIONS.indexOf(S.glOption) >= 0 ? S.glOption : 'script';
                S.glOption = opt;
                document.querySelectorAll('#dtwTabGreenlight .dtw-gl-opt').forEach(function(b) { var on = b.getAttribute('data-gl') === opt; b.classList.toggle('active', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
                GL_OPTIONS.forEach(function(o) { var h = el(PI_HOST[o]); if (!h) return; var on = o === opt; h.hidden = !on; if (!on) h.innerHTML = ''; });
            }
            window.dtwGreenlightPick = function(opt) {
                if (GL_OPTIONS.indexOf(opt) < 0) opt = 'script';
                S.glOption = opt; S.piTab = opt; S.tab = 'greenlight';
                if (S.pilot && piTabFor(S.pilot.mode) !== opt) { S.pilot = null; }
                syncGreenlightOptions();
                renderPilotConsole();
                remember();
            };
            window.dtwHowtoOpen = function(on) {
                var d = el('dtwHowtoDrawer'), s = el('dtwHowtoScrim');
                if (!d) return;
                d.classList.toggle('open', !!on); if (s) s.classList.toggle('open', !!on);
                d.setAttribute('aria-hidden', on ? 'false' : 'true');
                if (on) { renderHowto(); if (!S.howto) api('/how-to').then(function(x) { S.howto = x; renderHowto(); renderEmptyAll(); }).catch(function() {}); }
            };
            document.addEventListener('keydown', function(e) { if (e.key !== 'Escape') return; var p = el('dtwSeatPop'); if (p && p.classList.contains('open')) return; var d = el('dtwHowtoDrawer'); if (d && d.classList.contains('open')) dtwHowtoOpen(false); });
            // The empty state of a lab tab: the directions once, short. The full text lives in the drawer.
            function renderEmpty(hostId, id) {
                var h = el(hostId); if (!h) return;
                var it = ((S.howto || {}).items || []).filter(function(x) { return x.id === id; })[0];
                var pick = S.twin ? '' : '<p class="dtw-p">Pick a twin on the Twins tab first, or build one.</p>';
                if (!it) { h.innerHTML = '<div class="dtw-card"><div class="dtw-eyebrow">Before the first run</div><h3 style="margin-top:0.35rem;">' + esc(kindLabel(id)) + '</h3>' + pick + '<p class="dtw-p">Fill in the card on the left and run it, or type the ask to Prometheus.</p><div class="dtw-actions"><button type="button" class="dtw-link" onclick="dtwHowtoOpen(true)">Full directions</button></div></div>'; return; }
                h.innerHTML = '<div class="dtw-card"><div class="dtw-eyebrow">Before the first run</div><h3 style="margin-top:0.35rem;">' + esc(it.title) + '</h3>' + pick + '<p class="dtw-p">' + esc(it.answers) + '</p><h4>Put in</h4><p class="dtw-muted">' + esc(it.put_in) + '</p><h4>Or type this to Prometheus</h4><div class="dtw-chips">' + (it.type_this || []).slice(0, 2).map(function(t) { return '<button type="button" class="dtw-chip" onclick="dtwChatFill(\'' + esc(jsq(t)) + '\')">' + esc(t) + '</button>'; }).join('') + '</div><div class="dtw-actions" style="margin-top:0.5rem;"><span class="dtw-muted">' + esc(it.cost) + '.</span><button type="button" class="dtw-link dtw-right" onclick="dtwHowtoOpen(true)">Full directions</button></div></div>';
            }
            function renderEmptyAll() {
                if (el('dtwLaResults') && !el('dtwLaResults').querySelector('[data-dtw-run-id]')) renderEmpty('dtwLaResults', 'launch');
                if (!S.plan && el('dtwSpResults')) renderEmpty('dtwSpResults', 'spend');
                if (!S.session && el('dtwPnConsole')) renderPanelConsole();
                if (!S.pilot && el('dtwPiConsole')) renderPilotConsole();
                if (el('dtwNoConsole') && !el('dtwNoConsole').innerHTML && S.tab === 'norms') renderNormsTab();
            }
            // ---- Build a twin, the walkthrough (Jenna 2026-10-27: 'when you click to build a twin we need to have it walk someone thru how to do it').
            // Four short steps in a drawer: what the twin stands for (a defined group from a profile, pulled the way Profile IQ pulls it, a group
            // shaped by demographics, or a list of content), the audience itself, what it is for, then the review with the prices. The seats are
            // never asked for: a room seats one person per kind of people in the audience, twelve or more (Jenna 2026-11-17). Prometheus usage is
            // metered on top of the priced steps.
            var BW_STEPS = ['Source', 'Audience', 'Purpose', 'Review'];   // no seat count is asked for: a room seats one person per kind of people in the audience, twelve or more (Jenna 2026-11-17)
            var BW_DEMO = [
                ['gender', 'Gender', ['Women', 'Men']],
                ['age', 'Age', ['18-24', '25-34', '35-44', '45-54', '55-64', '65+']],
                ['income', 'Household income', ['Less than $25,000', '$25,000 - $49,999', '$50,000 - $74,999', '$75,000 - $99,999', '$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or more']],
                ['ethnicity', 'Ethnicity', ['White', 'Hispanic or Latino', 'Black or African American', 'Asian', 'Another race/ethnicity']],
                ['parental', 'Parents', ['Has children', 'No children']]
            ];
            // ---- A share beside each pick (Jenna 2026-11-13: 'it should allow you to list the percentage you want of each demographic
            // tab. so if I select men I would then type in 50% or 30%'). A pick with a share is that part of the group; picks with no
            // share take what is left in their natural mix; with no shares anywhere a pick is the filter it always was. One group's
            // shares total at most 100. The server checks the same rules again.
            var BW_SHARE_LABEL = { gender: 'Gender', age: 'Age', income: 'Household income', ethnicity: 'Ethnicity', parental: 'Parents', education: 'Education' };
            var BW_SHARE_HINT = 'Want a set mix? Type a share beside a pick, like 30. With no shares, the group is only the picks you chose.';
            function bwShareNum(raw) {
                var s = String(raw == null ? '' : raw).replace(/%/g, '').trim();
                if (!s) return null;
                if (!/^\d{0,3}(\.\d*)?$/.test(s) || s === '.') return NaN;
                var x = parseFloat(s);
                return isFinite(x) ? Math.round(x * 10) / 10 : NaN;
            }
            function bwPctWords(x) { x = Math.round(Number(x || 0) * 10) / 10; return (Math.round(x) === x ? String(x) : x.toFixed(1)) + '%'; }
            function bwShareSet(cat) {
                var b = S.bw || {}, sh = (b.shares || {})[cat] || {}, out = {};
                ((b.demo || {})[cat] || []).forEach(function(v) { var x = bwShareNum(sh[v]); if (x != null && isFinite(x) && x > 0 && x <= 100) out[v] = x; });
                return out;
            }
            function bwShareState(cat) {
                var b = S.bw, g = BW_DEMO.filter(function(x) { return x[0] === cat; })[0], picks = b.demo[cat] || [], sh = (b.shares || {})[cat] || {};
                var name = BW_SHARE_LABEL[cat] || cat, set = [], open = [], bad = false, tot = 0;
                picks.forEach(function(v) {
                    var x = bwShareNum(sh[v]);
                    if (x == null || x === 0) { open.push(v); return; }
                    if (!isFinite(x) || x < 0 || x > 100) { bad = true; return; }
                    set.push([v, x]); tot += x;
                });
                tot = Math.round(tot * 10) / 10;
                if (bad) return { err: 'A share is a number from 0 to 100.', line: '' };
                if (!set.length) return { err: '', line: '' };
                var said = set.map(function(p) { return p[0] + ' ' + bwPctWords(p[1]); }).join(', ') + '. ';
                if (tot > 100) return { err: name + ' adds to ' + bwPctWords(tot) + '. The shares in one group can total at most 100%.', line: '' };
                var left = Math.round((100 - tot) * 10) / 10;
                if (left <= 0) return open.length ? { err: name + ' already adds to 100%. Lower a share, or clear ' + open.join(' and ') + '.', line: '' } : { err: '', line: said + 'That is the whole group.' };
                if (open.length) return { err: '', line: said + 'The other ' + bwPctWords(left) + ' goes to ' + (open.length === 1 ? open[0] : open.slice(0, -1).join(', ') + ' and ' + open[open.length - 1] + ', in their natural mix') + '.' };
                if (g && picks.length >= g[2].length) return { err: 'Every option in ' + name + ' has a share, so they need to add to 100%. They add to ' + bwPctWords(tot) + '.', line: '' };
                return { err: '', line: said + 'The other ' + bwPctWords(left) + ' follows the audience\'s natural mix.' };
            }
            function bwSharesOk() { return BW_DEMO.every(function(g) { return !bwShareState(g[0]).err; }); }
            function bwSharesErr() { var e = ''; BW_DEMO.forEach(function(g) { if (!e) e = bwShareState(g[0]).err; }); return e; }
            function bwSharesBody() { var out = {}; BW_DEMO.forEach(function(g) { var set = bwShareSet(g[0]); if (Object.keys(set).length) out[g[0]] = set; }); return out; }
            function bwSharesWords(shares) {
                shares = shares || bwSharesBody();
                return Object.keys(shares).map(function(c) { var blk = shares[c] || {}; return (BW_SHARE_LABEL[c] || c) + ': ' + Object.keys(blk).map(function(v) { return v + ' ' + bwPctWords(blk[v]); }).join(', ') + '.'; }).join(' ');
            }
            function bwShareInput(cat, v) {
                // the share box sits right under the pill it belongs to (Jenna 2026-11-17), so the pill names it
                var sh = (S.bw.shares || {})[cat] || {};
                return '<label class="dtw-bw-share"><input class="dtw-in" inputmode="decimal" maxlength="5" autocomplete="off" value="' + esc(sh[v] == null ? '' : sh[v]) + '" placeholder="share" aria-label="Share of the group that is ' + esc(v) + ', in percent" data-cat="' + cat + '" data-v="' + esc(v) + '" oninput="dtwBwShare(this)"><i>%</i></label>';
            }
            function bwShareLine(cat) {
                var picks = S.bw.demo[cat] || [];
                if (!picks.length) return '';
                var st = bwShareState(cat);
                return '<div class="dtw-bw-shareline' + (st.err ? ' err' : '') + '" id="dtwBwShareLine-' + cat + '">' + esc(st.err || st.line || BW_SHARE_HINT) + '</div>';
            }
            var BW_PURPOSES = ['A new season or sequel', 'A product launch', 'Pricing', 'A partnership', 'Casting or talent', 'An ad plan', 'To ask it questions'];
            var BW_INCLUDED = 12, BW_MAX = 40;
            function bwFresh() { return { step: 0, source: '', subject: '', profile_key: '', profile_name: '', profile_cat: '', q: '', results: null, loading: false, demo: {}, shares: {}, markets: '', interests: '', purpose: '', chip: '', busy: false, job: null, err: '', list: ulFresh() }; }
            function bwPrice(which) {
                // the twin is included (2026-11-05); a group is priced at the plan rate (S.card); the audience build keeps its line
                if (which === 'twin' || which === 'panelist') return 0;
                if (which === 'session') { var c = groupCard(); return (c && c.rates && c.rates[groupTierDefault()] != null) ? Number(c.rates[groupTierDefault()]) : 750; }
                var lines = S.pricing || [], rx = { profile: /^audience build\b/i }[which];
                var line = lines.filter(function(x) { return rx.test(x); })[0] || '';
                var m = /\$([0-9,]+(?:\.\d+)?)/.exec(line);
                return m ? parseFloat(m[1].replace(/,/g, '')) : { profile: 300 }[which];
            }
            function usd(v) { v = Number(v || 0); return '$' + v.toLocaleString('en-US', { minimumFractionDigits: (Math.round(v) === v) ? 0 : 2, maximumFractionDigits: 2 }); }
            window.dtwBuildOpen = function(on) {
                if (on) { try { dtwNewRoomOpen(false); dtwRoomsOpen(false); dtwHowtoOpen(false); } catch (_) {} if (!S.bw || (S.bw.job && S.bw.job.done)) S.bw = bwFresh(); }
                drawer('dtwBuildDrawer', 'dtwBuildScrim', on, function() { renderBuild(); if (!(S.pricing || []).length) dtwLoadList(false).then(function() { renderBuild(); }); });
            };
            window.dtwBuildTwin = function() { dtwBuildOpen(true); };
            function bwRail() {
                var b = S.bw;
                return '<div class="dtw-bw-rail">' + BW_STEPS.map(function(n, i) {
                    var cls = i < b.step ? 'done' : (i === b.step ? 'on' : '');
                    return (i ? '<span class="sep"></span>' : '') + '<button type="button" class="' + cls + '"' + (i < b.step && !b.job ? ' onclick="dtwBwGo(' + i + ')"' : '') + '><span class="n">' + (i < b.step ? '&#10003;' : (i + 1)) + '</span>' + n + '</button>';
                }).join('') + '</div>';
            }
            function bwNav(canNext, nextLabel) {
                var b = S.bw, busy = !!b.busy;
                return '<div class="dtw-bw-nav">' + (b.step > 0 ? '<button type="button" class="dtw-btn"' + (busy ? ' disabled' : '') + ' onclick="dtwBwGo(' + (b.step - 1) + ')">Back</button>' : '') +
                    '<button type="button" class="dtw-btn dtw-btn-primary dtw-right' + (busy ? ' dtw-busy" aria-busy="true"' : '"') + (canNext && !busy ? '' : ' disabled') + ' onclick="dtwBwNext()">' + (busy ? dtwBusyHtml(nextLabel || 'Working') : esc(nextLabel || 'Next')) + '</button></div>' + (busy ? dtwBusyLineHtml('Working') : '');
            }
            function bwMarkets() { return String(S.bw.markets || '').split(/,|;|\n/).map(function(s) { return s.trim(); }).filter(Boolean); }
            function bwSpans(picks, parse) {
                var spans = [], ok = true;
                picks.forEach(function(p) { var s = parse(p); if (!s) ok = false; else spans.push(s); });
                if (!ok || !spans.length) return null;
                spans.sort(function(a, b) { return a[0] - b[0]; });
                var merged = [spans[0].slice()];
                spans.slice(1).forEach(function(s) { var last = merged[merged.length - 1]; if (s[0] <= last[1] + 1) last[1] = Math.max(last[1], s[1]); else merged.push(s.slice()); });
                return merged;
            }
            function bwAgeWords(picks) {
                var m = bwSpans(picks, function(p) { if (p === '65+') return [65, 999]; var r = /^(\d{2})-(\d{2})$/.exec(p); return r ? [parseInt(r[1], 10), parseInt(r[2], 10)] : null; });
                return m ? m.map(function(s) { return s[1] >= 999 ? s[0] + '+' : s[0] + ' to ' + s[1]; }).join(', ') : picks.join(', ');
            }
            function bwIncomeWords(picks) {
                var m = bwSpans(picks, function(p) {
                    var nums = (p.match(/\$?(\d[\d,]{2,})/g) || []).map(function(x) { return parseInt(x.replace(/[$,]/g, ''), 10); }), low = p.toLowerCase();
                    if (/less|under/.test(low) && nums.length) return [0, nums[0] - 1];
                    if (/more|over|\+/.test(low) && nums.length) return [nums[0], 1e9];
                    return nums.length >= 2 ? [nums[0], nums[1]] : null;
                });
                return m ? m.map(function(s) { return s[0] <= 0 ? 'under $' + (s[1] + 1).toLocaleString('en-US') : s[1] >= 1e9 ? '$' + s[0].toLocaleString('en-US') + ' or more' : '$' + s[0].toLocaleString('en-US') + ' to $' + s[1].toLocaleString('en-US'); }).join(', ') : picks.join(', ');
            }
            function bwAudienceLabel() {
                var b = S.bw;
                if (b.source === 'list') { var u = b.list || {}, p = u.preview; return String(u.name || '').trim() || (p ? fmtN(p.count) + ' URLs of content' : 'a list of content'); }
                if (b.source !== 'demographics') return b.profile_name || b.subject || '';
                var d = b.demo, parts = [], mix = [];
                // a group with shares reads share first ('30% Men and 70% Women'); a pick with no share keeps its plain name
                var mixed = function(cat, vals, sep) {
                    // one pick with no share takes all that is left, so the name says its share too ('30% Men and 70% Women')
                    var set = bwShareSet(cat), blank = vals.filter(function(v) { return set[v] == null; }), tot = 0;
                    Object.keys(set).forEach(function(v) { tot += set[v]; });
                    var left = Math.round((100 - tot) * 10) / 10;
                    if (blank.length === 1 && left > 0) set[blank[0]] = left;
                    return vals.map(function(v) { return set[v] != null ? bwPctWords(set[v]) + ' ' + v : v; }).join(sep);
                };
                var has = function(cat) { return Object.keys(bwShareSet(cat)).length > 0; };
                if ((d.gender || []).length) { if (has('gender')) mix.push(mixed('gender', d.gender, ' and ')); else parts.push(d.gender.join(' and ')); }
                if ((d.age || []).length) { if (has('age')) mix.push(mixed('age', d.age, ', ')); else parts.push(bwAgeWords(d.age)); }
                var head = parts.join(' ') || 'US adults', tail = mix.slice();
                if ((d.income || []).length) tail.push(has('income') ? mixed('income', d.income, ', ') : bwIncomeWords(d.income));
                if ((d.ethnicity || []).length) tail.push(has('ethnicity') ? mixed('ethnicity', d.ethnicity, ', ') : d.ethnicity.join(', '));
                if ((d.parental || []).length) tail.push(has('parental') ? mixed('parental', d.parental, ', ') : d.parental.join(', '));
                var mk = bwMarkets();
                if (mk.length) tail.push('in ' + (mk.length <= 4 ? mk.join(', ').replace(/, ([^,]*)$/, ' and $1') : mk.length + ' markets'));
                if (String(b.interests || '').trim()) tail.push('who ' + b.interests.trim().replace(/\.$/, ''));
                return head + (tail.length ? ', ' + tail.join(', ') : '');
            }
            function bwDemoPicked() { var d = S.bw.demo; return Object.keys(d).some(function(k) { return (d[k] || []).length; }) || bwMarkets().length > 0; }
            function bwCanNext() {
                var b = S.bw;
                if (b.step === 0) return !!b.source;
                if (b.step === 1) return b.source === 'demographics' ? (bwDemoPicked() && bwSharesOk()) : (b.source === 'list' ? (ulHasList() && !!String(b.list.name || '').trim()) : !!(b.profile_key || b.subject));
                if (b.source === 'list' && !(b.list && b.list.preview)) return false;    // the list is read back before the purpose and the review
                return true;
            }
            function bwStepSource() {
                var b = S.bw;
                return '<div class="dtw-bw-q">What should the twin stand for?</div><div class="dtw-bw-sub">A twin is an audience you can put questions, titles, prices and plans to. Pick where its people come from.</div>'
                    + '<div class="dtw-bw-opts">'
                    + '<button type="button" class="dtw-bw-opt' + (b.source === 'profile' ? ' on' : '') + '" onclick="dtwBwSource(\'profile\')"><span class="k">A defined group</span><b>From a profile</b><span>The audience of a title, a talent, a brand or a product. Prometheus pulls the audience and seats the panelists the same way Profile IQ does.</span></button>'
                    + '<button type="button" class="dtw-bw-opt' + (b.source === 'demographics' ? ' on' : '') + '" onclick="dtwBwSource(\'demographics\')"><span class="k">A group you shape</span><b>By demographics</b><span>Pick gender, age, income, ethnicity, parents and markets. Prometheus builds the audience to that shape and seats the panelists from it.</span></button>'
                    + '<button type="button" class="dtw-bw-opt' + (b.source === 'list' ? ' on' : '') + '" onclick="dtwBwSource(\'list\')"><span class="k">A list of content</span><b>From a list</b><span>Upload or paste the URLs of the content. The twin is the people who watched, read or visited it.</span></button>'
                    + '</div>'
                    + '<div class="dtw-bw-sub" style="margin-top:0.9rem;">Or just tell Prometheus in the chat: <button type="button" class="dtw-link" onclick="dtwBuildOpen(false); dtwChatFill(\'Build a twin of \')">Build a twin of ...</button></div>'
                    + bwNav(!!b.source);
            }
            function bwRows() {
                var b = S.bw, q = String(b.q || '').trim(), rows = b.results || [], out = '';
                var exact = rows.some(function(r) { return String(r.display_name || '').toLowerCase() === q.toLowerCase(); });
                if (q.length >= 2 && !exact) out += '<button type="button" class="dtw-bw-row new' + (!b.profile_key && b.subject === q ? ' on' : '') + '" onclick="dtwBwNewSubject()">Build from a new subject: &ldquo;' + esc(q) + '&rdquo;<span class="c">Prometheus pulls the audience</span></button>';
                if (b.loading && !rows.length) out += '<div class="dtw-bw-row quiet">Looking through your profiles...</div>';
                out += rows.map(function(r) { return '<button type="button" class="dtw-bw-row' + (r.s3_key === b.profile_key ? ' on' : '') + '" onclick="dtwBwPickProfile(this)" data-key="' + esc(r.s3_key) + '" data-name="' + esc(r.display_name) + '" data-cat="' + esc(r.category || '') + '">' + esc(r.display_name) + '<span class="c">' + esc(r.category || 'profile') + '</span></button>'; }).join('');
                if (!b.loading && b.results && !rows.length && q.length < 2) out += '<div class="dtw-bw-row quiet">No profiles on your list yet. Type a subject and Prometheus pulls the audience.</div>';
                return out;
            }
            function bwStepAudience() {
                var b = S.bw;
                if (b.source === 'list') return ulForm();
                if (b.source === 'demographics') {
                    var out = '<div class="dtw-bw-q">Who is in the group?</div><div class="dtw-bw-sub">Pick as much or as little as you like. Anything you leave open stays the US mix.</div>';
                    BW_DEMO.forEach(function(g) {
                        var picks = b.demo[g[0]] || [];
                        out += '<div class="dtw-bw-group"><div class="k"><span>' + esc(g[1]) + '</span><em>pick any, then set a share if you want one</em></div><div class="dtw-chips dtw-chips-cols">' + g[2].map(function(o) {
                            var on = picks.indexOf(o) >= 0;
                            return '<span class="dtw-chipcol">' + '<button type="button" class="dtw-chip' + (on ? ' on' : '') + '" onclick="dtwBwPick(\'' + g[0] + '\', this)" data-v="' + esc(o) + '">' + esc(o) + '</button>' + (on ? bwShareInput(g[0], o) : '') + '</span>';
                        }).join('') + '</div>' + bwShareLine(g[0]) + '</div>';
                    });
                    out += '<div class="dtw-bw-group"><div class="k"><span>Markets</span><em>optional, by name</em></div><input class="dtw-in" value="' + esc(b.markets) + '" placeholder="Dallas, Los Angeles (blank is national)" oninput="dtwBwField(\'markets\', this.value)"></div>';
                    out += '<div class="dtw-bw-group"><div class="k"><span>They also</span><em>optional</em></div><input class="dtw-in" value="' + esc(b.interests) + '" placeholder="shop at Target, follow NASCAR, subscribe to Hulu" oninput="dtwBwField(\'interests\', this.value)"></div>';
                    out += '<div class="dtw-bw-line" id="dtwBwLine">The twin: <b>' + esc(bwAudienceLabel()) + '</b></div>';
                    return out + bwNav(bwDemoPicked() && bwSharesOk());
                }
                var pick = '';
                if (b.profile_key) pick = '<div class="dtw-bw-pick"><span>Picked: <b>' + esc(b.profile_name) + '</b>. The twin uses this audience as pulled.</span><button type="button" class="dtw-link" onclick="dtwBwClearPick()">Change</button></div>';
                else if (b.subject) pick = '<div class="dtw-bw-pick"><span>New subject: <b>' + esc(b.subject) + '</b>. Prometheus pulls the audience as part of the build, the way Profile IQ does.</span><button type="button" class="dtw-link" onclick="dtwBwClearPick()">Change</button></div>';
                return '<div class="dtw-bw-q">Which audience?</div><div class="dtw-bw-sub">Search the profiles you already have, or type a new subject: a title, a talent, a brand, a product.</div>'
                    + '<input id="dtwBwQ" class="dtw-in" value="' + esc(b.q) + '" placeholder="Gilmore Girls, Mark Rober, Nike..." autocomplete="off" oninput="dtwBwQuery(this.value)" onkeydown="if(event.key===\'Enter\'){event.preventDefault(); dtwBwEnter();}">'
                    + '<div class="dtw-bw-list" id="dtwBwList">' + bwRows() + '</div>' + pick + bwNav(!!(b.profile_key || b.subject));
            }
            function bwStepPurpose() {
                var b = S.bw;
                return '<div class="dtw-bw-q">What will you use it for?</div><div class="dtw-bw-sub">This shapes the first two runs and the read. One tap is enough; add a line if it helps.</div>'
                    + '<div class="dtw-chips">' + BW_PURPOSES.map(function(p) { return '<button type="button" class="dtw-chip' + (b.chip === p ? ' on' : '') + '" onclick="dtwBwChip(this)" data-v="' + esc(p) + '">' + esc(p) + '</button>'; }).join('') + '</div>'
                    + '<label class="dtw-field">In your words (optional)</label><textarea class="dtw-in" placeholder="Season 2 pricing on Hulu, with a Target boxed set at the holidays" oninput="dtwBwField(\'purpose\', this.value)">' + esc(b.purpose) + '</textarea>'
                    + bwNav(true, 'Next');
            }
            function bwStepReview() {
                var b = S.bw, pt = bwPrice('twin'), ps = bwPrice('session');
                if (b.source === 'list') return ulReview();
                // a new subject (no profile picked) has its audience pulled first (2026-11-04): the audience build is priced with the twin
                var pull = b.source !== 'demographics' && !b.profile_key && !!b.subject, pa = pull ? bwPrice('profile') : 0;
                var rows = [['Source', b.source === 'demographics' ? 'A group shaped by demographics' : (b.profile_key ? 'A defined group, from the profile you picked' : 'A defined group; no audience on file yet, so Prometheus pulls it first'), 0],
                            ['Audience', bwAudienceLabel(), 1], ['Purpose', (b.chip ? b.chip : '') + (b.purpose.trim() ? (b.chip ? '. ' : '') + b.purpose.trim() : '') || 'Open', 2],
                            ['Seats', 'One archetype representative per archetype in the audience, twelve or more, each sized by the people it stands for (the seats are part of each group)', null]];
                var shw = b.source === 'demographics' ? bwSharesWords() : '';
                if (shw) rows.splice(2, 0, ['Shares', shw, 1]);
                var sum = '<div class="dtw-bw-sum">' + rows.map(function(r) { return '<div class="r"><span class="k">' + r[0] + '</span><span class="v">' + esc(r[1]) + (r[2] === null ? '' : '<button type="button" class="dtw-link" onclick="dtwBwGo(' + r[2] + ')">Change</button>') + '</span></div>'; }).join('') + '</div>';
                var gc = groupCard(), gl = (gc && gc.labels) || {}, glab = (gl['20'] && gl['40']) ? (gl['20'] + ' (1-20 questions) or ' + gl['40'] + ' (1-40)') : (gl['40'] ? gl['40'] + ' (1-40 questions)' : usd(ps));
                var price = '<div class="dtw-bw-price">'
                    + (pull ? '<div class="r"><span><i>The audience</i>, pulled first (usually under an hour)</span><b>' + usd(pa) + '</b></div>' : '')
                    + '<div class="r"><span><i>The twin</i>, with its first two runs</span><b>Included</b></div>'
                    + '<div class="r"><span><i>Each group</i> you open on it (a focus group room or a pilot test' + (gc && gc.plan_label ? ', ' + esc(gc.plan_label.toLowerCase()) : '') + ')</span><b>' + esc(glab) + '</b></div>'
                    + '<div class="r"><span><i>Prometheus</i>, the build and every question</span><b>Metered</b></div>'
                    + '<div class="r total"><span>Charged now</span><b>' + usd(pa) + '</b></div>'
                    + '<div class="m">' + (pull ? 'If this audience is already on file, nothing is charged now. ' : '') + 'Each group is charged to your company when it opens; the seats are part of it. Prometheus usage is billed at the published rate on top.</div></div>';
                var when = pull ? 'The audience is pulled first (usually under an hour), then the twin is built from it; both land on the Twins tab and Prometheus confirms in the chat and by email.' : 'The twin lands on the Twins tab in a few minutes.';
                return '<div class="dtw-bw-q">Ready to build.</div><div class="dtw-bw-sub">Check the ' + (rows.length > 4 ? 'five' : 'four') + ' lines, then build. ' + when + '</div>' + sum + price
                    + (b.err ? '<div class="dtw-bw-err">' + esc(b.err) + '</div>' : '') + bwNav(!b.busy, b.busy ? 'Starting the build' : 'Build the twin');
            }
            function bwProgress() {
                var j = S.bw.job || {};
                if (j.error) return '<div class="dtw-bw-q">The twin did not finish.</div><div class="dtw-bw-sub">' + esc(j.error) + '</div><div class="dtw-bw-nav"><button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwBwRetry()">Try again</button><button type="button" class="dtw-btn" onclick="dtwBuildOpen(false)">Close</button></div>';
                if (j.done) return '<div class="dtw-bw-q">Your twin is ready.</div><div class="dtw-bw-sub">' + esc(j.ready || (j.subject + ' is live on the Twins tab.')) + '</div><div class="dtw-bw-nav"><button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwBwOpenTwin()">Open the twin</button><button type="button" class="dtw-btn" onclick="dtwBwAnother()">Build another</button></div>';
                return '<div class="dtw-bw-q">Building ' + esc(j.subject || 'the twin') + '.</div><div class="dtw-bw-sub">' + (j.from_list ? 'The audience your list defines is pulled first, then the twin is built from it. You can close this; it lands on the Twins tab and Prometheus confirms in the chat and by email.' : 'Prometheus is on it. You can close this; the twin lands on the Twins tab and Prometheus confirms in the chat.') + '</div>'
                    + '<div class="dtw-bw-prog"><div class="bar"><i></i></div><div class="stage"><span class="dtw-fg-pulse"></span><span id="dtwBwStage">' + esc(j.stage || 'queued') + '</span></div></div>'
                    + '<div class="dtw-bw-nav"><button type="button" class="dtw-btn" onclick="dtwBuildOpen(false)">Close, it keeps building</button></div>';
            }
            function renderBuild() {
                var h = el('dtwBuildBody'); if (!h) return;
                if (!S.bw) S.bw = bwFresh();
                var b = S.bw;
                if (b.job) { h.innerHTML = bwRail() + bwProgress(); return; }
                h.innerHTML = bwRail() + [bwStepSource, bwStepAudience, bwStepPurpose, bwStepReview][b.step]();
                if (b.step === 1 && b.source === 'profile') {
                    var q = el('dtwBwQ'); if (q) setTimeout(function() { try { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } catch (_) {} }, 80);
                    if (b.results === null && !b.loading) bwSearch();
                }
            }
            window.dtwBwGo = function(i) { if (S.bw.job) return; S.bw.step = Math.max(0, Math.min(BW_STEPS.length - 1, i)); S.bw.err = ''; renderBuild(); };
            window.dtwBwNext = function() {
                if (!bwCanNext()) return;
                var b = S.bw;
                if (b.step === 1 && b.source === 'list' && !(b.list && b.list.preview)) return dtwUlPreview();   // the list is read back, then the wizard moves on
                if (b.step >= 3) { dtwBwSubmit(); return; }
                dtwBwGo(b.step + 1);
            };
            window.dtwBwSource = function(src) { var b = S.bw; if (b.source !== src) { b.source = src; b.profile_key = ''; b.profile_name = ''; b.subject = ''; b.results = null; } dtwBwGo(1); };
            window.dtwBwPick = function(cat, btn) {
                var b = S.bw, v = btn.getAttribute('data-v');
                var cur = b.demo[cat] || [], i = cur.indexOf(v);
                if (i >= 0) cur.splice(i, 1); else cur.push(v);
                b.demo[cat] = cur;
                if (i >= 0 && b.shares && b.shares[cat]) delete b.shares[cat][v];
                renderBuild();
            };
            window.dtwBwShare = function(inp) {
                var b = S.bw, cat = inp.getAttribute('data-cat'), v = inp.getAttribute('data-v');
                b.shares = b.shares || {}; b.shares[cat] = b.shares[cat] || {};
                b.shares[cat][v] = String(inp.value || '');
                var st = bwShareState(cat), ln = el('dtwBwShareLine-' + cat);
                if (ln) { ln.textContent = st.err || st.line || BW_SHARE_HINT; ln.classList.toggle('err', !!st.err); }
                var line = el('dtwBwLine'); if (line) line.innerHTML = 'The twin: <b>' + esc(bwAudienceLabel()) + '</b>';
                var nb = document.querySelector('#dtwBuildBody .dtw-bw-nav .dtw-btn-primary'); if (nb) nb.disabled = !bwCanNext();
            };
            window.dtwBwField = function(k, v) { S.bw[k] = String(v || ''); var line = el('dtwBwLine'); if (line) line.innerHTML = 'The twin: <b>' + esc(bwAudienceLabel()) + '</b>'; if (S.bw.step === 1) { var nb = document.querySelector('#dtwBuildBody .dtw-bw-nav .dtw-btn-primary'); if (nb) nb.disabled = !bwCanNext(); } };
            window.dtwBwChip = function(btn) { var v = btn.getAttribute('data-v'); S.bw.chip = S.bw.chip === v ? '' : v; document.querySelectorAll('#dtwBuildBody .dtw-chips .dtw-chip').forEach(function(c) { c.classList.toggle('on', c.getAttribute('data-v') === S.bw.chip); }); };
            var bwSearchT = null;
            function bwSearch() {
                var b = S.bw, q = String(b.q || '').trim();
                b.loading = true;
                api('/profiles?q=' + encodeURIComponent(q) + '&limit=30').then(function(d) { if (S.bw !== b || String(b.q || '').trim() !== q) return; b.results = d.profiles || []; b.loading = false; var l = el('dtwBwList'); if (l) l.innerHTML = bwRows(); })
                    .catch(function() { if (S.bw !== b) return; b.results = b.results || []; b.loading = false; var l = el('dtwBwList'); if (l) l.innerHTML = bwRows(); });
            }
            window.dtwBwQuery = function(v) {
                var b = S.bw; b.q = String(v || '');
                if (b.profile_key || b.subject) { b.profile_key = ''; b.profile_name = ''; b.subject = ''; var pk = document.querySelector('#dtwBuildBody .dtw-bw-pick'); if (pk) pk.remove(); }
                var nb = document.querySelector('#dtwBuildBody .dtw-bw-nav .dtw-btn-primary'); if (nb) nb.disabled = true;
                var l = el('dtwBwList'); if (l) l.innerHTML = bwRows();
                clearTimeout(bwSearchT); bwSearchT = setTimeout(bwSearch, 220);
            };
            window.dtwBwPickProfile = function(btn) { var b = S.bw; b.profile_key = btn.getAttribute('data-key'); b.profile_name = btn.getAttribute('data-name'); b.profile_cat = btn.getAttribute('data-cat') || ''; b.subject = ''; renderBuild(); };
            window.dtwBwNewSubject = function() { var b = S.bw; b.subject = String(b.q || '').trim(); b.profile_key = ''; b.profile_name = ''; renderBuild(); };
            window.dtwBwClearPick = function() { var b = S.bw; b.profile_key = ''; b.profile_name = ''; b.subject = ''; renderBuild(); };
            window.dtwBwEnter = function() { var b = S.bw; if (b.profile_key || b.subject) { dtwBwNext(); return; } var q = String(b.q || '').trim(); var exact = (b.results || []).filter(function(r) { return String(r.display_name || '').toLowerCase() === q.toLowerCase(); })[0]; if (exact) { b.profile_key = exact.s3_key; b.profile_name = exact.display_name; b.profile_cat = exact.category || ''; renderBuild(); } else if (q.length >= 2) dtwBwNewSubject(); };
            window.dtwBwSubmit = async function() {
                var b = S.bw; if (b.busy) return;
                if (b.source === 'demographics' && !bwSharesOk()) { b.err = bwSharesErr(); b.step = 1; renderBuild(); return; }
                var purpose = ((b.chip ? b.chip + '. ' : '') + b.purpose.trim()).trim();
                if (b.source === 'list') {
                    var u = b.list || {}; u.name = String(u.name || '').trim();
                    if (!u.preview) { b.err = 'Read the list first.'; b.step = 1; renderBuild(); return; }
                    if (!u.name) { b.err = 'Name the audience first: a plain name like Yellowstone and Landman viewers.'; b.step = 1; renderBuild(); return; }
                    b.busy = true; b.err = ''; renderBuild();
                    try { var dl = await api('/build-from-list', { method: 'POST', body: JSON.stringify({ list_token: u.preview.list_token, name: u.name, window: (u.ws && u.we) ? { start: u.ws, end: u.we } : null, purpose: purpose }) }); }
                    catch (e) { b.busy = false; b.err = e.message; renderBuild(); return; }
                    b.busy = false;
                    b.job = { id: dl.job_id, subject: dl.subject || u.name, stage: 'queued', done: false, key: '', error: '', from_list: true };
                    renderBuild();
                    bwPoll(b.job);
                    return;
                }
                b.busy = true; b.err = ''; renderBuild();
                var body = { source: b.source, purpose: purpose };
                if (b.source === 'demographics') { body.demographics = { markets: bwMarkets(), interests: b.interests.trim() }; Object.keys(b.demo).forEach(function(k) { if ((b.demo[k] || []).length) body.demographics[k] = b.demo[k]; }); var bsh = bwSharesBody(); if (Object.keys(bsh).length) body.demographics.shares = bsh; }
                else { body.subject = b.profile_key ? b.profile_name : b.subject; if (b.profile_key) body.profile_key = b.profile_key; }
                try { var d = await api('/build', { method: 'POST', body: JSON.stringify(body) }); }
                catch (e) { b.busy = false; b.err = e.message; renderBuild(); return; }
                b.busy = false;
                b.job = { id: d.job_id, subject: d.subject || body.subject || bwAudienceLabel(), stage: 'queued', done: false, key: '', error: '' };
                renderBuild();
                bwPoll(b.job);
            };
            function bwPoll(job) {
                (async function loop() {
                    var waited = 0;
                    while (waited < 7200000 && S.bw && S.bw.job === job && !job.done && !job.error) {
                        await new Promise(function(res) { setTimeout(res, 4000); }); waited += 4000;
                        var d = null; try { d = await api('/jobs/' + job.id); } catch (_) { d = null; }
                        if (!d || !d.job) continue;
                        var j = d.job;
                        job.stage = j.status === 'done' ? 'done' : (j.stage || j.status || 'working');
                        var st = el('dtwBwStage'); if (st) st.textContent = job.stage;
                        if (j.status === 'done') { job.done = true; job.key = j.s3_key || ''; job.ready = j.ready_message || ''; renderBuild(); try { await dtwLoadList(true); } catch (_) {} fgToast('Your twin is ready: ' + (j.subject || job.subject) + '.'); return; }
                        if (j.status === 'error' || j.status === 'cancelled') { job.error = j.error || 'The twin did not finish. Nothing more is charged; try again and it runs fresh.'; renderBuild(); return; }
                    }
                })();
            }
            window.dtwBwOpenTwin = function() { var j = S.bw && S.bw.job; dtwBuildOpen(false); if (j && j.key) { dtwLoadList(true).then(function() { dtwOpen(j.key); }); } };
            window.dtwBwAnother = function() { S.bw = bwFresh(); renderBuild(); };
            window.dtwBwRetry = function() { var b = S.bw; b.job = null; b.step = 3; renderBuild(); };
            // ---- Build a twin from a list of content URLs (Jenna 2026-11-04). The list DEFINES the audience: the twin's people are the
            // US people who watched, read or visited that content in the window. The list is read back (count, hosts, rows dropped, the
            // category by the hosts, the price) before anything runs; the confirm charges once (the audience build plus the twin) and the
            // audience is pulled first, then the twin is built from it. Same progress poll as the walkthrough.
            function ulFresh() { var w = ulDefaultWindow(); return { name: '', text: '', file: null, fileName: '', ws: w[0], we: w[1], preview: null, busy: false, err: '', job: null }; }
            function ulIso(d) { return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
            function ulDefaultWindow() { var e = new Date(), s = new Date(e); s.setMonth(s.getMonth() - 12); return [ulIso(s), ulIso(e)]; }
            // Since 2026-11-17 the list is a source card inside Build a twin like the other two (Jenna: "the build from a list should be in a
            // card just like the other ones"): the audience step is the list form, Next reads the list back, the review shows what it
            // read and the price, Build starts the audience pull and the twin. The separate drawer is gone.
            function ulHasList() { var u = (S.bw || {}).list || {}; return !!(u.file || String(u.text || '').trim()); }
            function ulForm() {
                var b = S.bw, u = b.list || (b.list = ulFresh()), p = u.preview;
                var read = p ? '<div class="dtw-bw-pick"><span>Read back: <b>' + fmtN(p.count) + ' URL' + (p.count === 1 ? '' : 's') + '</b>' + (p.hosts && p.hosts.length ? ', ' + esc(p.hosts.slice(0, 4).map(function(h) { return h[0]; }).join(', ')) : '') + (p.dropped ? '; ' + fmtN(p.dropped) + ' row' + (p.dropped === 1 ? '' : 's') + ' with no usable link dropped' : '') + '.</span></div>' : '';
                return '<div class="dtw-bw-q">Which content?</div><div class="dtw-bw-sub">Upload a CSV, TXT or XLSX with one URL per row (a column named url or link is best), or paste the URLs below. The twin is the US people who watched, read or visited that content in the window.</div>'
                    + '<label class="dtw-field">The list</label><input type="file" class="dtw-in" accept=".csv,.tsv,.txt,.md,.xlsx,.xlsm" onchange="dtwUlFile(this)">'
                    + (u.fileName ? '<div class="dtw-bw-pick"><span>File: <b>' + esc(u.fileName) + '</b></span><button type="button" class="dtw-link" onclick="dtwUlClearFile()">Remove</button></div>' : '')
                    + '<label class="dtw-field">Or paste URLs, one per line</label><textarea class="dtw-in" rows="5" placeholder="https://www.netflix.com/title/70155618&#10;https://www.paramountplus.com/shows/landman/&#10;https://www.nytimes.com/2026/10/01/arts/television/ranch-dramas.html" oninput="dtwUlField(\'text\', this.value)">' + esc(u.text) + '</textarea>'
                    + '<div class="dtw-bw-q" style="margin-top:1rem;">Name the audience</div><div class="dtw-bw-sub">A plain name is best.</div>'
                    + '<input class="dtw-in" value="' + esc(u.name) + '" placeholder="Yellowstone and Landman viewers, Our fall campaign readers" oninput="dtwUlField(\'name\', this.value)">'
                    + '<div class="dtw-bw-q" style="margin-top:1rem;">Window</div><div class="dtw-bw-sub">Trailing 12 months unless you change it.</div>'
                    + '<div style="display:flex; gap:10px; flex-wrap:wrap;"><input type="date" class="dtw-in" style="flex:1; min-width:150px;" value="' + esc(u.ws) + '" onchange="dtwUlField(\'ws\', this.value)" aria-label="Window start"><input type="date" class="dtw-in" style="flex:1; min-width:150px;" value="' + esc(u.we) + '" onchange="dtwUlField(\'we\', this.value)" aria-label="Window end"></div>'
                    + read + (b.err ? '<div class="dtw-bw-err">' + esc(b.err) + '</div>' : '')
                    + bwNav(bwCanNext(), b.busy ? 'Reading the list' : (p ? 'Next' : 'Read the list'));
            }
            function ulReview() {
                var b = S.bw, u = b.list || {}, p = u.preview || {}, pr = p.price || {}, k = p.kinds || {}, hosts = p.hosts || [];
                var shape = [];
                if (k.watch) shape.push(k.watch + ' watch page' + (k.watch === 1 ? '' : 's'));
                if (k.listen) shape.push(k.listen + ' listening page' + (k.listen === 1 ? '' : 's'));
                var read = (k.article || 0) + (k.site || 0); if (read) shape.push(read + ' article' + (read === 1 ? '' : 's') + ' or site page' + (read === 1 ? '' : 's'));
                var rows = [['Source', 'A list of content: ' + fmtN(p.count) + ' URL' + (p.count === 1 ? '' : 's') + (shape.length ? ' (' + shape.join(', ') + ')' : '') + (p.dropped ? '; ' + fmtN(p.dropped) + ' row' + (p.dropped === 1 ? '' : 's') + ' with no usable link dropped' : ''), 0],
                            ['Audience', (u.name || 'Not named yet') + ': US people who watched, read or visited this content in the window', 1],
                            ['Hosts', hosts.map(function(h) { return h[0] + ' (' + h[1] + ')'; }).join(', '), 1],
                            ['Window', p.window_words || '', 1],
                            ['Category', (p.category || '') + (p.category_reason ? ' (' + p.category_reason + ')' : ''), null],
                            ['Purpose', (b.chip ? b.chip : '') + (b.purpose.trim() ? (b.chip ? '. ' : '') + b.purpose.trim() : '') || 'Open', 2],
                            ['Seats', 'One archetype representative per archetype in the audience, twelve or more, each sized by the people it stands for (the seats are part of each group)', null]];
                var sum = '<div class="dtw-bw-sum">' + rows.map(function(r) { return '<div class="r"><span class="k">' + r[0] + '</span><span class="v">' + esc(r[1]) + (r[2] === null ? '' : '<button type="button" class="dtw-link" onclick="dtwBwGo(' + r[2] + ')">Change</button>') + '</span></div>'; }).join('') + '</div>';
                var price = '<div class="dtw-bw-price">'
                    + '<div class="r"><span><i>The audience</i>, pulled from your list</span><b>' + esc(pr.profile_label || '') + '</b></div>'
                    + '<div class="r"><span><i>The twin</i>, with its first two runs</span><b>' + esc(pr.twin_label || '') + '</b></div>'
                    + '<div class="r"><span><i>Each further run</i></span><b>' + esc(pr.run_label || '') + '</b></div>'
                    + '<div class="r"><span><i>Prometheus</i>, the build and every question</span><b>Metered</b></div>'
                    + '<div class="r total"><span>Charged now</span><b>' + esc(pr.total_label || '') + '</b></div>'
                    + '<div class="m">The audience is pulled first (usually under an hour), then the twin is built from it. Both land on your account; Prometheus confirms in the chat and by email.</div></div>';
                return '<div class="dtw-bw-q">Ready to build.</div><div class="dtw-bw-sub">Here is the list, read back. Check the lines, then build.</div>' + sum + price
                    + (b.err ? '<div class="dtw-bw-err">' + esc(b.err) + '</div>' : '') + bwNav(!b.busy, b.busy ? 'Starting the build' : 'Build the twin');
            }
            window.dtwUlField = function(k, v) {
                var b = S.bw, u = b.list || (b.list = ulFresh()); u[k] = String(v || '');
                if (k !== 'name' && u.preview) { u.preview = null; }      // the list or the window changed: it is read back again before the review
                if (k === 'text' || k === 'name') {
                    var nb = document.querySelector('#dtwBuildBody .dtw-bw-nav .dtw-btn-primary');
                    if (nb) { nb.disabled = !bwCanNext(); if (k === 'text') nb.textContent = u.preview ? 'Next' : 'Read the list'; }
                } else renderBuild();
            };
            window.dtwUlFile = function(input) { var f = input && input.files && input.files[0]; var u = S.bw.list || (S.bw.list = ulFresh()); u.file = f || null; u.fileName = f ? f.name : ''; u.preview = null; renderBuild(); };
            window.dtwUlClearFile = function() { var u = S.bw.list; u.file = null; u.fileName = ''; u.preview = null; renderBuild(); };
            window.dtwUlPreview = async function() {
                var b = S.bw, u = b.list || {}; if (b.busy || !ulHasList()) return;
                b.busy = true; b.err = ''; renderBuild();
                var fd = new FormData();
                if (u.file) fd.append('file', u.file, u.file.name);
                fd.append('text', u.text || ''); fd.append('name', u.name || ''); fd.append('window_start', u.ws || ''); fd.append('window_end', u.we || '');
                try {
                    var r = await fetch('/api/digital-twins/list-preview', { method: 'POST', credentials: 'same-origin', body: fd });
                    var d = null; try { d = await r.json(); } catch (_) { d = null; }
                    if (!r.ok || !d || d.success === false) throw new Error((d && d.error) || ('Request failed (' + r.status + ')'));
                    u.preview = d;
                } catch (e) { b.err = e.message; }
                b.busy = false;
                if (u.preview && !b.err) dtwBwGo(2); else renderBuild();
            };
            // ---- the track record (Jenna 2026-11-17: the record is not a twin's; it lives in one place for everyone). One ledger for the
            // whole product: every call any room or test made before its outcome. A seat reads its own company's calls and the house's
            // public record (the website carries the house's calls); staff read every company.
            function recUnit(v, unit, kind) {
                if (v == null || v === '') return '';
                if (typeof v === 'string') return v;
                var n = Number(v);
                if (unit === 'people') return fmtN(n) + ' people';
                if (unit === 'points') return (Math.round(n * 10) / 10) + ' points';
                if (unit === 'pct') return (Math.round(n * 10) / 10) + '%';
                return fmtN(n);
            }
            function recSaid(r, which) {
                var dist = r[which + '_distribution'], v = r[which];
                if (dist && dist.length) return dist.slice(0, 4).map(function(d) { return esc(d.label) + ' ' + (Math.round(Number(d.pct) * 10) / 10) + '%'; }).join(', ');
                var s = recUnit(v, r.unit, r.kind);
                if (which === 'said' && r.low != null && r.high != null && typeof v !== 'string') s += ' <span class="dtw-muted">(' + recUnit(r.low, r.unit) + ' to ' + recUnit(r.high, r.unit) + ')</span>';
                return esc(s).replace(/&lt;span class=&quot;dtw-muted&quot;&gt;/g, '<span class="dtw-muted">').replace(/&lt;\/span&gt;/g, '</span>');
            }
            function recMiss(r) {
                if (r.hit === true) return 'Called right';
                if (r.hit === false) return 'Called wrong';
                if (r.tvd != null) return (Math.round(Number(r.tvd) * 10) / 10) + ' points off';
                if (r.rel_error != null) return (Math.round(Number(r.rel_error) * 1000) / 10) + '% off';
                if (r.abs_error != null) return recUnit(r.abs_error, r.unit) + ' off';
                return '';
            }
            function recKind(k) { return { forecast: 'Pilot test', room_count: 'Focus group', scorecard: 'Pilot test', call: 'Call' }[k] || k || ''; }
            function recCap(s) { s = String(s || ''); return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }
            // the open calls carry the day each is due and what settles it; a client adds its own result to its own open calls
            // (Jenna 2026-11-20: the Add your result button); a scored call is never revised
            function recTable(rows, staff, settled) {
                if (!rows.length) return '<p class="dtw-muted">' + (settled ? 'Nothing settled yet.' : 'Nothing waiting.') + '</p>';
                var acts = !settled && rows.some(function(r) { return r.can_add; });
                var head = '<tr><th>Audience</th><th>What was asked</th><th>We said</th>' + (settled ? '<th>What happened</th><th>Miss</th>' : '<th>How firm</th><th>Due</th>') + '<th>Called</th>' + (settled ? '<th>Settled</th>' : '') + (staff ? '<th>Company</th>' : '') + (acts ? '<th></th>' : '') + '</tr>';
                var cols = 6 + (settled ? 1 : 0) + (staff ? 1 : 0) + (acts ? 1 : 0);
                var body = rows.map(function(r) {
                    var tr = '<tr><td>' + esc(r.subject) + '<div class="dtw-muted">' + esc(recKind(r.kind)) + (r.house ? ' &middot; Crosswalk' : '') + '</div></td><td>' + esc(r.what) + '</td><td>' + recSaid(r, 'said') + '</td>'
                        + (settled ? '<td>' + recSaid(r, 'happened') + (r.outcome_class ? '<div class="dtw-muted">' + esc(r.outcome_class) + '</div>' : '') + '</td><td>' + esc(recMiss(r)) + '</td>'
                                   : '<td>' + esc(recCap(r.firmness)) + '</td><td>' + esc(r.due_on || 'Not set yet') + (r.check ? '<div class="dtw-muted">Settled by ' + esc(r.check) + '</div>' : '') + '</td>')
                        + '<td>' + esc(r.called_on) + '</td>' + (settled ? '<td>' + esc(r.settled_on) + '</td>' : '') + (staff ? '<td>' + esc(r.company || '') + (r.public ? ' <span class="dtw-muted">public</span>' : '') + '</td>' : '')
                        + (acts ? '<td>' + (r.can_add ? '<button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwRecAdd(\'' + esc(jsq(r.id)) + '\')">Add your result</button>' : '') + '</td>' : '') + '</tr>';
                    if (!settled && S.recAdd === r.id) tr += '<tr class="dtw-rec-addrow"><td colspan="' + cols + '">' + recAddForm(r) + '</td></tr>';
                    return tr;
                }).join('');
                return '<table class="dtw-table">' + head + body + '</table>';
            }
            function recAddForm(r) {
                var id = esc(jsq(r.id)), how = String(r.scored_as || ''), field;
                if (how === 'winner' && (r.options || []).length) field = '<select id="dtwRecAddV" class="dtw-in">' + r.options.map(function(o) { return '<option value="' + esc(o) + '">' + esc(o) + '</option>'; }).join('') + '</select>';
                else if (how === 'yes_no') field = '<select id="dtwRecAddV" class="dtw-in"><option value="yes">It happened</option><option value="no">It did not happen</option></select>';
                else field = '<input id="dtwRecAddV" class="dtw-in" inputmode="decimal" placeholder="' + (r.unit === 'pct' ? 'The share your results show, like 61.4' : 'The number your results show') + '">';
                return '<div class="dtw-rec-add"><label class="dtw-field">Your result</label>' + field + '<input id="dtwRecAddNote" class="dtw-in" placeholder="A note, if you want one">'
                    + '<div class="dtw-actions"><button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwRecAddSend(\'' + id + '\')">Add your result</button><button type="button" class="dtw-btn" onclick="dtwRecAdd(null)">Cancel</button></div>'
                    + '<div id="dtwRecAddLine" class="dtw-muted">Once it is added the call is scored. A scored call is never revised.</div></div>';
            }
            window.dtwRecAdd = function(id) { S.recAdd = id || null; renderRecord(S.record); };
            window.dtwRecAddSend = async function(id) {
                var r = ((S.record || {}).open || []).filter(function(x) { return x.id === id; })[0] || {};
                var v = ((el('dtwRecAddV') || {}).value || '').trim(), body = { note: ((el('dtwRecAddNote') || {}).value || '').trim() };
                if (r.scored_as === 'winner') body.winner = v; else if (r.scored_as === 'yes_no') body.happened = (v === 'yes'); else body.value = v;
                var line = el('dtwRecAddLine'); if (line) line.textContent = 'Adding your result...';
                try {
                    await api('/ledger/' + encodeURIComponent(id) + '/result', { method: 'POST', body: JSON.stringify(body) });
                    S.recAdd = null;
                    var d = await api('/track-record'); S.record = d; renderRecord(d);
                } catch (e) { if (line) line.textContent = String((e && e.message) || e); }
            };
            // staff: put a question on the record on purpose, before its result (Jenna 2026-11-20: "Only calls we deliberately log
            // before the result"); a Crosswalk call logged here goes on the public record, a client's stays with the client
            function recLogForm(d) {
                var lg = S.recLog || {};
                var twins = (S.runs || []).map(function(r) { return '<option value="' + esc(r.key) + '"' + (r.key === lg.key ? ' selected' : '') + '>' + esc(twinOptionLabel(r)) + '</option>'; }).join('');
                var sess = lg.twin ? (lg.twin.panel_sessions || []).concat(lg.twin.pilot_sessions || []).filter(function(s) { return (s.questions || []).some(function(q) { return q.finished_at; }); }) : [];
                var cur = sess.filter(function(s) { return s.id === lg.sid; })[0] || null;
                var sessOpts = sess.map(function(s) { return '<option value="' + esc(s.id) + '"' + (cur && s.id === cur.id ? ' selected' : '') + '>' + esc(s.title || 'Room') + '</option>'; }).join('');
                var qOpts = cur ? (cur.questions || []).filter(function(q) { return q.finished_at && q.kind !== 'rating' && q.kind !== 'probe'; }).map(function(q) { return '<option value="' + esc(q.id) + '"' + (q.id === lg.qid ? ' selected' : '') + '>' + esc(String(q.q || '').slice(0, 110)) + '</option>'; }).join('') : '';
                var openOpts = ((d || {}).open || []).filter(function(r) { return !r.logged; }).map(function(r) { return '<option value="' + esc(r.id) + '">' + esc(String(r.subject || '') + ': ' + String(r.what || '').slice(0, 80)) + '</option>'; }).join('');
                return '<h4 style="margin-top:1.4rem;">Log a call</h4><div class="dtw-bw-sub">Put a question on the record before its result. A Crosswalk call logged here goes on the public record; a client\'s stays with the client until an admin releases it.</div>'
                    + '<div class="dtw-rec-log">'
                    + '<label class="dtw-field">Twin</label><select class="dtw-in" id="dtwRecLogTwin" onchange="dtwRecLogTwin(this.value)"><option value="">Pick a twin</option>' + twins + '</select>'
                    + '<label class="dtw-field">Room</label><select class="dtw-in" id="dtwRecLogSess" onchange="dtwRecLogSess(this.value)"><option value="">' + (lg.key ? (sess.length ? 'Pick a room' : 'No finished questions on this twin') : 'Pick a twin first') + '</option>' + sessOpts + '</select>'
                    + '<label class="dtw-field">Question</label><select class="dtw-in" id="dtwRecLogQ" onchange="dtwRecLogQ(this.value)"><option value="">' + (cur ? 'Pick a question' : 'Pick a room first') + '</option>' + qOpts + '</select>'
                    + (openOpts ? '<label class="dtw-field">Or a call already waiting</label><select class="dtw-in" id="dtwRecLogId"><option value="">None</option>' + openOpts + '</select>' : '')
                    + '<label class="dtw-field">What is measured</label><input class="dtw-in" id="dtwRecLogWhat" placeholder="Who wins the runoff on December 9" value="' + esc(lg.what || '') + '">'
                    + '<div class="dtw-two"><div><label class="dtw-field">Due</label><input type="date" class="dtw-in" id="dtwRecLogDue"></div><div><label class="dtw-field">The result exists from</label><input type="date" class="dtw-in" id="dtwRecLogKnow"></div></div>'
                    + '<div class="dtw-two"><div><label class="dtw-field">Settled by</label><select class="dtw-in" id="dtwRecLogCheck"><option value="certified">A certified result</option><option value="published">A published result</option><option value="counted">Our own count</option><option value="client">The client\'s own results</option></select></div>'
                    + '<div><label class="dtw-field">Scored as</label><select class="dtw-in" id="dtwRecLogScored"><option value="winner">The winner</option><option value="number">A number</option><option value="yes_no">Yes or no</option><option value="split">The split</option></select></div></div>'
                    + '<div class="dtw-two"><div><label class="dtw-field">What happens</label><select class="dtw-in" id="dtwRecLogEvent"><option value="election">An election</option><option value="premiere">A premiere</option><option value="opening_weekend">An opening weekend</option><option value="behavior">Something people do</option><option value="other">Something else</option></select></div>'
                    + '<div><label class="dtw-field">Our own count of</label><select class="dtw-in" id="dtwRecLogMetric"><option value="">Nothing; another check settles it</option><option value="title_viewers">People who watched the title</option><option value="return_pct">The share who watched again in week two</option></select></div></div>'
                    + '<div class="dtw-two"><div><label class="dtw-field">Title, for our own count</label><input class="dtw-in" id="dtwRecLogTitle"></div><div><label class="dtw-field">Platform</label><input class="dtw-in" id="dtwRecLogPlatform"></div></div>'
                    + '<div class="dtw-two"><div><label class="dtw-field">Window starts</label><input type="date" class="dtw-in" id="dtwRecLogWs"></div><div><label class="dtw-field">Window ends</label><input type="date" class="dtw-in" id="dtwRecLogWe"></div></div>'
                    + '<div class="dtw-actions"><button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwRecLogSend()">Log the call</button></div><div id="dtwRecLogLine" class="dtw-muted"></div></div>';
            }
            window.dtwRecLogTwin = async function(key) {
                S.recLog = { key: key || '', twin: null, sid: '', qid: '', what: '' };
                if (key) { try { var d = await api('/' + key); S.recLog.twin = d.twin || null; } catch (_) {} }
                renderRecord(S.record);
            };
            window.dtwRecLogSess = function(sid) { var lg = S.recLog || {}; lg.sid = sid || ''; lg.qid = ''; S.recLog = lg; renderRecord(S.record); };
            window.dtwRecLogQ = function(qid) {
                var lg = S.recLog || {}; lg.qid = qid || '';
                var sess = lg.twin ? (lg.twin.panel_sessions || []).concat(lg.twin.pilot_sessions || []).filter(function(s) { return s.id === lg.sid; })[0] : null;
                var q = sess ? (sess.questions || []).filter(function(x) { return x.id === qid; })[0] : null;
                var w = el('dtwRecLogWhat'); if (w && q && !w.value) { w.value = String(q.q || ''); lg.what = w.value; }
                S.recLog = lg;
            };
            window.dtwRecLogSend = async function() {
                var lg = S.recLog || {}, v = function(id) { return ((el(id) || {}).value || '').trim(); };
                var body = { twin_key: lg.key || '', session_id: lg.sid || '', question_id: v('dtwRecLogQ') || lg.qid || '', ledger_id: v('dtwRecLogId'), what: v('dtwRecLogWhat'),
                    due_at: v('dtwRecLogDue'), knowable_at: v('dtwRecLogKnow') || v('dtwRecLogDue'), check: v('dtwRecLogCheck'), scored_as: v('dtwRecLogScored'), event: v('dtwRecLogEvent'),
                    metric: v('dtwRecLogMetric'), title: v('dtwRecLogTitle'), platform: v('dtwRecLogPlatform'), window_start: v('dtwRecLogWs'), window_end: v('dtwRecLogWe') };
                var line = el('dtwRecLogLine'); if (line) line.textContent = 'Logging the call...';
                try {
                    var r = await api('/ledger/log', { method: 'POST', body: JSON.stringify(body) });
                    S.recLog = null;
                    var d = await api('/track-record'); S.record = d; renderRecord(d);
                    var l2 = el('dtwRecLogLine'); if (l2) l2.textContent = 'Logged. Due ' + (r.due_on || 'when its day is set') + (r.public ? '; it is on the public record.' : '; it stays with the client until an admin releases it.');
                } catch (e) { if (line) line.textContent = String((e && e.message) || e); }
            };
            function renderRecord(d) {
                var h = el('dtwRecordBody'); if (!h) return;
                if (!d) { h.innerHTML = '<p class="dtw-muted">Reading the record...</p>'; return; }
                if (d.error) { h.innerHTML = '<p class="dtw-muted">The record could not be read: ' + esc(d.error) + '</p>'; return; }
                var sm = d.summary || {}, staff = !!d.staff;
                var tiles = '<div class="dtw-bw-sum">'
                    + '<div class="r"><span class="k">Calls scored</span><span class="v">' + fmtN(sm.calls_scored || 0) + '</span></div>'
                    + (sm.hit_rate != null ? '<div class="r"><span class="k">Called right</span><span class="v">' + Math.round(sm.hit_rate * 100) + '% of the calls</span></div>' : '')
                    + (sm.mean_relative_miss != null ? '<div class="r"><span class="k">Typical miss</span><span class="v">' + (Math.round(sm.mean_relative_miss * 1000) / 10) + '% on a count</span></div>' : '')
                    + '</div>';
                var who = staff ? (d.company ? 'The calls of ' + esc(d.company) + ' and the public record.' : 'Every company\'s calls and the public record.') : 'Your company\'s calls, and the public record. Another company never sees yours.';
                h.innerHTML = '<div class="dtw-bw-sub">' + esc(d.line || '') + ' ' + who + '</div>' + tiles
                    + '<h4 style="margin-top:1rem;">Settled</h4><div class="dtw-bw-sub">What we said, then what happened.</div>' + recTable(d.rows || [], staff, true)
                    + '<h4 style="margin-top:1.2rem;">Open</h4><div class="dtw-bw-sub">Calls waiting on their result, with the day each is due and what settles it. Each is dated by its hash; nothing is added after the fact.</div>' + recTable(d.open || [], staff, false)
                    + (staff ? recLogForm(d) : '')
                    + '<div class="dtw-bw-sub" style="margin-top:1.2rem;">The public record is on the website: <a class="dtw-link" href="' + esc(d.public_url || 'https://www.crosswalknyc.com/twins-track-record.html') + '" target="_blank" rel="noopener">crosswalknyc.com/twins-track-record</a>.</div>';
            }
            window.dtwRecordOpen = function(on) {
                if (on) { try { dtwBuildOpen(false); dtwNewRoomOpen(false); dtwRoomsOpen(false); dtwHowtoOpen(false); } catch (_) {} }
                drawer('dtwRecordDrawer', 'dtwRecordScrim', on, function() {
                    renderRecord(null);
                    api('/track-record').then(function(d) { S.record = d; renderRecord(d); }).catch(function(e) { renderRecord({ error: String((e && e.message) || e) }); });
                });
            };
            // the New room drawer: the line quotes the group at the company's plan rate for the tier picked (Jenna 2026-11-05: priced per
            // group, the seats are part of it); the seats themselves are the kinds of people in the audience (2026-11-17)
            window.dtwPnSeatsNote = function() {
                // the seats are the kinds of people in the audience, twelve or more (Jenna 2026-11-17); nothing to set
                var h = el('dtwPnSeats'); if (!h) return;
                var sel = el('dtwPnTier'); if (sel && sel.options.length && groupCard()) { var cur = sel.value; sel.innerHTML = groupTierOptions(cur); }
                var tier = (sel && sel.value) || groupTierDefault();
                var k = ((S.twin || {}).archetypes || {}).k;
                h.innerHTML = '<b>' + (k ? k + ' seats' : 'One seat per archetype') + '</b>, one archetype representative for each archetype in the audience, each sized by the people it stands for; the seats are part of the group. ' + esc(groupQuote(tier));
            };
            window.dtwAttach = function(kind) {
                var nm = twinName() || '[twin]';
                var line = { script: 'Read this script in Greenlight on the ' + nm + ' twin', concept: 'Test this concept in Greenlight on the ' + nm + ' twin with the attached one-sheet', launch: 'Run Launch Lab on the ' + nm + ' twin with the attached budget sheet', spend: 'Activate the attached plan in the Spend Engine on the ' + nm + ' twin, approve by chip', panel: 'Open a focus group with the ' + nm + ' twin and ask about the attached file: ' }[kind] || '';
                chatFill(line);
                try { var f = document.getElementById('synthChatFileInput'); if (f) f.click(); } catch (_) {}
            };
            window.dtwLoadList = async function(force) {
                if (S.loaded && !force) return S.runs;
                // Company scope (2026-11-05): the list is the seat's company's library. Crosswalk staff see every company and can narrow to one.
                try { var d = await api('/list' + (S.companyFilter ? '?company=' + encodeURIComponent(S.companyFilter) : '')); S.runs = d.runs || []; S.pricing = d.pricing || []; S.card = d.group_card || null; S.loaded = true;
                      S.isStaff = !!d.is_staff; S.canAssign = !!d.can_assign; S.companies = d.companies || []; S.company = d.company || ''; }
                catch (e) { if (el('dtwLibrary')) el('dtwLibrary').innerHTML = '<div class="dtw-card dtw-muted" style="grid-column:1/-1">' + esc(e.message) + '</div>'; return []; }
                renderLibrary(); fillTwinSelects(); fillCompanyFilter(); renderPrices(); syncInputCards();
                return S.runs;
            };
            function companyOptions(selected) {
                return (S.companies || []).map(function(c) { return '<option value="' + esc(c.company) + '"' + (c.company === selected ? ' selected' : '') + '>' + esc(c.label || c.company) + (c.twins ? ' (' + c.twins + ')' : '') + '</option>'; }).join('');
            }
            function fillCompanyFilter() {
                var f = el('dtwCompanyFilter'); if (!f) return;
                if (!S.isStaff) { f.hidden = true; return; }
                f.innerHTML = '<option value="">All companies</option>' + companyOptions(S.companyFilter || '');
                f.value = S.companyFilter || '';
                f.hidden = false;
            }
            function twinCompanyOf(key) { var r = (S.runs || []).filter(function(x) { return x.key === key; })[0]; return (r && r.company) || ''; }
            // The super admin's assign control: the twin, with its tests, rooms and saved setups, moves to the company picked.
            function assignControl(key) {
                if (!S.canAssign) return '';
                var k = esc(jsq(key)), cur = twinCompanyOf(key);
                return '<span class="dtw-assign" title="Assign this twin to a company. Its tests, rooms and saved setups go with it."><select id="dtwAssignCompany" class="dtw-in dtw-btn-sm" aria-label="Assign to company"><option value="">Assign to company' + (cur ? ' (now ' + esc(cur) + ')' : '') + '</option>' + companyOptions('') + '</select><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwAssignTwin(\'' + k + '\')">Assign</button><span id="dtwAssignNote" class="dtw-muted"></span></span>';
            }
            window.dtwAssignTwin = async function(key) {
                var sel = el('dtwAssignCompany'), note = el('dtwAssignNote'), co = sel ? sel.value : '';
                if (!co) { if (note) note.textContent = 'Pick a company first.'; return; }
                var tok = dtwBusy(dtwBtn('.dtw-assign .dtw-btn'), 'Assigning', note); if (tok === false) return;
                dtwStage(tok, 'Assigning');
                try { var d = await api('/' + key + '/company', { method: 'POST', body: JSON.stringify({ company: co }) }); dtwIdle(tok); if (note) note.textContent = d.message || 'Assigned.'; S.loaded = false; await dtwLoadList(true); if (S.key === key) { await dtwOpen(key, null, null, null, S.tab); } }
                catch (e) { dtwIdle(tok, e); }
            };
            function renderPrices() {
                var p = S.pricing || [];
                var find = function(k) { var m = p.filter(function(x) { return x.toLowerCase().indexOf(k) === 0; })[0]; return m ? m.split(' - ').slice(1).join(' - ') : ''; };
                if (el('dtwGlScriptPrice')) el('dtwGlScriptPrice').textContent = find('greenlight script');
                if (el('dtwGlConceptPrice')) el('dtwGlConceptPrice').textContent = find('greenlight concept');
                if (el('dtwLaPrice')) el('dtwLaPrice').textContent = find('each further');
                if (el('dtwSpPrice')) el('dtwSpPrice').textContent = find('spend engine');
                if (el('dtwPnPrice')) el('dtwPnPrice').textContent = groupQuote((el('dtwPnTier') || {}).value) || find('individual groups');
            }
            function fillTwinSelects() {
                var sw = el('dtwTwinSwitch');
                if (sw) { sw.innerHTML = (S.runs.length ? '<option value="">Switch twin</option>' : '<option value="">No twins yet</option>') + S.runs.map(function(r) { return '<option value="' + esc(r.key) + '"' + (r.key === S.key ? ' selected' : '') + '>' + esc(twinOptionLabel(r)) + '</option>'; }).join(''); if (S.key) sw.value = S.key; }
                ['dtwPnTwin2'].forEach(function(id) {
                    var s = el(id); if (!s) return; var cur = s.value; var first = s.options[0] ? s.options[0].outerHTML : '';
                    s.innerHTML = first + S.runs.filter(function(r) { return r.key !== S.key; }).map(function(r) { return '<option value="' + esc(r.key) + '">' + esc(twinOptionLabel(r)) + '</option>'; }).join('');
                    if (cur) s.value = cur;
                });
            }
            // Every lab card carries the same line: which twin it runs on. No twin, no run.
            function syncInputCards() {
                var nm = twinName();
                document.querySelectorAll('#digitalTwinsIQView [data-dtw-on]').forEach(function(n) { n.innerHTML = nm ? 'Runs on <b>' + esc(nm) + '</b>. <button type="button" class="dtw-link" onclick="dtwTab(\'twins\')">Switch twin</button>' : 'No twin selected. <button type="button" class="dtw-link" onclick="dtwTab(\'twins\')">Pick one on the Twins tab</button> or build one.'; });
                document.querySelectorAll('#digitalTwinsIQView [data-dtw-run]').forEach(function(b) { b.disabled = !S.key; });
            }
            function renderSpine() {
                var h = el('dtwSpineTwin'); if (!h) return;
                var t = S.twin, m = t ? (t.meta || {}) : null;
                var sw = el('dtwTwinSwitch'); if (sw) sw.value = S.key || '';
                if (!m) { h.innerHTML = '<div class="dtw-spine-name">No twin selected</div><div class="dtw-spine-facts">' + (S.runs.length ? 'Pick a twin below, or build one.' : 'Build the first twin with the button on the right; it walks you through it.') + '</div>'; return; }
                var facts = ['<span><b>' + fmtN(m.size_us) + '</b> US people</span>', '<span>' + esc(fmtDate(m.window_start)) + ' to ' + esc(fmtDate(m.window_end)) + '</span>'];
                facts.push('<span><b>' + (t.runs || []).length + '</b> runs, <b>' + (t.panel_sessions || []).length + '</b> rooms, <b>' + (((t.labs || {}).spend) || []).length + '</b> plans</span>');
                h.innerHTML = '<div class="dtw-spine-name">' + esc(m.name || m.subject) + '</div><div class="dtw-spine-facts">' + facts.join('') + '</div>';
            }
            // same-name twins read apart (Jenna 2026-11-20, keep both Paramount+ twins): who built each and when, only when
            // another visible twin shares the name; the builder's display name comes from the /list route (built_by)
            function twinBuiltLine(r, withSource) {
                var when = r.created_at ? fmtDay(r.created_at) : '', by = r.built_by || r.created_by || '', src = '';
                if (withSource) {
                    var s = String(r.source || '');
                    if (s === 'profile') src = r.profile_name ? ' from the ' + r.profile_name + ' profile' : ' from a profile';
                    else if (s === 'demographics') src = ' from a shaped group';
                    else if (s === 'urls' || s === 'url_list' || s === 'links') src = ' from a list of links';
                }
                return 'Built' + (when ? ' ' + when : '') + src + (by ? ' by ' + by : '');
            }
            function twinNameShared(r) {
                if (S._dupRuns !== S.runs || !S._dupNames) {
                    var seen = {};
                    (S.runs || []).forEach(function(x) { var k = String(x.name || x.subject || '').trim().toLowerCase(); if (k) seen[k] = (seen[k] || 0) + 1; });
                    S._dupRuns = S.runs; S._dupNames = seen;
                }
                var key = String((r || {}).name || (r || {}).subject || '').trim().toLowerCase();
                return !!(key && S._dupNames[key] > 1);
            }
            function twinQualifier(r) { return twinNameShared(r) ? twinBuiltLine(r) : ''; }
            function twinOptionLabel(r) { var q = twinQualifier(r); return String(r.name || r.subject || r.key || '') + (q ? ' (' + q + ')' : ''); }
            function twinKindLabel(r) {
                var k = String((r && r.subject_kind) || '').toLowerCase();
                var f = String((r && r.format) || '').toLowerCase();
                if (k === 'talent' || f === 'creator') return 'Talent';
                if (k === 'brand' || f === 'brand') return 'Brand';
                if (k === 'idea') return 'Idea';
                if (f === 'series') return 'Series';
                if (f === 'film') return 'Film';
                if (f === 'book') return 'Book';
                if (f === 'game') return 'Game';
                if (f === 'podcast') return 'Podcast';
                if (k === 'ip') return 'Series';
                return 'Audience';
            }
            function twinPrice() {
                var line = (S.pricing || []).filter(function(x) { return /^Digital Twin/i.test(x); })[0] || '';
                var m = /\$[0-9,.]+/.exec(line);
                return m ? m[0] : '$750';
            }
            var SEP_TXT = ' \u00b7 ';
            // Ask: Predictive Analytics on this twin, at the question step; Room: a new focus group room on it; Pilot: the start form
            window.dtwTwinAsk = async function(key) {
                S.pa = paFresh(); S.pa.key = key; S.pa.picked = true; S.pa.step = 1;
                if (S.key !== key) { try { await dtwOpen(key, null, null, null, 'predict'); } catch (_) {} if (S.pa) { S.pa.key = key; S.pa.picked = true; S.pa.step = 1; renderPredict(); } }
                else dtwTab('predict');
            };
            window.dtwTwinRoom = async function(key) {
                if (S.key !== key) { try { await dtwOpen(key, null, null, null, 'panel'); } catch (_) {} }
                else dtwTab('panel');
                try { dtwNewRoomOpen(true); } catch (_) {}
            };
            window.dtwTwinPilot = async function(key) {
                S.pilot = null; S.pilotMedia = null; S.pilotCompare = null;
                if (S.key !== key) { try { await dtwOpen(key, null, null, null, 'pilot'); } catch (_) {} }
                else { try { renderPilotConsole(); } catch (_) {} dtwTab('pilot'); }
                setTimeout(function() { var t = el('dtwPiTitle'); if (t) { try { t.focus(); } catch (_) {} } }, 80);
            };
            function coverActivity(r) {
                var bits = [];
                bits.push((r.runs || 0) + ((r.runs === 1) ? ' run' : ' runs'));
                if (r.panel_sessions) bits.push(r.panel_sessions + ((r.panel_sessions === 1) ? ' room' : ' rooms'));
                if (r.spend_plans) bits.push(r.spend_plans + ((r.spend_plans === 1) ? ' plan' : ' plans'));
                return bits.join(' · ');
            }
            function twinDelId(key) { return String(key || '').replace(/[^A-Za-z0-9]+/g, '_'); }
            function twinDeleteChrome(key, name) {
                var k = esc(jsq(key)), id = 'dtwTwinDel-' + twinDelId(key);
                return '<button type="button" class="dtw-x" title="Delete this twin" aria-label="Delete this twin" onclick="event.stopPropagation(); dtwTwinDeleteAsk(\'' + k + '\')">&#215;</button>'
                    + '<div class="dtw-confirm" id="' + id + '" data-twin-del="' + esc(key) + '" style="display:none;" onclick="event.stopPropagation()"><span>Delete ' + esc(name || 'this twin') + '? The twin and its rooms leave the library. You can undo for 30 days.</span>'
                    + '<button type="button" class="dtw-btn dtw-btn-sm dtw-btn-primary" onclick="event.stopPropagation(); dtwTwinDelete(\'' + k + '\')">Delete</button>'
                    + '<button type="button" class="dtw-btn dtw-btn-sm" onclick="event.stopPropagation(); dtwTwinDeleteAsk(\'' + k + '\', false)">Keep</button></div>';
            }
            function twinPageConfirm(key, name) {
                var k = esc(jsq(key)), id = 'dtwTwinDelP-' + twinDelId(key);
                return '<div class="dtw-confirm" id="' + id + '" style="display:none;"><span>Delete ' + esc(name || 'this twin') + '? The twin and its rooms leave the library. You can undo for 30 days.</span>'
                    + '<button type="button" class="dtw-btn dtw-btn-sm dtw-btn-primary" onclick="dtwTwinDelete(\'' + k + '\')">Delete</button>'
                    + '<button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwTwinDeleteAsk(\'' + k + '\', false, \'dtwTwinDelP-\')">Keep</button></div>';
            }
            window.dtwCoverOpen = function(ev, key) {
                var t = ev && ev.target;
                if (t && t.closest && t.closest('.dtw-x, .dtw-confirm')) return;
                var art = ev && ev.currentTarget;
                if (art && art.classList && art.classList.contains('confirming')) return;
                dtwOpen(key);
            };
            window.dtwTwinDeleteAsk = function(key, on, prefix) {
                var id = (prefix || 'dtwTwinDel-') + twinDelId(key);
                var c = el(id); if (!c) return;
                var show = (on == null) ? c.style.display === 'none' : !!on;
                document.querySelectorAll('#dtwLibrary .dtw-confirm, #dtwTwinPage .dtw-confirm').forEach(function(x) { x.style.display = 'none'; });
                document.querySelectorAll('#dtwLibrary .dtw-cover.confirming, #dtwLibrary .dtw-entry.confirming').forEach(function(x) { x.classList.remove('confirming'); });
                c.style.display = show ? 'flex' : 'none';
                var art = c.closest('.dtw-cover, .dtw-entry');
                if (art) art.classList.toggle('confirming', show);
            };
            window.dtwTwinDelete = async function(key) {
                if (!key) return;
                var cs = [el('dtwTwinDel-' + twinDelId(key)), el('dtwTwinDelP-' + twinDelId(key))].filter(Boolean);
                cs.forEach(function(x) { x.innerHTML = '<span>Deleting...</span>'; });
                var d = null;
                try {
                    try { d = await api('/' + key, { method: 'DELETE' }); }
                    catch (e1) { if (/\((404|405)\)$/.test(e1.message)) d = await api('/' + key + '/delete', { method: 'POST', body: '{}' }); else throw e1; }
                } catch (e) {
                    cs.forEach(function(x) { x.innerHTML = '<span>' + esc(e.message) + '</span><button type="button" class="dtw-btn dtw-btn-sm" onclick="event.stopPropagation(); dtwTwinDeleteAsk(\'' + esc(jsq(key)) + '\', false)">Keep</button>'; });
                    return;
                }
                S.runs = (S.runs || []).filter(function(r) { return r.key !== key; });
                if (S.key === key) {
                    S.twin = null; S.key = ''; S.summary = null; S.openRun = null; S.session = null; S.plan = null; S.panelKind = '';
                    clearPortraitTimers(); try { dtwSeatOpen(null); } catch (_) {}
                    try { remember(); } catch (_) {}
                    fillTwinSelects(); renderSpine(); renderTwin(); syncInputCards();
                    dtwTab('twins');
                }
                renderLibrary(); fillTwinSelects();
                fgToast((d && d.message) || 'Twin deleted.');
                try { dtwLoadList(true); } catch (_) {}
            };
            window.dtwLibFilter = function(kind) { S.libKind = kind || 'all'; renderLibrary(); };
            window.dtwLibQuery = function(q) { S.libQ = String(q || ''); renderLibrary(); };
            // ---- the Twins library (Jenna 2026-11-19: 'make it more elevated design. I like editorial style'; 'LOVE IT. do it'). Every twin
            // is an entry with the same six things on it: the kinds as a bar, Learn more, Past work, Open a room, Start a pilot test, Start
            // Predictive Analytics. One entry open at a time: Learn more opens on who is in the twin, Past work on everything done with it.
            function renderLibrary() {
                var h = el('dtwLibrary'); if (!h) return;
                var n = S.runs.length;
                var c = el('dtwLibCount'); if (c) c.textContent = n ? SEP_TXT + n : '';
                var pr = el('dtwLibPrice'); if (pr) pr.textContent = twinPrice();
                var chips = el('dtwLibChips'); if (chips) { chips.innerHTML = ''; chips.style.display = 'none'; }
                var search = el('dtwLibSearch');
                if (search) { var lab = search.closest('.dtw-lib-find'); if (lab) lab.style.display = n ? '' : 'none'; if (S.libQ && search.value !== S.libQ) search.value = S.libQ; }
                if (S.libOpen && !S.runs.some(function(r) { return r.key === S.libOpen.key; })) S.libOpen = null;
                if (!n) {
                    h.innerHTML = '<div class="dtw-lib-empty"><p class="stand">No twins yet. A twin is an audience you can ask: who is in it, what it would pay, which cut wins, whether a move lands. Build the first one from a profile we hold, a group you shape, or a list of links.</p><button type="button" class="dtw-lb go pa" onclick="dtwBuildTwin()">Build the first twin<i>&rarr;</i></button></div>';
                    return;
                }
                var q = String(S.libQ || '').toLowerCase().trim();
                var shown = S.runs.filter(function(r) {
                    if (S.libKind && S.libKind !== 'all' && twinKindLabel(r) !== S.libKind) return false;
                    if (!q) return true;
                    return [r.name, r.subject, r.built_by, twinQualifier(r)].join(' ').toLowerCase().indexOf(q) >= 0;
                });
                if (!shown.length) { h.innerHTML = '<div class="dtw-lib-empty"><p class="stand">No twin matches that. Try a shorter word, or build the twin you are looking for.</p></div>'; return; }
                var out = '<div class="dtw-lib-cols"><span class="dtw-cap">&#8470;</span><span class="dtw-cap">The archetypes</span><span class="r"><span class="dtw-cap">The twin</span><span class="dtw-cap">Size' + SEP_TXT + 'archetypes' + SEP_TXT + 'last used</span></span></div>';
                shown.forEach(function(r, i) { out += libEntry(r, i + 1); if (S.libOpen && S.libOpen.key === r.key) out += libPanel(r); });
                h.innerHTML = out + '<div class="dtw-lib-end"></div>';
            }
            function pad2(i) { return (i < 10 ? '0' : '') + i; }
            function lcFirst(s) { s = String(s || ''); return s.charAt(0).toLowerCase() + s.slice(1); }
            function ageWords(l) { l = String(l || ''); var m = /^(\d+)\s*-\s*(\d+)$/.exec(l); if (m) return m[1] + ' to ' + m[2]; return l.replace(/ or Older$/i, ' or older').replace(/ and Under$/i, ' and under'); }
            function libPastCount(r) { return Number(r.panel_sessions || 0) + Number(r.pilot_sessions || 0) + Number(r.runs || 0); }
            function libKindClass(r) { var k = String(twinKindLabel(r) || '').toLowerCase(); return k === 'talent' ? 'talent' : (k === 'brand' ? 'brand' : (k === 'series' || k === 'film' || k === 'book' ? 'content' : 'group')); }
            // the kinds as one bar: a segment per kind sized by its share, the biggest named under it
            function libKindsBar(r) {
                var sh = (r.shares || []).map(Number).filter(function(v) { return v > 0; }), k = Number(r.kinds || sh.length || 0), big = r.biggest || null;
                var segs = sh.length ? sh.map(function(v, i) { return '<span style="flex:' + Math.max(0.25, v).toFixed(2) + ';background:' + (i < 10 ? hueFor(i) : 'var(--dtw-track)') + '"></span>'; }).join('') : '<span style="flex:1;background:var(--dtw-track)"></span>';
                var line = k ? ('<b>' + k + ' archetypes.</b>' + (big && big.label ? ' The biggest: ' + esc(big.label) + ', ' + pct(big.share_pct, 1) : '')) : 'The archetypes fill in when the twin is opened.';
                return '<div class="kinds"><div class="bar">' + segs + '</div><div class="line">' + line + '</div></div>';
            }
            function libEntry(r, i) {
                var key = r.key, kn = esc(jsq(key)), nm = r.name || r.subject || 'this twin';
                var open = (S.libOpen && S.libOpen.key === key) ? S.libOpen.kind : '', cur = key === S.key, last = r.last_at || r.created_at || '';
                return '<article class="dtw-entry' + (open ? ' open' : '') + (cur ? ' current' : '') + '" data-key="' + esc(key) + '">'
                    + '<span class="no">' + pad2(i) + '</span>' + libKindsBar(r)
                    + '<div class="main"><div class="name-row"><button type="button" class="name" title="See the full twin" onclick="dtwLibSeeTwin(\'' + kn + '\')">' + esc(nm) + '</button>'
                    + '<span class="kind ' + libKindClass(r) + '">' + esc(twinKindLabel(r) || 'Twin') + '</span>' + (cur ? '<span class="kind cur">Open</span>' : '') + (twinQualifier(r) ? '<span class="built">' + esc(twinQualifier(r)) + '</span>' : '')
                    + '<span class="facts">' + esc(human(r.size_us)) + ' people' + SEP_TXT + (r.kinds ? r.kinds + ' archetypes' + SEP_TXT : '') + (last ? 'last used ' + esc(fmtDay(last)) : 'new') + '</span></div>'
                    + '<div class="acts">'
                    + '<button type="button" class="dtw-lb look' + (open === 'learn' ? ' on' : '') + '" aria-expanded="' + (open === 'learn') + '" onclick="dtwLibToggle(\'' + kn + '\', \'learn\')">Learn more<i>&#9660;</i></button>'
                    + '<button type="button" class="dtw-lb look' + (open === 'past' ? ' on' : '') + '" aria-expanded="' + (open === 'past') + '" onclick="dtwLibToggle(\'' + kn + '\', \'past\')">Past work<em>' + libPastCount(r) + '</em><i>&#9660;</i></button>'
                    + '<span class="sep"></span>'
                    + '<button type="button" class="dtw-lb go room" onclick="dtwTwinRoom(\'' + kn + '\')">Open a room<i>&rarr;</i></button>'
                    + '<button type="button" class="dtw-lb go pilot" onclick="dtwTwinPilot(\'' + kn + '\')">Start a pilot test<i>&rarr;</i></button>'
                    + '<button type="button" class="dtw-lb go pa" onclick="dtwTwinAsk(\'' + kn + '\')">Start Predictive Analytics<i>&rarr;</i></button>'
                    + '</div></div>' + twinDeleteChrome(key, nm) + '</article>';
            }
            // the twin document behind an open entry: the page's open twin when it is the one, else fetched once and kept
            function libDoc(key) { if (S.key === key && S.twin) return S.twin; var d = (S.libDocs || {})[key]; return d && !d._error ? d : null; }
            function libLoadDoc(key) {
                S.libDocs = S.libDocs || {}; S.libLoading = S.libLoading || {};
                if (S.libLoading[key]) return;
                S.libLoading[key] = true;
                api('/' + key).then(function(d) { S.libDocs[key] = (d && d.twin) || { _error: true }; }, function() { S.libDocs[key] = { _error: true }; })
                    .then(function() { delete S.libLoading[key]; if (S.libOpen && S.libOpen.key === key) renderLibrary(); });
            }
            window.dtwLibToggle = function(key, kind) {
                if (S.libOpen && S.libOpen.key === key && S.libOpen.kind === kind) S.libOpen = null;
                else { S.libOpen = { key: key, kind: kind, item: null }; if (S.libDocs && S.libDocs[key] && S.libDocs[key]._error) delete S.libDocs[key]; }
                renderLibrary();
                if (S.libOpen && !libDoc(key)) libLoadDoc(key);
            };
            function libPanel(r) {
                var key = r.key, kind = S.libOpen.kind, doc = libDoc(key), err = !!(S.libDocs && S.libDocs[key] && S.libDocs[key]._error);
                var inner = doc ? (kind === 'learn' ? libLearn(r, doc) : libPast(r, doc)) : '<div class="dtw-lib-wait">' + (err ? 'This twin could not be opened right now.' : 'Opening the twin...') + '</div>';
                return '<div class="dtw-lib-panel ' + kind + '"><span class="side">' + (kind === 'learn' ? 'About this twin' : 'Past work') + '</span><div class="inner">' + inner + '</div></div>';
            }
            // Learn more: a standfirst, the kinds with the person who stands for each, who they are as large numerals
            function libLearn(r, doc) {
                var key = r.key, kn = esc(jsq(key)), m = doc.meta || {}, arch = doc.archetypes || {}, k = Number(arch.k || (arch.groups || []).length || 0);
                var tl = ((doc.the_read || {}).tldr || []), stand = tl.length ? tl.slice(0, 3).join(' ') : ((doc.identity || {}).read || '');
                var win = (m.window_start && m.window_end) ? fmtDay(m.window_start) + ' to ' + fmtDay(m.window_end) : '';
                var built = twinBuiltLine({ created_at: m.created_at || r.created_at, built_by: r.built_by || m.created_by, source: m.source || r.source, profile_name: m.profile_name }, true);
                var out = '<div class="dtw-lib-learn"><div class="top"><div class="col">' + (stand ? '<p class="stand">' + esc(stand) + '</p>' : '') + '<p class="built">' + esc(built) + '.</p>'
                    + '<div class="dtw-lib-links first"><button type="button" class="dtw-lb link" onclick="dtwLibSeeTwin(\'' + kn + '\')">See the full twin<i>&rarr;</i></button>'
                    + '<a class="dtw-lb link" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/export.csv">CSV<i>&rarr;</i></a>'
                    + '<a class="dtw-lb link" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/export.pdf">PDF<i>&rarr;</i></a>'
                    + '<button type="button" class="dtw-lb link quiet" onclick="dtwTwinDeleteAsk(\'' + kn + '\', true)">Delete this twin</button></div></div>'
                    + '<div class="col"><div class="dtw-lib-sec"><span class="dtw-cap">Who they are</span><span class="n">' + fmtN(m.size_us || r.size_us) + ' ' + esc(arch.noun || 'people') + (win ? SEP_TXT + esc(win) : '') + '</span></div>' + libStats(doc) + '</div></div>';
                // the whole Who makes up this audience section, as it stood on the twin page (Jenna 2026-11-19: 'this should just fall in the dropdown')
                var kinds = kindsSection(doc);
                out += kinds ? '<div class="kinds">' + kinds + '</div>' : '<div class="dtw-lib-sec"><span class="dtw-cap">Who makes up this audience</span></div><div class="dtw-lib-wait">The archetypes fill in the first time the twin is opened.</div>';
                return out + '</div>';
            }
            function libStats(doc) {
                var dm = ((doc.identity || {}).demographics) || {}, out = [];
                var top = function(cat) { var rows = (dm[cat] || []).slice().sort(function(a, b) { return Number(b.pct) - Number(a.pct); }); return rows[0] || null; };
                var find = function(cat, re) { return (dm[cat] || []).filter(function(x) { return re.test(String(x.label || '')); })[0] || null; };
                var f = find('gender', /^female$/i), mm = find('gender', /^male$/i);
                if (f && mm) { var w = Number(f.pct) >= Number(mm.pct); out.push([pct(w ? f.pct : mm.pct, 1), w ? 'women' : 'men', pct(w ? mm.pct : f.pct, 1) + (w ? ' men' : ' women')]); }
                var a = top('age'); if (a) out.push([esc(ageWords(a.label)), 'the biggest age', pct(a.pct, 1) + ' of the twin']);
                var hi = (dm.income || []).filter(function(x) { return /\$(100|150|250),000/.test(String(x.label || '')); }).reduce(function(s, x) { return s + Number(x.pct || 0); }, 0);
                if (hi > 0) out.push([pct(hi, 1), 'households at $100K and up', '']);
                var kids = find('parental', /^has children$/i); if (kids) out.push([pct(kids.pct, 1), 'with kids at home', '']);
                var lc = doc.lifecycle || {};
                if (lc.habit_pct != null) out.push([pct(lc.habit_pct, 1), 'in the habit', 'new ' + pct(Number(lc.discovering_pct || 0) + Number(lc.trying_pct || 0), 1) + SEP_TXT + 'lapsing ' + pct(lc.lapsing_pct || 0, 1) + SEP_TXT + 'returning ' + pct(lc.returning_pct || 0, 1)]);
                var tr = Object.keys(doc.traits || {}).map(function(k) { return doc.traits[k]; }).filter(function(v) { return v && v.value != null; }).sort(function(x, y) { return Number(y.value) - Number(x.value); });
                if (tr.length) out.push([String(Math.round(Number(tr[0].value))), esc(lcFirst(tr[0].label || '')) + ', the top dial', tr.slice(1, 3).map(function(v) { return esc(lcFirst(v.label || '')) + ' ' + Math.round(Number(v.value)); }).join(SEP_TXT)]);
                return out.map(function(s) { return '<div class="dtw-lib-stat"><span class="v">' + s[0] + '</span><span><span class="l">' + s[1] + '</span>' + (s[2] ? '<span class="s">' + s[2] + '</span>' : '') + '</span></div>'; }).join('');
            }
            // Past work: everything done with the twin, newest first, each a way back in; a line for what has not been started
            function libTypeColor(t) { return t === 'pa' ? 'var(--dtw-accent)' : t === 'room' ? 'var(--dtw-lb-room)' : t === 'pilot' ? 'var(--dtw-lb-pilot)' : 'var(--dtw-lb-run)'; }
            function libDate(iso) { var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || '')); if (!m) return '<span class="d"></span>'; var y = new Date().getFullYear(); return '<span class="d">' + parseInt(m[3], 10) + '<small>' + MONTHS[parseInt(m[2], 10) - 1] + (parseInt(m[1], 10) !== y ? ' ' + m[1] : '') + '</small></span>'; }
            function libEmptyItem(label, line, act) { return '<div class="dtw-lib-item empty" style="--c:var(--dtw-track)"><span class="d"></span><span class="ty">' + label + '</span><div class="b"><div class="t">' + line + '</div></div>' + act + '</div>'; }
            function libPast(r, doc) {
                var key = r.key, kn = esc(jsq(key)), items = [];
                var first = function(s) { s = String(s || '').replace(/\s+/g, ' ').trim(); var m = /^(.+?[.!?])(\s|$)/.exec(s); return m ? m[1] : s; };
                var short = function(s) { s = String(s || ''); return s.length > 32 ? s.slice(0, 30).replace(/\s+\S*$/, '') + '\u2026' : s; };
                var openIt = (S.libOpen && S.libOpen.key === key && S.libOpen.kind === 'past' && S.libOpen.item) || null;
                var isOpen = function(type, id) { return !!(openIt && openIt.type === type && String(openIt.id) === String(id)); };
                // the way in: the item opens right under its line (Jenna 2026-11-20); the same button closes it
                var inline = function(type, id, label) {
                    var on = isOpen(type, id);
                    return '<button type="button" class="dtw-lb link' + (on ? ' on' : '') + '" aria-expanded="' + (on ? 'true' : 'false') + '" onclick="dtwLibOpenItem(\'' + kn + '\', \'' + type + '\', \'' + esc(jsq(id)) + '\')">' + (on ? 'Close<i class="c">&#9650;</i>' : label + '<i class="c">&#9660;</i>') + '</button>';
                };
                (doc.panel_sessions || []).forEach(function(s) {
                    var when = s.updated_at || s.created_at || '', sid = esc(jsq(s.id));
                    if (s.guided) {
                        var g = s.guided, o = g.outcome || null, plan = g.plan || {};
                        items.push({ when: g.finished_at || when, type: 'pa', id: s.id, label: 'Predictive Analytics', title: plan.goal || s.title || 'Predictive Analytics',
                            call: o ? short(paCallWord(o)) : (g.status === 'running' ? 'Running' : 'Not finished'), line: o ? first(o.answer) : ((g.done || 0) + ' of ' + (g.total || 0) + ' questions answered'),
                            act: o ? inline('pa', s.id, 'Open report') : '<button type="button" class="dtw-lb link" onclick="dtwLibOpenReport(\'' + kn + '\', \'' + sid + '\')">Open the run<i>&rarr;</i></button>' });
                    } else {
                        var qs = s.questions || [], last = qs.length ? qs[qs.length - 1] : null, dist = last ? (last.distribution || []).slice().sort(function(a, b) { return Number(b.pct) - Number(a.pct); }) : [];
                        var lead = dist.length ? short(dist[0].label) : '';
                        items.push({ when: (last && (last.finished_at || last.asked_at)) || when, type: 'room', id: s.id, label: 'Focus group room', title: roomTitle(s),
                            call: dist.length ? (lead + ' ' + pct(dist[0].pct, 0)) : (qs.length ? '' : 'Empty room'), line: last ? (last.read ? first(last.read) : qs.length + (qs.length === 1 ? ' question' : ' questions')) : 'No questions asked yet.',
                            act: inline('room', s.id, 'Open room') });
                    }
                });
                (doc.pilot_sessions || []).forEach(function(s) {
                    var sc = s.scorecard || {}, hd = ((sc.battery || {}).headline) || {};
                    items.push({ when: s.updated_at || s.created_at || '', type: 'pilot', id: s.id, label: 'Pilot test', title: s.title || 'Untitled test',
                        call: hd.total_positive_pct != null ? pct(hd.total_positive_pct, 1) + ' positive' : (sc.overall_like != null ? 'Dial ' + Number(sc.overall_like).toFixed(1) : cap(s.status || 'draft')),
                        line: (sc.norm_lines || [])[0] || '', act: inline('pilot', s.id, 'Open test') });
                });
                productRuns(doc).forEach(function(rn) {
                    var o = rn.outputs || {};
                    items.push({ when: rn.created_at || '', type: 'run', id: rn.id, label: rn.label || kindLabel(rn.kind), title: rn.question || '',
                        call: o.expected != null ? 'Expected ' + (o.unit === 'usd' ? humanMoney(o.expected) : human(o.expected)) : '', line: first(rn.recommendation || ''),
                        act: inline('run', rn.id, 'Open run') });
                });
                items.sort(function(a, b) { return String(b.when).localeCompare(String(a.when)); });
                var n = function(t) { return items.filter(function(x) { return x.type === t; }).length; };
                var np = n('pa'), nr = n('room'), nt = n('pilot'), nn = n('run');
                var sum = [np ? np + ' Predictive Analytics ' + (np === 1 ? 'report' : 'reports') : 'no Predictive Analytics reports', nr ? nr + (nr === 1 ? ' room' : ' rooms') : 'no rooms', nn ? nn + (nn === 1 ? ' run' : ' runs') : '', nt ? nt + (nt === 1 ? ' pilot test' : ' pilot tests') : 'no pilot tests'].filter(Boolean).join(SEP_TXT);
                var out = '<div class="dtw-lib-past"><div class="head"><span class="t">' + (items.length ? 'Everything done with this twin, newest first.' : 'Nothing has been done with this twin yet.') + '</span><span class="n">' + esc(sum) + '</span></div>';
                items.forEach(function(it) {
                    var on = isOpen(it.type, it.id);
                    out += '<div class="dtw-lib-item' + (on ? ' open' : '') + '" style="--c:' + libTypeColor(it.type) + '">' + libDate(it.when) + '<span class="ty">' + esc(it.label) + '</span><div class="b"><div class="t">' + esc(it.title) + '</div>' + ((it.call || it.line) ? '<div class="l">' + (it.call ? '<span class="call">' + esc(it.call) + '</span>' : '') + esc(it.line || '') + '</div>' : '') + '</div>' + it.act + '</div>';
                    if (on) out += libDetail(r, doc, it);
                });
                if (!np) out += libEmptyItem('Predictive Analytics', 'Nothing asked through Predictive Analytics yet.', '<button type="button" class="dtw-lb link" onclick="dtwTwinAsk(\'' + kn + '\')">Start Predictive Analytics<i>&rarr;</i></button>');
                if (!nr) out += libEmptyItem('Focus group room', 'No rooms on this twin yet.', '<button type="button" class="dtw-lb link" onclick="dtwTwinRoom(\'' + kn + '\')">Open a room<i>&rarr;</i></button>');
                if (!nt) out += libEmptyItem('Pilot test', 'Nothing tested on this twin yet.', '<button type="button" class="dtw-lb link" onclick="dtwTwinPilot(\'' + kn + '\')">Start a pilot test<i>&rarr;</i></button>');
                return out + '</div>';
            }
            // the item itself, right under its line (Jenna 2026-11-20: 'it should show the run right under this not at the bottom of the
            // screen and it should give you the option to reopen the room'): the run as the twin page draws it, the report's outcome, the
            // room's questions with their splits and reads, the test's scorecard; and on every one the way back into a room
            function libSession(doc, id) { return ((doc || {}).panel_sessions || []).filter(function(s) { return s && s.id === id; })[0] || null; }
            function libPilot(doc, id) { return ((doc || {}).pilot_sessions || []).filter(function(s) { return s && s.id === id; })[0] || null; }
            function libRun(doc, id) { return productRuns(doc).filter(function(x) { return x.id === id; })[0] || null; }
            function libSeatsStrip(s) {
                var roster = ((s || {}).group || {}).roster || [];
                if (!roster.length) return '';
                return '<div class="dtw-lib-seats">' + roster.slice(0, 20).map(function(p, i) {
                    var nm = titleCase(p.name || ''), who = nm + (p.age != null && p.age !== '' ? ', ' + p.age : '') + (p.city ? ', ' + titleCase(shortCity(p.city)) : '');
                    return '<span class="dtw-kind-face" style="--hue:' + hueFor(p.seat != null ? Number(p.seat) - 1 : i) + '" title="' + esc(who) + '">' + (p.portrait_url ? '<img src="' + esc(thumb(p.portrait_url)) + '" alt="" loading="lazy" onerror="this.remove()">' : esc(initials(nm))) + '</span>';
                }).join('') + (roster.length > 20 ? '<span class="dtw-lib-facts">+' + (roster.length - 20) + '</span>' : '') + '</div>';
            }
            // the room's questions, each with its split and its read; the last six, with a line for what came before
            function libRoomQs(s) {
                var qs = ((s || {}).questions || []).filter(function(q) { return q && q.q; });
                if (!qs.length) return '<div class="dtw-lib-wait">No questions asked in this room yet.</div>';
                var shown = qs.slice(-6), out = '';
                var brief = function(t) { t = String(t || '').replace(/\s+/g, ' ').trim(); var m = /^((?:[^.!?]+[.!?]+\s*){1,2})/.exec(t); return m ? m[1].trim() : t; };
                if (qs.length > shown.length) out += '<div class="dtw-lib-wait">' + (qs.length - shown.length) + ' earlier ' + (qs.length - shown.length === 1 ? 'question is' : 'questions are') + ' in the room.</div>';
                shown.forEach(function(q) {
                    var idx = qs.indexOf(q), rd = q.read_short || brief(q.read);
                    out += '<div class="dtw-lib-q"><div class="qt"><span class="n">' + pad2(idx + 1) + '</span><span>' + esc(q.q) + '</span></div>'
                        + (qInFlight(q) ? '<div class="dtw-lib-wait">The room is still working on this one.</div>' : (qFailed(q) ? '<div class="dtw-lib-wait">This one did not finish.</div>' : questionLolli(q) + (rd ? '<p class="rd">' + readHtml(rd) + '</p>' : '')))
                        + '</div>';
                });
                return out;
            }
            function libRoomFacts(s) {
                var g = (s || {}).group || {}, seats = Number(g.in_room || (g.roster || []).length || 0), qs = ((s || {}).questions || []).length, when = s.created_at ? fmtDay(s.created_at) : '';
                return '<div class="dtw-lib-facts">' + (seats ? seats + ' seats' : '') + (qs ? SEP_TXT + qs + (qs === 1 ? ' question' : ' questions') : '') + (g.size_people ? SEP_TXT + 'standing for ' + human(g.size_people) + ' people' : '') + (when ? SEP_TXT + 'opened ' + esc(when) : '') + '</div>';
            }
            function libDetail(r, doc, it) {
                var key = r.key, kn = esc(jsq(key)), id = esc(jsq(it.id)), body = '', acts = '';
                var close = '<button type="button" class="dtw-lb link quiet" onclick="dtwLibOpenItem(\'' + kn + '\', \'' + it.type + '\', \'' + id + '\')">Close</button>';
                var csv = function(sid) { return '<a class="dtw-lb link" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/panel/' + esc(sid) + '/export.csv">Raw data (CSV)<i>&rarr;</i></a>'; };
                if (it.type === 'run') {
                    var rn = libRun(doc, it.id); if (!rn) return '';
                    body = runHtml(rn, { exports: false });
                    // a run carries no room of its own, so the way back in is a new room on the twin with the run's question ready to ask
                    acts = '<button type="button" class="dtw-lb go room" onclick="dtwLibRoomOn(\'' + kn + '\', \'' + id + '\')">Open a room on this<i>&rarr;</i></button>';
                } else if (it.type === 'pa') {
                    var ps = libSession(doc, it.id); if (!ps || !ps.guided) return '';
                    var g = ps.guided, plan = g.plan || {};
                    body = '<div class="dtw-lib-pa"><div class="dtw-lib-sec"><span class="dtw-cap">The outcome</span><span class="n">' + (g.finished_at ? 'finished ' + esc(fmtDay(g.finished_at)) : '') + '</span></div>' + paOutcomeBody(g.outcome) + '</div>'
                        + '<div class="dtw-lib-sec" style="margin-top:16px"><span class="dtw-cap">The questions the room answered</span><span class="n">' + (g.done || (ps.questions || []).length || 0) + ' of ' + (g.total || (plan.questions || []).length || 0) + '</span></div>' + libSeatsStrip(ps) + libRoomQs(ps);
                    acts = '<a class="dtw-lb go pa" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/guided/' + esc(ps.id) + '/report.pdf">Download the report (PDF)<i>&rarr;</i></a>'
                        + '<button type="button" class="dtw-lb go room" onclick="dtwLibOpenRoom(\'' + kn + '\', \'' + id + '\')">Reopen the room<i>&rarr;</i></button>'
                        + '<button type="button" class="dtw-lb link" onclick="dtwLibOpenReport(\'' + kn + '\', \'' + id + '\')">The full report<i>&rarr;</i></button>' + csv(ps.id);
                } else if (it.type === 'room') {
                    var rs = libSession(doc, it.id); if (!rs) return '';
                    body = '<div class="dtw-lib-sec"><span class="dtw-cap">' + esc(roomTitle(rs)) + '</span><span class="n"></span></div>' + libRoomFacts(rs) + libSeatsStrip(rs) + libRoomQs(rs);
                    acts = '<button type="button" class="dtw-lb go room" onclick="dtwLibOpenRoom(\'' + kn + '\', \'' + id + '\')">Reopen the room<i>&rarr;</i></button>' + csv(rs.id);
                } else if (it.type === 'pilot') {
                    var pt = libPilot(doc, it.id); if (!pt) return '';
                    var card = renderScorecard(pt, true);
                    body = '<div class="dtw-lib-sec"><span class="dtw-cap">' + esc(pt.title || 'Untitled test') + '</span><span class="n">' + esc(cap(String(pt.status || 'draft'))) + (pt.created_at ? SEP_TXT + esc(fmtDay(pt.created_at)) : '') + '</span></div>' + (card || '<div class="dtw-lib-wait">This test has no scores yet. Open it to run the room.</div>');
                    acts = '<button type="button" class="dtw-lb go pilot" onclick="dtwLibOpenTest(\'' + kn + '\', \'' + id + '\', \'' + esc(jsq(pt.mode || 'pilot')) + '\')">Open the test<i>&rarr;</i></button>';
                }
                return '<div class="dtw-lib-detail ' + it.type + '" style="--c:' + libTypeColor(it.type) + '"><div class="inner">' + body + '</div><div class="dtw-lib-acts">' + acts + close + '</div></div>';
            }
            window.dtwLibOpenItem = function(key, type, id) {
                var cur = (S.libOpen && S.libOpen.key === key && S.libOpen.kind === 'past') ? S.libOpen : null;
                if (cur && cur.item && cur.item.type === type && String(cur.item.id) === String(id)) cur.item = null;
                else S.libOpen = { key: key, kind: 'past', item: { type: type, id: id } };
                renderLibrary();
                if (S.libOpen && S.libOpen.item && !libDoc(key)) libLoadDoc(key);
                var line = document.querySelector('#dtwLibrary .dtw-lib-item.open');
                if (line) { try { line.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (_) {} }
            };
            // a room on the run: the New room drawer on this twin, titled from the run, with the run's question ready in the composer
            window.dtwLibRoomOn = async function(key, id) {
                var doc = libDoc(key), rn = doc ? libRun(doc, id) : null;
                if (rn) S.roomSeed = { key: key, q: String(rn.question || '').replace(/\s+/g, ' ').trim(), title: String(rn.label || kindLabel(rn.kind) || 'Room').slice(0, 120), at: Date.now() };
                await dtwTwinRoom(key);
                if (!rn) return;
                var t = el('dtwPnTitle'); if (t) t.value = S.roomSeed ? S.roomSeed.title : '';
                var st = el('dtwPnStatus'); if (st) st.textContent = 'The room opens with the run\'s question ready to ask.';
            };
            function libRoomSeedTake(key) {
                var seed = S.roomSeed; S.roomSeed = null;
                return (seed && seed.key === key && seed.q && (Date.now() - (seed.at || 0)) < 15 * 60 * 1000) ? seed : null;
            }
            // the ways back in from an entry
            window.dtwLibSeeTwin = async function(key) {
                if (S.key !== key) { try { await dtwOpen(key, null, null, null, 'twins'); } catch (_) {} } else dtwTab('twins');
                S.twinPage = true; renderTwin(); renderLibrary();
                var pg = el('dtwTwinPage'); if (pg) { try { pg.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {} }
            };
            window.dtwTwinPageHide = function() { S.twinPage = false; renderTwin(); var lib = el('dtwLibrary'); if (lib) { try { lib.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {} } };
            window.dtwLibOpenReport = async function(key, sid) {
                if (S.key !== key) { try { await dtwOpen(key, null, null, null, 'predict'); } catch (_) {} } else dtwTab('predict');
                dtwPaOpenRun(sid);
            };
            window.dtwLibOpenRoom = async function(key, sid) { try { await dtwOpen(key, null, sid, null, 'panel'); } catch (_) {} };
            window.dtwLibOpenTest = async function(key, sid, mode) { var tab = piTabFor(mode); if (DTW_HIDDEN_TABS.indexOf(tab) >= 0) tab = 'pilot'; try { await dtwOpen(key, null, sid, null, tab); } catch (_) {} };
            window.dtwLibOpenRun = async function(key, id) { try { await dtwOpen(key, id); } catch (_) {} var rr = el('dtwRunResults'); if (rr && rr.innerHTML) { try { rr.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {} } };
            window.dtwOpen = async function(key, runId, sessionId, planId, tab) {
                var page = el('dtwTwinPage');
                if (page) page.innerHTML = '<div class="dtw-card dtw-muted">Opening the twin...</div>';
                try { var d = await api('/' + key); } catch (e) { if (page) page.innerHTML = '<div class="dtw-card dtw-muted">' + esc(e.message) + '</div>'; return; }
                var switched = S.key && S.key !== key;
                if (switched) S.twinPage = false;
                S.twin = d.twin; S.key = key; S.summary = d.summary; S.openRun = runId || null; S.session = null; S.pilot = null; S.plan = null; S.spendDay = 1;
                S.twin.runs = productRuns(S.twin);
                clearPortraitTimers(); try { dtwSeatOpen(null); } catch (_) {}
                fillTwinSelects(); renderSpine(); renderLibrary(); renderTwin(); syncInputCards();
                if (switched) { ['dtwRunResults', 'dtwLaResults', 'dtwSpResults', 'dtwPnConsole', 'dtwPiConsole', 'dtwTrConsole', 'dtwTiConsole', 'dtwScConsole', 'dtwCoConsole', 'dtwNoConsole'].forEach(function(id) { if (el(id)) el(id).innerHTML = ''; }); S.pilotCompare = null; }
                if (el('dtwRunResults') && !runId) el('dtwRunResults').innerHTML = '';
                var run = runId ? (S.twin.runs || []).filter(function(x) { return x.id === runId; })[0] : null;
                if (sessionId && isPiTab(tab)) { S.pilot = (S.twin.pilot_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || null; }
                else if (sessionId) { S.session = (S.twin.panel_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || S.pilot; if (!S.session) S.pilot = (S.twin.pilot_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || null; }
                if (planId) { S.plan = ((S.twin.labs || {}).spend || []).filter(function(p) { return p.plan_id === planId; })[0] || null; }
                if (run) { if (run.kind === 'launch') renderLaunch(run); else if (run.kind === 'spend') { S.plan = ((S.twin.labs || {}).spend || []).filter(function(p) { return p.plan_id === (run.inputs || {}).plan_id; })[0] || S.plan; } else renderRun(run); }
                else { if (el('dtwLaResults') && !el('dtwLaResults').querySelector('[data-dtw-run-id]')) renderEmpty('dtwLaResults', 'launch'); }
                syncSpendSources();
                if (S.session) renderPanelConsole(); else renderPanelConsole();
                renderPanelSessions();
                if (S.pilot) { S.piTab = piTabFor(S.pilot.mode); }
                if (S.pilot || el('dtwPiConsole')) renderPilotConsole();
                if (S.plan) renderSpend();
                dtwTab(tab || (S.pilot ? piTabFor(S.pilot.mode) : S.session ? 'panel' : S.plan ? 'spend' : run ? ({'launch': 'launch', 'spend': 'spend'}[run.kind] || 'twins') : 'twins'));
                try { if (typeof _pmWarmScreen === 'function') _pmWarmScreen(); } catch (_) {}
            };
            window.dtwAsk = function() {
                var inp = el('dtwAskInput'); var q = inp && inp.value.trim();
                if (!q) { chatFill('Ask the ' + twinName() + ' twin: '); return; }
                if (inp) inp.value = '';
                chatSend(q);
            };
            function demoRows(cat) { return (((S.twin || {}).identity || {}).demographics || {})[cat] || []; }
            function fold(id, title, note, body, open) { return '<details class="dtw-fold" id="dtwFold-' + id + '"' + (open ? ' open' : '') + '><summary>' + esc(title) + (note ? '<span class="n">' + esc(note) + '</span>' : '') + '<span class="chev">&#9662;</span></summary><div class="dtw-fold-body">' + body + '</div></details>'; }
            // Who makes up this audience (Jenna 2026-11-19, from the team notice she liked): the kinds of people in the twin, largest
            // first, each with the person who stands for it, how many people that is and the share, and what the desk wrote about them
            // behind a tap. The person carries a face once it exists (the twin's own portrait pass), initials until then.
            function kindsSection(t) {
                var a = (t || {}).archetypes || {}, groups = (a.groups || []).filter(function(g) { return g && g.label; });
                if (!groups.length) return '';
                var noun = a.noun || 'people', size = Number(a.size_people || ((t || {}).meta || {}).size_us || 0), top = groups[0];
                var maxShare = Math.max.apply(null, groups.map(function(g) { return Number(g.share_pct || 0); }).concat([1]));
                var line = groups.length + ' archetypes make up the ' + fmtN(size) + ' ' + esc(noun) + ' in this audience; the largest is ' + esc(String(top.label || '').charAt(0).toLowerCase() + String(top.label || '').slice(1)) + ' at ' + Number(top.share_pct || 0).toFixed(1) + '% (' + fmtN(top.people) + ' ' + esc(noun) + ').';
                var rows = groups.map(function(g, i) {
                    var rep = g.representative || {}, who = rep.name ? esc(titleCase(rep.name)) + (rep.age != null && rep.age !== '' ? ', ' + esc(rep.age) : '') + (rep.city ? ', ' + esc(titleCase(shortCity(rep.city))) : '') : '';
                    var face = rep.portrait_url ? '<img src="' + esc(thumb(rep.portrait_url)) + '" alt="" onerror="this.remove()">' : esc(initials(rep.name || g.label || ''));
                    var about = [['Who they are', g.who], ['How they relate to it', g.relationship], ['How they decide', g.decides], ['What moves them', g.moves_them], ['What holds them back', g.holds_them_back]].filter(function(x) { return x[1]; });
                    var more = about.length ? '<div class="dtw-kind-about">' + about.map(function(x) { return '<div><span class="k">' + esc(x[0]) + '</span><span>' + esc(x[1]) + '</span></div>'; }).join('') + '</div>' : '';
                    var extra = [rep.occupation, rep.income].filter(Boolean).map(function(x) { return esc(x); }).join(SEP);
                    return '<details class="dtw-kind"><summary><span class="dtw-kind-face" style="--hue:' + hueFor(i) + '">' + face + '</span>'
                        + '<span class="dtw-kind-main"><span class="lab">' + esc(g.label) + '</span><span class="who">' + who + (extra ? '<span class="x">' + SEP + extra + '</span>' : '') + '</span></span>'
                        + '<span class="dtw-kind-n"><b>' + fmtN(g.people) + '</b> ' + esc(noun) + '<i>' + Number(g.share_pct || 0).toFixed(1) + '%</i></span>'
                        + '<span class="dtw-kind-bar"><span style="width:' + Math.max(2, Math.min(100, Number(g.share_pct || 0) / maxShare * 100)).toFixed(1) + '%"></span></span>'
                        + (more ? '<span class="chev">&#9662;</span>' : '<span class="chev" style="visibility:hidden">&#9662;</span>') + '</summary>' + more + '</details>';
                }).join('');
                return '<div class="dtw-kinds"><div class="dtw-kinds-h"><div><div class="dtw-eyebrow">Who makes up this audience</div><p class="dtw-kinds-line">' + line + '</p></div><div class="dtw-kinds-note">Every room seats one archetype representative of each; tap an archetype for who they are.</div></div><div class="dtw-kinds-list">' + rows + '</div></div>';
            }
            // The selected twin: Identity, Traits, Lifecycle, Runs. No hero card.
            function renderTwin() {
                var t = S.twin, h = el('dtwTwinPage'); if (!h) return;
                if (!t) { h.innerHTML = ''; return; }
                var m = t.meta || {}, ident = t.identity || {}, key = m.key || S.key, lc = t.lifecycle || {};
                var kn = esc(jsq(key)), nm = m.name || m.subject || 'this twin';
                if (!S.twinPage) { h.innerHTML = ''; return; }
                // the kinds live in the entry's Learn more now (Jenna 2026-11-19); this page is what See the full twin opens
                var out = '<div class="dtw-lib-sec dtw-twinpage-h"><span class="dtw-cap on">The full twin' + SEP_TXT + esc(nm) + '</span><button type="button" class="dtw-lb link quiet" onclick="dtwTwinPageHide()">Hide</button></div><div class="dtw-actions dtw-export"><a class="dtw-btn dtw-btn-sm" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/export.csv">CSV</a><a class="dtw-btn dtw-btn-sm" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/export.pdf">PDF</a><a class="dtw-btn dtw-btn-sm" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(key) + '/export.pptx">Deck</a><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwTwinDeleteAsk(\'' + kn + '\', null, \'dtwTwinDelP-\')">Delete</button>' + assignControl(key) + twinPageConfirm(key, nm) + '</div>';
                // Identity
                var dsh = m.demographics_shares || null, shb = '';
                if (dsh && dsh.asked && Object.keys(dsh.asked).length) shb = '<div class="dtw-muted" style="margin-bottom:0.6rem;"><b style="color:var(--dtw-ink);font-weight:600;">Shares set for this twin.</b> ' + esc(bwSharesWords(dsh.asked)) + ((dsh.notes || []).length ? ' ' + esc(dsh.notes.join(' ')) : '') + '</div>';
                var idb = shb + '<div class="dtw-two"><div>';
                [['gender', 'Gender'], ['age', 'Age'], ['sexual_orientation', 'Orientation'], ['education', 'Education'], ['occupation', 'Occupation'], ['income', 'Income'], ['ethnicity', 'Ethnicity'], ['relationship', 'Relationship']].forEach(function(c) { var rows = demoRows(c[0]); if (rows.length) { idb += '<h4>' + c[1] + '</h4>' + rows.slice(0, 8).map(function(r) { return bar(r.label, r.pct, 100, false, pct(r.pct, 1)); }).join(''); } });
                if ((ident.geography || []).length) { idb += '<h4>Top markets</h4>' + ident.geography.slice(0, 8).map(function(r) { return bar(r.label, r.pct, Math.max.apply(null, ident.geography.map(function(x) { return x.pct; })), true, pct(r.pct, 1)); }).join(''); }
                idb += '</div><div>';
                if ((ident.platforms || []).length) { idb += '<h4>Platforms, share of the twin in a year</h4>' + ident.platforms.slice(0, 10).map(function(r) { return bar(r.label, r.reach_pct, 100, false, pct(r.reach_pct, 1)); }).join(''); }
                if ((ident.purchase || []).length) { idb += '<h4>What they buy</h4>' + ident.purchase.slice(0, 10).map(function(r) { return bar(r.label, r.pct, 100, true, pct(r.pct, 1)); }).join(''); }
                if ((ident.talent || []).length) { idb += '<h4>People they follow</h4><div class="dtw-muted">' + ident.talent.slice(0, 8).map(function(r) { return esc(r.label) + ' ' + pct(r.pct, 1); }).join(', ') + '</div>'; }
                idb += '</div></div>';
                out += fold('identity', 'Identity', 'who they are', idb, false);
                // Traits
                var tr = '<div class="dtw-muted" style="margin-bottom:0.5rem;">Twelve dials, 0 to 100. 50 is an ordinary US audience.</div><div class="dtw-two">';
                var keys = Object.keys(t.traits || {}), half = Math.ceil(keys.length / 2);
                [keys.slice(0, half), keys.slice(half)].forEach(function(ks) { tr += '<div>' + ks.map(function(k) { var v = t.traits[k]; return '<div style="margin:0.5rem 0;">' + bar(v.label || k, v.value, 100, false, String(v.value)) + '<div class="dtw-muted" style="padding-left:0.1rem;">They ' + esc(v.read || '') + '.</div></div>'; }).join('') + '</div>'; });
                tr += '</div>';
                out += fold('traits', 'Traits', 'how they respond', tr, false);
                // Lifecycle
                var lcb = '<div class="dtw-two"><div>';
                [['discovering_pct', 'Discovering'], ['trying_pct', 'Trying'], ['habit_pct', 'In the habit'], ['lapsing_pct', 'Lapsing'], ['returning_pct', 'Returning']].forEach(function(c) { if (lc[c[0]] != null) lcb += bar(c[1], lc[c[0]], 100, false, pct(lc[c[0]], 1)); });
                lcb += '<p class="dtw-muted" style="margin-top:0.6rem;">' + esc(lc.read || '') + '</p></div><div>';
                var sq = t.standing_questions;
                if (sq) { lcb += '<h4 style="margin-top:0;">The standing questions</h4><p class="dtw-muted">' + esc(sq.relative_popularity || '') + ' ' + esc(sq.demographics_line || '') + ' Prime Video in the last 30 days: ' + pct(sq.prime_video_t30d_pct) + '. SVOD churn: ' + pct(sq.svod_churn_pct) + '. On Prime Video: ' + pct(sq.on_prime_pct) + '; the rest sit on ' + (sq.off_prime_platforms || []).slice(0, 5).map(function(p) { return esc(p.label) + ' ' + pct(p.pct, 1); }).join(', ') + '.</p>'; }
                if (t.track_record) { lcb += '<h4>Track record</h4><p class="dtw-muted">' + esc(t.track_record.line) + '</p>'; }
                lcb += '</div></div>';
                out += fold('lifecycle', 'Lifecycle', 'where they sit', lcb, false);
                // Runs
                var rb = '<div class="dtw-muted" style="margin-bottom:0.3rem;">Every lab result lands here. Click one to open it.</div>';
                productRuns(t).forEach(function(r) { var o = r.outputs || {}; var usd = o.unit === 'usd'; rb += '<div class="dtw-run' + (r.id === S.openRun ? ' on' : '') + '" onclick="dtwOpenRun(\'' + esc(r.id) + '\')"><div class="q"><span class="dtw-tag" style="margin-right:0.4rem;">' + esc(r.label || kindLabel(r.kind)) + '</span>' + esc(r.question || '') + '</div><div class="m">Expected ' + (usd ? fmtMoney(o.expected) : fmtN(o.expected) + ' ' + esc(o.noun || 'people')) + (o.low != null ? ', range ' + (usd ? fmtMoney(o.low) : fmtN(o.low)) + ' to ' + (usd ? fmtMoney(o.high) : fmtN(o.high)) : '') + (o.odds_pct != null ? '. Odds ' + pct(o.odds_pct) + ' (' + esc(o.odds_words || '') + ')' : '') + '. ' + esc(fmtDate(r.created_at)) + ' by ' + esc(r.created_by || '') + '</div></div>'; });
                if (!productRuns(t).length) rb += '<p class="dtw-muted">No runs yet. Run Launch on this twin, or ask Prometheus for a price, a platform move or a new season read.</p>';
                out += fold('runs', 'Runs', productRuns(t).length + ' on this twin', rb, false);
                h.innerHTML = out;
            }
            window.dtwOpenRun = function(id) { var r = (S.twin.runs || []).filter(function(x) { return x.id === id; })[0]; if (!r || isRetiredRun(r)) return; S.openRun = id; if (r.kind === 'launch') { renderLaunch(r); dtwTab('launch'); } else if (r.kind === 'spend') { S.plan = ((S.twin.labs || {}).spend || []).filter(function(p) { return p.plan_id === (r.inputs || {}).plan_id; })[0] || null; renderSpend(); dtwTab('spend'); } else { renderRun(r); dtwTab('twins'); var rh = el('dtwRunResults'); if (rh && rh.scrollIntoView) { try { rh.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {} } } };
            function renderHowto() {
                var h = el('dtwHowto'); if (!h) return;
                var d = S.howto; if (!d) { h.innerHTML = '<div class="dtw-eyebrow">How to</div><div class="dtw-muted" style="margin-top:0.6rem;">Loading the directions...</div>'; return; }
                var out = '<div class="dtw-eyebrow">How to</div><h2>' + esc(d.title) + '</h2><p class="dtw-p">' + esc(d.intro) + '</p>';
                (d.items || []).forEach(function(it) {
                    out += '<h3>' + esc(it.title) + '</h3><p class="dtw-p"><b>What it answers.</b> ' + esc(it.answers) + '</p>' + ((it.options || []).length ? '<p class="dtw-p"><b>The options.</b> ' + it.options.map(function(o) { return esc(o.title) + (o.price ? ' (' + esc(o.price) + ')' : '') + ': ' + esc(o.line || ''); }).join(' ') + '</p>' : '') + '<p class="dtw-p"><b>What to put in.</b> ' + esc(it.put_in) + '</p><p class="dtw-p"><b>Type this to Prometheus</b> (click to fill it in):</p><div>' + (it.type_this || []).map(function(t) { return '<span class="ex" onclick="dtwChatFill(\'' + esc(jsq(t)) + '\')">' + esc(t) + '</span>'; }).join('') + '</div><p class="dtw-p"><b>Attaching a file.</b> ' + esc(it.attach) + '</p><p class="dtw-p"><b>What comes back and where.</b> ' + esc(it.comes_back) + '</p>' + (it.protect ? '<p class="dtw-p"><b>Protecting your cut.</b> ' + esc(it.protect) + '</p>' : '') + '<p class="dtw-p"><b>Cost.</b> ' + esc(it.cost) + ' <b>Export.</b> ' + esc(it.export) + '</p>';
                });
                out += '<h3>Pricing</h3>' + paras(d.pricing) + '<p class="dtw-muted">' + esc(d.where || '') + '</p>';
                h.innerHTML = out;
            }
            function clearPortraitTimers() { (S.portraitTimers || []).forEach(function(t) { clearTimeout(t); }); S.portraitTimers = []; }
            window._dtwRender = { renderTwin: renderTwin, renderHowto: renderHowto, renderEmpty: renderEmpty, renderSpine: renderSpine };
            window._dtwInternal = { remembered: remembered, fmtDay: fmtDay, titleWords: titleWords, syncInputCards: syncInputCards, fillTwinSelects: fillTwinSelects, clearPortraitTimers: clearPortraitTimers, exportRow: exportRow, kindLabel: kindLabel, paras: paras, signed: signed, fmtWhen: fmtWhen, cap: cap };

            // ---------------------------------------------------------------- jobs
            window.dtwPollJob = function(jobId, onDone, statusEl) {
                var waited = 0;
                (async function loop() {
                    while (waited < 1500000) {
                        await new Promise(function(res) { setTimeout(res, 6000); }); waited += 6000;
                        var d = null; try { d = await api('/jobs/' + jobId); } catch (_) { d = null; }
                        if (!d || !d.job) continue;
                        var j = d.job;
                        if (statusEl && el(statusEl)) el(statusEl).textContent = j.status === 'done' ? 'Done.' : (j.stage || j.status || 'working') + '...';
                        if (j.status === 'done') { try { onDone(j); } catch (_) {} return; }
                        if (j.status === 'error' || j.status === 'cancelled') { if (statusEl && el(statusEl)) el(statusEl).textContent = j.error || 'That run did not finish; nothing more is charged.'; return; }
                    }
                })();
            };
            function parseChannels(text) {
                var out = [];
                String(text || '').split(/\n|;/).forEach(function(line) {
                    var m = line.match(/([A-Za-z][A-Za-z +\/&-]{1,24}?)\s*[:=,-]?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\b/);
                    if (!m) return;
                    var amt = parseFloat(m[2].replace(/,/g, '')); var k = (m[3] || '').toLowerCase(); amt *= k === 'k' ? 1000 : k === 'm' ? 1000000 : 1;
                    if (amt > 0) out.push({ channel: m[1].trim(), spend: amt });
                });
                return out;
            }
            function goalOf(valId, nounId) { var v = parseFloat(String((el(valId) || {}).value || '').replace(/[,$]/g, '')); if (!v) return null; return { metric: (el(nounId) || {}).value || 'viewers', value: Math.round(v), window_days: 30 }; }
            function runMeta(r) { return (r.created_at ? fmtDate(r.created_at) : '') + (r.created_by ? ' by ' + r.created_by : ''); }
            // The Launch tab's run. (Greenlight no longer runs from here: its two options are Pilot Testing sessions, opened by dtwPilotOpen.)
            window.dtwRunLab = async function(kind) {
                if (GL_OPTIONS.indexOf(kind) >= 0 || kind === 'greenlight') { dtwTab(kind === 'greenlight' ? 'greenlight' : kind); return; }
                var statusId = 'dtwLaStatus';
                var key = S.key;
                if (!key) { el(statusId).textContent = 'Pick a twin on the Twins tab first, or build one.'; return; }
                var body = { kind: 'launch', inputs: {} };
                var chans = parseChannels(el('dtwLaChannels').value);
                if (!chans.length) { el(statusId).textContent = 'Add at least one channel with spend (Meta $120,000).'; return; }
                var plan = { channels: chans, window_weeks: parseInt(el('dtwLaWeeks').value || '6', 10) || 6, assets: (el('dtwLaAssets').value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean), platform: (el('dtwLaPlatform').value || '').trim() };
                var g2 = goalOf('dtwLaGoal', 'dtwLaNoun'); if (g2) plan.goal = g2;
                body.inputs = { plan: plan };
                el(statusId).textContent = 'Queued. Running it forward on the twin...';
                try { var d = await api('/' + key + '/run', { method: 'POST', body: JSON.stringify(body) }); }
                catch (e) { el(statusId).textContent = e.message; return; }
                el(statusId).textContent = d.message || 'Running...';
                dtwPollJob(d.job_id, function(j) { dtwOpen(key, j.run_id || null, null, null, 'launch'); }, statusId);
            };
            function cohortTable(rows) { return '<table class="dtw-table"><tr><th>Cohort</th><th class="num">Share</th><th class="num">People</th></tr>' + (rows || []).map(function(c) { return '<tr><td>' + esc(c.cohort) + '</td><td class="num">' + pct(c.share_pct, 1) + '</td><td class="num">' + fmtN(c.people) + '</td></tr>'; }).join('') + '</table>'; }
            function leverTable(rows, usd) { return '<table class="dtw-table"><tr><th>#</th><th>Lever</th><th class="num">Earns</th></tr>' + (rows || []).map(function(l) { return '<tr><td>' + (l.rank || '') + '</td><td>' + esc(l.lever) + '<div class="dtw-muted">' + esc(l.read || '') + '</div></td><td class="num">' + signed(l.delta_pct) + '<div class="dtw-muted">' + (usd ? fmtMoney(l.delta_people) : fmtN(l.delta_people) + ' people') + '</div></td></tr>'; }).join('') + '</table>'; }
            function readCard(r) { var rd = r.read || {}; var body = paras(rd.tldr) + ((r.drivers || []).length ? '<h4>Why</h4>' + paras(r.drivers) : '') + (r.recommendation ? '<h4>What to do</h4><p class="dtw-p"><b>' + esc(r.recommendation) + '</b></p>' : ''); return card('The read', 'What the numbers say and what to do about it.', body); }
            // A run opened from the Runs fold (pricing, platform move, content drop, partnership, talent attach, merch drop, compare) on the Twins pane:
            // headline figures, then the chart and table, then levers and the package, then the read, then exports.
            // the run as HTML, drawn once for the twin page's bottom host and for the inline open in the library's Past work
            // (Jenna 2026-11-20: 'it should show the run right under this not at the bottom of the screen')
            function runHtml(r, opts) {
                opts = opts || {};
                if (!r || isRetiredRun(r)) return '';
                var exports = opts.exports === false ? '' : exportRow();
                var o = r.outputs || {}, rd = r.read || {}, out = '<div data-dtw-run-id="' + esc(r.id) + '">';
                if (r.kind === 'compare') {
                    var a = o.a || {}, b = o.b || {};
                    out += card(r.question || 'Compare', 'Two twins on the same idea. ' + runMeta(r), '<div class="dtw-eyebrow" style="margin-top:0.4rem;">Compare</div>' + figs([[fmtN(a.expected) + ' ' + (a.noun || ''), a.twin || 'Twin A', 'expected', true], [fmtN(b.expected) + ' ' + (b.noun || ''), b.twin || 'Twin B', 'expected'], [signed(o.delta_pct), 'A against B']]) + paras(rd.tldr) + (r.recommendation ? '<p class="dtw-p"><b>' + esc(r.recommendation) + '</b></p>' : ''));
                    return out + exports + '</div>';
                }
                var days = (o.goal || {}).window_days || 30, noun = o.noun || 'people';
                var head = '';
                if (o.odds_pct != null) { head += '<div class="dtw-gauge"><div class="big">' + pct(o.odds_pct) + '</div><div class="say">' + esc(cap(o.odds_words || '')) + ' chance it clears ' + fmtN((o.goal || {}).value) + ' ' + esc(noun) + ' in its first ' + days + ' days on ' + esc(o.platform || 'its home') + '.</div></div>'; }
                if (r.kind === 'platform_move') {
                    head += figs([[fmtN(o.expected), 'People after the move', 'from ' + (o.from || '') + ' to ' + (o.to || ''), true], [fmtN(o.low) + ' to ' + fmtN(o.high), 'Range'], [fmtN(o.retained), 'Kept', pct(o.retention_pct) + ' of the audience'], [fmtN(o.lost), 'Lost'], [fmtN(o.gained), 'New', o.net_change_pct != null ? signed(o.net_change_pct) + ' net' : '']]);
                } else if (r.kind === 'content') {
                    head += figs([[fmtN(o.expected) + ' ' + noun, 'Expected' + (o.platform ? ' on ' + o.platform : ''), '', true], [fmtN(o.low) + ' to ' + fmtN(o.high), 'Range'], [pct(o.completion_pct), 'Completion'], [pct(o.return_pct), 'Come back for the next one'], [pct(o.subscribe_renew_pct), 'Subscribe or renew'], [pct(o.churn_risk_at_end_pct), 'Churn risk at the end']]);
                } else {
                    head += figs([[fmtN(o.expected) + ' ' + noun, 'Expected, first ' + days + ' days' + (o.platform ? ' on ' + o.platform : ''), '', o.odds_pct == null], [fmtN(o.low) + ' to ' + fmtN(o.high), 'Range'], [pct(o.completion_pct), 'Completion'], [pct(o.return_pct), 'Come back for the next one']]);
                }
                out += card(r.question || kindLabel(r.kind), kindLabel(r.kind) + '. ' + runMeta(r), head + (o.window_note ? '<p class="dtw-muted">' + esc(o.window_note) + '</p>' : ''));
                if ((o.per_platform || []).length || (o.cohorts || []).length) {
                    out += '<div class="dtw-two">';
                    if ((o.per_platform || []).length) out += card('Range per platform', 'Expected and range for the first ' + days + ' days.', (o.per_platform || []).map(function(p) { return bar(p.platform, p.expected, Math.max.apply(null, (o.per_platform || []).map(function(x) { return x.high; })), false, fmtN(p.low) + ' to ' + fmtN(p.high) + (p.odds_pct != null ? ', odds ' + pct(p.odds_pct) : '')); }).join(''));
                    if ((o.cohorts || []).length) out += card('Who shows up', 'Share of the first-month audience by cohort.', cohortTable(o.cohorts));
                    out += '</div>';
                }
                var pk = o.package || null;
                if ((o.levers || []).length || pk) {
                    out += '<div class="dtw-two">';
                    if ((o.levers || []).length) out += card('Levers, ranked by what they earn', 'Each one on its own, against the expected figure.', leverTable(o.levers, false));
                    if (pk) out += card('The package to run', pk.read || '', kv([[pk.platform || '', 'Home'], [pk.window || '', 'Window'], [pk.lead_asset || '', 'Lead asset'], [pk.clip_cadence || '', 'Clips'], [pk.spend_mix || '', 'Paid'], [pk.holdout || '', 'Holdout']]) + '<div class="dtw-actions"><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwTab(\'launch\')">Plan the launch</button><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwChatSend(\'Open a focus group with the ' + esc(jsq(twinName())) + ' twin and ask whether they would watch ' + esc(jsq(o.title || 'it')) + ' on ' + esc(jsq(o.platform || '')) + '\')">Ask the room about it</button></div>');
                    out += '</div>';
                }
                out += readCard(r) + exports + '</div>';
                return out;
            }
            function renderRun(r) { var h = el('dtwRunResults'); if (!h) return; h.innerHTML = runHtml(r); }
            function bandChart(weeks, goal) {
                if (!weeks || !weeks.length) return '';
                var W = 560, Hh = 170, pad = 30, max = Math.max.apply(null, weeks.map(function(w) { return w.high; }).concat([goal || 0])) || 1;
                var x = function(i) { return pad + (W - 2 * pad) * (i / Math.max(1, weeks.length - 1)); }, y = function(v) { return Hh - pad - (Hh - 2 * pad) * (v / max); };
                var top = weeks.map(function(w, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(w.high).toFixed(1); }).join(' ');
                var bot = weeks.slice().reverse().map(function(w, j) { var i = weeks.length - 1 - j; return 'L' + x(i).toFixed(1) + ' ' + y(w.low).toFixed(1); }).join(' ');
                var mid = weeks.map(function(w, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(w.expected).toFixed(1); }).join(' ');
                var g = goal ? '<line x1="' + pad + '" x2="' + (W - pad) + '" y1="' + y(goal).toFixed(1) + '" y2="' + y(goal).toFixed(1) + '" stroke="#E682FF" stroke-dasharray="4 4"></line><text x="' + (W - pad) + '" y="' + (y(goal) - 4).toFixed(1) + '" font-size="10" text-anchor="end" fill="#E682FF">goal ' + human(goal) + '</text>' : '';
                return '<svg class="dtw-band" viewBox="0 0 ' + W + ' ' + Hh + '"><path d="' + top + ' ' + bot + ' Z" fill="rgba(199,242,62,0.16)"></path><path d="' + mid + '" fill="none" stroke="#C7F23E" stroke-width="2"></path>' + g + weeks.map(function(w, i) { return '<text x="' + x(i).toFixed(1) + '" y="' + (Hh - 8) + '" font-size="10" text-anchor="middle" fill="#7C878A">W' + w.week + (w.odds_pct != null ? ' ' + pct(w.odds_pct, 0) : '') + '</text>'; }).join('') + '</svg>';
            }
            // Launch results: the three plans, then the week-by-week band and table, then levers and the holdout, then the read, then exports.
            function renderLaunch(r) {
                var h = el('dtwLaResults'); if (!h) return;
                var o = r.outputs || {}, goal = (o.goal || {}).value, noun = o.noun || 'people';
                var out = '<div data-dtw-run-id="' + esc(r.id) + '">';
                var plans = '<div class="dtw-plans">' + (o.plans || []).map(function(p, i) {
                    return '<div class="dtw-plan"><div class="dtw-eyebrow">' + esc(p.label) + '</div><div class="big">' + fmtN(p.expected) + ' <span class="dtw-muted">' + esc(noun) + '</span></div><div class="dtw-muted">' + fmtMoney(p.spend) + ' spend, ' + fmtN(p.first_actions) + ' first actions at ' + fmtMoney(p.cpa) + '</div>' + (p.odds_pct != null ? '<div class="dtw-muted">Odds of clearing the goal: <b>' + pct(p.odds_pct) + '</b> (' + esc(p.odds_words || '') + ')</div>' : '') + '<div class="dtw-muted" style="margin-top:0.3rem;">' + esc(p.note || '') + '</div><div style="margin-top:0.5rem;">' + (p.channels || []).map(function(c) { return bar(c.channel, c.spend, Math.max.apply(null, (p.channels || []).map(function(x) { return x.spend; })), i === 1, fmtMoney(c.spend)); }).join('') + '</div><div style="margin-top:0.7rem;"><button type="button" class="dtw-btn dtw-btn-sm dtw-btn-primary" onclick="dtwSetLive(\'' + esc(r.id) + '\', \'' + esc(jsq(p.label)) + '\')">Set it live</button></div></div>';
                }).join('') + '</div>';
                out += card(r.question || 'Launch', 'Three plans side by side: as allocated, signal-led, and the minimum that still hits the goal. ' + runMeta(r), figs([[fmtN(o.expected) + ' ' + noun, 'Expected on the signal-led plan', '', true], [fmtN(o.low) + ' to ' + fmtN(o.high), 'Range'], [goal ? fmtN(goal) + ' ' + noun : 'none set', 'Goal'], [o.odds_pct != null ? pct(o.odds_pct) : '', 'Odds of clearing it']].filter(function(f) { return f[0] !== ''; })) + plans);
                out += card('Week by week on the signal-led plan', 'Expected with its low and high each week; the dashed line is the goal.', bandChart(o.weeks, goal) + '<table class="dtw-table" style="margin-top:0.5rem;"><tr><th>Week</th><th class="num">Expected</th><th class="num">Low</th><th class="num">High</th><th class="num">Odds</th></tr>' + (o.weeks || []).map(function(w) { return '<tr><td>' + w.week + '</td><td class="num">' + fmtN(w.expected) + '</td><td class="num">' + fmtN(w.low) + '</td><td class="num">' + fmtN(w.high) + '</td><td class="num">' + (w.odds_pct != null ? pct(w.odds_pct) : '') + '</td></tr>'; }).join('') + '</table>');
                var ho = o.holdout || {};
                out += '<div class="dtw-two">' + card('Levers, ranked by what they earn', 'Each one on its own, against the signal-led plan.', leverTable(o.levers, false)) + card('The holdout', 'The smallest group held out that still proves lift.', figs([[fmtN(ho.size), 'Matched people held out'], [fmtMoney(ho.spend), 'Lowest spend that proves lift'], [pct(ho.expected_lift_pct), 'Lift it can read']]) + '<p class="dtw-p">' + esc(ho.lift_read || '') + '</p>') + '</div>';
                out += readCard(r) + exportRow() + '</div>';
                h.innerHTML = out;
            }
            window.dtwSetLive = function(runId, label) { S.liveFrom = { run_id: runId, plan_label: label }; dtwTab('spend'); syncSpendSources(); if (el('dtwSpSource')) { el('dtwSpSource').value = 'run:' + runId + '|' + label; dtwSpendSourceChanged(); } if (el('dtwSpStatus')) el('dtwSpStatus').textContent = 'The ' + label + ' plan from Launch is loaded. Pick a mode and activate it.'; };

            // ------------------------------------------------------------ spend engine
            function syncSpendSources() {
                var src = el('dtwSpSource'); if (!src) return;
                var cur = src.value; var opts = '<option value="manual">Paste channels</option>';
                ((S.twin || {}).runs || []).filter(function(r) { return r.kind === 'launch'; }).forEach(function(r) { (((r.outputs || {}).plans) || []).forEach(function(p) { opts += '<option value="run:' + esc(r.id) + '|' + esc(p.label) + '">Launch ' + esc(fmtDate(r.created_at)) + ': ' + esc(p.label) + ' (' + fmtMoney(p.spend) + ')</option>'; }); });
                src.innerHTML = opts; if (cur) src.value = cur; if (!src.value) src.value = 'manual'; dtwSpendSourceChanged();
            }
            window.dtwSpendPickTwin = function() { syncSpendSources(); };
            window.dtwSpendSourceChanged = function() { var v = (el('dtwSpSource') || {}).value || 'manual'; if (el('dtwSpManual')) el('dtwSpManual').style.display = v === 'manual' ? 'block' : 'none'; };
            window.dtwSpendMode = function(mode) { S.spendMode = mode; el('dtwSpModeChip').classList.toggle('on', mode === 'chip'); el('dtwSpModeRule').classList.toggle('on', mode === 'rule'); el('dtwSpRule').style.display = mode === 'rule' ? 'block' : 'none'; };
            window.dtwSpendActivate = async function() {
                var key = S.key; if (!key) { el('dtwSpStatus').textContent = 'Pick a twin on the Twins tab first.'; return; }
                var body = { mode: S.spendMode, rule: { max_daily_shift_pct: parseFloat(el('dtwSpMax').value || '15'), floor_per_channel: parseFloat(el('dtwSpFloor').value || '0') } };
                var src = (el('dtwSpSource') || {}).value || 'manual';
                if (src.indexOf('run:') === 0) { var parts = src.slice(4).split('|'); body.from_run = parts[0]; body.plan_label = parts[1] || 'Signal-led'; body.plan = { assets: (el('dtwSpAssets').value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean) }; }
                else {
                    var chans = parseChannels(el('dtwSpChannels').value);
                    if (!chans.length) { el('dtwSpStatus').textContent = 'Add at least one channel with spend (Meta $60,000).'; return; }
                    body.plan = { channels: chans, window: { days: parseInt(el('dtwSpDays').value || '14', 10) || 14 }, assets: (el('dtwSpAssets').value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean) };
                    var g = parseFloat(String(el('dtwSpGoal').value || '').replace(/[,$]/g, '')); if (g) body.plan.goal = { metric: 'first actions', value: Math.round(g) };
                }
                el('dtwSpStatus').textContent = 'Running the plan forward day by day...';
                try { var d = await api('/' + key + '/spend/activate', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { el('dtwSpStatus').textContent = e.message; return; }
                el('dtwSpStatus').textContent = 'Activated.';
                await dtwOpen(key, null, null, d.plan.plan_id, 'spend');
            };
            window.dtwSpendDay = function(d) { S.spendDay = d; renderSpend(); };
            window.dtwSpendApprove = async function(day, moveId) {
                if (!S.plan) return;
                try { var d = await api('/' + S.key + '/spend/' + S.plan.plan_id + '/approve', { method: 'POST', body: JSON.stringify({ day: day, move_id: moveId }) }); S.plan = d.plan; replacePlan(); renderSpend(); } catch (e) { el('dtwSpStatus').textContent = e.message; }
            };
            window.dtwSpendSetMode = async function(mode) {
                if (!S.plan) return;
                var rule = { max_daily_shift_pct: parseFloat((el('dtwSpRuleMax') || el('dtwSpMax') || {}).value || '15'), floor_per_channel: parseFloat((el('dtwSpRuleFloor') || el('dtwSpFloor') || {}).value || '0') };
                try { var d = await api('/' + S.key + '/spend/' + S.plan.plan_id + '/mode', { method: 'POST', body: JSON.stringify({ mode: mode || S.plan.mode, rule: rule }) }); S.plan = d.plan; replacePlan(); renderSpend(); } catch (e) { el('dtwSpStatus').textContent = e.message; }
            };
            function replacePlan() { var plans = ((S.twin || {}).labs || {}).spend || []; for (var i = 0; i < plans.length; i++) { if (plans[i].plan_id === S.plan.plan_id) plans[i] = S.plan; } }
            window.dtwSpendHandoff = async function() {
                if (!S.plan) return;
                try { var d = await api('/' + S.key + '/spend/' + S.plan.plan_id + '/handoff'); chatFill('Set up Attribution IQ tracking. ' + (d.package || {}).text); } catch (e) { el('dtwSpStatus').textContent = e.message; }
            };
            window.dtwSpendPick = function(pid) { S.plan = (((S.twin || {}).labs || {}).spend || []).filter(function(p) { return p.plan_id === pid; })[0] || null; S.spendDay = 1; renderSpend(); };
            // Spend Engine results: settings row and the ledger figures, the day strip, the channel table, the moves, tests and the holdout, the read, exports.
            function renderSpend() {
                var h = el('dtwSpResults'); if (!h) return;
                var p = S.plan; if (!p) { renderEmpty('dtwSpResults', 'spend'); return; }
                var plans = ((S.twin || {}).labs || {}).spend || [];
                var led = p.ledger || {}, days = p.days || [], dayN = Math.min(Math.max(1, S.spendDay), days.length), day = days[dayN - 1] || {}, rule = p.rule || {};
                var settings = '<div class="dtw-settings">' + (plans.length > 1 ? '<span class="k">Plan</span><select class="dtw-in" style="max-width:280px; width:auto; padding:0.3rem 0.5rem; font-size:0.76rem;" onchange="dtwSpendPick(this.value)">' + plans.map(function(x) { return '<option value="' + esc(x.plan_id) + '"' + (x.plan_id === p.plan_id ? ' selected' : '') + '>' + esc(x.label) + '</option>'; }).join('') + '</select>' : '') + '<span class="k">Mode</span><div class="dtw-toggle"><button type="button" class="' + (p.mode !== 'rule' ? 'on' : '') + '" onclick="dtwSpendSetMode(\'chip\')">Approve each move</button><button type="button" class="' + (p.mode === 'rule' ? 'on' : '') + '" onclick="dtwSpendSetMode(\'rule\')">On a rule</button></div><span class="k">Max daily shift %</span><input id="dtwSpRuleMax" class="dtw-in" value="' + esc(Number(rule.max_daily_shift_pct || 15).toFixed(0)) + '"><span class="k">Floor per channel $</span><input id="dtwSpRuleFloor" class="dtw-in" value="' + esc(Number(rule.floor_per_channel || 0).toFixed(0)) + '"><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwSpendSetMode(\'' + (p.mode === 'rule' ? 'rule' : 'chip') + '\')">Apply the rule</button></div>';
                var out = card(p.label, days.length + ' days, ' + (p.source === 'launch' ? 'from Launch' : 'a pasted plan') + (p.platform ? ', on ' + p.platform : '') + '. ' + (p.mode === 'rule' ? 'On a rule: moves inside it approve on their own.' : 'You approve each move.'), settings + figs([[fmtMoney(led.saved_total), 'Saved against the plan as allocated', '', true], [String(led.moves_approved || 0) + ' of ' + String(led.moves_proposed || 0), 'Moves approved'], [fmtN(led.signal_led_outcome), 'First actions, with the moves'], [fmtN(led.as_allocated_outcome), 'First actions, as allocated'], [fmtMoney(led.signal_led_cpa), 'Cost per first action now', 'was ' + fmtMoney(led.as_allocated_cpa)]]));
                var strip = '<div class="dtw-days">' + days.map(function(d) { var pend = (d.moves || []).some(function(m) { return !m.approved; }); return '<div class="dtw-day' + (d.day === dayN ? ' on' : '') + (pend ? ' pend' : '') + '" onclick="dtwSpendDay(' + d.day + ')" title="Day ' + d.day + ': ' + fmtN(d.first_actions) + ' first actions"><div class="d">' + d.day + '</div><div>' + fmtN(d.first_actions) + '</div></div>'; }).join('') + '</div>';
                var table = '<table class="dtw-table"><tr><th>Channel</th><th class="num">Spend</th><th class="num">Reach</th><th class="num">First actions</th><th class="num">Cost per first action</th></tr>' + (day.by_channel || []).map(function(b) { return '<tr><td>' + esc(b.channel) + (b.momentum && Math.abs(b.momentum - 1) > 0.01 ? ' <span class="dtw-muted">momentum x' + Number(b.momentum).toFixed(2) + '</span>' : '') + '</td><td class="num">' + fmtMoney(b.spend) + '</td><td class="num">' + fmtN(b.reach) + '</td><td class="num">' + fmtN(b.first_actions) + '</td><td class="num">' + fmtMoney(b.cpa) + '</td></tr>'; }).join('') + '</table>';
                out += card('Day by day', 'Pick a day to see its channels and moves. A dot marks a day with moves waiting.', strip + '<h4>Day ' + dayN + ' by channel</h4>' + table);
                var moves = (day.moves || []).length ? (day.moves || []).map(function(m) { return '<div class="dtw-move' + (m.approved ? ' ok' : '') + '"><span class="w">' + esc(m.kind === 'asset' ? 'Swap the lead asset' : m.kind === 'raise' ? 'Raise ' + m.to : 'Move ' + fmtMoney(m.amount) + ' a day from ' + m.from + ' to ' + m.to) + '</span> <span class="dtw-muted">earns about ' + fmtN(m.delta) + ' first actions</span><div>' + esc(m.why) + '</div>' + (m.approved ? '<div class="dtw-muted">Approved by ' + esc(m.approved_by || 'you') + ', ' + esc(fmtWhen(m.approved_at)) + '</div>' : '<div style="margin-top:0.35rem;"><button type="button" class="dtw-chip on" onclick="dtwSpendApprove(' + day.day + ', \'' + esc(m.id) + '\')">Approve</button></div>') + '</div>'; }).join('') : '<p class="dtw-muted">No moves this day.</p>';
                out += card('Moves on day ' + dayN, 'Each move with what it earns. Approving writes it to the ledger and the moves list for your buyers.', moves);
                var ho = p.holdout || {};
                out += '<div class="dtw-two">' + card('Tests to run inside the flight', 'A/B tests the first days can settle.', (p.tests || []).map(function(t) { return '<div class="dtw-run" style="cursor:default;"><div class="q">' + esc(t.name) + '</div><div class="m">' + esc(t.what) + ' Split: ' + esc(t.split) + '. ' + t.days + ' days. ' + esc(t.why) + '</div></div>'; }).join('') || '<p class="dtw-muted">No tests proposed.</p>') + card('Lift read against the holdout', 'The smallest group held out that still proves lift.', figs([[fmtN(ho.size), 'Matched people held out'], [fmtMoney(ho.spend), 'Lowest spend that proves lift'], [pct(ho.expected_lift_pct), 'Lift it can read']]) + '<p class="dtw-p">' + esc(ho.lift_read || '') + '</p>') + '</div>';
                out += card('The read', 'Where the plan stands today.', '<p class="dtw-p">' + esc(led.read || '') + '</p>');
                out += card('Export and hand off', 'The approved moves for your buyers; Attribution IQ measures the plan.', '<div class="dtw-actions"><a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/spend/' + esc(p.plan_id) + '/moves.csv">Export moves (CSV)</a><button type="button" class="dtw-btn" onclick="dtwSpendHandoff()">Hand off to Attribution IQ</button></div>');
                h.innerHTML = out;
            }
            window.dtwConnectRequest = async function() {
                var plats = (el('dtwConnectPlatforms').value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean);
                try { var d = await api('/connect-request', { method: 'POST', body: JSON.stringify({ platforms: plats, twin_key: S.key }) }); el('dtwConnectStatus').textContent = d.message || 'Request filed.'; } catch (e) { el('dtwConnectStatus').textContent = e.message; }
            };

            // ------------------------------------------------------- focus group iq (panel): option F
            // The console as Jenna approved it on 2026-10-27 ('love it go for it'): the room's seats as a portrait band under the title and
            // the question (E), the room as a conversation thread with the composer under it (C), the tally pinned on the right, a seat's bio
            // as a popover under its portrait, the saved rooms behind Rooms and the room builder behind New room. Every call the console made
            // before is kept: open, resume, ask (with the identical-text guard, the duplicate note and the failed line), delete with its
            // confirm, save, export, the job poll every 4 s while a question is in flight, the 20 s / 75 s portrait re-reads.
            var KINDS = [['yes_no', 'Yes / no'], ['multiple_choice', 'Multiple choice'], ['rating', 'Rate 1 to 5'], ['price', 'Would you pay $X'], ['ab', 'A vs B'], ['show_of_hands', 'Show of hands'], ['open', 'Open question'], ['probe', 'Probe']];
            var KIND_WORDS = { yes_no: 'Yes / no', multiple_choice: 'Multiple choice', rating: 'Rate 1 to 5', price: 'Price', ab: 'A vs B', show_of_hands: 'Show of hands', open: 'Open question', probe: 'Probe', asset_read: 'Content read' };
            // The five series: the lead answer (by published share) in Signal Green, then Orchid, Cobalt, Dusk, Pavement for the rest;
            // on a light page the Signal and Orchid twins (Olive, Amethyst) take over through the view's variables.
            var SERIES = ['var(--dtw-s1)', 'var(--dtw-s2)', 'var(--dtw-s3)', 'var(--dtw-s4)', 'var(--dtw-s5)'];
            var STD_PROBES = [['Why?', 'Why?'], ['What would change your mind?', 'What would change your mind?'], ['Show of hands', 'Show of hands, who watched the last one?']];
            var ORD = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth', 'eleventh', 'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth', 'seventeenth', 'eighteenth', 'nineteenth', 'twentieth', 'twenty-first', 'twenty-second', 'twenty-third', 'twenty-fourth'];
            var MARK_SVG = '<svg viewBox="0 0 828 822" aria-hidden="true"><path d="M178.5,821.5 19.5,821.4 15.5,820.8 12.5,819.7 8.8,817.5 5.4,814.5 3.1,811.5 1.2,807.5 0.2,802.5 0.3,798.5 198.2,24.5 201.2,14.5 202.2,12.5 204.3,9.5 208.5,5.2 213.5,2.2 216.5,1.2 219.5,0.7 331.5,0.7 334.5,1.0 338.5,2.2 342.5,4.3 345.5,6.9 347.8,9.5 349.5,12.5 350.9,16.5 351.5,21.5 350.9,26.5 214.2,780.5 209.9,805.5 208.6,809.5 206.9,812.5 203.2,816.5 199.5,819.0 195.5,820.7 193.5,821.0 186.5,821.5ZM498.5,821.5 328.5,821.5 324.5,820.9 321.5,819.8 316.5,816.6 314.4,814.5 312.1,811.5 310.2,807.5 309.3,803.5 309.2,799.5 310.2,791.5 428.7,51.5 431.2,33.5 433.2,23.5 435.2,16.5 437.3,11.5 439.3,8.5 441.5,6.2 445.5,3.3 447.5,2.3 452.5,1.0 455.5,0.7 570.5,0.7 573.5,1.4 576.5,2.9 578.5,4.2 581.7,7.5 582.9,9.5 584.5,13.5 584.9,15.5 585.0,19.5 523.3,759.5 520.2,796.5 520.0,803.5 519.7,805.5 517.7,810.5 515.7,813.5 512.5,816.7 509.5,818.7 506.5,820.0 503.5,821.0ZM803.5,821.5 636.5,821.5 632.5,820.9 629.5,819.9 625.5,817.8 623.9,816.5 621.0,813.5 619.0,810.5 618.1,808.5 617.0,804.5 616.7,799.5 617.7,789.5 667.3,28.5 668.1,22.5 669.1,18.5 671.2,13.5 674.5,8.6 676.5,6.4 680.5,3.5 685.5,1.4 690.5,0.3 697.5,0.3 701.5,0.8 801.5,0.6 805.5,1.0 809.5,2.2 811.5,3.3 814.5,5.4 816.7,7.5 818.9,10.5 819.9,12.5 820.8,14.5 821.7,18.5 822.1,24.5 822.6,81.5 827.2,771.5 827.4,797.5 827.7,801.5 826.8,806.5 825.7,809.5 823.9,812.5 820.1,816.5 818.5,817.7 814.5,819.8 810.5,820.9Z"/></svg>';
            var SEP = '<span class="dtw-fg-sep">&middot;</span>';
            S.fg = S.fg || { sid: null, open: {}, earlier: {}, seat: null, tallyQ: null };
            S.fg.vShown = S.fg.vShown || {};
            function modav() { return '<span class="dtw-fg-modav">' + MARK_SVG + '</span>'; }
            function fgReset(sid) { if (S.fg.sid !== sid) { S.fg.sid = sid; S.fg.open = {}; S.fg.earlier = {}; S.fg.vShown = {}; S.fg.seat = null; S.fg.tallyQ = null; } }
            function syncFgBody() {
                var room = S.tab === 'panel' && !!S.session;
                var view = el('digitalTwinsIQView');
                if (view) {
                    view.classList.toggle('is-fg-room', room);
                    // theater (Jenna 2026-11-18): a Predictive Analytics room running its planned questions is watched, not steered
                    view.classList.toggle('is-theater', room && paRunning(S.session));
                    if (!room) view.style.removeProperty('--dtw-main-h');
                }
                var b = document.querySelector('#digitalTwinsIQView .dtw-body');
                if (b) {
                    b.classList.toggle('is-fg', S.tab === 'panel');
                    b.classList.toggle('is-fg-room', room);
                }
                syncFgActs();
                if (room) fgDockSize();
            }
            function ordWord(n) { n = Number(n || 0); return ORD[n - 1] || (n + 'th'); }
            function kindWord(k) { return KIND_WORDS[String(k || '')] || cap(String(k || '').replace(/_/g, ' ')); }
            function roomTitle(s) { var t = String((s || {}).title || '').replace(/\s+/g, ' ').trim(); return t || 'Room'; }
            function seatsTotal(q) { return Number((q || {}).seats_total || ((S.session || {}).group || {}).in_room || (((S.session || {}).group || {}).roster || []).length || 0); }
            function roomStandsForLine(g) {
                // what the room stands for when it was cut (Jenna 2026-11-05: 'the room would need to be sized accordingly'): the
                // server's one plain line, 'Standing for the 4 groups you chose: 9.6M of the 83.8M people in this audience.'
                var sz = (g || {}).sizing || {};
                return sz && sz.line ? String(sz.line) : '';
            }
            function roomStandsForNote(g, cls) {
                var line = roomStandsForLine(g);
                return line ? '<div class="' + (cls || 'dtw-pi-fn') + '">' + esc(line) + '</div>' : '';
            }
            function perSeat(g, r) {
                var seg = r ? ((g || {}).segments || []).filter(function(x) { return x.label === r.segment; })[0] : null;
                if (seg && seg.people && seg.seats) return Math.round(Number(seg.people) / Number(seg.seats));
                var n = Number((g || {}).in_room || ((g || {}).roster || []).length || 0); return n ? Math.round(Number((g || {}).size_people || 0) / n) : 0;
            }
            // who the seat stands for (Jenna 2026-11-05: 'on the bios it needs to say what this person represents, like
            // represents x number of people from the total'): the figures the server put on the seat (stands_for_people,
            // stands_for_share), else, on a record from before them, the segment's share over its seats, moved off a
            // round figure by the seat's index; the total is the one the room prints
            function seatStandsFor(g, r) {
                g = g || {}; r = r || {};
                var roster = g.roster || [], segs = g.segments || [], sum = function(a, x) { return a + (Number(x.people) || 0); };
                var total = Number(g.size_people || 0) || segs.reduce(sum, 0) || roster.reduce(sum, 0);
                var people = Number(r.stands_for_people || 0), share = r.stands_for_share;
                if (!(people > 0)) {
                    people = perSeat(g, r) || 0; share = null;
                    var idx = roster.indexOf(r); if (idx < 0) idx = Math.max(0, Number(r.seat || 1) - 1);
                    if (people > 0 && people % 10 === 0) people += (idx % 9) + 1;
                }
                if (share == null || !isFinite(Number(share))) share = (total > 0 && people > 0) ? Math.round(people / total * 1000) / 10 : null;
                return { people: people, share: share, total: total };
            }
            // an archetype representative who sat in earlier focus groups on this twin (Jenna 2026-11-20): the twin carries the
            // count per seat for this viewer (earlier_groups[session id][seat]); the card prints the one line
            function earlierGroupsLine(sid, seat) {
                var eg = ((S.twin || {}).earlier_groups || {})[String(sid || '')] || {}, rec = eg[String(seat)];
                return rec && rec.line ? String(rec.line) : '';
            }
            function standsForLine(nm, g, r) {
                var sf = seatStandsFor(g, r); if (!(sf.people > 0) || !(sf.total > 0)) return '';
                return esc(nm) + ' stands for ' + fmtN(sf.people) + ' of the ' + fmtN(sf.total) + ' people in this audience' + (sf.share != null ? ' (' + Number(sf.share).toFixed(1) + '%)' : '') + '.';
            }
            // ---- the question in large type: the last clause carries the weight (E's weight pair), only when it reads as a clause.
            function weightPair(text) {
                var t = String(text || '').trim(); if (!t) return '';
                var re = /,\s+|\s+(if|or|when|before|after|for|unless|while)\s+/gi, m, best = null;
                while ((m = re.exec(t))) {
                    var head = t.slice(0, m.index), tail = t.slice(m.index + m[0].length);
                    var tw = tail.split(/\s+/).filter(Boolean).length, hw = head.split(/\s+/).filter(Boolean).length;
                    if (tw >= 2 && tw <= 9 && hw >= 3) best = { head: head, kw: m[1] || '', tail: tail };
                }
                if (!best) return esc(t);
                return best.kw ? esc(best.head) + ' <b>' + esc(best.kw) + ' ' + esc(best.tail) + '</b>' : esc(best.head) + ', <b>' + esc(best.tail) + '</b>';
            }
            function readHtml(text) { var t = String(text || '').trim(); var m = /^([^.!?:]{8,140}[.!?:])\s+([\s\S]+)$/.exec(t); return m ? '<b>' + esc(m[1]) + '</b> ' + esc(m[2]) : esc(t); }
            // ---- the room at work: a question answers as a job over a minute or two; the page shows the stage and polls until it lands.
            function normQ(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9$ ]+/g, ' ').replace(/\s+/g, ' ').trim(); }
            var IN_FLIGHT = { 'researching': 1, 'the room is thinking': 1, 'moderator is reading': 1, 'picking it back up': 1 };
            function qInFlight(q) { var st = String((q || {}).stage || ''); return !!IN_FLIGHT[st] && !(q || {}).finished_at; }
            function qFailed(q) { return String((q || {}).stage || '') === 'failed'; }
            function qElapsedS(q) { var t = Date.parse(String((q || {}).started_at || (q || {}).asked_at || '')); if (!isFinite(t)) return 0; return Math.max(0, Math.round((Date.now() - t) / 1000)); }
            function qTookS(q) { var a = Date.parse(String((q || {}).started_at || (q || {}).asked_at || '')), b = Date.parse(String((q || {}).finished_at || '')); if (!isFinite(a) || !isFinite(b) || b < a) return null; return Math.round((b - a) / 1000); }
            function stageWords(q) {
                var st = String(q.stage || ''), n = (q.answers || []).length, seats = seatsTotal(q);
                if (st === 'researching') return 'getting the facts on the question';
                if (st === 'the room is thinking') return 'the twins are answering' + (seats ? ', ' + n + ' of ' + seats : '');
                if (st === 'moderator is reading') return 'Prometheus is reading the room';
                if (st === 'picking it back up') return 'picking the question back up';
                return st.replace(/_/g, ' ');
            }
            function stageLead(q) {
                var st = String(q.stage || ''), n = (q.answers || []).length, seats = seatsTotal(q);
                if (st === 'researching') return '<b>Getting the facts on the question</b>';
                if (st === 'the room is thinking') return '<b>The twins are answering' + (seats ? ',</b> ' + n + ' of ' + seats : '</b>');
                if (st === 'moderator is reading') return '<b>Prometheus is reading the room</b>';
                if (st === 'picking it back up') return '<b>Picking the question back up</b>';
                return '<b>' + esc(cap(st.replace(/_/g, ' '))) + '</b>';
            }
            function workingLine(q) {
                var e = qElapsedS(q), slow = e > 300;
                return '<div class="dtw-fg-working' + (slow ? ' slow' : '') + '" data-qid="' + esc(q.id || '') + '"><span class="dtw-fg-pulse"></span><span>' + (slow ? 'This one is taking longer than usual; it will finish in the background.' : 'The room is working: ' + esc(stageWords(q)) + ' &middot; <span class="el">' + e + '</span>s') + '</span></div>';
            }
            function anyInFlight() { return ((S.session || {}).questions || []).some(qInFlight); }
            function flyQ() { var qs = (S.session || {}).questions || []; for (var i = qs.length - 1; i >= 0; i--) { if (qInFlight(qs[i])) return qs[i]; } return null; }
            // The question the band and the tally stand on: the latest one that finished (the last read), else the latest one at all.
            function focusQ() {
                var qs = (S.session || {}).questions || [];
                for (var i = qs.length - 1; i >= 0; i--) { if (!qInFlight(qs[i]) && !qFailed(qs[i])) return qs[i]; }
                for (var j = qs.length - 1; j >= 0; j--) { if (!qInFlight(qs[j])) return qs[j]; }
                return qs.length ? qs[qs.length - 1] : null;
            }
            function qId(q, i) { return (q && q.id) ? String(q.id) : ('i' + i); }
            function tallyFocusQ() {
                var qs = (S.session || {}).questions || [];
                if (S.fg.tallyQ) {
                    for (var i = 0; i < qs.length; i++) { if (qId(qs[i], i) === String(S.fg.tallyQ)) return qs[i]; }
                }
                return focusQ();
            }
            function setAskEnabled(on) { var b = el('dtwPnAskBtn'); if (!b) return; var busy = !on || anyInFlight() || paRunning(S.session); b.disabled = busy; b.classList.toggle('working', busy); b.classList.toggle('dtw-busy', busy); if (busy) b.setAttribute('aria-busy', 'true'); else b.removeAttribute('aria-busy'); b.innerHTML = busy ? dtwBusyHtml(S.asking ? 'Asking the room' : 'Working') : 'Ask the room<span class="arr"></span>'; }
            function clearRoomPoll() { if (S.roomPollTimer) { clearTimeout(S.roomPollTimer); S.roomPollTimer = null; } if (S.roomTick) { clearInterval(S.roomTick); S.roomTick = null; } }
            function adoptQuestion(fq, force) {
                if (!fq || !fq.id || !S.session) return false;
                var qs = S.session.questions = S.session.questions || [];
                for (var i = 0; i < qs.length; i++) {
                    if (qs[i].id !== fq.id) continue;
                    var cq = qs[i];
                    var moved = force || cq.stage !== fq.stage || (cq.answers || []).length !== (fq.answers || []).length || (!!cq.read) !== (!!fq.read) || (cq.distribution || []).length !== (fq.distribution || []).length || (cq.options || []).length !== (fq.options || []).length || (cq.note || '') !== (fq.note || '') || (cq.facts_used || []).length !== (fq.facts_used || []).length;
                    if (!moved) return false;
                    if (!fq.job_id && cq.job_id) fq.job_id = cq.job_id;
                    qs[i] = fq; return true;
                }
                qs.push(fq); return true;
            }
            function scheduleRoomPoll() {
                clearRoomPoll();
                var s = S.session; if (!s || !S.key) { setAskEnabled(true); return; }
                setAskEnabled(true);
                if (!anyInFlight() && !paRunning(s)) return;
                var sid = s.id, key = S.key;
                S.roomTick = setInterval(function() {
                    if (!S.session || S.session.id !== sid || S.key !== key) { clearRoomPoll(); return; }
                    (S.session.questions || []).forEach(function(q) {
                        if (!qInFlight(q)) return;
                        var e = qElapsedS(q);
                        var w = document.querySelector('#dtwPnTranscript .dtw-fg-working[data-qid="' + (q.id || '') + '"]');
                        if (w) { if (e > 300 && !w.classList.contains('slow')) { w.outerHTML = workingLine(q); } else { var sp = w.querySelector('.el'); if (sp) sp.textContent = e; } }
                        var h = el('dtwFgStageEl'); if (h) h.textContent = e;
                        var th = el('dtwFgTheaterEl'); if (th) th.textContent = e;
                    });
                }, 1000);
                var tick = async function() {
                    S.roomPollTimer = null;
                    if (!S.session || S.session.id !== sid || S.key !== key) { clearRoomPoll(); return; }
                    var open = (S.session.questions || []).filter(qInFlight);
                    if (!open.length && !paRunning(S.session)) { clearRoomPoll(); setAskEnabled(true); return; }
                    var changed = false, wasRunning = paRunning(S.session);
                    try {
                        var d = await api('/' + key + '/panel/' + sid);
                        ((d.session || {}).questions || []).forEach(function(fq) { if (adoptQuestion(fq)) changed = true; });
                        if (d.session && d.session.guided) { var og = S.session.guided || {}; if (og.status !== d.session.guided.status || !!og.outcome !== !!d.session.guided.outcome || og.done !== d.session.guided.done) changed = true; S.session.guided = d.session.guided; }
                        if (d.session && d.session.updated_at) S.session.updated_at = d.session.updated_at;
                        if (d.session && d.session.questions_paid != null) S.session.questions_paid = d.session.questions_paid;
                        var freshR = ((d.session || {}).group || {}).roster || [], curR = (S.session.group || {}).roster || [];
                        freshR.forEach(function(fr) { var cur = curR.filter(function(r) { return r.seat === fr.seat; })[0]; if (!cur) return; ['persona', 'memory', 'portrait_url', 'portrait_hash'].forEach(function(k) { if (fr[k] != null && JSON.stringify(cur[k]) !== JSON.stringify(fr[k])) { cur[k] = fr[k]; changed = true; } }); });
                    } catch (_) {}
                    // the seats land one by one on the job document; the session only moves at the stage boundaries
                    for (var i = 0; i < open.length; i++) {
                        var q = open[i]; if (!q.job_id) continue;
                        try { var j = await api('/jobs/' + q.job_id); var jq = ((j || {}).job || {}).question; if (jq && jq.id) { if (!jq.job_id) jq.job_id = q.job_id; if (adoptQuestion(jq)) changed = true; } } catch (_) {}
                    }
                    if (!S.session || S.session.id !== sid) { clearRoomPoll(); return; }
                    if (changed) {
                        fgRefresh();
                        if (wasRunning && !paRunning(S.session) && S.session.guided && S.session.guided.outcome) fgToast('The outcome is in. The report is ready on Predictive Analytics.');
                        if (S.fg.seat != null) renderSeatPanel();
                        var ss = (S.twin || {}).panel_sessions || []; for (var k = 0; k < ss.length; k++) { if (ss[k].id === sid) ss[k] = S.session; }
                        renderPanelSessions();
                    }
                    var still = (S.session.questions || []).filter(qInFlight);
                    if (!still.length && !paRunning(S.session)) { clearRoomPoll(); setAskEnabled(true); return; }
                    var slow = still.length && still.every(function(x) { return qElapsedS(x) > 300; });
                    S.roomPollTimer = setTimeout(tick, slow ? 20000 : 4000);
                };
                S.roomPollTimer = setTimeout(tick, 4000);
            }
            // ---- portraits: the seat's photo when it has one, an initials circle in a fixed hue when it does not.
            function seatHue(seat, i) { return hueFor(seat && seat.seat != null ? Number(seat.seat) - 1 : i); }
            function thumb(url) { url = String(url || ''); if (!url || /[?&]size=/.test(url)) return url; return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'size=128'; }
            function face(seat, i, size) {
                var hue = seatHue(seat, i), nm = titleCase((seat || {}).name || ''), url = (seat || {}).portrait_url || '';
                var src = url ? (size === 64 ? url : thumb(url)) : '';
                var open = seat && seat.seat != null ? ' data-fg-seat="' + Number(seat.seat) + '" onclick="dtwSeatOpen(' + Number(seat.seat) + ', this)"' : '';
                return '<button type="button" class="dtw-fg-faceb' + (size ? ' s' + size : '') + (seat && seat.seat != null && S.fg.seat === Number(seat.seat) ? ' on' : '') + '" style="--hue:' + hue + '" title="Who ' + esc(nm) + ' is" aria-label="Who ' + esc(nm) + ' is"' + open + '><span>' + esc(initials(nm)) + '</span>' + (src ? '<img src="' + esc(src) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</button>';
            }
            function multiSeg(roster) { var seen = {}; (roster || []).forEach(function(r) { seen[r.segment || ''] = 1; }); return Object.keys(seen).length > 1; }
            function seatFor(voice, roster) {
                roster = roster || [];
                if (voice && voice.seat != null) { var bySeat = roster.filter(function(r) { return Number(r.seat) === Number(voice.seat); })[0]; if (bySeat) return bySeat; }
                var hit = roster.filter(function(r) { return r.name === voice.name && String(r.age) === String(voice.age) && r.city === voice.city; })[0];
                if (!hit) hit = roster.filter(function(r) { return r.name === voice.name && String(r.age) === String(voice.age); })[0];
                if (!hit) hit = roster.filter(function(r) { return r.name === voice.name; })[0];
                return hit || null;
            }
            // ---- the options and their series: the lead answer by published share takes the first color, then the order of the rest.
            function optObjs(q) { var o = (q || {}).options || []; return (o.length && o[0] && typeof o[0] === 'object') ? o : []; }   // older records carry the options as plain strings
            function optsOf(q) {
                var o = optObjs(q); if (o.length) return o;
                return ((q || {}).distribution || []).map(function(d) { return { id: d.label, label: d.label, pct: d.pct, people: d.people }; });
            }
            function optionIndex(q, optId) { var opts = optsOf(q); for (var i = 0; i < opts.length; i++) { if (String(opts[i].id) === String(optId)) return i; } return -1; }
            function optionLabelOf(q, optId) { var i = optionIndex(q, optId); return i >= 0 ? (optsOf(q)[i].label || '') : ''; }
            function answerOptId(q, a) {
                if (!a) return '';
                if (optionIndex(q, a.option) >= 0) return String(a.option);
                var lab = a.option_label || a.option || '', opts = optsOf(q);
                for (var i = 0; i < opts.length; i++) { if (String(opts[i].label) === String(lab)) return String(opts[i].id); }
                return '';
            }
            function liveCounts(q) { var c = {}; ((q || {}).answers || []).forEach(function(a) { var id = answerOptId(q, a); if (id) c[id] = (c[id] || 0) + 1; }); return c; }
            function rankMap(q) {
                var opts = optsOf(q), idx = opts.map(function(o, i) { return i; });
                var anyPct = opts.some(function(o) { return Number(o.pct || 0) > 0; }), counts = anyPct ? null : liveCounts(q);
                idx.sort(function(a, b) {
                    var va = anyPct ? Number(opts[a].pct || 0) : (counts[String(opts[a].id)] || 0), vb = anyPct ? Number(opts[b].pct || 0) : (counts[String(opts[b].id)] || 0);
                    return vb !== va ? vb - va : a - b;
                });
                var m = {}; idx.forEach(function(oi, rank) { m[String(opts[oi].id)] = Math.min(rank, 4); }); return m;
            }
            function seriesOf(rk, optId) { var r = rk[String(optId)]; return r == null ? SERIES[4] : SERIES[r]; }
            function edgeOf(rk, optId) { var r = rk[String(optId)]; return r == null ? '' : SERIES[r]; }
            function optTag(q, rk, optId, label) { var lab = label || optionLabelOf(q, optId); if (!lab) return ''; return '<span class="dtw-fg-tag"><span class="dtw-fg-dot" style="background:' + seriesOf(rk, optId) + '"></span>' + esc(lab) + '</span>'; }
            function stack(parts, cls) {
                // parts: [{pct, color}] in option order; the remainder of 100 stays the Pavement track
                return '<div class="dtw-fg-stack' + (cls ? ' ' + cls : '') + '">' + parts.filter(function(p) { return Number(p.pct) > 0; }).map(function(p) { return '<i style="width:' + Math.max(0, Math.min(100, Number(p.pct))).toFixed(1) + '%;background:' + p.color + '"></i>'; }).join('') + '</div>';
            }
            function questionStack(q, cls) {
                var opts = optsOf(q), rk = rankMap(q), live = qInFlight(q), counts = live ? liveCounts(q) : null, seats = seatsTotal(q);
                // while the room is still answering the bar is in motion (a shimmer), so it never reads as the published split
                return stack(opts.map(function(o) { var v = live ? (seats ? (counts[String(o.id)] || 0) / seats * 100 : 0) : Number(o.pct || 0); return { pct: v, color: seriesOf(rk, o.id) }; }), (cls || '') + (live ? ' live' : ''));
            }
            // A row of the split while the room is still answering (Jenna 2026-11-18: "if it's still tallying it shouldn't show %'s
            // because it's confusing ... have those pinwheel or something or animate so you know they are being worked on until
            // they are final"): the seat tally so far, a pulsing dot, and a spinner where the percent lands once the count publishes.
            function liveRow(o, count, seats, color, lead) {
                var tally = (count || 0) + (seats ? ' of ' + seats : '') + ' so far';
                return '<div class="dtw-fg-lrow live' + (lead ? ' lead' : '') + '"><span class="dtw-fg-dot" style="background:' + color + '"></span><div class="body"><div class="l1"><span class="lab">' + esc(o.label) + '</span><span class="det">' + tally + '<span class="dtw-fg-rowspin" aria-label="Still tallying" title="Still tallying"></span></span></div></div></div>';
            }
            function liveHead(nA, seats, sub) {
                return '<div class="dtw-fg-th live"><span class="t"><span class="dtw-fg-pulse"></span>The split so far.</span><span>' + nA + (seats ? ' of ' + seats : '') + ' answered' + (sub ? SEP + sub : '') + '</span></div>';
            }
            function questionLolli(q) {
                var opts = optsOf(q), rk = rankMap(q), live = qInFlight(q), counts = live ? liveCounts(q) : null, seats = seatsTotal(q);
                if (!opts.length) return '';
                var rows = opts.map(function(o) { var v = live ? (seats ? (counts[String(o.id)] || 0) / seats * 100 : 0) : Number(o.pct || 0); return { o: o, v: Math.max(0, Math.min(100, v)), r: rk[String(o.id)] }; });
                rows.sort(function(a, b) { return (a.r == null ? 99 : a.r) - (b.r == null ? 99 : b.r); });
                return '<div class="dtw-fg-lolli">' + rows.map(function(x) {
                    var c = seriesOf(rk, x.o.id), w = x.v.toFixed(1) + '%';
                    return '<span class="lb" title="' + esc(x.o.label) + '">' + esc(x.o.label) + '</span><span class="tr"><span class="ln" style="width:' + w + ';background:' + c + '"></span><span class="dt" style="left:' + w + ';background:' + c + '"></span></span><span class="vl">' + Math.round(x.v) + '%</span>';
                }).join('') + '</div>';
            }
            // ---- the band: the seats as a row of circles (ring in the answer's color once answered, the check or the hollow mark while a
            // question runs, name and 'age, city' under each), then the room title, the question and the meta or stage line.
            function seatCircle(r, i, opts) {
                opts = opts || {};
                var nm = titleCase(r.name || ''), who = nm + (r.age != null && r.age !== '' ? ', ' + r.age : '') + (r.city ? ', ' + titleCase(r.city) : '');
                var img = r.portrait_url ? '<img src="' + esc(r.portrait_url) + '" alt="" onerror="this.remove()">' : '';
                var circ = '<span class="dtw-fg-circ"><span class="ini">' + esc(initials(nm)) + '</span>' + img + (opts.mark || '') + '</span>';
                if (opts.still) return '<span class="dtw-fg-seat" style="--hue:' + seatHue(r, i) + '" title="' + esc(who) + '">' + circ + '</span>';
                return '<button type="button" class="dtw-fg-seat' + (opts.pending ? ' pending' : '') + (S.fg.seat === Number(r.seat) ? ' sel' : '') + '" data-seat="' + Number(r.seat) + '" data-fg-seat="' + Number(r.seat) + '" style="--hue:' + seatHue(r, i) + ';--edge:' + (opts.edge || 'transparent') + '" title="Who ' + esc(nm) + ' is" aria-label="Who ' + esc(nm) + ' is" onclick="dtwSeatOpen(' + Number(r.seat) + ', this)">' + circ + '<span class="nm">' + esc(nm) + '</span><span class="ag">' + esc(r.age != null ? r.age : '') + (r.city ? ', ' + esc(titleCase(r.city)) : '') + '</span></button>';
            }
            function heroHtml(s, g, roster, fq, fly) {
                var rk = fq ? rankMap(fq) : {}, bySeat = {}, flyBySeat = {};
                if (fq) (fq.answers || []).forEach(function(a) { bySeat[String(a.seat)] = a; });
                if (fly) (fly.answers || []).forEach(function(a) { flyBySeat[String(a.seat)] = a; });
                var seats = roster.map(function(r, i) {
                    var a = bySeat[String(r.seat)], landed = !!flyBySeat[String(r.seat)];
                    return seatCircle(r, i, { edge: a ? edgeOf(rk, answerOptId(fq, a)) : '', mark: fly ? (landed ? '<span class="mark"></span>' : '<span class="mark wait"></span>') : '', pending: !!fly && !landed });
                }).join('');
                return '<div class="dtw-fg-seatrow" role="list">' + seats + '</div>';
            }
            function dockHtml(s, g, roster, fq, fly) {
                var meta, qsAll = s.questions || [];
                if (fly && (!fq || fly === fq)) {
                    meta = '<span class="dtw-fg-pulse"></span><span>' + stageLead(fly) + SEP + '<span id="dtwFgStageEl">' + qElapsedS(fly) + '</span>s</span>';
                } else if (fq) {
                    var n = qsAll.indexOf(fq) + 1, took = qTookS(fq), nA = (fq.answers || []).length, total = seatsTotal(fq);
                    meta = '<span>Question ' + n + (fq.kind ? SEP + esc(kindWord(fq.kind)) : '');
                    if (qFailed(fq)) meta += SEP + 'did not finish';
                    else meta += SEP + '<b>' + nA + (total ? ' of ' + total : '') + '</b> answered' + (took != null ? ' in ' + took + 's' : '');
                    meta += SEP + fmtN(g.in_room || roster.length) + ' twins standing for <b>' + fmtN(g.size_people) + '</b> people' + (roomStandsForLine(g) ? SEP + esc(roomStandsForLine(g)) : '') + (s.updated_at ? SEP + 'Saved ' + esc(fmtDay(s.updated_at)) : '') + '</span>';
                    if (fly) meta += SEP + '<span class="dtw-fg-pulse"></span><span>Question ' + (qsAll.indexOf(fly) + 1) + ' is working</span>';
                } else {
                    meta = '<span>' + fmtN(g.in_room || roster.length) + ' twins standing for <b>' + fmtN(g.size_people) + '</b> people' + (roomStandsForLine(g) ? SEP + esc(roomStandsForLine(g)) : '') + SEP + 'Ask the first question below; the shares, the read and the voices come back in a minute or two.</span>';
                }
                return '<div class="dtw-fg-acts" id="dtwFgDockActs"></div><div class="dtw-fg-htext"><div class="dtw-fg-rtwrap"><input id="dtwFgTitle" class="dtw-fg-rt" value="' + esc(roomTitle(s)) + '" size="' + Math.max(8, String(roomTitle(s) || '').length + 1) + '" maxlength="120" aria-label="Room name" placeholder="Name this room" onfocus="this.dataset.dirty=\'1\'" oninput="this.dataset.dirty=\'1\';this.size=Math.max(8,this.value.length+1)" onkeydown="if(event.key===\'Enter\'){event.preventDefault();this.blur();}" onblur="dtwPanelRename(this.value)"><button type="button" class="dtw-fg-rt-edit" onclick="event.preventDefault();var t=document.getElementById(\'dtwFgTitle\');if(t){t.focus();t.select();}">Edit</button></div><div class="dtw-fg-fm" id="dtwFgFm">' + meta + '</div></div>';
            }
            function titleEditing() {
                var t = el('dtwFgTitle');
                return !!(t && (document.activeElement === t || t.dataset.dirty === '1'));
            }
            function paintFgDock(s, g, roster, fq, fly) {
                var hero = el('dtwFgHero'), dock = el('dtwFgDock');
                if (!hero || !dock) return false;
                var keep = titleEditing(), old = el('dtwFgTitle'), start = old ? old.selectionStart : 0, end = old ? old.selectionEnd : 0, typed = old ? old.value : '';
                hero.innerHTML = heroHtml(s, g, roster, fq, fly);
                dock.innerHTML = dockHtml(s, g, roster, fq, fly);
                if (keep) {
                    var neu = el('dtwFgTitle');
                    if (neu) {
                        neu.value = typed;
                        neu.dataset.dirty = '1';
                        try { neu.focus(); neu.setSelectionRange(start, end); } catch (_) {}
                    }
                }
                syncFgActs();
                fgLayoutSeats();
                return true;
            }
            // The row fits the width it has: one row when the seats fit at their pitch, else as few rows as needed, filled evenly (never one seat alone on a second row).
            function fgLayoutSeats() {
                document.querySelectorAll('#dtwPnConsole .dtw-fg-seatrow').forEach(function(row) {
                    var n = row.children.length, w = row.clientWidth; if (!n || w <= 0) return;
                    var cs = getComputedStyle(row), pitch = parseFloat(cs.getPropertyValue('--seat-pitch')) || 108, gap = parseFloat(cs.columnGap) || 8;
                    var rows = Math.max(1, Math.ceil(n * pitch / (w + gap))), per = Math.ceil(n / rows);
                    row.style.gridTemplateColumns = 'repeat(' + per + ', minmax(0, ' + (row.classList.contains('mini') ? 72 : 124) + 'px))';
                });
                fgDockSize();
            }
            function fgDockSize() {
                var view = el('digitalTwinsIQView'), dock = el('dtwFgDock');
                if (!view || !dock || !S.session) { if (view) view.style.removeProperty('--dtw-main-h'); return; }
                view.style.setProperty('--dtw-main-h', Math.max(240, view.clientHeight - dock.offsetHeight) + 'px');
                fgDockStuck();
                fgVoicesFill();
            }
            window.addEventListener('resize', function() { if (S.tab !== 'panel') return; clearTimeout(S.fgResizeT); S.fgResizeT = setTimeout(fgLayoutSeats, 120); });
            // the dock paints over the strip above it only once it is frozen, so the seat row keeps its last line until then
            function fgDockStuck() {
                var hero = el('dtwFgHero'), dock = el('dtwFgDock'); if (!hero || !dock) return;
                dock.classList.toggle('stuck', hero.getBoundingClientRect().bottom < dock.getBoundingClientRect().top - 0.5);
            }
            // the thread back at its top brings the seats and the twin back down
            function fgSnapTop() { var v = el('digitalTwinsIQView'); if (v && v.classList.contains('is-fg-room') && v.scrollTop > 0) v.scrollTo({ top: 0, behavior: 'smooth' }); }
            document.addEventListener('scroll', function(e) {
                if (e.target === el('digitalTwinsIQView')) fgDockStuck();
                var tr = el('dtwPnTranscript');
                if (tr && e.target === tr) { if (tr.scrollTop <= 0 && (S.fgTrLast || 0) > 0) fgSnapTop(); S.fgTrLast = tr.scrollTop; }
            }, true);
            document.addEventListener('wheel', function(e) {
                var tr = el('dtwPnTranscript'); if (!tr || e.deltaY >= 0 || !(e.target instanceof Element) || !tr.contains(e.target)) return;
                if (tr.scrollTop <= 0) fgSnapTop();
            }, { passive: true, capture: true });
            document.addEventListener('scroll', function(e) {
                var p = el('dtwSeatPop'); if (!p || p.style.position !== 'fixed' || !p.classList.contains('open') || S.fg.seat == null) return;
                if (e.target instanceof Element && p.contains(e.target)) return;
                var a = document.querySelector('#dtwPnConsole .dtw-fg-seat[data-seat="' + S.fg.seat + '"]'), r = a ? a.getBoundingClientRect() : null;
                if (!r || r.bottom < 0 || r.top > window.innerHeight) { dtwSeatOpen(null); return; }
                var sb = el('dtwSeatBody'), keep = sb ? sb.scrollTop : 0;
                placePop(a); if (sb) sb.scrollTop = keep;
            }, true);
            // ---- the thread: the question as the moderator's message, the answers as messages, the read as the closing message.
            function qMeta(q, i, extra) {
                var qs = (S.session || {}).questions || [], again = false;
                for (var j = 0; j < i; j++) { if (normQ(qs[j].q) === normQ(q.q) && (qs[j].kind || '') === (q.kind || '')) { again = true; break; } }
                return '<span class="qm">Question ' + (i + 1) + (q.kind ? SEP + esc(kindWord(q.kind)) : '') + (again ? SEP + 'asked again' : '') + (extra || '') + '</span>';
            }
            function qMsg(q, i, roster) {
                var seats = seatsTotal(q);
                if (qInFlight(q) && (String(q.kind || '') === 'probe' || q.follows)) {
                    return '<div class="dtw-fg-probe" data-q="' + esc(q.id || '') + '">' + modav() + '<span class="tx"><span class="dtw-fg-mod">Prometheus</span>' + esc(q.q) + '</span><span class="dtw-fg-tag">Probe' + SEP + 'sent to all ' + (seats || roster.length) + '</span></div>';
                }
                return '<div class="dtw-fg-qmsg" data-q="' + esc(q.id || '') + '">' + modav() + '<div class="dtw-fg-qt"><span class="dtw-fg-mod">Prometheus</span>' + esc(q.q) + qMeta(q, i) + (q.framing && normQ(q.framing) !== normQ(q.q) ? '<div class="fr">' + esc(q.framing) + '</div>' : '') + (q.note ? '<div class="nt">' + esc(q.note) + '</div>' : '') + '</div></div>';
            }
            function answerMsg(q, rk, a, roster) {
                var seat = roster.filter(function(r) { return Number(r.seat) === Number(a.seat); })[0] || { name: a.name, seat: a.seat, age: a.age, city: a.city };
                var idx = a.seat != null ? Number(a.seat) - 1 : roster.indexOf(seat);
                var optId = answerOptId(q, a), tag = optTag(q, rk, optId, a.option_label || '');
                var open = seat.seat != null ? ' data-fg-seat="' + Number(seat.seat) + '" onclick="dtwSeatOpen(' + Number(seat.seat) + ', this)"' : '';
                return '<div class="dtw-fg-ab">' + face(seat, idx, 36) + '<div class="card"><div class="dtw-fg-nm"><b' + open + '>' + esc(titleCase(seat.name || a.name || '')) + '</b><span class="ag">' + esc(seat.age != null ? seat.age : '') + (seat.city ? ', ' + esc(titleCase(seat.city)) : '') + (seat.segment && multiSeg(roster) ? ', ' + esc(seat.segment) : '') + '</span>' + tag + '</div>' + (a.line ? '<div class="dtw-fg-tx">' + esc(a.line) + '</div>' : '') + '</div></div>';
            }
            // the answers as speech bubbles in two staggered columns: faces on the outside edges, the right column set lower
            function bubblesHtml(items) {
                if (!items.length) return '';
                var l = items.filter(function(x, i) { return i % 2 === 0; }), r = items.filter(function(x, i) { return i % 2 === 1; });
                return '<div class="dtw-fg-bubbles"><div class="dtw-fg-bcol l">' + l.join('') + '</div>' + (r.length ? '<div class="dtw-fg-bcol r">' + r.join('') + '</div>' : '') + '</div>';
            }
            function answersHtml(q, roster) {
                var rk = rankMap(q), bySeat = (q.answers || []).slice().sort(function(a, b) { return Number(a.seat) - Number(b.seat); }), ans = [], used = {};
                // the voices the read picked lead, in the same order as the tally's The voices., then the rest by seat
                (q.voices || []).forEach(function(v) {
                    var seat = v.seat != null ? Number(v.seat) : Number((seatFor(v, roster) || {}).seat);
                    var a = bySeat.filter(function(x) { return Number(x.seat) === seat; })[0];
                    if (a && !used[seat]) { used[seat] = 1; ans.push(a); }
                });
                bySeat.forEach(function(a) { if (!used[Number(a.seat)]) ans.push(a); });
                if (!ans.length) return '';
                var fold = !S.fg.earlier[q.id], shown = fold ? [] : ans;
                var out = fold ? '' : bubblesHtml(shown.map(function(a) { return answerMsg(q, rk, a, roster); }));
                if (fold) out += '<button type="button" class="dtw-fg-earlier" onclick="dtwFgEarlier(\'' + esc(jsq(q.id)) + '\', true)">Show the ' + ans.length + ' ' + (ans.length === 1 ? 'answer' : 'answers') + '</button>';
                else out += '<button type="button" class="dtw-fg-earlier" onclick="dtwFgEarlier(\'' + esc(jsq(q.id)) + '\', false)">Hide the answers</button>';
                return out;
            }
            function probesHtml(q) {
                var seen = {}, chips = [];
                (q.probes_next || []).slice(0, 2).forEach(function(p) { p = String(p || '').trim(); if (!p || seen[normQ(p)]) return; seen[normQ(p)] = 1; chips.push([p, p]); });
                STD_PROBES.forEach(function(p) { if (chips.length >= 4 || seen[normQ(p[1])]) return; seen[normQ(p[1])] = 1; chips.push(p); });
                return '<div class="dtw-fg-nextp">Probe next:' + chips.map(function(c) { return '<button type="button" class="dtw-fg-chip" onclick="dtwPanelAsk(\'' + esc(jsq(c[1])) + '\')">' + esc(c[0]) + '</button>'; }).join('') + '</div>';
            }
            function readMsg(q) {
                var n = (q.answers || []).length;
                return '<div class="dtw-fg-readmsg">' + modav() + '<div class="dtw-fg-bubble"><div class="lab">The read<span>Prometheus' + SEP + (n ? 'after the ' + esc(ordWord(n)) + ' answer' : 'after the room answered') + '</span></div><p>' + readHtml(q.read) + '</p>' + (q.moderator_note ? '<div class="note"><b>A note from Prometheus.</b> ' + esc(q.moderator_note) + '</div>' : '') + assetReadExtras(q) + '</div></div>';
            }
            // ---- content in the room (Jenna 2026-11-05: 'upload content into the focus group (a video, image, mp3) and it should assess it for
            // you the same way the pilot assesses video. so that you could then ask questions about the content as an a/b testing kind of thing').
            function fgAssets(s) { return ((s || {}).assets || []).filter(function(a) { return a && a.status && (a.kind === 'video' || a.kind === 'image' || a.kind === 'audio'); }); }
            function assetImgUrl(s, a, n) { return '/api/digital-twins/' + encodeURIComponent(S.key || '') + '/panel/' + encodeURIComponent((s || {}).id || '') + '/assets/' + encodeURIComponent(a.id) + '/image?n=' + (n || 0); }
            var ASSET_ICON = { audio: '<svg viewBox="0 0 26 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 10h2M6 6v8M10 3v14M14 7v6M18 4v12M22 8v4"/></svg>',
                               video: '<svg viewBox="0 0 26 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="2" y="3" width="16" height="14" rx="2"/><path d="M18 8l6-3v10l-6-3z"/></svg>',
                               image: '<svg viewBox="0 0 26 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="2" width="20" height="16" rx="2"/><path d="M3 14l6-5 5 4 3-2 6 5"/><circle cx="17" cy="7" r="1.6"/></svg>' };
            function assetStatusHtml(a) {
                if (a.status === 'processing') return '<span class="dtw-fg-pulse"></span>' + esc(a.stage || 'taking it in');
                if (a.status === 'failed') return esc(a.note || 'could not be taken in');
                return a.read_id ? 'read' + SEP + 'open' : 'ready';
            }
            function assetChipHtml(s, a) {
                var th = a.has_thumb ? '<img src="' + esc(assetImgUrl(s, a, 0)) + '" alt="" loading="lazy" onerror="this.remove()">' : (ASSET_ICON[a.kind] || '');
                var on = S.fg.tallyQ && a.read_id === S.fg.tallyQ ? ' on' : '';
                var ttl = (a.kind === 'video' ? 'Video' : a.kind === 'audio' ? 'Audio' : 'Image') + (a.filename && a.source !== 'url' ? ': ' + a.filename : '') + (a.read_id ? '. Open what the room made of it.' : '');
                return '<button type="button" class="dtw-fg-asset ' + esc(a.status || '') + on + '" data-asset="' + esc(a.id) + '" title="' + esc(ttl) + '" onclick="dtwPanelAssetOpen(\'' + esc(jsq(a.id)) + '\')"><span class="th">' + th + '</span><span class="body"><span class="lb">' + esc(a.label || '') + '</span><span class="st">' + assetStatusHtml(a) + '</span></span>'
                    + '<span class="x" role="button" aria-label="Remove ' + esc(a.label || 'this') + '" title="Remove from the room" onclick="event.stopPropagation(); dtwPanelAssetDelete(\'' + esc(jsq(a.id)) + '\')">&#215;</span></button>';
            }
            // One bar (Jenna 2026-11-16: 'this is confusing with the two bars. instead it just needs to have a place where you can upload in
            // the actual question part'): the clip inside the question field adds a file; a direct link typed or pasted into the field and sent
            // goes in as content; a file can be dropped on the field or pasted into it. The room labels each one itself (A, B, ...).
            var ASSET_ACCEPT = '.mp4,.mov,.webm,.png,.jpg,.jpeg,.webp,.mp3,.m4a,.wav,video/mp4,video/quicktime,video/webm,image/png,image/jpeg,image/webp,audio/mpeg,audio/mp4,audio/wav';
            var CLIP_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.5 9.4l-6.6 6.6a4 4 0 0 1-5.7-5.7l7-7a2.7 2.7 0 0 1 3.8 3.8l-6.9 6.9a1.3 1.3 0 0 1-1.9-1.9l6.2-6.2"/></svg>';
            function attachHtml() {
                return '<label class="dtw-fg-attach" id="dtwFgAttach" tabindex="0" role="button" aria-label="Add content" title="Add content: a video (mp4, mov, webm), an image (png, jpg, webp) or audio (mp3, m4a, wav). A second one makes it an A or B." onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();this.querySelector(\'input\').click();}">' + CLIP_ICON
                    + '<input type="file" accept="' + ASSET_ACCEPT + '" hidden onchange="dtwPanelAssetAdd(this.files[0]); this.value=\'\';"></label>';
            }
            function assetsStripHtml(s, compact) {
                var list = fgAssets(s);
                if (compact) return list.length ? '<div class="dtw-fg-addc"><div class="dtw-fg-assets">' + list.map(function(a) { return assetChipHtml(s, a); }).join('') + '</div></div>' : '';
                return list.length ? '<div class="dtw-fg-assets" id="dtwFgAssets">' + list.map(function(a) { return assetChipHtml(s, a); }).join('') + '</div>' : '';
            }
            function assetReadExtras(q) {
                if (!q || q.kind !== 'asset_read') return '';
                var s = S.session, a = fgAssets(s).filter(function(x) { return x.id === q.asset_id; })[0], out = '';
                if (a && a.kind === 'image' && a.has_thumb) out += '<div class="pics"><img src="' + esc(assetImgUrl(s, a, 0)) + '" alt="" onerror="this.parentNode.remove()"></div>';
                else if (a && a.kind === 'video' && a.frames_n) { out += '<div class="pics">'; for (var i = 1; i <= Math.min(4, a.frames_n); i++) out += '<img src="' + esc(assetImgUrl(s, a, i)) + '" alt="" loading="lazy" onerror="this.remove()">'; out += '</div>'; }
                var cl = q.clarity || {};
                if (cl.about) out += '<div><b>What it is about.</b> ' + esc(cl.about) + '</div>';
                if (cl.for_whom) out += '<div><b>Who it is for.</b> ' + esc(cl.for_whom) + (cl.clear ? ' (' + esc(cl.clear) + ')' : '') + '</div>';
                if ((q.stood_out || []).length) out += '<div><b>Stood out.</b> ' + esc(q.stood_out.join('; ')) + '</div>';
                if ((q.fell_flat || []).length) out += '<div><b>Fell flat.</b> ' + esc(q.fell_flat.join('; ')) + '</div>';
                (q.lean_by_segment || []).slice(0, 4).forEach(function(r) { if (r && r.line) out += '<div>' + esc(r.line) + '</div>'; });
                if (a && a.kind === 'audio' && a.transcript_excerpt) out += '<div><b>Heard.</b> ' + esc(String(a.transcript_excerpt).slice(0, 320)) + '</div>';
                return out ? '<div class="dtw-fg-ard">' + out + '</div>' : '';
            }
            function anyAssetProcessing() { return fgAssets(S.session).some(function(a) { return a.status === 'processing'; }); }
            function clearAssetPoll() { if (S.assetPollTimer) { clearTimeout(S.assetPollTimer); S.assetPollTimer = null; } }
            // the room takes a file in on its own time: re-read the session every few seconds until every piece is ready or failed, and adopt the read it lands
            function scheduleAssetPoll() {
                clearAssetPoll();
                var s = S.session; if (!s || !S.key || !anyAssetProcessing()) return;
                var sid = s.id, key = S.key;
                var tick = async function() {
                    S.assetPollTimer = null;
                    if (!S.session || S.session.id !== sid || S.key !== key) return;
                    var changed = false;
                    try {
                        var d = await api('/' + key + '/panel/' + sid);
                        var fresh = (d.session || {}).assets || [];
                        if (JSON.stringify(fresh) !== JSON.stringify(S.session.assets || [])) { S.session.assets = fresh; changed = true; }
                        ((d.session || {}).questions || []).forEach(function(fq) { if (adoptQuestion(fq)) changed = true; });
                        if (d.session && d.session.updated_at) S.session.updated_at = d.session.updated_at;
                    } catch (_) {}
                    if (!S.session || S.session.id !== sid) return;
                    if (changed) {
                        var landed = (S.session.questions || []).filter(function(q) { return q.kind === 'asset_read'; }).slice(-1)[0];
                        if (landed && landed.id && !S.fg.assetSeen) { S.fg.tallyQ = landed.id; }
                        fgRefresh();
                        var ss = (S.twin || {}).panel_sessions || []; for (var k = 0; k < ss.length; k++) { if (ss[k].id === sid) ss[k] = S.session; }
                        renderPanelSessions();
                    }
                    if (anyAssetProcessing()) S.assetPollTimer = setTimeout(tick, 5000);
                };
                S.assetPollTimer = setTimeout(tick, 5000);
            }
            async function assetPost(fd) {
                var r = await fetch('/api/digital-twins/' + encodeURIComponent(S.key) + '/panel/' + encodeURIComponent(S.session.id) + '/assets', { method: 'POST', credentials: 'same-origin', body: fd });
                var u = null; try { u = await r.json(); } catch (_) { u = null; }
                if (!r.ok || !u || u.success === false) { var e = new Error((u && u.error) || 'The file could not be stored.'); e.doc = u; throw e; }
                return u;
            }
            function assetAdopt(u) {
                if (!S.session) return;
                if (u.assets) S.session.assets = u.assets;
                if (u.session && u.session.questions_paid != null) S.session.questions_paid = u.session.questions_paid;
                fgRefresh();
                scheduleAssetPoll();
                liveNote((u.asset && u.asset.label ? u.asset.label : 'It') + ' is in the room. The room is taking it in; what it made of it lands in the thread in a minute or two.');
            }
            function assetFail(e) {
                liveNote(e.message);
                if (e.doc && e.doc.upgrade) liveNoteHtml(esc(e.doc.upgrade.line || e.message) + ' <button type="button" class="dtw-link" onclick="dtwPanelUpgrade()">Move it to 1-40 for ' + esc(e.doc.upgrade.label || '') + '</button>');
                else if (e.doc && e.doc.add_funds_url) liveNoteHtml(esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>');
            }
            window.dtwPanelAssetAdd = async function(file) {
                if (!S.session || !S.key || !file) return;
                if (S.assetBusy) { liveNote('One piece of content at a time; the last one is still going in.'); return; }
                if (!/\.(mp4|mov|webm|m4v|png|jpe?g|webp|mp3|m4a|wav)$/i.test(file.name || '')) { liveNote('Use a video (mp4, mov, webm), an image (png, jpg, webp) or audio (mp3, m4a, wav).'); return; }
                S.assetBusy = true;
                liveNote('Storing ' + (file.name || 'the file') + '...');
                var fd = new FormData(); fd.append('file', file);
                try { assetAdopt(await assetPost(fd)); } catch (e) { assetFail(e); }
                S.assetBusy = false;
            };
            window.dtwPanelAssetUrl = async function(link) {
                var inp = el('dtwPnQ'), url = String(link || (inp ? inp.value : '') || '').trim(); if (!S.session || !url) return;
                if (!/^https?:\/\//i.test(url)) { liveNote('Paste a direct link to the file (https://...).'); return; }
                if (S.assetBusy) { liveNote('One piece of content at a time; the last one is still going in.'); return; }
                S.assetBusy = true;
                liveNote('Fetching the file...');
                var fd = new FormData(); fd.append('url', url);
                try { var got = await assetPost(fd); var now = el('dtwPnQ'); if (now && now.value.trim() === url) now.value = ''; assetAdopt(got); } catch (e) { assetFail(e); }
                S.assetBusy = false;
            };
            window.dtwPanelAssetDrop = function(ev) {
                try { ev.preventDefault(); } catch (_) {}
                var box = el('dtwFgField'); if (box) box.classList.remove('over');
                var f = ev && ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
                if (f) { dtwPanelAssetAdd(f); return; }
                var txt = ev && ev.dataTransfer ? (ev.dataTransfer.getData('text/uri-list') || ev.dataTransfer.getData('text/plain') || '') : '';
                if (/^https?:\/\/\S+$/i.test(txt.trim())) dtwPanelAssetUrl(txt.trim());
            };
            // a file copied to the clipboard (a screenshot, a copied image) pasted into the question field goes in as content
            window.dtwPanelAssetPaste = function(ev) {
                var f = ev && ev.clipboardData && ev.clipboardData.files && ev.clipboardData.files[0];
                if (!f) return;
                try { ev.preventDefault(); } catch (_) {}
                dtwPanelAssetAdd(f);
            };
            window.dtwPanelAssetOpen = function(id) {
                var a = fgAssets(S.session).filter(function(x) { return x.id === id; })[0]; if (!a) return;
                if (a.status === 'failed') { liveNote(a.note || 'The room could not take this one in. Add it again and it runs fresh.'); return; }
                if (!a.read_id) { liveNote(a.status === 'processing' ? 'The room is still taking ' + (a.label || 'it') + ' in; what it made of it lands in the thread in a minute or two.' : 'The read for ' + (a.label || 'it') + ' is on its way.'); return; }
                S.fg.tallyQ = a.read_id; S.fg.open[a.read_id] = true;
                fgRefresh();
                var blk = document.querySelector('#dtwPnTranscript [data-q="' + esc(a.read_id) + '"]'); if (blk) fgScrollTo(blk);
                var inp = el('dtwPnQ'); if (inp) { try { inp.focus(); } catch (_) {} }
            };
            window.dtwPanelAssetDelete = async function(id) {
                if (!S.session || !S.key) return;
                var a = fgAssets(S.session).filter(function(x) { return x.id === id; })[0]; if (!a) return;
                if (!confirm('Take ' + (a.label || 'this') + ' out of the room? What the room made of it stays in the thread.')) return;
                try { var d = await api('/' + S.key + '/panel/' + S.session.id + '/assets/' + encodeURIComponent(id), { method: 'DELETE' }); }
                catch (e1) { try { d = await api('/' + S.key + '/panel/' + S.session.id + '/assets/' + encodeURIComponent(id) + '/delete', { method: 'POST', body: '{}' }); } catch (e2) { liveNote(e2.message); return; } }
                if (d && d.assets) S.session.assets = d.assets;
                fgRefresh();
            };
            function failedMsg(q, isLast) { return '<div class="dtw-fg-failed" data-q="' + esc(q.id || '') + '"><span>' + esc(q.read || 'The room did not finish this question. Ask it again and it runs fresh.') + '</span>' + (isLast ? '<button type="button" class="dtw-fg-chip" onclick="dtwPanelAsk(\'' + esc(jsq(q.q)) + '\', true)">Ask again</button>' : '') + '</div>'; }
            function oldHead(q, i, open) {
                var seats = seatsTotal(q), nA = (q.answers || []).length;
                var extra = qFailed(q) ? SEP + 'did not finish' : (nA ? SEP + nA + (seats ? ' of ' + seats : '') : '');
                return '<button type="button" class="dtw-fg-old' + (open ? ' open' : '') + '" data-q="' + esc(q.id || '') + '" aria-expanded="' + (open ? 'true' : 'false') + '" onclick="dtwFgToggle(\'' + esc(jsq(q.id)) + '\')">' + modav() + '<div class="body"><div class="qt">' + esc(q.q) + qMeta(q, i, extra) + '</div>' + (qFailed(q) || !open ? '' : questionLolli(q)) + '</div><span class="chev">&#9662;</span></button>';
            }
            function fullBlock(q, i, roster, isLast, fly, noHead) {
                var out = noHead ? ((q.framing && normQ(q.framing) !== normQ(q.q)) || q.note ? '<div class="dtw-fg-qmsg" data-q="' + esc(q.id || '') + '"><span class="dtw-fg-modav" style="visibility:hidden"></span><div class="dtw-fg-qt">' + (q.framing && normQ(q.framing) !== normQ(q.q) ? '<div class="fr" style="margin-top:0">' + esc(q.framing) + '</div>' : '') + (q.note ? '<div class="nt">' + esc(q.note) + '</div>' : '') + '</div></div>' : '') : qMsg(q, i, roster);
                if (qFailed(q)) return out + failedMsg(q, isLast && !fly);
                // the read first, then the answers behind one line (Jenna 2026-11-18: the chat is the page; the answers fold)
                if (q.read) out += readMsg(q);
                out += answersHtml(q, roster);
                if (isLast && !fly) out += probesHtml(q);
                return out;
            }
            function flyBlock(q, i, roster) {
                var rk = rankMap(q), ans = (q.answers || []).slice().sort(function(a, b) { return Number(a.seat) - Number(b.seat); });
                return qMsg(q, i, roster) + workingLine(q) + bubblesHtml(ans.map(function(a) { return answerMsg(q, rk, a, roster); }));
            }
            function threadHtml(s, roster, fq, fly) {
                var qs = s.questions || [], parts = [];
                if (!qs.length) parts.push('<div class="dtw-fg-empty-t">The room is seated. Ask the first question below; the shares, the read and a few voices come back in a minute or two.</div>');
                qs.forEach(function(q, i) {
                    var isLast = i === qs.length - 1;
                    if (qInFlight(q)) parts.push(flyBlock(q, i, roster));
                    else if (q === fq || isLast) parts.push(fullBlock(q, i, roster, isLast, !!fly));
                    else if (S.fg.open[q.id]) parts.push(oldHead(q, i, true) + fullBlock(q, i, roster, false, true, true));
                    else parts.push(oldHead(q, i, false));
                });
                // a Predictive Analytics room (2026-11-18): the outcome closes the thread once the last planned question lands
                if (s.guided && s.guided.outcome && !fly) parts.push(paOutcomeMsg(s));
                else if (paRunning(s) && !fly) parts.push('<div class="dtw-fg-empty-t">Prometheus is running the planned questions; the next one is on its way. Ask your own when the outcome lands.</div>');
                return '<div class="dtw-fg-msgs">' + parts.join('') + '</div>';
            }
            function paRunning(s) { return !!(s && s.guided && s.guided.status === 'running'); }
            function paOutcomeBody(o) {
                o = o || {};
                var body = '<p><b>' + esc(paCallWord(o)) + '.</b> ' + esc(o.answer || '') + '</p>';
                if (o.why) body += '<p>' + esc(o.why) + '</p>';
                if ((o.numbers || []).length) body += '<ul class="dtw-pa-nums">' + o.numbers.map(function(n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>';
                if (o.who) body += '<p>' + esc(o.who) + '</p>';
                if (o.would_change) body += '<p><b>What would change it.</b> ' + esc(o.would_change) + '</p>';
                if (o.next) body += '<p><b>Worth asking next.</b> ' + esc(o.next) + '</p>';
                return body;
            }
            function paOutcomeMsg(s) {
                var g = s.guided || {}, plan = g.plan || {};
                var body = paOutcomeBody(g.outcome);
                return '<div class="dtw-fg-readmsg dtw-pa-outmsg">' + modav() + '<div class="dtw-fg-bubble"><div class="lab">The outcome<span>Prometheus' + SEP + esc(plan.goal || 'the business question') + '</span></div>' + body
                    + '<div class="dtw-pa-outacts"><button type="button" class="dtw-fg-act" onclick="dtwPaOpenRun(\'' + esc(s.id) + '\')">See the report</button></div></div></div>';
            }
            function paCallWord(o) { var c = String((o || {}).call || '').trim(); var w = { 'yes': 'Yes', 'lean yes': 'Leaning yes', 'lean no': 'Leaning no', 'no': 'No', 'split': 'Split' }; return c ? (w[c.toLowerCase()] || c) : 'Split'; }
            // ---- the tally: one stacked bar in the five series with its legend, what the room knew, two voices, the split by segment.
            function voiceMini(v, roster, q) {
                var seat = seatFor(v, roster), idx = v.seat ? Number(v.seat) - 1 : (seat ? roster.indexOf(seat) : 0);
                var f = { name: v.name, seat: v.seat || (seat || {}).seat, portrait_url: v.portrait_url || (seat || {}).portrait_url || '' };
                var a = (seat && q) ? (q.answers || []).filter(function(x) { return Number(x.seat) === Number(seat.seat); })[0] : null;
                var because = v.because ? 'Because ' + String(v.because).replace(/^because\s+/i, '').replace(/\.?$/, '.') : ((a && a.reason) || '');
                var open = seat ? ' data-fg-seat="' + Number(seat.seat) + '" onclick="dtwSeatOpen(' + Number(seat.seat) + ', this)"' : '';
                return '<div class="dtw-fg-vmini"><div class="who">' + face(f, idx, 24) + '<span' + open + '>' + esc(titleCase(v.name)) + '<span>, ' + esc(v.age) + ', ' + esc(titleCase(v.city)) + (v.segment && multiSeg(roster) ? ', ' + esc(v.segment) : '') + '</span></span></div><q>' + esc(v.line) + '</q>' + (because ? '<span class="why">' + esc(because) + '</span>' : '') + '</div>';
            }
            // the voices the read picked, then every other seat that answered, so the list runs through the whole room
            function voicesAll(q, roster) {
                var out = [], used = {};
                (q.voices || []).forEach(function(v) { var seat = v.seat != null ? Number(v.seat) : Number((seatFor(v, roster) || {}).seat); if (seat && !used[seat]) { used[seat] = 1; out.push(v); } else if (!seat) out.push(v); });
                (q.answers || []).slice().sort(function(a, b) { return Number(a.seat) - Number(b.seat); }).forEach(function(a) {
                    var seat = Number(a.seat); if (!seat || used[seat] || !a.line) return; used[seat] = 1;
                    var r = roster.filter(function(x) { return Number(x.seat) === seat; })[0] || {};
                    out.push({ seat: seat, name: r.name || a.name, age: r.age != null ? r.age : a.age, city: r.city || a.city, segment: r.segment, portrait_url: r.portrait_url, line: a.line });
                });
                return out;
            }
            function fgVoicesMore() {
                var c = S.fg.vctx, box = el('dtwFgVoices'); if (!c || !box) return;
                var have = box.children.length; if (have >= c.list.length) return;
                var next = c.list.slice(have, have + 2);
                box.insertAdjacentHTML('beforeend', next.map(function(v) { return voiceMini(v, c.roster, c.q); }).join(''));
                S.fg.vShown[c.key] = have + next.length;
                var n = el('dtwFgVoicesN'); if (n) n.textContent = (have + next.length) + ' of ' + c.list.length;
            }
            function fgVoicesFill() {
                var t = el('dtwFgTallyIn'), box = el('dtwFgVoices'), c = S.fg.vctx; if (!t || !box || !c) return;
                for (var i = 0; i < 12 && box.children.length < c.list.length && t.scrollHeight <= t.clientHeight + 80; i++) fgVoicesMore();
            }
            document.addEventListener('scroll', function(e) {
                var t = e.target; if (!(t instanceof Element) || t.id !== 'dtwFgTallyIn') return;
                if (t.scrollTop + t.clientHeight >= t.scrollHeight - 80) fgVoicesMore();
            }, true);
            function factsSec(q) {
                var f = ((q || {}).facts_used || []).filter(Boolean); if (!f.length) return '';
                return '<div class="dtw-fg-sec"><div class="dtw-fg-th">What the room knew.</div>' + f.map(function(x, i) { return '<div class="dtw-fg-fact"><span class="num">' + (i < 9 ? '0' : '') + (i + 1) + '</span><span>' + esc(x) + '</span></div>'; }).join('') + '</div>';
            }
            // the folded sections under the split (Jenna 2026-11-18): Responses and What the room knew open in place; closed by default
            function foldSec(key, title, count, body) {
                var open = !!(S.fg.fold || {})[key];
                return '<div class="dtw-fg-fold' + (open ? ' open' : '') + '"><button type="button" class="dtw-fg-foldh" aria-expanded="' + (open ? 'true' : 'false') + '" onclick="dtwFgFold(\'' + key + '\')"><span>' + title + '<span class="n">' + count + '</span></span><span class="chev"></span></button>' + (open ? '<div class="dtw-fg-foldb">' + body + '</div>' : '') + '</div>';
            }
            window.dtwFgFold = function(key) { S.fg.fold = S.fg.fold || {}; S.fg.fold[key] = !S.fg.fold[key]; dtwFgTallyQ(S.fg.tallyQ); };
            // the room's faces under the split: the same seats as the strip, ringed in the color of each one's answer; tap one for who
            // they are and an Ask line that puts their name in the box
            function roomSeatsSec(g, roster, fq, fly) {
                if (!roster.length) return '';
                var rk = fq ? rankMap(fq) : {}, bySeat = {}, flyBySeat = {};
                if (fq) (fq.answers || []).forEach(function(a) { bySeat[String(a.seat)] = a; });
                if (fly) (fly.answers || []).forEach(function(a) { flyBySeat[String(a.seat)] = a; });
                var seats = roster.map(function(r, i) {
                    var a = bySeat[String(r.seat)], landed = !!flyBySeat[String(r.seat)];
                    return seatCircle(r, i, { edge: a ? edgeOf(rk, answerOptId(fq, a)) : '', mark: fly ? (landed ? '<span class="mark"></span>' : '<span class="mark wait"></span>') : '', pending: !!fly && !landed });
                }).join('');
                return '<div class="dtw-fg-sec dtw-fg-roomsec"><div class="dtw-fg-th">The room.<span>' + fmtN((g || {}).in_room || roster.length) + ' twins standing for ' + fmtN((g || {}).size_people) + ' people' + SEP + 'tap one to ask them</span></div><div class="dtw-fg-seatrow mini" role="list">' + seats + '</div></div>';
            }
            window.dtwFgAskSeat = function(seatNo) {
                var r = ((S.session || {}).group || {}).roster || [], seat = r.filter(function(x) { return Number(x.seat) === Number(seatNo); })[0]; if (!seat) return;
                var inp = el('dtwPnQ'); if (!inp) return;
                var tag = '@' + titleCase(seat.name || '') + ', ', cur = String(inp.value || '');
                if (cur.indexOf(tag) < 0) inp.value = tag + cur.replace(/^@[^,]+,\s*/, '');
                dtwSeatOpen(null);
                try { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } catch (_) {}
            };
            // What people were saying (2026-11-04): the lines the audience itself used, from the media desk, under the facts.
            function talkSec(q) {
                var t = ((q || {}).talk || []).filter(Boolean); if (!t.length) return '';
                return '<div class="dtw-fg-sec"><div class="dtw-fg-th">What people were saying.</div>' + t.map(function(x, i) { return '<div class="dtw-fg-fact"><span class="num">' + (i < 9 ? '0' : '') + (i + 1) + '</span><span>' + esc(x) + '</span></div>'; }).join('') + '</div>';
            }
            function segmentsSec(q, rk) {
                var segs = (q || {}).by_segment || []; if (segs.length < 2) return '';
                var opts = optsOf(q), byLabel = {}; opts.forEach(function(o) { byLabel[String(o.label)] = o; });
                return '<div class="dtw-fg-sec"><div class="dtw-fg-th">By segment.<span>Share of each cohort, by answer</span></div>' + segs.map(function(sg) {
                    var parts = (sg.distribution || []).map(function(d) { var o = byLabel[String(d.label)]; return { pct: Number(d.pct || 0), color: o ? seriesOf(rk, o.id) : SERIES[4] }; });
                    return '<div class="dtw-fg-seg"><div class="l1"><span>' + esc(sg.segment) + '</span><b>' + fmtN(sg.people) + ' people</b></div>' + stack(parts) + '</div>';
                }).join('') + '</div>';
            }
            function roomFacts(g) {
                var segs = (g || {}).segments || [];
                if (segs.length < 2) return '<div class="dtw-fg-quiet">' + fmtN((g || {}).in_room) + ' twins standing for ' + fmtN((g || {}).size_people) + ' people.</div>';
                return '<div class="dtw-fg-sec"><div class="dtw-fg-th">Who is in the room.</div>' + segs.map(function(sg) { return '<div class="dtw-fg-seg"><div class="l1"><span>' + esc(sg.label) + '</span><b>' + pct(sg.share_pct, 1) + '</b></div><div class="dtw-fg-quiet" style="margin-top:1px;">' + fmtN(sg.people) + ' people, ' + sg.seats + (Number(sg.seats) === 1 ? ' seat' : ' seats') + '</div></div>'; }).join('') + '</div>';
            }
            function qPickHtml(s, selected) {
                var qs = (s || {}).questions || [];
                if (qs.length < 2) return '';
                var opts = qs.map(function(q, i) {
                    var label = (i + 1) + '. ' + String(q.q || ('Question ' + (i + 1)));
                    if (qInFlight(q)) label += ' (working)';
                    else if (qFailed(q)) label += ' (did not finish)';
                    if (label.length > 92) label = label.slice(0, 89) + '...';
                    return '<option value="' + esc(qId(q, i)) + '"' + (selected && qId(q, i) === qId(selected, qs.indexOf(selected)) ? ' selected' : '') + '>' + esc(label) + '</option>';
                }).join('');
                return '<div class="dtw-fg-qpick"><label for="dtwFgTallyQ">Questions asked</label><select id="dtwFgTallyQ" class="dtw-in" aria-label="Question to show" onchange="dtwFgTallyQ(this.value)">' + opts + '</select></div>';
            }
            function tallyHtml(s, g, roster, fq) {
                var body;
                var fly0 = flyQ();
                if (!fq) body = '<div class="dtw-fg-th">The split.<span>Share of the room, by answer</span></div><div class="dtw-fg-quiet">Ask the first question and the split lands here: one bar, the people behind each answer, what the room knew and the voices.</div>' + roomFacts(g) + roomSeatsSec(g, roster, null, fly0);
                else if (qFailed(fq)) body = '<div class="dtw-fg-th">The split.<span>Share of the room, by answer</span></div><div class="dtw-fg-quiet">' + esc(fq.read || 'The room did not finish this question. Ask it again and it runs fresh.') + '</div>' + factsSec(fq) + roomSeatsSec(g, roster, fq, fly0);
                else {
                    var live = qInFlight(fq), opts = optsOf(fq), rk = rankMap(fq), seats = seatsTotal(fq), counts = live ? liveCounts(fq) : null, nA = (fq.answers || []).length;
                    body = live ? liveHead(nA, seats, 'the shares land when the last answer does') : '<div class="dtw-fg-th">The split.<span>Share of the room, by answer</span></div>';
                    if (!opts.length) { body += '<div class="dtw-fg-quiet">' + (live ? 'The options land with the first answers.' : 'An open question: the read and the voices carry it.') + '</div>'; }
                    else {
                        body += questionStack(fq);
                        body += opts.map(function(o) {
                            // while the room is still answering the row is the seat tally so far, in motion, and carries no percent: a
                            // share of the audience only exists once the count publishes (Jenna 2026-11-18: an audience of millions is
                            // never 0.0%, and a row that is still tallying must look like it). A published option with people behind it
                            // but no seat reads 'under a seat', not '0 of 16'.
                            if (live) return liveRow(o, counts[String(o.id)] || 0, seats, seriesOf(rk, o.id), rk[String(o.id)] === 0);
                            var v = Number(o.pct || 0);
                            var tally = ((o.seats_n === 0 || (o.tally && /^0 of /.test(String(o.tally)))) && v > 0 ? 'under a seat' : (o.tally || (o.seats_n != null && seats ? o.seats_n + ' of ' + seats : '')));
                            var det = [];
                            if (o.people != null) det.push(fmtN(o.people) + ' people');
                            if (tally) det.push(tally);
                            return '<div class="dtw-fg-lrow' + (rk[String(o.id)] === 0 ? ' lead' : '') + '"><span class="dtw-fg-dot" style="background:' + seriesOf(rk, o.id) + '"></span><div class="body"><div class="l1"><span class="lab">' + esc(o.label) + '</span>' + (det.length ? '<span class="det">' + det.join(SEP) + '</span>' : '') + '<b>' + pct(v, 1) + '</b></div>' + (o.context && !live ? '<div class="ctx">' + esc(o.context) + '</div>' : '') + '</div></div>';
                        }).join('');
                    }
                    // under the split: Responses and What the room knew fold (Jenna 2026-11-18), then the cohorts, then the room's faces
                    var voices = voicesAll(fq, roster), vkey = qId(fq, (s.questions || []).indexOf(fq));
                    S.fg.vctx = { q: fq, roster: roster, list: voices, key: vkey };
                    S.fg.vShown[vkey] = voices.length;
                    if (voices.length) body += foldSec('resp', 'Responses', esc(voices.length + (voices.length === 1 ? ' answer' : ' answers')), '<div class="dtw-fg-vtwo" id="dtwFgVoices">' + voices.map(function(v) { return voiceMini(v, roster, fq); }).join('') + '</div>');
                    var facts = ((fq || {}).facts_used || []).filter(Boolean), talk = ((fq || {}).talk || []).filter(Boolean);
                    if (facts.length || talk.length) body += foldSec('facts', 'What the room knew', esc(facts.length + (facts.length === 1 ? ' fact' : ' facts')) + (talk.length ? SEP + esc(talk.length + ' said') : ''), (factsSec(fq) + talkSec(fq)).replace(/<div class="dtw-fg-th">What the room knew\.<\/div>/, ''));
                    body += segmentsSec(fq, rk);
                    body += roomSeatsSec(g, roster, fq, fly0);
                }
                return qPickHtml(s, fq) + '<div class="dtw-fg-tally-in" id="dtwFgTallyIn">' + body + '</div>';
            }
            window.dtwFgTallyQ = function(id) {
                S.fg.tallyQ = id || null;
                var s = S.session, h = el('dtwFgTally'); if (!s || !h) { fgRefresh(); return; }
                var g = s.group || {}, roster = g.roster || [], fq = tallyFocusQ(), fly = flyQ();
                if (!paintFgDock(s, g, roster, fq, fly)) { fgRefresh(); return; }
                h.innerHTML = tallyHtml(s, g, roster, fq);
                fgLayoutSeats();
                var inn = el('dtwFgTallyIn'); if (inn) inn.scrollTop = 0;
            };
            // ---- the composer, under the thread, as the Prometheus chat: the kind chips, the field, Ask the room as the one Signal Green button.
            function composerHtml(typed, typedOpts) {
                var ready = fgAssets(S.session).filter(function(a) { return a.status === 'ready'; });
                var ph = ready.length >= 2 ? 'Ask about ' + ready[0].label + ', ' + ready[1].label + ' or both.' : (ready.length ? 'Ask about ' + ready[0].label + ', or anything else in plain words.' : (fgAssets(S.session).length ? 'Ask the room anything in plain words.' : 'Ask the room anything, or add a video, image or audio file to show it.'));
                var out = '<div class="dtw-fg-composer">' + assetsStripHtml(S.session) + '<div id="dtwPnKinds" class="dtw-fg-kinds">' + KINDS.map(function(k) { return '<button type="button" class="dtw-fg-chip' + (S.panelKind === k[0] ? ' on' : '') + '" data-k="' + k[0] + '" onclick="dtwPanelKind(\'' + k[0] + '\')">' + k[1] + '</button>'; }).join('') + '</div>';
                out += '<textarea id="dtwPnOptions" class="dtw-in dtw-fg-opts" style="display:' + ((S.panelKind === 'multiple_choice' || S.panelKind === 'ab') ? 'block' : 'none') + ';" placeholder="Options, one per line (or comma separated)">' + esc(typedOpts) + '</textarea>';
                out += '<div class="dtw-fg-field has-attach" id="dtwFgField" ondragover="event.preventDefault(); this.classList.add(\'over\');" ondragleave="this.classList.remove(\'over\');" ondrop="dtwPanelAssetDrop(event)">' + attachHtml() + '<input id="dtwPnQ" value="' + esc(typed) + '" placeholder="' + esc(ph) + '" aria-label="Ask the room" onpaste="dtwPanelAssetPaste(event)" onkeydown="if(event.key===\'Enter\'){dtwPanelAsk();}"><button type="button" id="dtwPnAskBtn" class="dtw-fg-send" onclick="dtwPanelAsk()">Ask the room<span class="arr"></span></button></div>';
                out += '<div class="dtw-fg-live" id="dtwPnLive"></div></div>';
                return out;
            }
            // ---- the theater (Jenna 2026-11-18, the approved design: 'the run over the table'). While a Predictive Analytics room
            // runs its planned questions the room is watched, not steered: the plan as a timeline across the top, the people around
            // a table with their faces (an answered seat lit with its answer on a chip, the rest dimmed), Prometheus at the head, the
            // question on the table, the live split and the latest answer at left, Leave at the foot. The composer is gone until
            // the outcome lands; then the room comes back with the outcome closing the thread.
            function shortCity(c) { return String(c || '').split(/ (?=St |Ft |Fort |Oakland|Sarasota|Jose|Worth|Petersburg|Tacoma)/)[0].trim(); }
            function stadiumSpots(n) {
                // seats around the table: a row across the head (Prometheus takes its center), more across the foot, the rest down the sides
                n = Math.max(0, Number(n) || 0); if (!n) return [];
                var side = Math.max(1, Math.round(n / 5.5)), rest = Math.max(0, n - 2 * side), top = Math.max(1, Math.floor(rest / 2) - (rest % 2 === 0 ? 1 : 0)), bottom = Math.max(0, rest - top);
                if (n <= 4) { side = 0; top = Math.ceil(n / 2); bottom = n - top; }
                var spots = [], i;
                for (i = 0; i < top; i++) spots.push([top === 1 ? 50 : 14 + (72 * i / (top - 1)), 9]);
                for (i = 0; i < side; i++) spots.push([97, side === 1 ? 50 : 24 + (52 * i / (side - 1))]);
                for (i = 0; i < bottom; i++) spots.push([bottom === 1 ? 50 : 86 - (72 * i / (bottom - 1)), 91]);
                for (i = 0; i < side; i++) spots.push([3, side === 1 ? 50 : 76 - (52 * i / (side - 1))]);
                return spots;
            }
            function theaterHtml(s) {
                var g = s.guided || {}, plan = g.plan || {}, qs = plan.questions || [], done = Number(g.done || 0), total = Number(g.total || qs.length || 0);
                var roster = (s.group || {}).roster || [], fly = flyQ(), byI = {};
                (s.questions || []).forEach(function(q) { if (q && q.guided_i != null && !(byI[q.guided_i] && !qInFlight(byI[q.guided_i]) && qInFlight(q))) byI[q.guided_i] = q; });
                var nowI = fly && fly.guided_i != null ? Number(fly.guided_i) : Math.min(done, Math.max(0, qs.length - 1));
                // the timeline
                var tl = qs.map(function(q, i) {
                    var rec = byI[i], fin = rec && !qInFlight(rec) && !qFailed(rec), st = fin ? 'done' : (i === nowI ? 'now' : 'wait');
                    var lead = '';
                    if (fin) { var top = (rec.distribution || []).slice().sort(function(a, b) { return Number(b.pct || 0) - Number(a.pct || 0); })[0]; lead = top ? esc(top.label) + ' ' + pct(top.pct, 1) : (rec.read ? 'Answered' : ''); }
                    else if (rec && qFailed(rec)) { st = 'done'; lead = 'Did not finish'; }
                    return '<div class="dtw-th-q ' + st + '"><div class="ln"><span class="dot"></span><span class="bar"></span></div><div class="lv">' + esc(PA_LEVELS[q.level] || '') + '</div><div class="qt">' + esc(q.q || '') + '</div><div class="ld">' + (st === 'now' ? '<span class="dtw-fg-pulse"></span>on the floor' : lead) + '</div></div>';
                }).join('');
                // the split so far (the question on the floor), in motion
                var split, rk = fly ? rankMap(fly) : {}, opts = fly ? optsOf(fly) : [], counts = fly ? liveCounts(fly) : {};
                if (fly && opts.length) split = opts.map(function(o) { return '<div class="dtw-th-srow"><span class="dtw-fg-dot" style="background:' + seriesOf(rk, o.id) + '"></span><span class="l">' + esc(o.label) + '</span><b>' + (counts[String(o.id)] || 0) + '</b><span class="dtw-fg-rowspin" aria-hidden="true"></span></div>'; }).join('') + '<div class="dtw-th-note">Shares land when the last answer does.</div>';
                else if (fly) split = '<div class="dtw-th-note">An open question: the voices carry it.</div>';
                else split = '<div class="dtw-th-note"><span class="dtw-fg-pulse"></span>The next question is on its way.</div>';
                // just in: the latest answer that landed
                var srcQ = (fly && (fly.answers || []).length) ? fly : null;
                if (!srcQ) { for (var k = qs.length - 1; k >= 0 && !srcQ; k--) { if (byI[k] && (byI[k].answers || []).length && !qInFlight(byI[k])) srcQ = byI[k]; } }
                var justin = '';
                if (srcQ) {
                    var a = (srcQ.answers || []).slice(-1)[0], seat = roster.filter(function(r) { return Number(r.seat) === Number(a.seat); })[0] || { name: a.name, seat: a.seat };
                    var srk = rankMap(srcQ), oid = answerOptId(srcQ, a), lab = a.option_label || optionLabelOf(srcQ, oid), col = oid ? seriesOf(srk, oid) : 'var(--dtw-accent)';
                    var idx = seat.seat != null ? Number(seat.seat) - 1 : 0, url = seat.portrait_url ? thumb(seat.portrait_url) : '';
                    justin = '<div class="dtw-th-justin"><div class="k">Just in</div><div class="row"><span class="dtw-th-face s" style="--hue:' + seatHue(seat, idx) + ';--ring:' + col + '">' + (url ? '<img src="' + esc(url) + '" alt="">' : esc(initials(seat.name || ''))) + '</span><div><div class="who">' + esc(titleCase(seat.name || '')) + (lab ? ' <span style="color:' + col + '">' + SEP + esc(lab) + '</span>' : '') + '</div>' + (a.line ? '<div class="tx">' + esc(a.line) + '</div>' : '') + '</div></div></div>';
                }
                // the people around it
                var bySeat = {}; if (fly) (fly.answers || []).forEach(function(x) { bySeat[String(x.seat)] = x; });
                var spots = stadiumSpots(roster.length);
                // the table (Jenna 2026-11-19: the rim of B, the inner circle of E): the rim is one piece per person, nearest their seat,
                // lit in the color of their answer as it lands; the latest answer speaks from the table under the question
                var onQ = fly ? String(fly.q || '') : String((qs[nowI] || {}).q || ''), onLvl = PA_LEVELS[(qs[nowI] || {}).level] || '', nA = fly ? (fly.answers || []).length : 0, seats = roster.length;
                var tcopy = '<div class="dtw-th-tcopy' + (srcQ && fly === srcQ ? ' talk' : '') + '"><div class="k">' + (fly ? 'On the table' : 'Up next') + (onLvl ? SEP + esc(onLvl) : '') + (fly ? SEP + nA + ' of ' + seats : '') + '</div><div class="q">' + esc(onQ) + '</div>';
                if (srcQ && fly === srcQ) {
                    var la = (fly.answers || []).slice(-1)[0], ls = roster.filter(function(r) { return Number(r.seat) === Number(la.seat); })[0] || { name: la.name, seat: la.seat };
                    var loid = answerOptId(fly, la), llab = la.option_label || optionLabelOf(fly, loid), lcol = loid ? seriesOf(rk, loid) : 'var(--dtw-accent)', lidx = ls.seat != null ? Number(ls.seat) - 1 : 0, lurl = ls.portrait_url ? thumb(ls.portrait_url) : '';
                    tcopy += '<div class="dtw-th-floor"><span class="dtw-th-face m" style="--hue:' + seatHue(ls, lidx) + ';--ring:' + lcol + '">' + (lurl ? '<img src="' + esc(lurl) + '" alt="">' : esc(initials(ls.name || ''))) + '</span><div class="t"><div class="who">' + esc(titleCase(ls.name || '')) + (ls.age != null && ls.age !== '' ? ', ' + esc(ls.age) : '') + (ls.city ? ', ' + esc(titleCase(shortCity(ls.city))) : '') + (llab ? ' <span style="color:' + lcol + '">' + SEP + esc(llab) + '</span>' : '') + '<span class="when">' + SEP + 'just now</span></div>' + (la.line ? '<div class="tx">' + esc(la.line) + '</div>' : '') + '</div></div>';
                } else {
                    tcopy += '<div class="n">' + (fly ? 'Waiting on the first answer' : (done ? done + ' of ' + total + ' answered so far' : 'The room is seated')) + '</div>';
                }
                tcopy += '</div>';
                // the rim pieces: each seat's piece sits nearest its seat, between the midpoints to its neighbors around the ellipse
                var VW = 1000, VH = 645, ecx = VW / 2, ecy = VH / 2, erx = VW * 0.33, ery = VH * 0.27, rrx = erx + 13, rry = ery + 13;
                var angs = roster.map(function(r, i) { var sp0 = spots[i] || [50, 50]; return { i: i, t: Math.atan2((sp0[1] - 50) / 50 * (VH / 2) / rry, (sp0[0] - 50) / 50 * (VW / 2) / rrx) }; });
                angs.sort(function(p, q2) { return p.t - q2.t; });
                var pieces = '';
                angs.forEach(function(p, k) {
                    var prev = angs[(k - 1 + angs.length) % angs.length].t, next = angs[(k + 1) % angs.length].t;
                    if (k === 0) prev -= 2 * Math.PI;
                    if (k === angs.length - 1) next += 2 * Math.PI;
                    var a0 = (p.t + prev) / 2 + 0.045, a1 = (p.t + next) / 2 - 0.045;
                    if (a1 <= a0) return;
                    var r = roster[p.i], an = bySeat[String(r.seat)], oid3 = an ? answerOptId(fly, an) : '', col3 = oid3 ? seriesOf(rk, oid3) : (an ? 'var(--dtw-accent)' : 'var(--dtw-s5)');
                    var x0 = ecx + rrx * Math.cos(a0), y0 = ecy + rry * Math.sin(a0), x1 = ecx + rrx * Math.cos(a1), y1 = ecy + rry * Math.sin(a1);
                    pieces += '<path d="M' + x0.toFixed(1) + ',' + y0.toFixed(1) + ' A' + rrx.toFixed(1) + ',' + rry.toFixed(1) + ' 0 ' + (a1 - a0 > Math.PI ? 1 : 0) + ' 1 ' + x1.toFixed(1) + ',' + y1.toFixed(1) + '" class="' + (an ? 'on' : '') + '" style="stroke:' + col3 + '" vector-effect="non-scaling-stroke"/>';
                });
                var table = '<svg class="dtw-th-svg" viewBox="0 0 ' + VW + ' ' + VH + '" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="dtwThTable" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#192D34"/><stop offset="0.6" stop-color="#15252A"/><stop offset="1" stop-color="#0F1E1E"/></linearGradient></defs>'
                    + '<ellipse class="top" cx="' + ecx + '" cy="' + ecy + '" rx="' + erx + '" ry="' + ery + '" fill="url(#dtwThTable)" vector-effect="non-scaling-stroke"/>'
                    + '<ellipse class="edge" cx="' + ecx + '" cy="' + ecy + '" rx="' + (erx - 14) + '" ry="' + (ery - 14) + '" fill="none" vector-effect="non-scaling-stroke"/>'
                    + '<g class="rim">' + pieces + '</g></svg>' + tcopy;
                var people = roster.map(function(r, i) {
                    var a2 = bySeat[String(r.seat)], oid2 = a2 ? answerOptId(fly, a2) : '', col2 = oid2 ? seriesOf(rk, oid2) : (a2 ? 'var(--dtw-accent)' : ''), lab2 = a2 ? (a2.option_label || optionLabelOf(fly, oid2)) : '';
                    var sp = spots[i] || [50, 50], url2 = r.portrait_url ? thumb(r.portrait_url) : '';
                    return '<div class="dtw-th-seat' + (a2 ? ' on' : '') + (S.fg.seat === Number(r.seat) ? ' sel' : '') + '" style="left:' + sp[0] + '%;top:' + sp[1] + '%;--hue:' + seatHue(r, i) + ';--ring:' + (col2 || 'transparent') + '" data-fg-seat="' + Number(r.seat) + '" role="button" tabindex="0" title="Who ' + esc(titleCase(r.name || '')) + ' is" onclick="dtwSeatOpen(' + Number(r.seat) + ', this)" onkeydown="if(event.key===\'Enter\'){dtwSeatOpen(' + Number(r.seat) + ', this)}">'
                        + (lab2 ? '<span class="chip">' + esc(lab2) + '</span>' : '') + '<span class="dtw-th-face">' + (url2 ? '<img src="' + esc(url2) + '" alt="" onerror="this.remove()">' : esc(initials(r.name || ''))) + '</span><b>' + esc(titleCase(r.name || '')) + '</b><span class="ag">' + esc(r.age != null ? r.age : '') + (r.city ? ', ' + esc(titleCase(shortCity(r.city))) : '') + '</span></div>';
                }).join('');
                var stageLine = fly ? '<span class="dtw-fg-pulse"></span><span>' + esc(cap(stageWords(fly))) + SEP + '<span id="dtwFgTheaterEl">' + qElapsedS(fly) + '</span>s</span>' : '<span class="dtw-fg-pulse"></span><span>Between questions</span>';
                return '<div class="dtw-th" id="dtwFgTheater">'
                    + '<div class="dtw-th-head"><span class="dtw-th-badge"><span class="dtw-fg-pulse"></span>Watching</span><span class="dtw-th-run">The run' + SEP + '<b>' + done + ' of ' + total + '</b> answered' + SEP + 'question ' + (nowI + 1) + ' on the floor</span><span class="dtw-th-stage">' + stageLine + '</span></div>'
                    + '<div class="dtw-th-tl">' + tl + '</div>'
                    + '<div class="dtw-th-body"><div class="dtw-th-side"><div class="dtw-th-split"><div class="k">The split so far</div>' + split + '</div>' + (srcQ && fly === srcQ ? '' : justin) + '</div>'
                    + '<div class="dtw-th-stagewrap"><div class="dtw-th-mod" title="Prometheus">' + MARK_SVG + '</div>' + table + people + '</div></div>'
                    + '<div class="dtw-th-foot"><span>Prometheus is running the planned questions for you. The outcome and the report land with question ' + total + '; you can ask your own then.</span><button type="button" class="dtw-fg-act" onclick="dtwPanelClose()">Leave</button></div>'
                    + '</div>';
            }
            function actsHtml(s) {
                var n = ((S.twin || {}).panel_sessions || []).length;
                var acts = '<span class="dtw-fg-saved" id="dtwPnSaved"></span><button type="button" class="dtw-fg-act" onclick="dtwRoomsOpen(true)">Rooms<b id="dtwFgRoomsN">' + n + '</b></button><button type="button" class="dtw-fg-act" onclick="dtwNewRoomOpen(true)">New room</button>';
                if (s) acts += '<button type="button" class="dtw-fg-act" onclick="dtwPanelSave()">Save</button><a class="dtw-fg-act" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/panel/' + esc(s.id) + '/export.csv">Export CSV</a><a class="dtw-fg-act" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/panel/' + esc(s.id) + '/export.pdf">Export PDF</a><button type="button" class="dtw-fg-act" title="Duplicate this room: the same twins, cohorts and seats, no questions yet" onclick="dtwPanelDuplicate(\'' + esc(s.id) + '\')">Duplicate</button><button type="button" class="dtw-fg-act close" title="Close this room (Escape). It stays under Rooms." aria-label="Close room" onclick="dtwPanelClose()"><span class="x" aria-hidden="true">&#215;</span>Close room</button>';
                return acts;
            }
            function syncFgActs() {
                var on = S.tab === 'panel', room = on && !!S.session;
                var spine = el('dtwFgActs');
                if (spine) { spine.hidden = !on || room; if (on && !room) spine.innerHTML = actsHtml(null); }
                var dock = el('dtwFgDockActs');
                if (dock) dock.innerHTML = room ? actsHtml(S.session) : '';
            }
            function popHtml() { return '<div id="dtwSeatPop" class="dtw-fg-pop" role="dialog" aria-label="Who they are"><span class="arrow"></span><button type="button" class="close" aria-label="Close" onclick="dtwSeatOpen(null)">&#215;</button><div class="dtw-fg-pop-in" id="dtwSeatBody"></div></div>'; }
            // ---- the landing, no room open (Jenna 2026-10-27: 'CAN you redesign this page to look better I dont like the circles'): no placeholder
            // faces. The claim and the primary action, the three steps, the saved rooms as cards (three real portraits of each room's seats), the
            // Prometheus examples as a small block. With no twin: the same hero, 'Pick a twin first', the twin switcher as the primary action.
            function cardFaces(s) {
                var roster = ((s || {}).group || {}).roster || [], n = roster.length;
                var out = roster.slice(0, 3).map(function(r, i) { var nm = titleCase(r.name || ''); return '<span class="dtw-fg-cface" style="--hue:' + seatHue(r, i) + '" title="' + esc(nm) + '">' + esc(initials(nm)) + (r.portrait_url ? '<img src="' + esc(thumb(r.portrait_url)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span>'; }).join('');
                if (n > 3) out += '<span class="dtw-fg-cmore">+' + (n - 3) + '</span>';
                return n ? '<div class="dtw-fg-faces">' + out + '</div>' : '';
            }
            function roomCard(s) {
                var qs = s.questions || [], n = qs.length, sid = esc(s.id), working = qs.some(qInFlight), last = n ? qs[n - 1] : null;
                var dup = SEP + '<button type="button" class="dtw-link" title="Duplicate this room" onclick="event.stopPropagation(); dtwPanelDuplicate(\'' + sid + '\')">duplicate</button>';
                var meta = (n ? '<span>' + n + (n === 1 ? ' question' : ' questions') + '</span>' + SEP + '<span>' + esc(fmtDay(s.updated_at || s.created_at)) + '</span>'
                    : '<span>empty</span>' + SEP + '<button type="button" class="dtw-link" onclick="event.stopPropagation(); dtwPanelDeleteAsk(\'' + sid + '\', true, \'dtwDelC-\')">delete</button>') + dup;
                return '<div class="dtw-fg-card' + (n ? '' : ' empty') + '" role="button" tabindex="0" aria-label="Open the room ' + esc(roomTitle(s)) + '" onclick="dtwPanelResume(\'' + sid + '\')" onkeydown="if(event.key===\'Enter\'&&event.target===this){dtwPanelResume(\'' + sid + '\');}">'
                    + cardFaces(s)
                    + '<div class="t"><span>' + esc(roomTitle(s)) + '</span>' + (s.guided ? '<span class="dtw-fg-tag">Predictive Analytics</span>' : '') + (working ? '<span class="dtw-fg-tag"><span class="dtw-fg-dot" style="background:var(--dtw-accent)"></span>working</span>' : '') + '</div>'
                    + (last ? '<div class="q">' + esc(last.q || '') + '</div>' : '')
                    + '<div class="m">' + meta + '</div>'
                    + '<button type="button" class="del" title="Delete this room" aria-label="Delete this room" onclick="event.stopPropagation(); dtwPanelDeleteAsk(\'' + sid + '\', null, \'dtwDelC-\')">&#215;</button>'
                    + '<div class="dtw-confirm" id="dtwDelC-' + sid + '" style="display:none;" onclick="event.stopPropagation()"><span>Delete this room? Its questions and answers go with it.</span><button type="button" class="dtw-fg-act" onclick="event.stopPropagation(); dtwPanelDelete(\'' + sid + '\')">Delete</button><button type="button" class="dtw-fg-act" onclick="event.stopPropagation(); dtwPanelDeleteAsk(\'' + sid + '\', false, \'dtwDelC-\')">Keep</button></div>'
                    + '</div>';
            }
            function noRoomHtml() {
                var t = S.twin, nm = twinName(), it = ((S.howto || {}).items || []).filter(function(x) { return x.id === 'panel'; })[0] || {};
                var ss = (t || {}).panel_sessions || [], runs = S.runs || [];
                var examples = (it.type_this || ['Open a focus group with the ' + (nm || 'Gilmore Girls') + ' twin and ask whether they would watch a new season', 'Ask them why']).slice(0, 3);
                var out = '<div class="dtw-fg-land">';
                if (t) {
                    var seats = ((t.archetypes || {}).k) || 12;
                    out += '<div class="dtw-fg-land-hero"><h2 class="dtw-fg-land-h">Put the twins in a room <b>and ask.</b></h2><p class="dtw-fg-land-sub"><b>' + seats + ' twins</b> stand for <b>' + fmtN((t.meta || {}).size_us) + '</b> people. Ask in plain words; the room answers with the split, who said what, and why.</p><div class="dtw-fg-land-acts"><button type="button" class="dtw-fg-act go" onclick="dtwNewRoomOpen(true)">Open a room</button><button type="button" class="dtw-fg-act" onclick="dtwChatFill(\'' + esc(jsq(examples[0])) + '\')">Type it to Prometheus</button></div></div>';
                } else {
                    out += '<div class="dtw-fg-land-hero"><h2 class="dtw-fg-land-h">Pick a twin <b>first.</b></h2><p class="dtw-fg-land-sub">A focus group runs on a twin, the audience its seats stand for. Prometheus moderates. ' + (runs.length ? 'Pick one here, then open a room.' : 'Build the first one with Build a twin, then open a room.') + '</p><div class="dtw-fg-land-acts">' + (runs.length ? '<select class="dtw-in" aria-label="Pick a twin" onchange="dtwSwitchTwin(this.value)"><option value="">Pick a twin</option>' + runs.map(function(r) { return '<option value="' + esc(r.key) + '">' + esc(twinOptionLabel(r)) + '</option>'; }).join('') + '</select><button type="button" class="dtw-fg-act" onclick="dtwBuildTwin()">Build a twin</button>' : '<button type="button" class="dtw-fg-act go" onclick="dtwBuildTwin()">Build a twin</button>') + '</div></div>';
                }
                out += '<div class="dtw-fg-steps"><div class="dtw-fg-step"><span class="n">01</span><b>Open a room</b><span>Name it, then choose the twins or cohorts and how many seats.</span></div><div class="dtw-fg-step"><span class="n">02</span><b>Ask</b><span>Yes/no, multiple choice, rating, price, A vs B, show of hands, open question, probes.</span></div><div class="dtw-fg-step"><span class="n">03</span><b>Read the split</b><span>Percentages and people, who said what and why, the facts the room knew.</span></div></div>';
                out += '<div class="dtw-fg-land-foot"><span>' + esc(String((groupCard() && groupQuote(groupTierDefault()).replace(/^This group/, 'A room')) || it.cost || 'A room is one group at the plan rate, charged when it opens').replace(/\.$/, '')) + '.</span><button type="button" class="dtw-link" onclick="dtwHowtoOpen(true)">Full directions</button></div>';
                if (t && ss.length) {
                    var when = function(s) { return String(s.updated_at || s.created_at || ''); };
                    var live = ss.filter(function(s) { return (s.questions || []).length; }).sort(function(a, b) { return when(b).localeCompare(when(a)); }), empty = ss.filter(function(s) { return !(s.questions || []).length; });
                    out += '<div class="dtw-fg-land-rooms"><div class="dtw-fg-land-head"><h3>Saved rooms</h3><span>' + ss.length + (ss.length === 1 ? ' room' : ' rooms') + ' on ' + esc(nm) + '. Each one resumes where you left it.</span></div><div class="dtw-fg-cards">' + live.concat(empty).map(roomCard).join('') + '</div></div>';
                }
                out += '<div class="dtw-fg-land-pm"><div class="k">Or say it to Prometheus</div>' + examples.map(function(x) { return '<button type="button" class="dtw-fg-chip" onclick="dtwChatFill(\'' + esc(jsq(x)) + '\')">' + esc(x) + '</button>'; }).join('') + '</div>';
                return out + '</div>';
            }
            // The console: the seats, then the question dock (pills + title), the thread and the tally.
            // An open room: pills sit on the question dock, the freeze point.
            // ---- the read a person is mailed once their room session ended (Jenna 2026-11-20). The page only hints: 'here' when a room
            // comes into view and every five minutes while it stays there, 'leaving' when it goes out of view (the room closed, another
            // tab or product, the page unloading). A browser tab in the background is not a leave; the server's quiet period covers it.
            var RP = { key: '', sid: '', showing: false, lastHere: 0, timer: null };
            function rpInView() {
                var v = el('digitalTwinsIQView');
                if (!v || !S.session || !S.key || S.tab !== 'panel') return false;
                try { if (getComputedStyle(v).display === 'none') return false; } catch (_) {}
                return true;
            }
            function rpSend(key, sid, state, beacon) {
                if (!key || !sid) return;
                var url = '/api/digital-twins/' + key + '/panel/' + sid + '/presence', body = JSON.stringify({ state: state });
                if (beacon) { try { if (navigator.sendBeacon && navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))) return; } catch (_) {} }
                try { fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body, keepalive: true }).catch(function() {}); } catch (_) {}
            }
            function rpTick(beacon) {
                var inView = rpInView(), key = inView ? S.key : '', sid = inView ? S.session.id : '';
                if (inView && (!RP.showing || RP.key !== key || RP.sid !== sid)) {
                    if (RP.showing && RP.sid && (RP.key !== key || RP.sid !== sid)) rpSend(RP.key, RP.sid, 'leaving');
                    RP.key = key; RP.sid = sid; RP.showing = true; RP.lastHere = Date.now();
                    rpSend(key, sid, 'here');
                    return;
                }
                if (!inView && RP.showing) { RP.showing = false; rpSend(RP.key, RP.sid, 'leaving', !!beacon); return; }
                if (inView && document.visibilityState !== 'hidden' && Date.now() - RP.lastHere > 5 * 60 * 1000) { RP.lastHere = Date.now(); rpSend(key, sid, 'here'); }
            }
            function rpStart() { if (RP.timer) return; RP.timer = setInterval(function() { try { rpTick(); } catch (_) {} }, 15000); }
            rpStart();
            window.addEventListener('pagehide', function() { try { if (RP.showing) { RP.showing = false; rpSend(RP.key, RP.sid, 'leaving', true); } } catch (_) {} });
            window.addEventListener('pageshow', function() { try { rpTick(); } catch (_) {} });
            function renderPanelConsole() {
                var h = el('dtwPnConsole'); if (!h) return;
                syncFgBody();
                try { rpTick(); } catch (_) {}
                var s = S.session;
                if (!s) { var had = S.fg.sid != null; clearRoomPoll(); clearPortraitTimers(); fgReset(null); h.innerHTML = noRoomHtml(); fgLayoutSeats(); if (had) fgScrollTop(); return; }
                var fresh = S.fg.sid !== s.id;
                fgReset(s.id);
                var g = s.group || {}, roster = g.roster || [], fq = tallyFocusQ(), latest = focusQ(), fly = flyQ();
                if (paRunning(s)) {
                    // theater (Jenna 2026-11-18): a Predictive Analytics room running for you is watched, not steered
                    h.innerHTML = theaterHtml(s) + popHtml();
                    syncFgActs();
                    if (fresh) fgScrollTop();
                    if (S.fg.seat != null) { renderSeatPanel(); placePop(); }
                    schedulePortraitRefresh();
                    scheduleRoomPoll();
                    return;
                }
                var typed = (el('dtwPnQ') || {}).value || '', typedOpts = (el('dtwPnOptions') || {}).value || '';
                var out = '<div class="dtw-fg-hero" id="dtwFgHero">' + heroHtml(s, g, roster, fq, fly) + '</div>';
                out += '<div class="dtw-fg-dock" id="dtwFgDock">' + dockHtml(s, g, roster, fq, fly) + '</div>';
                out += '<div class="dtw-fg-main"><div class="dtw-fg-chat"><div class="dtw-fg-stream" id="dtwPnTranscript">' + threadHtml(s, roster, latest, fly) + '</div>' + composerHtml(typed, typedOpts) + '</div>';
                out += '<aside class="dtw-fg-tally" id="dtwFgTally" aria-label="The split">' + tallyHtml(s, g, roster, fq) + '</aside></div>';
                out += popHtml();
                h.innerHTML = out;
                syncFgActs();
                fgLayoutSeats();
                if (fresh) fgScrollTop();
                setAskEnabled(true);
                if (S.fg.seat != null) { renderSeatPanel(); placePop(); }
                schedulePortraitRefresh();
                scheduleRoomPoll();
                scheduleAssetPoll();
            }
            // A refresh while the room works or a portrait lands: the band, the thread and the tally redraw; the composer keeps what was typed.
            function fgRefresh() {
                var s = S.session, h = el('dtwPnConsole'); if (!s || !h) { renderPanelConsole(); return; }
                // the stage and the room trade places when a Predictive Analytics run starts or lands (2026-11-18)
                if (!!el('dtwFgTheater') !== paRunning(s)) { renderPanelConsole(); return; }
                if (paRunning(s)) {
                    var stage = el('dtwFgTheater'); if (stage) stage.outerHTML = theaterHtml(s);
                    syncFgActs();
                    if (S.fg.seat != null) { renderSeatPanel(); placePop(); }
                    return;
                }
                if (!el('dtwFgHero') || !el('dtwFgDock')) { renderPanelConsole(); return; }
                var g = s.group || {}, roster = g.roster || [], fq = tallyFocusQ(), latest = focusQ(), fly = flyQ();
                paintFgDock(s, g, roster, fq, fly);
                var tr = el('dtwPnTranscript');
                if (tr) { var atEnd = fgAtEnd(); tr.innerHTML = threadHtml(s, roster, latest, fly); if (atEnd) fgScrollEnd(); }
                var ta = el('dtwFgTally'); if (ta) { ta.innerHTML = tallyHtml(s, g, roster, fq); fgLayoutSeats(); }
                syncFgActs();
                setAskEnabled(true);
                if (S.fg.seat != null) placePop();
            }
            // The thread is the left scroller. Opening a room scrolls the page (the view) to its top.
            function fgPage() { return el('digitalTwinsIQView'); }
            function fgScroller() { return el('dtwPnTranscript') || document.querySelector('#digitalTwinsIQView .dtw-body'); }
            function fgAtEnd() { var sc = fgScroller(); return !sc || (sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80); }
            function fgScrollEnd() { var sc = fgScroller(); if (sc) sc.scrollTop = sc.scrollHeight; }
            function fgScrollTop() { var page = S.session ? fgPage() : fgScroller(); if (page) page.scrollTop = 0; var sc = fgScroller(); if (sc && sc !== page) sc.scrollTop = 0; }
            // bring a block into the scrollport: its top a little under the edge when it sits below, or above, the visible band
            function fgScrollTo(node, pad) {
                var sc = fgScroller(); if (!sc || !node) return;
                var sr = sc.getBoundingClientRect(), nr = node.getBoundingClientRect();
                if (nr.top < sr.top + 8 || nr.bottom > sr.bottom - 8) sc.scrollTop += nr.top - sr.top - (pad == null ? 12 : pad);
            }
            // the note under the composer; when it appears the composer grows, so a thread that sat at its end stays there
            function liveNote(text) { var live = el('dtwPnLive'); if (!live) return; live.textContent = text || ''; }
            function liveNoteHtml(html) { var live = el('dtwPnLive'); if (!live) return; live.innerHTML = html || ''; }
            // a 1-20 room at twenty questions: move it to 1-40 for the difference at the company's plan rate, then the waiting question runs (Jenna 2026-11-05)
            window.dtwPanelUpgrade = async function() {
                if (!S.key || !S.session) return;
                liveNote('Moving this room to 1-40 questions...');
                try { var d = await api('/' + S.key + '/panel/' + S.session.id + '/upgrade', { method: 'POST', body: '{}' }); }
                catch (e) { liveNote(e.message); if (e.doc && e.doc.add_funds_url) liveNoteHtml(esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>'); return; }
                if (d.session) { S.session.questions_paid = d.session.questions_paid; S.session.group_tier = d.session.group_tier; }
                liveNote('This room now answers up to 40 questions.');
                var pending = S.fg.pendingQ; S.fg.pendingQ = '';
                if (pending) { try { await dtwPanelAsk(pending); } catch (_) {} }
            };
            function fgShown() { fgLayoutSeats(); fgDockSize(); if (S.fg.seat != null) placePop(); }
            window.dtwFgToggle = function(qid) { S.fg.open[qid] = !S.fg.open[qid]; fgRefresh(); var blk = document.querySelector('#dtwPnTranscript [data-q="' + esc(qid) + '"]'); if (blk && S.fg.open[qid]) fgScrollTo(blk); };
            window.dtwFgEarlier = function(qid, on) { S.fg.earlier[qid] = !!on; fgRefresh(); if (!on) { var blk = document.querySelector('#dtwPnTranscript [data-q="' + esc(qid) + '"]'); if (blk) fgScrollTo(blk); } };
            // ---- the drawers: the saved rooms behind Rooms, the room builder behind New room (the drawer component How to uses).
            // ---------------------------------------------------------------- Predictive Analytics (Jenna 2026-11-18)
            // Its own tab: the audience, the business question, the plan Prometheus wrote (the big picture down to the question itself),
            // the run, and the report of the whole process. The focus group stays for the depth and the control.
            var PA_STEPS = ['Audience', 'Question', 'Plan', 'Run', 'Report'];
            var PA_KINDS = [
                ['effect', 'If I do X, will Y happen?', 'A change you could make and the result you want from it.', 'If we add a holiday boxed set at Target, will more people buy season two?'],
                ['contest', 'Will X win?', 'Two or more options; which one the audience picks.', 'Will the Flashdance cut or the NCIS cut win them over for a Lululemon spot?'],
                ['go', 'Should I do X?', 'A go or no-go call on one move.', 'Should we sign her for a two-year activewear deal?'],
                ['price', 'What will they pay?', 'A price, a tier or a bundle.', 'Will this audience pay $45 for a hoodie from her collection?'],
                ['open', 'Something else', 'Any question about this audience.', 'What would it take for this audience to see her as a fashion name, not a nostalgia act?']
            ];
            var PA_LEVELS = { macro: 'The big picture', mid: 'Closer in', micro: 'The question itself' };
            // the sentence's connecting words per shape (Jenna 2026-11-23, PA1 'the sentence you fill in'): Ask [twin] <join> [what you type];
            // lead is how the typed clause is put to the room when it is not already a question of its own
            var PA_JOIN = {
                '': { join: 'whether', lead: 'Whether' },
                effect: { join: 'whether', lead: 'Whether' },
                contest: { join: 'which', lead: 'Which' },
                go: { join: 'whether', lead: 'Whether' },
                price: { join: 'what they would pay for', lead: 'What they would pay for' },
                open: { join: 'about', lead: '' }
            };
            var PA_BUILD_KEY = '__build__';   // the dropdown's first option (Jenna 2026-11-23: 'the first option is build new twin group')
            // the audience the sentence asks, not the person (Jenna 2026-11-23): the twin's name plus its own people noun, unless the
            // name already names people (texas voters, PValley on Starz Viewers)
            var PA_PEOPLE_WORDS = /\b(viewers?|voters?|fans?|subscribers?|followers?|members?|buyers?|shoppers?|listeners?|players?|customers?|users?|readers?|people|audiences?|households?|consumers?|parents|moms|dads|owners?|gamers?|watchers?|guests?|travel(?:l)?ers?|drinkers?|eaters?|riders?|drivers?|renters?|homeowners?|students?|graduates?|teens?|adults?|women|men|kids|families)\b/i;
            function paAudienceNoun(key) {
                var fromTwin = (S.key === key && S.twin) ? String(((S.twin || {}).archetypes || {}).noun || '') : '';
                var r = paRun(key);
                return fromTwin || String((r && r.noun) || '');
            }
            function paAudiencePhrase(key) {
                var name = paTwinName(key); if (!name) return '';
                if (PA_PEOPLE_WORDS.test(name)) return name;
                var noun = paAudienceNoun(key);
                if (noun && noun !== 'people') return name + ' ' + noun;
                return 'the ' + name + ' audience';
            }
            // the seat's footing for a run, one source (Jenna 2026-11-23: the start's line had said 'Crosswalk seats are never charged' twice,
            // once inside a parenthesis the quote added and once after it): free (a Crosswalk seat), included (a plan that covers the run), paying
            function paSeatKind() { var c = groupCard(); if (!c) return 'paying'; if (c.free) return 'free'; if (c.included) return 'included'; return 'paying'; }
            function paPriceLine() {
                var k = paSeatKind();
                if (k === 'free') return 'Planning and the run are free for Crosswalk seats.';
                if (k === 'included') return 'Planning is free. The run is one room, included with your plan.';
                return 'Planning is free. The run is one room at your plan rate.';
            }
            function paRunNote() { return 'Runs when you approve the plan'; }   // the index's Run row names the state; the price is said once, beside Plan it
            function paFresh() { return { step: 0, key: S.key || '', picked: false, full: false, goal: '', kind: '', plan: null, sid: '', job: '', report: null, guided: null, busy: false, err: '', runs: null, runsKey: '', pollT: null }; }
            function pa() { if (!S.pa) S.pa = paFresh(); return S.pa; }
            function paRun(key) { return (S.runs || []).filter(function(r) { return r.key === key; })[0] || null; }
            function paTwinName(key) { var r = paRun(key); return r ? (r.name || r.subject || '') : (key === S.key ? twinName() : ''); }
            function paPeople(key) { var r = paRun(key); return r && r.size_us ? Number(r.size_us) : (key === S.key ? Number(((S.twin || {}).meta || {}).size_us || 0) : 0); }
            function paClearPoll() { var p = pa(); if (p.pollT) { clearTimeout(p.pollT); p.pollT = null; } }
            // the five steps as a numbered dot-leader index that fills in as the user goes (PA1); the active step in Signal Green,
            // every completed step a way back. On the start it sits under the sentence; on Plan, Run and Report it is the rail up top.
            function paIndexStatus(p, i) {
                var r = paRun(p.key), g = p.guided || {}, o = (p.report || {}).outcome || null;
                if (i === 0) return ((p.picked || p.step >= 2) && p.key) ? (esc(paTwinName(p.key)) + (paPeople(p.key) ? ', ' + fmtN(paPeople(p.key)) + ' people' : '') + (r && r.kinds ? ', ' + r.kinds + ' archetypes' : '')) : 'Select twin';
                if (i === 1) { var k = PA_KINDS.filter(function(x) { return x[0] === p.kind; })[0]; var typed = (p.goal || '').trim(); return typed ? esc(k ? k[1] : 'In your words') : (k ? esc(k[1]) : 'What you type above'); }
                if (i === 2) { var qs = ((p.plan || {}).questions || []).length; return qs ? qs + ' questions, from the wide to the narrow' : 'Written for you when you press Plan it'; }
                if (i === 3) { if (g.status === 'done') return 'Done'; if (g.status === 'stopped') return (g.done || 0) + ' of ' + (g.total || 0) + ' answered, stopped'; if (g.status === 'running') return (g.done || 0) + ' of ' + (g.total || 0) + ' answered'; return paRunNote(); }
                return o ? 'Verdict: ' + esc(paCallWord(o)) : 'The verdict, the numbers, who drove it';
            }
            function paIndex(p, compact) {
                var active = p.step <= 1 ? ((p.picked && p.key) ? 1 : 0) : p.step;
                var desc = ['The twin whose answer you need.', 'Your business question, in your words.', 'Five to eight questions, from the wide to the narrow.', 'One room, one archetype representative per archetype, one question at a time.', 'The verdict at the top, the numbers under it.'];
                var rows = PA_STEPS.map(function(n, i) {
                    var done = i < active;
                    var back = done && !p.busy && i <= 1 && p.step >= 2;   // back to the start from Plan, Run or Report; never back into a plan once the room runs
                    var cls = 'dtw-pa-ix' + (i === active ? ' on' : '') + (done ? ' done' : '') + (back ? ' back' : '');
                    var tag = back ? 'button type="button" onclick="dtwPaGo(' + (i <= 1 ? 1 : i) + ')"' : 'div';
                    return '<' + tag + ' class="' + cls + '"><span class="n">0' + (i + 1) + '</span><span class="t">' + esc(n) + '</span><span class="d"><span class="w">' + esc(desc[i]) + '</span><span class="lead"></span></span><span class="s">' + paIndexStatus(p, i) + '</span></' + (back ? 'button' : 'div') + '>';
                }).join('');
                return '<div class="dtw-pa-index' + (compact ? ' compact' : '') + '">' + (compact ? '' : '<div class="dtw-pa-ixh"><span class="dtw-cap">What happens next</span><span>five steps' + ((p.picked && p.key) ? ', the first already done' : '') + '</span></div>') + rows + '</div>';
            }
            function paRail() { return paIndex(pa(), true); }
            function paNav(canNext, nextLabel, backTo) {
                var p = pa(), busy = !!p.busy;
                return '<div class="dtw-bw-nav">' + (p.step > 0 && backTo !== false ? '<button type="button" class="dtw-btn"' + (busy ? ' disabled' : '') + ' onclick="dtwPaGo(' + (backTo != null ? backTo : p.step - 1) + ')">Back</button>' : '')
                    + (nextLabel ? '<button type="button" class="dtw-btn dtw-btn-primary dtw-right' + (busy ? ' dtw-busy" aria-busy="true"' : '"') + (canNext && !busy ? '' : ' disabled') + ' onclick="dtwPaNext()">' + (busy ? dtwBusyHtml(nextLabel) : esc(nextLabel) + '<span class="arr"></span>') + '</button>' : '')
                    + '</div>' + (p.err ? '<div class="dtw-bw-err">' + esc(p.err) + '</div>' : '');
            }
            function paHero() { return '<div class="dtw-pa-eyebrow dtw-cap on">Predictive Analytics</div>'; }
            function paEarlier(p) {
                var runs = p.runs;
                if (!p.key) return '';
                if (runs === null) return '<div class="dtw-pa-earlier"><div class="dtw-fg-land-head"><h3>Earlier runs</h3></div><div class="dtw-busy-line">' + dtwBusyHtml('Looking') + '</div></div>';
                if (!(runs || []).length) return '';
                return '<div class="dtw-pa-earlier"><div class="dtw-fg-land-head"><h3>Earlier runs on ' + esc(paTwinName(p.key)) + '</h3><span>' + runs.length + (runs.length === 1 ? ' question answered' : ' questions answered') + '. Open one to read its report.</span></div><div class="dtw-fg-cards">'
                    + runs.map(function(s) {
                        var g = s.guided || {}, o = g.outcome || null, running = g.status === 'running', stopped = g.status === 'stopped';
                        var tag = o ? '<span class="dtw-pa-call ' + paCallClass(o) + '">' + esc(paCallWord(o)) + '</span>' : (running ? '<span class="dtw-fg-tag"><span class="dtw-fg-dot" style="background:var(--dtw-accent)"></span>working</span>' : (stopped ? '<span class="dtw-fg-tag">stopped</span>' : ''));
                        return '<div class="dtw-fg-card dtw-pa-card" role="button" tabindex="0" onclick="dtwPaOpenRun(\'' + esc(s.id) + '\')" onkeydown="if(event.key===\'Enter\'){dtwPaOpenRun(\'' + esc(s.id) + '\')}"><div class="t"><span>' + esc((g.plan || {}).goal || roomTitle(s)) + '</span>' + tag + '</div>'
                            + '<div class="m"><span>' + (g.done != null ? g.done + ' of ' + g.total + ' questions' : '') + '</span>' + SEP + '<span>' + esc(fmtDay(s.updated_at || s.created_at)) + '</span></div></div>';
                    }).join('') + '</div></div>';
            }
            function paCallClass(o) { var c = String((o || {}).call || '').toLowerCase(); return c === 'yes' || c === 'lean yes' ? 'yes' : (c === 'no' || c === 'lean no' ? 'no' : (c === 'split' ? 'split' : 'pick')); }
            function paStart(p) {
                // PA1, as Jenna asked on 2026-11-23: one line the user completes, Ask [Select twin] whether [your question]. Building a
                // twin happens from the dropdown only (its first option); the closed control reads Select twin until the user picks here;
                // once picked it names the audience, not the person (Jennifer Beals followers); no example question is pre-filled.
                var runs = S.runs || [], picked = !!(p.picked && p.key), cur = picked ? (runs.filter(function(r) { return r.key === p.key; })[0] || null) : null;
                var j = PA_JOIN[p.kind] || PA_JOIN[''];
                // the ask form (Jenna 2026-11-23: 'update wheter to say ask until somthing is selected'; her pick: '[Select twin] ask your question'):
                // until BOTH a twin and a question type are picked the line reads [Select twin] ask [your question]; with both it is the full
                // sentence, Ask [Jennifer Beals followers] whether [question]. The dropdown and the slot keep their places; the leading Ask slides in.
                var full = picked && !!p.kind, wasFull = !!p.full; p.full = full;
                var out = '<div class="dtw-pa-sent' + (full ? ' full' : '') + '">';
                out += '<span class="w lead' + (wasFull ? ' in' : '') + '" id="dtwPaLead" aria-hidden="' + (full ? 'false' : 'true') + '"' + (full !== wasFull ? ' data-to="' + (full ? 'in' : 'out') + '"' : '') + '>Ask</span>';
                out += '<span class="dtw-pa-dd inline" id="dtwPaDd"><button type="button" class="dtw-pa-dd-btn" id="dtwPaDdBtn" aria-haspopup="listbox" aria-expanded="false" aria-controls="dtwPaDdList" onclick="dtwPaDdToggle()" onkeydown="dtwPaDdKey(event)">'
                    + '<span class="nm' + (cur ? '' : ' ph') + '">' + esc(cur ? paAudiencePhrase(p.key) : 'Select twin') + '</span><i class="chev" aria-hidden="true">&#9660;</i></button>'
                    + '<div class="dtw-pa-dd-menu" id="dtwPaDdMenu" hidden>'
                    + (runs.length > 7 ? '<input class="dtw-pa-dd-find" id="dtwPaDdFind" type="search" placeholder="Search twins" autocomplete="off" aria-label="Search twins" oninput="dtwPaDdFilter(this.value)" onkeydown="dtwPaDdKey(event)">' : '')
                    + '<div class="dtw-pa-dd-list" id="dtwPaDdList" role="listbox" aria-label="Twins" tabindex="-1" onkeydown="dtwPaDdKey(event)">'
                    + '<div class="dtw-pa-dd-opt build" role="option" id="dtwPaDdOptBuild" data-key="' + PA_BUILD_KEY + '" data-build="1" data-hay="build new twin group" aria-selected="false" onclick="dtwPaDdPick(\'' + PA_BUILD_KEY + '\')" onmousemove="dtwPaDdHover(this)"><span class="k"><i class="plus" aria-hidden="true">+</i></span><span class="nm">Build new twin group</span><span class="n">' + esc(twinPrice()) + '</span></div>'
                    + '<div class="dtw-pa-dd-rule" aria-hidden="true"></div>'
                    + runs.map(function(r, i) { var on = picked && r.key === p.key; return '<div class="dtw-pa-dd-opt' + (on ? ' on' : '') + '" role="option" id="dtwPaDdOpt' + i + '" data-key="' + esc(r.key) + '" data-hay="' + esc(twinOptionLabel(r).toLowerCase()) + '" aria-selected="' + on + '" onclick="dtwPaDdPick(\'' + esc(jsq(r.key)) + '\')" onmousemove="dtwPaDdHover(this)">' + paTwinRow(r) + '</div>'; }).join('')
                    + '</div></div></span>';
                out += '<span class="w" id="dtwPaJoin">' + (full ? esc(j.join) : 'ask') + '</span>';
                out += '<span class="dtw-pa-goalin" id="dtwPaGoal" contenteditable="plaintext-only" role="textbox" aria-multiline="true" aria-label="Your question" data-ph="your question" oninput="dtwPaGoalInput(this.textContent)" onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault(); dtwPaNext();}">' + esc(p.goal || '') + '</span>';
                out += '</div>';
                // the shape of the question, as flat tinted chips
                out += '<div class="dtw-pa-shapes"><span class="lbl">The shape of the question</span>' + PA_KINDS.map(function(k) { return '<button type="button" class="dtw-pa-shape' + (k[0] === p.kind ? ' on' : '') + '" onclick="dtwPaKind(\'' + k[0] + '\')">' + esc(k[1]) + '</button>'; }).join('') + '</div>';
                // Plan it and the price line as one compact block
                var can = picked && !!(p.goal || '').trim();
                out += '<div class="dtw-pa-startline"><button type="button" class="dtw-lb go pa' + (p.busy ? ' busy' : '') + '" id="dtwPaPlanBtn"' + (can && !p.busy ? '' : ' disabled') + ' onclick="dtwPaNext()">' + (p.busy ? 'Planning' : 'Plan it') + '<i>&rarr;</i></button>'
                    + '<span class="price">' + esc(paPriceLine()) + '</span>'
                    + (p.err ? '<span class="dtw-pa-err">' + esc(p.err) + '</span>' : '') + '</div>';
                return out + paIndex(p, false) + paEarlier(p);
            }
            // the typed clause as the room hears it: as written when it is already a question, else with the shape's lead
            function paGoalValue() { var g = el('dtwPaGoal'); if (!g) return pa().goal || ''; return String(g.value != null && g.tagName === 'TEXTAREA' ? g.value : g.textContent || ''); }
            function paGoalText(p) {
                var clause = String(p.goal || '').replace(/\s+/g, ' ').trim();
                if (!clause) return '';
                if (/\?$/.test(clause) || /^(if|will|would|should|what|which|who|how|can|could|does|do|is|are|did|when|where|why)\b/i.test(clause)) return clause.charAt(0).toUpperCase() + clause.slice(1);
                var lead = p.kind ? (PA_JOIN[p.kind] || PA_JOIN['']).lead : '';   // no type picked: as typed (Jenna 2026-11-23)
                return lead ? lead + ' ' + clause.charAt(0).toLowerCase() + clause.slice(1) : clause.charAt(0).toUpperCase() + clause.slice(1);
            }
            // one row of the dropdown: the type, the name with the same-name qualifier, the people
            function paTwinRow(r) {
                return '<span class="k">' + esc(twinKindLabel(r) || 'Twin') + '</span><span class="nm">' + esc(r.name || r.subject || '') + (twinQualifier(r) ? '<span class="built">' + esc(twinQualifier(r)) + '</span>' : '') + '</span><span class="n">' + (r.size_us ? fmtN(r.size_us) + ' people' : '') + '</span>';
            }
            // the dropdown's behaviour: open and close, arrows, Enter, Escape, Home and End, type to jump, a search box once the list is long
            var paDd = { open: false, active: -1, typed: '', typedAt: 0 };
            function paDdOpts() { var l = el('dtwPaDdList'); return l ? Array.prototype.slice.call(l.querySelectorAll('.dtw-pa-dd-opt')).filter(function(o) { return !o.hidden; }) : []; }
            function paDdMark(i) {
                var opts = paDdOpts(); if (!opts.length) { paDd.active = -1; return; }
                paDd.active = Math.max(0, Math.min(opts.length - 1, i));
                opts.forEach(function(o, j) { o.classList.toggle('active', j === paDd.active); });
                var l = el('dtwPaDdList'); if (l) l.setAttribute('aria-activedescendant', opts[paDd.active].id);
                try { opts[paDd.active].scrollIntoView({ block: 'nearest' }); } catch (_) {}
            }
            window.dtwPaDdToggle = function(on) {
                var dd = el('dtwPaDd'), m = el('dtwPaDdMenu'), b = el('dtwPaDdBtn'); if (!dd || !m || !b) return;
                on = (on == null) ? !paDd.open : !!on;
                paDd.open = on; m.hidden = !on; dd.classList.toggle('open', on); b.setAttribute('aria-expanded', on ? 'true' : 'false');
                if (on) {
                    var f = el('dtwPaDdFind'); if (f) { f.value = ''; dtwPaDdFilter(''); }
                    var opts = paDdOpts(), sel = opts.map(function(o) { return o.classList.contains('on'); }).indexOf(true);
                    paDdMark(sel >= 0 ? sel : (opts.length > 1 && opts[0].getAttribute('data-build') ? 1 : 0));   // nothing chosen yet: the first twin, with Build new twin group one step up
                    try { (f || el('dtwPaDdList')).focus(); } catch (_) {}
                } else {
                    paDd.active = -1;
                    try { b.focus(); } catch (_) {}
                }
            };
            window.dtwPaDdPick = function(key) { dtwPaDdToggle(false); if (key === PA_BUILD_KEY) { dtwBuildOpen(true); return; } pa().picked = true; dtwPaPickTwin(key); };
            window.dtwPaDdHover = function(o) { var opts = paDdOpts(), i = opts.indexOf(o); if (i >= 0 && i !== paDd.active) paDdMark(i); };
            window.dtwPaDdFilter = function(q) {
                q = String(q || '').toLowerCase().trim();
                var l = el('dtwPaDdList'); if (!l) return;
                Array.prototype.slice.call(l.querySelectorAll('.dtw-pa-dd-opt')).forEach(function(o) { o.hidden = !!q && !o.getAttribute('data-build') && String(o.getAttribute('data-hay') || '').indexOf(q) < 0; });
                var none = el('dtwPaDdNone');
                if (!paDdOpts().length) { if (!none) { none = document.createElement('div'); none.id = 'dtwPaDdNone'; none.className = 'dtw-pa-dd-none'; none.textContent = 'No twin matches that. The first line builds a new one.'; l.appendChild(none); } }
                else if (none) none.remove();
                paDdMark(q ? 1 : 0);
            };
            window.dtwPaDdKey = function(ev) {
                var k = ev.key, inFind = ev.target && ev.target.id === 'dtwPaDdFind';
                if (!paDd.open) {
                    if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Enter' || k === ' ') { ev.preventDefault(); dtwPaDdToggle(true); }
                    return;
                }
                if (k === 'Escape') { ev.preventDefault(); ev.stopPropagation(); dtwPaDdToggle(false); return; }
                if (k === 'ArrowDown') { ev.preventDefault(); paDdMark(paDd.active + 1); return; }
                if (k === 'ArrowUp') { ev.preventDefault(); paDdMark(paDd.active - 1); return; }
                if (k === 'Home') { ev.preventDefault(); paDdMark(0); return; }
                if (k === 'End') { ev.preventDefault(); paDdMark(paDdOpts().length - 1); return; }
                if (k === 'Enter' || (k === ' ' && !inFind)) { ev.preventDefault(); var opts = paDdOpts(), o = opts[paDd.active]; if (o) dtwPaDdPick(o.getAttribute('data-key')); return; }
                if (k === 'Tab') { dtwPaDdToggle(false); return; }
                if (!inFind && k && k.length === 1 && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
                    // type to jump: the letters typed within a moment find the first twin whose name starts with them
                    var now = Date.now(); paDd.typed = (now - paDd.typedAt < 700 ? paDd.typed : '') + k.toLowerCase(); paDd.typedAt = now;
                    var opts2 = paDdOpts(), hit = -1;
                    for (var i = 0; i < opts2.length; i++) { var nm = ((opts2[i].querySelector('.nm') || {}).textContent || '').toLowerCase(); if (nm.indexOf(paDd.typed) === 0) { hit = i; break; } }
                    if (hit >= 0) paDdMark(hit);
                }
            };
            document.addEventListener('click', function(e) {
                if (!paDd.open) return;
                var dd = el('dtwPaDd'); if (dd && e.target && dd.contains && !dd.contains(e.target)) dtwPaDdToggle(false);
            });
            function paQRow(q, i, editable) {
                var lvl = PA_LEVELS[q.level] || '';
                var opts = (q.options || []).length ? '<div class="dtw-pa-opts">' + q.options.map(function(o) { return '<span class="dtw-fg-chip">' + esc(o) + '</span>'; }).join('') + '</div>' : '';
                var body = editable ? '<input class="dtw-in dtw-pa-qin" value="' + esc(q.q || '') + '" aria-label="Question ' + (i + 1) + '" oninput="dtwPaEditQ(' + i + ', this.value)">' : '<div class="dtw-pa-qtext">' + esc(q.q || '') + '</div>';
                return '<div class="dtw-pa-q"><div class="dtw-pa-qn"><span class="n">' + (i + 1) + '</span><span class="lvl ' + esc(q.level || '') + '">' + esc(lvl) + '</span></div><div class="dtw-pa-qbody">' + body + opts
                    + (q.tests ? '<div class="dtw-pa-tests">' + esc(q.tests) + '</div>' : '') + '</div>'
                    + (editable ? '<button type="button" class="dtw-pa-drop" title="Drop this question" aria-label="Drop this question" onclick="dtwPaDropQ(' + i + ')">&#215;</button>' : '') + '</div>';
            }
            function paStepPlan(p) {
                var plan = p.plan || {}, qs = plan.questions || [];
                var out = '<div class="dtw-bw-q">Here is the plan.</div><div class="dtw-bw-sub">The goal, what would count as yes, and the questions in the order the room will hear them: the big picture first, then closer in, then the question itself. Change any line or drop one.</div>';
                out += '<div class="dtw-bw-group"><div class="k">The goal</div><input class="dtw-in" id="dtwPaPlanGoal" value="' + esc(plan.goal || '') + '" oninput="dtwPaEditPlan(\'goal\', this.value)"></div>';
                out += '<div class="dtw-bw-group"><div class="k">What would count as yes</div><input class="dtw-in" id="dtwPaPlanYes" value="' + esc(plan.success || '') + '" oninput="dtwPaEditPlan(\'success\', this.value)"></div>';
                out += '<div class="dtw-bw-group"><div class="k">The questions <em>' + qs.length + ', from the wide to the narrow</em></div><div class="dtw-pa-qs">' + qs.map(function(q, i) { return paQRow(q, i, true); }).join('') + '</div></div>';
                var quote = paSeatKind() === 'free' ? 'The run is free for Crosswalk seats.' : ((groupCard() && groupQuote(groupTierDefault())) || 'One group at your plan rate, charged when the room opens.');
                out += '<div class="dtw-bw-line">This runs as one room on <b>' + esc(paTwinName(p.key)) + '</b>' + (paPeople(p.key) ? ' (' + fmtN(paPeople(p.key)) + ' people)' : '') + '. ' + esc(quote) + ' Each answer lands as it finishes; the outcome and the report land with the last one.</div>';
                out += '<div class="dtw-bw-shareline"><button type="button" class="dtw-link" onclick="dtwPaReplan()">Plan it again</button> if this is not the shape you had in mind.</div>';
                return out + paNav(qs.length >= 2, 'Run it');
            }
            function paSplitBars(split) {
                if (!(split || []).length) return '';
                return '<div class="dtw-pa-split">' + split.slice(0, 6).map(function(r, i) {
                    var pct = Number(r.pct || 0);
                    return '<div class="dtw-pa-srow"><span class="l">' + esc(r.label || '') + '</span><div class="t"><div class="f" style="width:' + Math.max(1, Math.min(100, pct)) + '%; background:' + (SERIES[i % SERIES.length] || 'var(--dtw-accent)') + '"></div></div><span class="v">' + pct.toFixed(1) + '%' + (r.people ? '<i>' + fmtN(r.people) + ' people</i>' : '') + '</span></div>';
                }).join('') + '</div>';
            }
            function paStepRun(p) {
                var rp = p.report || {}, g = p.guided || {}, qs = rp.questions || (p.plan || {}).questions || [];
                var done = Number(g.done || 0), total = Number(g.total || qs.length || 0), stopped = g.status === 'stopped';
                var out = '<div class="dtw-bw-q">' + (stopped ? 'The room stopped before it finished.' : 'The room is working.') + '</div>';
                out += '<div class="dtw-bw-sub">' + (rp.audience && rp.audience.seats ? '<b>' + rp.audience.seats + ' seats</b> stand for <b>' + fmtN(rp.audience.people) + '</b> people. ' : '') + (stopped ? 'Nothing more was charged. Resume and it picks up at the next question.' : 'Each answer lands as it finishes; the outcome and the report land with the last one. You can leave this page; it keeps running.') + '</div>';
                out += '<div class="dtw-pa-progress"><div class="t"><div class="f" style="width:' + (total ? Math.round(done / total * 100) : 0) + '%"></div></div><span>' + done + ' of ' + total + ' answered</span></div>';
                out += '<div class="dtw-pa-qs">' + qs.map(function(q, i) {
                    var st = q.answered ? 'done' : (q.failed ? 'failed' : (i === done ? 'now' : 'wait'));
                    var lead = q.lead ? '<div class="dtw-pa-lead">' + esc(q.lead.label) + ' <b>' + Number(q.lead.pct || 0).toFixed(1) + '%</b>' + (q.lead.people ? ', ' + fmtN(q.lead.people) + ' people' : '') + '</div>' : '';
                    var now = st === 'now' && !stopped ? '<div class="dtw-fg-working"><span class="dtw-fg-pulse"></span><span>The room is on this one.</span></div>' : '';
                    return '<div class="dtw-pa-q ' + st + '"><div class="dtw-pa-qn"><span class="n">' + (st === 'done' ? '&#10003;' : (i + 1)) + '</span><span class="lvl ' + esc(q.level || '') + '">' + esc(PA_LEVELS[q.level] || q.level_word || '') + '</span></div><div class="dtw-pa-qbody"><div class="dtw-pa-qtext">' + esc(q.q || '') + '</div>' + lead + now + (q.read && st === 'done' ? '<div class="dtw-pa-read">' + esc(q.read) + '</div>' : '') + '</div></div>';
                }).join('') + '</div>';
                out += '<div class="dtw-bw-nav">' + (stopped ? '<button type="button" class="dtw-btn dtw-btn-primary' + (p.busy ? ' dtw-busy" aria-busy="true"' : '"') + ' onclick="dtwPaResume()">' + (p.busy ? dtwBusyHtml('Resuming') : 'Resume') + '</button>' : '')
                    + '<button type="button" class="dtw-btn dtw-right" onclick="dtwPaWatchRoom()">Watch the room</button></div>' + (p.err ? '<div class="dtw-bw-err">' + esc(p.err) + '</div>' : '');
                return out;
            }
            function paFace(seat, i) {
                var url = seat.portrait_url ? thumb(seat.portrait_url) : '';
                return '<div class="dtw-pa-seat"><span class="dtw-pa-face" style="--hue:' + hueFor(i) + '">' + (url ? '<img src="' + esc(url) + '" alt="" loading="lazy">' : esc(initials(seat.name))) + '</span><b>' + esc(titleCase(seat.name || '')) + '</b><span>' + esc([seat.age, titleCase(seat.city || '')].filter(Boolean).join(', ')) + '</span>' + (seat.kind ? '<em>' + esc(seat.kind) + '</em>' : '') + (seat.people ? '<i>' + fmtN(seat.people) + ' people</i>' : '') + '</div>';
            }
            function paReport(p) {
                var rp = p.report || {}, o = rp.outcome || null, aud = rp.audience || {};
                var out = '<div class="dtw-pa-report">';
                out += '<div class="dtw-eyebrow">Predictive Analytics ' + SEP + ' ' + esc(aud.name || paTwinName(p.key)) + (aud.people ? ' ' + SEP + ' ' + fmtN(aud.people) + ' people' : '') + (rp.finished_at ? ' ' + SEP + ' ' + esc(fmtDay(rp.finished_at)) : '') + '</div>';
                out += '<h2 class="dtw-pa-h">' + esc(rp.goal || rp.title || '') + '</h2>';
                if (rp.asked && rp.asked !== rp.goal) out += '<div class="dtw-pa-asked">You asked: ' + esc(rp.asked) + '</div>';
                // the exports and the room, above the card (Jenna 2026-11-18): download the report, the raw data, ask the room, start another
                out += '<div class="dtw-actions dtw-pa-acts top"><a class="dtw-btn dtw-btn-primary" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(p.key) + '/guided/' + esc(p.sid) + '/report.pdf">Download the report (PDF)</a>'
                    + '<a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(p.key) + '/panel/' + esc(p.sid) + '/export.csv">Raw data (CSV)</a>'
                    + '<button type="button" class="dtw-btn" onclick="dtwPaWatchRoom()">Ask the room</button>'
                    + '<button type="button" class="dtw-btn dtw-right" onclick="dtwPaAnother()">Ask another question</button></div>';
                if (o) {
                    out += '<div class="dtw-pa-outcome"><div class="dtw-pa-callbox ' + paCallClass(o) + '"><span class="k">The call</span><b>' + esc(paCallWord(o)) + '</b></div><div class="dtw-pa-outbody"><p class="lead">' + esc(o.answer || '') + '</p>' + (o.why ? '<p>' + esc(o.why) + '</p>' : '')
                        + ((o.numbers || []).length ? '<ul class="dtw-pa-nums">' + o.numbers.map(function(n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>' : '')
                        + (o.who ? '<div class="dtw-pa-kv"><span class="k">Who drove it</span><span>' + esc(o.who) + '</span></div>' : '')
                        + (o.would_change ? '<div class="dtw-pa-kv"><span class="k">What would change it</span><span>' + esc(o.would_change) + '</span></div>' : '')
                        + (o.next ? '<div class="dtw-pa-kv"><span class="k">Worth asking next</span><span>' + esc(o.next) + '</span></div>' : '') + '</div></div>';
                    if (rp.success) out += '<div class="dtw-bw-shareline">What would have counted as yes: ' + esc(rp.success) + '</div>';
                }
                out += '<div class="dtw-pa-sect"><div class="dtw-fg-land-head"><h3>What the room was asked</h3><span>' + (rp.questions || []).length + ' questions, in the order they ran: the big picture first, then closer in, then the question itself.</span></div>';
                out += (rp.questions || []).map(function(q, i) {
                    return '<div class="dtw-pa-rq' + (q.settles ? ' settles' : '') + '"><div class="dtw-pa-qn"><span class="n">' + (i + 1) + '</span><span class="lvl ' + esc(q.level || '') + '">' + esc(q.level_word || PA_LEVELS[q.level] || '') + (q.settles ? ' (settles it)' : '') + '</span></div>'
                        + '<div class="dtw-pa-qbody"><div class="dtw-pa-qtext">' + esc(q.q || '') + '</div>' + paSplitBars(q.split) + (q.read ? '<div class="dtw-pa-read">' + esc(q.read) + '</div>' : (q.failed ? '<div class="dtw-pa-read">The room did not finish this one.</div>' : ''))
                        + ((q.voices || []).length ? '<div class="dtw-pa-voices">' + q.voices.map(function(v) { return '<div class="dtw-pa-voice"><b>' + esc(titleCase(v.name || '')) + (v.age ? ', ' + esc(v.age) : '') + (v.city ? ', ' + esc(titleCase(v.city)) : '') + '</b> ' + esc(v.line || '') + '</div>'; }).join('') + '</div>' : '') + '</div></div>';
                }).join('') + '</div>';
                if ((rp.seats || []).length) out += '<div class="dtw-pa-sect"><div class="dtw-fg-land-head"><h3>Who sat in the room</h3><span>One archetype representative for each archetype in the audience, each standing for the people of that archetype.</span></div><div class="dtw-pa-seats">' + rp.seats.map(paFace).join('') + '</div></div>';
                return out + '</div>';
            }
            function renderPredict() {
                var h = el('dtwPaConsole'); if (!h) return;
                var p = pa();
                // the wizard's audience is the page's open twin (Jenna 2026-11-18: the header must say the twin you picked); a twin
                // switched anywhere else brings the wizard back to the audience step on that twin
                if (S.key && p.key && p.key !== S.key) { paClearPoll(); S.pa = paFresh(); p = S.pa; }
                if (!p.key && S.key) p.key = S.key;
                if (p.step === 0 && p.key && p.runsKey !== p.key) paLoadRuns(p.key);
                var out = paHero();
                if (p.step <= 1) out += paStart(p);
                else if (p.step === 2) out += paRail() + paStepPlan(p);
                else if (p.step === 3) out += paRail() + paStepRun(p);
                else out += paRail() + paReport(p);
                h.innerHTML = '<div class="dtw-pa-wrap' + (p.step <= 1 ? ' start' : '') + '">' + out + '</div>';
                var lead = el('dtwPaLead'); if (lead && lead.getAttribute('data-to')) { var to = lead.getAttribute('data-to'); (window.requestAnimationFrame || function(f) { setTimeout(f, 16); })(function() { lead.classList.toggle('in', to === 'in'); }); }
                if (p.step <= 1 && p.picked && p.key && !p.goal && !paDd.open) { var ge = el('dtwPaGoal'); if (ge) { try { ge.focus(); } catch (_) {} } }
                if (p.step === 3 && p.guided && p.guided.status === 'running') paSchedulePoll();
            }
            function paLoadRuns(key) {
                var p = pa(); p.runs = null; p.runsKey = key;
                if (S.key === key && S.twin) {    // the open twin is already here; no second fetch
                    var have = (S.twin.panel_sessions || []).filter(function(s) { return s && s.guided; });
                    have.sort(function(a, b) { return String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')); });
                    p.runs = have; return;
                }
                api('/' + key).then(function(d) {
                    if (pa().runsKey !== key) return;
                    var ss = ((d.twin || {}).panel_sessions || []).filter(function(s) { return s && s.guided; });
                    ss.sort(function(a, b) { return String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || '')); });
                    pa().runs = ss;
                    if (S.tab === 'predict' && pa().step === 0) renderPredict();
                }).catch(function() { if (pa().runsKey === key) { pa().runs = []; if (S.tab === 'predict' && pa().step === 0) renderPredict(); } });
            }
            function paSchedulePoll() {
                var p = pa(); paClearPoll();
                var sid = p.sid, key = p.key;
                p.pollT = setTimeout(async function() {
                    var q = pa(); q.pollT = null;
                    if (q.sid !== sid || q.step !== 3) return;
                    try {
                        var d = await api('/' + key + '/guided/' + sid + '/report');
                        if (pa().sid !== sid) return;
                        pa().report = d.report || pa().report; pa().guided = d.guided || pa().guided;
                        var g = pa().guided || {};
                        if (g.status === 'done') { pa().step = 4; pa().err = ''; if (S.tab === 'predict') renderPredict(); fgToast('The report is ready.'); return; }
                        if (S.tab === 'predict' && pa().step === 3) renderPredict();
                        if (g.status === 'running') paSchedulePoll();
                    } catch (_) { if (pa().sid === sid && pa().step === 3) paSchedulePoll(); }
                }, 4000);
            }
            window.dtwPaGo = function(i) { var p = pa(); if (p.busy) return; if (p.step >= 3 && i >= 2) return; p.step = Math.max(0, Math.min(PA_STEPS.length - 1, i)); p.err = ''; paClearPoll(); renderPredict(); };
            window.dtwPaPickTwin = async function(key) {
                var p = pa(); p.key = key; p.err = ''; p.runs = null; p.runsKey = '';
                // the page opens the twin, so the header and every tab follow; dtwOpen lands back here and redraws the step
                if (S.key !== key) { try { await dtwOpen(key, null, null, null, 'predict'); } catch (_) {} if (pa().key !== key) { pa().key = key; } }
                renderPredict();
            };
            window.dtwPaKind = function(k) { var p = pa(); p.goal = paGoalValue(); p.kind = p.kind === k ? '' : k; renderPredict(); };
            window.dtwPaGoalInput = function(v) { var p = pa(); p.goal = String(v || ''); var b = el('dtwPaPlanBtn'); if (b) b.disabled = !(p.picked && p.key && (p.goal || '').trim()); };
            window.dtwPaEditPlan = function(k, v) { var p = pa(); if (p.plan) p.plan[k] = v; };
            window.dtwPaEditQ = function(i, v) { var p = pa(); if (p.plan && p.plan.questions && p.plan.questions[i]) p.plan.questions[i].q = v; };
            window.dtwPaDropQ = function(i) {
                var p = pa(); if (!p.plan || !p.plan.questions || p.plan.questions.length <= 2) return;
                var fin = Number(p.plan.final); p.plan.questions.splice(i, 1);
                if (isFinite(fin)) { if (fin === i) p.plan.final = p.plan.questions.length - 1; else if (fin > i) p.plan.final = fin - 1; }
                renderPredict();
            };
            window.dtwPaReplan = function() { var p = pa(); p.plan = null; p.step = 1; p.err = ''; renderPredict(); };
            window.dtwPaNext = async function() {
                var p = pa(); if (p.busy) return;
                if (p.step === 0) { if (!p.key || !p.picked) return; p.step = 1; }   // the start covers both: fall through to planning
                if (p.step === 1) {
                    p.goal = paGoalValue();
                    var goal = paGoalText(p); if (!goal) return;
                    p.busy = true; p.err = ''; renderPredict();
                    try { var d = await api('/' + p.key + '/guided/plan', { method: 'POST', body: JSON.stringify({ goal: goal, kind: p.kind || '' }) }); p.plan = d.plan; p.step = 2; }
                    catch (e) { p.err = dtwSafeErr(e, 'The plan did not come back. Try again in a moment.'); }
                    p.busy = false; renderPredict(); return;
                }
                if (p.step === 2) {
                    if (!p.plan) return;
                    var qs = (p.plan.questions || []).map(function(q) { return Object.assign({}, q, { q: String(q.q || '').replace(/\s+/g, ' ').trim() }); }).filter(function(q) { return q.q; });
                    if (qs.length < 2) { p.err = 'Keep at least two questions.'; renderPredict(); return; }
                    var plan = Object.assign({}, p.plan, { questions: qs, goal: String(p.plan.goal || '').trim(), success: String(p.plan.success || '').trim() });
                    p.busy = true; p.err = ''; renderPredict();
                    try {
                        var r = await api('/' + p.key + '/guided/run', { method: 'POST', body: JSON.stringify({ plan: plan }) });
                        p.sid = r.session_id; p.job = r.job_id; p.guided = r.guided || null; p.report = null; p.step = 3; p.runsKey = '';
                        try { var rd = await api('/' + p.key + '/guided/' + p.sid + '/report'); p.report = rd.report || null; p.guided = rd.guided || p.guided; } catch (_) {}
                        if (S.key === p.key && S.twin && r.session) { S.twin.panel_sessions = S.twin.panel_sessions || []; if (!S.twin.panel_sessions.some(function(s) { return s.id === r.session.id; })) S.twin.panel_sessions.unshift(r.session); try { renderPanelSessions(); } catch (_) {} }
                    } catch (e) {
                        p.err = dtwSafeErr(e, 'The room did not open. Nothing was charged; try again in a moment.');
                        if (e && e.doc && e.doc.add_funds_url) p.err += ' Add funds at ' + e.doc.add_funds_url + '.';
                    }
                    p.busy = false; renderPredict(); return;
                }
            };
            window.dtwPaOpenRun = async function(sid) {
                var p = pa(); if (!p.key && S.key) p.key = S.key;
                var key = p.key; p.sid = sid; p.err = ''; p.busy = false; paClearPoll();
                if (S.tab !== 'predict') dtwTab('predict');
                try {
                    var d = await api('/' + key + '/guided/' + sid + '/report');
                    p.report = d.report || null; p.guided = d.guided || null; p.plan = p.guided ? { goal: p.guided.goal, success: p.guided.success, questions: p.guided.questions, final: p.guided.final, kind: p.guided.kind } : p.plan;
                    p.step = (p.guided && p.guided.status === 'done') ? 4 : 3;
                } catch (e) { p.err = dtwSafeErr(e, 'That run could not be opened.'); p.step = 0; }
                renderPredict();
            };
            window.dtwPaResume = async function() {
                var p = pa(); if (p.busy || !p.sid) return;
                p.busy = true; p.err = ''; renderPredict();
                try { var d = await api('/' + p.key + '/guided/' + p.sid + '/resume', { method: 'POST', body: '{}' }); p.guided = d.guided || p.guided; }
                catch (e) { p.err = dtwSafeErr(e, 'It did not resume. Try again in a moment.'); }
                p.busy = false; renderPredict();
            };
            window.dtwPaWatchRoom = async function() { var p = pa(); if (!p.key || !p.sid) return; paClearPoll(); try { await dtwOpen(p.key, null, p.sid, null, 'panel'); } catch (_) {} };
            window.dtwPaAnother = function() { var p = pa(), key = p.key; S.pa = paFresh(); S.pa.key = key; S.pa.picked = !!key; S.pa.step = 1; renderPredict(); };
            function drawer(id, scrimId, on, onOpen) {
                var d = el(id), sc = el(scrimId); if (!d) return;
                d.classList.toggle('open', !!on); if (sc) sc.classList.toggle('open', !!on); d.setAttribute('aria-hidden', on ? 'false' : 'true');
                if (on) { d.scrollTop = 0; if (onOpen) onOpen(); }
            }
            window.dtwRoomsOpen = function(on) { if (on) dtwNewRoomOpen(false); drawer('dtwRoomsDrawer', 'dtwRoomsScrim', on, function() { renderPanelSessions(); }); };
            window.dtwNewRoomOpen = function(on) {
                if (on) dtwRoomsOpen(false); else S.roomSeed = null;
                drawer('dtwNewRoomDrawer', 'dtwNewRoomScrim', on, function() { syncInputCards(); renderPrices(); var st = el('dtwPnStatus'); if (st) st.textContent = S.key ? '' : 'Pick a twin on the Twins tab first.'; try { dtwPnSeatsNote(); } catch (_) {} var t = el('dtwPnTitle'); if (t) { t.value = ''; setTimeout(function() { try { t.focus(); } catch (_) {} }, 120); } });
            };
            // ---- the tutorial pop-up (Jenna 2026-11-23: a Tutorial link beside Start on the Build a new twin card opens the seating
            // video). One overlay on the body, built on first use: a Graphite scrim, the video at 16:9 with native controls and its poster,
            // playing on open (the click is the gesture). Close, Escape and a click on the scrim close it; closing pauses and rewinds;
            // focus moves to the close button and back to the link. The video and poster come through the app's tutorial route.
            var TUTORIALS = { seating: { title: 'How to seat your focus group' } };   // panel is a reader-banned word; the Focus Group IQ tab's wording
            var tutFrom = null;
            function tutorialEl() {
                var d = el('dtwTutorial');
                if (d) return d;
                d = document.createElement('div'); d.id = 'dtwTutorial'; d.className = 'dtw-tutorial'; d.setAttribute('aria-hidden', 'true');
                d.innerHTML = '<div class="dtw-tut-scrim" data-tut-scrim="1"></div>'
                    + '<div class="dtw-tut-dialog" role="dialog" aria-modal="true" aria-labelledby="dtwTutorialTitle">'
                    + '<div class="dtw-tut-head"><span class="dtw-tut-eyebrow">Tutorial</span><span class="dtw-tut-title" id="dtwTutorialTitle"></span>'
                    + '<button type="button" class="dtw-tut-close" id="dtwTutorialClose" aria-label="Close the tutorial" onclick="dtwTutorialClose()">&#215;</button></div>'
                    + '<div class="dtw-tut-frame"><video id="dtwTutorialVideo" controls playsinline preload="metadata"></video></div></div>';
                d.addEventListener('click', function(ev) { var t = ev.target; if (t && t.getAttribute && t.getAttribute('data-tut-scrim')) dtwTutorialClose(); });
                document.body.appendChild(d);
                return d;
            }
            window.dtwTutorialOpen = function(name, from) {
                name = String(name || 'seating'); var rec = TUTORIALS[name] || TUTORIALS.seating;
                var d = tutorialEl(), v = el('dtwTutorialVideo'), t = el('dtwTutorialTitle');
                tutFrom = from || (document.activeElement && document.activeElement !== document.body ? document.activeElement : null);
                if (t) t.textContent = rec.title || '';
                if (v) {
                    var src = '/api/digital-twins/tutorial/' + encodeURIComponent(name) + '.mp4';
                    if (v.getAttribute('src') !== src) { v.setAttribute('poster', '/api/digital-twins/tutorial/' + encodeURIComponent(name) + '.jpg'); v.setAttribute('src', src); try { v.load(); } catch (_) {} }
                }
                d.classList.add('open'); d.setAttribute('aria-hidden', 'false');
                try { document.body.classList.add('dtw-tut-open'); } catch (_) {}
                if (v) { try { var pr = v.play(); if (pr && pr.catch) pr.catch(function() {}); } catch (_) {} }
                var c = el('dtwTutorialClose'); if (c) { try { c.focus(); } catch (_) {} }
            };
            window.dtwTutorialClose = function() {
                var d = el('dtwTutorial'); if (!d || !d.classList.contains('open')) return;
                var v = el('dtwTutorialVideo');
                if (v) { try { v.pause(); } catch (_) {} try { v.currentTime = 0; } catch (_) {} }
                d.classList.remove('open'); d.setAttribute('aria-hidden', 'true');
                try { document.body.classList.remove('dtw-tut-open'); } catch (_) {}
                var back = tutFrom; tutFrom = null;
                if (back && back.focus) { try { back.focus(); } catch (_) {} }
            };
            document.addEventListener('keydown', function(e) {
                if (e.key !== 'Escape') return;
                var tut = el('dtwTutorial'); if (tut && tut.classList.contains('open')) { dtwTutorialClose(); return; }
                var p = el('dtwSeatPop'); if (p && p.classList.contains('open')) { dtwSeatOpen(null); return; }
                var bd = el('dtwBuildDrawer'); if (bd && bd.classList.contains('open')) { dtwBuildOpen(false); return; }
                var td = el('dtwRecordDrawer'); if (td && td.classList.contains('open')) { dtwRecordOpen(false); return; }
                var nd = el('dtwNewRoomDrawer'); if (nd && nd.classList.contains('open')) { dtwNewRoomOpen(false); return; }
                var rd = el('dtwRoomsDrawer'); if (rd && rd.classList.contains('open')) { dtwRoomsOpen(false); return; }
                // with no popover or drawer open, Escape closes the room (Jenna 2026-10-27); a first Escape inside the composer only leaves the field
                var hd = el('dtwHowtoDrawer'); if (hd && hd.classList.contains('open')) return;
                if (S.tab !== 'panel' || !S.session || e.defaultPrevented) return;
                var v = el('digitalTwinsIQView'); if (!v || getComputedStyle(v).display === 'none') return;
                var pw = el('prometheusWidget'); if (pw && pw.classList.contains('pm-on')) return;
                var t = e.target;
                if (t instanceof Element && t !== document.body && t !== document.documentElement) {
                    if (!t.closest('#digitalTwinsIQView')) return;
                    if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) { try { t.blur(); } catch (_) {} return; }
                }
                dtwPanelClose();
            });
            // Close room (Jenna 2026-10-27: 'you should have a way to exit/close a room'): nothing new is saved (the room autosaves), the console
            // returns to the no-room state, the remembered view drops the room, and the room stays under Rooms to reopen. A question still in
            // flight keeps running on the server; the room reads 'working' under Rooms until it lands.
            window.dtwPanelClose = function() {
                var s = S.session; if (!s) return;
                var working = (s.questions || []).some(qInFlight);
                clearRoomPoll(); clearPortraitTimers();
                try { dtwSeatOpen(null); } catch (_) {}
                S.session = null; S.panelKind = ''; fgReset(null);
                renderPanelConsole(); renderPanelSessions();
                try { remember(); } catch (_) {}
                try { rpTick(); } catch (_) {}
                fgToast(working ? 'Room closed. The question keeps running; find it under Rooms.' : 'Room closed. Find it under Rooms.');
            };
            function fgToast(text, action) {
                var t = el('dtwFgToast');
                if (!t) { t = document.createElement('div'); t.id = 'dtwFgToast'; t.className = 'dtw-fg-toast'; t.setAttribute('role', 'status'); t.setAttribute('aria-live', 'polite'); (el('digitalTwinsIQView') || document.body).appendChild(t); }
                t.textContent = text || ''; clearTimeout(S.fgToastT);
                if (action && action.label && typeof action.onclick === 'function') {
                    // an action on the toast (Undo after a delete, 2026-11-03); it stays up longer so it can be reached
                    var b = document.createElement('button'); b.type = 'button'; b.className = 'dtw-fg-toastact'; b.textContent = action.label;
                    b.onclick = function() { t.classList.remove('show'); try { action.onclick(); } catch (_) {} };
                    t.appendChild(b);
                }
                t.classList.add('show');
                S.fgToastT = setTimeout(function() { t.classList.remove('show'); }, action ? 9000 : 5200);
            }
            document.addEventListener('click', function(e) {
                var p = el('dtwSeatPop'); if (!p || !p.classList.contains('open')) return;
                var t = e.target; if (!(t instanceof Element)) return;
                if (t.closest('#dtwSeatPop') || t.closest('[data-fg-seat]')) return;
                dtwSeatOpen(null);
            });
            // The library: every saved room on the twin, the open one lit, a working tag on a room with a question in flight, delete with its confirm.
            window.dtwPanelPickTwin = function() { renderPanelSessions(); };
            function renderPanelSessions() {
                var h = el('dtwPnSessions');
                var ss = (S.twin || {}).panel_sessions || [];
                var nb = el('dtwFgRoomsN'); if (nb) nb.textContent = ss.length;
                if (!h) return;
                if (!S.twin) { h.innerHTML = '<div class="dtw-fg-note">Pick a twin on the Twins tab to see its rooms.</div>'; return; }
                var pick = (S.runs || []).length > 1 ? '<div class="dtw-fg-twinrow"><span>Rooms on</span><select class="dtw-in dtw-fg-twinsel" aria-label="Rooms on which twin" onchange="dtwRoomsSwitchTwin(this.value)">' + S.runs.map(function(r) { return '<option value="' + esc(r.key) + '"' + (r.key === S.key ? ' selected' : '') + '>' + esc(twinOptionLabel(r)) + (r.key === S.key ? ' (' + ss.length + ')' : (r.panel_sessions ? ' (' + r.panel_sessions + ')' : '')) + '</option>'; }).join('') + '</select></div>' : '';
                if (!ss.length) { h.innerHTML = pick + '<div class="dtw-fg-note">No rooms on ' + esc(twinName()) + ' yet. Open the first one.</div><div class="dtw-fg-note" id="dtwFgNote"></div>'; return; }
                var live = ss.filter(function(s) { return (s.questions || []).length; }), empty = ss.filter(function(s) { return !(s.questions || []).length; });
                var row = function(s) {
                    var n = (s.questions || []).length, sid = esc(s.id), on = S.session && S.session.id === s.id, working = (s.questions || []).some(qInFlight);
                    return '<div class="dtw-run dtw-roomrow' + (on ? ' on' : '') + '" role="button" tabindex="0" onclick="dtwPanelResume(\'' + sid + '\')" onkeydown="if(event.key===\'Enter\'){dtwPanelResume(\'' + sid + '\');}"><div style="display:flex; gap:0.5rem; align-items:flex-start;"><div style="min-width:0; flex:1 1 auto;"><div class="q"><span>' + esc(roomTitle(s)) + '</span>' + (on ? '<span class="dtw-fg-tag">open</span>' : '') + (working ? '<span class="dtw-fg-tag"><span class="dtw-fg-dot" style="background:var(--dtw-accent)"></span>working</span>' : '') + '</div><div class="m">' + fmtN((s.group || {}).in_room || 0) + ' twins &middot; ' + n + (n === 1 ? ' question' : ' questions') + ' &middot; ' + esc(fmtDay(s.updated_at || s.created_at)) + '</div></div><button type="button" class="dtw-roomdup" title="Duplicate this room" aria-label="Duplicate this room" onclick="event.stopPropagation(); dtwPanelDuplicate(\'' + sid + '\')">Duplicate</button><button type="button" class="dtw-x" title="Delete this room" aria-label="Delete this room" onclick="event.stopPropagation(); dtwPanelDeleteAsk(\'' + sid + '\')">&#215;</button></div><div class="dtw-confirm" id="dtwDel-' + sid + '" style="display:none;" onclick="event.stopPropagation()"><span>Delete this room? Its questions and answers go with it.</span><button type="button" class="dtw-btn dtw-btn-sm dtw-btn-primary" onclick="event.stopPropagation(); dtwPanelDelete(\'' + sid + '\')">Delete</button><button type="button" class="dtw-btn dtw-btn-sm" onclick="event.stopPropagation(); dtwPanelDeleteAsk(\'' + sid + '\', false)">Keep</button></div></div>';
                };
                var out = pick;
                if (live.length) out += '<div class="dtw-fg-group">' + esc(twinName()) + '</div>' + live.map(row).join('');
                if (empty.length) out += '<div class="dtw-fg-group quiet">Empty rooms</div>' + empty.map(row).join('');
                out += '<div class="dtw-fg-note" id="dtwFgNote"></div>';
                h.innerHTML = out;
            }
            function fgNote(text) { var n = el('dtwFgNote'); if (n) n.textContent = text || ''; }
            window.dtwRoomsSwitchTwin = async function(key) { if (!key || key === S.key) return; await dtwOpen(key, null, null, null, 'panel'); dtwRoomsOpen(true); };
            window.dtwPanelDeleteAsk = function(sid, on, prefix) {
                var c = el((prefix || 'dtwDel-') + sid); if (!c) return;
                var show = (on == null) ? c.style.display === 'none' : !!on;
                document.querySelectorAll('#dtwPnSessions .dtw-confirm, #dtwPnConsole .dtw-confirm').forEach(function(x) { x.style.display = 'none'; });
                c.style.display = show ? 'flex' : 'none';
            };
            window.dtwPanelDelete = async function(sid) {
                if (!S.key || !S.twin) return;
                var cs = [el('dtwDel-' + sid), el('dtwDelC-' + sid)].filter(Boolean), c = cs[0] || null;
                cs.forEach(function(x) { x.innerHTML = '<span>Deleting...</span>'; });
                var d = null;
                try {
                    try { d = await api('/' + S.key + '/panel/' + sid, { method: 'DELETE' }); }
                    catch (e1) { if (/\((404|405)\)$/.test(e1.message)) d = await api('/' + S.key + '/panel/' + sid + '/delete', { method: 'POST', body: '{}' }); else throw e1; }
                } catch (e) {
                    cs.forEach(function(x) { x.innerHTML = '<span>' + esc(e.message) + '</span><button type="button" class="' + (x.id.indexOf('dtwDelC-') === 0 ? 'dtw-fg-act' : 'dtw-btn dtw-btn-sm') + '" onclick="event.stopPropagation(); this.parentNode.style.display=\'none\'">Keep</button>'; });
                    return;
                }
                S.twin.panel_sessions = (S.twin.panel_sessions || []).filter(function(x) { return x.id !== sid; });
                if (S.session && S.session.id === sid) { S.session = null; S.panelKind = ''; clearPortraitTimers(); dtwSeatOpen(null); try { remember(); } catch (_) {} }
                if (!S.session) renderPanelConsole();
                var r = S.runs.filter(function(x) { return x.key === S.key; })[0]; if (r) r.panel_sessions = S.twin.panel_sessions.length;
                renderSpine(); renderLibrary(); renderPanelSessions();
                fgNote((d && d.message) || 'Room deleted.');
                if (!S.session) fgToast((d && d.message) || 'Room deleted.');
                var nb = el('dtwFgRoomsN'); if (nb) nb.textContent = S.twin.panel_sessions.length;
                try { dtwLoadList(true); } catch (_) {}
            };
            // Duplicate a room (Jenna, 2026-11-04: "duplicate a room with all of the input if you want to so if you just want to change
            // one thing you can"): a new room with the same twins, cohorts and seats, named '<room> copy', no questions yet and nothing
            // paid (the session price waits for the first question, as on any new room). It opens at the ask screen, where the name
            // can change; the source room stays as it was.
            window.dtwPanelDuplicate = async function(sid) {
                if (!S.key || !sid) return;
                var src = ((S.twin || {}).panel_sessions || []).filter(function(s) { return s.id === sid; })[0] || null;
                var tok = dtwBusy(dtwBtn(), 'Duplicating', el('dtwFgNote')); if (tok === false) return;
                dtwStage(tok, 'Duplicating the room');
                try { var d = await api('/' + S.key + '/panel/' + encodeURIComponent(sid) + '/duplicate', { method: 'POST', body: '{}' }); }
                catch (e) { dtwIdle(tok); fgToast(e && e.message ? e.message : 'The room could not be duplicated.'); return; }
                dtwStage(tok, 'Opening the room');
                dtwRoomsOpen(false);
                try { await dtwOpen(S.key, null, d.session.id, null, 'panel'); } finally { dtwIdle(tok); }
                fgToast('Duplicated ' + ((src && roomTitle(src)) || 'the room') + ' as ' + roomTitle(d.session) + '. Same twins, cohorts and seats, no questions yet. Rename it here if you like.');
            };
            window.dtwPanelResume = function(sid) {
                S.session = ((S.twin || {}).panel_sessions || []).filter(function(s) { return s.id === sid; })[0] || null;
                S.panelKind = ''; fgReset(S.session ? S.session.id : null); S.fg.seat = null;
                dtwRoomsOpen(false); renderPanelSessions(); renderPanelConsole();
                try { remember(); } catch (_) {}
            };
            window.dtwPanelOpen = async function() {
                var key = S.key, st = el('dtwPnStatus');
                if (!key) { if (st) st.textContent = 'Pick a twin on the Twins tab first.'; return; }
                var title = ((el('dtwPnTitle') || {}).value || '').replace(/\s+/g, ' ').trim();
                if (!title) { if (st) st.textContent = 'Name the room first.'; var tn = el('dtwPnTitle'); if (tn) { try { tn.focus(); } catch (_) {} } return; }
                var segs = ((el('dtwPnSegments') || {}).value || '').split('\n').map(function(s) { return s.trim(); }).filter(Boolean).map(function(s) { return { label: s }; });
                var other = (el('dtwPnTwin2') || {}).value;
                var body = { segments: segs.length ? segs : null, in_room: 0, title: title, twins: other && other !== key ? [other] : [], tier: parseInt((el('dtwPnTier') || {}).value || groupTierDefault(), 10) || 20 };
                var tok = dtwBusy(dtwBtn('[onclick^="dtwPanelOpen("]'), 'Opening the room', st); if (tok === false) return;
                dtwStage(tok, 'Seating the room');
                try { var d = await api('/' + key + '/panel/open', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { dtwIdle(tok, e); if (st && e.doc && e.doc.add_funds_url) st.innerHTML = esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>'; return; }
                dtwStage(tok, 'Opening the room');
                var seed = libRoomSeedTake(key);
                dtwNewRoomOpen(false);
                try { await dtwOpen(key, null, d.session.id, null, 'panel'); } catch (e) { dtwIdle(tok, e); return; }
                dtwIdle(tok);
                if (seed) { var sq = el('dtwPnQ'); if (sq) { sq.value = seed.q; try { sq.focus(); } catch (_) {} } }
                if (st) st.textContent = d.reused ? 'That room was already open; here it is.' : 'The room is open.';
            };
            window.dtwPanelKind = function(k) { S.panelKind = (S.panelKind === k) ? '' : k; document.querySelectorAll('#dtwPnKinds .dtw-fg-chip').forEach(function(b) { b.classList.toggle('on', b.getAttribute('data-k') === S.panelKind); }); var o = el('dtwPnOptions'); if (o) o.style.display = (S.panelKind === 'multiple_choice' || S.panelKind === 'ab') ? 'block' : 'none'; var q = el('dtwPnQ'); if (q) { try { q.focus(); } catch (_) {} } };
            window.dtwPanelAsk = async function(presetQ, askAgain) {
                if (!S.session) return;
                var inp = el('dtwPnQ'); var q = presetQ || (inp && inp.value.trim());
                if (!q) { if (inp) { try { inp.focus(); } catch (_) {} } return; }
                if (!presetQ && /^https?:\/\/\S+$/i.test(q)) { dtwPanelAssetUrl(q); return; }   // a link on its own is content for the room
                var inflight = (S.session.questions || []).filter(qInFlight);
                if (inflight.length) {
                    var same = inflight.some(function(x) { return normQ(x.q) === normQ(q); });
                    liveNote(same ? 'That question is already in the room; the answer is on its way.' : 'The room is still working on the last question. Ask the next one when it lands.');
                    return;
                }
                if (S.asking) return;
                S.asking = true; setAskEnabled(false);
                var body = { q: q };
                if (S.panelKind) body.kind = S.panelKind;
                if (askAgain) body.ask_again = true;
                var optsRaw = (el('dtwPnOptions') || {}).value || '';
                if (optsRaw.trim()) body.options = optsRaw.split(/\n|,/).map(function(s) { return s.trim(); }).filter(Boolean);
                liveNoteHtml(dtwBusyHtml('Asking the room'));
                if (inp && !presetQ) inp.value = '';
                try { var d = await api('/' + S.key + '/panel/' + S.session.id + '/ask', { method: 'POST', body: JSON.stringify(body) }); }
                catch (e) {
                    S.asking = false; setAskEnabled(true); liveNote(e.message); if (inp && !presetQ && !inp.value) inp.value = q;
                    if (e.doc && e.doc.upgrade) { S.fg.pendingQ = q; liveNoteHtml(esc(e.doc.upgrade.line || e.message) + ' <button type="button" class="dtw-link" onclick="dtwPanelUpgrade()">Move it to 1-40 for ' + esc(e.doc.upgrade.label || '') + '</button>'); }
                    else if (e.doc && e.doc.add_funds_url) { liveNoteHtml(esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>'); }
                    return;
                }
                S.asking = false;
                S.session.questions = S.session.questions || [];
                if (d.duplicate && d.question) {
                    // the same question again in this room: the record it already has comes back with a note; nothing was charged, no second job
                    adoptQuestion(d.question, true);
                    S.fg.open[d.question.id] = true;
                    if (d.question.id) S.fg.tallyQ = d.question.id;
                    renderPanelConsole();
                    liveNote(d.note || d.question.note || 'You asked this earlier in this session; here is that answer.');
                    var blk = document.querySelector('#dtwPnTranscript [data-q="' + esc(d.question.id) + '"]'); if (blk) fgScrollTo(blk);
                    return;
                }
                S.session.questions.push(d.question); if (d.questions_paid != null) S.session.questions_paid = d.questions_paid;
                if (d.question && d.question.id) S.fg.tallyQ = d.question.id;
                renderPanelConsole();
                var nb = document.querySelector('#dtwPnTranscript [data-q="' + esc(d.question.id) + '"]'); if (nb) fgScrollTo(nb); else fgScrollEnd();
            };
            // ---- who they are: the seat popover. The bio the seat acts on (roster[].persona) when the room has written it, else what the twin
            // carries for the seat; then what this seat said in the room, across every question (answers[].seat). Jenna 2026-10-26: 'If you click
            // on a panelist it should give you the bio about her ... so you can see who they are'.
            window.dtwSeatOpen = function(seatNo, anchor) {
                var p = el('dtwSeatPop');
                if (seatNo == null) { S.fg.seat = null; if (p) p.classList.remove('open'); markSeat(); return; }
                S.fg.seat = Number(seatNo); renderSeatPanel(); placePop(anchor); markSeat();
            };
            function markSeat() {
                document.querySelectorAll('#dtwPnConsole .dtw-fg-seat[data-seat]').forEach(function(b) { b.classList.toggle('sel', S.fg.seat != null && Number(b.getAttribute('data-seat')) === S.fg.seat); });
                document.querySelectorAll('#dtwPnConsole .dtw-fg-faceb[data-fg-seat]').forEach(function(b) { b.classList.toggle('on', S.fg.seat != null && Number(b.getAttribute('data-fg-seat')) === S.fg.seat); });
            }
            function placePop(anchor) {
                var root = el('dtwPnConsole'), p = el('dtwSeatPop'); if (!root || !p || S.fg.seat == null) return;
                if (!anchor || !(anchor instanceof Element) || !root.contains(anchor)) anchor = root.querySelector('.dtw-fg-seat[data-seat="' + S.fg.seat + '"]') || root.querySelector('[data-fg-seat="' + S.fg.seat + '"]');
                p.classList.add('open'); p.classList.remove('up', 'side-r', 'side-l'); p.style.removeProperty('--pop-max'); p.style.removeProperty('position');
                var arrowEl = p.querySelector('.arrow'); if (arrowEl) { arrowEl.style.removeProperty('top'); arrowEl.style.removeProperty('left'); }
                var rr = root.getBoundingClientRect(), pw = p.offsetWidth, sc = fgScroller(), sr = sc ? sc.getBoundingClientRect() : rr;
                var vTop = Math.max(rr.top, sr.top, 0), vBottom = Math.min(rr.bottom, sr.bottom, window.innerHeight);
                if (!anchor) { p.style.left = Math.max(8, rr.width - pw - 8) + 'px'; p.style.top = Math.max(8, vTop - rr.top + 8) + 'px'; p.style.setProperty('--pop-max', Math.max(160, vBottom - vTop - 40) + 'px'); return; }
                var ar = anchor.getBoundingClientRect(), cx = ar.left - rr.left + ar.width / 2;
                // beside the seat when there is room, so the bio runs the full height instead of the strip under the row
                var circ = anchor.querySelector('.dtw-fg-circ'), cr = circ ? circ.getBoundingClientRect() : ar, cy = cr.top + cr.height / 2;
                var rightX = cr.right - rr.left + 14, leftX = cr.left - rr.left - 14 - pw, side = rightX + pw <= rr.width - 8 ? 'side-r' : (leftX >= 8 ? 'side-l' : '');
                if (side) {
                    // fixed to the window so it can rise above the scrolling body, its top level with the twin's facts line
                    p.classList.add(side); p.style.position = 'fixed';
                    var facts = document.querySelector('#dtwSpineTwin .dtw-spine-facts'), fr = facts ? facts.getBoundingClientRect() : null;
                    var want = fr && fr.height && fr.top >= 0 ? Math.min(fr.top, cy - 34) : cy - 34, wBottom = window.innerHeight;
                    p.style.setProperty('--pop-max', Math.max(220, Math.min(wBottom - Math.max(8, want) - 16, 760)) + 'px');
                    var ph = p.offsetHeight, sTop = Math.max(8, Math.min(want, wBottom - 8 - ph));
                    p.style.left = (side === 'side-r' ? cr.right + 14 : cr.left - 14 - pw) + 'px'; p.style.top = sTop + 'px';
                    if (arrowEl) arrowEl.style.top = Math.max(12, Math.min(ph - 24, cy - sTop - 6)) + 'px';
                    var sb = el('dtwSeatBody'); if (sb) sb.scrollTop = 0;
                    return;
                }
                var left = Math.max(8, Math.min(cx - 48, rr.width - pw - 8)), top = ar.bottom - rr.top + 10, below = vBottom - ar.bottom - 20, above = ar.top - vTop - 20;
                if (below < 260 && above > Math.max(below, 300)) { p.classList.add('up'); p.style.setProperty('--pop-max', Math.max(160, Math.min(above, 560)) + 'px'); top = Math.max(4, ar.top - rr.top - 10 - p.offsetHeight); }
                else p.style.setProperty('--pop-max', Math.max(160, Math.min(Math.max(below, 240), 560)) + 'px');
                p.style.left = left + 'px'; p.style.top = top + 'px';
                var arrow = p.querySelector('.arrow'); if (arrow) arrow.style.left = Math.max(12, Math.min(pw - 24, cx - left - 6)) + 'px';
                var inner = el('dtwSeatBody'); if (inner) inner.scrollTop = 0;
                // the thread scrolls, so a card that runs past the bottom edge is brought up, never past its own seat
                if (sc && anchor && sc.contains(anchor) && !p.classList.contains('up')) { var pr = p.getBoundingClientRect(), over = pr.bottom - (sr.bottom - 10); if (over > 0) sc.scrollTop += Math.min(over, Math.max(0, ar.top + 40 - sr.top)); }
            }
            function seatSaid(s, seatNo) { var out = []; (s.questions || []).forEach(function(q, i) { var a = (q.answers || []).filter(function(x) { return Number(x.seat) === Number(seatNo); })[0]; if (a && (a.line || a.option_label)) out.push({ i: i, q: q, a: a }); }); return out; }
            function bioParas(text) {
                var parts = String(text || '').split(/\n+/).map(function(p) { return p.trim(); }).filter(Boolean);
                if (parts.length === 1 && parts[0].length > 380) {
                    var sents = parts[0].match(/[^.!?]+[.!?]+["']?(\s+|$)/g) || [parts[0]], per = Math.ceil(sents.length / Math.min(3, Math.ceil(sents.length / 3)));
                    parts = []; for (var i = 0; i < sents.length; i += per) parts.push(sents.slice(i, i + per).join('').trim());
                }
                return parts.map(function(p) { return '<p>' + esc(p) + '</p>'; }).join('');
            }
            function seatTwinCarries() {
                var t = S.twin; if (!t) return '';
                var demo = ((t.identity || {}).demographics || {}), bits = [];
                [['age', 'Age'], ['income', 'Income'], ['education', 'Education'], ['parental', 'Children at home'], ['relationship', 'Relationship']].forEach(function(c) { var rows = (demo[c[0]] || []).slice().sort(function(a, b) { return Number(b.pct) - Number(a.pct); }); if (rows.length) bits.push(c[1] + ', the largest group: ' + rows[0].label + ' (' + pct(rows[0].pct, 1) + ')'); });
                var out = bits.length ? '<h5>What the twin carries</h5>' + bits.map(function(b) { return '<p>' + esc(b) + '</p>'; }).join('') : '';
                var tr = t.traits || {}, keys = Object.keys(tr);
                if (keys.length) out += '<h5>How they respond</h5><p>' + keys.map(function(k) { var v = tr[k] || {}; return esc(v.label || k) + ' ' + esc(v.value); }).join(', ') + '. 50 is an ordinary US audience.</p>';
                return out;
            }
            function renderSeatPanel() {
                var h = el('dtwSeatBody'), s = S.session; if (!h) return;
                if (!s) { h.innerHTML = '<p>Open a room first.</p>'; return; }
                var g = s.group || {}, roster = g.roster || [], r = roster.filter(function(x) { return Number(x.seat) === S.fg.seat; })[0];
                if (!r) { h.innerHTML = '<p>That seat is not in this room.</p>'; return; }
                var nm = titleCase(r.name), inRoom = fmtN(g.in_room || roster.length), who = r.gender === 'F' ? 'woman' : (r.gender === 'M' ? 'man' : ''), fq = focusQ();
                var out = '<div class="head">' + face(r, Number(r.seat) - 1, 64) + '<div style="min-width:0;"><div class="who">' + esc(nm) + ', ' + esc(r.age) + ', ' + esc(titleCase(r.city)) + '</div><div class="stands">Seat ' + esc(r.seat) + ' of ' + inRoom + (who ? SEP + who : '') + '</div>' + (standsForLine(nm, g, r) ? '<div class="stands">' + standsForLine(nm, g, r) + '</div>' : '') + (earlierGroupsLine(s.id, r.seat) ? '<div class="stands earlier">' + esc(earlierGroupsLine(s.id, r.seat)) + '</div>' : '') + '</div></div>';
                var tags = '';
                if (r.segment && (multiSeg(roster) || normQ(r.segment) !== normQ(twinName()))) tags += '<span class="dtw-fg-tag">' + esc(r.segment) + '</span>';
                var a0 = fq ? (fq.answers || []).filter(function(x) { return Number(x.seat) === Number(r.seat); })[0] : null;
                if (a0) tags += optTag(fq, rankMap(fq), answerOptId(fq, a0), a0.option_label || '');
                if (tags) out += '<div class="tags">' + tags + '</div>';
                out += '<h5>Who they are</h5>';
                if (r.persona) out += bioParas(r.persona);
                else out += '<p>' + esc(nm) + ' holds one of ' + inRoom + ' seats standing for ' + fmtN(perSeat(g, r) * Number(g.in_room || roster.length || 1)) + ' ' + esc(r.segment || twinName()) + ' people. Who ' + (r.gender === 'M' ? 'he' : 'she') + ' is, in full, is written the first time this room answers a question.</p>' + seatTwinCarries();
                var said = seatSaid(s, r.seat);
                out += '<h5>What they said in this room <span>&middot; ' + said.length + (said.length === 1 ? ' answer' : ' answers') + '</span></h5>';
                if (!said.length) out += '<p>Nothing yet. Ask the room a question and ' + esc(nm) + ' answers in character.</p>';
                else out += said.map(function(x) { var rk = rankMap(x.q), optId = answerOptId(x.q, x.a); return '<div class="said"><div class="q">' + ((x.i < 9 ? '0' : '') + (x.i + 1)) + ' &middot; ' + esc(x.q.q) + '</div>' + optTag(x.q, rk, optId, x.a.option_label || '') + '<div class="line">' + esc(x.a.line || '') + '</div>' + (x.a.reason ? '<div class="why">' + esc(x.a.reason) + '</div>' : '') + '</div>'; }).join('');
                out += '<div class="pacts"><button type="button" class="dtw-fg-act" onclick="dtwFgAskSeat(' + Number(r.seat) + ')">Ask ' + esc(nm) + '</button><button type="button" class="dtw-fg-act" onclick="dtwSeatOpen(null)">Close</button></div>';
                h.innerHTML = out;
            }
            // Portraits finish after the room is seated: re-read the session once or twice and redraw the band, the thread and the voices, nothing else.
            function schedulePortraitRefresh() {
                clearPortraitTimers();
                var s = S.session; if (!s || !S.key) return;
                var roster = (s.group || {}).roster || [];
                if (!roster.length || roster.every(function(r) { return !!r.portrait_url; })) return;
                var sid = s.id, key = S.key;
                [20000, 75000].forEach(function(ms) {
                    S.portraitTimers.push(setTimeout(async function() {
                        if (!S.session || S.session.id !== sid || S.key !== key) return;
                        try {
                            var d = await api('/' + key + '/panel/' + sid);
                            var fresh = ((d.session || {}).group || {}).roster || [];
                            var changed = false;
                            fresh.forEach(function(fr) { var cur = roster.filter(function(r) { return r.seat === fr.seat; })[0]; if (cur && fr.portrait_url && cur.portrait_url !== fr.portrait_url) { cur.portrait_url = fr.portrait_url; changed = true; } });
                            if (changed) { fgRefresh(); if (S.fg.seat != null) renderSeatPanel(); }
                        } catch (_) {}
                    }, ms));
                });
            }
            window.dtwPanelRename = async function(raw) {
                var s = S.session, inp = el('dtwFgTitle');
                if (!s || !S.key) return;
                var title = String(raw == null ? ((inp && inp.value) || '') : raw).replace(/\s+/g, ' ').trim().slice(0, 120);
                if (inp) inp.dataset.dirty = '';
                if (!title) { if (inp) inp.value = roomTitle(s); return; }
                if (title === String(s.title || '').trim()) { if (inp) inp.value = title; return; }
                var prev = s.title;
                s.title = title;
                if (inp) inp.value = title;
                renderPanelSessions();
                try {
                    var d = await api('/' + S.key + '/panel/' + s.id + '/rename', { method: 'POST', body: JSON.stringify({ title: title }) });
                    if (d.session && d.session.title) {
                        s.title = d.session.title;
                        var ss = (S.twin || {}).panel_sessions || [];
                        for (var i = 0; i < ss.length; i++) { if (ss[i].id === s.id) ss[i].title = d.session.title; }
                        if (inp && document.activeElement !== inp) inp.value = d.session.title;
                    }
                    renderPanelSessions();
                    var n = el('dtwPnSaved'); if (n) n.textContent = 'Name saved.';
                } catch (e) {
                    s.title = prev;
                    if (inp && document.activeElement !== inp) inp.value = roomTitle(s);
                    var n2 = el('dtwPnSaved'); if (n2) n2.textContent = e.message;
                }
            };
            window.dtwPanelSave = async function() {
                if (!S.session || !S.key) return;
                var tok = dtwBusy(dtwBtn('[onclick^="dtwPanelSave("]'), 'Saving', el('dtwPnSaved')); if (tok === false) return;
                dtwStage(tok, 'Saving');
                try {
                    var d = await api('/' + S.key + '/panel/' + S.session.id);
                    if (d.session) { var ss = (S.twin || {}).panel_sessions || []; for (var i = 0; i < ss.length; i++) { if (ss[i].id === d.session.id) ss[i] = d.session; } S.session = d.session; }
                    dtwIdle(tok);
                    renderPanelConsole(); renderPanelSessions();
                    var n2 = el('dtwPnSaved'); if (n2) n2.textContent = 'Saved on the twin.';
                } catch (e) { dtwIdle(tok, e); }
            };

            S.pilot = S.pilot || null;
            S.pilotKind = S.pilotKind || '';
            S.pilotClock = S.pilotClock || { raf: 0, t: 0, t0: 0, slate: false, bound: '' };
            var PI_EVT = { love: 'Loved it', laugh: 'Laughed', 'lean-in': 'Leaned in', confuse: 'Lost', 'check-out': 'Checked out', wow: 'Wow' };
            function clockFmt(t) { t = Math.max(0, Math.round(Number(t) || 0)); var m = Math.floor(t / 60), s = t % 60; return m + ':' + (s < 10 ? '0' : '') + s; }
            function pilotPrice() { var c = groupCard(); if (c && c.quote) { return groupQuote(groupTierDefault()).replace(/^This group/, 'A test'); } var it = ((S.howto || {}).items || []).filter(function(x) { return x.id === 'pilot'; })[0]; return it ? String(it.cost || '') : 'One group at the plan rate, charged when the test opens'; }
            window.piTierNote = function() { var n = el('dtwPiTierNote'); if (n) n.textContent = groupQuote((el('dtwPiTier') || {}).value).replace(/^This group/, 'This test'); };
            S.piTab = S.piTab || 'pilot'; S.piMode = S.piMode || 'pilot'; S.pilotCompare = S.pilotCompare || null;
            function adoptPilot(sess) {
                if (!sess) return;
                S.pilot = sess;
                var list = (S.twin || {}).pilot_sessions || [];
                var hit = false;
                for (var i = 0; i < list.length; i++) { if (list[i].id === sess.id) { list[i] = sess; hit = true; } }
                if (!hit && S.twin) { S.twin.pilot_sessions = [sess].concat(list); }
            }
            function hasMedia(s) { var m = (s || {}).media || {}; return !!m.has_file; }
            function mediaUrl(s) { return (hasMedia(s) && S.pilotMedia && S.pilotMedia.sid === s.id && S.pilotMedia.url) ? S.pilotMedia.url : ''; }
            function watchMedia(s) { var m = (s || {}).media || {}; return (m.source === 'watch_url' && (m.url || m.embed_url)) ? m : null; }
            function wirePilotVideo(v) {
                if (!v || v._piBound) return;
                v._piBound = true;
                v.addEventListener('timeupdate', function() { revealPilot(v.currentTime); });
                v.addEventListener('play', function() {
                    stopPilotClock();
                    (function tick() { if (!v || v.paused) return; revealPilot(v.currentTime); S.pilotClock.raf = requestAnimationFrame(tick); })();
                });
                v.addEventListener('pause', function() { stopPilotClock(); revealPilot(v.currentTime); });
                v.addEventListener('ended', function() { if (piReplayable(S.pilot)) { piReplayHalt(true); return; } try { dtwPilotStop(true); } catch (_) {} });
                v.addEventListener('loadedmetadata', function() { if (piReplayable(S.pilot)) { try { v.currentTime = Number((S.pilotClock || {}).t || 0); } catch (_) {} } });
            }
            function bindWatchLink(s) {
                var m = watchMedia(s);
                var v = el('dtwPiVideo'), fr = el('dtwPiFrame'), slate = el('dtwPiSlate');
                if (!m) {
                    if (fr) { try { fr.removeAttribute('src'); } catch (_) {} fr.style.display = 'none'; }
                    return false;
                }
                if (m.kind === 'video' && (m.embed_url || m.url) && v) {
                    var url = m.embed_url || m.url;
                    if (S.pilotClock.bound !== url) { v.src = url; S.pilotClock.bound = url; }
                    v.style.display = 'block';
                    if (fr) { try { fr.removeAttribute('src'); } catch (_) {} fr.style.display = 'none'; }
                    if (slate) slate.style.display = 'none';
                    wirePilotVideo(v);
                    return true;
                }
                // an embeddable player (YouTube, Vimeo) used to sit in an iframe here; a cross-site frame gives the room no
                // frames to read, so every non-file link now opens on its own site and is mirrored into the stage (2026-10-30)
                if (v) { try { v.removeAttribute('src'); } catch (_) {} v.style.display = 'none'; }
                if (fr) { try { fr.removeAttribute('src'); } catch (_) {} fr.style.display = 'none'; }
                if (slate) slate.style.display = 'flex';
                S.pilotClock.bound = 'slate';
                return false;
            }
            function openWatchWithRoom(s) {
                var m = watchMedia(s);
                if (!m || !m.url || m.kind === 'video') return;
                try { window.open(m.url, 'dtwWatch'); } catch (_) {}
            }
            // ---- live watch (2026-10-30): a watch link is mirrored into the stage; a frame goes to the room every twelve seconds and the seats react to it.
            var LIVE_FRAME_MS = 12000, LIVE_FRAME_W = 512;
            function isLiveSess(s) { s = s || {}; return !!((s.live && s.live.on) || (watchMedia(s) && !hasMedia(s))); }
            function liveFinished(s) { return !!(((s || {}).live || {}).finished_at); }
            function attachLiveMirror(s) {
                var L = S.pilotLive; if (!L || !L.stream || !s || L.sid !== s.id) return false;
                var v = el('dtwPiVideo'), slate = el('dtwPiSlate'), fr = el('dtwPiFrame');
                if (!v) return false;
                if (fr) { try { fr.removeAttribute('src'); } catch (_) {} fr.style.display = 'none'; }
                try { if (v.srcObject !== L.stream) v.srcObject = L.stream; v.muted = true; v.controls = false; v.style.display = 'block'; v.play().catch(function() {}); } catch (_) {}
                if (slate) slate.style.display = 'none';
                ensureWatermark(s);
                return true;
            }
            function piPlannedRuntime(s) {
                var m = (s || {}).media || {};
                var v = Number(m.runtime_planned_s != null ? m.runtime_planned_s : (m.runtime_s || 0));
                return isFinite(v) && v > 0 ? v : 0;
            }
            function piParseRuntime(text) {
                // '42:35' is 42 minutes 35 seconds; '1:02:10' has hours; '42' or '42.5' is minutes
                var t = String(text || '').trim(); if (!t) return 0;
                if (t.indexOf(':') >= 0) {
                    var parts = t.split(':').map(function(x) { return parseFloat(x || '0') || 0; });
                    if (parts.length === 2) return Math.max(0, parts[0] * 60 + parts[1]);
                    if (parts.length === 3) return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2]);
                    return 0;
                }
                var mins = parseFloat(t); return isFinite(mins) && mins > 0 ? mins * 60 : 0;
            }
            function startLiveClock() {
                var L = S.pilotLive; if (!L) return;
                stopPilotClock(); S.pilotClock.slate = false;
                L.t0 = Date.now() - (Number(S.pilotClock.t) || 0) * 1000;
                L.running = true;
                (function tick() {
                    if (!S.pilotLive || !S.pilotLive.running) return;
                    var t = (Date.now() - S.pilotLive.t0) / 1000;
                    var planned = piPlannedRuntime(S.pilot);
                    if (planned > 0 && t >= planned && S.pilot && S.pilot.status === 'playing' && S.pilotAutoStopped !== S.pilot.id) {
                        // the runtime ran out: the room is scored without anyone pressing Stop (Jenna 2026-11-03)
                        S.pilotAutoStopped = S.pilot.id;
                        revealPilot(planned);
                        try { dtwPilotStop(true); } catch (_) {}
                        return;
                    }
                    revealPilot(t);
                    maybeSendLiveFrame(t);
                    S.pilotClock.raf = requestAnimationFrame(tick);
                })();
            }
            function pauseLiveClock() { if (S.pilotLive) S.pilotLive.running = false; stopPilotClock(); }
            function stopLiveMirror() {
                var L = S.pilotLive; if (!L) return;
                pauseLiveClock();
                try { (L.stream ? L.stream.getTracks() : []).forEach(function(tr) { try { tr.stop(); } catch (_) {} }); } catch (_) {}
                L.stream = null;
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); v.srcObject = null; } catch (_) {} }
            }
            function maybeSendLiveFrame(t) {
                var L = S.pilotLive; if (!L || L.busy || !L.stream) return;
                var now = Date.now(); if (now - (L.lastSent || 0) < LIVE_FRAME_MS) return;
                var v = el('dtwPiVideo'); if (!v || v.readyState < 2 || !v.videoWidth) return;
                L.lastSent = now; L.busy = true;
                var data = '';
                try {
                    var w = LIVE_FRAME_W, h = Math.max(1, Math.round(v.videoHeight * (w / v.videoWidth)));
                    var c = L.canvas || (L.canvas = document.createElement('canvas')); c.width = w; c.height = h;
                    c.getContext('2d').drawImage(v, 0, 0, w, h);
                    data = c.toDataURL('image/jpeg', 0.6);
                } catch (e) { L.busy = false; L.failed = (L.failed || 0) + 1; return; }
                var s = S.pilot, key = S.key, sid = s ? s.id : ''; if (!s || !key) { L.busy = false; return; }
                apiRaw('/' + encodeURIComponent(key) + '/pilot/' + encodeURIComponent(sid) + '/frame', { method: 'POST', body: JSON.stringify({ t: Math.round(t * 100) / 100, image: data }) }).then(function(r) {
                    L.busy = false;
                    if (!r.ok || !r.data || r.data.skipped || !S.pilot || S.pilot.id !== sid) return;
                    mergeLiveFrame(S.pilot, r.data);
                    L.sent = (L.sent || 0) + 1;
                    revealPilot(playheadNow());
                }).catch(function() { L.busy = false; L.failed = (L.failed || 0) + 1; });
            }
            function mergeLiveFrame(s, d) {
                s.dial = s.dial || { t: [], value: [], splits: {} };
                s.dial.t = s.dial.t || []; s.dial.value = s.dial.value || []; s.dial.splits = s.dial.splits || {};
                s.live = s.live || { on: true }; s.live.frames = s.live.frames || []; s.live.seat_dials = s.live.seat_dials || {};
                // a frame the page already holds (a poll of the test landed it first) is never laid down twice
                if (s.live.frames.some(function(f) { return Number(f.t) === Number(d.t); })) return;
                var modesBefore = piViewModesKey(s);
                if (d.dial) { s.dial.t.push(Number(d.dial.t)); s.dial.value.push(Number(d.dial.value)); }
                var meta = d.split_meta || {};
                Object.keys(d.splits || {}).forEach(function(k) {
                    // a new split row takes the label and group the room gave it: a cohort row is a cohort, not a gender (2026-11-04)
                    var m = meta[k] || {};
                    var row = s.dial.splits[k] = s.dial.splits[k] || { label: m.label || (k === 'women' ? 'Women' : (k === 'men' ? 'Men' : k)), group: m.group || (/^cohort_/.test(k) ? 'cohort' : 'gender'), n: Number(m.n || 0), value: [] };
                    if (m.n != null) row.n = Number(m.n);
                    while (row.value.length < s.dial.value.length - 1) row.value.push(row.value.length ? row.value[row.value.length - 1] : Number(d.splits[k]));
                    row.value.push(Number(d.splits[k]));
                });
                // every seat's own reading at this frame: the People and Cohorts views draw it while the room is still watching
                Object.keys(d.seats || {}).forEach(function(sno) {
                    var v = Number(d.seats[sno]); if (!isFinite(v)) return;
                    (s.live.seat_dials[String(sno)] = s.live.seat_dials[String(sno)] || []).push([Number(d.t), v]);
                });
                (d.events || []).forEach(function(e) { (s.events = s.events || []).push(e); });
                if (d.caption) { var sc = s.scenes = s.scenes || []; if (sc.length) sc[sc.length - 1].t_end = Number(d.t); sc.push({ id: sc.length, t_start: Number(d.t), t_end: Number(d.t) + 12, caption: d.caption }); }
                s.watch = s.watch || {}; s.watch.source = 'live';
                s.live.frames.push({ t: Number(d.t), caption: d.caption || '' });
                s.media = s.media || {};
                if (Number(d.t) > Number(s.media.runtime_s || 0)) {
                    // the dial keeps drawing past a planned runtime, but the planned runtime is what the auto-stop reads
                    if (s.media.runtime_planned_s == null && Number(s.media.runtime_s || 0) > 0) s.media.runtime_planned_s = Number(s.media.runtime_s);
                    s.media.runtime_s = Math.max(Number(s.media.runtime_s || 0), Number(d.t) + 60);
                }
                if (piViewModesKey(s) !== modesBefore) piLegendRepaint();   // the first frame turns a view on: the toggles show it at once
            }
            window.dtwPilotMirror = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var note = el('dtwPiNote');
                if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) { if (note) note.textContent = 'This browser cannot mirror a tab. Use Chrome or Edge on a computer.'; return; }
                // the mirror start shows it is working too (Jenna 2026-11-05): the button spins through the picker, the handshake and the first frame
                var tok = dtwBusy(dtwBtn('.dtw-pi-slatebtns .dtw-btn-primary'), 'Mirroring', note); if (tok === false) return;
                dtwHoldMore(tok, '.dtw-pi-controls button:not(#dtwPiPlayBtn), .dtw-pi-head-r button');
                dtwStage(tok, 'Connecting the room');
                var stream;
                try {
                    stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: { ideal: 5, max: 10 } }, audio: false, selfBrowserSurface: 'exclude', surfaceSwitching: 'include' });
                } catch (e) { dtwIdle(tok, 'The mirror was not started. Pick the tab or window with the episode.'); return; }
                stopLiveMirror();
                S.pilotLive = { stream: stream, sid: s.id, lastSent: 0, busy: false, sent: 0, failed: 0, running: false, t0: 0 };
                var track = stream.getVideoTracks()[0];
                if (track) track.addEventListener('ended', function() { dtwPilotMirrorEnded(); });
                attachLiveMirror(s);
                if (s.status !== 'playing') { await dtwPilotPlay(true); }
                if (!S.pilotLive || !S.pilotLive.stream) { dtwIdle(tok); return; }
                dtwStage(tok, 'First frame');
                startLiveClock();
                await dtwFirstFrame(el('dtwPiVideo'));
                dtwIdle(tok);
                var n2 = el('dtwPiNote'); if (n2) n2.textContent = 'The room is watching with you.';
                var b = el('dtwPiPlayBtn'); if (b) b.textContent = 'Playing';
            };
            window.dtwPilotMirrorEnded = function() {
                var s = S.pilot;
                stopLiveMirror();
                var v = el('dtwPiVideo'), slate = el('dtwPiSlate');
                if (v) v.style.display = 'none';
                if (slate) { slate.style.display = 'flex'; var h = slate.querySelector('.s:not(#dtwPiOn)'); if (h) h.textContent = 'The mirror stopped. Mirror it again to keep going, or press Stop to score what the room watched.'; }
                var note = el('dtwPiNote'); if (note) note.textContent = 'The mirror stopped.';
                if (s) try { remember(); } catch (_) {}
            };
            async function apiRaw(path, opts) {
                var r = await fetch('/api/digital-twins' + path, Object.assign({ credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } }, opts || {}));
                var d = null; try { d = await r.json(); } catch (_) { d = null; }
                return { ok: r.ok && d && d.success !== false, status: r.status, data: d || {} };
            }
            // the norm sets a test can be compared to (migration/digital_twin_norms.GENRES without 'all'; scripts/test_digital_twin_norms_choice.py holds the two in step)
            var PI_GENRES = ['procedural', 'drama', 'comedy', 'action', 'thriller', 'horror', 'romance', 'family', 'animation', 'documentary', 'unscripted'];
            var PI_NORM_NOUN = { pilot: 'pilots', feature: 'features', episode: 'episodes', binge: 'episodes', trailer: 'trailers', spot: 'trailers', title: 'titles', key_art: 'key art', script: 'scripts', concept: 'concepts' };
            var PI_NORM_ONE = { pilots: 'pilot', features: 'feature', episodes: 'episode', trailers: 'trailer', titles: 'title', 'key art': 'key art', scripts: 'script', concepts: 'concept' };
            function piNormNoun(fmt) { return PI_NORM_NOUN[String(fmt || 'pilot')] || 'pilots'; }
            function piNormLabel(fmt, genre) { return genre ? cap(genre) + ' ' + piNormNoun(fmt) : 'All ' + piNormNoun(fmt); }
            // the norm a test is compared to (Jenna 2026-11-04: the user sets it): the card's own read when the server put one on it
            function piNormChoice(s) {
                var sc = (s || {}).scorecard || {}, ch = sc.norm_choice;
                if (ch && typeof ch === 'object' && ch.label) return ch;
                var fmt = (s || {}).format || sc.format || (s || {}).mode || 'pilot', genre = (s || {}).genre || '';
                var has = !!genre && ((sc.norms || {}).genre_used || 'all') !== 'all';
                var note = (genre && !has) ? 'No ' + genre + ' norm yet; it fills as tests are filed under ' + genre + ', 0 of 5 so far.' : '';
                return { genre: genre, label: piNormLabel(fmt, genre), all_label: piNormLabel(fmt, ''), has_genre_norm: has, own_tests: 0, needed: 5, stand_in: '', compared_label: has ? piNormLabel(fmt, genre) : piNormLabel(fmt, ''), notes: note ? [note] : [], note: note };
            }
            // the norm a card is actually read against, from the server's choice ('All pilots', 'Procedural pilots', 'Feature screenings' while the all column is a stand-in)
            function piComparedLabel(ch) { ch = ch || {}; return String(ch.compared_label || (ch.has_genre_norm ? ch.label : ch.all_label) || 'All pilots'); }
            // the honest lines, each once ('No pilot norm yet; it fills as pilot tests are filed, 1 of 5 so far.')
            function piNormNotes(ch) { ch = ch || {}; return (ch.notes && ch.notes.length) ? ch.notes.slice() : (ch.note ? [ch.note] : []); }
            // 'the procedural pilot norm' / 'the all-pilots norm' / 'the feature screening norm'
            function piNormWord(ch, which) { if (which !== 'genre' && ch && ch.stand_in) return String(ch.stand_in) + ' norm'; var lab = String((which === 'genre' ? ch.label : ch.all_label) || 'All pilots'); var parts = lab.split(' '); var noun = parts.slice(1).join(' '); return which === 'genre' ? (parts[0].toLowerCase() + ' ' + (PI_NORM_ONE[noun] || noun) + ' norm') : ('all-' + noun + ' norm'); }
            // the seat tally behind every money share, from the battery's rows ('8 of 16'); '' on an older card
            function piTallies(bat) {
                var out = {}, seats = 0;
                (bat.questions || []).forEach(function(q) { (q.rows || []).forEach(function(r) { var m = /of\s+(\d+)/.exec(String(r.tally || '')); if (m) seats = Math.max(seats, Number(m[1])); }); });
                (bat.questions || []).forEach(function(q) {
                    var rows = q.rows || []; if (!rows.length) return;
                    var top = String(rows[0].tally || '');
                    if (q.id === 'overall') { out.excellent = top; out.total_positive = (top && rows[1] && rows[1].tally) ? (Number(rows[0].seats_n || 0) + Number(rows[1].seats_n || 0)) + ' of ' + seats : ''; }
                    else if (q.id === 'recommend') out.definitely_recommend = top;
                    else if (q.id === 'finish') out.finish = top;
                    else if (q.id === 'tune_in') out.tune_in = top;
                });
                out.seats = seats;
                return out;
            }
            // what the shares are of, in one plain line (the server's when the card carries it)
            function piDenominator(s, sc) {
                if (sc && sc.denominator_line) return String(sc.denominator_line);
                var quads = ((sc || {}).battery || {}).quadrants || [], total = null;
                quads.forEach(function(q) { if (q && q.key === 'total') total = q.people; });
                var seats = (((s || {}).group || {}).roster || []).length;
                var seatW = seats ? 'the ' + seats + ' seats' : 'the seats';
                return total ? 'Shares are of the ' + piPeopleShort(total) + ' people ' + seatW + ' stand for; the count beside each share is the seats themselves.' : 'Shares are of the audience ' + seatW + ' stand for; the count beside each share is the seats themselves.';
            }
            function piNormOptions(fmt, genre) { return '<option value=""' + (!genre ? ' selected' : '') + '>' + esc(piNormLabel(fmt, '')) + '</option>' + PI_GENRES.map(function(g) { return '<option value="' + g + '"' + (genre === g ? ' selected' : '') + '>' + esc(cap(g)) + '</option>'; }).join(''); }
            // the words in a title that suggest a norm (the same order as migration/digital_twin_pilot.keyword_genre: procedural first, drama last)
            var PI_NORM_WORDS = [['procedural', /\b(procedurals?|cops?|cop show|detectives?|precinct|ncis|csi|fbi|law\s*&\s*order|law and order|homicide|forensics?|police drama|police procedural)\b/i], ['horror', /\b(horror|scare|scary|haunt|slasher)\b/i], ['comedy', /\b(comedy|sitcom|funny|laugh|romcom|rom-com|sketch|spoof|parody)\b/i], ['thriller', /\b(thriller|horror|scare|suspense|crime|noir|mystery)\b/i], ['action', /\b(action|heist|war|chase|fight|superhero|stunt)\b/i], ['romance', /\b(romance|love|wedding|date|affair)\b/i], ['family', /\b(family|kids|animated|animation|cartoon)\b/i], ['documentary', /\b(documentary|docuseries|true story|doc)\b/i], ['unscripted', /\b(reality|unscripted|competition|game show|dating show)\b/i], ['drama', /\b(dramas?|dramatic series|prestige)\b/i]];
            function piGuessNorm(text) { var t = String(text || ''); for (var i = 0; i < PI_NORM_WORDS.length; i++) { if (PI_NORM_WORDS[i][1].test(t)) return PI_NORM_WORDS[i][0]; } return ''; }
            // the Compare-to field follows the title while the person has not touched it
            function piNormGuessSync() {
                var sel = el('dtwPiNorm'), t = el('dtwPiNormTouched'); if (!sel) return;
                var hint = el('dtwPiNormHint');
                if (t && t.value === '1') { if (hint) hint.textContent = 'Every score is read against this norm. You can change it on the finished card too.'; return; }
                var g = piGuessNorm(((el('dtwPiTitle') || {}).value || '') + ' ' + ((el('dtwPiCutTitle') || {}).value || '') + ' ' + ((el('dtwPiLogline') || {}).value || ''));
                sel.value = g;
                if (hint) hint.textContent = g ? 'Suggested from the title. Change it if the show is something else; the room checks it once it has watched the cut.' : 'Every score is read against this norm. The room suggests one once it has watched the cut; you can change it any time.';
            }
            window.dtwPilotNormTouched = function() { var t = el('dtwPiNormTouched'); if (t) t.value = '1'; piNormGuessSync(); };
            var PI_SPLIT_A = '#E682FF', PI_SPLIT_B = '#3358FF';
            // The cut plays through a signed link that lives ten minutes; the player asks for a fresh one before it expires
            // and swaps it in at the same second. The file's storage key never reaches the page.
            async function fetchMediaUrl(s) {
                if (!s || !S.key || !hasMedia(s)) return null;
                var r = await apiRaw('/' + encodeURIComponent(S.key) + '/pilot/' + encodeURIComponent(s.id) + '/media-url', { method: 'GET' });
                if (!r.ok) { S.pilotMedia = { sid: s.id, url: '', denied: true, error: (r.data || {}).error || '' }; return null; }
                var d = r.data;
                S.pilotMedia = { sid: s.id, url: d.url, expiresIn: Number(d.expires_in || 600), issued: Date.now(), who: ((d.watermark || {}).who) || '', denied: false };
                scheduleMediaRefresh(s);
                return d.url;
            }
            function scheduleMediaRefresh(s) {
                if (S.pilotMedia && S.pilotMedia.timer) { try { clearTimeout(S.pilotMedia.timer); } catch (_) {} }
                if (!S.pilotMedia || !S.pilotMedia.url) return;
                var wait = Math.max(30, (S.pilotMedia.expiresIn || 600) * 0.8) * 1000;
                S.pilotMedia.timer = setTimeout(async function() {
                    if (!S.pilot || S.pilot.id !== s.id) return;
                    var v = el('dtwPiVideo'); if (!v) return;
                    var t = v.currentTime || 0, wasPlaying = !v.paused && !v.ended;
                    var url = await fetchMediaUrl(S.pilot); if (!url) return;
                    var once = function() { v.removeEventListener('loadedmetadata', once); try { v.currentTime = t; } catch (_) {} if (wasPlaying) { try { v.play(); } catch (_) {} } };
                    v.addEventListener('loadedmetadata', once);
                    v.src = url; S.pilotClock.bound = url;
                }, wait);
            }
            // Two watermark layers above the video: a drifting tag with the viewer's name, the test and the clock, and a tiled
            // layer of the same words. Both ignore the pointer; hiding one leaves the other, and a removed layer comes back.
            function wmText(s) {
                var who = (S.pilotMedia && S.pilotMedia.who) || (typeof currentUserEmail !== 'undefined' && currentUserEmail) || (typeof currentUsername !== 'undefined' && currentUsername) || '';
                return (who ? who + '  ' : '') + 'test ' + String((s || {}).id || '');
            }
            function wmTile(text) {
                var t = String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
                var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="200"><text x="10" y="110" transform="rotate(-18 210 100)" font-family="Crosswalk Inter, Inter, sans-serif" font-size="13" fill="rgba(233,232,225,0.9)" letter-spacing="1.5">' + t + '</text></svg>';
                return 'url("data:image/svg+xml;utf8,' + encodeURIComponent(svg) + '")';
            }
            function ensureWatermark(s) {
                var stage = document.querySelector('.dtw-pane.active .dtw-pi .dtw-pi-stage'); if (!stage) return;
                var a = stage.querySelector('.dtw-pi-wm.a'), b = stage.querySelector('.dtw-pi-wm.b');
                if (!a) { a = document.createElement('div'); a.className = 'dtw-pi-wm a'; a.innerHTML = '<span class="tag"></span>'; stage.appendChild(a); }
                if (!b) { b = document.createElement('div'); b.className = 'dtw-pi-wm b'; stage.appendChild(b); }
                var text = wmText(s), tag = a.querySelector('.tag');
                if (tag) tag.textContent = text + '  ' + clockFmt(playheadNow());
                if (b.getAttribute('data-wm') !== text) { b.style.backgroundImage = wmTile(text); b.setAttribute('data-wm', text); }
                if (!S.pilotWm) S.pilotWm = {};
                if (S.pilotWm.stage !== stage) {
                    if (S.pilotWm.obs) { try { S.pilotWm.obs.disconnect(); } catch (_) {} }
                    S.pilotWm.stage = stage;
                    try { S.pilotWm.obs = new MutationObserver(function() { if (S.pilot && hasMedia(S.pilot) && (!stage.querySelector('.dtw-pi-wm.a') || !stage.querySelector('.dtw-pi-wm.b'))) ensureWatermark(S.pilot); }); S.pilotWm.obs.observe(stage, { childList: true }); } catch (_) {}
                }
                if (!S.pilotWm.timer) {
                    S.pilotWm.timer = setInterval(function() {
                        var st = document.querySelector('.dtw-pane.active .dtw-pi .dtw-pi-stage'); if (!st) return;
                        var tg = st.querySelector('.dtw-pi-wm.a .tag'); if (!tg) { if (S.pilot && hasMedia(S.pilot)) ensureWatermark(S.pilot); return; }
                        var x = 4 + Math.random() * 62, y = 6 + Math.random() * 78;
                        tg.style.left = x.toFixed(1) + '%'; tg.style.top = y.toFixed(1) + '%';
                        tg.textContent = wmText(S.pilot) + '  ' + clockFmt(playheadNow());
                    }, 4000);
                }
            }
            // ---- the replay (Jenna 2026-11-19: "after one finishes you see the dial test still and can run the timeline
            // back/forward/etc to see what was happening at that time if you want"). A finished test keeps its dial on screen,
            // drawn in full, and the playhead runs over it: Replay and Pause, ten seconds either way, the start and the end,
            // a scrubber under the dial, the arrow keys and the space bar. Whatever sits under the playhead is read at that
            // second: the scene on screen, the room's line and every group's, the seats that moved most in the last half
            // minute, and what the seats said in it. Nothing is added: every reading, remark and scene is the test's own.
            S.pilotReplay = S.pilotReplay || { sid: '', on: false, raf: 0, t0: 0, from: 0, rate: 1 };
            var PI_STEP_S = 10, PI_KEY_STEP_S = 5, PI_KEY_BIG_S = 30, PI_NOW_BACK_S = 30, PI_RATES = [1, 2, 4];
            var PI_ACT_WORD = { open: 'The opening', setup: 'The setup', middle: 'The middle', turn: 'The turn', close: 'The close', end: 'The end' };
            var PI_SVG_PLAY = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 1.5l8 4.5-8 4.5z"/></svg>';
            var PI_SVG_PAUSE = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 1.5h2.6v9H2.5zM6.9 1.5h2.6v9H6.9z"/></svg>';
            var PI_SVG_START = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 1.5h1.8v9H2zM10.5 1.5v9L4.4 6z"/></svg>';
            var PI_SVG_END = '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M8.2 1.5H10v9H8.2zM1.5 1.5v9L7.6 6z"/></svg>';
            function piReplayable(s) { s = s || S.pilot || {}; return piFinished(s) && s.status !== 'preparing' && (((s.dial || {}).t || []).length > 1); }
            function piReplayOn() { var r = S.pilotReplay; return !!(r && r.on && S.pilot && r.sid === S.pilot.id); }
            function piVideoEl() { var v = el('dtwPiVideo'); return (v && v.src) ? v : null; }
            function piReplayPlaying() { var v = piVideoEl(); if (v) return !v.paused && !v.ended; return piReplayOn(); }
            function piReplayHalt(keepVideo) {
                var r = S.pilotReplay; if (!r) return;
                r.on = false;
                if (r.raf) { try { cancelAnimationFrame(r.raf); } catch (_) {} r.raf = 0; }
                if (!keepVideo) { var v = piVideoEl(); if (v && !v.paused) { try { v.pause(); } catch (_) {} } }
                piTransportPaint();
            }
            function piSeekTo(t) {
                var s = S.pilot || {}; t = Math.max(0, Math.min(piStopPoint(s), Number(t) || 0));
                var v = piVideoEl(); if (v) { try { if (Math.abs(v.currentTime - t) > 0.25) v.currentTime = t; } catch (_) {} }
                S.pilotClock.t = t;
                if (piReplayOn()) { S.pilotReplay.from = t; S.pilotReplay.t0 = Date.now(); }
                revealPilot(t);
            }
            window.dtwPilotReplay = function(on) {
                var s = S.pilot; if (!s || !piReplayable(s)) return;
                var r = S.pilotReplay; r.sid = s.id;
                if (on == null) on = !piReplayPlaying();
                if (!on) { piReplayHalt(); return; }
                var end = piStopPoint(s), t = Number(S.pilotClock.t || 0);
                if (t >= end - 0.05) t = 0;   // Replay at the end starts over
                r.on = true; r.from = t; r.t0 = Date.now();
                S.pilotClock.t = t;
                var v = piVideoEl();
                if (v) {   // the picture is the clock: its ticks reveal the room
                    try { v.playbackRate = r.rate; if (Math.abs(v.currentTime - t) > 0.25) v.currentTime = t; var p = v.play(); if (p && p.catch) p.catch(function() {}); } catch (_) {}
                    piTransportPaint(); return;
                }
                stopPilotClock(); S.pilotClock.slate = false;
                revealPilot(t);
                (function tick() {
                    if (!piReplayOn()) return;
                    var now = r.from + (Date.now() - r.t0) / 1000 * r.rate;
                    if (now >= end) { r.on = false; r.raf = 0; S.pilotClock.t = end; revealPilot(end); return; }
                    S.pilotClock.t = now; revealPilot(now);
                    r.raf = requestAnimationFrame(tick);
                })();
                piTransportPaint();
            };
            window.dtwPilotStep = function(ds) { if (!piReplayable()) return; piSeekTo(Number(S.pilotClock.t || 0) + Number(ds || 0)); };
            window.dtwPilotJump = function(where) { if (!piReplayable()) return; piSeekTo(where === 'end' ? piStopPoint() : 0); };
            window.dtwPilotRate = function() {
                var r = S.pilotReplay, i = PI_RATES.indexOf(Number(r.rate) || 1); r.rate = PI_RATES[(i + 1) % PI_RATES.length];
                var v = piVideoEl(); if (v) { try { v.playbackRate = r.rate; } catch (_) {} }
                if (piReplayOn()) { r.from = Number(S.pilotClock.t || 0); r.t0 = Date.now(); }
                piTransportPaint();
            };
            function piTransportHtml(s) {
                var end = piStopPoint(s), t = Math.max(0, Math.min(end, Number((S.pilotClock || {}).t || 0)));
                return '<div class="dtw-pi-controls dtw-pi-transport" id="dtwPiTransport" role="group" aria-label="Run the test back and forward">'
                    + '<span class="dtw-pi-note" id="dtwPiNote">Stopped at ' + esc(clockFmt(end)) + '. Run it back from any second.</span>'
                    + '<button type="button" class="dtw-pi-tb" onclick="dtwPilotJump(\'start\')" title="Back to the start (Home)" aria-label="Back to the start">' + PI_SVG_START + '</button>'
                    + '<button type="button" class="dtw-pi-tb" onclick="dtwPilotStep(-' + PI_STEP_S + ')" title="Back ten seconds. The left arrow key steps five; with shift, thirty." aria-label="Back ten seconds">&lsaquo; 10s</button>'
                    + '<button type="button" class="dtw-btn dtw-btn-primary dtw-pi-tplay" id="dtwPiReplayBtn" onclick="dtwPilotReplay()" title="Replay from here (space bar)">' + PI_SVG_PLAY + 'Replay</button>'
                    + '<button type="button" class="dtw-pi-tb" onclick="dtwPilotStep(' + PI_STEP_S + ')" title="Forward ten seconds. The right arrow key steps five; with shift, thirty." aria-label="Forward ten seconds">10s &rsaquo;</button>'
                    + '<button type="button" class="dtw-pi-tb" onclick="dtwPilotJump(\'end\')" title="To the end (End)" aria-label="To the end">' + PI_SVG_END + '</button>'
                    + '<button type="button" class="dtw-pi-tb rate" id="dtwPiRate" onclick="dtwPilotRate()" title="Replay speed">' + esc(String(S.pilotReplay.rate || 1)) + 'x</button>'
                    + '<span class="dtw-pi-clock" id="dtwPiClock">' + esc(clockFmt(t)) + '</span><span class="dtw-pi-tdur">/ ' + esc(clockFmt(end)) + '</span></div>';
            }
            function piTransportPaint() {
                var b = el('dtwPiReplayBtn'); if (!b) return;
                var playing = piReplayPlaying(), html = playing ? (PI_SVG_PAUSE + 'Pause') : (PI_SVG_PLAY + 'Replay');
                if (b.innerHTML !== html) { b.innerHTML = html; b.title = playing ? 'Pause (space bar)' : 'Replay from here (space bar)'; }
                var rt = el('dtwPiRate'); if (rt) { var txt = String(S.pilotReplay.rate || 1) + 'x'; if (rt.textContent !== txt) rt.textContent = txt; }
            }
            // ---- the scrubber under the dial: the played part in green, every reaction as a tick in its color, every question as a dot
            function piScrubHtml(s) {
                return '<div class="dtw-pi-scrub" id="dtwPiScrub" role="slider" tabindex="0" aria-label="Where you are in the test" aria-valuemin="0" aria-valuemax="' + Math.round(piStopPoint(s)) + '" aria-valuenow="0" title="Drag, or click, to any second">'
                    + '<div class="tr"><i id="dtwPiScrubDone"></i></div><div class="mk" id="dtwPiScrubMarks"></div><i class="hd" id="dtwPiScrubHead"></i></div>';
            }
            function paintPilotScrub(t) {
                var box = el('dtwPiScrub'); if (!box) return;
                var s = S.pilot || {}, dur = piDur(s), end = piStopPoint(s), tt = Math.max(0, Math.min(end, Number(t) || 0)), pct = (tt / dur * 100).toFixed(3) + '%';
                var marks = el('dtwPiScrubMarks'), key = [s.id, (s.events || []).length, (s.questions || []).length, dur.toFixed(1)].join('|');
                if (marks && marks.getAttribute('data-k') !== key) {
                    var html = '';
                    (s.events || []).forEach(function(e) { var et = Number(e.t || 0); if (!(et >= 0 && et <= dur)) return; html += '<i class="ev ' + esc(e.type || '') + '" style="left:' + (et / dur * 100).toFixed(3) + '%"></i>'; });
                    (s.questions || []).forEach(function(q) { var qt = Number(q.playhead_s); if (!isFinite(qt) || !String(q.q || '').trim()) return; html += '<i class="qm" style="left:' + (Math.min(dur, Math.max(0, qt)) / dur * 100).toFixed(3) + '%"></i>'; });
                    marks.innerHTML = html; marks.setAttribute('data-k', key);
                }
                var done = el('dtwPiScrubDone'); if (done) done.style.width = pct;
                var hd = el('dtwPiScrubHead'); if (hd) hd.style.left = pct;
                box.setAttribute('aria-valuenow', String(Math.round(tt))); box.setAttribute('aria-valuetext', clockFmt(tt) + ' of ' + clockFmt(end));
            }
            // ---- what was happening at the playhead
            function piIdxAt(ts, t) { var k = -1; for (var i = 0; i < (ts || []).length; i++) { if (Number(ts[i]) <= t + 0.05) k = i; else break; } return k; }
            function piNowFace(r, i) {
                var url = (r || {}).portrait_url || '', nm = titleCase((r || {}).name || '');
                return '<span class="dtw-pi-nowface" style="--hue:' + seatHue(r, i) + '">' + esc(initials(nm)) + (url ? '<img src="' + esc(thumb(url)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span>';
            }
            function piNowKey(s, t) {
                var ts = ((s || {}).dial || {}).t || [], k = piIdxAt(ts, t), n = 0, lo = t - PI_NOW_BACK_S;
                ((s || {}).events || []).forEach(function(e) { var et = Number(e.t || 0); if (et <= t + 0.05 && et >= lo) n++; });
                var sc = sceneAt(s, t);
                return [s.id, k, n, sc ? sc.id : '', ((s || {}).questions || []).length].join('|');
            }
            function piNowHtml(s, t) {
                var d = (s || {}).dial || {}, ts = d.t || [], vs = d.value || [], k = piIdxAt(ts, t);
                if (!ts.length) return '<div class="dtw-pi-fn">This test has no readings to run back through.</div>';
                var at = '<div class="at">At ' + esc(clockFmt(t)) + '</div>';
                if (k < 0) return '<div class="top">' + at + '</div><div class="dtw-pi-fn">Before the first reading, at ' + esc(clockFmt(ts[0])) + '.</div>';
                var sc = sceneAt(s, t), actWord = sc && sc.act ? (PI_ACT_WORD[String(sc.act)] || '') : '';
                var k0 = Math.max(0, piIdxAt(ts, t - PI_NOW_BACK_S));
                var val = Number(vs[k]), was = Number(vs[k0]), delta = (isFinite(val) && isFinite(was) && k0 < k) ? val - was : NaN;
                at = '<div class="at">At ' + esc(clockFmt(t)) + (actWord ? ' <span>&middot; ' + esc(actWord) + '</span>' : '') + (sc ? ' <span>&middot; scene ' + esc(String(Number(sc.id) + 1)) + '</span>' : '') + '</div>';
                var dial = '<div class="dial"><span class="v">' + (isFinite(val) ? val.toFixed(1) : '-') + '</span>'
                    + (isFinite(delta) && Math.abs(delta) >= 0.05 ? '<span class="d ' + (delta > 0 ? 'up' : 'dn') + '">' + (delta > 0 ? '+' : '') + delta.toFixed(1) + ' in the last half minute</span>' : '<span class="d">the room, 0 to 100</span>');
                piCohortSplits(s).forEach(function(c) { var cv = Number((c.value || [])[k]); if (!isFinite(cv)) return; dial += '<span class="grp"><i style="background:' + esc(c.color) + '"></i>' + esc(c.label) + ' <b>' + cv.toFixed(1) + '</b></span>'; });
                dial += '</div>';
                var scene = sc ? ('<div class="scene">' + (sc.caption ? '<div class="cap">' + esc(sc.caption) + '</div>' : '<div class="cap dtw-muted">No scene read at this second.</div>')
                    + (sc.dialogue ? '<div class="dlg">' + (sc.speaker ? '<b>' + esc(sc.speaker) + ':</b> ' : '') + '&ldquo;' + esc(sc.dialogue) + '&rdquo;</div>' : '') + '</div>') : '<div class="scene"><div class="cap dtw-muted">No scene read at this second.</div></div>';
                // the seats that moved most in the last half minute, on the test's own readings
                var roster = ((s.group || {}).roster || []), movers = [];
                if (k0 < k) roster.forEach(function(r, i) {
                    var ss = piSeatSeries(s, r.seat); if (!ss || (ss.t || []).length !== ts.length) return;
                    var a = Number(ss.v[k]), b = Number(ss.v[k0]); if (!isFinite(a) || !isFinite(b) || Math.abs(a - b) < 0.05) return;
                    movers.push({ r: r, i: i, d: a - b, v: a });
                });
                movers.sort(function(x, y) { return Math.abs(y.d) - Math.abs(x.d); });
                var mv = movers.slice(0, 4).map(function(m) {
                    return '<div class="dtw-pi-nowrow"><span class="circwrap">' + piNowFace(m.r, m.i) + '</span><span class="nm">' + esc(titleCase(m.r.name || '')) + '</span><span class="dl ' + (m.d > 0 ? 'up' : 'dn') + '">' + (m.d > 0 ? '+' : '') + m.d.toFixed(1) + '</span><span class="ln">now ' + m.v.toFixed(1) + '</span></div>';
                }).join('');
                var moversHtml = '<div><div class="dtw-pi-h">Moved most</div>' + (mv || '<div class="dtw-pi-fn">' + (k0 < k ? 'No seat moved in the last half minute.' : 'The first reading.') + '</div>') + '</div>';
                // what the seats said in the last half minute, newest first
                var lo = t - PI_NOW_BACK_S, bySeat = {};
                roster.forEach(function(r, i) { bySeat[Number(r.seat)] = { r: r, i: i }; });
                var said = (s.events || []).filter(function(e) { var et = Number(e.t || 0); return et <= t + 0.05 && et >= lo; });
                said.sort(function(a, b) { return Number(b.t || 0) - Number(a.t || 0); });
                var sd = said.slice(0, 5).map(function(e) {
                    var hit = bySeat[Number(e.seat)] || {}, r = hit.r || { seat: e.seat, name: e.name };
                    return '<div class="dtw-pi-nowrow"><span class="circwrap">' + piNowFace(r, hit.i || 0) + '</span><span class="txt"><span class="who"><span class="nm">' + esc(titleCase(r.name || e.name || '')) + '</span><span class="k">' + esc(clockFmt(e.t)) + ' &middot; ' + esc(PI_EVT[e.type] || e.type || '') + '</span></span>' + (e.line ? '<span class="ln">' + esc(e.line) + '</span>' : '') + '</span></div>';
                }).join('');
                var asked = (s.questions || []).filter(function(q) { var qt = Number(q.playhead_s); return isFinite(qt) && String(q.q || '').trim() && qt <= t + 0.75 && qt >= lo; });
                var askHtml = asked.length ? '<div class="ask"><b>Asked here:</b> ' + esc(asked[0].q) + (asked.length > 1 ? ' <span class="dtw-muted">and ' + (asked.length - 1) + ' more</span>' : '') + '<span class="dtw-muted"> The answers are in the room.</span></div>' : '';
                var saidHtml = '<div><div class="dtw-pi-h">Said in the last half minute</div>' + (sd || '<div class="dtw-pi-fn">Nothing said in the last half minute.</div>') + askHtml + '</div>';
                return '<div class="top">' + at + dial + '</div>' + scene + '<div class="cols">' + moversHtml + saidHtml + '</div>';
            }
            function paintPilotNow(t) {
                var box = el('dtwPiNow'); if (!box) return;
                var s = S.pilot || {}, key = piNowKey(s, t);
                if (box.getAttribute('data-k') === key) { var at = box.querySelector('.at'); if (at) { var sc = sceneAt(s, t), actWord = sc && sc.act ? (PI_ACT_WORD[String(sc.act)] || '') : ''; at.innerHTML = 'At ' + esc(clockFmt(t)) + (actWord ? ' <span>&middot; ' + esc(actWord) + '</span>' : '') + (sc ? ' <span>&middot; scene ' + esc(String(Number(sc.id) + 1)) + '</span>' : ''); } return; }
                box.innerHTML = piNowHtml(s, t); box.setAttribute('data-k', key);
            }
            // ---- the scrubber takes the pointer; the keys run the test when nothing else has them
            if (!window._piReplayWired) {
                window._piReplayWired = true;
                var piDrag = null;
                var piScrubAt = function(e, box) { var r = box.getBoundingClientRect(), f = (e.clientX - r.left) / Math.max(1, r.width); return Math.max(0, Math.min(1, f)) * piDur(S.pilot); };
                document.addEventListener('pointerdown', function(e) {
                    var box = e.target && e.target.closest ? e.target.closest('#dtwPiScrub') : null;
                    if (!box || !piReplayable(S.pilot) || (e.button != null && e.button !== 0)) return;
                    e.preventDefault();
                    piDrag = { box: box, was: piReplayPlaying() };
                    box.classList.add('drag');
                    try { box.setPointerCapture(e.pointerId); } catch (_) {}
                    if (piDrag.was) piReplayHalt();
                    piSeekTo(piScrubAt(e, box));
                    try { box.focus({ preventScroll: true }); } catch (_) {}
                }, true);
                document.addEventListener('pointermove', function(e) { if (!piDrag) return; piSeekTo(piScrubAt(e, piDrag.box)); }, true);
                var piDragEnd = function(e) { if (!piDrag) return; var d = piDrag; piDrag = null; d.box.classList.remove('drag'); try { d.box.releasePointerCapture(e.pointerId); } catch (_) {} if (d.was) dtwPilotReplay(true); };
                document.addEventListener('pointerup', piDragEnd, true);
                document.addEventListener('pointercancel', piDragEnd, true);
                document.addEventListener('keydown', function(e) {
                    if (!S.pilot || !piReplayable(S.pilot) || !isPiTab(S.tab) || !el('dtwPiTransport')) return;
                    if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
                    var tgt = e.target, tag = tgt && tgt.tagName ? tgt.tagName.toLowerCase() : '';
                    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (tgt && tgt.isContentEditable)) return;
                    var k = e.key;
                    if (k === ' ' || k === 'Spacebar') { if ((tag === 'button' || tag === 'a') && tgt.id !== 'dtwPiReplayBtn') return; e.preventDefault(); dtwPilotReplay(); }
                    else if (k === 'ArrowLeft') { e.preventDefault(); dtwPilotStep(-(e.shiftKey ? PI_KEY_BIG_S : PI_KEY_STEP_S)); }
                    else if (k === 'ArrowRight') { e.preventDefault(); dtwPilotStep(e.shiftKey ? PI_KEY_BIG_S : PI_KEY_STEP_S); }
                    else if (k === 'Home') { e.preventDefault(); dtwPilotJump('start'); }
                    else if (k === 'End') { e.preventDefault(); dtwPilotJump('end'); }
                }, true);
            }
            function playheadNow() {
                var v = el('dtwPiVideo');
                // a finished test keeps its own clock: the picture, when there is one, is moved to it (dtwPilotReplay, piSeekTo)
                if (v && v.src && isFinite(v.currentTime) && !piReplayable(S.pilot)) return v.currentTime;
                return Number((S.pilotClock || {}).t || 0);
            }
            function stopPilotClock() { if (S.pilotClock && S.pilotClock.raf) { try { cancelAnimationFrame(S.pilotClock.raf); } catch (_) {} S.pilotClock.raf = 0; } }
            function sceneAt(s, t) {
                var sc = (s || {}).scenes || [];
                for (var i = 0; i < sc.length; i++) { if (Number(sc[i].t_start) <= t && t < Number(sc[i].t_end)) return sc[i]; }
                return sc.length && t >= Number(sc[sc.length - 1].t_end) ? sc[sc.length - 1] : null;
            }
            function revealPilot(t) {
                S.pilotClock.t = t;
                drawPilotDial(t);
                paintPilotTicker(t);
                paintPilotRail(t);
                paintPilotLive(t);
                paintPilotSeats(t);
                paintPilotMarks(t);
                paintPilotSuggest(t);
                paintPilotScrub(t);
                paintPilotNow(t);
                piTransportPaint();
                var ck = el('dtwPiClock'); if (ck) ck.textContent = clockFmt(t);
                var slc = el('dtwPiSlateClock'); if (slc) slc.textContent = clockFmt(t);
                var num = el('dtwPiDialNum');
                var s = S.pilot || {}, d = s.dial || {}, ts = d.t || [], vs = d.value || [];
                var val = vs.length ? vs[0] : 0;
                for (var i = 0; i < ts.length; i++) { if (ts[i] <= t) val = vs[i]; }
                if (num) num.textContent = val ? Number(val).toFixed(1) : '';
                var now = el('dtwPiDialNow'); if (now) now.textContent = val ? Number(val).toFixed(1) : '-';
                var on = el('dtwPiOn');
                if (on) { var sc = sceneAt(s, t); var cap = sc ? (sc.caption || '') : ''; on.textContent = cap ? ('On screen: ' + cap) : ''; }
                var tg = document.querySelector('.dtw-pane.active .dtw-pi .dtw-pi-wm.a .tag'); if (tg) tg.textContent = wmText(s) + '  ' + clockFmt(t);
            }
            function drawPilotDial(upto) {
                var c = el('dtwPiDial'); if (!c) return;
                var s = S.pilot || {}, d = s.dial || {}, ts = d.t || [], vs = d.value || [];
                var w = c.clientWidth || 640, h = c.clientHeight || 132;
                if (c.width !== w) c.width = w; if (c.height !== h) c.height = h;
                var ctx = c.getContext('2d'); if (!ctx) return;
                ctx.clearRect(0, 0, w, h);
                var dur = piDur(s);
                function X(sec) { return (sec / dur) * w; }
                function Y(v) { return h - 10 - (Math.max(0, Math.min(100, v)) / 100) * (h - 20); }
                ctx.strokeStyle = 'rgba(233,232,225,0.12)'; ctx.lineWidth = 1;
                ctx.beginPath(); ctx.moveTo(0, Y(50)); ctx.lineTo(w, Y(50)); ctx.stroke();
                // probe marks: a faint tick where the room dipped or peaked
                (s.probes || []).forEach(function(p) { ctx.strokeStyle = p.kind === 'valley' ? 'rgba(183,179,216,0.35)' : 'rgba(199,242,62,0.35)'; ctx.beginPath(); ctx.moveTo(X(Number(p.t || 0)), 4); ctx.lineTo(X(Number(p.t || 0)), h - 4); ctx.stroke(); });
                function line(vals, color, width) {
                    if (!vals || !vals.length) return;
                    ctx.strokeStyle = color; ctx.lineWidth = width || 2; ctx.beginPath();
                    var started = false;
                    for (var i = 0; i < ts.length; i++) {
                        if (ts[i] > upto) break;
                        var yv = Number(vals[i]); if (!isFinite(yv)) continue;   // a frame this line has no reading for
                        var x = X(ts[i]), y = Y(yv);
                        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
                    }
                    if (started) ctx.stroke();
                }
                function lineT(tArr, vals, color, width) {
                    if (!tArr || !vals || !vals.length) return;
                    ctx.strokeStyle = color; ctx.lineWidth = width || 2; ctx.beginPath();
                    var started = false;
                    for (var i = 0; i < tArr.length && i < vals.length; i++) {
                        if (tArr[i] > upto) break;
                        var yv = Number(vals[i]); if (!isFinite(yv)) continue;
                        var x = X(tArr[i]), y = Y(yv);
                        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
                    }
                    if (started) ctx.stroke();
                }
                var view = piView();
                // live or scored, the three views draw from one source: the live frame readings while the room is watching (the
                // room line is the mean of every seat, a cohort line the mean of its seats), the scored series once the test is scored
                var cohorts = piCohortSplits(s), roster = ((s.group || {}).roster || []);
                var focusSeat = view.hover != null ? view.hover : view.seat;
                function seatsOf(cohortKey) { return piCohortSeats(s, cohortKey); }
                var cut = (view.mode === 'cohorts' && view.cohort) ? cohorts.filter(function(x) { return x.key === view.cohort; })[0] : null;
                // a finished test keeps the whole dial on screen (Jenna 2026-11-19): the part past the playhead is drawn quiet
                var replay = piReplayable(s);
                if (replay && upto < piStopPoint(s) - 0.05) {
                    var lineRest = function(vals, color, width) {
                        if (!vals || !vals.length) return;
                        ctx.strokeStyle = color; ctx.lineWidth = width || 1.2; ctx.beginPath();
                        var started = false;
                        for (var i = 0; i < ts.length; i++) {
                            if (ts[i] < upto) continue;
                            var yv = Number(vals[i]); if (!isFinite(yv)) continue;
                            var x = X(ts[i]), y = Y(yv);
                            if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
                        }
                        if (started) ctx.stroke();
                    };
                    ctx.save(); ctx.globalAlpha = 0.28;
                    if (cut) lineRest(cut.value, cut.color, 1.2);
                    else if (view.mode === 'cohorts') cohorts.forEach(function(c) { lineRest(c.value, c.color, 1.0); });
                    lineRest(vs, '#E9E8E1', 1.4);
                    ctx.restore();
                }
                if (view.mode === 'people') {
                    // every seat thin in its cohort color; the followed seat full and on top
                    roster.forEach(function(r, i) { if (Number(r.seat) === focusSeat) return; var ss = piSeatSeries(s, r.seat); if (ss) lineT(ss.t, ss.v, piAlpha(piSeatLineColor(s, r, i), focusSeat != null ? 0.22 : 0.55), 1.1); });
                    if (focusSeat != null) roster.forEach(function(r, i) { if (Number(r.seat) !== focusSeat) return; var ss = piSeatSeries(s, r.seat); if (ss) lineT(ss.t, ss.v, piSeatLineColor(s, r, i), 2.2); });
                } else if (cut) {
                    // one cohort cut out: its people thin, its line full, the rest gone
                    seatsOf(cut.key).forEach(function(r, i) { var ss = piSeatSeries(s, r.seat); if (ss) lineT(ss.t, ss.v, piAlpha(cut.color, Number(r.seat) === focusSeat ? 0.95 : 0.35), Number(r.seat) === focusSeat ? 2.0 : 1.0); });
                    line(cut.value, cut.color, 2.0);
                } else if (view.mode === 'cohorts') {
                    var hovKey = S.piCohortHover && S.piCohortHover.sid === s.id ? S.piCohortHover.key : '';
                    cohorts.forEach(function(c) { if (c.key !== hovKey) line(c.value, hovKey ? piAlpha(c.color, 0.5) : c.color, 1.6); });
                    if (hovKey) cohorts.forEach(function(c) { if (c.key === hovKey) line(c.value, c.color, 2.4); });
                }
                line(vs, '#E9E8E1', view.mode === 'people' ? 1.6 : 2.2);
                ctx.fillStyle = '#E9E8E1';
                var last = 0;
                for (var j = 0; j < ts.length; j++) { if (ts[j] <= upto) last = j; }
                if (vs.length) { ctx.beginPath(); ctx.arc(X(ts[last] || 0), Y(vs[last] || 0), 3.5, 0, Math.PI * 2); ctx.fill(); }
                if (replay) { var px = Math.round(X(Math.min(upto, dur))) + 0.5; ctx.strokeStyle = 'rgba(199,242,62,0.9)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(px, 2); ctx.lineTo(px, h - 2); ctx.stroke(); }
                piDrawBuckets(ctx, s, view, X, Y, upto, dur, w, h);
            }
            function paintPilotRail(t) {
                var rail = el('dtwPiRail'); if (!rail) return;
                var s = S.pilot || {}, ev = s.events || [], dur = Number((s.media || {}).runtime_s || 1) || 1;
                var marks = ev.map(function(e) {
                    var left = Math.max(0, Math.min(100, (Number(e.t || 0) / dur) * 100));
                    var seen = Number(e.t || 0) <= t + 0.05;
                    return '<button type="button" class="dtw-pi-mark ' + esc(e.type || '') + '" style="left:' + left.toFixed(2) + '%;opacity:' + (seen ? '1' : '0.25') + '" title="' + esc((PI_EVT[e.type] || e.type) + ' at ' + clockFmt(e.t) + '. ' + (e.line || '')) + '" onclick="dtwPilotSeek(' + Number(e.t || 0) + ')"></button>';
                }).join('');
                var ph = Math.max(0, Math.min(100, (t / dur) * 100));
                rail.innerHTML = marks + '<i class="dtw-pi-playhead" style="left:' + ph.toFixed(2) + '%"></i>';
            }
            function paintPilotLive(t) {
                var box = el('dtwPiLive'); if (!box) return;
                var s = S.pilot || {}, ev = s.events || [];
                var shown = ev.filter(function(e) { return Number(e.t || 0) <= t + 0.05; }).slice(-8);
                if (!shown.length) { box.innerHTML = '<div class="dtw-muted">The room is watching.</div>'; return; }
                box.innerHTML = shown.map(function(e) {
                    return '<div class="dtw-pi-voice"><span class="k">' + esc(clockFmt(e.t)) + '  ' + esc(PI_EVT[e.type] || e.type) + '  ' + esc(e.name || '') + '</span><div>' + esc(e.line || '') + '</div></div>';
                }).join('');
            }
            function probeChips(s) {
                var ps = (s || {}).probes || [];
                if (!ps.length) return '';
                return '<div class="dtw-pi-probes">' + ps.map(function(p) {
                    return '<button type="button" class="dtw-pi-probe ' + esc(p.kind || '') + '" title="' + esc(p.on_screen || '') + '" onclick="dtwPilotAskAt(' + Number(p.t || 0) + ', \'' + esc(jsq(p.q || '')) + '\')"><span class="c">' + esc(p.clock || clockFmt(p.t)) + '</span>' + esc(p.chip || 'Ask here') + '</button>';
                }).join('') + '</div>';
            }
            function bindPilotMedia() {
                var s = S.pilot; if (!s) return;
                var v = el('dtwPiVideo'), slate = el('dtwPiSlate');
                if (attachLiveMirror(s)) return;
                if (watchMedia(s) && !hasMedia(s)) { bindWatchLink(s); return; }
                if (v && hasMedia(s)) {
                    var wire = function(url) {
                        if (S.pilotClock.bound !== url) { v.src = url; S.pilotClock.bound = url; }
                        v.style.display = 'block';
                        if (slate) slate.style.display = 'none';
                        ensureWatermark(s);
                        wirePilotVideo(v);
                    };
                    var url = mediaUrl(s);
                    if (url) { wire(url); return; }
                    fetchMediaUrl(s).then(function(u) {
                        if (u) { wire(u); return; }
                        if (slate) { slate.style.display = 'flex'; var sNote = slate.querySelector('.s'); if (sNote && S.pilotMedia && S.pilotMedia.denied) sNote.textContent = S.pilotMedia.error || 'The cut plays only for the person who uploaded it. The read is here for everyone on the twin.'; }
                        S.pilotClock.bound = 'slate';
                    });
                } else if (slate) {
                    if (v) { v.removeAttribute('src'); v.style.display = 'none'; }
                    slate.style.display = 'flex';
                    S.pilotClock.bound = 'slate';
                    var sm = slate.querySelector('.s');
                    if (sm && (s.media || {}).purged_at) sm.textContent = 'The file was removed when the test finished. The read, the dial and the transcript stay.';
                }
            }
            function pilotProtectLine() { var it = ((S.howto || {}).items || []).filter(function(x) { return x.id === 'pilot'; })[0]; return it && it.protect ? String(it.protect) : 'How we protect your cut: it plays through links that expire after ten minutes and only for you, your name sits on every frame while it plays, and a switch on the test removes the file the moment the scorecard is final.'; }
            function piTabFor(mode) { var m = String(mode || 'pilot'); for (var t in PI_TABS) { if (PI_TABS[t].indexOf(m) >= 0) return t; } return 'pilot'; }
            function piTab() { return S.piTab && PI_HOST[S.piTab] ? S.piTab : 'pilot'; }
            function piHostEl() { return el(PI_HOST[piTab()] || 'dtwPiConsole'); }
            function piModeLabel(s) { return PI_MODE_LABEL[String((s || {}).mode || 'pilot')] || 'Pilot test'; }
            function piDefaultMode(tab) { return (PI_TABS[tab] || ['pilot'])[0]; }
            function piPrice(tab) {
                var items = ((S.howto || {}).items || []);
                if (GL_OPTIONS.indexOf(tab) >= 0) {
                    // the two Greenlight options carry their own price on the Greenlight How To item (2026-10-28)
                    var gl = items.filter(function(x) { return x.id === 'greenlight'; })[0];
                    var opt = ((gl || {}).options || []).filter(function(o) { return o.id === tab; })[0];
                    if (opt && opt.cost) return String(opt.cost);
                    return tab === 'script' ? '$3,500 a script read, charged on first Play' : '$2,500 a concept test, charged on first Play';
                }
                var id = tab === 'pilot' ? 'pilot' : tab;
                var it = items.filter(function(x) { return x.id === id; })[0];
                return it ? String(it.cost || '') : pilotPrice();
            }
            function renderPilotStart() {
                var tab = piTab(), mode = S.piMode && PI_TABS[tab].indexOf(S.piMode) >= 0 ? S.piMode : piDefaultMode(tab);
                S.piMode = mode;
                var others = (S.runs || []).filter(function(r) { return r.key && r.key !== S.key; }).map(function(r) {
                    return '<option value="' + esc(r.key) + '">' + esc(twinOptionLabel(r)) + '</option>';
                }).join('');
                var past = ((S.twin || {}).pilot_sessions || []).filter(function(s) { return piTabFor(s.mode) === tab; });
                var rows = past.map(function(s) {
                    var on = S.pilot && S.pilot.id === s.id;
                    var sc = s.scorecard || {}, hd = ((sc.battery || {}).headline) || {};
                    var st = s.status || 'draft';
                    var money = hd.total_positive_pct != null ? ('  total positive ' + Number(hd.total_positive_pct).toFixed(1) + '%') : (sc.overall_like != null ? '  dial ' + Number(sc.overall_like).toFixed(1) : '');
                    return '<div class="dtw-pi-rowwrap"><button type="button" class="dtw-pi-row' + (on ? ' on' : '') + '" onclick="dtwPilotPick(\'' + esc(s.id) + '\')"><div class="h">' + esc(s.title || 'Untitled test') + (s.cut_label && s.lineage ? ' <span class="dtw-muted">' + esc(s.cut_label) + '</span>' : '') + '</div><div class="m">' + esc(PI_MODE_LABEL[s.mode] || 'Pilot test') + '  ' + esc(st) + esc(money) + '  ' + esc(fmtWhen(s.updated_at || s.created_at)) + '</div></button>'
                        + '<button type="button" class="dtw-pi-rowdel" title="Delete this test" aria-label="Delete ' + esc(s.title || 'this test') + '" onclick="dtwPilotDeleteId(\'' + esc(s.id) + '\')">Delete</button>'
                        + '<button type="button" class="dtw-pi-rowdup" title="Duplicate this test" aria-label="Duplicate ' + esc(s.title || 'this test') + '" onclick="dtwPilotDuplicateId(\'' + esc(s.id) + '\')">Duplicate</button></div>';
                }).join('');
                var intro = {
                    pilot: 'Upload a pilot, movie or episode, or paste a watch link from any streamer. Prometheus moderates. The twins watch it scene by scene: a dial, laughs and loves at the second they happen in their own words, a question at any timestamp that knows what just played, and the scorecard studios use with the norm beside every number.',
                    trailer: 'Up to three trailers or spots, 15 seconds to three minutes, to the same room. Interest in the title before the trailer plays and after, the lift in definite interest, what they understood, the moments that sold it, who it is for, would they watch, would they recommend.',
                    titles: 'Up to six titles, or up to six pieces of key art, to the same room. Titles: appeal from the title alone, fit once the logline is read, how much it stands out, the preferred option with reasons, by quadrant. Key art: preference, what holds the eye, what it says the title is, the lift in interest.',
                    script: 'Paste or upload the script and the room reads it scene by scene on a reading clock, with a dial per scene and story, structure and character notes. Or upload a recorded table read and it runs like a pilot.',
                    concept: 'A logline, a synopsis, a one-sheet or deck text. Interest and definite interest, how much it stands out, who it is for, would they watch, the strongest element and the biggest doubt, each next to the concept norm and by quadrant.'
                }[tab];
                var modeSel = '';
                if (tab === 'pilot') {
                    modeSel = '<label class="dtw-field">What is this</label><select id="dtwPiMode" class="dtw-in" onchange="dtwPilotMode(this.value, true)"><option value="pilot"' + (mode === 'pilot' ? ' selected' : '') + '>A pilot</option><option value="feature"' + (mode === 'feature' ? ' selected' : '') + '>A feature</option><option value="episode"' + (mode === 'episode' ? ' selected' : '') + '>An episode after the pilot</option><option value="binge"' + (mode === 'binge' ? ' selected' : '') + '>A binge test across episodes</option></select>'
                        + '<div class="dtw-pi-togs"><label class="dtw-pi-switch"><input type="checkbox" id="dtwPiRough"> Rough cut (temp music, missing effects)</label>'
                        + '<label class="dtw-pi-switch"><input type="checkbox" id="dtwPiCritics"> Critics room (critics and journalists in the seats)</label></div>';
                } else if (tab === 'trailer') {
                    modeSel = '<label class="dtw-field">What is this</label><select id="dtwPiMode" class="dtw-in" onchange="dtwPilotMode(this.value, false)"><option value="trailer"' + (mode === 'trailer' ? ' selected' : '') + '>A trailer</option><option value="spot"' + (mode === 'spot' ? ' selected' : '') + '>A TV spot</option></select>';
                } else if (tab === 'titles') {
                    modeSel = '<label class="dtw-field">What is this</label><select id="dtwPiMode" class="dtw-in" onchange="dtwPilotMode(this.value, true)"><option value="title"' + (mode === 'title' ? ' selected' : '') + '>Titles (type up to six)</option><option value="key_art"' + (mode === 'key_art' ? ' selected' : '') + '>Key art (upload up to six images)</option></select>';
                }
                var stim = '';
                if (tab === 'pilot') {
                    stim = '<label class="dtw-field">The cut (mp4, mov or webm)</label><input id="dtwPiFile" class="dtw-in" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm">'
                        + (mode === 'binge' ? '<label class="dtw-field">More episodes (up to six in all)</label><input id="dtwPiFiles" class="dtw-in" type="file" multiple accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm">' : '')
                        + '<label class="dtw-field">Or paste a watch link (any streamer)</label><input id="dtwPiWatchUrl" class="dtw-in" placeholder="https://www.paramountplus.com/shows/video/... or Netflix, Hulu, YouTube, a file link" autocomplete="off">'
                        + '<div class="dtw-row2"><div><label class="dtw-field">Title if you do not have the file yet</label><input id="dtwPiCutTitle" class="dtw-in" placeholder="NCIS: New York, episode 1"></div><div><label class="dtw-field">Runtime (42:35 or minutes)</label><input id="dtwPiMins" class="dtw-in" inputmode="decimal" placeholder="42:35" title="The test stops on its own when the clock reaches this."></div></div>'
                        + (mode === 'episode' ? '<label class="dtw-field">The prior cut the room watched</label><select id="dtwPiPrior" class="dtw-in"><option value="">None</option>' + ((S.twin || {}).pilot_sessions || []).filter(function(s) { return s.scorecard; }).map(function(s) { return '<option value="' + esc(s.id) + '">' + esc(s.title) + '</option>'; }).join('') + '</select>' : '')
                        + '<label class="dtw-field">Cast (optional, one name per line or comma separated)</label><input id="dtwPiCast" class="dtw-in" placeholder="LL Cool J, Scott Caan, Jennifer Beals as Lena">'
                        + '<div class="dtw-pi-fn" style="margin-top:4px">The room recognizes the cast on screen and names them in what it says. Add the character after the name to hear that name too.</div>';
                } else if (tab === 'trailer') {
                    stim = '<div class="dtw-row2"><div><label class="dtw-field">Title</label><input id="dtwPiCutTitle" class="dtw-in" placeholder="Hurry Up Tomorrow"></div><div><label class="dtw-field">Runtime, seconds (if no file yet)</label><input id="dtwPiSecs" class="dtw-in" inputmode="numeric" placeholder="120"></div></div>'
                        + '<label class="dtw-field">Logline (what the room reads before the trailer plays)</label><input id="dtwPiLogline" class="dtw-in" placeholder="A chef returns to her hometown diner after a scandal.">'
                        + '<label class="dtw-field">Trailer A (mp4, mov or webm)</label><input id="dtwPiFile" class="dtw-in" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm">'
                        + '<label class="dtw-field">Or paste a watch link (any streamer)</label><input id="dtwPiWatchUrl" class="dtw-in" placeholder="https://www.youtube.com/watch?v=... or the trailer on any service" autocomplete="off">'
                        + '<label class="dtw-field">Trailer B and C (optional, side by side)</label><input id="dtwPiFiles" class="dtw-in" type="file" multiple accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm">';
                } else if (tab === 'titles') {
                    stim = '<label class="dtw-field">Logline</label><input id="dtwPiLogline" class="dtw-in" placeholder="A chef returns to her hometown diner after a scandal.">';
                    if (mode === 'title') {
                        stim += '<label class="dtw-field">The titles (up to six)</label><div class="dtw-pi-opts">' + [0, 1, 2, 3, 4, 5].map(function(i) { return '<input class="dtw-in dtw-pi-opt" placeholder="Title ' + String.fromCharCode(65 + i) + '">'; }).join('') + '</div>';
                    } else {
                        stim += '<label class="dtw-field">The art (jpg, png or webp, up to six)</label><input id="dtwPiFiles" class="dtw-in" type="file" multiple accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"><label class="dtw-field">Title</label><input id="dtwPiCutTitle" class="dtw-in" placeholder="Second Helping">';
                    }
                } else if (tab === 'script') {
                    stim = '<label class="dtw-field">Paste the script</label><textarea id="dtwPiText" class="dtw-in" rows="8" placeholder="INT. DINER - MORNING&#10;&#10;MAE wipes the counter..."></textarea>'
                        + '<label class="dtw-field">Or upload it (txt or pdf), or a recorded table read (mp4, mov or webm)</label><input id="dtwPiFile" class="dtw-in" type="file" accept=".txt,.pdf,.fountain,.fdx,.md,text/plain,application/pdf,video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm">'
                        + '<label class="dtw-field">Title</label><input id="dtwPiCutTitle" class="dtw-in" placeholder="Second Helping">';
                } else if (tab === 'concept') {
                    stim = '<label class="dtw-field">Title (optional)</label><input id="dtwPiCutTitle" class="dtw-in" placeholder="Second Helping">'
                        + '<label class="dtw-field">The concept (a logline is enough; a synopsis or one-sheet gives the room more)</label><textarea id="dtwPiText" class="dtw-in" rows="6" placeholder="A chef returns to her hometown diner after a scandal. She has one summer to win the town back."></textarea>';
                }
                var quotas = '<details class="dtw-pi-details"><summary class="dtw-muted">Recruit the room to quotas (optional)</summary><div class="dtw-row2"><div><label class="dtw-field">% under 35</label><input id="dtwPiQUnder" class="dtw-in" inputmode="numeric" placeholder="65"></div><div><label class="dtw-field">% women</label><input id="dtwPiQWomen" class="dtw-in" inputmode="numeric" placeholder="50"></div></div><div class="dtw-row2"><div><label class="dtw-field">% fans of the lead</label><input id="dtwPiQFans" class="dtw-in" inputmode="numeric" placeholder="40"></div><div><label class="dtw-field">Must have seen (how many of the comps)</label><input id="dtwPiQSeen" class="dtw-in" inputmode="numeric" placeholder="2"></div></div><label class="dtw-field">Comp titles (comma separated)</label><input id="dtwPiComps" class="dtw-in" placeholder="Flashdance, The L Word"></details>';
                var cutHead = { pilot: 'The cut', trailer: 'The trailers', titles: mode === 'key_art' ? 'The art' : 'The titles', script: 'The script', concept: 'The concept' }[tab] || 'The cut';
                // Saved setups (Jenna, 2026-11-03): the form kept under a name so it can be reopened and edited later
                var setups = ((S.twin || {}).pilot_setups || []).filter(function(x) { return (x.tab || 'pilot') === tab; });
                var srows = setups.map(function(x) {
                    var on = S.piSetupId === x.id;
                    return '<div class="dtw-pi-rowwrap"><button type="button" class="dtw-pi-row' + (on ? ' on' : '') + '" onclick="dtwPilotLoadSetup(\'' + esc(x.id) + '\')"><div class="h">' + esc(x.name || 'Untitled setup') + '</div><div class="m">' + esc(PI_MODE_LABEL[x.mode] || PI_TAB_LABEL[tab] || 'Setup') + (x.media && x.media.has_file ? '  cut: ' + esc(x.media.filename || x.file_name || 'attached') : (x.file_name ? '  file: ' + esc(x.file_name) : '')) + '  saved ' + esc(fmtWhen(x.updated_at || x.created_at)) + '</div></button>'
                        + '<button type="button" class="dtw-pi-rowdel" title="Delete this saved setup" aria-label="Delete ' + esc(x.name || 'this setup') + '" onclick="dtwPilotDeleteSetup(\'' + esc(x.id) + '\')">Delete</button>'
                        + '<button type="button" class="dtw-pi-rowdup" title="Duplicate this setup" aria-label="Duplicate ' + esc(x.name || 'this setup') + '" onclick="dtwPilotDuplicateSetup(\'' + esc(x.id) + '\')">Duplicate</button></div>';
                }).join('');
                var saved = '<div class="dtw-pi-h">Saved setups <span class="n">' + setups.length + '</span></div><div class="dtw-pi-list">' + (srows || '<p class="dtw-muted" style="margin:8px 0 0;font-size:12px">Fill in the form and press Save setup to keep it here. Come back any time and open it to edit, without typing it again.</p>') + '</div>';
                var tests = '<div class="dtw-pi-col side tests">' + saved + '<div class="dtw-pi-h">Past tests <span class="n">' + past.length + '</span></div><div class="dtw-pi-list">' + (rows || '<p class="dtw-muted" style="margin:8px 0 0;font-size:12px">No tests of this type on this twin yet. The first one lands here.</p>') + '</div></div>';
                var normSel = '<label class="dtw-field" for="dtwPiNorm">Compare to</label><select id="dtwPiNorm" class="dtw-in" required onchange="dtwPilotNormTouched()">' + piNormOptions(mode, '') + '</select><input type="hidden" id="dtwPiNormTouched" value="">'
                    + '<div class="dtw-pi-fn" id="dtwPiNormHint" style="margin-top:4px">Every score is read against this norm. The room suggests one once it has watched the cut; you can change it any time.</div>';
                var cut = '<div class="dtw-pi-col side cut"><div class="dtw-pi-h">' + esc(cutHead) + '</div>' + modeSel + normSel + stim + '</div>';
                var room = '<div class="dtw-pi-col side room"><div class="dtw-pi-h">The room' + (S.twin && twinName() ? ' <span class="n">on ' + esc(twinName()) + '</span>' : '') + '</div>'
                    + (S.twin ? '' : '<p class="dtw-p" style="margin-top:8px">Pick a twin on the Twins tab first, or build one.</p>')
                    + '<label class="dtw-field" for="dtwPiTitle">Name this test</label><input id="dtwPiTitle" class="dtw-in" placeholder="' + esc({ pilot: 'Season 2 pilot', trailer: 'Trailer A vs B', titles: 'Six titles', script: 'Second Helping, draft 3', concept: 'Second Helping' }[tab]) + '" maxlength="120" autocomplete="off">'
                    + '<label class="dtw-field">Add a second twin (optional; two audiences on one cut)</label><select id="dtwPiTwin2" class="dtw-in"><option value="">Just this twin</option>' + others + '</select>'
                    + '<label class="dtw-field">Questions this group answers</label><select id="dtwPiTier" class="dtw-in" onchange="piTierNote()">' + groupTierOptions() + '</select><div class="dtw-fg-price" id="dtwPiTierNote">' + esc(groupQuote(groupTierDefault()).replace(/^This group/, 'This test')) + '</div>'
                    + piCohortBuilderHtml()
                    + quotas
                    + (tab === 'pilot' || tab === 'trailer' || tab === 'script' ? '<label class="dtw-pi-switch"><input type="checkbox" id="dtwPiPurgeNew"> Remove the file when the test is done</label>' : '')
                    + '<div class="dtw-lab-run"><button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwPilotOpen()">Start the test</button><button type="button" class="dtw-btn" onclick="dtwPilotSaveSetup()" title="Keep this form under its name to come back to later">' + (S.piSetupId ? 'Save changes' : 'Save setup') + '</button><span class="dtw-price">' + esc(piPrice(tab)) + '</span></div>'
                    + '<div class="dtw-pi-fn" style="margin-top:6px">Save setup keeps everything typed here on this twin, under Saved setups on the left. Nothing is seated or charged until you start the test.</div>'
                    + (tab === 'pilot' || tab === 'trailer' || tab === 'titles' || tab === 'script' ? '<div class="dtw-pi-protect">' + esc(pilotProtectLine()) + '</div>' : '')
                    + '<div class="dtw-status" id="dtwPiStatus"></div></div>';
                var editing = '';
                if (S.piSetupId) {
                    var cur = setups.filter(function(x) { return x.id === S.piSetupId; })[0];
                    if (cur) {
                        editing = '<div class="dtw-pi-editing">Editing the saved setup <b>' + esc(cur.name || 'Untitled setup') + '</b><span class="sp"></span>'
                            + '<button type="button" class="lnk" onclick="dtwPilotSaveSetup(true)">Save as a new setup</button>'
                            + '<button type="button" class="lnk" onclick="dtwPilotCloseSetup()">Start a blank form</button></div>';
                    }
                }
                return '<div class="dtw-card"><div class="dtw-eyebrow">' + esc(PI_TAB_LABEL[tab]) + '</div><h3 style="margin-top:0.35rem;">' + esc({ pilot: 'Watch a cut with the room', trailer: 'Test a trailer with the room', titles: 'Test titles or key art with the room', script: 'Read a script with the room', concept: 'Put a concept to the room' }[tab]) + '</h3>'
                    + '<div class="dtw-sub" style="margin-bottom:0">' + esc(intro) + '</div>' + editing
                    + '<div class="dtw-pi-setup">' + tests + cut + room + '</div></div>';
            }
            function normCell(sc, metric, which) {
                var col = ((sc || {}).norm_columns || {})[metric] || {};
                var v = which === 'genre' ? col.genre_norm : col.norm;
                return v == null ? '' : Number(v).toFixed(1) + '%';
            }
            function pct1(v) { return v == null ? '' : Number(v).toFixed(1) + '%'; }
            // ---- the performance forecast on a scored test (Jenna 2026-11-03): what the episode would do if released,
            // how the season would hold, who it would bring to the service. The notes come through the server's output guard and
            // piReaderNotes below: nothing on the card says what the forecast was set against (Jenna 2026-11-05)
            function piPeopleShort(v) {
                var n = Number(v || 0);
                if (!isFinite(n) || n <= 0) return '-';
                if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
                if (n >= 1e4) return Math.round(n / 1e3) + 'K';
                return fmtN(n);
            }
            function piAbout(n) { return n ? ', about ' + fmtN(n) + ' people' : ''; }
            // the output guard on the page: a note that says calibrated, anchored, benchmarked, as aired, or names a ratings
            // service never renders (the server strips the same; this holds for a record read before the server did)
            var PI_CAL_RX = /calibrat|benchmark|\banchored\b|\banchors? (?:to|on|against)\b|\bas (?:it |they |originally )?aired\b|\baired (?:episodes?|figures?|numbers?|performance|audience)\b|\bactuals\b|\bnielsen\b|\bcomscore\b|\bsamba ?tv\b|\bvideoamp\b|\bluminate\b|\bparrot analytics\b|\bratings? (?:service|data|book|figures?|numbers?)\b|\blive ?\+|\bsame[- ]day ratings?\b|\bthe ledger\b/i;
            function piReaderNotes(list) { return (list || []).filter(function(n) { return n && !PI_CAL_RX.test(String(n)); }); }
            function piForecastSide(s) {
                var fc = (s || {}).forecast || {}; if (!fc.episode) return '';
                var ep = fc.episode, se = fc.series || {}, su = fc.subscriptions || {};
                var plat = su.platform || 'the service';
                var out = '<div class="dtw-pi-hr"></div><div class="dtw-pi-h">Forecast</div>'
                    + (ep.week_one_audience ? '<div class="dtw-pi-stat"><div class="v">' + esc(piPeopleShort(ep.week_one_audience)) + '</div><div class="k">Week-one audience if released</div></div>' : '')
                    + '<div class="dtw-pi-stat"><div class="v">' + esc(pct1(se.season_completion_pct)) + '</div><div class="k">Would finish the season</div></div>'
                    + '<div class="dtw-pi-stat"><div class="v">' + esc(pct1(se.second_screen_pct)) + '</div><div class="k">On a second screen during the episode</div></div>'
                    + '<div class="dtw-pi-stat"><div class="v">' + esc(pct1(su.subscribe_pct_of_nonsubscribers)) + '</div><div class="k">Of people without ' + esc(plat) + ' would subscribe for it' + esc(piAbout(su.subscribe_people)) + '</div></div>'
                    + '<div class="dtw-pi-stat"><div class="v">' + esc(pct1(su.reactivate_pct_of_dormant)) + '</div><div class="k">Of dormant accounts would come back for it' + esc(piAbout(su.reactivate_people)) + '</div></div>';
                var note = piReaderNotes(fc.notes)[0]; if (note) out += '<div class="dtw-pi-fn">' + esc(note) + '</div>';
                return out;
            }
            function piForecastFull(s) {
                var fc = (s || {}).forecast || {}; if (!fc.episode) return '';
                var ep = fc.episode, se = fc.series || {}, su = fc.subscriptions || {};
                var plat = su.platform || 'the service';
                var money = '<div class="dtw-pi-money fc">'
                    + (ep.week_one_audience ? '<div class="m"><div class="k">Week-one audience if released</div><div class="v">' + esc(piPeopleShort(ep.week_one_audience)) + '</div><div class="n">' + esc(piPeopleShort(ep.low)) + ' to ' + esc(piPeopleShort(ep.high)) + '. ' + esc(ep.definition || '') + '</div></div>' : '')
                    + '<div class="m"><div class="k">The season</div><div class="v">' + esc(pct1(se.season_completion_pct)) + '</div><div class="n">would finish the season; ' + esc(pct1(se.second_screen_pct)) + ' on a second screen during the episode.' + (se.season_average_audience ? ' Season average ' + esc(piPeopleShort(se.season_average_audience)) + ', finale ' + esc(piPeopleShort(se.finale_audience)) + '.' : '') + '</div></div>'
                    + '<div class="m"><div class="k">Would subscribe to ' + esc(plat) + ' for it</div><div class="v">' + esc(pct1(su.subscribe_pct_of_nonsubscribers)) + '</div><div class="n">of people without ' + esc(plat) + esc(piAbout(su.subscribe_people)) + '.</div></div>'
                    + '<div class="m"><div class="k">Would come back for it</div><div class="v">' + esc(pct1(su.reactivate_pct_of_dormant)) + '</div><div class="n">of dormant ' + esc(plat) + ' accounts' + esc(piAbout(su.reactivate_people)) + '.</div></div>'
                    + '</div>';
                var notes = piReaderNotes((fc.notes || []).concat(fc.board_notes || [])).map(function(n) { return '<p class="dtw-muted" style="margin-top:8px">' + esc(n) + '</p>'; }).join('');
                return '<div class="dtw-eyebrow" style="margin-top:18px">Forecast</div>' + money + notes;
            }
            function renderScorecard(s, brief) {
                var sc = s.scorecard || {}, bat = sc.battery || {}, hd = bat.headline || {};
                if (hd.total_positive_pct == null) return '';
                var genre = s.genre || '', ch = piNormChoice(s), gl = !!genre && !!ch.has_genre_norm;   // gl: the chosen norm has a column of its own
                var allW = esc(String(ch.all_label || 'All pilots')), genW = esc(String(ch.label || '')), cmpW = esc(piComparedLabel(ch)), which = gl ? 'genre' : 'all';
                // the comparison named for what it is, the honest lines once, then what the shares are of (Liz Huszarik 2026-11-05)
                var notes = piNormNotes(ch);
                var compared = '<p class="dtw-p" style="margin-top:8px">Compared to: <b>' + cmpW + '</b>.' + (notes.length ? ' ' + esc(notes.join(' ')) : '') + '</p>'
                    + '<p class="dtw-p dtw-muted" style="margin-top:6px">' + esc(piDenominator(s, sc)) + '</p>';
                var tl = piTallies(bat);
                var seatN = function(k) { return tl[k] ? ' <span class="dtw-muted">' + esc(tl[k]) + ' seats</span>' : ''; };
                var normN = function(metric) { var v = normCell(sc, metric, which); if (!v && metric === 'finish') return String(((sc.norm_columns || {}).finish || {}).note || 'No would-finish norm yet; it fills as tests are filed.'); return cmpW + ' ' + (v || '-') + (gl ? ', ' + allW.toLowerCase() + ' ' + (normCell(sc, metric, 'all') || '-') : ''); };
                var money = '<div class="dtw-pi-money">'
                    + '<div class="m"><div class="k">Total positive</div><div class="v">' + pct1(hd.total_positive_pct) + seatN('total_positive') + '</div><div class="n">Excellent + very good. ' + normN('total_positive') + '</div></div>'
                    + '<div class="m"><div class="k">Definitely recommend</div><div class="v">' + pct1(hd.definitely_recommend_pct) + seatN('definitely_recommend') + '</div><div class="n">' + normN('definitely_recommend') + '</div></div>'
                    + '<div class="m"><div class="k">Excellent</div><div class="v">' + pct1(hd.excellent_pct) + seatN('excellent') + '</div><div class="n">' + normN('excellent') + '</div></div>'
                    + (hd.finish_pct != null ? '<div class="m"><div class="k">Would finish</div><div class="v">' + pct1(hd.finish_pct) + seatN('finish') + '</div><div class="n">' + normN('finish') + '</div></div>' : '')
                    + '</div>';
                // the norm lines (the four measures), then the plain line that says the card moved with the room's own answers (Jenna 2026-11-04)
                var lines = piReaderNotes(sc.norm_lines).filter(function(l) { return notes.indexOf(l) < 0; }).slice(0, 4).concat(piReaderNotes(sc.reconcile_notes)).map(function(l) { return '<p class="dtw-p" style="margin-top:8px">' + esc(l) + '</p>'; }).join('');
                var metricFor = { overall: { a: 'excellent' }, recommend: { a: 'definitely_recommend' }, finish: { a: 'finish' }, tune_in: { a: 'tune_in' }, urgency: { a: 'urgency_opening' }, urgency_pilot: { a: 'urgency_opening' } };
                var trs = '';
                (bat.questions || []).forEach(function(q) {
                    trs += '<tr class="sub"><td colspan="' + (gl ? 6 : 5) + '"><b>' + esc(q.label || '') + '</b></td></tr>';
                    (q.rows || []).forEach(function(r) {
                        var m = (metricFor[q.id] || {})[r.id];
                        trs += '<tr' + (m ? ' class="money"' : '') + '><td>' + esc(r.label || '') + '</td><td>' + pct1(r.pct) + '</td><td class="n">' + fmtN(r.people) + '</td><td class="n">' + esc(r.tally || '') + '</td><td class="n">' + (m ? normCell(sc, m, which) : '') + '</td>' + (gl ? '<td class="n">' + (m ? normCell(sc, m, 'all') : '') + '</td>' : '') + '</tr>';
                    });
                    if (q.id === 'overall' && q.total_positive_pct != null) {
                        trs += '<tr class="money"><td>Total positive</td><td>' + pct1(q.total_positive_pct) + '</td><td class="n">' + fmtN(q.total_positive_people) + '</td><td class="n">' + esc(tl.total_positive || '') + '</td><td class="n">' + normCell(sc, 'total_positive', which) + '</td>' + (gl ? '<td class="n">' + normCell(sc, 'total_positive', 'all') + '</td>' : '') + '</tr>';
                    }
                });
                var table = '<table class="dtw-pi-table"><thead><tr><th>Question</th><th>Share</th><th>People</th><th>In the room</th><th>' + cmpW + ' norm</th>' + (gl ? '<th>' + allW + ' norm</th>' : '') + '</tr></thead><tbody>' + trs + '</tbody></table>';
                var qt = '';
                (bat.quadrants || []).forEach(function(qd) {
                    if (!qd.n && qd.key !== 'total') { qt += '<tr><td>' + esc(qd.label) + '</td><td class="n">0</td><td class="n"></td><td></td><td></td><td></td><td></td></tr>'; return; }
                    qt += '<tr' + (qd.key === 'total' ? ' class="money"' : '') + '><td>' + esc(qd.label) + '</td><td class="n">' + esc(qd.n) + '</td><td class="n">' + fmtN(qd.people) + '</td><td>' + pct1(qd.excellent_pct) + '</td><td>' + pct1(qd.total_positive_pct) + '</td><td>' + pct1(qd.definitely_recommend_pct) + '</td><td>' + pct1(qd.finish_pct) + '</td></tr>';
                });
                var na = (sc.norms || {}).all || {}, ng = (sc.norms || {}).genre || {};
                var pick = function(o, k) { return o && o[k] && o[k].value != null ? Number(o[k].value).toFixed(1) + '%' : ''; };
                qt += '<tr class="sub"><td>' + (gl ? allW : cmpW) + ' norm</td><td class="n"></td><td class="n"></td><td>' + pick(na, 'excellent') + '</td><td>' + pick(na, 'total_positive') + '</td><td>' + pick(na, 'definitely_recommend') + '</td><td>' + (pick(na, 'finish') || 'no norm yet') + '</td></tr>';
                if (gl && ng.total_positive && ng.total_positive.genre_used && ng.total_positive.genre_used !== 'all') qt += '<tr class="sub"><td>' + genW + ' norm</td><td class="n"></td><td class="n"></td><td>' + pick(ng, 'excellent') + '</td><td>' + pick(ng, 'total_positive') + '</td><td>' + pick(ng, 'definitely_recommend') + '</td><td>' + (pick(ng, 'finish') || 'no norm yet') + '</td></tr>';
                var quad = '<div class="dtw-eyebrow" style="margin-top:16px">By quadrant</div><table class="dtw-pi-table"><thead><tr><th>Group</th><th>Seats</th><th>People</th><th>Excellent</th><th>Total positive</th><th>Definitely recommend</th><th>Would finish</th></tr></thead><tbody>' + qt + '</tbody></table>';
                var dialReads = '<div class="dtw-eyebrow" style="margin-top:16px">The dial</div>'
                    + '<div class="dtw-pi-scores">'
                    + '<div class="dtw-pi-score"><div class="k">Dial average</div><div class="v">' + (sc.overall_like != null ? Number(sc.overall_like).toFixed(1) : '-') + '</div></div>'
                    + '<div class="dtw-pi-score"><div class="k">Favorite moment</div><div class="v" style="font-size:16px">' + esc((sc.favorite_moment || {}).clock || '') + '</div><div class="dtw-muted">' + esc((sc.favorite_moment || {}).line || '') + '</div></div>'
                    + '<div class="dtw-pi-score"><div class="k">Dead spot</div><div class="v" style="font-size:16px">' + esc((sc.dead_spot || {}).clock || '') + '</div><div class="dtw-muted">' + esc((sc.dead_spot || {}).line || '') + '</div></div>'
                    + '</div>'
                    + (piReaderNotes(sc.notes).map(function(n) { return '<p class="dtw-muted">' + esc(n) + '</p>'; }).join(''));
                if (brief) return compared + money + lines + dialReads;   // the library's inline open: the headline, the tables and the forecast stay on the test itself
                return '<div class="dtw-eyebrow" style="margin-top:18px">Scorecard</div>' + compared + money + lines + table + quad + dialReads + piForecastFull(s);
            }
            function renderGuide(s) {
                var g = s.guide || {}, qs = g.questions || [];
                if (!qs.length) return '';
                return '<div class="dtw-eyebrow" style="margin-top:16px">Moderator guide</div><div class="dtw-pi-guide">' + qs.map(function(q) {
                    return '<div class="g"><span class="c">' + esc(q.clock || clockFmt(q.t)) + '</span><span>' + esc(q.q || '') + (q.why ? ' <span class="dtw-muted">(' + esc(q.why) + ')</span>' : '') + '</span><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotAskAt(' + Number(q.t || 0) + ', \'' + esc(jsq(q.q || '')) + '\', \'' + esc(q.kind || '') + '\')">Ask</button></div>';
                }).join('') + (g.battery && g.battery.length ? '<div class="dtw-muted" style="margin-top:6px">At the end the room fills in the card: ' + esc(g.battery.join('; ')) + '.</div>' : '') + '</div>';
            }
            // ---- the control room (Jenna 2026-10-30): the seats carry the generated portrait and the person's latest line on the clock; a click on the portrait opens who they are (hover only follows the seat's line).
            function piSeatLine(s, r, t) {
                var ev = ((s || {}).events || []).filter(function(e) { return Number(e.seat) === Number(r.seat) && Number(e.t || 0) <= t + 0.05; });
                if (ev.length) { var e = ev[ev.length - 1]; return { line: e.line || (PI_EVT[e.type] || e.type || ''), lit: true, clock: clockFmt(e.t) }; }
                var bio = String(r.persona || '').trim();
                if (bio) { var m = bio.match(/^[^.!?]+[.!?]/); return { line: (m ? m[0] : bio).trim(), lit: false, clock: '' }; }
                return { line: r.city ? titleCase(r.city) : 'Seated', lit: false, clock: '' };
            }
            function piSeatBio(r) {
                var nm = titleCase(r.name || '');
                var head = '<h6>' + esc(nm) + ', ' + esc(r.age || '') + (r.city ? ', ' + esc(titleCase(r.city)) : '') + (r.segment ? ' <span class="cohort">' + esc(r.segment) + '</span>' : '') + '</h6>';
                var body = r.persona ? bioParas(r.persona) : '<p>' + esc(nm) + ' holds seat ' + esc(r.seat || '') + ' in this room. Who ' + (r.gender === 'M' ? 'he' : 'she') + ' is, in full, is written as the room watches.</p>';
                // the seat's latest thought at the playhead (a live room; 2026-11-03): the dial moves with it
                var th = (((S.pilot || {}).live || {}).thoughts || {})[String(r.seat)] || [], now = playheadNow(), last = null;
                for (var i = 0; i < th.length; i++) { if (Number(th[i][0]) <= now + 0.05) last = th[i]; }
                var thought = last ? '<p class="dtw-pi-thought"><span class="dtw-muted">' + esc(clockFmt(last[0])) + '</span> ' + esc(last[1]) + '</p>' : '';
                var stands = standsForLine(nm, (S.pilot || {}).group || {}, r);
                var earlier = earlierGroupsLine((S.pilot || {}).id, r.seat);
                return head + (stands ? '<p class="dtw-pi-stands">' + stands + '</p>' : '') + (earlier ? '<p class="dtw-pi-stands earlier">' + esc(earlier) + '</p>' : '') + thought + body;
            }
            function piSayHtml(sl) { return esc(sl.line); }
            function piRoomHasCohorts() {
                var seen = {}, n = 0;
                (((S.pilot || {}).group || {}).roster || []).forEach(function(r) { var k = String(r.segment || ''); if (k && !seen[k]) { seen[k] = 1; n++; } });
                return n > 1;
            }
            // ---- the room as a chat thread (Jenna, 2026-11-02: "you should be able to scroll in this chat thread and the
            // chats should wrap so you can see the whole thing"). Every remark up to the playhead, oldest first, each line in
            // full; the thread scrolls and follows the newest remark unless the reader has scrolled up to read back. Before
            // the first remark the rows are the seats themselves (who they are, in a line).
            function piThreadEvents(s, t) {
                var out = ((s || {}).events || []).filter(function(e) { return Number(e.t || 0) <= t + 0.05; });
                out.sort(function(a, b) { return (Number(a.t) || 0) - (Number(b.t) || 0); });
                return out;
            }
            // ---- Ask the room lands in this thread (Jenna, 2026-11-04: "have the ask the room show up in the panelist chat bar
            // instead of below ... just launch in to what the panelists say"). A question is a Prometheus line at the clock it
            // was asked (the words as typed, nothing else); each seat's answer is that seat's remark, in the order the seats
            // answered, landing as they answer; after the last seat one short Prometheus line carries the read. An answer that
            // lands while this page is watching takes the clock it landed at, so it sits at the foot of the thread; a question
            // already answered when the page opens sits under its own line.
            function piPosStore(s) {
                var sid = String((s || {}).id || ''), st = S.piAnsPos;
                if (!st || st.sid !== sid) st = S.piAnsPos = { sid: sid, pos: {}, fly: {} };
                return st;
            }
            function piItemPos(st, q, key, tq, t) {
                var k = String(q.id || '') + '|' + key;
                if (st.pos[k] == null) st.pos[k] = st.fly[String(q.id || '')] ? Math.max(tq, Number(t) || 0) : tq;
                return st.pos[k];
            }
            function piAnswersInOrder(q) {
                var ans = ((q || {}).answers || []).filter(function(a) { return a && a.seat != null && String(a.line || '').trim(); }).map(function(a, i) { return { a: a, i: i }; });
                ans.sort(function(x, y) {
                    var p = Number(x.a.answered_at), r = Number(y.a.answered_at);
                    p = isFinite(p) ? p : Infinity; r = isFinite(r) ? r : Infinity;
                    return (p - r) || (x.i - y.i);
                });
                return ans.map(function(x) { return x.a; });
            }
            function piThreadItems(s, t) {
                var items = [];
                piThreadEvents(s, t).forEach(function(e, i) { items.push({ kind: 'event', t: Number(e.t || 0), seq: i, e: e }); });
                var st = piPosStore(s);
                ((s || {}).questions || []).forEach(function(q, qi) {
                    if (!q || !String(q.q || '').trim()) return;
                    var tq = Number(q.playhead_s); if (!isFinite(tq)) tq = 0;
                    if (tq > t + 0.75) return;
                    var base = 100000 + qi * 1000, fly = qInFlight(q), clock = q.playhead_clock || clockFmt(tq);
                    if (fly) st.fly[String(q.id || '')] = true;
                    items.push({ kind: 'ask', t: tq, seq: base, q: q, clock: clock });
                    piAnswersInOrder(q).forEach(function(a, k) { items.push({ kind: 'answer', t: piItemPos(st, q, 's' + a.seat, tq, t), seq: base + 1 + k, q: q, a: a, clock: clock }); });
                    var rd = String(q.read_short || q.read || '').trim();
                    if (rd && !fly) items.push({ kind: 'read', t: piItemPos(st, q, 'read', tq, t), seq: base + 900, q: q, read: rd });
                });
                items.sort(function(a, b) { return (a.t - b.t) || (a.seq - b.seq); });
                return items;
            }
            function piThreadKey(s, t) {
                var items = piThreadItems(s, t), last = items[items.length - 1], k = items.length + '|';
                if (!last) return k;
                if (last.kind === 'event') return k + last.t + '|' + last.e.seat + '|' + String(last.e.line || '').length;
                if (last.kind === 'ask') return k + 'ask|' + last.q.id;
                if (last.kind === 'answer') return k + 'ans|' + last.q.id + '|' + last.a.seat + '|' + String(last.a.line || '').length;
                return k + 'read|' + last.q.id + '|' + last.read.length;
            }
            function piPromRowHtml(text, opts) {
                // a Prometheus line in the thread: the question as typed, or the short read; no seat behind it, so no card, no pin
                opts = opts || {};
                return '<div class="dtw-pi-seatrow remark prom' + (opts.cls ? ' ' + opts.cls : '') + (opts.now ? ' now' : '') + '"' + (opts.t != null && isFinite(Number(opts.t)) ? ' data-t="' + Number(opts.t) + '"' : '') + '>'
                    + '<span class="circ">' + MARK_SVG + '</span>'
                    + '<div class="txt"><div class="who"><span class="nm">Prometheus</span>' + (opts.clock ? '<span class="c">' + esc(opts.clock) + '</span>' : '') + '</div><div class="say">' + esc(text) + '</div></div></div>';
            }
            function piCohortColorFor(r) {
                // the color of the seat's cohort line on the dial (the legend uses the same), '' when the room has no cohorts
                var seg = String((r || {}).segment || '').replace(/\s+/g, ' ').trim().toLowerCase(); if (!seg) return '';
                var cs = piCohortSplits(S.pilot || {});
                for (var i = 0; i < cs.length; i++) {
                    var lab = String(cs[i].label || '').replace(/\s+/g, ' ').trim().toLowerCase(), key = String(cs[i].key || '').toLowerCase();
                    if (lab === seg || key === seg || key === seg.replace(/[^a-z0-9]+/g, '_') || key === 'cohort_' + seg.replace(/[^a-z0-9]+/g, '_')) return cs[i].color;
                }
                // a cohort the dial has no line for yet: a stable color after the cohorts' own
                var segs = {}; (((S.pilot || {}).group || {}).roster || []).forEach(function(x) { var k = String(x.segment || '').replace(/\s+/g, ' ').trim().toLowerCase(); if (k) segs[k] = 1; });
                var extra = Object.keys(segs).filter(function(k) { return !cs.some(function(c) { return String(c.label || '').replace(/\s+/g, ' ').trim().toLowerCase() === k; }); }).sort();
                var at = extra.indexOf(seg); if (at < 0) return '';
                return PI_COHORT_COLORS[(cs.length + at) % PI_COHORT_COLORS.length];
            }
            function piSeatRowHtml(r, i, sl, opts) {
                // a seat in the room (before the first remark) opens who they are on a click of its photo, never on hover
                // (Jenna 2026-11-04: "only have bios pop up if you click on a user's icon not just on hover"); hover only
                // follows the seat's line. A remark in the chat never opens the card, so its photo is not a button. The
                // card itself is one per window: piBioShow / piBioToggle.
                opts = opts || {};
                var hue = seatHue(r, i), url = r.portrait_url || '', nm = titleCase(r.name || ''), coh = piRoomHasCohorts() ? piCohortColorFor(r) : '';
                var pinned = piView().seat != null && piView().seat === Number(r.seat);
                return '<div class="dtw-pi-seatrow' + (sl.lit ? ' lit' : '') + (opts.now ? ' now' : '') + (opts.remark ? ' remark' : '') + (opts.answer ? ' ans' : '') + (coh ? ' coh' : '') + (pinned ? ' pinned' : '') + '"' + (opts.remark ? ' tabindex="0"' : '') + ' data-seat="' + Number(r.seat) + '"' + (opts.t != null && isFinite(Number(opts.t)) ? ' data-t="' + Number(opts.t) + '"' : '') + ' style="--hue:' + hue + (coh ? ';--coh:' + coh : '') + '">'
                    + '<span class="circ"' + (opts.remark ? '' : ' role="button" tabindex="0" aria-label="Who ' + esc(nm) + ' is"') + '>' + esc(initials(nm)) + (url ? '<img src="' + esc(thumb(url)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span>'
                    + '<div class="txt"><div class="who"><span class="nm">' + esc(nm) + ', ' + esc(r.age || '') + (piRoomHasCohorts() && r.segment ? ' <span class="cohort">' + esc(r.segment) + '</span>' : '') + '</span>' + (sl.clock ? '<span class="c">' + esc(sl.clock) + '</span>' : '') + '</div><div class="say">' + piSayHtml(sl) + '</div></div></div>';
            }
            function piChipHtml(r, i) {
                var hue = seatHue(r, i), url = r.portrait_url || '', nm = titleCase(r.name || ''), coh = piRoomHasCohorts() ? piCohortColorFor(r) : '';
                return '<div class="dtw-pi-seatrow dtw-pi-chip' + (coh ? ' coh' : '') + '" role="button" tabindex="0" data-seat="' + Number(r.seat) + '" style="--hue:' + hue + (coh ? ';--coh:' + coh : '') + '" aria-label="Who ' + esc(nm) + ' is">'
                    + '<span class="circ">' + esc(initials(nm)) + (url ? '<img src="' + esc(thumb(url)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span></div>';
            }
            function renderPilotSeats(s, t) {
                var roster = ((s || {}).group || {}).roster || [];
                var items = piThreadItems(s, t);
                if (!items.length) {
                    return '<div class="dtw-pi-thread">' + roster.map(function(r, i) { return piSeatRowHtml(r, i, piSeatLine(s, r, t)); }).join('') + '</div>';
                }
                var bySeat = {};
                roster.forEach(function(r, i) { bySeat[Number(r.seat)] = { r: r, i: i }; });
                var last = items[items.length - 1];
                var strip = '<div class="dtw-pi-strip">' + roster.map(function(r, i) { return piChipHtml(r, i); }).join('') + '</div>';
                var rows = items.map(function(it) {
                    if (it.kind === 'event') {
                        var e = it.e, hit = bySeat[Number(e.seat)] || {};
                        var r = hit.r || { seat: e.seat, name: e.name, age: e.age, city: e.city, gender: e.gender, segment: e.segment };
                        return piSeatRowHtml(r, hit.i || 0, { line: e.line || (PI_EVT[e.type] || e.type || ''), lit: true, clock: clockFmt(e.t) }, { now: it === last, remark: true, t: it.t });
                    }
                    if (it.kind === 'ask') return piPromRowHtml(it.q.q, { cls: 'ask', clock: it.clock, now: it === last, t: it.t });
                    if (it.kind === 'answer') {
                        var a = it.a, ah = bySeat[Number(a.seat)] || {};
                        var ar = ah.r || { seat: a.seat, name: a.name };
                        return piSeatRowHtml(ar, ah.i || 0, { line: a.line, lit: true, clock: it.clock }, { now: it === last, remark: true, answer: true, t: it.t });
                    }
                    return piPromRowHtml(it.read, { cls: 'read', now: it === last, t: it.t });
                }).join('');
                return strip + '<div class="dtw-pi-thread">' + rows + '</div>';
            }
            function piThreadFollow(box) {
                var th = box && box.querySelector('.dtw-pi-thread'); if (!th) return;
                th.scrollTop = th.scrollHeight;
                var want = th.scrollTop;
                // once more after the fonts and portraits settle, unless the reader has scrolled in the meantime
                setTimeout(function() { if (th.isConnected && Math.abs(th.scrollTop - want) < 2) th.scrollTop = th.scrollHeight; }, 320);
            }
            function paintPilotSeats(t) {
                var s = S.pilot || {}, box = el('dtwPiSeats');
                if (box) {
                    var key = piThreadKey(s, t);
                    if (box.getAttribute('data-k') !== key) {
                        var th = box.querySelector('.dtw-pi-thread');
                        // the reader stays where they scrolled to; at the end of the thread it follows the newest remark
                        var pinned = !th || !box.getAttribute('data-k') || (th.scrollHeight - th.scrollTop - th.clientHeight) < 28;
                        var keep = (th && !pinned) ? th.scrollTop : null;
                        box.innerHTML = renderPilotSeats(s, t);
                        box.setAttribute('data-k', key);
                        piMinuteApply(box, false);
                        if (pinned) piThreadFollow(box);
                        else { var th2 = box.querySelector('.dtw-pi-thread'); if (th2 && keep != null) th2.scrollTop = keep; }
                    } else if (!((s.events || []).length)) {
                        // the seats before the first remark: the bio line fills in as the personas are written
                        ((s.group || {}).roster || []).forEach(function(r) {
                            var row = box.querySelector('.dtw-pi-seatrow[data-seat="' + Number(r.seat) + '"]'); if (!row) return;
                            var sl = piSeatLine(s, r, t), say = row.querySelector('.say'), html = piSayHtml(sl);
                            if (say && say.innerHTML !== html) say.innerHTML = html;
                        });
                    }
                }
                var ac = el('dtwPiAskClock'); if (ac) ac.textContent = clockFmt(t);
            }
            function paintPilotPortraits() {
                var s = S.pilot || {}, box = el('dtwPiSeats'); if (!box) return;
                ((s.group || {}).roster || []).forEach(function(r) {
                    box.querySelectorAll('.dtw-pi-seatrow[data-seat="' + Number(r.seat) + '"]').forEach(function(row) {
                        var circ = row.querySelector('.circ');
                        if (circ && r.portrait_url && !circ.querySelector('img')) { var img = document.createElement('img'); img.alt = ''; img.loading = 'lazy'; img.onerror = function() { try { img.remove(); } catch (_) {} }; img.src = thumb(r.portrait_url); circ.appendChild(img); }
                    });
                });
                piBioRefresh();
            }
            // ---- the who-they-are card (Jenna 2026-11-04: "if you have the people toggle on and hover over a line it should
            // show you who the person is and their bio. but if you hover over the person here in chat it should NOT show you
            // the bio"; then, the same day: "only have bios pop up if you click on a user's icon not just on hover"). One card
            // per window, filled from the roster as it opens. In the room (the grid, or the seats before the first remark) a
            // click on the seat's photo opens it beside the seat and it stays (stuck) until the same photo is clicked again, a
            // click lands outside the card, Escape is pressed, or another photo is clicked; hover only follows the seat's line.
            // A seat's own line on the dial still opens it by the pointer on hover, and that card leaves with the pointer. A
            // remark in the chat only follows the line. It is placed on the viewport (the thread scrolls, and a scrolling box
            // clips anything positioned inside it); a hover card stays a moment after the pointer leaves, so it can be scrolled.
            var PI_BIO = { timer: 0, doc: null, seat: null, over: false, stuck: false };
            function piSeatByNo(seatNo) {
                var n = Number(seatNo), roster = (((S.pilot || {}).group || {}).roster || []);
                for (var i = 0; i < roster.length; i++) { if (Number(roster[i].seat) === n) return { r: roster[i], i: i }; }
                return null;
            }
            function piBioCard(doc) {
                doc = doc || document;
                var card = null; try { card = doc.getElementById('dtwPiBioCard'); } catch (_) { return null; }
                if (card) return card;
                var host = doc.getElementById('digitalTwinsIQView') || doc.body; if (!host) return null;
                card = doc.createElement('div'); card.id = 'dtwPiBioCard'; card.className = 'dtw-pi-biocard'; card.setAttribute('role', 'tooltip');
                card.addEventListener('mouseenter', function() { PI_BIO.over = true; piBioHold(); if (PI_BIO.seat != null) piHoverSeat(PI_BIO.seat); });
                card.addEventListener('mouseleave', function(e) { PI_BIO.over = false; if (!piRowOf(e.relatedTarget)) piHoverSeat(null); piBioRelease(); });
                host.appendChild(card);
                return card;
            }
            function piBioOn(doc) { var c = null; try { c = (doc || document).getElementById('dtwPiBioCard'); } catch (_) {} return !!(c && c.classList.contains('on')); }
            function piBioShow(seatNo, doc, at) {
                // at: {rect} the seat's box in the room, or {x, y, avoid} the pointer on the dial and the dial's box, in that window's viewport
                doc = doc || document;
                var hit = piSeatByNo(seatNo), card = piBioCard(doc); if (!hit || !card) return false;
                piBioHold();
                if (PI_BIO.doc && PI_BIO.doc !== doc) piBioHide(PI_BIO.doc);   // one card open at a time, page or popped-out room
                var n = Number(seatNo), same = card.getAttribute('data-seat') === String(n) && card.classList.contains('on'), keep = same ? card.scrollTop : 0;
                card.innerHTML = piSeatBio(hit.r);   // fresh each time: the seat's latest thought at the playhead rides in it
                card.setAttribute('data-seat', String(n));
                card.style.setProperty('--hue', seatHue(hit.r, hit.i));
                var coh = piRoomHasCohorts() ? piCohortColorFor(hit.r) : '';
                if (coh) card.style.setProperty('--coh', coh); else card.style.removeProperty('--coh');
                card.classList.add('on');
                card.scrollTop = keep;
                PI_BIO.doc = doc; PI_BIO.seat = n;
                piBioPlace(card, doc.defaultView || window, at);
                return true;
            }
            function piBioPlace(card, win, at) {
                var w = Math.min(320, Math.max(200, win.innerWidth - 24));
                card.style.width = w + 'px';
                var h = card.offsetHeight || 340, left, top;
                if (at && at.rect) {
                    // beside a seat in the room: to its left where there is room, under it on a narrow screen
                    var r = at.rect;
                    if (win.innerWidth <= 860 || r.left - w - 10 < 8) { left = r.left + 46; top = r.bottom + 4; }
                    else { left = r.left - w - 10; top = r.top - 6; }
                } else {
                    // by the pointer on the dial, centered on it and under the dial itself (above it when the dial sits low on
                    // the screen), so no line is covered; with no dial box to clear, just beside the pointer
                    var x = Number((at || {}).x || 0), y = Number((at || {}).y || 0), av = (at || {}).avoid, gap = 10;
                    left = x - w / 2;
                    if (av) top = (av.bottom + gap + h <= win.innerHeight - 8 || av.top - gap - h < 8) ? av.bottom + gap : av.top - gap - h;
                    else top = (y + 16 + h <= win.innerHeight - 8) ? y + 16 : y - 16 - h;
                }
                left = Math.max(8, Math.min(left, win.innerWidth - w - 8));
                top = Math.max(8, Math.min(top, win.innerHeight - h - 8));
                card.style.left = left + 'px'; card.style.top = top + 'px';
            }
            function piBioHide(doc) {
                var docs = doc ? [doc] : [document].concat(PI_BIO.doc && PI_BIO.doc !== document ? [PI_BIO.doc] : []);
                docs.forEach(function(d) { try { var c = d.getElementById('dtwPiBioCard'); if (c) { c.classList.remove('on'); c.removeAttribute('data-seat'); } } catch (_) {} });
                if (!doc || doc === PI_BIO.doc) { PI_BIO.doc = null; PI_BIO.seat = null; PI_BIO.over = false; PI_BIO.stuck = false; }
            }
            function piBioHold() { if (PI_BIO.timer) { clearTimeout(PI_BIO.timer); PI_BIO.timer = 0; } }
            function piBioRelease(ms) {
                piBioHold();
                if (PI_BIO.over || PI_BIO.stuck) return;   // the pointer is on the card itself, or a click opened the card and it waits to be closed
                PI_BIO.timer = setTimeout(function() { PI_BIO.timer = 0; if (!PI_BIO.over) piBioHide(); }, ms == null ? 160 : ms);
            }
            function piBioRefresh() {
                // a persona that finishes while its card is open lands in the card
                if (PI_BIO.seat == null || !PI_BIO.doc) return;
                var card = null; try { card = PI_BIO.doc.getElementById('dtwPiBioCard'); } catch (_) { return; }
                var hit = piSeatByNo(PI_BIO.seat); if (card && hit && card.classList.contains('on')) { var keep = card.scrollTop; card.innerHTML = piSeatBio(hit.r); card.scrollTop = keep; }
            }
            // Portraits and the written personas finish after the room is seated: re-read the test once or twice and fill the seats in place (a playing cut is never re-rendered).
            function schedulePilotPortraits() {
                (S.pilotPortraitTimers || []).forEach(function(x) { try { clearTimeout(x); } catch (_) {} });
                S.pilotPortraitTimers = [];
                var s = S.pilot; if (!s || !S.key) return;
                var roster = (s.group || {}).roster || [];
                if (!roster.length || roster.every(function(r) { return !!r.portrait_url && !!r.persona; })) return;
                var sid = s.id, key = S.key;
                [20000, 75000].forEach(function(ms) {
                    S.pilotPortraitTimers.push(setTimeout(async function() {
                        if (!S.pilot || S.pilot.id !== sid || S.key !== key) return;
                        try {
                            var r = await apiRaw('/' + encodeURIComponent(key) + '/pilot/' + encodeURIComponent(sid), { method: 'GET' });
                            if (!r.ok || !r.data || !r.data.session) return;
                            var fresh = ((r.data.session.group || {}).roster || []), mine = (S.pilot.group || {}).roster || [], changed = false;
                            fresh.forEach(function(f) {
                                mine.forEach(function(m) {
                                    if (Number(m.seat) !== Number(f.seat)) return;
                                    if (f.portrait_url && m.portrait_url !== f.portrait_url) { m.portrait_url = f.portrait_url; changed = true; }
                                    if (f.persona && m.persona !== f.persona) { m.persona = f.persona; changed = true; }
                                });
                            });
                            if (changed) { paintPilotPortraits(); paintPilotSeats(playheadNow()); }
                        } catch (_) {}
                    }, ms));
                });
            }
            // ---- the room's moments: one pill per place the dial peaked or dipped, named by what the seats did there. Click: jump the cut there and put the question in the box.
            function piMomentLabel(s, p) {
                var t = Number(p.t || 0), counts = {};
                ((s || {}).events || []).forEach(function(e) { if (Math.abs(Number(e.t || 0) - t) <= 15 && e.type) counts[e.type] = (counts[e.type] || 0) + 1; });
                var best = '', n = 0;
                Object.keys(counts).forEach(function(k) { if (counts[k] > n) { n = counts[k]; best = k; } });
                if (best) return PI_EVT[best] || best;
                return p.kind === 'valley' ? 'The room dipped' : 'The room rose';
            }
            function renderPilotMarks(s, t) {
                var ps = ((s || {}).probes || []).slice().sort(function(a, b) { return Number(a.t || 0) - Number(b.t || 0); }).slice(0, 6);
                if (!ps.length) return '';
                return ps.map(function(p) {
                    var pt = Number(p.t || 0);
                    return '<button type="button" class="dtw-pi-mk ' + esc(p.kind || '') + (pt <= t + 0.05 ? ' seen' : '') + '" data-t="' + pt + '" title="' + esc(p.on_screen || '') + '" onclick="dtwPilotMoment(' + pt + ', \'' + esc(jsq(p.q || '')) + '\')"><span class="c">' + esc(p.clock || clockFmt(pt)) + '</span>' + esc(piMomentLabel(s, p)) + '</button>';
                }).join('');
            }
            function paintPilotMarks(t) {
                var box = el('dtwPiMarks'); if (!box) return;
                box.querySelectorAll('.dtw-pi-mk').forEach(function(b) { b.classList.toggle('seen', Number(b.getAttribute('data-t') || 0) <= t + 0.05); });
            }
            // Inline handlers run in the page scope, not this module's: these three reach S for them (the old inline S.* calls threw).
            window.dtwPilotNew = function() { S.pilot = null; renderPilotConsole(); };
            // Exit test (Jenna, 2026-11-02: "you need to be able to exit the test here"). Leaves the test and
            // returns to the Pilot Testing start page, where every test on the twin is listed. A test still
            // playing cannot run without this page, so it is stopped first and the room scores what it watched;
            // a test mid-scoring keeps scoring and opens with its scorecard when that lands.
            window.dtwPilotClose = async function() {
                var s = S.pilot, title = (s && s.title) || 'The test';
                stopPilotClock(); S.pilotClock.slate = false;
                stopLiveMirror();
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); } catch (_) {} }
                if (S.pilotQPoll) { try { clearTimeout(S.pilotQPoll); } catch (_) {} S.pilotQPoll = null; }
                if (S.pilotPrep && S.pilotPrep.timer) { try { clearTimeout(S.pilotPrep.timer); } catch (_) {} S.pilotPrep.timer = null; }
                var note = '';
                if (s && S.key && s.status === 'playing') {
                    try {
                        var d = await api('/' + S.key + '/pilot/' + s.id + '/stop', { method: 'POST', body: '{}' });
                        if (d.session) adoptPilot(d.session);
                        note = d.finishing ? ('Stopped ' + title + '. The room is scoring what it watched; open it again for the scorecard.')
                                           : ('Stopped ' + title + '. The scorecard is in.');
                    } catch (e) { note = 'Left ' + title + '. ' + (e && e.message ? e.message : ''); }
                } else if (s && s.status === 'preparing') {
                    note = title + ' is still scoring. Open it again for the scorecard when that lands.';
                }
                S.pilot = null; S.pilotMedia = null; S.pilotCompare = null;
                renderPilotConsole();
                if (note) fgToast(note);
            };
            window.dtwPilotMode = function(v, rerender) { S.piMode = v; if (rerender) renderPilotConsole(); };
            window.dtwPilotKindSel = function(v) { S.pilotKind = v || ''; };
            window.dtwPilotMoment = function(t, q) {
                dtwPilotSeek(Number(t || 0));
                var inp = el('dtwPiQ'); if (inp && q) { inp.value = q; try { inp.focus(); } catch (_) {} }
            };
            // ---- two suggested questions for this second: the guide's next one and the nearest moment's
            function piSuggestions(s, t) {
                var out = [], seen = {};
                function add(q, kind) { q = String(q || '').trim(); if (!q || seen[q.toLowerCase()]) return; seen[q.toLowerCase()] = 1; out.push({ q: q, kind: kind || '' }); }
                var gq = (((s || {}).guide || {}).questions || []).slice().sort(function(a, b) { return Number(a.t || 0) - Number(b.t || 0); });
                var next = gq.filter(function(q) { return Number(q.t || 0) >= t - 5; })[0] || gq[gq.length - 1];
                if (next) add(next.q, next.kind);
                var ps = ((s || {}).probes || []).slice().sort(function(a, b) { return Math.abs(Number(a.t || 0) - t) - Math.abs(Number(b.t || 0) - t); });
                if (ps[0]) add(ps[0].q, '');
                var qs = (s || {}).questions || [], last = qs[qs.length - 1];
                if (out.length < 2 && last && (last.probes_next || [])[0]) add(last.probes_next[0], '');
                return out.slice(0, 2);
            }
            function renderPilotSuggest(s, t) {
                return piSuggestions(s, t).map(function(x) { return '<button type="button" class="dtw-pi-probe" onclick="dtwPilotSuggest(\'' + esc(jsq(x.q)) + '\', \'' + esc(x.kind || '') + '\')">' + esc(x.q) + '</button>'; }).join('');
            }
            function paintPilotSuggest(t) {
                var box = el('dtwPiSugg'); if (!box) return;
                var html = renderPilotSuggest(S.pilot || {}, t);
                if (box.innerHTML !== html) box.innerHTML = html;
            }
            window.dtwPilotSuggest = function(q, kind) {
                var inp = el('dtwPiQ'); if (inp) { inp.value = q || ''; try { inp.focus(); } catch (_) {} }
                S.pilotKind = kind || '';
                var sel = el('dtwPiKindSel'); if (sel) sel.value = S.pilotKind;
            };
            // ---- the thread: the latest question in full, the earlier ones folded
            // ---- under the ask box: only the controls, the progress line while a question runs, and the questions asked,
            // folded (Jenna, 2026-11-04). The question and the answers themselves are in the chat above; the record of a
            // question (the split with the tally, the read, the voices, what the room knew, what people were saying) opens
            // from the list, in the same shape Focus Group IQ keeps for a question.
            function piQClock(q) { return (q || {}).playhead_clock || ((q || {}).playhead_s != null ? clockFmt(q.playhead_s) : ''); }
            function piBycoHtml(q) {
                if (!(q.read && Array.isArray(q.by_segment) && q.by_segment.length > 1 && piRoomHasCohorts())) return '';
                var bits = q.by_segment.map(function(b) {
                    var top = (b.distribution || []).slice().sort(function(x, y) { return (Number(y.pct) || 0) - (Number(x.pct) || 0); })[0];
                    var v = (b.yes_pct != null) ? (Math.round(Number(b.yes_pct)) + '% yes') : (b.average != null ? (Number(b.average).toFixed(1) + ' average') : (top ? (Math.round(Number(top.pct) || 0) + '% ' + String(top.label || '')) : ''));
                    return v ? '<b>' + esc(b.segment || '') + '</b> ' + esc(v) : '';
                }).filter(Boolean);
                return bits.length ? '<div class="dtw-pi-byco">By cohort: ' + bits.join(' \u00b7 ') + '</div>' : '';
            }
            function renderPilotThread(s) {
                var qs = ((s || {}).questions || []).filter(function(q) { return q && String(q.q || '').trim(); }), out = '';
                qs.forEach(function(q) {
                    if (qInFlight(q)) out += '<div class="dtw-pi-working" data-qid="' + esc(q.id || '') + '"><span class="dtw-fg-pulse"></span><span>' + esc(cap(stageWords(q))) + '</span></div>';
                });
                var last = qs[qs.length - 1];
                if (last && qFailed(last)) out += '<div class="dtw-pi-probes"><button type="button" class="dtw-pi-probe" onclick="dtwPilotAskAt(' + Number(last.playhead_s || 0) + ', \'' + esc(jsq(last.q || '')) + '\')">Ask again</button></div>';
                var done = qs.filter(function(q) { return !qInFlight(q); }).reverse();
                if (done.length) {
                    out += '<details class="dtw-pi-more" data-fold="earlier" data-sid="' + esc((s || {}).id || '') + '"' + (piFoldOpen(s, 'earlier') ? ' open' : '') + ' ontoggle="dtwPilotFoldToggle(this)"><summary>Earlier questions <span class="n">' + done.length + '</span></summary><div class="dtw-pi-qlist">'
                        + done.map(function(q) {
                            var n = (q.answers || []).length, seats = seatsTotal(q), meta = qFailed(q) ? 'did not finish' : (n ? n + (seats ? ' of ' + seats : '') : '');
                            return '<button type="button" class="dtw-pi-qitem" onclick="dtwPilotRecord(\'' + esc(jsq(q.id || '')) + '\', this)" title="Open the record of this question"><span class="c">' + esc(piQClock(q)) + '</span><span class="t">' + esc(q.q || '') + '</span>' + (meta ? '<span class="n">' + esc(meta) + '</span>' : '') + '</button>';
                        }).join('') + '</div></details>';
                }
                return out;
            }
            function piRecordCard(doc) {
                doc = doc || document;
                var card = null; try { card = doc.getElementById('dtwPiRecordCard'); } catch (_) { return null; }
                if (card) return card;
                var host = doc.getElementById('digitalTwinsIQView') || doc.body; if (!host) return null;
                var scrim = doc.createElement('div'); scrim.id = 'dtwPiRecScrim'; scrim.className = 'dtw-pi-recscrim';
                scrim.addEventListener('click', function() { dtwPilotRecordClose(scrim); });
                card = doc.createElement('div'); card.id = 'dtwPiRecordCard'; card.className = 'dtw-pi-reccard'; card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', 'The question in full');
                host.appendChild(scrim); host.appendChild(card);
                doc.addEventListener('keydown', function(e) { if (e.key === 'Escape' && card.classList.contains('on')) dtwPilotRecordClose(card); });
                return card;
            }
            function piRecordKey(q) { return String((q || {}).stage || '') + '|' + ((q || {}).answers || []).length + '|' + String((q || {}).read || '').length + '|' + ((q || {}).facts_used || []).length; }
            function piRecordHtml(s, q) {
                var roster = ((s.group || {}).roster || []), qs = (s.questions || []).filter(function(x) { return x && String(x.q || '').trim(); });
                var clock = piQClock(q), kind = q.kind && q.kind !== 'open' ? kindLabel(q.kind) : '';
                var head = '<div class="hd"><div><div class="k">' + (clock ? 'Asked at ' + esc(clock) : 'Asked') + (kind ? SEP + esc(kind) : '') + '</div><div class="q">' + esc(q.q || '') + '</div></div><button type="button" class="x" onclick="dtwPilotRecordClose(this)">Close</button></div>';
                var pick = qs.length > 1 ? '<div class="dtw-fg-qpick"><label for="dtwPiRecQ">Questions asked</label><select id="dtwPiRecQ" class="dtw-in" aria-label="Question to show" onchange="dtwPilotRecord(this.value, this)">' + qs.map(function(x, j) {
                    var lab = (j + 1) + '. ' + String(x.q || ''); if (qInFlight(x)) lab += ' (working)'; else if (qFailed(x)) lab += ' (did not finish)'; if (lab.length > 92) lab = lab.slice(0, 89) + '...';
                    return '<option value="' + esc(x.id || '') + '"' + (x === q ? ' selected' : '') + '>' + esc(lab) + '</option>';
                }).join('') + '</select></div>' : '';
                if (qFailed(q)) return head + pick + '<div class="dtw-fg-sec"><div class="rd">' + esc(q.read || 'The room did not finish this question. Ask it again and it runs fresh.') + '</div><div class="dtw-pi-probes"><button type="button" class="dtw-pi-probe" onclick="dtwPilotRecordClose(this);dtwPilotAskAt(' + Number(q.playhead_s || 0) + ', \'' + esc(jsq(q.q || '')) + '\')">Ask again</button></div></div>';
                var live = qInFlight(q), opts = optsOf(q), rk = rankMap(q), seats = seatsTotal(q), counts = live ? liveCounts(q) : null, nA = (q.answers || []).length;
                var body = '<div class="dtw-fg-sec">' + (live ? liveHead(nA, seats, '') : '<div class="dtw-fg-th">The split.<span>Share of the room, by answer</span></div>');
                if (!opts.length) body += '<div class="dtw-pi-fn" style="margin-top:4px">' + (live ? 'The options land with the first answers.' : 'An open question: the read and the voices carry it.') + '</div>';
                else {
                    body += questionStack(q) + opts.map(function(o) {
                        // still tallying: the seat count so far in motion, no percent (Jenna 2026-11-18); the share lands with the count
                        if (live) return liveRow(o, counts[String(o.id)] || 0, seats, seriesOf(rk, o.id), rk[String(o.id)] === 0);
                        var v = Number(o.pct || 0);
                        var tally = ((o.seats_n === 0 || (o.tally && /^0 of /.test(String(o.tally)))) && v > 0 ? 'under a seat' : (o.tally || (o.seats_n != null && seats ? o.seats_n + ' of ' + seats : '')));
                        var det = [];
                        if (o.people != null) det.push(fmtN(o.people) + ' people');
                        if (tally) det.push(tally);
                        return '<div class="dtw-fg-lrow' + (rk[String(o.id)] === 0 ? ' lead' : '') + '"><span class="dtw-fg-dot" style="background:' + seriesOf(rk, o.id) + '"></span><div class="body"><div class="l1"><span class="lab">' + esc(o.label) + '</span>' + (det.length ? '<span class="det">' + det.join(SEP) + '</span>' : '') + '<b>' + pct(v, 1) + '</b></div></div></div>';
                    }).join('');
                }
                body += piBycoHtml(q) + '</div>';
                if (q.read) body += '<div class="dtw-fg-sec"><div class="dtw-fg-th">The read.</div><div class="rd">' + esc(q.read) + '</div></div>';
                else if (live) body += '<div class="dtw-fg-sec"><div class="dtw-pi-working" data-qid="' + esc(q.id || '') + '"><span class="dtw-fg-pulse"></span><span>' + esc(cap(stageWords(q))) + '</span></div></div>';
                if (q.framing && normQ(q.framing) !== normQ(q.q)) body += '<div class="dtw-fg-sec"><div class="dtw-fg-th">How it was put to the room.</div><div class="fr">' + esc(q.framing) + '</div></div>';
                var voices = voicesAll(q, roster);
                if (voices.length) body += '<div class="dtw-fg-sec"><div class="dtw-fg-th">The voices.<span>' + voices.length + (seats ? ' of ' + seats : '') + '</span></div><div class="dtw-pi-thread">' + voices.map(function(v) {
                    var hit = v.seat != null ? piSeatByNo(v.seat) : null, r = hit ? hit.r : { seat: v.seat, name: v.name, age: v.age, city: v.city, segment: v.segment, portrait_url: v.portrait_url };
                    return piSeatRowHtml(r, hit ? hit.i : 0, { line: v.line, lit: true, clock: '' }, { remark: true, answer: true });
                }).join('') + '</div></div>';
                body += factsSec(q) + talkSec(q) + segmentsSec(q, rk);
                var probes = (q.probes_next || []).slice(0, 2);
                if (probes.length) body += '<div class="dtw-pi-probes">' + probes.map(function(p) { return '<button type="button" class="dtw-pi-probe" onclick="dtwPilotRecordClose(this);dtwPilotAskAt(' + Number(q.playhead_s || 0) + ', \'' + esc(jsq(p)) + '\')">' + esc(p) + '</button>'; }).join('') + '</div>';
                return head + pick + body;
            }
            window.dtwPilotRecord = function(qid, elm) {
                var s = S.pilot; if (!s) return;
                var q = (s.questions || []).filter(function(x) { return x && String(x.id) === String(qid); })[0]; if (!q) return;
                var doc = (elm && elm.ownerDocument) || document, card = piRecordCard(doc); if (!card) return;
                S.piRecord = { sid: s.id, qid: String(q.id), doc: doc };
                card.innerHTML = piRecordHtml(s, q); card.setAttribute('data-k', piRecordKey(q));
                card.classList.add('on'); card.scrollTop = 0;
                var sc = doc.getElementById('dtwPiRecScrim'); if (sc) sc.classList.add('on');
            };
            window.dtwPilotRecordClose = function(elm) {
                var docs = [document];
                if (elm && elm.ownerDocument && docs.indexOf(elm.ownerDocument) < 0) docs.push(elm.ownerDocument);
                if (S.piRecord && S.piRecord.doc && docs.indexOf(S.piRecord.doc) < 0) docs.push(S.piRecord.doc);
                docs.forEach(function(d) { try { var c = d.getElementById('dtwPiRecordCard'); if (c) c.classList.remove('on'); var sc = d.getElementById('dtwPiRecScrim'); if (sc) sc.classList.remove('on'); } catch (_) {} });
                S.piRecord = null;
            };
            function piRecordRefresh() {
                // the open record follows a question still with the room: the answers so far, then the read
                var rec = S.piRecord, s = S.pilot; if (!rec || !s || rec.sid !== s.id) return;
                var q = (s.questions || []).filter(function(x) { return x && String(x.id) === rec.qid; })[0]; if (!q) return;
                var card = null; try { card = rec.doc.getElementById('dtwPiRecordCard'); } catch (_) { card = null; }
                if (!card || !card.classList.contains('on')) return;
                var key = piRecordKey(q);
                if (card.getAttribute('data-k') !== key) { var top = card.scrollTop; card.innerHTML = piRecordHtml(s, q); card.setAttribute('data-k', key); card.scrollTop = top; }
            }
            function renderPilotScores(s) {
                var scored = s.status === 'stopped' && !!s.scorecard;   // the room's scores exist only once it has watched and stopped
                var sc = scored ? (s.scorecard || {}) : {}, hd = ((sc.battery || {}).headline || {});
                var genre = s.genre || '', ch = piNormChoice(s), which = (genre && ch.has_genre_norm) ? 'genre' : 'all', normWord = piNormWord(ch, which);
                var roster = ((s.group || {}).roster || []), people = Number((s.group || {}).size_people || 0);
                var gsel = '<select class="dtw-pi-gsel" onchange="dtwPilotGenre(this.value)" aria-label="Compare to">' + piNormOptions(s.format || sc.format || s.mode, genre) + '</select>';
                var sideNotes = piNormNotes(ch);
                var normNote = sideNotes.length ? '<div class="dtw-pi-fn">' + esc(sideNotes.join(' ')) + '</div>' : '';
                var out = '<div class="dtw-pi-h">This cut</div>';
                function stat(v, label, metric) {
                    var norm = normCell(sc, metric, which), nv = norm ? parseFloat(norm) : NaN, up = isFinite(nv) && v != null && Number(v) >= nv;
                    return '<div class="dtw-pi-stat"><div class="v' + (up ? ' up' : '') + '">' + (v == null ? '-' : Number(v).toFixed(1)) + '</div><div class="k">' + esc(label) + (norm ? ', ' + esc(normWord) + ' ' + esc(norm.replace('%', '')) : (metric === 'finish' && v != null ? ', no norm yet' : '')) + '</div></div>';
                }
                if (hd.total_positive_pct != null) {
                    out += stat(hd.total_positive_pct, 'Total positive', 'total_positive') + stat(hd.definitely_recommend_pct, 'Definitely recommend', 'definitely_recommend') + stat(hd.finish_pct, 'Would finish', 'finish');
                    var w = function(x) { return Math.max(0, Math.min(100, Number(x) || 0)).toFixed(1) + '%'; };
                    var bars = [['Total positive', hd.total_positive_pct, 'total_positive'], ['Recommend', hd.definitely_recommend_pct, 'definitely_recommend'], ['Finish', hd.finish_pct, 'finish']].map(function(r) {
                        var norm = normCell(sc, r[2], which), nv = norm ? parseFloat(norm) : NaN, v = r[1] == null ? NaN : Number(r[1]);
                        return '<div class="dtw-pi-bar"><div class="l"><span>' + esc(r[0]) + '</span><span>' + (isFinite(v) ? v.toFixed(1) : '-') + (isFinite(nv) ? ' / ' + nv.toFixed(1) : '') + '</span></div><div class="t"><i style="width:' + (isFinite(v) ? w(v) : '0%') + '"></i></div>' + (isFinite(nv) ? '<div class="t n"><i style="width:' + w(nv) + '"></i></div>' : '') + '</div>';
                    }).join('');
                    out += '<div class="dtw-pi-hr"></div><div class="dtw-pi-h">Against the norm</div><div class="dtw-pi-bars">' + bars + '</div><div class="dtw-pi-fn">Green is this cut. Grey is the norm. Compared to: ' + gsel + '</div>' + normNote;
                    out += roomStandsForNote(s.group);
                    out += piForecastSide(s);
                } else {
                    out += '<div class="dtw-pi-stat"><div class="v" id="dtwPiDialNow">-</div><div class="k">Dial now, 0 to 100</div></div>'
                        + '<div class="dtw-pi-stat"><div class="v">' + roster.length + '</div><div class="k">Seated' + (people ? ', standing for ' + fmtN(people) + ' people' : '') + '</div></div>'
                        + roomStandsForNote(s.group)
                        + '<div class="dtw-pi-hr"></div><div class="dtw-pi-fn">' + (s.status === 'preparing' ? 'The room is scoring what it watched.' : 'Scores land here when you press Stop. Compared to: ' + gsel) + '</div>';
                }
                var others = ((S.twin || {}).pilot_sessions || []).filter(function(x) { return x && x.id !== s.id; }).slice(0, 5);
                if (others.length) {
                    out += '<div class="dtw-pi-hr"></div><div class="dtw-pi-h">Other tests</div>' + others.map(function(o) {
                        var h = (((o.scorecard || {}).battery || {}).headline || {});
                        return '<button type="button" class="dtw-pi-other" onclick="dtwPilotPick(\'' + esc(o.id) + '\')" title="' + esc(o.title || '') + '"><span class="t">' + esc(o.title || 'Untitled') + '</span><span class="n">' + (h.total_positive_pct != null ? Number(h.total_positive_pct).toFixed(1) : (o.status === 'preparing' ? 'preparing' : 'open')) + '</span></button>';
                    }).join('') + '<div class="dtw-pi-fn">Total positive, out of 100.</div>';
                }
                return '<div class="dtw-pi-col side scores">' + out + '</div>';
            }
            // ---- the dial views (Jenna 2026-11-03: "see each cohort's dial as well as cut it to see each individual
            // person's dial and the overall"). Room: the room line. Cohorts: the cohort lines, a chip cuts to one cohort
            // and its people. People: every seat's own line in its cohort color; hover a seat to follow it, click to pin.
            function piView() {
                var s = S.pilot || {};
                if (!S.pilotView || S.pilotView.sid !== s.id) S.pilotView = { sid: s.id, mode: piCohortSplits(s).length ? 'cohorts' : 'room', cohort: '', seat: null, hover: null };
                return S.pilotView;
            }
            function piSeatSeries(s, seatNo) {
                // {t: [...], v: [...]} for one seat: the prepared cut aligns to the dial clock, the live room keeps [t, v] pairs
                var sno = String(seatNo), d = (s || {}).dial || {}, ts = d.t || [];
                // a stopped test has been scored: its seat dials sit on the dial's own clock and level; the live frame
                // readings are the picture while the room is still watching
                var sd = (s || {}).seat_dials || {};
                if (sd[sno] && sd[sno].length && ts.length && sd[sno].length === ts.length) return { t: ts, v: sd[sno].map(Number) };
                var live = ((s || {}).live || {}).seat_dials || {};
                if (live[sno] && live[sno].length) {
                    // the pairs are split once per new frame, not on every tick of the clock
                    var pts = live[sno], cache = S.piSeatCache || (S.piSeatCache = {}), c = cache[sno];
                    if (c && c.src === pts && c.n === pts.length) return c.out;
                    var out = { t: pts.map(function(p) { return Number(p[0]); }), v: pts.map(function(p) { return Number(p[1]); }) };
                    cache[sno] = { src: pts, n: pts.length, out: out };
                    return out;
                }
                if (sd[sno] && sd[sno].length) return { t: ts, v: sd[sno].map(Number) };
                return null;
            }
            function piScored(s) {
                // a scored test: a seat's dial sits on the dial's own clock
                s = s || {}; var sd = s.seat_dials || {}, n = ((s.dial || {}).t || []).length;
                return n > 0 && Object.keys(sd).some(function(k) { return (sd[k] || []).length === n; });
            }
            function piLiveSeries(s) {
                // the room is still watching (or being scored): the live frame readings are the one source all three views draw from
                s = s || {};
                return !piScored(s) && isLiveSess(s);
            }
            function piSeatLineColor(s, r, i) {
                return (piRoomHasCohorts() && piCohortColorFor(r)) || seatHue(r, i);
            }
            function piAlpha(hex, a) {
                var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || '').trim());
                if (!m) return hex;
                return 'rgba(' + parseInt(m[1], 16) + ',' + parseInt(m[2], 16) + ',' + parseInt(m[3], 16) + ',' + a + ')';
            }
            function piViewModes(s) {
                // Room always. Cohorts when the room was cut into cohorts. People when a seat has a line, or the room is live and
                // the lines land frame by frame (Jenna 2026-11-04: "these toggles should be active when the pilot testing is live
                // so that you can toggle between them in real time to see whichever view you want").
                s = s || S.pilot || {};
                var roster = ((s.group || {}).roster || []);
                var hasSeats = roster.some(function(r) { return !!piSeatSeries(s, r.seat); });
                var modes = [['room', 'Room']];
                if (piCohortSplits(s).length) modes.push(['cohorts', 'Cohorts']);
                if (roster.length && (hasSeats || piLiveSeries(s))) modes.push(['people', 'People']);
                return modes;
            }
            function piViewModesKey(s) { return piViewModes(s).map(function(m) { return m[0]; }).join(','); }
            function piViewControl(s) {
                var v = piView(), modes = piViewModes(s);
                if (modes.length < 2) return '';
                return '<span class="dtw-pi-views" role="tablist" aria-label="Dial view">' + modes.map(function(m) { return '<button type="button" class="' + (v.mode === m[0] ? 'on' : '') + '" onclick="dtwPilotView(\'' + m[0] + '\')">' + m[1] + '</button>'; }).join('') + '</span>';
            }
            window.dtwPilotView = function(mode) {
                var v = piView(); v.mode = mode || 'room'; if (mode !== 'cohorts') v.cohort = ''; if (mode !== 'people') { v.seat = null; v.hover = null; }
                piLegendRepaint(); drawPilotDial(playheadNow()); piPinPaint();
            };
            window.dtwPilotViewCohort = function(key) {
                var v = piView(); v.mode = 'cohorts'; v.cohort = (v.cohort === key) ? '' : (key || '');
                piLegendRepaint(); drawPilotDial(playheadNow());
            };
            window.dtwPilotViewSeat = function(seatNo) {
                var v = piView(); var n = Number(seatNo);
                if (v.mode !== 'people') v.mode = 'people';
                v.seat = (v.seat === n) ? null : n;
                piLegendRepaint(); drawPilotDial(playheadNow()); piPinPaint();
            };
            function piPinPaint() {
                var v = piView();
                document.querySelectorAll('#dtwPiSeats .dtw-pi-seatrow').forEach(function(row) { row.classList.toggle('pinned', v.seat != null && Number(row.getAttribute('data-seat')) === v.seat); });
                var p = S.roomPop; if (p && p.win && !p.win.closed) { try { p.win.document.querySelectorAll('#dtwPiSeats .dtw-pi-seatrow').forEach(function(row) { row.classList.toggle('pinned', v.seat != null && Number(row.getAttribute('data-seat')) === v.seat); }); } catch (_) {} }
            }
            function piLegendRepaint() {
                var s = S.pilot; if (!s) return;
                var tmp = document.createElement('div'); tmp.innerHTML = splitLegend(s);
                document.querySelectorAll('.dtw-pane.active .dtw-pi-legend').forEach(function(lg) { var fresh = tmp.firstChild; if (fresh) lg.outerHTML = fresh.outerHTML; });
            }
            function piHoverSeat(seatNo) {
                var v = piView(); var n = seatNo == null ? null : Number(seatNo);
                if (v.hover === n) return;
                v.hover = n;
                if (v.mode === 'people' || v.cohort) drawPilotDial(playheadNow());
            }
            function piRowOf(t) { return t && t.closest ? t.closest('#dtwPiSeats .dtw-pi-seatrow[data-seat]') : null; }   // a Prometheus line has no seat
            function piBioTrigger(t, row) {
                // the one thing in the room that opens who they are: the seat's photo (or initials), or a chip in the strip,
                // which is the photo; a remark in the chat has none
                if (!row || row.classList.contains('remark')) return null;
                if (row.classList.contains('dtw-pi-chip')) return row;
                var c = t && t.closest ? t.closest('.circ') : null;
                return c && row.contains(c) ? c : null;
            }
            function piBioToggle(row) {
                // the photo clicked: open who they are beside the seat and keep it open; the same photo again closes it
                var n = Number(row.getAttribute('data-seat')), doc = row.ownerDocument || document;
                if (PI_BIO.stuck && PI_BIO.seat === n && PI_BIO.doc === doc && piBioOn(doc)) { piBioHide(); return; }
                PI_BIO.stuck = !!piBioShow(n, doc, { rect: row.getBoundingClientRect() });
            }
            function piSeatRowOver(e) {
                // a seat in the room, or a remark in the chat: follow the line; the card never opens on hover here
                var row = piRowOf(e.target); if (!row) return;
                piHoverSeat(row.getAttribute('data-seat'));
            }
            function piSeatRowOut(e) {
                var row = piRowOf(e.target); if (!row) return;
                var to = e.relatedTarget;
                if (PI_BIO.over || (to && (row.contains(to) || (to.closest && to.closest('#dtwPiBioCard'))))) return;
                piHoverSeat(null); piBioRelease();
            }
            function piSeatRowClick(e) {
                // the photo opens (or closes) who they are; a click anywhere else closes an open card, and on the rest of a
                // seat's row (the name, the text, a remark) it pins the seat, as before
                var row = piRowOf(e.target), inCard = !!(e.target && e.target.closest && e.target.closest('#dtwPiBioCard'));
                if (row && piBioTrigger(e.target, row)) { piBioToggle(row); return; }
                if (PI_BIO.stuck && !inCard) piBioHide();
                if (!row) return;
                if (!(e.target.closest && e.target.closest('button, a, input, select'))) dtwPilotViewSeat(row.getAttribute('data-seat'));
            }
            function piSeatRowKey(e) {
                // Escape closes an open card; Enter or Space on a focused photo opens (or closes) it, as a click does
                if (e.key === 'Escape' || e.key === 'Esc') { if (PI_BIO.doc) piBioHide(); return; }
                if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
                var row = piRowOf(e.target), trig = piBioTrigger(e.target, row);
                if (!trig || trig !== e.target) return;
                e.preventDefault(); piBioToggle(row);
            }
            // ---- the pointer on the dial: on the People view (or a cut cohort) the seat whose line it is on is followed, who
            // they are opens beside the pointer, and a click pins the seat, the way a seat in the room does.
            function piCohortSeats(s, cohortKey) {
                // the seats a cohort line stands for (by the cohort key, or by the color a seat's segment resolved to)
                var c = piCohortSplits(s).filter(function(x) { return x.key === cohortKey; })[0];
                return c ? (((s || {}).group || {}).roster || []).filter(function(r) { return piCohortKey(r.segment) === c.key || piCohortColorFor(r) === c.color; }) : [];
            }
            function piDrawnSeats(s, view) {
                // the seats whose own lines are on the dial: every seat on People, the cut cohort's seats on Cohorts
                if (view.mode === 'people') return (((s || {}).group || {}).roster || []);
                if (view.mode === 'cohorts' && view.cohort) return piCohortSeats(s, view.cohort);
                return [];
            }
            function piSegDist(x, y, x1, y1, x2, y2) {
                var dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy, t = l2 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / l2)) : 0;
                return Math.sqrt(Math.pow(x - (x1 + t * dx), 2) + Math.pow(y - (y1 + t * dy), 2));
            }
            function piSeatHit(c, mx, my, upto) {
                // the seat whose line passes within a few pixels of (mx, my) on the dial canvas, the seat already followed (or
                // pinned) keeping a tie; null off every line. The same geometry drawPilotDial draws with, up to the playhead.
                var s = S.pilot || {}, d = s.dial || {}, ts = d.t || [], view = piView();
                var seats = piDrawnSeats(s, view); if (!seats.length) return null;
                var w = c.clientWidth || c.width || 640, h = c.clientHeight || c.height || 132;
                var dur = piDur(s);
                var tol = 6, best = null, bestD = Infinity, hold = view.hover != null ? view.hover : view.seat, holdD = Infinity;
                seats.forEach(function(r) {
                    var ss = piSeatSeries(s, r.seat); if (!ss) return;
                    var tArr = ss.t, vals = ss.v, px = null, py = null, dd = Infinity;
                    for (var i = 0; i < tArr.length && i < vals.length; i++) {
                        if (tArr[i] > upto) break;
                        var yv = Number(vals[i]); if (!isFinite(yv)) continue;
                        var x = (tArr[i] / dur) * w, y = h - 10 - (Math.max(0, Math.min(100, yv)) / 100) * (h - 20);
                        if (px == null) { if (Math.abs(x - mx) <= tol) dd = Math.min(dd, Math.sqrt((x - mx) * (x - mx) + (y - my) * (y - my))); }
                        else if (mx >= Math.min(px, x) - tol && mx <= Math.max(px, x) + tol) dd = Math.min(dd, piSegDist(mx, my, px, py, x, y));
                        px = x; py = y;
                        if (x > mx + tol) break;
                    }
                    if (dd > tol) return;
                    if (hold != null && Number(r.seat) === Number(hold)) holdD = dd;
                    if (dd < bestD) { bestD = dd; best = Number(r.seat); }
                });
                return holdD <= tol ? Number(hold) : best;
            }
            function piDialHover(c, e) {
                var rect = c.getBoundingClientRect(), n = piSeatHit(c, e.clientX - rect.left, e.clientY - rect.top, playheadNow());
                c.style.cursor = n != null ? 'pointer' : (piReplayable(S.pilot) ? 'ew-resize' : '');
                if (n == null) { piHoverSeat(null); piBioRelease(); return; }
                // the card opens once beside the pointer and stays put while the same line is followed, so it can be read or
                // scrolled; a card a photo in the room opened moves to the pointer here and leaves with it, as any hover card does
                var fresh = PI_BIO.stuck || PI_BIO.seat !== n || PI_BIO.doc !== document || !piBioOn(document);
                PI_BIO.stuck = false;
                piHoverSeat(n);
                if (fresh) piBioShow(n, document, { x: e.clientX, y: e.clientY, avoid: rect }); else piBioHold();
            }
            if (!window._piHoverWired) {
                window._piHoverWired = true;
                document.addEventListener('mouseover', piSeatRowOver, true);
                document.addEventListener('focusin', piSeatRowOver, true);
                document.addEventListener('mouseout', piSeatRowOut, true);
                document.addEventListener('focusout', piSeatRowOut, true);
                document.addEventListener('click', piSeatRowClick, true);
                document.addEventListener('keydown', piSeatRowKey, true);
                document.addEventListener('mousemove', function(e) { var c = e.target; if (c && c.id === 'dtwPiDial') piDialHover(c, e); }, true);
                document.addEventListener('mouseout', function(e) { var c = e.target; if (!c || c.id !== 'dtwPiDial') return; var to = e.relatedTarget; if (to && to.closest && to.closest('#dtwPiBioCard')) return; c.style.cursor = ''; piHoverSeat(null); piBioRelease(); }, true);
                document.addEventListener('click', function(e) { var c = e.target; if (!c || c.id !== 'dtwPiDial') return; var rect = c.getBoundingClientRect(), n = piSeatHit(c, e.clientX - rect.left, e.clientY - rect.top, playheadNow()); if (n != null) dtwPilotViewSeat(n); else if (piReplayable(S.pilot)) piSeekTo((e.clientX - rect.left) / Math.max(1, rect.width) * piDur(S.pilot)); }, true);
            }
            // ---- the ticker under the dial, the five-minute averages, the minute under the pointer, and the chat lit for a minute
            // (Jenna 2026-11-05: "it needs to show a ticker with duration and time across the bottom so you can see where things
            // happen against minutes. if you hover over a certain minute it should open a summary of what people were saying at
            // that point so that you can get a read on sentiment there. every 5 minutes it needs to give you the average rating
            // based on room/cohort/people whatever you're on and hovering over. and then if you click on it it would highlight
            // the chats from that time so you could read them"). The ticker spans the runtime: a mark every minute, labels every
            // minute (every five past thirty minutes of runtime), the playhead while the room watches, the stop point once it has
            // stopped. The averages and the minute read follow the active line: the room on Room; on Cohorts the hovered line or
            // chip, else the cut cohort, else the room; on People the followed seat. The read comes from the test
            // (GET .../pilot/<id>/minute) and is kept per test, line and minute; a finished test carries every Room minute already.
            // Hovering a minute never opens who someone is: that stays on a click of the photo.
            function piDur(s) { s = s || S.pilot || {}; var ts = ((s.dial || {}).t || []); return Number((s.media || {}).runtime_s || (ts.length ? ts[ts.length - 1] : 1)) || 1; }
            function piFinished(s) { s = s || S.pilot || {}; return s.status === 'stopped' || liveFinished(s); }
            function piStopPoint(s) { s = s || S.pilot || {}; var ts = ((s.dial || {}).t || []); return ts.length ? Number(ts[ts.length - 1]) : piDur(s); }
            function piTickPlan(dur) {
                // a mark every minute; labels every minute up to thirty minutes of runtime, every five past that
                dur = Math.max(1, Number(dur) || 0);
                var every = dur > 1800 ? 5 : 1, ticks = [];
                for (var m = 0; m * 60 <= dur + 0.001; m++) ticks.push({ t: m * 60, m: m, major: m % 5 === 0, label: m % every === 0 ? clockFmt(m * 60) : '' });
                return { dur: dur, every: every, ticks: ticks };
            }
            function piMinuteWindow(m, margin) { margin = margin == null ? 30 : Number(margin); return { a: m * 60 - margin, b: (m + 1) * 60 + margin }; }
            function piMinuteAvgOf(tArr, vArr, m) {
                // the line's average over one minute (points with m:00 <= t < m+1:00); null when it has none there
                var sum = 0, n = 0;
                for (var i = 0; i < (tArr || []).length && i < (vArr || []).length; i++) { var t = Number(tArr[i]), v = Number(vArr[i]); if (!isFinite(t) || !isFinite(v)) continue; if (t >= m * 60 && t < (m + 1) * 60) { sum += v; n++; } }
                return n ? { avg: sum / n, n: n } : null;
            }
            function piBucketMarks(tArr, vArr, dur, upto, finished, width) {
                // the average over the five minutes before each five-minute mark the line has reached, and the end once finished
                width = width || 300; dur = Math.max(1, Number(dur) || 0);
                var end = Math.max(0, Math.min(Number(upto) || 0, dur)), marks = [], out = [];
                if (!tArr || !tArr.length || !vArr || !vArr.length) return out;
                for (var k = width; k <= end + 0.001; k += width) marks.push(k);
                if (finished && end > 0 && (!marks.length || end - marks[marks.length - 1] > 45)) marks.push(end);
                marks.forEach(function(te) {
                    var a = te - width, sum = 0, n = 0;
                    for (var i = 0; i < tArr.length && i < vArr.length; i++) { var t = Number(tArr[i]), v = Number(vArr[i]); if (!isFinite(t) || !isFinite(v)) continue; if (t > a && t <= te + 0.001) { sum += v; n++; } }
                    if (n) out.push({ t: te, from: Math.max(0, a), avg: sum / n, n: n });
                });
                return out;
            }
            function piActiveSeries(s, view) {
                // the line the averages and the minute read follow
                s = s || S.pilot || {}; view = view || piView();
                var d = s.dial || {}, room = { mode: 'room', id: '', label: 'Room', color: '#E9E8E1', t: d.t || [], v: d.value || [] };
                var seatNo = view.hover != null ? view.hover : view.seat;
                if ((view.mode === 'people' || (view.mode === 'cohorts' && view.cohort)) && seatNo != null) {
                    var ss = piSeatSeries(s, seatNo), hit = piSeatByNo(seatNo);
                    if (ss) return { mode: 'person', id: String(seatNo), label: hit ? titleCase(hit.r.name || '') : ('Seat ' + seatNo), color: hit ? piSeatLineColor(s, hit.r, hit.i) : '#E9E8E1', t: ss.t, v: ss.v };
                }
                if (view.mode === 'cohorts') {
                    var hov = S.piCohortHover && S.piCohortHover.sid === s.id ? S.piCohortHover.key : '', key = hov || view.cohort;
                    if (key) { var c = piCohortSplits(s).filter(function(x) { return x.key === key; })[0]; if (c) return { mode: 'cohort', id: c.key, label: c.label, color: c.color, t: d.t || [], v: c.value || [] }; }
                }
                return room;
            }
            function piSeriesSeats(s, act) {
                // the seats whose lines a minute lights: every seat for the room, the cohort's seats, the one seat
                if (!act || act.mode === 'room') return null;
                if (act.mode === 'person') return [Number(act.id)];
                return piCohortSeats(s, act.id).map(function(r) { return Number(r.seat); });
            }
            function piCohortHit(c, mx, my, upto) {
                // the cohort whose line passes within a few pixels of the pointer (the Cohorts view, none cut)
                var s = S.pilot || {}, d = s.dial || {}, ts = d.t || [], cs = piCohortSplits(s); if (!cs.length) return '';
                var w = c.clientWidth || c.width || 640, h = c.clientHeight || c.height || 132, dur = piDur(s), tol = 6, best = '', bestD = Infinity;
                cs.forEach(function(co) {
                    var vals = co.value || [], px = null, py = null, dd = Infinity;
                    for (var i = 0; i < ts.length && i < vals.length; i++) {
                        if (ts[i] > upto) break;
                        var yv = Number(vals[i]); if (!isFinite(yv)) continue;
                        var x = (ts[i] / dur) * w, y = h - 10 - (Math.max(0, Math.min(100, yv)) / 100) * (h - 20);
                        if (px == null) { if (Math.abs(x - mx) <= tol) dd = Math.min(dd, Math.sqrt((x - mx) * (x - mx) + (y - my) * (y - my))); }
                        else if (mx >= Math.min(px, x) - tol && mx <= Math.max(px, x) + tol) dd = Math.min(dd, piSegDist(mx, my, px, py, x, y));
                        px = x; py = y;
                        if (x > mx + tol) break;
                    }
                    if (dd <= tol && dd < bestD) { bestD = dd; best = co.key; }
                });
                return best;
            }
            function piDrawBuckets(ctx, s, view, X, Y, upto, dur, w, h) {
                // the five-minute averages on the active line: a dot on the line, the figure beside it, one decimal
                var fin = piFinished(s), act = piActiveSeries(s, view);
                var marks = piBucketMarks(act.t, act.v, dur, fin ? Math.min(upto, piStopPoint(s)) : upto, fin);
                if (!marks.length) return;
                ctx.save();
                ctx.font = '600 10px "Crosswalk Inter", Inter, system-ui, sans-serif'; ctx.textBaseline = 'middle';
                marks.forEach(function(b) {
                    var x = X(b.t), y = Y(b.avg), label = b.avg.toFixed(1), tw = ctx.measureText(label).width;
                    ctx.globalAlpha = 0.96; ctx.fillStyle = act.color;
                    ctx.beginPath(); ctx.arc(x, y, 2.8, 0, Math.PI * 2); ctx.fill();
                    var lx = x + 6; if (lx + tw + 4 > w) lx = x - tw - 6;
                    var ly = y - 10; if (ly < 9) ly = y + 10;
                    ctx.fillStyle = 'rgba(12,22,24,0.72)'; ctx.fillRect(lx - 3, ly - 7, tw + 6, 14);
                    ctx.fillStyle = act.color; ctx.fillText(label, lx, ly);
                });
                ctx.restore();
            }
            function piMinuteSel(s) { var sel = S.piMinuteSel; return sel && sel.sid === ((s || S.pilot || {}).id) ? sel : null; }
            function paintPilotTicker(t) {
                var box = el('dtwPiTicker'); if (!box) return;
                var s = S.pilot || {}, dur = piDur(s), fin = piFinished(s), w = box.clientWidth || 640, plan = piTickPlan(dur);
                // the labels thin out when the box is too narrow for one every minute
                var every = plan.every; while (every < 60 && (w * 60 * every / dur) < 34) every = every === 1 ? 2 : (every === 2 ? 5 : every * 2);
                var sel = piMinuteSel(s), key = [s.id, dur.toFixed(2), w, every, fin ? 1 : 0, sel ? sel.minute : -1].join('|');
                if (box.getAttribute('data-k') !== key) {
                    var html = '<div class="ax"></div>', n = Math.ceil(dur / 60);
                    plan.ticks.forEach(function(k) {
                        var left = (k.t / dur * 100).toFixed(3);
                        html += '<i class="tk' + (k.major ? ' maj' : '') + '" style="left:' + left + '%"></i>';
                        // the end is always labeled; a minute label within 100 s of it gives way
                        var isEnd = dur - k.t < 60, lab = isEnd || (k.m % every === 0 && (k.m === 0 || dur - k.t > 100));
                        if (lab) html += '<span class="lb' + (k.m === 0 ? ' l0' : (dur - k.t < dur * 0.04 ? ' lend' : '')) + '" style="left:' + left + '%">' + clockFmt(k.t) + '</span>';
                    });
                    for (var m = 0; m < n; m++) {
                        var a = (m * 60 / dur * 100).toFixed(3), bw = (Math.min(60, dur - m * 60) / dur * 100).toFixed(3);
                        html += '<button type="button" class="hit' + (sel && sel.minute === m ? ' on' : '') + '" data-m="' + m + '" style="left:' + a + '%;width:' + bw + '%" aria-label="Minute ' + clockFmt(m * 60) + '" onmouseenter="dtwPilotMinuteOver(' + m + ')" onfocus="dtwPilotMinuteOver(' + m + ')" onmouseleave="dtwPilotMinuteOut()" onblur="dtwPilotMinuteOut()" onclick="dtwPilotMinuteClick(' + m + ')"></button>';
                    }
                    html += '<i class="ph' + (fin ? ' stop' : '') + '" id="dtwPiTickHead"></i>';
                    box.innerHTML = html; box.setAttribute('data-k', key);
                }
                var ph = el('dtwPiTickHead'); if (!ph) return;
                var at = Math.max(0, Math.min(dur, fin ? piStopPoint(s) : (Number(t) || 0)));
                ph.style.left = (at / dur * 100).toFixed(3) + '%';
                ph.title = (fin ? 'Stopped at ' : '') + clockFmt(at);
                var rp = el('dtwPiTickRp');
                if (fin && piReplayable(s)) {
                    if (!rp) { rp = document.createElement('i'); rp.className = 'ph rp'; rp.id = 'dtwPiTickRp'; box.appendChild(rp); }
                    var tt = Math.max(0, Math.min(dur, Number(t) || 0));
                    rp.style.left = (tt / dur * 100).toFixed(3) + '%'; rp.style.display = Math.abs(tt - at) < 0.5 ? 'none' : ''; rp.title = clockFmt(tt);
                } else if (rp) rp.remove();
            }
            // the minute tip: the time, the active line's average in that minute, how many seats were talking, and the read
            var PI_TIP = { minute: null, key: '', el: null };
            function piTipEl() {
                var tip = PI_TIP.el; if (tip && tip.isConnected) return tip;
                tip = document.createElement('div'); tip.className = 'dtw-pi-tip'; tip.id = 'dtwPiTip'; tip.setAttribute('role', 'status'); tip.style.display = 'none';
                document.body.appendChild(tip); PI_TIP.el = tip; return tip;
            }
            function piLeanClass(lean) { lean = String(lean || ''); return /warm/.test(lean) ? 'warm' : (/cool/.test(lean) ? 'cool' : (lean === 'split' ? 'split' : '')); }
            function piMinuteCacheKey(s, act, m) { return [s.id, act.mode, act.id, m].join('|'); }
            function piMinuteLocal(s, act, m) {
                // what the test already carries for this minute (a finished test has every Room minute written at stop)
                var rec = ((s || {}).minute_digests || {})[act.mode + ':' + act.id + ':' + m];
                return rec && rec.summary ? { summary: rec.summary, lean: rec.lean || '', n: null, local: true } : null;
            }
            function piMinuteTipHtml(s, act, m, got) {
                var dur = piDur(s), a = piMinuteAvgOf(act.t, act.v, m), avg = a ? a.avg : (got && got.avg != null ? Number(got.avg) : null);
                var head = '<div class="k"><b>' + esc(clockFmt(m * 60)) + ' to ' + esc(clockFmt(Math.min(dur, (m + 1) * 60))) + '</b><span><i class="sw" style="background:' + esc(act.color) + '"></i>' + esc(act.label) + ' ' + (avg != null ? avg.toFixed(1) : '-') + '</span></div>';
                var talk = got && got.n != null ? ('<div class="m">' + (got.n ? (got.n + (got.n === 1 ? ' seat' : ' seats') + ' talking') : 'Nobody spoke up') + '</div>') : '';
                var body = got ? ('<div class="r">' + esc(got.summary || '') + (got.lean ? ' <span class="lean ' + piLeanClass(got.lean) + '">' + esc(got.lean) + '</span>' : '') + '</div>') : '<div class="r wait">Reading the minute.</div>';
                return head + talk + body + '<div class="m">Click to light the chat from this minute.</div>';
            }
            function piMinuteTipPlace(tip, m) {
                var tk = el('dtwPiTicker'), ref = tk || el('dtwPiDial'); if (!ref) return;
                var r = ref.getBoundingClientRect(), dur = piDur(S.pilot || {}), x = r.left + (Math.min(dur, m * 60 + 30) / dur) * r.width;
                tip.style.display = 'block';
                var tw = tip.offsetWidth || 260, th = tip.offsetHeight || 80;
                var left = Math.max(8, Math.min(window.innerWidth - tw - 8, x - tw / 2)), top = r.bottom + 8;
                if (top + th > window.innerHeight - 8) top = Math.max(8, r.top - th - 8);
                tip.style.left = left + 'px'; tip.style.top = top + 'px';
            }
            function piMinuteTipShow(m) {
                var s = S.pilot || {}; if (!s.id || m == null || m < 0) { piMinuteTipHide(); return; }
                if (m * 60 > (piFinished(s) ? piStopPoint(s) : playheadNow()) + 0.5) { piMinuteTipHide(); return; }   // a minute the room has not reached
                var act = piActiveSeries(s, piView()), key = piMinuteCacheKey(s, act, m), tip = piTipEl();
                PI_TIP.minute = m; PI_TIP.key = key;
                var cache = S.piMinuteCache || (S.piMinuteCache = {}), got = cache[key];
                // the minute the room is in keeps changing while it watches: its read is asked for again after a moment
                if (got && !piFinished(s) && m >= Math.floor(playheadNow() / 60) - 1 && (Date.now() - (got.at || 0)) > 20000) got = null;
                tip.innerHTML = piMinuteTipHtml(s, act, m, got || piMinuteLocal(s, act, m));
                piMinuteTipPlace(tip, m);
                if (!got) piMinuteFetch(s, act, m, key);
            }
            function piMinuteTipHide() { PI_TIP.minute = null; PI_TIP.key = ''; if (PI_TIP.el) PI_TIP.el.style.display = 'none'; }
            function piMinuteFetch(s, act, m, key) {
                if (!S.key) return;
                var flights = S.piMinuteFlights || (S.piMinuteFlights = {}); if (flights[key]) return;
                flights[key] = true;
                var q = '/' + S.key + '/pilot/' + s.id + '/minute?t=' + encodeURIComponent(m * 60) + '&mode=' + encodeURIComponent(act.mode) + (act.id ? '&id=' + encodeURIComponent(act.id) : '');
                api(q).then(function(d) {
                    delete flights[key];
                    if (!d || !d.success) return;
                    var rec = { summary: d.summary || '', lean: d.lean || '', n: d.n, avg: d.avg, lines: d.lines || [], at: Date.now() };
                    (S.piMinuteCache || (S.piMinuteCache = {}))[key] = rec;
                    if (PI_TIP.key === key && PI_TIP.el && PI_TIP.el.style.display !== 'none') { PI_TIP.el.innerHTML = piMinuteTipHtml(S.pilot || {}, act, m, rec); piMinuteTipPlace(PI_TIP.el, m); }
                }).catch(function() { delete flights[key]; });
            }
            function piMinuteAt(c, mx) {
                // the minute under the pointer on the dial, or null past where the room has reached
                var s = S.pilot || {}, dur = piDur(s), w = c.clientWidth || c.width || 640, sec = Math.max(0, Math.min(dur, mx / w * dur));
                var reach = piFinished(s) ? piStopPoint(s) : playheadNow();
                if (sec > reach + 0.5) return null;
                return Math.floor(sec / 60);
            }
            function piMinuteDialMove(c, e) {
                var rect = c.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top, view = piView();
                if (view.mode === 'cohorts' && !view.cohort) dtwPilotCohortOver(piCohortHit(c, mx, my, playheadNow()));
                var m = piMinuteAt(c, mx);
                if (m == null) { piMinuteTipHide(); return; }
                if (PI_TIP.minute !== m || PI_TIP.key !== piMinuteCacheKey(S.pilot || {}, piActiveSeries(S.pilot || {}, view), m)) piMinuteTipShow(m);
            }
            window.dtwPilotMinuteOver = function(m) { piMinuteTipShow(Number(m)); };
            window.dtwPilotMinuteOut = function() { piMinuteTipHide(); };
            window.dtwPilotCohortOver = function(key) {
                var s = S.pilot || {}; key = key || '';
                var cur = S.piCohortHover || {}; if ((cur.key || '') === key && cur.sid === s.id) return;
                S.piCohortHover = { sid: s.id, key: key };
                drawPilotDial(playheadNow());
            };
            window.dtwPilotMinuteClick = function(m) {
                // the chat lines from that minute lit, the rest dimmed; the same minute again clears; another minute moves it
                var s = S.pilot || {}; if (!s.id) return;
                m = Number(m); var act = piActiveSeries(s, piView()), cur = piMinuteSel(s);
                if (cur && cur.minute === m && cur.mode === act.mode && cur.id === act.id) S.piMinuteSel = null;
                else S.piMinuteSel = { sid: s.id, minute: m, mode: act.mode, id: act.id, label: act.label, seats: piSeriesSeats(s, act) };
                piMinuteApplyAll(true);
                paintPilotTicker(playheadNow());
            };
            function piMinuteClear() { if (!S.piMinuteSel) return; S.piMinuteSel = null; piMinuteApplyAll(false); paintPilotTicker(playheadNow()); }
            function piMinuteApply(box, scroll) {
                // the lines from the minute (30 seconds either side) on the active line lit, every other remark dimmed; the first
                // scrolled into view; a repaint of the thread calls this again so the lit minute survives it
                if (!box) return;
                var th = box.querySelector('.dtw-pi-thread'); if (!th) return;
                var sel = piMinuteSel(S.pilot || {});
                if (!sel) { th.classList.remove('minute-on'); th.querySelectorAll('.minute-hit').forEach(function(r) { r.classList.remove('minute-hit'); }); return; }
                var win = piMinuteWindow(sel.minute), seats = sel.seats, first = null;
                th.classList.add('minute-on');
                th.querySelectorAll('.dtw-pi-seatrow[data-t]').forEach(function(row) {
                    var t = Number(row.getAttribute('data-t')), sn = row.getAttribute('data-seat');
                    var on = isFinite(t) && t >= win.a && t < win.b && (seats == null || sn == null || seats.indexOf(Number(sn)) >= 0);
                    row.classList.toggle('minute-hit', on);
                    if (on && !first) first = row;
                });
                if (scroll && first) { try { th.scrollTop = Math.max(0, th.scrollTop + first.getBoundingClientRect().top - th.getBoundingClientRect().top - 24); } catch (_) {} }
            }
            function piMinuteApplyAll(scroll) {
                piMinuteApply(el('dtwPiSeats'), scroll);
                var p = S.roomPop; if (p && p.win && !p.win.closed) { try { piMinuteApply(p.win.document.getElementById('dtwPiSeats'), scroll); } catch (_) {} }
            }
            if (!window._piMinuteWired) {
                window._piMinuteWired = true;
                document.addEventListener('mousemove', function(e) { var c = e.target; if (c && c.id === 'dtwPiDial') piMinuteDialMove(c, e); }, true);
                document.addEventListener('mouseout', function(e) { var c = e.target; if (!c || c.id !== 'dtwPiDial') return; piMinuteTipHide(); if (S.piCohortHover && S.piCohortHover.key) dtwPilotCohortOver(''); }, true);
                document.addEventListener('click', function(e) { var c = e.target; if (!c || c.id !== 'dtwPiDial') return; var rect = c.getBoundingClientRect(), m = piMinuteAt(c, e.clientX - rect.left); if (m != null) dtwPilotMinuteClick(m); }, true);
                document.addEventListener('keydown', function(e) { if (e.key === 'Escape' || e.key === 'Esc') piMinuteClear(); }, true);
                window.addEventListener('resize', function() { if (S.pilot && el('dtwPiTicker')) paintPilotTicker(playheadNow()); });
            }
            function splitLegend(s) {
                // the legend lists every group (the view control, the room, the cohort chips); the overlay pickers that sat here
                // are gone (Jenna 2026-11-05: "it's not needed since all groups are already listed")
                var v = piView();
                var room = piViewControl(s) + '<span><i class="sw" style="background:#E9E8E1"></i>Room</span>';
                if (v.mode === 'people') room += '<span class="hint">Every seat, in its cohort color. Hover a line, or click a seat\'s photo in the room, to see who they are; click a line to pin.</span>';
                // the cohort chips ride in every view (Jenna 2026-11-04: the Room view used to hide them, which read as
                // cohorts missing); they ride on the room's own cohorts, so a live test has them before its first frame lands
                var cs = piCohortSplits(s), cutOn = v.mode === 'cohorts' && !!v.cohort && cs.some(function(c) { return c.key === v.cohort; });
                cs.forEach(function(c) {
                    var cls = 'coh' + (cutOn ? (v.cohort === c.key ? ' cut' : ' dim') : '');
                    room += '<button type="button" class="' + cls + '" onclick="dtwPilotViewCohort(\'' + esc(c.key) + '\')" onmouseenter="dtwPilotCohortOver(\'' + esc(c.key) + '\')" onmouseleave="dtwPilotCohortOver(\'\')" title="' + (v.mode === 'cohorts' ? 'Cut the dial to this cohort' : 'Show the cohorts and cut the dial to this one') + '"><i class="sw" style="background:' + c.color + '"></i>' + esc(c.label) + ' (' + esc(c.n) + ')</button>';
                });
                return '<div class="dtw-pi-legend">' + room + '</div>';
            }
            function piKindSel() {
                return '<select id="dtwPiKindSel" class="dtw-in" onchange="dtwPilotKindSel(this.value)" aria-label="Type of question"><option value=""' + (!S.pilotKind ? ' selected' : '') + '>Open</option><option value="yes_no"' + (S.pilotKind === 'yes_no' ? ' selected' : '') + '>Yes / no</option><option value="rating"' + (S.pilotKind === 'rating' ? ' selected' : '') + '>Rating</option><option value="show_of_hands"' + (S.pilotKind === 'show_of_hands' ? ' selected' : '') + '>Show of hands</option></select>';
            }
            function piRoomHtml(s, t0, kindSel) {
                // the room column: the seats as a thread, the composer, the questions. Painted inline or into the popped-out window.
                var roster = ((s.group || {}).roster || []);
                kindSel = kindSel || piKindSel();
                var popBtn = piRoomPopped() ? '<button type="button" class="dtw-pi-pop" onclick="dtwRoomPopIn()" title="Bring the room back into the page">Back to page</button>'
                                            : '<button type="button" class="dtw-pi-pop" onclick="dtwRoomPopOut()" title="Open the room in its own window">Pop out</button>';
                var standing = Number((s.group || {}).size_people || 0);
                return '<div class="dtw-pi-h">The room <span class="n">' + roster.length + ' seated' + (standing ? ', standing for ' + fmtN(standing) + ' people' : '') + '</span>' + popBtn + '</div>'
                    + roomStandsForNote(s.group)
                    + (typeof assetsStripHtml === 'function' ? assetsStripHtml(s, true) : '')
                    + '<div id="dtwPiSeats" class="dtw-pi-seatlist">' + renderPilotSeats(s, t0) + '</div>'
                    + '<div class="dtw-pi-hr"></div><div class="dtw-pi-h">Ask the room at <span class="n" id="dtwPiAskClock">' + esc(clockFmt(t0)) + '</span></div>'
                    + '<div class="dtw-pi-sugg" id="dtwPiSugg">' + renderPilotSuggest(s, t0) + '</div>'
                    + '<input id="dtwPiQ" class="dtw-in" placeholder="Would you keep watching?" onkeydown="if(event.key===\'Enter\'){event.preventDefault();dtwPilotAsk();}">'
                    + '<div class="dtw-pi-askrow">' + kindSel + '<button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwPilotAsk()">Ask the room</button></div>'
                    + '<div id="dtwPiAskNote" class="dtw-status"></div>'
                    + '<div id="dtwPiThread">' + renderPilotThread(s) + '</div>';
            }
            // ---- the room in its own window (Jenna 2026-11-03: "pop the chat box out as its own window so you can really
            // interact and engage with them"). The page keeps the clock, the events and the questions; the window is the
            // room's surface: the same markup, painted there, with the inline handlers routed back to this page.
            function piRoomPopped() { var p = S.roomPop; return !!(p && p.win && !p.win.closed); }
            // A room popped out into its own window lives off this page: an update never refreshes under it.
            try { (window.cwHoldRefresh = window.cwHoldRefresh || []).push(function() { return piRoomPopped(); }); } catch (_) {}
            function piPopoutShims(win, html) {
                // every dtw* handler the page exposes answers in that window too (the markup's inline handlers call them by name)
                var names = {}; String(html || '').replace(/\b(dtw[A-Za-z0-9_]+)\s*\(/g, function(_, n) { names[n] = 1; return ''; });
                try { Object.keys(window).forEach(function(k) { if (/^dtw/.test(k) && typeof window[k] === 'function') names[k] = 1; }); } catch (_) {}
                Object.keys(names).forEach(function(n) { if (typeof window[n] === 'function' && typeof win[n] !== 'function') win[n] = function() { return window[n].apply(window, arguments); }; });
            }
            function piPaintPopout(html) {
                var p = S.roomPop; if (!p || !p.win || p.win.closed) return;
                try {
                    var box = p.win.document.getElementById('dtwPopRoom'); if (!box) return;
                    piPopoutShims(p.win, html);
                    var keep = piScrollKeep(box);
                    box.innerHTML = html;
                    piMinuteApply(box.querySelector('#dtwPiSeats'), false);
                    piScrollRestore(box, keep);
                    var seats = box.querySelector('#dtwPiSeats'); if (seats) { seats.setAttribute('data-k', piThreadKey(S.pilot || {}, playheadNow())); if (keep.thread != null) piThreadRestore(box, keep); else piThreadFollow(seats); }
                } catch (e) { console.warn('[pilot] popout paint', e); }
            }
            window.dtwRoomPopOut = function() {
                var s = S.pilot; if (!s) return;
                if (piRoomPopped()) { try { S.roomPop.win.focus(); } catch (_) {} return; }
                var w = null;
                try { w = window.open('', 'dtwRoom_' + String(s.id || 'room').replace(/[^A-Za-z0-9_]/g, ''), 'popup=yes,width=440,height=' + Math.min(900, Math.max(600, window.screen.availHeight - 80))); } catch (_) { w = null; }
                if (!w) { var n = el('dtwPiAskNote'); if (n) n.textContent = 'The browser blocked the window. Allow pop-ups for this site and try again.'; return; }
                var styles = Array.prototype.map.call(document.querySelectorAll('style'), function(st) { return st.textContent || ''; }).join('\n');
                var links = Array.prototype.map.call(document.querySelectorAll('link[rel="stylesheet"], link[rel="preconnect"], link[rel="preload"]'), function(l) { return l.outerHTML; }).join('');
                var theme = document.body.getAttribute('data-theme') || '';
                var doc = w.document;
                doc.open();
                doc.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><base href="' + location.origin + '/"><title>' + esc((s.title || 'The room') + ': the room') + '</title>' + links + '<style>' + styles + '</style><style>html,body{margin:0;background:#0C1618;} body[data-theme="light"]{background:#E9E8E1;}</style></head>'
                    + '<body' + (theme ? ' data-theme="' + esc(theme) + '"' : '') + '><div id="digitalTwinsIQView" class="dtw-popout" style="display:flex;flex-direction:column;"><div class="dtw-body"><div class="dtw-card"><div class="dtw-pi-room"><div class="dtw-pi-col side room" id="dtwPopRoom"></div></div></div></div></div></body></html>');
                doc.close();
                S.roomPop = { win: w, id: s.id };
                try {
                    // the room's seats answer in that window too: hover follows the line, a click on the photo opens who they
                    // are (a remark in the chat never does), a click on the rest of the row pins, Escape closes the card; the
                    // card lives in that window, made there by piBioCard on first use
                    doc.addEventListener('mouseover', piSeatRowOver, true);
                    doc.addEventListener('focusin', piSeatRowOver, true);
                    doc.addEventListener('mouseout', piSeatRowOut, true);
                    doc.addEventListener('focusout', piSeatRowOut, true);
                    doc.addEventListener('click', piSeatRowClick, true);
                    doc.addEventListener('keydown', piSeatRowKey, true);
                    w.addEventListener('pagehide', function() { piBioHide(); setTimeout(function() { if (!piRoomPopped()) { S.roomPop = null; if (S.pilot) renderPilotConsole(); } }, 60); });
                } catch (_) {}
                if (S.roomPopTimer) { try { clearInterval(S.roomPopTimer); } catch (_) {} }
                S.roomPopTimer = setInterval(function() { if (S.roomPop && (!S.roomPop.win || S.roomPop.win.closed)) { clearInterval(S.roomPopTimer); S.roomPopTimer = null; S.roomPop = null; if (S.pilot) renderPilotConsole(); } }, 1500);
                renderPilotConsole();
                try { w.focus(); } catch (_) {}
            };
            window.dtwRoomPopIn = function() {
                var p = S.roomPop; S.roomPop = null; piBioHide();
                if (S.roomPopTimer) { try { clearInterval(S.roomPopTimer); } catch (_) {} S.roomPopTimer = null; }
                if (p && p.win && !p.win.closed) { try { p.win.close(); } catch (_) {} }
                if (S.pilot) renderPilotConsole();
            };
            window.addEventListener('beforeunload', function() { if (piRoomPopped()) { try { S.roomPop.win.close(); } catch (_) {} } });
            // ---- the test takes the screen while the reader is in it (Jenna 2026-11-03; 2026-11-05: "this part is supposed to
            // not be there when I scroll"). Once the body has scrolled past the masthead's own height the masthead folds, the
            // test bar locks at the top and the page scrolls freely; scrolling back up to the very top (or a wheel up while
            // there, or the masthead button) brings the twin and its tabs back, and nothing refolds until the reader has
            // scrolled past the masthead again. A fold never moves what is under the reader's eyes: the body's top edge rises by
            // the masthead's height, so the scroll position drops by the same amount in the same frame, with the masthead's
            // transition off for that frame. (The first version folded at 56px with the transition on, which pushed the page by
            // the masthead's height on every fold and unfolded only at an exact top the reader could not hold: the bounce. The
            // one after it never refolded, so the masthead stuck once the top had been visited.) At the top the masthead takes
            // its room back and the test slides down under it; the reader is at the top, so nothing refolds. Play (and the
            // PLAYING button) settles the page on the player once. A repaint never changes the fold; the chat follows inside
            // its own box.
            function piFocusOn() { return !!S.pilot && !!PI_HOST[piTab()] && !!document.querySelector('.dtw-pane.active .dtw-pi-head'); }
            function piFocusQuiet(body, ms) { S.piScrollLast = body.scrollTop; S.piUpRun = 0; S.piQuietUntil = Date.now() + ms; }   // our own scroll writes never read as the reader's move
            function piFocusScroll(settle) {
                var v = el('digitalTwinsIQView'); if (!v) return;
                var body = v.querySelector('.dtw-body'); if (!body) return;
                if (!piFocusOn()) { v.classList.remove('dtw-focus'); return; }
                if (settle === true) { v.classList.add('dtw-focus'); body.scrollTop = 0; piFocusQuiet(body, 800); }
            }
            function piFoldHeld(v, body) {
                // the masthead folds with the page held still: the body's top edge is measured, the masthead folds without its
                // transition, and the scroll position moves by the exact shift of that edge before the frame paints
                var spine = v.querySelector('.dtw-spine'), top0 = body.getBoundingClientRect().top;
                if (spine) spine.style.transition = 'none';
                v.classList.add('dtw-focus');
                body.scrollTop += body.getBoundingClientRect().top - top0;
                piFocusQuiet(body, 400);
                if (spine) requestAnimationFrame(function() { spine.style.transition = ''; });
            }
            function piFocusOnScroll(e) {
                var v = el('digitalTwinsIQView'), body = v && v.querySelector('.dtw-body'); if (!v || !body) return;
                if (!piFocusOn()) { v.classList.remove('dtw-focus'); return; }   // no test on the pane: the twin shows
                var st = body.scrollTop, was = S.piScrollLast == null ? st : S.piScrollLast; S.piScrollLast = st;
                if (Date.now() < (S.piQuietUntil || 0)) { S.piUpRun = 0; return; }
                S.piUpRun = st < was ? (S.piUpRun || 0) + (was - st) : (st > was ? 0 : (S.piUpRun || 0));   // how far the reader has come up since they last went down
                var folded = v.classList.contains('dtw-focus');
                if (e && e.type === 'wheel') {
                    if (folded && e.deltaY < 0 && st <= 0 && !piInnerScroller(e.target, body)) { v.classList.remove('dtw-focus'); piFocusQuiet(body, 400); }
                    return;
                }
                if (folded) { if (st <= 2 && S.piUpRun > 8) { v.classList.remove('dtw-focus'); piFocusQuiet(body, 400); } return; }   // the reader scrolled up to the top (a nudge of a few px right after a fold is not that; a slowing fling that ends there is)
                var spine = v.querySelector('.dtw-spine');
                if (st > Math.max(56, spine ? spine.offsetHeight : 0)) piFoldHeld(v, body);   // past the masthead's own height: below that a held fold has no room
            }
            function piInnerScroller(t, body) {
                // a wheel inside a box that scrolls on its own (the chat, a card) is that box's, not the page's
                for (; t && t !== body && t.nodeType === 1; t = t.parentNode) {
                    var o = getComputedStyle(t).overflowY;
                    if ((o === 'auto' || o === 'scroll') && t.scrollHeight > t.clientHeight + 1) return true;
                }
                return false;
            }
            window.dtwPilotUnfocus = function() {
                var v = el('digitalTwinsIQView'), body = v && v.querySelector('.dtw-body');
                if (v) v.classList.remove('dtw-focus');
                if (body) { body.scrollTop = 0; piFocusQuiet(body, 800); }   // to the top at once: a smooth scroll is cut short while the masthead's return resizes the body, and its own scroll never refolds
            };
            (function() { var b = document.querySelector('#digitalTwinsIQView .dtw-body'); if (b && !b._piFocusWired) { b._piFocusWired = true; b.addEventListener('scroll', piFocusOnScroll, { passive: true }); b.addEventListener('wheel', piFocusOnScroll, { passive: true }); } })();
            // ---- what the reader opened, and where they scrolled, survive a repaint (Jenna 2026-11-04: "these dont stay open
            // when I click one if something else is happening they should"). The test is repainted whole whenever its session
            // refreshes: a question the room was answering lands, a forecast attaches, the norm changes, the room pops out or
            // back. The folds (Scorecard in full, Diagnostics, Moderator guide, Recut, comps, talent and the brief, the room's
            // Earlier questions) keep their open state on S by test and fold, written on toggle and applied on paint; the page
            // and every scroller inside the test go back to where they were; the chat keeps following only when it was at its end.
            function piFoldKey(sid, id) { return String(sid || '') + '|' + String(id || ''); }
            function piFoldOpen(s, id) { return !!((S.piFolds || {})[piFoldKey((s || {}).id, id)]); }
            function piFold(s, id, label, body) {
                if (!body) return '';
                return '<details class="dtw-pi-more" data-fold="' + esc(id) + '" data-sid="' + esc((s || {}).id || '') + '"' + (piFoldOpen(s, id) ? ' open' : '') + ' ontoggle="dtwPilotFoldToggle(this)"><summary>' + label + '</summary>' + body + '</details>';
            }
            window.dtwPilotFoldToggle = function(d) {
                if (!d || !d.getAttribute) return;
                S.piFolds = S.piFolds || {};
                S.piFolds[piFoldKey(d.getAttribute('data-sid'), d.getAttribute('data-fold'))] = !!d.open;
                if (d.open) drawCompareChart();   // a chart inside a closed fold has no width to draw on; it draws when the fold opens
            };
            function piScrollKeep(root) {
                var m = { page: null, ids: {}, thread: null }; if (!root) return m;
                try {
                    var doc = root.ownerDocument || document, body = doc.querySelector('#digitalTwinsIQView .dtw-body');
                    if (body && body.scrollTop > 0) m.page = body.scrollTop;
                    root.querySelectorAll('[id]').forEach(function(n) { if (n.id && n.scrollTop > 0) m.ids[n.id] = n.scrollTop; });
                    var seats = root.querySelector('#dtwPiSeats'), th = seats && seats.querySelector('.dtw-pi-thread');
                    if (th && seats.getAttribute('data-k') && (th.scrollHeight - th.scrollTop - th.clientHeight) >= 28) m.thread = th.scrollTop;
                } catch (_) {}
                return m;
            }
            function piScrollRestore(root, m) {
                if (!root || !m) return;
                try {
                    var doc = root.ownerDocument || document, body = doc.querySelector('#digitalTwinsIQView .dtw-body');
                    if (body && m.page != null) body.scrollTop = m.page;
                    Object.keys(m.ids).forEach(function(id) { var n = doc.getElementById(id); if (n && root.contains(n)) n.scrollTop = m.ids[id]; });
                } catch (_) {}
            }
            function piThreadRestore(root, m) {
                if (!root || !m || m.thread == null) return;
                try { var seats = root.querySelector('#dtwPiSeats'), th = seats && seats.querySelector('.dtw-pi-thread'); if (th) th.scrollTop = m.thread; } catch (_) {}
            }
            function renderPilotPlayer(s) {
                var roster = ((s.group || {}).roster || []);
                var preparing = s.status === 'preparing';
                var ready = !preparing && (s.status === 'ready' || s.status === 'playing' || s.status === 'stopped' || ((s.dial || {}).value || []).length);
                var t0 = Number((S.pilotClock || {}).t || 0);
                var prep = preparing ? ('<div class="dtw-pi-prep"><div><b>' + (isLiveSess(s) && ((s.live || {}).frames || []).length ? 'Scoring.' : 'Preparing the cut.') + '</b> ' + esc(s.prepare_stage || (isLiveSess(s) ? 'The room is scoring what it watched. A minute or two.' : 'The room is watching it scene by scene. Play is ready the moment it has.')) + '</div><div class="bar"><i></i></div></div>') : '';
                var fmt = s.format || (s.scorecard || {}).format || '';
                var metaBits = [esc(piModeLabel(s)) + (s.rough_cut ? ', rough cut' : (s.mode === 'pilot' || s.mode === 'feature' || s.mode === 'episode' || s.mode === 'binge') ? ', locked cut' : '') + (s.critics_room ? ', critics room' : '')];
                if (fmt && fmt !== s.mode) metaBits.push('Read as a ' + esc(fmt === 'feature' ? 'feature' : fmt.replace('_', ' ')));
                metaBits.push(esc(s.cut_label || 'Cut A'));
                metaBits.push('Compared to: ' + esc(piComparedLabel(piNormChoice(s))));
                if (s.lineage && s.lineage.parent) metaBits.push('<button type="button" class="dtw-link" onclick="dtwPilotPick(\'' + esc(s.lineage.parent) + '\')">against ' + esc(s.lineage.parent_cut_label || 'Cut A') + '</button>');
                var controls = piReplayable(s) ? piTransportHtml(s) : (piFinished(s) && !preparing) ? '<div class="dtw-pi-controls"><span class="dtw-pi-note" id="dtwPiNote">Stopped. This test kept no second-by-second readings to run back through.</span><span class="dtw-pi-clock" id="dtwPiClock">' + esc(clockFmt(t0)) + '</span></div>' : '<div class="dtw-pi-controls"><span class="dtw-pi-note" id="dtwPiNote">' + esc(preparing ? 'Preparing the cut' : (s.prepare_note || (ready ? 'Ready to play' : 'Preparing the cut'))) + '</span><button type="button" class="dtw-btn dtw-btn-primary" id="dtwPiPlayBtn" onclick="dtwPilotPlay()"' + (preparing ? ' disabled' : '') + '>' + (s.status === 'playing' ? 'Playing' : 'Play') + '</button><button type="button" class="dtw-btn" onclick="dtwPilotStop()">Stop</button><span class="dtw-pi-clock" id="dtwPiClock">' + esc(clockFmt(t0)) + '</span></div>';
                // the controls ride in the locked test bar (2026-11-03); the meta line stays over the stage
                var topbar = '<div class="dtw-pi-topbar"><div class="meta">' + metaBits.map(function(b) { return '<span>' + b + '</span>'; }).join('') + '</div></div>';
                var live = isLiveSess(s) && (watchMedia(s) || {}).kind !== 'video', platform = (s.media || {}).platform_label || 'the service';
                var hint = preparing ? (live && liveFinished(s) === false && (s.live || {}).frames && (s.live || {}).frames.length ? 'The room is scoring what it watched.' : 'The room is watching the cut.')
                    : (live ? (s.status === 'playing' ? ('Open the episode on ' + platform + ', press play there, then mirror it here. The room reacts to what is on screen.' + (piPlannedRuntime(s) ? ' It stops on its own at ' + clockFmt(piPlannedRuntime(s)) + '.' : '')) : (s.status === 'stopped' ? 'Stopped.' : 'Press Play to seat the room. Then open the episode on ' + platform + ' and mirror it here.' + (piPlannedRuntime(s) ? ' It stops on its own at ' + clockFmt(piPlannedRuntime(s)) + '.' : '')))
                    : (watchMedia(s) ? ('Press Play. The room watches with you' + ((s.media || {}).platform_label ? ' on ' + (s.media || {}).platform_label : '') + '.') : 'The room is seated. Press Play when you are ready.'));
                var slateBtns = (watchMedia(s) && (s.media || {}).url ? '<a class="dtw-btn dtw-pi-open" href="' + esc((s.media || {}).url) + '" target="_blank" rel="noopener">' + esc((s.media || {}).open_label || 'Open the episode') + '</a>' : '')
                    + (live && s.status === 'playing' ? '<button type="button" class="dtw-btn dtw-btn-primary dtw-pi-open" onclick="dtwPilotMirror()">Mirror it here</button>' : '');
                var stage = '<div class="dtw-pi-stage"><video id="dtwPiVideo" playsinline controls controlsList="nodownload" disablePictureInPicture preload="metadata" style="display:none" oncontextmenu="return false"></video><iframe id="dtwPiFrame" class="dtw-pi-frame" title="The episode" allow="autoplay; fullscreen; encrypted-media; picture-in-picture" referrerpolicy="no-referrer-when-downgrade" style="display:none"></iframe><div id="dtwPiSlate" class="dtw-pi-slate"><div class="t">' + esc((s.media || {}).title || s.title || 'The cut') + '</div><div class="c" id="dtwPiSlateClock">' + esc(clockFmt(t0)) + '</div><div class="s" id="dtwPiOn"></div><div class="s">' + esc(hint) + '</div>' + (slateBtns ? '<div class="dtw-pi-slatebtns">' + slateBtns + '</div>' : '') + '</div></div>';
                var center = '<div class="dtw-pi-col center">' + topbar + stage + prep
                    + '<div class="dtw-pi-dialwrap"><canvas id="dtwPiDial"></canvas><div class="dtw-pi-dialnum" id="dtwPiDialNum"></div></div>'
                    + (piReplayable(s) ? piScrubHtml(s) : '')
                    + '<div class="dtw-pi-ticker" id="dtwPiTicker" aria-label="Minutes"></div>'
                    + splitLegend(s)
                    + (piReplayable(s) ? '<div class="dtw-pi-now" id="dtwPiNow">' + piNowHtml(s, t0) + '</div>' : '')
                    + '<div class="dtw-pi-marks" id="dtwPiMarks">' + renderPilotMarks(s, t0) + '</div></div>';
                var kindSel = piKindSel();
                var roomInner = piRoomHtml(s, t0, kindSel);
                var popped = piRoomPopped();
                var room = popped
                    ? '<div class="dtw-pi-col side room popped"><div class="dtw-pi-h">The room <span class="n">' + roster.length + ' seated</span></div><div class="dtw-pi-fn">The room is open in its own window. The conversation goes on there.</div><button type="button" class="dtw-btn" onclick="dtwRoomPopIn()">Bring it back here</button></div>'
                    : '<div class="dtw-pi-col side room">' + roomInner + '</div>';
                S.roomPopHtml = popped ? roomInner : '';
                var purge = '<label class="dtw-pi-switch"><input type="checkbox" id="dtwPiPurge"' + (s.purge_media_after ? ' checked' : '') + ' onchange="dtwPilotPurgeToggle(this.checked)"> Remove the file when the test is done' + ((s.media || {}).purged_at ? ' <span class="dtw-muted">(removed; the read stays)</span>' : '') + '</label>';
                // one report in every format (Jenna 2026-11-04): the deck, the PDF and the CSV are drawn from the same pages, the room
                // included, so nothing differs between them but the file type; the older PDF and Deck buttons pointed at a thinner export and are gone
                var exp = S.key ? ('<div class="dtw-actions"><a class="dtw-btn dtw-btn-primary" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/pilot/' + esc(s.id) + '/report.pptx" title="The full report as a deck: the scorecard against its norm, the dial, the room, every question, the forecast and the conclusions">Report (deck)</a><a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/pilot/' + esc(s.id) + '/report.pdf" title="The same report as a PDF">Report (PDF)</a><a class="dtw-btn" onclick="return dtwDownload(this, event)" href="/api/digital-twins/' + esc(S.key) + '/pilot/' + esc(s.id) + '/export.csv" title="Every number in the report, block by block, plus the dial second by second and every reaction">CSV</a><button type="button" class="dtw-btn" onclick="dtwPilotDuplicate()" title="Duplicate this test">Duplicate this test</button><button type="button" class="dtw-btn" onclick="dtwPilotDelete()">Delete this test</button></div>') : '';
                var scoredNow = s.status === 'stopped' && !!s.scorecard;
                var full = scoredNow ? renderScorecard(s) : '', diag = scoredNow ? (renderModeRead(s) + renderDiagnostics(s)) : '', extras = renderPilotExtras(s);
                var guide = renderGuide(s);
                var more = piFold(s, 'scorecard', 'Scorecard in full', full)
                    + piFold(s, 'diagnostics', 'Diagnostics', diag)
                    + piFold(s, 'guide', 'Moderator guide', guide)
                    + piFold(s, 'extras', 'Recut, comps, talent and the brief', extras);
                var twinName = String(((S.twin || {}).name || (S.twin || {}).subject || ((S.twin || {}).meta || {}).name || ((S.twin || {}).meta || {}).subject || 'the twin'));
                var head = '<div class="dtw-pi-head"><div class="dtw-pi-head-l">'
                    + '<button type="button" class="dtw-pi-reveal" onclick="dtwPilotUnfocus()" title="Show the twin and its tabs again">&#9650; ' + esc(twinName) + '</button>'
                    + '<div class="dtw-eyebrow">' + esc(PI_TAB_LABEL[piTabFor(s.mode)] || 'Pilot Testing') + '</div>'
                    + '<div class="dtw-fg-rtwrap"><input id="dtwPiName" class="dtw-fg-rt" value="' + esc(s.title || '') + '" onblur="dtwPilotRename()"><button type="button" class="dtw-link" onclick="dtwPilotNew()">New test</button></div></div>'
                    + controls
                    + '<div class="dtw-pi-head-r"><button type="button" class="dtw-btn dtw-btn-sm dtw-pi-exit" onclick="dtwPilotClose()" title="Leave this test. A test still playing is stopped and scored first.">Exit test</button>'
                    + '<button type="button" class="dtw-btn dtw-btn-sm dtw-pi-dup" onclick="dtwPilotDuplicate()" title="Duplicate this test: every input into a new setup, none of the results. Change one thing and start it.">Duplicate</button>'
                    + '<button type="button" class="dtw-btn dtw-btn-sm dtw-pi-del" onclick="dtwPilotDelete()" title="Delete this test. An admin can restore it for 30 days.">Delete</button></div></div>';
                return '<div class="dtw-card">' + head
                    + '<div class="dtw-pi-room">' + renderPilotScores(s) + center + room + '</div>'
                    + '<div class="dtw-pi-foot">' + exp + purge + '</div>'
                    + more + '</div>';
            }
            function renderPilotConsole() {
                var h = piHostEl(); if (!h) return;
                if (GL_OPTIONS.indexOf(piTab()) >= 0 && S.glOption !== piTab()) { S.glOption = piTab(); syncGreenlightOptions(); }
                if (!S.twin) { S.piPainted = null; h.innerHTML = renderPilotStart(); return; }
                if (!S.pilot) { S.piPainted = null; h.innerHTML = renderPilotStart(); piAfterStartPaint(); return; }
                Object.keys(PI_HOST).forEach(function(t) { var o = el(PI_HOST[t]); if (o && o !== h) o.innerHTML = ''; });
                var keep = S.piPainted === S.pilot.id ? piScrollKeep(h) : null;   // the same test repainted: the reader stays put
                h.innerHTML = renderPilotPlayer(S.pilot);
                S.piPainted = S.pilot.id;
                if (piRoomPopped() && S.roomPopHtml) piPaintPopout(S.roomPopHtml);
                piScrollRestore(h, keep);
                piFocusScroll(!keep && S.pilot.status === 'playing');   // a test already playing when it is first shown settles once; a repaint leaves the fold alone
                if (piReplayable(S.pilot)) { if (S.pilotClock.sid !== S.pilot.id) { S.pilotClock.sid = S.pilot.id; S.pilotClock.t = piStopPoint(S.pilot); S.pilotReplay.on = false; } }
                else { S.pilotClock.sid = S.pilot.id; if (S.pilotReplay.on) piReplayHalt(true); }
                bindPilotMedia();
                schedulePilotPortraits();
                revealPilot(playheadNow());
                piThreadRestore(h, keep);
                drawCompareChart();
                if (S.pilot.status === 'preparing') pollPilotPrepare();
                pollPilotQuestions();   // a question still with the room keeps landing after a reload
                try { remember(); } catch (_) {}
            }
            function pollPilotPrepare() {
                var s = S.pilot; if (!s || !S.key || s.status !== 'preparing') return;
                if (S.pilotPrep && S.pilotPrep.timer) { try { clearTimeout(S.pilotPrep.timer); } catch (_) {} }
                S.pilotPrep = S.pilotPrep || {};
                S.pilotPrep.timer = setTimeout(async function() {
                    if (!S.pilot || S.pilot.id !== s.id || S.pilot.status !== 'preparing') return;
                    var jid = S.pilot.prepare_job_id, stage = '';
                    if (jid) { var j = await apiRaw('/jobs/' + encodeURIComponent(jid), { method: 'GET' }); if (j.ok) { stage = ((j.data || {}).job || {}).stage || ''; var st = ((j.data || {}).job || {}).status || ''; if (st === 'error') stage = 'The cut could not be prepared. Press Play and the room runs on the clock.'; } }
                    var r = await apiRaw('/' + encodeURIComponent(S.key) + '/pilot/' + encodeURIComponent(s.id), { method: 'GET' });
                    if (r.ok && r.data.session) {
                        adoptPilot(r.data.session);
                        if (S.pilot.status !== 'preparing') { renderPilotConsole(); return; }
                    }
                    var p = document.querySelector('.dtw-pane.active .dtw-pi .dtw-pi-prep > div'); if (p && stage) p.innerHTML = '<b>Preparing the cut.</b> ' + esc(stage);
                    pollPilotPrepare();
                }, 4000);
            }
            window.dtwPilotKind = function(k) { S.pilotKind = (S.pilotKind === k) ? '' : k; document.querySelectorAll('#dtwPiKinds .dtw-chip').forEach(function(b) { b.classList.toggle('on', b.getAttribute('data-k') === S.pilotKind); }); };
            window.dtwPilotGenre = async function(g) {
                var s = S.pilot; if (!s || !S.key) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/norm', { method: 'POST', body: JSON.stringify({ genre: g || '' }) }); if (d.session) { adoptPilot(d.session); renderPilotConsole(); } } catch (e) { var n = el('dtwPiNote'); if (n) n.textContent = e.message; }
            };
            window.dtwPilotPurgeToggle = async function(on) {
                var s = S.pilot; if (!s || !S.key) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/rename', { method: 'POST', body: JSON.stringify({ purge_media_after: !!on }) }); if (d.session) adoptPilot(d.session); } catch (e) { var n = el('dtwPiNote'); if (n) n.textContent = e.message; }
            };
            window.dtwPilotPick = function(sid) {
                var s = ((S.twin || {}).pilot_sessions || []).filter(function(x) { return x.id === sid; })[0] || null;
                S.pilot = s;
                S.pilotMedia = null; S.pilotCompare = null;
                var tab = piTabFor((s || {}).mode);
                S.piTab = tab;
                dtwTab(tab); renderPilotConsole();
                if (s && s.lineage && s.lineage.parent && s.scorecard) dtwPilotCompare();
            };
            // ---- cohorts (Jenna, 2026-11-02): cut the audience into named groups at setup, each with its composition
            // in words (from the twin's base: men, women, men with children, Spanish speakers) and the panelists wanted
            // in it; every cohort stands for its own share of the twin, and the results split by cohort.
            var PI_COHORT_COLORS = ['#E682FF', '#3358FF', '#C7F23E', '#B7B3D8', '#FF9F43', '#2ED8C3', '#F25C54', '#8E3FA8', '#FFD166', '#06D6A0', '#EF476F', '#73C2FB'];
            var PI_COHORT_MAX = 20;
            // The standard demographics a cohort is picked from (Jenna, 2026-11-03: "selected from our standard
            // demographics"). Generated from migration/digital_twin_demos.py; scripts/test_digital_twin_demos.py
            // holds the two in step. Within a category the picks combine as either; across categories as both.
            var DTW_DEMOS = [{"key": "GENDER", "label": "Gender", "values": ["Female", "Male", "Non-Binary", "Trans Female", "Trans Male"]}, {"key": "AGE", "label": "Age", "values": ["17 and Under", "18-24", "25-34", "35-44", "45-54", "55-64", "65 or Older"]}, {"key": "ETHNICITY", "label": "Ethnicity", "values": ["White", "Hispanic or Latino", "Black or African American", "Asian", "Another Race/Ethnicity"]}, {"key": "PRIMARY_LANGUAGE", "label": "Primary language", "values": ["English", "Spanish", "Chinese", "Other"]}, {"key": "INCOME", "label": "Household income", "values": ["Less than $25,000", "$25,000 - $49,999", "$50,000 - $74,999", "$75,000 - $99,999", "$100,000 - $149,999", "$150,000 - $249,999", "$250,000 or More"]}, {"key": "EDUCATION", "label": "Education", "values": ["High School or Less", "Some College / Associate Degree", "Bachelors Degree", "Graduate or Professional Degree"]}, {"key": "RELATIONSHIP", "label": "Relationship", "values": ["Single", "In a Relationship", "Married", "Divorced or Separated", "Widowed"]}, {"key": "PARENTAL_STATUS", "label": "Children at home", "values": ["Has Children", "No Children"]}, {"key": "NUMBER_OF_CHILDREN", "label": "Number of children", "values": ["1", "2", "3", "4+"]}, {"key": "AGE_OF_CHILDREN", "label": "Age of children", "values": ["Under 3", "3 to 5", "6 to 10", "11 to 13", "14 to 17"]}, {"key": "OCCUPATION", "label": "Occupation", "values": ["Management, Business & Professional", "Healthcare Practitioners or Support", "Sales & Retail", "Education or Library Services", "Service & Hospitality", "Science, Technology & Technical Professions", "Skilled Trades/Construction or Maintenance", "Agriculture & Outdoor", "Transportation & Logistics", "Manufacturing & Production", "Public Safety & Protective Services", "Legal", "Other"]}, {"key": "SEXUAL_ORIENTATION", "label": "Sexual orientation", "values": ["Straight / Heterosexual", "Gay or Lesbian", "Another Sexual Orientation"]}];
            var DTW_DEMO_BY_KEY = {}; DTW_DEMOS.forEach(function(d) { DTW_DEMO_BY_KEY[d.key] = d; });
            function piCohortSeatsDefault(n) { return n <= 9 ? 4 : (n <= 13 ? 3 : 2); }
            var PI_COHORT_EXAMPLES = [['Men', 'men'], ['Women', 'women'], ['Dads', 'men with children'], ['Spanish speakers', 'Spanish speakers'], ['Under 35', 'adults 18 to 34'], ['Parents', 'parents'], ['College grads', 'college graduates'], ['Suburban', 'suburban']];
            // ---- the selection in words (a mirror of digital_twin_demos.demos_words; the server rewrites it anyway)
            var PI_AGE_LO = { '17 and Under': 13, '18-24': 18, '25-34': 25, '35-44': 35, '45-54': 45, '55-64': 55, '65 or Older': 65 };
            var PI_AGE_HI = { '17 and Under': 17, '18-24': 24, '25-34': 34, '35-44': 44, '45-54': 54, '55-64': 64, '65 or Older': 80 };
            function piRuns(values, order) {
                var idx = (values || []).map(function(v) { return order.indexOf(v); }).filter(function(i) { return i >= 0; }).sort(function(a, b) { return a - b; });
                var runs = [], cur = [];
                idx.forEach(function(i) { if (cur.length && i !== cur[cur.length - 1] + 1) { runs.push(cur); cur = []; } cur.push(i); });
                if (cur.length) runs.push(cur);
                return runs.map(function(r) { return r.map(function(i) { return order[i]; }); });
            }
            function piAgeWords(values) {
                return piRuns(values, DTW_DEMO_BY_KEY.AGE.values).map(function(run) {
                    var a = run[0], z = run[run.length - 1];
                    if (a === '17 and Under' && z === '17 and Under') return '17 and under';
                    if (z === '65 or Older') return a === '17 and Under' ? 'all ages' : (PI_AGE_LO[a] + ' and older');
                    if (a === '17 and Under') return PI_AGE_HI[z] + ' and under';
                    return PI_AGE_LO[a] + ' to ' + PI_AGE_HI[z];
                }).join(' or ');
            }
            function piIncomeWords(values) {
                var order = DTW_DEMO_BY_KEY.INCOME.values;
                var lo = { 'Less than $25,000': 0, '$25,000 - $49,999': 25, '$50,000 - $74,999': 50, '$75,000 - $99,999': 75, '$100,000 - $149,999': 100, '$150,000 - $249,999': 150, '$250,000 or More': 250 };
                var hi = { 'Less than $25,000': 25, '$25,000 - $49,999': 50, '$50,000 - $74,999': 75, '$75,000 - $99,999': 100, '$100,000 - $149,999': 150, '$150,000 - $249,999': 250, '$250,000 or More': 0 };
                return piRuns(values, order).map(function(run) {
                    var a = lo[run[0]], z = hi[run[run.length - 1]];
                    if (a === 0 && z === 0) return '';
                    if (a === 0) return 'household income under $' + z + 'k';
                    if (z === 0) return 'household income $' + a + 'k and up';
                    return 'household income $' + a + 'k to $' + z + 'k';
                }).filter(Boolean).join(' or ');
            }
            function piDemosWords(demos) {
                demos = demos || {};
                var bits = [];
                var gw = { 'Female': 'women', 'Male': 'men', 'Non-Binary': 'non-binary people', 'Trans Female': 'trans women', 'Trans Male': 'trans men' };
                if ((demos.GENDER || []).length) bits.push(demos.GENDER.map(function(v) { return gw[v] || v.toLowerCase(); }).join(' or '));
                if ((demos.AGE || []).length) bits.push(piAgeWords(demos.AGE));
                if ((demos.ETHNICITY || []).length) bits.push(demos.ETHNICITY.join(' or '));
                if ((demos.PRIMARY_LANGUAGE || []).length) { var L = demos.PRIMARY_LANGUAGE.filter(function(v) { return v !== 'Other'; }); bits.push(L.length ? L.join(' or ') + ' speakers' : 'speakers of another language'); }
                if ((demos.INCOME || []).length) { var iw = piIncomeWords(demos.INCOME); if (iw) bits.push(iw); }
                if ((demos.EDUCATION || []).length) { var ew = { 'High School or Less': 'high school or less', 'Some College / Associate Degree': 'some college', 'Bachelors Degree': 'college graduates', 'Graduate or Professional Degree': 'graduate degrees' }; bits.push(demos.EDUCATION.map(function(v) { return ew[v] || v.toLowerCase(); }).join(' or ')); }
                if ((demos.RELATIONSHIP || []).length) bits.push(demos.RELATIONSHIP.map(function(v) { return v.toLowerCase(); }).join(' or '));
                if ((demos.PARENTAL_STATUS || []).length === 1) bits.push(demos.PARENTAL_STATUS[0] === 'Has Children' ? 'with children at home' : 'without children');
                if ((demos.NUMBER_OF_CHILDREN || []).length) bits.push(demos.NUMBER_OF_CHILDREN.join(' or ') + (demos.NUMBER_OF_CHILDREN.length === 1 && demos.NUMBER_OF_CHILDREN[0] === '1' ? ' child' : ' children'));
                if ((demos.AGE_OF_CHILDREN || []).length) bits.push('children ' + demos.AGE_OF_CHILDREN.map(function(v) { return v.toLowerCase(); }).join(' or '));
                if ((demos.OCCUPATION || []).length) bits.push('working in ' + demos.OCCUPATION.map(function(v) { return v === 'Other' ? 'other fields' : v.toLowerCase(); }).join(' or '));
                if ((demos.SEXUAL_ORIENTATION || []).length) { var ow = { 'Straight / Heterosexual': 'straight', 'Gay or Lesbian': 'gay or lesbian', 'Another Sexual Orientation': 'another sexual orientation' }; bits.push(demos.SEXUAL_ORIENTATION.map(function(v) { return ow[v] || v.toLowerCase(); }).join(' or ')); }
                return bits.filter(Boolean).join(', ');
            }
            function piSuggestName(demos) {
                demos = demos || {};
                var bits = [];
                var gw = { 'Female': 'Women', 'Male': 'Men', 'Non-Binary': 'Non-binary people', 'Trans Female': 'Trans women', 'Trans Male': 'Trans men' };
                if ((demos.GENDER || []).length) bits.push(demos.GENDER.map(function(v, i) { return i ? (gw[v] || v).toLowerCase() : (gw[v] || v); }).join(' or '));
                if ((demos.AGE || []).length) bits.push(piAgeWords(demos.AGE));
                var L = (demos.PRIMARY_LANGUAGE || []).filter(function(v) { return v !== 'Other'; });
                if (L.length) bits.push(L.join(' or ') + ' speakers');
                if (!bits.length && (demos.ETHNICITY || []).length) bits.push(demos.ETHNICITY.join(' or '));
                if ((demos.PARENTAL_STATUS || []).join() === 'Has Children') bits.push('with kids');
                if (!bits.length) { var w = piDemosWords(demos); return w ? (w.charAt(0).toUpperCase() + w.slice(1)).slice(0, 60) : ''; }
                return bits.join(' ').slice(0, 60);
            }
            function piDemosCount(demos) { var n = 0; Object.keys(demos || {}).forEach(function(k) { n += (demos[k] || []).length; }); return n; }
            function piCohortDemosHtml(i, demos) {
                var chips = '';
                DTW_DEMOS.forEach(function(d) {
                    ((demos || {})[d.key] || []).forEach(function(v) {
                        chips += '<span class="dtw-pi-coh-chip"><span class="k">' + esc(d.label) + '</span>' + esc(v) + '<button type="button" aria-label="Remove ' + esc(v) + '" onclick="dtwCohortToggle(' + i + ', \'' + esc(d.key) + '\', \'' + esc(v).replace(/'/g, '&#39;') + '\')">&times;</button></span>';
                    });
                });
                var open = S.piCohortOpen === i;
                return '<div class="dtw-pi-coh-demos">' + chips
                    + (chips ? '' : '<span class="dtw-pi-coh-empty">Who is in this cohort:</span>')
                    + '<button type="button" class="dtw-pi-coh-add' + (open ? ' on' : '') + '" onclick="dtwCohortPick(' + i + ')">' + (chips ? 'Change' : '+ Pick demographics') + '</button></div>';
            }
            // The picker: one panel over the page with every standard demographic as a pill; a pick lands on the
            // cohort's row as a chip the moment it is made. Esc, the backdrop or Done closes it.
            function renderDemoPicker() {
                var root = el('digitalTwinsIQView'); if (!root) return;
                var i = S.piCohortOpen;
                var panel = el('dtwDemoPicker');
                if (i == null) { if (panel) panel.remove(); return; }
                var c = (S.piCohorts || [])[i] || {}; var demos = c.demos || {};
                var n = Number(S.piCohortN || 1);
                var html = '<div class="dtw-demo-panel" role="dialog" aria-modal="true" aria-label="Pick the demographics for this cohort">'
                    + '<div class="dtw-demo-head"><div><div class="dtw-eyebrow">Cohort ' + (i + 1) + ' of ' + n + '</div><h3>' + esc(c.name || 'Who is in this cohort') + '</h3></div>'
                    + '<button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwDemoPickerClose()">Done</button></div>'
                    + '<div class="dtw-demo-words">' + (piDemosCount(demos) ? 'This cohort is <b>' + esc(piDemosWords(demos)) + '</b>.' : 'Pick from the standard demographics below. More than one in a row means either of them; picks in different rows all have to be true.') + '</div>'
                    + '<div class="dtw-demo-grid">';
                DTW_DEMOS.forEach(function(d) {
                    var on = demos[d.key] || [];
                    html += '<div class="dtw-pi-dcat"><span class="k">' + esc(d.label) + (on.length ? ' <span style="letter-spacing:0;text-transform:none;color:var(--dtw-ink)">' + on.length + ' picked</span>' : '') + '</span><div class="pills">' + d.values.map(function(v) {
                        return '<button type="button" class="dtw-pill' + (on.indexOf(v) >= 0 ? ' on' : '') + '" aria-pressed="' + (on.indexOf(v) >= 0 ? 'true' : 'false') + '" onclick="dtwCohortToggle(' + i + ', \'' + esc(d.key) + '\', \'' + esc(v).replace(/'/g, '&#39;') + '\')">' + esc(v) + '</button>';
                    }).join('') + '</div></div>';
                });
                html += '</div><div class="dtw-pi-dcat-foot"><span>More than one in a row means either of them (18-24 or 25-34). Picks in different rows all have to be true (men who are 18 to 34).</span>'
                    + '<div class="acts">' + (piDemosCount(demos) ? '<button type="button" class="dtw-btn" onclick="dtwCohortClear(' + i + ')">Clear</button>' : '') + '<button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwDemoPickerClose()">Done</button></div></div></div>';
                if (!panel) {
                    panel = document.createElement('div'); panel.id = 'dtwDemoPicker'; panel.className = 'dtw-demo-backdrop';
                    panel.addEventListener('click', function(e) { if (e.target === panel) dtwDemoPickerClose(); });
                    root.appendChild(panel);
                }
                var keep = panel.querySelector('.dtw-demo-panel') ? panel.querySelector('.dtw-demo-panel').scrollTop : 0;
                panel.innerHTML = html;
                var inner = panel.querySelector('.dtw-demo-panel'); if (inner && keep) inner.scrollTop = keep;
            }
            window.dtwDemoPickerClose = function() { S.piCohortOpen = null; renderDemoPicker(); piRerenderCohorts(); };
            window.dtwCohortClear = function(i) { piCohortsSync(); var c = (S.piCohorts || [])[i]; if (c) { var wasAuto = !c.name || c.name === piSuggestName(c.demos || {}); c.demos = {}; if (wasAuto) c.name = ''; } piRerenderCohorts(); renderDemoPicker(); };
            document.addEventListener('keydown', function(e) { if (e.key === 'Escape' && S.piCohortOpen != null && el('dtwDemoPicker')) { e.preventDefault(); dtwDemoPickerClose(); } });
            function piCohortsSync() {
                // the names and seats as typed, into S.piCohorts (the picks already live there)
                var names = document.querySelectorAll('#dtwPiCohorts .dtw-pi-coh-name'), seats = document.querySelectorAll('#dtwPiCohorts .dtw-pi-coh-seats');
                var out = S.piCohorts || [];
                for (var i = 0; i < names.length; i++) {
                    out[i] = out[i] || {};
                    out[i].name = (names[i].value || '').trim();
                    out[i].seats = parseInt((seats[i] || {}).value || '0', 10) || out[i].seats || piCohortSeatsDefault(names.length);
                    out[i].demos = out[i].demos || {};
                }
                S.piCohorts = out;
            }
            function piRerenderCohorts() {
                var box = el('dtwPiCohorts'); if (!box) return;
                var tmp = document.createElement('div'); tmp.innerHTML = piCohortBuilderHtml();
                var fresh = tmp.querySelector('#dtwPiCohorts'); if (fresh) box.innerHTML = fresh.innerHTML;
                try { piAutosaveSoon(); } catch (_) {}
            }
            window.dtwCohortPick = function(i) {
                piCohortsSync();
                S.piCohortOpen = i;
                piRerenderCohorts();
                renderDemoPicker();
            };
            window.dtwCohortToggle = function(i, key, value) {
                piCohortsSync();
                var c = S.piCohorts[i] = S.piCohorts[i] || { name: '', seats: piCohortSeatsDefault(Number(S.piCohortN || 2)), demos: {} };
                c.demos = c.demos || {};
                var cur = c.demos[key] || [];
                var at = cur.indexOf(value);
                var wasAuto = !c.name || c.name === piSuggestName(c.demos);
                if (at >= 0) cur.splice(at, 1); else cur.push(value);
                var order = (DTW_DEMO_BY_KEY[key] || {}).values || [];
                cur.sort(function(a, b) { return order.indexOf(a) - order.indexOf(b); });
                if (cur.length) c.demos[key] = cur; else delete c.demos[key];
                if (wasAuto) c.name = piSuggestName(c.demos);     // a blank name follows the picks until it is typed over
                piRerenderCohorts();
                if (S.piCohortOpen === i) renderDemoPicker();
            };
            function piCohortBuilderHtml() {
                var n = Math.max(1, Math.min(PI_COHORT_MAX, Number(S.piCohortN || 1)));
                var custom = !!S.piCohortCustom || n > 6;
                var opts = [1, 2, 3, 4, 5, 6].map(function(k) { return '<option value="' + k + '"' + (k === n && !custom ? ' selected' : '') + '>' + (k === 1 ? 'One room, no cohorts' : k + ' cohorts') + '</option>'; }).join('')
                    + '<option value="custom"' + (custom ? ' selected' : '') + '>Custom</option>';
                // Custom (Jenna, 2026-11-03): type any number of cohorts, up to twenty
                var customBox = custom ? '<div class="dtw-pi-cohort-custom"><label class="dtw-field" for="dtwPiCohortCustom">How many cohorts</label><input id="dtwPiCohortCustom" class="dtw-in" inputmode="numeric" value="' + esc(n > 1 ? n : '') + '" placeholder="8" onchange="dtwPilotCohortN(this.value, true)" onkeydown="if(event.key===\'Enter\'){event.preventDefault();dtwPilotCohortN(this.value, true);}"></div>' : '';
                var rows = '';
                if (n > 1) {
                    rows = '<div class="dtw-pi-cohort-head"><span>Name, then who is in it</span><span>Seats</span></div>';
                    for (var i = 0; i < n; i++) {
                        var ex = PI_COHORT_EXAMPLES[i % PI_COHORT_EXAMPLES.length], saved = (S.piCohorts || [])[i] || {};
                        rows += '<div class="dtw-pi-cohort" data-ci="' + i + '"><input class="dtw-in dtw-pi-coh-name" placeholder="' + esc(ex[0]) + '" value="' + esc(saved.name || '') + '" maxlength="60" aria-label="Cohort name">'
                            + '<input class="dtw-in dtw-pi-coh-seats" inputmode="numeric" value="' + esc(saved.seats || piCohortSeatsDefault(n)) + '" aria-label="Seats in this cohort">'
                            + piCohortDemosHtml(i, saved.demos || {}) + '</div>';
                    }
                    rows += '<div class="dtw-pi-fn">Pick each cohort from the standard demographics: gender, age, ethnicity, language, income, education, relationship, children, occupation, orientation. Each cohort stands for its own share of the whole audience, and the dial, the scorecard and every answer split by cohort.</div>';
                }
                return '<label class="dtw-field">Cohorts</label><select id="dtwPiCohortN" class="dtw-in" onchange="dtwPilotCohortN(this.value)">' + opts + '</select><div class="dtw-pi-cohorts" id="dtwPiCohorts">' + customBox + rows + '</div>';
            }
            function piReadCohorts() {
                var n = Number(S.piCohortN || 1);
                if (n <= 1) return [];
                piCohortsSync();
                var out = [];
                for (var i = 0; i < n; i++) {
                    var c = (S.piCohorts || [])[i] || {};
                    var name = (c.name || '').trim(), demos = c.demos || {}, k = Number(c.seats) || 0;
                    var picked = piDemosCount(demos) > 0;
                    if (!name && !picked) continue;
                    if (!picked) return null;                       // a cohort needs at least one pick
                    out.push({ name: name || piSuggestName(demos), demos: demos, composition: piDemosWords(demos), seats: Math.max(1, Math.min(24, k || 4)) });
                }
                if (out.length < 2) return null;
                return out;
            }
            window.dtwPilotCohortN = function(v, typed) {
                try { piReadCohorts(); } catch (_) {}
                var wasCustom = !!S.piCohortCustom;
                if (String(v) === 'custom') {
                    // the picker's Custom: keep the typed count if there is one, else start at eight and wait for the number
                    S.piCohortCustom = true;
                    S.piCohortN = Math.max(2, Math.min(PI_COHORT_MAX, Number(S.piCohortN || 0) > 6 ? Number(S.piCohortN) : 8));
                } else if (typed) {
                    var k = parseInt(v || '0', 10) || 0;
                    if (k < 2) { fgToast('Type a number of cohorts from 2 to ' + PI_COHORT_MAX + '.'); return; }
                    S.piCohortCustom = true;
                    S.piCohortN = Math.min(PI_COHORT_MAX, k);
                } else {
                    S.piCohortCustom = false;
                    S.piCohortN = parseInt(v || '1', 10) || 1;
                }
                var box = el('dtwPiCohorts'); if (!box) return;
                var tmp = document.createElement('div'); tmp.innerHTML = piCohortBuilderHtml();
                var fresh = tmp.querySelector('#dtwPiCohorts'); if (fresh) box.innerHTML = fresh.innerHTML;
                if (S.piCohortCustom && !wasCustom && !typed) { var ci = el('dtwPiCohortCustom'); if (ci) { try { ci.focus(); ci.select(); } catch (_) {} } }
            };
            function piCohortKey(label) {
                // the split key the room gives a cohort (the same key live and scored, so a cut cohort survives the scoring)
                return 'cohort_' + (String(label || '').toLowerCase().replace(/[^0-9a-z]+/g, '_').replace(/^_+|_+$/g, '') || 'x');
            }
            function piSeatWeights(s) {
                // {seat: the people it stands for}: the seat's own figure (stands_for_people, else people); one each when the
                // room carries none, so the line is a plain average then
                var out = {}, every = true;
                (((s || {}).group || {}).roster || []).forEach(function(r) { var w = Number(r.stands_for_people || r.people || 0); if (!(w > 0)) every = false; out[String(r.seat)] = w; });
                if (!every) Object.keys(out).forEach(function(k) { out[k] = 1; });   // a seat without a figure: the room counts one each
                return out;
            }
            function piRosterCohorts(s) {
                // the cohorts the room was cut into, in seat order (two or more), as [{key, label, seats}]
                var out = [], by = {};
                (((s || {}).group || {}).roster || []).forEach(function(r) {
                    var lab = String(r.segment || '').replace(/\s+/g, ' ').trim(); if (!lab) return;
                    var k = piCohortKey(lab);
                    if (!by[k]) { by[k] = { key: k, label: lab, seats: [] }; out.push(by[k]); }
                    by[k].seats.push(Number(r.seat));
                });
                return out.length > 1 ? out : [];
            }
            function piCohortSplits(s) {
                s = s || {};
                var sp = (s.dial || {}).splits || {}, ts = (s.dial || {}).t || [], order = piRosterCohorts(s);
                function colorOf(label, i) {
                    // a cohort keeps its color from live to scored: the color follows the seat order, not the split order
                    var want = String(label || '').replace(/\s+/g, ' ').trim().toLowerCase(), at = -1;
                    for (var j = 0; j < order.length; j++) { if (order[j].label.toLowerCase() === want) { at = j; break; } }
                    return PI_COHORT_COLORS[(at >= 0 ? at : i) % PI_COHORT_COLORS.length];
                }
                if (!piLiveSeries(s)) {
                    return Object.keys(sp).filter(function(k) { return sp[k].group === 'cohort'; }).map(function(k, i) { return { key: k, label: sp[k].label || k, n: sp[k].n, value: sp[k].value || [], color: colorOf(sp[k].label, i) }; });
                }
                // the room is still watching (2026-11-04): each cohort's line is the mean of its seats' live readings, frame by
                // frame, the same source the People view draws from (the room line is the mean of every seat)
                var live = (s.live || {}).seat_dials || {}, last = ts.length ? ts[ts.length - 1] : '';
                var sig = [s.id, ts.length, last, order.map(function(c) { return c.key + ':' + c.seats.length; }).join('|')].join('#');
                var cache = S.piCohortCache || {};
                if (cache.sig === sig && cache.live === live) return cache.list;
                var list = order.map(function(c, i) {
                    // each seat counts by the people it stands for (Jenna 2026-11-20), the weights the server's line and the
                    // published split use; a cohort whose seats all stand for the same people is the plain average
                    var sums = {}, counts = {}, wts = piSeatWeights(s);
                    c.seats.forEach(function(seat) {
                        var w = wts[String(seat)] || 1;
                        (live[String(seat)] || []).forEach(function(p) { var tk = String(Number(p[0])), v = Number(p[1]); if (!isFinite(v)) return; sums[tk] = (sums[tk] || 0) + v * w; counts[tk] = (counts[tk] || 0) + w; });
                    });
                    var value = [], prev = NaN;
                    for (var k = 0; k < ts.length; k++) { var tk = String(Number(ts[k])); if (counts[tk]) prev = Math.round((sums[tk] / counts[tk]) * 100) / 100; value.push(prev); }
                    return { key: c.key, label: c.label, n: c.seats.length, value: value, color: PI_COHORT_COLORS[i % PI_COHORT_COLORS.length] };
                });
                S.piCohortCache = { sig: sig, live: live, list: list };
                return list;
            }
            function piReadQuotas() {
                var q = {};
                var u = parseFloat((el('dtwPiQUnder') || {}).value || ''), w = parseFloat((el('dtwPiQWomen') || {}).value || ''), f = parseFloat((el('dtwPiQFans') || {}).value || ''), n = parseInt((el('dtwPiQSeen') || {}).value || '', 10);
                if (isFinite(u)) q.under_35_pct = u; if (isFinite(w)) q.women_pct = w; if (isFinite(f)) q.fans_of_lead_pct = f; if (isFinite(n)) q.must_have_seen = n;
                var comps = ((el('dtwPiComps') || {}).value || '').split(',').map(function(x) { return x.trim(); }).filter(Boolean);
                if (comps.length) q.comps = comps;
                return { quotas: Object.keys(q).length ? q : null, comps: comps };
            }
            // ------------------------------------------------------------ saved setups
            // Jenna, 2026-11-03: "have a way to save a room without having to re-enter everything if you come back
            // later". Two layers: everything typed into the setup form is kept in this browser as you type (so a
            // page away and back, or a mode change, never empties the form), and Save setup keeps it on the twin
            // under a name for anyone with the twin to reopen and edit. A browser cannot hand a chosen file back,
            // so a setup keeps the file's name as the reminder to pick it again.
            function piFormSnapshot() {
                var host = piHostEl(); var setup = host ? host.querySelector('.dtw-pi-setup') : null;
                var snap = { tab: piTab(), mode: S.piMode || piDefaultMode(piTab()), fields: {}, options: [], cohorts: [], cohort_n: Number(S.piCohortN || 1), cohort_custom: !!S.piCohortCustom, file_name: '', files: [] };
                if (!setup) return snap;
                Array.prototype.forEach.call(setup.querySelectorAll('input[id^="dtwPi"], select[id^="dtwPi"], textarea[id^="dtwPi"]'), function(i) {
                    if (i.type === 'file') return;
                    if (i.id === 'dtwPiCohortN' || i.id === 'dtwPiCohortCustom' || i.id === 'dtwPiMode') return;   // mode and cohorts ride as their own fields
                    snap.fields[i.id] = (i.type === 'checkbox') ? !!i.checked : String(i.value == null ? '' : i.value);
                });
                snap.options = Array.prototype.map.call(setup.querySelectorAll('.dtw-pi-opt'), function(i) { return String(i.value || ''); });
                var names = setup.querySelectorAll('#dtwPiCohorts .dtw-pi-coh-name'), seats = setup.querySelectorAll('#dtwPiCohorts .dtw-pi-coh-seats');
                for (var k = 0; k < names.length; k++) {
                    var dem = ((S.piCohorts || [])[k] || {}).demos || {};
                    snap.cohorts.push({ name: String(names[k].value || ''), demos: dem, composition: piDemosWords(dem), seats: parseInt((seats[k] || {}).value || '0', 10) || piCohortSeatsDefault(names.length) });
                }
                var f = el('dtwPiFile'); if (f && f.files && f.files[0]) snap.file_name = f.files[0].name || '';
                var fs = el('dtwPiFiles'); if (fs && fs.files) snap.files = Array.prototype.map.call(fs.files, function(x) { return x.name || ''; });
                return snap;
            }
            function piFormApply(snap) {
                if (!snap) return;
                var host = piHostEl(); var setup = host ? host.querySelector('.dtw-pi-setup') : null;
                if (!setup) return;
                var fields = snap.fields || {};
                Object.keys(fields).forEach(function(id) {
                    var i = el(id); if (!i || i.type === 'file' || id === 'dtwPiMode') return;
                    if (i.type === 'checkbox') i.checked = !!fields[id]; else i.value = fields[id] == null ? '' : String(fields[id]);
                });
                var opts = setup.querySelectorAll('.dtw-pi-opt');
                (snap.options || []).forEach(function(v, k) { if (opts[k]) opts[k].value = v || ''; });
                // cohorts: the count and the rows come back exactly as typed
                var n = Math.max(1, Math.min(PI_COHORT_MAX, Number(snap.cohort_n || (snap.cohorts || []).length || 1)));
                S.piCohorts = (snap.cohorts || []).map(function(c) { return { name: c.name || '', demos: (c.demos && typeof c.demos === 'object') ? c.demos : {}, composition: c.composition || '', seats: c.seats || 4 }; });
                if (n !== Number(S.piCohortN || 1) || !!snap.cohort_custom !== !!S.piCohortCustom || (snap.cohorts || []).length) {
                    S.piCohortN = n; S.piCohortCustom = !!snap.cohort_custom || n > 6;
                    var box = el('dtwPiCohorts'); var sel = el('dtwPiCohortN');
                    if (box) { var tmp = document.createElement('div'); tmp.innerHTML = piCohortBuilderHtml(); var fresh = tmp.querySelector('#dtwPiCohorts'); if (fresh) box.innerHTML = fresh.innerHTML; }
                    if (sel) sel.value = S.piCohortCustom ? 'custom' : String(n);
                }
                var fi = el('dtwPiFile');
                setup.querySelectorAll('.dtw-pi-filehint').forEach(function(o) { o.remove(); });
                var names = [snap.file_name].concat(snap.files || []).filter(Boolean);
                var att = piSetupMedia();
                if (fi && att) {
                    // the cut a duplicated setup carries (Jenna 2026-11-04): it starts with the test; a picked file replaces it
                    var ah = document.createElement('div'); ah.className = 'dtw-pi-filehint';
                    var aw = document.createElement('span');
                    aw.textContent = 'The cut' + (att.filename ? ' ' + att.filename : '') + ' comes with this setup' + (att.from_title ? ' from ' + att.from_title : '') + '. Start the test and the room watches it. Pick a different file to replace it, or ';
                    var ab = document.createElement('button'); ab.type = 'button'; ab.className = 'dtw-link'; ab.textContent = 'remove it'; ab.onclick = function() { dtwPilotDropSetupMedia(); };
                    ah.appendChild(aw); ah.appendChild(ab); ah.appendChild(document.createTextNode('.'));
                    fi.parentNode.insertBefore(ah, fi.nextSibling);
                    names = names.filter(function(n) { return n !== att.filename; });
                }
                if (fi && names.length) {
                    var hint = document.createElement('div'); hint.className = 'dtw-pi-filehint';
                    hint.textContent = (names.length === 1 ? 'Last time the file was ' + names[0] + '. ' : 'Last time the files were ' + names.join(', ') + '. ') + 'Pick ' + (names.length === 1 ? 'it' : 'them') + ' again to run the test; everything else is filled in.';
                    fi.parentNode.insertBefore(hint, fi.nextSibling);
                }
            }
            function piLocalKey() { return 'bg_dtw_pilot_form:' + String(S.key || '') + ':' + piTab(); }
            function piLocalSave() { try { localStorage.setItem(piLocalKey(), JSON.stringify({ ts: Date.now(), setup_id: S.piSetupId || '', snap: piFormSnapshot() })); } catch (_) {} }
            function piLocalLoad() { try { var d = JSON.parse(localStorage.getItem(piLocalKey()) || 'null'); return d && d.snap ? d : null; } catch (_) { return null; } }
            function piLocalClear() { try { localStorage.removeItem(piLocalKey()); } catch (_) {} }
            function piAutosaveSoon() { clearTimeout(S.piAutosaveT); S.piAutosaveT = setTimeout(piLocalSave, 350); }
            function piSnapshotEmpty(snap) {
                var f = (snap || {}).fields || {};
                return !Object.keys(f).some(function(k) { var v = f[k]; return k !== 'dtwPiRoom' && k !== 'dtwPiTwin2' && k !== 'dtwPiMode' && (v === true || (typeof v === 'string' && v.trim())); })
                    && !((snap || {}).options || []).some(function(v) { return v && v.trim(); })
                    && !((snap || {}).cohorts || []).some(function(c) { return (c.name || c.composition || '').trim(); });
            }
            function piAfterStartPaint() {
                var host = piHostEl(); var setup = host ? host.querySelector('.dtw-pi-setup') : null;
                if (!setup || !S.key) return;
                setup.addEventListener('input', piAutosaveSoon);
                setup.addEventListener('change', piAutosaveSoon);
                setup.addEventListener('input', piNormGuessSync);
                var pending = S.piPending;
                if (pending) {
                    if (pending.mode && PI_TABS[piTab()].indexOf(pending.mode) >= 0 && pending.mode !== S.piMode) { S.piMode = pending.mode; renderPilotConsole(); return; }
                    S.piPending = null;
                    piFormApply(pending);
                    piNormGuessSync();
                    piLocalSave();
                    return;
                }
                var local = piLocalLoad();
                if (local && local.snap && !piSnapshotEmpty(local.snap)) {
                    if (local.setup_id && !S.piSetupId) S.piSetupId = local.setup_id;
                    piFormApply(local.snap);
                    piNormGuessSync();
                    if (!setup.querySelector('.dtw-pi-restored')) {
                        var note = document.createElement('div'); note.className = 'dtw-pi-restored';
                        note.innerHTML = '<span>Picked up what you typed here last time.</span><button type="button" onclick="dtwPilotCloseSetup()">Start a blank form</button>';
                        var cutCol = setup.querySelector('.dtw-pi-col.cut'); if (cutCol) cutCol.insertBefore(note, cutCol.firstChild.nextSibling);
                    }
                }
            }
            window.dtwPilotSaveSetup = async function(asNew) {
                var key = S.key, st = el('dtwPiStatus');
                if (!key) { if (st) st.textContent = 'Pick a twin on the Twins tab first.'; return; }
                var snap = piFormSnapshot();
                var title = String((snap.fields || {}).dtwPiTitle || '').replace(/\s+/g, ' ').trim();
                var name = title || String((snap.fields || {}).dtwPiCutTitle || '').trim();
                if (!name) { if (st) st.textContent = 'Name the test first, so the setup has a name.'; var tn = el('dtwPiTitle'); if (tn) { try { tn.focus(); } catch (_) {} } return; }
                var body = { id: asNew ? '' : (S.piSetupId || ''), name: name, tab: snap.tab, mode: snap.mode, fields: snap.fields, options: snap.options, cohorts: snap.cohorts.filter(function(c) { return (c.name || c.composition || '').trim(); }), cohort_n: snap.cohort_n, cohort_custom: snap.cohort_custom, file_name: snap.file_name, files: snap.files };
                var tok = dtwBusy(dtwBtn(asNew ? '[onclick^="dtwPilotSaveSetup(true"]' : '[onclick="dtwPilotSaveSetup()"]'), 'Saving', st); if (tok === false) return;
                dtwStage(tok, 'Saving the setup');
                try {
                    var d = await api('/' + key + '/pilot/setups', { method: 'POST', body: JSON.stringify(body) });
                    if (S.twin) S.twin.pilot_setups = d.setups || [];
                    S.piSetupId = (d.setup || {}).id || S.piSetupId;
                    piLocalSave();
                    fgToast('Saved as ' + name + '. It is under Saved setups on the left; open it any time to edit or start the test.');
                    dtwIdle(tok);
                    renderPilotConsole();
                } catch (e) { dtwIdle(tok, e.message || 'The setup could not be saved.'); }
            };
            window.dtwPilotLoadSetup = function(id) {
                var x = (((S.twin || {}).pilot_setups) || []).filter(function(s) { return s.id === id; })[0];
                if (!x) return;
                S.pilot = null;
                S.piSetupId = x.id;
                if (x.tab && PI_HOST[x.tab]) S.piTab = x.tab;
                if (x.mode && PI_TABS[piTab()].indexOf(x.mode) >= 0) S.piMode = x.mode;
                S.piCohortN = Number(x.cohort_n || (x.cohorts || []).length || 1); S.piCohortCustom = !!x.cohort_custom; S.piCohorts = (x.cohorts || []).slice();
                S.piPending = { mode: x.mode, fields: x.fields || {}, options: x.options || [], cohorts: x.cohorts || [], cohort_n: S.piCohortN, cohort_custom: S.piCohortCustom, file_name: x.file_name || '', files: x.files || [] };
                renderPilotConsole();
            };
            window.dtwPilotCloseSetup = function() {
                S.piSetupId = null; S.piPending = null; S.piCohorts = []; S.piCohortN = 1; S.piCohortCustom = false; S.piCohortOpen = null; renderDemoPicker();
                piLocalClear();
                renderPilotConsole();
            };
            window.dtwPilotDeleteSetup = async function(id) {
                var key = S.key; if (!key) return;
                var x = (((S.twin || {}).pilot_setups) || []).filter(function(s) { return s.id === id; })[0];
                try {
                    var d = await api('/' + key + '/pilot/setups/' + encodeURIComponent(id) + '/delete', { method: 'POST', body: '{}' });
                    if (S.twin) S.twin.pilot_setups = d.setups || [];
                    if (S.piSetupId === id) S.piSetupId = null;
                    renderPilotConsole();
                    fgToast('Deleted the saved setup' + (x && x.name ? ' ' + x.name : '') + '.', { label: 'Undo', onclick: async function() {
                        if (!x) return;
                        try {
                            var back = await api('/' + key + '/pilot/setups', { method: 'POST', body: JSON.stringify({ id: '', name: x.name, tab: x.tab, mode: x.mode, fields: x.fields, options: x.options, cohorts: x.cohorts, cohort_n: x.cohort_n, cohort_custom: x.cohort_custom, file_name: x.file_name, files: x.files }) });
                            if (S.twin) S.twin.pilot_setups = back.setups || [];
                            renderPilotConsole();
                        } catch (e) { fgToast(e.message || 'The setup could not be restored.'); }
                    } });
                } catch (e) { fgToast(e.message || 'The setup could not be deleted.'); }
            };
            // Duplicate (Jenna, 2026-11-04: "have a duplicate button where you could duplicate a room with all of the input if you want
            // to so if you just want to change one thing you can"). A saved setup or a test becomes a NEW saved setup with every input
            // and none of the results; a test's cut comes along so it is not uploaded again. It opens in the form so one thing can
            // change before Start the test, and it is priced like a setup typed from scratch: nothing seated, nothing charged yet.
            function piSetupMedia() {
                var cur = S.piSetupId ? (((S.twin || {}).pilot_setups) || []).filter(function(x) { return x.id === S.piSetupId; })[0] : null;
                if (!cur || !cur.media || !cur.media.has_file) return null;
                return Object.assign({ from_title: cur.duplicated_from_title || '' }, cur.media);
            }
            function piLeaveQuiet() {
                // leave the open test's page without stopping it (a Duplicate from the test page): the clock, the mirror and the polls rest
                stopPilotClock(); if (S.pilotClock) S.pilotClock.slate = false;
                stopLiveMirror();
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); } catch (_) {} }
                if (S.pilotQPoll) { try { clearTimeout(S.pilotQPoll); } catch (_) {} S.pilotQPoll = null; }
                if (S.pilotPrep && S.pilotPrep.timer) { try { clearTimeout(S.pilotPrep.timer); } catch (_) {} S.pilotPrep.timer = null; }
                S.pilotMedia = null; S.pilotCompare = null;
            }
            function piAdoptDuplicate(d, stay) {
                if (S.twin) S.twin.pilot_setups = d.setups || (S.twin.pilot_setups || []);
                var rec = d.setup;
                if (!rec) { renderPilotConsole(); return; }
                var withCut = !!(rec.media && rec.media.has_file);
                if (stay) {
                    // a test still playing keeps its page; the duplicate waits under Saved setups
                    renderPilotConsole();
                    fgToast('Duplicated as ' + (rec.name || 'a new setup') + '. It is under Saved setups; open it when this test is done.');
                    return;
                }
                if (S.pilot) piLeaveQuiet();
                if (rec.tab && PI_HOST[rec.tab] && rec.tab !== piTab()) { S.piTab = rec.tab; dtwTab(rec.tab); }
                dtwPilotLoadSetup(rec.id);
                fgToast('Duplicated as ' + (rec.name || 'a new setup') + '. Change what you want, then start the test.' + (withCut ? ' The cut comes with it.' : ''));
            }
            window.dtwPilotDuplicateSetup = async function(id) {
                var key = S.key; if (!key || !id) return;
                var tok = dtwBusy(dtwBtn(), 'Duplicating'); if (tok === false) return;
                try { var d = await api('/' + key + '/pilot/setups/' + encodeURIComponent(id) + '/duplicate', { method: 'POST', body: '{}' }); }
                catch (e) { dtwIdle(tok); fgToast(e && e.message ? e.message : 'The setup could not be duplicated.'); return; }
                dtwIdle(tok);
                piAdoptDuplicate(d, false);
            };
            window.dtwPilotDuplicateId = async function(sid) {
                var key = S.key; if (!key || !sid) return;
                var tok = dtwBusy(dtwBtn(), 'Duplicating'); if (tok === false) return;
                try { var d = await api('/' + key + '/pilot/' + encodeURIComponent(sid) + '/duplicate', { method: 'POST', body: '{}' }); }
                catch (e) { dtwIdle(tok); fgToast(e && e.message ? e.message : 'The test could not be duplicated.'); return; }
                dtwIdle(tok);
                piAdoptDuplicate(d, !!(S.pilot && S.pilot.id === sid && S.pilot.status === 'playing'));
            };
            window.dtwPilotDuplicate = function() { var s = S.pilot; if (!s) return; return dtwPilotDuplicateId(s.id); };
            window.dtwPilotDropSetupMedia = async function() {
                var key = S.key; if (!key || !S.piSetupId || !piSetupMedia()) return;
                var cur = (((S.twin || {}).pilot_setups) || []).filter(function(x) { return x.id === S.piSetupId; })[0] || {};
                var snap = piFormSnapshot();
                var body = { id: S.piSetupId, drop_media: true, name: String((snap.fields || {}).dtwPiTitle || '').trim() || cur.name || '', tab: snap.tab, mode: snap.mode, fields: snap.fields, options: snap.options,
                             cohorts: snap.cohorts.filter(function(c) { return (c.name || c.composition || '').trim(); }), cohort_n: snap.cohort_n, cohort_custom: snap.cohort_custom, file_name: snap.file_name, files: snap.files };
                try { var d = await api('/' + key + '/pilot/setups', { method: 'POST', body: JSON.stringify(body) }); }
                catch (e) { fgToast(e && e.message ? e.message : 'The cut could not be removed from the setup.'); return; }
                if (S.twin) S.twin.pilot_setups = d.setups || [];
                piLocalSave();
                renderPilotConsole();
                fgToast('The cut is off this setup. Pick a file or paste a watch link when you start the test.');
            };
            async function piUploadAsset(key, sid, file, slot, label, kind) {
                var fd = new FormData(); fd.append('file', file); fd.append('slot', String(slot)); if (label) fd.append('label', label); if (kind) fd.append('kind', kind);
                var r = await fetch('/api/digital-twins/' + encodeURIComponent(key) + '/pilot/' + encodeURIComponent(sid) + '/assets', { method: 'POST', credentials: 'same-origin', body: fd });
                var u = null; try { u = await r.json(); } catch (_) { u = null; }
                if (!r.ok || !u || u.success === false) throw new Error((u && u.error) || 'The file could not be stored.');
                return u;
            }
            window.dtwPilotOpen = async function() {
                var key = S.key, st = el('dtwPiStatus');
                if (!key) { if (st) st.textContent = 'Pick a twin on the Twins tab first.'; return; }
                var tab = piTab(), mode = ((el('dtwPiMode') || {}).value) || S.piMode || piDefaultMode(tab);
                var title = ((el('dtwPiTitle') || {}).value || '').replace(/\s+/g, ' ').trim();
                if (!title) { if (st) st.textContent = 'Name the test first.'; var tn = el('dtwPiTitle'); if (tn) { try { tn.focus(); } catch (_) {} } return; }
                var other = (el('dtwPiTwin2') || {}).value;
                var mins = piParseRuntime((el('dtwPiMins') || {}).value || '') / 60;   // '42:35' or '42'
                var secs = parseFloat((el('dtwPiSecs') || {}).value || '0') || 0;
                var cutTitle = ((el('dtwPiCutTitle') || {}).value || '').trim();
                var logline = ((el('dtwPiLogline') || {}).value || '').trim();
                var text = ((el('dtwPiText') || {}).value || '').trim();
                var purgeNew = !!((el('dtwPiPurgeNew') || {}).checked);
                var options = Array.prototype.slice.call(document.querySelectorAll('#' + (PI_HOST[tab] || 'dtwPiConsole') + ' .dtw-pi-opt')).map(function(i) { return (i.value || '').trim(); }).filter(Boolean);
                var cast = ((el('dtwPiCast') || {}).value || '').split(/[,\n]/).map(function(x) { return x.trim(); }).filter(Boolean);
                var rq = piReadQuotas();
                var file = (el('dtwPiFile') || {}).files && el('dtwPiFile').files[0];
                var more = (el('dtwPiFiles') || {}).files ? Array.prototype.slice.call(el('dtwPiFiles').files) : [];
                var carried = file ? null : piSetupMedia();   // the cut a duplicated setup carries starts with the test (Jenna 2026-11-04); a picked file wins
                if (mode === 'title' && options.length < 2) { if (st) st.textContent = 'Type at least two titles.'; return; }
                if (mode === 'key_art' && !more.length) { if (st) st.textContent = 'Upload the art (up to six images).'; return; }
                if (mode === 'concept' && !text) { if (st) st.textContent = 'Paste the concept first.'; return; }
                if (mode === 'script' && !text && !file && !carried) { if (st) st.textContent = 'Paste the script or upload it.'; return; }
                var watchUrl = ((el('dtwPiWatchUrl') || {}).value || '').trim();
                var body = { title: title, in_room: 0, twins: other && other !== key ? [other] : [], mode: mode, tier: parseInt((el('dtwPiTier') || {}).value || groupTierDefault(), 10) || 20 };
                if (carried) body.setup_id = S.piSetupId;
                var cohorts = piReadCohorts();
                if (cohorts === null) { if (st) st.textContent = 'Pick at least one demographic for every cohort (at least two cohorts).'; return; }
                if (cohorts.length) { body.cohorts = cohorts; body.in_room = cohorts.reduce(function(a, c) { return a + c.seats; }, 0); }
                if (watchUrl) body.watch_url = watchUrl;
                if (cutTitle) body.cut_title = cutTitle;
                if (mins > 0) body.runtime_s = Math.round(mins * 60);
                if (secs > 0) body.runtime_s = Math.round(secs);
                if (logline) body.logline = logline;
                if (text && mode !== 'script') body.text = text;
                if (text && mode === 'script') body.text = text;
                if (options.length && mode === 'title') body.options = options;
                if (cast.length) body.cast = cast;
                if (rq.quotas) body.quotas = rq.quotas;
                if (rq.comps.length) body.comps = rq.comps;
                if ((el('dtwPiRough') || {}).checked) body.rough_cut = true;
                if ((el('dtwPiCritics') || {}).checked) body.critics_room = true;
                if (el('dtwPiNorm')) { body.genre = (el('dtwPiNorm').value || ''); body.genre_set_by_user = ((el('dtwPiNormTouched') || {}).value === '1'); }
                var prior = (el('dtwPiPrior') || {}).value;
                var tok = dtwBusy(dtwBtn('[onclick^="dtwPilotOpen("]'), 'Starting the test', st); if (tok === false) return;
                dtwStage(tok, 'Seating the room');
                try { var d = await api('/' + key + '/pilot/open', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { dtwIdle(tok, e); if (st && e.doc && e.doc.add_funds_url) st.innerHTML = esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>'; return; }
                adoptPilot(d.session);
                piLocalClear(); S.piPending = null;      // the test is open; the saved setup (if any) stays on the twin for next time
                S.pilotMedia = null; S.pilotCompare = null;
                var sid = d.session.id;
                if (purgeNew) { try { await api('/' + key + '/pilot/' + sid + '/rename', { method: 'POST', body: JSON.stringify({ purge_media_after: true }) }); } catch (_) {} }
                if (prior) {
                    var ps = ((S.twin || {}).pilot_sessions || []).filter(function(x) { return x.id === prior; })[0];
                    if (ps) { try { var pr = await api('/' + key + '/pilot/' + sid + '/stimulus', { method: 'POST', body: JSON.stringify({ cut_label: 'Cut A' }) }); } catch (_) {} }
                }
                try {
                    if (file) {
                        var isVideo = /\.(mp4|mov|webm|m4v)$/i.test(file.name || '');
                        dtwStage(tok, isVideo ? 'Uploading the cut' : 'Reading the file');
                        if (isVideo && mode !== 'key_art') {
                            var fd = new FormData(); fd.append('file', file); fd.append('title', cutTitle || title);
                            var r = await fetch('/api/digital-twins/' + encodeURIComponent(key) + '/pilot/' + encodeURIComponent(sid) + '/upload', { method: 'POST', credentials: 'same-origin', body: fd });
                            var u = null; try { u = await r.json(); } catch (_) { u = null; }
                            if (!r.ok || !u || u.success === false) throw new Error((u && u.error) || 'The cut could not be stored.');
                            adoptPilot(u.session);
                            if (mode === 'trailer' || mode === 'spot' || mode === 'binge') { try { var a0 = await piUploadAsset(key, sid, file, 0, cutTitle || (mode === 'binge' ? 'Episode 1' : 'Trailer A'), 'video'); adoptPilot(a0.session); } catch (_) {} }
                        } else {
                            var ux = await piUploadAsset(key, sid, file, 0, cutTitle || title, 'text');
                            adoptPilot(ux.session);
                        }
                    }
                    for (var i = 0; i < more.length && i < 6; i++) {
                        dtwStage(tok, 'Uploading ' + (i + 1) + ' of ' + more.length);
                        var kind = mode === 'key_art' ? 'image' : 'video';
                        var slot = mode === 'key_art' ? i : (file ? i + 1 : i);
                        var lab = mode === 'key_art' ? ('Art ' + String.fromCharCode(65 + i)) : (mode === 'binge' ? ('Episode ' + (slot + 1)) : ('Trailer ' + String.fromCharCode(65 + slot)));
                        var ua = await piUploadAsset(key, sid, more[i], slot, lab, kind);
                        adoptPilot(ua.session);
                    }
                } catch (e) { if (st) st.textContent = e.message; }
                dtwStage(tok, 'Preparing');
                try {
                    var p = await api('/' + key + '/pilot/' + sid + '/prepare', { method: 'POST', body: JSON.stringify({ title: cutTitle || title, runtime_s: body.runtime_s || 0 }) });
                    adoptPilot(p.session);
                } catch (e) { if (st) st.textContent = e.message || 'Preparing'; }
                dtwIdle(tok);
                renderPilotConsole();
            };
            // resume=true is the automatic restart after an Ask: the clock picks up where it paused and the streamer tab the room is already watching is left alone.
            window.dtwPilotPlay = async function(resume) {
                var s = S.pilot; if (!s || !S.key) return;
                var note = el('dtwPiNote');
                if (s.status === 'preparing') { if (note) note.textContent = 'Preparing the cut. Play is ready the moment the room has watched it.'; pollPilotPrepare(); return; }
                // Play shows it is working (Jenna 2026-11-05: 'same when you click play here'): the button disables at once with the spinner,
                // Stop / Exit test / Duplicate / Delete are held until the player is actually playing, the note reports the stage
                // (Connecting the room, then First frame), a second click is ignored, and a failure puts Play back with a plain line.
                var tok = dtwBusy(dtwBtn('#dtwPiPlayBtn'), 'Starting', note); if (tok === false) return;
                dtwHoldMore(tok, '.dtw-pi-head-r button');
                dtwStage(tok, 'Connecting the room');
                var r = await apiRaw('/' + encodeURIComponent(S.key) + '/pilot/' + encodeURIComponent(s.id) + '/play', { method: 'POST', body: JSON.stringify({ runtime_s: (s.media || {}).runtime_s || 0 }) });
                if (!r.ok) {
                    if (r.data && r.data.preparing) { dtwIdle(tok); if (r.data.session) adoptPilot(r.data.session); renderPilotConsole(); return; }
                    dtwIdle(tok, (r.data || {}).error || 'Play did not start.'); return;
                }
                adoptPilot(r.data.session);
                if (!resume || s.status !== 'playing') piFocusScroll(true);   // play starts, or PLAYING is pressed: the page settles on the player once (a resume after a question never moves it)
                var v = el('dtwPiVideo');
                var wm = watchMedia(S.pilot);
                if (v && hasMedia(S.pilot)) {
                    dtwStage(tok, 'First frame');
                    if (!mediaUrl(S.pilot)) { await fetchMediaUrl(S.pilot); }
                    bindPilotMedia();
                    if (mediaUrl(S.pilot)) { try { await dtwPlayWait(v); } catch (_) {} }
                } else if (wm && isLiveSess(S.pilot) && wm.kind !== 'video') {
                    // a mirrored watch: the clock and the frames run while the mirror does
                    if (S.pilotLive && S.pilotLive.stream && S.pilotLive.sid === S.pilot.id) { dtwLabel(tok, 'Mirroring'); dtwStage(tok, 'First frame'); attachLiveMirror(S.pilot); startLiveClock(); piLegendRepaint(); }
                    else { if (!resume) openWatchWithRoom(S.pilot); renderPilotConsole(); }
                    if (S.pilotLive && S.pilotLive.stream) await dtwFirstFrame(v);   // the held controls come back when the mirror shows its first frame
                    dtwIdle(tok);
                    var ln = el('dtwPiNote'); if (ln) ln.textContent = (S.pilotLive && S.pilotLive.stream) ? 'The room is watching with you.' : 'Open the episode, press play there, then mirror it here.';
                    var lb = el('dtwPiPlayBtn'); if (lb) lb.textContent = 'Playing';
                    return;
                } else if (wm) {
                    bindWatchLink(S.pilot);
                    if (!resume) openWatchWithRoom(S.pilot);
                    if (v && wm.kind === 'video' && v.src) { dtwStage(tok, 'First frame'); try { await dtwPlayWait(v); } catch (_) {} }
                }
                if (!(v && mediaUrl(S.pilot)) && !(wm && wm.kind === 'video' && v && v.src)) {
                    S.pilotClock.slate = true;
                    S.pilotClock.t0 = Date.now() - (S.pilotClock.t || 0) * 1000;
                    stopPilotClock();
                    (function tick() {
                        if (!S.pilotClock.slate) return;
                        var t = (Date.now() - S.pilotClock.t0) / 1000;
                        var dur = piPlannedRuntime(S.pilot) || Number((S.pilot.media || {}).runtime_s || 60);
                        if (t >= dur) { t = dur; S.pilotClock.slate = false; S.pilotAutoStopped = S.pilot.id; revealPilot(t); try { dtwPilotStop(true); } catch (_) {} return; }
                        revealPilot(t);
                        var sl = el('dtwPiSlateClock'); if (sl) sl.textContent = clockFmt(t);
                        S.pilotClock.raf = requestAnimationFrame(tick);
                    })();
                }
                dtwIdle(tok);
                if (note) note.textContent = 'The room is live.';
                var btn = el('dtwPiPlayBtn'); if (btn) btn.textContent = 'Playing';
            };
            window.dtwPilotStop = async function(fromEnd) {
                stopPilotClock();
                S.pilotClock.slate = false;
                var wasLive = !!(S.pilotLive && S.pilotLive.stream);
                stopLiveMirror();
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); } catch (_) {} }
                if (!S.pilot || !S.key) { renderPilotConsole(); return; }
                var tok = dtwBusy(dtwBtn('[onclick^="dtwPilotStop("]'), 'Stopping', el('dtwPiNote')); if (tok === false) return;
                dtwStage(tok, 'Stopping the room');
                var removed = false, finishing = false;
                try {
                    var d = await api('/' + S.key + '/pilot/' + S.pilot.id + '/stop', { method: 'POST', body: '{}' });
                    adoptPilot(d.session); removed = !!d.media_removed; finishing = !!d.finishing;
                } catch (_) {}
                dtwIdle(tok);
                if (removed) { S.pilotMedia = null; }
                renderPilotConsole();
                if (fromEnd && !finishing) revealPilot(Number((S.pilot.media || {}).runtime_s || playheadNow()));
                var note = el('dtwPiNote'); if (note) { if (finishing) note.innerHTML = dtwBusyHtml('The room is scoring what it watched'); else note.textContent = removed ? 'Stopped. The file was removed; the read stays.' : (wasLive ? 'Stopped.' : 'Stopped. The scorecard is in.'); }
            };
            window.dtwPilotSeek = function(t) {
                t = Number(t || 0);
                if (piReplayable(S.pilot)) { piSeekTo(t); return; }
                var v = el('dtwPiVideo');
                if (v && v.src) { try { v.currentTime = t; } catch (_) {} }
                S.pilotClock.t = t;
                if (S.pilotClock.slate) S.pilotClock.t0 = Date.now() - t * 1000;
                revealPilot(t);
            };
            window.dtwPilotAskAt = function(t, q, kind) {
                dtwPilotSeek(Number(t || 0));
                var inp = el('dtwPiQ'); if (inp) inp.value = q || '';
                if (kind) { S.pilotKind = kind; document.querySelectorAll('#dtwPiKinds .dtw-chip').forEach(function(b) { b.classList.toggle('on', b.getAttribute('data-k') === kind); }); }
                dtwPilotAsk(Number(t || 0));
            };
            window.dtwPilotAsk = async function(atT) {
                var s = S.pilot; if (!s || !S.key) return;
                var inp = el('dtwPiQ'); var q = (inp && inp.value || '').trim();
                if (!q) { if (inp) try { inp.focus(); } catch (_) {} return; }
                var note = el('dtwPiAskNote');
                var tok = dtwBusy(dtwBtn('[onclick^="dtwPilotAsk("]'), 'Asking', note); if (tok === false) return;
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); } catch (_) {} }
                var wasSlate = S.pilotClock.slate, wasLive = !!(S.pilotLive && S.pilotLive.running); S.pilotClock.slate = false; pauseLiveClock(); stopPilotClock();
                var t = (atT != null && isFinite(atT)) ? Number(atT) : playheadNow();
                dtwStage(tok, 'Asking the room at ' + clockFmt(t));
                var body = { q: q, playhead_s: t };
                if (S.pilotKind) body.kind = S.pilotKind;
                if (inp) inp.value = '';
                try {
                    var d = await api('/' + S.key + '/pilot/' + s.id + '/ask', { method: 'POST', body: JSON.stringify(body) });
                    if (d.session) adoptPilot(d.session);
                    else if (d.question) { s.questions = s.questions || []; s.questions.push(d.question); }
                } catch (e) {
                    dtwIdle(tok, e);
                    if (note) { if (e.doc && e.doc.upgrade) { S.piPendingQ = { q: q, t: t }; note.innerHTML = esc(e.doc.upgrade.line || e.message) + ' <button type="button" class="dtw-link" onclick="dtwPilotUpgrade()">Move it to 1-40 for ' + esc(e.doc.upgrade.label || '') + '</button>'; } else if (e.doc && e.doc.add_funds_url) { note.innerHTML = esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>'; } }
                    if (inp && !inp.value) inp.value = q; return;
                }
                dtwIdle(tok);
                renderPilotConsole();
                dtwPilotSeek(t);
                if (note) note.textContent = 'Asked at ' + clockFmt(t) + '. The twins answer in the chat as they go.';
                pollPilotQuestions();
                if (v && mediaUrl(S.pilot)) { try { await v.play(); } catch (_) {} }
                else if (wasLive && S.pilotLive && S.pilotLive.stream) { S.pilotClock.t = t; startLiveClock(); }
                else if (wasSlate) { S.pilotClock.slate = true; S.pilotClock.t0 = Date.now() - t * 1000; dtwPilotPlay(true); }
            };
            // a 1-20 test at twenty questions: move it to 1-40 for the difference at the company's plan rate, then the waiting question runs (Jenna 2026-11-05)
            window.dtwPilotUpgrade = async function() {
                var s = S.pilot, note = el('dtwPiNote'); if (!S.key || !s) return;
                if (note) note.textContent = 'Moving this test to 1-40 questions...';
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/upgrade', { method: 'POST', body: '{}' }); if (d.session) adoptPilot(d.session); }
                catch (e) { if (note) { note.textContent = e.message; if (e.doc && e.doc.add_funds_url) note.innerHTML = esc(e.message) + ' <a class="dtw-link" href="' + esc(e.doc.add_funds_url) + '">Add funds</a>'; } return; }
                if (note) note.textContent = 'This test now answers up to 40 questions.';
                var p = S.piPendingQ; S.piPendingQ = null;
                if (p && p.q) { var inp = el('dtwPiQ'); if (inp) inp.value = p.q; try { await dtwPilotAsk(p.t); } catch (_) {} }
            };
            function pollPilotQuestions() {
                var s = S.pilot; if (!s || !S.key) return;
                var open = (s.questions || []).filter(function(q) { return q.stage && q.stage !== 'done' && q.stage !== 'stopped' && q.stage !== 'failed'; });
                if (!open.length) return;
                if (S.pilotQPoll) { try { clearTimeout(S.pilotQPoll); } catch (_) {} }
                // the seats answer one by one and each lands in the chat as it does: every three seconds while the room is
                // thinking, every five otherwise
                var thinking = open.some(function(q) { return q.stage === 'the room is thinking'; });
                S.pilotQPoll = setTimeout(async function() {
                    if (!S.pilot || S.pilot.id !== s.id) return;
                    var before = {}; (S.pilot.questions || []).forEach(function(q) { before[String(q.id)] = String(q.stage || ''); });
                    var r = await apiRaw('/' + encodeURIComponent(S.key) + '/pilot/' + encodeURIComponent(s.id), { method: 'GET' });
                    if (r.ok && r.data.session) {
                        var t = playheadNow();
                        adoptPilot(r.data.session);
                        var th = el('dtwPiThread'); if (th) th.innerHTML = renderPilotThread(S.pilot);
                        paintPilotSeats(t);     // the question, each seat's answer as it lands, then the read, in the chat
                        piRecordRefresh();
                        var landed = (S.pilot.questions || []).some(function(q) { var st = String(q.stage || ''); return before[String(q.id)] && before[String(q.id)] !== st && (st === 'done' || st === 'failed' || st === 'stopped'); });
                        var scw = document.querySelector('.dtw-pane.active .dtw-pi .dtw-card'); if (scw && landed && S.pilot.status === 'stopped') { renderPilotConsole(); dtwPilotSeek(t); }
                    }
                    pollPilotQuestions();
                }, thinking ? 3000 : 5000);
            }
            window.dtwPilotRename = async function() {
                var s = S.pilot, inp = el('dtwPiName'); if (!s || !S.key || !inp) return;
                var title = (inp.value || '').replace(/\s+/g, ' ').trim();
                if (!title || title === s.title) return;
                try {
                    var d = await api('/' + S.key + '/pilot/' + s.id + '/rename', { method: 'POST', body: JSON.stringify({ title: title }) });
                    if (d.session) adoptPilot(d.session);
                } catch (_) {}
            };
            window.dtwPilotDelete = async function() { var s = S.pilot; if (!s) return; return dtwPilotDeleteId(s.id); };
            // Delete any test from the list or the open test (Jenna, 2026-11-03: "allow a way to delete pilot tests").
            // The server keeps a copy an admin can restore for 30 days; the page drops it at once.
            window.dtwPilotDeleteId = async function(sid) {
                if (!sid || !S.key) return;
                var list = ((S.twin || {}).pilot_sessions || []);
                var s = list.filter(function(x) { return x.id === sid; })[0] || (S.pilot && S.pilot.id === sid ? S.pilot : null);
                var title = (s && s.title) || 'this test';
                if (!window.confirm('Delete ' + title + '? An admin can restore it for 30 days.')) return;
                if (S.pilot && S.pilot.id === sid) { stopPilotClock(); stopLiveMirror(); if (S.pilotQPoll) { try { clearTimeout(S.pilotQPoll); } catch (_) {} S.pilotQPoll = null; } }
                try { await api('/' + S.key + '/pilot/' + encodeURIComponent(sid) + '/delete', { method: 'POST', body: '{}' }); }
                catch (e) { fgToast(e && e.message ? e.message : 'That test could not be deleted just now.'); return; }
                if (S.twin) S.twin.pilot_sessions = list.filter(function(x) { return x.id !== sid; });
                if (S.pilot && S.pilot.id === sid) { S.pilot = null; S.pilotMedia = null; S.pilotCompare = null; }
                renderPilotConsole();
                fgToast('Deleted ' + title + '.', { label: 'Undo', onclick: function() { dtwPilotRestoreId(sid); } });
            };
            window.dtwPilotRestoreId = async function(sid) {
                if (!sid || !S.key) return;
                try { var d = await api('/' + S.key + '/pilot/' + encodeURIComponent(sid) + '/restore', { method: 'POST', body: '{}' }); }
                catch (e) { fgToast(e && e.message ? e.message : 'That test could not be restored.'); return; }
                if (d && d.session) { adoptPilot(d.session); S.pilot = null; renderPilotConsole(); fgToast('Restored ' + (d.session.title || 'the test') + '.'); }
            };
            // ------------------------------------------------------------ Pilot Testing phase 2: the mode reads, the diagnostics, the extras
            function piTable(head, rows) {
                return '<table class="dtw-pi-table"><thead><tr>' + head.map(function(h, i) { return '<th' + (i ? ' class="n"' : '') + '>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' + rows.map(function(r) { return '<tr>' + r.map(function(c, i) { return '<td' + (i ? ' class="n"' : '') + '>' + c + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table>';
            }
            function piNorm(v) { return v == null ? '-' : Number(v).toFixed(1) + '%'; }
            function piRoomSaid(ch) {
                // what the room said about the people named on the test (Jenna 2026-11-04): two or three sentences in the room's own
                // terms and one seat's line under the characters table; the read is built against the metrics above it
                var rows = (ch.characters || []).filter(function(c) { return c.room_said && c.room_said.said; });
                if (!rows.length) return '';
                return rows.map(function(c) {
                    var r = c.room_said, q = r.quote || null;
                    var who = q ? [q.name || (q.seat != null ? 'seat ' + q.seat : ''), (q.name && q.seat != null) ? 'seat ' + q.seat : '', q.clock || ''].filter(Boolean).join(', ') : '';
                    return '<h4>What the room said about ' + esc(c.name) + '</h4><p class="dtw-p">' + esc(r.said) + '</p>' + (q && q.line ? '<div class="dtw-pi-quote">' + esc(who) + ': ' + esc(q.line) + '</div>' : '');
                }).join('');
            }
            function renderModeRead(s) {
                var r = s.mode_read || {}, mode = r.mode || s.mode || 'pilot';
                if (!s.scorecard) return '';
                var out = '';
                if ((mode === 'trailer' || mode === 'spot') && r.interest_after_pct != null) {
                    var lift = Number(r.definite_interest_lift_pp || 0);
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">The trailer</div>'
                        + '<div class="dtw-pi-money"><div class="m"><div class="k">Definitely interested before</div><div class="v">' + pct1(r.interest_before_pct) + '</div><div class="n">From the title and logline alone</div></div>'
                        + '<div class="m"><div class="k">Definitely interested after</div><div class="v">' + pct1(r.interest_after_pct) + '</div><div class="n">Lift ' + (lift >= 0 ? '+' : '') + lift.toFixed(1) + ' points</div></div>'
                        + '<div class="m"><div class="k">Would watch or see it</div><div class="v">' + pct1(r.would_watch_pct) + '</div><div class="n">' + esc((r.who_for || {}).line || '') + '</div></div></div>'
                        + '<p class="dtw-p" style="margin-top:8px">' + esc(r.read || '') + '</p>'
                        + ((r.sold_it || []).length ? '<h4>The moments that sold it</h4>' + (r.sold_it || []).map(function(m) { return '<div class="dtw-pi-voice"><span class="k">' + esc(m.clock) + '  ' + esc(PI_EVT[m.type] || m.type) + '  ' + esc(m.name || '') + '</span><div>' + esc(m.line || '') + '</div></div>'; }).join('') : '')
                        + '<h4>What they understood it is about</h4>' + (r.understood || []).slice(0, 5).map(function(u) { return '<div class="dtw-pi-quote">' + esc(u.name) + ': ' + esc(u.line) + '</div>'; }).join('')
                        + '<h4>What they expect</h4>' + (r.expect || []).slice(0, 4).map(function(u) { return '<div class="dtw-pi-quote">' + esc(u.name) + ': ' + esc(u.line) + '</div>'; }).join('')
                        + ((r.quadrants || []).length ? '<h4>By quadrant</h4>' + piTable(['Quadrant', 'Seats', 'Before', 'After', 'Lift', 'Would watch'], (r.quadrants || []).filter(function(q) { return q.n; }).map(function(q) { return [esc(q.label), esc(q.n), pct1(q.interest_before_pct), pct1(q.interest_after_pct), (q.definite_interest_lift_pp != null ? (q.definite_interest_lift_pp >= 0 ? '+' : '') + q.definite_interest_lift_pp : ''), pct1(q.would_watch_pct)]; })) : '');
                    var cmp = r.compare || null, reads = Object.keys(s.asset_reads || {}).map(function(k) { return s.asset_reads[k]; });
                    if (cmp && (cmp.ranked || []).length) {
                        out += '<h4>Side by side</h4><p class="dtw-p">' + esc(cmp.read || '') + '</p>' + piTable(['Asset', 'Lift', 'After', 'Would watch', 'Recommend'], cmp.ranked.map(function(x) { return [esc(x.asset_label), (x.definite_interest_lift_pp >= 0 ? '+' : '') + esc(x.definite_interest_lift_pp), pct1(x.interest_after_pct), pct1(x.would_watch_pct), pct1(x.recommend_pct)]; }));
                    }
                    if (reads.length) out += '<canvas id="dtwPiAssets" class="dtw-pi-cmp"></canvas>';
                    out += '</div>';
                } else if ((mode === 'title' || mode === 'key_art') && (r.options || []).length) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">' + (mode === 'title' ? 'The titles, ranked' : 'The art, ranked') + '</div><p class="dtw-p" style="margin-top:6px">' + esc(r.read || '') + '</p>'
                        + (r.options || []).map(function(o) {
                            var sub = mode === 'title' ? ('fits the logline ' + pct1(o.fit_pct) + ', stands out ' + pct1(o.unique_pct) + ', love it ' + pct1(o.love_it_pct)) : ('holds the eye ' + pct1(o.attention_pct) + ', interest ' + (o.interest_lift_pp != null ? ((o.interest_lift_pp >= 0 ? '+' : '') + o.interest_lift_pp + ' points') : '-') + ', love it ' + pct1(o.love_it_pct));
                            return '<div class="dtw-pi-rank"><span class="r">' + esc(o.rank) + '</span><div><div><b>' + esc(o.label) + '</b>' + (o.caption ? ' <span class="dtw-muted">' + esc(o.caption) + '</span>' : '') + '</div><div class="dtw-muted" style="font-size:0.78rem">' + esc(sub) + '</div>' + (o.reasons || []).slice(0, 2).map(function(q) { return '<div class="dtw-pi-quote">' + esc(q.name) + ': ' + esc(q.line) + '</div>'; }).join('') + '</div><span class="v">' + pct1(o.preference_pct) + '</span></div>';
                        }).join('')
                        + ((r.quadrants || []).filter(function(q) { return q.n; }).length ? '<h4>By quadrant</h4>' + piTable(['Quadrant', 'Seats', 'Prefers'], (r.quadrants || []).filter(function(q) { return q.n; }).map(function(q) { return [esc(q.label), esc(q.n), esc(q.winner || '') + (q.winner_pct != null ? ' (' + pct1(q.winner_pct) + ')' : '')]; })) : '')
                        + '</div>';
                } else if (mode === 'concept' && r.definitely_interested_pct != null) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">The idea</div>'
                        + '<div class="dtw-pi-money"><div class="m"><div class="k">Definitely interested</div><div class="v">' + pct1(r.definitely_interested_pct) + '</div><div class="n">' + pct1(r.interested_pct) + ' at least somewhat</div></div>'
                        + '<div class="m"><div class="k">Stands out</div><div class="v">' + pct1(r.unique_pct) + '</div><div class="n">' + esc((r.who_for || {}).line || '') + '</div></div>'
                        + '<div class="m"><div class="k">Would watch</div><div class="v">' + pct1(r.would_watch_pct) + '</div><div class="n">' + ((r.strongest || [])[0] ? 'Strongest: ' + esc(r.strongest[0].label) + ' ' + pct1(r.strongest[0].pct) : '') + '</div></div></div>'
                        + '<p class="dtw-p" style="margin-top:8px">' + esc(r.read || '') + '</p>'
                        + '<div class="dtw-pi-diag"><div class="dtw-card"><h4>The strongest element</h4>' + piTable(['Element', 'Share'], (r.strongest || []).map(function(x) { return [esc(x.label), pct1(x.pct)]; })) + '</div><div class="dtw-card"><h4>The biggest doubt</h4>' + piTable(['Doubt', 'Share'], (r.doubt || []).map(function(x) { return [esc(x.label), pct1(x.pct)]; })) + '</div></div>'
                        + '</div>';
                } else if (mode === 'script' && (r.scenes || []).length) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Scene by scene</div><p class="dtw-p" style="margin-top:6px">' + esc(r.read || '') + '</p>'
                        + piTable(['Scene', 'Dial', 'Notes'], (r.scenes || []).map(function(x) { var notes = Object.keys(x.notes || {}).map(function(k) { return x.notes[k]; }).filter(Boolean).join(' '); return ['<button type="button" class="dtw-link" onclick="dtwPilotSeek(' + Number((s.scenes || []).filter(function(sc) { return sc.id === x.id; }).map(function(sc) { return sc.t_start; })[0] || 0) + ')">' + esc(x.clock) + ' ' + esc(x.slug) + '</button>', x.dial != null ? esc(Number(x.dial).toFixed(1)) : '', esc(notes)]; }))
                        + '<div class="dtw-pi-money" style="margin-top:10px"><div class="m"><div class="k">Story works</div><div class="v">' + pct1(r.story_pct) + '</div></div><div class="m"><div class="k">Structure works</div><div class="v">' + pct1(r.structure_pct) + '</div></div><div class="m"><div class="k">Characters work</div><div class="v">' + pct1(r.character_pct) + '</div></div></div></div>';
                } else if (mode === 'episode' && r.commitment_pct != null) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">After the pilot</div><div class="dtw-pi-money"><div class="m"><div class="k">Would keep going</div><div class="v">' + pct1(r.commitment_pct) + '</div></div><div class="m"><div class="k">Better than the last one</div><div class="v">' + pct1(r.better_than_prior_pct) + '</div></div>' + (r.tune_in_delta_pp != null ? '<div class="m"><div class="k">Would watch the next one</div><div class="v">' + (r.tune_in_delta_pp >= 0 ? '+' : '') + esc(r.tune_in_delta_pp) + ' pts</div><div class="n">against ' + esc((r.prior || {}).title || 'the prior cut') + '</div></div>' : '') + '</div><p class="dtw-p" style="margin-top:8px">' + esc(r.read || '') + '</p></div>';
                } else if (mode === 'binge' && (r.episodes || []).length) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Across the episodes</div><p class="dtw-p" style="margin-top:6px">' + esc(r.read || '') + '</p>' + piTable(['Episode', 'Total positive', 'Would watch the next one', 'Cumulative'], (r.episodes || []).map(function(e, i) { return [esc(e.label), pct1(e.total_positive_pct), pct1(e.tune_in_pct), pct1((r.cumulative_tune_in || [])[i])]; })) + '</div>';
                }
                var mk = s.marketing_read || null;
                if (mk && mk.read) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Locked cut: the marketing read</div><p class="dtw-p" style="margin-top:6px">' + esc(mk.read) + '</p>' + ((mk.trailer_moments || []).length ? '<div class="dtw-chips">' + mk.trailer_moments.map(function(m) { return '<button type="button" class="dtw-chip" onclick="dtwPilotSeek(' + Number(m.t || 0) + ')">' + esc(m.clock) + ' ' + esc(PI_EVT[m.type] || m.type) + '</button>'; }).join('') + '</div>' : '') + '</div>';
                }
                return out;
            }
            function renderDiagnostics(s) {
                var d = s.diagnostics || {};
                if (!s.scorecard || !Object.keys(d).length) return '';
                var ch = d.characters || {}, pace = d.pace || {}, sc = d.scenes || {}, comps = d.components || {};
                var out = '<div class="dtw-pi-diag">';
                if ((ch.characters || []).length) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Characters</div><p class="dtw-p" style="margin-top:6px">' + esc(ch.read || '') + '</p>'
                        + piTable(['Character', 'Excellent', 'Norm', 'On screen'], ch.characters.map(function(c) { return [esc(c.name) + ' <span class="dtw-muted">' + esc(c.role) + '</span>', pct1(c.excellent_pct), piNorm(c.norm), esc(c.screen_clock) + ' <span class="dtw-muted">' + esc(c.time_vs_like) + '</span>']; }))
                        + (ch.relationship ? '<p class="dtw-muted" style="margin-top:6px">The key relationship, ' + esc(ch.relationship.pair.join(' and ')) + ': ' + pct1(ch.relationship.excellent_pct) + ' excellent.</p>' : '')
                        + ((ch.follow || []).length ? '<p class="dtw-muted">Would follow into a second episode: ' + ch.follow.map(function(f) { return esc(f.name) + ' ' + pct1(f.pct); }).join(', ') + '.</p><p class="dtw-muted" style="margin-top:2px;font-size:11px">' + esc(ch.follow_note || 'A seat can follow more than one person.') + '</p>' : '')
                        + piRoomSaid(ch)
                        + '</div>';
                } else if (ch.read) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Characters</div><p class="dtw-muted" style="margin-top:6px">' + esc(ch.read) + '</p><div class="dtw-actions"><input id="dtwPiCastLate" class="dtw-in" placeholder="Mae, Ray, Lorelai" style="max-width:260px"><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotSetCast()">Add the cast</button></div></div>';
                }
                if ((pace.acts || []).length) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Pace, confusion, the ending</div><p class="dtw-p" style="margin-top:6px">' + esc(pace.read || '') + '</p>'
                        + piTable(['Stretch', 'Too slow', 'Just right', 'Too fast'], pace.acts.map(function(a) { return ['<button type="button" class="dtw-link" onclick="dtwPilotSeek(' + Number(((s.beats || []).filter(function(b) { return clockFmt(b.t) === a.from_clock; })[0] || {}).t || 0) + ')">' + esc(a.label) + ' <span class="dtw-muted">' + esc(a.from_clock) + ' to ' + esc(a.to_clock) + '</span></button>', pct1(a.too_slow_pct), pct1(a.just_right_pct), pct1(a.too_fast_pct)]; }))
                        + '<div class="dtw-pi-money" style="margin-top:10px"><div class="m"><div class="k">Beginning</div><div class="v">' + pct1((pace.beginning || {}).excellent_pct) + '</div><div class="n">excellent</div></div><div class="m"><div class="k">Ending</div><div class="v">' + pct1((pace.ending || {}).excellent_pct) + '</div><div class="n">excellent</div></div><div class="m"><div class="k">Confused somewhere</div><div class="v">' + pct1(pace.confusion_pct) + '</div><div class="n">' + esc(pace.confused_seats || 0) + ' seats</div></div></div>'
                        + ((pace.confusions || []).length ? '<h4>What confused them</h4>' + pace.confusions.slice(0, 6).map(function(c) { return '<div class="dtw-pi-quote"><button type="button" class="dtw-link" onclick="dtwPilotSeek(' + Number(c.t || 0) + ')">' + esc(c.clock) + '</button> ' + esc(c.name) + ': ' + esc(c.line) + '</div>'; }).join('') : '')
                        + '</div>';
                }
                if ((sc.liked_best || []).length || (sc.liked_least || []).length) {
                    function sceneList(rows) { return rows.map(function(x) { return '<div class="dtw-pi-rank"><span class="r">' + esc(x.mentions) + '</span><div><div><button type="button" class="dtw-link" onclick="dtwPilotSeek(' + Number(((s.scenes || []).filter(function(z) { return z.id === x.scene_id; })[0] || {}).t_start || 0) + ')">' + esc(x.clock) + '</button> ' + esc(x.on_screen || 'the scene') + '</div>' + (x.quotes || []).slice(0, 2).map(function(q) { return '<div class="dtw-pi-quote">' + esc(q.name) + ': ' + esc(q.line) + '</div>'; }).join('') + '</div></div>'; }).join(''); }
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Scenes liked best and least</div><p class="dtw-p" style="margin-top:6px">' + esc(sc.read || '') + '</p><h4>Liked best</h4>' + sceneList(sc.liked_best || []) + '<h4>Liked least</h4>' + sceneList(sc.liked_least || [])
                        + ((sc.not_recommending || []).length ? '<h4>Why some would not recommend it</h4>' + sc.not_recommending.slice(0, 5).map(function(n) { return '<div class="dtw-pi-quote">' + esc(n.name) + ': ' + esc(n.line) + '</div>'; }).join('') : '')
                        + ((sc.similar_titles || []).length ? '<p class="dtw-muted" style="margin-top:8px">It reminded them of: ' + sc.similar_titles.map(function(t) { return esc(t.title) + ' (' + esc(t.mentions) + ')'; }).join(', ') + '.</p>' : '')
                        + '</div>';
                }
                if ((comps.rows || []).length) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Components</div><p class="dtw-p" style="margin-top:6px">' + esc(comps.read || '') + '</p>' + piTable(['Component', 'Excellent', 'Norm', 'Against the norm'], comps.rows.map(function(c) { return [esc(c.label), pct1(c.excellent_pct), piNorm(c.norm), c.vs_norm_pp != null ? ((c.vs_norm_pp >= 0 ? '+' : '') + esc(c.vs_norm_pp)) : '']; })) + '</div>';
                }
                return out + '</div>';
            }
            function renderPilotExtras(s) {
                if (!S.key) return '';
                var out = '<div class="dtw-pi-diag">';
                // the recut re-test and the compare
                var cmp = S.pilotCompare && S.pilotCompare.sid === s.id ? S.pilotCompare.data : null;
                out += '<div class="dtw-card"><div class="dtw-eyebrow">Recut re-test</div><p class="dtw-muted" style="margin-top:6px">' + (s.lineage && s.lineage.parent ? 'This is ' + esc(s.cut_label || 'Cut B') + ' on the same room as ' + esc(s.lineage.parent_cut_label || 'Cut A') + '.' : 'Upload the next cut to the same room: the same seats watch it fresh, and the two dials sit side by side with the deltas scene by scene.') + '</p>'
                    + '<div class="dtw-actions">' + (s.scorecard && !(s.lineage && s.lineage.parent) ? '<button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotRecut()">Open the recut on this room</button>' : '') + (s.lineage && s.lineage.parent && s.scorecard ? '<button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotCompare()">Compare with ' + esc(s.lineage.parent_cut_label || 'Cut A') + '</button>' : '') + ((s.recuts || []).length ? s.recuts.map(function(r) { return '<button type="button" class="dtw-link" onclick="dtwPilotPick(\'' + esc(r.id) + '\')">' + esc(r.label) + '</button>'; }).join('') : '') + '</div>'
                    + (cmp && cmp.recut ? '<p class="dtw-p">' + esc(cmp.recut.read || '') + '</p><canvas id="dtwPiCompare" class="dtw-pi-cmp"></canvas>' + piTable(['Measure', esc(cmp.recut.a.label), esc(cmp.recut.b.label), 'Delta'], (cmp.recut.scorecard_deltas || []).map(function(d) { return [esc(d.label), pct1(d.a), pct1(d.b), (d.delta >= 0 ? '+' : '') + esc(d.delta)]; })) + ((cmp.recut.act_deltas || []).length ? '<h4>By act</h4>' + piTable(['Act', esc(cmp.recut.a.label), esc(cmp.recut.b.label), 'Delta'], cmp.recut.act_deltas.map(function(d) { return [esc(d.label), esc(d.a), esc(d.b), (d.delta >= 0 ? '+' : '') + esc(d.delta)]; })) : '') + ((cmp.recut.trims || []).length ? '<p class="dtw-muted">Trims: ' + cmp.recut.trims.slice(0, 5).map(function(t) { return esc(t.clock) + ' (' + esc(t.act_label) + (t.act_delta != null ? ', ' + (t.act_delta >= 0 ? '+' : '') + esc(t.act_delta) : '') + ')'; }).join('; ') + '.</p>' : '') : '')
                    + '</div>';
                // two audiences
                var ta = s.two_audiences || null;
                if (ta && (ta.lines || []).length >= 2) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Two audiences on one cut</div><p class="dtw-p" style="margin-top:6px">' + esc(ta.read || '') + '</p><canvas id="dtwPiTwo" class="dtw-pi-cmp"></canvas>'
                        + piTable(['Audience', 'Seats', 'Dial', 'Total positive', 'Definitely recommend'], ta.lines.map(function(l) { return [esc(l.name), esc(l.n), esc(l.mean), pct1(l.total_positive_pct), pct1(l.definitely_recommend_pct)]; }))
                        + ((ta.splits || []).length ? '<h4>Where they split</h4>' + ta.splits.slice(0, 4).map(function(sp) { var said = Object.keys(sp.said || {}).map(function(n) { return (sp.said[n] || []).slice(0, 1).map(function(e) { return '<div class="dtw-pi-quote">' + esc(n) + ': ' + esc(e.line) + '</div>'; }).join(''); }).join(''); return '<div class="dtw-pi-rank"><span class="r">' + esc(sp.gap) + '</span><div><div><button type="button" class="dtw-link" onclick="dtwPilotSeek(' + Number(sp.from_s || 0) + ')">' + esc(sp.from_clock) + ' to ' + esc(sp.to_clock) + '</button> ' + esc(sp.higher) + ' higher' + (sp.on_screen ? ', on ' + esc(sp.on_screen) : '') + '</div>' + said + '</div></div>'; }).join('') : '')
                        + '</div>';
                }
                // comps
                var cs = s.comp_set || null;
                out += '<div class="dtw-card"><div class="dtw-eyebrow">Versus comps</div>' + (cs && (cs.rows || []).length ? '<p class="dtw-p" style="margin-top:6px">' + esc(cs.read || '') + '</p>' + piTable(['Comp', 'Total positive', 'Definitely recommend', 'Excellent', 'From'], cs.rows.map(function(c) { return [esc(c.title), pct1(c.total_positive_pct), pct1(c.definitely_recommend_pct), pct1(c.excellent_pct), '<span class="dtw-muted">' + esc(c.source) + (c.outcome ? '. ' + esc(c.outcome) : '') + '</span>']; })) : '<p class="dtw-muted" style="margin-top:6px">Name comps (with any numbers you know) and the test is placed among them, next to the twin\'s prior tests.</p>')
                    + '<div class="dtw-actions"><input id="dtwPiCompsIn" class="dtw-in" placeholder="Flashdance, The L Word" style="max-width:300px" value="' + esc(((s.comps || []).map(function(c) { return c.title || c; })).join(', ')) + '"><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotComps()">' + (cs ? 'Refresh the comps' : 'Place it among the comps') + '</button></div></div>';
                // recruit block
                var rec = s.recruit || null;
                if (rec) {
                    var w = rec.walkouts || {};
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Preview stats</div><p class="dtw-p" style="margin-top:6px">Recruited to ' + esc(rec.concept || "the twin's own mix") + '.</p>'
                        + '<div class="dtw-pi-money"><div class="m"><div class="k">Seated</div><div class="v">' + esc(rec.seated || '') + '</div><div class="n">' + (rec.invited ? fmtN(rec.invited) + ' invited, ' + esc(rec.invite_ratio) : 'the twin\'s own mix') + '</div></div><div class="m"><div class="k">Walked out</div><div class="v">' + esc(w.count != null ? w.count : '-') + '</div><div class="n">' + esc(w.read || '') + '</div></div>' + ((rec.screener || {}).titles && rec.screener.titles.length ? '<div class="m"><div class="k">Screener</div><div class="v">' + esc(rec.screener.must_have_seen || 0) + ' of ' + esc(rec.screener.titles.length) + '</div><div class="n">' + esc(rec.screener.titles.slice(0, 3).join(', ')) + '</div></div>' : '') + '</div>'
                        + ((rec.quotas_met || []).length ? piTable(['Quota', 'Asked for', 'Seated'], rec.quotas_met.map(function(q) { return [esc(q.quota), pct1(q.target_pct), pct1(q.seated_pct)]; })) : '')
                        + '</div>';
                }
                // critics
                var cr = s.critics || null;
                if (cr && cr.critic_score != null) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Critics room</div><div class="dtw-pi-money"><div class="m"><div class="k">Critic score forecast</div><div class="v">' + esc(Math.round(cr.critic_score)) + '</div><div class="n">likely ' + esc(Math.round(cr.range[0])) + ' to ' + esc(Math.round(cr.range[1])) + '</div></div></div><p class="dtw-p" style="margin-top:8px">' + esc(cr.awards_read || '') + '</p></div>';
                } else if (!s.play_charged && !s.critics_room) {
                    out += '<div class="dtw-card"><div class="dtw-eyebrow">Critics room</div><p class="dtw-muted" style="margin-top:6px">Seat critics and journalists instead of fans before Play: a critic score forecast and an awards read, at the session price.</p><div class="dtw-actions"><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotCritics()">Make this a critics room</button></div></div>';
                }
                // talent attach
                var tal = (s.talent_attach || []);
                out += '<div class="dtw-card"><div class="dtw-eyebrow">Talent attach</div><p class="dtw-muted" style="margin-top:6px">How much a name lifts the first month: the name\'s pull with this audience and what the room made of them once it watched, by quadrant, and who they would watch it with. Billing is shown, never weighed.</p>'
                    + tal.slice(-6).map(function(t) { return '<p class="dtw-p">' + esc(t.read || '') + '</p>' + piTable(['Quadrant', 'Lift'], (t.by_quadrant || []).filter(function(q) { return q.n; }).map(function(q) { return [esc(q.label), pct1(q.reach_lift_pct)]; })) + ((t.watch_with || []).length ? '<p class="dtw-muted">Who they would watch it with: ' + t.watch_with.slice().sort(function(a, b) { return b.pct - a.pct; }).slice(0, 3).map(function(r) { return esc(r.label) + ' ' + pct1(r.pct); }).join(', ') + '.</p>' : ''); }).join('')
                    + '<div class="dtw-actions"><input id="dtwPiTalentIn" class="dtw-in" placeholder="Jennifer Beals" style="max-width:240px"><select id="dtwPiTalentRole" class="dtw-in" style="width:auto"><option value="lead">Lead</option><option value="supporting">Supporting</option><option value="host">Host</option></select><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotTalent()">Attach a name</button></div></div>';
                // activation brief
                var br = s.activation_brief || null;
                out += '<div class="dtw-card"><div class="dtw-eyebrow">Activation brief</div>' + (br ? '<p class="dtw-p" style="margin-top:6px">' + esc(br.read || '') + '</p>' + piTable(['Segment', 'Definitely recommend', 'People'], (br.segments || []).map(function(g) { return [esc(g.label), pct1(g.definitely_recommend_pct), fmtN(g.people)]; })) + '<p class="dtw-muted" style="margin-top:6px">' + esc(br.text || '') + '</p><div class="dtw-actions"><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwChatFill(\'' + esc(jsq(br.launch_prompt || '')) + '\')">Plan the launch on these segments</button><button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwTab(\'launch\')">Open Launch Lab</button></div>' : '<p class="dtw-muted" style="margin-top:6px">The segments that scored highest on definitely recommend, handed to Launch Lab and the Spend Engine in the shape they take.</p><div class="dtw-actions">' + (s.scorecard ? '<button type="button" class="dtw-btn dtw-btn-sm" onclick="dtwPilotBrief()">Write the activation brief</button>' : '<span class="dtw-muted">Play the test first.</span>') + '</div>') + '</div>';
                return out + '</div>';
            }
            function drawLines(canvasId, ts, series, colors, dur) {
                var c = el(canvasId); if (!c || !ts || !ts.length) return;
                var w = c.clientWidth || 640, h = c.clientHeight || 150;
                if (c.width !== w) c.width = w; if (c.height !== h) c.height = h;
                var ctx = c.getContext('2d'); if (!ctx) return;
                ctx.clearRect(0, 0, w, h);
                dur = dur || ts[ts.length - 1] || 1;
                function X(sec) { return (sec / dur) * w; }
                function Y(v) { return h - 10 - (Math.max(0, Math.min(100, v)) / 100) * (h - 20); }
                ctx.strokeStyle = 'rgba(233,232,225,0.12)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, Y(50)); ctx.lineTo(w, Y(50)); ctx.stroke();
                series.forEach(function(vals, k) {
                    if (!vals || !vals.length) return;
                    ctx.strokeStyle = colors[k % colors.length]; ctx.lineWidth = k === 0 ? 2.2 : 1.6; ctx.beginPath();
                    var started = false;
                    for (var i = 0; i < ts.length && i < vals.length; i++) { var x = X(ts[i]), y = Y(vals[i]); if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); }
                    if (started) ctx.stroke();
                });
            }
            function drawCompareChart() {
                var s = S.pilot; if (!s) return;
                var cmp = S.pilotCompare && S.pilotCompare.sid === s.id ? S.pilotCompare.data : null;
                if (cmp && cmp.recut && el('dtwPiCompare')) drawLines('dtwPiCompare', cmp.recut.dial.t, [cmp.recut.dial.b, cmp.recut.dial.a], ['#E9E8E1', '#B7B3D8'], Number((s.media || {}).runtime_s || 0));
                var ta = s.two_audiences;
                if (ta && el('dtwPiTwo')) drawLines('dtwPiTwo', ta.t, ta.lines.map(function(l) { return l.dial; }), [PI_SPLIT_A, PI_SPLIT_B, '#C7F23E', '#E9E8E1'], Number((s.media || {}).runtime_s || 0));
                var reads = Object.keys(s.asset_reads || {}).map(function(k) { return s.asset_reads[k]; });
                if (reads.length && el('dtwPiAssets')) {
                    var primary = (s.dial || {}).value || [], ts = (s.dial || {}).t || [];
                    var maxT = Math.max.apply(null, [Number((s.media || {}).runtime_s || 0)].concat(reads.map(function(r) { return Number(r.runtime_s || 0); })));
                    var c = el('dtwPiAssets'); var w = c.clientWidth || 640, h = c.clientHeight || 150; if (c.width !== w) c.width = w; if (c.height !== h) c.height = h;
                    var ctx = c.getContext('2d'); if (!ctx) return; ctx.clearRect(0, 0, w, h);
                    function X(sec) { return (sec / (maxT || 1)) * w; } function Y(v) { return h - 10 - (Math.max(0, Math.min(100, v)) / 100) * (h - 20); }
                    [[ts, primary, '#E9E8E1']].concat(reads.map(function(r, i) { return [r.t || [], r.dial || [], ['#E682FF', '#3358FF'][i % 2]]; })).forEach(function(row) { var t = row[0], v = row[1]; ctx.strokeStyle = row[2]; ctx.lineWidth = 1.8; ctx.beginPath(); var st = false; for (var i = 0; i < t.length && i < v.length; i++) { var x = X(t[i]), y = Y(v[i]); if (!st) { ctx.moveTo(x, y); st = true; } else ctx.lineTo(x, y); } if (st) ctx.stroke(); });
                }
            }
            window.dtwPilotRecut = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var note = el('dtwPiNote');
                var tok = dtwBusy(dtwBtn('[onclick^="dtwPilotRecut("]'), 'Opening the recut', note); if (tok === false) return;
                dtwStage(tok, 'Opening the recut on the same room');
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/recut', { method: 'POST', body: '{}' }); adoptPilot(d.session); S.pilotCompare = null; S.pilotMedia = null; dtwIdle(tok); renderPilotConsole(); if (el('dtwPiNote')) el('dtwPiNote').textContent = 'Add ' + (d.session.cut_label || 'Cut B') + ' with the upload below the player, or press Play on a slate.'; }
                catch (e) { dtwIdle(tok, e); }
            };
            window.dtwPilotCompare = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var tok = dtwBusy(dtwBtn(), 'Comparing', true); if (tok === false) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/compare'); S.pilotCompare = { sid: s.id, data: d }; dtwIdle(tok); renderPilotConsole(); } catch (e) { dtwIdle(tok, e); }
            };
            window.dtwPilotComps = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var comps = ((el('dtwPiCompsIn') || {}).value || '').split(',').map(function(x) { return x.trim(); }).filter(Boolean);
                var tok = dtwBusy(dtwBtn(), 'Placing it', true); if (tok === false) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/comps', { method: 'POST', body: JSON.stringify({ comps: comps }) }); s.comps = comps.map(function(c) { return { title: c }; }); s.comp_set = d.comps; dtwIdle(tok); renderPilotConsole(); } catch (e) { dtwIdle(tok, e); }
            };
            window.dtwPilotCritics = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var tok = dtwBusy(dtwBtn(), 'Seating the critics', true); if (tok === false) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/critics', { method: 'POST', body: '{}' }); adoptPilot(d.session); dtwIdle(tok); renderPilotConsole(); var n = el('dtwPiNote'); if (n) n.textContent = d.critics_twin ? ('The room is critics and journalists. A critics twin also exists: ' + d.critics_twin.name + '.') : 'The room is critics and journalists. Press Play.'; } catch (e) { dtwIdle(tok, e); }
            };
            window.dtwPilotTalent = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var name = ((el('dtwPiTalentIn') || {}).value || '').trim(); if (!name) return;
                var role = (el('dtwPiTalentRole') || {}).value || 'lead';
                var tok = dtwBusy(dtwBtn(), 'Attaching', true); if (tok === false) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/talent-attach', { method: 'POST', body: JSON.stringify({ talent: name, role: role }) }); if (d.session) adoptPilot(d.session); dtwIdle(tok); renderPilotConsole(); } catch (e) { dtwIdle(tok, e); }
            };
            window.dtwPilotBrief = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/activation-brief', { method: 'POST', body: '{}' }); s.activation_brief = d.brief; renderPilotConsole(); } catch (e) { var n = el('dtwPiNote'); if (n) n.textContent = e.message; }
            };
            window.dtwPilotSetCast = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var cast = ((el('dtwPiCastLate') || {}).value || '').split(/[,\n]/).map(function(x) { return x.trim(); }).filter(Boolean);
                if (!cast.length) return;
                try { var d = await api('/' + S.key + '/pilot/' + s.id + '/stimulus', { method: 'POST', body: JSON.stringify({ cast: cast }) }); adoptPilot(d.session); var p = await api('/' + S.key + '/pilot/' + s.id + '/prepare', { method: 'POST', body: '{}' }); if (p.session) adoptPilot(p.session); renderPilotConsole(); } catch (e) { var n = el('dtwPiNote'); if (n) n.textContent = e.message; }
            };
            // ------------------------------------------------------------ the Norms tab
            S.normsGenre = S.normsGenre || 'all';
            async function renderNormsTab() {
                var h = el('dtwNoConsole'); if (!h) return;
                if (!S.norms || S.norms.genre !== S.normsGenre) {
                    h.innerHTML = '<div class="dtw-card"><div class="dtw-eyebrow">Norms</div><p class="dtw-muted" style="margin-top:6px">Loading the norms...</p></div>';
                    try { S.norms = await api('/pilot-norms/all?genre=' + encodeURIComponent(S.normsGenre)); } catch (e) { h.innerHTML = '<div class="dtw-card"><div class="dtw-eyebrow">Norms</div><p class="dtw-muted">' + esc(e.message) + '</p></div>'; return; }
                }
                var d = S.norms, fmts = d.formats || [], metrics = d.metrics || [];
                var sel = '<select class="dtw-in" style="display:inline-block;width:auto;padding:3px 6px;font-size:12px" onchange="dtwNormsGenre(this.value)">' + (d.genres || []).map(function(g) { return '<option value="' + esc(g) + '"' + (g === S.normsGenre ? ' selected' : '') + '>' + esc((d.genre_labels || {})[g] || g) + '</option>'; }).join('') + '</select>';
                var head = '<tr><th>Measure</th>' + fmts.map(function(f) { return '<th class="n">' + esc((d.format_labels || {})[f] || f) + '</th>'; }).join('') + '</tr>';
                var rows = metrics.map(function(m) {
                    var cells = fmts.map(function(f) {
                        var col = S.normsGenre === 'all' ? ((d.norms[f] || {}).all || {}) : ((d.norms[f] || {}).genre || {});
                        var c = col[m];
                        if (!c || c.value == null) return '<td class="n dtw-muted">-</td>';
                        var from = c.format_used && c.format_used !== f ? ' title="reads from ' + esc((d.format_labels || {})[c.format_used] || c.format_used) + ' until our own tests fill it"' : '';
                        return '<td class="n"' + from + '>' + Number(c.value).toFixed(1) + '%<span class="cnt">' + (c.n ? esc(c.n) + (c.n === 1 ? ' test' : ' tests') : (c.format_used && c.format_used !== f ? 'nearest' : 'published')) + '</span></td>';
                    }).join('');
                    return '<tr><td>' + esc((d.metric_labels || {})[m] || m) + '</td>' + cells + '</tr>';
                }).join('');
                h.innerHTML = '<div class="dtw-card"><div class="dtw-eyebrow">Norms</div><h3 style="margin-top:0.35rem;">What a number means, by format and genre</h3><div class="dtw-sub">' + esc(d.line || 'Every screening adds to these.') + ' The count under a cell is how many of our own tests sit behind it; a published cell stands until they do.</div><div class="dtw-pi-splitbar" style="margin-top:8px">Genre ' + sel + '</div><div style="overflow-x:auto"><table class="dtw-pi-table dtw-norms-table"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div></div>';
            }
            window.renderNormsTab = renderNormsTab;
            window.dtwNormsGenre = function(v) { S.normsGenre = v || 'all'; renderNormsTab(); };
            window._dtwRender.renderPilotConsole = renderPilotConsole;
            window._dtwRender.renderRun = renderRun; window._dtwRender.renderLaunch = renderLaunch; window._dtwRender.renderSpend = renderSpend; window._dtwRender.renderPanelConsole = renderPanelConsole; window._dtwRender.renderPanelSessions = renderPanelSessions; window._dtwRender.renderPilotConsole = renderPilotConsole;
        })();
