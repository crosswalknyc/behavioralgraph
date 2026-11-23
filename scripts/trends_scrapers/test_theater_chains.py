"""
Offline checks for the theater-chain registry and its place in the
film_ticketing snapshot (2026-11-23). No network.

    PYTHONPATH=. python3 scripts/trends_scrapers/test_theater_chains.py
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.trends_scrapers import theater_chains as tc  # noqa: E402
from scripts.trends_scrapers.theater_common import (  # noqa: E402
    base_title, looks_walled, rank_by_showtimes)

HERE = Path(__file__).resolve().parent


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print('ok ', msg)


def test_registry_modules_answer_the_contract():
    keys = [c.key for c in tc.CHAINS]
    check(len(keys) == len(set(keys)) == 15, 'fifteen chains, no repeated key')
    national = {'fandango', 'cinemark', 'amc', 'regal'}
    check(not national & set(keys), 'no chain key collides with a ticket site')
    for c in tc.CHAINS:
        mod = importlib.import_module(f'scripts.trends_scrapers.{c.module}')
        check(callable(getattr(mod, 'fetch', None)), f'{c.key}: {c.module}.fetch exists')
        if c.site:
            check(c.site in getattr(mod, 'SITES', {}), f'{c.key}: site {c.site} is registered')
        check(c.label and '\u2014' not in c.label and '\u2013' not in c.label,
              f'{c.key}: label is plain')


def test_card_line_is_plain():
    harkins = next(c for c in tc.CHAINS if c.key == 'harkins')
    check(tc.card_line(harkins, 33) ==
          'The films with the most showtimes today across 33 Harkins theaters.',
          'card line names the theaters read')
    check(tc.card_line(harkins, None) ==
          'The films with the most showtimes today at Harkins Theatres.',
          'card line without a count')


def test_carry_rules():
    today = date(2026, 11, 23)
    prev = {'items': [{'rank': 1, 'title': 'X'}], 'date': '2026-11-22'}
    kept = tc._carry(prev, today)
    check(kept and kept['carried'] is True, 'a day-old list is kept')
    old = dict(prev, date=(today - timedelta(days=3)).isoformat())
    check(tc._carry(old, today) is None, 'a list older than two days is not kept')
    check(tc._carry({'items': [], 'date': '2026-11-22'}, today) is None, 'an empty list is not kept')
    check(tc._carry({'items': [{'title': 'X'}]}, today) is None, 'a list with no date is not kept')


def _fake_reader(items, meta):
    mod = types.ModuleType('fake_reader')
    mod.fetch = lambda limit=30: (items, meta)
    return mod


def test_fetch_chains_builds_panels_and_carries():
    real = tc.CHAINS
    try:
        tc.CHAINS = (tc.Chain('aaa', 'AAA Cinemas', 'fake_ok', 'AAA'),
                     tc.Chain('bbb', 'BBB Cinemas', 'fake_empty', 'BBB'),
                     tc.Chain('ccc', 'CCC Cinemas', 'fake_empty', 'CCC'))
        ranked = rank_by_showtimes([{'title': 'Wicked', 'showtimes': 12, 'theater_ids': [1, 2]}])
        sys.modules['scripts.trends_scrapers.fake_ok'] = _fake_reader(
            ranked, {'theaters': 2, 'date': '2026-11-23'})
        sys.modules['scripts.trends_scrapers.fake_empty'] = _fake_reader(
            [], {'theaters': 0, 'blocked': True})
        yday = (date.today() - timedelta(days=1)).isoformat()
        prev = {'bbb': {'label': 'BBB Cinemas', 'items': [{'rank': 1, 'title': 'Old'}],
                        'available': True, 'date': yday}}
        out = tc.fetch_chains(previous=prev)
        a, b, c = out['aaa'], out['bbb'], out['ccc']
        check(a['available'] and a['kind'] == 'showtimes' and a['group'] == 'chain',
              'a read chain is an available showtimes panel')
        check(a['items'][0]['showtimes'] == 12 and a['items'][0]['theaters'] == 2,
              'items keep showtimes and theaters')
        check('across 2 AAA theaters' in a['sub'], 'panel line uses the theater count')
        check(b.get('carried') and b['items'][0]['title'] == 'Old', 'an unread chain keeps its last list')
        check(c['available'] is False and c['items'] == [], 'an unread chain with nothing to keep is empty')
        only = tc.fetch_chains(only={'aaa'}, previous=prev)
        check(list(only) == ['aaa'], 'only limits the chains read')
    finally:
        tc.CHAINS = real
        sys.modules.pop('scripts.trends_scrapers.fake_ok', None)
        sys.modules.pop('scripts.trends_scrapers.fake_empty', None)


def test_titles_and_walls():
    check(base_title('The Odyssey: The IMAX Experience') == 'The Odyssey', 'IMAX tag comes off whole')
    check(base_title('Godzilla Minus Zero [Japanese, subtitled]') == 'Godzilla Minus Zero',
          'language tag comes off')
    check(base_title('OM Chapter 1: Udhiram \u2013 The Blood Wood') ==
          'OM Chapter 1: Udhiram - The Blood Wood', 'a long dash prints as a hyphen')
    check(base_title('Prime Time') == 'Prime Time', 'a title made of format words survives')
    page = '<html><head><title>Malco</title><script src="recaptcha"></script></head>' + 'x' * 9000
    check(not looks_walled(page), 'a real page that loads reCAPTCHA is not a wall')
    check(looks_walled('<html><head><title>Just a moment...</title></head></html>'),
          'a Cloudflare challenge is a wall')
    check(looks_walled('<iframe src="/_Incapsula_Resource?x=1"></iframe>'), 'an Imperva page is a wall')


def test_trending_fuse_skips_chain_panels():
    src = (HERE.parent.parent / 'trends_iq.py').read_text(encoding='utf-8')
    i = src.index('# ---------------- Films (theatrical ticketing) ----------------')
    block = src[i:i + 900]
    check("get('group') == 'chain'" in block, 'the Trending fuse skips chain panels')


def test_dashboard_lists_every_chain():
    html = (HERE.parent.parent / 'templates' / 'index.html').read_text(encoding='utf-8')
    i = html.index('function _tiqFilmsKeys(sources)')
    order = html[i:i + 700]
    for c in tc.CHAINS:
        check(f"'{c.key}'" in order, f'Films tab orders {c.key}')
    check('setTrendsIQFilm' in html and '_tiqFilmsShowLine' in html, 'Films pills and showtimes line exist')


def test_no_long_dashes_in_new_files():
    for p in sorted(HERE.glob('theater_*.py')) + [Path(__file__)]:
        text = p.read_text(encoding='utf-8')
        check('\u2014' not in text and '\u2013' not in text, f'{p.name}: no long dashes')


if __name__ == '__main__':
    os.environ.setdefault('TRENDS_THEATERS_OFFLINE', '1')
    test_registry_modules_answer_the_contract()
    test_card_line_is_plain()
    test_carry_rules()
    test_fetch_chains_builds_panels_and_carries()
    test_titles_and_walls()
    test_trending_fuse_skips_chain_panels()
    test_dashboard_lists_every_chain()
    test_no_long_dashes_in_new_files()
    print('ALL OK')
