# Ticker Data Recovery Guide

## Current Situation

**What's in S3 (Persisted):**
- ✅ AAPL ticker image
- ✅ AAPL profile mappings (2 profiles)
- ✅ ADT profile mapping (1 profile)
- ❌ No other ticker images
- ❌ No SEC actuals

**What Happened:**
The ticker metadata (images, profile mappings, SEC actuals) was never stored locally before the S3 migration. The system was designed to store this data in memory or temporary files that don't persist across deployments.

## Why Only AAPL Has Data

You likely added AAPL's image and profile mappings through the admin panel AFTER the S3 migration was deployed. This data was saved directly to S3 and persisted correctly.

## Solution: Re-add Ticker Data

You need to re-add ticker images and other metadata through the admin panel. This time, it will be saved to S3 and persist forever.

### Steps to Add Ticker Data:

1. **Go to Admin Panel**
   - Click "Admin" tab
   - Click "Tickers" section

2. **For Each Ticker:**
   - Click on the ticker card
   - Add ticker image (upload file or paste URL)
   - Link customer profiles (up to 5)
   - Add SEC actuals (quarter + net growth %)
   - Click "Save"

3. **Verify Data Persists:**
   - Refresh the page
   - Data should still be there
   - Check Hedge Fund IQ dashboard
   - Images and profiles should display

## Data That Needs Re-adding

Based on the tickers visible in your screenshot:

### Tickers Without Images:
- ATUS (Residential Customers Broadband)
- BADOO (Paying Users)
- CABO (Residential Data PSUs)
- CHTR (Internet Residential Customer Relationships)
- CMCSA (Domestic Broadband Residential Customers)
- DASH (Total Orders)
- DIS (Paid subscribers - Disney+ Domestic US & Canada)
- DUOL (Paid Subscribers)
- ... and many more

### Where to Find Ticker Images:

**Option 1: Upload from Local Files**
- If you have logo files saved locally
- Use the file upload in admin panel

**Option 2: Use Image URLs**
- Find ticker logos online
- Copy image URL
- Paste into admin panel

**Option 3: Use Company Websites**
- Go to company's investor relations page
- Right-click logo → Copy image address
- Paste URL into admin panel

## Automated Bulk Import (Future Enhancement)

If you have a CSV or JSON file with all ticker data, I can create a script to bulk import it to S3. Let me know if you have:

- List of ticker symbols
- Image URLs for each ticker
- Profile filename mappings
- SEC actuals data

## Verification

After re-adding data, verify it's in S3:

```bash
cd /Users/jennamenking/Desktop/finished_codes/bg-webapp
python3 -c "
import boto3, json, os
from dotenv import load_dotenv
load_dotenv()

s3 = boto3.client('s3', region_name='us-east-1',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'))

obj = s3.get_object(Bucket='dashboard-inputs', Key='metadata/ticker_images_cache.json')
images = json.loads(obj['Body'].read().decode('utf-8'))
print(f'Ticker images in S3: {len(images)}')
for ticker in images:
    print(f'  - {ticker}')
"
```

## Important Notes

✅ **Data Now Persists**: Any data added through admin panel is saved to S3
✅ **Survives Redeployments**: S3 data persists across all deployments
✅ **No More Data Loss**: The old local storage issue is fixed
❌ **Old Data Not Recovered**: Previous data before S3 migration is lost
💡 **One-Time Re-entry**: You only need to add data once, then it's permanent

## Need Help?

If you have:
- A backup of ticker data
- A spreadsheet with ticker info
- A list of image URLs

I can create a migration script to bulk import everything to S3.
