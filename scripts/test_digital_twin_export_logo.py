#!/usr/bin/env python3
"""The Digital Twins exports carry the real Crosswalk logo (Jenna,
2026-11-05: "use our real logo in the digital twins exports. this is a
bad rendering of it").

Before this change every light page of the deck, the PDF, the Focus
Group IQ session and the pilot report placed ``static/crosswalk-logo-
deck.png``, a 199 by 33 pixel stand-in (three bars and the word in a
bold sans), and the dark pages a 1024 by 129 copy of the white lockup.
Now one helper, ``digital_twin_exports.draw_logo``, places the pack's
own files (``design_skill.logo_path``: the black lockup on Off-White and
on the Signal Green closing page, the white lockup on Graphite Teal) at
the file's own proportions, the same corner and height on every page of
every format. A missing file falls back to the wordmark in Crosswalk
Inter and logs it; the export still comes back.

Hermetic: REGRESSION_TEST_MODE=1, no model, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_export_logo.py
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import design_skill as ds  # noqa: E402
from migration import digital_twin_exports as X  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_exports_parity import _portrait_fn, _session  # noqa: E402

BLACK = ds.logo_path('black')
WHITE = ds.logo_path('white')
TOL = 0.01      # the lockup's aspect, within 1%


def _sha1(path) -> str:
    with open(path, 'rb') as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def _png_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def _page_images(page) -> list:
    """(width, height, PIL image) for every image XObject a PDF page draws."""
    out = []
    for im in page.images:
        pil = im.image
        out.append((pil.size[0], pil.size[1], pil))
    return out


def _ink(pil) -> str:
    """'white' or 'black' from the opaque pixels of a lockup."""
    rgba = pil.convert('RGBA')
    w, h = rgba.size
    opaque = [rgba.getpixel((x, h // 2)) for x in range(0, w, max(1, w // 400))]
    opaque = [p for p in opaque if p[3] > 200]
    assert opaque, 'no opaque pixel on the midline'
    lum = sum(sum(p[:3]) / 3.0 for p in opaque) / len(opaque)
    return 'white' if lum > 200 else 'black' if lum < 60 else f'grey({lum:.0f})'


def _slide_logos(prs) -> list:
    """Per slide: the picture shapes carrying one of the two lockups
    (by the embedded bytes' sha1), with their aspect and the ground."""
    sha_b, sha_w = _sha1(BLACK), _sha1(WHITE)
    out = []
    for sl in prs.slides:
        fill = sl.background.fill
        try:
            ground = 'dark' if fill.type == 1 and str(fill.fore_color.rgb) == X.GRAPHITE[1:] else 'light'
        except Exception:
            ground = 'light'
        logos = []
        for sh in sl.shapes:
            if sh.shape_type != 13:
                continue
            try:
                sha = sh.image.sha1
            except Exception:
                continue
            if sha in (sha_b, sha_w):
                logos.append({'file': 'black' if sha == sha_b else 'white', 'w': sh.width / 914400.0, 'h': sh.height / 914400.0,
                              'left': sh.left / 914400.0, 'top': sh.top / 914400.0})
        out.append({'ground': ground, 'logos': logos})
    return out


class ThePackFiles(unittest.TestCase):
    def test_the_pack_carries_both_lockups_at_one_aspect(self):
        self.assertIsNotNone(BLACK, 'the design pack is not on this machine')
        self.assertIsNotNone(WHITE)
        self.assertTrue(str(BLACK).endswith('crosswalk-logo-black.png') and str(WHITE).endswith('crosswalk-logo-white.png'))
        self.assertNotIn('crosswalk-logo-deck', str(BLACK))
        bw, bh = _png_size(BLACK)
        ww, wh = _png_size(WHITE)
        self.assertAlmostEqual(bw / bh, ww / wh, delta=TOL * bw / bh)
        self.assertGreater(bw, 2000, 'the real artwork, not a thumbnail')
        self.assertEqual(X.logo_file('dark'), WHITE)
        self.assertEqual(X.logo_file('light'), BLACK)
        self.assertEqual(X.logo_file('anything else'), BLACK)


class ThePdfs(unittest.TestCase):
    def _check_pdf(self, pdf: bytes, light_page_present: bool = True):
        import pypdf
        rd = pypdf.PdfReader(io.BytesIO(pdf))
        aspect = _png_size(BLACK)[0] / _png_size(BLACK)[1]
        inks = []
        for i, pg in enumerate(rd.pages, 1):
            lockups = [(w, h, pil) for (w, h, pil) in _page_images(pg) if abs(w / h - aspect) <= TOL * aspect]
            self.assertEqual(len(lockups), 1, f'page {i}: one lockup image, found {len(lockups)}')
            w, h, pil = lockups[0]
            self.assertEqual((w, h), _png_size(BLACK), f'page {i}: the pack file at its own pixel size')
            inks.append(_ink(pil))
        self.assertEqual(inks[0], 'white', 'the cover is dark: the white lockup')
        self.assertIn('black', inks[1:] if light_page_present else inks, 'a light page carries the black lockup')
        self.assertTrue(set(inks) <= {'white', 'black'}, inks)
        return inks

    def test_the_pilot_report_pdf_embeds_the_lockup_on_every_page(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        pdf = R.build_report_pdf(payload, sess, portrait_fn=_portrait_fn)
        inks = self._check_pdf(pdf)
        pages = R.report_pages(payload, sess, portrait_fn=_portrait_fn)
        qi = [i for i, p in enumerate(pages) if p['kind'] == 'question']
        self.assertTrue(qi, 'the report has question pages')
        # the dark ground gets the white file, the light ground the black file, page by page
        for i, p in enumerate(pages):
            want = 'white' if (i == 0 or p.get('dark')) else 'black'
            self.assertEqual(inks[i], want, f"page {i + 1} ({p['kind']}, {'dark' if p.get('dark') else 'light'})")

    def test_the_room_pdf_embeds_the_lockup_on_the_cover_and_the_question_pages(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        pdf = X.build_panel_pdf(payload, sess, portrait_fn=_portrait_fn)
        import pypdf
        n = len(pypdf.PdfReader(io.BytesIO(pdf)).pages)
        self.assertGreaterEqual(n, 1 + len(sess['questions']), 'the room page and one page per question')
        inks = self._check_pdf(pdf)
        self.assertEqual(len(inks), n)

    def test_the_twin_pdf_embeds_the_lockup(self):
        twin, _ = _session()
        inks = self._check_pdf(X.build_pdf({'meta': twin['meta']}))
        self.assertGreaterEqual(len(inks), 5, 'the cover, the TLDR, who they are, the dials, what to do next')

    def test_one_xobject_per_lockup_not_one_per_page(self):
        import pypdf
        twin, sess = _session()
        pdf = R.build_report_pdf({'meta': twin['meta']}, sess, portrait_fn=_portrait_fn)
        rd = pypdf.PdfReader(io.BytesIO(pdf))
        names = set()
        for pg in rd.pages:
            xo = pg['/Resources'].get('/XObject') or {}
            for k, v in xo.items():
                o = v.get_object()
                if o.get('/Subtype') == '/Image' and (o['/Width'], o['/Height']) == _png_size(BLACK):
                    names.add(k)
        self.assertEqual(len(names), 2, f'the white and the black lockup are each written once and shared: {sorted(names)}')


class TheDecks(unittest.TestCase):
    def _check_deck(self, deck: bytes):
        from pptx import Presentation
        prs = Presentation(io.BytesIO(deck))
        aspect = _png_size(BLACK)[0] / _png_size(BLACK)[1]
        found = _slide_logos(prs)
        self.assertTrue(found)
        for i, s in enumerate(found, 1):
            self.assertEqual(len(s['logos']), 1, f'slide {i}: one lockup picture')
            lg = s['logos'][0]
            self.assertAlmostEqual(lg['w'] / lg['h'], aspect, delta=TOL * aspect, msg=f'slide {i}: the picture keeps the file aspect')
            self.assertAlmostEqual(lg['h'], X.LOGO_HEIGHT, places=3)
            self.assertAlmostEqual(lg['left'], 0.92, places=3)
            self.assertAlmostEqual(lg['top'], X.LOGO_TOP, places=3)
            self.assertEqual(lg['file'], 'white' if s['ground'] == 'dark' else 'black', f"slide {i}: the {s['ground']} ground")
        return found

    def test_the_pilot_report_deck_places_the_lockup_on_every_slide(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        found = self._check_deck(R.build_report_deck(payload, sess, portrait_fn=_portrait_fn))
        self.assertEqual(found[0]['ground'], 'dark', 'the cover')
        self.assertIn('light', {s['ground'] for s in found})
        pages = R.report_pages(payload, sess, portrait_fn=_portrait_fn)
        self.assertEqual(len(found), len(pages))
        # the closing page is the Signal Green gradient page: a light ground, the black lockup
        self.assertEqual(found[-1]['logos'][0]['file'], 'black')

    def test_the_twin_deck_places_the_lockup(self):
        twin, _ = _session()
        found = self._check_deck(X.build_deck({'meta': twin['meta']}))
        self.assertGreaterEqual(len(found), 5)
        self.assertEqual([s['ground'] for s in found[:3]], ['dark', 'dark', 'light'])


class TheHelper(unittest.TestCase):
    def test_a_canvas_gets_the_file_at_its_own_proportions(self):
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=landscape(letter))
        self.assertEqual(X.draw_logo(c, 0.92, 0.45, 0.26, ground='dark'), WHITE)
        self.assertEqual(X.draw_logo(c, 0.92, 0.45, 0.26, ground='light'), BLACK)
        c.save()
        import pypdf
        pg = pypdf.PdfReader(io.BytesIO(buf.getvalue())).pages[0]
        sizes = {(w, h) for (w, h, _) in _page_images(pg)}
        self.assertEqual(sizes, {_png_size(BLACK)})

    def test_a_slide_gets_the_file_at_its_own_proportions(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        sl = prs.slides.add_slide(prs.slide_layouts[6])
        self.assertEqual(X.draw_logo(sl, 0.92, 0.45, 0.26, ground='light'), BLACK)
        pics = [sh for sh in sl.shapes if sh.shape_type == 13]
        self.assertEqual(len(pics), 1)
        w, h = pics[0].width / 914400.0, pics[0].height / 914400.0
        bw, bh = _png_size(BLACK)
        self.assertAlmostEqual(w / h, bw / bh, delta=TOL * bw / bh)
        self.assertAlmostEqual(h, 0.26, places=3)

    def test_a_missing_file_falls_back_to_the_wordmark_and_logs(self):
        from pptx import Presentation
        from pptx.util import Inches
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.pdfgen import canvas
        orig = ds.logo_path
        ds.logo_path = lambda variant='white': None
        try:
            prs = Presentation()
            prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
            sl = prs.slides.add_slide(prs.slide_layouts[6])
            with self.assertLogs('migration.digital_twin_exports', level='WARNING') as logs:
                self.assertIsNone(X.draw_logo(sl, 0.92, 0.45, 0.26, ground='dark'))
            self.assertTrue(any('wordmark' in m for m in logs.output), logs.output)
            self.assertEqual([sh for sh in sl.shapes if sh.shape_type == 13], [], 'no picture')
            tb = [sh for sh in sl.shapes if sh.has_text_frame]
            self.assertEqual(len(tb), 1)
            run = tb[0].text_frame.paragraphs[0].runs[0]
            self.assertEqual(run.text, 'Crosswalk')
            self.assertEqual(run.font.name, X._face('Bold'))
            self.assertTrue(run.font.bold)
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=landscape(letter))
            with self.assertLogs('migration.digital_twin_exports', level='WARNING'):
                self.assertIsNone(X.draw_logo(c, 0.92, 0.45, 0.26, ground='light'))
            c.save()
            import pypdf
            pg = pypdf.PdfReader(io.BytesIO(buf.getvalue())).pages[0]
            self.assertEqual(_page_images(pg), [], 'no image')
            self.assertIn('Crosswalk', pg.extract_text() or '')
            # a whole export still comes back
            twin, sess = _session()
            self.assertTrue(X.build_panel_pdf({'meta': twin['meta']}, sess)[:4] == b'%PDF')
        finally:
            ds.logo_path = orig

    def test_an_unknown_target_never_raises(self):
        with self.assertLogs('migration.digital_twin_exports', level='WARNING'):
            self.assertIsNone(X.draw_logo(object(), 0, 0, 0.26, ground='dark'))


class TheSource(unittest.TestCase):
    """The stand-in code path is gone from the builders, and every logo
    placement goes through the one helper."""

    def _src(self, name: str) -> str:
        with open(os.path.join(ROOT, 'migration', name), encoding='utf-8') as fh:
            return fh.read()

    def test_no_stand_in_file_or_drawn_bars_remain(self):
        for name in ('digital_twin_exports.py', 'digital_twin_report.py'):
            src = self._src(name)
            body = re.sub(r'"""[\s\S]*?"""', '', src)        # the docstrings may name the retired file to say it is gone
            for tok in ('crosswalk-logo-deck', 'crosswalk-logo-white-transparent', '_LOGO_WHITE', '_LOGO_BLACK', "'static' / 'crosswalk-logo", 'static/crosswalk-logo'):
                self.assertNotIn(tok, body, f'{name}: {tok}')
            # no bars drawn for a logo: no polygon / freeform / parallelogram next to the word
            for m in re.finditer(r'(?i)(polygon|freeform|parallelogram|skew)', body):
                ctx = body[max(0, m.start() - 200):m.end() + 200].lower()
                self.assertNotIn('logo', ctx, f'{name}: a drawn logo near {m.group(0)!r}')
                self.assertNotIn('crosswalk', ctx, f'{name}: a drawn wordmark near {m.group(0)!r}')

    def test_every_placement_goes_through_draw_logo(self):
        exports = re.sub(r'"""[\s\S]*?"""', '', self._src('digital_twin_exports.py'))
        # logo_path is read in one place (the helper's file lookup)
        self.assertEqual(exports.count('logo_path('), 1, 'the pack lookup lives in logo_file only')
        # the deck loop and the PDF page both call the helper
        self.assertGreaterEqual(exports.count('draw_logo('), 3, 'the definition, the deck, the PDF page')
        self.assertIn("draw_logo(slide, L, LOGO_TOP, LOGO_HEIGHT, ground='dark' if dark else 'light')", exports)
        self.assertIn("draw_logo(c, self.m / 72.0, LOGO_TOP, LOGO_HEIGHT, ground='dark' if dark else 'light')", exports)
        # no other add_picture / drawImage places a logo
        for m in re.finditer(r'(add_picture|drawImage)\(', exports):
            line = exports[exports.rfind('\n', 0, m.start()) + 1:exports.find('\n', m.end())]
            if 'reader' in line or 'str(path)' in line:
                continue        # the helper itself
            self.assertNotIn('logo', line.lower(), line)
        report = re.sub(r'"""[\s\S]*?"""', '', self._src('digital_twin_report.py'))
        self.assertNotIn('logo_path', report)
        self.assertNotIn('add_picture', report, 'the report draws through build_deck')
        for m in re.finditer(r'drawImage\(', report):
            line = report[report.rfind('\n', 0, m.start()) + 1:report.find('\n', m.end())]
            self.assertNotIn('logo', line.lower(), line)


if __name__ == '__main__':
    unittest.main(verbosity=2)
