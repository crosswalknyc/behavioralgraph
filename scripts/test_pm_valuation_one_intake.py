#!/usr/bin/env python3
"""A valuation ask has one intake: the wizard, which can ask
(Jenna 2026-11-11).

Which side of a pairing is the talent is known or asked, never read from
the order the names were written in. The wizard asks when nothing
settles it. The older one-shot brief reader
(`intake_reader.keyword_parse_brand_partnership`, behind the intake flow
table's 'brand_partnership' row) cannot ask: for a pairing nothing is
known about it keeps the written order. This holds the reason that is
safe: no ask a reader types can reach that reader.

  1. the guided-intake turn sends every valuation ask to the wizard and
     returns before the flow table is read
  2. nothing calls the guided-intake turn with the valuation flow; the
     analyze handler and the one door both go to the wizard
  3. the only other reader of the flow table's row is the internal
     canary: a shared secret, no user, no thread, no charge, no job
  4. the wizard, given a pairing nothing is known about, asks which one
     is the talent; the one-shot reader, given the same words, cannot

Run: python3 scripts/test_pm_valuation_one_intake.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ['CORPUS_CATALOG'] = '0'
sys.modules.setdefault('app', None)

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import intake_reader as IR  # noqa: E402
from prometheus import partnership_roles as ROLES  # noqa: E402

FAIL = 0
N = 0


def check(label, cond):
    global FAIL, N
    N += 1
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def body_of(src, name):
    """The source of one top-level function."""
    m = re.search(rf"^def {re.escape(name)}\(", src, re.M)
    if not m:
        return ''
    rest = src[m.start():]
    nxt = re.search(r"^(?:def |@|[A-Za-z_][\w]* = )", rest[1:], re.M)
    return rest[:nxt.start() + 1] if nxt else rest


def main():
    chat = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
    svc = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()

    print("1. the guided-intake turn sends a valuation ask to the wizard before the flow table is read")
    turn = body_of(chat, '_pm_intake_turn')
    head = turn.split("if flow == 'flywheel':")[0]
    check("the valuation branch is first", "if flow == 'brand_partnership':" in head
          and head.index("if flow == 'brand_partnership':") < turn.index('_pm_intake_flow_table()[flow]'))
    branch = head.split("if flow == 'brand_partnership':")[1]
    check("it asks the wizard", '_bpiq_wiz.analyze_hook(' in branch)
    check("and returns either way: the wizard's reply, or the wizard's own first step",
          'return hit' in branch and '_bpiq_wiz.step_prompt(_bpiq_wiz.empty_draft())' in branch
          and "'bpiq_wizard': _bpiq_wiz.empty_draft()}" in branch
          and branch.rstrip().endswith("'bpiq_collect': True, 'bpiq_wizard': _bpiq_wiz.empty_draft()}"))
    check("the one-shot readers are not named anywhere in that branch", 'keyword_parse' not in branch and '_pm_bpiq_parse' not in branch
          and '_pm_intake_resolve' not in branch)

    print("2. nothing sends a valuation ask to the guided-intake turn")
    calls = re.findall(r"_pm_intake_turn\(\s*'([a-z_]+)'", chat + svc)
    check("the guided-intake turn is called for journeys, flywheels and campaigns only",
          bool(calls) and set(calls) <= {'digital_journey', 'flywheel', 'attribution'} and 'brand_partnership' not in calls)
    check("no call passes the flow in a variable", not re.search(r"_pm_intake_turn\(\s*(?!')[A-Za-z_]", chat.replace('def _pm_intake_turn(flow', ''))
          and '_pm_intake_turn' not in svc)
    check("the analyze handler asks the wizard for a valuation, twice over", chat.count('_bpiq_wiz.analyze_hook(') >= 4)

    print("3. the only other reader of the valuation row is the internal canary")
    readers = [m.start() for m in re.finditer(r"(?<!def )_pm_intake_flow_table\(\)", chat)]
    owners = set()
    for pos in readers:
        defs = [m for m in re.finditer(r"^def (\w+)\(", chat[:pos], re.M)]
        owners.add(defs[-1].group(1) if defs else '')
    check(f"the flow table is read in three places {sorted(owners)}",
          owners == {'_pm_intake_resume_reply', '_pm_intake_turn', 'api_internal_intake_canary'})
    resume = body_of(chat, '_pm_intake_resume_reply')
    check("the resume reply reads only the row's opening copy", '_pm_intake_flow_table()[flow][3]' in resume
          and 'parse_fn' not in resume and 'fallback' not in resume)
    canary = body_of(chat, 'api_internal_intake_canary')
    check("the canary needs the build server's shared secret", "request.headers.get('X-Synth-Auth')" in canary
          and 'compare_digest(secret, given)' in canary)
    check("the canary has no user, charges nothing and starts nothing", 'user=None' in canary and 'alert=False' in canary
          and not re.search(r"start_engine_job|consume_credit|_pm_run_\w+_job|_charge|status_write|_pm_pending_intake|threading\.", canary))
    resolvers = re.findall(r"^def (\w+)\(|(_pm_intake_resolve)\(", chat, re.M)
    owner, using = '', set()
    for d, call in resolvers:
        if d:
            owner = d
        elif owner != '_pm_intake_resolve':
            using.add(owner)
    check(f"the one-shot read is run by the guided-intake turn and the canary only {sorted(using)}",
          using == {'_pm_intake_turn', 'api_internal_intake_canary'})

    print("4. the wizard asks; the one-shot reader cannot")
    ROLES.KIND_LOOKUP = {}.get                      # nothing is known about either name
    draft = W.apply_ahead(W.empty_draft(), 'Zorb x Quix 9/22/26 to 11/11/26')
    draft = W.sane_draft(draft)
    draft['step'] = W.next_step(draft)
    reply, chips = W.step_prompt(draft)
    check("the wizard, on a pairing nothing is known about, asks which one is the talent",
          draft.get('step') == W.STEP_TALENT and reply.startswith('Which one is the talent or property: ')
          and set(chips[:2]) == {'Zorb', 'Quix'})
    kw = IR.keyword_parse_brand_partnership('Zorb x Quix 9/22/26 to 11/11/26')
    check("the one-shot reader, on the same words, has only the written order to go on",
          (kw.get('qualifier'), kw.get('brand_partner')) == ('Zorb', 'Quix'))
    ROLES.KIND_LOOKUP = {'gap': 'brand'}.get
    kw = IR.keyword_parse_brand_partnership('value the Gap x JYT partnership 9/22/26 to 11/11/26')
    check("and where something is known it uses it", (kw.get('qualifier'), kw.get('brand_partner')) == ('JYT', 'Gap'))
    ROLES.KIND_LOOKUP = None

    print(f"\n{'OK' if not FAIL else 'FAIL'} ({N} checks)" if not FAIL else f"\nFAIL ({FAIL} of {N})")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
