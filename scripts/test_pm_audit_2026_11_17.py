#!/usr/bin/env python3
"""The questions asked on 2026-11-17, as a standing regression set (Jenna
2026-11-17: "audit questions asked today and see how we did and make
smarter from misses").

What missed that day, and what holds it now:

  * "Run a profile for Euphoria" drew a brief for Euphoria, the series on
    Max, and under it "We have Sephora (built Oct 16, 2026) - is that
    what you meant?". A name that is its own subject (the brief says so,
    and the two share no naming word) is never offered a profile with a
    similar spelling (prometheus.match_question.own_subject).
  * "Build Euphoria series viewers" at that question was asked the same
    question again. The subject said again is a no (reply_reading), and
    one profile is asked about once in a thread (asked_before).
  * "no, build fresh", pressed twenty minutes later on a page that no
    longer held the brief, ran as a question of its own on "Build
    Euphoria series". The question is in the thread, so the server
    resolves the answer (lane, service lane match_answer), and an open
    tab no longer refreshes while a brief or a question is waiting.
  * "Run a profile for Euphoria series viewers" drew "I could not find
    Euphoria US ... Did you mean Euphoria?". A suggestion that is the
    name the person typed is taken, not asked (unresolved).
  * "how can i attempt to calculate monthly hours streamed per customer
    for a specific service like paramount+?" was run as a read. It is a
    question about how a figure is worked out (prometheus.how_computed).
    Her "is it done" after a reply that never arrived drew "You don't
    have any profiles running right now".
  * "Pull Subscriber IQ for Starz" got the right question (which Starz
    title?) after 81 seconds; the reader had pressed stop at 56. It is
    asked before any model call (guards.subiq_platform_only).
  * Three briefs printed the working behind the Subject line ("set to
    false pending user confirmation", "NOT a beauty brand or any other
    property sharing the name"). The line says who the subject is
    (prometheus.identity_line).

Hermetic: no model, no S3, no app module. The door checks replay the
day's own words through the request handlers with everything outside
them faked.
"""
from __future__ import annotations

import os
import re
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS = []


def check(ok, name, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else f"  ({str(detail)[:400]})"))
    if not ok:
        FAILS.append(name)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


# the day's own words
BRIEF = ("New Euphoria profile, ready to run. Subject: Euphoria, the series on Max Window: November 17, 2025 to "
         "November 17, 2026 Estimated audience: about 11,394,317 to 15,544,657 US people (4.14% incidence) Avid tier: "
         "~138,789 Cost: Included with your plan Build time: ~20 min Approve to run, or send another message to adjust. "
         "We have Sephora (built Oct 16, 2026) - is that what you meant? Yes uses it as-is (ready now, no charge); no "
         "builds Euphoria fresh, exactly as you described it.")
REASK = "We have Sephora (built Oct 16, 2026) - is that what you meant? Reply yes to use it, or no to build Euphoria fresh."
CHIPS = {'options': [{'label': 'Yes, use it', 'send': 'yes, use it'}, {'label': 'No, build fresh', 'send': 'no, build fresh'}]}
THREAD = [
    {'role': 'user', 'text': 'Run a profile for Euphoria'},
    {'role': 'agent', 'text': BRIEF, 'meta': CHIPS},
    {'role': 'user', 'text': 'Build Euphoria series viewers'},
    {'role': 'agent', 'text': REASK, 'meta': CHIPS},
]
CARA = 'how can i attempt to calculate monthly hours streamed per customer for a specific service like paramount+?'
CALM = 'Working on it. Confirmed your task is in progress and I will email you when it completes.'

print('[A] a name that is its own subject is not offered a profile with a similar spelling')
from prometheus import match_question as MQ   # noqa: E402
q = MQ.parse_question(BRIEF)
check(q == {'candidate': 'Sephora', 'subject': 'Euphoria'}, 'the question under the brief reads as Sephora offered for Euphoria', q)
check(MQ.parse_question(REASK) == {'candidate': 'Sephora', 'subject': 'Euphoria'}, 'the question on its own reads the same')
check(MQ.parse_question('New Euphoria profile, ready to run. Approve to run.') is None, 'a brief with no question is not one')
for note in ('Euphoria, the series on Max',
             'Euphoria, the HBO/Max teen drama series (US), not to be confused with any other property of the same name'):
    check(MQ.own_subject('Euphoria', {'identity_note': note}, 'Sephora'), f'Euphoria is its own subject: {note[:44]}')
check(MQ.own_subject('Euphoria', {}, 'Sephora', kind_conflict=True), 'a series is not a retailer, whatever the brief says')
check(not MQ.own_subject('Euphoria', {}, 'Sephora'), 'no word from the brief and no clash of kinds: the question stands')
check(not MQ.own_subject('Ordeal', {'identity_note': "L'Oreal, the beauty brand"}, "L'Oreal"),
      'a misspelling the brief resolved to the profile is still asked about')
check(not MQ.own_subject('Apple', {'identity_note': 'Apple, the technology company'}, 'Apple TV EST Buyers'),
      'a shared name is still asked about (Apple against Apple TV EST Buyers)')
app = _read('app.py')
gate = app[app.index('def _apply_universe_qualifier_gate'):]
gate = gate[:gate.index('\ndef ', 10)]
check('_mq.own_subject(' in gate and 'allow_ask and not _identity_mismatch and not _own_subject' in gate,
      'the gate asks nothing when the typed name is its own subject')
check('Changed-name gate' in gate and '_typed_swap = True' in gate, 'the changed-name gate is still there')

print('[B] the subject said again is a no, and one profile is asked about once')
check(MQ.reply_reading('Build Euphoria series viewers', 'Euphoria', 'Sephora') == (False, True),
      '"Build Euphoria series viewers" at the question: build it as stated')
check(MQ.reply_reading('no, build fresh', 'Euphoria', 'Sephora') == (False, True), '"no, build fresh" is a no')
check(MQ.reply_reading('yes, use it', 'Euphoria', 'Sephora') == (True, False), '"yes, use it" is a yes')
check(MQ.reply_reading('the Sephora one', 'Euphoria', 'Sephora') == (True, False), 'naming only the profile on offer is a yes')
check(MQ.reply_reading('hmm what is the difference', 'Euphoria', 'Sephora') == (False, False), 'neither is neither')
check(MQ.asked_before(THREAD[:2], 'Sephora') and MQ.asked_before(THREAD, 'SEPHORA - Total Universe'),
      'the thread already asked about Sephora')
check(not MQ.asked_before(THREAD, 'Euphoria') and not MQ.asked_before([], 'Sephora'), 'and about nothing else')
chat = _legacy_view()
check('_mq.reply_reading(answer, draft.get(\'subject\') or subject, disp)' in chat, 'the brief flow reads the reply that way')
check('allow_ask=not _mq_g.asked_before(history, spec_draft.get(\'existing_match_display_name\'))' in chat,
      'the brief flow asks about one profile once')
check('"allow_ask"' not in chat and "body.get('allow_ask')" not in chat, 'and never on a field the page could send')
check(_chat_py_lines() < 19900, 'the brief flow file stays under its line ceiling', len(chat.splitlines()))

print('[C] the answer chips resolve from the thread when the page lost the brief')
la = MQ.lane('no, build fresh', THREAD)
check(bool(la) and la['kind'] == 'no' and la['rewrite'] == 'Build a new profile on Euphoria', 'no, build fresh: build Euphoria', la)
la = MQ.lane('yes, use it', THREAD)
check(bool(la) and la['kind'] == 'yes' and la['rewrite'] == 'Run a profile for Sephora', 'yes, use it: the profile on offer', la)
check(MQ.lane('no, build fresh', THREAD + [{'role': 'user', 'text': 'x'}, {'role': 'agent', 'text': 'Profile building: Euphoria.'}]) is None,
      'only when the question was the last thing said')
check(MQ.lane('no, build fresh', []) is None and MQ.lane('what does avid mean', THREAD) is None,
      'no question in the thread, or another question asked: nothing')
check(MQ.lane('Build Euphoria series viewers', THREAD) is None, 'an order in its own words runs as typed')
svc = _read('prometheus', 'service.py')
check(svc.index('# lane: confirm_answer') < svc.index('# lane: match_answer') < svc.index('# lane: short_reply'),
      'the lane sits between the confirm answer and the short reply')
check("'confirm_answer', 'match_answer', 'short_reply'" in svc, 'and is in the lane order')

print('[D] a suggestion that is the name the person typed is taken, not asked')
u = MQ.unresolved('Euphoria US', 'Euphoria', 'Run a profile for Euphoria series viewers')
check(u == {'adopt': 'Euphoria'}, '"Euphoria US ... Did you mean Euphoria?": carry on with Euphoria', u)
u = MQ.unresolved('Zoomaa', 'Zooma', 'Build a profile for Zoomaa')
check('adopt' not in u and u['chips'] == ['Build a profile for Zooma'] and u['copy'].endswith('Did you mean Zooma?'),
      'a different name is still asked about', u)
long_subj = 'Please run one read for a single window covering all eleven titles in the list above'
u = MQ.unresolved(long_subj, '', long_subj)
check(long_subj not in u['copy'] and 'one name' in u['copy'], 'a sentence is never echoed back as a subject', u)
check('_mq.unresolved(subj, suggest, text)' in chat and '_PM_UNRESOLVED_SUBJECT_COPY' not in chat,
      'the brief flow takes its unresolved reply from there')

print('[E] how a figure is worked out, in the reader\'s own first person')
from prometheus import how_computed as HC   # noqa: E402
a = HC.answer(CARA)
check(bool(a) and a['reply'].startswith('Hours streamed per viewer:') and 'How it is computed:' in a['reply'],
      'monthly hours streamed per customer: the definition and how it is computed', a and a['reply'][:200])
check(bool(a) and 'divided by the people with at least one play' in a['reply'] and 'hours per account' in a['reply'],
      'per viewer and per account are both stated', a and a['reply'])
check(bool(a) and '"Paramount+ hours per viewer by month, past 12 months"' in a['reply'], 'the next step names her service and the figure')
check(bool(a) and not re.search(r'input mix|\d{2,}[.,]\d|\$\d', a['reply']), 'no figures and no price in a definition')
low = (a or {}).get('reply', '').lower()
check(not any(w in low for w in ('model', 'estimat', 'synth', 'panel', 'nielsen', '\u2014')), 'no house-banned words in it')
for t in ('how do i find retention curves on this site', 'where can i find retention curves on this site'):
    r = HC.answer(t)
    check(bool(r) and r['how_computed']['shape'] == 'where', f'still a where question: {t}', r and r['how_computed'])
for t in ('what is the watch time for Paramount+', 'how many hours watched does Tubi have', 'tell me about hours watched on Tubi',
          'Run monthly hours per viewer for Paramount+ across the trailing 12 months', 'how do I calculate my index'):
    check(HC.answer(t) is None, f'still a read or another lane: {t}')
r = HC.answer('how can i calculate churn for a service like hulu')
check(bool(r) and r['how_computed']['topics'] == ['churn'], 'the first person works for churn too', r and r['how_computed'])
page = _read('templates', 'index.html')
check(page.count('function _synthChatLastReplyWasLost()') == 1 and CALM in page
      and 'the answer to your last question has not come back to this chat' in page,
      '"is it done" after a reply that never arrived says so')

print('[F] a service in the title slot is asked about at once')
from prometheus import guards as G   # noqa: E402
check(G.subiq_platform_only('Pull Subscriber IQ for Starz') == 'Starz', 'Pull Subscriber IQ for Starz: Starz is a service')
check(G.subiq_platform_only('Run a Subscriber IQ read on Paramount+') == 'Paramount+', 'Run a Subscriber IQ read on Paramount+')
check(G.subiq_platform_only('Can you pull Subscriber IQ for Hulu?') == 'Hulu', 'Can you pull Subscriber IQ for Hulu?')
for t in ('Pull Subscriber IQ for Outlander season 7 on Starz', 'Pull Subscriber IQ for Landman', 'do you see the Starz Subscriber IQ?',
          'Build a profile for Starz', 'Starz subscribers as a Profile IQ', ''):
    check(G.subiq_platform_only(t) == '', f'not a bare service order: {t!r}', G.subiq_platform_only(t))
core = chat[chat.index('def _pm_interpret_core('):]
check(0 < core.index("_pm_ask_hint(route='subiq_platform_only'") < core.index('_pm_subiq_lookup_answer(user, _lk_title)'),
      'the brief flow asks before the library lookup and before any model call')

print('[G] the Subject line says who the subject is')
from prometheus import identity_line as IL   # noqa: E402
check(IL.clean("Sara Boustany is a lifestyle and fashion influencer/creator active across Instagram and TikTok; no major "
               "same-name collision detected, though she is a relatively niche figure - set to false pending user confirmation")
      == 'Sara Boustany is a lifestyle and fashion influencer/creator active across Instagram and TikTok', 'Sara Boustany')
check(IL.clean('Euphoria, the HBO/Max teen drama series (US), not to be confused with any other property of the same name')
      == 'Euphoria, the HBO/Max teen drama series (US)', 'Euphoria, the HBO/Max teen drama series (US)')
check(IL.clean('Euphoria is the HBO original teen drama series created by Sam Levinson, starring Zendaya - NOT a beauty '
               'brand or any other property sharing the name')
      == 'Euphoria is the HBO original teen drama series created by Sam Levinson, starring Zendaya', 'Euphoria, starring Zendaya')
for keep in ('Euphoria, the series on Max', 'resolved to MS NOW, formerly MSNBC', 'EJAE, the singer behind Golden from KPop Demon Hunters',
             'Zoomaa: I could not place this name with certainty', 'Nike (athletic footwear and apparel brand)'):
    check(IL.clean(keep) == keep, f'left as written: {keep}', IL.clean(keep))
check(IL.clean('Paul Rodriguez, the comedian and actor - not the skateboarder Paul Rodriguez Jr.').endswith('not the skateboarder Paul Rodriguez Jr'),
      'a plain "not the skateboarder" stays: the reader needs it')
check(IL.clean("Assumed to be the user's own brand") == '' and IL.clean('') == '' and IL.clean(None) == '',
      'working and nothing else: empty, and the plain line is used')
line = app[app.index('def _set_resolved_identity_line'):]
line = line[:line.index('\ndef ', 10)]
check('_idl.clean(note)' in line, 'the brief cleans the line before it is shown')

print('[H] an open tab does not refresh while a brief or a question is waiting')
blk = page[page.index('<script id="cw-stale-tab-refresh">'):]
blk = blk[:blk.index('</script>')]
for v in ('synthChatCurrentDraft', 'synthChatBatchDrafts', 'synthChatClarify', 'synthChatActiveRun', 'synthChatStashedDraft',
          'synthChatIncidenceOffer', 'synthChatDiscoveryOffer', 'synthChatAwaitingDateConfirm'):
    check(('return ' + v + ';') in blk, f'held while {v} is set')
check("return 'brief'" in blk and 'if (recent) {' in blk, 'and only while it is recent')

print('[I] the day\'s own words through the door')
from prometheus import replay   # noqa: E402
items = [
    {'question': 'no, build fresh', 'view_id': 'profileIQ', 'subject': 'PValley Viewers', 'history': THREAD},
    {'question': 'no, build fresh', 'view_id': '', 'subject': '', 'history': THREAD},
    {'question': 'Pull Subscriber IQ for Starz', 'view_id': '', 'subject': '', 'history': []},
    {'question': CARA, 'view_id': 'profileIQ', 'subject': 'Paramount+', 'history': []},
    {'question': CARA, 'view_id': '', 'subject': '', 'history': [
        {'role': 'user', 'text': CARA}, {'role': 'agent', 'text': CALM}, {'role': 'user', 'text': 'is it done'},
        {'role': 'agent', 'text': "You don't have any profiles running right now. Start a new one by describing the audience or subject."}]},
]
res = replay.probe_many(items, timeout_s=900)
for i in (0, 1):
    r = res[i]
    check(r.get('route') == 'profile_build' and not r.get('jobs') and 'reading' not in str(r.get('reply') or '').lower(),
          f'no, build fresh after the question starts no read ({"on a profile page" if i == 0 else "no page"})',
          (r.get('route'), r.get('jobs'), str(r.get('reply'))[:160]))
r = res[2]
check(r.get('route') == 'subiq_platform_only' and 'Which Starz title and season?' in str(r.get('reply') or '') and not r.get('jobs'),
      'Pull Subscriber IQ for Starz: which Starz title, at once', (r.get('route'), str(r.get('reply'))[:200]))
for i in (3, 4):
    r = res[i]
    check(r.get('route') == 'how_computed' and not r.get('jobs') and str(r.get('reply') or '').startswith('Hours streamed per viewer:'),
          f'her question is answered from the definitions, no read and no price ({"first ask" if i == 3 else "asked again"})',
          (r.get('route'), r.get('jobs'), str(r.get('reply'))[:160]))

print()
if FAILS:
    print(f'{len(FAILS)} FAILED:')
    for f in FAILS:
        print('  -', f)
    sys.exit(1)
print('all 2026-11-17 audit checks passed')
