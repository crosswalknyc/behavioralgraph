#!/usr/bin/env python3
"""The US subscriber base lane (2026-11-16): a subscriber question about
a streaming service, or about each of them, is answered from the house
registry and is never read as a title by the where-watched lane."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import service_subscribers as S   # noqa: E402
from prometheus import where_watched as W         # noqa: E402

ASKS = [
    "where to find subscriber estimates for each streaming service",
    "i don't care about content. i just care about the number of subscribers to say for example paramount+. do you estimate that anywhere?",
    "how many subscribers does Peacock have in the US",
    "what is netflix's US subscriber base",
    "subscriber counts for netflix, hulu and disney+",
    "do you have subscriber numbers for the streaming platforms",
    "how many US subs does HBO Max have?",
    "subscribers for amc+ and mgm+",
]
NOT_THIS = [
    "how do you estimate churn/retention for a service like paramount+?",
    "who watches love island?",
    "where to watch the office",
    "how many hours of The Office US were streamed last month",
    "Open Paramount+",
    "is Mutiny on Prime Video",
    "paramount+ viewers in the trailing 12 months",
    "what platforms do Glen Powell fans stream on",
    "subscribers who watched landman",
    "build a profile of Netflix subscribers",
    "max subscribers who churned",
]


def main():
    fails = []
    for q in ASKS:
        if not S.is_ask(q):
            fails.append(f"not taken: {q}")
        if W.parse(q) is not None:
            fails.append(f"where_watched took a subscriber ask as a title: {q}")
    for q in NOT_THIS:
        if S.is_ask(q):
            fails.append(f"taken by mistake: {q}")
    # names resolve, in registry order, and the figures are the registry's
    p = S.parse("subscriber counts for netflix, hulu and disney+")
    if [s['name'] for s in p['services']] != ['Netflix', 'Disney+', 'Hulu']:
        fails.append(f"named services wrong: {[s['name'] for s in p['services']]}")
    p = S.parse(ASKS[0])
    if not p['all'] or len(p['services']) != len(S.SERVICES):
        fails.append("the each-service ask did not take every service")
    reply, chips = S.compose(p)
    for s in S.SERVICES:
        if f"{s['us_subscribers']:,}" not in reply or s['name'] not in reply:
            fails.append(f"{s['name']} missing from the all-services reply")
    if 'US subscriber bases' not in reply or S.AS_OF not in reply:
        fails.append("the all-services reply does not say US subscriber bases or the as-of")
    for s in S.SERVICES:
        if s['us_subscribers'] % 10 == 0:
            fails.append(f"{s['name']} figure ends in 0")
    one = S.answer("how many subscribers does Peacock have in the US")
    if not one or '41,318,547' not in one['reply'] or one['action'] != 'answer':
        fails.append("the single-service answer is wrong")
    for bad in ('synth', 'model', 'estimated', 'source', 'panel', 'Claude'):
        if bad.lower() in reply.lower() or bad.lower() in one['reply'].lower():
            fails.append(f"forbidden word in the reply: {bad}")
    # where_watched still takes real titles
    for q, title in (("where to watch the office", "The Office"), ("where can i watch The Numbers Station", "The Numbers Station")):
        got = (W.parse(q) or {}).get('title')
        if got != title:
            fails.append(f"where_watched lost a real title: {q} -> {got}")
    if W.parse("where to find the churn numbers for peacock") is not None:
        fails.append("where_watched took a data phrase as a title")
    if fails:
        print("FAIL"); [print("  -", f) for f in fails]; return 1
    print(f"OK: {len(ASKS)} asks taken, {len(NOT_THIS)} left to their lanes, {len(S.SERVICES)} services in the registry")
    return 0


if __name__ == '__main__':
    sys.exit(main())
