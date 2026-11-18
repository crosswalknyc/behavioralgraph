"""What a build is quoted at is what its approval takes (Jenna 2026-11-17).

A Subscriber IQ ordered with the Profile IQ add-on read "Total: $800
($500 Subscriber IQ + $300 Profile IQ)" in the chat and "+$300" on the
add-on question, and the wallet was charged $500. Jenna was asked which
side should move and chose: "Quote what is charged ($500)". The charge
stays exactly as it is; every quote now reads it.

HOW. The charge is one arithmetic: ``wallet.pull_price_usd`` (the tool
price for the pull, times its quantity, plus the embedded add-on cuts),
reached from an approval through ``wallet.chat_pull_type`` (the pull the
approve step names) and ``wallet.pull_type_to_tool_key``. A quote used
to be worked out separately, in five places (the chat's step lines, the
brief card, the ask log, the question copy, the partner API), each with
its own sum. Every one of them now asks this module, and this module
asks ``wallet.order_price_usd``, which is those same three calls. There
is no second sum to drift.

  price(user, data, decision, cuts)     dollars an approval takes
  for_draft(host, username, draft)      the same, for a drafted brief
  table(user, data)                     the prices a brief card prints,
                                        one per decision and cut count
  table_for_draft(host, user, draft)    that table for one brief, its
                                        dated quarter reads inside it
  from_table(pt, decision, cuts)        a card's lookup in that table
  draft_usd(draft)                      the same, off the draft's table
  stale_quote(host, user, draft, ...)   an Approve on a card that shows
                                        less than the charge is not run
  subiq(...) / lock(...) / cut_each()   the chat's own lines

DATED QUARTER READS (Jenna 2026-11-18: "Charge $100 each, as the chat
used to say"). One quarter read is one calendar quarter of one profile,
its own dated file. The approval charges each at the add-on cut price
(``wallet.quarter_reads_usd``, inside ``wallet.pull_price_usd``), on the
decisions that deliver them (``wallet.billable_quarter_reads``). A
brief's table carries its quarter reads inside every row, so a card
reads its row and is right whatever script drew it.

A BRIEF QUOTED BEFORE A PRICE MOVED is never charged more than its card
shows: ``stale_quote`` compares the figure on the card (the draft's own
table) with the charge, and when the charge is higher nothing starts
and the reader is asked to draft it again. It is the rule the
Prometheus app's stored briefs already keep (``pending_briefs``).

WHAT IT DOES NOT DECIDE. Whether a seat is charged at all (an unlimited
seat, a seat on a plan allowance that reads "Included with your plan.",
a seat still holding credits) is the charge's own decision and
``priced_read.included_with_plan``'s. This module is the amount.

Nothing here charges, and nothing here raises. Regression:
scripts/test_quote_equals_charge.py.
"""
from __future__ import annotations

import copy

DECISIONS = ('new_build', 'time_shifted_refresh', 'derive_cut', 'cut_needs_parent', 'subscriber_iq', 'existing_match')
GRID = 16                   # cut counts 0..GRID are on the table; a card reads the row, it does not add
_PUBLISHED = {'new_build': 300.0, 'time_shifted_refresh': 300.0, 'derive_cut': 100.0, 'cut_needs_parent': 300.0,
              'subscriber_iq': 500.0, 'cut': 100.0}

__all__ = ['DECISIONS', 'GRID', 'PRICE_MOVED', 'SEAT_UNREAD', 'decision_of', 'cuts_of', 'quarters_of', 'price', 'seat', 'for_draft', 'table',
           'table_for_draft', 'from_table', 'draft_usd', 'stale_quote', 'cut_each', 'quarter_each', 'build_usd', 'subiq', 'lock']


def _w():
    import wallet
    return wallet


def decision_of(draft_or_decision):
    """The decision a quote is for, as the approve step spells it."""
    d = draft_or_decision.get('decision') if isinstance(draft_or_decision, dict) else draft_or_decision
    return str(d or 'new_build').strip().lower() or 'new_build'


def cuts_of(draft):
    """How many add-on cuts an approval of this draft charges for (the
    approve step's own list, ``wallet.billable_cuts``)."""
    try:
        return len(_w().billable_cuts(draft))
    except Exception:
        return 0


def quarters_of(draft, decision=None):
    """How many dated quarter reads an approval of this draft charges
    for (the approve step's own list, ``wallet.billable_quarter_reads``:
    the ones the build ships, on a decision that delivers them)."""
    try:
        return len(_w().billable_quarter_reads(draft, decision_of(draft) if decision is None else decision))
    except Exception:
        return 0


def _delivers_quarters(decision):
    try:
        return decision in _w().QUARTER_READ_DECISIONS
    except Exception:
        return decision in ('new_build', 'time_shifted_refresh', 'cut_needs_parent')


def _published(decision, cuts, quarters=0):
    """The published price, through the charge's own arithmetic on the
    shipped defaults; the constants only when the wallet cannot be read."""
    try:
        w = _w()
        v = float(w.order_price_usd(None, {}, decision, cuts, pricing=w.DEFAULT_PRICING, quarter_reads=quarters) or 0)
        if v > 0:
            return v
    except Exception:
        pass
    base, cut = _PUBLISHED.get(decision, _PUBLISHED['new_build']), _PUBLISHED['cut']
    return base + cut * (max(cuts - 1, 0) if decision == 'derive_cut' else cuts) + cut * quarters


def price(user, data, decision, cuts=0, v1=False, pricing=None, owed=False, quarter_reads=0):
    """Dollars approving a build at ``decision`` with ``cuts`` billable
    add-on cuts and ``quarter_reads`` dated quarter reads takes from this
    seat's wallet (``wallet.order_price_usd``, the charge's own
    computation). Quarter reads count only on a decision that delivers
    them. Reusing a profile that already exists takes nothing unless the
    seat owes for the file (``owed``), which is then the same pull the
    charge names. A price that cannot be read quotes the published one.
    Never raises."""
    d = decision_of(decision)
    try:
        n = max(int(cuts or 0), 0)
    except (TypeError, ValueError):
        n = 0
    try:
        q = max(int(quarter_reads or 0), 0) if _delivers_quarters(d) else 0
    except (TypeError, ValueError):
        q = 0
    if d == 'existing_match' and not owed:
        return 0.0
    try:
        v = float(_w().order_price_usd(user if isinstance(user, dict) and user else None, data or {}, d, n,
                                       v1=bool(v1), pricing=pricing, quarter_reads=q) or 0)
        if v > 0:
            return round(v, 2)
    except Exception:
        pass
    return round(_published(d, n, q), 2)


def seat(host, username=None):
    """(user record, users data) for the reader a quote is for: the name
    given, else the session's, else the one the ask door stamped. ({}, {})
    when nobody can be named, which quotes the published price."""
    try:
        uname = str(username or '').strip()
        if not uname:
            from . import ask_outcome as _ao
            uname = _ao.seat_username(None)
        if not uname:
            return {}, {}
        data = host.load_users() or {}
        return ((data.get('users') or {}).get(uname) or {}), data
    except Exception:
        return {}, {}


def _draft_price(user, data, draft, v1=False, pricing=None, decision=None):
    """The charge for approving ``draft`` as it stands: its decision, its
    billable cuts and its dated quarter reads, which is all the approve
    step hands the wallet. A Subscriber IQ's Profile IQ add-on rides the
    tracker's own pull, so a draft reads the same figure with or without
    it, because the charge does."""
    d = decision_of(draft) if decision is None else decision_of(decision)
    return price(user, data, d, cuts_of(draft), v1=v1, pricing=pricing, quarter_reads=quarters_of(draft, d))


def for_draft(host, username, draft, v1=False, decision=None):
    """What approving this drafted brief takes from the reader's wallet
    (``decision``: the one the approve step settled on, when it is known)."""
    user, data = seat(host, username)
    return _draft_price(user, data, draft, v1=v1, decision=decision)


def table(user, data, v1=False, pricing=None, quarter_reads=0):
    """The prices a brief card prints for one seat. ``by_cuts[decision][n]``
    is the charge for that decision with n add-on cuts, every entry read
    from ``price``; a card looks its row up and adds nothing.

    ``quarter_reads`` dated quarter reads are inside every row of a
    decision that delivers them (a brief's own table, ``table_for_draft``);
    ``quarter_reads`` on the table says how many, and ``quarter_read``
    what one costs, so a reader holding a brief whose quarter reads have
    moved since can still say what it costs.

    The flat keys are kept for a card already open in a browser and for
    older readers: the tier price, ``addon_cut`` (one more cut on a
    build), and ``cut_needs_parent`` as a profile with its first cut,
    which is how those readers have always added it up."""
    try:
        q = max(int(quarter_reads or 0), 0)
    except (TypeError, ValueError):
        q = 0
    by = {d: [price(user, data, d, n, v1=v1, pricing=pricing, quarter_reads=q) for n in range(GRID + 1)]
          for d in DECISIONS if d != 'existing_match'}
    out = {d: by[d][0] for d in by}
    out['existing_match'] = 0.0
    out['cut_needs_parent'] = by['cut_needs_parent'][1]
    out['addon_cut'] = round(by['new_build'][1] - by['new_build'][0], 2)
    tracker = {'decision': 'subscriber_iq', 'subiq': {'addon_profile': False}}
    out['subiq_profile_addon'] = round(
        _draft_price(user, data, dict(tracker, subiq={'addon_profile': True}), v1=v1, pricing=pricing)
        - _draft_price(user, data, tracker, v1=v1, pricing=pricing), 2)     # what adding the Profile IQ adds to the charge
    out['quarter_read'] = round(price(user, data, 'new_build', 0, v1=v1, pricing=pricing, quarter_reads=1)
                                - price(user, data, 'new_build', 0, v1=v1, pricing=pricing), 2)
    out['quarter_reads'] = q
    out['by_cuts'] = by
    return out


def table_for_draft(host, username, draft, base=None):
    """The price table for one drafted brief. A brief with no dated
    quarter reads keeps ``base`` (the seat's table) untouched; one that
    carries them gets a table whose rows include them, so the card, the
    ask log and the question copy read the charge off the row. ``base``
    is returned as it is when it already holds this brief's quarter
    reads, and on any failure."""
    try:
        q = len(_w().clean_quarter_reads(draft))
        held = int((base or {}).get('quarter_reads') or 0) if isinstance(base, dict) else 0
        if q == held and (isinstance(base, dict) or not q):
            return base
        user, data = seat(host, username)
        return table(user, data, quarter_reads=q)
    except Exception:
        return base


def from_table(pt, decision, cuts=0, quarter_reads=None):
    """A card's price for (decision, cuts) off a stamped table: the row
    ``table`` wrote. ``quarter_reads`` is the brief's own count: when it
    differs from the count the rows were written with, the difference is
    added at the table's own ``quarter_read`` price (a table from before
    quarter reads were charged carries no such price and adds nothing,
    which is what its card showed). A table from before the rows existed
    is added up the way it always was. None when there is no table."""
    try:
        if not isinstance(pt, dict) or not pt:
            return None
        d = decision_of(decision)
        n = max(int(cuts or 0), 0)
        if d == 'existing_match':
            return 0.0
        by = pt.get('by_cuts') if isinstance(pt.get('by_cuts'), dict) else None
        row = (by.get(d) or by.get('new_build')) if by else None
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            moved = 0.0
            if quarter_reads is not None and _delivers_quarters(d):
                moved = float(pt.get('quarter_read') or 0) * (int(quarter_reads) - int(pt.get('quarter_reads') or 0))
            if n < len(row):
                return float(row[n]) + moved
            return float(row[-1]) + (float(row[-1]) - float(row[-2])) * (n - len(row) + 1) + moved
        cut = float(pt.get('addon_cut') or 0)
        if d == 'derive_cut':
            return cut * max(n, 1)
        if d == 'cut_needs_parent':
            return float(pt.get('cut_needs_parent') or 0) + cut * max(n - 1, 0)
        return float(pt.get(d, pt.get('new_build')) or 0) + cut * n
    except Exception:
        return None


def draft_usd(draft):
    """The price on a drafted brief, off the table the server stamped on
    it for its reader. None when the draft carries no table, so a caller
    prints nothing rather than a guess."""
    try:
        pt = draft.get('price_table') if isinstance(draft, dict) else None
        if not isinstance(pt, dict) or not pt:
            return None
        n = cuts_of(draft) if isinstance(pt.get('by_cuts'), dict) else \
            (len(draft.get('addon_cuts') or []) if isinstance(draft.get('addon_cuts'), list) else 0)
        return from_table(pt, decision_of(draft), n, len(_w().clean_quarter_reads(draft)))
    except Exception:
        return None


PRICE_MOVED = ("The price on this brief has changed since I drafted it, so I did not start it and nothing was "
               "charged. It now comes to {now}. Ask for it again and I will draft a fresh brief at that price.")
SEAT_UNREAD = ("I could not check your account just now, so I did not start this and nothing was charged. Send it "
               "again in a minute.")


def stale_quote(host, username, draft, decision=None, money=None):
    """None when an Approve may go ahead; else the reply for a brief
    whose card shows less than its approval would now take (a card left
    open across a price change). Nothing starts and nothing is charged:
    the reader is asked to draft it again, at the price as it stands.

    The figure on the card is read off the draft's own table, the way
    the card reads it. A draft with no table was never shown a price (a
    seat whose plan includes the run, an order started without a card),
    and a seat that reads "Included with your plan." has no figure to
    protect; both go ahead. So does a brief whose card priced a different
    decision than the one the approve step settled on.

    Two seats are never told a price here. A seat on a plan allowance
    reads no price anywhere: one whose allowance cannot cover the run is
    answered by the funds check, with the plan's own line. And when the
    seat's record cannot be read at this moment, its own rate is not
    known (the figure in hand is the published one, which is not what a
    company on its own rate pays), so nothing starts, nothing is charged
    and no price is named: the reader sends it again."""
    try:
        shown = draft_usd(draft)
        if shown is None:
            return None
        d = decision_of(draft) if decision is None else decision_of(decision)
        if d == 'existing_match' or d != decision_of(draft):
            return None     # the card priced another decision than the one being approved: not a moved price
        user, data = seat(host, username)
        now = _draft_price(user, data, draft, decision=d)
        if now <= float(shown) + 0.005:
            return None
        if not user:
            return {'success': False, 'guidance': True, 'not_started': True, 'error': SEAT_UNREAD}
        from . import plan_allowance as _plan
        w = _w()
        if w.is_plan_allowance(w.resolve_billing_subject(user, data)[0]) or _plan.seat_reads_included(host, None, now, username):
            return None
        label = money(now) if callable(money) else (f"${now:,.0f}" if abs(now - round(now)) < 0.009 else f"${now:,.2f}")
        return {'success': False, 'guidance': True, 'not_started': True, 'price_moved': True,
                'error': PRICE_MOVED.format(now=label)}
    except Exception:
        return None


def cut_each(host, draft=None, username=None):
    """One more add-on cut on this brief, as its approval charges it."""
    try:
        user, data = seat(host, username)
        d = decision_of(draft if isinstance(draft, dict) else 'new_build')
        if d == 'existing_match':
            d = 'new_build'
        v = price(user, data, d, 2) - price(user, data, d, 1)
        if v > 0:
            return round(v, 2)
    except Exception:
        pass
    return _PUBLISHED['cut']


def build_usd(host, decision, username=None):
    """A build at ``decision`` with no cuts, for a line that offers one."""
    user, data = seat(host, username)
    return price(user, data, decision, 0)


def subiq(host, draft, label, money, username=None):
    """The Subscriber IQ add-on step's lines and figures for a seat that
    reads a price: {'total', 'alone', 'extra', 'ask', 'added', 'just'}.
    ``total`` is the approval's charge with the Profile IQ added,
    ``alone`` without it, ``extra`` the difference. All three are read
    from the charge, so the lines say what it takes."""
    d = dict(draft) if isinstance(draft, dict) else {}
    d['decision'] = 'subscriber_iq'
    sq = d.get('subiq') if isinstance(d.get('subiq'), dict) else {}
    with_p, without = copy.copy(d), copy.copy(d)
    with_p['subiq'], without['subiq'] = dict(sq, addon_profile=True), dict(sq, addon_profile=False)
    total, alone = for_draft(host, username, with_p), for_draft(host, username, without)
    extra = round(total - alone, 2)
    t, a = money(total), money(alone)
    tail = "It builds on the same viewers, so demographics and audience size match across both. Reply add it or just the tracker."
    added = ("Added - a Profile IQ for this audience builds alongside the tracker, on the same viewer universe so "
             "the two always agree. ")
    if extra > 0:
        ask = f"Want a Profile IQ for the {label} audience alongside the tracker (+{money(extra)})? {tail}"
        added += f"Total: {t} ({a} Subscriber IQ + {money(extra)} Profile IQ). "
    else:
        ask = f"Want a Profile IQ for the {label} audience alongside the tracker? The price stays at {t} with it added. {tail}"
        added += f"Total: {t} for both. "
    return {'total': total, 'alone': alone, 'extra': extra, 'ask': ask,
            'added': added + "Review the brief below and approve to start the build.",
            'just': f"Just the Subscriber IQ - {a}. Review the brief below and approve to start the build."}


def quarter_each(host, draft=None, username=None):
    """One dated quarter read on this brief, as its approval charges it
    (0.0 on a brief whose decision does not deliver them)."""
    try:
        user, data = seat(host, username)
        d = decision_of(draft if isinstance(draft, dict) else 'new_build')
        if d == 'existing_match':
            d = 'new_build'
        return round(price(user, data, d, 0, quarter_reads=1) - price(user, data, d, 0), 2)
    except Exception:
        return 0.0


def lock(host, draft, username=None):
    """The figures the cut lock line prints: {'total', 'base', 'each',
    'n', 'cuts', 'quarters', 'quarter_each'}. ``total`` is the approval's
    charge for the brief as it stands; ``cuts`` and ``quarters`` the
    add-on cuts and dated quarter reads it charges for, ``n`` the two
    together; ``each`` one cut, ``quarter_each`` one quarter read (the
    same price, by the charge); ``base`` the rest."""
    d = dict(draft) if isinstance(draft, dict) else {}
    if decision_of(d) == 'existing_match':
        d['decision'] = 'new_build'
    n, q = cuts_of(d), quarters_of(d)
    total = for_draft(host, username, d)
    each = cut_each(host, d, username) if n else 0.0
    q_each = quarter_each(host, d, username) if q else 0.0
    return {'total': total, 'n': n + q, 'cuts': n, 'quarters': q, 'each': each or q_each, 'quarter_each': q_each,
            'base': round(total - each * n - q_each * q, 2)}
