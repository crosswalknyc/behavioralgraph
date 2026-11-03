#!/usr/bin/env python3
"""The Flywheel wizard and the build behind it (Jenna 2026-11-03, after
Grace Yin's Gilmore Girls on Prime Video attempt died on a cosmetic
share check).

  1. the wizard asks the entry point, the ecosystem, the date range, in
     that order, and only for what the message did not already carry
  2. a start phrase fills ahead; a cancel clears; the confirm card
     carries the engine inputs the build reads
  3. the builder never refuses a reasoned frame: the primitives that
     killed the live run build; every hygiene rule holds on the output
  4. the research answer is retried once; a frame with no cohort or no
     rows is the one upstream failure

Run:  REGRESSION_TEST_MODE=1 python3 scripts/test_flywheel_wizard.py
"""
import csv
import io
import json
import os
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from prometheus import flywheel_wizard as W          # noqa: E402
from migration import flywheel_synthesis as FW      # noqa: E402

TODAY = '2026-11-03'
FAILS = []


def check(name, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + name + (f'  ({detail})' if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------- primitives
# The shape the research answer takes (the live frame of 2026-11-03 had a Whole Foods row at 16.8300 and a
# duplicate pair; the frame below carries both defects plus a gated row that fell, a flat row that jumped and
# two research rows, so every repair fires).
PRIM = {
    'cohort': 68473, 'cohort_label': 'US people who watched Gilmore Girls on Prime Video',
    'entry_surface': 'prime_video_gilmore_girls', 'entry_label': 'Prime Video: Gilmore Girls (entry play)',
    'ecosystem_ceiling_pct': 88.0,
    'splits': {'enabled': True, 'a_key': 'new_signup', 'a_label': 'First-ever Prime signup', 'a_pct': 41.37,
               'b_key': 'reactivated_dormant', 'b_label': 'Reactivated lapsed Prime', 'a_note': 'younger', 'b_note': 'older'},
    'touchpoints': [
        {'surface': 'amazon_retail', 'label': 'Amazon retail', 'family': 'retail', 'pre_pct': 54.83, 'post_pct': 79.15, 'a_share_of_row_pct': 36.2, 'gated': True, 'research_row': False},
        {'surface': 'whole_foods', 'label': 'Whole Foods', 'family': 'grocery', 'pre_pct': 11.29, 'post_pct': 16.83, 'a_share_of_row_pct': 22.0, 'gated': True, 'research_row': False},
        {'surface': 'amazon_fresh', 'label': 'Amazon Fresh', 'family': 'grocery', 'pre_pct': 7.46, 'post_pct': 16.83, 'a_share_of_row_pct': 21.0, 'gated': True, 'research_row': False},
        {'surface': 'audible', 'label': 'Audible', 'family': 'audio', 'pre_pct': 12.37, 'post_pct': 11.90, 'a_share_of_row_pct': 44.0, 'gated': True, 'research_row': False},
        {'surface': 'imdb', 'label': 'IMDb', 'family': 'reading', 'pre_pct': 29.74, 'post_pct': 23.11, 'a_share_of_row_pct': 51.0, 'gated': False, 'research_row': True},
        {'surface': 'goodreads', 'label': 'Goodreads', 'family': 'reading', 'pre_pct': 8.13, 'post_pct': 7.90, 'a_share_of_row_pct': 40.0, 'gated': False, 'research_row': True},
        {'surface': 'alexa_app', 'label': 'Alexa app', 'family': 'devices', 'pre_pct': 26.41, 'post_pct': 41.00, 'a_share_of_row_pct': 30.0, 'gated': False, 'research_row': False},
        {'surface': 'ring_app', 'label': 'Ring app', 'family': 'devices', 'pre_pct': 9.62, 'post_pct': 9.87, 'a_share_of_row_pct': 25.0, 'gated': False, 'research_row': False},
        {'surface': 'twitch', 'label': 'Twitch', 'family': 'video', 'pre_pct': 95.0, 'post_pct': 99.0, 'a_share_of_row_pct': 60.0, 'gated': True, 'research_row': False},
        {'surface': 'zappos', 'label': 'Zappos', 'family': 'retail', 'pre_pct': 0.0, 'post_pct': 0.0, 'a_share_of_row_pct': 50.0, 'gated': False, 'research_row': False},
        {'surface': 'amazon_retail', 'label': 'Amazon retail dup', 'family': 'retail', 'pre_pct': 1.0, 'post_pct': 2.0, 'a_share_of_row_pct': 50.0, 'gated': True, 'research_row': False},
        {'label': 'no surface id', 'family': 'other', 'pre_pct': 3.0, 'post_pct': 4.0},
    ],
    'unions': {'any_owned_pre_pct': 40.0, 'any_owned_post_pct': 50.0, 'checkout_pre_pct': 60.0, 'checkout_post_pct': 70.0},
    'checkout_what': [
        {'surface': 'core_retail_everyday', 'label': 'Core retail: household, consumables', 'pct_of_checkout': 61.47, 'a_share_of_row_pct': 36.18},
        {'bucket': 'series_merch', 'label': 'Series merch', 'share_of_checkout_pct': 12.4, 'a_share_of_row_pct': 60.0},   # alias keys
        {'label': 'Grocery (Whole Foods and Fresh orders)', 'pct_of_checkout': 18.2},                                       # id from the label
        'not a row',
    ],
}

WIZARD_INPUTS = {
    'subject': 'Gilmore Girls', 'platform': 'Prime Video', 'entry_point': 'watched Gilmore Girls on Prime Video',
    'captured_action': 'watched Gilmore Girls on Prime Video', 'cohort_count': None, 'ecosystem': 'Amazon',
    'conversion_event': 'any Amazon-owned checkout, sign-up or first use inside 30 days of the entry',
    'pre_days': 30, 'post_days': 30, 'splits_hint': None, 'start_date': '2025-11-03', 'end_date': '2026-11-03', 'notes': '',
}


def rows_of(csv_text):
    return list(csv.DictReader(io.StringIO(csv_text)))


# ------------------------------------------------------------------ tests
def test_wizard_steps():
    print('1. the three questions, one at a time')
    st = W.MemoryStore()
    r = W.analyze_hook('Build a Flywheel', {}, [], 'graceyin', None, 'graceyin', {}, first=True, store=st, today=TODAY)
    check('the chip opens on the entry point', r and r.get('fw_collect') and r['reply'] == W.ASK_ENTRY)
    r = W.analyze_hook('watched gilmore girls on APV', {'fw_inputs': True}, [], 'graceyin', None, 'graceyin', {}, first=False, store=st, today=TODAY)
    check('the entry is read and cased; the ecosystem is asked next', r and r.get('fw_collect') and 'Gilmore Girls on Prime Video' in r['reply'] and 'ecosystem' in r['reply'].lower())
    check('APV means Amazon: the question proposes it and says why', r and 'Prime Video belongs to Amazon' in r['reply'] and 'Go with Amazon, or name a different company' in r['reply'])
    check('chips: the proposed company, Other, Cancel', r and r['followups'] == ['Amazon', W.OTHER_CHIP, 'Cancel'])
    r = W.analyze_hook('Amazon', {'fw_inputs': True}, [], 'graceyin', None, 'graceyin', {}, first=False, store=st, today=TODAY)
    check('then the date range, with the default chip', r and r.get('fw_collect') and r['reply'] == W.ASK_DATES and r['followups'][0] == 'Trailing 12 months')
    r = W.analyze_hook('Trailing 12 months', {'fw_inputs': True}, [], 'graceyin', None, 'graceyin', {}, first=False, store=st, today=TODAY)
    check('the confirm card carries the price and the run chip', r and r.get('fw_confirm_payload') and '$500' in r['reply'] and r['followups'] == [W.RUN_CHIP, 'Cancel'])
    p = (r or {}).get('fw_confirm_payload') or {}
    check('engine inputs: entry, subject, platform, ecosystem, window',
          p.get('entry_point') == 'watched Gilmore Girls on Prime Video' and p.get('subject') == 'Gilmore Girls' and p.get('platform') == 'Prime Video'
          and p.get('ecosystem') == 'Amazon' and p.get('start_date') == '2025-11-03' and p.get('end_date') == TODAY and p.get('splits_hint') is None, json.dumps(p)[:200])
    check('the draft is cleared once the card is out', st.load('graceyin') is None)
    check('the card reads before the entry against after it', 'before the entry' in r['reply'] and 'after it' in r['reply'])


def test_fill_ahead_and_cancel():
    print('2. fill ahead, framing, cancel')
    st = W.MemoryStore()
    r = W.analyze_hook('I tried to run a flywheel for Gilmore girls on PV', {}, [], 'u', None, 'u', {}, first=True, store=st, today=TODAY)
    check('Grace\'s own phrasing lands on the ecosystem step with Amazon proposed', r and r.get('fw_collect') and 'Amazon ecosystem' in r['reply'] and 'Gilmore Girls on Prime Video' in r['reply'] and r['followups'][0] == 'Amazon')
    r = W.analyze_hook('run a flywheel for people who watched gilmore girls on APV, Amazon, trailing 12 months', {}, [], 'u2', None, 'u2', {}, first=True, store=W.MemoryStore(), today=TODAY)
    check('one message with all three answers goes straight to the card', r and r.get('fw_confirm_payload') and r['fw_confirm_payload']['ecosystem'] == 'Amazon')
    r = W.analyze_hook('can you build a flywheel on Reacher on Prime Video for the Amazon ecosystem in calendar 2025', {}, [], 'u3', None, 'u3', {}, first=True, store=W.MemoryStore(), today=TODAY)
    p = (r or {}).get('fw_confirm_payload') or {}
    check('"the Amazon ecosystem" and "calendar 2025" are read', p.get('ecosystem') == 'Amazon' and p.get('start_date') == '2025-01-01' and p.get('end_date') == '2025-12-31', json.dumps(p)[:160])
    r = W.analyze_hook('new flywheel: subscribed to Peacock, NBCUniversal ecosystem, March 1, 2026 to August 31, 2026', {}, [], 'u4', None, 'u4', {}, first=True, store=W.MemoryStore(), today=TODAY)
    p = (r or {}).get('fw_confirm_payload') or {}
    check('a sign-up entry on another ecosystem with explicit dates', p.get('entry_point') == 'subscribed to Peacock' and p.get('ecosystem') == 'NBCUniversal' and p.get('start_date') == '2026-03-01' and p.get('end_date') == '2026-08-31')
    st = W.MemoryStore()
    W.analyze_hook('Build a Flywheel', {}, [], 'u5', None, 'u5', {}, first=True, store=st, today=TODAY)
    W.analyze_hook('bought a Ring doorbell on Amazon', {'fw_inputs': True}, [], 'u5', None, 'u5', {}, first=False, store=st, today=TODAY)
    r = W.analyze_hook('Cancel', {'fw_inputs': True}, [], 'u5', None, 'u5', {}, first=False, store=st, today=TODAY)
    check('cancel clears the draft and says nothing was charged', r and r['reply'] == W.CANCEL_REPLY and st.load('u5') is None)
    r = W.analyze_hook('what did Reacher do on Prime Video last year', {}, [], 'u6', None, 'u6', {}, first=None, store=W.MemoryStore(), today=TODAY)
    check('a question that is not a flywheel ask with no draft open is left alone', r is None)
    st = W.MemoryStore()
    W.analyze_hook('Build a Flywheel', {}, [], 'u7', None, 'u7', {}, first=True, store=st, today=TODAY)
    r = W.analyze_hook('ok', {'fw_inputs': True}, [], 'u7', None, 'u7', {}, first=False, store=st, today=TODAY)
    check('an answer that names no entry is asked again, plainly', r and r.get('fw_collect') and 'entry point' in r['reply'].lower())
    # the ecosystem step: Other asks for a name (never a company called Other); a bare yes takes the proposal;
    # an entry with no service behind it gets the open question with the common four plus Other
    st = W.MemoryStore()
    W.analyze_hook('run a flywheel for watched Gilmore Girls on Prime Video', {}, [], 'u8', None, 'u8', {}, first=True, store=st, today=TODAY)
    r = W.analyze_hook('Other', {'fw_inputs': True}, [], 'u8', None, 'u8', {}, first=False, store=st, today=TODAY)
    check('Other asks for the company by name, with only Cancel left', r and r['reply'] == W.ASK_ECOSYSTEM_OTHER and r['followups'] == ['Cancel'] and not (st.load('u8') or {}).get('ecosystem'))
    r = W.analyze_hook('Walmart', {'fw_inputs': True}, [], 'u8', None, 'u8', {}, first=False, store=st, today=TODAY)
    check('then the named company is taken and the dates are asked', r and r['reply'] == W.ASK_DATES and (st.load('u8') or {}).get('ecosystem') == 'Walmart')
    st = W.MemoryStore()
    W.analyze_hook('run a flywheel for watched Reacher on Prime Video', {}, [], 'u9', None, 'u9', {}, first=True, store=st, today=TODAY)
    r = W.analyze_hook('yes', {'fw_inputs': True}, [], 'u9', None, 'u9', {}, first=False, store=st, today=TODAY)
    check('a bare yes takes the proposed Amazon', r and r['reply'] == W.ASK_DATES and (st.load('u9') or {}).get('ecosystem') == 'Amazon')
    st = W.MemoryStore()
    W.analyze_hook('Build a Flywheel', {}, [], 'u10', None, 'u10', {}, first=True, store=st, today=TODAY)
    r = W.analyze_hook('bought a Peloton bike', {'fw_inputs': True}, [], 'u10', None, 'u10', {}, first=False, store=st, today=TODAY)
    check('no service behind the entry: the open question, four common companies, Other, Cancel',
          r and 'Which ecosystem' in r['reply'] and r['followups'] == list(W.ECO_CHIPS) + [W.OTHER_CHIP, 'Cancel'])
    r = W.analyze_hook('Something else', {'fw_inputs': True}, [], 'u10', None, 'u10', {}, first=False, store=st, today=TODAY)
    check('"something else" is also a request to name one', r and r['reply'] == W.ASK_ECOSYSTEM_OTHER)
    check('the running line names the entry and the ecosystem', 'Gilmore Girls' in W.running_reply(WIZARD_INPUTS) and 'Amazon-owned' in W.running_reply(WIZARD_INPUTS))


def test_builder_repairs():
    print('3. the builder repairs, never refuses')
    csv_text, name, summary = FW.build_flywheel_study(WIZARD_INPUTS, PRIM)
    rows = rows_of(csv_text)
    comp = [r for r in rows if r['table'] == 'flywheel_compare']
    shares = [r['share_of_cohort_pct'] for r in comp]
    check('builds from the defective frame', bool(comp) and summary['cohort'] == 68473)
    check('no .XX00 share, no duplicate 4dp share', len(shares) == len(set(shares)) and not any(s.endswith('00') and s not in ('0.0000', '100.0000') for s in shares), str(shares))
    by = {r['surface']: r for r in comp}
    check('the duplicate surface is dropped; a row with a label and no id keeps its row under a derived id',
          'no_surface_id' in by and sum(1 for r in comp if r['surface'] == 'amazon_retail') == 1)
    check('the gated row that fell now rises (Audible)', float(by['audible']['lift_pp']) > 0)
    check('the flat row that jumped is held in the flat band (Alexa)', abs(float(by['alexa_app']['lift_pp'])) < 1.0)
    check('exactly one row falls by more than a point (the research row); the second flagged row is flattened',
          sum(1 for r in comp if float(r['lift_pp']) <= -1.0) == 1 and float(by['imdb']['lift_pp']) < -1.0 and abs(float(by['goodreads']['lift_pp'])) < 1.0)
    check('the ceiling holds and the gated row still rises (Twitch at 95/99)', float(by['twitch']['share_of_cohort_pct']) <= 88.0 and float(by['twitch']['pre_180d_share_of_cohort_pct']) <= 88.0 and float(by['twitch']['lift_pp']) > 0,
          f"{by['twitch']['pre_180d_share_of_cohort_pct']} -> {by['twitch']['share_of_cohort_pct']}")
    ou, cu = by['any_owned_union'], by['checkout_union']
    members = [r for r in comp if r['surface'] not in ('any_owned_union', 'checkout_union', 'prime_video_gilmore_girls')]
    check('the any-owned union sits above every member row', int(ou['accounts']) > max(int(r['accounts']) for r in members) and int(ou['pre_180d_accounts']) > max(int(r['pre_180d_accounts']) for r in members))
    check('the checkout union sits under it', int(cu['accounts']) < int(ou['accounts']) and int(cu['pre_180d_accounts']) <= int(ou['pre_180d_accounts']))
    check('the entry row reads as the entry point, plainly', by['prime_video_gilmore_girls']['surface_label'] == 'watched Gilmore Girls on Prime Video')
    check('the cohort row names the people who did the entry', any(r['surface'] == 'captured_cohort' and r['surface_label'] == 'US people who watched Gilmore Girls on Prime Video' for r in rows))
    check('no split rows when the wizard sent no split hint', not any(r['surface'] in ('new_signup', 'reactivated_dormant') for r in rows))
    check('the window reads before they did it against after', 'before they watched Gilmore Girls on Prime Video vs 30 days after' in summary['window'])
    check('no em dashes anywhere in the file', '\u2014' not in csv_text)
    check('the study name carries the ecosystem and the date', name.startswith('Gilmore_Girls_Amazon_Flywheel_'))
    cw = [r for r in rows if r['table'] == 'checkout_what']
    check('checkout rows tolerate alias keys and derive an id from the label', [r['surface'] for r in cw] == ['core_retail_everyday', 'series_merch', 'grocery_whole_foods_and_fresh_orders'], str([r['surface'] for r in cw]))
    # the live frame that failed, when it is on disk
    live = '/tmp/fw_prim.json'
    if os.path.exists(live):
        try:
            FW.build_flywheel_study(dict(WIZARD_INPUTS, splits_hint='new vs reactivated'), json.load(open(live)))
            check('the frame that killed the live run builds', True)
        except Exception as exc:
            check('the frame that killed the live run builds', False, str(exc))
    # the splits path (a hint given) still partitions the cohort
    csv2, _n, _s = FW.build_flywheel_study(dict(WIZARD_INPUTS, splits_hint='new vs reactivated'), PRIM)
    rows2 = rows_of(csv2)
    coh = {r['surface']: int(r['accounts']) for r in rows2 if r['table'] == 'cohort'}
    check('with a hint the two splits partition the cohort', coh.get('new_signup', 0) + coh.get('reactivated_dormant', 0) == coh.get('captured_cohort'))


def test_upstream_failure_and_retry():
    print('4. the one upstream failure; one retry')
    calls = {'n': 0}

    def empty_then_good(system, user_prompt, **kw):
        calls['n'] += 1
        return {} if calls['n'] == 1 else PRIM

    csv_text, name, summary = FW.synthesize(WIZARD_INPUTS, empty_then_good)
    check('an empty research answer is retried once, then builds', calls['n'] == 2 and summary['cohort'] == 68473)
    calls['n'] = 0

    def always_empty(system, user_prompt, **kw):
        calls['n'] += 1
        return {}

    try:
        FW.synthesize(WIZARD_INPUTS, always_empty)
        check('two empty answers are the upstream failure', False)
    except RuntimeError:
        check('two empty answers are the upstream failure', calls['n'] == 2)
    try:
        FW.build_flywheel_study(WIZARD_INPUTS, dict(PRIM, cohort=0))
        check('no cohort is the upstream failure', False)
    except RuntimeError:
        check('no cohort is the upstream failure', True)
    seen = {}

    def capture(system, user_prompt, **kw):
        seen['prompt'] = json.loads(user_prompt)
        seen['system'] = system
        return PRIM

    FW.synthesize(WIZARD_INPUTS, capture)
    check('the research input carries the entry point and the platform', seen['prompt'].get('entry_point') == 'watched Gilmore Girls on Prime Video' and seen['prompt'].get('platform') == 'Prime Video')
    check('the research asks for every owned surface', 'EVERY company, service or product' in seen['system'] and 'EVERY OWNED SURFACE IS A ROW' in seen['system'])


if __name__ == '__main__':
    test_wizard_steps()
    test_fill_ahead_and_cancel()
    test_builder_repairs()
    test_upstream_failure_and_retry()
    print()
    if FAILS:
        print(f'{len(FAILS)} failing: ' + '; '.join(FAILS))
        sys.exit(1)
    print('all flywheel wizard checks pass')
