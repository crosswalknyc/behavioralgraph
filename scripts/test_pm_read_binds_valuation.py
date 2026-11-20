#!/usr/bin/env python3
"""Two fixtures from 2026-11-20 (Jenna), fixed for new runs.

1. A read about a valuation on file reads the stored file for exactly the
   window asked and never mixes runs; a read's lines agree with its own
   headline. In one thread a read on August 27 to September 8 printed the
   September 1 file's $102,145,967 and 92,615,994 people, and an earlier
   full-period read headlined $6,977,878.28 while listing a total of
   1,997,755.4 under it. Fixtures carry the figures only.
2. A question about the world ('is there any awareness for the film
   ...') is never answered from the profile shelf and never with a build
   offer: the catalog lane stands down (at the door and when the Reader's
   executor reaches it), the ask goes on to the reasoning path with the
   guidance to answer the question asked from the research.

Hermetic: no model, no live store, no mail, no app boot."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import consistency as X  # noqa: E402
from prometheus import knowledge as K  # noqa: E402
from prometheus import catalog_lane as CL  # noqa: E402
from prometheus import read_repair as RR  # noqa: E402
from prometheus import figure_binding as FB  # noqa: E402
from migration import bpiq_anchors as BA  # noqa: E402
import test_pm_near_twin_consistency as T  # noqa: E402  (the Anne figures)

FAIL = 0
BANNED = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', 'anthropic', 'researched',
          'calibrated', 'nielsen', 'statista', 'according to', 'source:')


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    return '\u2014' not in low and '\u2013' not in low and not any(w in low for w in BANNED)


FILES = {
    'Anne_Hathaway_x_Shiseido_11_20_2026_04_21.json': dict(T.ANNE_FIRST, post_period={'start': '2026-09-01', 'end': '2026-10-08', 'days': 38}),
    'Anne_Hathaway_x_Shiseido_11_20_2026_07_11.json': dict(T.ANNE_SECOND, post_period={'start': '2023-09-06', 'end': '2026-10-08', 'days': 1129}),
    'Anne_Hathaway_x_Shiseido_11_20_2026_07_37.json': dict(T.ANNE_THIRD, post_period={'start': '2026-08-27', 'end': '2026-10-08', 'days': 43}),
}
META = {k: {'display_name': 'Anne Hathaway x Shiseido'} for k in FILES}

# the read that mixed runs: the September 1 file's figures under the August 27 to September 8 window
MIXED_READ = {
    'subject': 'Anne Hathaway x Shiseido', 'window_start': '2026-08-27', 'window_end': '2026-09-08',
    'headline': '',
    'metrics': [
        {'name': 'total_brand_value', 'label': 'Total brand value', 'unit': 'usd', 'value': 102145967},
        {'name': 'attributable_value', 'label': 'Value attributable to the partnership', 'unit': 'usd', 'value': 17700376},
        {'name': 'attributable_share', 'label': 'Partnership share of total brand value', 'unit': 'pct', 'value': 17.3},
        {'name': 'us_people_reached', 'label': 'US people the partnership reached', 'unit': 'people', 'value': 92615994},
        {'name': 'new_us_people', 'label': 'New US people engaging with the brand', 'unit': 'people', 'value': 2091965},
        {'name': 'partnership_content_audience', 'label': 'Direct partnership content audience', 'unit': 'people', 'value': 53727},
    ],
    'reads': ['This is the 13-day campaign window, not the full-period read.',
              'Just over a dollar of brand value per person reached, with 17.3% of the total sitting with the partnership.',
              '2,091,965 of the people reached are new to the brand, about 2.3% of reach.'],
}
# the full-period read whose lines did not agree with its headline
FULL_READ = {
    'subject': 'Anne Hathaway x Shiseido', 'window_start': '2023-09-06', 'window_end': '2026-09-08',
    'headline': 'Across the full partnership period, Anne Hathaway x Shiseido carries a total brand value of $6,977,878.28, with $2,073,609.62 of that attributable to the partnership itself.',
    'metrics': [
        {'name': 'total_brand_value', 'label': 'Total brand value', 'unit': 'usd', 'value': 1997755},
        {'name': 'attributable_value', 'label': 'Value attributable to the partnership', 'unit': 'usd', 'value': 2074726},
        {'name': 'audience_reached', 'label': 'US audience reached by the partnership', 'unit': 'people', 'value': 340475},
        {'name': 'engaged_with_shiseido', 'label': 'US people who engaged with Shiseido', 'unit': 'people', 'value': 162865},
        {'name': 'converted', 'label': 'US people who converted', 'unit': 'people', 'value': 13888},
        {'name': 'incremental_users', 'label': 'Incremental US users', 'unit': 'people', 'value': 57103},
    ],
    'reads': ['Conversion inside the engaged pool runs 8.5%, and engagement reaches 47.8% of everyone the partnership touched.',
              'The three-year figure is not a sum of the campaign flight: the September 2026 burst window alone carries $102,145,967.00 in total brand value against a much larger single-week reach.'],
}


def _hooks(on=True):
    X.META_FOR_TESTS = META if on else None
    X.LOADER_FOR_TESTS = (lambda key: FILES.get(key)) if on else None


def test_which_file():
    print('which valuation a read is about')
    _hooks()
    try:
        cands = X.valuation_candidates('Anne Hathaway', 'Shiseido')
        check('three files on the pairing', len(cands) == 3)
        b = X.which_valuation(cands, {'start': '2026-08-27', 'end': '2026-09-08'}, 'Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26')
        check('the window asked picks the file for exactly that window (August 27 to September 8)', b and b['ref'].endswith('07_37.json'))
        b2 = X.which_valuation(cands, {'start': '2026-09-01', 'end': '2026-09-08'}, 'what is it worth from Sep 1 to Sep 8, 2026')
        check('September 1 to 8 picks the September 1 file', b2 and b2['ref'].endswith('04_21.json'))
        b3 = X.which_valuation(cands, {'start': '', 'end': ''}, 'Can you resend for the full period?')
        check("'the full period' picks the longest window", b3 and b3['ref'].endswith('07_11.json'))
        b4 = X.which_valuation(cands, {'start': '2026-08-29', 'end': '2026-09-08'}, 'what about Aug 29 to Sep 8')
        check('a window inside the August 27 file that matches none exactly picks the file sharing the most of it', b4 and b4['ref'].endswith('07_37.json'))
        b5 = X.which_valuation(cands, {'start': '2022-01-01', 'end': '2022-02-01'}, 'what about January 2022')
        check('a window touching no file binds nothing (a fresh read)', b5 is None)
        b6 = X.which_valuation(cands, {'start': '', 'end': ''}, 'what is Anne Hathaway x Shiseido worth')
        check('no window and no phrase: the newest delivered file', b6 and b6['ref'].endswith('07_37.json'))
    finally:
        _hooks(False)


def test_block_names_one_file():
    print('the prompt block')
    _hooks()
    try:
        cands = X.valuation_candidates('Anne Hathaway', 'Shiseido')
        b = X.which_valuation(cands, {'start': '2026-08-27', 'end': '2026-09-08'}, '')
        blk = X.valuation_block(b, cands)
        check('the block carries the bound file alone: $1,994,140.40 on 53,727 people, measured through October 8',
              '$1,994,140.40' in blk and '53,727' in blk and 'October 8, 2026' in blk and '102,145,967' not in blk and '6,977,878' not in blk)
        check('and names the other windows so their figures are never this answer',
              'Other valuations exist on this pairing for other windows' in blk and 'September 1, 2026 to September 8, 2026' in blk
              and 'September 6, 2023 to September 8, 2026' in blk)
        check('binding words: the lines agree with the headline, nothing re-derived', 'every line agrees with the headline' in blk)
    finally:
        _hooks(False)


def test_mixed_read_is_bound():
    print('the read on August 27 to September 8 that carried the September 1 figures')
    _hooks()
    try:
        cands = X.valuation_candidates('Anne Hathaway', 'Shiseido')
        bound = X.which_valuation(cands, {'start': '2026-08-27', 'end': '2026-09-08'}, 'Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26')
        data, rec = X.hold_read_to_valuation(json.loads(json.dumps(MIXED_READ)), bound, cands)
        v = {m['name']: m['value'] for m in data['metrics']}
        check(f"total brand value is the file's: {v.get('total_brand_value')}", v.get('total_brand_value') == 1_994_140.4)
        check(f"attributable is the file's: {v.get('attributable_value')}", v.get('attributable_value') == 1_005_526.4)
        check(f"people reached is the file's: {v.get('us_people_reached'):,}", v.get('us_people_reached') == 53_727)
        check(f"new people is the file's: {v.get('new_us_people'):,}", v.get('new_us_people') == 39_973)
        check('a second line on the same figure (the content audience) is dropped, not doubled', 'partnership_content_audience' not in v)
        check('a rate that belongs to no file figure is left alone', v.get('attributable_share') == 17.3)
        check('the record says what moved', len(rec['set']) >= 3 and rec['ref'].endswith('07_37.json') and rec['dropped'])
        check('the read carries the file\'s window and the bound names for the passes that follow',
              data['window_start'] == '2026-08-27' and data['window_end'] == '2026-09-08' and 'total_brand_value' in data['_valuation_bound'])
        check('a headline is composed from the file when the read had none, in plain words',
              data['headline'].startswith('Anne Hathaway x Shiseido, August 27, 2026 to September 8, 2026: total brand value $1,994,140.40')
              and clean(data['headline']))
        check("the read that carried the September 1 run's 2,091,965 is dropped; the two that carried no other run's figure stay",
              len(data['reads']) == 2 and not any('2,091,965' in r for r in data['reads']) and rec['sentences_dropped'] == 1)
    finally:
        _hooks(False)


def test_full_period_lines_agree_with_headline():
    print("the full-period read whose total line said 1,997,755.4 under a $6,977,878.28 headline")
    _hooks()
    try:
        cands = X.valuation_candidates('Anne Hathaway', 'Shiseido')
        bound = X.which_valuation(cands, {'start': '', 'end': ''}, 'Can you resend for the full period?')
        data, rec = X.hold_read_to_valuation(json.loads(json.dumps(FULL_READ)), bound, cands)
        v = {m['name']: m['value'] for m in data['metrics']}
        check(f"the total line is the headline's figure: {v.get('total_brand_value')}", v.get('total_brand_value') == 6_977_878.28)
        check('attributable agrees with the headline', v.get('attributable_value') == 2_073_609.62)
        check('the counts are the file\'s', v.get('audience_reached') == 340_475 and v.get('engaged_with_shiseido') == 162_865
              and v.get('converted') == 13_888 and v.get('incremental_users') == 57_103)
        check('the headline the model wrote stands (it was the file\'s figure)', data['headline'].startswith('Across the full partnership period'))
        check("the read that quoted another run's $102,145,967.00 is dropped; the one that did not stays",
              len(data['reads']) == 1 and data['reads'][0].startswith('Conversion inside') and rec['sentences_dropped'] == 1)
    finally:
        _hooks(False)


def test_stored_read_never_overrides_the_file():
    print('a stored read on another window never overwrites a bound figure')
    res = {'subject': 'Anne Hathaway x Shiseido', 'cohort': '', 'window_start': '2023-09-06', 'window_end': '2026-09-08',
           'metrics': [{'name': 'total_brand_value', 'label': 'Total brand value', 'unit': 'usd', 'value': 6_977_878.28},
                       {'name': 'engaged', 'label': 'US people who engaged', 'unit': 'people', 'value': 162_865}],
           '_valuation_bound': ['total_brand_value']}
    prior = [{'ts': '2026-11-20T18:18:00', 'cohort': '', 'metrics': [{'name': 'total_brand_value', 'value': 1_994_140.4},
                                                                     {'name': 'engaged', 'value': 150_001}]}]
    out, bound = FB.bind_to_stored(json.loads(json.dumps(res)), prior, ask_text='resend for the full period')
    v = {m['name']: m['value'] for m in out['metrics']}
    check('the bound total stays the file\'s though an earlier entry stated another', v['total_brand_value'] == 6_977_878.28)
    check('a figure that is not bound still follows the stored read (the standing rule)', v['engaged'] == 150_001)


def test_knowledge_resolves_the_file_and_rebinds():
    print('through the knowledge layer and the repair seam')
    _hooks()
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    try:
        base = {'subject': 'Anne Hathaway x Shiseido', 'source': 'catalog'}
        ask = K.ask_from(None, 'Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26', base, {})
        check('the words carry the window', K.inline_window('Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26') == {'start': '2026-08-27', 'end': '2026-09-08'})
        ask['window'] = K.inline_window('Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26')
        bound, others = K.valuation_for_read('Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26', ask, base, {})
        check('the pairing and the window resolve to the August 27 file', bound and bound['ref'].endswith('07_37.json') and len(others) == 3)
        ask2 = K.ask_from(None, 'Can you resend for the full period?', base, {})
        bound2, _ = K.valuation_for_read('Can you resend for the full period?', ask2, base, {})
        check("'the full period' on the thread's pairing resolves to the three-year file", bound2 and bound2['ref'].endswith('07_11.json'))
        blk = K.read_blocks('Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26', {}, base, pk=None, saved=lambda *a, **k: 'LEGACY SAVED', catalog=None)
        check('the read\'s block carries the file for the window asked, not the legacy newest-file block',
              'THE VALUATION ON FILE FOR THIS WINDOW' in blk and '$1,994,140.40' in blk and 'LEGACY SAVED' not in blk and '102,145,967' not in blk)
        check('the binding is left for the hold', K.peek_valuation('Anne Hathaway x Shiseido', 'Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26') is not None)
        n_before = len((X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or [])

        def enforce(d):
            # the coherence pass as a stand-in: it redraws the total (a digit nudge) and keeps a thread figure
            out = json.loads(json.dumps(d))
            for m in out['metrics']:
                if m['name'] == 'total_brand_value':
                    m['value'] = 1_997_755
            out.pop('_valuation_bound', None)
            return out
        res, d = RR.coherent(json.loads(json.dumps(MIXED_READ)), enforce=enforce, question='Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26',
                             subject='Anne Hathaway x Shiseido')
        v = {m['name']: m['value'] for m in res['metrics']}
        check('after the coherence pass the figures are the file\'s again and the bound names ride the result',
              v['total_brand_value'] == 1_994_140.4 and v['us_people_reached'] == 53_727 and 'total_brand_value' in res.get('_valuation_bound', []))
        check('a staff record was written for the bound read',
              len((X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or []) > n_before)
        check('the pending binding is cleared once the pass went through', K.peek_valuation('Anne Hathaway x Shiseido', 'Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26') is None)
        held = K.held('Anne Hathaway x Shiseido', bpiq_source_key='x/Anne_Hathaway_x_Shiseido_11_20_2026_07_37.json')
        check('held() takes a file to filter the catalog on (the catalog is off here; the call stands)', isinstance(held, dict))
    finally:
        _hooks(False)
        X.RECORDS_FOR_TESTS = None


def test_saved_valuation_prefers_exact_window():
    print('the legacy saved-valuation lookup prefers the exact window')
    w = {'start': '2026-08-27', 'end': '2026-09-08'}

    def anchors_for(subject, window=None, products=None, with_ledger=True):
        if subject.lower() != 'anne hathaway x shiseido':
            return {'subject': subject, 'facts': []}
        def mk(key, win, total, people, as_of):
            return [{'product': 'bpiq', 'kind': 'valuation', 'label': 'total brand value, USD', 'value': total, 'window': win, 'as_of': as_of, 'source_key': key},
                    {'product': 'bpiq', 'kind': 'audience_size', 'label': 'US audience reached', 'value': people, 'window': win, 'as_of': as_of, 'source_key': key}]
        return {'subject': 'Anne Hathaway x Shiseido', 'facts':
                mk('a/first.json', {'start': '2026-09-01', 'end': '2026-09-08'}, 102_145_967.0, 92_615_994, '2026-11-20T03:21:23')
                + mk('a/third.json', {'start': '2026-08-27', 'end': '2026-09-08'}, 1_994_140.4, 53_727, '2026-11-20T06:37:44')
                + mk('a/newest.json', {'start': '2026-08-30', 'end': '2026-09-08'}, 3_000_000.0, 70_001, '2026-11-20T09:00:00')}
    os.environ['BPIQ_CORPUS_ANCHORS'] = '1'
    try:
        v = BA.saved_valuation(('Anne Hathaway', 'Shiseido'), window=w, anchors_for=anchors_for)
        check('the file for exactly the window asked wins over a newer file on another window', v and v['source_key'] == 'a/third.json')
        v2 = BA.saved_valuation(('Anne Hathaway', 'Shiseido'), window=None, anchors_for=anchors_for)
        check('with no window the newest file is the one', v2 and v2['source_key'] == 'a/newest.json')
    finally:
        os.environ['BPIQ_CORPUS_ANCHORS'] = '0'


def test_awareness_is_not_a_shelf_lookup():
    print('a question about the world')
    q = 'is there any awareness for the film Josephine'
    check('the catalog lane does not parse it as an existence ask', CL.parse(q) is None)
    check('and answers nothing for it', CL.answer(q) is None)
    check("the Reader's executor words ('do we have Josephine?') after that question are stood down too", CL.answer('do we have Josephine?') is None)
    for t in ('is there any buzz around Harbor Lights', 'is there momentum behind the Harbor Lights launch', 'is anyone talking about Harbor Lights',
              'is there interest in the film Harbor Lights'):
        check(f'  {t!r} is a world question', CL.world_question(t) and CL.parse(t) is None)
    CL._remember('do we have a profile of Harbor Lights viewers?')
    check('a shelf question still parses as one', CL.parse('do we have a profile of Harbor Lights viewers?') == ('exists', 'Harbor Lights viewers'))
    check("'do we have any awareness data on X' asks about the shelf and stays a lookup",
          CL.parse('do we have any awareness data on Harbor Lights') is not None)
    wb = K.world_block(q, 'Josephine')
    check('the read is told to answer the question asked from the research and never offer a build',
          'never offer to build a profile' in wb and 'Never say the subject is not on the dashboard' in wb and 'Josephine' in wb)
    check('and to speak in the signals Crosswalk reads (no awareness, recall or intent words in the reply)',
          'attention' in wb and 'the words awareness, recall and intent never appear' in wb)
    check('no block for an ordinary question', K.world_block('how many people watched Harbor Lights', 'Harbor Lights') == '')


def test_lines_plain():
    print('the lines')
    _hooks()
    try:
        cands = X.valuation_candidates('Anne Hathaway', 'Shiseido')
        bound = X.which_valuation(cands, {'start': '2026-08-27', 'end': '2026-09-08'}, '')
        data, _ = X.hold_read_to_valuation({'metrics': [], 'reads': []}, bound, cands)
        check(f'the composed headline is plain: {data["headline"]!r}', clean(data['headline']) and data['headline'].endswith('partnership.'))
    finally:
        _hooks(False)


if __name__ == '__main__':
    test_which_file()
    test_block_names_one_file()
    test_mixed_read_is_bound()
    test_full_period_lines_agree_with_headline()
    test_stored_read_never_overrides_the_file()
    test_knowledge_resolves_the_file_and_rebinds()
    test_saved_valuation_prefers_exact_window()
    test_awareness_is_not_a_shelf_lookup()
    test_lines_plain()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
