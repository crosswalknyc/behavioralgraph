#!/usr/bin/env python3
"""Restructure the Multi-Touch Attribution sub-tab renderer into three
cards (Journeys -> Co-exposure -> Coefficient chart).

Per .cursor/rules/index-html-safety.mdc: templates/index.html is edited
only via byte-level Python splice (never StrReplace, never Write). This
script performs ONE well-anchored replacement of the `_iiqMTARender`
function body. The bar-chart + details-table logic from v1 is preserved
verbatim inside the new Card 3.

Payload prerequisite: bg-webapp/mta_iq.py schema_version 2 (adds
`journeys` and `co_exposure` blocks; frontend guards against stale v1
by only rendering the new cards when those keys are present).
"""
from pathlib import Path

# Repo layout: script lives at bg-webapp/scripts/, so parent's parent is
# the bg-webapp submodule root.
HERE = Path(__file__).resolve().parent
INDEX = HERE.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_mta_v2_cards.html")


# ---- OLD anchor -----------------------------------------------------------
# The exact current v1 `_iiqMTARender` body. Must be unique in the file.
OLD = """            function _iiqMTARender(d) {
                var host = document.getElementById('iiqMTAContent');
                if (!host) return;
                var tps = (d.touchpoints || []).slice();
                var bfl = d.bottom_funnel_label || 'Ticketing';
                var convNoun = d.conversion_noun || 'conversion';

                if (!tps.length) {
                    host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem;">No touchpoints available for this campaign yet.</div>';
                    return;
                }

                // === Header + How-to-read (collapsible) ===
                var howToRead =
                    '<details style="margin: 0 0 0.85rem 0; padding: 0.6rem 0.9rem; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;">'
                  + '  <summary style="cursor: pointer; user-select: none; font-weight: 600; font-size: 0.82rem; color: var(--text-primary); list-style: none;">'
                  + '    <span class="iiq-disclosure-arrow" style="display:inline-block; transition: transform 0.15s; margin-right:0.35rem;">&#9654;</span>'
                  + '    How to read this.'
                  + '  </summary>'
                  + '  <div style="margin-top: 0.55rem; font-size: 0.76rem; line-height: 1.55; color: var(--text-secondary);">'
                  + '    <p style="margin: 0 0 0.4rem 0;"><strong style="color: #cbd5e1;">The lift</strong> next to each bar is the marginal contribution of that touchpoint to a viewer becoming a ' + escapeHtml(convNoun) + ', holding every other touchpoint in the campaign constant. Bars far from zero moved the needle; bars sitting near zero rode along without changing outcomes.</p>'
                  + '    <p style="margin: 0 0 0.4rem 0;"><strong style="color: #cbd5e1;">The odds ratio</strong> is the same number in an easier form: an odds ratio of 1.26 means viewers exposed to this touchpoint were <strong>1.26x more likely</strong> to visit the ' + escapeHtml(bfl.toLowerCase()) + ' page, all else equal.</p>'
                  + '    <p style="margin: 0 0 0.4rem 0;"><strong style="color: #cbd5e1;">The band</strong> after the number is the plausible range for that lift. A tight band means the read is precise; a wide band means the touchpoint had lighter exposure and the read is directional.</p>'
                  + '    <p style="margin: 0 0 0.4rem 0;"><strong style="color: #cbd5e1;">The Strong / Moderate / Weak chip</strong> is a shortcut for how much the data supports the lift being different from zero. Strong = high statistical support, Weak = the exposure ran but the bottom-funnel read is inconclusive.</p>'
                  + '    <p style="margin: 0.55rem 0 0 0; padding: 0.4rem 0.55rem; background: rgba(234,179,8,0.10); border-left: 2px solid rgba(234,179,8,0.5); border-radius: 4px; color:#fde68a;"><strong>One caveat.</strong> Lifts read at the campaign level. Adding or removing a touchpoint will move every other lift a little, so treat the ranking as a spend prompt, not a fixed rate card.</p>'
                  + '  </div>'
                  + '</details>';

                // === Title + subhead ===
                var header =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Touchpoint contribution to ' + escapeHtml(bfl.toLowerCase()) + '.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">Per-touchpoint lift on ' + escapeHtml(convNoun) + ' conversion, holding every other touchpoint in the campaign constant. Ranked by absolute lift.</div>'
                  + '</div>';

                // === Top strip: 3 summary tiles ===
                var fit = d.model_fit || {};
                var quality = String(fit.quality || 'weak');
                var qLabel = { strong: 'Strong fit', moderate: 'Moderate fit', weak: 'Weak fit' }[quality] || 'Weak fit';
                var qColor = { strong: '#C7F23E', moderate: '#9FD628', weak: '#94a3b8' }[quality] || '#94a3b8';
                var qBg    = { strong: 'rgba(199,242,62,0.14)', moderate: 'rgba(159,214,40,0.14)', weak: 'rgba(148,163,184,0.14)' }[quality] || 'rgba(148,163,184,0.14)';
                var qChip  = '<span style="display:inline-block; padding:0.1rem 0.55rem; font-size:0.68rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; background:' + qBg + '; color:' + qColor + ';">' + qLabel + '</span>';
                var tiles = [
                    iiqSummaryTile('Baseline conversion rate', _iiqMTAFmtPct(d.conversion_rate, 2), 'share of exposed viewers who visited the ' + bfl.toLowerCase() + ' page in the attribution window'),
                    iiqSummaryTile('Exposed viewers', fmtCompact(d.sample_size), 'individual viewers used in the fit'),
                    '<div class="iiq-card" style="padding:0.6rem 0.75rem; display:flex; flex-direction:column; justify-content:space-between;">'
                      + '<div class="iiq-card-meta" style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.5px; opacity:0.7;">Read strength</div>'
                      + '<div style="margin-top:0.35rem;">' + qChip + '</div>'
                      + '<div class="iiq-card-meta" style="margin-top:0.4rem; font-size:0.7rem; opacity:0.7;">pseudo R\\u00b2 ' + (fit.pseudo_r_squared != null ? Number(fit.pseudo_r_squared).toFixed(3) : '\\u2014') + ' \\u00b7 ' + (fit.convergence ? 'converged' : 'no convergence') + '</div>'
                    + '</div>'
                ].join('');
                var topStrip = '<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; margin-bottom: 0.85rem;">' + tiles + '</div>';

                // === Horizontal bar chart ===
                var visLimit = 20;
                var visible = tps.slice(0, visLimit);
                var extra = tps.length - visible.length;
                var maxAbs = 0;
                for (var i = 0; i < tps.length; i++) { var v = Math.abs(Number(tps[i].coefficient) || 0); if (v > maxAbs) maxAbs = v; }
                if (maxAbs < 1e-6) maxAbs = 1;
                var barRows = visible.map(function(r) {
                    var c = Number(r.coefficient) || 0;
                    var w = (Math.abs(c) / maxAbs) * 100.0;
                    var color = _iiqMTABarColor(r.significance, c);
                    var track = '#3B3D38';   // Pavement
                    // Two half-tracks meet at center; the bar extends left (negative) or right (positive)
                    var leftHalf  = (c < 0)
                        ? '<div style="flex:1; display:flex; justify-content:flex-end;"><div style="width:' + w.toFixed(2) + '%; height:14px; background:' + color + '; border-radius:9999px;"></div></div>'
                        : '<div style="flex:1; display:flex; justify-content:flex-end;"><div style="width:0; height:14px;"></div></div>';
                    var rightHalf = (c >= 0)
                        ? '<div style="flex:1;"><div style="width:' + w.toFixed(2) + '%; height:14px; background:' + color + '; border-radius:9999px;"></div></div>'
                        : '<div style="flex:1;"><div style="width:0; height:14px;"></div></div>';
                    var centerRule = '<div style="width:1px; align-self:stretch; background:rgba(255,255,255,0.10); margin:0 2px;"></div>';
                    var band = r.confidence_interval || [];
                    var bandText = (band.length === 2) ? '[' + Number(band[0]).toFixed(3) + ', ' + Number(band[1]).toFixed(3) + ']' : '';
                    return (
                        '<div style="display:grid; grid-template-columns: 240px 1fr 140px 90px; gap:0.6rem; align-items:center; padding:0.35rem 0;">'
                      + '  <div style="min-width:0;">'
                      + '    <div style="font-size:0.78rem; color: var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="' + escapeHtml(r.asset_title) + '">' + escapeHtml(r.asset_title) + '</div>'
                      + '    <div style="font-size:0.66rem; color: var(--text-secondary); margin-top:0.1rem;">' + escapeHtml(r.channel) + ' \\u00b7 ' + escapeHtml(r.phase || '') + '</div>'
                      + '  </div>'
                      + '  <div style="display:flex; align-items:center; background: rgba(255,255,255,0.02); border-radius:9999px; padding:2px 6px;">' + leftHalf + centerRule + rightHalf + '</div>'
                      + '  <div style="font-size:0.75rem; color: var(--text-primary); text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap;">' + _iiqMTAFmtSigned(c) + ' <span style="color: var(--text-secondary);">(' + Number(r.odds_ratio).toFixed(2) + 'x)</span></div>'
                      + '  <div style="text-align:right;">' + _iiqMTASigChip(r.significance) + '</div>'
                      + '</div>'
                    );
                }).join('');

                var chartHead =
                    '<div style="display:grid; grid-template-columns: 240px 1fr 140px 90px; gap:0.6rem; padding: 0 0 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary);">'
                  + '  <div>Touchpoint</div>'
                  + '  <div style="text-align:center;">Lift (log-odds)</div>'
                  + '  <div style="text-align:right;">Lift value</div>'
                  + '  <div style="text-align:right;">Read strength</div>'
                  + '</div>';

                var chart =
                    '<div style="padding: 0.75rem 0.9rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;">'
                  + chartHead
                  + '<div style="margin-top: 0.15rem;">' + barRows + '</div>'
                  + (extra > 0 ? '<div style="margin-top:0.5rem; font-size:0.7rem; color: var(--text-secondary); text-align:center;">' + extra + ' more touchpoints below the top ' + visLimit + ' \\u2014 open the details table for the full list.</div>' : '')
                  + '</div>';

                // === Details table (default hidden) ===
                var tableHeadRow =
                    '<tr>'
                  + '<th style="text-align:left;  padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Individual touchpoint (creative + channel)">Touchpoint</th>'
                  + '<th style="text-align:left;  padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Content platform / distribution surface">Channel</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Marginal contribution to conversion (log-odds), holding all other touchpoints constant">Lift</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Multiplicative form of lift: exp(lift). 1.20 means 1.20x more likely to convert.">Odds ratio</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Plausible band around the lift. Tighter = higher precision.">Band</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Wald p-value: lower = stronger evidence the lift is not zero.">p-value</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Number of exposed viewers included in this touchpoint\\u2019s fit">Exposed</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Of the exposed, how many took the bottom-funnel action">Converted</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Categorical read strength: Strong / Moderate / Weak">Read</th>'
                  + '</tr>';
                var tableRows = tps.map(function(r) {
                    var c = Number(r.coefficient) || 0;
                    var band = r.confidence_interval || [];
                    var bandText = (band.length === 2) ? '[' + Number(band[0]).toFixed(3) + ', ' + Number(band[1]).toFixed(3) + ']' : '\\u2014';
                    return (
                        '<tr>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.75rem; color: var(--text-primary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + escapeHtml(r.asset_title) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + escapeHtml(r.channel) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.75rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: ' + (c >= 0 ? '#e2e8f0' : '#fda4af') + ';">' + _iiqMTAFmtSigned(c) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.75rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-primary);">' + Number(r.odds_ratio).toFixed(3) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + bandText + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + Number(r.p_value).toFixed(4) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + (r.exposed_n || 0).toLocaleString() + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + (r.converted_n || 0).toLocaleString() + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; border-bottom: 1px solid rgba(255,255,255,0.05);">' + _iiqMTASigChip(r.significance) + '</td>'
                      + '</tr>'
                    );
                }).join('');
                var detailsTable =
                    '<details style="margin-top: 0.85rem; padding: 0.5rem 0.7rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;">'
                  + '  <summary style="cursor: pointer; user-select: none; font-weight: 600; font-size: 0.82rem; color: var(--text-primary); list-style: none;">'
                  + '    <span class="iiq-disclosure-arrow" style="display:inline-block; transition: transform 0.15s; margin-right:0.35rem;">&#9654;</span>'
                  + '    Show details (all ' + tps.length + ' touchpoints).'
                  + '  </summary>'
                  + '  <div style="margin-top: 0.45rem; overflow-x: auto;">'
                  + '    <table style="width:100%; border-collapse: collapse; font-family: inherit;">'
                  + '      <thead>' + tableHeadRow + '</thead>'
                  + '      <tbody>' + tableRows + '</tbody>'
                  + '    </table>'
                  + '  </div>'
                  + '</details>';

                host.innerHTML = header + howToRead + topStrip + chart + detailsTable;
            }"""


# ---- NEW block ------------------------------------------------------------
# v2: three cards in order (Journeys -> Co-exposure -> Coefficient chart),
# expanded "How to read this" that covers all three. Copy compliance:
# no em/en dashes; individual-level nouns; no methodology jargon
# (regression / logistic / IRLS / L2 / p-value / pseudo-R^2 / Wald CI) in
# visible strings.
NEW = """            // ------------------------------------------------------------
            // Channel glyph pill: short abbreviation shown before an asset
            // title chip in the Journeys card. Kept ASCII-only so a browser
            // without emoji fallback still renders coherent pills.
            // ------------------------------------------------------------
            function _iiqMTAChannelGlyph(ch) {
                var s = String(ch || '').toLowerCase();
                if (s.indexOf('tiktok')   !== -1) return 'TT';
                if (s.indexOf('youtube')  !== -1) return 'YT';
                if (s.indexOf('instagram')!== -1) return 'IG';
                if (s.indexOf('facebook') !== -1) return 'FB';
                if (s.indexOf('reddit')   !== -1) return 'RD';
                if (s.indexOf('snap')     !== -1) return 'SC';
                if (s.indexOf('twitter')  !== -1 || s === 'x') return 'X';
                if (s.indexOf('google')   !== -1 || s.indexOf('search') !== -1) return 'GG';
                if (s.indexOf('podcast')  !== -1) return 'PC';
                if (s.indexOf('wikipedia')!== -1) return 'WK';
                if (s.indexOf('imdb')     !== -1) return 'IM';
                return (String(ch || 'CH').replace(/[^A-Za-z]/g, '').slice(0, 2) || 'CH').toUpperCase();
            }

            // Lift-vs-baseline cell color per the spec:
            //   >= 1.5x -> Signal Green
            //   0.8 - 1.5x -> Dusk
            //   < 0.8x -> Pavement-outlined
            function _iiqMTALiftStyle(x) {
                var n = Number(x);
                if (!isFinite(n)) return { bg: 'transparent', color: '#94a3b8', outline: 'none' };
                if (n >= 1.5) return { bg: 'rgba(199,242,62,0.16)', color: '#C7F23E', outline: '1px solid rgba(199,242,62,0.35)' };
                if (n >= 0.8) return { bg: 'rgba(183,179,216,0.14)', color: '#B7B3D8', outline: '1px solid rgba(183,179,216,0.28)' };
                return { bg: 'transparent',                 color: '#7C878A', outline: '1px solid #3B3D38' };
            }

            // Truncate a display string to at most `n` characters, adding an
            // ellipsis when clipped. Preserves the raw string in a `title`
            // attribute on the emitter side so hovering shows the full label.
            function _iiqMTATrunc(s, n) {
                s = String(s || '');
                if (!n) n = 25;
                if (s.length <= n) return s;
                return s.slice(0, Math.max(1, n - 1)) + '\\u2026';
            }

            // Heatmap cell background along Pavement -> Slate Teal -> Signal
            // Green. Anchors: 0.0 -> #3B3D38, 0.5 -> #15252A, 1.0 -> #C7F23E.
            // Between anchors we lerp component-wise. Text color flips light
            // when the background sits in the dark middle of the ramp so the
            // percentage stays legible.
            function _iiqMTAHeatColor(v) {
                v = Number(v);
                if (!isFinite(v)) v = 0;
                v = Math.max(0, Math.min(1, v));
                var stops = [
                    { at: 0.0, rgb: [59, 61, 56]   },  // Pavement
                    { at: 0.5, rgb: [21, 37, 42]   },  // Slate Teal
                    { at: 1.0, rgb: [199, 242, 62] }   // Signal Green
                ];
                var lo = stops[0], hi = stops[stops.length - 1];
                for (var i = 0; i < stops.length - 1; i++) {
                    if (v >= stops[i].at && v <= stops[i + 1].at) { lo = stops[i]; hi = stops[i + 1]; break; }
                }
                var t = (v - lo.at) / Math.max(1e-6, (hi.at - lo.at));
                var r = Math.round(lo.rgb[0] + (hi.rgb[0] - lo.rgb[0]) * t);
                var g = Math.round(lo.rgb[1] + (hi.rgb[1] - lo.rgb[1]) * t);
                var b = Math.round(lo.rgb[2] + (hi.rgb[2] - lo.rgb[2]) * t);
                // Simple luminance rule: bright cells get graphite text,
                // dark cells get off-white text.
                var lum = 0.299*r + 0.587*g + 0.114*b;
                var text = lum > 140 ? '#0C1618' : '#E9E8E1';
                return {
                    bg:  'rgb(' + r + ',' + g + ',' + b + ')',
                    fg:  text
                };
            }

            // ------------------------------------------------------------
            // Card 1: journeys. Table with columns:
            //   Journey | Path length | Exposed | Converted |
            //   Conversion rate | Lift vs baseline | Share of exposed
            // Sortable headers (click to sort). Default sort: Converted desc.
            // Table state is kept in a closure so a re-sort re-renders the
            // <tbody> only, not the whole card.
            // ------------------------------------------------------------
            function _iiqMTARenderJourneysCard(d, containerId) {
                var journeys = (d.journeys || []).slice();
                if (!journeys.length) {
                    return '<div class="iiq-empty" style="padding:1rem;">Not enough exposure paths yet to break out the top journeys on this campaign.</div>';
                }
                var bfl = d.bottom_funnel_label || 'Ticketing';
                var intro =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">How the journeys built up to this.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">The ' + journeys.length + ' exposure paths that generated the most conversions on this campaign. Each row is a distinct combination of touchpoints a set of viewers all saw.</div>'
                  + '</div>';

                var tableId = containerId + '_tbl';
                var cols = [
                    { key: 'journey',        label: 'Journey',           align: 'left'  },
                    { key: 'path_length',    label: 'Path length',       align: 'right' },
                    { key: 'exposed_n',      label: 'Exposed',           align: 'right' },
                    { key: 'converted_n',    label: 'Converted',         align: 'right' },
                    { key: 'conversion_rate',label: 'Conversion rate',   align: 'right' },
                    { key: 'lift_vs_baseline', label: 'Lift vs baseline', align: 'right' },
                    { key: 'share_of_exposed', label: 'Share of exposed', align: 'right' }
                ];
                var headers = cols.map(function(c) {
                    var arrow = '<span data-arrow="' + c.key + '" style="opacity:0.35; margin-left:0.25rem;">\\u2195</span>';
                    return '<th data-sort-key="' + c.key + '" style="cursor:pointer; user-select:none; text-align:' + c.align + '; padding:0.4rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);">' + escapeHtml(c.label) + arrow + '</th>';
                }).join('');

                function renderChipStrip(tps) {
                    if (!tps || !tps.length) return '';
                    return '<div style="display:flex; flex-wrap:wrap; gap:4px; align-items:center; max-width:520px;">' + tps.map(function(t) {
                        var glyph = _iiqMTAChannelGlyph(t.channel);
                        var title = _iiqMTATrunc(t.asset_title, 25);
                        return (
                            '<span style="display:inline-flex; align-items:center; padding:2px 8px 2px 4px; border:1px solid rgba(255,255,255,0.12); border-radius:9999px; background: rgba(255,255,255,0.03); font-size:0.7rem; line-height:1.2; color:#e2e8f0; white-space:nowrap;" title="' + escapeHtml(t.channel + ' ' + t.asset_title) + '">'
                          + '<span style="display:inline-block; margin-right:4px; padding:1px 5px; border-radius:9999px; background: rgba(199,242,62,0.14); color:#C7F23E; font-size:0.6rem; font-weight:700; letter-spacing:0.05em;">' + escapeHtml(glyph) + '</span>'
                          + escapeHtml(title)
                          + '</span>'
                        );
                    }).join('') + '</div>';
                }

                function renderRow(r) {
                    var liftStyle = _iiqMTALiftStyle(r.lift_vs_baseline);
                    var chipStrip = renderChipStrip(r.touchpoints || []);
                    var rate = _iiqMTAFmtPct(r.conversion_rate, 2);
                    var share = _iiqMTAFmtPct(r.share_of_exposed, 1);
                    var liftTxt = (Number(r.lift_vs_baseline) || 0).toFixed(2) + 'x';
                    return (
                        '<tr>'
                      + '<td style="padding:0.5rem 0.55rem; vertical-align:middle; border-bottom: 1px solid rgba(255,255,255,0.05);">' + chipStrip + '</td>'
                      + '<td style="padding:0.5rem 0.55rem; text-align:right; font-variant-numeric: tabular-nums; font-size:0.75rem; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + (r.path_length || 0) + '</td>'
                      + '<td style="padding:0.5rem 0.55rem; text-align:right; font-variant-numeric: tabular-nums; font-size:0.78rem; color: var(--text-primary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + fmtInt(r.exposed_n) + '</td>'
                      + '<td style="padding:0.5rem 0.55rem; text-align:right; font-variant-numeric: tabular-nums; font-size:0.78rem; color: var(--text-primary); border-bottom: 1px solid rgba(255,255,255,0.05); font-weight:600;">' + fmtInt(r.converted_n) + '</td>'
                      + '<td style="padding:0.5rem 0.55rem; text-align:right; font-variant-numeric: tabular-nums; font-size:0.78rem; color: var(--text-primary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + escapeHtml(rate) + '</td>'
                      + '<td style="padding:0.5rem 0.55rem; text-align:right; font-variant-numeric: tabular-nums; font-size:0.78rem; border-bottom: 1px solid rgba(255,255,255,0.05);">'
                      +   '<span style="display:inline-block; padding:2px 8px; border-radius:6px; background:' + liftStyle.bg + '; color:' + liftStyle.color + '; outline:' + liftStyle.outline + '; font-weight:600; white-space:nowrap;">' + escapeHtml(liftTxt) + '</span>'
                      + '</td>'
                      + '<td style="padding:0.5rem 0.55rem; text-align:right; font-variant-numeric: tabular-nums; font-size:0.75rem; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + escapeHtml(share) + '</td>'
                      + '</tr>'
                    );
                }

                var body = journeys.map(renderRow).join('');
                var table =
                    '<div style="padding: 0.75rem 0.9rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; overflow-x: auto;">'
                  + '  <table id="' + tableId + '" style="width:100%; border-collapse: collapse; font-family: inherit;">'
                  + '    <thead>' + headers + '</thead>'
                  + '    <tbody>' + body + '</tbody>'
                  + '  </table>'
                  + '</div>';

                // Wire sort handlers after mount.
                setTimeout(function() {
                    var tbl = document.getElementById(tableId);
                    if (!tbl) return;
                    var state = { key: 'converted_n', dir: 'desc' };
                    // Reflect default sort in the header arrows.
                    var defA = tbl.querySelector('[data-arrow="' + state.key + '"]');
                    if (defA) { defA.textContent = '\\u25BE'; defA.style.opacity = '1'; }
                    tbl.querySelectorAll('th[data-sort-key]').forEach(function(th) {
                        th.addEventListener('click', function() {
                            var k = th.getAttribute('data-sort-key');
                            if (state.key === k) {
                                state.dir = (state.dir === 'desc') ? 'asc' : 'desc';
                            } else {
                                state.key = k;
                                state.dir = (k === 'journey' || k === 'path_length') ? 'asc' : 'desc';
                            }
                            var sorted = journeys.slice().sort(function(a, b) {
                                var av, bv;
                                if (k === 'journey') {
                                    av = (a.touchpoints && a.touchpoints.length) ? String(a.touchpoints[0].asset_title || '').toLowerCase() : '';
                                    bv = (b.touchpoints && b.touchpoints.length) ? String(b.touchpoints[0].asset_title || '').toLowerCase() : '';
                                    return (av < bv ? -1 : av > bv ? 1 : 0) * (state.dir === 'asc' ? 1 : -1);
                                }
                                av = Number(a[k] || 0);
                                bv = Number(b[k] || 0);
                                return (av - bv) * (state.dir === 'asc' ? 1 : -1);
                            });
                            var tb = tbl.querySelector('tbody');
                            if (tb) tb.innerHTML = sorted.map(renderRow).join('');
                            tbl.querySelectorAll('[data-arrow]').forEach(function(a) {
                                var ak = a.getAttribute('data-arrow');
                                if (ak === state.key) {
                                    a.textContent = (state.dir === 'asc') ? '\\u25B4' : '\\u25BE';
                                    a.style.opacity = '1';
                                } else {
                                    a.textContent = '\\u2195';
                                    a.style.opacity = '0.35';
                                }
                            });
                        });
                    });
                }, 0);

                return intro + table;
            }

            // ------------------------------------------------------------
            // Card 2: co-exposure heatmap. Compact N x N grid rendered as
            // a CSS grid. Column labels rotate ~35deg for readability. The
            // whole thing sits in an overflow-x scroller for narrow panels.
            // ------------------------------------------------------------
            function _iiqMTARenderCoExposureCard(d) {
                var ce = d.co_exposure || {};
                var tps = ce.touchpoints || [];
                var mat = ce.matrix || [];
                if (!tps.length || !mat.length) {
                    return '<div class="iiq-empty" style="padding:1rem;">Co-exposure grid needs at least a handful of overlapping touchpoints. Come back once the campaign has more exposure paths logged.</div>';
                }
                var N = Math.min(tps.length, mat.length);
                var intro =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Which touchpoints travel together.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">For the top ' + N + ' touchpoints by lift, the share of people exposed to the row that were also exposed to the column. Diagonal is always 100%. Bright cells travel together.</div>'
                  + '</div>';

                // Cell + label sizing. Column labels get vertical room via a
                // dedicated header row (fixed height, rotated text). Row
                // labels are truncated titles in a fixed-width first column.
                var cell   = 30;
                var labelW = 170;
                var headerH = 92;

                // Column-label header (rotated ~35deg). Wrap each label so
                // the rotated text pivots around its bottom-left corner
                // (transform-origin) and aligns to the column center.
                var colHdrCells = '';
                for (var j = 0; j < N; j++) {
                    var t = tps[j];
                    var label = _iiqMTATrunc(t.asset_title, 18);
                    colHdrCells += (
                        '<div style="width:' + cell + 'px; height:' + headerH + 'px; position:relative;">'
                      + '  <div title="' + escapeHtml(t.channel + ' ' + t.asset_title) + '" style="position:absolute; left:50%; bottom:6px; transform: translateX(-50%) rotate(-35deg); transform-origin: left bottom; white-space:nowrap; font-size:0.66rem; color: var(--text-secondary);">'
                      + '<span style="display:inline-block; padding:0 4px; border-radius:4px; background: rgba(199,242,62,0.10); color:#C7F23E; font-size:0.55rem; font-weight:700; letter-spacing:0.05em; margin-right:3px;">' + escapeHtml(_iiqMTAChannelGlyph(t.channel)) + '</span>'
                      + escapeHtml(label)
                      + '  </div>'
                      + '</div>'
                    );
                }
                var colHdrCorner = '<div style="width:' + labelW + 'px; height:' + headerH + 'px;"></div>';

                // Body rows.
                var bodyRows = '';
                for (var i = 0; i < N; i++) {
                    var tr = tps[i];
                    var rowLabel = _iiqMTATrunc(tr.asset_title, 26);
                    var marg = _iiqMTAFmtPct(tr.marginal_exposure_rate, 1);
                    var rowHtml = (
                        '<div style="width:' + labelW + 'px; padding: 0 8px 0 4px; display:flex; align-items:center; justify-content:flex-end; gap:6px; font-size:0.72rem; color: var(--text-primary); white-space:nowrap;" title="' + escapeHtml(tr.channel + ' ' + tr.asset_title + ' (marginal exposure ' + marg + ')') + '">'
                      + '  <span style="display:inline-block; padding:1px 5px; border-radius:9999px; background: rgba(199,242,62,0.14); color:#C7F23E; font-size:0.55rem; font-weight:700; letter-spacing:0.05em;">' + escapeHtml(_iiqMTAChannelGlyph(tr.channel)) + '</span>'
                      + '  <span style="overflow:hidden; text-overflow:ellipsis;">' + escapeHtml(rowLabel) + '</span>'
                      + '</div>'
                    );
                    for (var j2 = 0; j2 < N; j2++) {
                        var v = Number((mat[i] || [])[j2] || 0);
                        var col = _iiqMTAHeatColor(v);
                        var pct = (v * 100).toFixed(0);
                        var showText = (v >= 0.15) ? (pct + '%') : '';
                        var titleStr = (tps[i].asset_title + ' -> ' + tps[j2].asset_title + ': ' + (v * 100).toFixed(1) + '%');
                        rowHtml += (
                            '<div title="' + escapeHtml(titleStr) + '" style="width:' + cell + 'px; height:' + cell + 'px; display:flex; align-items:center; justify-content:center; font-size:0.62rem; font-variant-numeric: tabular-nums; background:' + col.bg + '; color:' + col.fg + '; border:1px solid rgba(12,22,24,0.55);">'
                          + escapeHtml(showText)
                          + '</div>'
                        );
                    }
                    bodyRows += '<div style="display:flex; align-items:center;">' + rowHtml + '</div>';
                }

                var grid =
                    '<div style="padding: 0.75rem 0.9rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; overflow-x: auto;">'
                  + '  <div style="display:inline-block; min-width: 100%;">'
                  + '    <div style="display:flex; align-items:flex-end;">' + colHdrCorner + colHdrCells + '</div>'
                  +      bodyRows
                  + '  </div>'
                  + '  <div style="display:flex; align-items:center; gap:0.6rem; margin-top:0.7rem; font-size:0.66rem; color: var(--text-secondary);">'
                  + '    <span>Less overlap</span>'
                  + '    <div style="width:120px; height:10px; border-radius:4px; background: linear-gradient(90deg, rgb(59,61,56) 0%, rgb(21,37,42) 50%, rgb(199,242,62) 100%);"></div>'
                  + '    <span>Overlaps more</span>'
                  + '    <span style="margin-left:auto;">Cells below 15% left blank for readability.</span>'
                  + '  </div>'
                  + '</div>';
                return intro + grid;
            }

            // ------------------------------------------------------------
            // Card 3: touchpoint contribution chart. Preserved from v1.
            // ------------------------------------------------------------
            function _iiqMTARenderCoefficientCard(d) {
                var tps = (d.touchpoints || []).slice();
                if (!tps.length) return '';
                var bfl = d.bottom_funnel_label || 'Ticketing';
                var convNoun = d.conversion_noun || 'conversion';
                var header =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Touchpoint contribution to ' + escapeHtml(bfl.toLowerCase()) + '.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">Per-touchpoint lift on ' + escapeHtml(convNoun) + ' conversion, ranked by absolute lift.</div>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">These lifts are the multi-touch answer: each coefficient is the marginal contribution of one additional exposure to that touchpoint, holding every other touchpoint constant.</div>'
                  + '</div>';

                var visLimit = 20;
                var visible = tps.slice(0, visLimit);
                var extra = tps.length - visible.length;
                var maxAbs = 0;
                for (var i = 0; i < tps.length; i++) { var v = Math.abs(Number(tps[i].coefficient) || 0); if (v > maxAbs) maxAbs = v; }
                if (maxAbs < 1e-6) maxAbs = 1;
                var barRows = visible.map(function(r) {
                    var c = Number(r.coefficient) || 0;
                    var w = (Math.abs(c) / maxAbs) * 100.0;
                    var color = _iiqMTABarColor(r.significance, c);
                    var leftHalf  = (c < 0)
                        ? '<div style="flex:1; display:flex; justify-content:flex-end;"><div style="width:' + w.toFixed(2) + '%; height:14px; background:' + color + '; border-radius:9999px;"></div></div>'
                        : '<div style="flex:1; display:flex; justify-content:flex-end;"><div style="width:0; height:14px;"></div></div>';
                    var rightHalf = (c >= 0)
                        ? '<div style="flex:1;"><div style="width:' + w.toFixed(2) + '%; height:14px; background:' + color + '; border-radius:9999px;"></div></div>'
                        : '<div style="flex:1;"><div style="width:0; height:14px;"></div></div>';
                    var centerRule = '<div style="width:1px; align-self:stretch; background:rgba(255,255,255,0.10); margin:0 2px;"></div>';
                    return (
                        '<div style="display:grid; grid-template-columns: 240px 1fr 140px 90px; gap:0.6rem; align-items:center; padding:0.35rem 0;">'
                      + '  <div style="min-width:0;">'
                      + '    <div style="font-size:0.78rem; color: var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="' + escapeHtml(r.asset_title) + '">' + escapeHtml(r.asset_title) + '</div>'
                      + '    <div style="font-size:0.66rem; color: var(--text-secondary); margin-top:0.1rem;">' + escapeHtml(r.channel) + ' \\u00b7 ' + escapeHtml(r.phase || '') + '</div>'
                      + '  </div>'
                      + '  <div style="display:flex; align-items:center; background: rgba(255,255,255,0.02); border-radius:9999px; padding:2px 6px;">' + leftHalf + centerRule + rightHalf + '</div>'
                      + '  <div style="font-size:0.75rem; color: var(--text-primary); text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap;">' + _iiqMTAFmtSigned(c) + ' <span style="color: var(--text-secondary);">(' + Number(r.odds_ratio).toFixed(2) + 'x)</span></div>'
                      + '  <div style="text-align:right;">' + _iiqMTASigChip(r.significance) + '</div>'
                      + '</div>'
                    );
                }).join('');
                var chartHead =
                    '<div style="display:grid; grid-template-columns: 240px 1fr 140px 90px; gap:0.6rem; padding: 0 0 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); font-size:0.66rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary);">'
                  + '  <div>Touchpoint</div>'
                  + '  <div style="text-align:center;">Lift shape</div>'
                  + '  <div style="text-align:right;">Lift value</div>'
                  + '  <div style="text-align:right;">Read strength</div>'
                  + '</div>';
                var chart =
                    '<div style="padding: 0.75rem 0.9rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;">'
                  + chartHead
                  + '<div style="margin-top: 0.15rem;">' + barRows + '</div>'
                  + (extra > 0 ? '<div style="margin-top:0.5rem; font-size:0.7rem; color: var(--text-secondary); text-align:center;">' + extra + ' more touchpoints below the top ' + visLimit + '. Open the details table for the full list.</div>' : '')
                  + '</div>';

                // Details table (default hidden). Preserved from v1, minus the
                // v1 "p-value" column heading which was renamed to a
                // reader-friendly "Signal" chip label.
                var tableHeadRow =
                    '<tr>'
                  + '<th style="text-align:left;  padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Individual touchpoint (creative + channel)">Touchpoint</th>'
                  + '<th style="text-align:left;  padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Content platform / distribution surface">Channel</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Marginal contribution of one additional exposure to conversion, holding all other touchpoints constant">Lift</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Multiplicative form of lift. 1.20 means 1.20x more likely to convert per additional exposure.">Odds ratio</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Plausible band around the lift. Tighter = higher precision.">Band</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Number of exposed viewers included in this touchpoint\\u2019s read">Exposed</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Of the exposed, how many took the bottom-funnel action">Converted</th>'
                  + '<th style="text-align:right; padding:0.35rem 0.55rem; font-size:0.68rem; text-transform:uppercase; letter-spacing:0.04em; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.10);" title="Read strength: Strong / Moderate / Weak">Signal</th>'
                  + '</tr>';
                var tableRows = tps.map(function(r) {
                    var c = Number(r.coefficient) || 0;
                    var band = r.confidence_interval || [];
                    var bandText = (band.length === 2) ? '[' + Number(band[0]).toFixed(3) + ', ' + Number(band[1]).toFixed(3) + ']' : '\\u2014';
                    return (
                        '<tr>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.75rem; color: var(--text-primary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + escapeHtml(r.asset_title) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; color: var(--text-secondary); border-bottom: 1px solid rgba(255,255,255,0.05);">' + escapeHtml(r.channel) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.75rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: ' + (c >= 0 ? '#e2e8f0' : '#fda4af') + ';">' + _iiqMTAFmtSigned(c) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.75rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-primary);">' + Number(r.odds_ratio).toFixed(3) + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + bandText + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + (r.exposed_n || 0).toLocaleString() + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; font-variant-numeric: tabular-nums; border-bottom: 1px solid rgba(255,255,255,0.05); color: var(--text-secondary);">' + (r.converted_n || 0).toLocaleString() + '</td>'
                      + '<td style="padding:0.32rem 0.55rem; font-size:0.72rem; text-align:right; border-bottom: 1px solid rgba(255,255,255,0.05);">' + _iiqMTASigChip(r.significance) + '</td>'
                      + '</tr>'
                    );
                }).join('');
                var detailsTable =
                    '<details style="margin-top: 0.85rem; padding: 0.5rem 0.7rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;">'
                  + '  <summary style="cursor: pointer; user-select: none; font-weight: 600; font-size: 0.82rem; color: var(--text-primary); list-style: none;">'
                  + '    <span class="iiq-disclosure-arrow" style="display:inline-block; transition: transform 0.15s; margin-right:0.35rem;">&#9654;</span>'
                  + '    Show details (all ' + tps.length + ' touchpoints).'
                  + '  </summary>'
                  + '  <div style="margin-top: 0.45rem; overflow-x: auto;">'
                  + '    <table style="width:100%; border-collapse: collapse; font-family: inherit;">'
                  + '      <thead>' + tableHeadRow + '</thead>'
                  + '      <tbody>' + tableRows + '</tbody>'
                  + '    </table>'
                  + '  </div>'
                  + '</details>';

                return header + chart + detailsTable;
            }

            function _iiqMTARender(d) {
                var host = document.getElementById('iiqMTAContent');
                if (!host) return;
                var tps = (d.touchpoints || []).slice();
                var bfl = d.bottom_funnel_label || 'Ticketing';
                var convNoun = d.conversion_noun || 'conversion';

                if (!tps.length) {
                    host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem;">No touchpoints available for this campaign yet.</div>';
                    return;
                }

                // How-to-read (collapsible). Rewritten to cover all three
                // cards on the tab. Keeps the standing caveat from v1.
                var howToRead =
                    '<details style="margin: 0 0 0.85rem 0; padding: 0.6rem 0.9rem; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;">'
                  + '  <summary style="cursor: pointer; user-select: none; font-weight: 600; font-size: 0.82rem; color: var(--text-primary); list-style: none;">'
                  + '    <span class="iiq-disclosure-arrow" style="display:inline-block; transition: transform 0.15s; margin-right:0.35rem;">&#9654;</span>'
                  + '    How to read this.'
                  + '  </summary>'
                  + '  <div style="margin-top: 0.55rem; font-size: 0.76rem; line-height: 1.55; color: var(--text-secondary);">'
                  + '    <p style="margin: 0 0 0.35rem 0;"><strong style="color: #cbd5e1;">What you\\u2019re looking at.</strong> Three views on the same set of viewers, in the order they build:</p>'
                  + '    <ul style="margin: 0 0 0.5rem 1.1rem; padding: 0;">'
                  + '      <li style="margin-bottom:0.2rem;"><strong style="color:#cbd5e1;">Journeys</strong> show which combinations of exposures produced conversions. Each row is a distinct set of touchpoints a group of viewers all saw.</li>'
                  + '      <li style="margin-bottom:0.2rem;"><strong style="color:#cbd5e1;">The heatmap</strong> shows which touchpoints travel together. Bright cells sit on pairs viewers see together a lot; dark cells sit on pairs that rarely overlap.</li>'
                  + '      <li><strong style="color:#cbd5e1;">The lift chart</strong> shows the marginal contribution of one more exposure to each touchpoint, holding every other touchpoint constant.</li>'
                  + '    </ul>'
                  + '    <p style="margin: 0 0 0.35rem 0;"><strong style="color: #cbd5e1;">Why this is multi-touch.</strong> Every person\\u2019s full exposure vector goes into one read. A person who saw five things contributes evidence for all five simultaneously. If two touchpoints always show up together and one is doing the real work, the other\\u2019s lift will collapse toward zero. That is what \\u201cholding every other touchpoint constant\\u201d means.</p>'
                  + '    <p style="margin: 0 0 0.35rem 0;"><strong style="color: #cbd5e1;">What each metric means.</strong></p>'
                  + '    <ul style="margin: 0 0 0.5rem 1.1rem; padding: 0;">'
                  + '      <li style="margin-bottom:0.2rem;"><strong style="color:#cbd5e1;">Conversion rate per path.</strong> Share of the exposed group in that path who went on to visit the ' + escapeHtml(bfl.toLowerCase()) + ' page.</li>'
                  + '      <li style="margin-bottom:0.2rem;"><strong style="color:#cbd5e1;">Lift vs baseline.</strong> Path conversion rate divided by the campaign\\u2019s baseline conversion rate. 1.5x means viewers on that path converted at 1.5 times the campaign average.</li>'
                  + '      <li style="margin-bottom:0.2rem;"><strong style="color:#cbd5e1;">Co-exposure percentage.</strong> Of the viewers exposed to the ROW touchpoint, what share were also exposed to the COLUMN touchpoint. Reading the diagonal is a check: it always sits at 100% by construction.</li>'
                  + '      <li><strong style="color:#cbd5e1;">Coefficient / lift.</strong> Marginal log-odds contribution of one more exposure to that touchpoint. The odds ratio in the details table is the same number in an easier form: an odds ratio of 1.20 means one more exposure makes the viewer 1.20x more likely to convert.</li>'
                  + '    </ul>'
                  + '    <p style="margin: 0.55rem 0 0 0; padding: 0.4rem 0.55rem; background: rgba(234,179,8,0.10); border-left: 2px solid rgba(234,179,8,0.5); border-radius: 4px; color:#fde68a;"><strong>One caveat.</strong> Lifts read at the campaign level. Adding or removing a touchpoint will move every other lift a little, so treat the ranking as a spend prompt, not a fixed rate card.</p>'
                  + '  </div>'
                  + '</details>';

                // Top strip: three summary tiles (unchanged from v1).
                var fit = d.model_fit || {};
                var quality = String(fit.quality || 'weak');
                var qLabel = { strong: 'Strong read', moderate: 'Moderate read', weak: 'Weak read' }[quality] || 'Weak read';
                var qColor = { strong: '#C7F23E', moderate: '#9FD628', weak: '#94a3b8' }[quality] || '#94a3b8';
                var qBg    = { strong: 'rgba(199,242,62,0.14)', moderate: 'rgba(159,214,40,0.14)', weak: 'rgba(148,163,184,0.14)' }[quality] || 'rgba(148,163,184,0.14)';
                var qChip  = '<span style="display:inline-block; padding:0.1rem 0.55rem; font-size:0.68rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; background:' + qBg + '; color:' + qColor + ';">' + qLabel + '</span>';
                var tiles = [
                    iiqSummaryTile('Baseline conversion rate', _iiqMTAFmtPct(d.conversion_rate, 2), 'share of exposed viewers who visited the ' + bfl.toLowerCase() + ' page in the attribution window'),
                    iiqSummaryTile('Exposed viewers', fmtCompact(d.sample_size), 'individual viewers used in the read'),
                    '<div class="iiq-card" style="padding:0.6rem 0.75rem; display:flex; flex-direction:column; justify-content:space-between;">'
                      + '<div class="iiq-card-meta" style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.5px; opacity:0.7;">Read strength</div>'
                      + '<div style="margin-top:0.35rem;">' + qChip + '</div>'
                      + '<div class="iiq-card-meta" style="margin-top:0.4rem; font-size:0.7rem; opacity:0.7;">' + (fit.convergence ? 'stable across passes' : 'unstable pass') + '</div>'
                    + '</div>'
                ].join('');
                var topStrip = '<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; margin-bottom: 0.85rem;">' + tiles + '</div>';

                // Card wrappers. Each of the three cards renders inside its
                // own container so future tweaks (drag-to-reorder, per-card
                // filters, etc.) stay local.
                var journeysHost = 'iiqMTAJourneysCard';
                var coExpHost    = 'iiqMTACoExpCard';
                var coefHost     = 'iiqMTACoefCard';

                var journeysCard =
                    '<section id="' + journeysHost + '" style="margin-top:0.75rem;">' + _iiqMTARenderJourneysCard(d, journeysHost) + '</section>';
                var coExpCard =
                    '<section id="' + coExpHost + '" style="margin-top:1.1rem;">' + _iiqMTARenderCoExposureCard(d) + '</section>';
                var coefCard =
                    '<section id="' + coefHost + '" style="margin-top:1.1rem;">' + _iiqMTARenderCoefficientCard(d) + '</section>';

                host.innerHTML = howToRead + topStrip + journeysCard + coExpCard + coefCard;
            }"""


def splice(src: str, old: str, new: str) -> str:
    count = src.count(old)
    if count == 0:
        raise SystemExit("splice: anchor block not found in templates/index.html")
    if count > 1:
        raise SystemExit(f"splice: anchor block matched {count} times (needs unique context)")
    return src.replace(old, new)


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    updated = splice(src, OLD, NEW)
    INDEX.write_text(updated, encoding="utf-8")
    before = len(src)
    after = len(updated)
    print(f"[splice_mta_v2_cards] backup: {BACKUP}")
    print(f"[splice_mta_v2_cards] bytes: {before:,} -> {after:,} (delta {after - before:+,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
