#!/usr/bin/env python3
"""The seating tutorial (Jenna 2026-11-23: 'on here next start put a link
that says tutorial and if someone clicks on it have it give a pop up of
this video'): the Build a new twin card carries the Tutorial link, the
pop-up opens and closes the way the house wants, and the tutorial route
serves a signed-in seat and refuses an anonymous one."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('DTIQ_ROOM_HOST', '0')

PAGE = os.path.join(ROOT, 'templates', 'index.html')
JS = os.path.join(ROOT, 'static', 'digital-twins', 'digital-twins.js')
CSS = os.path.join(ROOT, 'static', 'digital-twins', 'digital-twins.css')


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


class Card(unittest.TestCase):
    def test_the_link_sits_beside_start(self):
        html = _read(PAGE)
        card = html[html.index('class="dtw-lib-build"'):html.index('<div id="dtwLibrary"')]
        self.assertIn('Start<i>&rarr;</i></span>', card)
        self.assertIn("dtwTutorialOpen('seating', this)", card)
        self.assertIn('>Tutorial</button>', card)
        self.assertIn('class="dtw-lb link tutorial"', card, 'a link in the card\'s small-caps style, not a second button')
        self.assertIn('event.stopPropagation(); dtwTutorialOpen', card, 'the click never opens the build drawer under it')
        self.assertIn('aria-haspopup="dialog"', card)
        self.assertNotIn('\u2014', card)

    def test_the_pop_up_is_wired(self):
        js = _read(JS)
        for need in ('window.dtwTutorialOpen = function(name, from)', 'window.dtwTutorialClose = function()', "role=\"dialog\" aria-modal=\"true\"",
                     "/api/digital-twins/tutorial/' + encodeURIComponent(name) + '.mp4", "'.jpg'", 'v.play()', 'v.pause()', 'v.currentTime = 0',
                     "var tut = el('dtwTutorial'); if (tut && tut.classList.contains('open')) { dtwTutorialClose(); return; }", "getAttribute('data-tut-scrim')"):
            self.assertIn(need, js, need)
        css = _read(CSS)
        for need in ('.dtw-tutorial.open { display: flex; }', 'aspect-ratio: 16 / 9', 'rgba(12, 22, 24, 0.86)', '@media (max-width: 640px) { .dtw-tutorial'):
            self.assertIn(need, css, need)
        title = js[js.index("var TUTORIALS = { seating:"):js.index('\n', js.index("var TUTORIALS = { seating:"))]
        self.assertNotIn('kind', title.lower())
        self.assertNotIn('\u2014', title)


@unittest.skipUnless(shutil.which('node'), 'node not available')
class Behaviour(unittest.TestCase):
    def test_open_plays_and_close_rewinds(self):
        js = _read(JS)
        a = js.index('            var TUTORIALS = { seating:')
        b = js.index("            document.addEventListener('keydown', function(e) {\n                if (e.key !== 'Escape') return;\n                var tut = el('dtwTutorial')")
        tut = js[a:b]
        node = r'''
var reg = {}, focused = null;
function mk(id, tag) { var n = { id: id || '', tag: tag || 'div', cls: {}, attrs: {}, children: [], listeners: {}, textContent: '', paused: true, currentTime: 7, played: 0, loads: 0,
  classList: { add: function(c) { n.cls[c] = 1; }, remove: function(c) { delete n.cls[c]; }, contains: function(c) { return !!n.cls[c]; } },
  setAttribute: function(k, v) { n.attrs[k] = String(v); if (k === 'id') { n.id = v; reg[v] = n; } }, getAttribute: function(k) { return n.attrs[k] == null ? null : n.attrs[k]; },
  addEventListener: function(t, f) { n.listeners[t] = f; }, appendChild: function(c) { n.children.push(c); if (c.id) reg[c.id] = c; },
  focus: function() { focused = n; }, play: function() { n.played++; n.paused = false; return Promise.resolve(); }, pause: function() { n.paused = true; }, load: function() { n.loads++; } };
  Object.defineProperty(n, 'className', { set: function(v) { v.split(' ').forEach(function(c) { if (c) n.cls[c] = 1; }); } });
  Object.defineProperty(n, 'innerHTML', { set: function(h) { var m, rx = /id="([A-Za-z0-9_]+)"/g; while ((m = rx.exec(h))) { n.appendChild(mk(m[1], 'div')); } } });
  return n; }
var body = mk('body'); global.document = { body: body, activeElement: body, createElement: function(tag) { return mk('', tag); } };
function el(id) { return reg[id] || null; }
global.window = global;
''' + tut + r'''
var link = mk('link'); link.focus();
dtwTutorialOpen('seating', link);
var d = el('dtwTutorial'), v = el('dtwTutorialVideo'), c = el('dtwTutorialClose');
var open = { isOpen: d.classList.contains('open'), aria: d.getAttribute('aria-hidden'), src: v.getAttribute('src'), poster: v.getAttribute('poster'), played: v.played, paused: v.paused, focusOnClose: focused === c, title: el('dtwTutorialTitle').textContent };
dtwTutorialClose();
var closed = { isOpen: d.classList.contains('open'), aria: d.getAttribute('aria-hidden'), paused: v.paused, time: v.currentTime, focusBack: focused === link };
dtwTutorialOpen('seating', link); var again = { sameEl: el('dtwTutorial') === d, loads: v.loads };
d.listeners.click({ target: { getAttribute: function(k) { return k === 'data-tut-scrim' ? '1' : null; } } });
console.log(JSON.stringify({ open: open, closed: closed, again: again, scrimClosed: !d.classList.contains('open') }));
'''
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(node)
            path = fh.name
        try:
            out = subprocess.run(['node', path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got['open'], {'isOpen': True, 'aria': 'false', 'src': '/api/digital-twins/tutorial/seating.mp4', 'poster': '/api/digital-twins/tutorial/seating.jpg',
                                       'played': 1, 'paused': False, 'focusOnClose': True, 'title': 'How to seat your panel'})
        self.assertEqual(got['closed'], {'isOpen': False, 'aria': 'true', 'paused': True, 'time': 0, 'focusBack': True})
        self.assertEqual(got['again'], {'sameEl': True, 'loads': 1}, 'a second open reuses the overlay and never reloads the video')
        self.assertTrue(got['scrimClosed'])


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, op, Params=None, ExpiresIn=0):
        self.calls.append((op, Params, ExpiresIn))
        return f"https://dashboard-inputs.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=test"


class Route(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import flask  # noqa: F401
        except Exception:
            raise unittest.SkipTest('flask not installed')

    def setUp(self):
        import flask
        import digital_twins_api as A
        from prometheus.host import host
        self.s3 = FakeS3()
        self.current = {'user': {'username': 'jenna', 'email': 'jenna@crosswalknyc.com', 'role': 'super_admin', 'has_digital_twins_iq_access': True}}
        fake_app = types.SimpleNamespace(s3_client=self.s3, get_current_user=lambda: self.current['user'], _normalize_role=lambda r: r,
                                         consume_credit=lambda *a, **k: True, load_users=lambda: {'users': {}})
        self._old_app = sys.modules.get('app')
        sys.modules['app'] = fake_app
        host.bind(gate=lambda allow_api_key=False: (self.current['user'], None), s3_client=self.s3)
        app = flask.Flask('t')
        app.secret_key = 'x'
        A.register(app)
        self.c = app.test_client()
        self.A = A

    def tearDown(self):
        if self._old_app is not None:
            sys.modules['app'] = self._old_app
        else:
            sys.modules.pop('app', None)

    def test_signed_in_seat_is_sent_to_the_video(self):
        with self.c.session_transaction() as sess:
            sess['username'] = 'jenna'
        r = self.c.get('/api/digital-twins/tutorial/seating.mp4')
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:200])
        self.assertIn('system/tutorials/Crosswalk_Digital_Twins_Seating_Tutorial.mp4', r.headers['Location'])
        self.assertEqual(r.headers.get('Cache-Control'), 'private, no-store')
        op, params, ttl = self.s3.calls[-1]
        self.assertEqual((op, params['Key'], ttl), ('get_object', 'system/tutorials/Crosswalk_Digital_Twins_Seating_Tutorial.mp4', self.A.TUTORIAL_URL_TTL_S))
        self.assertLessEqual(ttl, 3600, 'short-lived')
        r = self.c.get('/api/digital-twins/tutorial/seating.jpg')
        self.assertEqual(r.status_code, 302)
        self.assertIn('Crosswalk_Digital_Twins_Seating_Tutorial.jpg', r.headers['Location'])

    def test_anonymous_is_refused(self):
        self.current['user'] = None
        r = self.c.get('/api/digital-twins/tutorial/seating.mp4')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.s3.calls, [], 'no URL is signed for nobody')

    def test_unknown_tutorial_is_404(self):
        with self.c.session_transaction() as sess:
            sess['username'] = 'jenna'
        self.assertEqual(self.c.get('/api/digital-twins/tutorial/nothing.mp4').status_code, 404)
        self.assertEqual(self.c.get('/api/digital-twins/tutorial/seating.gif').status_code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
