#!/usr/bin/env python3
"""
Script to divide all values in MOST PURCHASED BRANDS category by 1.6
in all CSV files in the dashboard-inputs S3 bucket.
"""

import boto3
import pandas as pd
import io
import os
from datetime import datetime

# S3 Configuration
S3_REGION = 'us-east-2'
S3_BUCKET = 'dashboard-inputs'
TARGET_CATEGORY = 'MOST PURCHASED BRANDS'
DIVIDE_BY = 1.6

# Columns to divide
DIVIDE_COLUMNS = ['Brand Penetration (Row)', 'Original Raw Numbers', 'US Gen Pop Projection']

def get_s3_client():
    """Get S3 client."""
    return boto3.client(
        's3',
        region_name=S3_REGION,
        endpoint_url=f'https://s3.{S3_REGION}.amazonaws.com',
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
    )

def list_csv_files(s3_client):
    """List all CSV files in the root of the bucket (not recursive)."""
    csv_files = []
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Delimiter='/'):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if '/' not in key and key.lower().endswith('.csv'):
                    csv_files.append(key)
    except Exception as e:
        print(f"❌ Error listing files: {e}")
    return csv_files

def process_csv(s3_client, key, dry_run=False):
    """Process a single CSV file - divide MOST PURCHASED BRANDS values by 1.6."""
    try:
        # Download the file
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        content = response['Body'].read().decode('utf-8')
        
        # Parse CSV
        df = pd.read_csv(io.StringIO(content))
        
        # Check if required columns exist
        if 'Column' not in df.columns or 'Value' not in df.columns:
            return False, 0, "Missing columns"
        
        # Find MOST PURCHASED BRANDS rows
        mask = df['Column'].str.upper().str.strip() == TARGET_CATEGORY.upper()
        matching_rows = df[mask]
        
        if len(matching_rows) == 0:
            return False, 0, "No matching rows"
        
        changes_made = 0
        
        # Process each matching row - divide by 1.6
        for idx in matching_rows.index:
            value_name = df.at[idx, 'Value']
            
            for col in DIVIDE_COLUMNS:
                if col in df.columns:
                    try:
                        current_val = df.at[idx, col]
                        if pd.isna(current_val):
                            continue
                        
                        current_str = str(current_val).replace(',', '')
                        current_num = float(current_str)
                        
                        # Divide by 1.6
                        new_num = current_num / DIVIDE_BY
                        
                        # Format appropriately
                        if col == 'Original Raw Numbers' or col == 'US Gen Pop Projection':
                            new_val = str(int(round(new_num)))
                        else:
                            new_val = f"{new_num:.2f}"
                        
                        df.at[idx, col] = new_val
                        changes_made += 1
                    except Exception as e:
                        pass
        
        # Recalculate Category Share for MOST PURCHASED BRANDS category
        if 'Brand Penetration (Row)' in df.columns and 'Category Share' in df.columns:
            category_rows = df[mask]
            if len(category_rows) > 0:
                total_penetration = 0
                for cat_idx in category_rows.index:
                    try:
                        val = str(df.at[cat_idx, 'Brand Penetration (Row)']).replace(',', '')
                        total_penetration += float(val)
                    except:
                        pass
                
                if total_penetration > 0:
                    for cat_idx in category_rows.index:
                        try:
                            val = str(df.at[cat_idx, 'Brand Penetration (Row)']).replace(',', '')
                            penetration = float(val)
                            new_share = (penetration / total_penetration) * 100
                            df.at[cat_idx, 'Category Share'] = f"{new_share:.2f}"
                        except:
                            pass
        
        if changes_made == 0:
            return False, 0, "No changes needed"
        
        if dry_run:
            print(f"  🔍 DRY RUN: Would update {key} ({len(matching_rows)} rows, {changes_made} value changes)")
            return True, changes_made, "Would update"
        
        # Upload the modified file back to S3
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=output.getvalue().encode('utf-8'),
            ContentType='text/csv'
        )
        
        print(f"  ✅ Updated {key} ({len(matching_rows)} rows)")
        return True, changes_made, "Updated"
        
    except Exception as e:
        return False, 0, f"Error: {e}"

def main(dry_run=True):
    """Main function to process all CSV files."""
    print(f"\n{'='*60}")
    print(f"MOST PURCHASED BRANDS Fix Script")
    print(f"Dividing all values by {DIVIDE_BY}")
    print(f"{'='*60}")
    print(f"Mode: {'DRY RUN (no changes will be made)' if dry_run else 'LIVE (changes will be saved)'}")
    print(f"Bucket: {S3_BUCKET}")
    print(f"Target category: {TARGET_CATEGORY}")
    print(f"{'='*60}\n")
    
    s3_client = get_s3_client()
    
    print("📂 Listing CSV files...")
    csv_files = list_csv_files(s3_client)
    print(f"Found {len(csv_files)} CSV files\n")
    
    total_updated = 0
    total_skipped = 0
    
    for i, key in enumerate(csv_files, 1):
        if i % 100 == 0:
            print(f"[{i}/{len(csv_files)}] Processing...")
        updated, changes, reason = process_csv(s3_client, key, dry_run=dry_run)
        if updated:
            total_updated += 1
        else:
            total_skipped += 1
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total files checked: {len(csv_files)}")
    print(f"Files {'that would be ' if dry_run else ''}updated: {total_updated}")
    print(f"Files skipped: {total_skipped}")
    print(f"{'='*60}\n")

if __name__ == '__main__':
    import sys
    dry_run = '--live' not in sys.argv
    if not dry_run:
        confirm = input("⚠️ LIVE MODE: This will modify files in S3. Type 'yes' to confirm: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    main(dry_run=dry_run)
