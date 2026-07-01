# Scaling & Autoscaling (Render)

## Memory (512MB OOM fix)

- **Starter plan (512MB):** Defaults are tuned to minimize memory:
  - `GUNICORN_WORKERS=1`, `GUNICORN_THREADS=1`
- **If you still see "Ran out of memory (used over 512MB)":** Upgrade to **Standard** (2GB):
  - **Dashboard → Service → Settings → Instance Type** → choose **Standard** (or higher).
  - Then you can safely set `GUNICORN_THREADS=2` (or more) for better concurrency.

## Manual scaling (instance count)

- In **render.yaml**: `numInstances: 1` (or 2–100 if your plan allows).
- Or **Dashboard → Service → Settings → Scaling** → set **Instance count** (e.g. 2 for redundancy).

## Autoscaling (by CPU / memory)

- **Requires a Professional workspace** (paid).
- **Dashboard → Service → Scaling**:
  - Set **Min** and **Max** instance count.
  - Enable **Target CPU** and/or **Target memory** and set target % (e.g. 70%).
- Render will scale instance count up/down within that range to meet the target.

## Tuning from Dashboard (no redeploy)

Set these **Environment** variables on the service to change behavior without code change:

| Variable             | Default | Use |
|----------------------|---------|-----|
| `GUNICORN_WORKERS`   | 1       | Increase only if you have more RAM (e.g. Standard 2GB). More workers = more memory. |
| `GUNICORN_THREADS`   | 1       | Threads per worker. Keep at 1 on 512MB; increase after upgrading to Standard (2GB). |
| `GUNICORN_TIMEOUT`   | 300     | Request timeout in seconds. |

After changing env vars, use **Manual Deploy → Deploy latest commit** (or trigger a deploy) so the new values are applied.
