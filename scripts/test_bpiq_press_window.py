#!/usr/bin/env python3
"""Brand Partnership IQ: a press piece published before the campaign window
counts only the reads shown inside the window, and a story's in-window
reads cannot be shown, so it counts nobody (Jenna 2026-11-24).

Emi Hill's Zarna Garg x Maybelline read counted three stories from early
August toward a campaign that ran October 5 to November 2. The pasted posts
already follow this rule (a post from before the window counts only its
views inside it); press now does too. A piece dated inside the window is
unchanged; an undated piece and unlisted pickups count as they always
have; a campaign whose only press sits outside the window has nothing
countable from it, so it gets the second look and the plain hold.

Hermetic: no network, no model, no mail. Fails on main, passes with the fix.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_TWIN_CHECK', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from migration import bpiq_synthesis as S  # noqa: E402
from migration import bpiq_campaign as C  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim fixtures)

FAIL = 0
WINDOW = {'start': '2026-10-05', 'end': '2026-11-24'}


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def piece(outlet, day, country='US', readers=None):
    return {'outlet': outlet, 'title': f'{outlet} story', 'url': f'https://{outlet.lower().replace(" ", "")}.example/s',
            'date': day, 'country': country, 'outlet_us_monthly_uniques': readers}


EARLY = [piece('Regional Daily', '2026-08-06', 'IN'), piece('City Wire', '2026-08-06', 'IN'),
         piece('Metro Ledger', '2026-08-07', 'IN')]
INSIDE = piece('National Weekly', '2026-10-20', 'US', 12_000_000)
LATE = piece('Late Gazette', '2026-12-30', 'US', 9_000_000)
UNDATED = piece('Undated Times', '', 'US', 8_000_000)


def order(**kw):
    d = T.inputs(brand='Brandco', campaign='Brandco', start='2026-10-05', end='2026-11-02',
                 post_end='2026-11-24', control=False, talent='Talent Y')
    d.update(kw)
    return d


SALT = S.run_salt(order())


def press(fp, window=WINDOW):
    r = C.derive_reach(C.normalize_footprint(fp), {}, SALT, 29, window=window)
    return int(r.get('press_people') or 0), r


def test_pieces_and_the_window():
    early, r = press({'press': EARLY, 'press_pieces_total': 3})
    check("stories from two months before the window count nobody", early == 0)
    check("so there is no press line", not any(ln.get('line') == 'press' for ln in r.get('lines') or []))
    check("and no press pieces are counted", r.get('press_pieces') == 0)
    legacy, _ = press({'press': EARLY, 'press_pieces_total': 3}, window=None)
    check("a caller that gives no window counts the pieces as it always did", legacy > 0)
    inside_w, _ = press({'press': [INSIDE]})
    inside_nw, _ = press({'press': [INSIDE]}, window=None)
    check("a piece dated inside the window is unchanged", inside_w == inside_nw and inside_w > 0)
    mixed_w, rm = press({'press': EARLY + [INSIDE], 'press_pieces_total': 4})
    mixed_nw, _ = press({'press': EARLY + [INSIDE], 'press_pieces_total': 4}, window=None)
    check("with stories before the window and one inside it, only the one inside counts",
          0 < mixed_w < mixed_nw and rm.get('press_pieces') == 1)
    check("the press line counts only the pieces it counted",
          any(ln.get('line') == 'press' and ln.get('basis', '').startswith('1 piece') for ln in rm['lines']))
    late, _ = press({'press': [LATE]})
    check("a story after the attribution window counts nobody", late == 0)
    und_w, _ = press({'press': [UNDATED]})
    und_nw, _ = press({'press': [UNDATED]}, window=None)
    check("an undated piece counts as it always has", und_w == und_nw and und_w > 0)
    pick, rp = press({'press': EARLY, 'press_pieces_total': 6})
    check("unlisted pickups carry no date and count as they always have", pick > 0 and rp.get('press_pieces') == 3)


def test_footprint():
    check("press only from before the window is no footprint", not C.has_footprint({'press': EARLY}, WINDOW))
    check("without a window the old test still holds", C.has_footprint({'press': EARLY}))
    check("a piece inside the window is a footprint", C.has_footprint({'press': EARLY + [INSIDE]}, WINDOW))
    check("an undated piece is a footprint", C.has_footprint({'press': [UNDATED]}, WINDOW))
    check("unlisted pickups are a footprint", C.has_footprint({'press': EARLY, 'press_pieces_total': 5}, WINDOW))
    ev = {'press': EARLY, 'events': [{'name': 'Launch night', 'date': '2026-09-01', 'attendee_followers_total': 900_000}]}
    check("events are not press: a launch event still counts", C.has_footprint(ev, WINDOW)
          and press(ev)[1].get('event_people', 0) > 0)


def _posts(n=10):
    return [{'url': f'https://www.tiktok.com/@talenty/video/{(1792000000 + i * 86400) << 32}', 'views': 600_000 + i * 1_111,
             'published': '2026-10-20'} for i in range(n)]


def test_the_valuation():
    camp = {'campaign_name': 'Brandco', 'footprint': C.normalize_footprint(
        {'press': EARLY, 'press_pieces_total': 3, 'link_reads': _posts()}), 'talent_role': {'channels': ['Instagram']}}
    pr = T.prim()
    pr['category'] = 'BEAUTY'
    p = S.build_payload(order(), pr, created_by='tester', campaign=camp)
    rows = {r['platform']: r for r in p['valuation']['emv_breakdown']}
    reach = p['campaign'].get('reach') or {}
    check("the valuation carries no press row for stories from before the window", 'Press' not in rows)
    check("the people reached are the campaign posts alone", reach.get('press_people', 0) == 0
          and reach.get('reach_us') == reach.get('link_people') and p['projected_audience_size'] == reach.get('reach_us'))
    line = p['campaign'].get('reach_line') or ''
    check("the reach line names only the posts", 'press' not in line.lower() and 'campaign posts' in line)
    v = p['valuation']
    check("the total is still the four parts",
          abs(v['total_brand_value'] - (v['brand_engagement_value'] + v['earned_media_value'] + v['brand_lift_value']
                                       + v['conversion_value'])) < 0.05)


class OnlyEarlyPress:
    """The research finds stories from August only, on the first look and the second."""

    def __init__(self):
        self.surfaces = []

    def __call__(self, system, user_prompt, **kw):
        self.surfaces.append(kw.get('surface'))
        if kw.get('surface') == 'bpiq_campaign_research':
            return {'campaign_name': 'Brandco', 'brand': 'Brandco', 'talent': 'Talent Y', 'category': 'BEAUTY',
                    'talent_role': {'role': 'ambassador', 'channels': ['Instagram']}}
        if kw.get('surface') == 'bpiq_campaign_footprint':
            return {'press': EARLY, 'press_pieces_total': 3}
        return {}


def test_nothing_countable_is_asked_about():
    fr = OnlyEarlyPress()
    held = None
    was = os.environ.get('BPIQ_WHOLE_AUDIENCE_FALLBACK')
    os.environ['BPIQ_WHOLE_AUDIENCE_FALLBACK'] = '0'     # the shared fixtures module keeps the old fallback on for itself
    try:
        S.synthesize(order(), fr, tools=None, created_by='tester', underpinnings=None, job_id='t1')
    except S.NoCountableReach as e:
        held = e
    except Exception as e:      # anything else means the order went on without countable reach
        held = None
        print('   (raised', type(e).__name__, str(e)[:120], ')')
    finally:
        if was is None:
            os.environ.pop('BPIQ_WHOLE_AUDIENCE_FALLBACK', None)
        else:
            os.environ['BPIQ_WHOLE_AUDIENCE_FALLBACK'] = was
    check("a campaign whose only press is from before the window gets the second look",
          fr.surfaces.count('bpiq_campaign_footprint') == 2)
    check("and is held with the plain question, never valued on the whole audience", held is not None)
    check("the question names the window it read", held is not None and '2026-10-05' in held.client_line)


def main():
    for t in (test_pieces_and_the_window, test_footprint, test_the_valuation, test_nothing_countable_is_asked_about):
        print(t.__name__)
        try:
            t()
        except Exception as e:     # a crash before the fix is a failure, never a skip
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran ({type(e).__name__}: {e})", False)
    print(f"\n{'FAIL' if FAIL else 'PASS'}: {FAIL} failure(s)")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
