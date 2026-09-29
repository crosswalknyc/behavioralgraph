#!/usr/bin/env python3
"""Stop a finished profile load from grabbing the view after a switch.

loadCachedResult and loadJobResult call showDashboardView() after their
await. Switching product while a profile CSV was in flight therefore
re-showed Profile IQ on top of whatever had just opened.

templates/index.html is edited by read/replace/write, never an editor
tool: the tail has been truncated before.
"""
import io

PATH = 'templates/index.html'

# The helper goes immediately above loadCachedResult.
HELPER_ANCHOR = "        async function loadCachedResult(fileKey) {\n"

HELPER = '''        // A profile load finishes asynchronously and used to take the
        // view unconditionally. Switching product while a profile CSV
        // was still in flight let the completion re-show Profile IQ on
        // top of the product that had just opened: renderDashboard and
        // showDashboardView both run after the await, and
        // showDashboardView calls showProfileIQ, the only thing in this
        // document that makes dashboardView visible. Unreachable until
        // Profile IQ started loading fast enough to switch away from
        // mid-flight (2026-09-29).
        function _piqActiveProduct() {
            var dd = document.getElementById('viewNavDropdown');
            return (dd && dd.value) || 'profileIQ';
        }
        function _piqTakeViewIfStillActive(productAtStart) {
            var now = _piqActiveProduct();
            if (now !== productAtStart) {
                // The data is rendered and cached either way; only the
                // view swap is dropped, so returning to Profile IQ
                // shows the profile without another fetch.
                console.warn('[view-switch] profile load finished after '
                             + 'switching from ' + productAtStart + ' to '
                             + now + ' - not taking the view');
                return false;
            }
            showDashboardView();
            return true;
        }

'''

EDITS = [
    # loadCachedResult: capture the product before the await.
    ("        async function loadCachedResult(fileKey) {\n"
     "            const tryKey = async (key) => {",
     "        async function loadCachedResult(fileKey) {\n"
     "            const _productAtStart = _piqActiveProduct();\n"
     "            const tryKey = async (key) => {"),
    ("                    renderDashboard(data.data, data.brand, data.date_range, data.display_override);\n"
     "                    showDashboardView();\n"
     "                    applyGenPopMode();",
     "                    renderDashboard(data.data, data.brand, data.date_range, data.display_override);\n"
     "                    _piqTakeViewIfStillActive(_productAtStart);\n"
     "                    applyGenPopMode();"),
    # loadJobResult: same race.
    ("        async function loadJobResult(jobId) {\n"
     "            try {",
     "        async function loadJobResult(jobId) {\n"
     "            const _productAtStart = _piqActiveProduct();\n"
     "            try {"),
    ("                    renderDashboard(data.data, data.brand, data.date_range);\n"
     "                    showDashboardView();",
     "                    renderDashboard(data.data, data.brand, data.date_range);\n"
     "                    _piqTakeViewIfStillActive(_productAtStart);"),
]


def main():
    with io.open(PATH, encoding='utf-8') as fh:
        src = fh.read()
    before_lines = src.count('\n')
    orig = src

    assert src.count(HELPER_ANCHOR) == 1, \
        'helper anchor not unique: %d' % src.count(HELPER_ANCHOR)
    assert '_piqTakeViewIfStillActive' not in src, 'already applied'
    src = src.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1)

    for old, new in EDITS:
        n = src.count(old)
        assert n == 1, 'anchor count %d for %r' % (n, old[:70])
        src = src.replace(old, new, 1)

    # showDashboardView must no longer be called from either completion.
    assert src.count('_piqTakeViewIfStillActive(_productAtStart);') == 2
    assert src.count('_productAtStart = _piqActiveProduct();') == 2
    assert src.endswith('</html>\n'), 'tail damaged: %r' % src[-40:]
    assert src.count('<body') == orig.count('<body'), 'body count changed'
    assert len(src) > len(orig), 'file shrank'

    with io.open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(src)
    print('lines: %d -> %d' % (before_lines, src.count('\n')))
    print('ends with </html>: %s' % src.rstrip().endswith('</html>'))


if __name__ == '__main__':
    main()
