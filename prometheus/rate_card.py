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
#   shape 'split'   -> "{label} - {c}{half} + {c}{half} Control for a {c}{price} total"
#   shape 'daily'   -> "{label} - {c}{price} for the initial pull and an optional {c}{daily} x day to track per campaign"
ROWS = (
    ('Digital Journey', 'journey_iq', 500.0, 'flat'),
    ('Profile', 'profile_iq_build', 300.0, 'flat'),
    ('Subscriber Acquisition', 'subscriber_iq_build', 500.0, 'flat'),
    ('Viewership Metrics', 'viewership_read', 500.0, 'flat'),
    ('Flywheel', 'flywheel_iq', 500.0, 'flat'),
    ('Brand Partnership', 'brand_partnership_iq', 1000.0, 'split'),
    ('Ad Attribution', 'attribution_iq_setup', 500.0, 'daily'),
    ('Any other custom ask', 'panel_report', 500.0, 'flat'),
)
DAILY_KEY, DAILY_DEFAULT = 'attribution_iq_daily', 100.0
CUT_KEY, CUT_DEFAULT = 'profile_iq_derived_cut', 100.0
# Metered Prometheus usage, per million tokens in / out, per search.
METERED_IN, METERED_OUT, METERED_SEARCH = 10.50, 52.50, 0.021

DEFAULTS = {key: usd for _l, key, usd, _s in ROWS}
DEFAULTS[DAILY_KEY] = DAILY_DEFAULT
DEFAULTS[CUT_KEY] = CUT_DEFAULT


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


def render(symbol='$', price_fn=None):
    """The card, Jenna's words, live numbers."""
    c = symbol or '$'
    lines = ['Pricing is:', '']
    for label, key, dflt, shape in ROWS:
        p = _price(price_fn, key, dflt)
        if shape == 'split':
            half = p / 2.0
            lines.append(f"{label} - {money(half, c)} + {money(half, c)} Control for a {money(p, c)} total")
        elif shape == 'daily':
            daily = _price(price_fn, DAILY_KEY, DAILY_DEFAULT)
            lines.append(f"{label} - {money(p, c)} for the initial pull and an optional {money(daily, c)} x "
                         "day to track per campaign")
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
        live = _price(price_fn, key, dflt)
        if abs(live - dflt) > 0.009:
            out.append((key, dflt, live))
    return out
