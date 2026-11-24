#!/usr/bin/env python3
"""The legacy chat code lives in organized modules and only shrinks (rebuild
stage 5, Jenna 2026-11-20; stage 7, Jenna 2026-11-23: "shouldnt we optimize
by moving it all somewhere where it can live together?").

  * Every block extracted out of prometheus/legacy/chat.py lives in the
    module its marker names: a stage 5 core (``prometheus/legacy/core_*.py``)
    or an organized module (``prometheus/<family>.py``, a dotted marker).
    Each module carries its blocks behind a ``# <<moved>>`` header; chat.py
    carries one marker per block and re-imports the names at its tail, so
    ``chat.<name>`` resolves to the module's own object and the host binds
    the same objects.
  * ``_C.globals()`` is chat.py's namespace (the lanes a module hands it to
    read chat's helpers from it); every other name chat.py binds is read
    through ``_C.<name>`` at call time, so a patch of ``chat.<name>`` is
    seen by every caller, wherever the caller lives now.
  * ``scripts/_pm_legacy_view.legacy_view()`` splices every block back at
    its marker: each moved def appears exactly once, no ``_C.`` is left in
    it, and no marker survives. The suites that pin the legacy text read
    this view; it is byte-identical across moves by construction.
  * The ratchet counts the legacy code and moves down with it: chat.py
    plus every core still under prometheus/legacy/ stays under
    ``LEGACY_CEILING`` (20,212 lines before stage 7's first move), and
    chat.py on its own under ``CHAT_RATCHET``. Neither ever grows again.
  * The extractor (scripts/extract_legacy_core.py) is importable and its
    dry run on an already-moved name refuses.

Hermetic: no network, no S3, no model. Run from either repo.
"""
from __future__ import annotations

import ast
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
CHAT_RATCHET = 5_450           # chat.py on its own (13,625 after stage 5; 12,777 after stage 7's first move; 10,839 after move 2; 7,838 after move 3; 6,488 after move 4; 5,403 after move 5)
_MARK = re.compile(r'^# <<extracted: ([A-Za-z0-9_.]+) block (\d+)>>$', re.M)


def _read(*p):
    with open(os.path.join(*p), encoding='utf-8') as fh:
        return fh.read()


def _markers():
    """{module: {block number: marker count}} as chat.py carries them."""
    out = {}
    for m in _MARK.finditer(LV.chat_py_text()):
        out.setdefault(m.group(1), {}).setdefault(int(m.group(2)), 0)
        out[m.group(1)][int(m.group(2))] += 1
    return out


def _names_of(block_text):
    names = []
    for st in ast.parse(block_text).body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(st.name)
        elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        names.append(sub.id)
    return names


class TheModulesCarryTheirBlocks(unittest.TestCase):
    def test_at_least_the_first_families_have_moved(self):
        mods = _markers()
        for must in ('prometheus.library_match', 'prometheus.thread_history', 'prometheus.model_chain', 'prometheus.date_snapshot'):
            self.assertIn(must, mods)

    def test_each_module_carries_its_blocks(self):
        chat = LV.chat_py_text()
        for mod, blocks in _markers().items():
            path = LV.module_path(mod)
            self.assertTrue(os.path.exists(path), (mod, path))
            src = _read(path)
            self.assertIn('\n# <<moved>>\n', src, mod)
            self.assertIn('from prometheus.legacy import H as _H, C as _C', src, mod)
            held = LV._core_blocks(path)
            self.assertEqual(sorted(held), sorted(blocks), mod)          # every marker has its block, every block its marker
            names = []
            for n in sorted(held):
                self.assertEqual(blocks[n], 1, (mod, n))                # one marker per block
                self.assertIn(f'\n# <<block: {n}>>\n', src, (mod, n))
                for nm in _names_of(held[n]):
                    if nm not in names:                                 # a name bound more than once (a def and its wrappers) is one name
                        names.append(nm)
            self.assertTrue(names, mod)
            self.assertIn('__all__ = [' + ', '.join(repr(n) for n in names) + ']', src, mod)
            # chat.py re-imports every moved name at its tail, from the module's own import path
            self.assertEqual(chat.count(f'# <<reimport: {mod}>>'), 1, mod)
            imp = mod if '.' in mod else f'prometheus.legacy.{mod}'
            tail = chat.split(f'# <<reimport: {mod}>>', 1)[1].split('# <</reimport>>', 1)[0]
            self.assertIn(f'from {imp} import (', tail, mod)
            for n in names:
                self.assertIn(f'    {n},\n', tail, (mod, n))
                if any(isinstance(st, ast.FunctionDef) and st.name == n for blk in held.values() for st in ast.parse(blk).body):
                    self.assertNotIn(f'\ndef {n}(', chat, n)

    def test_the_view_reads_as_before_the_move(self):
        view = LV.legacy_view()
        self.assertNotIn('<<extracted:', view)
        self.assertNotIn('<<reimport:', view)
        self.assertNotIn('<<block:', view)
        self.assertNotIn('_C.', view)
        for mod in _markers():
            for blk in LV._core_blocks(LV.module_path(mod)).values():
                for st in ast.parse(blk).body:
                    if isinstance(st, ast.FunctionDef):
                        self.assertEqual(view.count(f'\ndef {st.name}('), 1, st.name)
        # the re-imports at the tail are the moves', not the view's
        self.assertNotIn('from prometheus.library_match import', view)

    def test_the_ratchets_moved_down(self):
        n = LV.chat_py_lines()
        self.assertLess(n, CHAT_RATCHET, f'chat.py is {n} lines; it never grows (ratchet {CHAT_RATCHET})')
        total = LV.legacy_lines()
        self.assertLess(total, LV.LEGACY_CEILING, f'the legacy code is {total} lines; it never grows (ceiling {LV.LEGACY_CEILING})')
        self.assertLess(LV.LEGACY_CEILING, 20_212, 'the ceiling only comes down')
        # the view is the old file: the moves took the lines out of the legacy code and nowhere else
        self.assertGreater(len(LV.legacy_view().splitlines()), n + 5_000)

    def test_every_proxy_read_is_a_name_chat_binds(self):
        view = ast.parse(LV.legacy_view())
        bound = set()
        for st in view.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(st.name)
            else:
                for sub in ast.walk(st):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        bound.add(sub.id)
                    elif isinstance(sub, ast.alias):
                        bound.add((sub.asname or sub.name).split('.')[0])
        for mod in _markers():
            src = _read(LV.module_path(mod))
            for attr in set(re.findall(r'(?<![\w.])_C\.(\w+)', src.split('# <<moved>>', 1)[1])):
                self.assertTrue(attr == 'globals' or attr in bound, (mod, attr))


class TheModulesResolve(unittest.TestCase):
    def test_chat_names_are_the_modules_objects(self):
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
        for mod in _markers():
            imp = mod if '.' in mod else f'prometheus.legacy.{mod}'
            module = importlib.import_module(imp)
            for blk in LV._core_blocks(LV.module_path(mod)).values():
                for name in _names_of(blk):
                    if name == '_pm_watch_notify':
                        # the harness's own baseline: a test process never mails, so load_chat
                        # replaces chat._pm_watch_notify with a no-op; the proxy rule is what
                        # lets that no-op reach every caller wherever the caller lives now
                        self.assertIsNot(getattr(chat, name), getattr(module, name), (mod, name))
                        continue
                    self.assertIs(getattr(chat, name), getattr(module, name), (mod, name))
        for name in ('_pm_analyze_core', '_pm_interpret_core', '_pm_generate_read_core'):
            self.assertIn(name, chat._EXPORTS)
        from prometheus.legacy import C
        self.assertIs(C.globals(), vars(chat))
        # a patch of chat.<name> is what a moved caller reads (the proxy rule)
        seen = []
        h.patch('_pm_resolve_snapshot_date', lambda t: seen.append(t) or '2026-01-02')
        try:
            draft = {'decision': 'existing_match', 'parent_s3_key': 'Example_Brand.csv'}
            self.assertTrue(chat._pm_promote_date_snapshot_ask(draft, 'how did this audience look as of January 2 2026'))
            self.assertTrue(seen)
            self.assertEqual(draft.get('snapshot_date'), '2026-01-02')
        finally:
            h.restore()

    def test_the_extractor_refuses_a_moved_name(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('extract_legacy_core', os.path.join(_HERE, 'extract_legacy_core.py'))
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        with self.assertRaises(SystemExit):
            ex.extract('prometheus.library_match', ['_shortlist_profile_matches'], 'x', dry_run=True)   # the module exists
        with self.assertRaises(SystemExit):
            ex.extract('prometheus.never_written', ['_no_such_name_in_the_legacy_text'], 'x', dry_run=True)

    def test_no_em_dash_in_the_moved_modules(self):
        for mod in _markers():
            src = _read(LV.module_path(mod))
            head = src.split('# <<moved>>')[0]
            self.assertNotIn('\u2014', head, mod)


if __name__ == '__main__':
    unittest.main(verbosity=1)
