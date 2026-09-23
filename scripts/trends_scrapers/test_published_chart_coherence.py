"""Regression cover for the published-chart coherence pass.

The two failures this guards against are both ones the pass itself
introduced during development, which is exactly why they are here:

  1. Clamping a value back into its move budget AFTER ordering put it
     above its neighbour again, so the pass shipped rails with fresh
     inversions of its own making.
  2. Measuring the boundary against the pre-rounding value left tail
     rows one or two above the chart's last position, which is the
     defect the pass exists to remove, in miniature.

Run:  python3 -m scripts.trends_scrapers.test_published_chart_coherence
"""

from __future__ import annotations

import random
import sys
from collections import Counter

try:
    from . import published_chart_coherence as C
    from . import run_guard
except ImportError:                                  # direct execution
    from scripts.trends_scrapers import published_chart_coherence as C
    from scripts.trends_scrapers import run_guard


def _rail(seed: int, scramble: float, n_pub: int = 10, n_tail: int = 25):
    rnd = random.Random(seed)
    base = rnd.choice([4_200, 18_000, 55_000, 240_000, 1_100_000])
    rows = []
    for i in range(n_pub):
        lvl = base * (0.97 ** i)
        rows.append({'title': f'pub {seed}-{i}',
                     'v': max(120, int(lvl * rnd.uniform(1 - scramble,
                                                         1 + scramble))),
                     'published_rank': i + 1})
    for i in range(n_tail):
        rows.append({'title': f'tail {seed}-{i}',
                     'v': max(110, int(base * (0.95 ** n_pub)
                                       * rnd.uniform(0.05, 1.05)))})
    return rows


def _run(rows, salt):
    return C.reconcile_rail(rows, salt=salt,
                            get_value=lambda r: r['v'],
                            set_value=lambda r, v: r.__setitem__('v', v))


def _pub_vals(rows):
    pub = sorted([r for r in rows if r.get('published_rank')],
                 key=lambda r: r['published_rank'])
    return [r['v'] for r in pub]


def test_descends_and_contains():
    """Across many rails: no inversion and no breach ever survives."""
    fails = []
    for regime, scramble in (('realistic', 0.12),
                             ('scrambled', 0.35),
                             ('pathological', 0.60)):
        held = 0
        for t in range(300):
            rows = _rail(t, scramble)
            rep = _run(rows, f'{regime}|{t}')
            if rep['held']:
                held += 1
                continue
            vals = _pub_vals(rows)
            if C.count_inversions(vals):
                fails.append((regime, t, 'inversion', vals))
            tail = sorted([r for r in rows if not r.get('published_rank')],
                          key=lambda r: -r['v'])
            if tail and vals and tail[0]['v'] >= vals[-1]:
                fails.append((regime, t, 'breach',
                              tail[0]['v'], vals[-1]))
        # A rail that cannot be reconciled is held, never forced. The
        # more incoherent the input, the more rails hold.
        assert held < 300, f'{regime}: every rail held, pass is inert'
    assert not fails, f'{len(fails)} failure(s): {fails[:4]}'
    print('  descent + containment: OK')


def test_holds_rather_than_forcing():
    """Two blocks on different scales are reported, not squeezed."""
    rows = [{'title': f'pub {i}', 'v': v, 'published_rank': i + 1}
            for i, v in enumerate([21_576, 19_254, 6_998, 6_554, 6_033,
                                   3_465, 3_173, 2_009, 1_768, 1_310])]
    rows += [{'title': f'tail {i}', 'v': v} for i, v in
             enumerate([21_004, 16_938, 11_582, 10_847, 9_372, 8_518,
                        8_484, 7_987, 7_421, 6_288, 5_905, 5_670])]
    before = [r['v'] for r in rows]
    rep = _run(rows, 'lionsgateplus|held')
    assert rep['held'], 'a rail on two scales must hold'
    assert [r['v'] for r in rows] == before, 'a held rail must not move'
    assert 'reasoning again' in rep['reason']
    print('  holds a two-scale rail without touching it:', rep['reason'][:60])


def test_no_ladder():
    """Gaps vary. An arithmetic progression would score cv 0."""
    cvs = []
    for t in range(300):
        rows = _rail(t, 0.12)
        if _run(rows, f'ladder|{t}')['held']:
            continue
        g = C.audit_gap_uniformity(_pub_vals(rows))
        if g['cv'] is not None:
            cvs.append(g['cv'])
            assert g['max_equal_run'] <= 2, f'constant delta run: {g}'
    cvs.sort()
    med = cvs[len(cvs) // 2]
    assert med > 0.15, f'gap spacing too regular, median cv {med}'
    print(f'  gap cv: min={cvs[0]:.3f} median={med:.3f} max={cvs[-1]:.3f}')


def test_last_digits_stay_natural():
    """About one value in ten ends in zero, and the guard passes."""
    vals = []
    for t in range(700):
        rows = _rail(t, 0.12)
        if _run(rows, f'digits|{t}')['held']:
            continue
        vals += [r['v'] for r in rows]
    counts = Counter(v % 10 for v in vals)
    zero_pct = 100.0 * counts[0] / len(vals)
    assert zero_pct >= run_guard.DIGIT_ZERO_MIN_PCT, (
        f'zeros at {zero_pct:.1f}%, the retired trailing-zero ban has '
        f'crept back in')
    res = run_guard.check_last_digit_distribution(vals)
    assert res is None or not res.get('alert'), res
    print(f'  {len(vals)} values, zeros {zero_pct:.1f}%, digit guard clean')


def test_unpublished_titles_are_bracketed():
    """A title the service published no figure for lands inside the
    interval its published neighbours define.

    The failure this guards is Top Gun: Maverick arriving at
    1,683,644 beside neighbours near 250,000, because the call was
    asked what its audience is rather than what belongs between two
    known numbers. Bounds are checked against a monotone envelope of
    the anchors, which is the tightest constraint that is actually
    satisfiable: before the coherence pass runs the published values
    do not necessarily descend among themselves, and where they cross
    no value can sit between them.
    """
    try:
        from . import chart_set_reasoning as CS
    except ImportError:
        from scripts.trends_scrapers import chart_set_reasoning as CS
    rnd = random.Random(3)
    outside = 0
    unsatisfiable = 0
    for t in range(300):
        rows, vals = [], {}
        for i in range(10):
            title = f't{t}-{i}'
            r = {'title': title, 'published_rank': i + 1}
            if rnd.random() > 0.35:
                r['weekly_views'] = rnd.randint(2_000_000, 12_000_000)
                vals[title] = int(r['weekly_views']
                                  * rnd.uniform(0.02, 0.09))
            else:
                vals[title] = rnd.randint(100_000, 4_000_000)
            rows.append(r)
        ordered = [r['title'] for r in rows]
        out = CS._bracket_unpublished(dict(vals), rows, ordered,
                                      f'p{t}', True, 4_285_714)
        for i, r in enumerate(rows):
            if r.get('weekly_views'):
                continue
            ups = [out[ordered[k]] for k in range(i)
                   if rows[k].get('weekly_views')]
            dns = [out[ordered[k]] for k in range(i + 1, 10)
                   if rows[k].get('weekly_views')]
            up = min(ups) if ups else None
            dn = max(dns) if dns else None
            if up is not None and dn is not None and up <= dn:
                unsatisfiable += 1
                continue
            v = out[r['title']]
            if up is not None and v >= up:
                outside += 1
            if dn is not None and v <= dn:
                outside += 1
    assert outside == 0, (
        f'{outside} bracketed placement(s) outside a satisfiable '
        f'interval')
    print(f'  0 placements outside a satisfiable bracket '
          f'({unsatisfiable} positions had crossing anchors)')


def test_share_products_descend():
    """The product of worldwide views and US share descends down the
    chart, and every share stays inside the credible band.

    That product is the quantity the service ranked by, so it is the
    thing that has to descend. Reasoning each share against its own
    title and hoping the order followed put #5 at twice #1.
    """
    try:
        from . import chart_set_reasoning as CS
    except ImportError:
        from scripts.trends_scrapers import chart_set_reasoning as CS
    rnd = random.Random(19)
    bad_order = 0
    bad_share = 0
    for t in range(300):
        rows, vals = [], {}
        for i in range(10):
            title = f's{t}-{i}'
            ww = rnd.randint(1_500_000, 14_000_000)
            rows.append({'title': title, 'published_rank': i + 1,
                         'weekly_views': ww})
            # A share reasoned per title, with no regard for order.
            vals[title] = int(ww * rnd.uniform(0.05, 0.70) * 0.15)
        ordered = [r['title'] for r in rows]
        out = dict(vals)
        unmet = CS._enforce_anchor_descent(out, rows, ordered,
                                           f's{t}', 0.15)
        seq = [out[x] for x in ordered]
        for i in range(1, len(seq)):
            if seq[i] >= seq[i - 1] and not unmet:
                bad_order += 1
        for r in rows:
            share = out[r['title']] / (r['weekly_views'] * 0.15)
            # A small tolerance: the solve clamps to the band edge.
            if share < CS._SHARE_MIN * 0.99 or share > CS._SHARE_MAX * 1.01:
                bad_share += 1
    assert bad_order == 0, f'{bad_order} pair(s) still out of order'
    assert bad_share == 0, f'{bad_share} share(s) outside the band'
    print('  products descend and every share stays credible')


def main() -> int:
    tests = [test_descends_and_contains, test_holds_rather_than_forcing,
             test_no_ladder, test_last_digits_stay_natural,
             test_unpublished_titles_are_bracketed,
             test_share_products_descend]
    bad = 0
    for t in tests:
        print(t.__name__)
        try:
            t()
        except AssertionError as e:
            bad += 1
            print('  FAIL:', e)
    print('OK' if not bad else f'{bad} failing')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
