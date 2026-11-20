#!/usr/bin/env python3
"""The three cores live outside chat.py and chat.py only shrinks (rebuild
stage 5, Jenna 2026-11-20).

  * prometheus/legacy/core_analyze.py (page analysis over the open
    profile), core_read.py (the generated read) and core_interpret.py (the
    brief drafter and its rulebook) each carry their moved blocks behind a
    ``# <<moved>>`` header; chat.py carries one marker per block and
    re-imports the names at its tail, so ``chat.<name>`` resolves to the
    core's own function and the host binds the same objects.
  * ``_C.globals()`` is chat.py's namespace (the lanes a core hands it to
    read chat's helpers from it).
  * ``scripts/_pm_legacy_view.legacy_view()`` splices every block back at
    its marker: each moved def appears exactly once, no ``_C.`` is left in
    it, and no marker survives. The suites that pin the legacy text read
    this view.
  * The ratchet moves down with the file: chat.py stays under 13,700 lines
    (it was 19,831 before the move, 13,625 after). It never grows again.
  * The extractor that made the move (scripts/extract_legacy_core.py) is
    importable and its dry run on an already-moved name refuses.

Hermetic: no network, no S3, no model. Run from either repo.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB = os.path.dirname(_HERE)
if not os.path.exists(os.path.join(_WEB, 'prometheus', 'legacy', 'chat.py')):
    _WEB = os.path.join(_WEB, 'bg-webapp')
for p in (_WEB, os.path.join(_WEB, 'scripts')):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import _pm_legacy_view as LV  # noqa: E402

LEGACY = os.path.join(_WEB, 'prometheus', 'legacy')
CORES = {
    'core_analyze': ('_pm_analyze_core',),
    'core_read': ('_pm_generate_metrics_response', '_pm_generate_read_core'),
    'core_interpret': ('_synth_chat_interpret_prompts', '_pm_interpret_core'),
}
RATCHET = 13_700


def _read(*p):
    with open(os.path.join(*p), encoding='utf-8') as fh:
        return fh.read()


class TheCoresLiveOutside(unittest.TestCase):
    def test_each_core_module_carries_its_blocks(self):
        for mod, names in CORES.items():
            src = _read(LEGACY, mod + '.py')
            self.assertIn('\n# <<moved>>\n', src, mod)
            self.assertIn('from prometheus.legacy import H as _H, C as _C', src, mod)
            for i, name in enumerate(names, 1):
                self.assertIn(f'# <<block: {i}>>\ndef {name}(', src, (mod, name))
            self.assertIn('__all__ = [' + ', '.join(repr(n) for n in names) + ']', src, mod)

    def test_chat_carries_one_marker_per_block_and_the_reimport(self):
        chat = LV.chat_py_text()
        for mod, names in CORES.items():
            for i, name in enumerate(names, 1):
                self.assertEqual(chat.count(f'# <<extracted: {mod} block {i}>>'), 1, (mod, i))
                self.assertNotIn(f'\ndef {name}(', chat, name)
            self.assertEqual(chat.count(f'# <<reimport: {mod}>>'), 1, mod)
            self.assertIn(f'from prometheus.legacy.{mod} import (', chat, mod)

    def test_the_view_reads_as_before_the_move(self):
        view = LV.legacy_view()
        self.assertNotIn('<<extracted:', view)
        self.assertNotIn('<<reimport:', view)
        self.assertNotIn('<<block:', view)
        self.assertNotIn('_C.', view)
        for names in CORES.values():
            for name in names:
                self.assertEqual(view.count(f'\ndef {name}('), 1, name)
        # the re-import at the tail is the move's, not the view's
        self.assertNotIn('from prometheus.legacy.core_analyze import', view)

    def test_the_ratchet_moved_down(self):
        n = LV.chat_py_lines()
        self.assertLess(n, RATCHET, f'chat.py is {n} lines; it never grows (ratchet {RATCHET})')
        # the view is the old file: the move took the lines out of chat.py and nowhere else
        self.assertGreater(len(LV.legacy_view().splitlines()), n + 5_000)


class TheCoresResolve(unittest.TestCase):
    def test_chat_names_are_the_cores_functions(self):
        root = os.path.dirname(_WEB)
        if root not in sys.path:
            sys.path.insert(0, os.path.join(root, 'scripts'))
        try:
            from _pm_behavior import load_chat
        except Exception:
            self.skipTest('the behavior harness (parent scripts/_pm_behavior.py) is not beside this checkout')
        h = load_chat()
        chat = h.chat
        import importlib
        for mod, names in CORES.items():
            core = importlib.import_module('prometheus.legacy.' + mod)
            for name in names:
                self.assertIs(getattr(chat, name), getattr(core, name), (mod, name))
        for name in ('_pm_analyze_core', '_pm_interpret_core', '_pm_generate_read_core'):
            self.assertIn(name, chat._EXPORTS)
        from prometheus.legacy import C
        self.assertIs(C.globals(), vars(chat))

    def test_the_extractor_refuses_a_moved_name(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('extract_legacy_core', os.path.join(_HERE, 'extract_legacy_core.py'))
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        with self.assertRaises(SystemExit):
            ex.extract('core_again', ['_pm_analyze_core'], 'x', dry_run=True)

    def test_no_em_dash_in_the_moved_modules(self):
        for mod in CORES:
            src = _read(LEGACY, mod + '.py')
            head = src.split('# <<moved>>')[0]
            self.assertNotIn('\u2014', head, mod)


if __name__ == '__main__':
    unittest.main(verbosity=1)
