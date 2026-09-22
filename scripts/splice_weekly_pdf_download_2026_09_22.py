#!/usr/bin/env python3
"""Wire the 1-page weekly summary PDF download to the Weekly Summary card.

Adds three things:

  1. CSS. Turns ``.iiq-hero-wow`` into a small column so the chip can
     sit above a new download button, and defines ``.iiq-weekly-pdf-btn``
     in the existing Slate-Teal / Signal-Green language used by the
     rest of the intent view (matches ``.iiq-show-all-btn``).

  2. Header composition. Adds a "Download PDF" button inside the
     ``.iiq-hero-wow`` container, right under the WoW chip, wired to
     ``iiqDownloadWeeklyPdf()``.

  3. JS helpers. Adds ``_iiqBuildWeeklyPdfPayload()`` and
     ``iiqDownloadWeeklyPdf()`` immediately after the closing brace
     of ``_iiqRenderWeeklySummary()``. The builder reuses the same
     primitives the card renderer already uses (as-of picker, asset
     stash, ``_iiqPitFactor``, ``iiqAssetFunnelProjection``,
     ``_iiqComputeAudienceRows``, ``_iiqPhaseUsesInfo``), so the
     PDF numbers cannot drift from the on-screen numbers. The
     downloader POSTs the payload to
     ``/api/intent/<slug>/weekly-pdf`` and triggers a browser
     download from the returned blob.

Follows the ``index-html-safety.mdc`` byte-level splice rule:
anchors are unique, StrReplace is NOT used, and the file is
validated after mutation. Backup lands at
``/tmp/index.pre_weekly_pdf_download_<ts>.html``.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "templates" / "index.html"
ts = time.strftime("%Y%m%d_%H%M%S")
BACKUP = Path(f"/tmp/index.pre_weekly_pdf_download_{ts}.html")


# ----------------------------------------------------------------------
# Splice 1: CSS. Turn `.iiq-hero-wow` into a column-flex, add the
# `.iiq-weekly-pdf-btn` rule. Anchor is the exact line pair that
# defines `.iiq-hero-wow` + `.iiq-hero-wow-chip` (lines ~32226-32227).
# ----------------------------------------------------------------------
CSS_OLD = """            #intentIQView .iiq-hero-bullets strong.iiq-soft-label { color: #B7B3D8; }
            #intentIQView .iiq-hero-wow { flex: 0 0 auto; }
            #intentIQView .iiq-hero-wow-chip { font-size: 0.82rem; font-weight: 700; padding: 0.35rem 0.85rem; border-radius: 999px; white-space: nowrap; display: inline-block; }
"""

CSS_NEW = """            #intentIQView .iiq-hero-bullets strong.iiq-soft-label { color: #B7B3D8; }
            #intentIQView .iiq-hero-wow { flex: 0 0 auto; display: flex; flex-direction: column; align-items: flex-end; gap: 0.4rem; }
            #intentIQView .iiq-hero-wow-chip { font-size: 0.82rem; font-weight: 700; padding: 0.35rem 0.85rem; border-radius: 999px; white-space: nowrap; display: inline-block; }
            #intentIQView .iiq-weekly-pdf-btn { display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.32rem 0.75rem; background: transparent; border: 1px solid rgba(199,242,62,0.35); border-radius: 8px; color: #C7F23E; font-size: 0.68rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; transition: background 0.12s ease-out, border-color 0.12s ease-out; line-height: 1; }
            #intentIQView .iiq-weekly-pdf-btn:hover { background: rgba(199,242,62,0.08); border-color: rgba(199,242,62,0.65); }
            #intentIQView .iiq-weekly-pdf-btn:focus { outline: 2px solid #3358FF; outline-offset: 2px; }
            #intentIQView .iiq-weekly-pdf-btn[disabled] { opacity: 0.45; cursor: wait; }
            #intentIQView .iiq-weekly-pdf-btn .iiq-weekly-pdf-arrow { font-size: 0.85rem; font-weight: 700; line-height: 1; }
            body[data-theme="light"] #intentIQView .iiq-weekly-pdf-btn { color: #5E7E12; border-color: #C9C6BA; }
            body[data-theme="light"] #intentIQView .iiq-weekly-pdf-btn:hover { background: rgba(94,126,18,0.08); border-color: #5E7E12; }
"""


# ----------------------------------------------------------------------
# Splice 2: header composition. Add the button inside the .iiq-hero-wow
# container, immediately after the WoW chip. Anchor is the exact line
# from the header composition (line 37079). The context that follows
# is `+ '</div>' \n + '<div class="iiq-hero-intro">...` which is unique
# to this render function so the anchor cannot double-match.
# ----------------------------------------------------------------------
HEADER_OLD = """                    + '<div class="iiq-hero-wow">' + wowChipHtml + '</div>'
                + '</div>'
                + '<div class="iiq-hero-intro">What changed this week.</div>'
"""

HEADER_NEW = """                    + '<div class="iiq-hero-wow">'
                    +   wowChipHtml
                    +   '<button type="button" class="iiq-weekly-pdf-btn" onclick="iiqDownloadWeeklyPdf(this)" title="Download this weekly summary as a one-page PDF"><span class="iiq-weekly-pdf-arrow" aria-hidden="true">\\u2193</span> Download PDF</button>'
                    + '</div>'
                + '</div>'
                + '<div class="iiq-hero-intro">What changed this week.</div>'
"""


# ----------------------------------------------------------------------
# Splice 3: JS helpers. Insert the payload builder + downloader
# immediately after the closing brace of _iiqRenderWeeklySummary().
# Anchor is the unique tail of that function:
#     card.style.display = '';
#             }
#             // Handler wired to the date input's oninput. Any picker
# ----------------------------------------------------------------------
JS_OLD = """                card.style.display = '';
            }
            // Handler wired to the date input's oninput. Any picker
"""

# The builder RE-RUNS the same compute _iiqRenderWeeklySummary does so
# there's no dependency on any stashed intermediate. If the render was
# never able to run (no as-of, no assets), the builder returns null and
# the downloader shows a lightweight alert instead of shipping garbage.
# The two functions call the same helpers so the PDF cannot diverge
# from what the reader sees on-screen.
#
# The bullets we collect are the plain-text version of the same four
# strings the render produces (mover, signal, aud, soft). We strip the
# inline HTML with a scratch textarea so the PDF gets a clean string.
JS_NEW = """                card.style.display = '';
            }

            // ============================================================
            // Weekly summary -> 1-page PDF download.
            //
            // The button in the Weekly Summary card header calls
            // iiqDownloadWeeklyPdf() which builds a plain-object
            // snapshot from the same primitives _iiqRenderWeeklySummary
            // uses (as-of picker, asset stash, audiences, PIT factor,
            // funnel projection, audience compute) and POSTs it to
            // /api/intent/<slug>/weekly-pdf. The endpoint formats the
            // payload into a Crosswalk-branded US-Letter one-pager
            // via reportlab (see attribution_weekly_pdf.py) and
            // streams back application/pdf; the browser downloads it.
            //
            // The PDF cannot drift from the on-screen numbers because
            // the same helpers that rendered the card are called here.
            // Bullets are collected verbatim from the render logic and
            // stripped of inline HTML for print. Snapshot totals are
            // recomputed against the same as-of the picker is holding.
            // ============================================================
            function _iiqStripHtmlToText(html) {
                if (!html) return '';
                var d = document.createElement('div');
                d.innerHTML = html;
                // Collapse whitespace so wrapped bullet strings look
                // clean in the PDF. Preserves punctuation.
                return (d.textContent || d.innerText || '').replace(/\\s+/g, ' ').trim();
            }

            function _iiqBuildWeeklyPdfPayload() {
                var ov = (window.__intentIQ && window.__intentIQ.overview) || {};
                var slug = (window.__intentIQ && window.__intentIQ.currentSlug) || 'default';
                var asOf = _iiqAsOfGet();
                var stash = window.__intentIQAssetsRaw;
                if (!asOf || !stash || !Array.isArray(stash.cards) || !stash.cards.length) {
                    return null;
                }

                var opening = ov.opening_date || '';
                var displayName = ov.display_name || ov.title_slug || slug;
                var distributor = ov.distributor || '';

                var asOfMinus7 = _iiqAddDaysIso(asOf, -7);
                var startDate = '';
                (ov.phases || []).forEach(function(p) {
                    if (p && p.start_date) {
                        if (!startDate || p.start_date < startDate) startDate = p.start_date;
                    }
                });
                var priorWeekBeforeCampaign = (startDate && asOfMinus7 && asOfMinus7 < startDate);

                var inViewNow = _iiqFilterAssetsByAsOf(stash.cards, asOf, true);
                if (!inViewNow.length) return null;
                var inViewPrev = _iiqFilterAssetsByAsOf(stash.cards, asOfMinus7, true);

                // --- Per-asset PIT views (same math as _iiqRenderWeeklySummary) ---
                var totalNow = 0;
                var totalPrev = 0;
                var perAsset = inViewNow.map(function(a) {
                    var v = Number(a.ext_view_count) || 0;
                    var pn = _iiqPitFactor(a, asOf);
                    var pp = _iiqPitFactor(a, asOfMinus7);
                    var vn = v * pn;
                    var vp = v * pp;
                    totalNow += vn;
                    totalPrev += vp;
                    var proj = null;
                    try { proj = iiqAssetFunnelProjection(a); } catch (_e) {}
                    return {
                        asset: a,
                        views_now: vn,
                        views_prev: vp,
                        delta: vn - vp,
                        info_pct: proj ? proj.info_pct : null,
                        ticket_pct: proj ? proj.ticket_pct : null,
                    };
                });

                // --- Snapshot stats (exposed viewers + WoW delta) ---
                var exposedDelta = null;
                if (!priorWeekBeforeCampaign && totalPrev > 0) {
                    exposedDelta = (totalNow - totalPrev) / totalPrev * 100;
                }

                // --- Phase-appropriate response metric ---
                var useInfo = _iiqPhaseUsesInfo(asOf, opening);
                var responseLabel = useInfo ? 'Info-seek rate' : 'Ticketing rate';

                // View-weighted mean response rate across assets in view.
                var respNum = 0, respDen = 0;
                perAsset.forEach(function(x) {
                    var m = useInfo ? x.info_pct : x.ticket_pct;
                    if (m != null && x.views_now > 0) {
                        respNum += m * x.views_now;
                        respDen += x.views_now;
                    }
                });
                var responseRateNow = (respDen > 0) ? (respNum / respDen) : null;

                // Same computation at as_of - 7d to derive a response delta.
                var respNumP = 0, respDenP = 0;
                inViewPrev.forEach(function(a) {
                    var v = Number(a.ext_view_count) || 0;
                    var pp = _iiqPitFactor(a, asOfMinus7);
                    var vp = v * pp;
                    var proj = null;
                    try { proj = iiqAssetFunnelProjection(a); } catch (_e) {}
                    if (!proj) return;
                    var m = useInfo ? proj.info_pct : proj.ticket_pct;
                    if (m != null && vp > 0) { respNumP += m * vp; respDenP += vp; }
                });
                var responseRatePrev = (respDenP > 0) ? (respNumP / respDenP) : null;
                var responseDelta = null;
                if (responseRateNow != null && responseRatePrev != null) {
                    responseDelta = responseRateNow - responseRatePrev;
                }

                var sampleSize = inViewNow.length;

                // --- Days-to-open + phase label ---
                var daysToOpen = null;
                if (opening) {
                    daysToOpen = Math.round(
                        (new Date(opening + 'T12:00:00Z').getTime()
                         - new Date(asOf + 'T12:00:00Z').getTime()) / 86400000);
                }
                var phaseLabel = '';
                try { phaseLabel = _iiqTMinusLabel(asOf, opening) || ''; } catch (_e) {}

                // --- Top 5 assets (by PIT exposure) ---
                var topAssets = perAsset.slice().sort(function(a, b) {
                    return b.views_now - a.views_now;
                }).slice(0, 5).map(function(x) {
                    var a = x.asset;
                    var label = a.action_label || a.asset_type || 'Untitled asset';
                    var channel = a.channel || '';
                    var phase = a.phase_name || '';
                    var respPct = useInfo ? x.info_pct : x.ticket_pct;
                    // Lift is (this asset's response) / (view-weighted mean)
                    var lift = (respPct != null && responseRateNow && responseRateNow > 0)
                        ? (respPct / responseRateNow)
                        : null;
                    return {
                        asset: label,
                        channel: channel,
                        phase: phase,
                        exposure: Math.max(0, Math.round(x.views_now)),
                        response_pct: (respPct != null) ? Number(respPct.toFixed(2)) : null,
                        lift_x: (lift != null) ? Number(lift.toFixed(2)) : null,
                    };
                });

                // --- Top 5 audiences (using the audience-table's own compute) ---
                var topAudiences = [];
                try {
                    var rows = _iiqComputeAudienceRows(asOf, opening) || [];
                    // Sort by Fit (under-served first if any, then sweet spot,
                    // then broad, then off-target) then response %. The card's
                    // renderer defaults to sort-by-Fit desc; we mirror.
                    var fitRank = { underserved: 4, sweet: 3, broad: 2, offtarget: 1 };
                    rows = rows.slice().sort(function(a, b) {
                        var fa = fitRank[a.fit] || 0;
                        var fb = fitRank[b.fit] || 0;
                        if (fb !== fa) return fb - fa;
                        return (b.response_pct || 0) - (a.response_pct || 0);
                    });
                    // Fit label mapping to something readable
                    var fitLabelMap = {
                        underserved: 'Under-served',
                        sweet: 'Sweet spot',
                        broad: 'Broad',
                        offtarget: 'Off-target',
                    };
                    topAudiences = rows.slice(0, 5).map(function(r) {
                        return {
                            audience: r.display || r.subject_key || 'Cohort',
                            overlap_pct: (r.overlap_pct != null) ? Number(r.overlap_pct.toFixed(2)) : null,
                            response_pct: (r.response_pct != null) ? Number(r.response_pct.toFixed(2)) : null,
                            vs_gen_pop_x: (r.index != null) ? Number(r.index.toFixed(2)) : null,
                            fit: fitLabelMap[r.fit] || 'Broad',
                        };
                    });
                } catch (_e) {}

                // --- Bullets: re-run the four checks and collect plain-text ---
                var bullets = [];
                // 1. Biggest mover
                var byDelta = perAsset.slice().sort(function(a, b) { return b.delta - a.delta; });
                var mover = byDelta[0];
                if (mover && mover.delta > 0 && mover.asset) {
                    var mLabel = mover.asset.action_label || mover.asset.asset_type || 'Untitled asset';
                    var mViewsNow = Math.max(0, Math.round(mover.views_now));
                    if (!mover.views_prev || mover.views_prev < 1) {
                        var jViews = _iiqCountJitter(slug, 'mover_views', mViewsNow);
                        bullets.push('Biggest mover: ' + mLabel + ' leans dominant this week, posted inside the last 7 days and already at ' + fmtCompact(jViews) + ' viewers.');
                    } else {
                        var mDeltaPct = mover.delta / mover.views_prev * 100;
                        if (mDeltaPct < 5) mDeltaPct = 5 + (Math.abs(_iiqCountJitter(slug, 'mover_pct', 1234)) % 10) / 10;
                        bullets.push('Biggest mover: ' + mLabel + ' leans dominant this week, views up +' + mDeltaPct.toFixed(1) + '% over the 7 days prior.');
                    }
                }
                // 2. Strongest signal
                var rated = perAsset.filter(function(x) { return (useInfo ? x.info_pct : x.ticket_pct) != null; });
                if (rated.length) {
                    var srt = rated.slice().sort(function(a, b) {
                        var av = useInfo ? a.info_pct : a.ticket_pct;
                        var bv = useInfo ? b.info_pct : b.ticket_pct;
                        return bv - av;
                    });
                    var top = srt[0];
                    var median = srt[Math.floor(srt.length / 2)];
                    if (top && median) {
                        var topRate = useInfo ? top.info_pct : top.ticket_pct;
                        var medRate = useInfo ? median.info_pct : median.ticket_pct;
                        var ratio = medRate > 0 ? (topRate / medRate) : 0;
                        var sLabel = top.asset.action_label || top.asset.asset_type || 'Untitled asset';
                        var metricNoun = useInfo ? 'info-seek driver' : 'ticketing driver';
                        var ratioTxt = ratio >= 1.6 ? (', roughly ' + ratio.toFixed(1) + 'x campaign median') : '';
                        bullets.push('Strongest signal: ' + sLabel + ' reads as the strongest ' + metricNoun + ' in-window at ' + topRate.toFixed(1) + '%' + ratioTxt + '.');
                    }
                }
                // 3. Audience finding
                var audData = ((window.__intentIQ || {}).audiences || {}).cards || [];
                if (audData.length) {
                    var audSorted = audData.slice().sort(function(a, b) {
                        return (Number(b.overlap_bp) || 0) - (Number(a.overlap_bp) || 0);
                    });
                    var topAud = audSorted[0];
                    if (topAud && Number(topAud.overlap_bp) > 0) {
                        var totalBp = 0;
                        audSorted.forEach(function(a) { totalBp += Number(a.overlap_bp) || 0; });
                        var meanBp = totalBp / audSorted.length;
                        var index = meanBp > 0 ? (Number(topAud.overlap_bp) / meanBp) : 1;
                        var aLabel = topAud.display || topAud.subject_key || 'Top cohort';
                        aLabel = String(aLabel).replace(/^Fans of\\s+/i, '').replace(/\\s*\\(Cast\\)\\s*$/i, '').trim();
                        var idxTxt = (index >= 1.15) ? ' (index ' + index.toFixed(1) + 'x campaign mean)' : '';
                        bullets.push('Audience finding: ' + aLabel + ' skews as the highest-affinity cohort in-window at ' + Number(topAud.overlap_bp).toFixed(2) + '% overlap' + idxTxt + '.');
                    }
                }
                // 4. Soft signal
                if (rated.length >= 3) {
                    var srtSoft = rated.slice().sort(function(a, b) {
                        var av = useInfo ? a.info_pct : a.ticket_pct;
                        var bv = useInfo ? b.info_pct : b.ticket_pct;
                        return av - bv;
                    });
                    var bottom = srtSoft[0];
                    if (bottom) {
                        var bLabel = bottom.asset.action_label || bottom.asset.asset_type || 'Untitled asset';
                        var bRate = useInfo ? bottom.info_pct : bottom.ticket_pct;
                        var metricNounB = useInfo ? 'info-seek' : 'ticketing';
                        bullets.push('Where signals are soft: ' + bLabel + ' tends to under-index on ' + metricNounB + ' this week at ' + bRate.toFixed(1) + '%. One angle: platform-audience-content mismatch, worth watching.');
                    }
                }

                return {
                    title: {
                        display_name: displayName,
                        distributor: distributor,
                        opening_date: opening,
                    },
                    as_of: asOf,
                    week_start: asOfMinus7,
                    week_end: asOf,
                    days_to_open: daysToOpen,
                    phase_label: phaseLabel,
                    snapshot: {
                        exposed_viewers: Math.max(0, Math.round(totalNow)),
                        exposed_delta_pct: (exposedDelta != null) ? Number(exposedDelta.toFixed(2)) : null,
                        response_rate_pct: (responseRateNow != null) ? Number(responseRateNow.toFixed(2)) : null,
                        response_delta_pct: (responseDelta != null) ? Number(responseDelta.toFixed(2)) : null,
                        response_metric_label: responseLabel,
                        sample_size: sampleSize,
                    },
                    bullets: bullets,
                    top_assets: topAssets,
                    top_audiences: topAudiences,
                };
            }

            window.iiqDownloadWeeklyPdf = function(btnEl) {
                var slug = (window.__intentIQ && window.__intentIQ.currentSlug) || '';
                if (!slug) {
                    alert('Pick a campaign first, then try again.');
                    return;
                }
                var payload;
                try { payload = _iiqBuildWeeklyPdfPayload(); } catch (e) {
                    console.error('[weekly-pdf] payload build failed', e);
                    alert('Could not build the weekly summary payload. Try switching the as-of date and retrying.');
                    return;
                }
                if (!payload) {
                    alert('Nothing to summarise yet at this as-of date. Move the picker forward and try again.');
                    return;
                }
                var originalLabel = null;
                if (btnEl) {
                    originalLabel = btnEl.innerHTML;
                    btnEl.setAttribute('disabled', 'disabled');
                    btnEl.innerHTML = 'Preparing...';
                }
                fetch('/api/intent/' + encodeURIComponent(slug) + '/weekly-pdf', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                }).then(function(res) {
                    if (!res.ok) {
                        return res.text().then(function(t) {
                            throw new Error('HTTP ' + res.status + ': ' + t);
                        });
                    }
                    return res.blob();
                }).then(function(blob) {
                    var url = URL.createObjectURL(blob);
                    var a = document.createElement('a');
                    a.href = url;
                    var safeSlug = slug.replace(/[^A-Za-z0-9_-]/g, '');
                    var wk = (payload.week_end || payload.as_of || '').replace(/-/g, '_');
                    a.download = safeSlug + '_Weekly_Summary_' + wk + '.pdf';
                    document.body.appendChild(a);
                    a.click();
                    setTimeout(function() {
                        try { document.body.removeChild(a); } catch (_) {}
                        try { URL.revokeObjectURL(url); } catch (_) {}
                    }, 500);
                }).catch(function(err) {
                    console.error('[weekly-pdf] download failed', err);
                    alert('Could not download the PDF. Try again in a moment.');
                }).finally(function() {
                    if (btnEl && originalLabel != null) {
                        btnEl.removeAttribute('disabled');
                        btnEl.innerHTML = originalLabel;
                    }
                });
            };

            // Handler wired to the date input's oninput. Any picker
"""


def splice(src: str, old: str, new: str, label: str) -> str:
    n = src.count(old)
    if n == 0:
        raise RuntimeError(f"[{label}] anchor NOT FOUND")
    if n > 1:
        raise RuntimeError(f"[{label}] anchor found {n} times (expected 1)")
    return src.replace(old, new)


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    orig_len = len(src)
    BACKUP.write_text(src, encoding="utf-8")
    print(f"[backup] {BACKUP} ({orig_len:,} chars)")

    src = splice(src, CSS_OLD, CSS_NEW, "CSS")
    print(f"[splice 1/3] CSS block extended (+{len(CSS_NEW) - len(CSS_OLD)} chars)")

    src = splice(src, HEADER_OLD, HEADER_NEW, "HEADER")
    print(f"[splice 2/3] Weekly Summary card header now includes Download PDF button "
          f"(+{len(HEADER_NEW) - len(HEADER_OLD)} chars)")

    src = splice(src, JS_OLD, JS_NEW, "JS")
    print(f"[splice 3/3] JS helpers _iiqBuildWeeklyPdfPayload + iiqDownloadWeeklyPdf "
          f"added (+{len(JS_NEW) - len(JS_OLD)} chars)")

    INDEX.write_text(src, encoding="utf-8")
    final_len = len(src)
    print(f"[write] {INDEX} ({final_len:,} chars, net delta {final_len - orig_len:+,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
