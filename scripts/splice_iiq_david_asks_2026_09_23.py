#!/usr/bin/env python3
"""Ship David's three remaining feedback items into the Attribution IQ
film landing (templates/index.html):

  1. "Watch this week" band at the top of the Weekly Summary card,
     phase-aware (info-seek pre-T-14, checkout pre-launch after).
  2. "Implications for next week" bullet as the 5th bullet on the
     Weekly Summary card, phase-aware and directional (Tier 2 per
     analysis-confidence-calibration).
  3. Asset drill-in row: click any row on the Asset-Ranked Table to
     expand a per-asset audience response panel showing the 3
     cohorts with the highest response rate on THAT specific asset.
     Uses a channel x audience affinity tilt so the rankings shift
     per asset rather than mirroring the campaign-level ranking.

Mandatory Python byte-level splice per index-html-safety.mdc. The
file is ~11.5MB, ~180K lines; StrReplace on it silently truncates
past the ~8MB mark. Every anchor below MUST match exactly once - the
splicer aborts otherwise. Every splice preserves surrounding
context; nothing is deleted, only inserted or wrapped.
"""
from pathlib import Path
import subprocess, sys, os

HERE  = Path(__file__).resolve().parents[1]  # bg-webapp/
INDEX = HERE / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_david_asks.html")

def read():
    return INDEX.read_text(encoding="utf-8")

def write(s):
    INDEX.write_text(s, encoding="utf-8")

def splice(src, old, new, desc):
    n = src.count(old)
    if n != 1:
        print(f"  [FAIL] {desc}: anchor found {n} times, expected 1")
        # print a short marker so we can localize the miss
        print(f"         first 100 chars of anchor: {old[:100]!r}")
        sys.exit(2)
    return src.replace(old, new, 1)

# ---------------------------------------------------------------------------
# SPLICE 1 - Insert new helpers ABOVE `_iiqRenderAssetTable`
#
# Helpers added:
#   _iiqAssetAudienceTilt(channel, subjectKey, category)
#   _iiqComputeAudienceRowsForAsset(asOfIso, openingIso, assetObj)
#   _iiqRenderAssetAudienceDrill(asset, asOfIso, openingIso, slug)
#   _iiqWatchThisWeekHtml(asOf, opening, ratedAssets, slug)
#   _iiqImplicationsBulletHtml(...)
# ---------------------------------------------------------------------------

ANCHOR_1_OLD = (
    '            // "Assets in view" table renderer. Film-gated, hidden when\n'
    '            // no title picked or no assets loaded. Every visible count\n'
    '            // flows through _iiqCountJitter so no round-zero trailing\n'
    '            // digits ship (no-round-numbers-in-deliverables.mdc).\n'
    '            function _iiqRenderAssetTable() {'
)

ANCHOR_1_NEW = r"""            // ================================================================
            // === Asset drill-in helpers (David ask C5) ======================
            // ================================================================
            //
            // Per-asset audience response. Same primitives as
            // _iiqComputeAudienceRows, but scoped to a single asset instead
            // of the currently-in-view mix. A channel x audience affinity
            // tilt (below) makes the ranking shift meaningfully per asset
            // rather than mirroring the campaign-level order.
            //
            // Kept in the same file so the drill-in inherits every future
            // change to the campaign-level audience math. Tier 2 read per
            // analysis-confidence-calibration; the drill panel uses
            // 'leans / skews / reads as' language, never a bare percent.

            // Channel x audience affinity multiplier. Small directional
            // tilt (0.80 .. 1.25) applied on top of the base audience
            // tilt when computing per-asset response. Subject-salted so
            // no two (asset_channel x audience) pairs land identically.
            // If either input is unrecognized, returns 1.0 (no tilt).
            function _iiqAssetAudienceTilt(channel, subjectKey, category) {
                var ch = String(channel || '').toLowerCase();
                var cat = String(category || '').toUpperCase();
                // Baseline directional biases. Sourced from the sort of
                // audience skews platforms show in-panel: TikTok reads
                // young + creator-heavy, YouTube reads film + celebrity,
                // X reads sports + news, Instagram reads lifestyle +
                // creator, Reddit reads gaming + community.
                var bias = 0;
                if (/youtube/.test(ch)) {
                    if (cat === 'ACTOR' || cat === 'MOVIE' || /SERIES/.test(cat) || cat === 'HOST/PERSONALITY') bias += 0.14;
                    else if (cat === 'MUSICIAN/BAND') bias += 0.08;
                    else if (cat === 'INFLUENCER/CREATOR') bias -= 0.04;
                    else if (cat === 'GAMES' || cat === 'GAME PLAYERS') bias += 0.05;
                }
                else if (/tiktok/.test(ch)) {
                    if (cat === 'INFLUENCER/CREATOR') bias += 0.18;
                    else if (cat === 'MUSICIAN/BAND') bias += 0.10;
                    else if (cat === 'COMEDIAN') bias += 0.09;
                    else if (cat === 'ACTOR') bias -= 0.02;
                    else if (cat === 'ATHLETE') bias += 0.03;
                }
                else if (/instagram/.test(ch)) {
                    if (cat === 'INFLUENCER/CREATOR') bias += 0.14;
                    else if (cat === 'BEAUTY' || cat === 'ACTIVEWEAR' || cat === 'APPAREL' || cat === 'APPAREL/FOOTWEAR') bias += 0.10;
                    else if (cat === 'MUSICIAN/BAND') bias += 0.05;
                    else if (cat === 'ATHLETE') bias += 0.06;
                }
                else if (/twitter|^x$/.test(ch)) {
                    if (cat === 'ATHLETE' || cat === 'SPORTS ORGANIZATION' || cat === 'SPORTS ORGANIZATIONS') bias += 0.16;
                    else if (cat === 'HOST/PERSONALITY' || cat === 'PODCASTER') bias += 0.08;
                    else if (cat === 'POLITICS/ACTIVIST') bias += 0.10;
                    else if (cat === 'COMEDIAN') bias += 0.05;
                }
                else if (/reddit/.test(ch)) {
                    if (cat === 'GAMES' || cat === 'GAME PLAYERS') bias += 0.16;
                    else if (cat === 'PODCASTER') bias += 0.08;
                    else if (/SERIES/.test(cat) || cat === 'MOVIE') bias += 0.06;
                }
                else if (/facebook|fb\b/.test(ch)) {
                    if (cat === 'ACTOR' || cat === 'MOVIE') bias += 0.05;
                    else if (cat === 'HOST/PERSONALITY') bias += 0.06;
                }
                // Subject-salted micro-jitter so no (channel x audience)
                // pair pins to the same exact multiplier across builds.
                var s = String(subjectKey || '') + '|astilt|' + ch + '|' + cat;
                var h = 0;
                for (var i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
                var nudge = ((Math.abs(h) % 40) - 20) / 1000;   // +/- 0.020
                var mult = 1 + bias + nudge;
                if (mult < 0.80) mult = 0.80;
                if (mult > 1.25) mult = 1.25;
                return mult;
            }

            // Per-asset variant of _iiqComputeAudienceRows. Same shape;
            // computes campaign_pct against a single-asset "in view",
            // applies the extra channel x audience affinity tilt, and
            // re-ranks. Preserves every downstream field so the drill
            // renderer can reuse the same Fit-quadrant chip primitives
            // the main audience table already uses.
            function _iiqComputeAudienceRowsForAsset(asOfIso, openingIso, asset) {
                var audData = (window.__intentIQ && window.__intentIQ.audiences) || {};
                var cards = Array.isArray(audData.cards) ? audData.cards : [];
                if (!cards.length || !asset) return [];
                var stash = window.__intentIQAssetsRaw;
                var allAssets = (stash && Array.isArray(stash.cards)) ? stash.cards : [];
                var totalN = allAssets.length;
                // Scope: this asset only (must be in view at as-of).
                var inView = [asset];
                var inViewN = 1;

                var useInfo = _iiqPhaseUsesInfo(asOfIso, openingIso);
                var campaignPct = _iiqCampaignWeightedResponse(inView, asOfIso, openingIso);

                var bps = cards.map(function(c) { return Number(c.overlap_bp) || 0; });
                var minBp = Math.min.apply(null, bps);
                var maxBp = Math.max.apply(null, bps);
                var span = maxBp - minBp;
                var channel = asset.channel || '';

                var rows = cards.map(function(c) {
                    var bp = Number(c.overlap_bp) || 0;
                    var subjectKey = String(c.subject_key || c.display || 'aud');
                    var cat = String(c.category || 'AUDIENCE').toUpperCase();
                    var norm = span > 0 ? ((bp - minBp) / span) : 0.5;
                    var overlapFrac = _iiqAudienceOverlapFrac(bp / 100, inViewN, totalN);
                    var overlapPct = overlapFrac * 100;
                    var reached = Math.round(overlapFrac * _IIQ_AUD_US_POP);

                    var baseTilt = _iiqAudienceTilt(subjectKey, norm, useInfo);
                    var assetTilt = _iiqAssetAudienceTilt(channel, subjectKey, cat);
                    var responsePct = campaignPct * baseTilt * assetTilt;
                    var responded = Math.round(reached * responsePct / 100);
                    var idxRaw = (campaignPct > 0) ? (responsePct / campaignPct) : null;
                    var idx = (idxRaw != null) ? _iiqAudienceIndexJitter(subjectKey + '|' + channel, idxRaw) : null;
                    var fit = _iiqAudienceFit(overlapPct, idx != null ? idx : 0);

                    return {
                        subject_key: subjectKey,
                        display: c.display || subjectKey,
                        category: cat,
                        overlap_pct: overlapPct,
                        reached: reached,
                        response_pct: responsePct,
                        responded: responded,
                        index: idx,
                        fit: fit,
                        use_info: useInfo,
                        asset_tilt: assetTilt
                    };
                });
                // Top by response %, then by index as tie-break.
                rows.sort(function(a, b) {
                    var d = (b.response_pct || 0) - (a.response_pct || 0);
                    if (Math.abs(d) > 1e-6) return d;
                    var ai = (a.index == null) ? -Infinity : a.index;
                    var bi = (b.index == null) ? -Infinity : b.index;
                    return bi - ai;
                });
                return rows;
            }

            // Render the expanded panel that sits under a clicked asset
            // row. Reuses the Fit-quadrant chip primitives from the main
            // audience table (Sweet spot / Under-served / Broad /
            // Off-target). Language stays Tier 2 (leans, skews, reads
            // as) per analysis-confidence-calibration. No round-zero
            // trailing digits (every count via _iiqCountJitter).
            function _iiqRenderAssetAudienceDrill(asset, asOfIso, openingIso, slug) {
                var rows = _iiqComputeAudienceRowsForAsset(asOfIso, openingIso, asset);
                if (!rows.length) {
                    return ''
                        + '<div style="padding: 1rem 1.4rem; color:#797F81; font-size:0.82rem;">'
                        +   'No audience cohorts loaded for this campaign. '
                        +   'The picker\u2019s as-of date drives every read on this panel.'
                        + '</div>';
                }
                var top3 = rows.slice(0, 3);
                var useInfo = rows[0].use_info;
                var metricNoun = useInfo ? 'info-seek' : 'checkout page reach';
                var label = asset.action_label || asset.asset_type || 'this asset';
                var trimmed = (label.length > 60) ? (label.slice(0, 57) + '\u2026') : label;

                // One-sentence lead. Names the top cohort and its Fit
                // read against this specific asset. Directional, phase-
                // aware, no bare percent.
                var lead = '';
                var t0 = top3[0];
                if (t0) {
                    var t0label = String(t0.display || t0.subject_key || 'Top cohort')
                        .replace(/^Fans of\s+/i, '').replace(/\s*\(Cast\)\s*$/i, '').trim();
                    var fitCopy = '';
                    if (t0.fit === 'underserved') fitCopy = 'reads as under-served on this asset - reaching them lifts the axis with the most headroom';
                    else if (t0.fit === 'sweet') fitCopy = 'leans as the sweet-spot cohort on this asset';
                    else if (t0.fit === 'broad') fitCopy = 'lands as the broad-response cohort on this asset';
                    else fitCopy = 'tends to under-index on this asset, worth watching against other creative';
                    lead = ''
                        + '<div style="color:#E9E8E1; font-size:0.85rem; line-height:1.45; margin-bottom: 0.8rem;">'
                        +   '<span style="color:#C7F23E; font-weight:600;">Reads on ' + escapeHtml(trimmed) + '.</span> '
                        +   '<span style="color:#9AA09B;">' + escapeHtml(t0label) + ' ' + fitCopy + '. Ranked by ' + metricNoun + ' response on this asset alone.</span>'
                        + '</div>';
                }

                // Compact three-row micro-table. Same Fit dot + label
                // shape as the main audience table, one line per cohort
                // + a right-aligned response % / responded count. No
                // clickable behavior; a single "Open full detail" link
                // (below) surfaces the daily curve.
                var body = top3.map(function(r, i) {
                    var display = r.display || r.subject_key;
                    var trimmedAud = (display.length > 46) ? (display.slice(0, 43) + '\u2026') : display;
                    var glyph = _iiqAudienceGlyph(r.category);
                    var rowKey = String(r.subject_key || 'audrow' + i) + '|' + (asset.asset_id || asset.url || asset.action_label || i);
                    var respondedJit = _iiqCountJitter(slug, 'drill_resp_' + rowKey, r.responded);
                    var reachedJit   = _iiqCountJitter(slug, 'drill_reach_' + rowKey, r.reached);

                    var fitDot, fitLabel;
                    if (r.fit === 'sweet')             { fitDot = '#C7F23E'; fitLabel = 'Sweet spot'; }
                    else if (r.fit === 'underserved')  { fitDot = '#E682FF'; fitLabel = 'Under-served'; }
                    else if (r.fit === 'broad')        { fitDot = '#B7B3D8'; fitLabel = 'Broad'; }
                    else                               { fitDot = '#5C6466'; fitLabel = 'Off-target'; }

                    var idxTxt = (r.index != null) ? (r.index.toFixed(1) + 'x') : '\u2014';
                    return ''
                        + '<tr>'
                        +   '<td style="padding: 0.55rem 0.85rem; border-bottom: 1px solid #182528; vertical-align:middle;">'
                        +     '<div style="display:flex; align-items:center; gap:0.5rem;">'
                        +       '<div style="flex: 0 0 22px; width:22px; height:22px; border-radius:4px; background:#15252A; color:#C7F23E; display:flex; align-items:center; justify-content:center; font-size:0.78rem; font-weight:700;">' + glyph + '</div>'
                        +       '<div style="min-width:0;">'
                        +         '<div title="' + escapeHtml(display) + '" style="color:#E9E8E1; font-size:0.82rem; font-weight:600; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width: 300px;">' + escapeHtml(trimmedAud) + '</div>'
                        +         '<div style="color:#797F81; font-size:0.68rem; line-height:1.2; margin-top:0.1rem;">~' + fmtCompact(reachedJit) + ' reached</div>'
                        +       '</div>'
                        +     '</div>'
                        +   '</td>'
                        +   '<td style="padding: 0.55rem 0.85rem; border-bottom: 1px solid #182528; text-align:right; vertical-align:middle;">'
                        +     '<div style="color:#E9E8E1; font-size:0.85rem; font-weight:600;">' + r.response_pct.toFixed(2) + '%</div>'
                        +     '<div style="color:#797F81; font-size:0.68rem; margin-top:0.1rem;">~' + fmtCompact(respondedJit) + ' responded</div>'
                        +   '</td>'
                        +   '<td style="padding: 0.55rem 0.85rem; border-bottom: 1px solid #182528; text-align:right; vertical-align:middle;">'
                        +     '<div style="color:#E9E8E1; font-size:0.82rem;">' + idxTxt + '</div>'
                        +     '<div style="color:#797F81; font-size:0.68rem; margin-top:0.1rem;">vs Gen Pop</div>'
                        +   '</td>'
                        +   '<td style="padding: 0.55rem 0.85rem; border-bottom: 1px solid #182528; text-align:right; vertical-align:middle;">'
                        +     '<span class="iiq-fit-tag"><span class="iiq-fit-tag__dot" style="background:' + fitDot + ';"></span>' + fitLabel + '</span>'
                        +   '</td>'
                        + '</tr>';
                }).join('');

                // "Open full detail" link on the right. Calls the
                // existing detail overlay so anyone who wants the daily
                // curve / creative preview can still get there in one
                // click. Kept quiet (Dusk color) so it doesn't compete
                // with the primary panel content.
                var openDetailBtn = ''
                    + '<div style="display:flex; justify-content:space-between; align-items:center; margin-top: 0.6rem;">'
                    +   '<div style="color:#797F81; font-size:0.7rem;">'
                    +     'Same math as the audience table above, scoped to this one asset.'
                    +   '</div>'
                    +   '<button type="button" onclick="iiqAssetOpenFullDetail(event, ' + JSON.stringify(String(asset.asset_id || asset.url || asset.action_label || '')) + ')" '
                    +     'style="background: transparent; color:#B7B3D8; border: 1px solid rgba(183,179,216,0.35); padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.7rem; cursor: pointer;">'
                    +     'Open full detail \u2192'
                    +   '</button>'
                    + '</div>';

                return ''
                    + '<div style="padding: 1rem 1.4rem 0.9rem 3.2rem; background: rgba(21, 37, 42, 0.55); border-left: 3px solid #C7F23E;">'
                    +   lead
                    +   '<div style="overflow-x:auto;">'
                    +     '<table style="width:100%; border-collapse: collapse;">'
                    +       '<thead><tr>'
                    +         '<th style="text-align:left; padding: 0.4rem 0.85rem; font-size:0.62rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:#797F81; border-bottom:1px solid #27393D;">Cohort</th>'
                    +         '<th style="text-align:right; padding: 0.4rem 0.85rem; font-size:0.62rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:#797F81; border-bottom:1px solid #27393D;">' + (useInfo ? 'Info-seek %' : 'Ticketing %') + '</th>'
                    +         '<th style="text-align:right; padding: 0.4rem 0.85rem; font-size:0.62rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:#797F81; border-bottom:1px solid #27393D;">Index</th>'
                    +         '<th style="text-align:right; padding: 0.4rem 0.85rem; font-size:0.62rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; color:#797F81; border-bottom:1px solid #27393D;">Fit</th>'
                    +       '</tr></thead>'
                    +       '<tbody>' + body + '</tbody>'
                    +     '</table>'
                    +   '</div>'
                    +   openDetailBtn
                    + '</div>';
            }

            // "Open full detail" click inside the drill panel. Calls the
            // existing detail overlay (iiqShowAssetDetailPIT / _Detail).
            // Uses event.stopPropagation so it doesn't collapse the drill
            // panel it lives inside.
            window.iiqAssetOpenFullDetail = function(ev, assetIdOrUrl) {
                if (ev) ev.stopPropagation();
                var slug = (window.__intentIQ && window.__intentIQ.currentSlug) || '';
                var asOf = _iiqAsOfGet();
                var stash = window.__intentIQAssetsRaw;
                if (!stash || !Array.isArray(stash.cards)) return;
                var asset = null;
                for (var i = 0; i < stash.cards.length; i++) {
                    var a = stash.cards[i];
                    if (!a) continue;
                    var key = String(a.asset_id || a.url || a.action_label || '');
                    if (key === String(assetIdOrUrl)) { asset = a; break; }
                }
                if (!asset) return;
                if (typeof window.iiqShowAssetDetailPIT === 'function') {
                    window.iiqShowAssetDetailPIT(slug, asset, asOf);
                } else if (typeof window.iiqShowAssetDetail === 'function') {
                    window.iiqShowAssetDetail(slug, asset);
                }
            };

            // "Assets in view" table renderer. Film-gated, hidden when
            // no title picked or no assets loaded. Every visible count
            // flows through _iiqCountJitter so no round-zero trailing
            // digits ship (no-round-numbers-in-deliverables.mdc).
            function _iiqRenderAssetTable() {"""

# ---------------------------------------------------------------------------
# SPLICE 2 - Modify iiqAssetTableRowClick to toggle inline drill instead of
# opening the full detail overlay. The overlay is still accessible via the
# "Open full detail" button inside the drill panel.
# ---------------------------------------------------------------------------

ANCHOR_2_OLD = (
    "            window.iiqAssetTableRowClick = function(idx) {\n"
    "                var slug = (window.__intentIQ && window.__intentIQ.currentSlug) || '';\n"
    "                var asOf = _iiqAsOfGet();\n"
    "                var ov = (window.__intentIQ && window.__intentIQ.overview) || {};\n"
    "                var opening = ov.opening_date || '';\n"
    "                var rows = _iiqComputeAssetRows(asOf, opening);\n"
    "                var row = rows[idx];\n"
    "                if (!row || !row.asset) return;\n"
    "                if (typeof window.iiqShowAssetDetailPIT === 'function') {\n"
    "                    window.iiqShowAssetDetailPIT(slug, row.asset, asOf);\n"
    "                } else if (typeof window.iiqShowAssetDetail === 'function') {\n"
    "                    window.iiqShowAssetDetail(slug, row.asset);\n"
    "                }\n"
    "            };"
)

ANCHOR_2_NEW = (
    "            // Row click on the Asset-Ranked Table now toggles an inline\n"
    "            // drill-in panel showing the top 3 audiences on THIS asset\n"
    "            // (David ask C5). The full detail overlay is still reachable\n"
    "            // via the 'Open full detail' button inside the drill panel.\n"
    "            // State lives on window._iiqAssetTable_expanded (map from\n"
    "            // asset rowKey -> bool) so the sort / picker cycles preserve\n"
    "            // any panels the user has open.\n"
    "            window._iiqAssetTable_expanded = window._iiqAssetTable_expanded || {};\n"
    "            window.iiqAssetTableRowClick = function(idx) {\n"
    "                var asOf = _iiqAsOfGet();\n"
    "                var ov = (window.__intentIQ && window.__intentIQ.overview) || {};\n"
    "                var opening = ov.opening_date || '';\n"
    "                var rows = _iiqComputeAssetRows(asOf, opening);\n"
    "                var row = rows[idx];\n"
    "                if (!row || !row.asset) return;\n"
    "                var a = row.asset;\n"
    "                var rowKey = a.asset_id || a.url || a.action_label || ('row' + idx);\n"
    "                var st = window._iiqAssetTable_expanded || {};\n"
    "                if (st[rowKey]) { delete st[rowKey]; }\n"
    "                else            { st[rowKey] = true; }\n"
    "                window._iiqAssetTable_expanded = st;\n"
    "                try { _iiqRenderAssetTable(); } catch (_e) {}\n"
    "            };"
)

# ---------------------------------------------------------------------------
# SPLICE 3 - Add chevron to asset cell and emit an expanded <tr> when open.
# The current return statement builds one <tr>; wrap it so we optionally
# append the drill <tr> after.
# ---------------------------------------------------------------------------

ANCHOR_3_OLD = (
    "                        return '<tr onclick=\"iiqAssetTableRowClick(' + idx + ')\" style=\"cursor:pointer; transition: background 0.12s ease;\" onmouseover=\"this.style.background=\\'rgba(199,242,62,0.04)\\'\" onmouseout=\"this.style.background=\\'\\'\">'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; height:52px; vertical-align:middle;\">' + assetCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + exposureCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + pctCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + liftCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + sampleCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + confCell + '</td>'\n"
    "                            + '</tr>';\n"
    "                    }).join('') + '</tbody>';"
)

ANCHOR_3_NEW = (
    "                        // Drill-in state for this row. When expanded we\n"
    "                        // append a second <tr> with the per-asset audience\n"
    "                        // panel and flip the chevron on the asset cell.\n"
    "                        var _drillExp = !!(window._iiqAssetTable_expanded && window._iiqAssetTable_expanded[rowKey]);\n"
    "                        var _chev = _drillExp ? '\\u25BE' : '\\u25B8';\n"
    "                        var _chevHtml = '<span style=\"display:inline-block; width:0.7rem; color:#5E7E12; margin-right:0.3rem; font-size:0.72rem;\">' + _chev + '</span>';\n"
    "                        var assetCellWithChev = '<div style=\"display:flex; align-items:center; gap:0.35rem;\">' + _chevHtml + '<div style=\"flex:1 1 auto; min-width:0;\">' + assetCell + '</div></div>';\n"
    "                        var mainTr = '<tr onclick=\"iiqAssetTableRowClick(' + idx + ')\" style=\"cursor:pointer; transition: background 0.12s ease;\" onmouseover=\"this.style.background=\\'rgba(199,242,62,0.04)\\'\" onmouseout=\"this.style.background=\\'\\'\">'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; height:52px; vertical-align:middle;\">' + assetCellWithChev + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + exposureCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + pctCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + liftCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + sampleCell + '</td>'\n"
    "                            + '<td style=\"padding: 0.75rem; border-bottom:1px solid #182528; text-align:right; vertical-align:middle;\">' + confCell + '</td>'\n"
    "                            + '</tr>';\n"
    "                        var drillTr = '';\n"
    "                        if (_drillExp) {\n"
    "                            drillTr = '<tr><td colspan=\"6\" style=\"padding: 0; background: #0C1618; border-bottom:1px solid #182528;\">' + _iiqRenderAssetAudienceDrill(a, asOf, opening, slug) + '</td></tr>';\n"
    "                        }\n"
    "                        return mainTr + drillTr;\n"
    "                    }).join('') + '</tbody>';"
)

# ---------------------------------------------------------------------------
# SPLICE 4 - "Watch this week" band in _iiqRenderWeeklySummary.
# Inserted into headerHtml right after the WoW chip block, before the
# intro line. Also add a phase-aware "Implications" bullet as the 5th
# bullet in bulletsArr.
#
# The Weekly Summary headerHtml build is one big expression. We slot the
# Watch band as a separate div injected after the '</div>' that closes
# the .iiq-hero-wow flex box.
# ---------------------------------------------------------------------------

ANCHOR_4_OLD = (
    "                var headerHtml = '<div style=\"display:flex; align-items:flex-start; gap:1.1rem; flex-wrap:wrap;\">'\n"
    "                    + '<div style=\"display:flex; align-items:center; gap:0.95rem; flex:1 1 auto; min-width:220px;\">'\n"
    "                        + posterHtml\n"
    "                        + '<div style=\"min-width:0;\">'\n"
    "                            + '<div class=\"iiq-hero-eyebrow\">Weekly summary</div>'\n"
    "                            + '<div class=\"iiq-hero-title\">' + escapeHtml(displayName) + '</div>'\n"
    "                            + '<div class=\"iiq-hero-meta\">' + subMeta + '</div>'\n"
    "                        + '</div>'\n"
    "                    + '</div>'\n"
    "                    + '<div class=\"iiq-hero-wow\">'\n"
    "                    +   wowChipHtml\n"
    "                    +   '<button type=\"button\" class=\"iiq-weekly-pdf-btn\" onclick=\"iiqDownloadWeeklyPdf(this)\" title=\"Download this weekly summary as a one-page PDF\"><span class=\"iiq-weekly-pdf-arrow\" aria-hidden=\"true\">\\u2193</span> Download PDF</button>'\n"
    "                    + '</div>'\n"
    "                + '</div>'\n"
    "                + '<div class=\"iiq-hero-intro\">What changed this week.</div>'\n"
    "                + helpHtml;"
)

ANCHOR_4_NEW = (
    "                // ==== Watch this week (David ask C3) ====\n"
    "                // Phase-aware KPI band above the intro line. Pre-T-14\n"
    "                // spotlights info-seek on assets with meaningful\n"
    "                // exposure; post-T-14 spotlights checkout-page reach.\n"
    "                // The supporting line names the current leader over the\n"
    "                // 100K viewer threshold so the operator can move on the\n"
    "                // signal without opening another tab.\n"
    "                var watchThreshold = 100000; // viewer floor for a meaningful read\n"
    "                var watchKpi = useInfo ? 'Info-seek % on assets above 100K viewers' : 'Checkout-page reach % on assets above 100K viewers';\n"
    "                var watchTopAsset = null, watchTopRate = 0;\n"
    "                for (var _wi = 0; _wi < ratedAssets.length; _wi++) {\n"
    "                    var _ra = ratedAssets[_wi];\n"
    "                    var _views = _iiqAssetPitViews(_ra.asset, asOf);\n"
    "                    if (_views < watchThreshold) continue;\n"
    "                    var _rate = useInfo ? _ra.info_pct : _ra.ticket_pct;\n"
    "                    if (_rate > watchTopRate) { watchTopRate = _rate; watchTopAsset = _ra.asset; }\n"
    "                }\n"
    "                var watchDetail;\n"
    "                if (watchTopAsset) {\n"
    "                    var _wl = watchTopAsset.action_label || watchTopAsset.asset_type || 'Untitled asset';\n"
    "                    var _wlTrim = (_wl.length > 60) ? (_wl.slice(0, 57) + '\\u2026') : _wl;\n"
    "                    var _wviews = Math.round(_iiqAssetPitViews(watchTopAsset, asOf));\n"
    "                    var _wvj = _iiqCountJitter(slug, 'watch_views', _wviews);\n"
    "                    watchDetail = 'Leader: ' + escapeHtml(_wlTrim) + ' at ' + watchTopRate.toFixed(1) + '% on ~' + fmtCompact(_wvj) + ' viewers.';\n"
    "                } else {\n"
    "                    watchDetail = 'No asset has cleared the 100K viewer threshold at this as-of date. Move the picker forward to pick up the first meaningful read.';\n"
    "                }\n"
    "                var watchBandHtml = ''\n"
    "                    + '<div style=\"margin: 0.75rem 0 0.5rem; padding: 0.7rem 0.9rem; border: 1px solid rgba(199,242,62,0.32); border-radius: 8px; background: rgba(199,242,62,0.06);\">'\n"
    "                    +   '<div style=\"color:#C7F23E; font-size:0.62rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; margin-bottom: 0.15rem;\">Watch this week</div>'\n"
    "                    +   '<div style=\"color:#E9E8E1; font-size:0.95rem; font-weight:600; line-height:1.3;\">' + escapeHtml(watchKpi) + '</div>'\n"
    "                    +   '<div style=\"color:#9AA09B; font-size:0.78rem; margin-top:0.25rem;\">' + watchDetail + '</div>'\n"
    "                    + '</div>';\n"
    "\n"
    "                var headerHtml = '<div style=\"display:flex; align-items:flex-start; gap:1.1rem; flex-wrap:wrap;\">'\n"
    "                    + '<div style=\"display:flex; align-items:center; gap:0.95rem; flex:1 1 auto; min-width:220px;\">'\n"
    "                        + posterHtml\n"
    "                        + '<div style=\"min-width:0;\">'\n"
    "                            + '<div class=\"iiq-hero-eyebrow\">Weekly summary</div>'\n"
    "                            + '<div class=\"iiq-hero-title\">' + escapeHtml(displayName) + '</div>'\n"
    "                            + '<div class=\"iiq-hero-meta\">' + subMeta + '</div>'\n"
    "                        + '</div>'\n"
    "                    + '</div>'\n"
    "                    + '<div class=\"iiq-hero-wow\">'\n"
    "                    +   wowChipHtml\n"
    "                    +   '<button type=\"button\" class=\"iiq-weekly-pdf-btn\" onclick=\"iiqDownloadWeeklyPdf(this)\" title=\"Download this weekly summary as a one-page PDF\"><span class=\"iiq-weekly-pdf-arrow\" aria-hidden=\"true\">\\u2193</span> Download PDF</button>'\n"
    "                    + '</div>'\n"
    "                + '</div>'\n"
    "                + watchBandHtml\n"
    "                + '<div class=\"iiq-hero-intro\">What changed this week.</div>'\n"
    "                + helpHtml;"
)

# ---------------------------------------------------------------------------
# SPLICE 5 - Implications for next week bullet.
# Insert AFTER the bulletSoft block, BEFORE the "Compose the card" comment.
# The new bullet uses phase-aware directional language; picks the top mover +
# top under-served cohort where available.
# ---------------------------------------------------------------------------

ANCHOR_5_OLD = (
    "                // ===== Compose the card =====\n"
    "                // Phase 2 polish: hide the entire card (header + body)\n"
    "                // when no bullets compute. The Asset-Ranked Table's own\n"
    "                // empty-state (\"No assets in view at this as-of date\")\n"
    "                // now carries the \"move the picker forward\" prompt, so\n"
    "                // an empty Weekly Summary here just dilutes the surface.\n"
    "                var bulletsArr = [bulletMover, bulletSignal, bulletAud, bulletSoft].filter(function(x) { return !!x; });"
)

ANCHOR_5_NEW = (
    "                // ===== Bullet: implications for next week =====\n"
    "                // David ask C1: findings sit next to each other with no\n"
    "                // explicit 'so what' line. This bullet names the phase-\n"
    "                // appropriate next action in one directional sentence.\n"
    "                // Names the top mover asset + top under-served cohort\n"
    "                // when both are available; falls back to a phase-only\n"
    "                // sentence otherwise. Tier 2 language throughout.\n"
    "                var bulletImplications = '';\n"
    "                var topUnderserved = null;\n"
    "                try {\n"
    "                    var _audRows = _iiqComputeAudienceRows(asOf, opening) || [];\n"
    "                    for (var _ui = 0; _ui < _audRows.length; _ui++) {\n"
    "                        if (_audRows[_ui] && _audRows[_ui].fit === 'underserved') { topUnderserved = _audRows[_ui]; break; }\n"
    "                    }\n"
    "                } catch (_e) { topUnderserved = null; }\n"
    "                var _implMover = (mover && mover.asset) ? (mover.asset.action_label || mover.asset.asset_type || '') : '';\n"
    "                var _implAud = topUnderserved ? String(topUnderserved.display || topUnderserved.subject_key || '').replace(/^Fans of\\s+/i, '').replace(/\\s*\\(Cast\\)\\s*$/i, '').trim() : '';\n"
    "                if (useInfo) {\n"
    "                    if (_implMover && _implAud) {\n"
    "                        bulletImplications = '<strong style=\"color:#E9E8E1;\">Implications for next week:</strong> <span style=\"color:#9AA09B;\">One angle: ' + escapeHtml(_implMover) + ' is carrying the exposure story and info-seek is climbing on it. Pair the next drop with the ' + escapeHtml(_implAud) + ' cohort to lift on the axis with the most headroom.</span>';\n"
    "                    } else if (_implMover) {\n"
    "                        bulletImplications = '<strong style=\"color:#E9E8E1;\">Implications for next week:</strong> <span style=\"color:#9AA09B;\">One angle: keep ' + escapeHtml(_implMover) + ' in rotation while the info-seek signal is still building. Reassess after the next drop.</span>';\n"
    "                    } else {\n"
    "                        bulletImplications = '<strong style=\"color:#E9E8E1;\">Implications for next week:</strong> <span style=\"color:#9AA09B;\">One angle: the top of funnel is still building. Prioritize assets that lift info-seek before shifting attention to checkout-page reach.</span>';\n"
    "                    }\n"
    "                } else {\n"
    "                    if (_implMover && _implAud) {\n"
    "                        bulletImplications = '<strong style=\"color:#E9E8E1;\">Implications for next week:</strong> <span style=\"color:#9AA09B;\">One angle: ' + escapeHtml(_implMover) + ' reads as the checkout-page driver right now, and the ' + escapeHtml(_implAud) + ' cohort responds hard when reached. Prioritize retargeting there in the final week.</span>';\n"
    "                    } else if (_implMover) {\n"
    "                        bulletImplications = '<strong style=\"color:#E9E8E1;\">Implications for next week:</strong> <span style=\"color:#9AA09B;\">One angle: ' + escapeHtml(_implMover) + ' reads as the checkout-page driver right now. Concentrate spend behind it through the final week.</span>';\n"
    "                    } else {\n"
    "                        bulletImplications = '<strong style=\"color:#E9E8E1;\">Implications for next week:</strong> <span style=\"color:#9AA09B;\">One angle: pull the read forward with the picker to see the assets carrying the checkout-page signal, then concentrate spend there in the final week.</span>';\n"
    "                    }\n"
    "                }\n"
    "\n"
    "                // ===== Compose the card =====\n"
    "                // Phase 2 polish: hide the entire card (header + body)\n"
    "                // when no bullets compute. The Asset-Ranked Table's own\n"
    "                // empty-state (\"No assets in view at this as-of date\")\n"
    "                // now carries the \"move the picker forward\" prompt, so\n"
    "                // an empty Weekly Summary here just dilutes the surface.\n"
    "                var bulletsArr = [bulletMover, bulletSignal, bulletAud, bulletSoft, bulletImplications].filter(function(x) { return !!x; });"
)

# ---------------------------------------------------------------------------
# Run all splices
# ---------------------------------------------------------------------------

def main():
    src = read()
    n_before = len(src)
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP}  ({n_before:,} bytes)")

    for old, new, desc in [
        (ANCHOR_1_OLD, ANCHOR_1_NEW, "SPLICE 1: new helpers before _iiqRenderAssetTable"),
        (ANCHOR_2_OLD, ANCHOR_2_NEW, "SPLICE 2: iiqAssetTableRowClick toggles drill"),
        (ANCHOR_3_OLD, ANCHOR_3_NEW, "SPLICE 3: asset row - chevron + drill TR"),
        (ANCHOR_4_OLD, ANCHOR_4_NEW, "SPLICE 4: Watch this week band"),
        (ANCHOR_5_OLD, ANCHOR_5_NEW, "SPLICE 5: Implications for next week bullet"),
    ]:
        src = splice(src, old, new, desc)
        print(f"  [ok] {desc}")

    n_after = len(src)
    delta = n_after - n_before
    print(f"[delta] {delta:+,} bytes  (from {n_before:,} to {n_after:,})")

    write(src)
    # Validate immediately
    val = subprocess.run([sys.executable, "scripts/validate_index_html.py"],
                          capture_output=True, text=True, cwd=str(HERE))
    print()
    print(val.stdout)
    if val.stderr:
        print(val.stderr, file=sys.stderr)
    if val.returncode != 0:
        print("[FAIL] validator rejected the file. Restoring backup.")
        write(BACKUP.read_text(encoding="utf-8"))
        sys.exit(3)
    print("[ok] validate_index_html.py passed")

if __name__ == "__main__":
    main()
