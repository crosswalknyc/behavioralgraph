"""The legacy Prometheus chat source as the suites read it (rebuild stage 5,
2026-11-20; stage 7, 2026-11-23).

Stage 5 moved the cores out of ``prometheus/legacy/chat.py`` into
``prometheus/legacy/core_*.py``. Stage 7 (Jenna 2026-11-23: "shouldnt we
optimize by moving it all somewhere where it can live together?") moves
the rest, and the cores, into organized ``prometheus/<family>.py``
modules. 129 suites open chat.py by path and pin its text (``'...' in
src``, ``src.index(a) < src.index(b)``), and most of those literals live
inside moved blocks. Those pins are not weakened: they read
``legacy_view()`` instead, which is chat.py with every extracted block
spliced back at the marker the extractor left
(``# <<extracted: <module> block N>>``; ``<module>`` is a legacy core name
such as ``core_read`` or a dotted import path such as
``prometheus.generated_read``), the ``_C.`` indirection stripped, so the
text reads as it did before any move and every ordering check holds. The
view is byte-identical across moves by construction: that is the proof
each move carries.

The line ratchets count the LEGACY CODE, never the view: ``legacy_lines()``
is chat.py plus every ``core_*.py`` still under ``prometheus/legacy/``,
against ``LEGACY_CEILING``, lowered at each move so the legacy code can
never grow back. ``chat_py_lines()`` is the real chat.py on its own.

Self-contained: finds ``prometheus/legacy/`` by walking up from this file
(bg-webapp/scripts/) or from the parent repo (scripts/ beside bg-webapp/).
The parent repo carries a byte-equal twin.
"""
from __future__ import annotations

import glob
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_MARK_RX = re.compile(r'^([ \t]*)# <<extracted: ([A-Za-z0-9_.]+) block (\d+)>>[ \t]*$', re.M)
_BLOCK_RX = re.compile(r'^# <<block: (\d+)>>\n', re.M)
_REIMPORT_RX = re.compile(r'^# <<reimport: [A-Za-z0-9_.]+>>\n.*?^# <</reimport>>\n', re.M | re.S)
_HOST_GET = re.compile(r'(?<![\w.])_C\.get\(')
_HOST_ATTR = re.compile(r'(?<![\w.])_C\.(?=\w)')

# The legacy code's line ceiling (chat.py plus the cores still under
# prometheus/legacy/). Lowered at every move; it never goes up. History:
# 19,900 on the pre-stage-5 chat.py; 20,212 legacy lines on 2026-11-23
# before stage 7's first move; 19,355 after it (the library match, thread
# history, model chain and date snapshot families moved out).
LEGACY_CEILING = 19_400


def legacy_dir() -> str:
    d = _HERE
    for _ in range(6):
        for cand in (os.path.join(d, 'prometheus', 'legacy'), os.path.join(d, 'bg-webapp', 'prometheus', 'legacy')):
            if os.path.exists(os.path.join(cand, 'chat.py')):
                return cand
        d = os.path.dirname(d)
    raise FileNotFoundError('prometheus/legacy/chat.py not found above ' + _HERE)


def package_root() -> str:
    """The directory that holds the ``prometheus`` package (bg-webapp/)."""
    return os.path.dirname(os.path.dirname(legacy_dir()))


def module_path(name: str) -> str:
    """The file a marker's module name points at: a bare name is a legacy
    core under prometheus/legacy/; a dotted name is an import path from
    the package root (``prometheus.generated_read`` ->
    bg-webapp/prometheus/generated_read.py)."""
    if '.' in name:
        return os.path.join(package_root(), *name.split('.')) + '.py'
    return os.path.join(legacy_dir(), name + '.py')


def chat_py_text() -> str:
    """The real chat.py, byte for byte."""
    with open(os.path.join(legacy_dir(), 'chat.py'), encoding='utf-8') as fh:
        return fh.read()


def chat_py_lines() -> int:
    """The real chat.py's line count (its own ratchet), as ``splitlines`` counts it."""
    return len(chat_py_text().splitlines())


def legacy_core_paths() -> list:
    """The stage 5 cores still under prometheus/legacy/ (none once stage 7
    has moved them all)."""
    return sorted(glob.glob(os.path.join(legacy_dir(), 'core_*.py')))


def legacy_lines() -> int:
    """The legacy code's line count: chat.py plus every core_*.py still
    under prometheus/legacy/. This is what the line ratchet counts."""
    n = chat_py_lines()
    for p in legacy_core_paths():
        with open(p, encoding='utf-8') as fh:
            n += len(fh.read().splitlines())
    return n


def _unhost(src: str) -> str:
    """Drop the ``_C.`` (chat core) indirection an extracted module reads
    chat.py through, so its text reads as it did inside chat.py.
    ``_H.`` stays: chat.py itself reads the host that way."""
    src = _HOST_GET.sub('globals().get(', src)
    return _HOST_ATTR.sub('', src)


def _blocks_of_text(src: str) -> dict:
    """{block number: moved text} for an extracted module's source: the
    text after its header (docstring and imports) is split at its
    ``# <<block: N>>`` lines; each block ends before the next one or before
    ``__all__``."""
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


def _core_blocks(path: str) -> dict:
    with open(path, encoding='utf-8') as fh:
        return _blocks_of_text(fh.read())


def legacy_view_from(chat_text: str, modules: dict) -> str:
    """The view computed from texts instead of files: ``chat_text`` is a
    chat.py source, ``modules`` maps a marker's module name to the source
    of the module that holds its blocks. A module not in the mapping is
    read from disk. The extractor proves a move on this before it writes."""
    cache = {}

    def _blocks(name):
        if name not in cache:
            if name in modules:
                cache[name] = _blocks_of_text(modules[name]) if modules[name] is not None else {}
            else:
                path = module_path(name)
                cache[name] = _core_blocks(path) if os.path.exists(path) else {}
        return cache[name]

    def _splice(m):
        name, n = m.group(2), int(m.group(3))
        blk = _blocks(name).get(n)
        return m.group(0) if blk is None else m.group(1) + blk
    text = _REIMPORT_RX.sub('', chat_text)          # the tail re-imports the moves added
    return _MARK_RX.sub(_splice, text)


def legacy_view() -> str:
    """chat.py with every extracted block spliced back at its marker."""
    return legacy_view_from(chat_py_text(), {})


def legacy_view_origins() -> list:
    """One entry per line of ``legacy_view()`` saying where the line lives
    now: ``('chat', i)`` for line ``i`` (0-based) of the real chat.py, or
    ``('block', module, n, j, i)`` for line ``j`` of block ``n`` of the
    module named in the marker, whose marker is line ``i`` of chat.py. The
    extractor uses this to find, for a span of the view, the chat.py lines
    and the module blocks it must rewrite."""
    real = chat_py_text()
    # the reimport blocks are whole lines; drop their line indexes from the map
    removed = set()
    for m in _REIMPORT_RX.finditer(real):
        first = real.count('\n', 0, m.start())
        last = real.count('\n', 0, m.end())          # the block ends with '\n': its last line is last - 1
        removed.update(range(first, last))
    real_lines = real.split('\n')
    origins_chat = [i for i in range(len(real_lines)) if i not in removed]
    stripped_lines = [real_lines[i] for i in origins_chat]
    out = []
    cache = {}
    for idx, ln in enumerate(stripped_lines):
        m = _MARK_RX.match(ln)
        if m:
            name, n = m.group(2), int(m.group(3))
            if name not in cache:
                path = module_path(name)
                cache[name] = _core_blocks(path) if os.path.exists(path) else {}
            blk = cache[name].get(n)
            if blk is None:
                out.append(('chat', origins_chat[idx]))
                continue
            for j, _ in enumerate(blk.split('\n')):
                out.append(('block', name, n, j, origins_chat[idx]))
        else:
            out.append(('chat', origins_chat[idx]))
    return out


__all__ = ['legacy_dir', 'package_root', 'module_path', 'chat_py_text', 'chat_py_lines',
           'legacy_core_paths', 'legacy_lines', 'LEGACY_CEILING', 'legacy_view', 'legacy_view_from',
           'legacy_view_origins']
