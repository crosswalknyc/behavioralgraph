#!/usr/bin/env python3
"""Build LLMO daily summary JSON from raw S3 CSV files, processing one date at a time.
Run locally or on a machine with sufficient memory. Uploads result to S3.
Schedule this daily before 5:45 AM PST so the server picks up the fresh summary."""
import sys, os, io, gzip, json, re, time
import boto3, pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote_plus
from collections import defaultdict

BUCKET = 'llmo'
RAW_PREFIX = 'full_table/'
SUMMARY_KEY = 'processed/llmo_daily_summary.json.gz'

s3 = boto3.client('s3', region_name='us-east-2')
search_pattern = re.compile(r'[?&](?:q|query|p|search|prompt|text)=([^&]+)', re.IGNORECASE)


def extract_search(url):
    if not isinstance(url, str):
        return None
    m = search_pattern.search(url)
    if m:
        try:
            return unquote_plus(m.group(1))[:200]
        except Exception:
            return m.group(1)[:200]
    return None


def load_one_file(key):
    try:
        resp = s3.get_object(Bucket=BUCKET, Key=key)
        raw = resp['Body'].read()
        text = gzip.decompress(raw).decode('utf-8')
        return pd.read_csv(
            io.StringIO(text),
            usecols=['UID', 'DELIVERED', 'COMMON_NAME', 'MATCH_TYPE', 'BROWSER', 'PLATFORM', 'URL', 'VISIT_TS'],
            dtype={'UID': 'str', 'COMMON_NAME': 'str', 'MATCH_TYPE': 'str',
                   'BROWSER': 'str', 'PLATFORM': 'str', 'URL': 'str'}
        )
    except Exception as e:
        print(f'  ERR {key}: {e}', flush=True)
        return None


def compute_date_summary(day_df):
    ai = day_df[day_df['MATCH_TYPE'] == 'AI_AGENT']
    post = day_df[day_df['MATCH_TYPE'] == 'POST_AI_NON_AGENT']

    total_ai_users = int(ai['UID'].nunique())
    total_ai_clicks = int(len(ai))

    llm_grp = ai.groupby('COMMON_NAME').agg(uu=('UID', 'nunique'), cl=('UID', 'size')).reset_index()
    llm_grp = llm_grp.sort_values('uu', ascending=False)
    llms = [{'name': str(r['COMMON_NAME'] or 'Unknown'), 'unique_users': int(r['uu']), 'total_clicks': int(r['cl'])}
            for _, r in llm_grp.iterrows()]

    post_v = post[post['COMMON_NAME'].notna() & (post['COMMON_NAME'] != '')]
    att_grp = post_v.groupby('COMMON_NAME').agg(uu=('UID', 'nunique'), cl=('UID', 'size')).reset_index()
    att_grp = att_grp.sort_values('uu', ascending=False).head(50)
    attribution = [{'name': str(r['COMMON_NAME'] or 'Unknown'), 'unique_users': int(r['uu']), 'total_clicks': int(r['cl'])}
                   for _, r in att_grp.iterrows()]

    flow_df = day_df[['UID', 'VISIT_TS', 'COMMON_NAME', 'MATCH_TYPE']].sort_values(['UID', 'VISIT_TS'])
    flow_df['prev_name'] = flow_df.groupby('UID')['COMMON_NAME'].shift(1)
    flow_df['prev_type'] = flow_df.groupby('UID')['MATCH_TYPE'].shift(1)
    mask = ((flow_df['MATCH_TYPE'] == 'POST_AI_NON_AGENT') & (flow_df['prev_type'] == 'AI_AGENT')
            & flow_df['prev_name'].notna() & flow_df['COMMON_NAME'].notna() & (flow_df['COMMON_NAME'] != ''))
    ff = flow_df[mask]
    if not ff.empty:
        fg = ff.groupby(['prev_name', 'COMMON_NAME']).agg(uu=('UID', 'nunique'), cl=('UID', 'size')).reset_index()
        fg = fg.sort_values('uu', ascending=False).head(100)
        flows = [{'source': str(r['prev_name']), 'destination': str(r['COMMON_NAME']),
                  'unique_users': int(r['uu']), 'clicks': int(r['cl'])} for _, r in fg.iterrows()]
    else:
        flows = []

    s_df = ai[ai['SEARCH_TERM'].notna() & (ai['SEARCH_TERM'] != '')]
    if not s_df.empty:
        sg = s_df.groupby('SEARCH_TERM').size().reset_index(name='count').sort_values('count', ascending=False).head(50)
        searches = [{'term': str(r['SEARCH_TERM']), 'count': int(r['count'])} for _, r in sg.iterrows()]
    else:
        searches = []

    br = ai[ai['BROWSER'].notna() & (ai['BROWSER'] != '')].groupby('BROWSER')['UID'].nunique().reset_index(name='uu').sort_values('uu', ascending=False)
    browsers = [{'name': str(r['BROWSER']), 'unique_users': int(r['uu'])} for _, r in br.iterrows()]

    pl = ai[ai['PLATFORM'].notna() & (ai['PLATFORM'] != '')].groupby('PLATFORM')['UID'].nunique().reset_index(name='uu').sort_values('uu', ascending=False)
    platforms = [{'name': str(r['PLATFORM']), 'unique_users': int(r['uu'])} for _, r in pl.iterrows()]

    return {
        'total_ai_users': total_ai_users,
        'total_ai_clicks': total_ai_clicks,
        'llms': llms, 'attribution': attribution,
        'flows': flows, 'searches': searches,
        'browsers': browsers, 'platforms': platforms,
    }


def main():
    incremental = '--incremental' in sys.argv

    existing_summary = {}
    if incremental:
        print("Incremental mode: loading existing summary...", flush=True)
        try:
            resp = s3.get_object(Bucket=BUCKET, Key=SUMMARY_KEY)
            raw = resp['Body'].read()
            try:
                text = gzip.decompress(raw).decode('utf-8')
            except Exception:
                text = raw.decode('utf-8')
            data = json.loads(text)
            existing_summary = data.get('by_date', {})
            print(f"  Existing summary has {len(existing_summary)} dates", flush=True)
        except Exception as e:
            print(f"  No existing summary found ({e}), doing full build", flush=True)
            incremental = False

    print("Listing raw files...", flush=True)
    t0 = time.time()
    paginator = s3.get_paginator('list_objects_v2')
    keys = []

    if incremental:
        try:
            head = s3.head_object(Bucket=BUCKET, Key=SUMMARY_KEY)
            summary_mtime = head['LastModified']
        except Exception:
            summary_mtime = None
            incremental = False

    for page in paginator.paginate(Bucket=BUCKET, Prefix=RAW_PREFIX):
        for obj in page.get('Contents', []):
            if obj['Key'].endswith('.csv.gz'):
                if incremental and summary_mtime and obj['LastModified'] <= summary_mtime:
                    continue
                keys.append(obj['Key'])
    print(f"Found {len(keys)} files to process", flush=True)

    if not keys and incremental:
        print("No new files. Existing summary is up to date.", flush=True)
        return

    print(f"Downloading {len(keys)} files (48 threads)...", flush=True)
    dfs = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        futures = {pool.submit(load_one_file, k): k for k in keys}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(keys)} files done...", flush=True)
            r = fut.result()
            if r is not None:
                dfs.append(r)

    print(f"Concatenating {len(dfs)} DataFrames...", flush=True)
    df = pd.concat(dfs, ignore_index=True)
    del dfs
    print(f"Total rows: {len(df):,}", flush=True)

    print("Extracting search terms...", flush=True)
    df['SEARCH_TERM'] = df['URL'].apply(extract_search)
    df.drop(columns=['URL'], inplace=True)

    print("Parsing dates...", flush=True)
    df['DELIVERED'] = pd.to_datetime(df['DELIVERED'], errors='coerce').dt.date
    df['VISIT_TS'] = pd.to_datetime(df['VISIT_TS'], errors='coerce')

    print("Computing per-date summaries...", flush=True)
    summary = dict(existing_summary)
    for date_val, day_df in df.groupby('DELIVERED'):
        if pd.isna(date_val):
            continue
        ds = str(date_val)
        stats = compute_date_summary(day_df)
        summary[ds] = stats
        print(f"  {ds}: {stats['total_ai_users']:,} users, {stats['total_ai_clicks']:,} clicks", flush=True)
    del df

    print(f"\nSaving summary ({len(summary)} dates)...", flush=True)
    data = {'dates': sorted(summary.keys(), reverse=True), 'by_date': summary}
    raw_json = json.dumps(data, separators=(',', ':')).encode('utf-8')
    compressed = gzip.compress(raw_json)
    print(f"JSON: {len(raw_json)/1e6:.1f} MB raw, {len(compressed)/1e6:.1f} MB gzipped", flush=True)

    print(f"Uploading to s3://{BUCKET}/{SUMMARY_KEY}...", flush=True)
    s3.put_object(Bucket=BUCKET, Key=SUMMARY_KEY, Body=compressed,
                  ContentType='application/json', ContentEncoding='gzip')
    print(f"DONE in {time.time()-t0:.1f}s", flush=True)


if __name__ == '__main__':
    main()
