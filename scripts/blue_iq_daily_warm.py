#!/usr/bin/env python3
"""
blue_iq_daily_warm.py — Thin wrapper around blue_iq_aggregator.

Was historically a 500-combo per-filter-cache pre-warm. That approach scaled
linearly with the dimension product and competed badly with the nightly
ClickHouse ETL. It's been replaced by a single-pass aggregate cube builder
in `bg-webapp/blue_iq_aggregator.py` that does one CH query per card-shape
(GROUP BY across all parties + geos at once) and writes a single ~5 MB JSON
to s3://dashboard-inputs/blue_iq/aggregates/latest.json.

The dashboard reads that cube at request time and slices it in-process,
so every filter combination is sub-second regardless of whether it's
National, a state, or a DMA.

Hetzner crontab (run after the nightly ETL finishes; ETL typically wraps
by ~5am UTC, so 8:01am UTC = 12:01am US/Pacific is a clean window):

    1 8 * * *  cd /root/finished_codes/bg-webapp && /usr/bin/python3 scripts/blue_iq_daily_warm.py >> /var/log/blue_iq_daily_warm.log 2>&1

Manual one-shot (after a fresh deploy, before the first cron run):

    python3 bg-webapp/scripts/blue_iq_daily_warm.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BG_WEBAPP = os.path.dirname(HERE)
sys.path.insert(0, BG_WEBAPP)

# Delegate to the aggregator's main() so flags pass through cleanly.
from blue_iq_aggregator import main as run_aggregator  # type: ignore  # noqa: E402


if __name__ == '__main__':
    run_aggregator()
