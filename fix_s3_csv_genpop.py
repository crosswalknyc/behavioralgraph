#!/usr/bin/env python3
"""
Fix US Gen Pop Projection for SAMPLE SIZE row in all CSV files in S3 bucket.
Many files are showing "32" instead of the correct projection value.
"""

import boto3
import csv
import io
import os
from botocore.exceptions import ClientError

# S3 Configuration
S3_BUCKET = 'dashboard-inputs'
S3_REGION = os.environ.get('AWS_REGION', 'us-east-1')

# Initialize S3 client
s3_client = boto3.client(
    's3',
    region_name=S3_REGION,
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
)

def calculate_gen_pop_projection(sample_size):
    """Calculate US Gen Pop Projection: (sample_size / 10,000,000) * 329,900,000"""
    try:
        sample_size_num = float(str(sample_size).replace(',', '').strip())
        projection = int((sample_size_num / 10_000_000.0) * 329_900_000.0)
        return str(projection)
    except (ValueError, TypeError):
        return None

def fix_csv_genpop(csv_content):
    """Fix the US Gen Pop Projection for SAMPLE SIZE row in CSV content."""
    # Read CSV into memory
    reader = csv.DictReader(io.StringIO(csv_content))
    fieldnames = reader.fieldnames
    
    if not fieldnames:
        return None, "No fieldnames found"
    
    rows = list(reader)
    fixed_count = 0
    sample_size_found = False
    
    # Check if US Gen Pop Projection column exists
    genpop_col = None
    for col in ['US Gen Pop Projection', 'us gen pop projection', 'US_GEN_POP_PROJECTION']:
        if col in fieldnames:
            genpop_col = col
            break
    
    if not genpop_col:
        # Add the column if it doesn't exist
        genpop_col = 'US Gen Pop Projection'
        fieldnames = list(fieldnames) + [genpop_col]
        for row in rows:
            row[genpop_col] = ''
    
    # Find SAMPLE SIZE row and fix it
    for row in rows:
        column_val = str(row.get('Column', '')).strip().upper()
        if column_val == 'SAMPLE SIZE':
            sample_size_found = True
            
            # Get sample size from Percentage or Original Raw Numbers
            sample_size = None
            for col in ['Percentage', 'Category Share', 'Original Raw Numbers']:
                if col in row and row[col]:
                    try:
                        val = str(row[col]).replace(',', '').strip()
                        if val and val not in ('', 'nan', 'NaN', 'None'):
                            sample_size = val
                            break
                    except:
                        pass
            
            if sample_size:
                projection = calculate_gen_pop_projection(sample_size)
                if projection:
                    old_projection = row.get(genpop_col, '').strip()
                    row[genpop_col] = projection
                    fixed_count += 1
                    print(f"  ✅ Fixed SAMPLE SIZE: {sample_size} → Projection: {projection} (was: {old_projection})")
                else:
                    print(f"  ⚠️  Could not calculate projection for sample size: {sample_size}")
            else:
                print(f"  ⚠️  Could not find sample size value in SAMPLE SIZE row")
    
    if not sample_size_found:
        return None, "SAMPLE SIZE row not found"
    
    if fixed_count == 0:
        return None, "No fixes needed"
    
    # Write fixed CSV back to string
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    
    return output.getvalue(), f"Fixed {fixed_count} row(s)"

def process_s3_file(s3_key):
    """Process a single S3 file to fix gen pop projection."""
    try:
        print(f"\n📄 Processing: {s3_key}")
        
        # Download file
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        
        # Fix the CSV
        fixed_content, message = fix_csv_genpop(csv_content)
        
        if fixed_content:
            # Upload fixed file back
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=s3_key,
                Body=fixed_content.encode('utf-8'),
                ContentType='text/csv'
            )
            print(f"  ✅ {message} - File updated in S3")
            return True
        else:
            print(f"  ℹ️  {message}")
            return False
            
    except ClientError as e:
        print(f"  ❌ Error processing {s3_key}: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Unexpected error processing {s3_key}: {e}")
        return False

def main():
    """List all CSV files in S3 and fix them."""
    print("🔍 Listing all CSV files in S3 bucket...")
    
    try:
        # List all objects in the bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=S3_BUCKET)
        
        csv_files = []
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    if key.lower().endswith('.csv'):
                        csv_files.append(key)
        
        print(f"✅ Found {len(csv_files)} CSV files")
        
        if not csv_files:
            print("⚠️  No CSV files found in bucket")
            return
        
        # Process each file
        fixed_count = 0
        skipped_count = 0
        error_count = 0
        
        for s3_key in csv_files:
            if process_s3_file(s3_key):
                fixed_count += 1
            else:
                skipped_count += 1
        
        print(f"\n📊 Summary:")
        print(f"  ✅ Fixed: {fixed_count} files")
        print(f"  ℹ️  Skipped: {skipped_count} files")
        print(f"  ❌ Errors: {error_count} files")
        
    except ClientError as e:
        print(f"❌ Error listing S3 files: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == '__main__':
    main()
