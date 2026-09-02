#!/usr/bin/env python3
"""Hide 'Build a Deck' features + Workspace surfaces across the dashboard.

Per Jenna 2026-09-02: "lets hide build a deck features, workspace and all
that entails from everywhere in the dashboard".

Approach: use the existing Live Features flag system rather than delete
code. That way an admin can un-hide everything from
Admin > Settings > Live Features later without any code change.

Concrete splices (all byte-safe; no StrReplace on index.html):

1) app.py
   - DEFAULT_LIVE_FEATURES: flip 'collaborate' + 'deckBuilder' to False
   - DEFAULT_HIDDEN_PRODUCTS: flip 'workspace' to True

2) templates/admin.html
   - Live Features grid: add a new 'Deck Builder' checkbox card
   - JS default state: flip 'deckBuilder' to false

3) templates/index.html
   - liveFeatures init: flip 'collaborate' + 'deckBuilder' to false
   - Tag surfaces with data-feature="deckBuilder" so the existing
     applyFeatureVisibility() framework hides them:
       - AI Deck sub-tab button
       - #ai-tab-deck content div
       - #profileIqDeckLink export item
       - #profileIqCombinedDeckLink export item
   - Extend applyLiveFeatureVisibility() to inject a <style> block +
     body class that hides every .deck-btn 'Add to Deck' module button
     and the Deck Builder modal when deckBuilder is off. Admin bypass
     preserved so super_admin still has access via the admin panel.

Idempotent: every splice is a no-op if the target is already applied.
"""
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]


def splice(src: str, old: str, new: str, desc: str) -> tuple[str, bool]:
    """Return (new_src, applied_bool). Idempotent: if already-applied
    marker (the `new` string) is found and `old` is absent, no-op."""
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

APP_OLD_1 = "    'content': True, 'collaborate': True, 'deckBuilder': True, 'rankers': True,"
APP_NEW_1 = "    'content': True, 'collaborate': False, 'deckBuilder': False, 'rankers': True,"

# Context: the workspace line sits inside DEFAULT_HIDDEN_PRODUCTS between
# shareOfTimeIQ and helmIQ. Include neighboring lines to be uniquely-anchored.
APP_OLD_2 = """    'shareOfTimeIQ': False,
    'workspace': False,
    'helmIQ': False,"""
APP_NEW_2 = """    'shareOfTimeIQ': False,
    'workspace': True,
    'helmIQ': False,"""


def edit_app_py():
    print("app.py:")
    src = APP_PY.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, APP_OLD_1, APP_NEW_1, "DEFAULT_LIVE_FEATURES collaborate+deckBuilder -> False")
    src, _ = splice(src, APP_OLD_2, APP_NEW_2, "DEFAULT_HIDDEN_PRODUCTS workspace -> True")
    if src != orig:
        APP_PY.write_text(src, encoding='utf-8')
        print(f"  wrote {APP_PY}")


# ------------------------------------------------------------------
# 2) templates/admin.html
# ------------------------------------------------------------------
ADMIN_HTML = REPO / 'templates' / 'admin.html'

# 2a. Add Deck Builder card in Live Features grid (right after Workspace)
ADMIN_OLD_1 = """                            <!-- Workspace (formerly Collaborate) -->
                            <label class="checkbox-group" style="padding: 0.75rem; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color); display: flex; align-items: center; gap: 0.5rem;">
                                <input type="checkbox" class="live-feature-checkbox" data-feature="collaborate" checked onchange="updateLiveFeature('collaborate', this.checked)">
                                <span style="font-size: 1.25rem;">🗂️</span>
                                <span style="font-size: 0.9rem;">Workspace</span>
                            </label>
                            
                            <!-- View Numbers (Subscriber IQ tab) -->"""

ADMIN_NEW_1 = """                            <!-- Workspace (formerly Collaborate) -->
                            <label class="checkbox-group" style="padding: 0.75rem; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color); display: flex; align-items: center; gap: 0.5rem;">
                                <input type="checkbox" class="live-feature-checkbox" data-feature="collaborate" onchange="updateLiveFeature('collaborate', this.checked)">
                                <span style="font-size: 1.25rem;">🗂️</span>
                                <span style="font-size: 0.9rem;">Workspace</span>
                            </label>
                            
                            <!-- Deck Builder (Add to Deck + Analysis Deck exports + AI Deck sub-tab + Deck Builder modal) -->
                            <label class="checkbox-group" style="padding: 0.75rem; background: var(--bg-card); border-radius: 8px; border: 1px solid var(--border-color); display: flex; align-items: center; gap: 0.5rem;">
                                <input type="checkbox" class="live-feature-checkbox" data-feature="deckBuilder" onchange="updateLiveFeature('deckBuilder', this.checked)">
                                <span style="font-size: 1.25rem;">📊</span>
                                <span style="font-size: 0.9rem;">Deck Builder</span>
                            </label>
                            
                            <!-- View Numbers (Subscriber IQ tab) -->"""

# 2b. Flip the JS default in the local state object so the grid renders
# unchecked before the server fetch lands. Anchor is line ~7692 which
# sits inside a small object literal.
ADMIN_OLD_2 = """            deckBuilder: true,"""
ADMIN_NEW_2 = """            deckBuilder: false,"""


def edit_admin_html():
    print("templates/admin.html:")
    src = ADMIN_HTML.read_text(encoding='utf-8')
    orig = src
    src, _ = splice(src, ADMIN_OLD_1, ADMIN_NEW_1, "add Deck Builder checkbox card + drop stale 'checked'")
    src, _ = splice(src, ADMIN_OLD_2, ADMIN_NEW_2, "JS default deckBuilder -> false")
    if src != orig:
        ADMIN_HTML.write_text(src, encoding='utf-8')
        print(f"  wrote {ADMIN_HTML}")


# ------------------------------------------------------------------
# 3) templates/index.html
# ------------------------------------------------------------------
INDEX_HTML = REPO / 'templates' / 'index.html'

# 3a. Flip liveFeatures init: collaborate + deckBuilder -> false.
# Anchor a multi-line block for uniqueness.
INDEX_OLD_1 = """            content: true,
            collaborate: true,
            deckBuilder: true,
            rankers: true,"""

INDEX_NEW_1 = """            content: true,
            collaborate: false,
            deckBuilder: false,
            rankers: true,"""

# 3b. AI Deck sub-tab button (line ~21965)
INDEX_OLD_2 = """                    <button class="ai-tab" onclick="showAITab('deck')">📊 Deck</button>"""
INDEX_NEW_2 = """                    <button class="ai-tab" data-feature="deckBuilder" onclick="showAITab('deck')">📊 Deck</button>"""

# 3c. AI Deck content div (line ~22009)
INDEX_OLD_3 = """                    <div id="ai-tab-deck" class="ai-tab-content">
                        <p class="ai-description">Generate a presentation deck for your business question.</p>"""
INDEX_NEW_3 = """                    <div id="ai-tab-deck" class="ai-tab-content" data-feature="deckBuilder">
                        <p class="ai-description">Generate a presentation deck for your business question.</p>"""

# 3d. Analysis Deck export link (line ~22032)
INDEX_OLD_4 = """                            <a href="javascript:void(0)" id="profileIqDeckLink" onclick="exportProfileIqDeck(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">🎨 Analysis Deck (.pptx)</a>"""
INDEX_NEW_4 = """                            <a href="javascript:void(0)" id="profileIqDeckLink" data-feature="deckBuilder" onclick="exportProfileIqDeck(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">🎨 Analysis Deck (.pptx)</a>"""

# 3e. Combined Deck export link (line ~22033)
INDEX_OLD_5 = """                            <a href="javascript:void(0)" id="profileIqCombinedDeckLink" onclick="exportProfileIqCombinedDeck(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">📊 Combined Deck (All Open Profiles)</a>"""
INDEX_NEW_5 = """                            <a href="javascript:void(0)" id="profileIqCombinedDeckLink" data-feature="deckBuilder" onclick="exportProfileIqCombinedDeck(); const d=document.getElementById('tabExportDropdown');if(d)d.style.display='none';">📊 Combined Deck (All Open Profiles)</a>"""

# 3f. Extend applyLiveFeatureVisibility() to inject the body-class hiding
# rules for .deck-btn buttons + the Deck Builder modal. Splice right
# before the closing brace of the function so the tail structure is
# preserved. Use the tail 5 lines as the unique anchor.
INDEX_OLD_6 = """            const insightsSubTabs = document.getElementById('insightsSubTabs');
            if (insightsSubTabs) {
                // If the currently active tab is hidden, switch to the first visible tab
                const activeTab = insightsSubTabs.querySelector('.insights-tab.active');
                if (activeTab && activeTab.style.display === 'none') {
                    const firstVisibleTab = insightsSubTabs.querySelector('.insights-tab:not([style*="display: none"])');
                    if (firstVisibleTab) {
                        firstVisibleTab.click();
                    }
                }
            }
        }
        
        // Helper function to apply visibility to a single feature element"""

INDEX_NEW_6 = """            const insightsSubTabs = document.getElementById('insightsSubTabs');
            if (insightsSubTabs) {
                // If the currently active tab is hidden, switch to the first visible tab
                const activeTab = insightsSubTabs.querySelector('.insights-tab.active');
                if (activeTab && activeTab.style.display === 'none') {
                    const firstVisibleTab = insightsSubTabs.querySelector('.insights-tab:not([style*="display: none"])');
                    if (firstVisibleTab) {
                        firstVisibleTab.click();
                    }
                }
            }
            
            // 2026-09-02: Deck Builder off = hide every 'Add to Deck' (.deck-btn)
            // module button + the Deck Builder modal shell + the Deck launcher
            // in the collaboration/workspace tab. Admin bypass preserved: super
            // admin/admin still see everything so the toggle can be re-enabled
            // from Admin -> Settings -> Live Features. Injects a single <style>
            // tag once, then toggles a body class on every render.
            try {
                const deckOff = (liveFeatures.deckBuilder === false) && !isAdmin;
                document.body.classList.toggle('bg-hide-deck-builder', deckOff);
                if (!document.getElementById('bgHideDeckBuilderStyles')) {
                    const s = document.createElement('style');
                    s.id = 'bgHideDeckBuilderStyles';
                    s.textContent = 'body.bg-hide-deck-builder .deck-btn,' +
                        'body.bg-hide-deck-builder #deckBuilderModal,' +
                        'body.bg-hide-deck-builder #deckBuilderStyles,' +
                        'body.bg-hide-deck-builder [id="deckBuilderTitleInput"],' +
                        'body.bg-hide-deck-builder .workspace-selector-bar { display: none !important; }';
                    document.head.appendChild(s);
                }
            } catch (_) {}
        }
        
        // Helper function to apply visibility to a single feature element"""


def edit_index_html():
    print("templates/index.html:")
    src = INDEX_HTML.read_text(encoding='utf-8')
    orig = src
    bytes_before = len(src.encode('utf-8'))
    src, _ = splice(src, INDEX_OLD_1, INDEX_NEW_1, "liveFeatures init collaborate+deckBuilder -> false")
    src, _ = splice(src, INDEX_OLD_2, INDEX_NEW_2, "AI Deck sub-tab button data-feature")
    src, _ = splice(src, INDEX_OLD_3, INDEX_NEW_3, "#ai-tab-deck content data-feature")
    src, _ = splice(src, INDEX_OLD_4, INDEX_NEW_4, "#profileIqDeckLink data-feature")
    src, _ = splice(src, INDEX_OLD_5, INDEX_NEW_5, "#profileIqCombinedDeckLink data-feature")
    src, _ = splice(src, INDEX_OLD_6, INDEX_NEW_6, "applyLiveFeatureVisibility hide-deck-builder body class")
    if src != orig:
        INDEX_HTML.write_text(src, encoding='utf-8')
        bytes_after = len(src.encode('utf-8'))
        print(f"  wrote {INDEX_HTML} (delta={bytes_after - bytes_before:+d} bytes)")


def main() -> int:
    edit_app_py()
    edit_admin_html()
    edit_index_html()
    print("\nAll splices applied. Next steps:")
    print("  1) python3 scripts/validate_index_html.py")
    print("  2) commit + push submodule + parent to main")
    print("  3) flush S3 admin_live_features.json to disable both flags immediately")
    return 0


if __name__ == '__main__':
    sys.exit(main())
