#!/usr/bin/env python3
"""ETag-guarded read-modify-write for shared JSON state in S3.

Shared JSON files (system/users.json, system/s3_cache.json,
metadata/admin_quick_selects.json, system/recent_subject_raws.json)
are mutated concurrently by ~21 queue workers plus the Flask app. A
bare GET -> mutate -> PUT loses writes whenever two writers overlap.

S3 supports conditional writes: PutObject with IfMatch=<etag> fails
with 412 PreconditionFailed if the object changed since the GET, and
IfNoneMatch='*' fails with 412 if the object already exists. This
module wraps the GET/mutate/conditional-PUT/retry loop so every
caller gets last-writer-loses-nothing semantics.

Usage:

    from migration.s3_json_state import update_json

    def mutate(obj):
        obj.setdefault('jobs', []).append(new_job)
        return obj           # return None to abort without writing

    update_json('dashboard-inputs', 'system/s3_cache.json', mutate)
"""
from __future__ import annotations

import json
import random
import time

import boto3
from botocore.exceptions import ClientError

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client('s3')
    return _s3


def read_json_with_etag(bucket: str, key: str, s3=None):
    """GET the object and return (parsed_json, etag).

    Returns (None, None) when the key does not exist. Raises on any
    other S3 error (caller decides whether that is fatal).
    """
    s3 = s3 or _client()
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('NoSuchKey', '404'):
            return None, None
        raise
    body = resp['Body'].read().decode('utf-8')
    etag = resp.get('ETag')
    try:
        obj = json.loads(body) if body.strip() else None
    except Exception:
        # Corrupt JSON: surface the raw state as None so the mutate_fn
        # can rebuild from scratch rather than crash every writer.
        obj = None
    return obj, etag


def _is_precondition_failed(err: ClientError) -> bool:
    code = (err.response.get('Error') or {}).get('Code', '')
    status = (err.response.get('ResponseMetadata') or {}).get('HTTPStatusCode')
    return code in ('PreconditionFailed', '412') or status == 412


def update_json(bucket: str, key: str, mutate_fn, max_retries: int = 5,
                default=None, content_type: str = 'application/json',
                s3=None, put_extra_args: dict | None = None,
                indent: int | None = 2):
    """GET -> mutate_fn(obj) -> conditional PUT, retrying on 412.

    mutate_fn receives the parsed JSON (or `default` when the key does
    not exist / is corrupt) and must return the object to write, or
    None to abort without writing. On a 412 conflict the fresh object
    is re-fetched and mutate_fn re-applied, so mutate_fn must be safe
    to call multiple times against different snapshots.

    Returns the object that was written, or None if mutate_fn aborted.
    Raises RuntimeError after max_retries consecutive conflicts.
    """
    s3 = s3 or _client()
    last_err = None
    for attempt in range(max_retries + 1):
        obj, etag = read_json_with_etag(bucket, key, s3=s3)
        if obj is None:
            obj = json.loads(json.dumps(default)) if default is not None else {}
        new_obj = mutate_fn(obj)
        if new_obj is None:
            return None
        body = json.dumps(new_obj, indent=indent, default=str).encode('utf-8')
        put_kwargs = dict(Bucket=bucket, Key=key, Body=body,
                          ContentType=content_type)
        if put_extra_args:
            put_kwargs.update(put_extra_args)
        if etag:
            put_kwargs['IfMatch'] = etag.strip('"')
        else:
            put_kwargs['IfNoneMatch'] = '*'
        try:
            s3.put_object(**put_kwargs)
            return new_obj
        except ClientError as e:
            if not _is_precondition_failed(e):
                raise
            last_err = e
            # Conflict: another writer landed between our GET and PUT.
            time.sleep(min(0.25 * (2 ** attempt), 4.0)
                       + random.uniform(0, 0.25))
    raise RuntimeError(
        f"update_json: {max_retries + 1} consecutive write conflicts on "
        f"s3://{bucket}/{key}") from last_err
