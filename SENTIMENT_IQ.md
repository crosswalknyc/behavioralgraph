# Sentiment IQ

Three-layer brand-sentiment tracker.

## Architecture

| Layer | What it does | Source |
| --- | --- | --- |
| 1. Behavioral | Score every clickstream event by query lexicon, domain taxonomy, path signals, subreddit bias | `clickstream.clickstream_final` (ClickHouse) |
| 2. Page-content | Fetch top-URL `<title>` / OG / meta-description and have OpenAI (`gpt-4o-mini`) classify positive / negative / neutral | Cached per URL forever at `s3://dashboard-inputs/sentiment-iq/pages/<sha256>.json` |
| 3. LLM web-search rollup | `gpt-4o-search-preview` pulls live narratives across news / Reddit / X / forums, `gpt-4o` synthesizes 3 themed rollups (positive / negative / neutral) with sample quotes + source links | Cached daily per tracker at `s3://dashboard-inputs/sentiment-iq/rollups/<tracker_id>/<YYYY-MM-DD>.json` |

Demographics for each sentiment bucket join `userdata.user_data_sanitized` on UID (Age, Gender, Income, Ethnicity, Education, Marital, Children, Homeowner, State, DMA).

A Net Sentiment Score in `-100..+100` summarizes Layer 1; the AI Rollups tab renders Layer 3.

## Files

| Path | Role |
| --- | --- |
| `bg-webapp/sentiment_iq.py` | All sentiment logic (lexicon, taxonomy, scoring, S3 helpers, orchestrator, alerts) |
| `bg-webapp/app.py` — `/api/sentiment-iq/*` | Flask routes |
| `bg-webapp/templates/index.html` — `#sentimentIQView` | Dashboard with 8 tabs (Overview, Channels, Platforms, Demographics, Geographic, AI Rollups, Top URLs, Compare) |
| `bg-webapp/templates/index.html` — `customAnalysisTabContentSentimentIQ` | Input form inside Analysis IQ |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/sentiment-iq/submit` | Create tracker; optional backfill costs 10 credits |
| GET | `/api/sentiment-iq/list` | Trackers visible to caller (owner-scoped; admin sees all) |
| GET | `/api/sentiment-iq/results/<tracker_id>` | Latest rolled-up dashboard payload + config |
| POST | `/api/sentiment-iq/refresh/<tracker_id>` | Manual re-run (owner/admin) |
| POST | `/api/sentiment-iq/delete/<tracker_id>` | Soft-delete (sets `ongoing=false`, `status=deleted`) |
| POST | `/api/cron/sentiment-iq` | Daily cron entry; `X-Cron-Secret` header required |

## S3 layout

```
s3://dashboard-inputs/
  sentiment-iq/
    trackers/<tracker_id>.json
    results/<tracker_id>/latest.json
    results/<tracker_id>/daily/<YYYY-MM-DD>.json
    rollups/<tracker_id>/<YYYY-MM-DD>.json
    pages/<sha256(url)>.json
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CRON_SECRET` | _(required)_ | Shared with the Render Cron Job |
| `CREDITS_SENTIMENT_IQ` | `10` | Credits to start a tracker (backfill only — Ongoing-only registration is free) |
| `SENTIMENT_IQ_ALERT_DROP_PCT` | `15` | Positive-share drop threshold (absolute pts) for an alert email |
| `SENTIMENT_IQ_ALERT_SPIKE_PCT` | `25` | Negative-event spike threshold (%) for an alert email |
| `OPENAI_API_KEY` | _(required for Layers 2 & 3)_ | OpenAI client |
| `PUBLIC_APP_URL` | _(optional)_ | Used to build the "Open Sentiment IQ" button URL in alert emails |

## Render Cron Job

In the Render dashboard for the `bg-webapp` service, add a new **Cron Job**:

| Field | Value |
| --- | --- |
| Name | `Sentiment IQ daily refresh` |
| Schedule | `0 13 * * *` (06:00 Pacific Standard Time / 13:00 UTC; adjust for DST as needed) |
| Command | `curl -fsS -X POST -H "X-Cron-Secret: $CRON_SECRET" "$PUBLIC_APP_URL/api/cron/sentiment-iq"` |
| Environment | Inherit `CRON_SECRET` and `PUBLIC_APP_URL` from the service |

Test it without writing/emailing by appending `?dry_run=1` to the URL. Force a non-Ongoing tracker to run with `?force=1&only=<tracker_id>`.

## Adding the access flag to a user

Set `has_sentiment_iq_access: true` either via the admin user-edit endpoint:

```
POST /api/admin/users/<username>
Content-Type: application/json
{ "has_sentiment_iq_access": true }
```

Or via company defaults to grant it to all current/future members of a company.

## Local testing

```bash
# Set env
export CRON_SECRET=devsecret
export OPENAI_API_KEY=sk-...

# Behavioral scoring quick test (no network needed)
python3 -c "
from sentiment_iq import score_behavioral_event
for u in ['https://www.google.com/search?q=nike+scam',
          'https://www.bbb.org/profile/nike',
          'https://www.reddit.com/r/scams/comments/abc/nike_drop/']:
    print(score_behavioral_event(u, 'NIKE'))
"

# Trigger a dry-run cron locally
curl -fsS -X POST -H "X-Cron-Secret: devsecret" \
  "http://localhost:5000/api/cron/sentiment-iq?dry_run=1"
```
