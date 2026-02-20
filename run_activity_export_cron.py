#!/usr/bin/env python3
"""Trigger activity export jobs on the web app. Run from Render cron (daily)."""
import os
import urllib.request
import urllib.error

def main():
    base = (os.environ.get('APP_URL') or 'https://behavioralgraph.onrender.com').rstrip('/')
    url = base + '/api/cron/run-activity-export-jobs'
    secret = os.environ.get('CRON_SECRET', '')
    if not secret:
        print('CRON_SECRET not set')
        exit(1)
    req = urllib.request.Request(url, method='POST', headers={'X-Cron-Secret': secret})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode()
            print(resp.status, body)
            exit(0 if resp.status == 200 else 1)
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())
        exit(1)
    except Exception as e:
        print('Error:', e)
        exit(1)

if __name__ == '__main__':
    main()
