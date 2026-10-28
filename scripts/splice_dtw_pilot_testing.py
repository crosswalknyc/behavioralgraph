#!/usr/bin/env python3
"""Byte-level splices for the Pilot Testing tab. Never StrReplace index.html."""
from pathlib import Path
import shutil
import subprocess
import sys

INDEX = Path("/tmp/bgw-dtiq-nokind/templates/index.html")
BACKUP = Path("/tmp/index.pre_pilot_testing.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


CSS = r"""
        #digitalTwinsIQView .dtw-pi { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
        #digitalTwinsIQView .dtw-pi-grid { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.8fr); gap: 20px; align-items: start; }
        #digitalTwinsIQView .dtw-pi-stage { position: relative; background: #081113; border: 1px solid var(--dtw-rule); border-radius: 12px; overflow: hidden; min-height: 220px; }
        #digitalTwinsIQView .dtw-pi-stage video { width: 100%; display: block; max-height: 420px; background: #081113; }
        #digitalTwinsIQView .dtw-pi-slate { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #E9E8E1; padding: 24px; text-align: center; }
        #digitalTwinsIQView .dtw-pi-slate .t { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; }
        #digitalTwinsIQView .dtw-pi-slate .c { font-size: 42px; font-weight: 300; font-variant-numeric: tabular-nums; letter-spacing: -0.03em; }
        #digitalTwinsIQView .dtw-pi-slate .s { font-size: 12px; color: #9AA09B; }
        #digitalTwinsIQView .dtw-pi-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
        #digitalTwinsIQView .dtw-pi-clock { font-variant-numeric: tabular-nums; font-size: 13px; color: var(--dtw-ink); font-weight: 600; }
        #digitalTwinsIQView .dtw-pi-note { font-size: 12px; color: var(--dtw-body); }
        #digitalTwinsIQView .dtw-pi-dialwrap { position: relative; height: 132px; border: 1px solid var(--dtw-rule); border-radius: 10px; background: var(--dtw-raised); overflow: hidden; }
        #digitalTwinsIQView .dtw-pi-dialwrap canvas { width: 100%; height: 100%; display: block; }
        #digitalTwinsIQView .dtw-pi-dialnum { position: absolute; right: 10px; top: 8px; font-size: 22px; font-weight: 300; font-variant-numeric: tabular-nums; color: var(--dtw-ink); }
        #digitalTwinsIQView .dtw-pi-rail { position: relative; height: 28px; margin-top: 8px; background: var(--dtw-track); border-radius: 999px; }
        #digitalTwinsIQView .dtw-pi-mark { position: absolute; top: 4px; width: 10px; height: 20px; margin-left: -5px; border: 0; border-radius: 3px; cursor: pointer; padding: 0; }
        #digitalTwinsIQView .dtw-pi-mark.love, #digitalTwinsIQView .dtw-pi-mark.wow { background: #C7F23E; }
        #digitalTwinsIQView .dtw-pi-mark.laugh { background: #E682FF; }
        #digitalTwinsIQView .dtw-pi-mark.lean-in { background: #3358FF; }
        #digitalTwinsIQView .dtw-pi-mark.confuse { background: #B7B3D8; }
        #digitalTwinsIQView .dtw-pi-mark.check-out { background: #7C878A; }
        #digitalTwinsIQView .dtw-pi-playhead { position: absolute; top: 0; width: 2px; height: 28px; background: var(--dtw-ink); margin-left: -1px; pointer-events: none; }
        #digitalTwinsIQView .dtw-pi-seats { display: flex; flex-wrap: wrap; gap: 10px; }
        #digitalTwinsIQView .dtw-pi-seat { appearance: none; background: transparent; border: 0; padding: 0; width: 64px; display: flex; flex-direction: column; align-items: center; gap: 3px; cursor: pointer; color: inherit; font-family: inherit; }
        #digitalTwinsIQView .dtw-pi-seat .circ { width: 44px; height: 44px; border-radius: 50%; background: var(--hue, #3358FF); color: #E9E8E1; display: flex; align-items: center; justify-content: center; font-weight: 600; position: relative; overflow: hidden; box-shadow: 0 0 0 2px var(--edge, transparent); }
        #digitalTwinsIQView .dtw-pi-seat .circ img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 50% 25%; }
        #digitalTwinsIQView .dtw-pi-seat .nm { font-size: 11px; font-weight: 600; text-align: center; overflow-wrap: anywhere; }
        #digitalTwinsIQView .dtw-pi-seat .ag { font-size: 10px; color: var(--dtw-mute); }
        #digitalTwinsIQView .dtw-pi-voices { display: flex; flex-direction: column; gap: 8px; }
        #digitalTwinsIQView .dtw-pi-voice { font-size: 13px; line-height: 17px; color: var(--dtw-ink); }
        #digitalTwinsIQView .dtw-pi-voice .k { font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--dtw-mute); font-weight: 600; }
        #digitalTwinsIQView .dtw-pi-list { display: flex; flex-direction: column; gap: 8px; }
        #digitalTwinsIQView .dtw-pi-row { appearance: none; width: 100%; text-align: left; background: var(--dtw-raised); border: 1px solid var(--dtw-rule); border-radius: 10px; padding: 10px 12px; color: inherit; font-family: inherit; cursor: pointer; }
        #digitalTwinsIQView .dtw-pi-row.on { border-color: var(--dtw-ink); }
        #digitalTwinsIQView .dtw-pi-row .h { font-weight: 600; }
        #digitalTwinsIQView .dtw-pi-row .m { font-size: 12px; color: var(--dtw-body); margin-top: 2px; }
        #digitalTwinsIQView .dtw-pi-scores { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
        #digitalTwinsIQView .dtw-pi-score { border: 1px solid var(--dtw-rule); border-radius: 10px; padding: 12px; }
        #digitalTwinsIQView .dtw-pi-score .k { font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--dtw-mute); font-weight: 600; }
        #digitalTwinsIQView .dtw-pi-score .v { font-size: 26px; font-weight: 300; font-variant-numeric: tabular-nums; margin-top: 4px; }
        @media (max-width: 860px) { #digitalTwinsIQView .dtw-pi-grid { grid-template-columns: 1fr; } #digitalTwinsIQView .dtw-pi-scores { grid-template-columns: 1fr; } }
"""

JS = r"""
            S.pilot = S.pilot || null;
            S.pilotKind = S.pilotKind || '';
            S.pilotClock = S.pilotClock || { raf: 0, t: 0, t0: 0, slate: false, bound: '' };
            var PI_EVT = { love: 'Loved it', laugh: 'Laughed', 'lean-in': 'Leaned in', confuse: 'Lost', 'check-out': 'Checked out', wow: 'Wow' };
            function clockFmt(t) { t = Math.max(0, Math.round(Number(t) || 0)); var m = Math.floor(t / 60), s = t % 60; return m + ':' + (s < 10 ? '0' : '') + s; }
            function pilotPrice() { var it = ((S.howto || {}).items || []).filter(function(x) { return x.id === 'pilot'; })[0]; return it ? String(it.cost || '') : '$5,500 a session (same as Focus Group IQ), charged on first Play'; }
            function adoptPilot(sess) {
                if (!sess) return;
                S.pilot = sess;
                var list = (S.twin || {}).pilot_sessions || [];
                var hit = false;
                for (var i = 0; i < list.length; i++) { if (list[i].id === sess.id) { list[i] = sess; hit = true; } }
                if (!hit && S.twin) { S.twin.pilot_sessions = [sess].concat(list); }
            }
            function mediaUrl(s) { if (!s || !S.key) return ''; var m = s.media || {}; return m.s3_key ? ('/api/digital-twins/' + encodeURIComponent(S.key) + '/pilot/' + encodeURIComponent(s.id) + '/media') : ''; }
            function playheadNow() {
                var v = el('dtwPiVideo');
                if (v && v.src && isFinite(v.currentTime)) return v.currentTime;
                return Number((S.pilotClock || {}).t || 0);
            }
            function stopPilotClock() { if (S.pilotClock && S.pilotClock.raf) { try { cancelAnimationFrame(S.pilotClock.raf); } catch (_) {} S.pilotClock.raf = 0; } }
            function revealPilot(t) {
                S.pilotClock.t = t;
                drawPilotDial(t);
                paintPilotRail(t);
                paintPilotLive(t);
                var ck = el('dtwPiClock'); if (ck) ck.textContent = clockFmt(t);
                var num = el('dtwPiDialNum');
                var s = S.pilot || {}, d = s.dial || {}, ts = d.t || [], vs = d.value || [];
                var val = vs.length ? vs[0] : 0;
                for (var i = 0; i < ts.length; i++) { if (ts[i] <= t) val = vs[i]; }
                if (num) num.textContent = val ? Number(val).toFixed(1) : '';
            }
            function drawPilotDial(upto) {
                var c = el('dtwPiDial'); if (!c) return;
                var s = S.pilot || {}, d = s.dial || {}, ts = d.t || [], vs = d.value || [];
                var w = c.clientWidth || 640, h = c.clientHeight || 132;
                if (c.width !== w) c.width = w; if (c.height !== h) c.height = h;
                var ctx = c.getContext('2d'); if (!ctx) return;
                ctx.clearRect(0, 0, w, h);
                var dur = Number((s.media || {}).runtime_s || (ts.length ? ts[ts.length - 1] : 1)) || 1;
                function X(sec) { return (sec / dur) * w; }
                function Y(v) { return h - 10 - (Math.max(0, Math.min(100, v)) / 100) * (h - 20); }
                ctx.strokeStyle = 'rgba(233,232,225,0.12)'; ctx.lineWidth = 1;
                ctx.beginPath(); ctx.moveTo(0, Y(50)); ctx.lineTo(w, Y(50)); ctx.stroke();
                function line(vals, color, width) {
                    if (!vals || !vals.length) return;
                    ctx.strokeStyle = color; ctx.lineWidth = width || 2; ctx.beginPath();
                    var started = false;
                    for (var i = 0; i < ts.length; i++) {
                        if (ts[i] > upto) break;
                        var x = X(ts[i]), y = Y(vals[i]);
                        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
                    }
                    if (started) ctx.stroke();
                }
                var splits = d.splits || {};
                if (splits.women) line(splits.women, '#E682FF', 1.4);
                if (splits.men) line(splits.men, '#3358FF', 1.4);
                if (splits.avid) line(splits.avid, '#C7F23E', 1.2);
                line(vs, '#E9E8E1', 2.2);
                ctx.fillStyle = '#E9E8E1';
                var last = 0;
                for (var j = 0; j < ts.length; j++) { if (ts[j] <= upto) last = j; }
                if (vs.length) { ctx.beginPath(); ctx.arc(X(ts[last] || 0), Y(vs[last] || 0), 3.5, 0, Math.PI * 2); ctx.fill(); }
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
                    return '<div class="dtw-pi-voice"><span class="k">' + esc(clockFmt(e.t)) + '  ' + esc(PI_EVT[e.type] || e.type) + '</span><div>' + esc(e.line || '') + '</div></div>';
                }).join('');
            }
            function bindPilotMedia() {
                var s = S.pilot; if (!s) return;
                var v = el('dtwPiVideo'), slate = el('dtwPiSlate'), url = mediaUrl(s);
                if (v && url) {
                    if (S.pilotClock.bound !== url) { v.src = url; S.pilotClock.bound = url; }
                    v.style.display = 'block';
                    if (slate) slate.style.display = 'none';
                    if (!v._piBound) {
                        v._piBound = true;
                        v.addEventListener('timeupdate', function() { revealPilot(v.currentTime); });
                        v.addEventListener('play', function() {
                            stopPilotClock();
                            (function tick() { if (!v || v.paused) return; revealPilot(v.currentTime); S.pilotClock.raf = requestAnimationFrame(tick); })();
                        });
                        v.addEventListener('pause', function() { stopPilotClock(); revealPilot(v.currentTime); });
                        v.addEventListener('ended', function() { try { dtwPilotStop(true); } catch (_) {} });
                    }
                } else if (slate) {
                    if (v) { v.removeAttribute('src'); v.style.display = 'none'; }
                    slate.style.display = 'flex';
                    S.pilotClock.bound = 'slate';
                }
            }
            function renderPilotStart() {
                var others = (S.runs || []).filter(function(r) { return r.key && r.key !== S.key; }).map(function(r) {
                    return '<option value="' + esc(r.key) + '">' + esc(r.name || r.subject || r.key) + '</option>';
                }).join('');
                var past = ((S.twin || {}).pilot_sessions || []);
                var rows = past.map(function(s) {
                    var on = S.pilot && S.pilot.id === s.id;
                    var sc = s.scorecard || {};
                    var st = s.status || 'draft';
                    return '<button type="button" class="dtw-pi-row' + (on ? ' on' : '') + '" onclick="dtwPilotPick(\'' + esc(s.id) + '\')"><div class="h">' + esc(s.title || 'Untitled test') + '</div><div class="m">' + esc(st) + (sc.overall_like != null ? '  like ' + Number(sc.overall_like).toFixed(1) : '') + '  ' + esc(fmtWhen(s.updated_at || s.created_at)) + '</div></button>';
                }).join('');
                return '<div class="dtw-card"><div class="dtw-eyebrow">Pilot Testing</div><h3 style="margin-top:0.35rem;">Watch a cut with the room</h3>'
                    + '<div class="dtw-sub">Upload a pilot, movie, trailer or episode. Prometheus moderates. Press Play and the twins watch on the clock: a dial, laughs and loves at the second they happen, and you can ask them anything at that timestamp.</div>'
                    + (S.twin ? '<div class="dtw-on">On <b>' + esc(twinName()) + '</b></div>' : '<p class="dtw-p">Pick a twin on the Twins tab first, or build one.</p>')
                    + '<label class="dtw-field" for="dtwPiTitle">Name this test</label><input id="dtwPiTitle" class="dtw-in" placeholder="Season 2 pilot" maxlength="120" autocomplete="off">'
                    + '<label class="dtw-field">The cut (mp4, mov or webm)</label><input id="dtwPiFile" class="dtw-in" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm">'
                    + '<div class="dtw-row2"><div><label class="dtw-field">Title if you do not have the file yet</label><input id="dtwPiCutTitle" class="dtw-in" placeholder="NCIS: New York, episode 1"></div><div><label class="dtw-field">Runtime, minutes</label><input id="dtwPiMins" class="dtw-in" inputmode="decimal" placeholder="42"></div></div>'
                    + '<label class="dtw-field">Add a second twin (optional)</label><select id="dtwPiTwin2" class="dtw-in"><option value="">Just this twin</option>' + others + '</select>'
                    + '<label class="dtw-field">Panelists in the room</label><input id="dtwPiRoom" class="dtw-in" value="12" inputmode="numeric">'
                    + '<div class="dtw-lab-run"><button type="button" class="dtw-btn dtw-btn-primary" onclick="dtwPilotOpen()">Start the test</button><span class="dtw-price">' + esc(pilotPrice()) + '</span></div>'
                    + '<div class="dtw-status" id="dtwPiStatus"></div></div>'
                    + '<div class="dtw-card"><h3>Past tests</h3>' + (rows || '<p class="dtw-muted">No tests on this twin yet.</p>') + '</div>';
            }
            function renderPilotPlayer(s) {
                var roster = ((s.group || {}).roster || []);
                var seats = roster.map(function(r, i) {
                    var hue = hueFor(i), url = r.portrait_url || '';
                    return '<button type="button" class="dtw-pi-seat" style="--hue:' + hue + '" title="' + esc((r.name || '') + ', ' + (r.age || '') + ', ' + (r.city || '')) + '"><span class="circ">' + esc(initials(r.name)) + (url ? '<img src="' + esc(url) + '" alt="" onerror="this.remove()">' : '') + '</span><span class="nm">' + esc(titleCase(r.name || '')) + '</span><span class="ag">' + esc((r.age || '') + (r.city ? ', ' + r.city : '')) + '</span></button>';
                }).join('');
                var ready = s.status === 'ready' || s.status === 'playing' || s.status === 'stopped' || ((s.dial || {}).value || []).length;
                var sc = s.scorecard || {};
                var qs = s.questions || [];
                var qhtml = qs.map(function(q) {
                    var voices = (q.voices || []).slice(0, 3).map(function(v) { return '<div class="dtw-pi-voice">' + esc(v.name || '') + ': ' + esc(v.line || '') + '</div>'; }).join('');
                    return '<div class="dtw-pi-voice"><span class="k">Prometheus at ' + esc(q.playhead_clock || clockFmt(q.playhead_s)) + '</span><div>' + esc(q.q || '') + '</div>' + (q.read ? '<div class="dtw-muted" style="margin-top:4px">' + esc(q.read) + '</div>' : '') + voices + '</div>';
                }).join('');
                var scores = (s.status === 'stopped' || (sc.overall_like != null && s.status !== 'draft')) ? (
                    '<div class="dtw-pi-scores">'
                    + '<div class="dtw-pi-score"><div class="k">Overall like</div><div class="v">' + (sc.overall_like != null ? Number(sc.overall_like).toFixed(1) : '-') + '</div></div>'
                    + '<div class="dtw-pi-score"><div class="k">Would recommend</div><div class="v">' + (sc.recommend_pct != null ? Number(sc.recommend_pct).toFixed(1) + '%' : '-') + '</div></div>'
                    + '<div class="dtw-pi-score"><div class="k">Would finish</div><div class="v">' + (sc.finish_intent_pct != null ? Number(sc.finish_intent_pct).toFixed(1) + '%' : '-') + '</div></div>'
                    + '</div>'
                    + ((sc.favorite_moment || {}).line ? '<p class="dtw-p">Favorite moment at ' + esc((sc.favorite_moment || {}).clock || '') + ': ' + esc((sc.favorite_moment || {}).line) + '</p>' : '')
                    + ((sc.dead_spot || {}).line ? '<p class="dtw-p">Dead spot at ' + esc((sc.dead_spot || {}).clock || '') + ': ' + esc((sc.dead_spot || {}).line) + '</p>' : '')
                    + ((sc.notes || []).map(function(n) { return '<p class="dtw-muted">' + esc(n) + '</p>'; }).join(''))
                ) : '';
                var exp = S.key ? ('<div class="dtw-actions"><a class="dtw-btn" href="/api/digital-twins/' + esc(S.key) + '/pilot/' + esc(s.id) + '/export.csv">CSV</a><a class="dtw-btn" href="/api/digital-twins/' + esc(S.key) + '/pilot/' + esc(s.id) + '/export.pdf">PDF</a><a class="dtw-btn" href="/api/digital-twins/' + esc(S.key) + '/pilot/' + esc(s.id) + '/export.pptx">Deck</a><button type="button" class="dtw-btn" onclick="dtwPilotDelete()">Delete this test</button></div>') : '';
                return '<div class="dtw-card"><div class="dtw-eyebrow">Pilot Testing</div>'
                    + '<div class="dtw-fg-rtwrap"><input id="dtwPiName" class="dtw-fg-rt" value="' + esc(s.title || '') + '" onblur="dtwPilotRename()"><button type="button" class="dtw-link" onclick="S.pilot=null;renderPilotConsole();">New test</button></div>'
                    + '<div class="dtw-pi-seats" style="margin:10px 0 12px">' + seats + '</div>'
                    + '<div class="dtw-pi-grid"><div><div class="dtw-pi-stage"><video id="dtwPiVideo" playsinline controls preload="metadata" style="display:none"></video><div id="dtwPiSlate" class="dtw-pi-slate"><div class="t">' + esc((s.media || {}).title || s.title || 'The cut') + '</div><div class="c" id="dtwPiSlateClock">0:00</div><div class="s">The room is seated. Press Play when you are ready.</div></div></div>'
                    + '<div class="dtw-pi-controls"><button type="button" class="dtw-btn dtw-btn-primary" id="dtwPiPlayBtn" onclick="dtwPilotPlay()">' + (s.status === 'playing' ? 'Playing' : 'Play') + '</button><button type="button" class="dtw-btn" onclick="dtwPilotStop()">Stop</button><span class="dtw-pi-clock" id="dtwPiClock">0:00</span><span class="dtw-pi-note" id="dtwPiNote">' + esc(s.prepare_note || (ready ? 'Ready to play' : 'Preparing the cut')) + '</span></div>'
                    + '<div class="dtw-pi-dialwrap"><canvas id="dtwPiDial"></canvas><div class="dtw-pi-dialnum" id="dtwPiDialNum"></div></div>'
                    + '<div class="dtw-pi-rail" id="dtwPiRail"></div></div>'
                    + '<div><div class="dtw-eyebrow">Live from the room</div><div id="dtwPiLive" class="dtw-pi-voices" style="margin-top:8px"></div>'
                    + '<label class="dtw-field" style="margin-top:14px">Ask the room at this timestamp</label><input id="dtwPiQ" class="dtw-in" placeholder="Would you keep watching?" onkeydown="if(event.key===\'Enter\'){event.preventDefault();dtwPilotAsk();}">'
                    + '<div class="dtw-chips" id="dtwPiKinds"><button type="button" class="dtw-chip" data-k="yes_no" onclick="dtwPilotKind(\'yes_no\')">Yes / no</button><button type="button" class="dtw-chip" data-k="open" onclick="dtwPilotKind(\'open\')">Open</button><button type="button" class="dtw-chip" data-k="rating" onclick="dtwPilotKind(\'rating\')">Rating</button><button type="button" class="dtw-chip" data-k="show_of_hands" onclick="dtwPilotKind(\'show_of_hands\')">Show of hands</button></div>'
                    + '<div class="dtw-lab-run"><button type="button" class="dtw-btn" onclick="dtwPilotAsk()">Ask</button></div>'
                    + '<div id="dtwPiAskNote" class="dtw-status"></div>'
                    + '<div class="dtw-eyebrow" style="margin-top:16px">On the clock</div><div id="dtwPiThread">' + (qhtml || '<p class="dtw-muted">No questions yet. Ask while the cut plays.</p>') + '</div></div></div>'
                    + scores + exp + '</div>';
            }
            function renderPilotConsole() {
                var h = el('dtwPiConsole'); if (!h) return;
                if (!S.twin) { h.innerHTML = renderPilotStart(); return; }
                if (!S.pilot) { h.innerHTML = renderPilotStart(); return; }
                h.innerHTML = renderPilotPlayer(S.pilot);
                bindPilotMedia();
                revealPilot(playheadNow());
                try { remember(); } catch (_) {}
            }
            window.dtwPilotKind = function(k) { S.pilotKind = (S.pilotKind === k) ? '' : k; document.querySelectorAll('#dtwPiKinds .dtw-chip').forEach(function(b) { b.classList.toggle('on', b.getAttribute('data-k') === S.pilotKind); }); };
            window.dtwPilotPick = function(sid) {
                S.pilot = ((S.twin || {}).pilot_sessions || []).filter(function(s) { return s.id === sid; })[0] || null;
                dtwTab('pilot'); renderPilotConsole();
            };
            window.dtwPilotOpen = async function() {
                var key = S.key, st = el('dtwPiStatus');
                if (!key) { if (st) st.textContent = 'Pick a twin on the Twins tab first.'; return; }
                var title = ((el('dtwPiTitle') || {}).value || '').replace(/\s+/g, ' ').trim();
                if (!title) { if (st) st.textContent = 'Name the test first.'; var tn = el('dtwPiTitle'); if (tn) { try { tn.focus(); } catch (_) {} } return; }
                var other = (el('dtwPiTwin2') || {}).value;
                var mins = parseFloat((el('dtwPiMins') || {}).value || '0') || 0;
                var cutTitle = ((el('dtwPiCutTitle') || {}).value || '').trim();
                var body = { title: title, in_room: parseInt((el('dtwPiRoom') || {}).value || '12', 10) || 12, twins: other && other !== key ? [other] : [] };
                if (cutTitle) body.cut_title = cutTitle;
                if (mins > 0) body.runtime_s = Math.round(mins * 60);
                if (st) st.textContent = 'Seating the room...';
                try { var d = await api('/' + key + '/pilot/open', { method: 'POST', body: JSON.stringify(body) }); } catch (e) { if (st) st.textContent = e.message; return; }
                adoptPilot(d.session);
                var file = (el('dtwPiFile') || {}).files && el('dtwPiFile').files[0];
                if (file) {
                    if (st) st.textContent = 'Uploading the cut...';
                    try {
                        var fd = new FormData(); fd.append('file', file); fd.append('title', cutTitle || title);
                        var r = await fetch('/api/digital-twins/' + encodeURIComponent(key) + '/pilot/' + encodeURIComponent(d.session.id) + '/upload', { method: 'POST', credentials: 'same-origin', body: fd });
                        var u = null; try { u = await r.json(); } catch (_) { u = null; }
                        if (!r.ok || !u || u.success === false) throw new Error((u && u.error) || 'The cut could not be stored.');
                        adoptPilot(u.session);
                    } catch (e) { if (st) st.textContent = e.message; }
                }
                if (st) st.textContent = 'Preparing the cut';
                try {
                    var p = await api('/' + key + '/pilot/' + d.session.id + '/prepare', { method: 'POST', body: JSON.stringify({ title: cutTitle || title, runtime_s: body.runtime_s || 0 }) });
                    adoptPilot(p.session);
                } catch (e) { if (st) st.textContent = e.message || 'Preparing the cut'; }
                renderPilotConsole();
            };
            window.dtwPilotPlay = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var note = el('dtwPiNote');
                if (note) note.textContent = s.play_charged ? 'Playing' : 'Starting the room...';
                try {
                    var d = await api('/' + S.key + '/pilot/' + s.id + '/play', { method: 'POST', body: JSON.stringify({ runtime_s: (s.media || {}).runtime_s || 0 }) });
                    adoptPilot(d.session);
                } catch (e) { if (note) note.textContent = e.message; return; }
                var v = el('dtwPiVideo');
                if (v && mediaUrl(S.pilot)) {
                    bindPilotMedia();
                    try { await v.play(); } catch (_) {}
                } else {
                    S.pilotClock.slate = true;
                    S.pilotClock.t0 = Date.now() - (S.pilotClock.t || 0) * 1000;
                    stopPilotClock();
                    (function tick() {
                        if (!S.pilotClock.slate) return;
                        var t = (Date.now() - S.pilotClock.t0) / 1000;
                        var dur = Number((S.pilot.media || {}).runtime_s || 60);
                        if (t >= dur) { t = dur; S.pilotClock.slate = false; revealPilot(t); try { dtwPilotStop(true); } catch (_) {} return; }
                        revealPilot(t);
                        var sl = el('dtwPiSlateClock'); if (sl) sl.textContent = clockFmt(t);
                        S.pilotClock.raf = requestAnimationFrame(tick);
                    })();
                }
                if (note) note.textContent = 'The room is live.';
                var btn = el('dtwPiPlayBtn'); if (btn) btn.textContent = 'Playing';
            };
            window.dtwPilotStop = async function(fromEnd) {
                stopPilotClock();
                S.pilotClock.slate = false;
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); } catch (_) {} }
                if (!S.pilot || !S.key) { renderPilotConsole(); return; }
                try {
                    var d = await api('/' + S.key + '/pilot/' + S.pilot.id + '/stop', { method: 'POST', body: '{}' });
                    adoptPilot(d.session);
                } catch (_) {}
                renderPilotConsole();
                if (fromEnd) revealPilot(Number((S.pilot.media || {}).runtime_s || playheadNow()));
            };
            window.dtwPilotSeek = function(t) {
                t = Number(t || 0);
                var v = el('dtwPiVideo');
                if (v && v.src) { try { v.currentTime = t; } catch (_) {} }
                S.pilotClock.t = t;
                if (S.pilotClock.slate) S.pilotClock.t0 = Date.now() - t * 1000;
                revealPilot(t);
            };
            window.dtwPilotAsk = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                var inp = el('dtwPiQ'); var q = (inp && inp.value || '').trim();
                if (!q) { if (inp) try { inp.focus(); } catch (_) {} return; }
                var note = el('dtwPiAskNote');
                var v = el('dtwPiVideo'); if (v) { try { v.pause(); } catch (_) {} }
                var wasSlate = S.pilotClock.slate; S.pilotClock.slate = false; stopPilotClock();
                var t = playheadNow();
                if (note) note.textContent = 'Asking the room at ' + clockFmt(t) + '...';
                var body = { q: q, playhead_s: t };
                if (S.pilotKind) body.kind = S.pilotKind;
                if (inp) inp.value = '';
                try {
                    var d = await api('/' + S.key + '/pilot/' + s.id + '/ask', { method: 'POST', body: JSON.stringify(body) });
                    if (d.session) adoptPilot(d.session);
                    else if (d.question) { s.questions = s.questions || []; s.questions.push(d.question); }
                } catch (e) { if (note) note.textContent = e.message; if (inp && !inp.value) inp.value = q; return; }
                renderPilotConsole();
                dtwPilotSeek(t);
                if (note) note.textContent = 'Asked at ' + clockFmt(t) + '.';
                if (v && mediaUrl(S.pilot)) { try { await v.play(); } catch (_) {} }
                else if (wasSlate) { S.pilotClock.slate = true; S.pilotClock.t0 = Date.now() - t * 1000; dtwPilotPlay(); }
            };
            window.dtwPilotRename = async function() {
                var s = S.pilot, inp = el('dtwPiName'); if (!s || !S.key || !inp) return;
                var title = (inp.value || '').replace(/\s+/g, ' ').trim();
                if (!title || title === s.title) return;
                try {
                    var d = await api('/' + S.key + '/pilot/' + s.id + '/rename', { method: 'POST', body: JSON.stringify({ title: title }) });
                    if (d.session) adoptPilot(d.session);
                } catch (_) {}
            };
            window.dtwPilotDelete = async function() {
                var s = S.pilot; if (!s || !S.key) return;
                if (!window.confirm('Delete this test? You can undo for 30 days.')) return;
                try { await api('/' + S.key + '/pilot/' + s.id + '/delete', { method: 'POST', body: '{}' }); } catch (e) { alert(e.message); return; }
                var list = (S.twin || {}).pilot_sessions || [];
                S.twin.pilot_sessions = list.filter(function(x) { return x.id !== s.id; });
                S.pilot = null;
                renderPilotConsole();
            };
            window._dtwRender.renderPilotConsole = renderPilotConsole;
"""


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")

    src = splice(
        src,
        '        #digitalTwinsIQView .dtw-fg-mod { display: block; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--dtw-mute); font-weight: 600; margin-bottom: 2px; }\n        </style>',
        '        #digitalTwinsIQView .dtw-fg-mod { display: block; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--dtw-mute); font-weight: 600; margin-bottom: 2px; }\n'
        + CSS +
        '        </style>',
        "css",
    )
    src = splice(
        src,
        '<button type="button" class="dtw-seg" data-tab="panel" role="tab" onclick="dtwTab(\'panel\')">Focus Group IQ</button>',
        '<button type="button" class="dtw-seg" data-tab="panel" role="tab" onclick="dtwTab(\'panel\')">Focus Group IQ</button>\n                    <button type="button" class="dtw-seg" data-tab="pilot" role="tab" onclick="dtwTab(\'pilot\')">Pilot Testing</button>',
        "tab button",
    )
    src = splice(
        src,
        '                <section id="dtwTabPanel" class="dtw-pane dtw-pane-fg">\n                    <div id="dtwPnConsole" class="dtw-fg"></div>\n                </section>',
        '                <section id="dtwTabPanel" class="dtw-pane dtw-pane-fg">\n                    <div id="dtwPnConsole" class="dtw-fg"></div>\n                </section>\n                <section id="dtwTabPilot" class="dtw-pane">\n                    <div id="dtwPiConsole" class="dtw-pi"></div>\n                </section>',
        "pane",
    )
    src = splice(
        src,
        "var S = window._dtw = window._dtw || { runs: [], twin: null, key: '', tab: 'twins', openRun: null, session: null, plan: null, howto: null, loaded: false, horizon: '10', spendDay: 1, spendMode: 'chip', pricing: [], summary: null, panelKind: '', portraitTimers: [], roomPollTimer: null, roomTick: null, libKind: 'all', libQ: '', coverTries: 0 };\n            var TABS = ['twins', 'greenlight', 'launch', 'spend', 'panel'];\n            var LAB_LABEL = { greenlight: 'Greenlight', launch: 'Launch', spend: 'Spend Engine', panel: 'Focus Group IQ', compare: 'Compare', content: 'Content drop', platform_move: 'Platform move', partnership: 'Partnership', pricing: 'Pricing', talent_attach: 'Talent attach', merch_drop: 'Merch drop' };",
        "var S = window._dtw = window._dtw || { runs: [], twin: null, key: '', tab: 'twins', openRun: null, session: null, pilot: null, plan: null, howto: null, loaded: false, horizon: '10', spendDay: 1, spendMode: 'chip', pricing: [], summary: null, panelKind: '', portraitTimers: [], roomPollTimer: null, roomTick: null, libKind: 'all', libQ: '', coverTries: 0 };\n            var TABS = ['twins', 'greenlight', 'launch', 'spend', 'panel', 'pilot'];\n            var LAB_LABEL = { greenlight: 'Greenlight', launch: 'Launch', spend: 'Spend Engine', panel: 'Focus Group IQ', pilot: 'Pilot Testing', compare: 'Compare', content: 'Content drop', platform_move: 'Platform move', partnership: 'Partnership', pricing: 'Pricing', talent_attach: 'Talent attach', merch_drop: 'Merch drop' };",
        "S TABS LAB",
    )
    src = splice(
        src,
        "function remember() { try { localStorage.setItem('bg_dtw_last', JSON.stringify({ key: S.key || '', tab: S.tab || 'twins', sid: S.session ? S.session.id : '' })); } catch (_) {} }",
        "function remember() { try { localStorage.setItem('bg_dtw_last', JSON.stringify({ key: S.key || '', tab: S.tab || 'twins', sid: S.session ? S.session.id : '', pid: S.pilot ? S.pilot.id : '' })); } catch (_) {} }",
        "remember pid",
    )
    src = splice(
        src,
        "                    else if (mem && known(mem.key)) { dtwOpen(mem.key, null, mem.sid || null, null, mem.tab || null); }",
        "                    else if (mem && known(mem.key)) { dtwOpen(mem.key, null, mem.tab === 'pilot' ? (mem.pid || mem.sid) : mem.sid, null, mem.tab || null); }",
        "restore pid",
    )
    src = splice(
        src,
        "                if (tab === 'panel') { renderPanelSessions(); if (!S.session || !el('dtwFgHero')) renderPanelConsole(); else fgShown(); }",
        "                if (tab === 'panel') { renderPanelSessions(); if (!S.session || !el('dtwFgHero')) renderPanelConsole(); else fgShown(); }\n                if (tab === 'pilot') { renderPilotConsole(); }",
        "dtwTab pilot",
    )
    src = splice(
        src,
        "                if (!S.session && el('dtwPnConsole')) renderPanelConsole();\n            }",
        "                if (!S.session && el('dtwPnConsole')) renderPanelConsole();\n                if (!S.pilot && el('dtwPiConsole')) renderPilotConsole();\n            }",
        "empty pilot",
    )
    src = splice(
        src,
        "                S.twin = d.twin; S.key = key; S.summary = d.summary; S.openRun = runId || null; S.session = null; S.plan = null; S.spendDay = 1;",
        "                S.twin = d.twin; S.key = key; S.summary = d.summary; S.openRun = runId || null; S.session = null; S.pilot = null; S.plan = null; S.spendDay = 1;",
        "open clear pilot",
    )
    src = splice(
        src,
        "                if (switched) { ['dtwGlResults', 'dtwLaResults', 'dtwSpResults', 'dtwPnConsole'].forEach(function(id) { if (el(id)) el(id).innerHTML = ''; }); }",
        "                if (switched) { ['dtwGlResults', 'dtwLaResults', 'dtwSpResults', 'dtwPnConsole', 'dtwPiConsole'].forEach(function(id) { if (el(id)) el(id).innerHTML = ''; }); }",
        "switch clear",
    )
    src = splice(
        src,
        "                if (sessionId) { S.session = (S.twin.panel_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || null; }",
        "                if (sessionId && tab === 'pilot') { S.pilot = (S.twin.pilot_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || null; }\n                else if (sessionId) { S.session = (S.twin.panel_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || S.pilot; if (!S.session) S.pilot = (S.twin.pilot_sessions || []).filter(function(s) { return s.id === sessionId; })[0] || null; }",
        "open session vs pilot",
    )
    src = splice(
        src,
                "                if (S.session) renderPanelConsole(); else renderPanelConsole();\n                renderPanelSessions();",
        "                if (S.session) renderPanelConsole(); else renderPanelConsole();\n                renderPanelSessions();\n                if (S.pilot || el('dtwPiConsole')) renderPilotConsole();",
        "open render pilot",
    )
    src = splice(
        src,
        "                dtwTab(tab || (S.session ? 'panel' : S.plan ? 'spend' : run ? ({'launch': 'launch', 'spend': 'spend'}[run.kind] || 'greenlight') : 'twins'));",
        "                dtwTab(tab || (S.pilot ? 'pilot' : S.session ? 'panel' : S.plan ? 'spend' : run ? ({'launch': 'launch', 'spend': 'spend'}[run.kind] || 'greenlight') : 'twins'));",
        "default tab pilot",
    )
    src = splice(
        src,
        "            window._dtwRender.renderGreenlight = renderGreenlight; window._dtwRender.renderLaunch = renderLaunch; window._dtwRender.renderSpend = renderSpend; window._dtwRender.renderPanelConsole = renderPanelConsole; window._dtwRender.renderPanelSessions = renderPanelSessions;",
        JS + "            window._dtwRender.renderGreenlight = renderGreenlight; window._dtwRender.renderLaunch = renderLaunch; window._dtwRender.renderSpend = renderSpend; window._dtwRender.renderPanelConsole = renderPanelConsole; window._dtwRender.renderPanelSessions = renderPanelSessions; window._dtwRender.renderPilotConsole = renderPilotConsole;",
        "js engine",
    )

    # no em dash leftover from score placeholder
    if "\u2014" in src[src.find("dtwTabPilot"): src.find("dtwTabPilot") + 400]:
        raise RuntimeError("em dash landed in the new pane")
    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote {INDEX} ({INDEX.stat().st_size} bytes)")
    r = subprocess.run([sys.executable, str(INDEX.parent.parent / "scripts" / "validate_index_html.py")], cwd=str(INDEX.parent.parent))
    if r.returncode != 0:
        print("[splice] VALIDATOR FAILED, restoring backup")
        shutil.copyfile(BACKUP, INDEX)
        raise SystemExit(1)
    print("[splice] validator ok")


if __name__ == "__main__":
    main()
