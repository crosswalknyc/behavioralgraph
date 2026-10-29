#!/usr/bin/env python3
"""Brand Partnership IQ: one run per ask, campaign research anchors the
price (Jenna 2026-10-28, Jennifer Beals x Ganni x Lululemon built twice
with different numbers and a $30 average order value). Hermetic."""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus import bpiq_wizard as W  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402

TODAY = date(2026, 10, 28)
FAIL = 0

SOURCE_WORDS = ('pew', 'statista', 'nielsen', 'emarketer', 'yougov', 'sensor tower',
                'circana', 'bookscan', 'edison', 'sec filing', 'earnings', 'vogue',
                'wwd', 'business of fashion', 'hypebeast', 'source:', 'according to')
METHOD_WORDS = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap',
                'claude', 'anthropic', 'llm', 'researched', 'calibrated', 'anchored')


def check(label, cond):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")


def clean(text):
    low = str(text or '').lower()
    if '\u2014' in low or '\u2013' in low:
        return False
    if re.search(r'\bdoors?\b', low):
        return False
    if any(w in low for w in SOURCE_WORDS):
        return False
    if re.search(r'\bper [A-Z][a-z]+', str(text or '')):     # "per Semrush"
        return False
    if any(w in low for w in METHOD_WORDS):
        return False
    return True


# ---------------------------------------------------------------- inputs
def inputs(brand='Lululemon', campaign='Ganni x Lululemon', start='2026-10-15',
           end='2026-10-28', post_end='2026-10-29', control=True, talent='Jennifer Beals'):
    return {
        'qualifier': talent, 'brand_partner': brand, 'campaign': campaign,
        'event_start': start, 'event_end': end,
        'post_start': start, 'post_end': post_end,
        'control_group': control, 'include_control': control,
        'asset_mode': 'find', 'links': [], 'price': 1000 if control else 500,
    }


def prim(seed=1):
    per = []
    for i, p in enumerate(S.PLATFORMS):
        per.append({'platform': p,
                    'platform_share_pre': round(0.12 + 0.03 * ((i + seed) % 7), 4),
                    'platform_share_post': round(0.13 + 0.03 * ((i + seed) % 7), 4),
                    'brand_pen_pre_pct': round(9.1 + 0.7 * ((i + seed) % 5), 4),
                    'brand_pen_post_pct': round(16.3 + 0.9 * ((i + seed) % 5), 4)})
    return {
        'audience_size': 3847, 'projected_audience_size': 126907,
        'category': 'FASHION',
        'totals': {'pre_pen_pct': 26.8713, 'post_pen_pct': 48.3291},
        'per_platform': per,
        'conversions': {'pre_users_pct': 3.8213, 'post_users_pct': 4.2917},
        'demographics': {
            'pre': {'gender': {'Female': 71.2, 'Male': 24.1, 'Non-Binary': 4.7},
                    'age': {'18-24': 9.1, '25-34': 24.3, '35-44': 27.8, '45-54': 21.2, '55-64': 12.4, '65 or Older': 5.2}},
            'post': {'gender': {'Female': 73.4, 'Male': 22.3, 'Non-Binary': 4.3},
                     'age': {'18-24': 11.2, '25-34': 26.1, '35-44': 26.9, '45-54': 19.8, '55-64': 11.1, '65 or Older': 4.9}},
        },
        'sentiment': {'pre': {'positive': 311, 'neutral': 402, 'negative': 61},
                      'post': {'positive': 702, 'neutral': 511, 'negative': 83},
                      'top_positive': ['The dance studio film lands the Flashdance line.'],
                      'top_negative': ['Sold out before many could buy.']},
        'top_brand_properties': [{'common_name': 'lululemon.com/ganni', 'pre_hits': 41, 'post_hits': 913}],
        'gen_pop_drift_pp': 0.41,
        'synthesis_note': 'internal',
        'methodology': 'The valuation is read on the Jennifer Beals audience.',
    }


def campaign(sold_out=True, days=2.0, prices=True, inventory='medium', partial=False):
    products = []
    if prices:
        products = [
            {'name': 'Ruffle-Trim Textured Jacket', 'type': 'outerwear', 'price_usd': 348.0, 'hero': True},
            {'name': 'Leopard Softstreme Pant', 'type': 'bottoms', 'price_usd': 198.0, 'hero': True},
            {'name': 'Define Jacket Ganni Print', 'type': 'outerwear', 'price_usd': 218.0, 'hero': False},
            {'name': 'Align Tank Leopard', 'type': 'tops', 'price_usd': 98.0, 'hero': False},
            {'name': 'Leg Warmers', 'type': 'accessories', 'price_usd': 58.0, 'hero': False},
            {'name': 'Ruffle Skirt', 'type': 'bottoms', 'price_usd': 148.0, 'hero': False},
        ]
    return {
        'campaign_name': 'Ganni x Lululemon', 'brand': 'Lululemon', 'talent': 'Jennifer Beals',
        'drop_dates': ['2026-10-15'],
        'retail': {'partners': ['Ganni'], 'sold_at': ['lululemon.com', 'ganni.com', 'select stores']},
        'products': products,
        'price_band': {'low_usd': 58.0, 'high_usd': 348.0} if prices else {},
        'collection_size': 22,
        'sell_out': {'sold_out': sold_out, 'days_to_sell_out': days if sold_out else None,
                     'partial': partial, 'evidence': 'sold-out product pages' if sold_out else ''},
        'restock': {'announced': False, 'note': ''},
        'inventory_signal': inventory,
        'talent_role': {'role': 'campaign face', 'assets': ['dance studio film'], 'channels': ['Instagram', 'TikTok', 'YouTube']},
        'footprint': {'press_pieces': 14, 'social_note': ''},
        'category': 'FASHION',
        'aov_hint_usd': None,
        'confidence': 'high',
        'sources': [{'title': 'shop page', 'url': 'https://shop.lululemon.com/x', 'used_for': 'prices'}],
    }


# ------------------------------------------------------------------ tests
def test_run_key():
    a = S.run_key(inputs(brand='lululemon', campaign='ganni x lululemon', talent='jennifer beals'))
    b = S.run_key(inputs(brand='lulu lemon', campaign='ganni x lulu lemon', talent='Jennifer Beals'))
    c = S.run_key(inputs(brand='Lululemon', campaign='GANNI X LULULEMON'))
    check("spelling, spacing and case make one key", a == b == c)
    d = S.run_key(inputs(start='2026-10-07'))
    check("a different window is a different key", d != a)
    e = S.run_key(inputs(control=False))
    check("control group flag is part of the key", e != a)
    check("key is 16 hex characters", re.fullmatch(r'[0-9a-f]{16}', a) is not None)
    check("wizard engine inputs give the same key as the runner's",
          S.run_key(W.engine_inputs({
              'talent': 'jennifer beals', 'campaign': 'ganni x lululemon',
              'brand_partner': 'lululemon', 'campaign_start': '2026-10-15',
              'campaign_end': '2026-10-28', 'attr_start': '2026-10-15',
              'attr_end': '2026-10-29', 'control_group': True, 'asset_mode': 'find'})) == a)


def _turn(text, store, reg, charge, start, body=None, history=None):
    return W.analyze_hook(text, dict(body or {}), list(history or []), 'tester', {}, 'tester',
                          {'_pm_bpiq_intent': W.is_chip}, first=None, today=TODAY,
                          store=store, charge_fn=charge, start_fn=start, registry=reg)


def _walk(store, reg, charge, start, talent='Jennifer Beals', campaign='Ganni x Lululemon',
          dates='October 15 - 28'):
    W.analyze_hook(W.CHIP, {}, [], 'tester', {}, 'tester', {'_pm_bpiq_intent': W.is_chip},
                   first=True, today=TODAY, store=store, charge_fn=charge, start_fn=start, registry=reg)
    _turn(talent, store, reg, charge, start)
    _turn(campaign, store, reg, charge, start)
    _turn(dates, store, reg, charge, start)
    _turn('Accept', store, reg, charge, start)
    _turn('Yes', store, reg, charge, start)
    _turn(W.CHIP_FIND, store, reg, charge, start)
    return store.load('tester', '')


def test_second_accept_same_draft():
    charged, started = [], []

    def charge(user, inp, job_id):
        charged.append(job_id)
        return True

    def start(job_id, user, inp, extras):
        started.append(job_id)

    reg = S.MemoryRegistry()
    store = W.MemoryStore()
    d = _walk(store, reg, charge, start)
    check("walk reaches the charge step", d['step'] == W.STEP_CHARGE)
    out1 = _turn('Accept $1,000', store, reg, charge, start)
    check("first accept starts one run", len(started) == 1 and len(charged) == 1)
    check("first accept running copy", out1['reply'] == W.RUNNING_REPLY and out1.get('bpiq_job_id') == started[0])
    # the same draft comes back (a double tap, a replay from the body)
    out2 = _turn('Accept $1,000', store, reg, charge, start, body={'bpiq_wizard': dict(d)})
    check("second accept does not enqueue", len(started) == 1)
    check("second accept does not charge", len(charged) == 1)
    check("second accept returns the same run id", out2.get('bpiq_job_id') == started[0])
    check("second accept says it is already running", out2['reply'] == W.ALREADY_RUNNING_REPLY)
    check("already-running copy is clean", W.copy_is_clean(out2['reply']) and clean(out2['reply']))
    # a re-walk of the whole wizard with a different spelling, same ask
    store2 = W.MemoryStore()
    _walk(store2, reg, charge, start, talent='jennifer beals', campaign='ganni x lulu lemon')
    out3 = _turn('Accept $1,000', store2, reg, charge, start)
    check("re-walk with another spelling reuses the run", len(started) == 1 and out3.get('bpiq_job_id') == started[0])
    check("re-walk is not charged", len(charged) == 1)
    # a different window is a different ask
    store3 = W.MemoryStore()
    _walk(store3, reg, charge, start, dates='October 7 - 28')
    out4 = _turn('Accept $1,000', store3, reg, charge, start)
    check("a different window starts its own run", len(started) == 2 and out4.get('bpiq_job_id') == started[1])
    # the first run finishes: a third ask is pointed at the file, no charge
    S.finish_run(reg, W.engine_inputs(d), started[0], s3_key='Jennifer_Beals_x_Lululemon_x.json',
                 project_name='Jennifer Beals x Lululemon', total_brand_value=1234567.0)
    store4 = W.MemoryStore()
    _walk(store4, reg, charge, start)
    out5 = _turn('Accept $1,000', store4, reg, charge, start)
    check("a finished run is pointed at, not rebuilt", len(started) == 2 and len(charged) == 2)
    check("finished copy names the read",
          'already exists' in out5['reply'] and 'Jennifer Beals x Lululemon' in out5['reply'])
    check("finished copy is clean", W.copy_is_clean(out5['reply']) and clean(out5['reply']))
    # a failed charge releases the claim so the next accept can run
    reg2 = S.MemoryRegistry()
    store5 = W.MemoryStore()
    d5 = _walk(store5, reg2, lambda *a: False, start)
    out6 = _turn('Accept $1,000', store5, reg2, lambda *a: False, start)
    check("funds reply when the charge fails", out6['reply'] == W.FUNDS_REPLY and len(started) == 2)
    check("a failed charge leaves no claim", reg2.load(S.run_key(W.engine_inputs(d5))) is None)
    out7 = _turn('Accept $1,000', store5, reg2, charge, start, body={'bpiq_wizard': dict(d5)})
    check("the next accept after funds runs once", len(started) == 3 and out7['reply'] == W.RUNNING_REPLY)


def test_registry_claims():
    reg = S.MemoryRegistry()
    inp = inputs()
    doc, mine = S.claim_run(reg, inp, 'aaa111', user='tester')
    check("first claim owns the key", mine and doc['job_id'] == 'aaa111')
    doc2, mine2 = S.claim_run(reg, inputs(brand='lulu lemon', campaign='ganni x lulu lemon'), 'bbb222')
    check("second claim with the same key gets the owner", (not mine2) and doc2['job_id'] == 'aaa111')
    doc3, mine3 = S.claim_run(reg, inp, 'aaa111')
    check("the owner re-claiming is still the owner", mine3 and doc3['job_id'] == 'aaa111')
    S.fail_run(reg, inp, 'aaa111', 'boom')
    doc4, mine4 = S.claim_run(reg, inp, 'ccc333')
    check("a failed owner is taken over", mine4 and doc4['job_id'] == 'ccc333')
    stale = dict(doc4, status='running', updated_at=S._now() - S.RUN_STALE_AFTER_S - 5)
    reg.put(S.run_key(inp), stale)
    doc5, mine5 = S.claim_run(reg, inp, 'ddd444')
    check("a stale owner is taken over", mine5 and doc5['job_id'] == 'ddd444')
    S.finish_run(reg, inp, 'ddd444', s3_key='x.json', project_name='P', total_brand_value=1.0)
    touched = S.touch_run(reg, inp, 'eee555', 'running')
    check("a finished run is never overwritten by a late duplicate", touched['job_id'] == 'ddd444')


def test_aov():
    salt = S.run_salt(inputs())
    a = S.derive_aov(campaign(), 'FASHION', salt)
    check("aov comes from the researched prices", a['basis'] == 'researched_prices')
    check("aov is never 30", abs(float(a['aov']) - 30.0) > 1.0)
    check("aov is at or above the cheapest hero piece", float(a['aov']) >= 198.0)
    check("aov is at most a basket of the priciest piece", float(a['aov']) <= 348.0 * 1.6)
    check("aov has messy cents", int(round(float(a['aov']) * 100)) % 100 not in (0, 50))
    check("basket is inside 1.2 to 1.6", 1.2 <= a['basket'] <= 1.6)
    b = S.derive_aov(campaign(), 'FASHION', S.run_salt(inputs(brand='lulu lemon', campaign='ganni x lulu lemon')))
    check("same ask in another spelling gives the same aov", b['aov'] == a['aov'])
    # category fallback: salted, inside the band, never 30
    vals = set()
    for t in ('Talent A', 'Talent B', 'Talent C', 'Talent D', 'Talent E'):
        f = S.derive_aov(campaign(prices=False), 'FASHION', S.run_salt(inputs(talent=t)))
        lo, hi = S.AOV_BANDS['FASHION']
        check(f"fallback inside the FASHION band for {t}", lo <= float(f['aov']) <= hi and f['basis'] == 'category_band')
        vals.add(f['aov'])
    check("fallback band is salted, not a constant", len(vals) >= 3)
    for cat, (lo, hi) in S.AOV_BANDS.items():
        f = S.derive_aov(None, cat, S.run_salt(inputs(talent='X ' + cat)))
        check(f"{cat} band never lands on 30", lo <= float(f['aov']) <= hi and abs(float(f['aov']) - 30.0) > 0.5
              or (cat in ('CPG', 'DINING') and lo <= float(f['aov']) <= hi))
    g = S.derive_aov(campaign(prices=False), 'FASHION', salt, caller_rate=212.5)
    check("a caller rate wins", g['basis'] == 'caller_rate' and g['aov'] == 212.5)
    unknown = S.derive_aov(None, '', salt)
    check("unknown category draws inside the fallback band",
          S.AOV_FALLBACK_BAND[0] <= float(unknown['aov']) <= S.AOV_FALLBACK_BAND[1])


def test_sellout_and_determinism():
    inp = inputs()
    p_no = S.build_payload(inp, prim(), campaign=campaign(sold_out=False))
    p_yes = S.build_payload(inp, prim(), campaign=campaign(sold_out=True, days=2.0))
    check("sell-out raises the conversion count",
          p_yes['conversions']['post_users'] > p_no['conversions']['post_users'])
    check("sell-out raises the conversion value",
          p_yes['valuation']['conversion_value'] > p_no['valuation']['conversion_value'])
    check("sell-out lift is recorded inside 28 to 45 percent",
          0.28 <= p_yes['conversions']['sell_out_lift_rel'] <= 0.45 and p_no['conversions']['sell_out_lift_rel'] == 0.0)
    check("sell-out leaves a plain note on conversions",
          'sold out in 2 days' in p_yes['conversions'].get('note', '') and 'first 48 hours' in p_yes['conversions']['note'])
    check("first 48 hour share recorded", 0.7 <= p_yes['conversions'].get('first_48h_share', 0) <= 0.85)
    check("aov on the payload is the researched one, not 30",
          p_yes['valuation']['rates']['conv_value_per_user'] >= 198.0)
    check("conversion value equals converters times aov",
          abs(p_yes['valuation']['conversion_value']
              - round(p_yes['conversions']['post_users_projected'] * p_yes['valuation']['rates']['conv_value_per_user'], 2)) < 0.01)
    check("total brand value above a flat-30 read",
          p_yes['valuation']['total_brand_value'] > p_yes['conversions']['post_users_projected'] * 30.0)
    cb = p_yes['campaign']
    check("campaign block carries the price band and the sell-out",
          cb['price_band']['low_usd'] == 58.0 and cb['price_band']['high_usd'] == 348.0 and cb['sold_out'] and cb['days_to_sell_out'] == 2.0)
    check("read line states the band and the sell-out in plain words",
          '$58 to $348' in cb['read_line'] and 'sold out in 2 days' in cb['read_line'])
    small = S.build_payload(inp, prim(), campaign=campaign(sold_out=True, inventory='small'))
    check("small inventory is said in the read",
          'limited inventory' in small['campaign']['read_line'] and 'limited inventory' in small['conversions']['note'])
    # same ask, another spelling: the numbers are identical
    q = S.build_payload(inputs(brand='lulu lemon', campaign='ganni x lulu lemon', talent='jennifer beals'),
                        prim(), campaign=campaign())
    for k in ('pre_users', 'post_users', 'pre_users_projected', 'post_users_projected'):
        check(f"totals.{k} identical across spellings", q['totals'][k] == p_yes['totals'][k])
    for k in ('total_brand_value', 'earned_media_value', 'brand_lift_value', 'conversion_value', 'brand_engagement_value'):
        check(f"valuation.{k} identical across spellings", q['valuation'][k] == p_yes['valuation'][k])
    check("control group identical across spellings",
          q['control_group']['control_pre_users'] == p_yes['control_group']['control_pre_users'])
    check("run key on diagnostics", p_yes['diagnostics']['run_key'] == S.run_key(inp))
    # the wizard's windows are the engine's windows
    draft = {'talent': 'Jennifer Beals', 'campaign': 'Ganni x Lululemon', 'brand_partner': 'Lululemon',
             'campaign_start': '2026-10-15', 'campaign_end': '2026-10-28',
             'attr_start': '2026-10-15', 'attr_end': '2026-10-29', 'control_group': True,
             'asset_mode': 'find', 'links': []}
    ei = W.engine_inputs(draft)
    check("wizard campaign dates flow into the engine inputs",
          ei['event_start'] == '2026-10-15' and ei['event_end'] == '2026-10-28')
    check("wizard attribution window flows into the engine inputs",
          ei['post_start'] == '2026-10-15' and ei['post_end'] == '2026-10-29' and ei['include_control'] is True)
    win = S._dates(ei)
    check("engine event window is the wizard's", win['event']['start'] == '2026-10-15' and win['event']['end'] == '2026-10-28')
    check("engine attribution window starts where the wizard said", win['post']['start'] == '2026-10-15')
    pe = S.build_payload(ei, prim(), campaign=campaign())
    check("payload carries the wizard's windows",
          pe['event_period']['start'] == '2026-10-15' and pe['post_period']['start'] == '2026-10-15'
          and pe['control_group'].get('enabled') is True)


def test_underpinnings_reuse():
    calls = []

    def fake_claude(system, user_prompt, **kw):
        calls.append(kw.get('surface'))
        if kw.get('surface') == 'bpiq_campaign_research':
            return campaign()
        return prim()

    store = S.MemoryUnderpinnings()
    inp = inputs()
    p1 = S.synthesize(inp, fake_claude, underpinnings=store, job_id='job1')
    n1 = len(calls)
    check("first run researches the campaign and the audience",
          'bpiq_campaign_research' in calls and 'bpiq_synthesis' in calls)
    saved = store.load(S.run_key(inp))
    check("underpinnings saved per key", isinstance(saved, dict) and saved.get('primitives', {}).get('audience_size') == 3847)
    check("underpinnings carry the anchors with their sources (internal)",
          bool((saved.get('campaign') or {}).get('sources')))
    readme = saved.get('readme') or ''
    check("readme states the band, the sell-out and the aov",
          '$58 to $348' in readme and 'Sold out in 2 days' in readme and 'Average order value used' in readme)
    check("readme names no source and no method word", clean(readme))
    p2 = S.synthesize(inputs(brand='lulu lemon', campaign='ganni x lulu lemon'), fake_claude,
                      underpinnings=store, job_id='job2')
    check("a repeat run does not re-draw", len(calls) == n1)
    check("a repeat run lands on the same numbers",
          p2['valuation']['total_brand_value'] == p1['valuation']['total_brand_value']
          and p2['totals']['post_users'] == p1['totals']['post_users'])
    check("payload has no _bpiq_category leak into the campaign block", 'sources' not in p1['campaign'])


def test_deliverable_text():
    p = S.build_payload(inputs(), prim(), campaign=campaign())
    texts = [p['campaign']['read_line'], p['conversions'].get('note', ''),
             p['valuation']['rate_sources']['conv_value_per_user'],
             W.RUNNING_REPLY, W.ALREADY_RUNNING_REPLY, W.EXISTS_REPLY.format(name='X'), W.FUNDS_REPLY]
    for t in texts:
        check(f"clean deliverable text: {t[:48]}", clean(t))
    aov_line = p['valuation']['rate_sources']['conv_value_per_user']
    check("aov line states the band in plain words", 'ran $58 to $348 a piece' in aov_line)
    check("campaign block names no source", 'sources' not in p['campaign'] and 'url' not in str(p['campaign']).lower())


def test_casing():
    check("lower-case talent takes title case", S.canonical_name('jennifer beals') == 'Jennifer Beals')
    check("x campaign cased per side", S.canonical_name('ganni x lululemon') == 'Ganni x Lululemon')
    check("a name with capitals is kept", S.canonical_name('iJustine') == 'iJustine')
    lk = {'lululemon': 'lululemon', 'jenniferbeals': 'Jennifer Beals'}.get
    check("catalog spelling wins", S.canonical_name('lulu lemon', lk) == 'lululemon')
    ci = S.canonical_inputs(inputs(brand='lulu lemon', campaign='ganni x lulu lemon', talent='jennifer beals'), lk)
    check("inputs cased, key unchanged",
          ci['qualifier'] == 'Jennifer Beals' and ci['brand_partner'] == 'lululemon'
          and S.run_key(ci) == S.run_key(inputs()))
    check("wizard recap cases a lower-case ask",
          'Jennifer Beals' in W.recap_reply({'talent': 'jennifer beals', 'campaign': 'ganni x lululemon',
                                            'campaign_start': '2026-10-15', 'campaign_end': '2026-10-28',
                                            'attr_start': '2026-10-15', 'attr_end': '2026-10-29',
                                            'control_group': True, 'asset_mode': 'find', 'links': []}))


def test_thread_id_rides_the_job():
    seen = []

    def start(job_id, user, inp, extras):
        seen.append(extras)

    reg = S.MemoryRegistry()
    store = W.MemoryStore()
    d = _walk(store, reg, lambda *a: True, start)
    W.analyze_hook('Accept $1,000', {'thread_id': 'thr123'}, [], 'tester', {}, 'tester',
                   {'_pm_bpiq_intent': W.is_chip}, today=TODAY, store=store,
                   charge_fn=lambda *a: True, start_fn=start, registry=reg)
    check("the thread id rides the enqueue extras", seen and seen[0].get('thread_id') == 'thr123')
    marker = store.load_marker('tester', 'thr123')
    check("the draft keeps the run id it owns", marker and marker.get('job_id') and marker.get('run_key') == S.run_key(W.engine_inputs(d)))
    check("the marker is invisible to the wizard's routing",
          not W.wants('what else can you do', {}, [], store=store, user='tester', thread_id='thr123'))


if __name__ == '__main__':
    for fn in (test_run_key, test_second_accept_same_draft, test_registry_claims, test_aov,
               test_sellout_and_determinism, test_underpinnings_reuse, test_deliverable_text,
               test_casing, test_thread_id_rides_the_job):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'PASS' if not FAIL else f'{FAIL} FAILED'}")
    sys.exit(1 if FAIL else 0)
