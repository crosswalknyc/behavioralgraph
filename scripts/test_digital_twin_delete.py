#!/usr/bin/env python3
"""Delete a twin from the wall (2026-10-28, Jenna). Hermetic FakeS3."""
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

from migration import digital_twin_synthesis as SYN  # noqa: E402
from migration import digital_twins as DT  # noqa: E402


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)


KEY = 'digital-twins/jenna/Jennifer_Beals_20261028_120000_abc123def0.json.gz'


def _payload(name='Jennifer Beals', created_by='jenna'):
    return {
        'meta': {'name': name, 'subject': name, 'created_by': created_by, 'size_us': 9339205, 'key': KEY},
        'panel_sessions': [{'id': 'r1', 'title': 'Season 2'}],
        'runs': [],
    }


def _seed(s3, payload=None, key=KEY):
    SYN.save_twin(s3, key, payload or _payload())
    return s3


class WhoMay(unittest.TestCase):
    def test_creator_can(self):
        self.assertTrue(DT.can_delete_twin(_payload(), 'jenna', False))

    def test_other_user_cannot(self):
        self.assertFalse(DT.can_delete_twin(_payload(), 'other', False))

    def test_admin_can(self):
        admin = {'username': 'jessie', 'role': 'admin'}
        self.assertTrue(DT.can_delete_twin(_payload(), 'jessie', DT.is_admin(admin)))

    def test_super_admin_can(self):
        sa = {'username': 'jenna', 'role': 'super_admin'}
        self.assertTrue(DT.can_delete_twin(_payload(created_by='other'), 'jenna', DT.is_admin(sa)))


class Delete(unittest.TestCase):
    def test_index_drops_the_key(self):
        s3 = _seed(FakeS3())
        self.assertTrue(any(r.get('key') == KEY for r in DT.list_runs(s3)))
        got = DT.delete_twin(s3, KEY, deleted_by='jenna')
        self.assertIsNotNone(got)
        self.assertEqual(got['deleted'], KEY)
        self.assertNotIn(KEY, s3.objs)
        self.assertFalse(any(r.get('key') == KEY for r in DT.list_runs(s3)))

    def test_deleted_copy_exists(self):
        s3 = _seed(FakeS3())
        DT.delete_twin(s3, KEY, deleted_by='jenna')
        undo = DT.deleted_twin_key(KEY)
        side = DT.deleted_twin_sidecar_key(KEY)
        self.assertIn(undo, s3.objs)
        self.assertIn(side, s3.objs)
        meta = json.loads(s3.objs[side].decode('utf-8'))
        self.assertEqual(meta['original_key'], KEY)
        self.assertEqual(meta['deleted_by'], 'jenna')
        self.assertIn('undo_until', meta)
        self.assertTrue(any(k.startswith('_backups/') and 'pre_twin_delete_' in k for k in s3.objs))

    def test_users_json_explicit_list_drops_star_untouched(self):
        s3 = _seed(FakeS3())
        users = {'users': {
            'jenna': {'allowed_digital_twin_runs': [KEY, 'digital-twins/keep/other.json.gz']},
            'star': {'allowed_digital_twin_runs': ['*']},
            'empty': {},
        }}
        star = list(users['users']['star']['allowed_digital_twin_runs'])
        DT.delete_twin(s3, KEY, deleted_by='jenna', users_doc=users)
        self.assertEqual(users['users']['jenna']['allowed_digital_twin_runs'],
                         ['digital-twins/keep/other.json.gz'])
        self.assertEqual(users['users']['star']['allowed_digital_twin_runs'], star)
        self.assertNotIn(DT.RUNS_FIELD, users['users']['empty'])

    def test_never_raises(self):
        self.assertIsNone(DT.delete_twin(None, ''))
        self.assertIsNone(DT.delete_twin(FakeS3(), KEY, payload=None))


class Page(unittest.TestCase):
    def test_wall_and_page_have_delete_control(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('window.dtwTwinDelete', page)
        self.assertIn('window.dtwTwinDeleteAsk', page)
        self.assertIn('The twin and its rooms leave the library', page)
        self.assertIn('You can undo for 30 days', page)
        self.assertIn('twinDeleteChrome', page)
        self.assertIn('dtwTwinDelP-', page)
        self.assertIn('dtwCoverOpen', page)
        build = page[page.find('dtw-cover build'):page.find('dtw-cover build') + 400]
        self.assertIn('dtwBuildTwin()', build)
        self.assertNotIn('dtwTwinDelete', build)
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('def dt_delete(', api)
        self.assertIn("methods=['DELETE']", api)
        self.assertIn('/<path:key>/delete', api)

    def test_how_to_mentions_delete(self):
        doc = DT.how_to()
        twin = next(it for it in doc['items'] if it['id'] == 'twin')
        blob = (twin['comes_back'] + ' ' + twin['export'] + ' ' + doc['where']).lower()
        self.assertIn('delete a twin from the twins tab', blob)
        self.assertIn('30 days', blob)


class PilotDeleteRestore(unittest.TestCase):
    """A pilot test deleted from the list comes back from its 30-day copy
    (Jenna, 2026-11-03: "allow a way to delete pilot tests")."""

    def _s3(self):
        s3 = FakeS3()
        s3.copy_object = lambda Bucket, CopySource, Key, **kw: s3.objs.__setitem__(Key, s3.objs[CopySource['Key']])
        return s3

    def test_delete_then_restore(self):
        from migration import digital_twin_pilot as PI
        s3 = self._s3()
        s3.objs['pilot-media/jenna/p1/cut.mp4'] = b'video'
        payload = {'meta': {'name': 'Paramount+', 'key': KEY, 'size_us': 1000}, 'panel_sessions': [],
                   'pilot_sessions': [{'id': 'p1', 'title': 'Season 2 pilot', 'created_by': 'jenna', 'status': 'stopped',
                                       'media': {'s3_key': 'pilot-media/jenna/p1/cut.mp4', 'filename': 'cut.mp4', 'title': 'x'}},
                                      {'id': 'p2', 'title': 'Other', 'created_by': 'jenna', 'status': 'ready', 'media': {}}]}
        gone = PI.delete_session(s3, KEY, payload, 'p1', deleted_by='jenna')
        self.assertEqual(gone['id'], 'p1')
        self.assertEqual([x['id'] for x in payload['pilot_sessions']], ['p2'])
        undo_key = PI.deleted_pilot_key(KEY, 'p1')
        self.assertIn(undo_key, s3.objs)
        self.assertNotIn('pilot-media/jenna/p1/cut.mp4', s3.objs)           # the file followed the test into the undo copy
        back = PI.restore_session(s3, KEY, payload, 'p1', restored_by='jenna')
        self.assertEqual(back['id'], 'p1')
        self.assertEqual([x['id'] for x in payload['pilot_sessions']], ['p1', 'p2'])
        self.assertTrue(back['media']['s3_key'].endswith('/cut.mp4'))
        self.assertIn(back['media']['s3_key'], s3.objs)                    # and came back with it
        self.assertNotIn(undo_key, s3.objs)
        self.assertIsNone(PI.restore_session(s3, KEY, payload, 'p1'))        # nothing left to restore
        self.assertIsNone(PI.restore_session(s3, KEY, payload, 'nope'))

    def test_deleted_test_leaves_the_norms_and_a_restore_brings_it_back(self):
        from migration import digital_twin_pilot as PI
        from migration import digital_twin_norms as N
        s3 = self._s3()
        N.seed(s3)
        k = N.cell_key('pilot', 'all', 'total_positive')
        sess = {'id': 'p9', 'title': 'Season 2 pilot', 'created_by': 'jenna', 'status': 'stopped', 'genre': 'drama', 'media': {},
                'scorecard': {'format': 'pilot', 'battery': {'headline': {'total_positive_pct': 80.0, 'finish_pct': 92.0}}}}
        payload = {'meta': {'name': 'Paramount+', 'key': KEY, 'size_us': 1000}, 'panel_sessions': [], 'pilot_sessions': [sess]}
        PI.record_norms(sess, s3)
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        self.assertEqual(doc['cells'][k]['tests'], ['p9'])
        self.assertAlmostEqual(doc['cells'][k]['value'], (65.0 * N.SEED_WEIGHT + 80.0) / (N.SEED_WEIGHT + 1), places=3)
        self.assertIn(N.cell_key('pilot', 'all', 'finish'), doc['cells'])
        PI.delete_session(s3, KEY, payload, 'p9', deleted_by='jenna')
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        self.assertEqual((doc['cells'][k]['value'], doc['cells'][k]['tests'], doc['cells'][k]['seeded']), (65.0, [], True))   # back on the anchor
        self.assertNotIn(N.cell_key('pilot', 'all', 'finish'), doc['cells'])    # no anchor, no tests: gone
        self.assertNotIn(N.cell_key('pilot', 'drama', 'finish'), doc['cells'])
        back = PI.restore_session(s3, KEY, payload, 'p9', restored_by='jenna')
        self.assertEqual(back['id'], 'p9')
        doc = json.loads(s3.objs[N.NORMS_KEY].decode('utf-8'))
        self.assertEqual(doc['cells'][k]['tests'], ['p9'])                        # the restored test counts again
        self.assertIn(N.cell_key('pilot', 'all', 'finish'), doc['cells'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
