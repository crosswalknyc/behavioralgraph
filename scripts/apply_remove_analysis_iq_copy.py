#!/usr/bin/env python3
"""Scrub every user-facing 'Analysis IQ' string from the dashboard.

Per Jenna 2026-09-09: 'remove Analysis IQ from everywhere' -> copy_only
scope (access gates + schema left intact).

Context: the 'Analysis IQ' dropdown option and drawer were already
retired earlier this year. What remained was ~80 references to
'Analysis IQ' as if it were still a live product surface:

  - Empty-state messages telling users to 'Run one in Analysis IQ'
    when Analysis IQ no longer exists in the UI.
  - Admin panel tooltips and captions referencing an 'Analysis IQ
    dropdown' that isn't there.
  - API error messages returned to callers as 'Analysis IQ access with
    <X> module required' when the caller has no way to grant Analysis
    IQ access from the admin UI.
  - The dropdown-order comment in index.html still listing 'Analysis
    IQ' between real dropdown entries.

This script scrubs ONLY user-facing strings. It leaves untouched:

  - `user_can_run_analysis_module()` gate logic in app.py.
  - The umbrella `has_analysis_iq_access` field on user records.
  - The `analysis_iq_modules` list field on user records.
  - `'analysis_iq_modules::<submodule>'` compound access-check keys.
  - Internal Python comments that record historical context (e.g.
    'Analysis IQ drawer retired', 'has_analysis_iq_access').

Where a splice removes a location hint ('under Analysis IQ ->
<Module>'), the replacement points to the module's actual current
name. Where the hint was purely locational and the module is now
reached differently, the hint is dropped entirely.

Idempotent: every splice no-ops if already applied.
"""
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
APP_PY = REPO / 'app.py'
INDEX_HTML = REPO / 'templates' / 'index.html'
ADMIN_HTML = REPO / 'templates' / 'admin.html'


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


# ===========================================================================
# templates/index.html
# ===========================================================================

# 1) Dropdown-order comment: strip 'Analysis IQ' from the list of dropdown
#    entries since it hasn't been in the dropdown for months.
IDX_1_OLD = """                                 Brand Partnership, Flywheel Conversion,
                                 Analysis IQ, Share of Time, Workspace,
                                 then tail (Admin)."""
IDX_1_NEW = """                                 Brand Partnership, Flywheel Conversion,
                                 Share of Time, Workspace,
                                 then tail (Admin)."""

# 2) Talent Fit results sidebar empty state (line ~21892).
IDX_2_OLD = """                        <div style="font-size: 24px; margin-bottom: 0.5rem; opacity: 0.5;">📋</div>
                        No assessments yet.<br>Run one in Analysis IQ.
                    </div>"""
IDX_2_NEW = """                        <div style="font-size: 24px; margin-bottom: 0.5rem; opacity: 0.5;">📋</div>
                        No assessments yet.<br>Run a new assessment below.
                    </div>"""

# 3) Talent Fit main empty state (line ~21922).
IDX_3_OLD = """                        <div style="font-size: 13px; max-width: 400px; margin-bottom: 1.5rem;">Select an assessment from the sidebar to view results, or run a new analysis in Analysis IQ.</div>"""
IDX_3_NEW = """                        <div style="font-size: 13px; max-width: 400px; margin-bottom: 1.5rem;">Select an assessment from the sidebar to view results, or run a new assessment.</div>"""

# 4) Flywheel Conversion empty state (line ~24153).
IDX_4_OLD = """                            <p>Choose a flywheel conversion analysis from the sidebar to view conversion funnel insights, or run a new analysis in Analysis IQ.</p>"""
IDX_4_NEW = """                            <p>Choose a flywheel conversion analysis from the sidebar to view conversion funnel insights, or run a new Flywheel Conversion analysis.</p>"""

# 5) Attribution IQ 'no campaigns' status (line ~26968).
IDX_5_OLD = """                        document.getElementById('iiqStatus').textContent = 'No campaigns available. Use "Build Attribution IQ Campaign" under Analysis IQ to create one.';"""
IDX_5_NEW = """                        document.getElementById('iiqStatus').textContent = 'No campaigns available. Use "Build Attribution IQ Campaign" to create one.';"""

# 6) Digital Journey IQ 'no journey loaded' hint (line ~38813).
IDX_6_OLD = """                <div style="font-size: 0.85rem; max-width: 520px; margin: 0 auto;">Pick a run from the dropdown above, or kick off a new one under <strong>Analysis IQ → Digital Journey IQ</strong>.</div>"""
IDX_6_NEW = """                <div style="font-size: 0.85rem; max-width: 520px; margin: 0 auto;">Pick a run from the dropdown above, or kick off a new one under <strong>Digital Journey IQ</strong>.</div>"""

# 7) SF -> LF Conversion empty state (line ~59249).
IDX_7_OLD = """                container.innerHTML = '<div style="padding: 1rem; font-size: 0.8rem; color: var(--text-secondary);">No released Short Form → Long Form Conversion analyses found.<br><br>Run new analyses from <strong>Analysis IQ → Short Form to Long Form Conversion</strong></div>';"""
IDX_7_NEW = """                container.innerHTML = '<div style="padding: 1rem; font-size: 0.8rem; color: var(--text-secondary);">No released Short Form → Long Form Conversion analyses found.<br><br>Run a new Short Form to Long Form Conversion analysis.</div>';"""

# 8) Brand Partnership sidebar empty state (line ~61895).
IDX_8_OLD = """                        tree.innerHTML = '<div style="padding:1rem;color:var(--text-secondary);font-size:13px;">No released results yet.<br><br>Run one in <strong>Analysis IQ → Brand Partnership Valuation</strong>.</div>';"""
IDX_8_NEW = """                        tree.innerHTML = '<div style="padding:1rem;color:var(--text-secondary);font-size:13px;">No released results yet.<br><br>Run one in <strong>Brand Partnership Valuation</strong>.</div>';"""

# 9) Talent Fit main-dashboard empty state (line ~71629).
IDX_9_OLD = """                    <div style="font-size:24px;margin-bottom:0.5rem;opacity:0.5;">📋</div>
                    No assessments yet.<br>Run one in Analysis IQ.
                </div>`;"""
IDX_9_NEW = """                    <div style="font-size:24px;margin-bottom:0.5rem;opacity:0.5;">📋</div>
                    No assessments yet.<br>Run a new assessment.
                </div>`;"""

# 10) Flywheel Conversion sidebar empty state (line ~106176).
IDX_10_OLD = """                el.innerHTML = '<div style="padding:1.5rem 1rem;text-align:center;color:var(--text-secondary);font-size:0.8rem;">No flywheel analyses found. Run one in Analysis IQ.</div>';"""
IDX_10_NEW = """                el.innerHTML = '<div style="padding:1.5rem 1rem;text-align:center;color:var(--text-secondary);font-size:0.8rem;">No flywheel analyses found. Run a new Flywheel Conversion analysis.</div>';"""


# ===========================================================================
# templates/admin.html
# ===========================================================================

# 11) Admin credit-cost panel description (line ~2418).
ADM_1_OLD = """                            Set the credit cost for each Analysis IQ module. Changes take effect immediately for new runs."""
ADM_1_NEW = """                            Set the credit cost for each analysis module. Changes take effect immediately for new runs."""

# 12) E-Commerce IQ tooltip (line ~3736) - references dead Analysis IQ dropdown.
ADM_2_OLD = """                        <small style="color: var(--text-secondary); font-size: 0.7rem; display: block; padding: 0 0.5rem 0.5rem 1.75rem;">
                            Gates the E-Commerce IQ option in the Analysis IQ dropdown and access to released E-Commerce IQ reports.
                        </small>"""
ADM_2_NEW = """                        <small style="color: var(--text-secondary); font-size: 0.7rem; display: block; padding: 0 0.5rem 0.5rem 1.75rem;">
                            Grants access to E-Commerce IQ views and released E-Commerce IQ reports.
                        </small>"""

# 13) Flywheel Conversion access-item title attr (line ~3779).
ADM_3_OLD = """                    <div class="dashboard-access-item" data-access-group="conversion" title="Grants access to the Flywheel Conversion view + the Flywheel Conversion module inside Analysis IQ.">"""
ADM_3_NEW = """                    <div class="dashboard-access-item" data-access-group="conversion" title="Grants access to the Flywheel Conversion view + the Flywheel Conversion analysis module.">"""

# 14) Chatbot Profile IQ header caption (line ~4054).
ADM_4_OLD = """                            <span style="margin-left: auto; font-size: 0.72rem; color: var(--text-secondary);">Analysis IQ · natural-language brief</span>"""
ADM_4_NEW = """                            <span style="margin-left: auto; font-size: 0.72rem; color: var(--text-secondary);">Natural-language brief</span>"""

# 15) Run TST checkbox label (line ~4177).
ADM_5_OLD = """                                    <label class="checkbox-group">
                                        <input type="checkbox" id="hasTicketSalesTrackerRun" onchange="syncTSTRunWithModule();">
                                        <span>Run TST via Analysis IQ</span>
                                    </label>"""
ADM_5_NEW = """                                    <label class="checkbox-group">
                                        <input type="checkbox" id="hasTicketSalesTrackerRun" onchange="syncTSTRunWithModule();">
                                        <span>Run TST</span>
                                    </label>"""

# 16) Run TST small caption (line ~4180).
ADM_6_OLD = """                                <small style="color: var(--text-secondary); font-size: 0.65rem; display: block; margin-top: 0.5rem;">View: access released reports. Run: submit TST analysis (requires Analysis IQ access + this).</small>"""
ADM_6_NEW = """                                <small style="color: var(--text-secondary); font-size: 0.65rem; display: block; margin-top: 0.5rem;">View: access released reports. Run: submit TST analysis (requires this checkbox).</small>"""


# ===========================================================================
# app.py - API error messages returned as JSON `error` field to callers
# ===========================================================================
# All of these are visible to whoever hits the endpoint (dashboard,
# partner API, or an internal admin curl). Every one is rewritten to
# name the actual product the caller was trying to reach, without
# implying they need a dead 'Analysis IQ' grant.

# 17) Talent Search
APP_1_OLD = """            return jsonify({'error': 'Analysis IQ access with Talent Search module required'}), 403"""
APP_1_NEW = """            return jsonify({'error': 'Talent Search access required'}), 403"""

# 18) Ticket Sales
APP_2_OLD = """            return jsonify({'error': 'Analysis IQ access with Ticket Sales module required'}), 403"""
APP_2_NEW = """            return jsonify({'error': 'Ticket Sales access required'}), 403"""

# 19) Ticket Sales Tracker
APP_3_OLD = """            return jsonify({'error': 'Analysis IQ access with Ticket Sales Tracker module required'}), 403"""
APP_3_NEW = """            return jsonify({'error': 'Ticket Sales Tracker access required'}), 403"""

# 20) SF-LF Conversion
APP_4_OLD = """            return jsonify({'error': 'Analysis IQ access with SF-LF Conversion module required'}), 403"""
APP_4_NEW = """            return jsonify({'error': 'SF-LF Conversion access required'}), 403"""

# 21) Flywheel Conversion
APP_5_OLD = """            return jsonify({'error': 'Analysis IQ access with Flywheel Conversion module required'}), 403"""
APP_5_NEW = """            return jsonify({'error': 'Flywheel Conversion access required'}), 403"""

# 22) SVOD
APP_6_OLD = """            return jsonify({'error': 'Analysis IQ access with SVOD module required'}), 403"""
APP_6_NEW = """            return jsonify({'error': 'SVOD access required'}), 403"""

# 23) Campaign (Attribution IQ campaign build)
APP_7_OLD = """            return jsonify({'error': 'Analysis IQ access with Campaign module required'}), 403"""
APP_7_NEW = """            return jsonify({'error': 'Attribution IQ Campaign access required'}), 403"""

# 24) Cross Show
APP_8_OLD = """            return jsonify({'error': 'Analysis IQ access with Cross Show module required'}), 403"""
APP_8_NEW = """            return jsonify({'error': 'Cross Show access required'}), 403"""

# 25) Watch Time
APP_9_OLD = """            return jsonify({'error': 'Analysis IQ access with Watch Time module required'}), 403"""
APP_9_NEW = """            return jsonify({'error': 'Watch Time access required'}), 403"""

# 26) Brand Partnership Valuation (multi-line error). Anchor includes the
#     preceding gate call + the preceding comment so it stays unique.
APP_10_OLD = """        # Module-level access gate. Brand Partnership IQ is part of Analysis IQ.
        if not user_can_run_analysis_module(user, 'brand_partnership_iq'):
            return jsonify({'error': 'Analysis IQ access with Brand Partnership '
                            'Valuation module required'}), 403"""
APP_10_NEW = """        # Module-level access gate for Brand Partnership Valuation.
        if not user_can_run_analysis_module(user, 'brand_partnership_iq'):
            return jsonify({'error': 'Brand Partnership Valuation access required'}), 403"""

# 27) Digital Journey IQ (multi-line error).
APP_11_OLD = """        if not user_can_run_analysis_module(user, 'journey_iq'):
            return jsonify({'error': 'Analysis IQ access with Digital Journey IQ '
                            'module required'}), 403"""
APP_11_NEW = """        if not user_can_run_analysis_module(user, 'journey_iq'):
            return jsonify({'error': 'Digital Journey IQ access required'}), 403"""

# 28) Attribution IQ Ingest (multi-line error). Also scrub the leading
#     docstring line 'from the Analysis IQ form' since the form no longer
#     lives under an 'Analysis IQ' surface.
APP_12_OLD = """    \"\"\"Kick off a brand-campaign Attribution IQ build from the Analysis IQ form.\"\"\"
    try:
        username = session.get('username')
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        if not user_can_run_analysis_module(user, 'intent_ingest'):
            return jsonify({'error': 'Analysis IQ access with Intent Ingest '
                            'module required'}), 403"""
APP_12_NEW = """    \"\"\"Kick off a brand-campaign Attribution IQ build from the ingest form.\"\"\"
    try:
        username = session.get('username')
        user = get_current_user()
        if not user:
            return jsonify({'error': 'User not authenticated'}), 401
        if not user_can_run_analysis_module(user, 'intent_ingest'):
            return jsonify({'error': 'Attribution IQ Ingest access required'}), 403"""


def edit_index_html() -> int:
    print("templates/index.html:")
    src = INDEX_HTML.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, IDX_1_OLD, IDX_1_NEW, "dropdown-order comment: drop 'Analysis IQ'")
    src, _ = splice(src, IDX_2_OLD, IDX_2_NEW, "Talent Fit sidebar empty state")
    src, _ = splice(src, IDX_3_OLD, IDX_3_NEW, "Talent Fit main dashboard empty state")
    src, _ = splice(src, IDX_4_OLD, IDX_4_NEW, "Flywheel Conversion empty state")
    src, _ = splice(src, IDX_5_OLD, IDX_5_NEW, "Attribution IQ 'no campaigns' status")
    src, _ = splice(src, IDX_6_OLD, IDX_6_NEW, "Digital Journey IQ 'no journey loaded' hint")
    src, _ = splice(src, IDX_7_OLD, IDX_7_NEW, "SF->LF Conversion empty state")
    src, _ = splice(src, IDX_8_OLD, IDX_8_NEW, "Brand Partnership sidebar empty state")
    src, _ = splice(src, IDX_9_OLD, IDX_9_NEW, "Talent Fit main-dashboard secondary empty state")
    src, _ = splice(src, IDX_10_OLD, IDX_10_NEW, "Flywheel Conversion sidebar empty state")
    if src != orig:
        INDEX_HTML.write_text(src, encoding='utf-8')
        print(f"  wrote {INDEX_HTML}")
        return 1
    return 0


def edit_admin_html() -> int:
    print("templates/admin.html:")
    src = ADMIN_HTML.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, ADM_1_OLD, ADM_1_NEW, "Analysis Pricing panel description")
    src, _ = splice(src, ADM_2_OLD, ADM_2_NEW, "E-Commerce IQ tooltip")
    src, _ = splice(src, ADM_3_OLD, ADM_3_NEW, "Flywheel Conversion access-item title attr")
    src, _ = splice(src, ADM_4_OLD, ADM_4_NEW, "Chatbot Profile IQ header caption")
    src, _ = splice(src, ADM_5_OLD, ADM_5_NEW, "Run TST checkbox label")
    src, _ = splice(src, ADM_6_OLD, ADM_6_NEW, "Run TST small caption")
    if src != orig:
        ADMIN_HTML.write_text(src, encoding='utf-8')
        print(f"  wrote {ADMIN_HTML}")
        return 1
    return 0


def edit_app_py() -> int:
    print("app.py:")
    src = APP_PY.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, APP_1_OLD, APP_1_NEW, "Talent Search error")
    src, _ = splice(src, APP_2_OLD, APP_2_NEW, "Ticket Sales error")
    src, _ = splice(src, APP_3_OLD, APP_3_NEW, "Ticket Sales Tracker error")
    src, _ = splice(src, APP_4_OLD, APP_4_NEW, "SF-LF Conversion error")
    src, _ = splice(src, APP_5_OLD, APP_5_NEW, "Flywheel Conversion error")
    src, _ = splice(src, APP_6_OLD, APP_6_NEW, "SVOD error")
    src, _ = splice(src, APP_7_OLD, APP_7_NEW, "Campaign error")
    src, _ = splice(src, APP_8_OLD, APP_8_NEW, "Cross Show error")
    src, _ = splice(src, APP_9_OLD, APP_9_NEW, "Watch Time error")
    src, _ = splice(src, APP_10_OLD, APP_10_NEW, "Brand Partnership Valuation error + comment")
    src, _ = splice(src, APP_11_OLD, APP_11_NEW, "Digital Journey IQ error")
    src, _ = splice(src, APP_12_OLD, APP_12_NEW, "Attribution IQ Ingest error + docstring")
    if src != orig:
        APP_PY.write_text(src, encoding='utf-8')
        print(f"  wrote {APP_PY}")
        return 1
    return 0


def main() -> int:
    changed = 0
    changed += edit_index_html()
    changed += edit_admin_html()
    changed += edit_app_py()
    if not changed:
        print("[skip] all splices already applied")
    return 0


if __name__ == '__main__':
    sys.exit(main())
