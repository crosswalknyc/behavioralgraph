#!/usr/bin/env python3
"""
Fix S3 CORS Configuration
==========================
Adds CORS policy to dashboard-inputs bucket to allow browser access
to presigned URLs for CSV files.
"""

import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

# S3 Configuration
S3_REGION = 'us-east-1'
BUCKET_NAME = 'dashboard-inputs'

# Initialize S3 client
s3_client = boto3.client(
    's3',
    region_name=S3_REGION,
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
)

def configure_cors():
    """Configure CORS policy for the S3 bucket."""
    
    cors_configuration = {
        'CORSRules': [
            {
                'AllowedHeaders': ['*'],
                'AllowedMethods': ['GET', 'HEAD'],
                'AllowedOrigins': [
                    'https://behavioralgraph.onrender.com',
                    'http://localhost:5000',
                    'http://127.0.0.1:5000'
                ],
                'ExposeHeaders': [
                    'ETag',
                    'Content-Length',
                    'Content-Type'
                ],
                'MaxAgeSeconds': 3600
            }
        ]
    }
    
    try:
        print(f"🔧 Configuring CORS for bucket: {BUCKET_NAME}")
        print("=" * 60)
        
        # Apply CORS configuration
        s3_client.put_bucket_cors(
            Bucket=BUCKET_NAME,
            CORSConfiguration=cors_configuration
        )
        
        print("✅ CORS configuration applied successfully!")
        print()
        print("CORS Rules:")
        print("-" * 60)
        print(json.dumps(cors_configuration, indent=2))
        print()
        print("=" * 60)
        print("✅ Configuration Complete!")
        print("=" * 60)
        print()
        print("What this allows:")
        print("  ✅ Browser can fetch CSV files from S3")
        print("  ✅ Presigned URLs work from Render domain")
        print("  ✅ Local development also works")
        print()
        print("Next steps:")
        print("  1. Refresh the dashboard")
        print("  2. Click Customer Profile tab")
        print("  3. Profile should load successfully!")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error configuring CORS: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_cors():
    """Verify CORS configuration is applied."""
    try:
        print("🔍 Verifying CORS configuration...")
        print("=" * 60)
        
        cors = s3_client.get_bucket_cors(Bucket=BUCKET_NAME)
        
        print("✅ Current CORS configuration:")
        print(json.dumps(cors['CORSRules'], indent=2))
        print()
        
        return True
        
    except s3_client.exceptions.NoSuchCORSConfiguration:
        print("❌ No CORS configuration found")
        return False
    except Exception as e:
        print(f"❌ Error verifying CORS: {e}")
        return False

if __name__ == '__main__':
    print()
    print("=" * 60)
    print("S3 CORS Configuration Fix")
    print("=" * 60)
    print()
    
    # Configure CORS
    if configure_cors():
        print()
        # Verify it was applied
        verify_cors()
    else:
        print()
        print("❌ Failed to configure CORS")
        print("Please check your AWS credentials and bucket permissions")
