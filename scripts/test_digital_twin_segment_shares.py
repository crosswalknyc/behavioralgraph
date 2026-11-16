#!/usr/bin/env python3
"""A share beside each demographic pick (Jenna, 2026-11-13: "it should
allow you to list the percentage you want of each demographic tab. so if
I select men I would then type in 50% or 30%").

Build a twin > By demographics. A pick with a share is that part of the
group; picks with no share take what is left in their own proportions;
with none of those the rest follows the natural mix; with no shares at
all a pick is the filter it always was. One group's shares total at most
100: the page refuses more before anything is sent and the route refuses
it again. Hermetic: a fake S3, no model, nothing charged, nothing mailed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

import test_digital_twin_builder as B  # noqa: E402  (the Gen Pop fixture and the fake S3)
from migration import digital_twin_synthesis as SYN  # noqa: E402
from migration import digital_twin_panel as PANEL  # noqa: E402
from migration import digital_twin_exports as EXP  # noqa: E402

_s3 = B._s3

_NODE_CHECKS = r'''const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function between(a, b) { const i = src.indexOf(a); const j = src.indexOf(b, i); if (i < 0 || j < 0) throw new Error('missing ' + a + ' / ' + b); return src.slice(i, j); }
// BW_DEMO + helpers up to BW_PURPOSES; span/age/income/label helpers; step functions
const part1 = between("            var BW_DEMO = [", "            var BW_PURPOSES");
const part2 = between("            function bwMarkets()", "            function bwCanNext()");
const part3 = between("            function bwCanNext()", "            function bwStepSource()");
const part4 = between("            function bwStepAudience()", "            function bwStepPurpose()");
const part5 = between("            window.dtwBwPick = ", "            window.dtwBwField");
const stubs = `
var S = { bw: { step: 1, source: 'demographics', demo: {}, shares: {}, markets: '', interests: '', busy: false } };
var window = {}; var __els = {};
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function el(id) { return __els[id] || null; }
var document = { querySelector: function() { return null; } };
function bwNav(can) { return '<NAV can=' + (!!can) + '>'; }
function renderBuild() {}
function dtwBusyHtml(x) { return x; } function dtwBusyLineHtml() { return ''; }
`;
eval(stubs + part1 + part2 + part3 + part4 + part5 + `
var assert = require('assert');
function st(cat) { return bwShareState(cat); }
// no picks, no shares
assert.strictEqual(bwSharesOk(), true); assert.deepStrictEqual(bwSharesBody(), {});
assert.strictEqual(bwAudienceLabel(), 'US adults');
// picks, no shares: label as before
S.bw.demo = { gender: ['Women'], age: ['25-34', '35-44'] };
assert.strictEqual(bwAudienceLabel(), 'Women 25 to 44'); assert.strictEqual(st('gender').line, ''); assert.strictEqual(bwCanNext(), true);
var html = bwStepAudience(); 
assert.ok(html.indexOf('dtw-bw-share') > 0 && html.indexOf('Want a set mix?') > 0 && html.indexOf('With no shares, the group is only the picks you chose.') > 0);
assert.strictEqual((html.match(/class="dtw-bw-share"/g) || []).length, 3);   // one input per pick
// Men 30
S.bw.demo = { gender: ['Men'] }; S.bw.shares = { gender: { Men: '30' } };
assert.strictEqual(st('gender').err, ''); assert.strictEqual(st('gender').line, "Men 30%. The other 70% follows the audience's natural mix.");
assert.deepStrictEqual(bwSharesBody(), { gender: { Men: 30 } }); assert.strictEqual(bwAudienceLabel(), 'US adults, 30% Men');
// Men 30, Women picked with no number
S.bw.demo = { gender: ['Men', 'Women'] };
assert.strictEqual(st('gender').line, 'Men 30%. The other 70% goes to Women.');
assert.strictEqual(bwAudienceLabel(), 'US adults, 30% Men and 70% Women');   // the one pick with no share says what it takes
// both typed under 100
S.bw.shares = { gender: { Men: '30', Women: '30%' } };
assert.ok(/need to add to 100%\\. They add to 60%/.test(st('gender').err)); assert.strictEqual(bwCanNext(), false);
// over 100
S.bw.shares = { gender: { Men: '80', Women: '50' } };
assert.strictEqual(st('gender').err, 'Gender adds to 130%. The shares in one group can total at most 100%.'); assert.strictEqual(bwSharesOk(), false);
// exactly 100
S.bw.shares = { gender: { Men: '50', Women: '50' } };
assert.strictEqual(st('gender').err, ''); assert.strictEqual(st('gender').line, 'Men 50%, Women 50%. That is the whole group.'); assert.strictEqual(bwAudienceLabel(), 'US adults, 50% Men and 50% Women');
// 100 with an open pick
S.bw.shares = { gender: { Men: '100' } };
assert.strictEqual(st('gender').err, 'Gender already adds to 100%. Lower a share, or clear Women.');
// not a number
S.bw.shares = { gender: { Men: 'abc' } }; assert.strictEqual(st('gender').err, 'A share is a number from 0 to 100.');
S.bw.shares = { gender: { Men: '101' } }; assert.strictEqual(st('gender').err, 'A share is a number from 0 to 100.');
S.bw.shares = { gender: { Men: '-5' } }; assert.strictEqual(st('gender').err, 'A share is a number from 0 to 100.');
// blank and 0 are no share
S.bw.shares = { gender: { Men: '', Women: '0' } }; assert.strictEqual(st('gender').err, ''); assert.deepStrictEqual(bwSharesBody(), {});
// one decimal
S.bw.demo = { age: ['18-24', '25-34', '35-44'] }; S.bw.shares = { age: { '18-24': '33.3', '25-34': '20' } };
assert.strictEqual(st('age').line, '18-24 33.3%, 25-34 20%. The other 46.7% goes to 35-44.');
assert.strictEqual(bwSharesWords(), 'Age: 18-24 33.3%, 25-34 20%.');
assert.strictEqual(bwAudienceLabel(), 'US adults, 33.3% 18-24, 20% 25-34, 46.7% 35-44');
S.bw.shares = { age: { '18-24': '33.3' } }; assert.strictEqual(bwAudienceLabel(), 'US adults, 33.3% 18-24, 25-34, 35-44'); S.bw.shares = { age: { '18-24': '33.3', '25-34': '20' } };
assert.ok(/18-24 33.3% is about 4 seats, 25-34 20% is about 2 seats/.test(bwShareSeats(12)));
// clearing a pick clears its share
window.dtwBwPick('age', { getAttribute: function() { return '18-24'; } });
assert.deepStrictEqual(S.bw.demo.age, ['25-34', '35-44']); assert.deepStrictEqual(bwSharesBody(), { age: { '25-34': 20 } });
// typing updates state and the line in place
var line = { textContent: '', classList: { toggle: function(c, on) { line.err = on; } } }; __els['dtwBwShareLine-age'] = line;
window.dtwBwShare({ value: '90', getAttribute: function(k) { return k === 'data-cat' ? 'age' : '35-44'; } });
assert.strictEqual(line.err, true); assert.ok(/adds to 110%/.test(line.textContent));
window.dtwBwShare({ value: '60', getAttribute: function(k) { return k === 'data-cat' ? 'age' : '35-44'; } });
assert.strictEqual(line.err, false); assert.ok(/The other 20% follows/.test(line.textContent));
console.log('node logic checks: OK');
`);
'''


def _rows(ident, cat):
    return {r['label']: r['pct'] for r in ident['demographics'][cat]}


def _ident(raw, seed='seed'):
    return SYN.demographic_identity(_s3(), SYN.demographic_spec(raw), seed)


class NoSharesIsToday(unittest.TestCase):
    """A segment with no shares anywhere is exactly what it was."""

    PICKS = {'gender': ['Women'], 'age': ['25-34', '35-44'], 'income': ['$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or more'], 'markets': ['Dallas', 'Los Angeles']}

    def test_spec_and_name_unchanged(self):
        spec = SYN.demographic_spec(self.PICKS)
        self.assertNotIn('shares', spec)
        self.assertEqual(spec, {'gender': ['Women'], 'age': ['25-34', '35-44'], 'income': ['$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or more'], 'markets': ['Dallas', 'Los Angeles']})
        self.assertEqual(SYN.demographic_name(spec), 'Women 25 to 44, $100,000 or more, in Dallas and Los Angeles')
        # empty, blank and zero shares are no shares
        for empty in ({}, {'gender': {}}, {'gender': {'Women': ''}}, {'gender': {'Women': 0}}, {'gender': {'Women': None}}, 'x', None):
            self.assertEqual(SYN.demographic_spec(dict(self.PICKS, shares=empty)), spec, empty)
            self.assertEqual(SYN.demographic_shares_error(dict(self.PICKS, shares=empty)) if not isinstance(empty, str) else '', '')

    def test_identity_is_the_filter_it_was(self):
        # the values the code on main produced for this fixture before shares existed (2026-11-13, 37679ce4)
        ident = _ident(self.PICKS)
        self.assertNotIn('shares', ident)
        self.assertEqual(ident['demographics']['gender'], [{'label': 'Women', 'pct': 100.0}])
        self.assertEqual(ident['demographics']['age'], [{'label': '35-44', 'pct': 50.5713}, {'label': '25-34', 'pct': 49.4287}])
        self.assertEqual(ident['size_us'], 1202087)
        self.assertEqual(_ident({'gender': ['Men']})['size_us'], 128351244)
        self.assertEqual(json.dumps(_ident(dict(self.PICKS, shares={})), sort_keys=True), json.dumps(ident, sort_keys=True))

    def test_build_carries_no_share_record(self):
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Women'], 'age': ['25-34']}, 'panelists': 12}, None, s3=_s3(), with_research=False)
        self.assertEqual(twin['meta']['name'], 'Women 25 to 34')
        self.assertNotIn('demographics_shares', twin['meta'])
        self.assertNotIn('shares', twin['meta']['demographics_spec'])


class OneShare(unittest.TestCase):
    def test_men_at_30(self):
        ident = _ident({'gender': ['Men'], 'shares': {'gender': {'Men': 30}}})
        g = _rows(ident, 'gender')
        self.assertAlmostEqual(g['Men'], 30.0, delta=0.005)             # within a few thousandths of what was typed
        self.assertEqual(f"{g['Men']:.1f}", '30.0')                     # so the page prints it as typed
        self.assertNotEqual(round(g['Men'], 2), g['Men'])               # and never on a round boundary
        self.assertAlmostEqual(sum(g.values()), 100.0, places=4)
        # the other 70 follows the natural mix of everyone else: women to non-binary as in the US
        self.assertAlmostEqual(g['Women'] / g['Non-binary'], 49.5723 / 1.4953, delta=0.6)
        self.assertEqual(ident['shares']['asked'], {'gender': {'Men': 30.0}})
        self.assertEqual(ident['shares']['notes'], [])
        self.assertTrue(any(r['label'] == 'Men' and r['set'] and r['pct'] == 30.0 for r in ident['shares']['applied']['gender']))
        # the people it stands for: the largest group with that mix the US holds (the scarcest value sets it)
        adults = 1 - 0.193339
        want = SYN.US_POP * adults * min(47.8859 / 98.9535 / 0.30, 49.5723 / 98.9535 / (g['Women'] / 100))
        self.assertLess(abs(ident['size_us'] - want) / want, 0.02)

    def test_men_at_50_and_100(self):
        self.assertEqual(f"{_rows(_ident({'gender': ['Men'], 'shares': {'gender': {'Men': 50}}}), 'gender')['Men']:.1f}", '50.0')
        whole = _ident({'gender': ['Men'], 'shares': {'gender': {'Men': 100}}})
        self.assertEqual(_rows(whole, 'gender'), {'Men': 100.0})
        self.assertEqual(whole['size_us'], _ident({'gender': ['Men']})['size_us'])   # 100% of one pick is that pick as a filter

    def test_a_share_names_its_pick(self):
        # a share sent for a value that was not in the picks adds the pick; the percent sign and a string are read
        spec = SYN.demographic_spec({'shares': {'gender': {'men': '30%'}}})
        self.assertEqual(spec, {'gender': ['men'], 'shares': {'gender': {'men': 30.0}}})
        spec = SYN.demographic_spec({'gender': ['Men'], 'shares': {'gender': {'MEN': '33.33'}, 'bogus': {'x': 5}}})
        self.assertEqual(spec['shares'], {'gender': {'Men': 33.3}})

    def test_the_name_says_the_share(self):
        self.assertEqual(SYN.demographic_name(SYN.demographic_spec({'gender': ['Men'], 'shares': {'gender': {'Men': 30}}})), 'US adults, 30% Men')
        self.assertEqual(SYN.demographic_name(SYN.demographic_spec({'gender': ['Women', 'Men'], 'age': ['25-34', '35-44'], 'shares': {'gender': {'Women': 50, 'Men': 50}}})),
                         '25 to 44, 50% Women and 50% Men')
        self.assertEqual(SYN.shares_words(SYN.demographic_spec({'gender': ['Men'], 'age': ['18-24'], 'shares': {'gender': {'Men': 30}, 'age': {'18-24': 33.3}}})),
                         'Gender: Men 30%. Age: 18-24 33.3%.')
        self.assertEqual(SYN.shares_words({'gender': ['Men']}), '')
        # one pick with no share takes all that is left, so the name gives its share too (never '30% Men and Women', which reads as one share for both)
        self.assertEqual(SYN.demographic_name(SYN.demographic_spec({'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 30}}})), 'US adults, 30% Men and 70% Women')
        self.assertEqual(SYN.demographic_name(SYN.demographic_spec({'age': ['18-24', '25-34', '35-44'], 'shares': {'age': {'18-24': 40, '25-34': 20}}})), 'US adults, 40% 18-24, 20% 25-34, 40% 35-44')
        # two picks with no share split what is left in their own proportions, so they keep their plain names
        self.assertEqual(SYN.demographic_name(SYN.demographic_spec({'age': ['18-24', '25-34', '35-44'], 'shares': {'age': {'18-24': 40}}})), 'US adults, 40% 18-24, 25-34, 35-44')


class SeveralTabs(unittest.TestCase):
    RAW = {'gender': ['Men', 'Women'], 'age': ['18-24', '25-34', '35-44'], 'ethnicity': ['Hispanic or Latino'], 'parental': ['Has children'],
           'shares': {'gender': {'Men': 50, 'Women': 50}, 'age': {'18-24': 40, '25-34': 20}, 'ethnicity': {'Hispanic or Latino': 35}, 'parental': {'Has children': 33.3}}}

    def test_each_tab_holds_on_its_own(self):
        ident = _ident(self.RAW)
        g, a, e, p = (_rows(ident, c) for c in ('gender', 'age', 'ethnicity', 'parental'))
        self.assertEqual((f"{g['Men']:.1f}", f"{g['Women']:.1f}"), ('50.0', '50.0'))
        self.assertEqual(set(g), {'Men', 'Women'})
        self.assertEqual((f"{a['18-24']:.1f}", f"{a['25-34']:.1f}", f"{a['35-44']:.1f}"), ('40.0', '20.0', '40.0'))   # the pick with no share takes what is left
        self.assertEqual(set(a), {'18-24', '25-34', '35-44'})
        self.assertEqual(f"{e['Hispanic or Latino']:.1f}", '35.0')
        self.assertEqual(f"{p['Has Children']:.1f}", '33.3')
        self.assertEqual(f"{p['No Children']:.1f}", '66.7')
        for cat in ('gender', 'age', 'ethnicity', 'parental', 'income'):
            self.assertAlmostEqual(sum(_rows(ident, cat).values()), 100.0, places=4, msg=cat)
        self.assertEqual(sorted(ident['shares']['applied']), ['age', 'ethnicity', 'gender', 'parental'])
        self.assertEqual(ident['shares']['notes'], [])
        # a tab with no shares is still the US mix
        self.assertEqual(len(_rows(ident, 'income')), 7)


class UnderAHundred(unittest.TestCase):
    def test_the_rest_goes_to_picks_with_no_share_first(self):
        ident = _ident({'age': ['18-24', '25-34', '35-44'], 'shares': {'age': {'18-24': 40}}})
        a = _rows(ident, 'age')
        self.assertEqual(set(a), {'18-24', '25-34', '35-44'})
        self.assertEqual(f"{a['18-24']:.1f}", '40.0')
        self.assertAlmostEqual(a['25-34'] + a['35-44'], 60.0, delta=0.01)
        self.assertAlmostEqual(a['25-34'] / a['35-44'], 13.2166 / 13.4853, delta=0.03)      # in their own proportions

    def test_the_rest_follows_the_natural_mix_when_every_pick_has_a_share(self):
        ident = _ident({'ethnicity': ['Hispanic or Latino', 'Asian'], 'shares': {'ethnicity': {'Hispanic or Latino': 30, 'Asian': 10}}})
        e = _rows(ident, 'ethnicity')
        self.assertEqual((f"{e['Hispanic or Latino']:.1f}", f"{e['Asian']:.1f}"), ('30.0', '10.0'))
        self.assertAlmostEqual(e['White'] + e['Black or African American'] + e['Another Race/Ethnicity'], 60.0, delta=0.01)
        self.assertAlmostEqual(e['White'] / e['Black or African American'], 57.7959 / 11.9178, delta=0.12)
        self.assertEqual(ident['shares']['notes'], [])


class OverAHundred(unittest.TestCase):
    RAW = {'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 80, 'Women': 50}}}

    def test_the_check_the_route_runs(self):
        self.assertEqual(SYN.demographic_shares_error(self.RAW), 'Gender adds to 130%. The shares in one group can total at most 100%.')
        self.assertEqual(SYN.demographic_shares_error({'age': ['18-24'], 'shares': {'age': {'18-24': 120}}}), 'Age: the share for 18-24 needs to be a number from 0 to 100.')
        self.assertEqual(SYN.demographic_shares_error({'age': ['18-24'], 'shares': {'age': {'18-24': 'lots'}}}), 'Age: the share for 18-24 needs to be a number from 0 to 100.')
        self.assertEqual(SYN.demographic_shares_error({'age': ['18-24'], 'shares': {'age': {'18-24': -5}}}), 'Age: the share for 18-24 needs to be a number from 0 to 100.')
        self.assertIn('could not be read', SYN.demographic_shares_error({'shares': ['x']}))
        for fine in ({'gender': ['Men'], 'shares': {'gender': {'Men': 100}}}, {'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 50, 'Women': 50}}},
                     {'gender': ['Men']}, {}, None, {'shares': {'income': {'$250,000 or more': 12.5}}}):
            self.assertEqual(SYN.demographic_shares_error(fine), '', fine)

    def test_a_stored_job_over_100_is_scaled_never_failed(self):
        # the route refuses it; if one ever reaches the build another way it is scaled to 100 and nothing raises
        spec = SYN.demographic_spec(self.RAW)
        self.assertAlmostEqual(sum(spec['shares']['gender'].values()), 100.0, delta=0.11)
        g = _rows(SYN.demographic_identity(_s3(), spec, 'seed'), 'gender')
        self.assertAlmostEqual(g['Men'] / g['Women'], 80 / 50, delta=0.01)
        self.assertAlmostEqual(sum(g.values()), 100.0, places=4)

    def test_the_route_refuses_before_anything_is_queued_or_charged(self):
        try:
            import flask
        except Exception:
            self.skipTest('flask not installed in this interpreter')
        import digital_twins_api as A
        from prometheus.host import host
        calls = []
        user = {'username': 'canary:shares', 'role': 'super_admin', 'has_digital_twins_iq_access': True}
        fake_app = types.SimpleNamespace(s3_client=_s3(), get_current_user=lambda: user, _normalize_role=lambda r: r,
                                         consume_credit=lambda *a, **k: calls.append('charge') or True)
        prev = sys.modules.get('app')
        sys.modules['app'] = fake_app
        try:
            host.bind(gate=lambda allow_api_key=False: (user, None))
            app = flask.Flask('t')
            app.secret_key = 'x'
            A.register(app)
            c = app.test_client()
            r = c.post('/api/digital-twins/build', json={'source': 'demographics', 'panelists': 12, 'demographics': self.RAW})
            self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:300])
            self.assertEqual(r.get_json(), {'success': False, 'error': 'Gender adds to 130%. The shares in one group can total at most 100%.'})
            r = c.post('/api/digital-twins/build', json={'source': 'demographics', 'panelists': 12, 'demographics': {'age': ['18-24'], 'shares': {'age': {'18-24': 'lots'}}}})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(calls, [])
        finally:
            if prev is None:
                sys.modules.pop('app', None)
            else:
                sys.modules['app'] = prev


class SharesThatHadToMove(unittest.TestCase):
    """A share that cannot be met as typed is never failed and never
    dropped quietly: the closest mix is built and the record says which
    share moved and to what."""

    def test_every_option_has_a_share_and_they_fall_short(self):
        ident = _ident({'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 30, 'Women': 30}}})
        g = _rows(ident, 'gender')
        self.assertEqual(set(g), {'Men', 'Women'})                              # the sliver nobody picked never soaks up the other 40
        self.assertEqual((f"{g['Men']:.1f}", f"{g['Women']:.1f}"), ('50.0', '50.0'))
        self.assertEqual(ident['shares']['notes'], ['Gender: the shares added to 60% and too few people sit outside those picks to fill the rest, so they were scaled to 100%: Men 50%, Women 50%.'])

    def test_a_pick_with_no_share_and_nothing_left(self):
        ident = _ident({'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 100}}})
        self.assertEqual(_rows(ident, 'gender'), {'Men': 100.0})
        self.assertEqual(ident['shares']['notes'], ['Gender: Women had no share and the others already added to 100%, so Women was left out.'])

    def test_a_share_on_a_group_that_is_not_on_file(self):
        ident = _ident({'age': ['18-24', '75-84'], 'shares': {'age': {'18-24': 40, '75-84': 20}}})
        a = _rows(ident, 'age')
        self.assertEqual(f"{a['18-24']:.1f}", '40.0')
        self.assertAlmostEqual(sum(a.values()), 100.0, places=4)
        self.assertIn('Age: 75-84 is not a group on file, so its share went to the rest.', ident['shares']['notes'])

    def test_the_twin_is_named_for_the_mix_it_holds(self):
        # a share that moved: the twin's name says what was built; the record keeps what was asked and the note says what moved
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 30, 'Women': 30}}}, 'panelists': 12,
                               'subject': 'US adults, 30% Men and 30% Women'}, None, s3=_s3(), with_research=False)
        m = twin['meta']
        self.assertEqual(m['name'], 'US adults, 50% Men and 50% Women')
        self.assertEqual(m['subject'], 'US adults, 50% Men and 50% Women')
        self.assertEqual(m['demographics_spec']['shares'], {'gender': {'Men': 30.0, 'Women': 30.0}})
        self.assertEqual(m['demographics_shares']['asked'], {'gender': {'Men': 30.0, 'Women': 30.0}})
        self.assertEqual(len(m['demographics_shares']['notes']), 1)
        # a pick that fell out leaves the name
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Men', 'Women'], 'age': ['25-34'], 'shares': {'gender': {'Men': 100}}}, 'panelists': 12}, None, s3=_s3(), with_research=False)
        self.assertEqual(twin['meta']['name'], '25 to 34, 100% Men')
        # a name the reader gave is theirs
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 30, 'Women': 30}}}, 'panelists': 12, 'name': 'My group',
                               'subject': 'US adults, 30% Men and 30% Women'}, None, s3=_s3(), with_research=False)
        self.assertEqual(twin['meta']['name'], 'My group')
        # nothing moved: the name is the picks as typed, and no 'built' record rides along
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Men'], 'shares': {'gender': {'Men': 30}}}, 'panelists': 12}, None, s3=_s3(), with_research=False)
        self.assertEqual(twin['meta']['name'], 'US adults, 30% Men')
        self.assertNotIn('built', twin['meta']['demographics_shares'])
        self.assertEqual(SYN.spec_as_built({'gender': ['Men']}, {}), {'gender': ['Men']})

    def test_notes_are_plain(self):
        for raw in ({'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 30, 'Women': 30}}}, {'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 100}}}):
            for note in _ident(raw)['shares']['notes']:
                self.assertNotIn('\u2014', note)
                self.assertNotIn('\u2013', note)
                self.assertFalse(re.search(r"synth|model|estimat|pipeline|quota|jitter|gen pop|normaliz|household", note, re.I), note)


class StoredAndSeated(unittest.TestCase):
    RAW = {'gender': ['Men'], 'age': ['18-24', '25-34'], 'shares': {'gender': {'Men': 30}, 'age': {'18-24': 25}}}

    def _twin(self, panelists=12):
        s3 = _s3()
        twin = SYN.build_twin({'source': 'demographics', 'demographics': self.RAW, 'panelists': panelists, 'purpose': 'Pricing'}, None, s3=s3, with_research=False)
        return s3, twin

    def test_the_twin_keeps_the_shares_as_set(self):
        _s3_, twin = self._twin()
        m = twin['meta']
        self.assertEqual(m['name'], 'US adults, 30% Men, 25% 18-24, 75% 25-34')       # the one pick with no share took the other 75, and the name says so
        self.assertEqual(m['demographics_spec']['shares'], {'gender': {'Men': 30.0}, 'age': {'18-24': 25.0}})
        self.assertEqual(m['demographics_shares']['asked'], {'gender': {'Men': 30.0}, 'age': {'18-24': 25.0}})
        g = {r['label']: r['pct'] for r in twin['identity']['demographics']['gender']}
        a = {r['label']: r['pct'] for r in twin['identity']['demographics']['age']}
        self.assertEqual(f"{g['Men']:.1f}", '30.0')                      # the build does not re-spread a set share
        self.assertEqual((f"{a['18-24']:.1f}", f"{a['25-34']:.1f}"), ('25.0', '75.0'))
        for cat, rows in twin['identity']['demographics'].items():
            self.assertAlmostEqual(sum(r['pct'] for r in rows), 100.0, places=3, msg=cat)

    def test_the_stored_definition_round_trips(self):
        s3, twin = self._twin()
        key = SYN.save_twin(s3, SYN.S3_PREFIX + 'canary_shares/shares_round_trip.json.gz', twin)      # the fake S3; no ledger entry, nothing real written
        back = SYN.load_twin(s3, key)
        self.assertEqual(back['meta']['demographics_spec'], twin['meta']['demographics_spec'])
        self.assertEqual(back['meta']['demographics_shares'], twin['meta']['demographics_shares'])
        self.assertEqual(back['identity']['demographics']['gender'], twin['identity']['demographics']['gender'])
        # the cleaned spec reads back to itself (a rebuild from the stored picks asks for the same mix)
        self.assertEqual(SYN.demographic_spec(json.loads(json.dumps(back['meta']['demographics_spec']))), back['meta']['demographics_spec'])

    def test_a_twin_stored_before_shares_still_loads_and_names(self):
        old_meta = {'source': 'demographics', 'demographics_spec': {'gender': ['Women'], 'age': ['25-34', '35-44'], 'markets': ['Dallas']}}
        self.assertEqual(SYN.demographic_spec(old_meta['demographics_spec']), old_meta['demographics_spec'])
        self.assertEqual(SYN.demographic_name(old_meta['demographics_spec']), 'Women 25 to 44, in Dallas')
        self.assertEqual(SYN.shares_words(old_meta['demographics_spec']), '')
        from prometheus import twin_notify as TN
        src = TN.__dict__
        fn = next((src[n] for n in src if callable(src[n]) and n in ('source_words', '_source_words', 'built_from', '_built_from')), None)
        if fn is not None:
            self.assertIn('Women 25 to 44', fn({}, old_meta) if fn.__code__.co_argcount == 2 else fn(old_meta))

    def test_a_room_seats_the_shares_within_a_seat(self):
        for n in (12, 20, 40):
            _s3_, twin = self._twin(n)
            twin['meta']['key'] = 'k_shares'
            group = PANEL.build_group([twin], None, n, 'seed-room')
            men = sum(1 for r in group['roster'] if r['gender'] == 'M')
            young = sum(1 for r in group['roster'] if 18 <= int(r['age']) <= 24)
            self.assertEqual(len(group['roster']), n)
            self.assertLessEqual(abs(men - 0.30 * n), 1.0, (n, men))                 # whole seats: within one of the share
            self.assertLessEqual(abs(young - 0.25 * n), 1.0, (n, young))
            self.assertTrue(all(18 <= int(r['age']) <= 34 for r in group['roster']))

    def test_the_export_shows_the_mix(self):
        _s3_, twin = self._twin()
        twin['meta']['key'] = 'k_shares'
        pages = EXP._pages(twin)
        rows = next(p['rows'] for p in pages if p.get('kind') == 'table' and any(str(r[0]).startswith('Gender:') for r in p.get('rows') or []))
        shown = {r[0]: r[1] for r in rows}
        self.assertEqual(shown['Gender: Men'], '30.0%')
        self.assertEqual(shown['Age: 18-24'], '25.0%')
        # the rows a share was set on say so; the rows that took what was left do not
        noted = {r[0]: r[2] for r in rows}
        self.assertEqual((noted['Gender: Men'], noted['Age: 18-24'], noted['Gender: Women'], noted['Age: 25-34']), ('share set', 'share set', '', ''))


class WhereTheSharesShow(unittest.TestCase):
    """Everything that shows a twin's make-up shows the shares that were
    set, and says so when one had to move: the summary, the CSV, the
    deck and PDF pages, the line that says the twin is ready and the
    team notice. A twin with no shares reads the way it always did."""

    RAW = {'gender': ['Men'], 'income': ['$250,000 or more'], 'shares': {'gender': {'Men': 30}, 'income': {'$250,000 or more': 12.5}}}
    MOVED = {'gender': ['Men', 'Women'], 'shares': {'gender': {'Men': 30, 'Women': 30}}}

    def _twin(self, raw, **extra):
        return SYN.build_twin(dict({'source': 'demographics', 'demographics': raw, 'panelists': 12}, **extra), None, s3=_s3(), with_research=False)

    def test_rows_read_largest_first(self):
        ident = _ident({'age': ['18-24'], 'shares': {'age': {'18-24': 40}}})
        pcts = [r['pct'] for r in ident['demographics']['age']]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertEqual(ident['demographics']['age'][0]['label'], '18-24')
        self.assertEqual([r['label'] for r in ident['shares']['applied']['age']], [r['label'] for r in ident['demographics']['age']])

    def test_the_summary(self):
        s = SYN.shares_summary(self._twin(self.RAW)['meta'])
        self.assertEqual(s['words'], 'Gender: Men 30%. Household income: $250,000 or more 12.5%.')
        self.assertEqual(s['notes'], [])
        self.assertEqual(s['set'], {'gender': {'Men'}, 'income': {'$250,000 or More'}})
        self.assertEqual(SYN.shares_notice(self._twin(self.RAW)['meta']), '')
        # a share that moved: one plain line, which share and what it came to
        moved = self._twin(self.MOVED, subject='US adults, 30% Men and 30% Women')['meta']
        self.assertEqual(SYN.shares_notice(moved), 'One share was adjusted. Gender: the shares added to 60% and too few people sit outside those picks to fill the rest, '
                                                   'so they were scaled to 100%: Men 50%, Women 50%.')
        # a twin with no shares, and one stored before shares existed
        for meta in (self._twin({'gender': ['Men']})['meta'], {'source': 'demographics', 'demographics_spec': {'gender': ['Women']}}, {}, None):
            self.assertEqual(SYN.shares_summary(meta), {'words': '', 'notes': [], 'set': {}})
            self.assertEqual(SYN.shares_notice(meta), '')

    def test_the_csv(self):
        from migration import digital_twins as DT
        rows = DT.csv_rows(self._twin(self.RAW))
        self.assertIn(['Twin', 'shares set', 'Gender: Men 30%. Household income: $250,000 or more 12.5%.', '', '', ''], rows)
        men = next(r for r in rows if r[:3] == ['Identity', 'gender', 'Men'])
        self.assertEqual((f"{men[3]:.1f}", men[5]), ('30.0', 'share set'))
        rich = next(r for r in rows if r[:3] == ['Identity', 'income', '$250,000 or More'])
        self.assertEqual((f"{rich[3]:.1f}", rich[5]), ('12.5', 'share set'))
        self.assertEqual(next(r for r in rows if r[:3] == ['Identity', 'gender', 'Women'])[5], '')
        self.assertFalse([r for r in rows if r[1] == 'share adjusted'])
        moved = DT.csv_rows(self._twin(self.MOVED, subject='US adults, 30% Men and 30% Women'))
        self.assertEqual([r[2] for r in moved if r[1] == 'share adjusted'],
                         ['Gender: the shares added to 60% and too few people sit outside those picks to fill the rest, so they were scaled to 100%: Men 50%, Women 50%.'])
        # no shares: the rows it always wrote (nothing in the Twin section about shares, every identity note blank)
        plain = DT.csv_rows(self._twin({'gender': ['Men'], 'age': ['25-34']}))
        self.assertFalse([r for r in plain if 'share' in str(r[1])])
        self.assertTrue(all(r[5] == '' for r in plain if r[0] == 'Identity' and r[1] in SYN.DEMO_PICK_CATS))

    def test_the_pages(self):
        twin = self._twin(self.RAW)
        twin['meta']['key'] = 'k_shares'
        rows = next(p['rows'] for p in EXP._pages(twin) if p.get('kind') == 'table' and any(str(r[0]).startswith('Gender:') for r in p.get('rows') or []))
        self.assertIn(('Income: $250,000 or More', '12.5%', 'share set'), rows)             # a group outside gender and age shows the rows a share was set on
        self.assertIn(('Gender: Men', '30.0%', 'share set'), rows)
        plain = self._twin({'gender': ['Men'], 'age': ['25-34']})
        plain['meta']['key'] = 'k_plain'
        rows = next(p['rows'] for p in EXP._pages(plain) if p.get('kind') == 'table' and any(str(r[0]).startswith('Gender:') for r in p.get('rows') or []))
        self.assertFalse([r for r in rows if r[2] == 'share set' or str(r[0]).startswith('Income:')])

    def test_the_team_notice(self):
        from prometheus import twin_notify as TN
        from migration.digital_twin_copy import strip_calibration_language
        st = {'username': 'canary:shares', 'name': 'Canary', 'email': '', 'company': ''}
        body = TN._twin_body(self._twin(self.RAW), 'k_shares', st, {'via': 'dashboard'}, 'dashboard')
        self.assertIn('Shares set: Gender: Men 30%. Household income: $250,000 or more 12.5%.', body)
        self.assertNotIn('Share adjusted', body)
        moved = TN._twin_body(self._twin(self.MOVED, subject='US adults, 30% Men and 30% Women'), 'k_moved', st, {'via': 'dashboard'}, 'dashboard')
        self.assertIn('Twin: US adults, 50% Men and 50% Women', moved)
        self.assertIn('Shares set: Gender: Men 30%, Women 30%.', moved)
        self.assertIn('Share adjusted: Gender: the shares added to 60%', moved)
        for text in (body, moved):
            self.assertEqual(TN.banned_tokens(text), [])
            self.assertEqual(strip_calibration_language(text), text)                         # the house guards leave every line standing
            self.assertNotIn('\u2014', text)
        self.assertNotIn('Shares set', TN._twin_body(self._twin({'gender': ['Men']}), 'k_plain', st, {'via': 'dashboard'}, 'dashboard'))

    def test_the_ready_line_carries_the_notice(self):
        # the job appends the notice to the line that says the twin is ready (the walkthrough's last screen and the chat print that line)
        with open(os.path.join(ROOT, 'prometheus', 'digital_twin_job.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('moved = SYN.shares_notice(m)', src)
        self.assertIn('ready = f"{ready} {moved}"', src)
        self.assertLess(src.index('moved = SYN.shares_notice(m)'), src.index("'ready_message': ready})"))


class ThePage(unittest.TestCase):
    """The markup and the wiring on the page (the logic itself is run in
    node by the build; here: the control exists and the body carries the
    shares)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            cls.html = fh.read()

    def test_a_percent_field_per_pick_and_the_rules(self):
        h = self.html
        for needle in ('function bwShareRow(cat)', 'oninput="dtwBwShare(this)"', 'window.dtwBwShare = function(inp)', 'class="dtw-bw-shareline',
                       'The shares in one group can total at most 100%.', "follows the audience\\'s natural mix.", 'body.demographics.shares = bsh',
                       "bwDemoPicked() && bwSharesOk()", 'Shares set for this twin.', 'function bwShareSeats(n)', "shares: {}, markets: ''"):
            self.assertEqual(h.count(needle) >= 1, True, needle)
        self.assertEqual(h.count('function bwShareState(cat)'), 1)

    def test_page_copy_is_plain(self):
        block = self.html[self.html.index('var BW_SHARE_LABEL'):self.html.index('var BW_PURPOSES')]
        self.assertNotIn('\u2014', block)
        self.assertFalse(re.search(r"synth|modeled|estimated|pipeline|quota|jitter|gen pop|\bHHs?\b|households\b|household(?! income)", block, re.I))   # 'Household income' is the group's own label on the page

    def test_the_page_logic_runs(self):
        # the page's own functions, lifted from the template and run in node: what the line under a group says as shares are typed,
        # what is refused, what the build request carries
        import shutil
        import subprocess
        import tempfile
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(_NODE_CHECKS)
            path = fh.name
        try:
            r = subprocess.run([node, path, os.path.join(ROOT, 'templates', 'index.html')], capture_output=True, text=True, timeout=120)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
        self.assertIn('node logic checks: OK', r.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=1)
