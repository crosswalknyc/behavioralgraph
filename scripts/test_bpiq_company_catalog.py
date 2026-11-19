#!/usr/bin/env python3
"""Company-wallet Brand Partnership portal: own company only.

Jenna 2026-11-19: each company wallet user sees valuations pulled by
their own company, nothing else. Crosswalk company sees every pull.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SKIP_LOAD_PRICING", "1")

import wallet  # noqa: E402

FAILS = []
LIB = "Willow_Smith_x_Free_People_10_05_2026_19_41"
KARTEL = "Mountain_Dew_x_Kartel_11_01_2026_12_04"
CAA = "Charli_XCX_x_Vinted_11_16_2026_16_19"
GRANTED = "Goody_s_x_NASCAR_09_01_2026_10_00"


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def users_doc():
    kartel = {
        "email": "eriley@kartel.com",
        "company": "Kartel",
        "billing_source": "company",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
        "role": "user",
    }
    kartel_teammate = {
        "email": "lpeterson@kartel.com",
        "company": "Kartel",
        "billing_source": "company",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
        "role": "user",
    }
    caa = {
        "email": "samantha.kolton@caa.com",
        "company": "CAA",
        "billing_source": "company",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
        "role": "user",
    }
    bent = {
        "email": "mike@bentpixels.com",
        "company": "Bent Pixels",
        "billing_source": "company",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": [GRANTED + ".json"],
        "role": "user",
    }
    nerali = {
        "email": "nerali@ilaren.co",
        "company": "Crosswalk",
        "billing_source": "user",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
        "role": "user",
    }
    liz = {
        "email": "liz@crosswalknyc.com",
        "company": "Crosswalk",
        "billing_source": "user",
        "role": "admin",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
    }
    paula = {
        "email": "paula@vazquezadvisory.com",
        "company": "Dick Wolf Productions",
        "billing_source": "user",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
        "role": "user",
    }
    adam = {
        "email": "adam@rabover.com",
        "billing_source": "user",
        "has_brand_partnership_iq_access": True,
        "brand_partnership_iq_journeys": "*",
        "role": "user",
    }
    return {
        "users": {
            "eriley": kartel,
            "lpeterson": kartel_teammate,
            "skolton": caa,
            "mike": bent,
            "nerali": nerali,
            "liz": liz,
            "paula": paula,
            "adam": adam,
            "admin": {
                "email": "jenna@crosswalknyc.com",
                "role": "super_admin",
                "company": "Crosswalk",
            },
        },
        "companies": {
            "Kartel": {"wallet_balance_usd": 3000, "paying_customer": True},
            "CAA": {"wallet_balance_usd": 0, "paying_customer": True},
            "Bent Pixels": {"wallet_balance_usd": 4000, "paying_customer": True},
            "Crosswalk": {"wallet_balance_usd": 0},
        },
    }


LIB_META = {}
KARTEL_META = {"company": "Kartel", "pulled_by": "eriley"}
CAA_META = {"company": "CAA", "pulled_by": "skolton"}
GRANT_META = {"company": "Crosswalk", "pulled_by": "jmenking"}
TEAMMATE_META = {"pulled_by": "lpeterson"}


def vis(doc, uname, jid, meta=None, payload=None):
    return wallet.bpiq_result_visible(
        doc["users"][uname],
        username=uname,
        journey_id=jid,
        meta=meta,
        users_data=doc,
        payload=payload,
    )


def main():
    doc = users_doc()

    check("crosswalk fold", wallet.is_crosswalk_company("Crosswalk Technologies"))
    check("kartel is not crosswalk", not wallet.is_crosswalk_company("Kartel"))
    check("nerali sees all catalog",
          wallet.sees_all_bpiq_catalog(doc["users"]["nerali"], "nerali"))
    check("liz staff sees all",
          wallet.sees_all_bpiq_catalog(doc["users"]["liz"], "liz"))
    check("kartel does not see all",
          not wallet.sees_all_bpiq_catalog(doc["users"]["eriley"], "eriley"))

    check("kartel star does not see library",
          not vis(doc, "eriley", LIB, LIB_META))
    check("kartel star sees own company pull",
          vis(doc, "eriley", KARTEL, KARTEL_META))
    check("kartel teammate sees eriley pull",
          vis(doc, "lpeterson", KARTEL, KARTEL_META))
    check("kartel sees teammate pull by pulled_by",
          vis(doc, "eriley", "Other_Kartel_Pull", TEAMMATE_META))
    check("kartel does not see CAA pull",
          not vis(doc, "eriley", CAA, CAA_META))
    check("caa does not see kartel pull",
          not vis(doc, "skolton", KARTEL, KARTEL_META))
    check("caa sees own pull",
          vis(doc, "skolton", CAA, CAA_META))

    check("crosswalk nerali sees library",
          vis(doc, "nerali", LIB, LIB_META))
    check("crosswalk nerali sees kartel pull",
          vis(doc, "nerali", KARTEL, KARTEL_META))
    check("liz sees CAA pull",
          vis(doc, "liz", CAA, CAA_META))
    check("super admin sees library",
          vis(doc, "admin", LIB, LIB_META))

    check("explicit grant still opens Crosswalk-built file",
          vis(doc, "mike", GRANTED, GRANT_META))
    check("explicit grant does not open the rest of the library",
          not vis(doc, "mike", LIB, LIB_META))

    check("paula personal does not see kartel",
          not vis(doc, "paula", KARTEL, KARTEL_META))
    check("paula personal does not see library via leftover star",
          not vis(doc, "paula", LIB, LIB_META))
    check("adam personal sees his own pull",
          vis(doc, "adam", "Adam_x_Brand", {"pulled_by": "adam"}))
    check("adam personal does not see library",
          not vis(doc, "adam", LIB, LIB_META))

    check("json suffix on grant matches",
          vis(doc, "mike", GRANTED + ".json", GRANT_META))
    check("payload created_by stands in for missing sidecar",
          vis(doc, "skolton", "Unstamped_CAA",
              meta={}, payload={"created_by": "skolton"}))

    stamp, wrote = wallet.record_bpiq_pull(
        doc, doc["users"]["eriley"], "eriley", KARTEL + ".json")
    check("record stamp names Kartel", stamp.get("company") == "Kartel",
          str(stamp))
    check("record wrote company list", wrote)
    rec = doc["companies"]["Kartel"]
    check("company journeys hold the id",
          KARTEL in wallet.bpiq_grant_ids(
              rec.get("brand_partnership_iq_journeys")))
    check("eriley leftover star is still star",
          doc["users"]["eriley"].get("brand_partnership_iq_journeys") == "*")

    bent_list = list(doc["users"]["mike"]["brand_partnership_iq_journeys"])
    wallet.record_bpiq_pull(
        doc, doc["users"]["mike"], "mike", "New_Bent_Pull")
    check("list-scoped seat gets the new id",
          "New_Bent_Pull" in wallet.bpiq_grant_ids(
              doc["users"]["mike"].get("brand_partnership_iq_journeys")))
    check("prior grant kept",
          GRANTED in wallet.bpiq_grant_ids(
              doc["users"]["mike"].get("brand_partnership_iq_journeys")))
    check("bent prior list grew",
          len(doc["users"]["mike"]["brand_partnership_iq_journeys"])
          == len(bent_list) + 1)

    check("scope kartel is company",
          wallet.bpiq_catalog_scope(
              doc["users"]["eriley"], doc, "eriley")[0] == "company")
    check("scope nerali is all",
          wallet.bpiq_catalog_scope(
              doc["users"]["nerali"], doc, "nerali")[0] == "all")
    check("scope adam is self",
          wallet.bpiq_catalog_scope(
              doc["users"]["adam"], doc, "adam")[0] == "self")

    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
