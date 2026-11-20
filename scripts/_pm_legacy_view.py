"""The legacy Prometheus chat source as the suites read it (rebuild stage 5,
2026-11-20).

Stage 5 moves the cores out of ``prometheus/legacy/chat.py`` into
``prometheus/legacy/core_*.py``. 129 suites open chat.py by path and pin
its text (``'...' in src``, ``src.index(a) < src.index(b)``), and 683 of
those literals live inside the moved cores. Those pins are not weakened:
they read ``legacy_view()`` instead, which is chat.py with every extracted
core spliced back in at the marker the extractor left
(``# <<extracted: core_x block N>>``), the ``_C.`` indirection stripped, so the text
reads as it did before the move and every ordering check holds.

The line ratchets keep counting the real file: ``chat_py_lines()``.

Self-contained: finds ``prometheus/legacy/`` by walking up from this file
(bg-webapp/scripts/) or from the parent repo (scripts/ beside bg-webapp/).
The parent repo carries a byte-equal twin.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_MARK_RX = re.compile(r'^([ \t]*)# <<extracted: ([A-Za-z0-9_]+) block (\d+)>>[ \t]*$', re.M)
_BLOCK_RX = re.compile(r'^# <<block: (\d+)>>\n', re.M)
_REIMPORT_RX = re.compile(r'^# <<reimport: [A-Za-z0-9_]+>>\n.*?^# <</reimport>>\n', re.M | re.S)
_HOST_GET = re.compile(r'(?<![\w.])_C\.get\(')
_HOST_ATTR = re.compile(r'(?<![\w.])_C\.(?=\w)')


def legacy_dir() -> str:
    d = _HERE
    for _ in range(6):
        for cand in (os.path.join(d, 'prometheus', 'legacy'), os.path.join(d, 'bg-webapp', 'prometheus', 'legacy')):
            if os.path.exists(os.path.join(cand, 'chat.py')):
                return cand
        d = os.path.dirname(d)
    raise FileNotFoundError('prometheus/legacy/chat.py not found above ' + _HERE)


def chat_py_text() -> str:
    """The real chat.py, byte for byte."""
    with open(os.path.join(legacy_dir(), 'chat.py'), encoding='utf-8') as fh:
        return fh.read()


def chat_py_lines() -> int:
    """The real chat.py's line count (the ratchet), as ``splitlines`` counts it."""
    return len(chat_py_text().splitlines())


def _unhost(src: str) -> str:
    """Drop the ``_C.`` (chat core) indirection an extracted core reads
    chat.py through, so its text reads as it did inside chat.py.
    ``_H.`` stays: chat.py itself reads the host that way."""
    src = _HOST_GET.sub('globals().get(', src)
    return _HOST_ATTR.sub('', src)


def _core_blocks(path: str) -> dict:
    """{block number: moved text} for an extracted core: the text after its
    header (docstring and imports) is split at its ``# <<block: N>>`` lines;
    each block ends before the next one or before ``__all__``."""
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    start = src.find('\n# <<moved>>\n')
    end = src.rfind('\n\n\n__all__ = ')
    if start < 0:
        return {}
    body = src[start + len('\n# <<moved>>\n'):end if end > start else len(src)]
    out = {}
    parts = _BLOCK_RX.split(body)
    # parts = ['', '1', text1, '2', text2, ...]
    for i in range(1, len(parts) - 1, 2):
        out[int(parts[i])] = _unhost(parts[i + 1].rstrip('\n'))
    return out


def legacy_view() -> str:
    """chat.py with every extracted core spliced back at its marker."""
    d = legacy_dir()
    text = chat_py_text()

    cache = {}

    def _splice(m):
        name, n = m.group(2), int(m.group(3))
        path = os.path.join(d, name + '.py')
        if not os.path.exists(path):
            return m.group(0)
        if name not in cache:
            cache[name] = _core_blocks(path)
        blk = cache[name].get(n)
        return m.group(0) if blk is None else m.group(1) + blk
    text = _REIMPORT_RX.sub('', text)          # the tail re-import the move added
    return _MARK_RX.sub(_splice, text)


__all__ = ['legacy_dir', 'chat_py_text', 'chat_py_lines', 'legacy_view']
