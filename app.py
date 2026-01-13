#!/usr/bin/env python3
"""
Behavioral Graph Web Application
================================
A Flask-based web interface for the BG.py behavioral analysis pipeline.
Session-based authentication with user credits and admin portal.
Includes S3 caching for existing results.
"""

import os
import sys
import uuid
import json
import threading
import traceback
import re
import io
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session
from flask_cors import CORS
import pandas as pd
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# Add parent directory to path for importing bg module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# ============================================================================
# CONFIGURATION
# ============================================================================

S3_BUCKET = 'dashboard-inputs'
S3_REGION = os.environ.get('AWS_REGION', 'us-east-1')
USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

# Initialize S3 client
try:
    s3_client = boto3.client(
        's3',
        region_name=S3_REGION,
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
    )
except Exception as e:
    print(f"Warning: S3 client initialization failed: {e}")
    s3_client = None

# ============================================================================
# USER MANAGEMENT
# ============================================================================

def hash_password(password, salt=None):
    """Hash a password with PBKDF2."""
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000)
    return f"pbkdf2:sha256:600000${salt}${pwd_hash.hex()}"

def verify_password(stored_hash, password):
    """Verify a password against its hash."""
    try:
        parts = stored_hash.split('$')
        if len(parts) != 3:
            return False
        salt = parts[1]
        stored_pwd_hash = parts[2]
        computed_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000)
        return computed_hash.hex() == stored_pwd_hash
    except Exception:
        return False

S3_USERS_KEY = 'system/users.json'  # S3 key for persistent user storage

def load_users():
    """Load users from S3 (persistent) or local file (fallback)."""
    # Try S3 first for persistence across Render restarts
    if s3_client:
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_USERS_KEY)
            data = json.loads(response['Body'].read().decode('utf-8'))
            print(f"✅ Loaded users from S3")
            # Also save locally for faster subsequent reads
            with open(USERS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            return data
        except s3_client.exceptions.NoSuchKey:
            print("📁 No users.json in S3 yet, will create on first save")
        except Exception as e:
            print(f"⚠️ S3 load failed: {e}, trying local file")
    
    # Fall back to local file
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading local users: {e}")
    
    # Return default structure if nothing exists
    return {
        "users": {
            "admin": {
                "password_hash": hash_password("midgenow!2"),
                "role": "admin",
                "credits": -1,
                "credits_used": 0,
                "created_at": datetime.now().isoformat(),
                "last_login": None,
                "allowed_categories": ["*"],
                "allowed_runs": ["*"]
            }
        },
        "categories": {},
        "runs": {}
    }

def save_users(data):
    """Save users to both S3 (persistent) and local file."""
    success = True
    
    # Save to local file first (fast)
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving local users: {e}")
        success = False
    
    # Save to S3 for persistence across Render restarts
    if s3_client:
        try:
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=S3_USERS_KEY,
                Body=json.dumps(data, indent=2),
                ContentType='application/json'
            )
            print(f"✅ Users saved to S3")
        except Exception as e:
            print(f"⚠️ Error saving users to S3: {e}")
            success = False
    
    return success

def is_valid_hash(hash_str):
    """Check if a password hash looks valid (has proper structure and length)."""
    if not hash_str or 'placeholder' in hash_str:
        return False
    parts = hash_str.split('$')
    # Valid hash should have 3 parts and the hash part should be 64 chars (SHA256 hex)
    return len(parts) == 3 and len(parts[2]) == 64

def init_users():
    """Initialize users file with default users if it doesn't exist or has placeholder passwords."""
    data = load_users()
    changed = False
    
    # Check for admin user
    if 'admin' not in data['users']:
        data['users']['admin'] = {
            "password_hash": hash_password("midgenow!2"),
            "role": "admin",
            "credits": -1,
            "credits_used": 0,
            "created_at": datetime.now().isoformat(),
            "last_login": None,
            "allowed_categories": ["*"],
            "allowed_runs": ["*"]
        }
        changed = True
    elif not is_valid_hash(data['users']['admin'].get('password_hash', '')):
        data['users']['admin']['password_hash'] = hash_password("midgenow!2")
        changed = True
    
    # Check for liz user
    if 'liz' not in data['users']:
        data['users']['liz'] = {
            "password_hash": hash_password("ZestyBuffalo"),
            "role": "enterprise",
            "credits": 5,
            "credits_used": 0,
            "created_at": datetime.now().isoformat(),
            "last_login": None,
            "allowed_categories": ["*"],
            "allowed_runs": ["*"]
        }
        changed = True
    elif not is_valid_hash(data['users']['liz'].get('password_hash', '')):
        data['users']['liz']['password_hash'] = hash_password("ZestyBuffalo")
        data['users']['liz']['role'] = "enterprise"
        changed = True
    
    # Check for jessie user
    if 'jessie' not in data['users']:
        data['users']['jessie'] = {
            "password_hash": hash_password("SpicySriracha"),
            "role": "enterprise",
            "credits": 5,
            "credits_used": 0,
            "created_at": datetime.now().isoformat(),
            "last_login": None,
            "allowed_categories": ["*"],
            "allowed_runs": ["*"]
        }
        changed = True
    elif not is_valid_hash(data['users']['jessie'].get('password_hash', '')):
        data['users']['jessie']['password_hash'] = hash_password("SpicySriracha")
        data['users']['jessie']['role'] = "enterprise"
        changed = True
    
    if changed:
        save_users(data)
    
    return data

# Initialize users on startup
init_users()

def get_current_user():
    """Get current logged-in user data."""
    if 'username' not in session:
        return None
    data = load_users()
    return data['users'].get(session['username'])

def check_user_credits(username):
    """Check if user has credits remaining. Returns (has_credits, credits_left)."""
    data = load_users()
    user = data['users'].get(username)
    if not user:
        return False, 0
    
    # -1 means unlimited
    if user['credits'] == -1:
        return True, -1
    
    return user['credits'] > 0, user['credits']

def consume_credit(username):
    """Consume one credit from user. Returns True if successful."""
    data = load_users()
    user = data['users'].get(username)
    if not user:
        return False
    
    # -1 means unlimited
    if user['credits'] == -1:
        user['credits_used'] = user.get('credits_used', 0) + 1
        save_users(data)
        return True
    
    if user['credits'] <= 0:
        return False
    
    user['credits'] -= 1
    user['credits_used'] = user.get('credits_used', 0) + 1
    save_users(data)
    return True

# ============================================================================
# AUTHENTICATION DECORATORS
# ============================================================================

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def requires_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login_page'))
        user = get_current_user()
        if not user or user.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# Store for job status and results
jobs = {}
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================================
# S3 CACHE FUNCTIONS
# ============================================================================

def normalize_brand_for_search(brand):
    """Normalize brand name for consistent matching."""
    return brand.lower().strip().replace(' ', '_').replace('.', '')

def parse_metadata_from_csv(csv_content):
    """Extract metadata from the INPUT_METADATA row of a CSV."""
    try:
        lines = csv_content.split('\n')
        for line in lines:
            if line.startswith('INPUT_METADATA,'):
                parts = line.split(',')
                if len(parts) >= 2:
                    metadata_str = parts[1]
                    # Parse: BRAND:xxx_SAMPLE_START:xxx_SAMPLE_END:xxx_BEHAVIOR_START:xxx_BEHAVIOR_END:xxx_SEED:xxx
                    metadata = {}
                    pairs = metadata_str.split('_')
                    for i, pair in enumerate(pairs):
                        if ':' in pair:
                            key, value = pair.split(':', 1)
                            # Handle compound keys like SAMPLE_START
                            if key in ['SAMPLE', 'BEHAVIOR']:
                                if i + 1 < len(pairs) and ':' in pairs[i + 1]:
                                    next_key, next_value = pairs[i + 1].split(':', 1)
                                    metadata[f'{key}_{next_key}'] = next_value
                            else:
                                metadata[key] = value
                    return metadata
        return None
    except Exception as e:
        print(f"Error parsing metadata: {e}")
        return None

def extract_demographics_from_csv(csv_content):
    """Extract demographic distributions from CSV for comparison."""
    demographics = {}
    try:
        lines = csv_content.split('\n')
        for line in lines:
            parts = line.split(',')
            if len(parts) >= 3:
                category = parts[0].upper()
                if category in ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS']:
                    value = parts[1]
                    try:
                        percentage = float(parts[2])
                        if category not in demographics:
                            demographics[category] = {}
                        demographics[category][value] = percentage
                    except (ValueError, IndexError):
                        pass
        return demographics
    except Exception as e:
        print(f"Error extracting demographics: {e}")
        return {}

def extract_sample_size_from_csv(csv_content):
    """Extract sample size from CSV."""
    try:
        lines = csv_content.split('\n')
        for line in lines:
            if line.startswith('SAMPLE SIZE,'):
                parts = line.split(',')
                if len(parts) >= 4:
                    return int(parts[3])
        return None
    except Exception as e:
        print(f"Error extracting sample size: {e}")
        return None

def compare_demographics(existing_demos, tolerance_percent=5):
    """
    Return True if demographics should be considered valid for use.
    This is a baseline check - actual validation happens during comparison.
    """
    return bool(existing_demos)

def check_s3_for_existing(brand_search, start_date, end_date):
    """
    Check S3 bucket for existing results matching the brand and dates.
    Returns: (exact_match_file, similar_files_with_different_dates)
    """
    if not s3_client:
        return None, []
    
    normalized_brand = normalize_brand_for_search(brand_search)
    exact_match = None
    similar_files = []
    
    try:
        # List all objects in the bucket
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.csv'):
                    continue
                
                # Check if filename contains the brand
                filename_lower = key.lower()
                if normalized_brand not in filename_lower and brand_search.lower() not in filename_lower:
                    continue
                
                # Download and check the file's metadata
                try:
                    response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                    csv_content = response['Body'].read().decode('utf-8')
                    metadata = parse_metadata_from_csv(csv_content)
                    
                    if metadata:
                        file_brand = metadata.get('BRAND', '').lower()
                        file_start = metadata.get('SAMPLE_START', '')
                        file_end = metadata.get('SAMPLE_END', '')
                        
                        # Check for exact match (same brand AND same dates)
                        if (normalized_brand in file_brand or brand_search.lower() in file_brand):
                            if file_start == start_date and file_end == end_date:
                                # Exact match!
                                exact_match = {
                                    'key': key,
                                    'content': csv_content,
                                    'metadata': metadata,
                                    'demographics': extract_demographics_from_csv(csv_content),
                                    'sample_size': extract_sample_size_from_csv(csv_content),
                                    'last_modified': obj['LastModified'].isoformat()
                                }
                            else:
                                # Same brand, different dates
                                similar_files.append({
                                    'key': key,
                                    'content': csv_content,
                                    'metadata': metadata,
                                    'demographics': extract_demographics_from_csv(csv_content),
                                    'sample_size': extract_sample_size_from_csv(csv_content),
                                    'start_date': file_start,
                                    'end_date': file_end,
                                    'last_modified': obj['LastModified'].isoformat()
                                })
                except Exception as e:
                    print(f"Error reading {key}: {e}")
                    continue
                    
    except ClientError as e:
        print(f"S3 error: {e}")
    except NoCredentialsError:
        print("AWS credentials not configured")
    except Exception as e:
        print(f"Unexpected error checking S3: {e}")
    
    return exact_match, similar_files

def validate_demographics_consistency(new_demographics, existing_demographics, tolerance=5):
    """
    Check if new demographics are within tolerance of existing demographics.
    Returns: (is_valid, discrepancies)
    """
    discrepancies = []
    
    for category, existing_values in existing_demographics.items():
        if category not in new_demographics:
            continue
        
        new_values = new_demographics[category]
        
        for value, existing_pct in existing_values.items():
            if value in new_values:
                new_pct = new_values[value]
                diff = abs(new_pct - existing_pct)
                
                if diff > tolerance:
                    discrepancies.append({
                        'category': category,
                        'value': value,
                        'existing': existing_pct,
                        'new': new_pct,
                        'difference': diff
                    })
    
    is_valid = len(discrepancies) == 0
    return is_valid, discrepancies

def upload_to_s3(file_path, brand_name, start_date, end_date):
    """Upload a result file to S3."""
    if not s3_client:
        return None
    
    try:
        timestamp = datetime.now().strftime('%m_%d_%Y_%H_%M')
        s3_key = f"{brand_name}_{timestamp}.csv"
        
        s3_client.upload_file(file_path, S3_BUCKET, s3_key)
        return s3_key
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        return None

# ============================================================================
# ROUTES
# ============================================================================

# ============================================================================
# LOGIN / LOGOUT ROUTES
# ============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'GET':
        # If already logged in, redirect to home
        if 'username' in session:
            return redirect(url_for('index'))
        return render_template('login.html')
    
    # POST - handle login
    try:
        data = request.json
        username = data.get('username', '').strip().lower()
        password = data.get('password', '')
        
        users_data = load_users()
        user = users_data['users'].get(username)
        
        if not user:
            return jsonify({'success': False, 'error': 'Invalid username or password'})
        
        if not verify_password(user['password_hash'], password):
            return jsonify({'success': False, 'error': 'Invalid username or password'})
        
        # Update last login
        user['last_login'] = datetime.now().isoformat()
        save_users(users_data)
        
        # Set session
        session['username'] = username
        session['role'] = user.get('role', 'user')
        
        redirect_url = '/admin' if user.get('role') == 'admin' else '/'
        return jsonify({'success': True, 'redirect': redirect_url})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/admin')
@requires_admin
def admin_portal():
    return render_template('admin.html')

# ============================================================================
# ADMIN API ROUTES
# ============================================================================

@app.route('/api/admin/users', methods=['GET'])
@requires_admin
def get_all_users():
    """Get all users (without password hashes)."""
    data = load_users()
    safe_users = {}
    for username, user in data['users'].items():
        safe_users[username] = {k: v for k, v in user.items() if k != 'password_hash'}
    return jsonify({'success': True, 'users': safe_users})

@app.route('/api/admin/users', methods=['POST'])
@requires_admin
def create_user():
    """Create a new user."""
    try:
        req_data = request.json
        username = req_data.get('username', '').strip().lower()
        password = req_data.get('password', '')
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Username and password required'})
        
        if len(username) < 2:
            return jsonify({'success': False, 'error': 'Username must be at least 2 characters'})
        
        data = load_users()
        
        if username in data['users']:
            return jsonify({'success': False, 'error': 'Username already exists'})
        
        data['users'][username] = {
            'password_hash': hash_password(password),
            'role': req_data.get('role', 'user'),
            'credits': req_data.get('credits', 5),
            'credits_used': 0,
            'created_at': datetime.now().isoformat(),
            'last_login': None,
            'allowed_categories': req_data.get('allowed_categories', ['*']),
            'allowed_runs': req_data.get('allowed_runs', ['*'])
        }
        
        save_users(data)
        return jsonify({'success': True, 'message': f'User {username} created'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/users/<username>', methods=['PUT'])
@requires_admin
def update_user(username):
    """Update an existing user."""
    try:
        req_data = request.json
        data = load_users()
        
        if username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        user = data['users'][username]
        
        # Update allowed fields
        if 'password' in req_data and req_data['password']:
            user['password_hash'] = hash_password(req_data['password'])
        if 'role' in req_data:
            user['role'] = req_data['role']
        if 'credits' in req_data:
            user['credits'] = req_data['credits']
        if 'allowed_categories' in req_data:
            user['allowed_categories'] = req_data['allowed_categories']
        if 'allowed_runs' in req_data:
            user['allowed_runs'] = req_data['allowed_runs']
        
        save_users(data)
        return jsonify({'success': True, 'message': f'User {username} updated'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/admin/users/<username>', methods=['DELETE'])
@requires_admin
def delete_user(username):
    """Delete a user."""
    try:
        if username == 'admin':
            return jsonify({'success': False, 'error': 'Cannot delete admin user'})
        
        data = load_users()
        
        if username not in data['users']:
            return jsonify({'success': False, 'error': 'User not found'})
        
        del data['users'][username]
        save_users(data)
        return jsonify({'success': True, 'message': f'User {username} deleted'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/user/info')
@requires_auth
def get_user_info():
    """Get current user info including credits."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'})
    
    return jsonify({
        'success': True,
        'username': session['username'],
        'role': user.get('role', 'user'),
        'credits': user.get('credits', 0),
        'credits_used': user.get('credits_used', 0),
        'allowed_categories': user.get('allowed_categories', ['*']),
        'allowed_runs': user.get('allowed_runs', ['*'])
    })

# ============================================================================
# MAIN ROUTES
# ============================================================================

@app.route('/')
@requires_auth
def index():
    user = get_current_user()
    return render_template('index.html', 
                           username=session.get('username'),
                           role=user.get('role', 'user') if user else 'user',
                           credits=user.get('credits', 0) if user else 0,
                           credits_used=user.get('credits_used', 0) if user else 0)


@app.route('/api/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})


@app.route('/api/check-cache', methods=['POST'])
@requires_auth
def check_cache():
    """Check S3 for existing results before running analysis."""
    try:
        data = request.json
        brand = data.get('brands', '').split(',')[0].strip()  # Use first brand for matching
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        
        if not brand or not start_date or not end_date:
            return jsonify({'found': False, 'message': 'Missing required fields'})
        
        exact_match, _ = check_s3_for_existing(brand, start_date, end_date)
        
        # Only return cached result for EXACT match (same brand AND same dates)
        if exact_match:
            return jsonify({
                'found': True,
                'type': 'exact',
                'file': exact_match['key'],
                'last_modified': exact_match['last_modified'],
                'sample_size': exact_match['sample_size'],
                'demographics': exact_match['demographics'],
                'message': f"Exact match found! File created {exact_match['last_modified']}"
            })
        
        # No exact match found - new analysis required
        return jsonify({
            'found': False,
            'message': 'No exact match found - new analysis required'
        })
        
    except Exception as e:
        return jsonify({'found': False, 'error': str(e)})


@app.route('/api/download-cached/<path:s3_key>')
@requires_auth
def download_cached(s3_key):
    """Download a cached file from S3."""
    if not s3_client:
        return jsonify({'error': 'S3 not configured'}), 500
    
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read()
        
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={s3_key}'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-csv-data/<path:s3_key>')
@requires_auth
def get_csv_data(s3_key):
    """Get CSV data as JSON for dashboard display."""
    print(f"📥 get_csv_data called for: {s3_key}")
    
    if not s3_client:
        print("❌ S3 client not configured")
        return jsonify({'success': False, 'error': 'S3 not configured'}), 500
    
    try:
        print(f"📂 Fetching from S3: {S3_BUCKET}/{s3_key}")
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        csv_content = response['Body'].read().decode('utf-8')
        print(f"✅ Got CSV content: {len(csv_content)} bytes")
        
        # Parse CSV
        df = pd.read_csv(io.StringIO(csv_content))
        # Replace NaN with None (which becomes null in JSON)
        df = df.fillna('')
        print(f"✅ Parsed CSV: {len(df)} rows")
        
        # Extract brand name from filename
        # Format: NAME_MM_DD_YYYY_HH_MM.csv where NAME can have multiple underscores
        name_without_ext = s3_key.replace('.csv', '')
        match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
        if match:
            brand_name = match.group(1).replace('_', ' ')
        else:
            brand_name = name_without_ext.replace('_', ' ')
        
        date_range = ''
        
        # Try to get from INPUT_METADATA
        metadata_rows = df[df['Column'] == 'INPUT_METADATA']
        if not metadata_rows.empty:
            metadata_value = str(metadata_rows.iloc[0]['Value'])
            if 'SAMPLE_START:' in metadata_value and 'SAMPLE_END:' in metadata_value:
                start = metadata_value.split('SAMPLE_START:')[1].split('_')[0]
                end = metadata_value.split('SAMPLE_END:')[1].split('_')[0]
                date_range = f"{start} - {end}"
        
        # Convert to records
        data = df.to_dict('records')
        
        print(f"✅ Returning data for brand: {brand_name.upper()}")
        return jsonify({
            'success': True,
            'data': data,
            'brand': brand_name.upper(),
            'date_range': date_range,
            's3_key': s3_key
        })
    except Exception as e:
        print(f"❌ Error in get_csv_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 's3_key': s3_key}), 500


@app.route('/api/job-data/<job_id>')
@requires_auth
def get_job_data(job_id):
    """Get job result data as JSON for dashboard display."""
    if job_id not in jobs:
        return jsonify({'success': False, 'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    if job['status'] != 'completed':
        return jsonify({'success': False, 'error': 'Job not completed'}), 400
    
    if not job['result_file'] or not os.path.exists(job['result_file']):
        return jsonify({'success': False, 'error': 'Result file not found'}), 404
    
    try:
        df = pd.read_csv(job['result_file'])
        # Replace NaN with empty string for valid JSON
        df = df.fillna('')
        
        # Extract brand and date range
        brand_name = job['project_name']
        date_range = ''
        
        metadata_rows = df[df['Column'] == 'INPUT_METADATA']
        if not metadata_rows.empty:
            metadata_value = str(metadata_rows.iloc[0]['Value'])
            if 'BRAND:' in metadata_value:
                brand_name = metadata_value.split('BRAND:')[1].split('_')[0]
            if 'SAMPLE_START:' in metadata_value and 'SAMPLE_END:' in metadata_value:
                start = metadata_value.split('SAMPLE_START:')[1].split('_')[0]
                end = metadata_value.split('SAMPLE_END:')[1].split('_')[0]
                date_range = f"{start} - {end}"
        
        data = df.to_dict('records')
        
        return jsonify({
            'success': True,
            'data': data,
            'brand': brand_name.upper(),
            'date_range': date_range
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/submit', methods=['POST'])
@requires_auth
def submit_analysis():
    """Submit a new behavioral graph analysis job."""
    try:
        # Check user credits first
        username = session.get('username')
        has_credits, credits_left = check_user_credits(username)
        
        if not has_credits:
            return jsonify({
                'error': 'No credits remaining. Please contact an administrator to get more credits.',
                'credits_left': 0
            }), 403
        
        data = request.json
        
        # Validate required fields (support both old and new date field names)
        if not data.get('project_name'):
            return jsonify({'error': 'Missing required field: project_name'}), 400
        if not data.get('brands'):
            return jsonify({'error': 'Missing required field: brands'}), 400
        if not (data.get('sample_start') or data.get('start_date')):
            return jsonify({'error': 'Missing required field: start date'}), 400
        if not (data.get('sample_end') or data.get('end_date')):
            return jsonify({'error': 'Missing required field: end date'}), 400
        
        # Create job ID
        job_id = str(uuid.uuid4())[:8]
        
        # Parse inputs
        project_name = data['project_name'].replace(' ', '_')
        project_name = re.sub(r'[<>:"/\\|?*]', '_', project_name)
        
        # Parse brands
        brands_raw = data['brands'].replace('\n', ',')
        brands = []
        for b in brands_raw.split(','):
            b = b.strip()
            if not b:
                continue
            match = re.search(r'https?://([^/]+)', b)
            clean_brand = match.group(1).lower() if match else b.lower()
            brands.append(clean_brand)
        
        # Auto-format brand variations
        expanded_brands = []
        for brand in brands:
            expanded_brands.append(brand)
            if '.' in brand:
                expanded_brands.append(brand.replace('.', ''))
            if ' ' in brand:
                expanded_brands.append(brand.replace(' ', ''))
                expanded_brands.append(brand.replace(' ', '-'))
        brands = list(set(expanded_brands))
        
        # Parse dates
        try:
            # Support both old format (start_date/end_date) and new format (sample_start/sample_end)
            start_date = data.get('sample_start') or data.get('start_date')
            end_date = data.get('sample_end') or data.get('end_date')
            behavior_start = data.get('behavior_start') or start_date
            behavior_end = data.get('behavior_end') or end_date
            
            start_date = datetime.strptime(start_date, '%Y-%m-%d').strftime('%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d').strftime('%Y-%m-%d')
            behavior_start = datetime.strptime(behavior_start, '%Y-%m-%d').strftime('%Y-%m-%d')
            behavior_end = datetime.strptime(behavior_end, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        # Optional parameters
        is_genpop = data.get('is_genpop', True)
        purchasers_only = data.get('purchasers_only', False)
        brand_category = data.get('brand_category', 'GENERAL')
        include_frequency = data.get('include_frequency', False)
        is_listener_watcher = data.get('is_listener_watcher', False)
        platform_name = data.get('platform_name', None) if is_listener_watcher else None
        previous_file = data.get('previous_file', None) if data.get('is_update', False) else None
        
        # Get reference file for demographic consistency (from similar runs)
        reference_demographics = None
        reference_sample_size = None
        if data.get('reference_file_key'):
            try:
                _, similar_files = check_s3_for_existing(brands[0], start_date, end_date)
                for f in similar_files:
                    if f['key'] == data['reference_file_key']:
                        reference_demographics = f['demographics']
                        reference_sample_size = f['sample_size']
                        break
            except:
                pass
        
        # Demographic filters - support both object format and individual fields
        filters = data.get('filters', {})
        if not filters:
            filters = {}
            for demo_field in ['gender', 'age', 'ethnicity', 'income', 'education', 'relationship', 'sexual_orientation', 'parental_status']:
                if data.get(demo_field):
                    filters[demo_field.upper()] = [data[demo_field]]
        
        # Skew settings - support both object format and individual fields
        skew_settings = data.get('skew_settings', {})
        if not skew_settings:
            skew_settings = {}
            if data.get('enable_skew', False) and data.get('skew_category') and data.get('skew_target'):
                targets = [t.strip() for t in data['skew_target'].split(',')]
                skew_settings[data['skew_category']] = {
                    'target': targets,
                    'strength': data.get('skew_strength', 'medium')
                }
        
        # Initialize job with simpler status tracking
        jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'message': 'Queued',
            'created_at': datetime.now().isoformat(),
            'project_name': project_name,
            'brands': brands[0] if brands else project_name,
            'result_file': None,
            'error': None,
            'logs': [],
            'reference_demographics': reference_demographics,
            'reference_sample_size': reference_sample_size
        }
        
        # Start processing
        thread = threading.Thread(
            target=run_analysis,
            args=(job_id, project_name, brands, start_date, end_date, 
                  behavior_start, behavior_end, filters, skew_settings, 
                  is_genpop, purchasers_only, brand_category, 
                  include_frequency, is_listener_watcher, platform_name, previous_file,
                  reference_demographics, reference_sample_size)
        )
        thread.daemon = True
        thread.start()
        
        # Consume credit for this run
        consume_credit(username)
        
        # Get updated credits
        _, credits_left = check_user_credits(username)
        
        return jsonify({
            'job_id': job_id,
            'message': 'Analysis job submitted successfully',
            'status': 'queued',
            'credits_left': credits_left,
            'brands_count': len(brands)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status/<job_id>')
@requires_auth
def get_job_status(job_id):
    """Get simplified status of a specific job."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    return jsonify({
        'job_id': job_id,
        'project_name': job.get('project_name', ''),
        'status': job['status'],
        'progress': job['progress'],
        'message': job['message'],
        'created_at': job['created_at'],
        'error': job['error'],
        'result_file': job['result_file'],
        'demographic_validation': job.get('demographic_validation')
    })


@app.route('/api/download/<job_id>')
@requires_auth
def download_result(job_id):
    """Download the result CSV file for a completed job."""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    job = jobs[job_id]
    if job['status'] != 'completed':
        return jsonify({'error': 'Job not completed yet'}), 400
    
    if not job['result_file'] or not os.path.exists(job['result_file']):
        return jsonify({'error': 'Result file not found'}), 404
    
    return send_file(
        job['result_file'],
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"{job['project_name']}_behavioral_graph.csv"
    )


# Cache for S3 file list
s3_cache = {
    'jobs': [],
    'categories': [],
    'last_updated': None,
    'file_count': 0
}
S3_CACHE_TTL = 300  # 5 minutes cache

@app.route('/api/jobs')
@requires_auth
def list_jobs():
    """List all jobs (local + S3 cached) with caching for performance."""
    import time
    
    job_list = []
    categories = set()
    
    # Add local jobs (always fresh)
    for job_id, job in jobs.items():
        job_list.append({
            'job_id': job_id,
            'project_name': job['project_name'],
            'status': job['status'],
            'progress': job['progress'],
            'created_at': job['created_at'],
            'source': 'local',
            'category': 'LOCAL'
        })
        categories.add('LOCAL')
    
    # Check if we need to refresh S3 cache
    now = time.time()
    need_refresh = (
        s3_cache['last_updated'] is None or
        (now - s3_cache['last_updated']) > S3_CACHE_TTL
    )
    
    if need_refresh and s3_client:
        refresh_s3_cache()
    
    # Add cached S3 jobs
    job_list.extend(s3_cache['jobs'])
    for cat in s3_cache['categories']:
        categories.add(cat)
    
    # Sort by created_at descending
    sorted_jobs = sorted(job_list, key=lambda x: x['created_at'], reverse=True)
    
    return jsonify({
        'jobs': sorted_jobs,
        'categories': sorted(list(categories))
    })


@app.route('/api/refresh-cache')
@requires_auth
def force_refresh_cache():
    """Force refresh the S3 cache."""
    if s3_client:
        refresh_s3_cache()
        return jsonify({'success': True, 'count': len(s3_cache['jobs'])})
    return jsonify({'success': False, 'error': 'S3 not configured'})


def refresh_s3_cache():
    """Refresh the S3 file cache in background."""
    import time
    
    job_list = []
    categories = set()
    
    try:
        # First, just get the list of files (fast)
        paginator = s3_client.get_paginator('list_objects_v2')
        all_objects = []
        
        for page in paginator.paginate(Bucket=S3_BUCKET):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('.csv'):
                    all_objects.append(obj)
        
        # Process each file
        for obj in all_objects:
            key = obj['Key']
            
            # Extract project name from filename
            # File format: NAME_MM_DD_YYYY_HH_MM.csv where NAME can have multiple underscores
            import re
            name_without_ext = key.replace('.csv', '')
            # Remove the date/time pattern at the end: _MM_DD_YYYY_HH_MM
            match = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', name_without_ext)
            if match:
                project_name = match.group(1).replace('_', ' ').upper()
            else:
                # Fallback: just use the whole name without extension
                project_name = name_without_ext.replace('_', ' ').upper()
            
            # Try to get category from BRAND CATEGORY row in CSV
            category = 'UNCATEGORIZED'
            try:
                # Get file size first
                head_response = s3_client.head_object(Bucket=S3_BUCKET, Key=key)
                file_size = head_response['ContentLength']
                
                # Read last 100KB where BRAND CATEGORY row usually is
                start_byte = max(0, file_size - 100000)
                response = s3_client.get_object(Bucket=S3_BUCKET, Key=key, Range=f'bytes={start_byte}-{file_size}')
                content = response['Body'].read().decode('utf-8', errors='ignore')
                
                # Look for BRAND CATEGORY row
                for line in content.split('\n'):
                    if line.startswith('BRAND CATEGORY,'):
                        parts = line.split(',')
                        if len(parts) >= 2 and parts[1].strip():
                            cat = parts[1].strip().upper()
                            if cat:
                                category = cat
                        break
            except Exception as e:
                print(f"Error reading category from {key}: {e}")
            
            categories.add(category)
            
            job_list.append({
                'job_id': key,
                'project_name': project_name,
                'status': 'cached',
                'progress': 100,
                'created_at': obj['LastModified'].isoformat(),
                'source': 's3',
                's3_key': key,
                'category': category
            })
        
        # Update cache
        s3_cache['jobs'] = job_list
        s3_cache['categories'] = list(categories)
        s3_cache['last_updated'] = time.time()
        s3_cache['file_count'] = len(job_list)
        
        print(f"✅ S3 cache refreshed: {len(job_list)} files")
        
    except Exception as e:
        print(f"Error refreshing S3 cache: {e}")


# ============================================================================
# ANALYSIS RUNNER
# ============================================================================

def update_job_status(job_id, status=None, progress=None, message=None, error=None, result_file=None, demographic_validation=None):
    """Update job status - simplified to avoid verbose terminal output."""
    if job_id in jobs:
        if status:
            jobs[job_id]['status'] = status
        if progress is not None:
            jobs[job_id]['progress'] = progress
        if message:
            jobs[job_id]['message'] = message
            # Only keep last 5 log entries for cleaner display
            jobs[job_id]['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
            jobs[job_id]['logs'] = jobs[job_id]['logs'][-5:]
        if error:
            jobs[job_id]['error'] = error
        if result_file:
            jobs[job_id]['result_file'] = result_file
        if demographic_validation:
            jobs[job_id]['demographic_validation'] = demographic_validation


def run_analysis(job_id, project_name, brands, sample_start, sample_end, 
                 behavior_start, behavior_end, filters, skew_settings, 
                 is_genpop, purchasers_only, brand_category,
                 include_frequency=False, is_listener_watcher=False, platform_name=None, 
                 previous_file_path=None, reference_demographics=None, reference_sample_size=None):
    """Run the behavioral graph analysis pipeline with demographic consistency validation."""
    try:
        update_job_status(job_id, status='running', progress=5, message='Initializing...')
        
        # Import the bg module
        try:
            import bg
            from config import SNOWFLAKE_CONFIG
        except ImportError as e:
            update_job_status(job_id, status='failed', error=f'Module import failed: {str(e)}')
            return
        
        # Connect to Snowflake
        update_job_status(job_id, progress=15, message='Connecting to database...')
        
        try:
            conn = bg.connect_snowflake()
        except Exception as e:
            update_job_status(job_id, status='failed', error=f'Database connection failed: {str(e)}')
            return
        
        update_job_status(job_id, progress=25, message='Running analysis...')
        
        # Run the full pipeline
        try:
            result_file = bg.run_full_pipeline(
                conn=conn,
                project_name=project_name,
                brands=brands,
                sample_start=sample_start,
                sample_end=sample_end,
                behavior_start=behavior_start,
                behavior_end=behavior_end,
                filters=filters,
                skew_settings=skew_settings,
                is_genpop=is_genpop,
                purchasers_only=purchasers_only,
                previous_file_path=previous_file_path,
                brand_category=brand_category
            )
            
            update_job_status(job_id, progress=85, message='Processing results...')
            
            if result_file and os.path.exists(result_file):
                # Validate demographics against reference if provided
                demographic_validation = None
                if reference_demographics:
                    try:
                        with open(result_file, 'r') as f:
                            new_csv_content = f.read()
                        new_demographics = extract_demographics_from_csv(new_csv_content)
                        new_sample_size = extract_sample_size_from_csv(new_csv_content)
                        
                        is_valid, discrepancies = validate_demographics_consistency(
                            new_demographics, reference_demographics, tolerance=5
                        )
                        
                        # Check sample size tolerance (2-5%)
                        sample_valid = True
                        sample_diff = 0
                        if reference_sample_size and new_sample_size:
                            sample_diff = abs(new_sample_size - reference_sample_size) / reference_sample_size * 100
                            sample_valid = sample_diff <= 5
                        
                        demographic_validation = {
                            'demographics_valid': is_valid,
                            'sample_size_valid': sample_valid,
                            'sample_size_diff_pct': round(sample_diff, 2),
                            'discrepancies': discrepancies[:10] if discrepancies else []
                        }
                    except Exception as e:
                        print(f"Demographic validation error: {e}")
                
                # Copy result to outputs directory
                output_filename = f"{job_id}_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                output_path = os.path.join(OUTPUT_DIR, output_filename)
                
                import shutil
                shutil.copy2(result_file, output_path)
                
                # Upload to S3
                update_job_status(job_id, progress=95, message='Saving to cache...')
                s3_key = upload_to_s3(output_path, brands[0] if brands else project_name, sample_start, sample_end)
                
                update_job_status(
                    job_id, 
                    status='completed', 
                    progress=100, 
                    message='Complete!',
                    result_file=output_path,
                    demographic_validation=demographic_validation
                )
            else:
                update_job_status(job_id, status='failed', error='No output file generated')
                
        except Exception as e:
            error_msg = f'Analysis error: {str(e)}'
            update_job_status(job_id, status='failed', error=error_msg)
            
        finally:
            try:
                conn.close()
            except:
                pass
                
    except Exception as e:
        update_job_status(job_id, status='failed', error=str(e))


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
