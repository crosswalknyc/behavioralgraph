#!/usr/bin/env python3
"""Find every place a value from a request reaches a storage call.

Static scan (ast only, nothing is imported or run). For each function it
works out which of its inputs can end up as the key, prefix or bucket of
a storage call, directly or through another function it calls, then
lists the route handlers where a value taken from the request does so.

Two shapes are told apart, because they are different risks:

  WHOLE   the request value IS the key (or its leading part, or the
          bucket). Whoever sends the request picks the object. These
          are the doors that must pass the shared gate (`file_access`).
  PART    the request value is a piece of a key that starts with a
          fixed location ('decks/' + id). Storage keys are literal, so
          the value cannot leave that location; the question left is
          only whose object under it.

Used two ways:
  * `python3 scripts/key_door_scan.py [-v] [--part]` prints the doors.
  * `scripts/test_file_access_by_key.py` imports `scan()` / `doors()`
    and fails when a WHOLE door reaches storage without passing the
    shared gate, unless the door is on the reviewed list there.

Sources in a handler: its own parameters (path parameters) and anything
read off `request`. Flow-insensitive: a name assigned from an expression
that carries a request value carries it too.

A door is gated when its body (or a helper it calls on the way to
storage) calls one of the shared gate functions.
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

SKIP_DIRS = {'scripts', 'archive', 'node_modules', '.git', 'tests', 'test',
             '__pycache__', 'venv', '.venv', 'desktop_ui', 'static', 'templates',
             'deploy', 'docs', 'design'}

READ_SINKS = {'get_object', 'head_object', 'generate_presigned_url',
              'download_file', 'download_fileobj', 'list_objects_v2',
              'list_objects'}
WRITE_SINKS = {'put_object', 'upload_file', 'upload_fileobj', 'copy_object',
               'delete_object', 'delete_objects'}
SINKS = READ_SINKS | WRITE_SINKS

# The shared gate. A call to one of these on the module (any alias
# below) marks the function as gated, and its return value is clean.
GATE_FUNCS = {
    'servable', 'clean_key', 'safe_id', 'safe_name', 'profile_key',
    'under_prefix', 'image_key', 'link_targets', 'scrub_page_context',
    # internal_copies: the Subscriber IQ rule the shared gate is built on
    'subscriber_iq_key_servable',
    # prometheus.key_scope: the same gate for keys a chat message carries
    'page_context', 'allowed_for', 'allowed_here',
}
GATE_ALIASES = {'file_access', '_file_access', '_fa', 'internal_copies',
                '_internal_copies', '_ic', 'key_scope', '_ks', '_ks0', '_key_scope'}
# Helpers in the application that call the shared gate before anything
# else. A handler that goes through one of them is gated.
GATE_WRAPPERS = {
    '_door_key', '_require_profile_run_access', '_user_can_open_subscriber',
    '_twin_or_404', '_can_read_profile', '_pm_can_open_profile',
    '_pm_can_open_journey', '_image_key_ok', '_link_targets',
}
# Host names the Prometheus package uses for app.py callables.
HOST_ALIASES = {'_H', 'host', '_host', 'h'}

REQ = '<request>'
_REQUEST_ATTRS = {'args', 'form', 'json', 'values', 'files', 'data', 'headers',
                  'get_json', 'view_args', 'cookies', 'path', 'full_path',
                  'url', 'query_string', 'get_data', 'stream'}


def py_files(root=ROOT):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in fns:
            if not fn.endswith('.py'):
                continue
            if ' 2.py' in fn or ' 3.py' in fn or fn.startswith('test_'):
                continue
            out.append(os.path.join(dp, fn))
    return sorted(out)


def _is_gate_call(node):
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in GATE_WRAPPERS
    return (isinstance(f, ast.Attribute) and f.attr in GATE_FUNCS
            and isinstance(f.value, ast.Name) and f.value.id in GATE_ALIASES)


def _is_request(node):
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == 'request' and node.attr in _REQUEST_ATTRS)


def _const_lead(node):
    """True when the expression is a string that starts with fixed text."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) \
        and bool(node.value.strip('/ '))


def _any(expr, taint):
    """Labels mentioned anywhere in the expression."""
    labs = set()
    stack = [expr]
    while stack:
        n = stack.pop()
        if _is_gate_call(n):
            continue
        if _is_request(n):
            labs.add(REQ)
            continue
        if isinstance(n, ast.Name):
            labs |= taint.get(n.id, _EMPTY)[1]
            continue
        stack.extend(ast.iter_child_nodes(n))
    return labs


def _whole(expr, taint):
    """Labels that reach the result as the whole value or its leading
    part (so the sender picks where the key starts)."""
    n = expr
    if n is None or _is_gate_call(n):
        return set()
    if _is_request(n):
        return {REQ}
    if isinstance(n, ast.Name):
        return set(taint.get(n.id, _EMPTY)[0])
    if isinstance(n, ast.Constant):
        return set()
    if isinstance(n, ast.JoinedStr):
        if not n.values:
            return set()
        first = n.values[0]
        if _const_lead(first):
            return set()
        if isinstance(first, ast.FormattedValue):
            return _whole(first.value, taint)
        if isinstance(first, ast.Constant) and len(n.values) > 1 \
                and isinstance(n.values[1], ast.FormattedValue):
            return _whole(n.values[1].value, taint)
        return set()
    if isinstance(n, ast.BinOp):
        if isinstance(n.op, ast.Add):
            if _const_lead(n.left):
                return set()
            left = _whole(n.left, taint)
            if left or not isinstance(n.left, ast.Constant):
                # an untainted, non-constant left part (a configured
                # prefix) is treated as fixed
                return left
            return _whole(n.right, taint)
        if isinstance(n.op, ast.Mod):
            if isinstance(n.left, ast.Constant) and isinstance(n.left.value, str):
                if n.left.value.lstrip('/').startswith('%'):
                    r = n.right
                    if isinstance(r, ast.Tuple) and r.elts:
                        return _whole(r.elts[0], taint)
                    return _whole(r, taint)
                return set()
            return _whole(n.left, taint)
        return set()
    if isinstance(n, ast.Call):
        f = n.func
        if _is_request(f):
            return {REQ}
        if isinstance(f, ast.Attribute):
            if f.attr == 'format' and isinstance(f.value, ast.Constant) \
                    and isinstance(f.value.value, str):
                if f.value.value.lstrip('/').startswith('{'):
                    return _whole(n.args[0], taint) if n.args else set()
                return set()
            if f.attr == 'join' and n.args:
                # os.path.join(a, b) / posixpath.join / '/'.join([a, b])
                if isinstance(f.value, ast.Constant):
                    a0 = n.args[0]
                    if isinstance(a0, (ast.List, ast.Tuple)) and a0.elts:
                        if _const_lead(a0.elts[0]):
                            return set()
                        return _whole(a0.elts[0], taint)
                    return _whole(a0, taint)
                if _const_lead(n.args[0]):
                    return set()
                return _whole(n.args[0], taint)
            if f.attr in ('get', 'pop', 'getlist', 'get_json', 'strip', 'lstrip',
                          'rstrip', 'replace', 'lower', 'upper', 'decode',
                          'encode', 'split', 'rsplit', 'removeprefix',
                          'removesuffix', 'title', 'copy', 'items', 'values',
                          'keys', 'to_dict', 'read'):
                return _whole(f.value, taint)
            if f.attr in ('unquote', 'unquote_plus', 'normpath', 'basename',
                          'secure_filename', 'loads'):
                out = set()
                for a in n.args:
                    out |= _whole(a, taint)
                return out
            return set()
        if isinstance(f, ast.Name):
            if f.id in ('str', 'unquote', 'unquote_plus', 'list', 'sorted',
                        'set', 'tuple', 'dict', 'reversed', 'enumerate',
                        'secure_filename'):
                out = set()
                for a in n.args:
                    out |= _whole(a, taint)
                return out
        return set()
    if isinstance(n, ast.BoolOp):
        out = set()
        for v in n.values:
            out |= _whole(v, taint)
        return out
    if isinstance(n, ast.IfExp):
        return _whole(n.body, taint) | _whole(n.orelse, taint)
    if isinstance(n, ast.Subscript):
        return _whole(n.value, taint)
    if isinstance(n, ast.Starred):
        return _whole(n.value, taint)
    if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
        out = set()
        for e in n.elts:
            out |= _whole(e, taint)
        return out
    if isinstance(n, ast.Dict):
        out = set()
        for v in n.values:
            if v is not None:
                out |= _whole(v, taint)
        return out
    if isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _whole(n.elt, taint)
    if isinstance(n, ast.Attribute):
        return _whole(n.value, taint)
    if isinstance(n, ast.NamedExpr):
        return _whole(n.value, taint)
    return set()


_EMPTY = (frozenset(), frozenset())


def _targets(node):
    """The names an assignment stores into. ``d[k] = v`` and ``o.a = v``
    store into ``d`` / ``o``, never into the names inside the index."""
    out = set()
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            out |= _targets(e)
    elif isinstance(node, ast.Starred):
        out |= _targets(node.value)
    elif isinstance(node, (ast.Subscript, ast.Attribute)):
        base = node.value
        while isinstance(base, (ast.Subscript, ast.Attribute)):
            base = base.value
        if isinstance(base, ast.Name):
            out.add(base.id)
    return out


class Func:
    def __init__(self, name, qual, file, node, top):
        self.name = name
        self.qual = qual
        self.file = file
        self.line = node.lineno
        self.end = getattr(node, 'end_lineno', node.lineno)
        self.node = node
        self.top = top
        a = node.args
        self.params = [x.arg for x in (a.posonlyargs + a.args + a.kwonlyargs)]
        self.routes = []
        self.decorators = []
        for d in node.decorator_list:
            self.decorators.append(ast.unparse(d))
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) \
                    and d.func.attr in ('route', 'get', 'post', 'put', 'delete') \
                    and d.args and isinstance(d.args[0], (ast.Constant, ast.JoinedStr)):
                methods = ''
                for kw in d.keywords:
                    if kw.arg == 'methods':
                        methods = ast.unparse(kw.value)
                a0 = d.args[0]
                path = str(a0.value) if isinstance(a0, ast.Constant) else ast.unparse(a0)[2:-1]
                self.routes.append((path, methods or d.func.attr))
        self.is_route = bool(self.routes)
        self.taint = {}
        # param -> {(sink, role, shape)}; shape 'whole' | 'part'
        self.sink_params = {}
        self.hits = []
        self.gated = False
        self.own = None


def _own_nodes(fn):
    if fn.own is None:
        out = []
        stack = list(fn.node.body)
        while stack:
            n = stack.pop()
            out.append(n)
            for c in ast.iter_child_nodes(n):
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                stack.append(c)
        fn.own = out
    return fn.own


def _key_exprs(call):
    name = call.func.attr
    out = []
    kws = {k.arg: k.value for k in call.keywords if k.arg}
    for role in ('Key', 'Prefix', 'Bucket'):
        if role in kws:
            out.append((role, kws[role]))
    if 'CopySource' in kws:
        cs = kws['CopySource']
        if isinstance(cs, ast.Dict):
            for k, v in zip(cs.keys, cs.values):
                if isinstance(k, ast.Constant) and k.value in ('Key', 'Bucket'):
                    out.append(('Source' + k.value, v))
        else:
            out.append(('SourceKey', cs))
    if 'Delete' in kws:
        out.append(('Key', kws['Delete']))
    if name == 'generate_presigned_url':
        p = kws.get('Params')
        if p is None and len(call.args) > 1:
            p = call.args[1]
        if isinstance(p, ast.Dict):
            for k, v in zip(p.keys, p.values):
                if isinstance(k, ast.Constant) and k.value in ('Key', 'Bucket'):
                    out.append((k.value, v))
        elif p is not None:
            out.append(('Key', p))
    elif name in ('download_file', 'download_fileobj') and 'Key' not in kws:
        if len(call.args) >= 2:
            out.append(('Bucket', call.args[0]))
            out.append(('Key', call.args[1]))
    elif name in ('upload_file', 'upload_fileobj') and 'Key' not in kws:
        if len(call.args) >= 3:
            out.append(('Bucket', call.args[1]))
            out.append(('Key', call.args[2]))
    return out


class Index:
    def __init__(self):
        self.by_file = {}       # rel -> {name: [Func]}
        self.imports = {}       # rel -> {alias: ('mod', rel) | ('func', rel, name)}
        self.mod_files = {}     # dotted -> rel

    def resolve(self, fn, call):
        f = call.func
        if isinstance(f, ast.Name):
            local = self.by_file.get(fn.file, {}).get(f.id)
            if local:
                return local
            imp = self.imports.get(fn.file, {}).get(f.id)
            if imp and imp[0] == 'func':
                return self.by_file.get(imp[1], {}).get(imp[2]) or []
            return []
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            alias = f.value.id
            imp = self.imports.get(fn.file, {}).get(alias)
            if imp and imp[0] == 'mod':
                return self.by_file.get(imp[1], {}).get(f.attr) or []
            if alias in HOST_ALIASES:
                return self.by_file.get('app.py', {}).get(f.attr) or []
        return []


def _analyse(fn, idx):
    taint = {p: (frozenset({p}), frozenset({p})) for p in fn.params
             if p not in ('self', 'cls')}
    nodes = _own_nodes(fn)
    assigns = []
    for n in nodes:
        if isinstance(n, ast.Assign):
            tg = set()
            for t in n.targets:
                tg |= _targets(t)
            assigns.append((tg, n.value))
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)) and n.value is not None:
            assigns.append((_targets(n.target), n.value))
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            assigns.append((_targets(n.target), n.iter))
        elif isinstance(n, ast.NamedExpr):
            assigns.append((_targets(n.target), n.value))
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            assigns.append((_targets(n.optional_vars), n.context_expr))
    for _ in range(6):
        changed = False
        for tg, val in assigns:
            w = _whole(val, taint)
            a = _any(val, taint)
            if not a and not w:
                continue
            a |= w
            for t in tg:
                cw, ca = taint.get(t, _EMPTY)
                if not (w <= cw and a <= ca):
                    taint[t] = (cw | w, ca | a)
                    changed = True
        if not changed:
            break
    fn.taint = taint
    hits = []
    gated = False
    for n in nodes:
        if not isinstance(n, ast.Call):
            continue
        if _is_gate_call(n):
            gated = True
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in SINKS:
            for role, expr in _key_exprs(n):
                w = _whole(expr, taint)
                a = _any(expr, taint) - w
                src = ast.unparse(expr)[:110]
                if w:
                    hits.append((n.lineno, f.attr, role, 'whole', frozenset(w), '', src))
                if a:
                    hits.append((n.lineno, f.attr, role, 'part', frozenset(a), '', src))
            continue
        callees = idx.resolve(fn, n)
        for callee in callees:
            if callee is fn or not callee.sink_params:
                continue
            ps = [p for p in callee.params if p not in ('self', 'cls')]
            pairs = []
            for i, a in enumerate(n.args):
                if i < len(ps) and not isinstance(a, ast.Starred):
                    pairs.append((ps[i], a))
            for kw in n.keywords:
                if kw.arg:
                    pairs.append((kw.arg, kw.value))
            for pname, a in pairs:
                kinds = callee.sink_params.get(pname)
                if not kinds:
                    continue
                w = _whole(a, taint)
                p_ = _any(a, taint) - w
                for (sink, role, shape, cgated) in kinds:
                    via = callee.name
                    src = ast.unparse(a)[:110]
                    if w:
                        hits.append((n.lineno, sink, role, shape, frozenset(w),
                                     via + ('*' if cgated else ''), src))
                    if p_:
                        hits.append((n.lineno, sink, role, 'part', frozenset(p_),
                                     via + ('*' if cgated else ''), src))
    fn.hits = hits
    fn.gated = gated
    new = {}
    for (_ln, sink, role, shape, labs, via, _src) in hits:
        cg = gated or via.endswith('*')
        for lab in labs:
            if lab in fn.params:
                new.setdefault(lab, set()).add((sink, role, shape, cg))
    if new != fn.sink_params:
        fn.sink_params = new
        return True
    return False


def _module_rel(root, dotted, from_rel=None, level=0):
    parts = [p for p in dotted.split('.') if p] if dotted else []
    if level:
        base = os.path.dirname(from_rel).split(os.sep) if os.path.dirname(from_rel) else []
        base = base[:len(base) - (level - 1)] if level > 1 else base
        parts = base + parts
    cand = os.path.join(*parts) + '.py' if parts else None
    if cand and os.path.exists(os.path.join(root, cand)):
        return cand
    if parts:
        cand = os.path.join(*parts, '__init__.py')
        if os.path.exists(os.path.join(root, cand)):
            return cand
    return None


def scan(root=ROOT, files=None):
    idx = Index()
    funcs = []
    for path in (files or py_files(root)):
        try:
            src = open(path, encoding='utf-8', errors='replace').read()
            tree = ast.parse(src)
        except Exception:
            continue
        rel = os.path.relpath(path, root)
        table = idx.by_file.setdefault(rel, {})
        imps = idx.imports.setdefault(rel, {})

        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for al in n.names:
                    m = _module_rel(root, al.name)
                    if m:
                        imps[al.asname or al.name.split('.')[-1]] = ('mod', m)
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ''
                for al in n.names:
                    m = _module_rel(root, (base + '.' + al.name).strip('.'), rel, n.level)
                    if m:
                        imps[al.asname or al.name] = ('mod', m)
                        continue
                    m = _module_rel(root, base, rel, n.level)
                    if m:
                        imps[al.asname or al.name] = ('func', m, al.name)

        def visit(node, prefix='', top=True):
            for c in ast.iter_child_nodes(node):
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = Func(c.name, prefix + c.name, rel, c, top)
                    funcs.append(fn)
                    table.setdefault(c.name, []).append(fn)
                    visit(c, prefix + c.name + '.', False)
                elif isinstance(c, ast.ClassDef):
                    visit(c, prefix + c.name + '.', False)
                else:
                    visit(c, prefix, top)
        visit(tree)
    for _ in range(10):
        changed = False
        for fn in funcs:
            if _analyse(fn, idx):
                changed = True
        if not changed:
            break
    return funcs


def doors(funcs, shapes=('whole',)):
    """Route handlers where a request value reaches storage.

    Returns [(func, [(line, sink, role, shape, labels, via, source)])]."""
    out = []
    for fn in funcs:
        if not fn.is_route:
            continue
        hits = []
        params = set(fn.params)
        for (ln, sink, role, shape, labs, via, src) in fn.hits:
            if shape not in shapes:
                continue
            if REQ in labs or (labs & params):
                hits.append((ln, sink, role, shape, sorted(labs), via, src))
        if hits:
            out.append((fn, hits))
    return out


def door_gated(fn, hits):
    """A door is gated when it calls the shared gate itself, or every
    path to storage runs through a helper that does."""
    if fn.gated:
        return True
    return all(h[5].endswith('*') for h in hits)


def main(argv):
    funcs = scan()
    shapes = ('whole', 'part') if '--part' in argv else ('whole',)
    ds = doors(funcs, shapes)
    for fn, hits in sorted(ds, key=lambda x: (x[0].file, x[0].line)):
        auth = [d for d in fn.decorators if 'route' not in d]
        kinds = sorted({'%s(%s,%s)' % (h[1], h[2], h[3]) for h in hits})
        print('%s:%d %s' % (fn.file, fn.line, fn.name))
        for r in fn.routes:
            print('    route %s %s' % r)
        print('    auth  %s%s' % (', '.join(auth) or '(none)',
                                  '  [gate]' if door_gated(fn, hits) else ''))
        print('    sinks %s' % ', '.join(kinds))
        if '-v' in argv:
            for h in sorted(set((a, b, c, d, tuple(e), f, g) for a, b, c, d, e, f, g in hits)):
                print('      L%d %s %s %s <- %s %s  [%s]' % (
                    h[0], h[1], h[2], h[3], ','.join(h[4]),
                    ('via ' + h[5]) if h[5] else '', h[6]))
    print('\n%d doors in %d route handlers scanned' % (
        len(ds), sum(1 for f in funcs if f.is_route)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
