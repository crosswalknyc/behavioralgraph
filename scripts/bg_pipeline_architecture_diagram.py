#!/usr/bin/env python3
"""
bg.py Pipeline – Architecture Diagram
Generates a PDF that shows how the Behavioral Graph pipeline (bg.py) works:
Snowflake → UID cohort → behavioral CTEs → DataFrame → normalization →
ESPN consolidation → sample size inflation → boosts → consistency → divisions → CSV.
Uses reportlab only (no system Graphviz required).
"""

import os
import sys

def main():
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except ImportError:
        print("Install reportlab: pip install reportlab")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(script_dir, "bg_pipeline_architecture.pdf")

    c = canvas.Canvas(out_path, pagesize=letter)
    w, h = letter
    margin = 0.5 * inch
    y = h - margin - 20

    def title(text, size=14):
        nonlocal y
        c.setFont("Helvetica-Bold", size)
        c.drawString(margin, y, text)
        y -= size + 4

    def section(label, bg_rgb=(0.93, 0.95, 1)):
        nonlocal y
        if y < margin + 60:
            c.showPage()
            y = h - margin - 30
        c.setFillColorRGB(*bg_rgb)
        c.rect(margin, y - 16, w - 2 * margin, 20, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin + 6, y - 12, label)
        c.setFont("Helvetica", 9)
        y -= 24

    def line(text, indent=0):
        nonlocal y
        if y < margin + 40:
            c.showPage()
            y = h - margin - 30
        c.drawString(margin + indent, y, text)
        y -= 13

    def bullet(lines_list, indent=12):
        for s in lines_list:
            line("• " + s, indent)

    # --- Page 1: Title + high-level flow ---
    title("bg.py – Behavioral Graph Pipeline Architecture", 16)
    c.setFont("Helvetica", 9)
    c.drawString(margin, y, "How the Profile IQ analysis pipeline works from Snowflake to CSV.")
    y -= 28

    section("1. Entry points")
    bullet([
        "Terminal: main() → connect_snowflake(); perform_full_universe_scan(conn, brands, s1, e1); run_full_pipeline(conn, ...).",
        "Dashboard: app.run_analysis() imports bg, calls bg.connect_snowflake(), then bg.run_full_pipeline(conn, project_name, brands, sample_start, sample_end, behavior_start, behavior_end, filters, skew_settings, is_genpop, purchasers_only, previous_file_path, brand_category).",
    ])
    line("")

    section("2. Snowflake connection & warehouse")
    bullet([
        "connect_snowflake(): Uses SNOWFLAKE_USER, SNOWFLAKE_TOKEN or SNOWFLAKE_PASSWORD, BEHAVIORGRAPH6X warehouse, BEHAVIORALGRAPH database, PUBLIC schema.",
        "run_full_pipeline starts by: USE WAREHOUSE BEHAVIORGRAPH6X; ALTER WAREHOUSE ... SET WAREHOUSE_SIZE = '6X-Large'; SET QUERY_ACCELERATION_MAX_SCALE_FACTOR = 25.",
    ])
    line("")

    section("3. Universe scan (optional, called before run_full_pipeline)")
    bullet([
        "perform_full_universe_scan(conn, brands, start_date, end_date, purchasers_only): Counts distinct UIDs in PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL matching brand + date range.",
        "Result stored on run_full_pipeline.universe_size; used for sample size inflation (65x down to 1x, cap 10M) and for dynamic sampling (e.g. 10%–50% based on universe size).",
    ])
    line("")

    section("4. UID cohort & clickstream sampling (inside run_full_pipeline)")
    bullet([
        "Two paths: (A) Fast path: streaming aggregation → MAPPED_EVENTS → TEMP_UIDS. (B) Default: brand_filter + optional purchasers_only → CLICKSTREAM_FINAL SAMPLE(...) → UIDs with visit count → TEMP_SAMPLED_UIDS.",
        "Date range and universe size drive sample_rate (e.g. 10%–50%) and max_uids (e.g. 10K–600K). Demographics filter can join USER_DATA_SANITIZED.",
        "Eligible UIDs: ELIGIBLE_UIDS = TEMP_SAMPLED_UIDS ⋈ CLICKSTREAM_FINAL (sample_start..sample_end, brand_filter), HAVING COUNT >= 2.",
    ])
    line("")

    section("5. Behavioral data from Snowflake (CTEs → pandas)")
    bullet([
        "PRE_SAMPLED_CLICKSTREAM: CLICKSTREAM_FINAL SAMPLE(clickstream_sample_pct) ⋈ ELIGIBLE_UIDS, DELIVERED in [behavior_start, behavior_end], limited per user (e.g. 75K visits/user).",
        "BEHAVIOR_EVENTS: PRE_SAMPLED_CLICKSTREAM ⋈ BEHAVIORALGRAPH.PUBLIC.HOST_MAPPING (COMMON_NAME → Brand, Category, Section, Most Purchased Categories).",
        "BEHAVIOR_WITH_DEMOS / BEHAVIORAL_SPLIT: Joins and splits HostSection, InterestRaw, MPC_TRIM into category columns.",
        "Final Snowflake result: large CTE query that aggregates by Column (category), Value (brand/interest), with demographics from USER_DATA_SANITIZED. Fetched into pandas DataFrame df.",
    ])
    line("")

    section("6. DataFrame normalization & ESPN Layer 1")
    bullet([
        "Columns normalized: normalize_category_name(Column), normalize_demo_value(Value).",
        "consolidate_espn_brands(df): Merge ESPN+ into ESPN; set all ESPN entries to the single highest value across categories; remove duplicate ESPN+ rows.",
        "Demographics and behavioral data split: df_demo vs df_behavior (everything not in demo fields or Sample Size).",
    ])
    line("")

    section("7. Sample size (intelligent inflation)")
    bullet([
        "get_final_sample_size(conn, is_genpop, ...): Uses run_full_pipeline.universe_size (from universe scan). Bounded to 10M cap.",
        "Inflation: try 65x, then 55x, 25x, 5x, 2.5x, 1x so inflated_sample_size ≤ 10M. final_sample_size = min(inflated, 10M). GenPop: fixed 10M.",
        "Raw numbers later derived from (percentage/100) × final_sample_size.",
    ])
    line("")

    section("8. Behavioral processing (noise, caps, merge)")
    bullet([
        "Behavior percentages: safe_float_convert, ±0.5 jitter, add_dirichlet_noise(alpha=0.05), add_gaussian_noise_to_behavior (per category renormalize to 100%).",
        "apply_category_caps: ensure_google_fiber_not_top_7, cap_specific_brands (BYD, Rivian, Centene, etc.), category-specific rules.",
        "Demographics: enforce_demographic_minimums; optional ensure_demographic_consistency with previous run. Merge df_demo + df_behavior + metadata rows (SAMPLE SIZE, BRAND INPUT, AVID FAN, CASUAL FAN, etc.) → df_final.",
    ])
    line("")

    section("9. Previous run & brand input 100%")
    bullet([
        "If previous_file_path: load_previous_run_data() → previous_demo_lookup, previous_behavioral_lookup. add_previous_run_column() adds comparison column.",
        "enforce_input_brand_100(df_final, brands): Set input brand rows to 100% in every category where they appear; set raw numbers to sample size. (Skipped for GenPop.)",
    ])
    line("")

    section("10. Boosts (order matters)")
    bullet([
        "boost_all_behavioral_by_2x: DISABLED (organic only).",
        "boost_sports_categories_by_436x: NFL/NBA/WNBA/MLB 40x then ÷4 later; NHL 4.36x then ÷4; other sports 4.36x. Cross-category: e.g. LA LAKERS gets same boost in all categories.",
        "boost_search_engine_ai_custom: Google 66x, top 4 @ 33x, others 5–11x. boost_streaming_platform_custom: Netflix 15x, Hulu 12x. divide_streaming_platform_except_netflix_espn: ÷2 except Netflix/ESPN.",
        "enforce_cross_category_brand_consistency: For each brand, set all category rows to the max percentage (and max raw) across categories.",
    ])
    line("")

    section("11. Final divisions & ESPN Layers 2–3")
    bullet([
        "divide_sports_categories_by_4: NFL/NBA/WNBA/NHL/MLB. enforce_sports_global_brand_consistency.",
        "enforce_espn_consistency_final: ESPN exact match only; set all ESPN % and raw to max across categories; update US Gen Pop.",
        "divide_espn_by_2_final: All ESPN values ÷2. enforce_input_brand_100 again (final 100% check). enforce_parental_status_sum_to_100.",
    ])
    line("")

    section("12. Output")
    bullet([
        "Column order: CATEGORY_ORDER (INPUT_METADATA, BRAND INPUT, SAMPLE SIZE, demographics, behavioral categories…). Sort by category then percentage descending.",
        "Rename Percentage → Category Share. Drop internal columns. Remove dash variants (remove_dash_variants_from_output).",
        "df_final.to_csv(final_file, index=False). final_file = ~/Desktop/Behavioral_Graph/{project_name}_{timestamp}.csv (or app overrides path when run from dashboard).",
    ])
    line("")

    section("13. Key data sources (Snowflake)")
    bullet([
        "PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL: UID, COMMON_NAME, DELIVERED, URL.",
        "BEHAVIORALGRAPH.PUBLIC.HOST_MAPPING: Brand, Category, Section, Most Purchased Categories (maps COMMON_NAME to behavioral categories).",
        "PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED: demographics (optional join for filtered UID sampling).",
    ])
    line("")

    section("14. S3 (optional)")
    bullet([
        "check_s3_for_existing_results(brand, start_date, end_date): Look up existing CSV in dashboard-inputs. upload_result_to_s3(file_path, brand_name): After save, upload to S3 (used by dashboard).",
    ])

    c.save()
    print(f"✅ PDF written: {out_path}")
    return out_path

if __name__ == "__main__":
    main()
