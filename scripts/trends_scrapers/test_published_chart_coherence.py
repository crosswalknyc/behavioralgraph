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


def test_holds_the_tail_but_still_orders_the_chart():
    """A catalog on a different scale is reported, not squeezed. The
    published block is ordered anyway.

    These are two different problems and only one of them is a
    judgement call. Whether a catalog that reads above its own chart
    should be crushed to fit is genuinely arguable, and the answer
    here is no: say so and leave it. What the service ranks first
    reading highest is not arguable, and an earlier version of this
    pass returned out of the whole function on the tail condition, so
    a rail with an awkward catalog ALSO shipped its chart in whatever
    order the numbers happened to arrive in.
    """
    rows = [{'title': f'pub {i}', 'v': v, 'published_rank': i + 1}
            for i, v in enumerate([21_576, 19_254, 6_998, 6_554, 6_033,
                                   3_465, 3_173, 2_009, 1_768, 1_310])]
    tail = [{'title': f'tail {i}', 'v': v} for i, v in
            enumerate([21_004, 16_938, 11_582, 10_847, 9_372, 8_518,
                       8_484, 7_987, 7_421, 6_288, 5_905, 5_670])]
    rows += tail
    tail_before = [r['v'] for r in tail]
    rep = _run(rows, 'lionsgateplus|held')
    assert rep['held'], 'a catalog on two scales must hold'
    assert [r['v'] for r in tail] == tail_before, \
        'a held catalog must not move'
    assert 'reasoning again' in rep['reason']
    pub = sorted([r for r in rows if r.get('published_rank')],
                 key=lambda r: r['published_rank'])
    seq = [r['v'] for r in pub]
    inv = [i for i in range(1, len(seq)) if seq[i] >= seq[i - 1]]
    assert not inv, 'the published block shipped out of order on a ' \
                    'rail whose TAIL was the problem'
    print('  holds the catalog, orders the chart:', rep['reason'][:52])


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


def test_published_scale_is_physical_and_ordered():
    """The published figures set the chart's SCALE and today's order
    decides the positions, with every title bounded by its own
    worldwide count.

    Two failures this guards. A pairwise product constraint across
    two sources covering DIFFERENT periods is unsatisfiable by
    construction, which is what the daily rail plus last week's file
    produces. And deriving the chart's share from the values being
    replaced is circular: a first attempt did that, clamped to the
    top of the band, and put three titles above a 100% US share,
    meaning more US viewers than the title had worldwide.
    """
    try:
        from . import chart_set_reasoning as CS
    except ImportError:
        from scripts.trends_scrapers import chart_set_reasoning as CS
    rnd = random.Random(23)
    bad_order = bad_share = 0
    for t in range(300):
        rows, vals = [], {}
        for i in range(10):
            title = f'p{t}-{i}'
            ww = rnd.randint(1_500_000, 14_000_000)
            r = {'title': title, 'published_rank': i + 1,
                 'weekly_views': ww,
                 '_us_share': rnd.uniform(0.08, 0.62)}
            rows.append(r)
            # Values deliberately unrelated to the order, which is
            # the state the call leaves them in.
            vals[title] = rnd.randint(40_000, 3_000_000)
        ordered = [r['title'] for r in rows]
        out = dict(vals)
        CS._apply_published_scale(out, rows, ordered, f'p{t}', 0.15)
        seq = [out[x] for x in ordered]
        for i in range(1, len(seq)):
            if seq[i] >= seq[i - 1]:
                bad_order += 1
        for r in rows:
            share = out[r['title']] / (r['weekly_views'] * 0.15)
            if share > 1.0:
                bad_share += 1
    assert bad_order == 0, f'{bad_order} pair(s) out of order'
    assert bad_share == 0, (
        f'{bad_share} title(s) implying more US viewers than they had '
        f'worldwide')
    print('  scale ordered by today, every share physically possible')


def test_nothing_lands_over_the_ceiling():
    """No reading this pass writes may exceed the platform's cap.

    Live defect, Tubi, 2026-09-23. The pass clamped the top of the
    chart to the daily cap and then re-applied natural last digits,
    which rounded it 18 UP, to 857,160 against a cap of 857,142. The
    render clamps anything over the cap and substitutes the title's
    own last reading, so the board showed #1 at 175,919 under a #2 of
    857,110: an inversion at the very top of the rail, produced by the
    pass that exists to remove inversions.

    Scoped to what this pass WRITES. Rows that arrive over the cap are
    the previous pass's job (`_reclamp_carried_to_platform_ceiling`
    runs on both sides of this one), so the rail is seeded under the
    cap and the question is whether the pass pushes anything through
    it. The top row sits exactly ON the cap, which is where the
    rounding bit.
    """
    over = 0
    checked = 0
    for seed in range(60):
        rows = _rail(seed, 0.35)
        ceiling = max(r['v'] for r in rows)
        C.reconcile_rail(rows, salt=f'cap{seed}',
                         get_value=lambda r: r['v'],
                         set_value=lambda r, v: r.__setitem__('v', v),
                         ceiling=ceiling)
        for r in rows:
            checked += 1
            if r['v'] > ceiling:
                over += 1
    assert over == 0, (
        f'{over} of {checked} reading(s) left above the platform '
        f'ceiling; the render will replace every one of them')
    print(f'  {checked} readings, none above the ceiling')


def test_descends_even_when_the_ceiling_binds():
    """A chart whose top wants more than the platform allows still
    has to read downwards.

    Live defect, Tubi, 2026-09-23. Positions 1, 2 and 3 all reasoned
    above the 857,142 daily cap, each was clamped to the cap on its
    own, and the per-title last digit then decided the order: the rail
    shipped 857,105 / 857,110 / 857,118, ascending, at the top of the
    page. Clamping row by row throws the ordering away exactly when
    the chart is most visible.
    """
    bad = 0
    rails = 0
    for seed in range(60):
        rows = _rail(seed, 0.30)
        pub = [r for r in rows if r.get('published_rank')]
        # A cap most of the published block wants to exceed, which is
        # the condition that produced the defect.
        ceiling = int(sorted((r['v'] for r in pub), reverse=True)[
            min(3, len(pub) - 1)])
        C.reconcile_rail(rows, salt=f'bind{seed}',
                         get_value=lambda r: r['v'],
                         set_value=lambda r, v: r.__setitem__('v', v),
                         ceiling=ceiling)
        rails += 1
        seq = [r['v'] for r in sorted(pub, key=lambda x: x['published_rank'])]
        bad += sum(1 for i in range(1, len(seq)) if seq[i] >= seq[i - 1])
    assert bad == 0, (
        f'{bad} position pair(s) not descending across {rails} rails '
        f'whose ceiling binds on the top of the chart')
    print(f'  {rails} ceiling-bound rails, every published block descends')


def test_number_one_is_lifted_over_number_two():
    """The title a service ranks first must read highest, however far
    under its neighbour it arrived.

    Live defect, HBO Max, 2026-09-24. HBO Max ranks Lanterns first.
    Our reading for it was 629,386 while 1000-lb Sisters, their number
    2, read 1.62M. Every other position was already in the right order,
    so the rail rendered their 2 through 9 as our 1 through 4 and 6
    through 9, with their number 1 sitting at our 5.

    The cause was an asymmetry at the top of the chart. Every other
    position is bracketed by a neighbour on each side, but position 1
    has only a lower bound, and the solver walked DOWN from it capping
    each value inside its own move budget. A number 1 that arrived too
    low therefore could not be lifted, the rail came back infeasible,
    and holding it shipped the block in exactly the wrong order.

    Jenna's bar, verbatim: "if lanterns is no 1 on hbo it should be no
    1 with us".
    """
    rows = [
        {'title': 'Lanterns', 'v': 629_386, 'published_rank': 1},
        {'title': '1000-lb Sisters', 'v': 1_619_993, 'published_rank': 2},
        {'title': 'Youth', 'v': 1_285_026, 'published_rank': 3},
        {'title': 'A Killer Story', 'v': 892_053, 'published_rank': 4},
        {'title': 'Stuart Fails', 'v': 713_979, 'published_rank': 5},
        {'title': '1000-lb Roomies', 'v': 583_023, 'published_rank': 6},
        {'title': '90 Day Last Resort', 'v': 440_976, 'published_rank': 7},
        {'title': 'President Curtis', 'v': 327_099, 'published_rank': 8},
        {'title': 'Halloween Baking', 'v': 268_015, 'published_rank': 9},
        {'title': 'Toxic', 'v': 218_994, 'published_rank': 10},
        # The real rail's catalog: 182 rows of which only two breach,
        # so the tail reconciles rather than holding. A two-row tail
        # that both breach is a different case and is covered above.
        {'title': 'Banshee', 'v': 228_522},
        {'title': 'Sicario', 'v': 282_785},
    ] + [{'title': f'catalog {i}', 'v': 200_000 - i * 900}
         for i in range(40)]
    rep = C.reconcile_rail(rows, salt='hbomax|series|2026-09-24',
                           get_value=lambda r: r['v'],
                           set_value=lambda r, v: r.__setitem__('v', v),
                           ceiling=3_000_000)
    pub = sorted([r for r in rows if r.get('published_rank')],
                 key=lambda r: r['published_rank'])
    seq = [r['v'] for r in pub]
    assert not rep.get('held'), f'rail was held: {rep.get("reason")}'
    inv = [i for i in range(1, len(seq)) if seq[i] >= seq[i - 1]]
    assert not inv, f'{len(inv)} inversion(s) left in the published block'

    # The acceptance test Jenna actually applies: sort our numbers and
    # the service's own order has to come back.
    ours = [r['title'] for r in sorted(pub, key=lambda r: -r['v'])]
    theirs = [r['title'] for r in pub]
    assert ours == theirs, (
        f'sorting our numbers does not reproduce their order\n'
        f'  ours  : {ours}\n  theirs: {theirs}')
    assert pub[0]['title'] == 'Lanterns' and pub[0]['v'] == max(seq), (
        'their number 1 does not carry the largest reading on the rail')

    # Containment still holds at the other edge: nothing they left off
    # the chart may out-draw the title they rank last.
    floor = seq[-1]
    over = [r['title'] for r in rows
            if not r.get('published_rank') and r['v'] >= floor]
    assert not over, f'uncharted rows above the chart floor: {over}'
    print(f"  Lanterns lifted {629_386:,} -> {pub[0]['v']:,}, leads the "
          f"rail, order reproduced, {len(rows) - len(pub)} tail rows "
          f"contained")


def test_a_blank_collection_is_not_a_chart():
    """A row with no collection is on no chart.

    Live defect, Paramount+, 2026-09-24. The collection match is
    deliberately loose, because a storefront names the same rail
    slightly differently by page and an exact set would silently drop
    a chart the day the wording moved. But `'' in anything` is true,
    so every row carrying a BLANK collection matched every pattern.
    Paramount+'s 182 catalog rows all carry one, so the whole catalog
    read as charted, got numbered 1 to 182, and the board grew a third
    chart group the service never published, led by a title that is
    not on either of its real rails.
    """
    try:
        from . import stream_estimates as se
    except ImportError:
        from scripts.trends_scrapers import stream_estimates as se

    snap = {'national': [
        {'title': 'Lanterns', 'collection': 'Most Watched Shows',
         'category_display': 'TV'},
        {'title': 'Bridgerton', 'collection': 'Most Watched Shows',
         'category_display': 'TV'},
        {'title': 'Zamboni', 'collection': '', 'category_display': 'TV'},
        {'title': 'Quokka', 'category_display': 'TV'},
        {'title': 'Wombat', 'collection': '   ',
         'category_display': 'Film'},
    ]}
    idx = se.published_chart_index('paramountplus', snap)
    charted = {k for k in idx if ':' in k}
    assert any('lanterns' in k for k in charted), \
        'the real chart row was dropped'
    for stray in ('zamboni', 'quokka', 'wombat'):
        assert not any(stray in k for k in idx), (
            f'{stray!r} has no collection and must not be charted')
    print(f'  {len(charted)} charted key(s), no blank-collection row '
          f'among them')


def main() -> int:
    tests = [test_descends_and_contains, test_holds_the_tail_but_still_orders_the_chart,
             test_no_ladder, test_last_digits_stay_natural,
             test_unpublished_titles_are_bracketed,
             test_published_scale_is_physical_and_ordered,
             test_nothing_lands_over_the_ceiling,
             test_descends_even_when_the_ceiling_binds,
             test_number_one_is_lifted_over_number_two,
             test_a_blank_collection_is_not_a_chart]
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
