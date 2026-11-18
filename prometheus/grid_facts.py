"""The figures already on file for a demographic grid's comparison row
(2026-11-18, Liz's nine series on Peacock).

"Give the same two figures for everyone who used Peacock in the window."
Everyone who uses a service is an audience the house already holds two
ways: the US baseline carries how many people use it (its own row for
the service), and the library carries who they are (the service's own
profile: its AGE and GENDER rows). A grid's comparison row is that
audience. It takes those figures, so the table can never disagree with
the profile a reader can open beside it; the pass's own draw for the row
is used only for what the files do not carry (how gender moves with
age).

``platform_facts(plan, s3, bucket, catalog, pma)`` reads them:
{'people': int, 'pct_women': float, 'ages': {band lo: share}, ...} or
None. Read only. Never raises. The arithmetic that applies them is
prometheus.demo_grid.cohere.
"""
from __future__ import annotations

import re

__all__ = ['platform_facts', 'US_PEOPLE']

US_PEOPLE = 329_900_000
_SERVICE_COLUMNS = ('STREAMING/PLATFORM', 'STREAMING PLATFORM', 'STREAMING VIDEO', 'STREAMING/MUSIC', 'STREAMING MUSIC',
                    'VIRTUAL MVPD/FAST', 'VIRTUAL MVPD FAST', 'FAST PLATFORM', 'SOCIAL MEDIA', 'APP/PLATFORM',
                    'APP/PLATFORM USAGE')


def _fold(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


def _service_profile(service, catalog):
    """The library's own profile of the service: the base file (no cut
    suffix) whose name is the service, the newest when there are several."""
    want = _fold(service)
    best = None
    for j in catalog or []:
        if not isinstance(j, dict):
            continue
        name = str(j.get('display_name') or j.get('subject') or '')
        if ' - ' in name or _fold(name) != want or not str(j.get('s3_key') or '').strip():
            continue
        if best is None or str(j.get('last_modified') or '') > str(best.get('last_modified') or ''):
            best = j
    return best


def platform_facts(plan, s3, bucket, catalog, pma):
    try:
        service = str((plan or {}).get('service') or '').strip()
        if not service or not (plan or {}).get('platform'):
            return None
        out = {}
        try:
            gp = pma.load_genpop_map(s3, bucket) or {}
            brand = pma._norm_brand(service)
            for col in _SERVICE_COLUMNS:
                v = gp.get((col, brand))
                if isinstance(v, (int, float)) and 0 < v <= 100:
                    out['people'] = int(round(float(v) / 100.0 * US_PEOPLE))
                    break
        except Exception:
            pass
        try:
            prof = _service_profile(service, catalog)
            if prof:
                df, _etag = pma.load_profile_df(s3, bucket, prof['s3_key'])
                bp = pma._bp_col(df)
                from . import demo_grid as _dg
                ages, female, male = {}, None, None
                for col, label, raw in zip(df['Column'].tolist(), df['Value'].tolist(), df[bp].tolist()):
                    c = pma._norm_cat(col)
                    v = pma._parse_bp(raw)
                    if v is None or v <= 0:
                        continue
                    if c == 'AGE':
                        i = _dg._band_index(str(label).replace('OR OLDER', 'and over').replace('AND UNDER', 'and under'), plan)
                        if i is not None:
                            ages[i] = ages.get(i, 0.0) + float(v)
                    elif c == 'GENDER':
                        lab = str(label or '').strip().upper()
                        if lab == 'FEMALE':
                            female = float(v)
                        elif lab == 'MALE':
                            male = float(v)
                if len(ages) == len(plan.get('bands') or []) and sum(ages.values()) > 0:
                    tot = sum(ages.values())
                    out['ages'] = {int(i): ages[i] / tot for i in ages}
                if female is not None and 0 < female < 100:
                    out['pct_women'] = female
                    out['pct_men'] = male
                out['profile'] = str(prof.get('display_name') or '')
        except Exception:
            pass
        return out or None
    except Exception:
        return None
