#!/usr/bin/env python3
"""Fix trailing-zero cosmetics + behavioral Gen Pop lookup bug in CSV exports.

Per Jenna 2026-09-08 (verbatim, in response to the new All Data (CSV)):
"see the 00s on output". Two distinct issues surfaced:

  A) BEHAVIORAL: every row shipped Gen Pop = 0.00% and Gen Pop Numbers
     = 0. Real bug in exportToCSV() (pre-existing) and inherited by
     exportAllTabsCSV(). Both read `item.genPopPct` which is empty on
     behavioral items. The authoritative source is
     `currentDashboardData.genPopBehavioral[category][item.name]` --
     the same lookup exportSpotlightCSV uses at line ~137325.

  B) Trailing-zero cosmetics: percentages like `18.00%` (demographics)
     and `0.2000%` (location) are visible zeros that read as fake
     precision. Per Jenna's direct feedback, trim trailing zeros so
     `18.00%` -> `18%`, `18.50%` -> `18.5%`, `0.2000%` -> `0.2%`, and
     `0.3456%` stays as `0.3456%`. Rule
     `no-round-numbers-in-deliverables.mdc` allows round percentages
     but Jenna's direct feedback wins.

Fixes both `exportToCSV()` (Current Page CSV, all tabs) AND
`exportAllTabsCSV()` (All Data CSV) so the two shipping paths stay
consistent.

Splices:

1) Add `_fmtPctTrim(v, maxDp)` helper right after `_fmtInt`.

2) exportToCSV Demographics: swap `.toFixed(2)` on Profile % / Gen Pop %
   for _fmtPctTrim; keep Index integer as-is.

3) exportToCSV Behavioral: authoritative Gen Pop lookup +
   trim-zero pcts. Cohort cells too.

4) exportToCSV Interests: trim-zero pcts.

5) exportToCSV Location: trim-zero pcts (was toFixed(4)).

6) exportAllTabsCSV Demographics: same trim.

7) exportAllTabsCSV Behavioral: same Gen Pop fix + trim.

8) exportAllTabsCSV Interests: trim.

9) exportAllTabsCSV Location: trim.

Idempotent: every splice no-ops if already applied.
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
# 1) Insert _fmtPctTrim helper right after _fmtInt
# ---------------------------------------------------------------------------
HELPER_OLD = """        function _fmtInt(v) {
            if (v == null || v === '' || !isFinite(v)) return '';
            return String(Math.round(Number(v)));
        }

        function exportSpotlightCSV(category) {"""

HELPER_NEW = """        function _fmtInt(v) {
            if (v == null || v === '' || !isFinite(v)) return '';
            return String(Math.round(Number(v)));
        }

        // 2026-09-08 (Jenna): trim trailing zeros on percentage cells
        // in every CSV export so the file reads truthful ('18%' not
        // '18.00%', '0.2%' not '0.2000%') while preserving up to maxDp
        // decimals when they carry information. Empty input returns ''
        // so downstream code that concatenates it stays clean.
        function _fmtPctTrim(v, maxDp) {
            if (v == null || v === '' || !isFinite(v)) return '';
            const dp = (typeof maxDp === 'number' && maxDp >= 0) ? maxDp : 4;
            let s = Number(v).toFixed(dp);
            if (s.indexOf('.') !== -1) {
                s = s.replace(/0+$/, '').replace(/\\.$/, '');
            }
            return s;
        }

        function exportSpotlightCSV(category) {"""


# ---------------------------------------------------------------------------
# 2) exportToCSV Demographics -- trim trailing zeros
# ---------------------------------------------------------------------------
ETC_DEMO_OLD = """                        Object.keys(data).forEach(label => {
                            const val = (parseFloat(data[label]) || 0).toFixed(2);
                            const gpNum = parseFloat(genPop[label]) || 0;
                            const gp = gpNum.toFixed(2);
                            const idx = Math.round(parseFloat(index[label]) || 100);
                            let _pageDemoCohortCells = '';
                            _pageDemoCohorts.forEach(function (c) {
                                const cPct = _cohortDemoPct(c, catKey, label);
                                const cIdx = _cohortDemoIndex(c, catKey, label, gpNum);
                                _pageDemoCohortCells += ',' + (cPct == null ? '' : cPct.toFixed(2) + '%');
                                _pageDemoCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                            });
                            csvContent += `"${labelFn(label)}",${val}%,${gp}%,${idx}${_pageDemoCohortCells}\\n`;
                        });"""

ETC_DEMO_NEW = """                        Object.keys(data).forEach(label => {
                            const valNum = parseFloat(data[label]) || 0;
                            const val = _fmtPctTrim(valNum, 2);
                            const gpNum = parseFloat(genPop[label]) || 0;
                            const gp = _fmtPctTrim(gpNum, 2);
                            const idx = Math.round(parseFloat(index[label]) || 100);
                            let _pageDemoCohortCells = '';
                            _pageDemoCohorts.forEach(function (c) {
                                const cPct = _cohortDemoPct(c, catKey, label);
                                const cIdx = _cohortDemoIndex(c, catKey, label, gpNum);
                                _pageDemoCohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 2) + '%');
                                _pageDemoCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                            });
                            csvContent += `"${labelFn(label)}",${val}%,${gp}%,${idx}${_pageDemoCohortCells}\\n`;
                        });"""


# ---------------------------------------------------------------------------
# 3) exportToCSV Behavioral -- authoritative Gen Pop lookup + trim
# ---------------------------------------------------------------------------
ETC_BEH_OLD = """                Object.keys(behavioral).forEach(category => {
                    const items = behavioral[category] || [];
                    if (items.length > 0) {
                        csvContent += `${category.toUpperCase()}\\n`;
                        csvContent += `Name,Audience %,Audience Numbers,Gen Pop %,Gen Pop Numbers,Index${_pageBehCohortHead}\\n`;
                        items.forEach(item => {
                            const audiencePct = (item.pct || 0).toFixed(2);
                            const audienceNumbers = (item.projection != null && item.projection !== '') ? Math.round(Number(item.projection)) : Math.round(((item.pct || 0) / 100) * US_POP);
                            const genPopPct = item.genPopPct || 0;
                            const genPopNumbers = (item.genPopProjection != null && item.genPopProjection !== '') ? Math.round(Number(item.genPopProjection)) : Math.round((genPopPct / 100) * US_POP);
                            const index = item.index || 0;
                            let _pageBehCohortCells = '';
                            _pageBehCohorts.forEach(function (c) {
                                const bi = _cohortBehavioralItem(c, category, item.name);
                                const cPct = (bi && bi.pct != null) ? Number(bi.pct) : null;
                                const cProj = (bi && bi.projection != null && bi.projection !== '')
                                    ? Math.round(Number(bi.projection))
                                    : (cPct != null ? Math.round((cPct / 100) * US_POP) : null);
                                let cIdx = (bi && bi.index != null && isFinite(bi.index)) ? Math.round(Number(bi.index)) : '';
                                if (cIdx === '' && cPct != null && genPopPct > 0) {
                                    cIdx = Math.round((cPct / genPopPct) * 100);
                                }
                                _pageBehCohortCells += ',' + (cPct == null ? '' : cPct.toFixed(2) + '%');
                                _pageBehCohortCells += ',' + (cProj == null ? '' : cProj);
                                _pageBehCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                            });
                            csvContent += `"${item.name}",${audiencePct}%,${audienceNumbers},${genPopPct.toFixed(2)}%,${genPopNumbers},${index}${_pageBehCohortCells}\\n`;
                        });
                        csvContent += `\\n`;
                    }
                });"""

ETC_BEH_NEW = """                // 2026-09-08 (Jenna): genPopBehavioral is the authoritative
                // Gen Pop source for behavioral rows. item.genPopPct is
                // empty on most profiles so the prior read produced
                // Gen Pop = 0.00% across every row.
                const _etcGpBehavioral = currentDashboardData.genPopBehavioral || {};
                Object.keys(behavioral).forEach(category => {
                    const items = behavioral[category] || [];
                    if (items.length > 0) {
                        const _etcGpCatMap = _etcGpBehavioral[category]
                            || _etcGpBehavioral[String(category).toUpperCase()]
                            || {};
                        csvContent += `${category.toUpperCase()}\\n`;
                        csvContent += `Name,Audience %,Audience Numbers,Gen Pop %,Gen Pop Numbers,Index${_pageBehCohortHead}\\n`;
                        items.forEach(item => {
                            const audiencePct = _fmtPctTrim(item.pct || 0, 2);
                            const audienceNumbers = (item.projection != null && item.projection !== '') ? Math.round(Number(item.projection)) : Math.round(((item.pct || 0) / 100) * US_POP);
                            const _gpFromMap = parseFloat(_etcGpCatMap[item.name]);
                            const genPopPct = isFinite(_gpFromMap) ? _gpFromMap : (parseFloat(item.genPopPct) || 0);
                            const genPopNumbers = (item.genPopProjection != null && item.genPopProjection !== '') ? Math.round(Number(item.genPopProjection)) : Math.round((genPopPct / 100) * US_POP);
                            const index = item.index || 0;
                            let _pageBehCohortCells = '';
                            _pageBehCohorts.forEach(function (c) {
                                const bi = _cohortBehavioralItem(c, category, item.name);
                                const cPct = (bi && bi.pct != null) ? Number(bi.pct) : null;
                                const cProj = (bi && bi.projection != null && bi.projection !== '')
                                    ? Math.round(Number(bi.projection))
                                    : (cPct != null ? Math.round((cPct / 100) * US_POP) : null);
                                let cIdx = (bi && bi.index != null && isFinite(bi.index)) ? Math.round(Number(bi.index)) : '';
                                if (cIdx === '' && cPct != null && genPopPct > 0) {
                                    cIdx = Math.round((cPct / genPopPct) * 100);
                                }
                                _pageBehCohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 2) + '%');
                                _pageBehCohortCells += ',' + (cProj == null ? '' : cProj);
                                _pageBehCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                            });
                            csvContent += `"${item.name}",${audiencePct}%,${audienceNumbers},${_fmtPctTrim(genPopPct, 2)}%,${genPopNumbers},${index}${_pageBehCohortCells}\\n`;
                        });
                        csvContent += `\\n`;
                    }
                });"""


# ---------------------------------------------------------------------------
# 4) exportToCSV Interests -- trim trailing zeros
# ---------------------------------------------------------------------------
ETC_INT_OLD = """                Object.entries(interests).forEach(([name, vals]) => {
                    const pctNum = (vals.pct != null ? Number(vals.pct) : 0);
                    const pct = pctNum.toFixed(2);
                    const gpPctNum = (vals.genPopPct != null ? Number(vals.genPopPct) : 0);
                    const genPopPct = gpPctNum.toFixed(2);
                    const index = vals.index != null ? vals.index : 0;
                    let _pageIntCohortCells = '';
                    _pageIntCohorts.forEach(function (c) {
                        const ii = _cohortInterestItem(c, name);
                        const cPct = (ii && ii.pct != null) ? Number(ii.pct) : null;
                        let cIdx = (ii && ii.index != null && isFinite(ii.index)) ? Math.round(Number(ii.index)) : '';
                        if (cIdx === '' && cPct != null && gpPctNum > 0) {
                            cIdx = Math.round((cPct / gpPctNum) * 100);
                        }
                        _pageIntCohortCells += ',' + (cPct == null ? '' : cPct.toFixed(2));
                        _pageIntCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `"${String(name).replace(/"/g, '""')}",${pct},${genPopPct},${index}${_pageIntCohortCells}\\n`;
                });"""

ETC_INT_NEW = """                Object.entries(interests).forEach(([name, vals]) => {
                    const pctNum = (vals.pct != null ? Number(vals.pct) : 0);
                    const pct = _fmtPctTrim(pctNum, 2);
                    const gpPctNum = (vals.genPopPct != null ? Number(vals.genPopPct) : 0);
                    const genPopPct = _fmtPctTrim(gpPctNum, 2);
                    const index = vals.index != null ? vals.index : 0;
                    let _pageIntCohortCells = '';
                    _pageIntCohorts.forEach(function (c) {
                        const ii = _cohortInterestItem(c, name);
                        const cPct = (ii && ii.pct != null) ? Number(ii.pct) : null;
                        let cIdx = (ii && ii.index != null && isFinite(ii.index)) ? Math.round(Number(ii.index)) : '';
                        if (cIdx === '' && cPct != null && gpPctNum > 0) {
                            cIdx = Math.round((cPct / gpPctNum) * 100);
                        }
                        _pageIntCohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 2));
                        _pageIntCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `"${String(name).replace(/"/g, '""')}",${pct},${genPopPct},${index}${_pageIntCohortCells}\\n`;
                });"""


# ---------------------------------------------------------------------------
# 5) exportToCSV Location -- trim trailing zeros (was toFixed(4))
# ---------------------------------------------------------------------------
ETC_LOC_OLD = """                sorted.forEach(loc => {
                    const rank = getDMARank(loc.name) || '';
                    const gpPctNum = (loc.genPopPct != null) ? Number(loc.genPopPct) : 0;
                    let _pageLocCohortCells = '';
                    _pageLocCohorts.forEach(function (c) {
                        const li = _cohortLocationItem(c, loc.name);
                        const cPct = (li && li.pct != null) ? Number(li.pct) : null;
                        let cIdx = (li && li.index != null && isFinite(li.index)) ? Math.round(Number(li.index)) : '';
                        if (cIdx === '' && cPct != null && gpPctNum > 0) {
                            cIdx = Math.round((cPct / gpPctNum) * 100);
                        }
                        _pageLocCohortCells += ',' + (cPct == null ? '' : cPct.toFixed(4) + '%');
                        _pageLocCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `${rank},"${normalizeDmaDisplay(loc.name)}",${(loc.pct || 0).toFixed(4)}%,${(loc.genPopPct || 0).toFixed(4)}%,${loc.index || 0}${_pageLocCohortCells}\\n`;
                });"""

ETC_LOC_NEW = """                sorted.forEach(loc => {
                    const rank = getDMARank(loc.name) || '';
                    const gpPctNum = (loc.genPopPct != null) ? Number(loc.genPopPct) : 0;
                    let _pageLocCohortCells = '';
                    _pageLocCohorts.forEach(function (c) {
                        const li = _cohortLocationItem(c, loc.name);
                        const cPct = (li && li.pct != null) ? Number(li.pct) : null;
                        let cIdx = (li && li.index != null && isFinite(li.index)) ? Math.round(Number(li.index)) : '';
                        if (cIdx === '' && cPct != null && gpPctNum > 0) {
                            cIdx = Math.round((cPct / gpPctNum) * 100);
                        }
                        _pageLocCohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 4) + '%');
                        _pageLocCohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `${rank},"${normalizeDmaDisplay(loc.name)}",${_fmtPctTrim(loc.pct || 0, 4)}%,${_fmtPctTrim(loc.genPopPct || 0, 4)}%,${loc.index || 0}${_pageLocCohortCells}\\n`;
                });"""


# ---------------------------------------------------------------------------
# 6) exportAllTabsCSV Demographics -- trim
# ---------------------------------------------------------------------------
EAT_DEMO_OLD = """                Object.keys(data).forEach(label => {
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
                });"""

EAT_DEMO_NEW = """                Object.keys(data).forEach(label => {
                    const valNum = parseFloat(data[label]) || 0;
                    const val = _fmtPctTrim(valNum, 2);
                    const gpNum = parseFloat(genPop[label]) || 0;
                    const gp = _fmtPctTrim(gpNum, 2);
                    const idx = Math.round(parseFloat(index[label]) || 100);
                    let cohortCells = '';
                    _allCohorts.forEach(function (c) {
                        const cPct = _cohortDemoPct(c, catKey, label);
                        const cIdx = _cohortDemoIndex(c, catKey, label, gpNum);
                        cohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 2) + '%');
                        cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `"${labelFn(label)}",${val}%,${gp}%,${idx}${cohortCells}\\n`;
                });"""


# ---------------------------------------------------------------------------
# 7) exportAllTabsCSV Behavioral -- Gen Pop fix + trim
# ---------------------------------------------------------------------------
EAT_BEH_OLD = """            Object.keys(behavioral).forEach(category => {
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
            });"""

EAT_BEH_NEW = """            // 2026-09-08 (Jenna): read Gen Pop from the authoritative
            // genPopBehavioral map. item.genPopPct is empty on most
            // profiles' behavioral items, which shipped Gen Pop = 0
            // across every row until this fix.
            const _eatGpBehavioral = currentDashboardData.genPopBehavioral || {};
            Object.keys(behavioral).forEach(category => {
                const items = behavioral[category] || [];
                if (items.length === 0) return;
                const _eatGpCatMap = _eatGpBehavioral[category]
                    || _eatGpBehavioral[String(category).toUpperCase()]
                    || {};
                csvContent += `${category.toUpperCase()}\\n`;
                csvContent += `Name,Audience %,Audience Numbers,Gen Pop %,Gen Pop Numbers,Index${_allBehCohortHead}\\n`;
                items.forEach(item => {
                    const audiencePct = _fmtPctTrim(item.pct || 0, 2);
                    const audienceNumbers = (item.projection != null && item.projection !== '')
                        ? Math.round(Number(item.projection))
                        : Math.round(((item.pct || 0) / 100) * US_POP);
                    const _gpFromMap = parseFloat(_eatGpCatMap[item.name]);
                    const genPopPct = isFinite(_gpFromMap) ? _gpFromMap : (parseFloat(item.genPopPct) || 0);
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
                        cohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 2) + '%');
                        cohortCells += ',' + (cProj == null ? '' : cProj);
                        cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                    });
                    csvContent += `"${item.name}",${audiencePct}%,${audienceNumbers},${_fmtPctTrim(genPopPct, 2)}%,${genPopNumbers},${index}${cohortCells}\\n`;
                });
                csvContent += `\\n`;
            });"""


# ---------------------------------------------------------------------------
# 8) exportAllTabsCSV Interests -- trim
# ---------------------------------------------------------------------------
EAT_INT_OLD = """            Object.entries(interests).forEach(([name, vals]) => {
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
            });"""

EAT_INT_NEW = """            Object.entries(interests).forEach(([name, vals]) => {
                const pctNum = (vals.pct != null ? Number(vals.pct) : 0);
                const pct = _fmtPctTrim(pctNum, 2);
                const gpPctNum = (vals.genPopPct != null ? Number(vals.genPopPct) : 0);
                const genPopPct = _fmtPctTrim(gpPctNum, 2);
                const index = vals.index != null ? vals.index : 0;
                let cohortCells = '';
                _allCohorts.forEach(function (c) {
                    const ii = _cohortInterestItem(c, name);
                    const cPct = (ii && ii.pct != null) ? Number(ii.pct) : null;
                    let cIdx = (ii && ii.index != null && isFinite(ii.index)) ? Math.round(Number(ii.index)) : '';
                    if (cIdx === '' && cPct != null && gpPctNum > 0) {
                        cIdx = Math.round((cPct / gpPctNum) * 100);
                    }
                    cohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 2));
                    cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                });
                csvContent += `"${String(name).replace(/"/g, '""')}",${pct},${genPopPct},${index}${cohortCells}\\n`;
            });"""


# ---------------------------------------------------------------------------
# 9) exportAllTabsCSV Location -- trim
# ---------------------------------------------------------------------------
EAT_LOC_OLD = """            locSorted.forEach(loc => {
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
            });"""

EAT_LOC_NEW = """            locSorted.forEach(loc => {
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
                    cohortCells += ',' + (cPct == null ? '' : _fmtPctTrim(cPct, 4) + '%');
                    cohortCells += ',' + (cIdx === '' ? '' : cIdx);
                });
                csvContent += `${rank},"${normalizeDmaDisplay(loc.name)}",${_fmtPctTrim(loc.pct || 0, 4)}%,${_fmtPctTrim(loc.genPopPct || 0, 4)}%,${loc.index || 0}${cohortCells}\\n`;
            });"""


def main() -> int:
    src = INDEX.read_text(encoding='utf-8')
    orig_bytes = len(src.encode('utf-8'))
    src, _ = splice(src, HELPER_OLD, HELPER_NEW, "insert _fmtPctTrim helper")
    src, _ = splice(src, ETC_DEMO_OLD, ETC_DEMO_NEW, "exportToCSV Demographics trim")
    src, _ = splice(src, ETC_BEH_OLD, ETC_BEH_NEW, "exportToCSV Behavioral Gen Pop fix + trim")
    src, _ = splice(src, ETC_INT_OLD, ETC_INT_NEW, "exportToCSV Interests trim")
    src, _ = splice(src, ETC_LOC_OLD, ETC_LOC_NEW, "exportToCSV Location trim")
    src, _ = splice(src, EAT_DEMO_OLD, EAT_DEMO_NEW, "exportAllTabsCSV Demographics trim")
    src, _ = splice(src, EAT_BEH_OLD, EAT_BEH_NEW, "exportAllTabsCSV Behavioral Gen Pop fix + trim")
    src, _ = splice(src, EAT_INT_OLD, EAT_INT_NEW, "exportAllTabsCSV Interests trim")
    src, _ = splice(src, EAT_LOC_OLD, EAT_LOC_NEW, "exportAllTabsCSV Location trim")
    if src == INDEX.read_text(encoding='utf-8'):
        print("[skip] all splices already applied; no write")
        return 0
    INDEX.write_text(src, encoding='utf-8')
    new_bytes = len(src.encode('utf-8'))
    print(f"delta = {new_bytes - orig_bytes:+d} bytes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
