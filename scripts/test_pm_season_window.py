#!/usr/bin/env python3
"""A season question with no window answers from the widest window held
(Jenna 2026-11-18).

Outlander: Blood of My Blood is held on six trackers: Season 2 on a
four-day premiere, the first 30 days and the season to date; Season 1 on
a four-day premiere, the full run and a later re-run of almost the same
window. Before this rule a plain "Season 2" question was read off the
premiere, the stored figures off the first 30 days, and a plain
"Season 1" question off the re-run.

What this holds:

  1. the question reader: season, premiere, day counts, to date, full
     run, explicit dates, and the windows it leaves alone
  2. the six files: plain Season 2 and Season 1, each named window,
     "Season 1 Full", explicit dates, a file named in full
  3. the evidence handed to the writer: one file, and the window said
  4. a title held on one file per season is exactly as it was
  5. a seat that cannot open the widest file gets the widest it can; a
     seat that can open none of the season, or whose access cannot be
     read, gets the lookup exactly as it was
  6. a question with no season: one file per season, widest each
  7. the stored figures follow the same file; a figure stored for
     another window of the season is left out; a subject-only lookup is
     unchanged
  8. the legacy chat module is under its line ceiling and nothing here
     carries a long dash

Hermetic: a stub bucket, an in-memory ledger, no model, no mail, no
live read or write.
"""
import io
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines, legacy_lines as _legacy_lines, LEGACY_CEILING as _LEGACY_CEILING  # noqa: E402  (stage 5: chat.py plus its extracted cores; stage 7: the ratchet counts the legacy code)
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import insights_ledger as IL  # noqa: E402
import prometheus_analysis as PMA  # noqa: E402
from prometheus import season_window as SW  # noqa: E402

PASS = FAIL = 0
BUCKET = 'stub-trackers'


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


# ---------------------------------------------------------------------------
# The stub library
# ---------------------------------------------------------------------------

def tracker(show, platform, start, end, watchers, signups):
    us = int(round(watchers * 32.99))
    su = int(round(signups * 32.99))
    return '\n'.join([
        'Category,Episode Date,Count,Count Label,Secondary Count,Secondary Label,Tertiary Count,Tertiary Label,'
        'Percentage,Gen Pop Projection',
        ',,SHOW-TO-PLATFORM ATTRIBUTION RESULTS,,,,,,,',
        ',,,,,,,,,',
        f'Show/Content Tracked,,,{show},,,,,,',
        f'Platform Tracked,,,{platform},,,,,,',
        f'Analysis Date Range,,,{start} to {end},,,,,,',
        'Exclusion Window (days),,180,,,,,,,',
        ',,KEY METRICS,,,,,,,Gen Pop Projection',
        f'Total Show Watchers,,{watchers},,,,,,,"{us:,}"',
        f'New Platform Signups,,{signups},,,,,,,"{su:,}"',
        '']) + '\n'


OB = 'Outlander_Blood_of_My_Blood'
A = f'{OB}_Season_2_10_01_2026_16_58.csv'          # Season 2 premiere, four days
B = f'{OB}_Season_1_10_02_2026_01_19.csv'          # Season 1 premiere, four days
C = f'{OB}_Season_1_10_02_2026_02_07.csv'          # Season 1 full run (held first)
D = f'{OB}_11_18_2026_01_08.csv'                   # Season 2 first 30 days, named for the title alone
E = f'{OB}_Season_2_to_date_11_18_2026_02_30.csv'  # Season 2 to date
F = f'{OB}_Season_1_11_18_2026_18_49.csv'          # Season 1 re-run, two days shorter than the full run
L1 = 'Landman_Season_1_03_26_2026_20_15.csv'
L2 = 'Landman_Season_2_03_26_2026_21_31.csv'
K0 = 'The_Kardashians_09_22_2026_09_50.csv'
K6 = 'The_Kardashians_Season_6_09_21_2026_19_26.csv'
K7 = 'The_Kardashians_Season_7_03_26_2026_23_23.csv'
M1 = 'Manifest_-_Season_4_Part_1_07_17_2026_11_29.csv'
M2 = 'Manifest_-_Season_4_Part_2_07_17_2026_11_30.csv'
MT = 'Manifest_-_Season_4_Total_07_17_2026_11_31.csv'
SX = 'SWAT_Exiles_Season_1_10_02_2026_01_59.csv'
H1 = 'Harbor_Lights_Season_1_10_02_2026_01_59.csv'          # premiere, four days
H2 = 'Harbor_Lights_Season_1_to_date_10_29_2026_23_02.csv'  # to date, built first
H3 = 'Harbor_Lights_Season_1_to_date_11_18_2026_18_57.csv'  # to date under the same name, built again later

# key -> (last modified, body). The corrected files were all rewritten on
# one day, so the last-modified order says nothing about which was held first.
LIB = {
    A: ('2026-10-01T16:58', tracker('Outlander: Blood of My Blood', 'starz', '2026-09-18', '2026-09-21', 22458, 808)),
    B: ('2026-10-02T01:19', tracker('Outlander: Blood of My Blood', 'starz', '2025-08-08', '2025-08-11', 13394, 353)),
    C: ('2026-11-18T16:48', tracker('Outlander: Blood of My Blood', 'starz', '2025-08-08', '2025-10-10', 48476, 1253)),
    D: ('2026-11-18T16:49', tracker('Outlander: Blood of My Blood', 'starz', '2026-09-18', '2026-10-17', 38616, 1319)),
    E: ('2026-11-18T16:50', tracker('Outlander: Blood of My Blood', 'starz', '2026-09-18', '2026-11-18', 44764, 1605)),
    F: ('2026-11-18T19:04', tracker('Outlander: Blood of My Blood', 'starz', '2025-08-08', '2025-10-08', 48475, 1162)),
    L1: ('2026-03-26T20:15', tracker('Landman', 'paramount+', '2024-11-17', '2025-01-12', 514998, 9001)),
    L2: ('2026-03-26T21:31', tracker('Landman', 'paramount+', '2025-11-16', '2026-01-18', 426223, 7002)),
    K0: ('2026-09-22T09:50', tracker('The Kardashians', 'hulu', '2022-04-14', '2026-01-24', 655304, 9003)),
    K6: ('2026-09-21T19:26', tracker('The Kardashians', 'hulu', '2025-02-06', '2025-05-10', 194553, 3004)),
    K7: ('2026-03-26T23:23', tracker('The Kardashians', 'hulu', '2025-10-23', '2026-01-24', 177254, 2005)),
    M1: ('2026-07-17T11:29', tracker('Manifest', 'netflix', '2022-11-04', '2022-12-11', 666868, 8006)),
    M2: ('2026-07-17T11:30', tracker('Manifest', 'netflix', '2023-06-02', '2023-06-30', 394058, 5007)),
    MT: ('2026-07-17T11:31', tracker('Manifest', 'netflix', '2022-11-04', '2023-06-30', 788117, 9008)),
    SX: ('2026-10-02T01:59', tracker('SWAT Exiles', 'starz', '2026-09-25', '2026-09-28', 14397, 391)),
    H1: ('2026-11-10T22:50', tracker('Harbor Lights', 'starz', '2026-09-25', '2026-09-28', 14311, 377)),
    H2: ('2026-11-10T22:50', tracker('Harbor Lights', 'starz', '2026-09-25', '2026-10-29', 51203, 1411)),
    H3: ('2026-11-18T17:57', tracker('Harbor Lights', 'starz', '2026-09-25', '2026-11-18', 61446, 1733)),
    # copies a reader never sees
    f'_backups/{C}.pre_fix_20261118': ('2026-11-18T16:47', 'old'),
    f'historic/{OB}_Season_2_09_30_2026_10_00.csv': ('2026-09-30T10:00', 'old'),
}


class StubS3:
    def __init__(self, lib):
        self.lib = lib
        self.writes = 0

    def get_paginator(self, _name):
        lib = self.lib

        class _P:
            def paginate(self, **_kw):
                yield {'Contents': [{'Key': k, 'LastModified': datetime.strptime(v[0], '%Y-%m-%dT%H:%M').replace(
                    tzinfo=timezone.utc)} for k, v in lib.items()]}
        return _P()

    def get_object(self, Bucket=None, Key=None, **_kw):
        if Key not in self.lib:
            raise KeyError(Key)
        return {'Body': io.BytesIO(self.lib[Key][1].encode('utf-8')), 'ETag': '"e"'}

    def head_object(self, Bucket=None, Key=None, **_kw):
        return {'ETag': '"e"'}

    def put_object(self, **_kw):
        self.writes += 1
        raise AssertionError('a lookup wrote to the library')


def parser(text):
    def cell(label, col=2):
        m = re.search(r'^' + re.escape(label) + r',(.*)$', text, re.M)
        return next(iter(__import__('csv').reader([m.group(1)])))[col - 1] if m else ''
    return {'metadata': {'platform': cell('Platform Tracked', 3), 'date_range': cell('Analysis Date Range', 3)},
            'key_metrics': {'total_watchers': {'count': cell('Total Show Watchers'), 'gen_pop': cell('Total Show Watchers', 9)},
                            'new_signups': {'count': cell('New Platform Signups'), 'gen_pop': cell('New Platform Signups', 9)}}}


def use_library(lib):
    with PMA._xmod_lock:
        PMA._xmod_subiq_index_cache.update(ts=0.0, index=None, shows=None)
        PMA._subiq_head_cache.clear()
        PMA._subiq_payload_cache.clear()
    return StubS3(lib)


S3 = use_library(LIB)
OBQ = 'Outlander: Blood of My Blood'


def named(q, can_open=None, s3=None):
    return [k for _s, k, _lm in PMA.find_subiq_titles_in_text(s3 or S3, BUCKET, q, can_open=can_open)]


def one(q, hint='', can_open=None, prefer_text=False):
    return PMA.find_subiq_title(S3, BUCKET, q, subject_hint=hint, prefer_text=prefer_text, can_open=can_open)[1]


def block(q, hint='', can_open=None):
    return PMA.build_subiq_evidence_block(S3, BUCKET, parser, q, subject_hint=hint, can_open=can_open)[0]


# ---------------------------------------------------------------------------
print('1. the question reader')
# ---------------------------------------------------------------------------
for q, kind, seasons, days in (
        ('How many signups did Outlander: Blood of My Blood Season 2 drive?', None, [2], None),
        ('Outlander BOMB S1 signups', None, [1], None),
        ('Season 2 premiere signups', 'premiere', [2], None),
        ('the opening weekend of season 1', 'premiere', [1], None),
        ('first four days of Season 2', 'days', [2], 4),
        ('Season 2 first 30 days viewers', 'days', [2], 30),
        ('how did the first week of season 2 go', 'days', [2], 7),
        ('Season 2 to date signups', 'to_date', [2], None),
        ('season 2 so far', 'to_date', [2], None),
        ('How did Season 2 do since its premiere?', 'to_date', [2], None),
        ('season 2 signups since it launched', 'to_date', [2], None),
        ('Season 1 Full', 'full', [1], None),
        ('the whole run of season 1', 'full', [1], None),
        ('Season 1 from Aug 8 to Oct 10, 2025', 'dates', [1], None),
        ('2026-09-18 to 2026-11-18', 'dates', [], None),
        ('season 2 signups in October', 'other', [2], None),
        ('season 2 signups in the last 30 days', 'other', [2], None),
        ('season 2 signups this month', 'other', [2], None),
        ('What may happen in season 3?', None, [3], None),
        ('total signups for season 2', None, [2], None)):
    a = SW.ask(q)
    check(f'{q!r} -> {kind}', a['kind'] == kind and a['seasons'] == seasons and a['days'] == days, str(a))
check('a title is its name up to the season', SW.title_base('Outlander Blood of My Blood Season 2 to date')
      == 'outlander blood of my blood' and SW.title_base('S.W.A.T. Exiles - Season 1') == 'swat exiles'
      and SW.name_tail('Outlander Blood of My Blood Season 2 to date') == 'to date'
      and SW.name_tail('Outlander Blood of My Blood Season 2') == '')
check('a file was held when its name says, whatever was corrected since',
      SW.built_stamp(C) == '202610020207' and SW.built_stamp(F) == '202611181849' and SW.built_stamp(C) < SW.built_stamp(F))
check('a window reads in plain words', SW.window_label('2026-09-18', '2026-11-18') == 'Sep 18 to Nov 18, 2026'
      and SW.window_label('2021-12-19', '2022-02-20') == 'Dec 19, 2021 to Feb 20, 2022')
files = PMA.subiq_title_files(S3, BUCKET, 'outlander blood of my blood')
check('the title holds six live files, each with its window and service', len(files) == 6
      and all(f['start'] and f['end'] and f['platform'] == 'starz' for f in files),
      str([(f['key'], f['start'], f['end'], f['platform']) for f in files]))
seasons = {c['key']: (c['season'], c['season_from']) for c in SW.attach_seasons(files)}
check('the file named for the title alone belongs to the season it sits inside', seasons[D] == (2, 'window'),
      str(seasons[D]))
kfiles = SW.attach_seasons(PMA.subiq_title_files(S3, BUCKET, 'the kardashians'))
check('a file that spans every season belongs to none', {c['key']: c['season'] for c in kfiles}[K0] is None)

# ---------------------------------------------------------------------------
print('2. the six files')
# ---------------------------------------------------------------------------
for q, want, why in (
        (f'How many signups did {OBQ} Season 2 drive for Starz?', [E], 'plain Season 2 -> the season to date'),
        (f'How many people watched {OBQ} Season 2?', [E], 'plain Season 2, another wording'),
        ('Outlander BOMB S2 signups', [E], 'the house shorthand'),
        (f'How many people watched {OBQ} Season 1?', [C], 'plain Season 1 -> the full run, held first'),
        (f'{OBQ} Season 1 Full signups', [C], '"Season 1 Full" -> the full run'),
        (f'{OBQ} full Season 1 run', [C], 'full run, other order'),
        (f'{OBQ} Season 2 premiere signups', [A], 'Season 2 premiere'),
        (f'How many signups in the first four days of {OBQ} Season 2?', [A], 'first four days'),
        (f'{OBQ} Season 2 first 30 days viewers', [D], 'first 30 days'),
        (f'{OBQ} Season 2 to date signups', [E], 'to date'),
        (f'{OBQ} Season 1 premiere viewers', [B], 'Season 1 premiere'),
        (f'{OBQ} Season 1 first 30 days', [C], 'first 30 days of a season held only whole'),
        (f'{OBQ} Season 1 signups from Aug 8 to Oct 10, 2025', [C], 'explicit dates'),
        (f'{OBQ} Season 2 signups from Sep 18 to Oct 17, 2026', [D], 'explicit dates, Season 2'),
        (f'{OBQ} Season 2 2026-09-18 to 2026-09-21', [A], 'explicit ISO dates'),
        (f'Compare {OBQ} Season 1 to Season 2 signups', [C, E], 'two seasons, widest each'),
        (f'How many signups did {OBQ} Season 3 drive?', [], 'a season not held names no file'),
        (f'Compare the first three days of {OBQ} Season 2 to the first three days of SWAT Exiles Season 1',
         [A, SX], 'two titles, the opening days of each')):
    got = named(q)
    check(why, got == want, f'{q!r} -> {got}')
check('one file answers a plain Season 2 question on the single-file path too',
      one(f'How many signups did {OBQ} Season 2 drive?') == E and one(f'{OBQ} Season 1 viewers', prefer_text=True) == C)
check('the open subject names the season the words left out',
      one('How many signups did it drive?', hint=f'{OBQ} - Season 2') == E
      and one('How many signups did it drive?', hint=f'{OBQ} - Season 1') == C)
check('the open subject names the title, the words name the window',
      one('what about the premiere?', hint=f'{OBQ} - Season 2') == A
      and one('and the first 30 days?', hint=f'{OBQ} - Season 2') == D)
check('a window this rule does not place leaves the lookup as it was',
      len(named(f'{OBQ} Season 2 signups in October')) > 1)
check('nothing was written', S3.writes == 0)

# ---------------------------------------------------------------------------
print('3. the evidence handed to the writer')
# ---------------------------------------------------------------------------
b2 = block(f'How many signups did {OBQ} Season 2 drive?')
check('plain Season 2: one read, the season to date',
      b2.count('SUBSCRIBER ACQUISITION EVIDENCE') == 1 and 'window 2026-09-18 to 2026-11-18' in b2, b2[:200])
check('and it carries the season-to-date figures', '44,764' in b2 and '1,605' in b2 and '22,458' not in b2
      and '38,616' not in b2)
check('the window is said in plain words', 'WINDOW OF THIS READ: Sep 18 to Nov 18, 2026.' in b2
      and 'these figures cover Sep 18 to Nov 18, 2026' in b2, b2[:400])
line = SW.window_line({'collapsed': True, 'label': 'Sep 18 to Nov 18, 2026'})
check('the line tells the writer to give dates only', 'never say how the window was chosen' in line
      and not re.search(r'widest|held|first-held|picked|library', line), line)
b1 = block(f'How many people watched {OBQ} Season 1?')
check('plain Season 1: the full run, with its window', b1.count('SUBSCRIBER ACQUISITION EVIDENCE') == 1
      and 'WINDOW OF THIS READ: Aug 8 to Oct 10, 2025.' in b1 and '48,476' in b1 and '48,475' not in b1, b1[:300])
bd = block(f'{OBQ} Season 2 to date signups')
check('a file named in full is read as named, with no extra line',
      bd.count('SUBSCRIBER ACQUISITION EVIDENCE') == 1 and 'WINDOW OF THIS READ' not in bd and '44,764' in bd)
bh = block('How many signups did it drive?', hint=f'{OBQ} - Season 2')
check('the open subject path says the window too', 'WINDOW OF THIS READ: Sep 18 to Nov 18, 2026.' in bh)

# ---------------------------------------------------------------------------
print('4. a title held on one file per season is exactly as it was')
# ---------------------------------------------------------------------------
check('Landman Season 2 -> its one file', named('How many signups did Landman Season 2 drive?') == [L2])
check('Landman Season 1 -> its one file', named('Landman Season 1 viewers') == [L1])
check('Landman with no season -> both files, newest first, as before',
      named('How many signups did Landman drive?') == [L2, L1])
bl = block('How many signups did Landman Season 2 drive?')
check('and no window line is added', 'WINDOW OF THIS READ' not in bl and '426,223' in bl)
check('a title with a file for every season and one for all of them is as it was',
      named('How many signups did The Kardashians drive?') == [K0, K6, K7]
      and named('The Kardashians Season 7 signups') == [K7, K0],
      f"{named('How many signups did The Kardashians drive?')} | {named('The Kardashians Season 7 signups')}")
check('a season held in parts answers from its widest window', named('How many signups did Manifest Season 4 drive?') == [MT])
check('and a part named in full is read as named', named('Manifest Season 4 Part 2 signups') == [M2])
check('a part named in words is read as named too', named('Manifest Season 4 part two signups') == [M2]
      and named('Manifest Season 4 Part 1 signups') == [M1] and named('Manifest Season 4 Total signups') == [MT])
check('two parts named together are both read, and nothing else', sorted(named('How many signups did Manifest Season 4 Part 1 and Part 2 drive?'))
      == sorted([M1, M2]) and sorted(named('Manifest Season 4 parts one and 2')) == sorted([M1, M2])
      and named('Manifest Part 2 signups') == [M2], str(named('How many signups did Manifest Season 4 Part 1 and Part 2 drive?')))
check('a part of another season or another title names nothing here',
      SW.files_named([{'show': 'Manifest - Season 4 Part 2', 'key': M2}], 'Manifest Season 3 Part 2') == []
      and SW.files_named([{'show': 'Manifest - Season 4 Part 2', 'key': M2}], 'Manifest Season 4 against Money Heist Part 2') == [])
check('a title held on one file is as it was', named('SWAT Exiles Season 1 signups') == [SX]
      and named('how did SWAT Exiles do') == [SX])
got = named('Harbor Lights Season 1 to date signups')
check('one full name built twice is one read, never the two side by side', got == [H3], str(got))
bh2 = block('Harbor Lights Season 1 to date signups')
check('and it says which window it is', bh2.count('SUBSCRIBER ACQUISITION EVIDENCE') == 1
      and 'WINDOW OF THIS READ: Sep 25 to Nov 18, 2026.' in bh2 and '51,203' not in bh2, bh2[:300])
check('plain Season 1 of that title -> the widest window', named('How many signups did Harbor Lights Season 1 drive?') == [H3]
      and named('Harbor Lights Season 1 premiere') == [H1])

# ---------------------------------------------------------------------------
print('5. a seat that cannot open the widest file')
# ---------------------------------------------------------------------------
no_e = (lambda key, show='': key != E)
no_c = (lambda key, show='': key != C)
only_a = (lambda key, show='': key == A)
none = (lambda key, show='': False)
check('Season 2 without the season to date -> the first 30 days', named(f'{OBQ} Season 2 signups', can_open=no_e) == [D])
check('Season 2 with only the premiere -> the premiere', named(f'{OBQ} Season 2 signups', can_open=only_a) == [A])
check('Season 1 without the full run -> the re-run', named(f'{OBQ} Season 1 signups', can_open=no_c) == [F])
check('the single-file path follows the seat too', one(f'{OBQ} Season 2 signups', can_open=no_e) == D
      and one('How many signups did it drive?', hint=f'{OBQ} - Season 2', can_open=no_e) == D)
# what the lookup returned before this rule: the file named "Season 2" (the premiere) and the title's own file;
# for Season 1 the newest of the three files that share the name, and the title's own file
check('a seat with none of the season gets the lookup exactly as it was',
      named(f'{OBQ} Season 2 signups', can_open=none) == [A, D] and named(f'{OBQ} Season 1 signups', can_open=none) == [F, D]
      and one(f'{OBQ} Season 2 signups', can_open=none) == A,
      f"{named(f'{OBQ} Season 2 signups', can_open=none)} {named(f'{OBQ} Season 1 signups', can_open=none)}")
check('and no window is chosen for it', 'WINDOW OF THIS READ' not in block(f'{OBQ} Season 2 signups', can_open=none))
check('a seat with one season but not the other: nothing is chosen for the title',
      named(f'Compare {OBQ} Season 1 to Season 2 signups', can_open=(lambda key, show='': key in (A, D, E)))
      == named(f'Compare {OBQ} Season 1 to Season 2 signups', can_open=none))
check('a full name built twice follows the seat', named('Harbor Lights Season 1 to date signups',
                                                         can_open=(lambda key, show='': key != H3)) == [H2]
      and named('Harbor Lights Season 1 to date signups', can_open=none) == [H3])
check('the evidence says the window the seat was read from',
      'WINDOW OF THIS READ: Sep 18 to Oct 17, 2026.' in block(f'{OBQ} Season 2 signups', can_open=no_e))
check('with nobody named the lookup is not narrowed', SW.reader_can_open() is None)
# the reader of the work in flight, on a host that knows its seats
from prometheus import ledger_gate as LG  # noqa: E402
from prometheus.host import host as HOST  # noqa: E402
_seats = {'users': {'seat_a': {'username': 'seat_a', 'role': 'user', 'opens': [A, D]},
                    'staff_b': {'username': 'staff_b', 'role': 'admin', 'opens': '*'}}}
_was = (dict(HOST._b), HOST.bound)
HOST._b.update(load_users=lambda: _seats,
               can_open_subiq=lambda user, key, show='': user.get('opens') == '*' or key in (user.get('opens') or []))
HOST.bound = True
try:
    with LG.as_asker('seat_a'):
        can = SW.reader_can_open()
        check('the reader in flight is read off the host', callable(can) and can(D) and not can(E))
        check('and the lookup follows that seat with nothing passed in',
              [k for _s, k, _lm in PMA.find_subiq_titles_in_text(S3, BUCKET, f'{OBQ} Season 2 signups')] == [D])
    with LG.as_asker('staff_b'):
        check('a seat that opens everything gets the widest',
              [k for _s, k, _lm in PMA.find_subiq_titles_in_text(S3, BUCKET, f'{OBQ} Season 2 signups')] == [E])
    with LG.as_asker('canary:probe', probe=True):
        check('a probe that is no seat is not narrowed', SW.reader_can_open() is None)
    with LG.as_asker('seat_a', probe=True):
        can = SW.reader_can_open()
        check('a probe run as a seat follows that seat', callable(can) and can(D) and not can(E))
    with LG.as_asker('nobody_on_file'):
        can = SW.reader_can_open()
        check('a name the host does not know opens nothing here', callable(can) and not can(E) and not can(A))
        check('so that reader gets the lookup exactly as it was',
              [k for _s, k, _lm in PMA.find_subiq_titles_in_text(S3, BUCKET, f'{OBQ} Season 2 signups')] == [A, D])
    HOST._b.pop('can_open_subiq', None)
    SW._reader_cache.clear()
    with LG.as_asker('seat_a'):
        can = SW.reader_can_open()
        check('a seat on a host that cannot say what it opens gets the lookup as it was', callable(can) and not can(D)
              and [k for _s, k, _lm in PMA.find_subiq_titles_in_text(S3, BUCKET, f'{OBQ} Season 2 signups')] == [A, D])
finally:
    SW._reader_cache.clear()
    HOST._b.clear()
    HOST._b.update(_was[0])
    HOST.bound = _was[1]

# ---------------------------------------------------------------------------
print('6. a question with no season')
# ---------------------------------------------------------------------------
got = named(f'How many signups did {OBQ} drive?')
check('one file per season, the widest of each, latest season first', got == [E, C], str(got))
bn = block(f'How many signups did {OBQ} drive?')
check('both windows are said', 'WINDOW OF THIS READ: Sep 18 to Nov 18, 2026.' in bn
      and 'WINDOW OF THIS READ: Aug 8 to Oct 10, 2025.' in bn and bn.count('SUBSCRIBER ACQUISITION EVIDENCE') == 2)
check('never two windows of one season side by side', '38,616' not in bn and '22,458' not in bn and '48,475' not in bn)
check('the open title with no season reads the latest season at its widest',
      one('How many signups did it drive?', hint=OBQ) == E)

# ---------------------------------------------------------------------------
print('7. the stored figures follow the same file')
# ---------------------------------------------------------------------------

def row(subject, key, ws, we, viewers, signups, ts):
    skey = IL.subject_key(subject)
    return skey, {'subject': subject, 'entries': [{
        'k': f'{skey}|viewership|{ws}|{we}', 'subject': subject, 'family': 'viewership',
        'q': f'Subscriber IQ tracker: {subject}', 'qn': IL.normalize_question(f'Subscriber IQ tracker: {subject}'),
        'route': 'delivered_subiq', 'ts': ts, 'ws': ws, 'we': we, 'wl': SW.window_label(ws, we),
        'metrics': [{'name': 'US viewers in window', 'label': 'US viewers in window', 'value': viewers, 'unit': 'people',
                     'definition': 'Subscriber IQ tracker'},
                    {'name': 'New platform signups', 'label': 'New platform signups', 'value': signups, 'unit': 'accounts',
                     'definition': 'Subscriber IQ tracker'}],
        'anchors': [], 'base': '', 'cohort': '', 'universe': '', 'breakdown': None,
        'reply': f'Subscriber IQ tracker {key}', 'followups': [], 'derivation': '', 'prov': 'delivered_artifact'}]}


subjects = dict([
    row(OBQ, D, '2026-09-18', '2026-10-17', 1273942, 43507, '2026-11-18T20:22:11Z'),
    row(f'{OBQ} - Season 2 to date', E, '2026-09-18', '2026-11-18', 1476764, 52949, '2026-11-18T20:22:05Z'),
    row(f'{OBQ} - Season 1', F, '2025-08-08', '2025-10-08', 1599182, 38334, '2026-11-18T20:22:17Z'),
    row('Landman - Season 2', L2, '2025-11-16', '2026-01-18', 14061097, 230996, '2026-03-27T00:00:00Z'),
])
KD, KE, KF = IL.subject_key(OBQ), IL.subject_key(f'{OBQ} - Season 2 to date'), IL.subject_key(f'{OBQ} - Season 1')
IL.use_test_ledger(index={'subjects': subjects, 'updated': '2026-11-18T20:30:00Z'})
SW._library_files = lambda base: PMA.subiq_title_files(S3, BUCKET, base)     # the library the bound host would read
_reader = [None]
SW.reader_can_open = lambda: _reader[0]


def stored(q, subject=None):
    led = IL.consult(subject=subject, question=q) or {}
    rows = [SW.tracker_file_of(e) for e in led.get('entries') or []]
    return led.get('skey'), [r for r in rows if r], led.get('block') or ''


k, rows, blk = stored(f'How many signups did {OBQ} Season 2 drive for Starz?')
check('plain Season 2 -> the season-to-date figures', k == KE and rows == [E] and '1,476,764' in blk
      and '1,273,942' not in blk, f'{k} {rows}')
check('and the stored row says its window', 'Sep 18 to Nov 18, 2026' in blk, blk[:300])
k, rows, blk = stored(f'{OBQ} Season 2 first 30 days viewers')
check('first 30 days -> the 30-day figures', k == KD and rows == [D] and '1,273,942' in blk, f'{k} {rows}')
k, rows, blk = stored(f'{OBQ} Season 2 to date signups')
check('to date -> the season-to-date figures', k == KE and rows == [E])
k, rows, blk = stored(f'How many people watched {OBQ} Season 1?')
check('plain Season 1: the re-run figure does not stand in for the full run', E not in rows and F not in rows
      and '1,599,182' not in blk and '38,334' not in blk, f'{k} {rows}')
k, rows, blk = stored(f'{OBQ} Season 2 premiere signups')
check('premiere: no other window is published in its place', rows == [] and '1,273,942' not in blk, f'{k} {rows}')
k, rows, blk = stored(f'How many signups did {OBQ} drive?')
check('no season -> the latest season at its widest', k == KE and rows == [E], f'{k} {rows}')
k, rows, blk = stored('How many signups did it drive?', subject=f'{OBQ} - Season 2')
check('the bound subject names the season', k == KE and rows == [E], f'{k} {rows}')
k, rows, blk = stored('How many signups did it drive?', subject=f'{OBQ} - Season 2 to date')
check('a bound subject named in full is read as named', k == KE and rows == [E])
led = IL.consult(subject=OBQ) or {}
check('a subject-only lookup is unchanged', led.get('skey') == KD
      and [SW.tracker_file_of(e) for e in led.get('entries') or []] == [D])
led = IL.consult(subject=f'{OBQ} - Season 1') or {}
check('and so is one on a season subject', led.get('skey') == KF)
k, rows, blk = stored('How many signups did Landman Season 2 drive?')
check('a title held on one file per season is unchanged', k == IL.subject_key('Landman - Season 2') and rows == [L2])
k, rows, blk = stored(f'{OBQ} Season 2 signups in October')
check('a window this rule does not place leaves the stored lookup as it was', k == KD and rows == [D], f'{k} {rows}')
_reader[0] = no_e
k, rows, blk = stored(f'How many signups did {OBQ} Season 2 drive?')
check('a seat without the season to date is answered from the first 30 days', k == KD and rows == [D]
      and '1,476,764' not in blk, f'{k} {rows}')
_reader[0] = none
k, rows, blk = stored(f'How many signups did {OBQ} Season 2 drive?')
check('a seat with none of the season: the stored lookup is as it was', k == KD and rows == [D], f'{k} {rows}')
_reader[0] = None
# with no library to read, the stored rows themselves are the windows held
SW._library_files = lambda base: None
k, rows, blk = stored(f'How many signups did {OBQ} Season 2 drive?')
check('with no library in reach the stored rows decide, the same way', k == KE and rows == [E], f'{k} {rows}')

# ---------------------------------------------------------------------------
print('8. housekeeping')
# ---------------------------------------------------------------------------
chat_lines = _legacy_lines()
check(f'the legacy chat code is under its ceiling ({chat_lines} lines)', chat_lines < _LEGACY_CEILING)
for rel in ('prometheus/season_window.py', 'scripts/test_pm_season_window.py'):
    src = (ROOT / rel).read_text(encoding='utf-8')
    check(f'no long dash in {rel}', '\u2014' not in src and '\u2013' not in src)
sw_src = (ROOT / 'prometheus' / 'season_window.py').read_text(encoding='utf-8')
check('the rule takes no flag from a request', not re.search(r'request\.(?:json|get_json|args|form)', sw_src))
check('nothing was written to the library', S3.writes == 0)

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
