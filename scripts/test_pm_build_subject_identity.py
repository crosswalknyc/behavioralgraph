#!/usr/bin/env python3
"""A build runs on the subject the reader named (2026-11-06).

Sydney McLain (WME) asked for a new profile on Tomorrow x Together, the
K-pop group. The interpret read 'x' as an intersect operator, matched
the left word 'Tomorrow' to the catalog's 'Hurry Up Tomorrow Movie',
named the leftover 'Together' a cut, built and delivered 'Hurry Up
Tomorrow Movie - Together', and answered her "please adjust" with a
$100 read offer. Five rules now hold:

  (a) a catalog match needs the WHOLE catalog subject in the ask, as a
      span; one shared word is never identity
  (b) leftover words never become a cut name unless the ask asked for
      a cut (female, 18-24, avid, Los Angeles, a behavioral cohort)
  (c) 'x' / 'X' / '&' / '+' between words bind one entity name
  (d) an entity type the ask names ('the K-pop group') travels with the
      identity; a MOVIE never matches it
  (e) "please adjust" right after an 'is building' turn is a build
      correction (stop, confirm the subject, queue the right one),
      never a priced read

Hermetic: the app.py helpers are AST-extracted and exec'd with stubs
(no Flask, no S3); prometheus/build_fix is driven with a stub host; the
service lane order and wiring are source-checked.

Run:  python3 scripts/test_pm_build_subject_identity.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_SRC = (ROOT / 'app.py').read_text(encoding='utf-8')
CHAT_SRC = (ROOT / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
SERVICE_SRC = (ROOT / 'prometheus' / 'service.py').read_text(encoding='utf-8')
HOST_SRC = (ROOT / 'prometheus' / 'host.py').read_text(encoding='utf-8')

FAIL = 0


def check(name, ok, detail=''):
    global FAIL
    print(('PASS' if ok else 'FAIL'), name, ('' if ok or not detail else f'  [{detail}]'))
    if not ok:
        FAIL += 1


# ----------------------------------------------------------------- extract
_NAMES = (
    '_normalize_for_match', '_IDENTITY_STOP_TOKENS', '_UNIVERSE_QUALIFIER_TOKENS', '_COMMON_CATEGORY_TOKENS',
    '_TU_GENERIC_PARENT_TOKENS', '_ASK_ENTITY_TYPES', '_CUT_LABEL_WORDS', '_ENTITY_CONNECTOR_RE',
    '_ask_entity_type', '_entity_type_compatible', '_identity_span_tokens', '_span_in', '_whole_subject_in_ask',
    '_subject_span_match', '_reads_as_cut_label', '_subject_identity_mismatch', '_catalog_category_for_display',
    '_INTERSECT_OPERATORS_RE', '_intersect_ops_re', '_extract_intersect_operands', '_parent_is_token_prefix',
    '_score_parents_for_left_operand', '_detect_intersect_parent_from_prompt', '_residual_cut_label',
    '_upgrade_cut_label_with_residual', '_compose_cut_name', '_maybe_promote_intersect_to_derive_cut',
    '_guard_catalog_identity', '_CUTLIKE_NAME_RE_CACHE', '_cutlike_display_name_re',
    '_collapse_for_match', '_collapsed_same_entity',
)


def _extract(src, names):
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            out[node.name] = ast.get_source_segment(src, node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    out[t.id] = ast.get_source_segment(src, node)
    missing = [n for n in names if n not in out]
    return out, missing


pieces, missing = _extract(APP_SRC, _NAMES)
check('every helper the rules need exists in app.py', not missing, ', '.join(missing))

NS = {'__name__': 'app_extract', 'print': lambda *a, **k: None}
NS['s3_cache'] = {'jobs': [
    {'display_name': 'Hurry Up Tomorrow Movie', 'category': 'MOVIE', 's3_key': 'Hurry_Up_Tomorrow_Movie_08_22_2026_05_18.csv'},
    {'display_name': 'Hurry Up Tomorrow - Avid Fan', 'category': 'MOVIE', 's3_key': 'Hurry Up Tomorrow - Avid Fan.csv'},
    {'display_name': 'TWICE', 'category': 'MUSICIAN/BAND', 's3_key': 'TWICE_11_05_2026_22_39.csv'},
]}
NS['_partner_download_key_allowed'] = lambda k: True
# ordered: constants first, then functions (functions resolve names at call time anyway)
for n in _NAMES:
    if n in pieces:
        exec(pieces[n], NS)


def _shortlist_stub(prompt, catalog, max_candidates=12):
    """Token-overlap shortlist the way the legacy chat scores it: a
    single shared word is enough to surface a candidate."""
    toks = set(NS['_normalize_for_match'](prompt).split())
    out = []
    for c in catalog:
        name = NS['_normalize_for_match'](f"{c.get('subject') or ''} {c.get('display_name') or ''}")
        ov = toks & set(name.split())
        if ov:
            out.append(dict(c, _score=round(len(ov) / max(len(toks), 1), 2) or 0.5))
    out.sort(key=lambda c: -c['_score'])
    return out[:max_candidates]


NS['_shortlist_profile_matches'] = _shortlist_stub

CATALOG = [
    {'s3_key': 'Hurry_Up_Tomorrow_Movie_08_22_2026_05_18.csv', 'display_name': 'Hurry Up Tomorrow Movie',
     'subject': 'Hurry Up Tomorrow Movie', 'category': 'MOVIE', 'days_old': 18},
    {'s3_key': 'Hurry Up Tomorrow - Avid Fan.csv', 'display_name': 'Hurry Up Tomorrow - Avid Fan',
     'subject': 'Hurry Up Tomorrow', 'category': 'MOVIE', 'days_old': 30},
    {'s3_key': 'Vizio_TV_Owners_08_10_2026_10_00.csv', 'display_name': 'Vizio TV Owners',
     'subject': 'Vizio TV Owners', 'category': 'TECHNOLOGY/DEVICE', 'days_old': 40},
    {'s3_key': 'TWICE_11_05_2026_22_39.csv', 'display_name': 'TWICE', 'subject': 'TWICE',
     'category': 'MUSICIAN/BAND', 'days_old': 1},
]


def cands_for(prompt):
    # the real shortlist is scored against the WHOLE prompt and surfaces
    # 'Hurry Up Tomorrow Movie' on the single word 'tomorrow'
    out = _shortlist_stub(prompt, CATALOG)
    for c in out:
        c['_score'] = max(float(c['_score']), 0.5)
    return out


SYDNEY = 'Build a new profile on Tomorrow x Together'

# ------------------------------------------------------- (c) connectors bind
check('(c) "x" is not an intersect operator: Tomorrow x Together is one name',
      NS['_extract_intersect_operands'](SYDNEY) is None)
check('(c) "X" upper and "&" and "+" bind one name too',
      NS['_extract_intersect_operands']('Build a profile on Tomorrow X Together') is None
      and NS['_extract_intersect_operands']('Build a profile on Ben & Jerrys') is None
      and NS['_extract_intersect_operands']('Build a profile on Gunna x Nike') is None
      and NS['_extract_intersect_operands']('Amazon Prime + Peloton owners') is None)
ops = NS['_extract_intersect_operands']('Vizio TV Owners for Spider-Man Moviegoers')
check('(c) "for" still reads as an intersect', ops == ('Vizio TV Owners', 'Spider-Man Moviegoers'), str(ops))
ops2 = NS['_extract_intersect_operands']('Netflix subscribers who watch F1')
check('(c) "who <verb>" still reads as an intersect', bool(ops2) and ops2[0] == 'Netflix subscribers', str(ops2))

# ------------------------------------------------- (a) whole subject as a span
W = NS['_whole_subject_in_ask']
check('(a) Hurry Up Tomorrow is NOT in "Tomorrow x Together"', not W('Hurry Up Tomorrow Movie', 'Tomorrow x Together'))
check('(a) Hurry Up Tomorrow IS in "Hurry Up Tomorrow female fans"', W('Hurry Up Tomorrow Movie', 'Hurry Up Tomorrow female fans'))
check('(a) a shared word out of order is not a span', not W('Hurry Up Tomorrow Movie', 'Tomorrow Up Hurry fans'))
M = NS['_subject_identity_mismatch']
check('(a) identity: Tomorrow x Together vs Hurry Up Tomorrow Movie is a mismatch',
      M(SYDNEY, 'Tomorrow X Together', 'Hurry Up Tomorrow Movie') is True)
check('(a) identity: Hurry Up Tomorrow female fans vs the movie is the same subject',
      M('Hurry Up Tomorrow female fans', 'Hurry Up Tomorrow', 'Hurry Up Tomorrow Movie') is False)
check('(a) identity: Jennie still matches Jennie (BLACKPINK)',
      M('Build a new profile on Jennie', 'Jennie', 'Jennie (BLACKPINK)') is False)
check('(a) identity: Tomorrow x Together avid fans vs the movie is a mismatch',
      M('Tomorrow x Together avid fans', 'Tomorrow X Together', 'Hurry Up Tomorrow Movie') is True)
check('(a) identity: SharkNinja still matches Shark Ninja',
      M('Build a profile on SharkNinja', 'SharkNinja', 'Shark Ninja') is False)
check('(a) identity: Travel & Leisure Co vs Sustainable Travel Clothing Buyers stays a mismatch',
      M('run a profile on Travel & Leisure Co', 'Travel & Leisure Co', 'Sustainable Travel Clothing Buyers') is True)

# ----------------------------------------------------- (b) leftover words
R = NS['_reads_as_cut_label']
check('(b) "Together" is not a cut', not R('Together'))
check('(b) female / 18-24 / avid / Los Angeles / TVOD Renters read as cuts',
      R('female') and R('18-24') and R('avid fans') and R('Los Angeles') and R('Marvel TVOD Renters') and R('Spider-Man Moviegoers'))
check('(b) a bare second name is not a cut', not R('Nike') and not R('Peloton'))

# ------------------------------------------------- (d) the named entity type
T = NS['_ask_entity_type']
kpop = T('Build a new profile on Tomorrow x Together, the K-pop group')
check('(d) "the K-pop group" names the MUSICIAN/BAND family', bool(kpop) and 'MUSICIAN/BAND' in kpop, str(kpop))
check('(d) a group never matches a MOVIE catalog entry',
      NS['_entity_type_compatible'](kpop, 'MOVIE') is False and NS['_entity_type_compatible'](kpop, 'MUSICIAN/BAND') is True)
check('(d) "the movie" names MOVIE and tolerates an unknown catalog category',
      'MOVIE' in (T('the Hurry Up Tomorrow movie') or ()) and NS['_entity_type_compatible'](T('the movie'), '') is True)
check('(d) a K-pop group ask against a MOVIE catalog entry is a mismatch even sharing a word',
      M('Build a new profile on Hurry Up Tomorrow, the K-pop group', 'Hurry Up Tomorrow', 'Hurry Up Tomorrow Movie') is True)
check('(d) no type named: no judgment from the category', T('Build a new profile on Hurry Up Tomorrow') is None)

# ----------------------------------------- the promoter on Sydney's exact ask
draft = {'decision': 'new_build', 'subject': 'Tomorrow X Together', 'name': 'Tomorrow X Together',
         'brand_category': 'MUSICIAN/BAND', 'run_avid': True}
NS['_maybe_promote_intersect_to_derive_cut'](draft, SYDNEY, cands_for(SYDNEY), catalog=CATALOG)
check("Sydney's ask stays new_build on Tomorrow X Together", draft['decision'] == 'new_build'
      and draft['subject'] == 'Tomorrow X Together' and not draft.get('cut_label')
      and not draft.get('existing_match_s3_key'), str(draft))
check("Sydney's ask keeps MUSICIAN/BAND", draft['brand_category'] == 'MUSICIAN/BAND')

# the old path, replayed: a draft already promoted to the film is demoted
wrong = {'decision': 'derive_cut', 'subject': 'Hurry Up Tomorrow Movie - Together', 'name': 'Hurry Up Tomorrow Movie - Together',
         'existing_match_s3_key': 'Hurry_Up_Tomorrow_Movie_08_22_2026_05_18.csv',
         'existing_match_display_name': 'Hurry Up Tomorrow Movie', 'parent_display_name': 'Hurry Up Tomorrow Movie',
         'derive_type': 'intersect_cut', 'cut_label': 'Together', 'brand_category': 'MUSICIAN/BAND'}
acted = NS['_guard_catalog_identity'](wrong, SYDNEY, 'Tomorrow X Together')
check('the identity backstop demotes a promoted wrong-title cut to new_build on the typed subject',
      acted and wrong['decision'] == 'new_build' and wrong['subject'] == 'Tomorrow X Together'
      and not wrong['existing_match_s3_key'] and not wrong['cut_label'] and not wrong['derive_type'], str(wrong))
check('the backstop remembers the related profile by name only',
      wrong.get('related_profile_display_name') == 'Hurry Up Tomorrow Movie')

# 'Hurry Up Tomorrow female fans' -> derive_cut female off the movie, untouched
fem = {'decision': 'derive_cut', 'subject': 'Hurry Up Tomorrow', 'name': 'Hurry Up Tomorrow - Female',
       'existing_match_s3_key': 'Hurry_Up_Tomorrow_Movie_08_22_2026_05_18.csv',
       'existing_match_display_name': 'Hurry Up Tomorrow Movie', 'derive_type': 'gender_F', 'cut_label': 'Female'}
acted = NS['_guard_catalog_identity'](fem, 'Hurry Up Tomorrow female fans', 'Hurry Up Tomorrow')
check('Hurry Up Tomorrow female fans stays a derive_cut female off the movie',
      not acted and fem['decision'] == 'derive_cut' and fem['derive_type'] == 'gender_F'
      and fem['existing_match_s3_key'].startswith('Hurry_Up_Tomorrow_Movie'), str(fem))

# 'Tomorrow x Together avid fans' -> new_build with avid, no movie
avid = {'decision': 'new_build', 'subject': 'Tomorrow X Together', 'name': 'Tomorrow X Together',
        'brand_category': 'MUSICIAN/BAND', 'run_avid': True}
p2 = 'Tomorrow x Together avid fans'
NS['_maybe_promote_intersect_to_derive_cut'](avid, p2, cands_for(p2), catalog=CATALOG)
NS['_guard_catalog_identity'](avid, p2, 'Tomorrow X Together')
check('Tomorrow x Together avid fans is a new_build with avid and no movie',
      avid['decision'] == 'new_build' and avid['subject'] == 'Tomorrow X Together' and avid.get('run_avid') is True
      and not avid.get('existing_match_s3_key'), str(avid))
# and a Claude draft that picked the movie as the avid's parent is demoted
avid_wrong = {'decision': 'derive_cut', 'subject': 'Hurry Up Tomorrow Movie', 'derive_type': 'avid',
              'existing_match_s3_key': 'Hurry_Up_Tomorrow_Movie_08_22_2026_05_18.csv',
              'existing_match_display_name': 'Hurry Up Tomorrow Movie'}
NS['_guard_catalog_identity'](avid_wrong, p2, 'Tomorrow X Together')
check('an avid derive off the movie for a Tomorrow x Together ask is demoted to new_build',
      avid_wrong['decision'] == 'new_build' and avid_wrong['subject'] == 'Tomorrow X Together', str(avid_wrong))

# the Vizio intersect still promotes (a real cut of a real parent)
viz = {'decision': 'new_build', 'subject': 'Vizio for Spider-Man Moviegoers', 'name': 'Vizio for Spider-Man Moviegoers'}
pv = 'Vizio TV Owners for Spider-Man Moviegoers'
NS['_maybe_promote_intersect_to_derive_cut'](viz, pv, cands_for(pv), catalog=CATALOG)
check('Vizio TV Owners for Spider-Man Moviegoers still promotes to a derive_cut off Vizio TV Owners',
      viz['decision'] == 'derive_cut' and viz.get('existing_match_s3_key', '').startswith('Vizio')
      and viz.get('cut_label') == 'Spider-Man Moviegoers', str(viz))

# --------------------------------------------------------- wiring: app.py
check('_v1_conclude runs the identity backstop after the promoters',
      '_guard_catalog_identity(draft, prompt, _typed_subject_pre)' in APP_SRC)
check('the dashboard interpret path runs the identity backstop after the promoter',
      '_H._guard_catalog_identity(spec_draft, text, _pre_promo_subject)' in CHAT_SRC)
check('the promoter itself refuses a parent that is not the typed subject',
      re.search(r"def _maybe_promote_intersect_to_derive_cut.*?_subject_identity_mismatch\(prompt, draft\.get\('subject'\)", APP_SRC, re.S) is not None)
check('the parent detector demands the whole subject as a span and a compatible type',
      re.search(r"def _detect_intersect_parent_from_prompt.*?_whole_subject_in_ask\(_parent_entity, left\).*?_entity_type_compatible\(_ask_type",
                APP_SRC, re.S) is not None)
check('the interpret prompt carries the identity rules',
      'CATALOG MATCH IDENTITY (MANDATORY - 2026-11-06)' in CHAT_SRC and 'Never split a name at a connector' in CHAT_SRC)

# ------------------------------------------------- (e) the build-fix lane
from prometheus import build_fix as BF  # noqa: E402
from prometheus import service as S  # noqa: E402

THREAD = [
    {'role': 'user', 'ts': '2026-11-06T03:33:34.144Z', 'text': SYDNEY},
    {'role': 'agent', 'ts': '2026-11-06T03:34:57.732Z',
     'text': 'New cut of the existing Hurry Up Tomorrow Movie - Together data, ready to run.\n\nSubject: Tomorrow X Together ...\nCost: $100 on approval'},
    {'role': 'agent', 'ts': '2026-11-06T03:45:02.528Z',
     'text': 'Profile building: Hurry Up Tomorrow Movie - Together (number 1 in line). I will email you when it is complete.',
     'meta': {'run_id': '0kIMBG4dC39z4Q', 'status': 'running', 'subject': 'Hurry Up Tomorrow Movie - Together'}},
    {'role': 'agent', 'ts': '2026-11-06T03:45:11.313Z', 'text': 'Hurry Up Tomorrow Movie - Together is building.'},
]


class _Host:
    def __init__(self):
        self.cancelled = []
        self.alerts = []
        self.queue_url = 'http://engine.test'
        self.queue_secret = 'shh'

    def has(self, name):
        return name in ('queue_url', 'queue_secret', 'error_email')

    def error_email(self, label, err, user_email=None, payload=None):
        self.alerts.append((label, str(err), payload))


host = _Host()
# the engine is not reachable in a hermetic run: the cancel reports 'failed' and ops are alerted
BF._cancel = lambda h, run_id: (h.cancelled.append(run_id) or 'stopping')

check('(e) "please adjust" says the build is wrong', BF.wants_build_fix('please adjust') and BF.wants_build_fix("that's the wrong subject")
      and BF.wants_build_fix('wrong profile') and BF.wants_build_fix('this is running based on the wrong movie'))
check('(e) "approve" / "status" / "how much" are not corrections',
      not BF.wants_build_fix('Approve') and not BF.wants_build_fix('status') and not BF.wants_build_fix('how much does it cost'))
bt = BF.build_turn(THREAD)
check('(e) the thread is sitting on a build', bool(bt) and bt['run_id'] == '0kIMBG4dC39z4Q' and bt['subject'].startswith('Hurry Up Tomorrow Movie'), str(bt))
raw = BF.answer('please adjust', 'smclain', 'f52a37aab5', THREAD, host=host, user={'email': 'smclain@wmegrp.com'})
check('(e) "please adjust" after "is building" routes to the build fix, not a read',
      bool(raw) and raw.get('build_fix') and raw['build_fix']['awaiting_subject'] is True, str(raw))
check('(e) the wrong run is stopped', host.cancelled == ['0kIMBG4dC39z4Q'], str(host.cancelled))
check('(e) the reply carries no price and asks for the subject',
      '$' not in raw['reply'] and 'cost' not in raw['reply'].lower() and 'Which subject' in raw['reply'], raw['reply'])
check('(e) the reply names no internal terms',
      not re.search(r'synth|pipeline|hostmap|claude|worker|queue|run id|derive', raw['reply'], re.I), raw['reply'])

# the lane's own question waits for the subject; the next words become the right build order
pending_thread = THREAD + [{'role': 'user', 'ts': '2026-11-06T03:45:43Z', 'text': 'please adjust'},
                           {'role': 'agent', 'ts': '2026-11-06T03:46:01Z', 'text': raw['reply'], 'meta': {'build_fix': raw['build_fix']}}]
pend = BF.pending_fix(pending_thread)
check('(e) the lane remembers it asked for the subject', bool(pend) and pend.get('awaiting_subject') is True)
rw = BF.rewrite_pending_subject('Tomorrow X Together, the K-pop group', pend)
check('(e) the subject answer becomes the build order', rw == 'Build a new profile on Tomorrow X Together', rw)
check('(e) a cancel or a question is not a subject answer',
      BF.rewrite_pending_subject('never mind', pend) == '' and BF.rewrite_pending_subject('what happened?', pend) == '')

# the words that name the subject queue the right build in the same turn
raw2 = BF.answer('wrong profile, I meant Tomorrow X Together, the K-pop group', 'smclain', 'f52a37aab5', THREAD,
                 host=host, user={'email': 'smclain@wmegrp.com'})
check('(e) "I meant Tomorrow X Together, the K-pop group" names the subject and queues it',
      bool(raw2) and raw2.get('rebuild_text') == 'Build a new profile on Tomorrow X Together, the K-pop group'
      and raw2['build_fix']['intended'] == 'Tomorrow X Together' and raw2['build_fix']['awaiting_subject'] is False,
      str(raw2))

# a finished wrong build is flagged for removal and refund (ops alert), never priced
done_thread = THREAD + [{'role': 'agent', 'ts': '2026-11-06T03:53:36Z',
                         'text': 'Hurry Up Tomorrow Movie - Together is finished and ready in the Select Profile dropdown.',
                         'meta': {'run_id': '0kIMBG4dC39z4Q', 'status': 'complete', 'subject': 'Hurry Up Tomorrow Movie - Together'}}]
host2 = _Host()
raw3 = BF.answer("that's the wrong profile", 'smclain', 'f52a37aab5', done_thread, host=host2, user={'email': 'smclain@wmegrp.com'})
check('(e) a finished wrong build is flagged for removal and a refund',
      bool(raw3) and 'flagged it for removal' in raw3['reply'] and host2.alerts and host2.alerts[0][0] == 'pm/build_fix'
      and not host2.cancelled, str(raw3))

# "please adjust" after a BRIEF (ready to run, nothing building) is the brief flow's, not this lane's
check('(e) "please adjust" after a brief that has not run passes through', BF.answer('please adjust', 'u', 't', THREAD[:2], host=host) is None)
# a normal question after a build is not a correction
check('(e) a question after a build is not a correction', BF.answer('how big is the audience?', 'u', 't', THREAD, host=host) is None)

# --------------------------------------------------- wiring: the service
check('(e) build_fix is a lane right after the cancel and wizard guards', S.LANE_ORDER[0] == 'intake_cancel' and S.LANE_ORDER[1] == 'bpiq_wizard' and S.LANE_ORDER[2] == 'build_fix')
check('(e) the lane markers match LANE_ORDER', list(S.LANE_ORDER) == S.lane_markers())
check('(e) build_fix is never priced', 'build_fix' in S.NEVER_PRICED_LANES and 'build_fix' in S.DETERMINISTIC_LANES)
check('(e) the lane runs before every priced surface',
      SERVICE_SRC.index('# lane: build_fix') < SERVICE_SRC.index('# lane: work_gate')
      and SERVICE_SRC.index('# lane: build_fix') < SERVICE_SRC.index('from . import priced_read as _pr'))
check('(e) the lane persists its question on the turn', "meta['build_fix'] = raw['build_fix']" in SERVICE_SRC)
check('(e) the host binds the engine url for the cancel', "'queue_url': 'SYNTH_QUEUE_URL'" in HOST_SRC)

print()
print('FAILURES:', FAIL)
sys.exit(1 if FAIL else 0)
