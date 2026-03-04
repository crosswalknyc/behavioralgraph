# Gen Pop data source

When you click **Gen Pop 2026** in the profile selector, the app uses exactly this file for all data (Most Purchased Brands, Where They Shop, Social Media, Demographics, etc.):

**Filename:** `Gen_Pop_2026_03_04_2026_04_29.csv`

- **In the app:** The dashboard shows “Data source: Gen_Pop_2026_03_04_2026_04_29.csv” under the date range when viewing Gen Pop 2026.
- **Backend:** `app.py` defines `GEN_POP_CANONICAL_KEY = 'Gen_Pop_2026_03_04_2026_04_29.csv'` and serves this file from the **S3 bucket `dashboard-inputs`** for any Gen Pop request.
- **Frontend:** `templates/index.html` defines `GEN_POP_CANONICAL_KEY = 'Gen_Pop_2026_03_04_2026_04_29.csv'` and always requests this key when loading the Gen Pop profile or baseline.

To make the app match your local file:

1. Ensure your local file is named exactly: **Gen_Pop_2026_03_04_2026_04_29.csv**
2. Upload it to S3 bucket **dashboard-inputs** with that key (overwriting any existing file with that name) so the app and your CSV are in sync.

Your local path (for reference): `Gen_Pop_2026_03_04_2026_04_29.csv` (e.g. in the repo root or your Desktop).
