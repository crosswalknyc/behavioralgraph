import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import image_backfill as ib


class ScoreDimensionsTest(unittest.TestCase):
    def test_square_and_sharp_scores_full(self):
        self.assertEqual(ib.score_dimensions(1000, 1000), 1.0)

    def test_poster_loses_to_square(self):
        self.assertLess(ib.score_dimensions(500, 750), ib.score_dimensions(800, 800))

    def test_wide_banner_scores_low(self):
        self.assertLess(ib.score_dimensions(1600, 500), 0.4)

    def test_small_image_is_penalised(self):
        self.assertLess(ib.score_dimensions(200, 200), ib.score_dimensions(600, 600) / 2)

    def test_zero_dimensions(self):
        self.assertEqual(ib.score_dimensions(0, 100), 0.0)


class WikimediaLadderTest(unittest.TestCase):
    def test_tiny_summary_thumb_is_tried_last(self):
        url = ("https://thumb.wikimedia.org/wikipedia/commons/thumb/2/22/Logo.svg/"
               "40px-Logo.svg.png?utm_source=en.wikipedia.org")
        ladder = ib._wikimedia_thumb_candidates(url)
        self.assertIn("/640px-Logo.svg.png", ladder[0])
        self.assertTrue(ladder[-1].endswith("/40px-Logo.svg.png"))
        self.assertTrue(all("upload.wikimedia.org" in u for u in ladder))

    def test_large_summary_thumb_leads(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b3/X.svg/960px-X.svg.png"
        self.assertEqual(ib._wikimedia_thumb_candidates(url)[0], url)


class PickBestImageTest(unittest.TestCase):
    def _run(self, master, imdb, wiki, bing, dims):
        with mock.patch.object(ib, "imdb_lookup", return_value=imdb), \
             mock.patch.object(ib, "wiki_lookup", return_value=wiki), \
             mock.patch.object(ib, "_bing_image_results", return_value=bing) as bing_mock, \
             mock.patch.object(ib, "probe_dimensions", side_effect=lambda u: dims.get(u)):
            return ib.pick_best_image("Some Name", master), bing_mock

    def test_large_imdb_headshot_beats_a_lower_ranked_square_hit(self):
        (url, tag), _ = self._run(
            "TALENT", ("Some Name", "imdb.jpg"), None, ["b0.jpg", "b1.jpg", "b2.jpg"],
            {"imdb.jpg": (1280, 1920), "b0.jpg": (1600, 900), "b1.jpg": (300, 2000), "b2.jpg": (900, 900)},
        )
        self.assertEqual(url, "imdb.jpg")
        self.assertTrue(tag.startswith("imdb:"))

    def test_tiny_imdb_headshot_loses_to_sharp_square_hit(self):
        (url, tag), _ = self._run(
            "TALENT", ("Some Name", "imdb.jpg"), None, ["b0.jpg"],
            {"imdb.jpg": (180, 270), "b0.jpg": (1000, 1100)},
        )
        self.assertEqual((url, tag), ("b0.jpg", "bing"))

    def test_brand_searches_for_the_logo(self):
        (_, _), bing_mock = self._run("BRAND", (None, None), None, [], {})
        self.assertEqual(bing_mock.call_args_list[0][0][0], "Some Name logo")

    def test_unmeasurable_offers_fall_back_to_the_old_ladder(self):
        with mock.patch.object(ib, "resolve_image_url", return_value=("old.jpg", "wiki")) as old:
            (result, _) = self._run("BRAND", (None, None), "w.jpg", ["b0.jpg"], {})
        self.assertEqual(result, ("old.jpg", "wiki"))
        old.assert_called_once()


if __name__ == "__main__":
    unittest.main()
