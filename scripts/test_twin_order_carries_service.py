#!/usr/bin/env python3
"""A build ordered from a twin carries the twin's service (2026-11-12).

"Gilmore Girls Hulu viewers" was ordered from a twin on Nov 5 (run
ndxbI2xiVIBa6A). The build spec that reached the engine read:

    name 'Gilmore Girls Hulu viewers', audience_type 'general',
    content_show none, seed_source none, no named platform,
    subject_rows [['SERIES - DRAMA', 'Gilmore Girls Hulu viewers']]

so the engine built a general audience with that name: no title, no
watch placements, no service, and Hulu read 73.5% on a Hulu universe.
The twin's pull sent its brief through the model read alone; the step
that binds a named service on every other order never ran.

Held here: the service the twin's audience definition names reaches the
spec, strict, with the title, the viewers universe and the twin's own
window intact; a definition that names no service is as before.

Hermetic: the pull's own functions with a converter built from the real
spec stamps; no model, no queue, no twin record is read or written.

    python3 scripts/test_twin_order_carries_service.py
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.pop('SYNTH_QUEUE_URL', None)
os.environ.pop('SYNTH_QUEUE_SECRET', None)

from prometheus import named_platforms as N  # noqa: E402
from prometheus import twin_named_pull as NP  # noqa: E402
from prometheus.streamscout_spec import stamp_client_scope  # noqa: E402

SUBJECT = 'Gilmore Girls Hulu viewers'
WINDOW = {'start': '2025-11-05', 'end': '2026-11-05', 'label': 'Trailing 12 months'}
DEMOS = {'GENDER': {'FEMALE': 71.2, 'MALE': 27.9, 'NON-BINARY': 0.9}, 'AGE': {'18-24': 18.1, '25-34': 30.4, '35-44': 24.7, '45-54': 14.2,
                                                                         '55-64': 8.3, '65 OR OLDER': 4.3}}


def model_draft(subject=SUBJECT, category='SERIES - DRAMA'):
    """What the model read came back with on Nov 5: a general audience named as asked."""
    return {'subject': subject, 'brand_category': category, 'audience_type': 'general', 'decision': 'new_build', 'run_avid': True,
            'subject_rows': [[category, subject]], 'tu_demos': DEMOS, 'avid_demos': DEMOS, 'subject_raw_tu': 46839, 'subject_raw_avid': 10705,
            'persona_notes': 'x', 'named_platforms': []}


def _scrub(v, **kw):
    return str(v)


def converter(d):
    """The chat converter's own seed and platform stamps, in its order (prometheus/legacy/chat.py _spec_from_draft)."""
    subject = d['subject']
    spec = {'name': subject, 'brand_category': d.get('brand_category'), 'subject_rows': [tuple(r) for r in d.get('subject_rows') or []],
            'tu_demos': d.get('tu_demos'), 'avid_demos': d.get('avid_demos'), 'subject_raw_tu': d.get('subject_raw_tu'),
            'subject_raw_avid': d.get('subject_raw_avid'), 'audience_type': str(d.get('audience_type') or 'general')}
    if str(d.get('seed_source') or '').strip() == 'content_map':
        spec['seed_source'] = 'content_map'
        spec['content_show'] = str(d.get('content_show') or subject)
    stamp_client_scope(spec, d, _scrub, subject)
    N.stamp_spec(spec, d, _scrub, subject)
    return spec


def body(subject=SUBJECT, draft=None, prompt='Build the twin', **kw):
    return NP.spec_body(draft or model_draft(subject), subject=subject, kind=kw.get('kind', 'ip'), fmt=kw.get('fmt', 'series'), window=WINDOW,
                        run_id='r1', requester={'username': 'maya'}, spec_from_draft=converter, prompt=prompt)


class TheGilmoreGirlsOrder(unittest.TestCase):

    def test_the_spec_as_it_shipped(self):
        # the converter alone, on the model's draft: what reached the engine on Nov 5
        spec = converter(NP._draft_hygiene(model_draft(), SUBJECT, 'ip', 'series', WINDOW))
        self.assertEqual(spec['audience_type'], 'general')
        self.assertNotIn('seed_source', spec)
        self.assertNotIn('content_seed_platforms_named', spec)
        self.assertEqual(spec['subject_rows'], [('SERIES - DRAMA', SUBJECT)])

    def test_the_service_reaches_the_build(self):
        spec = body()['spec']
        self.assertEqual(spec['content_seed_platforms_named'], ['Hulu'])
        self.assertTrue(spec['content_seed_platforms_strict'])            # no other service is ever used in its place
        self.assertEqual(spec['seed_source'], 'content_map')
        self.assertEqual(spec['content_show'], 'Gilmore Girls')           # the title, without the service or the audience noun
        self.assertEqual(spec['audience_type'], 'viewers')
        # the engine pins a service the client named from these two fields (its seed rule); the anchor row is the title
        self.assertIn(('SERIES - DRAMA', 'Gilmore Girls'), [tuple(r) for r in spec['subject_rows']])
        self.assertNotIn(('SERIES - DRAMA', SUBJECT), [tuple(r) for r in spec['subject_rows']])

    def test_the_twins_name_and_window_are_intact(self):
        b = body()
        self.assertEqual(b['spec']['name'], SUBJECT)                       # the twin keeps the requester's words
        self.assertEqual(b['spec']['date_range'], '2025-11-05 TO 2026-11-05')
        self.assertEqual(b['decision'], 'new_build')
        self.assertEqual(b['spec']['addon_cuts'], [])

    def test_every_way_the_definition_names_its_service(self):
        for subject, want, title in (('Gilmore Girls viewers on Hulu', ['Hulu'], 'Gilmore Girls'),
                                     ('Hulu Gilmore Girls Viewers', ['Hulu'], 'Gilmore Girls'),
                                     ('Yellowstone viewers (Peacock)', ['Peacock'], 'Yellowstone'),
                                     ("Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime)", ['HBO Max', 'Amazon Prime Video'],
                                      "Georgie & Mandy's First Marriage"),
                                     ('Stranger Things Netflix viewers', ['Netflix'], 'Stranger Things')):
            spec = body(subject, model_draft(subject))['spec']
            self.assertEqual(spec.get('content_seed_platforms_named'), want, subject)
            self.assertEqual(spec.get('content_show'), title, subject)
            self.assertEqual(spec['name'], subject)
        # named in the words the twin was asked for in, when the subject itself is bare
        spec = body('Gilmore Girls viewers', model_draft('Gilmore Girls viewers'), prompt='Build a twin of the Gilmore Girls viewers on Hulu')['spec']
        self.assertEqual(spec.get('content_seed_platforms_named'), ['Hulu'])

    def test_the_brief_tells_the_read_where_the_audience_watches(self):
        p = NP.audience_prompt(SUBJECT, 'ip', '', WINDOW, fmt='series')
        self.assertIn('only the people who watched or listened on Hulu', p)
        self.assertIn('List exactly Hulu in named_platforms', p)
        self.assertNotIn('watched or listened on', NP.audience_prompt('Gilmore Girls', 'ip', '', WINDOW, fmt='series'))


class NothingElseChanges(unittest.TestCase):

    def test_a_definition_with_no_service_is_as_before(self):
        for subject, cat, kind in (('Gilmore Girls', 'SERIES - DRAMA', 'ip'), ('Gilmore Girls viewers', 'SERIES - DRAMA', 'ip'),
                                   ('Testy McSubject', 'INFLUENCER/CREATOR', 'talent'), ('Nike shoppers', 'APPAREL', 'brand')):
            d = model_draft(subject, cat)
            spec = body(subject, d, kind=kind)['spec']
            self.assertNotIn('content_seed_platforms_named', spec, subject)
            self.assertNotIn('seed_source', spec, subject)
            self.assertEqual(spec['audience_type'], 'general', subject)
            self.assertEqual(spec['subject_rows'], [(cat, subject)], subject)

    def test_a_services_own_audience_is_not_a_title_on_the_service(self):
        for subject, cat in (('Netflix viewers', 'STREAMING/PLATFORM'), ('Hulu viewers', 'STREAMING/PLATFORM'), ('Netflix viewers', 'SERIES - DRAMA')):
            spec = body(subject, model_draft(subject, cat), kind='brand')['spec']
            self.assertNotIn('content_seed_platforms_named', spec, (subject, cat))
            self.assertNotIn('content_show', spec, (subject, cat))

    def test_a_person_or_a_brand_on_a_service_is_not_scoped(self):
        spec = body('Taylor Swift Netflix viewers', model_draft('Taylor Swift Netflix viewers', 'MUSICIAN/BAND'), kind='talent')['spec']
        self.assertNotIn('content_seed_platforms_named', spec)

    def test_a_title_that_carries_a_services_word_keeps_it(self):
        self.assertEqual(N._title_without('Max Headroom', ['HBO Max']), 'Max Headroom')
        self.assertEqual(N._title_without('The Amazon', ['Amazon Prime Video']), 'The Amazon')
        self.assertEqual(N._title_without('Gilmore Girls Hulu', ['Hulu']), 'Gilmore Girls')
        self.assertEqual(N._title_without('Hulu', ['Hulu']), 'Hulu')

    def test_carrying_never_raises(self):
        self.assertEqual(NP.carry_audience_scope(None, SUBJECT), None)
        self.assertEqual(NP.named_scope(None), [])


class TheRealConverter(unittest.TestCase):

    def test_the_chat_converter_stamps_what_this_test_stamps(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        conv = src[src.index("def _spec_from_draft(draft):"):]
        for line in ("if str(draft.get('seed_source') or '').strip() == 'content_map':", "stamp_client_scope(spec, draft, _scrub, subject)",
                     "_stamp_platform_spec(spec, draft, _scrub, subject)"):
            self.assertIn(line, conv, line)
        pull = open(os.path.join(ROOT, 'prometheus', 'twin_named_pull.py'), encoding='utf-8').read()
        self.assertLess(pull.index("d = _draft_hygiene(draft, subject, kind, fmt, window)"), pull.index("d = carry_audience_scope(d, subject, prompt)"))
        self.assertLess(pull.index("d = carry_audience_scope(d, subject, prompt)"), pull.index("spec = dict(spec_from_draft(d) or {})"))


if __name__ == '__main__':
    unittest.main(verbosity=1)
