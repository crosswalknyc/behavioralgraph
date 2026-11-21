#!/usr/bin/env python3
"""Jenna 2026-11-20 (verbatim): "dont ever call an archetype kinds you can
always say archetype representative or archetype".

An archetype is called an archetype; the person who stands for one is the
archetype representative. This sweeps the rendered reader surfaces for the
archetype sense of kind / kinds (zero hits) and checks that the word
archetype passes every reader-copy scrub."""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import _index_page  # noqa: E402  (the page with its split files inlined back, 2026-11-20)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('DTIQ_ROOM_HOST', '0')

from migration import digital_twin_archetypes as A  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from test_digital_twin_archetypes import _twin  # noqa: E402

KIND_RX = re.compile(r"\bkinds?\b", re.I)
# the archetype sense of the word, wherever it could sit beside the archetypes
# lower case on purpose: reader copy writes these words in lower case; an identifier such as KINDS.map is code, not copy
KIND_SENSE_RX = re.compile(r"\bkinds? of (?:people|person|subscribers|viewers|fans|buyers|voters|members)\b|\bper kind\b|\beach kind\b|\bkinds?\.|\b\d+ kinds?\b|tap a kind|that kind\b|this kind\b|a kind of person")


def _twin_with_archetypes():
    t = _twin()
    arch = A.derive_archetypes(t)
    t['archetypes'] = arch
    return t


class ReaderSurfaces(unittest.TestCase):
    def setUp(self):
        self.t = _twin_with_archetypes()

    def test_summary_and_lines(self):
        line = A.archetype_summary_line(self.t)
        self.assertIn(' archetypes make up the 1,000,000 ', line)
        self.assertIsNone(KIND_RX.search(line), line)
        for ln in A.archetype_lines(self.t):
            self.assertIsNone(KIND_RX.search(ln), ln)
        for g in A.public_groups(self.t['archetypes']):
            for field in ('label', 'who', 'relationship', 'decides', 'moves_them', 'holds_them_back'):
                self.assertIsNone(KIND_SENSE_RX.search(str(g.get(field) or '')), (field, g.get(field)))

    def test_the_desk_is_told_the_word(self):
        self.assertIsNone(KIND_RX.search(A.ARCHETYPE_SYSTEM.replace('Never call an archetype a kind or a type of person', '')), 'the desk never hears kind for an archetype')
        self.assertIn('archetype representative', A.ARCHETYPE_SYSTEM)
        from migration import digital_twin_guided as GD
        self.assertNotIn('kind of person in the audience', GD.PLAN_SYSTEM)
        self.assertIn('archetype representatives who each stand for an archetype', GD.PLAN_SYSTEM)

    def test_team_notice(self):
        from prometheus import twin_notify as TN
        body = TN._twin_body(self.t, 'digital-twins/admin/x.json.gz', {'name': 'Jenna Menking', 'company': 'Crosswalk', 'email': ''}, {}, 'dashboard')
        self.assertIn('archetypes make up the', body)
        self.assertNotIn('Kind:', body)
        self.assertIsNone(KIND_SENSE_RX.search(body), body)

    def test_exports_page(self):
        from migration import digital_twin_exports as X
        pages = X._pages(self.t)
        pg = next(p for p in pages if str(p.get('header') or '').lower().endswith('archetypes make up this audience.') or 'archetypes make up this audience' in str(p.get('header') or '').lower())
        self.assertEqual(pg['head'][0], 'Archetype')
        self.assertIn('archetype representative', pg['head'][2])
        for p in pages:
            text = ' '.join([str(p.get('header') or ''), str(p.get('sub') or '')] + [str(h) for h in (p.get('head') or ())])
            self.assertIsNone(KIND_SENSE_RX.search(text), text)

    def test_room_csv_headings(self):
        from migration import digital_twin_panel as P
        csv_text = P.session_csv({'id': 's1', 'title': 'Room', 'questions': [], 'group': {'roster': [{'seat': 1, 'name': 'Ava', 'age': 30, 'city': 'Austin', 'segment': 'All', 'archetype': {'label': 'Weeknight regulars'}, 'people': 1200}]}})
        self.assertIn('Archetype', csv_text)
        self.assertIn('Question type', csv_text)
        for line in csv_text.splitlines():
            if line.startswith('Session,'):
                self.assertNotIn('Kind', line, line)

    def test_prometheus_answer_line(self):
        with open(os.path.join(ROOT, 'prometheus', 'digital_twin_lane.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('seats one archetype representative per archetype, each standing for the people of that archetype', src)
        self.assertNotIn('one person per kind', src)

    def test_how_to(self):
        from migration import digital_twins as DT
        doc = DT.how_to()
        text = ' '.join(str(v) for it in (doc.get('items') or []) for v in it.values() if isinstance(v, str)) + ' ' + str(doc.get('intro') or '')
        self.assertIsNone(KIND_SENSE_RX.search(text), KIND_SENSE_RX.search(text))
        self.assertIn('archetype representative', text)

    def test_the_page(self):
        html = _index_page.text(ROOT)   # the page with its split files inlined back
        a = html.index('var DTW_HIDDEN_TABS'); b = html.index('function renderPilotConsole()')
        page = html[a:b]
        # reader strings only: code comments stripped
        page = re.sub(r"//[^\n]*", '', page)
        for need in ("' archetypes.</b>'", "' archetypes' + SEP_TXT", 'The archetypes</span>', "' archetypes make up the '", 'tap an archetype for who they are', 'One archetype representative for each archetype in the audience'):
            self.assertIn(need, page, need)
        hits = [m.group(0) for m in KIND_SENSE_RX.finditer(page)]
        self.assertEqual(hits, [], hits)
        self.assertNotIn('Kind of question', page)


class ArchetypePassesEveryScrub(unittest.TestCase):
    LINE = 'The largest archetype is older regulars; its archetype representative is Victor, 56.'

    def test_scrubs_keep_the_word(self):
        from migration import digital_twin_room as ROOM
        from migration import digital_twin_synthesis as SYN
        from migration import digital_twin_guided as GD
        from migration import digital_twin_report as RP
        from migration import digital_twin_pilot as PI
        from prometheus import twin_notify as TN
        from prometheus import outbound_mail as OM
        self.assertEqual(A.scrub_text(self.LINE), self.LINE)
        self.assertEqual(ROOM.scrub(self.LINE), self.LINE)
        self.assertEqual(ROOM.has_banned(self.LINE), [])
        self.assertEqual(SYN.scrub_text(self.LINE), self.LINE)
        self.assertEqual(GD._scrub(self.LINE), self.LINE)
        self.assertIsNone(GD._BANNED.search(self.LINE))
        self.assertIsNone(GD._DROP_SENTENCE.search(self.LINE))
        self.assertEqual(RP._clean(self.LINE), self.LINE)
        self.assertEqual(PI.has_banned_copy(self.LINE), [])
        self.assertEqual(TN.banned_tokens(self.LINE), [])
        self.assertEqual(OM.banned_tokens(self.LINE), [])

    def test_the_other_words_stay_banned(self):
        from migration import digital_twin_guided as GD
        for w in ('persona', 'segment', 'cohort', 'model', 'synthetic', 'panel'):
            self.assertIsNotNone(A._BANNED_RX.search(f'a {w} here'), w)
            self.assertIsNotNone(GD._BANNED.search(f'a {w} here'), w)


if __name__ == '__main__':
    unittest.main(verbosity=2)
