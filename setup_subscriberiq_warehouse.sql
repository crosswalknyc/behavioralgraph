-- Create SUBSCRIBERIQ 6XL warehouse for Subscriber IQ pipeline
-- Run this in Snowflake Worksheets or via SnowSQL

USE ROLE ACCOUNTADMIN;

-- Create the warehouse
CREATE WAREHOUSE IF NOT EXISTS SUBSCRIBERIQ
    WAREHOUSE_SIZE = '6X-LARGE'
    WAREHOUSE_TYPE = 'STANDARD'
    AUTO_SUSPEND = 300           -- Suspend after 5 minutes of inactivity
    AUTO_RESUME = TRUE           -- Auto-resume when queries arrive
    MIN_CLUSTER_COUNT = 1
    MAX_CLUSTER_COUNT = 1
    SCALING_POLICY = 'STANDARD'
    INITIALLY_SUSPENDED = FALSE
    COMMENT = 'Warehouse for Subscriber IQ / SVOD Churn Attribution pipeline';

-- Grant usage to appropriate roles
GRANT USAGE ON WAREHOUSE SUBSCRIBERIQ TO ROLE ACCOUNTADMIN;
GRANT OPERATE ON WAREHOUSE SUBSCRIBERIQ TO ROLE ACCOUNTADMIN;

-- Verify the warehouse was created
SHOW WAREHOUSES LIKE 'SUBSCRIBERIQ';

SELECT 'SUBSCRIBERIQ warehouse created successfully!' AS status;
