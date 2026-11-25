#!/usr/bin/env python3
"""The catalog files a turn's figures under what the turn is about, never
under a name that merely appears in it (2026-11-25).

Armine's film reads were filed under Prime Video, MovieSphere+, Tyler
Perry, Target and Martin Lawrence: the thread sweep took the longest
catalog name anywhere in the question or the reply, so a platform, an
actor, a name inside a title, or the common word 'target' became the
subject.

Holds (the sweep, corpus_catalog_sync.backfill_threads, through
corpus_catalog.turn_subjects):
  1. a read about a film that mentions its platform and an actor files
     under the film only, with its bound subject and without one
  2. "Tyler Perry's Meet the Browns" files under the film, not Tyler Perry
  3. a genuine two-subject comparison files under both: a bound 'X vs Y'
     subject files each figure under the title its sentence names; a
     question comparing two titles files under both
  4. a turn the conversation marked retired or withdrawn is never filed;
     a turn about nothing the catalog can name is not filed at all
  5. the write hook still files a reply under the subject it is given
  6. unfile_source takes one source off one page and leaves it filed
     elsewhere, never marked retired

Hermetic: REGRESSION_TEST_MODE=1, a fake catalog store, the ledger's
in-memory store.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['CORPUS_CATALOG'] = '1'

from botocore.exceptions import ClientError  # noqa: E402

import insights_ledger as il  # noqa: E402
from migration import corpus_catalog as cc  # noqa: E402
from migration import corpus_catalog_sync as ccs  # noqa: E402

PASS, FAIL = 0, []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name}  {detail}')


def _err(code, status=404):
    return ClientError({'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}, 'Op')


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, body):
        return hashlib.md5(body).hexdigest()

    def get_object(self, Bucket, Key, **_kw):
        if Key not in self.objs:
            raise _err('NoSuchKey')
        return {'Body': _Body(self.objs[Key]), 'ETag': '"%s"' % self._etag(self.objs[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _err('404')
        return {'ETag': '"%s"' % self._etag(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None, **_kw):
        cur = self.objs.get(Key)
        if IfNoneMatch == '*' and cur is not None:
            raise _err('PreconditionFailed', 412)
        if IfMatch is not None and (cur is None or self._etag(cur) != IfMatch):
            raise _err('PreconditionFailed', 412)
        self.objs[Key] = Body if isinstance(Body, bytes) else Body.encode('utf-8')
        return {}

    def list_objects_v2(self, Bucket, Prefix='', **_kw):
        return {'Contents': [{'Key': k, 'ETag': '"%s"' % self._etag(v)} for k, v in sorted(self.objs.items())
                             if k.startswith(Prefix)], 'IsTruncated': False}


SUBJECTS = ['Prime Video', 'Martin Lawrence', 'Target', 'Mindcage', 'Moonfall', 'Dogma', 'Tyler Perry', 'MovieSphere+']


def values_on(subject):
    page = cc.load_subject_page(cc.subject_key(subject), force=True) or {}
    return sorted({il.value_tokens(f.get('value')) for f in page.get('facts') or [] if f.get('kind') == 'stated_number'} - {''})


def thread(fake, user, tid, turns):
    fake.objs[f'system/synth_chat_threads/{user}/{tid}.json'] = json.dumps(turns).encode('utf-8')


def sweep():
    return ccs.backfill_threads(dict(cc.load_sources()))


def main():
    il.use_test_ledger({})
    fake = FakeS3()
    cc.set_client(fake)
    for s in SUBJECTS:   # every one of these has a profile in the catalog already
        cc.upsert_source('profile', s.replace(' ', '_') + '.csv', s,
                         [cc.make_fact('profile', 'sample_size', 'Sample', 31207, 'people', source_key=s.replace(' ', '_') + '.csv')])

    print('1. a read about a film that names its platform and an actor files under the film only')
    thread(fake, 'seat_a', 't1', [
        {'role': 'user', 'text': 'This is for the title Mindcage on Prime Video, Nov 1 to Nov 9.'},
        {'role': 'agent', 'meta': {'kind': 'read', 'subject': 'Mindcage'},
         'text': 'Mindcage drew 310,671 unique US viewers on Prime Video. Martin Lawrence, who stars in it, carries a '
                 '41,287-person fan base on YouTube. Most of the audience sits in the target age band at 62.5%.'}])
    n = sweep()
    check('with its bound subject: the film page holds the figures', n == 1 and {'310671', '41287', '62.5'} <= set(values_on('Mindcage')),
          f'{n} {values_on("Mindcage")}')
    check('the platform, the actor and the common word get nothing',
          not values_on('Prime Video') and not values_on('Martin Lawrence') and not values_on('Target'),
          f"{values_on('Prime Video')} {values_on('Martin Lawrence')} {values_on('Target')}")
    thread(fake, 'seat_a', 't2', [
        {'role': 'user', 'text': 'how many people watched Mindcage on Prime Video?'},
        {'role': 'agent', 'text': 'Mindcage, starring Martin Lawrence, drew 310,672 unique US viewers on Prime Video in its first week.'}])
    sweep()
    check('without one: the name that opens the figure is the film',
          '310672' in values_on('Mindcage') and not values_on('Prime Video') and not values_on('Martin Lawrence'),
          f"{values_on('Mindcage')} {values_on('Prime Video')} {values_on('Martin Lawrence')}")

    print('2. a name inside a title is not the subject')
    thread(fake, 'seat_a', 't3', [
        {'role': 'user', 'text': "let's now do the same thing but for Tyler Perry's Meet the Browns"},
        {'role': 'agent', 'meta': {'kind': 'read', 'subject': "Tyler Perry's Meet the Browns"},
         'text': "Tyler Perry's Meet the Browns drew 80,161 unique US viewers on YouTube Movies & TV."}])
    sweep()
    check("the film, not Tyler Perry", '80161' in values_on("Tyler Perry's Meet the Browns") and not values_on('Tyler Perry'),
          f"{values_on('Tyler Perry')}")

    print('3. a genuine comparison files under both')
    thread(fake, 'seat_b', 't4', [
        {'role': 'user', 'text': 'Moonfall vs Dogma on YouTube'},
        {'role': 'agent', 'meta': {'kind': 'read', 'subject': 'Moonfall vs Dogma'},
         'text': 'Moonfall drew 136,657 unique US viewers. Dogma drew 34,824.'}])
    sweep()
    check('a bound X vs Y files each figure under the title its sentence names',
          '136657' in values_on('Moonfall') and '34824' in values_on('Dogma')
          and '34824' not in values_on('Moonfall') and '136657' not in values_on('Dogma'),
          f"{values_on('Moonfall')} {values_on('Dogma')}")
    thread(fake, 'seat_b', 't5', [
        {'role': 'user', 'text': 'compare Moonfall and Dogma on MovieSphere+'},
        {'role': 'agent', 'text': 'Moonfall drew 41,312 unique US viewers and Dogma drew 29,463 over the same dates on MovieSphere+.'}])
    sweep()
    check('a question comparing two titles files under both',
          {'41312', '29463'} <= set(values_on('Moonfall')) and {'41312', '29463'} <= set(values_on('Dogma')),
          f"{values_on('Moonfall')} {values_on('Dogma')}")
    check('and never under the platform named in passing', not values_on('MovieSphere+'), str(values_on('MovieSphere+')))

    print('4. what is never filed')
    thread(fake, 'seat_c', 't6', [
        {'role': 'user', 'text': 'Mindcage again?'},
        {'role': 'agent', 'meta': {'kind': 'read', 'subject': 'Mindcage', 'retired': {'by': 'operator'}},
         'text': 'Mindcage drew 999,901 unique US viewers.'},
        {'role': 'agent', 'meta': {'kind': 'read', 'subject': 'Mindcage', 'withdrawn': True},
         'text': 'Mindcage drew 999,902 unique US viewers.'},
        {'role': 'user', 'text': 'what about the week?'},
        {'role': 'agent', 'text': 'The last week added 4,117 new viewers on Prime Video.'}])
    sweep()
    check('a retired or withdrawn turn', not {'999901', '999902'} & set(values_on('Mindcage')), str(values_on('Mindcage')))
    check('a turn about nothing the catalog can name', '4117' not in values_on('Prime Video') and '4117' not in values_on('Mindcage'))

    print('5. the write hook files under the subject it is given')
    key = cc.record_answer('Dogma', 'Dogma reached 52,296 plays in the window.', user='seat_d', thread_id='t7')
    check('as before', bool(key) and '52296' in values_on('Dogma'))

    print('6. unfile_source')
    text = 'Forever My Girl drew 33,631 unique US viewers on Prime Video.'
    src = f"thread/seat_e/t8/{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"
    facts = cc.facts_from_answer('Prime Video', text, user='seat_e', thread_id='t8')
    for f in facts:
        f['source']['key'] = src
    cc.upsert_source('chat', src, 'Prime Video', facts)
    cc.record_answer('Forever My Girl', text, user='seat_e', thread_id='t8')
    removed = cc.unfile_source(src, cc.subject_key('Prime Video'))
    check('takes it off the page it does not belong on', removed >= 1 and '33631' not in values_on('Prime Video'), str(removed))
    check('leaves it where it belongs, never marked retired',
          '33631' in values_on('Forever My Girl') and not cc.is_retired_source(src))

    src_txt = Path(__file__).read_text(encoding='utf-8')
    check('no long dash in this file', '\u2014' not in src_txt and '\u2013' not in src_txt)
    cc.set_client(None)
    print(f'\n{PASS} passed, {len(FAIL)} failed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
