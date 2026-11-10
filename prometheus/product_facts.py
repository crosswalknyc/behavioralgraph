"""What Prometheus does, in one or two sentences (Jenna 2026-11-05:
"update the prometheus client guide ... and all other updates like the
brand partnership valuation wizard").

Three questions people ask the chat about the product itself, answered
the same way the client guide answers them, from the product as it
reads today, with the live prices:

  overview    "what can you do", "what can I ask you", "how can you help"
  bulk_list   "can I upload a list of names", "do you do bulk profiles",
              "how do I pull profiles for everyone on a spreadsheet"
  valuation   "how do I value a partnership", "how does the brand
              partnership valuation work", "what is Brand Partnership IQ"
  twin_pricing "what does a focus group cost", "how much is a pilot
              test", "digital twins pricing": the per-group card
              (Jenna 2026-11-05, migration/digital_twin_pricing)

Boundaries (an order wins over this lane):
  * a bulk order with a names sheet on the thread ("pull profiles for
    everyone on this list" with the CSV attached) is the bulk door's
    (prometheus.bulk_pull); the caller passes ``has_names_list`` so the
    question form is answered here and the order form runs there;
  * a valuation order that names its subject ("value the Gunna x Nike
    partnership", "how do I value the Stanley deal") is the wizard's;
    only the general question lands here;
  * a question about an attached file, a status ask, a data question
    and a deck ask keep their own lanes (none of them match here).

Pure module: no Flask, no S3, no model. Prices come from the caller's
``price_fn(tool_key, default_usd)`` (the live rate for the seat) and
fall back to the published rate card. Kill switch ``PM_PRODUCT_FACTS=0``.
"""
from __future__ import annotations

import os
import re

from . import rate_card as _rc

__all__ = ['FAMILIES', 'enabled', 'looks_like', 'family_of', 'answer', 'price_fn_for', 'thread_has_names', 'facts']

FAMILIES = ('overview', 'bulk_list', 'valuation', 'twin_pricing')
_MAX_LEN = 320

_LEAD = r"^\W*(?:(?:hi|hey|hello|ok|okay|so|and|also|prometheus|quick question|one more|one more thing)[,!.:\s]+)*"
_OVERVIEW_RX = re.compile(
    _LEAD + r"(?:"
    r"what (?:all |else )?(?:can|could|do|does) (?:you|prometheus|crosswalk|this (?:thing|tool|chat)|it) (?:do|help (?:me |us )?with|answer|handle|offer|know)\b"
    r"|what (?:are|is) (?:you|prometheus|this) (?:able to do|for|good at|capable of)\b"
    r"|what (?:kinds? of |sorts? of |types? of )?(?:things|questions|asks|stuff) can (?:i|we) ask(?: (?:you|here|prometheus))?\b"
    r"|what can (?:i|we) (?:ask|do|get|run|pull|build) (?:you|here|from you|in here|with you|with prometheus)\b"
    r"|what (?:are|is) your capabilit(?:y|ies)\b"
    r"|(?:tell|show) me what (?:you|prometheus) (?:can do|do(?:es)?)\b"
    r"|how (?:can|could|do) (?:you|prometheus) help(?: me| us)?\b"
    r"|(?:give me |i want )?(?:an? )?(?:quick )?(?:overview|rundown|tour|summary) of (?:what (?:you|prometheus) (?:can )?do|prometheus|your (?:features|capabilities|products))\b"
    r"|what (?:do|does) (?:you|prometheus) do\b"
    r"|help me understand what (?:you|prometheus) (?:can )?do\b"
    r")", re.I)

_Q_FRAME = (r"(?:can|could|may|do|does|would|will|should|is it possible(?: for me)? to|is there (?:a|any) way (?:for me )?to|"
            r"how (?:do|can|would|could|should) (?:i|we|you)|what (?:about|if)|are you able to|do you (?:support|allow|handle|offer|take|accept|do))")
_LIST_NOUN = r"(?:csv|list|spreadsheet|sheet|xlsx|excel|file|roster|batch|column)"
_PEOPLE = r"(?:names|people|talent|talents|subjects|brands|titles|profiles|creators|influencers|celebrities|celebs|athletes|artists|companies|shows|accounts|entities|rows)"
_BULK_RX = re.compile(
    r"(?:"
    r"\b" + _Q_FRAME + r"\b[^?]{0,40}?\b(?:upload|attach|send|give|paste|drop|hand|load|import|submit)\b[^?]{0,30}?\b" + _LIST_NOUN + r"\b"
    r"|\b" + _Q_FRAME + r"\b[^?]{0,50}?\b(?:profiles?|pulls?|builds?)\b[^?]{0,40}?\b(?:in bulk|for (?:a|the|my|an entire|a whole) " + _LIST_NOUN + r"|for (?:everyone|everybody|each (?:name|person|one)|all (?:of )?(?:the |these |those )?" + _PEOPLE + r"|all of them) (?:on|in) (?:a|an|the|my|our|this|that) " + _LIST_NOUN + r"|at once|all at once|in one go|from (?:a|an|the|my|our|this|that) " + _LIST_NOUN + r")"
    r"|\b(?:bulk|batch) (?:upload|pull|pulls|build|builds|profiles?|profile iq|order|request)s?\b"
    r"|\bprofiles? (?:in bulk|for (?:a|the|my|a whole) (?:list|csv|spreadsheet|roster))\b"
    r"|\b(?:many|multiple|several|a lot of|dozens of|hundreds of|\d{2,4}) (?:profiles|people|names|subjects|talent|brands) (?:at once|at a time|in one (?:go|shot|call)|from (?:a|one) " + _LIST_NOUN + r")\b"
    r"|\bupload (?:a |an |my |the )?" + _LIST_NOUN + r" of " + _PEOPLE + r"\b"
    r")", re.I)
_TWIN_RX = re.compile(r"\b(?:digital\s+)?twins?\b|\burls?\b|\blinks?\b", re.I)
_STATUS_RX = re.compile(r"\b(?:how (?:is|are|'s) (?:my|the) (?:list|bulk|batch)|status of (?:my|the) (?:list|bulk|batch)|where is my (?:list|bulk))\b", re.I)

_VAL_FRAME = (r"(?:how (?:do|can|would|should|could|does|did) (?:i|we|you|one|it|this|that|prometheus|crosswalk)|what (?:is|does|are|do)|"
              r"is there a way to|can (?:i|we|you)|could (?:i|we|you)|do you|explain|walk me through|tell me (?:about|how))")
_VALUATION_RX = re.compile(
    r"(?:"
    r"\b" + _VAL_FRAME + r"\b[^?]{0,60}?\b(?:value|valuation|valuing|valuate|price|measure|assess|size up|quantify)\b[^?]{0,40}?\b(?:(?:brand |talent |creator |celebrity )?partnerships?|sponsorships?|brand deals?|endorsements?|collab(?:oration)?s?)\b"
    r"|\b" + _VAL_FRAME + r"\b[^?]{0,60}?\b(?:partnership|sponsorship)\s+(?:valuation|value)s?\b"
    r"|\b(?:brand partnership(?: iq| valuation)?|bpiq|partnership valuation|the valuation wizard)\b[^?]{0,40}?\b(?:work|works|do|does|steps?|how|involve|include|cost|price)\b"
    r"|\b(?:how|what) (?:does|is|do) (?:the )?(?:brand )?partnership (?:valuation|iq|tool|wizard|read)\b"
    r")", re.I)
_NAME_OK = frozenset(('i', 'prometheus', 'crosswalk', 'brand', 'partnership', 'partnerships', 'valuation', 'iq', 'bpiq', 'profile',
                      'flywheel', 'digital', 'twins', 'twin', 'journey', 'attribution', 'subscriber', 'us', 'a', 'the', 'what', 'how',
                      'can', 'is', 'does', 'do', 'explain', 'tell', 'walk', 'value', 'please', 'hi', 'hey', 'hello', 'ok', 'okay'))


def enabled():
    return str(os.environ.get('PM_PRODUCT_FACTS', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(str(text or '').split())


def _names_a_subject(t):
    """A capitalized word that is not the product's own vocabulary, or a
    'X x Y' pairing: the ask names its subject and is an order."""
    if ' x ' in f" {t.lower()} ":
        return True
    for i, w in enumerate(re.findall(r"[A-Za-z][\w'&.+-]*", t)):
        if i == 0:
            continue
        if w[:1].isupper() and w.lower() not in _NAME_OK:
            return True
    return False


def _twin_pricing_ask(t):
    try:
        from migration.digital_twin_pricing import is_pricing_ask
        return bool(is_pricing_ask(t))
    except Exception:
        return False


def family_of(text):
    """'overview' | 'bulk_list' | 'valuation' | 'twin_pricing' | None."""
    if not enabled():
        return None
    t = _clean(text)
    if not t or len(t) > _MAX_LEN:
        return None
    if _OVERVIEW_RX.search(t):
        return 'overview'
    if _STATUS_RX.search(t):
        return None
    if _twin_pricing_ask(t):
        return 'twin_pricing'
    if _BULK_RX.search(t) and not _TWIN_RX.search(t):
        return 'bulk_list'
    if _VALUATION_RX.search(t) and not _names_a_subject(t):
        return 'valuation'
    return None


def looks_like(text):
    return family_of(text) is not None


def price_fn_for(username=None):
    """The live rate for a seat (prometheus.legacy.watch._pm_usd), or
    None so the published rate card renders (tests, a process without
    the web app)."""
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return None
    try:
        from prometheus.legacy.watch import _pm_usd
        return lambda key, dflt: _pm_usd(key, dflt, username)
    except Exception:
        return None


def thread_has_names(uname, tid, host):
    """A names sheet or text list on the thread (the bulk door's own
    check); False when it cannot be read."""
    try:
        from . import bulk_pull as _bp
        return bool(_bp.thread_has_names(uname, tid, host))
    except Exception:
        return False


def _money(price_fn, key, dflt):
    return _rc.money(_rc._price(price_fn, key, dflt))


def facts(price_fn=None):
    """The prices the answers quote, from the live card."""
    profile = _rc._price(price_fn, 'profile_iq_build', _rc.default('profile_iq_build'))
    bp_control = _rc._price(price_fn, 'brand_partnership_iq', _rc.default('brand_partnership_iq'))
    try:
        from . import bulk_pull as _bp
        list_max = int(_bp.MAX_SUBJECTS)
        build_minutes = int(_bp.BUILD_MINUTES)
    except Exception:
        list_max, build_minutes = 2000, 65
    # where finished things land, from the one map of the dashboard
    # (prometheus.dashboard_map, Jenna 2026-11-10); the wayfinding lane
    # gives directions from the same names
    try:
        from . import dashboard_map as _dm
        where = {k: _dm.where_lands(k) for k in ('journey', 'profile', 'twin', 'valuation', 'flywheel', 'campaign')}
        where['dropdown'] = _dm.DROPDOWN_WHERE
    except Exception:
        where = {}
    return {'profile': _rc.money(profile), 'bp_base': _rc.money(bp_control / 2.0), 'bp_control': _rc.money(bp_control),
            'list_max': list_max, 'build_minutes': build_minutes, 'where': where}


INCLUDED_LINE = "Included with your plan."
INCLUDED_PHRASE = "included with your plan"


def answer(text, *, price_fn=None, has_names_list=None, included=False):
    """{'reply', 'followups', 'family'} or None. ``has_names_list`` is a
    callable (or bool) saying whether a names sheet sits on the thread:
    a bulk ORDER with the list attached is never answered here.
    ``included`` is an unlimited seat (or Crosswalk staff): every price
    in the answer reads "included with your plan" (Jenna 2026-11-08)."""
    fam = family_of(text)
    if not fam:
        return None
    f = facts(price_fn)
    if included:
        f = dict(f, profile=INCLUDED_PHRASE, bp_base=INCLUDED_PHRASE, bp_control=INCLUDED_PHRASE)
    if fam == 'bulk_list':
        try:
            from . import bulk_pull as _bp
            is_order = _bp.is_bulk_ask(_clean(text), True)
        except Exception:
            is_order = False
        if is_order:
            try:
                has = has_names_list() if callable(has_names_list) else bool(has_names_list)
            except Exception:
                has = False
            if has:
                return None       # the door reads the list back and runs it
        readback = (f"how many names are already in the library (reused as is), how many are new builds ({INCLUDED_PHRASE}), "
                    f"the window and the time at the current pace" if included else
                    f"how many names are already in the library (reused at no charge), how many are new builds at "
                    f"{f['profile']} each, the window, the price and the time at the current pace")
        reply = (f"Yes. Attach a CSV, TXT or XLSX with one name per row (a column named subject, name, talent, brand or title, "
                 f"else the first text column; up to {f['list_max']:,} names) and say 'pull profiles for everyone on this list'. "
                 f"I read the list back first: {readback} (about {f['build_minutes']} minutes a build, "
                 f"several at a time); say 'Queue the list' and the builds run as capacity allows, each profile landing in Select Profile as it "
                 f"finishes, with a summary email as the list progresses. Ask 'how is my list going' any time.")
        return {'reply': reply, 'followups': ['How is my list going?'], 'family': fam}
    if fam == 'twin_pricing':
        if included:
            reply = (INCLUDED_LINE + " Focus groups and pilot tests open on your company's plan with nothing charged to your account, "
                     "in any mode and at either size (1-20 or 1-40 questions). Open a room on the Focus Group IQ tab or a test on the "
                     "Pilot Testing tab, or say 'Open a focus group with the <subject> twin'.")
        else:
            from migration.digital_twin_pricing import rate_card_text
            reply = (rate_card_text(price_fn) + "\n\nThe plan is set per company; every seat on the company opens groups at that rate, and the group is charged "
                     "to the company wallet when it opens. Open a room on the Focus Group IQ tab or a test on the Pilot Testing tab, or say 'Open a focus group with the <subject> twin'.")
        return {'reply': reply, 'followups': ['Open a focus group', 'Open Pilot Testing', 'How do I use Digital Twins IQ?'], 'family': fam}
    if fam == 'valuation':
        recap = ("You approve the recap, and the valuation lands" if included else "You approve the recap with the price on it, and the valuation lands")
        tail = (f" It is {INCLUDED_PHRASE}, with or without a control group." if included
                else f": {f['bp_base']}, or {f['bp_control']} with a control group.")
        reply = (f"Say 'Value a brand partnership' and I walk you through it one question at a time: the talent or property, the campaign, "
                 f"its dates, the attribution window (the campaign dates plus 30 days, or your own), whether to include a control group, and "
                 f"the assets (paste the links or I find them). {recap} in the "
                 f"Brand Partnership tab with one dollar figure for the partnership and the audience fit, earned media, brand lift and conversion "
                 f"lenses behind it" + ('.' if included else '') + tail)
        return {'reply': reply, 'followups': ['Value a brand partnership'], 'family': fam}
    priced = ("each included with your plan" if included else "each priced before it runs")
    where = f.get('where') or {}
    lands = (f" Each lands on the dashboard: a profile {where['profile']}, a journey {where['journey']}, a twin {where['twin']}; ask me 'where is X' any time."
             if where.get('profile') and where.get('journey') and where.get('twin') else '')
    reply = (f"I answer questions about any US audience from what people actually do, in plain English: who they are, what they watch, "
             f"stream, buy, search and subscribe to, and how two audiences compare. I also build what is not on file yet: a profile of any "
             f"person, title, brand or behavior ({f['profile']}, about an hour), profiles for a whole list of names from an uploaded CSV, a "
             f"Brand Partnership valuation, a Flywheel, a Digital Journey, a Subscriber IQ read and a Digital Twin, {priced}. "
             f"Every data answer comes with a CSV you can download or have emailed." + lands)
    return {'reply': reply, 'followups': ['Run a profile', 'Value a brand partnership', 'Build a Flywheel', 'How do I pull profiles for a list?'],
            'family': fam}
