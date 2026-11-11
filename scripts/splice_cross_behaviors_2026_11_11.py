#!/usr/bin/env python3
"""Profile IQ: a "Cross behaviors" section inside the Data Cuts panel
(2026-11-11, Jenna: "Build it on the page, free for anyone with the
profile").

Inside one profile, pick two or three behaviors from any category or
interest (a search box per pick, each option with its category and its
own share), optionally one demographic bucket, and size the people who
do all of them: share of the audience, US people, the index with its
plain comparison, and the table of singles, pairs and the whole group.
Export CSV writes the Profile CSV's own columns; Build as a cut hands
the words to Prometheus's build flow, unchanged.

The page never computes a figure. It asks
/api/profile-iq/cross-behaviors/<key> (GET the rows to pick, POST the
picks), which is prometheus.crosstab's read: the engine and the record a
chat answer uses, so the page and the chat state one number. Free: no
quote, no charge. Same access gate as the profile file.

Three edits:
  1. the section's functions, ahead of the Data Cuts panel's opener;
  2. the panel mounts the section before it is placed;
  3. a profile with no other cut still shows the Data Cuts button (the
     section lives there), Gen Pop excepted.

Byte-level splice (templates/index.html is too large for an in-editor
replace). Every anchor must occur exactly once. Run, then
`python3 scripts/validate_index_html.py`.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_cross_behaviors.html")

EDITS = []


def edit(desc, old, new):
    EDITS.append((desc, old, new))


SECTION_JS = r"""        // ─── Cross behaviors (2026-11-11, Jenna: "Build it on the page, free for anyone with the profile") ───
        // A section inside the Data Cuts panel: two or three behaviors from any category or interest,
        // an optional demographic bucket, and the people in this audience who do all of them. The page
        // never computes a figure: it asks /api/profile-iq/cross-behaviors/<key>, the same read
        // Prometheus answers with, so the page and the chat state one number. No quote, no charge.
        const _piqCross = { key: '', picks: [null, null, null], demo: null, demos: null, hasInterests: true,
                            result: null, open: false, busy: false, msg: '', notes: [], seq: 0 };
        function _piqCrossKey() {
            try {
                return String((_comparisonRunsState && _comparisonRunsState.currentKey)
                    || (typeof currentProfileData !== 'undefined' && currentProfileData && currentProfileData.s3Key) || '');
            } catch (_) { return ''; }
        }
        function _piqCrossUrl(key) {
            return '/api/profile-iq/cross-behaviors/' + String(key).split('/').map(encodeURIComponent).join('/');
        }
        function _piqCrossEsc(s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        }
        function _piqCrossNum(n) { return (n == null || n === '') ? '' : Number(n).toLocaleString('en-US'); }
        function _piqCrossPct(n) { return (n == null || n === '') ? '' : Number(n).toFixed(1) + '%'; }
        function _piqCrossFit(pop) {
            // the panel grows for this section; keep it on screen
            if (!pop) return;
            pop.style.maxWidth = _piqCross.open ? 'min(660px, 94vw)' : '380px';
            pop.style.minWidth = _piqCross.open ? 'min(560px, 94vw)' : '280px';
            pop.style.maxHeight = _piqCross.open ? '84vh' : '60vh';
            const r = pop.getBoundingClientRect();
            if (r.right > window.innerWidth - 8) pop.style.left = Math.max(8, window.innerWidth - 8 - r.width) + 'px';
            if (r.bottom > window.innerHeight - 8) pop.style.top = Math.max(8, window.innerHeight - 8 - r.height) + 'px';
        }
        function _piqCrossMount(pop) {
            const key = _piqCrossKey();
            if (!pop || !key || /gen[_ ]?pop/i.test(key)) return;
            if (_piqCross.key !== key) {
                // a different profile: nothing carries over
                Object.assign(_piqCross, { key: key, picks: [null, null, null], demo: null, demos: null, hasInterests: true,
                                           result: null, busy: false, msg: '', notes: [] });
            }
            const wrap = document.createElement('div');
            wrap.id = 'piqCrossWrap';
            wrap.style.cssText = 'border-top:1px solid var(--border-color, #2c3041);margin-top:0.6rem;padding-top:0.55rem;';
            const head = document.createElement('div');
            head.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:0.5rem;cursor:pointer;';
            head.innerHTML = '<span style="font-size:0.78rem;font-weight:600;color:var(--text-secondary, #9ca3af);text-transform:uppercase;letter-spacing:0.04em;">Cross behaviors</span>'
                + '<span id="piqCrossCaret" style="font-size:0.75rem;color:var(--accent-purple, #7c3aed);">' + (_piqCross.open ? 'Hide' : 'Open') + '</span>';
            const body = document.createElement('div');
            body.id = 'piqCrossBody';
            body.style.display = _piqCross.open ? 'block' : 'none';
            head.onclick = function() {
                _piqCross.open = !_piqCross.open;
                body.style.display = _piqCross.open ? 'block' : 'none';
                const c = document.getElementById('piqCrossCaret');
                if (c) c.textContent = _piqCross.open ? 'Hide' : 'Open';
                if (_piqCross.open) _piqCrossRender();
                _piqCrossFit(pop);
            };
            wrap.appendChild(head);
            wrap.appendChild(body);
            pop.appendChild(wrap);
            if (_piqCross.open) { _piqCrossRender(); setTimeout(function() { _piqCrossFit(pop); }, 0); }
        }
        function _piqCrossLoadDemos() {
            if (_piqCross.demos || !_piqCross.key) return;
            const key = _piqCross.key;
            fetch(_piqCrossUrl(key), { credentials: 'same-origin' }).then(function(r) { return r.json(); }).then(function(d) {
                if (_piqCross.key !== key || !d || !d.success) return;
                _piqCross.demos = d.demographics || [];
                _piqCross.hasInterests = d.has_interests !== false;
                _piqCrossRender();
            }).catch(function() {});
        }
        function _piqCrossSearch(i, q, listEl) {
            const key = _piqCross.key, seq = ++_piqCross.seq;
            fetch(_piqCrossUrl(key) + '?q=' + encodeURIComponent(q || ''), { credentials: 'same-origin' })
                .then(function(r) { return r.json(); }).then(function(d) {
                    if (seq !== _piqCross.seq || _piqCross.key !== key || !listEl.isConnected) return;
                    const rows = ((d && d.categories) || []).concat((d && d.behaviors) || []);
                    if (!d || !d.success || !rows.length) {
                        listEl.innerHTML = '<div style="padding:0.4rem 0.5rem;font-size:0.76rem;color:var(--text-secondary, #9ca3af);">'
                            + _piqCrossEsc((d && d.message) || 'No row on this profile matches that. Try another name.') + '</div>';
                        listEl.style.display = 'block';
                        return;
                    }
                    listEl.innerHTML = '';
                    rows.forEach(function(o) {
                        const row = document.createElement('div');
                        row.style.cssText = 'display:flex;justify-content:space-between;gap:0.75rem;padding:0.34rem 0.5rem;border-radius:5px;cursor:pointer;font-size:0.8rem;';
                        row.onmouseenter = function() { row.style.background = 'rgba(124,58,237,0.12)'; };
                        row.onmouseleave = function() { row.style.background = 'transparent'; };
                        row.innerHTML = '<span style="color:var(--text-primary, #e6e6e6);overflow-wrap:anywhere;">' + _piqCrossEsc(o.label) + '</span>'
                            + '<span style="color:var(--text-secondary, #9ca3af);font-size:0.72rem;white-space:nowrap;">' + _piqCrossEsc(o.category)
                            + (o.share_pct != null ? ' · ' + _piqCrossPct(o.share_pct) : '') + '</span>';
                        row.onmousedown = function(e) { e.preventDefault(); };
                        row.onclick = function() { _piqCross.picks[i] = o; _piqCross.result = null; _piqCross.msg = ''; _piqCross.notes = []; _piqCrossRender(); };
                        listEl.appendChild(row);
                    });
                    listEl.style.display = 'block';
                }).catch(function() {});
        }
        function _piqCrossRender() {
            const body = document.getElementById('piqCrossBody');
            if (!body) return;
            _piqCrossLoadDemos();
            const lab = 'font-size:0.68rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-secondary, #9ca3af);margin:0.55rem 0 0.25rem;';
            const box = 'background:var(--bg-input, #15171f);color:var(--text-primary, #e6e6e6);border:1px solid var(--border-color, #2c3041);border-radius:6px;padding:0.4rem 0.5rem;font-size:0.82rem;width:100%;box-sizing:border-box;font-family:inherit;';
            body.innerHTML = '';
            const hint = document.createElement('div');
            hint.style.cssText = 'font-size:0.72rem;color:var(--text-secondary, #9ca3af);margin:0.4rem 0 0.1rem;line-height:1.35;';
            hint.textContent = 'Pick two or three behaviors from any category' + (_piqCross.hasInterests ? ' or interest' : '')
                + '. The result is the people in this audience who do all of them.'
                + (_piqCross.hasInterests ? '' : ' This profile has no interest rows.');
            body.appendChild(hint);
            [0, 1, 2].forEach(function(i) {
                const l = document.createElement('div');
                l.style.cssText = lab;
                l.textContent = 'Behavior ' + (i + 1) + (i === 2 ? ' (optional)' : '');
                body.appendChild(l);
                const p = _piqCross.picks[i];
                if (p) {
                    const chip = document.createElement('div');
                    chip.style.cssText = box + 'display:flex;justify-content:space-between;align-items:center;gap:0.6rem;';
                    chip.innerHTML = '<span style="overflow-wrap:anywhere;">' + _piqCrossEsc(p.label) + '</span><span style="white-space:nowrap;font-size:0.72rem;color:var(--text-secondary, #9ca3af);">'
                        + _piqCrossEsc(p.category) + (p.share_pct != null ? ' · ' + _piqCrossPct(p.share_pct) : '')
                        + ' <a href="#" data-x="1" style="color:var(--accent-purple, #7c3aed);text-decoration:none;margin-left:0.45rem;">Change</a></span>';
                    chip.querySelector('[data-x]').onclick = function(e) { e.preventDefault(); _piqCross.picks[i] = null; _piqCross.result = null; _piqCross.msg = ''; _piqCrossRender(); };
                    body.appendChild(chip);
                    return;
                }
                const inp = document.createElement('input');
                inp.type = 'text';
                inp.placeholder = 'Search a brand, an interest or a category';
                inp.autocomplete = 'off';
                inp.style.cssText = box;
                const list = document.createElement('div');
                list.style.cssText = 'display:none;max-height:176px;overflow:auto;border:1px solid var(--border-color, #2c3041);border-radius:6px;margin-top:0.2rem;padding:0.15rem;';
                let t = null;
                const go = function() { clearTimeout(t); t = setTimeout(function() { _piqCrossSearch(i, inp.value.trim(), list); }, 170); };
                inp.addEventListener('input', go);
                inp.addEventListener('focus', go);
                inp.addEventListener('blur', function() { setTimeout(function() { if (list.isConnected) list.style.display = 'none'; }, 160); });
                body.appendChild(inp);
                body.appendChild(list);
            });
            const dl = document.createElement('div');
            dl.style.cssText = lab;
            dl.textContent = 'Narrow by (optional)';
            body.appendChild(dl);
            const sel = document.createElement('select');
            sel.style.cssText = box;
            let opts = '<option value="">Everyone in this audience</option>', grp = '';
            (_piqCross.demos || []).forEach(function(d, j) {
                if (d.category !== grp) { opts += (grp ? '</optgroup>' : '') + '<optgroup label="' + _piqCrossEsc(d.category) + '">'; grp = d.category; }
                const on = _piqCross.demo && _piqCross.demo.column === d.column && _piqCross.demo.value === d.value;
                opts += '<option value="' + j + '"' + (on ? ' selected' : '') + '>' + _piqCrossEsc(d.label) + ' · ' + _piqCrossPct(d.share_pct) + '</option>';
            });
            sel.innerHTML = opts + (grp ? '</optgroup>' : '');
            sel.onchange = function() { _piqCross.demo = sel.value === '' ? null : (_piqCross.demos || [])[Number(sel.value)] || null; _piqCross.result = null; _piqCross.msg = ''; _piqCrossRender(); };
            body.appendChild(sel);
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:0.5rem;align-items:center;margin-top:0.7rem;flex-wrap:wrap;';
            const btnCss = 'border-radius:6px;padding:0.4rem 0.8rem;font-size:0.8rem;font-weight:600;cursor:pointer;font-family:inherit;';
            const goBtn = document.createElement('button');
            goBtn.type = 'button';
            goBtn.textContent = _piqCross.busy ? 'Sizing…' : 'Size this group';
            goBtn.disabled = !!_piqCross.busy;
            goBtn.style.cssText = btnCss + 'background:var(--accent-purple, #7c3aed);color:#fff;border:1px solid var(--accent-purple, #7c3aed);';
            goBtn.onclick = _piqCrossRun;
            const clr = document.createElement('a');
            clr.href = '#';
            clr.textContent = 'Clear';
            clr.style.cssText = 'font-size:0.75rem;color:var(--accent-purple, #7c3aed);text-decoration:none;';
            clr.onclick = function(e) { e.preventDefault(); Object.assign(_piqCross, { picks: [null, null, null], demo: null, result: null, msg: '', notes: [] }); _piqCrossRender(); };
            row.appendChild(goBtn);
            row.appendChild(clr);
            body.appendChild(row);
            if (_piqCross.msg || (_piqCross.notes || []).length) {
                const m = document.createElement('div');
                m.style.cssText = 'font-size:0.76rem;line-height:1.4;margin-top:0.5rem;color:' + (_piqCross.msg ? '#fd971f' : 'var(--text-secondary, #9ca3af)') + ';';
                m.textContent = [_piqCross.msg].concat(_piqCross.notes || []).filter(Boolean).join(' ');
                body.appendChild(m);
            }
            const d = _piqCross.result;
            if (d) {
                const res = document.createElement('div');
                res.id = 'piqCrossResult';
                res.style.cssText = 'margin-top:0.7rem;border:1px solid rgba(124,58,237,0.25);background:rgba(124,58,237,0.06);border-radius:6px;padding:0.6rem 0.65rem;';
                const big = 'font-size:1.25rem;font-weight:700;color:var(--text-primary, #e6e6e6);line-height:1.15;';
                const sub = 'font-size:0.68rem;color:var(--text-secondary, #9ca3af);';
                let h = '<div style="display:flex;gap:1.3rem;flex-wrap:wrap;align-items:flex-end;margin-bottom:0.55rem;">'
                    + '<div><div style="' + big + '">' + _piqCrossEsc(d.share_text || _piqCrossPct(d.share_pct)) + '</div><div style="' + sub + '">of the audience</div></div>'
                    + '<div><div style="' + big + '">' + _piqCrossNum(d.us) + '</div><div style="' + sub + '">US people</div></div>';
                if (d.index != null) {
                    h += '<div><div style="' + big + '">' + _piqCrossEsc(d.index) + '</div><div style="' + sub + '">index, ' + _piqCrossEsc(d.times || '') + '</div></div>';
                }
                h += '</div><div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.76rem;">'
                    + '<tr style="color:var(--text-secondary, #9ca3af);text-transform:uppercase;letter-spacing:0.04em;font-size:0.64rem;">'
                    + '<th style="text-align:left;padding:0.25rem 0.3rem;font-weight:600;">Group</th><th style="text-align:right;padding:0.25rem 0.3rem;font-weight:600;">Share of audience</th>'
                    + '<th style="text-align:right;padding:0.25rem 0.3rem;font-weight:600;">US people</th><th style="text-align:right;padding:0.25rem 0.3rem;font-weight:600;">Share of the US</th>'
                    + '<th style="text-align:right;padding:0.25rem 0.3rem;font-weight:600;">Index</th></tr>';
                (d.table || []).forEach(function(r) {
                    const w = r.kind === 'all' ? 'font-weight:700;' : '';
                    h += '<tr style="border-top:1px solid var(--border-color, #2c3041);' + w + '"><td style="padding:0.3rem;text-align:left;overflow-wrap:anywhere;">' + _piqCrossEsc(r.group) + '</td>'
                        + '<td style="padding:0.3rem;text-align:right;white-space:nowrap;">' + _piqCrossPct(r.share_pct) + '</td>'
                        + '<td style="padding:0.3rem;text-align:right;white-space:nowrap;">' + _piqCrossNum(r.us) + '</td>'
                        + '<td style="padding:0.3rem;text-align:right;white-space:nowrap;">' + _piqCrossPct(r.us_share_pct) + '</td>'
                        + '<td style="padding:0.3rem;text-align:right;white-space:nowrap;" title="' + _piqCrossEsc(r.times || '') + '">' + (r.index == null ? '' : _piqCrossEsc(r.index)) + '</td></tr>';
                });
                h += '</table></div><div style="font-size:0.74rem;color:var(--text-secondary, #9ca3af);line-height:1.4;margin-top:0.5rem;">' + _piqCrossEsc(d.read || '') + '</div>'
                    + '<div style="font-size:0.68rem;color:var(--text-secondary, #9ca3af);line-height:1.4;margin-top:0.35rem;">Rows read on this profile: ' + _piqCrossEsc(d.rows_read || '')
                    + '. Each person is counted once and meets every one of these in the window.</div>'
                    + '<div style="display:flex;gap:0.5rem;margin-top:0.6rem;flex-wrap:wrap;">'
                    + '<button type="button" id="piqCrossCsv" style="' + btnCss + 'background:transparent;color:var(--text-primary, #e6e6e6);border:1px solid var(--border-color, #2c3041);">Export CSV</button>'
                    + '<button type="button" id="piqCrossCut" style="' + btnCss + 'background:transparent;color:var(--text-primary, #e6e6e6);border:1px solid var(--border-color, #2c3041);">Build as a cut</button></div>';
                res.innerHTML = h;
                body.appendChild(res);
                res.querySelector('#piqCrossCsv').onclick = _piqCrossExport;
                res.querySelector('#piqCrossCut').onclick = _piqCrossBuildCut;
            }
            _piqCrossFit(document.getElementById('compareDateRangesPopover'));
        }
        function _piqCrossRun() {
            const picks = _piqCross.picks.filter(Boolean).map(function(p) { return { column: p.column, value: p.value, whole: !!p.whole }; });
            if (picks.length < 2) { _piqCross.msg = 'Pick at least two different behaviors to size a group.'; _piqCross.result = null; _piqCrossRender(); return; }
            if (_piqCross.demo) picks.push({ column: _piqCross.demo.column, value: _piqCross.demo.value });
            const key = _piqCross.key;
            Object.assign(_piqCross, { busy: true, msg: '', notes: [] });
            _piqCrossRender();
            fetch(_piqCrossUrl(key), { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                                       body: JSON.stringify({ behaviors: picks }) })
                .then(function(r) { return r.json(); }).then(function(d) {
                    if (_piqCross.key !== key) return;
                    _piqCross.busy = false;
                    _piqCross.notes = (d && d.notes) || [];
                    if (d && d.success) { _piqCross.result = d; _piqCross.msg = ''; }
                    else { _piqCross.result = null; _piqCross.msg = (d && (d.message || d.error)) || 'That group could not be sized right now.'; }
                    _piqCrossRender();
                }).catch(function() {
                    if (_piqCross.key !== key) return;
                    Object.assign(_piqCross, { busy: false, result: null, msg: 'That group could not be sized right now.' });
                    _piqCrossRender();
                });
        }
        function _piqCrossExport() {
            const d = _piqCross.result;
            if (!d || !d.csv) return;
            const cell = function(v) { v = v == null ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
            const text = d.csv.map(function(r) { return r.map(cell).join(','); }).join('\n') + '\n';
            const name = String(d.subject || 'Profile').replace(/[^A-Za-z0-9 _.&+'-]+/g, ' ').replace(/\s+/g, ' ').trim() + ' - Cross Behaviors.csv';
            const a = document.createElement('a');
            a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
            a.download = name;
            document.body.appendChild(a);
            a.click();
            setTimeout(function() { URL.revokeObjectURL(a.href); a.remove(); }, 500);
        }
        function _piqCrossBuildCut() {
            // the words go to Prometheus's own build flow, unchanged: it drafts the brief and prices the cut there
            const d = _piqCross.result;
            if (!d || !d.cut_order) return;
            try { if (typeof _closeCompareDateRangesPopover === 'function') _closeCompareDateRangesPopover(); } catch (_) {}
            try { if (typeof window.prometheusExpand === 'function') window.prometheusExpand(); } catch (_) {}
            setTimeout(function() {
                const input = document.getElementById('synthChatInput');
                if (!input) return;
                input.value = d.cut_order;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.focus();
                try { if (typeof synthChatSubmit === 'function') synthChatSubmit(); } catch (_) {}
            }, 350);
        }

"""

edit("section functions",
     """        window._openCompareDateRangesPopover = function _openCompareDateRangesPopover() {
""",
     SECTION_JS + """        window._openCompareDateRangesPopover = function _openCompareDateRangesPopover() {
""")

edit("mount in the panel",
     """            document.body.appendChild(pop);
            const r = btn.getBoundingClientRect();
            const pad = 6;
            const popW = Math.min(320, Math.max(240, pop.offsetWidth || 260));
""",
     """            try { _piqCrossMount(pop); } catch (e) { console.warn('[cross behaviors] mount failed', e); }
            document.body.appendChild(pop);
            const r = btn.getBoundingClientRect();
            const pad = 6;
            const popW = Math.min(320, Math.max(240, pop.offsetWidth || 260));
""")

edit("the button stays for a profile with no other cut",
     """                } else {
                    if (deltasBtn) deltasBtn.style.display = 'none';
                    if (typeof _resetComparisonRunsState === 'function') {
                        _resetComparisonRunsState('', profileId, []);
                    }
                }
""",
     """                } else {
                    // Cross behaviors lives in the Data Cuts panel (2026-11-11), so a profile with no
                    // other cut keeps the button; Gen Pop has nothing to cross and hides it as before.
                    const _piqCrossHere = !/gen[_ ]?pop/i.test(String(profileId || ''));
                    if (deltasBtn) {
                        deltasBtn.style.display = _piqCrossHere ? '' : 'none';
                        if (_piqCrossHere) {
                            deltasBtn.onclick = function(ev) {
                                if (ev) { ev.preventDefault(); ev.stopPropagation(); }
                                if (typeof _openCompareDateRangesPopover === 'function') _openCompareDateRangesPopover();
                            };
                        }
                    }
                    if (typeof _resetComparisonRunsState === 'function') {
                        _resetComparisonRunsState('', profileId, []);
                    }
                }
""")


def main():
    src = INDEX.read_text(encoding="utf-8")
    if "function _piqCrossMount(pop)" in src:
        print("already applied")
        return
    BACKUP.write_text(src, encoding="utf-8")
    for desc, old, new in EDITS:
        n = src.count(old)
        if n != 1:
            raise RuntimeError(f"[{desc}] anchor found {n}x")
        src = src.replace(old, new)
    INDEX.write_text(src, encoding="utf-8")
    print(f"applied {len(EDITS)} edits; {len(src.encode('utf-8')):,} bytes")


if __name__ == "__main__":
    main()
