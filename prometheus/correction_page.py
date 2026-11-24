"""The page a staff correction link opens: the HTML shell and its escape.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401

# <<moved>>
# <<block: 1>>
def _pm_correct_page(title, body_html):
    return (
        "<html><head><title>" + title + "</title>"
        "<meta name='viewport' content='width=device-width,"
        "initial-scale=1'></head>"
        "<body style='margin:0;background:#E9E8E1;font-family:"
        "Helvetica,Arial,sans-serif;color:#5C6560'>"
        "<div style='max-width:640px;margin:0 auto;padding:40px 24px'>"
        "<div style='font-size:11px;letter-spacing:.12em;"
        "text-transform:uppercase;color:#5E7E12;font-weight:600'>"
        "Prometheus</div>"
        f"<h1 style='font-size:22px;color:#0C1618;font-weight:700;"
        f"margin:10px 0 18px'>{title}</h1>"
        f"{body_html}"
        "</div></body></html>")


# <<block: 2>>
def _pm_correct_esc(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


__all__ = ['_pm_correct_page', '_pm_correct_esc']
