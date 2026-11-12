"""Per-user Anthropic + OpenAI spend rollup.

The dollars match the Anthropic cost line on the ops completion
emails (Requested by + Anthropic cost). Rolled from
s3://dashboard-inputs/system/usage/daily_costs.json:

  detail.anthropic_by_user   preferred (every commissioning origin)
  detail.prometheus_by_user  fallback for days written before the
                             all-origin field existed
  detail.openai_by_user      OpenAI Responses / web-search spend
                             (logged from 2026-10-06 on)

Used by the billing-portal download and by the monthly Czarina
email (migration/user_model_spend.py).
"""
from __future__ import annotations

import csv
import io
import json
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:      # in a test process no write reaches a live store (migration/storage_test_guard.py); nothing happens anywhere else
    import test_guard_boot as _test_guard_boot
    _test_guard_boot.install()
except Exception:
    pass

BUCKET = "dashboard-inputs"
STORE_KEY = "system/usage/daily_costs.json"
USERS_KEY = "system/users.json"

CSV_COLUMNS = (
    "username",
    "company",
    "total_anthropic_spend",
    "total_openai_spend",
)

# Named rows for spend that is not a person's work (2026-11-03, Jenna:
# "accurately capturing hard cost spend per user"). Leftover vs the
# day's billed total is split across these rows in this order; what no
# computed figure explains lands on "billed, not logged". Never smeared
# onto a user. Mirrors migration/daily_cost_report.fold_system_residual.
TRENDS_USERNAME = "trends and rankers"
AGENTS_USERNAME = "agents off the build server"
LOCAL_OPS_USERNAME = "local-ops"
NO_RUN_USERNAME = "build server, no run attached"
OTHER_KEYS_USERNAME = "other api keys"
UNSTAMPED_USERNAME = "calls not stamped to a user"
UNLOGGED_USERNAME = "billed, not logged"
# Legacy names (store rows written before 2026-11-03); recognized on read.
UNTAGGED_USERNAME = "untagged box"
OTHER_USERNAME = "other"
SYSTEM_USERNAME = "system"
SYSTEM_COMPANY = "system"
SYSTEM_ROW_ORDER = (
    TRENDS_USERNAME,
    AGENTS_USERNAME,
    LOCAL_OPS_USERNAME,
    NO_RUN_USERNAME,
    OTHER_KEYS_USERNAME,
    UNSTAMPED_USERNAME,
    UNLOGGED_USERNAME,
    UNTAGGED_USERNAME,
    OTHER_USERNAME,
    SYSTEM_USERNAME,
)
UNASSIGNABLE_USERS = frozenset({
    "",
    "local-ops",
    "trends_ranker",
    "trends and rankers",
    "agents off the build server",
    "agents",
    "dev key",
    "build server, no run attached",
    "untagged box",
    "untagged",
    "other api keys",
    "other",
    "calls not stamped to a user",
    "unstamped",
    "billed, not logged",
    "system",
})

# Standing reassignments (Jenna 2026-11-03, verbatim: "Billed by Anthropic,
# not logged by any tracker ... should go to trends and rankers, Builds we
# started ourselves (local-ops) should go to lions gate and armine and
# Partner API check/interpret calls and dashboard calls not stamped to a
# user ... should all go to kartel"). Applied at read time, per day, after
# the named-row fold, so the store stays raw and every surface (monthly
# sheet, admin billing page, exports) agrees. Each rule: the named row it
# moves, the first and last day it applies (None = open), and the label it
# moves the dollars to (a user email or username resolves through
# users.json; a named row stays a named row). The two dated rules end on
# the day per-call stamping began: from 2026-11-04 an ops build is tagged
# with the user it is for at queue time, and a partner API call carries
# its account, so anything that still lands on those rows is visible and
# can be assigned rather than silently charged.
ATTRIBUTION_OVERRIDES = (
    (UNLOGGED_USERNAME, None, None, TRENDS_USERNAME),
    (LOCAL_OPS_USERNAME, "2026-08-18", "2026-11-03", "ameseldzhyan@lionsgate.com"),
    (UNSTAMPED_USERNAME, "2026-08-18", "2026-11-03", "bkusin"),
)


def apply_attribution_overrides(spend: dict, date: Optional[str] = None,
                                overrides=ATTRIBUTION_OVERRIDES) -> dict:
    """Move named-row dollars per ATTRIBUTION_OVERRIDES for one day.
    `date` is YYYY-MM-DD; when unknown only the undated rules apply.
    Never changes the day's total."""
    out = {str(k).strip().lower(): float(v or 0.0) for k, v in (spend or {}).items()}
    for row, first, last, target in overrides or ():
        if first and (not date or date < first):
            continue
        if last and (not date or date > last):
            continue
        amt = out.pop(row, 0.0)
        if amt <= 0.0:
            continue
        tgt = str(target or "").strip().lower()
        out[tgt] = out.get(tgt, 0.0) + amt
    return {k: _round2(v) for k, v in out.items() if _round2(v) > 0.0}


def parse_month(ym: str) -> tuple[int, int]:
    raw = (ym or "").strip()
    y, m = raw.split("-", 1)
    year, month = int(y), int(m)
    if month < 1 or month > 12:
        raise ValueError(f"bad month: {ym}")
    return year, month


def month_label(year: int, month: int) -> str:
    return datetime(year, month, 1).strftime("%B %Y")


def previous_month(today=None) -> tuple[int, int]:
    d = today or datetime.now(timezone.utc).date()
    first = d.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.year, prev.month


def month_dates(year: int, month: int) -> list[str]:
    last = monthrange(year, month)[1]
    return [date(year, month, day).isoformat() for day in range(1, last + 1)]


def _s3():
    import boto3
    return boto3.client("s3")


def load_store(s3=None) -> dict:
    client = s3 or _s3()
    body = client.get_object(Bucket=BUCKET, Key=STORE_KEY)["Body"].read()
    data = json.loads(body)
    if not isinstance(data, dict):
        return {"days": {}}
    days = data.get("days")
    if not isinstance(days, dict):
        data["days"] = {}
    return data


def load_users_dict(s3=None, users=None) -> dict:
    if isinstance(users, dict):
        if "users" in users and isinstance(users.get("users"), dict):
            return users["users"]
        return users
    client = s3 or _s3()
    body = client.get_object(Bucket=BUCKET, Key=USERS_KEY)["Body"].read()
    data = json.loads(body)
    return (data or {}).get("users") or {}


def users_index(users: dict) -> dict:
    """label (lowercased email or username) -> (username, company)."""
    idx = {}
    for uname, u in (users or {}).items():
        if not isinstance(u, dict):
            continue
        company = str(u.get("company") or "").strip()
        idx[str(uname).strip().lower()] = (str(uname), company)
        em = str(u.get("email") or "").strip().lower()
        if em:
            idx[em] = (str(uname), company)
    return idx


def _canonical_system_row(label: str) -> str:
    lab = (label or "").strip().lower()
    if lab in ("trends and rankers", "trends_ranker", "trends", "rankers"):
        return TRENDS_USERNAME
    if lab in ("agents off the build server", "agents", "dev key",
               "agent runs", "agent_runs"):
        return AGENTS_USERNAME
    if lab in ("local-ops", "local ops", "localops"):
        return LOCAL_OPS_USERNAME
    if lab in ("build server, no run attached", "no run", "no-run",
               "build server no run"):
        return NO_RUN_USERNAME
    if lab in ("other api keys", "other keys"):
        return OTHER_KEYS_USERNAME
    if lab in ("calls not stamped to a user", "unstamped", "not stamped"):
        return UNSTAMPED_USERNAME
    if lab in ("billed, not logged", "unlogged", "not logged"):
        return UNLOGGED_USERNAME
    if lab in ("untagged box", "untagged", "untagged box spend"):
        return UNTAGGED_USERNAME
    if lab in ("other",):
        return OTHER_USERNAME
    if lab == SYSTEM_USERNAME:
        return SYSTEM_USERNAME
    return ""


def resolve_identity(label: str, index: dict) -> tuple[str, str]:
    lab = (label or "").strip()
    if not lab:
        return "", ""
    sys_row = _canonical_system_row(lab)
    if sys_row:
        return sys_row, SYSTEM_COMPANY
    hit = index.get(lab.lower())
    if hit:
        return hit
    return lab, ""


def is_assignable_user(label: str) -> bool:
    lab = (label or "").strip().lower()
    return bool(lab) and lab not in UNASSIGNABLE_USERS


def is_system_row(label: str) -> bool:
    return bool(_canonical_system_row(label))


def _round2(v: float) -> float:
    return round(float(v or 0.0) + 1e-9, 2)


def fold_system_residual(anth: dict, day_total, entry=None) -> dict:
    """Keep assignable people. Leftover vs the day's billed total is
    split onto the named rows: trends and rankers, agents off the build
    server, local-ops, build server work with no run attached, other
    api keys, and finally billed-not-logged for whatever no computed
    figure explains. Never spreads a remainder onto a user."""
    people = {}
    for k, v in (anth or {}).items():
        lab = str(k or "").strip().lower()
        if not is_assignable_user(lab):
            continue
        people[lab] = people.get(lab, 0.0) + float(v or 0.0)
    if day_total is None:
        return {k: _round2(v) for k, v in people.items() if _round2(v) > 0.0}
    leftover = _round2(float(day_total or 0.0) - sum(people.values()))
    if leftover <= 0.0:
        return {k: _round2(v) for k, v in people.items() if _round2(v) > 0.0}

    day = entry if isinstance(entry, dict) else {}
    detail = (day.get("detail") or {}) if day else {}
    box = detail.get("box") or {}
    agent_ask = _round2(day.get("agent_runs") or 0.0)
    # agent_runs travels INSIDE the trends figure (memo), so the trends
    # row is the trends figure net of it.
    trends_ask = _round2(max(0.0, float(day.get("trends") or 0.0) - agent_ask))
    local_ops_ask = _round2(
        detail.get("local_ops_usd")
        if detail.get("local_ops_usd") is not None
        else 0.0)
    no_run_ask = _round2(box.get("no_run_usd")
                         if box.get("no_run_usd") is not None
                         else (box.get("non_run_usd") or 0.0))
    other_ask = _round2(day.get("other") or 0.0)
    # Dashboard and partner API calls that carried no account on the
    # record. Computed per day by the daily report; scaled with the rest.
    unstamped_ask = _round2(detail.get("chat_unstamped_usd") or 0.0)
    system_ch = _round2(day.get("system") or 0.0)

    def take(asked: float) -> float:
        nonlocal leftover
        amt = _round2(min(max(0.0, asked), leftover))
        leftover = _round2(leftover - amt)
        return amt

    has_channels = any(v > 0.0 for v in (
        trends_ask, agent_ask, local_ops_ask, no_run_ask, other_ask,
        unstamped_ask, system_ch))
    if has_channels:
        t = take(trends_ask)
        if t:
            people[TRENDS_USERNAME] = t
        a = take(agent_ask)
        if a:
            people[AGENTS_USERNAME] = a
        lo = take(local_ops_ask)
        if lo:
            people[LOCAL_OPS_USERNAME] = lo
        nr = take(no_run_ask)
        if nr:
            people[NO_RUN_USERNAME] = nr
        o = take(other_ask)
        if o:
            people[OTHER_KEYS_USERNAME] = o
        u = take(unstamped_ask)
        if u:
            people[UNSTAMPED_USERNAME] = u
    if leftover > 0.0:
        people[UNLOGGED_USERNAME] = leftover
    return {k: _round2(v) for k, v in people.items() if _round2(v) > 0.0}


def day_user_spend(entry: dict, date: Optional[str] = None,
                   apply_overrides: bool = True) -> tuple[dict, dict]:
    detail = (entry or {}).get("detail") or {}
    anth = (detail.get("anthropic_by_user")
            or detail.get("prometheus_by_user")
            or {})
    oai = detail.get("openai_by_user") or {}
    if not isinstance(anth, dict):
        anth = {}
    if not isinstance(oai, dict):
        oai = {}
    # Only fold when the day carries a billed/computed total so older
    # test fixtures and days without a headline number stay as-is.
    if isinstance(entry, dict) and "total" in entry:
        anth = fold_system_residual(anth, entry.get("total"), entry)
    else:
        anth = {str(k).strip().lower(): float(v or 0.0)
                for k, v in anth.items() if str(k or "").strip()}
    if apply_overrides:
        anth = apply_attribution_overrides(anth, date)
    return anth, oai


def roll_month(days: dict, year: int, month: int):
    anth = defaultdict(float)
    oai = defaultdict(float)
    days_used = 0
    for d in month_dates(year, month):
        e = (days or {}).get(d)
        if not isinstance(e, dict):
            continue
        days_used += 1
        a, o = day_user_spend(e, date=d)
        for k, v in a.items():
            if k:
                anth[str(k).strip().lower()] += float(v or 0.0)
        for k, v in o.items():
            if k:
                oai[str(k).strip().lower()] += float(v or 0.0)
    return anth, oai, days_used


def month_rows(days: dict, users: dict, year: int, month: int) -> tuple:
    index = users_index(users)
    anth, oai, days_used = roll_month(days, year, month)
    labels = set(anth) | set(oai)
    merged = {}
    for lab in labels:
        uname, company = resolve_identity(lab, index)
        key = (uname or lab).lower()
        slot = merged.setdefault(key, {
            "username": uname or lab,
            "company": company,
            "total_anthropic_spend": 0.0,
            "total_openai_spend": 0.0,
        })
        if company and not slot["company"]:
            slot["company"] = company
        slot["total_anthropic_spend"] += float(anth.get(lab) or 0.0)
        slot["total_openai_spend"] += float(oai.get(lab) or 0.0)
    rows = []
    for slot in merged.values():
        slot["total_anthropic_spend"] = round(
            slot["total_anthropic_spend"] + 1e-9, 2)
        slot["total_openai_spend"] = round(
            slot["total_openai_spend"] + 1e-9, 2)
        if (slot["total_anthropic_spend"] or slot["total_openai_spend"]):
            rows.append(slot)
    rows.sort(key=lambda r: (-r["total_anthropic_spend"],
                             r["username"].lower()))
    people = [r for r in rows
              if not is_system_row(r.get("username") or "")]
    system = []
    by_name = {str(r.get("username") or "").lower(): r for r in rows
               if is_system_row(r.get("username") or "")}
    for name in SYSTEM_ROW_ORDER:
        if name in by_name:
            system.append(by_name[name])
    for name, r in by_name.items():
        if name not in SYSTEM_ROW_ORDER:
            system.append(r)
    return people + system, days_used


def filter_rows(rows: list, username: Optional[str] = None,
                company: Optional[str] = None) -> list:
    uname = (username or "").strip().lower()
    comp = (company or "").strip().lower()
    out = []
    for r in rows:
        if uname and str(r.get("username") or "").strip().lower() != uname:
            continue
        if comp and str(r.get("company") or "").strip().lower() != comp:
            continue
        out.append(r)
    return out


def totals(rows: list) -> dict:
    people = [r for r in rows
              if not is_system_row(r.get("username") or "")]
    system = [r for r in rows
              if is_system_row(r.get("username") or "")]
    anth = sum(float(r.get("total_anthropic_spend") or 0) for r in rows)
    oai = sum(float(r.get("total_openai_spend") or 0) for r in rows)
    user_anth = sum(float(r.get("total_anthropic_spend") or 0)
                    for r in people)
    sys_anth = sum(float(r.get("total_anthropic_spend") or 0)
                   for r in system)
    out = {
        "total_anthropic_spend": round(anth + 1e-9, 2),
        "total_openai_spend": round(oai + 1e-9, 2),
        "user_anthropic_spend": round(user_anth + 1e-9, 2),
        "system_anthropic_spend": round(sys_anth + 1e-9, 2),
        "users": len(people),
    }
    for r in system:
        key = "row_" + str(r.get("username") or "").replace(" ", "_")
        out[key] = float(r.get("total_anthropic_spend") or 0)
    return out


def csv_text(rows: list, include_total: bool = True) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS),
                       extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({
            "username": r.get("username") or "",
            "company": r.get("company") or "",
            "total_anthropic_spend": f"{float(r.get('total_anthropic_spend') or 0):.2f}",
            "total_openai_spend": f"{float(r.get('total_openai_spend') or 0):.2f}",
        })
    if include_total and rows:
        t = totals(rows)
        w.writerow({
            "username": "TOTAL",
            "company": "",
            "total_anthropic_spend": f"{t['total_anthropic_spend']:.2f}",
            "total_openai_spend": f"{t['total_openai_spend']:.2f}",
        })
    return buf.getvalue()


def build_month(year: int, month: int, *, s3=None, users=None,
                username: Optional[str] = None,
                company: Optional[str] = None) -> dict:
    store = load_store(s3)
    users_d = load_users_dict(s3, users)
    rows, days_used = month_rows(store.get("days") or {}, users_d,
                                 year, month)
    rows = filter_rows(rows, username=username, company=company)
    return {
        "year": year,
        "month": month,
        "month_key": f"{year:04d}-{month:02d}",
        "month_label": month_label(year, month),
        "days_used": days_used,
        "rows": rows,
        "totals": totals(rows),
    }


__all__ = [
    "BUCKET", "STORE_KEY", "USERS_KEY", "CSV_COLUMNS",
    "UNASSIGNABLE_USERS", "SYSTEM_USERNAME", "SYSTEM_COMPANY",
    "TRENDS_USERNAME", "AGENTS_USERNAME", "LOCAL_OPS_USERNAME",
    "NO_RUN_USERNAME", "OTHER_KEYS_USERNAME", "UNSTAMPED_USERNAME",
    "UNLOGGED_USERNAME", "UNTAGGED_USERNAME", "OTHER_USERNAME",
    "SYSTEM_ROW_ORDER", "ATTRIBUTION_OVERRIDES", "apply_attribution_overrides",
    "parse_month", "month_label", "previous_month", "month_dates",
    "load_store", "load_users_dict", "users_index", "resolve_identity",
    "is_assignable_user", "is_system_row", "fold_system_residual",
    "day_user_spend", "roll_month", "month_rows", "filter_rows",
    "totals", "csv_text", "build_month",
]
