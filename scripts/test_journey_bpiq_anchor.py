#!/usr/bin/env python3
"""A Digital Journey on a partnership we already valued lands on the
valuation's counts (2026-11-17, Coach x WNBA).

Jenna: "make sure the values align with the brand partnership valuation."
The journey and the valuation read the same people in the same window, so
the people reached, the people who engaged with the brand and the people
who converted are one count in both products.

These checks run the real engine on a made-up partnership with a stubbed
model; no network.
"""
import os
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ['JOURNEY_CORPUS_ANCHORS'] = '0'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import journey_bpiq_anchor as A  # noqa: E402
from migration import journey_synthesis as JS  # noqa: E402

FAIL = 0


def check(name, ok, detail=''):
    global FAIL
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''))
    if not ok:
        FAIL += 1


def valuation_payload():
    return {
        'project_name': 'Harbor x Summit League', 'brand_partner': 'Harbor', 'qualifier_type': 'talent',
        'qualifier_value': ['Ana Reyes', 'Jo Park'], 'event_period': {'start': '2026-04-03', 'end': '2026-09-27', 'days': 178},
        'audience_size': 9143, 'projected_audience_size': 2_914_637,
        'totals': {'pre_users_projected': 588_214, 'post_users_projected': 903_457},
        'conversions': {'enabled': True, 'pre_users_projected': 71_346, 'post_users_projected': 104_823},
        'per_platform': [
            {'platform': 'Instagram', 'pre_users_projected': 240_113, 'post_users_projected': 391_256},
            {'platform': 'Direct (Brand Site)', 'pre_users_projected': 110_042, 'post_users_projected': 166_319},
            {'platform': 'TikTok', 'pre_users_projected': 98_317, 'post_users_projected': 158_774}],
        'demographics': {'post': {'gender': [{'value': 'Female', 'count': 61, 'percentage': 58.4127},
                                             {'value': 'Male', 'count': 39, 'percentage': 41.5873}]}},
        'sentiment': {'top_positive': [{'common_name': 'Opening night film', 'url': 'https://www.youtube.com/watch?v=abc', 'summary': 'x'}]},
        'top_brand_properties': [{'common_name': 'harbor.com home', 'hits': 9000, 'hits_projected': 2_869_113}],
        'valuation': {'incremental_users': 315_243, 'conversion_value': 104_823 * 180.0, 'rates': {'conv_value_per_user': 180.0}},
        'diagnostics': {'synthesis_note': 'internal'},
    }


def prim(with_ids: bool):
    head = ([{'id': 'reached', 'label': 'Saw it', 'doing': 'x', 'where': 'x', 'share_of_tam_pct': 2.1},
             {'id': 'engaged', 'label': 'Engaged', 'doing': 'x', 'where': 'x', 'kept_of_prior_pct': 12.0}] if with_ids else
            [{'id': 'social', 'label': 'Saw a post', 'doing': 'x', 'where': 'x', 'share_of_tam_pct': 4.4},
             {'id': 'liked', 'label': 'Liked a post', 'doing': 'x', 'where': 'x', 'kept_of_prior_pct': 7.0}])
    return {
        'customer_brand': 'Harbor', 'product': 'Harbor', 'category': 'brands', 'cover_title': 'Harbor x Summit League',
        'facts': [{'label': 'Old', 'value': '9,999,999 saw it and 123,456 bought.'}],
        'nest': head + [
            {'id': 'site', 'label': 'Went to harbor.com', 'doing': 'x', 'where': 'harbor.com', 'kept_of_prior_pct': 21.3},
            {'id': 'pdp', 'label': 'Opened a product page', 'doing': 'x', 'where': 'harbor.com', 'kept_of_prior_pct': 44.2},
            {'id': 'bag', 'label': 'Added to bag', 'doing': 'x', 'where': 'harbor.com', 'kept_of_prior_pct': 31.7},
            {'id': 'paid', 'label': 'Bought on harbor.com', 'doing': 'x', 'where': 'harbor.com', 'kept_of_prior_pct': 18.9}],
        'fork': {'enabled': True, 'abandoned_of_stage_pct': 71.3, 'retargeted_of_abandoned_pct': 52.4,
                 'returned_of_retargeted_pct': 33.8, 'paid_return_of_returned_pct': 41.2},
        'detours': [
            {'title': 'Discovery mix', 'kind': 'partition', 'of': f"stage:{'reached' if with_ids else 'social'}",
             'rows': [{'label': 'Player posts', 'pct': 61.3, 'doing': 'x'}, {'label': 'Press', 'pct': 38.7, 'doing': 'x'}]},
            {'title': 'Where the first post sat', 'kind': 'partition', 'of': f"stage:{'reached' if with_ids else 'social'}",
             'rows': [{'label': 'TikTok', 'pct': 70.0, 'doing': 'x'}, {'label': 'Instagram', 'pct': 30.0, 'doing': 'x'}]},
            {'title': 'What they bought', 'kind': 'partition', 'of': 'conversion',
             'rows': [{'label': 'The tote', 'pct': 58.2, 'doing': 'x'}, {'label': 'The charm', 'pct': 41.8, 'doing': 'x'}]},
            {'title': 'Last touch before the order', 'kind': 'partition', 'of': 'conversion',
             'rows': [{'label': 'Site revisit', 'pct': 55.1, 'doing': 'x'}, {'label': 'Email', 'pct': 44.9, 'doing': 'x'}]}],
        'clickstream': {'steps': [
            {'people': 2_900_000, 'date': '2026-04-05', 'surface': 'Social', 'action': 'Saw player content',
             'urls': [{'url': 'https://www.instagram.com/anareyes/', 'why': 'Player feed', 'share_of_step_pct': 55},
                      {'url': 'https://www.tiktok.com/@harbor', 'why': 'Brand clips', 'share_of_step_pct': 31}]},
            {'people': 2_100_000, 'date': '2026-05-02', 'surface': 'Instagram', 'action': 'Watched the opening night film',
             'urls': [{'url': 'https://www.instagram.com/reel/abc123/', 'why': 'The film', 'share_of_step_pct': 64},
                      {'url': 'https://www.harbor.com/film', 'why': 'Film hub', 'share_of_step_pct': 22}]},
            {'people': 1_400_000, 'date': '2026-06-11', 'surface': 'Search', 'action': 'Looked a piece up',
             'urls': [{'url': 'https://www.google.com/search?q=harbor+tote', 'why': 'Piece query', 'share_of_step_pct': 48}]},
            {'people': 330_000, 'date': '2026-07-09', 'surface': 'harbor.com', 'action': 'Opened a product page',
             'urls': [{'url': 'https://www.harbor.com/products/tote', 'why': 'Tote page', 'share_of_step_pct': 52}]},
            {'people': 104_823, 'date': '2026-08-14', 'surface': 'harbor.com', 'action': 'Ordered',
             'urls': [{'url': 'https://www.harbor.com/checkout', 'why': 'Order flow', 'share_of_step_pct': 71}]}]},
        'who_they_are': {'gender': [{'label': 'Female', 'pct': 50.0}, {'label': 'Male', 'pct': 50.0}]},
        'the_read': {'cards': [{'title': 'Plain', 'body': 'Player posts did the work.'}], 'moves': []},
    }


def run(with_ids: bool):
    val = A.from_payload('brand-partnership-iq/Harbor_x_Summit_League.json', valuation_payload())
    real = A.find_valuation
    A.find_valuation = lambda inputs, anchors=None, s3=None: val
    try:
        calls = []

        def cj(system, user, **kw):
            calls.append((kw.get('surface'), system, user))
            return prim(with_ids) if str(kw.get('surface') or '').startswith('journey_synthesis') else {}
        payload = JS.synthesize({'subject': 'Harbor x Summit League', 'platform': 'harbor.com', 'journey_kind': 'purchase',
                                 'conversion_event': 'bought on harbor.com'}, cj, created_by='test')
    finally:
        A.find_valuation = real
    return val, payload, calls


def main():
    val = A.from_payload('k', valuation_payload())
    check('valuation reads the three shared counts', (val['reached'], val['engaged'], val['converted']) == (2_914_637, 903_457, 104_823))
    check('valuation reads the year before and the growth', (val['engaged_before'], val['added']) == (588_214, 315_243))
    check('valuation reads who they are', val['who']['gender'][0] == {'label': 'Female', 'pct': 58.4127})
    bad = valuation_payload(); bad['conversions']['enabled'] = False
    check('a valuation with no conversions does not anchor', A.from_payload('k', bad) is None)

    for with_ids in (True, False):
        tag = 'ids followed' if with_ids else 'ids ignored'
        val, p, calls = run(with_ids)
        j = p['fragrance_shop_journey']
        sp = j['spine']
        by = {s['id']: s['accounts'] for s in sp}
        check(f'{tag}: reached lands on the valuation', by.get('reached') == 2_914_637, by)
        check(f'{tag}: engaged lands on the valuation', by.get('engaged') == 903_457, by)
        check(f'{tag}: the order lands on the valuation', sp[-1]['accounts'] == 104_823, sp[-1]['accounts'])
        accs = [s['accounts'] for s in sp]
        check(f'{tag}: the nest still narrows', all(a > b for a, b in zip(accs, accs[1:])), accs)
        fk = {f['id']: f['accounts'] for f in j['fork']}
        check(f'{tag}: the fork closes on the order', fk['paid_first'] + fk['paid_return'] == 104_823 and fk['paid_first'] + fk['abandoned'] == sp[-2]['accounts'], fk)
        titles = [d['title'] for d in j['detours']]
        check(f'{tag}: no whole-market buyer tables', not any(t.startswith(('The rest of the social', 'Every online')) for t in titles), titles)
        eng = next((d for d in j['detours'] if d['title'] == 'Where they engaged with Harbor'), None)
        check(f'{tag}: where they engaged carries the valuation rows',
              bool(eng) and {r['label']: r['accounts'] for r in eng['rows']} == {'Instagram': 391_256, 'Direct (Brand Site)': 166_319, 'TikTok': 158_774})
        grow = next((d for d in j['detours'] if d['title'] == 'Growth in the Harbor audience'), None)
        check(f'{tag}: growth sums to the engaged people', bool(grow) and sum(r['accounts'] for r in grow['rows']) == 903_457)
        for d in j['detours']:
            if d.get('kind') == 'partition' and d.get('rows'):
                tot = sum(r['accounts'] for r in d['rows'])
                check(f"{tag}: partition sums [{d['title']}]", tot == d.get('base'), (tot, d.get('base')))
        first = next((d for d in j['detours'] if d['title'] == 'First Harbor content they saw'), None)
        check(f'{tag}: brand content divides the engaged people, not the reached',
              bool(first) and first.get('base') == 903_457 and sum(r['accounts'] for r in first['rows']) == 903_457
              and [r['label'] for r in first['rows']] == ['Player posts', 'Press'], first and (first.get('base'), first['rows']))
        check(f'{tag}: no first-surface table beside the valuation platforms', not any('first post sat' in t.lower() for t in titles), titles)
        bought = [d for d in j['detours'] if d['title'] == 'What they bought first']
        check(f'{tag}: one bought-first table, the research one', len(bought) == 1 and [r['label'] for r in bought[0]['rows']] == ['The tote', 'The charm'])
        steps = (p.get('clickstream') or {}).get('steps') or []
        check(f'{tag}: url block has steps', len(steps) >= 4, len(steps))
        check(f'{tag}: no url step after the first holds more than the engaged people',
              all(s['people'] <= 903_457 for s in steps[1:]), [s['people'] for s in steps])
        check(f'{tag}: url steps still narrow', all(a['people'] > b['people'] for a, b in zip(steps, steps[1:])), [s['people'] for s in steps])
        caps = {'instagram.com': 391_256, 'tiktok.com': 158_774}
        bad = [(u['url'], u['people']) for i, s in enumerate(steps) for u in s['urls'] for h, c in caps.items()
               if h in u['url'] and (i > 0 or 'harbor' in u['url']) and u['people'] >= c]
        check(f'{tag}: people on a brand social url stay under the valuation platform count', not bad, bad)
        check(f'{tag}: url people never exceed their step', all(u['people'] <= s['people'] for s in steps for u in s['urls']))
        # a settled journey keeps its sections: the PDF, the short lines and a replay never bring the whole-market tables back
        from migration import journey_gold as JG, journey_pdf as JP, journey_pdf_partnership as PV
        import copy as _copy
        again = JG.apply(_copy.deepcopy(p))
        check(f'{tag}: a second standard pass leaves the settled journey alone',
              [d['title'] for d in again['fragrance_shop_journey']['detours']] == titles)
        check(f'{tag}: the journey reads as standard to the delivery paths', JG.is_gold(p))
        lines = JG.ship_lines(p)
        blob_lines = ' '.join(lines)
        check(f'{tag}: short lines carry the three valuation counts',
              all(x in blob_lines for x in ('2,914,637', '903,457', '104,823')), blob_lines[:300])
        check(f'{tag}: short lines do not claim a 30-day window', '30 days of' not in blob_lines and 'saw a post' not in blob_lines, blob_lines[:300])
        check(f'{tag}: short lines carry the exact dollars', '$18,868,140.00' in blob_lines, blob_lines[:400])
        pdf = PV.build_pdf(p)
        check(f'{tag}: the partnership PDF renders', pdf[:4] == b'%PDF' and len(pdf) > 20000, len(pdf))
        for banned in ('synth', 'modeled', 'estimated', 'valuation_anchor', '\u2014', ' door'):
            check(f'{tag}: short lines carry no {banned!r}', banned not in blob_lines.lower())
        check(f'{tag}: who they are is the valuation', p['who_they_are']['gender'][0]['pct'] == 58.4127)
        check(f'{tag}: the time table spans the window', any('More than 90 days' in [r['label'] for r in d['rows']] for d in j['detours']))
        facts = ' '.join(f['value'] for f in p['facts'])
        check(f'{tag}: facts quote the held counts', all(n in facts for n in ('2,914,637', '903,457', '104,823')), facts)
        check(f'{tag}: the order value prints exact', '$180.00' in facts and '$18,868,140.00' in facts, facts)
        check(f'{tag}: the stale research fact is gone', '9,999,999' not in facts and '123,456' not in facts)
        check(f'{tag}: words quote only counts the file holds', not A.unmatched_counts(p, val), A.unmatched_counts(p, val)[:4])
        check(f'{tag}: kpis follow', p['kpis']['total_users'] == 104_823)
        check(f'{tag}: the window is the valuation window', (p['meta']['start_date'], p['meta']['end_date']) == ('2026-04-03', '2026-09-27'))
        research = next(c for c in calls if str(c[0]).startswith('journey_synthesis'))
        check(f'{tag}: the research is told the counts are binding', 'THE PARTNERSHIP VALUATION IS BINDING' in research[1] and '2914637' in research[2])
        blob = str(p).lower()
        check(f'{tag}: nothing internal in the file words', 'synthesis_note' not in str(p['facts']) and 'internal' not in facts.lower())
        check(f'{tag}: norms pass stood down', not (p['meta'].get('norms') or {}).get('band'))

    # no valuation: the build is untouched by this module
    os.environ['JOURNEY_BPIQ_ANCHOR'] = '0'
    check('kill switch stands the anchor down', A.find_valuation({'valuation_key': 'x'}) is None)
    os.environ['JOURNEY_BPIQ_ANCHOR'] = '1'
    p2 = JS.build_journey({'subject': 'Harbor', 'platform': 'harbor.com', 'journey_kind': 'purchase'}, prim(True), created_by='test')
    check('a journey with no valuation carries no anchor', 'valuation_anchor' not in p2['meta'])
    check('finish is a no-op without the anchor', A.finish(p2, val) is p2 and not any(d['title'].startswith('Where they engaged') for d in p2['fragrance_shop_journey']['detours']))

    print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAILED'}")
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
