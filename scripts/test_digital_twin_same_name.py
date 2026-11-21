#!/usr/bin/env python3
"""Same-name twins read apart (Jenna 2026-11-20, keep both Paramount+ twins):
the list carries who built each and when, only when another visible twin
shares the name; every picker names a twin the same way; search matches
the qualifier; the /list entry carries the builder's display name."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import _index_page  # noqa: E402  (the page with its split files inlined back, 2026-11-20)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from migration import digital_twins as DT  # noqa: E402

USERS = {'users': {
    'cpearson': {'first_name': 'Casey', 'last_name': 'Pearson', 'email': 'casey.pearson@paramount.com'},
    'admin': {'first_name': 'Jenna', 'last_name': 'Menking`', 'email': 'jenna@crosswalknyc.com'},
    'solo': {'name': 'Solo Seat'},
    'disp': {'display_name': 'Display Only'},
    'bare': {'email': 'bare@example.com'},
}}


class BuilderName(unittest.TestCase):
    def test_display_name_then_username(self):
        self.assertEqual(DT.builder_name('cpearson', USERS), 'Casey Pearson')
        self.assertEqual(DT.builder_name('admin', USERS), 'Jenna Menking')   # a stray backtick on the last name is dropped
        self.assertEqual(DT.builder_name('solo', USERS), 'Solo Seat')
        self.assertEqual(DT.builder_name('disp', USERS), 'Display Only')
        self.assertEqual(DT.builder_name('bare', USERS), 'bare')
        self.assertEqual(DT.builder_name('nobody', USERS), 'nobody')
        self.assertEqual(DT.builder_name('', USERS), '')
        self.assertEqual(DT.builder_name(None, USERS), '')

    def test_annotate_in_place(self):
        runs = [{'key': 'a', 'created_by': 'cpearson'}, {'key': 'b', 'created_by': 'admin'}, {'key': 'c'}, 'not a dict']
        out = DT.annotate_builders(runs, USERS)
        self.assertIs(out, runs)
        self.assertEqual([r.get('built_by') for r in runs if isinstance(r, dict)], ['Casey Pearson', 'Jenna Menking', ''])

    def test_never_raises(self):
        self.assertEqual(DT.builder_name('x', {'users': 'broken'}), 'x')
        self.assertEqual(DT.annotate_builders(None, USERS), None)


def _page():
    return _index_page.text(ROOT)   # the page with its split files inlined back (2026-11-20)


def _fn(src, name):
    a = src.index('            function ' + name + '(')
    b = min(src.index('\n            function ', a + 10), src.index('\n            window.', a + 10), src.index('\n            var ', a + 10))
    return src[a:b]


class Page(unittest.TestCase):
    def test_every_picker_and_the_entry_carry_the_qualifier(self):
        html = _page()
        for need in ('function twinBuiltLine(r, withSource)', 'function twinNameShared(r)', 'function twinQualifier(r)', 'function twinOptionLabel(r)'):
            self.assertIn(need, html, need)
        entry = html[html.index('function libEntry(r, i)'):html.index('function libDoc(key)')]
        self.assertIn("twinQualifier(r) ? '<span class=\"built\">'", entry)
        learn = html[html.index('function libLearn(r, doc)'):html.index('function libStats(doc)')]
        self.assertIn('twinBuiltLine({ created_at: m.created_at || r.created_at, built_by: r.built_by || m.created_by', learn)
        # the search haystack carries the builder and the qualifier
        lib = html[html.index('function renderLibrary()'):html.index('function pad2(i)')]
        self.assertIn('[r.name, r.subject, r.built_by, twinQualifier(r)].join', lib)
        # the pickers: the spine switch and the room's second twin, Rooms on, the pilot's other twins, the Predictive Analytics audience step,
        # and the Track record's Log a call form (2026-11-20)
        self.assertEqual(html.count("esc(twinOptionLabel(r))"), 6, 'six option pickers name a twin through twinOptionLabel')
        pa = html[html.index('function paStepAudience(p)'):html.index('function paStepQuestion(p)')]
        self.assertIn("twinQualifier(r) ? '<span class=\"built\">'", pa)
        self.assertNotIn("esc(r.name || r.subject) + '</option>'", html)
        self.assertNotIn('\u2014', entry + learn + lib)

    def test_list_route_annotates_builders(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        route = api[api.index('def dt_list():'):api.index('def dt_assign_company(')]
        self.assertIn('DT.annotate_builders(vis)', route)


@unittest.skipUnless(shutil.which('node'), 'node not available')
class Behaviour(unittest.TestCase):
    def test_qualifier_only_for_shared_names(self):
        src = _page()
        js = '\n'.join(_fn(src, n) for n in ('twinBuiltLine', 'twinNameShared', 'twinQualifier', 'twinOptionLabel', 'fmtDay'))
        a = src.index('            var MONTHS = ')
        js = src[a:src.index('\n', a) + 1] + js
        runs = [
            {'key': 'pp', 'name': 'Paramount+', 'created_at': '2026-11-19T18:57:25Z', 'built_by': 'Casey Pearson', 'created_by': 'cpearson'},
            {'key': 'pp0', 'name': 'paramount+ ', 'created_at': '2026-10-30T20:21:56Z', 'built_by': 'Jenna Menking', 'created_by': 'admin'},
            {'key': 'pv', 'name': 'Amazon Prime Video', 'created_at': '2026-11-18T18:57:54Z', 'built_by': 'Jenna Menking'},
            {'key': 'nb', 'name': 'No Builder', 'created_at': '2026-11-01T00:00:00Z'},
        ]
        node = ("var S = { runs: " + json.dumps(runs) + " };\n" + js + "\n"
                "var q = S.runs.map(twinQualifier), o = S.runs.map(twinOptionLabel);\n"
                "var withSrc = twinBuiltLine({ created_at: '2026-11-19T18:57:25Z', built_by: 'Casey Pearson', source: 'profile', profile_name: 'Paramount+' }, true);\n"
                "var demo = twinBuiltLine({ created_at: '2026-11-01T00:00:00Z', source: 'demographics' }, true);\n"
                "S.runs = S.runs.slice(2); var after = S.runs.map(twinQualifier);\n"
                "console.log(JSON.stringify({ q: q, o: o, withSrc: withSrc, demo: demo, after: after }));")
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(node)
            path = fh.name
        try:
            out = subprocess.run(['node', path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        got = json.loads(out.stdout.strip().splitlines()[-1])
        # the two Paramount+ twins (case and a trailing space folded) carry the qualifier; the others carry nothing
        self.assertEqual(got['q'], ['Built Nov 19 by Casey Pearson', 'Built Oct 30 by Jenna Menking', '', ''])
        self.assertEqual(got['o'][:2], ['Paramount+ (Built Nov 19 by Casey Pearson)', 'paramount+  (Built Oct 30 by Jenna Menking)'])
        self.assertEqual(got['o'][2:], ['Amazon Prime Video', 'No Builder'])
        self.assertEqual(got['withSrc'], 'Built Nov 19 from the Paramount+ profile by Casey Pearson')
        self.assertEqual(got['demo'], 'Built Nov 1 from a shaped group')
        # once the list no longer holds two of a name, the qualifier goes (the memo follows the runs list)
        self.assertEqual(got['after'], ['', ''])


if __name__ == '__main__':
    unittest.main(verbosity=2)
