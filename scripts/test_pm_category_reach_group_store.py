#!/usr/bin/env python3
"""A named brand group's member list is kept per profile (2026-11-11).

`prometheus.category_reach._s3()` read `from . import host`, which is the
module that holds the host registry, not the registry. `host.has` did
not exist on it, the lookup fell through to (None, None), and so no
group was ever saved or read back: every deck and every chat answer
drew a named group's members again, and the same name could carry two
lists on one profile. The same import sat in `cross_sizing` and
`brand_coverage` (reached when no host is passed).

Holds:
  1. with the host bound, `_s3()` hands back its client and bucket;
  2. a group saved with no client passed lands in the store and reads
     back; the first list a name was given stays;
  3. the deck's annotate call reaches the store through the same door;
  4. a probe's ask never writes a group (first writer wins);
  5. no module in the package takes the host module for the registry.

Hermetic: the host is bound to an in-memory store.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus.host import host  # noqa: E402
from prometheus import category_reach as CR  # noqa: E402
from prometheus import ledger_gate as LG  # noqa: E402

FAILS = []


def check(msg, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (f"  [{detail}]" if (detail and not cond) else ''))
    if not cond:
        FAILS.append(msg)


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[(Bucket, Key)] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')


KEY = 'Chime_Banking_Customers_05_12_2026_15_12.csv'

print("[1] the host's client and bucket")
check("unbound: nothing to write to, nothing raised", CR._s3() == (None, None) or CR._s3()[0] is not None)
s3 = FakeS3()
host.bind(s3_client=s3, bucket='dashboard-inputs')
got = CR._s3()
check("bound: _s3() is the host's own client and bucket", got[0] is s3 and got[1] == 'dashboard-inputs', str(got))

print("[2] a group is kept and read back")
ok = CR.save_group(KEY, 'Budget grocers', ['ALDI', 'LIDL', 'WINCO FOODS'], by='chat')
check("saved with no client passed", ok is True)
stored = [k for (_b, k) in s3.objs if k.startswith(CR.GROUP_PREFIX)]
check("one object under the group store", len(stored) == 1, str(stored))
groups = CR.load_groups(KEY)
g = groups.get(CR.label_key('Budget grocers')) or {}
check("read back with no client passed", g.get('brands') == ['ALDI', 'LIDL', 'WINCO FOODS'], str(groups))
again = CR.save_group(KEY, 'budget grocers', ['KROGER'], by='deck')
check("the first list a name was given stays", again is False
      and CR.load_groups(KEY)[CR.label_key('Budget grocers')]['brands'] == ['ALDI', 'LIDL', 'WINCO FOODS'])
check("another profile has its own store", CR.load_groups('Other_Profile.csv') == {})

print("[3] the deck's annotate call reaches the store")
src = open(os.path.join(ROOT, 'prometheus', 'category_reach.py'), encoding='utf-8').read()
check("annotate_deck asks _s3() for the client", 's3, bucket = _s3()' in src[src.index('def annotate_deck('):][:900])
check("_s3 imports the registry object", 'from .host import host' in src[src.index('def _s3():'):][:700])

print("[4] a probe never writes a group")
with LG.as_asker('canary:probe'):
    wrote = CR.save_group(KEY, 'Dollar stores', ['DOLLAR GENERAL', 'DOLLAR TREE'])
check("a canary's group is not kept", wrote is False and CR.label_key('Dollar stores') not in CR.load_groups(KEY))
with LG.as_asker('jessie'):
    wrote = CR.save_group(KEY, 'Dollar stores', ['DOLLAR GENERAL', 'DOLLAR TREE'])
check("a reader's is", wrote is True and CR.label_key('Dollar stores') in CR.load_groups(KEY))

print("[5] no module takes the host module for the registry")
bad = []
pkg = os.path.join(ROOT, 'prometheus')
for name in sorted(os.listdir(pkg)):
    if not name.endswith('.py') or name in ('__init__.py', 'host.py'):
        continue
    text = open(os.path.join(pkg, name), encoding='utf-8').read()
    for m in re.finditer(r"from \. import host(?: as (\w+))?\s*\n", text):
        alias = m.group(1) or 'host'
        tail = text[m.end():m.end() + 1500]
        if re.search(rf"\b{alias}\.(?:has\(|s3_client|bucket\b|s3_json|load_history)", tail) \
                or re.search(rf"\bhost = {alias}\b", tail):
            bad.append(name)
check("every caller imports `from .host import host`", not bad, str(bad))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all checks passed")
