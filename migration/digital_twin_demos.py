"""The standard demographics a cohort is built from.

Jenna, 2026-11-03 (the Pilot Testing cohort builder): "this should be
where you select the demographics for the cohort, not write them in.
should be selected from our standard demographics." The list is the
panel's own demographic vocabulary (her demos.csv of 2026-11-03): twelve
categories, the values exactly as the twins carry them, so a selection
matches a twin's rows by name. Within a category the picked values are
OR (18-24 or 25-34); across categories they are AND (men who are 18 to
34 and speak Spanish).

This module is the single source: the page's picker is generated from
``DEMOS`` (``scripts/test_digital_twin_demos.py`` holds the two in
step), the pilot setup stores a cohort's choice as ``demos``
({KEY: [values]}), and the panel reads a filter, a share and seats from
it. A category a twin has no rows for falls back to the panel-wide share
in ``SHARE`` (from the same file); the children categories condition on
the twin's parents.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

# key, the twin's demographics category, the picker label, the values in picker order
DEMOS = [
    {'key': 'GENDER', 'cat': 'gender', 'label': 'Gender',
     'values': ['Female', 'Male', 'Non-Binary', 'Trans Female', 'Trans Male']},
    {'key': 'AGE', 'cat': 'age', 'label': 'Age',
     'values': ['17 and Under', '18-24', '25-34', '35-44', '45-54', '55-64', '65 or Older']},
    {'key': 'ETHNICITY', 'cat': 'ethnicity', 'label': 'Ethnicity',
     'values': ['White', 'Hispanic or Latino', 'Black or African American', 'Asian', 'Another Race/Ethnicity']},
    {'key': 'PRIMARY_LANGUAGE', 'cat': 'language', 'label': 'Primary language',
     'values': ['English', 'Spanish', 'Chinese', 'Other']},
    {'key': 'INCOME', 'cat': 'income', 'label': 'Household income',
     'values': ['Less than $25,000', '$25,000 - $49,999', '$50,000 - $74,999', '$75,000 - $99,999', '$100,000 - $149,999',
                '$150,000 - $249,999', '$250,000 or More']},
    {'key': 'EDUCATION', 'cat': 'education', 'label': 'Education',
     'values': ['High School or Less', 'Some College / Associate Degree', 'Bachelors Degree', 'Graduate or Professional Degree']},
    {'key': 'RELATIONSHIP', 'cat': 'relationship', 'label': 'Relationship',
     'values': ['Single', 'In a Relationship', 'Married', 'Divorced or Separated', 'Widowed']},
    {'key': 'PARENTAL_STATUS', 'cat': 'parental', 'label': 'Children at home',
     'values': ['Has Children', 'No Children']},
    {'key': 'NUMBER_OF_CHILDREN', 'cat': 'number_of_children', 'label': 'Number of children',
     'values': ['1', '2', '3', '4+']},
    {'key': 'AGE_OF_CHILDREN', 'cat': 'age_of_children', 'label': 'Age of children',
     'values': ['Under 3', '3 to 5', '6 to 10', '11 to 13', '14 to 17']},
    {'key': 'OCCUPATION', 'cat': 'occupation', 'label': 'Occupation',
     'values': ['Management, Business & Professional', 'Healthcare Practitioners or Support', 'Sales & Retail',
                'Education or Library Services', 'Service & Hospitality', 'Science, Technology & Technical Professions',
                'Skilled Trades/Construction or Maintenance', 'Agriculture & Outdoor', 'Transportation & Logistics',
                'Manufacturing & Production', 'Public Safety & Protective Services', 'Legal', 'Other']},
    {'key': 'SEXUAL_ORIENTATION', 'cat': 'sexual_orientation', 'label': 'Sexual orientation',
     'values': ['Straight / Heterosexual', 'Gay or Lesbian', 'Another Sexual Orientation']},
]
KEYS = [d['key'] for d in DEMOS]
_BY_KEY = {d['key']: d for d in DEMOS}

# Panel-wide share of each value (percent of the category's answered rows,
# from demos.csv 2026-11-03). Used only when a twin carries no rows for the
# category. The children categories are shares among parents.
SHARE = {
    'GENDER': {'Female': 49.43, 'Male': 45.11, 'Trans Male': 2.19, 'Trans Female': 1.67, 'Non-Binary': 1.6},
    'AGE': {'25-34': 30.69, '18-24': 18.45, '35-44': 18.43, '45-54': 11.93, '17 and Under': 8.99, '55-64': 7.4, '65 or Older': 4.12},
    'ETHNICITY': {'White': 63.53, 'Another Race/Ethnicity': 12.79, 'Hispanic or Latino': 10.55, 'Black or African American': 7.72, 'Asian': 5.41},
    'PRIMARY_LANGUAGE': {'Other': 65.84, 'English': 26.65, 'Spanish': 7.31, 'Chinese': 0.2},
    'INCOME': {'$50,000 - $74,999': 27.05, '$75,000 - $99,999': 23.21, '$100,000 - $149,999': 21.92, '$150,000 - $249,999': 15.52,
               '$250,000 or More': 11.85, '$25,000 - $49,999': 0.46, 'Less than $25,000': 0.01},
    'EDUCATION': {'Bachelors Degree': 64.05, 'High School or Less': 30.59, 'Graduate or Professional Degree': 4.82, 'Some College / Associate Degree': 0.55},
    'RELATIONSHIP': {'Single': 37.44, 'In a Relationship': 27.22, 'Married': 24.82, 'Divorced or Separated': 10.52, 'Widowed': 0.01},
    'PARENTAL_STATUS': {'No Children': 64.77, 'Has Children': 35.23},
    'NUMBER_OF_CHILDREN': {'1': 35.65, '2': 42.71, '3': 18.03, '4+': 3.65},            # among parents
    'AGE_OF_CHILDREN': {'Under 3': 17.84, '3 to 5': 20.41, '6 to 10': 17.89, '11 to 13': 14.56, '14 to 17': 29.31},   # among parents
    'OCCUPATION': {'Other': 32.27, 'Management, Business & Professional': 24.01, 'Healthcare Practitioners or Support': 11.25, 'Sales & Retail': 10.87,
                   'Education or Library Services': 8.09, 'Service & Hospitality': 2.89, 'Science, Technology & Technical Professions': 1.99,
                   'Skilled Trades/Construction or Maintenance': 1.79, 'Agriculture & Outdoor': 1.68, 'Transportation & Logistics': 1.6,
                   'Manufacturing & Production': 1.52, 'Public Safety & Protective Services': 1.48, 'Legal': 0.57},
    'SEXUAL_ORIENTATION': {'Straight / Heterosexual': 86.81, 'Gay or Lesbian': 11.85, 'Another Sexual Orientation': 1.3},
}
SPANISH_AT_HOME = 0.72     # the share of US Hispanic adults who speak Spanish at home


def _norm(v) -> str:
    s = str(v or '').replace('\u2013', '-').replace('\u2014', '-').strip().lower()
    s = re.sub(r"\s*-\s*", '-', s)
    s = re.sub(r"\s*/\s*", '/', s)
    s = s.replace("'", '')
    return ' '.join(s.split())


def canon_value(key: str, value) -> Optional[str]:
    """The picker's spelling of a value, or None when it is not one."""
    d = _BY_KEY.get(str(key or '').upper())
    if not d:
        return None
    want = _norm(value)
    for v in d['values']:
        if _norm(v) == want:
            return v
    # the few aliases the panel file carries
    alias = {'GENDER': {'women': 'Female', 'woman': 'Female', 'men': 'Male', 'man': 'Male', 'nonbinary': 'Non-Binary'},
             'PARENTAL_STATUS': {'yes': 'Has Children', 'no': 'No Children', 'parents': 'Has Children', 'no kids': 'No Children'},
             'EDUCATION': {'bachelors degree': 'Bachelors Degree', 'bachelor degree': 'Bachelors Degree', 'graduate or professional degree': 'Graduate or Professional Degree',
                           'some college/associate degree': 'Some College / Associate Degree'},
             'RELATIONSHIP': {'single (not living with a partner)': 'Single'},
             'SEXUAL_ORIENTATION': {'lgbtq+': 'Gay or Lesbian', 'lgbtq': 'Gay or Lesbian', 'straight': 'Straight / Heterosexual', 'heterosexual': 'Straight / Heterosexual'},
             'AGE': {'65+': '65 or Older', 'under 18': '17 and Under'}}.get(d['key'], {})
    return alias.get(want)


def clean_demos(demos) -> dict:
    """{KEY: [canonical values]} in picker order; unknown keys and values dropped."""
    out = {}
    if not isinstance(demos, dict):
        return out
    for d in DEMOS:
        raw = demos.get(d['key'])
        if raw is None:
            raw = demos.get(d['key'].lower()) or demos.get(d['cat'])
        if raw is None:
            continue
        vals = raw if isinstance(raw, (list, tuple)) else [raw]
        picked = []
        for v in vals:
            c = canon_value(d['key'], v)
            if c and c not in picked:
                picked.append(c)
        if picked:
            out[d['key']] = [v for v in d['values'] if v in picked]
    return out


# ----------------------------------------------------------------- words
_AGE_BOUNDS = {'17 and Under': (13, 17), '18-24': (18, 24), '25-34': (25, 34), '35-44': (35, 44), '45-54': (45, 54), '55-64': (55, 64), '65 or Older': (65, 80)}
_INCOME_BOUNDS = {'Less than $25,000': (0, 25_000), '$25,000 - $49,999': (25_000, 50_000), '$50,000 - $74,999': (50_000, 75_000),
                  '$75,000 - $99,999': (75_000, 100_000), '$100,000 - $149,999': (100_000, 150_000), '$150,000 - $249,999': (150_000, 250_000),
                  '$250,000 or More': (250_000, 10_000_000)}


def _runs(values: list, order: list) -> list:
    """Contiguous runs of picked values, in the category's order."""
    idx = [order.index(v) for v in values if v in order]
    idx.sort()
    runs, cur = [], []
    for i in idx:
        if cur and i != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    return [[order[i] for i in r] for r in runs]


def _k(n: int) -> str:
    return f"${n // 1000}k" if n < 1_000_000 else f"${n // 1_000_000}M"


def age_words(values: list) -> str:
    order = _BY_KEY['AGE']['values']
    bits = []
    for run in _runs(values, order):
        lo = _AGE_BOUNDS[run[0]][0]
        hi = _AGE_BOUNDS[run[-1]][1]
        if run[0] == '17 and Under' and run[-1] == '17 and Under':
            bits.append('17 and under')
        elif run[-1] == '65 or Older':
            bits.append(f"{lo} and older" if run[0] != '17 and Under' else 'all ages')
        elif run[0] == '17 and Under':
            bits.append(f"{hi} and under")
        else:
            bits.append(f"{lo} to {hi}")
    return ' or '.join(bits)


def income_words(values: list) -> str:
    order = _BY_KEY['INCOME']['values']
    bits = []
    for run in _runs(values, order):
        lo = _INCOME_BOUNDS[run[0]][0]
        hi = _INCOME_BOUNDS[run[-1]][1]
        if lo == 0 and hi >= 10_000_000:
            continue
        if lo == 0:
            bits.append(f"household income under {_k(hi)}")
        elif hi >= 10_000_000:
            bits.append(f"household income {_k(lo)} and up")
        else:
            bits.append(f"household income {_k(lo)} to {_k(hi)}")
    return ' or '.join(bits)


def _gender_words(values: list) -> str:
    w = {'Female': 'women', 'Male': 'men', 'Non-Binary': 'non-binary people', 'Trans Female': 'trans women', 'Trans Male': 'trans men'}
    return ' or '.join(w.get(v, v.lower()) for v in values)


def demos_words(demos: dict) -> str:
    """The selection in plain words, for the seat cards and the page:
    'men, 18 to 34, Spanish speakers, with children at home'."""
    d = clean_demos(demos)
    bits = []
    if d.get('GENDER'):
        bits.append(_gender_words(d['GENDER']))
    if d.get('AGE'):
        bits.append(age_words(d['AGE']))
    if d.get('ETHNICITY'):
        bits.append(' or '.join(d['ETHNICITY']))
    if d.get('PRIMARY_LANGUAGE'):
        langs = [v for v in d['PRIMARY_LANGUAGE'] if v != 'Other']
        if langs:
            bits.append(' or '.join(langs) + ' speakers')
        elif d['PRIMARY_LANGUAGE']:
            bits.append('speakers of another language')
    if d.get('INCOME'):
        w = income_words(d['INCOME'])
        if w:
            bits.append(w)
    if d.get('EDUCATION'):
        w = {'High School or Less': 'high school or less', 'Some College / Associate Degree': 'some college',
             'Bachelors Degree': 'college graduates', 'Graduate or Professional Degree': 'graduate degrees'}
        bits.append(' or '.join(w.get(v, v.lower()) for v in d['EDUCATION']))
    if d.get('RELATIONSHIP'):
        bits.append(' or '.join(v.lower() for v in d['RELATIONSHIP']))
    if d.get('PARENTAL_STATUS'):
        if d['PARENTAL_STATUS'] == ['Has Children']:
            bits.append('with children at home')
        elif d['PARENTAL_STATUS'] == ['No Children']:
            bits.append('without children')
    if d.get('NUMBER_OF_CHILDREN'):
        n = d['NUMBER_OF_CHILDREN']
        bits.append((' or '.join(n) + (' child' if n == ['1'] else ' children')))
    if d.get('AGE_OF_CHILDREN'):
        bits.append('children ' + ' or '.join(v.lower() for v in d['AGE_OF_CHILDREN']))
    if d.get('OCCUPATION'):
        bits.append('working in ' + ' or '.join(v.lower() if v != 'Other' else 'other fields' for v in d['OCCUPATION']))
    if d.get('SEXUAL_ORIENTATION'):
        w = {'Straight / Heterosexual': 'straight', 'Gay or Lesbian': 'gay or lesbian', 'Another Sexual Orientation': 'another sexual orientation'}
        bits.append(' or '.join(w.get(v, v.lower()) for v in d['SEXUAL_ORIENTATION']))
    return ', '.join(b for b in bits if b)


def suggest_name(demos: dict) -> str:
    """A short name for the cohort from its selection ('Men 18 to 34')."""
    d = clean_demos(demos)
    bits = []
    if d.get('GENDER'):
        bits.append(_gender_words(d['GENDER']).capitalize())
    if d.get('AGE'):
        bits.append(age_words(d['AGE']))
    if d.get('PRIMARY_LANGUAGE') and [v for v in d['PRIMARY_LANGUAGE'] if v != 'Other']:
        bits.append(' or '.join(v for v in d['PRIMARY_LANGUAGE'] if v != 'Other') + ' speakers')
    if d.get('ETHNICITY') and not bits:
        bits.append(' or '.join(d['ETHNICITY']))
    if d.get('PARENTAL_STATUS') == ['Has Children']:
        bits.append('with kids')
    if not bits:
        w = demos_words(d)
        return (w[:1].upper() + w[1:])[:60] if w else ''
    return ' '.join(bits)[:60]


# ------------------------------------------------------------- matching
def _income_bounds_of(label: str):
    nums = [int(x.replace(',', '')) for x in re.findall(r"\$?\s?(\d{1,3}(?:,\d{3})+|\d{4,7})", str(label or ''))]
    low = str(label or '').lower()
    if not nums:
        return None
    if len(nums) >= 2:
        return min(nums), max(nums) + 1
    if re.search(r"less than|under|below", low):
        return 0, nums[0]
    if re.search(r"or more|\+|over|above|and up", low):
        return nums[0], 10_000_000
    return nums[0], nums[0] + 1


def _age_bounds_of(label: str):
    s = str(label or '')
    m = re.search(r"(\d{2})\s*(?:-|\u2013|to)\s*(\d{2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{2})\s*(?:\+|or older|and older|and up)", s, re.I)
    if m:
        return int(m.group(1)), 99
    m = re.search(r"(?:under|and under)\s*(\d{2})|(\d{2})\s*and under", s, re.I)
    if m:
        return 0, int(m.group(1) or m.group(2))
    return None


def value_pred(key: str, values: list) -> Callable[[str], bool]:
    """True for a twin row label that belongs to any of the picked values."""
    key = str(key or '').upper()
    picked = [canon_value(key, v) for v in (values or [])]
    picked = [v for v in picked if v]
    if not picked:
        return lambda label: True
    if key == 'AGE':
        spans = [_AGE_BOUNDS[v] for v in picked]

        def pred(label):
            b = _age_bounds_of(label)
            if not b:
                return canon_value('AGE', label) in picked
            return any(not (b[1] < lo or b[0] > hi) for lo, hi in spans)
        return pred
    if key == 'INCOME':
        spans = [_INCOME_BOUNDS[v] for v in picked]

        def pred(label):
            b = _income_bounds_of(label)
            if not b:
                return canon_value('INCOME', label) in picked
            return any(not (b[1] <= lo or b[0] >= hi) for lo, hi in spans)
        return pred
    if key == 'SEXUAL_ORIENTATION':
        queer = [v for v in picked if v != 'Straight / Heterosexual']

        def pred(label):
            c = canon_value('SEXUAL_ORIENTATION', label)
            if c in picked:
                return True
            # a twin carries one LGBTQ+ row for every non-straight answer
            return bool(queer) and bool(re.search(r"lgbtq|gay|lesbian|bisexual|queer|another sexual", str(label or ''), re.I))
        return pred
    if key == 'PARENTAL_STATUS':
        want_kids = 'Has Children' in picked

        def pred(label):
            l = str(label or '')
            has = bool(re.search(r"has child|have child|with child|parent of|children at home|kids", l, re.I)) and not re.search(r"\bno\b|without|not a parent|non-?parent", l, re.I)
            return has if want_kids else (not has and not re.search(r"prefer", l, re.I))
        return pred
    if key == 'GENDER':
        low = {_norm(v) for v in picked}

        def pred(label):
            c = canon_value('GENDER', label)
            return (c in picked) if c else (_norm(label) in low)
        return pred
    norm_picked = {_norm(v) for v in picked}
    return lambda label: (canon_value(key, label) in picked) or (_norm(label) in norm_picked)


def fallback_share(key: str, values: list) -> float:
    """The panel-wide share of the picked values (0 to 1)."""
    key = str(key or '').upper()
    table = SHARE.get(key) or {}
    tot = sum(table.values()) or 100.0
    hit = sum(table.get(canon_value(key, v) or '', 0.0) for v in (values or []))
    return max(0.001, min(1.0, hit / tot)) if hit else 0.05


def gender_codes(values: list) -> list:
    code = {'Female': 'F', 'Male': 'M', 'Non-Binary': 'X', 'Trans Female': 'F', 'Trans Male': 'M'}
    out = []
    for v in values or []:
        c = code.get(canon_value('GENDER', v) or '')
        if c and c not in out:
            out.append(c)
    return out


def age_span(values: list):
    """(lo, hi) covering the picked buckets, for the seat ages."""
    spans = [_AGE_BOUNDS[canon_value('AGE', v)] for v in (values or []) if canon_value('AGE', v)]
    if not spans:
        return None
    return min(s[0] for s in spans), max(s[1] for s in spans)


def public_demos() -> list:
    """The picker's list for the page: key, label, values."""
    return [{'key': d['key'], 'label': d['label'], 'values': list(d['values'])} for d in DEMOS]
