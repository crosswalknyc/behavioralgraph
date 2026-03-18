#!/usr/bin/env python3
"""Build LLMO daily summary JSON from the existing parquet on S3."""
import sys, io, gzip, json, time, boto3, pandas as pd

BUCKET = 'llmo'
PARQUET_KEY = 'processed/llmo_processed.parquet'
SUMMARY_KEY = 'processed/llmo_daily_summary.json.gz'

s3 = boto3.client('s3', region_name='us-east-2')

print("Downloading parquet...", flush=True)
t0 = time.time()
resp = s3.get_object(Bucket=BUCKET, Key=PARQUET_KEY)
raw = resp['Body'].read()
print(f"Downloaded {len(raw)/1e9:.2f} GB in {time.time()-t0:.1f}s", flush=True)

print("Parsing parquet...", flush=True)
df = pd.read_parquet(io.BytesIO(raw))
del raw
print(f"Loaded {len(df):,} rows", flush=True)

df['DELIVERED'] = pd.to_datetime(df['DELIVERED'], errors='coerce').dt.date
df['VISIT_TS'] = pd.to_datetime(df['VISIT_TS'], errors='coerce')

import re
from urllib.parse import unquote_plus
pattern = re.compile(r'[?&](?:q|query|p|search|prompt|text)=([^&]+)', re.IGNORECASE)
def extract(url):
    if not isinstance(url, str): return None
    m = pattern.search(url)
    if m:
        try: return unquote_plus(m.group(1))[:200]
        except: return m.group(1)[:200]
    return None

if 'SEARCH_TERM' not in df.columns:
    if 'URL' in df.columns:
        print("Extracting search terms...", flush=True)
        df['SEARCH_TERM'] = df['URL'].apply(extract)
    else:
        df['SEARCH_TERM'] = None

print("Computing per-date summaries...", flush=True)
summary = {}
for date_val, day_df in df.groupby('DELIVERED'):
    if pd.isna(date_val):
        continue
    ds = str(date_val)
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

    summary[ds] = {
        'total_ai_users': total_ai_users,
        'total_ai_clicks': total_ai_clicks,
        'llms': llms, 'attribution': attribution,
        'flows': flows, 'searches': searches,
        'browsers': browsers, 'platforms': platforms,
    }
    print(f"  {ds}: {total_ai_users:,} users, {total_ai_clicks:,} clicks", flush=True)

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
