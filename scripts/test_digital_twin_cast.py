#!/usr/bin/env python3
"""The cast on screen, by face (migration/digital_twin_cast).

Jenna, 2026-11-02 (NCIS: New York pilot): "it needs to realize the woman
in the tan blazer is jennifer beals and the two men by the bridge are ll
cool j and scott kahn. i input the cast so it should have checked their
IMDB or something to find out what they look like and figured it out by
facial recognition."

Hermetic: REGRESSION_TEST_MODE, a fake model for the name resolution,
a stand-in for the face read. The live face read is exercised by hand
on the build server (scripts/check_cast_faces_live.py).

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_cast.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

os.environ['REGRESSION_TEST_MODE'] = '1'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import digital_twin_cast as C        # noqa: E402
from migration import digital_twin_watch as W       # noqa: E402
from migration import digital_twin_pilot as PI      # noqa: E402
from migration.digital_twin_room import Models      # noqa: E402


class Names(unittest.TestCase):
    def test_parse_cast_shapes(self):
        people = C.parse_cast(['LL Cool J as Sam Hanna', 'Jennifer Beals (Lena)', 'Scott Caan - Danny', ' Scott Cahn ', 'LL Cool J', ''])
        self.assertEqual([p['actor'] for p in people], ['LL Cool J', 'Jennifer Beals', 'Scott Caan', 'Scott Cahn'])
        self.assertEqual([p['character'] for p in people], ['Sam Hanna', 'Lena', 'Danny', ''])

    def test_same_person(self):
        self.assertTrue(C.same_person('Scott Cahn', 'Scott Caan'))
        self.assertTrue(C.same_person('Jennifer Beals', 'jennifer beals'))
        self.assertTrue(C.same_person('Michael B. Jordan', 'Michael Jordan'))
        self.assertFalse(C.same_person('Jennifer Beals', 'Jennifer Hudson'))
        self.assertFalse(C.same_person('Scott Caan', 'James Caan'))
        self.assertTrue(C._initials_match('LL Cool J', 'L.L. Cool J'))

    def test_resolve_names_through_the_model(self):
        def fake(role, system, user, **kw):
            self.assertEqual(role, 'cast_names')
            body = json.loads(user)
            self.assertEqual(body['title'], 'NCIS: New York Pilot')
            return {'cast': [{'entered': 'LL Cool J', 'actor': 'LL Cool J', 'character': 'Sam Hanna', 'wikipedia': 'LL Cool J'},
                             {'entered': 'Scott Cahn', 'actor': 'Scott Caan', 'character': '', 'wikipedia': 'Scott Caan'},
                             {'entered': 'Jennifer Beals', 'actor': 'Jennifer Hudson', 'character': '', 'wikipedia': ''}]}
        people = C.parse_cast(['LL Cool J', 'Scott Cahn', 'Jennifer Beals'])
        out = C.resolve_names(people, 'NCIS: New York Pilot', models=Models(model_fn=fake))
        self.assertEqual(out[1]['actor'], 'Scott Caan')          # the misspelling resolved
        self.assertEqual(out[1]['wikipedia'], 'Scott Caan')
        self.assertEqual(out[0]['character'], 'Sam Hanna')
        self.assertEqual(out[2]['actor'], 'Jennifer Beals')      # a different person from the model is ignored

    def test_regression_mode_keeps_the_typed_names(self):
        people = C.resolve_names(C.parse_cast(['Scott Cahn']), 'NCIS')
        self.assertEqual(people[0]['actor'], 'Scott Cahn')
        self.assertEqual(C.people_for({'cast': ['Scott Cahn']}, title='NCIS'), [])   # no faces are read in tests
        self.assertEqual(C.faces_in_frame(b'x' * 300, [{'actor': 'Scott Caan', 'celebrity': 'Scott Caan'}]), [])


class Words(unittest.TestCase):
    def test_on_screen_line(self):
        hits = [{'actor': 'Jennifer Beals', 'character': '', 'pos': 'center', 'confidence': 99.1, 'box': {'left': 0.4, 'top': 0.1, 'width': 0.2, 'height': 0.3}},
                {'actor': 'Scott Caan', 'character': 'Danny', 'pos': 'right', 'confidence': 91.0, 'box': {'left': 0.7, 'top': 0.1, 'width': 0.15, 'height': 0.3}}]
        self.assertEqual(C.on_screen_line(hits), 'On screen, confirmed by face: Jennifer Beals (center), Scott Caan as Danny (right).')
        self.assertEqual(C.names(hits), ['Jennifer Beals', 'Scott Caan'])
        self.assertIn('No cast member is recognizable', C.on_screen_line([]))

    def test_dedupe_keeps_the_stronger_match_per_face(self):
        box = {'left': 0.4, 'top': 0.1, 'width': 0.2, 'height': 0.3}
        hits = [{'actor': 'Scott Caan', 'confidence': 88.0, 'box': box, 'pos': 'center'},
                {'actor': 'LL Cool J', 'confidence': 99.5, 'box': dict(box, left=0.41), 'pos': 'center'},
                {'actor': 'LL Cool J', 'confidence': 95.0, 'box': {'left': 0.05, 'top': 0.1, 'width': 0.2, 'height': 0.3}, 'pos': 'left'}]
        out = C._dedupe(hits)
        self.assertEqual([h['actor'] for h in out], ['LL Cool J'])
        self.assertEqual(out[0]['confidence'], 99.5)


class Captions(unittest.TestCase):
    def test_caption_frames_names_the_cast_the_faces_give(self):
        tmp = tempfile.mkdtemp()
        frames = []
        for i in range(3):
            path = os.path.join(tmp, f'f{i}.jpg')
            with open(path, 'wb') as fh:
                fh.write(b'\xff\xd8' + bytes([i]) * 400)
            frames.append({'scene_id': i, 't': 10.0 * i, 'path': path})
        people = [{'actor': 'Jennifer Beals', 'character': '', 'celebrity': 'Jennifer Beals', 'has_headshot': True},
                  {'actor': 'Scott Caan', 'character': '', 'celebrity': '', 'has_headshot': True}]
        seen_lines = []

        def faces(raw, ppl):
            if raw[2] == 1:
                return [{'actor': 'Jennifer Beals', 'character': '', 'pos': 'center', 'confidence': 99.0, 'box': {'left': 0.4, 'top': 0.1, 'width': 0.2, 'height': 0.3}},
                        {'actor': 'Scott Caan', 'character': '', 'pos': 'right', 'confidence': 92.0, 'box': {'left': 0.7, 'top': 0.1, 'width': 0.15, 'height': 0.3}}]
            return []

        def fake(role, system, user, **kw):
            body = json.loads(user)
            seen_lines.extend(body['frames'])
            rows = []
            for k, line in enumerate(body['frames'], 1):
                cap = 'Jennifer Beals stands over Scott Caan at his desk' if 'Jennifer Beals' in line else 'A man in a dark suit sits at a bar'
                rows.append({'k': k, 'caption': cap, 'tone': 'tense', 'joke': False, 'setting': 'ops room', 'people': 2, 'text_on_screen': ''})
            return {'frames': rows, 'genre_guess': 'drama', 'format_guess': 'pilot'}
        out = W.caption_frames(frames, models=Models(model_fn=fake), title='NCIS: New York Pilot', cast=people, faces_fn=faces, batch=6, workers=1)
        self.assertEqual(len(out['captions']), 3)
        self.assertIn('On screen, confirmed by face: Jennifer Beals (center), Scott Caan (right).', seen_lines[1])
        self.assertIn('No cast member is recognizable', seen_lines[0])
        self.assertEqual(out['captions'][1]['cast'], ['Jennifer Beals', 'Scott Caan'])
        self.assertEqual(out['captions'][0]['cast'], [])
        self.assertIn('Jennifer Beals', out['captions'][1]['caption'])
        self.assertIn('confirmed by face', W.CAPTION_SYSTEM)
        self.assertNotIn("never a real actor's name", W.CAPTION_SYSTEM)

    def test_scenes_carry_the_cast(self):
        caps = {0: {'caption': 'Jennifer Beals in a beige blazer leans over the analyst', 'tone': 'tense', 'cast': ['Jennifer Beals']},
                1: {'caption': 'Two men under the bridge', 'tone': 'quiet', 'cast': ['LL Cool J', 'Scott Caan']}}
        rows = PI.build_scenes('NCIS', 60.0, 'm', scenes=[{'id': 0, 't_start': 0, 't_end': 30}, {'id': 1, 't_start': 30, 't_end': 60}], captions=caps)
        self.assertEqual(rows[0]['cast'], ['Jennifer Beals'])
        self.assertEqual(rows[1]['cast'], ['LL Cool J', 'Scott Caan'])
        srows = W._scene_rows(rows)
        self.assertEqual(srows[1]['cast_on_screen'], ['LL Cool J', 'Scott Caan'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
