"""Spend Engine: the twins' spend loop (2026-10-26, Jenna: "the spend
output has to be part of it").

A plan (channels with spend, assets, a window, a goal) or the Launch
Lab plan that was approved runs forward day by day on the twin: daily
spend and the signal by channel (reach, first actions, cost per first
action from the twin's response curves plus Trends IQ momentum where the
subject has snapshots), recommended moves each day (move $X from A to B,
raise or cut, swap the lead asset, with the earned delta in plain
words), A/B tests spun from the trends, a lift read against a matched
holdout sized at the lowest spend that proves lift, and a running
ledger of what the moves saved against the plan as allocated.

Two modes: ``chip`` (the user approves each move) or ``rule`` (moves
inside a guardrail are approved on their own: a max daily shift and a
floor per channel). Execution is a handoff: approved moves go to the
exportable moves list for the user's buyers and the Attribution IQ
tracking intake measures them. Nothing here talks to an ad platform.

Schema (``labs.spend[]`` on the twin):
  {plan_id, source: 'launch'|'manual', channels:[{channel, spend}],
   window:{days, start}, goal, assets, platform,
   days:[{day, by_channel:[{channel, spend, reach, first_actions, cpa}],
          moves:[{id, from, to, amount, why, delta, approved, kind}], approved}],
   tests:[{id, name, what, split, days, why, status}],
   holdout:{size, spend, lift_read, expected_lift_pct},
   ledger:{saved_total, moves_approved, moves_proposed, as_allocated_outcome,
           signal_led_outcome, as_allocated_cpa, signal_led_cpa},
   mode, rule:{max_daily_shift_pct, floor_per_channel}, created_by, created_at, updated_at}
"""
from __future__ import annotations

import csv
import io
import time
import uuid
from typing import Optional

from migration.digital_twin_engine import (
    VERSION, canon_channel, channel_signal, clamp, fmt_money, human_money, human_people, messy_count, messy_money, pct1,
    size_holdout, trait, unit,
)

DEFAULT_RULE = {'max_daily_shift_pct': 15.0, 'floor_per_channel': 0.0}
MAX_DAYS = 60


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _norm_channels(channels) -> list:
    out = {}
    for c in channels or []:
        if isinstance(c, dict):
            name = canon_channel(c.get('channel') or c.get('name'))
            try:
                sp = float(str(c.get('spend') or 0).replace('$', '').replace(',', ''))
            except (TypeError, ValueError):
                sp = 0.0
        else:
            continue
        if sp > 0:
            out[name] = out.get(name, 0.0) + sp
    return [{'channel': k, 'spend': round(v, 2)} for k, v in out.items()]


def _momentum(channel: str, trends: Optional[dict], seed: str) -> float:
    """Trends IQ momentum for a channel: a multiplier on the twin's
    response (0.8 to 1.3). Snapshots name the surface they moved on;
    social channels take the social momentum, search takes search."""
    if not isinstance(trends, dict) or not trends:
        return 1.0
    social = ('Meta', 'TikTok', 'YouTube')
    key = 'social' if channel in social else 'search' if channel == 'Google' else 'commerce' if channel == 'Amazon' else 'video' if channel == 'CTV' else 'general'
    try:
        m = float(trends.get(channel) or trends.get(key) or trends.get('general') or 1.0)
    except (TypeError, ValueError):
        m = 1.0
    return clamp(m, 0.8, 1.3)


def _day_eff(day: int, days: int, channel: str, seed: str) -> float:
    """Response drifts over a flight: the first days are the freshest,
    creative fatigue sets in, and a small daily wobble keeps the numbers
    honest. Deterministic."""
    fatigue = 1.0 - 0.28 * (day / max(1, days - 1)) ** 1.3
    wobble = 1 + (unit('day', day, channel, seed) - 0.5) * 0.12
    return clamp(fatigue * wobble, 0.5, 1.15)


def tests_from_trends(twin: dict, doc: dict, trends: Optional[dict], seed: str) -> list:
    """A/B tests to run inside the flight: what to test, how to split,
    how many days. Spun from the trends when the subject has them, else
    from the twin's dials."""
    out = []
    chans = [c['channel'] for c in doc.get('channels') or []]
    names = []
    if isinstance(trends, dict):
        names = [str(t) for t in (trends.get('names') or []) if str(t).strip()][:3]
    lead_social = next((c for c in ('Meta', 'TikTok', 'YouTube') if c in chans), None)
    if lead_social:
        out.append({'name': 'Cast-forward vs premise trailer', 'what': f"Two lead assets on {lead_social}: the cast in the first frame against the premise in the first frame.",
                    'split': f"50/50 on {lead_social}", 'days': 4, 'channel': lead_social,
                    'why': ('This group follows the people behind a title, so the cast cut should win; the first days settle it.' if trait(twin, 'creator_follow') >= 55
                            else 'This group buys the premise more than the names; the first days tell you which cut to scale.')})
    if 'Google' in chans:
        out.append({'name': 'Title search copy vs franchise copy', 'what': 'Two headline sets on Google: the title alone against the franchise name with the title.',
                    'split': '50/50 on Google', 'days': 5, 'channel': 'Google', 'why': 'Search intent here splits between people who know the franchise and people who met the title in the feed.'})
    if 'TikTok' in chans and names:
        out.append({'name': f"Hook on '{names[0]}'", 'what': f"A TikTok cut that opens on {names[0]}, a trend moving right now, against the standard cut.",
                    'split': '60/40 on TikTok, trend cut heavier', 'days': 3, 'channel': 'TikTok', 'why': 'Momentum is live; a three-day read is enough to catch it before it turns.'})
    elif 'TikTok' in chans:
        out.append({'name': 'Sound-led vs dialogue-led cut', 'what': 'A TikTok cut led by the score against one led by a dialogue line.',
                    'split': '50/50 on TikTok', 'days': 3, 'channel': 'TikTok', 'why': 'Short-form reach for this group moves with the hook, not the plot.'})
    if 'CTV' in chans:
        out.append({'name': '15s vs 30s on CTV', 'what': 'The 15 second spot against the 30 on connected TV.', 'split': '50/50 on CTV', 'days': 6, 'channel': 'CTV',
                    'why': ('They sit through ads; the 30 may earn its cost.' if trait(twin, 'ad_tolerance') >= 55 else 'Ad tolerance is modest; the 15 should hold reach at a lower cost.')})
    if not out:
        out.append({'name': 'Lead asset A vs B', 'what': f"The two strongest assets head to head on {chans[0] if chans else 'the lead channel'}.", 'split': '50/50', 'days': 4,
                    'channel': chans[0] if chans else 'Other', 'why': 'The first days decide which asset carries the rest of the flight.'})
    for i, t in enumerate(out):
        t['id'] = f"t{i + 1}"
        t['status'] = 'proposed'
        t['days'] = int(clamp(int(t['days'] * (0.85 + 0.3 * unit('tdays', t['name'], seed))), 2, 10))
    return out[:4]


def _apply_moves(daily: dict, moves: list, floor: float) -> dict:
    out = dict(daily)
    for m in moves:
        a, b, amt = m.get('from'), m.get('to'), float(m.get('amount') or 0)
        if a in out and b in out and amt > 0:
            amt = min(amt, max(0.0, out[a] - floor))
            out[a] = round(out[a] - amt, 2)
            out[b] = round(out[b] + amt, 2)
    return out


def _propose_moves(twin: dict, day: int, days: int, daily: dict, cum: dict, moms: dict, rule: dict, assets: list, seed: str) -> list:
    """The moves this day earns: shift money from the weakest marginal
    channel to the strongest; swap the lead asset when fatigue bites."""
    moves = []
    margins = {}
    for c, sp in daily.items():
        eff = _day_eff(day, days, c, seed)
        sig = channel_signal(twin, c, cum[c] + sp, moms[c] * eff)
        margins[c] = (sig['marginal_actions_per_dollar'], sig)
    if len(margins) >= 2:
        best = max(margins, key=lambda c: margins[c][0])
        worst = min(margins, key=lambda c: margins[c][0])
        mb, mw = margins[best][0], margins[worst][0]
        floor = float(rule.get('floor_per_channel') or 0)
        if best != worst and mw > 0 and mb / mw >= 1.22 and daily[worst] - floor > 1:
            max_shift = daily[worst] * float(rule.get('max_daily_shift_pct') or 15) / 100.0
            amount = clamp(daily[worst] * 0.25, 0, max(0.0, daily[worst] - floor))
            amount = round(min(amount, max(max_shift, 1.0)) if rule.get('mode') == 'rule' else amount, 2)
            if amount >= 1:
                delta = (mb - mw) * amount
                cpa_b = margins[best][1]['cpa'] or 0
                cpa_w = margins[worst][1]['cpa'] or 0
                moves.append({'id': f"d{day + 1}m1", 'kind': 'shift', 'from': worst, 'to': best, 'amount': amount,
                              'delta': messy_count(seed, f"mv|{day}|delta", max(1, delta)),
                              'why': (f"{best} is buying a first action at {fmt_money(cpa_b)} against {fmt_money(cpa_w)} on {worst}. "
                                      f"Moving {human_money(amount)} a day earns about {messy_count(seed, f'mv|{day}|delta', max(1, delta)):,} more first actions over the day."),
                              'within_rule': amount <= max_shift + 0.01 and (daily[worst] - amount) >= floor, 'approved': False})
    # creative fatigue: swap the lead asset when the day's efficiency has slipped
    if assets and len(assets) >= 2 and day >= 3 and (day - 3) % 4 == 0:
        i = ((day - 3) // 4) % len(assets)
        nxt = assets[(i + 1) % len(assets)]
        cur = assets[i % len(assets)]
        moves.append({'id': f"d{day + 1}m{len(moves) + 1}", 'kind': 'asset', 'from': str(cur)[:80], 'to': str(nxt)[:80], 'amount': 0.0,
                      'delta': messy_count(seed, f"asset|{day}", sum(m[1]['first_actions'] for m in margins.values()) * 0.05 + 3),
                      'why': f"Response to '{str(cur)[:40]}' has slipped about {pct1((1 - _day_eff(day, days, 'Meta', seed)) * 100)}% since launch; leading with '{str(nxt)[:40]}' recovers part of it.",
                      'within_rule': True, 'approved': False})
    # raise or cut: when the best channel still has room and the plan is young
    if margins and day in (1, 6) and len(margins) >= 1:
        best = max(margins, key=lambda c: margins[c][0])
        sig = margins[best][1]
        if sig['marginal_actions_per_dollar'] > 0 and sig['cpa'] and sig['cpa'] < 1.0e6:
            amt = round(daily[best] * 0.1, 2)
            moves.append({'id': f"d{day + 1}m{len(moves) + 1}", 'kind': 'raise', 'from': 'reserve', 'to': best, 'amount': amt,
                          'delta': messy_count(seed, f"raise|{day}", max(1, sig['marginal_actions_per_dollar'] * amt)),
                          'why': f"{best} still has room at {fmt_money(sig['cpa'])} a first action; another {human_money(amt)} a day from reserve earns about {messy_count(seed, f'raise|{day}', max(1, sig['marginal_actions_per_dollar'] * amt)):,} more a day.",
                          'within_rule': False, 'approved': False})
    return moves


def recompute(twin: dict, doc: dict, trends: Optional[dict] = None) -> dict:
    """Run (or re-run) the day loop from day one, applying every
    approved move from the day it was approved for. Idempotent."""
    seed = f"{(twin.get('meta') or {}).get('subject')}|spend|{doc.get('plan_id')}|{VERSION}"
    channels = _norm_channels(doc.get('channels'))
    doc['channels'] = channels
    days = int(clamp(int((doc.get('window') or {}).get('days') or 14), 1, MAX_DAYS))
    doc.setdefault('window', {})['days'] = days
    rule = dict(DEFAULT_RULE, **(doc.get('rule') or {}))
    rule['mode'] = doc.get('mode') or 'chip'
    doc['rule'] = {k: rule[k] for k in ('max_daily_shift_pct', 'floor_per_channel')}
    mode = str(doc.get('mode') or 'chip')
    assets = [str(a) for a in (doc.get('assets') or []) if str(a).strip()]
    moms = {c['channel']: _momentum(c['channel'], trends, seed) for c in channels}
    daily_alloc = {c['channel']: round(c['spend'] / days, 2) for c in channels}
    daily_now = dict(daily_alloc)
    prior = {d['day']: {m['id']: m for m in d.get('moves') or []} for d in (doc.get('days') or []) if isinstance(d, dict)}
    cum = {c: 0.0 for c in daily_alloc}
    cum_alloc = {c: 0.0 for c in daily_alloc}
    prev_actions = {c: 0 for c in daily_alloc}
    prev_actions_alloc = {c: 0 for c in daily_alloc}
    prev_reach = {c: 0 for c in daily_alloc}
    out_days = []
    tot_actions, tot_actions_alloc, tot_spend = 0, 0, 0.0
    for d in range(days):
        by = []
        for c in daily_alloc:
            eff = _day_eff(d, days, c, seed)
            m = moms[c] * eff
            sp = daily_now[c]
            sig_today = channel_signal(twin, c, cum[c] + sp, m)
            sig_yday = channel_signal(twin, c, cum[c], m)
            actions = max(0, sig_today['first_actions'] - sig_yday['first_actions'])
            reach = max(0, sig_today['reach'] - sig_yday['reach'])
            cum[c] += sp
            tot_spend += sp
            a_today = channel_signal(twin, c, cum_alloc[c] + daily_alloc[c], m)
            a_yday = channel_signal(twin, c, cum_alloc[c], m)
            cum_alloc[c] += daily_alloc[c]
            actions_alloc = max(0, a_today['first_actions'] - a_yday['first_actions'])
            tot_actions += actions
            tot_actions_alloc += actions_alloc
            by.append({'channel': c, 'spend': round(sp, 2), 'reach': reach, 'first_actions': actions,
                       'cpa': round(sp / actions, 2) if actions else 0.0, 'momentum': round(moms[c], 3)})
        moves = _propose_moves(twin, d, days, daily_now, cum, moms, dict(rule), assets, seed)
        # carry approvals from the stored day forward, or auto-approve inside the rule
        for mv in moves:
            old = (prior.get(d + 1) or {}).get(mv['id'])
            if old and old.get('approved'):
                mv['approved'] = True
                mv['approved_at'] = old.get('approved_at') or _now()
                mv['approved_by'] = old.get('approved_by') or ''
            elif mode == 'rule' and mv.get('within_rule') and mv.get('kind') == 'shift':
                mv['approved'] = True
                mv['approved_at'] = _now()
                mv['approved_by'] = 'rule'
        approved = [mv for mv in moves if mv.get('approved') and mv.get('kind') == 'shift']
        if approved:
            daily_now = _apply_moves(daily_now, approved, float(rule.get('floor_per_channel') or 0))
        out_days.append({'day': d + 1, 'by_channel': by, 'spend': round(sum(b['spend'] for b in by), 2),
                         'first_actions': sum(b['first_actions'] for b in by), 'reach': sum(b['reach'] for b in by),
                         'moves': moves, 'approved': all(mv.get('approved') for mv in moves) if moves else True})
    doc['days'] = out_days
    doc['tests'] = doc.get('tests') or tests_from_trends(twin, doc, trends, seed)
    outcome = {'reach': sum(d['reach'] for d in out_days), 'first_actions': max(1, tot_actions), 'spend': tot_spend}
    doc['holdout'] = size_holdout(twin, outcome, seed)
    n_app = sum(1 for d in out_days for mv in d['moves'] if mv.get('approved'))
    n_prop = sum(len(d['moves']) for d in out_days)
    cpa_alloc = tot_spend / tot_actions_alloc if tot_actions_alloc else 0.0
    cpa_sig = tot_spend / tot_actions if tot_actions else 0.0
    gained = max(0, tot_actions - tot_actions_alloc)
    doc['ledger'] = {
        'saved_total': messy_money(seed, 'ledger|saved', gained * cpa_alloc) if gained else 0.0,
        'moves_approved': n_app, 'moves_proposed': n_prop,
        'as_allocated_outcome': messy_count(seed, 'ledger|alloc', tot_actions_alloc) if tot_actions_alloc else 0,
        'signal_led_outcome': messy_count(seed, 'ledger|signal', tot_actions) if tot_actions else 0,
        'as_allocated_cpa': round(cpa_alloc, 2), 'signal_led_cpa': round(cpa_sig, 2),
        'spend': round(tot_spend, 2), 'reach': messy_count(seed, 'ledger|reach', outcome['reach']) if outcome['reach'] else 0,
        'read': '',
    }
    led = doc['ledger']
    if gained:
        led['read'] = (f"The approved moves earned {human_people(gained)} more first actions than the plan as allocated, worth {human_money(led['saved_total'])} "
                       f"at the allocated cost per first action ({fmt_money(cpa_alloc)}); the flight is running at {fmt_money(cpa_sig)}.")
    else:
        led['read'] = (f"No approved moves yet. As allocated, the plan buys {human_people(led['as_allocated_outcome'])} first actions at {fmt_money(cpa_alloc)} each"
                       + (f"; {n_prop} move{'s' if n_prop != 1 else ''} wait for approval." if n_prop else '.'))
    doc['updated_at'] = _now()
    return doc


def activate_plan(twin: dict, plan: dict, *, mode: str = 'chip', rule: Optional[dict] = None, source: str = 'manual',
                  trends: Optional[dict] = None, created_by: str = '', plan_id: str = '') -> dict:
    """Build a Spend Engine document from a plan and run it forward."""
    plan = plan or {}
    channels = _norm_channels(plan.get('channels'))
    if not channels:
        raise ValueError('a plan needs at least one channel with spend')
    win = plan.get('window') if isinstance(plan.get('window'), dict) else {}
    days = win.get('days') or plan.get('window_days') or plan.get('days') or (int(plan.get('window_weeks') or 0) * 7 if plan.get('window_weeks') else 0) or 14
    goal = plan.get('goal') if isinstance(plan.get('goal'), dict) else ({'metric': 'first actions', 'value': int(plan.get('goal'))} if str(plan.get('goal') or '').replace(',', '').isdigit() else {})
    doc = {
        'plan_id': plan_id or uuid.uuid4().hex[:10], 'source': source if source in ('launch', 'manual') else 'manual',
        'channels': channels, 'window': {'days': int(clamp(int(days), 1, MAX_DAYS)), 'start': str(win.get('start') or plan.get('start') or '')},
        'goal': goal, 'assets': [str(a) for a in (plan.get('assets') or []) if str(a).strip()][:8],
        'platform': str(plan.get('platform') or (twin.get('meta') or {}).get('platform_home') or ''),
        'mode': 'rule' if str(mode) == 'rule' else 'chip', 'rule': dict(DEFAULT_RULE, **{k: float(v) for k, v in (rule or {}).items() if k in DEFAULT_RULE and v not in (None, '')}),
        'days': [], 'tests': [], 'holdout': {}, 'ledger': {}, 'created_by': created_by or 'prometheus', 'created_at': _now(), 'updated_at': _now(),
    }
    mix_txt = ', '.join(c['channel'] + ' ' + human_money(c['spend']) for c in channels)
    doc['label'] = f"{'Launch Lab plan' if source == 'launch' else 'Plan'}: {mix_txt}"
    return recompute(twin, doc, trends)


def approve_move(twin: dict, doc: dict, day: int, move_id: str, by: str = '', trends: Optional[dict] = None) -> dict:
    """Approve one move (writes it to the ledger and re-runs the days
    after it). Unknown day or move: the document is returned unchanged."""
    for d in doc.get('days') or []:
        if int(d.get('day') or 0) != int(day):
            continue
        for mv in d.get('moves') or []:
            if mv.get('id') == move_id:
                mv['approved'] = True
                mv['approved_at'] = _now()
                mv['approved_by'] = by or 'user'
                return recompute(twin, doc, trends)
    return doc


def set_mode(twin: dict, doc: dict, mode: str, rule: Optional[dict] = None, trends: Optional[dict] = None) -> dict:
    doc['mode'] = 'rule' if str(mode) == 'rule' else 'chip'
    if rule:
        cur = dict(doc.get('rule') or DEFAULT_RULE)
        for k, v in rule.items():
            if k in DEFAULT_RULE and v not in (None, ''):
                try:
                    cur[k] = float(v)
                except (TypeError, ValueError):
                    pass
        doc['rule'] = cur
    return recompute(twin, doc, trends)


def moves_csv(doc: dict) -> str:
    """The exportable moves list for the user's buyers."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Plan', 'Day', 'Kind', 'From', 'To', 'Amount per day (USD)', 'Why', 'Earned (first actions)', 'Approved', 'Approved at', 'Approved by'])
    for d in doc.get('days') or []:
        for mv in d.get('moves') or []:
            w.writerow([doc.get('plan_id'), d.get('day'), mv.get('kind'), mv.get('from'), mv.get('to'), f"{float(mv.get('amount') or 0):.2f}",
                        mv.get('why'), mv.get('delta'), 'yes' if mv.get('approved') else 'no', mv.get('approved_at') or '', mv.get('approved_by') or ''])
    return buf.getvalue()


def spend_lines(doc: dict) -> list:
    """Plain lines for chat and the thread note."""
    led = doc.get('ledger') or {}
    days = doc.get('days') or []
    out = [f"{doc.get('label') or 'Plan'} over {len(days)} days, {doc.get('mode')} mode"
           + (f" (max {doc['rule'].get('max_daily_shift_pct'):.0f}% a day, floor {fmt_money(doc['rule'].get('floor_per_channel'))} per channel)." if doc.get('mode') == 'rule' else '.')]
    if led:
        out.append(led.get('read') or '')
    pend = [(d['day'], mv) for d in days for mv in d.get('moves') or [] if not mv.get('approved')][:3]
    for day, mv in pend:
        out.append(f"Day {day}: {mv['why']}")
    h = doc.get('holdout') or {}
    if h.get('lift_read'):
        out.append(h['lift_read'])
    return [o for o in out if o]


def handoff_package(twin: dict, doc: dict) -> dict:
    """What the Attribution IQ tracking intake is prefilled with: the
    campaign name, the channels and the goal, as text."""
    name = f"{(twin.get('meta') or {}).get('name') or (twin.get('meta') or {}).get('subject')} launch"
    chans = ', '.join(f"{c['channel']} {human_money(c['spend'])}" for c in doc.get('channels') or [])
    goal = doc.get('goal') or {}
    text = (f"Campaign: {name}. Channels: {chans}. Window: {doc.get('window', {}).get('days')} days. "
            f"Conversion: {goal.get('metric') or 'first action'}" + (f" ({goal.get('value'):,})" if goal.get('value') else '') + '. Daily tracking on.')
    return {'campaign_name': name, 'text': text, 'channels': doc.get('channels'), 'goal': goal}


__all__ = ['activate_plan', 'recompute', 'approve_move', 'set_mode', 'moves_csv', 'spend_lines', 'handoff_package', 'tests_from_trends', 'DEFAULT_RULE', 'MAX_DAYS']
