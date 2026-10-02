#!/usr/bin/env python3
"""Panel facts: exact answers from the shipped base file
(Jenna 2026-09-30: "lets now do the panel-fact queries upgrades").

Functional checks run the real detector and answerer against a
synthetic profile frame; source checks pin the app.py wiring."""
import os
import re
import sys
# Legacy-move shim (2026-10-01): the Prometheus code lives in
# bg-webapp/prometheus/legacy/chat.py; read app.py + legacy as one source.
import os as _pm_os, sys as _pm_sys
_pm_r = _pm_os.path.dirname(_pm_os.path.abspath(__file__))
while not _pm_os.path.exists(_pm_os.path.join(_pm_r, 'bg-webapp', 'app.py')):
    _pm_r = _pm_os.path.dirname(_pm_r)
_pm_sys.path.insert(0, _pm_os.path.join(_pm_r, 'scripts'))
from _pm_test_source import app_path as _pm_app_path, host_for as _pm_host_for  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

FAIL = 0


def check(name, ok, detail=''):
    global FAIL
    print(('PASS' if ok else 'FAIL'), name, detail)
    if not ok:
        FAIL += 1


# ------------------------- detection -------------------------------
d = pma.detect_panel_fact("what's the female share on the Reba file")
check('detect: female share -> GENDER/FEMALE',
      d == {'kind': 'demo', 'column': 'GENDER', 'bucket': 'FEMALE'},
      repr(d))
d = pma.detect_panel_fact('what is the age breakdown')
check('detect: age breakdown -> AGE split',
      d == {'kind': 'demo', 'column': 'AGE', 'bucket': None}, repr(d))
d = pma.detect_panel_fact('what % of them watch Hulu')
check('detect: brand reach ask -> Hulu',
      d == {'kind': 'brand', 'brand': 'Hulu'}, repr(d))
d = pma.detect_panel_fact('Chick-fil-A penetration')
check('detect: penetration phrasing',
      d is not None and d.get('kind') == 'brand'
      and 'chick' in d.get('brand', '').lower(), repr(d))
d = pma.detect_panel_fact('top 5 qsr for this audience')
check('detect: top 5 qsr',
      d == {'kind': 'top', 'cat_hint': 'qsr', 'n': 5}, repr(d))
d = pma.detect_panel_fact('top streaming services')
check('detect: top streaming (suffix stripped)',
      d is not None and d.get('kind') == 'top'
      and d.get('cat_hint') == 'streaming', repr(d))
d = pma.detect_panel_fact('how big is this audience')
check('detect: audience size', d == {'kind': 'size'}, repr(d))

for bad in ('why is Hulu winning with this audience',
            'compare Reba fans vs Dolly fans on QSR',
            'what should we pitch to the female side',
            'put together a report on toy white space',
            'walk me through the journey from search to checkout',
            'x' * 230):
    check(f'detect: analytical ask falls through ({bad[:28]!r})',
          pma.detect_panel_fact(bad) is None)
check('detect: apparel never resolves through the app alias',
      pma.detect_panel_fact('top apparel brands') is not None)

# ------------------------- answering -------------------------------
BP = 'Brand Penetration (Row)'
df = pd.DataFrame([
    {'Column': 'BRAND INPUT', 'Value': 'REBA MCENTIRE', BP: '100.0000%',
     'Raw': '84213', 'Projection': '2778387'},
    {'Column': 'SUBJECT', 'Value': 'Reba McEntire', BP: '',
     'Raw': '', 'Projection': ''},
    {'Column': 'SAMPLE SIZE', 'Value': '07/01/2025 - 06/30/2026',
     BP: '100.0000%', 'Raw': '84213', 'Projection': '2778387'},
    {'Column': 'GENDER', 'Value': 'FEMALE', BP: '61.4321%',
     'Raw': '51737', 'Projection': '1706887'},
    {'Column': 'GENDER', 'Value': 'MALE', BP: '38.5679%',
     'Raw': '32476', 'Projection': '1071499'},
    {'Column': 'AGE', 'Value': '18-24', BP: '9.1342%', 'Raw': '7692',
     'Projection': '253790'},
    {'Column': 'AGE', 'Value': '25-34', BP: '17.2411%',
     'Raw': '14520', 'Projection': '479063'},
    {'Column': 'AGE', 'Value': '35-44', BP: '21.9876%',
     'Raw': '18517', 'Projection': '610934'},
    {'Column': 'AGE', 'Value': '45-54', BP: '24.1132%',
     'Raw': '20307', 'Projection': '669998'},
    {'Column': 'AGE', 'Value': '55+', BP: '27.5239%', 'Raw': '23179',
     'Projection': '764601'},
    {'Column': 'QSR', 'Value': "McDonald's", BP: '52.1234%',
     'Raw': '43898', 'Projection': '1448264'},
    {'Column': 'QSR', 'Value': 'Chick-fil-A', BP: '44.9871%',
     'Raw': '37885', 'Projection': '1249877'},
    {'Column': 'QSR', 'Value': 'Starbucks', BP: '41.2345%',
     'Raw': '34725', 'Projection': '1145623'},
    {'Column': 'STREAMING/PLATFORM', 'Value': 'Netflix',
     BP: '71.2345%', 'Raw': '59989', 'Projection': '1979146'},
    {'Column': 'STREAMING/PLATFORM', 'Value': 'Hulu', BP: '58.3456%',
     'Raw': '49134', 'Projection': '1621005'},
    {'Column': 'TALENT', 'Value': 'Reba McEntire', BP: '100.0000%',
     'Raw': '84213', 'Projection': '2778387'},
    {'Column': 'TALENT', 'Value': 'Dolly Parton', BP: '48.7123%',
     'Raw': '41022', 'Projection': '1353377'},
])
meta = pma._profile_meta(df, 'Reba McEntire')
check('meta: sample and projection parsed',
      meta.get('sample') == 84213 and meta.get('proj') == 2778387,
      repr(meta))

gmap = {('STREAMING/PLATFORM', 'hulu'): 42.1234,
        ('QSR', 'chickfila'): 39.8765}

a = pma.answer_panel_fact({'kind': 'demo', 'column': 'GENDER',
                           'bucket': 'FEMALE'}, df, meta, gmap)
check('answer: female share reads the exact file value',
      a is not None and '61.4%' in a['reply']
      and '38.6%' in a['reply'], repr((a or {}).get('reply')))
check('answer: demo metrics carry file precision',
      a is not None and any(m['value'] == 61.4321
                            for m in a['metrics']))

a = pma.answer_panel_fact({'kind': 'demo', 'column': 'AGE',
                           'bucket': None}, df, meta, gmap)
check('answer: age split lists the buckets',
      a is not None and '55+' in a['reply'] and '27.5%' in a['reply'],
      repr((a or {}).get('reply')))
check('answer: age breakdown rides all buckets',
      a is not None and len(a['breakdown']['rows']) == 5)

a = pma.answer_panel_fact({'kind': 'brand', 'brand': 'Hulu'},
                          df, meta, gmap)
check('answer: brand reach with US average comparison',
      a is not None and '58.3%' in a['reply']
      and '42.1%' in a['reply'] and '1.4x' in a['reply'],
      repr((a or {}).get('reply')))

a = pma.answer_panel_fact({'kind': 'brand', 'brand': 'Peacock'},
                          df, meta, gmap)
check('answer: unknown brand falls through (None)', a is None)

a = pma.answer_panel_fact({'kind': 'top', 'cat_hint': 'qsr', 'n': 3},
                          df, meta, gmap)
check('answer: top 3 QSR ordered desc',
      a is not None and a['reply'].index("McDonald's")
      < a['reply'].index('Chick-fil-A') < a['reply'].index('Starbucks'),
      repr((a or {}).get('reply')))

a = pma.answer_panel_fact({'kind': 'top', 'cat_hint': 'talent',
                           'n': 5}, df, meta, gmap)
check('answer: subject self-pin excluded from top lists',
      a is None or 'Reba McEntire' not in a['reply'],
      repr((a or {}).get('reply')))

a = pma.answer_panel_fact({'kind': 'size'}, df, meta, gmap)
check('answer: audience size reads the file projection',
      a is not None and '2,778,387' in a['reply'],
      repr((a or {}).get('reply')))

# Vocabulary: replies never leak internal terms.
BANNED = re.compile(
    r'\b(?:panel|modeled|estimated|synth|pipeline|hostmap|clickstream|'
    r'gen pop|genpop|s3|bucket|dataframe|penetration \(row\))\b', re.I)
for fact in ({'kind': 'demo', 'column': 'GENDER', 'bucket': 'FEMALE'},
             {'kind': 'brand', 'brand': 'Hulu'},
             {'kind': 'top', 'cat_hint': 'qsr', 'n': 3},
             {'kind': 'size'}):
    a = pma.answer_panel_fact(fact, df, meta, gmap)
    check(f"vocab: clean reply for {fact['kind']}",
          a is not None and not BANNED.search(a['reply']),
          repr((a or {}).get('reply')))

# ------------------------- app.py wiring ---------------------------
app_src = open(str(_pm_app_path()), encoding='utf-8').read()
check('wiring: response helper defined',
      'def _pm_panel_fact_response(' in app_src)
seg = app_src[app_src.index('def _pm_panel_fact_response('):]
seg = seg[:seg.index('\ndef _pm_generate_metrics_response(')]
check('wiring: csv-only bases (never subiq/panel sources)',
      "endswith('.csv')" in seg)
check('wiring: metered, never free',
      "_pm_meter_answer('panel_fact'" in seg)
check('wiring: joins the consistency ledger', 'il.persist(' in seg)
check('wiring: answer ships its CSV',
      '_pm_answer_file_payload(' in seg)
check('wiring: telemetry route stamped',
      "route='panel_fact'" in seg)
gen = app_src[app_src.index('def _pm_generate_metrics_response('):]
i_replay = gen.index("'reply': exact['reply']")
i_pf = gen.index('_pm_panel_fact_response(_pm_user')
i_async = gen.index('if async_fresh is None:')
check('wiring: runs after replay, before the fresh read job',
      i_replay < i_pf < i_async)
check('wiring: comparison pairs and charged reports skip it',
      'if not _two and panel_charge is None:' in gen[:i_async])

print()
if FAIL:
    print(f'{FAIL} CHECK(S) FAILED')
    sys.exit(1)
print('ALL CHECKS PASSED')
