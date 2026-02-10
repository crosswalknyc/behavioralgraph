# Deck Builder Export Fix

## Issues Addressed

1. **PDF prints whole screen** – Previously, PDF export used `window.print()` which printed the entire app view (sidebar, headers, etc.) instead of just the deck content.
2. **Visual mismatch** – Exports (PPT, PDF, Google Slides) did not match the deck builder visuals; they output text/tables instead of the actual slide layout with charts.

## Changes Made

### 1. Added CDN Scripts (index.html head)
- `html2canvas` – For capturing slide visuals
- `jsPDF` – For programmatic PDF generation
- `PptxGenJS` – For PowerPoint export

### 2. Export Flow
- **PDF**: Opens a new window with *only* the deck slides as styled HTML, then triggers print. User selects "Save as PDF" in the print dialog. This ensures only the deck is printed, not the whole app.
- **PowerPoint / Google Slides / Keynote**: Use `getDeckExportHtml()` to generate slide-sized (960×540) HTML that matches the deck builder styling. Download as `.html`; open in PowerPoint via File > Open, or print to PDF from that window.

### 3. `getDeckExportHtml()` (from patch_export.py)
- Builds 960×540 slide-sized HTML
- Includes title slide with image, subtitle, footer
- Content slides with demo boxes (demographics) and tables (behavioral)
- Consistent Crosswalk branding

### 4. PDF Print Window
- Opens `window.open()` with deck-only HTML
- Uses `@media print` so only slides are printed
- `page-break-after: always` for each slide

## How to Apply

Run the patch (if exportToPowerPoint exists in index.html):
```bash
cd bg-webapp
python3 patch_export.py
```

Or manually add the helper and wire up export buttons to use the print-window approach instead of `window.print()`.

## Auto-Commit

Per workspace rules, changes are committed and pushed to GitHub after each update.
