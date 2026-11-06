#!/usr/bin/env python3
"""Focus Group IQ: content in the room (Jenna 2026-11-05).

"you should be able to upload content into the focus group (a video,
image, mp3) and it should assess it for you the same way the pilot
assesses video. so that you could then ask quesions about the content
as an a/b testing kind of thing."

Held here:
  * intake of each type (video, image, audio, a URL) stores the file
    under digital-twins/assets/<twin>/<sid>/ with a label and processes
    it to ready: frames and a transcript for video, the image and a plain
    description for an image, a transcript and a voice / music / tone
    description for audio
  * a failed processing reads plain copy, never a stack or 'processing'
    forever
  * the assessment read has the fields (headline, the appeal as the asset
    implies, clarity, what stood out and fell flat, the lean by segment,
    the voices), no banned words, people sized to what the room stands
    for, and lands as a question-like record (kind asset_read) headed
    'What the room made of <label>'
  * later questions carry the content as context for the seats; naming A
    targets A; 'which do you prefer, A or B?' runs the preference split
    (A / B / neither / no difference) with the seats mirroring it
  * asset reads and the A/B count toward the group's question cap as one
    question each
  * the room export has a page for the asset read and a page for the A/B;
    the report page is headed the same way; the team notice lists the
    content; Prometheus routes 'test this image in the room'

Hermetic: a fake store, a fake transcriber, a fake vision reader, a fake
model; REGRESSION_TEST_MODE never calls a model, Whisper or AWS.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_room_assets.py
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_assets as A      # noqa: E402
from migration import digital_twin_panel as P       # noqa: E402
from migration import digital_twin_pricing as PR    # noqa: E402
from migration import digital_twin_room as ROOM     # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_room_seats_mirror_count import _pvalley  # noqa: E402

BANNED = ('synth', 'synthetic', 'modeled', 'estimated', 'calibrat', 'anchored', 'benchmark', 'nielsen', 'persona', 'twin', 'claude', 'pipeline', 'as aired', '\u2014', '\u2013')


class _Body:
    def __init__(self, raw):
        self._b = io.BytesIO(raw)

    def read(self, n=-1):
        return self._b.read(n)


class FakeS3:
    def __init__(self):
        self.objs, self.meta = {}, {}

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key]), 'ContentType': self.meta.get(Key, {}).get('ContentType', 'application/octet-stream'),
                'ContentLength': len(self.objs[Key]), 'ContentEncoding': self.meta.get(Key, {}).get('ContentEncoding')}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')
        self.meta[Key] = {k: v for k, v in kw.items() if k in ('ContentType', 'ContentEncoding')}

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'ContentLength': len(self.objs[Key])}

    def copy_object(self, Bucket, CopySource, Key, **kw):
        src = CopySource['Key']
        if src not in self.objs:
            raise KeyError(src)
        self.objs[Key] = self.objs[src]
        self.meta[Key] = dict(self.meta.get(src, {}))

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)


def _png(w=8, h=8):
    """A tiny valid PNG."""
    raw = b''.join(b'\x00' + bytes([200, 60, 220] * w) for _ in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def _wav(seconds=2):
    import wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b'\x00\x01' * 8000 * seconds)
    return buf.getvalue()


def _mp4():
    return b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 2048


TRANSCRIPT = [{'t': 0.0, 'end': 2.0, 'text': 'Some nights the Pynk is the only place that feels like home.'},
              {'t': 2.0, 'end': 4.5, 'text': 'New season, new money, same family.'}]
SEEN = {'persona': [], 'moderator': [], 'vision': [], 'transcribe': []}


def fake_vision(system, blocks):
    SEEN['vision'].append(system[:40])
    if 'one image' in system:
        return {'description': 'A neon-lit club doorway at night with a woman in a sequined jacket looking back over her shoulder; the words NEW SEASON across the top.',
                'eye_lands_on': 'the sequined jacket', 'text_on_it': 'NEW SEASON', 'what_it_is': 'key art', 'implied_action': 'watch', 'mood': 'electric, defiant'}
    return None


def fake_transcriber(local_path, s3_key, kind):
    SEEN['transcribe'].append((kind, os.path.basename(s3_key)))
    return list(TRANSCRIPT)


def fake_model(role, system, user, **kw):
    d = json.loads(user)
    if role == 'moderator' and 'content' in d and 'headline' in d:
        SEEN['moderator'].append(d)
        segs = [{'segment': s, 'line': f"{s} lean toward it on the music and the faces they know."} for s in (d.get('audience') or {}).get('segments') or []]
        return {'headline': 'Most of the room would watch on the strength of it.',
                'read': 'The room leaned in. The neon doorway and the line about family did the work; the title card read as a promise to people who already love the Pynk. The people who held back wanted a face they knew in the first two seconds. Nobody judged it by its length.',
                'chat': 'The room leaned in. The doorway and the family line did the work.', 'about': 'a new season at the club', 'for_whom': 'people who already watch', 'clear': 'clear',
                'stood_out': ['the neon doorway', 'the line about family'], 'fell_flat': ['no familiar face up front'], 'by_segment': segs, 'voices': [1, 2, 3],
                'probes_next': ['What would make you share it?']}
    if role == 'moderator' and 'frames_in_order' in d:
        return {'description': 'A thirty-second teaser: a neon doorway, a woman counting bills, a slow push on a stage, the title card.', 'what_it_is': 'trailer', 'about': 'a new season at the club',
                'for_whom': 'people who already watch', 'beats': ['the doorway', 'the money', 'the stage', 'the title card'], 'strongest_moment': 'the title card over the family line', 'implied_action': 'watch', 'mood': 'electric'}
    if role == 'moderator' and 'transcript_with_timings' in d:
        return {'description': 'A warm, unhurried voice over a slow bass line; it reads as a promo spot.', 'what_it_is': 'ad', 'about': 'a new season', 'for_whom': 'people who already watch',
                'voice': 'warm, low', 'music': 'slow bass', 'tone': 'intimate', 'implied_action': 'listen'}
    if role == 'moderator' and 'question' in d:
        return {'read': 'Most of the room picked A for the face in the doorway; B read as generic to the people who chose it least. The seats that said no difference wanted to see both in motion.',
                'chat': 'Most of the room picked A for the face in the doorway.', 'voices': [1, 2], 'probes_next': ['What would make B the pick?'], 'facts_used': []}
    if role == 'persona' and 'what_you_were_shown' in d and 'your_answer' not in d:
        SEEN['persona'].append(d)
        shown = d['what_you_were_shown']
        if isinstance(shown, list):
            if len(shown) >= 2 and len(d.get('options') or []) >= 4:
                return {'option': 'a', 'line': f"{shown[0].get('label')} for me, the doorway shot. {shown[1].get('label')} looks like every other poster.", 'confidence': 4, 'reason': 'the doorway'}
            return {'option': 'a', 'line': f"I keep thinking about {shown[0].get('label')}, that doorway. I would press play tonight.", 'confidence': 4, 'reason': 'the doorway'}
        return {'option': 'a', 'line': 'That doorway shot is the whole show for me. I would watch it the night it drops.', 'stood_out': 'the doorway', 'fell_flat': 'the title card is small',
                'about': 'a new season at the club', 'for_whom': 'people like me who never left', 'confidence': 5, 'reason': 'the doorway'}
    if role == 'persona' and 'your_answer' in d:
        return {'line': f"Honestly, {d['your_answer']['label']}. I would need more than a doorway.", 'confidence': 3, 'reason': 'wants more'}
    if role == 'persona':
        return {'option': 'a', 'line': 'Yes, I would.', 'confidence': 3, 'reason': 'habit'}
    return None


def _room(sid='room-assets-1', in_room=6):
    ROOM.clear_room_caches()
    twin = _pvalley()
    sess = P.open_session([twin], in_room=in_room, title='P-Valley', session_id=sid, created_by='jenna')
    sess['twin_keys'] = ['pv']
    PR.record_tier(sess, 20, 'individual')
    return twin, sess


def _models():
    return ROOM.Models(model_fn=fake_model)


class Setup(unittest.TestCase):
    def setUp(self):
        for k in SEEN:
            SEEN[k].clear()
        A.set_transcriber_fn(fake_transcriber)
        A.set_vision_fn(fake_vision)
        A.set_process_hook(None)
        self.s3 = FakeS3()

    def tearDown(self):
        A.set_transcriber_fn(None)
        A.set_vision_fn(None)
        A.set_process_hook(None)

    def assertClean(self, text, where=''):
        low = str(text or '').lower()
        for b in BANNED:
            self.assertNotIn(b, low, f"{where}: {b!r} in {text[:160]!r}")
        self.assertEqual(A.banned(str(text or '')), [], where)


class Intake(Setup):
    def test_kinds_and_labels(self):
        self.assertEqual((A.kind_for('a.mp4'), A.kind_for('b.MOV'), A.kind_for('c.webm'), A.kind_for('d.png'), A.kind_for('e.jpg'), A.kind_for('f.webp'), A.kind_for('g.mp3'), A.kind_for('h.m4a'), A.kind_for('i.wav')),
                         ('video', 'video', 'video', 'image', 'image', 'image', 'audio', 'audio', 'audio'))
        self.assertIsNone(A.kind_for('deck.pptx'))
        self.assertEqual(A.kind_for('', 'image/jpeg'), 'image')
        twin, sess = _room()
        self.assertEqual(A.next_label(sess), 'A')
        a, err = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='art.png', content_type='image/png', label='', added_by='jenna')
        self.assertIsNone(err)
        self.assertEqual((a['label'], a['kind'], a['status']), ('A', 'image', 'processing'))
        self.assertTrue(a['s3_key'].startswith('digital-twins/assets/pv/room-assets-1/') and a['s3_key'].endswith('.png'), a['s3_key'])
        self.assertIn(a['s3_key'], self.s3.objs)
        b, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_wav(), filename='spot.wav', label='b', added_by='jenna')
        self.assertEqual(b['label'], 'B')
        c, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_mp4(), filename='teaser.mp4', label='Teaser two', added_by='jenna')
        self.assertEqual(c['label'], 'Teaser two')
        self.assertEqual(A.next_label(sess), 'C')
        pub = A.public_asset(a)
        self.assertNotIn('s3_key', pub)
        self.assertTrue(pub['has_file'])
        self.assertEqual(len(A.session_assets(sess)), 3)
        self.assertEqual(A.store_upload(self.s3, sess, twin_key='pv', raw=b'x' * 100, filename='notes.txt')[1][:5], 'Use a')
        self.assertEqual(A.store_upload(self.s3, sess, twin_key='pv', raw=b'', filename='a.png')[1], 'That file is empty.')

    def test_url_intake_refuses_pages_and_reads_files(self):
        twin, sess = _room()
        # REGRESSION_TEST_MODE never reaches the network: the URL path lands plain copy
        a, err = A.store_url(self.s3, sess, twin_key='pv', url='https://www.youtube.com/watch?v=abc', label='A')
        self.assertIsNone(a)
        self.assertTrue(err and 'link' in err.lower(), err)
        self.assertEqual(A.chat_intake_url('see https://cdn.example.com/k/teaser.mp4 and https://youtu.be/x'), 'https://cdn.example.com/k/teaser.mp4')
        self.assertEqual(A.fetch_url('not a url')[2], 'Paste a direct link to the file (https://...).')

    def test_processing_each_kind_to_ready(self):
        twin, sess = _room()
        tbk = {'pv': twin}
        img, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='art.png', label='A')
        aud, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_wav(), filename='spot.wav', label='B')
        vid, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_mp4(), filename='teaser.mp4', label='C')
        payload = dict(twin, panel_sessions=[sess])
        for a in (img, aud, vid):
            got = A.process_asset(self.s3, 'pv', sess['id'], a['id'], payload=payload, twins_by_key=tbk, models=_models(), save=False)
            self.assertEqual(got['status'], 'ready', (a['label'], got.get('error'), got.get('note')))
            self.assertTrue(got.get('read_id'), 'the room read it as soon as it was ready')
        # image: the image itself and a plain description, a thumbnail beside it
        ti = img['taken_in']
        self.assertIn('neon-lit club doorway', ti['description'])
        self.assertEqual((ti['what_it_is'], ti['implied_action']), ('key art', 'watch'))
        self.assertTrue(img.get('thumb_key') in self.s3.objs, 'a thumbnail beside the image')
        # audio: a transcript plus the voice, the music and the tone
        ti = aud['taken_in']
        self.assertEqual(ti['transcribed_by'], 'test')
        self.assertIn('Pynk', ti['transcript_excerpt'])
        self.assertEqual((ti['voice'], ti['music'], ti['tone'], ti['implied_action']), ('warm, low', 'slow bass', 'intimate', 'listen'))
        self.assertGreater(float(ti['duration_s'] or 0), 1.0)
        # video: the transcript of the audio track (frames need ffmpeg; without it the read says so and still lands)
        ti = vid['taken_in']
        self.assertIn('Pynk', ti['transcript_excerpt'])
        self.assertIn(('video', os.path.basename(vid['s3_key'])), SEEN['transcribe'])
        self.assertEqual(ti['what_it_is'], 'trailer')
        self.assertEqual(len([q for q in sess['questions'] if q['kind'] == 'asset_read']), 3)
        self.assertEqual([q['q'] for q in sess['questions']], ['What the room made of A', 'What the room made of B', 'What the room made of C'])
        self.assertEqual([a['label'] for a in A.ready_assets(sess)], ['A', 'B', 'C'])

    def test_a_failed_processing_reads_plain_copy(self):
        twin, sess = _room()
        a, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='art.png', label='A')
        self.s3.objs.pop(a['s3_key'])         # the file is gone before the read
        got = A.process_asset(self.s3, 'pv', sess['id'], a['id'], payload=dict(twin, panel_sessions=[sess]), twins_by_key={'pv': twin}, models=_models(), save=False)
        self.assertEqual(got['status'], 'failed')
        self.assertEqual(got['note'], A.FAILED_LINE)
        self.assertClean(got['note'], 'failed note')
        self.assertNotIn('Traceback', got['note'])
        self.assertEqual(sess['questions'], [], 'no read for a file the room could not take in')
        pub = A.public_asset(got)
        self.assertEqual((pub['status'], pub['note']), ('failed', A.FAILED_LINE))
        # a thread that died leaves 'processing' behind: the sweep reads it as failed with plain copy
        b, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='art2.png', label='B')
        b['updated_at'] = '2026-11-05T00:00:00Z'
        self.assertTrue(A.sweep_assets(sess))
        self.assertEqual(b['status'], 'failed')
        self.assertIn('Add it again', b['note'])

    def test_landing_on_the_stored_twin_keeps_a_question_that_arrived_meanwhile(self):
        from migration import digital_twins as DT
        twin, sess = _room()
        payload = dict(twin, panel_sessions=[sess])
        key = 'digital-twins/pv_test.json.gz'
        DT.save_twin(self.s3, key, payload)
        a, _ = A.store_upload(self.s3, sess, twin_key=key, raw=_png(), filename='art.png', label='A')
        DT.save_twin(self.s3, key, payload)
        # a question lands on the stored room while the read runs
        stored = DT.load_twin(self.s3, key)
        stored['panel_sessions'][0]['questions'].append({'id': 'q1', 'q': 'Would you watch a new season?', 'kind': 'yes_no', 'stage': 'done', 'distribution': []})
        DT.save_twin(self.s3, key, stored)
        got = A.process_asset(self.s3, key, sess['id'], a['id'], payload=payload, twins_by_key={key: twin}, models=_models(), save=True)
        self.assertEqual(got['status'], 'ready')
        after = DT.load_twin(self.s3, key)['panel_sessions'][0]
        self.assertEqual([q['q'] for q in after['questions']], ['Would you watch a new season?', 'What the room made of A'])
        self.assertEqual(after['questions'][1]['id'], 'q2', 'the read takes the next id, never a duplicate')
        self.assertEqual(after['assets'][0]['status'], 'ready')
        self.assertEqual(after['assets'][0]['read_id'], 'q2')


class Assessment(Setup):
    def _ready_image(self, label='A', sess=None, twin=None):
        if sess is None:
            twin, sess = _room()
        a, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename=f"{label}.png", label=label)
        A.process_asset(self.s3, 'pv', sess['id'], a['id'], payload=dict(twin, panel_sessions=[sess]), twins_by_key={'pv': twin}, models=_models(), save=False)
        return twin, sess, a

    def test_the_read_has_the_fields_and_sizes_to_the_room(self):
        twin, sess, a = self._ready_image()
        rec = sess['questions'][-1]
        self.assertEqual(rec['kind'], 'asset_read')
        self.assertEqual(rec['q'], 'What the room made of A')
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual([o['label'] for o in rec['options']], ['Would watch', 'Might watch', 'Would not watch'], 'the appeal as the asset implies')
        self.assertTrue(rec['headline'])
        self.assertTrue(rec['read'])
        self.assertEqual(rec['clarity']['about'], 'a new season at the club')
        self.assertEqual(rec['clarity']['for_whom'], 'people who already watch')
        self.assertEqual(rec['stood_out'], ['the neon doorway', 'the line about family'])
        self.assertEqual(rec['fell_flat'], ['no familiar face up front'])
        self.assertTrue(rec['lean_by_segment'] and rec['lean_by_segment'][0]['line'])
        self.assertGreaterEqual(len(rec['voices']), 3)
        self.assertEqual(len(rec['answers']), 6, 'every seat reacted')
        self.assertTrue(all(x.get('stood_out') for x in rec['answers']))
        self.assertEqual(sum(t['seats'] for t in rec['tally']), 6, 'the seats mirror the count')
        people = A.represented_people(sess, twin)
        self.assertEqual(people, P.room_total_people(sess['group']))
        self.assertEqual(rec['people_total'], people)
        self.assertEqual(sum(int(r['people']) for r in rec['distribution']), people, 'people counts add up to what the room stands for')
        self.assertEqual(people, int(twin['meta']['size_us']), 'an uncut room stands for the whole audience')
        cut = dict(sess, group=dict(sess['group'], sizing={'people': 1_234_567, 'twin_people': int(twin['meta']['size_us']), 'is_cut': True, 'cuts_n': 1}))
        self.assertEqual(A.represented_people(cut, twin), 1_234_567, 'a room sized to its cuts stands for the represented people, never the full audience')
        self.assertEqual(rec['headline_people'], max(int(r['people']) for r in rec['distribution']))
        for k in ('read', 'read_short', 'headline', 'framing'):
            self.assertClean(rec[k], k)
        for v in rec['voices']:
            self.assertClean(v['line'], 'voice')
        self.assertIn('What would make you', ' '.join(rec['probes_next']))
        self.assertIn('Who is this for?', rec['probes_next'])
        # every seat took the content in: the body it was handed carried what_you_were_shown with the description
        self.assertEqual(len(SEEN['persona']), 6)
        self.assertTrue(all('neon-lit' in json.dumps(b['what_you_were_shown']) for b in SEEN['persona']))
        self.assertEqual(SEEN['persona'][0]['what_you_were_shown']['it_asks_you_to'], 'watch')
        self.assertIn('Never judge it by how long it is', A.ASSET_SEAT_SYSTEM)
        self.assertIn('Never let the length of the piece stand in for its appeal', A.ASSET_READ_SYSTEM)

    def test_the_read_passes_the_guard_even_when_the_model_names_a_source(self):
        def leaky(role, system, user, **kw):
            d = json.loads(user)
            if role == 'moderator' and 'content' in d and 'headline' in d:
                out = fake_model(role, system, user, **kw)
                out['read'] = 'Calibrated to the first week as aired, the room leaned in. ' + out['read'] + ' Per Nielsen the premiere drew 4.1M people.'
                out['headline'] = 'Anchored to the premiere, most would watch.'
                return out
            return fake_model(role, system, user, **kw)
        twin, sess = _room()
        a, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='A.png', label='A')
        A.process_asset(self.s3, 'pv', sess['id'], a['id'], payload=dict(twin, panel_sessions=[sess]), twins_by_key={'pv': twin}, models=ROOM.Models(model_fn=leaky), save=False)
        rec = sess['questions'][-1]
        self.assertClean(rec['read'], 'read')
        self.assertClean(rec.get('headline_read') or '', 'headline_read')
        self.assertNotIn('Nielsen', rec['read'])
        self.assertNotIn('alibrated', rec['read'])
        self.assertIn('The room leaned in', rec['read'])

    def test_later_questions_carry_the_content(self):
        twin, sess, a = self._ready_image()
        SEEN['persona'].clear()
        rec = ROOM.ask_room(sess, {'pv': twin}, 'Would you click this thumbnail?', s3=None, twin_key='pv', run_async=False, model_fn=fake_model)
        self.assertEqual(rec['stage'], 'done')
        self.assertTrue(rec['stimulus'].get('assets'))
        self.assertTrue(rec['stimulus'].get('asset_focus'), 'a question about this content runs on the content alone')
        self.assertEqual(rec['trail']['content'], {'labels': ['A'], 'focus': True, 'ab': False})
        self.assertEqual(rec['trail'].get('anchor_status'), 'prior', 'no desk research on the content itself')
        self.assertEqual(len(SEEN['persona']), 6)
        for body in SEEN['persona']:
            shown = body['what_you_were_shown']
            self.assertIsInstance(shown, list)
            self.assertEqual(shown[0]['label'], 'A')
            self.assertIn('neon-lit', shown[0]['description'])
        self.assertIn('what_the_room_was_shown', rec['trail']['facts_brief'])
        # a general question still carries it as context, with the desks on
        SEEN['persona'].clear()
        rec2 = ROOM.ask_room(sess, {'pv': twin}, 'Would you watch a new season on Starz?', s3=None, twin_key='pv', run_async=False, model_fn=fake_model)
        self.assertTrue(rec2['stimulus'].get('assets'))
        self.assertFalse(rec2['stimulus'].get('asset_focus'))
        self.assertTrue(all(b.get('what_you_were_shown') for b in SEEN['persona']))
        self.assertEqual(PR.questions_asked(sess), 3, 'the asset read and two questions')

    def test_a_or_b_runs_the_preference_split(self):
        twin, sess, a = self._ready_image('A')
        twin, sess, b = self._ready_image('B', sess=sess, twin=twin)
        self.assertEqual(PR.questions_asked(sess), 2)
        self.assertEqual(sess['questions'][-1]['probes_next'][1], 'Which do you prefer, A or B?')
        SEEN['persona'].clear()
        rec = ROOM.ask_room(sess, {'pv': twin}, 'Which do you prefer, A or B?', s3=None, twin_key='pv', run_async=False, model_fn=fake_model)
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual(rec['kind'], 'ab')
        self.assertEqual([o['label'] for o in rec['options']], ['A', 'B', 'Neither', 'No difference'])
        self.assertEqual(rec['stimulus']['ab_assets']['labels'], ['A', 'B'])
        self.assertTrue(A.is_ab_read(rec))
        self.assertEqual(sum(o['seats_n'] for o in rec['options']), 6, 'the seats mirror the split')
        self.assertAlmostEqual(sum(float(o['pct']) for o in rec['options']), 100.0, delta=0.2)
        self.assertEqual(sum(int(o['people']) for o in rec['options']), A.represented_people(sess, twin))
        self.assertEqual(rec['by_segment'][0]['segment'], sess['group']['segments'][0]['label'])
        self.assertTrue(all(len(bd['what_you_were_shown']) == 2 for bd in SEEN['persona']), 'both pieces in front of every seat')
        self.assertIn('Which do you prefer, A or B?', rec['framing'])
        self.assertClean(rec['read'], 'ab read')
        self.assertEqual(PR.questions_asked(sess), 3, 'the comparison is one question on the cap')
        # 'A or B?' bare also compares; naming only B targets B
        stim, kind, opts = A.attach_question_assets(sess, 'A or B?', None, None, None)
        self.assertEqual((kind, opts), ('ab', ['A', 'B']))
        stim, kind, opts = A.attach_question_assets(sess, 'Does the music fit B?', None, None, None)
        self.assertEqual([v['label'] for v in stim['assets']], ['B'])
        self.assertTrue(stim['asset_focus'])
        stim, kind, opts = A.attach_question_assets(sess, 'Would you pay $12 a month?', None, None, None)
        self.assertEqual(len(stim['assets']), 2)
        self.assertFalse(stim.get('asset_focus'))
        # a label that is a letter does not fire on ordinary words
        stim, kind, opts = A.attach_question_assets(sess, 'Is it a show you would binge?', None, None, None)
        self.assertFalse(stim.get('asset_focus'))


class Cap(Setup):
    def test_asset_reads_count_toward_the_cap(self):
        twin, sess = _room()
        self.assertTrue(PR.ASSET_READ_COUNTS_AS_QUESTION)
        sess['questions'] = [{'id': f"q{i}", 'q': 'x', 'kind': 'open'} for i in range(1, 19)]
        self.assertEqual(PR.question_gate(sess, 'individual')[0], 'ok')
        a, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='A.png', label='A')
        A.process_asset(self.s3, 'pv', sess['id'], a['id'], payload=dict(twin, panel_sessions=[sess]), twins_by_key={'pv': twin}, models=_models(), save=False)
        self.assertEqual((PR.questions_asked(sess), PR.asset_reads(sess)), (19, 1))
        b, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename='B.png', label='B')
        A.process_asset(self.s3, 'pv', sess['id'], b['id'], payload=dict(twin, panel_sessions=[sess]), twins_by_key={'pv': twin}, models=_models(), save=False)
        self.assertEqual(PR.questions_asked(sess), 20)
        verdict, offer = PR.question_gate(sess, 'individual')
        self.assertEqual(verdict, 'offer', 'the twenty-first (the A/B) waits for the upgrade, as any question would')
        self.assertEqual(PR.public_fields(sess, 'individual')['asset_reads'], 2)


class Pages(Setup):
    def _room_with_reads(self):
        twin, sess = _room()
        for lab in ('A', 'B'):
            a, _ = A.store_upload(self.s3, sess, twin_key='pv', raw=_png(), filename=f"{lab}.png", label=lab)
            A.process_asset(self.s3, 'pv', sess['id'], a['id'], payload=dict(twin, panel_sessions=[sess]), twins_by_key={'pv': twin}, models=_models(), save=False)
        ROOM.ask_room(sess, {'pv': twin}, 'Which do you prefer, A or B?', s3=None, twin_key='pv', run_async=False, model_fn=fake_model)
        return twin, sess

    def test_export_pages_for_the_asset_read_and_the_a_b(self):
        from migration import digital_twin_exports as X
        twin, sess = self._room_with_reads()
        payload = dict(twin, panel_sessions=[sess])
        pdf = X.build_panel_pdf(payload, sess, asset_image_fn=lambda asset, n: _png(16, 16) if n == 0 else None)
        self.assertTrue(pdf.startswith(b'%PDF'))
        try:
            from pypdf import PdfReader
        except Exception:
            self.skipTest('pypdf not installed')
        r = PdfReader(io.BytesIO(pdf))
        texts = [(p.extract_text() or '').replace('\n', ' ') for p in r.pages]
        self.assertGreaterEqual(len(r.pages), 4, 'the room, two content reads, the A/B (and whatever closes the file)')

        def page_with(want):
            hits = [i for i, t in enumerate(texts) if want in t]
            self.assertTrue(hits, f"no page carries {want!r}: {[t[:60] for t in texts]}")
            return hits[0]
        pa = page_with('What the room made of A.')
        self.assertIn('question 1, content', texts[pa].lower())
        self.assertIn('What stood out', texts[pa])
        pb = page_with('What the room made of B.')
        self.assertIn('question 2, content', texts[pb].lower())
        pab = page_with('Which do you prefer, A or B?')
        self.assertIn('question 3, a or b', texts[pab].lower())
        self.assertIn('No difference', texts[pab])
        self.assertEqual((pa, pb, pab), (1, 2, 3), 'the content reads and the A/B print in the order they were asked, right after the room')
        self.assertIn('/Image', str(r.pages[pa].get('/Resources') or ''), 'the image sits on the page')
        for i in (pa, pb, pab):
            self.assertClean(texts[i], 'export page')
        self.assertEqual(A.page_header(sess['questions'][0]), 'What the room made of A.')
        self.assertEqual(A.page_eyebrow(sess['questions'][0]), 'Content')
        self.assertEqual(A.page_eyebrow(sess['questions'][2]), 'A or B')

    def test_report_page_is_headed_the_same_way(self):
        from migration import digital_twin_report as R
        twin, sess = self._room_with_reads()
        pg = R._question_page(sess['questions'][0], sess, True, n=1)
        self.assertEqual(pg['header'], 'What the room made of A.')
        self.assertEqual(pg['eyebrow'], 'Question 1, content')
        self.assertTrue(any(k.startswith('What stood out') for k in pg['knew']))
        pg3 = R._question_page(sess['questions'][2], sess, False, n=3)
        self.assertEqual(pg3['eyebrow'], 'Question 3, A or B')
        self.assertEqual(pg3['header'], 'Which do you prefer, A or B?')
        csv = P.session_csv(sess)
        self.assertIn('What the room made of A', csv)

    def test_the_notice_lists_the_content(self):
        from prometheus import twin_notify as TN
        twin, sess = self._room_with_reads()
        payload = dict(twin, panel_sessions=[sess])
        body = TN._room_body(payload, sess, TN.seat('jenna', s3=self.s3), 'dashboard', s3=self.s3)
        self.assertIn('Content in the room: A (image, A.png); B (image, B.png)', body)
        self.assertEqual(TN.banned_tokens(body), [], body)
        twin2, sess2 = _room('room-assets-2')
        self.assertNotIn('Content in the room', TN._room_body(dict(twin2, panel_sessions=[sess2]), sess2, TN.seat('jenna', s3=self.s3), 'dashboard', s3=self.s3))


class Chat(Setup):
    def test_prometheus_routes_the_ask(self):
        from prometheus import digital_twin_lane as L
        self.assertTrue(L.asset_intake_intent('test this image in the room'))
        self.assertTrue(L.asset_intake_intent('put this trailer to the group as B'))
        self.assertTrue(L.asset_intake_intent('show them this thumbnail https://cdn.example.com/t.png'))
        self.assertFalse(L.asset_intake_intent('ask them would you watch a new season'))
        self.assertFalse(L.asset_intake_intent('what image does the brand have in the room'))
        self.assertEqual(L._asset_label_in('test this image in the room as B'), 'B')
        self.assertEqual(L._asset_label_in('run this spot past them, call it "Spot two"'), 'Spot two')
        # the hook sits on the panel lane: with no active room the reply says to open one first
        import prometheus.digital_twin_lane as lane
        old, old_s3 = lane.active_session, lane._s3
        lane.active_session = lambda user: None
        lane._s3 = lambda: (self.s3, 'dashboard-inputs')
        try:
            out = lane.run_asset_intake('test this image in the room https://cdn.example.com/t.png', {}, [], {'username': 'jenna'})
        finally:
            lane.active_session, lane._s3 = old, old_s3
        self.assertIn('Open a room first', out['reply'])


class Routes(Setup):
    def test_the_routes_are_registered(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            src = fh.read()
        for want in ("panel/<sid>/assets', methods=['POST']", "panel/<sid>/assets', methods=['GET']", "panel/<sid>/assets/<aid>/image'", "panel/<sid>/assets/<aid>', methods=['DELETE']",
                     "_question_gate(user, payload, sess, {**body", 'A.start_processing(s3, key, sid, asset'):
            self.assertIn(want, src)
        from prometheus import read_recovery as RR
        self.assertIn('room_asset', RR.ENGINE_KINDS)
        self.assertEqual(RR._MODULE_KINDS['room_asset'], 'prometheus.room_asset_job')
        from prometheus import room_asset_job as RJ
        self.assertEqual(RJ.KIND, 'room_asset')
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
        for want in ('function assetsStripHtml(', 'window.dtwPanelAssetAdd', 'window.dtwPanelAssetUrl', 'window.dtwPanelAssetOpen', 'function scheduleAssetPoll(', 'Add content',
                     "'Ask about ' + ready[0].label + ', ' + ready[1].label + ' or both.'", "assetsStripHtml(s, true)", 'asset_read: \'Content read\''):
            self.assertIn(want, html)


if __name__ == '__main__':
    unittest.main(verbosity=2)
