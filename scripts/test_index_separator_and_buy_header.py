#!/usr/bin/env python3
"""An index of four digits or more prints with a thousands separator, and
the deck's buy page is titled as what it is (2026-11-11).

The regenerated Wicked Tuna deck carried a card that read "Costa
Sunglasses reaches 6.5% at index 5548" and a buy page headed "Four
aisles where the file and the journeys agree."

  [1] the shared formatter: fmt_index and separate_index_figures
  [2] every reader exit (scrub_user_text) separates a bare index; a
      year, a percent, a decimal and a link are left alone
  [3] the deck builder: the group card, the INDEX column, the generated
      subhead; a rendered deck carries 'Index 5,548'
  [4] the deterministic chat lines (the cross, the category reach block)
  [5] the dashboard: formatIndex and the category line (node, when
      installed); the row-level CSV cell stays a plain number
  [6] the deck planner names the buy page a set of categories and bans
      store words for it; the readability check flags the header that
      shipped and passes the literal retail uses

Run: python3 scripts/test_index_separator_and_buy_header.py
Hermetic: no network, no S3, no model call.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

FAILS: list[str] = []


def check(cond, msg, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (f'  ({str(detail)[:240]})' if (detail and not cond) else ''))
    if not cond:
        FAILS.append(msg)


print('[1] the shared formatter')
from prometheus.index_format import fmt_index, separate_index_figures as sep  # noqa: E402

check([fmt_index(v) for v in (5548, '5548', 119, 5548.6, 12450, '5,548')] == ['5,548', '5,548', '119', '5,549', '12,450', '5,548'],
      'fmt_index: whole number, separator at four digits', [fmt_index(v) for v in (5548, '5548', 119, 5548.6, 12450, '5,548')])
check([fmt_index(v) for v in (None, '', 'n/a', True)] == ['', '', '', ''], 'fmt_index: not a number prints nothing')
for raw, want in [
    ('Costa Sunglasses reaches 6.5% at index 5548.', 'Costa Sunglasses reaches 6.5% at index 5,548.'),
    ('an index of 12450 against the US', 'an index of 12,450 against the US'),
    ('(index 5548)', '(index 5,548)'),
    ('Index: 2301', 'Index: 2,301'),
    ('idx 5548', 'idx 5,548'),
    ('over-indexes at 2104 here', 'over-indexes at 2,104 here'),
    ('Index 10000 vs 100', 'Index 10,000 vs 100'),
    ('at index 5548, and Yeti at index 683', 'at index 5,548, and Yeti at index 683'),
]:
    check(sep(raw) == want, f'{raw!r} gains its separator', sep(raw))
for raw in ('index 119, 1.2x the US average', 'the index 5,548', 'index 999', 'index 5548.2', 'index 5548%',
            'https://x.example/?index=5548', 'see /index/5548 for the page', 'index 2025 to 2026', 'the 2026 upfront'):
    check(sep(raw) == raw, f'{raw!r} is left alone', sep(raw))

print('[2] the reader exit')
import prometheus_analysis as pma  # noqa: E402

out = pma.scrub_user_text('Costa Sunglasses reaches 6.5% at index 5548, and Yeti sits at index 683.')
check('index 5,548' in out and 'index 683' in out and '5548' not in out, 'scrub_user_text separates a bare four-digit index', out)
plan = pma.enforce_insights_plan({'title': 'T.', 'slides': [
    {'type': 'argument', 'title': 'Four brand categories lead what this audience buys.',
     'cards': [{'head': 'Fishing gear', 'body': 'Costa Sunglasses reaches 6.5% at index 5548.'}]}]}, 'Wicked Tuna')
body = plan['slides'][0]['cards'][0]['body']
check('index 5,548' in body, 'a deck plan card body leaves the plan pass with the separator', body)

print('[3] the deck builder')
import deck_builder as db  # noqa: E402

check(db._fmt_index(5548) == '5,548' and db._fmt_index('683') == '683' and db._fmt_index('') == '' and db._fmt_index('n/a') == 'n/a',
      'deck_builder._fmt_index', [db._fmt_index(v) for v in (5548, '683', '', 'n/a')])
src = (ROOT / 'deck_builder.py').read_text(encoding='utf-8')
check('f"Index {_fmt_index(idx)}"' in src and '_fmt_index(r.get("index"))' in src and '_fmt_index(top.get("index"))' in src
      and 'f"Index {int(_num(idx))}"' not in src, 'the group card, the INDEX column and the subhead print through it')
try:
    from pptx import Presentation  # type: ignore
    deck_plan = {'title': 'Wicked Tuna.', 'filename_stem': 'Wicked_Tuna', 'slides': [
        {'type': 'argument', 'ground': 'light', 'eyebrow': 'Buy', 'title': 'Four brand categories lead what this audience buys.',
         'sub': 'Each card is a group of brands.', '_reach': True,
         'cards': [{'head': 'Fishing gear', 'body': 'Costa Sunglasses and Yeti lead the group.', 'reach': '41.2%', 'index': 5548,
                    'times': '55.5x the US average', 'brands': ['Costa Sunglasses', 'Yeti']},
                   {'head': 'Outdoor apparel', 'body': 'Columbia and Carhartt lead the group.', 'reach': '38.4%', 'index': 212,
                    'times': '2.1x the US average', 'brands': ['Columbia', 'Carhartt']}]},
        {'type': 'bars', 'ground': 'light', 'eyebrow': 'Channel', 'title': 'They over-shop the tackle channel.',
         'sub': 'Penetration and index.', 'source': 'Profile IQ.', 'show_index': True, 'value_suffix': '%',
         'rows': [{'label': 'Costa Sunglasses', 'value': 6.5, 'index': 5548}, {'label': 'Yeti', 'value': 12.1, 'index': 683},
                  {'label': 'Bass Pro', 'value': 22.4, 'index': 412}, {'label': 'Cabelas', 'value': 18.3, 'index': 377}],
         'read': 'Costa leads on index.'}]}
    with tempfile.TemporaryDirectory() as td:
        outp = os.path.join(td, 'deck.pptx')
        db.render_insights_deck(deck_plan, outp, static_dir=str(ROOT / 'static'))
        text = '\n'.join(sh.text_frame.text for sl in Presentation(outp).slides for sh in sl.shapes if sh.has_text_frame)
    check('Index 5,548' in text and 'Index 212' in text, "the rendered group card reads 'Index 5,548'", re.findall(r'Index [\d,]+', text))
    check('5,548' in text and '5548' not in text, 'no bare four-digit index anywhere on the rendered pages',
          [ln for ln in text.splitlines() if '5548' in ln][:3])
except ImportError:
    print('  (python-pptx not installed: the render check is skipped)')

print('[4] deterministic chat lines')
from prometheus import crosstab as xt, category_reach as cre  # noqa: E402

check(xt._index_words(5548, '55.5x the US average') == 'index 5,548 (55.5x the US average)' and xt._index_words(119, 'x') == 'index 119 (x)'
      and xt._index_words(None, 'x') == '', 'the cross states its index with the separator', xt._index_words(5548, 'x'))
check("index {_fmt_index(r['index']) or r['index']}" in (ROOT / 'prometheus' / 'category_reach.py').read_text(encoding='utf-8'),
      'the category reach block prints through the formatter')
check(cre._fmt_index(2301) == '2,301', 'category_reach carries the formatter')

print('[5] the dashboard')
html = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')


def fn(name):
    i = html.index('function ' + name + '(')
    j = html.index('{', i)
    depth = 0
    while True:
        ch = html[j]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
        j += 1


check("return Math.round(n).toLocaleString('en-US');" in fn('formatIndex'), 'formatIndex separates thousands')
check(".toLocaleString('en-US')" in fn('_piqCatReachText') and ".toLocaleString('en-US')" in fn('_piqCatReachHtml'),
      'the Profiles page category line separates thousands')
check("formatIndex(item.index || 0).replace(/,/g, '')" in html, 'the row-level CSV index cell stays a plain number')
node = shutil.which('node')
if node:
    js = '\n'.join([
        "var window = { _piqCatReach: { cats: { 'ACCESSORIES': { reach: 41.2, genpop: 0.7, index: 5548, times: '55.5x the US average' },"
        " 'QSR': { reach: 74.4, genpop: 71.1, index: 105, times: 'in line with the US average' } } } };",
        fn('formatIndex'), fn('_piqCatNorm'), fn('_piqCatReachFor'), fn('_piqCatReachText'), fn('_piqCatReachHtml'),
        "console.log(JSON.stringify({a: formatIndex(5548), b: formatIndex(683), c: formatIndex('12450.4'), d: formatIndex(0), e: formatIndex(null),",
        "  t: _piqCatReachText(_piqCatReachFor('accessories')), h: _piqCatReachHtml(_piqCatReachFor('ACCESSORIES')), q: _piqCatReachText(_piqCatReachFor('QSR'))}));",
    ])
    r = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
    got = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else {}
    check(got.get('a') == '5,548' and got.get('b') == '683' and got.get('c') == '12,450' and got.get('d') == '' and got.get('e') == '',
          'formatIndex: 5,548 / 683 / 12,450 / nothing for no index', got or r.stderr[-200:])
    check(got.get('t') == '41.2% engage, index 5,548 (55.5x the US average)' and got.get('h') == '<b>41.2%</b> engage, index <b>5,548</b> (55.5x the US average)'
          and got.get('q') == '74.4% engage, index 105 (in line with the US average)', 'the category line reads index 5,548', got)
else:
    print('  (node not installed: the JS run is skipped)')

print('[6] the buy page header')
PROMPT = pma.INSIGHTS_DECK_SYSTEM_PROMPT
i = PROMPT.index('19. argument (ground=light): the buy.')
line19 = PROMPT[i:PROMPT.index('\n', i)]
check('set of brand categories' in line19 and 'never aisles, shelves, baskets' in line19 and 'categories or groups of brands' in line19,
      'the planner is told the page is a set of categories and to say so', line19[:200])
check('where the file and the journeys agree' not in PROMPT, 'the phrase the header echoed is gone from the planner')
check('"the file" or "the journeys"' in line19, 'and neither internal noun goes in a title')
check('thousands separator (5,548)' in PROMPT, 'the planner prints a four-digit index with its separator')
check('\u2014' not in line19 and '\u2013' not in line19, 'no dashes in the new instruction')


def _scripts_dir():
    spec = importlib.util.spec_from_file_location('design_skill_probe', str(ROOT / 'migration' / 'design_skill.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod            # the module's dataclasses look themselves up by name
    spec.loader.exec_module(mod)
    return mod.scripts_dir()


sd = _scripts_dir()
read_py = Path(sd) / 'readability_check.py' if sd else None
if read_py and read_py.is_file():
    def flags(text):
        r = subprocess.run([sys.executable, str(read_py), '--text', text], capture_output=True, text=True)
        return r.returncode, r.stdout
    rc, so = flags('Four aisles where the file and the journeys agree.')
    check(rc == 1 and 'store metaphor for a group' in so, 'the readability check flags the header that shipped', so[-240:])
    for ok_text in ('Four brand categories lead what this audience buys.', 'The snack aisle at Target leads the trip.',
                    'Basket size is 12% above the US average.'):
        rc, so = flags(ok_text)
        check('store metaphor' not in so, f'{ok_text!r} is not flagged as a store metaphor', so[-200:])
else:
    check(False, 'the design pack readability check is present', sd)

print()
if FAILS:
    print(f'{len(FAILS)} FAILED')
    for m in FAILS:
        print(' -', m)
    sys.exit(1)
print('ALL PASS')
