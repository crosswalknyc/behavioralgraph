#!/usr/bin/env python3
"""The Focus Group IQ export: every question page's header is the
question itself (Jenna, 2026-11-05: "on the export for focus group the
header on each page should just be the question doesnt need to come up
with something like this").

Before this change the page header was the first twelve words of the
room's read ('Two days after an election in which Democrats took both
chambers and.') with 'Question 5: Did influencer content on social media
sway your vote?' under it, and the seat quotes ran under the footer and
could stop mid-word ('not someb'). Now the eyebrow is 'Question N', the
header is the question as asked (whole, wrapped, never shortened), the
lead answer with its share and people sits under it, and every quote is
measured: one that does not fit what is left of the page moves whole,
with who said it, to 'Question N, continued'; one longer than a page
ends at the last complete sentence that fits. A line the room cut at its
character cap ends at its last complete sentence or word. The pilot
report's question pages (deck and PDF, one page list) follow the same
rule, so the two formats agree.

Hermetic: REGRESSION_TEST_MODE=1, no model, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_room_export_headers.py
"""
from __future__ import annotations

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

from migration import digital_twin_exports as X  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402

sys.path.insert(0, HERE)

ASKED = [
    'How often do you see political content from influencers you follow?',
    'Which platform carried most of what you saw about the election?',
    'When an influencer you like endorses a candidate, what do you do with that?',
    'For the people who said they trust creators over anchors, what is it about the creator, the delivery, the fact that they are not paid by a network, or something else?',
    'Did influencer content on social media sway your vote?',
]
READS = [
    'Most of the room sees it weekly, and the ones under 30 see it daily.',
    'TikTok carried it for the young seats and Facebook for the older ones.',
    'The room mostly keeps scrolling; a quarter looks the candidate up.',
    'Two days after an election in which Democrats took both chambers and the room was still sorting out who it believed, the creators won on delivery.',
    'Two days after an election in which Democrats took both chambers and the room had cooled, a third said yes and the rest said no with some heat.',
]
SEATS = [(1, 'Yara', 42, 'Kansas City'), (2, 'Jamal', 29, 'Houston'), (3, 'Isaiah', 35, 'Dallas'), (4, 'Ethan', 51, 'El Paso')]

LONG = ('I do not think a creator changed my mind on the ballot itself, but the creators I follow are the reason I knew what was on it in the first place, '
        'and that is a different kind of influence than people mean when they ask the question. ' * 6)     # about nine hundred characters, every sentence whole
SENT = 'I scroll past most of it and I do not think twice about who paid for the post. '
TAIL = 'The ones I trust are people I already follow, not somebody a brand paid to hold up a water bottle for ten seconds'
CUT = ((SENT * 6) + TAIL)[:X.QUOTE_CAP]     # the room keeps a line to its cap: this one stops mid-word, the way 'not someb' did


def _session():
    roster = [{'seat': s, 'name': n, 'age': a, 'city': c, 'segment': 'Voters'} for s, n, a, c in SEATS]
    qs = []
    for i, (q, read) in enumerate(zip(ASKED, READS)):
        dist = [{'label': 'Yes', 'pct': 34.6, 'people': 1_318_410}, {'label': 'No', 'pct': 52.1, 'people': 1_985_124}, {'label': 'Not sure', 'pct': 13.3, 'people': 506_773}]
        if i == 3:     # the long question: eight answers and four long quotes, so the voices overflow the page
            dist = [{'label': f"Answer {k + 1} to a long question about creators", 'pct': round(30.0 - 3.1 * k, 1), 'people': 900_000 - 70_000 * k} for k in range(8)]
        voices = []
        for s, n, a, c in SEATS:
            line = LONG if i == 3 else (CUT if (i == 4 and s == 2) else f"{n} says what {n} says about question {i + 1}.")
            voices.append({'seat': s, 'name': n, 'age': a, 'city': c, 'segment': 'Voters', 'line': line})
        qs.append({'q': q, 'read': read, 'distribution': dist, 'voices': voices})
    return {'id': 'fg01', 'title': 'Good Influence voters', 'created_by': 'admin',
            'group': {'in_room': 4, 'size_people': 3_810_307, 'segments': [{'label': 'Voters', 'share_pct': 100.0, 'people': 3_810_307, 'seats': 4}], 'roster': roster},
            'questions': qs}


def _pdf_pages(pdf: bytes) -> list:
    import pypdf
    return [' '.join((p.extract_text() or '').split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]


def _slides(deck: bytes) -> list:
    from pptx import Presentation
    return [' '.join(' '.join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame).split()) for sl in Presentation(io.BytesIO(deck)).slides]


def _quotes(page_text: str) -> list:
    """What each seat said on the page, by the attribution that opens it
    (the footer line is drawn before the body, so it is dropped, not split on)."""
    body = page_text.replace('CROSSWALK / BEHAVIORAL INTELLIGENCE ENGINE', ' ')
    parts = re.split(r'(?=(?:Yara|Jamal|Isaiah|Ethan), \d+, [A-Za-z ]+: )', body)
    return [p.strip() for p in parts[1:] if p.strip()]


class Helpers(unittest.TestCase):
    def test_question_header_is_the_question_as_asked(self):
        self.assertEqual(X.question_header('Did influencer content on social media sway your vote?'), 'Did influencer content on social media sway your vote?')
        self.assertEqual(X.question_header('  did influencer   content sway your vote?  '), 'Did influencer content sway your vote?', 'whitespace collapsed, the first letter capitalized, the rest verbatim')
        self.assertEqual(X.question_header('Would you tell a friend'), 'Would you tell a friend?', 'a question typed with no end mark gets one')
        self.assertEqual(X.question_header('Tell me about the last ad you noticed'), 'Tell me about the last ad you noticed.', 'a prompt gets a full stop')
        self.assertEqual(X.question_header(ASKED[3]), ASKED[3], 'a long question is never shortened')
        self.assertGreater(len(ASKED[3].split()), 12)
        self.assertEqual(X.question_header(''), 'What the room was asked.')

    def test_lead_line_names_the_lead_answer_share_and_people(self):
        dist = [{'label': 'Yes', 'pct': 34.6, 'people': 1_318_410}, {'label': 'No', 'pct': 52.1, 'people': 1_985_124}]
        self.assertEqual(X.question_lead_line(dist), 'Lead answer: No, 52.1% of the room, 1,985,124 people.')
        self.assertEqual(X.question_lead_line(dist, people_fmt=lambda n: f"{n / 1e6:.2f}M"), 'Lead answer: No, 52.1% of the room, 1.99M people.')
        self.assertEqual(X.question_lead_line([]), '')
        self.assertEqual(X.question_lead_line([{'label': 'Yes', 'pct': 100}]), 'Lead answer: Yes, 100.0% of the room.')

    def test_clean_quote_never_ends_mid_word(self):
        self.assertEqual(X.clean_quote('  That is where I land.  '), 'That is where I land.')
        self.assertEqual(X.clean_quote('That is where I land'), 'That is where I land.', 'a short line with no end mark gets one')
        self.assertEqual(len(CUT), X.QUOTE_CAP)
        self.assertFalse(CUT.endswith(('.', '?', '!')))
        cleaned = X.clean_quote(CUT)
        self.assertEqual(cleaned, (SENT * 6).strip(), 'a line cut at the cap ends at its last complete sentence')
        self.assertNotIn(CUT.split()[-1], cleaned.split()[-1])
        one = ('word ' * 120)[:X.QUOTE_CAP]      # cut at the cap with no sentence boundary at all
        self.assertEqual(X.clean_quote(one), ('word ' * 103).strip() + '.', 'no sentence to fall back to: the last complete word, with a full stop')
        early = ('Yes. ' + 'and then some more about it ' * 30)[:X.QUOTE_CAP]
        out = X.clean_quote(early)
        self.assertTrue(out.endswith('.') and not out.endswith(' .'))
        self.assertGreater(len(out), X.QUOTE_CAP * 0.8, 'a lone early sentence is not the whole quote; the words stay and the fragment goes')
        self.assertEqual(X.clean_quote(''), '')

    def test_fit_quote_trims_at_a_sentence_then_a_word(self):
        lines_of = lambda t: -(-len(t) // 40)      # forty characters a line
        text = 'Yara, 42, Kansas City: The first sentence is short. The second one runs a good deal longer than the first. A third.'
        self.assertEqual(X.fit_quote(text, lines_of, 9), text, 'fits whole')
        self.assertEqual(X.fit_quote(text, lines_of, 2), 'Yara, 42, Kansas City: The first sentence is short.', 'the last complete sentence that fits')
        self.assertEqual(X.fit_quote(text, lines_of, 1), 'Yara, 42, Kansas City: The first.', 'no sentence fits: the words that do, with a full stop')
        self.assertEqual(X.fit_quote('Yara, 42, Kansas City: Thirty characters of a long word', lines_of, 0), '')
        self.assertEqual(X.fit_quote('Yara, 42, Kansas City: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', lines_of, 1), '', 'only the attribution would fit: nothing prints')


class RoomExport(unittest.TestCase):
    def setUp(self):
        self.sess = _session()
        self.pdf = X.build_panel_pdf({'meta': {'name': 'Good Influence', 'subject': 'Good Influence'}}, self.sess)
        self.pages = _pdf_pages(self.pdf)

    def test_every_question_page_is_headed_by_the_question(self):
        self.assertTrue(self.pdf[:4] == b'%PDF')
        self.assertIn('Good Influence voters', self.pages[0], 'the room page keeps its own header')
        self.assertTrue(self.pages[0].startswith('FOCUS GROUP IQ'))
        for n, q in enumerate(ASKED, 1):
            pg = next(p for p in self.pages if p.startswith(f"QUESTION {n} "))
            self.assertIn(q, pg, f"Question {n}: the header is the question verbatim")
            self.assertNotIn(f"Question {n}: ", pg, 'the question is no longer a sub line under a generated header')
            self.assertNotIn('Two days after an election', pg, 'no sentence drawn from the read on the page')
            self.assertIn('Lead answer: No, 52.1% of the room, 1,985,124 people.' if n != 4 else 'Lead answer: Answer 1 to a long question about creators, 30.0% of the room, 900,000 people.', pg)
            self.assertIn('ANSWER SHARE PEOPLE', pg)
        for p in self.pages:
            self.assertNotIn(' took ', p)

    def test_a_long_question_is_whole_and_its_quotes_continue_on_a_second_page(self):
        first = next(p for p in self.pages if p.startswith('QUESTION 4 '))
        self.assertIn(ASKED[4 - 1], first, 'the 29-word question wraps; nothing is cut')
        cont = [p for p in self.pages if p.startswith('QUESTION 4, CONTINUED')]
        self.assertGreaterEqual(len(cont), 1, 'the quotes that did not fit moved to a continuation page')
        for c in cont:
            self.assertIn(ASKED[3], c, 'a continuation page is headed by the question too')
            self.assertGreaterEqual(len(_quotes(c)), 1)
        self.assertEqual(len(self.pages), 1 + len(ASKED) + len(cont), 'the room page, one page per question, the continuations')
        self.assertEqual([i for i, p in enumerate(self.pages) if p.startswith('QUESTION 4')], list(range(4, 5 + len(cont))), 'the continuations follow their question')
        quotes = _quotes(first) + [q for c in cont for q in _quotes(c)]
        self.assertEqual(len(quotes), 4, 'every quote printed, each whole with who said it')
        self.assertGreaterEqual(len(_quotes(first)), 1, 'the first page keeps what fits')
        for qt in quotes:
            self.assertRegex(qt, r'^(Yara|Jamal|Isaiah|Ethan), \d+, [A-Za-z ]+: ')
            self.assertTrue(qt.endswith('than people mean when they ask the question.'), qt[-60:])
            self.assertEqual(qt.count('a different kind of influence'), 6, 'the whole quote, not a trimmed one')

    def test_a_line_the_room_cut_mid_word_ends_at_its_last_sentence(self):
        pg = next(p for p in self.pages if p.startswith('QUESTION 5 '))
        quotes = _quotes(pg)
        self.assertEqual(len(quotes), 4)
        jamal = next(q for q in quotes if q.startswith('Jamal, 29, Houston: '))
        self.assertNotIn('someb', jamal)
        self.assertNotIn('The ones I trust', jamal, 'the fragment after the last full stop is gone')
        self.assertTrue(jamal.endswith('who paid for the post.'), jamal[-80:])
        for qt in quotes:
            last = qt.split()[-1]
            self.assertRegex(last, r"^[A-Za-z0-9'\"]+[.!?]$", f"the last token is a complete word ending in punctuation: {last!r}")

    def test_nothing_is_drawn_under_the_footer(self):
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(self.pdf))
        for page in reader.pages:
            low = []

            def visit(text, cm, tm, fd, fs, _low=low):
                if text.strip() and tm[5] < X.PANEL_FLOOR - 1 and 'CROSSWALK' not in text and not re.fullmatch(r'\d\d', text.strip()):
                    _low.append((text, tm[5]))
            page.extract_text(visitor_text=visit)
            self.assertEqual(low, [], low[:3])


class TwinDeckPanelPage(unittest.TestCase):
    def test_the_twin_deck_panel_page_is_headed_by_the_question(self):
        sess = _session()
        payload = {'meta': {'name': 'Good Influence', 'subject': 'Good Influence', 'size_us': 3_810_307}, 'the_read': {}, 'identity': {}, 'traits': {}, 'panel_sessions': [sess]}
        pg = next(p for p in X._pages(payload) if p.get('eyebrow') == 'Question 5')
        self.assertEqual(pg['header'], ASKED[4])
        self.assertEqual(pg['sub'], 'Focus Group IQ, Good Influence voters. Lead answer: No, 52.1% of the room, 1,985,124 people.')
        self.assertEqual(len(pg['body']), 3)
        jamal = next(b for b in pg['body'] if b.startswith('Jamal, 29, Houston: '))
        self.assertTrue(jamal.endswith('who paid for the post.'))
        self.assertNotIn('someb', jamal)
        slides = _slides(X.build_deck(payload))
        self.assertTrue(any(s.startswith('QUESTION 5 ') and ASKED[4] in s for s in slides), 'the eyebrow and the header land on the slide')


class ReportAgrees(unittest.TestCase):
    def test_deck_and_pdf_carry_the_same_question_headers_and_eyebrows(self):
        from test_digital_twin_exports_parity import _session as _report_session
        twin, sess = _report_session()
        payload = {'meta': twin['meta']}
        pages = R.report_pages(payload, sess)
        qp = [p for p in pages if p['kind'] == 'question']
        self.assertEqual(len(qp), 3)
        asked = {q['q'] for q in sess['questions']}
        slides = _slides(R.build_report_deck(payload, sess))
        pdf_pages = _pdf_pages(R.build_report_pdf(payload, sess))
        for p in qp:
            self.assertIn(p['header'], asked)
            self.assertRegex(p['eyebrow'], r'^Question \d+$')
            eye = p['eyebrow'].upper() + ' '
            slide = next(s for s in slides if s.startswith(eye))
            page = next(s for s in pdf_pages if s.startswith(eye))
            self.assertIn(p['header'], slide, 'the deck')
            self.assertIn(p['header'], page, 'the PDF')
            self.assertNotIn(' took ', slide)
            self.assertNotIn(' took ', page)
            self.assertIn(p['sub'].split('Lead answer: ')[1], page, 'the lead answer under the header')
        self.assertFalse(any('Yes took 58.3%' in s for s in slides + pdf_pages), 'the old generated header is gone from every format')


if __name__ == '__main__':
    unittest.main(verbosity=2)
