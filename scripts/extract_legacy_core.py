#!/usr/bin/env python3
"""Move top-level code out of the legacy Prometheus chat module into an
organized module, behaviour unchanged (rebuild stage 5, 2026-11-20; stage
7, 2026-11-23: every family leaves prometheus/legacy/ for prometheus/).

    python3 scripts/extract_legacy_core.py --plan moves/step1.json [--dry-run]
    python3 scripts/extract_legacy_core.py --module prometheus.date_snapshot \
        --names _PM_SNAPSHOT_MONTHS,_pm_resolve_snapshot_date,_pm_promote_date_snapshot_ask \
        --doc "Point-in-time snapshots: a dated cut of an existing file." [--dry-run]

A plan is a JSON list of ``{"module": "prometheus.<name>", "doc": "...",
"names": [...]}`` entries applied together, all or nothing. A name is any
top-level binding of the legacy chat text: a def or a module-level
assignment (a constant, a compiled regex, a wrapped function). Every
binding of a name moves together (``_pm_generation_base`` is a def and two
wrapping assignments; all three go).

The source of truth is the COMBINED VIEW (``scripts/_pm_legacy_view
.legacy_view()``): chat.py with every block already extracted spliced back
at its marker, the text as it read before any move. A block to move may
live in chat.py today or in a stage 5 core (``prometheus/legacy/core_*.py``);
either way its text is taken from the view and rewritten fresh.

What the tool does, in order:

  1. Reads the view and parses it. The named top-level statements, with
     the unindented comment lines that sit directly above each, are the
     moved blocks.
  2. In the moved text every free name that the view binds at module level
     (a def, an assignment, a name bound inside a top-level try/if/with, a
     name imported from a ``prometheus.legacy.*`` family) becomes
     ``_C.<name>`` (the legacy chat proxy, resolved against chat.py's
     namespace at call time; a name chat.py re-exports after a move
     resolves to the moved object, a test's patch of ``chat.<name>`` is
     seen by every caller exactly as it was when the code lived in one
     file); ``globals()`` becomes ``_C.globals()`` (chat.py's own
     namespace). A name moved into the SAME module stays a direct reference
     only where it is read at definition time (a decorator, a default
     value, a module-level statement), because chat.py has not re-imported
     it yet when the module loads. Scopes are respected: a name bound in
     the function (a parameter, an assignment, a loop or with target, an
     except name, a comprehension variable, a nested def) is left alone
     where that binding is visible. Every other import the moved text used
     is copied into the module with the same statement.
  3. Writes each destination module: a docstring, the proxy import, the
     copied imports, then each block behind a ``# <<block: N>>`` line, then
     ``__all__``. A stage 5 core that loses every block is deleted (its
     tail re-import in chat.py too); one that keeps some is regenerated
     from the view with the names it still holds.
  4. In chat.py each block is replaced by one marker line
     ``# <<extracted: <module> block N>>`` (a block that lived in a core
     has its marker repointed) and the moved names are re-imported at the
     tail, after the other families, so every ``chat.<name>`` lookup, every
     ``_C.<name>`` read and ``_EXPORTS`` resolve as before.
  5. Proves the move before writing: the view computed from the new texts
     must read byte for byte as it did; every file compiles; each
     rewritten block is the original block's syntax tree with nothing but
     ``_C.`` prefixes added; every ``_C.<name>`` is a name chat.py binds;
     no module reads a free name; a definition-time read of a name that
     moved to another module in the same plan comes after that module in
     plan order; nothing left in chat.py reads a moved name at definition
     time. Then writes, reads the view back from disk and checks it once
     more; any failure reverts every file.

Dry run with --dry-run. Never force-writes.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _pm_legacy_view as _lv  # noqa: E402

LEGACY = _lv.legacy_dir()
ROOT = _lv.package_root()
CHAT = os.path.join(LEGACY, 'chat.py')
TAIL_ANCHOR = "# Screen warm-up route (2026-10-02 S7): /api/brief-chat/warm primes\n"
BUILTINS = set(dir(builtins)) | {'__name__', '__file__', '__doc__', '__builtins__'}
STAGE_NOTE = 'rebuild stage 7, 2026-11-23'


class Refuse(SystemExit):
    pass


# ---------------------------------------------------------------------------
# scopes
# ---------------------------------------------------------------------------
def _bound_names(node):
    """Names a statement binds in its own scope (not descending into nested defs/lambdas/comprehensions)."""
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            out.add(sub.name)
            continue
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
            out.add(sub.id)
        elif isinstance(sub, ast.arg):
            out.add(sub.arg)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            out.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for a in sub.names:
                out.add((a.asname or a.name).split('.')[0])
        elif isinstance(sub, (ast.Global, ast.Nonlocal)):
            out.update(sub.names)
    return out


def _assign_targets(st):
    """The module-level names an assignment statement binds (its direct targets, not a comprehension's variables)."""
    out = []
    targets = st.targets if isinstance(st, ast.Assign) else [st.target]
    for t in targets:
        for sub in ast.walk(t):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                out.append(sub.id)
    return out


def _stmt_binds(st):
    """The module-level names a top-level statement binds."""
    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [st.name]
    if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return _assign_targets(st)
    return sorted(_bound_names(st))


def _import_binds(stmt):
    out = {}
    if isinstance(stmt, ast.Import):
        for a in stmt.names:
            out[(a.asname or a.name).split('.')[0]] = stmt
    elif isinstance(stmt, ast.ImportFrom):
        for a in stmt.names:
            out[a.asname or a.name] = stmt
    return out


class _Rewriter(ast.NodeVisitor):
    """Collects (lineno, col, end_col, replacement) edits for free names.

    ``deftime`` is True while the code being visited runs when the module
    is imported (module-level statements, decorators, default values); a
    function body runs later. A name moved into this module is read
    directly at definition time and through ``_C.`` otherwise; every other
    name chat.py binds is read through ``_C.``."""

    def __init__(self, chat_globals, chat_imports, moved_here):
        self.chat_globals, self.chat_imports, self.moved_here = chat_globals, chat_imports, moved_here
        self.scopes = [set()]
        self.edits = []
        self.imports_used = set()
        self.deftime = True
        self.deftime_names = set()     # names read at definition time (moved here or not)

    def _visible(self, name):
        return any(name in s for s in self.scopes)

    def _enter(self, node, extra=()):
        s = set(extra)
        s |= _bound_names(node)
        self.scopes.append(s)

    def _args(self, args):
        params = [a.arg for a in args.args + args.kwonlyargs + getattr(args, 'posonlyargs', [])]
        if args.vararg:
            params.append(args.vararg.arg)
        if args.kwarg:
            params.append(args.kwarg.arg)
        return params

    def visit_FunctionDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(d)
        for a in node.args.args + node.args.kwonlyargs + getattr(node.args, 'posonlyargs', []):
            if a.annotation is not None:
                self.visit(a.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        self._enter(node, self._args(node.args))
        was = self.deftime
        self.deftime = False
        for st in node.body:
            self.visit(st)
        self.deftime = was
        self.scopes.pop()
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for d in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(d)
        self.scopes.append(set(self._args(node.args)))
        was = self.deftime
        self.deftime = False
        self.visit(node.body)
        self.deftime = was
        self.scopes.pop()

    def _comp(self, node, elts):
        # the first iterable is evaluated in the enclosing scope
        gens = node.generators
        self.visit(gens[0].iter)
        names = set()
        for g in gens:
            names |= _bound_names(g.target)
        self.scopes.append(names)
        for g in gens:
            for c in g.ifs:
                self.visit(c)
        for g in gens[1:]:
            self.visit(g.iter)
        for e in elts:
            self.visit(e)
        self.scopes.pop()

    def visit_ListComp(self, node): self._comp(node, [node.elt])
    def visit_SetComp(self, node): self._comp(node, [node.elt])
    def visit_GeneratorExp(self, node): self._comp(node, [node.elt])
    def visit_DictComp(self, node): self._comp(node, [node.key, node.value])

    def visit_ClassDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        for k in node.keywords:
            self.visit(k)
        self._enter(node)
        for st in node.body:
            self.visit(st)
        self.scopes.pop()

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == 'globals' and not node.args and not self._visible('globals'):
            f = node.func
            self.edits.append((f.lineno, f.col_offset, f.end_col_offset, '_C.globals'))
            for a in node.keywords:
                self.visit(a)
            return
        self.generic_visit(node)

    def visit_Name(self, node):
        if not isinstance(node.ctx, ast.Load):
            return
        n = node.id
        if self._visible(n) or n in BUILTINS or n in ('_H', '_C'):
            return
        if n in self.chat_globals:
            if self.deftime:
                self.deftime_names.add(n)
            if n in self.moved_here and self.deftime:
                return                       # read when the module loads: the module's own binding
            self.edits.append((node.lineno, node.col_offset, node.end_col_offset, '_C.' + n))
        elif n in self.chat_imports:
            self.imports_used.add(n)


def _apply_edits(text, edits):
    lines = text.split('\n')
    for ln, c0, c1, rep in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        line = lines[ln - 1]
        lines[ln - 1] = line[:c0] + rep + line[c1:]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# the view as statements
# ---------------------------------------------------------------------------
_FAMILY_IMPORT_RX = re.compile(r'^prometheus\.legacy(?:\.|$)')


class _View:
    def __init__(self):
        self.text = _lv.legacy_view()
        self.lines = self.text.split('\n')
        self.tree = ast.parse(self.text)
        self.origins = _lv.legacy_view_origins()
        if len(self.origins) != len(self.lines):
            raise Refuse(f'origins ({len(self.origins)}) and view lines ({len(self.lines)}) disagree')
        self.by_name = {}          # name -> [stmt, ...] in file order
        self.chat_globals = set()  # names the view binds at module level (read through _C. after a move)
        self.chat_imports = {}     # name -> import statement to copy
        for st in self.tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.by_name.setdefault(st.name, []).append(st)
                self.chat_globals.add(st.name)
            elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for n in _assign_targets(st):
                    self.by_name.setdefault(n, []).append(st)
                    self.chat_globals.add(n)
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                binds = _import_binds(st)
                mod = getattr(st, 'module', '') or ''
                if isinstance(st, ast.ImportFrom) and _FAMILY_IMPORT_RX.match(mod):
                    # a legacy family (threads, jobs, watch, ...) or the proxies: read through _C. after a move
                    for n in binds:
                        if n not in ('_H', '_C', '_bind_core'):
                            self.chat_globals.add(n)
                else:
                    self.chat_imports.update(binds)
            elif isinstance(st, (ast.Try, ast.If, ast.With)):
                for sub in ast.walk(st):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        for n in _import_binds(sub):
                            self.chat_globals.add(n)
                    elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        self.chat_globals.add(sub.id)
        # a name bound by a top-level statement is a chat global even if it was also imported
        for n in list(self.chat_imports):
            if n in self.chat_globals:
                del self.chat_imports[n]

    def span(self, st):
        """(start, end) 0-based inclusive view lines: the statement plus the unindented comment lines directly above it."""
        start = min([st.lineno] + [d.lineno for d in getattr(st, 'decorator_list', [])]) - 1
        while start > 0 and self.lines[start - 1].startswith('#'):
            start -= 1
        return start, st.end_lineno - 1


def _block_key(origin):
    return (origin[1], origin[2]) if origin[0] == 'block' else None


# ---------------------------------------------------------------------------
# the move
# ---------------------------------------------------------------------------
def _module_file(module):
    return _lv.module_path(module)


def _import_path(module):
    return module if '.' in module else f'prometheus.legacy.{module}'


def _reimport_block(module, names, doc):
    return (f'# <<reimport: {module}>>\n'
            f'from {_import_path(module)} import (  # noqa: E402,F401  ({STAGE_NOTE}: {doc.rstrip(".")})\n'
            + ''.join(f'    {n},\n' for n in names) + ')\n# <</reimport>>\n')


def _module_text(module, doc, blocks, import_lines, names):
    head = (f'"""{doc}\n\n'
            f'Moved out of prometheus/legacy/chat.py ({STAGE_NOTE}) by\n'
            'scripts/extract_legacy_core.py, behaviour unchanged. This module reads the\n'
            'host through ``_H`` and the legacy chat namespace through ``_C``\n'
            '(prometheus.legacy.C), both bound before it is imported; chat.py imports it\n'
            'last and re-exports the moved names, so every name chat.py binds is read\n'
            'through ``_C.<name>`` at call time exactly as it was when the code lived in\n'
            'one file. ``_C.globals()`` is chat.py\'s own namespace, the one the lanes this\n'
            'code hands it to expect. The suites read the moved text through\n'
            'scripts/_pm_legacy_view.legacy_view(), which splices each block back at its\n'
            'marker in chat.py.\n"""\n')
    body = head + 'from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401\n'
    if import_lines:
        body += '\n'.join(import_lines) + '\n'
    body += '\n# <<moved>>\n'
    for i, blk in enumerate(blocks, 1):
        body += f'# <<block: {i}>>\n{blk}\n\n\n'
    body = body.rstrip('\n') + '\n\n\n__all__ = [' + ', '.join(repr(n) for n in names) + ']\n'
    return body


def _normalize(tree):
    """A rewritten block's tree with the ``_C.`` prefixes taken off again."""
    class _Strip(ast.NodeTransformer):
        def visit_Attribute(self, node):
            self.generic_visit(node)
            if isinstance(node.value, ast.Name) and node.value.id == '_C' and isinstance(node.ctx, ast.Load):
                return ast.copy_location(ast.Name(id=node.attr, ctx=ast.Load()), node)
            return node
    return _Strip().visit(tree)


def _free_names(module_src):
    """Names a module reads that nothing binds: module-level bindings, imports, builtins, _H and _C excluded."""
    tree = ast.parse(module_src)
    bound = set()
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(st.name)
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            bound |= set(_import_binds(st))
        else:
            bound |= _bound_names(st)

    class _Free(_Rewriter):
        def __init__(self):
            super().__init__(set(), {}, set())
            self.free = set()

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and not self._visible(node.id) and node.id not in bound \
                    and node.id not in BUILTINS and node.id not in ('_H', '_C'):
                self.free.add(node.id)
    f = _Free()
    f.visit(tree)
    return f.free


def _deftime_reads(chat_src, names):
    """Names from ``names`` that the chat.py text reads at definition time (a module-level statement, a decorator, a default)."""
    tree = ast.parse(chat_src)
    hits = set()
    rw = _Rewriter(set(names), {}, set())
    for st in tree.body:
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            continue
        rw.deftime = True
        rw.deftime_names = set()
        rw.visit(st)
        hits |= rw.deftime_names
    return hits


def plan_moves(plan, dry_run=False):
    """Apply a plan: a list of {module, doc, names}. All or nothing."""
    view = _View()
    chat_src = open(CHAT, encoding='utf-8').read()
    chat_lines = chat_src.split('\n')
    if chat_src.count(TAIL_ANCHOR) != 1:
        raise Refuse('tail anchor not unique in chat.py')
    # already extracted into an organized module: refuse to move again
    existing_markers = {(m.group(2), int(m.group(3))) for m in _lv._MARK_RX.finditer(chat_src)}
    moved_all = {}                     # name -> module (this plan)
    for entry in plan:
        for n in entry['names']:
            if n in moved_all:
                raise Refuse(f'{n} named twice in the plan')
            moved_all[n] = entry['module']
    modules_out = {}                   # module -> text
    spans_by_entry = []
    consumed_blocks = {}               # (legacy module, n) -> names
    chat_regions = []                  # (start, end, marker_line) in chat.py, 0-based inclusive
    all_spans = []
    reimports = []
    for entry in plan:
        module, doc, names = entry['module'], entry['doc'], list(entry['names'])
        if '.' not in module:
            raise Refuse(f'{module}: a destination is a dotted import path (prometheus.<family>)')
        path = _module_file(module)
        if os.path.exists(path) or any(k[0] == module for k in existing_markers):
            raise Refuse(f'{path} exists (a module is written once; extend the plan instead)')
        missing = [n for n in names if n not in view.by_name]
        if missing:
            raise Refuse(f'not top-level bindings of the legacy chat text: {missing}')
        stmts = []
        for n in names:
            for st in view.by_name[n]:
                if st not in stmts:
                    stmts.append(st)
        # every name a moved statement binds must be in the plan (a tuple assign, a shared statement)
        for st in stmts:
            for b in _stmt_binds(st):
                if b in view.chat_globals and b not in names:
                    raise Refuse(f'{module}: statement at view line {st.lineno} also binds {b!r}; add it to the plan')
        stmts.sort(key=lambda s: s.lineno)
        spans = [view.span(st) for st in stmts]
        for (a, b), (c, d) in zip(spans, spans[1:]):
            if c <= b:
                raise Refuse(f'{module}: overlapping blocks at view lines {a + 1}-{b + 1} and {c + 1}-{d + 1}')
        all_spans.extend(spans)
        # rewrite each block from the view text
        moved_here = set(names)
        out_blocks, imports_used, deftime_other = [], set(), {}
        for (a, b), st in zip(spans, stmts):
            blk = '\n'.join(view.lines[a:b + 1])
            btree = ast.parse(blk)
            rw = _Rewriter(view.chat_globals, view.chat_imports, moved_here)
            rw.visit(btree)
            new_blk = _apply_edits(blk, rw.edits)
            # proof: the rewritten block is the original with nothing but _C. prefixes added
            if ast.dump(_normalize(ast.parse(new_blk))) != ast.dump(btree):
                raise Refuse(f'{module}: the rewrite of the block at view line {a + 1} changed more than the proxy prefix')
            for attr in set(re.findall(r'(?<![\w.])_C\.(\w+)', new_blk)):
                if attr != 'globals' and attr not in view.chat_globals:
                    raise Refuse(f'{module}: _C.{attr} is not a name the legacy chat text binds')
            out_blocks.append(new_blk)
            imports_used |= rw.imports_used
            for n in rw.deftime_names:
                if n in moved_all and moved_all[n] != module:
                    deftime_other[n] = moved_all[n]
        # a definition-time read of a name another module of this plan holds: that module must come first
        order = [e['module'] for e in plan]
        for n, other in deftime_other.items():
            if order.index(other) > order.index(module):
                raise Refuse(f'{module} reads {n} at definition time; {other} must come before it in the plan')
        # the import statements to copy, in view order, deduplicated
        stmts_imp = []
        for n in imports_used:
            st = view.chat_imports[n]
            if st not in stmts_imp:
                stmts_imp.append(st)
        stmts_imp.sort(key=lambda s: s.lineno)
        import_lines = []
        for st in stmts_imp:
            seg = '\n'.join(view.lines[st.lineno - 1:st.end_lineno])
            seg = re.sub(r'\s+#.*$', '', seg, flags=re.M) if '\n' not in seg else seg
            import_lines.append(seg)
        text = _module_text(module, doc, out_blocks, import_lines, names)
        compile(text, path, 'exec')
        free = _free_names(text)
        if free:
            raise Refuse(f'{module}: free names after the rewrite: {sorted(free)}')
        modules_out[module] = text
        reimports.append(_reimport_block(module, names, doc))
        # where each block lives now
        for k, ((a, b), st) in enumerate(zip(spans, stmts), 1):
            origins = view.origins[a:b + 1]
            keys = {_block_key(o) for o in origins} - {None}
            if len(keys) > 1:
                raise Refuse(f'{module}: the block at view line {a + 1} spans two extracted blocks')
            chat_idx = [o[1] for o in origins if o[0] == 'chat']
            if keys:
                (src_mod, n) = keys.pop()
                blk_lines = sum(1 for o in origins if o[0] == 'block')
                if blk_lines != len(_lv._core_blocks(_module_file(src_mod))[n].split('\n')):
                    raise Refuse(f'{module}: the block at view line {a + 1} takes part of {src_mod} block {n}')
                marker_idx = [o[4] for o in origins if o[0] == 'block'][0]
                chat_idx.append(marker_idx)
                consumed_blocks.setdefault(src_mod, set()).add(n)
            lo, hi = min(chat_idx), max(chat_idx)
            if hi - lo + 1 != len(chat_idx):
                raise Refuse(f'{module}: the block at view line {a + 1} is not one region of chat.py ({lo}-{hi})')
            chat_regions.append((lo, hi, f'# <<extracted: {module} block {k}>>'))
    # chat.py: markers in place of the regions, from the bottom up
    for (a, b), (c, d) in zip(sorted(all_spans), sorted(all_spans)[1:]):
        if c <= b:
            raise Refuse(f'overlapping blocks across the plan at view lines {a + 1}-{b + 1} and {c + 1}-{d + 1}')
    new_lines = list(chat_lines)
    for lo, hi, marker in sorted(chat_regions, reverse=True):
        new_lines[lo:hi + 1] = [marker]
    new_src = '\n'.join(new_lines)
    # the stage 5 cores that lost blocks
    legacy_out = {}                    # legacy module -> new text or None (deleted)
    for src_mod, gone in consumed_blocks.items():
        if '.' in src_mod:
            raise Refuse(f'{src_mod}: moving out of an organized module is not supported; move from the view instead')
        held = _lv._core_blocks(_module_file(src_mod))
        remaining = sorted(set(held) - gone)
        rx = re.compile(r'^# <<reimport: ' + re.escape(src_mod) + r'>>\n.*?^# <</reimport>>\n', re.M | re.S)
        if not rx.search(new_src):
            raise Refuse(f'{src_mod}: no tail re-import block in chat.py')
        if not remaining:
            legacy_out[src_mod] = None
            new_src = rx.sub('', new_src)
        else:
            # regenerate the core from the view with the names it still holds
            core_src = open(_module_file(src_mod), encoding='utf-8').read()
            doc = core_src.split('\n', 1)[0].strip('"').strip()
            names_left = []
            for n in remaining:
                b = held[n]
                for st in ast.parse(b).body:
                    names_left.extend(_stmt_binds(st))
            sub_plan = [{'module': src_mod, 'doc': doc, 'names': names_left}]
            raise Refuse(f'{src_mod}: a partial move of a stage 5 core is not supported; move its other blocks too '
                         f'({names_left}) or run {json.dumps(sub_plan)} first')
    # the re-imports at the tail, in plan order
    new_src = new_src.replace(TAIL_ANCHOR, ''.join(reimports) + TAIL_ANCHOR)
    compile(new_src, CHAT, 'exec')
    # nothing left in chat.py reads a moved name at definition time
    left = _deftime_reads(new_src, set(moved_all))
    if left:
        raise Refuse(f'chat.py still reads {sorted(left)} at definition time; move the statement that reads them too')
    # the view proof, in memory
    texts = dict(modules_out)
    texts.update(legacy_out)
    before_view = view.text
    after_view = _lv.legacy_view_from(new_src, texts)
    if after_view != before_view:
        i = next((k for k in range(min(len(after_view), len(before_view))) if after_view[k] != before_view[k]),
                 min(len(after_view), len(before_view)))
        raise Refuse(f'view differs from the original at char {i}: {after_view[i-80:i+80]!r} vs {before_view[i-80:i+80]!r}')
    n_moved = sum(len(e['names']) for e in plan)
    if dry_run:
        for entry in plan:
            txt = modules_out[entry['module']]
            print(f"[dry-run] {entry['module']}: {len(entry['names'])} name(s), {len(txt.splitlines())} lines, "
                  f"{txt.count('_C.')} _C. reads")
        for src_mod, t in legacy_out.items():
            print(f'[dry-run] {src_mod}: ' + ('deleted' if t is None else 'regenerated'))
        print(f'[dry-run] chat.py {len(chat_lines)} -> {len(new_lines)} lines; view byte-identical; {n_moved} names')
        return
    # write, then read the view back from disk
    written = []
    try:
        for module, text in modules_out.items():
            p = _module_file(module)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w', encoding='utf-8').write(text)
            written.append(p)
        deleted = {}
        for src_mod, t in legacy_out.items():
            p = _module_file(src_mod)
            deleted[p] = open(p, encoding='utf-8').read()
            if t is None:
                os.remove(p)
            else:
                open(p, 'w', encoding='utf-8').write(t)
        open(CHAT, 'w', encoding='utf-8').write(new_src)
        if _lv.legacy_view() != before_view:
            raise Refuse('the view read back from disk differs; reverted')
    except BaseException:
        open(CHAT, 'w', encoding='utf-8').write(chat_src)
        for p in written:
            if os.path.exists(p):
                os.remove(p)
        for p, t in deleted.items():
            open(p, 'w', encoding='utf-8').write(t)
        raise
    print(f'moved {n_moved} name(s) into {len(modules_out)} module(s); chat.py {len(chat_lines)} -> {len(new_lines)} lines; '
          f'legacy lines {_lv.legacy_lines()}; view byte-identical')


def extract(module, names, doc, dry_run=False):
    """One module (the stage 5 entry point, kept for the suite that pins it)."""
    plan_moves([{'module': module, 'doc': doc, 'names': list(names)}], dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plan', help='JSON file: a list of {module, doc, names}')
    ap.add_argument('--module')
    ap.add_argument('--names', help='comma-separated top-level names, in file order')
    ap.add_argument('--doc')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.plan:
        with open(a.plan, encoding='utf-8') as fh:
            plan = json.load(fh)
        plan_moves(plan, dry_run=a.dry_run)
    else:
        if not (a.module and a.names and a.doc):
            ap.error('--plan, or --module with --names and --doc')
        extract(a.module, [n.strip() for n in a.names.split(',') if n.strip()], a.doc, dry_run=a.dry_run)


if __name__ == '__main__':
    main()
