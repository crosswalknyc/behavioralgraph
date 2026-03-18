#!/usr/bin/env python3
"""One-time script: build processed LLMO parquet from all raw S3 CSV files."""
import sys, os, io, gzip, re, time
import boto3, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote_plus

BUCKET = 'llmo'
PREFIX = 'full_table/'
PARQUET_KEY = 'processed/llmo_processed.parquet'

s3 = boto3.client('s3', region_name='us-east-2')

def load_one(key):
    try:
        resp = s3.get_object(Bucket=BUCKET, Key=key)
        raw = resp['Body'].read()
        text = gzip.decompress(raw).decode('utf-8')
        return pd.read_csv(
            io.StringIO(text),
            usecols=['UID','DELIVERED','COMMON_NAME','MATCH_TYPE','BROWSER','PLATFORM','URL','VISIT_TS'],
            dtype={'UID':'str','COMMON_NAME':'str','MATCH_TYPE':'str','BROWSER':'str','PLATFORM':'str','URL':'str'}
        )
    except Exception as e:
        print(f'  ERR {key}: {e}', flush=True)
        return None

print('Listing files...', flush=True)
t0 = time.time()
paginator = s3.get_paginator('list_objects_v2')
keys = []
for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
    for obj in page.get('Contents', []):
        if obj['Key'].endswith('.csv.gz'):
            keys.append(obj['Key'])
print(f'Found {len(keys)} files', flush=True)

print('Downloading and parsing (48 threads)...', flush=True)
dfs = []
with ThreadPoolExecutor(max_workers=48) as pool:
    futures = {pool.submit(load_one, k): k for k in keys}
    done = 0
    for fut in as_completed(futures):
        done += 1
        if done % 50 == 0:
            print(f'  {done}/{len(keys)} files done...', flush=True)
        r = fut.result()
        if r is not None:
            dfs.append(r)

print(f'Concatenating {len(dfs)} DataFrames...', flush=True)
df = pd.concat(dfs, ignore_index=True)
del dfs
print(f'Total rows: {len(df):,}', flush=True)

print('Extracting search terms...', flush=True)
pattern = re.compile(r'[?&](?:q|query|p|search|prompt|text)=([^&]+)', re.IGNORECASE)
def extract(url):
    if not isinstance(url, str): return None
    m = pattern.search(url)
    if m:
        try: return unquote_plus(m.group(1))[:200]
        except: return m.group(1)[:200]
    return None
df['SEARCH_TERM'] = df['URL'].apply(extract)
df.drop(columns=['URL'], inplace=True)

print('Parsing dates & optimising dtypes...', flush=True)
df['DELIVERED'] = pd.to_datetime(df['DELIVERED'], errors='coerce').dt.date
df['VISIT_TS'] = pd.to_datetime(df['VISIT_TS'], errors='coerce')
for col in ['COMMON_NAME','MATCH_TYPE','BROWSER','PLATFORM']:
    df[col] = df[col].astype('category')

mem_gb = df.memory_usage(deep=True).sum() / 1e9
print(f'Memory: {mem_gb:.2f} GB', flush=True)
print(f'Date range: {df["DELIVERED"].min()} to {df["DELIVERED"].max()}', flush=True)
print(f'Unique dates: {df["DELIVERED"].nunique()}', flush=True)

print('Writing parquet...', flush=True)
save_df = df.copy()
save_df['DELIVERED'] = pd.to_datetime(save_df['DELIVERED'].astype(str), errors='coerce')
for col in ['COMMON_NAME','MATCH_TYPE','BROWSER','PLATFORM']:
    save_df[col] = save_df[col].astype(str)
buf = io.BytesIO()
save_df.to_parquet(buf, index=False, engine='pyarrow', compression='snappy')
buf.seek(0)
size_mb = buf.getbuffer().nbytes / 1e6
print(f'Parquet size: {size_mb:.0f} MB', flush=True)

print(f'Uploading to s3://{BUCKET}/{PARQUET_KEY}...', flush=True)
s3.put_object(Bucket=BUCKET, Key=PARQUET_KEY, Body=buf.getvalue())

elapsed = time.time() - t0
print(f'DONE in {elapsed:.1f}s', flush=True)
