#!/usr/bin/env python3
"""Revert 56_Days_Season_1 CSV to Total Show Conversion Rate = 0.77% (76,734 is 0.77% of 9,999,995)."""

import boto3
import csv
import io
import os

SUBSCRIBER_S3_BUCKET = 'svod-acquisition'
KEY = '56_Days_Season_1_03_17_2026_17_05.csv'

def main():
    s3 = boto3.client(
        's3',
        region_name=os.environ.get('AWS_REGION', 'us-east-1'),
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    )
    resp = s3.get_object(Bucket=SUBSCRIBER_S3_BUCKET, Key=KEY)
    rows = list(csv.reader(io.StringIO(resp['Body'].read().decode('utf-8'))))
    for i, row in enumerate(rows):
        if row and 'Total Show Conversion Rate' in (row[0] or ''):
            while len(row) < 10:
                row.append('')
            row[8] = '0.77%'
            rows[i] = row
            break
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\n')
    for r in rows:
        w.writerow(r)
    s3.put_object(
        Bucket=SUBSCRIBER_S3_BUCKET,
        Key=KEY,
        Body=out.getvalue().encode('utf-8'),
        ContentType='text/csv',
    )
    print('Reverted 56_Days_Season_1 to Total Show Conversion Rate = 0.77%')

if __name__ == '__main__':
    main()
