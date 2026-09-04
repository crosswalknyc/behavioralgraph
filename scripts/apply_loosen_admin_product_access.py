#!/usr/bin/env python3
"""Loosen product-access gate so all admins can grant/revoke user access.

Per Jenna 2026-09-04: "make sure admins can revoke and grant access to
certain things for users". Follow-up decision: partial loosen -- role
stays super-admin-only, all product-access flags open to every admin.

This REVERSES the 2026-08-25 posture ("only let super admins grant access
to allow product access to users and assign user status") for the
product-access half only. Role changes (SA / A / U badge) stay super-
admin-only so a regular admin can't self-promote.

Also fills one admin-UI gap discovered in the audit:
  - has_sentiment_iq_access: the field flows through create + update on
    the backend but no admin UI checkbox existed to grant / revoke it.
    Adds a checkbox in the product-access grid + wires reset / load / save.

(A second gap, `analysis_iq_modules`, is NOT filled in this pass. It is
a sub-module-within-Analysis-IQ gate that overlaps substantially with
several existing has_*_access flags. UI needs its own considered design
so admins don't accidentally create a "product on, sub-module off"
inconsistency. Flagged in the summary for follow-up.)

Splices:

1) app.py
   - SUPER_ADMIN_ONLY_USER_FIELDS narrowed to only {'role'}. Comment
     updated to document the 2026-09-04 loosening and preserve the
     historical 2026-08-25 context.
   - Error message in _reject_if_non_super_touches_restricted retuned
     for the role-only reality (it now only ever fires on role change).

2) templates/admin.html
   - SUPER_ADMIN_ONLY_USER_FIELDS JS array narrowed to just ['role'] +
     matching comment update.
   - _applySuperAdminOnlyControls() rewritten to gate ONLY the role
     dropdown. The 30+ lines that disabled every product-access
     checkbox, the Prometheus tier radios, the Prometheus mode radios,
     the allowed-tabs checkboxes, and the extras list are removed.
   - New Sentiment IQ product-access card in the modal grid (right
     after Brand Tracking).
   - Reset in clearNewUserForm, load in editUser, save in the save-user
     payload, init in the default user object.

Idempotent: every splice no-ops if already applied.
"""
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]


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


# ------------------------------------------------------------------
# 1) app.py
# ------------------------------------------------------------------
APP_PY = REPO / 'app.py'

# 1a. Redefine SUPER_ADMIN_ONLY_USER_FIELDS: only 'role' is restricted.
# _PRODUCT_ACCESS_FIELDS stays for documentation + potential future use.
APP_OLD_1 = """# 'role' drives the SA / A / U badge - the "user status" in the UI.
SUPER_ADMIN_ONLY_USER_FIELDS = _PRODUCT_ACCESS_FIELDS | frozenset({'role'})"""

APP_NEW_1 = """# 'role' drives the SA / A / U badge - the "user status" in the UI.
# 2026-09-04 (Jenna, verbatim: "make sure admins can revoke and grant
# access to certain things for users"). Partial loosen of the 2026-08-25
# posture: product-access flags in _PRODUCT_ACCESS_FIELDS above are now
# editable by ALL admins so any admin can grant / revoke a user's access
# to any product. Only `role` remains super-admin-only so a regular
# admin cannot self-promote or promote another user to admin /
# super_admin. _PRODUCT_ACCESS_FIELDS is kept as documentation and for
# potential future re-tightening -- do not delete without checking who
# imports it.
SUPER_ADMIN_ONLY_USER_FIELDS = frozenset({'role'})"""

# 1b. Retune error message: with only 'role' restricted, "product access"
# language is misleading. Reword.
APP_OLD_2 = """    return jsonify({
        'success': False,
        'error': (
            "Only a super admin can grant product access or assign "
            "user status. Restricted fields in this request: "
            + ", ".join(sorted(changing))
        ),
    }), 403"""

APP_NEW_2 = """    return jsonify({
        'success': False,
        'error': (
            "Only a super admin can assign user status (role). "
            "Restricted fields in this request: "
            + ", ".join(sorted(changing))
        ),
    }), 403"""


def edit_app_py():
    print("app.py:")
    src = APP_PY.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, APP_OLD_1, APP_NEW_1, "SUPER_ADMIN_ONLY_USER_FIELDS -> {'role'} only")
    src, _ = splice(src, APP_OLD_2, APP_NEW_2, "gate error message: drop 'product access' phrasing")
    if src != orig:
        APP_PY.write_text(src, encoding='utf-8')
        print(f"  wrote {APP_PY}")


# ------------------------------------------------------------------
# 2) templates/admin.html
# ------------------------------------------------------------------
ADMIN_HTML = REPO / 'templates' / 'admin.html'

# 2a. Narrow the JS SUPER_ADMIN_ONLY_USER_FIELDS array to just ['role'].
ADMIN_OLD_1 = """        // Per Jenna 2026-08-25: "only let super admins grant access to
        // allow product access to users and assign user status". Fields
        // that only a super_admin may modify on a user record; kept in
        // sync with SUPER_ADMIN_ONLY_USER_FIELDS in app.py.
        const SUPER_ADMIN_ONLY_USER_FIELDS = [
            'role',
            'has_profile_iq_access','has_subscriber_iq_access',
            'has_ecommerce_iq_access',
            'has_ticket_sales_iq_access',
            'has_hedge_fund_iq_access','gets_hedge_fund_iq_emails',
            'hedge_fund_iq_tabs','hedge_fund_iq_tickers',
            'hedge_fund_iq_data_cutoff',
            'analysis_iq_modules',
            'has_ticket_sales_tracker_access',
            'has_rankers_iq_access','rankers_iq_options',
            'has_talent_fit_access',
            'has_sf_conversion_access','has_flywheel_conversion_access',
            'has_brand_partnership_iq_access','has_sentiment_iq_access',
            'has_journey_iq_access','allowed_journey_iq_runs',
            'has_intent_iq_access','allowed_intent_iq_runs',
            'has_share_of_time_access','has_share_of_time_run_access',
            'has_blue_iq_access',
            'has_brand_tracking_iq_access',
            'has_impact_iq_access','impact_iq_journeys',
            'has_trends_iq_access','has_microdramas_iq_access',
            'allowed_lenses',
            'allowed_trends_tabs','allowed_rankers_tabs',
            'has_chatbot_profile_iq_access',
            'prometheus_access',
            'auto_access_new'
        ];"""

ADMIN_NEW_1 = """        // Per Jenna 2026-09-04 (verbatim: "make sure admins can revoke
        // and grant access to certain things for users"). Loosened from
        // the 2026-08-25 posture: product-access flags are now editable
        // by every admin. Only `role` (which promotes / demotes admin
        // itself) remains super-admin-only so a regular admin cannot
        // self-promote. Kept in sync with SUPER_ADMIN_ONLY_USER_FIELDS
        // in app.py.
        const SUPER_ADMIN_ONLY_USER_FIELDS = ['role'];"""

# 2b. Simplify _applySuperAdminOnlyControls: gate ONLY the role dropdown.
# The prior body disabled ~40 different controls; nearly all are now
# admin-editable so the extra logic is removed.
ADMIN_OLD_2 = """        // Disable the role dropdown + every product-access checkbox in
        // the user create/edit modal when the current admin is not a
        // super_admin. Backend enforces the same guard (see
        // SUPER_ADMIN_ONLY_USER_FIELDS in app.py); this keeps the UI
        // honest so non-super admins can't fill out controls that would
        // 403 on save. Idempotent - safe to call every modal open.
        function _applySuperAdminOnlyControls() {
            const isSuper = currentUserRole === 'super_admin';
            const roleSel = document.getElementById('newRole');
            if (roleSel) {
                roleSel.disabled = !isSuper;
                roleSel.title = isSuper ? '' : 'Only a super admin can assign user status';
            }
            const disableTitle = 'Only a super admin can grant product access';
            document.querySelectorAll('#userModal [id^="has"][id$="Access"]').forEach(el => {
                el.disabled = !isSuper;
                el.title = isSuper ? '' : disableTitle;
            });
            const extras = [
                'getsHedgeFundIQEmails','hedgeFundIQDataCutoff','allHedgeFundTickers',
                'prometheusAccessFull','prometheusAccessPullsOnly',
                // Prometheus mode (2026-09-03): three-way per-user split.
                // Only super admins can grant / revoke.
                'prometheusModeBoth','prometheusModeAnalysis','prometheusModePull'
            ];
            extras.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    el.disabled = !isSuper;
                    el.title = isSuper ? '' : disableTitle;
                }
            });
            const trendsAllOn = !!(document.getElementById('allowAllTrendsTabs') && document.getElementById('allowAllTrendsTabs').checked);
            const rankersAllOn = !!(document.getElementById('allowAllRankersTabs') && document.getElementById('allowAllRankersTabs').checked);
            ['allowAllTrendsTabs', 'allowAllRankersTabs'].forEach(id => {
                const el = document.getElementById(id);
                if (el) { el.disabled = !isSuper; el.title = isSuper ? '' : disableTitle; }
            });
            document.querySelectorAll('.allowed-trends-tab-checkbox').forEach(el => {
                el.disabled = !isSuper || trendsAllOn;
                el.title = isSuper ? '' : disableTitle;
            });
            document.querySelectorAll('.allowed-rankers-tab-checkbox').forEach(el => {
                el.disabled = !isSuper || rankersAllOn;
                el.title = isSuper ? '' : disableTitle;
            });
            const notice = document.getElementById('userModalSuperAdminNotice');
            if (notice) notice.style.display = isSuper ? 'none' : 'block';
        }"""

ADMIN_NEW_2 = """        // Disable ONLY the role dropdown in the user create / edit
        // modal when the current admin is not a super_admin. Backend
        // enforces the same guard (SUPER_ADMIN_ONLY_USER_FIELDS in
        // app.py). Per Jenna 2026-09-04, every product-access checkbox
        // in this modal is now editable by every admin, so the previous
        // sweeping disable pass is retired. Idempotent - safe to call
        // every modal open.
        function _applySuperAdminOnlyControls() {
            const isSuper = currentUserRole === 'super_admin';
            const roleSel = document.getElementById('newRole');
            if (roleSel) {
                roleSel.disabled = !isSuper;
                roleSel.title = isSuper ? '' : 'Only a super admin can assign user status (role)';
            }
            // Re-enable + clear any tooltip that the pre-2026-09-04
            // implementation may have written onto product-access
            // controls in a still-open modal DOM. Belt-and-suspenders
            // so a stale disabled state can't survive across a role
            // change in the same session.
            const wasDisabledIds = [
                'getsHedgeFundIQEmails','hedgeFundIQDataCutoff','allHedgeFundTickers',
                'prometheusAccessFull','prometheusAccessPullsOnly',
                'prometheusModeBoth','prometheusModeAnalysis','prometheusModePull',
                'allowAllTrendsTabs','allowAllRankersTabs'
            ];
            wasDisabledIds.forEach(id => {
                const el = document.getElementById(id);
                if (el && !isSuper) { el.disabled = false; el.title = ''; }
                if (el && isSuper) { el.disabled = false; el.title = ''; }
            });
            document.querySelectorAll('#userModal [id^="has"][id$="Access"]').forEach(el => {
                el.disabled = false; el.title = '';
            });
            const trendsAllOn = !!(document.getElementById('allowAllTrendsTabs') && document.getElementById('allowAllTrendsTabs').checked);
            const rankersAllOn = !!(document.getElementById('allowAllRankersTabs') && document.getElementById('allowAllRankersTabs').checked);
            document.querySelectorAll('.allowed-trends-tab-checkbox').forEach(el => {
                el.disabled = trendsAllOn;
                el.title = '';
            });
            document.querySelectorAll('.allowed-rankers-tab-checkbox').forEach(el => {
                el.disabled = rankersAllOn;
                el.title = '';
            });
            const notice = document.getElementById('userModalSuperAdminNotice');
            if (notice) notice.style.display = isSuper ? 'none' : 'block';
        }"""

# 2c. Insert Sentiment IQ product-access card right after Brand Tracking
# in the modal. Anchor includes Brand Tracking's closing </div>s plus
# the comment block that immediately follows so it's unique.
ADMIN_OLD_3 = """                    <!-- Brand Tracking Access -->
                    <div class="dashboard-access-item" data-access-group="finance">
                        <div class="dashboard-access-header">
                            <label class="checkbox-group">
                                <input type="checkbox" id="hasBrandTrackingIQAccess">
                                <span style="font-weight: 600;">📡 BRAND TRACKING</span>
                            </label>
                        </div>
                    </div>

                    <!-- 2026-07-30: standalone Trends IQ + Microdramas IQ +
                         Talent Ranker cards consolidated into a single"""

ADMIN_NEW_3 = """                    <!-- Brand Tracking Access -->
                    <div class="dashboard-access-item" data-access-group="finance">
                        <div class="dashboard-access-header">
                            <label class="checkbox-group">
                                <input type="checkbox" id="hasBrandTrackingIQAccess">
                                <span style="font-weight: 600;">📡 BRAND TRACKING</span>
                            </label>
                        </div>
                    </div>

                    <!-- Sentiment IQ Access (2026-09-04 gap fix: backend
                         already accepted has_sentiment_iq_access on
                         create + update, but the admin modal had no
                         checkbox to grant / revoke it per user. Added
                         so admins can actually control who sees
                         Sentiment IQ in the dashboard. -->
                    <div class="dashboard-access-item" data-access-group="trends">
                        <div class="dashboard-access-header">
                            <label class="checkbox-group">
                                <input type="checkbox" id="hasSentimentIQAccess">
                                <span style="font-weight: 600;">🎭 SENTIMENT IQ</span>
                            </label>
                        </div>
                    </div>

                    <!-- 2026-07-30: standalone Trends IQ + Microdramas IQ +
                         Talent Ranker cards consolidated into a single"""

# 2d. Reset in clearNewUserForm. Anchor around the Brand Tracking reset.
ADMIN_OLD_4 = """            var btAccessReset = document.getElementById('hasBrandTrackingIQAccess');
            if (btAccessReset) btAccessReset.checked = false;
            var iiqAccessReset = document.getElementById('hasImpactIQAccess');"""

ADMIN_NEW_4 = """            var btAccessReset = document.getElementById('hasBrandTrackingIQAccess');
            if (btAccessReset) btAccessReset.checked = false;
            var sentAccessReset = document.getElementById('hasSentimentIQAccess');
            if (sentAccessReset) sentAccessReset.checked = false;
            var iiqAccessReset = document.getElementById('hasImpactIQAccess');"""

# 2e. Load in editUser. Anchor around the Brand Tracking load.
ADMIN_OLD_5 = """            var btAccess = document.getElementById('hasBrandTrackingIQAccess');
            if (btAccess) btAccess.checked = user.has_brand_tracking_iq_access === true; // Default false
            var tiqAccess = document.getElementById('hasTrendsIQAccess');"""

ADMIN_NEW_5 = """            var btAccess = document.getElementById('hasBrandTrackingIQAccess');
            if (btAccess) btAccess.checked = user.has_brand_tracking_iq_access === true; // Default false
            var sentAccess = document.getElementById('hasSentimentIQAccess');
            if (sentAccess) sentAccess.checked = user.has_sentiment_iq_access === true; // Default false
            var tiqAccess = document.getElementById('hasTrendsIQAccess');"""

# 2f. Init in default user object (used by "check all users match defaults"
# reconciler). Anchor around Brand Tracking.
ADMIN_OLD_6 = """                hasBrandTrackingIQAccess: false,
                hasHedgeFundIQAccess: false,"""

ADMIN_NEW_6 = """                hasBrandTrackingIQAccess: false,
                hasSentimentIQAccess: false,
                hasHedgeFundIQAccess: false,"""

# 2g. Save in save-user payload. Anchor around Brand Tracking.
ADMIN_OLD_7 = """                has_brand_tracking_iq_access: !!(document.getElementById('hasBrandTrackingIQAccess') && document.getElementById('hasBrandTrackingIQAccess').checked),
                has_trends_iq_access: !!(document.getElementById('hasTrendsIQAccess') && document.getElementById('hasTrendsIQAccess').checked),"""

ADMIN_NEW_7 = """                has_brand_tracking_iq_access: !!(document.getElementById('hasBrandTrackingIQAccess') && document.getElementById('hasBrandTrackingIQAccess').checked),
                has_sentiment_iq_access: !!(document.getElementById('hasSentimentIQAccess') && document.getElementById('hasSentimentIQAccess').checked),
                has_trends_iq_access: !!(document.getElementById('hasTrendsIQAccess') && document.getElementById('hasTrendsIQAccess').checked),"""


def edit_admin_html():
    print("templates/admin.html:")
    src = ADMIN_HTML.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, ADMIN_OLD_1, ADMIN_NEW_1, "SUPER_ADMIN_ONLY_USER_FIELDS JS array -> ['role']")
    src, _ = splice(src, ADMIN_OLD_2, ADMIN_NEW_2, "_applySuperAdminOnlyControls: role-only gate")
    src, _ = splice(src, ADMIN_OLD_3, ADMIN_NEW_3, "add Sentiment IQ access card after Brand Tracking")
    src, _ = splice(src, ADMIN_OLD_4, ADMIN_NEW_4, "Sentiment IQ reset in clearNewUserForm")
    src, _ = splice(src, ADMIN_OLD_5, ADMIN_NEW_5, "Sentiment IQ load in editUser")
    src, _ = splice(src, ADMIN_OLD_6, ADMIN_NEW_6, "Sentiment IQ init in default user object")
    src, _ = splice(src, ADMIN_OLD_7, ADMIN_NEW_7, "Sentiment IQ save in save-user payload")
    if src != orig:
        ADMIN_HTML.write_text(src, encoding='utf-8')
        print(f"  wrote {ADMIN_HTML}")


def main() -> int:
    edit_app_py()
    edit_admin_html()
    print()
    print("Splices applied. Next:")
    print("  1) python3 -c 'import ast; ast.parse(open(\"app.py\").read())'")
    print("  2) node --check on the extracted admin.html JS")
    print("  3) commit + push submodule + parent to main")
    return 0


if __name__ == '__main__':
    sys.exit(main())
