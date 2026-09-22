#!/usr/bin/env python3
"""Hostmap mapping-table ingest + email approval flow (Jenna 2026-08-28).

Mandate: "build an approval-button flow into the hostmap mapping email
so future CSVs ingest automatically on an Approve click (Reject or no
click = nothing happens)."

One module, three consumers (keep them on the SAME code):

1. One-shot ingest scripts (e.g. scripts/ingest_holley_family_hostmap.py)
   call ``parse_mapping_csv`` + ``ingest_mapping_rows`` directly for a
   verbally-approved CSV.
2. The mapping-email builder (migration/hostmap_gap_mapping.py) calls
   ``persist_approval`` at build time to stage the CSV for one-click
   approval and get Approve / Reject URLs for the email buttons.
3. The dashboard endpoints (bg-webapp/app.py, /api/hostmap-mapping/*)
   call ``handle_approval_action`` when a recipient clicks a button.

TWIN NOTE: bg-webapp/migration/hostmap_ingest.py must stay a
byte-identical copy of this file (scripts/test_module_twin_sync.py
enforces it). The parent repo copy is the maintained one; remediate
drift with  cp migration/hostmap_ingest.py bg-webapp/migration/ .

Ingest semantics (dedupe is a hard requirement, double-ingest must be
impossible by construction):

- A row is skipped when its (brand, hostname) already exists in
  reference.host_mapping under case + punctuation insensitive
  comparison.
- A hostname may map to ONE brand only (column-B uniqueness). A row
  whose hostname is already mapped to a DIFFERENT brand is skipped
  with the owning brand named in the reason.
- Hostname comparison folds like the clickstream matcher reads:
  lowercase, scheme + www. stripped, every punctuation run treated as
  a single space ('alfa-romeo' == 'alfa romeo'; 'https://holley.com'
  == 'holley.com' == 'holley com'). Brand comparison uses the standard
  hostmap norm (accent fold + upper + strip non-alphanumerics).
- Blank-HOSTNAME rows (the column-B uniqueness hand-review fallback)
  are never ingested.
- HOSTNAME_NORM is derived exactly like migration/sync_host_mapping.py
  derives it (lowercase; URL rows keep only the parsed hostname; www.
  and trailing slash stripped).

Approval-state store (S3, region us-east-2):

- s3://dashboard-inputs/system/hostmap_gap_approvals/<id>.csv   the CSV
- s3://dashboard-inputs/system/hostmap_gap_approvals/<id>.json  state
- status: pending -> approved | rejected, one-way, transitioned with
  ETag compare-and-swap (migration/s3_json_state). A consumed link
  renders the already-processed page and never re-ingests (the CAS
  claim is the single-use ledger; the dedupe above is the backstop).
- Reject voids the id: a later Approve click on a rejected id does
  nothing. No click = pending forever = nothing happens.

Tokens: HMAC-SHA256 over "<id>:<action>" with a server secret,
constant-time compare on verify. Secret resolution order:

1. env HOSTMAP_APPROVAL_SECRET  (set on the Render services)
2. s3://dashboard-inputs/system/hostmap_approval_secret.txt  (lets the
   engine host + local scripts sign links with zero env plumbing)

When no secret is resolvable the email builder sends WITHOUT buttons
(fail-safe) and the endpoints refuse every token.

Google Sheet append (the mapping table's source-of-truth sheet, see
migration/sync_host_mapping.py): wired behind env-configured
credentials (GOOGLE_SHEETS_CREDENTIALS_JSON = service-account JSON,
inline or a file path). Gracefully skipped with a clear log line and a
note on the confirmation page when unconfigured. The sheet step can
never block or fail the mapping-table ingest.

ClickHouse access is plain HTTP (requests) so the module behaves
identically from the dashboard service, the engine host, and a laptop.
Connection settings reuse the clickhouse_connector env names:
CH_HOST / CH_PORT / CH_USER / CH_PASSWORD.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from html import escape as _esc
from urllib.parse import quote as _urlquote
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAPPING_COLUMNS = ("BRAND", "HOSTNAME", "CATEGORY", "SECTION")
CH_TABLE = "reference.host_mapping"
CH_INSERT_COLUMNS = (
    "BRAND", "HOSTNAME", "CATEGORY", "SECTION",
    "Most Purchased Categories", "SECOND_PASS", "HOSTNAME_NORM", "PRICING",
)

APPROVAL_BUCKET = "dashboard-inputs"
APPROVAL_PREFIX = "system/hostmap_gap_approvals"
APPROVAL_SECRET_S3_KEY = "system/hostmap_approval_secret.txt"
S3_REGION = "us-east-2"

DEFAULT_BASE_URL = "https://dashboard.crosswalknyc.com"
# Native Google Sheet (Jenna 2026-09-21). Previous ID
# 1zwy73Z0BZ5iMToo9YAqUezS4Nc94Twyb was an .xlsx uploaded to Drive and
# the Sheets API cannot read/write Office files - see the header
# comment on ``_sheet_credentials``. On the new sheet the CONTENT_MAPPING
# tab (gid=1321298530, index 1) is the append target for the content
# proposal flow; the hostmap tabs are dated
# (HOSTMAP mm.dd.yy; most recent is HOSTMAP 04.20.26) and the resolver
# below auto-picks the newest one so we never hardcode a stale target.
DEFAULT_SHEET_ID = "1I5F5at4hJ92krhgYdew68pcwd7_gqpoVfWLAHKnLpzA"

_ALNUM_ONLY_RE = re.compile(r"[^A-Z0-9]")
_PUNCT_RUN_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Folds (comparison keys)
# ---------------------------------------------------------------------------
def fold_brand(s) -> str:
    """Standard hostmap brand norm: accent fold + upper + strip
    non-alphanumerics ('Alfa-Romeo' / 'alfa romeo' -> 'ALFAROMEO')."""
    folded = unicodedata.normalize("NFKD", str(s or ""))
    folded = folded.encode("ascii", "ignore").decode("ascii")
    return _ALNUM_ONLY_RE.sub("", folded.upper())


def fold_hostname(s) -> str:
    """Case + punctuation insensitive hostname key, matcher-shaped:
    lowercase, scheme and www. stripped, punctuation runs collapse to
    one space. 'https://holley.com' == 'holley.com' == 'holley com';
    'parts/jegs' == 'parts jegs'."""
    h = str(s or "").strip().lower()
    if not h:
        return ""
    if h.startswith(("http://", "https://")):
        try:
            h = urlparse(h).hostname or h
        except Exception:
            pass
    if h.startswith("www."):
        h = h[4:]
    return _PUNCT_RUN_RE.sub(" ", h).strip()


def derive_hostname_norm(hostname) -> str:
    """The HOSTNAME_NORM cell value, mirroring
    migration/sync_host_mapping.py::parse_and_normalize."""
    hn = str(hostname or "").strip().lower()
    if hn.startswith(("http://", "https://")):
        try:
            hn = urlparse(hn).hostname or hn
        except Exception:
            pass
    if hn.startswith("www."):
        hn = hn[4:]
    return hn.rstrip("/")


# ---------------------------------------------------------------------------
# Mapping CSV parse
# ---------------------------------------------------------------------------
def parse_mapping_csv(text) -> list:
    """Parse a Mapping_Table_V1-format CSV into row dicts. Raises
    ValueError when the header does not carry the four mapping columns
    (extra columns are tolerated and ignored)."""
    reader = csv.DictReader(io.StringIO(str(text or "").lstrip("\ufeff")))
    fields = [f.strip().upper() for f in (reader.fieldnames or [])]
    missing = [c for c in MAPPING_COLUMNS if c not in fields]
    if missing:
        raise ValueError(f"mapping CSV is missing column(s): {missing}")
    rows = []
    for raw in reader:
        row = {k.strip().upper(): (v or "").strip()
               for k, v in raw.items() if k}
        if not row.get("BRAND"):
            continue
        rows.append({c: row.get(c, "") for c in MAPPING_COLUMNS})
    return rows


# ---------------------------------------------------------------------------
# ClickHouse over HTTP
# ---------------------------------------------------------------------------
def _ch_settings() -> dict:
    return {
        "url": (os.environ.get("HOSTMAP_CH_URL")
                or f"http://{os.environ.get('CH_HOST', '168.119.215.48')}:"
                   f"{os.environ.get('CH_PORT', '8123')}"),
        "user": os.environ.get("CH_USER", "bgapp"),
        "password": os.environ.get("CH_PASSWORD",
                                   "4qPllwDG+S3PptBWTRAJPTkpCzkRZ6tZ"),
    }


def ch_query(sql, data=None, timeout=60):
    """Run one ClickHouse HTTP query. Returns the response text.
    Raises on HTTP or transport errors (callers decide fail-safety)."""
    import requests
    cfg = _ch_settings()
    resp = requests.post(
        cfg["url"],
        params={"query": sql},
        data=(data.encode("utf-8") if isinstance(data, str) else data),
        auth=(cfg["user"], cfg["password"]),
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"mapping store query failed (HTTP {resp.status_code}): "
            f"{resp.text[:300]}")
    return resp.text


def mapping_store_health() -> dict:
    """Cheap reachability probe for the mapping store."""
    try:
        n = int(ch_query(
            f"SELECT count() FROM {CH_TABLE} FORMAT TSV", timeout=10).strip())
        return {"ok": True, "rows": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def load_existing_hostname_index() -> dict:
    """{fold_hostname: (BRAND, HOSTNAME)} over the whole mapping table.
    First writer wins within a fold group (good enough for dedupe:
    presence is what matters)."""
    text = ch_query(
        f"SELECT BRAND, HOSTNAME FROM {CH_TABLE} FORMAT JSONEachRow",
        timeout=120)
    index: dict = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        fh = fold_hostname(obj.get("HOSTNAME"))
        if fh and fh not in index:
            index[fh] = (str(obj.get("BRAND") or ""),
                         str(obj.get("HOSTNAME") or ""))
    return index


# ---------------------------------------------------------------------------
# The ingest (dedupe-checked, shared by every consumer)
# ---------------------------------------------------------------------------
def ingest_mapping_rows(rows, dry_run: bool = False,
                        existing_index=None) -> dict:
    """Insert mapping rows into reference.host_mapping with dedupe.

    Returns {"inserted": int, "skipped": [{brand, hostname, reason}],
    "inserted_rows": [...], "dry_run": bool}. With ``dry_run`` nothing
    is written; the summary reports what WOULD be inserted.

    Never double-ingests: rows already present (same brand + hostname
    fold) are skipped, and a hostname owned by another brand is never
    reassigned.
    """
    summary = {"inserted": 0, "skipped": [], "inserted_rows": [],
               "dry_run": bool(dry_run)}
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return summary
    index = (existing_index if existing_index is not None
             else load_existing_hostname_index())
    batch_folds: set = set()
    to_insert = []
    for r in rows:
        brand = str(r.get("BRAND") or "").strip()
        hostname = str(r.get("HOSTNAME") or "").strip()
        if not hostname:
            summary["skipped"].append({
                "brand": brand, "hostname": "",
                "reason": "blank hostname (hand-review row)"})
            continue
        fh = fold_hostname(hostname)
        if not fh:
            summary["skipped"].append({
                "brand": brand, "hostname": hostname,
                "reason": "hostname folds to empty"})
            continue
        if fh in batch_folds:
            summary["skipped"].append({
                "brand": brand, "hostname": hostname,
                "reason": "duplicate hostname within this file"})
            continue
        hit = index.get(fh)
        if hit is not None:
            own_brand, own_host = hit
            if fold_brand(own_brand) == fold_brand(brand):
                reason = ("already in the mapping table as "
                          f"{own_brand!r} / {own_host!r}")
            else:
                reason = (f"hostname already maps to {own_brand!r} "
                          f"(as {own_host!r}); one hostname, one brand")
            summary["skipped"].append({
                "brand": brand, "hostname": hostname, "reason": reason})
            continue
        batch_folds.add(fh)
        to_insert.append({
            "BRAND": brand,
            "HOSTNAME": hostname,
            "CATEGORY": str(r.get("CATEGORY") or "").strip(),
            "SECTION": str(r.get("SECTION") or "").strip(),
            "Most Purchased Categories": "",
            "SECOND_PASS": "",
            "HOSTNAME_NORM": derive_hostname_norm(hostname),
            "PRICING": "",
        })
    if to_insert and not dry_run:
        cols = ", ".join(f"`{c}`" for c in CH_INSERT_COLUMNS)
        body = "\n".join(json.dumps(r, ensure_ascii=False)
                         for r in to_insert)
        ch_query(
            f"INSERT INTO {CH_TABLE} ({cols}) FORMAT JSONEachRow",
            data=body, timeout=120)
    summary["inserted"] = len(to_insert)
    summary["inserted_rows"] = [
        {"brand": r["BRAND"], "hostname": r["HOSTNAME"],
         "category": r["CATEGORY"], "section": r["SECTION"]}
        for r in to_insert
    ]
    return summary


def verify_brands_in_table(brands) -> list:
    """Post-insert verification: every current row for these brands."""
    quoted = ", ".join(
        "'" + str(b).replace("\\", "\\\\").replace("'", "\\'") + "'"
        for b in sorted({str(b) for b in (brands or []) if str(b).strip()})
    )
    if not quoted:
        return []
    text = ch_query(
        f"SELECT BRAND, HOSTNAME, CATEGORY, SECTION, HOSTNAME_NORM "
        f"FROM {CH_TABLE} WHERE BRAND IN ({quoted}) "
        f"ORDER BY BRAND, HOSTNAME FORMAT JSONEachRow", timeout=60)
    out = []
    for line in text.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ---------------------------------------------------------------------------
# Google Sheet append (graceful skip when unconfigured)
# ---------------------------------------------------------------------------
# Credentials for the sheet append are searched in this order (first hit
# wins). All paths are optional; a miss on every one degrades to a
# graceful skip that never blocks the ClickHouse ingest.
#
#   1. env GOOGLE_SHEETS_CREDENTIALS_JSON = inline JSON string
#   2. env GOOGLE_SHEETS_CREDENTIALS_JSON = filesystem path
#   3. ~/.crosswalk/google_sheets_credentials.json  (mode 600)
#   4. /root/.crosswalk/google_sheets_credentials.json  (Hetzner worker
#      when it runs under a shell whose HOME is misresolved)
#   5. s3://dashboard-inputs/system/google_sheets_credentials.json
#      (private bucket, AES256 at rest; the shared fallback that lets
#      Render, Hetzner, and local scripts all sign appends without any
#      env plumbing)
#
# The bucket-side fallback is cached per process, matching how
# approval_secret() caches the HMAC secret.
_SHEET_CREDS_S3_KEY = "system/google_sheets_credentials.json"
_SHEET_CREDS_CACHE: dict = {}


def _sheet_cred_candidates():
    """Return the ordered list of (label, resolver) tuples the loader
    walks. Split out for testability and so callers can log which slot
    a hit came from."""
    import pwd as _pwd

    def _real_home():
        try:
            return _pwd.getpwuid(os.getuid()).pw_dir
        except Exception:
            return os.path.expanduser("~") or ""

    home = _real_home()
    paths = []
    if home:
        paths.append(os.path.join(home, ".crosswalk",
                                  "google_sheets_credentials.json"))
    paths.append("/root/.crosswalk/google_sheets_credentials.json")
    return paths


def _sheet_credentials():
    """Service-account credentials for the mapping sheet. Walks the
    resolution order documented above and returns google-auth
    Credentials on the first hit, or None (with a log line) when
    nothing resolves."""
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as goog_transport
    except ImportError:
        print("  [hostmap-ingest] sheet append skipped: google-auth "
              "not installed")
        return None
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    def _from_info(info, source):
        try:
            c = service_account.Credentials.from_service_account_info(
                info, scopes=scopes)
            c.refresh(goog_transport.Request())
            print(f"  [hostmap-ingest] sheet credentials loaded from "
                  f"{source}")
            return c
        except Exception as e:
            print(f"  [hostmap-ingest] sheet credentials at {source} "
                  f"failed to load ({e})")
            return None

    # 1 + 2: env var (inline JSON or a filesystem path)
    raw = (os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON") or "").strip()
    if raw:
        if raw.startswith("{"):
            try:
                return _from_info(json.loads(raw), "env (inline JSON)")
            except Exception as e:
                print(f"  [hostmap-ingest] env inline JSON parse "
                      f"failed ({e}); continuing to filesystem/S3")
        elif os.path.isfile(raw) and os.access(raw, os.R_OK):
            try:
                with open(raw, "r", encoding="utf-8") as f:
                    return _from_info(json.load(f), f"env path ({raw})")
            except Exception as e:
                print(f"  [hostmap-ingest] env-pointed file {raw} "
                      f"failed to load ({e})")

    # 3 + 4: filesystem candidates
    for p in _sheet_cred_candidates():
        try:
            if os.path.isfile(p) and os.access(p, os.R_OK):
                with open(p, "r", encoding="utf-8") as f:
                    return _from_info(json.load(f), p)
        except OSError:
            continue

    # 5: S3 fallback (cached per process)
    cached = _SHEET_CREDS_CACHE.get("s3")
    if cached is not None:
        return cached
    try:
        body = _s3_client().get_object(
            Bucket=APPROVAL_BUCKET, Key=_SHEET_CREDS_S3_KEY,
        )["Body"].read().decode("utf-8")
        creds = _from_info(json.loads(body),
                           f"s3://{APPROVAL_BUCKET}/{_SHEET_CREDS_S3_KEY}")
        _SHEET_CREDS_CACHE["s3"] = creds
        return creds
    except Exception as e:
        _SHEET_CREDS_CACHE["s3"] = None
        print(f"  [hostmap-ingest] sheet append skipped: no credentials "
              f"resolved (env / ~/.crosswalk / /root/.crosswalk / S3 "
              f"fallback all missed; last error: {e})")
        return None


_HOSTMAP_TAB_DATE_RE = re.compile(
    r"^\s*HOSTMAP\s+(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*$", re.IGNORECASE)


def _pick_latest_hostmap_tab(sheets):
    """Given the workbook's tabs list, pick the newest one whose title
    matches ``HOSTMAP mm.dd.yy``. Returns the tab title or None.

    Convention (Jenna): the mapping sheet keeps a dated hostmap tab
    that rotates every few months (HOSTMAP 12.31.25, HOSTMAP 04.20.26,
    ...). The active append target is always the most recent by parsed
    date; older tabs are read-only history."""
    best_date = None
    best_title = None
    for s in sheets or []:
        p = s.get("properties") or {}
        title = str(p.get("title") or "").strip()
        m = _HOSTMAP_TAB_DATE_RE.match(title)
        if not m:
            continue
        try:
            month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if year < 100:
                year += 2000
            from datetime import date as _d
            d = _d(year, month, day)
        except Exception:
            continue
        if best_date is None or d > best_date:
            best_date = d
            best_title = title
    return best_title


def _resolve_tab_title(sheet_id, tab_gid=None, tab_title=None,
                      headers=None):
    """Resolve the target tab title.

    Resolution order:
    1. ``tab_title`` (exact match wins; error when the title doesn't
       exist on the workbook).
    2. ``tab_gid`` (numeric gid).
    3. Env override ``HOST_MAPPING_TAB_TITLE``.
    4. Latest dated hostmap tab (``HOSTMAP mm.dd.yy`` regex).
    5. First tab on the workbook (legacy fallback; loud log line).
    """
    import requests
    r = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
        f"?fields=sheets.properties(sheetId,title,index)",
        headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"metadata HTTP {r.status_code}: "
                           f"{r.text[:200]}")
    sheets = r.json().get("sheets") or []
    if tab_title:
        for s in sheets:
            p = s.get("properties") or {}
            if str(p.get("title") or "").strip() == str(tab_title).strip():
                return p.get("title")
        # Title miss - fall through to gid if provided, so a renamed
        # tab still resolves via the numeric gid (Jenna 2026-09-21:
        # tab titles get edited, gids are stable).
        if tab_gid is None:
            raise RuntimeError(f"tab title {tab_title!r} not found on "
                               f"sheet (and no gid fallback given)")
        print(f"  [hostmap-ingest] tab title {tab_title!r} missing on "
              f"sheet; falling back to gid={tab_gid}")
    if tab_gid is not None:
        want = int(tab_gid)
        for s in sheets:
            p = s.get("properties") or {}
            if int(p.get("sheetId", -1)) == want:
                return p.get("title")
        raise RuntimeError(f"tab gid={tab_gid} not found on sheet")
    env_override = (os.environ.get("HOST_MAPPING_TAB_TITLE") or "").strip()
    if env_override:
        for s in sheets:
            p = s.get("properties") or {}
            if str(p.get("title") or "").strip() == env_override:
                return p.get("title")
        raise RuntimeError(f"HOST_MAPPING_TAB_TITLE={env_override!r} "
                           f"not found on sheet")
    latest = _pick_latest_hostmap_tab(sheets)
    if latest:
        print(f"  [hostmap-ingest] resolved dated hostmap tab: "
              f"{latest!r}")
        return latest
    first = ((sheets[0].get("properties") or {}).get("title")
             if sheets else None) or "Sheet1"
    print(f"  [hostmap-ingest] no dated HOSTMAP tab found; falling "
          f"back to first tab {first!r} - set HOST_MAPPING_TAB_TITLE "
          f"env var to pin an explicit tab")
    return first


def append_rows_to_sheet(rows, sheet_id=None, tab_gid=None,
                         tab_title=None, columns=None) -> dict:
    """Append rows to the mapping sheet. Graceful skip (never raises,
    never blocks the ClickHouse ingest): returns {"appended": n} on
    success or {"skipped": reason}.

    Defaults (backward compat, hostmap flow):
    - ``columns`` = ("BRAND", "HOSTNAME", "CATEGORY", "SECTION")
    - target tab = first tab on the workbook (the mapping tab)

    For the content-map flow (Jenna 2026-09-21) the caller passes
    ``tab_gid=1321298530`` (or the resolved tab_title) and
    ``columns=("SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON")``.
    The row dicts may use lowercase or uppercase keys interchangeably.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return {"skipped": "no rows"}
    creds = _sheet_credentials()
    if creds is None:
        return {"skipped": "sheet credentials not configured"}
    sid = sheet_id or os.environ.get("HOST_MAPPING_SHEET_ID",
                                     DEFAULT_SHEET_ID)
    cols = tuple(columns) if columns else ("BRAND", "HOSTNAME",
                                            "CATEGORY", "SECTION")
    try:
        import requests
        headers = {"Authorization": f"Bearer {creds.token}"}
        title = _resolve_tab_title(sid, tab_gid=tab_gid,
                                   tab_title=tab_title, headers=headers)
        # Column letter span: A, B, ..., Z. cols<=26 is enough for
        # BRAND/HOSTNAME/CATEGORY/SECTION (4) and SHOW/URL/PRODUCTION/
        # PLATFORM/SEASON (5). Extend if a future schema needs it.
        last_col = chr(ord('A') + len(cols) - 1)
        values = []
        for r in rows:
            row_vals = []
            for c in cols:
                # Accept both uppercase and lowercase keys.
                v = r.get(c)
                if v is None:
                    v = r.get(str(c).lower())
                row_vals.append("" if v is None else str(v))
            values.append(row_vals)
        rng = _urlquote(f"'{title}'!A:{last_col}", safe="")
        resp = requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
            f"/values/{rng}:append"
            f"?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
            headers={**headers, "Content-Type": "application/json"},
            json={"values": values}, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"append HTTP {resp.status_code}: "
                               f"{resp.text[:200]}")
        print(f"  [hostmap-ingest] appended {len(values)} row(s) to "
              f"'{title}' tab of the mapping sheet")
        return {"appended": len(values), "tab": title}
    except Exception as e:
        print(f"  [hostmap-ingest] sheet append skipped: {e}")
        return {"skipped": str(e)[:200]}


# ---------------------------------------------------------------------------
# Approval secret + tokens
# ---------------------------------------------------------------------------
_SECRET_CACHE: dict = {}
_SECRET_LOCK = threading.Lock()


def _s3_client():
    import boto3
    return boto3.client("s3", region_name=S3_REGION)


def approval_secret() -> str:
    """The HMAC secret: env HOSTMAP_APPROVAL_SECRET first, then the S3
    fallback (cached per process). Returns '' when unresolvable."""
    env = (os.environ.get("HOSTMAP_APPROVAL_SECRET") or "").strip()
    if env:
        return env
    with _SECRET_LOCK:
        if "s3" in _SECRET_CACHE:
            return _SECRET_CACHE["s3"]
        try:
            body = _s3_client().get_object(
                Bucket=APPROVAL_BUCKET, Key=APPROVAL_SECRET_S3_KEY,
            )["Body"].read().decode("utf-8").strip()
        except Exception:
            body = ""
        _SECRET_CACHE["s3"] = body
        return body


def sign_token(approval_id, action, secret=None) -> str:
    sec = secret if secret is not None else approval_secret()
    if not sec:
        return ""
    msg = f"{approval_id}:{action}".encode("utf-8")
    return hmac.new(sec.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_token(approval_id, action, token, secret=None) -> bool:
    expected = sign_token(approval_id, action, secret=secret)
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, str(token))


# ---------------------------------------------------------------------------
# Approval state store
# ---------------------------------------------------------------------------
def _state_key(approval_id) -> str:
    return f"{APPROVAL_PREFIX}/{approval_id}.json"


def _csv_key(approval_id) -> str:
    return f"{APPROVAL_PREFIX}/{approval_id}.csv"


def _update_json(bucket, key, mutate_fn):
    try:
        from migration.s3_json_state import update_json
    except ImportError:
        from s3_json_state import update_json  # type: ignore
    return update_json(bucket, key, mutate_fn, s3=_s3_client())


def load_approval(approval_id):
    """(state dict or None) for an approval id."""
    aid = str(approval_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,80}", aid):
        return None
    try:
        body = _s3_client().get_object(
            Bucket=APPROVAL_BUCKET, Key=_state_key(aid),
        )["Body"].read().decode("utf-8")
        return json.loads(body)
    except Exception:
        return None


def load_approval_csv(approval_id) -> str:
    try:
        return _s3_client().get_object(
            Bucket=APPROVAL_BUCKET, Key=_csv_key(str(approval_id)),
        )["Body"].read().decode("utf-8")
    except Exception:
        return ""


def persist_approval(csv_text, csv_name, subject, requested_by="",
                     dry_run: bool = False, base_url=None,
                     recipients=None) -> dict:
    """Stage a mapping CSV for one-click approval. Writes the CSV and a
    pending state JSON to S3 and returns
    {"id", "approve_url", "reject_url", "row_count"}.

    Raises when the secret is unresolvable or S3 is unreachable; the
    email builder catches and sends without buttons (fail-safe).
    ``dry_run=True`` stages a state whose Approve click only REPORTS
    what would be inserted (used by format-test emails).

    ``recipients`` is the full To+Cc list the proposal email went to. It
    is stored on the state so a later Approve/Reject click can reply-all:
    notify everyone that a decision was made so nobody else has to act
    (Jenna 2026-08-31).
    """
    secret = approval_secret()
    if not secret:
        raise RuntimeError("no approval secret configured "
                           "(HOSTMAP_APPROVAL_SECRET or the S3 fallback)")
    rows = parse_mapping_csv(csv_text)
    if not rows:
        raise ValueError("mapping CSV has no rows")
    stamp = datetime.now(timezone.utc)
    aid = f"hm_{stamp.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"
    s3 = _s3_client()
    s3.put_object(Bucket=APPROVAL_BUCKET, Key=_csv_key(aid),
                  Body=str(csv_text).encode("utf-8"),
                  ContentType="text/csv")
    state = {
        "id": aid,
        "status": "pending",
        "created_at": stamp.isoformat(),
        "requested_by": str(requested_by or ""),
        "subject": str(subject or ""),
        "csv_name": str(csv_name or f"{aid}.csv"),
        "csv_key": _csv_key(aid),
        "row_count": len(rows),
        "brand_count": len({fold_brand(r["BRAND"]) for r in rows}),
        "dry_run": bool(dry_run),
        "recipients": _dedupe_emails(recipients),
    }
    s3.put_object(Bucket=APPROVAL_BUCKET, Key=_state_key(aid),
                  Body=json.dumps(state, indent=2).encode("utf-8"),
                  ContentType="application/json")
    base = (base_url or os.environ.get("HOSTMAP_APPROVAL_BASE_URL")
            or DEFAULT_BASE_URL).rstrip("/")
    return {
        "id": aid,
        "row_count": len(rows),
        "approve_url": (f"{base}/api/hostmap-mapping/approve?id={aid}"
                        f"&token={sign_token(aid, 'approve', secret)}"),
        "reject_url": (f"{base}/api/hostmap-mapping/reject?id={aid}"
                       f"&token={sign_token(aid, 'reject', secret)}"),
    }


def _dedupe_emails(recipients) -> list:
    """Order-preserving, case-insensitive dedupe of an email list."""
    seen: set = set()
    out: list = []
    for r in recipients or []:
        e = str(r or "").strip()
        k = e.lower()
        if e and k not in seen:
            seen.add(k)
            out.append(e)
    return out


# Reply-all decision notifications are sent from the same address the
# proposal email uses. Kept partner-safe: the copy never names internal
# systems, only "mapping table" / "mapping additions" (the same vocabulary
# the confirmation pages already use).
_NOTIFY_FROM = "no_reply@crosswalknyc.com"


def _send_group_email(recipients, subject_line, text_body, html_body) -> None:
    """Send one email addressed to the whole recipient group. Never
    raises; logs and returns on any failure."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import boto3

    recips = _dedupe_emails(recipients)
    if not recips:
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject_line
    msg["From"] = _NOTIFY_FROM
    msg["To"] = ", ".join(recips)
    msg.attach(MIMEText(text_body or "", "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    ses = boto3.client("ses", region_name=S3_REGION)
    ses.send_raw_email(Source=_NOTIFY_FROM, Destinations=recips,
                       RawMessage={"Data": msg.as_string()})
    print(f"  [hostmap-ingest] decision notification sent to "
          f"{len(recips)} recipient(s)")


def _notify_decision(state, action) -> None:
    """Reply-all on a decision: tell every original recipient that the
    proposal was approved or rejected, so the rest of the group knows
    they do not need to act (Jenna 2026-08-31). Fires exactly once (on
    the CAS-claimed transition). Strictly fail-safe: a send failure never
    blocks or breaks the approval flow."""
    try:
        recips = _dedupe_emails((state or {}).get("recipients"))
        if not recips:
            return
        subject_name = (str((state or {}).get("subject") or "").strip()
                        or "this profile")
        n_rows = int((state or {}).get("row_count") or 0)
        n_brands = int((state or {}).get("brand_count") or 0)
        if action == "approve":
            decided = "approved"
            lead = (f"The mapping additions for {subject_name} were approved "
                    f"and added to the mapping table "
                    f"({n_rows} row(s) across {n_brands} brand(s)).")
        else:
            decided = "declined"
            lead = (f"The mapping additions for {subject_name} were declined. "
                    f"No changes were made to the mapping table.")
        text_body = (
            f"{lead}\n\n"
            "No further action is needed from anyone on this thread.\n"
        )
        html_body = (
            '<html><body style="font-family:-apple-system,Helvetica,Arial,'
            'sans-serif;color:#333;">'
            f'<p>{_esc(lead)}</p>'
            '<p style="color:#555;">No further action is needed from anyone '
            'on this thread.</p>'
            '<p style="color:#999;font-size:12px;margin-top:24px;">'
            'Behavioral Graph by Crosswalk</p></body></html>'
        )
        subject_line = f"Mapping additions for {subject_name}: {decided}"
        _send_group_email(recips, subject_line, text_body, html_body)
    except Exception as e:
        print(f"  [hostmap-ingest] decision notify failed (non-fatal): {e}")


def _claim_transition(approval_id, action) -> tuple:
    """CAS transition pending -> approved/rejected (the single-use
    ledger). Returns (claimed: bool, state: dict or None). When not
    claimed, ``state`` is the current state for rendering."""
    target = "approved" if action == "approve" else "rejected"
    result = {"claimed": False}

    def mutate(obj):
        if not isinstance(obj, dict) or not obj.get("id"):
            return None
        if obj.get("status") != "pending":
            return None
        obj["status"] = target
        obj[f"{target}_at"] = datetime.now(timezone.utc).isoformat()
        if action == "approve":
            obj["ingest_status"] = "running"
        result["claimed"] = True
        result["state"] = obj
        return obj

    try:
        _update_json(APPROVAL_BUCKET, _state_key(approval_id), mutate)
    except Exception as e:
        print(f"  [hostmap-ingest] state transition failed: {e}")
        return False, load_approval(approval_id)
    if result["claimed"]:
        return True, result.get("state")
    return False, load_approval(approval_id)


def _record_ingest_result(approval_id, ingest, sheet, status) -> None:
    def mutate(obj):
        if not isinstance(obj, dict) or not obj.get("id"):
            return None
        obj["ingest_status"] = status
        obj["ingest"] = ingest
        obj["sheet"] = sheet
        return obj

    try:
        _update_json(APPROVAL_BUCKET, _state_key(approval_id), mutate)
    except Exception as e:
        print(f"  [hostmap-ingest] result record failed: {e}")


# ---------------------------------------------------------------------------
# Endpoint flow + confirmation pages
# ---------------------------------------------------------------------------
def handle_approval_action(approval_id, action, token) -> tuple:
    """Full endpoint flow. Returns (html, http_status).

    approve on pending  -> claim, ingest (or dry-run report), sheet
                           append, record, confirmation page
    approve on approved -> already-processed page (retries the ingest
                           only when the recorded ingest FAILED; the
                           dedupe makes the retry safe)
    approve on rejected -> nothing happens page
    reject on pending   -> claim, no changes page
    reject on consumed  -> already-processed page
    bad token / id      -> refusal page
    """
    action = str(action or "").strip().lower()
    aid = str(approval_id or "").strip()
    if action not in ("approve", "reject"):
        return _page("Not available", "<p>This link is not valid.</p>"), 404
    if not verify_token(aid, action, token):
        return _page(
            "Link not valid",
            "<p>This link is not valid or has expired. No changes were "
            "made.</p>"), 403
    state = load_approval(aid)
    if state is None:
        return _page(
            "Proposal not found",
            "<p>This mapping proposal could not be found. No changes "
            "were made.</p>"), 404

    if action == "reject":
        claimed, state = _claim_transition(aid, "reject")
        if claimed:
            _notify_decision(state, "reject")
            return _page(
                "Proposal rejected",
                f"<p>The mapping proposal <b>{_esc(state.get('csv_name') or aid)}</b> "
                f"was rejected. No changes were made to the mapping "
                f"table.</p>{_state_meta_html(state)}"), 200
        return _already_processed_page(state), 200

    # approve
    if (state or {}).get("status") == "rejected":
        return _page(
            "Proposal was rejected",
            "<p>This mapping proposal was rejected earlier, so this "
            "approval link is void. No changes were made.</p>"), 200
    if (state or {}).get("status") == "approved":
        if (state or {}).get("ingest_status") == "failed":
            return _run_approved_ingest(aid, state)
        return _already_processed_page(state), 200
    claimed, state = _claim_transition(aid, "approve")
    if not claimed:
        return _already_processed_page(state), 200
    _notify_decision(state, "approve")
    return _run_approved_ingest(aid, state)


def _run_approved_ingest(approval_id, state) -> tuple:
    dry_run = bool((state or {}).get("dry_run"))
    csv_text = load_approval_csv(approval_id)
    if not csv_text:
        _record_ingest_result(approval_id, {"error": "csv missing"},
                              {"skipped": "csv missing"}, "failed")
        return _page(
            "Approval recorded",
            "<p>The approval was recorded but the proposal file could "
            "not be loaded. The ingest will be retried; click the "
            "Approve link again in a few minutes.</p>"), 200
    try:
        rows = parse_mapping_csv(csv_text)
        ingest = ingest_mapping_rows(rows, dry_run=dry_run)
    except Exception as e:
        _record_ingest_result(approval_id, {"error": str(e)[:300]},
                              {"skipped": "ingest failed"}, "failed")
        return _page(
            "Approval recorded",
            "<p>The approval was recorded but the mapping table could "
            "not be updated just now. Click the Approve link again in a "
            "few minutes to retry; rows already added are never "
            "duplicated.</p>"), 200
    if dry_run:
        sheet = {"skipped": "dry run"}
        _record_ingest_result(approval_id, ingest, sheet, "dry_run")
        return _confirmation_page(state, ingest, sheet, dry_run=True), 200
    sheet = append_rows_to_sheet(ingest.get("inserted_rows") or [])
    _record_ingest_result(approval_id, ingest, sheet, "done")
    return _confirmation_page(state, ingest, sheet, dry_run=False), 200


# ---------------------------------------------------------------------------
# Pages (Crosswalk-styled, standalone)
# ---------------------------------------------------------------------------
def _page(title, body_html) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} | Crosswalk</title>
<style>
  body {{ margin:0; background:#0C1618; color:#9AA09B;
         font-family:Inter,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:640px; margin:0 auto; padding:64px 24px; }}
  .eyebrow {{ font-size:12px; letter-spacing:0.08em; text-transform:uppercase;
             color:#5C6466; margin-bottom:16px; }}
  .eyebrow span {{ color:#C7F23E; margin-right:6px; }}
  h1 {{ color:#E9E8E1; font-size:26px; font-weight:700; margin:0 0 16px; }}
  p {{ line-height:1.55; margin:0 0 14px; }}
  b {{ color:#E9E8E1; font-weight:600; }}
  .card {{ background:#15252A; border-radius:12px; padding:20px 22px;
          margin:20px 0; }}
  .kpi {{ color:#C7F23E; font-weight:700; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  td, th {{ padding:6px 8px; text-align:left; border-bottom:1px solid #1F3238;
           color:#9AA09B; }}
  th {{ color:#5C6466; font-weight:600; text-transform:uppercase;
       font-size:11px; letter-spacing:0.05em; }}
  .muted {{ color:#5C6466; font-size:12px; margin-top:28px; }}
</style></head><body><div class="wrap">
<div class="eyebrow"><span>&#9679;</span>Crosswalk mapping table</div>
<h1>{_esc(title)}</h1>
{body_html}
<p class="muted">Behavioral Graph by Crosswalk</p>
</div></body></html>"""


def _state_meta_html(state) -> str:
    state = state or {}
    return (f"<div class='card'><p><b>{int(state.get('row_count') or 0)}</b> "
            f"proposed row(s) across "
            f"<b>{int(state.get('brand_count') or 0)}</b> brand(s).</p></div>")


def _rows_table_html(rows, cap=45) -> str:
    rows = rows or []
    body = "".join(
        f"<tr><td>{_esc(r.get('brand', ''))}</td>"
        f"<td>{_esc(r.get('hostname', ''))}</td>"
        f"<td>{_esc(r.get('section', ''))}</td></tr>"
        for r in rows[:cap])
    more = (f"<p class='muted'>and {len(rows) - cap} more row(s).</p>"
            if len(rows) > cap else "")
    if not body:
        return ""
    return (f"<div class='card'><table><tr><th>Brand</th><th>Hostname</th>"
            f"<th>Section</th></tr>{body}</table>{more}</div>")


def _sheet_note_html(sheet) -> str:
    sheet = sheet or {}
    if sheet.get("appended"):
        return (f"<p>The same {int(sheet['appended'])} row(s) were also "
                f"added to the mapping sheet.</p>")
    if sheet.get("skipped") == "dry run":
        return ""
    return ("<p>The mapping sheet was not updated automatically (sheet "
            "access is not configured yet), so please paste the CSV rows "
            "into the sheet as usual.</p>")


def _confirmation_page(state, ingest, sheet, dry_run: bool) -> str:
    inserted = int(ingest.get("inserted") or 0)
    skipped = ingest.get("skipped") or []
    skip_html = ""
    if skipped:
        items = "".join(
            f"<tr><td>{_esc(s.get('brand', ''))}</td>"
            f"<td>{_esc(s.get('hostname', ''))}</td>"
            f"<td>{_esc(s.get('reason', ''))}</td></tr>"
            for s in skipped[:30])
        skip_html = (f"<div class='card'><p><b>{len(skipped)}</b> row(s) "
                     f"skipped:</p><table><tr><th>Brand</th><th>Hostname"
                     f"</th><th>Reason</th></tr>{items}</table></div>")
    if dry_run:
        title = "Dry run complete"
        lead = (f"<p>This proposal is a format test. On a real approval, "
                f"<span class='kpi'>{inserted}</span> row(s) would be added "
                f"to the mapping table. <b>No changes were made.</b></p>")
    else:
        title = "Mapping approved"
        lead = (f"<p><b>{_esc((state or {}).get('csv_name') or '')}</b> is "
                f"approved. <span class='kpi'>{inserted}</span> row(s) were "
                f"added to the mapping table.</p>")
    return _page(
        title,
        lead + _rows_table_html(ingest.get("inserted_rows"))
        + skip_html + ("" if dry_run else _sheet_note_html(sheet)))


def _already_processed_page(state) -> str:
    state = state or {}
    status = state.get("status", "unknown")
    if status == "rejected":
        body = ("<p>This mapping proposal was already <b>rejected</b>. "
                "No changes were made.</p>")
    elif status == "approved":
        ingest = state.get("ingest") or {}
        n = int(ingest.get("inserted") or 0)
        k = len(ingest.get("skipped") or [])
        if state.get("dry_run"):
            body = ("<p>This format-test proposal was already processed "
                    "as a dry run. No changes were made.</p>")
        elif state.get("ingest_status") == "done":
            body = (f"<p>This mapping proposal was already <b>approved"
                    f"</b>: <span class='kpi'>{n}</span> row(s) added, "
                    f"{k} skipped. Nothing was ingested twice.</p>")
        else:
            body = ("<p>This mapping proposal was already approved and "
                    "its ingest is in progress. Nothing was ingested "
                    "twice.</p>")
    else:
        body = "<p>This mapping proposal was already processed.</p>"
    return _page("Already processed", body)
