# Snowflake Configuration
# Copy this file to config.py and update with your actual credentials
# For Render deployment, set these as environment variables instead

import os

SNOWFLAKE_CONFIG = {
    'user': os.environ.get('SNOWFLAKE_USER', 'your_username'),
    'password': os.environ.get('SNOWFLAKE_PASSWORD', 'your_password'),
    'account': os.environ.get('SNOWFLAKE_ACCOUNT', 'your_account'),
    'warehouse': os.environ.get('SNOWFLAKE_WAREHOUSE', 'YOUR_WAREHOUSE'),
    'database': os.environ.get('SNOWFLAKE_DATABASE', 'YOUR_DATABASE'),
    'schema': os.environ.get('SNOWFLAKE_SCHEMA', 'PUBLIC'),
    'role': os.environ.get('SNOWFLAKE_ROLE', 'YOUR_ROLE')
}

# Performance Configuration
PERFORMANCE_CONFIG = {
    "base_warehouse_size": "X-Large",
    "max_warehouse_size": "3X-Large",
    "base_acceleration_factor": 8,
    "max_acceleration_factor": 16,
    "statement_timeout_seconds": 3600,
    "universe_scale_factor": 111,
    "sample_rate": 0.01,
    "max_visits_per_user": 6000
}

# Cost Configuration
COST_CONFIG = {
    "credit_rate_per_dollar": 2.40,
    "enable_cost_tracking": True,
    "enable_warehouse_scaling": True
}

# Output Configuration
OUTPUT_CONFIG = {
    "output_directory": os.environ.get('OUTPUT_DIR', './outputs/'),
    "file_naming_pattern": "{project_name}_{date}_{time}.csv",
    "include_timestamps": True,
    "compress_output": False
}

# Debug Configuration
DEBUG_CONFIG = {
    "silence_verbose_output": True,
    "enable_progress_monitoring": True,
    "log_query_costs": True,
    "validate_output": True
}
