#!/usr/bin/env python3
"""Add 'All Data (CSV)' export that covers every tab in one file.

Per Jenna 2026-09-08 (verbatim): "there should be a way to download all
data displayed in the csv not just current page and it should include if
they have cuts selected and gen pop, this did exist not sure what
happened to it".

Provenance: an earlier menu item labeled "All Data (CSV)" existed in
Feb 2026 (commit cd38681c on netflix-ranker-cron-render-dev-yaml) but
the label lied - the exportToCSV() function underneath was always per-
tab scoped. That commit renamed the label to "Current Page (CSV)" to
match reality. What Jenna remembers is the LABEL; the actual all-tabs
export never existed. This splice makes it real.

Splices to templates/index.html (byte-safe, StrReplace-free per
`index-html-safety.mdc`):

1) Insert new dropdown link "All Data (CSV)" between "Current Page (CSV)"
   and "All Charts (PNG)".

2) Insert new function exportAllTabsCSV() right after exportToCSV(). The
   function walks Demographics, Behavioral, Interests, Location, and
   Insights in that order, appending each section to one csvContent
   string. Every section already carries the profile %, Gen Pop %, and
   Index columns for the primary profile, plus one pair (%, Index or %,
   Numbers, Index) per selected cut using _extraCohortsForCsv() and the
   _cohort* readers that exportToCSV() already relies on. Downloads as
   `{profileName}_All_Data.csv`.

Idempotent: both splices no-op if already applied.
"""
from pathlib import Path
import sys

INDEX = Path(__file__).resolve().parents[1] / 'templates' / 'index.html'


def splice(src: str, old: str, new: str, desc: str) -> tuple[str, bool]:
    if new in src and old not in src:
        print(f"  [skip] {desc}: already applied")
        return src, False
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x (must be unique)")
    print(f"  [apply] {desc}")
    return src.replace(old, new), True


# ---------------------------------------------------------------------------
# 1) Dropdown link
# ---------------------------------------------------------------------------
MENU_OLD = """                            <a href="javascript:void(0)" onclick="exportToCSV(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">📄 Current Page (CSV)</a>
                            <a href="javascript:void(0)" onclick="exportAllPNG(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">🖼️ All Charts (PNG)</a>"""

MENU_NEW = """                            <a href="javascript:void(0)" onclick="exportToCSV(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">📄 Current Page (CSV)</a>
                            <!-- 2026-09-08 (Jenna): true all-tabs export.
                                 Covers Demographics + Behavioral +
                                 Interests + Location + Insights in one
                                 CSV, includes selected cut columns and
                                 Gen Pop % / Index alongside every profile
                                 value. See exportAllTabsCSV(). -->
                            <a href="javascript:void(0)" onclick="exportAllTabsCSV(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">📚 All Data (CSV)</a>
                            <a href="javascript:void(0)" onclick="exportAllPNG(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">🖼️ All Charts (PNG)</a>"""


# ---------------------------------------------------------------------------
# 2) New function exportAllTabsCSV() inserted right after exportToCSV()
# ---------------------------------------------------------------------------
# Anchor: the closing brace of exportToCSV() + the start of exportAllPNG().
FN_OLD = """            showNotification(`✅ Exported ${tabLabel} data to CSV`, 'success');
        }
        
        function exportAllPNG() {"""

FN_NEW = """            showNotification(`✅ Exported ${tabLabel} data to CSV`, 'success');
        }
        
        // ---------------------------------------------------------------
        // 2026-09-08 (Jenna): true "All Data (CSV)" export.
        // Walks every dashboard tab and concatenates one CSV that
        // includes:
        //   - Demographics (all 11 categories, Profile % / Gen Pop % / Index)
        //   - Behavioral   (every category, Audience % / Numbers /
        //                   Gen Pop % / Numbers / Index)
        //   - Interests    (Audience % / Gen Pop % / Index)
        //   - Location     (DMA Rank / DMA / Audience % / Gen Pop % / Index)
        //   - Insights     (sample size, US projection, top demos)
        // Every section includes one pair (% + Index) or triple (% +
        // Numbers + Index) per selected cut, via the same
        // _extraCohortsForCsv() + _cohort* helpers exportToCSV() uses.
        // Structure and formatting mirror exportToCSV() so the two paths
        // never drift; when exportToCSV() gains a new column, add it
        // here too.
        // ---------------------------------------------------------------
        function exportAllTabsCSV() {
            if (!currentProfileData || !currentDashboardData) {
                alert('Please select a profile first');
                return;
            }

            const profileName = currentProfileData.name || 'Profile';
            const US_POP = 329900000;
            let csvContent = `${profileName} - All Data\\n\\n`;

            // Cuts are shared across every section so the reader can
            // compare cohort vs primary vs Gen Pop in the same file.
            const _allCohorts = _extraCohortsForCsv();

            trackActivity('export_all_data_csv', 'all_tabs');

            // -----------------------------------------------------------
            // Section 1: DEMOGRAPHICS
            // -----------------------------------------------------------
            csvContent += `=== DEMOGRAPHICS ===\\n\\n`;
            const _allDemoCats = [
                { key: 'age', title: 'Age' },
                { key: 'gender', title: 'Gender' },
                { key: 'ethnicity', title: 'Ethnicity' },
                { key: 'income', title: 'Income' },
                { key: 'education', title: 'Education' },
                { key: 'relationship', title: 'Relationship Status' },
                { key: 'sexual_orientation', title: 'Sexual Orientation' },
                { key: 'parental_status', title: 'Parental Status' },
                { key: 'occupation', title: 'Occupation' },
                { key: 'age_of_children', title: 'Age of Children' },
                { key: 'number_of_children', title: 'Number of Children' }
            ];
            const _allSoLabel = (raw) => {
                const u = (raw || '').trim();
                if (!u) return u;
                const upper = u.toUpperCase();
                if (upper === 'NO' || upper === 'STRAIGHT / HETEROSEXUAL') return 'STRAIGHT / HETEROSEXUAL';
                if (upper === 'YES' || upper === 'GAY OR LESBIAN' || upper === 'LGBTQ+') return 'LGBTQ+';
                if (upper === 'OTHER' || upper === 'ANOTHER SEXUAL ORIENTATION') return 'OTHER';
                if (upper === 'PREFER NOT TO SAY') return 'PREFER NOT TO SAY';
                return upper;
            };
            let _allDemoCohortHead = '';
            _allCohorts.forEach(function (c) {
                _allDemoCohortHead += ',' + _csvSafeHeader(c.label, '%');
                _allDemoCohortHead += ',' + _csvSafeHeader(c.label, 'Index');
            });
            _allDemoCats.forEach(({ key: catKey, title: catTitle }) => {
                const data = (currentDashboardData.demographics || {})[catKey] || {};
                const index = (currentDashboardData.demographicsIndex || {})[catKey] || {};
                const genPop = (currentDashboardData.demographicsGenPop || {})[catKey] || {};
                if (Object.keys(data).length === 0) return;
                csvContent += `${catTitle.toUpperCase()}\\n`;
                csvContent += `Label,Profile %,Gen Pop %,Index${_allDemoCohortHead}\\n`;
                const labelFn = (catKey === 'sexual_orientation') ? _allSoLabel : (l) => l;
                Object.keys(data).forEach(label => {
                    const val = (parseFloat(data[label]) || 0).toFixed(2);
                    const gpNum = parseFloat(genPop[label]) || 0;
                    const gp = gpNum.toFixed(2);
                    const idx = Math.round(parseFloat(index[label]) || 100);
                    let cohortCells = '';
                    _allCohorts.forEach(function (c) {
                        const cPct = _cohortDemoPct(c, catKey, label);
                        const cIdx = _cohortDemoIndex(c, catKey, label, gpNum);
                        cohortCells += ',' + (cPct == null ? '' : cPct.toFixed(2) + '%');
                        cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `"${labelFn(label)}",${val}%,${gp}%,${idx}${cohortCells}\\n`;
                });
                csvContent += `\\n`;
            });

            // -----------------------------------------------------------
            // Section 2: BEHAVIORAL
            // -----------------------------------------------------------
            csvContent += `\\n=== BEHAVIORAL ===\\n\\n`;
            const behavioral = currentDashboardData.behavioral || {};
            let _allBehCohortHead = '';
            _allCohorts.forEach(function (c) {
                _allBehCohortHead += ',' + _csvSafeHeader(c.label, '%');
                _allBehCohortHead += ',' + _csvSafeHeader(c.label, 'Numbers');
                _allBehCohortHead += ',' + _csvSafeHeader(c.label, 'Index');
            });
            Object.keys(behavioral).forEach(category => {
                const items = behavioral[category] || [];
                if (items.length === 0) return;
                csvContent += `${category.toUpperCase()}\\n`;
                csvContent += `Name,Audience %,Audience Numbers,Gen Pop %,Gen Pop Numbers,Index${_allBehCohortHead}\\n`;
                items.forEach(item => {
                    const audiencePct = (item.pct || 0).toFixed(2);
                    const audienceNumbers = (item.projection != null && item.projection !== '')
                        ? Math.round(Number(item.projection))
                        : Math.round(((item.pct || 0) / 100) * US_POP);
                    const genPopPct = item.genPopPct || 0;
                    const genPopNumbers = (item.genPopProjection != null && item.genPopProjection !== '')
                        ? Math.round(Number(item.genPopProjection))
                        : Math.round((genPopPct / 100) * US_POP);
                    const index = item.index || 0;
                    let cohortCells = '';
                    _allCohorts.forEach(function (c) {
                        const bi = _cohortBehavioralItem(c, category, item.name);
                        const cPct = (bi && bi.pct != null) ? Number(bi.pct) : null;
                        const cProj = (bi && bi.projection != null && bi.projection !== '')
                            ? Math.round(Number(bi.projection))
                            : (cPct != null ? Math.round((cPct / 100) * US_POP) : null);
                        let cIdx = (bi && bi.index != null && isFinite(bi.index)) ? Math.round(Number(bi.index)) : '';
                        if (cIdx === '' && cPct != null && genPopPct > 0) {
                            cIdx = Math.round((cPct / genPopPct) * 100);
                        }
                        cohortCells += ',' + (cPct == null ? '' : cPct.toFixed(2) + '%');
                        cohortCells += ',' + (cProj == null ? '' : cProj);
                        cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `"${item.name}",${audiencePct}%,${audienceNumbers},${genPopPct.toFixed(2)}%,${genPopNumbers},${index}${cohortCells}\\n`;
                });
                csvContent += `\\n`;
            });

            // -----------------------------------------------------------
            // Section 3: INTERESTS
            // -----------------------------------------------------------
            csvContent += `\\n=== INTERESTS ===\\n\\n`;
            const interests = currentDashboardData.interests || {};
            let _allIntCohortHead = '';
            _allCohorts.forEach(function (c) {
                _allIntCohortHead += ',' + _csvSafeHeader(c.label, '%');
                _allIntCohortHead += ',' + _csvSafeHeader(c.label, 'Index');
            });
            csvContent += `Interest,Audience %,Gen Pop %,Index${_allIntCohortHead}\\n`;
            Object.entries(interests).forEach(([name, vals]) => {
                const pctNum = (vals.pct != null ? Number(vals.pct) : 0);
                const pct = pctNum.toFixed(2);
                const gpPctNum = (vals.genPopPct != null ? Number(vals.genPopPct) : 0);
                const genPopPct = gpPctNum.toFixed(2);
                const index = vals.index != null ? vals.index : 0;
                let cohortCells = '';
                _allCohorts.forEach(function (c) {
                    const ii = _cohortInterestItem(c, name);
                    const cPct = (ii && ii.pct != null) ? Number(ii.pct) : null;
                    let cIdx = (ii && ii.index != null && isFinite(ii.index)) ? Math.round(Number(ii.index)) : '';
                    if (cIdx === '' && cPct != null && gpPctNum > 0) {
                        cIdx = Math.round((cPct / gpPctNum) * 100);
                    }
                    cohortCells += ',' + (cPct == null ? '' : cPct.toFixed(2));
                    cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                });
                csvContent += `"${String(name).replace(/"/g, '""')}",${pct},${genPopPct},${index}${cohortCells}\\n`;
            });

            // -----------------------------------------------------------
            // Section 4: LOCATION (sorted like the tab: DMA rank asc)
            // -----------------------------------------------------------
            csvContent += `\\n\\n=== LOCATION ===\\n\\n`;
            const locations = currentDashboardData.locations || [];
            let locSorted = [...locations];
            try {
                const st = (typeof locationSortState !== 'undefined' && locationSortState && locationSortState['all']) || { column: 'rank', direction: 'asc' };
                if (st.column === 'rank') {
                    locSorted.sort((a, b) => {
                        const diff = (getDMARank(a.name) || 999) - (getDMARank(b.name) || 999);
                        return st.direction === 'asc' ? diff : -diff;
                    });
                } else if (st.column === 'target' || st.column === 'percentage' || st.column === 'profilerank') {
                    locSorted.sort((a, b) => {
                        const diff = b.pct - a.pct;
                        return st.direction === 'asc' ? -diff : diff;
                    });
                } else if (st.column === 'index') {
                    locSorted.sort((a, b) => {
                        const diff = b.index - a.index;
                        return st.direction === 'asc' ? -diff : diff;
                    });
                } else if (st.column === 'genpop') {
                    locSorted.sort((a, b) => {
                        const diff = (b.genPopPct ?? 0) - (a.genPopPct ?? 0);
                        return st.direction === 'asc' ? -diff : diff;
                    });
                }
            } catch (e) {}
            let _allLocCohortHead = '';
            _allCohorts.forEach(function (c) {
                _allLocCohortHead += ',' + _csvSafeHeader(c.label, '%');
                _allLocCohortHead += ',' + _csvSafeHeader(c.label, 'Index');
            });
            csvContent += `DMA Rank,DMA,Audience %,Gen Pop %,Index${_allLocCohortHead}\\n`;
            locSorted.forEach(loc => {
                const rank = getDMARank(loc.name) || '';
                const gpPctNum = (loc.genPopPct != null) ? Number(loc.genPopPct) : 0;
                let cohortCells = '';
                _allCohorts.forEach(function (c) {
                    const li = _cohortLocationItem(c, loc.name);
                    const cPct = (li && li.pct != null) ? Number(li.pct) : null;
                    let cIdx = (li && li.index != null && isFinite(li.index)) ? Math.round(Number(li.index)) : '';
                    if (cIdx === '' && cPct != null && gpPctNum > 0) {
                        cIdx = Math.round((cPct / gpPctNum) * 100);
                    }
                    cohortCells += ',' + (cPct == null ? '' : cPct.toFixed(4) + '%');
                    cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                });
                csvContent += `${rank},"${normalizeDmaDisplay(loc.name)}",${(loc.pct || 0).toFixed(4)}%,${(loc.genPopPct || 0).toFixed(4)}%,${loc.index || 0}${cohortCells}\\n`;
            });

            // -----------------------------------------------------------
            // Section 5: INSIGHTS SUMMARY
            // -----------------------------------------------------------
            csvContent += `\\n\\n=== INSIGHTS SUMMARY ===\\n\\n`;
            csvContent += `Metric,Value\\n`;
            csvContent += `"Sample Size",${currentProfileData.sampleSize || currentDashboardData.sampleSize || 'N/A'}\\n`;
            csvContent += `"US",${currentProfileData.projectedUS || currentDashboardData.projectedUS || 'N/A'}\\n`;
            csvContent += `\\nTOP DEMOGRAPHICS\\n`;
            ['age', 'gender', 'ethnicity', 'income'].forEach(cat => {
                const data = (currentDashboardData.demographics || {})[cat] || {};
                const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
                if (sorted.length > 0) {
                    csvContent += `"Top ${cat}","${sorted[0][0]} (${parseFloat(sorted[0][1]).toFixed(1)}%)"\\n`;
                }
            });

            // Download
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            const safeName = String(profileName).replace(/[^a-zA-Z0-9_.-]/g, '_');
            link.download = `${safeName}_All_Data.csv`;
            link.click();

            const cutSuffix = _allCohorts.length > 0
                ? ` (with ${_allCohorts.length} cut${_allCohorts.length === 1 ? '' : 's'})`
                : '';
            showNotification(`✅ Exported all data to CSV${cutSuffix}`, 'success');
        }

        function exportAllPNG() {"""


def main() -> int:
    src = INDEX.read_text(encoding='utf-8')
    orig_bytes = len(src.encode('utf-8'))
    src, _ = splice(src, MENU_OLD, MENU_NEW, "add 'All Data (CSV)' dropdown item")
    src, _ = splice(src, FN_OLD, FN_NEW, "add exportAllTabsCSV() function")
    if src == INDEX.read_text(encoding='utf-8'):
        print("[skip] both splices already applied; no write")
        return 0
    INDEX.write_text(src, encoding='utf-8')
    new_bytes = len(src.encode('utf-8'))
    print(f"delta = {new_bytes - orig_bytes:+d} bytes")
    print()
    print("Next:")
    print("  1) python3 scripts/validate_index_html.py")
    print("  2) commit + push submodule + parent to main")
    return 0


if __name__ == '__main__':
    sys.exit(main())
