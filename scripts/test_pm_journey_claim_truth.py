#!/usr/bin/env python3
"""A journey claim must be true (Sony Television, 2026-11-10).

The seat asked for its Walter Boys read "in the dashboard where it says
select a journey". A reasoned read answered "It is in the picker now: ...
is built as a Digital Journey and shows under Select a journey" and, asked
again, "It is on the dashboard now: ... sits as a Digital Journey card",
while no journey-iq/ object and no journey job existed.

Rule: a sentence that claims an artifact sits in a picker, dropdown, tab
or card is printed only when the registry object exists (the payload
carries its key); a reasoned read without one says the read is here in the
chat and offers the real build ('Add it to Digital Journey IQ', which runs
the journey intake). Same for 'in your Select Profile dropdown' and 'on the
Twins tab'.

    REGRESSION_TEST_MODE=1 python3 scripts/test_pm_journey_claim_truth.py
"""
import os
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prometheus import reply_shape as RS  # noqa: E402
from prometheus import intents as IN  # noqa: E402

SONY_READ = (
    "On My Life with the Walter Boys (Season 3 cut vs full viewers universe) - Season 3 watchers:\n\n"
    "It is in the picker now: My Life with the Walter Boys (Season 3 cut vs full viewers universe) is built as a Digital Journey "
    "and shows under Select a journey, carrying 62,418,537 people at first touch down to the 8,146,329 Season 3 watchers.\n\n"
    "First touch with a Season 3 surface: 62,418,537, Season 3 watchers (journey end): 8,146,329, New to the title: 1,489,237.\n\n"
    "- Pathways: four routes cover almost everyone who reached the end. In-app route, 31,208,944 at entry and the fastest at 2.1 days.\n"
    "- Where they left: 16,908,274 reached the title page and 11,164,663 pressed play on any episode.\n\n"
    "How these are counted:\n- Window: Aug 6 2026 to Nov 10 2026."
)
SONY_READ_2 = (
    "On My Life with the Walter Boys (Season 3 cut vs full viewers universe) - Season 3 watchers:\n\n"
    "It is on the dashboard now: My Life with the Walter Boys (Season 3 cut vs full viewers universe) sits as a Digital Journey card "
    "running 62,418,537 people at first touch down to 8,146,329 Season 3 watchers.\n\n"
    "- The card carries the same ten steps you already have under Select a journey, so the dashboard tile and the picker read off "
    "one journey, not two: 62,418,537 at first touch, 8,146,329 at the Season 3 play.\n"
    "- Completion holds strong once play starts: 62.8% of Season 3 starters reached episode 10."
)
FIGURES = ('62,418,537', '8,146,329', '1,489,237', '31,208,944', '16,908,274', '11,164,663', '62.8%')
CLAIMS = ('in the picker', 'Select a journey', 'built as a Digital Journey', 'Digital Journey card', 'on the dashboard now')


def _payload(reply, **extra):
    p = {'success': True, 'action': 'answer', 'reply': reply, 'followups': ['Week by week Season 3 viewer build'],
         'profile': 'My Life with the Walter Boys (Season 3 cut vs full viewers universe) - Season 3 watchers'}
    p.update(extra)
    return p


def test_reasoned_read_claims_are_dropped():
    for text in (SONY_READ, SONY_READ_2):
        p = RS.shape_finished_read(_payload(text))
        out = p['reply']
        for c in CLAIMS:
            assert c.lower() not in out.lower(), (c, out)
        assert RS.HERE_LINE in out, out
        for f in FIGURES:
            if f in text:
                assert f in out, (f, out)
        assert p['followups'][0] == 'Add it to Digital Journey IQ', p['followups']
        assert p.get('artifact_claims_dropped') == ['journey'], p.get('artifact_claims_dropped')
        assert 'Week by week Season 3 viewer build' in p['followups']


def test_a_registered_journey_keeps_its_line():
    p = RS.shape_finished_read(_payload(SONY_READ, journey_key='journey-iq/sonytv/My_Life_with_the_Walter_Boys_Season_3_on_Netflix_x.json.gz'))
    assert 'under Select a journey' in p['reply']
    assert RS.HERE_LINE not in p['reply']
    assert 'Add it to Digital Journey IQ' not in p['followups']
    # a caller that checked the registry says so
    p = RS.shape_finished_read(_payload(SONY_READ), artifact_exists=lambda kind: kind == 'journey')
    assert 'in the picker now' in p['reply']


def test_profile_and_twin_claims():
    p = RS.shape_finished_read({'success': True, 'reply': 'The audience reads 2,183,406 people. It is in your Select Profile dropdown now as Kalshi Customers.',
                                'followups': []})
    assert 'Select Profile dropdown' not in p['reply'] and '2,183,406' in p['reply'] and RS.HERE_LINE in p['reply']
    assert p['followups'][0] == 'Build this profile'
    # a read on an open profile names it; that profile is real
    p = RS.shape_finished_read({'success': True, 'reply': 'Pick Kalshi Customers in the Select Profile dropdown to open it.', 'followups': [],
                                'profile': 'Kalshi Customers'})
    assert 'Select Profile dropdown' in p['reply']
    p = RS.shape_finished_read({'success': True, 'reply': 'Your twin is live on the Twins tab now. 4,118,207 people in the US.', 'followups': []})
    assert 'Twins tab' not in p['reply'] and '4,118,207' in p['reply']
    assert p['followups'][0] == 'Build a twin from this audience'
    p = RS.shape_finished_read({'success': True, 'reply': 'Your twin is live on the Twins tab now.', 'followups': [], 'twin_key': 'digital-twins/x.json.gz'})
    assert 'Twins tab' in p['reply']


def test_plain_reads_untouched():
    text = ("Season 3 watchers are 8,146,329 people, 59.3% of the 13,742,586 universe.\n\n"
            "- The 25-34 band stays the largest single block in both reads.")
    p = RS.shape_finished_read(_payload(text))
    assert p['reply'] == text, p['reply']
    assert 'artifact_claims_dropped' not in p
    assert RS.artifact_claim_kinds(text) == []


def test_the_chip_runs_the_real_build():
    for t in ('Add it to Digital Journey IQ', 'add this as a digital journey', 'i want it in the dashboard where it says select a journey',
              'no but i want to add it to the dashboard as a digital journey', 'put it under Select a journey'):
        assert IN.jiq_intent(t), t
    for t in ('did you add this as a digital journey iq profile?', 'where is the digital journey in the dashboard?',
              'Season 3 watchers are 8,146,329 people', 'what is the audience overlap between p-valley and swat exiles?'):
        assert not IN.jiq_intent(t), t


if __name__ == '__main__':
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'PASS {name}')
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f'FAIL {name}: {e!r}')
    print('OK' if not fails else f'{fails} failing')
    sys.exit(1 if fails else 0)
