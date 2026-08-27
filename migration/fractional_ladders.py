"""Fractional-part ladder detection (2026-08-26 Liz QA escalation).

THE DEFECT CLASS (seeded value ladders): large row clusters share an
IDENTICAL 4-decimal fractional part at integer-stepped values, e.g.
TALENT rows at 67.8912 / 55.8912 / 54.8912 / ... / 3.8912, or clean
unit-step runs like 4.1847 / 3.1847 / 2.1847 / 1.2847 / 0.1847. Born
in row-by-row model output: when a category chunk is reasoned in one
call, the model reuses the same fractional part across many rows
(laziness), stepping only the integer part. Every existing dejitter
pass keys on EXACT 4dp identity or 2dp display identity, so
same-suffix-different-integer ladders sailed through untouched
(escalated file: BETHENNY FRANKEL, Bethenny - Avid Fan.csv, 2026-08-26,
run 3jEG3Kw76rpoZA - .2847 x76 file-wide, TALENT .8234 x16).

Detection is shared by three consumers (single implementation, no
drift):
  * migration/post_generation_enforcers.dejitter_fractional_ladders
    (the deterministic fixer, wired into run_all_enforcers AND
    run_write_safety_net so cut paths get it too)
  * migration/final_ship_gate._check_i14 (blocking terminal invariant)
  * migration/profile_writer step 6.8 auto-remediation (I14 is in
    _AUTOFIX_GATE_CODES)
plus the at-birth guard `deladder_decision_map` that re-salts model
decision batches inside apply_avid_transform / cut transforms before
values ever land in a frame.

THRESHOLDS (empirical, 2026-08-26). Measured on six known-good files
(BETHENNY FRANKEL - Avid Fan generations Aug 24-25, its parent TU,
Danny Go - Avid Fan; ~14.2K in-scope rows each): organic max suffix
multiplicity is 9-10 file-wide and 3-4 within a category (even in
2,100-row TALENT / 1,900-row MPB blocks). The escalated file showed 76
file-wide and 16 in-category. The formulas below sit ~2x above the
organic ceiling and ~2x below the observed defect floor:

  per-category:  flag suffix shared by >= 6 + cat_rows // 1000 rows
  file-wide:     flag suffix shared by >= 20 + in_scope_rows // 2000
  stepped rule:  flag suffix shared by >= 5 rows spanning >= 4 distinct
                 integer parts, REGARDLESS of category size (2026-08-27
                 Alofoke avid TALENT: six rows at .7343 across integers
                 20/16/14/10/5/1 slid under the size-scaled threshold
                 of 8 in a 2,355-row category)

At 4dp there are 10,000 possible suffixes; for a 2,000-row category
the expected multiplicity per suffix is 0.2, so P(one suffix reaching
8) is ~1e-9 per suffix - organic false positives are effectively
impossible at these bars. The stepped rule is even safer: it requires
the shared suffix AND an integer-stepped structure that organic
reasoning never produces (known-good corpus max: 3-4 shared suffixes
in-category).

Twin: bg-webapp/migration/fractional_ladders.py must stay
byte-identical (scripts/test_module_twin_sync.py pins it).
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

# Categories never in scope: demo sums are sacred (renormalized to
# ~100), LOCATION carries gen-pop DMA structure, fan anchors and
# metadata rows aren't brand grids.
LADDER_EXEMPT_CATS = frozenset({
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "PARENTAL_STATUS", "PARENTAL STATUS", "RELATIONSHIP",
    "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION",
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT",
    "INPUT_METADATA", "INPUT METADATA",
    "LOCATION",
    "AVID FAN", "CASUAL FAN",
})

PERCAT_BASE = 6
FILEWIDE_BASE = 20
# Within a SINGLE model decision batch (one category's returned rows)
# shared suffixes are never organic at 4 members; the at-birth guard
# is deliberately more aggressive than the file-level detector.
DECISION_BATCH_MIN = 4
# Stepped-ladder structural rule (2026-08-27, Liz: Alofoke avid TALENT
# six rows at .7343 across integer steps 20/16/14/10/5/1 shipped past
# I14). The count thresholds above scale UP with category size
# (2,355-row TALENT -> threshold 8), so a 6-row ladder slid under the
# bar; and the at-birth guard only sees ONE ~200-row chunk at a time,
# so a ladder accumulated ACROSS chunks never reaches DECISION_BATCH_MIN
# within any single batch. The stepped rule keys on STRUCTURE instead
# of count: a shared 4dp suffix at >= PERCAT_STEPPED_MIN members
# spanning >= PERCAT_STEPPED_INTS distinct integer parts is flagged
# regardless of category size. Organic odds of five rows sharing an
# exact 4dp suffix at four+ integer levels are (1/10^4)^4-scale - the
# known-good corpus (2026-08-26 measurement) never exceeded 3-4 shared
# suffixes in-category at ANY integer spread.
PERCAT_STEPPED_MIN = 5
PERCAT_STEPPED_INTS = 4


def suffix4(bp) -> str:
    """4-digit fractional part of a BP as a string ('8912')."""
    return f"{float(bp):.4f}".split(".")[1]


def percat_threshold(cat_rows: int) -> int:
    return PERCAT_BASE + int(cat_rows) // 1000


def filewide_threshold(scope_rows: int) -> int:
    return FILEWIDE_BASE + int(scope_rows) // 2000


def ladder_in_scope(cat_u: str, bp) -> bool:
    """True when a row participates in ladder detection: non-exempt
    category, strictly interior BP (self-pins at ~100 and truly-zero
    rows are excluded), and a non-zero fractional part (integer /
    X.0000 values are the round-value class owned by the depin
    passes, not this detector)."""
    if not cat_u or cat_u in LADDER_EXEMPT_CATS:
        return False
    try:
        v = float(bp)
    except (TypeError, ValueError):
        return False
    if v <= 0.0001 or v >= 99.99:
        return False
    return suffix4(v) != "0000"


def detect_fractional_ladders(triples):
    """Detect seeded value ladders.

    `triples`: iterable of (row_id, cat_u, bp_float) - rows ALREADY
    filtered through `ladder_in_scope` (callers own scoping so the
    same rows they can fix are the rows counted).

    Returns dict:
      flagged_ids     set of row_ids in any flagged group
      percat_groups   [(cat_u, suffix, count, threshold)]
      filewide_groups [(suffix, count, threshold)]
      flagged_suffixes set of suffixes in any flagged group
      n_scope         number of in-scope rows counted
    """
    triples = list(triples)
    n_scope = len(triples)
    by_cat = defaultdict(list)
    filewide = Counter()
    for row_id, cat_u, bp in triples:
        s = suffix4(bp)
        by_cat[cat_u].append((row_id, s, float(bp)))
        filewide[s] += 1

    percat_groups = []
    flagged_ids = set()
    flagged_suffixes = set()
    for cat_u, entries in by_cat.items():
        thr = percat_threshold(len(entries))
        counts = Counter(s for _, s, _ in entries)
        for s, c in counts.items():
            hit = c >= thr
            if not hit and c >= PERCAT_STEPPED_MIN:
                # Stepped-ladder structural rule: same 4dp suffix
                # spanning distinct integer levels is fabricated
                # regardless of how large the category is.
                ints = {int(v) for _, sfx, v in entries if sfx == s}
                hit = len(ints) >= PERCAT_STEPPED_INTS
            if hit:
                percat_groups.append((cat_u, s, c, min(thr, c)))
                flagged_suffixes.add(s)
                flagged_ids.update(
                    rid for rid, sfx, _ in entries if sfx == s)

    fw_thr = filewide_threshold(n_scope)
    filewide_groups = []
    for s, c in filewide.items():
        if c >= fw_thr:
            filewide_groups.append((s, c, fw_thr))
            flagged_suffixes.add(s)
            flagged_ids.update(
                rid for rid, cat_u, bp in triples if suffix4(bp) == s)

    percat_groups.sort(key=lambda t: -t[2])
    filewide_groups.sort(key=lambda t: -t[1])
    return {
        "flagged_ids": flagged_ids,
        "percat_groups": percat_groups,
        "filewide_groups": filewide_groups,
        "flagged_suffixes": flagged_suffixes,
        "n_scope": n_scope,
    }


def _unit(seed: str) -> float:
    h = hashlib.blake2b(seed.encode("utf-8"), digest_size=8).hexdigest()
    return int(h, 16) / 0xFFFFFFFFFFFFFFFF


def deladder_decision_values(decisions: dict, subject: str, cat: str,
                             *, min_group: int = DECISION_BATCH_MIN):
    """At-birth guard for one category's model decision batch.

    `decisions`: {label: bp} as parsed from the model response. Any
    fractional suffix shared by >= `min_group` members is a laziness
    artifact, never reasoning: each member (beyond the first, which
    keeps the model's literal value so one representative of the
    model's intent survives) gets its fractional part re-drawn from a
    per-(subject, category, label) salted hash. Integer part is
    preserved - the model's MAGNITUDE call is respected; only the
    fabricated shared suffix is replaced. New values avoid exact-4dp
    collisions within the batch and avoid re-creating any flagged
    suffix.

    Returns (new_decisions, n_changed).
    """
    numeric = {}
    for label, v in decisions.items():
        try:
            numeric[label] = float(v)
        except (TypeError, ValueError):
            continue
    groups = defaultdict(list)
    for label, v in numeric.items():
        if 0.0001 < v < 99.99 and suffix4(v) != "0000":
            groups[suffix4(v)].append(label)
    bad = {s: labels for s, labels in groups.items()
           if len(labels) >= min_group}
    if not bad:
        return decisions, 0

    used4 = {round(v, 4) for v in numeric.values()}
    bad_suffixes = set(bad)
    out = dict(decisions)
    n_changed = 0
    for s, labels in bad.items():
        # Deterministic member order; the first keeps the model value.
        for label in sorted(labels)[1:]:
            old = numeric[label]
            base = int(old)  # integer part preserved
            seed = f"{subject}|{cat}|{label}|decision-deladder-v1"
            u = _unit(seed)
            frac = 0.0004 + 0.9991 * u
            cand = round(base + frac, 4)
            step = 0.0003 + 0.0011 * _unit(seed + "|step")
            ok = False
            for k in range(4000):
                c4 = round(cand, 4)
                if c4 >= base + 1:
                    cand = base + 0.0004
                    continue
                if c4 <= 0.0001:
                    cand = base + 0.0004 + k * 0.0007
                    continue
                if (c4 not in used4
                        and suffix4(c4) not in bad_suffixes):
                    ok = True
                    break
                cand = base + ((cand - base + step) % 0.9995) + 0.0003
            if not ok:
                continue
            c4 = round(cand, 4)
            used4.add(c4)
            out[label] = c4
            n_changed += 1
    return out, n_changed


def deladder_decision_map(category_decisions: dict, subject: str,
                          *, min_group: int = DECISION_BATCH_MIN,
                          verbose: bool = True):
    """Apply `deladder_decision_values` to every category in a
    {cat: {label: bp}} decision map. Returns (map, total_changed)."""
    if not category_decisions:
        return category_decisions, 0
    total = 0
    out = {}
    for cat, decisions in category_decisions.items():
        if isinstance(decisions, dict) and decisions:
            fixed, n = deladder_decision_values(
                decisions, subject, str(cat), min_group=min_group)
            out[cat] = fixed
            total += n
        else:
            out[cat] = decisions
    if total and verbose:
        print(f"    [deladder] re-salted {total} model decision value(s) "
              f"that shared fabricated fractional parts")
    return out, total
