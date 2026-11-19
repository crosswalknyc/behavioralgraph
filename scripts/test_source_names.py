#!/usr/bin/env python3
"""A citation never names a measurement firm or a research house on a
surface a reader sees. The fact stays. A sentence that is only the
citation goes. A sentence with no citation is unchanged. The view
definition lines still pass the house scrub.

Hermetic: no app module, no S3, no mail.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("REGRESSION_TEST_MODE", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from migration.digital_twin_copy import strip_calibration_language  # noqa: E402
from migration.source_names import names_source, scrub_office_file, strip_named_sources  # noqa: E402
from prometheus import guards as G  # noqa: E402
from prometheus import methodology as M  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

# Words a citation must not be replaced with.
_RELABEL = (
    "measured", "counted", "in our data", "in crosswalk's data",
    "external source", "outside source", "third party", "market research",
    "researched", "modeled", "estimated", "synthetic", "synth",
)


def _clean(text: str) -> None:
    low = text.lower()
    for w in _RELABEL:
        if w in low:
            raise AssertionError(f"{w!r} in {text!r}")


class Strip(unittest.TestCase):
    def test_the_fact_stays_and_the_name_goes(self):
        cases = (
            ("According to Nielsen, 4.1M people watched.", "4.1M people watched."),
            ("per Variety", ""),
            ("(Source: Pew)", ""),
            ("Statista puts it at 12%.", "12%."),
            ("According to Google Trends, 12% searched.", "12% searched."),
            ("The room liked the pilot.", "The room liked the pilot."),
        )
        for src, want in cases:
            got = strip_named_sources(src)
            self.assertEqual(got, want, src)
            self.assertFalse(names_source(got), got)
            _clean(got)
            self.assertEqual(strip_named_sources(got), got)

    def test_a_plain_sentence_is_unchanged_and_a_bare_citation_goes(self):
        plain = "The room liked the pilot and 4.1M people watched it."
        self.assertEqual(strip_named_sources(plain), plain)
        self.assertEqual(strip_named_sources("See Variety."), "")
        mixed = "The room liked the pilot. According to Nielsen, 4.1M people watched. See Variety."
        self.assertEqual(strip_named_sources(mixed), "The room liked the pilot. 4.1M people watched.")

    def test_the_house_scrub_keeps_the_fact_and_the_definition(self):
        out = pma.scrub_user_text("According to Nielsen, 4.1M people watched. The room liked the pilot.")
        self.assertNotIn("Nielsen", out)
        self.assertIn("4.1M people watched", out)
        self.assertIn("The room liked the pilot", out)
        _clean(out)
        self.assertEqual(pma.scrub_user_text("The room liked the pilot."), "The room liked the pilot.")
        self.assertEqual(pma.scrub_user_text("(Source: Pew)"), "")
        full = M.others_part()
        self.assertEqual(pma.scrub_user_text(full), full)
        self.assertIn(M.NIELSEN_LINE, pma.scrub_user_text("The read stands. " + M.NIELSEN_LINE))
        self.assertIn(M.PEACOCK_LINE, pma.scrub_user_text(M.PEACOCK_LINE))

    def test_the_twin_guard_and_the_partner_belt(self):
        got = strip_calibration_language("According to Nielsen, week one drew 4.1M viewers.")
        self.assertEqual(got, "Week one drew 4.1M viewers.")
        _clean(got)
        self.assertEqual(strip_calibration_language("The room liked the pilot."), "The room liked the pilot.")
        self.assertEqual(strip_calibration_language("(Source: Pew)"), "")
        partner = G.scrub_partner_text("The read stands. According to Nielsen, the finale drew 4.1M viewers.")
        self.assertNotIn("Nielsen", partner)
        self.assertIn("4.1M", partner)
        _clean(partner)
        self.assertIn(M.NIELSEN_LINE, G.scrub_partner_text(M.NIELSEN_LINE + " Per Nielsen, the finale drew 4.1M viewers."))
        self.assertNotIn("Per Nielsen", G.scrub_partner_text(M.NIELSEN_LINE + " Per Nielsen, the finale drew 4.1M viewers."))

    def test_an_office_file_loses_the_name_and_keeps_the_line(self):
        slide = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody>"
            "<a:p><a:r><a:t>According to Nielsen, 4.1M people watched.</a:t></a:r></a:p>"
            "<a:p><a:r><a:t>The room liked the pilot.</a:t></a:r></a:p>"
            "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
        with tempfile.TemporaryDirectory() as d:
            deck = Path(d) / "deck.pptx"
            with zipfile.ZipFile(deck, "w") as z:
                z.writestr("ppt/slides/slide1.xml", slide)
                z.writestr("[Content_Types].xml", "<Types></Types>")
            self.assertGreater(scrub_office_file(deck), 0)
            with zipfile.ZipFile(deck) as z:
                xml = z.read("ppt/slides/slide1.xml").decode("utf-8")
            self.assertNotIn("Nielsen", xml)
            self.assertIn("4.1M people watched", xml)
            self.assertIn("The room liked the pilot", xml)
            page = Path(d) / "note.html"
            page.write_text(
                f"<p>{M.NIELSEN_LINE}</p><p>(Source: Pew)</p><p>The room liked the pilot.</p>",
                encoding="utf-8",
            )
            scrub_office_file(page, hold=M.shield_definitions, release=M.unshield_definitions)
            html = page.read_text(encoding="utf-8")
            self.assertIn(M.NIELSEN_LINE, html)
            self.assertNotIn("Pew", html)
            self.assertIn("The room liked the pilot", html)
            clean = Path(d) / "clean.html"
            body = "<p>The room liked the pilot.</p>"
            clean.write_text(body, encoding="utf-8")
            self.assertEqual(scrub_office_file(clean), 0)
            self.assertEqual(clean.read_text(encoding="utf-8"), body)

    def test_the_exits_call_the_one_list(self):
        finish = (ROOT and Path(ROOT, "migration", "design_skill.py").read_text(encoding="utf-8"))
        self.assertIn("scrub_office_file", finish)
        app = Path(ROOT, "app.py").read_text(encoding="utf-8")
        prog = app[app.index("def _scrub_v1_progress"):app.index("def _scrub_v1_freetext")]
        free = app[app.index("def _scrub_v1_freetext"):app.index("def _scrub_v1_status")]
        self.assertIn("strip_named_sources", prog)
        self.assertIn("strip_named_sources", free)
        self.assertIn("shield_definitions", free)


if __name__ == "__main__":
    unittest.main(verbosity=1)
