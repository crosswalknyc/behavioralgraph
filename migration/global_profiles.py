"""Worldwide (GLOBAL) profiles: the US profile inside the worldwide one.

Jenna, 2026-11-23 (verbatim): *"it should encompass everything from the
us cut PLUS all other countries"*. A worldwide file ('<Subject> - Global',
any file whose SAMPLE SIZE row carries `| MARKET: GLOBAL`) counts the
subject's US audience plus everyone outside the US, so read at the people
level (Projection, never the share: the two files have different bases)
every row of the subject's US profile sits in it with at least its US
people:

  * every brand, platform, media, purchase, talent and event row
  * every US city (the US profile's DMAs, as '<DMA>, USA')
  * every US demographic bucket, inside the worldwide bucket that holds it

The ship gate checks it (I21 worldwide superset) and the writer's autofix
pass corrects it in place before the gate re-runs (no hold, no rebuild):
a row at or under its US people goes to its US people plus the people
outside the US who engaged with it, reasoned row by row against the
file's own country mix at the low end of the range (a review pass trims
the largest), never a multiplier. When no reasoning is reachable the
deterministic fallback is a near-zero, subject-salted increment, so the
file always ships with the US audience inside it.

Pure functions over (Column, Value, people) tuples serve the gate; the
fixer works on the writer's DataFrame. Nothing here writes to S3.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from migration import international_profiles as _ip
except ImportError:  # pragma: no cover
    import international_profiles as _ip  # type: ignore

GLOBAL = "GLOBAL"
BUCKET = "dashboard-inputs"
DEMO_COLS = ("AGE", "GENDER", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
             "PARENTAL_STATUS", "RELATIONSHIP", "SEXUAL_ORIENTATION")
META_COLS = frozenset({"BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT", "AVID FAN",
                       "CASUAL FAN", "INPUT_METADATA"})
SKIP_COLS = META_COLS | frozenset(DEMO_COLS) | {"LOCATION", "AGE_OF_CHILDREN", "NUMBER_OF_CHILDREN",
                                                "PRIMARY_LANGUAGE"}
# a US column the worldwide file carries under its canonical twin (the US file's duplicate is never copied)
COLUMN_TWINS = {"SPORTS ORGANIZATION": "SPORTS ORGANIZATIONS", "APP/PLATFORM": "APP/PLATFORM USAGE"}
NEAR_RATIO = 1.02            # a row this close above its US people carries no visible non-US audience yet
_MARKET_RX = re.compile(r"\|\s*MARKET:\s*([^|]+)", re.I)
_STAMP_RX = re.compile(r"_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2})?$")
_KEY_TS_RX = re.compile(r"_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})\.csv$", re.I)

# US demographic buckets and the worldwide bucket(s) that hold them (people level)
US_TO_WORLD = {
    "AGE": [(["17 AND UNDER"], ["UNDER 18"]), (["18-24"], ["18-24"]), (["25-34"], ["25-34"]),
            (["35-44"], ["35-44"]), (["45-54"], ["45-54"]), (["55-64"], ["55-64"]),
            (["65 OR OLDER"], ["65+"])],
    "GENDER": [(["MALE"], ["MALE"]), (["FEMALE"], ["FEMALE"]), (["NON-BINARY"], ["NON-BINARY"]),
               (["TRANS FEMALE"], ["TRANS FEMALE"]), (["TRANS MALE"], ["TRANS MALE"]),
               (["PREFER NOT TO SAY"], ["PREFER NOT TO SAY"])],
    "ETHNICITY": [(["WHITE"], ["EUROPEAN HERITAGE"]), (["HISPANIC OR LATINO"], ["LATIN AMERICAN HERITAGE"]),
                  (["BLACK OR AFRICAN AMERICAN"], ["AFRICAN HERITAGE"]), (["ASIAN"], ["ASIAN HERITAGE"]),
                  (["ANOTHER RACE/ETHNICITY"], ["MIXED / OTHER", "MIDDLE EASTERN / NORTH AFRICAN HERITAGE"])],
    "EDUCATION": [(["BACHELORS DEGREE"], ["BACHELORS DEGREE"]),
                  (["GRADUATE OR PROFESSIONAL DEGREE"], ["MASTERS DEGREE", "DOCTORATE / PHD"]),
                  (["HIGH SCHOOL OR LESS"], ["SECONDARY SCHOOL OR LESS"]),
                  (["SOME COLLEGE / ASSOCIATE DEGREE"], ["VOCATIONAL OR TECHNICAL QUALIFICATION"]),
                  (["PREFER NOT TO SAY"], ["PREFER NOT TO SAY"])],
    "INCOME": [(["LESS THAN $25,000"], ["UNDER US$10,000", "US$10,000 - US$24,999"]),
               (["$25,000 - $49,999"], ["US$25,000 - US$49,999"]),
               (["$50,000 - $74,999"], ["US$50,000 - US$74,999"]),
               (["$75,000 - $99,999"], ["US$75,000 - US$99,999"]),
               (["$100,000 - $149,999"], ["US$100,000 - US$149,999"]),
               (["$150,000 - $249,999", "$250,000 OR MORE"], ["US$150,000+"]),
               (["PREFER NOT TO SAY"], ["PREFER NOT TO SAY"])],
}
SAME_LABEL_DEMOS = ("OCCUPATION", "PARENTAL_STATUS", "RELATIONSHIP", "SEXUAL_ORIENTATION")
# the worldwide label written when a bucket has to be added (title case, as the GLOBAL schema writes it)
WORLD_LABEL = {"UNDER 18": "Under 18", "TRANS MALE": "Trans Male", "TRANS FEMALE": "Trans Female",
               "NON-BINARY": "Non-Binary", "PREFER NOT TO SAY": "Prefer Not to Say", "MALE": "Male",
               "FEMALE": "Female"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _unit(*parts) -> float:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(1 << 48)


def _ceil4(x: float) -> float:
    return math.ceil(x * 10000.0 - 1e-7) / 10000.0


def _on_2dp(x: float) -> bool:
    return abs(round(x * 100.0) - x * 100.0) < 1e-7


def _fmt(x: float) -> str:
    return f"{x:.4f}"


def plain_name(name) -> str:
    """A key as the profile it names: folder, '.csv' and the build stamp off, underscores as spaces."""
    n = str(name or "").strip().rsplit("/", 1)[-1]
    if n.lower().endswith(".csv"):
        n = n[:-4]
    return re.sub(r"\s+", " ", _STAMP_RX.sub("", n).replace("_", " ")).strip()


def worldwide_parts(name):
    """(subject, cut) for a worldwide name, None otherwise: 'Guinness_Global_11_23_2026_20_44.csv'
    -> ('Guinness', ''), 'Guinness - Global - Avid Fan.csv' -> ('Guinness', 'Avid Fan')."""
    nm = plain_name(name)
    if not _ip.name_is_global(nm):
        return None
    parts = [p.strip() for p in nm.split(" - ")]
    seg = getattr(_ip, "_GLOBAL_NAME_SEGMENT_RE", None)
    for i, p in enumerate(parts[1:], start=1):
        if seg is not None and seg.match(p):
            return " - ".join(parts[:i]), " - ".join(parts[i + 1:])
    head = parts[0].split()
    return " ".join(head[:-1]), " - ".join(parts[1:])


def market_of_cell(cell) -> str:
    m = _MARKET_RX.search(str(cell or ""))
    if not m:
        return ""
    c = _ip.normalize_country(m.group(1).strip())
    return "" if c == "US" else c


def is_global_rows(rows, s3_key="") -> bool:
    """rows: (Column, Value) pairs. The MARKET tag on SAMPLE SIZE, the content signature, or a worldwide name."""
    pairs = [(str(c), str(v)) for c, v, *_ in rows]
    for c, v in pairs:
        if c.strip().upper() == "SAMPLE SIZE":
            m = market_of_cell(v)
            if m:
                return m == GLOBAL
            break
    try:
        if _ip.detect_country_from_pairs(pairs) == GLOBAL:
            return True
    except Exception:
        pass
    return bool(s3_key) and worldwide_parts(s3_key) is not None


def _key_ts(k):
    m = _KEY_TS_RX.search(str(k))
    if not m:
        return (0, 0, 0, 0, 0)
    mo, d, y, hh, mi = (int(x) for x in m.groups())
    return (y, mo, d, hh, mi)


def us_counterpart_key(s3_key, subject, keys):
    """The subject's US file for a worldwide file: the US Total Universe for a worldwide Total
    Universe (newest dated '<Subject>_<stamp>.csv', or an undated '<Subject>.csv'), the same cut
    of the US file for a cut of the worldwide one ('<Subject> - Avid Fan.csv' for
    '<Subject> - Global - Avid Fan.csv'). Never a worldwide file, never the file itself."""
    parts = worldwide_parts(s3_key)
    subj, cut = (parts if parts else (str(subject or "").strip(), ""))
    if not subj:
        return None
    sn = _norm(subj)
    keys = [str(k) for k in keys if str(k).lower().endswith(".csv") and "/" not in str(k)]
    if cut:
        want = _norm(f"{subj} {cut}")
        hits = [k for k in keys if k != s3_key and " - " in k and _norm(plain_name(k)) == want
                and worldwide_parts(k) is None]
        return sorted(hits, key=_key_ts)[-1] if hits else None
    hits = []
    for k in keys:
        if k == s3_key or worldwide_parts(k) is not None:
            continue
        base = k.rsplit("/", 1)[-1][:-4]
        if " - " in base:
            continue
        bn = _norm(base)
        if bn == sn or (bn.startswith(sn) and bn[len(sn):].isdigit() and len(bn) - len(sn) >= 8):
            hits.append(k)
    return sorted(hits, key=_key_ts)[-1] if hits else None


def rows_of_frame(df, universe=None):
    """(Column, Value, people) from a profile DataFrame: the Projection cell, or with ``universe``
    the share of it the row's BP says (a frame mid-pipeline may carry a stale Projection)."""
    proj = next((c for c in df.columns if "projection" in str(c).lower()), None)
    bp = next((c for c in df.columns if str(c).startswith("Brand Penetration")), None)
    out = []
    if universe and bp:
        for c, v, b in zip(df["Column"], df["Value"], df[bp]):
            x = _num(b)
            out.append((str(c).strip(), str(v).strip(), None if x is None else x / 100.0 * universe))
        return out
    for c, v, p in zip(df["Column"], df["Value"], df[proj] if proj else [None] * len(df)):
        out.append((str(c).strip(), str(v).strip(), _num(p)))
    return out


# ---------------------------------------------------------------------------
# matching a US row to the worldwide row that holds it
# ---------------------------------------------------------------------------

class _Index:
    def __init__(self, rows):
        self.by_key, self.by_col, self.cols = {}, {}, set()
        for c, v, p in rows:
            cu, vu = c.strip().upper(), v.strip().upper()
            self.cols.add(cu)
            k = (cu, vu)
            self.by_key[k] = max(self.by_key.get(k, 0.0), p or 0.0)
            self.by_col.setdefault(cu, {}).setdefault(_norm(vu), vu)

    def people(self, k):
        return self.by_key.get(k)


def _tokens(v):
    return [t for t in re.split(r"[^A-Z0-9]+", str(v or "").upper()) if t]


def _near_spelling(vu, col_values):
    """The worldwide value that is the same multi-word name with one word spelled a little
    differently ('KELLY BLUE BOOK' / 'KELLEY BLUE BOOK', 'JON STAMOS' / 'JOHN STAMOS'), or None.
    One-word names never match this way: 'Jameson' and 'Jamieson' are two brands."""
    a = _tokens(vu)
    if len(a) < 2:
        return None
    best, score = None, 0.0
    for cand in col_values:
        b = _tokens(cand)
        if len(b) != len(a):
            continue
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        if len(diff) != 1:
            continue
        x, y = diff[0]
        if x[0] != y[0] or abs(len(x) - len(y)) > 2:
            continue
        r = difflib.SequenceMatcher(None, x, y).ratio()
        if r >= 0.75 and r > score:
            best, score = cand, r
    return best


def match_row(col, val, gidx, us_norms_by_col=None):
    """The worldwide (COLUMN, VALUE) that holds a US row, or None. Exact, then case and
    punctuation insensitive in the same column (or its canonical twin when the worldwide file has
    only the twin), then a near spelling of the same multi-word name (a US typo such as
    'Kelly Blue Book' for 'Kelley Blue Book', 'Jon Stamos' for 'John Stamos')."""
    cu, vu = col.strip().upper(), val.strip().upper()
    cols = [cu] if cu in gidx.cols else []
    tw = COLUMN_TWINS.get(cu)
    if tw and tw in gidx.cols and tw not in cols:
        cols.append(tw)
    vn = _norm(vu)
    for c in cols:
        if (c, vu) in gidx.by_key:
            return (c, vu)
        hit = gidx.by_col.get(c, {}).get(vn)
        if hit is not None:
            return (c, hit)
    for c in cols:
        cand = _near_spelling(vu, list(gidx.by_col.get(c, {}).values()))
        if cand:
            return (c, cand)
    return None


def _us_location_label(label) -> str:
    return f"{str(label).strip()}, USA"


def _loc_country(label) -> str:
    try:
        return _ip.location_country(label) or ""
    except Exception:
        return ""


def _is_us_city(label, us_dmas) -> bool:
    lab = str(label).strip().upper()
    if lab in us_dmas:
        return True
    c = _loc_country(label)
    return c in ("USA", "UNITEDSTATES", "US", "UNITEDSTATESOFAMERICA", "AMERICA")


def superset_findings(g_rows, u_rows, *, is_hidden=None):
    """Every way the worldwide rows fall short of the US rows, at the people level.

    g_rows / u_rows: (Column, Value, people). Returns a dict of lists:
      missing      [ {col, val, us} ]                      US row with no worldwide row
      below        [ {col, val, us, world, target} ]       fewer people than in the US file
      equal / near [ ... ]                                 at, or within 2% above, the US people
      loc_missing  [ {val, us} ] / loc_below [ {val, us, world, target} ]
      loc_extra_us [ {val, world} ]                        a US city row that is not a US profile city
      demo_missing [ {col, us_labels, world_labels, us} ] / demo_below [ {..., world} ]
    A US row whose name is hidden today (``is_hidden(value)``) is not owed."""
    gidx = _Index(g_rows)
    us_norms = {}
    for c, v, _p in u_rows:
        us_norms.setdefault(c.strip().upper(), set()).add(_norm(v))
    out = {k: [] for k in ("missing", "below", "equal", "near", "loc_missing", "loc_below",
                           "loc_extra_us", "demo_missing", "demo_below")}
    us_dmas = set()
    for c, v, p in u_rows:
        cu = c.strip().upper()
        if cu in SKIP_COLS and cu != "LOCATION":
            continue
        up = p or 0.0
        if up <= 0:
            continue
        if cu == "LOCATION":
            us_dmas.add(v.strip().upper())
            hit = None
            for lab in (_us_location_label(v).upper(), v.strip().upper()):
                if ("LOCATION", lab) in gidx.by_key:
                    hit = ("LOCATION", lab)
                    break
            if hit is None:
                out["loc_missing"].append({"val": v, "us": up})
            elif gidx.people(hit) < up:
                out["loc_below"].append({"val": v, "us": up, "world": gidx.people(hit), "target": hit})
            continue
        if is_hidden is not None and is_hidden(v):
            continue
        hit = match_row(c, v, gidx, us_norms)
        if hit is None:
            out["missing"].append({"col": c, "val": v, "us": up})
            continue
        gp = gidx.people(hit) or 0.0
        rec = {"col": c, "val": v, "us": up, "world": gp, "target": hit}
        if gp < up:
            out["below"].append(rec)
        elif gp == up:
            out["equal"].append(rec)
        elif gp <= up * NEAR_RATIO:
            out["near"].append(rec)
    covered = {_us_location_label(d).upper() for d in us_dmas} | us_dmas
    if us_dmas:
        for c, v, p in g_rows:
            if c.strip().upper() == "LOCATION" and v.strip().upper() not in covered and _is_us_city(v, us_dmas):
                out["loc_extra_us"].append({"val": v, "world": p or 0.0})
    # demographics
    def _people(rows, cat):
        d = {}
        for c, v, p in rows:
            if c.strip().upper() == cat:
                d[v.strip().upper()] = d.get(v.strip().upper(), 0.0) + (p or 0.0)
        return d
    for cat, pairs in US_TO_WORLD.items():
        up, gp = _people(u_rows, cat), _people(g_rows, cat)
        if not up or not gp:
            continue
        for ul, gl in pairs:
            need = sum(up.get(x, 0.0) for x in ul)
            if need <= 0:
                continue
            present = [x for x in gl if x in gp]
            have = sum(gp.get(x, 0.0) for x in gl)
            rec = {"col": cat, "us_labels": ul, "world_labels": gl, "us": need, "world": have}
            if not present:
                out["demo_missing"].append(rec)
            elif have < need:
                out["demo_below"].append(rec)
    for cat in SAME_LABEL_DEMOS:
        up, gp = _people(u_rows, cat), _people(g_rows, cat)
        if not up or not gp:
            continue
        for lab, need in up.items():
            if need <= 0:
                continue
            rec = {"col": cat, "us_labels": [lab], "world_labels": [lab], "us": need, "world": gp.get(lab, 0.0)}
            if lab not in gp:
                out["demo_missing"].append(rec)
            elif gp[lab] < need:
                out["demo_below"].append(rec)
    return out


def owed(findings) -> int:
    """How many shortfalls the ship gate reports (equal and near are not shortfalls)."""
    return sum(len(findings[k]) for k in ("missing", "below", "loc_missing", "loc_below", "loc_extra_us",
                                          "demo_missing", "demo_below"))


def gate_messages(findings, limit=25):
    """(where, value, plain) for the ship gate's I21 lines, worst first, capped."""
    lines = []
    for r in sorted(findings["below"], key=lambda r: r["world"] - r["us"]):
        lines.append((f"{r['col']} / {r['val']}", f"{r['world']:,.0f} < {r['us']:,.0f}",
                      f"{r['val']} in {r['col']} holds {r['world']:,.0f} people here, fewer than the "
                      f"{r['us']:,.0f} in the US profile this worldwide file contains."))
    for r in findings["missing"]:
        lines.append((f"{r['col']} / {r['val']}", "missing",
                      f"{r['val']} in {r['col']} is in the US profile ({r['us']:,.0f} people) but not "
                      f"in this worldwide file."))
    for r in findings["loc_missing"]:
        lines.append((f"LOCATION / {r['val']}", "missing",
                      f"The US city {r['val']} ({r['us']:,.0f} people) is missing from LOCATION."))
    for r in findings["loc_below"]:
        lines.append((f"LOCATION / {r['val']}", f"{r['world']:,.0f} < {r['us']:,.0f}",
                      f"The US city {r['val']} holds {r['world']:,.0f} people here, fewer than its "
                      f"{r['us']:,.0f} in the US profile."))
    for r in findings["loc_extra_us"]:
        lines.append((f"LOCATION / {r['val']}", f"{r['world']:,.0f}",
                      f"{r['val']} is a US city row that is not one of the US profile's cities; the US "
                      f"part of LOCATION is the US profile's own cities."))
    for r in findings["demo_missing"]:
        lines.append((f"{r['col']} / {'+'.join(r['world_labels'])}", "missing",
                      f"{r['col']} has no bucket holding the US profile's {', '.join(r['us_labels'])} "
                      f"({r['us']:,.0f} people)."))
    for r in findings["demo_below"]:
        lines.append((f"{r['col']} / {'+'.join(r['world_labels'])}", f"{r['world']:,.0f} < {r['us']:,.0f}",
                      f"{r['col']} {', '.join(r['world_labels'])} holds {r['world']:,.0f} people, fewer than "
                      f"the US profile's {', '.join(r['us_labels'])} ({r['us']:,.0f})."))
    extra = max(0, len(lines) - limit)
    return lines[:limit], extra


# ---------------------------------------------------------------------------
# reasoning the people outside the US (row by row, the low end)
# ---------------------------------------------------------------------------

_REASON_CACHE = {}           # (subject, column, value) -> non-US share, for the life of the process
_CHUNK = 70


def reasoning_enabled() -> bool:
    if (os.environ.get("REGRESSION_TEST_MODE") or "").strip() == "1":
        return False
    return (os.environ.get("GLOBAL_SUPERSET_REASONING") or "1").strip().lower() not in ("0", "false", "off", "no")


def _model_call():
    """fn(system, user, n) -> text, through the house pool when it is here, else the house client."""
    keys = []
    try:
        from migration.avid_key_pool import load_keys
        keys = list(load_keys() or [])
    except Exception:
        keys = []
    direct = None
    for mod in ("scripts.synth_engine_row_by_row", "synth_engine_row_by_row"):
        try:
            direct = getattr(__import__(mod, fromlist=["_direct_claude_call"]), "_direct_claude_call")
            break
        except Exception:
            continue
    if direct and keys:
        return lambda system, user, n: direct(system, user, keys[n % len(keys)], max_tokens=8000,
                                              temperature=0.3)
    try:
        from migration.claude_client import claude_messages
    except Exception:
        return None
    return lambda system, user, n: claude_messages(system=system, user=user, max_tokens=8000,
                                                   temperature=0.3)


_SYSTEM = ("You size digital audiences across countries for a behavioral panel. You reason item by item "
           "and return strict JSON only. Every value is a percent of people (0 to 100) with up to 4 "
           "decimals; never round numbers, never two items with the same value, never a template or a "
           "flat ratio.")


def _head(ctx) -> str:
    return (
        f"SUBJECT: {ctx['subject']} ({ctx.get('category') or 'profile'}). WORLDWIDE audience: people who "
        f"engaged with it online in the window. {ctx['universe']:,} people: {ctx['us_people']:,} in the US "
        f"and {ctx['non_us']:,} outside the US.\n"
        f"Where the {ctx['non_us']:,} non-US people live: {ctx['mix']}.\n\n"
        "For each item, us_pct is the measured share of the US part of this audience that engaged with it "
        "(for a demographic item: the share of the US part in that bucket). Estimate non_us_pct: the share "
        f"of the NON-US part (the {ctx['non_us']:,} people) that engaged with the same item (or sits in the "
        "same bucket) in the same window. Reason from where the item exists and is popular, market by "
        "market, using the mix above:\n"
        "- A US-only store, bank, insurer, telco, service, college, local station, regional chain, US team "
        "or league, or a personality known only in the US: near zero outside the US (about 0.005 to 0.5).\n"
        "- A brand or service present in some of these markets: its reach there times the share of the "
        "non-US audience living there.\n"
        "- A global platform, global brand, Hollywood film star, global musician or global athlete: can be "
        "as high as, or higher than, its US level.\n"
        "- A name native to the markets above can be far higher outside the US than inside it.\n"
        "Continuous values, every item reasoned on its own. Use the LOW end of the defensible range.\n\n")


_REVIEW = ("REVIEW PASS. Each item carries a first estimate (first). Check it and return the LOW end of the "
           "defensible range (the bottom third), never the figure a brand would brag about. US broadcast "
           "networks, US cable channels, US news sites and papers, US sports leagues and their events, US "
           "politicians and US-only personalities have modest digital reach outside the US: a few percent "
           "of these non-US people at most, unless the item really is a global name. Keep a genuinely "
           "global or non-US item where its markets put it.\n\n")


def _parse(text):
    if not text:
        return {}
    m = re.search(r"\{[\s\S]*\}", str(text))
    if not m:
        return {}
    try:
        doc = json.loads(m.group(0))
    except Exception:
        return {}
    out = {}
    for x in doc.get("items") or []:
        try:
            i, v = int(x.get("id")), float(x.get("non_us_pct"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0.0 <= v <= 100.0:
            out[i] = v
    return out


def _ask(call, ctx, items, review, n):
    lines = [_head(ctx) + (_REVIEW if review else ""),
             "ITEMS (id | item | categories | us_pct" + (" | first" if review else "") + "):"]
    for it in items:
        tail = f" | first={it['first']:.4f}" if review else ""
        lines.append(f"- {it['id']} | {it['label']} | {', '.join(it['cats'][:4])} | us_pct={it['us_pct']:.4f}{tail}")
    lines.append('\nReturn ONLY: {"items":[{"id":<id>,"non_us_pct":<float>}, ...]} with every id above.')
    body = "\n".join(lines)
    for attempt in range(2):
        try:
            got = _parse(call(_SYSTEM, body, n + attempt))
            if got:
                return got
        except Exception as e:
            print(f"   [global-superset] reasoning chunk error ({type(e).__name__}: {str(e)[:120]})")
    return {}


def house_reason(items, ctx, verbose=True):
    """{id: non-US share} for items [{id, label, cats, us_pct}], first pass then the low-end review
    of the largest. {} when no model is reachable (the caller's fallback applies)."""
    if not items or not reasoning_enabled():
        return {}
    call = _model_call()
    if call is None:
        return {}
    out = {}
    chunks = [items[i:i + _CHUNK] for i in range(0, len(items), _CHUNK)]
    with ThreadPoolExecutor(max_workers=max(1, min(12, len(chunks)))) as ex:
        futs = {ex.submit(_ask, call, ctx, c, False, n): c for n, c in enumerate(chunks)}
        for f in as_completed(futs):
            want = {it["id"] for it in futs[f]}
            out.update({i: v for i, v in (f.result() or {}).items() if i in want})
    big = [dict(it, first=out[it["id"]]) for it in items if out.get(it["id"], 0.0) >= 1.5]
    if big:
        rchunks = [big[i:i + _CHUNK] for i in range(0, len(big), _CHUNK)]
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(rchunks)))) as ex:
            futs = {ex.submit(_ask, call, ctx, c, True, 1000 + n): c for n, c in enumerate(rchunks)}
            for f in as_completed(futs):
                for i, v in (f.result() or {}).items():
                    if i in out and v <= out[i]:
                        out[i] = v
    if verbose:
        print(f"   [global-superset] reasoned {len(out)} of {len(items)} item(s); {len(big)} reviewed at the low end")
    return out


def fallback_share(subject, col, val) -> float:
    """Near zero, subject-salted: the low end for a row nothing could reason (0.005 to 0.05%)."""
    return round(0.005 + _unit(subject, col, val, "global-superset-fallback") * 0.045, 4)


# ---------------------------------------------------------------------------
# the fixer
# ---------------------------------------------------------------------------

def _cols(df):
    bp = next((c for c in df.columns if str(c).startswith("Brand Penetration")), None)
    cs = next((c for c in df.columns if str(c).strip().lower() == "category share"), None)
    raw = next((c for c in df.columns if str(c).lower().startswith("original raw")), None)
    proj = next((c for c in df.columns if "projection" in str(c).lower()), None)
    gpp = next((c for c in df.columns if str(c).strip().lower() == "gen pop penetration"), None)
    idx = next((c for c in df.columns if str(c).strip().lower().startswith("index vs")), None)
    return bp, cs, raw, proj, gpp, idx


def _sample_and_universe(df, bp, raw, proj):
    for meta in ("SAMPLE SIZE", "BRAND INPUT"):
        r = df[df["Column"].astype(str).str.strip().str.upper() == meta]
        if len(r):
            s, u = _num(r.iloc[0][raw]), _num(r.iloc[0][proj])
            if s and u:
                return s, u
    return None, None


def _market_mix(df, bp) -> str:
    """The non-US audience by country, from the worldwide file's own LOCATION rows."""
    shares = {}
    for c, v, b in zip(df["Column"], df["Value"], df[bp]):
        if str(c).strip().upper() != "LOCATION":
            continue
        x = _num(b)
        if not x:
            continue
        lab = str(v)
        if "," not in lab:
            continue
        ctry = lab.rsplit(",", 1)[1].strip()
        if _norm(ctry) in ("USA", "US", "UNITEDSTATES"):
            continue
        shares[ctry] = shares.get(ctry, 0.0) + x
    tot = sum(shares.values()) or 1.0
    top = sorted(shares.items(), key=lambda kv: -kv[1])
    parts = [f"{k} {v / tot * 100:.1f}%" for k, v in top[:22]]
    if len(top) > 22:
        parts.append("then smaller shares across the rest of the world")
    return ", ".join(parts) or "markets across the world"


def enforce_us_superset(df, us_df, subject, *, s3_key="", category="", reason_fn=None, is_hidden=None,
                        verbose=True):
    """Hold every row of the US profile inside the worldwide frame at the people level, in place.
    Returns (df, stats). Never raises: on any internal error the frame comes back unchanged."""
    stats = {"lifted": 0, "added": 0, "cities": 0, "metros_folded": 0, "demo": 0, "reasoned": 0, "fallback": 0}
    try:
        return _enforce(df, us_df, subject, s3_key, category, reason_fn, is_hidden, stats, verbose)
    except Exception as e:  # pragma: no cover - fail-safe
        print(f"   [global-superset] skipped ({type(e).__name__}: {e})")
        return df, stats


def _enforce(df, us_df, subject, s3_key, category, reason_fn, is_hidden, stats, verbose):
    import pandas as pd
    bp, cs, raw, proj, gpp, idx = _cols(df)
    ubp, _ucs, uraw, uproj, ugpp, _uidx = _cols(us_df)
    if not (bp and raw and proj and ubp and uproj):
        return df, stats
    S, U = _sample_and_universe(df, bp, raw, proj)
    _us_s, US_U = _sample_and_universe(us_df, ubp, uraw, uproj)
    if not (S and U and US_U) or US_U >= U:
        return df, stats
    NON = U - US_U
    df = df.copy()
    g_rows, u_rows = rows_of_frame(df, U), rows_of_frame(us_df)
    f = superset_findings(g_rows, u_rows, is_hidden=is_hidden)
    lift = f["below"] + f["equal"] + f["near"]
    if not (lift or f["missing"] or owed(f)):
        return df, stats
    cu_s = df["Column"].astype(str).str.strip().str.upper()
    vu_s = df["Value"].astype(str).str.strip().str.upper()
    pos = {}
    for i, k in zip(df.index, zip(cu_s, vu_s)):
        pos.setdefault(k, []).append(i)
    us_by_key, us_gpp = {}, {}
    for c, v, g in zip(us_df["Column"], us_df["Value"], us_df[ugpp] if ugpp else [""] * len(us_df)):
        k = (str(c).strip().upper(), str(v).strip().upper())
        us_by_key.setdefault(k, (str(c).strip(), str(v).strip()))
        us_gpp.setdefault(k, str(g or ""))

    try:
        from migration.post_generation_enforcers import _MPB_MIRROR_SKIP_CATS as _skip_mirror
    except Exception:
        try:
            from post_generation_enforcers import _MPB_MIRROR_SKIP_CATS as _skip_mirror  # type: ignore
        except Exception:
            _skip_mirror = set(SKIP_COLS) | {"MOST PURCHASED BRANDS", "TALENT", "ACTOR", "MUSICIAN/BAND"}

    # ---- items to reason: one per worldwide row (an exact mirror family shares one) ----
    items, by_target = [], {}
    def _item_for(target, label, cats, us_people):
        key = (str(subject).upper(), target[0], _norm(target[1]))
        if key in by_target:
            it = items[by_target[key]]
            it["us_people"] = max(it["us_people"], us_people)
            return it
        it = {"id": len(items), "label": label, "cats": cats, "us_people": us_people,
              "us_pct": round(us_people / US_U * 100.0, 4), "key": key, "targets": []}
        by_target[key] = len(items)
        items.append(it)
        return it
    for r in lift:
        it = _item_for(r["target"], r["val"], [r["target"][0]], r["us"])
        it["targets"].append(r["target"])
    for r in f["missing"]:
        col = r["col"].strip().upper()
        col = COLUMN_TWINS.get(col, col) if COLUMN_TWINS.get(col) in set(cu_s) else col
        it = _item_for((col, r["val"].strip().upper()), r["val"], [col], r["us"])
        it.setdefault("adds", []).append(r)
    for r in f["demo_missing"] + f["demo_below"]:
        lab = "+".join(r["world_labels"])
        it = _item_for((r["col"], lab), f"{r['col'].title()}: {' or '.join(r['us_labels']).title()} "
                       f"(a demographic bucket)", [r["col"]], r["us"])
        it["demo"] = r

    # ---- the non-US share per item: cached, reasoned, else the near-zero fallback ----
    todo = [it for it in items if it["key"] not in _REASON_CACHE]
    got = (reason_fn or house_reason)(
        [{"id": it["id"], "label": it["label"], "cats": it["cats"], "us_pct": it["us_pct"]} for it in todo],
        {"subject": subject, "category": category, "universe": int(U), "us_people": int(US_U),
         "non_us": int(NON), "mix": _market_mix(df, bp)}, verbose) if todo else {}
    for it in todo:
        if it["id"] in (got or {}):
            _REASON_CACHE[it["key"]] = float(got[it["id"]])
            stats["reasoned"] += 1
    for it in items:
        if it["key"] in _REASON_CACHE:
            it["p"] = _REASON_CACHE[it["key"]]
        else:
            it["p"] = fallback_share(subject, it["key"][1], it["key"][2])
            stats["fallback"] += 1
        it["people"] = it["us_people"] + NON * it["p"] / 100.0

    changed = set()
    def _set(i, people):
        cur = (_num(df.at[i, bp]) or 0.0) / 100.0 * U
        if people <= cur:
            return False
        df.at[i, bp] = _fmt(_ceil4(people / U * 100.0))
        changed.add(i)
        return True

    # ---- lift the rows that hold a US row ----
    for it in items:
        if "demo" in it:
            continue
        for t in it["targets"]:
            for i in pos.get(t, []):
                if _set(i, it["people"]):
                    stats["lifted"] += 1

    # ---- add the US rows the file lacks, in the worldwide column (the US casing and baseline) ----
    new_rows = []
    for it in items:
        for r in it.get("adds", []):
            ur = us_by_key.get((r["col"].strip().upper(), r["val"].strip().upper()))
            row = {c: "" for c in df.columns}
            col = it["key"][1]
            exist = next((str(c) for c in df["Column"] if str(c).strip().upper() == col), None)
            row["Column"] = exist or (ur[0] if ur is not None else r["col"])
            row["Value"] = ur[1] if ur is not None else r["val"]
            row[bp] = _fmt(_ceil4(it["people"] / U * 100.0))
            if gpp and ugpp:
                row[gpp] = us_gpp.get((r["col"].strip().upper(), r["val"].strip().upper()), "")
            new_rows.append(row)
            stats["added"] += 1
    if new_rows:
        base = len(df)
        add = pd.DataFrame(new_rows, columns=df.columns)
        add.index = range(max(df.index) + 1, max(df.index) + 1 + len(add)) if len(df) else range(len(add))
        df = pd.concat([df, add])
        changed |= set(add.index)
        cu_s = df["Column"].astype(str).str.strip().str.upper()
        vu_s = df["Value"].astype(str).str.strip().str.upper()

    # ---- purchase mirrors: one value per brand across MPB and its mirrors, the highest one ----
    fam = {}
    for i, c, v in zip(df.index, cu_s, df["Value"]):
        b = _num(df.at[i, bp])
        if b is None or abs(b - 100.0) < 0.01:
            continue
        if c == "MOST PURCHASED BRANDS" or c not in _skip_mirror:
            fam.setdefault(_norm(v), []).append(i)
    families = {nb: ix for nb, ix in fam.items() if len(ix) > 1
                and any(cu_s.at[i] == "MOST PURCHASED BRANDS" for i in ix) and any(i in changed for i in ix)}
    member = {}
    for nb, ix in families.items():
        top = max(_num(df.at[i, bp]) for i in ix)
        for i in ix:
            member[i] = nb
            if abs(_num(df.at[i, bp]) - top) > 1e-9:
                df.at[i, bp] = _fmt(top)
                changed.add(i)

    # ---- off the 2dp boundary, unique in its column and across the brand's own rows (upward only) ----
    from collections import Counter
    bpv = {i: _num(df.at[i, bp]) for i in df.index}
    nmv = {i: _norm(df.at[i, "Value"]) for i in df.index}
    by_cat, by_name = {}, {}
    for i in df.index:
        x = bpv[i]
        if x is None:
            continue
        by_cat.setdefault(cu_s.at[i], Counter())[round(x, 4)] += 1
        if cu_s.at[i] not in SKIP_COLS:
            by_name.setdefault(nmv[i], Counter())[round(x, 4)] += 1
    done = set()
    for i in sorted(changed, key=str):
        if i in done or cu_s.at[i] in SKIP_COLS:
            continue
        grp = families.get(member.get(i), [i]) if i in member else [i]
        for j in grp:                      # the group's own values never clash with themselves
            by_cat[cu_s.at[j]][round(bpv[j], 4)] -= 1
            if cu_s.at[j] not in SKIP_COLS:
                by_name[nmv[j]][round(bpv[j], 4)] -= 1
        x = round(bpv[grp[0]], 4)
        step = 0
        while step <= 400:
            clash = _on_2dp(x) or any(by_cat[cu_s.at[j]][x] > 0 for j in grp) or any(
                by_name.get(nmv[j], Counter())[x] > 0 for j in grp if cu_s.at[j] not in SKIP_COLS)
            if not clash:
                break
            step += 1
            x = round(x + 0.0001 * (1 + int(_unit(subject, df.at[grp[0], "Value"], step) * 3)), 4)
        for j in grp:
            df.at[j, bp] = _fmt(x)
            bpv[j] = x
            by_cat[cu_s.at[j]][x] += 1
            if cu_s.at[j] not in SKIP_COLS:
                by_name.setdefault(nmv[j], Counter())[x] += 1
            done.add(j)

    # ---- LOCATION: the US profile's own cities at their US people; the US metros they replace fold in ----
    loc_ix = [i for i in df.index if cu_s.at[i] == "LOCATION"]
    us_loc = [(str(v).strip(), _num(p) or 0.0) for c, v, p in zip(us_df["Column"], us_df["Value"], us_df[uproj])
              if str(c).strip().upper() == "LOCATION" and (_num(p) or 0.0) > 0]
    if loc_ix and us_loc and (f["loc_missing"] or f["loc_below"] or f["loc_extra_us"]):
        us_dmas = {v.upper() for v, _p in us_loc}
        loc_total = sum(_num(df.at[i, bp]) or 0.0 for i in loc_ix)
        have = {}
        for i in loc_ix:
            have.setdefault(str(df.at[i, "Value"]).strip().upper(), i)
        keep_us, drop = {}, []
        for i in loc_ix:
            lab = str(df.at[i, "Value"]).strip()
            lu = lab.upper()
            if lu in us_dmas or (lu.endswith(", USA") and lu[:-5] in us_dmas):
                keep_us[lu[:-5] if lu.endswith(", USA") else lu] = i
            elif _is_us_city(lab, us_dmas):
                drop.append(i)
        template = df.loc[loc_ix[0]].copy()
        used = set()
        new_loc = []
        for v, p in us_loc:
            x = _ceil4(p / U * 100.0)
            i = keep_us.get(v.upper())
            if i is not None:
                x = max(x, _num(df.at[i, bp]) or 0.0)
            while _on_2dp(x) or round(x, 4) in used:
                x = round(x + 0.0001, 4)
            used.add(round(x, 4))
            if i is not None:
                if abs((_num(df.at[i, bp]) or 0.0) - x) > 1e-9:
                    df.at[i, bp] = _fmt(x)
                    stats["cities"] += 1
            else:
                row = template.copy()
                for c in df.columns:
                    row[c] = ""
                row["Column"] = template["Column"]
                row["Value"] = _us_location_label(v)
                row[bp] = _fmt(x)
                new_loc.append(row)
                stats["cities"] += 1
        stats["metros_folded"] = len(drop)
        df = df.drop(index=drop)
        if new_loc:
            add = pd.DataFrame(new_loc, columns=df.columns)
            add.index = range(max(df.index) + 1, max(df.index) + 1 + len(add))
            df = pd.concat([df, add])
        cu_s = df["Column"].astype(str).str.strip().str.upper()
        loc_ix = [i for i in df.index if cu_s.at[i] == "LOCATION"]
        us_part = {i for i in loc_ix if str(df.at[i, "Value"]).strip().upper().endswith(", USA")
                   or str(df.at[i, "Value"]).strip().upper() in us_dmas}
        non_us = [i for i in loc_ix if i not in us_part]
        us_total = sum(_num(df.at[i, bp]) or 0.0 for i in us_part)
        non_total = sum(_num(df.at[i, bp]) or 0.0 for i in non_us)
        target_total = min(100.0, max(loc_total, us_total))
        if non_us and us_total + non_total > target_total + 1e-9 and non_total > 0:
            target_non = round(max(0.0, target_total - us_total), 4)
            vals = {i: round((_num(df.at[i, bp]) or 0.0) * target_non / non_total, 4) for i in non_us}
            order = sorted(non_us, key=lambda i: -vals[i])
            resid = round(target_non - sum(vals.values()), 4)
            k = 0
            while abs(resid) >= 0.00005 and k < 100000:
                d = 0.0001 if resid > 0 else -0.0001
                j = order[k % len(order)]
                vals[j] = round(vals[j] + d, 4)
                resid = round(resid - d, 4)
                k += 1
            seen = {round(_num(df.at[i, bp]), 4) for i in us_part}
            for i in order:
                x = vals[i]
                tries = 0
                while (_on_2dp(x) or x in seen) and tries < 50:
                    x = round(x - 0.0001, 4)
                    for j in order:
                        y = round(vals[j] + 0.0001, 4)
                        if j != i and not _on_2dp(y) and y not in seen and y != x:
                            vals[j] = y
                            break
                    tries += 1
                vals[i] = x
                seen.add(x)
            for i in non_us:
                df.at[i, bp] = _fmt(vals[i])

    # ---- demographics: every US bucket held by a worldwide bucket, each category back to 100 ----
    for cat in sorted({r["col"] for r in f["demo_missing"] + f["demo_below"]}):
        need = {}
        for it in items:
            r = it.get("demo")
            if r is None or r["col"] != cat:
                continue
            need[tuple(r["world_labels"])] = it["people"]
        if not need:
            continue
        ix = [i for i in df.index if cu_s.at[i] == cat]
        if not ix:
            continue
        cur = {str(df.at[i, "Value"]).strip().upper(): i for i in ix}
        share = {i: _num(df.at[i, bp]) or 0.0 for i in ix}
        floor = {}
        for labels, ppl in need.items():
            present = [l for l in labels if l in cur]
            want = _ceil4(ppl / U * 100.0)
            if not present:
                lab = labels[0]
                row = df.loc[ix[0]].copy()
                for c in df.columns:
                    row[c] = ""
                row["Column"] = df.at[ix[0], "Column"]
                row["Value"] = WORLD_LABEL.get(lab, lab.title())
                row[bp] = _fmt(want)
                add = pd.DataFrame([row], columns=df.columns)
                add.index = [max(df.index) + 1]
                df = pd.concat([df, add])
                ni = add.index[0]
                ix.append(ni)
                share[ni] = want
                floor[ni] = want
                cur[lab] = ni
                stats["demo"] += 1
            else:
                have = sum(share[cur[l]] for l in present)
                if have < want:
                    big = max(present, key=lambda l: share[cur[l]])
                    share[cur[big]] += round(want - have, 4)
                    stats["demo"] += 1
                for l in present:
                    floor[cur[l]] = share[cur[l]]
        # every other worldwide bucket keeps at least the US people it holds
        mins = dict(floor)
        total = sum(share.values())
        excess = round(total - 100.0, 4)
        if excess > 0:
            slack = {i: share[i] - mins.get(i, 0.0) for i in ix if i not in floor}
            s_tot = sum(v for v in slack.values() if v > 0) or 0.0
            for i, v in slack.items():
                if v > 0 and s_tot > 0:
                    share[i] = share[i] - excess * v / s_tot
        vals = {i: round(share[i], 4) for i in ix}
        resid = round(100.0 - sum(vals.values()), 4)
        free = [i for i in ix if i not in floor] or ix
        big = max(free, key=lambda i: vals[i])
        vals[big] = round(vals[big] + resid, 4)
        for i in ix:
            if _on_2dp(vals[i]):
                vals[i] = round(vals[i] + (0.0001 if i in floor else -0.0001), 4)
                vals[big] = round(vals[big] + (-0.0001 if i in floor else 0.0001), 4)
        for i in ix:
            df.at[i, bp] = _fmt(vals[i])
        cu_s = df["Column"].astype(str).str.strip().str.upper()

    # ---- the chain on every row: Raw and Projection from BP, Category Share, Index ----
    cu_s = df["Column"].astype(str).str.strip().str.upper()
    tot = {}
    for i in df.index:
        c = cu_s.at[i]
        b = _num(df.at[i, bp])
        if c in META_COLS or b is None:
            continue
        tot[c] = tot.get(c, 0.0) + b
    for i in df.index:
        c = cu_s.at[i]
        b = _num(df.at[i, bp])
        if c in META_COLS or b is None:
            continue
        df.at[i, raw] = str(int(round(b / 100.0 * S)))
        df.at[i, proj] = str(int(round(b / 100.0 * U)))
        if cs:
            df.at[i, cs] = _fmt(b if (c in DEMO_COLS or c == "LOCATION") else b / tot[c] * 100.0)
        if gpp and idx:
            g = _num(df.at[i, gpp])
            if g and g > 0:
                df.at[i, idx] = f"{b / g * 100.0:.1f}"
    df = df.reset_index(drop=True)
    if verbose:
        left = owed(superset_findings(rows_of_frame(df, U), u_rows, is_hidden=is_hidden))
        print(f"   [global-superset] {subject}: {stats['lifted']} row(s) lifted, {stats['added']} added, "
              f"{stats['cities']} US cities set ({stats['metros_folded']} US metros folded in), "
              f"{stats['demo']} demographic bucket(s); {stats['reasoned']} reasoned, {stats['fallback']} at the "
              f"near-zero fallback; {left} shortfall(s) left")
    return df, stats


# ---------------------------------------------------------------------------
# the writer's entry point (I21 autofix)
# ---------------------------------------------------------------------------

_US_CACHE = {}               # us key -> (etag, DataFrame)


def load_us_counterpart(s3_key, subject, s3=None, keys=None, rows=None):
    """(us_key, DataFrame) for a worldwide file whose subject has a US profile, else (None, None)."""
    import io as _io
    import pandas as pd
    if keys is None:
        try:
            try:
                from migration.final_ship_gate import _list_root_csvs
            except ImportError:
                from final_ship_gate import _list_root_csvs  # type: ignore
            keys = [k for k, _lm in _list_root_csvs(s3)]
        except Exception as e:
            print(f"   [global-superset] root listing unavailable ({type(e).__name__}: {e})")
            return None, None
    uk = us_counterpart_key(s3_key, subject, keys)
    if not uk:
        return None, None
    try:
        if s3 is None:
            import boto3
            s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=BUCKET, Key=uk)
        tag = str(obj.get("ETag") or "")
        hit = _US_CACHE.get(uk)
        if hit and hit[0] == tag and tag:
            return uk, hit[1]
        body = obj["Body"].read()
        udf = pd.read_csv(_io.BytesIO(body), dtype=str, keep_default_na=False)
    except Exception as e:
        print(f"   [global-superset] US profile {uk} could not be read ({type(e).__name__}: {e})")
        return None, None
    if is_global_rows(list(zip(udf["Column"], udf["Value"]))):
        return None, None
    _US_CACHE[uk] = (tag, udf)
    return uk, udf


def fix_us_superset(df, subject, s3_key, s3=None, *, category="", is_hidden=None, reason_fn=None, verbose=True):
    """The I21 fixer: (df, n_changed). A frame that is not worldwide, or whose subject has no US
    profile, comes back unchanged (and says so)."""
    if not is_global_rows(list(zip(df["Column"], df["Value"])), s3_key):
        return df, 0
    uk, udf = load_us_counterpart(s3_key, subject, s3)
    if udf is None:
        if verbose:
            print(f"   [global-superset] {s3_key}: no US profile for {subject!r}; nothing to hold")
        return df, 0
    if is_hidden is None:
        is_hidden = hidden_predicate()
    df2, st = enforce_us_superset(df, udf, subject, s3_key=s3_key, category=category, reason_fn=reason_fn,
                                  is_hidden=is_hidden, verbose=verbose)
    return df2, int(st["lifted"] + st["added"] + st["cities"] + st["metros_folded"] + st["demo"])


def hidden_predicate():
    """The ship gate's own do-not-publish test (I9), so a US row hidden since its build is not owed."""
    try:
        try:
            from migration.final_ship_gate import _load_hidden_norms
        except ImportError:
            from final_ship_gate import _load_hidden_norms  # type: ignore
        hidden = _load_hidden_norms(verbose=False) or set()
    except Exception:
        hidden = set()
    if not hidden:
        return None
    try:
        from migration.hostmap_norm import norm_key
    except ImportError:
        try:
            from hostmap_norm import norm_key  # type: ignore
        except ImportError:
            norm_key = _norm
    return lambda v: norm_key(v) in hidden
