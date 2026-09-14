#!/usr/bin/env python3
"""Attribution IQ Phase 2 (Sony GOAT redesign): drill-in modal.

Deliverable B: adds iiqShowAssetDetailPIT, the point-in-time drill-in
that fires when a row in the "Assets in view" table is clicked.
Reuses the existing iiqAssetModal overlay container so the open/close
UX matches the rest of the campaign view.

Content:
  * Header: title + full URL (opens the real platform) + channel +
    posted_date + phase.
  * Metric strip: Exposure, Info-seek % (raw N), Ticketing % (raw N),
    Engagement (raw N), Engagement rate. All computed at as-of via
    _iiqPitFactor so the numbers agree with the row that opened the
    modal.
  * Daily curve: iiqSynthAssetDailySeries truncated at as-of; renders
    on iiqLineChartDual with views on the left axis and the phase-
    appropriate primary funnel metric on the right axis.
  * Close (X in top-right); overlay-click also dismisses.

Byte-level splice per index-html-safety.mdc.
"""
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_asset_table_b.html")


def splice(src, old, new, desc):
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{desc}] anchor NOT FOUND")
    if count > 1:
        raise RuntimeError(f"[{desc}] anchor found {count}x")
    return src.replace(old, new)


# Anchor: right after the existing iiqShowAssetDetail function closes,
# right before the Q3 signal-breakdown block.
OLD = """                        if (chartHost) chartHost.innerHTML = '<div class="iiq-empty">Failed: ' + escapeHtml(e.message || e) + '</div>';
                    });
            };

            // ===== Per-comp estimated signal breakdown (Q3) ====="""

NEW = """                        if (chartHost) chartHost.innerHTML = '<div class="iiq-empty">Failed: ' + escapeHtml(e.message || e) + '</div>';
                    });
            };

            // ================================================================
            // === iiqShowAssetDetailPIT: point-in-time drill-in modal    =====
            // === Deliverable B (Phase 2). Reuses iiqAssetModal overlay  =====
            // === with metrics + chart computed at the current as-of.    =====
            // ================================================================
            window.iiqShowAssetDetailPIT = function(slug, asset, asOfIso) {
                if (!asset) return;
                var asOf = asOfIso || (typeof _iiqAsOfGet === 'function' ? _iiqAsOfGet() : '');
                var ov = (window.__intentIQ && window.__intentIQ.overview) || {};
                var opening = ov.opening_date || '';
                var useInfo = _iiqPhaseUsesInfo(asOf, opening);

                var overlay = document.getElementById('iiqAssetModal');
                if (!overlay) {
                    overlay = document.createElement('div');
                    overlay.id = 'iiqAssetModal';
                    overlay.style.cssText = 'position:fixed; inset:0; background: rgba(0,0,0,0.7); z-index:9999; display:flex; align-items:center; justify-content:center; padding:2rem;';
                    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
                    document.body.appendChild(overlay);
                } else {
                    overlay.innerHTML = '';
                    overlay.style.display = 'flex';
                }

                // ===== PIT-adjusted metrics at as-of =====
                var proj = iiqAssetFunnelProjection(asset) || { info_pct: 0, ticket_pct: 0 };
                var lifetimeViews = Number(asset.ext_view_count) || Number(asset.views_total) || 0;
                var lifetimeEng   = Number(asset.ext_engagement_count) || Number(asset.engagement_total) || 0;
                var pitFactor = _iiqPitFactor(asset, asOf);
                var viewsAtAsOf = Math.max(0, Math.round(lifetimeViews * pitFactor));
                var engAtAsOf   = Math.max(0, Math.round(lifetimeEng   * pitFactor));
                var infoCount   = Math.max(0, Math.round(viewsAtAsOf * proj.info_pct   / 100));
                var tkCount     = Math.max(0, Math.round(viewsAtAsOf * proj.ticket_pct / 100));
                var engRate     = viewsAtAsOf > 0 ? (engAtAsOf / viewsAtAsOf * 100) : 0;

                var slugSalt = String(slug || '') + '|' + String(asset.asset_id || asset.url || asset.action_label || 'asset');
                function _pitJit(bucket, base) {
                    if (typeof _iiqCountJitter === 'function') return _iiqCountJitter(slugSalt, bucket, base);
                    return base;
                }

                // ===== Header + metric strip =====
                var label = asset.action_label || asset.asset_type || 'Asset #' + (asset.asset_id || '?');
                var color = (typeof IIQ_QPALETTE !== 'undefined')
                    ? (IIQ_QPALETTE[asset.paid_or_organic] || IIQ_QPALETTE.unknown)
                    : '#94a3b8';
                var urlHtml = asset.url
                    ? '<a href="' + escapeHtml(asset.url) + '" target="_blank" rel="noopener" style="color:#22d3ee; text-decoration:none; word-break:break-all;">' + escapeHtml(asset.url) + '</a>'
                    : '<span style="color:#797F81;">no URL on record</span>';
                var phaseTxt = asset.phase_name ? escapeHtml(asset.phase_name) : '\u2014';
                var postedTxt = asset.posted_date ? escapeHtml(String(asset.posted_date).slice(0,10)) : '\u2014';
                var channelTxt = escapeHtml(asset.channel || '\u2014');

                var infoTileTip = 'Info-seek % is the share of viewers within a 7-day post-view window who searched the title or hit an info page (IMDb / RT / Letterboxd). Computed at ' + escapeHtml(_iiqFmtAsOfDate(asOf)) + '.';
                var tkTileTip = 'Ticketing % is the share of viewers within a 7-day post-view window who visited a ticketing site (Fandango / AMC / Regal / Cinemark / Atom). Computed at ' + escapeHtml(_iiqFmtAsOfDate(asOf)) + '.';
                var primaryFlag = ' <span style="font-size:0.62rem; color:#C7F23E; font-weight:600; text-transform:uppercase; letter-spacing:0.06em;">primary</span>';
                var infoLabelHtml = 'Info-seek %' + (useInfo ? primaryFlag : '');
                var tkLabelHtml   = 'Ticketing %' + (useInfo ? '' : primaryFlag);

                function tile(labelHtml, valueTxt, subTxt, tipText) {
                    var tipAttr = tipText ? (' title="' + escapeHtml(tipText) + '"') : '';
                    return '<div' + tipAttr + ' style="background:#0f172a; border:1px solid rgba(255,255,255,0.06); border-radius:8px; padding:0.7rem 0.85rem;">'
                        +   '<div style="font-size:0.65rem; text-transform:uppercase; letter-spacing:0.06em; color:#9AA09B; font-weight:600;">' + labelHtml + '</div>'
                        +   '<div style="font-size:1.05rem; font-weight:700; color:#E9E8E1; margin-top:0.2rem;">' + valueTxt + '</div>'
                        +   (subTxt ? '<div style="font-size:0.68rem; color:#797F81; margin-top:0.15rem;">' + subTxt + '</div>' : '')
                        + '</div>';
                }

                var stripHtml = '<div style="display:grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap:0.55rem; margin: 0.9rem 0 1rem;">'
                    + tile('Exposure',      fmtCompact(_pitJit('modal_views', viewsAtAsOf)),                'viewers at ' + escapeHtml(_iiqFmtAsOfDate(asOf)))
                    + tile(infoLabelHtml,   proj.info_pct.toFixed(2) + '%',                                 '~' + fmtCompact(_pitJit('modal_info', infoCount)) + ' viewers within 7d', infoTileTip)
                    + tile(tkLabelHtml,     proj.ticket_pct.toFixed(2) + '%',                               '~' + fmtCompact(_pitJit('modal_tk',   tkCount))   + ' viewers within 7d', tkTileTip)
                    + tile('Engagement',    fmtCompact(_pitJit('modal_eng',  engAtAsOf)),                   'likes + comments + shares')
                    + tile('Engagement rate', engRate.toFixed(2) + '%',                                     'engagement / exposure')
                    + '</div>';

                var panel = document.createElement('div');
                panel.style.cssText = 'background: #0C1618; border:1px solid #27393D; border-radius:10px; max-width:960px; width:100%; max-height:90vh; overflow:auto; padding:1.25rem 1.5rem; box-shadow: 0 24px 64px rgba(0,0,0,0.5); font-family: \\'Inter\\', -apple-system, BlinkMacSystemFont, sans-serif;';

                panel.innerHTML =
                    '<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.4rem; gap:1rem;">'
                  +   '<div style="min-width:0; flex:1;">'
                  +     '<div style="display:flex; align-items:center; gap:0.55rem; margin-bottom:0.35rem;">'
                  +       '<span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:' + color + ';"></span>'
                  +       '<h3 style="margin:0; color:#E9E8E1; font-size:1.05rem; font-weight:700; line-height:1.2;">' + escapeHtml(label) + '</h3>'
                  +     '</div>'
                  +     '<div style="font-size:0.72rem; color:#9AA09B; line-height:1.5;">'
                  +       channelTxt + ' \u00b7 ' + escapeHtml(asset.asset_type || 'asset')
                  +       ' \u00b7 ' + escapeHtml(asset.paid_or_organic || 'unknown')
                  +       ' \u00b7 phase ' + phaseTxt
                  +       ' \u00b7 posted ' + postedTxt
                  +     '</div>'
                  +     '<div style="font-size:0.72rem; color:#797F81; margin-top:0.3rem;">'
                  +       urlHtml
                  +     '</div>'
                  +   '</div>'
                  +   '<button type="button" onclick="var m=document.getElementById(\\'iiqAssetModal\\'); if(m) m.remove();" style="background: rgba(255,255,255,0.06); color:#E9E8E1; border:1px solid rgba(255,255,255,0.12); border-radius:6px; padding:0.35rem 0.7rem; cursor:pointer; font-size:0.85rem; flex:none;">Close \u2715</button>'
                  + '</div>'
                  + stripHtml
                  + '<div id="iiqAssetPITChart" style="background:#0f172a; border:1px solid rgba(255,255,255,0.06); border-radius:8px; padding:0.85rem 1rem;">'
                  +   '<div style="font-size:0.75rem; color:#9AA09B; margin-bottom:0.4rem;">Daily performance up to ' + escapeHtml(_iiqFmtAsOfDate(asOf)) + '</div>'
                  +   '<div style="font-size:0.72rem; color:#797F81;">Loading daily curve\u2026</div>'
                  + '</div>';

                overlay.appendChild(panel);

                // ===== Daily curve at as-of =====
                // Prefer backend-supplied daily series if the endpoint has
                // one; fall back to iiqSynthAssetDailySeries synthesized
                // from window totals + posted_date. Then truncate at
                // as-of and render on the dual-axis line chart (views
                // left, primary funnel metric right).
                function _renderChartFromSeries(seriesFull, isSynth) {
                    var host = document.getElementById('iiqAssetPITChart');
                    if (!host) return;
                    var cutoff = asOf || '';
                    var s = (seriesFull || []).filter(function(p) {
                        return !cutoff || String(p.date || '').slice(0,10) <= cutoff;
                    });
                    if (!s.length) {
                        host.innerHTML = '<div style="font-size:0.75rem; color:#9AA09B; margin-bottom:0.4rem;">Daily performance up to ' + escapeHtml(_iiqFmtAsOfDate(asOf)) + '</div>'
                            + '<div style="font-size:0.72rem; color:#797F81;">No daily data in view yet at this cursor. Move the picker forward to see the first day this asset accrued measurable activity.</div>';
                        return;
                    }
                    var viewsPts = s.map(function(p) { return { x: p.date, y: Number(p.views) || 0 }; });
                    var primaryPts = s.map(function(p) {
                        var v = useInfo ? Number(p.info_seekers) : Number(p.ticket_visits);
                        return { x: p.date, y: isFinite(v) ? v : 0 };
                    });
                    var primaryLabel = useInfo ? 'Daily info-seekers' : 'Daily ticketing visits';
                    var primaryColor = useInfo ? '#a5b4fc' : '#f472b6';
                    var chartHtml;
                    if (typeof iiqLineChartDual === 'function') {
                        chartHtml = iiqLineChartDual(
                            [{ name: 'Daily views', color: '#60a5fa', points: viewsPts, style: 'area' }],
                            [{ name: primaryLabel,  color: primaryColor, points: primaryPts }],
                            { height: 260, rightFormatter: fmtCompact, rightLabelColor: 'rgba(165,180,252,0.85)' }
                        );
                    } else {
                        chartHtml = iiqLineChart(
                            [{ name: 'Daily views', color: '#60a5fa', points: viewsPts },
                             { name: primaryLabel,  color: primaryColor, points: primaryPts }],
                            { height: 260 }
                        );
                    }
                    var caption = 'Daily performance up to ' + escapeHtml(_iiqFmtAsOfDate(asOf))
                        + ' \u00b7 <span style="color:#797F81;">views on the left axis, ' + (useInfo ? 'info-seekers' : 'ticketing visits') + ' on the right axis</span>'
                        + (isSynth ? ' \u00b7 <span style="color:#797F81;">daily shape derived from window total + posted_date</span>' : '');
                    host.innerHTML = '<div style="font-size:0.75rem; color:#9AA09B; margin-bottom:0.4rem;">' + caption + '</div>' + chartHtml;
                }

                var aid = asset.asset_id;
                if (aid && slug) {
                    fetch('/api/intent/' + slug + '/asset/' + aid + '/timeseries?window=ytd', { credentials: 'same-origin' })
                        .then(function(r) { return r.json(); })
                        .then(function(d) {
                            var s = (d && d.series) || [];
                            if (s.length) {
                                _renderChartFromSeries(s, false);
                                return;
                            }
                            var synth = iiqSynthAssetDailySeries(asset, lifetimeViews, lifetimeEng) || [];
                            _renderChartFromSeries(synth, true);
                        })
                        .catch(function() {
                            var synth = iiqSynthAssetDailySeries(asset, lifetimeViews, lifetimeEng) || [];
                            _renderChartFromSeries(synth, true);
                        });
                } else {
                    var synth = iiqSynthAssetDailySeries(asset, lifetimeViews, lifetimeEng) || [];
                    _renderChartFromSeries(synth, true);
                }
            };

            // ===== Per-comp estimated signal breakdown (Q3) ====="""


def main():
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[splice] backup written: {BACKUP} ({len(src):,} bytes)")

    src = splice(src, OLD, NEW, "iiqShowAssetDetailPIT")

    INDEX.write_text(src, encoding="utf-8")
    print(f"[splice] wrote: {INDEX} ({len(src):,} bytes)")


if __name__ == "__main__":
    main()
