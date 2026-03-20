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

## LLMO IQ — Snowflake procedure & S3 summary

1. **Apply procedure changes** (from repo root `finished_codes`): run the `CREATE OR REPLACE PROCEDURE ... SP_LLMO_DAILY` block in Snowflake Worksheets (see `setup_llmo_daily.sql`). Ensure the S3 stage `PROCESSEDCLICKSTREAM.PUBLIC.LLMO_EXPORT_STAGE` exists with valid credentials for `s3://llmo/`.
2. **Run a daily load** (with Snowflake env configured, from `bg-webapp/`):  
   `python3 run_llmo_daily_snowflake.py`  
   This calls `CALL PROCESSEDCLICKSTREAM.PUBLIC.SP_LLMO_DAILY();` which processes **yesterday (US/Pacific)** into `LLMO`, rebuilds the JSON summary, and exports to `s3://llmo/processed/llmo_daily_summary.json.gz`.
3. **Scheduled task**: `LLMO_DAILY_TASK` (cron in `setup_llmo_daily.sql`) should call the same procedure after you deploy the updated procedure body.
