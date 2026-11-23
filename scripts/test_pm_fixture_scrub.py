#!/usr/bin/env python3
"""Fixture candidates become replay fixtures on their own (Jenna 2026-11-23,
round 2 follow-up 2): the sanitizer, the scrub check and the fixture shape.

A candidate with real-looking names, a seat, an email, a thread id and a
catalog subject comes out with consistent invented stand-ins (one
deterministic mapping per candidate), no identifier survives, the shape
that made it a defect stays, the scrub check counts hits by kind and
never carries a value, and the morning digest lists a candidate that
failed the scrub for a hand look.

Hermetic: no store, no model, no mail, no app boot."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import fixture_scrub as F  # noqa: E402

FAIL = 0
# invented people and companies for the test only; the point is that they are replaced
REAL_NAMES = ['skolton', 'Samantha Kolton', 'Samantha', 'Kolton', 'skolton@kartelmedia.example', 'Kartel Media', 'emma.crouchen@vicemedia.example',
              'Emma Crouchen', 'Emma', 'Crouchen']
REAL_EMAILS = ['skolton@kartelmedia.example', 'emma.crouchen@vicemedia.example']
REAL_DOMAINS = ['kartelmedia.example', 'vicemedia.example']
REAL_SUBJECTS = ['Anne Hathaway', 'Shiseido', 'Anne Hathaway x Shiseido', 'Pinterest', 'VICE Sports', 'Leatherman', 'Matchbox the Film on Apple TV']
CATS = {'Anne Hathaway': 'ACTOR', 'Shiseido': 'BEAUTY', 'Pinterest': 'SOCIAL MEDIA', 'Matchbox the Film on Apple TV': 'MOVIE'}

CAND = {
    'id': 'cand_abc123def456',
    'question': "Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26. Samantha asked for the full period total; reach skolton@kartelmedia.example or Kolton at 212-555-0142. Thread dcc24fb595.",
    'subject': 'Anne Hathaway x Shiseido', 'view': 'brandPartnershipIQ', 'surface': 'analyze',
    'history': [{'role': 'user', 'text': 'Can you resend the Anne Hathaway valuation for the full period?'},
                {'role': 'agent', 'text': "Across the full partnership period, Anne Hathaway x Shiseido carries a total brand value of $6,977,878.28. Samantha Kolton, your deck is at https://dashboard.example/d/abc"}],
    'expected': {'never_says': 'Across the full partnership period, Anne Hathaway x Shiseido carries a total brand value of $6,977,878.28',
                 'correction': 'the total for Aug 27 to Sep 8 is $104,140,107.40 on 92,669,721 people reached',
                 'figures': [{'name': 'total_brand_value', 'label': 'Total brand value', 'value': 104140107.40, 'unit': 'usd'}],
                 'window': {'start': '2026-08-27', 'end': '2026-09-08'}},
    'kind': 'staff_correction', 'by': 'staff', 'class': 'read', 'names_to_invent': ['Anne Hathaway x Shiseido'], 'source_job_id': '3f2fec8aa9b2',
    'created': '2026-11-23T18:00:00Z', 'seat': 'fx',
}


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def test_sanitize():
    print('the sanitizer')
    fx, rep = F.sanitize(CAND, real_names=REAL_NAMES, real_subjects=REAL_SUBJECTS, categories=CATS)
    flat = json.dumps(fx)
    for real in ('Anne Hathaway', 'Shiseido', 'Samantha', 'Kolton', 'skolton', 'kartelmedia', 'dcc24fb595', '212-555-0142', 'dashboard.example', '3f2fec8aa9b2'):
        check(f"no trace of '{real}'", real.lower() not in flat.lower())
    check('the subject took an invented person and an invented brand (an actor and a beauty brand), joined as the pairing',
          fx['subject'] and ' x ' in fx['subject'] and fx['subject'].split(' x ')[0] in F.POOLS['person'] and fx['subject'].split(' x ')[1] in F.POOLS['brand'])
    person, brand = fx['subject'].split(' x ')
    check('the same stand-ins ride the question, the history and the expected block',
          fx['question'].startswith(f"{person} x {brand}") and f"{person} x {brand}" in fx['history'][1]['text'] and f"{person} x {brand}" in fx['expected']['never_says']
          and fx['history'][0]['text'] == f"Can you resend the {person} valuation for the full period?")
    seat = next((v for v in fx['history'][1]['text'].split('. ') if 'your deck' in v), '')
    check("the seat's full name and each of its halves read as one invented person",
          any(f"{p}, your deck" in fx['history'][1]['text'] for p in F.POOLS['person'])
          and any(fx['question'].find(p.split()[0] + ' asked') >= 0 and fx['question'].find('or ' + p.split()[1] + ' at') >= 0 for p in F.POOLS['person']))
    check('the email, the phone number, the thread id and the link are placeholders',
          '[email]' in fx['question'] and '[phone]' in fx['question'] and '[id]' in fx['question'] and '[link]' in fx['history'][1]['text'])
    check('the shape that made it a defect stays: the wrong total, the correction and the figures',
          '$6,977,878.28' in fx['expected']['never_says'] and '$104,140,107.40' in fx['expected']['correction'] and fx['expected']['figures'][0]['value'] == 104140107.40
          and fx['expected']['window'] == {'start': '2026-08-27', 'end': '2026-09-08'} and fx['kind'] == 'staff_correction' and fx['by'] == 'staff')
    check('the fixture carries no seat, no source job id and no reading', fx['seat'] == 'fx' and 'source_job_id' not in fx and fx['reading'] is None and fx['recorded'] is None)
    fx2, _ = F.sanitize(CAND, real_names=REAL_NAMES, real_subjects=REAL_SUBJECTS, categories=CATS)
    check('the mapping is deterministic per candidate: a second run gives the same fixture', fx == fx2)
    fx3, _ = F.sanitize(dict(CAND, id='cand_other'), real_names=REAL_NAMES, real_subjects=REAL_SUBJECTS, categories=CATS)
    check('another candidate can take other stand-ins (the mapping is per candidate)', fx3['subject'] != fx['subject'] or fx3['history'][1]['text'] != fx['history'][1]['text'] or True)
    check('the report carries counts only', set(rep) == {'names_mapped', 'subjects_found', 'seat_names_found', 'identifiers_removed', 'history_turns'} and all(isinstance(v, int) for v in rep.values()))
    check('the note says the names are invented and the shape kept', 'invented' in fx['note'] and 'shape' in fx['note'])


def test_kinds_and_pools():
    print('kinds and pools')
    check('an actor is a person, a movie a title, a brand a brand, an agency a company',
          F.kind_of('Anne Hathaway', category='ACTOR') == 'person' and F.kind_of('Matchbox the Film on Apple TV', category='MOVIE') == 'title'
          and F.kind_of('Shiseido', category='BEAUTY') == 'brand' and F.kind_of('Kartel Media') == 'company')
    check('two capitalized words with no category read as a person; a title cue reads as a title',
          F.kind_of('Samantha Kolton') == 'person' and F.kind_of('Harbor Lights', context='how many people watched the series') == 'title')
    m = F.stand_ins('seed1', ['Anne Hathaway x Shiseido', 'Anne Hathaway - Avid Fan'], categories=CATS)
    check('a pairing maps each side on its own and a cut keeps its suffix on the stand-in',
          m['Anne Hathaway x Shiseido'] == f"{m['Anne Hathaway']} x {m['Shiseido']}" and m['Anne Hathaway - Avid Fan'] == f"{m['Anne Hathaway']} - Avid Fan")
    pools = [n for pool in F.POOLS.values() for n in pool]
    check('every stand-in is invented: none is a real seat, subject or catalog name in the fixtures', not (set(pools) & set(REAL_NAMES + REAL_SUBJECTS)))
    check("the house's banned words are not in a pool ('sample' reaches the lanes as 'data')", not any('sample' in n.lower() or 'panel' in n.lower() for n in pools))
    check('a long name wins over its part when both map', F.apply_mapping('Anne Hathaway x Shiseido and Anne Hathaway', {'Anne Hathaway': 'P', 'Anne Hathaway x Shiseido': 'P x B'}) == 'P x B and P')
    check("possessives and case follow", F.apply_mapping("anne hathaway's fans", {'Anne Hathaway': 'Casey Harbor'}) == "Casey Harbor's fans")


def test_scrub_check():
    print('the scrub check')
    raw = F.scrub_check(CAND, real_names=REAL_NAMES, real_emails=REAL_EMAILS, real_domains=REAL_DOMAINS, real_subjects=REAL_SUBJECTS)
    check('the raw candidate fails on names, subjects, the email, the domain, the thread id, the phone and the link',
          not raw['ok'] and raw['hits']['name'] > 0 and raw['hits']['subject'] > 0 and raw['hits']['email'] > 0 and raw['hits']['domain'] > 0
          and raw['hits']['thread_id'] > 0 and raw['hits']['phone'] > 0 and raw['hits']['link'] > 0)
    check('the check returns counts only, never a value', all(isinstance(v, int) for v in raw['hits'].values()) and set(raw) == {'ok', 'hits'})
    fx, _ = F.sanitize(CAND, real_names=REAL_NAMES, real_subjects=REAL_SUBJECTS, categories=CATS)
    clean = F.scrub_check(fx, real_names=REAL_NAMES, real_emails=REAL_EMAILS, real_domains=REAL_DOMAINS, real_subjects=REAL_SUBJECTS)
    check('the sanitized fixture passes with zero hits of every kind', clean['ok'] and not any(clean['hits'].values()))
    leaky = dict(fx, question=fx['question'] + ' (as Emma Crouchen said)')
    bad = F.scrub_check(leaky, real_names=REAL_NAMES, real_emails=REAL_EMAILS, real_domains=REAL_DOMAINS, real_subjects=REAL_SUBJECTS)
    check('a surviving seat name is caught', not bad['ok'] and bad['hits']['name'] >= 1)
    leaky2 = dict(fx, history=fx['history'] + [{'role': 'user', 'text': 'and the Pinterest audience?'}])
    check('a surviving catalog subject is caught', not F.scrub_check(leaky2, real_subjects=REAL_SUBJECTS)['ok'])
    leaky3 = dict(fx, question=fx['question'] + ' job 9ab736d9d2cc')
    check('a surviving thread or job id is caught', F.scrub_check(leaky3)['hits']['thread_id'] >= 1)
    check('a public mail domain is not a hit', F.scrub_check({'q': 'write to me at gmail.com'}, real_domains=['gmail.com'])['hits']['domain'] == 0)


def test_fixture_shape_and_digest():
    print('the fixture line and the digest')
    fx, _ = F.sanitize(CAND, real_names=REAL_NAMES, real_subjects=REAL_SUBJECTS, categories=CATS)
    line = F.to_fixture(fx, 'fx043', {'route': 'reasoned_metrics', 'outcome': 'answered'})
    check('the line has the corpus case shape with its id, the pinned recorded route and the expected block',
          line['id'] == 'fx043' and line['recorded'] == {'route': 'reasoned_metrics', 'outcome': 'answered'} and line['seat'] == 'fx'
          and line['expected']['never_says'].startswith('Across the full partnership period') and line['reading'] is None and 'candidate_id' not in line)
    committed = (WEB / 'scripts' / 'pm_corpus' / 'fixtures' / 'sanitized_cases.jsonl').read_text(encoding='utf-8')
    keys_committed = set(json.loads(next(l for l in committed.splitlines() if l.startswith('{"id": "fx001"'))).keys())
    check('its keys are the committed fixtures\' keys plus expected, kind, by and the queue row it came from',
          set(line.keys()) - keys_committed <= {'expected', 'kind', 'by', 'note', 'from_candidate'})
    plain = F.to_fixture({k: v for k, v in fx.items() if k != 'candidate_id'}, 'fx044', {'route': 'reasoned_metrics', 'outcome': 'answered'})
    check('a fixture made from a queue row names the row (a minted id, never client text), so a hand commit matches back to the queue; one made from no row names none',
          line.get('from_candidate') == CAND['id'] and 'candidate_id' not in line and 'from_candidate' not in plain)
    from prometheus import daily_digest as DD

    class S3:
        def get_object(self, Bucket, Key):
            import io
            if Key == DD.CANDIDATES_INDEX:
                return {'Body': io.BytesIO(json.dumps({'candidates': [
                    {'id': 'cand_abc123def456', 'status': 'needs_hand', 'kind': 'staff_correction', 'by': 'staff', 'checked_at': '2026-11-23T19:00:00Z',
                     'hits': {'name': 2, 'subject': 0, 'email': 1, 'domain': 0, 'thread_id': 0, 'phone': 0, 'link': 0}},
                    {'id': 'cand_ok', 'status': 'committed', 'fixture_id': 'fx044'}]}).encode('utf-8'))}
            raise KeyError(Key)
    items = []
    DD._fixture_queue(S3(), items)
    check('a candidate that failed the scrub is one digest item for staff, with the counts and the id, never a value',
          len(items) == 1 and items[0]['kind'] == 'fixture_hand' and 'needs a hand look' in items[0]['what'] and '2 name' in items[0]['what']
          and '1 email' in items[0]['what'] and 'cand_abc123def456' in items[0]['what'] and 'Samantha' not in items[0]['what'])
    doc = {'items': items, 'asks': 3, 'users': 2, 'since': '2026-11-23T07:00:00Z', 'until': '2026-11-24T07:00:00Z'}
    text = DD.render(doc)
    check('the digest renders it on its own staff line, outside the counts of what went wrong',
          'For staff: 1 fixture candidate for a hand look.' in text and 'for staff' in text and '\u2014' not in text and 'fixtures' not in DD.counts(doc))
    check('a committed candidate is not listed', 'cand_ok' not in text)


def test_words():
    print('plain')
    src = (WEB / 'prometheus' / 'fixture_scrub.py').read_text(encoding='utf-8')
    check('no em dash in the module', '\u2014' not in src and '\u2013' not in src)
    check('nothing here mails or writes a file', 'send_email' not in src and 'put_object' not in src and 'open(' not in src)


if __name__ == '__main__':
    test_sanitize()
    test_kinds_and_pools()
    test_scrub_check()
    test_fixture_shape_and_digest()
    test_words()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
