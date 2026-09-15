"""Read the rendered Trends IQ payload and report what a reader sees.

Audit-only. Never writes anything, never mutates a snapshot. Used to
capture the board's state before and after a change so the two can be
compared row for row:

  * per list: distinct scope labels and their counts
  * per list: top and median audience value (proves levels held)
  * per list: ordering violations (a row above another with a smaller
    value) and whether ranks run dense from 1
  * board-wide: how rows got their value (researched, carried forward,
    or the last-resort rank tier)
  * board-wide: movement chips over a given percentage

    python3 -m scripts.trends_scrapers.audit_board_state --out /tmp/before.json
    python3 -m scripts.trends_scrapers.audit_board_state \
        --compare /tmp/before.json --out /tmp/after.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_TITLE_KEYS = ('title', 'term', 'name', 'display_name', 'query', 'show',
               'channel_name', 'headline')
_SKIP_CARD_KEYS = {'lens_config', 'lens_scores', 'lens_cutoffs'}

_HOUSEHOLD_TERMS = ('household', 'households', 'hh', 'hhs')

# Scope brackets naming a service other than the row's own list. Filled
# from the list path so the check stays generic.
_SERVICE_BY_PATH_HINT = {
    'netflix': 'netflix', 'hulu': 'hulu', 'max': 'max',
    'disneyplus': 'disney', 'primevideo': 'prime', 'paramountplus': 'paramount',
    'peacock': 'peacock', 'appletv': 'apple', 'roku': 'roku', 'tubi': 'tubi',
    'pluto': 'pluto', 'xumo': 'xumo', 'starz': 'starz', 'mgmplus': 'mgm',
    'britbox': 'britbox', 'amcplus': 'amc',
}

# A rail that is one service carried on another names both, and both
# are its own. Starz on Amazon is the case: the label has to say the
# service whose catalog it is and the path it is carried through, or
# the number reads as all Starz viewing. Longest path hint wins, so
# `starz_amazon` resolves here rather than falling to `starz`.
_EXTRA_OWN_SERVICES = {
    'starz_amazon': {'starz', 'prime'},
}


def _item_title(it: dict) -> str:
    for k in _TITLE_KEYS:
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _audience_block(it: dict) -> Optional[dict]:
    for f in ('us_streams', 'us_readers'):
        blk = it.get(f)
        if isinstance(blk, dict):
            try:
                if float(blk.get('us_estimate') or 0) > 0:
                    return blk
            except (TypeError, ValueError):
                pass
    return None


def _walk(cards: dict):
    """Yield (path, index, item) for every rendered row."""
    def _w(node, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                if not path and k in _SKIP_CARD_KEYS:
                    continue
                yield from _w(v, f'{path}.{k}' if path else k)
            return
        if not isinstance(node, list):
            return
        items = [x for x in node if isinstance(x, dict) and _item_title(x)]
        if items:
            for i, it in enumerate(items):
                yield path, i, it
            return
        for x in node:
            if isinstance(x, (dict, list)):
                yield from _w(x, path)
    yield from _w(cards or {}, '')


def _median(vals: list) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return float(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0)


def audit(filters: dict, force_refresh: bool = False) -> dict[str, Any]:
    import trends_iq
    payload = trends_iq.compute_view(dict(filters),
                                      force_refresh=force_refresh)
    cards = (payload or {}).get('cards') or {}

    per_list: dict[str, dict] = {}
    basis_counts: dict[str, int] = {}
    household_hits: list = []
    wrong_service: list = []
    big_chips: list = []
    last_digit: dict[str, int] = {str(d): 0 for d in range(10)}

    for path, idx, it in _walk(cards):
        blk = _audience_block(it)
        bucket = per_list.setdefault(path, {
            'rows': 0, 'valued': 0, 'labels': {}, 'values': [],
            'seq': [], 'ranks': [],
        })
        bucket['rows'] += 1
        try:
            rk = int(it.get('rank')) if it.get('rank') is not None else None
        except (TypeError, ValueError):
            rk = None
        bucket['ranks'].append(rk)
        if not blk:
            bucket['seq'].append(None)
            continue
        bucket['valued'] += 1
        val = int(float(blk.get('us_estimate') or 0))
        bucket['values'].append(val)
        bucket['seq'].append(val)
        last_digit[str(val % 10)] += 1

        lab = str(blk.get('unit_label') or '').strip()
        bucket['labels'][lab] = bucket['labels'].get(lab, 0) + 1
        bucket.setdefault('windows', {})
        cad = lab.split(' ', 1)[0].lower() if lab else '(none)'
        bucket['windows'][cad] = bucket['windows'].get(cad, 0) + 1

        basis = str(blk.get('est_basis') or 'researched')
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
        bucket.setdefault('basis', {})
        bucket['basis'][basis] = bucket['basis'].get(basis, 0) + 1

        low = lab.lower()
        if any(f' {t} ' in f' {low} ' or low.endswith(f' {t}')
               for t in _HOUSEHOLD_TERMS):
            household_hits.append({'path': path, 'title': _item_title(it),
                                    'label': lab})

        if '(' in lab or 'streaming on' in low or ' on ' in low:
            own = None
            owned: set = set()
            plow = path.lower()
            for hint, extra in _EXTRA_OWN_SERVICES.items():
                if hint in plow:
                    owned |= extra
            for hint, svc in _SERVICE_BY_PATH_HINT.items():
                if hint in plow:
                    own = svc
                    break
            if own:
                owned.add(own)
            for hint, svc in _SERVICE_BY_PATH_HINT.items():
                if svc in low and (not owned or svc not in owned):
                    wrong_service.append({'path': path,
                                           'title': _item_title(it),
                                           'label': lab,
                                           'own_service': own,
                                           'named': svc})
                    break

        # delta_pct is a fraction: 5.0 is a 500 percent move.
        try:
            d = abs(float(blk.get('delta_pct') or 0))
            if d > 5.0:
                big_chips.append({'path': path, 'title': _item_title(it),
                                   'pct': round(d * 100.0, 1)})
        except (TypeError, ValueError):
            pass

    lists_out: dict[str, Any] = {}
    for path, b in sorted(per_list.items()):
        vals = b['values']
        seq = [v for v in b['seq'] if v is not None]
        inversions = sum(1 for a, c in zip(seq, seq[1:]) if c > a)
        ranks = [r for r in b['ranks'] if r is not None]
        dense = (ranks == list(range(1, len(ranks) + 1))) if ranks else None
        lists_out[path] = {
            'rows': b['rows'],
            'valued': b['valued'],
            'labels': dict(sorted(b['labels'].items(),
                                   key=lambda kv: -kv[1])),
            'n_labels': len(b['labels']),
            'top': max(vals) if vals else 0,
            'median': _median(vals),
            'inversions': inversions,
            'ranks_dense_from_1': dense,
            'basis': b.get('basis', {}),
            'windows': b.get('windows', {}),
            'n_windows': len(b.get('windows', {})),
            'strictly_descending': inversions == 0,
        }

    # Household language anywhere in the payload, not just in scope
    # labels. HHI as an income bucket is allowed by the standing rule
    # and is counted apart.
    import json as _json
    blob = _json.dumps(cards, default=str)
    hh_anywhere = {}
    for term in ('households', 'household', 'HHs', 'HHI', ' HH '):
        n = blob.count(term)
        if n:
            hh_anywhere[term] = n

    return {
        'filters': filters,
        'household_anywhere': hh_anywhere,
        'lists': lists_out,
        'basis_counts': basis_counts,
        'household_hits': household_hits,
        'wrong_service': wrong_service,
        'big_chips': big_chips,
        'last_digit': last_digit,
        'totals': {
            'rows': sum(v['rows'] for v in lists_out.values()),
            'valued': sum(v['valued'] for v in lists_out.values()),
            'inversions': sum(v['inversions'] for v in lists_out.values()),
            'lists_multi_label': sum(1 for v in lists_out.values()
                                      if v['n_labels'] > 1),
            'lists_multi_window': sum(1 for v in lists_out.values()
                                       if v['n_windows'] > 1),
        },
    }


def _print_report(rep: dict, compare: Optional[dict] = None) -> None:
    t = rep['totals']
    print(f"rows={t['rows']} valued={t['valued']} "
          f"ordering_inversions={t['inversions']} "
          f"lists_with_mixed_labels={t['lists_multi_label']} "
          f"lists_with_mixed_WINDOWS={t['lists_multi_window']}")
    print(f"basis: {rep['basis_counts']}")
    print(f"household terms in labels : {len(rep['household_hits'])}")
    print(f"household terms in payload: "
          f"{rep.get('household_anywhere') or 'none'}")
    print(f"wrong-service brackets    : {len(rep['wrong_service'])}")
    print(f"chips over 500 percent    : {len(rep['big_chips'])}")
    ld = rep['last_digit']
    tot = sum(ld.values()) or 1
    print("last digit distribution   : " + "  ".join(
        f"{d}:{100.0 * ld[d] / tot:.1f}%" for d in sorted(ld)))
    print()
    print(f"{'list':<46} {'rows':>5} {'lbls':>5} {'win':>4} {'inv':>4} "
          f"{'dense':>6} {'top':>12} {'median':>12}")
    for path, v in sorted(rep['lists'].items()):
        print(f"{path[:46]:<46} {v['rows']:>5} {v['n_labels']:>5} "
              f"{v['n_windows']:>4} "
              f"{v['inversions']:>4} {str(v['ranks_dense_from_1']):>6} "
              f"{v['top']:>12,} {v['median']:>12,.0f}")
    multi = {k: v['windows'] for k, v in rep['lists'].items()
             if v['n_windows'] > 1}
    if multi:
        print("\nlists still carrying more than one window:")
        for k, w in sorted(multi.items()):
            print(f"  {k:<46} {w}")

    if compare:
        print("\nlevel movement vs baseline (top / median per list):")
        moved = 0
        for path, v in sorted(rep['lists'].items()):
            old = (compare.get('lists') or {}).get(path)
            if not old:
                continue
            if old['top'] != v['top'] or abs(old['median'] - v['median']) > 0.5:
                moved += 1
                print(f"  {path[:46]:<46} top {old['top']:>12,} -> "
                      f"{v['top']:>12,}   median {old['median']:>12,.0f} -> "
                      f"{v['median']:>12,.0f}")
        print(f"  lists whose top or median moved: {moved}")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description='Trends IQ board audit')
    ap.add_argument('--out')
    ap.add_argument('--compare')
    ap.add_argument('--force-refresh', action='store_true')
    ap.add_argument('--lookback-days', type=int, default=1)
    args = ap.parse_args(argv)

    filters = {'geo_type': 'National', 'geo_value': '',
               'lookback_days': args.lookback_days}
    rep = audit(filters, force_refresh=args.force_refresh)
    base = None
    if args.compare:
        with open(args.compare) as fh:
            base = json.load(fh)
    _print_report(rep, base)
    if args.out:
        with open(args.out, 'w') as fh:
            json.dump(rep, fh, indent=1)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
