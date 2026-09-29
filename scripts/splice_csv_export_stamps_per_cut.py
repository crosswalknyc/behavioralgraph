#!/usr/bin/env python3
"""Stamp in-page CSV exports with DATE DOWNLOADED + per-cut STUDY DATE RANGE.

Profile IQ exports (tab exports, All Data, and every Compare export) now
open with a DATE DOWNLOADED row and one STUDY DATE RANGE row per included
profile/cut. A single-profile export keeps the plain unlabeled row so it
matches the server-side download stamp. Jenna 2026-09-28: "if there are
multiple cuts included it needs to include the date range of study for
each cut."
"""
import sys
from pathlib import Path

INDEX = Path(sys.argv[1] if len(sys.argv) > 1 else "templates/index.html")
BACKUP = Path("/tmp/index.pre_csv_export_stamps.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x (need exactly 1)")
    return src.replace(old, new)


src = INDEX.read_text(encoding="utf-8")
BACKUP.write_text(src, encoding="utf-8")

# 1. Shared helpers, inserted right after _csvSafeHeader (same script block
#    as the export functions that call them).
OLD_HELPER = """        function _csvSafeHeader(label, suffix) {
            var base = String(label || 'Cut')
                .replace(/[\\r\\n]+/g, ' ')
                .replace(/"/g, '""')
                .trim();
            return '"' + base + (suffix ? ' ' + suffix : '') + '"';
        }"""
NEW_HELPER = OLD_HELPER + """

        // ---- CSV download stamp (DATE DOWNLOADED + STUDY DATE RANGE) ----
        var _CW_STAMP_MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];

        function _cwFmtStampRange(rangeStr) {
            var s = String(rangeStr || '').trim();
            if (!s) return '';
            try {
                return s.replace(/\\d{4}-\\d{2}-\\d{2}/g, function (iso) {
                    var m = iso.match(/^(\\d{4})-(\\d{2})-(\\d{2})$/);
                    if (!m) return iso;
                    var mo = parseInt(m[2], 10), dd = parseInt(m[3], 10);
                    if (!mo || mo > 12 || !dd) return iso;
                    return _CW_STAMP_MONTHS[mo - 1] + ' ' + dd + ', ' + m[1];
                });
            } catch (e) { return s; }
        }

        function _cwCsvDownloadStamp(ranges) {
            // Header rows prepended to every exported CSV. `ranges` is an
            // array of {label, range}. One entry emits the plain
            // STUDY DATE RANGE row (same shape as server downloads); two or
            // more entries (profile + cuts) each get a labeled row.
            try {
                var now = new Date();
                var today = _CW_STAMP_MONTHS[now.getMonth()] + ' ' + now.getDate() + ', ' + now.getFullYear();
                var q = function (v) { return '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"'; };
                var lines = ['DATE DOWNLOADED,' + q(today)];
                var rs = (ranges || []).filter(function (r) { return r; });
                var withRange = rs.filter(function (r) { return String(r.range || '').trim(); });
                if (!withRange.length) {
                    lines.push('STUDY DATE RANGE,' + q('Not stated in file'));
                } else if (rs.length === 1) {
                    lines.push('STUDY DATE RANGE,' + q(_cwFmtStampRange(rs[0].range)));
                } else {
                    rs.forEach(function (r) {
                        var rng = String((r && r.range) || '').trim();
                        var lbl = String((r && r.label) || 'Cut').replace(/[\\r\\n]+/g, ' ').trim() || 'Cut';
                        lines.push(q('STUDY DATE RANGE (' + lbl + ')') + ',' + q(rng ? _cwFmtStampRange(rng) : 'Not stated in file'));
                    });
                }
                return lines.join('\\n') + '\\n\\n';
            } catch (e) { return ''; }
        }

        function _cwPageStampRanges(baseLabel) {
            // Ranges for the CURRENT Profile IQ page: the loaded profile
            // plus every cut pinned via Compare Runs.
            var out = [];
            try {
                var d = (typeof currentDashboardData !== 'undefined' && currentDashboardData)
                    ? currentDashboardData : (window.currentDashboardData || null);
                var lbl = baseLabel || '';
                if (!lbl) { try { lbl = (typeof currentProfileData !== 'undefined' && currentProfileData && currentProfileData.name) || ''; } catch (e0) {} }
                if (!lbl) { try { lbl = (typeof _baseCohortLabel === 'function' && _baseCohortLabel()) || ''; } catch (e1) {} }
                out.push({ label: lbl || 'Profile', range: (d && d.dateRange) || '' });
                var cuts = (typeof _extraCohortsForCsv === 'function') ? (_extraCohortsForCsv() || []) : [];
                cuts.forEach(function (c) {
                    if (!c) return;
                    out.push({ label: c.label || 'Cut', range: c.dateRange || (c.parsed && c.parsed.dateRange) || '' });
                });
            } catch (e) {}
            return out;
        }"""
src = splice(src, OLD_HELPER, NEW_HELPER, "stamp helpers")

# 2. _getVisibleCohorts: carry each cohort's study window through so the
#    export stamp can read it (label/parsed alone dropped it).
src = splice(
    src,
    "label: (typeof _baseCohortLabel === 'function' ? _baseCohortLabel() : 'Total Universe'),\n                    parsed: originalData",
    "label: (typeof _baseCohortLabel === 'function' ? _baseCohortLabel() : 'Total Universe'),\n                    dateRange: (originalData && originalData.dateRange) || '',\n                    parsed: originalData",
    "visible cohorts TU dateRange",
)
src = splice(
    src,
    "label: run.label || 'Run',\n                    parsed: run.parsed",
    "label: run.label || 'Run',\n                    dateRange: run.dateRange || (run.parsed && run.parsed.dateRange) || '',\n                    parsed: run.parsed",
    "visible cohorts run dateRange",
)

# 3. exportToCSV (per-tab export with cohort columns).
src = splice(
    src,
    """            // Download the CSV
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename.replace(/[^a-zA-Z0-9_.-]/g, '_');""",
    """            // Download the CSV
            csvContent = _cwCsvDownloadStamp(_cwPageStampRanges(profileName)) + csvContent;
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filename.replace(/[^a-zA-Z0-9_.-]/g, '_');""",
    "exportToCSV stamp",
)

# 4. exportAllTabsCSV (All Data export with cohort columns).
src = splice(
    src,
    """            // Download
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            const safeName = String(profileName).replace(/[^a-zA-Z0-9_.-]/g, '_');
            link.download = `${safeName}_All_Data.csv`;""",
    """            // Download
            csvContent = _cwCsvDownloadStamp(_cwPageStampRanges(profileName)) + csvContent;
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            const safeName = String(profileName).replace(/[^a-zA-Z0-9_.-]/g, '_');
            link.download = `${safeName}_All_Data.csv`;""",
    "exportAllTabsCSV stamp",
)

# 5. Compare load loop, fetch branch: keep each profile's window.
src = splice(
    src,
    """behavioralProjection: parsed.behavioralProjection,
                            locations: parsed.locations
                        };""",
    """behavioralProjection: parsed.behavioralProjection,
                            locations: parsed.locations,
                            dateRange: data.date_range || ''
                        };""",
    "compare fetch dateRange",
)

# 6. Compare load loop, open-tab branch.
src = splice(
    src,
    """behavioralProjection: {},
                        locations: p.locations || []
                    };""",
    """behavioralProjection: {},
                        locations: p.locations || [],
                        dateRange: openTab.dateRange || p.dateRange || ''
                    };""",
    "compare open-tab dateRange",
)

_CMP_STAMP = "_cwCsvDownloadStamp((profiles || []).map(function (p) { return { label: p.name, range: p.dateRange || '' }; }))"

# 7. exportComparisonSectionsAsCSV.
src = splice(
    src,
    """            const blob = new Blob([fullCsv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `comparison_selected_${new Date().toISOString().split('T')[0]}.csv`;""",
    """            fullCsv = """ + _CMP_STAMP + """ + fullCsv;
            const blob = new Blob([fullCsv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `comparison_selected_${new Date().toISOString().split('T')[0]}.csv`;""",
    "comparison selected stamp",
)

# 8. exportComparisonAllCSV.
src = splice(
    src,
    """            const blob = new Blob([fullCsv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `comparison_all_${new Date().toISOString().split('T')[0]}.csv`;""",
    """            fullCsv = """ + _CMP_STAMP + """ + fullCsv;
            const blob = new Blob([fullCsv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `comparison_all_${new Date().toISOString().split('T')[0]}.csv`;""",
    "comparison all stamp",
)

# 9. Per-module comparison export.
src = splice(
    src,
    """            // Download the CSV
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `comparison_${moduleType}_${new Date().toISOString().split('T')[0]}.csv`;""",
    """            // Download the CSV
            csvContent = """ + _CMP_STAMP + """ + csvContent;
            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `comparison_${moduleType}_${new Date().toISOString().split('T')[0]}.csv`;""",
    "comparison module stamp",
)

# 10. Gap analysis export.
src = splice(
    src,
    """            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `gap_analysis_${primaryProfile.name.replace(/[^a-zA-Z0-9]/g, '_')}.csv`;""",
    """            csv = """ + _CMP_STAMP + """ + csv;
            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `gap_analysis_${primaryProfile.name.replace(/[^a-zA-Z0-9]/g, '_')}.csv`;""",
    "gap analysis stamp",
)

# 11. Benchmark export (aggregates; ranges come off comparisonProfilesData).
src = splice(
    src,
    """            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'benchmark_comparison.csv';""",
    """            csv = _cwCsvDownloadStamp((window.comparisonProfilesData || []).map(function (p) { return { label: p.name, range: p.dateRange || '' }; })) + csv;
            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'benchmark_comparison.csv';""",
    "benchmark stamp",
)

INDEX.write_text(src, encoding="utf-8")
print("csv export stamps spliced (11 changes)")
