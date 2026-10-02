"""Family schematic for the legacy chat module (2026-10-02 RCA W3).

``prometheus/families.json`` says which family every top-level name in
``prometheus/legacy/*.py`` belongs to and which families have already
moved out of chat.py. This module applies it:

    python3 -m prometheus.family_map            # the schematic, as text
    python3 -m prometheus.family_map --json     # machine readable

``survey()`` returns, per family: its members, where each one lives,
the cross-family references in and out (the coupling an extraction has
to carry through the ``C`` proxy), and the names no rule claims. The
regression suite (scripts/test_pm_families.py) fails on an unclaimed
name and on a moved family whose members are not where the schematic
says. Pure AST, no imports of the legacy code.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY_DIR = os.path.join(HERE, 'legacy')
FAMILIES_PATH = os.path.join(HERE, 'families.json')


def load_families(path: str = FAMILIES_PATH) -> list[dict]:
    with open(path, 'r', encoding='utf-8') as fh:
        doc = json.load(fh)
    fams = []
    for f in doc.get('families') or []:
        fams.append({
            'name': f['name'], 'module': f.get('module') or f['name'],
            'moved': bool(f.get('moved')), 'purpose': f.get('purpose', ''),
            'names': set(f.get('names') or []),
            'rules': [re.compile(r) for r in (f.get('rules') or [])],
        })
    return fams


def assign(name: str, families: list[dict]) -> str | None:
    """Family for a top-level name: explicit names first, then rules in order."""
    for f in families:
        if name in f['names']:
            return f['name']
    for f in families:
        if any(r.search(name) for r in f['rules']):
            return f['name']
    return None


def legacy_modules() -> list[tuple[str, str]]:
    """[(module_name, path)] with chat first."""
    out = []
    for fn in sorted(os.listdir(LEGACY_DIR)):
        if fn.endswith('.py') and fn != '__init__.py':
            out.append((fn[:-3], os.path.join(LEGACY_DIR, fn)))
    out.sort(key=lambda x: (x[0] != 'chat', x[0]))
    return out


def top_level(path: str) -> dict[str, ast.AST]:
    with open(path, 'r', encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    top: dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    top[t.id] = n
    return top


def _refs(node: ast.AST, universe: set[str]) -> set[str]:
    out = set()
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and x.id in universe:
            out.add(x.id)
        elif isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) \
                and x.value.id in ('_C',) and x.attr in universe:
            out.add(x.attr)  # a moved family reading chat through the proxy
    return out


def survey(families: list[dict] | None = None) -> dict:
    families = families or load_families()
    by_mod = {m: top_level(p) for m, p in legacy_modules()}
    universe = {n for top in by_mod.values() for n in top}
    where = {}
    for m, top in by_mod.items():
        for n in top:
            # chat.py re-imports moved names at its tail; the defining module wins
            if n not in where or m != 'chat':
                where[n] = m
    members: dict[str, list[str]] = {f['name']: [] for f in families}
    unassigned = []
    fam_of = {}
    for n in sorted(universe):
        if n.startswith('__') and n.endswith('__'):
            continue  # module metadata (__all__), not a family member
        f = assign(n, families)
        if f is None:
            unassigned.append(n)
            continue
        fam_of[n] = f
        members[f].append(n)
    # cross-family references
    out_refs = {f['name']: {} for f in families}
    in_refs = {f['name']: {} for f in families}
    for m, top in by_mod.items():
        for n, node in top.items():
            if m == 'chat' and where.get(n) != 'chat':
                continue  # the re-export import, not a definition
            fa = fam_of.get(n)
            if not fa:
                continue
            for r in _refs(node, universe):
                fb = fam_of.get(r)
                if fb and fb != fa:
                    out_refs[fa][r] = out_refs[fa].get(r, 0) + 1
                    in_refs[fb][r] = in_refs[fb].get(r, 0) + 1
    report = {'modules': {m: len(t) for m, t in by_mod.items()},
              'unassigned': unassigned, 'families': []}
    for f in families:
        mems = members[f['name']]
        lives = {}
        for n in mems:
            lives[where.get(n, '?')] = lives.get(where.get(n, '?'), 0) + 1
        misplaced = []
        if f['moved']:
            misplaced = [n for n in mems if where.get(n) != f['module']]
        report['families'].append({
            'name': f['name'], 'module': f['module'], 'moved': f['moved'],
            'purpose': f['purpose'], 'count': len(mems), 'members': mems,
            'lives_in': lives, 'misplaced': misplaced,
            'outbound': out_refs[f['name']], 'inbound': in_refs[f['name']],
            'coupling': sum(out_refs[f['name']].values()) + sum(in_refs[f['name']].values()),
        })
    return report


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    rep = survey()
    if '--json' in argv:
        print(json.dumps(rep, indent=1))
        return 0 if not rep['unassigned'] else 1
    print('legacy modules:', ', '.join(f"{m} ({n} names)" for m, n in rep['modules'].items()))
    print()
    order = sorted(rep['families'], key=lambda f: (f['moved'], f['coupling']))
    for f in order:
        state = 'MOVED' if f['moved'] else f"in chat.py, coupling {f['coupling']} (out {sum(f['outbound'].values())}, in {sum(f['inbound'].values())})"
        print(f"{f['name']:12s} {f['count']:3d} names  {state}")
        print(f"             {f['purpose']}")
        if f['misplaced']:
            print(f"             MISPLACED: {', '.join(f['misplaced'])}")
    if rep['unassigned']:
        print('\nUNASSIGNED (place these in prometheus/families.json):')
        for n in rep['unassigned']:
            print('  ', n)
        return 1
    print('\nnext extraction candidates (lowest coupling first): '
          + ', '.join(f['name'] for f in order if not f['moved'] and f['name'] != 'shared')[:200])
    return 0


if __name__ == '__main__':
    sys.exit(main())
