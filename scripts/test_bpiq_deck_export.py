#!/usr/bin/env python3
"""Brand Partnership IQ: the Download as a deck option (Jenna 2026-10-29).

The broadsheet shell parks the tab's original Export menu (CSV / PDF /
Deck) in its hidden attic; on 2026-10-23 only CSV came back on the tab
row, so the deck had no visible home. These checks hold the restored
option and the route behind it:

  [1] the page's export row (the open-partnership tab row) reads CSV,
      PDF, Deck: Download as a deck and PDF wired to the existing
      exports, each with its wait state on the pressed button; the PDF
      capture shows every broadsheet section; the menu entries stay
  [2] the route in app.py delegates to migration/bpiq_deck_export
  [3] the route answers a .pptx for a run document in the NEW shape
      (campaign, windows, corrected_at, talent share) and in the OLD
      shape (yearly breakdown, no campaign); 400 with no data; the
      sidecar category reaches the builder (automotive drops the
      conversion slide)
  [4] the builder never raises on a missing, null or non-numeric
      optional field
  [5] the output has no door, no em dash, no internal vocabulary, and
      the design pack's finish reports READY (fonts embedded, no lint
      FAIL) when the pack is on this machine
  [6] the cover photo (2026-10-29): a `/api/profile-image-file/<key>`
      image is read from the bucket (relative or on the dashboard's
      origin) and lands on the cover, webp or AVIF converted to JPEG,
      cropped to the slot, geometry unchanged; an http URL is fetched
      with a browser User-Agent and a 404 or a refused connection lands
      the plate; nothing ever raises; the route hands the sidecar's
      image and the app's client to the builder
  [7] layout (2026-10-29): the Jennifer Beals x Lululemon run put five
      collisions on the exported deck (cover subtitle under the DRAFT
      stamp, a 14-character total wrapping in its tile, the rate-sources
      note on the note below it, an eight-row AGE column on the source
      line, two four-line methodology paragraphs on each other). Every
      block is now measured and fitted; `check_layout` proves on the
      finished presentation that no two text frames' rendered extents
      meet and no frame holds less than its text. The check itself is
      proven on a deck built to collide; then the live run's document
      shape, a long-text variant, both fixture shapes and the odd
      documents all come back with zero findings, the figures intact.

Hermetic: no S3, no network, no app import. Run:
    python3 scripts/test_bpiq_deck_export.py
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

FAIL = 0


def check(label, cond, detail=""):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}" + (f"  ({str(detail)[:240]})" if detail else ""))


# --------------------------------------------------------------- fixtures
def _demo(*rows):
    return [{"value": v, "count": c, "percentage": p} for v, c, p in rows]


def _demographics():
    block = {
        "gender": _demo(("Female", 44392, 72.1489), ("Male", 14661, 23.8281),
                        ("Prefer Not to Say", 973, 1.5759), ("Non-binary", 1521, 2.4471)),
        "age": _demo(("18-24", 6123, 9.9512), ("25-34", 18231, 29.6287), ("35-44", 17044, 27.6996),
                     ("45-54", 11270, 18.3157), ("55-64", 5807, 9.4373), ("65+", 3052, 4.9675)),
        "ethnicity": _demo(("White", 36211, 58.8537), ("Hispanic or Latino", 9734, 15.8206),
                           ("Black or African American", 7903, 12.8447), ("Asian", 5621, 9.1356),
                           ("Other", 2058, 3.3454)),
        "income": _demo(("Under $50,000", 12033, 19.5568), ("$50,000 to $99,999", 21874, 35.5513),
                        ("$100,000 to $149,999", 14212, 23.0985), ("$150,000+", 13408, 21.7934)),
    }
    return {"pre": json.loads(json.dumps(block)), "post": block}


def _per_platform():
    return [
        {"platform": "TikTok", "pre_users": 14314, "post_users": 25288, "lift_pct_users": 76.67,
         "pre_users_projected": 472218, "post_users_projected": 834249, "pre_pen_pct": 31.85, "post_pen_pct": 52.91},
        {"platform": "Instagram", "pre_users": 32411, "post_users": 47941, "lift_pct_users": 47.92,
         "pre_users_projected": 1069236, "post_users_projected": 1581569, "pre_pen_pct": 52.67, "post_pen_pct": 77.91},
        {"platform": "YouTube", "pre_users": 9031, "post_users": 12877, "lift_pct_users": 42.59,
         "pre_users_projected": 297923, "post_users_projected": 424811, "pre_pen_pct": 14.68, "post_pen_pct": 20.93},
    ]


def _properties(prefix):
    return [{"common_name": f"{prefix}/collection", "hits": 297418, "hits_projected": 9811794},
            {"common_name": f"{prefix}/press", "hits": 120431, "hits_projected": 3973121},
            {"common_name": f"{prefix}/social", "hits": 88217, "hits_projected": 2910473}]


def _sentiment():
    return {"enabled": True, "sample_size": 1411, "used_llm": False,
            "pre": {"positive": 611, "neutral": 702, "negative": 98},
            "post": {"positive": 823, "neutral": 511, "negative": 77},
            "pre_net_score": 36.4, "post_net_score": 52.9, "net_shift": 16.5,
            "top_positive": [], "top_negative": []}


def new_shape_doc():
    """The 2026-10-28 run document: one run per ask, campaign research
    anchors the price (campaign, windows, corrected_at, talent share)."""
    return {
        "project_name": "Jane Example x Northwind Apparel",
        "brand_partner": "Northwind Apparel",
        "campaign_name": "Jane Example x Northwind Apparel",
        "qualifier_type": "talent",
        "qualifier_value": ["Jane Example"],
        "start_date": "2026-09-23", "end_date": "2026-10-23",
        "attribution_window_days": 30,
        "pre_period": {"start": "2026-08-24", "end": "2026-09-22", "days": 30},
        "event_period": {"start": "2026-09-23", "end": "2026-10-23", "days": 31},
        "post_period": {"start": "2026-10-24", "end": "2026-10-28", "days": 5},
        "pre_period_days": 30, "post_period_days": 5,
        "windows": {"matched_pre_window": True,
                    "baseline_period": {"start": "2026-08-24", "end": "2026-09-22", "days": 30}},
        "audience_size": 61537, "projected_audience_size": 2030121,
        "totals": {"pre_hits": 240119, "post_hits": 611287, "pre_users": 31207, "post_users": 48811,
                   "pre_users_projected": 1029511, "post_users_projected": 1610307,
                   "lift_pct_hits": 154.6, "lift_pct_users": 56.4,
                   "pre_hits_per_day": 8003.9, "post_hits_per_day": 16980.2,
                   "audience_pen_pre_pct": 50.71, "audience_pen_post_pct": 79.32},
        "control_group": {"enabled": True, "control_size": 61537, "projected_control_size": 2030121,
                          "control_pre_users": 31144, "control_post_users": 32011,
                          "control_pre_users_projected": 1027433, "control_post_users_projected": 1056033,
                          "treat_pre_pen_pct": 50.71, "treat_post_pen_pct": 79.32,
                          "control_pre_pen_pct": 50.61, "control_post_pen_pct": 52.02,
                          "treat_delta_pp": 28.61, "control_delta_pp": 1.41,
                          "incremental_lift_pp": 27.2, "incremental_lift_rel_pct": 1929.1,
                          "control_lift_pct_users": 2.78, "background_drift_pp": 1.41,
                          "match": {"method": "size and pre-period engagement", "exposed_size": 61537,
                                    "control_size": 61537, "exposed_pre_rate_pct": 50.71,
                                    "control_pre_rate_pct": 50.61, "pre_rate_rel_diff_pct": 0.2,
                                    "band_rel_pct": 5.0, "size_matched": True, "pre_rate_matched": True,
                                    "note": "matched on size and pre-period brand engagement"}},
        "conversions": {"pre_hits": 2211, "post_hits": 14211, "pre_users": 1102, "post_users": 6733,
                        "pre_users_projected": 36355, "post_users_projected": 222119,
                        "low_signal": False, "lift_pct_hits": 542.7, "lift_pct_users": 511.0,
                        "enabled": True, "sell_out_lift_rel": 4.11, "basis": "orders in the window",
                        "units_us": 19873, "revenue_usd": 3132869.38, "first_48h_share": 0.62,
                        "note": "Most of the collection sold out inside the window."},
        "valuation": {"total_brand_value": 9021337.51, "brand_engagement_value": 891223.0,
                      "earned_media_value": 6730112.11, "brand_lift_value": 564325.4,
                      "conversion_value": 835677.0, "incremental_users": 552041,
                      "rates": {"bev_per_user": 1.5, "blv_per_incr_user": 1.02, "conv_value_per_user": 3.76,
                                "emv_per_user": {"TikTok": 9.0, "Instagram": 11.0, "YouTube": 7.0}},
                      "rate_sources": {"conv_value_per_user": "average order value in the window"},
                      "aov_basis": "a typical order carried about 1.5 pieces",
                      "emv_breakdown": [
                          {"platform": "Instagram", "post_users_projected": 1581569, "pre_users_projected": 1069236,
                           "incremental_users_projected": 492971, "emv_per_user_rate": 11.0, "emv_value": 5422681.0},
                          {"platform": "TikTok", "post_users_projected": 834249, "pre_users_projected": 472218,
                           "incremental_users_projected": 362031, "emv_per_user_rate": 9.0, "emv_value": 3258279.0}],
                      "methodology": "matched comparison group",
                      "talent_share": 0.61, "talent_share_basis": "the talent's own channels carried most of the reach",
                      "attributable_to_talent": 5503015.9, "attributable_to_partnership": 9021337.51,
                      "comparison_note": "the comparison group moved 1.4 points over the same days"},
        "campaign": {"name": "Jane Example x Northwind Apparel", "drop_dates": ["2026-09-23"],
                     "sold_at": ["northwindapparel.com"], "price_band": {"low_usd": 70.0, "high_usd": 280.0},
                     "collection_size": 186, "sold_out": True, "days_to_sell_out": 1.0,
                     "partial_sell_out": False, "restock_announced": False, "inventory_signal": "small",
                     "talent_role": "collaborator", "talent_channels": ["Instagram", "TikTok"],
                     "first_48h_share_of_purchases": 0.62, "aov_usd": 236.14, "aov_basis": "1.5 pieces an order",
                     "read_line": "The collection ran $70 to $280 a piece. Most of it sold out in a day.",
                     "reach_line": "The campaign reached 2,030,121 people in the window.",
                     "drop_line": "19,873 pieces moved in the US in the window.",
                     "audience_basis": "people who met the campaign", "talent_audience_in_window": 1211003,
                     "reach": {"reach_us": 2030121, "gross_people": 2611223, "overlap": 0.22, "lines": [],
                               "site_people": 611230, "press_people": 403112, "social_people": 1211003,
                               "commercial_people": 0, "event_people": 20118, "search_people": 365760,
                               "views_total": 41211003, "press_pieces": 44, "events": 1},
                     "drop": {"skus": 186, "units_per_sku": 231.1, "us_share_of_run": 0.587, "units_us": 19873,
                              "orders": 13267, "piece_price_usd": 157.64, "per_order_usd": 236.14,
                              "revenue_usd": 3132869.38, "sell_through": 0.814, "inventory_signal": "small",
                              "basis": "most of the run sold out inside the window"}},
        "demographics": _demographics(),
        "per_platform": _per_platform(),
        "top_brand_properties": _properties("shop.northwindapparel.com"),
        "top_brand_properties_pre": _properties("northwindapparel.com"),
        "sentiment": _sentiment(),
        "diagnostics": {"audience_filter_used": "talent", "brand_search_variants": ["northwind"],
                        "qualifier_search_variants": ["jane example"],
                        "data_provenance": "synth run via the synth engine pipeline",
                        "synthesis_note": "Claude reasoned the hostmap gap; modeled from the pipeline",
                        "post_window_clipped_to_today": True, "run_key": "jane-example|northwind-apparel|2026-09-23"},
        "created_at": "2026-10-29T03:17:43.991217Z", "corrected_at": "2026-10-29T04:56:45Z",
        "created_by": "tester",
    }


def old_shape_doc():
    """A 2026-06 run document: no campaign block, a yearly breakdown,
    the boost marker, the older diagnostics keys."""
    years = []
    for i, yr in enumerate((2023, 2024, 2025)):
        years.append({"year": yr, "label": str(yr), "start": f"{yr}-01-01", "end": f"{yr}-12-31", "days": 365,
                      "users_raw": 12011 + i * 3107, "users_projected": 396211 + i * 102503,
                      "audience_raw": 61537, "audience_projected": 2030121,
                      "penetration_pct": 19.5 + i * 5.1, "brand_hits_raw": 88213 + i * 20111,
                      "brand_hits_projected": 2910473 + i * 663421, "conversions_users": 0,
                      "conversions_users_projected": 0, "earned_media_value": 1211003.0 + i * 402117.0,
                      "momentum": "up" if i else "flat", "top_brand_properties": _properties("dodge.com"),
                      "per_platform": _per_platform(),
                      "valuation": {"total_brand_value": 2011003.0 + i * 511002.0, "earned_media_value": 1211003.0 + i * 402117.0,
                                    "brand_engagement_value": 400111.0, "brand_lift_value": 399889.0, "conversion_value": 0.0},
                      "totals": {"pre_users": 12011, "post_users": 15118, "pre_users_projected": 396211, "post_users_projected": 498714},
                      "audience_size": 61537, "projected_audience_size": 2030121,
                      "top_brand_properties_pre": _properties("dodge.com")})
    return {
        "_bpiq_boost_applied": True,
        "project_name": "Sample Driver x Dodge Endorsement",
        "brand_partner": "Dodge",
        "qualifier_type": "talent",
        "qualifier_value": ["Sample Driver"],
        "start_date": "2023-01-01", "end_date": "2025-12-31",
        "attribution_window_days": 90,
        "pre_period": {"start": "2022-10-03", "end": "2022-12-31", "days": 90},
        "event_period": {"start": "2023-01-01", "end": "2025-12-31", "days": 1096},
        "post_period": {"start": "2026-01-01", "end": "2026-03-31", "days": 90},
        "pre_period_days": 90, "post_period_days": 90,
        "audience_size": 61537, "projected_audience_size": 2030121,
        "totals": {"pre_hits": 140119, "post_hits": 311287, "pre_users": 21207, "post_users": 38811,
                   "pre_users_projected": 699511, "post_users_projected": 1280307,
                   "lift_pct_hits": 122.1, "lift_pct_users": 83.0,
                   "audience_pen_pre_pct": 34.46, "audience_pen_post_pct": 63.07},
        "control_group": {"enabled": True, "control_size": 61537, "projected_control_size": 2030121,
                          "treat_pre_pen_pct": 34.46, "treat_post_pen_pct": 63.07,
                          "control_pre_pen_pct": 34.11, "control_post_pen_pct": 36.02,
                          "treat_delta_pp": 28.61, "control_delta_pp": 1.91,
                          "incremental_lift_pp": 26.7, "incremental_lift_rel_pct": 77.5,
                          "control_lift_pct_users": 5.6, "background_drift_pp": 1.91},
        "conversions": {"enabled": False, "low_signal": True, "pre_users_projected": 0, "post_users_projected": 0,
                        "note": "Conversions suppressed: automotive purchases are not observable via web panel data."},
        "valuation": {"total_brand_value": 6011003.0, "brand_engagement_value": 1200111.0,
                      "earned_media_value": 3811003.0, "brand_lift_value": 999889.0, "conversion_value": 0.0,
                      "incremental_users": 542041,
                      "rates": {"bev_per_user": 1.5, "blv_per_incr_user": 1.02, "conv_value_per_user": 0.0,
                                "emv_per_user": {"TikTok": 9.0, "Instagram": 11.0, "YouTube": 7.0}},
                      "emv_breakdown": [
                          {"platform": "Instagram", "post_users_projected": 1081569, "pre_users_projected": 669236,
                           "incremental_users_projected": 412333, "emv_per_user_rate": 11.0, "emv_value": 4535663.0}]},
        "demographics": _demographics(),
        "per_platform": _per_platform(),
        "top_brand_properties": _properties("dodge.com"),
        "top_brand_properties_pre": _properties("dodge.com"),
        "sentiment": _sentiment(),
        "yearly_breakdown": years,
        "diagnostics": {"synthetic": True, "generated_for": "demo", "generated_at": "2026-06-25T15:00:00Z",
                        "yearly_cuts": True, "conversions_disabled": True},
        "created_at": "2026-06-25T15:00:00Z", "created_by": "tester",
    }


def beals_lululemon_doc():
    """The live Jennifer Beals x Lululemon run (2026-10-29) in the shape
    the dashboard POSTs: fourteen platforms in the earned-media breakdown
    (press and the launch event among them), an eight-bucket AGE column,
    a six-bucket GENDER column, the talent share, the matched-group
    account, the campaign's own lines. This is the document that put
    five collisions on the exported deck."""
    def emv(platform, post, pre, incr, rate, value, **extra):
        row = {"platform": platform, "post_users_projected": post, "pre_users_projected": pre,
               "incremental_users_projected": incr, "emv_per_user_rate": rate, "emv_value": value}
        row.update(extra)
        return row

    return {
        "project_name": "Jennifer Beals x Lululemon",
        "brand_partner": "Lululemon",
        "campaign_name": "Ganni x Lululemon",
        "qualifier_type": "other",
        "qualifier_value": ["Jennifer Beals"],
        "start_date": "2026-10-15", "end_date": "2026-10-28",
        "attribution_window_days": 15,
        "pre_period": {"start": "2026-10-01", "end": "2026-10-14", "days": 14},
        "event_period": {"start": "2026-10-15", "end": "2026-10-28", "days": 14},
        "post_period": {"start": "2026-10-15", "end": "2026-10-29", "days": 15},
        "pre_period_days": 14, "post_period_days": 15,
        "windows": {"matched_pre_window": True,
                    "baseline_period": {"start": "2025-10-15", "end": "2026-10-14", "days": 365}},
        "audience_size": 108937, "projected_audience_size": 3593822,
        "totals": {"pre_hits": 122306, "post_hits": 190743, "pre_users": 43371, "post_users": 61529,
                   "pre_users_projected": 1430805, "post_users_projected": 2029836,
                   "lift_pct_hits": 55.96, "lift_pct_users": 41.87,
                   "pre_hits_per_day": 8736.14, "post_hits_per_day": 13624.5,
                   "audience_pen_pre_pct": 39.81, "audience_pen_post_pct": 56.48},
        "control_group": {"enabled": True, "control_size": 108937, "projected_control_size": 3593822,
                          "control_pre_users": 41791, "control_post_users": 42471,
                          "control_pre_users_projected": 1378681, "control_post_users_projected": 1401115,
                          "treat_pre_pen_pct": 39.81, "treat_post_pen_pct": 56.48,
                          "control_pre_pen_pct": 38.36, "control_post_pen_pct": 38.99,
                          "treat_delta_pp": 16.67, "control_delta_pp": 0.63,
                          "incremental_lift_pp": 16.04, "incremental_lift_rel_pct": 2546.03,
                          "control_lift_pct_users": 1.63, "background_drift_pp": 0.6238,
                          "match": {"method": "difference_in_differences", "exposed_size": 108937,
                                    "control_size": 108937, "exposed_pre_rate_pct": 39.8129,
                                    "control_pre_rate_pct": 38.3625, "pre_rate_rel_diff_pct": 3.64,
                                    "band_rel_pct": 5.0, "size_matched": True, "pre_rate_matched": True,
                                    "note": "Compared against a matched group of the same size with the "
                                            "same brand engagement before the partnership."}},
        "conversions": {"pre_hits": 38, "post_hits": 563, "pre_users": 29, "post_users": 402,
                        "pre_users_projected": 957, "post_users_projected": 13267, "low_signal": False,
                        "lift_pct_hits": 0.0, "lift_pct_users": 1286.21, "enabled": True,
                        "sell_out_lift_rel": 0.1979, "basis": "the drop's orders", "units_us": 19873,
                        "revenue_usd": 3132869.38, "first_48h_share": 0.6219,
                        "note": "The collection sold out in a day, so most purchases landed in the first "
                                "48 hours; later traffic in the window is waitlist and restock intent. The "
                                "run was small, so the sell-out reflects limited inventory as much as demand."},
        "valuation": {"total_brand_value": 33409966.88, "brand_engagement_value": 5074590.0,
                      "earned_media_value": 22319522.5, "brand_lift_value": 2882985.0,
                      "conversion_value": 3132869.38, "incremental_users": 576597,
                      "rates": {"bev_per_user": 2.5, "blv_per_incr_user": 5.0, "conv_value_per_user": 236.14,
                                "emv_per_user": {"TikTok": 7.0, "Instagram": 11.0, "YouTube": 3.5, "Facebook": 14.0,
                                                 "X (Twitter)": 6.0, "Reddit": 4.0, "Pinterest": 6.0, "Snapchat": 5.0,
                                                 "LinkedIn": 25.0, "Direct (Brand Site)": 5.0, "Threads": 10.0,
                                                 "Twitch": 15.0, "Press": 4.0, "Launch event": 5.0}},
                      "rate_sources": {"conv_value_per_user": "Average order value $236.14: the collection ran "
                                                              "$70 to $280 a piece and a typical order carried "
                                                              "about 1.5 pieces."},
                      "aov_basis": "researched_prices",
                      "emv_breakdown": [
                          emv("Instagram", 1581569, 1069236, 492971, 11.0, 5422681.0),
                          emv("Launch event", 626476, 0, 626476, 5.0, 3132380.0, earned_direct=True),
                          emv("Direct (Brand Site)", 1469503, 857045, 589312, 5.0, 2946560.0),
                          emv("Press", 647929, 0, 647929, 4.0, 2591716.0, earned_direct=True),
                          emv("TikTok", 834249, 472218, 348349, 7.0, 2438443.0),
                          emv("Facebook", 595501, 477727, 113323, 14.0, 1586522.0),
                          emv("YouTube", 1018926, 654421, 350729, 3.5, 1227551.5),
                          emv("LinkedIn", 150467, 101642, 46986, 25.0, 1174650.0),
                          emv("Pinterest", 341973, 228389, 109291, 6.0, 655746.0),
                          emv("X (Twitter)", 225024, 151589, 70665, 6.0, 423990.0),
                          emv("Threads", 116322, 73732, 40987, 10.0, 409870.0),
                          emv("Reddit", 188578, 119687, 66287, 4.0, 265148.0),
                          emv("Snapchat", 74326, 67202, 6855, 5.0, 34275.0),
                          emv("Twitch", 8841, 8149, 666, 15.0, 9990.0)],
                      "methodology": "matched comparison group",
                      "talent_share": 0.208,
                      "talent_share_basis": "65% of the campaign's exposure carried the talent; those assets drew "
                                            "42% more response than the brand's alone; the matched group kept 96% "
                                            "of the lift",
                      "attributable_to_talent": 6949273.11, "attributable_to_partnership": 4145261.43,
                      "comparison_note": "Compared against a matched group of the same size with the same brand "
                                         "engagement before the partnership."},
        "campaign": {"name": "GANNI x lululemon, Joy Is a Power Move",
                     "drop_dates": ["2026-10-08", "2026-10-14", "2026-10-15"],
                     "price_band": {"low_usd": 70.0, "high_usd": 280.0}, "collection_size": 22,
                     "sold_out": True, "days_to_sell_out": 0.1, "partial_sell_out": True,
                     "aov_usd": 236.14, "aov_basis": "researched_prices",
                     "read_line": "The collection ran $70 to $280 a piece. Most of the collection sold out in a "
                                  "day. The run was small, so the sell-out reflects limited inventory as much as "
                                  "demand. A typical order carried about 1.5 pieces, $236.14 per order.",
                     "reach_line": "The campaign reached about 3.6M people in the window: 637K on the collection "
                                   "pages, 648K through press, 3.3M through the film and the launch posts, 626K "
                                   "around the launch event, counted once each.",
                     "drop_line": "About 19,873 pieces sold in the US, 13,267 orders, $3,132,869 at the "
                                  "collection's prices. Most of the run sold out inside the window.",
                     "audience_basis": "campaign reach", "talent_audience_in_window": 490001},
        "demographics": {"post": {
            "gender": _demo(("Female", 44392, 72.1489), ("Male", 14661, 23.8281), ("Prefer Not to Say", 973, 1.5759),
                            ("Trans Male", 142, 0.2301), ("Trans Female", 326, 0.5298), ("Non-Binary", 1038, 1.6872)),
            "age": _demo(("17 and Under", 1012, 1.6452), ("18-24", 7835, 12.7334), ("25-34", 16252, 26.4143),
                         ("35-44", 14752, 23.9759), ("45-54", 11475, 18.6505), ("55-64", 7005, 11.3855),
                         ("65 or Older", 2994, 4.8661), ("Other", 202, 0.3291)),
            "ethnicity": _demo(("White", 35586, 57.8366), ("Hispanic or Latino", 9612, 15.6191),
                               ("Black or African American", 7297, 11.8481), ("Asian", 7057, 11.4686),
                               ("Another Race/Ethnicity", 1986, 3.2276)),
            "income": _demo(("Less than $25,000", 3575, 5.8104), ("$25,000 - $49,999", 6729, 10.9359),
                            ("$50,000 - $74,999", 9398, 15.2744), ("$75,000 - $99,999", 10488, 17.0461),
                            ("$100,000 - $149,999", 14662, 23.8259), ("$150,000 - $249,999", 11237, 18.2632),
                            ("$250,000 or More", 5442, 8.8441))}},
        "per_platform": _per_platform(),
        "top_brand_properties": [
            {"common_name": "shop.lululemon.com/ganni-x-lululemon", "hits": 297418, "hits_projected": 9811794},
            {"common_name": "@lululemon Instagram co-authored launch reel", "hits": 183472, "hits_projected": 6052725},
            {"common_name": "lululemon.com women-new-arrivals", "hits": 126794, "hits_projected": 4182923},
            {"common_name": "lululemon YouTube joy-is-a-power-move-film", "hits": 112346, "hits_projected": 3706285},
            {"common_name": "lululemon TikTok campaign posts", "hits": 94271, "hits_projected": 3109992},
            {"common_name": "shop.lululemon.com ruffle-trim-textured-jacket", "hits": 73629, "hits_projected": 2429014},
            {"common_name": "lululemon app drop alert and cart", "hits": 69138, "hits_projected": 2280857},
            {"common_name": "shop.lululemon.com define-jacket-ganni", "hits": 61837, "hits_projected": 2039997}],
        "top_brand_properties_pre": [
            {"common_name": "lululemon.com women-new-arrivals", "hits": 88341, "hits_projected": 2914362},
            {"common_name": "@lululemon Instagram co-authored launch reel", "hits": 64729, "hits_projected": 2135404},
            {"common_name": "shop.lululemon.com/ganni-x-lululemon", "hits": 41263, "hits_projected": 1361263},
            {"common_name": "lululemon YouTube joy-is-a-power-move-film", "hits": 23817, "hits_projected": 785721}],
        "sentiment": _sentiment(),
        "diagnostics": {"audience_filter_used": "qualifier_only", "post_window_clipped_to_today": True,
                        "run_key": "jennifer-beals|lululemon|2026-10-15"},
        "created_at": "2026-10-29T03:17:43.991217Z", "corrected_at": "2026-10-29T04:56:45Z",
        "created_by": "tester",
    }


def long_text_doc():
    """The Beals run with every run-to-run line stretched well past what
    any slide was drawn for: a long partnership name, a long brand name,
    campaign lines three times as long, a sixteen-platform roster each
    with its own long rate source, ten AGE buckets with long labels, long
    touchpoint names, a long matched-group account, a study context. The
    deck has to hold all of it without a collision."""
    doc = beals_lululemon_doc()
    doc["project_name"] = "Alexandra Montgomery-Whitfield x The Northwind Outdoor Apparel Company"
    doc["brand_partner"] = "The Northwind Outdoor Apparel Company"
    doc["qualifier_value"] = ["Alexandra Montgomery-Whitfield"]
    filler = (" The press run opened with the long-lead titles and closed with the regional dailies, "
              "and every placement named the collection, the talent and the drop date in its first "
              "paragraph, which is why the search line rose on the same days as the film.")
    camp = doc["campaign"]
    camp["read_line"] = camp["read_line"] + filler + filler
    camp["reach_line"] = camp["reach_line"] + filler + filler
    camp["drop_line"] = camp["drop_line"] + filler + filler
    doc["conversions"]["note"] = doc["conversions"]["note"] + filler + filler
    val = doc["valuation"]
    val["talent_share_basis"] = (val["talent_share_basis"] + "; the talent's own posts outdrew the brand's on "
                                 "every platform where both posted; the launch-night coverage carried her name "
                                 "in nine of every ten pieces; the matched group's movement was flat across the "
                                 "same days")
    val["rate_sources"]["bev_per_user"] = ("Rate: the open-market value of one engaged consumer at the 2026 "
                                           "benchmark for premium apparel, set against the brand's own paid "
                                           "social cost per engaged user over the same quarter." + filler)
    val["rate_sources"]["blv_per_incr_user"] = val["rate_sources"]["bev_per_user"]
    val["rate_sources"]["conv_value_per_user"] = val["rate_sources"]["conv_value_per_user"] + filler
    long_src = ("Open-market benchmark for the platform's premium apparel inventory at typical exposure "
                "frequency, restated per engaged consumer for the window")
    val["rate_sources"]["emv_per_user"] = {p: f"{long_src} ({p})" for p in val["rates"]["emv_per_user"]}
    val["rates"]["emv_per_user"].update({"Podcast": 9.0, "Newsletter": 3.0})
    val["emv_breakdown"] += [
        {"platform": "Podcast", "post_users_projected": 221003, "pre_users_projected": 110221,
         "incremental_users_projected": 110782, "emv_per_user_rate": 9.0, "emv_value": 997038.0},
        {"platform": "Newsletter", "post_users_projected": 98021, "pre_users_projected": 61003,
         "incremental_users_projected": 37018, "emv_per_user_rate": 3.0, "emv_value": 111054.0}]
    doc["control_group"]["match"]["note"] = doc["control_group"]["match"]["note"] + filler
    doc["demographics"]["post"]["age"] = _demo(
        ("17 and Under", 1012, 1.6452), ("18-20", 2835, 4.6), ("21-24", 5000, 8.1334), ("25-29", 8252, 13.4143),
        ("30-34", 8000, 13.0), ("35-44", 14752, 23.9759), ("45-54", 11475, 18.6505), ("55-64", 7005, 11.3855),
        ("65 to 74 Years Old", 1994, 3.2661), ("75 or Older, Including Retirees", 1000, 1.6))
    doc["demographics"]["post"]["ethnicity"] = _demo(
        ("White", 35586, 57.8366), ("Hispanic or Latino", 9612, 15.6191),
        ("Black or African American", 7297, 11.8481), ("Asian", 7057, 11.4686),
        ("Native Hawaiian or Other Pacific Islander", 986, 1.6), ("American Indian or Alaska Native", 1000, 1.6276))
    doc["demographics"]["source_note"] = ("Source: Crosswalk BehaviorGraph. This is the audience that met the "
                                          "campaign across the collection pages, the press, the film and the "
                                          "launch posts, not the brand's engager base, and every column is the "
                                          "post window." + filler)
    doc["top_brand_properties"] = [
        {"common_name": f"shop.northwindoutdoorapparel.com/the-alexandra-montgomery-whitfield-capsule-collection-{i}",
         "hits": 297418 - i * 20000, "hits_projected": 9811794 - i * 700000} for i in range(7)]
    doc["top_brand_properties_pre"] = [
        {"common_name": doc["top_brand_properties"][0]["common_name"], "hits": 41263, "hits_projected": 1361263}]
    doc["diagnostics"]["study_context"] = ("Rerun of the first-flight read with the post window closed on the "
                                           "final day of the attribution window." + filler)
    return doc


# ------------------------------------------------------------- deck text
BANNED = ("synth", "modeled", "pipeline", "claude", "hostmap", "hetzner", "estimated")


def slide_texts(pptx_bytes):
    from pptx import Presentation
    prs = Presentation(io.BytesIO(pptx_bytes))
    out = []
    for s in prs.slides:
        for sh in s.shapes:
            if sh.has_text_frame:
                out.append(sh.text_frame.text)
    return len(prs.slides), "\n".join(out)


def deck_is_clean(label, pptx_bytes, min_slides=10):
    n, blob = slide_texts(pptx_bytes)
    low = blob.lower()
    check(f"{label}: a real deck ({n} slides)", n >= min_slides, n)
    check(f"{label}: no slide fell back to the failure note", "Slide build failed" not in blob)
    check(f"{label}: no em dash or en dash", "\u2014" not in blob and "\u2013" not in blob)
    check(f"{label}: no door", not re.search(r"\bdoors?\b", low))
    hits = [w for w in BANNED if re.search(r"\b" + w + r"\b", low)]
    check(f"{label}: no internal vocabulary", not hits, hits)
    return n


# ------------------------------------------------------------------ [1]
def test_page_carries_the_deck_option():
    print("[1] the export row on the Brand Partnership tab")
    html = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
    i = html.index("function _bpRenderTabs()")
    j = html.index("function loadBpiqProfiles()", i)
    row = html[i:j]
    check("the tab row renders the CSV button", 'data-bp-csv="1"' in row)
    check("the tab row renders Download as a deck", 'data-bp-deck="1"' in row and ">Download as a deck<" in row)
    check("the tab row renders PDF", 'data-bp-pdf="1"' in row and ">PDF<" in row)
    check("the row reads CSV, PDF, Deck, then Close all tabs",
          row.index('data-bp-csv="1"') < row.index('data-bp-pdf="1"') < row.index('data-bp-deck="1"')
          < row.index('data-bp-close-all="1"'))
    check("the deck button is wired to the export",
          "closest('[data-bp-deck]')" in row and "_bpiqExportDeck();" in row)
    check("the PDF button is wired to the print-to-PDF export",
          "closest('[data-bp-pdf]')" in row and "_bpiqExportPdf();" in row)
    check("the CSV button still reaches its export", "_bpiqExportCsv();" in row)
    check("the deck and PDF buttons share the style rule",
          "#ecommerceIQView .bp-tab-deck, #ecommerceIQView .bp-tab-pdf {" in html)
    p = html.index("function _bpiqExportPdf()")
    pdf_fn = html[p:html.index("// Deck-with-Analysis export", p)]
    check("the PDF export keeps its capture-and-print path",
          "window.html2canvas(el" in pdf_fn and "w.print()" in pdf_fn)
    check("the PDF wait state shows on the tab row button",
          "querySelector('#bpTabsBar [data-bp-pdf]')" in pdf_fn and "pdfBusy(true)" in pdf_fn
          and "pdfBusy(false)" in pdf_fn)
    check("the PDF capture shows every broadsheet section and puts the open one back",
          "bpShowAll();" in pdf_fn and pdf_fn.count("bpRestore();") >= 2)
    check("the menu's PDF entry is still there", 'onclick="_bpiqExportPdf()"' in html)
    check("the menu entry behind the attic is still there",
          'id="bpiqDeckBtn" onclick="_bpiqExportDeck()"' in html)
    k = html.index("function _bpiqExportDeck()")
    fn = html[k:html.index("// ----- View-by-Year slicer -----", k)]
    check("the export POSTs the live run to the deck route",
          "fetch('/api/brand-partnership-iq/export-deck'" in fn)
    check("the wait state shows on the tab row button",
          "querySelector('#bpTabsBar [data-bp-deck]')" in fn and "rowBtn.disabled = true" in fn)
    check("the tab row button is restored when the deck lands",
          "rowNow.disabled = false" in fn and "origRowText" in fn)
    for tok in ("\u2014", "synth", "pipeline"):
        check(f"no {tok!r} in the page copy that was added",
              tok not in row.lower() and tok not in fn.lower() and tok not in pdf_fn.lower())


# ------------------------------------------------------------------ [2]
def test_route_delegates():
    print("[2] the route in app.py")
    src = (WEB / "app.py").read_text(encoding="utf-8")
    i = src.index("@app.route('/api/brand-partnership-iq/export-deck', methods=['POST'])")
    body = src[i:i + 2500]
    check("the route is authenticated", "@requires_auth" in body.split("def export_brand_partnership_iq_deck")[0])
    check("the route delegates to the shared adapter",
          "from migration.bpiq_deck_export import deck_response" in body and "return deck_response(" in body)
    check("the sidecar lookup rides along", "load_bpiq_metadata()" in body and "s3_prefix=BRAND_PARTNERSHIP_IQ_S3_PREFIX" in body)


# ------------------------------------------------------------------ [3]
def _client(meta_for_key=None, prefix="brand-partnership-iq/"):
    import flask
    from migration.bpiq_deck_export import deck_response
    app = flask.Flask("bpiq-deck-test")
    app.secret_key = "x"

    @app.route("/api/brand-partnership-iq/export-deck", methods=["POST"])
    def _export():
        return deck_response(flask.request.get_json(force=True, silent=True) or {},
                             meta_for_key=meta_for_key, s3_prefix=prefix)

    return app.test_client()


def test_route_answers_both_shapes():
    print("[3] the deck route, new shape and old shape")
    out = {}
    try:
        import flask  # noqa: F401
    except Exception:
        # The repo convention when this interpreter has no Flask: the route
        # checks skip, the decks themselves are still built and read.
        print("  skip  flask is not installed in this interpreter; the route is not mounted here")
        from migration.bpiq_deck_builder import build_deck
        for label, doc in (("new shape", new_shape_doc()), ("old shape", old_shape_doc())):
            data = build_deck(doc, image_url=None, category=None)
            check(f"{label}: zip bytes", data[:2] == b"PK", data[:8])
            out[label] = (data, deck_is_clean(label, data))
        return out
    c = _client()
    for label, doc in (("new shape", new_shape_doc()), ("old shape", old_shape_doc())):
        r = c.post("/api/brand-partnership-iq/export-deck", json={"data": doc, "s3_key": "", "image_url": "", "category": ""})
        check(f"{label}: 200", r.status_code == 200,
              "" if r.status_code == 200 else r.get_data(as_text=True)[:200])
        check(f"{label}: a pptx attachment",
              r.mimetype == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
              and ".pptx" in (r.headers.get("Content-Disposition") or ""), r.headers.get("Content-Disposition"))
        data = r.get_data()
        check(f"{label}: zip bytes", data[:2] == b"PK", data[:8])
        out[label] = (data, deck_is_clean(label, data))
    r = c.post("/api/brand-partnership-iq/export-deck", json={})
    check("no data: 400 with a plain error", r.status_code == 400 and r.get_json().get("error") == "data payload required")
    r = c.post("/api/brand-partnership-iq/export-deck", data="not json", content_type="text/plain")
    check("a body that is not JSON: 400, not a crash", r.status_code == 400)

    # the sidecar: the bare key is looked up, its category reaches the builder
    seen = []

    def meta(bare):
        seen.append(bare)
        return {"category": "automotive", "image_url": ""}

    c2 = _client(meta_for_key=meta)
    doc = new_shape_doc()
    r = c2.post("/api/brand-partnership-iq/export-deck",
                json={"data": doc, "s3_key": "brand-partnership-iq/Jane_Example_x_Northwind_Apparel_10_29_2026_04_17.json"})
    check("sidecar: the bare key is what gets looked up",
          seen == ["Jane_Example_x_Northwind_Apparel_10_29_2026_04_17.json"], seen)
    check("sidecar: 200", r.status_code == 200)
    n_auto, blob_auto = slide_texts(r.get_data())
    n_full = out.get("new shape", (b"", 0))[1]
    check("sidecar: the automotive category drops the conversion slide", n_full and n_auto == n_full - 1, (n_full, n_auto))

    # a sidecar that raises never blocks the deck
    c3 = _client(meta_for_key=lambda bare: (_ for _ in ()).throw(RuntimeError("sidecar down")))
    r = c3.post("/api/brand-partnership-iq/export-deck", json={"data": new_shape_doc(), "s3_key": "brand-partnership-iq/x.json"})
    check("a failing sidecar still ships the deck", r.status_code == 200 and r.get_data()[:2] == b"PK")
    return out


# ------------------------------------------------------------------ [4]
def test_builder_never_raises():
    print("[4] the builder on thin and odd run documents")
    from migration.bpiq_deck_builder import build_deck, suggested_filename
    cases = {
        "empty document": {},
        "name only": {"project_name": "Only A Name", "brand_partner": "Brand"},
        "nulls everywhere": {"project_name": "Nulls", "totals": None, "valuation": None, "control_group": None,
                             "conversions": None, "demographics": None, "per_platform": None,
                             "top_brand_properties": None, "sentiment": None, "yearly_breakdown": None,
                             "campaign": None, "windows": None, "diagnostics": None},
        "wrong types": {"project_name": 12345, "brand_partner": ["list"], "totals": "n/a", "valuation": [1, 2],
                        "control_group": "none", "conversions": {"enabled": True, "post_users_projected": "lots"},
                        "audience_size": "1,234", "projected_audience_size": "2.5M people",
                        "attribution_window_days": None, "created_at": 20261029,
                        "start_date": 2026, "end_date": None, "yearly_breakdown": "2024"},
        "text where numbers go": {"project_name": "Text Numbers", "brand_partner": "Brand",
                                  "totals": {"audience_pen_pre_pct": "12.5%", "audience_pen_post_pct": "n/a",
                                             "pre_users": "", "post_users": None},
                                  "valuation": {"total_brand_value": "$1,200", "incremental_users": "many"},
                                  "control_group": {"incremental_lift_pp": "3.1", "treat_pre_pen_pct": "zero",
                                                    "incremental_lift_rel_pct": "n/a"}},
    }
    for label, doc in cases.items():
        try:
            data = build_deck(doc, image_url=None, category=None)
            n, _blob = slide_texts(data)
            check(f"{label}: a deck comes back ({n} slides)", data[:2] == b"PK" and n >= 1)
            fname = suggested_filename(doc)
            check(f"{label}: a filename comes back", fname.endswith(".pptx") and " " not in fname, fname)
        except Exception as e:  # noqa: BLE001
            check(f"{label}: never raises", False, repr(e))
    try:
        data = build_deck(None, image_url=None, category=None)  # type: ignore[arg-type]
        check("a null document: a deck comes back", data[:2] == b"PK")
    except Exception as e:  # noqa: BLE001
        check("a null document: never raises", False, repr(e))


# ------------------------------------------------------------------ [5]
def test_finish_ready(decks):
    print("[5] the design pack's finish on the route's output")
    try:
        from migration import design_skill as ds
    except Exception as e:  # noqa: BLE001
        check("design_skill imports", False, repr(e))
        return
    if not ds.available():
        print("  skip  the crosswalk-design pack is not on this machine; READY not checked")
        return
    try:
        import fontTools  # noqa: F401
    except Exception:
        print("  skip  fonttools is not installed here; READY not checked")
        return
    for label, (data, _n) in decks.items():
        _out, rep = ds.gate_attachment("deck.pptx", data)
        steps = {n: ok for n, ok, _o in rep.steps}
        check(f"{label}: fonts embedded", steps.get("embed", False), rep.summary)
        check(f"{label}: brand lint has no FAIL", steps.get("brand_lint", True), rep.summary)
        check(f"{label}: READY", rep.ready, rep.summary)


# ------------------------------------------------------------------ [6]
PHOTO_RGB = (230, 120, 20)      # a flat orange no plate could average to
PHOTO_KEY = "brand-partnership-iq-images/287ec4820840449cbd2ad02d03fba175.webp"


class FakeS3:
    """get_object over a dict of key -> bytes; a missing key raises like boto3."""

    def __init__(self, objects):
        self.objects = dict(objects)
        self.asked = []

    def get_object(self, Bucket, Key):
        self.asked.append((Bucket, Key))
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(self.objects[Key]), "ContentType": "image/webp"}


def _photo_bytes(fmt):
    """A 900x700 flat photo in `fmt` (WEBP / PNG / JPEG / AVIF), or None
    when this Pillow cannot write that format."""
    from PIL import Image
    im = Image.new("RGB", (900, 700), PHOTO_RGB)
    buf = io.BytesIO()
    try:
        im.save(buf, fmt)
    except Exception:
        return None
    return buf.getvalue()


def _cover_photo(pptx_bytes):
    """The hero picture on slide 1 (the 7.6in slot at 5.8in), decoded."""
    from pptx import Presentation
    from PIL import Image
    prs = Presentation(io.BytesIO(pptx_bytes))
    for sh in prs.slides[0].shapes:
        if sh.shape_type == 13 and abs(sh.left / 914400 - 5.8) < 0.05:
            return Image.open(io.BytesIO(sh.image.blob)).convert("RGB"), sh
    return None, None


def _mean_rgb(im):
    small = im.resize((40, 40))
    px = list(small.getdata())
    return tuple(sum(c[i] for c in px) / len(px) for i in range(3))


def _is_photo(im):
    m = _mean_rgb(im)
    return all(abs(m[i] - PHOTO_RGB[i]) < 30 for i in range(3))


def test_cover_photo():
    print("[6] the cover photo")
    from migration import bpiq_deck_builder as B
    webp = _photo_bytes("WEBP")
    if webp is None:
        print("  skip  this Pillow cannot write webp; the cover photo checks use PNG instead")
        webp = _photo_bytes("PNG")
    doc = new_shape_doc()
    slot = (7.6 / 7.5)

    # 1. the admin upload path: relative proxy path, read from the bucket
    s3 = FakeS3({PHOTO_KEY: webp})
    data = B.build_deck(doc, image_url="/api/profile-image-file/" + PHOTO_KEY, category=None, s3_client=s3)
    im, pic = _cover_photo(data)
    check("proxy path: the bucket is read for the bare key", s3.asked == [("dashboard-inputs", PHOTO_KEY)], s3.asked)
    check("proxy path: the photo lands on the cover", im is not None and _is_photo(im), im and _mean_rgb(im))
    check("proxy path: a webp arrives as a JPEG python-pptx places",
          pic is not None and pic.image.content_type == "image/jpeg", pic and pic.image.content_type)
    check("proxy path: cropped to the cover slot's aspect",
          im is not None and abs(im.width / im.height - slot) < 0.02, im and im.size)
    check("proxy path: the slot geometry is unchanged (5.8in, 7.6 x 7.5)",
          pic is not None and abs(pic.width / 914400 - 7.6) < 0.01 and abs(pic.height / 914400 - 7.5) < 0.01
          and pic.top == 0)
    n, blob = slide_texts(data)
    check("proxy path: the rest of the deck is untouched", n >= 12 and "Slide build failed" not in blob)

    # 2. the same path written as an absolute URL on the dashboard's origin
    s3b = FakeS3({PHOTO_KEY: webp})
    data = B.build_deck(doc, image_url="https://dashboard.crosswalknyc.com/api/profile-image-file/" + PHOTO_KEY,
                        s3_client=s3b)
    im, _ = _cover_photo(data)
    check("absolute proxy URL: read from the bucket, photo on the cover",
          s3b.asked == [("dashboard-inputs", PHOTO_KEY)] and im is not None and _is_photo(im))

    # 3. the bucket has no such object: the plate, never a raise
    s3c = FakeS3({})
    data = B.build_deck(doc, image_url="/api/profile-image-file/brand-partnership-iq-images/missing.webp", s3_client=s3c)
    im, _ = _cover_photo(data)
    check("missing object: the plate stands in", im is not None and not _is_photo(im))

    # 4. http URLs: a 404 lands the plate; a 200 webp converts; a browser User-Agent is sent
    calls = []

    class _Resp:
        def __init__(self, status, content):
            self.status_code, self.content = status, content

    def fake_get(url, **kw):
        calls.append((url, kw.get("headers") or {}))
        if url.endswith("/gone.jpg"):
            return _Resp(404, b"")
        if url.endswith("/photo.webp"):
            return _Resp(200, webp)
        raise RuntimeError("connection refused")

    real_get = B.requests.get
    B.requests.get = fake_get
    s3h = FakeS3({})
    try:
        data = B.build_deck(doc, image_url="https://example.com/press/gone.jpg", s3_client=s3h)
        im, _ = _cover_photo(data)
        check("http 404: the plate stands in", im is not None and not _is_photo(im))
        data = B.build_deck(doc, image_url="https://example.com/press/photo.webp", s3_client=s3h)
        im, pic = _cover_photo(data)
        check("http 200 webp: the photo lands, converted to JPEG",
              im is not None and _is_photo(im) and pic.image.content_type == "image/jpeg")
        data = B.build_deck(doc, image_url="https://example.com/press/refused.png", s3_client=s3h)
        im, _ = _cover_photo(data)
        check("http connection failure: the plate stands in, no raise", im is not None and not _is_photo(im))
        uas = [h.get("User-Agent", "") for _u, h in calls]
        check("http fetches carry a browser User-Agent",
              len(uas) == 3 and all(ua.startswith("Mozilla/5.0") for ua in uas), uas[:1])
        check("http URLs never touch the bucket", not s3h.asked, s3h.asked)
    finally:
        B.requests.get = real_get

    # 5. an AVIF saved with a .jpg name (the Jennifer Beals photo) decodes when Pillow can
    avif = _photo_bytes("AVIF")
    if avif is None:
        print("  skip  this Pillow has no AVIF codec (wheels carry it from 11.3.0); the deploy floor is Pillow>=11.3.0")
        req = (WEB / "requirements.txt").read_text(encoding="utf-8")
        check("requirements pin a Pillow that decodes AVIF", "Pillow>=11.3.0" in req)
    else:
        key = "profile-images/53153082d3fb4d268aea58617230091d.jpg"
        s3d = FakeS3({key: avif})
        data = B.build_deck(doc, image_url="/api/profile-image-file/" + key, s3_client=s3d)
        im, _ = _cover_photo(data)
        check("avif inside a .jpg: the photo lands on the cover", im is not None and _is_photo(im), im and _mean_rgb(im))

    # 6. bytes no decoder reads: the plate, never a raise
    s3e = FakeS3({PHOTO_KEY: b"not an image at all, just text" * 20})
    data = B.build_deck(doc, image_url="/api/profile-image-file/" + PHOTO_KEY, s3_client=s3e)
    im, _ = _cover_photo(data)
    check("undecodable bytes: the plate stands in", im is not None and not _is_photo(im))

    # 7. the route hands the sidecar's image_url and the app's client to the builder
    try:
        import flask  # noqa: F401
    except Exception:
        print("  skip  flask is not installed in this interpreter; the route plumbing is not mounted here")
        return
    s3f = FakeS3({PHOTO_KEY: webp})
    import flask as _fl
    from migration.bpiq_deck_export import deck_response
    app = _fl.Flask("bpiq-cover-test")

    @app.route("/x", methods=["POST"])
    def _x():
        return deck_response(_fl.request.get_json(force=True, silent=True) or {},
                             meta_for_key=lambda bare: {"image_url": "/api/profile-image-file/" + PHOTO_KEY},
                             s3_prefix="brand-partnership-iq/", s3_client=s3f)

    r = app.test_client().post("/x", json={"data": new_shape_doc(), "s3_key": "brand-partnership-iq/run.json"})
    im, _ = _cover_photo(r.get_data())
    check("route: the sidecar photo reaches the cover through the app's client",
          r.status_code == 200 and im is not None and _is_photo(im) and s3f.asked == [("dashboard-inputs", PHOTO_KEY)])


# ------------------------------------------------------------------ [7]
def _colliding_deck():
    """A presentation built to fail the check: two text frames whose text
    lands on the same spot, and a frame whose text needs far more height
    than its box. Proves the check sees collisions before it is trusted
    to report none."""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])

    def box(left, top, width, height, text, size):
        tb = s.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        r = tf.paragraphs[0].add_run()
        r.text, r.font.size = text, Pt(size)
        return tb

    box(0.6, 1.0, 6.0, 0.5, "The first line of a claim that sits here.", 24)
    box(0.6, 1.1, 6.0, 0.5, "A second claim written on top of the first one.", 24)
    box(0.6, 4.0, 3.0, 0.3, " ".join(["overflow"] * 60), 12)
    return prs


def test_layout_fits():
    print("[7] layout: nothing collides, nothing overflows")
    from pptx import Presentation
    from migration import bpiq_deck_builder as B

    # the check sees a collision and an overflow when they are there
    found = B.check_layout(_colliding_deck())
    check("the check reports two frames whose text lands on the same spot",
          any("collides" in f for f in found), found)
    check("the check reports a frame whose text needs more height than its box",
          any("more than its box" in f for f in found), found)

    # the fit helpers do what the slides lean on
    sz = B._fit_stat("$33,409,966.88", 3.5, 54, floor=20)
    m = B._measure("$33,409,966.88", sz, 3.5, bold=True)
    check(f"a 14-character total holds one line in a 3.5in tile ({sz}pt)",
          m.lines == 1 and m.max_w_in <= 3.5, (sz, m))
    check("a short stat keeps the tile's display size", B._fit_stat("3,593,822", 3.5, 54, floor=20) >= 40)
    long_note = ("First sentence of a note. Second sentence of the note. Third sentence of the note. "
                 "Fourth sentence of the note that is also long.")
    fsz, fsp, ftxt, fm = B._fit_body(long_note, 3.0, 0.45, size=10, floor=9, spacing=1.3, min_spacing=1.1)
    check("a note with no room steps down, tightens, then loses trailing sentences, never its first",
          fm.height_in <= 0.45 and ftxt.startswith("First sentence of a note.") and fsz >= 9, (fsz, fsp, ftxt))
    check("a note with room is left as written",
          B._fit_body(long_note, 12.0, 2.0, size=10, floor=9, spacing=1.3)[2] == long_note)

    # the live run, the long-text variant, both fixture shapes, the odd documents
    out = {}
    docs = {
        "Beals x Lululemon": beals_lululemon_doc(),
        "long text": long_text_doc(),
        "new shape": new_shape_doc(),
        "old shape": old_shape_doc(),
        "empty document": {},
        "name only": {"project_name": "Only A Name", "brand_partner": "Brand"},
    }
    for label, doc in docs.items():
        data = B.build_deck(doc, image_url=None, category=None)
        prs = Presentation(io.BytesIO(data))
        issues = B.check_layout(prs)
        check(f"{label}: zero layout findings on {len(prs.slides)} slides", not issues, issues[:3])
        n, blob = slide_texts(data)
        check(f"{label}: no slide fell back to the failure note", "Slide build failed" not in blob)
        out[label] = (data, n)
    _n, blob = slide_texts(out["Beals x Lululemon"][0])
    for fig in ("$33,409,966.88", "$22,319,522.50", "$5,074,590", "$2,882,985", "$3,132,869.38",
                "3,593,822", "$6,949,273.11", "$4,145,261.43"):
        check(f"Beals x Lululemon: {fig} is on the deck", fig in blob)
    check("Beals x Lululemon: the rate-sources note names every platform with a rate",
          all(p in blob for p in ("Instagram $11.00", "Launch event $5.00", "Twitch $15.00")))
    low = blob.lower()
    check("Beals x Lululemon: the eight AGE buckets are all on the deck",
          all(b in low for b in ("17 and under", "65 or older", "other", "18-24", "55-64")))
    _n, blob = slide_texts(out["long text"][0])
    check("long text: the total and the talent's share survive the fit",
          "$33,409,966.88" in blob and "$6,949,273.11" in blob)
    check("long text: a shortened account still opens with its first sentence",
          "About 19,873 pieces sold in the US" in blob and "The collection ran $70 to $280 a piece." in blob)
    check("long text: every one of the ten AGE buckets is on the deck",
          all(b in blob.lower() for b in ("18-20", "21-24", "25-29", "30-34", "65 to 74 years old",
                                          "75 or older, including retirees")))
    return {k: out[k] for k in ("Beals x Lululemon", "long text")}


if __name__ == "__main__":
    test_page_carries_the_deck_option()
    test_route_delegates()
    decks = test_route_answers_both_shapes()
    test_builder_never_raises()
    test_cover_photo()
    decks.update(test_layout_fits())
    test_finish_ready(decks)
    print(f"\n{'PASS' if not FAIL else f'{FAIL} FAIL'}: Brand Partnership IQ deck export")
    sys.exit(1 if FAIL else 0)
