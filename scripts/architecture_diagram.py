#!/usr/bin/env python3
"""
Behavioral Graph Dashboard – Architecture Diagram
Generates a PDF with a visual architecture diagram + component description.
Uses reportlab only (no system Graphviz required).
"""

import os
import sys

def draw_diagram(c, w, h, margin):
    """Draw a one-page visual architecture diagram."""
    from reportlab.lib import colors

    # Helper: rounded rect and label
    def box(x, y, wb, hb, label, fill=(0.9, 0.95, 1)):
        c.setFillColorRGB(*fill)
        c.roundRect(x, y, wb, hb, 4, fill=1, stroke=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 8)
        # Word-wrap roughly
        words = label.replace("\n", " ").split()
        lines, cur = [], []
        for wd in words:
            cur.append(wd)
            if len(" ".join(cur)) > 22:
                cur.pop()
                lines.append(" ".join(cur))
                cur = [wd]
        if cur:
            lines.append(" ".join(cur))
        if len(lines) > 3:
            lines = lines[:3] + ["..."]
        ty = y + hb - 6
        for L in lines:
            c.drawString(x + 4, ty, L[:28])
            ty -= 10
        return (x, y, wb, hb)

    # Layout: two columns for symmetry
    col1 = margin + 10
    col2 = w / 2 + 10
    bw = (w / 2) - margin - 30
    bh = 36
    row = h - margin - 50
    step = 52

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, h - margin - 20, "Behavioral Graph Dashboard – Architecture")
    c.setFont("Helvetica", 9)
    c.drawString(margin, h - margin - 36, "Crosswalk IQ+ (Flask on Render, S3, Snowflake)")

    # Left column: User → App → Workers
    box(col1, row, bw, bh, "Browser / User\n(Session, Credits)")
    row -= step
    box(col1, row, bw, bh, "Flask app.py\nAPI, Auth, Job Queue")
    row -= step
    box(col1, row, bw, bh, "bg.py\nProfile IQ Pipeline")
    box(col2, row, bw, bh, "Attribution Scripts\nTicket, SVOD, Talent")
    row -= step

    # S3 buckets
    box(col1, row, bw, bh, "S3 dashboard-inputs\nProfiles, metadata, jobs")
    box(col2, row, bw, bh, "S3 svod / ticket-sales\naggregated-tickers")
    row -= step

    # Snowflake
    box(col1, row, bw, bh, "Snowflake\nBEHAVIORALGRAPH\n(Profile IQ, Netflix)")
    box(col2, row, bw, bh, "Snowflake\nPROCESSEDCLICKSTREAM\n(Attribution)")
    row -= step

    # Data ingestion
    box(col1, row, bw, bh, "lordata → CSV\nsend_to_snow → Snowflake")
    box(col2, row, bw, bh, "OpenAI / Gmail API")

    # Arrows (simple lines with labels)
    c.setFont("Helvetica", 7)
    c.setDash(1, 2)
    arrow_y = h - margin - 50 - 18
    c.line(margin + 20, arrow_y, margin + 20, arrow_y - 90)
    c.drawString(margin + 22, arrow_y - 42, "HTTPS")
    arrow_y -= 90
    c.line(margin + 20, arrow_y, margin + 20, arrow_y - 90)
    c.drawString(margin + 22, arrow_y - 42, "run_analysis")
    c.setDash()

    # Center flow line: App to S3/Snowflake
    cx = w / 2 - 15
    c.line(cx, h - margin - 50 - 18, cx, row + 20)
    c.drawString(cx + 2, h - margin - 120, "read/write")
    c.drawString(cx + 2, row + 80, "query / upload")


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
    margin = 0.5 * inch

    # Page 1: Visual diagram
    draw_diagram(c, w, h, margin)

    # Page 2+: Detailed sections
    c.showPage()
    y = h - margin - 30
    c.setFont("Helvetica", 11)

    def section(title, bg_rgb=(0.95, 0.95, 0.95)):
        nonlocal y
        c.setFillColorRGB(*bg_rgb)
        c.rect(margin, y - 18, w - 2 * margin, 22, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin + 6, y - 14, title)
        c.setFont("Helvetica", 9)
        y -= 28

    def line(text, indent=0):
        nonlocal y
        if y < margin + 50:
            c.showPage()
            y = h - margin - 30
        c.drawString(margin + indent, y, text)
        y -= 14

    def para(lines_list, indent=0):
        for s in lines_list:
            line(s, indent)

    section("1. High-level data flow")
    para([
        "Browser → Login (session, users.json, credits) → Flask app on Render.",
        "Profile IQ: Submit job → app runs bg.py → Snowflake BEHAVIORALGRAPH → result CSV → S3 dashboard-inputs.",
        "Hedge Fund IQ: Ticker CSVs in S3 aggregated-tickers; metadata in S3 dashboard-inputs/metadata/.",
        "Subscriber IQ: CSVs in S3 svod-acquisition; attribution → SVOD_Churn_Attribution.py → Snowflake PROCESSEDCLICKSTREAM.",
        "Ticket Sales IQ / Tracker: S3 ticket-sales-iq, ticket-sales-tracker; attribution → Snowflake.",
        "Netflix Ranker: app queries Snowflake BEHAVIORALGRAPH.PUBLIC.NETFLIX; cache in S3.",
    ])

    section("2. Components")
    line("Render (Flask / Gunicorn)")
    para([
        "app.py: routes, /api/*, auth, job queue, S3/Snowflake clients.",
        "bg.py: behavioral graph pipeline (universe scan, boosting, ESPN consistency); runs per Profile IQ job.",
        "Attribution scripts: Talent_Theater_Attribution, Ticket_Sales_Attribution, SVOD_Churn_Attribution, etc.",
    ], indent=12)
    line("")
    line("AWS S3 (us-east-2)")
    para([
        "dashboard-inputs: profile CSVs, metadata/, purgatory/, system/jobs_status.json, Netflix cache.",
        "svod-acquisition, ticket-sales-iq, ticket-sales-tracker, aggregated-tickers.",
    ], indent=12)
    line("")
    line("Snowflake")
    para([
        "BEHAVIORALGRAPH (BEHAVIORGRAPH6X): clickstream, Profile IQ, NETFLIX table.",
        "PROCESSEDCLICKSTREAM: attribution (Ticket, SVOD, Talent).",
    ], indent=12)
    line("")
    line("Data ingestion: lordata_part1/part2 → CSV; send_to_snow.py → Snowflake PUT + COPY.")
    line("External: OpenAI (insights, persona, deck); Gmail API (emails).")

    section("3. Profile IQ job flow")
    para([
        "1. User submits via /api/submit. 2. app creates job_id, status in S3 jobs_status.json.",
        "3. run_analysis(): bg.connect_snowflake(), perform_full_universe_scan(), run_full_pipeline().",
        "4. bg.py reads clickstream, applies pipeline, writes CSV. 5. app uploads CSV to S3, updates cache.",
        "6. User opens profile (data from S3 via presigned URL or API).",
    ])

    section("4. IQ products")
    para([
        "Profile IQ: Run analysis or open existing profile; demographics, behavioral, AI insights.",
        "Hedge Fund IQ: Ticker → linked profiles; SEC actuals, accuracy.",
        "Subscriber IQ / Ticket Sales IQ / Tracker: list and data from S3; run attribution jobs.",
        "Netflix Ranker: by date, show, season, episode from Snowflake or S3 cache.",
    ])

    section("5. Auth & admin")
    para([
        "Session auth; users.json; credits per run. Admin: user CRUD, Gmail, purgatory, cache refresh, ticker metadata.",
    ])

    c.save()
    print(f"✅ PDF written: {out_path}")
    return out_path

if __name__ == "__main__":
    main()
