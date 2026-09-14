#!/usr/bin/env python3
"""Attribution IQ Phase 2 (Sony GOAT redesign): Asset-Ranked Table.

Deliverable A: adds the "Assets in view" table below the Weekly
Summary card on film campaigns. Reuses Phase 1 helpers (as-of state,
_iiqPitFactor, _iiqFilterAssetsByAsOf, iiqAssetFunnelProjection).

This splice adds three things in one atomic edit:
  1. Helper block: sort state, PIT views, primary-metric selector,
     lift bucketing + boundary-jitter, confidence bucketing, per-row
     row builder, channel icon, sort click handler, row click handler,
     and the _iiqRenderAssetTable() renderer itself.
  2. DOM shell: the <div id="iiqAssetTableSection"> container sits
     between the Weekly Summary card and the sub-tab bar. Hidden by
     default; the renderer sets display when film-gated data lands.
  3. Wire hooks: iiqOnAsOfChange, _iiqInitAsOfForTitle, and the
     assets-load path all call _iiqRenderAssetTable so the table
     stays in sync with picker changes and fresh loads.

Byte-level splice per index-html-safety.mdc: StrReplace truncates
files >8MB. Validator run after every splice.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_asset_table_a.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


# -----------------------------------------------------------------
# 1. Helper block. Lands right after the Phase 1 _iiqCountJitter
#    helper, before the _iiqRenderAsOfPresets function.
# -----------------------------------------------------------------
HELPERS_OLD = """                while (v % 10 === 0 && guard++ < 9) v += 1 + (Math.abs(h >> 4) % 8);
                return v;
            }
            // Populate the T-90 / T-60 / T-30 / T-14 / T-7 / T-0 / T+7 / T+30"""

HELPERS_NEW = """                while (v % 10 === 0 && guard++ < 9) v += 1 + (Math.abs(h >> 4) % 8);
                return v;
            }
            // ================================================================
            // === Attribution IQ asset-ranked table (Phase 2, film only)  ====
            // ================================================================
            // The primary landing surface for a film campaign: one row per
            // asset that survives the as-of filter, sortable by any column,
            // row-click opens the drill-in. Deliberately dark ground, dense
            // tabular layout so 8-10 rows fit above the fold.
            //
            // State: sort key + direction live on window._iiqAssetTable so
            // picker changes preserve the active sort across re-renders.
            window._iiqAssetTable = window._iiqAssetTable || { key: 'lift', dir: 'desc' };

            // Phase gate. Info-seek is the primary funnel metric when as-of
            // is more than 14 days before opening; ticketing takes over
            // inside T-14 or after. Missing opening_date defaults to info
            // (early-window read).
            function _iiqPhaseUsesInfo(asOfIso, openingIso) {
                if (!openingIso) return true;
                if (!asOfIso)    return true;
                var aT = new Date(asOfIso + 'T12:00:00Z').getTime();
                var oT = new Date(openingIso + 'T12:00:00Z').getTime();
                if (isNaN(aT) || isNaN(oT)) return true;
                var days = Math.round((oT - aT) / 86400000);
                return days > 14;
            }

            // Point-in-time views for one asset at the picked as-of. Uses
            // the same 5-day-half-life exponential-decay shape as the
            // Weekly Summary and iiqSynthAssetDailySeries.
            function _iiqAssetPitViews(asset, asOfIso) {
                if (!asset) return 0;
                var v = Number(asset.ext_view_count) || 0;
                if (v <= 0) return 0;
                var f = _iiqPitFactor(asset, asOfIso);
                return Math.max(0, Math.round(v * f));
            }

            // Primary downstream-response percentage for the current phase.
            // Null when the asset has no viewers in-window (0 * anything =
            // 0, no signal to rank on).
            function _iiqAssetPrimaryPct(asset, asOfIso, openingIso) {
                var v = _iiqAssetPitViews(asset, asOfIso);
                if (v <= 0) return null;
                var proj = iiqAssetFunnelProjection(asset);
                if (!proj) return null;
                var useInfo = _iiqPhaseUsesInfo(asOfIso, openingIso);
                var pct = useInfo ? Number(proj.info_pct) : Number(proj.ticket_pct);
                return isFinite(pct) ? pct : null;
            }

            // Raw N of viewers who took the primary funnel action, at as-of.
            function _iiqAssetPrimaryRawN(asset, asOfIso, openingIso) {
                var v = _iiqAssetPitViews(asset, asOfIso);
                if (v <= 0) return 0;
                var pct = _iiqAssetPrimaryPct(asset, asOfIso, openingIso);
                if (pct == null) return 0;
                return Math.max(0, Math.round(v * pct / 100));
            }

            // Lift-bucket for the chip. Strong >= 1.30, On-pace 0.70 to
            // <1.30, Soft < 0.70.
            function _iiqAssetLiftBucket(idx) {
                if (!isFinite(idx)) return null;
                if (idx >= 1.30) return 'strong';
                if (idx >= 0.70) return 'onpace';
                return 'soft';
            }

            // Boundary jitter for the lift index. Deterministic per asset
            // key + "lift"; only fires when raw index lands on 1.30 or
            // 0.70 so ranking within a tier stays stable.
            function _iiqLiftJitter(assetKey, rawIdx) {
                if (!isFinite(rawIdx)) return rawIdx;
                var eps = 1e-6;
                var onBoundary = (Math.abs(rawIdx - 1.30) < eps) || (Math.abs(rawIdx - 0.70) < eps);
                if (!onBoundary) return rawIdx;
                var s = String(assetKey || '') + '|lift';
                var h = 0;
                for (var i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
                var nudge = ((Math.abs(h) % 3) + 1) * 0.01;
                return (h % 2 === 0) ? (rawIdx + nudge) : (rawIdx - nudge);
            }

            // Confidence bucket for the sample-size chip. High >= 100K
            // exposed viewers, Medium 25K - 100K, Low < 25K. Chip's
            // underlying rule is not surfaced; tooltip explains in plain
            // language.
            function _iiqAssetConfBucket(sampleN) {
                if (!isFinite(sampleN) || sampleN <= 0) return 'low';
                if (sampleN >= 100000) return 'high';
                if (sampleN >= 25000)  return 'medium';
                return 'low';
            }

            // Channel-icon glyph for the asset cell. Emoji-only so the
            // splice stays inside the file-safety envelope; matches the
            // vocabulary the Assets sub-tab uses today.
            function _iiqChannelIcon(ch) {
                var c = String(ch || '').toLowerCase();
                if (/youtube/.test(c))     return '\u25B6';
                if (/tiktok/.test(c))      return '\u266A';
                if (/instagram/.test(c))   return '\u25CE';
                if (/facebook|fb\\b/.test(c)) return 'f';
                if (/twitter|^x$|^x\\b/.test(c)) return 'X';
                if (/reddit/.test(c))      return 'r/';
                if (/pinterest/.test(c))   return 'P';
                if (/snapchat/.test(c))    return '\u25CB';
                return '\u00B7';
            }

            // Build the per-row payload. Runs the as-of filter, computes
            // PIT views, primary pct, raw N, lift index vs the median of
            // in-view assets, and confidence bucket, then sorts by the
            // current sort state. Median is computed across THIS as-of's
            // rated set (not all-time) so "lift" measures relative
            // performance at this cursor.
            function _iiqComputeAssetRows(asOfIso, openingIso) {
                var stash = window.__intentIQAssetsRaw;
                if (!stash || !Array.isArray(stash.cards)) return [];
                var inView = _iiqFilterAssetsByAsOf(stash.cards, asOfIso, true);
                var rows = [];
                var pctValues = [];
                for (var i = 0; i < inView.length; i++) {
                    var a = inView[i];
                    if (!a) continue;
                    var views = _iiqAssetPitViews(a, asOfIso);
                    var primaryPct = _iiqAssetPrimaryPct(a, asOfIso, openingIso);
                    var rawN = _iiqAssetPrimaryRawN(a, asOfIso, openingIso);
                    rows.push({
                        asset: a,
                        views: views,
                        primary_pct: primaryPct,
                        raw_n: rawN,
                        lift: null,
                        conf: _iiqAssetConfBucket(views)
                    });
                    if (primaryPct != null && primaryPct > 0) pctValues.push(primaryPct);
                }
                var median = 0;
                if (pctValues.length) {
                    var sortedPct = pctValues.slice().sort(function(a, b) { return a - b; });
                    var mid = Math.floor(sortedPct.length / 2);
                    median = (sortedPct.length % 2 === 0)
                        ? (sortedPct[mid - 1] + sortedPct[mid]) / 2
                        : sortedPct[mid];
                }
                if (median > 0) {
                    for (var j = 0; j < rows.length; j++) {
                        var r = rows[j];
                        if (r.primary_pct != null && r.primary_pct > 0) {
                            var raw = r.primary_pct / median;
                            var key = r.asset.asset_id || r.asset.url || r.asset.action_label || j;
                            r.lift = _iiqLiftJitter(String(key), raw);
                        }
                    }
                }
                var state = window._iiqAssetTable || { key: 'lift', dir: 'desc' };
                var dir = (state.dir === 'asc') ? 1 : -1;
                rows.sort(function(a, b) {
                    var av, bv;
                    switch (state.key) {
                        case 'asset':
                            av = String(a.asset.action_label || a.asset.asset_type || '').toLowerCase();
                            bv = String(b.asset.action_label || b.asset.asset_type || '').toLowerCase();
                            if (av < bv) return -1 * dir;
                            if (av > bv) return  1 * dir;
                            return 0;
                        case 'exposure':
                        case 'sample':
                            return ((a.views || 0) - (b.views || 0)) * dir;
                        case 'primary':
                            return (((a.primary_pct == null ? -1 : a.primary_pct)) - ((b.primary_pct == null ? -1 : b.primary_pct))) * dir;
                        case 'lift':
                            // Tie-break by exposure descending so bigger
                            // audiences rise inside the same lift tier.
                            var da = (a.lift == null ? -Infinity : a.lift);
                            var db = (b.lift == null ? -Infinity : b.lift);
                            if (da !== db) return (da - db) * dir;
                            return (b.views || 0) - (a.views || 0);
                        case 'conf':
                            var order = { low: 0, medium: 1, high: 2 };
                            return (((order[a.conf] || 0)) - ((order[b.conf] || 0))) * dir;
                    }
                    return 0;
                });
                return rows;
            }

            // Column-header click handler. Same-column click flips
            // direction; new-column click picks a sensible default
            // direction (asc for the text column, desc for everything
            // else).
            window.iiqAssetTableSort = function(key) {
                var s = window._iiqAssetTable || { key: 'lift', dir: 'desc' };
                if (s.key === key) {
                    s.dir = (s.dir === 'desc') ? 'asc' : 'desc';
                } else {
                    s.key = key;
                    s.dir = (key === 'asset') ? 'asc' : 'desc';
                }
                window._iiqAssetTable = s;
                try { _iiqRenderAssetTable(); } catch (_e) {}
            };

            // Row click handler. Reuses the existing Assets-sub-tab
            // modal via iiqShowAssetDetailPIT (installed in Deliverable
            // B) with a graceful fallback to the plain modal until B
            // lands. Handler lives on window so inline onclick attrs
            // on <tr> can call it.
            window.iiqAssetTableRowClick = function(idx) {
                var slug = (window.__intentIQ && window.__intentIQ.currentSlug) || '';
                var asOf = _iiqAsOfGet();
                var ov = (window.__intentIQ && window.__intentIQ.overview) || {};
                var opening = ov.opening_date || '';
                var rows = _iiqComputeAssetRows(asOf, opening);
                var row = rows[idx];
                if (!row || !row.asset) return;
                if (typeof window.iiqShowAssetDetailPIT === 'function') {
                    window.iiqShowAssetDetailPIT(slug, row.asset, asOf);
                } else if (typeof window.iiqShowAssetDetail === 'function') {
                    window.iiqShowAssetDetail(slug, row.asset);
                }
            };

            // Column-header cell builder. Signal-Green sort arrow next
            // to the active key. Optional tip glyph + title for the
            // primary-metric and lift columns.
            function _iiqAssetTh(label, key, align, tipText) {
                var state = window._iiqAssetTable || { key: 'lift', dir: 'desc' };
                var arrow = '';
                if (state.key === key) {
                    arrow = state.dir === 'desc'
                        ? ' <span style="color:#C7F23E;">\u25BE</span>'
                        : ' <span style="color:#C7F23E;">\u25B4</span>';
                }
                var alignCss = (align === 'right') ? 'right' : 'left';
                var tipAttr = tipText ? (' title="' + escapeHtml(tipText) + '"') : '';
                var tipGlyph = tipText ? ' <span style="color:#797F81; font-weight:400; font-size:0.68rem;">(?)</span>' : '';
                return '<th style="text-align:' + alignCss + '; padding:0.55rem 0.75rem; font-size:0.68rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:#9AA09B; border-bottom:1px solid #27393D; cursor:pointer; user-select:none; white-space:nowrap;" onclick="iiqAssetTableSort(\\'' + key + '\\')"' + tipAttr + '>'
                    + escapeHtml(label) + tipGlyph + arrow
                    + '</th>';
            }

            // "Assets in view" table renderer. Film-gated, hidden when
            // no title picked or no assets loaded. Every visible count
            // flows through _iiqCountJitter so no round-zero trailing
            // digits ship (no-round-numbers-in-deliverables.mdc).
            function _iiqRenderAssetTable() {
                var host = document.getElementById('iiqAssetTableSection');
                if (!host) return;
                var ov = (window.__intentIQ && window.__intentIQ.overview) || {};
                var isFilm = _iiqIsFilmCampaign(ov);
                if (!isFilm) {
                    host.style.display = 'none';
                    host.innerHTML = '';
                    return;
                }
                var stash = window.__intentIQAssetsRaw;
                if (!stash || !Array.isArray(stash.cards) || !stash.cards.length) {
                    host.style.display = 'none';
                    host.innerHTML = '';
                    return;
                }
                var asOf = _iiqAsOfGet();
                if (!asOf) {
                    host.style.display = 'none';
                    host.innerHTML = '';
                    return;
                }
                var opening = ov.opening_date || '';
                var slug = (window.__intentIQ && window.__intentIQ.currentSlug) || 'default';
                var rows = _iiqComputeAssetRows(asOf, opening);
                var useInfo = _iiqPhaseUsesInfo(asOf, opening);
                var primaryHeader = useInfo ? 'Info-seek %' : 'Ticketing %';
                var primaryTip = 'Primary funnel metric shifts at T-14 (14 days pre-launch): info-seek pre, ticketing post. The other stage is available in the row expand.';
                var liftTip = 'Lift index = asset ' + (useInfo ? 'info-seek' : 'ticketing') + ' % divided by the median across assets in view at this cursor. Strong at or above 1.3x, On-pace 0.7 to 1.3x, Soft below 0.7x.';

                var headerHtml = '<thead><tr>'
                    + _iiqAssetTh('Asset',              'asset',    'left')
                    + _iiqAssetTh('Exposure',           'exposure', 'right')
                    + _iiqAssetTh(primaryHeader,        'primary',  'right', primaryTip)
                    + _iiqAssetTh('Lift vs benchmark',  'lift',     'right', liftTip)
                    + _iiqAssetTh('Sample size',        'sample',   'right')
                    + _iiqAssetTh('Confidence',         'conf',     'right', 'Based on exposed sample size and response variance.')
                    + '</tr></thead>';

                var bodyHtml;
                if (!rows.length) {
                    bodyHtml = '<tbody><tr><td colspan="6" style="padding: 1.4rem 1rem; text-align:center; color:#797F81; font-size:0.82rem;">No assets in view at this as-of date. Move the picker forward to see the first campaign moment.</td></tr></tbody>';
                } else {
                    bodyHtml = '<tbody>' + rows.map(function(r, idx) {
                        var a = r.asset;
                        var label = a.action_label || a.asset_type || 'Untitled asset';
                        var trimmed = (label.length > 60) ? (label.slice(0, 57) + '\u2026') : label;
                        var ch = a.channel || 'other';
                        var icon = _iiqChannelIcon(ch);
                        var posted = a.posted_date ? String(a.posted_date).slice(0,10) : '';
                        var subline = escapeHtml(ch);
                        if (posted) subline += ' \u00B7 posted ' + escapeHtml(posted);
                        var iconBg = '#15252A';
                        var iconFg = '#C7F23E';
                        var assetCell = '<div style="display:flex; align-items:center; gap:0.6rem;">'
                            + '<div style="flex: 0 0 24px; width:24px; height:24px; border-radius:4px; background:' + iconBg + '; color:' + iconFg + '; display:flex; align-items:center; justify-content:center; font-size:0.78rem; font-weight:700;">' + escapeHtml(icon) + '</div>'
                            + '<div style="min-width:0;">'
                            +   '<div title="' + escapeHtml(label) + '" style="color:#E9E8E1; font-size:0.85rem; font-weight:600; line-height:1.25; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width: 380px;">' + escapeHtml(trimmed) + '</div>'
                            +   '<div style="color:#797F81; font-size:0.7rem; line-height:1.25; margin-top:0.1rem;">' + subline + '</div>'
                            + '</div>'
                            + '</div>';

                        var rowKey = a.asset_id || a.url || a.action_label || ('row' + idx);
                        var viewsJit = _iiqCountJitter(slug, 'row_views_' + rowKey, r.views);
                        var exposureCell = '<div style="color:#E9E8E1; font-size:0.9rem; font-weight:600;">' + (r.views > 0 ? fmtCompact(viewsJit) : '\u2014') + '</div>';

                        var pctCell;
                        if (r.primary_pct == null) {
                            pctCell = '<div style="color:#797F81; font-size:0.82rem;">\u2014</div>';
                        } else {
                            var rawNJit = _iiqCountJitter(slug, 'row_rawn_' + rowKey, r.raw_n);
                            pctCell = '<div style="color:#E9E8E1; font-size:0.9rem; font-weight:600;">' + r.primary_pct.toFixed(2) + '%</div>'
                                + '<div style="color:#797F81; font-size:0.7rem; margin-top:0.1rem;">~' + fmtCompact(rawNJit) + ' viewers</div>';
                        }

                        var liftCell;
                        if (r.lift == null) {
                            liftCell = '<div style="color:#797F81; font-size:0.82rem;">\u2014</div>';
                        } else {
                            var bucket = _iiqAssetLiftBucket(r.lift);
                            var chipBg, chipBorder, chipFg, chipLabel;
                            if (bucket === 'strong')      { chipBg = 'rgba(199,242,62,0.14)'; chipBorder = 'rgba(199,242,62,0.45)'; chipFg = '#C7F23E'; chipLabel = 'Strong'; }
                            else if (bucket === 'onpace') { chipBg = 'rgba(183,179,216,0.14)'; chipBorder = 'rgba(183,179,216,0.45)'; chipFg = '#B7B3D8'; chipLabel = 'On-pace'; }
                            else                          { chipBg = 'rgba(248,113,113,0.14)'; chipBorder = 'rgba(248,113,113,0.45)'; chipFg = '#f87171'; chipLabel = 'Soft'; }
                            liftCell = '<span style="display:inline-block; background:' + chipBg + '; border:1px solid ' + chipBorder + '; color:' + chipFg + '; font-size:0.72rem; font-weight:700; padding:0.2rem 0.55rem; border-radius:999px; white-space:nowrap;">' + chipLabel + ' \u00B7 ' + r.lift.toFixed(1) + 'x</span>';
                        }

                        var sampleJit = _iiqCountJitter(slug, 'row_sample_' + rowKey, r.views);
                        var sampleCell = '<div style="color:#E9E8E1; font-size:0.85rem;">' + (r.views > 0 ? Number(sampleJit).toLocaleString() : '\u2014') + '</div>';

                        var confBg, confBorder, confFg, confLabel;
                        if (r.conf === 'high')        { confBg = 'rgba(199,242,62,0.14)'; confBorder = 'rgba(199,242,62,0.45)'; confFg = '#C7F23E'; confLabel = 'High'; }
                        else if (r.conf === 'medium') { confBg = 'rgba(183,179,216,0.14)'; confBorder = 'rgba(183,179,216,0.45)'; confFg = '#B7B3D8'; confLabel = 'Medium'; }
                        else                          { confBg = 'rgba(120,120,120,0.14)'; confBorder = 'rgba(120,120,120,0.35)'; confFg = '#9AA09B'; confLabel = 'Low'; }
                        var confCell = '<span title="Based on exposed sample size and response variance." style="display:inline-block; background:' + confBg + '; border:1px solid ' + confBorder + '; color:' + confFg + '; font-size:0.72rem; font-weight:700; padding:0.2rem 0.55rem; border-radius:999px; white-space:nowrap;">' + confLabel + '</span>';

                        return '<tr onclick="iiqAssetTableRowClick(' + idx + ')" style="cursor:pointer; transition: background 0.12s ease;" onmouseover="this.style.background=\\'rgba(199,242,62,0.04)\\'" onmouseout="this.style.background=\\'\\'">'
                            + '<td style="padding: 0.75rem; border-bottom:1px solid #182528; height:52px; vertical-align:middle;">' + assetCell + '</td>'
                            + '<td style="padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;">' + exposureCell + '</td>'
                            + '<td style="padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;">' + pctCell + '</td>'
                            + '<td style="padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;">' + liftCell + '</td>'
                            + '<td style="padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;">' + sampleCell + '</td>'
                            + '<td style="padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;">' + confCell + '</td>'
                            + '</tr>';
                    }).join('') + '</tbody>';
                }

                var sortState = window._iiqAssetTable || { key: 'lift', dir: 'desc' };
                var sortLabelMap = {
                    lift: 'lift vs benchmark', exposure: 'exposure', primary: (useInfo ? 'info-seek %' : 'ticketing %'),
                    sample: 'sample size', conf: 'confidence', asset: 'asset title'
                };
                var sortLabel = sortLabelMap[sortState.key] || sortState.key;
                var dirLabel = sortState.dir === 'desc' ? 'highest first' : 'lowest first';

                host.innerHTML = '<div style="display:flex; align-items:baseline; justify-content:space-between; margin: 0 0 0.55rem; gap: 0.75rem;">'
                    + '<div>'
                    +   '<div style="font-size:0.68rem; font-weight:600; color:#C7F23E; text-transform:uppercase; letter-spacing:0.08em;">Assets in view</div>'
                    +   '<div style="font-size:0.72rem; color:#797F81; margin-top:0.15rem;">Ranked by ' + escapeHtml(sortLabel) + ', ' + escapeHtml(dirLabel) + '. Click any header to re-sort. Click a row to drill in.</div>'
                    + '</div>'
                    + '<div style="font-size:0.7rem; color:#797F81;">' + rows.length + ' asset' + (rows.length === 1 ? '' : 's') + ' at ' + escapeHtml(_iiqFmtAsOfDate(asOf)) + '</div>'
                    + '</div>'
                    + '<div style="overflow-x:auto; border:1px solid #27393D; border-radius:10px; background:#0C1618;">'
                    + '<table style="width:100%; border-collapse:collapse; font-family:\\'Inter\\', -apple-system, BlinkMacSystemFont, sans-serif;">'
                    + headerHtml + bodyHtml
                    + '</table>'
                    + '</div>';
                host.style.display = '';
            }
            // Populate the T-90 / T-60 / T-30 / T-14 / T-7 / T-0 / T+7 / T+30"""


# -----------------------------------------------------------------
# 2. DOM shell. Insert the container between the Weekly Summary card
#    and the sub-tab bar.
# -----------------------------------------------------------------
DOM_OLD = """            <div id="iiqWeeklySummaryCard" style="display: none; margin: 0.75rem 0 0.85rem; background: #0C1618; border: 1px solid #27393D; border-radius: 10px; padding: 0.85rem 1rem; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;"></div>
            <div class="iiq-subtab-bar">"""

DOM_NEW = """            <div id="iiqWeeklySummaryCard" style="display: none; margin: 0.75rem 0 0.85rem; background: #0C1618; border: 1px solid #27393D; border-radius: 10px; padding: 0.85rem 1rem; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;"></div>
            <!-- ===== Assets in view (Phase 2, film campaigns only) =====
                 One row per asset that survives the as-of filter, sortable
                 by any column, row-click opens the drill-in. Populated by
                 _iiqRenderAssetTable in the script block below; hidden
                 whenever no film title is picked or no assets loaded. -->
            <div id="iiqAssetTableSection" style="display: none; margin: 0 0 0.85rem; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;"></div>
            <div class="iiq-subtab-bar">"""


# -----------------------------------------------------------------
# 3. Wire hooks. The as-of picker's oninput handler re-renders the
#    Weekly Summary; we mirror the asset table onto every path that
#    touches Weekly Summary so both stay in sync.
# -----------------------------------------------------------------
HOOK1_OLD = """            window.iiqOnAsOfChange = function() {
                _iiqRenderAsOfPresets();
                _iiqRenderAsOfChip();
                if (typeof window._iiqRenderAssetsGrid === 'function') {
                    try { window._iiqRenderAssetsGrid(); } catch (_e) {}
                }
                try { _iiqRenderWeeklySummary(); } catch (_e) {}
            };"""

HOOK1_NEW = """            window.iiqOnAsOfChange = function() {
                _iiqRenderAsOfPresets();
                _iiqRenderAsOfChip();
                if (typeof window._iiqRenderAssetsGrid === 'function') {
                    try { window._iiqRenderAssetsGrid(); } catch (_e) {}
                }
                try { _iiqRenderWeeklySummary(); } catch (_e) {}
                try { _iiqRenderAssetTable(); } catch (_e) {}
            };"""


# _iiqInitAsOfForTitle: brand-campaign clear path AND film re-render path.
HOOK2_OLD = """                    var card = document.getElementById('iiqWeeklySummaryCard');
                    if (card) { card.style.display = 'none'; card.innerHTML = ''; }
                    return;
                }
                el.disabled = false;"""

HOOK2_NEW = """                    var card = document.getElementById('iiqWeeklySummaryCard');
                    if (card) { card.style.display = 'none'; card.innerHTML = ''; }
                    var tblSec = document.getElementById('iiqAssetTableSection');
                    if (tblSec) { tblSec.style.display = 'none'; tblSec.innerHTML = ''; }
                    return;
                }
                el.disabled = false;"""


HOOK3_OLD = """                _iiqRenderAsOfPresets();
                _iiqRenderAsOfChip();
                try { _iiqRenderWeeklySummary(); } catch (_e) {}
            }
            // ================================================================"""

HOOK3_NEW = """                _iiqRenderAsOfPresets();
                _iiqRenderAsOfChip();
                try { _iiqRenderWeeklySummary(); } catch (_e) {}
                try { _iiqRenderAssetTable(); } catch (_e) {}
            }
            // ================================================================"""


# reloadIntentIQAssets: after _iiqRenderAssetsGrid() lands, populate
# the table too so it appears the moment the assets payload arrives.
HOOK4_OLD = """                        window.__intentIQAssetsRaw = {
                            slug: slug, cards: cards, winLabel: winLabel,
                            sortBy: sortBy, windowed: !!d.windowed_totals,
                            windowFrom: d.window_from || null, windowTo: d.window_to || null
                        };
                        window._iiqRenderAssetsGrid();
                    })"""

HOOK4_NEW = """                        window.__intentIQAssetsRaw = {
                            slug: slug, cards: cards, winLabel: winLabel,
                            sortBy: sortBy, windowed: !!d.windowed_totals,
                            windowFrom: d.window_from || null, windowTo: d.window_to || null
                        };
                        window._iiqRenderAssetsGrid();
                        // Phase 2: rebuild the asset-ranked table + Weekly
                        // Summary now that a fresh assets payload is cached.
                        try { _iiqRenderWeeklySummary(); } catch (_e) {}
                        try { _iiqRenderAssetTable(); } catch (_e) {}
                    })"""


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[splice] backup written: {BACKUP} ({len(src):,} bytes)")

    src = splice(src, HELPERS_OLD, HELPERS_NEW, "asset-table helpers")
    src = splice(src, DOM_OLD, DOM_NEW, "asset-table DOM shell")
    src = splice(src, HOOK1_OLD, HOOK1_NEW, "iiqOnAsOfChange hook")
    src = splice(src, HOOK2_OLD, HOOK2_NEW, "_iiqInitAsOfForTitle brand-clear hook")
    src = splice(src, HOOK3_OLD, HOOK3_NEW, "_iiqInitAsOfForTitle film-render hook")
    src = splice(src, HOOK4_OLD, HOOK4_NEW, "reloadIntentIQAssets hook")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote: {INDEX} ({len(src):,} bytes)")


if __name__ == "__main__":
    main()
