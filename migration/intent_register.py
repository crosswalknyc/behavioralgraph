"""Register a freshly ingested Attribution IQ title so it appears in the
dashboard's Attribution IQ title selector.

Analogous to `migration/dashboard_register.py`, but writes into
`intent/registry.json` in s3://dashboard-inputs/ (separate file so it
doesn't collide with the Profile IQ quick-selects).

Two side effects:

  1. `s3://dashboard-inputs/intent/registry.json`
        Persisted title list. The Flask app loads this in
        `/api/intent/titles` and refreshes when its ETag changes.

  2. `s3://dashboard-inputs/metadata/admin_quick_selects.json`
        Optional admin gate, same file the Profile IQ dropdown uses.
        Intent titles aren't profile rows, so they're recorded under a
        separate `intent_titles` key (never collides with `profiles`).

Usage:

    from migration.intent_register import register_intent_title

    register_intent_title(
        title_slug='goat',
        display_name='Goat',
        distributor='Sony Pictures Animation',
        opening_date='2026-02-13',
        ticketing_open_date='2026-01-21',
        source_xlsx_s3_key='intent/goat/source/GOAT_Campaign_URL_Mapping.xlsx',
        asset_count=100,
        phases=['Trailer Launch', 'Bridge Campaign',
                'Branding (T-4)', 'Branding (T-3)',
                'Branding (T-2)', 'Branding (T-1)',
                'Opening Weekend (T-0)'],
    )

The helper is idempotent: re-registering the same title_slug updates the
entry in place and leaves any admin-set False quick_select flag unchanged.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

import boto3

BUCKET = os.environ.get("INTENT_REGISTRY_BUCKET", "dashboard-inputs")
REGISTRY_KEY = "intent/registry.json"
QUICK_SELECTS_KEY = "metadata/admin_quick_selects.json"


def _empty_registry() -> dict:
    return {
        "titles": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
    }


def _load_or_init_registry(s3) -> dict:
    try:
        resp = s3.get_object(Bucket=BUCKET, Key=REGISTRY_KEY)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return _empty_registry()
    except Exception as e:
        msg = str(e)
        if "NoSuchKey" in msg or "not found" in msg.lower():
            return _empty_registry()
        raise


def register_intent_title(
    title_slug: str,
    display_name: str,
    distributor: str,
    opening_date: str,
    ticketing_open_date: Optional[str] = None,
    source_xlsx_s3_key: Optional[str] = None,
    asset_count: Optional[int] = None,
    phases: Optional[Iterable[str]] = None,
    audiences_of_interest: Optional[Iterable[str]] = None,
    image_url: Optional[str] = None,
    # ── Brand-mode extensions (added 2026-08-12 alongside the
    # Analysis IQ "Build Intent Campaign" agent). Films may omit
    # all of these and default to the original film-mode rendering.
    title_type: Optional[str] = None,          # 'film' | 'brand'
    terminology: Optional[dict] = None,        # per-title terminology overrides
    enabled_tabs: Optional[dict] = None,       # per-title tab enable/disable
    brand_config: Optional[dict] = None,       # brand-specific config (attribution window, LTV, etc.)
    s3_client=None,
) -> dict:
    """Add or update a title row in the Attribution IQ registry. Idempotent.

    Brand campaigns pass `title_type='brand'` plus a `terminology` block
    + `enabled_tabs` map so the dashboard can render fintech / retail /
    DTC-style campaigns instead of film-shaped labels. See
    `intent_iq.TITLE_TYPE_DEFAULTS` for the default keys + shapes.
    """
    s3 = s3_client or boto3.client("s3", region_name="us-east-2")
    now_iso = datetime.now(timezone.utc).isoformat()

    registry = _load_or_init_registry(s3)
    titles = registry.setdefault("titles", [])

    new_entry = {
        "title_slug": title_slug,
        "display_name": display_name,
        "distributor": distributor,
        "opening_date": opening_date,
        "ticketing_open_date": ticketing_open_date,
        "source_xlsx_s3_key": source_xlsx_s3_key,
        "asset_count": asset_count,
        "phases": list(phases) if phases else [],
        "audiences_of_interest": list(audiences_of_interest) if audiences_of_interest else [],
        "image_url": image_url,
        "title_type": title_type,
        "terminology": terminology,
        "enabled_tabs": enabled_tabs,
        "brand_config": brand_config,
        "updated_at": now_iso,
    }

    existing_idx = None
    for i, t in enumerate(titles):
        if isinstance(t, dict) and t.get("title_slug") == title_slug:
            existing_idx = i
            break

    if existing_idx is not None:
        merged = dict(titles[existing_idx])
        merged.update({k: v for k, v in new_entry.items() if v is not None})
        titles[existing_idx] = merged
        added = False
    else:
        new_entry["created_at"] = now_iso
        titles.append(new_entry)
        added = True

    registry["updated_at"] = now_iso
    registry["title_count"] = len(titles)
    s3.put_object(
        Bucket=BUCKET, Key=REGISTRY_KEY,
        Body=json.dumps(registry, indent=2).encode("utf-8"),
        ContentType="application/json",
        CacheControl="no-cache, max-age=0",
    )

    quick_select_added = False
    try:
        qs_resp = s3.get_object(Bucket=BUCKET, Key=QUICK_SELECTS_KEY)
        qs = json.loads(qs_resp["Body"].read().decode("utf-8"))
    except Exception:
        qs = {}
    intent_titles = qs.setdefault("intent_titles", {})
    if title_slug not in intent_titles:
        intent_titles[title_slug] = True
        quick_select_added = True
        qs["updated_at"] = now_iso
        s3.put_object(
            Bucket=BUCKET, Key=QUICK_SELECTS_KEY,
            Body=json.dumps(qs, indent=2).encode("utf-8"),
            ContentType="application/json",
            CacheControl="no-cache, max-age=0",
        )

    return {
        "title_slug": title_slug,
        "registry_added": added,
        "registry_updated": not added,
        "quick_select_added": quick_select_added,
        "total_titles": len(titles),
    }


__all__ = ["register_intent_title"]
