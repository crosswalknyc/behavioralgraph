#!/usr/bin/env python3
"""Stop the self-serve nav sync from pinning Profile IQ over other products.

_syncSelfServeProductNav forces dashboardView to display:flex !important
whenever the seat has Profile IQ. It runs from loadAndApplyLiveFeatures,
which is fired and forgotten during boot and from several show
functions, so its completion lands after a product switch and pins
Profile IQ back on top of Rankers or Blue IQ.

templates/index.html is edited by read/replace/write, never an editor
tool: the tail has been truncated before.
"""
import io

PATH = 'templates/index.html'

OLD = """        if (has) {
            document.body.classList.add('cw-self-serve-products');
            document.body.classList.remove('cw-self-serve');
            var dv = document.getElementById('dashboardView');
            if (dv && window.CW_SS_PROFILE) {
                dv.style.setProperty('display', 'flex', 'important');
                dv.style.setProperty('visibility', 'visible', 'important');
            }
        } else if (window.CW_SELF_SERVE_PLAN) {"""

NEW = """        if (has) {
            document.body.classList.add('cw-self-serve-products');
            document.body.classList.remove('cw-self-serve');
            var dv = document.getElementById('dashboardView');
            // Only pin Profile IQ visible while Profile IQ is the product
            // on screen. This function runs from
            // loadAndApplyLiveFeatures, which is fired and forgotten
            // during boot and from several show functions, so its
            // completion used to land after a product switch and put
            // Profile IQ back on top of Rankers or Blue IQ. It pinned with
            // !important, which is why the chrome stayed up too:
            // _piqV4ViewVisible kept reading the view as visible, so
            // _piqV4PinChrome re-pinned the body-level hero and tab bar
            // instead of tearing them down (2026-09-29).
            var _ssDd = document.getElementById('viewNavDropdown');
            var _ssActive = (_ssDd && _ssDd.value) || 'profileIQ';
            if (dv && window.CW_SS_PROFILE && _ssActive === 'profileIQ') {
                dv.style.setProperty('display', 'flex', 'important');
                dv.style.setProperty('visibility', 'visible', 'important');
            } else if (dv && window.CW_SS_PROFILE) {
                console.warn('[self-serve nav] access sync finished while on '
                             + _ssActive + ' - not pinning Profile IQ');
            }
        } else if (window.CW_SELF_SERVE_PLAN) {"""


def main():
    with io.open(PATH, encoding='utf-8') as fh:
        src = fh.read()
    before = src.count('\n')
    orig = src

    n = src.count(OLD)
    assert n == 1, 'anchor count %d' % n
    src = src.replace(OLD, NEW, 1)

    assert src.count("_ssActive === 'profileIQ'") == 1
    assert src.count("dv.style.setProperty('display', 'flex', 'important')") == 1
    assert src.endswith('</html>\n'), 'tail damaged: %r' % src[-40:]
    assert src.count('<body') == orig.count('<body')
    assert len(src) > len(orig)

    with io.open(PATH, 'w', encoding='utf-8') as fh:
        fh.write(src)
    print('lines: %d -> %d' % (before, src.count('\n')))
    print('ends with </html>: %s' % src.rstrip().endswith('</html>'))


if __name__ == '__main__':
    main()
