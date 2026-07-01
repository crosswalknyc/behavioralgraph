#!/usr/bin/env python3
"""
One-time: set must_reset_password=True for users who were re-created from CSV
(so they're forced to set a new password on first login). Exempt usernames keep
their current password and are not forced to reset.

Usage (from bg-webapp):
  python scripts/set_must_reset_password_for_restored.py
  python scripts/set_must_reset_password_for_restored.py --exempt admin,jane,joe

Default exempt: admin (only). Add more if you know the other "original" users.
Uses users.json in bg-webapp root. Save to S3 via app (deploy + restore-from-deployed-file) or upload.
"""

import json
import os
import argparse

USERS_FILE = os.path.join(os.path.dirname(__file__), '..', 'users.json')

def main():
    parser = argparse.ArgumentParser(description='Set must_reset_password for restored users')
    parser.add_argument('--exempt', default='admin', help='Comma-separated usernames to NOT force reset (default: admin)')
    parser.add_argument('--dry-run', action='store_true', help='Only print what would be done')
    args = parser.parse_args()
    exempt = {u.strip().lower() for u in args.exempt.split(',') if u.strip()}

    if not os.path.exists(USERS_FILE):
        print(f"Not found: {USERS_FILE}")
        return
    with open(USERS_FILE, 'r') as f:
        data = json.load(f)
    users = data.get('users', {})

    set_count = 0
    skipped = []
    for username, u in users.items():
        uname = username.lower()
        if uname in exempt:
            skipped.append(username)
            continue
        u['must_reset_password'] = True
        set_count += 1

    if args.dry_run:
        print(f"Would set must_reset_password=True for {set_count} users. Exempt: {sorted(skipped)}")
        return
    with open(USERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Set must_reset_password=True for {set_count} users. Exempt (no reset required): {sorted(skipped)}")
    print("Commit and deploy, then run restore-from-deployed-file so S3 gets this file (or upload users.json to S3).")

if __name__ == '__main__':
    main()
