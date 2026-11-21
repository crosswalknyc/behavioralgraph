"""The dashboard page as the browser sees it.

``templates/index.html`` loads some of its own CSS and JS from static/
files (the front-end split, Jenna 2026-10-14; the Digital Twins IQ CSS
and JS, Jenna 2026-11-20: SPLIT, the page back under its size budget).
A suite that reads the page's own code reads it through here: ``text()``
is index.html with every split file inlined back at the line that
references it, so a block that moved into static/ is still found at its
place on the page, byte for byte as it stood before the split. ``path()``
writes that page to a temp file for a node harness that takes a path.

The split registry is the validator's (``scripts/validate_index_html.py``
SPLIT_FILES); this module reads it so the two never drift.

    import _index_page
    html = _index_page.text(ROOT)          # ROOT = the bg-webapp checkout
    page = _index_page.path(ROOT)          # a file path for node
"""
from __future__ import annotations

import atexit
import importlib.util
import os
import re
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.dirname(_HERE)
_CACHE: dict[str, str] = {}
_PATHS: dict[str, str] = {}


def _split_files(root: str) -> list[tuple[str, str, str]]:
    """(reference, repo-relative path, kind) from the validator's registry."""
    spec = importlib.util.spec_from_file_location(
        '_pm_validate_index_html', os.path.join(root, 'scripts', 'validate_index_html.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [(ref, rel, kind) for ref, rel, kind, _floor in mod.SPLIT_FILES]


def _inline_back(src: str, ref: str, body: str, kind: str) -> str:
    """Put a split file's text back where the page references it. The
    reference line keeps its indent; the block reads exactly as the
    inline block did: ``<style>`` / ``<script>``, a newline, the file,
    the indent, the closing tag."""
    tag = 'style' if kind == 'style' else 'script'
    pat = re.compile(r'^([ \t]*)<(?:link|script)\b[^>\n]*' + re.escape(ref) + r'[^>\n]*>(?:</script>)?[ \t]*$', re.M)
    hits = list(pat.finditer(src))
    if len(hits) != 1:
        raise RuntimeError(f"the page references {ref!r} {len(hits)}x; expected exactly one line")
    m = hits[0]
    indent = m.group(1)
    block = f"{indent}<{tag}>\n{body}{indent}</{tag}>"
    return src[:m.start()] + block + src[m.end():]


def text(root: str | None = None) -> str:
    """index.html with every split file inlined back where the page loads it."""
    root = os.path.abspath(root or _DEFAULT_ROOT)
    if root in _CACHE:
        return _CACHE[root]
    with open(os.path.join(root, 'templates', 'index.html'), encoding='utf-8') as fh:
        src = fh.read()
    for ref, rel, kind in _split_files(root):
        fp = os.path.join(root, rel)
        if not os.path.isfile(fp):
            raise FileNotFoundError(f"split file missing: {fp} (the page references {ref!r})")
        with open(fp, encoding='utf-8') as fh:
            src = _inline_back(src, ref, fh.read(), kind)
    _CACHE[root] = src
    return src


def path(root: str | None = None) -> str:
    """A file holding ``text(root)`` for a harness that takes a path
    (node reads it with fs.readFileSync). One temp file per process."""
    root = os.path.abspath(root or _DEFAULT_ROOT)
    if root in _PATHS and os.path.isfile(_PATHS[root]):
        return _PATHS[root]
    fd, p = tempfile.mkstemp(prefix='index_page_', suffix='.html')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(text(root))
    _PATHS[root] = p
    atexit.register(lambda: os.path.exists(p) and os.unlink(p))
    return p
