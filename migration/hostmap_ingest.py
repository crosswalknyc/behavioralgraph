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
    """Hostmap CSV S3 key. Kept as-is for backward compat: legacy
    approvals (single-section, from before the tri-state extension)
    stored the hostmap CSV at ``<id>.csv``; new persists still write
    the hostmap CSV here so the reader sees an unchanged shape."""
    return f"{APPROVAL_PREFIX}/{approval_id}.csv"


def _content_csv_key(approval_id) -> str:
    """Content-map CSV S3 key (Jenna 2026-09-21). New persists write
    the secondary content-map CSV here alongside the hostmap CSV;
    legacy approvals have no such file."""
    return f"{APPROVAL_PREFIX}/{approval_id}.content.csv"


def _update_json(bucket, key, mutate_fn):
    try:
        from migration.s3_json_state import update_json
    except ImportError:
        from s3_json_state import update_json  # type: ignore
    return update_json(bucket, key, mutate_fn, s3=_s3_client())


# ---------------------------------------------------------------------------
# State shape (Jenna 2026-09-21, tri-state extension)
# ---------------------------------------------------------------------------
# A single approval package holds up to TWO independently-decidable
# sides: ``hostmap`` (the mapping-table proposals) and ``content`` (the
# content_mapping proposals). Each side has its own per-side status
# (pending / approved / rejected / not_included) and its own ingest
# result block; ``status`` at the top of the document is a computed
# rollup. Four action verbs sign one HMAC token each:
#
#     approve_all       -> approve every side that's still pending
#     approve_hostmap   -> flip only the hostmap side, if pending
#     approve_content   -> flip only the content side, if pending
#     reject            -> flip every pending side to rejected
#
# Backward compat: legacy states (from before this extension) carry
# only top-level ``csv_key``, ``row_count``, ``brand_count``, ``status``
# and no sub-blocks. ``load_approval`` lifts them into the new shape on
# read (never writes back; the S3 object stays byte-equal until a real
# transition rewrites it). The legacy ``approve`` action is treated as
# ``approve_all`` in the new code, so links printed by earlier email
# builds continue to work.
_PER_SIDE_INITIAL = {
    "status": "pending",
    "csv_key": "",
    "csv_name": "",
    "row_count": 0,
    "ingest_status": "not_run",
    "ingest": None,
    "sheet": None,
}


def _side_not_included(side_key: str) -> dict:
    """Placeholder block for a side that isn't part of this approval."""
    return {**_PER_SIDE_INITIAL, "status": "not_included",
            "ingest_status": "not_run"}


def _rollup_status(state: dict) -> str:
    """Compute the top-level status from the two side blocks.
    - all pending (or one pending + one not_included) -> "pending"
    - all decided approved -> "approved"
    - all decided rejected -> "rejected"
    - mix of decided (one approved, one rejected) -> "closed"
    - any pending + any decided -> "partial"
    """
    sides = [state.get("hostmap") or {}, state.get("content") or {}]
    live = [s for s in sides if s.get("status") != "not_included"]
    if not live:
        return "pending"
    statuses = {s.get("status", "pending") for s in live}
    if statuses == {"pending"}:
        return "pending"
    if statuses == {"approved"}:
        return "approved"
    if statuses == {"rejected"}:
        return "rejected"
    if statuses == {"approved", "rejected"}:
        return "closed"
    return "partial"


def _upgrade_legacy_state(raw: dict) -> dict:
    """Return a new-shape copy of a legacy state document. Does NOT
    mutate the input and does NOT rewrite the S3 object. The upgraded
    document is safe to feed to every new handler, since a legacy
    state has no content side by definition."""
    if not isinstance(raw, dict):
        return raw
    if "hostmap" in raw or "content" in raw:
        return raw  # already new-shape
    up = dict(raw)
    up["hostmap"] = {
        "status": raw.get("status", "pending"),
        "csv_key": raw.get("csv_key", ""),
        "csv_name": raw.get("csv_name", ""),
        "row_count": int(raw.get("row_count") or 0),
        "brand_count": int(raw.get("brand_count") or 0),
        "decided_at": (raw.get("approved_at") or raw.get("rejected_at")
                       or None),
        "ingest_status": raw.get("ingest_status", "not_run"),
        "ingest": raw.get("ingest"),
        "sheet": raw.get("sheet"),
    }
    up["content"] = _side_not_included("content")
    return up


def load_approval(approval_id):
    """(state dict or None) for an approval id, normalized to
    new-shape (per-side blocks). Never rewrites the S3 object."""
    aid = str(approval_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,80}", aid):
        return None
    try:
        body = _s3_client().get_object(
            Bucket=APPROVAL_BUCKET, Key=_state_key(aid),
        )["Body"].read().decode("utf-8")
        raw = json.loads(body)
    except Exception:
        return None
    return _upgrade_legacy_state(raw)


def load_approval_csv(approval_id, side: str = "hostmap") -> str:
    """Load a per-side CSV from S3. ``side`` is ``hostmap`` (default)
    or ``content``. Returns '' on any failure (missing file, bad ACL,
    S3 hiccup) - callers record a retryable state and never raise."""
    key = (_content_csv_key(str(approval_id)) if side == "content"
           else _csv_key(str(approval_id)))
    try:
        return _s3_client().get_object(
            Bucket=APPROVAL_BUCKET, Key=key,
        )["Body"].read().decode("utf-8")
    except Exception:
        return ""


def persist_approval(csv_text, csv_name, subject, requested_by="",
                     dry_run: bool = False, base_url=None,
                     recipients=None,
                     content_csv_text=None, content_csv_name=None) -> dict:
    """Stage an approval package for one-click ingest. Supports two
    sides (Jenna 2026-09-21):

    - Hostmap section (``csv_text`` / ``csv_name``): the standard
      Mapping_Table_V1 CSV (BRAND, HOSTNAME, CATEGORY, SECTION).
      Backward-compatible; every existing caller passes just this.
    - Content section (``content_csv_text`` / ``content_csv_name``,
      optional): five-column content-mapping CSV (SHOW, URL,
      PRODUCTION, PLATFORM, SEASON) when the build's BRAND INPUT
      resolved researched content URLs.

    Writes both CSVs to S3 alongside a state JSON that carries per-side
    blocks. Returns:

        {"id", "row_count", "content_row_count",
         "approve_url",           # legacy alias for approve_all
         "approve_all_url",
         "approve_hostmap_url",
         "approve_content_url",   # None when content_csv_text is None
         "reject_url"}

    ``dry_run=True`` stages a state whose approve actions only REPORT
    what would be inserted (used by format-test emails).

    Raises when the secret is unresolvable or S3 is unreachable; the
    email builder catches and sends without buttons (fail-safe).
    """
    secret = approval_secret()
    if not secret:
        raise RuntimeError("no approval secret configured "
                           "(HOSTMAP_APPROVAL_SECRET or the S3 fallback)")
    rows = parse_mapping_csv(csv_text) if csv_text else []
    content_rows = []
    if content_csv_text:
        # Parse the content CSV via the sibling module so schema
        # validation matches the ingest path exactly.
        try:
            from migration.content_mapping_ingest import (
                parse_content_mapping_csv)
        except ImportError:
            from content_mapping_ingest import (  # type: ignore
                parse_content_mapping_csv)
        content_rows = parse_content_mapping_csv(content_csv_text)
    if not rows and not content_rows:
        raise ValueError("approval package has no hostmap or content rows")

    stamp = datetime.now(timezone.utc)
    aid = f"hm_{stamp.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"
    s3 = _s3_client()

    # Write the CSVs first, then the state (so a state that references
    # a CSV is guaranteed to find it on the S3 GET).
    if rows:
        s3.put_object(Bucket=APPROVAL_BUCKET, Key=_csv_key(aid),
                      Body=str(csv_text).encode("utf-8"),
                      ContentType="text/csv")
    if content_rows:
        s3.put_object(Bucket=APPROVAL_BUCKET,
                      Key=_content_csv_key(aid),
                      Body=str(content_csv_text).encode("utf-8"),
                      ContentType="text/csv")

    hostmap_block = _side_not_included("hostmap")
    content_block = _side_not_included("content")
    if rows:
        hostmap_block = {
            "status": "pending",
            "csv_key": _csv_key(aid),
            "csv_name": str(csv_name or f"{aid}.csv"),
            "row_count": len(rows),
            "brand_count": len({fold_brand(r["BRAND"]) for r in rows}),
            "decided_at": None,
            "ingest_status": "not_run",
            "ingest": None,
            "sheet": None,
        }
    if content_rows:
        show_count = len({str(r.get("SHOW") or "").strip()
                          for r in content_rows if r.get("SHOW")})
        content_block = {
            "status": "pending",
            "csv_key": _content_csv_key(aid),
            "csv_name": str(content_csv_name or f"{aid}.content.csv"),
            "row_count": len(content_rows),
            "show_count": show_count,
            "decided_at": None,
            "ingest_status": "not_run",
            "ingest": None,
            "sheet": None,
        }

    state = {
        "id": aid,
        "status": "pending",
        "created_at": stamp.isoformat(),
        "requested_by": str(requested_by or ""),
        "subject": str(subject or ""),
        "dry_run": bool(dry_run),
        "recipients": _dedupe_emails(recipients),
        # Per-side blocks (source of truth for the new flow):
        "hostmap": hostmap_block,
        "content": content_block,
        # Legacy top-level mirrors of the hostmap block, so an older
        # reader (or an existing external cache) still sees the shape
        # it expects. Never used by the new handler.
        "csv_name": hostmap_block.get("csv_name", ""),
        "csv_key": hostmap_block.get("csv_key", ""),
        "row_count": hostmap_block.get("row_count", 0),
        "brand_count": hostmap_block.get("brand_count", 0),
    }
    s3.put_object(Bucket=APPROVAL_BUCKET, Key=_state_key(aid),
                  Body=json.dumps(state, indent=2).encode("utf-8"),
                  ContentType="application/json")

    base = (base_url or os.environ.get("HOSTMAP_APPROVAL_BASE_URL")
            or DEFAULT_BASE_URL).rstrip("/")

    def _url(action):
        return (f"{base}/api/hostmap-mapping/{action}?id={aid}"
                f"&token={sign_token(aid, action, secret)}")

    return {
        "id": aid,
        "row_count": len(rows),
        "content_row_count": len(content_rows),
        "brand_count": hostmap_block.get("brand_count", 0),
        "show_count": content_block.get("show_count", 0),
        # Legacy alias (older email builders call it approve_url).
        # Points at the same handler as approve_all so an email sent
        # BEFORE the tri-state extension still works after this ships.
        "approve_url": _url("approve"),
        "approve_all_url": _url("approve_all"),
        "approve_hostmap_url": (_url("approve_hostmap") if rows else None),
        "approve_content_url": (_url("approve_content") if content_rows
                                else None),
        "reject_url": _url("reject"),
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


def _side_label(side: str) -> str:
    if side == "content":
        return "content mapping"
    return "hostmap mapping"


def _notify_decision(state, action, sides_touched=None) -> None:
    """Reply-all on a decision: tell every original recipient that the
    proposal was approved or rejected, so the rest of the group knows
    they do not need to act (Jenna 2026-08-31). Fires exactly once per
    CAS-claimed transition. Strictly fail-safe: a send failure never
    blocks or breaks the approval flow.

    ``sides_touched`` is a list of "hostmap" / "content" describing
    which sides this action actually flipped. When omitted, defaults to
    every side that's in the "not_included" position (legacy single
    section)."""
    try:
        recips = _dedupe_emails((state or {}).get("recipients"))
        if not recips:
            return
        subject_name = (str((state or {}).get("subject") or "").strip()
                        or "this profile")

        # Build the per-side summary from the (already-transitioned)
        # state document. Sides not touched by this action are skipped
        # so the notification only speaks to what THIS click did.
        touched = list(sides_touched or [])
        if not touched:
            for side in ("hostmap", "content"):
                if (state or {}).get(side, {}).get("status") \
                        in ("approved", "rejected"):
                    touched.append(side)

        approved_bits = []
        rejected_bits = []
        for side in touched:
            block = (state or {}).get(side) or {}
            n_rows = int(block.get("row_count") or 0)
            side_name = _side_label(side)
            if block.get("status") == "approved":
                if side == "hostmap":
                    n_brands = int(block.get("brand_count") or 0)
                    approved_bits.append(
                        f"{side_name} ({n_rows} row(s) across "
                        f"{n_brands} brand(s))")
                else:
                    n_shows = int(block.get("show_count") or 0)
                    approved_bits.append(
                        f"{side_name} ({n_rows} row(s) across "
                        f"{n_shows} show(s))")
            elif block.get("status") == "rejected":
                rejected_bits.append(f"{side_name} ({n_rows} row(s))")

        lead_lines = []
        if approved_bits:
            lead_lines.append(
                f"Approved and added to the mapping tables: "
                f"{', '.join(approved_bits)}.")
        if rejected_bits:
            lead_lines.append(
                f"Declined (no changes made): "
                f"{', '.join(rejected_bits)}.")
        # Note if one side is still pending (partial approve).
        for side in ("hostmap", "content"):
            block = (state or {}).get(side) or {}
            if block.get("status") == "pending":
                lead_lines.append(
                    f"The {_side_label(side)} section of this proposal "
                    f"is still open. Anyone on this thread can approve "
                    f"or reject it independently.")

        if not lead_lines:
            return  # nothing meaningful happened; skip the notice

        lead = " ".join(lead_lines)
        text_body = (
            f"Mapping additions for {subject_name}:\n\n"
            f"{lead}\n\n"
            "No further action is needed on the parts that have been "
            "decided.\n"
        )
        html_body = (
            '<html><body style="font-family:-apple-system,Helvetica,Arial,'
            'sans-serif;color:#333;">'
            f'<p><b>Mapping additions for {_esc(subject_name)}</b></p>'
            f'<p>{_esc(lead)}</p>'
            '<p style="color:#555;">No further action is needed on the '
            'parts that have been decided.</p>'
            '<p style="color:#999;font-size:12px;margin-top:24px;">'
            'Behavioral Graph by Crosswalk</p></body></html>'
        )
        rollup = _rollup_status(state)
        if rollup == "approved":
            tail = "approved"
        elif rollup == "rejected":
            tail = "declined"
        elif rollup == "partial":
            tail = "partial decision"
        else:
            tail = "decision recorded"
        subject_line = f"Mapping additions for {subject_name}: {tail}"
        _send_group_email(recips, subject_line, text_body, html_body)
    except Exception as e:
        print(f"  [hostmap-ingest] decision notify failed (non-fatal): {e}")


def _claim_side_transition(approval_id, side, target) -> tuple:
    """CAS: flip ONE side (hostmap or content) from pending to
    approved/rejected. Returns (claimed, state). When not claimed,
    ``state`` is the current (upgraded, new-shape) state for
    rendering."""
    result = {"claimed": False}

    def mutate(obj):
        if not isinstance(obj, dict) or not obj.get("id"):
            return None
        upgraded = _upgrade_legacy_state(obj)
        block = upgraded.get(side) or {}
        if block.get("status") != "pending":
            return None
        block["status"] = target
        block["decided_at"] = datetime.now(timezone.utc).isoformat()
        if target == "approved":
            block["ingest_status"] = "running"
        upgraded[side] = block
        upgraded["status"] = _rollup_status(upgraded)
        # Keep legacy top-level mirror in sync if this is the hostmap
        # side of a legacy state.
        if side == "hostmap":
            upgraded["csv_name"] = block.get("csv_name", "")
            upgraded["csv_key"] = block.get("csv_key", "")
            upgraded["row_count"] = int(block.get("row_count") or 0)
            upgraded["brand_count"] = int(block.get("brand_count") or 0)
            if target == "approved":
                upgraded["approved_at"] = block["decided_at"]
                upgraded["ingest_status"] = "running"
            else:
                upgraded["rejected_at"] = block["decided_at"]
        result["claimed"] = True
        result["state"] = upgraded
        return upgraded

    try:
        _update_json(APPROVAL_BUCKET, _state_key(approval_id), mutate)
    except Exception as e:
        print(f"  [hostmap-ingest] state transition failed "
              f"({side}={target}): {e}")
        return False, load_approval(approval_id)
    if result["claimed"]:
        return True, result.get("state")
    return False, load_approval(approval_id)


def _claim_multi_transition(approval_id, sides, target) -> tuple:
    """Same as _claim_side_transition but for MULTIPLE sides in one
    atomic S3 write. Only sides that are ``pending`` get flipped; sides
    already decided are left untouched. Returns (sides_flipped, state).
    ``sides_flipped`` is a list of the side keys that actually
    transitioned (may be empty when every side is already decided)."""
    result = {"flipped": []}

    def mutate(obj):
        if not isinstance(obj, dict) or not obj.get("id"):
            return None
        upgraded = _upgrade_legacy_state(obj)
        flipped = []
        now = datetime.now(timezone.utc).isoformat()
        for side in sides:
            block = upgraded.get(side) or {}
            if block.get("status") != "pending":
                continue
            block["status"] = target
            block["decided_at"] = now
            if target == "approved":
                block["ingest_status"] = "running"
            upgraded[side] = block
            flipped.append(side)
            if side == "hostmap":
                upgraded["csv_name"] = block.get("csv_name", "")
                upgraded["csv_key"] = block.get("csv_key", "")
                upgraded["row_count"] = int(block.get("row_count") or 0)
                upgraded["brand_count"] = int(block.get("brand_count") or 0)
                if target == "approved":
                    upgraded["approved_at"] = now
                    upgraded["ingest_status"] = "running"
                else:
                    upgraded["rejected_at"] = now
        if not flipped:
            return None  # nothing to do; keep the object as-is
        upgraded["status"] = _rollup_status(upgraded)
        result["flipped"] = flipped
        result["state"] = upgraded
        return upgraded

    try:
        _update_json(APPROVAL_BUCKET, _state_key(approval_id), mutate)
    except Exception as e:
        print(f"  [hostmap-ingest] multi transition failed "
              f"({sides}={target}): {e}")
        return [], load_approval(approval_id)
    if result["flipped"]:
        return result["flipped"], result.get("state")
    return [], load_approval(approval_id)


def _record_side_result(approval_id, side, ingest, sheet, status) -> None:
    """Persist the ingest result for one side. Mirrors the top-level
    fields when the side is hostmap so a legacy reader still sees the
    old shape."""
    def mutate(obj):
        if not isinstance(obj, dict) or not obj.get("id"):
            return None
        upgraded = _upgrade_legacy_state(obj)
        block = upgraded.get(side) or {}
        block["ingest_status"] = status
        block["ingest"] = ingest
        block["sheet"] = sheet
        upgraded[side] = block
        if side == "hostmap":
            upgraded["ingest_status"] = status
            upgraded["ingest"] = ingest
            upgraded["sheet"] = sheet
        return upgraded

    try:
        _update_json(APPROVAL_BUCKET, _state_key(approval_id), mutate)
    except Exception as e:
        print(f"  [hostmap-ingest] result record failed ({side}): {e}")


# ---------------------------------------------------------------------------
# Endpoint flow + confirmation pages
# ---------------------------------------------------------------------------
_ACTION_VERBS = (
    "approve", "approve_all", "approve_hostmap", "approve_content",
    "reject",
)


def _sides_pending(state, sides) -> list:
    """Return the subset of sides currently in the 'pending' state on
    the (upgraded) state dict."""
    out = []
    for side in sides:
        block = (state or {}).get(side) or {}
        if block.get("status") == "pending":
            out.append(side)
    return out


def handle_approval_action(approval_id, action, token) -> tuple:
    """Full endpoint flow. Returns (html, http_status).

    Supports the tri-state action set (Jenna 2026-09-21):

    approve             -> alias for approve_all (backward compat with
                           links from emails sent before the extension)
    approve_all         -> flip every pending side to approved, ingest
                           each; already-decided sides are untouched
    approve_hostmap     -> flip only the hostmap side, if pending
    approve_content     -> flip only the content side, if pending
    reject              -> flip every pending side to rejected

    approve on approved -> already-processed page (retries the ingest
                           only when the recorded ingest FAILED; the
                           dedupe makes the retry safe)
    approve on rejected -> nothing happens page
    bad token / id      -> refusal page
    """
    action = str(action or "").strip().lower()
    aid = str(approval_id or "").strip()
    if action not in _ACTION_VERBS:
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

    # Which sides does this action touch? approve_hostmap/reject touch
    # one specific side; approve/approve_all/reject touch both.
    if action == "approve_hostmap":
        target_sides = ["hostmap"]
    elif action == "approve_content":
        target_sides = ["content"]
    else:  # approve, approve_all, reject
        target_sides = ["hostmap", "content"]

    # Filter target_sides to sides that actually exist on this
    # approval (skip "not_included" sides).
    target_sides = [s for s in target_sides
                    if (state or {}).get(s, {}).get("status") != "not_included"]
    if not target_sides:
        return _page(
            "Section not available",
            "<p>This approval package does not include the section "
            "targeted by this link. No changes were made.</p>"), 200

    # -------------------------------------------------------------------
    # REJECT
    # -------------------------------------------------------------------
    if action == "reject":
        pending = _sides_pending(state, target_sides)
        if not pending:
            return _already_processed_page(state), 200
        flipped, state = _claim_multi_transition(aid, pending, "rejected")
        if not flipped:
            return _already_processed_page(state), 200
        _notify_decision(state, "reject", sides_touched=flipped)
        return _rejection_confirmation_page(state, flipped), 200

    # -------------------------------------------------------------------
    # APPROVE (any variant)
    # -------------------------------------------------------------------
    # Retry path: side-specific action + already approved but ingest
    # failed = re-run the ingest (dedupe keeps it safe).
    if len(target_sides) == 1:
        side = target_sides[0]
        block = (state or {}).get(side) or {}
        if block.get("status") == "rejected":
            return _page(
                "Section was rejected",
                f"<p>The {_side_label(side)} section of this proposal "
                f"was rejected earlier. No changes were made.</p>"), 200
        if block.get("status") == "approved":
            if block.get("ingest_status") == "failed":
                return _run_side_ingest(aid, side, state)
            return _already_processed_page(state), 200
        # Fresh pending -> claim + ingest
        claimed, state = _claim_side_transition(aid, side, "approved")
        if not claimed:
            return _already_processed_page(state), 200
        _notify_decision(state, "approve", sides_touched=[side])
        return _run_side_ingest(aid, side, state)

    # approve / approve_all: flip every pending side, then ingest each
    pending = _sides_pending(state, target_sides)
    if not pending:
        # Nothing pending; retry any failed ingests, else already-processed
        for side in target_sides:
            block = (state or {}).get(side) or {}
            if block.get("status") == "approved" \
                    and block.get("ingest_status") == "failed":
                _run_side_ingest(aid, side, state)  # side-effect retry
                state = load_approval(aid) or state
        return _already_processed_page(state), 200
    flipped, state = _claim_multi_transition(aid, pending, "approved")
    if not flipped:
        # Race: another click grabbed the transitions first. Fall
        # through to the already-processed page - the winning click
        # is running the ingest.
        return _already_processed_page(state), 200
    _notify_decision(state, "approve", sides_touched=flipped)
    return _run_multi_ingest(aid, flipped, state)


def _run_side_ingest(approval_id, side, state) -> tuple:
    """Run the ingest for one side and render its confirmation page."""
    ingest, sheet, status = _do_ingest(approval_id, side, state)
    _record_side_result(approval_id, side, ingest, sheet, status)
    state = load_approval(approval_id) or state
    return _confirmation_page(state,
                              side_results={side: (ingest, sheet)},
                              dry_run=bool((state or {}).get("dry_run"))), 200


def _run_multi_ingest(approval_id, sides, state) -> tuple:
    """Run ingests for MULTIPLE sides sequentially. Each side records
    its own result; a failure on one side does not block the other.
    Returns a combined confirmation page."""
    side_results = {}
    for side in sides:
        ingest, sheet, status = _do_ingest(approval_id, side, state)
        _record_side_result(approval_id, side, ingest, sheet, status)
        side_results[side] = (ingest, sheet)
    state = load_approval(approval_id) or state
    return _confirmation_page(state, side_results=side_results,
                              dry_run=bool((state or {}).get("dry_run"))), 200


def _do_ingest(approval_id, side, state) -> tuple:
    """Load one side's CSV, parse, and ingest. Returns
    (ingest_dict, sheet_dict, status_str). Never raises."""
    dry_run = bool((state or {}).get("dry_run"))
    csv_text = load_approval_csv(approval_id, side=side)
    if not csv_text:
        return ({"error": "csv missing", "inserted": 0, "skipped": [],
                 "inserted_rows": []},
                {"skipped": "csv missing"},
                "failed")
    try:
        if side == "content":
            try:
                from migration.content_mapping_ingest import (
                    parse_content_mapping_csv,
                    ingest_content_mapping_rows,
                    append_content_rows_to_sheet)
            except ImportError:
                from content_mapping_ingest import (  # type: ignore
                    parse_content_mapping_csv,
                    ingest_content_mapping_rows,
                    append_content_rows_to_sheet)
            rows = parse_content_mapping_csv(csv_text)
            ingest = ingest_content_mapping_rows(rows, dry_run=dry_run)
        else:
            rows = parse_mapping_csv(csv_text)
            ingest = ingest_mapping_rows(rows, dry_run=dry_run)
    except Exception as e:
        return ({"error": str(e)[:300], "inserted": 0, "skipped": [],
                 "inserted_rows": []},
                {"skipped": "ingest failed"},
                "failed")
    if ingest.get("error"):
        return (ingest, {"skipped": "ingest error"}, "failed")
    if dry_run:
        return (ingest, {"skipped": "dry run"}, "dry_run")
    try:
        if side == "content":
            sheet = append_content_rows_to_sheet(
                ingest.get("inserted_rows") or [])
        else:
            sheet = append_rows_to_sheet(ingest.get("inserted_rows") or [])
    except Exception as e:
        sheet = {"error": str(e)[:200], "skipped": "sheet append failed"}
    return (ingest, sheet, "done")


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
    """One-line summary of both sides. Content side only listed when
    it exists on this approval."""
    state = state or {}
    bits = []
    hm = state.get("hostmap") or {}
    if hm.get("status") != "not_included":
        bits.append(
            f"<b>{int(hm.get('row_count') or 0)}</b> hostmap row(s) "
            f"across <b>{int(hm.get('brand_count') or 0)}</b> brand(s)")
    cm = state.get("content") or {}
    if cm.get("status") != "not_included":
        bits.append(
            f"<b>{int(cm.get('row_count') or 0)}</b> content row(s) "
            f"across <b>{int(cm.get('show_count') or 0)}</b> show(s)")
    inner = " and ".join(bits) or "no rows"
    return f"<div class='card'><p>{inner}.</p></div>"


def _rows_table_html_hostmap(rows, cap=45) -> str:
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


def _rows_table_html_content(rows, cap=45) -> str:
    rows = rows or []
    body = "".join(
        f"<tr><td>{_esc(r.get('show', ''))}</td>"
        f"<td>{_esc(r.get('platform', ''))}</td>"
        f"<td>{_esc(r.get('season', ''))}</td>"
        f"<td>{_esc(r.get('url', ''))}</td></tr>"
        for r in rows[:cap])
    more = (f"<p class='muted'>and {len(rows) - cap} more row(s).</p>"
            if len(rows) > cap else "")
    if not body:
        return ""
    return (f"<div class='card'><table><tr><th>Show</th><th>Platform</th>"
            f"<th>Season</th><th>URL</th></tr>{body}</table>{more}</div>")


def _rows_table_html_for(side, rows, cap=45) -> str:
    if side == "content":
        return _rows_table_html_content(rows, cap=cap)
    return _rows_table_html_hostmap(rows, cap=cap)


def _sheet_note_html(sheet, side="hostmap") -> str:
    sheet = sheet or {}
    if sheet.get("appended"):
        n = int(sheet["appended"])
        tab = sheet.get("tab") or ""
        tab_bit = f" (tab: <b>{_esc(tab)}</b>)" if tab else ""
        return (f"<p>The same {n} row(s) were also added to the "
                f"mapping sheet{tab_bit}.</p>")
    if sheet.get("skipped") == "dry run":
        return ""
    if sheet.get("error"):
        return ("<p>The mapping table was updated, but the mapping sheet "
                "append failed. Please paste the rows into the sheet "
                "manually.</p>")
    return ("<p>The mapping sheet was not updated automatically (sheet "
            "access is not configured yet), so please paste the CSV rows "
            "into the sheet as usual.</p>")


def _confirmation_page(state, side_results, dry_run: bool) -> str:
    """Confirmation page after one or more sides were approved.
    ``side_results`` = {"hostmap": (ingest, sheet), "content": (ingest,
    sheet)}. Renders one section per side that was touched."""
    state = state or {}
    side_results = side_results or {}
    subject_name = str(state.get("subject") or "").strip()

    sections = []
    total_inserted = 0
    for side in ("hostmap", "content"):
        if side not in side_results:
            continue
        ingest, sheet = side_results[side]
        n = int((ingest or {}).get("inserted") or 0)
        total_inserted += n
        skipped = (ingest or {}).get("skipped") or []
        block = (state or {}).get(side) or {}
        csv_name = block.get("csv_name") or ""

        if dry_run:
            head = (f"<p><b>{_esc(_side_label(side).title())} - {_esc(csv_name)}</b>: "
                    f"dry run. On a real approval, "
                    f"<span class='kpi'>{n}</span> row(s) would be added "
                    f"to the mapping table.</p>")
        else:
            head = (f"<p><b>{_esc(_side_label(side).title())} - {_esc(csv_name)}</b>: "
                    f"<span class='kpi'>{n}</span> row(s) added to the "
                    f"mapping table.</p>")

        skip_html = ""
        if skipped:
            if side == "content":
                items = "".join(
                    f"<tr><td>{_esc(s.get('show', ''))}</td>"
                    f"<td>{_esc(s.get('platform', ''))}</td>"
                    f"<td>{_esc(s.get('url', ''))}</td>"
                    f"<td>{_esc(s.get('reason', ''))}</td></tr>"
                    for s in skipped[:30])
                skip_html = (f"<div class='card'><p><b>{len(skipped)}</b> row(s) "
                             f"skipped on the content side:</p><table>"
                             f"<tr><th>Show</th><th>Platform</th>"
                             f"<th>URL</th><th>Reason</th></tr>{items}"
                             f"</table></div>")
            else:
                items = "".join(
                    f"<tr><td>{_esc(s.get('brand', ''))}</td>"
                    f"<td>{_esc(s.get('hostname', ''))}</td>"
                    f"<td>{_esc(s.get('reason', ''))}</td></tr>"
                    for s in skipped[:30])
                skip_html = (f"<div class='card'><p><b>{len(skipped)}</b> row(s) "
                             f"skipped on the hostmap side:</p><table>"
                             f"<tr><th>Brand</th><th>Hostname</th>"
                             f"<th>Reason</th></tr>{items}</table></div>")

        rows_html = _rows_table_html_for(
            side, (ingest or {}).get("inserted_rows"))
        sheet_html = ("" if dry_run else _sheet_note_html(sheet, side=side))
        sections.append(head + rows_html + skip_html + sheet_html)

    # Pending-side reminder (e.g. approved hostmap but content still open)
    still_pending = []
    for side in ("hostmap", "content"):
        block = (state or {}).get(side) or {}
        if block.get("status") == "pending":
            still_pending.append(_side_label(side))
    pending_html = ""
    if still_pending:
        pending_html = (
            f"<p><em>The {' and '.join(still_pending)} section(s) of this "
            f"proposal are still open. Anyone on the thread can approve "
            f"or reject them independently.</em></p>")

    if dry_run:
        title = "Dry run complete"
    elif total_inserted:
        title = ("Mapping approved" if len(side_results) == 1
                 else "Mapping approved (both sections)")
    else:
        title = "Approval recorded"

    lead = ""
    if subject_name:
        lead = (f"<p>Approval recorded for <b>{_esc(subject_name)}</b>."
                f"</p>")
    return _page(title, lead + "".join(sections) + pending_html)


def _rejection_confirmation_page(state, sides) -> str:
    """Confirmation page after one or more sides were rejected."""
    state = state or {}
    subject_name = str(state.get("subject") or "").strip()
    parts = []
    for side in sides:
        block = state.get(side) or {}
        n = int(block.get("row_count") or 0)
        parts.append(
            f"<p>The <b>{_esc(_side_label(side).title())}</b> section "
            f"(<b>{_esc(block.get('csv_name') or '')}</b>, {n} row(s)) "
            f"was rejected. No changes were made.</p>")
    still_pending = []
    for side in ("hostmap", "content"):
        block = state.get(side) or {}
        if block.get("status") == "pending":
            still_pending.append(_side_label(side))
    if still_pending:
        parts.append(
            f"<p><em>The {' and '.join(still_pending)} section(s) of this "
            f"proposal are still open. Anyone on the thread can approve "
            f"or reject them independently.</em></p>")
    lead = ""
    if subject_name:
        lead = f"<p>Decision recorded for <b>{_esc(subject_name)}</b>.</p>"
    return _page("Section rejected", lead + "".join(parts))


def _already_processed_page(state) -> str:
    """Rendered when a click hits a link whose target sides are already
    decided. Summarizes both sides."""
    state = state or {}
    lines = []
    for side in ("hostmap", "content"):
        block = state.get(side) or {}
        status = block.get("status", "not_included")
        if status == "not_included":
            continue
        label = _side_label(side).title()
        if status == "pending":
            lines.append(f"<p><b>{_esc(label)}</b>: still pending.</p>")
        elif status == "rejected":
            lines.append(f"<p><b>{_esc(label)}</b>: rejected. No changes "
                         f"were made.</p>")
        elif status == "approved":
            ingest = block.get("ingest") or {}
            n = int(ingest.get("inserted") or 0)
            k = len(ingest.get("skipped") or [])
            if state.get("dry_run"):
                lines.append(f"<p><b>{_esc(label)}</b>: dry run "
                             f"processed. No changes were made.</p>")
            elif block.get("ingest_status") == "done":
                lines.append(f"<p><b>{_esc(label)}</b>: approved. "
                             f"<span class='kpi'>{n}</span> row(s) "
                             f"added, {k} skipped. Nothing was "
                             f"ingested twice.</p>")
            elif block.get("ingest_status") == "failed":
                lines.append(f"<p><b>{_esc(label)}</b>: approved but "
                             f"the ingest failed. Click the section's "
                             f"Approve link again to retry; dedupe "
                             f"prevents duplicates.</p>")
            else:
                lines.append(f"<p><b>{_esc(label)}</b>: approved. "
                             f"Ingest in progress.</p>")
    if not lines:
        lines.append("<p>This mapping proposal was already processed.</p>")
    return _page("Already processed", "".join(lines))
