#!/usr/bin/env python3
"""The twin's own people have faces (Jenna 2026-11-19: the kinds that make up
the twin, shown on the twin page with the person who stands for each).

Held here: the twin pass attaches cached faces and generates the missing
ones for the kinds' representatives, writes the url onto the twin document
(load fresh, by kind id, save); the self-heal attaches what exists without
generating; the schedule runs once per process per twin and not at all when
every kind already has its face; the twin page carries the section.
    python3 scripts/test_digital_twin_twin_portraits.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
sys.path.insert(0, HERE)

from migration import digital_twin_portraits as PT  # noqa: E402
from migration import digital_twin_synthesis as SYN  # noqa: E402
import test_digital_twins_company_scope as CS  # noqa: E402

KEY = 'digital-twins/a1/Kinds_Twin_20261119_120000_abcdef1234.json.gz'


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (64, 64), (120, 60, 200)).save(buf, format='PNG')
    return buf.getvalue()


def _twin():
    groups = []
    for i, (lab, nm, age, city, g) in enumerate([('Older regulars on sport and crime series', 'Victor', 56, 'Tampa St Petersburg Sarasota', 'M'), ('Thursday night football people', 'Devin', 41, 'Chicago', 'M'),
                                                  ('Under-35s testing it against Netflix', 'Sofia', 25, 'Atlanta', 'F')]):
        groups.append({'id': f'k{i}', 'label': lab, 'share_pct': [12.9, 11.9, 7.1][i], 'people': [16469203, 15193704, 9101538][i], 'who': 'w', 'relationship': 'r', 'decides': 'd', 'moves_them': 'm', 'holds_them_back': 'h',
                       'representative': {'name': nm, 'age': age, 'city': city, 'gender': g, 'gender_label': 'Male' if g == 'M' else 'Female', 'occupation': 'Retail', 'income': '$50,000 - $74,999'}})
    return {'meta': {'name': 'Kinds Twin', 'subject': 'Kinds Twin', 'key': KEY, 'created_by': 'a1', 'size_us': 128116302}, 'identity': {'demographics': {}}, 'archetypes': {'k': 3, 'noun': 'subscribers', 'size_people': 128116302, 'groups': groups}, 'panel_sessions': []}


class FacesForTheKinds(unittest.TestCase):
    def setUp(self):
        self.s3 = CS.FakeS3()
        self.s3.head_object = lambda Bucket, Key, **kw: ({} if Key in self.s3.objs else (_ for _ in ()).throw(KeyError(Key)))
        self.asked = []
        PT.reset_for_tests()
        PT.configure(generator=lambda prompt: self.asked.append(prompt) or _png(), background=False, verify_delay=0)
        self.twin = _twin()
        SYN.save_twin(self.s3, KEY, self.twin)

    def tearDown(self):
        PT.configure()
        PT.reset_for_tests()

    def test_the_pass_gives_every_kind_a_face_and_writes_it_on_the_twin(self):
        try:
            import PIL  # noqa: F401
        except Exception:
            self.skipTest('PIL not installed')
        out = PT.ensure_twin_portraits(self.s3, KEY, self.twin)
        self.assertEqual(sorted(out), ['k0', 'k1', 'k2'])
        self.assertEqual(len(self.asked), 3, 'one generation per kind')
        reps = [g['representative'] for g in self.twin['archetypes']['groups']]
        self.assertTrue(all(r.get('portrait_url') and r.get('portrait_hash') for r in reps), 'in memory')
        saved = SYN.load_twin(self.s3, KEY)
        self.assertTrue(all(g['representative'].get('portrait_url') for g in saved['archetypes']['groups']), 'on the document')
        self.assertTrue(all('/api/digital-twins/portrait/' in g['representative']['portrait_url'] for g in saved['archetypes']['groups']))
        # the prompt never carries the kind's label or the twin's name (the same rule as a seat)
        for pr in self.asked:
            self.assertNotIn('Kinds Twin', pr)
            self.assertNotIn('football', pr.lower())
        # a second pass generates nothing: every face is cached
        self.asked.clear()
        PT.ensure_twin_portraits(self.s3, KEY, self.twin)
        self.assertEqual(self.asked, [])

    def test_the_self_heal_attaches_what_exists_without_generating(self):
        try:
            import PIL  # noqa: F401
        except Exception:
            self.skipTest('PIL not installed')
        PT.ensure_twin_portraits(self.s3, KEY, self.twin)          # faces exist now
        fresh = _twin()                                             # a copy of the twin with no urls on it
        self.asked.clear()
        n = PT.attach_twin_portraits(fresh, self.s3)
        self.assertEqual(n, 3)
        self.assertEqual(self.asked, [], 'attach never generates')
        self.assertTrue(all(g['representative'].get('portrait_url') for g in fresh['archetypes']['groups']))
        self.assertEqual(PT.attach_twin_portraits(fresh, self.s3), 0, 'nothing left to attach')

    def test_the_schedule_runs_once_and_skips_a_twin_already_pictured(self):
        try:
            import PIL  # noqa: F401
        except Exception:
            self.skipTest('PIL not installed')
        self.assertTrue(PT.schedule_twin_portraits(self.s3, KEY, self.twin))      # inline under background=False
        self.assertEqual(len(self.asked), 3)
        self.assertFalse(PT.schedule_twin_portraits(self.s3, KEY, self.twin), 'every kind has its face: nothing to schedule')
        PT.reset_for_tests()       # the defaults, then the generator again with the pass in the background
        PT.configure(generator=lambda prompt: self.asked.append(prompt) or _png(), background=True, verify_delay=0)
        bare = _twin()
        self.assertTrue(PT.schedule_twin_portraits(self.s3, 'digital-twins/a1/other.json.gz', bare))
        self.assertFalse(PT.schedule_twin_portraits(self.s3, 'digital-twins/a1/other.json.gz', bare), 'once per process per twin')


class TwinPage(unittest.TestCase):
    def test_the_twin_page_carries_the_kinds_section(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
        self.assertIn('function kindsSection(t)', html)
        self.assertIn('Who makes up this audience', html)
        self.assertIn("var out = kindsSection(t) + '<div class=\"dtw-actions dtw-export\">", html)
        for need in ('dtw-kind-face', 'dtw-kind-about', 'What holds them back', 'rep.portrait_url'):
            self.assertIn(need, html, need)


if __name__ == '__main__':
    unittest.main(verbosity=2)
