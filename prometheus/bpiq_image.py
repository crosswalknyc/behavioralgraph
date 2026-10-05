"""Thumbnail for a Brand Partnership IQ result (2026-10-05, Jenna:
"for willow smith it should have uploaded a willow smith photo").

Every hand-built partnership read carried an image in the metadata
sidecar; the chat-driven job wrote title + category and left the tile
blank. This resolves one the same way the Profile IQ image backfill
does, in this order:

  1. the partner talent / title's own Profile IQ image when an admin
     already uploaded one (same photo the profile shows);
  2. the image_backfill ladder for the qualifier (IMDb headshot for
     talent, IMDb poster for a title, Wikipedia, web image search),
     downloaded and stored under brand-partnership-iq-images/;
  3. the same ladder for the brand partner.

Pure best-effort: every step is wrapped, the function never raises,
and a miss returns None so the job still registers the read.
"""
from __future__ import annotations

import uuid

IMAGE_PREFIX = "brand-partnership-iq-images/"

_MASTER_BY_QUALIFIER = {
    "talent": "TALENT",
    "show": "CONTENT",
    "franchise": "CONTENT",
    "event": "OTHER",
    "other": "OTHER",
}


def _names(inputs):
    """Qualifier names first, brand partner last."""
    out = []
    q = (inputs or {}).get("qualifier")
    if isinstance(q, (list, tuple)):
        out.extend(str(x).strip() for x in q if str(x or "").strip())
    elif str(q or "").strip():
        out.append(str(q).strip())
    qv = (inputs or {}).get("qualifier_value")
    if isinstance(qv, (list, tuple)):
        out.extend(str(x).strip() for x in qv if str(x or "").strip())
    seen = set()
    uniq = []
    for n in out:
        if n.lower() not in seen:
            seen.add(n.lower())
            uniq.append(n)
    return uniq


def profile_iq_image(host, name):
    """The admin-uploaded Profile IQ image for `name`, or None."""
    try:
        host.load_profile_image_cache()
        cache = getattr(host, "profile_image_cache", None) or {}
        for key in host._profile_image_lookup_keys(name):
            hit = cache.get(key) or {}
            if hit.get("is_custom") and hit.get("image_url"):
                return str(hit["image_url"])
    except Exception:
        pass
    return None


def store_remote_image(host, url):
    """Download `url` and store it under the BPIQ image prefix; returns
    the dashboard-served path or None."""
    try:
        from image_backfill import download_image
        got = download_image(url)
        if not got:
            return None
        data, ext = got
        content_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
        key = f"{IMAGE_PREFIX}{uuid.uuid4().hex}.{ext}"
        host.s3_client.put_object(
            Bucket=host.S3_BUCKET, Key=key, Body=data,
            ContentType=content_type)
        return f"/api/profile-image-file/{key}"
    except Exception:
        return None


def resolve_bpiq_image(host, inputs, log=print):
    """(image_url, source_tag) for the partnership, or (None, 'none')."""
    try:
        from image_backfill import resolve_image_url
    except Exception:
        resolve_image_url = None
    master = _MASTER_BY_QUALIFIER.get(
        str((inputs or {}).get("qualifier_type") or "").lower(), "OTHER")
    names = _names(inputs)
    for name in names:
        hit = profile_iq_image(host, name)
        if hit:
            return hit, f"profile_iq:{name}"
    if resolve_image_url is None:
        return None, "none"
    ladder = [(n, master) for n in names]
    brand = str((inputs or {}).get("brand_partner") or "").strip()
    if brand:
        ladder.append((brand, "OTHER"))
    for name, bucket in ladder:
        try:
            url, tag = resolve_image_url(name, bucket)
        except Exception as exc:
            log(f"[bpiq-image] {name!r} lookup failed: {exc}")
            continue
        if not url:
            continue
        stored = store_remote_image(host, url)
        if stored:
            return stored, f"{tag}:{name}"
        log(f"[bpiq-image] {name!r} found via {tag} but download failed")
    return None, "none"
