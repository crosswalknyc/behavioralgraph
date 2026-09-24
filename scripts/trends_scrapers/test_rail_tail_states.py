#!/usr/bin/env python3
"""A rail row with no reading for its own service must still be
reachable by the sizing pass.

The blank cells on the board were rows the tail builder dropped: it
required a priced per-platform block, so a title that had never been
collected, or had been collected but never priced for THIS service,
was invisible to the pass whose job is to give it a number. Dead Mail
at the bottom of Lionsgate+ is the case these cover.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.trends_scrapers import stream_estimates as SE


def _tail(monkey_rows, researched, slug='lionsgateplus'):
    SE._published_rail_rows = lambda *a, **k: monkey_rows
    return SE._rail_tail_rows(researched, slug, {}, {}, {})


def test_three_states_are_all_returned():
    researched = {
        'film:priced one':   {'title': 'Priced One', 'kind': 'film',
                              'by_platform': {'lionsgateplus':
                                              {'us_estimate': 50_000}}},
        'film:no block':     {'title': 'No Block', 'kind': 'film',
                              'by_platform': {'primevideo':
                                              {'us_estimate': 90_000}}},
    }
    rows = _tail([('Priced One', 'film'), ('No Block', 'film'),
                  ('Dead Mail', 'film')], researched)
    states = {r['title']: r['_state'] for r in rows}
    assert states == {'Priced One': 'priced', 'No Block': 'unpriced',
                      'Dead Mail': 'absent'}, states
    assert rows[0]['title'] == 'Priced One', 'priced rows sort first'
    print(f'  three states returned: {states}')


def test_writer_creates_a_block_and_an_item():
    researched = {
        'film:no block': {'title': 'No Block', 'kind': 'film',
                          'us_estimate': 90_000,
                          'by_platform': {'primevideo':
                                          {'us_estimate': 90_000}}},
    }
    rows = _tail([('No Block', 'film'), ('Dead Mail', 'film')], researched)
    made = 0
    for r in rows:
        if SE._write_platform_reading(researched, r, 'lionsgateplus',
                                      12_345, 'salt',
                                      target_date_iso='2026-09-24'):
            made += 1
    assert made == 2, made
    blk = researched['film:no block']['by_platform']['lionsgateplus']
    assert blk['us_estimate'] > 0, blk
    assert researched['film:no block']['by_platform']['primevideo'][
        'us_estimate'] == 90_000, 'sibling service untouched'
    assert researched['film:no block']['us_estimate'] > 90_000, \
        'aggregate grows by the block that appeared'
    assert 'film:dead mail' in researched, 'absent row got an item'
    made_blk = researched['film:dead mail']['by_platform']['lionsgateplus']
    assert made_blk['us_estimate'] > 0, made_blk
    print(f"  block created ({blk['us_estimate']:,}), item created "
          f"({made_blk['us_estimate']:,}), sibling untouched")


def test_priced_rows_still_move_not_duplicate():
    researched = {
        'film:priced one': {'title': 'Priced One', 'kind': 'film',
                            'us_estimate': 50_000,
                            'by_platform': {'lionsgateplus':
                                            {'us_estimate': 50_000}}},
    }
    rows = _tail([('Priced One', 'film')], researched)
    assert SE._write_platform_reading(researched, rows[0], 'lionsgateplus',
                                      33_333, 'salt')
    blk = researched['film:priced one']['by_platform']['lionsgateplus']
    assert blk['us_estimate'] == 33_333, blk
    assert len(researched['film:priced one']['by_platform']) == 1
    print(f"  priced row moved to {blk['us_estimate']:,}, no duplicate block")


if __name__ == '__main__':
    orig = SE._published_rail_rows
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith('test_') and callable(fn):
                print(name)
                fn()
    finally:
        SE._published_rail_rows = orig
    print('OK')
