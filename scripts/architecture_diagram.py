#!/usr/bin/env python3
"""
Behavioral Graph Dashboard – Architecture Diagram
Generates a PDF with a visual architecture diagram and in-depth component description.
Uses reportlab only (no system Graphviz required).
"""

import os
import sys

# Letter page: 612 x 792 pt. Origin bottom-left; y increases upward.
def draw_diagram(c, w, h, margin):
    """Draw a one-page visual architecture diagram with clear flow and no overlap."""
    from reportlab.lib.units import inch

    # Layout constants (points)
    col1_x = margin + 8
    col2_x = w * 0.5 - 20
    col3_x = w - margin - 140
    box_w = 130
    box_h = 32
    v_gap = 28
    top_y = h - margin - 44

    def draw_box(x, y, width, height, label_lines, fill=(0.92, 0.95, 1.0), stroke=1):
        c.setFillColorRGB(*fill)
        c.setStrokeColorRGB(0.4, 0.5, 0.6)
        c.roundRect(x, y, width, height, 5, fill=1, stroke=stroke)
        c.setFillColorRGB(0.1, 0.1, 0.15)
        c.setFont("Helvetica", 8)
        ty = y + height - 8
        for line in label_lines:
            if ty < y + 6:
                break
            c.drawString(x + 5, ty, line[:26])
            ty -= 10

    def draw_arrow(x1, y1, x2, y2, label_text=""):
        c.setStrokeColorRGB(0.3, 0.4, 0.5)
        c.setFillColorRGB(0.2, 0.2, 0.3)
        c.line(x1, y1, x2, y2)
        if label_text:
            mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
            c.setFont("Helvetica", 7)
            c.drawString(mid_x + 4, mid_y + 2, label_text)

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.setFillColorRGB(0.1, 0.15, 0.3)
    c.drawString(margin, h - margin - 18, "Behavioral Graph Dashboard – System Architecture")
    c.setFont("Helvetica", 9)
    c.drawString(margin, h - margin - 34, "Crosswalk IQ+ (Profile IQ, Hedge Fund IQ, Subscriber IQ, Ticket Sales IQ, Netflix Ranker, Attribution)")

    # Row 1: User & entry
    y = top_y
    draw_box(col1_x, y, box_w, box_h, ["Browser / User", "Session, Credits"], fill=(0.85, 0.95, 0.9))
    draw_box(col2_x, y, box_w + 40, box_h, ["Flask app (app.py)", "Render · Gunicorn", "API, Auth, Job queue"], fill=(1.0, 0.95, 0.85))
    draw_arrow(col1_x + box_w + 4, y + box_h / 2, col2_x - 4, y + box_h / 2, "HTTPS")

    # Row 2: Workers
    y -= box_h + v_gap
    draw_box(col1_x, y, box_w, box_h, ["bg.py", "Profile IQ pipeline"], fill=(1.0, 0.92, 0.8))
    draw_box(col2_x + 60, y, box_w, box_h, ["Attribution scripts", "Ticket, SVOD, Talent"], fill=(1.0, 0.92, 0.8))
    draw_arrow(col2_x + box_w / 2 + 20, top_y - 8, col1_x + box_w / 2, y + box_h + 4, "run_analysis")
    draw_arrow(col2_x + box_w / 2 + 20, top_y - 8, col2_x + 60 + box_w / 2, y + box_h + 4, "run_*")

    # Row 3: S3
    y -= box_h + v_gap
    draw_box(col1_x, y, box_w, box_h, ["S3 dashboard-inputs", "profiles, metadata, jobs"], fill=(0.9, 0.95, 1.0))
    draw_box(col2_x, y, box_w + 40, box_h, ["S3 svod-acquisition", "ticket-sales-iq/tracker"], fill=(0.9, 0.95, 1.0))
    draw_box(col3_x, y, 120, box_h, ["S3 aggregated-", "tickers (Hedge Fund)"], fill=(0.9, 0.95, 1.0))
    draw_arrow(col1_x + box_w / 2, y + box_h + 4, col1_x + box_w / 2, y + box_h + v_gap - 4, "upload CSV")
    draw_arrow(col2_x + box_w / 2 + 20, top_y - box_h - v_gap - 8, col1_x + box_w / 2, y + box_h + 4, "")
    draw_arrow(col2_x + 60 + box_w / 2, y + box_h + 4, col2_x + box_w / 2 + 20, y + box_h + 4, "upload")

    # Row 4: ClickHouse
    y -= box_h + v_gap
    draw_box(col1_x, y, box_w, box_h, ["ClickHouse", "behavioralgraph"], fill=(0.85, 0.92, 1.0))
    draw_box(col2_x + 40, y, box_w, box_h, ["ClickHouse", "clickstream"], fill=(0.85, 0.92, 1.0))
    draw_arrow(col1_x + box_w / 2, y + box_h + 4, col1_x + box_w / 2, y + box_h + v_gap - 4, "query")
    draw_arrow(col1_x + box_w / 2, top_y - 2 * (box_h + v_gap) - 8, col1_x + box_w / 2, y + box_h + 4, "clickstream")
    draw_arrow(col2_x + 60 + box_w / 2, top_y - 2 * (box_h + v_gap) - 8, col2_x + 40 + box_w / 2, y + box_h + 4, "attribution")

    # Row 5: Ingestion & external
    y -= box_h + v_gap
    draw_box(col1_x, y, box_w, box_h, ["lordata → CSV", "send_to_snow → SF"], fill=(0.95, 0.9, 0.95))
    draw_box(col2_x + 40, y, box_w, box_h, ["OpenAI", "Gmail API"], fill=(0.95, 0.95, 0.95))
    draw_arrow(col1_x + box_w / 2, y + box_h + 4, col1_x + box_w / 2, y + box_h + v_gap - 4, "COPY INTO")
    draw_arrow(col2_x + box_w / 2 + 20, top_y - 8, col2_x + 40 + box_w / 2, y + box_h + 4, "AI / email")

    # Vertical flow labels
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.35, 0.4, 0.5)
    c.drawString(col1_x - 2, top_y - box_h - v_gap / 2 - 20, "App →")
    c.drawString(col3_x + 122, top_y - box_h - v_gap / 2 - 20, "← S3 read")


def add_section(c, margin, w, h, y_ref, title, body_lines, indent_pt=14):
    """Add a section; returns new y (bottom-up)."""
    from reportlab.lib.units import inch
    y = y_ref[0]
    if y < margin + 85:
        c.showPage()
        y = h - margin - 28
        y_ref[0] = y
    # Section header
    c.setFillColorRGB(0.25, 0.35, 0.5)
    c.rect(margin, y - 20, w - 2 * margin, 22, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin + 8, y - 15, title)
    y -= 28
    c.setFillColorRGB(0.1, 0.1, 0.15)
    c.setFont("Helvetica", 9)
    for line in body_lines:
        if y < margin + 60:
            c.showPage()
            y = h - margin - 24
        c.drawString(margin + indent_pt if line.startswith("  ") else margin, y, line.strip())
        y -= 13
    y -= 8
    y_ref[0] = y
    return y


def main():
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas
    except ImportError:
        print("Install reportlab: pip install reportlab")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(script_dir, "behavioral_graph_architecture.pdf")

    c = canvas.Canvas(out_path, pagesize=letter)
    w, h = letter
    margin = 0.55 * inch

    # ----- Page 1: Visual diagram only -----
    draw_diagram(c, w, h, margin)

    # ----- Page 2: High-level flow & components -----
    c.showPage()
    y_ref = [h - margin - 28]

    add_section(c, margin, w, h, y_ref, "1. High-level data flow", [
        "• User opens dashboard in browser, logs in (session; users.json or S3). Credits tracked per user.",
        "• Profile IQ: User submits job (brand, date ranges, filters) → app runs run_analysis() in background → bg.py connects to ClickHouse (behavioralgraph), runs full pipeline (universe scan, behavioral graph, boosting, ESPN consistency) → result CSV written → app uploads to S3 dashboard-inputs, updates job status (in-memory + S3 system/jobs_status.json), updates profile list cache (system/s3_cache.json).",
        "• Hedge Fund IQ: Ticker list and CSV data from S3 aggregated-tickers. Ticker images, profile mappings, SEC actuals from S3 dashboard-inputs/metadata/ (ticker_images_cache.json, ticker_profile_mappings.json, hedge_fund_sec_actuals.json). No ClickHouse for ticker data; all from S3.",
        "• Subscriber IQ: Profile list and CSV data from S3 svod-acquisition. Attribution: user runs SVOD job → app runs SVOD_Churn_Attribution.py → ClickHouse (clickstream) → result CSV uploaded to S3.",
        "• Ticket Sales IQ: List and data from S3 ticket-sales-iq. Ticket Sales Tracker: S3 ticket-sales-tracker. Attribution jobs run Ticket_Sales_Attribution.py (and related) against ClickHouse (clickstream); results uploaded to respective S3 buckets.",
        "• Netflix Ranker: App queries ClickHouse (behavioralgraph.netflix) (by date, show, season, episode). Results cached in S3 dashboard-inputs for fast repeat loads; backfill runs in background for historical days.",
    ])

    add_section(c, margin, w, h, y_ref, "2. Components (in-depth)", [
        "Render (host)",
        "  • Flask app (app.py): All HTTP routes, /api/* endpoints, session-based auth, user/credit management, job queue (in-memory jobs dict + S3 jobs_status.json for persistence across workers). S3 clients (boto3) for dashboard-inputs, svod-acquisition, ticket-sales-iq, ticket-sales-tracker, aggregated-tickers. ClickHouse used indirectly via bg.py and attribution scripts.",
        "  • bg.py: Behavioral graph pipeline. Connects to ClickHouse (behavioralgraph), performs universe scan, runs full pipeline (brand expansion, clickstream queries, behavioral categories, 2x universal boost, sports boosts, ESPN consistency, sample size inflation, post-save divisions). Outputs CSV; app uploads to S3.",
        "  • Attribution scripts: Talent_Theater_Attribution.py, Ticket_Sales_Attribution.py, SVOD_Churn_Attribution.py, etc. Each connects to ClickHouse (clickstream), runs attribution logic, writes CSV; app uploads to S3.",
        "",
        "AWS S3 (region us-east-2)",
        "  • dashboard-inputs: Profile result CSVs (per job); metadata/ (ticker_images_cache.json, ticker_profile_mappings.json, hedge_fund_sec_actuals.json); purgatory/ (files awaiting admin release); system/jobs_status.json (cross-worker job status); system/s3_cache.json (profile list cache); Netflix Ranker cache.",
        "  • svod-acquisition: Subscriber IQ CSV files.",
        "  • ticket-sales-iq: Ticket Sales IQ CSV files (talent-to-theater attribution).",
        "  • ticket-sales-tracker: Ticket Sales Tracker CSV files (movie viewers → theater).",
        "  • aggregated-tickers: Hedge Fund IQ ticker CSV files.",
        "",
        "ClickHouse",
        "  • BEHAVIORALGRAPH database, BEHAVIORGRAPH6X warehouse: Raw clickstream tables (loaded by send_to_snow.py from lordata CSVs); used by bg.py for Profile IQ. NETFLIX table used for Netflix Ranker.",
        "  • PROCESSEDCLICKSTREAM database: Attribution warehouses (e.g. TICKETS_SALES_WH_6XL, ATTRIBUTIONPROCESSING) for Ticket Sales, SVOD, Talent Theater attribution scripts.",
        "",
        "Data ingestion (offline)",
        "  • lordata_part1 / lordata_part2: Raw clickstream data → normalized CSV (columns e.g. visit_ts, browser, platform, url, uid, Delivered).",
        "  • send_to_snow.py: Scans local CSV folder, INSERT into ClickHouse clickstream tables.",
        "",
        "External",
        "  • OpenAI: AI-generated insights, persona, marketing strategy, business deck, profile comparison (API key in env).",
        "  • Gmail API: Welcome and password-reset emails (OAuth tokens stored by app).",
    ])

    # ----- Page 3: Profile IQ flow & IQ products -----
    c.showPage()
    y_ref = [h - margin - 28]

    add_section(c, margin, w, h, y_ref, "3. Profile IQ job flow (step-by-step)", [
        "1. User submits via /api/submit (POST) with project_name, brands, sample_start/end, behavior_start/end, filters, options.",
        "2. App creates job_id (UUID), stores job in memory (jobs[job_id]) and persists status to S3 system/jobs_status.json.",
        "3. run_analysis() runs in background thread: imports bg, expands brands via generate_brand_variations, sets deterministic seed, optionally downloads reference file from S3 for demographic consistency.",
        "4. bg.connect_db() (CH via clickhouse_connector). perform_full_universe_scan() for sample sizing. bg.run_full_pipeline() runs full behavioral graph (queries, boosting, ESPN, divisions), writes CSV to temp file.",
        "5. App uploads result CSV to S3 dashboard-inputs, updates job status to completed, triggers smart_cache_update so profile list includes new file.",
        "6. User sees job complete; can open profile (data loaded via presigned S3 URL or /api/job-data). Frontend renders demographics, behavioral charts, AI insights (OpenAI), compare profiles.",
    ])

    add_section(c, margin, w, h, y_ref, "4. IQ products (in-depth)", [
        "Profile IQ",
        "  • Run new analysis (brand, dates, filters) or open existing profile from S3 list. Demographics, behavioral categories, frequency (optional), Gen Pop projection. AI: insights, persona, marketing strategy, business deck, compare profiles. Credits consumed per run.",
        "Hedge Fund IQ",
        "  • Ticker-centric. Ticker list and CSV data from S3 aggregated-tickers. Metadata (images, profile mappings, SEC actuals) from dashboard-inputs/metadata/. User picks ticker → sees linked customer profiles (up to 5 per ticker), SEC actuals by quarter, accuracy/MAPE, historic performance.",
        "Subscriber IQ",
        "  • Profile list from S3 svod-acquisition (merged with dashboard-inputs cache for single list). View CSV data (subscriber metrics). Run SVOD attribution job → SVOD_Churn_Attribution.py → ClickHouse (clickstream) → result CSV to S3.",
        "Ticket Sales IQ",
        "  • List and data from S3 ticket-sales-iq. Talent-to-theater attribution. Run attribution job → Ticket_Sales_Attribution.py → Snowflake → result to S3. Admin can set display name, category, image per file.",
        "Ticket Sales Tracker",
        "  • List and data from S3 ticket-sales-tracker. Movie viewers → theater attribution. Same pattern: run job → attribution script → Snowflake → S3.",
        "Netflix Ranker",
        "  • Data from Snowflake BEHAVIORALGRAPH.PUBLIC.NETFLIX. API returns by_date (series), by_date_season (show+season), by_date_episode (show+season+episode). Cached in S3 for speed; backfill fills historical days in background.",
    ])

    # ----- Page 4: API, auth, deployment -----
    c.showPage()
    y_ref = [h - margin - 28]

    add_section(c, margin, w, h, y_ref, "5. API endpoints (summary)", [
        "Auth: /login, /logout, /terms, /privacy. /health, /healthz, /ready (no auth).",
        "Profile IQ: /api/submit (POST), /api/status/<job_id>, /api/jobs (list; ?refresh=1 to sync S3), /api/job-data/<job_id>, /api/download/<job_id>, /api/profile-data (presigned S3 URL).",
        "Hedge Fund IQ: /api/hedge-fund-iq/tickers (list), /api/hedge-fund-iq/data/<ticker>, /api/hedge-fund-iq/profile-mapping/<ticker>, /api/hedge-fund-iq/sec-actuals/<ticker>, /api/ticker-image/<ticker>.",
        "Subscriber IQ: List/data via /api/jobs (svod-acquisition prefix). /api/attribution/svod-acquisition (POST) to run attribution.",
        "Ticket Sales IQ: /api/ticket-sales-iq/list, /api/ticket-sales-iq/data/<s3_key>. Ticket Sales Tracker: /api/ticket-sales-tracker/list, /api/ticket-sales-tracker/data/<s3_key>, /api/ticket-sales-tracker/download/<s3_key>. /api/attribution/ticket-sales-tracker (POST).",
        "Netflix Ranker: /api/netflix-ranker (GET; optional force_refresh).",
        "Admin: /admin, /api/admin/users, /api/admin/refresh-cache, /api/admin/ticker-image, /api/admin/ticket-sales-metadata, purgatory release, Gmail connect/callback, etc.",
    ])

    add_section(c, margin, w, h, y_ref, "6. Auth, credits & admin", [
        "• Session-based auth; users and hashed passwords in users.json (or S3). Roles: admin, user (optional purgatory_access).",
        "• Credits: each user has a credit balance; running a Profile IQ job consumes credits. Admin can set balance, restore defaults.",
        "• Admin: Create/edit/delete users, reset password, send welcome/reset emails (Gmail). Purgatory: uploaded files land in S3 purgatory/ until admin releases to main bucket. Cache refresh (metadata, profile list). Ticker images and profile mappings (S3 metadata/). Push-cache-update endpoint for uploaders to invalidate profile list cache.",
    ])

    add_section(c, margin, w, h, y_ref, "7. Deployment & tech stack", [
        "• Host: Render (render.com). Web service from render.yaml / render-native.yaml; buildCommand (build.sh), startCommand (start.sh), healthCheckPath /healthz. Gunicorn (workers/threads configurable via env).",
        "• Python: Flask, flask-cors, flask-socketio, pandas, numpy, clickhouse-connect, boto3, openai, google-api-python-client, reportlab, etc. (see requirements.txt).",
        "• Frontend: Single-page app in templates/index.html (Chart.js, Socket.IO for real-time). Admin UI in templates/admin.html.",
        "• Config: Environment variables (APP_USERNAME, APP_PASSWORD, SNOWFLAKE_*, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, OPENAI_API_KEY, etc.). config.py (Snowflake) not committed; use env or Render env vars.",
    ])

    c.save()
    print(f"✅ PDF written: {out_path}")
    return out_path

if __name__ == "__main__":
    main()
