#!/usr/bin/env python3
"""
Restore users from a user_details_export CSV.
- Existing users: update role, credits, credits_used, email, company, department, last_login, created_at.
  Passwords and profile_picture are preserved (not in CSV).
- New users: create with generated password; passwords written to restored_passwords.txt.

Run from bg-webapp: python restore_users_from_csv.py "/path/to/user_details_export (4).csv"
Uses users.json in the same directory (local file only). For production, upload the updated
users.json to S3 (system/users.json) or run this where the app can save to S3.
"""

import csv
import json
import os
import secrets
import hashlib

USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000)
    return f"pbkdf2:sha256:600000${salt}${pwd_hash.hex()}"

def generate_password(length=12):
    import string
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(secrets.choice(chars) for _ in range(length))

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python restore_users_from_csv.py <path-to-csv>")
        sys.exit(1)
    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        sys.exit(1)

    # Load current users
    with open(USERS_FILE, 'r') as f:
        data = json.load(f)
    users = data.get('users', {})

    # Default new-user template (access like a normal user)
    def new_user_template(username, email, company, department, role, credits, credits_used, last_login, created_at):
        return {
            'password_hash': hash_password(generate_password()),
            'email': email,
            'first_name': '',
            'last_name': '',
            'company': company,
            'department': department,
            'role': role,
            'credits': credits,
            'credits_used': credits_used,
            'created_at': created_at or None,
            'last_login': last_login or None,
            'allowed_categories': ['*'],
            'allowed_runs': ['*'],
            'allowed_behavioral_categories': ['*'],
            'has_profile_iq_access': True,
            'has_subscriber_iq_access': False,
            'has_ticket_sales_iq_access': True,
            'has_hedge_fund_iq_access': False,
            'hedge_fund_iq_tabs': [],
            'hedge_fund_iq_tickers': ['*'],
            'has_analysis_iq_access': False,
            'analysis_iq_modules': [],
            'has_ticket_sales_tracker_access': False,
            'has_rankers_iq_access': False,
            'rankers_iq_options': [],
            'has_purgatory_approval': False,
            'must_reset_password': True,
        }

    new_passwords = []
    updated = 0
    created = 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            username = (row.get('Username') or '').strip().lower()
            if not username:
                continue
            email = (row.get('Email') or '').strip().lower()
            company = (row.get('Company') or '').strip()
            department = (row.get('Department') or '').strip()
            role_raw = (row.get('Role') or '').strip().lower()
            role = role_raw if role_raw in ('super_admin', 'admin', 'user') else 'user'
            credits_str = (row.get('Credits Remaining') or '').strip()
            credits = -1 if credits_str == 'Unlimited' else int(credits_str) if credits_str.isdigit() else 5
            credits_used = int((row.get('Credits Used') or '0').strip()) if (row.get('Credits Used') or '0').strip().isdigit() else 0
            last_login_raw = (row.get('Last Login') or '').strip()
            last_login = last_login_raw.replace(' ', 'T') + ('Z' if last_login_raw and 'T' not in last_login_raw else '') if last_login_raw else None
            if last_login and len(last_login) == 19:
                last_login = last_login + '.000000'
            created_raw = (row.get('Created At') or '').strip()
            created_at = created_raw.replace(' ', 'T') if created_raw else None
            if created_at and len(created_at) == 19:
                created_at = created_at + ':00'

            if username in users:
                # Update existing: role, credits, credits_used, email, company, department, last_login, created_at
                u = users[username]
                u['role'] = role
                u['credits'] = credits
                u['credits_used'] = credits_used
                u['email'] = email
                u['company'] = company
                u['department'] = department
                if last_login:
                    u['last_login'] = last_login
                if created_at:
                    u['created_at'] = created_at
                # Never overwrite admin's role with non-super_admin
                if username == 'admin':
                    u['role'] = 'super_admin'
                updated += 1
            else:
                # Create new user with generated password
                pwd = generate_password()
                users[username] = new_user_template(username, email, company, department, role, credits, credits_used, last_login, created_at)
                users[username]['password_hash'] = hash_password(pwd)
                new_passwords.append((username, pwd, email))
                created += 1

    with open(USERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Done: {updated} updated, {created} created. Saved to {USERS_FILE}")

    if new_passwords:
        out_path = os.path.join(os.path.dirname(__file__), 'restored_passwords.txt')
        with open(out_path, 'w') as f:
            f.write("New users only - share securely; users should change password after first login.\n\n")
            for username, pwd, email in new_passwords:
                f.write(f"{username}\t{pwd}\t{email}\n")
        print(f"New user passwords written to {out_path}")
    print("For production (Render): upload the updated users.json to S3 key system/users.json, or deploy and ensure app loads this file.")

if __name__ == '__main__':
    main()
