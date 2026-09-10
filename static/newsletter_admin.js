/* Newsletter CMS for Admin > Newsletter */
(function () {
    const state = {
        data: null,
        view: 'campaigns',
        editingId: null,
        reportId: null,
        dirty: false,
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
                : (c.status === 'scheduled'
                    ? 'Sends ' + fmtWhen(c.scheduled_at)
                    : (c.subject || 'Draft · no subject yet'));
            if (dl.unique_downloads) {
                meta += ` · ${fmtNum(dl.unique_downloads)} downloads`;
            }
            return `<article class="nl-card">
                <div class="nl-card-preview"><iframe src="/n/preview/${encodeURIComponent(c.id)}" loading="lazy"></iframe></div>
                <div class="nl-card-body">
                    <span class="nl-badge ${c.status || 'draft'}">${c.status || 'draft'}</span>
                    <h3>${esc(c.name || 'Untitled')}</h3>
                    <div class="meta">${esc(meta)}</div>
                    <div class="nl-card-foot">
                        <button class="btn btn-small btn-secondary" onclick="nlOpenEditor('${c.id}')">Edit</button>
                        ${c.status === 'sent' ? `<button class="btn btn-small btn-secondary" onclick="nlOpenReport('${c.id}')">Report</button>` : `<button class="btn btn-small btn-primary" onclick="nlOpenEditor('${c.id}', true)">Send</button>`}
                        <button class="btn btn-small btn-secondary" onclick="nlDuplicate('${c.id}')">Duplicate</button>
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
        $('nl-preview').src = '/n/preview/' + encodeURIComponent(id) + '?t=' + Date.now();
        $('nl-html-file').value = '';
        if ($('nl-dl-file')) $('nl-dl-file').value = '';
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

    window.nlConfirmSend = async function (schedule) {
        if (!state.editingId) return;
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
        const msg = schedule
            ? `Schedule "${subject}" to ${n} people on ${label} at ${fmtWhen(when)}?`
            : `Send "${subject}" to ${n} people on ${label} now? This uses no_reply@crosswalknyc.com. Replies go to hello@crosswalknyc.com.`;
        openModal(msg, async () => {
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
                else toast('Sending to ' + (data.recipients || n) + ' people');
                showView('campaigns');
            } catch (e) { toast(e.message, true); }
        });
    };

    window.nlDeleteCampaign = async function () {
        if (!state.editingId) return;
        if (!confirm('Delete this newsletter?')) return;
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId), { method: 'DELETE' });
            state.data = data;
            state.editingId = null;
            renderAll();
            showView('campaigns');
            toast('Deleted');
        } catch (e) { toast(e.message, true); }
    };

    window.nlOpenReport = async function (id) {
        state.reportId = id;
        try {
            const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(id) + '/report');
            const c = data.campaign || {};
            const s = data.stats || {};
            const d = data.download_stats || {};
            $('nl-report-title').textContent = c.name || 'Report';
            $('nl-report-sub').textContent = (c.subject || '') + (c.sent_at ? ' · sent ' + fmtWhen(c.sent_at) : '');
            $('nl-report-stats').innerHTML = [
                ['Sent', s.sent],
                ['Failed', s.failed],
                ['Unique opens', s.unique_opens],
                ['Opens', s.opens],
                ['Unique clicks', s.unique_clicks],
                ['Clicks', s.clicks],
                ['Unsubscribes', s.unsubs],
                ['Download leads', d.leads],
                ['Downloads', d.unique_downloads],
                ['Paid', d.paid],
                ['Revenue', fmtMoney(d.revenue_usd)],
            ].map(([k, v]) => `<div class="nl-stat"><div class="k">${k}</div><div class="v">${typeof v === 'string' ? esc(v) : fmtNum(v)}</div></div>`).join('');
            const links = data.top_links || [];
            $('nl-report-links').innerHTML = links.length
                ? '<table class="nl-table"><thead><tr><th>Link</th><th>Clicks</th></tr></thead><tbody>' +
                  links.map((l) => `<tr><td>${esc(l.url)}</td><td>${fmtNum(l.clicks)}</td></tr>`).join('') +
                  '</tbody></table>'
                : '<div class="nl-empty">No clicks yet.</div>';
            const dls = data.downloads || [];
            if ($('nl-report-downloads')) {
                $('nl-report-downloads').innerHTML = dls.length
                    ? '<table class="nl-table"><thead><tr><th>Email</th><th>Entered</th><th>Paid</th><th>Downloads</th></tr></thead><tbody>' +
                      dls.map((r) => `<tr><td>${esc(r.email)}</td><td>${r.entered_at ? fmtWhen(r.entered_at) : '-'}</td><td>${r.paid ? fmtMoney(r.amount_usd) : 'Free'}</td><td>${fmtNum(r.download_count)}</td></tr>`).join('') +
                      '</tbody></table>'
                    : '<div class="nl-empty">No one has entered an email for the file yet.</div>';
            }
            const recips = data.recipients || [];
            $('nl-report-recips').innerHTML = recips.length
                ? '<table class="nl-table"><thead><tr><th>Email</th><th>Status</th><th>Opened</th><th>Clicks</th></tr></thead><tbody>' +
                  recips.map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.status || '')}</td><td>${r.opened_at ? fmtWhen(r.opened_at) : '-'}</td><td>${fmtNum(r.click_count)}</td></tr>`).join('') +
                  '</tbody></table>'
                : '<div class="nl-empty">No recipients on this send yet.</div>';
            showView('report');
        } catch (e) { toast(e.message, true); }
    };

    function renderOutcomes() {
        const host = $('nl-outcomes-table');
        if (!host) return;
        const camps = (state.data && state.data.campaigns) || [];
        if (!camps.length) {
            host.innerHTML = '<div class="nl-empty">No campaigns yet.</div>';
            return;
        }
        host.innerHTML = '<table class="nl-table"><thead><tr>' +
            '<th>Campaign</th><th>Status</th><th>Sent</th><th>Open</th><th>Click</th><th>Leads</th><th>Downloads</th><th>Paid</th><th>Revenue</th><th></th>' +
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
        $('nl-set-addr').value = settings.company_address || 'Crosswalk, New York, NY';
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
            toast('Sender settings saved');
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
