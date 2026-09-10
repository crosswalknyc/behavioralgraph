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

    function showView(name) {
        state.view = name;
        ['campaigns', 'editor', 'audience', 'report'].forEach((v) => {
            const el = $('nl-view-' + v);
            if (el) el.classList.toggle('nl-hidden', v !== name);
        });
        document.querySelectorAll('#section-newsletter .nl-nav button').forEach((b) => {
            b.classList.toggle('on', b.getAttribute('data-view') === (name === 'editor' || name === 'report' ? 'campaigns' : name));
        });
    }

    function renderStats() {
        const s = (state.data && state.data.stats) || {};
        $('nl-stat-subs').textContent = fmtNum(s.subscribers);
        $('nl-stat-sent').textContent = fmtNum(s.emails_sent);
        $('nl-stat-open').textContent = (s.avg_open_rate || 0) + '%';
        $('nl-stat-click').textContent = (s.avg_click_rate || 0) + '%';
        const ident = (state.data && state.data.from_identity) || 'The Read <no_reply@crosswalknyc.com>';
        $('nl-from-line').textContent = 'Sends from ' + ident;
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
            const meta = c.status === 'sent'
                ? `${fmtNum(stats.sent)} sent · ${c.open_rate}% opened · ${c.click_rate}% clicked`
                : (c.status === 'scheduled'
                    ? 'Sends ' + fmtWhen(c.scheduled_at)
                    : (c.subject || 'Draft · no subject yet'));
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

    function fillListSelect(sel, current) {
        const lists = (state.data && state.data.lists) || [];
        sel.innerHTML = lists.map((l) => {
            const n = l.subscriber_count || 0;
            const selAttr = l.id === current ? ' selected' : '';
            return `<option value="${esc(l.id)}"${selAttr}>${esc(l.name)} (${n})</option>`;
        }).join('');
    }

    window.nlOpenEditor = async function (id, focusSend) {
        state.editingId = id;
        const camp = ((state.data && state.data.campaigns) || []).find((c) => c.id === id);
        $('nl-ed-name').value = (camp && camp.name) || '';
        $('nl-ed-subject').value = (camp && camp.subject) || '';
        $('nl-ed-preheader').value = (camp && camp.preheader) || '';
        $('nl-ed-from-name').value = (camp && camp.from_name) || 'The Read';
        $('nl-ed-reply').value = (camp && camp.reply_to) || 'jenna@crosswalknyc.com';
        fillListSelect($('nl-ed-list'), camp && camp.list_id);
        $('nl-ed-schedule').value = toLocalInput(camp && camp.scheduled_at);
        $('nl-preview').src = '/n/preview/' + encodeURIComponent(id) + '?t=' + Date.now();
        $('nl-html-file').value = '';
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
                body: JSON.stringify({
                    name: $('nl-ed-name').value,
                    subject: $('nl-ed-subject').value,
                    preheader: $('nl-ed-preheader').value,
                    from_name: $('nl-ed-from-name').value,
                    reply_to: $('nl-ed-reply').value,
                    list_id: $('nl-ed-list').value,
                    scheduled_at: fromLocalInput($('nl-ed-schedule').value),
                }),
            });
            state.dirty = false;
            toast('Saved');
            await nlRefresh(true);
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
        const listId = $('nl-ed-list').value;
        const list = ((state.data && state.data.lists) || []).find((l) => l.id === listId);
        const n = list ? (list.subscriber_count || 0) : 0;
        if (!n) {
            toast('That list has no subscribed people yet. Add them under Audience.', true);
            return;
        }
        const when = schedule ? fromLocalInput($('nl-ed-schedule').value) : '';
        if (schedule && !when) {
            toast('Pick a send time first', true);
            return;
        }
        const subject = $('nl-ed-subject').value || '(no subject)';
        const msg = schedule
            ? `Schedule "${subject}" to ${n} people on ${list.name} at ${fmtWhen(when)}?`
            : `Send "${subject}" to ${n} people on ${list.name} now? This uses no_reply@crosswalknyc.com.`;
        openModal(msg, async () => {
            try {
                const data = await api('/api/admin/newsletter/campaigns/' + encodeURIComponent(state.editingId) + '/send', {
                    method: 'POST',
                    body: JSON.stringify({
                        list_id: listId,
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
            ].map(([k, v]) => `<div class="nl-stat"><div class="k">${k}</div><div class="v">${fmtNum(v)}</div></div>`).join('');
            const links = data.top_links || [];
            $('nl-report-links').innerHTML = links.length
                ? '<table class="nl-table"><thead><tr><th>Link</th><th>Clicks</th></tr></thead><tbody>' +
                  links.map((l) => `<tr><td>${esc(l.url)}</td><td>${fmtNum(l.clicks)}</td></tr>`).join('') +
                  '</tbody></table>'
                : '<div class="nl-empty">No clicks yet.</div>';
            const recips = data.recipients || [];
            $('nl-report-recips').innerHTML = recips.length
                ? '<table class="nl-table"><thead><tr><th>Email</th><th>Status</th><th>Opened</th><th>Clicks</th></tr></thead><tbody>' +
                  recips.map((r) => `<tr><td>${esc(r.email)}</td><td>${esc(r.status || '')}</td><td>${r.opened_at ? fmtWhen(r.opened_at) : '-'}</td><td>${fmtNum(r.click_count)}</td></tr>`).join('') +
                  '</tbody></table>'
                : '<div class="nl-empty">No recipients on this send yet.</div>';
            showView('report');
        } catch (e) { toast(e.message, true); }
    };

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
        const settings = (state.data && state.data.settings) || {};
        $('nl-set-from').value = settings.from_name || 'The Read';
        $('nl-set-reply').value = settings.reply_to || 'jenna@crosswalknyc.com';
        $('nl-set-addr').value = settings.company_address || 'Crosswalk, New York, NY';
        const q = ($('nl-sub-search').value || '').trim().toLowerCase();
        const rows = ((state.data && state.data.subscribers) || []).filter((s) => {
            if (!q) return true;
            return [s.email, s.name, s.company, (s.list_ids || []).join(' ')].join(' ').toLowerCase().includes(q);
        });
        $('nl-sub-table').innerHTML = rows.length
            ? '<table class="nl-table"><thead><tr><th>Email</th><th>Name</th><th>Company</th><th>Lists</th><th>Status</th><th></th></tr></thead><tbody>' +
              rows.map((s) => `<tr>
                <td>${esc(s.email)}</td>
                <td>${esc(s.name || '')}</td>
                <td>${esc(s.company || '')}</td>
                <td>${esc((s.list_ids || []).map(listName).join(', '))}</td>
                <td>${esc(s.status || '')}</td>
                <td><button class="btn btn-small btn-secondary" onclick="nlRemoveSub('${esc(s.email)}')">Remove</button></td>
              </tr>`).join('') + '</tbody></table>'
            : '<div class="nl-empty">No people on the list yet. Add an email, or sync dashboard users.</div>';
    }

    window.nlAddSubscriber = async function () {
        const email = $('nl-add-email').value;
        const name = $('nl-add-name').value;
        const listId = $('nl-add-list').value;
        try {
            const data = await api('/api/admin/newsletter/subscribers', {
                method: 'POST',
                body: JSON.stringify({ email, name, list_ids: [listId] }),
            });
            state.data = data;
            $('nl-add-email').value = '';
            $('nl-add-name').value = '';
            renderAll();
            toast('Added');
        } catch (e) { toast(e.message, true); }
    };

    window.nlBulkAdd = async function () {
        const raw = $('nl-bulk-emails').value || '';
        const emails = raw.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
        if (!emails.length) { toast('Paste emails first', true); return; }
        const listId = $('nl-add-list').value;
        try {
            const data = await api('/api/admin/newsletter/subscribers', {
                method: 'POST',
                body: JSON.stringify({
                    subscribers: emails.map((email) => ({ email, list_ids: [listId] })),
                }),
            });
            state.data = data;
            $('nl-bulk-emails').value = '';
            renderAll();
            toast('Added ' + emails.length);
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
            showView(state.view === 'editor' && state.editingId ? 'editor' : 'campaigns');
        } catch (e) {
            toast(e.message, true);
        }
    };

    window.nlShow = function (view) {
        if (view === 'campaigns' || view === 'audience') showView(view);
        if (view === 'audience') renderAudience();
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
