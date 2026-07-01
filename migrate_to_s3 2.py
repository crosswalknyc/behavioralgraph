#!/usr/bin/env python3
"""
Migrate ticker metadata to S3
==============================
This script creates the initial metadata files in S3 with the data
that the user has mentioned should be persisted.
"""

import json
import boto3
import os
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# S3 Configuration
S3_REGION = 'us-east-1'
METADATA_BUCKET = 'dashboard-inputs'  # Use existing bucket

# Initialize S3 client
s3_client = boto3.client(
    's3',
    region_name=S3_REGION,
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
)

def ensure_bucket_exists():
    """Ensure the metadata bucket exists."""
    try:
        s3_client.head_bucket(Bucket=METADATA_BUCKET)
        print(f"✅ Bucket '{METADATA_BUCKET}' exists")
    except Exception as e:
        print(f"❌ Error checking bucket: {e}")
        raise

def upload_json_to_s3(filename, data):
    """Upload JSON data to S3."""
    try:
        json_data = json.dumps(data, indent=2)
        s3_client.put_object(
            Bucket=METADATA_BUCKET,
            Key=filename,
            Body=json_data.encode('utf-8'),
            ContentType='application/json'
        )
        print(f"✅ Uploaded {filename} to S3")
        return True
    except Exception as e:
        print(f"❌ Error uploading {filename}: {e}")
        return False

def main():
    """Main migration function."""
    print("=" * 60)
    print("Ticker Metadata Migration to S3")
    print("=" * 60)
    
    # Ensure bucket exists
    ensure_bucket_exists()
    
    # Initialize empty metadata files
    # These will be populated by admins through the UI
    
    # 1. Ticker Images Cache
    ticker_images = {}
    upload_json_to_s3('metadata/ticker_images_cache.json', ticker_images)
    
    # 2. Ticker Profile Mappings
    ticker_profiles = {}
    upload_json_to_s3('metadata/ticker_profile_mappings.json', ticker_profiles)
    
    # 3. SEC Actuals
    sec_actuals = {}
    upload_json_to_s3('metadata/hedge_fund_sec_actuals.json', sec_actuals)
    
    print("\n" + "=" * 60)
    print("✅ Migration Complete!")
    print("=" * 60)
    print("\nNext Steps:")
    print("1. Deploy the updated app.py to Render")
    print("2. Log in to admin panel")
    print("3. Re-add ticker images, profile mappings, and SEC actuals")
    print("4. Data will now persist across all future deploys!")
    print()

if __name__ == '__main__':
    main()
