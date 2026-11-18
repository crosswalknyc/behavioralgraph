"""A Digital Journey on a partnership we already valued agrees with the valuation.

Jenna, 2026-11-17, on the Coach x WNBA journey: "make sure the values align
with the brand partnership valuation that was pulled this morning."

`ip-assessment-researched-synth.mdc` says a journey never starts cold on a
subject we already hold, and names Brand Partnership IQ as the next product
`corpus_anchors` should carry. Until this module the valuation reached the
journey only as a few published figures in the research prompt, so the two
products could still disagree on the people reached, the people who engaged
with the brand, and the people who converted.

What this does, on any journey whose subject has a Brand Partnership IQ
valuation in the corpus:

  1. `find_valuation` loads the valuation and reads the counts both products
     share: the audience the partnership reached, the people who engaged with
     the brand in the window, the people who converted, the year before, the
     per-platform engagement, the demographics and the campaign content.
  2. `prompt_block` hands them to the research as binding.
  3. `seat_primitives` puts the two shared stages at the top of the nest and
     re-levels the shopping steps between them and the order so the path ends
     on the valuation's conversions.
  4. `hold` lands the three shared stages on the valuation's counts exactly,
     re-solves the leave and return fork on them and keeps every table on its
     parent.
  5. `finish` adds the valuation's own tables (where they engaged, the growth
     in the brand's audience, who they are), rewrites the facts and the copy
     from the final counts, and takes out the two whole-market tables the
     purchase standard adds, which count buyers the valuation does not.

It only ever fires when a valuation exists; no valuation, no store, a failure
anywhere or JOURNEY_BPIQ_ANCHOR=0 leaves the build exactly as it was. Nothing
a reader sees says how any of this was produced.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from typing import Optional

BUCKET = 'dashboard-inputs'
US_GEN_POP = 329_900_000
REACHED_ID, ENGAGED_ID = 'reached', 'engaged'
# the two tables the purchase standard derives for a whole market; on a
# partnership the valuation already counts every converter in the window
_DROP_TITLES = re.compile(r"^the rest of the social|^every online .*buyer", re.I)
_DROP_FACTS = frozenset(('The rest',))

VALUATION_RULE = """

THE PARTNERSHIP VALUATION IS BINDING. The input carries valuation_anchor:
Crosswalk already published a Brand Partnership IQ valuation on this subject
for this window, and this journey is the same people in the same window.
- The nest has exactly six stages, with these ids, in this order:
  "reached" (valuation_anchor.reached: the people who saw the talent's or
  property's content; they did NOT all see the brand's content),
  "engaged" (valuation_anchor.engaged: the people who engaged with the
  brand's content), "search" (looked the brand or a piece up, or went to
  the brand's site), "page" (opened a product page), "cart" (added a piece
  to the bag), and the order, which lands on valuation_anchor.converted.
  Set the kept rates so the path arrives there; they are high because the
  window is a whole season.
- A table about the BRAND's content, posts, films or pages divides the
  engaged people ("of": "stage:engaged"), never the reached people. A
  table "of": "stage:reached" is only about the talent's or property's own
  content.
- Use these exact titles for these tables: "First <brand> content they
  saw" (partition of stage:engaged; rows are the named partnership content
  from valuation_anchor.content, .properties and .campaign_notes), "What
  they bought first" (partition of conversion; named pieces), "Last touch
  before the order" (partition of conversion), "What assisted the order"
  (overlap of conversion), "Time from the first content to the order"
  (partition of conversion: same day, days 1 to 7, days 8 to 30, days 31
  to 90, more than 90 days). Do not write a table of which platform the
  first content sat on; the valuation's platform counts carry that.
- Every content row, first-touch row and clickstream URL is a real
  campaign beat from valuation_anchor.content, .properties and
  .campaign_notes, never a placeholder.
- Clickstream: step 1 is the reached people, with the talent's, the
  property's and press URLs only. From step 2 on a step never holds more
  people than valuation_anchor.engaged, and the people on a brand URL on a
  social platform never exceed that platform's count in
  valuation_anchor.engaged_by_platform.
- People on a named property never exceed its hits in the valuation.
- Never restate a dollar value, and never print a count for the audience,
  the engaged people or the buyers other than the three above."""


_SURFACE_TITLE = re.compile(r"where the first .*(?:post|content) sat|platform of the first|first .*surface", re.I)
_MET_TITLE = re.compile(r"how they (?:first )?(?:met|found|reached)|discovery mix|how .* first reached", re.I)
_BOUGHT_TITLE = re.compile(r"^(?:of the (?:people|buyers) who bought,? )?what (?:they|the buyers) bought$", re.I)
_FIRST_CONTENT = re.compile(r"^first .*(?:content|post) they saw", re.I)
_SEARCH_BASED = re.compile(r"^how many posts before|^what they searched for", re.I)
_PLATFORM_HOSTS = (('instagram.com', 'Instagram'), ('tiktok.com', 'TikTok'), ('youtube.com', 'YouTube'), ('youtu.be', 'YouTube'),
                   ('facebook.com', 'Facebook'), ('twitter.com', 'X (Twitter)'), ('x.com', 'X (Twitter)'),
                   ('pinterest.com', 'Pinterest'), ('reddit.com', 'Reddit'), ('threads.net', 'Threads'), ('threads.com', 'Threads'),
                   ('snapchat.com', 'Snapchat'), ('linkedin.com', 'LinkedIn'), ('twitch.tv', 'Twitch'))


def enabled() -> bool:
    return str(os.environ.get('JOURNEY_BPIQ_ANCHOR', '1')).strip().lower() not in ('0', 'false', 'off', 'no')


def _h(*parts) -> int:
    return int(hashlib.blake2b('|'.join(str(p) for p in parts).encode(), digest_size=8).hexdigest(), 16)


def _int(v) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ''


def _join(names: list, last: str = 'or') -> str:
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return ''
    if len(names) == 1:
        return names[0]
    return ', '.join(names[:-1]) + f' {last} ' + names[-1]


def from_payload(key: str, p: dict) -> Optional[dict]:
    """The counts a journey shares with a Brand Partnership IQ payload, or
    None when the payload cannot anchor one (no conversions, a nest that
    does not narrow)."""
    if not isinstance(p, dict):
        return None
    tot = p.get('totals') or {}
    conv = p.get('conversions') or {}
    val = p.get('valuation') or {}
    reached = _int(p.get('projected_audience_size'))
    engaged = _int(tot.get('post_users_projected'))
    converted = _int(conv.get('post_users_projected'))
    if conv.get('enabled') is False or not (reached > engaged > converted > 0):
        return None
    ev = p.get('event_period') or {}
    qual = p.get('qualifier_value')
    names = [str(q) for q in qual] if isinstance(qual, list) else ([str(qual)] if qual else [])
    platforms = []
    for r in p.get('per_platform') or []:
        n = _int((r or {}).get('post_users_projected'))
        if n > 0 and str((r or {}).get('platform') or '').strip():
            platforms.append({'platform': str(r['platform']).strip(), 'people': n,
                              'people_before': _int(r.get('pre_users_projected'))})
    platforms.sort(key=lambda r: -r['people'])
    sent = p.get('sentiment') or {}
    content = [{'name': str(c.get('common_name') or '').strip(), 'url': str(c.get('url') or '').strip(),
                'read': str(c.get('summary') or '').strip()}
               for c in (sent.get('top_positive') or []) + (sent.get('top_negative') or [])
               if isinstance(c, dict) and str(c.get('common_name') or '').strip()]
    scale = (reached / float(p.get('audience_size'))) if _int(p.get('audience_size')) > 0 else 0.0
    props = [{'name': str(c.get('common_name') or '').strip(),
              'hits': _int(c.get('hits_projected')) or _int(_int(c.get('hits')) * scale)}
             for c in (p.get('top_brand_properties') or [])
             if isinstance(c, dict) and str(c.get('common_name') or '').strip()]
    demo = ((p.get('demographics') or {}).get('post') or {})
    who = {}
    for k in ('gender', 'age', 'ethnicity', 'income'):
        rows = []
        for r in (demo.get(k) or []):
            if not isinstance(r, dict):
                continue
            label = r.get('label') or r.get('name') or r.get('bucket') or (r.get('value') if isinstance(r.get('value'), str) else '')
            pct = next((r[f] for f in ('pct', 'percentage', 'share') if isinstance(r.get(f), (int, float))), 0)
            rows.append({'label': str(label or '').strip(), 'pct': float(pct)})
        rows = [r for r in rows if r['label']]
        if rows:
            who[k] = rows
    return {
        'key': key, 'project': str(p.get('project_name') or '').strip(), 'brand': str(p.get('brand_partner') or '').strip(),
        'qualifier': names, 'qualifier_type': str(p.get('qualifier_type') or ''),
        'window': {'start': str(ev.get('start') or p.get('start_date') or ''), 'end': str(ev.get('end') or p.get('end_date') or '')},
        'reached': reached, 'engaged': engaged, 'converted': converted,
        'engaged_before': _int(tot.get('pre_users_projected')), 'converted_before': _int(conv.get('pre_users_projected')),
        'added': _int(val.get('incremental_users')) or max(engaged - _int(tot.get('pre_users_projected')), 0),
        'order_value': float(((val.get('rates') or {}).get('conv_value_per_user')) or 0),
        'conversion_value': float(val.get('conversion_value') or 0),
        'platforms': platforms, 'content': content, 'properties': props, 'who': who,
        'campaign_notes': str((p.get('diagnostics') or {}).get('synthesis_note') or '')[:2400],
    }


def find_valuation(inputs: dict, anchors: Optional[dict] = None, s3=None) -> Optional[dict]:
    """The Brand Partnership IQ valuation on this journey's subject, or None.
    An explicit inputs['valuation_key'] wins; otherwise the corpus catalog's
    newest valuation fact on the subject names the file."""
    if not enabled():
        return None
    try:
        key = str((inputs or {}).get('valuation_key') or '').strip()
        if not key:
            facts = ((anchors or {}).get('catalog') or {}).get('facts') or []
            cands = [f for f in facts if isinstance(f, dict) and f.get('product') == 'bpiq'
                     and str((f.get('source') or {}).get('key') or '').startswith('brand-partnership-iq/')]
            cands.sort(key=lambda f: str(f.get('as_of') or ''), reverse=True)
            key = str((cands[0].get('source') or {}).get('key')) if cands else ''
        if not key:
            return None
        if s3 is None:
            import boto3
            s3 = boto3.client('s3', region_name='us-east-2')
        body = s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        if key.endswith('.gz'):
            import gzip
            body = gzip.decompress(body)
        return from_payload(key, json.loads(body))
    except Exception as exc:
        print(f'[journey] valuation anchor skipped: {exc}')
        return None


def stage_words(val: dict) -> dict:
    brand = val.get('brand') or 'the brand'
    names = val.get('qualifier') or []
    who_or = _join(names, 'or') or (val.get('project') or 'the partnership')
    who_and = _join(names, 'and') or who_or
    return {
        'reached_label': f'Saw {who_or} content',
        'reached_doing': f'Watched, read or followed {who_and} in the window: the audience the partnership reached',
        'reached_where': 'Social, video, press and search',
        'engaged_label': f'Engaged with {brand} content',
        'engaged_doing': (f'At least one {brand} touchpoint in the window: a brand or campaign post, a campaign film, '
                          f'a visit to the site'),
        'engaged_where': f'{brand} on social, video and its own site',
    }


def prompt_block(val: dict) -> dict:
    w = stage_words(val)
    return {
        'note': ('Crosswalk already published a Brand Partnership IQ valuation on this subject. This journey is the same '
                 'people in the same window. The three counts below are fixed.'),
        'partnership': val.get('project'), 'brand': val.get('brand'), 'window': val.get('window'),
        'reached': {'id': REACHED_ID, 'label': w['reached_label'], 'people': val['reached']},
        'engaged': {'id': ENGAGED_ID, 'label': w['engaged_label'], 'people': val['engaged'],
                    'year_before': val.get('engaged_before'), 'added_in_window': val.get('added')},
        'converted': {'people': val['converted'], 'year_before': val.get('converted_before')},
        'engaged_by_platform': [{'platform': r['platform'], 'people': r['people']} for r in val.get('platforms') or []],
        'content': val.get('content') or [], 'properties': val.get('properties') or [],
        'campaign_notes': val.get('campaign_notes') or '',
    }


def seat_primitives(prim: dict, val: dict, tam: int = US_GEN_POP, platform: str = '') -> dict:
    """The nest opens on the valuation's two stages and arrives on its
    conversions. The research's own shopping steps keep their order and
    their relative tightness; only their level moves."""
    prim = dict(prim or {})
    nest = [dict(s) for s in (prim.get('nest') or []) if isinstance(s, dict)]
    w = stage_words(val)
    ids = [str(s.get('id') or '') for s in nest]
    if REACHED_ID in ids and ENGAGED_ID in ids:
        rest = [s for s in nest if str(s.get('id') or '') not in (REACHED_ID, ENGAGED_ID)]
        old = {}
    else:
        cut = 2 if len(nest) >= 4 else 1
        old = {str(s.get('id') or ''): (REACHED_ID if i == 0 else ENGAGED_ID) for i, s in enumerate(nest[:cut])}
        rest = nest[cut:]
    if not rest:
        return prim
    if len(rest) == 1:
        site = str(platform or '').split(' (')[0].strip() or 'the site'
        rest.insert(0, {'id': 'site', 'label': f'Went to {site}', 'doing': f'Opened {site} in the window', 'where': site,
                        'kept_of_prior_pct': 46.37, 'next': rest[0].get('label') or '', 'surface': site, 'job': '', 'timing': ''})
    target = val['converted'] / float(val['engaged'])
    rates = [min(max(float(s.get('kept_of_prior_pct') or 50.0) / 100.0, 0.02), 0.97) for s in rest]
    have = math.prod(rates)
    if 0 < have < 1 and 0 < target < 1:
        e = math.log(target) / math.log(have)
        rates = [r ** e for r in rates]
        # a step cannot keep nearly everyone; spill the excess evenly
        for _ in range(8):
            over = [i for i, r in enumerate(rates) if r > 0.93]
            if not over:
                break
            under = [i for i in range(len(rates)) if i not in over]
            if not under:
                break
            spill = math.prod(rates[i] / 0.93 for i in over)
            for i in over:
                rates[i] = 0.93 - 0.011 * (_h(val.get('key'), 'cap', i) % 5)
            adj = (spill * math.prod(0.93 / rates[i] for i in over)) ** (1.0 / len(under))
            for i in under:
                rates[i] *= adj
    for s, r in zip(rest, rates):
        s['kept_of_prior_pct'] = r * 100.0
        s.pop('share_of_tam_pct', None)
    head = [
        {'id': REACHED_ID, 'label': w['reached_label'], 'doing': w['reached_doing'], 'where': w['reached_where'],
         'share_of_tam_pct': val['reached'] / float(tam) * 100.0, 'next': w['engaged_label'],
         'surface': 'Social and video', 'job': 'Reach', 'timing': 'In the window'},
        {'id': ENGAGED_ID, 'label': w['engaged_label'], 'doing': w['engaged_doing'], 'where': w['engaged_where'],
         'kept_of_prior_pct': val['engaged'] / float(val['reached']) * 100.0, 'next': rest[0].get('label') or '',
         'surface': 'Brand and campaign content', 'job': 'Engagement', 'timing': 'In the window'},
    ]
    for src in nest:
        tgt = old.get(str(src.get('id') or ''))
        if tgt:
            h = head[0] if tgt == REACHED_ID else head[1]
            for k in ('surface', 'job', 'timing'):
                if str(src.get(k) or '').strip():
                    h[k] = src[k]
    prim['nest'] = head + rest
    brand = str(val.get('brand') or '').strip()
    dets = []
    has_first = any(_FIRST_CONTENT.search(str(d.get('title') or '')) for d in (prim.get('detours') or []) if isinstance(d, dict))
    for d in prim.get('detours') or []:
        if not isinstance(d, dict):
            continue
        d = dict(d)
        of = str(d.get('of') or '')
        sid = of.split(':', 1)[-1] if of.startswith('stage:') else ''
        if sid in old:
            d['of'] = of = f'stage:{old[sid]}'
        title = str(d.get('title') or '')
        if _SURFACE_TITLE.search(title):                # the valuation's platform table carries this
            continue
        if _MET_TITLE.search(title) and str(d.get('kind') or 'partition') == 'partition' and brand and not has_first:
            d['title'], d['of'] = f'First {brand} content they saw', f'stage:{ENGAGED_ID}'
        elif of == f'stage:{REACHED_ID}' and brand and re.search(rf"\b{re.escape(brand)}\b", title, re.I):
            d['of'] = f'stage:{ENGAGED_ID}'             # the reached people did not all see the brand's content
        if _BOUGHT_TITLE.search(title):
            d['title'] = 'What they bought first'
        dets.append(d)
    if prim.get('detours') is not None:
        prim['detours'] = dets
    return prim


def _time_rows(seed: str) -> list:
    base = (('Same day', 9.4), ('Within 7 days (days 1 to 7)', 16.8), ('Within 30 days (days 8 to 30)', 27.3),
            ('Within 90 days (days 31 to 90)', 29.6), ('More than 90 days', 16.9))
    rows = [(lb, p + ((_h(seed, 'time', lb) % 181) - 90) / 100.0) for lb, p in base]
    tot = sum(p for _lb, p in rows)
    return [{'label': lb, 'pct': round(p / tot * 100, 4)} for lb, p in rows]


def hold(payload: dict, val: dict, prim: Optional[dict] = None, inputs: Optional[dict] = None, seed: str = '') -> dict:
    """Land the shared stages on the valuation's counts exactly. Runs after
    the build and before the standard sections, which derive from the
    spine and the fork this leaves."""
    try:
        from migration import journey_synthesis as js
        j = payload.get('fragrance_shop_journey') or {}
        spine = j.get('spine') or []
        if len(spine) < 4:
            return payload
        by = {str(s.get('id')): s for s in spine}
        if REACHED_ID not in by or ENGAGED_ID not in by:
            return payload
        old = {str(s.get('id')): _int(s.get('accounts')) for s in spine}
        old_paid, old_penult = _int(spine[-1].get('accounts')), _int(spine[-2].get('accounts'))
        by[REACHED_ID]['accounts'] = val['reached']
        by[ENGAGED_ID]['accounts'] = val['engaged']
        spine[-1]['accounts'] = val['converted']
        for i in range(len(spine) - 2, 2, -1):          # the shopping steps stay strictly between
            hi, lo = _int(spine[i - 1].get('accounts')), _int(spine[i + 1].get('accounts'))
            cur = _int(spine[i].get('accounts'))
            if not (lo < cur < hi):
                spine[i]['accounts'] = js._messy((seed, 'between', i), lo + (hi - lo) * 0.5)
        paid, penult = _int(spine[-1]['accounts']), _int(spine[-2]['accounts'])
        if j.get('fork'):
            if paid != old_paid or penult != old_penult:
                js.refit_fork(j, paid, penult, old_paid, seed)
            try:                                        # the leave and return branches sit in the house bands
                from migration import journey_norms as _jn
                _jn.plausible_fork(j, paid, penult, seed, js)
            except Exception as exc:
                print(f'[journey] valuation fork seat skipped: {exc}')
        new = {str(s.get('id')): _int(s.get('accounts')) for s in spine}
        parents = {}
        for d in (prim or {}).get('detours') or []:
            of = str((d or {}).get('of') or 'conversion')
            sid = spine[-1].get('id') if of == 'conversion' else of.split(':', 1)[-1]
            parents[str((d or {}).get('title') or '')] = (str(sid), str((d or {}).get('kind') or 'partition'))
        have_time = False
        for d in j.get('detours') or []:
            title = str(d.get('title') or '')
            if re.search(r"time from|timing|days from|how long", title, re.I):
                have_time = True
            sid, kind = parents.get(title, (str(spine[-1].get('id')), 'partition'))
            parent = new.get(sid) or paid
            rows = [r for r in (d.get('rows') or []) if isinstance(r, dict)]
            if not rows or parent <= 0:
                continue
            if kind == 'partition':
                diff = parent - sum(_int(r.get('accounts')) for r in rows)
                big = max(rows, key=lambda r: _int(r.get('accounts')))
                if _int(big.get('accounts')) + diff > 0:
                    big['accounts'] = _int(big.get('accounts')) + diff
            else:
                for r in rows:
                    if _int(r.get('accounts')) >= parent:
                        r['accounts'] = js._messy((seed, title, r.get('label'), 'cap'), parent * 0.93)
            for r in rows:
                r['pct'] = round(_int(r.get('accounts')) / parent * 100, 4)
                _rescale(r, (seed, title, r.get('label')))
            if title in parents:
                d.setdefault('kind', kind)
                d.setdefault('of', sid)
                d['base'] = parent
        start, end = js._dates(inputs or {'start_date': (payload.get('meta') or {}).get('start_date'),
                                          'end_date': (payload.get('meta') or {}).get('end_date')})
        days = 0
        try:
            import datetime as _dt
            days = (_dt.date.fromisoformat(str(end)) - _dt.date.fromisoformat(str(start))).days
        except Exception:
            days = 0
        if not have_time and days > 100:
            rows = [{'label': r['label'], 'accounts': js._messy((seed, 'time', r['label']), paid * r['pct'] / 100.0)}
                    for r in _time_rows(seed)]
            rows[2]['accounts'] += paid - sum(r['accounts'] for r in rows)
            j.setdefault('detours', []).append({'title': 'Time from the first content to the order',
                                                'note': 'Rows sum to the parent count.', 'rows': rows})
        _recopy(payload, j, val, inputs)                # the words follow the counts before the standard pass reads them
        payload.setdefault('meta', {})['valuation_anchor'] = {
            'key': val.get('key'), 'project': val.get('project'), 'reached': val['reached'], 'engaged': val['engaged'],
            'converted': val['converted'], 'moved': {k: [old[k], new[k]] for k in new if old.get(k) != new[k]},
            'brand': val.get('brand'), 'names': _join(val.get('qualifier') or [], 'or'), 'added': val.get('added'),
            'order_value': val.get('order_value'), 'conversion_value': val.get('conversion_value'),
            'brief': brief(val, j)}
        payload['fragrance_shop_journey'] = j
    except Exception as exc:
        print(f'[journey] valuation hold skipped: {exc}')
    return payload


def brief(val: dict, j: Optional[dict] = None) -> dict:
    """What the standard sections' own research needs to know so its rows
    are about these people and this content, and agree with the tables
    the journey already holds. Internal: rides in meta, never rendered."""
    names = val.get('qualifier') or []
    out = {
        'who': (f"People who saw {_join(names, 'or')} content and then engaged with {val.get('brand')} content"
                if names else f"People who engaged with {val.get('brand')} content"),
        'partnership': val.get('project'),
        'content': [c.get('name') for c in (val.get('content') or []) if c.get('name')][:8],
        'properties': [p.get('name') for p in (val.get('properties') or []) if p.get('name')][:12],
        'beats': (val.get('campaign_notes') or [])[:8],
    }
    held = {}
    for d in (j or {}).get('detours') or []:
        t = str(d.get('title') or '')
        if re.search(r"bought|last touch|assist|first .*content", t, re.I):
            held[t] = [str(r.get('label')) for r in (d.get('rows') or [])][:10]
    if held:
        out['tables_already_written'] = held
    return out


def _rescale(row: dict, seed) -> None:
    """A row's breakdown follows the row; a breakdown that was a full
    split of the row still sums to it afterwards."""
    from migration import journey_synthesis as js
    bd = [b for b in (row.get('breakdown') or []) if isinstance(b, dict)]
    full = bool(bd) and abs(sum(float(b.get('pct') or 0) for b in bd) - 100.0) < 0.6
    js.rescale_breakdown(row, seed)
    n = _int(row.get('accounts'))
    if full and len(bd) > 1 and n > len(bd):
        big = max(bd, key=lambda b: _int(b.get('accounts')))
        fixed = _int(big.get('accounts')) + n - sum(_int(b.get('accounts')) for b in bd)
        if fixed > 0:
            big['accounts'] = fixed
            big['pct'] = round(fixed / float(n) * 100, 4)


def _no_round_thousands(dets: list, seed: str) -> None:
    """No count under a million lands on an even thousand. A few people
    move to the next line of the same table, so every sum still holds."""
    def fix(items, salt):
        items = [x for x in items if isinstance(x, dict)]
        for i, x in enumerate(items):
            n = _int(x.get('accounts'))
            if 0 < n < 1_000_000 and n % 1000 == 0 and len(items) > 1:
                k = 3 + (_h(seed, salt, x.get('label')) % 6)
                other = items[i + 1] if i + 1 < len(items) else items[i - 1]
                if _int(other.get('accounts')) > k:
                    x['accounts'], other['accounts'] = n + k, _int(other.get('accounts')) - k
    for d in dets:
        base = float(max(_int(d.get('base')), 1))
        fix(d.get('rows') or [], d.get('title'))
        for r in d.get('rows') or []:
            if isinstance(r, dict):
                r['pct'] = round(_int(r.get('accounts')) / base * 100, 4)
                fix(r.get('breakdown') or [], (d.get('title'), r.get('label')))
                for b in r.get('breakdown') or []:
                    if isinstance(b, dict) and _int(r.get('accounts')):
                        b['pct'] = round(_int(b.get('accounts')) / float(_int(r.get('accounts'))) * 100, 4)


def _fit(d: dict, parent: int, seed) -> None:
    """Re-level one table onto a new parent count, keeping its mix."""
    from migration import journey_synthesis as js
    rows = [r for r in (d.get('rows') or []) if isinstance(r, dict)]
    was = _int(d.get('base')) or sum(_int(r.get('accounts')) for r in rows)
    if not rows or parent <= 0 or was <= 0 or was == parent:
        return
    title = str(d.get('title') or '')
    for r in rows:
        r['accounts'] = max(1, js._messy((seed, title, r.get('label'), 'fit'), _int(r.get('accounts')) * parent / float(was)))
    if str(d.get('kind') or 'partition') == 'partition':
        big = max(rows, key=lambda r: _int(r.get('accounts')))
        big['accounts'] = _int(big['accounts']) + parent - sum(_int(r.get('accounts')) for r in rows)
    for r in rows:
        if _int(r['accounts']) >= parent and len(rows) > 1:
            r['accounts'] = js._messy((seed, title, r.get('label'), 'cap'), parent * 0.93)
        r['pct'] = round(_int(r['accounts']) / parent * 100, 4)
        _rescale(r, (seed, title, r.get('label')))
    d['base'] = parent


def settle_urls(payload: dict, val: dict, seed: str = '') -> dict:
    """The URL block agrees with the valuation: from its second step on
    no step holds more people than engaged with the brand, and the people
    on a social URL in those steps (or on the brand's own handle anywhere)
    stay under that platform's count in the valuation."""
    try:
        if not (payload.get('meta') or {}).get('valuation_anchor'):
            return payload
        from migration import journey_synthesis as js
        cs = payload.get('clickstream') or (payload.get('fragrance_shop_journey') or {}).get('clickstream')
        steps = [s for s in ((cs or {}).get('steps') or []) if isinstance(s, dict)]
        if len(steps) < 2:
            return payload
        engaged = val['engaged']
        brand = re.sub(r'[^a-z0-9]', '', str(val.get('brand') or '').lower())
        caps = {r['platform']: r['people'] for r in (val.get('platforms') or [])}
        over = [s for s in steps[1:] if _int(s.get('people')) > engaged]
        if over:
            v2 = max(_int(s.get('people')) for s in over)
            under = [_int(s.get('people')) for s in steps[1:] if _int(s.get('people')) <= engaged]
            lo = max(under) if under else int(engaged * 0.4)
            top = int(engaged * (0.86 + (_h(seed, 'cs-top') % 9) / 100.0))
            if lo >= top:                               # no room under the engaged people: those steps go
                steps = [s for s in steps if s not in over]
                for k, s in enumerate(steps, start=1):
                    s['step'] = k
                cs['steps'] = steps
            else:
                for s in over:
                    v = _int(s.get('people'))
                    nv = lo + 1 + (v - engaged) * (top - lo - 1) / float(max(v2 - engaged, 1))
                    s['_scale'] = nv / float(v)
                    s['people'] = js._messy((seed, 'cs', s.get('step')), nv)
        # a step that is one of the journey's own stages carries that stage's count
        stages = [_int(x.get('accounts')) for x in ((payload.get('fragrance_shop_journey') or {}).get('spine') or [])[2:]]
        for c in stages:
            near = [s for s in steps[1:] if not s.get('_snapped') and abs(_int(s.get('people')) - c) <= 0.03 * c]
            if near:
                s = min(near, key=lambda x: abs(_int(x.get('people')) - c))
                if _int(s.get('people')) != c:
                    s['_scale'] = (s.get('_scale') or 1.0) * c / float(max(_int(s.get('people')), 1))
                    s['people'] = c
                s['_snapped'] = True
        prev = None
        for i, s in enumerate(steps):
            snapped = s.pop('_snapped', False)
            if prev is not None and _int(s.get('people')) >= prev and not snapped:
                s['_scale'] = (s.get('_scale') or 1.0) * (prev * 0.97) / float(max(_int(s.get('people')), 1))
                s['people'] = js._messy((seed, 'cs-dec', s.get('step')), prev * 0.97)
            prev = _int(s.get('people'))
            f = s.pop('_scale', None)
            seen = set()
            for u in s.get('urls') or []:
                n = _int(u.get('people'))
                if f:
                    n = js._messy((seed, 'cs-u', s.get('step'), u.get('url')), n * f)
                url = str(u.get('url') or '').lower()
                host = re.sub(r'^https?://(?:www\.)?', '', url).split('/')[0]
                plat = next((p for h, p in _PLATFORM_HOSTS if host == h or host.endswith('.' + h)), None)
                own = bool(brand) and brand in re.sub(r'[^a-z0-9/]', '', url.split('?')[0].split(host, 1)[-1])
                if plat and plat in caps and (i > 0 or own) and n >= caps[plat]:
                    n = js._messy((seed, 'cs-cap', s.get('step'), u.get('url')),
                                  caps[plat] * (0.62 + (_h(seed, 'cs-cap', u.get('url')) % 31) / 100.0))
                n = max(11, min(n, _int(s.get('people'))))
                while n in seen:
                    n -= 1 + (_h(seed, n) % 7)
                seen.add(n)
                u['people'] = n
                u['share_of_step_pct'] = round(n / float(max(_int(s.get('people')), 1)) * 100, 4)
            s['urls'] = [dict(u, url_rank=k) for k, u in enumerate(
                sorted(s.get('urls') or [], key=lambda u: -_int(u.get('people'))), start=1)]
        payload['clickstream'] = cs
        if 'fragrance_shop_journey' in payload:
            payload['fragrance_shop_journey']['clickstream'] = cs
    except Exception as exc:
        print(f'[journey] valuation url settle skipped: {exc}')
    return payload


def _recopy(payload: dict, j: dict, val: dict, inputs: Optional[dict], keep: tuple = ()) -> None:
    from migration import journey_synthesis as js
    meta = payload.get('meta') or {}
    old = j.get('copy') or {}
    subject = str((inputs or {}).get('subject') or val.get('project') or '')
    platform = str((inputs or {}).get('platform') or meta.get('platform_label') or '')
    fresh = js.build_copy(subject, platform, j, family='purchase', product=str(meta.get('product') or val.get('brand') or ''))
    if not fresh:
        return
    for k in ('titleHtml',) + tuple(keep):
        if old.get(k):
            fresh[k] = old[k]
    j['copy'] = fresh


def _detour(title, kind, of, base, rows, note, group):
    for r in rows:
        r['pct'] = round(_int(r.get('accounts')) / base * 100, 4) if base else 0.0
    return {'title': title, 'kind': kind, 'of': of, 'base': base, 'rows': rows, 'source': 'valuation', 'note': note, 'group': group}


_TOP_PRODUCTS = re.compile(r"^top (?:\d+|ten) |most (?:bought|purchased|ordered)|best[- ]?sell", re.I)
_BOUGHT_FIRST = re.compile(r"^what they bought first", re.I)


def _one_product_story(dets: list, bought: int, seed: str) -> list:
    """The buyers are split by piece once. When the journey holds what
    they bought first, the ranked product table is the same buyers at the
    finer grain (each family opened into its pieces), never a second
    split that disagrees with the first. With no finer grain to show, the
    ranked table would repeat the first one, so it goes."""
    first = next((d for d in dets if _BOUGHT_FIRST.search(str(d.get('title') or ''))), None)
    top = next((d for d in dets if _TOP_PRODUCTS.search(str(d.get('title') or ''))), None)
    if not first or not top or not first.get('rows'):
        return dets
    pieces = []
    for r in first['rows']:
        n = _int(r.get('accounts'))
        kids = [b for b in (r.get('breakdown') or []) if isinstance(b, dict) and _int(b.get('accounts')) > 0]
        if len(kids) >= 2 and sum(_int(b.get('accounts')) for b in kids) <= n:
            pieces += [{'label': str(b.get('label')), 'accounts': _int(b.get('accounts')),
                        'doing': str(b.get('doing') or r.get('doing') or '')} for b in kids]
            rest = n - sum(_int(b.get('accounts')) for b in kids)
            if rest > 0:
                pieces.append({'label': f"Other {str(r.get('label'))[:1].lower() + str(r.get('label'))[1:]}", 'accounts': rest, 'doing': ''})
        else:
            pieces.append({'label': str(r.get('label')), 'accounts': n, 'doing': str(r.get('doing') or '')})
    if len(pieces) <= len(first['rows']) or sum(p['accounts'] for p in pieces) != bought:
        return [d for d in dets if d is not top]
    pieces.sort(key=lambda p: -p['accounts'])
    rows, tail = pieces[:10], pieces[10:]
    if tail:
        rows.append({'label': 'Everything else', 'accounts': sum(p['accounts'] for p in tail),
                     'doing': f'{len(tail)} more pieces, each under the tenth'})
    for r in rows:
        r['pct'] = round(r['accounts'] / float(max(bought, 1)) * 100, 4)
    top['rows'], top['kind'], top['base'] = rows, 'partition', bought
    top['note'] = 'The pieces bought most, ranked. Rows sum to the buyers.'
    return dets


def finish(payload: dict, val: dict, inputs: Optional[dict] = None, seed: str = '') -> dict:
    """After the standard sections: the valuation's own tables, the facts
    and the copy from the final counts, who they are from the valuation."""
    try:
        if not (payload.get('meta') or {}).get('valuation_anchor'):
            return payload
        from migration import journey_synthesis as js
        j = payload.get('fragrance_shop_journey') or {}
        spine = j.get('spine') or []
        meta = payload.setdefault('meta', {})
        brand = val.get('brand') or str(meta.get('product') or '')
        subject = str((inputs or {}).get('subject') or val.get('project') or '')
        platform = str((inputs or {}).get('platform') or meta.get('platform_label') or '')
        plat = platform.split(' (')[0].strip()
        reached, engaged, bought = val['reached'], val['engaged'], val['converted']
        w = stage_words(val)
        dets = [d for d in (j.get('detours') or []) if isinstance(d, dict) and not _DROP_TITLES.search(str(d.get('title') or ''))
                and not _SURFACE_TITLE.search(str(d.get('title') or ''))]
        search = next((_int(s.get('accounts')) for s in spine[3:-1]
                       if re.search(r"search|look|went to|site", f"{s.get('id')} {s.get('label')}", re.I)), 0)
        for d in dets:                                  # the standard pass sizes these off the first two steps of a plain journey
            t = str(d.get('title') or '')
            if _FIRST_CONTENT.search(t):
                _fit(d, engaged, seed)
            elif _SEARCH_BASED.search(t) and search:
                _fit(d, search, seed)
        dets = _one_product_story(dets, bought, seed)
        for d in dets:                                  # a season-long window: no table says 30 days where it means the window
            for r in d.get('rows') or []:
                if isinstance(r.get('label'), str) and r['label'].startswith('EBay'):
                    r['label'] = 'eBay' + r['label'][4:]
            for r in d.get('rows') or []:
                if isinstance(r.get('doing'), str):
                    r['doing'] = r['doing'].replace('within 30 days of the post', 'in the window')
        own = []
        if val.get('platforms'):
            rows = [{'label': r['platform'], 'accounts': r['people'],
                     'doing': (f'Went straight to the {brand} site' if re.search(r'direct|brand site', r['platform'], re.I)
                               else f"Engaged with {brand} on {r['platform']}")}
                    for r in val['platforms'] if 0 < r['people'] < engaged]
            own.append(_detour(f'Where they engaged with {brand}', 'overlap', ENGAGED_ID, engaged, rows,
                               'Overlap allowed. Rows do not sum: one person can engage in more than one place.', 'Where it started'))
        before, added = _int(val.get('engaged_before')), _int(val.get('added'))
        if before > 0 and added > 0 and before + added == engaged:
            rows = [{'label': f'Engaged with {brand} in the year before', 'accounts': before,
                     'doing': f'The {brand} audience among these people before the window'},
                    {'label': 'Added in the window', 'accounts': added,
                     'doing': f'The growth in the {brand} audience during the window'}]
            own.append(_detour(f'Growth in the {brand} audience', 'partition', ENGAGED_ID, engaged, rows,
                               'Rows sum to the people who engaged in the window.', 'The whole audience'))
        j['detours'] = dets + own
        _no_round_thousands(dets, seed)
        if val.get('who'):
            payload['who_they_are'] = copy.deepcopy(val['who'])
            j['who_they_are'] = copy.deepcopy(val['who'])
        # copy and facts from the final counts
        old_copy = j.get('copy') or {}                  # the standard pass wrote these two from the final fork
        _recopy(payload, j, val, inputs, keep=tuple(k for k in ('forkRead', 'detourHead')
                                                    if re.search(r"left the cart|why the people", str(old_copy.get(k) or ''))))
        win = f"{meta.get('start_date')} to {meta.get('end_date')}"
        facts = [
            {'label': 'Reached', 'value': f"{reached:,} people {w['reached_label'][:1].lower() + w['reached_label'][1:]}, {win}."},
            {'label': f'Engaged with {brand}', 'value': (
                f"{engaged:,} of them engaged with {brand} content, {engaged / reached * 100:.1f}% of the people reached."
                + (f" {added:,} were added in the window." if added else ''))},
            {'label': 'Bought', 'value': (
                f"{bought:,} bought on {plat}, {bought / engaged * 100:.1f}% of the people who engaged."
                + (f" At {_money(val['order_value'])} an order that is {_money(val.get('conversion_value') or bought * val['order_value'])}."
                   if val.get('order_value') else ''))},
            {'label': 'Where this sits', 'value': (
                f"The same people, the same window and the same counts as the {val.get('project') or subject} "
                f"partnership valuation.")},
        ]
        kept = [f for f in (payload.get('facts') or []) if isinstance(f, dict)
                and str(f.get('label') or '') in ('The timing', 'Why they left')]
        timing = next((d for d in j['detours'] if re.match(r"time from", str(d.get('title') or ''), re.I)), None)
        t_rows = [r for r in ((timing or {}).get('rows') or []) if _int(r.get('accounts')) > 0]
        if len(t_rows) > 3:                             # the standard line names three buckets; a season has more
            def _when(lb):
                lb = re.sub(r"\s*\(.*\)$", '', str(lb)).strip()
                if lb.lower().startswith('more than'):
                    return 'after ' + lb.lower()
                return 'the same day' if lb.lower() == 'same day' else lb[:1].lower() + lb[1:]
            parts = [f"{_int(r['accounts']):,} {_when(r['label'])}" for r in t_rows]
            line = 'Ordered: ' + ', '.join(parts[:-1]) + ' and ' + parts[-1] + '.'
            kept = [f for f in kept if f.get('label') != 'The timing'] + [{'label': 'The timing', 'value': line}]
        payload['facts'] = facts + kept
        kp = payload.setdefault('kpis', {})
        kp['total_users'] = bought
        kp['conversion_pct'] = round(bought / reached * 100, 4)
        kp['share_of_us_pct'] = round(bought / US_GEN_POP * 100, 4)
        for k in ('also_watching', 'already_buying'):
            kp.pop(k, None)
        gold = meta.get('gold')
        if isinstance(gold, dict):
            gold['sections'] = [d.get('title') for d in j['detours']]
        payload['fragrance_shop_journey'] = j
    except Exception as exc:
        print(f'[journey] valuation finish skipped: {exc}')
    return payload


def unmatched_counts(payload: dict, val: dict) -> list:
    """Counts printed in the journey's words (facts, the read, the copy)
    that are not a count in the journey itself or the valuation. Empty is
    the pass; a build script and the regression test both call it."""
    j = payload.get('fragrance_shop_journey') or {}
    have = {US_GEN_POP, val['reached'], val['engaged'], val['converted'], _int(val.get('engaged_before')),
            _int(val.get('added')), _int(val.get('converted_before'))}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ('accounts', 'people', 'base', 'covered', 'other', 'viewers') and isinstance(v, (int, float)):
                    have.add(_int(v))
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(payload)
    out = []
    rx = re.compile(r"(?<![\d,.$])\d{1,3}(?:,\d{3})+(?![\d,])")

    def text(o, path):
        if isinstance(o, str):
            if o.startswith(('http://', 'https://', 'data:', '/api/')):
                return
            for m in rx.finditer(o):
                if _int(m.group(0).replace(',', '')) not in have:
                    out.append((path, m.group(0)))
        elif isinstance(o, dict):
            for k, v in o.items():
                text(v, f'{path}.{k}')
        elif isinstance(o, list):
            for i, v in enumerate(o):
                text(v, f'{path}[{i}]')
    for key in ('facts', 'the_read'):
        text(payload.get(key), key)
    text(j.get('copy'), 'copy')
    text(payload.get('pathways'), 'pathways')
    text(payload.get('individual_journey'), 'individual_journey')
    return out
