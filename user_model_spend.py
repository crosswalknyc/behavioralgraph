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

BUCKET = "dashboard-inputs"
STORE_KEY = "system/usage/daily_costs.json"
USERS_KEY = "system/users.json"

CSV_COLUMNS = (
    "username",
    "company",
    "total_anthropic_spend",
    "total_openai_spend",
)

# Labels that are not a person. Leftover vs the day's billed total
# is split across these rows, never smeared onto a user.
TRENDS_USERNAME = "trends and rankers"
LOCAL_OPS_USERNAME = "local-ops"
UNTAGGED_USERNAME = "untagged box"
OTHER_USERNAME = "other"
SYSTEM_USERNAME = "system"
SYSTEM_COMPANY = "system"
SYSTEM_ROW_ORDER = (
    TRENDS_USERNAME,
    LOCAL_OPS_USERNAME,
    UNTAGGED_USERNAME,
    OTHER_USERNAME,
    SYSTEM_USERNAME,
)
UNASSIGNABLE_USERS = frozenset({
    "",
    "local-ops",
    "trends_ranker",
    "trends and rankers",
    "untagged box",
    "untagged",
    "other",
    "system",
})


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
    if lab in ("local-ops", "local ops", "localops"):
        return LOCAL_OPS_USERNAME
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
    """Keep assignable people. Leftover vs the day's billed total
    is split onto trends and rankers, local-ops, untagged box, and
    other. Any remainder after those asks lands on other."""
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
    local_ops_ask = _round2(
        detail.get("local_ops_usd")
        if detail.get("local_ops_usd") is not None
        else 0.0)
    trends_ask = _round2(day.get("trends") or 0.0)
    system_ch = _round2(day.get("system") or 0.0)
    untagged_ask = _round2(max(0.0, system_ch - local_ops_ask))
    other_ask = _round2(day.get("other") or 0.0)

    def take(asked: float) -> float:
        nonlocal leftover
        amt = _round2(min(max(0.0, asked), leftover))
        leftover = _round2(leftover - amt)
        return amt

    # Named asks first. If the day has no channel fields, the whole
    # leftover falls through to other.
    has_channels = any(v > 0.0 for v in (
        trends_ask, local_ops_ask, system_ch, other_ask))
    if has_channels:
        t = take(trends_ask)
        if t:
            people[TRENDS_USERNAME] = t
        lo = take(local_ops_ask)
        if lo:
            people[LOCAL_OPS_USERNAME] = lo
        u = take(untagged_ask)
        if u:
            people[UNTAGGED_USERNAME] = u
        o = take(other_ask + leftover)
        if o:
            people[OTHER_USERNAME] = o
    else:
        people[OTHER_USERNAME] = leftover
    return {k: _round2(v) for k, v in people.items() if _round2(v) > 0.0}


def day_user_spend(entry: dict) -> tuple[dict, dict]:
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
        a, o = day_user_spend(e)
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
    "TRENDS_USERNAME", "LOCAL_OPS_USERNAME", "UNTAGGED_USERNAME",
    "OTHER_USERNAME", "SYSTEM_ROW_ORDER",
    "parse_month", "month_label", "previous_month", "month_dates",
    "load_store", "load_users_dict", "users_index", "resolve_identity",
    "is_assignable_user", "is_system_row", "fold_system_residual",
    "day_user_spend", "roll_month", "month_rows", "filter_rows",
    "totals", "csv_text", "build_month",
]
