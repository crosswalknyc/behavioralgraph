"""Digital Twins IQ pricing: the per-group rate card (Jenna 2026-11-05).

Jenna, verbatim: "update digital twins pricing to be this. regardless of
if it is pilot testing or focus group these should be the rates". The
card (the Twin IQ deck, page 14: "Pay per group or $5K deposit for the
package rate. Price per group. Each group answers 1-40 questions.
Includes talent, asset, video, audio, copy, printed material testing."):

  INDIVIDUAL GROUPS   $750 a group for 1-20 questions, $1,000 for 1-40.
                      Pay for each group as you open it. A card on file
                      with a $2,000 balance.
  PACKAGE RATES       $500 a group for 1-20 questions, $750 for 1-40.
                      A $5,000 deposit on the company wallet unlocks the
                      rate on every group while it is drawn down.
  ENTERPRISE          $200 a group, 1-40 questions. $20K a month on a
                      12-month term, paid in full.

A GROUP is one focus group room or one pilot test, in any mode (pilot,
episode, feature, binge, rough cut, trailer, title, key art, script,
concept, critics room). Same rates for both. A duplicate is a new group;
a recut (cut B) is a new group; popping a room out is not. The twin
build carries no separate charge (the group is the unit); each Launch,
content, pricing, platform or compare run and the Spend Engine keep
their own lines (they are not groups). Prometheus usage is metered on
top as before.

The PLAN is a company attribute: ``digital_twins_plan`` on the
shared-wallet company record in users.json (the same company the
Digital Twins company scope reads, ``migration.digital_twin_company``),
with ``digital_twins_plan_meta`` for the deposit or the enterprise term.
``individual`` is the default. ``package`` activates when a $5,000
deposit lands on the company wallet (``wallet.apply_wallet_topup``
calls ``note_deposit``) or when an admin marks it, and stays active
while the deposit is being drawn down (the wallet still holds money);
``enterprise`` is set by an admin with its monthly and term recorded and
stays active through the term. A seat with no company carries the plan
on its own record. Crosswalk staff seats are never charged.

Charges post at open through the host's ``consume_credit`` (the wallet
path every product uses), with the room id as the run id so
``refund_credit`` restores the charge when the open fails or a pilot is
deleted before it ever started. The tier (1-20 or 1-40) is chosen at
open and the room enforces the cap; a 1-20 group at twenty questions is
offered the upgrade to 1-40 for the difference at the plan's rate, and
the twenty-first question waits until it is accepted.

The dollar amounts live in one place (``RATES``); ``wallet.py`` carries
the same numbers as the published ``per_tool_usd`` defaults so the
billing panel can override a line per company and the card, the quote
and the charge never disagree (``scripts/test_digital_twin_pricing.py``
pins the two tables together).
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

PLANS = ('individual', 'package', 'enterprise')
DEFAULT_PLAN = 'individual'
TIER_SMALL, TIER_LARGE = 20, 40
TIERS = (TIER_SMALL, TIER_LARGE)

RATES = {
    ('individual', TIER_SMALL): 750.0, ('individual', TIER_LARGE): 1000.0,
    ('package', TIER_SMALL): 500.0, ('package', TIER_LARGE): 750.0,
    ('enterprise', TIER_LARGE): 200.0,
}
# a 1-20 group that reaches twenty questions upgrades to 1-40 for the difference
UPGRADE_RATES = {'individual': 250.0, 'package': 250.0}
PACKAGE_DEPOSIT_USD = 5000.0
INDIVIDUAL_BALANCE_FLOOR_USD = 2000.0     # the card's recommended balance; the hard gate is the group's own price
ENTERPRISE_MONTHLY_USD = 20000.0
ENTERPRISE_TERM_MONTHS = 12

TOOL_KEYS = {
    ('individual', TIER_SMALL): 'digital_twin_group_individual_20', ('individual', TIER_LARGE): 'digital_twin_group_individual_40',
    ('package', TIER_SMALL): 'digital_twin_group_package_20', ('package', TIER_LARGE): 'digital_twin_group_package_40',
    ('enterprise', TIER_LARGE): 'digital_twin_group_enterprise',
}
UPGRADE_TOOL_KEYS = {'individual': 'digital_twin_group_upgrade_individual', 'package': 'digital_twin_group_upgrade_package'}
PULL_TYPES = {
    'digital_twin_group_individual_20': 'Digital Twins IQ Group (Individual, 1-20 questions)',
    'digital_twin_group_individual_40': 'Digital Twins IQ Group (Individual, 1-40 questions)',
    'digital_twin_group_package_20': 'Digital Twins IQ Group (Package, 1-20 questions)',
    'digital_twin_group_package_40': 'Digital Twins IQ Group (Package, 1-40 questions)',
    'digital_twin_group_enterprise': 'Digital Twins IQ Group (Enterprise, 1-40 questions)',
    'digital_twin_group_upgrade_individual': 'Digital Twins IQ Group Upgrade (Individual, to 1-40 questions)',
    'digital_twin_group_upgrade_package': 'Digital Twins IQ Group Upgrade (Package, to 1-40 questions)',
}
# the internal credit unit a group takes on a credits seat (about $50 a credit, as the twin build read)
CREDITS = {
    'digital_twin_group_individual_20': 15, 'digital_twin_group_individual_40': 20,
    'digital_twin_group_package_20': 10, 'digital_twin_group_package_40': 15,
    'digital_twin_group_enterprise': 4,
    'digital_twin_group_upgrade_individual': 5, 'digital_twin_group_upgrade_package': 5,
}
PLAN_LABELS = {'individual': 'Individual groups', 'package': 'Package rate', 'enterprise': 'Enterprise agreement'}
PLAN_FIELD = 'digital_twins_plan'
PLAN_META_FIELD = 'digital_twins_plan_meta'
ADD_FUNDS_URL = '/wallet'

# retired on 2026-11-05: the session, per-mode and per-panelist lines the card replaced
RETIRED_KEYS = ('digital_twin_panel', 'digital_twin_panel_more', 'digital_twin_panelist', 'digital_twin_pilot', 'digital_twin_pilot_more',
                'digital_twin_trailer', 'digital_twin_title', 'digital_twin_key_art', 'digital_twin_concept', 'digital_twin_script', 'digital_twin_recut')


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ------------------------------------------------------------- the card
def normalize_plan(value) -> str:
    p = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {'ind': 'individual', 'individual_groups': 'individual', 'pay_per_group': 'individual', 'per_group': 'individual',
               'pkg': 'package', 'package_rate': 'package', 'package_rates': 'package', 'deposit': 'package',
               'ent': 'enterprise', 'enterprise_agreement': 'enterprise'}
    p = aliases.get(p, p)
    return p if p in PLANS else DEFAULT_PLAN


def tiers_for(plan) -> tuple:
    return (TIER_LARGE,) if normalize_plan(plan) == 'enterprise' else TIERS


def default_tier(plan) -> int:
    return TIER_LARGE if normalize_plan(plan) == 'enterprise' else TIER_SMALL


def normalize_tier(value, plan=DEFAULT_PLAN) -> int:
    """20 or 40 (enterprise is always 40). Accepts 20, '20', '1-20',
    '1-40', 'small', 'large'; anything else is the plan's default."""
    p = normalize_plan(plan)
    if p == 'enterprise':
        return TIER_LARGE
    s = str(value if value is not None else '').strip().lower()
    if s in ('40', '1-40', '1 - 40', 'large', 'big', 'forty'):
        return TIER_LARGE
    if s in ('20', '1-20', '1 - 20', 'small', 'twenty'):
        return TIER_SMALL
    try:
        n = int(float(s))
        if n > TIER_SMALL:
            return TIER_LARGE
        if n > 0:
            return TIER_SMALL
    except (TypeError, ValueError):
        pass
    return default_tier(p)


def tier_label(tier) -> str:
    return f"1-{int(tier)} questions"


def plan_label(plan) -> str:
    return PLAN_LABELS[normalize_plan(plan)]


def tool_key(plan, tier) -> str:
    p = normalize_plan(plan)
    return TOOL_KEYS[(p, normalize_tier(tier, p))]


def upgrade_tool_key(plan) -> str:
    return UPGRADE_TOOL_KEYS.get(normalize_plan(plan), '')


def pull_type(plan, tier) -> str:
    return PULL_TYPES[tool_key(plan, tier)]


def credits(plan, tier) -> int:
    return CREDITS[tool_key(plan, tier)]


def _live(price_fn, key, dflt):
    if price_fn is None:
        return float(dflt)
    try:
        v = float(price_fn(key, dflt) or 0)
        return v if v > 0 else float(dflt)
    except Exception:
        return float(dflt)


def rate(plan, tier, price_fn: Optional[Callable] = None) -> float:
    """The price of one group on this plan at this tier, the published
    rate or the seat's live rate through ``price_fn(tool_key, default)``."""
    p = normalize_plan(plan)
    t = normalize_tier(tier, p)
    return _live(price_fn, TOOL_KEYS[(p, t)], RATES[(p, t)])


def upgrade_rate(plan, price_fn: Optional[Callable] = None) -> float:
    """What moving a 1-20 group to 1-40 costs on this plan; 0 on
    enterprise (one tier)."""
    p = normalize_plan(plan)
    if p not in UPGRADE_RATES:
        return 0.0
    return _live(price_fn, UPGRADE_TOOL_KEYS[p], UPGRADE_RATES[p])


def money(v, symbol='$') -> str:
    v = float(v or 0)
    return f"{symbol}{v:,.0f}" if abs(v - round(v)) < 0.009 else f"{symbol}{v:,.2f}"


def rate_card_lines(price_fn: Optional[Callable] = None, symbol='$') -> list:
    """The card in plain lines, for the Twins tab, the chat and the
    guide. Live numbers when a price function is given."""
    m = lambda v: money(v, symbol)   # noqa: E731
    return [
        f"Individual groups - {m(rate('individual', 20, price_fn))} a group for 1-20 questions, {m(rate('individual', 40, price_fn))} a group for 1-40. "
        f"Pay for each group as you open it; no deposit. Best for a single pilot, ad or cut. A card on file with a {m(INDIVIDUAL_BALANCE_FLOOR_USD)} balance.",
        f"Package rate - {m(rate('package', 20, price_fn))} a group for 1-20 questions, {m(rate('package', 40, price_fn))} a group for 1-40, on a "
        f"{m(PACKAGE_DEPOSIT_USD)} deposit. The deposit unlocks the lower rate on every group while it is drawn down. Best for a slate, a season or a campaign tested over time.",
        f"Enterprise agreement - {m(rate('enterprise', 40, price_fn))} a group, 1-40 questions, at {m(ENTERPRISE_MONTHLY_USD)} a month on a {ENTERPRISE_TERM_MONTHS}-month term, paid in full.",
        "A group is one focus group room or one pilot test, in any mode (pilot, episode, feature, binge, trailer, title, key art, script, concept, critics room). Same rates for both. "
        "Each group answers 1-40 questions and covers talent, asset, video, audio, copy and printed material testing. A duplicate or a recut is a new group.",
        f"Choose 1-20 or 1-40 when you open a group. A 1-20 group that reaches twenty questions can move to 1-40 for the difference ({m(upgrade_rate('individual', price_fn))}).",
        "Prometheus usage is metered at the published rate on top, for the build and for every question in a room.",
    ]


def rate_card_text(price_fn: Optional[Callable] = None, symbol='$') -> str:
    return 'Digital Twins IQ pricing, per group:\n\n' + '\n'.join('- ' + ln for ln in rate_card_lines(price_fn, symbol))


def quote_line(plan, tier, price_fn: Optional[Callable] = None, symbol='$', *, kind='group') -> str:
    """One sentence for a confirm card: what this group costs on the
    seat's plan."""
    p = normalize_plan(plan)
    t = normalize_tier(tier, p)
    what = {'pilot': 'This pilot test', 'panel': 'This focus group', 'room': 'This room'}.get(kind, 'This group')
    line = f"{what} is one group at {money(rate(p, t, price_fn), symbol)} ({tier_label(t)}, {plan_label(p).lower()}), charged when it opens."
    if t == TIER_SMALL and p in UPGRADE_RATES:
        line += f" At twenty questions you can move it to 1-40 for {money(upgrade_rate(p, price_fn), symbol)}."
    return line


_PRICING_ASK_RX = re.compile(
    r"\b(?:what (?:does|do|would|will) (?:a|an|the|each|one)?\s*(?:focus group|pilot test|pilot testing|twin room|room|screening|digital twins?|twin)s?(?: room| test)? (?:cost|run|charge)"
    r"|how much (?:is|are|does|do|for|would|will)\b.{0,40}\b(?:focus group|pilot test|pilot testing|digital twins?|twin|twin room|screening)s?"
    r"|(?:digital twins?|twins? iq|focus group iq|focus group|pilot testing|pilot test|screening)s?\b.{0,30}\b(?:pricing|price|prices|rates?|rate card|cost|costs|fee|fees)"
    r"|(?:pricing|price|prices|rates?|rate card|cost|costs)\b.{0,30}\b(?:digital twins?|twins? iq|focus groups?|pilot tests?|pilot testing|screenings?)"
    r"|price per group|per group (?:price|rate|pricing)|package rate|enterprise (?:rate|agreement|pricing)|\$5k deposit|5,?000 deposit)\b", re.I)


def is_pricing_ask(text) -> bool:
    """A question about what a focus group, a pilot test or Digital
    Twins costs (the chat answers with the card)."""
    t = ' '.join(str(text or '').split())
    return bool(t) and len(t) <= 400 and bool(_PRICING_ASK_RX.search(t))


# ------------------------------------------------------------- the plan
def _companies(data) -> dict:
    comps = (data or {}).get('companies') if isinstance(data, dict) else None
    return comps if isinstance(comps, dict) else {}


def _company_record(data, company):
    """The users.json company record (case-insensitive key), or None."""
    name = str(company or '').strip()
    if not name:
        return None
    comps = _companies(data)
    rec = comps.get(name)
    if isinstance(rec, dict):
        return rec
    try:
        from wallet import canonical_company_name
        canon = str(canonical_company_name(name) or '').strip()
        if canon and isinstance(comps.get(canon), dict):
            return comps[canon]
    except Exception:
        pass
    low = name.lower()
    for k, cand in comps.items():
        if isinstance(cand, dict) and str(k).strip().lower() == low:
            return cand
    return None


def _user_record(data, username):
    users = (data or {}).get('users') if isinstance(data, dict) else None
    if not isinstance(users, dict):
        return None
    uname = str(username or '').strip()
    rec = users.get(uname)
    if isinstance(rec, dict):
        return rec
    low = uname.lower()
    for k, cand in users.items():
        if isinstance(cand, dict) and (str(k).lower() == low or str(cand.get('email') or '').strip().lower() == low):
            return cand
    return None


def plan_holder(data, company):
    """The record the plan lives on for a company-scope name: the company
    record, or the seat's own record for ``user:<username>``."""
    c = str(company or '').strip()
    if not c:
        return None
    try:
        from migration.digital_twin_company import OWN_PREFIX
    except Exception:
        OWN_PREFIX = 'user:'
    if c.startswith(OWN_PREFIX):
        return _user_record(data, c[len(OWN_PREFIX):])
    return _company_record(data, c)


def _balance(rec) -> Optional[float]:
    if not isinstance(rec, dict) or 'wallet_balance_usd' not in rec:
        return None
    try:
        return float(rec.get('wallet_balance_usd') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def plan_record(rec) -> dict:
    """The plan as stored on a record, read for activity: ``plan`` is
    the stored plan, ``active_plan`` what the seat is charged at today
    (a lapsed package or an ended enterprise term reads individual),
    with the deposit or term fields and a plain ``status`` line."""
    rec = rec if isinstance(rec, dict) else {}
    stored = normalize_plan(rec.get(PLAN_FIELD))
    meta = rec.get(PLAN_META_FIELD) if isinstance(rec.get(PLAN_META_FIELD), dict) else {}
    out = {'plan': stored, 'active_plan': stored, 'active': True, 'status': '', 'meta': dict(meta)}
    if stored == 'package':
        bal = _balance(rec)
        if bal is not None and bal <= 0.0:
            out.update({'active_plan': DEFAULT_PLAN, 'active': False,
                        'status': f"Package lapsed: the deposit is drawn down. A new {money(PACKAGE_DEPOSIT_USD)} deposit restores the rate."})
        else:
            dep = meta.get('package_deposit_usd')
            out['status'] = (f"Package active on a {money(dep)} deposit" if dep else 'Package active') + (f" (balance {money(bal)})" if bal is not None else '') + '.'
    elif stored == 'enterprise':
        ends = str(meta.get('enterprise_ends_at') or '')[:10]
        today = time.strftime('%Y-%m-%d', time.gmtime())
        if ends and ends < today:
            out.update({'active_plan': DEFAULT_PLAN, 'active': False, 'status': f"Enterprise term ended {ends}. Groups are charged at the individual rate until a new term is set."})
        else:
            monthly = meta.get('enterprise_monthly_usd') or ENTERPRISE_MONTHLY_USD
            months = meta.get('enterprise_term_months') or ENTERPRISE_TERM_MONTHS
            out['status'] = f"Enterprise: {money(monthly)} a month, {months}-month term" + (f", through {ends}" if ends else '') + '.'
    else:
        out['status'] = 'Individual groups: each group is charged as it opens.'
    return out


def plan_for_company(company, data=None) -> str:
    """The plan a company is charged at today ('individual' when none
    is set, when the package has lapsed, or when the enterprise term
    has ended)."""
    if data is None:
        try:
            from migration.digital_twin_company import users_data
            data = users_data()
        except Exception:
            data = {}
    rec = plan_holder(data, company)
    if rec is None:
        return DEFAULT_PLAN
    return plan_record(rec)['active_plan']


def company_for_user(user, username='', data=None) -> str:
    try:
        from migration.digital_twin_company import caller_company
        return caller_company(user, username, data)
    except Exception:
        return ''


def plan_for_user(user, username='', data=None) -> str:
    """The plan of the seat's company (the Digital Twins company scope)."""
    if data is None:
        try:
            from migration.digital_twin_company import users_data
            data = users_data()
        except Exception:
            data = {}
    return plan_for_company(company_for_user(user, username, data), data)


def set_plan(rec, plan, *, by='', package_deposit_usd=None, enterprise_monthly_usd=None, enterprise_term_months=None,
             enterprise_started_at=None, note='') -> dict:
    """Write the plan on a record (company or own seat) in place and
    return its ``plan_record``. Enterprise records the monthly, the term
    and the dates; package the deposit; individual clears the term."""
    if not isinstance(rec, dict):
        raise ValueError('company not found')
    p = normalize_plan(plan)
    meta = rec.get(PLAN_META_FIELD) if isinstance(rec.get(PLAN_META_FIELD), dict) else {}
    meta = dict(meta)
    meta.update({'set_at': _now(), 'set_by': str(by or '')})
    if note:
        meta['note'] = str(note)[:300]
    if p == 'enterprise':
        try:
            monthly = float(enterprise_monthly_usd) if enterprise_monthly_usd not in (None, '') else float(meta.get('enterprise_monthly_usd') or ENTERPRISE_MONTHLY_USD)
        except (TypeError, ValueError):
            monthly = ENTERPRISE_MONTHLY_USD
        try:
            months = int(enterprise_term_months) if enterprise_term_months not in (None, '') else int(meta.get('enterprise_term_months') or ENTERPRISE_TERM_MONTHS)
        except (TypeError, ValueError):
            months = ENTERPRISE_TERM_MONTHS
        months = max(1, months)
        start = str(enterprise_started_at or meta.get('enterprise_started_at') or '')[:10] or time.strftime('%Y-%m-%d', time.gmtime())
        try:
            y, mo, d = (int(x) for x in start.split('-'))
        except Exception:
            y, mo, d = (int(x) for x in time.strftime('%Y-%m-%d', time.gmtime()).split('-'))
            start = f"{y:04d}-{mo:02d}-{d:02d}"
        total = mo - 1 + months
        ey, em = y + total // 12, total % 12 + 1
        meta.update({'enterprise_monthly_usd': monthly, 'enterprise_term_months': months, 'enterprise_started_at': start,
                     'enterprise_ends_at': f"{ey:04d}-{em:02d}-{min(d, 28):02d}"})
        for k in ('package_deposit_usd', 'package_activated_at', 'package_activated_by'):
            meta.pop(k, None)
    elif p == 'package':
        try:
            dep = float(package_deposit_usd) if package_deposit_usd not in (None, '') else float(meta.get('package_deposit_usd') or PACKAGE_DEPOSIT_USD)
        except (TypeError, ValueError):
            dep = PACKAGE_DEPOSIT_USD
        meta.update({'package_deposit_usd': dep, 'package_activated_at': _now(), 'package_activated_by': str(by or 'admin')})
        for k in ('enterprise_monthly_usd', 'enterprise_term_months', 'enterprise_started_at', 'enterprise_ends_at'):
            meta.pop(k, None)
    else:
        for k in ('enterprise_monthly_usd', 'enterprise_term_months', 'enterprise_started_at', 'enterprise_ends_at',
                  'package_deposit_usd', 'package_activated_at', 'package_activated_by'):
            meta.pop(k, None)
    rec[PLAN_FIELD] = p
    rec[PLAN_META_FIELD] = meta
    return plan_record(rec)


def set_company_plan(data, company, plan, **fields) -> dict:
    """``set_plan`` on the company (or own-seat) record inside a users.json
    document; raises ValueError when the company is not there."""
    rec = plan_holder(data, company)
    if rec is None:
        raise ValueError('company not found')
    return set_plan(rec, plan, **fields)


def note_deposit(rec, amount_usd) -> bool:
    """A deposit landed on a wallet record (``wallet.apply_wallet_topup``):
    a deposit of the package size turns the plan to package (an active
    enterprise term is left alone). True when the plan changed."""
    try:
        amt = float(amount_usd or 0)
    except (TypeError, ValueError):
        return False
    if not isinstance(rec, dict) or amt + 1e-9 < PACKAGE_DEPOSIT_USD:
        return False
    cur = plan_record(rec)
    if cur['plan'] == 'enterprise' and cur['active']:
        return False
    if cur['plan'] == 'package' and cur['active']:
        meta = rec.get(PLAN_META_FIELD) if isinstance(rec.get(PLAN_META_FIELD), dict) else {}
        meta = dict(meta)
        meta['package_last_deposit_usd'] = amt
        meta['package_last_deposit_at'] = _now()
        rec[PLAN_META_FIELD] = meta
        return False
    set_plan(rec, 'package', by='deposit', package_deposit_usd=amt)
    return True


# -------------------------------------------------------------- a group
def _plainly_free(user) -> bool:
    """The free flags a seat record carries on its face: super admin,
    unlimited, a Crosswalk staff address."""
    if not isinstance(user, dict):
        return False
    role = str(user.get('role') or '').strip().lower().replace(' ', '_')
    if role in ('super_admin', 'superadmin') or bool(user.get('is_super_admin')) or bool(user.get('unlimited')) or bool(user.get('no_cap')):
        return True
    if str(user.get('billing_mode') or '').strip().lower() == 'unlimited':
        return True
    try:
        if int(user.get('credits', 0) or 0) == -1:
            return True
    except (TypeError, ValueError):
        pass
    return str(user.get('email') or '').strip().lower().endswith('@crosswalknyc.com')


def seat_is_free(user, username='') -> bool:
    """Crosswalk staff, super admins and unlimited seats are never
    charged: the seat's own flags first, then the live row through
    prometheus.priced_read.seat_is_free (only once the app is loaded, so
    a hermetic test never pulls the web app in)."""
    if _plainly_free(user):
        return True
    import sys as _sys
    if 'app' not in _sys.modules:
        try:
            from prometheus.priced_read import staff_user
            return bool(staff_user(user if isinstance(user, dict) else {}))
        except Exception:
            return False
    try:
        from prometheus.priced_read import seat_is_free as _free
        return bool(_free(user, username or None, None))
    except Exception:
        return False


def group_charge(sess) -> dict:
    gc = (sess or {}).get('group_charge')
    return gc if isinstance(gc, dict) else {}


def legacy_paid(sess) -> bool:
    """A room or test priced under the session card before 2026-11-05:
    it paid for its block, so it reads as a paid 1-40 group."""
    s = sess or {}
    if group_charge(s) or s.get('group_tier'):
        return False            # opened under the per-group card: paid only through its group_charge
    try:
        if int(s.get('questions_paid') or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(s.get('play_charged'))


def group_paid(sess) -> bool:
    gc = group_charge(sess)
    if gc and not gc.get('refunded_at'):
        return True
    return legacy_paid(sess)


def group_tier(sess, plan=DEFAULT_PLAN) -> int:
    """The tier the group was opened on (the record), 40 for a legacy
    paid session, else the plan's default."""
    s = sess or {}
    if s.get('group_tier'):
        return normalize_tier(s.get('group_tier'), plan if normalize_plan(plan) != 'enterprise' else 'individual')
    if legacy_paid(s):
        return TIER_LARGE
    return default_tier(plan)


def question_cap(sess, plan=DEFAULT_PLAN) -> int:
    """How many questions this group may answer: its tier."""
    return group_tier(sess, plan)


def questions_asked(sess) -> int:
    return len((sess or {}).get('questions') or [])


def record_tier(sess, tier, plan=DEFAULT_PLAN) -> int:
    """Set the tier on a session record (before the charge, so the open
    quote and the charge agree). Returns the tier."""
    p = normalize_plan(plan)
    t = normalize_tier(tier, p)
    sess['group_tier'] = t
    sess['group_plan'] = p
    return t


def charge_group_open(user, payload, sess, *, plan=None, tier=None, charge_fn: Callable, username='', kind='group', data=None, by='') -> Optional[object]:
    """Charge one group as it opens, through ``charge_fn(tool_key,
    description, job_id)`` (None when charged or free, else the funds
    reply the route returns). Idempotent on the session: a group already
    charged (or a legacy paid session) charges nothing. On success the
    session carries ``group_charge``, ``group_tier``, ``group_plan`` and
    ``questions_paid`` = the tier; on a funds reply nothing is written."""
    s = sess
    if group_paid(s):
        return None
    p = normalize_plan(plan if plan is not None else plan_for_user(user, username, data))
    t = normalize_tier(tier if tier is not None else s.get('group_tier'), p)
    name = str(((payload or {}).get('meta') or {}).get('name') or '').strip()
    title = str(s.get('title') or '').strip()
    label = {'pilot': 'Pilot test', 'panel': 'Focus group', 'room': 'Focus group', 'recut': 'Recut test', 'duplicate': 'Focus group', 'screening': 'Screening'}.get(kind, 'Group')
    desc = f"Digital Twins group ({label}, {tier_label(t)}): {title or name}" + (f" on {name}" if title and name and name.lower() not in title.lower() else '')
    sid = str(s.get('id') or '')
    key = TOOL_KEYS[(p, t)]
    free = seat_is_free(user, username)
    funds = None if free else charge_fn(key, desc, sid)
    if funds is not None:
        return funds
    s['group_tier'] = t
    s['group_plan'] = p
    s['questions_paid'] = t
    s['group_charge'] = {'tool_key': key, 'pull_type': PULL_TYPES[key], 'credits': 0 if free else CREDITS[key], 'usd': 0.0 if free else RATES[(p, t)],
                         'plan': p, 'tier': t, 'job_id': sid, 'description': desc, 'kind': kind, 'charged_at': _now(), 'by': str(by or username or ''), 'free': free}
    s['updated_at'] = _now()
    return None


def refund_group_open(user, sess, *, refund_fn: Callable, reason='') -> bool:
    """Hand the group's open charge back through ``refund_fn(credits,
    reason, job_id, amount_usd, pull_type)`` when the open failed or a
    pilot never started. Marks the charge refunded on the record (a
    second call is a no-op). True when a refund was sent."""
    gc = group_charge(sess)
    if not gc or gc.get('refunded_at'):
        return False
    if not gc.get('free') and int(gc.get('credits') or 0) > 0:
        try:
            refund_fn(int(gc.get('credits') or 0), reason or 'Digital Twins group did not open', str(gc.get('job_id') or sess.get('id') or ''),
                      float(gc.get('usd') or 0) or None, str(gc.get('pull_type') or ''))
        except Exception:
            return False
    gc['refunded_at'] = _now()
    gc['refund_reason'] = str(reason or '')
    sess['group_charge'] = gc
    sess['questions_paid'] = 0
    sess['updated_at'] = _now()
    return True


def offer_tier_upgrade(sess, plan=None, price_fn: Optional[Callable] = None, symbol='$') -> Optional[dict]:
    """The upgrade a 1-20 group is offered at twenty questions: the move
    to 1-40 for the difference at the plan's rate. None when the group
    is already 1-40 or the plan has one tier."""
    p = normalize_plan(plan if plan is not None else (sess or {}).get('group_plan'))
    t = group_tier(sess, p)
    if t >= TIER_LARGE or p not in UPGRADE_RATES:
        return None
    usd = upgrade_rate(p, price_fn)
    return {'from_tier': t, 'to_tier': TIER_LARGE, 'usd': usd, 'label': money(usd, symbol), 'plan': p, 'tool_key': UPGRADE_TOOL_KEYS[p],
            'line': f"This group has answered its {t} questions. Move it to 1-40 for {money(usd, symbol)} and keep asking, or open a new group."}


def question_gate(sess, plan=None, *, accept_upgrade=False, price_fn: Optional[Callable] = None, symbol='$') -> tuple:
    """Whether the next question may run. ``('ok', None)`` under the cap;
    ``('offer', offer)`` at the cap of a 1-20 group (the twenty-first
    waits for the upgrade); ``('accept', offer)`` when the caller
    accepted it (charge the difference, then ask); ``('cap', None)`` at
    forty."""
    p = normalize_plan(plan if plan is not None else (sess or {}).get('group_plan'))
    if questions_asked(sess) < question_cap(sess, p):
        return 'ok', None
    offer = offer_tier_upgrade(sess, p, price_fn, symbol)
    if offer is None:
        return 'cap', None
    return ('accept' if accept_upgrade else 'offer'), offer


def accept_tier_upgrade(user, payload, sess, *, plan=None, charge_fn: Callable, username='', data=None, by='') -> Optional[object]:
    """Charge the difference and move the group to 1-40. None when done
    (or the group already is), else the funds reply."""
    p = normalize_plan(plan if plan is not None else (sess or {}).get('group_plan') or plan_for_user(user, username, data))
    if group_tier(sess, p) >= TIER_LARGE:
        return None
    key = UPGRADE_TOOL_KEYS.get(p)
    if not key:
        sess['group_tier'] = TIER_LARGE
        sess['questions_paid'] = TIER_LARGE
        return None
    name = str(((payload or {}).get('meta') or {}).get('name') or '').strip()
    title = str(sess.get('title') or '').strip()
    desc = f"Digital Twins group upgrade to 1-40 questions: {title or name}"
    sid = str(sess.get('id') or '')
    free = seat_is_free(user, username)
    funds = None if free else charge_fn(key, desc, f"{sid}-upgrade")
    if funds is not None:
        return funds
    sess['group_tier'] = TIER_LARGE
    sess['questions_paid'] = TIER_LARGE
    ups = sess.get('group_upgrades') if isinstance(sess.get('group_upgrades'), list) else []
    ups.append({'tool_key': key, 'pull_type': PULL_TYPES[key], 'credits': 0 if free else CREDITS[key], 'usd': 0.0 if free else UPGRADE_RATES[p], 'plan': p,
                'to_tier': TIER_LARGE, 'job_id': f"{sid}-upgrade", 'charged_at': _now(), 'by': str(by or username or ''), 'free': free})
    sess['group_upgrades'] = ups
    sess['updated_at'] = _now()
    return None


def cap_reply(sess, plan=None) -> str:
    t = group_tier(sess, plan if plan is not None else (sess or {}).get('group_plan'))
    return f"This group has answered its {t} questions, the most a group covers. Open a new group to keep going (a duplicate carries the same room)."


# --------------------------------------------------------------- the gate
def gate_open(user, username='', *, plan=None, tier=None, data=None, price_fn: Optional[Callable] = None, symbol='$') -> tuple:
    """Whether a group may open on this seat before anything is charged.

    Staff and free seats pass. A seat billed through a wallet (a paying
    customer) must hold the group's price in the wallet, and on the
    individual plan a card on file too; the card's $2,000 balance is the
    recommended floor in the copy, the hard gate is the group being
    opened. Seats on internal credits pass here (``consume_credit``
    decides). Returns ``(ok, reply)`` where ``reply`` is the plain
    message with the add-funds link when blocked."""
    if seat_is_free(user, username):
        return True, None
    try:
        import wallet as w
    except Exception:
        return True, None
    if data is None:
        try:
            from migration.digital_twin_company import users_data
            data = users_data()
        except Exception:
            data = {}
    uname = str(username or (user or {}).get('username') or (user or {}).get('email') or '').strip()
    rec = _user_record(data, uname) or (user if isinstance(user, dict) else {})
    try:
        subject, _kind, _key = w.resolve_billing_subject(rec, data)
    except Exception:
        subject = rec
    if not isinstance(subject, dict) or not w.is_paying_customer(subject) or w.is_unlimited(subject):
        return True, None
    p = normalize_plan(plan if plan is not None else plan_for_user(user, uname, data))
    t = normalize_tier(tier, p)
    price = rate(p, t, price_fn)
    try:
        mode = w.billing_mode(subject)
    except Exception:
        mode = ''
    if mode == 'monthly_invoice':
        return True, None
    bal = w.wallet_balance(subject)
    label = money(price, symbol)
    if p == 'individual' and not w.has_card_on_file(subject):
        return False, {'success': False, 'guidance': True, 'needs_card': True, 'add_funds_url': ADD_FUNDS_URL, 'price_usd': price,
                       'error': (f"Opening this group runs {label} ({tier_label(t)}). Individual groups need a card on file with a balance that covers the group; "
                                 f"we recommend keeping {money(INDIVIDUAL_BALANCE_FLOOR_USD, symbol)} on hand. Add a card and funds at {ADD_FUNDS_URL}, and it opens the moment you are set.")}
    if bal + 1e-9 < price:
        return False, {'success': False, 'guidance': True, 'needs_funds': True, 'add_funds_url': ADD_FUNDS_URL, 'price_usd': price, 'balance_usd': round(bal, 2),
                       'error': (f"Opening this group runs {label} ({tier_label(t)}) and your account holds {money(bal, symbol)}. "
                                 f"Add funds at {ADD_FUNDS_URL}" + (f" (we recommend keeping {money(INDIVIDUAL_BALANCE_FLOOR_USD, symbol)} on hand)" if p == 'individual' else '')
                                 + ", and it opens the moment you are set.")}
    return True, None


def public_fields(sess, plan=None) -> dict:
    """What a page needs about the group's billing: the tier, the cap,
    the questions used, the plan, the charge and the upgrade offer."""
    p = normalize_plan(plan if plan is not None else (sess or {}).get('group_plan'))
    t = group_tier(sess, p)
    gc = group_charge(sess)
    out = {'group_tier': t, 'question_cap': t, 'questions_asked': questions_asked(sess), 'group_plan': p, 'group_paid': group_paid(sess),
           'group_charge': {k: gc.get(k) for k in ('usd', 'tier', 'plan', 'charged_at', 'refunded_at', 'kind', 'free') if k in gc} if gc else None}
    offer = offer_tier_upgrade(sess, p)
    if offer is not None and questions_asked(sess) >= t:
        out['upgrade'] = offer
    return out


__all__ = ['PLANS', 'DEFAULT_PLAN', 'TIERS', 'TIER_SMALL', 'TIER_LARGE', 'RATES', 'UPGRADE_RATES', 'PACKAGE_DEPOSIT_USD', 'INDIVIDUAL_BALANCE_FLOOR_USD',
           'ENTERPRISE_MONTHLY_USD', 'ENTERPRISE_TERM_MONTHS', 'TOOL_KEYS', 'UPGRADE_TOOL_KEYS', 'PULL_TYPES', 'CREDITS', 'PLAN_LABELS', 'PLAN_FIELD', 'PLAN_META_FIELD',
           'ADD_FUNDS_URL', 'RETIRED_KEYS', 'normalize_plan', 'normalize_tier', 'tiers_for', 'default_tier', 'tier_label', 'plan_label', 'tool_key', 'upgrade_tool_key',
           'pull_type', 'credits', 'rate', 'upgrade_rate', 'money', 'rate_card_lines', 'rate_card_text', 'quote_line', 'is_pricing_ask',
           'plan_holder', 'plan_record', 'plan_for_company', 'company_for_user', 'plan_for_user', 'set_plan', 'set_company_plan', 'note_deposit',
           'seat_is_free', 'group_charge', 'legacy_paid', 'group_paid', 'group_tier', 'question_cap', 'questions_asked', 'record_tier', 'charge_group_open', 'refund_group_open',
           'offer_tier_upgrade', 'question_gate', 'accept_tier_upgrade', 'cap_reply', 'gate_open', 'public_fields']
