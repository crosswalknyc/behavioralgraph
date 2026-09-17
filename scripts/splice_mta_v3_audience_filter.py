#!/usr/bin/env python3
"""Add the audience-cohort dropdown to the Multi-Touch Attribution tab.

Six coordinated splices inside `_IIQ_MTA_MODULE_v1_` in
templates/index.html:

  1. Cache init: track ``audience_slug`` alongside ``slug`` + ``data`` +
     ``ts`` so a dropdown swap doesn't refetch. Reset audience_slug='' on
     a campaign switch.
  2. New JS helpers: activePayload merger, dropdown renderer, cohort
     meta strip renderer, and the global setAudience handler wired to
     the dropdown's onchange.
  3-5. Card intros (Journeys / Co-exposure / Coefficient) prefix
     "Within <audience_label>:" when an audience is selected.
  6. `_iiqMTARender` body rewritten to render the dropdown row + cohort
     meta strip above the three summary tiles, expand the "How to read
     this" copy with the new "Filter by audience" section, and render
     the three cards from the active slice.

Per .cursor/rules/index-html-safety.mdc: templates/index.html is edited
ONLY via byte-level Python splice (never StrReplace, never Write). This
script reads once, applies every replacement in memory, then writes
once. If any anchor is missing or non-unique the script raises before
any write happens, so the file cannot be corrupted mid-splice.

Payload prerequisite: bg-webapp/mta_iq.py SCHEMA_VERSION = 3 (adds the
nested ``overall`` + ``audiences`` wrapper). The frontend still renders
correctly against a v2-shape payload because the active-payload merger
falls back to the top-level fields when ``overall`` is missing.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX = HERE.parent / "templates" / "index.html"
BACKUP = Path("/tmp/index.pre_mta_v3_audience_filter.html")


# ---------------------------------------------------------------------------
# Edit 1: cache init line
# ---------------------------------------------------------------------------
OLD_1 = "window.__iiqMTACache = window.__iiqMTACache || { slug: null, data: null, ts: 0 };"
NEW_1 = "window.__iiqMTACache = window.__iiqMTACache || { slug: null, audience_slug: '', data: null, ts: 0 };"


# ---------------------------------------------------------------------------
# Edit 2: iiqRenderMTA -- reset audience_slug when campaign changes, and
# still honor the current audience_slug on a cache hit.
# ---------------------------------------------------------------------------
OLD_2 = """            window.iiqRenderMTA = function() {
                var slug = window.__intentIQ && window.__intentIQ.currentSlug;
                var host = document.getElementById('iiqMTAContent');
                if (!host) { console.warn('[MTA] host div #iiqMTAContent missing'); return; }
                if (!slug) { host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem;">Load a campaign first.</div>'; return; }
                // Serve from cache if same slug fetched within 5 min.
                if (window.__iiqMTACache.slug === slug && window.__iiqMTACache.data && (Date.now() - window.__iiqMTACache.ts) < 300000) {
                    _iiqMTARender(window.__iiqMTACache.data);
                    return;
                }
                host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem;">Loading touchpoint contribution model...</div>';
                fetch('/api/intent/' + slug + '/mta', { credentials: 'same-origin' })
                    .then(function(r) {
                        if (r.status === 401 || r.status === 403) throw new Error('Not authorized (HTTP ' + r.status + '). Refresh and sign in again.');
                        if (r.status === 404) throw new Error('Multi-Touch Attribution is not enabled for this campaign.');
                        if (!r.ok) throw new Error('Server returned HTTP ' + r.status);
                        return r.json();
                    })
                    .then(function(d) {
                        if (!d || !d.success) {
                            host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem; color:#fca5a5;">Load failed: ' + escapeHtml((d && d.error) || 'no error message') + '</div>';
                            return;
                        }
                        window.__iiqMTACache = { slug: slug, data: d, ts: Date.now() };
                        _iiqMTARender(d);
                    })
                    .catch(function(e) {
                        console.error('[MTA] fetch chain failed:', e);
                        host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem; color:#fca5a5;">' + escapeHtml(e.message || String(e)) + '</div>';
                    });
            };"""

NEW_2 = """            window.iiqRenderMTA = function() {
                var slug = window.__intentIQ && window.__intentIQ.currentSlug;
                var host = document.getElementById('iiqMTAContent');
                if (!host) { console.warn('[MTA] host div #iiqMTAContent missing'); return; }
                if (!slug) { host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem;">Load a campaign first.</div>'; return; }
                // Serve from cache if same slug fetched within 5 min. The
                // audience_slug lives ON the cache so a dropdown swap reuses
                // the same payload without a refetch; a campaign switch
                // (below) resets audience_slug back to the all-viewers view.
                if (window.__iiqMTACache.slug === slug && window.__iiqMTACache.data && (Date.now() - window.__iiqMTACache.ts) < 300000) {
                    _iiqMTARender(window.__iiqMTACache.data);
                    return;
                }
                host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem;">Loading touchpoint contribution model...</div>';
                fetch('/api/intent/' + slug + '/mta', { credentials: 'same-origin' })
                    .then(function(r) {
                        if (r.status === 401 || r.status === 403) throw new Error('Not authorized (HTTP ' + r.status + '). Refresh and sign in again.');
                        if (r.status === 404) throw new Error('Multi-Touch Attribution is not enabled for this campaign.');
                        if (!r.ok) throw new Error('Server returned HTTP ' + r.status);
                        return r.json();
                    })
                    .then(function(d) {
                        if (!d || !d.success) {
                            host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem; color:#fca5a5;">Load failed: ' + escapeHtml((d && d.error) || 'no error message') + '</div>';
                            return;
                        }
                        // Campaign switch: fresh data + reset audience filter
                        // back to All exposed viewers (the standing default).
                        window.__iiqMTACache = { slug: slug, audience_slug: '', data: d, ts: Date.now() };
                        _iiqMTARender(d);
                    })
                    .catch(function(e) {
                        console.error('[MTA] fetch chain failed:', e);
                        host.innerHTML = '<div class="iiq-empty" style="padding:1.5rem; color:#fca5a5;">' + escapeHtml(e.message || String(e)) + '</div>';
                    });
            };

            // ------------------------------------------------------------
            // v3 audience filter: merge the wrapper + selected slice into a
            // single "active payload" the existing card renderers already
            // know how to consume. When d is a v2-shape payload (no
            // ``overall`` block), we fall through to d itself so the
            // renderer still works for older cache reads.
            // ------------------------------------------------------------
            function _iiqMTAActivePayload(d, audienceSlug) {
                if (!d) return {};
                var overall = d.overall || null;
                var auds = d.audiences || {};
                var slice = null;
                if (audienceSlug && auds && auds[audienceSlug]) {
                    slice = auds[audienceSlug];
                } else if (overall) {
                    slice = overall;
                }
                if (!slice) return d;   // v2-shape fallback
                // Wrapper metadata (campaign_slug, display_name, title_type,
                // conversion_noun, bottom_funnel_label, interpretation_note)
                // rides through; slice-specific fields (touchpoints, journeys,
                // co_exposure, model_fit, sample_size, conversion_rate,
                // cohort_meta, source) override.
                var merged = {};
                for (var k in d) { if (Object.prototype.hasOwnProperty.call(d, k)) merged[k] = d[k]; }
                for (var k2 in slice) { if (Object.prototype.hasOwnProperty.call(slice, k2)) merged[k2] = slice[k2]; }
                return merged;
            }

            // Return the sorted list of dropdown options for the current
            // wrapper. First entry is always the all-viewers reset. Order
            // preserves the audiences map iteration (which the backend
            // writes in the same order as the campaign's audiences array
            // on normalized_assets.json).
            function _iiqMTAAudienceOptions(d) {
                var opts = [{ slug: '', label: 'All exposed viewers', size: 0 }];
                if (!d || !d.audiences) return opts;
                for (var slug in d.audiences) {
                    if (!Object.prototype.hasOwnProperty.call(d.audiences, slug)) continue;
                    var slice = d.audiences[slug] || {};
                    var meta = slice.cohort_meta || {};
                    var label = meta.audience_label || slug;
                    var size = meta.cohort_size || slice.sample_size || 0;
                    opts.push({ slug: slug, label: label, size: size });
                }
                return opts;
            }

            // Dropdown row rendered ABOVE the three summary tiles. When
            // the campaign has no audience slices, we still render the row
            // with just the all-viewers option so the layout is consistent
            // across campaigns.
            function _iiqMTARenderAudienceDropdown(d, currentSlug) {
                var opts = _iiqMTAAudienceOptions(d);
                var options = opts.map(function(o) {
                    var label = escapeHtml(o.label);
                    if (o.slug && o.size) label += ' (n=' + Number(o.size).toLocaleString() + ')';
                    var sel = (o.slug === currentSlug) ? ' selected' : '';
                    return '<option value="' + escapeHtml(o.slug) + '"' + sel + '>' + label + '</option>';
                }).join('');
                return (
                    '<div style="display:flex; align-items:center; gap:0.75rem; margin: 0 0 0.85rem 0; padding: 0.55rem 0.8rem; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; flex-wrap: wrap;">'
                  + '  <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em; color: var(--text-secondary); font-weight:600;">View by audience</div>'
                  + '  <select id="iiqMTAAudienceSelect" onchange="_iiqMTASetAudience(this.value)" style="flex:1; min-width:220px; max-width:420px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.12); color: var(--text-primary); font-size: 0.78rem; padding: 0.35rem 0.6rem; border-radius: 6px; font-family: inherit;">'
                  +      options
                  + '  </select>'
                  + '  <div style="font-size:0.66rem; color: var(--text-secondary); flex:0 0 auto;">Each cohort re-fits on that cohort\\u2019s exposures only.</div>'
                  + '</div>'
                );
            }

            // Cohort meta strip. Hidden when no audience is selected;
            // shown as a small muted line + optional "Thin read" chip when
            // an audience is selected. Numbers use individual-level nouns
            // per individual-level-language.mdc.
            function _iiqMTARenderCohortMetaStrip(activePayload) {
                var meta = activePayload && activePayload.cohort_meta;
                if (!meta || !meta.audience_label) return '';
                var n = Number(meta.cohort_size || 0);
                var overlap = Number(meta.overlap_bp || 0);
                var gps = Number(meta.gen_pop_share || 0);
                var parts = [];
                if (n > 0) parts.push('n=' + n.toLocaleString() + ' viewers in this cohort');
                if (overlap > 0) parts.push(overlap.toFixed(1) + '% of exposed');
                if (gps > 0) parts.push(gps.toFixed(1) + '% of US');
                var line = parts.join(' \\u00b7 ');
                var thinChip = '';
                if (meta.thin_read) {
                    thinChip = ' <span title="This cohort is small; read the ranking directionally, not as an exact rate card." style="display:inline-block; margin-left:0.5rem; padding:1px 8px; font-size:0.62rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; background: rgba(199,242,62,0.14); color:#C7F23E; border:1px solid rgba(199,242,62,0.35);">Thin read</span>';
                }
                return (
                    '<div style="margin: -0.35rem 0 0.65rem 0; padding: 0.3rem 0.75rem; font-size: 0.72rem; color: var(--text-secondary);">'
                  +   escapeHtml(line) + thinChip
                  + '</div>'
                );
            }

            // Global change handler wired to the dropdown's onchange.
            // Writes the new selection to the cache and re-renders the
            // whole MTA panel from the same in-memory payload -- no
            // refetch, instant swap.
            window._iiqMTASetAudience = function(newSlug) {
                if (!window.__iiqMTACache || !window.__iiqMTACache.data) return;
                window.__iiqMTACache.audience_slug = String(newSlug || '');
                _iiqMTARender(window.__iiqMTACache.data);
            };"""


# ---------------------------------------------------------------------------
# Edit 3: Journeys card intro -- prefix "Within <audience_label>:" when the
# active payload is an audience slice. Preserves the exact wording when
# no audience is selected.
# ---------------------------------------------------------------------------
OLD_3 = """                var bfl = d.bottom_funnel_label || 'Ticketing';
                var intro =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">How the journeys built up to this.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">The ' + journeys.length + ' exposure paths that generated the most conversions on this campaign. Each row is a distinct combination of touchpoints a set of viewers all saw.</div>'
                  + '</div>';"""

NEW_3 = """                var bfl = d.bottom_funnel_label || 'Ticketing';
                var audLabel = (d.cohort_meta && d.cohort_meta.audience_label) ? String(d.cohort_meta.audience_label) : '';
                var introLead = audLabel
                    ? ('Within ' + escapeHtml(audLabel) + ': the ' + journeys.length + ' exposure paths')
                    : ('The ' + journeys.length + ' exposure paths');
                var intro =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">How the journeys built up to this.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">' + introLead + ' that generated the most conversions on this campaign. Each row is a distinct combination of touchpoints a set of viewers all saw.</div>'
                  + '</div>';"""


# ---------------------------------------------------------------------------
# Edit 4: Co-exposure card intro -- same prefix pattern.
# ---------------------------------------------------------------------------
OLD_4 = """                var N = Math.min(tps.length, mat.length);
                var intro =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Which touchpoints travel together.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">For the top ' + N + ' touchpoints by lift, the share of people exposed to the row that were also exposed to the column. Diagonal is always 100%. Bright cells travel together.</div>'
                  + '</div>';"""

NEW_4 = """                var N = Math.min(tps.length, mat.length);
                var audLabelCe = (d.cohort_meta && d.cohort_meta.audience_label) ? String(d.cohort_meta.audience_label) : '';
                var introLeadCe = audLabelCe
                    ? ('Within ' + escapeHtml(audLabelCe) + ': for the top ' + N + ' touchpoints by lift')
                    : ('For the top ' + N + ' touchpoints by lift');
                var intro =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Which touchpoints travel together.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">' + introLeadCe + ', the share of people exposed to the row that were also exposed to the column. Diagonal is always 100%. Bright cells travel together.</div>'
                  + '</div>';"""


# ---------------------------------------------------------------------------
# Edit 5: Coefficient card header -- same prefix pattern.
# ---------------------------------------------------------------------------
OLD_5 = """                var bfl = d.bottom_funnel_label || 'Ticketing';
                var convNoun = d.conversion_noun || 'conversion';
                var header =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Touchpoint contribution to ' + escapeHtml(bfl.toLowerCase()) + '.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">Per-touchpoint lift on ' + escapeHtml(convNoun) + ' conversion, ranked by absolute lift.</div>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">These lifts are the multi-touch answer: each coefficient is the marginal contribution of one additional exposure to that touchpoint, holding every other touchpoint constant.</div>'
                  + '</div>';"""

NEW_5 = """                var bfl = d.bottom_funnel_label || 'Ticketing';
                var convNoun = d.conversion_noun || 'conversion';
                var audLabelCoef = (d.cohort_meta && d.cohort_meta.audience_label) ? String(d.cohort_meta.audience_label) : '';
                var introLeadCoef = audLabelCoef
                    ? ('Within ' + escapeHtml(audLabelCoef) + ': per-touchpoint lift on ' + escapeHtml(convNoun) + ' conversion, ranked by absolute lift.')
                    : ('Per-touchpoint lift on ' + escapeHtml(convNoun) + ' conversion, ranked by absolute lift.');
                var header =
                    '<div style="margin-bottom: 0.6rem;">'
                  + '  <h4 style="margin: 0;">Touchpoint contribution to ' + escapeHtml(bfl.toLowerCase()) + '.</h4>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">' + introLeadCoef + '</div>'
                  + '  <div style="font-size:0.72rem; color: var(--text-secondary); margin-top:0.2rem;">These lifts are the multi-touch answer: each coefficient is the marginal contribution of one additional exposure to that touchpoint, holding every other touchpoint constant.</div>'
                  + '</div>';"""


# ---------------------------------------------------------------------------
# Edit 6: _iiqMTARender main body -- rewire around the active-payload
# picker, render the dropdown above the tiles, add the cohort meta strip,
# expand the "How to read this" copy with a "Filter by audience" section.
# ---------------------------------------------------------------------------
OLD_6 = """            function _iiqMTARender(d) {
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

NEW_6 = """            function _iiqMTARender(d) {
                var host = document.getElementById('iiqMTAContent');
                if (!host) return;

                // Pick the active audience filter from the cache. Falls
                // back to '' (All exposed viewers) when the cache is
                // fresh or the previous selection was reset by a
                // campaign switch. An audience_slug that no longer maps
                // to a valid slice transparently degrades to overall.
                var audienceSlug = (window.__iiqMTACache && window.__iiqMTACache.audience_slug) || '';
                if (audienceSlug && d && d.audiences && !d.audiences[audienceSlug]) {
                    audienceSlug = '';
                    if (window.__iiqMTACache) window.__iiqMTACache.audience_slug = '';
                }
                var activePayload = _iiqMTAActivePayload(d, audienceSlug);
                var tps = (activePayload.touchpoints || []).slice();
                var bfl = activePayload.bottom_funnel_label || d.bottom_funnel_label || 'Ticketing';
                var convNoun = activePayload.conversion_noun || d.conversion_noun || 'conversion';

                // Dropdown row + cohort meta strip render UNCONDITIONALLY
                // (dropdown always; strip only when an audience is selected)
                // so the reader always has the filter available even before
                // touchpoints have arrived on a fresh campaign.
                var dropdownRow = _iiqMTARenderAudienceDropdown(d, audienceSlug);
                var cohortStrip = _iiqMTARenderCohortMetaStrip(activePayload);

                if (!tps.length) {
                    host.innerHTML = dropdownRow + cohortStrip + '<div class="iiq-empty" style="padding:1.5rem;">No touchpoints available for this cohort yet.</div>';
                    return;
                }

                // How-to-read (collapsible). Rewritten to cover all three
                // cards on the tab and the new audience filter. Keeps the
                // standing caveat from v1.
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
                  + '    <p style="margin: 0 0 0.35rem 0;"><strong style="color: #cbd5e1;">Filter by audience.</strong> Each cohort re-fits the model on only that cohort\\u2019s exposures. A touchpoint that reads strong overall may sit quiet inside a specific audience, and vice versa. That is the point of the filter.</p>'
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

                // Top strip: three summary tiles. Reads from the ACTIVE
                // payload so the tiles reflect the cohort's baseline +
                // exposed count when an audience is selected.
                var fit = activePayload.model_fit || {};
                var quality = String(fit.quality || 'weak');
                var qLabel = { strong: 'Strong read', moderate: 'Moderate read', weak: 'Weak read' }[quality] || 'Weak read';
                var qColor = { strong: '#C7F23E', moderate: '#9FD628', weak: '#94a3b8' }[quality] || '#94a3b8';
                var qBg    = { strong: 'rgba(199,242,62,0.14)', moderate: 'rgba(159,214,40,0.14)', weak: 'rgba(148,163,184,0.14)' }[quality] || 'rgba(148,163,184,0.14)';
                var qChip  = '<span style="display:inline-block; padding:0.1rem 0.55rem; font-size:0.68rem; font-weight:600; letter-spacing:0.04em; text-transform:uppercase; border-radius:9999px; background:' + qBg + '; color:' + qColor + ';">' + qLabel + '</span>';
                var exposedLabel = audienceSlug ? 'Viewers in this cohort' : 'Exposed viewers';
                var tiles = [
                    iiqSummaryTile('Baseline conversion rate', _iiqMTAFmtPct(activePayload.conversion_rate, 2), 'share of exposed viewers who visited the ' + bfl.toLowerCase() + ' page in the attribution window'),
                    iiqSummaryTile(exposedLabel, fmtCompact(activePayload.sample_size), 'individual viewers used in the read'),
                    '<div class="iiq-card" style="padding:0.6rem 0.75rem; display:flex; flex-direction:column; justify-content:space-between;">'
                      + '<div class="iiq-card-meta" style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.5px; opacity:0.7;">Read strength</div>'
                      + '<div style="margin-top:0.35rem;">' + qChip + '</div>'
                      + '<div class="iiq-card-meta" style="margin-top:0.4rem; font-size:0.7rem; opacity:0.7;">' + (fit.convergence ? 'stable across passes' : 'unstable pass') + '</div>'
                    + '</div>'
                ].join('');
                var topStrip = '<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; margin-bottom: 0.85rem;">' + tiles + '</div>';

                // Card wrappers. Each of the three cards renders inside its
                // own container so future tweaks (drag-to-reorder, per-card
                // filters, etc.) stay local. All three cards read from the
                // ACTIVE payload so the audience filter cascades through.
                var journeysHost = 'iiqMTAJourneysCard';
                var coExpHost    = 'iiqMTACoExpCard';
                var coefHost     = 'iiqMTACoefCard';

                var journeysCard =
                    '<section id="' + journeysHost + '" style="margin-top:0.75rem;">' + _iiqMTARenderJourneysCard(activePayload, journeysHost) + '</section>';
                var coExpCard =
                    '<section id="' + coExpHost + '" style="margin-top:1.1rem;">' + _iiqMTARenderCoExposureCard(activePayload) + '</section>';
                var coefCard =
                    '<section id="' + coefHost + '" style="margin-top:1.1rem;">' + _iiqMTARenderCoefficientCard(activePayload) + '</section>';

                host.innerHTML = dropdownRow + cohortStrip + howToRead + topStrip + journeysCard + coExpCard + coefCard;
            }"""


EDITS = [
    ("cache init line", OLD_1, NEW_1),
    ("iiqRenderMTA + new helpers", OLD_2, NEW_2),
    ("Journeys card intro", OLD_3, NEW_3),
    ("Co-exposure card intro", OLD_4, NEW_4),
    ("Coefficient card header", OLD_5, NEW_5),
    ("_iiqMTARender main body", OLD_6, NEW_6),
]


def splice(src: str, old: str, new: str, desc: str) -> str:
    count = src.count(old)
    if count == 0:
        raise SystemExit(f"[splice_mta_v3_audience_filter] anchor NOT FOUND: {desc}")
    if count > 1:
        raise SystemExit(f"[splice_mta_v3_audience_filter] anchor matched {count}x (needs unique context): {desc}")
    return src.replace(old, new)


def main() -> int:
    src = INDEX.read_text(encoding="utf-8")
    BACKUP.write_text(src, encoding="utf-8")
    before = len(src)
    for desc, old, new in EDITS:
        src = splice(src, old, new, desc)
        print(f"[splice_mta_v3_audience_filter] applied: {desc}")
    INDEX.write_text(src, encoding="utf-8")
    after = len(src)
    print(f"[splice_mta_v3_audience_filter] backup: {BACKUP}")
    print(f"[splice_mta_v3_audience_filter] bytes: {before:,} -> {after:,} (delta {after - before:+,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
