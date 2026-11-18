"""What the chat says about price to a seat on a plan (Jenna 2026-11-17).

Naomi Inoshita at Sony approved a Subscriber IQ and read "This run costs
$0. Wallet balance is $11,000. Top up to keep going." Jenna: "it should
just say included with your plan and run everything UNTIL it hits 11k of
usage at which point it would ask them to top up and email me and liz".

A seat whose plan includes the run (an unlimited seat, Crosswalk staff,
or a plan allowance that still covers it: priced_read.included_with_plan)
reads "Included with your plan." and no price, on every line the chat
writes. A run that cannot be covered reads one shared line
(wallet.funds_refusal_line): a plan allowance is asked to top up with no
price and no balance, a pay-per-use wallet reads its real price, and
nothing ever reads "costs $0".

The copy lives here, not in the legacy chat module; the decisions are
the wallet's (wallet.pull_funding) and priced_read's. Nothing in this
module charges. Never raises.
"""
from __future__ import annotations

import traceback

INCLUDED = 'Included with your plan.'

SUBIQ_JUST = ("Just the Subscriber IQ. Included with your plan. Review the brief below and approve to "
              "start the build.")
SUBIQ_ADDED = ("Added - a Profile IQ for this audience builds alongside the tracker, on the same viewer "
               "universe so the two always agree. Included with your plan. Review the brief below and "
               "approve to start the build.")
OUT_OF_PLAN = ("Your plan's included usage is used up. Top up to keep going. The moment funding lands, "
               "your next ask goes straight through.")
_NO_COVER = 'Your account cannot cover this run right now. Top up to keep going.'

APPROVE_WHAT = {
    'subscriber_iq': 'Subscriber IQ', 'new_build': 'Profile', 'derive_cut': 'Profile cut',
    'cut_needs_parent': 'Profile with a cut', 'time_shifted_refresh': 'Profile refresh',
    'existing_match': 'Profile',
}


def subiq_ask(label):
    """The add-on question on the Subscriber IQ step, for an included seat."""
    return (f"Want a Profile IQ for the {label} audience alongside the tracker? It is included with your "
            f"plan. It builds on the same viewers, so demographics and audience size match across both. "
            f"Reply add it or just the tracker.")


def approve_what(decision, spec):
    """The run an Approve asked for, in a few words, for the plan
    allowance notice to Jenna and Liz ('Subscriber IQ: Outlander')."""
    try:
        kind = APPROVE_WHAT.get(str(decision or '').strip().lower(), 'Profile')
        name = ' '.join(str((spec or {}).get('name') or '').split())[:120]
        return f"{kind}: {name}" if name else kind
    except Exception:
        return ''


def seat_reads_included(host, session=None, amount_usd=None, username=None):
    """True when this seat reads 'Included with your plan.' in place of a
    price: unlimited, staff, or a plan allowance that still covers
    ``amount_usd``. ``host`` carries load_users."""
    try:
        uname = str(username or '').strip()
        if not uname and session is not None:
            try:
                uname = str(session.get('username') or '').strip()
            except Exception:
                uname = ''
        if not uname:
            try:   # a Prometheus app user has no session: the reader the ask door stamped
                from . import ask_outcome as _ao
                uname = _ao.seat_username(None)
            except Exception:
                uname = ''
        if not uname:
            return False
        data = host.load_users()
        user = (data.get('users') or {}).get(uname) or {}
        if not user:
            return False
        from . import priced_read as _pr
        return bool(_pr.included_with_plan(user, uname, host, amount_usd=amount_usd))
    except Exception:
        return False


def refusal_payload(host, username, pull_type, addon_cuts=0, credits_required=0, money=None):
    """The 402 body for an Approve that cannot start for funding reasons.
    The line is the wallet's shared one; a plan allowance carries no
    balance on the payload either."""
    line, verdict, left = _NO_COVER, {}, 0
    try:
        import wallet as _w
        data = host.load_users()
        user = (data.get('users') or {}).get(username) or {}
        verdict = _w.pull_funding(user, data, pull_type, addon_cuts=addon_cuts)
        line = _w.funds_refusal_line(verdict, money=money)
    except Exception:
        traceback.print_exc()
    try:
        _, left = host.check_user_credits(username)
    except Exception:
        left = 0
    return {
        'success': False, 'guidance': True, 'error': line, 'not_started': True,
        'credits_required': credits_required, 'credits_remaining': left,
        'wallet_balance_usd': (None if verdict.get('allowance')
                               else float(verdict.get('balance_usd') or 0.0)),
        'top_up_url': '/wallet', 'top_up_label': 'Add funds',
    }


def out_of_plan_reply(host, username, user, reply):
    """The funds-gate line: a plan allowance that has run out reads the
    plain top-up ask, and Jenna and Liz hear once. Any other seat keeps
    ``reply``."""
    try:
        import wallet as _w
        data = host.load_users()
        seat = (data.get('users') or {}).get(username) or user or {}
        subject, _kind, _key = _w.resolve_billing_subject(seat, data)
        if not _w.is_plan_allowance(subject):
            return reply
        _w.note_allowance_shortfall(
            seat, data, {'covered': False, 'reason': 'insufficient', 'allowance': True, 'paying': True,
                         'tool_key': 'prometheus', 'usd': 0},
            username=username, what='A Prometheus question')
        return OUT_OF_PLAN
    except Exception:
        traceback.print_exc()
        return reply


# ---- the add-on cut lines: an included seat reads no per-cut price
def cut_rate(included, price):
    return 'Cuts are included with your plan' if included else f"{price} per cut"


def cut_prompt_note(included, price):
    return ("cuts are included with the client's plan; never state a price" if included
            else f"each cut costs {price}")


def cut_each(included, price):
    return 'each cut is included with your plan' if included else f"each cut is {price}"


def cut_word(included, price):
    return 'cut' if included else f"{price} cut"


def cut_tag(included, price):
    return '' if included else f" (+{price})"


def lock_total(included, total, base):
    return 'Included with your plan (the base ' if included else f"Total: {total} (base {base} "


def lock_cuts(included, n, price):
    return 'the cuts are ' if included else f"{n} x {price} for the cuts, "


def build_cost_clause(included, price):
    return '. It is included with your plan' if included else f": {price} on approval"


__all__ = ['INCLUDED', 'SUBIQ_JUST', 'SUBIQ_ADDED', 'OUT_OF_PLAN', 'APPROVE_WHAT', 'subiq_ask', 'approve_what',
           'seat_reads_included', 'refusal_payload', 'out_of_plan_reply', 'cut_rate', 'cut_prompt_note',
           'cut_each', 'cut_word', 'cut_tag', 'lock_total', 'lock_cuts', 'build_cost_clause']
