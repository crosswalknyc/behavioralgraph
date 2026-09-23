#!/usr/bin/env python3
"""Purge every user-visible 'ticketing' / 'ticketer' string from the
Attribution IQ stack. Standing rule (Jenna 2026-09-23, verbatim):
    "I dont want to ever say bought a ticket, we dont want to model
     ticketing. we just want to say traffic that went to the site and
     got to the checkoutpage or something"

Concrete outcome:
    - Stage 3 nest label becomes 'Lower funnel: Reached the cart/purchase page within Nd'
    - Fork / archetype / leak descriptions swap 'ticketer' vocabulary for
      cart-page / conversion-site vocabulary
    - Data files (Hades -23, GOAT -23, GOAT -22 as source of truth for
      future regenerations) get the matching swaps in every string cell

Internal variable / dict-key names (3_ticketer, ticketer_partition,
paid_pct_ticketer, etc.) STAY. Rule from no-modeled-or-source-language:
implementation-facing identifiers are not user-visible; only surfaces
that render to a client are scrubbed. Same reason 4_paid stays as a
stage id even though the surface label is now 'Reached the checkout
page'.
"""
from pathlib import Path
import boto3, json, re, sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parents[1]  # bg-webapp/
MTA  = HERE / "mta_iq.py"
BACKUP_DIR = Path("/tmp")

# =====================================================================
# 1. mta_iq.py splices (each anchor MUST match exactly once)
# =====================================================================

SPLICES = [
    # ---- 1a. Stage 3 nest label for film kind ------------------------
    (
        '            "3_ticketer": f"Lower funnel: Ticketing-site visit within {window_days}d",',
        '            "3_ticketer": f"Lower funnel: Reached the cart/purchase page within {window_days}d",',
        "nest[3] label - film kind",
    ),
    # ---- 1b. Fork-question label for stage 3, film -------------------
    (
        '            "3_ticketer": "Hit more than one ticketer (deal-hunt)",',
        '            "3_ticketer": "Hit more than one cart/purchase surface (deal-hunt)",',
        "fork stage 3 - film kind",
    ),
    # ---- 1c. Archetype descriptions - film ---------------------------
    (
        '            {"archetype": "Straight-through",\n'
        '             "description": "Exposed to ticketer to paid, no research or retarget"},',
        '            {"archetype": "Straight-through",\n'
        '             "description": "Exposed to cart page to checkout, no research or retarget"},',
        "archetype Straight-through description",
    ),
    (
        '            {"archetype": "Researched",\n'
        '             "description": "Exposed to info-seek to ticketer to paid"},',
        '            {"archetype": "Researched",\n'
        '             "description": "Exposed to info-seek to cart page to checkout"},',
        "archetype Researched description",
    ),
    (
        '            {"archetype": "Deal-hunt",\n'
        '             "description": "Touched multiple ticketers before paying"},',
        '            {"archetype": "Deal-hunt",\n'
        '             "description": "Touched multiple cart/purchase surfaces before reaching checkout"},',
        "archetype Deal-hunt description",
    ),
    # ---- 1d. Leak descriptions ---------------------------------------
    (
        '                "Searched the title within 7d but never hit a ticketer",',
        '                "Searched the title within 7d but never reached a cart/purchase page",',
        "leak search-only description",
    ),
    (
        '                "Ticketer visit no ticket",',
        '                "Cart-page visit no checkout",',
        "leak ticketer-visit-no-ticket label",
    ),
    (
        '                "Opened a ticketer within 7d but never reached the checkout page, the bag-abandon equivalent",',
        '                "Reached a cart/purchase page within 7d but never reached the checkout page, the bag-abandon equivalent",',
        "leak bag-abandon description",
    ),
    # ---- 1e. leak_competing_note (identical on all 4 film cards) -----
    #        This anchor appears 4 times (2 goat + 1 dhar_mann + 1 default
    #        film card block), so we use replace_all-style bulk swap.
    #        Handled below in the bulk_replace pass.
    # ---- 1f. Default bottom_funnel_label -----------------------------
    (
        '    bottom_funnel_label = term.get("bottom_funnel_label") or "Ticketing"',
        '    bottom_funnel_label = term.get("bottom_funnel_label") or "Cart / checkout"',
        "default bottom_funnel_label",
    ),
    # ---- 1g. Docstring on _nest_stage_labels -------------------------
    (
        "    conversion noun, 'Ticketing-site visit' vs 'Website or app visit',",
        "    conversion noun, 'Cart/purchase page reach' vs 'Website or app visit',",
        "docstring _nest_stage_labels",
    ),
]

# Multi-occurrence swaps (still user-visible; anchor appears >1x by design)
BULK_REPLACE = [
    (
        # leak_competing_note appears verbatim on every film card
        '"Opened a ticketer within 7d and reached the checkout page for a "',
        '"Reached a cart/purchase page within 7d and reached the checkout page for a "',
        "leak_competing_note x N film cards",
    ),
]

def splice_mta():
    src = MTA.read_text(encoding="utf-8")
    backup = BACKUP_DIR / "mta_iq.py.pre_purge_ticketing.py"
    backup.write_text(src, encoding="utf-8")
    print(f"[backup] {backup}")
    for old, new, desc in SPLICES:
        n = src.count(old)
        if n != 1:
            print(f"  [FAIL] {desc}: anchor found {n} times, expected 1")
            print(f"         anchor first 80 chars: {old[:80]!r}")
            sys.exit(2)
        src = src.replace(old, new, 1)
        print(f"  [ok]   {desc}")
    for old, new, desc in BULK_REPLACE:
        n = src.count(old)
        if n == 0:
            print(f"  [skip] {desc}: 0 occurrences")
            continue
        src = src.replace(old, new)
        print(f"  [ok]   {desc}: swapped {n} occurrences")
    MTA.write_text(src, encoding="utf-8")
    # sanity: file still imports
    import subprocess
    r = subprocess.run([sys.executable, "-c", "import ast, sys; ast.parse(open('bg-webapp/mta_iq.py').read())"],
                        capture_output=True, text=True,
                        cwd=str(HERE.parent))
    if r.returncode != 0:
        print("[FAIL] mta_iq.py did not parse after edits")
        print(r.stderr)
        sys.exit(3)
    print("[ok] mta_iq.py still parses")

# =====================================================================
# 2. S3 data file sweep (Hades -23, GOAT -23, GOAT -22)
# =====================================================================

DATA_KEYS = [
    "intent/the_influencer_project_hades/mta/coefficients_2026-09-23.json",
    "intent/the_influencer_project_hades/mta/coefficients_2026-09-22.json",
    "intent/goat/mta/coefficients_2026-09-23.json",
    "intent/goat/mta/coefficients_2026-09-22.json",
]

# Order matters (longest first, so containing phrases swap before their
# substrings; e.g. 'Ticketing-site visit' before 'ticketing').
DATA_REPLACEMENTS = [
    # Full nest labels
    ("Lower funnel: Ticketing-site visit within 7d",
     "Lower funnel: Reached the cart/purchase page within 7d"),
    ("Lower funnel: Ticketing-site visit within 14d",
     "Lower funnel: Reached the cart/purchase page within 14d"),
    ("Ticketing-site visit within 7d",
     "Reached the cart/purchase page within 7d"),
    ("Ticketing-site visit within 14d",
     "Reached the cart/purchase page within 14d"),
    ("Ticketing-site visit", "Cart/purchase page reach"),
    ("Ticketing site visit",  "Cart/purchase page reach"),
    # Leak descriptions
    ("Opened a ticketer within 7d and reached the checkout page for a ",
     "Reached a cart/purchase page within 7d and reached the checkout page for a "),
    ("Opened a ticketer within 7d but never reached the checkout page, the bag-abandon equivalent",
     "Reached a cart/purchase page within 7d but never reached the checkout page, the bag-abandon equivalent"),
    ("Ticketer visit no ticket", "Cart-page visit no checkout"),
    # Fork / archetype text
    ("Hit more than one ticketer (deal-hunt)",
     "Hit more than one cart/purchase surface (deal-hunt)"),
    ("Touched multiple ticketers before paying",
     "Touched multiple cart/purchase surfaces before reaching checkout"),
    ("Exposed to ticketer to paid, no research or retarget",
     "Exposed to cart page to checkout, no research or retarget"),
    ("Exposed to info-seek to ticketer to paid",
     "Exposed to info-seek to cart page to checkout"),
    ("Searched the title within 7d but never hit a ticketer",
     "Searched the title within 7d but never reached a cart/purchase page"),
    # Anything else generically referring to "ticketing" as a user surface
    ("ticketing surface", "cart/purchase surface"),
    ("ticketing surfaces", "cart/purchase surfaces"),
]

def sweep_string(s: str) -> str:
    for old, new in DATA_REPLACEMENTS:
        if old in s:
            s = s.replace(old, new)
    return s

def sweep_value(v):
    if isinstance(v, str):
        return sweep_string(v)
    if isinstance(v, list):
        return [sweep_value(x) for x in v]
    if isinstance(v, dict):
        return {k: sweep_value(x) for k, x in v.items()}
    return v

def sweep_s3():
    s3 = boto3.client("s3")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    for key in DATA_KEYS:
        try:
            obj = s3.get_object(Bucket="dashboard-inputs", Key=key)
        except s3.exceptions.NoSuchKey:
            print(f"  [skip] {key} (no such key)")
            continue
        d = json.loads(obj["Body"].read())
        # backup
        parts = key.rsplit("/", 1)
        bk = f"{parts[0]}/_backups/{parts[1]}.pre_purge_ticketing_{stamp}.json"
        s3.copy_object(Bucket="dashboard-inputs", Key=bk,
                        CopySource={"Bucket": "dashboard-inputs", "Key": key})
        d2 = sweep_value(d)
        # Marker so the _save_cache defensive guard preserves this relabel
        d2.setdefault("_relabels", []).append({
            "when_utc": datetime.now(timezone.utc).isoformat(),
            "reason": "Standing rule (Jenna 2026-09-23): purge every user-visible 'ticketing' / 'ticketer' string. Stage 3 now reads as cart/purchase page reach; stage 4 as checkout page reach.",
        })
        body = json.dumps(d2, default=str).encode("utf-8")
        s3.put_object(Bucket="dashboard-inputs", Key=key, Body=body,
                       ContentType="application/json")
        # verify
        d3 = json.loads(s3.get_object(Bucket="dashboard-inputs", Key=key)["Body"].read())
        nest = ((d3.get("overall") or {}).get("paths") or {}).get("nest") or []
        n3 = nest[3]["label"] if len(nest) > 3 else "?"
        n4 = nest[4]["label"] if len(nest) > 4 else "?"
        print(f"  [ok] {key}")
        print(f"       nest[3]: {n3}")
        print(f"       nest[4]: {n4}")
        print(f"       backup: {bk}")

if __name__ == "__main__":
    print("== 1. mta_iq.py splices ==")
    splice_mta()
    print()
    print("== 2. S3 data-file sweep ==")
    sweep_s3()
    print()
    print("done.")
