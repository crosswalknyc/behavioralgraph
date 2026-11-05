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
        N.record(s3, 'feature', 'all', {'excellent': 60.0}, 't1')
        before = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))['cells'][N.cell_key('feature', 'all', 'excellent')]
        self.assertFalse(before['seeded'])
        changed = [dict(c, value=99.0) if (c['format'], c['genre'], c['metric']) == ('feature', 'all', 'excellent') else c for c in N.SEED_CELLS]
        N.seed(s3, cells=changed)
        after = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))['cells'][N.cell_key('feature', 'all', 'excellent')]
        self.assertFalse(after['seeded'])
        self.assertEqual(after['seed_value'], 99.0)
        self.assertEqual(after['our_n'], 1)
        self.assertAlmostEqual(after['value'], (99.0 * N.SEED_WEIGHT + 60.0) / (N.SEED_WEIGHT + 1), places=3)

    def test_drop_tests_puts_a_cell_back_on_its_seed(self):
        s3 = FakeS3()
        N.seed(s3)
        N.record(s3, 'pilot', 'all', {'total_positive': 43.7, 'finish': 91.3}, 'trial-1')
        N.record(s3, 'pilot', 'all', {'total_positive': 40.0, 'finish': 90.0}, 'trial-2')
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        self.assertEqual(doc['cells'][N.cell_key('pilot', 'all', 'total_positive')]['our_n'], 2)
        self.assertEqual(doc['cells'][N.cell_key('pilot', 'all', 'finish')]['seed_value'], None)   # no anchor anywhere for finish
        st = N.seed(s3, drop_tests=['trial-1', 'trial-2'])
        self.assertEqual(st['dropped_tests'], 4)
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        tp = doc['cells'][N.cell_key('pilot', 'all', 'total_positive')]
        self.assertTrue(tp['seeded'])
        self.assertEqual((tp['value'], tp['our_n'], tp['tests']), (65.0, 0, []))
        self.assertNotIn(N.cell_key('pilot', 'all', 'finish'), doc['cells'])    # a cell with no seed and no tests is gone
        self.assertEqual(st['removed'], 1)

    def test_a_cell_with_no_anchor_needs_several_tests_before_it_is_a_norm(self):
        s3 = FakeS3()
        N.seed(s3)
        for i in range(N.MIN_OWN_TESTS - 1):
            N.record(s3, 'pilot', 'all', {'finish': 90.0 + i}, f'p{i}')
        n = N.norms_for('pilot', 'all', s3=s3)
        self.assertNotIn('finish', n['all'])             # four tests of our own are not a norm yet
        N.record(s3, 'pilot', 'all', {'finish': 95.0}, 'p-last')
        n = N.norms_for('pilot', 'all', s3=s3)
        self.assertEqual(n['all']['finish']['n'], N.MIN_OWN_TESTS)
        self.assertFalse(n['all']['finish']['seeded'])

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
        # the pilot comedy cell has no seed of its own: it inherits the pilot all-genres anchor (50, weight 8), so one test blends in
        self.assertEqual(cell['seed_value'], 50.0)
        self.assertAlmostEqual(cell['value'], (50.0 * N.SEED_WEIGHT + 68.0) / (N.SEED_WEIGHT + 1), places=3)
        doc = N.record(s3, 'pilot', 'comedy', {'definitely_recommend': 52.0}, 'test-2')
        cell = doc['cells'][k]
        self.assertEqual(cell['n'], 2)
        self.assertAlmostEqual(cell['value'], (50.0 * N.SEED_WEIGHT + 68.0 + 52.0) / (N.SEED_WEIGHT + 2), places=3)
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
        n = N.norms_for('pilot', 'unscripted', s3=s3)   # pilot cells of their own (the recruited-screening anchors); no unscripted cells: all
        self.assertEqual(n['format'], 'pilot')
        self.assertEqual(n['genre_used'], 'all')
        self.assertEqual(n['all']['excellent']['value'], 30.0)
        self.assertEqual(n['all']['excellent']['format_used'], 'pilot')
        self.assertEqual(n['genre']['excellent']['value'], 30.0)
        self.assertEqual(n['genre']['excellent']['genre_used'], 'all')
        t = N.norms_for('trailer', 'all', s3=s3)       # a trailer reads its own tune-in and the feature's character norms
        self.assertEqual(t['all']['tune_in']['format_used'], 'trailer')
        self.assertEqual(t['all']['lead_character']['format_used'], 'feature')
        c = N.norms_for('feature', 'comedy', s3=s3)
        self.assertEqual(c['genre_used'], 'all')        # no comedy norm is published; the blockbuster benchmark is not the comedy average
        self.assertEqual(c['genre']['excellent']['value'], 30.0)
        f = N.norms_for('feature', 'family', s3=s3)     # the NRG five-year family norms
        self.assertEqual(f['genre_used'], 'family')
        self.assertEqual((f['genre']['excellent']['value'], f['genre']['total_positive']['value'], f['genre']['definitely_recommend']['value']), (40.0, 75.0, 60.0))
        self.assertEqual(N.norms_for('feature', 'animation', s3=s3)['genre']['total_positive']['value'], 75.0)
        self.assertEqual(f['genre']['lead_character']['genre_used'], 'all')   # no family character cell: all
        self.assertNotIn('sources', json.dumps(c))

    def test_norms_for_without_a_ledger_uses_the_seed(self):
        n = N.norms_for('pilot', 'comedy', s3=FakeS3())
        self.assertTrue(n['from_seed_only'])
        self.assertEqual(n['genre']['definitely_recommend']['value'], 50.0)
        self.assertEqual(n['genre']['definitely_recommend']['genre_used'], 'all')

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
        # the line names the norm it reads against (2026-11-04): a chosen genre with no norm of its own reads the all-genres
        # column and says so; the pilot column is a feature-screening stand-in until five pilot tests sit in it, and says
        # THAT (Liz Huszarik 2026-11-05: the feature figure is never called the pilot norm)
        n = N.norms_for('pilot', 'comedy', s3=FakeS3())
        line = N.norm_line('definitely_recommend', 54.0, n, 'pilot', 'comedy')
        self.assertEqual(line, 'Definitely recommend 54.0%, four points above the feature screening norm of 50%.')
        line2 = N.norm_line('definitely_recommend', 82.0, n, 'pilot', 'comedy', tally='9 of 12')
        self.assertEqual(line2, 'Definitely recommend 82.0% (9 of 12), 32 points above the feature screening norm of 50%.')
        fam = N.norm_line('total_positive', 80.0, N.norms_for('feature', 'family', s3=FakeS3()), 'feature', 'family')
        self.assertEqual(fam, 'Total positive 80.0%, five points above the family feature norm of 75%.')
        flat = N.norm_line('total_positive', 75.3, N.norms_for('feature', 'family', s3=FakeS3()), 'feature', 'family')
        self.assertEqual(flat, 'Total positive 75.3%, right at the family feature norm of 75%.')
        plain = N.norm_line('excellent', 20.0, N.norms_for('pilot', 'all', s3=FakeS3()), 'pilot', 'all')
        self.assertEqual(plain, 'Excellent 20.0%, ten points below the feature screening norm of 30%.')
        # the feature column is a norm of its own format: 'the all-features norm'
        feat = N.norm_line('excellent', 20.0, N.norms_for('feature', 'all', s3=FakeS3()), 'feature', 'all')
        self.assertEqual(feat, 'Excellent 20.0%, ten points below the all-features norm of 30%.')
        # a trailer reading the feature's character anchor names the format it borrowed
        tr = N.norm_line('lead_character', 61.0, N.norms_for('trailer', 'all', s3=FakeS3()), 'trailer', 'all')
        self.assertEqual(tr, 'Lead 61.0%, eleven points above the all-features norm of 50%.')
        # a measure with no norm yet says so in words, never a bare figure
        fin = N.norm_line('finish', 96.7, N.norms_for('pilot', 'all', s3=FakeS3()), 'pilot', 'all', tally='16 of 16')
        self.assertEqual(fin, 'Would finish 96.7% (16 of 16). No would-finish norm yet; it fills as tests are filed, 0 of 5 so far.')
        for l in (line, line2, fam, flat, plain, feat, tr, fin):
            self.assertNotIn('\u2014', l)

    def test_a_stand_in_reads_at_its_published_value_until_five_tests_of_the_format(self):
        """The pilot cells carry the feature recruited-screening card. One
        pilot test blended in (50 -> 47.8 in the running mean) must not
        read as 'the all-pilots norm of 48%': the column is a feature
        screening stand-in at 50 until five pilot tests sit in it, then
        the running mean reads as the pilot norm."""
        s3 = FakeS3()
        N.seed(s3)
        N.record(s3, 'pilot', 'procedural', {'definitely_recommend': 29.8, 'excellent': 6.5, 'total_positive': 49.4}, 'ncis')
        n = N.norms_for('pilot', 'procedural', s3=s3)
        self.assertEqual(n['stand_in'], 'feature screening')
        self.assertEqual(n['all']['definitely_recommend'], {'value': 50.0, 'n': 1, 'seeded': False, 'format_used': 'pilot', 'genre_used': 'all', 'stand_in': 'feature screening'})
        self.assertEqual(N.norm_line('definitely_recommend', 29.8, n, 'pilot', 'procedural', tally='5 of 16'),
                         'Definitely recommend 29.8% (5 of 16), 20 points below the feature screening norm of 50%.')
        ch = n['choice']
        self.assertEqual(ch['compared_label'], 'Feature screenings')
        self.assertEqual(ch['compared_line'], 'Compared to feature screenings.')
        self.assertEqual(ch['notes'], ['No pilot norm yet; it fills as pilot tests are filed, 1 of 5 so far.',
                                       'No procedural norm yet; it fills as tests are filed under procedural, 1 of 5 so far.'])
        self.assertEqual(N.norm_word(ch), 'the feature screening norm')
        # the ledger itself still blends (the running mean is the pilot norm in the making)
        cell = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))['cells'][N.cell_key('pilot', 'all', 'definitely_recommend')]
        self.assertAlmostEqual(cell['value'], (50.0 * N.SEED_WEIGHT + 29.8) / (N.SEED_WEIGHT + 1), places=3)
        self.assertEqual(cell['stand_in'], 'feature screening')
        # four more pilot tests: the column is a pilot norm, read at the running mean, named for its format
        for i in range(N.MIN_OWN_TESTS - 1):
            N.record(s3, 'pilot', 'all', {'definitely_recommend': 30.0 + i, 'excellent': 10.0 + i, 'total_positive': 50.0 + i}, f'p{i}')
        n = N.norms_for('pilot', 'all', s3=s3)
        self.assertEqual(n['stand_in'], '')
        self.assertEqual(n['all']['definitely_recommend']['stand_in'], '')
        self.assertEqual(n['all']['definitely_recommend']['n'], N.MIN_OWN_TESTS)
        self.assertLess(n['all']['definitely_recommend']['value'], 50.0)
        self.assertIn('the all-pilots norm of', N.norm_line('definitely_recommend', 40.0, n, 'pilot', 'all'))
        self.assertEqual(n['choice']['compared_label'], 'All pilots')
        self.assertEqual(n['choice']['notes'], [])
        # a feature cell is a norm of its own format from the first look
        f = N.norms_for('feature', 'all', s3=s3)
        self.assertEqual(f['stand_in'], '')
        self.assertEqual(f['all']['definitely_recommend']['stand_in'], '')

    def test_the_honest_lines_share_one_shape(self):
        self.assertEqual(N.genre_note('pilot', 'procedural', 1), 'No procedural norm yet; it fills as tests are filed under procedural, 1 of 5 so far.')
        self.assertEqual(N.format_note('pilot', 0), 'No pilot norm yet; it fills as pilot tests are filed, 0 of 5 so far.')
        self.assertEqual(N.metric_note('finish', 1), 'No would-finish norm yet; it fills as tests are filed, 1 of 5 so far.')
        self.assertEqual(N.metric_note('tune_in', 7), 'No second-episode norm yet; it fills as tests are filed, 5 of 5 so far.')
        n = N.norms_for('pilot', 'all', s3=FakeS3())
        self.assertEqual(n['pending']['finish'], {'own_tests': 0, 'needed': N.MIN_OWN_TESTS, 'note': 'No would-finish norm yet; it fills as tests are filed, 0 of 5 so far.'})
        self.assertNotIn('definitely_recommend', n['pending'])
        for line in (N.genre_note('pilot', 'drama', 2), N.format_note('episode', 3), N.metric_note('finish', 0)):
            self.assertNotIn('\u2014', line)
            self.assertTrue(line.endswith('so far.'))

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
