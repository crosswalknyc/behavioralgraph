# Render Deployment Timeout Fix

## Problem
Render deployments are timing out during the build/deploy process.

## Root Causes
1. **Heavy dependencies**: pandas, numpy, snowflake-connector, weasyprint take time to install
2. **Docker build overhead**: Docker builds are slower than native Python deployments
3. **Large application file**: app.py is 7500+ lines, takes time to import
4. **No build caching**: Dependencies reinstalled on every deploy

## Solutions Implemented

### 1. Optimized Dockerfile (Multi-stage Build)
- Uses multi-stage build to reduce final image size
- Better layer caching for dependencies
- Faster subsequent builds

### 2. .dockerignore File
- Reduces build context size
- Excludes unnecessary files from Docker build

### 3. Alternative: Native Python Runtime
- Created `render-native.yaml` for faster builds
- Native Python runtime is typically 2-3x faster than Docker
- No Docker build overhead

## How to Fix Timeout Issues

### Option 1: Use Native Python Runtime (RECOMMENDED - Fastest)

1. **In Render Dashboard:**
   - Go to your service settings
   - Change "Runtime" from "Docker" to "Python 3"
   - Set "Build Command" to: `pip install --upgrade pip setuptools wheel && pip install -r requirements.txt`
   - Set "Start Command" to: `./start.sh`

2. **OR rename the file:**
   ```bash
   cd bg-webapp
   mv render.yaml render-docker.yaml
   mv render-native.yaml render.yaml
   git add render.yaml
   git commit -m "Switch to native Python runtime for faster builds"
   git push
   ```

### Option 2: Optimize Current Docker Setup

The Dockerfile has been optimized with:
- Multi-stage builds
- Better caching
- Smaller final image

Just push the changes:
```bash
cd bg-webapp
git add Dockerfile .dockerignore render.yaml
git commit -m "Optimize Docker build for faster deployments"
git push
```

### Option 3: Upgrade Render Plan

If timeouts persist:
- Upgrade from "Starter" to "Standard" plan
- Higher plans have longer timeout limits
- Better build resources

### Option 4: Split Dependencies (Advanced)

If still timing out, consider:
1. Split requirements.txt into `requirements-base.txt` and `requirements-optional.txt`
2. Install only essential packages first
3. Load optional packages on-demand

## Monitoring Deployments

1. **Check Render logs** during deployment to see where it's timing out
2. **Build phase timeout**: Usually installing dependencies
3. **Deploy phase timeout**: Usually app startup/initialization

## Quick Test

After switching to native Python runtime, deployments should:
- Build in ~5-10 minutes (vs 15-20+ with Docker)
- Deploy faster
- Use less resources

## If Still Timing Out

1. Check Render service logs for specific error
2. Verify all environment variables are set
3. Consider using Render's "Manual Deploy" to test
4. Contact Render support to increase timeout limits

---

## behavioral-graph-dev not showing latest code?

We push app changes to the GitHub **`dev`** branch (`crosswalknyc/behavioralgraph`). **Production** often deploys from **`main`**, so the **dev** Render service must be wired to **`dev`**.

1. **Render Dashboard** → open service **behavioral-graph-dev** (or whatever the dev web service is named).
2. **Settings** → **Build & Deploy**:
   - **Branch** = **`dev`** (if it says `main`, dev will never show dev-branch commits).
   - **Auto-Deploy** = **On** (so each push to `dev` starts a deploy).
3. **Manual Deploy** → **Deploy latest commit** to pick up changes immediately.
4. Confirm GitHub: [behavioralgraph `dev` branch](https://github.com/crosswalknyc/behavioralgraph/tree/dev) shows your commit (e.g. LLMO scroll/top-100 work is on `dev`).

If the service is a **separate repo** or **submodule** checkout, ensure the build pulls **`behavioralgraph` @ `dev`**, not `finished_codes` only.

---

## LLMO IQ — Snowflake procedure & S3 summary

1. **S3 stage**: `PROCESSEDCLICKSTREAM.PUBLIC.LLMO_EXPORT_STAGE` must exist with valid credentials for `s3://llmo/` (see commented example in `setup_llmo_daily.sql`).  
   **Apply procedure DDL**: either paste `setup_llmo_daily.sql` into Snowflake Worksheets, or use `deploy_llmo_procedure_snowflake.py` (step 3).
2. **Credentials (local only):** Copy `finished_codes/.env.example` → `finished_codes/.env` (or `bg-webapp/.env`) and fill in values. `.env` is **gitignored** — never commit passwords. The deploy/run scripts load it via `python-dotenv`.  
3. **Redeploy procedure + run once** (with env vars or `.env` set):  
   `cd bg-webapp && python3 deploy_llmo_procedure_snowflake.py`  
   This reads `../setup_llmo_daily.sql`, runs `CREATE OR REPLACE PROCEDURE ... SP_LLMO_DAILY`, then `CALL` it.
4. **Run daily load only** (procedure already deployed):  
   `python3 run_llmo_daily_snowflake.py`  
   Calls `SP_LLMO_DAILY()` — processes **yesterday (US/Pacific)** into `LLMO`, rebuilds the JSON summary, exports to `s3://llmo/processed/llmo_daily_summary.json.gz`.
5. **Scheduled task**: `LLMO_DAILY_TASK` (cron in `setup_llmo_daily.sql`) should call the same procedure after you deploy the updated procedure body.

### LLMO “AI search themes” (OpenAI rollup)

- Requires **`OPENAI_API_KEY`** (same as other AI features). Themes are cached under `ai_cache/llmo_search_themes/` per date range + search payload hash.
- Optional env: **`LLMO_SEARCH_THEMES_MODEL`** (default `gpt-4o`; use `gpt-4o-mini` to reduce cost), **`LLMO_SEARCH_THEMES_BATCH`** (default `75`, queries per API call).
