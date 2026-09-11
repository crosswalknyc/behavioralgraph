"""LinkedIn company-page posting for The Read.

OAuth, share-card image, clickbait copy, and the Posts API live here.
Tokens stay in newsletter state. Nothing here is partner-visible.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from urllib.parse import urlencode

import requests

LINKEDIN_AUTH = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API = "https://api.linkedin.com/rest"
LINKEDIN_V2 = "https://api.linkedin.com/v2"
LINKEDIN_VERSION = os.environ.get("LINKEDIN_API_VERSION", "202509")
SCOPES = "openid profile email w_organization_social r_organization_social"
DEAD_SCOPES = frozenset({"offline_access", "r_liteprofile", "r_emailaddress"})
UNKNOWN_SCOPE_RE = re.compile(r"unknown scope\s+[\"']?([a-z0-9_]+)", re.I)
IMAGE_NAME = "linkedin.jpg"
SEED_HEADLINE = "YouTube was supposed to lose. 22.4 million people proved it did not."
SEED_TEXT = (
    "Kinigra Deon reached more Creatorverse viewers than MrBeast.\n\n"
    "22.4 million people opened a Creatorverse title in year one. "
    "Their YouTube time never moved.\n\n"
    "23% of Tubi's audience. 41% of the hours.\n\n"
    "The first-year numbers are in."
)

_FONT_DIRS = [
    Path(__file__).resolve().parent.parent / "website" / "assets" / "fonts",
    Path(__file__).resolve().parent.parent / ".cursor" / "skills" / "crosswalk-brand-standards" / "assets" / "fonts",
    Path(__file__).resolve().parent / ".." / "website" / "assets" / "fonts",
]
_LOGO_PATHS = [
    Path(__file__).resolve().parent.parent / ".cursor" / "skills" / "crosswalk-brand-standards" / "assets" / "crosswalk-logo-white.png",
    Path(__file__).resolve().parent.parent / "website" / "assets" / "logos" / "crosswalk-logo-white.png",
]


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _nl():
    import newsletter
    return newsletter


def empty_campaign_linkedin():
    return {
        "enabled": True,
        "headline": "",
        "text": "",
        "image_name": IMAGE_NAME,
        "posted_at": None,
        "post_urn": "",
        "post_url": "",
        "error": "",
    }


def default_seed_linkedin():
    row = empty_campaign_linkedin()
    row["headline"] = SEED_HEADLINE
    row["text"] = SEED_TEXT
    return row


def empty_settings_linkedin():
    return {
        "client_id": "",
        "client_secret": "",
        "auto_post": True,
        "organization_id": "",
        "organization_name": "",
        "organization_urn": "",
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0,
        "pages": [],
        "oauth_state": "",
        "oauth_rejected": [],
        "connected_at": "",
    }


def _li_settings(settings=None):
    raw = ((settings or {}).get("linkedin") or {})
    out = empty_settings_linkedin()
    if isinstance(raw, dict):
        out.update(raw)
    return out


def _campaign_li(camp):
    raw = (camp or {}).get("linkedin") or {}
    out = empty_campaign_linkedin()
    if isinstance(raw, dict):
        out.update({k: raw.get(k, out.get(k)) for k in out})
    return out


def client_id(settings=None):
    env = (os.environ.get("LINKEDIN_CLIENT_ID") or "").strip()
    if env:
        return env
    return (_li_settings(settings).get("client_id") or "").strip()


def client_secret(settings=None):
    env = (os.environ.get("LINKEDIN_CLIENT_SECRET") or "").strip()
    if env:
        return env
    return (_li_settings(settings).get("client_secret") or "").strip()


def redirect_uri(settings=None):
    env = (os.environ.get("LINKEDIN_REDIRECT_URI") or "").strip()
    if env:
        return env.rstrip("/")
    nl = _nl()
    return f"{nl._public_base(settings)}/n/linkedin/callback"


def public_status(settings=None):
    li = _li_settings(settings)
    org_id = (li.get("organization_id") or "").strip()
    token = (li.get("access_token") or "").strip()
    return {
        "connected": bool(token and org_id),
        "has_client_id": bool(client_id(settings)),
        "has_client_secret": bool(client_secret(settings)),
        "client_id": client_id(settings),
        "redirect_uri": redirect_uri(settings),
        "auto_post": bool(li.get("auto_post", True)),
        "organization_id": org_id,
        "organization_name": (li.get("organization_name") or "").strip(),
        "pages": li.get("pages") or [],
        "connected_at": li.get("connected_at") or "",
    }


def public_campaign_linkedin(camp, settings=None):
    row = _campaign_li(camp)
    cid = (camp or {}).get("id") or ""
    nl = _nl()
    base = nl._public_base(settings)
    image_name = row.get("image_name") or IMAGE_NAME
    has_image = False
    if cid:
        has_image = nl._get_bytes(nl.ASSET_KEY.format(cid=cid, name=image_name)) is not None
        if not has_image and cid == nl.SEED_CAMPAIGN_ID:
            has_image = (nl.SEED_ASSETS / image_name).exists()
    return {
        "enabled": bool(row.get("enabled", True)),
        "headline": row.get("headline") or "",
        "text": row.get("text") or "",
        "image_name": image_name,
        "image_url": f"{base}/n/asset/{cid}/{image_name}" if cid and has_image else "",
        "share_url": f"{base}/n/r/{cid}" if cid else "",
        "has_image": has_image,
        "posted_at": row.get("posted_at"),
        "post_url": row.get("post_url") or "",
        "error": row.get("error") or "",
    }


def merge_settings_body(current, body):
    li = _li_settings({"linkedin": current if isinstance(current, dict) else {}})
    src = body.get("linkedin") if isinstance(body.get("linkedin"), dict) else body
    if "client_id" in src:
        li["client_id"] = (src.get("client_id") or "").strip()
    secret = (src.get("client_secret") or "").strip()
    if secret and secret != "••••••••":
        li["client_secret"] = secret
    if "auto_post" in src:
        li["auto_post"] = bool(src.get("auto_post"))
    if "organization_id" in src and src.get("organization_id"):
        org_id = str(src.get("organization_id")).strip()
        li["organization_id"] = org_id
        li["organization_urn"] = f"urn:li:organization:{org_id}"
        for page in li.get("pages") or []:
            if str(page.get("id")) == org_id:
                li["organization_name"] = page.get("name") or li.get("organization_name")
                break
    return li


def merge_campaign_body(current, body):
    row = _campaign_li({"linkedin": current if isinstance(current, dict) else {}})
    src = body.get("linkedin") if isinstance(body.get("linkedin"), dict) else {}
    if "enabled" in src:
        row["enabled"] = bool(src.get("enabled"))
    if "headline" in src:
        row["headline"] = (src.get("headline") or "").strip()[:220]
    if "text" in src:
        row["text"] = (src.get("text") or "").strip()[:2900]
    return row


def requested_scopes(settings=None):
    rejected = set(_li_settings(settings).get("oauth_rejected") or [])
    rejected |= DEAD_SCOPES
    return [s for s in SCOPES.split() if s and s not in rejected]


def unknown_scope_from_error(err):
    text = unescape(err or "")
    for token in ("&quot;", "&#34;", "&#39;", "&apos;"):
        text = text.replace(token, '"')
    match = UNKNOWN_SCOPE_RE.search(text)
    return (match.group(1) if match else "").strip()


def authorization_url(settings, state_token, scopes=None):
    cid = client_id(settings)
    if not cid:
        return ""
    scope = " ".join(scopes or requested_scopes(settings))
    if not scope:
        return ""
    q = urlencode({
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": redirect_uri(settings),
        "state": state_token,
        "scope": scope,
    })
    return f"{LINKEDIN_AUTH}?{q}"


def retry_authorization_url(rejected):
    """Start a fresh LinkedIn login without a scope the app does not have."""
    rejected = (rejected or "").strip()
    if not rejected:
        return ""
    nl = _nl()
    settings = (nl.load_state_raw() or {}).get("settings") or {}
    if not client_id(settings) or not client_secret(settings):
        return ""
    li = _li_settings(settings)
    already = set(li.get("oauth_rejected") or [])
    already.add(rejected)
    already |= DEAD_SCOPES
    scopes = [s for s in SCOPES.split() if s and s not in already]
    if not scopes:
        return ""
    token = nl.sign_token({"p": "li", "t": _utcnow(), "retry": rejected})
    li["oauth_rejected"] = sorted(already)
    li["oauth_state"] = token
    persist_linkedin_settings(li)
    return authorization_url(settings, token, scopes)


def _api_headers(token, rest=True):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if rest:
        headers["Linkedin-Version"] = LINKEDIN_VERSION
        headers["X-Restli-Protocol-Version"] = "2.0.0"
    return headers


def exchange_code(settings, code):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(settings),
        "client_id": client_id(settings),
        "client_secret": client_secret(settings),
    }
    resp = requests.post(LINKEDIN_TOKEN, data=data, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(_token_error(resp))
    return resp.json()


def refresh_access_token(settings):
    li = _li_settings(settings)
    refresh = (li.get("refresh_token") or "").strip()
    if not refresh:
        return li
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id(settings),
        "client_secret": client_secret(settings),
    }
    resp = requests.post(LINKEDIN_TOKEN, data=data, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(_token_error(resp))
    payload = resp.json()
    li["access_token"] = payload.get("access_token") or li.get("access_token")
    if payload.get("refresh_token"):
        li["refresh_token"] = payload["refresh_token"]
    expires_in = int(payload.get("expires_in") or 0)
    if expires_in:
        li["expires_at"] = int(time.time()) + expires_in - 120
    return li


def _token_error(resp):
    try:
        body = resp.json()
        return body.get("error_description") or body.get("error") or resp.text[:240]
    except Exception:
        return resp.text[:240] or f"LinkedIn HTTP {resp.status_code}"


def ensure_fresh_token(settings):
    li = _li_settings(settings)
    expires_at = int(li.get("expires_at") or 0)
    if li.get("access_token") and expires_at and expires_at > int(time.time()) + 60:
        return li
    if li.get("refresh_token"):
        return refresh_access_token(settings)
    return li


def list_company_pages(token):
    pages = _list_pages_rest(token)
    if pages:
        return pages
    return _list_pages_v2(token)


def _list_pages_rest(token):
    url = (
        f"{LINKEDIN_API}/organizationAcls"
        "?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED"
    )
    resp = requests.get(url, headers=_api_headers(token), timeout=30)
    if resp.status_code >= 400:
        return []
    pages = []
    for el in (resp.json() or {}).get("elements") or []:
        urn = el.get("organization") or el.get("organizationalTarget") or ""
        org_id = _org_id_from_urn(urn)
        if not org_id:
            continue
        name = _org_name(token, org_id) or f"Company {org_id}"
        pages.append({"id": org_id, "name": name, "urn": f"urn:li:organization:{org_id}"})
    return pages


def _list_pages_v2(token):
    url = (
        f"{LINKEDIN_V2}/organizationalEntityAcls"
        "?q=roleAssignee&role=ADMINISTRATOR"
        "&projection=(elements*(organizationalTarget~(localizedName,id)))"
    )
    resp = requests.get(url, headers=_api_headers(token, rest=False), timeout=30)
    if resp.status_code >= 400:
        return []
    pages = []
    for el in (resp.json() or {}).get("elements") or []:
        target = el.get("organizationalTarget~") or {}
        urn = el.get("organizationalTarget") or ""
        org_id = str(target.get("id") or "") or _org_id_from_urn(urn)
        if not org_id:
            continue
        name = target.get("localizedName") or _org_name(token, org_id) or f"Company {org_id}"
        pages.append({"id": org_id, "name": name, "urn": f"urn:li:organization:{org_id}"})
    return pages


def _org_id_from_urn(urn):
    text = str(urn or "")
    m = re.search(r"organization:(\d+)", text)
    return m.group(1) if m else ""


def _org_name(token, org_id):
    resp = requests.get(
        f"{LINKEDIN_API}/organizations/{org_id}",
        headers=_api_headers(token),
        timeout=20,
    )
    if resp.status_code >= 400:
        return ""
    data = resp.json() or {}
    return (
        data.get("localizedName")
        or (data.get("name") or {}).get("localized")
        or ""
    )


def apply_oauth_payload(settings, payload, pages):
    li = _li_settings(settings)
    li["access_token"] = payload.get("access_token") or ""
    if payload.get("refresh_token"):
        li["refresh_token"] = payload["refresh_token"]
    expires_in = int(payload.get("expires_in") or 0)
    li["expires_at"] = int(time.time()) + expires_in - 120 if expires_in else 0
    li["pages"] = pages
    li["oauth_state"] = ""
    li["oauth_rejected"] = []
    li["connected_at"] = _utcnow()
    if len(pages) == 1:
        li["organization_id"] = pages[0]["id"]
        li["organization_name"] = pages[0]["name"]
        li["organization_urn"] = pages[0]["urn"]
    elif pages and not li.get("organization_id"):
        li["organization_id"] = pages[0]["id"]
        li["organization_name"] = pages[0]["name"]
        li["organization_urn"] = pages[0]["urn"]
    return li


def disconnect(settings):
    li = _li_settings(settings)
    keep_id = li.get("client_id") or ""
    keep_secret = li.get("client_secret") or ""
    auto = bool(li.get("auto_post", True))
    nxt = empty_settings_linkedin()
    nxt["client_id"] = keep_id
    nxt["client_secret"] = keep_secret
    nxt["auto_post"] = auto
    return nxt


def generate_post_copy(campaign, html=""):
    camp = campaign or {}
    if camp.get("id") == "the-read-creatorverse":
        existing = _campaign_li(camp)
        if existing.get("headline") and existing.get("text"):
            return existing["headline"], existing["text"]
        return SEED_HEADLINE, SEED_TEXT
    subject = (camp.get("subject") or camp.get("name") or "The Read").strip()
    preheader = (camp.get("preheader") or "").strip()
    excerpt = _nl().strip_tags(html or "")[:900]
    generated = _claude_copy(subject, preheader, excerpt)
    if generated:
        return generated
    headline = subject
    if preheader and preheader.lower() not in headline.lower():
        headline = f"{subject} {preheader}".strip()
    if not headline.endswith("."):
        headline = headline.rstrip(".") + "."
    text = preheader or subject
    if excerpt:
        first = excerpt.split(".")[0].strip()
        if first and first.lower() not in text.lower():
            text = f"{text}\n\n{first}." if text else f"{first}."
    text = (text or "The new issue of The Read is out.").strip()
    return headline[:220], text[:2900]


def _claude_copy(subject, preheader, excerpt):
    try:
        from claude_client import claude_messages
    except Exception:
        return None
    system = (
        "Write a LinkedIn company-page post for Crosswalk, The Read. "
        "Return JSON only with keys headline and text. "
        "Headline is one sentence, a finding the reader can argue with, ends in a period. "
        "Text is 40 to 90 words, short paragraphs, number-led, a little clickbait, still true. "
        "No hashtags, no emojis, no em dashes, no en dashes, no 'actually', no 'absolutely'. "
        "Never say modeled, synth, panel, pipeline, or Claude. "
        "Do not include the URL."
    )
    user = f"Subject: {subject}\nPreview: {preheader}\nExcerpt: {excerpt}"
    try:
        raw = claude_messages(system=system, user=user, max_tokens=400, temperature=0.6)
    except Exception:
        return None
    if not raw:
        return None
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        headline = (data.get("headline") or "").strip()
        text = (data.get("text") or "").strip()
        if headline and text:
            return headline[:220], text[:2900]
    except Exception:
        return None
    return None


def _font_path(weight="Bold"):
    name = f"Inter_18pt-{weight}.ttf"
    for folder in _FONT_DIRS:
        path = Path(folder) / name
        if path.exists():
            return str(path)
    return None


def _logo_path():
    for path in _LOGO_PATHS:
        if path.exists():
            return str(path)
    return None


def render_share_card(headline, kicker="The Read"):
    """1200x627 Graphite card. Used when a campaign has no custom image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise RuntimeError("Pillow is required to draw a LinkedIn card") from e

    w, h = 1200, 627
    img = Image.new("RGB", (w, h), "#0C1618")
    draw = ImageDraw.Draw(img)
    bold = _load_font(82 if len(headline) < 28 else 48, "Bold")
    light = _load_font(16, "Regular")
    kicker_font = _load_font(14, "Medium")

    draw.rectangle((72, 72, 88, 88), fill="#C7F23E")
    draw.text((104, 72), (kicker or "THE READ").upper(), font=kicker_font, fill="#C7F23E")

    logo = _logo_path()
    if logo:
        try:
            mark = Image.open(logo).convert("RGBA")
            mark.thumbnail((220, 36))
            img.paste(mark, (w - 72 - mark.width, 70), mark)
        except Exception:
            pass

    lines = _wrap_text(headline, bold, 1040, draw)
    y = 200
    for i, line in enumerate(lines[:3]):
        color = "#C7F23E" if i == len(lines[:3]) - 1 and len(lines) > 1 else "#E9E8E1"
        draw.text((72, y), line, font=bold, fill=color)
        y += int(bold.size * 1.12) if hasattr(bold, "size") else 70

    draw.text((72, h - 72), "CROSSWALK / THE READ", font=light, fill="#7C878A")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def _load_font(size, weight):
    from PIL import ImageFont
    path = _font_path(weight)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap_text(text, font, max_width, draw):
    words = (text or "").split()
    lines = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        box = draw.textbbox((0, 0), trial, font=font)
        if box[2] - box[0] <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [text or ""]


def ensure_share_image(cid, campaign, html=""):
    nl = _nl()
    row = _campaign_li(campaign)
    name = row.get("image_name") or IMAGE_NAME
    existing = nl._get_bytes(nl.ASSET_KEY.format(cid=cid, name=name))
    if existing:
        return name, existing
    if cid == nl.SEED_CAMPAIGN_ID and (nl.SEED_ASSETS / name).exists():
        raw = (nl.SEED_ASSETS / name).read_bytes()
        nl._put_bytes(nl.ASSET_KEY.format(cid=cid, name=name), raw, "image/jpeg")
        return name, raw
    headline, _text = generate_post_copy(campaign, html)
    try:
        raw = render_share_card(headline)
    except Exception:
        fallback = None
        if cid == nl.SEED_CAMPAIGN_ID and (nl.SEED_ASSETS / "img06.jpg").exists():
            fallback = (nl.SEED_ASSETS / "img06.jpg").read_bytes()
        if not fallback:
            raise
        raw = fallback
    nl._put_bytes(nl.ASSET_KEY.format(cid=cid, name=name), raw, "image/jpeg")
    return name, raw


def store_uploaded_image(cid, raw, filename="linkedin.jpg"):
    nl = _nl()
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"
    name = "linkedin.jpg" if ext in ("jpg", "jpeg") else f"linkedin.{ext}"
    nl._put_bytes(nl.ASSET_KEY.format(cid=cid, name=name), raw, nl._content_type_for(name))
    return name


def share_url(cid, settings=None):
    nl = _nl()
    return f"{nl._public_base(settings)}/n/r/{cid}"


def inject_share_meta(html, title, description, image_url, page_url):
    tags = (
        f'<meta property="og:type" content="article">\n'
        f'<meta property="og:title" content="{escape(title)}">\n'
        f'<meta property="og:description" content="{escape(description)}">\n'
        f'<meta property="og:image" content="{escape(image_url)}">\n'
        f'<meta property="og:url" content="{escape(page_url)}">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{escape(title)}">\n'
        f'<meta name="twitter:description" content="{escape(description)}">\n'
        f'<meta name="twitter:image" content="{escape(image_url)}">\n'
    )
    html = html or ""
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", r"\1\n" + tags, html, count=1, flags=re.I)
    return tags + html


def _upload_image(token, org_urn, raw, content_type="image/jpeg"):
    init = {
        "initializeUploadRequest": {
            "owner": org_urn,
        }
    }
    resp = requests.post(
        f"{LINKEDIN_API}/images?action=initializeUpload",
        headers=_api_headers(token),
        json=init,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"LinkedIn image init failed: {_token_error(resp)}")
    value = (resp.json() or {}).get("value") or {}
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise RuntimeError("LinkedIn did not return an image upload URL")
    put = requests.put(
        upload_url,
        data=raw,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        timeout=60,
    )
    if put.status_code >= 400:
        raise RuntimeError(f"LinkedIn image upload failed: {put.text[:240]}")
    return image_urn


def _post_body(org_urn, commentary, headline, image_urn, link):
    return {
        "author": org_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "article": {
                "source": link,
                "title": headline,
                "description": commentary[:200],
                "thumbnail": image_urn,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def _post_image_fallback(org_urn, commentary, headline, image_urn):
    return {
        "author": org_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "id": image_urn,
                "title": headline,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def create_company_post(settings, headline, text, image_raw, link, content_type="image/jpeg"):
    li = ensure_fresh_token(settings)
    token = (li.get("access_token") or "").strip()
    org_id = (li.get("organization_id") or "").strip()
    if not token:
        raise RuntimeError("LinkedIn is not connected")
    if not org_id:
        raise RuntimeError("Pick a company page in Admin")
    org_urn = li.get("organization_urn") or f"urn:li:organization:{org_id}"
    image_urn = _upload_image(token, org_urn, image_raw, content_type)
    commentary = (text or "").strip()
    if link and link not in commentary:
        commentary = f"{commentary}\n\n{link}".strip()
    body = _post_body(org_urn, commentary, headline, image_urn, link)
    resp = requests.post(
        f"{LINKEDIN_API}/posts",
        headers=_api_headers(token),
        json=body,
        timeout=40,
    )
    if resp.status_code >= 400:
        fallback = _post_image_fallback(org_urn, commentary, headline, image_urn)
        resp = requests.post(
            f"{LINKEDIN_API}/posts",
            headers=_api_headers(token),
            json=fallback,
            timeout=40,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"LinkedIn post failed: {_token_error(resp)}")
    post_urn = resp.headers.get("x-restli-id") or (resp.json() or {}).get("id") or ""
    post_url = ""
    if post_urn:
        encoded = requests.utils.quote(post_urn, safe="")
        post_url = f"https://www.linkedin.com/feed/update/{encoded}"
    return {
        "linkedin": li,
        "post_urn": post_urn,
        "post_url": post_url,
    }


def persist_linkedin_settings(li):
    nl = _nl()

    def mutate(st):
        st.setdefault("settings", {})["linkedin"] = li
        return st

    return nl._cas_update_state(mutate)


def persist_campaign_linkedin(cid, patch):
    nl = _nl()

    def mutate(st):
        c = nl._campaign(st, cid)
        if not c:
            return None
        row = _campaign_li(c)
        row.update(patch)
        c["linkedin"] = row
        c["updated_at"] = _utcnow()
        return st

    return nl._cas_update_state(mutate)


def prepare_campaign_post(cid):
    nl = _nl()
    state = nl.load_state()
    camp = nl._campaign(state, cid)
    if not camp:
        raise RuntimeError("campaign not found")
    html = nl.get_campaign_html(cid) or ""
    row = _campaign_li(camp)
    headline = (row.get("headline") or "").strip()
    text = (row.get("text") or "").strip()
    if not headline or not text:
        headline, text = generate_post_copy(camp, html)
        persist_campaign_linkedin(cid, {"headline": headline, "text": text})
    name, raw = ensure_share_image(cid, {**camp, "linkedin": {**row, "headline": headline, "text": text}}, html)
    if name != row.get("image_name"):
        persist_campaign_linkedin(cid, {"image_name": name})
    settings = state.get("settings") or {}
    return {
        "campaign": camp,
        "headline": headline,
        "text": text,
        "image_name": name,
        "image_raw": raw,
        "link": share_url(cid, settings),
        "settings": settings,
    }


def post_campaign(cid):
    prepared = prepare_campaign_post(cid)
    settings = prepared["settings"]
    if not public_status(settings).get("connected"):
        raise RuntimeError("Connect the LinkedIn company page in Admin first")
    result = create_company_post(
        settings,
        prepared["headline"],
        prepared["text"],
        prepared["image_raw"],
        prepared["link"],
        content_type="image/jpeg",
    )
    persist_linkedin_settings(result["linkedin"])
    persist_campaign_linkedin(cid, {
        "headline": prepared["headline"],
        "text": prepared["text"],
        "image_name": prepared["image_name"],
        "posted_at": _utcnow(),
        "post_urn": result.get("post_urn") or "",
        "post_url": result.get("post_url") or "",
        "error": "",
    })
    return result


def maybe_autopost(cid):
    try:
        nl = _nl()
        state = nl.load_state_raw()
        camp = nl._campaign(state, cid)
        if not camp:
            return
        settings = state.get("settings") or {}
        li_set = _li_settings(settings)
        row = _campaign_li(camp)
        if not row.get("enabled", True):
            return
        if not li_set.get("auto_post", True):
            return
        if not public_status(settings).get("connected"):
            persist_campaign_linkedin(cid, {"error": "LinkedIn is not connected"})
            return
        if row.get("posted_at") and row.get("post_urn"):
            return
        post_campaign(cid)
        print(f"newsletter LinkedIn post published {cid}")
    except Exception as e:
        traceback.print_exc()
        try:
            persist_campaign_linkedin(cid, {"error": str(e)[:400]})
        except Exception:
            pass
