#!/usr/bin/env python3
"""New-numbers quote first; sizing is $100 (Jenna 2026-10-21).

Jay Patel, Emmet Riley and Debi Pomerantz were getting incrementality,
path tables, ranked lists, where-watched and sample sizes at the
metered chat rate. Jenna: quote, they accept, then answer. Sizing is
$100, not free. A custom ask is $100 for the first on a subject in a
thread and $50 for each follow-up on the same subject (Jenna, later the
same day: "only $100 instead of $500 for the initial custom ask then
$50 x subsequent"). Talk on a paid file stays metered. Kartel stickers
stay $275 / $925.

Hermetic: imports priced_read / overlap_read and reads chat.py /
service.py / rate_card.py as source. No network.
"""
from __future__ import annotations

import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus.priced_read import (  # noqa: E402
    APPROVE_CHIP, CANCEL_CHIP, COST_LINE, classify, display_subject, is_talk,
    needs_generate_quote, offer, same_frame, service_turn, sizing_lead,
)
from prometheus import ask_outcome  # noqa: E402
from prometheus.overlap_read import is_overlap_read  # noqa: E402

FAIL = 0


def check(name, ok):
    global FAIL
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAIL += 1


# --- sizing is $100 -------------------------------------------------
chip = classify("how big is Chipotle in the dataset")
check("Chipotle sizing is a $100 sizing",
      bool(chip) and chip.get('kind') == 'sizing' and chip.get('usd') == 100)
check("Chipotle sizing is the service lane, not generate",
      bool(chip) and chip.get('via') == 'service')
check("FanDuel audience size is sizing $100, not overlap",
      classify("how big is the fanduel audience size")['kind'] == 'sizing'
      and not is_overlap_read("how big is the fanduel audience size"))
check("sample size of Chipotle buyers is sizing",
      classify("sample size of Chipotle buyers")['kind'] == 'sizing')
check("two data sets FanDuel and Kalshi is sizing",
      classify("i need the size of two data sets, first FanDuel customers, "
               "the second Kalshi customers")['kind'] == 'sizing')

# --- consumption is not sizing --------------------------------------
marie = ("how many people watched Everybody Loves Raymond on Prime Video "
         "in September 2026 by platform as a table")
check("Marie watched-count is not sizing",
      (classify(marie) or {}).get('kind') != 'sizing')
check("Marie is not talk", is_talk(marie) is False)

# --- overlap / incrementality: a $100 custom ask ---------------------
jay = "how incremental is Lore Olympus to Prime Video?"
hit = classify(jay)
check("Lore Olympus incrementality is generate $100",
      bool(hit) and hit.get('via') == 'generate' and hit.get('usd') == 100)
check("needs_generate_quote on incrementality",
      needs_generate_quote(jay) is True)

# --- same-frame extra title $100 ------------------------------------
prior = [
    {'role': 'user', 'text': 'how incremental is The Kiss Quotient to Prime Video?'},
    {'role': 'agent', 'text': (
        'The Kiss Quotient is incremental to Prime Video. 1,284,317 people '
        'who engaged with it had not streamed Prime Video in the prior 30 days. '
        'That is 18.4 percent of the title audience.')},
]
addon = classify("how incremental is Lore Olympus to Prime Video?", prior)
check("Lore Olympus after Kiss Quotient incrementality is $100",
      bool(addon) and addon.get('same_frame') is True and addon.get('usd') == 100)
check("same_frame helper agrees",
      same_frame(prior, 'incrementality|prime video') is True)
declined = [
    {'role': 'user', 'text': 'how incremental is The Kiss Quotient to Prime Video?'},
    {'role': 'agent', 'text': (
        'I can pull that as a custom read on The Kiss Quotient and deliver '
        'the numbers in this chat. Pulling this data will cost $500.')},
]
check("a declined incrementality offer is not a paid frame",
      same_frame(declined, 'incrementality|prime video') is False)

# --- talk stays metered ---------------------------------------------
check("Brock insights is talk",
      is_talk("craziest insights from the Brock profile") is True)
check("what does this 36.2% mean is talk",
      is_talk("what does this 36.2% mean") is True)
check("where do I see it is talk",
      is_talk("where do I see the Love Hypothesis file") is True)
check("talk classify is None",
      classify("craziest insights from the Brock profile") is None)
tania = classify("how many UFC fans are there out there, of those, "
                 "how many are on Paramount+, on HBO Max and how many "
                 "have neither service")
check("Tania on-service split is a cross: a $100 custom ask in the sizing lane",
      bool(tania) and tania.get('kind') == 'cross' and tania.get('via') == 'service'
      and tania.get('usd') == 100)

# --- ranked list ----------------------------------------------------
lst = classify("who are the top 25 YouTube creators")
check("top 25 YouTube creators is a $100 list",
      bool(lst) and lst.get('kind') == 'list' and lst.get('usd') == 100)

# --- offer copy is the one shape (Jenna 2026-10-21, twice) ----------
# One sentence on what they get, 'Pulling this data will cost $N.', then
# Approve / Cancel. Never 'I will not answer until you accept', never
# 'That is a $N read', never 'It runs $N', never the page's own label.
reply, chips, off = offer('Chipotle', '$100', 'how big is Chipotle',
                          kind='sizing', frame='sizing')
check("sizing offer names $100", '$100' in reply)
check("sizing offer ends on the cost line",
      reply.endswith(COST_LINE.format(price='$100')))
check("sizing offer names the subject in the lead",
      reply.startswith('I can size Chipotle'))
check("chips are Approve and Cancel", chips == [APPROVE_CHIP, CANCEL_CHIP])
check("sizing offer kind is sizing", off.get('kind') == 'sizing')
RETIRED = ('I will not answer', 'That is a $', 'It runs $', 'Run the full read',
           'Run the breakdown', 'Never mind')
check("no retired phrasing in the sizing offer",
      not any(x in reply or any(x in c for c in chips) for x in RETIRED))
for kind, subj in (('list', 'DraftKings users'),
                   ('path', 'Landman viewers'), ('overlap', 'Lore Olympus'),
                   ('derived', 'The Office US'), ('report', 'Eastside Golf')):
    r, c, o = offer(subj, '$500', 'q', kind=kind)
    check(f"{kind} offer: one shape, names {subj}",
          subj in r and r.endswith(COST_LINE.format(price='$500'))
          and c == [APPROVE_CHIP, CANCEL_CHIP]
          and not any(x in r for x in RETIRED))

# Casey Pearson, 2026-10-21 22:33 UTC, open profile 'market size for prank
# content': the offer read "market size for prank content: data, share, and
# US people in the window. That is a $100 read. I will not answer until you
# accept." The subject was the open profile's title; the ask names three
# content categories and Paramount+.
casey = ("what is the market size for prank content, horror, and comedy "
         "and how penetrated is Paramount+ in each of them?")
casey_ctx = {'primary': {'name': 'market size for prank content',
                         'subject': 'market size for prank content'}}
lead = sizing_lead(casey, casey_ctx)
check("Casey: the lead names the categories and Paramount+",
      lead == "I can size the market for prank content, horror and comedy "
              "and Paramount+'s share of each.")
casey_out = service_turn(casey, user={'username': 'cpearson'}, ctx=casey_ctx,
                         history=[], host=None)
check("Casey: the offer is the lead plus the $100 cost line",
      bool(casey_out) and casey_out.get('reply') == lead + ' ' + COST_LINE.format(price='$100'))
check("Casey: Approve / Cancel chips",
      bool(casey_out) and casey_out.get('followups') == [APPROVE_CHIP, CANCEL_CHIP])
check("Casey: the page label never leads the offer",
      bool(casey_out) and 'market size for prank content:' not in casey_out.get('reply', '')
      and not casey_out.get('reply', '').lower().startswith('market size'))
check("Casey: no retired phrasing",
      bool(casey_out) and not any(x in casey_out.get('reply', '') for x in RETIRED))
check("Casey: the offer still arms the sizing confirm",
      bool(casey_out) and (casey_out.get('panel_offer') or {}).get('kind') == 'sizing')
check("the page label alone shows no subject",
      display_subject('market size for prank content', casey,
                      'market size for prank content') == '')
check("the open tab never leads a path offer (Liz, Netflix open)",
      offer('Netflix', '$100',
            'What did Landman viewers watch next after the Season 2 finale '
            'on Paramount+, and which other streaming services do they pay '
            'for? plus the share who subscribe to Netflix, Peacock, Max and '
            'Prime Video.', kind='path', page='Netflix')[0]
      == 'I can pull what Landman viewers did next. ' + COST_LINE.format(price='$100'))
check("a metric phrase never leads an offer (Bria, 'Unique')",
      display_subject('Unique', 'Give me unique US viewers per surface') == '')

# --- the retired sentences can never ship again ---------------------
for old_line in ("market size for prank content: data, share, and US people in the "
                 "window. That is a $100 read. I will not answer until you accept.",
                 "The path for Netflix. That is a $500 read. I will not answer until you accept.",
                 "I can split the figures by device. It runs $100.",
                 "Run the full read - $500"):
    check(f"gate holds the retired offer line: {old_line[:38]}",
          ask_outcome.is_faulted_text(old_line) is True)
check("the new offer line passes the gate",
      ask_outcome.is_faulted_text(casey_out.get('reply', '')) is False)
check("an out-of-funds line is not an offer closer",
      ask_outcome.is_faulted_text("The Chipotle read runs $100 and your account "
                                  "cannot cover it right now.") is False)

# --- service / chat wiring ------------------------------------------
service = (WEB / 'prometheus' / 'service.py').read_text()
check("service calls priced_read.service_turn",
      'priced_read' in service and 'service_turn' in service)
check("service persist keeps priced_paid",
      "meta['priced_paid']" in service)
chat = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text()
check("interpret incidence quotes before the sample check",
      'outcome=\'panel_offer\'' in chat[chat.find('INCIDENCE / SAMPLE-SIZE'):
                                       chat.find('INCIDENCE / SAMPLE-SIZE') + 2500]
      or 'outcome="panel_offer"' in chat)
inc_block = chat[chat.find('INCIDENCE / SAMPLE-SIZE'):
                 chat.find('INCIDENCE / SAMPLE-SIZE') + 2800]
check("interpret incidence offer uses priced_read",
      'priced_read' in inc_block and 'sizing_offer_payload' in inc_block)
check("generate_metrics hands the sizing / cross confirm to the lane (never where-watched)",
      "in ('sizing', 'cross')" in chat and "'sizing', 'where_watched'" not in chat)
# NEVER THIS (2026-10-21): where a named title is watched is a lookup of carriage, the free lane, never a quote
check("Jay's Mutiny turn is never a priced hit",
      classify('where people watched the film mutiny', [], {'primary': {'name': 'Amazon Prime Video'}}) is None
      and needs_generate_quote('where people watched the film mutiny') is False)
check("where-watched is out of the custom family", 'where_watched' not in __import__('prometheus.priced_read', fromlist=['CUSTOM_FAMILY']).CUSTOM_FAMILY)
gate_at = chat.find("NEW-NUMBERS ON AN OPEN FILE (2026-10-21)")
digest_at = chat.find("Base rows ride the prompt (2026-08-27, toy-categories routing)")
check("with-base new-numbers gate sits before the digest load",
      0 < gate_at < digest_at)
check("same-frame rides the derived kind through the one gate",
      '_same_frame' in chat[gate_at:digest_at]
      and "_pr_kind = 'derived'" in chat[gate_at:digest_at]
      and 'offer_or_charge' in chat[gate_at:digest_at])
check("unconfirmed generate quote returns panel_offer",
      'panel_offer' in chat[gate_at:digest_at])

# --- rate card and Kartel stickers ----------------------------------
card = (WEB / 'prometheus' / 'rate_card.py').read_text()
check("rate card names audience size at derived_read $100",
      'Audience size / sample check' in card and "'derived_read', 100.0" in card)
check("custom ask is $100 first on a subject", "'panel_report', 100.0" in card)
check("custom ask follow-up is $50", "'panel_report_followup', 50.0" in card)
wp = (WEB / 'wallet.py').read_text()
check("Kartel profile sticker stays 275", '275' in wp)
check("Kartel partnership sticker stays 925", '925' in wp)

print("\n" + ("OK" if not FAIL else f"{FAIL} FAILED"))
sys.exit(1 if FAIL else 0)
