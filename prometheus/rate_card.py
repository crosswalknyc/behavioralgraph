"""The rate card, once (2026-10-09, item 7).

Jenna's card (2026-10-07, Viewership Metrics added 2026-10-08) is the
words; the numbers are the live per-pull prices admins set in the
billing panel (wallet.tool_price_usd, system/pricing.json), which are
also what the charge uses. Rendering the card from those prices means
the pricing lane, the brief card and the charge can never disagree: a
price changed in the panel changes the card the same minute.

  ROWS                        the card, in Jenna's order
  render(symbol, price_fn)    the card text in the seat's currency
  default(key)                the published default for a tool key
  drift(price_fn)             tools whose live price differs from the
                              published default (information for ops)

price_fn(tool_key, default_usd) -> float is supplied by the caller
(prometheus.legacy.watch._pm_usd bound to a username); without one the
published defaults render.
"""
from __future__ import annotations

# (label, tool_key, published_usd, shape)
#   shape 'flat'    -> "{label} - {c}{price}"
#   shape 'split'   -> "{label} - {c}{half} without a control group, {c}{price} with one"
#   shape 'daily'   -> "{label} - {c}{price} for the initial pull and an optional {c}{daily} x day to track per campaign"
#   shape 'followup'-> "{label} - {c}{price} for the first ask on a subject, then metered usage for each follow-up on the same subject"
ROWS = (
    ('Digital Journey', 'journey_iq', 500.0, 'flat'),
    ('Profile', 'profile_iq_build', 300.0, 'flat'),
    ('Subscriber Acquisition', 'subscriber_iq_build', 500.0, 'flat'),
    ('Viewership Metrics', 'viewership_read', 100.0, 'followup'),
    ('Flywheel', 'flywheel_iq', 500.0, 'flat'),
    ('Brand Partnership', 'brand_partnership_iq', 1000.0, 'split'),
    ('Ad Attribution', 'attribution_iq_setup', 500.0, 'daily'),
    ('Any other custom ask (a ranked list, a table, a cross, a top N, what they did next)', 'panel_report', 100.0, 'followup'),
    ('Audience size / sample check', 'derived_read', 100.0, 'flat'),
    ('Breakdown of a delivered figure (by device, daypart, geography or demographic)', 'derived_read', 100.0, 'flat'),
    # Digital Twins IQ (Jenna 2026-11-05): priced PER GROUP. A group is one focus group room or one pilot test, in any
    # mode (pilot, episode, trailer, title, key art, script, concept, critics room), 1-40 questions, at the company's
    # plan rate. The twin build and its first two runs are included; each further run is its own line. The table of
    # record is migration/digital_twin_pricing.py; the session / per-mode / per-panelist lines of October are retired.
    ('Digital Twins IQ focus group or pilot test (one group, any mode)', 'digital_twin_group_individual_20', 750.0, 'group'),
    ('Digital Twin runs (each further Launch, content, pricing, platform or compare run; the twin and its first two runs are included)', 'digital_twin_run', 100.0, 'flat'),
    ('Digital Twin Spend Engine (a plan for its window, daily runs included)', 'digital_twin_spend', 250.0, 'flat'),
)
TWIN_RUN_KEY, TWIN_RUN_DEFAULT = 'digital_twin_run', 100.0
GROUP_KEYS = {('individual', 20): 'digital_twin_group_individual_20', ('individual', 40): 'digital_twin_group_individual_40',
              ('package', 20): 'digital_twin_group_package_20', ('package', 40): 'digital_twin_group_package_40',
              ('enterprise', 40): 'digital_twin_group_enterprise'}
GROUP_DEFAULTS = {'digital_twin_group_individual_20': 750.0, 'digital_twin_group_individual_40': 1000.0,
                  'digital_twin_group_package_20': 500.0, 'digital_twin_group_package_40': 750.0,
                  'digital_twin_group_enterprise': 200.0,
                  'digital_twin_group_upgrade_individual': 250.0, 'digital_twin_group_upgrade_package': 250.0}
PACKAGE_DEPOSIT_USD, ENTERPRISE_MONTHLY_USD, ENTERPRISE_TERM_MONTHS = 5000.0, 20000.0, 12
DAILY_KEY, DAILY_DEFAULT = 'attribution_iq_daily', 100.0
CUT_KEY, CUT_DEFAULT = 'profile_iq_derived_cut', 100.0
# Jenna 2026-10-21: the initial custom ask is $100. Jenna 2026-10-30:
# follow-ups on the same subject in the thread are metered, not priced
# (viewership the same: $100 first, metered after). The key stays for
# old ledgers; 0 means no flat price.
FOLLOWUP_KEY, FOLLOWUP_DEFAULT = 'panel_report_followup', 0.0
# Metered Prometheus usage, per million tokens in / out, per search.
METERED_IN, METERED_OUT, METERED_SEARCH = 10.50, 52.50, 0.021

DEFAULTS = {key: usd for _l, key, usd, _s in ROWS}
DEFAULTS[DAILY_KEY] = DAILY_DEFAULT
DEFAULTS[CUT_KEY] = CUT_DEFAULT
DEFAULTS[FOLLOWUP_KEY] = FOLLOWUP_DEFAULT
DEFAULTS[TWIN_RUN_KEY] = TWIN_RUN_DEFAULT
DEFAULTS.update(GROUP_DEFAULTS)


def default(key):
    return float(DEFAULTS.get(str(key or ''), 0.0))


def money(v, symbol='$'):
    v = float(v)
    return f"{symbol}{v:,.0f}" if abs(v - round(v)) < 0.009 else f"{symbol}{v:,.2f}"


def _price(price_fn, key, dflt):
    if price_fn is None:
        return float(dflt)
    try:
        v = float(price_fn(key, dflt) or 0)
        return v if v > 0 else float(dflt)
    except Exception:
        return float(dflt)


# An unlimited seat (or Crosswalk staff) asking what things cost reads this,
# never a price (Jenna 2026-11-08: "for anyone with unlimited credit it just
# should say included with your plan instead of giving a price").
INCLUDED_LINE = "Included with your plan."
INCLUDED_CARD = (INCLUDED_LINE + " Profiles, custom asks, audience sizes, breakdowns, viewership reads, journeys, "
                 "valuations, attribution campaigns and Digital Twins groups all run on your plan with nothing charged "
                 "to your account. Ask for the read and it starts.")


def render(symbol='$', price_fn=None, included=False):
    """The card, Jenna's words, live numbers. ``included`` is the
    unlimited seat's answer: the one line, no prices."""
    if included:
        return INCLUDED_CARD
    c = symbol or '$'
    lines = ['Pricing is:', '']
    for label, key, dflt, shape in ROWS:
        p = _price(price_fn, key, dflt)
        if shape == 'split':
            half = p / 2.0
            lines.append(f"{label} - {money(half, c)} without a control group, {money(p, c)} with one")
        elif shape == 'followup':
            lines.append(f"{label} - {money(p, c)} for the first ask on a subject, then metered usage for each "
                         f"follow-up on the same subject")
        elif shape == 'daily':
            daily = _price(price_fn, DAILY_KEY, DAILY_DEFAULT)
            lines.append(f"{label} - {money(p, c)} for the initial pull and an optional {money(daily, c)} x "
                         "day to track per campaign")
        elif shape == 'group':
            g = {k: _price(price_fn, GROUP_KEYS[k], GROUP_DEFAULTS[GROUP_KEYS[k]]) for k in GROUP_KEYS}
            up = _price(price_fn, 'digital_twin_group_upgrade_individual', GROUP_DEFAULTS['digital_twin_group_upgrade_individual'])
            lines.append(f"{label} - individual groups {money(g[('individual', 20)], c)} a group for 1-20 questions or {money(g[('individual', 40)], c)} for 1-40, "
                         f"paid as each group opens (a card on file with a {money(2000, c)} balance); package rate {money(g[('package', 20)], c)} / "
                         f"{money(g[('package', 40)], c)} a group on a {money(PACKAGE_DEPOSIT_USD, c)} deposit; enterprise {money(g[('enterprise', 40)], c)} a group "
                         f"(1-40) at {money(ENTERPRISE_MONTHLY_USD, c)} a month on a {ENTERPRISE_TERM_MONTHS}-month term. Choose 1-20 or 1-40 at open; "
                         f"a 1-20 group moves to 1-40 for {money(up, c)}. Same rates for focus groups and pilot tests; a duplicate or a recut is a new group")
        else:
            lines.append(f"{label} - {money(p, c)}")
    lines.append('')
    lines.append(f"All Prometheus (chat bot) usage is billed at a metered rate of "
                 f"{c}{METERED_IN:.2f} / {c}{METERED_OUT:.2f} per million in/out, plus {c}{METERED_SEARCH} per search.")
    return '\n'.join(lines)


def drift(price_fn):
    """[(tool_key, published, live)] where the live price differs from
    the published default. Information, never a correction: the live
    price is the truth and the card already shows it."""
    out = []
    for key, dflt in DEFAULTS.items():
        if dflt <= 0:
            continue    # metered, never a flat price
        live = _price(price_fn, key, dflt)
        if abs(live - dflt) > 0.009:
            out.append((key, dflt, live))
    return out
