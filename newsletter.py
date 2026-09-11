"""Crosswalk Newsletter: admin CMS + SES delivery + open/click tracking.

Personal Mailchimp for The Read and any later letter. Sends from the same
SES identity the dashboard completion mail uses (no_reply@crosswalknyc.com,
us-east-2). State and HTML live in s3://dashboard-inputs/system/newsletter/.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from html import escape
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, Response, jsonify, redirect, request, session

newsletter_bp = Blueprint("newsletter", __name__)

S3_BUCKET = os.environ.get("S3_BUCKET", "dashboard-inputs")
SES_REGION = os.environ.get("SES_REGION", "us-east-2")
FROM_EMAIL = "no_reply@crosswalknyc.com"
DEFAULT_FROM_NAME = "The Read"
DEFAULT_REPLY_TO = "hello@crosswalknyc.com"
DEFAULT_COMPANY_ADDRESS = "Crosswalk, 23465 Civic Center Way Bldg 9, Malibu, CA 90265"
DEFAULT_PUBLIC_BASE = "https://dashboard.crosswalknyc.com"
STATE_KEY = "system/newsletter/state.json"
CAMPAIGN_HTML_KEY = "system/newsletter/campaigns/{cid}.html"
ASSET_KEY = "system/newsletter/assets/{cid}/{name}"
SEND_KEY = "system/newsletter/sends/{cid}.json"
EVENTS_KEY = "system/newsletter/events/{cid}.jsonl"
DOWNLOAD_KEY = "system/newsletter/downloads/{cid}.json"
ATTACH_KEY = "system/newsletter/attachments/{cid}/{name}"

SEED_DIR = Path(__file__).resolve().parent / "newsletter_seed"
SEED_HTML = SEED_DIR / "the_read_creatorverse.html"
SEED_ASSETS = SEED_DIR / "assets" / "the-read-creatorverse"
SEED_CAMPAIGN_ID = "the-read-creatorverse"

TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
DATA_URI_RE = re.compile(
    r"data:image/(?P<fmt>[A-Za-z0-9.+-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+?)(?=(?:[\"')\s]))",
    re.I,
)
HREF_RE = re.compile(r"""href=(?P<q>["'])(?P<url>https?://[^"']+)(?P=q)""", re.I)
UNSUB_PLACEHOLDER_RE = re.compile(
    r"(?:%%unsubscribe%%|\{\{unsubscribe\}\}|\{unsubscribe\})", re.I
)

_state_lock = threading.Lock()
_scheduler_started = False
_seed_assets_done = False
_s3 = None
_ses = None


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _s3_client():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3")
    return _s3


def _ses_client():
    global _ses
    if _ses is None:
        import boto3
        _ses = boto3.client("ses", region_name=SES_REGION)
    return _ses


def _signing_secret() -> bytes:
    raw = (
        os.environ.get("NEWSLETTER_SIGNING_SECRET")
        or os.environ.get("SECRET_KEY")
        or "crosswalk-newsletter-dev"
    )
    return raw.encode("utf-8")


def _public_base(settings=None) -> str:
    settings = settings or {}
    for candidate in (
        (settings.get("public_base_url") or "").strip(),
        (os.environ.get("PUBLIC_APP_URL") or "").strip(),
        (os.environ.get("APP_URL") or "").strip(),
        DEFAULT_PUBLIC_BASE,
    ):
        if candidate:
            return candidate.rstrip("/")
    return DEFAULT_PUBLIC_BASE


def _new_id(prefix="nl"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _valid_email(value):
    value = (value or "").strip().lower()
    if not value or not EMAIL_RE.match(value):
        return ""
    return value


def _safe_filename(name):
    name = os.path.basename(name or "").strip()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or f"file-{uuid.uuid4().hex[:8]}"


def _content_type_for(name):
    ext = (name or "").rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "html": "text/html; charset=utf-8",
        "json": "application/json",
        "pdf": "application/pdf",
        "ppt": "application/vnd.ms-powerpoint",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xls": "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "csv": "text/csv",
        "zip": "application/zip",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Tokens (open / click / unsubscribe)
# ---------------------------------------------------------------------------

def sign_token(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(_signing_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    raw = f"{body}|{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_token(token: str):
    if not token:
        return None
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad).decode("utf-8")
        body, sig = raw.rsplit("|", 1)
    except Exception:
        return None
    expect = hmac.new(_signing_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def extract_data_uris(html: str):
    """Yield (full_match, filename, bytes) for every embedded image."""
    ext_map = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}
    n = 0
    for m in DATA_URI_RE.finditer(html or ""):
        n += 1
        fmt = (m.group("fmt") or "bin").lower().split("+")[0]
        raw = base64.b64decode(re.sub(r"\s+", "", m.group("data")))
        name = f"img{n:02d}.{ext_map.get(fmt, 'bin')}"
        yield m.group(0), name, raw


def rewrite_data_uris(html: str, url_for_name):
    """Replace data:image URIs. url_for_name(name, raw_bytes) -> url."""
    mapping = {}

    def repl(m):
        full = m.group(0)
        if full in mapping:
            return mapping[full]
        fmt = (m.group("fmt") or "bin").lower().split("+")[0]
        raw = base64.b64decode(re.sub(r"\s+", "", m.group("data")))
        name = f"img{len(mapping) + 1:02d}.{('jpg' if fmt in ('jpeg', 'jpg') else fmt)}"
        url = url_for_name(name, raw)
        mapping[full] = url
        return url

    return DATA_URI_RE.sub(repl, html or ""), list(mapping.values())


def collect_trackable_links(html: str):
    links = []
    seen = {}
    for m in HREF_RE.finditer(html or ""):
        url = m.group("url")
        if "/n/" in url:
            continue
        if url not in seen:
            seen[url] = len(links)
            links.append(url)
    return links


def build_send_template(html: str, asset_base: str, download_url: str = ""):
    """Turn stored HTML into a per-recipient template with placeholders."""
    html = html or ""
    if asset_base:
        html = html.replace("{{ASSET_BASE}}", asset_base.rstrip("/"))
    if download_url:
        html = html.replace("{{DOWNLOAD}}", download_url)
        html = re.sub(
            r'(href=["\'])mailto:hello@crosswalknyc\.com\?[^"\']*(["\'])',
            r"\1" + download_url + r"\2",
            html,
            flags=re.I,
        )
    html = UNSUB_PLACEHOLDER_RE.sub("{{UNSUB}}", html)
    links = []
    seen = {}

    def repl(m):
        url = m.group("url")
        if "/n/" in url or url.startswith("{{"):
            return m.group(0)
        if url not in seen:
            seen[url] = len(links)
            links.append(url)
        return f"href={m.group('q')}{{{{CLICK:{seen[url]}}}}}{m.group('q')}"

    html = HREF_RE.sub(repl, html)
    if "{{UNSUB}}" not in html:
        html = html.rstrip()
        if html.lower().endswith("</html>"):
            html = html[: -len("</html>")].rstrip()
        if html.lower().endswith("</body>"):
            html = html[: -len("</body>")].rstrip()
        html += (
            '<div style="font-family:Arial,sans-serif;font-size:12px;'
            'color:#7C878A;text-align:center;padding:18px 12px;">'
            '<a href="{{UNSUB}}" style="color:#547110;">Unsubscribe</a>'
            "</div></body></html>"
        )
    if "{{PIXEL}}" not in html:
        pixel = (
            '<img src="{{PIXEL}}" width="1" height="1" alt="" '
            'style="display:block;width:1px;height:1px;border:0;" />'
        )
        if re.search(r"</body>", html, re.I):
            html = re.sub(r"</body>", pixel + "</body>", html, count=1, flags=re.I)
        else:
            html += pixel
    return html, links


def personalize_html(template: str, campaign_id: str, email: str, links, base: str):
    email = _valid_email(email)
    unsub = f"{base}/n/u/{sign_token({'c': campaign_id, 'e': email, 'p': 'u'})}"
    pixel = f"{base}/n/o/{sign_token({'c': campaign_id, 'e': email, 'p': 'o'})}.gif"
    html = template.replace("{{UNSUB}}", unsub).replace("{{PIXEL}}", pixel)
    for i, dest in enumerate(links):
        tok = sign_token({"c": campaign_id, "e": email, "p": "c", "i": i})
        html = html.replace(f"{{{{CLICK:{i}}}}}", f"{base}/n/c/{tok}")
    return html, unsub


def apply_preheader(html: str, preheader: str) -> str:
    """Keep the hidden inbox preview line in sync with the CMS field."""
    preheader = (preheader or "").strip()
    if not html:
        return html
    if not preheader:
        return html
    safe = escape(preheader)

    def repl(m):
        return m.group(1) + "\n" + safe + "\n" + m.group(3)

    new, n = re.subn(
        r'(<div[^>]*display:\s*none[^>]*>)([\s\S]*?)(</div>)',
        repl,
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return new
    inject = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;'
        f'font-size:1px;color:#070F11;">\n{safe}\n</div>\n'
    )
    return re.sub(r"(<body[^>]*>)", r"\1\n" + inject, html, count=1, flags=re.I)


def strip_tags(html: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", " ", html or "", flags=re.I)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&rsquo;|&#39;", "'", text)
    text = re.sub(r"&ldquo;|&rdquo;|&quot;", '"', text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# S3 / local storage
# ---------------------------------------------------------------------------

def _use_local():
    return os.environ.get("NEWSLETTER_LOCAL") == "1"


def _local_root() -> Path:
    root = Path(os.environ.get("NEWSLETTER_LOCAL_DIR") or "/tmp/crosswalk-newsletter")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_bytes(key: str):
    if _use_local():
        p = _local_root() / key
        if not p.exists():
            return None
        return p.read_bytes()
    try:
        resp = _s3_client().get_object(Bucket=S3_BUCKET, Key=key)
        return resp["Body"].read()
    except Exception as e:
        code = ""
        if hasattr(e, "response"):
            code = ((e.response or {}).get("Error") or {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def _put_bytes(key: str, body: bytes, content_type: str):
    if _use_local():
        p = _local_root() / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        return
    _s3_client().put_object(
        Bucket=S3_BUCKET, Key=key, Body=body, ContentType=content_type
    )


def _get_text(key: str):
    raw = _get_bytes(key)
    if raw is None:
        return None
    return raw.decode("utf-8")


def _put_text(key: str, text: str, content_type="text/plain; charset=utf-8"):
    _put_bytes(key, (text or "").encode("utf-8"), content_type)


def _get_json(key: str):
    raw = _get_text(key)
    if raw is None or not raw.strip():
        return None
    return json.loads(raw)


def _put_json(key: str, obj):
    _put_text(key, json.dumps(obj, indent=2), "application/json")


def _append_event(campaign_id: str, event: dict):
    line = json.dumps(event, separators=(",", ":")) + "\n"
    key = EVENTS_KEY.format(cid=campaign_id)
    existing = _get_bytes(key) or b""
    _put_bytes(key, existing + line.encode("utf-8"), "application/x-ndjson")


def empty_state():
    return {
        "version": 1,
        "updated_at": _utcnow(),
        "settings": {
            "from_name": DEFAULT_FROM_NAME,
            "from_email": FROM_EMAIL,
            "reply_to": DEFAULT_REPLY_TO,
            "company_address": DEFAULT_COMPANY_ADDRESS,
            "public_base_url": "",
        },
        "lists": [
            {
                "id": "dashboard-users",
                "name": "Dashboard users",
                "kind": "synced",
                "created_at": _utcnow(),
            },
            {
                "id": "the-read",
                "name": "The Read",
                "kind": "manual",
                "created_at": _utcnow(),
            },
        ],
        "subscribers": [],
        "segments": [],
        "campaigns": [],
    }


def _cas_update_state(mutate_fn):
    """Apply mutate_fn(state) -> state. Returns the written state."""
    if _use_local():
        with _state_lock:
            state = _get_json(STATE_KEY) or empty_state()
            nxt = mutate_fn(state)
            if nxt is None:
                return state
            nxt["updated_at"] = _utcnow()
            _put_json(STATE_KEY, nxt)
            return nxt
    try:
        from s3_json_state import update_json
    except ImportError:
        from migration.s3_json_state import update_json  # type: ignore

    holder = {}

    def _mutate(obj):
        state = obj if isinstance(obj, dict) and obj.get("version") else empty_state()
        nxt = mutate_fn(state)
        if nxt is None:
            return None
        nxt["updated_at"] = _utcnow()
        holder["state"] = nxt
        return nxt

    update_json(S3_BUCKET, STATE_KEY, _mutate, default=empty_state())
    return holder.get("state") or load_state_raw()


def load_state():
    with _state_lock:
        state = _get_json(STATE_KEY)
    if not state:
        state = empty_state()
        _put_json(STATE_KEY, state)
    ensure_seeded(state)
    raw = load_state_raw()
    return _persist_normalized(raw)


def load_state_raw():
    return _get_json(STATE_KEY) or empty_state()


def _campaign(state, cid):
    for c in state.get("campaigns") or []:
        if c.get("id") == cid:
            return c
    return None


def _list(state, lid):
    for row in state.get("lists") or []:
        if row.get("id") == lid:
            return row
    return None


def _segment(state, sid):
    for row in state.get("segments") or []:
        if row.get("id") == sid:
            return row
    return None


def _empty_download():
    return {
        "enabled": False,
        "title": "",
        "filename": "",
        "original_name": "",
        "paid": False,
        "price_usd": 0,
        "list_id": "the-read",
    }


def _default_seed_download():
    d = _empty_download()
    d.update({
        "enabled": True,
        "title": "Creatorverse deck",
        "list_id": "the-read",
    })
    return d


def _normalize_tags(raw):
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[,;|]", str(raw or ""))
    out = []
    seen = set()
    for part in parts:
        tag = re.sub(r"\s+", " ", str(part or "").strip())
        key = tag.lower()
        if not tag or key in seen:
            continue
        seen.add(key)
        out.append(tag[:40])
    return out[:24]


def _legacy_jenna_reply(value):
    return (value or "").strip().lower() == "jenna@crosswalknyc.com"


def normalize_state(state):
    """Repair older state in place: reply-to, tags, segments, download."""
    if not isinstance(state, dict):
        return empty_state()
    changed = False
    settings = state.setdefault("settings", {})
    if _legacy_jenna_reply(settings.get("reply_to")) or not settings.get("reply_to"):
        settings["reply_to"] = DEFAULT_REPLY_TO
        changed = True
    addr = (settings.get("company_address") or "").strip()
    if not addr or addr in ("Crosswalk, New York, NY", "New York, NY"):
        settings["company_address"] = DEFAULT_COMPANY_ADDRESS
        changed = True
    settings["from_email"] = FROM_EMAIL
    state.setdefault("segments", [])
    state.setdefault("lists", [])
    state.setdefault("subscribers", [])
    state.setdefault("campaigns", [])
    for sub in state["subscribers"]:
        if "tags" not in sub:
            sub["tags"] = []
            changed = True
    for camp in state["campaigns"]:
        if _legacy_jenna_reply(camp.get("reply_to")) or not camp.get("reply_to"):
            camp["reply_to"] = DEFAULT_REPLY_TO
            changed = True
        dl = camp.get("download")
        if not isinstance(dl, dict):
            camp["download"] = (
                _default_seed_download() if camp.get("id") == SEED_CAMPAIGN_ID
                else _empty_download()
            )
            changed = True
        elif camp.get("id") == SEED_CAMPAIGN_ID and (dl.get("title") or "") == "Creatorverse deck and workbook":
            dl["title"] = "Creatorverse deck"
            changed = True
    return state, changed


def _persist_normalized(state):
    state, changed = normalize_state(state)
    if not changed:
        return state

    def mutate(st):
        nxt, did = normalize_state(st)
        return nxt if did else None

    return _cas_update_state(mutate)


def get_campaign_html(cid: str) -> str:
    return _get_text(CAMPAIGN_HTML_KEY.format(cid=cid)) or ""


def put_campaign_html(cid: str, html: str):
    _put_text(CAMPAIGN_HTML_KEY.format(cid=cid), html, "text/html; charset=utf-8")


def get_send_snapshot(cid: str):
    return _get_json(SEND_KEY.format(cid=cid)) or {}


def put_send_snapshot(cid: str, snap: dict):
    _put_json(SEND_KEY.format(cid=cid), snap)


ALLOWED_ATTACH_EXT = {
    "pdf", "ppt", "pptx", "xls", "xlsx", "csv", "zip", "doc", "docx",
}
MAX_ATTACH_BYTES = 25 * 1024 * 1024


def get_download_store(cid: str):
    return _get_json(DOWNLOAD_KEY.format(cid=cid)) or {
        "campaign_id": cid,
        "leads": {},
    }


def put_download_store(cid: str, store: dict):
    store["campaign_id"] = cid
    _put_json(DOWNLOAD_KEY.format(cid=cid), store)


def download_stats_from_store(store):
    leads = (store or {}).get("leads") or {}
    entered = 0
    paid = 0
    downloads = 0
    unique_downloads = 0
    revenue_cents = 0
    for row in leads.values():
        entered += 1
        if row.get("paid"):
            paid += 1
        count = int(row.get("download_count") or 0)
        downloads += count
        if count > 0:
            unique_downloads += 1
        revenue_cents += int(row.get("amount_cents") or 0)
    return {
        "leads": entered,
        "paid": paid,
        "downloads": downloads,
        "unique_downloads": unique_downloads,
        "revenue_usd": round(revenue_cents / 100.0, 2),
    }


def _campaign_download(camp):
    dl = dict(_empty_download())
    if isinstance(camp.get("download"), dict):
        dl.update(camp["download"] or {})
    try:
        dl["price_usd"] = float(dl.get("price_usd") or 0)
    except (TypeError, ValueError):
        dl["price_usd"] = 0
    dl["enabled"] = bool(dl.get("enabled"))
    dl["paid"] = bool(dl.get("paid"))
    dl["has_file"] = bool(dl.get("filename"))
    return dl


def _public_download_url(cid, settings=None):
    return f"{_public_base(settings)}/n/d/{cid}"


def _upsert_subscriber(st, email, name="", company="", list_ids=None, tags=None, source="manual"):
    email = _valid_email(email)
    if not email:
        return False
    list_ids = [x for x in (list_ids or ["the-read"]) if x]
    tags = _normalize_tags(tags or [])
    existing = {_valid_email(s.get("email")): s for s in (st.get("subscribers") or [])}
    cur = existing.get(email)
    if not cur:
        st.setdefault("subscribers", []).append({
            "email": email,
            "name": name or "",
            "company": company or "",
            "list_ids": list(dict.fromkeys(list_ids)),
            "tags": tags,
            "status": "subscribed",
            "source": source,
            "added_at": _utcnow(),
        })
        return True
    lists = list(cur.get("list_ids") or [])
    for lid in list_ids:
        if lid not in lists:
            lists.append(lid)
    cur["list_ids"] = lists
    have_tags = _normalize_tags(cur.get("tags") or [])
    for tag in tags:
        if tag.lower() not in {t.lower() for t in have_tags}:
            have_tags.append(tag)
    cur["tags"] = have_tags
    if name and not cur.get("name"):
        cur["name"] = name
    if company and not cur.get("company"):
        cur["company"] = company
    if cur.get("status") == "unsubscribed":
        return False
    return True


def record_download_lead(cid, email, name="", company="", paid=False, amount_cents=0, session_id=""):
    email = _valid_email(email)
    if not email:
        return None
    store = get_download_store(cid)
    leads = store.setdefault("leads", {})
    row = leads.get(email) or {
        "email": email,
        "name": name or "",
        "company": company or "",
        "entered_at": _utcnow(),
        "paid": False,
        "paid_at": None,
        "amount_cents": 0,
        "stripe_session_id": "",
        "download_count": 0,
        "last_download_at": None,
    }
    if name:
        row["name"] = name
    if company:
        row["company"] = company
    if paid:
        row["paid"] = True
        row["paid_at"] = row.get("paid_at") or _utcnow()
        if amount_cents:
            row["amount_cents"] = int(amount_cents)
        if session_id:
            row["stripe_session_id"] = session_id
    leads[email] = row
    put_download_store(cid, store)
    return row


def mark_file_downloaded(cid, email):
    email = _valid_email(email)
    if not email:
        return
    store = get_download_store(cid)
    leads = store.setdefault("leads", {})
    row = leads.get(email) or {
        "email": email,
        "entered_at": _utcnow(),
        "paid": False,
        "download_count": 0,
    }
    row["download_count"] = int(row.get("download_count") or 0) + 1
    row["last_download_at"] = _utcnow()
    leads[email] = row
    put_download_store(cid, store)
    try:
        _append_event(cid, {
            "at": row["last_download_at"],
            "email": email,
            "kind": "download",
            "url": "",
        })
    except Exception:
        pass


def fulfill_paid_download(obj):
    """Called from the Stripe webhook after a paid newsletter download."""
    obj = obj or {}
    md = obj.get("metadata") or {}
    cid = (md.get("campaign_id") or md.get("cid") or "").strip()
    email = _valid_email(md.get("email") or obj.get("customer_email") or "")
    if not cid or not email:
        print("[newsletter] paid download missing campaign or email")
        return
    amount_cents = int(obj.get("amount_total") or 0)
    session_id = obj.get("id") or ""
    record_download_lead(
        cid, email, paid=True, amount_cents=amount_cents, session_id=session_id,
    )
    state = load_state_raw()
    camp = _campaign(state, cid) or {}
    dl = _campaign_download(camp)
    list_id = dl.get("list_id") or camp.get("list_id") or "the-read"

    def mutate(st):
        _upsert_subscriber(
            st, email,
            list_ids=[list_id],
            tags=["download", "paid"],
            source="download",
        )
        return st

    _cas_update_state(mutate)
    print(f"[newsletter] paid download unlocked {cid} -> {email}")


# ---------------------------------------------------------------------------
# Seed: first issue of The Read (Creatorverse)
# ---------------------------------------------------------------------------

def ensure_seeded(state=None):
    state = state or load_state_raw()
    if _campaign(state, SEED_CAMPAIGN_ID):
        _ensure_seed_assets()
        camp = _campaign(state, SEED_CAMPAIGN_ID)
        if camp and camp.get("status") == "draft" and SEED_HTML.exists():
            fresh = SEED_HTML.read_text(encoding="utf-8")
            current = get_campaign_html(SEED_CAMPAIGN_ID) or ""
            if current != fresh:
                put_campaign_html(SEED_CAMPAIGN_ID, fresh)
                _ensure_seed_assets(force=True)
        return state
    if not SEED_HTML.exists():
        return state

    html = SEED_HTML.read_text(encoding="utf-8")
    put_campaign_html(SEED_CAMPAIGN_ID, html)
    _ensure_seed_assets()

    def mutate(st):
        if _campaign(st, SEED_CAMPAIGN_ID):
            return None
        st.setdefault("campaigns", []).insert(0, {
            "id": SEED_CAMPAIGN_ID,
            "name": "The Read / Creatorverse",
            "subject": "22.4M watched Tubi's Creatorverse in year one",
            "preheader": "Their YouTube time never moved.",
            "from_name": "The Read",
            "from_email": FROM_EMAIL,
            "reply_to": DEFAULT_REPLY_TO,
            "list_id": "the-read",
            "status": "draft",
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "sent_at": None,
            "scheduled_at": None,
            "seed": True,
            "stats": _empty_stats(),
            "download": _default_seed_download(),
        })
        if not _list(st, "the-read"):
            st.setdefault("lists", []).append({
                "id": "the-read",
                "name": "The Read",
                "kind": "manual",
                "created_at": _utcnow(),
            })
        return st

    return _cas_update_state(mutate)


def _ensure_seed_assets(force=False):
    global _seed_assets_done
    if _seed_assets_done and not force:
        return
    if not SEED_ASSETS.exists():
        return
    sentinel = ASSET_KEY.format(cid=SEED_CAMPAIGN_ID, name="img01.jpg")
    if force or _get_bytes(sentinel) is None:
        for path in sorted(SEED_ASSETS.iterdir()):
            if not path.is_file():
                continue
            _put_bytes(
                ASSET_KEY.format(cid=SEED_CAMPAIGN_ID, name=path.name),
                path.read_bytes(),
                _content_type_for(path.name),
            )
    _seed_assets_done = True


def _empty_stats():
    return {
        "recipients": 0,
        "sent": 0,
        "failed": 0,
        "opens": 0,
        "unique_opens": 0,
        "clicks": 0,
        "unique_clicks": 0,
        "unsubs": 0,
        "bounces": 0,
    }


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------

def sync_dashboard_users(state=None):
    try:
        from app import load_users
        users_doc = load_users() or {}
        users = users_doc.get("users") or {}
    except Exception:
        users = {}

    incoming = []
    for username, user in users.items():
        if not isinstance(user, dict):
            continue
        email = _valid_email(user.get("email"))
        if not email:
            continue
        status = (user.get("status") or "").strip().lower()
        if status in ("disabled", "deleted", "inactive"):
            continue
        first = (user.get("first_name") or "").strip()
        last = (user.get("last_name") or "").strip()
        name = (f"{first} {last}").strip() or username
        incoming.append({
            "email": email,
            "name": name,
            "company": (user.get("company") or "").strip(),
            "source": "dashboard",
            "username": username,
        })

    by_email = {row["email"]: row for row in incoming}

    def mutate(st):
        existing = {(_valid_email(s.get("email"))): s for s in (st.get("subscribers") or [])}
        changed = False
        for email, row in by_email.items():
            cur = existing.get(email)
            if not cur:
                st.setdefault("subscribers", []).append({
                    "email": email,
                    "name": row["name"],
                    "company": row["company"],
                    "list_ids": ["dashboard-users"],
                    "status": "subscribed",
                    "source": "dashboard",
                    "username": row["username"],
                    "added_at": _utcnow(),
                })
                changed = True
                continue
            if cur.get("status") == "unsubscribed":
                continue
            lists = list(cur.get("list_ids") or [])
            if "dashboard-users" not in lists:
                lists.append("dashboard-users")
                cur["list_ids"] = lists
                changed = True
            for field in ("name", "company", "username"):
                if row.get(field) and cur.get(field) != row[field]:
                    cur[field] = row[field]
                    changed = True
            cur["source"] = cur.get("source") or "dashboard"
        return st if changed else None

    return _cas_update_state(mutate)


def subscriber_matches_segment(sub, segment):
    """True when a subscriber matches a saved segment's rules."""
    if not segment:
        return False
    rules = segment.get("rules") or {}
    status = (sub.get("status") or "subscribed")
    want_status = (rules.get("status") or "subscribed").strip().lower()
    if want_status not in ("", "any") and status != want_status:
        return False
    list_ids = [x for x in (rules.get("list_ids") or []) if x]
    if list_ids:
        have = set(sub.get("list_ids") or [])
        if not have.intersection(list_ids):
            return False
    tags_any = [t.lower() for t in _normalize_tags(rules.get("tags") or rules.get("tags_any") or [])]
    if tags_any:
        have_tags = {t.lower() for t in (sub.get("tags") or [])}
        if not have_tags.intersection(tags_any):
            return False
    company = (rules.get("company_contains") or "").strip().lower()
    if company and company not in (sub.get("company") or "").lower():
        return False
    source = (rules.get("source") or "").strip().lower()
    if source and (sub.get("source") or "").lower() != source:
        return False
    return True


def resolve_recipients(state, list_id=None, emails=None, segment_id=None):
    wanted = None
    if emails:
        wanted = {_valid_email(e) for e in emails}
        wanted.discard("")
    segment = _segment(state, segment_id) if segment_id else None
    out = []
    seen = set()
    for sub in state.get("subscribers") or []:
        email = _valid_email(sub.get("email"))
        if not email or email in seen:
            continue
        if (sub.get("status") or "subscribed") != "subscribed":
            continue
        if wanted is not None and email not in wanted:
            continue
        if segment:
            if not subscriber_matches_segment(sub, segment):
                continue
        elif list_id and list_id not in (sub.get("list_ids") or []):
            continue
        seen.add(email)
        out.append({
            "email": email,
            "name": (sub.get("name") or "").strip(),
            "company": (sub.get("company") or "").strip(),
            "tags": list(sub.get("tags") or []),
        })
    return out


_CSV_EMAIL_KEYS = {"email", "e-mail", "e_mail", "mail", "email address", "emailaddress"}
_CSV_NAME_KEYS = {"name", "full name", "fullname", "contact"}
_CSV_FIRST_KEYS = {"first", "first name", "firstname", "given"}
_CSV_LAST_KEYS = {"last", "last name", "lastname", "surname"}
_CSV_COMPANY_KEYS = {"company", "organization", "org", "firm", "account"}
_CSV_LIST_KEYS = {"list", "list_id", "list id", "audience"}
_CSV_TAG_KEYS = {"tags", "tag", "labels", "segments"}


def _header_key(cell):
    return re.sub(r"[^a-z0-9]+", " ", (cell or "").strip().lower()).strip()


def parse_import_csv(text):
    """Turn a pasted or uploaded CSV into subscriber dicts.

    Header-tolerant. A file of bare emails (one per line) is fine.
    """
    raw = (text or "").lstrip("\ufeff").strip()
    if not raw:
        return []
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except Exception:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []

    def pick(row, mapping, keys):
        for key in keys:
            idx = mapping.get(key)
            if idx is not None and idx < len(row):
                return (row[idx] or "").strip()
        return ""

    first = [_header_key(c) for c in rows[0]]
    has_header = any(h in _CSV_EMAIL_KEYS or h in _CSV_NAME_KEYS for h in first)
    out = []
    seen = set()
    if has_header:
        mapping = {}
        for i, h in enumerate(first):
            mapping.setdefault(h, i)
        for row in rows[1:]:
            email = _valid_email(pick(row, mapping, _CSV_EMAIL_KEYS))
            if not email and row:
                email = _valid_email(row[0])
            if not email or email in seen:
                continue
            name = pick(row, mapping, _CSV_NAME_KEYS)
            if not name:
                name = " ".join(
                    x for x in (
                        pick(row, mapping, _CSV_FIRST_KEYS),
                        pick(row, mapping, _CSV_LAST_KEYS),
                    ) if x
                ).strip()
            out.append({
                "email": email,
                "name": name,
                "company": pick(row, mapping, _CSV_COMPANY_KEYS),
                "tags": _normalize_tags(pick(row, mapping, _CSV_TAG_KEYS)),
                "list_hint": pick(row, mapping, _CSV_LIST_KEYS),
            })
            seen.add(email)
        return out

    for row in rows:
        for cell in row:
            email = _valid_email(cell)
            if email and email not in seen:
                out.append({
                    "email": email,
                    "name": "",
                    "company": "",
                    "tags": [],
                    "list_hint": "",
                })
                seen.add(email)
                break
    return out


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _from_header(campaign, settings):
    name = (campaign.get("from_name") or settings.get("from_name") or DEFAULT_FROM_NAME).strip()
    addr = (campaign.get("from_email") or settings.get("from_email") or FROM_EMAIL).strip()
    return f"{name} <{addr}>", addr


def send_one_email(to_email, subject, html, text, from_header, from_addr,
                   reply_to, unsub_url):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["List-Unsubscribe"] = f"<{unsub_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    _ses_client().send_raw_email(
        Source=from_addr,
        Destinations=[to_email],
        RawMessage={"Data": msg.as_string()},
    )


def _claim_send(campaign_id, list_id=None, emails=None, scheduled=False, segment_id=None):
    """Mark campaign sending and write the recipient snapshot. None if blocked."""
    state = load_state()
    camp = _campaign(state, campaign_id)
    if not camp:
        return None, "campaign not found"
    if camp.get("status") not in ("draft", "scheduled", "sending"):
        return None, f"campaign is {camp.get('status')}"
    segment_id = segment_id or camp.get("segment_id") or None
    recipients = resolve_recipients(
        state,
        None if segment_id else (list_id or camp.get("list_id")),
        emails,
        segment_id=segment_id,
    )
    if not recipients:
        return None, "no subscribed recipients on that audience"

    snap = get_send_snapshot(campaign_id)
    existing = (snap.get("recipients") or {}) if camp.get("status") == "sending" else {}
    recips = {}
    for row in recipients:
        email = row["email"]
        prev = existing.get(email) or {}
        recips[email] = {
            "name": row.get("name") or prev.get("name") or "",
            "status": prev.get("status") if prev.get("status") in ("sent", "failed") else "queued",
            "sent_at": prev.get("sent_at"),
            "error": prev.get("error"),
            "opened_at": prev.get("opened_at"),
            "open_count": prev.get("open_count") or 0,
            "clicks": prev.get("clicks") or [],
            "unsubscribed_at": prev.get("unsubscribed_at"),
        }

    html = get_campaign_html(campaign_id)
    if not html.strip():
        return None, "campaign has no HTML"

    def mutate(st):
        c = _campaign(st, campaign_id)
        if not c:
            return None
        if c.get("status") not in ("draft", "scheduled", "sending"):
            return None
        if scheduled and c.get("status") == "sending":
            return None
        c["status"] = "sending"
        c["list_id"] = list_id or c.get("list_id")
        if segment_id:
            c["segment_id"] = segment_id
        c["updated_at"] = _utcnow()
        stats = c.setdefault("stats", _empty_stats())
        stats["recipients"] = len(recips)
        return st

    nxt = _cas_update_state(mutate)
    claimed = _campaign(nxt, campaign_id)
    if not claimed or claimed.get("status") != "sending":
        return None, "could not claim send"
    put_send_snapshot(campaign_id, {
        "campaign_id": campaign_id,
        "started_at": snap.get("started_at") or _utcnow(),
        "finished_at": None,
        "list_id": list_id or camp.get("list_id"),
        "segment_id": segment_id or camp.get("segment_id") or "",
        "recipients": recips,
    })
    return recips, None


def _run_send(campaign_id):
    try:
        state = load_state_raw()
        camp = _campaign(state, campaign_id)
        if not camp:
            return
        settings = state.get("settings") or {}
        html = get_campaign_html(campaign_id)
        base = _public_base(settings)
        asset_base = f"{base}/n/asset/{campaign_id}"
        download_url = f"{base}/n/d/{campaign_id}" if (camp.get("download") or {}).get("enabled") else ""
        template, links = build_send_template(html, asset_base, download_url)
        text_fallback = strip_tags(html)[:4000]
        from_header, from_addr = _from_header(camp, settings)
        reply_to = (camp.get("reply_to") or settings.get("reply_to") or DEFAULT_REPLY_TO).strip()
        subject = (camp.get("subject") or camp.get("name") or "The Read").strip()

        snap = get_send_snapshot(campaign_id)
        snap["links"] = links
        put_send_snapshot(campaign_id, snap)
        recips = snap.get("recipients") or {}
        sent = failed = 0
        for email, row in recips.items():
            if row.get("status") == "sent":
                sent += 1
                continue
            try:
                personalized, unsub = personalize_html(template, campaign_id, email, links, base)
                send_one_email(
                    email, subject, personalized, text_fallback,
                    from_header, from_addr, reply_to, unsub,
                )
                row["status"] = "sent"
                row["sent_at"] = _utcnow()
                row["error"] = None
                sent += 1
            except Exception as e:
                row["status"] = "failed"
                row["error"] = str(e)[:400]
                failed += 1
                print(f"newsletter send failed {campaign_id} -> {email}: {e}")
            snap["recipients"][email] = row
            put_send_snapshot(campaign_id, snap)
            time.sleep(0.08)

        snap["finished_at"] = _utcnow()
        snap["links"] = links
        put_send_snapshot(campaign_id, snap)
        stats = _stats_from_snapshot(snap)

        def mutate(st):
            c = _campaign(st, campaign_id)
            if not c:
                return None
            c["status"] = "sent"
            c["sent_at"] = snap.get("finished_at")
            c["scheduled_at"] = None
            c["stats"] = stats
            c["updated_at"] = _utcnow()
            return st

        _cas_update_state(mutate)
        print(f"newsletter campaign {campaign_id} finished sent={sent} failed={failed}")
    except Exception:
        traceback.print_exc()


def start_send_async(campaign_id):
    t = threading.Thread(target=_run_send, args=(campaign_id,), daemon=True)
    t.start()
    return t


def send_test(campaign_id, to_email):
    email = _valid_email(to_email)
    if not email:
        return False, "valid email required"
    state = load_state()
    camp = _campaign(state, campaign_id)
    if not camp:
        return False, "campaign not found"
    html = get_campaign_html(campaign_id)
    if not html.strip():
        return False, "campaign has no HTML"
    settings = state.get("settings") or {}
    base = _public_base(settings)
    asset_base = f"{base}/n/asset/{campaign_id}"
    download_url = f"{base}/n/d/{campaign_id}" if (camp.get("download") or {}).get("enabled") else ""
    template, links = build_send_template(html, asset_base, download_url)
    personalized, unsub = personalize_html(template, campaign_id, email, links, base)
    from_header, from_addr = _from_header(camp, settings)
    reply_to = (camp.get("reply_to") or settings.get("reply_to") or DEFAULT_REPLY_TO).strip()
    subject = f"[Test] {(camp.get('subject') or camp.get('name') or 'The Read').strip()}"
    send_one_email(
        email, subject, personalized, strip_tags(html)[:4000],
        from_header, from_addr, reply_to, unsub,
    )
    return True, "test sent"


def _stats_from_snapshot(snap):
    stats = _empty_stats()
    recips = snap.get("recipients") or {}
    stats["recipients"] = len(recips)
    clickers = set()
    for email, row in recips.items():
        status = row.get("status")
        if status == "sent":
            stats["sent"] += 1
        elif status == "failed":
            stats["failed"] += 1
        if status == "bounced":
            stats["bounces"] += 1
        stats["opens"] += int(row.get("open_count") or 0)
        if row.get("opened_at"):
            stats["unique_opens"] += 1
        clicks = row.get("clicks") or []
        stats["clicks"] += sum(int(c.get("count") or 1) for c in clicks)
        if clicks:
            clickers.add(email)
        if row.get("unsubscribed_at"):
            stats["unsubs"] += 1
    stats["unique_clicks"] = len(clickers)
    return stats


def _record_engagement(campaign_id, email, kind, url=None):
    email = _valid_email(email)
    if not email:
        return
    snap = get_send_snapshot(campaign_id)
    recips = snap.setdefault("recipients", {})
    row = recips.setdefault(email, {
        "name": "", "status": "sent", "open_count": 0, "clicks": [],
    })
    now = _utcnow()
    if kind == "open":
        row["open_count"] = int(row.get("open_count") or 0) + 1
        if not row.get("opened_at"):
            row["opened_at"] = now
    elif kind == "click" and url:
        found = None
        for c in row.get("clicks") or []:
            if c.get("url") == url:
                found = c
                break
        if found:
            found["count"] = int(found.get("count") or 1) + 1
            found["last_at"] = now
        else:
            row.setdefault("clicks", []).append({
                "url": url, "count": 1, "at": now, "last_at": now,
            })
        if not row.get("opened_at"):
            row["opened_at"] = now
            row["open_count"] = max(int(row.get("open_count") or 0), 1)
    elif kind == "unsub":
        row["unsubscribed_at"] = now
    recips[email] = row
    snap["recipients"] = recips
    put_send_snapshot(campaign_id, snap)
    stats = _stats_from_snapshot(snap)

    def mutate(st):
        c = _campaign(st, campaign_id)
        if c:
            c["stats"] = stats
            c["updated_at"] = now
        if kind == "unsub":
            for sub in st.get("subscribers") or []:
                if _valid_email(sub.get("email")) == email:
                    sub["status"] = "unsubscribed"
                    sub["unsubscribed_at"] = now
        return st

    _cas_update_state(mutate)
    try:
        _append_event(campaign_id, {
            "at": now, "email": email, "kind": kind, "url": url or "",
        })
    except Exception:
        pass


def _kick_due_sends():
    state = load_state_raw()
    now = datetime.now(timezone.utc)
    for camp in state.get("campaigns") or []:
        cid = camp.get("id")
        status = camp.get("status")
        if status == "sending":
            snap = get_send_snapshot(cid)
            queued = [
                e for e, r in (snap.get("recipients") or {}).items()
                if (r.get("status") or "") == "queued"
            ]
            if queued:
                start_send_async(cid)
            continue
        if status != "scheduled":
            continue
        when = camp.get("scheduled_at") or ""
        try:
            due = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except Exception:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now:
            recips, err = _claim_send(
                cid, camp.get("list_id"), scheduled=True,
                segment_id=camp.get("segment_id") or None,
            )
            if recips:
                start_send_async(cid)
            elif err:
                print(f"newsletter scheduled send skipped {cid}: {err}")


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        time.sleep(8)
        while True:
            try:
                _kick_due_sends()
            except Exception:
                traceback.print_exc()
            time.sleep(30)

    threading.Thread(target=loop, name="newsletter-scheduler", daemon=True).start()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _admin_guard():
    if "username" not in session:
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": "Session expired. Please log in again."}), 401
        return redirect("/login")
    try:
        from app import get_current_user
        user = get_current_user()
    except Exception:
        user = None
    role = (user or {}).get("role", "")
    if not user or role not in ("admin", "super_admin"):
        return jsonify({"success": False, "error": "Admin access required"}), 403
    return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        guard = _admin_guard()
        if guard is not None:
            return guard
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _public_campaign(c, subscriber_counts=None, settings=None, include_downloads=True):
    stats = c.get("stats") or _empty_stats()
    sent = int(stats.get("sent") or 0)
    opens = int(stats.get("unique_opens") or 0)
    clicks = int(stats.get("unique_clicks") or 0)
    dl = _campaign_download(c)
    cid = c.get("id")
    dl_stats = download_stats_from_store(get_download_store(cid)) if include_downloads and cid else {
        "leads": 0, "paid": 0, "downloads": 0, "unique_downloads": 0, "revenue_usd": 0,
    }
    return {
        "id": cid,
        "name": c.get("name"),
        "subject": c.get("subject"),
        "preheader": c.get("preheader"),
        "from_name": c.get("from_name"),
        "from_email": c.get("from_email") or FROM_EMAIL,
        "reply_to": c.get("reply_to") or DEFAULT_REPLY_TO,
        "list_id": c.get("list_id"),
        "segment_id": c.get("segment_id") or "",
        "status": c.get("status"),
        "created_at": c.get("created_at"),
        "updated_at": c.get("updated_at"),
        "sent_at": c.get("sent_at"),
        "scheduled_at": c.get("scheduled_at"),
        "seed": bool(c.get("seed")),
        "stats": stats,
        "open_rate": round((opens / sent) * 100, 1) if sent else 0,
        "click_rate": round((clicks / sent) * 100, 1) if sent else 0,
        "has_html": True,
        "download": dl,
        "download_url": _public_download_url(cid, settings) if dl.get("enabled") else "",
        "download_stats": dl_stats,
    }


def _overview_payload():
    ensure_seeded()
    sync_dashboard_users()
    state = load_state_raw()
    subs = state.get("subscribers") or []
    active = [s for s in subs if (s.get("status") or "subscribed") == "subscribed"]
    list_counts = {}
    for s in active:
        for lid in s.get("list_ids") or []:
            list_counts[lid] = list_counts.get(lid, 0) + 1
    settings = state.get("settings") or {}
    campaigns = [_public_campaign(c, settings=settings) for c in state.get("campaigns") or []]
    sent_camps = [c for c in campaigns if c.get("status") == "sent"]
    tot_sent = sum(int((c.get("stats") or {}).get("sent") or 0) for c in sent_camps)
    tot_opens = sum(int((c.get("stats") or {}).get("unique_opens") or 0) for c in sent_camps)
    tot_clicks = sum(int((c.get("stats") or {}).get("unique_clicks") or 0) for c in sent_camps)
    tot_downloads = sum(int((c.get("download_stats") or {}).get("unique_downloads") or 0) for c in campaigns)
    tot_dl_leads = sum(int((c.get("download_stats") or {}).get("leads") or 0) for c in campaigns)
    tot_revenue = sum(float((c.get("download_stats") or {}).get("revenue_usd") or 0) for c in campaigns)
    stripe_on = False
    try:
        import billing
        stripe_on = bool(billing.is_enabled())
    except Exception:
        stripe_on = False
    segments = []
    for seg in state.get("segments") or []:
        n = len(resolve_recipients(state, segment_id=seg.get("id")))
        segments.append({**seg, "subscriber_count": n})
    return {
        "success": True,
        "settings": settings,
        "from_identity": f"{settings.get('from_name') or DEFAULT_FROM_NAME} <{FROM_EMAIL}>",
        "stripe_enabled": stripe_on,
        "lists": [
            {**row, "subscriber_count": list_counts.get(row.get("id"), 0)}
            for row in (state.get("lists") or [])
        ],
        "segments": segments,
        "subscribers": [
            {
                "email": s.get("email"),
                "name": s.get("name") or "",
                "company": s.get("company") or "",
                "list_ids": s.get("list_ids") or [],
                "tags": s.get("tags") or [],
                "status": s.get("status") or "subscribed",
                "source": s.get("source") or "manual",
                "added_at": s.get("added_at"),
                "unsubscribed_at": s.get("unsubscribed_at"),
            }
            for s in subs
        ],
        "campaigns": campaigns,
        "stats": {
            "subscribers": len(active),
            "unsubscribed": sum(1 for s in subs if s.get("status") == "unsubscribed"),
            "campaigns": len(campaigns),
            "sent_campaigns": len(sent_camps),
            "emails_sent": tot_sent,
            "avg_open_rate": round((tot_opens / tot_sent) * 100, 1) if tot_sent else 0,
            "avg_click_rate": round((tot_clicks / tot_sent) * 100, 1) if tot_sent else 0,
            "downloads": tot_downloads,
            "download_leads": tot_dl_leads,
            "download_revenue": round(tot_revenue, 2),
        },
    }


def _preview_html(campaign_id):
    state = load_state_raw()
    settings = state.get("settings") or {}
    html = get_campaign_html(campaign_id)
    base = _public_base(settings)
    asset_base = f"{base}/n/asset/{campaign_id}"
    html = (html or "").replace("{{ASSET_BASE}}", asset_base)
    camp = _campaign(state, campaign_id) or {}
    if (camp.get("download") or {}).get("enabled"):
        html = html.replace("{{DOWNLOAD}}", f"{base}/n/d/{campaign_id}")
    html = UNSUB_PLACEHOLDER_RE.sub("#", html)
    return html


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

@newsletter_bp.route("/api/admin/newsletter")
@admin_required
def api_overview():
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/settings", methods=["POST"])
@admin_required
def api_settings():
    body = request.get_json(silent=True) or {}

    def mutate(st):
        s = st.setdefault("settings", {})
        if "from_name" in body:
            s["from_name"] = (body.get("from_name") or DEFAULT_FROM_NAME).strip()
        if "reply_to" in body:
            s["reply_to"] = _valid_email(body.get("reply_to")) or DEFAULT_REPLY_TO
        if "company_address" in body:
            s["company_address"] = (body.get("company_address") or "").strip()
        if "public_base_url" in body:
            s["public_base_url"] = (body.get("public_base_url") or "").strip().rstrip("/")
        s["from_email"] = FROM_EMAIL
        return st

    _cas_update_state(mutate)
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/campaigns", methods=["POST"])
@admin_required
def api_create_campaign():
    body = request.get_json(silent=True) or {}
    cid = _new_id("nl")
    html = body.get("html") or ""
    if html:
        html, _ = _store_html_assets(cid, html)
        put_campaign_html(cid, html)
    else:
        put_campaign_html(cid, "")

    def mutate(st):
        st.setdefault("campaigns", []).insert(0, {
            "id": cid,
            "name": (body.get("name") or "Untitled newsletter").strip(),
            "subject": (body.get("subject") or "").strip(),
            "preheader": (body.get("preheader") or "").strip(),
            "from_name": (body.get("from_name") or (st.get("settings") or {}).get("from_name") or DEFAULT_FROM_NAME).strip(),
            "from_email": FROM_EMAIL,
            "reply_to": _valid_email(body.get("reply_to")) or (st.get("settings") or {}).get("reply_to") or DEFAULT_REPLY_TO,
            "list_id": (body.get("list_id") or "the-read").strip(),
            "status": "draft",
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "sent_at": None,
            "scheduled_at": None,
            "stats": _empty_stats(),
            "segment_id": (body.get("segment_id") or "").strip(),
            "download": _download_from_body(body, _empty_download()),
        })
        return st

    _cas_update_state(mutate)
    return jsonify({"success": True, "id": cid, **_overview_payload()})


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>", methods=["GET"])
@admin_required
def api_get_campaign(cid):
    state = load_state()
    camp = _campaign(state, cid)
    if not camp:
        return jsonify({"success": False, "error": "campaign not found"}), 404
    return jsonify({
        "success": True,
        "campaign": _public_campaign(camp),
        "html": get_campaign_html(cid),
    })


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>", methods=["PUT"])
@admin_required
def api_update_campaign(cid):
    body = request.get_json(silent=True) or {}

    def mutate(st):
        c = _campaign(st, cid)
        if not c:
            return None
        for field in ("name", "subject", "preheader", "from_name", "list_id"):
            if field in body:
                c[field] = (body.get(field) or "").strip()
        if "segment_id" in body:
            c["segment_id"] = (body.get("segment_id") or "").strip()
        if "reply_to" in body:
            c["reply_to"] = _valid_email(body.get("reply_to")) or DEFAULT_REPLY_TO
        if "download" in body or any(k.startswith("download_") for k in body):
            c["download"] = _download_from_body(body, c.get("download") or _empty_download())
        if "scheduled_at" in body:
            raw = (body.get("scheduled_at") or "").strip()
            c["scheduled_at"] = raw or None
            if raw and c.get("status") in ("draft", "scheduled"):
                c["status"] = "scheduled"
            if not raw and c.get("status") == "scheduled":
                c["status"] = "draft"
        c["from_email"] = FROM_EMAIL
        c["updated_at"] = _utcnow()
        return st

    nxt = _cas_update_state(mutate)
    if not _campaign(nxt, cid):
        return jsonify({"success": False, "error": "campaign not found"}), 404
    html = get_campaign_html(cid)
    if "html" in body:
        html, _ = _store_html_assets(cid, body.get("html") or "")
    if "preheader" in body:
        html = apply_preheader(html, body.get("preheader") or "")
    if "html" in body or "preheader" in body:
        put_campaign_html(cid, html)
    return jsonify({"success": True, "campaign": _public_campaign(_campaign(nxt, cid))})


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>", methods=["DELETE"])
@admin_required
def api_delete_campaign(cid):
    def mutate(st):
        before = len(st.get("campaigns") or [])
        st["campaigns"] = [c for c in (st.get("campaigns") or []) if c.get("id") != cid]
        return st if len(st["campaigns"]) != before else None

    _cas_update_state(mutate)
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>/html", methods=["POST"])
@admin_required
def api_upload_html(cid):
    state = load_state()
    if not _campaign(state, cid):
        return jsonify({"success": False, "error": "campaign not found"}), 404
    html = ""
    if request.files.get("file"):
        html = request.files["file"].read().decode("utf-8", errors="replace")
    else:
        body = request.get_json(silent=True) or {}
        html = body.get("html") or ""
    html, n_assets = _store_html_assets(cid, html)
    put_campaign_html(cid, html)

    def mutate(st):
        c = _campaign(st, cid)
        if not c:
            return None
        c["updated_at"] = _utcnow()
        return st

    _cas_update_state(mutate)
    return jsonify({"success": True, "bytes": len(html.encode("utf-8")), "assets": n_assets})


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>/duplicate", methods=["POST"])
@admin_required
def api_duplicate(cid):
    state = load_state()
    src = _campaign(state, cid)
    if not src:
        return jsonify({"success": False, "error": "campaign not found"}), 404
    new_id = _new_id("nl")
    html = get_campaign_html(cid)
    put_campaign_html(new_id, html)
    # copy assets we can see locally / in s3 via seed folder is enough for seed;
    # uploaded assets stay on the original id, so rewrite ASSET_BASE at send time
    # per campaign. Copy seed-style files if present.
    _copy_assets(cid, new_id)

    def mutate(st):
        st.setdefault("campaigns", []).insert(0, {
            "id": new_id,
            "name": f"{src.get('name') or 'Untitled'} (copy)",
            "subject": src.get("subject") or "",
            "preheader": src.get("preheader") or "",
            "from_name": src.get("from_name") or DEFAULT_FROM_NAME,
            "from_email": FROM_EMAIL,
            "reply_to": src.get("reply_to") or DEFAULT_REPLY_TO,
            "list_id": src.get("list_id") or "the-read",
            "status": "draft",
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "sent_at": None,
            "scheduled_at": None,
            "stats": _empty_stats(),
            "segment_id": src.get("segment_id") or "",
            "download": dict(src.get("download") or _empty_download()),
        })
        return st

    _cas_update_state(mutate)
    _copy_attachment(cid, new_id)
    return jsonify({"success": True, "id": new_id, **_overview_payload()})


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>/test", methods=["POST"])
@admin_required
def api_test_send(cid):
    body = request.get_json(silent=True) or {}
    to_email = body.get("email") or ""
    if not to_email:
        try:
            from app import get_current_user
            user = get_current_user() or {}
            to_email = user.get("email") or ""
        except Exception:
            to_email = ""
    try:
        ok, msg = send_test(cid, to_email)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)[:240]}), 500
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "message": f"Test sent to { _valid_email(to_email) }"})


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>/send", methods=["POST"])
@admin_required
def api_send(cid):
    body = request.get_json(silent=True) or {}
    list_id = (body.get("list_id") or "").strip() or None
    segment_id = (body.get("segment_id") or "").strip() or None
    emails = body.get("emails")
    scheduled_at = (body.get("scheduled_at") or "").strip()
    if scheduled_at:
        def mutate(st):
            c = _campaign(st, cid)
            if not c:
                return None
            if c.get("status") not in ("draft", "scheduled"):
                return None
            c["status"] = "scheduled"
            c["scheduled_at"] = scheduled_at
            if list_id:
                c["list_id"] = list_id
            if segment_id:
                c["segment_id"] = segment_id
            c["updated_at"] = _utcnow()
            return st

        nxt = _cas_update_state(mutate)
        if not _campaign(nxt, cid):
            return jsonify({"success": False, "error": "campaign not found"}), 404
        return jsonify({"success": True, "status": "scheduled", **_overview_payload()})

    recips, err = _claim_send(cid, list_id, emails, segment_id=segment_id)
    if err:
        return jsonify({"success": False, "error": err}), 400
    start_send_async(cid)
    return jsonify({
        "success": True,
        "status": "sending",
        "recipients": len(recips),
        **_overview_payload(),
    })


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>/report")
@admin_required
def api_report(cid):
    state = load_state()
    camp = _campaign(state, cid)
    if not camp:
        return jsonify({"success": False, "error": "campaign not found"}), 404
    snap = get_send_snapshot(cid)
    recips = []
    link_counts = {}
    for email, row in (snap.get("recipients") or {}).items():
        recips.append({
            "email": email,
            "name": row.get("name") or "",
            "status": row.get("status"),
            "sent_at": row.get("sent_at"),
            "opened_at": row.get("opened_at"),
            "open_count": row.get("open_count") or 0,
            "click_count": sum(int(c.get("count") or 1) for c in (row.get("clicks") or [])),
            "clicks": row.get("clicks") or [],
            "error": row.get("error"),
            "unsubscribed_at": row.get("unsubscribed_at"),
        })
        for c in row.get("clicks") or []:
            url = c.get("url") or ""
            link_counts[url] = link_counts.get(url, 0) + int(c.get("count") or 1)
    recips.sort(key=lambda r: (r.get("opened_at") or ""), reverse=True)
    top_links = sorted(
        [{"url": u, "clicks": n} for u, n in link_counts.items() if u],
        key=lambda x: x["clicks"],
        reverse=True,
    )
    store = get_download_store(cid)
    leads = []
    for email, row in (store.get("leads") or {}).items():
        leads.append({
            "email": email,
            "name": row.get("name") or "",
            "company": row.get("company") or "",
            "entered_at": row.get("entered_at"),
            "paid": bool(row.get("paid")),
            "paid_at": row.get("paid_at"),
            "amount_usd": round(int(row.get("amount_cents") or 0) / 100.0, 2),
            "download_count": int(row.get("download_count") or 0),
            "last_download_at": row.get("last_download_at"),
        })
    leads.sort(key=lambda r: (r.get("entered_at") or ""), reverse=True)
    return jsonify({
        "success": True,
        "campaign": _public_campaign(camp),
        "stats": _stats_from_snapshot(snap) if snap.get("recipients") else (camp.get("stats") or _empty_stats()),
        "recipients": recips,
        "top_links": top_links,
        "started_at": snap.get("started_at"),
        "finished_at": snap.get("finished_at"),
        "download_stats": download_stats_from_store(store),
        "downloads": leads,
    })


@newsletter_bp.route("/api/admin/newsletter/lists", methods=["POST"])
@admin_required
def api_create_list():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "list name required"}), 400
    lid = _new_id("list")

    def mutate(st):
        st.setdefault("lists", []).append({
            "id": lid,
            "name": name,
            "kind": "manual",
            "created_at": _utcnow(),
        })
        return st

    _cas_update_state(mutate)
    return jsonify({"success": True, "id": lid, **_overview_payload()})


@newsletter_bp.route("/api/admin/newsletter/lists/<lid>", methods=["DELETE"])
@admin_required
def api_delete_list(lid):
    if lid in ("dashboard-users", "the-read"):
        return jsonify({"success": False, "error": "that list stays"}), 400

    def mutate(st):
        st["lists"] = [x for x in (st.get("lists") or []) if x.get("id") != lid]
        for sub in st.get("subscribers") or []:
            lists = [x for x in (sub.get("list_ids") or []) if x != lid]
            sub["list_ids"] = lists
        return st

    _cas_update_state(mutate)
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/lists/dashboard-users/sync", methods=["POST"])
@admin_required
def api_sync_dashboard():
    sync_dashboard_users()
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/subscribers", methods=["POST"])
@admin_required
def api_add_subscribers():
    body = request.get_json(silent=True) or {}
    rows = body.get("subscribers")
    if not rows:
        rows = [{
            "email": body.get("email"),
            "name": body.get("name") or "",
            "company": body.get("company") or "",
            "list_ids": body.get("list_ids") or ["the-read"],
            "tags": body.get("tags") or [],
        }]
    cleaned = []
    for row in rows:
        email = _valid_email(row.get("email") if isinstance(row, dict) else row)
        if not email:
            continue
        if isinstance(row, dict):
            cleaned.append({
                "email": email,
                "name": (row.get("name") or "").strip(),
                "company": (row.get("company") or "").strip(),
                "list_ids": row.get("list_ids") or ["the-read"],
                "tags": _normalize_tags(row.get("tags") or []),
            })
        else:
            cleaned.append({
                "email": email, "name": "", "company": "",
                "list_ids": ["the-read"], "tags": [],
            })
    if not cleaned:
        return jsonify({"success": False, "error": "no valid emails"}), 400

    def mutate(st):
        existing = {_valid_email(s.get("email")): s for s in (st.get("subscribers") or [])}
        for row in cleaned:
            cur = existing.get(row["email"])
            if not cur:
                st.setdefault("subscribers", []).append({
                    "email": row["email"],
                    "name": row["name"],
                    "company": row["company"],
                    "list_ids": list(dict.fromkeys(row["list_ids"])),
                    "tags": row.get("tags") or [],
                    "status": "subscribed",
                    "source": "manual",
                    "added_at": _utcnow(),
                })
                continue
            lists = list(cur.get("list_ids") or [])
            for lid in row["list_ids"]:
                if lid not in lists:
                    lists.append(lid)
            cur["list_ids"] = lists
            have_tags = _normalize_tags(cur.get("tags") or [])
            for tag in row.get("tags") or []:
                if tag.lower() not in {t.lower() for t in have_tags}:
                    have_tags.append(tag)
            cur["tags"] = have_tags
            if row["name"]:
                cur["name"] = row["name"]
            if row["company"]:
                cur["company"] = row["company"]
            if cur.get("status") == "unsubscribed" and body.get("resubscribe"):
                cur["status"] = "subscribed"
                cur["unsubscribed_at"] = None
        return st

    _cas_update_state(mutate)
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/subscribers/<path:email>", methods=["DELETE"])
@admin_required
def api_remove_subscriber(email):
    email = _valid_email(unquote(email))
    if not email:
        return jsonify({"success": False, "error": "bad email"}), 400

    def mutate(st):
        before = len(st.get("subscribers") or [])
        st["subscribers"] = [
            s for s in (st.get("subscribers") or [])
            if _valid_email(s.get("email")) != email
        ]
        return st if len(st["subscribers"]) != before else None

    _cas_update_state(mutate)
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/subscribers/import", methods=["POST"])
@admin_required
def api_import_subscribers():
    text = ""
    if request.files.get("file"):
        text = request.files["file"].read().decode("utf-8", errors="replace")
    else:
        body = request.get_json(silent=True) or {}
        text = body.get("csv") or body.get("text") or ""
    default_list = (
        (request.form.get("list_id") if request.form else None)
        or ((request.get_json(silent=True) or {}).get("list_id"))
        or "the-read"
    )
    extra_tags = _normalize_tags(
        (request.form.get("tags") if request.form else None)
        or ((request.get_json(silent=True) or {}).get("tags") or [])
    )
    parsed = parse_import_csv(text)
    if not parsed:
        return jsonify({"success": False, "error": "no valid emails in that file"}), 400

    def mutate(st):
        known_lists = {row.get("id") for row in (st.get("lists") or [])}
        known_names = {(row.get("name") or "").strip().lower(): row.get("id") for row in (st.get("lists") or [])}
        for row in parsed:
            lids = [default_list]
            hint = (row.get("list_hint") or "").strip()
            if hint:
                if hint in known_lists:
                    lids = [hint]
                elif hint.lower() in known_names:
                    lids = [known_names[hint.lower()]]
            tags = list(row.get("tags") or [])
            tags.extend(extra_tags)
            _upsert_subscriber(
                st,
                row["email"],
                name=row.get("name") or "",
                company=row.get("company") or "",
                list_ids=lids,
                tags=tags,
                source="import",
            )
        return st

    _cas_update_state(mutate)
    return jsonify({"success": True, "imported": len(parsed), **_overview_payload()})


@newsletter_bp.route("/api/admin/newsletter/segments", methods=["POST"])
@admin_required
def api_create_segment():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "segment name required"}), 400
    sid = _new_id("seg")
    rules = _segment_rules_from_body(body)

    def mutate(st):
        st.setdefault("segments", []).append({
            "id": sid,
            "name": name,
            "rules": rules,
            "created_at": _utcnow(),
        })
        return st

    _cas_update_state(mutate)
    return jsonify({"success": True, "id": sid, **_overview_payload()})


@newsletter_bp.route("/api/admin/newsletter/segments/<sid>", methods=["PUT"])
@admin_required
def api_update_segment(sid):
    body = request.get_json(silent=True) or {}

    def mutate(st):
        seg = _segment(st, sid)
        if not seg:
            return None
        if body.get("name"):
            seg["name"] = (body.get("name") or "").strip()
        if "rules" in body or any(k in body for k in ("list_ids", "tags", "company_contains", "status", "source")):
            seg["rules"] = _segment_rules_from_body(body, seg.get("rules") or {})
        return st

    nxt = _cas_update_state(mutate)
    if not _segment(nxt, sid):
        return jsonify({"success": False, "error": "segment not found"}), 404
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/segments/<sid>", methods=["DELETE"])
@admin_required
def api_delete_segment(sid):
    def mutate(st):
        before = len(st.get("segments") or [])
        st["segments"] = [x for x in (st.get("segments") or []) if x.get("id") != sid]
        return st if len(st["segments"]) != before else None

    _cas_update_state(mutate)
    return jsonify(_overview_payload())


@newsletter_bp.route("/api/admin/newsletter/campaigns/<cid>/attachment", methods=["POST"])
@admin_required
def api_upload_attachment(cid):
    state = load_state()
    if not _campaign(state, cid):
        return jsonify({"success": False, "error": "campaign not found"}), 404
    up = request.files.get("file")
    if not up or not up.filename:
        return jsonify({"success": False, "error": "choose a file to attach"}), 400
    name = _safe_filename(up.filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_ATTACH_EXT:
        return jsonify({"success": False, "error": "use a pdf, deck, spreadsheet, doc, or zip"}), 400
    raw = up.read()
    if not raw:
        return jsonify({"success": False, "error": "that file is empty"}), 400
    if len(raw) > MAX_ATTACH_BYTES:
        return jsonify({"success": False, "error": "file is over 25 MB"}), 400
    _put_bytes(ATTACH_KEY.format(cid=cid, name=name), raw, _content_type_for(name))

    def mutate(st):
        c = _campaign(st, cid)
        if not c:
            return None
        dl = dict(c.get("download") or _empty_download())
        dl["enabled"] = True
        dl["filename"] = name
        dl["original_name"] = (up.filename or name).strip()
        if not dl.get("title"):
            dl["title"] = Path(up.filename or name).stem.replace("_", " ")
        c["download"] = dl
        c["updated_at"] = _utcnow()
        return st

    nxt = _cas_update_state(mutate)
    camp = _campaign(nxt, cid)
    return jsonify({
        "success": True,
        "filename": name,
        "bytes": len(raw),
        "campaign": _public_campaign(camp) if camp else None,
    })


# ---------------------------------------------------------------------------
# Public tracking + assets + preview
# ---------------------------------------------------------------------------

@newsletter_bp.route("/n/o/<token>.gif")
@newsletter_bp.route("/n/o/<token>")
def track_open(token):
    payload = verify_token(token)
    if payload and payload.get("p") == "o":
        try:
            _record_engagement(payload.get("c"), payload.get("e"), "open")
        except Exception:
            traceback.print_exc()
    return Response(
        TRANSPARENT_GIF,
        mimetype="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@newsletter_bp.route("/n/c/<token>")
def track_click(token):
    payload = verify_token(token)
    dest = DEFAULT_PUBLIC_BASE
    if payload and payload.get("p") == "c":
        snap = get_send_snapshot(payload.get("c") or "")
        links = snap.get("links") or []
        idx = payload.get("i")
        if isinstance(idx, int) and 0 <= idx < len(links):
            dest = links[idx]
        elif payload.get("u"):
            dest = payload.get("u")
        try:
            _record_engagement(payload.get("c"), payload.get("e"), "click", dest)
        except Exception:
            traceback.print_exc()
    if not dest.startswith("http"):
        dest = DEFAULT_PUBLIC_BASE
    return redirect(dest, code=302)


@newsletter_bp.route("/n/u/<token>", methods=["GET", "POST"])
def track_unsub(token):
    payload = verify_token(token)
    if not payload or payload.get("p") != "u":
        return _unsub_page("This unsubscribe link is not valid.", ok=False), 400
    email = _valid_email(payload.get("e"))
    if request.method == "POST" or request.args.get("confirm") == "1":
        try:
            _record_engagement(payload.get("c"), email, "unsub")
        except Exception:
            traceback.print_exc()
        return _unsub_page(
            f"{email} is unsubscribed from Crosswalk newsletters. "
            "Nothing else on your account changed."
        )
    return _unsub_page(
        f"Unsubscribe {email} from Crosswalk newsletters?",
        confirm=True,
        token=token,
    )


@newsletter_bp.route("/n/asset/<cid>/<name>")
def public_asset(cid, name):
    name = _safe_filename(name)
    cid = re.sub(r"[^A-Za-z0-9._-]", "", cid or "")
    raw = _get_bytes(ASSET_KEY.format(cid=cid, name=name))
    if raw is None and cid == SEED_CAMPAIGN_ID:
        local = SEED_ASSETS / name
        if local.exists():
            raw = local.read_bytes()
    if raw is None:
        return Response("not found", status=404)
    return Response(
        raw,
        mimetype=_content_type_for(name),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@newsletter_bp.route("/n/preview/<cid>")
@admin_required
def preview_campaign(cid):
    html = _preview_html(cid)
    if not html.strip():
        return Response("No HTML on this campaign yet.", status=404)
    return Response(html, mimetype="text/html; charset=utf-8")


@newsletter_bp.route("/n/d/<cid>", methods=["GET", "POST"])
def public_download_gate(cid):
    cid = re.sub(r"[^A-Za-z0-9._-]", "", cid or "")
    state = load_state_raw()
    camp = _campaign(state, cid)
    if not camp:
        return _download_page("This download is not available.", ok=False), 404
    dl = _campaign_download(camp)
    if not dl.get("enabled"):
        return _download_page("This download is not available.", ok=False), 404
    settings = state.get("settings") or {}
    title = dl.get("title") or camp.get("name") or "Crosswalk report"
    paid = bool(dl.get("paid"))
    price = float(dl.get("price_usd") or 0)
    if request.method == "GET":
        return _download_page(
            f"Enter your email to download {title}." if not paid
            else f"Enter your email and pay ${price:.2f} to download {title}.",
            title=title,
            form=True,
            cid=cid,
            paid=paid,
            price=price,
        )
    email = _valid_email(request.form.get("email") or (request.get_json(silent=True) or {}).get("email"))
    if not email:
        return _download_page(
            "That email is not valid. Try again.",
            title=title, form=True, cid=cid, paid=paid, price=price, ok=False,
        ), 400
    name = (request.form.get("name") or "").strip()[:80]
    record_download_lead(cid, email, name=name)
    list_id = dl.get("list_id") or camp.get("list_id") or "the-read"

    def mutate(st):
        _upsert_subscriber(
            st, email, name=name, list_ids=[list_id],
            tags=["download"],
            source="download",
        )
        return st

    _cas_update_state(mutate)
    if paid:
        return _start_download_checkout(cid, email, title, price, settings)
    token = sign_token({"c": cid, "e": email, "p": "d"})
    return redirect(f"/n/d/{cid}/file/{token}", code=302)


@newsletter_bp.route("/n/d/<cid>/paid")
def public_download_paid(cid):
    cid = re.sub(r"[^A-Za-z0-9._-]", "", cid or "")
    session_id = (request.args.get("session_id") or "").strip()
    state = load_state_raw()
    camp = _campaign(state, cid)
    if not camp:
        return _download_page("This download is not available.", ok=False), 404
    title = _campaign_download(camp).get("title") or camp.get("name") or "Crosswalk report"
    if not session_id:
        return _download_page("Payment did not finish. Enter your email again to retry.", title=title, form=True, cid=cid, paid=True), 400
    try:
        import billing
        sess = billing.retrieve_checkout_session(session_id)
    except Exception as e:
        print(f"[newsletter] retrieve checkout failed: {e}")
        return _download_page("We could not confirm that payment. Try again or write hello@crosswalknyc.com.", title=title, ok=False), 400
    if (sess.get("payment_status") or "") != "paid":
        return _download_page("Payment is not complete yet. If you were charged, write hello@crosswalknyc.com.", title=title, ok=False), 400
    md = sess.get("metadata") or {}
    if (md.get("purpose") or "") != "newsletter_download" or (md.get("campaign_id") or "") != cid:
        return _download_page("That payment does not match this file.", title=title, ok=False), 400
    email = _valid_email(md.get("email") or sess.get("customer_email") or "")
    fulfill_paid_download({
        "id": sess.get("id"),
        "amount_total": sess.get("amount_total") or 0,
        "customer_email": email,
        "metadata": md,
    })
    if not email:
        return _download_page("Payment landed. Write hello@crosswalknyc.com and we will send the file.", title=title), 200
    token = sign_token({"c": cid, "e": email, "p": "d"})
    return redirect(f"/n/d/{cid}/file/{token}", code=302)


@newsletter_bp.route("/n/d/<cid>/file/<token>")
def public_download_file(cid, token):
    cid = re.sub(r"[^A-Za-z0-9._-]", "", cid or "")
    payload = verify_token(token)
    if not payload or payload.get("p") != "d" or payload.get("c") != cid:
        return _download_page("This download link is not valid.", ok=False), 400
    email = _valid_email(payload.get("e"))
    state = load_state_raw()
    camp = _campaign(state, cid)
    if not camp:
        return _download_page("This download is not available.", ok=False), 404
    dl = _campaign_download(camp)
    if not dl.get("enabled") or not dl.get("filename"):
        return _download_page(
            "The file is not attached yet. Write hello@crosswalknyc.com and we will send it.",
            title=dl.get("title") or camp.get("name") or "Crosswalk report",
            ok=False,
        ), 404
    if dl.get("paid"):
        lead = ((get_download_store(cid).get("leads") or {}).get(email) or {})
        if not lead.get("paid"):
            return _download_page("Payment is still required for this file.", form=True, cid=cid, paid=True, title=dl.get("title") or ""), 402
    raw = _get_bytes(ATTACH_KEY.format(cid=cid, name=dl["filename"]))
    if raw is None:
        return _download_page("The file is missing. Write hello@crosswalknyc.com.", ok=False), 404
    mark_file_downloaded(cid, email)
    filename = dl.get("original_name") or dl.get("filename")
    return Response(
        raw,
        mimetype=_content_type_for(filename),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Internals used by routes
# ---------------------------------------------------------------------------

def _store_html_assets(cid, html):
    n = 0

    def save(name, raw):
        nonlocal n
        n += 1
        _put_bytes(ASSET_KEY.format(cid=cid, name=name), raw, _content_type_for(name))
        return "{{ASSET_BASE}}/" + name

    rewritten, _urls = rewrite_data_uris(html or "", save)
    return rewritten, n


def _download_from_body(body, current=None):
    dl = dict(_empty_download())
    if isinstance(current, dict):
        dl.update({k: current.get(k, dl.get(k)) for k in dl})
        dl["filename"] = current.get("filename") or ""
        dl["original_name"] = current.get("original_name") or ""
    nested = body.get("download") if isinstance(body.get("download"), dict) else {}

    def take(key):
        if key in nested:
            return True, nested.get(key)
        alt = "download_" + key
        if alt in body:
            return True, body.get(alt)
        return False, None

    present, val = take("enabled")
    if present:
        dl["enabled"] = bool(val)
    present, val = take("title")
    if present:
        dl["title"] = (val or "").strip()[:160]
    present, val = take("paid")
    if present:
        dl["paid"] = bool(val)
    present, val = take("price_usd")
    if present:
        try:
            dl["price_usd"] = max(0, float(val or 0))
        except (TypeError, ValueError):
            dl["price_usd"] = 0
    present, val = take("list_id")
    if present:
        dl["list_id"] = (val or "the-read").strip() or "the-read"
    if dl["paid"] and dl["price_usd"] < 0.5:
        dl["price_usd"] = 0.5
    if not dl["paid"]:
        dl["price_usd"] = 0
    return dl


def _segment_rules_from_body(body, current=None):
    rules = {
        "list_ids": [],
        "tags": [],
        "company_contains": "",
        "status": "subscribed",
        "source": "",
    }
    if isinstance(current, dict):
        rules.update(current)
    src = body.get("rules") if isinstance(body.get("rules"), dict) else body
    if "list_ids" in src:
        raw = src.get("list_ids") or []
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.split(",") if x.strip()]
        rules["list_ids"] = [x for x in raw if x]
    if "tags" in src or "tags_any" in src:
        rules["tags"] = _normalize_tags(src.get("tags") or src.get("tags_any") or [])
    if "company_contains" in src:
        rules["company_contains"] = (src.get("company_contains") or "").strip()[:80]
    if "status" in src:
        status = (src.get("status") or "subscribed").strip().lower()
        rules["status"] = status if status in ("subscribed", "unsubscribed", "any") else "subscribed"
    if "source" in src:
        rules["source"] = (src.get("source") or "").strip().lower()[:40]
    return rules


def _copy_attachment(src_cid, dest_cid):
    state = load_state_raw()
    src = _campaign(state, src_cid) or {}
    name = ((src.get("download") or {}).get("filename") or "").strip()
    if not name:
        return
    raw = _get_bytes(ATTACH_KEY.format(cid=src_cid, name=name))
    if raw is None:
        return
    _put_bytes(ATTACH_KEY.format(cid=dest_cid, name=name), raw, _content_type_for(name))


def _copy_assets(src_cid, dest_cid):
    if src_cid == SEED_CAMPAIGN_ID and SEED_ASSETS.exists():
        for path in SEED_ASSETS.iterdir():
            if path.is_file():
                _put_bytes(
                    ASSET_KEY.format(cid=dest_cid, name=path.name),
                    path.read_bytes(),
                    _content_type_for(path.name),
                )
        return
    if _use_local():
        src = _local_root() / f"system/newsletter/assets/{src_cid}"
        if src.exists():
            for path in src.iterdir():
                if path.is_file():
                    _put_bytes(
                        ASSET_KEY.format(cid=dest_cid, name=path.name),
                        path.read_bytes(),
                        _content_type_for(path.name),
                    )


def _start_download_checkout(cid, email, title, price, settings):
    try:
        import billing
        if not billing.is_enabled():
            return _download_page(
                "Card checkout is not available right now. Write hello@crosswalknyc.com.",
                title=title, ok=False,
            ), 503
        base = _public_base(settings)
        sess = billing.create_guest_checkout_session(
            amount_usd=price,
            email=email,
            success_url=f"{base}/n/d/{cid}/paid?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/n/d/{cid}",
            product_name=title,
            metadata={
                "purpose": "newsletter_download",
                "campaign_id": cid,
                "email": email,
            },
        )
        url = sess.get("url")
        if not url:
            return _download_page(
                "Checkout did not start. Try again.",
                title=title, form=True, cid=cid, paid=True, price=price, ok=False,
            ), 502
        return redirect(url, code=302)
    except Exception as e:
        print(f"[newsletter] checkout failed: {e}")
        return _download_page(
            "Card checkout failed. Write hello@crosswalknyc.com if it happens again.",
            title=title, form=True, cid=cid, paid=True, price=price, ok=False,
        ), 502


def _download_page(message, ok=True, form=False, cid="", title="", paid=False, price=0):
    tone = "#5E7E12" if ok else "#8E3FA8"
    heading = escape(title or "The full read")
    action = ""
    if form and cid:
        price_line = ""
        button = "Download"
        if paid and price:
            price_line = (
                f'<div style="font-size:14px;color:#5C6560;margin:0 0 16px;">'
                f'${price:.2f} · card checkout</div>'
            )
            button = f"Pay ${price:.2f} and download"
        action = (
            f'<form method="post" action="/n/d/{escape(cid)}" style="margin-top:22px;">'
            '<label style="display:block;font-size:12px;color:#5C6560;margin:0 0 6px;">Email</label>'
            '<input type="email" name="email" required placeholder="you@company.com" '
            'style="width:100%;box-sizing:border-box;padding:12px 14px;border:1px solid #C9C6BA;'
            'border-radius:8px;font-size:16px;background:#fff;color:#0C1618;">'
            '<label style="display:block;font-size:12px;color:#5C6560;margin:14px 0 6px;">Name (optional)</label>'
            '<input type="text" name="name" placeholder="Your name" '
            'style="width:100%;box-sizing:border-box;padding:12px 14px;border:1px solid #C9C6BA;'
            'border-radius:8px;font-size:16px;background:#fff;color:#0C1618;">'
            f"{price_line}"
            f'<button type="submit" style="margin-top:18px;width:100%;background:#0C1618;'
            "color:#E9E8E1;border:0;border-radius:8px;padding:14px 18px;"
            f'font-size:15px;font-weight:700;cursor:pointer;">{escape(button)}</button></form>'
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{heading} · Crosswalk</title></head>
<body style="margin:0;background:#E9E8E1;color:#0C1618;
font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:480px;margin:72px auto;padding:0 24px;">
<div style="font-size:11px;letter-spacing:2.6px;text-transform:uppercase;
color:#5C6560;">Crosswalk / The Read</div>
<h1 style="font-size:28px;margin:16px 0 12px;">{heading}</h1>
<p style="font-size:16px;line-height:1.5;color:#5C6560;">{escape(message)}</p>
{action}
<p style="margin-top:36px;font-size:12px;color:#888C89;">
<span style="color:{tone};">&#9679;</span> {escape(_company_address())}
</p>
</div></body></html>"""


def _company_address():
    try:
        settings = (load_state_raw() or {}).get("settings") or {}
        addr = (settings.get("company_address") or "").strip()
        if addr and addr not in ("Crosswalk, New York, NY", "New York, NY"):
            return addr
    except Exception:
        pass
    return DEFAULT_COMPANY_ADDRESS


def _unsub_page(message, ok=True, confirm=False, token=""):
    action = ""
    if confirm and token:
        action = (
            f'<form method="post" action="/n/u/{escape(token)}">'
            '<button type="submit" style="margin-top:22px;background:#0C1618;'
            "color:#E9E8E1;border:0;border-radius:8px;padding:12px 18px;"
            'font-size:14px;cursor:pointer;">Unsubscribe</button></form>'
        )
    tone = "#5E7E12" if ok else "#8E3FA8"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crosswalk</title></head>
<body style="margin:0;background:#E9E8E1;color:#0C1618;
font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:480px;margin:72px auto;padding:0 24px;">
<div style="font-size:11px;letter-spacing:2.6px;text-transform:uppercase;
color:#5C6560;">Crosswalk / The Read</div>
<h1 style="font-size:28px;margin:16px 0 12px;">Newsletter preference</h1>
<p style="font-size:16px;line-height:1.5;color:#5C6560;">{escape(message)}</p>
{action}
<p style="margin-top:36px;font-size:12px;color:#888C89;">
<span style="color:{tone};">&#9679;</span> {escape(_company_address())}
</p>
</div></body></html>"""


def register_newsletter_blueprint(app):
    try:
        app.register_blueprint(newsletter_bp)
        start_scheduler()
        print("Newsletter blueprint registered")
    except Exception as e:
        print(f"Newsletter blueprint registration failed: {e}")
