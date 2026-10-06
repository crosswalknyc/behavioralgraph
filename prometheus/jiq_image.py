"""Hero image for a chat-built Digital Journey IQ read (2026-10-05,
Jenna: "the display in the dashboard needs to be smart enough to do
all of this and display it properly. right image, header, vernacular").

Hand-built journeys (Young Sheldon, Babylon 5, Dexter's Lab) ship a
still; the chat-built job wrote the payload with no image, so the tile
drew initials. This resolves one the same way the Brand Partnership IQ
thumbnail and the Profile IQ image backfill do:

  1. the subject's own Profile IQ image when an admin uploaded one;
  2. the image_backfill ladder for the subject (IMDb poster for a
     film / series, Wikipedia, web image search), downloaded and stored
     under journey-iq-images/;
  3. the same ladder for the platform when the subject is not a title
     (a brand journey on Amazon shows the brand, else the platform).

Best-effort: every step is wrapped, the function never raises, and a
miss returns None so the job still persists the read.
"""
from __future__ import annotations

import re
import uuid

IMAGE_PREFIX = "journey-iq-images/"

_TITLE_RE = re.compile(
    r"\b(film|movie|series|show|season|episode|documentary|premiere|"
    r"theatrical|opening weekend|watch(?:ed|ers)?|stream(?:ed|ing)?|"
    r"ticket(?:ing|s)?|box office)\b", re.I)


def clean_subject(subject) -> str:
    """'The Influencer Project (film), opening weekend' -> 'The Influencer
    Project'. Drops a parenthetical and anything after a comma."""
    s = str(subject or "").strip()
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = s.split(",")[0].strip()
    return s


def master_for(inputs) -> str:
    """CONTENT for a title, OTHER for a brand or persona journey."""
    blob = " ".join(str((inputs or {}).get(k) or "") for k in
                    ("subject", "journey_kind", "conversion_event",
                     "notes", "category", "platform"))
    if str((inputs or {}).get("journey_kind") or "").lower() in (
            "watch", "ticketing", "before_after", "discovery_existing",
            "music"):
        return "CONTENT"
    return "CONTENT" if _TITLE_RE.search(blob) else "OTHER"


def profile_iq_image(host, name):
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


def resolve_jiq_image(host, inputs, log=print):
    """(image_url, source_tag) for the journey, or (None, 'none')."""
    try:
        from image_backfill import resolve_image_url
    except Exception:
        resolve_image_url = None
    subject = clean_subject((inputs or {}).get("subject"))
    master = master_for(inputs)
    if subject:
        hit = profile_iq_image(host, subject)
        if hit:
            return hit, f"profile_iq:{subject}"
    if resolve_image_url is None or not subject:
        return None, "none"
    ladder = [(subject, master)]
    if master != "CONTENT":
        plat = clean_subject((inputs or {}).get("platform"))
        if plat and plat.lower() != subject.lower():
            ladder.append((plat, "OTHER"))
    for name, bucket in ladder:
        try:
            url, tag = resolve_image_url(name, bucket)
        except Exception as exc:
            log(f"[jiq-image] {name!r} lookup failed: {exc}")
            continue
        if not url:
            continue
        stored = store_remote_image(host, url)
        if stored:
            return stored, f"{tag}:{name}"
        log(f"[jiq-image] {name!r} found via {tag} but download failed")
    return None, "none"


def attach_hero(payload: dict, image_url) -> dict:
    """Write the image where the Digital Journey tab reads it: the run
    meta and the story block's meta. No-op on a miss."""
    if not image_url or not isinstance(payload, dict):
        return payload
    payload.setdefault("meta", {})["hero_image"] = image_url
    mode = (payload.get("meta") or {}).get("story_mode") or "fragrance_shop_journey"
    block = payload.get(mode)
    if isinstance(block, dict):
        block.setdefault("meta", {})["hero_image"] = image_url
    return payload
