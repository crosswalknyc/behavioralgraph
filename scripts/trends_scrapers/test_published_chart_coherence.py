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


def main() -> int:
    tests = [test_descends_and_contains, test_holds_rather_than_forcing,
             test_no_ladder, test_last_digits_stay_natural]
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
