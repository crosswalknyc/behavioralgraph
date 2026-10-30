"""Tests for the Books-tab genre classifier (book_genres.py)."""
import unittest
from unittest import mock

from scripts.trends_scrapers import book_genres as bg


class ClassifyTests(unittest.TestCase):
    def test_specific_tag_beats_generic(self):
        self.assertEqual(bg.classify(['Fiction', 'Fantasy']), 'Fantasy & Sci-Fi')
        self.assertEqual(bg.classify(['Books', 'Romance']), 'Romance')

    def test_generic_fallbacks(self):
        self.assertEqual(bg.classify(['Fiction']), 'Literary & General Fiction')
        self.assertEqual(bg.classify(['Nonfiction']), 'Other Nonfiction')
        self.assertIsNone(bg.classify([]))

    def test_every_result_is_a_listed_genre(self):
        for tags in (['Thriller'], ['Horror'], ['True Crime'], ['Biography & Autobiography'],
                     ['Comics & Graphic Novels'], ['Juvenile Fiction'], ['Self-Improvement']):
            self.assertIn(bg.classify(tags), bg.GENRES, tags)


class KeyTests(unittest.TestCase):
    def test_subtitle_and_author_normalised(self):
        a = bg.book_key('Fourth Wing: Empyrean, Book 1', 'Rebecca Yarros')
        b = bg.book_key('Fourth Wing', 'Yarros, Rebecca')
        self.assertEqual(a, b)


class AnnotateTests(unittest.TestCase):
    def test_stamps_every_row_and_shares_across_charts(self):
        books = {
            'apple_books': {'items': [{'title': 'Fourth Wing', 'author': 'Rebecca Yarros',
                                       'genres': ['Fantasy']}]},
            'amazon_books': {'items': [{'title': 'Fourth Wing (Empyrean)', 'author': 'Rebecca Yarros'},
                                       {'title': 'Nobody Knows This', 'author': 'A. Writer'}]},
        }
        libby = {'magazine': {'items': [{'title': 'The New Yorker'}]}}
        with mock.patch.object(bg, 'load_cache', return_value={}), \
             mock.patch.object(bg, 'save_cache'), \
             mock.patch.object(bg, '_fill_in_background') as fill:
            bg.annotate(books, libby)
        self.assertEqual(books['apple_books']['items'][0]['book_genre'], 'Fantasy & Sci-Fi')
        self.assertEqual(books['amazon_books']['items'][0]['book_genre'], 'Fantasy & Sci-Fi')
        self.assertEqual(books['amazon_books']['items'][1]['book_genre'], bg.UNCLASSIFIED)
        self.assertEqual(libby['magazine']['items'][0]['book_genre'], 'Magazines')
        fill.assert_called_once()

    def test_never_raises(self):
        with mock.patch.object(bg, 'load_cache', side_effect=RuntimeError('s3 down')):
            bg.annotate({'apple_books': {'items': [{'title': 'X'}]}}, {})


if __name__ == '__main__':
    unittest.main()
