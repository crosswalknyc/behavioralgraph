#!/usr/bin/env python3
"""Rewrite admin (or other) password hash on S3-backed users.json (dashboard-inputs).

Used when Render dashboard login is unknown. Requires AWS creds locally (same as prod).

Example:
  export AWS_PROFILE=your-profile   # or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
  python scripts/reset_admin_password.py admin 'YourNewStrongPassword!'
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys

try:
    import boto3
except ImportError:
    print("Install boto3: pip install boto3", file=sys.stderr)
    sys.exit(1)


S3_BUCKET = "dashboard-inputs"
S3_USERS_KEY = "system/users.json"


def hash_password(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 600000
    )
    return f"pbkdf2:sha256:600000${salt}${pwd_hash.hex()}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("username", nargs="?", default="admin")
    p.add_argument("password", help="New plaintext password (wrap in quotes if shell meta-chars)")
    args = p.parse_args()

    s3 = boto3.client("s3")
    raw = s3.get_object(Bucket=S3_BUCKET, Key=S3_USERS_KEY)["Body"].read().decode("utf-8")
    data = json.loads(raw)
    users = data.setdefault("users", {})
    user = users.get(args.username.strip().lower())
    if not user:
        print(f"User '{args.username}' not found — create via dashboard first.", file=sys.stderr)
        sys.exit(2)

    user["password_hash"] = hash_password(args.password)
    user.pop("must_reset_password", None)

    payload = json.dumps(data, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=S3_USERS_KEY,
        Body=payload,
        ContentType="application/json",
    )
    print(f"Updated password for '{args.username}' in s3://{S3_BUCKET}/{S3_USERS_KEY}")


if __name__ == "__main__":
    main()
