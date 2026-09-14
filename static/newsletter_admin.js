/* Newsletter CMS for Admin > Newsletter */
(function () {
    const state = {
        data: null,
        view: 'campaigns',
        editingId: null,
        reportId: null,
        dirty: false,
        sendBusy: false,
        report: null,
        reportWho: '',
        reportSort: {
            recips: { key: '', dir: 'asc' },
            downloads: { key: '', dir: 'asc' },
            links: { key: '', dir: 'asc' },
            outcomes: { key: '', dir: 'asc' },
            who: { key: '', dir: 'asc' },
        },
    };

    function $(id) { return document.getElementById(id); }

    function toast(msg, err) {
        if (typeof showToast === 'function') showToast(msg, !!err);
        else alert(msg);
    }

    async function api(path, opts) {
        const resp = await fetch(path, {
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', ...(opts && opts.headers || {}) },
            ...opts,
        });
        const data = await resp.json().catch(() => ({ success: false, error: 'Bad response' }));
        if (!resp.ok || data.success === false) {
            throw new Error(data.error || ('HTTP ' + resp.status));
        }
        return data;
    }

    function fmtNum(n) {
        n = Number(n || 0);
        return n.toLocaleString('en-US');
    }

    function fmtMoney(n) {
        n = Number(n || 0);
        return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function fmtWhen(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return iso;
        return d.toLocaleString();
    }

    function listName(id) {
        const lists = (state.data && state.data.lists) || [];
        const hit = lists.find((l) => l.id === id);
        return hit ? hit.name : id || 'No list';
    }

    function segmentName(id) {
        const segs = (state.data && state.data.segments) || [];
        const hit = segs.find((s) => s.id === id);
        return hit ? hit.name : id || '';
    }

    function audienceValue(camp) {
        if (camp && camp.segment_id) return 'segment:' + camp.segment_id;
        return 'list:' + ((camp && camp.list_id) || 'the-read');
    }

    function parseAudience(value) {
        value = value || '';
        if (value.indexOf('segment:') === 0) {
            return { list_id: '', segment_id: value.slice(8) };
        }
        if (value.indexOf('list:') === 0) {
            return { list_id: value.slice(5), segment_id: '' };
        }
        return { list_id: value, segment_id: '' };
    }

    function audienceCount(value) {
        const parsed = parseAudience(value);
        if (parsed.segment_id) {
            const seg = ((state.data && state.data.segments) || []).find((s) => s.id === parsed.segment_id);
            return seg ? (seg.subscriber_count || 0) : 0;
        }
        const list = ((state.data && state.data.lists) || []).find((l) => l.id === parsed.list_id);
        return list ? (list.subscriber_count || 0) : 0;
    }

    function audienceLabel(value) {
        const parsed = parseAudience(value);
        if (parsed.segment_id) return segmentName(parsed.segment_id) || 'Segment';
        return listName(parsed.list_id);
    }

    function showView(name) {
        state.view = name;
        ['campaigns', 'editor', 'audience', 'report', 'outcomes'].forEach((v) => {
            const el = $('nl-view-' + v);
            if (el) el.classList.toggle('nl-hidden', v !== name);
        });
        document.querySelectorAll('#section-newsletter .nl-nav button').forEach((b) => {
            const tab = b.getAttribute('data-view');
            const on = tab === name || (tab === 'campaigns' && (name === 'editor' || name === 'report'));
            b.classList.toggle('on', on);
        });
    }

    function renderStats() {
        const s = (state.data && state.data.stats) || {};
        $('nl-stat-subs').textContent = fmtNum(s.subscribers);
        $('nl-stat-sent').textContent = fmtNum(s.emails_sent);
        $('nl-stat-open').textContent = (s.avg_open_rate || 0) + '%';
        $('nl-stat-click').textContent = (s.avg_click_rate || 0) + '%';
        if ($('nl-stat-downloads')) $('nl-stat-downloads').textContent = fmtNum(s.downloads);
        const ident = (state.data && state.data.from_identity) || 'The Read <no_reply@crosswalknyc.com>';
        $('nl-from-line').textContent = 'Sends from ' + ident + '. Replies go to hello@crosswalknyc.com.';
    }

    function renderCampaigns() {
        const host = $('nl-campaign-grid');
        const camps = (state.data && state.data.campaigns) || [];
        if (!camps.length) {
            host.innerHTML = '<div class="nl-empty">No newsletters yet. Compose one, or the first issue of The Read will land here.</div>';
            return;
        }
        host.innerHTML = camps.map((c) => {
            const stats = c.stats || {};
            const dl = c.download_stats || {};
            let meta = c.status === 'sent'
                ? `${fmtNum(stats.sent)} sent · ${c.open_rate}% opened · ${c.click_rate}% clicked`
                : (c.status === 'sending'
                    ? `${fmtNum(stats.sent || 0)} sent so far · still going`
                    : (c.status === 'scheduled'
                        ? 'Sends ' + fmtWhen(c.scheduled_at)
                        : (c.subject || 'Draft · no subject yet')));
            if (dl.unique_downloads) {
                meta += ` · ${fmtNum(dl.unique_downloads)} downloads`;
            }
            const sendBtn = c.status === 'sent'
                ? `<button class="btn btn-small btn-secondary" onclick="nlOpenReport('${esc(c.id)}')">Report</button>`
                : (c.status === 'sending'
                    ? `<button class="btn btn-small btn-secondary" disabled>Sending…</button>`
                    : `<button class="btn btn-small btn-primary" onclick="nlOpenEditor('${esc(c.id)}', true)">Send</button>`);
            return `<article class="nl-card">
                <div class="nl-card-preview"><iframe src="/n/preview/${encodeURIComponent(c.id)}" loading="lazy"></iframe></div>
                <div class="nl-card-body">
                    <span class="nl-badge ${c.status || 'draft'}">${c.status || 'draft'}</span>
                    <h3>${esc(c.name || 'Untitled')}</h3>
                    <div class="meta">${esc(meta)}</div>
                    <div class="nl-card-foot">
                        <button class="btn btn-small btn-secondary" onclick="nlOpenEditor('${esc(c.id)}')">Edit</button>
                        ${sendBtn}
                        <button class="btn btn-small btn-secondary" onclick="nlDuplicate('${esc(c.id)}')">Duplicate</button>
                        ${c.status === 'sent' || c.status === 'sending' ? '' : `<button class="btn btn-small btn-secondary" onclick="nlDeleteCampaign('${esc(c.id)}')">Delete</button>`}
                    </div>
                </div>
            </article>`;
        }).join('');
    }

    function fillListSelect(sel, current, includeAll) {
        const lists = (state.data && state.data.lists) || [];
        const opts = (includeAll ? `<option value="">Any list</option>` : '') + lists.map((l) => {
            const n = l.subscriber_count || 0;
            const selAttr = l.id === current ? ' selected' : '';
            return `<option value="${esc(l.id)}"${selAttr}>${esc(l.name)} (${n})</option>`;
        }).join('');
        sel.innerHTML = opts;
    }

    function fillAudienceSelect(sel, camp) {
        const lists = (state.data && state.data.lists) || [];
        const segs = (state.data && state.data.segments) || [];
        const current = audienceValue(camp);
        let html = '<optgroup label="Lists">';
        html += lists.map((l) => {
            const val = 'list:' + l.id;
            return `<option value="${esc(val)}"${val === current ? ' selected' : ''}>${esc(l.name)} (${l.subscriber_count || 0})</option>`;
        }).join('');
        html += '</optgroup>';
        if (segs.length) {
            html += '<optgroup label="Segments">';
            html += segs.map((s) => {
                const val = 'segment:' + s.id;
                return `<option value="${esc(val)}"${val === current ? ' selected' : ''}>${esc(s.name)} (${s.subscriber_count || 0})</option>`;
            }).join('');
            html += '</optgroup>';
        }
        sel.innerHTML = html;
    }

    function fillDownloadFields(camp) {
        const dl = (camp && camp.download) || {};
        $('nl-dl-enabled').checked = !!dl.enabled;
        $('nl-dl-title').value = dl.title || '';
        $('nl-dl-paid').checked = !!dl.paid;
        $('nl-dl-price').value = dl.paid ? (dl.price_usd || 25) : 25;
        fillListSelect($('nl-dl-list'), dl.list_id || 'the-read');
        $('nl-dl-price-wrap').classList.toggle('nl-hidden', !$('nl-dl-paid').checked);
        const status = $('nl-dl-file-status');
        if (dl.has_file || dl.filename) {
            status.textContent = 'Attached: ' + (dl.original_name || dl.filename);
        } else {
            status.textContent = 'No file attached yet.';
        }
        const link = $('nl-dl-link');
        if (dl.enabled && camp && camp.download_url) {
            link.innerHTML = `Public page: <a href="${esc(camp.download_url)}" target="_blank" rel="noopener">${esc(camp.download_url)}</a>`;
        } else {
            link.textContent = '';
        }
    }

    window.nlTogglePaid = function () {
        $('nl-dl-price-wrap').classList.toggle('nl-hidden', !$('nl-dl-paid').checked);
    };

    function fillLinkedInFields(camp) {
        const li = (camp && camp.linkedin) || {};
        if ($('nl-li-enabled')) $('nl-li-enabled').checked = li.enabled !== false;
        if ($('nl-li-headline')) $('nl-li-headline').value = li.headline || '';
        if ($('nl-li-text')) $('nl-li-text').value = li.text || '';
        const status = $('nl-li-status');
        if (status) {
            if (li.post_url) status.innerHTML = `Posted: <a href="${esc(li.post_url)}" target="_blank" rel="noopener">open on LinkedIn</a>`;
            else if (li.error) status.textContent = li.error;
            else if (li.share_url) status.innerHTML = `Issue link: <a href="${esc(li.share_url)}" target="_blank" rel="noopener">${esc(li.share_url)}</a>`;
            else status.textContent = '';
        }
        const wrap = $('nl-li-preview-wrap');
        const img = $('nl-li-preview');
        if (wrap && img) {
            if (li.image_url) {
                img.src = li.image_url + '?t=' + Date.now();
                wrap.classList.remove('nl-hidden');
            } else {
                wrap.classList.add('nl-hidden');
            }
        }
    }

    function applyCampaignLinkedIn(data) {
        if (!data) return;
        if (data.success !== false) state.data = data;
        const camp = (data && data.campaign) || ((state.data && state.data.campaigns) || []).find((c) => c.id === state.editingId);
        if (camp) {
            const camps = (state.data && state.data.campaigns) || [];
            const idx = camps.findIndex((c) => c.id === (camp.id || state.editingId));
            if (idx >= 0) camps[idx] = camp;
            fillLinkedInFields(camp);
        }
        renderStats();
    }

    function campaignPayload() {
        const aud = parseAudience($('nl-ed-list').value);
        return {
            name: $('nl-ed-name').value,
            subject: $('nl-ed-subject').value,
            preheader: $('nl-ed-preheader').value,
            from_name: $('nl-ed-from-name').value,
            reply_to: $('nl-ed-reply').value,
            list_id: aud.list_id || 'the-read',
            segment_id: aud.segment_id || '',
            scheduled_at: fromLocalInput($('nl-ed-schedule').value),
            download: {
                enabled: $('nl-dl-enabled').checked,
                title: $('nl-dl-title').value,
                paid: $('nl-dl-paid').checked,
                price_usd: Number($('nl-dl-price').value || 0),
                list_id: $('nl-dl-list').value || 'the-read',
            },
            linkedin: {
                enabled: $('nl-li-enabled') ? $('nl-li-enabled').checked : true,
                headline: $('nl-li-headline') ? $('nl-li-headline').value : '',
                text: $('nl-li-text') ? $('nl-li-text').value : '',
            },
        };
    }

    window.nlOpenEditor = async function (id, focusSend) {
        state.editingId = id;
        const camp = ((state.data && state.data.campaigns) || []).find((c) => c.id === id);
        $('nl-ed-name').value = (camp && camp.name) || '';
        $('nl-ed-subject').value = (camp && camp.subject) || '';
        $('nl-ed-preheader').value = (camp && camp.preheader) || '';
        $('nl-ed-from-name').value = (camp && camp.from_name) || 'The Read';
        $('nl-ed-reply').value = (camp && camp.reply_to) || 'hello@crosswalknyc.com';
        fillAudienceSelect($('nl-ed-list'), camp);
        $('nl-ed-schedule').value = toLocalInput(camp && camp.scheduled_at);
        fillDownloadFields(camp);
        fillLinkedInFields(camp);
        $('nl-preview').src = '/n/preview/' + encodeURIComponent(id) + '?t=' + Date.now();
        $('nl-html-file').value = '';
        if ($('nl-dl-file')) $('nl-dl-file').value = '';
        if ($('nl-send-now')) {
            $('nl-send-now').disabled = !!(camp && camp.status === 'sending');
            $('nl-send-now').textContent = (camp && camp.status === 'sending')
                ? 'Sending…'
                : ((camp && camp.status === 'sent') ? 'Send leftovers' : 'Send now');
        }
        state.dirty = false;
        showView('editor');
        if (focusSend) $('nl-send-now').focus();
    };

    window.nlCompose = async function () {
        try {
            const data = await api('/api/admin/newsletter/campaigns', {
                method: 'POST',
                body: JSON.stringify({
                    name: 'Untitled newsletter',
                    subject: '',
                    list_id: 'the-read',
                    reply_to: 'hello@crosswalknyc.com',
                }),
            });
            state.data = data;
            renderStats();
            renderCampaigns();
            await nlOpenEditor(data.id);
        } catch (e) {
            toast(e.message, true);
        }
    };

    window.nlDuplicate = async function (id) {
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(id) + '/duplicate', { method: 'POST', body: '{}' });
            state.data = data;
            renderAll();
            toast('Duplicated as a new draft');
            if (data.id) nlOpenEditor(data.id);
        } catch (e) { toast(e.message, true); }
    };

    window.nlSaveCampaign = async function () {
        if (!state.editingId) return;
        try {
            await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId), {
                method: 'PUT',
                body: JSON.stringify(campaignPayload()),
            });
            state.dirty = false;
            toast('Saved');
            await nlRefresh(true);
            const camp = ((state.data && state.data.campaigns) || []).find((c) => c.id === state.editingId);
            fillDownloadFields(camp);
            fillLinkedInFields(camp);
        } catch (e) { toast(e.message, true); }
    };

    window.nlUploadHtml = async function () {
        if (!state.editingId) return;
        const file = $('nl-html-file').files[0];
        if (!file) { toast('Choose an HTML file first', true); return; }
        const fd = new FormData();
        fd.append('file', file);
        try {
            const resp = await fetch('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/html', {
                method: 'POST',
                credentials: 'same-origin',
                body: fd,
            });
            const data = await resp.json();
            if (!resp.ok || data.success === false) throw new Error(data.error || 'Upload failed');
            $('nl-preview').src = '/n/preview/' + encodeURIComponent(state.editingId) + '?t=' + Date.now();
            toast('HTML loaded' + (data.assets ? ` · ${data.assets} images hosted` : ''));
        } catch (e) { toast(e.message, true); }
    };

    window.nlUploadAttachment = async function () {
        if (!state.editingId) return;
        const file = $('nl-dl-file').files[0];
        if (!file) { toast('Choose a file to attach first', true); return; }
        const fd = new FormData();
        fd.append('file', file);
        try {
            const resp = await fetch('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/attachment', {
                method: 'POST',
                credentials: 'same-origin',
                body: fd,
            });
            const data = await resp.json();
            if (!resp.ok || data.success === false) throw new Error(data.error || 'Upload failed');
            $('nl-dl-enabled').checked = true;
            if (data.campaign) {
                const camps = (state.data && state.data.campaigns) || [];
                const idx = camps.findIndex((c) => c.id === state.editingId);
                if (idx >= 0) camps[idx] = data.campaign;
                fillDownloadFields(data.campaign);
            }
            toast('File attached');
        } catch (e) { toast(e.message, true); }
    };

    window.nlTestSend = async function () {
        if (!state.editingId) return;
        await nlSaveCampaign();
        const email = prompt('Send a test to which email?');
        if (!email) return;
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/test', {
                method: 'POST',
                body: JSON.stringify({ email }),
            });
            toast(data.message || 'Test sent');
        } catch (e) { toast(e.message, true); }
    };

    function sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function pollSend(id) {
        for (let i = 0; i < 180; i += 1) {
            await sleep(2000);
            try {
                const data = await api('/api/admin/newsletter');
                state.data = data;
                renderAll();
                const camp = ((data && data.campaigns) || []).find((c) => c.id === id);
                if (!camp || camp.status !== 'sending') {
                    if (camp && camp.status === 'sent') {
                        toast('Sent to ' + fmtNum((camp.stats && camp.stats.sent) || 0) + ' people');
                    }
                    return;
                }
            } catch (e) { /* keep waiting through a deploy blip */ }
        }
        toast('Still sending. Refresh in a minute.', true);
    }

    window.nlConfirmSend = async function (schedule) {
        if (!state.editingId || state.sendBusy) return;
        await nlSaveCampaign();
        const value = $('nl-ed-list').value;
        const n = audienceCount(value);
        if (!n) {
            toast('That audience has no subscribed people yet. Add them under Audience.', true);
            return;
        }
        const when = schedule ? fromLocalInput($('nl-ed-schedule').value) : '';
        if (schedule && !when) {
            toast('Pick a send time first', true);
            return;
        }
        const subject = $('nl-ed-subject').value || '(no subject)';
        const label = audienceLabel(value);
        const camp = ((state.data && state.data.campaigns) || []).find((c) => c.id === state.editingId);
        if (!schedule && camp && camp.status === 'sending') {
            toast('This letter is already going out. One copy per address.');
            await pollSend(state.editingId);
            return;
        }
        const liOn = $('nl-li-enabled') && $('nl-li-enabled').checked;
        const liConn = state.data && state.data.settings && state.data.settings.linkedin && state.data.settings.linkedin.connected;
        const liNote = (liOn && liConn) ? ' LinkedIn gets the headline, image, and issue link at the same time.' : '';
        const leftovers = camp && camp.status === 'sent';
        const msg = schedule
            ? `Schedule "${subject}" to ${n} people on ${label} at ${fmtWhen(when)}?` + liNote
            : (leftovers
                ? `Send leftover copies of "${subject}" on ${label}? Anyone who already received it is skipped.`
                : `Send "${subject}" to ${n} people on ${label} now? Each address gets one copy. This uses no_reply@crosswalknyc.com. Replies go to hello@crosswalknyc.com.`) + liNote;
        openModal(msg, async () => {
            if (state.sendBusy) return;
            state.sendBusy = true;
            if ($('nl-send-now')) {
                $('nl-send-now').disabled = true;
                $('nl-send-now').textContent = 'Sending…';
            }
            try {
                const aud = parseAudience(value);
                const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/send', {
                    method: 'POST',
                    body: JSON.stringify({
                        list_id: aud.list_id || undefined,
                        segment_id: aud.segment_id || undefined,
                        scheduled_at: when || undefined,
                    }),
                });
                state.data = data;
                renderAll();
                if (data.status === 'scheduled') toast('Scheduled');
                else if (data.status === 'sending') {
                    const extra = data.already_sent ? ` ${data.already_sent} already received it and will be skipped.` : '';
                    toast('Sending to ' + (data.queued || data.recipients || n) + ' people.' + extra);
                    showView('campaigns');
                    await pollSend(state.editingId);
                }
                else if (data.failed) toast('Sent to ' + (data.sent || 0) + ', ' + data.failed + ' did not go out', true);
                else toast('Sent to ' + (data.sent || data.recipients || n) + ' people');
                showView('campaigns');
            } catch (e) { toast(e.message, true); }
            finally {
                state.sendBusy = false;
                const latest = ((state.data && state.data.campaigns) || []).find((c) => c.id === state.editingId);
                if ($('nl-send-now')) {
                    $('nl-send-now').disabled = !!(latest && latest.status === 'sending');
                    $('nl-send-now').textContent = (latest && latest.status === 'sending')
                        ? 'Sending…'
                        : ((latest && latest.status === 'sent') ? 'Send leftovers' : 'Send now');
                }
            }
        });
    };

    window.nlDeleteCampaign = async function (id) {
        const cid = id || state.editingId;
        if (!cid) return;
        if (!confirm('Delete this newsletter?')) return;
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(cid), { method: 'DELETE' });
            state.data = data;
            if (state.editingId === cid) state.editingId = null;
            renderAll();
            showView('campaigns');
            toast('Deleted');
        } catch (e) { toast(e.message, true); }
    };

    function ensureSortStyles() {
        if (document.getElementById('nl-sort-style')) return;
        const s = document.createElement('style');
        s.id = 'nl-sort-style';
        s.textContent = [
            '.nl-table th.nl-sort{cursor:pointer;user-select:none;white-space:nowrap}',
            '.nl-table th.nl-sort:hover{text-decoration:underline}',
            '.nl-stat.nl-stat-hit{cursor:pointer}',
            '.nl-stat.nl-stat-hit:hover{filter:brightness(1.08)}',
            '.nl-stat.nl-stat-on{outline:2px solid var(--accent-cyan,#C7F23E);outline-offset:2px}',
        ].join('');
        document.head.appendChild(s);
    }

    function sortValue(v) {
        if (v == null || v === '') return { t: 0, n: 0, s: '' };
        if (typeof v === 'number' && !Number.isNaN(v)) return { t: 1, n: v, s: '' };
        const raw = String(v);
        const ts = Date.parse(raw);
        if (!Number.isNaN(ts) && /^\d{4}-\d{2}-\d{2}/.test(raw)) return { t: 1, n: ts, s: '' };
        const num = Number(raw);
        if (raw !== '' && !Number.isNaN(num) && raw.trim() === String(num)) return { t: 1, n: num, s: '' };
        return { t: 1, n: 0, s: raw.toLowerCase() };
    }

    function sortRows(rows, spec, getter) {
        if (!spec || !spec.key) return rows.slice();
        const mul = spec.dir === 'desc' ? -1 : 1;
        return rows.slice().sort((a, b) => {
            const va = sortValue(getter(a, spec.key));
            const vb = sortValue(getter(b, spec.key));
            if (va.t !== vb.t) return (va.t - vb.t) * (spec.dir === 'desc' ? 1 : -1);
            if (va.s && vb.s) return va.s < vb.s ? -mul : va.s > vb.s ? mul : 0;
            if (va.n !== vb.n) return (va.n - vb.n) * mul;
            return 0;
        });
    }

    function sortTh(table, key, label) {
        const spec = state.reportSort[table] || {};
        const on = spec.key === key;
        const arrow = on ? (spec.dir === 'desc' ? ' \u2193' : ' \u2191') : '';
        return `<th class="nl-sort" onclick="nlSortReport('${table}','${key}')">${esc(label)}${arrow}</th>`;
    }

    window.nlSortReport = function (table, key) {
        const spec = state.reportSort[table] || { key: '', dir: 'asc' };
        if (spec.key === key) spec.dir = spec.dir === 'asc' ? 'desc' : 'asc';
        else spec.dir = (key === 'clicks' || key === 'click_count' || key === 'download_count' || key === 'amount_usd' || key === 'sent' || key === 'open_rate' || key === 'click_rate' || key === 'leads' || key === 'unique_downloads' || key === 'paid' || key === 'revenue_usd') ? 'desc' : 'asc';
        spec.key = key;
        state.reportSort[table] = spec;
        if (table === 'outcomes') renderOutcomes();
        else if (table === 'who') renderReportWho();
        else renderReportTables();
    };

    function recipField(r, key) {
        if (key === 'email') return r.email || '';
        if (key === 'status') return r.status || '';
        if (key === 'opened_at') return r.opened_at || '';
        if (key === 'click_count') return Number(r.click_count || 0);
        if (key === 'error') return r.error || '';
        return '';
    }

    function downloadField(r, key) {
        if (key === 'email') return r.email || '';
        if (key === 'entered_at') return r.entered_at || '';
        if (key === 'amount_usd') return r.paid ? Number(r.amount_usd || 0) : -1;
        if (key === 'download_count') return Number(r.download_count || 0);
        return '';
    }

    function linkField(r, key) {
        if (key === 'url') return r.url || '';
        if (key === 'clicks') return Number(r.clicks || 0);
        return '';
    }

    function whoField(r, key) {
        return r[key];
    }

    function whoClickRows(uniqueOnly) {
        const out = [];
        ((state.report && state.report.recipients) || []).forEach((r) => {
            const clicks = r.clicks || [];
            const total = Number(r.click_count || 0);
            if (!total && !clicks.length) return;
            if (uniqueOnly) {
                out.push({
                    email: r.email,
                    name: r.name || '',
                    url: clicks.length > 1 ? (clicks.length + ' links') : ((clicks[0] && clicks[0].url) || ''),
                    count: total || clicks.length,
                    at: (clicks[0] && (clicks[0].at || clicks[0].last_at)) || '',
                });
                return;
            }
            if (!clicks.length) {
                out.push({ email: r.email, name: r.name || '', url: '', count: total, at: '' });
                return;
            }
            clicks.forEach((c) => {
                out.push({
                    email: r.email,
                    name: r.name || '',
                    url: c.url || '',
                    count: Number(c.count || 1),
                    at: c.at || c.last_at || '',
                });
            });
        });
        return out;
    }

    function whoPeople() {
        const recips = (state.report && state.report.recipients) || [];
        const dls = (state.report && state.report.downloads) || [];
        const key = state.reportWho;
        if (key === 'sent') {
            return {
                title: 'Sent',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'name', label: 'Name' },
                    { key: 'sent_at', label: 'Sent', fmt: 'when' },
                ],
                rows: recips.filter((r) => r.status === 'sent').map((r) => ({
                    email: r.email, name: r.name || '', sent_at: r.sent_at || '',
                })),
            };
        }
        if (key === 'failed') {
            return {
                title: 'Failed',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'error', label: 'Note' },
                ],
                rows: recips.filter((r) => r.status === 'failed').map((r) => ({
                    email: r.email, error: r.error || '',
                })),
            };
        }
        if (key === 'unique_opens' || key === 'opens') {
            return {
                title: key === 'opens' ? 'Opens' : 'Unique opens',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'opened_at', label: 'First opened', fmt: 'when' },
                    { key: 'open_count', label: 'Opens', fmt: 'num' },
                ],
                rows: recips.filter((r) => r.opened_at || Number(r.open_count || 0) > 0).map((r) => ({
                    email: r.email, opened_at: r.opened_at || '', open_count: Number(r.open_count || 0),
                })),
            };
        }
        if (key === 'unique_clicks' || key === 'clicks') {
            return {
                title: key === 'clicks' ? 'Clicks' : 'Unique clicks',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'url', label: 'Link' },
                    { key: 'count', label: 'Clicks', fmt: 'num' },
                    { key: 'at', label: 'When', fmt: 'when' },
                ],
                rows: whoClickRows(key === 'unique_clicks'),
            };
        }
        if (key === 'unsubs') {
            return {
                title: 'Unsubscribes',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'unsubscribed_at', label: 'Unsubscribed', fmt: 'when' },
                ],
                rows: recips.filter((r) => r.unsubscribed_at).map((r) => ({
                    email: r.email, unsubscribed_at: r.unsubscribed_at,
                })),
            };
        }
        if (key === 'leads') {
            return {
                title: 'Download leads',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'name', label: 'Name' },
                    { key: 'entered_at', label: 'Entered', fmt: 'when' },
                ],
                rows: dls.map((r) => ({
                    email: r.email, name: r.name || '', entered_at: r.entered_at || '',
                })),
            };
        }
        if (key === 'downloads') {
            return {
                title: 'Downloads',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'download_count', label: 'Downloads', fmt: 'num' },
                    { key: 'last_download_at', label: 'Last download', fmt: 'when' },
                ],
                rows: dls.filter((r) => Number(r.download_count || 0) > 0).map((r) => ({
                    email: r.email,
                    download_count: Number(r.download_count || 0),
                    last_download_at: r.last_download_at || '',
                })),
            };
        }
        if (key === 'paid' || key === 'revenue') {
            return {
                title: key === 'revenue' ? 'Revenue' : 'Paid',
                cols: [
                    { key: 'email', label: 'Email' },
                    { key: 'amount_usd', label: 'Paid', fmt: 'money' },
                    { key: 'paid_at', label: 'When', fmt: 'when' },
                ],
                rows: dls.filter((r) => r.paid).map((r) => ({
                    email: r.email,
                    amount_usd: Number(r.amount_usd || 0),
                    paid_at: r.paid_at || '',
                })),
            };
        }
        return null;
    }

    function fmtWhoCell(col, row) {
        const v = row[col.key];
        if (col.fmt === 'when') return v ? fmtWhen(v) : '-';
        if (col.fmt === 'num') return fmtNum(v);
        if (col.fmt === 'money') return fmtMoney(v);
        return esc(v || '');
    }

    function ensureWhoHost() {
        let el = $('nl-report-who');
        if (el) return el;
        const stats = $('nl-report-stats');
        if (!stats) return null;
        el = document.createElement('div');
        el.id = 'nl-report-who';
        el.className = 'card';
        el.style.margin = '1rem 0';
        el.style.padding = '1rem';
        el.style.display = 'none';
        stats.insertAdjacentElement('afterend', el);
        return el;
    }

    function renderReportWho() {
        const host = ensureWhoHost();
        if (!host) return;
        const spec = whoPeople();
        if (!spec) {
            host.style.display = 'none';
            host.innerHTML = '';
            return;
        }
        ensureSortStyles();
        const rows = sortRows(spec.rows, state.reportSort.who, whoField);
        host.style.display = '';
        const empty = rows.length ? '' : '<div class="nl-empty">No one has done this yet.</div>';
        host.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 0.6rem;">' +
            `<h3 style="margin:0;">${esc(spec.title)} · ${fmtNum(rows.length)}</h3>` +
            '<button class="btn btn-small btn-secondary" onclick="nlClearReportWho()">Close</button></div>' +
            (rows.length
                ? '<table class="nl-table"><thead><tr>' +
                  spec.cols.map((c) => sortTh('who', c.key, c.label)).join('') +
                  '</tr></thead><tbody>' +
                  rows.map((r) => '<tr>' + spec.cols.map((c) => `<td>${fmtWhoCell(c, r)}</td>`).join('') + '</tr>').join('') +
                  '</tbody></table>'
                : empty);
        host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    window.nlShowReportWho = function (key) {
        if (state.reportWho === key) {
            state.reportWho = '';
        } else {
            state.reportWho = key;
            state.reportSort.who = { key: '', dir: 'asc' };
        }
        renderReportStats();
        renderReportWho();
    };

    window.nlClearReportWho = function () {
        state.reportWho = '';
        renderReportStats();
        renderReportWho();
    };

    function renderReportStats() {
        const data = state.report;
        if (!data || !$('nl-report-stats')) return;
        ensureSortStyles();
        const s = data.stats || {};
        const d = data.download_stats || {};
        const tiles = [
            ['sent', 'Sent', s.sent],
            ['failed', 'Failed', s.failed],
            ['unique_opens', 'Unique opens', s.unique_opens],
            ['opens', 'Opens', s.opens],
            ['unique_clicks', 'Unique clicks', s.unique_clicks],
            ['clicks', 'Clicks', s.clicks],
            ['unsubs', 'Unsubscribes', s.unsubs],
            ['leads', 'Download leads', d.leads],
            ['downloads', 'Downloads', d.unique_downloads],
            ['paid', 'Paid', d.paid],
            ['revenue', 'Revenue', fmtMoney(d.revenue_usd)],
        ];
        $('nl-report-stats').innerHTML = tiles.map(([key, label, v]) => {
            const on = state.reportWho === key ? ' nl-stat-on' : '';
            return `<div class="nl-stat nl-stat-hit${on}" onclick="nlShowReportWho('${key}')" title="Show who">` +
                `<div class="k">${esc(label)}</div>` +
                `<div class="v">${typeof v === 'string' ? esc(v) : fmtNum(v)}</div></div>`;
        }).join('');
    }

    function renderReportTables() {
        const data = state.report;
        if (!data) return;
        ensureSortStyles();
        const links = sortRows(data.top_links || [], state.reportSort.links, linkField);
        $('nl-report-links').innerHTML = links.length
            ? '<table class="nl-table"><thead><tr>' +
              sortTh('links', 'url', 'Link') +
              sortTh('links', 'clicks', 'Clicks') +
              '</tr></thead><tbody>' +
              links.map((l) => `<tr><td>${esc(l.url)}</td><td>${fmtNum(l.clicks)}</td></tr>`).join('') +
              '</tbody></table>'
            : '<div class="nl-empty">No clicks yet.</div>';
        const dls = sortRows(data.downloads || [], state.reportSort.downloads, downloadField);
        if ($('nl-report-downloads')) {
            $('nl-report-downloads').innerHTML = dls.length
                ? '<table class="nl-table"><thead><tr>' +
                  sortTh('downloads', 'email', 'Email') +
                  sortTh('downloads', 'entered_at', 'Entered') +
                  sortTh('downloads', 'amount_usd', 'Paid') +
                  sortTh('downloads', 'download_count', 'Downloads') +
                  '</tr></thead><tbody>' +
                  dls.map((r) => `<tr><td>${esc(r.email)}</td><td>${r.entered_at ? fmtWhen(r.entered_at) : '-'}</td><td>${r.paid ? fmtMoney(r.amount_usd) : 'Free'}</td><td>${fmtNum(r.download_count)}</td></tr>`).join('') +
                  '</tbody></table>'
                : '<div class="nl-empty">No one has entered an email for the file yet.</div>';
        }
        const recips = sortRows(data.recipients || [], state.reportSort.recips, recipField);
        $('nl-report-recips').innerHTML = recips.length
            ? '<table class="nl-table"><thead><tr>' +
              sortTh('recips', 'email', 'Email') +
              sortTh('recips', 'status', 'Status') +
              sortTh('recips', 'opened_at', 'Opened') +
              sortTh('recips', 'click_count', 'Clicks') +
              sortTh('recips', 'error', 'Note') +
              '</tr></thead><tbody>' +
              recips.map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.status || '')}</td><td>${r.opened_at ? fmtWhen(r.opened_at) : '-'}</td><td>${fmtNum(r.click_count)}</td><td>${esc(r.error || '')}</td></tr>`).join('') +
              '</tbody></table>'
            : '<div class="nl-empty">No recipients on this send yet.</div>';
    }

    window.nlOpenReport = async function (id) {
        state.reportId = id;
        state.reportWho = '';
        state.reportSort.recips = { key: '', dir: 'asc' };
        state.reportSort.downloads = { key: '', dir: 'asc' };
        state.reportSort.links = { key: '', dir: 'asc' };
        state.reportSort.who = { key: '', dir: 'asc' };
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(id) + '/report');
            state.report = data;
            const c = data.campaign || {};
            $('nl-report-title').textContent = c.name || 'Report';
            $('nl-report-sub').textContent = (c.subject || '') + (c.sent_at ? ' · sent ' + fmtWhen(c.sent_at) : '');
            renderReportStats();
            renderReportWho();
            renderReportTables();
            showView('report');
        } catch (e) { toast(e.message, true); }
    };

    function outcomeField(c, key) {
        const s = c.stats || {};
        const d = c.download_stats || {};
        if (key === 'name') return c.name || '';
        if (key === 'status') return c.status || '';
        if (key === 'sent') return Number(s.sent || 0);
        if (key === 'open_rate') return Number(c.open_rate || 0);
        if (key === 'click_rate') return Number(c.click_rate || 0);
        if (key === 'leads') return Number(d.leads || 0);
        if (key === 'unique_downloads') return Number(d.unique_downloads || 0);
        if (key === 'paid') return Number(d.paid || 0);
        if (key === 'revenue_usd') return Number(d.revenue_usd || 0);
        return '';
    }

    function renderOutcomes() {
        const host = $('nl-outcomes-table');
        if (!host) return;
        ensureSortStyles();
        const camps = sortRows((state.data && state.data.campaigns) || [], state.reportSort.outcomes, outcomeField);
        if (!camps.length) {
            host.innerHTML = '<div class="nl-empty">No campaigns yet.</div>';
            return;
        }
        host.innerHTML = '<table class="nl-table"><thead><tr>' +
            sortTh('outcomes', 'name', 'Campaign') +
            sortTh('outcomes', 'status', 'Status') +
            sortTh('outcomes', 'sent', 'Sent') +
            sortTh('outcomes', 'open_rate', 'Open') +
            sortTh('outcomes', 'click_rate', 'Click') +
            sortTh('outcomes', 'leads', 'Leads') +
            sortTh('outcomes', 'unique_downloads', 'Downloads') +
            sortTh('outcomes', 'paid', 'Paid') +
            sortTh('outcomes', 'revenue_usd', 'Revenue') +
            '<th></th>' +
            '</tr></thead><tbody>' +
            camps.map((c) => {
                const s = c.stats || {};
                const d = c.download_stats || {};
                return `<tr>
                    <td>${esc(c.name || 'Untitled')}<div class="nl-sub">${esc(c.subject || '')}</div></td>
                    <td><span class="nl-badge ${c.status || 'draft'}">${esc(c.status || 'draft')}</span></td>
                    <td>${fmtNum(s.sent)}</td>
                    <td>${c.open_rate || 0}%</td>
                    <td>${c.click_rate || 0}%</td>
                    <td>${fmtNum(d.leads)}</td>
                    <td>${fmtNum(d.unique_downloads)}</td>
                    <td>${fmtNum(d.paid)}</td>
                    <td>${fmtMoney(d.revenue_usd)}</td>
                    <td><button class="btn btn-small btn-secondary" onclick="nlOpenReport('${c.id}')">Open</button></td>
                </tr>`;
            }).join('') +
            '</tbody></table>';
    }

    function renderAudience() {
        const lists = (state.data && state.data.lists) || [];
        $('nl-lists').innerHTML = lists.map((l) => `
            <div class="nl-stat">
                <div class="k">${esc(l.kind || 'list')}</div>
                <div class="v" style="font-size:1.1rem">${esc(l.name)}</div>
                <div class="k" style="margin-top:0.35rem">${fmtNum(l.subscriber_count)} subscribed</div>
                ${l.id === 'dashboard-users' || l.id === 'the-read' ? '' : `<button class="btn btn-small btn-secondary" style="margin-top:0.5rem" onclick="nlDeleteList('${l.id}')">Remove</button>`}
            </div>
        `).join('');
        fillListSelect($('nl-add-list'));
        if ($('nl-import-list')) fillListSelect($('nl-import-list'));
        if ($('nl-seg-list')) fillListSelect($('nl-seg-list'), '', true);
        const segs = (state.data && state.data.segments) || [];
        if ($('nl-segments')) {
            $('nl-segments').innerHTML = segs.length
                ? '<table class="nl-table"><thead><tr><th>Segment</th><th>People</th><th></th></tr></thead><tbody>' +
                  segs.map((s) => {
                      const rules = s.rules || {};
                      const bits = [];
                      (rules.list_ids || []).forEach((id) => bits.push(listName(id)));
                      (rules.tags || []).forEach((t) => bits.push('tag:' + t));
                      if (rules.company_contains) bits.push('company:' + rules.company_contains);
                      return `<tr>
                        <td>${esc(s.name)}<div class="nl-sub">${esc(bits.join(' · ') || 'All subscribed')}</div></td>
                        <td>${fmtNum(s.subscriber_count)}</td>
                        <td><button class="btn btn-small btn-secondary" onclick="nlEditSegment('${s.id}')">Edit</button>
                            <button class="btn btn-small btn-secondary" onclick="nlDeleteSegment('${s.id}')">Remove</button></td>
                      </tr>`;
                  }).join('') + '</tbody></table>'
                : '<div class="nl-empty">No segments yet. Build one from a list, tags, or company name.</div>';
        }
        const settings = (state.data && state.data.settings) || {};
        $('nl-set-from').value = settings.from_name || 'The Read';
        $('nl-set-reply').value = settings.reply_to || 'hello@crosswalknyc.com';
        $('nl-set-addr').value = settings.company_address || 'Crosswalk, 23465 Civic Center Way Bldg 9, Malibu, CA 90265';
        renderLinkedInSettings(settings.linkedin || {});
        const q = ($('nl-sub-search').value || '').trim().toLowerCase();
        const rows = ((state.data && state.data.subscribers) || []).filter((s) => {
            if (!q) return true;
            return [s.email, s.name, s.company, (s.list_ids || []).join(' '), (s.tags || []).join(' ')].join(' ').toLowerCase().includes(q);
        });
        $('nl-sub-table').innerHTML = rows.length
            ? '<table class="nl-table"><thead><tr><th>Email</th><th>Name</th><th>Company</th><th>Lists</th><th>Tags</th><th>Status</th><th></th></tr></thead><tbody>' +
              rows.map((s) => `<tr>
                <td>${esc(s.email)}</td>
                <td>${esc(s.name || '')}</td>
                <td>${esc(s.company || '')}</td>
                <td>${esc((s.list_ids || []).map(listName).join(', '))}</td>
                <td>${esc((s.tags || []).join(', '))}</td>
                <td>${esc(s.status || '')}</td>
                <td><button class="btn btn-small btn-secondary" onclick="nlRemoveSub('${esc(s.email)}')">Remove</button></td>
              </tr>`).join('') + '</tbody></table>'
            : '<div class="nl-empty">No people on the list yet. Import a CSV, add an email, or sync dashboard users.</div>';
    }

    window.nlAddSubscriber = async function () {
        const email = $('nl-add-email').value;
        const name = $('nl-add-name').value;
        const listId = $('nl-add-list').value;
        const tags = ($('nl-add-tags') && $('nl-add-tags').value) || '';
        try {
            const data = await api('/api/admin/newsletter/subscribers', {
                method: 'POST',
                body: JSON.stringify({ email, name, list_ids: [listId], tags }),
            });
            state.data = data;
            $('nl-add-email').value = '';
            $('nl-add-name').value = '';
            if ($('nl-add-tags')) $('nl-add-tags').value = '';
            renderAll();
            toast('Added');
        } catch (e) { toast(e.message, true); }
    };

    window.nlBulkAdd = async function () {
        const raw = $('nl-bulk-emails').value || '';
        const emails = raw.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
        if (!emails.length) { toast('Paste emails first', true); return; }
        const listId = $('nl-add-list').value;
        const tags = ($('nl-add-tags') && $('nl-add-tags').value) || '';
        try {
            const data = await api('/api/admin/newsletter/subscribers', {
                method: 'POST',
                body: JSON.stringify({
                    subscribers: emails.map((email) => ({ email, list_ids: [listId], tags })),
                }),
            });
            state.data = data;
            $('nl-bulk-emails').value = '';
            renderAll();
            toast('Added ' + emails.length);
        } catch (e) { toast(e.message, true); }
    };

    window.nlImportCsv = async function () {
        const file = $('nl-import-file') && $('nl-import-file').files[0];
        if (!file) { toast('Choose a CSV first', true); return; }
        const fd = new FormData();
        fd.append('file', file);
        fd.append('list_id', $('nl-import-list').value || 'the-read');
        fd.append('tags', ($('nl-import-tags').value || '').trim());
        try {
            const resp = await fetch('/api/admin/newsletter/subscribers/import', {
                method: 'POST',
                credentials: 'same-origin',
                body: fd,
            });
            const data = await resp.json();
            if (!resp.ok || data.success === false) throw new Error(data.error || 'Import failed');
            state.data = data;
            $('nl-import-file').value = '';
            renderAll();
            toast('Imported ' + (data.imported || 0) + ' emails');
        } catch (e) { toast(e.message, true); }
    };

    window.nlNewSegment = function () {
        $('nl-seg-id').value = '';
        $('nl-seg-name').value = '';
        $('nl-seg-tags').value = '';
        $('nl-seg-company').value = '';
        fillListSelect($('nl-seg-list'), '', true);
        $('nl-seg-form').classList.remove('nl-hidden');
        $('nl-seg-name').focus();
    };

    window.nlEditSegment = function (id) {
        const seg = ((state.data && state.data.segments) || []).find((s) => s.id === id);
        if (!seg) return;
        const rules = seg.rules || {};
        $('nl-seg-id').value = id;
        $('nl-seg-name').value = seg.name || '';
        $('nl-seg-tags').value = (rules.tags || []).join(', ');
        $('nl-seg-company').value = rules.company_contains || '';
        fillListSelect($('nl-seg-list'), (rules.list_ids || [])[0] || '', true);
        $('nl-seg-form').classList.remove('nl-hidden');
    };

    window.nlSaveSegment = async function () {
        const id = $('nl-seg-id').value;
        const body = {
            name: $('nl-seg-name').value,
            rules: {
                list_ids: $('nl-seg-list').value ? [$('nl-seg-list').value] : [],
                tags: $('nl-seg-tags').value,
                company_contains: $('nl-seg-company').value,
                status: 'subscribed',
            },
        };
        try {
            const data = id
                ? await api('/api/admin/newsletter/segments/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(body) })
                : await api('/api/admin/newsletter/segments', { method: 'POST', body: JSON.stringify(body) });
            state.data = data;
            $('nl-seg-form').classList.add('nl-hidden');
            renderAll();
            toast(id ? 'Segment updated' : 'Segment saved');
        } catch (e) { toast(e.message, true); }
    };

    window.nlDeleteSegment = async function (id) {
        if (!confirm('Remove this segment? People stay on their lists.')) return;
        try {
            const data = await api('/api/admin/newsletter/segments/' + encodeURIComponent(id), { method: 'DELETE' });
            state.data = data;
            renderAll();
        } catch (e) { toast(e.message, true); }
    };

    window.nlRemoveSub = async function (email) {
        if (!confirm('Remove ' + email + ' from newsletters?')) return;
        try {
            const data = await api('/api/admin/newsletter/subscribers/' + encodeURIComponent(email), { method: 'DELETE' });
            state.data = data;
            renderAll();
        } catch (e) { toast(e.message, true); }
    };

    window.nlNewList = async function () {
        const name = prompt('List name?');
        if (!name) return;
        try {
            const data = await api('/api/admin/newsletter/lists', {
                method: 'POST',
                body: JSON.stringify({ name }),
            });
            state.data = data;
            renderAll();
        } catch (e) { toast(e.message, true); }
    };

    window.nlDeleteList = async function (id) {
        if (!confirm('Remove this list? People stay in the book, just off this list.')) return;
        try {
            const data = await api('/api/admin/newsletter/lists/' + encodeURIComponent(id), { method: 'DELETE' });
            state.data = data;
            renderAll();
        } catch (e) { toast(e.message, true); }
    };

    window.nlSyncDashboard = async function () {
        try {
            const data = await api('/api/admin/newsletter/lists/dashboard-users/sync', { method: 'POST', body: '{}' });
            state.data = data;
            renderAll();
            toast('Dashboard users synced');
        } catch (e) { toast(e.message, true); }
    };

    window.nlSaveSettings = async function () {
        try {
            const data = await api('/api/admin/newsletter/settings', {
                method: 'POST',
                body: JSON.stringify({
                    from_name: $('nl-set-from').value,
                    reply_to: $('nl-set-reply').value,
                    company_address: $('nl-set-addr').value,
                }),
            });
            state.data = data;
            renderStats();
            renderAudience();
            toast('Sender settings saved');
        } catch (e) { toast(e.message, true); }
    };

    function renderLinkedInSettings(li) {
        li = li || {};
        if ($('nl-li-client')) $('nl-li-client').value = li.client_id || '';
        if ($('nl-li-secret')) $('nl-li-secret').value = '';
        if ($('nl-li-redirect')) $('nl-li-redirect').value = li.redirect_uri || '';
        if ($('nl-li-auto')) $('nl-li-auto').checked = li.auto_post !== false;
        const conn = $('nl-li-conn');
        if (conn) {
            if (li.connected) {
                conn.textContent = 'Connected' + (li.organization_name ? ' as ' + li.organization_name : '') + '.';
            } else if (li.last_error) {
                conn.textContent = li.last_error;
            } else if (li.has_client_id && li.has_client_secret) {
                conn.textContent = 'App saved. Connect the company page next.';
            } else {
                conn.textContent = 'Not connected.';
            }
        }
        const wrap = $('nl-li-pages-wrap');
        const sel = $('nl-li-page');
        const pages = li.pages || [];
        if (wrap && sel) {
            wrap.classList.toggle('nl-hidden', pages.length < 2 && !li.connected);
            sel.innerHTML = pages.map((p) => {
                const on = String(p.id) === String(li.organization_id || '');
                return `<option value="${esc(p.id)}"${on ? ' selected' : ''}>${esc(p.name || p.id)}</option>`;
            }).join('');
        }
    }

    window.nlSaveLinkedIn = async function () {
        try {
            const data = await api('/api/admin/newsletter/settings', {
                method: 'POST',
                body: JSON.stringify({
                    linkedin: {
                        client_id: $('nl-li-client').value,
                        client_secret: $('nl-li-secret').value,
                        auto_post: $('nl-li-auto').checked,
                    },
                }),
            });
            state.data = data;
            renderAudience();
            toast('LinkedIn app saved');
        } catch (e) { toast(e.message, true); }
    };

    window.nlConnectLinkedIn = async function () {
        try {
            await nlSaveLinkedIn();
            const data = await api('/api/admin/newsletter/linkedin/connect');
            if (data.url) window.location.href = data.url;
        } catch (e) { toast(e.message, true); }
    };

    window.nlDisconnectLinkedIn = async function () {
        if (!confirm('Disconnect the LinkedIn company page?')) return;
        try {
            const data = await api('/api/admin/newsletter/linkedin/disconnect', { method: 'POST', body: '{}' });
            state.data = data;
            renderAudience();
            toast('LinkedIn disconnected');
        } catch (e) { toast(e.message, true); }
    };

    window.nlPickLinkedInPage = async function () {
        try {
            const data = await api('/api/admin/newsletter/linkedin/page', {
                method: 'POST',
                body: JSON.stringify({ organization_id: $('nl-li-page').value }),
            });
            state.data = data;
            renderAudience();
            toast('Company page saved');
        } catch (e) { toast(e.message, true); }
    };

    window.nlGenerateLinkedIn = async function () {
        if (!state.editingId) return;
        await nlSaveCampaign();
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/linkedin/generate', {
                method: 'POST',
                body: '{}',
            });
            applyCampaignLinkedIn(data);
            toast('LinkedIn post ready');
        } catch (e) { toast(e.message, true); }
    };

    window.nlUploadLinkedInImage = async function () {
        if (!state.editingId) return;
        const file = $('nl-li-file') && $('nl-li-file').files[0];
        if (!file) { toast('Choose a LinkedIn image first', true); return; }
        const fd = new FormData();
        fd.append('file', file);
        try {
            const resp = await fetch('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/linkedin/image', {
                method: 'POST',
                credentials: 'same-origin',
                body: fd,
            });
            const data = await resp.json();
            if (!resp.ok || data.success === false) throw new Error(data.error || 'Upload failed');
            applyCampaignLinkedIn(data);
            toast('LinkedIn image uploaded');
        } catch (e) { toast(e.message, true); }
    };

    window.nlPostLinkedIn = async function () {
        if (!state.editingId) return;
        await nlSaveCampaign();
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/linkedin/post', {
                method: 'POST',
                body: '{}',
            });
            applyCampaignLinkedIn(data);
            toast(data.post_url ? 'Posted to LinkedIn' : 'Posted to LinkedIn');
        } catch (e) { toast(e.message, true); }
    };

    function renderAll() {
        renderStats();
        renderCampaigns();
        renderAudience();
        renderOutcomes();
    }

    async function nlRefresh(keepView) {
        const data = await api('/api/admin/newsletter');
        state.data = data;
        renderAll();
        if (!keepView) showView(state.view);
        return data;
    }

    window.nlInit = async function () {
        try {
            await nlRefresh(true);
            showView(state.view === 'editor' && state.editingId ? 'editor' : (state.view || 'campaigns'));
        } catch (e) {
            toast(e.message, true);
        }
    };

    window.nlShow = function (view) {
        if (view === 'campaigns' || view === 'audience' || view === 'outcomes') showView(view);
        if (view === 'audience') renderAudience();
        if (view === 'outcomes') renderOutcomes();
    };

    window.nlBack = function () {
        showView('campaigns');
    };

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function toLocalInput(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return '';
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function fromLocalInput(local) {
        if (!local) return '';
        const d = new Date(local);
        if (Number.isNaN(d.getTime())) return '';
        return d.toISOString();
    }

    function openModal(message, onYes) {
        $('nl-modal-msg').textContent = message;
        $('nl-modal').classList.add('on');
        $('nl-modal-yes').onclick = () => {
            $('nl-modal').classList.remove('on');
            onYes();
        };
    }
    window.nlCloseModal = function () {
        $('nl-modal').classList.remove('on');
    };

    document.addEventListener('DOMContentLoaded', () => {
        const search = $('nl-sub-search');
        if (search) search.addEventListener('input', renderAudience);
    });
})();
