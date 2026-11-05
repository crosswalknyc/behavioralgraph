#!/usr/bin/env python3
"""Pilot Testing: who they are opens on a click of the seat's photo, never on hover (Jenna, 2026-11-04:
"only have bios pop up if you click on a user's icon not just on hover").

Hermetic: reads templates/index.html and checks the room's handlers as written. The behavior itself (hover
opens nothing, the photo toggles the card, outside click and Escape close it, another photo moves it, a line
on the dial still opens it by the pointer, the popped-out room does the same) is driven in a browser by the
headless harness that shipped with the change; this file keeps the wiring from drifting back.
"""
from __future__ import annotations

import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _fn(page, name):
    """The body of a 12-space-indented function in the Digital Twins IQ script, by name."""
    m = re.search(r'^ {12}function ' + re.escape(name) + r'\(.*?^ {12}\}', page, re.S | re.M)
    assert m, name
    return m.group(0)


class BioOpensOnClick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            cls.page = fh.read()

    def test_hover_follows_the_line_and_opens_nothing(self):
        over = _fn(self.page, 'piSeatRowOver')
        self.assertIn('piHoverSeat(', over)
        self.assertNotIn('piBioShow', over)
        self.assertNotIn('piBioToggle', over)

    def test_the_photo_is_the_one_trigger(self):
        trig = _fn(self.page, 'piBioTrigger')
        self.assertIn("row.classList.contains('remark')", trig)      # a chat row never opens the card
        self.assertIn("row.classList.contains('dtw-pi-chip')", trig)  # a chip in the strip is the photo
        self.assertIn("closest('.circ')", trig)
        click = _fn(self.page, 'piSeatRowClick')
        self.assertIn('piBioTrigger(e.target, row)', click)
        self.assertIn('piBioToggle(row)', click)
        self.assertIn('if (PI_BIO.stuck && !inCard) piBioHide();', click)   # a click anywhere outside the card closes it
        self.assertIn("dtwPilotViewSeat(row.getAttribute('data-seat'))", click)   # the rest of the row still pins

    def test_the_same_photo_again_closes_and_the_card_waits_to_be_closed(self):
        toggle = _fn(self.page, 'piBioToggle')
        self.assertIn('PI_BIO.stuck && PI_BIO.seat === n && PI_BIO.doc === doc && piBioOn(doc)', toggle)
        self.assertIn('PI_BIO.stuck = !!piBioShow(n, doc, { rect: row.getBoundingClientRect() });', toggle)
        release = _fn(self.page, 'piBioRelease')
        self.assertIn('if (PI_BIO.over || PI_BIO.stuck) return;', release)
        hide = _fn(self.page, 'piBioHide')
        self.assertIn('PI_BIO.stuck = false;', hide)

    def test_keyboard_escape_enter_space(self):
        key = _fn(self.page, 'piSeatRowKey')
        self.assertIn("e.key === 'Escape'", key)
        self.assertIn("e.key !== 'Enter' && e.key !== ' '", key)
        self.assertIn('trig !== e.target', key)
        self.assertIn('e.preventDefault(); piBioToggle(row);', key)

    def test_listeners_on_the_page_and_in_the_popped_out_room(self):
        page = self.page
        self.assertEqual(page.count("document.addEventListener('keydown', piSeatRowKey, true);"), 1)
        pop = page[page.find('window.dtwRoomPopOut = function'):page.find('window.dtwRoomPopIn = function')]
        for ev, fn in (('mouseover', 'piSeatRowOver'), ('focusin', 'piSeatRowOver'), ('mouseout', 'piSeatRowOut'),
                       ('focusout', 'piSeatRowOut'), ('click', 'piSeatRowClick'), ('keydown', 'piSeatRowKey')):
            self.assertIn("doc.addEventListener('%s', %s, true);" % (ev, fn), pop)

    def test_dial_line_hover_still_opens_the_card_and_takes_a_clicked_one_over(self):
        dial = _fn(self.page, 'piDialHover')
        self.assertIn('piBioShow(n, document, { x: e.clientX, y: e.clientY, avoid: rect })', dial)
        self.assertIn('var fresh = PI_BIO.stuck || PI_BIO.seat !== n', dial)
        self.assertIn('PI_BIO.stuck = false;', dial)

    def test_markup_photo_is_a_button_only_in_the_room(self):
        row = _fn(self.page, 'piSeatRowHtml')
        self.assertIn('\'<span class="circ"\' + (opts.remark ? \'\' : \' role="button" tabindex="0" aria-label="Who \' + esc(nm) + \' is"\') + \'>\'', row)
        self.assertNotIn('tabindex="0" data-seat', row)   # the row is not the focus stop any more; its photo is
        chip = _fn(self.page, 'piChipHtml')
        self.assertIn('role="button" tabindex="0" data-seat', chip)

    def test_people_hint_and_copy(self):
        page = self.page
        self.assertIn("Hover a line, or click a seat\\'s photo in the room, to see who they are; click a line to pin.", page)
        self.assertNotIn('Hover a line, or a seat in the room, to see who they are; click to pin.', page)
        region = page[page.find('var PI_BIO = '):page.find('window.dtwRoomPopIn = function')]
        self.assertGreater(len(region), 1000)
        self.assertNotIn('\u2014', region)
        self.assertNotIn('\u2013', region)
        css = page[page.find('.dtw-pi-seatrow .circ[role="button"]'):][:400]
        self.assertIn('cursor: pointer', css)


if __name__ == '__main__':
    unittest.main()
