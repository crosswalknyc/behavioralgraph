#!/usr/bin/env python3
"""Move top-level functions out of prometheus/legacy/chat.py into a core
module, behaviour unchanged (rebuild stage 5, 2026-11-20).

    python3 scripts/extract_legacy_core.py --module core_analyze \
        --names _pm_analyze_core --doc "Page analysis over the open profile."

What it does, in order:

  1. Reads chat.py and parses it. The named top-level functions (with the
     comment lines that sit directly above each) are the moved blocks.
  2. In the moved text every free name that is a chat.py module-level
     binding (a def, a class, an assignment) and not itself moved becomes
     ``_C.<name>`` (the legacy chat proxy, resolved at call time), every
     ``globals()`` becomes ``_C.globals()`` (the chat module's namespace,
     the one the lanes expect), and every name bound by a chat.py import
     is imported into the core module with the same statement. Scopes are
     respected: a name bound in the function (a parameter, an assignment,
     a loop or with target, an except name, a comprehension variable, a
     nested def) is left alone where that binding is visible.
  3. Writes prometheus/legacy/<module>.py: a docstring, the proxy import,
     the copied imports, then each block behind a ``# <<block: N>>`` line,
     then ``__all__``.
  4. In chat.py each block is replaced by one marker line
     ``# <<extracted: <module> block N>>`` (a byte-level splice, each
     anchor unique) and the moved names are re-imported at the tail, after
     the other families, so every ``chat.<name>`` lookup and every call site
     resolves as before.
  5. Proves the move: ``scripts/_pm_legacy_view.legacy_view()`` (chat.py
     with every extracted block spliced back, ``_C.`` stripped) must read
     byte for byte as it did before this move, and both files must compile.

Dry run with --dry-run. Never force-writes: any failed check leaves both
files as they were.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
LEGACY = os.path.join(WEB, 'prometheus', 'legacy')
CHAT = os.path.join(LEGACY, 'chat.py')
TAIL_ANCHOR = "# Screen warm-up route (2026-10-02 S7): /api/brief-chat/warm primes\n"
BUILTINS = set(dir(builtins)) | {'__name__', '__file__', '__doc__'}


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
    """Collects (lineno, col, end_col, replacement) edits for free names."""

    def __init__(self, chat_globals, chat_imports, moved):
        self.chat_globals, self.chat_imports, self.moved = chat_globals, chat_imports, moved
        self.scopes = [set()]
        self.edits = []
        self.imports_used = set()

    def _visible(self, name):
        return any(name in s for s in self.scopes)

    def _enter(self, node, extra=()):
        s = set(extra)
        s |= _bound_names(node)
        self.scopes.append(s)

    def visit_FunctionDef(self, node):
        args = node.args
        params = [a.arg for a in args.args + args.kwonlyargs + getattr(args, 'posonlyargs', [])]
        if args.vararg: params.append(args.vararg.arg)
        if args.kwarg: params.append(args.kwarg.arg)
        for d in node.decorator_list: self.visit(d)
        for d in args.defaults + [x for x in args.kw_defaults if x is not None]: self.visit(d)
        self._enter(node, params)
        for st in node.body: self.visit(st)
        self.scopes.pop()
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        args = node.args
        params = [a.arg for a in args.args + args.kwonlyargs + getattr(args, 'posonlyargs', [])]
        if args.vararg: params.append(args.vararg.arg)
        if args.kwarg: params.append(args.kwarg.arg)
        for d in args.defaults + [x for x in args.kw_defaults if x is not None]: self.visit(d)
        self.scopes.append(set(params))
        self.visit(node.body)
        self.scopes.pop()

    def _comp(self, node, elts):
        # the first iterable is evaluated in the enclosing scope
        gens = node.generators
        self.visit(gens[0].iter)
        names = set()
        for g in gens: names |= _bound_names(g.target)
        self.scopes.append(names)
        for g in gens:
            for c in g.ifs: self.visit(c)
        for g in gens[1:]: self.visit(g.iter)
        for e in elts: self.visit(e)
        self.scopes.pop()

    def visit_ListComp(self, node): self._comp(node, [node.elt])
    def visit_SetComp(self, node): self._comp(node, [node.elt])
    def visit_GeneratorExp(self, node): self._comp(node, [node.elt])
    def visit_DictComp(self, node): self._comp(node, [node.key, node.value])

    def visit_ClassDef(self, node):
        for d in node.decorator_list: self.visit(d)
        for b in node.bases: self.visit(b)
        self._enter(node)
        for st in node.body: self.visit(st)
        self.scopes.pop()

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == 'globals' and not node.args and not self._visible('globals'):
            f = node.func
            self.edits.append((f.lineno, f.col_offset, f.end_col_offset, '_C.globals'))
            return
        self.generic_visit(node)

    def visit_Name(self, node):
        if not isinstance(node.ctx, ast.Load):
            return
        n = node.id
        if self._visible(n) or n in self.moved or n in BUILTINS or n in ('_H', '_C'):
            return
        if n in self.chat_globals:
            self.edits.append((node.lineno, node.col_offset, node.end_col_offset, '_C.' + n))
        elif n in self.chat_imports:
            self.imports_used.add(n)


def _apply_edits(text, edits):
    lines = text.split('\n')
    for ln, c0, c1, rep in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        line = lines[ln - 1]
        lines[ln - 1] = line[:c0] + rep + line[c1:]
    return '\n'.join(lines)


def _block_span(lines, node):
    """(start, end) 0-based inclusive line indexes: the def plus the comment lines directly above it."""
    start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
    while start > 0 and lines[start - 1].lstrip().startswith('#'):
        start -= 1
    return start, node.end_lineno - 1


def extract(module, names, doc, dry_run=False):
    src = open(CHAT, encoding='utf-8').read()
    lines = src.split('\n')
    tree = ast.parse(src)
    top = {}
    chat_globals, chat_imports = set(), {}
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top[st.name] = st; chat_globals.add(st.name)
        elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for sub in ast.walk(st):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    chat_globals.add(sub.id)
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            chat_imports.update(_import_binds(st))
        elif isinstance(st, (ast.Try, ast.If, ast.With)):
            for sub in ast.walk(st):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    chat_imports.update(_import_binds(sub))
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    chat_globals.add(sub.id)
    missing = [n for n in names if n not in top]
    if missing:
        raise SystemExit(f'not top-level defs in chat.py: {missing}')
    moved = set(names)
    chat_globals -= moved
    # names re-imported from already extracted families are chat globals too (they are bound in chat's namespace)
    spans = sorted(_block_span(lines, top[n]) for n in names)
    for (a, b), (c, d) in zip(spans, spans[1:]):
        if c <= b:
            raise SystemExit('overlapping blocks')
    blocks = ['\n'.join(lines[a:b + 1]) for a, b in spans]
    # rewrite each block
    out_blocks, imports_used = [], set()
    for blk in blocks:
        btree = ast.parse(blk)
        rw = _Rewriter(chat_globals, chat_imports, moved)
        rw.visit(btree)
        out_blocks.append(_apply_edits(blk, rw.edits))
        imports_used |= rw.imports_used
    # the import statements to copy, in chat.py order, deduplicated
    stmts = []
    for n in imports_used:
        st = chat_imports[n]
        if st not in stmts:
            stmts.append(st)
    stmts.sort(key=lambda s: s.lineno)
    import_lines = []
    for st in stmts:
        seg = '\n'.join(lines[st.lineno - 1:st.end_lineno])
        seg = re.sub(r'\s+#.*$', '', seg, flags=re.M) if '\n' not in seg else seg
        import_lines.append(seg)
    # the core module
    head = (f'"""{doc}\n\n'
            'Extracted from prometheus/legacy/chat.py (rebuild stage 5, 2026-11-20) by\n'
            'scripts/extract_legacy_core.py, behaviour unchanged. This module reads the\n'
            'host through ``_H`` and the rest of the legacy chat module through ``_C``\n'
            '(prometheus.legacy.C), both bound before it is imported; chat.py imports it\n'
            'last and re-exports the moved names. ``_C.globals()`` is chat.py\'s own\n'
            'namespace, the one the lanes this code hands it to expect. The suites read\n'
            'the moved text through scripts/_pm_legacy_view.legacy_view(), which splices\n'
            'each block back at its marker in chat.py.\n"""\n')
    body = head + 'from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401\n'
    if import_lines:
        body += '\n'.join(import_lines) + '\n'
    body += '\n# <<moved>>\n'
    for i, blk in enumerate(out_blocks, 1):
        body += f'# <<block: {i}>>\n{blk}\n\n\n'
    body = body.rstrip('\n') + '\n\n\n__all__ = [' + ', '.join(repr(n) for n in names) + ']\n'
    # chat.py: markers in place of the blocks, re-import at the tail
    new_lines = list(lines)
    for i, (a, b) in reversed(list(enumerate(spans, 1))):
        new_lines[a:b + 1] = [f'# <<extracted: {module} block {i}>>']
    new_src = '\n'.join(new_lines)
    if new_src.count(TAIL_ANCHOR) != 1:
        raise SystemExit('tail anchor not unique in chat.py')
    reimport = (f'# <<reimport: {module}>>\n'
                f'from prometheus.legacy.{module} import (  # noqa: E402,F401  (stage 5: {doc.rstrip(".")})\n'
                + ''.join(f'    {n},\n' for n in names) + ')\n# <</reimport>>\n')
    new_src = new_src.replace(TAIL_ANCHOR, reimport + TAIL_ANCHOR)
    # prove it
    compile(new_src, CHAT, 'exec')
    compile(body, os.path.join(LEGACY, module + '.py'), 'exec')
    sys.path.insert(0, HERE)
    import _pm_legacy_view as _lv
    saved = {}
    core_path = os.path.join(LEGACY, module + '.py')
    if os.path.exists(core_path):
        raise SystemExit(f'{core_path} exists')
    if dry_run:
        print(f'[dry-run] {module}: {len(blocks)} block(s), {sum(b.count(chr(10)) + 1 for b in blocks)} lines, '
              f'{sum(len(ast.parse(b).body) for b in blocks)} defs, rewrites: '
              f'{sum(len(_Rewriter(chat_globals, chat_imports, moved).edits) for _ in [0])}; imports copied: {len(import_lines)}')
        for i, (blk, ob) in enumerate(zip(blocks, out_blocks), 1):
            n_c = ob.count('_C.')
            print(f'   block {i}: {blk.count(chr(10)) + 1} lines, {n_c} _C. references')
        print('   imports:', ', '.join(sorted(imports_used)))
        return
    before_view = _lv.legacy_view()       # chat.py with every earlier core spliced back: what the suites read
    open(core_path, 'w', encoding='utf-8').write(body)
    open(CHAT, 'w', encoding='utf-8').write(new_src)
    view = _lv.legacy_view()
    if view != before_view:
        # revert
        open(CHAT, 'w', encoding='utf-8').write(src)
        os.remove(core_path)
        # find the first difference for the report
        i = next((k for k in range(min(len(view), len(before_view))) if view[k] != before_view[k]), min(len(view), len(before_view)))
        raise SystemExit(f'view differs from the original at char {i}: {view[i-80:i+80]!r} vs {before_view[i-80:i+80]!r}; reverted')
    print(f'{module}: moved {len(blocks)} block(s), chat.py {src.count(chr(10)) + 1} -> {new_src.count(chr(10)) + 1} lines; view byte-identical')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module', required=True)
    ap.add_argument('--names', required=True, help='comma-separated top-level function names, in file order')
    ap.add_argument('--doc', required=True)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    extract(a.module, [n.strip() for n in a.names.split(',') if n.strip()], a.doc, dry_run=a.dry_run)


if __name__ == '__main__':
    main()
