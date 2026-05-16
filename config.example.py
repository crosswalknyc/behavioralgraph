# Configuration template
# Copy this file to config.py and update with your actual credentials.
# For Render deployment, set these as environment variables instead.

import os

# ClickHouse connection — actual values come from .env / Render env vars,
# read by migration/clickhouse_connector.py. This dict is here for
# discoverability only; nothing in the live pipeline imports it directly.
CLICKHOUSE_CONFIG = {
    'host':     os.environ.get('CH_HOST',     '168.119.215.48'),
    'port':     int(os.environ.get('CH_PORT', '8123')),
    'user':     os.environ.get('CH_USER',     'bgapp'),
    'password': os.environ.get('CH_PASSWORD', ''),
}

# Performance Configuration
PERFORMANCE_CONFIG = {
    "statement_timeout_seconds": 3600,
    "universe_scale_factor": 111,
    "sample_rate": 0.01,
    "max_visits_per_user": 6000,
}

# Output Configuration
OUTPUT_CONFIG = {
    "output_directory": os.environ.get('OUTPUT_DIR', './outputs/'),
    "file_naming_pattern": "{project_name}_{date}_{time}.csv",
    "include_timestamps": True,
    "compress_output": False,
}

# Debug Configuration
DEBUG_CONFIG = {
    "silence_verbose_output": True,
    "enable_progress_monitoring": True,
    "validate_output": True,
}
