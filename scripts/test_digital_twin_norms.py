#!/usr/bin/env python3
"""Pilot Testing norms ledger (2026-10-28). Hermetic FakeS3."""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from migration import digital_twin_norms as N  # noqa: E402


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    """ETag-aware: a stale IfMatch raises the way the real client does."""

    def __init__(self):
        self.objs = {}
        self.puts = 0

    def _etag(self, key):
        import hashlib
        return hashlib.md5(self.objs[key]).hexdigest()

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key]), 'ETag': f'"{self._etag(Key)}"'}

    def put_object(self, Bucket, Key, Body, **kw):
        if 'IfMatch' in kw and (Key not in self.objs or self._etag(Key) != kw['IfMatch']):
            raise RuntimeError('PreconditionFailed 412')
        if 'IfNoneMatch' in kw and Key in self.objs:
            raise RuntimeError('PreconditionFailed 412')
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')
        self.puts += 1

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)


class Seed(unittest.TestCase):
    def test_seed_cells_are_sourced_and_canonical(self):
        self.assertGreaterEqual(len(N.SEED_CELLS), 16)
        for c in N.SEED_CELLS:
            self.assertIn(c['format'], N.FORMATS)
            self.assertIn(c['genre'], N.GENRES)
            self.assertIn(c['metric'], N.METRICS)
            self.assertTrue(c['sources'], c)
            for s in c['sources']:
                self.assertTrue(str(s.get('url') or '').startswith('http'), s)
                self.assertTrue(s.get('note'))
            self.assertTrue(0 < float(c['value']) < 100)
        keys = [N.cell_key(c['format'], c['genre'], c['metric']) for c in N.SEED_CELLS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_seed_is_idempotent(self):
        s3 = FakeS3()
        first = N.seed(s3)
        self.assertEqual(first['written'], len(N.SEED_CELLS))
        self.assertGreaterEqual(first['sources'], 1)
        puts = s3.puts
        second = N.seed(s3)
        self.assertEqual(second['written'], 0)
        self.assertEqual(second['updated'], 0)
        self.assertEqual(second['skipped'], len(N.SEED_CELLS))
        self.assertEqual(s3.puts, puts)
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        for k, cell in doc['cells'].items():
            self.assertTrue(cell['seeded'], k)
            self.assertTrue(cell['sources'], k)
            self.assertEqual(cell['n'], 0)
        rep = N.sources_report(doc)
        self.assertEqual(rep['cells'], len(N.SEED_CELLS))
        self.assertEqual(rep['accumulated'], 0)

    def test_seed_never_overwrites_an_accumulated_cell(self):
        s3 = FakeS3()
        N.seed(s3)
        N.record(s3, 'feature', 'comedy', {'excellent': 60.0}, 't1')
        before = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))['cells'][N.cell_key('feature', 'comedy', 'excellent')]
        self.assertFalse(before['seeded'])
        changed = [dict(c, value=99.0) if (c['format'], c['genre'], c['metric']) == ('feature', 'comedy', 'excellent') else c for c in N.SEED_CELLS]
        N.seed(s3, cells=changed)
        after = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))['cells'][N.cell_key('feature', 'comedy', 'excellent')]
        self.assertFalse(after['seeded'])
        self.assertEqual(after['seed_value'], 99.0)
        self.assertEqual(after['our_n'], 1)
        self.assertAlmostEqual(after['value'], (99.0 * N.SEED_WEIGHT + 60.0) / (N.SEED_WEIGHT + 1), places=3)

    def test_dry_run_writes_nothing(self):
        s3 = FakeS3()
        st = N.seed(s3, dry_run=True)
        self.assertEqual(st['cells'], len(N.SEED_CELLS))
        self.assertNotIn(N.NORMS_KEY, s3.objs)


class Record(unittest.TestCase):
    def test_record_updates_n_and_value(self):
        s3 = FakeS3()
        N.seed(s3)
        k = N.cell_key('pilot', 'comedy', 'definitely_recommend')
        doc = N.record(s3, 'pilot', 'comedy', {'definitely_recommend': 68.0, 'excellent': 40.0}, 'test-1')
        cell = doc['cells'][k]
        self.assertEqual(cell['n'], 1)
        self.assertEqual(cell['our_n'], 1)
        self.assertFalse(cell['seeded'])
        self.assertEqual(cell['value'], 68.0)          # no seed on the pilot cell: our value alone
        doc = N.record(s3, 'pilot', 'comedy', {'definitely_recommend': 52.0}, 'test-2')
        cell = doc['cells'][k]
        self.assertEqual(cell['n'], 2)
        self.assertEqual(cell['value'], 60.0)
        # the all-genres cell for the format moves too
        cell_all = doc['cells'][N.cell_key('pilot', 'all', 'definitely_recommend')]
        self.assertEqual(cell_all['n'], 2)
        # a seeded cell blends with the seed as a pseudo-count
        doc = N.record(s3, 'feature', 'all', {'excellent': 50.0}, 'test-3')
        fc = doc['cells'][N.cell_key('feature', 'all', 'excellent')]
        self.assertFalse(fc['seeded'])
        self.assertEqual(fc['n'], 1)
        self.assertAlmostEqual(fc['value'], (30.0 * N.SEED_WEIGHT + 50.0) / (N.SEED_WEIGHT + 1), places=3)
        self.assertTrue(any('Seed 30.0' in str(s.get('note')) for s in fc['sources']))

    def test_same_test_counts_once(self):
        s3 = FakeS3()
        N.record(s3, 'pilot', 'drama', {'excellent': 44.0}, 'dup')
        doc = N.record(s3, 'pilot', 'drama', {'excellent': 44.0}, 'dup')
        self.assertIsNone(doc)   # nothing changed, nothing written
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        self.assertEqual(doc['cells'][N.cell_key('pilot', 'drama', 'excellent')]['n'], 1)

    def test_record_ignores_unknown_metrics_and_empty(self):
        s3 = FakeS3()
        self.assertIsNone(N.record(s3, 'pilot', 'drama', {'nonsense': 44.0}, 'x'))
        self.assertIsNone(N.record(s3, 'pilot', 'drama', {}, 'x'))
        self.assertIsNone(N.record(s3, 'pilot', 'drama', {'excellent': 44.0}, ''))

    def test_write_conflict_retries(self):
        s3 = FakeS3()
        N.seed(s3)

        class Racy(FakeS3):
            pass
        racy = Racy()
        racy.objs = dict(s3.objs)
        orig = racy.put_object
        state = {'n': 0}

        def racing_put(Bucket, Key, Body, **kw):
            if 'IfMatch' in kw:
                state['n'] += 1
            if state['n'] == 1 and 'IfMatch' in kw:
                # another writer lands first
                cur = json.loads(racy.objs[Key].decode('utf-8'))
                cur['cells']['x|y|z'] = {'value': 1}
                racy.objs[Key] = json.dumps(cur).encode('utf-8')
            return orig(Bucket, Key, Body, **kw)
        racy.put_object = racing_put
        doc = N.record(racy, 'pilot', 'horror', {'excellent': 20.0}, 'race')
        self.assertIn('x|y|z', doc['cells'])
        self.assertEqual(doc['cells'][N.cell_key('pilot', 'horror', 'excellent')]['n'], 1)

    def test_daily_backup_once(self):
        s3 = FakeS3()
        N.seed(s3)
        N.record(s3, 'pilot', 'horror', {'excellent': 20.0}, 'b1')
        N.record(s3, 'pilot', 'horror', {'excellent': 24.0}, 'b2')
        backups = [k for k in s3.objs if k.startswith(N.BACKUP_PREFIX)]
        self.assertEqual(len(backups), 1)


class Lookup(unittest.TestCase):
    def test_norms_for_falls_back_genre_then_format(self):
        s3 = FakeS3()
        N.seed(s3)
        n = N.norms_for('pilot', 'unscripted', s3=s3)   # no pilot cells, no unscripted cells: feature / all
        self.assertEqual(n['format'], 'pilot')
        self.assertEqual(n['genre_used'], 'all')
        self.assertEqual(n['all']['excellent']['value'], 30.0)
        self.assertEqual(n['all']['excellent']['format_used'], 'feature')
        self.assertEqual(n['genre']['excellent']['value'], 30.0)
        self.assertEqual(n['genre']['excellent']['genre_used'], 'all')
        c = N.norms_for('feature', 'comedy', s3=s3)
        self.assertEqual(c['genre_used'], 'comedy')
        self.assertEqual(c['genre']['excellent']['value'], 48.0)
        self.assertEqual(c['genre']['total_positive']['value'], 82.0)
        self.assertEqual(c['genre']['lead_character']['genre_used'], 'all')   # no comedy character cell: all
        self.assertNotIn('sources', json.dumps(c))

    def test_norms_for_without_a_ledger_uses_the_seed(self):
        n = N.norms_for('pilot', 'comedy', s3=FakeS3())
        self.assertTrue(n['from_seed_only'])
        self.assertEqual(n['genre']['definitely_recommend']['value'], 71.0)

    def test_values_at_one_decimal_after_our_tests(self):
        s3 = FakeS3()
        N.seed(s3)
        N.record(s3, 'feature', 'all', {'excellent': 33.3333}, 'd1')
        n = N.norms_for('feature', 'all', s3=s3)
        v = n['all']['excellent']['value']
        self.assertEqual(v, round(v, 1))
        self.assertFalse(n['all']['excellent']['seeded'])
        self.assertEqual(n['all']['excellent']['n'], 1)

    def test_norm_line(self):
        n = N.norms_for('pilot', 'comedy', s3=FakeS3())
        line = N.norm_line('definitely_recommend', 68.0, n, 'pilot', 'comedy')
        self.assertEqual(line, 'Definitely recommend 68.0%, comedy screenings usually land near 71%.')
        line2 = N.norm_line('definitely_recommend', 82.0, n, 'pilot', 'comedy')
        self.assertIn('sits well above', line2)
        plain = N.norm_line('excellent', 20.0, N.norms_for('pilot', 'all', s3=FakeS3()), 'pilot', 'all')
        self.assertEqual(plain, 'Excellent 20.0%, screenings usually land near 30%; this one sits below.')
        for l in (line, line2, plain):
            self.assertNotIn('\u2014', l)

    def test_genre_and_format_aliases(self):
        self.assertEqual(N._genre('Rom-Com'), 'romance')
        self.assertEqual(N._genre('sitcom'), 'comedy')
        self.assertEqual(N._genre('reality'), 'unscripted')
        self.assertEqual(N._genre('nonsense'), 'all')
        self.assertEqual(N._fmt('movie'), 'feature')
        self.assertEqual(N._fmt('nonsense'), 'pilot')


class Script(unittest.TestCase):
    def test_seed_script_exists_and_is_quiet(self):
        path = os.path.join(ROOT, 'scripts', 'seed_pilot_norms.py')
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('--dry-run', src)
        self.assertNotIn('\u2014', src)


if __name__ == '__main__':
    unittest.main(verbosity=1)
