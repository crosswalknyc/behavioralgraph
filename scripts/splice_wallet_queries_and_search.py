#!/usr/bin/env python3
"""Wallet modal: Prometheus Queries section (searchable) + dollar
amounts; Prometheus widget: thread search bar (2026-09-28 Jenna).
Python byte-level splice per index-html-safety.mdc."""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_wallet_queries_search.html")

# 1. Modal: dollar wording on the used row -----------------------------
OLD_USED_ROW = """                <div class="credits-info-row credits-used-row" id="creditsUsedRow" onclick="toggleCreditUsageDetail()" style="cursor: pointer;" title="Click to see what each credit was used for">
                    <span class="credits-info-label">Credits Used</span>"""
NEW_USED_ROW = """                <div class="credits-info-row credits-used-row" id="creditsUsedRow" onclick="toggleCreditUsageDetail()" style="cursor: pointer;" title="Click to see what each dollar went to">
                    <span class="credits-info-label">Spend to date</span>"""

OLD_USED_HEAD = """<span style="font-size:0.56rem; font-weight:600; color:#7C878A; text-transform:uppercase; letter-spacing:0.09em;">What each credit went to</span>"""
NEW_USED_HEAD = """<span style="font-size:0.56rem; font-weight:600; color:#7C878A; text-transform:uppercase; letter-spacing:0.09em;">What each dollar went to</span>"""

# 2. Modal: Prometheus Queries section after the usage detail ----------
OLD_AFTER_USAGE = """                    <div id="creditUsageList"></div>
                </div>
                <div class="credits-info-row">
                    <span class="credits-info-label">Account</span>"""
NEW_AFTER_USAGE = """                    <div id="creditUsageList"></div>
                </div>
                <div class="credits-info-row credits-used-row" id="pmQueriesRow" onclick="togglePmQueriesDetail()" style="cursor: pointer;" title="Every question you have asked Prometheus">
                    <span class="credits-info-label">Prometheus Queries</span>
                    <span class="credits-used-value-wrap">
                        <span class="credits-info-value" id="pmQueriesCount"></span>
                        <span class="credits-used-chevron" id="pmQueriesChevron" aria-label="Expand to view your questions">\u25be</span>
                    </span>
                </div>
                <div id="pmQueriesDetail" style="display: none; margin: 0 0 0.35rem;">
                    <input type="text" id="pmQueriesSearch" placeholder="Search your questions..." oninput="_pmQueriesRender(this.value)" onclick="event.stopPropagation();" style="width:100%; box-sizing:border-box; background:rgba(233,232,225,0.06); border:1px solid rgba(233,232,225,0.16); border-radius:8px; color:#E9E8E1; font-size:0.74rem; padding:0.45rem 0.6rem; margin:0.15rem 0 0.35rem; outline:none; font-family:inherit;">
                    <div id="pmQueriesList" style="max-height: 240px; overflow-y: auto; overflow-x: hidden;"></div>
                </div>
                <div class="credits-info-row">
                    <span class="credits-info-label">Account</span>"""

# 3. Row renderer: dollars instead of credits --------------------------
OLD_ROW_AMOUNT = """                                 +   `<div style="text-align:right; white-space:nowrap;">`
                                 +     `<div style="color:#E9E8E1; font-size:0.74rem; font-weight:600; font-variant-numeric:tabular-nums;">${credits} credit${credits !== 1 ? 's' : ''}</div>`"""
NEW_ROW_AMOUNT = """                                 +   `<div style="text-align:right; white-space:nowrap;">`
                                 +     `<div style="color:#E9E8E1; font-size:0.74rem; font-weight:600; font-variant-numeric:tabular-nums;">${_walletUsdFmt(row, credits)}</div>`"""

# 4. JS: usd formatter + spend total + queries section + thread search -
OLD_JS_ANCHOR = """        let creditUsageCache = null;
        function toggleCreditUsageDetail() {"""
NEW_JS_ANCHOR = """        function _walletUsdFmt(row, credits) {
            // Dollars, not credits (2026-09-28): wallet rows carry usd
            // from the server; legacy rows convert at $60/credit.
            var usd = (row && row.usd != null) ? Number(row.usd)
                      : Number(credits || 0) * 60;
            return '$' + usd.toLocaleString(undefined,
                { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        let _pmQueriesCache = null;
        function togglePmQueriesDetail() {
            const detail = document.getElementById('pmQueriesDetail');
            const chevron = document.getElementById('pmQueriesChevron');
            if (!detail || !chevron) return;
            if (detail.style.display === 'none' || !detail.style.display) {
                detail.style.display = 'block';
                chevron.textContent = '\\u25b4';
                if (_pmQueriesCache) { _pmQueriesRender(); return; }
                const listEl = document.getElementById('pmQueriesList');
                if (listEl) listEl.innerHTML = '<span style="color: var(--text-secondary);">Loading...</span>';
                fetch('/api/me/prometheus-queries')
                    .then(r => r.json())
                    .then(data => {
                        _pmQueriesCache = (data && data.queries) || [];
                        const cEl = document.getElementById('pmQueriesCount');
                        if (cEl) cEl.textContent = _pmQueriesCache.length;
                        _pmQueriesRender();
                    })
                    .catch(() => {
                        if (listEl) listEl.innerHTML = '<span style="color: var(--accent-red);">Could not load your questions.</span>';
                    });
            } else {
                detail.style.display = 'none';
                chevron.textContent = '\\u25be';
            }
        }
        function _pmQueriesRender(filter) {
            const listEl = document.getElementById('pmQueriesList');
            if (!listEl) return;
            const f = String(filter == null
                ? (document.getElementById('pmQueriesSearch') || {}).value || ''
                : filter).trim().toLowerCase();
            const rows = (_pmQueriesCache || []).filter(function (r) {
                return !f || String(r.q || '').toLowerCase().indexOf(f) !== -1;
            });
            if (!rows.length) {
                listEl.innerHTML = '<span style="color: var(--text-secondary);">'
                    + (f ? 'No questions match.' : 'No Prometheus questions yet. Ask anything and it will show here.')
                    + '</span>';
                return;
            }
            listEl.innerHTML = rows.map(function (r) {
                const d = r.ts ? new Date(r.ts) : null;
                const short = (d && !isNaN(d)) ? _walletShortDate(d) : '';
                return '<div style="display:grid; grid-template-columns:minmax(0,1fr) auto; gap:0.75rem; align-items:baseline; padding:0.5rem 0; border-top:1px solid rgba(233,232,225,0.07);">'
                     +   '<div style="min-width:0; color:#E9E8E1; font-size:0.74rem; line-height:1.35;">' + escapeHtml(String(r.q || '')) + '</div>'
                     +   '<div style="color:#7C878A; font-size:0.64rem; white-space:nowrap;">' + escapeHtml(short) + '</div>'
                     + '</div>';
            }).join('');
        }

        let creditUsageCache = null;
        function toggleCreditUsageDetail() {"""

# 5. Spend total in dollars on the live refresh ------------------------
OLD_USED_SET = """                    const usedEl = document.getElementById('modalCreditsUsed');
                    if (usedEl && typeof data.credits_used === 'number') {
                        usedEl.textContent = data.credits_used;
                    }"""
NEW_USED_SET = """                    const usedEl = document.getElementById('modalCreditsUsed');
                    if (usedEl && typeof data.spend_usd_total === 'number') {
                        usedEl.textContent = '$' + Number(data.spend_usd_total).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                    } else if (usedEl && typeof data.credits_used === 'number') {
                        usedEl.textContent = data.credits_used;
                    }"""

# 6. Widget header: thread search bar ---------------------------------
OLD_HEADER_RIGHT = """                <div style="display:flex; align-items:center; gap:0.5rem; flex:0 0 auto;">
                    <span id="synthChatQueueBadge"></span>
                    <button type="button" id="prometheusCollapseBtn" onclick="prometheusCollapse()" aria-label="Collapse Prometheus" title="Collapse">"""
NEW_HEADER_RIGHT = """                <div style="display:flex; align-items:center; gap:0.5rem; flex:0 0 auto;">
                    <input type="text" id="pmThreadSearch" placeholder="Search queries" aria-label="Search your queries" oninput="_pmThreadSearch(this.value)" style="width:132px; background:rgba(233,232,225,0.07); border:1px solid rgba(233,232,225,0.16); border-radius:999px; color:#E9E8E1; font-size:0.68rem; padding:0.28rem 0.65rem; outline:none; font-family:inherit;">
                    <span id="synthChatQueueBadge"></span>
                    <button type="button" id="prometheusCollapseBtn" onclick="prometheusCollapse()" aria-label="Collapse Prometheus" title="Collapse">"""

# 7. Thread search JS + apply-after-render hook ------------------------
OLD_RERENDER_FN = """        function synthChatRerender() {
            var box = document.getElementById('synthChatMessages');
            if (!box) return;"""
NEW_RERENDER_FN = """        var _pmSearchQ = '';
        function _pmThreadSearch(q) {
            // Search past queries in the thread (2026-09-28 Jenna): a
            // user turn that matches stays visible with its reply; an
            // empty box restores the full thread. Survives rerenders
            // via the hook at the end of synthChatRerender.
            _pmSearchQ = String(q || '').trim().toLowerCase();
            _pmApplyThreadSearch();
        }
        function _pmApplyThreadSearch() {
            var box = document.getElementById('synthChatMessages');
            if (!box) return;
            var nodes = box.querySelectorAll('.pm-msg');
            if (!_pmSearchQ) {
                nodes.forEach(function (n) { n.style.display = ''; });
                return;
            }
            var showNext = false;
            nodes.forEach(function (n) {
                var isUser = n.classList.contains('pm-msg-user');
                var txt = (n.textContent || '').toLowerCase();
                if (isUser) {
                    var hit = txt.indexOf(_pmSearchQ) !== -1;
                    n.style.display = hit ? '' : 'none';
                    showNext = hit;
                } else {
                    n.style.display = showNext ? '' : 'none';
                }
            });
        }
        function synthChatRerender() {
            var box = document.getElementById('synthChatMessages');
            if (!box) return;"""


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")
for old, new, desc in (
    (OLD_USED_ROW, NEW_USED_ROW, "used row label"),
    (OLD_USED_HEAD, NEW_USED_HEAD, "usage list header"),
    (OLD_AFTER_USAGE, NEW_AFTER_USAGE, "queries section markup"),
    (OLD_ROW_AMOUNT, NEW_ROW_AMOUNT, "row dollar amount"),
    (OLD_JS_ANCHOR, NEW_JS_ANCHOR, "queries js"),
    (OLD_USED_SET, NEW_USED_SET, "spend total set"),
    (OLD_HEADER_RIGHT, NEW_HEADER_RIGHT, "widget search input"),
    (OLD_RERENDER_FN, NEW_RERENDER_FN, "thread search js"),
):
    src = splice(src, old, new, desc)

# rerender tail hook: apply the active search after every rebuild
OLD_TAIL = "            box.innerHTML = rows;"
count = src.count(OLD_TAIL)
if count != 1:
    raise RuntimeError(f"[rerender tail] anchor found {count}x")
src = src.replace(
    OLD_TAIL,
    "            box.innerHTML = rows;\n"
    "            try { _pmApplyThreadSearch(); } catch (_) {}")

INDEX.write_text(src, encoding="utf-8")
print("spliced 9 changes")
