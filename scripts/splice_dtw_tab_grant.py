#!/usr/bin/env python3
"""Wire Digital Twins per-tab grants into index.html and admin.html."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "templates" / "index.html"
ADMIN = ROOT / "templates" / "admin.html"


def splice(src, old, new, desc):
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"[{desc}] expected 1 match, found {count}")
    return src.replace(old, new)


def patch_index(src):
    src = splice(
        src,
        "        window.__allowedTrendsTabs = {{ allowed_trends_tabs | default(['*'], true) | tojson | safe }};\n"
        "        window.__allowedRankersTabs = {{ allowed_rankers_tabs | default(['*'], true) | tojson | safe }};\n",
        "        window.__allowedTrendsTabs = {{ allowed_trends_tabs | default(['*'], true) | tojson | safe }};\n"
        "        window.__allowedRankersTabs = {{ allowed_rankers_tabs | default(['*'], true) | tojson | safe }};\n"
        "        window.__allowedDigitalTwinsTabs = {{ allowed_digital_twins_tabs | default(['*'], true) | tojson | safe }};\n"
        "        window.dtwTabAllowed = function(key) {\n"
        "            if (typeof isSuperAdmin !== 'undefined' && isSuperAdmin) return true;\n"
        "            var list = window.__allowedDigitalTwinsTabs;\n"
        "            if (!Array.isArray(list) || list.indexOf('*') !== -1) return true;\n"
        "            var aliases = {create:'twins', creation:'twins', twins_creation:'twins', twin:'twins',\n"
        "                focus:'panel', focus_group:'panel', focus_group_iq:'panel', spend:'launch',\n"
        "                script:'greenlight', concept:'greenlight'};\n"
        "            var k = aliases[key] || key;\n"
        "            return list.indexOf(k) !== -1;\n"
        "        };\n"
        "        window.dtwFirstAllowedTab = function() {\n"
        "            var order = ['twins', 'panel', 'pilot', 'greenlight', 'launch', 'trailer', 'titles', 'norms'];\n"
        "            for (var i = 0; i < order.length; i++) { if (window.dtwTabAllowed(order[i])) return order[i]; }\n"
        "            return 'twins';\n"
        "        };\n"
        "        window.dtwApplyTabGrant = function() {\n"
        "            document.querySelectorAll('#digitalTwinsIQView .dtw-seg').forEach(function(b) {\n"
        "                b.hidden = !window.dtwTabAllowed(b.getAttribute('data-tab'));\n"
        "            });\n"
        "            if (window._dtw && !window.dtwTabAllowed(window._dtw.tab || 'twins')) {\n"
        "                try { window.dtwTab(window.dtwFirstAllowedTab()); } catch (_) {}\n"
        "            }\n"
        "        };\n",
        "index allowed dtw tabs",
    )
    src = splice(
        src,
        "                    if (Array.isArray(d.allowed_trends_tabs)) window.__allowedTrendsTabs = d.allowed_trends_tabs;\n"
        "                    if (Array.isArray(d.allowed_rankers_tabs)) window.__allowedRankersTabs = d.allowed_rankers_tabs;\n",
        "                    if (Array.isArray(d.allowed_trends_tabs)) window.__allowedTrendsTabs = d.allowed_trends_tabs;\n"
        "                    if (Array.isArray(d.allowed_rankers_tabs)) window.__allowedRankersTabs = d.allowed_rankers_tabs;\n"
        "                    if (Array.isArray(d.allowed_digital_twins_tabs)) {\n"
        "                        window.__allowedDigitalTwinsTabs = d.allowed_digital_twins_tabs;\n"
        "                        try { if (typeof window.dtwApplyTabGrant === 'function') window.dtwApplyTabGrant(); } catch (_) {}\n"
        "                    }\n",
        "index product-access refresh",
    )
    src = splice(
        src,
        "                    var v = el('digitalTwinsIQView'); if (v) { v.style.display = 'flex'; v.style.visibility = 'visible'; }\n",
        "                    var v = el('digitalTwinsIQView'); if (v) { v.style.display = 'flex'; v.style.visibility = 'visible'; }\n"
        "                    try { if (typeof window.dtwApplyTabGrant === 'function') window.dtwApplyTabGrant(); } catch (_) {}\n",
        "index showDigitalTwinsIQ apply",
    )
    src = splice(
        src,
        "                if (TABS.indexOf(tab) < 0) tab = 'twins';\n"
        "                S.tab = tab;\n",
        "                if (TABS.indexOf(tab) < 0) tab = 'twins';\n"
        "                if (typeof window.dtwTabAllowed === 'function' && !window.dtwTabAllowed(tab)) {\n"
        "                    var _first = (typeof window.dtwFirstAllowedTab === 'function') ? window.dtwFirstAllowedTab() : 'twins';\n"
        "                    if (_first && _first !== tab) { dtwTab(_first); return; }\n"
        "                    return;\n"
        "                }\n"
        "                S.tab = tab;\n",
        "index dtwTab bounce",
    )
    src = splice(
        src,
        "                var dt = [\n"
        "                    { t: 'Test a concept in Greenlight on the ' + (tw || 'open') + ' twin', hot: true },\n"
        "                    { t: 'Open a focus group with the ' + (tw || 'open') + ' twin and ask whether they would watch a new season' },\n"
        "                    { t: 'How do I use Greenlight on a twin?' },\n"
        "                    { t: 'Analyze the data on this screen' }\n"
        "                ];\n"
        "                return dt;\n",
        "                var dt = [\n"
        "                    { t: 'Test a concept in Greenlight on the ' + (tw || 'open') + ' twin', hot: true, need: 'greenlight' },\n"
        "                    { t: 'Open a focus group with the ' + (tw || 'open') + ' twin and ask whether they would watch a new season', need: 'panel' },\n"
        "                    { t: 'How do I use Greenlight on a twin?', need: 'greenlight' },\n"
        "                    { t: 'Build a twin of this audience', need: 'twins' },\n"
        "                    { t: 'Analyze the data on this screen' }\n"
        "                ];\n"
        "                if (typeof window.dtwTabAllowed === 'function') {\n"
        "                    dt = dt.filter(function(x) { return !x.need || window.dtwTabAllowed(x.need); });\n"
        "                }\n"
        "                return dt;\n",
        "index prometheus chips",
    )
    src = splice(
        src,
        "        #digitalTwinsIQView .dtw-seg.active { color: var(--dtw-ink); border-bottom-color: var(--dtw-accent); }\n",
        "        #digitalTwinsIQView .dtw-seg.active { color: var(--dtw-ink); border-bottom-color: var(--dtw-accent); }\n"
        "        #digitalTwinsIQView .dtw-seg[hidden] { display: none !important; }\n",
        "index hidden tab css",
    )
    return src


def patch_admin(src):
    src = splice(
        src,
        "                    <!-- Digital Twins IQ Access (2026-10-26, Jenna) -->\n"
        "                    <div class=\"dashboard-access-item\" data-access-group=\"core\">\n"
        "                        <div class=\"dashboard-access-header\">\n"
        "                            <label class=\"checkbox-group\" onclick=\"event.stopPropagation()\">\n"
        "                                <input type=\"checkbox\" id=\"hasDigitalTwinsIQAccess\">\n"
        "                                <span style=\"font-weight: 600;\">🧬 DIGITAL TWINS IQ</span>\n"
        "                            </label>\n"
        "                        </div>\n"
        "                        <div style=\"padding:0.4rem 1rem 0.2rem;font-size:0.78rem;color:var(--text-secondary);\">Twins, Greenlight (script reads and concept tests), Launch Lab, Spend Engine, Focus Group IQ and Pilot Testing. Every twin joins the shared library; the user sees all twins unless an explicit list is set through the API.</div>\n"
        "                    </div>\n",
        "                    <!-- Digital Twins IQ Access (2026-10-26, Jenna) -->\n"
        "                    <div class=\"dashboard-access-item\" data-access-group=\"core\">\n"
        "                        <div class=\"dashboard-access-header\">\n"
        "                            <label class=\"checkbox-group\" onclick=\"event.stopPropagation()\">\n"
        "                                <input type=\"checkbox\" id=\"hasDigitalTwinsIQAccess\">\n"
        "                                <span style=\"font-weight: 600;\">🧬 DIGITAL TWINS IQ</span>\n"
        "                            </label>\n"
        "                        </div>\n"
        "                        <div style=\"padding:0.4rem 1rem 0.85rem;font-size:0.78rem;color:var(--text-secondary);\">Twins, Greenlight, Launch, Focus Group IQ and Pilot Testing. Uncheck ALL TABS to hide the ones they should not see.</div>\n"
        "                        <div style=\"padding:0 1rem 0.85rem;\">\n"
        "                            <label class=\"checkbox-group\" style=\"margin:0 0 0.45rem;\">\n"
        "                                <input type=\"checkbox\" id=\"allowAllDigitalTwinsTabs\" checked onchange=\"onAllowAllDigitalTwinsTabsChanged()\">\n"
        "                                <span>ALL TABS</span>\n"
        "                            </label>\n"
        "                            <div style=\"display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:0.3rem;\">\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"twins\"><span>Twins</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"panel\"><span>Focus Group IQ</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"pilot\"><span>Pilot Testing</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"greenlight\"><span>Greenlight</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"launch\"><span>Launch</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"trailer\"><span>Trailers</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"titles\"><span>Titles and Key Art</span></label>\n"
        "                                <label class=\"checkbox-group allowed-dtw-tab-row\" style=\"padding:0.4rem 0.5rem;background:var(--bg-card);border-radius:6px;border:1px solid var(--border-color);\"><input type=\"checkbox\" class=\"allowed-dtw-tab-checkbox\" data-tab=\"norms\"><span>Norms</span></label>\n"
        "                            </div>\n"
        "                        </div>\n"
        "                    </div>\n",
        "admin dtw tab checkboxes",
    )
    src = splice(
        src,
        "            populateAllowedTrendsTabsCheckboxes(user.allowed_trends_tabs);\n"
        "            populateAllowedRankersTabsCheckboxes(user.allowed_rankers_tabs);\n",
        "            populateAllowedTrendsTabsCheckboxes(user.allowed_trends_tabs);\n"
        "            populateAllowedRankersTabsCheckboxes(user.allowed_rankers_tabs);\n"
        "            populateAllowedDigitalTwinsTabsCheckboxes(user.allowed_digital_twins_tabs);\n",
        "admin populate dtw tabs",
    )
    src = splice(
        src,
        "            populateAllowedTrendsTabsCheckboxes(['*']);\n"
        "            populateAllowedRankersTabsCheckboxes(['*']);\n",
        "            populateAllowedTrendsTabsCheckboxes(['*']);\n"
        "            populateAllowedRankersTabsCheckboxes(['*']);\n"
        "            populateAllowedDigitalTwinsTabsCheckboxes(['*']);\n",
        "admin reset dtw tabs",
    )
    src = splice(
        src,
        "                allowed_trends_tabs: getAllowedTrendsTabsPayload(),\n"
        "                allowed_rankers_tabs: getAllowedRankersTabsPayload(),\n",
        "                allowed_trends_tabs: getAllowedTrendsTabsPayload(),\n"
        "                allowed_rankers_tabs: getAllowedRankersTabsPayload(),\n"
        "                allowed_digital_twins_tabs: getAllowedDigitalTwinsTabsPayload(),\n",
        "admin save dtw tabs",
    )
    src = splice(
        src,
        "        function getAllowedRankersTabsPayload() {\n"
        "            const allCb = document.getElementById('allowAllRankersTabs');\n"
        "            if (allCb && allCb.checked) return ['*'];\n"
        "            const out = [];\n"
        "            document.querySelectorAll('.allowed-rankers-tab-checkbox').forEach(cb => {\n"
        "                if (cb.checked && cb.dataset.tab) out.push(cb.dataset.tab);\n"
        "            });\n"
        "            return out;\n"
        "        }\n",
        "        function getAllowedRankersTabsPayload() {\n"
        "            const allCb = document.getElementById('allowAllRankersTabs');\n"
        "            if (allCb && allCb.checked) return ['*'];\n"
        "            const out = [];\n"
        "            document.querySelectorAll('.allowed-rankers-tab-checkbox').forEach(cb => {\n"
        "                if (cb.checked && cb.dataset.tab) out.push(cb.dataset.tab);\n"
        "            });\n"
        "            return out;\n"
        "        }\n"
        "        function populateAllowedDigitalTwinsTabsCheckboxes(allowed) {\n"
        "            _populateTabGrantCheckboxes(allowed, 'allowAllDigitalTwinsTabs', '.allowed-dtw-tab-checkbox');\n"
        "        }\n"
        "        function onAllowAllDigitalTwinsTabsChanged() {\n"
        "            const isAll = !!(document.getElementById('allowAllDigitalTwinsTabs') && document.getElementById('allowAllDigitalTwinsTabs').checked);\n"
        "            document.querySelectorAll('.allowed-dtw-tab-checkbox').forEach(cb => {\n"
        "                cb.disabled = isAll;\n"
        "                if (isAll) cb.checked = true;\n"
        "            });\n"
        "            document.querySelectorAll('.allowed-dtw-tab-row').forEach(row => { row.style.opacity = isAll ? '0.55' : '1'; });\n"
        "        }\n"
        "        function getAllowedDigitalTwinsTabsPayload() {\n"
        "            const allCb = document.getElementById('allowAllDigitalTwinsTabs');\n"
        "            if (allCb && allCb.checked) return ['*'];\n"
        "            const out = [];\n"
        "            document.querySelectorAll('.allowed-dtw-tab-checkbox').forEach(cb => {\n"
        "                if (cb.checked && cb.dataset.tab) out.push(cb.dataset.tab);\n"
        "            });\n"
        "            return out;\n"
        "        }\n",
        "admin dtw tab js",
    )
    return src


def main():
    idx = INDEX.read_text(encoding="utf-8")
    INDEX.write_text(patch_index(idx), encoding="utf-8")
    adm = ADMIN.read_text(encoding="utf-8")
    ADMIN.write_text(patch_admin(adm), encoding="utf-8")
    print("spliced index.html and admin.html")


if __name__ == "__main__":
    main()
